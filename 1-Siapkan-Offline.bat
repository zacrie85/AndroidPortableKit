@echo off
title Siapkan Offline - Android Portable Kit
cd /d "%~dp0"
echo.
echo  Mode OFFLINE: semua yang dibutuhkan udah kamu download dari Release.
echo  Bat ini tinggal merakit semuanya - GAK BUTUH INTERNET.
echo  Pastikan file-file ini ada di folder kit ini:
echo    - Runtime-QEMU-ADB.zip
echo    - android-base.qcow2.part1, part2, dst (atau BlissOS16-GApps.iso.part...)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\siapkan-offline.ps1"
