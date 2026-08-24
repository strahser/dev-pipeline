@echo off
cd /d D:\Projects\dev-pipeline
set SUBAGENT_TIMEOUT_SEC=5400
python -X utf8 -u -m agents.plan_runner --project heatlossrevit2ui > Tasks_heatloss_ui_runner_out.log 2>&1
