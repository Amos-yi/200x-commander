@echo off
echo 关停所有...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im cmd.exe >nul 2>&1
echo 已关停
pause
