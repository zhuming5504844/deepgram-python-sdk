@echo off
setlocal
cd /d "%~dp0"

python scripts\build_gui_exe.py
if errorlevel 1 (
  echo Failed to build EXE package.
  pause
  exit /b 1
)

echo.
echo EXE packaging finished successfully.
pause
