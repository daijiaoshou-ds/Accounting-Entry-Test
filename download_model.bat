@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [错误] 未找到虚拟环境 venv\Scripts\activate.bat
    echo 请先执行:  python -m venv venv
    echo          venv\Scripts\activate
    echo          pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    echo          pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
    pause
    exit /b 1
)

call "venv\Scripts\activate.bat"

python -c "import modelscope" 2>nul
if errorlevel 1 (
    echo [错误] 未安装 modelscope —— 它属于「模型版」依赖，功能版没装它。
    echo 请先执行: pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
    echo （模型版完整安装步骤见 SKILL.md 第零步 / Step 3）
    pause
    exit /b 1
)

echo [1/2] 下载 hajishou 模型到 summary_cleaner\nn\_storage ...
modelscope download daijiaoshou/hajishou-V1.0 --local_dir summary_cleaner\nn\_storage
if errorlevel 1 (
    echo [错误] 模型下载失败，请检查网络后重试；
    echo        也可到 https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0 手动下载 4 件交付物。
    pause
    exit /b 1
)

echo [2/2] 验证交付物...
if exist "summary_cleaner\nn\_storage\fine_tuned\model.safetensors" (
    echo [OK] 模型已就绪: summary_cleaner\nn\_storage\fine_tuned\model.safetensors
) else (
    echo [警告] 未找到 fine_tuned\model.safetensors，请检查下载结果。
)

pause
