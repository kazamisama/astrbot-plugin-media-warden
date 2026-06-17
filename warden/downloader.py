"""异步下载器 —— 把 Component 抓成本地 bytes.

Phase 2 抽象: downloader 接受一个 Component 和一个 fetcher 协议,产出 bytes.
默认 fetcher 用 aiohttp 走 URL 公开链接.平台协议(OneBot download_file / TG getFile)
以 fetcher 注入的形式接入,本模块不直接耦合.

错误统一抛 DownloadError,带可读 reason.
"""
from __future__ import annotations
import asyncio
import io
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from warden.components import Component


class DownloadError(Exception):
    pass


@dataclass
class Downloaded:
    data: bytes
    mime: Optional[str] = None
    size: int = 0


# fetcher 协议: (component) -> Downloaded
Fetcher = Callable[["Component"], Awaitable[Downloaded]]


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
            # 大小限制:优先 Content-Length,缺失则流式累计
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


# ----------------- 顶层 download -----------------

async def download(component: "Component",
                   fetcher: Optional[Fetcher] = None,
                   **fetcher_kwargs) -> Downloaded:
    """统一入口:fetcher 缺省走 aiohttp.允许 Phase 2.1 注入 OneBot 协议 fetcher."""
    fn = fetcher or aiohttp_fetcher
    return await fn(component, **fetcher_kwargs)
