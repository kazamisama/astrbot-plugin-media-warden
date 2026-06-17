"""warden 核心包 —— 群聊素材守门人.

公开符号遵循最小集,具体实现分散到子模块.
"""
from warden.config import MediaWardenConfig, VERSION, EXPORT_FORMAT_VERSION
from warden.policy import MatchDecision, evaluate
from warden.components import Component, extract_components, summarize
from warden.reporter import AssetResult, BatchResult, format_batch

__version__ = VERSION
__all__ = [
    "MediaWardenConfig",
    "MatchDecision",
    "evaluate",
    "Component",
    "extract_components",
    "summarize",
    "AssetResult",
    "BatchResult",
    "format_batch",
    "__version__",
    "EXPORT_FORMAT_VERSION",
]
