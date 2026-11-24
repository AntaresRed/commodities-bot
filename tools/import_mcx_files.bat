@echo off
REM Imports the MCX files you saved in data\raw\mcx and shows what was found.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot.data.mcx_bhavcopy
pause
