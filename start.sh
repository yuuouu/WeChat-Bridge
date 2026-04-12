#!/usr/bin/env bash
set -e

echo ""
echo " 💬 WeChat Bridge"
echo " ══════════════════════════════════"
echo ""

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo " [✗] 未检测到 Python 3，请先安装:"
    echo "     macOS: brew install python3"
    echo "     Ubuntu/Debian: sudo apt install python3 python3-pip"
    exit 1
fi

# 安装依赖
echo " [1/3] 安装依赖..."
pip3 install -q -r app/requirements.txt

# 创建数据目录
mkdir -p data
echo " [2/3] 数据目录已就绪"

# 启动服务
echo " [3/3] 启动服务..."
echo ""
echo " ┌─────────────────────────────────────────┐"
echo " │  打开浏览器访问: http://localhost:5200   │"
echo " │  按 Ctrl+C 停止服务                      │"
echo " └─────────────────────────────────────────┘"
echo ""
cd app
python3 main.py
