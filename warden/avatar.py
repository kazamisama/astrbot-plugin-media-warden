"""用户头像抓取 + 文件缓存 —— v1.7.0 新增.

策略:
  - QQ 平台优先:https://q1.qlogo.cn/g?b=qq&nk={sender_id}&s={size}
  - 抓取失败(404 / 网络错 / 超时) → 返回 None,渲染时跳过头像
  - 文件缓存:storage_root/_avatars/{platform}_{sender_id}.jpg
  - 容量控制:超过 avatar_cache_size_mb 时按 mtime LRU 删旧
  - 单文件上限 2MB(防异常大头像把缓存打爆)
  - 单请求超时 5s(防 OneBot / CDN 拖慢整条消息)
"""
from __future__ import annotations
import asyncio
import os
import time
from typing import Awaitable, Callable, Optional, Tuple, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    pass


_QQ_AVATAR_BASE = "https://q1.qlogo.cn/g"
_DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024  # v1.7.1: 2MB -> 5MB


def qq_avatar_url(sender_id: str, size: int = 640) -> str:
    """构造 QQ qlogo.cn 头像 URL。不做可达性检查,由调用方 GET 探活。"""
    return f"{_QQ_AVATAR_BASE}?b=qq&nk={sender_id}&s={size}"


class AvatarCache:
    """文件缓存 + LRU 容量淘汰."""

    def __init__(self, root: str, *, max_bytes: int = 50 * 1024 * 1024,
                 timeout_s: float = 5.0,
                 max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES):
        self.root = os.path.abspath(root)
        self.max_bytes = max_bytes
        self.timeout_s = timeout_s
        self.max_file_bytes = max_file_bytes
        os.makedirs(self.root, exist_ok=True)

    @staticmethod
    def _safe_id(sender_id: str) -> str:
        """文件名安全化:剥离非字母数字/-/_ 的字符,空则用 'anon'."""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(sender_id))
        return safe or "anon"

    def _path_for(self, platform: str, sender_id: str) -> str:
        safe = self._safe_id(sender_id)
        plat = self._safe_id(platform or "unknown")
        return os.path.join(self.root, f"{plat}_{safe}.jpg")

    def _existing(self, path: str) -> Optional[str]:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return os.path.realpath(path)
        except OSError:
            return None
        return None

    async def _fetch_bytes(self, url: str) -> Optional[bytes]:
        """GET URL,200 + size OK 返回 bytes,其他情况 None."""
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_s)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    cl = resp.headers.get("Content-Length")
                    if cl is not None:
                        try:
                            if int(cl) > self.max_file_bytes:
                                return None
                        except ValueError:
                            pass
                    buf = bytearray()
                    n = 0
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        n += len(chunk)
                        if n > self.max_file_bytes:
                            return None
                        buf.extend(chunk)
                    return bytes(buf)
        except (asyncio.TimeoutError, aiohttp.ClientError, Exception):
            return None

    def _evict_if_needed(self) -> None:
        """超过 max_bytes 时按 mtime 从最旧开始删,直到回落到上限内."""
        try:
            entries = []
            total = 0
            for fn in os.listdir(self.root):
                p = os.path.join(self.root, fn)
                if not os.path.isfile(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                entries.append((p, st.st_mtime, st.st_size))
                total += st.st_size
            if total <= self.max_bytes:
                return
            entries.sort(key=lambda x: x[1])  # 旧→新
            for p, _, sz in entries:
                if total <= self.max_bytes:
                    break
                try:
                    os.remove(p)
                    total -= sz
                except OSError:
                    pass
        except OSError:
            pass

    async def get_or_fetch(self, platform: str, sender_id: str,
                           *, url: Optional[str] = None,
                           fetcher: Optional[Callable[[str], Awaitable[bytes]]] = None
                           ) -> Optional[str]:
        """命中缓存 → 返回本地路径。未命中 → 下载并落盘,失败返回 None.

        fetcher: 可注入的下载函数,默认 aiohttp;测试时可用 mock 替身。
        """
        path = self._path_for(platform, sender_id)
        existing = self._existing(path)
        if existing is not None:
            return existing
        if url is None:
            url = qq_avatar_url(sender_id)
        if fetcher is not None:
            try:
                data = await fetcher(url)
            except Exception:
                return None
        else:
            data = await self._fetch_bytes(url)
        if not data:
            return None
        if len(data) > self.max_file_bytes:
            return None
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError:
            return None
        self._evict_if_needed()
        return os.path.realpath(path)


async def fetch_many(cache: AvatarCache,
                     items: list[Tuple[str, str]],
                     *,
                     concurrency: int = 4
                     ) -> dict[Tuple[str, str], Optional[str]]:
    """并发抓多个 (platform, sender_id) 头像,返回 dict 映射.

    失败的项映射到 None(由调用方决定 fallback 策略)。
    """
    if not items:
        return {}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(platform: str, sender_id: str
                   ) -> Tuple[Tuple[str, str], Optional[str]]:
        async with sem:
            p = await cache.get_or_fetch(platform, sender_id)
            return (platform, sender_id), p

    results = await asyncio.gather(*(_one(p, s) for p, s in items))
    return dict(results)