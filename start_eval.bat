@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Accounting Toolbox - EVAL MODE

rem ============================================================
rem  Model evaluation mode: 100% NN, no program fusion / fallback.
rem  Produces unreviewed training data WITH confidence for AI review.
rem  Use start.bat for normal (production) mode.
rem
rem  NOTE: keep this file ASCII-only + CRLF. Chinese text in a .bat
rem  combined with "chcp 65001" corrupts the rest of the file.
rem ============================================================
set NN_PURE_MODEL_EVAL=1

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv\Scripts\python.exe not found.
    pause
    exit /b 1
)

rem Launch with the venv interpreter directly (no activate / PATH).
"%~dp0venv\Scripts\python.exe" run.py
pause
