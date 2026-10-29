"""插件页面 API：存档的浏览 / 删除 / 重发。

注册到 AstrBot 的插件 Web API（路径前缀 `/<plugin>/page`），dashboard 里的插件
页面通过 `AstrBotPluginPage.apiGet/apiPost("page/...")` 调用。真正的逻辑都在
`warden/record_admin.py`（纯函数，离线可测），这里只做 HTTP 转接。
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import time
from typing import Optional

log = logging.getLogger(__name__)

from . import record_admin
from .record import load_entry, load_record, record_dir, set_meta

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
# 单张图回 base64 的上限：超过就让页面走下载，别把宿主请求体撑爆
_THUMB_MAX_BYTES = 8 * 1024 * 1024


def _mime_for(path: str) -> str:
    return _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(),
                            "application/octet-stream")

# native 转发失败的哪些原因算「原消息永久没了」:QQ 那边不会复活,值得记住;
# 网络抖动/权限这类偶发错误不记,否则一次超时会把按钮永久禁用。
_GONE_HINTS = ("取不到原消息", "消息不存在", "无法找到")


def _native_gone(error) -> bool:
    text = str(error or "")
    return any(h in text for h in _GONE_HINTS)


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
        register(f"{PAGE_PREFIX}/render", self.rerender, ["POST"],
                 "media-warden 按当前样式重画渲染图（补老存档）")
        register(f"{PAGE_PREFIX}/delete", self.delete, ["POST"],
                 "media-warden 删除存档")
        register(f"{PAGE_PREFIX}/reveal", self.reveal, ["POST"],
                 "media-warden 在资源管理器里打开目录")
        register(f"{PAGE_PREFIX}/style", self.get_style, ["GET"],
                 "media-warden 渲染样式(读取)")
        register(f"{PAGE_PREFIX}/style_save", self.save_style, ["POST"],
                 "media-warden 渲染样式(保存)")
        register(f"{PAGE_PREFIX}/style_preview", self.style_preview, ["POST"],
                 "media-warden 渲染样式(实时预览)")
        register(f"{PAGE_PREFIX}/purge_legacy", self.purge_legacy, ["POST"],
                 "media-warden 清理老的扁平转发渲染图")
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

    # 页面类型 → 索引库里真实的 kind 值。老版本把转发渲染图单独记成
    # forward_render、把转发 JSON 记成 forward_json，页面把它们并到
    # 「图片 / 聊天记录」两类里，否则这两批文件在页面上永远点不到。
    _FLAT_KIND_MAP = {
        # v1.17.0: forward_render 不再并进「图片」—— 857 张没有条目的旧渲染图
        # 以前被算作图片，既点不开也筛不掉；现在单列「旧渲染图」，可一键清理。
        "image": ["image"],
        "render": ["forward_render"],
        "gif": ["gif"],
        "video": ["video"],
        "file": ["file"],
        "voice": ["voice"],
        "forward": ["forward", "forward_json"],
    }

    def _flat_kinds(self, kind: str):
        """None = 不过滤；[] = 这一类没有扁平文件（纯文本）。"""
        k = str(kind or "").strip()
        if not k:
            return None
        return list(self._FLAT_KIND_MAP.get(k, []))

    def _flat_summary(self, *, kinds=None, group=None, q="") -> dict:
        """索引库的行数/总字节（同过滤条件）—— 页面顶部数字用这个，别数一页。"""
        index = getattr(self.plugin, "_index", None)
        if index is None:
            return {"total": 0, "bytes": 0}
        try:
            return index.summary(kinds=kinds, group_id=group or None, q=q)
        except Exception:
            return {"total": 0, "bytes": 0}

    def _flat_rows(self, *, kinds=None, group=None, q="", limit=None) -> list:
        """索引库里的扁平落盘资产（图片/动图/视频/文档）—— 没有索引时返回空。"""
        index = getattr(self.plugin, "_index", None)
        if index is None:
            return []
        try:
            return index.list_assets(limit=limit, kinds=kinds,
                                     group_id=group or None, q=q)["items"]
        except Exception:
            return []

    async def get_stats(self):
        data = record_admin.stats(self._root)
        flat = self._flat_summary()
        data["flat_total"] = int(flat["total"])
        data["flat_bytes"] = int(flat["bytes"])
        # 六类各自的保存开关:页面空态要靠它说「这一类是关的」,而不是笼统说
        # 「发图会自动保存」(v1.14.0 起每一类都是可开关的)。
        data["save_types"] = self._save_type_flags()
        # v1.17.0: 老的扁平转发渲染图单独计数（以前混在「图片」里，1.25 GB 看不见）
        legacy = self._flat_summary(kinds=["forward_render"])
        data["legacy_renders"] = int(legacy["total"])
        data["legacy_bytes"] = int(legacy["bytes"])
        return {"status": "ok", "data": data}

    def _save_type_flags(self) -> dict:
        cfg = getattr(self.plugin, "cfg", None)
        fn = getattr(cfg, "save_type_flags", None)
        if not callable(fn):
            return {}
        try:
            flags = fn()
        except Exception:
            return {}
        return {str(k): bool(v) for k, v in (flags or {}).items()}

    async def list_records(self):
        from astrbot.api.web import request
        kind = request.query.get("kind", "") or ""
        group = request.query.get("group", "") or ""
        q = request.query.get("q", "") or ""
        # 过滤必须在 SQL 里做：以前先取一页（500 行）再在 Python 里筛类型，
        # 于是「聊天记录」只出最新窗口里那几条、「图片」永远显示 500。
        flat_kinds = self._flat_kinds(kind)
        have_flat = flat_kinds is None or bool(flat_kinds)
        flat_rows = (self._flat_rows(kinds=flat_kinds, group=group, q=q)
                     if have_flat else [])
        flat_total = (self._flat_summary(kinds=flat_kinds, group=group, q=q)["total"]
                      if have_flat else 0)
        data = record_admin.scan_items(
            self._root,
            q=q, group=group, kind=kind,
            source=request.query.get("source", "") or "",
            limit=request.query.get("limit", "50", int) or 50,
            offset=request.query.get("offset", "0", int) or 0,
            flat_rows=flat_rows,
            flat_total=flat_total,
        )
        return {"status": "ok", "data": data}

    async def get_record(self):
        from astrbot.api.web import request
        rid = str(request.query.get("id", "") or "")
        detail = record_admin.record_detail(self._root, rid)
        if detail is None:
            return {"status": "error", "message": "存档不存在", "data": {}}
        return {"status": "ok", "data": detail}

    def _resolve_file(self, rid: str, rel: str, source: str = "") -> Optional[str]:
        """页面给 (id,path) 或 (source=flat,path) —— 两套保存物都要能取到。"""
        if source == "flat" or (not rid and rel):
            return record_admin.resolve_flat(self._root, rel)
        if not rid:
            return None
        return (record_admin.entry_file(self._root, rid, rel)
                or record_admin.resolve_asset(self._root, rid, rel))

    async def get_asset(self):
        from astrbot.api.web import file_response, request
        rid = str(request.query.get("id", "") or "")
        rel = str(request.query.get("path", "") or "")
        full = self._resolve_file(rid, rel, request.query.get("source", "") or "")
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
        full = self._resolve_file(rid, rel, request.query.get("source", "") or "")
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

    async def rerender(self):
        """按当前样式重画一条存档的渲染图（老存档没有、或改了样式要刷新）。

        节点与图片字节都在条目目录里，重画不碰网络（头像除外，失败会静默降级）。
        """
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        if not isinstance(body, dict):
            body = {}
        rid = str(body.get("id") or "")
        if not rid:
            return {"status": "error", "message": "缺少 id", "data": {}}
        fn = getattr(self.plugin, "rerender_record", None)
        if not callable(fn):
            return {"status": "error", "message": "插件未提供重渲染", "data": {}}
        try:
            out = await fn(rid)
        except Exception as e:
            return {"status": "error", "message": f"重渲染失败: {e!r}", "data": {}}
        if not out.get("ok"):
            return {"status": "error", "message": str(out.get("error") or "重渲染失败"),
                    "data": {}}
        return {"status": "ok", "data": out}

    async def resend(self):
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        if not isinstance(body, dict):
            body = {}
        rid = str(body.get("id") or "")
        rec = load_record(self._root, rid)
        if not rec:
            if load_entry(self._root, rid) is not None:
                return {"status": "error",
                        "message": "纯文本条目没有可转发的合并转发（只有聊天记录条目能转发）",
                        "data": {}}
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
            # v1.17.0: 只有「发回原群」失败才可能是原消息真的失效。跨群 native
            # 也会回「消息不存在」（QQ 按原群找消息），以前同样被记成永久失效 ——
            # 原消息明明还在，页面却再也不给「转发原消息」了。
            same_group = str(group_id) == str(rec.get("group_id") or "")
            if (not res.get("ok") and same_group
                    and _native_gone(res.get("error"))):
                try:
                    set_meta(self._root, rid, native_unavailable=int(time.time()))
                except Exception as e:  # 标记失败不影响这次回退
                    log.warning("mark native_unavailable failed: %r", e)
            if not res.get("ok") and bool(body.get("fallback", True)):
                rb = await record_admin.rebuild_forward(bot, group_id, rec, base)
                if rb.get("ok"):
                    rb["note"] = f"转发原消息不可用（{res.get('error')}），已改用重建转发"
                    res = rb
        return {"status": "ok", "data": res}

    async def purge_legacy(self):
        """删掉 v1.16 之前每次转发额外落下的扁平渲染图（它们没有对应条目）。

        只删 kind=forward_render 的行与文件；带条目 msg_id 的行跳过。
        """
        index = getattr(self.plugin, "_index", None)
        if index is None:
            return {"status": "error", "message": "索引未启用", "data": {}}
        try:
            res = record_admin.purge_orphan_renders(self._root, index)
        except Exception as e:
            return {"status": "error", "message": f"清理失败: {e!r}", "data": {}}
        return {"status": "ok", "data": res}

    # ---------------- 样式页 ----------------

    async def get_style(self):
        snap = getattr(self.plugin, "style_snapshot", None)
        if not callable(snap):
            return {"status": "error", "message": "插件版本不支持样式页", "data": {}}
        return {"status": "ok", "data": {"style": snap()}}

    async def save_style(self):
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        values = (body or {}).get("values") if isinstance(body, dict) else None
        updater = getattr(self.plugin, "update_style", None)
        if not callable(updater):
            return {"status": "error", "message": "插件版本不支持样式保存", "data": {}}
        snap, errors, persisted = updater(values if isinstance(values, dict) else {})
        if errors:
            return {"status": "error", "message": "；".join(errors), "data": {}}
        return {"status": "ok", "data": {"style": snap,
                                         "persisted": len(persisted)}}

    async def style_preview(self):
        """用当前(或请求里带的)样式渲染一张样例图,base64 回给页面。"""
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        values = (body or {}).get("values") if isinstance(body, dict) else None
        try:
            blob = await self._render_preview(values if isinstance(values, dict) else {})
        except Exception as e:
            return {"status": "error", "message": f"预览渲染失败: {e!r}", "data": {}}
        return {"status": "ok", "data": {
            "data_url": "data:image/png;base64," + base64.b64encode(blob).decode("ascii"),
            "bytes": len(blob),
        }}

    async def _render_preview(self, values: dict) -> bytes:
        """样例对话渲染:颜色/圆角/字号/时间格式/头像形状都在这张图里体现。"""
        import tempfile
        from PIL import Image, ImageDraw
        from .forwarder import Forwarder, _coerce_nodes

        snap = {}
        snap_fn = getattr(self.plugin, "style_snapshot", None)
        if callable(snap_fn):
            snap = snap_fn()
        style = dict(snap)
        for k, v in (values or {}).items():
            if k in style:
                style[k] = v

        width = int(style.get("style_width") or 720)
        fw = Forwarder(width=max(200, min(3000, width)), max_nodes=5)
        fw.apply_style(
            width=width,
            bg=style.get("style_bg_color"),
            bubble=style.get("style_bubble_color"),
            text=style.get("style_text_color"),
            dim=style.get("style_dim_color"),
            radius=style.get("style_radius"),
            font_size=style.get("style_font_size"),
            avatar_shape=style.get("style_avatar_shape"),
            time_format=style.get("style_time_format"),
            dark=bool(style.get("style_dark")),
        )
        nodes = _coerce_nodes([
            {"sender": {"user_id": 1, "nickname": "样例用户"},
             "time": int(__import__("time").time()) - 120,
             "message": [{"type": "text", "data": {
                 "text": "这是一条预览消息：气泡颜色、圆角、字号、时间格式都在这里体现。"}}]},
            {"sender": {"user_id": 2, "nickname": "第二位"},
             "time": int(__import__("time").time()),
             "message": [{"type": "text", "data": {
                 "text": "第二条，用来看多节点间距。"}},
                 {"type": "face", "data": {"id": "14"}}]},
        ])
        # 合成一个头像,否则看不出「圆形/方形」的差别
        # v1.17.0: 临时目录必须收拾。以前每次预览都 mkdtemp 且从不删除 ——
        # 拖一次滑块一个目录,实测 %TEMP% 里积了 108 个 wd_style_*。
        tmp = tempfile.mkdtemp(prefix="wd_style_")
        try:
            avatar = os.path.join(tmp, "avatar.png")
            im = Image.new("RGBA", (72, 72), (47, 125, 225, 255))
            ImageDraw.Draw(im).ellipse((10, 10, 62, 62), fill=(255, 255, 255, 255))
            im.save(avatar)
            for nd in nodes:
                nd.avatar_path = avatar
            return await fw.render(nodes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def reveal(self):
        """在运行 AstrBot 的这台机器上打开文件所在目录。

        桌面版用户看不到服务器路径,「打开目录」比给一串绝对路径有用。
        只对 storage_root 之下的真实目录生效,非 Windows 直接说明不支持。
        """
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        if not isinstance(body, dict):
            body = {}
        rid = str(body.get("id") or "")
        flat = str(body.get("path") or "")
        target = ""
        if flat:
            full = record_admin.resolve_flat(self._root, flat)
            if not full:
                return {"status": "error", "message": "文件不存在", "data": {}}
            target = os.path.dirname(full)
        elif rid:
            target = record_dir(self._root, rid)
            if not os.path.isdir(target):
                return {"status": "error", "message": "存档不存在", "data": {}}
        else:
            target = self._root
        if not os.path.isdir(target):
            return {"status": "error", "message": "目录不存在", "data": {}}
        if os.name != "nt":
            return {"status": "ok", "data": {"opened": False, "path": target,
                                             "note": "非 Windows 平台不自动打开"}}
        try:
            os.startfile(target)  # noqa: S606 - 只打开 storage_root 下的目录
        except OSError as e:
            return {"status": "error", "message": f"打开失败: {e!r}", "data": {}}
        return {"status": "ok", "data": {"opened": True, "path": target}}

    async def delete(self):
        from astrbot.api.web import request
        body = await request.json(default={}) or {}
        if not isinstance(body, dict):
            body = {}
        if str(body.get("source") or "") == "flat":
            # v1.17.0: 按行 id 删，文件只在「没有别的行还引用它」时才 unlink。
            # 以前是 os.remove + forget_path(该路径的全部行) —— Storage 去重让
            # 284 个路径被多行共享(最多 11 行)，删一张图会顺手删掉别人 10 条记录。
            path = str(body.get("path") or "")
            full = record_admin.resolve_flat(self._root, path)
            if not full:
                return {"status": "error", "message": "文件不存在或路径越界",
                        "data": {}}
            index = getattr(self.plugin, "_index", None)
            asset_id = body.get("asset_id")
            rows = 0
            left = 0
            if index is not None:
                try:
                    if asset_id not in (None, ""):
                        rows = index.delete_row(int(asset_id))
                    else:
                        rows = index.forget_path(full)
                    left = index.count_path(full)
                except Exception as e:
                    return {"status": "error",
                            "message": f"清理索引失败: {e!r}", "data": {}}
            if left > 0:
                # 文件还有别的记录在用：只摘掉这一条，别动字节
                return {"status": "ok", "data": {
                    "deleted": True, "kept_file": True, "path": full,
                    "rows": rows, "shared": left,
                }, "message": f"已删除这条记录；文件仍被 {left} 条记录引用，未删除"}
            res = record_admin.delete_flat(self._root, path)
            if res.get("deleted"):
                res["rows"] = rows
            return {"status": "ok" if res.get("deleted") else "error",
                    "message": res.get("reason", ""), "data": res}
        rid = str(body.get("id") or "")
        if not rid:
            return {"status": "error", "message": "缺少 id", "data": {}}
        res = record_admin.delete_record(self._root, rid)
        return {"status": "ok" if res.get("deleted") else "error",
                "message": res.get("reason", ""), "data": res}
