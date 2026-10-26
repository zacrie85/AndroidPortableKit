#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autoinstall.py — Pasang Android-x86/BlissOS ke disk virtual SECARA OTOMATIS
(headless, di GitHub Actions windows runner).

Dua mode:
  parse-grub : ekstrak kernel/initrd/grub.cfg dari ISO, cari entry
               "Auto Install to specified harddisk" dan ambil param boot-nya.
  run        : boot installer via -kernel/-initrd (skip menu GRUB), lalu
               kirim tombol Enter berkala via monitor HMP sampai installer
               selesai (qemu keluar karena -no-reboot).

Sukses = qemu keluar dengan sendirinya (install selesai -> reboot -> -no-reboot).
Semua screendump disimpan ke --outdir buat diagnosis kalau gagal.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path


def log(msg):
    print(f"[autoinstall {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# MODE parse-grub
# ---------------------------------------------------------------------------

def _7z_extract(seven, iso: Path, iso_path: str, outdir: Path) -> bool:
    """Ekstrak satu file dari ISO (7z 'e' = rata, nama akhirnya dipakai)."""
    r = subprocess.run([seven, "e", str(iso), f"-o{outdir}",
                        iso_path.lstrip("/"), "-y"],
                       capture_output=True, text=True)
    name = Path(iso_path).name
    return (outdir / name).exists()


def _try_isolinux(outdir: Path) -> tuple[str, str, str] | None:
    """Parse isolinux.cfg: cari label auto_install -> (kernel, initrd, append).
    Contoh append: initrd=/initrd.img root=/dev/ram0 AUTO_INSTALL=0 DEBUG= noexec=off
    """
    cfg = outdir / "isolinux.cfg"
    if not cfg.exists():
        return None
    text = cfg.read_text(encoding="utf-8", errors="replace")
    labels = re.split(r"(?m)^(?=label\s)", text)
    for block in labels:
        if not re.match(r"label\s", block):
            continue
        m_label = re.search(r"label\s+(\S+)", block)
        m_menu = re.search(r"menu label\s+(.+)", block)
        name = (m_label.group(1) if m_label else "") + " " + \
               (m_menu.group(1) if m_menu else "")
        if "auto_install" not in name.lower() and "auto install" not in name.lower():
            continue
        m_kernel = re.search(r"^\s*kernel\s+(\S+)", block, re.M)
        m_append = re.search(r"^\s*append\s+(.+)$", block, re.M)
        if not m_kernel or not m_append:
            continue
        kernel = m_kernel.group(1)
        append_full = " ".join(m_append.group(1).split())
        # append berbentuk: initrd=/initrd.img root=/dev/ram0 AUTO_INSTALL=0 ...
        m_initrd = re.search(r"(?:^|\s)initrd=(\S+)", append_full)
        initrd = m_initrd.group(1) if m_initrd else "/initrd.img"
        append = re.sub(r"(?:^|\s)initrd=\S+", "", append_full).strip()
        log(f"isolinux.cfg: label '{name.strip()}'")
        log(f"  kernel={kernel} initrd={initrd}")
        log(f"  append={append}")
        return kernel, initrd, append
    return None


def _try_grub_chain(outdir: Path) -> tuple[str, str, str] | None:
    """Fallback: parse rantai grub.cfg -> source -> android.cfg (grub dinamis).
    Cari add_entry "...Auto Install..." lalu gabungkan dengan template linux."""
    combined = ""
    for cand in ("grub.cfg", "android.cfg"):
        f = outdir / cand
        if f.exists():
            combined += f.read_text(encoding="utf-8", errors="replace") + "\n"

    m_add = re.search(
        r'add_entry\s+"([^"]*[Aa]uto\s+[Ii]nstall[^"]*)"\s+(.+)', combined)
    m_tpl = re.search(r"^\s*linux\s+(\S+)\s+(.+)$", combined, re.M)
    m_initrd = re.search(r"^\s*initrd\s+(\S+)", combined, re.M)
    if not (m_add and m_tpl and m_initrd):
        return None
    extra = " ".join(m_add.group(2).split())
    kernel_tpl, args_tpl = m_tpl.group(1), m_tpl.group(2)
    kernel = kernel_tpl.replace("$kd/", "/").replace("$kdir/", "/")
    initrd = m_initrd.group(1).replace("$kd/", "/").replace("$kdir/", "/")
    m_src = re.search(r"^\s*set\s+src=(\S+)\s*$", combined, re.M)
    src = m_src.group(1) if m_src else ""
    append = (args_tpl.replace("$src", src).replace("$@", extra))
    append = " ".join(append.split())
    log(f"grub chain: kernel={kernel} initrd={initrd}")
    log(f"  append={append}")
    return kernel, initrd, append


def mode_parse_grub(a):
    iso = Path(a.iso)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    seven = a.sevenzip
    listing = subprocess.run([seven, "l", str(iso)], capture_output=True,
                             text=True).stdout
    (outdir / "listing.txt").write_text(listing, encoding="utf-8",
                                        errors="replace")
    cfgs = [l.split()[-1] for l in listing.splitlines()
            if l.strip().lower().endswith(".cfg")]
    log(f"file cfg di ISO: {cfgs}")
    for c in cfgs:
        _7z_extract(seven, iso, c, outdir)

    result = _try_isolinux(outdir) or _try_grub_chain(outdir)
    if not result:
        log("Gagal menentukan parameter boot Auto Install (semua parser gagal).")
        return 1
    kernel_iso, initrd_iso, append = result

    for iso_path, dest in ((kernel_iso, "kernel.img"),
                           (initrd_iso, "initrd.img")):
        ok = _7z_extract(seven, iso, iso_path, outdir)
        src_name = outdir / Path(iso_path).name
        if ok and src_name.name != dest:
            (outdir / dest).write_bytes(src_name.read_bytes())
            src_name.unlink()
        if not (outdir / dest).exists():
            log(f"gagal ekstrak {iso_path}")
            return 1
    (outdir / "append.txt").write_text(append, encoding="utf-8")
    ksz = (outdir / "kernel.img").stat().st_size
    isz = (outdir / "initrd.img").stat().st_size
    log(f"kernel.img {ksz/1e6:.1f} MB, initrd.img {isz/1e6:.1f} MB")
    if ksz < 1_000_000 or isz < 100_000:
        log("ukuran kernel/initrd ngaco, kemungkinan salah ekstrak")
        return 1
    log(f"APPEND FINAL: {append}")
    return 0


# ---------------------------------------------------------------------------
# MODE run
# ---------------------------------------------------------------------------

class Monitor:
    def __init__(self, host="127.0.0.1", port=4444):
        for i in range(60):
            try:
                self.s = socket.create_connection((host, port), timeout=5)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("gagal konek ke monitor QEMU")
        self.s.settimeout(5)
        self.buf = b""
        self.drain()

    def send(self, cmd: str) -> str:
        self.s.sendall((cmd + "\n").encode())
        return self.drain()

    def drain(self) -> str:
        out = b""
        try:
            while True:
                data = self.s.recv(65536)
                if not data:
                    break
                out += data
                if b"(qemu)" in out:
                    break
        except socket.timeout:
            pass
        text = out.decode("utf-8", errors="replace")
        return text


def mode_run(a):
    qemu = a.qemu
    qemu_img = a.qemu_img
    bootdir = Path(a.bootdir)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    disk = Path(a.disk)
    iso = Path(a.iso)
    append = (bootdir / "append.txt").read_text(encoding="utf-8").strip()
    kernel = bootdir / "kernel.img"
    initrd = bootdir / "initrd.img"

    cmd = [
        qemu,
        "-machine", "pc",
        "-accel", "tcg,thread=multi",
        "-cpu", "max",
        "-smp", "4",
        "-m", "2048",
        "-kernel", str(kernel),
        "-initrd", str(initrd),
        "-append", append,
        "-hda", str(disk),
        "-cdrom", str(iso),
        "-vga", "std",
        "-display", "none",
        "-no-reboot",
        "-net", "none",
        "-serial", f"file:{(outdir / 'serial.log').as_posix()}",
        "-monitor", "tcp:127.0.0.1:4444,server,nowait",
        "-rtc", "base=localtime",
    ]
    log("Booting installer: " + " ".join(cmd[:14]) + " ...")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)
    try:
        mon = Monitor()
    except RuntimeError as e:
        log(str(e))
        proc.kill()
        return 1
    log("Monitor HMP konek.")

    dump_dir = outdir / "screens"
    dump_dir.mkdir(exist_ok=True)
    dump_count = [0]

    def screendump():
        dump_count[0] += 1
        ppm = dump_dir / f"tmp.ppm"
        try:
            mon.send(f"screendump {ppm.as_posix()}")
        except Exception as e:
            log(f"screendump gagal: {e}")
            return
        time.sleep(1.5)
        if ppm.exists() and ppm.stat().st_size > 1000:
            png = dump_dir / f"stage-{dump_count[0]:03d}.png"
            try:
                from PIL import Image
                Image.open(ppm).save(png)
                ppm.unlink()
            except Exception:
                png = dump_dir / f"stage-{dump_count[0]:03d}.ppm"
                ppm.rename(png)
        else:
            if ppm.exists():
                ppm.unlink()

    deadline = t0 + a.timeout * 60

    # Jadwal tombol. Installer BlissOS/AOSP pakai DUA jenis dialog:
    #   1. Prompt console y/n: "This will replace all operating-systems... (y/n)?"
    #      -> HARUS ditekan 'y' dulu baru Enter (Enter kosong = batal!)
    #   2. Dialog menu (newt): pilih disk (sda), filesystem (ext4), dll.
    #      -> cukup Enter (pilihan default udah benar), tekan 'y' gak ngaruh
    # Karena itu fase awal pakai pola 'y'+Enter berkala, lanjutan Enter saja.
    events = [(50, "y"), (52, "ret"),
              (75, "y"), (77, "ret"),
              (100, "y"), (102, "ret"),
              (130, "y"), (132, "ret"),
              (160, "ret"), (190, "ret"), (220, "ret"), (250, "ret"),
              (280, "ret"), (310, "ret")]
    ei = 0
    next_periodic = t0 + 370          # setelah jadwal habis: Enter tiap 60 dtk
    next_dump = t0 + 40
    exited = False

    def try_send(k):
        try:
            mon.send(f"sendkey {k}")
            return True
        except Exception as e:
            log(f"monitor gak bisa kirim '{k}': {e}")
            return False

    while time.time() < deadline:
        if proc.poll() is not None:
            exited = True
            log(f"QEMU keluar sendiri (elapsed {int(time.time()-t0)}s) - "
                f"installer kelihatannya selesai.")
            break
        elapsed = time.time() - t0
        if ei < len(events) and elapsed >= events[ei][0]:
            k = events[ei][1]
            log(f"Kirim '{k}' (jadwal #{ei+1}, t={int(elapsed)}s)")
            try_send(k)
            ei += 1
        elif ei >= len(events) and time.time() >= next_periodic:
            log("Kirim 'ret' (periodik)")
            try_send("ret")
            next_periodic = time.time() + 60
        if time.time() >= next_dump:
            screendump()
            next_dump = time.time() + (60 if dump_count[0] < 7 else 120)
        time.sleep(3)

    if not exited:
        log(f"Timeout {a.timeout} menit - matiin VM paksa.")
        try:
            mon.send("quit")
        except Exception:
            pass
        time.sleep(5)
        if proc.poll() is None:
            proc.kill()

    elapsed = int(time.time() - t0)
    raw = disk.stat().st_size if disk.exists() else 0
    status = {
        "exited": exited,
        "elapsed_sec": elapsed,
        "disk_raw_bytes": raw,
        "disk_raw_gb": round(raw / 1024**3, 2),
    }
    ok = exited and elapsed >= 240 and raw > 2 * 1024**3
    status["ok"] = ok
    (outdir / "status.json").write_text(json.dumps(status, indent=2))
    log("STATUS: " + json.dumps(status))
    try:
        screendump()  # frame terakhir buat diagnosis
    except Exception:
        pass
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)

    pg = sub.add_parser("parse-grub")
    pg.add_argument("--iso", required=True)
    pg.add_argument("--sevenzip", required=True)
    pg.add_argument("--outdir", required=True)

    rn = sub.add_parser("run")
    rn.add_argument("--qemu", required=True)
    rn.add_argument("--qemu-img", required=True)
    rn.add_argument("--disk", required=True)
    rn.add_argument("--iso", required=True)
    rn.add_argument("--bootdir", required=True)
    rn.add_argument("--outdir", required=True)
    rn.add_argument("--timeout", type=int, default=110, help="menit")

    a = p.parse_args()
    if a.mode == "parse-grub":
        sys.exit(mode_parse_grub(a))
    else:
        sys.exit(mode_run(a))


if __name__ == "__main__":
    main()
