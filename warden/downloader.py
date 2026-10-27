"""异步下载器 —— 把 Component 抓成本地 bytes.

Phase 2 抽象: downloader 接受一个 Component 和一个 fetcher 协议,产出 bytes.
默认 fetcher 用 aiohttp 走 URL 公开链接.平台协议(OneBot download_file / TG getFile)
以 fetcher 注入的形式接入,本模块不直接耦合.

错误统一抛 DownloadError,带可读 reason.

v1.3 增强:
  - 指数退避重试:transient 错误(超时/连接错误/5xx)最多 retries 次
  - 非 transient(4xx)立即抛

v1.9 增强(http 源链接失效后的补救):
  - astrbot_component_fetcher 的 http 分支失败后回退到组件本地副本
    (meta["path"] / raw.convert_to_file_path / convert_to_base64)
  - 本地副本也失败时,把原因附到**原始**错误上(不覆盖主错误)

v1.10: 删掉 download(fallback=) 与 refresher 两个钩子 —— 生产路径从来没用过它们,
       真正的多级补救链在 main._download_one 里(本地缓存 → get_image/get_msg →
       本地副本 → 迟到缓存重查)。两套并存的补救机制只会让读代码的人误判。
"""
from __future__ import annotations
import asyncio
import io
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .components import Component


class DownloadError(Exception):
    pass


@dataclass
class Downloaded:
    data: bytes
    mime: Optional[str] = None
    size: int = 0


# fetcher 协议: (component, **kwargs) -> Downloaded
Fetcher = Callable[["Component"], Awaitable[Downloaded]]


# transient 错误类型(用于决定是否重试)
# 这些错误在 v1 阶段我们尽量兼容:可能因为具体 aiohttp 版本不同而类名不同
def _is_transient(exc: BaseException) -> bool:
    """判断是否 transient:超时 / 连接错 / 5xx."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "connect", "reset", "broken")):
        return True
    # 自定义 DownloadError 的 reason(我们 aiohttp fetcher 把 aiohttp 异常转成的)
    msg = str(exc)
    if any(k in msg for k in ("timeout", "connection", "reset", "broken pipe")):
        return True
    # aiohttp 的 status 错误由 fetcher 内部转 DownloadError,带 "http NNN" 信息
    if "http 5" in msg or "http 429" in msg:
        return True
    return False


# ----------------- aiohttp fetcher -----------------

async def aiohttp_fetcher(component: "Component",
                          *,
                          timeout_s: float = 30.0,
                          max_bytes: int = 200 * 1024 * 1024,
                          session=None) -> Downloaded:
    """走 URL 公开链接.需要 aiohttp,缺包时给出明确错误."""
    if not component.url:
        raise DownloadError(
            f"component has no url (kind={component.kind}, file_id={component.file_id!r})"
        )
    try:
        import aiohttp
    except ImportError as e:
        raise DownloadError(
            "aiohttp not installed; pip install aiohttp to use URL fetcher"
        ) from e
    _aiohttp_client_errors = (
        (aiohttp.ClientError,) if hasattr(aiohttp, "ClientError") else ()
    )

    own_session = False
    if session is None:
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s))
        own_session = True
    try:
        async with session.get(component.url) as resp:
            if resp.status != 200:
                raise DownloadError(
                    f"http {resp.status} for {component.url!r}"
                )
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > max_bytes:
                        raise DownloadError(
                            f"content-length {cl} > max_bytes {max_bytes}"
                        )
                except ValueError:
                    pass
            buf = io.BytesIO()
            n = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                n += len(chunk)
                if n > max_bytes:
                    raise DownloadError(
                        f"stream exceeded max_bytes {max_bytes}"
                    )
                buf.write(chunk)
            data = buf.getvalue()
            return Downloaded(
                data=data,
                mime=resp.headers.get("Content-Type"),
                size=len(data),
            )
    except asyncio.TimeoutError as e:
        raise DownloadError(f"timeout after {timeout_s}s for {component.url!r}") from e
    except _aiohttp_client_errors as e:
        raise DownloadError(f"connection error for {component.url!r}: {e}") from e
    finally:
        if own_session:
            await session.close()


async def astrbot_component_fetcher(component: "Component",
                                    *,
                                    timeout_s: float = 30.0,
                                    max_bytes: int = 200 * 1024 * 1024,
                                    session=None) -> Downloaded:
    """优先走 url;http 失败或没 url 时回退到组件本地副本.

    商城表情 / napcat / lagrange 的 Image 常常 url 为空或直接是本地缓存路径（非 http），
    AstrBot 组件自带的 convert_to_file_path() 能把 file_id / file:// / base64:// 统一解析为本地文件。

    v1.9: url 是 http(s) 但下载失败（典型: QQ rkey 授权链接过期 -> 400）时，
    不再直接抛错，而是回退到组件本地副本（PreProcessStage 可能已经落过一份）。
    """
    url = component.url or ""
    if url.startswith("http://") or url.startswith("https://"):
        try:
            return await aiohttp_fetcher(
                component, timeout_s=timeout_s, max_bytes=max_bytes, session=session
            )
        except DownloadError as primary:
            try:
                return await _local_fallback(component, max_bytes)
            except DownloadError as e:
                primary.fallback_error = str(e)
            raise

    return await _local_fallback(component, max_bytes)


async def _local_fallback(component: "Component",
                          max_bytes: int) -> Downloaded:
    """只用本地副本解析组件 (非 http 的 url / meta.path / raw 转换).

    解析不出来时抛 DownloadError. 供 astrbot_component_fetcher 的 http 失败
    回退链与 main 侧的 component 兜底复用.
    """
    url = component.url or ""
    # url 为本地路径 / file:// / base64:// / 空 都走本地解析
    # napcat 等环境下 Image.url 常直接是 \.astrbot\data\temp\... 本地缓存路径
    local = _url_to_local_path(url)
    if not local:
        local = _url_to_local_path((component.meta or {}).get("path") or "")
    if local:
        return _read_local_file(local, max_bytes)

    raw = getattr(component, "raw", None)
    if raw is None:
        raise DownloadError(
            f"component has no usable url and no raw object (kind={component.kind}, url={component.url!r}, file_id={component.file_id!r})"
        )

    to_path = getattr(raw, "convert_to_file_path", None)
    if callable(to_path):
        try:
            path = await to_path()
        except Exception as e:
            raise DownloadError(f"convert_to_file_path failed: {e!r}") from e
        resolved = _url_to_local_path(str(path))
        if resolved:
            return _read_local_file(resolved, max_bytes)
        raise DownloadError(f"convert_to_file_path gave no local file: {path!r}")

    to_b64 = getattr(raw, "convert_to_base64", None)
    if callable(to_b64):
        try:
            import base64 as _b64
            b64 = await to_b64()
            data = _b64.b64decode(b64.split(",", 1)[-1])
        except Exception as e:
            raise DownloadError(f"convert_to_base64 failed: {e!r}") from e
        if len(data) > max_bytes:
            raise DownloadError(f"data {len(data)} > max_bytes {max_bytes}")
        return Downloaded(data=data, mime=None, size=len(data))

    raise DownloadError(
        f"component has no url and raw object cannot resolve bytes (kind={component.kind})"
    )


def _url_to_local_path(url: str):
    """把非 http(s) 的 url 解析为本地文件路径；不是本地文件返回 None。

    兼容：file:///C:/x.jpg、file://C:/x.jpg、URL 编码的 c%3A%5C...、裸本地路径。
    base64:// 交给 raw.convert_to_base64 处理，这里返回 None。
    """
    import os
    from urllib.parse import unquote, urlparse
    if not url or url.startswith("base64://"):
        return None
    candidate = url
    if url.startswith("file://"):
        parsed = urlparse(url)
        candidate = unquote(parsed.path or "")
        # Windows: /C:/x -> C:/x
        if os.name == "nt" and len(candidate) >= 3 and candidate[0] == "/" and candidate[2] == ":":
            candidate = candidate[1:]
    else:
        # 可能是 URL 编码过的本地路径（如 c%5CUsers%5C...）
        if "%" in candidate:
            candidate = unquote(candidate)
    candidate = candidate.replace("\\", os.sep).replace("/", os.sep) if os.name == "nt" else candidate
    try:
        if os.path.isfile(candidate):
            return candidate
    except OSError:
        return None
    return None


def _read_local_file(path: str, max_bytes: int) -> Downloaded:
    import os
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise DownloadError(f"local file stat failed: {e!r}") from e
    if size > max_bytes:
        raise DownloadError(f"local file {size} > max_bytes {max_bytes}")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        raise DownloadError(f"local file read failed: {e!r}") from e
    mime = None
    low = path.lower()
    for ext, m in (
        (".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
        (".gif", "image/gif"), (".webp", "image/webp"), (".mp4", "video/mp4"),
        (".amr", "audio/amr"), (".silk", "audio/silk"),
    ):
        if low.endswith(ext):
            mime = m
            break
    return Downloaded(data=data, mime=mime, size=len(data))


# ----------------- 顶层 download(带重试) -----------------

async def download(component: "Component",
                   fetcher: Optional[Fetcher] = None,
                   *,
                   retries: int = 2,
                   backoff_base: float = 0.5,
                   backoff_cap: float = 4.0,
                   **fetcher_kwargs) -> Downloaded:
    """统一入口 + 指数退避重试.

    retries=0 -> 不重试(默认失败 1 次立即抛)
    retries=N -> 最多重试 N 次
    backoff: 0.5s, 1s, 2s, ... jitter 0~0.5s,封顶 backoff_cap

    v1.9: 主错误上可能挂 fallback_error(补救链失败原因),由调用方读出来写回执。
    """
    fn = fetcher or aiohttp_fetcher
    attempt = 0
    last_exc: Optional[BaseException] = None
    while attempt <= retries:
        try:
            return await fn(component, **fetcher_kwargs)
        except DownloadError as e:
            last_exc = e
            if not _is_transient(e) or attempt >= retries:
                break
            # 指数退避 + 抖动
            delay = min(backoff_cap, backoff_base * (2 ** attempt))
            delay = delay * (0.5 + random.random() * 0.5)
            await asyncio.sleep(delay)
            attempt += 1
    if last_exc is None:  # 不可达,这里只是让类型检查器安心
        raise DownloadError("download failed without an exception")
    raise last_exc

