@echo off
title Carnet Emploi 42
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 app.py
) else (
  python app.py
)
if %errorlevel%==0 exit /b 0
echo.
echo Le demarrage a echoue. Verifiez que Python 3 est installe.
pause
