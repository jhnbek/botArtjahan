@echo off
rem ============================================================
rem  Trader Brain - shadow forward tracker (no orders, no money)
rem  Runs the frozen breakout-with-trend rule on closed daily
rem  bars and appends results to _shadow_forward\.
rem  Idempotent: safe to run any time, catches up missed days.
rem ============================================================
setlocal
cd /d "%~dp0"
if not exist "_shadow_forward" mkdir "_shadow_forward"
echo [%date% %time%] run >> "_shadow_forward\runs.log"
".venv\Scripts\python.exe" -X utf8 "knowledge_bot\shadow_breakout_tracker.py" >> "_shadow_forward\runs.log" 2>&1
set "tracker_exit=%errorlevel%"
endlocal & exit /b %tracker_exit%
