@echo off
echo 🚀 Starting SpineEdge Build Process...

:: Navigate to script directory
cd /d "%~dp0"

:: Activate Virtual Environment
if exist "..\venv\Scripts\activate.bat" (
    call "..\venv\Scripts\activate.bat"
) else (
    echo ⚠️  Venv not found at ..\venv. Trying local venv...
    if exist "venv\Scripts\activate.bat" (
        call "venv\Scripts\activate.bat"
    ) else (
        echo ❌  Could not find virtual environment!
        pause
        exit /b 1
    )
)

:: Run Build Script
python build_installer.py

echo.
if exist "dist\SpineEdge.exe" (
    echo ✅ Build Complete! Installer is located at:
    echo    %~dp0dist\SpineEdge.exe
) else (
    echo ❌ Build Failed. Check output above.
)

pause
