@echo off
title Study Assistant Setup
cd /d "%~dp0"

echo ========================================================
echo        Study Assistant 1-Click Dependency Installer
echo ========================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

echo [*] Upgrading pip and installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo.
        echo [NOTE] Created a fresh .env file.
        echo Please edit the .env file and paste your own GROQ_API_KEY and GEMINI_API_KEY!
    )
)

echo.
echo ========================================================
echo [SUCCESS] Setup complete!
echo Edit your keys in .env, then double-click run_assistant.bat
echo ========================================================
pause
