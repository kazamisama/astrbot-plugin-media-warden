"""插件页面 API：存档的浏览 / 删除 / 重发。

注册到 AstrBot 的插件 Web API（路径前缀 `/<plugin>/page`），dashboard 里的插件
页面通过 `AstrBotPluginPage.apiGet/apiPost("page/...")` 调用。真正的逻辑都在
`warden/record_admin.py`（纯函数，离线可测），这里只做 HTTP 转接。
"""
from __future__ import annotations

import base64
import os

from . import record_admin
from .record import load_record, record_dir

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
# 单张图回 base64 的上限：超过就让页面走下载，别把宿主请求体撑爆
_THUMB_MAX_BYTES = 8 * 1024 * 1024


def _mime_for(path: str) -> str:
    return _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(),
                            "application/octet-stream")

PLUGIN_NAME = "astrbot-plugin-media-warden"
PAGE_PREFIX = f"/{PLUGIN_NAME}/page"


class RecordPageApi:
    """把存档目录暴露成插件页面的后端。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    # ---------------- 注册 ----------------

    def register_routes(self) -> bool:
        """注册全部路由；宿主不支持 register_web_api 时返回 False（老版本 AstrBot）。"""
        register = getattr(self.plugin.context, "register_web_api", None)
        if not callable(register):
            return False
        register(f"{PAGE_PREFIX}/stats", self.get_stats, ["GET"],
                 "media-warden 存档汇总")
        register(f"{PAGE_PREFIX}/records", self.list_records, ["GET"],
                 "media-warden 存档列表")
        register(f"{PAGE_PREFIX}/record", self.get_record, ["GET"],
                 "media-warden 存档详情")
        register(f"{PAGE_PREFIX}/asset", self.get_asset, ["GET"],
                 "media-warden 存档图片")
        register(f"{PAGE_PREFIX}/thumb", self.get_thumb, ["GET"],
                 "media-warden 存档图片（base64，给页面内联显示）")
        register(f"{PAGE_PREFIX}/groups", self.list_groups, ["GET"],
                 "media-warden 机器人所在的群（带群名，给转发目标下拉）")
        register(f"{PAGE_PREFIX}/resend", self.resend, ["POST"],
                 "media-warden 重发存档")
        register(f"{PAGE_PREFIX}/delete", self.delete, ["POST"],
                 "media-warden 删除存档")
        return True

    # ---------------- 内部 ----------------

    @property
    def _root(self) -> str:
        return self.plugin._records_root()

    def _bot(self):
        """拿 aiocqhttp 的 CQHttp 客户端（页面重发要用真正的机器人账号）。"""
        try:
            platform = self.plugin.context.get_platform("aiocqhttp")
        except Exception:
            platform = None
        return getattr(platform, "bot", None)

    # ---------------- 处理函数 ----------------

    async def get_stats(self):
        return {"status": "ok", "data": record_admin.stats(self._root)}

    async def list_records(self):
        from astrbot.api.web import request
        data = record_admin.scan_records(
            self._root,
            q=request.query.get("q", "") or "",
            group=request.query.get("group", "") or "",
            limit=request.query.get("limit", "50", int) or 50,
            offset=request.query.get("offset", "0", int) or 0,
        )
        return {"status": "ok", "data": data}

    async def get_record(self):
        from astrbot.api.web import request
        rid = str(request.query.get("id", "") or "")
        detail = record_admin.record_detail(self._root, rid)
        if detail is None:
            return {"status": "error", "message": "存档不存在", "data": {}}
        return {"status": "ok", "data": detail}

    async def get_asset(self):
        from astrbot.api.web import file_response, request
        rid = str(request.query.get("id", "") or "")
        rel = str(request.query.get("path", "") or "")
        full = record_admin.resolve_asset(self._root, rid, rel)
        if not full:
            return {"status": "error", "message": "资产不存在", "data": {}}
        return file_response(full)

    async def list_groups(self):
        """机器人所在的群列表（OneBot get_group_list，带群名）。

        页面上的「转发到」下拉用它 —— 裸群号没人认得出来，带群名才选得动。
        aiocqhttp 的 call_action 成功时已解包成 data（列表），个别实现回
        {"data": [...]}，这里两种都认。
        """
        bot = self._bot()
        if bot is None:
            return {"status": "error", "message": "机器人未连接（没有 aiocqhttp 实例）",
                    "data": {}}
        try:
            res = await bot.call_action("get_group_list")
        except Exception as e:
            return {"status": "error", "message": f"取群列表失败: {e!r}", "data": {}}
        if isinstance(res, dict):
            res = res.get("data") or res.get("groups") or []
        items = []
        for g in res or []:
            if not isinstance(g, dict):
                continue
            gid = g.get("group_id") or g.get("groupId") or g.get("id")
            if gid in (None, ""):
                continue
            name = g.get("group_name") or g.get("groupName") or g.get("name") or ""
            count = g.get("member_count") or g.get("memberCount") or 0
            try:
                count = int(count or 0)
            except (TypeError, ValueError):
                count = 0
            items.append({"group_id": str(gid), "name": str(name),
                          "member_count": count})
        items.sort(key=lambda it: (it["name"], it["group_id"]))
        return {"status": "ok", "data": {"groups": items, "total": len(items)}}

    async def get_thumb(self):
        """把一张资产读成 data URL。

        页面在 dashboard 的 iframe 里，<img> 带不上 Authorization 头（宿主的
        鉴权走 axios），而 Tauri/浏览器不一定留着 dashboard 的 cookie —— 所以
        缩略图改走这里：桥接口是带鉴权的，图片以 base64 回给页面。
        """
        from astrbot.api.web import request
        rid = str(request.query.get("id", "") or "")
        rel = str(request.query.get("path", "") or "")
        full = record_admin.resolve_asset(self._root, rid, rel)
        if not full:
            return {"status": "error", "message": "资产不存在", "data": {}}
        try:
            size = os.path.getsize(full)
            if size > _THUMB_MAX_BYTES:
                return {"status": "error",
                        "message": f"图片太大（{size} 字节），请用下载", "data": {}}
            with open(full, "rb") as fh:
                blob = fh.read()
        except OSError as e:
            return {"status": "error", "message": f"读取失败: {e!r}", "data": {}}
        return {"status": "ok", "data": {
            "data_url": "data:" + _mime_for(full) + ";base64," +
                        base64.b64encode(blob).decode("ascii"),
            "bytes": len(blob),
        }}

    async def resend(self):
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        if not isinstance(body, dict):
            body = {}
        rid = str(body.get("id") or "")
        rec = load_record(self._root, rid)
        if not rec:
            return {"status": "error", "message": "存档不存在", "data": {}}
        group_id = str(body.get("group_id") or rec.get("group_id") or "")
        if not group_id:
            return {"status": "error", "message": "不知道发到哪个群", "data": {}}
        mode = str(body.get("mode") or "native").lower()
        bot = self._bot()
        base = record_dir(self._root, rid)
        if mode == "rebuild":
            res = await record_admin.rebuild_forward(bot, group_id, rec, base)
        else:
            res = await record_admin.native_forward(
                bot, group_id, rec.get("root_msg_id") or "")
            if not res.get("ok") and bool(body.get("fallback", True)):
                rb = await record_admin.rebuild_forward(bot, group_id, rec, base)
                if rb.get("ok"):
                    rb["note"] = f"原地转发不可用（{res.get('error')}），已回退重建"
                    res = rb
        return {"status": "ok", "data": res}

    async def delete(self):
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        rid = str((body or {}).get("id") or "")
        if not rid:
            return {"status": "error", "message": "缺少 id", "data": {}}
        res = record_admin.delete_record(self._root, rid)
        return {"status": "ok" if res.get("deleted") else "error",
                "message": res.get("reason", ""), "data": res}
