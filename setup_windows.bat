@echo off
rem SeekMaid 女仆 Windows 侧一键初始化
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    if not exist ".venv\Scripts\python.exe" (
        echo [1/3] Creating virtualenv...
        py -3 -m venv .venv
    )
    echo [2/3] Upgrading pip...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    echo [3/3] Installing PySide6...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    echo.
    echo Setup complete. Run run_pet.bat to start.
) else (
    echo [ERROR] Python not found. Please install Python 3.10+ and check "Add to PATH".
)
pause
