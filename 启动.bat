@echo off
setlocal
cd /d "%~dp0"
title POLAR-RM Custom Client

echo [POLAR-RM] Starting custom client...

if not exist "main.py" (
    echo main.py was not found. Please keep this bat file in the client root folder.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating local Python environment...
    py -3 -m venv .venv >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    )

    if not exist ".venv\Scripts\python.exe" (
        echo Python 3 was not found, or the virtual environment could not be created.
        echo Please install Python 3.11+ first, then run this file again.
        pause
        exit /b 1
    )

    echo [2/3] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto install_failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto install_failed
)

echo [3/3] Launching client...
echo [POLAR-RM] Opening scenario selection...
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo POLAR-RM exited with an error.
    pause
)
exit /b 0

:install_failed
echo Dependency installation failed. Check the network connection or install packages manually:
echo .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
