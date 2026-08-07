@echo off
cd /d "%~dp0"
echo ============================================
echo  dev-pipeline : server + dashboard
echo ============================================
echo  Dashboard: http://127.0.0.1:8787/
echo  Stop: Ctrl+C
echo ============================================
echo.
python -X utf8 -m server --port 8787
pause
