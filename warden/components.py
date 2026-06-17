"""消息组件归一化 —— 把 event.message_obj 拆成 Component[].

Phase 1 仅识别,不做下载.Phase 2 接入 downloader 后会沿用 Component 抽象.

OneBot v11 segment 形态参考:
  {"type": "image",  "data": {"file": "...", "url": "..."}}
  {"type": "video",  "data": {"file": "...", "url": "..."}}
  {"type": "record", "data": {"file": "...", "url": "..."}}   # 语音/音频
  {"type": "file",   "data": {"file": "...", "name": "...", "file_size": "..."}}
  {"type": "json",   "data": {"data": "<json string>"}}
  {"type": "node",   "data": {"content": [segment, ...]}}     # 合并转发节点
  {"type": "text",   "data": {"text": "..."}}
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional


Kind = Literal["image", "file", "video", "voice", "forward", "json", "text", "unknown"]


@dataclass
class Component:
    kind: Kind
    url: Optional[str] = None
    file_id: Optional[str] = None
    name: Optional[str] = None
    size: Optional[int] = None
    meta: dict = field(default_factory=dict)
    raw: Any = None

    @property
    def is_media(self) -> bool:
        return self.kind in ("image", "file", "video", "voice")

    @property
    def is_forward(self) -> bool:
        return self.kind == "forward"

    @property
    def is_text_only(self) -> bool:
        return self.kind == "text"


def _coerce_one(item: Any) -> Component:
    if not isinstance(item, dict):
        return Component(kind="unknown", raw=item)

    t = (item.get("type") or "").lower()
    data = item.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if t == "image":
        return Component(
            kind="image",
            url=data.get("url"),
            file_id=data.get("file"),
            meta={"summary": data.get("summary")},
            raw=item,
        )
    if t == "video":
        return Component(
            kind="video",
            url=data.get("url"),
            file_id=data.get("file"),
            raw=item,
        )
    if t in ("record", "voice", "audio"):
        return Component(
            kind="voice",
            url=data.get("url"),
            file_id=data.get("file"),
            raw=item,
        )
    if t == "file":
        size_raw = data.get("file_size")
        try:
            size = int(size_raw) if size_raw not in (None, "") else None
        except (TypeError, ValueError):
            size = None
        return Component(
            kind="file",
            url=data.get("url"),
            file_id=data.get("file"),
            name=data.get("name"),
            size=size,
            raw=item,
        )
    if t == "json":
        return Component(kind="json", meta={"data": data.get("data")}, raw=item)
    if t == "node":
        nodes = data.get("content") if isinstance(data, dict) else None
        return Component(kind="forward", meta={"nodes": nodes}, raw=item)
    if t == "text":
        return Component(kind="text", name=data.get("text"), raw=item)

    return Component(kind="unknown", meta={"type": t}, raw=item)


def extract_components(event) -> List[Component]:
    """从 event.message_obj 抽 Component[].

    支持形态:
      1) event.message_obj.message 是 list[dict] (OneBot v11)
      2) event.message_obj.message 是 str (纯文本)
      3) event.message_obj.segments (自定义平台)
      4) 全部失败时退化到 message_str 文本
    """
    obj = getattr(event, "message_obj", None)
    if obj is None:
        text = getattr(event, "message_str", "") or ""
        return [Component(kind="text", name=text)] if text else []

    msg = getattr(obj, "message", None)

    if isinstance(msg, list):
        return [_coerce_one(x) for x in msg]
    if isinstance(msg, str):
        return [Component(kind="text", name=msg)] if msg else []

    segments = getattr(obj, "segments", None)
    if isinstance(segments, list):
        return [_coerce_one(x) for x in segments]

    text = getattr(event, "message_str", "") or ""
    return [Component(kind="text", name=text)] if text else []


def summarize(components: List[Component]) -> dict:
    """统计各类型数量 —— 方便 reporter 输出."""
    from collections import Counter
    return dict(Counter(c.kind for c in components))
