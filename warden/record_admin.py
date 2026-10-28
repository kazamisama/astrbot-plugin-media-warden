"""存档管理逻辑（纯函数，离线可测）。

dashboard 页面与页面 API 都只做 IO 转接，真正的扫描/搜索/删除/重发目标解析
都在这里，好处是这些行为能用 _smoke.py 直接测，不必起 AstrBot。
"""
from __future__ import annotations

import base64
import os
import shutil
from typing import Any, Optional

from .record import load_record, record_dir, records_root


def _asset_bytes(rec: dict) -> int:
    return sum(int(a.get("bytes") or 0) for a in rec.get("assets") or []
               if isinstance(a, dict))


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


def _all_text(rec: dict) -> str:
    chunks = [str(rec.get("record_id") or ""), str(rec.get("group_id") or "")]
    for node in rec.get("nodes") or []:
        if isinstance(node, dict):
            chunks.append(str(node.get("name") or ""))
            chunks.append(_node_text(node))
    return " ".join(chunks)


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
        preview = _node_text(nodes[0]) if nodes else ""
        items.append({
            "record_id": rec.get("record_id", name),
            "captured_ts": int(rec.get("captured_ts") or 0),
            "group_id": str(rec.get("group_id") or ""),
            "root_msg_id": str(rec.get("root_msg_id") or ""),
            "node_count": int(rec.get("node_count") or len(nodes)),
            "asset_count": len(rec.get("assets") or []),
            "missing_count": len(rec.get("missing") or []),
            "bytes": _asset_bytes(rec),
            "preview": preview[:80],
            "can_native": bool(str(rec.get("root_msg_id") or "").isdigit()),
        })
    items.sort(key=lambda r: r["captured_ts"], reverse=True)
    total = len(items)
    page = items[max(0, int(offset)):max(0, int(offset)) + max(1, int(limit))]
    return {"total": total, "items": page, "root": base}


def stats(root: str) -> dict:
    """页面顶部的汇总数字。"""
    base = records_root(root)
    out = {"records": 0, "nodes": 0, "assets": 0, "missing": 0, "bytes": 0,
           "groups": {}, "root": base}
    if not os.path.isdir(base):
        return out
    for name in os.listdir(base):
        rec = load_record(root, name)
        if not rec:
            continue
        out["records"] += 1
        out["nodes"] += int(rec.get("node_count") or 0)
        out["assets"] += len(rec.get("assets") or [])
        out["missing"] += len(rec.get("missing") or [])
        out["bytes"] += _asset_bytes(rec)
        gid = str(rec.get("group_id") or "")
        if gid:
            out["groups"][gid] = out["groups"].get(gid, 0) + 1
    return out


def record_detail(root: str, record_id: str) -> Optional[dict]:
    """单条存档的完整视图：节点分段可读化 + 图片资产编号（给页面取图用）。"""
    rec = load_record(root, record_id)
    if not rec:
        return None
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
        "record_id": rec.get("record_id", record_id),
        "platform": str(rec.get("platform") or ""),
        "group_id": str(rec.get("group_id") or ""),
        "root_msg_id": str(rec.get("root_msg_id") or ""),
        "captured_ts": int(rec.get("captured_ts") or 0),
        "can_native": bool(str(rec.get("root_msg_id") or "").isdigit()),
        "schema_version": int(rec.get("schema_version") or 0),
        "node_count": int(rec.get("node_count") or len(nodes_out)),
        "asset_count": len(rec.get("assets") or []),
        "missing": rec.get("missing") or [],
        "bytes": _asset_bytes(rec),
        "origin_sha256": str(rec.get("origin_sha256") or ""),
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
            "content": content,
        }})
    return out


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
        await call("send_group_forward_msg", group_id=str(group_id),
                   messages=nodes)
    except Exception as e:
        return {"ok": False, "error": f"重建转发失败: {e!r}"}
    return {"ok": True, "mode": "rebuild", "node_count": len(nodes),
            "group_id": str(group_id)}
