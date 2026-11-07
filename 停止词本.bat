@echo off
cd /d "%~dp0"
echo.
echo   Stopping wordbook background service ...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
echo.
echo   Press any key to close.
pause >nul
