@echo off
cd /d "%~dp0"
echo ============================================
echo  dev-pipeline : DXF visualization (MepTagging)
echo  rooms/elements/tags/shifts + room summary
echo ============================================
echo.
echo  --- build CoreConsoleRunner (if needed) ---
dotnet build "E:\ПлагиныРевит\MepTaggingSolution\CoreConsoleRunner\CoreConsoleRunner.csproj" --nologo -v q
echo.
echo  --- DXF from fixture View1 ---
python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --dxf --out "Tasks\Эксперт\View1.dxf"
echo.
echo  --- room summary + collisions ---
python -X utf8 agents/dump_suggestions.py --project meptaggingsolution --summary --verify
echo.
echo  Open DXF: E:\ПлагиныРевит\MepTaggingSolution\Tasks\Эксперт\View1.dxf
pause
