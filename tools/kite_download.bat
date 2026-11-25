@echo off
REM Downloads MCX history from Kite (read-only). Opens the Kite login in your browser if needed.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.data.kite_history
pause
