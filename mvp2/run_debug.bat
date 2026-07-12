@echo off
echo ===================================================
echo   SpinEdge DEBUG MODE (Fast Start)
echo   Config Path: %USERPROFILE%\.spinedge\config\config.json
echo ===================================================

REM Check for venv
if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
) else (
    echo Venv not found at ..\venv, assuming active environment...
)

python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Application crashed! See logs above.
    pause
)
