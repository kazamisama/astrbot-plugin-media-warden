"""MediaWardenConfig —— 从 AstrBot 传入的配置 dict 构造 dataclass.

Phase 3 (v1.2) 仅做字段归一与合法性校验,不接触文件系统.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


VERSION = "1.9.8"
EXPORT_FORMAT_VERSION = "1.0"


@dataclass
class MediaWardenConfig:
    target_groups: List[str] = field(default_factory=list)
    target_users: List[str] = field(default_factory=list)
    match_mode: str = "whitelist"
    storage_root: str = "data/warden"
    filename_pattern: str = (
        "{group_id}/{sender_id}/{date}_{time}_{kind}_{short_id}{ext}"
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
    download_timeout_s: float = 30.0
    at_on_save_failure: bool = True
    # v1.7.0: 转发节点头像
    enable_user_avatar: bool = True
    avatar_size: int = 36
    avatar_timeout_s: int = 10
    avatar_cache_size_mb: int = 50
    # v1.8.0: 允许抓取头像的平台白名单(其他平台直接跳过)
    avatar_platforms: List[str] = field(default_factory=lambda: ["aiocqhttp", "qq_official"])
    # v1.7.1: 转发渲染上限(超过此节点数自动降级 JSON)
    forward_render_max_nodes: int = 30
    # v1.9.0: 源链接失效后的补救 + 失败入库
    get_media_fallback: bool = True
    index_reuse_saved: bool = False

    max_file_size_bytes: int = field(init=False)

    def __post_init__(self):
        self.max_file_size_bytes = self.max_file_size_mb * 1024 * 1024
        if self.download_timeout_s <= 0:
            raise ValueError(
                f"download_timeout_s must be > 0, got {self.download_timeout_s!r}"
            )
        if self.avatar_timeout_s <= 0:
            raise ValueError(
                f"avatar_timeout_s must be > 0, got {self.avatar_timeout_s!r}"
            )
        if self.avatar_size <= 0:
            raise ValueError(
                f"avatar_size must be > 0, got {self.avatar_size!r}"
            )
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
        if self.max_concurrent < 1:
            raise ValueError(
                f"max_concurrent must be >= 1, got {self.max_concurrent!r}"
            )
        if self.download_retries < 0:
            raise ValueError(
                f"download_retries must be >= 0, got {self.download_retries!r}"
            )
        if self.max_file_size_mb < 1:
            raise ValueError(
                f"max_file_size_mb must be >= 1, got {self.max_file_size_mb!r}"
            )
        if self.forward_image_width < 1:
            raise ValueError(
                f"forward_image_width must be >= 1, got {self.forward_image_width!r}"
            )
        if self.forward_render_max_nodes < 1:
            raise ValueError(
                f"forward_render_max_nodes must be >= 1, got {self.forward_render_max_nodes!r}"
            )
        if self.avatar_cache_size_mb < 0:
            raise ValueError(
                f"avatar_cache_size_mb must be >= 0, got {self.avatar_cache_size_mb!r}"
            )
        if not isinstance(self.avatar_platforms, list) or not all(
            isinstance(p, str) for p in self.avatar_platforms
        ):
            raise ValueError(
                f"avatar_platforms must be a list of strings, got {self.avatar_platforms!r}"
            )

    @classmethod
    def from_raw(cls, raw: dict | None) -> "MediaWardenConfig":
        raw = raw or {}
        known = {f.name for f in cls.__dataclass_fields__.values() if f.init}
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(**kwargs)

    @classmethod
    def from_raw_lenient(cls, raw: dict | None) -> tuple["MediaWardenConfig", List[str]]:
        """按字段容错构造:合法字段保留,非法字段跳过并记录错误."""
        raw = raw or {}
        known = {f.name for f in cls.__dataclass_fields__.values() if f.init}
        errors: List[str] = []
        kwargs: dict = {}
        for key in sorted(known):
            if key not in raw:
                continue
            candidate = dict(kwargs)
            candidate[key] = raw[key]
            try:
                cls(**candidate)
            except Exception as e:
                errors.append(f"{key}: {e}")
                continue
            kwargs = candidate
        return cls(**kwargs), errors

    def matched_groups_set(self) -> set[str]:
        # 平台名统一小写,与 policy._key 的归一规则对齐
        return {str(x).lower() for x in self.target_groups if x}

    def matched_users_set(self) -> set[str]:
        return {str(x) for x in self.target_users if x}


