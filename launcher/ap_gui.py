# -*- coding: utf-8 -*-
"""
ap_gui.py — Tampilan launcher Android Portable Kit (Bahasa Indonesia)
=====================================================================
Jalankan:  python ap_gui.py   (atau AndroidPortable.exe hasil build)
Fungsi utama:
  1. Jalankan Android (TikTok, Instagram, SnackVideo, YouTube, dll.)
  2. Kirim video dari PC ke Android biar bisa diupload
  3. Install APK, bikin profile baru buat multi-akun (clone)
"""

import threading
import queue
import os
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, scrolledtext

import ap_core


class App(tk.Tk):
    def __init__(self, engine: ap_core.AndroidEngine):
        super().__init__()
        self.engine = engine
        self.title(f"{ap_core.APP_NAME} v{ap_core.APP_VERSION} — "
                   f"Jalankan Android di Windows")
        self.geometry("780x640")
        self.minsize(720, 560)

        self.worker = None
        self.done_q: queue.Queue = queue.Queue()
        self.bg_buttons: list[ttk.Button] = []
        self._update_info = None

        self._build_ui()
        self._startup_checks()
        self.after(3000, self._update_check_bg)
        self.after(200, self._tick)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"),
                        padding=8)
        style.configure("Head.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 9))

        pad = {"padx": 10, "pady": 6}

        # --- header ---
        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="Android Portable Kit", style="Head.TLabel").pack(
            anchor="w")
        ttk.Label(head, text="Nonton & upload video TikTok, Instagram, SnackVideo, "
                             "YouTube langsung dari Windows — tanpa install.",
                  style="Sub.TLabel").pack(anchor="w")
        self.lbl_specs = ttk.Label(head, text="Cek spek PC...", style="Sub.TLabel")
        self.lbl_specs.pack(anchor="w")

        # --- banner update (muncul cuma kalau ada versi baru di GitHub) ---
        self.frm_update = ttk.Frame(self)
        self.lbl_update = ttk.Label(self.frm_update, style="Sub.TLabel",
                                    foreground="#b45309", text="")
        self.lbl_update.pack(side="left")
        self.btn_update = ttk.Button(self.frm_update, text="Update Sekarang",
                                     command=self._do_update)
        self.btn_update.pack(side="left", padx=8)

        # --- status kesiapan ---
        self.frm_status = ttk.LabelFrame(self, text="Status Kesiapan")
        self.frm_status.pack(fill="x", **pad)
        self.lbl_status = ttk.Label(self.frm_status, text="...", padding=6)
        self.lbl_status.pack(fill="x")

        # --- profile / instance ---
        frm_prof = ttk.LabelFrame(self, text="Profile Android (1 profile = 1 akun)")
        frm_prof.pack(fill="x", **pad)
        row1 = ttk.Frame(frm_prof); row1.pack(fill="x", padx=6, pady=4)
        ttk.Label(row1, text="Profile:").pack(side="left")
        self.cmb_profile = ttk.Combobox(row1, width=22, state="readonly")
        self.cmb_profile.pack(side="left", padx=6)
        self.btn_profile_refresh = ttk.Button(row1, text="Muat Ulang",
                                              command=self.refresh_profiles)
        self.btn_profile_refresh.pack(side="left", padx=3)
        self.btn_profile_new = ttk.Button(row1, text="+ Profile Baru",
                                          command=self.on_new_profile)
        self.btn_profile_new.pack(side="left", padx=3)
        self.btn_profile_del = ttk.Button(row1, text="Hapus Profile",
                                          command=self.on_del_profile)
        self.btn_profile_del.pack(side="left", padx=3)

        row2 = ttk.Frame(frm_prof); row2.pack(fill="x", padx=6, pady=4)
        self.btn_start = ttk.Button(row2, text="JALANKAN ANDROID",
                                    style="Big.TButton", command=self.on_start)
        self.btn_start.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(row2, text="Matikan Terpilih",
                                   command=self.on_stop_one)
        self.btn_stop.pack(side="left", padx=3)
        self.btn_stop_all = ttk.Button(row2, text="Matikan Semua",
                                       command=self.on_stop_all)
        self.btn_stop_all.pack(side="left", padx=3)
        self.btn_cfg = ttk.Button(row2, text="Pengaturan (config.json)",
                                  command=self.on_open_config)
        self.btn_cfg.pack(side="right", padx=3)

        # --- daftar instance yang jalan ---
        frm_inst = ttk.LabelFrame(self, text="Android yang Lagi Jalan")
        frm_inst.pack(fill="x", **pad)
        cols = ("profile", "port", "status")
        self.tree = ttk.Treeview(frm_inst, columns=cols, show="headings",
                                 height=3)
        self.tree.heading("profile", text="Profile")
        self.tree.heading("port", text="Port ADB")
        self.tree.heading("status", text="Status")
        self.tree.column("profile", width=200)
        self.tree.column("port", width=90, anchor="center")
        self.tree.column("status", width=380)
        self.tree.pack(fill="x", padx=6, pady=4)

        # --- aksi utama ---
        frm_act = ttk.LabelFrame(self,
                                 text="Aksi Utama — Nonton & Upload Video")
        frm_act.pack(fill="x", **pad)
        row3 = ttk.Frame(frm_act); row3.pack(fill="x", padx=6, pady=4)
        ttk.Label(row3, text="Kirim ke:").pack(side="left")
        self.cmb_target = ttk.Combobox(row3, width=20, state="readonly")
        self.cmb_target.pack(side="left", padx=6)

        self.btn_video = ttk.Button(row3, text="KIRIM VIDEO DARI PC",
                                    style="Big.TButton", command=self.on_send_video)
        self.btn_video.pack(side="left", padx=6)
        self.btn_apk = ttk.Button(row3, text="Install APK",
                                  command=self.on_install_apk)
        self.btn_apk.pack(side="left", padx=6)
        self.btn_folder = ttk.Button(row3, text="Buka Folder Video",
                                     command=self.on_open_folder)
        self.btn_folder.pack(side="left", padx=6)

        tip = ttk.Label(frm_act, wraplength=700, justify="left", style="Sub.TLabel",
                        text="Cara upload: klik 'KIRIM VIDEO DARI PC' dan pilih video "
                             "dari folder komputer kamu. Video bakal muncul di galeri "
                             "Android, lalu buka TikTok/Instagram/SnackVideo/YouTube, "
                             "tekan tombol Upload (+), pilih videonya, dan tulis "
                             "caption/judul di app-nya. Gampang kan?")
        tip.pack(fill="x", padx=8, pady=(0, 6))

        # --- log ---
        frm_log = ttk.LabelFrame(self, text="Log")
        frm_log.pack(fill="both", expand=True, **pad)
        self.txt_log = scrolledtext.ScrolledText(frm_log, height=10, wrap="word",
                                                 font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)
        self.txt_log.configure(state="disabled")

        self._collect_bg_buttons()

    def _collect_bg_buttons(self):
        self.bg_buttons = [
            self.btn_start, self.btn_stop, self.btn_stop_all,
            self.btn_profile_new, self.btn_profile_del,
            self.btn_video, self.btn_apk,
        ]

    def _set_busy(self, busy: bool):
        for b in self.bg_buttons:
            b.state(["disabled"] if busy else ["!disabled"])

    # ------------------------------------------------------------------
    # STARTUP & POLLING
    # ------------------------------------------------------------------
    def _startup_checks(self):
        eng = self.engine
        if not eng.p.config_file.exists():
            eng.log("Pertama kali jalan — mendeteksi spek PC (auto-fit)...")
            ap_core.autodetect_config(eng.p)
            eng.cfg = ap_core.load_config(eng.p)
        st = eng.check_setup()
        self.lbl_specs.config(text=ap_core.specs_summary(eng))
        if st["ready"]:
            txt = ("Siap! QEMU: OK | ADB: OK | Sistem Android: OK"
                   + (f" | ISO terpasang: {st['iso_name']}" if st["iso_name"] else ""))
            self.lbl_status.config(text=txt, foreground="green")
        else:
            missing = []
            if not st["qemu_ok"]:
                missing.append("QEMU")
            if not st["adb_ok"]:
                missing.append("ADB")
            if not st["base_ok"]:
                missing.append("Sistem Android")
            self.lbl_status.config(
                text="Belum siap. Belum ada: " + ", ".join(missing) +
                     "  ->  Jalankan '1-Setup-Awal.bat' dulu ya.",
                foreground="red")
            eng.log("Setup belum lengkap: " + ", ".join(missing))
        self.refresh_profiles()

        # autolog tips
        eng.log("Tips: buat ngeluarin mouse dari jendela Android, tekan Ctrl+Alt+G.")
        eng.log("Tips: satu profile = satu akun. Bikin beberapa profile buat "
                "banyak akun jalan bareng.")

    def refresh_profiles(self):
        profs = self.engine.list_profiles()
        self.cmb_profile["values"] = profs
        if profs and not self.cmb_profile.get():
            self.cmb_profile.set(profs[0])

    def _tick(self):
        # log
        for line in self.engine.poll_log():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", line + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        # selesaikan worker
        try:
            while True:
                self.done_q.get_nowait()
                self._set_busy(False)
                self.worker = None
        except queue.Empty:
            pass
        # refresh daftar instance
        eng = self.engine
        items = []
        targets = []
        for inst in eng.instances.values():
            if inst.alive():
                status = "Nyala, nunggu ADB..." if not inst.adb_ready \
                    else "Jalan & siap dipakai"
                items.append((inst.profile, str(inst.port), status))
                if inst.adb_ready:
                    targets.append(inst.profile)
            else:
                items.append((inst.profile, "-", "Mati"))
        # simpan pilihan biar gak ilang pas refresh
        sel_prof = None
        sel = self.tree.selection()
        if sel:
            try:
                sel_prof = str(self.tree.item(sel[0])["values"][0])
            except Exception:
                sel_prof = None
        self.tree.delete(*self.tree.get_children())
        for it in items:
            iid = self.tree.insert("", "end", values=it)
            if sel_prof and str(it[0]) == sel_prof:
                self.tree.selection_set(iid)
                self.tree.see(iid)
        prev = self.cmb_target.get()
        self.cmb_target["values"] = targets
        if targets:
            if prev in targets:
                self.cmb_target.set(prev)
            elif not self.cmb_target.get():
                self.cmb_target.set(targets[0])
        else:
            self.cmb_target.set("")
        self.after(800, self._tick)

    # ------------------------------------------------------------------
    # WORKER HELPER
    # ------------------------------------------------------------------
    def _run_bg(self, fn):
        if self.worker and self.worker.is_alive():
            self.engine.log("Tungguin dulu, masih ada proses yang jalan ya...")
            return
        self._set_busy(True)

        def wrap():
            try:
                fn()
            except Exception as e:
                self.engine.log(f"ERROR: {e}")
                self.done_q.put("err")
            else:
                self.done_q.put("ok")

        self.worker = threading.Thread(target=wrap, daemon=True)
        self.worker.start()

    def _running_instance(self) -> ap_core.Instance | None:
        name = self.cmb_target.get().strip()
        if not name:
            messagebox.showinfo(
                "Belum ada Android nyala",
                "Nyalain dulu Android-nya: pilih profile di atas lalu klik "
                "'JALANKAN ANDROID'.")
            return None
        inst = self.engine.instances.get(name)
        if not inst or not inst.alive():
            messagebox.showinfo("Instance mati", "Android-nya udah mati, "
                               "nyalain lagi dulu ya.")
            return None
        return inst

    # ------------------------------------------------------------------
    # AKSI
    # ------------------------------------------------------------------
    def on_start(self):
        eng = self.engine
        profile = self.cmb_profile.get().strip()
        if not profile:
            messagebox.showwarning("Belum ada profile",
                                   "Bikin dulu profile baru (tombol '+ Profile "
                                   "Baru'), atau jalankan '1-Setup-Awal.bat' "
                                   "kalau setup awal belum dijalanin.")
            return

        def job():
            inst = eng.start_instance(profile)
            eng.wait_adb_ready(inst)
            if inst.adb_ready:
                eng.log("Sekarang kamu bisa: login akun Google, install "
                        "TikTok/Instagram/SnackVideo/YouTube dari Play Store, "
                        "lalu kirim video dari PC buat diupload.")
            else:
                eng.log("Android masih nyala layarnya, tapi ADB belum siap. "
                        "Cek Settings > Developer options > ADB over Network.")

        self._run_bg(job)

    def on_stop_one(self):
        eng = self.engine
        # ikuti yang dipilih di daftar instance kalau ada
        profile = None
        sel = self.tree.selection()
        if sel:
            try:
                profile = str(self.tree.item(sel[0])["values"][0])
            except Exception:
                profile = None
        if not profile:
            profile = self.cmb_profile.get().strip()
        if not profile:
            return

        def job():
            eng.stop_instance(profile)

        self._run_bg(job)

    def on_stop_all(self):
        eng = self.engine

        def job():
            eng.stop_all()

        self._run_bg(job)

    def on_new_profile(self):
        eng = self.engine
        if not eng.p.base_disk.exists():
            messagebox.showwarning(
                "Sistem Android belum ada",
                "Sistem dasar Android belum terpasang.\n"
                "Jalanin dulu '1-Setup-Awal.bat' sampai selesai.")
            return
        name = simpledialog.askstring(
            "Profile Baru",
            "Nama profile/akun baru (contoh: Akun-2, KedaiKopi, Personal):",
            parent=self)
        if not name:
            return
        try:
            eng.create_profile(name)
        except Exception as e:
            messagebox.showerror("Gagal", str(e))
            return
        self.refresh_profiles()
        self.cmb_profile.set(eng.list_profiles()[-1])

    def on_del_profile(self):
        eng = self.engine
        profile = self.cmb_profile.get().strip()
        if not profile:
            return
        if not messagebox.askyesno(
                "Hapus Profile",
                f"Hapus profile '{profile}'?\n\nSEMUA data, app, dan akun di "
                f"profile ini bakal hilang permanen."):
            return
        try:
            eng.delete_profile(profile)
        except Exception as e:
            messagebox.showerror("Gagal", str(e))
        self.refresh_profiles()

    def on_send_video(self):
        eng = self.engine
        inst = self._running_instance()
        if not inst:
            return
        start_dir = str(eng.p.shared_video_dir) if eng.p.shared_video_dir.exists() \
            else str(Path.home())
        files = filedialog.askopenfilenames(
            title="Pilih video yang mau dikirim ke Android",
            initialdir=start_dir,
            filetypes=[
                ("Video", "*.mp4 *.mov *.mkv *.webm *.avi *.3gp"),
                ("Semua file", "*.*"),
            ])
        if not files:
            return

        def job():
            results = eng.push_videos(inst, list(files))
            ok = [n for n, s in results if s.startswith("OK")]
            bad = [(n, s) for n, s in results if not s.startswith("OK")]
            eng.log(f"Selesai kirim: {len(ok)} sukses, {len(bad)} gagal.")
            for n, s in bad:
                eng.log(f"  GAGAL {n}: {s}")

        self._run_bg(job)

    def on_install_apk(self):
        eng = self.engine
        inst = self._running_instance()
        if not inst:
            return
        apk = filedialog.askopenfilename(
            title="Pilih file APK yang mau diinstall",
            filetypes=[("APK", "*.apk"), ("Semua file", "*.*")])
        if not apk:
            return

        def job():
            result = eng.install_apk(inst, apk)
            eng.log(f"Hasil install: {result}")

        self._run_bg(job)

    def on_open_folder(self):
        self.engine.open_folder(self.engine.p.shared_video_dir)

    def on_open_config(self):
        try:
            if os.name == "nt":
                os.startfile(str(self.engine.p.config_file))  # type: ignore
            else:
                self.engine._run(["xdg-open", str(self.engine.p.config_file)])
        except Exception as e:
            messagebox.showerror("Gagal", f"Gagal buka config: {e}")

    # ------------------------------------------------------------------
    # AUTO-UPDATE (dicek setelah app terbuka)
    # ------------------------------------------------------------------
    def _update_check_bg(self):
        if not getattr(sys, "frozen", False):
            self.engine.log("Mode source: auto-update aktif cuma di versi .exe.")
            return

        def work():
            info = ap_core.check_update()
            if info:
                self.after(0, lambda: self._show_update(info))

        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, info):
        self._update_info = info
        size_mb = info["size"] / (1024 * 1024)
        self.lbl_update.config(
            text=f"Versi baru {info['version']} tersedia ({size_mb:.0f} MB). "
                 f"Mau update sekarang?")
        self.frm_update.pack(fill="x", padx=10, before=self.frm_status)
        self.engine.log(f"Update tersedia: {info['version']}")

    def _do_update(self):
        if self.worker and self.worker.is_alive():
            self.engine.log("Tungguin proses lain selesai dulu ya...")
            return
        eng = self.engine
        running = [i.profile for i in eng.instances.values() if i.alive()]
        if running:
            messagebox.showwarning(
                "Matikan Android dulu",
                "Masih ada Android nyala: " + ", ".join(running) +
                "\nMatikan dulu (tombol Matikan Semua), baru update.")
            return
        if not self._update_info:
            return
        self._set_busy(True)
        info = self._update_info

        def work():
            dest = eng.p.app_dir / "AndroidPortable.exe.new"
            eng.log(f"Download update {info['version']}...")

            def prog(done, total):
                if total:
                    pct = int(done / total * 100)
                    eng.log(f"Progress: {pct}%")

            ok = ap_core.download_update(info["url"], dest, progress=prog)
            if ok:
                ap_core.make_update_bat(eng.p.app_dir)
                self.after(0, self._finish_update)
            else:
                eng.log("Gagal download update. Cek internet lalu coba lagi.")
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self):
        messagebox.showinfo(
            "Update Siap",
            "Update udah terunduh.\n\n"
            "Launcher bakal nutup dan memperbarui dirinya otomatis.\n"
            "Nanti buka lagi lewat 2-Buka-Launcher.bat ya.")
        try:
            os.startfile(str(self.engine.p.app_dir / "Update-Launcher.bat"))  # type: ignore
        except Exception as e:
            self.engine.log(f"Gagal jalanin updater: {e}")
        self.destroy()

    def _on_close(self):
        eng = self.engine
        running = [i.profile for i in eng.instances.values() if i.alive()]
        if running:
            ans = messagebox.askyesnocancel(
                "Keluar",
                "Masih ada Android yang nyala: " + ", ".join(running) + "\n\n"
                "Yes     = matiin semua Android lalu keluar\n"
                "No      = biarkan nyala, keluar aja\n"
                "Cancel = batal")
            if ans is None:
                return
            if ans:

                def job():
                    eng.stop_all()

                self._run_bg(job)
                self.after(500, self._wait_close)
                return
            else:
                eng.log("Android dibiarkan nyala. Tutup manual dari jendela "
                        "Android-nya kalau mau matiin.")
        self.destroy()

    def _wait_close(self):
        if self.worker and self.worker.is_alive():
            self.after(500, self._wait_close)
        else:
            self.destroy()


def main():
    engine = ap_core.AndroidEngine()
    app = App(engine)
    app.mainloop()


if __name__ == "__main__":
    main()
