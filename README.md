# astrbot-plugin-media-warden

[![smoke](https://github.com/kazamisama/astrbot-plugin-media-warden/actions/workflows/smoke.yml/badge.svg)](https://github.com/kazamisama/astrbot-plugin-media-warden/actions/workflows/smoke.yml)

> 群聊素材守门人 —— 监听特定群/用户的非文字消息，按固定命名落盘，转发链渲染/JSON 保存并回复处理结果，支持预览图回传。

## 进度

| Phase | 内容 | 状态 |
|---|---|---|
| 1 | 骨架 + 配置 + 钩子 + 策略 + 组件识别 + 离线 smoke | ✅ v1.0 |
| 2 | 下载 (aiohttp) + 落盘 + 命名模板 + blake2b 去重 + reporter 接入真实结果 | ✅ v1.1 |
| 3a | 转发链渲染 (PIL 自绘气泡) + JSON sidecar + 节点超限降级 | ✅ v1.2 |
| 3b | SQLite 索引 + `/warden list / stats / export` 命令 | ✅ v1.2 |
| 3c | 预览图回传 (`image_result` + 文本降级) | ✅ v1.2 |
| 3d | 并发下载 (`download_many`) + 指数退避重试 + path-safety 防御 | ✅ v1.3 |
| 3e | 反向查询 `/warden lookup` + 时效清理 `/warden prune` | ✅ v1.3 |

## 目录

```
astrbot-plugin-media-warden/
  metadata.yaml              # AstrBot 插件元数据
  _conf_schema.json          # 配置 schema（WebUI 编辑）
  requirements.txt           # Pillow>=10.0
  main.py                    # 入口（@register 类 + 钩子 + 3 命令）
  warden/
    __init__.py              # 公开符号
    config.py                # MediaWardenConfig dataclass
    policy.py                # 群 / 用户 / 黑白名单 策略
    components.py            # 消息组件归一化（OneBot v11 segment）
    reporter.py              # 回执文本组装
    downloader.py            # aiohttp 流式下载 + fetcher 抽象
    storage.py               # 命名模板 + 落盘 + blake2b 去重
    forwarder.py             # PIL 自绘气泡渲染
    index.py                 # SQLite 资产索引 (find_by_msg / count / prune)
  README.md                  # 本文件
  _smoke.py                  # 离线冒烟测试（mock astrbot.api）
  CONTRIBUTING.md            # 贡献指南
  .github/ISSUE_TEMPLATE/    # bug / feature / question 模板
```

## 依赖

```bash
pip install Pillow  # forwarder 自绘气泡
# aiohttp 由 AstrBot 运行环境提供,缺包时 downloader 给出明确错误
```

## 部署

把整个目录复制到 AstrBot 的 `data/plugins/` 下，**目录名必须保留** `astrbot-plugin-media-warden`。重启 AstrBot 即可在 WebUI 看到插件。

## 配置

在 AstrBot WebUI → 插件管理 → astrbot-plugin-media-warden → 配置：

| 字段 | 默认 | 说明 |
|---|---|---|
| `target_groups` | `[]` | 群白名单/黑名单，格式 `platform:group_id` 或裸 ID |
| `target_users` | `[]` | 用户白名单/黑名单（sender.user_id） |
| `match_mode` | `whitelist` | `whitelist` \| `blacklist` |
| `storage_root` | `data/warden` | 落盘根目录，相对 AstrBot 工作目录 |
| `filename_pattern` | 见下 | 命名模板 |
| `forward_render_mode` | `image` | `image` \| `json` \| `both` |
| `forward_image_engine` | `pil` | `pil` \| `playwright` \| `wkhtml`（v1.2 仅 `pil` 落地） |
| `forward_image_width` | `720` | 转发渲染图宽度(像素) |
| `reply_to_original` | `true` | 是否引用原消息回复（由 hook 决定，模块不感知） |
| `reply_preview` | `true` | 是否附带第一张图作预览缩略图 |
| `max_file_size_mb` | `100` | 单文件大小上限 |
| `dedupe` | `true` | 基于 blake2b 头 1MB 做去重 |
| `enable_index_db` | `true` | 是否启用 SQLite 索引（`storage_root/_warden.db`） |
| `log_to_stdout` | `true` | 是否把每条匹配消息打到日志 |
| `download_retries` | `2` | 失败重试次数（transient: 5xx / 超时 / 连接错） |
| `max_concurrent` | `4` | 单消息多组件 / 转发节点图的并发上限 |

**默认命名模板**：

```
{platform}/{group_id}/{date}/{sender_id}_{msg_id}_{idx}_{safe_name}.{ext}
```

可用变量：`{platform}` `{group_id}` `{sender_id}` `{sender_name}` `{date}` `{time}` `{msg_id}` `{idx}` `{safe_name}` `{ext}` `{kind}`

## 实际行为

匹配到目标群 + 用户 + 含非文字组件的群消息后：

1. **策略过滤**（policy）—— 群 / 用户白名单或黑名单
2. **组件识别**（components）—— OneBot v11 segment → 归一化 Component
3. **下载**（downloader）—— aiohttp 流式抓 bytes,带超时 + 大小限制;多组件消息走 `download_many` 并发 (`max_concurrent` 限流);transient 错误(超时/连接错/5xx)按 `download_retries` 指数退避重试,4xx 立即抛
4. **命名 + 落盘**（storage）—— 按模板渲染相对路径,blake2b 去重,blake 软链复用,落盘前 `_assert_within_root` 防止越权写入
5. **转发节点处理**（forwarder）—— 按 `forward_render_mode`:
   - `image`: PIL 渲染节点链为 PNG（30 节点上限,超限降级 JSON）
   - `json`: 节点链结构化落到 `.json` sidecar
   - `both`: 两者都生成
6. **索引写入**（index）—— SQLite 记录每条资产
7. **回执**（reporter）—— 四段式:
   ```
   [Warden] ✅ 已保存
     用时: 0.42s
     类型: image×1, forward_render×1
     路径: data/warden/aiocqhttp/g1/20240617/
     成功 (2):
       - image: .../u1_m42_0_abc.png 19B
       - forward_render: .../u1_m42_1_forward_m42_1.png 3249B
   ```
8. **预览回传**（3c）—— 第一张 `forward_render > image` 走 `event.image_result(path)`;无该方法时降级为 `预览: <path>`;`reply_preview=False` 时跳过

## 命令

| 命令 | 说明 |
|---|---|
| `/warden list [N]` | 列最近 N 条索引记录（默认 10,上限 50） |
| `/warden stats` | 资产统计：总数 / 按类型分布 / 总字节数 / db 路径 |
| `/warden export [path]` | 导出索引为 JSON,默认 `<storage_root>/_export.json` |
| `/warden lookup <msg_id> [#idx]` | 反向查询:该消息保存了哪些资产,可选按 idx 过滤 |
| `/warden prune <days>` | 时效清理:从索引里删 N 天前的记录(不删实际文件) |

## 离线自测

```bash
cd astrbot-plugin-media-warden
python -X utf8 _smoke.py
```

无外部 AstrBot 依赖,也不发起真实网络。覆盖 **58 项**（17 Phase 1 + 12 Phase 2 + 17 Phase 3 + 12 Phase 4）：

- 配置加载 / 校验 / 降级
- 策略匹配（白名单 / 黑名单 / 私聊跳过 / 空列表 = 不限）
- 组件识别（OneBot v11 segments：image / video / record / file / json / node / text）
- 落盘 roundtrip + blake2b 去重 + 防路径穿越
- aiohttp mock 成功 + stream 超限中断
- 端到端 image 落盘 + 端到端 forward JSON / PNG / both / 超限降级
- 索引：写入 / recent / sha 查复用 / stats / export
- 预览：有 image_result / 无 image_result 降级 / 关闭 / 转发渲染优先
- 三个 `/warden` 命令
- 插件入口构造与配置注入
- Phase 4 (v1.3) 补充:
  - `download` 指数退避: transient 重试 / 4xx 不重试 / 达到上限抛错
  - `download_many` 并发保序 / 超时边界
  - `_assert_within_root` 越权拒绝 / 合法路径放行
  - `index.find_by_msg` 多 msg 隔离 + 排序
  - `index.count` / `prune_older_than` 时效清理
  - `/warden lookup` 命中 / `/warden prune` 阈值/参数错/索引关闭
  - `forwarder.render_with_images` 接收预下载 bytes

## 已知边界

- v1.2 仅 OneBot v11 平台 schema 已验证;其他平台（TG / 微信等）抽到 v1.3 扩 adapter
- `playwright` / `wkhtml` 引擎预留但未实现,切换会触发 `forwarder init failed` 并降级 JSON
- 私聊 / 系统消息默认跳过（v1 服务群聊场景）
- 转发的私聊截图带"匿名"头像占位（v1 不抓头像）
- 大量并发下载 / 群发场景未压测
- 真实 AstrBot runtime 下 `event.image_result` 的具体协议差异需在各 adapter 单独验证

## 后续可选

- **v1.4 adapter 扩展** —— TG / 微信 / 其他平台的消息组件 schema
- **playwright 渲染引擎** —— HTML 模板 + headless Chromium,样式自由度更高
- **头像 + @提醒** —— 节点内抓头像,@人高亮
- **媒体分类** —— image / video / file 各自子目录 + 配 metadata.json 索引

