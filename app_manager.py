import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import os


class AppManagerTab(ttk.Frame):
    def __init__(self, parent, source_dir, log_callback, on_app_imported_callback):
        super().__init__(parent)
        self.source_dir = source_dir
        self.log = log_callback
        self.on_app_imported = on_app_imported_callback  # Callback an die Haupt-GUI
        self.packages = []

        os.makedirs(self.source_dir, exist_ok=True)

        self.create_widgets()
        self.load_packages()

    def create_widgets(self):
        # --- Linke Seite: Liste und Download ---
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
        ttk.Button(btn_frame, text="📥 App ziehen (Pull)", command=self.pull_apk).pack(side="right")

        # --- Rechte Seite: Automatisches Session-Setup ---
        right_frame = ttk.LabelFrame(self, text="2. Session Status (Auto-Config)")
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        self.lbl_status = ttk.Label(right_frame, text="Warte auf App-Import...", font=("Segoe UI", 10, "italic"))
        self.lbl_status.pack(pady=15)

        # Status Indikatoren für die Einstellungen
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
        try:
            result = subprocess.check_output("adb shell pm list packages -3", shell=True, text=True)
            for line in result.strip().split('\n'):
                if line.startswith("package:"):
                    pkg = line.replace("package:", "").strip()
                    self.packages.append(pkg)
                    self.listbox.insert(tk.END, pkg)
        except subprocess.CalledProcessError:
            self.log("[!] ADB Fehler: Konnte Paketliste nicht laden.")

    def filter_list(self, event):
        search = self.ent_search.get().lower()
        self.listbox.delete(0, tk.END)
        for pkg in self.packages:
            if search in pkg.lower():
                self.listbox.insert(tk.END, pkg)

    def pull_apk(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Hinweis", "Bitte zuerst eine App aus der Liste auswählen.")
            return

        pkg = self.listbox.get(sel[0])
        target_folder = os.path.join(self.source_dir, pkg)
        os.makedirs(target_folder, exist_ok=True)

        self.log(f"[*] Ermittle Pfade für {pkg}...")
        self.lbl_status.config(text=f"Ziehe Dateien für {pkg}...", foreground="blue")
        self.update_idletasks()  # UI erzwingen

        try:
            path_result = subprocess.check_output(f"adb shell pm path {pkg}", shell=True, text=True)
            paths = [line.replace("package:", "").strip() for line in path_result.strip().split('\n') if line]

            if not paths:
                self.log(f"[!] Keine APK-Pfade gefunden für {pkg}")
                self.lbl_status.config(text="Fehler: Keine Pfade gefunden.", foreground="red")
                return

            for apk_path in paths:
                self.log(f"[*] Ziehe {apk_path}...")
                subprocess.run(f"adb pull \"{apk_path}\" \"{target_folder}\"", shell=True)

            self.log(f"[+] App erfolgreich nach source/{pkg}/ gezogen.")
            self.lbl_status.config(text="Erfolgreich gezogen! Analysiere...", foreground="orange")

            # --- Analyse der gezogenen Dateien für die Auto-Config ---
            self.analyze_and_apply_settings(pkg, target_folder, paths)

        except subprocess.CalledProcessError as e:
            self.log(f"[!] Fehler beim Pull: {e}")
            self.lbl_status.config(text="Fehler beim Download", foreground="red")

    def analyze_and_apply_settings(self, pkg_name, folder_path, apk_paths):
        """Scannt die heruntergeladenen Dateien und leitet Architektur und Workspace ab."""

        # 1. Architektur erkennen (Anhand der Split-Namen)
        detected_arch = "ARM32"  # Fallback
        for path in apk_paths:
            if "arm64_v8a" in path or "arm64" in path:
                detected_arch = "ARM64"
                break
            elif "x86_64" in path:
                detected_arch = "x86_64"
                break

        # Update UI Status
        self.stat_workspace.config(text=f"🟢 Verzeichnis: source/{pkg_name}/")
        self.stat_arch.config(text=f"🟢 Architektur erkannt: {detected_arch}")
        self.stat_logcat.config(text=f"🟢 Logcat Filter gesetzt auf: {pkg_name}")
        self.stat_api.config(text=f"🟢 App-spezifische API Profile geladen")

        self.lbl_status.config(text=f"Session Setup für {pkg_name} aktiv!", foreground="green")

        # 2. Callback an die Haupt-GUI feuern, damit die anderen Reiter aktualisiert werden
        if self.on_app_imported:
            session_data = {
                "package_name": pkg_name,
                "workspace_path": folder_path,
                "architecture": detected_arch,
                "target_lib": "libflutter.so"  # Standard für unser Flutter-Szenario
            }
            self.on_app_imported(session_data)