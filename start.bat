@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Accounting Toolbox

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found.
    echo Create it first:
    echo     python -m venv venv
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    echo.
    echo See SKILL.md "Step 0" for which variant to install.
    pause
    exit /b 1
)

rem Launch with the venv interpreter directly (no activate / PATH).
rem Going through PATH can pick the system Python, which usually has no
rem torch -- the app would then silently fall back to rule-only mode.
"%~dp0venv\Scripts\python.exe" run.py
pause
