@echo off
REM S1/S3 backtest on real MCX prices from Kite. Run tools\kite_download.bat first.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.backtest.proxy --source kite
if exist "reports\mcx_backtest.md" start "" "reports\mcx_backtest.md"
pause
