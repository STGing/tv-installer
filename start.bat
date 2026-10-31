@echo off
chcp 65001 >nul
title TV Installer
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo 没找到 python，请先安装 Python 3.8+ 并勾选 "Add Python to PATH"
  pause
  exit /b 1
)
python server.py %*
echo.
echo 服务已退出。
pause
