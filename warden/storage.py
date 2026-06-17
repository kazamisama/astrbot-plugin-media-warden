"""存储层 —— 命名模板渲染 + 落盘 + blake2b 头 1MB dedupe.

设计:
  - 模板变量: {platform} {group_id} {sender_id} {sender_name}
              {date} {time} {msg_id} {idx} {safe_name} {ext} {kind}
  - safe_name: 字符级清理(替换非字母数字/中文/.-_ 的字符为 _)
  - ext: 从 mime / url / file_id 推断,缺省 '.bin'
  - dedupe: blake2b 指纹(小文件全量 / 大文件前1MB+size) -> _blake/<aa>/<bb>/<hex>.ptr
             指针文件内容=首存真实相对路径；命中时返回首存原位置
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
    from .components import Component


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


def _sniff_ext(data: Optional[bytes]) -> Optional[str]:
    """由内容魔数推断扩展名（最可靠，不受 QQ 文件名/Content-Type 干扰）。"""
    if not data or len(data) < 12:
        return None
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"\x00\x00\x01\x00":
        return ".ico"
    # mp4 / mov: ....ftyp
    if data[4:8] == b"ftyp":
        return ".mp4"
    # OGG / Opus / amr
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:6] == b"#!AMR\n" or data[:5] == b"#!AMR":
        return ".amr"
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:4] == b"PK\x03\x04":
        return ".zip"
    return None


def _guess_ext(component: "Component", mime: Optional[str] = None,
               data: Optional[bytes] = None) -> str:
    # 1) 内容魔数优先（QQ 动图经常 file=.gif 但下到 jpg 静帧，以真实字节为准）
    sniff = _sniff_ext(data)
    if sniff:
        return sniff
    # 2) 显式文件名后缀
    name = component.name or ""
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", name)
    if m:
        return "." + m.group(1).lower()
    # 3) file_id 携带的后缀（如 8BD....gif）—— 优先于 mime 猜测
    if component.file_id:
        m = re.search(r"\.([A-Za-z0-9]{1,5})$", component.file_id)
        if m:
            return "." + m.group(1).lower()
    # 4) mime
    if mime:
        ext = MIME_EXT.get(mime.lower())
        if ext:
            return ext
        m = re.search(r"/([A-Za-z0-9.+-]+)", mime)
        if m:
            return "." + m.group(1).lower()
    # 5) url 路径后缀
    if component.url:
        m = re.search(r"\.([A-Za-z0-9]{1,5})(?:\?|$)", component.url)
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
                    *, mime: Optional[str] = None, short_id: Optional[str] = None,
                    data: Optional[bytes] = None) -> str:
    fmt = _format_ctx(ctx)
    vars = dict(fmt)
    ext = _guess_ext(component, mime, data)
    # safe_name 去掉末尾与 ext 重复的后缀，避免 ".jpg..jpg" 双扩展名
    raw_name = component.name or component.file_id or "asset"
    base_name, name_ext = os.path.splitext(raw_name)
    if name_ext and name_ext.lower() == ext.lower():
        raw_name = base_name
    vars["safe_name"] = _safe_name(raw_name)
    vars["ext"] = ext
    vars["kind"] = component.kind
    vars["short_id"] = (short_id or "")[:8] or "noid"
    try:
        rel = pattern.format(**vars)
    except KeyError as e:
        raise ValueError(f"unknown template variable: {e.args[0]!r}") from e
    # 收尾再保险一道：压掉连续重复的同名扩展
    if ext and rel.endswith(ext + ext):
        rel = rel[: -len(ext)]
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


_FULL_HASH_LIMIT = 16 * 1024 * 1024  # ≤ 16MB 全量哈希；超过则前1MB + total_size


def _blake_digest(data: bytes) -> str:
    """内容指纹。

    - 小文件（≤ _FULL_HASH_LIMIT）：全量哈希，零误判/零漏判。
    - 大文件：前 1MB + 总字节数进摘要，消除“前1MB相同即误判”。
    """
    h = hashlib.blake2b(digest_size=16)
    if len(data) <= _FULL_HASH_LIMIT:
        h.update(data)
    else:
        h.update(data[: 1024 * 1024])
        h.update(b"\x00")
        h.update(str(len(data)).encode("ascii"))
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
                 pattern: str = "{group_id}/{sender_id}/{date}_{time}_{kind}_{short_id}{ext}",
                 max_bytes: int = 100 * 1024 * 1024):
        self.root = os.path.abspath(root)
        self.dedupe = dedupe
        self.pattern = pattern
        self.max_bytes = max_bytes
        os.makedirs(self.root, exist_ok=True)
        # 解析 symlink 后真实路径,用于路径越权检查
        self._real_root = os.path.realpath(self.root)

    def _blake_path(self, digest: str) -> str:
        # 指针文件：内容为首存真实文件的相对路径（相对 storage_root）
        return os.path.join(self.root, "_blake", digest[:2], digest[2:4], digest + ".ptr")

    def _legacy_blake_path(self, digest: str) -> str:
        # 旧版 symlink/拷贝文件（.bin），向后兼容读取
        return os.path.join(self.root, "_blake", digest[:2], digest[2:4], digest + ".bin")

    def _write_pointer(self, digest: str, full: str) -> None:
        bp = self._blake_path(digest)
        os.makedirs(os.path.dirname(bp), exist_ok=True)
        rel = os.path.relpath(full, start=self.root).replace("\\", "/")
        with open(bp, "w", encoding="utf-8", newline="\n") as f:
            f.write(rel)

    def _existing_for_digest(self, digest: str) -> Optional[str]:
        """返回首存真实文件的绝对路径（而非 _blake 指纹路径）。"""
        ptr = self._blake_path(digest)
        if os.path.exists(ptr):
            try:
                with open(ptr, "r", encoding="utf-8") as f:
                    rel = f.read().strip()
                if rel:
                    target = os.path.join(self.root, rel)
                    if os.path.exists(target):
                        return os.path.normpath(target)
            except OSError:
                pass
            # 指针损坏/原件被删 -> 视为未命中，重新落盘
            return None
        # 向后兼容：旧版 .bin（symlink 或拷贝）
        legacy = self._legacy_blake_path(digest)
        if os.path.exists(legacy):
            real = os.path.realpath(legacy)
            return real if os.path.exists(real) else None
        return None

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

        rel = render_filename(
            self.pattern, component, ctx, mime=mime,
            short_id=(digest or _blake_digest(data)),
            data=data,
        )
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
            # 记录指针：digest -> 首存真实文件相对路径（跨平台，不依赖 symlink）
            if not os.path.exists(self._blake_path(digest)):
                try:
                    self._write_pointer(digest, full)
                except OSError:
                    pass
        return SaveResult(path=full, size=len(data), reused=False, blake16=digest)

    def _assert_within_root(self, path: str) -> None:
        """防路径越权:确保 path 在 self._real_root 内."""
        rp = os.path.realpath(path)
        if not (rp == self._real_root or rp.startswith(self._real_root + os.sep)):
            raise ValueError(
                f"path escapes storage root: {path!r} -> {rp!r} not under {self._real_root!r}"
            )

