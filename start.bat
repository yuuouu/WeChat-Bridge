@echo off
chcp 65001 >nul 2>&1
echo.
echo  💬 WeChat Bridge
echo  ══════════════════════════════════
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [✗] 未检测到 Python，请先安装 Python 3.11+
    echo      https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 安装依赖
echo  [1/3] 安装依赖...
pip install -q -r app\requirements.txt
if errorlevel 1 (
    echo  [✗] 依赖安装失败
    pause
    exit /b 1
)

:: 创建数据目录
if not exist data mkdir data
echo  [2/3] 数据目录已就绪

:: 启动服务
echo  [3/3] 启动服务...
echo.
echo  ┌─────────────────────────────────────────┐
echo  │  打开浏览器访问: http://localhost:5200   │
echo  │  按 Ctrl+C 停止服务                      │
echo  └─────────────────────────────────────────┘
echo.
cd app
python main.py
