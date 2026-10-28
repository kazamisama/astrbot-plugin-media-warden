"""warden 核心包 —— 群聊素材守门人.

公开符号遵循最小集,具体实现分散到子模块.
"""
from .config import MediaWardenConfig, VERSION
from .policy import (
    MatchDecision, evaluate,
    event_platform, event_group_id, event_sender_id, event_sender_name,
    media_kind, kind_allowed, comp_is_animated, blob_is_animated, ALL_SENDERS,
)
from .components import (
    Component, extract_components, summarize, recover_raw_media_refs, raw_segments,
)
from .reporter import (
    AssetResult, BatchResult, format_batch,
    classify_error,
)
from .avatar import AvatarCache, qq_avatar_url, fetch_many as fetch_many_avatars

__version__ = VERSION
__all__ = [
    "MediaWardenConfig",
    "MatchDecision",
    "evaluate",
    "event_platform",
    "event_group_id",
    "event_sender_id",
    "event_sender_name",
    "media_kind",
    "kind_allowed",
    "comp_is_animated",
    "blob_is_animated",
    "ALL_SENDERS",
    "Component",
    "extract_components",
    "summarize",
    "recover_raw_media_refs",
    "raw_segments",
    "AssetResult",
    "BatchResult",
    "format_batch",
    "classify_error",
    "AvatarCache",
    "qq_avatar_url",
    "fetch_many_avatars",
    "__version__",
]
