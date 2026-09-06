@echo off
rem Classroom sync server quick start (Windows, Python 3.10+).
rem Data lives in .\sync-server-data next to this script. Open TCP port 10001
rem in Windows Firewall for classroom PCs; do not expose it to the internet
rem without TLS.
chcp 65001 >nul
cd /d "%~dp0"
python sync_server/server.py serve --host 0.0.0.0 --port 10001
pause
