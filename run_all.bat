@echo off
REM Launch all 3 processes — each in its own visible terminal window.
REM Double-click this file OR run from cmd:  run_all.bat
REM Close any of the windows to stop that process.

cd /d "%~dp0"

echo Starting D1 Portfolio Trading System...
echo  - Bot (trader)
echo  - Monitor agent (risk manager)
echo  - Analytics agent (analyst)
echo.

start "BOT — D1 Portfolio Trader"      cmd /k "python d1_portfolio_bot.py"
timeout /t 2 /nobreak >nul

start "MONITOR — Risk Manager"          cmd /k "python monitor_agent.py"
timeout /t 2 /nobreak >nul

start "ANALYTICS — Trade Analyst"       cmd /k "python analytics_agent.py"

echo.
echo All 3 windows launched. Close any window to stop that process.
echo This launcher window can now be closed.
timeout /t 5 /nobreak >nul
