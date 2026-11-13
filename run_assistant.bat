@echo off
title Study Assistant Launcher
cd /d "%~dp0"

echo ========================================================
echo         Launching Study Assistant (Background)
echo ========================================================
echo.
echo Hotkeys:
echo   [Alt + E]           - Hover line and capture into buffer (repeat for each line)
echo   [Alt + Q]           - Solve all accumulated lines (or auto-solve at cursor)
echo   [Alt + C]           - Clear accumulated lines buffer
echo   [Alt + S]           - Quick snip box
echo   [Alt + W] or [Esc]  - Dismiss answer
echo.

:: Check if PIL is installed in the active Python environment
python -c "import PIL" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [!] Dependencies are missing in your Python environment.
    echo [*] Auto-installing required packages... Please wait a moment...
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Failed to install packages automatically.
        echo Please make sure you have internet access and run setup.bat first.
        pause
        exit /b 1
    )
    echo [SUCCESS] Dependencies installed!
    echo.
)

python study_assistant.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] An error occurred while running the script.
    pause
)
