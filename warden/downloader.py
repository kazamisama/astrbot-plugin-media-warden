"""异步下载器 —— 把 Component 抓成本地 bytes.

Phase 2 抽象: downloader 接受一个 Component 和一个 fetcher 协议,产出 bytes.
默认 fetcher 用 aiohttp 走 URL 公开链接.平台协议(OneBot download_file / TG getFile)
以 fetcher 注入的形式接入,本模块不直接耦合.

错误统一抛 DownloadError,带可读 reason.

v1.3 增强:
  - 指数退避重试:transient 错误(超时/连接错误/5xx)最多 retries 次
  - 非 transient(4xx)立即抛
"""
from __future__ import annotations
import asyncio
import io
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Tuple, Type, TYPE_CHECKING

if TYPE_CHECKING:
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
    finally:
        if own_session:
            await session.close()


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
                raise
            # 指数退避 + 抖动
            delay = min(backoff_cap, backoff_base * (2 ** attempt))
            delay = delay * (0.5 + random.random() * 0.5)
            await asyncio.sleep(delay)
            attempt += 1
    # 不可达,这里只是让类型检查器安心
    raise DownloadError(f"unreachable: {last_exc!r}")


async def download_many(components: list, fetcher: Optional[Fetcher] = None,
                        **kwargs) -> list:
    """并发下载多个 Component;返回与输入等长的 Downloaded 列表,失败项抛 DownloadError.

    使用 asyncio.gather(..., return_exceptions=False) 让首个失败冒泡,其他已完成结果丢失.
    如需"全部完成即便部分失败"传 return_exceptions=True.
    """
    return await asyncio.gather(
        *(download(c, fetcher=fetcher, **kwargs) for c in components)
    )

