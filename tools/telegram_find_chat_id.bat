@echo off
REM Shows your Telegram chat ID. First send your bot any message ("hi") in Telegram.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.notify.telegram --find-chat-id
pause
