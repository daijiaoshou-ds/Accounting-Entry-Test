@echo off
REM journal entry: same as venv\Scripts\python.exe journal.py (run from repo root).
REM AI review loop: journal inbox / next JOB / submit JOB --file d.json / report JOB
setlocal
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0journal.py" %*
endlocal
