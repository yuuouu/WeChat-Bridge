@echo off
chcp 65001 >nul 2>&1
echo.
echo  Stopping WeChat Bridge...

:: 查找并结束 main.py 进程
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "main.py" >nul && (
        taskkill /pid %%a /f >nul 2>&1
        echo  [OK] Stopped process %%a
    )
)
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq pythonw.exe" /fo list ^| findstr "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr "main.py" >nul && (
        taskkill /pid %%a /f >nul 2>&1
        echo  [OK] Stopped process %%a
    )
)

echo  Done.
timeout /t 2 /nobreak >nul
