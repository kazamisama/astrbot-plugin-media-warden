# Changelog

## v1.9.1

### Fixed
- **`get_image` 的入参用错了字段**（v1.9.0 的二级补救形同虚设）。v1.9.0 把 `component.file_id` 当 OneBot `get_image` 的入参传，而 AstrBot 4.26+ 的 `PreProcessStage` 早把 `Image` 组件重写成 jpeg 静帧、`component.file_id` 已变成 `C0C9610D3203EB3B404638B24226AE23.jpg` 这种本地文件名 —— napcat 按「路径/URL/Base64」解析它，返回 `retcode=1200 file not found`（线上 2026-10-10 日志逐字可见），补救直接掉到第三级（本地副本也常常没有）→ 用户看到「最新的图也说链接失效」。
  - `_recover_original_urls()` 现在把 raw segment 里的 `file`（napcat 自己的图片 id）与 `fileid` 一起记进 `comp.meta`（`raw_file` / `fileid`）。
  - 新增 `_media_refetch_candidates()`：候选取值按 **`meta["raw_file"]` → `meta["file"]` → `meta["fileid"]` → `meta["file_id"]` → url 里的 `fileid` → 非本地存在的 `meta["path"]` → `component.file_id`** 排序并去重（每个候选还会补一条去扩展名的 basename，与 AstrBot 核心的 `_build_image_id_candidates` 一致），最多取 3 个。
  - 新增 `_refetch_media()` 取代 `_fresh_media_url()`：对 (候选 × 参数形态) 依次尝试，参数形态为 `file` / `file_id` / `id` / `image`（与 AstrBot 核心 `quoted_message/image_resolver.py::_build_image_resolve_actions` 同款），总尝试上限 8 次；响应里除 `data.url` 外还接受 `data.base64`（解成字节直接入库）。
  - `bot.call_action` 的取法也向核心看齐：优先 `event.bot.api.call_action`（aiocqhttp），再退回 `event.bot.call_action`。

### Tests
- `_smoke.py` PHASE5 新增 2 项（→ 14）：补救候选排序（raw_file 优先、去扩展名 basename、fileid 兜底）、`get_image` 逐形态尝试 + base64 返回；`test_recover_raw_url_overrides_local_jpeg` 补断言 `meta["raw_file"]`/`meta["fileid"]`。
- 冒烟总数 **95 → 97**（phase1=22 phase2=16 phase3=21 phase4=24 phase5=14）。

## v1.9.0

### Added
- New config `get_media_fallback` (default `true`): 当媒体 url 是 http(s) 却下载失败时（典型：QQ rkey 授权链接过期 → 400），插件不再直接判失败，而是走三级补救链 —— ① 原 url 重试；② 向 bot 发 OneBot `get_image` 换一条新链接重下（新链接会记进回执 note `链接已重取: …`）；③ 回退组件本地副本（napcat 的 `.astrbot/data/temp`、`meta["path"]` 或 `raw.convert_to_file_path()`）。三级都失败才计失败，且**保留原始错误**（补救链失败原因挂在 `DownloadError.fallback_error` 上）。`event is None` 时第 ③ 级仍然生效。
- New config `index_reuse_saved` (default `false`): 同一条消息的同一下标 `(msg_id, idx)` 已落盘过、且文件仍在磁盘时，跳过下载与落盘直接复用，回执中标 `索引复用`。文件已被删除则明确报 `reuse: stored file missing (<path>)` 并计为**落盘失败**，不伪装成功。
- 失败入库：索引新增 `failures` 表（`ts/platform/group_id/sender_id/msg_id/idx/kind/err/subject/url/attempts`）。`subject` 优先用 `fileid:<id>`（从 url query 提取，能吸收 rkey/sign 变化），其次是 `url:<host><path>`，同一 subject 反复失败时 `attempts` 累加，回执 note 追加 `[历史第 N 次,上次 MM-DD HH:MM]`。失败资产不入 `assets` 表，所以以前只能靠日志统计，现在可查。
- New command `/warden fail [N]`（ADMIN，默认 10、上限 50）：列出最近 N 条保存失败（时间 / kind / msg#idx / attempts / subject / err）。`/warden stats` 增加 `failures:` 与 `failures_by_kind:` 两行。
- 失败原因区分：`warden/reporter.py` 新增 `classify_error()` / `failure_summary()` / `BatchResult.failure_breakdown()` / `failure_phrase()`。回执表头不再一律说「部分保存失败」，而是分「下载失败（源链接失效）」与「落盘失败」，并多一行 `原因:`；@ 触发者的文案也随原因分叉（`N 个媒体没能保存 —— 下载失败(源链接已失效)，X/Y 已保存`）。

### Changed
- `astrbot_component_fetcher` 接受 `refresher` 回调，`download()` 接受 `fallback` 回调；`astrbot_component_fetcher` 在 http 失败后先试 refresher 再试本地副本。
- `Storage.save` 的异常路径与下载失败现在都经 `_failed()` 统一记账（写日志 + 失败入库 + 返回带 `failure_kind`/`note` 的 `AssetResult`）。
- `main.py` 的 `_download_one()` 从「单次下载」改为三级回退；`_download_all()` 不变（仍并发 + 单资产隔离）。
- `_find_saved_asset` 的判空从 `not idx` 修正为 `idx is None`：下标 0（第一条媒体）以前永远命中不了索引。

### Known limits
- 转发节点图（`_save_forward`）仍只按 `[hi, url]` 两个候选 url 试下载，**没有**接 `get_image` / 本地副本回退 —— 节点图 url 里没有 `file_id`，换链接无从下手。节点图仍按 v1.5.0 设计整体失败即不落盘。
- `/warden fail` 只读索引库里的失败记录；`enable_index_db=false` 时该命令无效。

### Tests
- `_smoke.py` 新增 PHASE5（12 项）：`_url_fileid` 提取、fetch/save 分类、失败文案、三级回退（本地副本 / 保留主错误 / 重取新链接）、`_download_one` 的两条补救路径、失败入库 + attempts 累加、索引复用（命中 / 文件缺失）、`/warden fail` 与 `stats`、`failure_stats`/`prune` 覆盖 failures 表。
- 冒烟总数 **83 → 95**（phase1=22 phase2=16 phase3=21 phase4=24 phase5=12）。

## v1.8.2

### Changed
- `aiohttp_fetcher` wraps `aiohttp.ClientError` as `DownloadError`, so connection failures participate in retry/backoff.
- Download and save failures are isolated per asset; a downloader or filesystem error no longer aborts the whole group-message batch.
- Forward render falls back to JSON when `Forwarder` is unavailable instead of dereferencing `None`.
- Forward-node images now respect `max_file_size_mb` and `max_concurrent`.
- Avatar cache writes are atomic and serialized per cache path.

### Tests
- Added regressions for transient `aiohttp.ClientError` retry, missing-forwarder JSON fallback, and `_store_dl` OSError isolation.

## v1.8.1

### Changed
- Config parsing is now field-tolerant: invalid fields are skipped with a recorded warning while valid settings are preserved, instead of falling back to a full default config.
- Forward-node avatars are only fetched when image rendering is actually enabled; `json` mode no longer makes wasted avatar requests.

### Fixed
- Dedupe pointer reuse now re-verifies the target file's blake2b digest before returning a reused path, so a tampered or modified file is re-saved instead of trusted.
- A stale dedupe pointer is now repaired after the re-save, so later identical content reuses the new file instead of creating another duplicate.
- `avatar_platforms` must be a list of strings; invalid values are skipped with a recorded warning.
- Group whitelist/blacklist matching normalizes platform prefix and group ID case on both config and event sides.

## v1.8.0

### Added
- New config `avatar_platforms` (default `["aiocqhttp", "qq_official"]`): only whitelisted platforms fetch forward-node avatars; non-QQ platforms no longer hit qlogo.cn.

### Changed
- `/warden list / stats / lookup / export / prune` are now ADMIN-only via `@filter.permission_type(PermissionType.ADMIN)`.
- `/warden export` now only writes inside `storage_root`; relative paths resolve under it and escaping paths are rejected.
- `MediaWardenConfig` validates numeric bounds (`max_concurrent >= 1`, `download_retries >= 0`, `max_file_size_mb >= 1`, `forward_image_width >= 1`, `forward_render_max_nodes >= 1`, `avatar_cache_size_mb >= 0`); `_conf_schema.json` mirrors the same minimums.

### Fixed
- `Storage._existing_for_digest` now re-validates pointer targets inside the storage root, so a tampered `.ptr` cannot reuse an out-of-root path.
- Restored Chinese comments/docstrings that had been replaced with `?` in `main.py` and `warden/components.py`.
- Forwarder now alpha-composites transparent node images onto the bubble background; previously transparent PNG areas were pasted as raw RGB and could render as black.

## v1.7.2

### Fixed
- `_wrap_mixed` 长文本不再被塞到单行被画布右边截断。当非 emoji cluster 自身宽度超过 `max_w`（例如长段中文）时，退化为 char-level 切行。emoji cluster 仍保持原子（ZWJ 链不拆）。原 bug：cluster 触发切行判定时 `lines[-1]` 在首行为空列表（falsy），导致永不切行。
- 转发图有头像时图片右边不再越出画布。`text_w` 现在扣除 `avatar_size + 6`（仅当 `enable_user_avatar=True` 且 `avatar_path` 非空时生效），与渲染时 `tx` 的实际偏移对齐。原 bug：图片起 x 已被头像推到 `bubble_x + padding + avatar_size + 6`，但 `text_w` 仍按气泡全宽算，导致 `tx + nw` 超过 `cfg.width` 约 10 px。
- 四个 `text_w` 计算点（`render` 高度测量、`render` 渲染、`_render_with_pil` 高度测量、`_render_with_pil` 渲染）统一扣除头像偏移。

### Tests
- `test_forwarder_emoji_mixed_layout` 新增两条 regression：长中文 `("中" * 80)` 验证切 ≥4 行且每行宽 ≤ `max_w`；带头像 + 720 宽图渲染后扫描画布最右 2 列，断言没有图片 / 气泡像素溢出。反向验证（还原 bug 版）测试 fail，证明覆盖有效。

## v1.7.1

### Changed
- `avatar_timeout_s` default: **5 → 10 秒**。q.qlogo.cn 冷启动偶发 5s+ 才回，避免首次抓头像被 timeout 截掉。
- `AvatarCache` 单文件上限: **2MB → 5MB**。部分用户高清头像超过 2MB 会被旧的 `max_file_bytes` 拒绝。
- `_conf_schema.json` 重写为 5 个 section（监听范围 / 保存与索引 / 下载与并发 / 转发渲染 / 回复行为），WebUI 按功能分框显示。运行时 config 仍是扁平 key（AstrBot 自动扁平化），旧 `astrbot-plugin-media-warden_config.json` 不用改。

### Added
- New config `forward_render_max_nodes`（默认 30）。原来硬编码在 `main.py` 的 `Forwarder(max_nodes=30)` 现在走配置。调到 60+ 可以强制 61 节点的群聊转发出 PNG（默认会降级 JSON-only）。


## v1.7.0

### Added
- Forward node rendering now draws circular user avatars next to the sender name. Avatars are fetched via `https://q1.qlogo.cn/g?b=qq&nk={sender_id}&s={size}` and cached as JPEG in `<storage_root>/_avatars/{platform}_{sender_id}.jpg`.
- New module `warden/avatar.py` exposing `AvatarCache` (file-based with LRU eviction by mtime), `qq_avatar_url(sender_id, size)`, and `fetch_many_avatars(cache, pairs, concurrency=N)` for concurrent batch fetch.
- New `ForwardNode.avatar_path: Optional[str]` field. Forwarder's `render()` and `_render_with_pil()` paste the avatar as a circle on the left of the sender name when set.
- Four new config items: `enable_user_avatar` (default `true`), `avatar_size` (default `36` px diameter), `avatar_timeout_s` (default `5`), `avatar_cache_size_mb` (default `50`, LRU eviction).
- `_RenderCfg` (Forwarder's internal render-config dataclass) gains `enable_user_avatar` and `avatar_size` fields so the Forwarder can render avatars standalone (without going through MediaWardenStar).

### Behavior
- Concurrent fetch via `Semaphore(max_concurrent)`; failures silently fall through (no avatar drawn, no error logged per node).
- Cache miss → fetch → write to disk → evict if total size exceeds cap. Cache hits skip the network entirely.
- Avatar fetch failures (404, network timeout, etc.) do not block rendering — nodes render without avatars.
- Avatar JPEG with non-transparent background is pasted via circular mask (visible result: white halo around circular avatar). Acceptable for v1.7.0; documented limitation.

### Follow-ups (separate PRs)
- v1.7.1: color avatar via twemoji PNG set (no Pillow CBDT dependency).
- v1.7.2: 1px border around avatar to mask white halo from JPEG backgrounds.


## v1.6.0

### Added
- Forward text rendering now supports emoji via system emoji font (Windows `seguiemj.ttf` / Linux `NotoColorEmoji.ttf` / macOS `Apple Color Emoji.ttc`). Emoji codepoints are detected and routed through the emoji font while CJK/Latin text stays on the regular font. ZWJ chains and VS-16 variation selectors are kept atomic. Without a system emoji font the original plain-text layout runs unchanged.
- New module-level helpers in `warden/forwarder.py`: `_is_emoji_char`, `_find_emoji_font`, `_wrap_mixed`, `_line_h_mixed`, `_char_w`. New `Forwarder._font_emoji` attribute set in `_ensure_fonts`.
- Smoke test `forwarder: emoji mixed-layout render (v1.6.0)` covers codepoint detection, segment splitting, ZWJ atomicity, VS-16 handling, newline behaviour, and end-to-end render.

### Known limitations
- Pillow 12 does not parse the CBDT / Apple color emoji glyph tables — emoji render as **black-and-white vector glyphs** rather than color. This removes tofu boxes but does not deliver colored emoji. Color emoji is planned for v2.0 via the twemoji PNG set.

### Fixed
- Two regressions caught by the smoke test's `(400, 253) 10789B` plain-text byte-equivalence assertion and fixed in the same release:
  - `_wrap_mixed` originally treated `\n` as a soft wrap; now matches `wrap_text` and forces a new line at every `\n`.
  - `_wrap_mixed` originally split consecutive non-emoji characters into single-char segments; now merges them so plain-text draw calls remain byte-equivalent to v1.5.1.


## v1.5.0

### Added
- New config `download_timeout_s` (default 30): per-download timeout in seconds for media and forward-node images.
- New config `at_on_save_failure` (default true): when any asset fails to save, @-mention the listened sender as a reminder.

### Changed
- Forward render now treats a node image that fails after all retries as a hard failure: the whole forward image is not rendered or saved (`forward_render` -> ok=False), instead of silently dropping the image.

### Fixed
- Forward image collection mapped downloaded bytes to nodes via `all_urls.index(url)`, which mis-assigned images when the same URL appeared in multiple nodes; now mapped by position.


## v1.4.14

### Changed
- Restore a subtle 6px-per-level indentation for nested forwarded messages.
- Use distinct left-rail colors for different nested forwarded-message levels.

## v1.4.13

### Fixed
- Extract top-level forward ids from `resid` / `res_id` and `nodes` segments so fetched sender names can replace generic inline names like `QQ用户`.

## v1.4.12

### Changed
- Reduce nested forwarded-message indentation so framed replies keep more content width.

## v1.4.11

### Added
- Render nested forwarded chat records with indented framed bubbles.

### Fixed
- Prefer `get_forward_msg` nodes over inline nodes when a top-level forward id is available, so first-level sender names can use the full fetched metadata instead of generic `QQ用户`.

## v1.4.10

### Added
- Expand nested forwarded chat records up to 2 levels before JSON saving and image rendering.

## v1.4.9

### Fixed
- Keep forwarded portrait images at full bubble width and render their full height without cropping.
- Keep forward-render height pre-calculation consistent with the actual PIL image draw size.

## v1.4.8

### ??
- ??????????????????? QQ ???? url ? `is_origin=0` ??? `is_origin=1`?`gchat.qpic.cn` / `multimedia.nt.qq` ???????????? url?

### ??
- ???????? LANCZOS ????????????????????????
- ?????????`max_image_width` 360 ? 720????????????????
- ????????????????????????????

### ??
- ?????? JSON?napcat/OneBot ??? forward id ?????? `get_forward_msg` ??????????/???
- ?? forward / node ??? `forward_id`??? napcat `get_forward_msg` ?????
