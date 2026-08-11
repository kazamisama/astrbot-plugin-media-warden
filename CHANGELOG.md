# Changelog

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
