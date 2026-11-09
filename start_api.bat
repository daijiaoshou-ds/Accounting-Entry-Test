@echo off
REM Start the cleaner HTTP API (127.0.0.1:8502).
REM Streamlit stays on 8501; both can run at the same time.
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -m api.run_api --host 127.0.0.1 --port 8502
endlocal
