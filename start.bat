@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 像素对照台

if exist "config.local.bat" call "config.local.bat"

if exist ".venv\Scripts\python.exe" goto install

echo [首次运行] 正在准备 Python 环境...
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m venv .venv
if errorlevel 1 goto venv_failed
goto install

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -m venv .venv
if errorlevel 1 goto venv_failed

:install
".venv\Scripts\python.exe" -c "import sys; from importlib.metadata import version; v=lambda n:tuple(int(x) for x in version(n).split('.')[:2]); raise SystemExit(0 if sys.version_info >= (3,10) and (3,1) <= v('Flask') < (4,0) and (11,0) <= v('Pillow') < (13,0) and (3,0) <= v('waitress') < (4,0) and (1,7) <= v('lark-oapi') < (2,0) else 1)" >nul 2>nul
if not errorlevel 1 goto run_server

echo [首次运行] 正在安装运行组件...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto install_failed

:run_server
if not exist "server.py" goto incomplete_files
if not exist "feishu_preview_media.py" goto incomplete_files
if not exist "stop_previous_server.ps1" goto incomplete_files
if not defined PORT set "PORT=8000"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_previous_server.ps1" -Port "%PORT%" -ProjectRoot "%~dp0"
if errorlevel 1 goto stop_previous_failed

".venv\Scripts\python.exe" server.py
if errorlevel 1 goto server_failed
goto stopped

:no_python
echo.
echo 未找到 Python。请先安装 Python 3.10 或更高版本，并勾选“Add Python to PATH”。
goto failed

:venv_failed
echo.
echo 无法创建运行环境，请确认 Python 安装完整且当前目录可写。
goto failed

:install_failed
echo.
echo 运行组件安装失败。首次启动需要能够访问 Python 软件源。
goto failed

:incomplete_files
echo.
echo 程序文件不完整，无法启动。
echo 请确认 server.py、feishu_preview_media.py 和 stop_previous_server.ps1 已全部同步到当前目录。
goto failed

:stop_previous_failed
echo.
echo 无法安全停止旧服务。请根据上方提示处理占用端口的进程。
goto failed

:server_failed
echo.
echo 服务启动失败。请查看上方 Python 错误；旧进程已在启动前完成清理。
goto failed

:stopped
echo.
echo 服务已停止。
pause
exit /b 0

:failed
echo.
pause
exit /b 1
