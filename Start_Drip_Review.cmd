@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0drip_app.py"
) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
    "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "%~dp0drip_app.py"
) else (
    echo Python runtime missing. Install Python 3.12 64-bit, then run Setup_Drip_Review.cmd.
    pause
    exit /b 1
)
if errorlevel 1 pause
