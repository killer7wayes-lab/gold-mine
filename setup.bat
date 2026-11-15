@echo off
setlocal
cd /d "%~dp0"
title Gold Mine - installation
echo.
echo   Gold Mine - installation
echo.
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo   Python n'est pas installe sur ce PC.
  echo   1. Ouvrez https://www.python.org/downloads/ et installez Python.
  echo   2. Pendant l'installation, cochez "Add python.exe to PATH".
  echo   3. Relancez setup.bat.
  echo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo   Creation de l'environnement Python...
  %PY% -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo   Impossible de creer l'environnement Python.
  pause
  exit /b 1
)
echo   Installation des composants, quelques minutes la premiere fois...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo   Echec de l'installation. Verifiez la connexion Internet puis relancez setup.bat.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m goldmine setup
pause
