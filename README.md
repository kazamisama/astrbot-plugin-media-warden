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

## 目录

```
astrbot-plugin-media-warden/
  metadata.yaml              # AstrBot 插件元数据
  _conf_schema.json          # 配置 schema（WebUI 编辑）
  requirements.txt           # Pillow>=10.0
  main.py                    # 入口（@register 类 + 钩子 + 5 个命令）
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
    avatar.py                # 头像抓取 + 文件缓存
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

AstrBot WebUI → 插件管理 → astrbot-plugin-media-warden → 配置。schema 按 5 组组织：监听范围 / 保存与索引 / 下载与并发 / 转发渲染 / 回复行为。

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
| `download_retries` | `2` | transient 错误重试次数（>= 0） |
| `max_concurrent` | `4` | 并发下载上限（>= 1） |
| `download_timeout_s` | `30` | 单次下载超时（> 0） |
| `forward_render_mode` | `image` | `image` \| `json` \| `both` |
| `forward_image_engine` | `pil` | `pil` \| `playwright` \| `wkhtml`（当前仅 `pil` 落地） |
| `forward_image_width` | `720` | 转发渲染图宽度（>= 1） |
| `forward_render_max_nodes` | `30` | 单条转发渲染最大节点数，超限降级 JSON（>= 1） |
| `enable_user_avatar` | `true` | 是否在转发渲染中绘制节点头像 |
| `avatar_size` | `36` | 头像直径（像素） |
| `avatar_timeout_s` | `10` | 单头像抓取超时 |
| `avatar_cache_size_mb` | `50` | 头像缓存容量上限（>= 0） |
| `avatar_platforms` | `["aiocqhttp", "qq_official"]` | 允许抓取头像的平台白名单 |
| `reply_to_original` | `true` | 是否引用原消息回复 |
| `reply_preview` | `true` | 是否附带第一张图作预览 |
| `at_on_save_failure` | `true` | 保存失败时 @ 触发消息的监听对象 |
| `log_to_stdout` | `true` | 是否把匹配消息打到日志 |

**默认命名模板**：

```
{group_id}/{sender_id}/{date}_{time}_{kind}_{short_id}{ext}
```

可用变量：`{platform}` `{group_id}` `{sender_id}` `{sender_name}` `{date}` `{time}` `{msg_id}` `{idx}` `{safe_name}` `{ext}` `{kind}` `{short_id}`

## 工作流程

匹配到目标群 + 用户且消息含媒体或转发组件后：

1. **策略过滤**（policy）—— 群 / 用户白名单或黑名单
2. **组件识别**（components）—— OneBot v11 segment 归一化为 `Component`，并从 `raw_message` 恢复被 PreProcessStage 覆盖的原始 http URL
3. **并发下载**（downloader）—— aiohttp 流式抓取，带超时和大小限制；transient 错误按 `download_retries` 指数退避重试，4xx 立即失败
4. **命名 + 落盘**（storage）—— 按模板渲染相对路径，blake2b 去重，落盘前做路径越权检查
5. **转发链处理**（forwarder）—— 拉取/展开嵌套节点，按 `avatar_platforms` 白名单抓头像，按 `forward_render_mode` 渲染 PNG 或保存 JSON；透明图片做 alpha 合成
6. **索引写入**（index）—— SQLite 记录每条资产
7. **回执**（reporter）—— 汇总成功 / 失败 / 去重复用 / 用时
8. **预览回传** —— 优先回传 `forward_render` 图，其次第一张 image；无 `image_result` 时降级为文本路径

## 存储目录

默认落盘结构（`filename_pattern` 可改）：

```
<storage_root>/
  <group_id>/<sender_id>/<date>_<time>_<kind>_<short_id><ext>
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

## 命令

所有 `/warden` 命令默认仅管理员可调用（`PermissionType.ADMIN`）。

| 命令 | 说明 |
|---|---|
| `/warden list [N]` | 列最近 N 条索引记录（默认 10，上限 50） |
| `/warden stats` | 资产统计：总数 / 类型分布 / 总字节数 / db 路径 |
| `/warden export [path]` | 导出索引为 JSON；path 必须落在 `storage_root` 内，默认 `_export.json` |
| `/warden lookup <msg_id> [#idx]` | 反向查询该消息保存了哪些资产，可选按 idx 过滤 |
| `/warden prune <days>` | 从索引删除 N 天前的记录（不删实际文件） |

## 离线自测

```bash
cd astrbot-plugin-media-warden
python -X utf8 _smoke.py
python -m pytest -q _smoke.py
```

无外部 AstrBot 依赖，不发起真实网络。当前共 **77 项**（Phase 1: 20 / Phase 2: 16 / Phase 3: 21 / Phase 4: 20），覆盖：

- 配置加载 / 校验 / 非法值降级
- 策略匹配：白名单 / 黑名单 / 私聊跳过 / 空列表不限
- 组件识别与 raw_message URL 恢复
- 落盘 roundtrip、blake2b 去重、路径越权防护、篡改指针拒绝
- 下载：aiohttp mock、超限中断、指数退避、并发保序
- 转发链：JSON / PNG / both / 超限降级 / 嵌套展开 / 透明图 alpha 合成
- 索引：写入 / recent / sha 查复用 / stats / export / lookup / prune
- 预览回传与命令权限注册

## 已知边界

- 主力验证平台为 OneBot/QQ 系（`aiocqhttp`、`qq_official`），其他平台的消息组件 schema 未逐一验证
- `playwright` / `wkhtml` 渲染引擎为预留槽位，切换会降级 JSON
- 私聊 / 系统消息默认跳过
- 透明 GIF / WebP 动画按首帧处理，未做帧动画合成
- 命令权限、头像网络抓取、`image_result` 协议差异需要真实 AstrBot runtime 验证
- 高并发群发场景尚未压测

## 后续可选

- 其他平台 adapter 的消息组件扩展
- HTML 模板 + playwright 渲染，提升转发图样式自由度
- 媒体分类子目录 + `metadata.json` 索引
- 头像 URL 模板配置，按平台自定义头像源
