@echo off
setlocal

cd /d "%~dp0.."

set "PYTHON_EXE="
set "PYTHONW_EXE="

if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if exist ".venv\Scripts\pythonw.exe" set "PYTHONW_EXE=%CD%\.venv\Scripts\pythonw.exe"

if not defined PYTHON_EXE (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [X] Python 3.10+ not found.
        exit /b 1
    )
    for /f "delims=" %%i in ('where python') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
    )
    where pythonw >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%i in ('where pythonw') do (
            if not defined PYTHONW_EXE set "PYTHONW_EXE=%%i"
        )
    )
)

"%PYTHON_EXE%" -m pip install -q -r app\requirements.txt >nul 2>&1
if not exist data mkdir data

if defined PYTHONW_EXE (
    start "" "%PYTHONW_EXE%" app\main.py
) else (
    start "WeChat Bridge" /min "%PYTHON_EXE%" app\main.py
)

ping -n 3 127.0.0.1 >nul
start "" http://localhost:5200

exit /b 0
