@echo off
cd /d "%~dp0"
title Gold Mine - scan
if not exist ".venv\Scripts\python.exe" (
  echo   Lancez d'abord setup.bat.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m goldmine run
pause
