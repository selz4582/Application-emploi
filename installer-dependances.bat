@echo off
setlocal
cd /d "%~dp0"
title Installation - Carnet Emploi 42
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install --upgrade -r requirements.txt
) else (
  python -m pip install --upgrade -r requirements.txt
)
if not %errorlevel%==0 (
  echo.
  echo L'installation des dependances a echoue.
  pause
  exit /b 1
)
echo.
echo Les dependances sont installees. Vous pouvez lancer demarrer.bat.
pause
