# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 格式。

## [1.0.1-rc1] - 2026-04-18

### Added
- 新增投递状态持久化：`delivery_state`、`overflow_sessions`、`pending_messages`
- 新增 `/pull` 补拉机制，用户回复后可按微信长度上限拉取未送达消息
- 新增 Web UI 投递状态展示，包括 `已缓存`、`已补拉`、`已丢弃`、`可能已送达` 等标签
- 新增关键回归测试，覆盖缓存补拉、Web API 与工具函数

### Changed
- 将原来的 `web.py` 拆分为 `webapp/` 模块，分离服务端、鉴权、页面与请求解析职责
- Windows 本地启动脚本改为更稳定的后台运行方式，便于本机联调和重启
- README 同步补充消息缓存、`/pull` 和投递状态说明

### Fixed
- 修复连续受限后消息只能失败不能回放的问题，统一接入缓存会话机制
- 修复图片发送命中 `24h` 或 `10 条限制` 时仍弹前端失败提示的问题
- 修复接口 `ReadTimeout` 场景下手机已收到但 SQLite / Web UI 漏记消息的问题
- 修复 Web 状态面板在无受限状态时仍长期占位的问题

## [1.0.0] - 2026-04-12

### Added
- 🔗 双向消息桥接：基于 iLink Bot API 的微信收发消息
- 🌐 RESTful HTTP API：`/api/send`、`/api/push`、`/api/contacts`、`/api/status`
- 📱 Web 管理面板：扫码登录、实时消息流、系统设置
- 🤖 内置 AI 助手：支持 OpenAI / Google Gemini / Anthropic Claude / DeepSeek
- 🔔 反向 Webhook 推送：收到消息时主动 POST 到外部服务（Dify / FastGPT / n8n）
- 📸 媒体消息支持：图片和视频的 CDN 下载、AES-128-ECB 解密、格式检测
- ⏰ 24h 保活守护：可配置的断线提醒（分钟级精度）
- 🔒 API Token 鉴权：可选的 Bearer Token 认证
- 💾 SQLite 消息持久化：WAL 模式、自动清理、分页查询
- 🐳 Docker 一键部署：alpine 镜像、健康检查、日志控制
- 📜 一键安装脚本：`curl | bash` 快速部署
- 🖼️ Web UI 图片/视频内联展示与 Lightbox 预览
- ⌨️ 微信指令系统：`/help`、`/status`、`/ai`、`/clear`
- 📊 AI Token 用量日统计与每日限额
- 📡 Webhook 适配器：自动解析 Grafana / GitHub / Uptime Kuma / Bark 告警负载
- 📢 多播发送：`to` 参数支持逗号分隔多目标
- 📝 Markdown 降级：自动将 Markdown 转为微信友好纯文本（`?markdown=1`）
- 🚀 GET `/api/send`：浏览器地址栏一行即可发送消息
