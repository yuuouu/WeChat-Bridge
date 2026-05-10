# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 格式。

## [1.2.0] - 2026-05-10

### Added
- 新增 Webhook 插件系统（`webhook_manager.py`）：统一 HTTP 入口，支持多插件自动发现、命令路由与会话管理
- 新增 Bridge Code Agent（`bridge_code_agent.py`）：通过微信远程驱动 Mac 上的 AI CLI（Gemini / Claude Code / Codex），支持 `/code`、`/switch`、`/cli`、`/exit` 命令
- 新增静默模式（Mute）：可对指定联系人关闭 AI 自动回复
- 新增多账号支持：`bot_id` 隔离不同 Bot 实例的数据与配置
- 新增 OpenWRT / iStoreOS 原生支持与对应部署文档

### Changed
- README 全面重构：新增 banner、Code Agent 效果预览图、「数据主权」定位
- `bridge-code-agent.md` 新增完整对话截图与插件开发指南
- 图片资源统一去除 `screenshot-` 前缀，文件名更简洁
- 重构 ilink 核心逻辑，提升稳定性；补全相关单元测试

### Fixed
- 修复 ruff lint / format 检查错误，CI 全量通过

---

## [1.1.1] - 2026-05-06

### Added
- 新增自定义 AI 厂商支持（任意 OpenAI-compatible 服务）
- 新增可选匿名遥测，可在 Web UI 中开启或关闭
- 新增 Markdown normalize 模式（`MARKDOWN_MODE=normalize`），将普通通知自动整理为 Markdown
- 新增 ZIP 安装包与对应安装指南
- 新增 GitHub 直连探针与边缘节点缓存加速安装脚本

### Changed
- Web 管理面板升级：界面布局与配置交互优化
- 安装脚本新增 Cloudflare 代理回退逻辑，提升国内网络下的安装成功率
- 全模块补齐 `from __future__ import annotations`

### Fixed
- 修复并发场景下的线程安全问题，重构投递状态机

### Docs
- 重构 README，新增 Webhook 日记收集器示例
- 补充卸载脚本使用指南

---

## [1.1.0] - 2026-04-30

### Added
- 新增可配置的 Webhook 转发功能，支持"仅未知命令"和"全部消息"两种模式
- 新增运行时版本暴露接口 (`/api/status` 返回 `version` 字段)
- 新增 GitHub Actions CI：Ruff lint + format 检查 + 多 Python 版本测试 (3.10/3.11/3.12)
- 新增 Docker 镜像自动构建与推送 (GHCR, amd64 + arm64)
- 新增 `config.py` 和 `ilink.py` 单元测试，测试用例总数 27 → 60
- CI 测试新增 `pytest-cov` 覆盖率报告

### Changed
- 模块化拆分：`commands.py`、`delivery.py`、`keepalive.py` 从 `bridge.py` 分离为独立 Mixin
- `/pull` 默认分块上限从 `1500` 调整为 `5200`（基于 2026-04-20 现网实测数据）
- 全量代码格式化（Ruff check + format），统一代码风格
- `pyproject.toml` 新增 `per-file-ignores` 配置，精确豁免测试和入口文件的 E402

### Fixed
- 修复 CI lint 失败：解决 103 个 Ruff 检查错误（import 排序、空白行、未使用导入、冗余参数等）

### Docs
- 新增异步 Webhook 回写集成指南 (`docs/webhook-async-reply.md`)

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
- 将 `/pull` 默认分块上限从 `1500` 调整为 `5200`，并补充 2026-04-20 现网实测记录：`5468` 可直发，`5484` 起进入缓存

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
