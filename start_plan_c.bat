@echo off
setlocal enabledelayedexpansion
echo Starting PLAN C - 23 coins x 43 strategies (filtered)
echo ========================================================

set BASE=%~dp0

set COINS=BTC ETH SOL BNB XRP ADA DOGE AVAX DOT LINK UNI ATOM LTC FIL APT ARB OP SUI SEI TIA INJ RUNE FET

for %%C in (%COINS%) do (
    if /I "%%C"=="SOL" (
        set BAT=%BASE%run_rt_paper.bat
    ) else (
        set BAT=%BASE%run_rt_paper_%%C.bat
    )
    echo Starting %%C_USDT ...
    start /MIN "rt_%%C" cmd /c "!BAT!"
    timeout /t 2 /nobreak >nul
)

echo.
echo All 23 coins started.
echo.
echo Starting hourly report daemon ...
start /MIN "hourly_report" cmd /c "C:\Users\Administrator\gate_bot\.venv\Scripts\python.exe %BASE%hourly_daemon.py"
echo ========================================================
