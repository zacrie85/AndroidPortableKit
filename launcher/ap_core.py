# -*- coding: utf-8 -*-
"""
ap_core.py — Mesin di balik Android Portable Kit
================================================
Modul ini yang ngurusin semua hal "berat":
  - Deteksi spek PC (RAM, CPU, akselerasi virtualisasi WHPX/TCG)
  - Manajemen profile (instance Android, satu profile = satu akun)
  - Menjalankan / mematikan Android lewat QEMU portable
  - Kirim video dari PC ke Android (via ADB push + media scan)
  - Install APK via ADB

Dipakai oleh ap_gui.py (tampilan GUI-nya).
100% Python standar (tanpa library tambahan).
"""

import os
import sys
import re
import json
import socket
import shutil
import subprocess
import threading
import time
import queue
from pathlib import Path

IS_WINDOWS = (os.name == "nt")
if IS_WINDOWS:
    import ctypes  # noqa: F401 (dipakai untuk deteksi RAM)

APP_NAME = "Android Portable Kit"
APP_VERSION = "1.1.9"

# Repo GitHub tempat cek update otomatis (owner/repo)
UPDATE_REPO = "zacrie85/AndroidPortableKit"
# Nama file exe di aset rilis GitHub
UPDATE_ASSET_NAME = "AndroidPortable.exe"


# ---------------------------------------------------------------------------
# PATH DASAR (bisa jalan dari .exe hasil PyInstaller atau dari source .py)
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    """Folder utama kit. Kalau di-freeze jadi .exe, exe harus ditaruh di root kit."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Kalau jalan dari source: file ini ada di <root kit>/launcher/ap_core.py
    return Path(__file__).resolve().parent.parent


class Paths:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self.config_dir = app_dir / "config"
        self.launcher_dir = app_dir / "launcher"
        self.runtime_dir = app_dir / "runtime"
        self.qemu_dir = self.runtime_dir / "qemu"
        self.adb_dir = self.runtime_dir / "adb"
        self.android_dir = self.runtime_dir / "android"
        self.iso_dir = self.android_dir / "iso"
        self.base_disk = self.android_dir / "base-system.qcow2"
        self.profiles_dir = app_dir / "data" / "profiles"
        self.shared_video_dir = app_dir / "data" / "Video-Buat-Upload"
        self.config_file = self.config_dir / "config.json"
        self.links_file = self.config_dir / "links.ini"
        self.ready_flag = self.android_dir / "READY.flag"

    # --- executable ---
    def qemu_exe(self) -> Path:
        return self.qemu_dir / "qemu-system-x86_64.exe"

    def qemu_img_exe(self) -> Path:
        return self.qemu_dir / "qemu-img.exe"

    def adb_exe(self) -> Path:
        return self.adb_dir / "adb.exe"

    def find_iso(self) -> Path | None:
        """Cari file ISO Android di folder iso/ (nama bebas)."""
        if not self.iso_dir.exists():
            return None
        isos = sorted(self.iso_dir.glob("*.iso"))
        return isos[0] if isos else None


# ---------------------------------------------------------------------------
# CONFIG (config.json) + AUTO-FIT SPEK PC
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "ram_mb": 3072,          # RAM buat tiap instance Android (MB)
    "cpu_cores": 3,          # jumlah core CPU buat Android
    "accel": "auto",         # auto | whpx | tcg
    "vga": "std",            # kartu grafis virtual: std | virtio
    "display": "sdl",        # cara nampilin jendela: sdl | gtk
    "machine": "pc",         # mesin virtual: pc | q35
    "base_port": 5555,       # port ADB instance pertama
    "adb_wait_seconds": 180  # batas waktu nunggu ADB siap (detik)
}


def load_config(p: Paths) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if p.config_file.exists():
        try:
            cfg.update(json.loads(p.config_file.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(p: Paths, cfg: dict) -> None:
    p.config_dir.mkdir(parents=True, exist_ok=True)
    p.config_file.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def _total_ram_mb() -> int:
    if IS_WINDOWS:
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys // (1024 * 1024))
        except Exception:
            pass
    try:
        import subprocess as sp
        out = sp.run(["free", "--mega"], capture_output=True, text=True).stdout
        line = out.splitlines()[1].split()
        return int(line[1])
    except Exception:
        return 8192  # asumsi 8 GB kalau gagal deteksi


def autodetect_config(p: Paths) -> dict:
    """Auto-fit: pilih RAM & core sesuai spek PC (dijalankan sekali di awal)."""
    cfg = load_config(p)
    total = _total_ram_mb()
    cpu_n = os.cpu_count() or 4

    # RAM buat Android: ~35% total RAM, dibatasi 2048 - 4096 MB
    ram = int(total * 0.35)
    ram = max(2048, min(4096, ram))
    # Core buat Android: sisain 2 core buat Windows
    cores = max(2, min(4, cpu_n - 2))

    cfg["ram_mb"] = ram
    cfg["cpu_cores"] = cores
    save_config(p, cfg)
    return cfg


# ---------------------------------------------------------------------------
# PARSER links.ini (daftar URL download) — simpel, tanpa library
# ---------------------------------------------------------------------------

def read_links(p: Paths) -> dict:
    links = {}
    if not p.links_file.exists():
        return links
    section = ""
    for raw in p.links_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            links[f"{section}.{k.strip().lower()}"] = v.strip()
    return links


# ---------------------------------------------------------------------------
# MESIN UTAMA
# ---------------------------------------------------------------------------

class Instance:
    """Satu Android yang lagi jalan."""
    def __init__(self, profile: str, proc: subprocess.Popen, port: int, ram_mb: int):
        self.profile = profile
        self.proc = proc
        self.port = port
        self.ram_mb = ram_mb
        self.adb_ready = False

    @property
    def serial(self) -> str:
        return f"127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc.poll() is None


class AndroidEngine:
    def __init__(self):
        self.p = Paths(get_app_dir())
        self.cfg = load_config(self.p)
        self.instances: dict[str, Instance] = {}
        self.lock = threading.RLock()
        self._log_q: queue.Queue = queue.Queue()
        self._accel_cache = None  # (nama_accel, cpu_model) atau ("none","")

    # ---------------- log ----------------
    def log(self, msg: str):
        self._log_q.put(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def poll_log(self) -> list[str]:
        out = []
        try:
            while True:
                out.append(self._log_q.get_nowait())
        except queue.Empty:
            pass
        return out

    # ---------------- helper subprocess ----------------
    def _run(self, cmd: list, timeout: int = 60, cwd: Path | None = None):
        """Jalankan perintah, balikin (returncode, stdout). Tanpa jendela console."""
        flags = 0
        if IS_WINDOWS:
            flags = subprocess.CREATE_NO_WINDOW
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=str(cwd) if cwd else None,
                creationflags=flags, errors="replace")
            return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
        except FileNotFoundError:
            return 127, "Program tidak ditemukan: " + " ".join(cmd[:2])
        except subprocess.TimeoutExpired:
            return 124, "Timeout: " + " ".join(cmd[:2])
        except Exception as e:
            return 1, f"Error: {e}"

    # ---------------- cek kesiapan ----------------
    # Disk yang benar2 terpasang BlissOS itu >= 1 GB.
    # Disk kosong (qemu-img create doang) ukurannya cuma ~200 KB.
    # Dipakai buat mendeteksi "disk ada tapi Android belum ter-install"
    # biar gak muncul "No bootable device" di jendela Android.
    BASE_DISK_MIN_BYTES = 800 * 1024 * 1024

    def check_setup(self) -> dict:
        p = self.p
        qemu_ok = p.qemu_exe().exists()
        adb_ok = p.adb_exe().exists()
        base_ok = False
        if p.base_disk.exists():
            try:
                base_ok = p.base_disk.stat().st_size >= self.BASE_DISK_MIN_BYTES
            except OSError:
                base_ok = False
        iso = p.find_iso()
        status = {
            "qemu_ok": qemu_ok,
            "adb_ok": adb_ok,
            "base_ok": base_ok,
            "iso_name": iso.name if iso else None,
            "ready": qemu_ok and adb_ok and base_ok,
        }
        return status

    # ---------------- deteksi akselerasi ----------------
    def detect_accel(self) -> tuple[str, str]:
        """Balikin (accel, cpu_model). whpx = cepat (pake virtualisasi CPU),
        tcg = lambat (emulasi software)."""
        if self._accel_cache is not None:
            return self._accel_cache
        choice = self.cfg.get("accel", "auto")
        if not self.p.qemu_exe().exists():
            self._accel_cache = ("none", "max")
            return self._accel_cache
        if choice == "whpx":
            self._accel_cache = ("whpx", "host")
            return self._accel_cache
        rc, out = self._run([str(self.p.qemu_exe()), "-accel", "help"], timeout=30)
        has_whpx = "whpx" in (out or "").lower()
        if has_whpx and choice != "tcg":
            self._accel_cache = ("whpx", "host")
        else:
            self._accel_cache = ("tcg", "max")
        return self._accel_cache

    # ---------------- profile ----------------
    def list_profiles(self) -> list[str]:
        p = self.p
        out = []
        if p.profiles_dir.exists():
            for d in sorted(p.profiles_dir.iterdir()):
                if d.is_dir() and (d / "overlay.qcow2").exists():
                    out.append(d.name)
        return out

    def create_profile(self, name: str) -> str:
        """Bikin profile baru = disk 'overlay' di atas sistem dasar ( hemat tempat,
        semua data & app akun ini nyimpen di overlay-nya sendiri)."""
        p = self.p
        name = "".join(c for c in name.strip() if c.isalnum() or c in "-_ ()")
        name = name or f"Akun-{int(time.time()) % 10000}"
        if not p.base_disk.exists():
            raise RuntimeError("Sistem Android dasar belum ada. Jalankan setup awal dulu.")
        prof_dir = p.profiles_dir / name
        overlay = prof_dir / "overlay.qcow2"
        if overlay.exists():
            raise RuntimeError(f"Profile '{name}' udah ada.")
        prof_dir.mkdir(parents=True, exist_ok=True)
        rc, out = self._run([
            str(p.qemu_img_exe()), "create", "-f", "qcow2",
            "-b", str(p.base_disk), "-F", "qcow2", str(overlay)], timeout=120)
        if rc != 0 or not overlay.exists():
            raise RuntimeError("Gagal bikin profile: " + out.strip()[:400])
        self.log(f"Profile baru dibuat: {name}")
        return name

    def delete_profile(self, name: str) -> None:
        if name in self.instances:
            raise RuntimeError("Matikan dulu instance yang lagi jalan.")
        prof_dir = self.p.profiles_dir / name
        if prof_dir.exists():
            shutil.rmtree(prof_dir, ignore_errors=True)
            self.log(f"Profile dihapus: {name}")

    def _fix_backing_path(self, overlay: Path) -> None:
        """Kalau folder kit dipindah (misal pindah flashdisk), rujukan ke disk dasar
        di dalem overlay jadi basi -> kita benerin otomatis."""
        rc, out = self._run([
            str(self.p.qemu_img_exe()), "info", "--output=json", str(overlay)],
            timeout=60)
        if rc != 0:
            return
        try:
            info = json.loads(out)
            backing = info.get("backing-filename")
            if backing and not Path(backing).exists() and self.p.base_disk.exists():
                self._run([
                    str(self.p.qemu_img_exe()), "rebase", "-u",
                    "-b", str(self.p.base_disk), "-F", "qcow2", str(overlay)],
                    timeout=120)
                self.log("Path disk dasar diperbaiki otomatis (mode portable).")
        except Exception:
            pass

    # ---------------- port kosong ----------------
    def _free_port(self) -> int:
        base = int(self.cfg.get("base_port", 5555))
        used = {i.port for i in self.instances.values()}
        port = base
        while port in used or port > base + 50:
            port += 1
        # cek juga port beneran kosong di Windows
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return self._free_port_retry(port + 1, used)
        return port

    def _free_port_retry(self, port: int, used: set) -> int:
        while port < 5700:
            if port not in used:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("127.0.0.1", port))
                        return port
                    except OSError:
                        pass
            port += 1
        return port

    # ---------------- jalanin / matiin Android ----------------
    def _build_qemu_cmd(self, overlay: Path, port: int, ram_mb: int, tag: str,
                        accel_mode: str | None = None,
                        with_audio: bool = True) -> list:
        p = self.p
        accel, cpu_model = self.detect_accel()
        if accel_mode:
            accel = accel_mode
        # '-cpu host' cuma valid di whpx/kvm; di tcg harus dipakai 'max'
        if accel != "whpx" and cpu_model == "host":
            cpu_model = "max"
        cmd = [str(p.qemu_exe()), "-name", f"AndroidPortable-{tag}"]
        if accel == "whpx":
            cmd += ["-accel", "whpx"]
        else:
            cmd += ["-accel", "tcg,thread=multi"]
        cmd += [
            "-cpu", cpu_model,
            "-smp", str(self.cfg.get("cpu_cores", 3)),
            "-m", str(ram_mb),
            "-hda", str(overlay),
            "-vga", self.cfg.get("vga", "std"),
            "-display", self.cfg.get("display", "sdl"),
            "-machine", self.cfg.get("machine", "pc"),
            "-netdev", f"user,id=n0,hostfwd=tcp:127.0.0.1:{port}-:5555",
            "-device", "e1000,netdev=n0",
            "-rtc", "base=localtime",
        ]
        if with_audio:
            cmd += ["-audiodev", "wasapi,id=snd0",
                    "-device", "intel-hda",
                    "-device", "hda-duplex,audiodev=snd0"]
        return cmd

    # ---------------- log QEMU ke file ----------------
    def _qemu_log_path(self, profile: str) -> Path:
        d = self.p.app_dir / "data" / "logs"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return d / f"qemu-{profile}.log"

    @staticmethod
    def _read_log_tail(path: Path, max_chars: int = 900) -> str:
        try:
            txt = path.read_bytes().decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
        return txt[-max_chars:] if len(txt) > max_chars else txt

    @staticmethod
    def _short_qemu_err(tail: str) -> str:
        t = (tail or "").lower()
        if "whpx" in t:
            return "WHPX (virtualisasi Windows) gak aktif di PC ini"
        if "gtk" in t and ("could not" in t or "failed" in t):
            return "Display GTK gak tersedia"
        if "sdl" in t and ("could not" in t or "failed" in t):
            return "Display SDL gagal"
        if "could not open" in t or "cannot open" in t:
            return "Ada file (disk/BIOS) yang gak ketemu"
        lines = [l.strip() for l in (tail or "").splitlines() if l.strip()]
        return lines[-1][:120] if lines else "QEMU keluar tanpa pesan error"

    def start_instance(self, profile: str) -> Instance:
        """Nyalain Android. Raise RuntimeError kalau ada masalah."""
        with self.lock:
            if profile in self.instances and self.instances[profile].alive():
                raise RuntimeError(f"Profile '{profile}' lagi jalan.")
            p = self.p
            st = self.check_setup()
            if not st["ready"]:
                if st["qemu_ok"] and st["adb_ok"] and not st["base_ok"]:
                    raise RuntimeError(
                        "Disk sistem Android-nya kosong (Android belum "
                        "ter-install di dalamnya).\nJalankan '1-Setup-Awal.bat' "
                        "-> dia bakal mendeteksi disk kosong dan nawarin "
                        "install ulang. Kalau ditanya, jawab y lalu ikuti "
                        "langkah install sampai 'Installation is complete'.")
                raise RuntimeError(
                    "Setup belum lengkap (butuh QEMU, ADB, dan sistem Android). "
                    "Jalankan '1-Setup-Awal.bat' dulu.")
            overlay = p.profiles_dir / profile / "overlay.qcow2"
            if not overlay.exists():
                raise RuntimeError(f"Profile '{profile}' gak ditemukan.")
            self._fix_backing_path(overlay)

            # instance kedua & seterusnya dikasih RAM lebih kecil biar gak tabrakan
            n_running = len([i for i in self.instances.values() if i.alive()])
            ram = int(self.cfg.get("ram_mb", 3072))
            if n_running > 0:
                ram = min(ram, 2048)
            port = self._free_port()

            # daftar percobaan: (mode akselerasi, pakai audio)
            # urutan: mode terpilih (whpx/tcg) dengan & tanpa audio,
            # lalu kalau whpx gagal -> coba tcg juga (WHPX sering gak aktif
            # di Windows walau QEMU-nya dukung)
            first_accel, _ = self.detect_accel()
            accels = [first_accel]
            if first_accel == "whpx":
                accels.append("tcg")
            attempts = [(a, True) for a in accels] + [(a, False) for a in accels]

            log_path = self._qemu_log_path(profile)
            flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
            self.log(f"Nyalain Android '{profile}' (RAM {ram} MB, port {port})...")

            proc = None
            used_accel = None
            last_tail = ""
            for i, (acc, with_audio) in enumerate(attempts):
                cmd = self._build_qemu_cmd(
                    overlay, port, ram, profile,
                    accel_mode=acc, with_audio=with_audio)
                try:
                    lf = open(log_path, "wb", buffering=0)
                    try:
                        proc = subprocess.Popen(
                            cmd, cwd=str(p.app_dir), stdin=subprocess.DEVNULL,
                            stdout=lf, stderr=subprocess.STDOUT,
                            creationflags=flags)
                    finally:
                        # QEMU udah dapat handle filenya sendiri,
                        # nutup punya Python gak ngaruh
                        lf.close()
                except Exception as e:
                    raise RuntimeError(f"Gagal membuka QEMU: {e}")
                time.sleep(3.0)
                if proc.poll() is None:
                    used_accel = acc
                    break
                last_tail = self._read_log_tail(log_path)
                self.log(f"Percobaan {i + 1}/{len(attempts)} gagal: "
                         f"{self._short_qemu_err(last_tail)}")
                proc = None

            if proc is None:
                raise RuntimeError(
                    "QEMU gagal nyala setelah semua percobaan.\n\n"
                    "Alasan terakhir dari QEMU:\n"
                    + (last_tail or "(gak ada output)")
                    + "\n\nDetail lengkap ada di: " + str(log_path))

            if first_accel == "whpx" and used_accel == "tcg":
                self.log("WHPX (virtualisasi Windows) gak aktif di PC ini — "
                         "otomatis pindah ke mode TCG (emulasi software, "
                         "lebih lambat tapi pasti jalan).")
                self.log("Biar jauh lebih kenceng: aktifkan 'Windows Hypervisor "
                         "Platform' di Windows Features + Virtualization di BIOS, "
                         "lalu ubah 'accel' di config/config.json jadi 'auto' lagi.")
                try:
                    self.cfg["accel"] = "tcg"
                    save_config(self.p, self.cfg)
                except Exception:
                    pass

            inst = Instance(profile, proc, port, ram)
            self.instances[profile] = inst
            self.log(f"Android '{profile}' udah nyala. Menunggu sistem siap...")
            return inst

    def wait_adb_ready(self, inst: Instance, timeout: int | None = None,
                       progress=None) -> bool:
        """Nunggu Android beneran hidup & ADB bisa nyambung."""
        adb = self.p.adb_exe()
        if not adb.exists():
            return False
        timeout = timeout or int(self.cfg.get("adb_wait_seconds", 180))
        deadline = time.time() + timeout
        time.sleep(12)  # kasih waktu bootloader
        while time.time() < deadline:
            if not inst.alive():
                return False
            self._run([str(adb), "connect", inst.serial], timeout=15)
            rc, out = self._run([str(adb), "-s", inst.serial, "get-state"], timeout=15)
            if rc == 0 and "device" in (out or ""):
                inst.adb_ready = True
                self.log(f"ADB siap di {inst.serial} — Android '{inst.profile}' "
                         f"siap dipakai!")
                return True
            if progress:
                progress(int((time.time() - (deadline - timeout)) / timeout * 100))
            time.sleep(5)
        self.log("ADB belum nyambung. Kalau Android udah kelihatan layarnya, "
                 "aktifkan 'ADB over Network' di Settings Android (Developer options).")
        return False

    def stop_instance(self, profile: str) -> None:
        with self.lock:
            inst = self.instances.get(profile)
            if not inst:
                return
            adb = self.p.adb_exe()
            self.log(f"Matikan Android '{profile}'...")
            if inst.alive():
                if adb.exists():
                    self._run([str(adb), "-s", inst.serial, "shell", "reboot", "-p"],
                              timeout=20)
                    for _ in range(25):
                        if not inst.alive():
                            break
                        time.sleep(1)
                if inst.alive():
                    try:
                        inst.proc.terminate()
                        inst.proc.wait(timeout=15)
                    except Exception:
                        try:
                            inst.proc.kill()
                        except Exception:
                            pass
            self.instances.pop(profile, None)
            self.log(f"Android '{profile}' udah mati.")

    def stop_all(self) -> None:
        for name in list(self.instances.keys()):
            try:
                self.stop_instance(name)
            except Exception as e:
                self.log(f"Gagal matiin {name}: {e}")

    # ---------------- ADB: kirim video & install APK ----------------
    def _adb(self, args: list, timeout: int = 300) -> tuple[int, str]:
        adb = str(self.p.adb_exe())
        return self._run([adb] + args, timeout=timeout)

    def push_videos(self, inst: Instance, files: list[str]) -> list[tuple[str, str]]:
        """Kirim video dari PC ke storage Android supaya bisa dipilih waktu upload."""
        results = []
        if not inst.adb_ready:
            ok = self.wait_adb_ready(inst, timeout=90)
            if not ok:
                return [(f, "GAGAL — ADB belum nyambung ke Android")] * len(files)
        remote_dir = "/sdcard/Movies/AndroidPortable"
        self._adb(["-s", inst.serial, "shell", "mkdir", "-p", remote_dir])
        for f in files:
            name = Path(f).name
            self.log(f"Kirim '{name}' ke Android '{inst.profile}'...")
            rc, out = self._adb(["-s", inst.serial, "push", f, remote_dir + "/"],
                                timeout=1800)
            if rc != 0:
                results.append((name, "GAGAL: " + (out or "").strip()[:200]))
                continue
            # refresh galeri/media store biar videonya kelihatan di TikTok dll.
            ok_scan = False
            rc1, _ = self._adb(["-s", inst.serial, "shell", "content", "call",
                                "--uri", "content://media", "--method", "scan_volume",
                                "--arg", "external_primary"], timeout=60)
            ok_scan = ok_scan or (rc1 == 0)
            rc2, _ = self._adb(["-s", inst.serial, "shell", "am", "broadcast",
                                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                                "-d", f"file://{remote_dir}/{name}"], timeout=60)
            ok_scan = ok_scan or (rc2 == 0)
            results.append((name, "OK" + ("" if ok_scan else
                            " (kirim sukses; kalau belum muncul di galeri, "
                            "cari di app Files > Movies/AndroidPortable)")))
            self.log(f"'{name}' udah sampe ke Android.")
        return results

    def install_apk(self, inst: Instance, apk_path: str) -> str:
        if not inst.adb_ready:
            ok = self.wait_adb_ready(inst, timeout=90)
            if not ok:
                return "GAGAL — ADB belum nyambung ke Android"
        self.log(f"Install APK '{Path(apk_path).name}' ke '{inst.profile}'...")
        rc, out = self._adb(["-s", inst.serial, "install", "-r", "-g", apk_path],
                            timeout=900)
        out = (out or "").strip()
        if rc == 0 and "Success" in out:
            self.log("Install APK sukses.")
            return "OK — APK terpasang"
        return "GAGAL: " + out[:300]

    # ---------------- buka folder ----------------
    def open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            self._run(["xdg-open", str(path)])


# ---------------------------------------------------------------------------
# AUTO-UPDATE (dicek SETELAH launcher kebuka, di background, gak ngganggu)
# ---------------------------------------------------------------------------

def _ver_tuple(v: str) -> tuple:
    """'v1.2.3' / '1.2' -> (1, 2, 3) biar bisa dibandingkan."""
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def check_update() -> dict | None:
    """Cek release terbaru di GitHub.
    Balikin info update kalau ada versi LEBIH BARU, kalau tidak None.
    Apa pun yang gagal (offline, rate limit, dsb.) balikin None — diam aja,
    jangan ganggu user yang lagi asik nonton TikTok :)."""
    import urllib.request
    repo = UPDATE_REPO
    try:
        cfg = load_config(Paths(get_app_dir()))
        repo = cfg.get("update_repo", UPDATE_REPO)
    except Exception:
        pass
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AndroidPortableKit-Updater",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
            return None
        asset_url = None
        size = 0
        for a in data.get("assets", []):
            if a.get("name") == UPDATE_ASSET_NAME:
                asset_url = a.get("browser_download_url")
                size = int(a.get("size", 0))
                break
        if not asset_url:
            return None
        return {"version": tag, "url": asset_url, "size": size,
                "notes": (data.get("body") or "")[:500]}
    except Exception:
        return None


def download_update(url: str, dest: Path, progress=None) -> bool:
    """Download exe baru ke dest (AndroidPortable.exe.new). True kalau sukses."""
    import urllib.request
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "AndroidPortableKit-Updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length", 0) or 0)
            done = 0
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        return dest.exists() and dest.stat().st_size > 1000000
    except Exception:
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return False


def make_update_bat(app_dir: Path) -> Path:
    """Bat kecil: nunggu launcher nutup (2 detik), ganti exe lama dengan yang
    baru, terus menghapus dirinya sendiri."""
    bat = app_dir / "Update-Launcher.bat"
    bat.write_text(
        '@echo off\r\n'
        'cd /d "%~dp0"\r\n'
        'echo Memperbarui launcher, jangan tutup jendela ini...\r\n'
        'timeout /t 2 /nobreak >nul\r\n'
        'del /f /q "AndroidPortable.exe"\r\n'
        'ren "AndroidPortable.exe.new" "AndroidPortable.exe"\r\n'
        '(goto) 2>nul & del /f /q "%~f0"\r\n', encoding="ascii")
    return bat


# ---------------------------------------------------------------------------
# INFO SPEK (buat ditampilkan di GUI)
# ---------------------------------------------------------------------------

def specs_summary(engine: AndroidEngine) -> str:
    accel, _ = engine.detect_accel()
    accel_txt = {"whpx": "WHPX (cepat)", "tcg": "TCG (lambat)",
                 "none": "belum ada"}[accel]
    total_gb = _total_ram_mb() / 1024
    return (f"RAM PC: {total_gb:.0f} GB | Alokasi Android: "
            f"{engine.cfg.get('ram_mb')} MB | Akselerasi: {accel_txt}")
