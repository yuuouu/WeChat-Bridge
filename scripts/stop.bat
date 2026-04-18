@echo off
setlocal

for %%n in (python.exe pythonw.exe) do (
    for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq %%n" /fo csv /nh 2^>nul') do (
        wmic process where "ProcessId=%%~a" get CommandLine 2>nul | findstr /i /c:"app\main.py" /c:"main.py" >nul
        if not errorlevel 1 (
            taskkill /pid %%~a /f >nul 2>&1
        )
    )
)

exit /b 0
