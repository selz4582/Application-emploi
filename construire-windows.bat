@echo off
setlocal
cd /d "%~dp0"
title Construction - Carnet Emploi 42
where py >nul 2>nul
if not %errorlevel%==0 (
  echo Python 3 et le lanceur py sont requis pour construire l'application.
  pause
  exit /b 1
)
py -3 -m pip install --upgrade -r requirements-dev.txt || goto :error
py -3 -m PyInstaller --noconfirm --clean packaging\carnet-emploi-42.spec || goto :error
echo.
echo Executable cree : dist\CarnetEmploi42.exe
where iscc >nul 2>nul
if %errorlevel%==0 (
  iscc packaging\installer.iss || goto :error
  echo Installateur cree dans dist.
) else (
  echo Inno Setup non detecte : l'executable portable est disponible, mais pas l'installateur.
)
exit /b 0
:error
echo.
echo La construction a echoue.
pause
exit /b 1
