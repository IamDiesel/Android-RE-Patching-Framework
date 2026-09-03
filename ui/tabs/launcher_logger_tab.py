import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus
from services.profile_manager_service import ProfileManagerService
from services.logcat_service import LogcatService


class LauncherLoggerTab(ttk.Frame):
    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.app = ws.app
        self.raw_logs = []
        self.search_pos = "1.0"

        data_dir = os.path.join(self.app.cfg.config.get("BASE_DIR", ""), "data")
        config_file = os.path.join(data_dir, "logger_profiles.json")
        self.profile_mgr = ProfileManagerService(config_file)
        self.logcat_service = LogcatService()

        # Abonnements für Android Logcat UND externe Python EventBus-Nachrichten (z.B. Frida via USB)
        EventBus.subscribe("LOGCAT_LINE", lambda line: self.after(0, self._append_log, line))
        EventBus.subscribe("LOG_INFO", lambda msg: self.after(0, self._append_frida_log, msg))

        self.create_widgets()
        self.bind("<Destroy>", self.on_close)

    def _format_cmd(self, cmd):
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        app_name = pkg.split('.')[-1] if '.' in pkg else pkg
        cmd = cmd.replace("{APP_PACKAGE}", pkg)
        cmd = cmd.replace("{APP_NAME}", app_name)
        return cmd

    def create_widgets(self):
        f_top = ttk.Frame(self)
        f_top.pack(fill="x", padx=10, pady=5)

        # --- Intent / App Start Row ---
        f_intent = ttk.Frame(f_top)
        f_intent.pack(fill="x", pady=2)
        ttk.Label(f_intent, text="App Start (Intent):", width=18).pack(side="left")

        self.cb_intents = ttk.Combobox(f_intent, values=self.profile_mgr.profiles["intents"])
        self.cb_intents.pack(side="left", fill="x", expand=True, padx=5)
        if self.profile_mgr.profiles["intents"]: self.cb_intents.current(0)

        ttk.Button(f_intent, text="💾 Speichern", command=lambda: self.save_template("intents", self.cb_intents)).pack(
            side="left", padx=2)
        ttk.Button(f_intent, text="🗑 Löschen", command=lambda: self.delete_template("intents", self.cb_intents)).pack(
            side="left", padx=2)

        # --- Logcat Shell Row ---
        f_logcat = ttk.Frame(f_top)
        f_logcat.pack(fill="x", pady=2)
        ttk.Label(f_logcat, text="ADB Logcat Filter:", width=18).pack(side="left")

        self.cb_logcats = ttk.Combobox(f_logcat, values=self.profile_mgr.profiles["logcats"])
        self.cb_logcats.pack(side="left", fill="x", expand=True, padx=5)
        if self.profile_mgr.profiles["logcats"]: self.cb_logcats.current(0)

        ttk.Button(f_logcat, text="💾 Speichern", command=lambda: self.save_template("logcats", self.cb_logcats)).pack(
            side="left", padx=2)
        ttk.Button(f_logcat, text="🗑 Löschen", command=lambda: self.delete_template("logcats", self.cb_logcats)).pack(
            side="left", padx=2)

        # --- Tool Row (Search, Filter, Wrap) ---
        f_tools = ttk.Frame(f_top)
        f_tools.pack(fill="x", pady=5)

        ttk.Label(f_tools, text="Suchen:").pack(side="left")
        self.ent_search = ttk.Entry(f_tools, width=20)
        self.ent_search.pack(side="left", padx=5)
        self.ent_search.bind("<Return>", lambda e: self.do_search())
        ttk.Button(f_tools, text="🔍", width=3, command=self.do_search).pack(side="left", padx=1)
        ttk.Button(f_tools, text="⬇ Next", command=self.search_next).pack(side="left", padx=1)

        ttk.Separator(f_tools, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(f_tools, text="UI-Filter (Grep):").pack(side="left")
        self.ent_filter = ttk.Entry(f_tools, width=20)
        self.ent_filter.pack(side="left", padx=5)
        self.ent_filter.bind("<KeyRelease>", self.apply_filter)

        ttk.Separator(f_tools, orient="vertical").pack(side="left", fill="y", padx=10)

        self.var_wrap = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_tools, text="Zeilenumbruch", variable=self.var_wrap, command=self.toggle_wrap).pack(
            side="left")

        # --- Controls Row (GETRENNTE BUTTONS) ---
        f_ctrl = ttk.Frame(f_top)
        f_ctrl.pack(fill="x", pady=5)

        self.btn_start_app = ttk.Button(f_ctrl, text="▶ App Starten", command=self.start_app_only)
        self.btn_start_app.pack(side="left", padx=2)

        self.btn_start_log = ttk.Button(f_ctrl, text="▶ Logcat Starten", command=self.start_logcat_only)
        self.btn_start_log.pack(side="left", padx=2)

        self.btn_start_combo = ttk.Button(f_ctrl, text="▶ Kombiniert (App + Log)", command=self.start_combined)
        self.btn_start_combo.pack(side="left", padx=2)

        self.btn_stop = ttk.Button(f_ctrl, text="⏹ Stop Logging", command=self.stop_capture, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        ttk.Separator(f_ctrl, orient="vertical").pack(side="left", fill="y", padx=5)

        ttk.Button(f_ctrl, text="🗑 Anzeige leeren", command=self.clear_console).pack(side="left", padx=5)
        ttk.Button(f_ctrl, text="📂 Archiv (PID) öffnen", command=self.open_archive).pack(side="right", padx=5)

        # --- Status ---
        self.lbl_status = ttk.Label(f_top, text="Status: Bereit", font=("Segoe UI", 9, "italic"), foreground="gray")
        self.lbl_status.pack(anchor="w", padx=5, pady=2)

        # --- Live Console with Scrollbars ---
        f_console = ttk.Frame(self)
        f_console.pack(fill="both", expand=True, padx=10, pady=5)

        self.console = tk.Text(f_console, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), wrap="word")

        # Tags für Farbmarkierungen definieren
        self.console.tag_configure("search", background="white", foreground="black")
        self.console.tag_configure("frida_log", foreground="#E5C07B", font=("Consolas", 10, "bold"))  # Warn-Gelb
        self.console.tag_configure("error_log", foreground="#E06C75", font=("Consolas", 10, "bold"))  # Error-Rot

        scroll_y = ttk.Scrollbar(f_console, orient="vertical", command=self.console.yview)
        scroll_y.pack(side="right", fill="y")
        scroll_x = ttk.Scrollbar(f_console, orient="horizontal", command=self.console.xview)
        scroll_x.pack(side="bottom", fill="x")

        self.console.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.console.pack(side="left", fill="both", expand=True)

    def toggle_wrap(self):
        if self.var_wrap.get():
            self.console.configure(wrap="word")
        else:
            self.console.configure(wrap="none")

    def do_search(self):
        self.console.tag_remove("search", "1.0", tk.END)
        query = self.ent_search.get()
        if not query: return
        start, first = "1.0", None
        while True:
            pos = self.console.search(query, start, stopindex=tk.END, nocase=True)
            if not pos: break
            end = f"{pos}+{len(query)}c"
            self.console.tag_add("search", pos, end)
            if not first: first = pos
            start = end
        if first:
            self.console.see(first)
            self.search_pos = first

    def search_next(self):
        query = self.ent_search.get()
        if not query: return
        pos = self.console.search(query, f"{self.search_pos}+1c", stopindex=tk.END, nocase=True)
        if pos:
            self.console.see(pos)
            self.search_pos = pos
        else:
            self.do_search()

    def apply_filter(self, event=None):
        query = self.ent_filter.get().lower()
        self.console.delete("1.0", tk.END)
        if not query:
            for line in self.raw_logs:
                self._insert_colored_line(line)
        else:
            filtered = [line for line in self.raw_logs if query in line.lower()]
            for line in filtered:
                self._insert_colored_line(line)
        self.console.see(tk.END)
        self.do_search()

    def save_template(self, group, combobox):
        if self.profile_mgr.add_template(group, combobox.get()):
            combobox["values"] = self.profile_mgr.profiles[group]
            messagebox.showinfo("Gespeichert", "Vorlage erfolgreich gespeichert!")

    def delete_template(self, group, combobox):
        if self.profile_mgr.remove_template(group, combobox.get()):
            combobox["values"] = self.profile_mgr.profiles[group]
            combobox.set("")

    # === NEUE LOGIK FÜR PID-RESOLVING & START ===

    def _resolve_pid(self, adb, base_dir):
        """Führt asynchron adb shell pidof aus und holt die echte Prozess ID."""
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        res = CommandRunner.run_blocking(f'"{adb}" shell pidof {pkg}', cwd=base_dir)
        pid = res.stdout.strip()
        return pid if res.returncode == 0 and pid.isdigit() else None

    def start_app_only(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        intent_cmd = self._format_cmd(self.cb_intents.get().strip())
        if not intent_cmd:
            return messagebox.showerror("Fehler", "Intent Kommando darf nicht leer sein.")
        self._launch_app(adb, intent_cmd, base_dir)

    def start_logcat_only(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        logcat_cmd = self._format_cmd(self.cb_logcats.get().strip())

        if not logcat_cmd:
            return messagebox.showerror("Fehler", "Logcat Kommando darf nicht leer sein.")

        if "{PID}" in logcat_cmd:
            pid = self._resolve_pid(adb, base_dir)
            if not pid:
                return messagebox.showerror("Fehler",
                                            "App läuft nicht! PID konnte nicht ermittelt werden.\nBitte starte zuerst die App (z.B. per Button oder am Handy).")
            logcat_cmd = logcat_cmd.replace("{PID}", pid)

        self._start_capture_internal(adb, logcat_cmd, base_dir)

    def start_combined(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        intent_cmd = self._format_cmd(self.cb_intents.get().strip())
        logcat_cmd = self._format_cmd(self.cb_logcats.get().strip())

        if not intent_cmd or not logcat_cmd:
            return messagebox.showerror("Fehler", "Intent und Logcat Kommando dürfen nicht leer sein.")

        # Alten Puffer auf Gerät leeren
        self.console.insert(tk.END, "[*] Leere alten Logcat-Puffer auf dem Gerät...\n")
        CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)

        # 1. Start App
        self._launch_app(adb, intent_cmd, base_dir)

        # 2. Polling-Schleife für Logcat falls {PID} verlangt wird
        if "{PID}" in logcat_cmd:
            self._delayed_pid_logcat_start(adb, logcat_cmd, base_dir, attempts=6)
        else:
            self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)

    def _delayed_pid_logcat_start(self, adb, logcat_cmd, base_dir, attempts):
        if attempts <= 0:
            self.console.insert(tk.END, "[!] Timeout: Konnte PID der App nach dem Start nicht ermitteln!\n",
                                "error_log")
            return

        pid = self._resolve_pid(adb, base_dir)
        if pid:
            logcat_cmd = logcat_cmd.replace("{PID}", pid)
            self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)
        else:
            self.console.insert(tk.END, f"[*] Warte auf App-Prozess... (noch {attempts} Versuche)\n", "frida_log")
            self.after(500, lambda: self._delayed_pid_logcat_start(adb, logcat_cmd, base_dir, attempts - 1))

    def _start_capture_internal(self, adb, logcat_cmd, base_dir, skip_clear=False):
        if not skip_clear:
            self.console.insert(tk.END, "[*] Leere alten Logcat-Puffer auf dem Gerät...\n")
            CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)

        archive_dir = getattr(self.app, 'current_archive_path', self.app.cfg.paths.get("ARCHIVE_DIR", ""))

        try:
            log_file_path = self.logcat_service.start_capture(adb, logcat_cmd, archive_dir)
            self.console.insert(tk.END, f"[*] Starte Logcat: \"{adb}\" shell \"{logcat_cmd}\"\n")

            self.btn_start_app.config(state="disabled")
            self.btn_start_log.config(state="disabled")
            self.btn_start_combo.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.lbl_status.config(text=f"🔴 Recording to: {os.path.basename(log_file_path)}", foreground="red")
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim Starten von Logcat: {e}\n", "error_log")

    def _launch_app(self, adb, intent_cmd, base_dir):
        full_intent_cmd = f'"{adb}" shell {intent_cmd}'
        self.console.insert(tk.END, f"\n[*] Starte App: {full_intent_cmd}\n")
        self.console.see(tk.END)
        try:
            CommandRunner.run_background(full_intent_cmd, cwd=base_dir)
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim App Start: {e}\n", "error_log")

    # === LOG FORMATIERUNG & FARBEN ===

    def _append_frida_log(self, msg):
        """Wird ausgelöst, wenn EventBus "LOG_INFO" empfängt (z.B. Frida via USB Python-Bridge)"""
        if msg.startswith("[Frida]") or msg.startswith("[Frida ERROR]"):
            self.console.insert(tk.END, msg + "\n", "frida_log")
            self.console.see(tk.END)
        elif msg.startswith("[!]"):
            self.console.insert(tk.END, msg + "\n", "error_log")
            self.console.see(tk.END)

    def _insert_colored_line(self, line):
        """Hilfsfunktion zum Einsetzen nativer Logcat-Zeilen mit Farbe"""
        tag = ""
        line_lower = line.lower()
        if "frida" in line_lower:
            tag = "frida_log"
        elif "fatal" in line_lower or "crash" in line_lower or " exception " in line_lower:
            tag = "error_log"

        if tag:
            self.console.insert(tk.END, line, tag)
        else:
            self.console.insert(tk.END, line)

    def _append_log(self, line):
        """Wird ausgelöst, wenn EventBus "LOGCAT_LINE" empfängt"""
        self.raw_logs.append(line)
        query = self.ent_filter.get().lower()
        if not query or query in line.lower():
            self._insert_colored_line(line)
            # Auto-Scroll nur, wenn wir sowieso ganz unten sind
            if self.console.yview()[1] >= 0.98:
                self.console.see(tk.END)

    def stop_capture(self):
        self.logcat_service.stop_capture()
        self.btn_start_app.config(state="normal")
        self.btn_start_log.config(state="normal")
        self.btn_start_combo.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="Status: Gestoppt", foreground="gray")
        self.console.insert(tk.END, "\n[*] Logging gestoppt.\n")
        self.console.see(tk.END)

    def clear_console(self):
        self.raw_logs.clear()
        self.console.delete("1.0", tk.END)

    def open_archive(self):
        archive_dir = getattr(self.app, 'current_archive_path', self.app.cfg.paths.get("ARCHIVE_DIR", ""))
        import subprocess
        if os.name == 'nt':
            os.startfile(archive_dir)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', archive_dir])
        else:
            subprocess.Popen(['xdg-open', archive_dir])

    def on_close(self, event=None):
        if self.logcat_service.is_running:
            self.stop_capture()