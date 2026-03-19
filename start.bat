@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PY_CMD="
for %%P in ("py -3.14" "py -3" "py" "python" "python3") do (
    if not defined PY_CMD (
        call :try_python %%~P
    )
)

if not defined PY_CMD (
    echo [ERROR] 未找到可用的 Python 解释器。
    echo         请确认 Python 3.14.3 已安装，并在安装时启用了 Add python.exe to PATH 或 py launcher。
    pause
    exit /b 1
)

echo [INFO] 使用解释器: %PY_CMD%

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] 正在创建虚拟环境 .venv ...
    call %PY_CMD% -m venv .venv
    if errorlevel 1 goto :venv_error
)

set "VENV_PYTHON=.venv\Scripts\python.exe"

echo [INFO] 正在升级 pip ...
call "%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :pip_error

echo [INFO] 正在安装 GUI 运行依赖 ...
call "%VENV_PYTHON%" -m pip install -e . python-dotenv tkinterdnd2
if errorlevel 1 goto :deps_error

echo [INFO] 正在启动图形界面 ...
call "%VENV_PYTHON%" gui.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] GUI 已退出，退出码: %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%

:try_python
%* -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=%*"
)
exit /b 0

:venv_error
echo [ERROR] 创建虚拟环境失败。
pause
exit /b 1

:pip_error
echo [ERROR] pip 升级失败，请检查网络或 Python 安装。
pause
exit /b 1

:deps_error
echo [ERROR] 依赖安装失败，请检查网络连接或 pip 输出信息。
pause
exit /b 1
