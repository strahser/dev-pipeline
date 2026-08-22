@echo off
chcp 65001 >nul
title dev-pipeline СЕРВЕР (не закрывайте это окно)
cd /d "%~dp0"
echo ============================================
echo  Панель конвейера: http://127.0.0.1:8787/
echo  Останов: Ctrl+C в этом окне
echo ============================================
python -X utf8 -m server --host 127.0.0.1 --port 8787
pause
