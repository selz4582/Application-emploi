@echo off
cd /d "%~dp0"
start "Cap Emploi 42" http://127.0.0.1:8765
py -3 app.py
pause

