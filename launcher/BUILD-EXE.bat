@echo off
title Build EXE Launcher - Android Portable Kit
cd /d "%~dp0"
echo.
echo  Ini bakal bikin AndroidPortable.exe dari source Python.
echo  Butuh Python 3.9+ terpasang (centang Add to PATH waktu install).
echo  Butuh internet buat download PyInstaller sekali aja.
echo.
python --version >nul 2>nul
if errorlevel 1 (
  echo [X] Python tidak ketemu.
  echo     Download di https://www.python.org/downloads/
  echo     Saat install, CENTANG "Add Python to PATH".
  pause
  exit /b 1
)
echo [1/3] Pasang PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [X] Gagal pasang PyInstaller. Cek koneksi internet.
  pause
  exit /b 1
)
echo [2/3] Build EXE...
python -m PyInstaller --noconsole --onefile --name AndroidPortable ap_gui.py
if errorlevel 1 (
  echo [X] Build gagal. Baca pesan error di atas.
  pause
  exit /b 1
)
echo [3/3] Pindahin EXE ke folder utama kit...
copy /y "dist\AndroidPortable.exe" "..\AndroidPortable.exe" >nul
echo.
echo  SELESAI! AndroidPortable.exe udah ada di folder utama kit.
echo  Dari sekarang tinggal jalankan 2-Buka-Launcher.bat.
echo.
pause
