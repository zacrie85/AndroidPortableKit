#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autoinstall.py — Pasang Android-x86/BlissOS ke disk virtual SECARA OTOMATIS
(headless, di GitHub Actions windows runner ATAU PC user via setup.ps1).

Dua mode:
  parse-grub : ekstrak kernel/initrd dari ISO, cari entry "Auto Install"
               dan ambil parameter boot-nya.
  run        : boot live system dengan DEBUG=1 (root shell di serial), lalu
               jalankan script installer. Script dikirim lewat INITRD
               INJECTION (cpio tambahan ditempel ke initrd.img, muncul
               sebagai /i.sh di guest) — serial cuma nerima perintah pendek
               `sh /i.sh`. Fallback: base64 via serial (baris pendek).

v1.2.3 — perbaikan dari analisa serial.log CI (v1.2.0/1/2 gagal):
  * fdisk bawaan install.img butuh libfdisk.so.1 (gak ada di initramfs)
    -> partisi sekarang 3 lapis: tools fdisk (LD_LIBRARY_PATH dibetulkan)
       -> busybox fdisk -> MBR jadi (dibuat host, dd + blockdev --rereadpt).
  * sumber file (kernel/initrd/system) dideteksi dinamis dari /mnt,
    system.sfs di loop-mount sendiri kalau init belum mount /sfs.
  * loop tunggu berhenti SEGERA kalau ketemu INSTALL-FAIL (dulu nunggu
    timeout penuh 150 menit padahal install udah mati di menit ke-4).
  * qcow2 virtual size dibaca dari qemu-img buat bikin MBR yang pas.

Sukses = INSTALL-OK + disk terisi > 2 GB + QEMU keluar sendiri (-no-reboot).
"""

import argparse
import base64
import json
import os
import re
import socket
import struct
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
# INITRD INJECTION (cpio newc tambahan, pure python)
# ---------------------------------------------------------------------------

def _cpio_newc_entry(name: str, data: bytes, mode: int = 0o100755) -> bytes:
    """Satu entry cpio 'newc' (SVR4)."""
    fields = [1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0]
    hdr = b"070701" + b"".join(b"%08X" % f for f in fields)
    out = hdr + name.encode() + b"\x00"
    out += b"\x00" * ((4 - len(out) % 4) % 4)
    out += data
    out += b"\x00" * ((4 - len(data) % 4) % 4)
    return out


def _build_initrd_with_files(initrd_path: Path, files: dict) -> Path:
    """Tempel cpio tambahan (berisi files) ke ujung initrd.img.
    Kernel Linux nerima initramfs berupa beberapa arsip cpio sambung-
    menyambung (masing2 boleh punya kompresi sendiri). Padding 4-byte
    relatif ke awal buffer dijaga biar parser kernel gak nyasar."""
    initrd_bytes = initrd_path.read_bytes()
    pad = b"\x00" * ((4 - len(initrd_bytes) % 4) % 4)
    blob = pad
    for name, data in files.items():
        blob += _cpio_newc_entry(name, data)
    blob += _cpio_newc_entry("TRAILER!!!", b"", mode=0o0)
    blob += b"\x00" * ((512 - len(blob) % 512) % 512)
    out = initrd_path.with_name(initrd_path.stem + "-run.img")
    out.write_bytes(initrd_bytes + blob)
    log(f"initrd injeksi: {initrd_path.name} ({len(initrd_bytes)//1e6:.1f} MB) "
        f"+ cpio [{', '.join(files)}] -> {out.name} ({len(blob)//1e3:.0f} KB tambahan)")
    return out


def _build_mbr_bin(disk: Path, qemu_img: str) -> bytes:
    """MBR 512 byte: satu partisi primary tipe 83 mulai LBA 2048.
    Ukuran partisi dihitung dari virtual-size qcow2 (pas, gak 'beyond EOD')."""
    total_sect = None
    try:
        r = subprocess.run([qemu_img, "info", "--output=json", str(disk)],
                           capture_output=True, text=True, timeout=60)
        info = json.loads(r.stdout or "{}")
        vsize = int(info.get("virtual-size") or 0)
        if vsize > 0:
            total_sect = vsize // 512
    except Exception as e:
        log(f"qemu-img info gagal ({e}) — pakai default 40G")
    if not total_sect or total_sect <= 2048:
        total_sect = 40 * 1024 * 1024 * 2  # 40 GB fallback
    part_sect = total_sect - 2048
    mbr = bytearray(512)
    # entry 1 @446: boot flag 0x80, CHS start dummy, tipe 0x83, CHS end dummy
    mbr[446:462] = struct.pack("<BBBBBBBBII",
                               0x80, 0x20, 0x21, 0x00,
                               0x83, 0xFE, 0xFF, 0x0A,
                               2048, part_sect)
    mbr[510:512] = b"\x55\xAA"
    log(f"MBR dibuat: partisi {part_sect} sektor "
        f"({part_sect*512/1024**3:.1f} GB) dari disk {total_sect} sektor")
    return bytes(mbr)


# ---------------------------------------------------------------------------
# Script installer (dijalankan DI GUEST via `sh /i.sh`)
# ---------------------------------------------------------------------------

INSTALL_SH = r"""
PATH=/tmp/ii/bin:/tmp/ii/sbin:$PATH
export PATH
LD_LIBRARY_PATH=/tmp/ii/lib:/tmp/ii/lib64:/tmp/ii/usr/lib:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH
echo "INSTALL-SCRIPT-START"
umount /hd 2>/dev/null

# --- 0a. ekstrak install.img (tools: fdisk, pv, grub-install + libs) ---
if [ ! -x /tmp/ii/sbin/grub-install ]; then
  echo "EXTRACT-TOOLS-START"
  mkdir -p /tmp/ii
  zcat /mnt/install.img | ( cd /tmp/ii && /bin/busybox cpio -idu > /dev/null 2>&1 ) \
    || zcat /src/install.img | ( cd /tmp/ii && /bin/busybox cpio -idu > /dev/null 2>&1 ) \
    || echo "TOOLS-EXTRACT-WARN"
  chmod 755 /tmp/ii/bin/* /tmp/ii/sbin/* 2>/dev/null
  echo "TOOLS-EXTRACTED"
fi
echo "TOOLS: $(ls /tmp/ii/bin 2>/dev/null | head -8 | tr '\n' ' ')"

# --- 0. cari direktori sumber (kernel/initrd/system) di media boot ---
SRC_DIR=""
for d in /mnt /src /mnt/iso /mnt/android-16.9.7; do
  if [ -f "$d/system.efs" ] || [ -f "$d/system.sfs" ] || [ -f "$d/system.img" ]; then
    SRC_DIR="$d"; break
  fi
done
echo "SRC_DIR=$SRC_DIR"
if [ -z "$SRC_DIR" ]; then
  echo "INSTALL-FAIL-NOSRC"
  ls -la /mnt /src 2>/dev/null
  exit 1
fi
ls -la "$SRC_DIR" > /tmp/srcdir.txt 2>&1
head -20 /tmp/srcdir.txt

# --- 1. partisi (3 lapis) ---
dd if=/dev/zero of=/dev/sda bs=1M count=16 2>/dev/null
sync
printf 'o\nn\np\n1\n\n\nw\n' | fdisk /dev/sda > /tmp/fdisk.log 2>&1
sync; sleep 1; mdev -s 2>/dev/null; sleep 1
if [ ! -b /dev/sda1 ]; then
  echo "PART-FALLBACK-1: busybox fdisk"
  printf 'o\nn\np\n1\n\n\nw\n' | /bin/busybox fdisk /dev/sda > /tmp/fdisk2.log 2>&1
  sync; sleep 1; mdev -s 2>/dev/null; sleep 1
fi
if [ ! -b /dev/sda1 ]; then
  echo "PART-FALLBACK-2: dd MBR jadi"
  dd if=/mbr.bin of=/dev/sda bs=512 count=1 conv=notrunc > /tmp/dd.log 2>&1
  /bin/busybox blockdev --rereadpt /dev/sda >> /tmp/dd.log 2>&1
  sync; sleep 1; mdev -s 2>/dev/null; sleep 1
fi
if [ ! -b /dev/sda1 ]; then
  mknod /dev/sda1 b 8 1 2>/dev/null
fi
if [ ! -b /dev/sda1 ]; then
  echo "INSTALL-FAIL-NOPART"
  ls -la /dev/sda*
  cat /tmp/fdisk.log /tmp/fdisk2.log /tmp/dd.log 2>/dev/null
  exit 1
fi
echo "PART-OK"
ls -la /dev/sda1

# --- 2. format (mkfs.ext4 gak ada di initramfs -> busybox mke2fs) ---
if ! /tmp/ii/sbin/mkfs.ext4 -F /dev/sda1 > /tmp/mkfs.log 2>&1; then
  if ! /bin/busybox mke2fs -F -t ext4 /dev/sda1 > /tmp/mkfs.log 2>&1; then
    if ! /bin/busybox mke2fs -F /dev/sda1 > /tmp/mkfs.log 2>&1; then
      tail -5 /tmp/mkfs.log
      echo "INSTALL-FAIL-MKFS"
      exit 1
    fi
  fi
fi
tail -3 /tmp/mkfs.log
echo "MKFS-OK"

# --- 3. mount + salin file sistem ---
mkdir -p /hd
mount -t ext4 /dev/sda1 /hd 2>/dev/null || mount /dev/sda1 /hd || { echo "INSTALL-FAIL-MOUNT"; exit 1; }
TGT=/android-16.9.7
mkdir -p /hd$TGT/data
cp "$SRC_DIR/kernel" /hd$TGT/kernel || { echo "INSTALL-FAIL-COPYKERNEL"; exit 1; }
cp "$SRC_DIR/initrd.img" /hd$TGT/initrd.img || { echo "INSTALL-FAIL-COPYINITRD"; exit 1; }
echo "KERNEL-INITRD-COPIED"

SYS_SRC=""
SYS_NAME=""
if [ -f /sfs/system.img ]; then
  SYS_SRC=/sfs/system.img; SYS_NAME=system.img
elif [ -f "$SRC_DIR/system.efs" ]; then
  SYS_SRC="$SRC_DIR/system.efs"; SYS_NAME=system.efs
elif [ -f "$SRC_DIR/system.sfs" ]; then
  mkdir -p /sfs2
  if mount -o loop "$SRC_DIR/system.sfs" /sfs2 2>/dev/null && [ -f /sfs2/system.img ]; then
    SYS_SRC=/sfs2/system.img; SYS_NAME=system.img
  else
    SYS_SRC="$SRC_DIR/system.sfs"; SYS_NAME=system.sfs
  fi
elif [ -f "$SRC_DIR/system.img" ]; then
  SYS_SRC="$SRC_DIR/system.img"; SYS_NAME=system.img
fi
if [ -z "$SYS_SRC" ] || [ ! -f "$SYS_SRC" ]; then
  echo "INSTALL-FAIL-NOSYS"
  ls -la /sfs /sfs2 "$SRC_DIR" 2>/dev/null
  exit 1
fi
echo "COPY-SYSTEM-START (dari $SYS_SRC)"
if command -v pv > /dev/null 2>&1; then
  pv -n "$SYS_SRC" > /hd$TGT/$SYS_NAME
else
  cat "$SYS_SRC" > /hd$TGT/$SYS_NAME
fi
echo "COPY-SYSTEM-DONE"
sync
SZ=$(du -sk /hd$TGT/$SYS_NAME | cut -f1)
echo "SYSTEM-IMG-KB=$SZ ($SYS_NAME)"
if [ "$SZ" -lt 1500000 ]; then echo "INSTALL-FAIL-COPY"; exit 1; fi

# --- 4. GRUB ---
mkdir -p /hd/boot/grub
grub-install --directory=/tmp/ii/lib/grub/i386-pc --modules="all_video video png gfxmenu gfxterm" --root-directory=/hd /dev/sda > /tmp/grub.log 2>&1 || { tail -8 /tmp/grub.log; echo "INSTALL-FAIL-GRUB"; exit 1; }
echo "GRUB-OK"
tail -2 /tmp/grub.log 2>/dev/null
cat > /hd/boot/grub/grub.cfg << CFGEOF9
set timeout=3
set default=0
menuentry 'BlissOS-16.9.7' {
        search --set=root --file $TGT/kernel
        linux $TGT/kernel root=/dev/ram0 SRC=android-16.9.7 quiet
        initrd $TGT/initrd.img
}
CFGEOF9
sync
ls -la /hd /hd$TGT
echo "INSTALL-OK"
sync
sleep 3
reboot -f
echo "INSTALL-FAIL-STILL-ALIVE"
"""


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


def _serial_recv(ser) -> bytes:
    try:
        return ser.recv(65536)
    except socket.timeout:
        return b""
    except OSError:
        return b""


def _send_b64_script(ser, script_text: str):
    """Fallback: kirim script sebagai base64 lewat serial (baris pendek,
    jeda cukup — baris 900 char @0.15s kebukti aman di v1.2.2)."""
    b64 = base64.b64encode(script_text.encode()).decode()
    lines = [b64[i:i + 700] for i in range(0, len(b64), 700)]
    ser.sendall(b"\n")
    time.sleep(1)
    ser.sendall(b"mkdir -p /tmp/ii && cd /tmp/ii && "
                b"zcat /mnt/install.img | cpio -idu && echo TOOLS-OK\r\n")
    time.sleep(0.3)
    for ln in lines:
        ser.sendall(f"echo {ln} >> /tmp/i.b64\r\n".encode())
        time.sleep(0.3)
    ser.sendall(b"base64 -d /tmp/i.b64 > /tmp/i.sh && echo SCRIPT-OK\r\n")
    time.sleep(0.5)
    ser.sendall(b"sh /tmp/i.sh\r\n")


def _check_boot_sector(disk: Path, qemu_img: str):
    """Verifikasi hasil install dari HOST: baca sector 0 disk via qemu-img dd.
    Return (boot_sig_55AA, part_type83, grub_code_nonzero)."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "mbr.bin"
            r = subprocess.run([qemu_img, "dd", f"if={disk}", f"of={out}",
                                "bs=512", "count=1"],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not out.exists():
                return False, False, False
            data = out.read_bytes()
            if len(data) < 512:
                return False, False, False
            sig = data[510:512] == b"\x55\xAA"
            part83 = data[450] == 0x83
            grub_code = any(data[0:446])
            return sig, part83, grub_code
    except Exception as e:
        log(f"cek boot sector gagal: {e}")
        return False, False, False


def mode_run(a):
    """Install otomatis VIA SERIAL SHELL, script dikirim via initrd injection.

    Alur: boot live dengan DEBUG=1 -> init kasih root shell di serial
    ("Type 'exit' to continue booting") -> kita kirim `sh /i.sh`
    (i.sh + mbr.bin nempel di initramfs) -> script partisi/format/salin/
    grub -> reboot -f (QEMU keluar karena -no-reboot).
    """
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

    # --- siapkan initrd ber-injeksi (i.sh + mbr.bin) ---
    mbr_bin = _build_mbr_bin(disk, a.qemu_img)
    inject = {"i.sh": INSTALL_SH.encode(), "mbr.bin": mbr_bin}
    initrd_run = _build_initrd_with_files(initrd, inject)
    log(f"initrd dipakai: {initrd_run.name}")

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
        "-m", str(a.mem),
        "-kernel", str(kernel),
        "-initrd", str(initrd_run),
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
    log("Booting live (serial shell + initrd injection): "
        + " ".join(cmd[:12]) + " ...")
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

    # --- fase 1: nunggu debug shell ---
    def phase1_wait_shell(deadline_sec):
        buf = ""
        dl = time.time() + deadline_sec
        next_dump = time.time() + 90
        while time.time() < dl:
            if proc.poll() is not None:
                return None
            got = _serial_recv(ser)
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
    log("Debug shell terdeteksi. Kirim perintah install...")
    time.sleep(2)
    ser.sendall(b"\n")
    time.sleep(1)

    # --- fase 2: eksekusi script (injeksi initrd; fallback b64) ---
    ser.sendall(b"sh /i.sh\r\n")
    sent_fallback = False

    deadline = t0 + a.timeout * 60
    saw_ok = False
    saw_fail = ""
    saw_script_start = False
    sent_fallback = False
    qemu_exited = False
    fail_markers = ("INSTALL-FAIL",)
    next_dump = time.time() + 90
    idle_since = time.time()
    while time.time() < deadline:
        if proc.poll() is not None:
            qemu_exited = True
            log(f"QEMU keluar (elapsed {int(time.time() - t0)}s)")
            break
        got = _serial_recv(ser)
        if got:
            idle_since = time.time()
            log_file.write(got)
            log_file.flush()
            buf += got.decode("utf-8", "replace")
            if len(buf) > 300000:
                buf = buf[-150000:]
            if "INSTALL-OK" in buf and not saw_ok:
                saw_ok = True
                log(">>> INSTALL-OK terlihat - nunggu VM reboot keluar sendiri...")
            if "INSTALL-SCRIPT-START" in buf and not saw_script_start:
                saw_script_start = True
                log(">>> script installer mulai jalan di guest...")
            if ("not found" in buf and "sh /i.sh" in buf
                    and not sent_fallback and not saw_script_start):
                # /i.sh gak ada (injeksi gagal?) -> kirim via base64
                sent_fallback = True
                log(">>> /i.sh gak ketemu — fallback kirim script via base64...")
                _send_b64_script(ser, INSTALL_SH)
            for mk in fail_markers:
                if mk in buf:
                    line = buf[buf.find(mk):]
                    saw_fail = line.splitlines()[0][:80]
                    log(f">>> GAGAL TERDETEKSI: {saw_fail}")
                    break
            if saw_fail and "INSTALL-FAIL-STILL-ALIVE" not in saw_fail:
                break   # berhenti SEGERA — jangan buang waktu nunggu timeout
        else:
            # idlenya lama + belum ada marker apa2 -> screendump buat diagnosis
            pass
        if time.time() >= next_dump:
            screendump()
            next_dump = time.time() + 120
        time.sleep(1)

    if proc.poll() is None:
        log("Matiin VM (selesai / gagal / timeout).")
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
    boot_sig, part83, grub_code = _check_boot_sector(disk, a.qemu_img)
    log(f"boot sector: 55AA={boot_sig} part83={part83} grub_code={grub_code}")
    # sukses: marker INSTALL-OK terlihat, ATAU QEMU keluar sendiri + MBR berisi
    # grub (echo terakhir bisa gak keburu keluar sebelum reboot -f)
    ok = (raw > 2 * 1024**3
          and (saw_ok or (qemu_exited and grub_code and part83)))
    status = {"elapsed_sec": elapsed, "disk_raw_bytes": raw,
              "disk_raw_gb": round(raw / 1024**3, 2),
              "saw_install_ok": saw_ok, "qemu_exited": qemu_exited,
              "boot_sig_55aa": boot_sig, "part_type83": part83,
              "grub_code": grub_code, "fail_hint": saw_fail, "ok": ok}
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
    rn.add_argument("--mem", type=int, default=2048, help="RAM guest MB")
    rn.add_argument("--accel", choices=["auto", "whpx", "tcg"], default="auto")

    a = p.parse_args()
    if a.mode == "parse-grub":
        sys.exit(mode_parse_grub(a))
    else:
        sys.exit(mode_run(a))


if __name__ == "__main__":
    main()
