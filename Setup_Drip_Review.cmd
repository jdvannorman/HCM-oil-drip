@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error
echo Setup complete. Double-click Start_Drip_Review.cmd.
pause
exit /b 0
:error
echo Setup failed. Install Python 3.12 64-bit from python.org, with the Python launcher enabled, then try again.
pause
exit /b 1
