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
streamlit run app.py --server.address=localhost --server.headless=false
pause
