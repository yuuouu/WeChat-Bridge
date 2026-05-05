#!/bin/bash
# ============================================================
#  WeChat Bridge — 卸载脚本 (macOS / Linux)
#  用法: bash wechat-bridge/scripts/uninstall.sh
#
#  默认不删除 data/ 目录（含消息数据库和配置）。
#  如需彻底清除，卸载完成后手动执行：
#    rm -rf <安装目录>/data
# ============================================================
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }

# 定位安装目录（脚本自身所在的上一级）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo -e "${CYAN}💬 WeChat Bridge 卸载程序${NC}"
echo -e "${CYAN}══════════════════════════════════${NC}"
echo ""
echo -e "  安装目录: ${CYAN}${INSTALL_DIR}${NC}"
echo ""

# ── 1. 停止服务 ──
if [ -f "${INSTALL_DIR}/scripts/stop.sh" ]; then
  info "正在停止服务..."
  bash "${INSTALL_DIR}/scripts/stop.sh" 2>/dev/null || true
fi

# 检查 Docker
if command -v docker &>/dev/null && [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
  info "正在停止 Docker 容器..."
  cd "${INSTALL_DIR}" && docker compose down 2>/dev/null || true
fi

# ── 2. 保留 data 目录，移出安装目录 ──
DATA_DIR="${INSTALL_DIR}/data"
BACKUP_DATA=""
if [ -d "$DATA_DIR" ]; then
  BACKUP_DATA="${INSTALL_DIR}/../wechat-bridge-data-backup"
  warn "保留数据目录: data/ → ${BACKUP_DATA}"
  mv "$DATA_DIR" "$BACKUP_DATA"
fi

# ── 3. 删除安装目录 ──
info "删除安装目录: ${INSTALL_DIR}"
rm -rf "$INSTALL_DIR"

# ── 4. 把 data 放回原位（如果需要重装可直接恢复） ──
if [ -n "$BACKUP_DATA" ] && [ -d "$BACKUP_DATA" ]; then
  mkdir -p "$INSTALL_DIR"
  mv "$BACKUP_DATA" "$DATA_DIR"
  info "数据目录已保留在: ${DATA_DIR}"
fi

echo ""
echo -e "${GREEN}══════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ WeChat Bridge 已卸载${NC}"
echo -e "${GREEN}══════════════════════════════════${NC}"
echo ""
if [ -d "$DATA_DIR" ]; then
  echo -e "  ${YELLOW}数据目录未删除:${NC} ${DATA_DIR}"
  echo -e "  如需彻底清除: ${CYAN}rm -rf ${DATA_DIR} && rm -rf ${INSTALL_DIR}${NC}"
  echo ""
fi
