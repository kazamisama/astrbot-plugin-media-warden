# 交接文档 · astrbot-plugin-media-warden v1.9.9

## 1. 当前状态

- 项目：`astrbot-plugin-media-warden`（AstrBot 群聊素材守门人插件）
- 版本：v1.9.9（`warden/config.py`、`metadata.yaml`、入口 `@register` 已同步）
- 分支：`main`
- Git：v1.9.0（`04ed9e0`）/ v1.9.1（`9f735cc`）/ v1.9.2（`440fc22`）/ v1.9.3（`6ea8bf9`）/ v1.9.4（诊断版，未提交）/ v1.9.5（`683b720`）/ v1.9.6（`eb95122`）/ v1.9.7（`832b6b4`，诊断版）/ v1.9.8（`54315ae`）/ v1.9.9 各单独一次提交（显式路径，未用 `git add -A`）
- 测试基线：`python -X utf8 _smoke.py` → `ALL OK (phase1=22 phase2=16 phase3=21 phase4=24 phase5=19, total=102)`

## 1.-5 转发节点图其实是「晚一步」（**v1.9.9 修正了 §1.-4 的结论**）

**§1.-4 那句「NapCat 从不下载转发节点里的图片字节」只对了一半**：字节确实不在 NapCat 手里，
但**接收端 QQ 客户端会在随后几秒~几十秒把它下载下来**，按 MD5 落到本地缓存。插件失败的时刻
正好卡在那个空档之前。同一台机器上的实测（2026-10-11）：

| 时刻 | 事实 |
| --- | --- |
| 20:26:57 | 转发节点图 `D867CE1671D3457B1A0534D240D4885E.jpg` 失败（`http 400`，节点 url 已失效） |
| 20:33:39 | 同一个 MD5 出现在 `…\3683351360\nt_qq\nt_data\Emoji\emoji-recv\2026-10\Ori\` |

所以 **v1.9.9 加了「迟到缓存重查」**：三级补救全败后隔 `4s`、`12s` 再查一次 QQ 本地缓存
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

### 诊断输出（临时，确认修复后可以删）

- 转发节点图每个候选 url 失败时打全 url：
  `fwd dl candidate 1/2 failed: http 400 for '…' | url=https://multimedia.nt.qq.com.cn/download?appid=…`

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
- 每一跳都进 `_refetch_trace`；全败时日志打 `refetch: …` 最后 4 条，回执里带
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
- `_fresh_media_url()`（v1.9.0 版，**v1.9.1 已被 `_refetch_media()` 取代，见 §1.1**）：`bot.call_action("get_image", file=<fileid>)`，fileid 取 `meta["fileid"]` → `comp.file_id` → `meta["path"]` → `_url_fileid(comp.url)`；响应经 `_get_media_url_candidates()` 兼容 dict/`data`/`url`/`file`/`file_url`/`path` 去重，返回第一个 ≠ 旧 url 的候选。
- `_recover_original_urls()` 恢复原始 url 时同时把 `_url_fileid(url)` 存进 `comp.meta["fileid"]`（rkey 会过期，fileid 是重取键）；v1.9.1 起同时存 `comp.meta["raw_file"] = ref["file"]`。

### 2) 失败原因区分

- `warden/reporter.py`：`AssetResult` 新增 `failure_kind` / `note`；新增 `classify_error(err) -> "fetch"|"save"|"other"`（fetch 判据：`image download failed` / `download:` / `get_media:` / `http ` / `timeout` / `connection error`；save 判据：`save:` / `json save:` / `render:`）、`failure_summary()`、`BatchResult.fail_items` / `failure_breakdown()` / `failure_phrase()`。
- `format_batch()` 表头带原因：全 fetch → `部分保存失败 (k/N) — 下载失败（源链接失效）`，全 save → `— 落盘失败`，混合 → `— 下载失败 X + 落盘失败 Y`；多一行 `原因:`；明细项末尾追加 `[note]`。
- @ 触发者文案：`N 个媒体没能保存 —— 下载失败(源链接已失效)，X/Y 已保存`（全 save / 混合另有措辞）。

### 3) 失败入库 + /warden fail

- `warden/index.py` 新增 `failures` 表（`ts/platform/group_id/sender_id/msg_id/idx/kind/err/subject/url/attempts`）+ `ix_fail_ts` / `ix_fail_subject`；新增 `failure_subject()`（`fileid:<id>` 优先，其次 `url:<host><path>`，丢 query 吸收 rkey/sign）、`record_failure()`（`attempts` = 同 subject+kind 历史条数+1）、`recent_failures()`、`failure_stats()`、`prune_failures_older_than()`；`stats()` 增加 `failures` / `failures_by_kind`，`prune_older_than()` 同时清同期 failures。
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

## 4. 验证记录

已验证：

- `python -X utf8 _smoke.py`：`ALL OK (phase1=22 phase2=16 phase3=21 phase4=24 phase5=18, total=101)`
- `_conf_schema.json` JSON 解析通过
- **v1.9.5 的本机实测**：8 张线上失败图逐个在 `C:\Users\chiriu\Documents\Tencent Files\` 下按 MD5 命中，单次查找 <1ms；`deadbeef…` / 短名 / 空串 / `None` 均正确返回 `None`（不误报）
- **v1.9.5 的线上实测（20:02:19）**：`used QQ local cache for image: …\Image\C2C\Image2\74760010d20c862b46a660054f601ead.jpg` → `✅ 已保存 …20261011_200219_image_9dbbb5a0.jpg 18749B [链接已重取: localcache:…]` —— 同一张图 19:59 还是 `http 400`
- 单测覆盖：`_url_fileid` 提取、fetch/save 分类、失败文案、三级回退三条路径、失败入库 + attempts 累加、索引复用命中/文件缺失、`/warden fail` + `stats`、`failure_stats`/`prune`、补救候选排序、`get_image` 多参数形态 + base64 返回、`get_msg` 重解析 + 多图挑段、**本地缓存命中零网络调用 / 未命中照旧回退 / `get_media_fallback=false` 时被忽略**、**转发节点图走整条 `_save_forward` 时同样零网络调用且 PNG 正常**

未验证：

- **`get_msg` 在真机上能否用 AstrBot 给的 `message_id` 解析成功**（本机只有 stub；`message_id` 的来源已按 `aiocqhttp_platform_adapter.py:228` 核对）。这是 v1.9.2 上线后要看的第一条日志。
- 真机上 `get_image` 对 segment 原始 `file` 的响应（**v1.9.2 已判定这条路对图片无效**，保留它只是为了兜住别的 OneBot 实现与「uuid 恰好还在」的情形）
- `/warden fail` 在非管理员下的拒绝行为（测试用 stub 模拟权限装饰器）

## 5. 未修改范围

- 转发链布局、字体、emoji 渲染逻辑
- blake2b 去重算法与 `assets` 表 schema
- `_save_forward` 的降级策略：仍按 v1.5.0 设计「任一节点图最终失败即整体失败、不落盘」（**v1.9.6 起节点图会先查本地缓存**，但没有 `file` 字段时仍只有 `[is_origin=1, 原 url]` 两个候选）

## 6. 回滚与备份

safe-edit 每次修改都会自动备份到 `C:\Users\chiriu\.irmia\backups\`，可按文件名和时间戳恢复：

```powershell
safe_edit list-backups
safe_edit rollback <file> [<backup_name>]
```

## 7. 下一步建议

1. 在真实 AstrBot + napcat 环境观察一次失效链接：**v1.9.5 起最先应该看到的是 `used QQ local cache for image: …`** —— 命中本地缓存时连网络都不走，资产正常落盘、回执不再计失败。合并转发里的图则看 **`used QQ local cache for forward node image: …`** 或 **`late local cache hit after Ns`**（v1.9.9 的迟到重查，见 §1.-5）。若本地确实没有（比如图片已被 QQ 清理、或换了台机器），才继续往下看到 `get_msg refreshed url for image: …` 或三级补救的败因。
2. 若仍失败，先确认失败那几张图的 `meta["raw_file"]`（消息段原始的 `data.file`）是不是一个 32 位 MD5 —— 不是的话本地缓存这条路用不上（`find_cached_image` 对短于 16 字符的候选直接放弃）；转发节点图则看节点段有没有 `file` 字段。
3. 转发节点图仍失败时，**先分辨是哪一种**（§1.-5）：客户端后来下过那张图（迟到重查没赶上 → 可以调大 `_LATE_CACHE_DELAYS_S`）／客户端从没下过（无解）。
   不要再把「NapCat 架构边界、无解」当成转发节点图失败的统一结论 —— v1.9.8 那个说法已被 v1.9.9 修正。
   若要彻底解决，只能让 NapCat 侧预先落盘（改 NapCat 或换实现），不是本插件能做的。
3. 若仍失败，日志里 `refetch: …` 会逐跳写清败因（`get_image file=… -> ActionFailed…` / `get_msg {…} -> …`），回执 note 里带最后一跳。
   常见的两种：`get_msg` 报「消息不存在或已被撤回」= 消息太老 / napcat 的 `MessageUnique` 映射已清；能拿到新链接却 `http 400` = 新时的 rkey 也救不回来（要查 napcat 的 `getRkeyData()` 是否拿到 packet rkey）。
4. 打开 `index_reuse_saved=true` 观察几天，确认「索引复用」不会掩盖「文件被人工清理」的情况（当前会明确计为落盘失败）。
5. 若要覆盖转发节点图，需要在展开节点时保留消息 id / `file_id`，再复用 `_refetch_media()`；这是独立的一步。
6. 本机 `_smoke.py` 依赖 aiohttp 导入期桩（`_install_aiohttp_stub_if_missing`）；装了真 aiohttp 的环境会走真实路径，不受影响。
