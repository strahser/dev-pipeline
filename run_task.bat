@echo off
cd /d "%~dp0"
echo ============================================
echo  dev-pipeline : run subagent on a task
echo  Usage: run_task.bat A-NN [project]
echo  Example: run_task.bat A-04 meptaggingsolution
echo ============================================
set "TASK=%~1"
if "%TASK%"=="" (
  echo  Specify task: run_task.bat A-NN [project]
  exit /b 1
)
set "PROJECT=%~2"
if "%PROJECT%"=="" set "PROJECT=meptaggingsolution"
echo  Task: %TASK%   Project: %PROJECT%
echo.
python -X utf8 agents/agent_manager.py task --project %PROJECT% --task %TASK% --sequential
echo.
echo Exit code: %ERRORLEVEL%
pause
