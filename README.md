<div align="center">

# 💬 WeChat Bridge

**基于腾讯 iLink Bot API 的微信消息桥接服务**

轻量 · 开箱即用 · 跨平台原生运行

[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://ghcr.io)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## ✨ 功能亮点

- 🔗 **双向消息桥接** — 微信收发消息全打通，支持文本、图片、视频解析及指令路由
- 🌐 **标准 HTTP API** — RESTful 接口，curl 一行即可发微信消息
- 🔔 **反向 Webhook 推送** — 收到微信消息时主动 POST 到你的服务（Dify / FastGPT / Node-RED）
- 🤖 **内置 AI 助手** — 原生集成 OpenAI / Gemini / Claude / DeepSeek，开箱即用
- 📱 **Web 管理面板** — 扫码登录、实时消息流、图片收发、AI 配置、保活设置，一站式管理
- ⏰ **24h 保活守护** — 智能检测微信通道 23h/23h58m 超时，主动提醒防断联
- 🔒 **API Token 鉴权** — 可选的 Bearer Token 认证，保护你的接口安全
- 🐳 **多种部署方式** — 支持 Docker / Windows / macOS / Linux 原生运行

---

<div align="center">
  <img src="docs/assets/screenshot-chat.png" alt="WeChat Bridge 聊天界面" width="700">
  <p><em>Web 管理面板 — 实时消息收发、图片支持、联系人管理</em></p>
</div>

---

## 🚀 快速开始

### 一键安装（推荐）

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/yuuouu/WeChat-Bridge/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
powershell -c "irm https://raw.githubusercontent.com/yuuouu/WeChat-Bridge/main/install.ps1 | iex"
```

脚本会自动检测环境、安装依赖、下载代码、启动后台服务并打开浏览器。

### 手动安装

只需 Python 3.10+：

```bash
git clone https://github.com/yuuouu/WeChat-Bridge.git
cd WeChat-Bridge
pip install -r app/requirements.txt
cd app && python main.py
```

### Docker Compose

```bash
mkdir -p wechat-bridge && cd wechat-bridge

cat > docker-compose.yml <<EOF
services:
  wechat-bridge:
    image: ghcr.io/yuuouu/wechat-bridge:latest
    container_name: wechat-bridge
    restart: unless-stopped
    ports:
      - "5200:5200"
    volumes:
      - ./data:/data
    environment:
      - WEBHOOK_URL=           # 可选：消息推送地址
      - API_TOKEN=             # 可选：API 鉴权 Token
      - TZ=Asia/Shanghai
EOF

docker compose up -d
```

安装完成后，浏览器打开 `http://localhost:5200`，扫码登录即可。

### 服务管理

```bash
# Windows
start.bat          # 后台启动服务并打开浏览器
stop.bat           # 停止后台服务

# macOS / Linux
./start.sh         # 后台启动服务
./stop.sh          # 停止后台服务
```

运行日志保存在 `data/run.log`，可随时查看。

---

## 📡 API 接口

> 如果设置了 `API_TOKEN`，所有 API 请求需携带 `Authorization: Bearer <TOKEN>` 请求头，或在 URL 中添加 `?token=<TOKEN>` 参数。

### 发送消息

```bash
# 最简单：GET 请求，to 省略时自动发给第一个联系人
curl "http://localhost:5200/api/send?text=Hello!"

# POST JSON（指定联系人）
curl -X POST http://localhost:5200/api/send \
  -H "Content-Type: application/json" \
  -d '{"to": "好友名称", "text": "Hello!"}'
```

#### 进阶功能

```bash
# 多播发送：逗号分隔多个联系人（每人间隔 0.5s 防风控）
curl "http://localhost:5200/api/send?to=老婆,家庭群&text=晚饭做好了"

# Markdown 降级：自动将 Markdown 转为微信友好的纯文本
curl "http://localhost:5200/api/send?text=**重要通知**&markdown=1"
```

### 快捷推送（兼容青龙面板 / ntfy / Bark）

这个接口专为第三方系统集成设计。如果未显式指定 `to`，系统会自动将消息发送给通讯录中的**第一个联系人**。

#### 1. 基础调用

```bash
# GET 方式（最简单）
curl "http://localhost:5200/api/push?title=提醒&content=该喝水了&token=YOUR_TOKEN"

# POST JSON
curl -X POST http://localhost:5200/api/push \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"to": "好友名称", "title": "提醒", "content": "消息内容"}'
```

#### 2. 在青龙面板中使用

前往青龙面板的 `系统设置` -> `通知设置`，按以下填写：

- **通知方式**：`自定义通知`
- **webhookMethod**：`GET`
- **webhookContentType**：`text/plain`
- **webhookUrl**：`http://你的IP:5200/api/push?title=$title&content=$content` *(如果设置了密码，末尾加 `&token=凭证`)*
- 其他选项保持默认留空。保存后点击测试，即可在微信中收到青龙的测试通知！

### 发送图片

支持三种方式上传图片，Web 面板也可直接点击 🖼️ 按钮发送：

```bash
# 方式一：multipart/form-data（最通用，适合脚本和前端）
curl -X POST http://localhost:5200/api/send_image \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "to=好友名称" \
  -F "image=@/path/to/photo.jpg"

# 方式二：JSON + Base64（适合程序化调用）
curl -X POST http://localhost:5200/api/send_image \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"to": "好友名称", "image": "<base64编码的图片数据>"}'

# 方式三：裸二进制流（适合管道和流式处理）
curl -X POST "http://localhost:5200/api/send_image?to=好友名称&token=YOUR_TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @/path/to/photo.jpg
```

### Webhook 适配器

自动识别并格式化第三方服务的告警负载为微信友好文本：

```bash
# 指定类型：Grafana / GitHub / Uptime Kuma / Bark
curl -X POST http://localhost:5200/api/webhook/grafana \
  -H "Content-Type: application/json" \
  -d '{"status": "firing", "alerts": [{"labels": {"alertname": "HighCPU"}}]}'

# 自动检测：系统会根据字段特征自动识别来源
curl -X POST http://localhost:5200/api/webhook \
  -H "Content-Type: application/json" \
  -d '{"title": "下载完成", "message": "文件已就绪"}'
```

支持的 Webhook 格式：`grafana` · `github` · `uptimekuma` · `bark` · 通用自动检测

### 获取联系人列表

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5200/api/contacts
```

### 健康检查（无需鉴权）

```bash
curl http://localhost:5200/api/status
```

---

## ⚙️ 配置说明

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PORT` | `5200` | 服务监听端口 |
| `WEBHOOK_URL` | _(空)_ | 收到消息时主动 POST 的目标地址 |
| `API_TOKEN` | _(空)_ | API 鉴权 Token，未设置则无鉴权 |
| `TOKEN_FILE` | `/data/token.json` | 登录凭证持久化路径 |
| `CONTACTS_FILE` | `/data/contacts.json` | 联系人缓存路径 |
| `AI_CONFIG_FILE` | `/data/ai_config.json` | AI 助手配置文件路径 |
| `TZ` | `Asia/Shanghai` | 容器时区 |

### Webhook 推送格式

当配置了 `WEBHOOK_URL` 后，收到微信消息时会向该地址发送 POST 请求：

```json
{
  "source": "wechat-bridge",
  "from_user": "用户ID",
  "from_name": "显示名",
  "text": "消息内容 (图片为 [图片:文件名], 视频为 [视频:文件名])",
  "msg_id": "消息ID",
  "timestamp": 1712345678,
  "msg_type": 1
}
```

---

## 🏗️ 架构概览

```
                                    ┌─────────────────┐
微信用户 ─── iLink API ───────────► │  WeChat Bridge   │
                                    │                  │
                                    │  ┌── Web UI ───┐ │
                                    │  │  扫码/配置   │ │
                                    │  └─────────────┘ │
                                    │                  │ ──► Webhook POST (Dify/FastGPT)
                                    │  ┌── AI引擎 ──┐ │
脚本/服务 ── POST /api/send ──────► │  │ GPT/Gemini │ │
                                    │  └────────────┘ │
                                    └─────────────────┘
```

---

## 🤖 内置 AI 助手

通过 Web 管理面板一键配置，无需编码：

| 厂商 | 支持模型 |
|------|---------|
| **OpenAI** | GPT-4o, GPT-4o Mini, GPT-4.1 Mini/Nano |
| **Google** | Gemini 2.0 Flash, 2.5 Flash/Pro |
| **Anthropic** | Claude Sonnet 4, Claude 3.5 Haiku |
| **DeepSeek** | DeepSeek Chat (V3), Reasoner (R1) |

微信中发送 `/help` 查看可用指令，`/clear` 清除对话历史。

---

## ⚠️ 注意事项

- iLink API 依赖腾讯官方平台，接口可能随时变动
- 当前已支持**文本、图片、视频**消息解析及保存，语音/文件等类型解析待后续扩展
- 建议在内网环境使用；若暴露到公网，**务必设置 `API_TOKEN`**
- 微信通道存在 24 小时超时限制，建议开启保活提醒功能
- iLink 协议限制：对方需先给你发一条消息，系统才能获取其 `user_id` 用于主动发送

---

## 📄 License

MIT License - 自由使用、修改和分发。
