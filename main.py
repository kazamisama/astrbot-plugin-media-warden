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

from .warden import (
    MediaWardenConfig,
    evaluate,
    event_platform,
    event_group_id,
    event_sender_id,
    event_sender_name,
    extract_components,
    summarize,
    AssetResult,
    BatchResult,
    format_batch,
    VERSION,
)
from .warden.components import Component
from .warden.downloader import (
    aiohttp_fetcher, astrbot_component_fetcher,
    download, download_many, DownloadError,
)
from .warden.storage import Storage, SaveContext


@register(
    "media-warden",
    "shirley",
    "群聊素材守门人 v1.3: 监听特定群/用户的非文字消息,按模板落盘,转发链 PIL 渲染/JSON 保存,并发下载 + 指数退避重试, /warden lookup 反查 + 预览回传",
    VERSION,
)
class MediaWardenStar(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        try:
            self.cfg = MediaWardenConfig.from_raw(config or {})
        except Exception as e:
            print(f"[media-warden] invalid config, fallback to defaults: {e!r}")
            self.cfg = MediaWardenConfig()
        self._storage: Optional[Storage] = None
        self._forwarder = None
        self._index = None
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
            from .warden.forwarder import Forwarder
            self._forwarder = Forwarder(
                width=self.cfg.forward_image_width,
                max_nodes=30,
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
            f"index={'on' if self._index else 'off'}"
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

        # 1) 并发下载所有 media
        save_contexts = [
            SaveContext(platform=platform, group_id=group_id, sender_id=sender_id,
                        sender_name=sender_name, msg_id=msg_id, idx=idx, ts=ts)
            for idx in range(len(media))
        ]
        dl_results = await self._download_all(media)
        for comp, ctx, dl in zip(media, save_contexts, dl_results):
            r = self._store_dl(comp, ctx, dl)
            batch.items.append(r)

        # 2) 转发节点(顺序,因渲染需要前序 json)
        for idx, comp in enumerate(forwards, start=len(media)):
            ctx = SaveContext(
                platform=platform, group_id=group_id, sender_id=sender_id,
                sender_name=sender_name, msg_id=msg_id, idx=idx, ts=ts,
            )
            for r in await self._save_forward(comp, ctx):
                batch.items.append(r)

        batch.duration_s = time.time() - t0
        text = format_batch(batch)
        yield event.plain_result(text)
        for extra in self._build_previews(event, batch):
            yield extra

    # ----------------- 内部:并发下载 + 落盘 -----------------

    async def _download_all(self, media: list[Component]) -> list:
        """并发下载所有 media 组件.

        用 Semaphore 限流到 cfg.max_concurrent.
        单个失败 -> 该项是 DownloadError,其他继续;最后返回 list[Downloaded|DownloadError].
        """
        if not media:
            return []
        sem = asyncio.Semaphore(self.cfg.max_concurrent)

        async def _one(c):
            async with sem:
                try:
                    return await download(
                        c, fetcher=astrbot_component_fetcher,
                        retries=self.cfg.download_retries,
                        max_bytes=self.cfg.max_file_size_bytes,
                    )
                except DownloadError as e:
                    return e

        return await asyncio.gather(*(_one(c) for c in media))

    def _store_dl(self, comp: Component, ctx: SaveContext, dl) -> AssetResult:
        if isinstance(dl, DownloadError):
            return AssetResult(kind=comp.kind, ok=False, err=f"download: {dl}")
        try:
            sr = self._storage.save(dl.data, comp, ctx, mime=dl.mime)
        except ValueError as e:
            return AssetResult(kind=comp.kind, ok=False, err=f"save: {e}")
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
        return AssetResult(
            kind=comp.kind, ok=True, path=sr.path,
            size=dl.size, preview_path=preview,
        )

    # ----------------- 内部:转发处理(同 v1.2) -----------------

    async def _save_forward(self, comp: Component, ctx: SaveContext
                            ) -> list[AssetResult]:
        from .warden.forwarder import from_component
        nodes = from_component(comp)
        results: list[AssetResult] = []

        want_image = self.cfg.forward_render_mode in ("image", "both")
        want_json = self.cfg.forward_render_mode in ("json", "both")

        if want_image and (self._forwarder is None or not self._forwarder.can_handle(nodes)):
            if nodes and not self._forwarder.can_handle(nodes):
                self._log(f"forward too large ({len(nodes)} nodes) — JSON only")
            want_image = False

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
                                           path=sr.path, size=len(payload)))
            except ValueError as e:
                results.append(AssetResult(kind="forward", ok=False, err=f"json save: {e}"))

        if want_image and nodes:
            # 节点内图片也并发下载
            all_urls: list[str] = []
            url_to_node: list[int] = []  # 第 N 个 url 对应第几个 node
            for ni, nd in enumerate(nodes):
                for u in nd.image_urls:
                    all_urls.append(u)
                    url_to_node.append(ni)
            node_imgs: dict[int, list[bytes]] = {ni: [] for ni in range(len(nodes))}
            if all_urls:
                async def _fetch(url: str) -> bytes:
                    dl = await download(
                        Component(kind="image", url=url),
                        fetcher=aiohttp_fetcher,
                        retries=self.cfg.download_retries,
                    )
                    return dl.data
                # 并发拉
                results_dl = await asyncio.gather(
                    *(_fetch(u) for u in all_urls),
                    return_exceptions=True,
                )
                for url, r in zip(all_urls, results_dl):
                    if isinstance(r, BaseException):
                        continue
                    ni = url_to_node[all_urls.index(url)]
                    node_imgs[ni].append(r)
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
                    preview_path=sr.path,
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

    # ----------------- 命令 -----------------

    @filter.command("warden list")
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
            f"  db: {s.get('db_path', '?')}"
        )

    @filter.command("warden export")
    async def cmd_warden_export(self, event: AstrMessageEvent, arg: str = ""):
        if self._index is None:
            yield event.plain_result("[Warden] 索引未启用")
            return
        path = arg.strip() or os.path.join(self.cfg.storage_root, "_export.json")
        try:
            n = self._index.export_json(path)
            yield event.plain_result(
                f"[Warden] 已导出 {n} 条 -> {path}"
            )
        except Exception as e:
            yield event.plain_result(f"[Warden] 导出失败: {e!r}")

    @filter.command("warden lookup")
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
