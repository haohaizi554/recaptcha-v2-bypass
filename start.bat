@echo off
chcp 65001 >nul 2>&1
title reCAPTCHA v2 自动化绕过工具 - 多方案版
cd /d "%~dp0"

echo.
echo ============================================
echo   reCAPTCHA v2 自动化绕过工具
echo   正在启动...
echo ============================================
echo.

:: 检查 Python 是否可用
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python, 请安装 Python 3.10+ 并添加到 PATH
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查关键依赖
python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装依赖...
    pip install -r requirements.txt
    playwright install chromium
)

:: 启动 GUI 面板
python gui.py

pause
