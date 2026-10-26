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
    """Serial-console automation (expect-style).

    console=ttyS0 jadi /dev/console -> SEMUA prompt installer kelihatan di
    serial dan jawaban dikirim langsung ke serial socket. Tanpa nebak tombol.
    """
    qemu = a.qemu
    bootdir = Path(a.bootdir)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    disk = Path(a.disk)
    iso = Path(a.iso)
    append = (bootdir / "append.txt").read_text(encoding="utf-8").strip()
    # AUTO_INSTALL=force: lewati dialog "last confirmation" (--defaultno) yang
    # FATAL kalau kena Enter (default No = reboot). Lihat 1-install baris 140.
    append = append.replace("AUTO_INSTALL=0", "AUTO_INSTALL=force")
    # FORCE_FORMAT=ext4: auto-pilih filesystem (kernel passer env ke init)
    # console=ttyS0 TERAKHIR -> /dev/console = serial (prompt + dialog di serial)
    append = append + " FORCE_FORMAT=ext4 console=tty0 console=ttyS0,115200"
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
        "-serial", "tcp:127.0.0.1:5557,server,nowait",
        "-monitor", "tcp:127.0.0.1:4444,server,nowait",
        "-rtc", "base=localtime",
    ]
    log("Booting installer (serial mode): " + " ".join(cmd[:12]) + " ...")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)

    # konek ke serial TCP socket (Windows Python gak punya AF_UNIX!)
    ser = None
    for _ in range(90):
        try:
            ser = socket.create_connection(("127.0.0.1", 5557), timeout=5)
            break
        except OSError:
            ser = None
            time.sleep(1)
    if ser is None:
        log("gagal konek serial socket")
        proc.kill()
        return 1
    ser.settimeout(2)
    log("Serial socket konek.")

    hmp = None
    for _ in range(30):
        try:
            hmp = socket.create_connection(("127.0.0.1", 4444), timeout=5)
            break
        except OSError:
            time.sleep(1)
    if hmp is not None:
        hmp.settimeout(3)

    def hmp_cmd(c):
        try:
            hmp.sendall((c + "\n").encode())
            time.sleep(0.5)
            return hmp.recv(65536).decode("utf-8", "replace")
        except Exception:
            return ""

    log_file = open(outdir / "serial.log", "wb")
    buf = ""
    responded = {}          # pattern -> jumlah respons terkirim
    dump_dir = outdir / "screens"
    dump_dir.mkdir(exist_ok=True)
    dump_n = [0]

    def screendump():
        dump_n[0] += 1
        hmp_cmd(f"screendump { (dump_dir / ('s%03d.ppm' % dump_n[0])).as_posix() }")
        time.sleep(1.0)
        ppm = dump_dir / f"s{dump_n[0]:03d}.ppm"
        if ppm.exists() and ppm.stat().st_size > 1000:
            try:
                from PIL import Image
                Image.open(ppm).save(str(ppm.with_suffix(".png")))
                ppm.unlink()
            except Exception:
                pass

    # (pola, jawaban, nama) — jawaban dikirim SATU kali per kemunculan pola
    RULES = [
        ("Are you sure you want to continue (y/n)?", "n\r",
         "prompt y/n (jawab n -> install)"),
        ("Would you like to customize the formatted drive name", "\r",
         "inputbox nama drive (default)"),
        ("Are you sure to create the image?", "\r",
         "data image -> Yes"),
        ("Please input the size of the data.img", "\r",
         "ukuran data.img (default 2048)"),
        ("Do you want to force format", "\x1b[D\r",
         "error mount -> force format Yes"),
        ("is installed successfully", "MENU_END",
         "Congratulations -> pilih Reboot"),
    ]

    deadline = t0 + a.timeout * 60
    next_dump = t0 + 60
    saw_rebooting = False
    congratulated = False
    last_data = time.time()

    while time.time() < deadline:
        if proc.poll() is not None:
            log(f"QEMU keluar (elapsed {int(time.time()-t0)}s)")
            break
        # baca serial
        got = b""
        try:
            got = ser.recv(65536)
        except socket.timeout:
            pass
        except OSError:
            pass
        if got:
            last_data = time.time()
            log_file.write(got)
            log_file.flush()
            try:
                buf += got.decode("utf-8", "replace")
            except Exception:
                pass
            # jalankan aturan jawaban
            for pat, ans, name in RULES:
                n_seen = buf.count(pat)
                n_sent = responded.get(pat, 0)
                while n_sent < n_seen:
                    if ans == "MENU_END":
                        log(f"SELESAI INSTALL! ({name}) - pilih Reboot")
                        congratulated = True
                        ser.sendall(b"\x1b[B")      # panah bawah -> Reboot
                        time.sleep(1.5)
                        ser.sendall(b"\r")
                    else:
                        log(f"JAWAB: {name} -> {ans!r}")
                        ser.sendall(ans.encode())
                    responded[pat] = n_sent + 1
                    n_sent += 1
            if "Rebooting" in buf and not saw_rebooting:
                saw_rebooting = True
                log(">>> 'Rebooting' terlihat - INSTALL BERHASIL! <<<")
        # dump berkala
        if time.time() >= next_dump:
            screendump()
            next_dump = time.time() + 120
        # diam total > 10 menit pasca-congrats -> kemungkinan hang, catet aja
        if congratulated and time.time() - last_data > 600:
            log("Serial diam lama setelah Congratulations - kemungkinan VM hang.")
        time.sleep(1)

    log_file.close()
    if proc.poll() is None:
        log("Timeout - matiin VM.")
        try:
            hmp_cmd("quit")
        except Exception:
            pass
        time.sleep(5)
        if proc.poll() is None:
            proc.kill()

    elapsed = int(time.time() - t0)
    raw = disk.stat().st_size if disk.exists() else 0
    # Sukses HANYA jika installer sendiri mengirim "Rebooting" (tanda selesai)
    # dan disk beneran terisi (sistem + data image).
    ok = saw_rebooting and (raw > 2 * 1024**3) and elapsed >= 240
    status = {"elapsed_sec": elapsed, "disk_raw_bytes": raw,
              "disk_raw_gb": round(raw / 1024**3, 2),
              "saw_rebooting": saw_rebooting, "ok": ok}
    (outdir / "status.json").write_text(json.dumps(status, indent=2))
    log("STATUS: " + json.dumps(status))
    return 0 if ok else 1


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
