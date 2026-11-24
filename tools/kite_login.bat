@echo off
REM Daily Kite login on its own (start_bot.bat also does this).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.broker.kite_auth
pause
