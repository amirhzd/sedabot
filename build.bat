@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" -m pip install -r requirements-build.txt
"%PY%" -c "from app_icon import ensure_icon; from pathlib import Path; ensure_icon(Path('assets/sayit.ico'))"
set SAYIT_ONEFILE=1
if /I "%~1"=="onedir" set SAYIT_ONEFILE=0
"%PY%" -m PyInstaller --noconfirm sayit.spec
echo.
if "%SAYIT_ONEFILE%"=="1" (
    echo Single exe: dist\Sedabot.exe
) else (
    echo Folder build: dist\Sedabot\Sedabot.exe
)
echo The exe does not contain voice models. The first launch downloads them next to the exe.
