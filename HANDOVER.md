# 交接文档 · astrbot-plugin-media-warden v1.17.2

## 1. 当前状态

- 项目：`astrbot-plugin-media-warden`（AstrBot 群聊素材守门人插件）
- 版本：v1.17.2（`warden/config.py`、`metadata.yaml`、入口 `@register` 已同步；v1.17.2 是存档页布局修正 —— 整页固定、左右面板各自滚动，见 §1.-9；v1.17.1 更正 v1.17.0 的「跨群原地转发必失败」误判 —— 原地转发与目标群无关，只取决于 NapCat 进程内短 id 缓存，**变化见下面 §1.-8**；v1.17.0 是针对生产数据的审计修复（共享文件按行删 / 文本去重按发送人 / 语音开关 / 旧渲染图清理 / 分页与统计 / 转发时间戳），见 §1.-7；v1.16.0 是渲染开关布尔化（`forward_render_mode` → `forward_render_image`，不再落扁平 JSON）+ 内联节点存档修复，**变化见下面 §1.-5**；v1.15.0 是配置页重做（九组重排 / 删重复旋钮 / native 默认），见 §1.-4；v1.14.0 是按类型采集 + 分类管理 + 样式页；v1.13.0 是 dashboard 存档页面见 §1.-2；v1.12.0 是聊天记录存档/重发见 §1.-1；v1.11.0 是第二轮审计修复见 §1.-0；v1.10.0 的重构/补齐见 §1.0）
- **环境（2026-10-11 起）：NapCat `4.18.37` + QQ `9.9.26.44498`** —— 这一条是硬依赖，
  详见 §1.-6；升级/重装 NapCat 后必须用 `C:\application\NapCat\start-napcat.bat` 启动
- 分支：`main`
- Git：v1.9.0（`04ed9e0`）/ v1.9.1（`9f735cc`）/ v1.9.2（`440fc22`）/ v1.9.3（`6ea8bf9`）/ v1.9.4（诊断版，未提交）/ v1.9.5（`683b720`）/ v1.9.6（`eb95122`）/ v1.9.7（`832b6b4`，诊断版）/ v1.9.8（`54315ae`）/ v1.9.9 / v1.10.0 / v1.11.0 / v1.12.0（`7396611` + `f3368ca`）/ v1.13.0（八笔小步提交）/ v1.14.0（`ba83bbf` → `4ef1afe` → `b8cb445` → `18e8544`）各一批提交（显式路径，未用 `git add -A`）
- 测试基线：`python -X utf8 _smoke.py` → `ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=33, total=164)`


## 1.-9 v1.17.2（存档页整页固定，当前版本）

用户的反馈（m05137）：「最外面多了一个滚动条，应该是单固定页面内嵌两个边距相同的框（目前就是这样，但是边距不一样，右边下面有空白），框内带滚动条」。

### 根因

- 页面高度靠内容撑开：`main` 只有 `padding: 14px 18px 28px`、`align-items: start`；两个面板各自用 `max-height: calc(100vh - 190px)`（列表）/ `calc(100vh - 230px)`（详情正文）—— 两个不同的常量，一旦内容多就把 body 顶出滚动条，同时右栏正文比面板短、底下留白，左右底边也不齐。

### 改法（纯 `pages/dashboard/styles.css`）

- `html, body { height: 100% }`；`body { display:flex; flex-direction:column; overflow:hidden }`。
- `.topbar`：去掉 `position: sticky / top / z-index`，改 `flex: 0 0 auto`（页面本身不滚了）。
- `main { flex: 1 1 auto; min-height: 0; overflow: hidden; padding: 14px 18px; }` —— 四边同距（去掉原来的底部 28px），`align-items: start` 删掉（默认 stretch，两栏同高）。
- `.list-pane`, `.detail-pane`：`display:flex; flex-direction:column; min-height:0`；`.kinds` / `.list-head` / `.detail-head` / `.detail-tabs` 全部 `flex: 0 0 auto`；`.list` 与 `.detail-body` 改 `flex: 1 1 auto; min-height: 0; overflow: auto`（删掉 `max-height`）。
- `.detail-pane` 的 `min-height: 240px` 删除（会顶破固定高度）；`.hint` 加 `overflow: auto`。
- 窄屏 `@media (max-width: 860px)`：`grid-template-columns: 1fr; grid-template-rows: minmax(0,40%) minmax(0,1fr)`，仍然各自内部滚动。

### 验证

- 用 Chrome headless（`--headless=new --window-size=1600,900 --screenshot`）加载一份内联同结构 + 同 `styles.css` 的测试页：截图显示外层无滚动条，左右面板同高、上下留白一致，滚动条只出现在两个面板内部（`%TEMP%\wd_layout\` 已清理）。
- 冒烟不受影响，仍 164 项。

## 1.-8 v1.17.1（更正「跨群原地转发必失败」，提交 57d4d01 + dd9e0d1）

用户的反馈（m05019：「我之前手动 native 转发时可是生效了的，没有丢渲染重建」）直接推翻了 v1.17.0 的一条核心结论。读了 NapCat 源码（`napcat.mjs`）确认：

- `get_msg`（`class vye`，napcat.mjs:74796 附近）与 `forward_group_single_msg`（`class tL`，napcat.mjs:76545 附近）都通过 `nt.getMsgIdAndPeerByShortId(shortId)` 查 **`msgIdMap` / `msgDataMap` 进程内缓存**（LRU 5000 条，构造参数 `5e3`；`createUniqueMsgId` 在消息进入 OneBot 层时写入，进程重启清空）。
- `forward_group_single_msg` 的 `getTargetPeer` 只决定**发到哪个群**，源消息的 peer 来自缓存 —— 所以**跨群完全可以**，v1.14.0 的「按 peer 找消息」结论是错的。
- 之前那次「发回原群成功、发到别的群失败」是两条不同的存档：失败那条（`20261027-234006`，消息 id `1481691615`）在 NapCat 2026-10-28 11:24 重启之前产生，短 id 已不在缓存里。

### 行为/默认值变化

- `auto_forward_mode` 默认 `native`（`warden/config.py`）；`_promote_auto_forward_default` 恢复为 `rebuild → native` 的反向迁移（条件：未启用 + 无目标群 + 值仍是 rebuild），日志 `auto_forward_mode: rebuild -> native (v1.17.1 恢复 native 默认;想省掉那一次失败尝试就改回 rebuild)`。
- `main._auto_forward`：`native` 优先（`get_msg` 核验 → `forward_group_single_msg`），失败记 `auto forward native failed for <群>: ... → rebuild` 再 `rebuild_forward`；成功列表记 `<群>(native)` / `<群>(rebuild)`。
- `record_page.resend`：去掉 v1.17.0 的 `same_group` 判定 —— 「消息不存在」= 短 id 已不在缓存，发哪个群都一样，直接写 `native_unavailable`。
- `pages/dashboard/app.js`：`syncNative()` 只按 `d.can_native` 置灰，不再看目标群；动作栏说明改成「发到哪个群都行，缓存失效自动改用重建」。

### 测试

- 仍是 **164 项**（改断言）：`test_plugin_auto_forward_native_first_then_rebuild`（native 成功时只调 `get_msg` + `forward_group_single_msg`；`fail_native` 时再调 `send_group_forward_msg`）、`test_plugin_promotes_auto_forward_default`（`rebuild → native`，三种例外保持不变）、`test_plugin_resend_native_failure_marks_unavailable`（失败即标记，与目标群无关）。

### 文档更正

README「已知边界」把原来的「跨群原地转发基本必然失败 / 按目标 peer 找消息」整条改写成缓存机制说明；CHANGELOG v1.14.0 / v1.15.0 / v1.17.0 里相关句子都加了「v1.17.1 更正」标注（历史条目保留，不篡改原结论）。

## 1.-7 v1.17.0（审计修复，提交 0d36ee3 + b8988ac + 4261881）

用户的诉求（m04527「再仔细调查一遍」→ m04681「可以」）：把第二轮深度审计发现的 18 条问题全修，不吝啬改动。

### 数据安全（最重要的三条）

- **删一条共享图会连坐删别人最多 10 条记录**：`record_page.delete` 以前先 `os.remove` 再 `index.forget_path(path)`（`DELETE FROM assets WHERE path = ?`）。而 Storage 按内容去重 —— 生产库 7490 行 / 7002 个唯一文件 / **284 个路径被多行共享（最多 11 行）**。改法：页面传 `asset_id` → `index.delete_row(id)`（只删这一行）→ `index.count_path(path)` 归零才 `record_admin.delete_flat` 删文件；仍被引用时回 `{kept_file: true, shared: N}`，页面提示「文件仍被 N 条记录引用，未删除」。`delete_flat(..., keep_file=True)` 只解析不删。
- **`resolve_flat` 前缀可绕过**：`rel.startswith("_records/")` 对 `"./_records/<rid>/render.png"` 无效 → 页面能读、也能删存档内部文件。改为先 `os.path.normpath`，再判首段是否为 `_records`、是否含 `..`。
- **纯文本去重认错发送者**：`record.capture_text` 用正文指纹查重 → alice 发「666」、bob 也发「666」，第二条直接复用 alice 的条目（sender/ts 全错，渲染图还写进 alice 目录）。`find_duplicate(..., kind=None, sender_id=None)` 新增两个收窄条件；`capture_text` 传 `kind="text", sender_id=...`。聊天记录仍按「内容 + 群」比对（同一条转发被转两次确实重复）。

### 功能坏点

- ~~**自动转发 native 永远失败**~~（**v1.17.1 已推翻**：原地转发按 NapCat 进程内短 id 缓存找消息，与目标群无关）：当时的做法是 `_auto_forward` 恒用 `rebuild_forward`、`auto_forward_mode` 默认 `rebuild`、`_promote_auto_forward_default` 做 native → rebuild 的反向迁移；v1.17.1 已全部恢复，见 §1.-8。
- ~~**跨群 native 失败毒化条目**~~（**v1.17.1 已推翻**：「消息不存在」只说明短 id 不在缓存，与目标群无关）：当时的做法是仅当目标群 == 原群才写 `native_unavailable`，页面按目标群置灰按钮；v1.17.1 恢复「失败即标记」、按钮只看 `can_native`。`doResend` 成功后重新拉详情重画这一点保留。
- **语音没有开关**：`policy.kind_allowed` 以前对不在开关表里的 kind 返回 `True` → 六类全关语音照存。新增 `save_voice: bool = False`（config + schema ② 组），`kind_allowed` 改成 `flags.get(kind, False)`（未知类型默认不存）；页面类型标签补 `voice`。
- **纯文本条目污染扁平索引**：`_save_text` 不再写 `index.record(kind="text", path=<条目>/render.png)` —— 条目本来就在 `_records/`，写了会以「磁盘文件」出现而 `resolve_flat` 拒读，点不开也删不掉。
- **纯文本回执无预览**：`_build_previews` 候选改为 渲染图 > **text** > image，路径取 `preview_path or path`。

### 口径 / 性能 / UI

- **857 张旧扁平渲染图独立成类**：`record_admin.KINDS` 增 `render`（标签「旧渲染图」），`flat_item` 把 `kind=forward_render` 映射成 `render`；`record_page._FLAT_KIND_MAP` 的 `image` 不再并 `forward_render`，新增 `"render": ["forward_render"]`。统计栏出现「清理旧渲染图 N 张」按钮 → `POST page/purge_legacy` → `record_admin.purge_orphan_renders(root, index)`：跳过带条目 msg_id 的行，按 path 归组删行，引用归零才删文件，返回 `{rows, files, bytes}`。**没有自动执行**：删的是用户数据，必须由人在页面上点。
- **分页翻不到第 2000 条之后**：`index.list_assets(limit=None)` 支持取全量（`record_page._flat_rows` 默认 None），`scan_items` 切片才是真的 offset。实测 `offset=2050` 能取 50 条（以前 0 条）。
- **`/warden stats` 纳入存档**：`index.stats()` 增 `unique_bytes`（按 path 去重，共享文件不再重复计）；命令同时打印 `record_admin.stats()` 的条目数/占用。
- **`/warden prune` 加 `--records`**：默认只删索引行；`record_admin.count_records_older_than` / `delete_records_older_than` 只在显式 `--records` 时调用，否则回执提示「另有 N 条存档未动」。
- **回执不再印内部类型名**：`reporter._KIND_LABELS` 把 `forward_render→聊天记录渲染图` 等翻成中文，类型行与成功/失败明细共用。
- **重建转发带原时间戳**：`record_admin.node_payloads` 的 node 加 `"time"`；`/warden 重发` 的重建路径改为优先直接 `send_group_forward_msg`（与页面/自动转发同一实现），只有没有 bot 时才退回 AstrBot `Nodes`（它不发 time）。
- **下载闸门只包网络**：`Semaphore` 从 `_download_all._one` 移进 `_download_one`，只包 `download(...)` 与 `_refetch_media(...)`；「等 5s/20s 再查本地缓存」不再占并发名额（n 张死链图不再 `ceil(n/4)*25s`）。
- **样式预览不泄漏临时目录**：`_render_preview` 的 `mkdtemp("wd_style_")` 改为 `try/finally shutil.rmtree`（实测积过 108 个）。

### 接口 / 签名变化（写代码时要看）

- `record.find_duplicate(storage_root, sha, *, exclude="", group_id=None, kind=None, sender_id=None)`。
- `record_admin.delete_flat(root, rel_path, *, keep_file=False)`。
- `record_admin.stats(root)` 不变；新增 `count_records_older_than(root, cutoff)` / `delete_records_older_than(root, cutoff)` / `purge_orphan_renders(root, index)`。
- `index.list_assets(..., limit=None)` 表示全量；新增 `index.count_path(path)`；`index.stats()` 增 `unique_bytes`。
- `record_page` 路由 **13 → 14**（新增 `page/purge_legacy`）；`get_stats` 增 `legacy_renders` / `legacy_bytes`；`delete` 接受 `{source:"flat", path, asset_id}`。
- `record_admin.KINDS` / `KIND_LABELS` 增 `render`（「旧渲染图」）。

### 测试

- 152 → **164**（Phase 9: 21 → 33）：共享文件按行删 + `resolve_flat` 防绕过、文本按发送人去重、纯文本回执预览、分页 offset 2050、旧渲染图清理（条目 msg_id 保留）、预览临时目录清理、`node_payloads` 带 time。
- 把 4 个「定义了却没注册」的用例接回 PHASE9（它们此前从未执行过）：`test_forwarder_emoji_mixed_layout`、`test_recover_raw_url_overrides_local_jpeg`、`test_recover_raw_no_http_keeps_local`、`test_recover_pairs_refs_by_identity_not_position`。
- 改：`test_plugin_auto_forward_native_falls_back_to_rebuild` → `..._cross_group_uses_rebuild`；`test_plugin_promotes_auto_forward_default` → `test_plugin_demotes_auto_forward_default`（v1.17.1 又改回 promote 语义）；回执类型断言改中文标签；路由数 13/14。

### 本机数据现状（升级后不必手忙的事）

- 生产库索引 7490 行、`_records/` 59 条条目、857 张旧扁平渲染图（1.25 GB）。旧渲染图**没有**自动删 —— 在 dashboard「旧渲染图」分类或统计栏按钮里人工清。
- 752 张 missing 图全在 legacy 条目里，用 `localcache` 逐张探过 0 张可救（老缓存已清）。
- `save_voice` 默认关，本机库中 0 条语音，升级后行为不变。
## 1.-5 v1.16.0（渲染开关布尔化）

用户诉求（m04174 选「3 按建议」）：三态 `forward_render_mode`（`image`/`json`/`both`）太难懂，收成一个布尔开关；不做老动画 WebP 重扫。

### 做了什么

- **`forward_render_mode` → `forward_render_image: bool = True`**（`warden/config.py`）。语义只剩一句：条目里画不画 `render.png`。结构化 `record.json` 一直都有，所以「存不存 JSON」不再需要配置。
- **旧值自动派生**：`from_raw_lenient` 里 `legacy_mode = raw.pop("forward_render_mode", None)`，仅当新键缺失时 `raw["forward_render_image"] = str(legacy_mode).lower() != "json"` —— `json` → 关，`image`/`both`/缺省 → 开。
- **不再落扁平 JSON 副本**：`_save_forward` 删掉 `want_json` 整段。不画图时，条目存在就回 `record.json` 的 `AssetResult(kind="forward")`；只有存档失败（PIL 缺失 / 目录不可写）才退回原来的扁平 JSON 兜底并写索引。
- **一条转发只回一个结果**：渲染开 → `kind="forward_render"`；渲染关 → `kind="forward"`。以前 `both` 会算两条（聊天记录 + 渲染图），回执计数偏大。
- `_conf_schema.json` ⑤ 组同步换键，hint 写明旧值映射。

### 顺带修的真 bug

- **内联节点存档成空壳**：转发节点有「裸节点」与「包装段 `{"type":"node","data":{sender,message}}`」两种形态，`record._segments()` 只认前者 → 整条转发只有内联节点时存档内容为空。新增 `record._payload(node)` 统一剥一层；`_segments` / `node_identity` / `dry_nodes` / `capture` 的 `time` 全部走它。
- **节点昵称缺顶层回退**：`node_identity()` 只读 `sender.card/nickname`，某些实现把昵称放节点顶层 → 名字退化成 QQ 号。现在 `sender.card → sender.nickname → 顶层 card → 顶层 nickname → QQ 号` 逐级回退。

### 测试

- `_archived_records(root)` helper 统一读 `<root>/_records/*/record.json`；三个以扁平 sidecar 为断言对象的测试改写为「条目里有 / 扁平目录里没有」。
- 结果种类断言从 `["forward", "forward_render"]` 改成 `["forward_render"]`。
- 冒烟 152 项（Phase 4: 26 / Phase 9: 21），全绿。

## 1.-4 v1.15.0（配置页重做）

用户诉求（m03924）：「默认 native；config schema 整体改一下，要清楚简洁易懂，可以改一下各个配置的位置」。

### 做了什么

- **九组重排**（配置页顺序 = 读的顺序）：① `filter` 监听范围 → ② `save_types` 保存哪些类型 → ③ `storage` 存储与索引 → ④ `download` 下载与重试 → ⑤ `forward` 聊天记录渲染 → ⑥ `render_style` 渲染样式 → ⑦ `auto_forward` 自动转发 → ⑧ `reply` 保存后的回执 → ⑨ `logging` 日志。每组 `description` 直接写序号。**组键（内部名）不动**，只改中文标题。
- **hint 重写**：只写「这一项现在会怎样」，删掉版本考古与实现细节。`_conf_schema.json` 从 46 键降到 45 键（删 `forward_image_width`）。
- **`auto_forward_mode` 默认 `native`**（`warden/config.py`）。实测约束写进 hint：native 只对原群可靠，跨群基本必失败并自动回退 rebuild。旧配置里的值不会自动变 —— 除非满足 `_promote_auto_forward_default()` 的三个条件（未启用 / 无目标群 / 值仍是旧默认 rebuild），那时启动会静默改成 native 并写回，日志 `auto_forward_mode: rebuild -> native`。
- **`forward_image_width` 彻底删除**：`warden/config.py` 字段与校验、`main.py` 里 `Forwarder(width=…)` 改用 `self.cfg.style_width`、`style_snapshot()` 里那一行。画布宽度只剩 `style_width`。
- **`forward_image_engine` 选项收成 `["pil"]`**；`main.initialize()` 对旧值（playwright/wkhtml）仍打告警并继续用 PIL。

### 为什么组键不改名（重要）

一度把 ⑤ 的组键从 `forward` 改成 `render`，实测（2026-10-28）重载后 **AstrBot 按新 schema 重写了配置文件，旧 `forward` 组连同里面的值一起消失**（写端根本不用插件的 `_flatten_raw` 那套兼容读取）。结论：`_conf_schema.json` 的顶层键是配置的**存储地址**，改标题安全、改键等于丢用户配置。所以最终只改 `description`，键保持 `forward`；`_migrate_config_groups()` 那套迁移也随之删除（没有键变化就不需要它）。

### 踩坑

- `test_conf_schema_covers_all_config_fields` 断言 schema 字段集合 == dataclass 可配字段集合，删 schema 键必须同时删 dataclass 字段。
- 默认值升级这类「迁移」逻辑写成 `@staticmethod`（`MediaWardenStar._promote_auto_forward_default(cfg)`）才能脱离插件实例直接测；插件模块名是 `astrbot_plugin_media_warden_smoke`，不能 `from astrbot_plugin_media_warden…`。
- 改表格文档别用 `Set-Content` 全量重写后立刻 `tools.edit` —— edit 的读缓存会失效，必须先 `read` 再 `edit`。

目标（用户 m02240）：只存**特定群 / 特定人**发来的**特定类型** —— 图片 / 动图 / 视频 / 纯文本 / 聊天记录 / 文档；纯文本与聊天记录的「正常形式」都是 Pillow 渲染（这点要标清）；dashboard 按类型**分类管理**；聊天记录的两种形态共用一个条目；结构化条目能「去气泡重发」也能自动转发到别的群；渲染样式单独一页可改。

### 升级前必读（语义反转）
- 白名单模式下**空列表 = 不保存**：`target_groups` 空 → 一个群都不存，`target_users` 空 → 一个发送人都不存。旧版本把空列表当「不限」，所以留空的人升级后会突然什么都不存。要「这个群所有人都存」写 `["*"]`，要「不设任何限制」用 `match_mode: blacklist`。
- 六类开关只有 `save_texts` 默认 `false`（纯文本以前完全不在流程里），其余默认 `true`，所以升级后的默认行为与 v1.13 一致。
- **WebUI 配置这次是真的生效了**：v1.10.0～v1.13.0 里 `from_raw_lenient()` 只读顶层键，而宿主是按 schema 分组嵌套传的，于是所有分组配置都没进过 dataclass（默认值恰好等于用户配置，才一直没被发现）。v1.14.0 的 `_flatten_raw()` 摊平嵌套组后，`target_groups` / `target_users` 等第一次真正生效 —— 本机升级后日志从 `groups=0 users=0` 变成 `groups=1 users=1`，抓取范围也跟着变成「只这个群 + 只这个人」。如果以前靠「空 = 不限」全量抓，升级前先确认 WebUI 里的名单。
- 纯文本 / 聊天记录都落在 `_records/`：`entry.json`（纯文本结构）/ `record.json` + `assets/`（聊天记录），都能再带一张 `render.png`。老的扁平 `…_forward_json_*.json` 不再新写，但页面与命令仍读、仍管。

### 机制
- **类型门控在下载之前**：`policy.media_kind(comp)` → `kind_allowed(cfg, kind)`，file 再叠 `cfg.doc_ext_allowed(扩展名或文件名)`（v1.14 起两种入参都收）；forward 关了就整类丢。全丢时日志写 `skip: nothing to save | dropped: image=1 …` —— 省带宽，也不再制造「下完才发现不该存」的失败记录。
- **动图判定不靠 PIL**：`comp_is_animated`（扩展名）+ `blob_is_animated`（GIF 帧数 / WebP `ANIM` / APNG `acTL`），没装 Pillow 的宿主也准。
- **纯文本**：`components.raw_segments(event)` 取原始段（text / at / face / reply）→ `record.capture_text()` 写 `entry.json` → 渲染一张 `render.png` 塞进同一目录 → 回执与媒体共用 `_emit_receipt()`。
- **聊天记录**：`_save_forward` 顺序改成「先下节点图（`blob_by_url`）→ `record.capture()` → JSON → 渲染 → `save_render()`」；存档失败只记日志（不影响 JSON 与渲染），渲染失败照样经 `_failed()` 进 failures。
- **分类管理**：`index.list_assets(kinds / group_id / q)` 查扁平文件，`record_admin.scan_items()` 把 `_records/` 条目与扁平行合成一页；类型标签同时覆盖两者。`page/thumb` 支持 `source=flat` 与 `render.png`；`page/reveal` → `os.startfile`（仅 Windows，且只对 `storage_root` 下的目录生效）。
- **统计/筛选必须走 SQL，不能先取一页再过滤**（v1.14.0 页面审计修的就是这个）：`record_page._FLAT_KIND_MAP` 把页面类型翻成索引 `kind`（image → `['image','forward_render']`、forward → `['forward','forward_json']`），`_flat_kinds()` 在 SQL 层过滤；总数用 `index.summary(...)` 的 `COUNT(*)` / `SUM(size)`，`scan_items(flat_total=...)` 只负责把「条目 + 扁平真实总数」拼成 `total`。占用口径含 `render.png` 与 `entry.json`（`_dir_file_bytes`）。
- **uin 只在可信时显示**：`record.uin_reliable(nodes)` —— NapCat 的 `get_forward_msg` 会给整条转发的每个节点**同一个** `sender.user_id`（昵称却各不相同），这种记录写 `uin_reliable: false`，页面只显示昵称、不显示那个假号。
- **取图一律走 `page/thumb`**（返回 `data:image/…;base64,…`）：页面脚本里任何要显示的 `<img>` 都带 `data-asset`，`hydrateThumbs()` 按 `img[data-asset]` 选——**不要**只按 `.thumb` 选，否则渲染图（`.render-img`）会静默变成破图标（v1.14.0 页面审计里踩过）。桌面版（Tauri）没有 dashboard cookie，`<img src="/api/plug/…">` 恒 401。
- **列表预览**：`_forward_preview(rec)` 拼整条记录的文字，纯图片记录退回「图片 N · 表情 M」；`entry_item` / `_forward_item` 的 `bytes` 都用 `_entry_bytes()`（资产 + render.png + json）。
- **详情字段**：`record_detail()` / `_entry_detail()` 都返回 `has_render` 与 `render`（文件名）、`duplicate_of`，页面靠这些决定「结构化 / 渲染图」标签与「内容重复」徽章；缺了标签就不出现（这正是审计里「render.png 点不开」的原因）。
- **重渲染**：`main.rerender_record(rid) -> {ok, render, bytes, nodes}`（路由 `page/render`，POST `{id}`）。把 `record.json` 的节点转回 `_coerce_nodes` 的 OneBot 形状（`{sender:{user_id,nickname}, time, message}`），图片字节从条目 `assets/` 按 `(node.index, 段下标 si)` 读（与 `capture()` 的登记口径一致），再 `render_with_images(nodes, node_imgs={节点位置: [bytes…]})` + `save_render()`。**纯文本条目走同一个函数**（`entry.json` 的 `segments` 当成单节点）。`uin_reliable === false` 时跳过头像拉取。
- **同群重复就地复用（不建目录）**：`capture()` / `capture_text()` 在**建目录、下图之前**先用 `record.dry_nodes(nodes)` 归一化结构算 `origin_sha256`，`find_duplicate(..., group_id=…)` 在**同一个群**的条目里找；命中就返回原 record（带 `reused` / `duplicate_of` / `duplicate_of_requested`），既不建目录也不下载图片。返回体的 `record_id` 是**原**条目的 id —— 调用方（`_save_forward` / `_save_text`）必须把它接回去，后续 `save_render` / 索引都用这个 id。**跨群不合并**：同一内容出现在别的群各存一份是对的。老数据里已经多出来的目录不自动删，`backfill_duplicates()` 只在启动时补 `duplicate_of` 标记（按 `(group_id, sha)` 配对，按目录名 = 时间先后）。
- **聊天记录的渲染图只住条目**：条目写成功时 `main._save_forward` 只把 PNG 写进条目目录的 `render.png`（`record.save_render`），**不再**经 `Storage.save` 落一份扁平 `forward_image_*.png`、也不再写 `kind="forward_render"` 索引行（v1.14.0 之前是两份字节 + 「图片」分类里多一条）。只有存档失败（`archived is None`）才回退到扁平落盘。回报里的那条 `forward_render` AssetResult 的 `path` / `preview_path` 因此指向**条目内**的 `render.png`。
- **老扁平聊天记录 → 条目**：`record_admin.migrate_flat_forwards(root, index)`（`main.initialize()` 里 `await`，在 `reconcile_flat_renders` 之后）。取 `index.list_assets(kinds=["forward","forward_json"])` 中路径以 `.json` 结尾的行（**生产库这批的 kind 是 `forward`，不是 `forward_json`** —— 第一版就是按后者查的，跑出来 0 条），JSON 里 `nodes` 直接喂 `record.capture()`，`fetch_image` 返回 None（老 url 已过期 → `missing`），`ts` 用索引行的 ts。成功后把原 JSON 复制成条目里 `legacy_forward.json`（同名冲突时加 sha8 后缀）、`record.set_meta(..., legacy_source=…, legacy_file=…, legacy_migrated=1)`，再 `index.delete_row()` + 删原文件。`nodes: null` 的坏文件只计入 skipped，不动。
- **空态与类型标签的真相来自后端**：`page/stats` 的 `save_types`（`plugin.cfg.save_type_flags()`）是页面判断「这一类是不是关的」的唯一来源；`app.js` 的 `KIND_FLAGS` 把类型映射到 `save_images / save_gifs / save_videos / save_files / save_texts / save_forwards`。新增类型或改开关名时两处必须同步。
- **原消息失效要落盘**：`page/resend` 的 native 失败且错误含「取不到原消息 / 消息不存在 / 无法找到」时调 `record.set_meta(root, rid, native_unavailable=<ts>)`（写回 `record.json` / `entry.json`）；`record_admin._can_native(rec)` = 有数字短 id 且没有这个标记，条目列表项 / 详情 / 页面按钮都读它。**偶发错误不写**（`_GONE_HINTS` 只认那三种），否则一次超时会把按钮永久禁掉。
- **动图分类兼容老索引**：v1.14.0 之前 GIF 记成 `kind=image`。`index._asset_filter()` 里 image / gif 两个分类按扩展名互斥切分（`lower(path) LIKE '%.gif'`），`record_admin.flat_item()` 同步把这类行显示成 `gif`，两边口径必须一致，否则「动图」筛不到、「图片」里混着 GIF。
- **旧数据迁移 `reconcile_flat_renders(root, index)`**：`main.initialize()` 在索引 open 后自动跑一次（日志 `flat forward renders reconciled: N row(s), M file(s) removed, K adopted into entries`）。三条规则：条目**已有** `render.png` → 删字节相同的扁平行（该路径无别的行引用才删文件）；条目**没有** render 且同 msg_id 的扁平行只有一种字节 → 把那张图**收编**成条目 `render.png` 再删行；一份文件被多行共享（`Storage` 去重）→ 只删行、文件留着。删行用 `index.delete_row(asset_id)`（同一个 path 可能有多行，`forget_path` 会连带删干净）。
- **转发**：`record_admin.native_forward`（**只对原群可靠**：2026-10-28 实测同一条存档原群成功、跨群回「消息不存在」——`forwardMsg(peer, …)` 按目标 peer 找消息）/ `rebuild_forward`（丢装扮，跨群要它），原地失败自动回退重建并在回执里说明；页面下拉 `page/groups` 带群名，默认这条记录的原群。自动转发在 `_save_forward` 末尾调 `_auto_forward()`，目标空或等于源群就跳过。
- **样式**：`Forwarder.apply_style(**10 项)` 改 `_RenderCfg`；`main._apply_render_style()` 在 `initialize()` 与 `update_style()` 后都调；`pages/style/` 实时预览（debounce 180ms，含合成头像），保存写宿主配置（`_persist_config` → `config.save_config()`，失败只记日志）。`dark` 只替换仍是默认值的颜色。

### 签名变化（改测试/改调用前先看这里）
- `record_admin.native_forward()` / `rebuild_forward()` 的结果多 `new_message_id`（native 另有 `origin_message_id`）；动作返回 `status=failed` 时 rebuild 也算失败（以前返回值被整个丢掉）。
- `config.update_style(values) -> (snapshot, errors, persisted)` —— **三元组**（曾经返回二元组，测试把 `persisted` 当成 errors）
- `config.doc_ext_allowed(ext_or_name)`、`save_type_flags()`、`forward_targets_set()`
- `policy.media_kind` / `kind_allowed` / `comp_is_animated` / `blob_is_animated`；`ALL_SENDERS = "*"`
- `record.capture_text(...) -> dict`、`load_entry`、`meta_filename`、`has_render`、`save_render`；常量 `ENTRY_FILENAME` / `RECORD_FILENAME` / `RENDER_FILENAME`；`list_records()` 混排 forward/text 并带 `kind`
- `record.capture()` / `capture_text()` 命中重复时返回**原条目**并带 `reused` / `duplicate_of` / `duplicate_of_requested`；新增 `record.dry_nodes(nodes)`、`record.find_duplicate(..., group_id=…)`、`record.backfill_duplicates(root) -> int`
- `index.list_assets(*, limit, offset, kinds, group_id, q) -> {"total","items"}`、`forget_path(path) -> int`、`index.summary(*, kinds, group_id, q) -> {"total","bytes"}`（`_asset_filter()` 静态方法被两者共用）
- `record_admin.scan_items(..., flat_total: Optional[int] = None)`、`record_detail()` 多 `render` / `has_render` / `uin_reliable`、`record.uin_reliable(nodes)`
- `record_admin.KINDS` / `KIND_LABELS` / `scan_items` / `entry_item` / `flat_item` / `entry_file` / `resolve_flat` / `delete_flat`
- `forwarder.apply_style(**kwargs)`、`_avatar_mask(size, shape)`、`_hex_rgb()`、`_fmt_time()`
- 页面路由 8 → 13（新增 `reveal` / `style` / `style_save` / `style_preview` / `render`）；`main.rerender_record(rid)`；`record.find_duplicate()`；列表项/详情多 `duplicate_of`
- `record_admin.reconcile_flat_renders(root, index) -> {"rows","files","adopted"}`、`index.delete_row(asset_id) -> int`
- `record.set_meta(root, rid, **fields) -> bool`（写回条目元数据）、`record_admin._can_native(rec)`；详情/列表项多 `native_unavailable`；`record_page._native_gone(error)`、`record_admin._forward_preview(rec)` / `_entry_bytes(root, name, rec)`（预览与体积口径）

### 配置组（schema 9 组）
`filter` / `storage` / `download` / `logging` / `forward` / `reply` / **`save_types`** / **`auto_forward`** / **`render_style`**

### 踩坑
- 纯文本分支最初忘了把 `event` 传给回执 helper，整条消息一条都不 yield（`_drive_command` 收到空列表，看起来像「没处理」）。回执一律 `_emit_receipt(batch, msg_id, sender_id, event)`。
- 白名单模式的空列表语义在 v1.14 反转过；写测试时用 `match_mode: blacklist` 的老用例不受影响，但任何依赖「whitelist + 空列表 = 全存」的旧断言都要改。

## 1.-2 v1.13.0（dashboard 存档页面）

目标：存档要能在 WebUI 里看、搜、删、发回去，而且浏览不需要 QQ 那种渲染（用户原话 m01757：保存 / dashboard 浏览管理 / 保装扮再发送）。

### 机制（AstrBot 这边，别再翻代码找了）
- 页面目录：`<plugin>/pages/<name>/index.html`，宿主自动发现（`pages` + `index.html` 两个常量在 `dashboard/routes/plugin.py`）；**同目录的 css/js 用相对路径引用即可**，宿主按「文档 URL 目录」解析，token 在路径里（`/api/v1/plugins/<plugin>/views/<page>/_t/<token>/`）
- 页面里用 `window.AstrBotPluginPage`（`apiGet/apiPost`）→ 宿主拼成 **`/api/plug/<pluginName>/<endpoint>`**（strapi 那层 axios 带 Authorization 头）；不是 `/api/v1/plugins/extensions/...`（易记错）
- 后端注册：`self.context.register_web_api(route, handler, methods, desc)`，route 必须自带插件名前缀；handler 里 `from astrbot.api.web import request`，`request.query.get(k, default, int)` / `await request.json(default={})` / `file_response(path)`
- `<img>` 带不上 Authorization 头，legacy `/api/plug/...` 只能靠 dashboard 登录 cookie `astrbot_dashboard_jwt`（httponly + SameSite=Strict + path=/）—— 但桌面版（Tauri）实测**没有**这个 cookie，图片 401。最终做法：`page/thumb` 把资产读成 base64 走**桥接口**（axios 带 Authorization），页面填 `img.src`。以后再做页面取图，直接照这条
- `bridge-sdk.js` 由宿主注入在 `</body>` 前、**排在页面自己的 `app.js` 之后** → 页面必须轮询等 `window.AstrBotPluginPage` 出现（最多 5s），不能假设脚本一执行桥就在
- 桌面版（Tauri）里 **`window.open` 是空操作**：预览大图要在页面内做蒙层，别指望新窗口/新标签
- 页面标题来自 `.astrbot-plugin/i18n/<locale>.json` 的 `pages.<name>.title`；不给就只显示目录名

### 新增
- `warden/record_admin.py`（读侧，纯函数）：`scan_records(root, *, q, group, limit, offset)` / `stats(root)` / `record_detail(root, rid)` / `resolve_asset(root, rid, rel)`（只认 record.assets 里的 path 且校验没越界）/ `delete_record` / `node_payloads` / `native_forward` / `rebuild_forward`
- `warden/record_page.py`：`PAGE_PREFIX = "/<plugin>/page"`，六个接口 stats/records/record/asset/resend/delete；`register_routes()` 返回 `False` 表示宿主太老
- `pages/dashboard/{index.html,app.js,styles.css}`；`.astrbot-plugin/i18n/{zh-CN,en-US}.json`
- `main.initialize()` 里 try/except 注册，失败只记日志；启动日志多了 `page=on|off`

### 设计取舍
- 页面**不**复刻 QQ 气泡：用户明确说浏览不需要那种渲染。要保装扮 = 原地转发，页面只是给它一个按钮。
- 页面只读 `_records/`，不碰 `_warden.db`：删存档删目录即可（索引里本来也没有存档行）。
- 搜索在 Python 里做（按 captured_ts 排序后切片），存档量不大，没必要再加索引。

## 1.-1 v1.12.0（聊天记录存档 + 原地重发）

目标：把群里的合并转发**存下来、以后还能原样发出去**。先做了一轮协议实验（NapCat 4.18.37 / QQ 9.9.26），结论直接决定了实现方式。

### 实测结论（别再重复验证）
- **气泡/挂件是用户级装扮，不随消息传输**：消息对象里 `generalFlags` / `avatarPendant` / `msgMeta` 全为空；资料卡 `fetchUserDetailInfo` 的 `vasInfo` 只有 `pendantId`（例子 163557），**没有任何 bubble/气泡 id**；`napcat.mjs` 全文「气泡」0 次、Bubble 1 次（protobuf 定义里的匿名消息字段，无接口）。
- **重建节点不保装扮**：存档 → `Nodes` → `send_group_forward_msg`，合并转发里每个节点只剩昵称 + 内容。
- **原地转发保装扮**：`forward_group_single_msg`（NapCat → `MsgApi.forwardMsg(peer, ..., [msgId])`）由客户端把原消息再发一次，气泡、挂件、头像、时间戳全部原样。
- **两套 message_id 不能混用**：`forward_group_single_msg` 只认 OneBot 短 `message_id`（`1481691615`），段里长 `msgId`（`7701368446137848000`）回「无法找到消息」；而 `get_forward_msg` 认的正是长 id。

### 新增
- `warden/record.py`：`<storage_root>/_records/<record_id>/`，`record.json`（schema_version / record_id / platform / group_id / root_msg_id / captured_ts / node_count / nodes[{index,uin,name,time,segments}] / assets[{node,seg,path,sha16,bytes}] / missing / origin_sha256）+ `assets/<blake2b16>.<ext>`。图片字节落盘，**不再依赖 rkey**。
- `/warden 存档 [msg_id]`：引用合并转发即可；定位三级（命令行 id → 当前消息 forward 段 → 引用 reply id），引用普通消息时 `get_msg` 找内部 forward 段。
- `/warden 重发 [原地|重建] [record_id|list]`：默认先原地、失败回退重建并说明原因。

### 设计取舍
- 存档不是为了替代原地转发，而是**原消息没了之后的兜底**（撤回 / 过期 / 清理）。两者分工：原地无损但依赖服务端还有那条消息；重建永远可用但丢装扮。
- `root_msg_id` 一律存**短 id**（原地转发只认这个）；`get_msg` 仅用于核验目标是不是合并转发，不用它返回的段内 id 去转发。

## 1.-0 v1.11.0（第二轮审计修复）

这一轮没有重构，只修「看起来能用、实际在特定平台/特定路径上永不生效」的地方，外加三处一致性。

### 外部行为变化
- **Windows 上回执终于有「路径:」行**：`warden/reporter.py` 的反斜杠字面量多转义了一层（`"\\\\"`），Windows 路径永远归一不成 posix → `storage_root` 恒 `None`。顺带把 `_lcp_dir()` 改成按目录段比较（按字符比较会在 `C:/a/x.jpg` 与 `C:/ab/y.jpg` 之间切出 `C:/a`）。
- **转发渲染失败会进 failures 表**：以前绕过 `_failed()`，`/warden fail` 里看不到；现在 `kind=forward_render`、`idx=ctx.idx+100`、`subject=forward_render:<msg>#<idx>`。
- **`/warden list` 每行带 sha16**（与 lookup 共用 `_format_asset_row()`），可以直接拿去做 `/warden lookup <sha16>`。
- **16 位十六进制的 msg_id 能查到了**：sha16 没命中时回退按 msg_id 查（以前直接判「未找到」）。
- **file 段的 http 链接**（放在 `file` 字段上）现在能被下载，与 image/video/voice 一致。
- 落盘改**原子写入**（临时文件 + `os.replace`），崩在半路不会留下半截素材。

### 签名变化
- `_failed(comp, ctx, stage, err, err_obj=None, *, url=None, subject=None, failure_kind=None, kind=None, row_idx=None)` —— 新增 `kind` / `row_idx`（同一条消息的第二个产物按自己的 kind 与 idx 入库）。
- `warden/config.py:EXPORT_FORMAT_VERSION` **已删除**（0 引用）；导出格式版本写在 `index.export_json()` 的注释里（整数 1）。
- `warden/index.py`：三个读资产方法共用 `_asset_rows()`；`recent()` 补 `id DESC` 兜底排序。
- `_format_asset_row(r)` 增加 `msg=<msg_id>#<idx>` 字段。

### 测试
- 新增 Phase 6 四项：Windows 路径公共目录、file 段 http 兜底、转发渲染失败入库、落盘无 `.part-` 残骸；并扩展 list / lookup / conf-schema 断言。

## 1.0 v1.10.0（重构 + 补齐，上一个版本）

审出来的问题分三类，一次处理完：**看起来有其实没有**的配置、**只有测试在用**的索引能力、**两份走散的重复实现**。

### 外部行为变化（只有这几处）
- `reply_to_original` 从「0 处生产引用」变成真生效（回执带 `Reply(id=msg_id)`，适配器不支持则退化纯文本），**默认值同步改为 `false`** —— 不这样改，升级的人会突然被引用回复刷屏。
- `forward_image_engine` 选 `playwright` / `wkhtml` 时：启动日志明确告警 + 继续用 PIL（以前文档说会降级 JSON，实际两样都没发生）。
- 转发节点取到空内容时**记为失败并入库**（以前落一个 `{"nodes": null}` 还回 ✅）。
- `/warden prune` 现在把同期失败记录数也报出来（以前静默删掉、只报索引行数）。

### 被删的 API（别在旧笔记里继续引用）
`warden/downloader.py`：`download(fallback=…)`、`astrbot_component_fetcher(refresher=…)`、`download_many()`；`main.py:_get_media_url_candidates()`；`warden/config.py:from_raw()`；`warden/index.py:prune_failures_older_than()`；`warden/components.is_text_only()`；`warden/reporter.py:failure_summary()`。

### 签名变化（改测试/改调用前先看这里）
- `_cached_copy(comp) -> (Downloaded|None, misses: list[str])`（不再写 `self._local_cache_misses`）
- `_refetch_media(comp, event) -> (Downloaded|None, trace: list[str])`（不再写 `self._refetch_trace` / `_refetch_last_error` —— 那是在 self 上放按组件状态，并发下会串味）
- `_download_fresh(comp, fresh)` 失败只返回 None，不存状态
- `_late_cache(probe, label)`：媒体与转发节点图共用同一份迟到重查循环
- `_local_cache_read(names) -> (Downloaded|None, path, misses)`：媒体与转发节点图共用同一份「查 QQ 本地缓存并读字节」；`_cached_copy` / `_forward_local_cached` 只剩包壳
- `_recover_original_urls` 先按标识（file / name / fileid / url 末段）配对，配不上才退回顺序
- `_reply_prefix(msg_id) -> list`：决定这条回执带不带 `Reply`
- `_failed(..., failure_kind=None)`：`failure_kind` 缺省走 `classify_error(err)`（`"other"` 因此可达）
- `_find_saved_asset(msg_id, idx, *, platform=None, group_id=None)`
- `AssetIndex.open()` 会跑 `_ensure_columns()`（`PRAGMA user_version` + 缺列 `ALTER TABLE`），迁移结果放在 `index.migrated`；**建表与建索引分成 `SCHEMA_TABLES` / `SCHEMA_INDEXES`**，顺序不能换
- `prune_older_than(cutoff_ts) -> {"assets": n, "failures": m}`

### 结构
- `main.py` 1564 行；`warden/forwarder.py` 609 行（两份渲染器合成 `render()` / `render_with_images()` → `_paint(nodes, node_pil)`）
- `warden/components.py` 只有一条归一化路径 `_coerce(t, _Fields, raw)`
- `warden/reporter.py` 的 fetch/save 分布只算一次（`_split_fetch_save()` + `_reason_line()`）

## 1.-6 真根因（2026-10-11 晚取证并修复）：**NapCat 4.18.4 在发一条早就死掉的 rkey**

> 这一节是**对 §1.-5 / §1.-4 的最终修正**。那两节描述的「NapCat 转发节点图无解」和「只能靠迟到缓存」
> 都是**在一个已经坏掉的环境上得出的结论**；换掉坏环境后，转发节点图**根本不需要补救**。

**结论**：那批 `http 400` 不是延迟问题，是 NapCat 给出的 URL **一生成就是死的**。

| 观测 | 数值 |
| --- | --- |
| 服务端原始拒绝 | `HTTP 400 {"retcode":-5503007,"retmsg":"download url has expired","retryflag":0}` |
| 生成到实测的间隔 | **0.6 秒**（22:00:23 写日志，22:00:25 探测即过期） |
| 同一条 rkey 的复用跨度 | `CAQSMEw42ic_ZWgg0y60…`（70 字符）从 **20:54:32 一直用到 22:00:23**，66 分钟没换过 |
| 那两个 rkey 服务 | `http://ss.xingzhige.com/music_card/rkey` → **502**；`https://secret-service.bietiaop.com/rkeys` → **TLS 握手失败** |
| 兜底常量 | NapCat 包里那两个 `CAQSKAB6JWEN…` 是 **59 字符**的老格式，从未被用上 → 说明走的是 packet rkey 那条路 |
| rkey 结构 | 70 字符 base64 = 52 字节 protobuf，只有 `field1 type`(2/4) + `field2` 48 字节密钥，**里面没有时间戳/TTL**，所以 NapCat 判不出过期 |

根因链：**NapCat 4.18.4（2026-05-22 的包）配 QQ 9.9.26-44498（2026-05-28 装的）**，5 个月没动过。
NapCat 的 `getRkeyData()` 里 `rkey_expired_group` 那段**把 `packetRkey[0]` 连写了两遍**（上游主分支到今天
仍然如此），判不出过期 → 永不刷新 → 一批批新图片链接全带着同一条死 rkey。普通图片因为 QQ 客户端
本地已落盘还能被 §1.-5 的本地缓存/迟到缓存救回来，**转发节点图没有本地副本，于是 100% 全灭**
（10-11 当天 `5 fetched / 5 aborted`）。

**修法（纯环境，与插件代码无关）**：升级 NapCat **4.18.4 → 4.18.37**（2026-10-10 发布）。
`napcat.mjs` 里 rkey 那段逻辑**一字未改**（`packetRkey[0]` 判据、`rkey.slice(6)`、`CAQSKAB6JWEN…` 常量、
`appid === "1406" ? private : group` 全在），改的是 **packet hook 那一层**（`native/` 下 36 个组件）——
也就是说旧版 hook 对不上 QQ 的包结构，取回来的 rkey 服务端不认。

**升级后的实测（2026-10-11 22:29 起，同一台机器）**：

| 时刻 | 结果 |
| --- | --- |
| 22:29:13 | 转发 `2 nodes` → `✅ 已保存`，**0.66s**，`forward_render` 568489B 落盘 |
| 22:33:08 | 嵌套转发 `2 -> 7 nodes` → `✅ 已保存`，480825B |
| 22:29 之后 | `http 400` = **0**、`render aborted` = **0**、`late cache still miss` = **0**；`failures` 表 1 小时新增 0 |

对照升级前同一台机器：**25.53 秒**走完 5s+20s 的迟到缓存空等，然后 `2/2 aborted`。

### 复盘该这么查（下次先做这一步）

1. **看 URL 是不是一生成就死**：抓一条刚写进日志的 URL，**几秒内**实测。
   报 `download url has expired` 而不是超时 → 是 NapCat 侧给的凭据问题，不是插件慢。
2. **看 rkey 有没有换**：把日志里所有 `rkey=` 去重（`_rkeytime` 式的统计），
   如果跨度几十分钟还是同一条 → NapCat 的 rkey 缓存/判据坏了。
3. **看 NapCat 版本与发布日期**：`napcat.mjs` 的 mtime 就是包日期；QQ 看
   `C:\Program Files\Tencent\QQNT\QQ.exe` 的 FileVersion。两者差半年以上就该先升级，再谈插件。
4. **看兜底是否也死了**：直接 curl 那两个 rkey 服务（`ss.xingzhige.com` / `secret-service.bietiaop.com`），
   都挂意味着连 fallback 都是 59 字符的老常量。

### 环境补丁（重装/升级 NapCat 后必须重做，否则起不来）

NapCat **4.18.37 自己的包有缺陷**，与本插件无关，但会盖掉「已修好」这个状态：

- 它的 hook 会去 **QQ 自己的目录**找 `.\loadNapCat.js`（相对 `resources\app`），
  而 `NapCat.Shell.zip` **根本没打包这个文件** → 启动即
  `Error: Cannot find module 'C:\Program Files\Tencent\QQNT\versions\<ver>\resources\app\loadNapCat.js'`
- 官方 `launcher.bat` 用 `echo` 生成 loader 时**不转义空格**，`NAPCAT_LOAD_PATH` 里有空格就会被 hook 切开

因此本机留了：

| 路径 | 作用 |
| --- | --- |
| `C:\application\NapCat\start-napcat.bat` | **以后重启用这个**（自提权 + 自动把 loader 补进 QQ 的 `resources\app` + 设好 5 个 `NAPCAT_*` 环境变量） |
| `C:\application\NapCatLoader\loadNapCat.js` | 无空格路径的 loader（读 `NAPCAT_MAIN_PATH`，缺省落到 `C:/application/NapCat/NapCat.Shell/napcat.mjs`） |
| `...\QQNT\versions\9.9.26-44498\resources\app\loadNapCat.js` | hook 真正去找的那一份（QQ 换版本目录后要重新放） |
| `C:\application\NapCat\_upgrade_4.18.37\` | 回滚材料：`backup_NapCat.Shell_4.18.4`（91 MB）+ `config_bak_20261011`（9 个配置文件） |

`config\` 目录（账号 / token / 端口 / `onebot11_*.json`）在整个升级里**没有被改动**。

## 1.-5 转发节点图其实是「晚一步」（**v1.9.9 修正了 §1.-4 的结论；其结论又已被 §1.-6 取代**）

**§1.-4 那句「NapCat 从不下载转发节点里的图片字节」只对了一半**：字节确实不在 NapCat 手里，
但**接收端 QQ 客户端会在随后几秒~几十秒把它下载下来**，按 MD5 落到本地缓存。插件失败的时刻
正好卡在那个空档之前。同一台机器上的实测（2026-10-11）：

| 时刻 | 事实 |
| --- | --- |
| 20:26:57 | 转发节点图 `D867CE1671D3457B1A0534D240D4885E.jpg` 失败（`http 400`，节点 url 已失效） |
| 20:33:39 | 同一个 MD5 出现在 `…\3683351360\nt_qq\nt_data\Emoji\emoji-recv\2026-10\Ori\` |

所以 **v1.9.9 加了「迟到缓存重查」**：三级补救全败后隔 `5s`、`20s` 再查一次 QQ 本地缓存
（`_LATE_CACHE_DELAYS_S`，普通图片与转发节点图都接），命中就用。日志里看到
`late local cache hit after Ns` 就是这条路生效了。

**仍然无解的只有一种**：客户端自始至终没下过那张图（本地永远没有字节，rkey url 又已失效）。
`DCFF08A6…jpg` / `FE56BB22…jpg` 就是这种 —— 全盘搜不到。转发链按 v1.5.0 设计
「任一节点图失败即整体失败、不落盘」，这是有意的：宁可明确报失败，也不要产出一张缺图、
回执却写着「已保存」的转发图。

### 400 是外部的，不是本插件

同一天 AstrBot **自己的**预处理管线也在同一域名上独立报了 400：

```
[v4.28.0-beta.1] [utils.io:188]: Failed to download file from https URL
  host='multimedia.nt.qq.com.cn' file='download' len=237. HTTP status code: 400
```
（20:13:52 / 20:32:50 / 20:33:01 / 20:33:09 / 20:33:42）

即 napcat 消息里那条 rkey url 本身已经失效（rkey 存活约 9 分钟）。普通图片能靠本地缓存救回来，
转发节点图只能靠上面的「迟到重查」。转发失败率对照（按天 `forward fetched` vs `aborted`）：
9-20~10-06 是 0%~4.8%，10-11 当天 `5 / 5 = 100%`。

### 诊断输出（保留）

- 转发节点图每个候选 url 失败时打全 url：
  `fwd dl candidate 1/2 failed: http 400 for '…' | url=https://multimedia.nt.qq.com.cn/download?appid=…`
- 这段日志 v1.9.9 时标成「取证完即删」，**v1.10.0 决定保留**：它是线上唯一能区分「url 死了」和「请求姿势不对」的线索，三条字段（候选序号 / 完整 url / 败因）缺一不可。


## 1.-4 转发节点图为什么难救（**v1.9.8 的取证，结论已被 §1.-5 修正**）

v1.9.6 给转发节点图接上本地缓存后，现场仍 `2/2 node image(s) failed to download`。
v1.9.7 的取证日志证明**字段假设是对的**（`file` 确实是 32 位 MD5）：

```
fwd cache: miss for file='DCFF08A6D4229FE78EFF487D48A0D0A8.jpg' |
  {"summary": "[动画表情]", "file": "DCFF08A6…", "sub_type": "<int>", "url": "…(len=235)", "file_size": "5964"}
```

那两个 MD5 当时**在整台机器上都搜不到**：`Documents\Tencent Files` 递归全搜 `NOT FOUND`；
NapCat 的 `NapCat.Shell\cache`（仅 1 个文件）、NapCat temp、`AppData` 下的 NapCat、
AstrBot 的 `data\temp`（466 个文件）也全部 `NOT FOUND`。

**`get_image` 为什么救不了**（`napcat.mjs`，`class GetFileBase extends OneBotAction`）：

```js
async _handle(payload) {
  payload.file ||= payload.file_id || "";
  const contextMsgFile = FileNapCatOneBotUUID.decode(payload.file);
  if (contextMsgFile && contextMsgFile.msgId && contextMsgFile.elementId) { … }
  const contextModelIdFile = FileNapCatO…            // 同样解 uuid
  const searchResult = await this.core.apis.FileApi.searchForFile([payload.file]);
```

- `FileNapCatOneBotUUID` 是 NapCat 进程内的内存 map（`TimeBasedCache`，24h TTL），
  **只有 NapCat 自己发出去/处理过的文件才有条目**；`decode` 返回 undefined 就直接
  `throw new Error("file not found")`（= 线上 `retcode=1200`）。节点图的 MD5 从未进过那个 map。
- 最后那条 `searchForFile` 走的是 `NodeIKernelFileAssistantService`（**文件助手**），
  按文件名在**聊天文件**里找，不是图片缓存 —— 拿图片名去搜等于必然落空。
- 转发节点图也不做 `get_image`/`get_msg` 重取：节点 url 里没有 `file_id`。

## 1.-3 v1.9.6 修的是什么（历史）

**转发链的节点图没有走第 ⓪ 级。** v1.9.5 上线后现场复测：普通图片已经能保存（`used QQ local cache for image: …` → `✅ 已保存`），但同一批里的合并转发仍整图失败：

```
[media-warden] forward fetched: 2 nodes for id=7695379530489107755
[media-warden] forward render aborted: 2/2 node image(s) failed to download
→ [Warden] ⚠️ 部分保存失败 (0/1) — 下载失败（源链接失效）
```

根因：`_save_forward` 的节点图**从不经过 `_download_one`** —— 它把节点内嵌 url 直接交给 `aiohttp_fetcher`，
候选只有 `[_upgrade_qq_image_url(url), url]` 两个。而节点里的 image 段其实**同样带 `file=<md5>.<ext>`**，
钥匙一直握在手里，只是没去用。

修法：

- `warden/forwarder.py`：`ForwardNode` 新增 `image_refs: List[dict]`，与 `image_urls` 一一对应，
  存放该 image 段的原始 segment `data`（`file` / `url` / `file_size`）。`_coerce_nodes` 填它，
  但**不改 `image_urls` 的语义**（仍是 `url or file`），老调用方不受影响。
- `main.py`：新增 `_forward_local_cached(ref)`；`_save_forward` 的 `_fetch(url)` 变 `_fetch(url, ref)`，
  先查本地缓存再走网络；`all_urls` 与 `all_refs` 同步构造。命中日志 `used QQ local cache for forward node image: <path>`。

同一条 `get_media_fallback` 开关；关掉即回到 v1.5.0 的「只试两个 url、任一失败即整体失败、不落盘」。

## 1.-2 v1.9.5 修的是什么（历史）

**图片本体一直就在本地，而补救链从没找过它。** 这是 v1.9.0–v1.9.4 全部线上失败的最终答案：
那些 `http 400`（rkey 过期）的图，**没有一张是真的丢了**。

QQ 客户端收到图片时就按 **MD5** 存了副本，而消息段里的 `data.file`
（我们存在 `Component.meta["raw_file"]`）就是那个 `<md5>.<ext>`：

| 来源 | 路径 |
| --- | --- |
| 普通图片 | `Tencent Files/<QQ号>/Image/C2C/Image2/<md5>.<ext>`（扩展名=原始格式，动图是 `gif`） |
| 表情 | `Tencent Files/<QQ号>/nt_qq/nt_data/Emoji/emoji-recv/<YYYY-MM>/Ori/<md5>.<ext>` |

本机实测（2026-10-11 19:17–19:43 全部 8 张失败图，**逐个命中**）：

```
A0A79925F70D32648280A014C0776CC1.jpg -> 2510536451\Image\C2C\Image2\a0a79925….jpg  36778B 19:39:45
86BCFD43FED302FA3BF498F39E8C593E.gif -> 2510536451\Image\C2C\Image2\86bcfd43….gif 246648B 19:19:23
9c3b278710ee06810443e938d520d2ec.gif -> ...\9c3b2787….gif                        1109322B 19:39:33
```

时间戳与消息到达时间一一对上；`9c3b2787…` 的本地 `gif` 有 1109322B，**比从网上拿到的还大 3 倍**。

此前的第 ③ 级「组件本地副本」只查 `component.path` 与 `raw.convert_to_file_path()` ——
本机这两条都拿不到东西：`meta["path"]` 是空串，AstrBot 的 `data/temp` 最新文件停在 **2026-09-24**。

修法（`main.py` + 新增 `warden/localcache.py`）：

- `find_cached_image(name) -> Optional[str]`：按 MD5 依次找 `Image/C2C/Image2`（先试原扩展名，
  再大小写不敏感地扫目录）与 `emoji-recv`（近 6 个月倒序，先 `Ori` 后 `Thumb`，最多扫 24 个目录）；
  短于 16 字符不猜；未命中返回 `None`。
- `main.py:_cached_copy()` + `_download_one` 新增**第 ⓪ 级**：先查本地缓存，命中即用、**一次网络都不走**。
  查找与读盘都走 `asyncio.to_thread`（不阻塞事件循环），日志 `used QQ local cache for <kind>: <path>`，
  `Downloaded.origin = "localcache:<path>"`。

**为什么必须放在第 ⓪ 级而不是最后一级**：rkey 的有效期只有约 9 分钟，重试与等待只会让链接死得更彻底，
而本地那份在消息到达的同一秒就已经写好了。这是本插件的**第 4 条**补救路，也是最可靠的一条 ——
前三条（http / 向 bot 重取 / 组件本地副本）都依赖外部状态，只有这一条依赖**已经发生的事实**。

**保留但已判定无效的路**：第 ② 级 `get_msg` —— napcat 的 `get_msg` 原样返回消息里那条 url
（实测 `url_same_as_msg=true`、`url_rkey_len=70`），并不重签，所以救不了 rkey 过期。

## 1.-1 v1.9.3 修的是什么（历史）

v1.9.2 的 `get_msg` 上线后现场仍是 `get_msg ['message_id'] -> 0 fresh url(s)`（2026-10-11 19:28 日志逐字）。
原因：**aiocqhttp 的 `call_action` 会把 OneBot 返回的外层解包** ——
`aiocqhttp/api_impl.py:28-39` 的 `_handle_api_result()` 成功时 `return result.get('data')`，
所以插件拿到的是 `{"message": [...], "message_id": …}` **本身**，
而 v1.9.2 的 `_media_urls_from_resp` 只在 `{"status":"ok","data":{...}}` 外壳里找 `message`。

修法：新增 `_segment_list(resp)` 同时认裸形态与带外层形态（并容忍 `message` 再包一层 dict），
`_media_urls_from_resp` 改为用它；trace 增加 `N seg(s), keys=[…]`。

**教训**：写 OneBot 调用时先确认**手上拿到的到底是整包还是解包后的 data**。
aiocqhttp 是解包的；`_get_media_url_candidates`（v1.9.0 写的）本来就是按解包后的形态写的，
`get_msg` 这条新路把它忘了。

## 1.0 v1.9.2 修的是什么

**读了 napcat 自己的实现**（`C:\application\NapCat\NapCat.Shell\napcat.mjs`）
后确认：v1.9.1 依赖的 `get_image` 对图片**根本救不回来**。

`class GetFileBase._handle`（`GetImage`/`GetFile`/`GetRecord` 的基类）只有三条路：

1. `FileNapCatOneBotUUID.decode(payload.file)` → 解出 `{peer,msgId,elementId}` → `FileApi.downloadMedia(...)`；
2. `FileNapCatOneBotUUID.decodeModelId(payload.file)`；
3. `FileApi.searchForFile([payload.file])` —— 按**文件名**在文件缓存里找。

而 `FileNapCatOneBotUUID.encode()` 只是 `this.cache.put(uuid, data)`（`FileUUIDManager` /
`TimeBasedCache(5000, 86400000)`，**进程内内存 map，24h TTL，重启即失效**），uuid 是
`randomUUID()`。napcat 的 `picElement` 转换器**只 encode、不回传**：

```
FileNapCatOneBotUUID.encode(peer, msg.msgId, elementWrapper.elementId, element.fileUuid, element.fileName);
...
data: { summary, file: element.fileName, sub_type, url: await FileApi.getImageUrl(element), file_size }
```

所以来消息的 image 段里 `data.file` 就是**图片文件名**（`C0C9610D3203EB3B404638B24226AE23.jpg`），
拿它（或去扩展名的 basename、或 url 里的 fileid）去 `get_image`，既解不出 uuid、
也不在文件搜索缓存里 → `retcode=1200 file not found`。**这正是 2026-10-11 19:17/19:18
线上那 3 条 `get_image/get_file exhausted for image (8 attempts, 3 candidate(s))` 的原因。**

同一时刻 AstrBot 自己的 `PreProcessStage` 也在这张图上拿到 `HTTP 400`
（`[utils.io:188] Failed to download file from https URL host='multimedia.nt.qq.com.cn'`）——
即**消息里那条 url 是死的**（rkey 失效），要靠 bot 侧重新解析才能拿到活链接。

v1.9.2 的修法 —— 加第二条重取路径 **`get_msg`**：

- `get_msg` 让 napcat 用 `get_msg` 重新解析整条消息；`GetMsg._handle` 内部走
  `MsgApi.parseMessage` → `picElement` 转换器 → `getImageUrl(element)` →
  **`getRkeyData()` + `getImageUrlFromParsedUrl()`** = 用**当前** rkey 重新拼链接。
- `_refetch_media()` 现在是三级：`get_image`（多候选 × `file`/`file_id`/`id`/`image`）
  → `get_msg`（入参形态：只 `message_id` / 带 `group_id` / 带 `self_id`）→ 本地副本。
- 新增 `_media_urls_from_resp(resp, old=…, hint=…)`：兼容 `{"data":{"message":[…]}}` 与
  `{"message":[…]}` 两种包裹；按 `data.file` 与候选文件名匹配挑对那一段（多图消息），
  匹配不到退回第一个可用图。
- 每一跳都进 `trace` 列表（v1.9.2 时存在 `self._refetch_trace`，**v1.10.0 改成 `_refetch_media()` 的返回值**）；全败时日志打 `refetch: …` 最后 4 条，回执里带
  `refetch: <最后一跳>` —— 不再只有一句 "exhausted"。
- 顺带：`_refetch_media` 现在**没有候选也会试 `get_msg`**（v1.9.1 里 `if not cands: return None` 会短路掉这条路）。

**注意**：`get_msg` 的入参用 AstrBot 给的 `event.message_obj.message_id`
（aiocqhttp 把它设成 `str(event.message_id)`，见 `aiocqhttp_platform_adapter.py:228`，
对 napcat 就是 `MessageUnique` 的 unique id）。走到这一步时 napcat 侧的映射必须还在
（`MessageUnique` 持久化，但群消息本身可能已被撤回/清理）。

## 1.1 v1.9.1 修的是什么（历史）

v1.9.0 的二级补救（OneBot `get_image`）**上线后一次也没成功过**：它把 `component.file_id`
当成 `get_image` 的入参传，而 AstrBot 4.26+ 的 `PreProcessStage` 早把 `Image` 组件重写成 jpeg
静帧、`component.file_id` 已经是 `C0C9610D3203EB3B404638B24226AE23.jpg` 这种本地文件名。
napcat 按「路径 / URL / Base64」解析这个值 → `retcode=1200 file not found`（线上日志逐字可见），
补救直接掉到第三级，而第三级本地副本在这些图上也常常不存在 → 用户看到「最新一条也说过期」。

v1.9.1 的修法：

- `_recover_original_urls()` 把 raw segment 的 `file`（napcat 自己的图片 id）与 `fileid` 一并记进 `comp.meta`（`raw_file` / `fileid`）。
- 新增 `_media_refetch_candidates()`：候选按 `raw_file` → `file` → `fileid` → `file_id` → url 里的 `fileid` → 非本地 `path` → `component.file_id` 排序去重，每个候选再补一条去扩展名的 basename（与 AstrBot 核心 `_build_image_id_candidates` 一致），最多 3 个。
- `_refetch_media()` 取代 `_fresh_media_url()`：对 (候选 × 参数形态) 依次试 `file` / `file_id` / `id` / `image`（与核心 `quoted_message/image_resolver.py::_build_image_resolve_actions` 同款），总尝试上限 8；响应除 `data.url` 外接受 `data.base64`。
- `call_action` 取法与核心一致：先 `event.bot.api.call_action`（aiocqhttp），再 `event.bot.call_action`。

**教训**：OneBot 实现对 `file` / `file_id` 的语义不统一（官方 OpenAPI：`file`=文件路径/URL/Base64、`file_id`=文件ID），
AstrBot 核心本身也是「多候选 × 多参数形态」地试 —— 只赌一种形态就等于没写补救。

## 2. v1.9.0 改动清单

### 起因

生产日志显示「部分保存失败 (0/N)」回执长期存在（backend.log 里 28 条、历史 5 个滚动文件里 60 条），
失败集中在 `400 multimedia.nt.qq.com.cn`（QQ rkey 授权链接过期）与 `404 gxh.vip.qq.com`（表情包直链死链）。
取证结论：**不是落盘 bug**（`storage.save` 的 OSError/ValueError 路径一次都没出现），
而是下载源 URL 失效，且插件把「下载失败」一律报成「保存失败」。

### 1) 源链接失效补救（三级回退）

- `astrbot_component_fetcher` 新增 `refresher` 回调：url 是 http(s) 但下载失败时，先给 refresher 一次机会（拿新链接重下），再回退组件本地副本；补救都失败时**保留原始错误**，原因挂 `DownloadError.fallback_error`。
- `download()` 新增 `fallback: Optional[Callable[[], Awaitable[Downloaded]]]`：主 fetcher（含重试）耗尽后跑一次补救链，补救失败则 `raise last_exc`（原始错误）。
- `_local_fallback(component, max_bytes)`：只用本地副本解析（`_url_to_local_path(url)` → `_url_to_local_path(meta["path"])` → `raw.convert_to_file_path()` → `raw.convert_to_base64()`）。
- `main.py:_download_one()` 改为三级：① `download(comp, fetcher=astrbot_component_fetcher, retries=cfg.download_retries, …)`；② 若 `cfg.get_media_fallback and event is not None` → `_refetch_media()`（v1.9.0 里叫 `_fresh_media_url()`，v1.9.1 重写）发 OneBot `get_image` 换新链接并用 `aiohttp_fetcher` 重下（成功后 `dl.origin = fresh`）；③ `if dl is None:` **无条件**跑 `_local_fallback`（与 event 无关，这是本次修掉的一处死代码：原实现把第 ③ 级关在 `event is not None` 分支里）。三级全败才 `primary.fallback_error = "; ".join(attempts[:3])[:300]` 并 `raise primary`。
- `_fresh_media_url()`（v1.9.0 版，**v1.9.1 已被 `_refetch_media()` 取代，见 §1.1**）：`bot.call_action("get_image", file=<fileid>)`，fileid 取 `meta["fileid"]` → `comp.file_id` → `meta["path"]` → `_url_fileid(comp.url)`；响应经 `_get_media_url_candidates()` 兼容 dict/`data`/`url`/`file`/`file_url`/`path` 去重，返回第一个 ≠ 旧 url 的候选。（该函数 v1.10.0 已删 —— 0 生产引用；现在 `_refetch_media` 自己解响应。）
- `_recover_original_urls()` 恢复原始 url 时同时把 `_url_fileid(url)` 存进 `comp.meta["fileid"]`（rkey 会过期，fileid 是重取键）；v1.9.1 起同时存 `comp.meta["raw_file"] = ref["file"]`。

### 2) 失败原因区分

- `warden/reporter.py`：`AssetResult` 新增 `failure_kind` / `note`；新增 `classify_error(err) -> "fetch"|"save"|"other"`（fetch 判据：`image download failed` / `download:` / `get_media:` / `http ` / `timeout` / `connection error`；save 判据：`save:` / `json save:` / `render:`）、`BatchResult.fail_items` / `failure_breakdown()` / `failure_phrase()`。（`failure_summary()` 在 v1.10.0 被删 —— 它和 `failure_phrase()` 算的是同一件事。）
- `format_batch()` 表头带原因：全 fetch → `部分保存失败 (k/N) — 下载失败（源链接失效）`，全 save → `— 落盘失败`，混合 → `— 下载失败 X + 落盘失败 Y`；多一行 `原因:`；明细项末尾追加 `[note]`。
- @ 触发者文案：`N 个媒体没能保存 —— 下载失败(源链接已失效)，X/Y 已保存`（全 save / 混合另有措辞）。

### 3) 失败入库 + /warden fail

- `warden/index.py` 新增 `failures` 表（`ts/platform/group_id/sender_id/msg_id/idx/kind/err/subject/url/attempts`）+ `ix_fail_ts` / `ix_fail_subject`；新增 `failure_subject()`（`fileid:<id>` 优先，其次 `url:<host><path>`，丢 query 吸收 rkey/sign）、`record_failure()`（`attempts` = 同 subject+kind 历史条数+1）、`recent_failures()`、`failure_stats()`（`prune_failures_older_than()` 在 v1.10.0 被并入 `prune_older_than()`，后者现在返回 `{"assets": n, "failures": m}`）；`stats()` 增加 `failures` / `failures_by_kind`，`prune_older_than()` 同时清同期 failures。
- `main.py` 新增 `_failed()`：统一写日志 + `record_failure()` + 返回带 `failure_kind` / `note` 的 `AssetResult`；`attempts > 1` 时 note 追加 `[历史第 N 次,上次 MM-DD HH:MM]`。
- 新命令 `/warden fail [N]`（ADMIN，默认 10、上限 50）；`/warden stats` 增 `failures:` 与 `failures_by_kind:`。

### 4) 索引复用（可选，默认关）

- 新配置 `index_reuse_saved`（默认 `false`）。开启后：`_find_saved_asset(msg_id, idx)` 命中且文件仍在 → `_reuse_saved()` 直接复用（`reused=True`，note `索引复用`）；文件不在 → `ok=False`、`err="reuse: stored file missing (<path>)"`、`failure_kind="save"`。
- **顺手修掉的真 bug**：`_find_saved_asset` 原来判空写的是 `not idx`，而下标 0（一条消息里的第一条媒体）恒为 falsy → 永远命中不了索引。已改为 `idx is None`。

### 5) 配置与版本

- `warden/config.py` `VERSION` `1.8.2` → **`1.9.0`**（v1.9.1 再 → **`1.9.1`**）；新增 `get_media_fallback: bool = True`、`index_reuse_saved: bool = False`；`EXPORT_FORMAT_VERSION` 仍 `1.0`。
- `_conf_schema.json`：`download` 分组加 `get_media_fallback`、`storage` 分组加 `index_reuse_saved`，带 hint（嵌套 section 只是 WebUI 分组，AstrBot 运行时自动扁平化，`MediaWardenConfig.from_raw_lenient` 接的是扁平 key）。
- `main.py` `@register` 描述串与 `metadata.yaml` 同步 v1.9.0。

## 3. 主要修改文件

| 文件 | 改动 |
| --- | --- |
| `warden/localcache.py` | **v1.9.5 新增**：`find_cached_image()` 按 MD5 找 QQ 本地图片缓存（`Image/C2C/Image2` 与 `emoji-recv/*/Ori`），未命中返回 `None` |
| `warden/downloader.py` | `refresher` 参数、`download(fallback=…)`、`_local_fallback()`、真导入 `Component`、保留原始错误 |
| `main.py` | **第 ⓪ 级 `_cached_copy`（v1.9.5）+ `_forward_local_cached`（v1.9.6）**、三级 `_download_one`、`_fresh_media_url` →（v1.9.1）`_refetch_media` + `_media_refetch_candidates` + `_b64_payload`、（v1.9.2）`_refetch_via_message` + `_media_urls_from_resp` + `_download_fresh` + `_refetch_trace`、`_failed`、失败入库、`/warden fail`、索引复用、`idx is None` 修复、`_url_fileid`、`meta["raw_file"]` |
| `warden/forwarder.py` | **v1.9.6：`ForwardNode.image_refs`（与 `image_urls` 一一对应的 segment data）+ `_coerce_nodes` 填它** |
| `warden/reporter.py` | `classify_error` / `failure_summary` / `failure_breakdown` / `failure_phrase`、原因分叉文案 |
| `warden/index.py` | `failures` 表 + `record_failure` / `recent_failures` / `failure_stats` / `prune_failures_older_than` / `failure_subject` |
| `warden/config.py` | 版本号、`get_media_fallback`、`index_reuse_saved` |
| `warden/__init__.py` | 导出 `classify_error` / `failure_summary` |
| `_conf_schema.json` | 两个新配置项 |
| `_smoke.py` | PHASE5 18 项（v1.9.0 的 12 + v1.9.1 的候选排序 / 参数形态与 base64 + v1.9.2 的 get_msg + **v1.9.5/v1.9.6 的 localcache 三项**）、aiohttp 导入期桩、rebind 到插件自己的 `warden` 包、命令数 5→6、总数行 |
| `CHANGELOG.md` | v1.9.0 … v1.9.6 记录 |
| `HANDOVER.md` | 本文档 |

### 3.1 v1.10.0 主要修改文件

| 文件 | 改动 |
| --- | --- |
| `main.py`（1564 行） | 删 `_get_media_url_candidates`；`_cached_copy` / `_refetch_media` 改返回元组；抽 `_late_cache` / `_reply_prefix` / `_is_sha16` / `_format_asset_row`；空转发记失败；JSON 分支 `json.dumps` 进 try；`_failed` 用 `classify_error`；`_find_saved_asset` 带 platform/group；`/warden lookup <sha16>` + `/warden fail` 反复失败 top + prune 双计数 + list 可读时间；启动时告警未实现引擎并真探 Pillow |
| `warden/forwarder.py`（609 行） | 两份渲染器合一 → `_paint(nodes, node_pil)`；`_line_h` / `_wrap_text_simple` 提升到模块级 |
| `warden/components.py` | 单一 `_coerce(t, _Fields, raw)`；`_Fields.of_obj/of_dict`；face/image 两条路径统一 |
| `warden/reporter.py` | 删 `failure_summary`；`_split_fetch_save()` + `_reason_line()` |
| `warden/index.py` | `SCHEMA_TABLES`/`SCHEMA_INDEXES` 分离 + `_ensure_columns()` + `PRAGMA user_version=1` + `index.migrated`；`prune_older_than` 返回 dict；删 `prune_failures_older_than`；`find_by_msg(platform=, group_id=)` |
| `warden/downloader.py` | 删 `download(fallback=)` / `astrbot_component_fetcher(refresher=)` / `download_many` |
| `warden/avatar.py` | aiohttp 按需导入；文档更正（5MB 上限 / 默认 10s） |
| `warden/config.py` | 删 `from_raw`；`VERSION=1.10.0`；`reply_to_original` 默认 `false` |
| `warden/localcache.py` | 删死分支 `_match_in(recursive=)`；文档改为「get_msg 确实能用当前 rkey 重签」 |
| `_conf_schema.json` | 第 6 组「日志」（`log_to_stdout`）；`max_file_size_mb` / 引擎 / `reply_to_original` 的 hint 诚实化 |
| `_smoke.py`（111 项） | 9 个新测试 + 1 个恢复配对测试 + 4 个依赖被删 API 的测试重写 |

### 3.2 v1.11.0 主要修改文件

| 文件 | 改动 |
| --- | --- |
| `warden/reporter.py` | `_lcp_dir()` 改为按目录段比较；`storage_root` 的反斜杠归一改用单个转义（Windows 上「路径:」行因此恢复） |
| `main.py`（1578 行） | `_failed()` 新增 `kind` / `row_idx`；转发渲染失败入 failures 表；list 与 lookup 共用 `_format_asset_row()`（带 msg_id + sha16）；sha16 未命中回退按 msg_id 查；两处 `except Exception: pass` 改为记日志 |
| `warden/storage.py` | `_write_atomic()`（临时文件 + `os.replace`），`save()` / `_write_pointer()` 复用 |
| `warden/index.py` | `_asset_rows()` 统一解 `forward_meta`；`recent()` 补 `id DESC`；`find_by_sha()` 也返回 `forward_meta` |
| `warden/components.py` | file 段用 `_media_url()` 兜底；forward 的 nodes 也认 `message` 键 |
| `warden/config.py` | 删 `EXPORT_FORMAT_VERSION`；`VERSION=1.11.0` |
| `_conf_schema.json` | 三个枚举补 `options`；迟到缓存 hint 改 5s/20s |
| `_smoke.py`（115 项） | 新 Phase 6 四项；扩展 list / lookup / conf-schema 断言 |

### 3.3 v1.13.0 主要修改文件

| 文件 | 改动 |
| --- | --- |
| `warden/record_admin.py`（新） | 存档读侧纯函数：`scan_records` / `stats` / `record_detail` / `resolve_asset` / `delete_record` / `node_payloads` / `native_forward` / `rebuild_forward` |
| `warden/record_page.py`（新） | `PAGE_PREFIX = "/<plugin>/page"` + `RecordPageApi`；六条路由；处理函数只做 IO 转接（`from astrbot.api.web import request` 惰性导入，离线可测） |
| `pages/dashboard/index.html` / `styles.css` / `app.js` | 列表 + 搜索 + 群过滤 + 节点详情 + 缩略图（cookie 鉴权取图）+ 原地/重建发回 + 删除 |
| `.astrbot-plugin/i18n/zh-CN.json` / `en-US.json` | 页面标题「聊天记录存档」/「Chat records」与显示名 |
| `main.py` | 导入 `RecordPageApi`；`initialize()` 里 try/except 注册（宿主没 `register_web_api` 只记一句日志）；启动日志加 `page=on|off` |
| `_smoke.py`（130 项） | 新 Phase 8 三项：存档读侧、页面路由与处理函数、initialize 注册/跳过 |

### 3.4 v1.14.0 主要修改文件

| 文件 | 改动 |
| --- | --- |
| `warden/config.py` | 六类 `save_*` 开关、`doc_ext_whitelist`、`auto_forward_*`、`style_*` 十项；`_flatten_raw()` 摊平宿主的分组嵌套配置（顶层优先）；`doc_ext_allowed()` 收扩展名与文件名；`update_style()` 返回三元组；`save_type_flags()` / `forward_targets_set()`；`VERSION = "1.14.0"` |
| `warden/policy.py` | `media_kind()` / `kind_allowed()` / `comp_is_animated()` / `blob_is_animated()`；白名单空列表语义反转为「不保存」，`ALL_SENDERS = "*"` |
| `warden/components.py` | `raw_segments(event)`：从 `raw_message` 取原始段（text/at/face/reply）给纯文本条目用 |
| `warden/record.py` | `capture_text()` + `entry.json`；`save_render()` / `has_render()` / `load_entry()` / `meta_filename()`；`capture()` 增 `kind` / `render`；`list_records()` 混排两类条目 |
| `warden/record_admin.py` | `KINDS` / `KIND_LABELS` / `scan_items()` / `entry_item()` / `flat_item()` / `entry_file()` / `resolve_flat()` / `delete_flat()`；`record_detail()` 支持纯文本 |
| `warden/record_page.py` | 路由 8 → 13（新增 `reveal` / `style` / `style_save` / `style_preview` / `render`）；`source=flat` 取图与删除；纯文本条目「发回」给明确错误 |
| `warden/index.py` | `list_assets(kinds / group_id / q)` + `forget_path(path)` |
| `warden/forwarder.py` | `apply_style()` 十参数；`_avatar_mask(shape)` 支持方形圆角；`_fmt_time()`；`_hex_rgb()`；深色只替换默认色 |
| `pages/dashboard/*` | 类型标签 + 带群名目标群下拉 + 结构化/渲染图切换 + 打开目录；`app.js` 重写（490 行） |
| `pages/style/*`（新） | 样式编辑器 + 实时预览（样例里带合成头像，才看得出圆/方差别） |
| `_conf_schema.json` | 6 组 → 9 组（`save_types` / `auto_forward` / `render_style`） |
| `main.py` | 下载前类型门控、`_save_text()`、`_emit_receipt()`、`_auto_forward()`、`_apply_render_style()` / `style_snapshot()` / `update_style()` / `_persist_config()`（写进键所属的 schema 分组，不在顶层留重复键） |
| `_smoke.py`（152 项） | 新 Phase 9 二十一项 |

## 4. 验证记录

已验证：

- `python -X utf8 _smoke.py`：`ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=33, total=164)`（v1.17.0）
- v1.17.0 本机实测（2026-10-29 09:54–09:56，重载后）：日志 `loaded: v1.17.0 | mode=whitelist | groups=1 users=1`、`page=on`；`page/stats` → records 59 / texts 0 / assets 5 / **bytes 9,458,206**（含 record.json + legacy_forward.json，此前只算 assets+render 是 2.5 MB）、flat_total 7490 / flat_bytes 5,266,143,004、**legacy_renders 857 / legacy_bytes 1,310,438,292**、`save_types` 含 `voice: False`；`page/records` → `kind=render` 857、`kind=image` **4155**（拆分前 5012 = 4155 + 857 旧渲染图）、`kind=forward` 60。**857 张旧渲染图没有自动删除**，留给页面上的「清理旧渲染图」按钮。
- `python -X utf8 _smoke.py`：`ALL OK (phase1=22 phase2=16 phase3=21 phase4=26 phase5=27 phase6=4 phase7=12 phase8=3 phase9=21, total=152)`（v1.16.0）
- v1.14.0 本机实测（2026-10-28）：重载后日志 `loaded: v1.14.0 | mode=whitelist | groups=1 users=1`、`page=on`（修复前同一条是 `groups=0 users=0` —— 配置根本没被读到）；`/api/plug/astrbot-plugin-media-warden/page/{stats,groups,style,records,style_preview}` 全部 200（stats：3 条存档 / 7 节点 / 4 资产 / flat 2000；groups：collect `961583271` 与 llm `256678737`，各 3 人；style_preview 返回 `data:image/png;base64,…`）；`/api/plugin/page/entry?name=…&page=style` 返回 `content_path`（标题由前端按 i18n 的 `pages.style.title` 解析）。**没做浏览器内点击验证**。
- 本机插件配置在 `C:\Users\chiriu\.astrbot\data\config\astrbot-plugin-media-warden_config.json`（按 schema 分组嵌套）；当前值 `target_groups=["961583271"]`、`target_users=["2510536451"]`、`match_mode=whitelist` —— 修复配置读取后，抓取范围就是「collect 群 + 風見かずき」。
- v1.14.0 跨群转发实测（2026-10-28，pages API 走真实 bot）：对 2026-10-27 的存档做 `mode=native` → 目标群 `256678737`，`get_msg` 直接回 **「消息不存在」**（事后确认是 **NapCat 11:24 重启后短 id 缓存已清**，不是跨群限制；插件先核验再转发，所以没有乱发）；同一条记录 `mode=rebuild` → `ok: true`，`send_group_forward_msg` 送 3 个节点进 llm 群。结论（**v1.17.1 更正**）：native 的成败取决于 NapCat 进程内短 id 缓存是否还有这条消息，与目标群无关；`_auto_forward` 的 native 失败回退 rebuild 仍是必须的（llm 群里留了一条测试转发，可手动撤回）。
- v1.14.0 页面审计实测（2026-10-28，用户截图驱动）：修复前 `stats` 的 `flat_total` 是 `_flat_rows(limit=2000)` 的长度（假 2000），`records?kind=forward` 只出 8 条、`kind=image` 恒 500；修复后同一批数据返回 `flat_total 7547` / `flat_bytes 5271692289`（≈4.9 GB）、`forward 60`、`image 7491`、`file 0`，条目侧 `bytes 789953`（含 render.png）；`record?id=20261028-141242-961583271-1576078598` → `has_render: true`、`render: "render.png"`、`uin_reliable: false`（该条 6 个节点共用一个 sender.user_id）。类型筛选现在由 `_FLAT_KIND_MAP` 决定（image 含 `forward_render`，forward 含 `forward_json`）。
- `python -X utf8 _smoke.py`：`ALL OK (phase1=22 phase2=16 phase3=21 phase4=25 phase5=27 phase6=4 phase7=12 phase8=3, total=130)`（v1.13.0）
- v1.13.0 页面链路在本机 AstrBot 上实测（2026-10-28）：插件重载后 `/api/plugin/page/entry?name=…&page=dashboard` 返回 `content_path`；页面 HTML 200（标题已被 i18n 换成「聊天记录存档」）；`app.js` / `styles.css` 以 `text/javascript` / `text/css` 200 返回；`/api/plug/<plugin>/page/stats` 与 `/page/records` 返回 3 条真实存档（群 961583271）。**没做浏览器内点击验证**（页面 DOM 只做了语法与 id 交叉检查）
- v1.11.0 的 Windows 路径修复用临时脚本实测过：`BatchResult.storage_root` 从 `None` 变为 `C:/data/warden/g1/`，且 `format_batch()` 输出 `  路径: C:/data/warden/g1/`
- `_conf_schema.json` JSON 解析通过
- **v1.9.5 的本机实测**：8 张线上失败图逐个在 `C:\Users\chiriu\Documents\Tencent Files\` 下按 MD5 命中，单次查找 <1ms；`deadbeef…` / 短名 / 空串 / `None` 均正确返回 `None`（不误报）
- **v1.9.5 的线上实测（20:02:19）**：`used QQ local cache for image: …\Image\C2C\Image2\74760010d20c862b46a660054f601ead.jpg` → `✅ 已保存 …20261011_200219_image_9dbbb5a0.jpg 18749B [链接已重取: localcache:…]` —— 同一张图 19:59 还是 `http 400`
- 单测覆盖：`_url_fileid` 提取、fetch/save 分类、失败文案、三级回退三条路径、失败入库 + attempts 累加、索引复用命中/文件缺失、`/warden fail` + `stats`、`failure_stats`/`prune`、补救候选排序、`get_image` 多参数形态 + base64 返回、`get_msg` 重解析 + 多图挑段、**本地缓存命中零网络调用 / 未命中照旧回退 / `get_media_fallback=false` 时被忽略**、**转发节点图走整条 `_save_forward` 时同样零网络调用且 PNG 正常**；v1.10.0 追加：schema↔配置双向对齐、组件双路径一致、AvatarCache 磁盘/LRU、旧库迁移、空转发失败、引擎告警、Reply 引用、索引按群隔离

未验证：

- **`get_msg` 在真机上能否用 AstrBot 给的 `message_id` 解析成功**（本机只有 stub；`message_id` 的来源已按 `aiocqhttp_platform_adapter.py:228` 核对）。这是 v1.9.2 上线后要看的第一条日志。
- 真机上 `get_image` 对 segment 原始 `file` 的响应（**v1.9.2 已判定这条路对图片无效**，保留它只是为了兜住别的 OneBot 实现与「uuid 恰好还在」的情形）
- `/warden fail` 在非管理员下的拒绝行为（测试用 stub 模拟权限装饰器）

## 5. 未修改范围

- 转发链布局、字体、emoji 渲染逻辑
- blake2b 去重算法与 `assets` 表 schema
- `_save_forward` 的降级策略：仍按 v1.5.0 设计「任一节点图最终失败即整体失败、不落盘」（**v1.9.6 起节点图会先查本地缓存**，但没有 `file` 字段时仍只有 `[is_origin=1, 原 url]` 两个候选）
- `AvatarCache._locks` 按 (`platform`, `sender_id`) 增长、没有回收（每把 `asyncio.Lock` 约百字节，规模受群成员数约束）；不回收是为避免与正在抓取的任务竞态 —— 回收时若另一协程刚取到同一把锁，会变成两把锁各自抓同一张图。真要回收，应改成「引用计数 + 空闲超时」而不是遍历删除
- `localcache` / `aiohttp_fetcher` 的每次调用都重新 listdir / 新建 `ClientSession`（无 memoisation）—— 命中路径的 listdir 在本机是 <1ms（v1.9.5 实测），暂不优化

## 6. 回滚与备份

safe-edit 每次修改都会自动备份到 `C:\Users\chiriu\.irmia\backups\`，可按文件名和时间戳恢复：

```powershell
safe_edit list-backups
safe_edit rollback <file> [<backup_name>]
```

## 7. 下一步建议

0. **遇到「保存失败」的第一件事：先量环境，别先改代码**（§1.-6 的复盘四步）。
   2026-10-11 这一整轮排查的最终答案不在插件里，而在一个 5 个月没升级的 NapCat 上；
   升级后所有转发一次通过。**先看 URL 是不是一生成就死、rkey 是不是几十分钟没换、
   NapCat/QQ 的日期差多少。**
1. 在真实 AstrBot + napcat 环境观察一次失效链接：**v1.9.5 起最先应该看到的是 `used QQ local cache for image: …`** —— 命中本地缓存时连网络都不走，资产正常落盘、回执不再计失败。合并转发里的图则看 **`used QQ local cache for forward node image: …`** 或 **`late local cache hit after Ns`**（v1.9.9 的迟到重查，见 §1.-5；NapCat 正常时**两个都不该出现**，出现了就说明源链接又在失效）。
2. 若仍失败，先确认失败那几张图的 `meta["raw_file"]`（消息段原始的 `data.file`）是不是一个 32 位 MD5 —— 不是的话本地缓存这条路用不上（`find_cached_image` 对短于 16 字符的候选直接放弃）；转发节点图则看节点段有没有 `file` 字段。
3. 转发节点图仍失败时，**先按 §1.-6 量环境**（NapCat 版本 / rkey 是否复用 / URL 是否一生成就过期）——
   2026-10-11 的结论是「坏环境」而不是「架构无解」。只有在环境确认正常、确实偶发时，再按 §1.-5 分辨：
   客户端后来下过那张图（迟到重查没赶上 → 可以调大 `_LATE_CACHE_DELAYS_S`）／客户端从没下过（无解）。
   **不要再把「NapCat 架构边界、无解」当成转发节点图失败的统一结论** —— v1.9.8 那个说法已被
   v1.9.9 修正，v1.9.9 的「只能靠迟到缓存」又被 §1.-6 修正。
3. 若仍失败，日志里 `refetch: …` 会逐跳写清败因（`get_image file=… -> ActionFailed…` / `get_msg {…} -> …`），回执 note 里带最后一跳。
   常见的两种：`get_msg` 报「消息不存在或已被撤回」= 消息太老 / napcat 的 `MessageUnique` 映射已清；能拿到新链接却 `http 400` = 新时的 rkey 也救不回来（要查 napcat 的 `getRkeyData()` 是否拿到 packet rkey）。
4. 打开 `index_reuse_saved=true` 观察几天，确认「索引复用」不会掩盖「文件被人工清理」的情况（当前会明确计为落盘失败）。
5. 若要覆盖转发节点图，需要在展开节点时保留消息 id / `file_id`，再复用 `_refetch_media()`；这是独立的一步。
6. 本机 `_smoke.py` 依赖 aiohttp 导入期桩（`_install_aiohttp_stub_if_missing`）；装了真 aiohttp 的环境会走真实路径，不受影响。
