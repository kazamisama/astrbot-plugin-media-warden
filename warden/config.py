"""MediaWardenConfig —— 从 AstrBot 传入的配置 dict 构造 dataclass.

Phase 3 (v1.2) 仅做字段归一与合法性校验,不接触文件系统.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


VERSION = "1.3.5"
EXPORT_FORMAT_VERSION = "1.0"


@dataclass
class MediaWardenConfig:
    target_groups: List[str] = field(default_factory=list)
    target_users: List[str] = field(default_factory=list)
    match_mode: str = "whitelist"
    storage_root: str = "data/warden"
    filename_pattern: str = (
        "{platform}/{group_id}/{date}/"
        "{sender_id}_{msg_id}_{idx}_{safe_name}.{ext}"
    )
    forward_render_mode: str = "image"
    forward_image_engine: str = "pil"
    forward_image_width: int = 720
    reply_to_original: bool = True
    reply_preview: bool = True
    max_file_size_mb: int = 100
    dedupe: bool = True
    enable_index_db: bool = True
    log_to_stdout: bool = True
    download_retries: int = 2
    max_concurrent: int = 4

    max_file_size_bytes: int = field(init=False)

    def __post_init__(self):
        self.max_file_size_bytes = self.max_file_size_mb * 1024 * 1024
        if self.match_mode not in ("whitelist", "blacklist"):
            raise ValueError(f"invalid match_mode: {self.match_mode!r}")
        if self.forward_render_mode not in ("image", "json", "both"):
            raise ValueError(
                f"invalid forward_render_mode: {self.forward_render_mode!r}"
            )
        if self.forward_image_engine not in ("pil", "playwright", "wkhtml"):
            raise ValueError(
                f"invalid forward_image_engine: {self.forward_image_engine!r}"
            )

    @classmethod
    def from_raw(cls, raw: dict | None) -> "MediaWardenConfig":
        raw = raw or {}
        known = {f.name for f in cls.__dataclass_fields__.values() if f.init}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(**kwargs)

    def matched_groups_set(self) -> set[str]:
        return {str(x) for x in self.target_groups if x}

    def matched_users_set(self) -> set[str]:
        return {str(x) for x in self.target_users if x}


