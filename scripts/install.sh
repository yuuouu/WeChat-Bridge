#!/bin/bash
# ============================================================
#  WeChat Bridge — 一键安装脚本 (macOS / Linux)
#  用法: curl -fsSL https://raw.githubusercontent.com/yuuouu/WeChat-Bridge/main/scripts/install.sh | bash
# ============================================================
set -e

REPO="yuuouu/WeChat-Bridge"
INSTALL_DIR="${WECHAT_BRIDGE_DIR:-$(pwd)/wechat-bridge}"
PORT="${WECHAT_BRIDGE_PORT:-5200}"

# ── 颜色 ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }

echo ""
echo -e "${CYAN}💬 WeChat Bridge 安装程序${NC}"
echo -e "${CYAN}══════════════════════════════════${NC}"
echo ""

# ── 检测安装模式 ──
HAS_DOCKER=false
HAS_PYTHON=false
command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1 && HAS_DOCKER=true
command -v python3 &>/dev/null && HAS_PYTHON=true

if ! $HAS_DOCKER && ! $HAS_PYTHON; then
  error "未检测到 Docker 或 Python 3。请安装其中之一:\n  Docker: https://docs.docker.com/get-docker/\n  Python: https://www.python.org/downloads/"
fi

# 优先原生 Python，有 Docker 时让用户选
MODE="python"
if $HAS_DOCKER && $HAS_PYTHON; then
  echo -e "检测到 Docker 和 Python 3 均可用"
  echo -e "  ${CYAN}1)${NC} 原生 Python（轻量，无需容器运行时）"
  echo -e "  ${CYAN}2)${NC} Docker Compose（适合服务器长期运行）"
  echo ""
  read -rp "选择安装方式 [1/2, 默认 1]: " choice
  [[ "$choice" == "2" ]] && MODE="docker"
elif $HAS_DOCKER; then
  MODE="docker"
fi

info "安装方式: $([[ $MODE == docker ]] && echo 'Docker Compose' || echo '原生 Python')"

# ── 克隆/下载代码 ──
if command -v git &>/dev/null; then
  info "正在克隆仓库..."
  git clone --depth 1 "https://github.com/${REPO}.git" "${INSTALL_DIR}" 2>/dev/null || {
    warn "目录已存在，正在更新..."
    cd "${INSTALL_DIR}" && git pull --ff-only
  }
else
  info "正在下载源码..."
  mkdir -p "${INSTALL_DIR}"
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/main.tar.gz" | tar xz --strip-components=1 -C "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"
mkdir -p data
info "安装目录: ${INSTALL_DIR}"

if [[ "$MODE" == "docker" ]]; then
  # ── Docker 模式 ──
  info "正在构建并启动 Docker 容器..."
  docker compose up -d --build
  echo ""
  echo -e "${GREEN}══════════════════════════════════${NC}"
  echo -e "${GREEN}  ✅ WeChat Bridge 安装成功!${NC}"
  echo -e "${GREEN}══════════════════════════════════${NC}"
  echo ""
  echo -e "  📱 Web 面板:  ${CYAN}http://localhost:${PORT}${NC}"
  echo -e "  📋 查看日志:  docker logs -f wechat-bridge"
else
  # ── 原生 Python 模式 ──
  info "正在安装 Python 依赖..."
  pip3 install -q -r app/requirements.txt

  echo ""
  echo -e "${GREEN}══════════════════════════════════${NC}"
  echo -e "${GREEN}  ✅ WeChat Bridge 安装成功!${NC}"
  echo -e "${GREEN}══════════════════════════════════${NC}"
  echo ""
  echo -e "  🚀 启动服务:  ${CYAN}cd ${INSTALL_DIR} && bash scripts/start.sh${NC}"
  echo -e "  📱 Web 面板:  ${CYAN}http://localhost:${PORT}${NC}"
fi

echo ""
echo -e "  ${YELLOW}可选配置 (环境变量):${NC}"
echo -e "    API_TOKEN     — API 接口鉴权 Token"
echo -e "    WEBHOOK_URL   — 收到消息时 POST 推送的目标地址"
echo ""
