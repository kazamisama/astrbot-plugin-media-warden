"""MediaWardenConfig —— 从 AstrBot 传入的配置 dict 构造 dataclass.

Phase 3 (v1.2) 仅做字段归一与合法性校验,不接触文件系统.
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import List


VERSION = "1.20.2"
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# 导出文件的 version 是 index.export_json 里写死的整数 1(见 warden/index.py),
# 这里曾有一个从未被使用的 EXPORT_FORMAT_VERSION="1.0" —— 两个"版本号"互不相干,
# 只会误导人,已删除.


@dataclass
class MediaWardenConfig:
    # ---- v1.14.0 采集范围 ----
    # 语义（用户 2026-10-28 定）：whitelist 模式下**空 = 不保存**（安全默认），
    # target_users 里写 "*" = 该群所有人。blacklist 模式沿用旧语义（空 = 不限），
    # 作为「想全存」的逃生口。
    target_groups: List[str] = field(default_factory=list)
    target_users: List[str] = field(default_factory=list)
    match_mode: str = "whitelist"
    # ---- v1.14.0 保存哪些类型（六类各自开关） ----
    save_images: bool = True
    save_gifs: bool = True
    save_videos: bool = True
    save_files: bool = True
    save_forwards: bool = True
    # v1.17.0: 语音以前不在开关表里，而 kind_allowed() 对未知类型一律放行 ——
    # 六个开关全关，语音照样落盘。补上开关，默认关（用户从未配置过这一类）。
    save_voice: bool = False
    # 纯文本是 v1.14.0 新增能力（以前纯文本根本不进保存流程），默认关
    save_texts: bool = False
    # 文档类扩展名白名单（不含点，大小写不敏感）；空列表 = 不限制
    doc_ext_whitelist: List[str] = field(default_factory=lambda: [
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
        "txt", "md", "csv", "zip", "7z", "rar", "epub",
    ])
    # ---- v1.14.0 聊天记录自动转发（可选，默认关） ----
    auto_forward_enabled: bool = False
    # native  = 转发原消息（保气泡/挂件/头像/原时间戳）。能否成功只看 NapCat 的
    #           进程内短 id 缓存（LRU 5000，重启清空），与目标群无关。
    # rebuild = 用存档重拼合并转发（无装扮，永远可用）
    # v1.17.0 曾误判「跨群原地转发必失败」而把默认改成 rebuild，v1.17.1 恢复 native；
    # 缓存里没有原消息时会自动回退 rebuild。
    auto_forward_mode: str = "native"
    forward_target_groups: List[str] = field(default_factory=list)
    # ---- v1.14.0 Pillow 渲染样式（样式页可改） ----
    style_width: int = 720
    style_bg_color: str = "#f5f6f7"
    style_bubble_color: str = "#ffffff"
    style_text_color: str = "#1c1f24"
    style_dim_color: str = "#8a9099"
    style_radius: int = 12
    style_font_size: int = 15
    style_avatar_shape: str = "circle"
    style_time_format: str = "%H:%M"
    style_dark: bool = False
    storage_root: str = "data/warden"
    filename_pattern: str = (
        "{group_id}/{sender_id}/{date}_{time}_{kind}_{short_id}{ext}"
    )
    # v1.16.0: 原来的三态 forward_render_mode(image/json/both)收成一个开关 ——
    # 条目里总会写 record.json,「存不存结构化」不再需要配置;只剩「画不画图」。
    forward_render_image: bool = True
    # 取值保留 pil/playwright/wkhtml 三个历史槽位,但只有 pil 落地:
    # main.initialize() 会对非 pil 打一条明确的告警并继续用 pil(不静默生效)。
    forward_image_engine: str = "pil"
    # 画布宽度只有一个真源:style_width(样式页/样式组)。旧的 forward_image_width
    # 在 v1.15.0 删除 —— 它总是被 apply_style() 覆盖,留在 schema 里只会让人以为
    # 是两个不同的旋钮。    # v1.10.0: 以前这个开关是空壳(代码里从没读过),实际行为 = 不引用。
    # 现在接上了 Reply 组件,默认值保持 false 以免升级后突然给每条回执加引用。
    reply_to_original: bool = False
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

        # v1.18.0: 只有 pil 实现。playwright / wkhtml 的旧值不再受理(以前是
        # 「受理但告警」的兼容分支,现在直接报配置错误)。
        if self.forward_image_engine != "pil":
            raise ValueError(
                f"invalid forward_image_engine: {self.forward_image_engine!r} "
                f"(only 'pil' is implemented)"
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

        if self.auto_forward_mode not in ("rebuild", "native"):
            raise ValueError(
                f"invalid auto_forward_mode: {self.auto_forward_mode!r}"
            )
        if self.style_avatar_shape not in ("circle", "square"):
            raise ValueError(
                f"invalid style_avatar_shape: {self.style_avatar_shape!r}"
            )
        for name in ("style_bg_color", "style_bubble_color", "style_text_color",
                     "style_dim_color"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
                raise ValueError(f"{name} 需要 #rrggbb 形式的颜色, got {value!r}")
        if not 200 <= int(self.style_width) <= 3000:
            raise ValueError(f"style_width 需要在 200..3000, got {self.style_width!r}")
        if not 8 <= int(self.style_font_size) <= 48:
            raise ValueError(
                f"style_font_size 需要在 8..48, got {self.style_font_size!r}"
            )
        if not 0 <= int(self.style_radius) <= 96:
            raise ValueError(f"style_radius 需要在 0..96, got {self.style_radius!r}")
        for name in ("doc_ext_whitelist", "forward_target_groups"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError(f"{name} 需要字符串列表, got {value!r}")
        # 扩展名归一：去点、去空白、小写（配置里写 ".PDF" 也能用）
        self.doc_ext_whitelist = [
            str(x).strip().lower().lstrip(".") for x in self.doc_ext_whitelist
            if str(x).strip() and str(x).strip().lower().lstrip(".")
        ]
        self.forward_target_groups = [
            str(x).strip() for x in self.forward_target_groups if str(x).strip()
        ]

    # ---- v1.14.0 便捷判定 ----

    def doc_ext_allowed(self, ext: str) -> bool:
        """文档扩展名是否在白名单里；白名单为空 = 不限制。

        入参可以是扩展名（"pdf" / ".pdf"）也可以是文件名（"报表.pdf"）——
        调用方手上通常只有 name，让它在两处都做一次归一比要求调用方记得更稳。
        """
        text = str(ext or "").strip().lower()
        if not self.doc_ext_whitelist:
            return True
        if not text:
            return False
        stem, dot_ext = os.path.splitext(text)
        cleaned = (dot_ext or text).lstrip(".")
        return cleaned in self.doc_ext_whitelist

    def save_type_flags(self) -> dict:
        return {
            "image": bool(self.save_images),
            "gif": bool(self.save_gifs),
            "video": bool(self.save_videos),
            "file": bool(self.save_files),
            "text": bool(self.save_texts),
            "forward": bool(self.save_forwards),
            "voice": bool(self.save_voice),
        }

    def forward_targets_set(self) -> list:
        out: list = []
        for v in self.forward_target_groups:
            if v not in out:
                out.append(v)
        return out

    @staticmethod
    def _flatten_raw(raw: dict) -> dict:
        """把 AstrBot 传来的分组配置摊平。

        AstrBot 用 `_conf_schema.json` 的顶层键**分组嵌套**传配置（`{"filter": {...},
        "render_style": {...}}`），而本类的字段是扁平的。v1.10.0 把 schema 改成
        分组之后，这里一直只读顶层键 → WebUI 里改的所有值都被静默忽略（当时
        各项默认值恰好等于用户配置，所以没人发现）。v1.14.0 的「白名单空列表 =
        不保存」让这个 bug 变成实伤：日志里 groups=0 而配置文件里明明写着群号。
        规则：同名键**顶层优先**（样式页等直接写顶层的调用方仍有效），嵌套值只
        在顶层缺失时补进来。
        """
        flat: dict = {}
        for value in raw.values():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    flat.setdefault(sub_key, sub_value)
        for key, value in raw.items():
            if not isinstance(value, dict):
                flat[key] = value
        return flat

    @classmethod
    def from_raw_lenient(cls, raw: dict | None) -> tuple["MediaWardenConfig", List[str]]:
        """按字段容错构造:合法字段保留,非法字段跳过并记录错误."""
        raw = cls._flatten_raw(raw or {})
        # v1.18.0: 不再为旧配置键做派生。schema 之外/已删除的键会被直接忽略,
        # 想改行为请在 WebUI 里改当前 schema 的键。
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


