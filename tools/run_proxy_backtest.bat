@echo off
REM Proxy backtest of S1/S3 on international prices (NOT the real MCX verdict).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.backtest.proxy
start "" "reports\proxy_backtest.md"
pause
