import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import shutil
import threading

from core.application.event_bus import EventBus
from core.infrastructure.command_runner import CommandRunner


class AppManagerTab(ttk.Frame):
    def __init__(self, parent, source_dir, on_app_imported_callback):
        super().__init__(parent)
        self.source_dir = source_dir
        self.on_app_imported = on_app_imported_callback
        self.packages = []

        os.makedirs(self.source_dir, exist_ok=True)
        self.create_widgets()
        self.load_packages()

    def log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

    def create_widgets(self):
        left_frame = ttk.LabelFrame(self, text="1. APK vom Gerät extrahieren")
        left_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        search_frame = ttk.Frame(left_frame)
        search_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(search_frame, text="Suchen:").pack(side="left")
        self.ent_search = ttk.Entry(search_frame)
        self.ent_search.pack(side="left", fill="x", expand=True, padx=5)
        self.ent_search.bind("<KeyRelease>", self.filter_list)

        self.listbox = tk.Listbox(left_frame, selectmode=tk.SINGLE)
        self.listbox.pack(fill="both", expand=True, padx=5, pady=5)

        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill="x", padx=5, pady=5)
        ttk.Button(btn_frame, text="🔄 Refresh", command=self.load_packages).pack(side="left")
        ttk.Button(btn_frame, text="📂 Lokale APK", command=self.import_local_apk).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="📥 App ziehen (Pull)", command=self.pull_apk).pack(side="right")

        right_frame = ttk.LabelFrame(self, text="2. Session Status (Auto-Config)")
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        self.lbl_status = ttk.Label(right_frame, text="Warte auf App-Import...", font=("Segoe UI", 10, "italic"))
        self.lbl_status.pack(pady=15)

        self.stat_workspace = ttk.Label(right_frame, text="⚪ Arbeitsverzeichnis gesetzt")
        self.stat_workspace.pack(anchor="w", padx=20, pady=5)
        self.stat_arch = ttk.Label(right_frame, text="⚪ Architektur erkannt")
        self.stat_arch.pack(anchor="w", padx=20, pady=5)
        self.stat_logcat = ttk.Label(right_frame, text="⚪ Logcat PID-Filter konfiguriert")
        self.stat_logcat.pack(anchor="w", padx=20, pady=5)
        self.stat_api = ttk.Label(right_frame, text="⚪ API Profile (Spalten/Regeln) geladen")
        self.stat_api.pack(anchor="w", padx=20, pady=5)

    def load_packages(self):
        self.listbox.delete(0, tk.END)
        self.packages = []
        self.listbox.insert(tk.END, "Lade Apps... (Warte auf ADB)")
        self.update_idletasks()

        def task():
            try:
                # Da der Task asynchron läuft, blockiert ADB das UI nicht.
                result = CommandRunner.run_blocking("adb shell pm list packages -3", self.source_dir)

                if result.returncode != 0:
                    self.after(0, lambda: self._handle_adb_error("ADB reagiert nicht oder Gerät offline."))
                    return

                pkgs = [line.replace("package:", "").strip() for line in result.stdout.strip().split('\n') if
                        line.startswith("package:")]
                self.after(0, lambda p=pkgs: self._update_list(p))
            except Exception as e:
                err_msg = str(e)
                self.after(0, lambda m=err_msg: self._handle_adb_error(m))

        threading.Thread(target=task, daemon=True).start()

    def _update_list(self, pkgs):
        self.listbox.delete(0, tk.END)
        self.packages = pkgs
        for pkg in self.packages:
            self.listbox.insert(tk.END, pkg)

    def _handle_adb_error(self, e):
        self.listbox.delete(0, tk.END)
        self.listbox.insert(tk.END, "⚠️ ADB Fehler / Gerät offline")
        self.log(f"[!] ADB Fehler beim App-Laden: {e}")

    def filter_list(self, event):
        search = self.ent_search.get().lower()
        self.listbox.delete(0, tk.END)
        for pkg in self.packages:
            if search in pkg.lower():
                self.listbox.insert(tk.END, pkg)

    def import_local_apk(self):
        folderpath = filedialog.askdirectory(title="Ordner mit lokalen APKs auswählen (muss base.apk enthalten)")
        if not folderpath: return

        if not os.path.exists(os.path.join(folderpath, "base.apk")):
            return messagebox.showerror("Fehler",
                                        "Der ausgewählte Ordner muss zwingend die rohen APK-Dateien inkl. 'base.apk' enthalten (Nicht den entpackten Code-Ordner)!")

        pkg_name = os.path.basename(folderpath).replace(" ", "_")
        target_folder = os.path.join(self.source_dir, pkg_name)
        os.makedirs(target_folder, exist_ok=True)

        self.log(f"[*] Importiere lokale APKs aus Ordner: {pkg_name}...")
        self.lbl_status.config(text=f"Importiere {pkg_name}...", foreground="blue")
        self.update_idletasks()

        try:
            for f in os.listdir(folderpath):
                if f.endswith(".apk"):
                    src_file = os.path.join(folderpath, f)
                    dst_file = os.path.join(target_folder, f)
                    if os.path.abspath(src_file) != os.path.abspath(dst_file):
                        shutil.copy(src_file, dst_file)

            self.log(f"[+] Lokale APKs erfolgreich nach source/{pkg_name}/ geladen.")

            self.stat_workspace.config(text=f"🟢 Verzeichnis: source/{pkg_name}/")
            self.stat_arch.config(text=f"🟢 Architektur erkannt: LOCAL (Generisch)")
            self.stat_logcat.config(text=f"🟢 Logcat Filter gesetzt auf: {pkg_name}")
            self.stat_api.config(text=f"🟢 App-spezifische API Profile geladen")
            self.lbl_status.config(text="Session Setup für lokale App aktiv!", foreground="green")

            if self.on_app_imported:
                session_data = {
                    "package_name": pkg_name,
                    "workspace_path": target_folder,
                    "architecture": "LOCAL",
                    "local_split_name": pkg_name
                }
                self.on_app_imported(session_data)
        except Exception as e:
            self.log(f"[!] Fehler beim Kopieren der APKs: {e}")
            self.lbl_status.config(text="Fehler beim Import", foreground="red")
            messagebox.showerror("Import Fehler", f"Details: {e}")

    def pull_apk(self):
        sel = self.listbox.curselection()
        if not sel: return messagebox.showinfo("Hinweis", "Bitte zuerst eine App auswählen.")

        pkg = self.listbox.get(sel[0])
        target_folder = os.path.join(self.source_dir, pkg)
        os.makedirs(target_folder, exist_ok=True)

        self.log(f"[*] Ermittle Pfade für {pkg}...")
        self.lbl_status.config(text=f"Ziehe Dateien für {pkg}... Bitte warten.", foreground="blue")
        self.update_idletasks()

        def task():
            try:
                path_result = CommandRunner.run_blocking(f"adb shell pm path {pkg}", target_folder)

                if path_result.returncode != 0:
                    self.after(0, lambda: self.lbl_status.config(text="Fehler beim Ermitteln der Pfade.",
                                                                 foreground="red"))
                    self.log(f"[!] ADB Error: {path_result.stderr}")
                    return

                paths = [line.replace("package:", "").strip() for line in path_result.stdout.strip().split('\n') if
                         line]

                if not paths:
                    self.after(0,
                               lambda: self.lbl_status.config(text="Fehler: Keine Pfade gefunden.", foreground="red"))
                    return

                for apk_path in paths:
                    self.log(f"[*] Ziehe {apk_path}...")
                    pull_res = CommandRunner.run_blocking(f"adb pull \"{apk_path}\" \"{target_folder}\"", target_folder)
                    if pull_res.returncode != 0:
                        self.log(f"[!] Warnung/Fehler beim Pull von {apk_path}: {pull_res.stderr}")

                self.after(0, lambda: self.lbl_status.config(text="Erfolgreich gezogen! Analysiere...",
                                                             foreground="orange"))

                detected_arch = "ARM32"
                for path in paths:
                    if "arm64_v8a" in path or "arm64" in path:
                        detected_arch = "ARM64";
                        break
                    elif "x86_64" in path:
                        detected_arch = "x86_64";
                        break

                self.after(0, lambda: self._finalize_pull(pkg, target_folder, detected_arch))

            except Exception as e:
                self.log(f"[!] Systemfehler beim Pull: {e}")
                self.after(0, lambda: self.lbl_status.config(text="Systemfehler beim Pull", foreground="red"))

        # Asynchron ausführen, da adb pull mehrere hundert Megabyte kopieren kann
        threading.Thread(target=task, daemon=True).start()

    def _finalize_pull(self, pkg, target_folder, detected_arch):
        self.stat_workspace.config(text=f"🟢 Verzeichnis: source/{pkg}/")
        self.stat_arch.config(text=f"🟢 Architektur erkannt: {detected_arch}")
        self.stat_logcat.config(text=f"🟢 Logcat Filter gesetzt auf: {pkg}")
        self.stat_api.config(text=f"🟢 App-spezifische API Profile geladen")
        self.lbl_status.config(text=f"Session Setup für {pkg} aktiv!", foreground="green")

        if self.on_app_imported:
            self.on_app_imported({
                "package_name": pkg,
                "workspace_path": target_folder,
                "architecture": detected_arch,
                "target_lib": "libflutter.so"
            })