@echo off
setlocal
title Configuration des connecteurs - Carnet Emploi 42
echo Les identifiants restent dans les variables utilisateur Windows.
echo Laissez un champ vide pour ne pas modifier sa configuration actuelle.
echo.
set /p "FT_ID=Identifiant client France Travail : "
if not "%FT_ID%"=="" setx FRANCE_TRAVAIL_CLIENT_ID "%FT_ID%" >nul
set /p "FT_SECRET=Secret client France Travail : "
if not "%FT_SECRET%"=="" setx FRANCE_TRAVAIL_CLIENT_SECRET "%FT_SECRET%" >nul
set /p "INSEE_TOKEN=Jeton API INSEE : "
if not "%INSEE_TOKEN%"=="" setx INSEE_API_TOKEN "%INSEE_TOKEN%" >nul
echo.
echo Configuration enregistree. Redemarrez Carnet Emploi 42.
pause
