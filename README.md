# astrbot-plugin-media-warden

[![smoke](https://github.com/kazamisama/astrbot-plugin-media-warden/actions/workflows/smoke.yml/badge.svg)](https://github.com/kazamisama/astrbot-plugin-media-warden/actions/workflows/smoke.yml)

> 群聊素材守门人：监听指定群/用户的非文字消息，按命名模板落盘，支持合并转发渲染/JSON 保存、SQLite 索引、预览回传、并发下载与内容去重。

## 功能

- 群 / 用户黑白名单过滤，群 ID 支持 `platform:group_id` 或裸 ID
- 图片 / 视频 / 语音 / 文件自动下载并按模板落盘
- blake2b 内容去重，命中时复用首存位置
- 合并转发链 PIL 渲染为 PNG（自绘气泡、emoji、头像、嵌套转发），节点超限自动降级 JSON
- SQLite 资产索引，支撑 `/warden list / stats / lookup / export / prune`
- 保存回执与预览图回传
- 并发下载限流、指数退避重试、落盘路径越权防护
- v1.8.0：`/warden` 命令默认仅管理员；export 限制在存储根目录内；头像按平台白名单抓取；透明 PNG 正确透出气泡底色
- v1.9.0：源链接失效补救（OneBot `get_image` 换新链接 → 组件本地副本），失败分类（下载失败 / 落盘失败）并入库，`/warden fail` 可查；可选按索引复用已落盘媒体
- v1.9.1：修 `get_image` 入参 —— 用消息 segment 原始的 `file`（napcat 图片 id）而不是被 `PreProcessStage` 覆盖成 jpeg 文件名的 `component.file_id`，并对 `file`/`file_id`/`id`/`image` 多种参数形态依次尝试（与 AstrBot 核心 `quoted_message/image_resolver.py` 同款），另支持 `data.base64` 返回
- v1.9.2：新增 `get_msg` 重取路径 —— 读 napcat 实现后确认 `get_image` 对图片无效（它只认自己内存里 24h TTL 的消息文件 uuid，而 image 段只回传文件名），改用 `get_msg` 让 napcat 用**当前** rkey 重新解析整条消息拿新链接；每跳败因写进日志与回执
- v1.9.3：修 `get_msg` 返回被解包的问题（aiocqhttp 的 `call_action` 成功时已 `return result["data"]`，原代码只在外壳里找 `message`）
- v1.9.5：**首选 QQ 本地图片缓存** —— 按消息段 MD5（`<md5>.<ext>`）在 `Tencent Files/<QQ号>/Image/C2C/Image2/` 与 `nt_qq/nt_data/Emoji/emoji-recv/*/Ori/` 找图片本体，命中即用、**不走网络**；rkey 过期（http 400）的图因此能正常保存
- v1.9.6：**合并转发的节点图接同一条本地缓存** —— `ForwardNode` 新增 `image_refs` 保留节点 image 段的 `file`(MD5)，`_save_forward` 拉节点图前先查本地缓存
- v1.9.8：确认**转发节点图里的老图救不回来**（NapCat 从不下载转发节点的图片字节，`get_image` 的内存 uuid 表里也没有它们）—— 详见「已知边界」
- v1.9.9：**迟到缓存重查** —— 三级补救全败后隔 `5s`/`20s` 再查一次 QQ 本地缓存（`_LATE_CACHE_DELAYS_S`，合计 25s；接收端客户端常在几秒~几十秒后才把图落盘，插件第一次查必然扑空）
- v1.10.0：**重构 + 补齐** —— 删掉空转配置与死代码（`download(fallback=)` / `download_many` / `from_raw` …），把重复实现合成一份（转发渲染双实现、组件双归一、失败文案三处），补上索引 schema 迁移、空转发记为失败、`reply_to_original` 真正生效（默认改为关闭）、`/warden lookup <sha16>` 与「反复失败」统计；冒烟 111 项
- v1.11.0：**第二轮审计修复** —— Windows 回执路径行（`reporter.py` 里的反斜杠字面量多转义了一层，Windows 路径永远算不出公共目录）、转发渲染失败入库、`/warden list` 显示 sha16、16-hex msg_id 可回退查询、file 段 http 链接兜底、落盘原子写入；冒烟 115 项
- v1.12.0：**聊天记录存档 + 原地重发** —— 引用合并转发发 `/warden 存档` 存成自包含目录（节点结构 + 图片字节）；`/warden 重发` 默认原地转发（保气泡/挂件/头像），失败回退按存档重建；冒烟 127 项
- v1.13.0：**dashboard 存档页面** —— 插件详情里的「页面」直接浏览/搜索/预览/重发/删除存档，图片走鉴权接口按需取；冒烟 130 项
- v1.14.0：**按类型采集 + 分类管理 + 样式页** —— 六类保存开关（图片/动图/视频/纯文本/聊天记录/文档）与扩展名白名单，白名单空列表语义反转为「不保存」；纯文本与聊天记录都是「一个条目两种形态」（结构化 JSON + Pillow 渲染图）；dashboard 按类型分类管理（含老的扁平落盘文件），带群名的目标群下拉与两种转发；可选的自动转发；独立的渲染样式页（宽度/字号/圆角/主色/头像形状/时间格式/深色 + 实时预览）；「重渲染」按当前样式补/刷新旧存档的渲染图，同群同内容不再重复建目录，聊天记录的渲染图只存在于条目里（不再单独成一张「图片」）；冒烟 150 项
- v1.15.0：**配置页重做** —— schema 九组按「监听 → 存什么 → 存到哪 → 下载 → 渲染 → 样式 → 自动转发 → 回执 → 日志」排序，每组标题带序号、hint 只写现状；删掉与 `style_width` 重复的 `forward_image_width`，渲染引擎选项收成 `pil`（只有它实现），`auto_forward_mode` 默认改成 `native`（优先保装扮，失败自动回退；旧配置在「没启用 + 没目标群 + 仍是旧默认」时才跟着升级）；组键保持不变（只改标题）—— 实测组键改名会让 AstrBot 按新 schema 重写配置文件、把旧值丢掉；冒烟 151 项
- v1.16.0：**渲染开关布尔化** —— 三态 `forward_render_mode`(image/json/both) 收成一个开关 `forward_render_image`（开 = 条目里画 `render.png`，关 = 只留 `record.json`）；旧配置的 `json` 自动映射成关闭，`image`/`both` 映射成开启。条目里本来总有 `record.json`，所以不再产生「扁平的额外 JSON 副本」，一条转发只回一个结果；顺手修了内联节点（`{"type":"node","data":{…}}` 包装形态）存档成空壳的问题；冒烟 152 项
- v1.17.0：**审计修复** —— 删共享扁平文件不再连坐（按行删 + 引用归零才删字节）；`resolve_flat` 先 normpath 再判 `_records` 前缀（`./_records/…` 绕不过去了）；纯文本去重按发送人收窄（不同人发同一句话各存一条）；语音补上 `save_voice` 开关、未知类型默认不存；857 张老的扁平转发渲染图单列「旧渲染图」并可一键清理；分页能翻到第 2000 条之后；`/warden stats` 纳入存档、`prune --records` 才删存档；重建转发带原时间戳；纯文本回执带渲染图预览；样式预览不再泄漏临时目录、下载闸门不再被迟到缓存占住；冒烟 164 项。**注：本版「自动转发恒用重建 / 跨群 native 必失败」两条结论是误判，已在 v1.17.1 更正**
- v1.18.0：**去补丁化（一次性归一化）** —— 按「不为旧数据长期留着补丁」的结论，升级时跑**一次** `warden/upgrade.py`（闸门 `<storage_root>/.schema_version` + 索引 `PRAGMA user_version=2`）：条目补 `kind`、删 `legacy_forward.json` 与 `legacy_*` 字段、老 GIF 行 `kind=image` 改成 `gif`、删迁移失败的空壳行；v1.16 之前多存的扁平渲染图（`kind=forward_render`）**先打包成 zip 备份再删**。同时删掉全部兼容分支：`migrate_flat_forwards` / `reconcile_flat_renders` / `purge_orphan_renders` +「清理旧渲染图」按钮 / `backfill_duplicates` / 按扩展名猜类型 / 旧配置键派生 / `_promote_auto_forward_default` / 引擎旧值告警（playwright/wkhtml 现在直接算配置错误）。冒烟 162 项
- v1.18.1：**去补丁化之后的纯重构（行为不变）** —— 三个装不下一屏的函数拆开：`_save_forward`（305 行/六层嵌套）拆成「取节点 / 决定画不画 / 抓头像 / 下载节点图 / 存档 / 渲染 / JSON / 索引写入」八个方法，`on_group_message`（133 行）拆出类型门控、事件身份、media 落盘三块（用 `_Gated` / `_EventIds` 传值），`_recover_original_urls`（83 行）拆出「先按标识、再按顺序」的配对与回填；`main.py` 最后两处 `except: pass` 改为记日志，宿主 getter 的吞咽收拢到 `policy._try_call` 一处；索引类型过滤不再按扩展名二次分类（v1.18.0 归一化后 `kind` 就是真实类型），`upgrade` 里重复的 `except` 合并；冒烟 162 项全绿
- v1.19.0：**存档内视频落字节** —— 以前视频段只剩原始 JSON（url 是带 rkey 的临时链接），页面只能看到 `[video file=... url=...]` 文本，链接一过期就没了；现在 `record.capture` 接 `fetch_video` 把视频下进 `assets/<sha16>.mp4`，详情页可内联播放（base64，上限 64 MB）、「重建转发」用 `base64://` 把视频一起发、渲染图留 `[视频]` 占位。老存档重新存档一次即可补齐（url 仍有效时），补不上页面会明确标注；冒烟 166 项
- v1.20.3：**大动图不再「图片加载失败」** —— 页面取图接口的图片上限以前是 8 MB，QQ 里 9.2 MB 的 GIF 明明存在本地却显示加载失败；现在图和视频统一 64 MB，超限时提示「太大，用『打开目录』看」并带上具体大小。冒烟仍 171 项
- v1.20.2：**补齐提示如实 / 图与视频分开数 / 多层转发能看出层级** —— ①「补齐缺失媒体」不再一律弹「已补齐」（前端 `unwrap()` 丢了服务端 message），改为说清补了几项、还剩几项，一项没补上时用错误色；②列表 pill 不再把视频算成图，分别显示「N 图」「N 视频」；③嵌套（多层）合并转发把 `depth` 写进存档，页面按层缩进标「嵌套第 N 层」，外层那一个 `[forward id=…]` 段改成「[嵌套聊天记录：内容已展开在下方]」，重建转发也不再补重复的占位。冒烟 171 项
- v1.20.1：**关渲染图时节点图也预下载** —— 以前 `forward_render_image: false` 时存档的节点图根本没下（`_archive_image` 只从渲染用的预下载结果里取），全部记成 `download_failed`，点「补齐缺失媒体」又秒补；现在预下载与渲染解耦，预下载键没命中时存档会直连补取。冒烟 170 项
- v1.20.0：**同内容去重不再被 rkey 干扰 + 补齐缺失媒体** —— 内容指纹换成忽略 `url`/`path` 的归一化版本（`origin_sha256_norm`），同一条转发转两次会复用原条目而不是再存一个目录；老记录没有新字段时现算一遍比对，升级后不会「失联」。详情页多了「补齐缺失媒体 (N)」按钮、命令 `/warden 补媒体 [list|<id>|all]`，按记录里剩的 url 重下当时没下成的图片/视频（rkey 过期就补不上，页面如实说还剩几项）；下载失败的原因不再只留一句 `download_failed`，日志里有具体异常。冒烟 169 项
- v1.18.2：**修「删共享文件误删字节」** —— 首存路径是 `…/961583271/2510536451/x.gif`（rel 里是 `/`），去重复用是 `os.path.normpath` 的全反斜杠写法，同一文件两种字符串让 `count_path` 漏数（本库 5,280 行 `/` + 1,423 行反斜杠，932 个文件双写法）；现在首次落盘也归一、启动时把存量行幂等归一、`count_path`/`forget_path` 入参也归一；存档页对「一份字节被多条消息引用」的扁平文件显示「共用 N 条」标签。冒烟 163 项
- v1.17.3：**样式页同一套固定布局**（不再有外层滚动条、面板各自滚动）+ **复用条目不再重复自动转发**（同内容在源群再发一次时跳过）；冒烟 165 项
- v1.17.2：**存档页整页固定** —— 外层不再出现整页滚动条，左右两块面板同高同边距、各自内部滚动（列表 / 详情正文），窄屏改为上下两段各自滚动；纯样式改动，冒烟仍 164 项
- v1.17.1：**纠正「跨群原地转发必失败」的误判** —— NapCat 的 `get_msg` 与 `forward_group_single_msg` 都按**进程内短 id 缓存**（LRU 5000 条，重启清空）找消息，跟目标群无关：缓存里有就能保气泡/挂件/头像发到任何群，缓存丢了（重启 / 被挤掉 / 消息过期）才回「消息不存在」。所以自动转发恢复「先 native、失败回退 rebuild」，`auto_forward_mode` 默认回到 `native`，页面「转发原消息」不再限定原群；失败仍会记成原消息失效（缓存丢了就是丢了）。冒烟 164 项

## 目录

```
astrbot-plugin-media-warden/
  metadata.yaml              # AstrBot 插件元数据
  _conf_schema.json          # 配置 schema（WebUI 编辑）
  requirements.txt           # Pillow>=10.0
  main.py                    # 入口（@register 类 + 钩子 + 8 个命令）
  pages/dashboard/           # 插件页面：存档分类管理（index.html / app.js / styles.css）
  pages/style/               # 插件页面：渲染样式编辑 + 实时预览
  .astrbot-plugin/i18n/      # 页面标题与显示名（zh-CN / en-US）
  warden/
    __init__.py              # 公开符号
    config.py                # MediaWardenConfig dataclass + 校验
    policy.py                # 群 / 用户 / 黑白名单策略
    components.py            # 消息组件归一化 + raw_message 原始 URL 恢复
    reporter.py              # 回执文本组装
    downloader.py            # aiohttp 下载 + fetcher 抽象 + 重试
    storage.py               # 命名模板 + 落盘 + blake2b 去重
    forwarder.py             # PIL 自绘气泡渲染
    index.py                 # SQLite 资产索引
    localcache.py            # 按 MD5 找 QQ 本地图片缓存（rkey 失效时的首选补救）
    avatar.py                # 头像抓取 + 文件缓存
    record.py                # 聊天记录存档（自包含目录：节点 + 图片字节）
    record_admin.py          # 存档读侧（扫描/搜索/汇总/详情/取图/删除/还原节点）
    record_page.py           # 插件页面后端（/<plugin>/page/* 十四个接口）
  README.md                  # 本文件
  CHANGELOG.md               # 变更记录
  HANDOVER.md                # 交接文档
  _smoke.py                  # 离线冒烟测试（mock astrbot.api）
  CONTRIBUTING.md            # 贡献指南
  .github/                   # issue 模板与 CI
```

## 依赖

```bash
pip install Pillow  # forwarder 自绘气泡
```

`aiohttp` 由 AstrBot 运行环境提供；缺包时 downloader 会给出明确错误。

## 部署

把整个目录放到 AstrBot 的 `data/plugins/` 下，**目录名必须保留** `astrbot-plugin-media-warden`。重启 AstrBot 后即可在 WebUI 看到插件。

## 配置

AstrBot WebUI → 插件管理 → astrbot-plugin-media-warden → 配置。schema 按 **9 组**组织，顺序就是配置页里的顺序：① 监听范围 → ② 保存哪些类型 → ③ 存储与索引 → ④ 下载与重试 → ⑤ 聊天记录渲染 → ⑥ 渲染样式 → ⑦ 自动转发 → ⑧ 保存后的回执 → ⑨ 日志。每组标题都带序号，hint 只写「这一项现在会怎样」。

> **v1.14.0 语义变化**：白名单模式下 **空列表 = 不保存**（以前是「不限」）。要「全存」请用 `match_mode=blacklist`（空列表 = 不限），或把 `target_users` 写成 `["*"]`。

**① 监听范围（`filter`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `match_mode` | `whitelist` | `whitelist`（群+人都命中才存，两个列表都空 = 不存）\| `blacklist`（任一维度命中就跳过，列表空 = 不限） |
| `target_groups` | `[]` | 要保存的群，`platform:group_id` 或裸群号 |
| `target_users` | `[]` | 要保存的发送人 user_id；`*` = 该群所有人 |

**② 保存哪些类型（`save_types`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `save_images` | `true` | 静态图片（动图走下一项） |
| `save_gifs` | `true` | 动图（GIF / APNG / 动态 WebP）。先按扩展名归类，落盘后按字节复核：多帧记 `gif`、单帧归 `image`（进「图片」分类） |
| `save_videos` | `true` | 视频 |
| `save_files` | `true` | 文档（群文件），扩展名受 `doc_ext_whitelist` 约束 |
| `save_voice` | `false` | 语音 / 录音消息（v1.17.0 才有开关：以前它不在开关表里，六类全关也会照存） |
| `doc_ext_whitelist` | `[pdf,doc,docx,xls,xlsx,ppt,pptx,txt,md,csv,zip,7z,rar,epub]` | 文档扩展名白名单；**空 = 不限**（注意与 ① 群/人白名单的空 = 不存相反） |
| `save_texts` | `false` | 纯文本消息：`entry.json` + `render.png`，共用一个条目 |
| `save_forwards` | `true` | 聊天记录：`record.json` + `render.png`，共用一个条目 |

**③ 存储与索引（`storage`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `storage_root` | `data/warden` | 落盘根目录（相对 AstrBot 工作目录） |
| `filename_pattern` | 见下 | 扁平文件的命名模板 |
| `max_file_size_mb` | `100` | 单文件上限（>= 1）；头像另有固定 5MB 上限 |
| `dedupe` | `true` | 相同内容只落盘一份（blake2b 指纹 + `.ptr` 指针） |
| `enable_index_db` | `true` | 启用 SQLite 索引；关掉后 `/warden list/stats/export/lookup/prune` 与页面分类管理都不可用 |
| `index_reuse_saved` | `false` | 同一消息同一下标已存过且文件仍在 → 跳过下载（文件被删会明确报错） |

**④ 下载与重试（`download`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `download_timeout_s` | `30` | 单次下载超时（秒） |
| `download_retries` | `2` | 只重试超时/连接错/5xx/429，4xx 直接失败 |
| `max_concurrent` | `4` | 同一条消息的并发下载数（节点图与头像共用） |
| `get_media_fallback` | `true` | 下载失败的补救链：QQ 本地缓存（按 MD5）→ `get_image` / `get_msg` 换新链接 → 组件本地副本 → 5s/20s 后再查本地缓存 |

**⑤ 聊天记录渲染（`forward`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `forward_render_image` | `true` | 开：条目里画一张 `render.png`（结构化数据本来就在 `record.json`）；关：只留结构化记录。v1.18.0 起旧键 `forward_render_mode` 不再解析（缺新键就按默认开） |
| `forward_render_max_nodes` | `30` | 超过就只存结构化、不出图 |
| `forward_image_engine` | `pil` | 只有 `pil`（纯 Python，无外部依赖）；playwright/wkhtml 的旧值从 v1.18.0 起算配置错误（回落默认 `pil` 并在日志里说明） |
| `enable_user_avatar` | `true` | 渲染时画头像（qlogo.cn，失败静默跳过） |
| `avatar_size` | `36` | 头像直径（像素） |
| `avatar_timeout_s` | `10` | 头像抓取超时（qlogo 冷启动可能要 5s+） |
| `avatar_cache_size_mb` | `50` | 头像缓存上限，超出按 mtime LRU 删 |
| `avatar_platforms` | `["aiocqhttp", "qq_official"]` | 允许抓头像的平台（napcat/lagrange 的平台名是 `aiocqhttp`）；**留空 = 不抓任何平台的头像** |

**⑥ 渲染样式（`render_style`）** —— dashboard 的「渲染样式」页有实时预览，建议在那里调。

| 字段 | 默认 | 说明 |
|---|---|---|
| `style_width` | `720` | 画布宽度（200..3000）；**唯一的宽度旋钮**（旧的 `forward_image_width` 已删） |
| `style_font_size` | `15` | 正文字号（8..48） |
| `style_radius` | `12` | 气泡圆角（0..96） |
| `style_avatar_shape` | `circle` | 头像形状 `circle` / `square` |
| `style_time_format` | `%H:%M` | 时间格式（strftime） |
| `style_dark` | `false` | 深色配色（只替换仍是默认值的颜色） |
| `style_bg_color` | `#f5f6f7` | 背景色 |
| `style_bubble_color` | `#ffffff` | 气泡色 |
| `style_text_color` | `#1c1f24` | 正文色 |
| `style_dim_color` | `#8a9099` | 次要文字/时间色 |

**⑦ 自动转发（`auto_forward`，可选）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `auto_forward_enabled` | `false` | 收到聊天记录后自动转发到目标群 |
| `forward_target_groups` | `[]` | 目标群（可多个，空 = 不转发） |
| `auto_forward_mode` | `native` | `native`（先转发原消息，保气泡/挂件/头像；缓存里没有原消息时自动回退重建）\| `rebuild`（直接按存档重拼，无装扮，永远可用）。原地转发能否成功取决于 NapCat 的短 id 缓存（进程内 LRU 5000，重启清空），**与目标群无关** |

**⑧ 保存后的回执（`reply`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `reply_preview` | `true` | 回执附图预览（优先渲染图，其次第一张图） |
| `reply_to_original` | `false` | 回执用 Reply 引用原消息（适配器不支持时退化为纯文本） |
| `at_on_save_failure` | `true` | 有失败项时 @ 触发消息的人 |

**⑨ 日志（`logging`）**

| 字段 | 默认 | 说明 |
|---|---|---|
| `log_to_stdout` | `true` | 把插件日志打到标准输出；关掉后失败仍进索引，可用 `/warden fail` 查 |

**默认命名模板**：

```
{group_id}/{sender_id}/{date}_{time}_{kind}_{short_id}{ext}
```

可用变量：`{platform}` `{group_id}` `{sender_id}` `{sender_name}` `{date}` `{time}` `{msg_id}` `{idx}` `{safe_name}` `{ext}` `{kind}` `{short_id}`

## 工作流程

匹配到目标群 + 用户且消息含媒体或转发组件后：

1. **策略过滤**（policy）—— 群 / 用户白名单或黑名单
2. **组件识别**（components）—— OneBot v11 segment 归一化为 `Component`，并从 `raw_message` 恢复被 PreProcessStage 覆盖的原始 http URL
3. **并发下载**（downloader）—— aiohttp 流式抓取，带超时和大小限制；transient 错误按 `download_retries` 指数退避重试，4xx 立即失败。失败后若 `get_media_fallback=true`：先向 bot 发 `get_image` 换一条新链接重下（候选值优先用 segment 原始的 `file`，参数形态 `file`/`file_id`/`id`/`image` 逐个试），再发 `get_msg` 让 napcat 用当前 rkey 重新解析整条消息拿新链接，最后回退组件本地缓存副本；都失败才计失败，原始错误保留，每一跳的败因写进日志与回执
4. **命名 + 落盘**（storage）—— 按模板渲染相对路径，blake2b 去重，落盘前做路径越权检查；`index_reuse_saved=true` 且同消息同下标已落盘时跳过下载与落盘
5. **转发链处理**（forwarder）—— 拉取/展开嵌套节点，按 `avatar_platforms` 白名单抓头像，按 `forward_render_image` 决定是否渲染 PNG（结构化数据始终写进条目 `record.json`）；透明图片做 alpha 合成
6. **索引写入**（index）—— SQLite 记录每条资产；失败项写入 `failures` 表（同 subject 累计 `attempts`）
7. **回执**（reporter）—— 汇总成功 / 失败 / 去重复用 / 用时，并按原因分叉文案（下载失败（源链接失效） vs 落盘失败）
8. **预览回传** —— 优先回传渲染图（聊天记录 / 纯文本条目的 `render.png`），其次第一张 image；无 `image_result` 时降级为文本路径

## 存储目录

默认落盘结构（`filename_pattern` 可改）：

```
<storage_root>/
  <group_id>/<sender_id>/<date>_<time>_<kind>_<short_id><ext>
  _records/<record_id>/record.json  # 聊天记录条目（结构化：节点 + 资产索引）
  _records/<record_id>/entry.json   # 纯文本条目（结构化：段 + 元数据）
  _records/<record_id>/render.png   # 条目的 Pillow 渲染图（两种条目共用字段）
  _records/<record_id>/assets/      # 该记录里的图片字节
  _warden.db                       # SQLite 资产索引
  _blake/<aa>/<bb>/<hex>.ptr       # blake2b 去重指针
  _avatars/                        # 头像文件缓存
  _export.json                     # /warden export 默认导出
```

例：`data/warden/961583271/2510536451/20260617_144530_image_a1b2c3d4.jpg`

以 `_` 开头的都是插件副产物：

- **`_warden.db`** —— 每存一个文件记一条元数据，支撑 `/warden list|stats|lookup|export|prune`。删除只丢台账，已存文件不受影响。
- **`_blake/`** —— 内容指纹指针，指向首存真实文件。命中则复用原路径，不重复写盘。
- **`_avatars/`** —— 转发节点头像缓存，按容量 LRU 淘汰。
- **`.schema_version`** —— v1.18.0 的一次性归一化闸门（内容是 `2`）。删掉它会让插件下次启动**重跑**归一化（幂等，已删的不会二次删；但旧渲染图不会再备份，因为已经不在了）。
- **`_records/`** —— 条目存档（v1.12.0，v1.14.0 并入纯文本）：**聊天记录**一条合并转发一个目录（`record.json` 原样保留每节点每段的结构，`assets/` 存节点内图片的**字节**，blake2b-16 命名、同字节只落一份）；**纯文本**一条消息一个目录（`entry.json` 存段与元数据）。两者都能再写一张 `render.png`，页面里「结构化 / 渲染图」切换就是读这两样。老的 `<group>/<sender>/…_forward_json_*.json` 只留 url、rkey 一过期就废；`missing` 字段如实记录取不到的图。

## 命令

所有 `/warden` 命令默认仅管理员可调用（`PermissionType.ADMIN`）。

| 命令 | 说明 |
|---|---|
| `/warden list [N]` | 列最近 N 条索引记录（默认 10，上限 50），时间可读，带 msg_id 与 sha16（v1.11.0 起与 lookup 共用同一行格式） |
| `/warden stats` | 资产统计：索引行数 / 类型分布 / 索引字节（含去重后）/ **存档条目数与占用** / db 路径（v1.17.0 起纳入 `_records/`） |
| `/warden export [path]` | 导出索引为 JSON；path 必须落在 `storage_root` 内，默认 `_export.json` |
| `/warden lookup <msg_id> [#idx]` / `<sha16>` | 反向查询该消息保存了哪些资产，可选按 idx 过滤；传 16 位 hex 则按内容指纹查它出现过几次（v1.10.0） |
| `/warden prune <days>` | 从索引删除 N 天前的记录（不删实际文件），同期失败记录一并清掉并分别报数（v1.10.0）。加 `--records` 才会连 `_records/` 条目目录一起删（v1.17.0） |
| `/warden fail [N]` | 列最近 N 条保存失败（默认 10，上限 50），带 attempts 与 subject；表头带累计次数与「反复失败」top（v1.9.0+ / v1.10.0 补统计） |
| `/warden 存档 [msg_id]` | **引用（回复）**一条合并转发后发它，把节点 + 节点内图片字节存成 `_records/<id>/`；也可直接转发给机器人，或给 OneBot `message_id`（v1.12.0） |
| `/warden 重发 [原地\|重建] [record_id\|list]` | 重发存档：默认先**原地**（`forward_group_single_msg`，让 QQ 把原消息再转发一遍，气泡/挂件/头像全保），不行再按存档**重建**合并转发（装饰会丢）；`list` 列最近 10 条（v1.12.0） |

## 存档页面（dashboard）

存档不只能用命令管，WebUI 里也能点开看：

1. dashboard → 插件 → `astrbot-plugin-media-warden` → 页面 `dashboard`（标题「聊天记录存档」）
2. 左侧列表：时间 / 群号 / 节点数 / 图片数 / 首节点预览；顶部输入框按「发送者 / 文本 / 群号 / 记录 id」搜索，下拉框按群过滤
3. 右侧详情：逐节点列出每段内容（文本 / 图片缩略图 / 表情 / @），缩略图点开看原图；取不到图的段会标出 `missing` 原因
4. 详情底部：**发到**（目标群下拉，带群名，默认原群）+ **转发原消息**（转原消息本体，气泡/挂件/头像/原时间戳都在，要求原消息还在群里，过期会自动改用重建）+ **重建转发**（按存档重拼，内容一致但没有装扮，随时可用）+ 重渲染 / 打开目录 / 删除（删整个 `_records/<id>/`，不可恢复）

**v1.14.0 分类管理**：顶部类型标签（全部 / 图片 / 动图 / 视频 / 文档 / 语音 / 纯文本 / 聊天记录）同时覆盖两类保存物 —— `_records/` 里的条目，以及老的扁平落盘文件（索引库里一行一条，按 `kind` 分类）。右侧「结构化 / 渲染图」切换看同一个条目的两种形态；「发到」的目标群下拉带群名（默认这条记录原来的群），两个转发按钮都用它 —— **转发原消息**保装扮但依赖原消息还在，**重建转发**不依赖、但没有装扮；纯文本条目没有合并转发可发，只提供浏览/删除/打开目录。

「重渲染」按**当前**样式重画这条的 `render.png` —— v1.14.0 之前存的条目没有渲染图（标签都不出现），改了样式之后旧图也还是旧样式，两者都靠它。同一份内容被转发两次时（同一个群内），**不会**再建第二个目录：直接复用原来那条（`reused`，图片也不再下一次），页面只看到一条。跨群不合并 —— 同一内容出现在别的群各存一份是对的。老版本已经存成两个目录的，第二条仍带 `duplicate_of` 显示「内容重复」徽章（不会自动删）。

聊天记录的渲染图以前会**再落一份扁平 PNG**，于是「图片」分类里多出一条、磁盘上两份字节。现在它只存在于条目目录的 `render.png`（只有存档失败时才回退扁平落盘）；v1.18.0 的一次性归一化会把这批多出来的扁平副本**打包备份后删掉**（见下），条目里的 `render.png` 一律不动。

**一次性归一化（v1.18.0，启动时只跑一次）**：历史上叠过三层「补丁式兼容」—— 扁平聊天记录迁成条目、扁平渲染图收进条目、重复条目回填标记。按「长远考虑、不为旧数据长期留补丁」的结论，这些在 v1.18.0 收敛成**一次** `warden/upgrade.py`：闸门是 `<storage_root>/.schema_version`（内容 `2`）与索引 `PRAGMA user_version=2`，跑过就跳过（日志 `schema v2: 已归一化,跳过`），中途崩溃下次启动重跑且幂等。它做四件事：① 老条目补 `kind`、删 `legacy_forward.json` 与 `legacy_*` 字段；② 索引里老的 GIF 行 `kind=image` 改成 `gif`；③ 删迁移失败留下的空壳行（≤256 B、`nodes:null` 的扁平 forward JSON）；④ v1.16 之前多存的扁平渲染图（`kind=forward_render`，本机 857 张 / 1.25 GB）**先打包成 zip**（默认 `<storage_root>` 旁边的 `warden_legacy_renders_v1.18.zip`，含 `manifest.json` + 文件）再删行删文件。跑完日志一行 `upgrade v1.18: entries=… legacy_removed=… gif_rows=… junk_rows=… renders=… rows/… files/… B (backup=…)`。**升级前请读 CHANGELOG 的「升级前必读」**：`legacy_forward.json` 会被删，v1.12 之前那些**没有条目**的老扁平聊天记录 JSON 也不再被自动迁移。

类型标签会标出**当前关掉的类别**（「纯文本 · 关」并淡化），空列表也直接说原因（这一类关了 / 还没存过 / 群和人还没配）—— v1.14.0 起各类都是开关，v1.17.0 补上语音，没有「默认自动存」的类型，页面上不再写含糊的「发图会自动保存」。

**关于老的扁平渲染图**：v1.16 之前每存一条聊天记录，除了条目里的 `render.png`，还会额外落一张扁平的 `*_forward_image_*.png`（索引 `kind=forward_render`）。v1.17.0 曾把它们单列「旧渲染图」并给了「清理旧渲染图」按钮；v1.18.0 起归一化会在备份后直接清掉这批数据，类型标签与按钮一并下线 —— **条目里的 `render.png` 不受影响**。

几个容易踩的点都在页面里处理掉了：老 GIF 行在 v1.18.0 归一化时已改成 `kind=gif`，不再需要按扩展名二次筛选；「转发原消息」失败且原因是原消息不存在时，会往条目元数据写 `native_unavailable`，之后那条按钮就是灰的、头部显示「原消息已失效」（网络抖动不写，避免一次超时永久禁掉）；删除用页内蒙层确认（桌面版的 `window.confirm` 不保证弹得出来）；列表标题写「共 N 条 · 已加载 M」，和底部的「加载更多」不再打架。

页面后端是插件自己注册的 Web API：`/<plugin>/page/{stats,records,record,asset,thumb,groups,resend,render,delete,reveal,style,style_save,style_preview}`，走页面桥（带 dashboard 鉴权）。宿主是没有 `context.register_web_api` 的老版本 AstrBot 时，启动日志里只说一句「page api unavailable」，命令照常工作。

缩略图经 `page/thumb` 读成 base64 内联显示：`<img>` 直接指后端 URL 带不上 Authorization 头，而桌面版（Tauri）不一定留着 dashboard 的 cookie —— 桥接接口两边都通。单张 >8 MB 时只回错误、让用户走下载。

> 页面**不做** QQ 那种气泡渲染 —— 浏览就是普通 HTML 列表 + 原图；想保留装扮只有「原地转发」一条路（见「已知边界」）。

## 样式页（dashboard）

dashboard → 插件 → `astrbot-plugin-media-warden` → 页面 `style`（标题「渲染样式」）：

- 可调：画布宽度、正文字号、气泡圆角、背景/气泡/正文/次要用色、头像形状（圆/方）、时间格式（strftime）、深色配色
- 右侧**实时预览**用当前参数渲染一张样例对话（含合成头像，才看得出圆形/方形差别）
- 「保存到插件配置」写回宿主的插件配置文件，同时立刻灌进运行中的渲染器（不重启、不重载也生效）
- 深色只替换**仍是默认值**的颜色：你自己挑过的主色不会被深色模式改掉

## 离线自测

```bash
cd astrbot-plugin-media-warden
python -X utf8 _smoke.py
python -m pytest -q _smoke.py
```

无外部 AstrBot 依赖，不发起真实网络。当前共 **162 项**（Phase 1: 22 / Phase 2: 16 / Phase 3: 21 / Phase 4: 26 / Phase 5: 27 / Phase 6: 4 / Phase 7: 12 / Phase 8: 3 / Phase 9: 31），覆盖：

- 配置加载 / 校验 / 非法字段按字段容错并保留有效值；**宿主的 schema 分组嵌套配置**能被摊平读取（顶层同名键优先）
- 策略匹配：白名单 / 黑名单 / 私聊跳过 / 空列表不限 / 群 ID 大小写归一
- 组件识别与 raw_message URL 恢复
- 落盘 roundtrip、blake2b 去重、路径越权防护、篡改/内容变更指针拒绝
- 下载：aiohttp mock、超限中断、指数退避、并发保序
- 下载补救链：**QQ 本地缓存按 MD5（命中即不碰网络；含合并转发节点图）**、本地副本回退、`get_image` 重取新链接（含候选排序与多参数形态 / base64 返回）、`get_msg` 重解析拿新链接（含多图挑段）、**全败后迟到缓存重查**、全败保留原始错误
- 失败分类与回执文案（下载失败 vs 落盘失败）、失败入库与 attempts 累加、`/warden fail`
- 索引复用（命中 / 文件已缺失时如实报错）
- 转发链：JSON / PNG / both / 超限降级 / 嵌套展开 / 透明图 alpha 合成 / json 模式不抓头像
- 索引：写入 / recent / sha 查复用 / stats / export / lookup / prune / failure_stats
- 预览回传与命令权限注册
- v1.10.0 补齐：`_conf_schema.json` 与配置字段双向对齐（漏配 / 多配都红）、组件 dict 与 object 两条路径归一化结果一致、`AvatarCache` 磁盘命中与 LRU 淘汰、旧库 schema 迁移（含幂等）、空转发记为失败、非 `pil` 引擎告警、`Reply` 引用、索引复用按 platform/group 隔离、raw_message 恢复链接按标识配对（顺序错位也不错领）
- v1.11.0 补齐（Phase 6）：Windows 反斜杠路径算出公共目录且不切出半截目录、file 段把 http 链接放在 `file` 上仍可下载、转发渲染失败在 failures 表留下 `forward_render` 行、落盘后没有 `.part-` 残骸
- v1.13.0 补齐（Phase 8）：存档读侧的扫描/搜索/汇总/详情/取图/删除（含路径越界与不存在的资产）、插件页面 6 条路由的注册与处理函数（含原地失败回退重建、鉴权取图）、`initialize()` 在有无 `register_web_api` 两种宿主下的行为
- v1.14.0 补齐（Phase 9）：六类开关与动图判定、扩展名白名单（扩展名/文件名两种入参）、纯文本条目往返与端到端落盘、禁用类型与坏扩展名被丢弃、聊天记录「一个条目两种形态」、分类扫描（含老的扁平文件）、自动转发到多群、`native` 失败回退 `rebuild`、`apply_style` 与头像形状、样式保存写进 schema 分组、`update_style` 三元组与样式页路由
- v1.12.0 补齐（Phase 7）：存档往返（`record.json` + `assets/` + `build_nodes` 还原）、同字节只落一份、单图失败只进 `missing`、列表倒序与坏目录容错、`image_ref` 规则、引用合并转发按 reply id 取节点、引用普通消息时 `get_msg` 找 forward 段、无引用时的可读提示、原地转发用 OneBot 短 id、老存档拒绝原地转发、默认先原地失败回退重建

## 已知边界

- 主力验证平台为 OneBot/QQ 系（`aiocqhttp`、`qq_official`），其他平台的消息组件 schema 未逐一验证
- `playwright` / `wkhtml` 渲染引擎从未实现：v1.18.0 起这两个值直接算**配置错误**（回落默认 `pil` 并在日志里说明）；v1.10.0–v1.17.x 的行为是「受理 + 告警，实际仍用 PIL」
- 私聊 / 系统消息默认跳过
- 透明 GIF / WebP 动画按首帧处理，未做帧动画合成
- 命令权限、头像网络抓取、`image_result` 协议差异需要真实 AstrBot runtime 验证
- 转发节点图（`_save_forward`）走的是节点内嵌 url + 节点 image 段的 `file`(MD5)：**v1.9.6 起会先查 QQ 本地缓存**，但节点段没有 `file` 字段（非 napcat 实现 / 外链图）时仍只有 `[is_origin=1, 原 url]` 两个候选，任一失败即按 v1.5.0 设计整体失败、不落盘
- **转发节点图里的老图**（v1.9.8/v1.9.9 定论）：NapCat 自己不下载转发节点里的图片字节（`get_forward_msg` 只给元数据），`get_image` 也救不了（它先查 `FileNapCatOneBotUUID` 这个**进程内内存 map**，节点图的 MD5 从未进过那里，直接 `file not found`）。**但接收端 QQ 客户端会在随后几秒~几十秒把图落到本地缓存** —— v1.9.9 的「迟到缓存重查」就是等这个（`late local cache hit after Ns`）。仍失败的只剩「客户端自始至终没下过那张图」这一种。**普通图片**不受此限：QQ 客户端会下，v1.9.5 能命中 `Image/C2C/Image2`
- **QQ 本地缓存补救依赖 QQ 客户端自己的缓存目录**（`Tencent Files/<QQ号>/…`，Windows 默认在“文档”下）：缓存被清理、机器人跑在与 QQ 不同的机器上、或消息段的 `data.file` 不是 32 位 MD5 时，这条路用不上，会照旧返回失败
- **合并转发里的装扮（气泡 / 挂件 / 头像）**：2026-10-27 实测两种重发方式结论不同 —— ①**重建节点**（存档 → `Nodes` → `send_group_forward_msg`）只有昵称 + 内容，装饰全丢；②**原地转发**（`forward_group_single_msg`）让 QQ 自己把原消息再转发一次，气泡、挂件、头像、原时间戳全部保留。因此 `/warden 重发` 默认走原地，**但原地依赖原消息仍在服务端**：消息被撤回 / 过期 / 清理后只能回落存档重建（装饰丢失）。
- **跨群「原地」转发其实可用（v1.17.1 更正）**：以前把「发到别的群回消息不存在」当成 peer 限定，读了 NapCat 源码才发现 `get_msg` / `forward_group_single_msg` 都是 `getMsgIdAndPeerByShortId(shortId)` 查**进程内缓存**（`msgIdMap`/`msgDataMap`，LRU 5000 条，重启清空）—— 缓存里有就能发到任何群，缓存里没有才叫「消息不存在」。所以能不能保装扮取决于「机器人启动后是否见过这条消息、有没有被 5000 条挤掉、原消息是否过期」，**不是目标群**。
- **同一条「原地」还受「原消息还在」约束**：缓存被清（NapCat 重启）、被 LRU 挤掉、消息过期或被撤回后，发回原群同样失败，只能回落重建（装饰丢失）。失败会被记进条目的 `native_unavailable`，页面把「转发原消息」置灰。
- **两套 message_id 不能混用**：`forward_group_single_msg` 只认 OneBot 层的短 `message_id`（如 `1481691615`），段里的长 `msgId`（`7701368446137848000`）会回「无法找到消息」；而 `get_forward_msg` 认的正是段里的长 id。插件里 `root_msg_id` 一律存短的，`get_msg` 只用于核验目标是不是合并转发。
- 存档**不解析语音字节**（还原时是占位文本）；**视频自 v1.19.0 起会落字节**（详情页内联播放、重建转发用 `base64://` 一起发），老存档里当时没下的视频/图片，只要 url 还有效，点详情里的「补齐缺失媒体」或发 `/warden 补媒体 <record_id|all>` 就能补（v1.20.0；内容指纹命中时重新存档一次也会顺手补），rkey 过期则补不上、页面会明确标注；节点里只有 `file=<md5>` 没有可下载 url 的图会进 `missing`（NapCat 本来就不下节点图）
- 高并发群发场景尚未压测

## 后续可选

- 其他平台 adapter 的消息组件扩展
- 媒体分类子目录 + `metadata.json` 索引
- 头像 URL 模板配置，按平台自定义头像源
