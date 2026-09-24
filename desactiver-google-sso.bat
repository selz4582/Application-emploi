@echo off
setlocal
cd /d "%~dp0"
title Recuperation Google SSO - Carnet Emploi 42
if exist "CarnetEmploi42.exe" (
  CarnetEmploi42.exe --disable-google-sso
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (
    py -3 app.py --disable-google-sso
  ) else (
    python app.py --disable-google-sso
  )
)
if not %errorlevel%==0 (
  echo La desactivation a echoue.
  pause
  exit /b 1
)
echo Vous pouvez maintenant relancer l'application.
pause
