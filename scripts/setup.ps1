# =============================================================
#  setup.ps1 - Setup Awal Android Portable Kit
#  Tugasnya (sekali jalan aja):
#   1. Download & pasang QEMU portable
#   2. Download & pasang ADB (platform-tools)
#   3. Download ISO BlissOS 16 (Android 13, sudah ada Play Store)
#   4. Bikin disk virtual + install Android ke dalemnya
#   5. Bikin profile pertama bernama "Utama"
# =============================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Head($t)  { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Write-Info($t)  { Write-Host "  $t" -ForegroundColor Gray }
function Write-Ok($t)    { Write-Host "  [OK] $t" -ForegroundColor Green }
function Write-Warn2($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Write-Err2($t)  { Write-Host "  [X] $t" -ForegroundColor Red }

# --- posisi folder kit (root = dua tingkat di atas file ini) ---
$KitRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $KitRoot

$RuntimeDir = Join-Path $KitRoot "runtime"
$QemuDir    = Join-Path $RuntimeDir "qemu"
$AdbDir     = Join-Path $RuntimeDir "adb"
$AndroidDir = Join-Path $RuntimeDir "android"
$IsoDir     = Join-Path $AndroidDir "iso"
$BaseDisk   = Join-Path $AndroidDir "base-system.qcow2"
$ReadyFlag  = Join-Path $AndroidDir "READY.flag"
$LinksFile  = Join-Path $KitRoot "config\links.ini"
$ConfigFile = Join-Path $KitRoot "config\config.json"
$ProfilesDir= Join-Path $KitRoot "data\profiles"

$QemuExe    = Join-Path $QemuDir "qemu-system-x86_64.exe"
$QemuImg    = Join-Path $QemuDir "qemu-img.exe"
$AdbExe     = Join-Path $AdbDir "adb.exe"

Write-Host ""
Write-Host "################################################" -ForegroundColor Magenta
Write-Host "#      SETUP AWAL - ANDROID PORTABLE KIT       #" -ForegroundColor Magenta
Write-Host "#   TikTok / Instagram / SnackVideo / YouTube  #" -ForegroundColor Magenta
Write-Host "################################################" -ForegroundColor Magenta
Write-Info ("Folder kit : " + $KitRoot)

# --- baca links.ini ---
$Links = @{}
if (Test-Path $LinksFile) {
    $section = ""
    Get-Content $LinksFile | ForEach-Object {
        $l = $_.Trim()
        if ($l -match "^\[(.+)\]$") { $section = $Matches[1].ToLower() }
        elseif ($l -match "^\s*([^#;\[].*?)\s*=\s*(.+)$") {
            $Links["$section." + $Matches[1].Trim().ToLower()] = $Matches[2].Trim()
        }
    }
}

# --- fungsi download anti-ribet (curl.exe bawaan Windows 10/11, fallback IWR) ---
function Get-File($url, $outFile, $label) {
    Write-Info "Download $label ..."
    $tmp = "$outFile.part"
    $ok = $false
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -L --retry 5 --retry-delay 3 -C - -o $tmp $url 2>$null
        $ok = ($LASTEXITCODE -eq 0) -and (Test-Path $tmp)
    }
    if (-not $ok) {
        Write-Warn2 "curl gagal/tidak ada, coba via PowerShell..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
            $ok = Test-Path $tmp
        } catch { $ok = $false }
    }
    if ($ok) {
        Move-Item -Force $tmp $outFile
        Write-Ok "$label selesai di-download"
        return $true
    }
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    return $false
}

# =============================================================
# LANGKAH 0: cek path & ruang disk
# =============================================================
Write-Head "LANGKAH 0/5 : Cek awal"
if ($KitRoot -match "\s") {
    Write-Warn2 "Lokasi kit ada SPACEnya: $KitRoot"
    Write-Warn2 "Sangat disarankan pindah/pindahin ke path TANPA spasi,"
    Write-Warn2 "contoh: C:\AndroidPortable   (ini penting biar QEMU terpasang mulus)"
}
$drive = Get-PSDrive -Name ($KitRoot.Substring(0,1))
$freeGb = [math]::Round($drive.Free / 1GB, 1)
Write-Info ("Ruang disk kosong: $freeGb GB")
if ($freeGb -lt 10) {
    Write-Err2 "Ruang disk kurang! Butuh minimal 10 GB kosong (ISO 2.4 GB + sistem + data)."
    Read-Host "Tekan ENTER buat keluar"
    exit 1
}

# =============================================================
# LANGKAH 1: QEMU
# =============================================================
Write-Head "LANGKAH 1/5 : QEMU (mesin virtualnya)"
if (Test-Path $QemuExe) {
    Write-Ok "QEMU udah terpasang"
} else {
    $qUrls = @()
    if ($Links.ContainsKey("qemu.primary"))   { $qUrls += $Links["qemu.primary"] }
    if ($Links.ContainsKey("qemu.fallback1")) { $qUrls += $Links["qemu.fallback1"] }
    if ($qUrls.Count -eq 0) { $qUrls += "https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.exe" }

    $installer = Join-Path $env:TEMP "qemu-portable-setup.exe"
    $got = $false
    foreach ($u in $qUrls) {
        if (Get-File $u $installer "QEMU (~197 MB)") { $got = $true; break }
        Write-Warn2 "Coba link berikutnya..."
    }
    if (-not $got) {
        Write-Err2 "Gagal download QEMU. Internet jalan? Atau download manual:"
        Write-Err2 $qUrls[0]
        Write-Err2 "Lalu taruh filenya jadi runtime\qemu-setup.exe dan jalankan lagi."
        Read-Host "Tekan ENTER buat keluar"
        exit 1
    }

    # pasang tanpa install (silent), pakai short path biar aman dari spasi
    New-Item -ItemType Directory -Force -Path $QemuDir | Out-Null
    $short = $QemuDir
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        $short = $fso.GetFolder($QemuDir).ShortPath
    } catch {}
    if ($short -match "\s") {
        Write-Err2 "Path kit ada spasi dan Windows gak kasih short path."
        Write-Err2 "Pindahin kit ke path tanpa spasi (mis. C:\AndroidPortable) lalu ulangi."
        Read-Host "Tekan ENTER buat keluar"
        exit 1
    }
    Write-Info "Pasang QEMU ke $short ..."
    Start-Process -FilePath $installer -ArgumentList "/S", "/D=$short" -Wait
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    if (Test-Path $QemuExe) { Write-Ok "QEMU terpasang (portable, tanpa nge-install ke Windows)" }
    else { Write-Err2 "QEMU gagal terpasang. Cek folder runtime\qemu"; Read-Host "ENTER"; exit 1 }
}

# =============================================================
# LANGKAH 2: ADB (platform-tools)
# =============================================================
Write-Head "LANGKAH 2/5 : ADB (platform-tools dari Google)"
if (Test-Path $AdbExe) {
    Write-Ok "ADB udah ada"
} else {
    $aUrl = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    if ($Links.ContainsKey("adb.primary")) { $aUrl = $Links["adb.primary"] }
    $zip = Join-Path $env:TEMP "platform-tools.zip"
    if (Get-File $aUrl $zip "ADB platform-tools (~6 MB)") {
        New-Item -ItemType Directory -Force -Path $AdbDir | Out-Null
        Expand-Archive -Force -Path $zip -DestinationPath $RuntimeDir
        # hasil extract = runtime\platform-tools\... -> geser isinya ke runtime\adb
        $pt = Join-Path $RuntimeDir "platform-tools"
        if (Test-Path $pt) {
            Get-ChildItem $pt | Move-Item -Destination $AdbDir -Force
            Remove-Item $pt -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        if (Test-Path $AdbExe) { Write-Ok "ADB siap" }
        else { Write-Err2 "adb.exe gak ketemu setelah extract"; Read-Host "ENTER"; exit 1 }
    } else {
        Write-Err2 "Gagal download ADB. Jalankan lagi script ini nanti."
        Read-Host "Tekan ENTER buat keluar"; exit 1
    }
}

# =============================================================
# LANGKAH 3: ISO Android (BlissOS 16 - Android 13 + Play Store)
# =============================================================
Write-Head "LANGKAH 3/5 : ISO Android (BlissOS 16 + Play Store)"
New-Item -ItemType Directory -Force -Path $IsoDir | Out-Null
$iso = Get-ChildItem -Path $IsoDir -Filter *.iso -ErrorAction SilentlyContinue | Select-Object -First 1
if ($iso) {
    Write-Ok ("ISO udah ada: " + $iso.Name)
} else {
    Write-Info "Pilihan:"
    Write-Info "  [1] Download otomatis BlissOS 16 GApps (2.4 GB)  <-- disarankan"
    Write-Info "  [2] Download Android-x86 9 alternatif (lebih ringan ~900 MB)"
    Write-Info "  [3] Manual (aku download sendiri, taruh di folder runtime\android\iso)"
    $choice = Read-Host "Pilih (1/2/3) [1]"
    if (-not $choice) { $choice = "1" }

    $urls = @()
    $dest = Join-Path $IsoDir "Bliss-v16.9.7-x86_64-OFFICIAL-gapps-20241011.iso"
    if ($choice -eq "2") {
        $dest = Join-Path $IsoDir "android-x86_64-9.0-r2.iso"
        if ($Links.ContainsKey("android.fallback1")) { $urls += $Links["android.fallback1"] }
        $urls += "https://downloads.sourceforge.net/project/android-x86/Release%209.0/android-x86_64-9.0-r2.iso"
    } elseif ($choice -eq "3") {
        Start-Process explorer.exe $IsoDir
        Write-Info "Download ISO-nya (link ada di config\links.ini), taruh di:"
        Write-Info "  $IsoDir"
        Read-Host "Tekan ENTER kalau ISO udah ditaruh"
        $iso = Get-ChildItem -Path $IsoDir -Filter *.iso -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $iso) { Write-Err2 "ISO belum ada."; Read-Host "ENTER"; exit 1 }
    } else {
        if ($Links.ContainsKey("android.primary")) { $urls += $Links["android.primary"] }
        $urls += "https://downloads.sourceforge.net/project/blissos-x86/Official/BlissOS16/Gapps/Generic/Bliss-v16.9.7-x86_64-OFFICIAL-gapps-20241011.iso"
    }

    if ($urls.Count -gt 0) {
        $got = $false
        foreach ($u in $urls) {
            if (Get-File $u $dest "ISO Android") { $got = $true; break }
            Write-Warn2 "Coba link berikutnya..."
        }
        if (-not $got) {
            Write-Err2 "Gagal download ISO. Coba lagi atau pilih mode manual (3)."
            Read-Host "Tekan ENTER buat keluar"; exit 1
        }
    }
    $iso = Get-Item $dest
}

$sizeGb = [math]::Round($iso.Length / 1GB, 2)
Write-Info ("Ukuran ISO: $sizeGb GB")
if ($sizeGb -lt 0.6) {
    Write-Err2 "ISO-nya kelihatannya rusak/kurang lengkap (<0.6 GB). Download ulang."
    Read-Host "Tekan ENTER buat keluar"; exit 1
}

# =============================================================
# LANGKAH 4: disk virtual + INSTALL Android ke disk
# =============================================================
Write-Head "LANGKAH 4/5 : Install Android ke disk virtual"
if (Test-Path $BaseDisk) {
    Write-Ok "Disk sistem Android udah ada"
} else {
    Write-Info "Bikin disk virtual 40 GB (file-nya tipis, nggak makan tempat langsung)..."
    & $QemuImg create -f qcow2 $BaseDisk 40G
    if (-not (Test-Path $BaseDisk)) { Write-Err2 "Gagal bikin disk"; Read-Host "ENTER"; exit 1 }

    # deteksi akselerasi
    $accelOut = & $QemuExe -accel help 2>&1 | Out-String
    $hasWhpx = $accelOut -match "whpx"
    $accelArgs = @("-accel", "tcg,thread=multi"); $cpuArg = "max"
    if ($hasWhpx) { $accelArgs = @("-accel", "whpx"); $cpuArg = "host" }
    if (-not $hasWhpx) {
        Write-Warn2 "WHPX (Windows Hypervisor Platform) belum aktif -> install & jalan bakal LAMBAT."
        Write-Warn2 "Sangat disarankan: Start Menu > ketik 'Turn Windows features on or off'"
        Write-Warn2 "> centang 'Windows Hypervisor Platform' > restart PC > jalankan setup lagi."
        $lanjut = Read-Host "Tetap lanjut tanpa WHPX? (y/n) [y]"
        if ($lanjut -eq "n") { exit 1 }
    }

    Write-Info "Membuka mesin virtual buat install Android..."
    Write-Host ""
    Write-Host "  >>> PERHATIAN - IKUTI LANGKAH INI DI JENDELA YANG MUNCUL <<<" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  1. Di menu biru/putih awal, pilih:  'Auto Install to specified harddisk'" -ForegroundColor White
    Write-Host "  2. Kalau ditanya disk, pilih:       sda  (40 GB)" -ForegroundColor White
    Write-Host "  3. Kalau ditanya filesystem, pilih: ext4" -ForegroundColor White
    Write-Host "  4. Jawab YES kalau diminta konfirmasi (data disk bakal dibuat)" -ForegroundColor White
    Write-Host "  5. Tunggu sampai muncul 'Installation is complete'" -ForegroundColor White
    Write-Host "  6. Pilih 'Reboot' / tutup aja jendelanya" -ForegroundColor White
    Write-Host ""
    $proc = Start-Process -FilePath $QemuExe -ArgumentList @(
        $accelArgs, "-cpu", $cpuArg, "-smp", "3", "-m", "3072",
        "-hda", $BaseDisk, "-cdrom", $iso.FullName, "-boot", "d",
        "-vga", "std", "-display", "sdl", "-rtc", "base=localtime"
    ) -PassThru
    Write-Info "Jendela Android udah kebuka. Kerjain di situ, tungguin di sini ya..."
    $proc.WaitForExit()
    Write-Ok "Jendela install ketutup."

    $sizeDisk = (Get-Item $BaseDisk).Length
    if ($sizeDisk -lt 800MB) {
        Write-Warn2 "Disk sistem masih kecil ($([math]::Round($sizeDisk/1MB,0)) MB)."
        Write-Warn2 "Kayaknya proses install belum selesai bener."
        $ulang = Read-Host "Ulangi langkah install? (y/n) [y]"
        if ($ulang -ne "n") {
            Remove-Item $BaseDisk -Force
            & $QemuImg create -f qcow2 $BaseDisk 40G
            $proc = Start-Process -FilePath $QemuExe -ArgumentList @(
                $accelArgs, "-cpu", $cpuArg, "-smp", "3", "-m", "3072",
                "-hda", $BaseDisk, "-cdrom", $iso.FullName, "-boot", "d",
                "-vga", "std", "-display", "sdl", "-rtc", "base=localtime"
            ) -PassThru
            Write-Info "Ulangi langkah install di jendela yang muncul ya..."
            $proc.WaitForExit()
        }
    }
    Write-Ok "Sistem Android terpasang di disk virtual."
}

# =============================================================
# LANGKAH 5: profile pertama + config
# =============================================================
Write-Head "LANGKAH 5/5 : Bikin profile pertama ('Utama')"
$utama = Join-Path $ProfilesDir "Utama"
if (-not (Test-Path (Join-Path $utama "overlay.qcow2"))) {
    New-Item -ItemType Directory -Force -Path $utama | Out-Null
    & $QemuImg create -f qcow2 -b $BaseDisk -F qcow2 (Join-Path $utama "overlay.qcow2")
    Write-Ok "Profile 'Utama' dibuat"
} else {
    Write-Ok "Profile 'Utama' udah ada"
}

if (-not (Test-Path $ConfigFile)) {
    $totalMb = 8192
    try {
        $cs = Get-CimInstance Win32_ComputerSystem
        $totalMb = [int]($cs.TotalPhysicalMemory / 1MB)
    } catch {}
    $ram = [math]::Max(2048, [math]::Min(4096, [int]($totalMb * 0.35)))
    $cpuN = [Environment]::ProcessorCount
    $cores = [math]::Max(2, [math]::Min(4, $cpuN - 2))
    $cfg = @{ ram_mb = $ram; cpu_cores = $cores; accel = "auto";
              vga = "std"; display = "sdl"; machine = "pc";
              base_port = 5555; adb_wait_seconds = 180 }
    $cfg | ConvertTo-Json | Set-Content -Path $ConfigFile -Encoding UTF8
    Write-Ok ("config.json dibuat (RAM Android: $ram MB, Core: $cores)")
}

New-Item -ItemType File -Force -Path $ReadyFlag | Out-Null

Write-Host ""
Write-Host "###############################################" -ForegroundColor Green
Write-Host "#   SETUP SELESAI! LANGKAH SELANJUTNYA:       #" -ForegroundColor Green
Write-Host "###############################################" -ForegroundColor Green
Write-Host ""
Write-Host "  1. (Sekali aja) Bangun launcher .exe: jalankan launcher\BUILD-EXE.bat"
Write-Host "  2. Buka launcher: jalankan 2-Buka-Launcher.bat"
Write-Host "  3. Klik JALANKAN ANDROID -> login akun Google"
Write-Host "  4. Install TikTok / Instagram / SnackVideo / YouTube dari Play Store"
Write-Host "  5. Klik 'KIRIM VIDEO DARI PC' buat masukin video yang mau diupload"
Write-Host ""
Write-Host "  Baca PANDUAN-LENGKAP.txt buat detail + troubleshooting."
Write-Host ""
Read-Host "Tekan ENTER buat nutup"
