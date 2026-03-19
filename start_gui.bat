@echo off
setlocal
cd /d "%~dp0"

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

echo Installing GUI runtime dependencies...
python -m pip install PySide6 pyqtdarktheme python-dotenv
if errorlevel 1 (
  echo Failed to install GUI dependencies.
  pause
  exit /b 1
)

python gui.py
pause
