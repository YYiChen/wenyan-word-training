@echo off
setlocal
cd /d "%~dp0"

set "PORT=8000"
set "URL=http://127.0.0.1:%PORT%/"
set "SERVER=tools\run_server.py"

rem The portable release includes one of these EXEs, so Python is not needed there.
if exist "文言实词限时训练.exe" (
  start "WenyanQuizServer" /min "文言实词限时训练.exe" --port %PORT%
  goto wait_for_server
)

if exist "文言实词训练服务.exe" (
  start "WenyanQuizServer" /min "文言实词训练服务.exe" --port %PORT%
  goto wait_for_server
)

rem In the source checkout, reuse an already-running development service.
powershell.exe -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
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

echo Python was not found. Install Python, or use the manual command in README.md.
pause
exit /b 1

:wait_for_server
for /l %%I in (1,1,10) do (
  powershell.exe -NoProfile -Command "try { Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
  if not errorlevel 1 goto open_page
  timeout /t 1 /nobreak >nul
)
echo The local service did not start. Please check README.md.
pause
exit /b 1

:open_page
start "" "%URL%"
exit /b 0
