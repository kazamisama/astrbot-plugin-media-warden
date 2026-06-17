name: Bug report
about: 报告 v1.3 warden 插件的 bug
title: "[bug] "
labels: bug
assignees: ""

---

** 现象 **
做了什么、看到什么、期望看到什么。

** 复现步骤 **
1. AstrBot 版本: v?
2. platform/adapter: aiocqhttp / telegram / ...?
3. 触发群: `?` (脱敏)
4. 触发用户: `?` (脱敏)
5. 消息内容: 图片 / 转发 / ...
6. 配置 (`_conf_schema.json` 关键字段):
   ```yaml
   target_groups: [...]
   match_mode: whitelist
   forward_render_mode: image
   ```

** 期望 **
简述期望行为。

** 实际 **
简述实际行为 + 错误片段 / stacktrace.

** 环境 **
- AstrBot: v?
- Python: 3.10 / 3.11 / 3.12
- OS: Windows / Linux / macOS
- warden 版本: v1.3.x

** 自测结果 **
```bash
python -X utf8 _smoke.py
# 输出: ?
```

** 日志 (脱敏) **
```
[media-warden] ...
```

** 附加信息 **
截图 / 配置 / 文件样例路径(注意脱敏).
