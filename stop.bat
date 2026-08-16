@echo off
chcp 65001 >nul

echo 正在停止会计分析工具箱 ...

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8501 ^| findstr LISTENING') do (
    set FOUND=1
    taskkill /PID %%a /F >nul 2>&1
)

if "%FOUND%"=="1" (
    echo [完成] 服务已停止，可以关闭浏览器和本窗口。
) else (
    echo [提示] 未检测到运行中的服务（可能已停止）。
)

pause
