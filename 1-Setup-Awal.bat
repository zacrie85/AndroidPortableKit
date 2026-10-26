@echo off
title Setup Awal - Android Portable Kit
cd /d "%~dp0"
echo.
echo  Ini setup AWAL yang cuma perlu dijalankan SEKALI.
echo  Nanti prosesnya: download QEMU + ADB + ISO Android, lalu install
echo  Android ke disk virtual. Butuh koneksi internet dan ruang disk 10+ GB.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
