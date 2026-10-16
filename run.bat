@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" sayit.py
if errorlevel 1 (
    echo.
    echo Sedabot failed to start. If a window never opened, see sayit.log.
    pause
)
