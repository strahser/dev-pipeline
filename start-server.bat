@echo off
cd /d "%~dp0"
rem ASCII-only: Cyrillic in .bat breaks after chcp 65001 (cmd re-reads lines)
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul
if %errorlevel%==0 (
  echo [!] Port 8787 already in use - server seems to be running already.
  echo     Panel: http://127.0.0.1:8787/
  pause
  exit /b 0
)
title dev-pipeline SERVER
echo ============================================
echo  Panel: http://127.0.0.1:8787/
echo  Stop: Ctrl+C in this window
echo ============================================
python -X utf8 -m server --host 127.0.0.1 --port 8787
pause
