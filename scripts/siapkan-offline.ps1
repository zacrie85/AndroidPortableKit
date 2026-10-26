# =============================================================
#  siapkan-offline.ps1 - Nyiapin kit dari file yang sudah di-download
#  (mode OFFLINE - tidak butuh internet sama sekali)
#
#  Yang dikerjakan:
#   1. QEMU + ADB   : extract Runtime-QEMU-ADB.zip -> runtime\
#   2. Sistem Android: gabungkan android-base.qcow2.part* -> base-system.qcow2
#      (kalau yang ada malah ISO BlissOS terpecah, gabungkan ISO-nya
#       lalu suruh user jalankan 1-Setup-Awal.bat - tetap tanpa internet)
#   3. Bikin profile pertama "Utama"
# =============================================================

$ErrorActionPreference = "Stop"

function Write-Head($t)  { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Write-Ok($t)    { Write-Host "  [OK] $t" -ForegroundColor Green }
function Write-Info($t)  { Write-Host "  $t" -ForegroundColor Gray }
function Write-Warn2($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Write-Err2($t)  { Write-Host "  [X] $t" -ForegroundColor Red }

$KitRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $KitRoot

$RuntimeDir = Join-Path $KitRoot "runtime"
$QemuDir    = Join-Path $RuntimeDir "qemu"
$AdbDir     = Join-Path $RuntimeDir "adb"
$AndroidDir = Join-Path $RuntimeDir "android"
$IsoDir     = Join-Path $AndroidDir "iso"
$BaseDisk   = Join-Path $AndroidDir "base-system.qcow2"
$ProfilesDir= Join-Path $KitRoot "data\profiles"
$QemuExe    = Join-Path $QemuDir "qemu-system-x86_64.exe"
$QemuImg    = Join-Path $QemuDir "qemu-img.exe"
$AdbExe     = Join-Path $AdbDir "adb.exe"

Write-Host ""
Write-Host "################################################" -ForegroundColor Magenta
Write-Host "#   SIAPKAN OFFLINE - ANDROID PORTABLE KIT     #" -ForegroundColor Magenta
Write-Host "#      (tidak butuh internet sama sekali)      #" -ForegroundColor Magenta
Write-Host "################################################" -ForegroundColor Magenta

# ---------------------------------------------------------
# 1. QEMU + ADB dari Runtime-QEMU-ADB.zip
# ---------------------------------------------------------
Write-Head "LANGKAH 1/3 : QEMU + ADB"
if ((Test-Path $QemuExe) -and (Test-Path $AdbExe)) {
    Write-Ok "QEMU dan ADB udah ada"
} else {
    $rtZip = Get-ChildItem -Path $KitRoot -Filter "Runtime-QEMU-ADB.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $rtZip) {
        Write-Err2 "Runtime-QEMU-ADB.zip gak ketemu di folder kit."
        Write-Err2 "Download file itu dari halaman Release, taruh di folder ini:"
        Write-Info $KitRoot
        Read-Host "Tekan ENTER buat keluar"; exit 1
    }
    Write-Info ("Extract " + $rtZip.Name + " ...")
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    Expand-Archive -Force -Path $rtZip.FullName -DestinationPath $RuntimeDir
    if (Test-Path $QemuExe) { Write-Ok "QEMU siap" } else { Write-Err2 "qemu-system-x86_64.exe gak ketemu setelah extract"; Read-Host "ENTER"; exit 1 }
    if (Test-Path $AdbExe)  { Write-Ok "ADB siap" } else { Write-Err2 "adb.exe gak ketemu setelah extract"; Read-Host "ENTER"; exit 1 }
}

# ---------------------------------------------------------
# 2. Sistem Android: base qcow2 (hasil pre-install) ATAU ISO
# ---------------------------------------------------------
Write-Head "LANGKAH 2/3 : Sistem Android"
New-Item -ItemType Directory -Force -Path $AndroidDir | Out-Null

if (Test-Path $BaseDisk) {
    Write-Ok "Sistem Android (base-system.qcow2) udah ada"
} else {
    # cari bagian-bagian disk yang udah di-preinstall
    $parts = Get-ChildItem -Path $KitRoot -Filter "android-base.qcow2.part*" -ErrorAction SilentlyContinue |
             Sort-Object Name
    if ($parts) {
        Write-Info ("Nggabungin " + $parts.Count + " bagian disk Android (sabar ya, 2-5 menit)...")
        $list = ($parts | ForEach-Object { '"' + $_.FullName + '"' }) -join "+"
        cmd /c copy /b $list "`"$BaseDisk`"" | Out-Null
        $size = (Get-Item $BaseDisk).Length
        if ($size -lt 1GB) {
            Write-Err2 "Hasil gabungan kecil sekali ($([math]::Round($size/1MB)) MB) - kayanya ada bagian yang kurang download."
            Read-Host "Tekan ENTER buat keluar"; exit 1
        }
        Write-Ok ("base-system.qcow2 jadi (" + [math]::Round($size/1GB,2) + " GB)")
        Write-Info "Bagian .part boleh dihapus manual kalau mau hemat tempat."
    } else {
        # fallback: mungkin yang ada adalah ISO BlissOS terpecah
        $isoParts = Get-ChildItem -Path $KitRoot -Filter "BlissOS16-GApps.iso.part*" -ErrorAction SilentlyContinue |
                    Sort-Object Name
        if ($isoParts) {
            New-Item -ItemType Directory -Force -Path $IsoDir | Out-Null
            $destIso = Join-Path $IsoDir "Bliss-v16.9.7-gapps.iso"
            Write-Info "Nggabungin ISO Android (mode install manual, sekali aja)..."
            $list = ($isoParts | ForEach-Object { '"' + $_.FullName + '"' }) -join "+"
            cmd /c copy /b $list "`"$destIso`"" | Out-Null
            if ((Get-Item $destIso).Length -gt 1GB) {
                Write-Ok "ISO Android siap di runtime\android\iso\"
                Write-Host ""
                Write-Host "  >>> SEKARANG JALANKAN: 1-Setup-Awal.bat <<<" -ForegroundColor Yellow
                Write-Host "  Setup bakal skip semua download (semua udah ada),"
                Write-Host "  dan tinggal 1 langkah: install Android di jendela"
                Write-Host "  yang muncul (pilih 'Auto Install to specified harddisk')."
                Write-Host ""
                Read-Host "Tekan ENTER buat nutup"
                exit 0
            } else {
                Write-Err2 "Gabungan ISO gak lengkap. Cek lagi file .part-nya."
                Read-Host "ENTER"; exit 1
            }
        } else {
            Write-Err2 "File sistem Android gak ketemu di folder kit."
            Write-Err2 "Cari file yang namanya diawali 'android-base.qcow2.part' ATAU"
            Write-Err2 "'BlissOS16-GApps.iso.part' di halaman Release, download SEMUA bagian,"
            Write-Err2 "lalu taruh semua di folder ini:"
            Write-Info $KitRoot
            Read-Host "Tekan ENTER buat keluar"; exit 1
        }
    }
}

# ---------------------------------------------------------
# 3. Profile pertama "Utama"
# ---------------------------------------------------------
Write-Head "LANGKAH 3/3 : Profile pertama ('Utama')"
$utama = Join-Path $ProfilesDir "Utama"
if (Test-Path (Join-Path $utama "overlay.qcow2")) {
    Write-Ok "Profile 'Utama' udah ada"
} else {
    New-Item -ItemType Directory -Force -Path $utama | Out-Null
    & $QemuImg create -f qcow2 -b $BaseDisk -F qcow2 (Join-Path $utama "overlay.qcow2") | Out-Null
    if (Test-Path (Join-Path $utama "overlay.qcow2")) { Write-Ok "Profile 'Utama' dibuat" }
    else { Write-Err2 "Gagal bikin profile"; Read-Host "ENTER"; exit 1 }
}

New-Item -ItemType File -Force -Path (Join-Path $AndroidDir "READY.flag") | Out-Null

Write-Host ""
Write-Host "###############################################" -ForegroundColor Green
Write-Host "#   SIAP! SEMUA OFFLINE, GAK BUTUH INTERNET   #" -ForegroundColor Green
Write-Host "###############################################" -ForegroundColor Green
Write-Host ""
Write-Host "  1. Jalankan 2-Buka-Launcher.bat"
Write-Host "  2. Klik JALANKAN ANDROID"
Write-Host "  3. Login Google -> install TikTok/IG/SnackVideo/YT dari Play Store"
Write-Host "  4. Klik 'KIRIM VIDEO DARI PC' buat upload video kamu"
Write-Host ""
Read-Host "Tekan ENTER buat nutup"
