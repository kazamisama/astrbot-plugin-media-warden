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
# 迁移自「扁平 forward JSON」的条目会在自己目录里留一份原始文件(取证/回滚用)


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


def _payload(node: dict) -> dict:
    """取节点真正的数据体。

    合并转发节点有两种形态:get_forward_msg 给的**裸节点**(sender/message 在顶层),
    以及消息段里的**包装段** `{"type": "node", "data": {sender, message}}`。
    后者以前会被当成「没有 sender、没有 message」的空节点 —— 内联节点(客户端把内容
    直接塞进段里、没有可取 id 的那种)因此在存档里变成空壳。这里统一剥一层。
    """
    data = node.get("data") if isinstance(node.get("data"), dict) else None
    if data and any(k in data for k in ("message", "content", "sender")):
        return data
    return node


def _segments(node: dict) -> list:
    body = _payload(node)
    segs = body.get("message")
    if not isinstance(segs, list):
        segs = body.get("content")
    return [s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []


def node_identity(node: dict) -> tuple[str, str]:
    """(uin, 显示名) —— 合并转发节点的发送者(包装段会先剥一层)。"""
    body = _payload(node)
    sender = body.get("sender") if isinstance(body.get("sender"), dict) else {}
    uin = sender.get("user_id", body.get("user_id", 0))
    name = (sender.get("card") or sender.get("nickname")
            or body.get("card") or body.get("nickname")
            or _safe_token(uin))
    return str(uin), str(name)


def uin_reliable(nodes: list) -> bool:
    """这条记录里的 uin 是否可信。

    NapCat 的 get_forward_msg 在某些转发里给**每个节点同一个 user_id**
    （实测：不同昵称的节点全是同一个号），那是转发创建者/占位值，不是发送者。
    「一个 uin 对应多个昵称」就说明这个字段不可信 —— 页面不该把它当发送者
    QQ 号显示，否则会看成「两个人共用一个号」。
    """
    uins = {str(n.get("uin") or "") for n in nodes if isinstance(n, dict)}
    names = {str(n.get("name") or "") for n in nodes if isinstance(n, dict)}
    return not (len(uins) == 1 and len(names) > 1)


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


def dry_nodes(nodes: list) -> list:
    """只归一化结构、不落盘也不下图——用来算内容指纹做去重判断。

    形状必须与 capture() 最后写进 record.json 的节点一致(同样含 index/uin/
    name/time/segments),否则两边的 origin_sha256 对不上。
    """
    out: list = []
    for ni, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        uin, name = node_identity(node)
        segments: list = []
        for seg in _segments(node):
            kind = str(seg.get("type") or "").lower()
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            segments.append({"type": kind, "data": dict(data)})
        out.append({"index": ni, "uin": uin, "name": name,
                    "time": int(_payload(node).get("time") or 0),
                    "segments": segments})
    return out


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
    # 同群同内容已经存过 → 直接复用那条目录，不再建新目录、也不再下图。
    # 指纹只归一化结构（段 data 原样），与后面写进 record.json 的节点同形。
    dry = dry_nodes(nodes)
    origin = _canonical_sha256(dry)
    dup = find_duplicate(storage_root, origin, exclude=rid, group_id=group_id)
    if dup:
        existing = load_record(storage_root, dup)
        if existing:
            existing = dict(existing)
            existing["reused"] = True
            existing["duplicate_of"] = dup
            existing["duplicate_of_requested"] = rid
            return existing
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
                          "time": int(_payload(node).get("time") or 0),
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
        "uin_reliable": uin_reliable(out_nodes),
        "origin_sha256": _canonical_sha256(out_nodes),
    }
    # 同内容被转发两次 → 后来的记下先来的 id（不合并目录，重发要各用各的 msg_id）
    record["duplicate_of"] = find_duplicate(
        storage_root, record["origin_sha256"], exclude=rid, group_id=group_id)
    with open(os.path.join(root, "record.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    return record


def find_duplicate(storage_root: str, origin_sha256: str, *,
                   exclude: str = "", group_id: Optional[str] = None,
                   kind: Optional[str] = None,
                   sender_id: Optional[str] = None) -> str:
    """同群同内容条目先来的那条 id（没有则空串）。

    扁平文件有 Storage 的 blake2b 去重、索引也有 sha 行；`_records/` 这一层以前
    是「一个 record_id 一个目录」，同一条消息被转发两次就是两个目录、两份图片
    字节。现在 capture()/capture_text() 在这里**直接复用**已有目录（不再建新
    目录、也不再下图），并把原来的 record_id 记进 `duplicate_of_requested`。
    只在**同一个群**内比对：同一内容出现在别的群各存一份是对的（每群语境不同）。

    v1.17.0: 纯文本还要按 kind + sender_id 收窄 —— 正文相同不代表是同一条记录。
    「alice 发 666、bob 也发 666」以前会被当成重复，第二条直接复用 alice 的条目
    （发送者/时间全是错的，渲染图还写进 alice 的目录）。聊天记录(forward)仍然
    只按内容+群比对：同一份转发被转两次，内容与发送者集合确实相同。
    """
    if not origin_sha256:
        return ""
    base = records_root(storage_root)
    if not os.path.isdir(base):
        return ""
    for name in sorted(os.listdir(base)):
        if name == exclude:
            continue
        root = record_dir(storage_root, name)
        for fname in (RECORD_FILENAME, ENTRY_FILENAME):
            path = os.path.join(root, fname)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    other = json.load(fh)
            except (OSError, ValueError):
                continue
            if str(other.get("origin_sha256") or "") != origin_sha256:
                continue
            # 只比同一个群:同一份内容在不同群各存一份是对的(每群的存档语境不同),
            # 跨群合并会让后一个群的记录指向别的群。
            if group_id is not None and str(other.get("group_id") or "") != str(group_id):
                continue
            # 纯文本:正文相同但发送者不同 → 不是同一条记录,不能复用目录
            if kind is not None and str(other.get("kind") or "") != str(kind):
                continue
            if (sender_id is not None
                    and str(other.get("sender_id") or "") != str(sender_id)):
                continue
            return str(other.get("record_id") or name)
    return ""



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

    cleaned: list = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        kind = str(seg.get("type") or "").lower()
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        cleaned.append({"type": kind, "data": dict(data)})

    origin = _canonical_sha256(cleaned)
    dup = find_duplicate(storage_root, origin, exclude=rid, group_id=group_id,
                         kind="text", sender_id=str(sender_id or ""))
    if dup:
        existing = load_entry(storage_root, dup)
        if existing:
            existing = dict(existing)
            existing["reused"] = True
            existing["duplicate_of"] = dup
            existing["duplicate_of_requested"] = rid
            return existing
    root = record_dir(storage_root, rid)
    os.makedirs(root, exist_ok=True)

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
        "origin_sha256": origin,
    }
    entry["duplicate_of"] = find_duplicate(
        storage_root, origin, exclude=rid, group_id=group_id,
        kind="text", sender_id=str(sender_id or ""))
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


def set_meta(storage_root: str, record_id: str, **fields) -> bool:
    """给条目元数据打标（record.json / entry.json 都适用），返回是否写到盘上。

    用于记录「原消息已失效」这类**跨会话仍要记得**的事实：只放内存里的话，
    刷新页面又变回一个看起来能点的按钮（原消息早就过期了）。
    """
    name = meta_filename(storage_root, record_id)
    if name is None:
        return False
    meta = _load_meta(storage_root, record_id)
    if meta is None:
        return False
    meta.update(fields)
    try:
        with open(os.path.join(record_dir(storage_root, record_id), name),
                  "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True


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
