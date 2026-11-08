@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ai-loop 闭环编排台
set "PYTHONPATH=%~dp0src"
if not exist ".venv\Scripts\python.exe" (
  echo 找不到 .venv：请先在本目录建好虚拟环境并安装依赖。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m ai_loop.ui --open %*
if errorlevel 1 (
  echo.
  echo 启动失败，请把上面的报错发我。
  pause
  exit /b 1
)
echo.
echo 界面地址 http://127.0.0.1:7878/　（浏览器没自动打开就手动访问）
"%SystemRoot%\System32\ping.exe" -n 6 127.0.0.1 >nul
