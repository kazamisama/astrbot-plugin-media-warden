# Changelog

## v1.20.3

「图片加载失败」的真凶是**取图接口的 8 MB 上限**，不是文件坏了。

### Fixed

- **超过 8 MB 的图/动图直接加载失败**：页面取图走 `GET page/thumb`（base64 内联，因为 iframe 里的 `<img>` 带不上鉴权头），而这个接口把非视频的阈值卡在 `_THUMB_MAX_BYTES = 8 MB`。QQ 里 9.2 MB 的 GIF 根本不在这个预算里 —— 字节明明就在存档目录里，页面却显示「图片加载失败」。现在图/视频统一走 `_MEDIA_MAX_BYTES = 64 MB`（视频本来就是这个数），9.6 MB 的 GIF 实测能取（base64 约 12.8 MB）。
- **超限时说清原因**：超过 64 MB 仍然拒绝，但 message 改成「媒体太大（X MB，上限 64 MB），请用『打开目录』下载」；页面对应的占位从「图片加载失败」换成「图片太大，用『打开目录』看」，并把完整原因放进 `title`。
- 取图路径不再按扩展名分叉（图/视频同一个上限）。

### Verified

- 冒烟补断言：9 MB 的扁平动图 `get_thumb` 返回 ok；65 MB 的文件仍报错且 message 带「上限」（冒烟仍 171 项）。
- 拿线上那条 9,624,805 字节的 GIF 实跑 `get_thumb`：status=ok，data_url 长度 12,833,098。

## v1.20.2

三处「页面说的和存档里的不是一回事」的修复：补齐提示、图/视频计数、多层转发。

### Fixed

- **补齐结果提示不再一律「已补齐」**：前端先 `unwrap()` 了响应，只拿到 `data`，把服务端写好的 `message`（「补齐 N 项 / 仍有 N 项缺失」）丢了，于是 `|| "已补齐"` 这个兜底永远生效 —— 一项没补上（链接过期）也说补好了。现在直接读响应里的 `message`，并按 `added`/`missing` 兜底拼文案：一项没补上时显示「没有可补的媒体；仍有 N 项缺失（链接已过期或没留地址）」且用错误色，部分补上时说清补了几项、还剩几项。服务端 `POST page/backfill` 的返回结构没变。
- **列表不再把视频数成图**：条目上的 pill 以前直接拿 `asset_count` 写「N 图」，于是「1 视频 + 1 图」显示成「2 图」。现在按资产 `kind` 分开数（老资产没 `kind` 时回查段类型），分别显示「N 图」「N 视频」；一种都没有但有别类资产时显示「N 媒体」。
- **多层（嵌套）合并转发终于能看出层级**：`main._expand_forward_nodes` 早就给节点打过 `__warden_nested_depth`，但 `record.capture` 把它丢了。于是页面把外层那一个 `[forward id=…]` 段渲染成原始文本，展开出来的子节点又和外层节点挤在同一层，完全看不出一条 6 节点的记录其实是「外层 1 条 + 转发里 5 条」。现在：
  - `record.capture` 把 `depth` 写进 `record.json`（不进内容指纹，去重结论不受影响）；
  - `record_admin.record_detail` 暴露每个节点的 `depth`，嵌套转发段给出 `id` 与 `expanded`（后一个节点层级更深即「已展开」），不再落到 `raw`；
  - 页面按层级缩进并标「嵌套第 N 层」，嵌套转发段显示「[嵌套聊天记录 <id>：内容已展开在下方]」；
  - `node_payloads`（重建转发）对已展开的嵌套转发不再补一条 `[嵌套聊天记录]` 占位，避免重发出来重复一段；
  - **老存档也能看**：v1.20.2 之前存的记录根本没写 depth，页面按结构推测 —— 只有一个 `forward` 段的节点是嵌套转发的容器，它后面到下一个容器之前的节点算作那次转发的内容 —— 并在详情底部注明「这是推测」，不冒充存档事实。

### Verified

- 新增回归用例 `test_record_nested_forward_depth_and_media_counts`（depth 落盘、详情暴露 `expanded`、重建不留占位、图/视频分别计数）；冒烟 170 → 171 全绿。
- `node --check pages/dashboard/app.js` 通过。

## v1.20.1

修一个「存档时**根本没去下**，点补齐又立刻能补」的真 bug —— 不是 QQ 抽风。

### Fixed

- **关掉「生成渲染图」时，合并转发里的节点图从来不下**：`_save_forward` 只有在要画渲染图时才调 `_download_forward_images`，而存档的 `_archive_image` 只从那份预下载结果里取字节；取不到就记 `download_failed`。于是 `forward_render_image: false` 的用户，所有转发内嵌图都显示「缺失」，点「补齐缺失媒体」又瞬间补上（补齐是真的去下载了）。现在节点图**总是**预下载 —— 存档本来就要这些字节，跟画不画渲染图无关。
- 预下载是按「组件侧 url」做键的，节点原始 `data.url` 与它未必是同一个字符串（appid/rkey/协议头差异），命中不了也会漏。现在存档时没命中就直连补取，并记一行日志 `archive: N 张节点图没命中预下载结果，存档时直连补取`。
- 回归用例：`forward_render_image=False` 时 `_save_forward` 仍会预下载、且预下载未命中时存档能直连补到（冒烟 169 → 170）。

### 升级后

- 之前那些「缺 N 项媒体」的聊天记录，多数媒体 url 已过期（rkey 有时效），补不上就仍然缺；能补的用「补齐缺失媒体」或 `/warden 补媒体 all`。

## v1.20.0

两件跟「同一份内容被转两次」有关的事：**内容去重不再被 rkey 干扰**，**当时没下成的媒体可以补**。

### Added

- 存档详情多了「补齐缺失媒体 (N)」按钮：按记录里还留着的 url 重试下载缺失的图片/视频，只补缺口（已经有的字节不重下），结果写回 `record.json`。补不上（rkey 过期）时会如实说「仍有 N 项缺失」。
- `/warden 补媒体 [list|<record_id>|all]`：命令版同一条链路（`list` 看谁还缺、`all` 一次补最近 20 条）。
- `record.backfill_record` 是上面两者的底层：`load_record` → 还原 OneBot 节点形状 → 走与存档同一个 `_backfill_media`。

### Fixed

- **同一条转发转两次不会再存成两条记录**：内容指纹（`origin_sha256_norm`）忽略 `url`/`path` 这类每次转发都会变的字段，只留 `file` 等内容标识。以前 dedupe 比的是含 rkey 的原始 JSON，rkey 一变就「认不出」，同一份内容被存成多个目录。
- 存量条目没有新指纹字段时，`find_duplicate` 会**现算**一遍归一化指纹再比 —— 升级后老记录照样能被复用命中，不会因为加了新字段而「失联」。
- 媒体下载失败不再只留一句 `download_failed`：插件日志会打「存档图片/视频下载失败（N 次尝试）: 具体异常」，下次能一眼看出是 HTTP 400（rkey 过期）还是超时。

### 说明

- 指纹只影响「要不要复用旧条目」，不影响 `record.json` 里保留的原始段数据（url 仍原样留着，重发时优先用 `assets/` 字节）。
- `list_records` 摘要新增 `missing_count`，命令与页面都靠它判断「缺几项」。

### Verified

- 冒烟 `ALL OK (… total=169)`：新增 3 条用例（换 rkey 仍复用 / 内容变了不误合 / 老条目缺 norm 字段也能匹配；`backfill_record` 只补缺口且幂等；插件入口 `record_backfill` 走自己的 fetcher 表）。

## v1.19.0

聊天记录存档现在会把**节点里的视频字节**一起落盘 —— 以前视频段只剩一段原始 JSON（`url` 是带 rkey 的临时链接），页面/重发都只能看到 `[video file=... url=...]` 文本，链接一过期就彻底没了。

### Added

- `record.capture` 新增可选的 `fetch_video` 回调（同一条「url → 下载 → assets/<sha16>.<ext>」链路）；落盘后资产带 `kind: "image" | "video"`，`missing` 条目也带 `kind`，页面能区分「图缺失 / 视频缺失」。
- 魔数识别视频扩展名（ISO BMFF `ftyp` → `.mp4`/`.mov`、`RIFF…AVI `、EBML → `.webm`），下不到时仍退段里的文件名。
- 复用同内容旧条目时顺手**补齐**它缺的媒体字节（`record._backfill_media`），老条目重新存档一次即可补上视频（url 仍有效时）。
- 存档详情里视频段映射到资产（`asset`/`bytes`/`sha16`），页面显示「▶ 加载视频（10.0 MB）」，点一下内联播放（base64 走同一个鉴权 `thumb` 接口，单条上限 64 MB）；`thumb` 的 MIME 表补了 mp4/mov/webm/mkv/avi。
- `/warden 存档` 回执与 `重发 list` 改称「媒体」（视频单列）；列表缺失徽标改成「缺 N 项媒体」。
- 重建转发（页面「重建转发」/ `/warden 重发`）带视频：`record_admin.node_payloads` 用 `base64://` 发 video 段，没有字节时留 `[视频]` 占位；AstrBot 组件路径 `record.build_nodes` 用 `Video.fromFileSystem`。
- 渲染图里视频/语音段以前会整段消失，现在留 `[视频]`/`[语音]` 占位（`warden/forwarder.py`）。

### 说明

- **老存档里的视频可以「重新存档一次」补回来**：以前没下字节，只有原始 JSON；把同一条合并转发再存档一次（内容指纹命中 → 复用原条目）时，`_backfill_media` 会按记录里的 url 把缺的图片/视频补进 `assets/` 并写回 `record.json` —— 前提是 url 还没过期。补不上（rkey 已失效）时详情页显示「视频：这条存档建立时还没存视频字节」，重建时用 `[视频]` 占位。
- 视频下载仍受 `max_file_size_mb` 限制（默认 100 MB），失败进 `missing`（`download_failed`），不影响整条存档。
- 关掉「保存视频」开关时不传 `fetch_video`，行为与旧版一致（视频段保留原始 JSON、不记缺失）。

### Verified

- 冒烟 `ALL OK (… total=166)`：新增 3 条用例（视频落盘 + 详情/重建映射；没给 `fetch_video` 时静默跳过；复用旧条目时补齐视频并写回 record.json）。

## v1.18.2

修一个会**误删仍被引用的文件**的隐患，并把「同一份字节被多条消息引用」在页面上标出来。

### 升级前必读

1. **启动时会把索引里的路径写法归一**（一次性，幂等）：以前首次落盘写的是 `…\961583271/2510536451\x.gif`（`os.path.join(root, rel)`，rel 里是 `/`），内容去重命中时写的是 `os.path.normpath` 后的全反斜杠写法。同一个文件两种字符串 → 按字符串精确计数的「还有几条记录引用它」会漏数。本库实测 6,703 行里 5,280 行是 `/` 写法、1,423 行是反斜杠写法，**932 个文件同时存在两种写法**。启动日志会打一行 `index: 归一化 N 条历史路径`。
2. 归一化只改字符串，不动文件；会保留既有文件名与目录结构。

### Fixed

- **删共享文件时不再误删字节**：`Storage.save` 首次落盘也返回 `os.path.normpath` 后的路径，`AssetIndex.open()` 把存量行幂等归一，`count_path` / `forget_path` 的入参同样归一。此前删掉「全反斜杠」那一行时，`count_path` 数不到同文件的「混合写法」行 → 引用归零 → 直接删文件，另一行立刻变成「缺失」。
- `index.record` 写库前统一 `normpath`，新产生的行不会再出现两种写法。

### Added

- `index.list_assets` 每行带 `shared`（同一个物理文件被几条索引引用）；存档页的扁平文件条目在 `shared > 1` 时显示「共用 N 条」标签 —— 以前两条一模一样的条目看着像重复落盘，其实是一份字节被两条消息引用（内容去重只省磁盘，不合并消息）。

### Verified

- 冒烟 `ALL OK (… total=163)`：新增一条用例覆盖「首存与去重写法归一 + count/forget/shared 一致」。

## v1.18.1

纯重构，行为不变：把三个装不下一屏的函数拆开，并把最后一处「吞异常」收进一个边界。没有新增/删除功能，没有配置项变化，不需要数据迁移。

### Changed

- `main._save_forward`（305 行、六层嵌套）拆成 `_forward_nodes`（拉取 + 展开节点）/ `_forward_render_enabled`（画不画）/ `_fetch_forward_avatars` / `_download_forward_images`（返回 `blob_by_url` / `node_imgs` / url 数与失败数）/ `_archive_forward`（条目目录，返回 `rid` 与 `archived`）/ `_save_forward_render` / `_save_forward_json` / `_index_asset`（统一写索引行），主流程只剩「取节点 → 决定画不画 → 下载 → 存档 → 落盘 → 自动转发」。
- `main.on_group_message`（133 行）拆出 `_gate_components`（类型开关门控，仍在下单之前，免得「类型没开」被记成下载失败）、`_event_identity`（platform/group/sender/msg_id/ts 收一处）、`_save_media`（索引复用 + 并发下载 + 落盘），用两个 `NamedTuple`（`_Gated` / `_EventIds`）传值。
- `main._recover_original_urls`（83 行）拆出 `_match_raw_ref`（先按标识配对、配不上再按顺序）与 `_apply_raw_ref`（回填 url/fileid/raw_file/name/size）；键归一化成模块级 `_media_keys` / `_ref_keys` / `_comp_keys`。
- `policy` 里四处「宿主 getter 取不到就静默跳过」收成一个 `_try_call` —— 吞咽只留这一个边界，且写明了为什么可以吞（拿不到就回退 `message_obj`）。
- `main._local_cache_names` 的 `urlparse` 失败落成显式「空候选」（不再 `pass`）；`_build_previews` 里回执预览发不出去时记日志。
- `index._asset_filter` 不再按扩展名二次分类：v1.18.0 归一化后 `kind` 就是落盘真实类型，类型过滤只看 `kind` 字段。
- `upgrade._drop_junk_forwards` 合并重复的两层 `except`（一行脏数据不该中止整次清扫）。

### Verified

- 冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=31, total=162)`，与 v1.18.0 同数 —— 纯重构，断言一条没改。

## v1.18.0

去补丁化：历史遗留形态收敛成**一次**启动归一化，之后代码里不再有「为旧数据兜底」的分支。

### 升级前必读

1. **升级时会把 v1.16 之前多存的扁平渲染图删掉**（索引 `kind=forward_render`，本机 857 张 / 1.25 GB）。删之前**先打包**到 `<storage_root>` 旁边的 `warden_legacy_renders_v1.18.zip`（含 `manifest.json` 与每个文件）；日志会打出打包与删除数量。zip 可以留着核对，也可以直接删。
2. **条目里的 `legacy_forward.json` 会被删除**，`record.json` 里的 `legacy_file` / `legacy_migrated` / `legacy_source` 字段一并去掉（归一化后的节点已在 `record.json` 里，重发/重渲染不需要原始副本）。
3. **不再自动迁移「扁平聊天记录 JSON」**：v1.14.0 起启动时会 `migrate_flat_forwards()` 把 v1.12 之前的 `…_forward_json_*.json` 迁成条目；v1.18.0 删掉了这条迁移。还留在扁平目录里、未迁移的老 JSON 保持原样（页面里仍作为普通文件行可见），需要的话请在升级**前**用 v1.17.x 跑一次。
4. **`forward_image_engine` 只接受 `pil`**：playwright / wkhtml 的旧值从本版起是配置错误（回落默认 `pil`，日志里说明），不再「受理 + 告警」。
5. **归一化只跑一次**：闸门是 `<storage_root>/.schema_version`（内容 `2`）+ 索引 `PRAGMA user_version=2`。删掉 marker 会让它下次启动重跑（幂等，已删的东西不会二次删）。

### Removed（删掉的兼容分支）

- `record_admin.migrate_flat_forwards` / `reconcile_flat_renders` / `purge_orphan_renders`、`record.backfill_duplicates`、`record.LEGACY_FILENAME`、`main._promote_auto_forward_default`。
- 页面路由 `page/purge_legacy`（13 条路由）、`get_stats` 的 `legacy_renders` / `legacy_bytes`、「清理旧渲染图」按钮与「旧渲染图」类型标签。
- `record_admin.flat_item` 里的三类推断：`forward_json` → 聊天记录、`forward_render` → 旧渲染图、按 `.gif` 扩展名改类型。索引 `kind` 现在由写入方决定，读到什么就是什么。
- `config.from_raw_lenient` 的 `forward_render_mode` 派生（旧键直接忽略）、`forward_image_engine` 的旧值告警分支。
- `index.open()` 不再无条件把 `PRAGMA user_version` 写回 1（只升不降），否则 upgrade 的闸门会被抹掉。

### Added

- `warden/upgrade.py`：`SCHEMA_VERSION=2`、`marker_path/read_version/write_version`、`normalize_entries()`（补 `kind`、删 legacy 副本与字段）、`purge_legacy_renders(index, backup_zip)`（zip 里写 `manifest.json` + `files/<basename>`，按 path 归组删行，引用归零才删文件）、`run_once(storage_root, index, backup_zip=…)`（版本 ≥2 直接 `{"skipped": True}`）。
- `index.backfill_gif_kind()`（`kind=image` 且路径以 `.gif` 结尾 → `gif`）、`index.set_user_version(version)`。
- `main.initialize()` 里的一次性归一化调用 + 一行汇总日志；`storage_root` 旁边的 zip 备份。
- 空壳转发行清理：`kind=forward`、≤256 B、`.json`、`nodes` 为空的迁移残骸（本机 1 条）。

### Fixed

- **upgrade 闸门被索引冲掉**：`index.open()` 原来每次都 `PRAGMA user_version = 1`，把 upgrade 写的 2 覆盖回去；现在只在更低时写入。
- **备份 zip 里同名文件被写重**：不同群的落盘文件名按同一模板生成会撞名（本机 11 组），`zipfile` 允许写两条同名 entry、解压时只剩最后一条 —— 备份等于残缺。现在 arcname 撞名时加序号（`files/0001_<name>`），并有用例断言 zip 内名字唯一。
- **归一化堵住事件循环**：第一版在 `initialize()` 里同步跑，1.2 GB 备份让事件循环卡了 111 s（`Event loop delay` 告警）。现在 `await asyncio.to_thread(...)`，线程内另开一条 sqlite 连接（连接不能跨线程），升级期间消息处理不再停摆。
- **回执/索引里的 `forward_json`**：存档失败退回扁平 JSON 时 `Component.kind` 与 failure subject 都改回 `forward`，`reporter._KIND_LABELS` 删掉 `forward_json` 条目（内部类型名不再泄漏到回执）。

### Tests

- 冒烟 165 → **162 项**（Phase 9: 34 → 31）：删掉四个只测已删 API 的用例（`test_record_admin_migrates_flat_forwards`、`test_record_admin_reconcile_flat_renders`、`test_record_admin_purge_orphan_renders`、`test_plugin_promotes_auto_forward_default`），`test_plugin_warns_on_unimplemented_render_engine` 改成 `test_config_rejects_unimplemented_render_engine`（断言旧值算配置错误并回落 `pil`）。
- 新增 `test_upgrade_v18_normalizes_and_purges`：造老条目（无 `kind` + 三个 legacy 字段 + `legacy_forward.json`）、老 GIF 行、空壳行、两张旧渲染图（其中一张被两行共享）→ 断言归一化结果、zip 内容（manifest 3 行 / 2 个文件）、marker 与 `user_version=2`、二次调用 `skipped`。
- `test_plugin_skip_text_only` 忽略 `.schema_version`（归一化闸门不是素材）；`test_page_stats_and_kind_filter_use_real_counts` 改用规范 `kind`（`forward` / `gif`），不再依赖扩展名推断。

## v1.17.3

两个独立的小修：样式页也被套上同样的固定布局；自动转发不再对「复用旧条目」的重复内容再发一遍。

### Fixed

- **样式页（pages/style）也有外层滚动条 / 底部边距不齐**：v1.17.2 只修了存档页，样式页还是 `sticky` 顶栏 + `main` 底部 28px + 预览 `max-height: calc(100vh - 200px)`，控件栏一长就顶出整页滚动条，右栏下方留白。现在与存档页同一套：`html, body { height: 100% }`、`body` 是 `flex column` + `overflow: hidden`、`main` 吃掉剩余高度、`.panel` 是 `flex column`、`.panel.controls` 与 `.preview-body` 各自 `overflow: auto`；窄屏把两栏改成 `minmax(0,45%) / minmax(0,1fr)` 两行。
- **同一段内容在源群被再发一次时，自动转发会往目标群再发一条**：存档层对「同群同内容」复用原条目（`reused`），但 `_save_forward` 仍会调 `_auto_forward` —— 目标群根本没收到新消息，等于重复刷屏。现在 `archived.get("reused")` 为真时跳过自动转发。

### Tests

- 冒烟 164 → **165 项**（Phase 9: 33 → 34）：新增 `test_plugin_auto_forward_skips_reused_entry`（第一次保存 → 只调 `get_msg` + `forward_group_single_msg`；同内容再发 → 复用条目，`calls == []`）。
- 样式页布局用 Chrome headless（1400×900）截图核对：无外层滚动条，左右面板同高同边距，预览区内部滚动。

### 顺带清理

- `%TEMP%\wd_style_*` 旧预览临时目录 123 个（v1.17.0 修复之前遗留，总共 23 KB）已删除；新版本每次预览都会在 `finally` 里清掉自己的目录。

## v1.17.2

存档页（dashboard）布局修正：整页固定，滚动只在左右两个面板内部。

### Fixed

- **外层多出一条整页滚动条**：页面高度以前由内容撑开（`main` 只给 `padding`，列表/详情各自 `max-height: calc(100vh - 190px / 230px)`），只要内容多一点就顶出 body 滚动条，而两个面板的 `max-height` 又不一样 —— 右栏下方留出一截空白，左右边距看起来也不一致。现在 `html, body { height: 100% }` + `body { display:flex; flex-direction:column; overflow:hidden }`，`main` 用 `flex:1; min-height:0; overflow:hidden` 吃掉剩余高度，`.list-pane` / `.detail-pane` 都是 `display:flex; flex-direction:column; min-height:0`，列表与详情正文用 `flex:1; min-height:0; overflow:auto` —— 两块面板同高、同边距（`main` 的 `padding: 14px 18px` 四边一致，去掉了原来底部的 28px），滚动条只出现在列表 / 详情正文里。
- 顶栏不再 `position: sticky`（页面本身不滚了，sticky 没有意义），改成 `flex: 0 0 auto`。
- 窄屏（<860px）由「两栏堆叠 + 列表 `40vh`」改为 `grid-template-rows: minmax(0,40%) minmax(0,1fr)`，两栏仍然各自内部滚动。

### Tests

- 冒烟仍是 **164 项**（纯静态样式改动；用 Chrome headless 在 1600×900 下截图核对：外层无滚动条，左右面板底边齐平、上下留白一致）。

## v1.17.1

更正 v1.17.0 的一个误判：**跨群「原地转发」并非必然失败**。

### 升级前必读（行为/配置变化）

- **`auto_forward_mode` 默认回到 `native`**：v1.17.0 依据「跨群实测必回『消息不存在』」把默认改成 `rebuild`，并让自动转发恒用重建 —— 结论是错的。读 NapCat 源码确认：`get_msg` 与 `forward_group_single_msg` 都走 `getMsgIdAndPeerByShortId()` 查**进程内短 id 缓存**（`msgIdMap` / `msgDataMap`，LRU 5000 条，重启清空），与目标群无关 —— 缓存里有就能保气泡/挂件/头像发到任何群，缓存里没有才回「消息不存在」。现在自动转发**先 native、失败自动回退 rebuild**；「没启用 + 无目标群 + 仍是 `rebuild`」的旧配置启动时静默改回 `native`（日志 `auto_forward_mode: rebuild -> native`）。
- **页面「转发原消息」不再按目标群置灰**：能不能点只看这条有没有可用的原消息 id；跨群同样可以保装扮，失败时仍会自动回退重建并在回执里说明。
- **`native_unavailable` 恢复「失败即标记」**：失败原因只与短 id 缓存有关（重启 / LRU 挤掉 / 消息过期），跟发去哪个群无关，所以不再区分「是否原群」。

### Changed

- `main._auto_forward`：`native` 优先（`get_msg` 核验 + `forward_group_single_msg`），失败才 `rebuild_forward`；成功日志分别记 `auto forwarded: <群>(native)` / `(rebuild)`。
- `main._promote_auto_forward_default`：反向迁移 `rebuild → native`（条件与 v1.15.0 相同：未启用、无目标群、值仍是旧默认）。
- `warden/record_page.py`：`resend` 去掉 `same_group` 判定。
- `pages/dashboard/app.js`：`syncNative()` 只按 `can_native` 决定按钮状态，动作栏说明文案同步改写。

### Tests

- 冒烟仍是 **164 项**（改的是断言，不是数量）：`test_plugin_auto_forward_native_first_then_rebuild`（native 成功 → 只调 `get_msg` + `forward_group_single_msg`；native 失败 → 再调 `send_group_forward_msg`）、`test_plugin_promotes_auto_forward_default`（`rebuild → native` 的三种例外）、`test_plugin_resend_native_failure_marks_unavailable`（失败即标记，与目标群无关）。

## v1.17.0

一次针对生产数据的审计修复轮：删文件不再连坐、文本去重不再认错人、跨群转发不再毒化条目、旧的扁平渲染图终于能看见也能清掉，外加几处口径/性能/UI 状态修正。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=33, total=164)`。

### 升级前必读（行为/配置变化）

- **`auto_forward_mode` 默认改回 `rebuild`**（**v1.17.1 更正：误判，已恢复 native；原地转发与目标群无关，取决于 NapCat 短 id 缓存**）：自动转发的目标群按定义就是别的群，而 QQ 的原地转发（native）按**原群**找消息 —— 跨群实测必回「消息不存在」。v1.15.0 把默认改成 native 后，每次自动转发都会白打一次 `get_msg` + `forward_group_single_msg` 再回退。现在自动转发**恒用重建**；配置里写 `native` 仍受理（只记一条说明），「没启用 + 无目标群 + 仍是 native」的旧配置会在启动时静默改回 `rebuild`。
- **新增 `save_voice`（默认 `false`）**：语音以前不在开关表里，而 `kind_allowed()` 对未知类型一律放行 —— 六个开关全关，语音照样落盘。现在它有独立开关；**认不出的类型默认不保存**（以后新增的类型不会再被静默存下）。
- **纯文本去重按发送人收窄**：以前只比正文指纹 —— alice 发「666」、bob 也发「666」，第二条会复用 alice 的条目（发送者/时间全错，渲染图还写进 alice 的目录）。现在纯文本要求 `kind=text` **且同一发送人**才算重复；聊天记录仍按内容+群比对。
- **老的扁平转发渲染图单列「旧渲染图」**：v1.16 之前每次转发额外落一张 `*_forward_image_*.png`（索引 `kind=forward_render`）。生产库 857 张 / 1.25 GB，与任何条目 msg_id 都不重叠，以前混在「图片」里既点不开也筛不掉。现在单独一类，统计栏有「清理旧渲染图 N 张」按钮（只删这批无条目的副本；条目里的 `render.png` 不动）。
- **`/warden prune` 加 `--records`**：默认仍只删索引行；要删 `_records/` 条目目录必须显式加 `--records`。不带时回执会告诉你「另有 N 条存档未动」。

### Fixed

- **删一条共享图会连坐删别人 10 条记录**：`record_page.delete` 以前先 `os.remove`、再 `forget_path(该 path 全部行)`。Storage 按内容去重，生产库 284 个路径被多行共享（最多 11 行）—— 删一张图 = 文件没了 + 该路径全部索引行没了。现在按行 id 删，文件仅在「没有别的行引用」时才 unlink；页面提示「文件仍被 N 条记录引用，未删除」。
- **`resolve_flat` 前缀可绕过**：`rel.startswith("_records/")` 对 `"./_records/<rid>/render.png"` 无效 → 页面能读、也能删存档内部文件。改为先 `os.path.normpath` 再判首段与 `..`。
- **自动转发 native 永远失败**：见上（恒用重建）。**v1.17.1 更正：不是永远失败，只是短 id 不在缓存里时失败。**
- **跨群 native 失败永久毒化条目**：`record_page.resend` 只要错误含「消息不存在」就写 `native_unavailable` —— 而跨群失败也是这句。现在只有「发回原群」失败才记（**v1.17.1 更正：失败与目标群无关，恢复失败即标记**）；原消息还在的条目不会再被标成「原消息已失效」。页面换目标群时「转发原消息」也会跟着置灰。
- **纯文本条目污染扁平索引**：`_save_text` 曾往索引写 `kind="text"` 行，页面以「磁盘文件」列出，而 `resolve_flat` 拒读 `_records/` → 点不开、删不掉，去重复用时同一路径再加一行。现在不写扁平行。
- **纯文本回执没有预览**：`_build_previews` 只挑 `forward_render`/`image`，text 结果永远不出图。现在把 text（`render.png`）纳入，优先级 渲染图 > 纯文本 > 图片。
- **回执印内部类型名**：`类型: forward_render×1` → 翻成「聊天记录渲染图×1」等中文标签（成功/失败明细同样）。
- **重建转发丢原时间戳**：AstrBot 的 `Node.to_dict` 不发 `time`。`/warden 重发` 重建、页面与自动转发统一走 OneBot `send_group_forward_msg`，`node_payloads` 补上 `time`。
- **样式预览泄漏临时目录**：`_render_preview` 每次 `mkdtemp("wd_style_")` 从不删除（实测积 108 个）。现在 `finally` 清理。
- **分页翻不到第 2000 条之后**：`_flat_rows` 固定只取 2000 行且不传 offset，再由合并列表切片 —— `offset=2050` 返回 0 条而 total=5012。现在按过滤条件取全量再切片（页面每页 200）。
- **下载并发闸门被迟到缓存占住**：`Semaphore` 包着整个 `_download_one`，包括「等 5s/20s 再查本地缓存」的 sleep —— n 张死链图最坏 `ceil(n/4)*25s` 才出回执。现在闸门只包网络尝试。
- **页面按钮状态**：`doResend` 的 `finally` 把所有按钮重新 enable，渲染时置灰的「转发原消息」又会活过来。现在成功后重新拉详情按真实状态重画。
- **`/warden stats` 看不见存档**：只报索引 7490 行、不含 59 条条目（9 MB）；`total_bytes` 是 `SUM(size)`，共享文件被重复计（4.90 GB vs 磁盘 4.56 GB）。现在同时报存档条目/占用与「去重后字节」。

### Tests

- 冒烟 152 → **164 项**（Phase 9: 21 → 33）。新增：共享文件按行删 + `resolve_flat` 防绕过；纯文本按发送人去重；纯文本回执预览；分页 offset 2050；旧渲染图清理（带条目的 msg_id 保留）；样式预览临时目录清理；`node_payloads` 带 time；~~跨群 native 失败不毒化条目~~（v1.17.1 已按新结论改写）。
- 把 4 个「定义了却没注册」的用例接回 PHASE9（emoji 混排渲染、raw url 覆盖、raw 无 http、按身份配 ref），它们此前从未被执行。
- 改：`test_plugin_auto_forward_native_falls_back_to_rebuild` → `..._cross_group_uses_rebuild`（断言不再发生 native 尝试）；`test_plugin_promotes_auto_forward_default` → `test_plugin_demotes_auto_forward_default`（v1.17.1 又改回 promote 语义并按新断言重写）；回执类型断言由 `image×1` 改为 `图片×1` 等中文标签；路由数 13 → 14（`purge_legacy`）。
## v1.16.0

渲染开关布尔化：三态 `forward_render_mode`（`image`/`json`/`both`）收成一个开关 `forward_render_image`。功能上只有一处变化：**不再落扁平 JSON 副本**。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=21, total=152)`（项数与 v1.15.0 相同 —— 改的是测试内容，不是数量）。

### 升级前必读（配置变化）

- **`forward_render_mode` 删除，换成 `forward_render_image`（布尔，默认 `true`）**：旧配置里的值会被自动换算 —— `json` → `false`，`image`/`both`/缺省 → `true`。换算只在**新键不存在**时发生，所以手动设过新键就以你为准。
- **不再产生扁平的 `forward_*.json`**：条目目录里的 `record.json` 一直就是完整结构化数据，`forward_render_mode=json` 当年多存的那份扁平 JSON 只是重复副本（还让「聊天记录」在分类页里多出一条看不出区别的行）。现在关掉渲染 = 条目里只留 `record.json`。
- **一条转发只回一个结果**：以前 `both` 会同时算「聊天记录 ×1 + 渲染图 ×1」，现在无论开不开渲染都只算一条（渲染开时结果类型是 `forward_render`，关时是 `forward`）。回执里的分类计数会因此变小，这是对的。

### Changed

- `warden/config.py`：`forward_render_mode: str` → `forward_render_image: bool = True`；三态校验删除；`from_raw_lenient` 增加旧值派生（`legacy_mode = raw.pop("forward_render_mode", None)`，新键缺失时 `str(legacy_mode).lower() != "json"`）。
- `main._save_forward`：`want_image` 只读新开关；`want_json` 整段删除。`forward_render_mode` 那套「不画图就额外存扁平 JSON」的分支改成：条目存在时直接回 `record.json` 的 `AssetResult`，**只有存档失败**（PIL 缺失 / 目录不可写）才退回扁平 JSON 兜底并写索引。
- `_conf_schema.json`：⑤ 组里 `forward_render_mode` 换成 `forward_render_image`，hint 说明「旧配置 json 自动映射成关闭」。

### Fixed

- **内联节点存档成空壳**：合并转发的节点有两种形态 —— 裸节点（`{user_id, nickname, content}`）与包装段（`{"type":"node","data":{sender, message}}`）。`record._segments()` 只认前者，遇到包装段当空节点，于是「整条转发只有内联节点」时存档内容为空。新增 `record._payload(node)` 统一剥一层，`_segments` / `node_identity` / `dry_nodes` / `capture` 的 `time` 全部走它。
- **节点昵称缺顶层回退**：`node_identity()` 只读 `sender.card/nickname`，而某些实现把昵称放在节点顶层 → 名字退化成 QQ 号本身。现在依次取 `sender.card` → `sender.nickname` → 顶层 `card` → 顶层 `nickname` → QQ 号。

### Tests

- 重写：`test_plugin_e2e_forward_saved_as_json_sidecar` → 断言条目 `record.json` + 无扁平 JSON；`test_plugin_e2e_forward_both_modes` → `test_plugin_e2e_forward_entry_render_and_record`；`test_plugin_e2e_forward_too_large_falls_back_to_json` → `..._falls_back_to_entry`。
- 新增 helper `_archived_records(root)`，嵌套展开 / 昵称来源两个测试改读条目而不是扁平 sidecar；schema 断言改为 `forward_render_image is True` + 旧值派生（`json` → `False`）。
## v1.15.0

配置页重做：把 9 组配置按「用户想事情的顺序」重排，砍掉重复与假选项，默认值按实测结论调整。功能行为除下面两处外不变。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=21, total=152)`。

### 升级前必读（行为/配置变化）

- **`auto_forward_mode` 默认 `rebuild` → `native`**：开自动转发时优先「转发原消息」保气泡/挂件。注意实测结论：native **只对原群可靠**（**v1.17.1 更正：这是误判 —— 跨群同样可用，取决于 NapCat 短 id 缓存**），而自动转发的目标群通常就是别的群 —— 每个跨群目标会先失败一次再自动回退 rebuild，回执里仍会说明。想省这一次请求就手动改回 `rebuild`。
- **`forward_image_width` 删除**：它一直是死旋钮 —— `main.initialize()` 先按它构造 Forwarder，紧接着 `_apply_render_style()` 用 `style_width` 覆盖。画布宽度只有 `style_width` 一个真源。旧配置文件里留着这个键不会报错，只是不再被读。
- **配置分组名保持不变**：组的中文标题改成了「⑤ 聊天记录渲染」，但 `_conf_schema.json` 的**内部键仍是 `forward`**。实测（2026-10-28）：把组键改名后重载，AstrBot 会按新 schema 重写配置文件，旧组里的值直接消失 —— 组键是配置的存储地址，改标题可以，改键就是丢配置。
- **`auto_forward_mode` 旧默认值会跟着升级**：若「自动转发没启用 + 目标群为空 + 值仍是旧默认 `rebuild`」，启动时静默改成 `native` 并写回配置（这三种条件下改了没有任何行为变化）。一旦你真开过自动转发，那 `rebuild` 视为你的选择，不再动；日志 `auto_forward_mode: rebuild -> native`。

### Changed

- **九组顺序**：① 监听范围（`filter`）→ ② 保存哪些类型（`save_types`）→ ③ 存储与索引（`storage`）→ ④ 下载与重试（`download`）→ ⑤ 聊天记录渲染（`forward`）→ ⑥ 渲染样式（`render_style`）→ ⑦ 自动转发（`auto_forward`）→ ⑧ 保存后的回执（`reply`）→ ⑨ 日志（`logging`）。每组标题直接写序号，扫一眼就知道从哪开始。
- **hint 全部改成「这一项现在会怎样」**：删掉版本考古（`v1.9.0+` / `v1.7.1: 5 → 10` / 「关闭后行为回到 v1.8.x」）与实现细节，替成一句可判断后果的话（例：白名单空列表 = 不存；`native` 缓存里没有原消息时自动回退）。
- **组内顺序按使用频率**：`reply_preview` 在 `reply_to_original` 之前（前者更常开）；下载组把超时/重试/并发放前面，补救链放最后；渲染组把「存成什么」放最前，头像参数在后。
- **`forward_image_engine` 选项收成 `["pil"]`**：只有 PIL 实现，之前摆出 playwright/wkhtml 两个假选项只会让人白试。旧配置里的这两个值仍会被接受并打一条告警（不静默生效）。
- **`save_types` 里文档白名单紧跟 `save_files`**：先「存不存文档」再「存哪些扩展名」，不再隔开。

### Fixed

- **动图类型落盘时不该丢**：`main._store_dl()` 以前一律用 `comp.kind`（图片段恒为 `image`）写索引，于是「动图」分类只能靠页面拿 `.gif` 扩展名兜底、**动画 WebP 永远进不了「动图」**，而 `policy.blob_is_animated()` 写了却从没被生产代码调用。现在落盘后用字节复核：`comp_is_animated()` 判过的图，多帧记 `gif`、单帧（含静态 WebP）记回 `image`，`AssetResult.kind` 与预览路径也跟着真实类型。
- **hint 里三处「说了做不到 / 缺了关键后果」**：`save_gifs` 原来写「静态 WebP 下载后会归回图片」（当时其实没实现，现已实现）；`avatar_platforms` 没写「留空 = 哪个平台都不抓头像」；`doc_ext_whitelist` 的「空 = 不限」没提醒与 ① 的群/人白名单「空 = 不存」**语义相反**；`forward_render_mode` 改成说明白「条目里的 record.json 总是有的，json/both 那份扁平 JSON 是额外副本」。

### Tests

- 冒烟 150 → **152 项**（Phase 4: 26 / Phase 9: 21）：新增「schema 分组顺序 + 宽度旋钮唯一 + `auto_forward_mode` 默认 native」「旧默认值升级规则（未启用/无目标群/仍为 rebuild 才改）」；枚举断言跟着改成 `render` 组与 `["pil"]`。
## v1.14.0

「存什么 / 怎么存 / 怎么看」三件事一起做完：类型门控 + 六类开关，纯文本与聊天记录统一成「一个条目两种形态」，页面按类型分类管理并支持发到别的群，渲染样式搬进独立页面。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=25 phase5=27 phase6=4 phase7=12 phase8=3 phase9=20, total=150)`。

### 升级前必读（语义变化）

- **白名单模式下「空列表」= 不保存**：`match_mode: whitelist` 的 `target_groups` 为空即「一个群都不存」、`target_users` 为空即「一个发送人都不存」。旧版本把空列表当「不限」，所以这两项留空的用户升级后**会突然什么都不存**。要「这个群所有人都存」写 `target_users: ["*"]`；要「不设任何限制」把 `match_mode` 改成 `blacklist`（它的空列表仍是不限）。
- 六类开关 `save_images` / `save_gifs` / `save_videos` / `save_files` / `save_forwards` / `save_texts` 新增，其中只有 `save_texts` 默认 `false`（纯文本以前完全不进流程），其余默认 `true` —— 升级后默认行为与 v1.13 一致。
- 纯文本 / 聊天记录的落盘统一到 `_records/`：`entry.json`（纯文本结构）、`record.json` + `assets/`（聊天记录）、`render.png`（两者共用）。老的扁平 `…_forward_json_*.json` 不再新写，但页面与命令仍能读、能管。

### Added

- **六类保存开关**：在**下载之前**按类型丢弃（省带宽，也不再制造失败记录）；文档再叠一层 `doc_ext_whitelist`（扩展名白名单，可改）。空列表语义与白名单模式一致 —— 文档白名单为空即「不存文档」。
- **动图判定**（`policy.comp_is_animated` / `blob_is_animated`）：扩展名 + 字节魔数（GIF 帧数、WebP `ANIM`、APNG `acTL`），不依赖 PIL，所以 `gif` 开关在没装 Pillow 的宿主上照样准。
- **纯文本条目**：`record.capture_text()` 写 `entry.json`（保留 text / at / face / reply 原始段、发送者、时间），再用现有 Pillow 渲染器画一张 `render.png` 放进同一目录 —— 一个条目两种形态，页面里切换着看。
- **聊天记录双形态**：`record.capture()` 的 `record.json` 增加 `kind: "forward"` 与 `render` 字段；渲染成功后把同一张 PNG 经 `save_render()` 放进条目。
- **分类管理页**：类型标签（全部 / 图片 / 动图 / 视频 / 文档 / 纯文本 / 聊天记录）同时覆盖 `_records/` 条目与老的扁平落盘文件（`index.list_assets()` 按 `kind` 查，`record_admin.scan_items()` 合并两类）；右侧「结构化 / 渲染图」切换看同一张条目的两种形态；新增「打开目录」（`page/reveal` → `os.startfile`，仅 Windows，且只对 `storage_root` 下的目录生效）。
- **跨群转发 + 目标群下拉**：页面「发回」旁多一个带群名的目标群下拉（`page/groups` 读 `get_group_list`，默认这条记录的原群），可选「原地（保装扮）」或「重建（丢装扮）」；原地失败自动回退重建，并在回执里说明走了哪条路。
- **自动转发**：`auto_forward_enabled` + `auto_forward_mode: rebuild|native` + `forward_target_groups`（多群，默认空 = 不转发）。存档成功后自动发到各目标群：`native` 先试原地（跨群同样保装扮），失败回退重建；目标等于源群时跳过。
- **跨群「原地」不可用（实测修正）**：同一条存档发回**原群** `native` 成功、发到**别的群**回「消息不存在」—— `forward_group_single_msg` 是按目标 peer 找消息的。所以「转发原消息」只对原群可靠，跨群请用「重建转发」；`auto_forward_mode=native` 时每个跨群目标都会白试一次再回退。页面上的说明文案也改成了这个说法。**v1.17.1 更正：以上结论是误判（NapCat 源码按进程内短 id 缓存查找，与目标群无关），跨群原地可用。**
- **转发按钮文案改正**：以前叫「原地发回（保装扮） / 重建转发（丢装扮）」—— 两个都不准确：下拉可以选**别的群**，所以不是「发回」；「丢装扮」像在描述缺陷。现在整条动作栏是「**发到** [目标群 ▾] + **转发原消息** + **重建转发**」，下面一行小字说明差别（转发原消息保留气泡/挂件/头像/原时间戳，但要求原消息还在，过期会自动改用重建；重建随时可用但没有装扮），原消息不可用的那条按钮 disabled 且 `title` 说明原因。发完的回执也一起改了：以前是「已原地转发 / 已重建发送」，现在是「已转发原消息 → 群名 (群号)」/「已重建转发 → 群名 (群号)」，回退时第二行写「转发原消息不可用（…），已改用重建转发」；纯文本条目的拒绝文案也从「只有聊天记录能发回」改成「只有聊天记录条目能转发」。
- **老的扁平聊天记录迁成条目**：v1.12.0 之前聊天记录存成 `…_forward_json_*.json`，页面上只有一张文件卡片 —— 读不到节点、不能重渲染、也不能重建转发。启动时 `record_admin.migrate_flat_forwards()` 把它们迁进 `_records/`（本机实测 **55 条**）：节点结构照旧、图片 url 早已过期所以不下载、进 `missing` 并在页面显示「缺 N 图」，原始 JSON 复制成条目里的 `legacy_forward.json` 留底后删掉扁平文件与索引行。列表预览从 `.json` 文件名变成正文首句，详情里多一个「迁移自老存档」标签。幂等；坏 JSON（本机有 1 个 `nodes: null` 的旧 bug 产物）不迁不删。
- **空列表说真话 + 类型标签标「关」**：空态原来写「群里发图/文档会自动保存；合并转发与纯文本需要先开启对应开关」—— 这是 v1.13 的话，v1.14.0 起**六类全部可开关**，没有哪类是「自动存」的。现在 `page/stats` 顺带返回 `save_types`，页面据此说话：选中关掉的类型 → 「这一类的保存开关是关的（配置项 save_gifs），打开后才会存」；全部为空 → 「当前关闭的类型：纯文本、聊天记录」；一个都没关 → 提示先查 `target_groups` / `target_users`（白名单空列表 = 不保存）。类型标签上关掉的那一类标「· 关」并淡化。
- **老 GIF 自动归到「动图」**：v1.14.0 之前没有 gif 这个 kind，GIF 也记成 `image` —— 于是「动图」标签一个老文件都筛不出来，「图片」里却混着几百个 gif。现在 `index._asset_filter()` 按扩展名再分一次（`kind='image' AND path LIKE '%.gif'` 算动图，两边互斥不重复计数），`record_admin.flat_item()` 也把这类行显示成「动图」。
- **「原消息已失效」会被记住**：`page/resend` 的 native 转发失败且原因是「消息不存在 / 取不到原消息」时，往条目元数据写 `native_unavailable`（`record.set_meta()`），`record_admin._can_native()` 之后返回 False —— 页面把「转发原消息」置灰、头部显示「原消息已失效」，不再是点一次失败一次的陷阱按钮。网络抖动/权限这类偶发错误**不写**标记。
- **删除改成页内确认**：桌面版（Tauri）的 `window.confirm` 不保证弹得出来（弹不出来时「删除」会静默什么都不做）；改成页面自己的蒙层确认框（Esc / 点遮罩取消，焦点默认在「取消」）。
- **「加载更多」**：列表一页 200 条，底部按钮按页追加（`page/records` 本就支持 `offset`）；以前只写「显示前 N 条」，几千个老文件根本翻不到 —— 现在标题栏写「共 N 条 · 已加载 M」，不再和按钮自相矛盾。
- **「重渲染」**：条目详情多一个按钮，按**当前**渲染样式重画 `render.png`。两件事靠它：v1.14.0 之前存的条目本来就没有渲染图（页面上连标签都不出现），以及样式页改了颜色/字号之后旧图还是旧样式。节点与图片字节都在条目目录里，重画不碰网络（头像失败静默降级），路由 `page/render`。
- **同群同内容不重复建目录**：同一条合并转发被转两次，以前是两份目录、两份图片字节。现在 `record.find_duplicate()` 先按 `origin_sha256`（只归一化结构、不下图）在同群条目里找，命中就**直接复用原条目**：不建新目录、不下载图片，返回的 record 带 `reused` / `duplicate_of` / `duplicate_of_requested`（后者是这次本来要用的 record_id）；调用方写渲染图/索引时用复用那条的 id。**跨群不合并** —— 同一内容出现在别的群各存一份是对的（每群语境不同）。纯文本条目同理。老代码已经产生的重复目录不自动删，仍带 `duplicate_of` 标「内容重复」。
- **聊天记录的渲染图只住在条目里**：以前存档除了条目内的 `render.png`，还会经 `Storage` 再落一份扁平的 `forward_image_*.png` —— 同一张图两份字节，页面「图片」分类里还单独成一条，与「一个条目两种形态」自相矛盾。现在只要条目写成功就**不再落扁平副本**（回报里那条 `forward_render` 的 path 指向条目内的 `render.png`），只有存档失败时才回退到扁平落盘。启动时还会整理旧数据（`record_admin.reconcile_flat_renders`）：条目**已有**渲染图的，字节相同的扁平副本删行删文件；条目**还没有**渲染图的，把那张旧渲染图**收编**成条目的 `render.png` 再删行（老条目不用手动点「重渲染」）。被别的行共享的文件只删行不删文件。
- **渲染样式页**（`pages/style/`）：宽度 / 字号 / 圆角 / 背景·气泡·正文·次要用色 / 头像形状（圆 / 方）/ 时间格式 / 深色，右侧**实时预览**（含合成头像，才看得出圆形与方形圆角的差别）；保存写回插件配置并立刻 `Forwarder.apply_style()` 生效，不用重载。

### Changed

- `_conf_schema.json` 6 组 → 9 组：新增 `save_types` / `auto_forward` / `render_style`。
- `Forwarder` 的渲染参数从写死常量变成 `_RenderCfg` 字段，新增 `apply_style()`；`_avatar_mask(size, shape)` 支持方形圆角头像；时间格式可配（默认 `%Y-%m-%d %H:%M`）；`dark` 只替换**仍是默认值**的颜色，用户自己挑过的色不会被深色模式改掉。
- `record.list_records()` 现在混排 `forward` 与 `text` 两类条目，返回项统一带 `kind`。
- 页面后端路由 8 → 13：新增 `reveal` / `style` / `style_save` / `style_preview` / `render`。
- `record_admin.native_forward()` / `rebuild_forward()` 的结果带回 `new_message_id`（native 另有 `origin_message_id`），页面「发回」能拿到新消息的 id 去核对或撤回；重建路径以前把动作返回值整个丢掉，现在动作返回 `failed` 也按失败处理，不再静默当成功。
- `main.on_group_message` 的类型判定提前到下载之前，日志会写 `dropped: image=1 gif=0 …`，全丢时写 `skip: nothing to save`（以前是「下完了才发现不该存」）。

### Fixed

- **WebUI 里改的配置从 v1.10.0 起一直被静默忽略**：AstrBot 是按 `_conf_schema.json` 顶层键**分组嵌套**把配置传给插件的（`{"filter": {...}, "storage": {...}}`），而 `MediaWardenConfig.from_raw_lenient()` 只读顶层键 —— 所有分组内的值都没进过 dataclass，一直跑在默认值上（v1.10.0～v1.13.0 各项默认值恰好等于当时的用户配置，所以没人发现）。v1.14.0 的白名单反转把它变成实伤：日志里 `groups=0 users=0`，插件会「什么都不存」。现在 `_flatten_raw()` 把嵌套组摊平（**顶层同名键优先**，样式页写顶层的调用方仍有效）。
- 样式页保存写到顶层键会在配置文件里留下**同名的重复键**（分组里一份、顶层一份）；现在 `_persist_config()` 先把键定位到它所属的分组（如 `render_style`）再写。
- 纯文本消息以前被整体忽略（走一句 `nothing to save`）；现在按 `save_texts` 决定存不存，存就同时有 `entry.json` 与 `render.png`。
- 纯文本分支最初漏传 `event` 给回执，导致这条消息一条消息都不 yield（用户侧静默无回执）；现在两个分支统一走 `_emit_receipt(batch, msg_id, sender_id, event)`。
- `Config.doc_ext_allowed()` 以前只认扩展名，传文件名（如 `doc.pdf`）一律被拒；现在两种入参都收（`os.path.splitext` 归一）。
- **页面统计是一页的行数，不是总数**：顶部「扁平文件 2000」= `_flat_rows(limit=2000)` 的长度，而索引里实际 7547 行、占用 4.9 GB。现在 `index.summary()` 用 `COUNT(*)` / `SUM(size)` 出真数，`get_stats` 分别返回条目侧与扁平侧的 `flat_total` / `flat_bytes`。
- **类型筛选在取数截断之后才做**：`list_records` 先取 500 行再在 Python 里按 `kind` 过滤 —— 「聊天记录」只剩 8 条（真实 60）、「图片」永远是 500（真实 7491）。现在 `_flat_kinds()` 把类型翻成索引里的 `kind` 列表、在 SQL 里过滤，`scan_items(flat_total=…)` 用真实总数算 `total`。
- **占用口径**：`stats.bytes` 只累加 `_records` 的图片字节，漏了 `render.png` / `entry.json`，与磁盘差一个量级；现在 `_dir_file_bytes()` 把这两个文件也算进去。
- **详情缺 `has_render` / `render`**：列表项有「有渲染图」徽章，详情里却没有这两个键，页面的「结构化 / 渲染图」标签根本不出现、`render.png` 点不开；现在 `record_detail()` 与 `_entry_detail()` 都带上。
- **详情里的渲染图只剩一个破图标**：页面用 `.thumb[data-asset]` 找要取图的 `<img>`，而渲染图是 `.render-img` —— 于是它永远没有 `src`，只显示 alt 文案「渲染图加载中…」。现在按 `img[data-asset]` 选，渲染图与缩略图一样走 `page/thumb` 的 base64（桌面版 `<img>` 直连带不上 Authorization，恒 401）。
- **列表预览只取第一个节点**：一个全是图片的聊天记录在列表里就只剩「[图片]」，看不出是什么。现在 `_forward_preview()` 拼整条记录的文字，一个字的文本都没有时才退回「图片 2 · 表情 1」这种计数。
- **条目占用不算 `render.png` / `entry.json`**：`_entry_bytes()` 现在把两者计入（同一条记录图片 133 KB、条目实际 286 KB）。
- 蒙层大图以前只认缩略图 `img.thumb`，渲染图点了没反应；现在 `.render-img` 也能点开。
- **不可信 uin 照抄**：NapCat 的 `get_forward_msg` 给整条转发的**每个节点同一个 `sender.user_id`**（昵称却是各节点的），页面会显示成「两个不同昵称共用一个 QQ 号」。`record.uin_reliable(nodes)` 在同一个 uin 对上多个昵称时判为不可信并写进 `record.json`，页面此时只显示昵称。

### Tests

- 新增 Phase 9 共 10 项：类型门控与动图判定、纯文本条目往返、纯文本消息端到端落盘、禁用类型与坏扩展名被丢弃、聊天记录「一个条目两种形态」、分类扫描（含扁平文件）、自动转发到多群、`native` 模式失败回退 `rebuild`、`apply_style` 生效与头像形状、`update_style` 三元组与样式页路由。
- 冒烟 130 → **150** 项（Phase 9: 20；多出的十项是「宿主分组嵌套配置能被读到」「样式保存写进 schema 分组、不在顶层留重复键」「转发结果带回新消息 id」「详情带 has_render / uin_reliable」「统计与类型筛选用真实总数（700 图 + 60 个扁平 JSON + 3 个老 GIF，超出旧的 500 行窗口也能数对，GIF 只归「动图」不重复计入「图片」）」「同群重复转发只留一个条目目录（第二次复用原条目、不建目录也不下图，跨群仍各存一份）+ 插件按当前样式重画渲染图」「聊天记录的渲染图收进条目：扁平副本删行删文件、旧图收编成条目 render.png、共享文件只删行」「原消息失效时写 `native_unavailable` 并让 `can_native` 变 False，网络抖动不写」「老的扁平聊天记录 JSON 迁成条目（节点/缺图/留底/幂等/坏文件保留）」）。
- 本机实测（2026-10-28）：跨群「原地」转发一条 2026-10-27 的存档回 `消息不存在`（原消息已过期，`get_msg` 先拦下并说明原因），回退「重建」到另一个群成功（`send_group_forward_msg` 3 个节点）—— 这正是「原消息还在就用原地，没了就重建」的设计预期。

## v1.13.0

### Added

- **dashboard 存档页面**（`pages/dashboard/`：`index.html` / `app.js` / `styles.css`）—— 插件详情的「页面」里直接管存档：列表 + 按「发送者/文本/群号/记录 id」搜索 + 按群过滤、逐节点详情、图片缩略图（点开看原图）、**发回原群（原地·保装扮）** / **发回群（重建·丢装扮）** / **删除存档**
- `warden/record_admin.py` —— 存档读侧纯函数（`scan_records` / `stats` / `record_detail` / `resolve_asset` / `delete_record` / `node_payloads` / `native_forward` / `rebuild_forward`），页面与 API 只做 IO 转接，所以这些行为都能离线测
- `warden/record_page.py` —— 插件页面后端，注册 `/<plugin>/page/{stats,records,record,asset,resend,delete}`（`context.register_web_api`）；宿主没有这个 API 时安静跳过
- `.astrbot-plugin/i18n/{zh-CN,en-US}.json` —— 页面标题（「聊天记录存档」/「Chat records」）与显示名；没有它页面标题只会是目录名 `dashboard`

### Notes

- 缩略图走 `page/thumb`（base64 内联，桥接口自带鉴权）：`<img>` 直连后端带不上 Authorization 头，桌面版（Tauri）也不保证留 dashboard cookie；单张 >8 MB 不回 base64，只提示走下载
- 页面启动会先等宿主注入的 `bridge-sdk.js`（它排在 `app.js` 之后），最多 5s；等不到再提示刷新
- 页面**不做** QQ 气泡渲染；要保装扮仍只有「原地转发」一条路（原消息还在服务端才行）
- 页面后端只读 `_records/`，不写 `_warden.db`：删存档只删目录，索引里没有它的行

### Tests

- 冒烟 127 → 130 项（Phase 8: 3）：存档读侧六个函数（含路径越界/不存在的资产）、页面六条路由的注册与处理函数（含原地失败回退重建、鉴权取图、参数缺失）、`initialize()` 在有/无 `register_web_api` 两种宿主下的行为

## v1.12.0

聊天记录「存档 + 重发」：先摸清 QQ 合并转发到底能不能带装扮，结论决定了两条路都要做。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=25 phase5=27 phase6=4 phase7=12, total=127)`。

### Added
- **`warden/record.py`（新模块，260 行）**：自包含存档格式 `<storage_root>/_records/<record_id>/`，`record.json` 原样保留每个节点每个段的 `{type, data}`，`assets/` 存节点内图片**字节**（blake2b-16 命名、同字节只落一份）。API：`make_record_id()` / `capture()` / `load_record()` / `list_records()` / `build_nodes()`。单张图下载失败只记进 `missing`，不影响整条记录。
- **`/warden 存档 [msg_id]`**：引用（回复）一条合并转发即可存档。目标定位三级：命令行 `msg_id` → 当前消息自带的 forward 段 → 引用目标的 reply id；引用到的若是普通消息，再用 `get_msg` 找它里面的 forward 段。
- **`/warden 重发 [原地|重建] [record_id|list]`**：默认先「原地」——走 NapCat 的 `forward_group_single_msg`，让 QQ 自己把原消息再转发一遍；不可用（没有原消息 id / 目标不是转发 / 转发失败）则回退按存档重建合并转发，并在回执里说明原因。`原地` / `重建` 可用于强制指定单条路径。
- 命令总数 6 → 8。

### 实测结论（2026-10-27，决定了两条路的取舍）
- **重建节点不保装扮**：存档 → `Nodes` → `send_group_forward_msg`，合并转发里每个节点只剩昵称 + 内容，气泡 / 挂件 / 头像全丢。
- **原地转发保装扮**：`forward_group_single_msg` 由客户端把原消息再发一次，气泡、挂件、头像、时间戳全部原样。
- 由此定下存档的定位：**原消息还在就用原地（无损），原消息没了才用存档兜底（会丢装扮）**。
- **两套 id 不能混用**：`forward_group_single_msg` 只认 OneBot 短 `message_id`，段里的长 `msgId` 会回「无法找到消息」；`get_forward_msg` 认的正是长 id。

### Fixed
- 存档把**命令消息**的 `message_id` 当成原消息 id（`root_msg_id`），导致原地转发转发出去的是「/warden 存档」那句话本身。现在存那条合并转发自己的短 id；老存档会被 `get_msg` 核验拦下并说明原因（不再默默转发错对象）。
- 原地转发不再直接信任 `root_msg_id`：先 `get_msg` 确认它确实是合并转发，否则拒绝并提示重新存档。

### Tests
- 新增 Phase 7 共 12 项：存档往返（`record.json` + `assets/` + `build_nodes` 还原）、同字节去重、单图失败进 `missing`、列表倒序与坏目录容错、`image_ref` 规则、引用合并转发按 reply id 取节点、引用普通消息时 `get_msg` 找 forward 段、无引用时的可读提示、原地转发用短 id、老存档拒绝原地转发、默认先原地失败回退重建、转发失败错误原样回报。
- 顺带修掉一个与秒边界有关的 flaky 用例：`test_index_failure_stats_and_prune` 以前用 `int(time.time())` 当 cutoff，跨秒运行时会把刚插入的行判成「旧」；改成「一分钟前」。


## v1.11.0

第二轮审计：修掉三处「只在特定平台/特定路径才触发」的实伤与「失败不入库」的不一致，补 4 个新用例。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=25 phase5=27 phase6=4, total=115)`。

### Fixed
- **Windows 上回执永远不显示「路径:」行**：`warden/reporter.py` 里两处本该是 `"\\"` 的字面量写成了 `"\\\\"`（四个反斜杠 → Python 字符串是两个），`_lcp_dir()` 的 `rfind("/")` 在 `C:\data\...` 上返回 -1 → `storage_root` 恒为 `None`，只有 posix 路径的测试因此一直没红。现在归一用单个反斜杠，并且 `_lcp_dir()` 改成**按目录段**比较：按字符比较会把 `C:/a/x.jpg` 与 `C:/ab/y.jpg` 的公共前缀切成 `C:/a`（那是另一个文件名的前缀，不是目录）。
- **转发渲染失败不进 failures 表**：节点图下载失败与 PIL 渲染抛错这两条路以前直接构造 `AssetResult`，绕过 `_failed()` —— 没有 subject / attempts，`/warden fail` 查不到，与 README 的「失败分类入库」自相矛盾。现在两条路都走 `_failed()`（`kind="forward_render"`、`idx=ctx.idx+100`、`subject=forward_render:<msg>#<idx>`）。`_failed()` 相应新增 `kind` / `row_idx` 两个可选参数（同一条消息的第二个产物要按自己的 kind 与 idx 入库）。
- **`/warden list` 看不到 sha16**：list 自己拼一行，而 `/warden lookup <sha16>` 正需要这个值。现在 list 与 lookup 共用 `_format_asset_row()`（时间可读 + msg_id + sha16）。
- **16 位十六进制的 msg_id 查不到**：lookup 一见到 16 hex 就当内容指纹查，查不到直接报「未找到 sha16」。现在 sha16 未命中时继续按 msg_id 查。
- **file 段的 http 链接丢在 `file` 字段上**：image / video / voice 都走 `_media_url()` 兜底，只有 file 例外（`component.url` 为空 → 下载环节直接判失败）。
- **转发内联内容放在 `message` 键上时降级去网络重取**：归一化只读 `content` / `nodes`，而 `_forward_seg_inline_nodes` 早就认 `message`，两边不一致。

### Changed
- **落盘改为原子写入**：`Storage.save()` 与 `_write_pointer()` 走同目录临时文件 + `os.replace()`，崩在半路不会留下「看起来完整」的半截素材。
- 转发 JSON / 渲染图写索引时的 `except Exception: pass` 改为记日志（与 `_store_dl` 一致，失败不再无声）。
- `warden/index.py` 三个读资产的方法共用 `_asset_rows()`（`forward_meta` 一律解成对象），`recent()` 补 `id DESC` 兜底排序（与 `recent_failures` 一致，同一秒的记录顺序不再随缘）。
- `_conf_schema.json`：`match_mode` / `forward_render_mode` / `forward_image_engine` 补 WebUI 下拉 `options`，省得用户手敲字符串拼错。

### Removed
- `warden/config.py:EXPORT_FORMAT_VERSION = "1.0"`（0 引用，且与导出文件里写死的整数 `version: 1` 是两回事，只会误导）；导出格式版本改为在 `index.export_json()` 处注明。

### Docs
- 迟到缓存窗口的文档数字（4s/12s）与代码 `(5, 20)` 不符 —— `41bca31` 实测后就已扩到合计 25s；README / HANDOVER / schema hint 一并更正。

### Tests
- 新增 4 项（新 Phase 6）：Windows 反斜杠路径的公共目录、file 段 http 链接兜底、转发渲染失败入 failures 表、落盘无 `.part-` 残骸。
- 扩展 list / lookup / conf-schema 三项断言（sha16 可见、16-hex msg_id 可回退、枚举 options 与默认值可被 config 接受）。

## 2026-10-11 环境：NapCat 4.18.4 -> 4.18.37（**插件代码未改**）

**这一版不改插件。**它记录的是「转发节点图 100% 失败」的最终根因与修法：坏的是环境，不是代码。

- 现象：转发节点图 `2/2 node image(s) failed to download`，服务端原文
  `HTTP 400 {"retcode":-5503007,"retmsg":"download url has expired"}`。
- 取证：URL **生成后 0.6 秒**实测即过期；同一条 70 字符 rkey（`CAQSMEw42ic_…`）
  从 20:54:32 复用到 22:00:23（66 分钟没换）；NapCat 包里那两个 `CAQSKAB6JWEN…` 兜底常量是
  59 字符的老格式、从未被使用；两个内置 rkey 服务同时是坏的（`ss.xingzhige.com` 502、
  `secret-service.bietiaop.com` TLS 握手失败）。
- 根因：NapCat `4.18.4`（2026-05-22 的包）+ QQ `9.9.26-44498`（2026-05-28 装），五个月没升级；
  `getRkeyData()` 的过期判据把 `packetRkey[0]` 连写两遍（上游主分支至今如此），判不出过期 →
  永不刷新 → 新链接全带着同一条死 rkey。普通图片还有 QQ 本地副本可救，转发节点图没有，于是全灭。
- 修法：升级 NapCat 到 `4.18.37`。注意 `napcat.mjs` 里 rkey 逻辑**一字未改**，改的是
  `native/` 下 packet hook 那一层。
- 结果（22:29 起同一台机器）：`forward_render` 0.66s 保存成功；`http 400` / `render aborted` /
  `late cache still miss` 三项计数全部为 **0**；`failures` 表 1 小时新增 0。
- 附带踩坑（与插件无关，但会盖掉修好的状态）：`NapCat.Shell.zip` 没打包 `loadNapCat.js`，
  而 4.18.37 的 hook 去 QQ 的 `resources\app\` 找它 → 启动即
  `Error: Cannot find module '...\resources\app\loadNapCat.js'`；官方 `launcher.bat` 生成 loader 时
  不转义路径空格。本机因此保留 `C:\application\NapCat\start-napcat.bat`（自提权 + 自动补 loader）
  与 `C:\application\NapCatLoader\loadNapCat.js`，细节见 `HANDOVER.md` §1.-6。

## v1.10.0

一次**不改外部行为**的重构 + 补齐：删掉空转的配置与死代码，把重复实现合成一份，把只有测试在用的索引能力接回命令，并补上索引 schema 迁移。
冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=25 phase5=27, total=111)`。

### Removed（判明是死代码 / 空转）
- `warden/downloader.py`：`download(fallback=…)`、`astrbot_component_fetcher(refresher=…)`、`download_many()` —— 生产路径一次都没用过（真正的补救链在 `main._download_one`），留着只会让下一个读者以为还有第二套补救。
- `main.py:_get_media_url_candidates()`（v1.9.0 遗留、0 引用）、`import os as _os`。
- `warden/config.py:from_raw()`（只有测试在用；测试改用 `from_raw_lenient()`）。
- `warden/index.py:prune_failures_older_than()`（并入 `prune_older_than()`）、`warden/components.is_text_only()`、`warden/reporter.py:failure_summary()`。

### Changed（重复实现合一）
- **转发渲染**：`Forwarder.render()`（183 行）与 `_render_with_pil()`（153 行）有 134 行逐字相同（最长一段 34 行），`line_h` / `wrap_text` 各自定义两遍。现在两者只做「取字节 / 转 PIL」，然后调同一个 `_paint(nodes, node_pil)`；`line_h` / `wrap_text` 提升为模块级 `_line_h()` / `_wrap_text_simple()`。文件 608 行。
- **组件归一化**：`_coerce_obj` / `_coerce_one` 两份并行实现已经走散（`face` 只有 object 路径支持，image 的 meta 一边叫 `path` 一边叫 `summary`）。现在只有一条 `_coerce(t, _Fields, raw)`，`_Fields.of_obj()` / `_Fields.of_dict()` 只负责取字段。
- **失败文案**：`failure_summary()` / `failure_phrase()` / `format_batch()` 各自重算 fetch/save 分布。现在统一走 `BatchResult._split_fetch_save()` + `_reason_line()`；回执字符串一字未改（有测试锁着）。
- **迟到缓存重查**：媒体路径与转发节点图路径各自那一份 `for delay in _LATE_CACHE_DELAYS_S` 循环，合成 `_late_cache(probe, label)`。
- **QQ 本地缓存读取**：媒体（`_cached_copy`）与转发节点图（`_forward_local_cached`）各写了一遍「查缓存 → 读字节 → 记未命中」，合成 `_local_cache_read(names)`；两个调用方只剩「候选怎么来、命中包成什么」不同。

### Fixed（之前只是「看起来有」）
- `reply_to_original` **真正生效**：v1.9.x 里它 0 处生产引用，改什么都没用。现在回执走 `event.chain_result([Reply(id=msg_id), Plain(text)])`；适配器没有 `Reply`（或 msg_id 是 `nomsg` / `nonemsg` / `0`）时自动退化为纯文本。**默认值改为 `false`** —— 对一个从没生效过的开关改默认，等于让升级的用户突然被引用回复刷屏。
- `forward_image_engine` 不再假装支持 `playwright` / `wkhtml`：启动日志明确告警「未实现 — 实际使用 PIL 渲染」，**继续用 PIL**，而不是静默降级成 JSON（那会让用户以为是自己配错了）。
- `main.initialize()` 里那个 `except ImportError` 以前**永远不可能触发**（`warden/forwarder.py` 顶层不 import PIL，PIL 全在方法里）。现在真的探一次 `import PIL`，缺 Pillow 才降级 JSON —— 与文档一致。
- `warden/avatar.py` 顶层 `import aiohttp` 改为按需导入：缺 `aiohttp` 不再让整个插件 import 失败（`_smoke.py` 的 aiohttp 桩可以继续留着，但不再是必需品）。
- **转发节点内容为空**（`get_forward_msg` 返回空 / 节点全空）以前会把 `{"nodes": null}` 当成功落盘并回 `✅`。现在记为失败（`failure_kind="fetch"`，`subject=forward:<msg>#<idx>`），索引里看得见。
- 转发 JSON 分支的 `json.dumps` 移进 `try`：`TypeError` / `OSError` 不再逃出 `on_group_message`（以前那种情况下这条消息连回执都没有）。
- `_failed()` 的 `failure_kind` 改为 `classify_error(err)` 的结果：之前非 fetch 一律标 `"save"`，`"other"` 这个分类永远不可达。
- **索引查询不再跨群串味**：`_find_saved_asset()` 带上 `platform` / `group_id` 过滤（`find_by_msg()` 支持这两个可选参数）。
- **索引补 schema 迁移**：新增 `PRAGMA user_version` + `_ensure_columns()`（`PRAGMA table_info` 发现缺列就 `ALTER TABLE ADD COLUMN`），旧库升级不再因为缺 `forward_meta` / `subject` / `attempts` 而炸。注意**建表与建索引必须分开**（`SCHEMA_TABLES` / `SCHEMA_INDEXES`）：旧库上 `CREATE INDEX … ON failures(subject)` 会在补列之前执行而直接报错。

### Added
- `/warden lookup <sha16>`：16 位 hex 直接查内容指纹在索引里出现过几次（`find_by_sha()` 以前 0 个生产调用者）。
- `/warden fail` 表头补「累计 N 次失败」与「反复失败」top（`failure_stats()` 以前也是死代码，只列 count>1 的）。
- `/warden prune` 同时报失败记录数：`prune_older_than()` 返回 `{"assets": n, "failures": m}`（以前只报索引行数，失败记录被静默删掉）。
- `/warden list` 的时间戳改成可读的 `MM-DD HH:MM`（之前直接打 epoch）。
- `_conf_schema.json` 新增第 6 组「日志」，把一直存在却不可配的 `log_to_stdout` 放进去；`max_file_size_mb` 的 hint 不再暗示头像也受它限制（头像有独立的 5MB 上限）。

### Tests
- 冒烟 **102 → 111**（phase4 24 → 25，phase5 19 → 27）：`_conf_schema.json` 与配置字段双向对齐（漏配 / 多配都红）、组件 dict 与 object 两条路径归一化结果一致、`AvatarCache` 磁盘命中与 LRU 淘汰、`fetch_many` 单点失败不影响其他头像、旧库 schema 迁移（含幂等）、空转发记为失败、非 `pil` 引擎告警、`reply_to_original` 走 `Reply`、索引复用按 platform/group 隔离、`raw_message` 恢复链接按标识配对（两张图顺序颠倒也不会错领）。
- 依赖被删 API 的 4 个测试重写：`download_many` 并发 → 直接驱动 `_download_all`；「`get_image` 重取新链接」那条被 `get_msg` 覆盖后删掉；fetcher 本地回退测试不再传 `refresher`。

## v1.9.9

### 修正 v1.9.8 的结论：转发节点图不是「无解」，而是**晚一步**

v1.9.8 说「NapCat 从不下载转发节点里的图片字节」——**这句话只对了一半**。字节确实不在
NapCat 手里，但**接收端 QQ 客户端会在随后（几秒~几十秒）把它下载下来**，按 MD5 落到本地缓存。
插件失败的时刻正好卡在那个空档之前。

线上实测（2026-10-11 20:10–20:33，同一台机器）：

| 时刻 | 事实 |
| --- | --- |
| 20:26:57 | 转发节点图 `D867CE1671D3457B1A0534D240D4885E.jpg` 下载失败（`http 400`，节点 url 已失效） |
| 20:33:39 | 同一个 MD5 出现在 `…\3683351360\nt_qq\nt_data\Emoji\emoji-recv\2026-10\Ori\` |

即「插件查缓存时文件还不存在 → 网络那条 url 已经 400 → 客户端随后才落盘」。
（另有 `DCFF08A6…jpg` / `FE56BB22…jpg` 两个 MD5 至今全盘搜不到 —— 客户端到底没下那张，
这类仍然无解。）

同时确认**400 与插件无关**：AstrBot 自己的预处理管线在同一时段对同一域名独立报了
`Failed to download file from https URL host='multimedia.nt.qq.com.cn' … HTTP status code: 400`
（20:13:52、20:32:50、20:33:01、20:33:09、20:33:42）—— 是 napcat 交给我们的那条
rkey url 本身已经失效，不是本插件请求姿势不对。

转发失败率对照（按天，`forward fetched` vs `forward render aborted`）：
9-20~10-06 为 0%~4.8%，10-11 当天 `5 fetched / 5 aborted = 100%`。

### Added
- **迟到缓存重查**（`_LATE_CACHE_DELAYS_S = (5, 20)`）：三级补救全败后，隔 5s / 20s 再查一次（最初是 4s/12s，`41bca31` 复测后扩到合计 25s）
  QQ 本地缓存，命中即用（`late local cache hit after Ns`）。普通图片与转发节点图都接。
  只在前面的路全败时才走，代价 = `sum(_LATE_CACHE_DELAYS_S)`。
  测试里用 `_sleep_scale`/`_LATE_CACHE_DELAYS_S` 关掉真实等待。

### Diagnostics（保留：定位 400 的唯一线索）
- 转发节点图每个候选 url 失败时打全 url 与败因：
  `fwd dl candidate 1/2 failed: http 400 for '…' | url=https://multimedia.nt.qq.com.cn/download?appid=…`
  —— 以前只看到「几个失败」，看不到到底是哪条 url、哪个候选、什么状态码。

### Known gaps
- 客户端**从未下载**过的那张图，仍然救不回来（本地没有字节、rkey url 又已失效）。
- 转发节点图不做 `get_image`/`get_msg` 重取：节点 url 里没有 `file_id`，且 napcat 的
  `get_image` 只认它自己那份进程内 uuid map（见 v1.9.8 段的源码证据）。

### Tests
- 新增 `test_main_late_local_cache_retry`（首次 miss + 网络 400 → 迟到重查命中即成功；
  始终没有则仍抛原错）。冒烟 `ALL OK (phase1=22 phase2=16 phase3=21 phase4=24 phase5=19, total=102)`

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

> **原文已损坏**：这条记录在提交 `2e7d89b` 里就已经是一串 `?`（中文在写入时丢了，git 历史里没有干净版本）。以下按该提交的 diff 与 commit message 重述，措辞是事后补的，事实部分可逐条对 diff 核。

### 转发节点图取原图
- `gchat.qpic.cn` 的图 url 带 `is_origin=0`（缩略图），改成 `is_origin=1` 取原图；只对 `gchat.qpic.cn` / `multimedia.nt.qq` 两个域改写，其他 url 原样返回。换原图失败时仍退回缩略图 url。

### 渲染对齐
- 图片重采样改用 LANCZOS，按气泡内宽铺满；`max_image_width` 从 360 提到 720。
- 图片与时间戳之间加间距，不再压住时间戳。

### 节点获取
- 只有 forward id 被推过来时（内联 `nodes` 为空），通过 `get_forward_msg` 拉节点；两者都没有才降级 JSON 保存（修「空 JSON」）。
- 解析 `forward` / `node` 段时把 `forward_id` 记进 `meta`（napcat 的 `get_forward_msg` 需要它）。
