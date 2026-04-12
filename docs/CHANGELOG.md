# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 格式。

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
