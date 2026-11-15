@echo off
setlocal
cd /d "%~dp0"
title Gold Mine - connexion a GitHub
git --version >nul 2>nul
if errorlevel 1 (
  echo   Git n'est pas installe. Installez "Git for Windows" depuis https://git-scm.com/download/win
  echo   puis relancez github.bat.
  pause
  exit /b 1
)
echo.
echo   Collez l'adresse de votre depot GitHub, par exemple :
echo   https://github.com/votre-nom/gold-mine.git
echo.
set /p URL=  Adresse : 
if "%URL%"=="" exit /b 1
if not exist ".git" git init -b main
git config user.name >nul 2>nul || git config user.name "Gold Mine"
git config user.email >nul 2>nul || git config user.email "goldmine@localhost"
git remote remove origin >nul 2>nul
git remote add origin "%URL%"
git add -A
git commit -m "Gold Mine" >nul 2>nul
echo.
echo   Envoi vers GitHub. Une fenetre de connexion GitHub peut s'ouvrir : connectez-vous.
git push -u origin HEAD:main
if errorlevel 1 (
  echo.
  echo   L'envoi a echoue. Verifiez l'adresse et que vous etes bien connecte a GitHub.
) else (
  echo.
  echo   C'est fait. Il reste a activer GitHub Pages : voir SETUP_WINDOWS.md, etape 4.
)
pause
