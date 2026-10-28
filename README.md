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
    record_page.py           # 插件页面后端（/<plugin>/page/* 十三个接口）
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

AstrBot WebUI → 插件管理 → astrbot-plugin-media-warden → 配置。schema 按 9 组组织：监听范围 / 保存与索引 / 下载与并发 / 日志 / 转发渲染 / 回复行为 / **保存类型** / **自动转发** / **渲染样式**。

> **v1.14.0 语义变化**：白名单模式下 **空列表 = 不保存**（以前是「不限」）。要「全存」请用 `match_mode=blacklist`（空列表 = 不限），或把 `target_users` 写成 `["*"]`。

| 字段 | 默认 | 说明 |
|---|---|---|
| `target_groups` | `[]` | 群白名单/黑名单，格式 `platform:group_id` 或裸 ID |
| `target_users` | `[]` | 用户白名单/黑名单（sender.user_id） |
| `match_mode` | `whitelist` | `whitelist` \| `blacklist` |
| `storage_root` | `data/warden` | 落盘根目录，相对 AstrBot 工作目录 |
| `filename_pattern` | 见下 | 命名模板 |
| `max_file_size_mb` | `100` | 单文件大小上限（必须 >= 1） |
| `dedupe` | `true` | 基于 blake2b 内容指纹去重 |
| `enable_index_db` | `true` | 是否启用 SQLite 索引 |
| `index_reuse_saved` | `false` | 同消息同下标已落盘过且文件仍在时直接复用（v1.9.0+） |
| `download_retries` | `2` | transient 错误重试次数（>= 0） |
| `max_concurrent` | `4` | 并发下载上限（>= 1） |
| `download_timeout_s` | `30` | 单次下载超时（> 0） |
| `get_media_fallback` | `true` | 下载失败后的补救链：QQ 本地缓存（按 MD5）→ OneBot `get_image` / `get_msg` 重取链接 → 组件本地副本 → 迟到缓存重查（v1.9.0+，v1.9.1 修入参，v1.9.2 加 `get_msg`，v1.9.9 加迟到重查） |
| `forward_render_mode` | `image` | `image` \| `json` \| `both` |
| `forward_image_engine` | `pil` | `pil` \| `playwright` \| `wkhtml`（仅 `pil` 实现；选别的会在启动日志明确告警并继续用 PIL 渲染，不静默降级 JSON） |
| `forward_image_width` | `720` | 转发渲染图宽度（>= 1） |
| `forward_render_max_nodes` | `30` | 单条转发渲染最大节点数，超限降级 JSON（>= 1） |
| `enable_user_avatar` | `true` | 是否在转发渲染中绘制节点头像 |
| `avatar_size` | `36` | 头像直径（像素） |
| `avatar_timeout_s` | `10` | 单头像抓取超时 |
| `avatar_cache_size_mb` | `50` | 头像缓存容量上限（>= 0） |
| `avatar_platforms` | `["aiocqhttp", "qq_official"]` | 允许抓取头像的平台白名单 |
| `reply_to_original` | `false` | 是否用 Reply 引用原消息回复（v1.10.0 起真正生效；适配器不支持 `Reply` 或拿不到 msg_id 时退化为纯文本；默认关闭 = v1.9.x 的实际行为） |
| `reply_preview` | `true` | 是否附带第一张图作预览 |
| `at_on_save_failure` | `true` | 保存失败时 @ 触发消息的监听对象 |
| `log_to_stdout` | `true` | 是否把匹配消息打到日志 |
| `save_images` | `true` | 保存静态图片（动图走 save_gifs） |
| `save_gifs` | `true` | 保存动图（gif / apng / 动画 webp；按扩展名 + 字节特征判定，不依赖 PIL） |
| `save_videos` | `true` | 保存视频 |
| `save_files` | `true` | 保存文档文件（受 doc_ext_whitelist 约束） |
| `save_forwards` | `true` | 保存合并转发（落 _records/<id>/，一个条目两种形态：record.json + render.png） |
| `save_texts` | `false` | 保存纯文本消息（entry.json + render.png 共用一个条目） |
| `doc_ext_whitelist` | `[pdf,doc,docx,xls,xlsx,ppt,pptx,txt,md,csv,zip,7z,rar,epub]` | 文档扩展名白名单；**空 = 不限制** |
| `auto_forward_enabled` | `false` | 收到聊天记录后自动转发到目标群（可选功能） |
| `auto_forward_mode` | `rebuild` | rebuild（重建，无装扮）/ native（先试原地保装扮；跨群基本必失败，会自动回退重建） |
| `forward_target_groups` | `[]` | 自动转发的目标群（可多个，空 = 不转发） |
| `style_width` | `720` | 渲染画布宽度（200..3000） |
| `style_bg_color` | `#f5f6f7` | 背景色 |
| `style_bubble_color` | `#ffffff` | 气泡色 |
| `style_text_color` | `#1c1f24` | 正文色 |
| `style_dim_color` | `#8a9099` | 次要文字/时间色 |
| `style_radius` | `12` | 气泡圆角（0..96） |
| `style_font_size` | `15` | 正文字号（8..48） |
| `style_avatar_shape` | `circle` | 头像形状 circle / square |
| `style_time_format` | `%H:%M` | 时间格式（strftime） |
| `style_dark` | `false` | 深色配色（只替换仍是默认值的颜色，不动你自己挑的色） |

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
5. **转发链处理**（forwarder）—— 拉取/展开嵌套节点，按 `avatar_platforms` 白名单抓头像，按 `forward_render_mode` 渲染 PNG 或保存 JSON；透明图片做 alpha 合成
6. **索引写入**（index）—— SQLite 记录每条资产；失败项写入 `failures` 表（同 subject 累计 `attempts`）
7. **回执**（reporter）—— 汇总成功 / 失败 / 去重复用 / 用时，并按原因分叉文案（下载失败（源链接失效） vs 落盘失败）
8. **预览回传** —— 优先回传 `forward_render` 图，其次第一张 image；无 `image_result` 时降级为文本路径

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
- **`_records/`** —— 条目存档（v1.12.0，v1.14.0 并入纯文本）：**聊天记录**一条合并转发一个目录（`record.json` 原样保留每节点每段的结构，`assets/` 存节点内图片的**字节**，blake2b-16 命名、同字节只落一份）；**纯文本**一条消息一个目录（`entry.json` 存段与元数据）。两者都能再写一张 `render.png`，页面里「结构化 / 渲染图」切换就是读这两样。老的 `<group>/<sender>/…_forward_json_*.json` 只留 url、rkey 一过期就废；`missing` 字段如实记录取不到的图。

## 命令

所有 `/warden` 命令默认仅管理员可调用（`PermissionType.ADMIN`）。

| 命令 | 说明 |
|---|---|
| `/warden list [N]` | 列最近 N 条索引记录（默认 10，上限 50），时间可读，带 msg_id 与 sha16（v1.11.0 起与 lookup 共用同一行格式） |
| `/warden stats` | 资产统计：总数 / 类型分布 / 总字节数 / db 路径 |
| `/warden export [path]` | 导出索引为 JSON；path 必须落在 `storage_root` 内，默认 `_export.json` |
| `/warden lookup <msg_id> [#idx]` / `<sha16>` | 反向查询该消息保存了哪些资产，可选按 idx 过滤；传 16 位 hex 则按内容指纹查它出现过几次（v1.10.0） |
| `/warden prune <days>` | 从索引删除 N 天前的记录（不删实际文件），同期失败记录一并清掉并分别报数（v1.10.0） |
| `/warden fail [N]` | 列最近 N 条保存失败（默认 10，上限 50），带 attempts 与 subject；表头带累计次数与「反复失败」top（v1.9.0+ / v1.10.0 补统计） |
| `/warden 存档 [msg_id]` | **引用（回复）**一条合并转发后发它，把节点 + 节点内图片字节存成 `_records/<id>/`；也可直接转发给机器人，或给 OneBot `message_id`（v1.12.0） |
| `/warden 重发 [原地\|重建] [record_id\|list]` | 重发存档：默认先**原地**（`forward_group_single_msg`，让 QQ 把原消息再转发一遍，气泡/挂件/头像全保），不行再按存档**重建**合并转发（装饰会丢）；`list` 列最近 10 条（v1.12.0） |

## 存档页面（dashboard）

存档不只能用命令管，WebUI 里也能点开看：

1. dashboard → 插件 → `astrbot-plugin-media-warden` → 页面 `dashboard`（标题「聊天记录存档」）
2. 左侧列表：时间 / 群号 / 节点数 / 图片数 / 首节点预览；顶部输入框按「发送者 / 文本 / 群号 / 记录 id」搜索，下拉框按群过滤
3. 右侧详情：逐节点列出每段内容（文本 / 图片缩略图 / 表情 / @），缩略图点开看原图；取不到图的段会标出 `missing` 原因
4. 详情底部：**发到**（目标群下拉，带群名，默认原群）+ **转发原消息**（转原消息本体，气泡/挂件/头像/原时间戳都在，要求原消息还在群里，过期会自动改用重建）+ **重建转发**（按存档重拼，内容一致但没有装扮，随时可用）+ 重渲染 / 打开目录 / 删除（删整个 `_records/<id>/`，不可恢复）

**v1.14.0 分类管理**：顶部类型标签（全部 / 图片 / 动图 / 视频 / 文档 / 纯文本 / 聊天记录）同时覆盖两类保存物 —— `_records/` 里的条目，以及老的扁平落盘文件（索引库里一行一条，按 `kind` 分类）。右侧「结构化 / 渲染图」切换看同一个条目的两种形态；「发到」的目标群下拉带群名（默认这条记录原来的群），两个转发按钮都用它 —— **转发原消息**保装扮但依赖原消息还在，**重建转发**不依赖、但没有装扮；纯文本条目没有合并转发可发，只提供浏览/删除/打开目录。

「重渲染」按**当前**样式重画这条的 `render.png` —— v1.14.0 之前存的条目没有渲染图（标签都不出现），改了样式之后旧图也还是旧样式，两者都靠它。同一份内容被转发两次时（同一个群内），**不会**再建第二个目录：直接复用原来那条（`reused`，图片也不再下一次），页面只看到一条。跨群不合并 —— 同一内容出现在别的群各存一份是对的。老版本已经存成两个目录的，第二条仍带 `duplicate_of` 显示「内容重复」徽章（不会自动删）。

聊天记录的渲染图以前会**再落一份扁平 PNG**，于是「图片」分类里多出一条、磁盘上两份字节。现在它只存在于条目目录的 `render.png`（只有存档失败时才回退扁平落盘）；插件启动时会把旧数据整理一遍：条目已有渲染图的删掉扁平副本，条目还没有的把那张旧图收编成 `render.png` —— 老条目因此**无需手动重渲染**就有图了。v1.12 之前那些没有条目的老渲染图仍然作为独立「图片」存在（它们本来就没有结构化存档）。

**老扁平聊天记录自动迁移**（v1.14.0 起，启动时跑一次）：v1.12.0 之前的聊天记录存成 `…_forward_json_*.json`，页面上只是文件卡片。现在 `record_admin.migrate_flat_forwards()` 把它们迁成 `_records/` 条目 —— 节点能看、能重渲染、能重建转发；图片 url 早已过期，所以不下载、进 `missing`（页面显示「缺 N 图」），原始 JSON 复制成条目里的 `legacy_forward.json` 留底，扁平文件与索引行随后删掉。列表预览也从文件名变成正文。幂等，坏 JSON 不迁不删。

类型标签会标出**当前关掉的类别**（「纯文本 · 关」并淡化），空列表也直接说原因（这一类关了 / 还没存过 / 群和人还没配）—— v1.14.0 起六类都是开关，没有「默认自动存」的类型，页面上不再写含糊的「发图会自动保存」。

几个容易踩的点都在页面里处理掉了：老 GIF 在索引里记的是 `image`，筛选按扩展名再分一次，「动图」标签能筛到 2026-10-28 之前存的那批；「转发原消息」失败且原因是原消息不存在时，会往条目元数据写 `native_unavailable`，之后那条按钮就是灰的、头部显示「原消息已失效」（网络抖动不写，避免一次超时永久禁掉）；删除用页内蒙层确认（桌面版的 `window.confirm` 不保证弹得出来）；列表标题写「共 N 条 · 已加载 M」，和底部的「加载更多」不再打架。

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

无外部 AstrBot 依赖，不发起真实网络。当前共 **150 项**（Phase 1: 22 / Phase 2: 16 / Phase 3: 21 / Phase 4: 25 / Phase 5: 27 / Phase 6: 4 / Phase 7: 12 / Phase 8: 3 / Phase 9: 20），覆盖：

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
- `playwright` / `wkhtml` 渲染引擎未实现：选了会在启动日志明确告警并继续用 PIL 渲染（v1.10.0 起不再静默降级 JSON）
- 私聊 / 系统消息默认跳过
- 透明 GIF / WebP 动画按首帧处理，未做帧动画合成
- 命令权限、头像网络抓取、`image_result` 协议差异需要真实 AstrBot runtime 验证
- 转发节点图（`_save_forward`）走的是节点内嵌 url + 节点 image 段的 `file`(MD5)：**v1.9.6 起会先查 QQ 本地缓存**，但节点段没有 `file` 字段（非 napcat 实现 / 外链图）时仍只有 `[is_origin=1, 原 url]` 两个候选，任一失败即按 v1.5.0 设计整体失败、不落盘
- **转发节点图里的老图**（v1.9.8/v1.9.9 定论）：NapCat 自己不下载转发节点里的图片字节（`get_forward_msg` 只给元数据），`get_image` 也救不了（它先查 `FileNapCatOneBotUUID` 这个**进程内内存 map**，节点图的 MD5 从未进过那里，直接 `file not found`）。**但接收端 QQ 客户端会在随后几秒~几十秒把图落到本地缓存** —— v1.9.9 的「迟到缓存重查」就是等这个（`late local cache hit after Ns`）。仍失败的只剩「客户端自始至终没下过那张图」这一种。**普通图片**不受此限：QQ 客户端会下，v1.9.5 能命中 `Image/C2C/Image2`
- **QQ 本地缓存补救依赖 QQ 客户端自己的缓存目录**（`Tencent Files/<QQ号>/…`，Windows 默认在“文档”下）：缓存被清理、机器人跑在与 QQ 不同的机器上、或消息段的 `data.file` 不是 32 位 MD5 时，这条路用不上，会照旧返回失败
- **合并转发里的装扮（气泡 / 挂件 / 头像）**：2026-10-27 实测两种重发方式结论不同 —— ①**重建节点**（存档 → `Nodes` → `send_group_forward_msg`）只有昵称 + 内容，装饰全丢；②**原地转发**（`forward_group_single_msg`）让 QQ 自己把原消息再转发一次，气泡、挂件、头像、原时间戳全部保留。因此 `/warden 重发` 默认走原地，**但原地依赖原消息仍在服务端**：消息被撤回 / 过期 / 清理后只能回落存档重建（装饰丢失）。
- **跨群「原地」转发基本必然失败**：2026-10-28 再实测同一条存档 —— 发回**原群** `native` 成功（气泡/挂件都在），发到**另一个群**回「消息不存在」。原因是 `forward_group_single_msg` → `MsgApi.forwardMsg(peer, …)` 是**按目标 peer** 去找那条消息的，别处根本没有它。所以跨群就别指望保装扮：直接「重建转发」；自动转发选 `native` 时每个群都会先白试一次再回退。
- **同一条「原地」还受「原消息还在」约束**：消息被撤回 / 过期 / 清理后，回原群也会失败，只能回落重建（装饰丢失）。
- **两套 message_id 不能混用**：`forward_group_single_msg` 只认 OneBot 层的短 `message_id`（如 `1481691615`），段里的长 `msgId`（`7701368446137848000`）会回「无法找到消息」；而 `get_forward_msg` 认的正是段里的长 id。插件里 `root_msg_id` 一律存短的，`get_msg` 只用于核验目标是不是合并转发。
- 存档**不解析视频 / 语音字节**（还原时是占位文本）；节点里只有 `file=<md5>` 没有可下载 url 的图会进 `missing`（NapCat 本来就不下节点图）
- 高并发群发场景尚未压测

## 后续可选

- 其他平台 adapter 的消息组件扩展
- HTML 模板 + playwright 渲染，提升转发图样式自由度
- 媒体分类子目录 + `metadata.json` 索引
- 头像 URL 模板配置，按平台自定义头像源
