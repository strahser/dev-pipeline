@echo off
chcp 65001 >nul
cd /d "%~dp0"
title dev-pipeline : conveyor
set "PIPELINE=e:\ПлагиныРевит\dev-pipeline"

:MENU
cls
echo ============================================================
echo   dev-pipeline : конвейер проектов (одна кнопка)
echo ============================================================
echo   1. Server + dashboard (http://127.0.0.1:8787)
echo   2. Миссия менеджера HeatLossRevit2 (все агенты)
echo   3. Задача HeatLossRevit2 через qwen-worker
echo   4. Задача HeatLossRevit2 полноценным агентом
echo   5. tdl-plan: построить иерархию миссии
echo   6. TDL: status / tree / validate / verify / dispatch
echo   7. Отчёт менеджера (HeatLossRevit2)
echo   8. Полные тесты dev-pipeline
echo   0. Exit
echo ============================================================
set /p CHOICE="Выбор: "

if "%CHOICE%"=="1" goto server
if "%CHOICE%"=="2" goto mission
if "%CHOICE%"=="3" goto taskqwen
if "%CHOICE%"=="4" goto taskfull
if "%CHOICE%"=="5" goto plan
if "%CHOICE%"=="6" goto tdl
if "%CHOICE%"=="7" goto report
if "%CHOICE%"=="8" goto tests
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
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "e:\ПлагиныРевит\HeatLossRevit2\Tasks\00_Референсы\МИССИЯ_менеджера_HeatLossRevit2.md" --split %SPLIT% --title "HeatLossRevit2_миссия" --worker qwen --sequential
) else if "%WORKER%"=="full" (
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "e:\ПлагиныРевит\HeatLossRevit2\Tasks\00_Референсы\МИССИЯ_менеджера_HeatLossRevit2.md" --split %SPLIT% --title "HeatLossRevit2_миссия" --model opencode-go/qwen3.8-max --sequential
) else (
  python -X utf8 agents/agent_manager.py mission --project heatlossrevit2 --mission "e:\ПлагиныРевит\HeatLossRevit2\Tasks\00_Референсы\МИССИЯ_менеджера_HeatLossRevit2.md" --split %SPLIT% --title "HeatLossRevit2_миссия" --sequential
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

:plan
set /p "SPEC=Спецификация (JSON) [HeatLossRevit2 план]: "
if "%SPEC%"=="" set "SPEC=e:\ПлагиныРевит\HeatLossRevit2\Tasks\00_Референсы\МИССИЯ_HeatLossRevit2_план.json"
python -X utf8 -m pipeline.cli tdl-plan heatlossrevit2 "%SPEC%"
echo  Exit code: %ERRORLEVEL%
pause
goto MENU

:tdl
echo.
echo   python -m pipeline.cli tdl-status heatlossrevit2
echo   python -m pipeline.cli tdl-tree heatlossrevit2
echo   python -m pipeline.cli tdl-validate heatlossrevit2
echo   python -m pipeline.cli tdl-verify heatlossrevit2 ^<A-NN^>
echo   python -m pipeline.cli tdl-dispatch heatlossrevit2 ^<файл^> --title ...
echo.
python -X utf8 -m pipeline.cli tdl-status heatlossrevit2
echo.
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
