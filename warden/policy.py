"""过滤策略 —— 决定一条事件是否进入流水线.

策略:
  - v1 仅服务群聊(私聊直接放行,不进入流水线)
  - whitelist: 群白名单 AND 用户白名单(任一为空表示该维不限)
  - blacklist: 群黑名单 OR 用户黑名单

群 ID 匹配两种形式都允许: "aiocqhttp:123456" 或裸 "123456".
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from warden.config import MediaWardenConfig


@dataclass(frozen=True)
class MatchDecision:
    allow: bool
    reason: str = ""


def _key(platform: Optional[str], gid: Optional[str]) -> str:
    return f"{(platform or 'unknown').lower()}:{gid}" if gid else ""


def _is_group_event(event) -> bool:
    """v1 仅服务群聊 —— 没有 group_id 直接放行."""
    pm = getattr(event, "platform_meta", None)
    gid = getattr(pm, "channel_id", None) if pm else None
    return bool(gid)


def evaluate(cfg: "MediaWardenConfig", event) -> MatchDecision:
    pm = getattr(event, "platform_meta", None)
    platform = getattr(pm, "platform", None) if pm else None
    gid = getattr(pm, "channel_id", None) if pm else None
    sender = getattr(event, "sender", None)
    uid = getattr(sender, "user_id", None) if sender else None

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
