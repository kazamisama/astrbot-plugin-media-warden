"""astrbot-plugin-media-warden 入口.

v1.3 增量:
  - /warden lookup <msg_id> 反向查询
  - 并发下载多组件
  - 配置项 retries / max_concurrent

保留 v1.2: forwarder / index / 命令 / 预览回传
"""
from __future__ import annotations
import asyncio
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
    summarize,
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
    download, download_many, DownloadError, Downloaded, _local_fallback,
    _read_local_file,
)
from .warden.storage import Storage, SaveContext
from .warden.index import failure_subject


def _local_cache_candidates(comp: Component) -> list:
    """本地缓存查找要试的候选名（QQ 缓存文件名 = 消息段里的 `<md5>.<ext>`）。

    顺序：消息段原始 `file`（`meta["raw_file"]`）→ `comp.file_id` → `meta["path"]`
    的文件名 → url 的最后一段。去重、保序、丢掉空值与非字符串。
    """
    import os as _os
    out: list = []
    meta = comp.meta or {}
    for v in (meta.get("raw_file"), comp.file_id, meta.get("path")):
        if isinstance(v, str) and v.strip():
            out.append(_os.path.basename(v.strip()))
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


def _get_media_url_candidates(resp) -> list[str]:
    """从 OneBot get_image 的返回里提取可用 url（兼容多种实现形态）。"""
    if resp is None:
        return []
    data = resp
    if isinstance(resp, dict) and isinstance(resp.get("data"), (dict, str)):
        data = resp["data"]
    out: list[str] = []
    if isinstance(data, dict):
        for key in ("url", "file", "file_url", "path"):
            v = data.get(key)
            if isinstance(v, str) and v:
                out.append(v)
    elif isinstance(data, str):
        out.append(data)
    else:
        for key in ("url", "file", "file_url", "path"):
            v = getattr(data, key, None)
            if isinstance(v, str) and v:
                out.append(v)
    if isinstance(resp, dict):
        for key in ("url", "file", "file_url"):
            v = resp.get(key)
            if isinstance(v, str) and v:
                out.append(v)
    seen: set = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


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


def _failure_subject_for(comp: Component) -> str:
    """稳定去重键:优先 fileid（rkey 换了也能对上同一张图）,否则 host+path。"""
    fid = (comp.meta or {}).get("fileid") or comp.file_id or ""
    if fid and "://" not in str(fid):
        return f"fileid:{fid}"
    return failure_subject(comp.url)


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
    "群聊素材守门人 v1.9.6: 监听特定群/用户的非文字消息,按模板落盘,转发链 PIL 渲染/JSON 保存,并发下载 + 指数退避重试 + 源链接失效补救(QQ 本地缓存按 MD5(含转发节点图) / OneBot get_image 多形态重取 + get_msg 重解析 / 组件本地副本), 失败入库可查, /warden lookup 反查 + 预览回传, 命令仅管理员可用",
    VERSION,
)
class MediaWardenStar(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.cfg, self._config_errors = MediaWardenConfig.from_raw_lenient(config or {})
        if self._config_errors:
            print(f"[media-warden] config warnings: {self._config_errors}")
        self._storage: Optional[Storage] = None
        self._forwarder = None
        self._index = None
        self._avatar_cache = None
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
        try:
            from .warden.forwarder import Forwarder
            self._forwarder = Forwarder(
                width=self.cfg.forward_image_width,
                max_nodes=self.cfg.forward_render_max_nodes,
            )
        except ImportError as e:
            self._log(f"forwarder init failed: {e!r} — falls back to JSON sidecar")
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
        self._log(
            f"initialize: storage={self._storage.root} "
            f"forwarder={'on' if self._forwarder else 'off'} "
            f"index={'on' if self._index else 'off'} "
            f"avatars={'on' if (self.cfg.enable_user_avatar and self._avatar_cache) else 'off'}"
        )
        return None

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
        media = [c for c in components if c.is_media]
        forwards = [c for c in components if c.is_forward]
        if not media and not forwards:
            if self.cfg.log_to_stdout:
                kinds = summarize(components)
                self._log(
                    f"skip: text-only | msg={getattr(event, 'message_id', '?')} "
                    f"| segments={kinds}"
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
                hit = self._find_saved_asset(msg_id, i)
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
        text = format_batch(batch)
        yield event.plain_result(text)
        for extra in self._build_previews(event, batch):
            yield extra
        if self.cfg.at_on_save_failure and batch.fail_count > 0:
            try:
                yield event.chain_result([
                    At(qq=sender_id),
                    Plain(" 有 " + batch.failure_phrase()),
                ])
            except Exception as e:
                self._log(f"at-on-failure notify failed: {e!r}")

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
        cursor: dict[str, int] = defaultdict(int)
        for comp in media:
            lst = buckets.get(comp.kind) or []
            i = cursor[comp.kind]
            if i >= len(lst):
                continue
            cursor[comp.kind] = i + 1
            ref = lst[i]
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

        v1.9: 每个组件按「原 url 重试 -> OneBot get_image 重取新链接 -> 组件本地副本」
        三级回退;三级都失败才记失败(入索引库 + 写日志)。
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

    async def _cached_copy(self, comp: Component):
        """查 QQ 本地图片缓存(按消息段里的 MD5)。命中就直接用,不走网络。

        QQ 收到图片时已经按 MD5 存了副本，而消息段的 `data.file` 就是那个 MD5。
        链接因 rkey 过期返回 400 时，本地这份是唯一还能拿到的来源 —— 所以放在
        **最前面**：有就不必再等一次注定失败的 http。
        """
        if not self.cfg.get_media_fallback:
            return None
        cands = _local_cache_candidates(comp)
        if not cands:
            return None
        try:
            from .warden.localcache import find_cached_image as _find  # 局部导入便于测试打桩
        except Exception as e:  # noqa: BLE001
            self._log(f"localcache module unavailable: {e!r}")
            return None
        misses = []
        for cand in cands:
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
            data.origin = f"localcache:{path}"
            self._log(f"used QQ local cache for {comp.kind}: {path}")
            return data
        self._local_cache_misses = misses
        return None

    async def _download_one(self, comp: Component, event=None):
        """单个组件的下载回退链;全失败时抛首个(最对症的)下载错误。

        顺序:
        ⓪ QQ 本地缓存(按消息段 MD5) —— 命中即用,不碰网络
        ① 正常 http / 组件 fetcher(内部含 raw 本地副本回退)
        ② 向 bot 重取(get_image / get_msg) —— 只对「消息段里有新 url」的情况有效
        ③ 组件本地副本(temp 缓存 / raw 转换)
        """
        cached = await self._cached_copy(comp)
        if cached is not None:
            return cached
        attempts: list = []
        _misses = list(getattr(self, "_local_cache_misses", None) or [])
        if _misses:
            attempts.append("qcache: no local copy for " + ", ".join(_misses[:2]))
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
                self._refetch_last_error = None
                self._refetch_trace = []
                dl = await self._refetch_media(comp, event)
                if dl is None:
                    tr = getattr(self, "_refetch_trace", None) or []
                    why = getattr(self, "_refetch_last_error", None)
                    detail = (tr[-1] if tr else (str(why) if why is not None
                                                 else "no usable ref"))
                    attempts.append(f"refetch: {detail}")
            # 第三级: 组件本地副本(napcat 的 temp 缓存 / raw 转换) —— 与 event 无关,总是试
            if dl is None:
                try:
                    dl = await _local_fallback(comp, self.cfg.max_file_size_bytes)
                except DownloadError as e:
                    attempts.append(f"local: {e}")
                else:
                    self._log(f"used local copy for {comp.kind} after download failure")
            if dl is None:
                primary.fallback_error = "; ".join(attempts[:3])[:300]
                raise primary
        return dl

    async def _refetch_media(self, comp: Component, event):
        """二级补救:向 bot 重取这张图的可用链接。

        两条路,依次尝试:

        ① `get_image` —— 对 (候选值 × 参数形态 `file`/`file_id`/`id`/`image`) 组合试。
           注意 napcat 的 `get_image` 其实**只认它自己发出去的消息文件 uuid**
           （`FileNapCatOneBotUUID`，内存 map，进程重启或超过 ttl 就没了）；用文件名
           当候选时会走到 `FileApi.searchForFile`，图片不在那个缓存里 → `file not found`。
        ② `get_msg` —— 拿消息 id 让 napcat 重新解析整条消息。它的 picElement → OB11
           转换器每次都调 `getImageUrl()`，即用**当前** rkey 重新拼链接，所以这是唯一
           能绕开「消息里那条已过期 url」的路(2026-10-11 线上 400 的根因)。

        全败时返回 None,调用方跳到第三级本地副本。
        """
        call = self._resolve_call_action(event)
        if call is None:
            return None
        cands = _media_refetch_candidates(comp)
        old = (comp.url or "").strip()
        trace: list[str] = []
        primary = None
        attempts = 0

        for cand in cands[:_MAX_REFETCH_CANDIDATES]:
            for key in _GET_IMAGE_PARAM_FORMS:
                if attempts >= _MAX_REFETCH_ATTEMPTS:
                    break
                attempts += 1
                try:
                    resp = await call("get_image", **{key: cand})
                except Exception as e:  # ActionFailed / 不支持该参数形态
                    primary = e
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
                        self._refetch_trace = trace
                        self._log(f"get_image refreshed url via {key} for {comp.kind}")
                        return dl
                    continue
                b64 = data.get("base64")
                if isinstance(b64, str) and b64 and len(b64) >= 16:
                    raw = _b64_payload(b64)
                    if raw:
                        dl = Downloaded(data=raw, mime=None, size=len(raw))
                        dl.origin = f"base64:{_short(cand, 24)}"
                        self._refetch_trace = trace
                        self._log(f"get_image returned base64 for {comp.kind}")
                        return dl
            if attempts >= _MAX_REFETCH_ATTEMPTS:
                break

        # ② get_msg:让 napcat 用当前 rkey 重新解析这条消息
        dl = await self._refetch_via_message(comp, event, trace)
        if dl is not None:
            self._refetch_trace = trace
            return dl

        self._refetch_trace = trace
        self._refetch_last_error = primary
        self._log(f"refetch exhausted for {comp.kind} "
                  f"({len(cands)} candidate(s), get_image={attempts})")
        for line in trace[-4:]:
            self._log(f"  refetch: {line}")
        return None

    async def _download_fresh(self, comp: Component, fresh: str):
        """把重取到的新链接下载成 Downloaded;失败返回 None 并把异常记进 _refetch_last_error。"""
        try:
            dl = await download(
                Component(kind=comp.kind, url=fresh,
                          file_id=comp.file_id, meta=dict(comp.meta or {})),
                fetcher=aiohttp_fetcher,
                retries=self.cfg.download_retries,
                max_bytes=self.cfg.max_file_size_bytes,
                timeout_s=self.cfg.download_timeout_s,
            )
        except DownloadError as e:
            self._refetch_last_error = e
            return None
        dl.origin = fresh
        return dl

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
                subject: Optional[str] = None) -> AssetResult:
        """统一构造失败结果:失败原因分类 + 入索引库 + 写日志。"""
        note = getattr(err_obj, "fallback_error", None)
        if self._index is not None:
            try:
                subj = subject or _failure_subject_for(comp)
                _id, attempts, last_ts = self._index.record_failure(
                    platform=ctx.platform, group_id=ctx.group_id,
                    sender_id=ctx.sender_id, msg_id=ctx.msg_id, idx=ctx.idx,
                    kind=comp.kind, err=err, subject=subj, url=url,
                )
                if attempts > 1 and last_ts:
                    extra = (f"  [历史第 {attempts} 次,"
                             f"上次 {time.strftime('%m-%d %H:%M', time.localtime(last_ts))}]")
                    note = f"{note or ''}{extra}".strip()
            except Exception as e:
                self._log(f"failure record failed: {e!r}")
        self._log(f"{stage} failed for {comp.kind}: {err[:200]}")
        return AssetResult(
            kind=comp.kind, ok=False, err=err,
            failure_kind="fetch" if classify_error(err) == "fetch" else "save",
            note=note,
        )

    def _find_saved_asset(self, msg_id: str, idx: int) -> Optional[dict]:
        """找同一条消息同一 idx 已落盘的资产(用于复用,不再下载)。"""
        if self._index is None or not msg_id or idx is None:
            return None
        if msg_id in ("nomsg", "nonemsg", "0"):
            return None
        try:
            rows = self._index.find_by_msg(msg_id)
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
        if not self.cfg.get_media_fallback or not isinstance(ref, dict):
            return None
        name = ref.get("file")
        if not isinstance(name, str) or not name.strip():
            self._log(f"fwd cache: ref has no usable file | {self._ref_preview(ref)}")
            return None
        try:
            from .warden.localcache import find_cached_image as _find
        except Exception as e:  # noqa: BLE001
            self._log(f"localcache module unavailable: {e!r}")
            return None
        try:
            path = await asyncio.to_thread(_find, name.strip())
        except Exception as e:  # noqa: BLE001
            self._log(f"forward local cache lookup failed for {name!r}: {e!r}")
            return None
        if not path:
            self._log(f"fwd cache: miss for file={name!r} | {self._ref_preview(ref)}")
            return None
        try:
            data = await asyncio.to_thread(
                _read_local_file, path, self.cfg.max_file_size_bytes
            )
        except DownloadError as e:
            self._log(f"forward local cache hit but unreadable ({path}): {e}")
            return None
        self._log(f"used QQ local cache for forward node image: {path}")
        return data.data

    @staticmethod
    def _ref_preview(ref: Any) -> str:
        """临时诊断:把节点 image 段的 data 压成一行短摘要(看 file 字段长什么样)。"""
        try:
            import json as _json
            if not isinstance(ref, dict):
                return f"ref<{type(ref).__name__}>"
            out = {}
            for k, v in ref.items():
                if isinstance(v, str):
                    out[k] = v if len(v) <= 60 else f"{v[:40]}…(len={len(v)})"
                else:
                    out[k] = f"<{type(v).__name__}>"
            return _json.dumps(out, ensure_ascii=False)[:280]
        except Exception as e:  # noqa: BLE001
            return f"preview failed: {e!r}"

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

        if want_json or not want_image:
            payload = json.dumps(
                {"forward": True, "node_count": len(nodes),
                 "nodes": (comp.meta or {}).get("nodes")},
                ensure_ascii=False, indent=2,
            )
            comp_json = Component(
                kind="forward_json", name=f"forward_{ctx.msg_id}_{ctx.idx}.json",
                raw=comp.raw,
            )
            try:
                sr = self._storage.save(payload.encode("utf-8"), comp_json, ctx,
                                         mime="application/json")
                if self._index is not None:
                    try:
                        self._index.record(
                            platform=ctx.platform, group_id=ctx.group_id,
                            sender_id=ctx.sender_id, msg_id=ctx.msg_id, idx=ctx.idx,
                            kind="forward", path=sr.path, size=sr.size,
                            sha16=sr.blake16, forward_meta={"node_count": len(nodes)},
                        )
                    except Exception:
                        pass
                results.append(AssetResult(kind="forward", ok=True,
                                           path=sr.path, size=len(payload),
                                           reused=sr.reused))
            except ValueError as e:
                results.append(AssetResult(kind="forward", ok=False, err=f"json save: {e}"))

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
                            except DownloadError as e:
                                last = e
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
            if failed_urls:
                # 节点内有图片全部重试后仍失败:整图判失败,不落盘
                self._log(
                    f"forward render aborted: {failed_urls}/{len(all_urls)} "
                    f"node image(s) failed to download"
                )
                results.append(AssetResult(
                    kind="forward_render", ok=False,
                    err=f"image download failed ({failed_urls}/{len(all_urls)})",
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
                    if self._index is not None:
                        try:
                            self._index.record(
                                platform=ctx.platform, group_id=ctx.group_id,
                                sender_id=ctx.sender_id, msg_id=ctx.msg_id,
                                idx=ctx.idx + 100,
                                kind="forward_render", path=sr.path, size=sr.size,
                                sha16=sr.blake16,
                            )
                        except Exception:
                            pass
                    results.append(AssetResult(
                        kind="forward_render", ok=True, path=sr.path, size=sr.size,
                        preview_path=sr.path, reused=sr.reused,
                    ))
                except Exception as e:
                    results.append(AssetResult(
                        kind="forward_render", ok=False, err=f"render: {e}",
                    ))

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
        lines = [f"## 最近 {len(rows)} 条"]
        for r in rows:
            lines.append(
                f"- [{r['ts']}] {r['platform']}:{r['group_id']} "
                f"sender={r['sender_id']} msg={r['msg_id']}#{r['idx']} "
                f"kind={r['kind']} size={r['size']} -> {r['path']}"
            )
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
        rows = self._index.find_by_msg(msg_id)
        if idx_filter is not None:
            rows = [r for r in rows if r["idx"] == idx_filter]
        if not rows:
            yield event.plain_result(f"[Warden] 未找到 msg_id={msg_id} 的资产")
            return
        lines = [f"## lookup msg_id={msg_id} ({len(rows)} 条)"]
        for r in rows:
            fwd = ""
            if r.get("forward_meta"):
                fwd = f" | fwd={r['forward_meta']}"
            lines.append(
                f"- id={r['id']} [{r['ts']}] {r['platform']}:{r['group_id']} "
                f"sender={r['sender_id']} idx={r['idx']} "
                f"kind={r['kind']} size={r['size']} "
                f"sha16={r.get('sha16')}{fwd} -> {r['path']}"
            )
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
            yield event.plain_result(f"[Warden] 已删除 {n} 条索引记录 (> {days} 天前)")
        except Exception as e:
            yield event.plain_result(f"[Warden] prune 失败: {e!r}")

    # ----------------- 工具 -----------------

    def _log(self, msg: str) -> None:
        if self.cfg.log_to_stdout:
            print(f"[media-warden] {msg}")


__all__ = ["MediaWardenStar"]
