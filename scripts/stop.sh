#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/.."
PID_FILE="$PROJECT_DIR/data/wechat-bridge.pid"

echo ""
echo " Stopping WeChat Bridge..."

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo " [OK] Stopped process $PID"
    else
        echo " [!] Process $PID not running"
    fi
    rm -f "$PID_FILE"
else
    # 兜底：通过进程名查找
    PIDS=$(pgrep -f "python.*main\.py" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | while read pid; do
            kill "$pid" 2>/dev/null && echo " [OK] Stopped process $pid"
        done
    else
        echo " [!] No running WeChat Bridge process found"
    fi
fi

echo " Done."
