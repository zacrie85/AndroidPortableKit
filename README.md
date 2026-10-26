# Android Portable Kit

Jalankan **Android di Windows secara portable** — nonton & upload video
TikTok, Instagram, SnackVideo, YouTube, dan app Android lainnya. **Tanpa
install apapun**, semua data ada di satu folder yang bisa dibawa pindah PC
atau flashdisk.

## Download

Ambil dari halaman **[Releases](https://github.com/zacrie85/AndroidPortableKit/releases/latest)**:

| File | Kegunaan |
|------|----------|
| `AndroidPortableKit.zip` | **Program utama** (exe launcher + setup + panduan) |
| `Runtime-QEMU-ADB.zip` | Mesin virtual QEMU + ADB (buat mode offline) |
| `BlissOS16-GApps.iso.part01`, `part02`, ... | ISO Android 13 + Play Store (mode offline, install sekali klik di VM) |
| `AndroidPortable.exe` | Launcher saja (untuk update manual / pemakai lanjutan) |

> Mode offline: download SEMUA file ke satu folder, extract `AndroidPortableKit.zip`,
> pindahkan file lain ke dalamnya, lalu jalankan `1-Siapkan-Offline.bat` —
> **tanpa internet sama sekali**.

## Mulai Cepat (Offline)

1. Download semua file release ke satu folder
2. Extract `AndroidPortableKit.zip` → masukkan file lain ke folder hasilnya
3. Jalankan `1-Siapkan-Offline.bat` (merakit semua, tanpa internet)
4. Jalankan `2-Buka-Launcher.bat` → klik **JALANKAN ANDROID**
5. Login Google → install TikTok/IG/SnackVideo/YT dari Play Store
6. Klik **KIRIM VIDEO DARI PC** → pilih video → upload + tulis caption di app

Baca `MULAI-DISINI.txt` dan `PANDUAN-LENGKAP.txt` di dalam paket untuk detail & troubleshooting.

## Fitur

- **Full portable** — tanpa install, tanpa driver, bisa dibawa flashdisk
- **Nonton & upload** — TikTok, Instagram, SnackVideo, YouTube (caption & judul ditulis di app)
- **Kirim video dari PC** — via file picker Windows Explorer, video muncul di galeri Android otomatis
- **Multi-akun (clone)** — 1 profile = 1 akun; jalankan beberapa profile bersamaan
- **Auto-update** — cek versi baru otomatis setelah app terbuka; update via 1 klik
- **Auto-fit** — deteksi RAM/CPU/akselerasi (WHPX/TCG) lalu pilih setting optimal sendiri
- **Bahasa Indonesia** — semua tombol, log, dan panduan

## Cara Kerja Singkat

```
Launcher (.exe) ──► QEMU portable ──► Android (BlissOS 16, Android 13 + Play Store)
      │                                     ▲
      └── ADB (kirim video / install APK) ──┘
```

- Sistem Android dipasang sekali ke disk virtual (`qcow2`)
- Tiap akun/profile = disk *overlay* sendiri (hemat ruang, bisa jalan paralel)
- Update launcher: app kebuka → cek release GitHub → download & ganti exe otomatis

## Build Dari Source (Opsional)

```bash
cd launcher
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --name AndroidPortable ap_gui.py
```

Atau cukup jalankan `launcher\BUILD-EXE.bat` di Windows.

Push tag baru (`git tag v1.0.1 && git push origin v1.0.1`) → GitHub Actions
otomatis build exe + bikin Release baru → semua user lama dapat notifikasi
update di launcher.

## Lisensi & Catatan

- Kode launcher: bebas dipakai & dimodifikasi
- Sistem Android: [BlissOS](https://blissos.org) / Android-x86 (open source, ISO diunduh dari sumber resmi)
- Patuhi ToS masing-masing app; jangan dipakai untuk spam multi-akun
