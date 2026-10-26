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

def mode_parse_grub(a):
    iso = Path(a.iso)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    seven = a.sevenzip
    listing = subprocess.run([seven, "l", str(iso)], capture_output=True,
                             text=True).stdout
    cfgs = [l.split()[-1] for l in listing.splitlines()
            if l.strip().endswith("grub.cfg")]
    if not cfgs:
        log("grub.cfg gak ketemu di ISO!")
        (outdir / "listing.txt").write_text(listing, encoding="utf-8",
                                            errors="replace")
        return 1
    cfg_path = sorted(cfgs, key=lambda p: (0 if "boot" in p.lower() else 1, len(p)))[0]
    log(f"grub.cfg dipilih: {cfg_path}")
    subprocess.run([seven, "e", str(iso), f"-o{outdir}", cfg_path, "-y"],
                   capture_output=True, text=True)
    cfg_file = outdir / Path(cfg_path).name
    cfg = cfg_file.read_text(encoding="utf-8", errors="replace")

    # cari menuentry yang mengandung "auto install"
    blocks = re.split(r"(?=menuentry\s)", cfg)
    target = None
    for b in blocks:
        if "auto install" in b.lower():
            target = b
            break
    if not target:
        log("Entry 'Auto Install' gak ketemu. Isi grub.cfg dicatet buat diagnosis.")
        (outdir / "grub-dump.txt").write_text(cfg, encoding="utf-8",
                                              errors="replace")
        return 1

    m_linux = re.search(r"^\s*linux\s+(\S+)\s+(.+)$", target, re.M)
    m_initrd = re.search(r"^\s*initrd\s+(\S+)", target, re.M)
    if not m_linux or not m_initrd:
        (outdir / "grub-dump.txt").write_text(target, encoding="utf-8",
                                              errors="replace")
        return 1
    kernel_path = m_linux.group(1).lstrip("/")
    initrd_path = m_initrd.group(1).lstrip("/")
    append = " ".join(m_linux.group(2).split())
    log(f"kernel: /{kernel_path}")
    log(f"initrd: /{initrd_path}")
    log(f"append: {append}")

    for iso_path, dest in ((kernel_path, "kernel.img"),
                           (initrd_path, "initrd.img")):
        r = subprocess.run([seven, "e", str(iso), f"-o{outdir}", iso_path, "-y"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not (outdir / dest).exists():
            # nama file asli beda dari dest -> copy
            src = outdir / Path(iso_path).name
            if src.exists():
                (outdir / dest).write_bytes(src.read_bytes())
            else:
                log(f"gagal ekstrak {iso_path}: {r.stderr[:300]}")
                return 1
    (outdir / "append.txt").write_text(append, encoding="utf-8")
    ksz = (outdir / "kernel.img").stat().st_size
    isz = (outdir / "initrd.img").stat().st_size
    log(f"kernel.img {ksz/1e6:.1f} MB, initrd.img {isz/1e6:.1f} MB")
    if ksz < 1_000_000 or isz < 100_000:
        log("ukuran kernel/initrd ngaco, kemungkinan salah ekstrak")
        return 1
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
        mon.send(f"screendump {ppm.as_posix()}")
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
    next_enter = t0 + 60
    fast_enters_until = t0 + 330     # jendela dialog: Enter tiap 25 detik
    next_dump = t0 + 45
    exited = False

    while time.time() < deadline:
        if proc.poll() is not None:
            exited = True
            log(f"QEMU keluar sendiri (elapsed {int(time.time()-t0)}s) - "
                f"installer kelihatannya selesai.")
            break
        now = time.time()
        if now >= next_enter:
            log("Kirim Enter...")
            mon.send("sendkey ret")
            next_enter = now + (25 if now < fast_enters_until else 60)
        if now >= next_dump:
            screendump()
            next_dump = now + 120
        time.sleep(4)

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
    screendump()  # frame terakhir buat diagnosis
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
