@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 像素对照台 - 部署到 Debian
set "PYTHONUTF8=1"
set "VENV_PYTHON=.venv\Scripts\python.exe"

echo [1/4] 检查本地 Python 环境...
if exist "%VENV_PYTHON%" goto ensure_dependencies

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m venv .venv
if errorlevel 1 goto venv_failed
goto ensure_dependencies

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -m venv .venv
if errorlevel 1 goto venv_failed

:ensure_dependencies
"%VENV_PYTHON%" -c "import sys, flask, PIL, waitress, lark_oapi, pytest, paramiko; valid=(sys.version_info.major == 3 and sys.version_info.minor in range(10, 100)) or sys.version_info.major in range(4, 100); raise SystemExit(0 if valid else 1)" >nul 2>nul
if not errorlevel 1 goto run_tests

echo [2/4] 安装测试与部署依赖...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements-dev.txt -r requirements-deploy.txt
if errorlevel 1 goto dependency_failed

:run_tests
echo [3/4] 运行完整测试...
"%VENV_PYTHON%" -m pytest tests -q
if errorlevel 1 goto tests_failed

echo [4/4] 连接 Debian 并部署...
"%VENV_PYTHON%" deploy\deploy_debian.py
if errorlevel 1 goto deploy_failed

echo.
echo 部署完成。
goto success

:no_python
echo.
echo 未找到 Python。请先安装 Python 3.10 或更高版本。
goto failed

:venv_failed
echo.
echo 无法创建 .venv，请确认 Python 安装完整且项目目录可写。
goto failed

:dependency_failed
echo.
echo 测试或部署依赖安装失败，请检查 Python 软件源和网络。
goto failed

:tests_failed
echo.
echo 测试未通过，已取消部署，服务器没有被修改。
goto failed

:deploy_failed
echo.
echo 部署失败。脚本已尝试恢复服务器上的上一版代码。
goto failed

:success
if not defined DEPLOY_NO_PAUSE pause
exit /b 0

:failed
if not defined DEPLOY_NO_PAUSE pause
exit /b 1
