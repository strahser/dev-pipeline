@echo off
chcp 65001 >nul
cd /d "%~dp0"
title dev-pipeline : conveyor
set "PIPELINE=%~dp0"
set "PIPELINE=%PIPELINE:~0,-1%"

rem --- Автовыбор рабочего места: E:\ПлагиныРевит (рабочий ПК) или D:\Projects (домашний ПК) ---
set "HLR_D=E:\ПлагиныРевит\HeatLossRevit2"
if not exist "%HLR_D%\" set "HLR_D=D:\Projects\HeatLossRevit2"
set "MEP_D=E:\ПлагиныРевит\MepTaggingSolution"
if not exist "%MEP_D%\" set "MEP_D=D:\Projects\MepTaggingSolution"
set "HLR_MISSION=%HLR_D%\Tasks\00_Референсы\МИССИЯ_менеджера_HeatLossRevit2.md"

:MENU
cls
echo ============================================================
echo   dev-pipeline : конвейер проектов (одна кнопка)
echo   Проект HeatLossRevit2: %HLR_D%
echo ============================================================
echo   1. Server + dashboard (http://127.0.0.1:8787)
echo   2. Миссия HeatLossRevit2 (все агенты)
echo   3. Задача HeatLossRevit2 через qwen-worker
echo   4. Задача HeatLossRevit2 полноценным агентом
echo   5. Сторож контролёра (agent_watch, анти-зависание)
echo   6. Отчёт менеджера (HeatLossRevit2)
echo   7. Полные тесты dev-pipeline
echo   0. Exit
echo ============================================================
set /p CHOICE="Выбор: "

if "%CHOICE%"=="1" goto server
if "%CHOICE%"=="2" goto mission
if "%CHOICE%"=="3" goto taskqwen
if "%CHOICE%"=="4" goto taskfull
if "%CHOICE%"=="5" goto watch
if "%CHOICE%"=="6" goto report
if "%CHOICE%"=="7" goto tests
if "%CHOICE%"=="0" exit /b 0
goto MENU

:server
echo  Запуск сервера... (Ctrl+C для остановки)
python -X utf8 -m server --port 8787
pause
goto MENU

:mission
set /p "SPLIT=Подзадач (split, по умолч. 1): "
if "%SPLIT%"=="" set "SPLIT=1"
set /p "WORKER=Режим (qwen / full / empty): "
if "%WORKER%"=="" set "WORKER=qwen"
if "%WORKER%"=="qwen" (
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "%HLR_MISSION%" --split %SPLIT% --title "HeatLossRevit2_миссия" --worker qwen --sequential
) else if "%WORKER%"=="full" (
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "%HLR_MISSION%" --split %SPLIT% --title "HeatLossRevit2_миссия" --model opencode-go/qwen3.8-max --sequential
) else (
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "%HLR_MISSION%" --split %SPLIT% --title "HeatLossRevit2_миссия" --sequential
)
echo  Exit code: %ERRORLEVEL%
pause
goto MENU

:taskqwen
set /p "TASK=Задача (A-NN): "
if "%TASK%"=="" goto MENU
python -X utf8 agents/agent_manager.py task --project heatlossrevit2 --task %TASK% --worker qwen --sequential
echo  Exit code: %ERRORLEVEL%
pause
goto MENU

:taskfull
set /p "TASK=Задача (A-NN): "
if "%TASK%"=="" goto MENU
python -X utf8 agents/agent_manager.py task --project heatlossrevit2 --task %TASK% --model opencode-go/qwen3.8-max --sequential
echo  Exit code: %ERRORLEVEL%
pause
goto MENU

:watch
echo  Сторож контролёра: verify отчётов + детектор зависших задач (3 ч).
echo  Ctrl+C для остановки.
python -X utf8 agents/agent_watch.py --project heatlossrevit2
echo  Exit code: %ERRORLEVEL%
pause
goto MENU

:report
python -X utf8 agents/agent_manager.py report --project heatlossrevit2
echo.
pause
goto MENU

:tests
python -X utf8 tests/run_all.py
echo  Exit code: %ERRORLEVEL%
pause
goto MENU
