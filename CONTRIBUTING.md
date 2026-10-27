# Contributing

感谢想为 `astrbot-plugin-media-warden` 添砖加瓦.下面是最常见的几种参与方式.

## 报告 bug

提 issue 时务必附上:
- AstrBot 版本 / platform adapter (aiocqhttp? Telegram?)
- warden 版本 (从 `metadata.yaml` 看)
- 复现步骤
- `_smoke.py` 输出 (111 项必须全绿,否则先解决本地问题)
- 关键配置(`_conf_schema.json` 字段值,记得脱敏)

模板: `.github/ISSUE_TEMPLATE/bug.md`

## 提议功能

模板: `.github/ISSUE_TEMPLATE/feature.md`

## 提 PR

1. fork 仓库,新建分支
2. 修改代码
3. 在 `astrbot-plugin-media-warden/` 下跑 `python -X utf8 _smoke.py`,111 项必须全绿
4. 新增功能时务必**在 `_smoke.py` 加对应测试**(沿用 mock 注入模式,见 hippocampus 的 `_smoke_v13.py` 写法)
5. PR 标题用 `[type] 简述`,如 `[fix] /warden lookup 解析 #idx 失败`
6. CI 跑过才会被 review (branch protection 已设)

## 开发约定

- **核心代码**都在 `warden/` 子包下,按职责拆模块:`config` / `policy` / `components` / `downloader` / `storage` / `forwarder` / `index` / `reporter`
- **入口** 始终在 `main.py` 一个文件,别再拆
- **配置项** 都从 `_conf_schema.json` 走,新增字段必须同步 schema
- **版本号** 三个地方同步: `metadata.yaml` / `warden/config.py:VERSION` / `main.py` 的 `@register` 描述串; 发布时再打同名 git tag (历史 tag 停在 `v1.4.7`, 之后一直没打, 新版本一并补上)
- **依赖** 写在 `requirements.txt`,Phase 1 走纯 stdlib,Phase 2+ 逐步加 `aiohttp` / `Pillow` 等
- **错误处理** 用明确的异常类(`DownloadError` / `ValueError` / `RuntimeError`),不要 silent except。
  例外只有一类: 纯尽力而为的收尾(改头像失败、删缓存文件失败)可以吞,但 `except` 必须窄到具体异常类型并至少 `self._log()` 一次 —— 现状有 39 处 `except`,逐个判断过,没有一处会盖住主流程错误
- **不依赖真实网络/AstrBot runtime** —— 测试一律 mock 注入
- **Unicode 路径** 跨平台必须能跑(Windows 文件 IO 用 `pathlib` 或显式 `os.path.realpath`)

## 路线图参考

- v1.3 ✓: 并发下载 + 重试 + /warden lookup + path-safety + issue templates
- v1.10 ✓: 完备性/冗余性清理(死代码删除、重复实现合一、索引迁移、配置诚实化)
- v1.4: 跨平台 adapter (Telegram / 微信); playwright 渲染引擎
- v2.0: LLM 摘要 + 自动标签; 群内反查命令; 多账号协调

