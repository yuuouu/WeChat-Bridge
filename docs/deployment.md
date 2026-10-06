# WeChat Bridge 部署指南：Docker、macOS、Linux 与 Windows

在 Docker、macOS、Linux 或 Windows 上部署和升级 WeChat Bridge 微信 Bot，并了解本地安装与运行方式。

> 返回 [WeChat Bridge 项目主页](../README.md)

---

## 🤖 让 AI 帮你部署
直接把下面这句话发给你正在使用的 AI 助手（如 Cursor, Cline 等）：
> `@workspace 请根据我的操作系统，直接执行 https://github.com/yuuouu/WeChat-Bridge 的一键安装脚本帮我部署 WeChat Bridge`

---

## 本地 ZIP 安装（无需 Git）

适合没有安装 Git、或只想下载源码包后本地解压运行的环境。只需准备 Python 3.10+。

下载地址：

- GitHub ZIP：`https://github.com/yuuouu/WeChat-Bridge/archive/refs/heads/main.zip`
- CDN 代理 ZIP：`https://wb.yuuou.qzz.io/archive/main.zip`

如果使用浏览器下载，下载完成后解压，将解压出的 `WeChat-Bridge-main` 文件夹重命名为 `wechat-bridge`，然后进入该目录执行启动脚本。

**macOS / Linux：**

```bash
curl -L -o wechat-bridge.zip https://github.com/yuuouu/WeChat-Bridge/archive/refs/heads/main.zip
unzip wechat-bridge.zip
mv WeChat-Bridge-main wechat-bridge
cd wechat-bridge

python3 -m pip install -r app/requirements.txt
bash scripts/start.sh
```

如果 GitHub 下载较慢或不可用，可以把第一行替换为：

```bash
curl -L -o wechat-bridge.zip https://wb.yuuou.qzz.io/archive/main.zip
```

**Windows PowerShell：**

```powershell
Invoke-WebRequest -Uri "https://github.com/yuuouu/WeChat-Bridge/archive/refs/heads/main.zip" -OutFile "wechat-bridge.zip"
Expand-Archive -Path "wechat-bridge.zip" -DestinationPath "." -Force
Rename-Item -Path ".\WeChat-Bridge-main" -NewName "wechat-bridge"
cd .\wechat-bridge

python -m pip install -r app\requirements.txt
scripts\start.bat
```

如果 GitHub 下载较慢或不可用，可以把第一行替换为：

```powershell
Invoke-WebRequest -Uri "https://wb.yuuou.qzz.io/archive/main.zip" -OutFile "wechat-bridge.zip"
```

启动完成后打开 `http://localhost:5200`，扫码登录即可。后续启动和停止可继续使用 `scripts/start.*`、`scripts/stop.*`。

---

## 手动安装（Git）

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
      - PORT=5200
      - TOKEN_FILE=/data/token.json
      - DATA_DIR=/data
      - AI_CONFIG_FILE=/data/ai_config.json
      - WEBHOOK_URL=           # 可选：外部 Webhook 地址
      - WEBHOOK_MODE=unknown_command  # 可选：unknown_command / all_messages
      - API_TOKEN=change-this-token    # 网络可访问时建议修改为强随机值
      - MARKDOWN_MODE=          # 可选：normalize 整理普通通知为 Markdown；plain 降级为纯文本；留空默认按 Markdown 发送
      - TZ=Asia/Shanghai
EOF

docker compose up -d
```

安装完成后，浏览器打开 `http://localhost:5200`，扫码登录即可。

> Webhook 也可以在 Web 管理面板中配置。环境变量适合容器化部署统一管理，Web UI 适合单机快速启用或临时调试。个人挂载、代理、AI CLI 凭据和内网服务地址建议放到未提交的 `docker-compose.override.yml`。

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
如果安装时使用了 PowerShell 一键脚本，可直接在此机器上重新运行该安装命令。脚本会自动进行文件拉取与覆盖、更新依赖并重启服务。

一键脚本更新采用覆盖式策略：上游新增文件会添加，同名文件会覆盖，安装目录中不同名的旧文件不会被删除。因此用户自己放在 `examples/` 中的私有插件只要不与上游同名，就会保留；如果同名，则以新版本为准。Git 安装不做额外处理，遵循 `git pull` 自身规则。

---

## 卸载

卸载操作默认保留 `data/` 目录（包含消息数据库、配置和缓存）。

```bash
# Windows (在项目根目录下执行)
powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1

# macOS / Linux (在项目根目录下执行)
bash scripts/uninstall.sh
```

如需彻底清除，卸载完成后请手动删除项目所在目录（如果你保留了原有的 `data/`）或使用命令强制删除。
