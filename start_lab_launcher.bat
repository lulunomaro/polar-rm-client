@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title POLAR Lab Launcher
color 0B

echo.
echo  ============================================
echo    POLAR x Gestalt Lab Launcher
echo  ============================================
echo.

if not exist "%~dp0main.py" (
    echo [ERROR] main.py not found.
    echo Run this file inside rm_custom_client folder.
    goto fail
)

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [1/2] Creating venv ...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%~dp0.venv"
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo [ERROR] Python 3 not found. Install Python 3.11+ .
            goto fail
        )
        python -m venv "%~dp0.venv"
    )
    if not exist "%PY%" (
        echo [ERROR] Failed to create .venv
        goto fail
    )
    echo [2/2] Installing requirements ...
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto fail
)

if not exist "%~dp0tools\gestalt_lab_launcher.py" (
    echo [ERROR] tools\gestalt_lab_launcher.py missing
    goto fail
)

if not exist "%~dp0tmp_selfcheck" mkdir "%~dp0tmp_selfcheck"

echo Starting GUI ...
echo Python: %PY%
echo Log: %~dp0tmp_selfcheck\launcher_error.txt
echo.

"%PY%" -X utf8 "%~dp0tools\gestalt_lab_launcher.py"
set "RC=%ERRORLEVEL%"

if "%RC%"=="0" (
    echo Launcher closed normally.
    exit /b 0
)

echo.
echo [ERROR] Launcher exited with code %RC%
if exist "%~dp0tmp_selfcheck\launcher_error.txt" (
    echo ---- error log ----
    type "%~dp0tmp_selfcheck\launcher_error.txt"
    echo -------------------
)

:fail
echo.
echo Window will stay open. Press any key to exit.
pause >nul
exit /b 1