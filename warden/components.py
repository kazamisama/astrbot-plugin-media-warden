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


def _comp_type_name(item: Any) -> Optional[str]:
    """AstrBot Pydantic 组件对象的 type 是 ComponentType 枚举，取其字符串名."""
    t = getattr(item, "type", None)
    if t is None:
        return None
    val = getattr(t, "value", None)
    name = str(val if val is not None else t)
    return name.lower()


def _coerce_obj(item: Any) -> Component:
    """处理 AstrBot 消息组件对象（Image/Face/Video/Record/File/Node/Forward/Json/Plain/At ...）."""
    tn = _comp_type_name(item)
    g = lambda *names: next(
        (v for n in names for v in [getattr(item, n, None)] if v not in (None, "")),
        None,
    )

    if tn == "image":
        # Image.file 可能是 url / file:// / base64:// / 本地路径
        f = g("url") or g("file")
        return Component(
            kind="image",
            url=g("url") or (f if isinstance(f, str) and f.startswith("http") else None),
            file_id=g("file"),
            meta={"path": getattr(item, "path", None)},
            raw=item,
        )
    if tn == "video":
        f = g("file")
        return Component(
            kind="video",
            url=g("url") or (f if isinstance(f, str) and f.startswith("http") else None),
            file_id=g("file"),
            raw=item,
        )
    if tn in ("record", "voice", "audio"):
        f = g("file")
        return Component(
            kind="voice",
            url=g("url") or (f if isinstance(f, str) and f.startswith("http") else None),
            file_id=g("file"),
            raw=item,
        )
    if tn == "file":
        size = None
        for n in ("size", "file_size"):
            v = getattr(item, n, None)
            if v not in (None, ""):
                try:
                    size = int(v)
                    break
                except (TypeError, ValueError):
                    pass
        return Component(
            kind="file",
            url=g("url"),
            file_id=g("file"),
            name=g("name"),
            size=size,
            raw=item,
        )
    if tn == "face":
        # QQ 内置表情：只有 id，无图片数据，归为 json 元数据保存
        return Component(
            kind="json",
            meta={"data": {"face_id": getattr(item, "id", None)}},
            raw=item,
        )
    if tn == "json":
        return Component(kind="json", meta={"data": g("data")}, raw=item)
    if tn in ("node", "nodes", "forward"):
        nodes = getattr(item, "content", None)
        fwd_id = g("id", "resid", "res_id")
        return Component(
            kind="forward",
            meta={"nodes": nodes, "forward_id": fwd_id},
            raw=item,
        )
    if tn == "plain":
        return Component(kind="text", name=g("text"), raw=item)

    return Component(kind="unknown", meta={"type": tn}, raw=item)


def _coerce_one(item: Any) -> Component:
    if not isinstance(item, dict):
        if getattr(item, "type", None) is not None:
            return _coerce_obj(item)
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
    if t in ("node", "nodes", "forward"):
        nodes = data.get("content") if isinstance(data, dict) else None
        fwd_id = (
            data.get("id") or data.get("resid") or data.get("res_id")
        ) if isinstance(data, dict) else None
        return Component(
            kind="forward",
            meta={"nodes": nodes, "forward_id": fwd_id},
            raw=item,
        )
    if t == "text":
        return Component(kind="text", name=data.get("text"), raw=item)

    return Component(kind="unknown", meta={"type": t}, raw=item)


def _seg_type(seg: Any) -> Optional[str]:
    t = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", None)
    if t is None:
        return None
    val = getattr(t, "value", None)
    return str(val if val is not None else t).lower()


def _seg_data(seg: Any) -> dict:
    d = seg.get("data") if isinstance(seg, dict) else getattr(seg, "data", None)
    return d if isinstance(d, dict) else {}


_RAW_MEDIA_KINDS = {
    "image": "image",
    "video": "video",
    "record": "voice",
    "voice": "voice",
    "audio": "voice",
    "file": "file",
}


def recover_raw_media_refs(event) -> List[dict]:
    """? event.message_obj.raw_message ?????????????.

    AstrBot 4.26 ? PreProcessStage ???? Image ????????? jpeg,
    ?? component ? url/file/path; ? raw_message ?? napcat ??????,
    ?? image ?? data.url ???? http ????(????? gif).

    Returns:
        List[dict]: ??????,?? media ???
        {"kind","url","file","name","size"}; url/file ??? None.
        ? OneBot ???? raw_message ??? [].
    """
    obj = getattr(event, "message_obj", None)
    raw = getattr(obj, "raw_message", None) if obj is not None else None
    if raw is None:
        return []
    segs = raw.get("message") if isinstance(raw, dict) else getattr(raw, "message", None)
    if not isinstance(segs, list):
        return []

    out: List[dict] = []
    for seg in segs:
        tn = _seg_type(seg)
        kind = _RAW_MEDIA_KINDS.get(tn or "")
        if kind is None:
            continue
        data = _seg_data(seg)
        url = data.get("url")
        size = None
        for n in ("file_size", "size"):
            v = data.get(n)
            if v not in (None, ""):
                try:
                    size = int(v)
                    break
                except (TypeError, ValueError):
                    pass
        out.append({
            "kind": kind,
            "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
            "file": data.get("file"),
            "name": data.get("file") or data.get("name"),
            "size": size,
        })
    return out


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
