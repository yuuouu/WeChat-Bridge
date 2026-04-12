#!/bin/bash
# ============================================================
#  WeChat Bridge — 一键安装脚本
#  用法: curl -fsSL https://raw.githubusercontent.com/<REPO>/main/install.sh | bash
# ============================================================
set -e

REPO="yuuouu/wechat-bridge"
IMAGE="ghcr.io/${REPO}:latest"
INSTALL_DIR="${WECHAT_BRIDGE_DIR:-$(pwd)/wechat-bridge}"

# ── 颜色 ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo ""
echo -e "${CYAN}💬 WeChat Bridge 安装程序${NC}"
echo -e "${CYAN}══════════════════════════════════${NC}"
echo ""

# ── 1. 检查 Docker ──
if ! command -v docker &>/dev/null; then
  error "未检测到 Docker，请先安装 Docker: https://docs.docker.com/get-docker/"
fi
if ! docker compose version &>/dev/null 2>&1 && ! command -v docker-compose &>/dev/null; then
  error "未检测到 Docker Compose，请先安装: https://docs.docker.com/compose/install/"
fi
info "Docker 已就绪"

# ── 2. 创建工作目录 ──
mkdir -p "${INSTALL_DIR}/data"
cd "${INSTALL_DIR}"
info "安装目录: ${INSTALL_DIR}"

# ── 3. 检测端口占用 ──
PORT="${WECHAT_BRIDGE_PORT:-5200}"
if command -v ss &>/dev/null; then
  if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    warn "端口 ${PORT} 已被占用，可通过 WECHAT_BRIDGE_PORT 环境变量指定其他端口"
  fi
elif command -v lsof &>/dev/null; then
  if lsof -i ":${PORT}" &>/dev/null; then
    warn "端口 ${PORT} 已被占用"
  fi
fi

# ── 4. 生成 docker-compose.yml ──
cat > docker-compose.yml <<YAML
services:
  wechat-bridge:
    image: ${IMAGE}
    container_name: wechat-bridge
    restart: unless-stopped
    ports:
      - "${PORT}:5200"
    volumes:
      - ./data:/data
    environment:
      - PORT=5200
      - TOKEN_FILE=/data/token.json
      - CONTACTS_FILE=/data/contacts.json
      - WEBHOOK_URL=\${WEBHOOK_URL:-}
      - API_TOKEN=\${API_TOKEN:-}
      - TZ=\${TZ:-Asia/Shanghai}
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
YAML
info "docker-compose.yml 已生成"

# ── 5. 拉取镜像并启动 ──
info "正在拉取镜像 ${IMAGE} ..."
if docker compose version &>/dev/null 2>&1; then
  docker compose pull
  docker compose up -d
else
  docker-compose pull
  docker-compose up -d
fi

echo ""
echo -e "${GREEN}══════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ WeChat Bridge 安装成功!${NC}"
echo -e "${GREEN}══════════════════════════════════${NC}"
echo ""
echo -e "  📱 Web 管理面板:  ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):${PORT}${NC}"
echo -e "  📂 数据目录:      ${INSTALL_DIR}/data"
echo -e "  📋 查看日志:      docker logs -f wechat-bridge"
echo ""
echo -e "  ${YELLOW}可选配置 (写入 .env 文件或设为环境变量):${NC}"
echo -e "    WEBHOOK_URL   — 收到消息时 POST 推送的目标地址"
echo -e "    API_TOKEN     — API 接口鉴权 Token"
echo ""
