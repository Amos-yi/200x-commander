@echo off
set RT_SYMBOL=SUI_USDT
set RT_STATE_FILE=%~dp0rt_paper_v2_state_sui.json
set LANG=en_US.UTF-8
:loop
echo [%date% %time%] Starting SUI_USDT >> "%~dp0logs\rt_sui.log"
C:\Users\Administrator\gate_bot\.venv\Scripts\python.exe -c "exec(open(r'%~dp0rt_paper_v2.py', encoding='utf-8').read())" >> "%~dp0logs\rt_sui.log" 2>&1
echo [%date% %time%] Process exited. Restarting in 10s... >> "%~dp0logs\rt_sui.log"
timeout /t 10 /nobreak >nul
goto loop
