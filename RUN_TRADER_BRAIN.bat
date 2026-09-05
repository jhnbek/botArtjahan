@echo off
rem ============================================================
rem  Trader Brain desktop launcher
rem  Uses a packaged build when present, otherwise runs source.
rem ============================================================
setlocal
cd /d "%~dp0"

if exist "dist\TraderBrain\TraderBrain.exe" goto packaged

if exist ".venv\Scripts\python.exe" goto source_venv

where python >nul 2>&1
if not errorlevel 1 goto source_python

where py >nul 2>&1
if not errorlevel 1 goto source_py

echo Python 3.12 was not found. Create .venv and install requirements.txt.
exit /b 1

:packaged
start "" "dist\TraderBrain\TraderBrain.exe"
exit /b %errorlevel%

:source_venv
".venv\Scripts\python.exe" -X utf8 "knowledge_bot\desktop_app.py"
exit /b %errorlevel%

:source_py
py -3.12 -X utf8 "knowledge_bot\desktop_app.py"
exit /b %errorlevel%

:source_python
python -X utf8 "knowledge_bot\desktop_app.py"
exit /b %errorlevel%
