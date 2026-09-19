@echo off
setlocal
set "DOSSIER=%~1"
if "%DOSSIER%"=="" (
  echo Indiquez le dossier de votre cle USB ou disque externe.
  set /p "DOSSIER=Dossier de sauvegarde externe : "
)
if "%DOSSIER%"=="" exit /b 1
if not exist "%DOSSIER%" mkdir "%DOSSIER%"
if not exist "%DOSSIER%" (
  echo Impossible de creer ou d'ouvrir ce dossier.
  pause
  exit /b 1
)
setx CARNET_EMPLOI_BACKUP_DIR "%DOSSIER%" >nul
if not %errorlevel%==0 (
  echo La configuration a echoue.
  pause
  exit /b 1
)
echo Dossier configure : %DOSSIER%
echo Redemarrez Carnet Emploi 42 pour appliquer ce choix.
pause
