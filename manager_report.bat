@echo off
cd /d "%~dp0"
echo ============================================
echo  dev-pipeline : manager report (project goals)
echo  Usage: manager_report.bat [project]
echo ============================================
set "PROJECT=%~1"
if "%PROJECT%"=="" set "PROJECT=meptaggingsolution"
echo  Project: %PROJECT%
echo.
python -X utf8 agents/agent_manager.py report --project %PROJECT%
echo.
pause
