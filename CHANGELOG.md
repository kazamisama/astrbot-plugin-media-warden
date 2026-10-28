# Changelog

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
