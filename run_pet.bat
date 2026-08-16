@echo off
rem SeekMaid 女仆启动脚本 (Windows)
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" deepseek_pet.py
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw deepseek_pet.py
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python deepseek_pet.py
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 deepseek_pet.py
    exit /b 0
)

echo [ERROR] 未找到 Python。请先安装 Python 3.10+ 并勾选 Add to PATH。
pause
