"""astrbot-plugin-media-warden 入口.

v1.3 增量:
  - /warden lookup <msg_id> 反向查询
  - 并发下载多组件
  - 配置项 retries / max_concurrent

保留 v1.2: forwarder / index / 命令 / 预览回传
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import time
from typing import Any, Optional

from astrbot.api.star import Star, register, Context
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import At, Plain

from .warden import (
    MediaWardenConfig,
    evaluate,
    event_platform,
    event_group_id,
    event_sender_id,
    event_sender_name,
    extract_components,
    recover_raw_media_refs,
    raw_segments,
    summarize,
    media_kind,
    kind_allowed,
    AssetResult,
    BatchResult,
    format_batch,
    classify_error,
    VERSION,
    AvatarCache,
    fetch_many_avatars,
)
from .warden.components import Component
from .warden.downloader import (
    aiohttp_fetcher, astrbot_component_fetcher,
    download, DownloadError, Downloaded, _local_fallback,
    _read_local_file,
)
from .warden.storage import Storage, SaveContext
from .warden.index import failure_subject
from .warden import record as record_store
from .warden.record_page import RecordPageApi


def _local_cache_candidates(comp: Component) -> list:
    """本地缓存查找要试的候选名（QQ 缓存文件名 = 消息段里的 `<md5>.<ext>`）。

    顺序：消息段原始 `file`（`meta["raw_file"]`）→ `comp.file_id` → `meta["path"]`
    的文件名 → url 的最后一段。去重、保序、丢掉空值与非字符串。
    """
    out: list = []
    meta = comp.meta or {}
    for v in (meta.get("raw_file"), comp.file_id, meta.get("path")):
        if isinstance(v, str) and v.strip():
            out.append(os.path.basename(v.strip()))
    url = (comp.url or "").strip()
    if url and "://" in url:
        try:
            from urllib.parse import urlparse
            last = urlparse(url).path.rsplit("/", 1)[-1]
            if last:
                out.append(last)
        except Exception:
            pass
    seen = set()
    uniq: list = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq[:4]


def _url_fileid(url: str) -> Optional[str]:
    """从多媒体下载链接里取 fileid（QQ 的 rkey 会变,fileid 是这张图的稳定标识）。"""
    if not isinstance(url, str) or not url:
        return None
    try:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
    except Exception:
        return None
    for key in ("fileid", "file_id"):
        vals = q.get(key)
        if vals and vals[0]:
            return vals[0]
    return None


# OneBot get_image 的参数形态:AstrBot 核心的 quoted_message/image_resolver.py 也是
# 逐个试这些形状(见 _build_image_resolve_actions)。不同 OneBot 实现对 file/file_id 的
# 语义不一致,只试一种就会像 v1.9.0 那样静默降级。
_GET_IMAGE_PARAM_FORMS = ("file", "file_id", "id", "image")
_MAX_REFETCH_ATTEMPTS = 8
_MAX_REFETCH_CANDIDATES = 3


def _short(v, n: int = 64) -> str:
    """日志用:把可能很长的候选值/url 截短。"""
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[:n] + "…"


def _b64_payload(raw: str) -> Optional[bytes]:
    """解 base64(兼容 data:image/...;base64, 前缀),解不出来返回 None。"""
    import base64
    s = raw.split(",", 1)[-1] if raw.startswith("data:") else raw
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=False)
    except Exception:
        return None


def _media_refetch_candidates(comp: Component) -> list[str]:
    """get_image 的候选取值:原始 segment 的 file(最可靠)优先,fileid 兜底。

    AstrBot 4.26+ 的 PreProcessStage 会把 Image 组件重写成 jpeg 静帧并覆盖
    component.file_id(值变成 "C0C9610D...jpg" 这种本地文件名),拿它当 get_image 的
    入参会得到 retcode=1200 file not found —— 所以原始 segment 的 file 要优先。
    """
    meta = comp.meta or {}
    out: list[str] = []
    seen: set = set()

    def _add(v) -> None:
        if not isinstance(v, str):
            return
        v = v.strip()
        if not v or v in seen or "://" in v or len(v) > 256:
            return
        seen.add(v)
        out.append(v)
        # AstrBot 核心还会再试一次去掉扩展名的 basename
        base, _, ext = v.rpartition(".")
        if base and ext and len(ext) <= 5 and base not in seen:
            seen.add(base)
            out.append(base)

    for v in (meta.get("raw_file"), meta.get("file"),
              meta.get("fileid"), meta.get("file_id"),
              _url_fileid(comp.url or ""),
              (meta.get("path") if not os.path.exists(str(meta.get("path") or ""))
               else None),
              comp.file_id):
        _add(v)
    return out


def _segment_list(resp) -> list:
    """从 get_msg 的返回里取出 message 段列表。

    **aiocqhttp 的 call_action 已经把外层结果解包了**（`_handle_api_result` 返回
    `result["data"]`），所以正常拿到的是 `{"message": [...], "message_id": …}` 本身；
    但别的实现/中间层可能带外层，所以两种包裹都认。
    """
    if not isinstance(resp, dict):
        return []
    for cand in (resp.get("message"), (resp.get("data") or {}).get("message")
                 if isinstance(resp.get("data"), dict) else None):
        if isinstance(cand, list):
            return cand
        if isinstance(cand, dict) and isinstance(cand.get("message"), list):
            return cand["message"]
    return []


def _media_urls_from_resp(resp, *, old: str = "", hint: Optional[str] = None) -> list[str]:
    """从 OneBot `get_msg` 的返回里挑出新链接。

    napcat 的 `get_msg` 会用**当前** rkey 重新生成图片 url（它的 picElement → OB11
    转换器每次都调 `getImageUrl`），这是唯一绕开「消息里那条已过期 url」的路子。
    hint 是原始 segment 的 `file`（文件名），用来在多图消息里挑对那一段。
    """
    hit: list[str] = []
    other: list[str] = []
    for seg in _segment_list(resp):
        if not isinstance(seg, dict):
            continue
        if str(seg.get("type") or "").lower() != "image":
            continue
        data = seg.get("data")
        if not isinstance(data, dict):
            continue
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url == old:
            continue
        fname = data.get("file")
        if hint and isinstance(fname, str) and fname == hint:
            hit.append(url)
        else:
            other.append(url)
    return hit + other


def _is_sha16(token: str) -> bool:
    """16 位十六进制 = 本插件的内容指纹(storage 用 blake2b 取前 16 位)."""
    return len(token) == 16 and all(c in "0123456789abcdefABCDEF" for c in token)


def _format_asset_row(r: dict) -> str:
    """/warden list / lookup(按 msg_id) / lookup(按 sha16) 共用的单行渲染.

    时间可读而不是裸 epoch,并带上 sha16 —— 那正是 /warden lookup <sha16> 的入参。
    """
    ts = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
    fwd = f" | fwd={r['forward_meta']}" if r.get("forward_meta") else ""
    return (
        f"- id={r['id']} [{ts}] {r['platform']}:{r['group_id']} "
        f"sender={r['sender_id']} msg={r.get('msg_id')}#{r['idx']} "
        f"kind={r['kind']} size={r['size']} "
        f"sha16={r.get('sha16')}{fwd} -> {r['path']}"
    )


def _failure_subject_for(comp: Component) -> str:
    """稳定去重键:优先 fileid（rkey 换了也能对上同一张图）,否则 host+path。"""
    fid = (comp.meta or {}).get("fileid") or comp.file_id or ""
    if fid and "://" not in str(fid):
        return f"fileid:{fid}"
    return failure_subject(comp.url)


# v1.9.9: 网络全败后「等客户端把图下载下来再查本地缓存」的等待点(秒)。
# 线上实测(2026-10-11): 消息刚到的那一瞬间客户端还没落盘 —— 插件第一次查必 miss,
# 而 napcat 给的那条 url 又已经失效(http 400); 客户端随后(几秒~几分钟)才下载。
# 实测那一次 d867ce16… 在转发失败后 6.5 分钟才落到本地缓存, 所以窗口从 20s
# 扩到 25s; 但更大的窗口会拖慢回执, 更慢的情形留给「重新触发一次」。
# 只在前面的路全败时才走, 单次代价 = sum(_LATE_CACHE_DELAYS_S)。
_LATE_CACHE_DELAYS_S = (5, 20)


def _upgrade_qq_image_url(url: str) -> str:
    """把 QQ 图床的缩略图 url 升级为原图 url。

    napcat/OneBot 合并转发节点里的 image url 多为缩略 jpeg，
    gchat.qpic.cn 域带 is_origin=0。把它改成 is_origin=1 即可拿原图。
    不是 QQ 图床 url / 拿不准时原样返回，由调用方回退。
    """
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return url
    if "gchat.qpic.cn" not in url and "multimedia.nt.qq" not in url:
        return url
    if "is_origin=0" in url:
        return url.replace("is_origin=0", "is_origin=1")
    return url


@register(
    "media-warden",
    "shirley",
    "群聊素材守门人 v1.14.0: 按群/按人 + 按类型(图片/动图/视频/纯文本/聊天记录/文档, 含扩展名白名单)归档, 模板命名落盘 + blake2b 去重, 转发链 PIL 渲染/JSON 保存, 并发下载 + 指数退避重试 + 源链接失效补救(QQ 本地缓存按 MD5 / OneBot get_image 多形态重取 + get_msg 重解析 / 组件本地副本 / 迟到缓存重查), 失败分类入库可查(含反复失败 top), /warden list/stats/export/lookup(含 sha16)/prune/fail/存档/重发, 预览回传 + 可选引用原消息, dashboard 分类管理页面(浏览/搜索/取图/发回/跨群转发/删除) + 渲染样式页(实时预览), 命令仅管理员可用",
    VERSION,
)
class MediaWardenStar(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        # v1.14.0: 留着宿主的配置对象(dict 子类),样式页保存时要写回它
        self._config_obj = config if isinstance(config, dict) else None
        self.cfg, self._config_errors = MediaWardenConfig.from_raw_lenient(config or {})
        if self._config_errors:
            print(f"[media-warden] config warnings: {self._config_errors}")
        self._storage: Optional[Storage] = None
        self._forwarder = None
        self._index = None
        self._avatar_cache = None
        # 迟到缓存重查的等待倍率(测试里置 0 以免真等)
        self._sleep_scale = 1.0
        self._log(
            f"loaded: v{VERSION} | mode={self.cfg.match_mode} "
            f"| groups={len(self.cfg.target_groups)} "
            f"users={len(self.cfg.target_users)} "
            f"| root={self.cfg.storage_root} "
            f"| retries={self.cfg.download_retries} "
            f"| concurrent={self.cfg.max_concurrent}"
        )

    async def initialize(self) -> None:
        self._storage = Storage(
            root=self.cfg.storage_root,
            dedupe=self.cfg.dedupe,
            pattern=self.cfg.filename_pattern,
            max_bytes=self.cfg.max_file_size_bytes,
        )
        try:
            self._avatar_cache = AvatarCache(
                root=os.path.join(self.cfg.storage_root, "_avatars"),
                timeout_s=self.cfg.avatar_timeout_s,
                max_bytes=self.cfg.avatar_cache_size_mb * 1024 * 1024,
            )
        except Exception as e:
            self._log(f"avatar cache init failed: {e!r} — runs without avatars")
            self._avatar_cache = None
        # 引擎只有 pil 落地:另外两个取值留着兼容旧配置,但必须说清楚(不静默生效)
        engine = (self.cfg.forward_image_engine or "pil").lower()
        if engine != "pil":
            self._log(
                f"forward_image_engine={engine!r} 未实现 — 实际使用 PIL 渲染"
                f"(明确告警而非静默降级)"
            )
        try:
            # 提前探测 Pillow:forwarder 模块本身不 import PIL(全在函数内),
            # 所以等到渲染时才失败会让每条消息都白跑一次,这里提前降级到 JSON。
            import PIL  # noqa: F401
            from .warden.forwarder import Forwarder
            self._forwarder = Forwarder(
                width=self.cfg.forward_image_width,
                max_nodes=self.cfg.forward_render_max_nodes,
            )
            self._apply_render_style()
        except ImportError as e:
            self._log(f"forwarder unavailable (Pillow 缺失?): {e!r} — JSON sidecar 兜底")
            self._forwarder = None
        except Exception as e:
            self._log(f"forwarder init failed: {e!r} — JSON sidecar 兜底")
            self._forwarder = None
        if self.cfg.enable_index_db:
            try:
                from .warden.index import AssetIndex
                self._index = AssetIndex(
                    db_path=os.path.join(self.cfg.storage_root, "_warden.db")
                )
                self._index.open()
            except Exception as e:
                self._log(f"index init failed: {e!r} — runs without index")
                self._index = None
        try:
            # dashboard 插件页面(pages/dashboard)的后端:老版本 AstrBot 没有
            # register_web_api 时安静跳过,页面本身也就不会被发现。
            self._page_api = RecordPageApi(self)
            if not self._page_api.register_routes():
                self._log("page api unavailable (宿主没有 register_web_api) — 存档页面不可用")
                self._page_api = None
        except Exception as e:
            self._log(f"page api init failed: {e!r} — 存档页面不可用")
            self._page_api = None
        self._log(
            f"initialize: storage={self._storage.root} "
            f"forwarder={'on' if self._forwarder else 'off'} "
            f"index={'on' if self._index else 'off'} "
            f"avatars={'on' if (self.cfg.enable_user_avatar and self._avatar_cache) else 'off'} "
            f"page={'on' if self._page_api else 'off'}"
        )
        return None

    # ----------------- 样式(v1.14.0) -----------------

    _STYLE_FIELDS = (
        "style_width", "style_bg_color", "style_bubble_color",
        "style_text_color", "style_dim_color", "style_radius",
        "style_font_size", "style_avatar_shape", "style_time_format",
        "style_dark",
    )

    def _apply_render_style(self) -> None:
        """把 cfg 里的样式参数灌进渲染器(初始化与样式页保存后都走这里)."""
        if self._forwarder is None:
            return
        c = self.cfg
        try:
            self._forwarder.apply_style(
                width=c.style_width,
                bg=c.style_bg_color,
                bubble=c.style_bubble_color,
                text=c.style_text_color,
                dim=c.style_dim_color,
                radius=c.style_radius,
                font_size=c.style_font_size,
                avatar_shape=c.style_avatar_shape,
                time_format=c.style_time_format,
                dark=bool(c.style_dark),
            )
        except Exception as e:
            self._log(f"apply render style failed: {e!r}")

    def style_snapshot(self) -> dict:
        out = {k: getattr(self.cfg, k) for k in self._STYLE_FIELDS}
        out["forward_image_width"] = self.cfg.forward_image_width
        return out

    def update_style(self, values: dict) -> tuple[dict, list[str], list[str]]:
        """样式页保存:校验 -> 应用到 cfg -> 灌进渲染器 -> 写回插件配置文件.

        返回 (快照, 校验错误, 已持久化的键) —— 三者分开,调用方不用猜第二项是什么。
        """
        merged = {k: getattr(self.cfg, k) for k in self._STYLE_FIELDS}
        for k, v in (values or {}).items():
            if k in merged:
                merged[k] = v
        cand, errors = MediaWardenConfig.from_raw_lenient({**merged})
        if errors:
            return {}, errors, []
        for k in self._STYLE_FIELDS:
            setattr(self.cfg, k, getattr(cand, k))
        self._apply_render_style()
        persisted = self._persist_config({k: getattr(self.cfg, k)
                                         for k in self._STYLE_FIELDS})
        return self.style_snapshot(), [], persisted

    def _persist_config(self, updates: dict) -> list[str]:
        """写回宿主的插件配置文件(失败只记日志 —— 本次运行仍然生效).

        宿主的插件配置按 `_conf_schema.json` 分组嵌套（`{"render_style": {...}}`），
        写到顶层既不会生效、还会在文件里留下同名的重复键。所以先找这个键所在的
        分组，写进它，找不到才写顶层。
        """
        obj = getattr(self, "_config_obj", None)
        if not isinstance(obj, dict):
            return []
        groups = [(k, v) for k, v in obj.items() if isinstance(v, dict)]
        written: list[str] = []
        for k, v in (updates or {}).items():
            target = obj
            for _group_name, group_value in groups:
                if k in group_value:
                    target = group_value
                    break
            try:
                target[k] = v
                written.append(k)
            except Exception as e:
                self._log(f"config set {k} failed: {e!r}")
        saver = getattr(obj, "save_config", None)
        if callable(saver):
            try:
                saver()
            except Exception as e:
                self._log(f"config save failed: {e!r} (本次运行仍生效)")
        return written

    async def terminate(self) -> None:
        if self._index is not None:
            try:
                self._index.close()
            except Exception:
                pass
        self._log("terminate: ok")

    # ----------------- 钩子 -----------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        decision = evaluate(self.cfg, event)
        if not decision.allow:
            if self.cfg.log_to_stdout:
                self._log(f"skip: {decision.reason}")
            return

        components = extract_components(event)

        # v1.14.0 类型门控:六类各自开关 + 文档扩展名白名单.
        # 门控放在这里(而不是下载之后),是为了让「类型没开」和「下载失败」在日志里
        # 分得清 —— 前者不是错误,不该进 failures 表。
        media: list[Component] = []
        dropped: dict[str, int] = {}
        for c in components:
            if not c.is_media:
                continue
            kind = media_kind(c)
            if not kind_allowed(self.cfg, kind):
                dropped[kind] = dropped.get(kind, 0) + 1
                continue
            if kind == "file" and not self.cfg.doc_ext_allowed(
                    (c.name or "") or (c.meta or {}).get("name") or ""):
                dropped["file_ext"] = dropped.get("file_ext", 0) + 1
                continue
            media.append(c)

        forwards = [c for c in components if c.is_forward]
        if forwards and not kind_allowed(self.cfg, "forward"):
            dropped["forward"] = len(forwards)
            forwards = []

        text_segments = self._text_segments(event)
        # 纯文本只在这条消息没有别的可存内容时才算数(带图的消息已由图片条目覆盖)
        wants_text = (
            bool(text_segments) and not media and not forwards
            and kind_allowed(self.cfg, "text")
        )

        if not media and not forwards and not wants_text:
            if self.cfg.log_to_stdout:
                extra = "".join(f" {k}={v}" for k, v in sorted(dropped.items()))
                self._log(
                    f"skip: nothing to save{(' | dropped:' + extra) if extra else ''} "
                    f"| msg={getattr(event, 'message_id', '?')} "
                    f"| segments={summarize(components)}"
                )
            return

        platform = (event_platform(event) or "unknown").lower()
        group_id = event_group_id(event) or "unknown"
        sender_id = event_sender_id(event) or "anon"
        sender_name = event_sender_name(event) or sender_id
        msg_obj = getattr(event, "message_obj", None)
        msg_id = (
            getattr(event, "message_id", None)
            or (getattr(msg_obj, "message_id", None) if msg_obj else None)
            or "nomsg"
        )
        ts = int(
            getattr(event, "timestamp", None)
            or (getattr(msg_obj, "timestamp", None) if msg_obj else None)
            or time.time()
        )

        if self.cfg.log_to_stdout:
            self._log(
                f"matched | msg={msg_id} sender={sender_id} "
                f"media={len(media)} forward={len(forwards)}"
            )

        # 纯文本走独立分支:一条消息就是一个条目,没有下载/落盘的并发问题
        if wants_text:
            t0 = time.time()
            text_item = await self._save_text(
                text_segments, platform=platform, group_id=group_id,
                sender_id=sender_id, sender_name=sender_name,
                msg_id=msg_id, ts=ts,
            )
            text_batch = BatchResult(items=[text_item],
                                     duration_s=time.time() - t0)
            async for ev in self._emit_receipt(text_batch, msg_id, sender_id,
                                              event=event):
                yield ev
            return

        t0 = time.time()
        batch = BatchResult()

        # 0) 先从 raw_message 恢复原始 http url.
        #    AstrBot 4.26+ 的 PreProcessStage 会把 Image 组件转成 jpeg 静帧,
        #    并覆盖 component.url/file/path; raw_message 里 napcat 保留原始引用(例如 gif).
        #    这里只恢复 http(s) url, 避免把本地缓存路径降级成 jpeg 静帧.
        self._recover_original_urls(event, media)

        # 1) 并发下载所有 media
        #    v1.9: 先看索引库有没有同一条消息的旧成果(index_reuse_saved)。
        save_contexts = [
            SaveContext(platform=platform, group_id=group_id, sender_id=sender_id,
                        sender_name=sender_name, msg_id=msg_id, idx=idx, ts=ts)
            for idx in range(len(media))
        ]
        reuse: list[tuple[Optional[AssetResult], Optional[str]]] = [
            (None, None) for _ in media
        ]
        if self.cfg.index_reuse_saved and self._index is not None:
            for i, comp in enumerate(media):
                hit = self._find_saved_asset(msg_id, i, platform=platform,
                                             group_id=group_id)
                if hit is not None:
                    reuse[i] = self._reuse_saved(comp, save_contexts[i], hit)
        missing = [i for i, (r, _) in enumerate(reuse) if r is None]
        fresh = await self._download_all([media[i] for i in missing], event)
        for slot, dl in zip(missing, fresh):
            reuse[slot] = (None, dl)
        for comp, ctx, (reused, dl) in zip(media, save_contexts, reuse):
            if reused is not None:
                batch.items.append(reused)
                continue
            batch.items.append(self._store_dl(comp, ctx, dl))

        # 2) 转发节点(顺序,因渲染需要前序 json)
        for idx, comp in enumerate(forwards, start=len(media)):
            ctx = SaveContext(
                platform=platform, group_id=group_id, sender_id=sender_id,
                sender_name=sender_name, msg_id=msg_id, idx=idx, ts=ts,
            )
            for r in await self._save_forward(comp, ctx, event):
                batch.items.append(r)

        batch.duration_s = time.time() - t0
        async for ev in self._emit_receipt(batch, msg_id, sender_id, event=event):
            yield ev

    async def _emit_receipt(self, batch: BatchResult, msg_id: str,
                            sender_id: str, event: AstrMessageEvent | None = None):
        """回执 + 预览 + @失败者 —— 图片路径与纯文本路径共用,避免两套文案漂移。"""
        text = format_batch(batch)
        # reply_to_original: 把回执挂成对原消息的引用(默认关闭 = v1.9 的实际行为)
        reply_prefix = self._reply_prefix(msg_id)
        if event is not None and reply_prefix:
            yield event.chain_result(reply_prefix + [Plain(text)])
        elif event is not None:
            yield event.plain_result(text)
        if event is not None:
            for extra in self._build_previews(event, batch):
                yield extra
        if self.cfg.at_on_save_failure and batch.fail_count > 0:
            try:
                if event is not None:
                    yield event.chain_result([
                        At(qq=sender_id),
                        Plain(" 有 " + batch.failure_phrase()),
                    ])
            except Exception as e:
                self._log(f"at-on-failure notify failed: {e!r}")

    # ----------------- 内部:纯文本条目 -----------------

    _TEXT_SEG_KINDS = ("text", "at", "face", "reply")

    def _text_segments(self, event: AstrMessageEvent) -> list[dict]:
        """纯文本条目的段列表.

        优先用 raw_message(保真:At/Face/Reply 原样保留),没有时退化成
        message_str 的一行文字 —— 非 OneBot 平台也能存,只是丢表情与 @。
        """
        try:
            segs = [s for s in raw_segments(event)
                    if s.get("type") in self._TEXT_SEG_KINDS]
        except Exception as e:
            self._log(f"raw segments failed: {e!r}")
            segs = []
        if segs:
            return segs
        text = (getattr(event, "message_str", "") or "").strip()
        return [{"type": "text", "data": {"text": text}}] if text else []

    async def _fetch_node_avatars(self, nodes: list, platform: str) -> None:
        """给渲染节点补头像(失败静默,渲染照常)."""
        if not (self.cfg.enable_user_avatar and self._avatar_cache is not None and nodes):
            return
        allowed = {p.lower() for p in self.cfg.avatar_platforms}
        if (platform or "").lower() not in allowed:
            return
        try:
            seen = set()
            pairs = []
            for nd in nodes:
                key = (platform, nd.sender_id)
                if nd.sender_id and key not in seen:
                    seen.add(key)
                    pairs.append(key)
            if not pairs:
                return
            avatar_map = await fetch_many_avatars(
                self._avatar_cache, pairs, concurrency=self.cfg.max_concurrent,
            )
            for nd in nodes:
                nd.avatar_path = avatar_map.get((platform, nd.sender_id))
        except Exception as e:
            self._log(f"avatar fetch failed: {e!r} — proceed without")

    async def _save_text(self, segments: list, *, platform: str, group_id: str,
                         sender_id: str, sender_name: str, msg_id: str,
                         ts: int) -> AssetResult:
        """纯文本条目:entry.json(结构化) + render.png(Pillow 渲染) 共用一个目录.

        两种形式都存是刻意的:结构化那份能搜索、能改样式重渲染、能重建发送;
        渲染图那份就是用户眼里的「正常形式」。
        """
        root = self.cfg.storage_root
        rid = record_store.make_record_id(group_id, msg_id, ts)
        ctx = SaveContext(platform=platform, group_id=group_id,
                          sender_id=sender_id, sender_name=sender_name,
                          msg_id=msg_id, idx=0, ts=ts)
        comp = Component(kind="text", name=f"text_{msg_id}")
        try:
            await record_store.capture_text(
                segments, platform=platform, group_id=group_id, msg_id=msg_id,
                sender_id=sender_id, sender_name=sender_name,
                storage_root=root, ts=ts, record_id=rid,
            )
        except Exception as e:
            return self._failed(
                comp, ctx, "text",
                f"text save: {type(e).__name__}: {e}", e,
                subject=f"text:{msg_id}#0", failure_kind="save",
            )

        render_name = None
        png: Optional[bytes] = None
        if self._forwarder is not None:
            from .warden.forwarder import _coerce_nodes
            node = {
                "sender": {"user_id": sender_id, "nickname": sender_name},
                "time": int(ts),
                "message": segments,
            }
            nodes = _coerce_nodes([node])
            if nodes and self._forwarder.can_handle(nodes):
                await self._fetch_node_avatars(nodes, platform)
                try:
                    png = await self._forwarder.render(nodes)
                except Exception as e:
                    self._log(f"text render failed: {e!r}")
                else:
                    try:
                        render_name = record_store.save_render(root, rid, png)
                    except OSError as e:
                        self._log(f"text render write failed: {e!r}")

        rel = render_name or record_store.ENTRY_FILENAME
        path = os.path.join(record_store.record_dir(root, rid), rel)
        size = len(png) if (png is not None and render_name) else 0
        if self._index is not None:
            try:
                sha16 = hashlib.blake2b(png, digest_size=8).hexdigest() if png else None
                self._index.record(
                    platform=platform, group_id=group_id, sender_id=sender_id,
                    msg_id=msg_id, idx=0, kind="text", path=path, size=size,
                    sha16=sha16,
                )
            except Exception as e:
                self._log(f"index record failed: {e!r}")
        if render_name is None:
            self._log(f"text saved without render: {rid}")
        return AssetResult(
            kind="text", ok=True, path=path, size=size,
            preview_path=path if render_name else None,
        )

    # ----------------- 内部:并发下载 + 落盘 -----------------

    def _recover_original_urls(self, event, media: list[Component]) -> None:
        """从 raw_message 恢复被 PreProcessStage 覆盖的原始 http url.

        只恢复 http(s) url, 并按 kind 顺序回填到对应 media 组件;
        没有原始 http 引用时保持组件原样(兼容非 OneBot 平台).
        """
        try:
            refs = recover_raw_media_refs(event)
        except Exception as e:
            if self.cfg.log_to_stdout:
                self._log(f"recover raw urls failed: {e!r}")
            return
        if not refs:
            return
        from collections import defaultdict
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in refs:
            buckets[r["kind"]].append(r)
        # v1.10: 先按「标识」配对,配不上再按顺序取。
        # 只按顺序时,raw_message 与组件列表只要有一边多/少一段(表情被转成别的类型、
        # 某段没有 url),后面所有图都会领到别人的 url —— 那比不恢复更糟。
        def _ref_keys(ref: dict) -> set:
            keys: set = set()
            for k in ("file", "name"):
                v = ref.get(k)
                if isinstance(v, str) and v.strip():
                    keys.add(v.strip())
                    keys.add(os.path.basename(v.strip()))
            return keys

        def _comp_keys(comp: Component) -> set:
            keys: set = set()
            for v in ((comp.meta or {}).get("raw_file"), (comp.meta or {}).get("path"),
                      comp.file_id, comp.name):
                if isinstance(v, str) and v.strip():
                    keys.add(v.strip())
                    keys.add(os.path.basename(v.strip()))
            url = (comp.url or "").strip()
            if url and "://" in url:
                keys.add(url.rsplit("/", 1)[-1])
            return keys

        used: set = set()
        for comp in media:
            lst = buckets.get(comp.kind) or []
            want = _comp_keys(comp)
            ref = None
            if want:
                for j, cand in enumerate(lst):
                    if j in used:
                        continue
                    if want & _ref_keys(cand):
                        ref = cand
                        used.add(j)
                        break
            if ref is None:
                for j, cand in enumerate(lst):
                    if j in used:
                        continue
                    ref = cand
                    used.add(j)
                    break
            if ref is None:
                continue
            url = ref.get("url")
            if url:
                comp.url = url
                fid = _url_fileid(url)
                if fid:
                    # 记下 fileid: rkey 会过期,fileid 是 OneBot get_image 的重取键
                    comp.meta["fileid"] = fid
                # 记下 segment 原始的 file: napcat 的图片 id。PreProcessStage 会把
                # component.file_id 覆盖成 jpeg 静帧文件名,那个值 get_image 查不到
                # (retcode=1200 file not found) —— 补救时必须优先用这个原值。
                raw_file = ref.get("file")
                if isinstance(raw_file, str) and raw_file:
                    comp.meta["raw_file"] = raw_file
                if ref.get("name") and not comp.name:
                    comp.name = ref["name"]
                if ref.get("size") and not comp.size:
                    comp.size = ref["size"]
                if self.cfg.log_to_stdout:
                    self._log(f"recovered original url for {comp.kind}: {url[:60]}...")

    async def _download_all(self, media: list[Component], event=None) -> list:
        """并发下载所有 media 组件.

        用 Semaphore 限流到 cfg.max_concurrent.
        单个失败 -> 该项是 DownloadError,其他继续;最后返回 list[Downloaded|DownloadError].

        v1.9: 每个组件按「本地缓存 -> 原 url 重试 -> OneBot get_image/get_msg 重取 ->
        组件本地副本 -> 迟到缓存重查」逐级回退;全败才记失败(入索引库 + 写日志)。
        """
        if not media:
            return []
        sem = asyncio.Semaphore(self.cfg.max_concurrent)

        async def _one(c):
            async with sem:
                try:
                    return await self._download_one(c, event)
                except DownloadError as e:
                    return e
                except Exception as e:
                    return DownloadError(f"{type(e).__name__}: {e}")

        return await asyncio.gather(*(_one(c) for c in media))

    async def _cached_copy(self, comp: Component) -> tuple:
        """查 QQ 本地图片缓存(按消息段里的 MD5)。返回 (Downloaded|None, 未命中候选)。

        QQ 收到图片时已经按 MD5 存了副本，而消息段的 data.file 就是那个 MD5。
        链接因 rkey 过期返回 400 时，本地这份是唯一还能拿到的来源 —— 所以放在
        **最前面**：有就不必再等一次注定失败的 http。

        v1.10: 未命中的候选随返回值一起给出(调用方写进失败回执),不再挂到
        self._local_cache_misses —— 并发下载时那是跨组件的共享状态。
        """
        data, path, misses = await self._local_cache_read(_local_cache_candidates(comp))
        if data is None:
            return None, misses
        data.origin = f"localcache:{path}"
        self._log(f"used QQ local cache for {comp.kind}: {path}")
        return data, misses

    async def _local_cache_read(self, names: list) -> tuple:
        """按候选名查 QQ 本地缓存并读出字节。返回 (Downloaded|None, 命中的 path, misses)。

        媒体(候选来自消息段)与转发节点图(候选来自节点 image 段的 "file" 字段)共用
        这一份查找/读取/失败记录 —— v1.10.0 之前是两套写法,「命中但读不出来」与
        「查找抛错」各处理一遍,改一处漏一处。
        """
        if not self.cfg.get_media_fallback or not names:
            return None, None, []
        try:
            from .warden.localcache import find_cached_image as _find  # 局部导入便于测试打桩
        except Exception as e:  # noqa: BLE001
            self._log(f"localcache module unavailable: {e!r}")
            return None, None, []
        misses: list[str] = []
        for cand in names:
            try:
                path = await asyncio.to_thread(_find, cand)
            except Exception as e:  # noqa: BLE001  本地查找失败不该影响下载
                self._log(f"local cache lookup failed for {cand!r}: {e!r}")
                misses.append(f"{cand}: lookup error")
                continue
            if not path:
                misses.append(cand)
                continue
            try:
                data = await asyncio.to_thread(
                    _read_local_file, path, self.cfg.max_file_size_bytes
                )
            except DownloadError as e:
                self._log(f"local cache hit but unreadable ({path}): {e}")
                misses.append(f"{cand}: unreadable")
                continue
            return data, path, misses
        return None, None, misses

    async def _download_one(self, comp: Component, event=None):
        """单个组件的下载回退链;全失败时抛首个(最对症的)下载错误。

        顺序:
        ⓪ QQ 本地缓存(按消息段 MD5) —— 命中即用,不碰网络
        ① 正常 http / 组件 fetcher(内部含 raw 本地副本回退)
        ② 向 bot 重取(get_image / get_msg) —— 只对「消息段里有新 url」的情况有效
        ③ 组件本地副本(temp 缓存 / raw 转换)
        ④ 等一等再查本地缓存(客户端可能刚把图下载下来)
        """
        cached, misses = await self._cached_copy(comp)
        if cached is not None:
            return cached
        attempts: list[str] = []
        if misses:
            attempts.append("qcache: 本地无副本 " + ", ".join(misses[:2]))
        try:
            dl = await download(
                comp, fetcher=astrbot_component_fetcher,
                retries=self.cfg.download_retries,
                max_bytes=self.cfg.max_file_size_bytes,
                timeout_s=self.cfg.download_timeout_s,
            )
        except DownloadError as primary:
            attempts.append(f"orig: {primary}")
            note = getattr(primary, "fallback_error", None)
            if note:
                attempts.append(f"local: {note}")
            dl = None
            # 第二级: 向 bot 重取(get_image / get_msg) —— rkey 过期唯一能救回来的路
            if self.cfg.get_media_fallback and event is not None:
                dl, trace = await self._refetch_media(comp, event)
                if dl is None:
                    attempts.append(f"refetch: {trace[-1] if trace else 'no usable ref'}")
            # 第三级: 组件本地副本(napcat 的 temp 缓存 / raw 转换) —— 与 event 无关,总是试
            if dl is None:
                try:
                    dl = await _local_fallback(comp, self.cfg.max_file_size_bytes)
                except DownloadError as e:
                    attempts.append(f"local: {e}")
                else:
                    self._log(f"used local copy for {comp.kind} after download failure")
            if dl is None:
                # 第四级: 等一会儿再看 QQ 本地缓存。
                async def _probe():
                    got, _ = await self._cached_copy(comp)
                    return got

                dl = await self._late_cache(_probe, f"late local cache ({comp.kind})")
            if dl is None:
                primary.fallback_error = "; ".join(attempts[:3])[:300]
                raise primary
        return dl

    async def _refetch_media(self, comp: Component, event):
        """二级补救:向 bot 重取这张图的可用链接。返回 (Downloaded|None, trace)。

        两条路,依次尝试:

        ① get_image —— 对 (候选值 × 参数形态 file/file_id/id/image) 组合试。
           注意 napcat 的 get_image 其实**只认它自己发出去的消息文件 uuid**
           （FileNapCatOneBotUUID，内存 map，进程重启或超过 ttl 就没了）；用文件名
           当候选时会走到 FileApi.searchForFile，图片不在那个缓存里 → file not found。
        ② get_msg —— 拿消息 id 让 napcat 重新解析整条消息。它的 picElement → OB11
           转换器每次都调 getImageUrl()，即用**当前** rkey 重新拼链接，所以这是唯一
           能绕开「消息里那条已过期 url」的路(2026-10-11 线上 400 的根因)。

        全败时返回 (None, trace),调用方跳到第三级本地副本。
        v1.10: trace 随返回值给出,不再写 self._refetch_trace/_refetch_last_error ——
        那些属性在一批消息并发下载时会互相覆盖,回执里的原因会张冠李戴。
        """
        call = self._resolve_call_action(event)
        if call is None:
            return None, ["refetch: event has no call_action"]
        cands = _media_refetch_candidates(comp)
        old = (comp.url or "").strip()
        trace: list[str] = []
        attempts = 0

        for cand in cands[:_MAX_REFETCH_CANDIDATES]:
            for key in _GET_IMAGE_PARAM_FORMS:
                if attempts >= _MAX_REFETCH_ATTEMPTS:
                    break
                attempts += 1
                try:
                    resp = await call("get_image", **{key: cand})
                except Exception as e:  # ActionFailed / 不支持该参数形态
                    trace.append(f"get_image {key}={_short(cand)} -> {type(e).__name__}: {e}")
                    continue
                data = resp.get("data") if isinstance(resp, dict) else None
                if not isinstance(data, dict):
                    trace.append(f"get_image {key}={_short(cand)} -> no data")
                    continue
                fresh = data.get("url")
                trace.append(
                    f"get_image {key}={_short(cand)} -> url={_short(str(fresh), 50)}"
                )
                if isinstance(fresh, str) and fresh and fresh != old:
                    dl = await self._download_fresh(comp, fresh)
                    if dl is not None:
                        self._log(f"get_image refreshed url via {key} for {comp.kind}")
                        return dl, trace
                    trace.append(f"download fresh url failed: {_short(fresh, 50)}")
                    continue
                b64 = data.get("base64")
                if isinstance(b64, str) and b64 and len(b64) >= 16:
                    raw = _b64_payload(b64)
                    if raw:
                        dl = Downloaded(data=raw, mime=None, size=len(raw))
                        dl.origin = f"base64:{_short(cand, 24)}"
                        self._log(f"get_image returned base64 for {comp.kind}")
                        return dl, trace
            if attempts >= _MAX_REFETCH_ATTEMPTS:
                break

        # ② get_msg:让 napcat 用当前 rkey 重新解析这条消息
        dl = await self._refetch_via_message(comp, event, trace)
        if dl is not None:
            return dl, trace

        self._log(f"refetch exhausted for {comp.kind} "
                  f"({len(cands)} candidate(s), get_image={attempts})")
        for line in trace[-4:]:
            self._log(f"  refetch: {line}")
        return None, trace

    async def _download_fresh(self, comp: Component, fresh: str):
        """把重取到的新链接下载成 Downloaded;失败返回 None(原因由调用方记 trace)."""
        try:
            dl = await download(
                Component(kind=comp.kind, url=fresh,
                          file_id=comp.file_id, meta=dict(comp.meta or {})),
                fetcher=aiohttp_fetcher,
                retries=self.cfg.download_retries,
                max_bytes=self.cfg.max_file_size_bytes,
                timeout_s=self.cfg.download_timeout_s,
            )
        except DownloadError:
            return None
        dl.origin = fresh
        return dl

    async def _late_cache(self, probe, label: str):
        """等客户端把图下载下来再查一次本地缓存(见 _LATE_CACHE_DELAYS_S).

        媒体与转发节点图共用这一段 —— 两处以前各写一份 sleep 循环。
        probe 是无参 async 函数,命中返回非 None。
        """
        for delay in _LATE_CACHE_DELAYS_S:
            await asyncio.sleep(self._sleep_scale * delay)
            got = await probe()
            if got is not None:
                self._log(f"{label} hit after {delay}s")
                return got
            self._log(f"{label} still miss after {delay}s")
        return None

    def _reply_prefix(self, msg_id) -> list:
        """reply_to_original 打开时,给回执套一个 Reply(引用原消息).

        特性探测:拿不到 Reply 组件/构造失败就返回空列表,回执退化为纯文本 ——
        一个展示开关不该让保存失败。
        """
        if not self.cfg.reply_to_original:
            return []
        if not msg_id or str(msg_id) in ("nomsg", "nonemsg", "0"):
            return []
        try:
            from astrbot.api.message_components import Reply  # type: ignore
        except Exception:
            return []
        try:
            return [Reply(id=str(msg_id))]
        except Exception:
            return []

    async def _refetch_via_message(self, comp: Component, event, trace: list):
        """用 `get_msg` 重取:napcat 会用当前 rkey 重新生成这条消息里的图片链接。"""
        call = self._resolve_call_action(event)
        obj = getattr(event, "message_obj", None)
        raw = getattr(obj, "raw_message", None)
        mid = getattr(obj, "message_id", None) or getattr(event, "message_id", None)
        if call is None or raw is None or not mid:
            trace.append("get_msg -> no message_id/raw_message")
            return None
        # 入参形态也要逐个试:不同实现对多余字段的容忍度不一样
        shapes: list[dict] = [{"message_id": str(mid)}]
        gid = getattr(obj, "group_id", None) or getattr(event, "group_id", None)
        sid = getattr(obj, "self_id", None)
        if gid is not None:
            try:
                shapes.append({"message_id": str(mid), "group_id": int(gid)})
            except (TypeError, ValueError):
                shapes.append({"message_id": str(mid), "group_id": str(gid)})
        if sid:
            shapes.append({"message_id": str(mid), "self_id": str(sid)})
        hint = (comp.meta or {}).get("raw_file") or comp.file_id
        hint = hint if isinstance(hint, str) else None
        old = (comp.url or "").strip()
        for params in shapes:
            try:
                resp = await call("get_msg", **params)
            except Exception as e:
                trace.append(f"get_msg {sorted(params)} -> {type(e).__name__}: {e}")
                continue
            segs = _segment_list(resp)
            urls = _media_urls_from_resp(resp, old=old, hint=hint)
            keys = sorted(resp.keys())[:8] if isinstance(resp, dict) else type(resp).__name__
            trace.append(
                f"get_msg {sorted(params)} -> {len(segs)} seg(s), {len(urls)} fresh url(s), keys={keys}"
            )
            for url in urls:
                dl = await self._download_fresh(comp, url)
                if dl is not None:
                    self._log(f"get_msg refreshed url for {comp.kind}: {url[:60]}...")
                    return dl
        return None

    @staticmethod
    def _resolve_call_action(event):
        """兼容 AstrBot 核心的取法:event.bot.api.call_action(aiocqhttp) 优先。"""
        bot = getattr(event, "bot", None)
        if bot is None:
            return None
        for obj in (getattr(bot, "api", None), bot):
            call = getattr(obj, "call_action", None)
            if callable(call):
                return call
        return None

    def _qq_bot(self):
        """aiocqhttp 的 CQHttp 客户端（自动转发要用真正的机器人账号）。"""
        try:
            platform = self.context.get_platform("aiocqhttp")
        except Exception:
            platform = None
        return getattr(platform, "bot", None)

    async def _auto_forward(self, rec: dict, rid: str,
                            ctx: SaveContext) -> list[str]:
        """把刚存的聊天记录转发到配置的目标群（v1.14.0 可选功能）.

        native 模式先试原地转发（保气泡），失败自动回退重建 —— 跨群原地
        并非 QQ 永远允许，回退不算错误，只记日志。
        """
        if not self.cfg.auto_forward_enabled:
            return []
        targets = [t for t in self.cfg.forward_targets_set()
                   if str(t) != str(ctx.group_id)]
        if not targets:
            return []
        bot = self._qq_bot()
        if bot is None:
            self._log("auto forward skipped: 没有 aiocqhttp 实例")
            return []
        from .warden import record_admin
        base = record_store.record_dir(self.cfg.storage_root, rid)
        done: list[str] = []
        for gid in targets:
            res = None
            if self.cfg.auto_forward_mode == "native":
                res = await record_admin.native_forward(
                    bot, gid, rec.get("root_msg_id") or "")
                if not res.get("ok"):
                    self._log(f"auto forward native failed for {gid}: "
                              f"{res.get('error')} — fallback rebuild")
                    res = None
            if res is None:
                res = await record_admin.rebuild_forward(bot, gid, rec, base)
            if res.get("ok"):
                done.append(f"{gid}({res.get('mode')})")
            else:
                self._log(f"auto forward failed for {gid}: {res.get('error')}")
        if done:
            self._log("auto forwarded: " + ", ".join(done))
        return done

    def _store_dl(self, comp: Component, ctx: SaveContext, dl,
                  *, reuse: bool = False) -> AssetResult:
        if isinstance(dl, DownloadError):
            return self._failed(comp, ctx, "download", f"download: {dl}",
                                dl, url=comp.url)
        try:
            sr = self._storage.save(dl.data, comp, ctx, mime=dl.mime)
        except (ValueError, OSError, RuntimeError) as e:
            return self._failed(comp, ctx, "save",
                                f"save: {type(e).__name__}: {e}", e)
        if self._index is not None:
            try:
                self._index.record(
                    platform=ctx.platform, group_id=ctx.group_id,
                    sender_id=ctx.sender_id, msg_id=ctx.msg_id, idx=ctx.idx,
                    kind=comp.kind, path=sr.path, size=sr.size,
                    sha16=sr.blake16,
                )
            except Exception as e:
                self._log(f"index record failed: {e!r}")
        preview = sr.path if comp.kind == "image" else None
        note = None
        origin = getattr(dl, "origin", None)
        if origin:
            note = f"链接已重取: {origin[:60]}"
        return AssetResult(
            kind=comp.kind, ok=True, path=sr.path,
            size=dl.size, preview_path=preview,
            reused=(True if reuse else sr.reused), note=note,
        )

    # ----------------- 内部:失败入库 / 索引复用 -----------------

    def _failed(self, comp: Component, ctx: SaveContext, stage: str,
                err: str, err_obj=None, *, url: Optional[str] = None,
                subject: Optional[str] = None,
                failure_kind: Optional[str] = None,
                kind: Optional[str] = None,
                row_idx: Optional[int] = None) -> AssetResult:
        """统一构造失败结果:失败原因分类 + 入索引库 + 写日志。

        kind / row_idx: 同一条消息里的第二个产物(转发渲染图)要按自己的 kind
        与 idx 入库(渲染图记 idx+100),不能借 comp.kind / ctx.idx。
        """
        out_kind = kind or comp.kind
        out_idx = row_idx if row_idx is not None else ctx.idx
        note = getattr(err_obj, "fallback_error", None)
        if self._index is not None:
            try:
                subj = subject or _failure_subject_for(comp)
                _id, attempts, last_ts = self._index.record_failure(
                    platform=ctx.platform, group_id=ctx.group_id,
                    sender_id=ctx.sender_id, msg_id=ctx.msg_id, idx=out_idx,
                    kind=out_kind, err=err, subject=subj, url=url,
                )
                if attempts > 1 and last_ts:
                    extra = (f"  [历史第 {attempts} 次,"
                             f"上次 {time.strftime('%m-%d %H:%M', time.localtime(last_ts))}]")
                    note = f"{note or ''}{extra}".strip()
            except Exception as e:
                self._log(f"failure record failed: {e!r}")
        self._log(f"{stage} failed for {out_kind}: {err[:200]}")
        return AssetResult(
            kind=out_kind, ok=False, err=err,
            failure_kind=failure_kind or classify_error(err),
            note=note,
        )

    def _find_saved_asset(self, msg_id: str, idx: int, *,
                          platform: Optional[str] = None,
                          group_id: Optional[str] = None) -> Optional[dict]:
        """找同一条消息同一 idx 已落盘的资产(用于复用,不再下载).

        v1.10: 带上 platform/group_id 过滤 —— 只按 msg_id 查会让不同群/不同平台的
        同名 msg_id 互相顶掉,复用出别人的文件。
        """
        if self._index is None or not msg_id or idx is None:
            return None
        if msg_id in ("nomsg", "nonemsg", "0"):
            return None
        try:
            rows = self._index.find_by_msg(
                msg_id, platform=platform, group_id=group_id
            )
        except Exception as e:
            self._log(f"index lookup failed: {e!r}")
            return None
        for r in rows:
            if r.get("idx") == idx:
                return r
        return None

    def _reuse_saved(self, comp: Component, ctx: SaveContext,
                     hit: dict) -> tuple[AssetResult, None]:
        """已有同 msg#idx 的落盘成果 -> 直接复用,跳过下载与落盘。"""
        path = hit.get("path")
        if path and not os.path.isfile(path):
            self._log(f"index hit but file missing: {path}")
            return (
                AssetResult(kind=comp.kind, ok=False,
                            err=f"reuse: stored file missing ({path})",
                            failure_kind="save",
                            note="索引命中但文件已不在磁盘"),
                None,
            )
        return (
            AssetResult(
                kind=comp.kind, ok=True, path=path,
                size=hit.get("size"), preview_path=path,
                reused=True, note="索引复用",
            ),
            None,
        )

    # ----------------- 内部:转发处理(同 v1.2) -----------------

    async def _forward_local_cached(self, ref):
        """转发节点内图片的⓪级补救：按 segment 的 `file`(MD5) 读 QQ 本地缓存。

        节点图以前只能靠节点内嵌 url（rkey 一过期就整图失败、不落盘）。节点里的
        image 段其实同样带 `file=<md5>.<ext>`，所以这里复用同一条本地缓存查找。
        没命中/没开补救/没有 file 字段时返回 None，调用方照旧走网络。
        """
        if not isinstance(ref, dict):
            return None
        name = ref.get("file")
        if not isinstance(name, str) or not name.strip():
            return None
        data, path, _ = await self._local_cache_read([name.strip()])
        if data is None:
            # 这是**正常**结果：NapCat 不下载转发节点里的图，所以本地没有副本。
            # 见 HANDOVER §1.-4「转发节点图为什么救不回来」。
            return None
        self._log(f"used QQ local cache for forward node image: {path}")
        return data.data

    async def _fetch_forward_nodes(self, comp: Component,
                                   event: AstrMessageEvent | None):
        """用 OneBot get_forward_msg 拉取合并转发的节点内容。

        返回 OneBot node 风格的 list, 失败返回 None。
        """
        if event is None:
            return None
        fwd_id = (comp.meta or {}).get("forward_id")
        if not fwd_id:
            fwd_id = self._forward_seg_id(comp.raw)
        if not fwd_id:
            # 从 raw_message 兑底找 forward id
            obj = getattr(event, "message_obj", None)
            raw = getattr(obj, "raw_message", None) if obj is not None else None
            segs = None
            if isinstance(raw, dict):
                segs = raw.get("message")
            if isinstance(segs, list):
                for seg in segs:
                    if isinstance(seg, dict):
                        seg_type = (seg.get("type") or "").lower()
                        if seg_type in ("node", "nodes", "forward"):
                            fwd_id = self._forward_seg_id(seg)
                        if fwd_id:
                            break
        if not fwd_id:
            self._log("forward fetch: no forward_id, fallback to JSON")
            return None
        bot = getattr(event, "bot", None)
        if bot is None:
            self._log("forward fetch: event has no bot (non-aiocqhttp?), fallback")
            return None
        call = getattr(bot, "call_action", None)
        if not callable(call):
            self._log("forward fetch: bot has no call_action, fallback")
            return None
        try:
            resp = await call("get_forward_msg", message_id=str(fwd_id))
        except Exception as e:
            try:
                resp = await call("get_forward_msg", id=str(fwd_id))
            except Exception as e2:
                self._log(f"forward fetch failed: {e!r} / {e2!r}")
                return None
        messages = None
        if isinstance(resp, dict):
            messages = resp.get("messages") or resp.get("message")
            data = resp.get("data")
            if messages is None and isinstance(data, dict):
                messages = data.get("messages") or data.get("message")
        if not isinstance(messages, list) or not messages:
            self._log(f"forward fetch: empty messages for id={fwd_id}")
            return None
        self._log(f"forward fetched: {len(messages)} nodes for id={fwd_id}")
        return messages

    @staticmethod
    def _forward_node_content(node: Any):
        if not isinstance(node, dict):
            return None
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        return (
            data.get("content")
            or node.get("content")
            or data.get("message")
            or node.get("message")
        )

    @staticmethod
    def _forward_seg_id(seg: Any):
        if isinstance(seg, dict):
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            return data.get("id") or data.get("resid") or data.get("res_id")
        if seg is None:
            return None
        for name in ("id", "resid", "res_id"):
            val = getattr(seg, name, None)
            if val:
                return val
        return None

    @staticmethod
    def _forward_seg_inline_nodes(seg: Any):
        if not isinstance(seg, dict):
            return None
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        nodes = (
            data.get("content")
            or seg.get("content")
            or data.get("message")
            or seg.get("message")
        )
        return nodes if isinstance(nodes, list) else None

    @staticmethod
    def _tag_forward_node_depth(node: Any, depth: int):
        if not isinstance(node, dict):
            return node
        tagged = dict(node)
        tagged["__warden_nested_depth"] = max(0, int(depth))
        return tagged

    async def _expand_forward_nodes(self, raw_nodes, event: AstrMessageEvent | None,
                                    *, depth: int = 0, max_depth: int = 2):
        """展开节点内容里嵌套的合并转发,最多展开 max_depth 层."""
        if not isinstance(raw_nodes, list):
            return raw_nodes
        if depth > max_depth:
            return raw_nodes
        can_expand_nested = depth < max_depth

        expanded = []
        for node in raw_nodes:
            expanded.append(self._tag_forward_node_depth(node, depth))
            if not can_expand_nested:
                continue
            content = self._forward_node_content(node)
            if not isinstance(content, list):
                continue
            for seg in content:
                if not isinstance(seg, dict):
                    continue
                seg_type = (seg.get("type") or "").lower()
                if seg_type not in ("node", "nodes", "forward"):
                    continue
                nested_nodes = self._forward_seg_inline_nodes(seg)
                if not nested_nodes:
                    fwd_id = self._forward_seg_id(seg)
                    if fwd_id:
                        nested_nodes = await self._fetch_forward_nodes(
                            Component(
                                kind="forward",
                                meta={"nodes": None, "forward_id": fwd_id},
                                raw=seg,
                            ),
                            event,
                        )
                if nested_nodes:
                    nested_nodes = await self._expand_forward_nodes(
                        nested_nodes, event, depth=depth + 1,
                        max_depth=max_depth,
                    )
                    expanded.extend(nested_nodes)
        if len(expanded) != len(raw_nodes):
            self._log(
                f"forward nested expanded: {len(raw_nodes)} -> {len(expanded)} "
                f"nodes (depth<={max_depth})"
            )
        return expanded

    async def _save_forward(self, comp: Component, ctx: SaveContext,
                            event: AstrMessageEvent | None = None
                            ) -> list[AssetResult]:
        from .warden.forwarder import from_component
        # napcat 等 OneBot 实现的合并转发只携带 forward id,
        # 节点内容不在消息体里, 需要用 get_forward_msg 主动拉取。
        inline_nodes = (comp.meta or {}).get("nodes")
        fetched = None
        if (
            (comp.meta or {}).get("forward_id")
            or self._forward_seg_id(comp.raw)
            or not inline_nodes
        ):
            fetched = await self._fetch_forward_nodes(comp, event)
        if fetched:
            if inline_nodes:
                self._log(
                    "forward fetch: replace inline nodes with fetched nodes"
                )
            comp.meta["nodes"] = fetched
        raw_nodes = (comp.meta or {}).get("nodes")
        if raw_nodes:
            comp.meta["nodes"] = await self._expand_forward_nodes(raw_nodes, event)
        nodes = from_component(comp)
        results: list[AssetResult] = []

        # v1.10: 节点内容真的空(既没有 inline 也没有 get_forward_msg 结果)时,
        # 落一个 nodes=null 的 JSON 只是"看起来成功"的垃圾 —— 明确报失败并入库。
        if not nodes:
            return [self._failed(
                comp, ctx, "forward",
                "forward: 未取到节点内容(get_forward_msg 为空 / 节点无内容)",
                url=comp.url,
                subject=f"forward:{ctx.msg_id}#{ctx.idx}",
                failure_kind="fetch",
            )]

        want_image = self.cfg.forward_render_mode in ("image", "both")
        want_json = self.cfg.forward_render_mode in ("json", "both")

        if want_image and (self._forwarder is None or not self._forwarder.can_handle(nodes)):
            if self._forwarder is None:
                self._log("forwarder unavailable — JSON only")
            elif nodes:
                self._log(f"forward too large ({len(nodes)} nodes) — JSON only")
            want_image = False

        # v1.8.0: 仅在需要渲染图片时,按平台白名单并发抓节点发送者头像,失败静默跳过
        if want_image and self.cfg.enable_user_avatar and self._avatar_cache is not None and nodes:
            avatar_platforms = {p.lower() for p in self.cfg.avatar_platforms}
            if (ctx.platform or "").lower() in avatar_platforms:
                try:
                    seen = set()
                    pairs = []
                    for nd in nodes:
                        key = (ctx.platform, nd.sender_id)
                        if nd.sender_id and key not in seen:
                            seen.add(key)
                            pairs.append(key)
                    if pairs:
                        avatar_map = await fetch_many_avatars(
                            self._avatar_cache, pairs,
                            concurrency=self.cfg.max_concurrent,
                        )
                        for nd in nodes:
                            nd.avatar_path = avatar_map.get((ctx.platform, nd.sender_id))
                except Exception as e:
                    self._log(f"avatar fetch failed: {e!r} — proceed without")

        # url -> 已下载字节:渲染与存档共用(声明在 if 之外,存档要用到)
        blob_by_url: dict[str, bytes] = {}
        if want_image and nodes:
            # 节点内图片也并发下载
            all_urls: list[str] = []
            all_refs: list[dict] = []  # 与 all_urls 一一对应:该图的 segment data(file/url/...)
            url_to_node: list[int] = []  # 第 N 个 url 对应第几个 node
            for ni, nd in enumerate(nodes):
                refs = getattr(nd, "image_refs", None) or [
                    {"url": u} for u in nd.image_urls
                ]
                for u in nd.image_urls:
                    all_urls.append(u)
                    url_to_node.append(ni)
                for ref in refs:
                    all_refs.append(ref if isinstance(ref, dict) else {"url": ref})
            node_imgs: dict[int, list[bytes]] = {ni: [] for ni in range(len(nodes))}
            failed_urls = 0
            if all_urls:
                sem = asyncio.Semaphore(self.cfg.max_concurrent)

                async def _fetch(url: str, ref: Optional[dict] = None) -> bytes:
                    async with sem:
                        # ⓪ QQ 本地缓存(节点图也有 file=MD5 时直接读盘,不碰网络)
                        cached = await self._forward_local_cached(ref)
                        if cached is not None:
                            return cached
                        hi = _upgrade_qq_image_url(url)
                        candidates = [hi, url] if hi != url else [url]
                        last: Optional[DownloadError] = None
                        for ci, cand in enumerate(candidates):
                            try:
                                dl = await download(
                                    Component(kind="image", url=cand),
                                    fetcher=aiohttp_fetcher,
                                    retries=self.cfg.download_retries,
                                    timeout_s=self.cfg.download_timeout_s,
                                    max_bytes=self.cfg.max_file_size_bytes,
                                )
                                return dl.data
                            except DownloadError as e:
                                last = e
                                # v1.10.0: 本行起不再「用完即删」 ---- 候选序号/完整 url/败因是区分「url 死了」与「请求姿势不对」的唯一线索
                                self._log(
                                    f"fwd dl candidate {ci + 1}/{len(candidates)} "
                                    f"failed: {e} | url={cand}"
                                )
                        # 等客户端落盘后再查一次本地缓存(见 _LATE_CACHE_DELAYS_S)
                        ref_name = ref.get("file") if isinstance(ref, dict) else ref
                        cached = await self._late_cache(
                            lambda: self._forward_local_cached(ref),
                            f"fwd late local cache ({_short(ref_name, 40)})",
                        )
                        if cached is not None:
                            return cached
                        raise last if last is not None else DownloadError(
                            f"no candidate url for {url!r}")

                # 并发拉
                results_dl = await asyncio.gather(
                    *(_fetch(u, all_refs[pos] if pos < len(all_refs) else None)
                      for pos, u in enumerate(all_urls)),
                    return_exceptions=True,
                )
                for pos, r in enumerate(results_dl):
                    if isinstance(r, BaseException):
                        failed_urls += 1
                        continue
                    ni = url_to_node[pos]
                    node_imgs[ni].append(r)
                    blob_by_url[all_urls[pos]] = r

        # v1.14.0: 一个条目 = record.json(结构化) + render.png(渲染) + assets/,
        # 两种显示模式共用一个目录(聊天记录与纯文本同理)。
        # 存档用的图片字节就是上面渲染下载的那一批,不再重复拉一遍网络。
        rid = record_store.make_record_id(ctx.group_id, ctx.msg_id, ctx.ts)
        archived: Optional[dict] = None
        raw_nodes_for_archive = (comp.meta or {}).get("nodes") or []
        if raw_nodes_for_archive:
            async def _archive_image(data, _blobs=blob_by_url):
                ref = record_store.image_ref(data)
                return _blobs.get(ref) if ref else None
            try:
                archived = await record_store.capture(
                    raw_nodes_for_archive,
                    platform=ctx.platform, group_id=ctx.group_id,
                    msg_id=ctx.msg_id, storage_root=self.cfg.storage_root,
                    fetch_image=_archive_image, ts=ctx.ts, record_id=rid,
                )
            except Exception as e:
                # 存档失败不该让整条消息判失败 —— 渲染图/JSON 仍能救场
                self._log(f"forward archive failed: {type(e).__name__}: {e!r}")

        if want_json or not want_image:
            comp_json = Component(
                kind="forward_json", name=f"forward_{ctx.msg_id}_{ctx.idx}.json",
                raw=comp.raw,
            )
            try:
                payload = json.dumps(
                    {"forward": True, "node_count": len(nodes),
                     "nodes": (comp.meta or {}).get("nodes")},
                    ensure_ascii=False, indent=2,
                )
                sr = self._storage.save(payload.encode("utf-8"), comp_json, ctx,
                                         mime="application/json")
            except (TypeError, ValueError, OSError, RuntimeError) as e:
                # v1.10: 以前只 catch ValueError —— json.dumps 的 TypeError、
                # Storage.save 的 OSError 都会逃出去,整条消息没有回执。
                results.append(self._failed(
                    comp, ctx, "json",
                    f"json save: {type(e).__name__}: {e}", e,
                    subject=f"forward_json:{ctx.msg_id}#{ctx.idx}",
                    failure_kind="save",
                ))
            else:
                if self._index is not None:
                    try:
                        self._index.record(
                            platform=ctx.platform, group_id=ctx.group_id,
                            sender_id=ctx.sender_id, msg_id=ctx.msg_id, idx=ctx.idx,
                            kind="forward", path=sr.path, size=sr.size,
                            sha16=sr.blake16, forward_meta={"node_count": len(nodes)},
                        )
                    except Exception as e:
                        self._log(f"index record failed: {e!r}")
                results.append(AssetResult(kind="forward", ok=True,
                                           path=sr.path, size=len(payload),
                                           reused=sr.reused))

        if want_image and nodes:
            if failed_urls:
                # 节点内有图片全部重试后仍失败:整图判失败,不落盘
                self._log(
                    f"forward render aborted: {failed_urls}/{len(all_urls)} "
                    f"node image(s) failed to download"
                )
                # v1.11.0: 这类失败以前绕过 _failed —— 不进 failures 表、没有 subject,
                # 与 README 的"失败分类入库"自相矛盾,/warden fail 也查不到。
                results.append(self._failed(
                    comp, ctx, "forward_image",
                    f"image download failed ({failed_urls}/{len(all_urls)})",
                    subject=f"forward_render:{ctx.msg_id}#{ctx.idx + 100}",
                    failure_kind="fetch", kind="forward_render",
                    row_idx=ctx.idx + 100,
                ))
            else:
                try:
                    # render 仍走原来的 PIL 路径,但喂预下载的 bytes
                    png = await self._forwarder.render_with_images(
                        nodes, node_imgs=node_imgs,
                    )
                    comp_png = Component(
                        kind="forward_image", name=f"forward_{ctx.msg_id}_{ctx.idx}.png",
                        raw=comp.raw,
                    )
                    sr = self._storage.save(png, comp_png, ctx, mime="image/png")
                    if archived is not None:
                        try:
                            record_store.save_render(self.cfg.storage_root, rid, png)
                        except OSError as e:
                            self._log(f"entry render write failed: {e!r}")
                    if self._index is not None:
                        try:
                            self._index.record(
                                platform=ctx.platform, group_id=ctx.group_id,
                                sender_id=ctx.sender_id, msg_id=ctx.msg_id,
                                idx=ctx.idx + 100,
                                kind="forward_render", path=sr.path, size=sr.size,
                                sha16=sr.blake16,
                            )
                        except Exception as e:
                            self._log(f"index record failed: {e!r}")
                    results.append(AssetResult(
                        kind="forward_render", ok=True, path=sr.path, size=sr.size,
                        preview_path=sr.path, reused=sr.reused,
                    ))
                except Exception as e:
                    results.append(self._failed(
                        comp, ctx, "forward_image", f"render: {e}", e,
                        subject=f"forward_render:{ctx.msg_id}#{ctx.idx + 100}",
                        failure_kind="save", kind="forward_render",
                        row_idx=ctx.idx + 100,
                    ))

        # v1.14.0: 可选的自动转发（目标群为空/开关关掉时什么都不做）
        if archived is not None and results:
            try:
                await self._auto_forward(archived, rid, ctx)
            except Exception as e:
                self._log(f"auto forward error: {type(e).__name__}: {e!r}")

        if not results:
            results.append(AssetResult(
                kind="forward", ok=False, err="empty forward or no mode enabled",
            ))
        return results

    # ----------------- 内部:预览回传(同 v1.2) -----------------

    def _build_previews(self, event: AstrMessageEvent, batch: BatchResult):
        if not self.cfg.reply_preview:
            return
        candidate = None
        for x in batch.items:
            if x.ok and x.kind == "forward_render" and x.path:
                candidate = x
                break
        if candidate is None:
            for x in batch.items:
                if x.ok and x.kind == "image" and x.path:
                    candidate = x
                    break
        if candidate is None:
            return
        path = candidate.path
        if not os.path.exists(path):
            return
        img_fn = getattr(event, "image_result", None)
        if callable(img_fn):
            try:
                yield event.image_result(path)
                return
            except Exception as e:
                self._log(f"image_result failed: {e!r} — fall back to text")
        try:
            yield event.plain_result(f"预览: {path}")
        except Exception:
            pass

    def _resolve_export_path(self, arg: str) -> str:
        """把 export 参数解析为 storage_root 内的绝对路径,越界抛 ValueError."""
        root = self._storage.root if self._storage is not None else os.path.abspath(self.cfg.storage_root)
        if not arg.strip():
            return os.path.join(root, "_export.json")
        raw = arg.strip()
        if not os.path.isabs(raw):
            raw = os.path.join(root, raw)
        real = os.path.realpath(raw)
        root_real = os.path.realpath(root)
        if real != root_real and not real.startswith(root_real + os.sep):
            raise ValueError(f"export path escapes storage root: {raw!r}")
        return real

    # ----------------- 命令 -----------------

    @filter.command("warden list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_list(self, event: AstrMessageEvent, arg: str = ""):
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        try:
            n = int(arg.strip()) if arg.strip() else 10
        except ValueError:
            n = 10
        n = max(1, min(n, 50))
        rows = self._index.recent(n)
        if not rows:
            yield event.plain_result("[Warden] (空索引)")
            return
        # v1.11.0: list 以前自己拼一行,不带 sha16 —— 而 /warden lookup <sha16>
        # 正需要这个值。现在与 lookup 共用 _format_asset_row。
        lines = [f"## 最近 {len(rows)} 条"]
        lines.extend(_format_asset_row(r) for r in rows)
        yield event.plain_result("\n".join(lines))

    @filter.command("warden stats")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_stats(self, event: AstrMessageEvent):
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        s = self._index.stats()
        yield event.plain_result(
            "## warden 统计\n"
            f"  assets: {s.get('total', 0)}\n"
            f"  by_kind: {s.get('by_kind', {})}\n"
            f"  total_bytes: {s.get('total_bytes', 0)}\n"
            f"  failures: {s.get('failures', 0)}\n"
            f"  failures_by_kind: {s.get('failures_by_kind', {})}\n"
            f"  db: {s.get('db_path', '?')}"
        )

    @filter.command("warden fail")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_failures(self, event: AstrMessageEvent, arg: str = ""):
        """用法: /warden fail [n]   最近 n 条保存失败记录(默认 10, 上限 50)"""
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        try:
            n = int(arg.strip()) if arg.strip() else 10
        except ValueError:
            n = 10
        n = max(1, min(n, 50))
        rows = self._index.recent_failures(n)
        if not rows:
            yield event.plain_result("[Warden] (没有失败记录)")
            return
        lines = [f"## 最近 {len(rows)} 条保存失败"]
        # v1.10: failure_stats 之前是死代码,现在把"反复失败的都是谁"露出来
        try:
            st = self._index.failure_stats()
        except Exception as e:
            st = {}
            self._log(f"failure_stats failed: {e!r}")
        if st.get("total"):
            lines.append(f"累计 {st['total']} 次失败")
            top = [x for x in st.get("top", []) if x.get("count", 0) > 1]
            if top:
                lines.append("  反复失败:")
                for x in top:
                    lines.append(f"    - {x['subject']} × {x['count']}")
        for r in rows:
            ts = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
            lines.append(
                f"- [{ts}] {r['kind']} msg={r['msg_id']}#{r['idx']} "
                f"attempts={r.get('attempts')} subject={r.get('subject')}\n"
                f"    {r.get('err')}"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("warden export")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_export(self, event: AstrMessageEvent, arg: str = ""):
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        try:
            path = self._resolve_export_path(arg)
        except ValueError as e:
            yield event.plain_result(f"[Warden] 导出路径非法: {e}")
            return
        try:
            n = self._index.export_json(path)
            yield event.plain_result(
                f"[Warden] 已导出 {n} 条 -> {path}"
            )
        except Exception as e:
            yield event.plain_result(f"[Warden] 导出失败: {e!r}")

    @filter.command("warden lookup")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_lookup(self, event: AstrMessageEvent, arg: str = ""):
        """用法: /warden lookup <msg_id>  (可选加 #<idx>)"""
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        token = arg.strip()
        if not token:
            yield event.plain_result("usage: /warden lookup <msg_id> [#idx]")
            return
        # 解析 msg_id#idx
        msg_id = token
        idx_filter: Optional[int] = None
        if "#" in token:
            msg_id, idx_s = token.split("#", 1)
            try:
                idx_filter = int(idx_s)
            except ValueError:
                yield event.plain_result(f"[Warden] 非法 idx: {idx_s!r}")
                return
        # v1.10: 16 位十六进制按内容 sha 反查(「这张图还在别处出现过吗」)
        if idx_filter is None and _is_sha16(msg_id):
            rows = self._index.find_by_sha(msg_id.lower())
            if rows:
                lines = [f"## sha16={msg_id} 出现 {len(rows)} 次"]
                lines.extend(_format_asset_row(r) for r in rows)
                yield event.plain_result("\n".join(lines))
                return
            # v1.11.0: 没命中内容指纹时继续按 msg_id 查 —— 恰好是 16 位十六进制的
            # msg_id 以前会被直接判"未找到",永远查不到。
        rows = self._index.find_by_msg(msg_id)
        if idx_filter is not None:
            rows = [r for r in rows if r["idx"] == idx_filter]
        if not rows:
            yield event.plain_result(f"[Warden] 未找到 msg_id={msg_id} 的资产")
            return
        lines = [f"## lookup msg_id={msg_id} ({len(rows)} 条)"]
        lines.extend(_format_asset_row(r) for r in rows)
        yield event.plain_result("\n".join(lines))

    @filter.command("warden prune")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_prune(self, event: AstrMessageEvent, arg: str = ""):
        """用法: /warden prune <days>   删除索引里 N 天前的记录(不删文件)"""
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        try:
            days = int(arg.strip())
        except ValueError:
            yield event.plain_result("usage: /warden prune <days>")
            return
        if days <= 0:
            yield event.plain_result("[Warden] days 必须 > 0")
            return
        cutoff = int(time.time()) - days * 86400
        try:
            n = self._index.prune_older_than(cutoff)
            yield event.plain_result(
                f"[Warden] 已删除 {n['assets']} 条索引记录"
                f"（另有 {n['failures']} 条失败记录）(> {days} 天前)"
            )
        except Exception as e:
            yield event.plain_result(f"[Warden] prune 失败: {e!r}")

    # ----------------- 聊天记录存档(原型) -----------------

    def _records_root(self) -> str:
        """存档目录：跟资产同一个 storage_root（_records/ 是它的子目录）。"""
        root = getattr(self._storage, "root", None) or self.cfg.storage_root
        return str(root)

    def _forward_component(self, event: AstrMessageEvent) -> Optional[Component]:
        """当前消息里的第一个合并转发组件（没有就 None）。"""
        try:
            comps = extract_components(event)
        except Exception:
            return None
        for comp in comps:
            if comp.is_forward:
                return comp
        return None

    async def _record_fetch_image(self, data: dict) -> Optional[bytes]:
        """节点图字节的获取链：QQ 本地缓存 → url 升级 → 直连下载。

        与 on_group_message 里的节点图下载同一条链，但失败只返回 None ——
        存档不允许因为单张图炸掉整条记录（record 里会记进 missing）。
        """
        ref = data if isinstance(data, dict) else {}
        try:
            cached = await self._forward_local_cached(ref)
        except Exception:
            cached = None
        if cached is not None:
            return cached
        url = record_store.image_ref(ref)
        if not url:
            return None
        hi = _upgrade_qq_image_url(url)
        candidates = [hi, url] if hi != url else [url]
        for cand in candidates:
            try:
                dl = await download(
                    Component(kind="image", url=cand),
                    fetcher=aiohttp_fetcher,
                    retries=self.cfg.download_retries,
                    timeout_s=self.cfg.download_timeout_s,
                    max_bytes=self.cfg.max_file_size_bytes,
                )
                return dl.data
            except Exception:
                continue
        return None

    async def _resolve_forward_nodes(self, comp: Component,
                                    event: AstrMessageEvent) -> Optional[list]:
        """拿节点内容：优先 get_forward_msg（最新最全），失败退回组件里的快照。"""
        try:
            nodes = await self._fetch_forward_nodes(comp, event)
        except Exception:
            nodes = None
        if nodes:
            return nodes
        cached = (comp.meta or {}).get("nodes")
        if isinstance(cached, list) and cached:
            return cached
        return None

    def _reply_message_id(self, event: AstrMessageEvent) -> str:
        """被引用消息的 message_id（没有引用就空串）。

        回复一条合并转发时，当前消息里只有 reply 段，真正的转发是被引用的那条 ——
        用它的 message_id 直接 get_forward_msg 就能拿到节点（QQ 的 reply id 就是
        被引用消息的 OneBot message_id）。
        """
        obj = getattr(event, "message_obj", None)
        raw = getattr(obj, "raw_message", None) if obj is not None else None
        segs = raw.get("message") if isinstance(raw, dict) else None
        if isinstance(segs, list):
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                if (seg.get("type") or "").lower() != "reply":
                    continue
                data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
                rid = data.get("id")
                if rid:
                    return str(rid)
        # 兜底: 组件层（raw_message 结构不同时）
        try:
            comps = extract_components(event)
        except Exception:
            comps = []
        for comp in comps:
            if (comp.meta or {}).get("type") != "reply":
                continue
            raw_seg = comp.raw if isinstance(comp.raw, dict) else {}
            data = raw_seg.get("data") if isinstance(raw_seg.get("data"), dict) else {}
            if data.get("id"):
                return str(data["id"])
        return ""

    async def _fetch_forward_by_id(self, message_id: str,
                                   event: AstrMessageEvent) -> Optional[list]:
        """用 message_id 直接 get_forward_msg（被引用那条的情形）。"""
        if not message_id:
            return None
        synthetic = Component(kind="forward",
                              meta={"forward_id": str(message_id)})
        try:
            return await self._fetch_forward_nodes(synthetic, event)
        except Exception as e:
            self._log(f"record: get_forward_msg({message_id}) failed: {e!r}")
            return None

    async def _fetch_forward_via_msg(self, message_id: str,
                                     event: AstrMessageEvent) -> tuple:
        """引用到的是一条普通消息时：get_msg 里找 forward 段再取。

        返回 (nodes, forward_id)。forward_id 必须留下来当存档的原消息 id ——
        否则「原地转发」会拿命令消息自己的 id 去转发，什么也转不出来。
        """
        bot = getattr(event, "bot", None)
        call = getattr(bot, "call_action", None) if bot is not None else None
        if call is None:
            return None, ""
        try:
            res = await call("get_msg", message_id=str(message_id))
        except Exception as e:
            self._log(f"record: get_msg({message_id}) failed: {e!r}")
            return None, ""
        inner = res
        if (isinstance(inner, dict) and isinstance(inner.get("data"), dict)
                and "message" not in inner):
            inner = inner["data"]
        segs = inner.get("message") if isinstance(inner, dict) else None
        if not isinstance(segs, list):
            return None, ""
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            if (seg.get("type") or "").lower() in ("forward", "node", "nodes"):
                fid = self._forward_seg_id(seg)
                if fid:
                    nodes = await self._fetch_forward_by_id(fid, event)
                    if nodes:
                        return nodes, str(fid)
        return None, ""

    async def _record_target_nodes(self, event: AstrMessageEvent,
                                   arg: str = "") -> tuple:
        """定位要存档的合并转发节点。

        顺序：命令行给的 msg_id → 当前消息自带的 forward 段 → 引用目标的 message_id
        （先当合并转发取，再当普通消息 get_msg 找里面的 forward 段）。
        返回 (nodes, hint, forward_id)：nodes 为空时 hint 是回给用户的一句话；
        forward_id 是「那条合并转发」的 message_id（原地转发要用它，不能拿命令自己的 id）。
        """
        arg = (arg or "").strip()
        if arg.isdigit():
            nodes = await self._fetch_forward_by_id(arg, event)
            if nodes:
                return nodes, "", arg
            return None, f"msg_id={arg} 取不到合并转发节点", ""
        comp = self._forward_component(event)
        if comp is not None:
            nodes = await self._resolve_forward_nodes(comp, event)
            if nodes:
                # 直接发来的合并转发本身就是一个消息: 它的 message_id 才是
                # forward_group_single_msg 认的那种 id（段里的是长 msgId, 不认）
                fid = (getattr(event, "message_id", None) or "")
                return nodes, "", str(fid)
        rid = self._reply_message_id(event)
        if not rid:
            return None, "请【引用】那条合并转发再发命令", ""
        nodes = await self._fetch_forward_by_id(rid, event)
        if nodes:
            return nodes, "", rid
        nodes, _inner_fid = await self._fetch_forward_via_msg(rid, event)
        if nodes:
            # 嵌套转发只有长 msgId 可用, 短 id 拿不到 -> 存空, 原地转发会明确拒绝
            return nodes, "", ""
        return None, f"引用消息 {rid} 不是合并转发（或 NapCat 取不到内容）", ""

    @filter.command("warden 存档")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_record(self, event: AstrMessageEvent, arg: str = ""):
        """用法: 回复一条合并转发 + /warden 存档 —— 把节点和节点内图片落成自包含目录。

        与转发落盘（sidecar JSON）的区别：那个 JSON 里的图还是死的 url，这个把图片
        字节一起存进 assets/，重发时不再依赖 rkey。
        """
        nodes, hint, forward_id = await self._record_target_nodes(event, arg)
        if not nodes:
            yield event.plain_result(f"[Warden] {hint}")
            return
        msg_obj = getattr(event, "message_obj", None)
        # 存档的「原消息 id」必须是那条合并转发自己的 id —— 重发 原地 要拿它去转发
        msg_id = (str(forward_id or "").strip()
                  or getattr(event, "message_id", None)
                  or getattr(msg_obj, "message_id", None) or "")
        root = self._records_root()
        try:
            rec = await record_store.capture(
                nodes,
                platform=(event_platform(event) or ""),
                group_id=(event_group_id(event) or ""),
                msg_id=msg_id,
                storage_root=root,
                fetch_image=self._record_fetch_image,
            )
        except Exception as e:
            self._log(f"record capture failed: {e!r}")
            yield event.plain_result(f"[Warden] 存档失败: {type(e).__name__}: {e}")
            return
        miss = len(rec.get("missing") or [])
        text = (f"[Warden] 已存档 {rec['record_id']}\n"
                f"  节点 {rec['node_count']} / 图片 {len(rec.get('assets') or [])}")
        if miss:
            text += f" / 缺失 {miss}"
        text += f"\n  目录: {record_store.record_dir(root, rec['record_id'])}"
        yield event.plain_result(text)

    @filter.command("warden 重发")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_warden_resend(self, event: AstrMessageEvent, arg: str = ""):
        """用法: /warden 重发 [原地] [record_id|list]

        默认: 用存档节点重建合并转发（图片来自 assets/，不依赖 rkey）。
        原地: 让 QQ 自己把原消息再转发一遍（forward_group_single_msg），
              渲染交给客户端 —— 理论上能保住原消息的装扮。
        """
        root = self._records_root()
        tokens = [t for t in (arg or "").split() if t]
        mode = "auto"
        if tokens and tokens[0] in ("原地", "native"):
            mode = "native"
            tokens = tokens[1:]
        elif tokens and tokens[0] in ("重建", "rebuild"):
            mode = "rebuild"
            tokens = tokens[1:]
        arg = tokens[0] if tokens else ""
        if arg in ("list", "ls"):
            rows = record_store.list_records(root, 10)
            if not rows:
                yield event.plain_result("[Warden] 还没有存档")
                return
            lines = ["## 存档(最近 10 条)"]
            for r in rows:
                stamp = time.strftime("%m-%d %H:%M",
                                      time.localtime(r["captured_ts"]))
                lines.append(f"{r['record_id']}  {stamp}  "
                             f"节点{r['node_count']}  图{r['asset_count']}")
            yield event.plain_result("\n".join(lines))
            return
        if not arg:
            rows = record_store.list_records(root, 1)
            if not rows:
                yield event.plain_result("[Warden] 还没有存档")
                return
            arg = rows[0]["record_id"]
        rec = record_store.load_record(root, arg)
        if not rec:
            yield event.plain_result(f"[Warden] 找不到存档 {arg}")
            return
        fallback_note = ""
        if mode in ("auto", "native"):
            ok, why = await self._try_resend_native(event, rec)
            if ok:
                yield event.plain_result(why)
                return
            if mode == "native":
                yield event.plain_result(why)
                return
            fallback_note = why
        try:
            from astrbot.api.message_components import Nodes
        except ImportError:
            yield event.plain_result("[Warden] 当前 AstrBot 没有 Nodes 组件, 发不了合并转发")
            return
        try:
            nodes = record_store.build_nodes(rec, record_store.record_dir(root, arg))
        except Exception as e:
            yield event.plain_result(f"[Warden] 还原失败: {type(e).__name__}: {e}")
            return
        if not nodes:
            yield event.plain_result(f"[Warden] 存档 {arg} 里没有可重发的节点")
            return
        self._log(f"resend record {arg}: {len(nodes)} nodes")
        if fallback_note:
            yield event.plain_result(
                f"[Warden] 原地转发不可用（{fallback_note}），改用存档重建")
        yield event.chain_result([Nodes(nodes)])

    async def _try_resend_native(self, event: AstrMessageEvent,
                                 rec: dict) -> tuple:
        """原地转发：让 QQ 把 root_msg_id 那条原消息再转发一遍。

        返回 (ok, text)。与重建节点的区别：消息不经过我们，是 QQ 客户端把「那条
        消息」重新转发一次，因此发送者/气泡装扮/挂件/头像全都是原样 —— 2026-10-27
        实测确认（重建节点则只剩纯文本+图片）。
        """
        rid = rec.get("record_id") or "?"
        target = str(rec.get("root_msg_id") or "").strip()
        if not target.isdigit():
            return False, f"存档 {rid} 没有原消息 id（重建才是它的强项）"
        group_id = str(event_group_id(event) or rec.get("group_id") or "").strip()
        bot = getattr(event, "bot", None)
        call = getattr(bot, "call_action", None) if bot is not None else None
        if call is None:
            return False, "当前平台没有 call_action, 不支持原地转发"
        # 先核验 root_msg_id 真的指向一条合并转发：v1.12.0 之前的存档把「命令消息」
        # 的 id 存了进来，直接转发会转发出「/warden 存档」那句话本身。
        is_forward = False
        try:
            probe = await call("get_msg", message_id=int(target))
        except Exception as e:
            return False, f"取不到原消息 {target}: {type(e).__name__}: {e}"
        inner = probe
        if (isinstance(inner, dict) and isinstance(inner.get("data"), dict)
                and "message" not in inner):
            inner = inner["data"]
        segs = inner.get("message") if isinstance(inner, dict) else None
        for seg in segs if isinstance(segs, list) else []:
            if not isinstance(seg, dict):
                continue
            if (seg.get("type") or "").lower() in ("forward", "node", "nodes"):
                is_forward = True
                break
        if not is_forward:
            return False, (
                f"存档 {rid} 的 root_msg_id={target} 不是合并转发"
                "（v1.12.0 之前的存档存的是命令消息 id）")
        # 注意: forward_group_single_msg 只认 OneBot 层的短 message_id(1481691615 这种),
        # 不认段里的长 msgId(7701368446137848000) —— 拿长 id 去会回
        # "无法找到消息..."。所以这里转发的就是 target 本身。
        try:
            res = await call("forward_group_single_msg",
                             group_id=group_id, message_id=int(target))
        except Exception as e:
            return False, f"原地转发失败: {type(e).__name__}: {e}"
        self._log(f"resend {rid} native: message_id={target} -> {res!r}")
        if isinstance(res, dict) and str(res.get("status") or "").lower() == "failed":
            return False, f"原地转发失败: {res.get('message') or res}"
        return True, (
            f"[Warden] 已让 QQ 原地转发原消息 {target}"
            "（客户端渲染, 装扮/挂件/头像原样保留）")

    # ----------------- 工具 -----------------

    def _log(self, msg: str) -> None:
        if self.cfg.log_to_stdout:
            print(f"[media-warden] {msg}")


__all__ = ["MediaWardenStar"]
