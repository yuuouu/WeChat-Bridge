# 🛠️ 部署与管理指南

> 返回 [README](../README.md)

---

## 🤖 让 AI 帮你部署
直接把下面这句话发给你正在使用的 AI 助手（如 Cursor, Cline 等）：
> `@workspace 请根据我的操作系统，直接执行 https://github.com/yuuouu/WeChat-Bridge 的一键安装脚本帮我部署 WeChat Bridge`

---

## 手动安装

只需 Python 3.10+：

```bash
git clone https://github.com/yuuouu/WeChat-Bridge.git
cd WeChat-Bridge
pip install -r app/requirements.txt
cd app && python main.py
```

---

## Docker Compose

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
      - WEBHOOK_URL=           # 可选：外部 Webhook 地址
      - WEBHOOK_ENABLED=false  # 可选：是否启用 Webhook 转发
      - WEBHOOK_MODE=unknown_command  # 可选：unknown_command / all_messages
      - WEBHOOK_TIMEOUT=5      # 可选：Webhook 请求超时（秒）
      - API_TOKEN=             # 可选：API 鉴权 Token
      - TZ=Asia/Shanghai
EOF

docker compose up -d
```

安装完成后，浏览器打开 `http://localhost:5200`，扫码登录即可。

> Webhook 也可以在 Web 管理面板中配置。环境变量适合容器化部署统一管理，Web UI 适合单机快速启用或临时调试。

---

## 服务管理

```bash
# Windows
scripts\start.bat          # 后台启动服务并打开浏览器
scripts\stop.bat           # 停止后台服务

# macOS / Linux
./scripts/start.sh         # 后台启动服务
./scripts/stop.sh          # 停止后台服务
```

运行日志保存在 `data/run.log`，可随时查看。

---

## 版本更新与升级

程序每次启动时会自动检测 GitHub 是否有新版本，并在运行日志 (`data/run.log`) 中输出提醒。

你也可以随时手动更新至最新版本：

**原生安装 (Git) 更新：**
```bash
cd WeChat-Bridge
git pull
# 停止旧服务后再次运行
./scripts/start.sh  # (Windows 为 scripts\start.bat)
```

**Docker 容器更新：**
```bash
cd wechat-bridge
docker compose pull
docker compose up -d
```

**Windows 一键脚本更新：**
如果安装时使用了 PowerShell 一键脚本，可直接在此机器上重新运行该安装命令。脚本会自动进行文件拉取与覆盖、更新依赖并重启服务，你的配置和 `data/` 目录将安全保留。
