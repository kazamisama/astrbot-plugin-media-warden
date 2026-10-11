# Changelog

## v1.9.8

### 结论：转发节点图里的「老图」救不回来（不是 bug，是 NapCat 的架构边界）

v1.9.6 给转发节点图接上本地缓存后，现场仍然 `2/2 node image(s) failed to download`。
v1.9.7 的取证日志给出了节点段的真实形态（**字段假设是对的**）：

```
fwd cache: miss for file='DCFF08A6D4229FE78EFF487D48A0D0A8.jpg' |
  {"summary": "[动画表情]", "file": "DCFF08A6…", "sub_type": "<int>", "url": "…(len=235)", "file_size": "5964"}
```

`file` 确实是 32 位 MD5。但那两个 MD5 **在整台机器上都不存在**：

- `Get-ChildItem "C:\Users\chiriu\Documents\Tencent Files" -Recurse` 全量搜 `dcff08a6…` / `fe56bb22…` → `NOT FOUND`
- NapCat 自己的缓存目录（`NapCat.Shell\cache`，只有 1 个文件）、NapCat temp、`AppData` 下的 NapCat、AstrBot 的 `data\temp`（466 个文件）→ 全部 `NOT FOUND`

原因（读 `napcat.mjs` 确认）：**NapCat 从不下载转发节点里的图片**。节点内容是 `get_forward_msg`
现查出来的元数据（`url` + `file`），NapCat 只是转发链接。而 QQ 客户端为了显示当然会下，所以
**普通图片**那条路（v1.9.5）能命中 `Image/C2C/Image2`；转发节点这条路上 NapCat 手里从来没有过字节。

`get_image` 也救不了 —— `GetFileBase._handle`（`napcat.mjs`，`class GetFileBase extends OneBotAction`）
第一步就是 `FileNapCatOneBotUUID.decode(payload.file)`，那是 **NapCat 进程内的内存 map**
（`TimeBasedCache`，24h TTL），只有 NapCat **自己发出去/处理过**的文件才有条目；`decode` 返回 undefined
就直接 `throw new Error("file not found")`（= 线上 `retcode=1200`）。节点图的 MD5 从未进过那个 map。

**所以链路是**：rkey 过期（约 9 分钟）→ 只有「按钮那一刻就存在的本地副本」能救 → 转发节点图的字节
NapCat 从没拉过 → 无解。已把这条写进 `HANDOVER.md §1.-4` 与 README「已知边界」，不再反复试。

### Changed
- 移除 v1.9.7 的诊断输出（`_ref_preview()` 与两处 `fwd cache:` 日志）—— 缓存未命中现在是**预期结果**，
  每张图打一行是噪声。命中仍打 `used QQ local cache for forward node image: <path>`。
- `_forward_local_cached` 未命中分支留注释指向 `HANDOVER.md §1.-4`，避免以后又被当成 bug 重查一遍。

### Tests
- 冒烟不变：`ALL OK (phase1=22 phase2=16 phase3=21 phase4=24 phase5=18, total=101)`

## v1.9.7

### Notes
- 一次性诊断版本：`_ref_preview(ref)` 把节点 image 段的 `data` 压成一行（字符串截断 60 字符并标长度、
  非字符串只标类型），在「没有可用 file」与「按 file 查缓存未命中」两处打日志。结论已并入 v1.9.8。

## v1.9.6

### Fixed
- **合并转发的节点图也接上本地缓存补救**（v1.9.5 上线后现场复测：普通图片已能保存，但转发链仍整图失败）。20:02:47 线上逐字：`forward fetched: 2 nodes for id=7695379530489107755` → `forward render aborted: 2/2 node image(s) failed to download` → `[Warden] ⚠️ 部分保存失败 (0/1) — 下载失败（源链接失效）`。
  根因是 `_save_forward` 的节点图**从不经过 `_download_one`**：它从节点内嵌 url 直接 `aiohttp_fetcher` 下载（只试 `[is_origin=1, 原 url]` 两个候选），链路里没有第 ⓪ 级。而节点里的 image 段其实同样带 `file=<md5>.<ext>` —— 钥匙一直握在手里。
- `warden/forwarder.py`：`ForwardNode` 新增 `image_refs: List[dict]`，与 `image_urls` 一一对应，存放该 image 段的原始 segment `data`（`file`/`url`/`file_size`）。`_coerce_nodes` 填它但不改 `image_urls` 的语义（仍是 `url or file`），老调用方不受影响。
- `main.py`：新增 `_forward_local_cached(ref)`；`_save_forward` 的 `_fetch(url)` 改为 `_fetch(url, ref)`，**先查本地缓存再走网络**，`all_urls` 与 `all_refs` 同步构造。
- 日志：命中时 `used QQ local cache for forward node image: <path>`。

### Notes
- 与 v1.9.5 同一条 `get_media_fallback` 开关；关掉即回到 v1.5.0 的「只试两个 url、任一失败即整体失败、不落盘」。
- 节点段没有 `file` 字段（非 napcat 实现、或节点图是外链）时行为不变。

### Tests
- `_smoke.py` 新增 `test_forward_local_cached_uses_node_file_md5`：走**整条 `_save_forward`**（`forward_render_mode="both"`）验证节点图从本地缓存来、网络调用 **0** 次、PNG 正常产出；并覆盖「无 `file` / 非 dict / 关闭开关 / 未命中」四种回退。`test_forwarder_coerce_nodes` 补 `image_refs` 断言。总数 **100 → 101**（phase5 17 → 18）。

## v1.9.5

### Fixed
- **图片本体其实一直就在本地，补救链却从没找过它**（v1.9.0–v1.9.4 的线上失败全部可以在本地救回来）。QQ 客户端收到图片时已按 **MD5** 存好副本；消息段里的 `data.file`（我们存在 `Component.meta["raw_file"]`）就是那个 `<md5>.<ext>`：
  - `Tencent Files/<QQ号>/Image/C2C/Image2/<md5>.<ext>` —— 普通图片，扩展名与原始格式一致（动图是 `gif`）；
  - `Tencent Files/<QQ号>/nt_qq/nt_data/Emoji/emoji-recv/<YYYY-MM>/Ori/<md5>.<ext>` —— 表情。

  实测 2026-10-11 19:17–19:43 全部 8 张失败图（`a0a79925…`、`86bcfd43…`、`971cfeae…`、`2b5d37ba…`、`9c3b2787…`、`3d225506…`、`a8b291af…`）都能在本地找到，且文件时间戳与消息到达时间一一对上（`86bcfd43…` 19:19:23 落盘，消息 19:19 到）。其中 `9c3b2787…` 本地 `gif` 有 **1109322B**，比从网上拿到的还大 3 倍。此前的第 ③ 级「组件本地副本」只查 `component.path` 与 `raw.convert_to_file_path()` —— 这两条在本机都拿不到东西（`component.path` 是空的，AstrBot 的 `data/temp` 最新文件停在 2026-09-24）。

### Added
- `warden/localcache.py`：`find_cached_image(name)` —— 入参是消息段里的 `data.file`（`<md5>.<ext>`），依次在 `Image/C2C/Image2`（先试原扩展名，再大小写不敏感地扫）与 `emoji-recv`（近 6 个月倒序，先 `Ori` 后 `Thumb`，最多扫 24 个目录）里按 MD5 找。短于 16 字符的不猜，未命中返回 `None`。
- `main.py:_cached_copy()` + `_download_one` 新增**第 ⓪ 级**：先查本地缓存，命中就直接用、**不碰一次网络**（命中日志 `used QQ local cache for <kind>: <path>`，`Downloaded.origin = "localcache:<path>"`）。查找与读盘都走 `asyncio.to_thread`，不阻塞事件循环；本地查找抛异常只记日志、不影响后续下载。

### Notes
- 为什么必须放在最前面：rkey 有效期只有约 9 分钟，重试/等待只会让链接死得更彻底；而本地副本在消息到达的同一秒就已经写好了。第 ② 级「向 bot 重取」保留但已确认救不了 rkey 过期（napcat 的 `get_msg` 原样返回消息里那条 url，实测 `url_same_as_msg=true`）。
- 与 ① 正常 http 的关系：第 ⓪ 级是**加速与兜底**，不是替代 —— 本地没命中时下载链与 v1.9.4 完全一致。

### Tests
- `_smoke.py` 新增两项：`test_localcache_finds_qq_image_by_md5`（大小写/扩展名不敏感、`C2C` 优先于 `emoji`、更晚月份的 `Thumb` 不吃、短名与未命中不误报）、`test_main_download_prefers_qq_local_cache`（缓存命中时网络调用次数为 **0**；未命中时照旧走 http 并如实报错）。总数 **98 → 100**（phase5 15 → 17）。

## v1.9.4

### Notes
- 一次性诊断版本（未单独提交）：`warden/diag_probe.py` 临时探针，用来现场确认 `get_msg` 返回的消息段、`get_status`/`can_send_image`/`get_version_info` 以及 4 种 User-Agent 下同一 url 的 http 响应。结论已并入 v1.9.5（见上），探针文件已删除。

## v1.9.3

### Fixed
- **`get_msg` 的返回被解包了，代码没跟上**（v1.9.2 上线后现场仍是 `0 fresh url(s)`）。aiocqhttp 的 `call_action` 走 `_handle_api_result()`，成功时 **return `result["data"]`**（`aiocqhttp/api_impl.py:28-39`）—— 也就是说我们拿到的是 `{"message": [...], "message_id": …}` **本身**，而不是 `{"status":"ok","data":{...}}` 外壳。v1.9.2 的 `_media_urls_from_resp` 只在外壳里找 `message`，于是每次都 `0 fresh url(s)`（19:28 线上日志逐字）。
- 新增 `_segment_list(resp)`：两种包裹都认（裸 `{"message": …}` 与带 `{"data": {"message": …}}`），并容忍 `message` 是 dict 包一层的情况；`_media_urls_from_resp` 改用它。
- `get_msg` 的 trace 现在写 `N seg(s), M fresh url(s), keys=[…]`，拿到空响应也能一眼看出是「没段」还是「没链接」。

### Tests
- `_smoke.py`：`test_main_refetch_via_get_msg_regenerates_url` 的 stub 改成返回**解包后**的形态（与 aiocqhttp 一致），并补 `_segment_list` 对裸 / 带外层 / 非 dict 三种输入的断言。
- 冒烟总数仍 **98**（phase5=15）。

## v1.9.2

### Fixed
- **`get_image` 对 napcat 其实救不了图**（v1.9.1 的补救在现场仍然全败）。读 napcat 自己的实现（`NapCat.Shell/napcat.mjs`）确认：`GetFileBase._handle` 只认三条路 —— ① `FileNapCatOneBotUUID.decode(file)` 解出 `{peer,msgId,elementId}` 再 `downloadMedia`；② `decodeModelId`；③ `FileApi.searchForFile([file])` 按**文件名**在文件缓存里找。`FileNapCatOneBotUUID` 是 napcat 进程内的内存 map（24h TTL，重启即失效），而来消息的 image 段里只有 `data.file = 图片文件名`（napcat 的 `picElement` 转换器明确把 `file` 设成 `element.fileName`，那个 uuid 只编码、不回传）。所以拿文件名去 `get_image` 永远落到第 ③ 条 → `retcode=1200 file not found`。
- 新增第二条重取路径 **`get_msg`**：用 `message_id` 让 napcat 重新解析整条消息。它内部的 picElement → OB11 转换器每次都调 `getImageUrl()`（`getRkeyData()` + `getImageUrlFromParsedUrl()`），也就是**用当前 rkey 重新拼链接** —— 这才是绕开「消息里那条已过期/失效 url」的正路。`_refetch_media` 现在是 `get_image`（多候选 × 多形态）→ `get_msg`（多入参形态：只 `message_id` / 带 `group_id` / 带 `self_id`）→ 本地副本。
- `_refetch_media` 的每一跳都记进 `_refetch_trace`，全败时把最后几条写进日志（`refetch: …`）并构成回执里的 `refetch: <最后一跳>`，不再像 v1.9.1 那样只有一句 "exhausted" 看不出败在哪。
- 多图消息按 `data.file` 与候选文件名匹配挑对那一段（`_media_urls_from_resp(resp, old=…, hint=…)`），匹配不到则退回消息里第一个可用图。

### Tests
- `_smoke.py` PHASE5 新增 1 项（→ 15）：`get_image` 全败后用 `get_msg` 重解析拿新链接并下载成功（含入参断言与多图挑段）。
- 冒烟总数 **97 → 98**（phase1=22 phase2=16 phase3=21 phase4=24 phase5=15）。

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
