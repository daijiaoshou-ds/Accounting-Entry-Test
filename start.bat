@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [错误] 未找到虚拟环境 venv\Scripts\activate.bat
    echo 请先执行:  python -m venv venv
    echo          venv\Scripts\activate
    echo          pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"
echo.
echo ============================================
echo   会计分析工具箱 启动中 ...
echo   停止方式：关闭浏览器后约 12 秒自动停止；
echo             或双击 stop.bat / 关闭本窗口立即停止。
echo ============================================
echo.
python run.py
pause
