@echo off
REM ==================================================================
REM  MCX signal bot - double-click to start.
REM  SIGNALS ONLY: the bot never places orders.
REM  First run sets up Python packages (a few minutes, needs internet).
REM ==================================================================
setlocal
cd /d "%~dp0"
title MCX signal bot

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating the Python environment. This takes a few minutes...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :fail
)

".venv\Scripts\python.exe" -m bot.live.runner %*
echo.
pause
exit /b 0

:fail
echo.
echo Setup failed. Check that Python 3.11 is installed and you are online,
echo then delete the ".venv" folder and double-click start_bot.bat again.
pause
exit /b 1
