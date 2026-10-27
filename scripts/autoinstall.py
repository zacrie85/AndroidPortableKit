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


def _whpx_works(qemu: str) -> bool:
    """Tes WHPX beneran aktif di Windows (bukan cuma ada di build QEMU).
    Boot -machine none -S selama 2.5 detik: kalau masih hidup = WHPX oke."""
    if os.name != "nt":
        return False
    try:
        proc = subprocess.Popen(
            [qemu, "-accel", "whpx", "-machine", "none", "-S", "-display", "none"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2.5)
        alive = proc.poll() is None
        if alive:
            proc.kill()
        return alive
    except Exception:
        return False


def mode_run(a):
    """Install otomatis VIA SERIAL SHELL (tanpa dialog installer sama sekali).

    Alur: boot live dengan DEBUG=1 -> init kasih root shell di serial
    ("Type 'exit' to continue booting") -> kita kirim script installer
    (base64 biar aman dari masalah EOL serial) -> script melakukan:
      fdisk + mkfs.ext4 + cp kernel/initrd/system.img + grub-install
      + grub.cfg -> reboot -f (QEMU keluar karena -no-reboot).
    Sukses = 'INSTALL-OK' terlihat + disk terisi > 2 GB + QEMU keluar.
    """
    import base64
    import time as _t
    qemu = a.qemu
    bootdir = Path(a.bootdir)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    disk = Path(a.disk)
    iso = Path(a.iso)
    append = (bootdir / "append.txt").read_text(encoding="utf-8").strip()
    # buang parameter yang gak dipakai mode ini, tambah DEBUG=1 + serial console
    append = re.sub(r"(^|\s)AUTO_INSTALL=\S*", " ", append)
    append = re.sub(r"(^|\s)DEBUG=\S*", " ", append)
    append = append + " DEBUG=1 noexec=off console=tty0 console=ttyS0,115200"
    append = " ".join(append.split())
    kernel = bootdir / "kernel.img"
    initrd = bootdir / "initrd.img"

    # pilih akselerasi: whpx kalau beneran aktif (kencang), kalau gak -> tcg
    accel_arg = ["-accel", "tcg,thread=multi"]
    cpu_arg = "max"
    if a.accel == "whpx" or (a.accel == "auto" and _whpx_works(qemu)):
        accel_arg = ["-accel", "whpx"]
        cpu_arg = "host"
    log(f"akselerasi: {accel_arg[1]} (cpu {cpu_arg})")

    cmd = [
        qemu,
        "-machine", "pc",
        accel_arg[0], accel_arg[1],
        "-cpu", cpu_arg,
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
    log("Booting live (serial shell mode): " + " ".join(cmd[:12]) + " ...")
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)

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
            hmp = None
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
    dump_dir = outdir / "screens"
    dump_dir.mkdir(exist_ok=True)
    dump_n = [0]

    def screendump():
        dump_n[0] += 1
        hmp_cmd(f"screendump { (dump_dir / ('s%03d.ppm' % dump_n[0])).as_posix() }")

    def serial_send(line: str):
        ser.sendall(line.encode() + b"\r\n")
        time.sleep(0.15)

    INSTALL_SH = """
PATH=/tmp/ii/bin:/tmp/ii/sbin:$PATH
export PATH
echo "INSTALL-SCRIPT-START"
umount /hd 2>/dev/null
echo "o
n
p
1


w" | fdisk /dev/sda
sleep 2
mdev -s
ls -la /dev/sda1
mkfs.ext4 -F /dev/sda1 || mke2fs -F /dev/sda1 || { echo "INSTALL-FAIL-MKFS"; exit 1; }
mkdir -p /hd
mount /dev/sda1 /hd || { echo "INSTALL-FAIL-MOUNT"; exit 1; }
mkdir -p /hd/android-16.9.7/data
cp /mnt/kernel /hd/android-16.9.7/kernel
cp /mnt/initrd.img /hd/android-16.9.7/initrd.img
echo "COPY-SYSTEM-START"
pv -n /sfs/system.img > /hd/android-16.9.7/system.img
echo "COPY-SYSTEM-DONE"
sync
SZ=$(du -sk /hd/android-16.9.7/system.img | cut -f1)
echo "SYSTEM-IMG-KB=$SZ"
if [ "$SZ" -lt 2000000 ]; then echo "INSTALL-FAIL-COPY"; exit 1; fi
mkdir -p /hd/boot/grub
grub-install --directory=/tmp/ii/lib/grub/i386-pc --modules="all_video video png gfxmenu gfxterm" --root-directory=/hd /dev/sda || { echo "INSTALL-FAIL-GRUB"; exit 1; }
cat > /hd/boot/grub/grub.cfg << CFGEOF9
set timeout=3
set default=0
menuentry 'BlissOS-16.9.7' {
	search --set=root --file /android-16.9.7/kernel
	linux /android-16.9.7/kernel root=/dev/ram0 SRC=android-16.9.7 quiet
	initrd /android-16.9.7/initrd.img
}
CFGEOF9
sync
ls -la /hd /hd/android-16.9.7
echo "INSTALL-OK"
reboot -f
echo "INSTALL-FAIL-STILL-ALIVE"
"""

    b64 = base64.b64encode(INSTALL_SH.encode()).decode()
    # pecah jadi baris 900 karakter biar aman di serial
    b64_lines = [b64[i:i + 900] for i in range(0, len(b64), 900)]

    def phase1_wait_shell(deadline_sec):
        buf = ""
        dl = time.time() + deadline_sec
        next_dump = time.time() + 90
        while time.time() < dl:
            if proc.poll() is not None:
                return None
            try:
                got = ser.recv(65536)
            except socket.timeout:
                got = b""
            except OSError:
                got = b""
            if got:
                log_file.write(got)
                log_file.flush()
                buf += got.decode("utf-8", "replace")
                if len(buf) > 200000:
                    buf = buf[-100000:]
            if "Type 'exit' to continue booting" in buf:
                return buf
            if time.time() >= next_dump:
                screendump()
                next_dump = time.time() + 120
        return None

    log("Menunggu debug shell di serial (maks 30 menit)...")
    buf = phase1_wait_shell(30 * 60) or ""
    if "Type 'exit' to continue booting" not in buf:
        log("GAGAL: debug shell gak pernah muncul.")
        log_file.close()
        try:
            hmp_cmd("quit")
        except Exception:
            pass
        time.sleep(5)
        if proc.poll() is None:
            proc.kill()
        return 1
    log("Debug shell terdeteksi. Kirim script installer...")
    time.sleep(2)
    ser.sendall(b"\n")
    time.sleep(1)

    serial_send("mkdir -p /tmp/ii && cd /tmp/ii && zcat /mnt/install.img | cpio -idu && echo TOOLS-OK")
    for ln in b64_lines:
        serial_send(f"echo {ln} >> /tmp/i.b64")
    serial_send("base64 -d /tmp/i.b64 > /tmp/i.sh && echo SCRIPT-OK")
    serial_send("sh /tmp/i.sh")

    deadline = t0 + a.timeout * 60
    saw_ok = False
    saw_fail = ""
    next_dump = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            log(f"QEMU keluar (elapsed {int(time.time() - t0)}s)")
            break
        try:
            got = ser.recv(65536)
        except socket.timeout:
            got = b""
        except OSError:
            got = b""
        if got:
            log_file.write(got)
            log_file.flush()
            buf += got.decode("utf-8", "replace")
            if len(buf) > 300000:
                buf = buf[-150000:]
            if "INSTALL-OK" in buf and not saw_ok:
                saw_ok = True
                log(">>> INSTALL-OK terlihat - nunggu VM reboot keluar sendiri...")
            for mk in ("INSTALL-FAIL", "Error!", "not found"):
                if mk in buf and not saw_fail:
                    pass  # cuma catatan; penentuan gagal tetap lewat marker akhir
        if "INSTALL-FAIL" in buf and not saw_fail:
            saw_fail = buf[buf.find("INSTALL-FAIL"):buf.find("INSTALL-FAIL") + 60].splitlines()[0]
            log(f">>> {saw_fail}")
        if time.time() >= next_dump:
            screendump()
            next_dump = time.time() + 120
        time.sleep(1)

    if proc.poll() is None:
        log("Timeout / belum keluar - matiin VM.")
        try:
            hmp_cmd("quit")
        except Exception:
            pass
        time.sleep(5)
        if proc.poll() is None:
            proc.kill()
    log_file.close()

    elapsed = int(time.time() - t0)
    raw = disk.stat().st_size if disk.exists() else 0
    ok = saw_ok and raw > 2 * 1024**3 and elapsed >= 120
    status = {"elapsed_sec": elapsed, "disk_raw_bytes": raw,
              "disk_raw_gb": round(raw / 1024**3, 2),
              "saw_install_ok": saw_ok, "fail_hint": saw_fail, "ok": ok}
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
    rn.add_argument("--accel", choices=["auto", "whpx", "tcg"], default="auto")

    a = p.parse_args()
    if a.mode == "parse-grub":
        sys.exit(mode_parse_grub(a))
    else:
        sys.exit(mode_run(a))


if __name__ == "__main__":
    main()
