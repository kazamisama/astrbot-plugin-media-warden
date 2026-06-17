"""过滤策略 —— 决定一条事件是否进入流水线.

策略:
  - v1 仅服务群聊(私聊直接放行,不进入流水线)
  - whitelist: 群白名单 AND 用户白名单(任一为空表示该维不限)
  - blacklist: 群黑名单 OR 用户黑名单

群 ID 匹配两种形式都允许: "aiocqhttp:123456" 或者 "123456".
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
    """v1 仅服务群聊 —— 没有 group_id 直接放行."""
    return bool(event_group_id(event))


def evaluate(cfg: "MediaWardenConfig", event) -> MatchDecision:
    platform = event_platform(event)
    gid = event_group_id(event)
    uid = event_sender_id(event)

    if not _is_group_event(event):
        return MatchDecision(False, "non-group event (private/stray) skipped")

    gk = _key(platform, gid)
    groups = cfg.matched_groups_set()
    users = cfg.matched_users_set()

    if cfg.match_mode == "whitelist":
        group_ok = (not groups) or (gk in groups) or (gid in groups)
        user_ok = (not users) or ((uid or "") in users)
        allow = group_ok and user_ok
        if allow:
            return MatchDecision(True, "whitelist match")
        if not group_ok:
            return MatchDecision(False, "group not in whitelist")
        return MatchDecision(False, "user not in whitelist")

    group_blocked = (gk in groups) or (gid in groups)
    user_blocked = (uid or "") in users
    if group_blocked:
        return MatchDecision(False, "group blacklisted")
    if user_blocked:
        return MatchDecision(False, "user blacklisted")
    return MatchDecision(True, "blacklist pass")
