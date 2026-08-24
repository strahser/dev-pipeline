@echo off
rem ASCII-only: Cyrillic in .bat breaks after chcp (see start-server.bat)
cd /d "%~dp0"
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul
if %errorlevel%==0 exit /b 0
start "dev-pipeline SERVER" /min cmd /c "C:\Users\Strakhov\AppData\Local\Python\pythoncore-3.14-64\python.exe -X utf8 -m server --host 127.0.0.1 --port 8787 1>>srv.log 2>>srv_err.log"
