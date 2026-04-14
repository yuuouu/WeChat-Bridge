@echo off
chcp 65001 >nul 2>&1
echo.
echo  💬 WeChat Bridge
echo  ══════════════════════════════════
echo.

:: 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] 未检测到 Python，请先安装 Python 3.10+
    echo      https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 确保在项目根目录（脚本在 scripts/ 下，需回到上一级）
cd /d "%~dp0.."

:: 安装依赖（静默）
pip install -q -r app\requirements.txt >nul 2>&1

:: 创建数据目录
if not exist data mkdir data

:: 后台启动服务（pythonw 无窗口，如果不存在则用 python + start /b）
echo  [OK] 启动服务中...
where pythonw >nul 2>&1
if errorlevel 1 (
    :: 没有 pythonw，用 start /b 后台运行
    start "WeChat Bridge" /min python app\main.py
) else (
    :: pythonw 完全无窗口运行
    start "" pythonw app\main.py
)

echo  [OK] 服务已在后台运行
echo.
echo  浏览器将自动打开 http://localhost:5200
echo  日志文件: data\run.log
echo.
echo  停止服务: taskkill /f /im python.exe (或在任务管理器结束)
echo.

:: 等 2 秒让服务启动，CMD 窗口自动关闭
timeout /t 2 /nobreak >nul
