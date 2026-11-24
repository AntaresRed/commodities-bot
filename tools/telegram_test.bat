@echo off
REM Sends a TEST signal with Taken/Skipped buttons to your Telegram and waits for your tap.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.notify.telegram --test
pause
