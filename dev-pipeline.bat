@echo off
cd /d "%~dp0"
title dev-pipeline : conveyor

:MENU
cls
echo ============================================
echo   dev-pipeline : conveyor for projects
echo ============================================
echo   1. Start server + dashboard (http://127.0.0.1:8787)
echo   2. Run subagent on task (run_task.bat A-NN)
echo   3. Manager report (project goals)
echo   4. DXF visualization (MepTagging)
echo   5. Full unit test
echo   6. CLI: status / verify / dispatch
echo   0. Exit
echo ============================================
set /p CHOICE="Choose: "

if "%CHOICE%"=="1" call start_server.bat
if "%CHOICE%"=="2" (
  set /p "T=Task (A-NN): "
  set /p "PR=Project [meptaggingsolution]: "
  if "%PR%"=="" set "PR=meptaggingsolution"
  call run_task.bat %T% %PR%
)
if "%CHOICE%"=="3" (
  set /p "PR=Project [meptaggingsolution]: "
  if "%PR%"=="" set "PR=meptaggingsolution"
  call manager_report.bat %PR%
)
if "%CHOICE%"=="4" call dxf_report.bat
if "%CHOICE%"=="5" call run_tests.bat
if "%CHOICE%"=="6" (
  echo.
  echo   python -m pipeline.cli status ^<project^>
  echo   python -m pipeline.cli verify ^<project^> ^<A-NN^>
  echo   python -m pipeline.cli dispatch ^<project^> ^<file^> --title ...
  echo.
  pause
)
if "%CHOICE%"=="0" exit /b 0
goto MENU
