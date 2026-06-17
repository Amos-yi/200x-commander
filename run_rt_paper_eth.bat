@echo off
set RT_SYMBOL=ETH_USDT
set RT_STATE_FILE=%~dp0rt_paper_v2_state_eth.json
set LANG=en_US.UTF-8
:loop
echo [%date% %time%] Starting ETH_USDT >> "%~dp0logs\rt_eth.log"
C:\Users\Administrator\gate_bot\.venv\Scripts\python.exe -c "exec(open(r'%~dp0rt_paper_v2.py', encoding='utf-8').read())" >> "%~dp0logs\rt_eth.log" 2>&1
echo [%date% %time%] Process exited. Restarting in 10s... >> "%~dp0logs\rt_eth.log"
timeout /t 10 /nobreak >nul
goto loop
