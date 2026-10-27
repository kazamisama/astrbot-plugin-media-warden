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

AstrBot 的 message_obj.message 有两种形态:OneBot dict 段,或 AstrBot 自己的
Pydantic 组件对象。两者字段语义一致(只是取值方式不同),所以这里用 _Fields
适配器统一到同一条归一化路径 —— v1.10 之前是两份平行实现,已经出现过
face 段 / image meta 的形态分叉。
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


def _comp_type_name(item: Any) -> Optional[str]:
    """AstrBot Pydantic 组件对象的 type 是 ComponentType 枚举，取其字符串名."""
    t = getattr(item, "type", None)
    if t is None:
        return None
    val = getattr(t, "value", None)
    name = str(val if val is not None else t)
    return name.lower()


class _Fields:
    """统一的字段取值器:对象走 getattr,OneBot data 走 dict.get."""

    __slots__ = ("_get",)

    def __init__(self, getter) -> None:
        self._get = getter

    @classmethod
    def of_obj(cls, item: Any) -> "_Fields":
        return cls(lambda n: getattr(item, n, None))

    @classmethod
    def of_dict(cls, data: dict) -> "_Fields":
        return cls(data.get)

    def first(self, *names: str):
        for n in names:
            v = self._get(n)
            if v not in (None, ""):
                return v
        return None

    def get(self, name: str):
        return self._get(name)


def _str_or_none(v) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _media_url(f: _Fields) -> Optional[str]:
    """图片/视频/语音的下载源:优先 url;没有 url 时 file 若是 http 就用它.

    (AstrBot 的 Image 有时把 http 链接放在 file 上。)
    """
    url = _str_or_none(f.first("url"))
    if url:
        return url
    fid = _str_or_none(f.first("file"))
    if fid and fid.startswith(("http://", "https://")):
        return fid
    return None


def _int_or_none(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce(t: Optional[str], f: _Fields, raw: Any) -> Component:
    """唯一归一化路径: t 是段/组件类型名(已小写), f 是字段取值器."""
    t = (t or "").lower()

    if t == "image":
        meta = {}
        for key in ("path", "summary"):
            v = f.get(key)
            if v not in (None, ""):
                meta[key] = v
        return Component(
            kind="image",
            url=_media_url(f),
            file_id=_str_or_none(f.first("file", "file_id")),
            name=_str_or_none(f.first("name", "summary")),
            size=_int_or_none(f.first("size", "file_size")),
            meta=meta,
            raw=raw,
        )
    if t == "video":
        return Component(
            kind="video",
            url=_media_url(f),
            file_id=_str_or_none(f.first("file")),
            name=_str_or_none(f.first("name")),
            size=_int_or_none(f.first("size", "file_size")),
            raw=raw,
        )
    if t in ("record", "voice", "audio"):
        return Component(
            kind="voice",
            url=_media_url(f),
            file_id=_str_or_none(f.first("file")),
            name=_str_or_none(f.first("name")),
            size=_int_or_none(f.first("size", "file_size")),
            raw=raw,
        )
    if t == "file":
        return Component(
            kind="file",
            url=_str_or_none(f.first("url")),
            file_id=_str_or_none(f.first("file")),
            name=_str_or_none(f.first("name")),
            size=_int_or_none(f.first("size", "file_size")),
            raw=raw,
        )
    if t == "face":
        # QQ 内置表情:只有 id,无图片数据,归为 json 元数据保存。
        # (v1.10 起 OneBot dict 段也走这条,不再落到 unknown)
        return Component(
            kind="json",
            meta={"data": {"face_id": f.first("id")}},
            raw=raw,
        )
    if t == "json":
        return Component(kind="json", meta={"data": f.first("data")}, raw=raw)
    if t in ("node", "nodes", "forward"):
        return Component(
            kind="forward",
            meta={
                "nodes": f.first("content", "nodes"),
                "forward_id": f.first("id", "resid", "res_id"),
            },
            raw=raw,
        )
    if t in ("plain", "text"):
        return Component(kind="text", name=_str_or_none(f.first("text")), raw=raw)
    return Component(kind="unknown", meta={"type": t}, raw=raw)


def _coerce_obj(item: Any) -> Component:
    """处理 AstrBot 消息组件对象（Image/Face/Video/Record/File/Node/Forward/Json/Plain/At ...）."""
    return _coerce(_comp_type_name(item), _Fields.of_obj(item), item)


def _coerce_one(item: Any) -> Component:
    if not isinstance(item, dict):
        if getattr(item, "type", None) is not None:
            return _coerce_obj(item)
        return Component(kind="unknown", raw=item)

    data = item.get("data")
    if not isinstance(data, dict):
        data = {}
    return _coerce(item.get("type"), _Fields.of_dict(data), item)


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
    """从 event.message_obj.raw_message 恢复被 PreProcessStage 覆盖的媒体引用.

    AstrBot 4.26 的 PreProcessStage 会把 Image 组件转成 jpeg 静帧,
    并覆盖 component 的 url/file/path; 而 raw_message 里 napcat 保留原始数据,
    其中 image 段的 data.url 通常是原始 http 链接(例如动图 gif).

    Returns:
        List[dict]: 恢复出的媒体引用列表, 每项包含
        {"kind","url","file","name","size"}; url/file 可能为 None.
        非 OneBot 形态或无 raw_message 时返回 [].
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
        out.append({
            "kind": kind,
            "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
            "file": data.get("file"),
            "name": data.get("file") or data.get("name"),
            "size": _int_or_none(data.get("file_size") if data.get("file_size") not in (None, "") else data.get("size")),
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
