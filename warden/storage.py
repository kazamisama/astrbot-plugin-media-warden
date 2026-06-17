"""存储层 —— 命名模板渲染 + 落盘 + blake2b 头 1MB dedupe.

设计:
  - 模板变量: {platform} {group_id} {sender_id} {sender_name}
              {date} {time} {msg_id} {idx} {safe_name} {ext} {kind}
  - safe_name: 字符级清理(替换非字母数字/中文/.-_ 的字符为 _)
  - ext: 从 mime / url / file_id 推断,缺省 '.bin'
  - dedupe: blake2b(data[:1MB], digest_size=16) -> 落到 _blake/<aa>/<bb>/<hex>.bin
            命中已有 hash -> 复用旧路径,AssetResult 标记 reused=True
"""
from __future__ import annotations
import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from warden.components import Component


def _is_safe_char(c: str) -> bool:
    if c.isalnum() or c in "-_.":
        return True
    o = ord(c)
    if 0x4E00 <= o <= 0x9FFF:
        return True
    return False


def _safe_name(s: str, max_len: int = 40) -> str:
    if not s:
        return "asset"
    # 剥控制字符
    s = "".join(c for c in s if ord(c) >= 0x20 and c != chr(0x7F))
    # 替换非安全字符
    s = "".join(c if _is_safe_char(c) else "_" for c in s)
    s = s.strip("_")
    if not s:
        return "asset"
    return s[:max_len]


MIME_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "video/mp4": ".mp4", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/amr": ".amr", "audio/ogg": ".ogg",
    "application/zip": ".zip", "application/json": ".json",
    "application/pdf": ".pdf",
}


def _guess_ext(component: "Component", mime: Optional[str] = None) -> str:
    name = component.name or ""
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", name)
    if m:
        return "." + m.group(1).lower()
    if mime:
        ext = MIME_EXT.get(mime.lower())
        if ext:
            return ext
        m = re.search(r"/([A-Za-z0-9.+-]+)", mime)
        if m:
            return "." + m.group(1).lower()
    if component.url:
        m = re.search(r"\.([A-Za-z0-9]{1,5})(?:\?|$)", component.url)
        if m:
            return "." + m.group(1).lower()
    if component.file_id:
        m = re.search(r"\.([A-Za-z0-9]{1,5})$", component.file_id)
        if m:
            return "." + m.group(1).lower()
    return ".bin"


@dataclass
class SaveContext:
    platform: str
    group_id: str
    sender_id: str
    sender_name: str
    msg_id: str
    idx: int
    ts: int


def _format_ctx(ctx: SaveContext) -> dict:
    dt = datetime.fromtimestamp(ctx.ts)
    return {
        "platform": ctx.platform or "unknown",
        "group_id": ctx.group_id or "unknown",
        "sender_id": ctx.sender_id or "anon",
        "sender_name": _safe_name(ctx.sender_name or ctx.sender_id or "anon", 20),
        "date": dt.strftime("%Y%m%d"),
        "time": dt.strftime("%H%M%S"),
        "msg_id": ctx.msg_id or "nomsg",
        "idx": ctx.idx,
    }


def render_filename(pattern: str, component: "Component", ctx: SaveContext,
                    *, mime: Optional[str] = None) -> str:
    fmt = _format_ctx(ctx)
    vars = dict(fmt)
    vars["safe_name"] = _safe_name(component.name or component.file_id or "asset")
    vars["ext"] = _guess_ext(component, mime)
    vars["kind"] = component.kind
    try:
        rel = pattern.format(**vars)
    except KeyError as e:
        raise ValueError(f"unknown template variable: {e.args[0]!r}") from e
    rel = rel.replace("\\", "/")
    rel = re.sub(r"/+", "/", rel)
    if rel.startswith("/"):
        rel = rel.lstrip("/")
    parts = []
    for p in rel.split("/"):
        if p in ("", ".", ".."):
            continue
        parts.append(p)
    return "/".join(parts)


def _blake_digest(data: bytes) -> str:
    h = hashlib.blake2b(digest_size=16)
    h.update(data[: 1024 * 1024])
    return h.hexdigest()


@dataclass
class SaveResult:
    path: str
    size: int
    reused: bool = False
    blake16: Optional[str] = None


class Storage:
    """本地落盘 + dedupe 复用."""

    def __init__(self, root: str, *, dedupe: bool = True,
                 pattern: str = "{platform}/{group_id}/{date}/"
                 "{sender_id}_{msg_id}_{idx}_{safe_name}.{ext}",
                 max_bytes: int = 100 * 1024 * 1024):
        self.root = os.path.abspath(root)
        self.dedupe = dedupe
        self.pattern = pattern
        self.max_bytes = max_bytes
        os.makedirs(self.root, exist_ok=True)
        # 解析 symlink 后真实路径,用于路径越权检查
        self._real_root = os.path.realpath(self.root)

    def _blake_path(self, digest: str) -> str:
        return os.path.join(self.root, "_blake", digest[:2], digest[2:4], digest + ".bin")

    def _existing_for_digest(self, digest: str) -> Optional[str]:
        p = self._blake_path(digest)
        return p if os.path.exists(p) else None

    def save(self, data: bytes, component: "Component", ctx: SaveContext,
             *, mime: Optional[str] = None) -> SaveResult:
        if len(data) > self.max_bytes:
            raise ValueError(
                f"data {len(data)}B exceeds max_bytes {self.max_bytes}"
            )
        # path-safety: 解析后路径必须在 self._real_root 内
        self._real_root = self._real_root or os.path.realpath(self.root)
        digest = _blake_digest(data) if self.dedupe else None

        if digest and self.dedupe:
            existing = self._existing_for_digest(digest)
            if existing is not None:
                return SaveResult(path=existing, size=len(data),
                                  reused=True, blake16=digest)

        rel = render_filename(self.pattern, component, ctx, mime=mime)
        full = os.path.join(self.root, rel)
        if os.path.exists(full) and digest:
            try:
                with open(full, "rb") as f:
                    head = f.read(1024 * 1024)
                if _blake_digest(head) == digest:
                    return SaveResult(path=full, size=len(data),
                                      reused=True, blake16=digest)
            except OSError:
                pass

        # 路径越权检查(防恶意模板或 symlink 把文件写到外面)
        self._assert_within_root(full)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if os.path.exists(full):
            base, ext = os.path.splitext(full)
            i = 1
            while i <= 999:
                cand = f"{base}__{i}{ext}"
                if not os.path.exists(cand):
                    full = cand
                    break
                i += 1
            else:
                raise RuntimeError("too many collisions on " + full)
        # 写入后再校验一次,防止 TOCTOU
        self._assert_within_root(full)
        with open(full, "wb") as f:
            f.write(data)

        if digest and self.dedupe:
            bp = self._blake_path(digest)
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            if not os.path.exists(bp):
                try:
                    rel_target = os.path.relpath(full, start=os.path.dirname(bp))
                    os.symlink(rel_target, bp)
                except (OSError, NotImplementedError):
                    import shutil
                    shutil.copy2(full, bp)
        return SaveResult(path=full, size=len(data), reused=False, blake16=digest)

    def _assert_within_root(self, path: str) -> None:
        """防路径越权:确保 path 在 self._real_root 内."""
        rp = os.path.realpath(path)
        if not (rp == self._real_root or rp.startswith(self._real_root + os.sep)):
            raise ValueError(
                f"path escapes storage root: {path!r} -> {rp!r} not under {self._real_root!r}"
            )

