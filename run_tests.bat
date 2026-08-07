@echo off
cd /d "%~dp0"
echo ============================================
echo  dev-pipeline : full unit test (48 tests)
echo ============================================
echo.
python -X utf8 tests/run_all.py
echo.
echo Exit code: %ERRORLEVEL%
pause
