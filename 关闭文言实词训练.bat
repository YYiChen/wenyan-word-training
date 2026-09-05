@echo off
set "STOPPED=0"
set "PORT=8000"
curl.exe -X POST --fail --silent --max-time 2 "http://127.0.0.1:%PORT%/api/shutdown" >nul 2>nul
if not errorlevel 1 set "STOPPED=1"
timeout /t 1 /nobreak >nul
taskkill /im "文言实词限时训练.exe" >nul 2>nul
if not errorlevel 1 set "STOPPED=1"
taskkill /im "文言实词训练服务.exe" >nul 2>nul
if not errorlevel 1 set "STOPPED=1"
if "%STOPPED%"=="0" (
  echo 文言实词训练服务当前未运行。
) else (
  echo 文言实词训练服务已关闭。
)
timeout /t 2 /nobreak >nul
