"""过滤策略 —— 决定一条事件是否进入流水线.

策略（v1.14.0 起，用户 2026-10-28 定）:
  - 仅服务群聊: 没有 group_id 的事件(私聊/系统消息)一律 skip
  - whitelist（默认）: **空 = 不保存** —— 群列表为空那就什么都不存；发送人列表
    为空同样什么都不存；发送人列表里的 "*" 表示该群所有人
  - blacklist: 群黑名单 OR 用户黑名单（**空 = 不限**，这是「想全存」的逃生口）

群 ID 匹配两种形式都允许: "aiocqhttp:123456" 或者 "123456".
类型（图片/动图/视频/文档/纯文本/聊天记录）另由 kind_allowed() 判。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import MediaWardenConfig


@dataclass(frozen=True)
class MatchDecision:
    allow: bool
    reason: str = ""


def _key(platform: Optional[str], gid: Optional[str]) -> str:
    return f"{(platform or 'unknown').lower()}:{gid}" if gid else ""


def event_platform(event) -> str:
    fn = getattr(event, "get_platform_name", None)
    if callable(fn):
        try:
            v = fn()
            if v:
                return str(v)
        except Exception:
            pass
    pm = getattr(event, "platform_meta", None)
    return str(getattr(pm, "name", None) or "unknown") if pm else "unknown"


def event_group_id(event) -> str:
    fn = getattr(event, "get_group_id", None)
    if callable(fn):
        try:
            v = fn()
            if v:
                return str(v)
        except Exception:
            pass
    obj = getattr(event, "message_obj", None)
    return str(getattr(obj, "group_id", "") or "") if obj else ""


def event_sender_id(event) -> str:
    fn = getattr(event, "get_sender_id", None)
    if callable(fn):
        try:
            v = fn()
            if v:
                return str(v)
        except Exception:
            pass
    obj = getattr(event, "message_obj", None)
    sender = getattr(obj, "sender", None) if obj else None
    return str(getattr(sender, "user_id", "") or "") if sender else ""


def event_sender_name(event) -> str:
    fn = getattr(event, "get_sender_name", None)
    if callable(fn):
        try:
            v = fn()
            if v:
                return str(v)
        except Exception:
            pass
    obj = getattr(event, "message_obj", None)
    sender = getattr(obj, "sender", None) if obj else None
    if sender:
        return str(getattr(sender, "nickname", None) or getattr(sender, "name", None) or "")
    return ""


def _is_group_event(event) -> bool:
    """是否群聊事件 —— 没有 group_id 的(私聊/系统消息)返回 False,调用方会 skip."""
    return bool(event_group_id(event))


ALL_SENDERS = "*"
_ANIMATED_EXTS = (".gif", ".webp", ".apng")


def comp_is_animated(comp) -> bool:
    """按扩展名判断图片是否动图（gif / webp / apng）。

    webp 既可能是静图也可能是动图，这里先当动图；下载后可以用
    blob_is_animated() 复核一次，把静态 webp 归回「图片」。
    """
    meta = getattr(comp, "meta", None) or {}
    candidates = [
        getattr(comp, "url", "") or "",
        getattr(comp, "file_id", "") or "",
        meta.get("raw_file") or "",
        meta.get("path") or "",
    ]
    for raw in candidates:
        path = str(raw).split("?", 1)[0].strip().lower()
        if path.endswith(_ANIMATED_EXTS):
            return True
    return False


def blob_is_animated(blob: bytes) -> bool:
    """不依赖 PIL 的动图判定：GIF 帧数 > 1 / WebP 带 ANIM 块 / APNG 带 acTL。"""
    if not blob or len(blob) < 16:
        return False
    head = blob[:6]
    if head in (b"GIF87a", b"GIF89a"):
        return blob.count(b"\x00\x21\xf9\x04") > 1 or blob.count(b"\x2c") > 1
    if head[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return b"ANIM" in blob[:4096]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return b"acTL" in blob[:4096]
    return False


def media_kind(comp) -> str:
    """组件 → 六类里的哪一类（image/gif/video/file）；非媒体返回自身 kind。"""
    kind = (getattr(comp, "kind", "") or "").lower()
    if kind == "image":
        return "gif" if comp_is_animated(comp) else "image"
    if kind == "video":
        return "video"
    if kind == "file":
        return "file"
    return kind or "unknown"


def kind_allowed(cfg: "MediaWardenConfig", kind: str) -> bool:
    """类型开关：'image'/'gif'/'video'/'file'/'text'/'forward' 各自是否保存。"""
    flags = cfg.save_type_flags()
    if kind in flags:
        return bool(flags[kind])
    return True


def evaluate(cfg: "MediaWardenConfig", event) -> MatchDecision:
    platform = event_platform(event)
    gid = event_group_id(event)
    uid = event_sender_id(event)

    if not _is_group_event(event):
        return MatchDecision(False, "non-group event (private/stray) skipped")

    gk = _key(platform, gid).lower()
    groups = cfg.matched_groups_set()
    users = cfg.matched_users_set()
    gid_l = (gid or "").lower()

    if cfg.match_mode == "whitelist":
        # 空 = 不保存（v1.14.0 起）。以前「空 = 不限」的语义默认会存下所有群，
        # 与「默认安全」相悖，已反转为显式配置才生效。
        if not groups:
            return MatchDecision(False, "no group configured (empty = save nothing)")
        if not ((gk in groups) or (gid_l in groups)):
            return MatchDecision(False, "group not in save list")
        if not users:
            return MatchDecision(False, "no sender configured (empty = save nothing)")
        if ALL_SENDERS not in users and (uid or "") not in users:
            return MatchDecision(False, "sender not in save list")
        return MatchDecision(True, "whitelist match")

    group_blocked = (gk in groups) or (gid_l in groups)
    user_blocked = (uid or "") in users
    if group_blocked:
        return MatchDecision(False, "group blacklisted")
    if user_blocked:
        return MatchDecision(False, "user blacklisted")
    return MatchDecision(True, "blacklist pass")
