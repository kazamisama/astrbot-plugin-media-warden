"""存档管理逻辑（纯函数，离线可测）。

dashboard 页面与页面 API 都只做 IO 转接，真正的扫描/搜索/删除/重发目标解析
都在这里，好处是这些行为能用 _smoke.py 直接测，不必起 AstrBot。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
from typing import Any, Optional

from . import record as record_store
from .record import (
    ENTRY_FILENAME, RECORD_FILENAME, RENDER_FILENAME,
    load_entry, load_record, meta_filename, record_dir, records_root,
    save_render, uin_reliable,
)

# v1.14.0 分类管理:一条「保存物」要么是条目(_records/ 下的聊天记录/纯文本),
# 要么是老的扁平落盘文件(图片/动图/视频/文档,索引库里一行一条)。
KINDS = ("image", "gif", "video", "file", "voice", "text", "forward")
KIND_LABELS = {
    "image": "图片", "gif": "动图", "video": "视频", "file": "文档",
    "voice": "语音", "text": "纯文本", "forward": "聊天记录",
}


def _can_native(rec: dict) -> bool:
    """能不能「转发原消息」:要有短 msg id,且没有被打上「原消息已失效」。

    失效标记由页面 resend 在 native 失败(消息不存在/取不到原消息)时写入元数据 ——
    过期是**永久**的(QQ 那边不会复活),不记住的话每次打开都还是一个主按钮。
    """
    if not bool(str(rec.get("root_msg_id") or "").isdigit()):
        return False
    return not bool(rec.get("native_unavailable"))


def _asset_bytes(rec: dict) -> int:
    return sum(int(a.get("bytes") or 0) for a in rec.get("assets") or []
               if isinstance(a, dict))


def _dir_file_bytes(root: str, name: str, filenames) -> int:
    """条目目录里这些文件的体积（render.png / entry.json）。"""
    total = 0
    base = record_dir(root, name)
    for fname in filenames:
        if not fname:
            continue
        full = os.path.join(base, fname)
        if os.path.isfile(full):
            total += os.path.getsize(full)
    return total


def _node_text(node: dict) -> str:
    """一个节点的可读文本（给列表预览 / 搜索用）。"""
    parts = []
    for seg in node.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "face":
            parts.append("[表情]")
        elif kind == "at":
            parts.append("@" + str(data.get("qq") or "all"))
    return " ".join(p for p in parts if p)


def _forward_preview(rec: dict) -> str:
    """列表预览:优先整条记录的文字(以前只取第一个节点,纯图片节点就成了「[图片]」)。

    整条里一个字的文本都没有时,退回「图片 N · 表情 M」这种计数 —— 比六个节点
    只显示一个「[图片]」有用。
    """
    texts = []
    counts = {"图片": 0, "表情": 0, "@": 0}
    for node in rec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for seg in node.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            kind = seg.get("type")
            data = seg.get("data") or {}
            if kind == "text":
                chunk = str(data.get("text") or "").strip()
                if chunk:
                    texts.append(chunk)
            elif kind == "image":
                counts["图片"] += 1
            elif kind == "face":
                counts["表情"] += 1
            elif kind == "at":
                counts["@"] += 1
    if texts:
        return " ".join(texts)
    parts = ["%s %d" % (k, v) for k, v in counts.items() if v]
    return " · ".join(parts)


def _entry_bytes(root: str, name: str, rec: dict) -> int:
    """条目占用的总字节:资产 + 渲染图 + 结构化 json。"""
    render = rec.get("render") if isinstance(rec.get("render"), str) else None
    files: list[str] = []
    for fname in (render, RENDER_FILENAME, RECORD_FILENAME, ENTRY_FILENAME):
        if fname and fname not in files:   # render 常等于 render.png,别算两遍
            files.append(fname)
    return _asset_bytes(rec) + _dir_file_bytes(root, name, files)


def _all_text(rec: dict) -> str:
    chunks = [str(rec.get("record_id") or ""), str(rec.get("group_id") or "")]
    for node in rec.get("nodes") or []:
        if isinstance(node, dict):
            chunks.append(str(node.get("name") or ""))
            chunks.append(_node_text(node))
    return " ".join(chunks)


def _entry_text(entry: dict) -> str:
    chunks = [str(entry.get("sender_name") or ""),
              str(entry.get("group_id") or ""),
              str(entry.get("record_id") or "")]
    for seg in entry.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        data = seg.get("data") or {}
        kind = seg.get("type")
        if kind == "text":
            chunks.append(str(data.get("text") or ""))
        elif kind == "face":
            chunks.append("[表情]")
        elif kind == "at":
            chunks.append("@" + str(data.get("qq") or "all"))
    return " ".join(c for c in chunks if c)


def entry_item(root: str, name: str, entry: dict) -> dict:
    """纯文本条目的列表项(与聊天记录同一套字段,免得页面写两套渲染)。"""
    meta = meta_filename(root, name)
    render = entry.get("render") if isinstance(entry.get("render"), str) else None
    return {
        "source": "entry",
        "kind": str(entry.get("kind") or "text"),
        "record_id": str(entry.get("record_id") or name),
        "captured_ts": int(entry.get("captured_ts") or 0),
        "group_id": str(entry.get("group_id") or ""),
        "root_msg_id": str(entry.get("root_msg_id") or ""),
        "sender_id": str(entry.get("sender_id") or ""),
        "sender_name": str(entry.get("sender_name") or ""),
        "node_count": 1,
        "asset_count": 0,
        "missing_count": 0,
        "bytes": _entry_bytes(root, name, entry),
        "preview": _entry_text(entry)[:80],
        "can_native": False,
        "has_render": bool(render) and meta is not None,
        "duplicate_of": str(entry.get("duplicate_of") or ""),
        "meta_file": meta or "",
    }


def flat_item(root: str, row: dict) -> Optional[dict]:
    """索引库里的一行 → 列表项;文件已经被手动删掉时返回 None(列表不该给出死链)。"""
    path = str(row.get("path") or "")
    if not path or not os.path.isfile(path):
        return None
    rel = os.path.relpath(path, root) if os.path.isabs(path) else path
    if rel.startswith(".."):
        return None
    kind = str(row.get("kind") or "unknown")
    return {
        "source": "flat",
        "asset_id": int(row.get("id") or 0),
        "kind": kind,
        "record_id": "",
        "captured_ts": int(row.get("ts") or 0),
        "group_id": str(row.get("group_id") or ""),
        "root_msg_id": str(row.get("msg_id") or ""),
        "sender_id": str(row.get("sender_id") or ""),
        "sender_name": "",
        "node_count": 0,
        "asset_count": 1,
        "missing_count": 0,
        "bytes": int(row.get("size") or 0),
        "preview": os.path.basename(path),
        "can_native": False,
        "has_render": False,
        "path": path,
        "rel": rel,
        "sha16": str(row.get("sha16") or ""),
        # 同一份字节被几条消息引用(内容去重只存一份):页面用它说明「不是重复落盘」
        "shared": int(row.get("shared") or 1),
    }


def scan_items(root: str, *, q: str = "", group: str = "", kind: str = "",
               source: str = "", limit: int = 100, offset: int = 0,
               flat_rows: Optional[list] = None,
               flat_total: Optional[int] = None) -> dict:
    """分类管理用的统一列表:条目 + 扁平文件,可再按类型/群/来源/关键词收窄.

    flat_rows 由调用方从索引库取(index.list_assets),这里保持纯函数、离线可测。
    flat_total 是**同一套过滤条件下**的扁平文件总数:取数有上限（一页 2000 行），
    而 total 必须是真数 —— 以前直接拿 len(取到的行) 当总数，页面上「图片」永远
    显示 500、「聊天记录」也只出最新窗口里的几条。
    """
    needle = (q or "").strip().lower()
    want_group = str(group or "").strip()
    want_kind = str(kind or "").strip()
    want_source = str(source or "").strip()
    items: list = []

    base = records_root(root)
    if want_source in ("", "entry") and os.path.isdir(base):
        for name in os.listdir(base):
            rec = load_record(root, name)
            if rec:
                if want_kind and want_kind != "forward":
                    continue
                if want_group and str(rec.get("group_id") or "") != want_group:
                    continue
                row = _forward_item(root, rec, name)
            else:
                entry = load_entry(root, name)
                if not entry:
                    continue
                row = entry_item(root, name, entry)
                if want_kind and want_kind != row["kind"]:
                    continue
                if want_group and row["group_id"] != want_group:
                    continue
            if needle and needle not in (row["preview"].lower()
                                          + " " + row["group_id"].lower()
                                          + " " + row["record_id"].lower()):
                continue
            items.append(row)

    if want_source in ("", "flat") and flat_rows:
        for raw in flat_rows:
            row = flat_item(root, raw) if isinstance(raw, dict) else None
            if row is None:
                continue
            if want_kind and row["kind"] != want_kind:
                continue
            if want_group and row["group_id"] != want_group:
                continue
            if needle and needle not in (row["preview"].lower()
                                          + " " + row["group_id"].lower()):
                continue
            items.append(row)

    items.sort(key=lambda r: r["captured_ts"], reverse=True)
    # flat_total 由调用方按同样的过滤条件（kind/group/q）从索引库 COUNT 出来；
    # 没给就退回「实际取到的行数」（离线测试与老调用方走这条）。
    flat_seen = sum(1 for r in items if r.get("source") == "flat")
    total = (len(items) - flat_seen)
    total += int(flat_total) if flat_total is not None else flat_seen
    page = items[max(0, int(offset)):max(0, int(offset)) + max(1, int(limit))]
    return {"total": total, "items": page, "root": records_root(root),
            "kinds": list(KINDS), "labels": dict(KIND_LABELS)}


def _forward_item(root: str, rec: dict, name: str) -> dict:
    nodes = [n for n in rec.get("nodes") or [] if isinstance(n, dict)]
    preview = _forward_preview(rec) or ("%d 节点" % len(nodes) if nodes else "")
    return {
        "source": "entry",
        "kind": "forward",
        "record_id": str(rec.get("record_id") or name),
        "captured_ts": int(rec.get("captured_ts") or 0),
        "group_id": str(rec.get("group_id") or ""),
        "root_msg_id": str(rec.get("root_msg_id") or ""),
        "sender_id": "",
        "sender_name": str(nodes[0].get("name") or "") if nodes else "",
        "node_count": int(rec.get("node_count") or len(nodes)),
        "asset_count": len(rec.get("assets") or []),
        "missing_count": len(rec.get("missing") or []),
        "bytes": _entry_bytes(root, name, rec),
        "preview": preview[:80],
        "can_native": _can_native(rec),
        "native_unavailable": bool(rec.get("native_unavailable")),
        "has_render": bool(rec.get("render")),
        "duplicate_of": str(rec.get("duplicate_of") or ""),
        "meta_file": "record.json",
    }


def scan_records(root: str, *, q: str = "", group: str = "",
                 limit: int = 100, offset: int = 0) -> dict:
    """扫描存档目录，返回 {total, items}；q 按「发送者/文本/群/记录 id」模糊匹配。"""
    base = records_root(root)
    items: list = []
    if not os.path.isdir(base):
        return {"total": 0, "items": [], "root": base}
    needle = (q or "").strip().lower()
    want_group = str(group or "").strip()
    for name in os.listdir(base):
        rec = load_record(root, name)
        if not rec:
            continue
        if want_group and str(rec.get("group_id") or "") != want_group:
            continue
        if needle and needle not in _all_text(rec).lower():
            continue
        nodes = [n for n in rec.get("nodes") or [] if isinstance(n, dict)]
        preview = _forward_preview(rec) or ("%d 节点" % len(nodes) if nodes else "")
        items.append({
            "record_id": rec.get("record_id", name),
            "captured_ts": int(rec.get("captured_ts") or 0),
            "group_id": str(rec.get("group_id") or ""),
            "root_msg_id": str(rec.get("root_msg_id") or ""),
            "node_count": int(rec.get("node_count") or len(nodes)),
            "asset_count": len(rec.get("assets") or []),
            "missing_count": len(rec.get("missing") or []),
            "bytes": _entry_bytes(root, name, rec),
            "preview": preview[:80],
            "can_native": _can_native(rec),
            "native_unavailable": bool(rec.get("native_unavailable")),
        })
    items.sort(key=lambda r: r["captured_ts"], reverse=True)
    total = len(items)
    page = items[max(0, int(offset)):max(0, int(offset)) + max(1, int(limit))]
    return {"total": total, "items": page, "root": base}


def _iter_records(root: str):
    """逐个读出 _records/ 下的条目（record.json 或 entry.json）。"""
    base = records_root(root)
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        rec = load_record(root, name) or load_entry(root, name)
        if rec:
            yield name, rec


def count_records_older_than(root: str, cutoff_ts: int) -> int:
    """captured_ts 早于 cutoffs 的条目数（prune 回执先说清会删多少）。"""
    return sum(1 for _, rec in _iter_records(root)
               if int(rec.get("captured_ts") or 0) < int(cutoff_ts))


def delete_records_older_than(root: str, cutoff_ts: int) -> int:
    """删掉 captured_ts 早于 cutoff 的条目目录，返回删除条数。

    只由 /warden prune --records 显式触发 —— 存档是原件，默认不碰。
    """
    n = 0
    for name, rec in _iter_records(root):
        if int(rec.get("captured_ts") or 0) >= int(cutoff_ts):
            continue
        if delete_record(root, name).get("deleted"):
            n += 1
    return n


def stats(root: str) -> dict:
    """页面顶部的汇总数字。"""
    base = records_root(root)
    out = {"records": 0, "texts": 0, "nodes": 0, "assets": 0, "missing": 0,
           "bytes": 0, "groups": {}, "kinds": {}, "root": base}
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        rec = load_record(root, name)
        if not rec:
            entry = load_entry(root, name)
            if entry is not None:
                out["texts"] += 1
                out["kinds"]["text"] = out["kinds"].get("text", 0) + 1
                gid = str(entry.get("group_id") or "")
                if gid:
                    out["groups"][gid] = out["groups"].get(gid, 0) + 1
                out["bytes"] += _entry_bytes(root, name, entry)
            continue
        out["records"] += 1
        out["kinds"]["forward"] = out["kinds"].get("forward", 0) + 1
        out["nodes"] += int(rec.get("node_count") or 0)
        out["assets"] += len(rec.get("assets") or [])
        out["missing"] += len(rec.get("missing") or [])
        # 资产 + 渲染图 + 结构化 json 都算占用（见 _entry_bytes）
        out["bytes"] += _entry_bytes(root, name, rec)
        gid = str(rec.get("group_id") or "")
        if gid:
            out["groups"][gid] = out["groups"].get(gid, 0) + 1
    return out


def _entry_detail(root: str, record_id: str, entry: dict) -> dict:
    """纯文本条目详情:段可读化(与聊天记录同一套 type 名,页面不用分支)。"""
    segs_out = []
    for seg in entry.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        item = {"type": kind}
        if kind == "text":
            item["text"] = str(data.get("text") or "")
        elif kind == "at":
            item["qq"] = str(data.get("qq") or "")
        elif kind == "face":
            item["id"] = str(data.get("id") or "")
        elif kind == "reply":
            item["id"] = str(data.get("id") or "")
        else:
            item["raw"] = {k: v for k, v in list(data.items())[:6]
                           if isinstance(v, (str, int, float))}
        segs_out.append(item)
    render = entry.get("render") if isinstance(entry.get("render"), str) else None
    return {
        "kind": str(entry.get("kind") or "text"),
        "record_id": str(entry.get("record_id") or record_id),
        "platform": str(entry.get("platform") or ""),
        "group_id": str(entry.get("group_id") or ""),
        "root_msg_id": str(entry.get("root_msg_id") or ""),
        "captured_ts": int(entry.get("captured_ts") or 0),
        "can_native": False,
        "schema_version": int(entry.get("schema_version") or 0),
        "node_count": 1,
        "asset_count": 0,
        "missing": [],
        "bytes": 0,
        "origin_sha256": str(entry.get("origin_sha256") or ""),
        "sender_id": str(entry.get("sender_id") or ""),
        "sender_name": str(entry.get("sender_name") or ""),
        "render": render,
        "has_render": bool(render),
        "duplicate_of": str(entry.get("duplicate_of") or ""),
        "uin_reliable": True,
        "nodes": [{
            "index": 0,
            "uin": str(entry.get("sender_id") or ""),
            "name": str(entry.get("sender_name") or ""),
            "time": int(entry.get("captured_ts") or 0),
            "segments": segs_out,
        }],
    }


def record_detail(root: str, record_id: str) -> Optional[dict]:
    """单条存档的完整视图：节点分段可读化 + 图片资产编号（给页面取图用）。"""
    rec = load_record(root, record_id)
    if not rec:
        entry = load_entry(root, record_id)
        if entry is None:
            return None
        return _entry_detail(root, record_id, entry)
    assets = {(a.get("node"), a.get("seg")): a
              for a in rec.get("assets") or [] if isinstance(a, dict)}
    nodes_out = []
    for node in rec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        segs_out = []
        for seg in node.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            kind = seg.get("type")
            data = seg.get("data") or {}
            item = {"type": kind}
            asset = assets.get((node.get("index"), len(segs_out)))
            if kind == "text":
                item["text"] = str(data.get("text") or "")
            elif kind == "image":
                item["name"] = str(data.get("file") or "")
                if asset:
                    item["asset"] = asset.get("path")
                    item["sha16"] = asset.get("sha16")
                else:
                    item["missing"] = True
            elif kind == "at":
                item["qq"] = str(data.get("qq") or "")
            elif kind == "face":
                item["id"] = str(data.get("id") or "")
            else:
                item["raw"] = {k: v for k, v in list(data.items())[:6]
                               if isinstance(v, (str, int, float))}
            segs_out.append(item)
        nodes_out.append({
            "index": node.get("index"),
            "uin": str(node.get("uin") or ""),
            "name": str(node.get("name") or ""),
            "time": int(node.get("time") or 0),
            "segments": segs_out,
        })
    return {
        "kind": "forward",
        "record_id": rec.get("record_id", record_id),
        "platform": str(rec.get("platform") or ""),
        "group_id": str(rec.get("group_id") or ""),
        "root_msg_id": str(rec.get("root_msg_id") or ""),
        "captured_ts": int(rec.get("captured_ts") or 0),
        "can_native": _can_native(rec),
        "schema_version": int(rec.get("schema_version") or 0),
        "node_count": int(rec.get("node_count") or len(nodes_out)),
        "asset_count": len(rec.get("assets") or []),
        "missing": rec.get("missing") or [],
        "bytes": _entry_bytes(root, record_id, rec),
        "origin_sha256": str(rec.get("origin_sha256") or ""),
        # 详情页要靠这两个决定「结构化 / 渲染图」两个标签显不显示（以前列表项有、
        # 详情没有，于是渲染图在页面上根本点不到）。
        "render": str(rec.get("render") or ""),
        "has_render": bool(rec.get("render")),
        "duplicate_of": str(rec.get("duplicate_of") or ""),
        # 页面用它把「转发原消息」标成「原消息已失效」，而不是留一个点了必敗的主按钮
        "native_unavailable": bool(rec.get("native_unavailable")),
        "uin_reliable": uin_reliable(nodes_out),
        "nodes": nodes_out,
    }


def resolve_asset(root: str, record_id: str, rel_path: str) -> Optional[str]:
    """把页面传来的 assets/xxx.png 解析成绝对路径；越界/不存在返回 None。"""
    rec = load_record(root, record_id)
    if not rec:
        return None
    wanted = str(rel_path or "").replace("\\", "/").lstrip("/")
    allowed = {str(a.get("path")) for a in rec.get("assets") or []
               if isinstance(a, dict)}
    if wanted not in allowed:
        return None
    base = record_dir(root, record_id)
    full = os.path.abspath(os.path.join(base, wanted.replace("/", os.sep)))
    base_abs = os.path.abspath(base)
    if not full.startswith(base_abs + os.sep):
        return None
    return full if os.path.isfile(full) else None


def entry_file(root: str, record_id: str, rel_path: str) -> Optional[str]:
    """条目目录内的可取文件:登记过的资产,或渲染图 render.png。"""
    base = os.path.abspath(record_dir(root, record_id))
    name = str(rel_path or "").replace("\\", "/").lstrip("/")
    if not name:
        return None
    full = os.path.abspath(os.path.join(base, name.replace("/", os.sep)))
    if not full.startswith(base + os.sep):
        return None
    if name != RENDER_FILENAME:
        meta = load_record(root, record_id) or load_entry(root, record_id) or {}
        allowed = {str(a.get("path")) for a in meta.get("assets") or []
                   if isinstance(a, dict)}
        if name not in allowed:
            return None
    return full if os.path.isfile(full) else None


def resolve_flat(root: str, rel_path: str) -> Optional[str]:
    """老的扁平落盘文件:只认 storage_root 之下的路径(不许从页面读任意文件).

    v1.17.0: 先 normpath 再判断前缀。以前直接 `rel.startswith("_records/")`，
    传 "./_records/<rid>/render.png" 就绕过去了 —— 页面能读、也能删存档内部文件。
    """
    base = os.path.abspath(root)
    rel = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    parts = [p for p in os.path.normpath(rel).replace("\\", "/").split("/")
             if p not in ("", ".")]
    if not parts or parts[0] == "_records" or ".." in parts:
        return None
    full = os.path.abspath(os.path.join(base, *parts))
    if not full.startswith(base + os.sep):
        return None
    return full if os.path.isfile(full) else None


def delete_flat(root: str, rel_path: str, *, keep_file: bool = False) -> dict:
    """删除一个扁平文件(老的落盘产物);索引行由调用方清理。

    v1.17.0: keep_file=True 只解析不删 —— 同一个文件可能被多行索引共享
    （Storage 按内容去重），调用方要先按行删、确认没有别的行再删文件。
    """
    full = resolve_flat(root, rel_path)
    if not full:
        return {"deleted": False, "reason": "文件不存在或路径越界"}
    if keep_file:
        return {"deleted": True, "path": full, "kept": True}
    try:
        os.remove(full)
    except OSError as e:
        return {"deleted": False, "reason": f"{type(e).__name__}: {e}"}
    return {"deleted": True, "path": full}


def delete_record(root: str, record_id: str) -> dict:
    """删除一条存档目录（只允许 records_root 之下）。"""
    base = os.path.abspath(records_root(root))
    target = os.path.abspath(record_dir(root, record_id))
    if not target.startswith(base + os.sep):
        return {"deleted": False, "reason": "路径越界"}
    if not os.path.isdir(target):
        return {"deleted": False, "reason": "存档不存在"}
    shutil.rmtree(target)
    return {"deleted": True, "record_id": record_id}


def node_payloads(rec: dict, base_dir: str) -> list:
    """把存档还原成 OneBot 合并转发用的 node 列表（图片转 base64://）。"""
    assets = {(a.get("node"), a.get("seg")): a
              for a in rec.get("assets") or [] if isinstance(a, dict)}
    out = []
    for node in rec.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        content = []
        for si, seg in enumerate(node.get("segments") or []):
            if not isinstance(seg, dict):
                continue
            kind = seg.get("type")
            data = seg.get("data") or {}
            if kind == "text":
                content.append({"type": "text",
                                "data": {"text": str(data.get("text") or "")}})
            elif kind == "face":
                content.append({"type": "face",
                                "data": {"id": str(data.get("id") or "")}})
            elif kind == "at":
                content.append({"type": "at",
                                "data": {"qq": str(data.get("qq") or "all")}})
            elif kind == "image":
                asset = assets.get((node.get("index"), si))
                if not asset:
                    continue
                full = os.path.join(base_dir,
                                    str(asset.get("path")).replace("/", os.sep))
                try:
                    with open(full, "rb") as fh:
                        blob = fh.read()
                except OSError:
                    continue
                content.append({"type": "image", "data": {
                    "file": "base64://" + base64.b64encode(blob).decode()}})
            else:
                content.append({"type": "text",
                                "data": {"text": f"[{kind}]"}})
        out.append({"type": "node", "data": {
            "user_id": str(node.get("uin") or "0"),
            "nickname": str(node.get("name") or ""),
            # v1.17.0: 带上原时间戳。AstrBot 的 Node.to_dict 不发 time，经它发送
            # 的合并转发会丢时间；这里直发 OneBot 结构，可以原样带回去。
            "time": int(node.get("time") or 0),
            "content": content,
        }})
    return out


def _result_message_id(res: Any) -> str:
    """从 OneBot 动作返回值里取「新消息 id」—— 各家实现放的位置不一样，
    取不到不算失败（只影响回执里能不能报出 id）。"""
    if isinstance(res, dict):
        data = res.get("data")
        if isinstance(data, dict):
            for key in ("message_id", "id"):
                if data.get(key) not in (None, ""):
                    return str(data[key])
        for key in ("message_id", "id"):
            if res.get(key) not in (None, ""):
                return str(res[key])
    return ""


async def native_forward(bot: Any, group_id: str, message_id: str) -> dict:
    """让 QQ 把原消息再转发一遍（保气泡/挂件/头像）。bot 是 aiocqhttp 的 CQHttp。"""
    if bot is None:
        return {"ok": False, "error": "机器人未连接（没有 aiocqhttp 实例）"}
    target = str(message_id or "").strip()
    if not target.isdigit():
        return {"ok": False, "error": "这条存档没有原消息 id（当时没取到短 id）"}
    call = getattr(bot, "call_action", None)
    if not callable(call):
        return {"ok": False, "error": "call_action 不可用"}
    try:
        probe = await call("get_msg", message_id=int(target))
    except Exception as e:
        return {"ok": False, "error": f"取不到原消息 {target}: {e!r}"}
    inner = probe
    if (isinstance(inner, dict) and isinstance(inner.get("data"), dict)
            and "message" not in inner):
        inner = inner["data"]
    segs = inner.get("message") if isinstance(inner, dict) else None
    is_forward = any(
        isinstance(seg, dict)
        and (seg.get("type") or "").lower() in ("forward", "node", "nodes")
        for seg in (segs if isinstance(segs, list) else []))
    if not is_forward:
        return {"ok": False,
                "error": f"原消息 {target} 已不是合并转发（撤回/过期/旧存档 id）"}
    try:
        res = await call("forward_group_single_msg",
                         group_id=str(group_id), message_id=int(target))
    except Exception as e:
        return {"ok": False, "error": f"原地转发失败: {e!r}"}
    if isinstance(res, dict) and str(res.get("status") or "").lower() == "failed":
        return {"ok": False, "error": str(res.get("message") or res)}
    return {"ok": True, "mode": "native", "message_id": target,
            "origin_message_id": target, "new_message_id": _result_message_id(res),
            "group_id": str(group_id)}


async def rebuild_forward(bot: Any, group_id: str, rec: dict,
                          base_dir: str) -> dict:
    """按存档重建合并转发（图片走 base64）。装饰会丢，但原消息没了也能发。"""
    if bot is None:
        return {"ok": False, "error": "机器人未连接（没有 aiocqhttp 实例）"}
    nodes = node_payloads(rec, base_dir)
    if not nodes:
        return {"ok": False, "error": "存档里没有可发送的节点"}
    call = getattr(bot, "call_action", None)
    try:
        res = await call("send_group_forward_msg", group_id=str(group_id),
                         messages=nodes)
    except Exception as e:
        return {"ok": False, "error": f"重建转发失败: {e!r}"}
    if isinstance(res, dict) and str(res.get("status") or "").lower() == "failed":
        return {"ok": False, "error": str(res.get("message") or res)}
    return {"ok": True, "mode": "rebuild", "node_count": len(nodes),
            "new_message_id": _result_message_id(res),
            "group_id": str(group_id)}


# ---------------- 扁平 forward JSON → 条目（v1.14.0） ----------------

async def _no_images(_data: dict) -> Optional[bytes]:
    """迁移时不下去下载：老 JSON 里的 url 早过期，直接记成 missing 更诚实。"""
    return None

