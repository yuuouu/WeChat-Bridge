#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo ""
echo " 💬 WeChat Bridge"
echo " ══════════════════════════════════"
echo ""

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo " [X] Python 3 not found"
    echo "     macOS: brew install python3"
    echo "     Ubuntu/Debian: sudo apt install python3 python3-pip"
    exit 1
fi

# 安装依赖
pip3 install -q -r app/requirements.txt 2>/dev/null

# 创建数据目录
mkdir -p data

# 后台启动服务
echo " [OK] Starting service in background..."
nohup python3 app/main.py >> data/run.log 2>&1 &
PID=$!
echo $PID > data/wechat-bridge.pid

echo " [OK] Service started (PID: $PID)"
echo ""
echo "  Web UI:   http://localhost:5200"
echo "  Logs:     tail -f data/run.log"
echo "  Stop:     kill $PID"
echo ""
