@echo off
cd /d "%~dp0"
echo.
echo   Wordbook - debug launcher (keeps this console open)
echo.
echo   Checking for an already running instance ...
powershell -NoProfile -Command "$f=Join-Path $env:LOCALAPPDATA 'Wordbook\port.txt'; if(Test-Path $f){ $p=(Get-Content $f -Raw).Trim(); try{ $r=Invoke-RestMethod ('http://127.0.0.1:'+$p+'/api/ping') -TimeoutSec 2; if($r.app -eq 'wordbook'){ Write-Host ('  Already running on port ' + $p + ' - not starting a second copy.'); exit 9 } }catch{} }"
if errorlevel 9 (
    echo.
    echo   Press any key to close.
    pause >nul
    exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 wordbook.py
) else (
    python wordbook.py
)
echo.
echo   Service stopped. Press any key to close.
pause >nul
