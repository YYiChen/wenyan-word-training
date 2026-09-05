@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not defined PORT set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%/"
set "HEALTH=http://127.0.0.1:%PORT%/api/health"
set "SERVER=%~dp0tools\run_server.py"

rem Only reuse this project's service; unrelated HTTP servers on the same port are not accepted.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$r=Invoke-RestMethod -Uri '%HEALTH%' -TimeoutSec 1; if ($r.ok -and $r.app -eq 'wenyan-word-training') { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 goto open_page

where py >nul 2>nul
if not errorlevel 1 (
  start "WenyanQuizServer" /min py -3 "%SERVER%" --port %PORT%
  goto wait_for_server
)

where python >nul 2>nul
if not errorlevel 1 (
  start "WenyanQuizServer" /min python "%SERVER%" --port %PORT%
  goto wait_for_server
)

echo Python was not found. Read README.md for the manual start command.
pause
exit /b 1

:wait_for_server
for /l %%I in (1,1,15) do (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$r=Invoke-RestMethod -Uri '%HEALTH%' -TimeoutSec 1; if ($r.ok -and $r.app -eq 'wenyan-word-training') { exit 0 } else { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto open_page
  timeout /t 1 /nobreak >nul
)
echo The local service did not start. Read README.md for help.
pause
exit /b 1

:open_page
start "" "%URL%"
exit /b 0
