@echo off
title Android Portable Kit
cd /d "%~dp0"
if exist "AndroidPortable.exe" (
  start "" "AndroidPortable.exe"
  exit /b
)
if exist "launcher\AndroidPortable.exe" (
  start "" "launcher\AndroidPortable.exe"
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "launcher\ap_gui.py"
  exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" python "launcher\ap_gui.py"
  exit /b
)
echo.
echo  Launcher .exe belum ada dan Python juga tidak ketemu.
echo  Solusi:
echo    1. Pasang Python dari https://www.python.org/downloads/ - centang Add to PATH
echo    2. Jalankan launcher\BUILD-EXE.bat buat bikin AndroidPortable.exe
echo  Baca PANDUAN-LENGKAP.txt buat detailnya.
echo.
pause
