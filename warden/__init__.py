"""warden 核心包 —— 群聊素材守门人.

公开符号遵循最小集,具体实现分散到子模块.
"""
from .config import MediaWardenConfig, VERSION, EXPORT_FORMAT_VERSION
from .policy import (
    MatchDecision, evaluate,
    event_platform, event_group_id, event_sender_id, event_sender_name,
)
from .components import Component, extract_components, summarize
from .reporter import AssetResult, BatchResult, format_batch

__version__ = VERSION
__all__ = [
    "MediaWardenConfig",
    "MatchDecision",
    "evaluate",
    "event_platform",
    "event_group_id",
    "event_sender_id",
    "event_sender_name",
    "Component",
    "extract_components",
    "summarize",
    "AssetResult",
    "BatchResult",
    "format_batch",
    "__version__",
    "EXPORT_FORMAT_VERSION",
]
