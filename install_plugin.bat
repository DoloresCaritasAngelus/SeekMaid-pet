@echo off
rem 安装 dsh-pet 插件到 DSH web profile
cd /d "%~dp0"
dsh plugin --profile web add file:%~dp0
if %errorlevel%==0 (
    echo.
    echo 安装成功。请重启 DSH 后桌宠会自动启动。
) else (
    echo.
    echo 安装失败。请确认 dsh 命令可用，或手动按 README.md 接线。
)
pause
