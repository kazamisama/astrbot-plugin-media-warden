"""聊天记录存档（原型 v0.1）—— 把合并转发的原始 OneBot 节点落成自包含目录。

为什么不用现成的 forward_json sidecar：那个 JSON 只是为了「渲染不出图时留个底」，
里面的图片还是死的 url（rkey 一过期就废），重发时没法用。存档必须把节点里的
媒体字节一起落盘，否则「保存再发送」在第二天就必然失败。

目录结构（每个记录一个目录，SQLite 只会在以后做索引，文件才是真相）:

    <storage_root>/_records/<record_id>/
        record.json          # 节点结构 + 资产索引
        assets/<sha16>.<ext> # 节点内的图片字节

record.json 结构见 README「聊天记录存档」或本模块下方 capture() 的返回值。

设计要点：

* 原始段一律原样保留（包括 url / file），重发时优先用 assets 里的字节，
  找不到才退回 url —— 存档是先快照、后渲染。
* 资产按下标 (node, seg) 关联，不篡改段内容，重发失败时还能看出原始结构。
* build_nodes 只在真正要发消息时 import AstrBot 组件，模块本身离线可测。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Awaitable, Callable, Optional

SCHEMA_VERSION = 1
ENTRY_SCHEMA_VERSION = 1
RECORDS_DIRNAME = "_records"
ENTRY_FILENAME = "entry.json"
RECORD_FILENAME = "record.json"
RENDER_FILENAME = "render.png"


def _safe_token(value: Any) -> str:
    """把任意 id 变成可放进路径的片段（只留字母数字和 - _）。"""
    text = str(value if value is not None else "")
    out = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in text)
    return out.strip("_") or "0"


def make_record_id(group_id: Any, msg_id: Any, ts: Optional[int] = None) -> str:
    """记录 id：时间在前（目录天然按时间排），后面跟群号和消息号。"""
    stamp = time.strftime("%Y%m%d-%H%M%S",
                          time.localtime(ts if ts is not None else time.time()))
    return f"{stamp}-{_safe_token(group_id)}-{_safe_token(msg_id)}"


def records_root(storage_root: str) -> str:
    return os.path.join(storage_root, RECORDS_DIRNAME)


def record_dir(storage_root: str, record_id: str) -> str:
    return os.path.join(records_root(storage_root), _safe_token(record_id))


def _segments(node: dict) -> list:
    segs = node.get("message")
    if not isinstance(segs, list):
        segs = node.get("content")
    return [s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []


def node_identity(node: dict) -> tuple[str, str]:
    """(uin, 显示名) —— 合并转发节点的发送者。"""
    sender = node.get("sender") if isinstance(node.get("sender"), dict) else {}
    uin = sender.get("user_id", node.get("user_id", 0))
    name = sender.get("card") or sender.get("nickname") or _safe_token(uin)
    return str(uin), str(name)


def image_ref(data: dict) -> Optional[str]:
    """段里能拿去下载的图片地址：优先 url，其次 file（偶尔只给 http）。"""
    url = data.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    fid = data.get("file")
    if isinstance(fid, str) and fid.startswith(("http://", "https://")):
        return fid
    return None


def _guess_ext(data: bytes, ref: dict) -> str:
    """先用魔数，猜不出再看段里的文件名。"""
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    if data.startswith(b"GIF8"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    name = ref.get("file")
    if isinstance(name, str):
        _stem, ext = os.path.splitext(name)
        if ext and len(ext) <= 6 and ext[1:].isalnum():
            return ext.lower()
    return ".bin"


def _canonical_sha256(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def capture(
    nodes: list,
    *,
    platform: str,
    group_id: Any,
    msg_id: Any,
    storage_root: str,
    fetch_image: Callable[[dict], Awaitable[Optional[bytes]]],
    ts: Optional[int] = None,
    record_id: Optional[str] = None,
) -> dict:
    """把 OneBot 节点列表存成一个自包含目录，返回 record dict。

    fetch_image 收一个 image 段的 data，返回字节；失败返回 None ——
    单张图失败不影响整条存档（会在 record 的 missing 里留痕）。
    """
    captured = int(ts if ts is not None else time.time())
    rid = record_id or make_record_id(group_id, msg_id, captured)
    root = record_dir(storage_root, rid)
    assets_dir = os.path.join(root, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    out_nodes: list = []
    assets: list = []
    missing: list = []

    for ni, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        uin, name = node_identity(node)
        segments: list = []
        for si, seg in enumerate(_segments(node)):
            kind = str(seg.get("type") or "").lower()
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            segments.append({"type": kind, "data": dict(data)})
            if kind != "image":
                continue
            url = image_ref(data)
            if url is None:
                # 段里没有可下载地址（比如只有 file=md5.jpg）—— 记下来，不猜
                missing.append({"node": ni, "seg": si, "reason": "no_url"})
                continue
            try:
                blob = await fetch_image(data)
            except Exception:  # 存档不能因为单张图炸掉整条记录
                blob = None
            if not blob:
                missing.append({"node": ni, "seg": si,
                                "reason": "download_failed", "url": url})
                continue
            sha16 = hashlib.blake2b(blob, digest_size=8).hexdigest()
            rel = f"assets/{sha16}{_guess_ext(blob, data)}"
            full = os.path.join(root, rel.replace("/", os.sep))
            if not os.path.exists(full):
                with open(full, "wb") as fh:
                    fh.write(blob)
            assets.append({"node": ni, "seg": si, "path": rel,
                           "sha16": sha16, "bytes": len(blob)})
        out_nodes.append({"index": ni, "uin": uin, "name": name,
                          "time": int(node.get("time") or 0),
                          "segments": segments})

    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "forward",
        "record_id": rid,
        "platform": platform,
        "group_id": str(group_id or ""),
        "root_msg_id": str(msg_id or ""),
        "captured_ts": captured,
        "node_count": len(out_nodes),
        "nodes": out_nodes,
        "assets": assets,
        "missing": missing,
        "render": None,
        "origin_sha256": _canonical_sha256(out_nodes),
    }
    with open(os.path.join(root, "record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    return record


def _entry_path(storage_root: str, record_id: str) -> str:
    return os.path.join(record_dir(storage_root, record_id), ENTRY_FILENAME)


def meta_filename(storage_root: str, record_id: str) -> Optional[str]:
    """条目目录里那份元数据文件名:entry.json(纯文本) 或 record.json(聊天记录).

    v1.14.0 起两种条目共用一个目录形态,只有元数据文件名不同 —— 渲染图登记在
    各自的元数据里,所以读写渲染字段前必须先问这份文件是谁。
    """
    root = record_dir(storage_root, record_id)
    for name in (ENTRY_FILENAME, RECORD_FILENAME):
        if os.path.isfile(os.path.join(root, name)):
            return name
    return None


async def capture_text(
    segments: list,
    *,
    platform: str,
    group_id: Any,
    msg_id: Any,
    sender_id: Any,
    sender_name: str,
    storage_root: str,
    ts: Optional[int] = None,
    record_id: Optional[str] = None,
) -> dict:
    """把一条**纯文本消息**存成条目（与聊天记录共用 _records/<id>/ 目录）。

    纯文本的「正常形式」是 Pillow 渲染图，但文字本身带元数据（谁/何时/说了什么）
    必须留一份结构化的，否则样式改了以后没法重渲染，也没法搜索。
    所以：entry.json（结构化）+ render.png（渲染图），缺一不可。
    """
    captured = int(ts if ts is not None else time.time())
    rid = record_id or make_record_id(group_id, msg_id, captured)
    root = record_dir(storage_root, rid)
    os.makedirs(root, exist_ok=True)

    cleaned: list = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        kind = str(seg.get("type") or "").lower()
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        cleaned.append({"type": kind, "data": dict(data)})

    entry = {
        "schema_version": ENTRY_SCHEMA_VERSION,
        "kind": "text",
        "record_id": rid,
        "platform": platform,
        "group_id": str(group_id or ""),
        "root_msg_id": str(msg_id or ""),
        "sender_id": str(sender_id or ""),
        "sender_name": str(sender_name or ""),
        "captured_ts": captured,
        "node_count": 1,
        "segments": cleaned,
        "assets": [],
        "missing": [],
        "render": None,
        "origin_sha256": _canonical_sha256(cleaned),
    }
    with open(_entry_path(storage_root, rid), "w", encoding="utf-8") as fh:
        json.dump(entry, fh, ensure_ascii=False, indent=2)
    return entry


def load_entry(storage_root: str, record_id: str) -> Optional[dict]:
    try:
        with open(_entry_path(storage_root, record_id), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_meta(storage_root: str, record_id: str) -> Optional[dict]:
    """读条目元数据:优先 entry.json,退回 record.json。"""
    entry = load_entry(storage_root, record_id)
    if entry is not None:
        return entry
    return load_record(storage_root, record_id)


def has_render(storage_root: str, record_id: str) -> Optional[str]:
    """条目有渲染图就返回文件名，没有返回 None（只认登记过的 render 字段）。"""
    meta = _load_meta(storage_root, record_id)
    name = (meta or {}).get("render")
    if not isinstance(name, str) or not name:
        return None
    if os.path.isfile(os.path.join(record_dir(storage_root, record_id), name)):
        return name
    return None


def save_render(storage_root: str, record_id: str, blob: bytes) -> Optional[str]:
    """把渲染图写进条目目录并登记到该条目的元数据；没有元数据时不写。"""
    name = meta_filename(storage_root, record_id)
    if name is None or not blob:
        return None
    meta = _load_meta(storage_root, record_id)
    if meta is None:
        return None
    root = record_dir(storage_root, record_id)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, RENDER_FILENAME), "wb") as fh:
        fh.write(blob)
    meta["render"] = RENDER_FILENAME
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return RENDER_FILENAME


def load_record(storage_root: str, record_id: str) -> Optional[dict]:
    path = os.path.join(record_dir(storage_root, record_id), "record.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_records(storage_root: str, limit: int = 20) -> list:
    """按 captured_ts 倒序列出条目摘要（聊天记录 + 纯文本，两种共用 _records/）。

    只读元数据文件，不碰资产。
    """
    root = records_root(storage_root)
    if not os.path.isdir(root):
        return []
    out: list = []
    for name in os.listdir(root):
        rec = load_record(storage_root, name)
        if rec:
            out.append({
                "record_id": rec.get("record_id", name),
                "kind": "forward",
                "captured_ts": rec.get("captured_ts", 0),
                "node_count": rec.get("node_count", 0),
                "asset_count": len(rec.get("assets") or []),
                "group_id": rec.get("group_id", ""),
                "root_msg_id": rec.get("root_msg_id", ""),
            })
            continue
        entry = load_entry(storage_root, name)
        if not entry:
            continue
        out.append({
            "record_id": entry.get("record_id", name),
            "kind": entry.get("kind") or "text",
            "captured_ts": entry.get("captured_ts", 0),
            "node_count": 1,
            "asset_count": 0,
            "group_id": entry.get("group_id", ""),
            "root_msg_id": entry.get("root_msg_id", ""),
            "sender_name": entry.get("sender_name", ""),
        })
    out.sort(key=lambda r: r["captured_ts"], reverse=True)
    return out[:limit]


def _asset_index(record: dict) -> dict:
    return {(a.get("node"), a.get("seg")): a
            for a in record.get("assets") or [] if isinstance(a, dict)}


def build_nodes(record: dict, base_dir: str):
    """把 record 还原成 AstrBot 的 Node 列表（仅在真的要发消息时调用）。"""
    from astrbot.api.message_components import At, Face, Image, Node, Plain

    assets = _asset_index(record)
    out = []
    for node in record.get("nodes") or []:
        ni = node.get("index")
        comps = []
        for si, seg in enumerate(node.get("segments") or []):
            kind = seg.get("type")
            data = seg.get("data") or {}
            if kind == "text":
                comps.append(Plain(text=str(data.get("text") or "")))
            elif kind == "face":
                comps.append(Face(id=int(data.get("id") or 0)))
            elif kind == "at":
                comps.append(At(qq=str(data.get("qq") or "all")))
            elif kind == "image":
                asset = assets.get((ni, si))
                if not asset:
                    continue
                path = os.path.join(
                    base_dir, str(asset.get("path")).replace("/", os.sep))
                try:
                    with open(path, "rb") as fh:
                        comps.append(Image.fromBytes(fh.read()))
                except OSError:
                    continue
            else:
                # 语音/视频/未知段：合并转发里本来就常放不出来，先留个占位
                comps.append(Plain(text=f"[{kind}]"))
        out.append(Node(content=comps, name=str(node.get("name") or ""),
                        uin=str(node.get("uin") or "0")))
    return out
