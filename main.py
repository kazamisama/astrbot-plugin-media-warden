"""astrbot-plugin-media-warden 入口.

Phase 3 (v1.2):
  - forwarder (PIL 渲染 / JSON sidecar,按 forward_render_mode 决定)
  - index SQLite
  - /warden list / /warden export / /warden stats 命令
  - 3c 预览图回传:image 类型用 event.image_result,降级 plain_result
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from typing import Any, Optional

from astrbot.api.star import Star, register, Context
from astrbot.api.event import filter, AstrMessageEvent

from warden import (
    MediaWardenConfig,
    evaluate,
    extract_components,
    summarize,
    AssetResult,
    BatchResult,
    format_batch,
    VERSION,
)
from warden.components import Component
from warden.downloader import aiohttp_fetcher, download, Downloaded, DownloadError
from warden.storage import Storage, SaveContext


@register(
    "media-warden",
    "shirley",
    "群聊素材守门人:监听特定群/用户的非文字消息,按固定命名落盘,转发链渲染/JSON保存并回复处理结果",
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
            f"| root={self.cfg.storage_root}"
        )

    async def initialize(self) -> None:
        self._storage = Storage(
            root=self.cfg.storage_root,
            dedupe=self.cfg.dedupe,
            pattern=self.cfg.filename_pattern,
            max_bytes=self.cfg.max_file_size_bytes,
        )
        try:
            from warden.forwarder import Forwarder
            self._forwarder = Forwarder(
                width=self.cfg.forward_image_width,
                max_nodes=30,
            )
        except ImportError as e:
            self._log(f"forwarder init failed: {e!r} — falls back to JSON sidecar")
            self._forwarder = None
        if self.cfg.enable_index_db:
            try:
                from warden.index import AssetIndex
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

        pm = getattr(event, "platform_meta", None)
        platform = (getattr(pm, "platform", None) or "unknown").lower()
        group_id = getattr(pm, "channel_id", None) or "unknown"
        sender = getattr(event, "sender", None)
        sender_id = (getattr(sender, "user_id", None) or "anon") if sender else "anon"
        sender_name = (
            getattr(sender, "nickname", None) or getattr(sender, "name", None) or sender_id
        )
        msg_id = getattr(event, "message_id", None) or "nomsg"
        ts = int(getattr(event, "timestamp", None) or time.time())

        if self.cfg.log_to_stdout:
            self._log(
                f"matched | msg={msg_id} sender={sender_id} "
                f"media={len(media)} forward={len(forwards)}"
            )

        t0 = time.time()
        batch = BatchResult()

        for idx, comp in enumerate(media):
            ctx = SaveContext(
                platform=platform, group_id=group_id, sender_id=sender_id,
                sender_name=sender_name, msg_id=msg_id, idx=idx, ts=ts,
            )
            r = await self._save_one(comp, ctx)
            batch.items.append(r)

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
        # 3c 预览图回传
        for extra in self._build_previews(event, batch):
            yield extra

    # ----------------- 内部 -----------------

    def _build_previews(self, event: AstrMessageEvent, batch: BatchResult):
        """生成预览图回传.

        优先级:
          1) forward_render 生成的 PNG  (kind == 'forward_render')
          2) 第一张 image 类型的 AssetResult

        有 event.image_result 方法 -> 调它
        没有 -> 降级:在 plain_result 后面再补一行 '预览: <path>'
        """
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
        # 降级:这条消息前面已经 yield 了 plain_result,这里只能再补一条
        try:
            yield event.plain_result(f"预览: {path}")
        except Exception:
            pass

    async def _save_one(self, comp: Component, ctx: SaveContext) -> AssetResult:
        try:
            dl = await download(comp, fetcher=aiohttp_fetcher)
        except DownloadError as e:
            return AssetResult(kind=comp.kind, ok=False, err=f"download: {e}")
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

    async def _save_forward(self, comp: Component, ctx: SaveContext
                            ) -> list[AssetResult]:
        from warden.forwarder import from_component
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
                {"forward": True, "node_count": len(nodes), "nodes": (comp.meta or {}).get("nodes")},
                ensure_ascii=False, indent=2,
            )
            comp_json = Component(
                kind="json", name=f"forward_{ctx.msg_id}_{ctx.idx}.json",
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
            async def _img_dl(url: str) -> bytes:
                dl = await download(
                    Component(kind="image", url=url), fetcher=aiohttp_fetcher
                )
                return dl.data
            try:
                png = await self._forwarder.render(nodes, image_downloader=_img_dl)
                comp_png = Component(
                    kind="image", name=f"forward_{ctx.msg_id}_{ctx.idx}.png",
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

    # ----------------- 工具 -----------------

    def _log(self, msg: str) -> None:
        if self.cfg.log_to_stdout:
            print(f"[media-warden] {msg}")


__all__ = ["MediaWardenStar"]
