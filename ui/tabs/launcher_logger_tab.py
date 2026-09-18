import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import time
import threading

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus
from services.profile_manager_service import ProfileManagerService
from services.logcat_service import LogcatService
from services.ghost_log_service import GhostLogService
from ui.panels.exec_run_panel import ExecRunPanel
from ui.widgets.tooltip import add_tooltip


class LauncherLoggerTab(ttk.Frame):
    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.app = ws.app
        self.raw_logs = []
        self.search_pos = "1.0"
        self._exec_tags = {}   # exe-Name -> Konsolen-Tag (Farbe pro Executable)
        self._session_fh = None  # gemeinsames Log (App + Logcat + Exe) fuer diese Session
        self._app_launched = False  # ob die App in dieser Sitzung gestartet wurde (Status-Punkt)

        data_dir = os.path.join(self.app.cfg.config.get("BASE_DIR", ""), "data")
        config_file = os.path.join(data_dir, "logger_profiles.json")
        self.profile_mgr = ProfileManagerService(config_file)

        self.logcat_service = LogcatService()
        self.ghost_log_service = GhostLogService()

        # Abonnements für Android Logcat, Frida USB und den neuen File-Stream[cite: 14]
        EventBus.subscribe("LOGCAT_LINE", lambda line: self.after(0, self._append_log, line))
        EventBus.subscribe("LOG_INFO", lambda msg: self.after(0, self._append_frida_log, msg))
        EventBus.subscribe("GHOST_LOG_LINE", lambda line: self.after(0, self._append_ghost_log, line))
        EventBus.subscribe("EXEC_OUTPUT", lambda data: self.after(0, self._append_exec_output, data))

        self.create_widgets()
        self.bind("<Destroy>", self.on_close)
        self._poll_status()   # Status-Punkte (App/Logcat/Exe) aktuell halten

    def _format_cmd(self, cmd):
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        app_name = pkg.split('.')[-1] if '.' in pkg else pkg
        cmd = cmd.replace("{APP_PACKAGE}", pkg)
        cmd = cmd.replace("{APP_NAME}", app_name)
        return cmd

    def create_widgets(self):
        # ===== Obere Zeile: App (links) · Executable (rechts) — 50/50 =====
        f_split = ttk.Frame(self)
        f_split.pack(fill="x", padx=10, pady=(5, 2))
        f_split.columnconfigure(0, weight=1)   # App  50 %
        f_split.columnconfigure(1, weight=1)   # Exe  50 %

        app_box = ttk.LabelFrame(f_split, text="▶ App Start")
        app_box.grid(row=0, column=0, sticky="nsew")
        f_intent = ttk.Frame(app_box)
        f_intent.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(f_intent, text="Intent:", width=8).pack(side="left")
        self.cb_intents = ttk.Combobox(f_intent, values=self.profile_mgr.profiles["intents"])
        self.cb_intents.pack(side="left", fill="x", expand=True, padx=4)
        if self.profile_mgr.profiles["intents"]: self.cb_intents.current(0)
        ttk.Button(f_intent, text="💾", width=3,
                   command=lambda: self.save_template("intents", self.cb_intents)).pack(side="left", padx=1)
        ttk.Button(f_intent, text="🗑", width=3,
                   command=lambda: self.delete_template("intents", self.cb_intents)).pack(side="left", padx=1)

        self.exec_panel = ExecRunPanel(f_split, self.app)
        self.exec_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # ===== Werkzeugzeile: Suche / Filter / Anzeige (kompakt, eine Zeile) =====
        f_tools = ttk.Frame(self)
        f_tools.pack(fill="x", padx=10, pady=(0, 2))
        ttk.Label(f_tools, text="Suchen:").pack(side="left")
        self.ent_search = ttk.Entry(f_tools, width=14)
        self.ent_search.pack(side="left", padx=3)
        self.ent_search.bind("<Return>", lambda e: self.do_search())
        ttk.Button(f_tools, text="🔍", width=3, command=self.do_search).pack(side="left", padx=1)
        ttk.Button(f_tools, text="⬇ Next", command=self.search_next).pack(side="left", padx=1)
        ttk.Separator(f_tools, orient="vertical").pack(side="left", fill="y", padx=5)
        ttk.Label(f_tools, text="Filter:").pack(side="left")
        self.ent_filter = ttk.Entry(f_tools, width=14)
        self.ent_filter.pack(side="left", padx=2)
        self.ent_filter.bind("<KeyRelease>", self.apply_filter)
        ttk.Label(f_tools, text="Exclude:").pack(side="left")
        self.ent_exclude = ttk.Entry(f_tools, width=14)
        self.ent_exclude.pack(side="left", padx=2)
        self.ent_exclude.bind("<KeyRelease>", self.apply_filter)
        ttk.Separator(f_tools, orient="vertical").pack(side="left", fill="y", padx=5)
        self.var_wrap = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_tools, text="Umbruch", variable=self.var_wrap, command=self.toggle_wrap).pack(side="left")
        b_savef = ttk.Button(f_tools, text="💾 Speichern", command=self.save_filtered_log)
        b_savef.pack(side="left", padx=(8, 2))
        add_tooltip(b_savef, "Speichert die AKTUELL angezeigte (gefilterte) Konsole in eine Datei.")
        ttk.Button(f_tools, text="🗑 Anzeige leeren", command=self.clear_console).pack(side="left", padx=2)
        ttk.Button(f_tools, text="📂 Archiv", command=self.open_archive).pack(side="left", padx=2)
        self.lbl_status = ttk.Label(f_tools, text="Status: Bereit", font=("Segoe UI", 9, "italic"), foreground="gray")
        self.lbl_status.pack(side="right", padx=5)

        # ===== Logcat-Konfiguration (unten): Early + Ghost + Logcat (breiter) =====
        f_logcat = ttk.Frame(self)
        f_logcat.pack(fill="x", padx=10, pady=(0, 3))
        self.var_early_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_logcat, text="Early Logcat", variable=self.var_early_log).pack(side="left", padx=(0, 8))
        self.var_ghost_stream = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_logcat, text="File-Stream (Ghost)", variable=self.var_ghost_stream).pack(side="left", padx=(0, 10))
        ttk.Label(f_logcat, text="Logcat:").pack(side="left")
        self.cb_logcats = ttk.Combobox(f_logcat, values=self.profile_mgr.profiles["logcats"], width=100)
        self.cb_logcats.pack(side="left", padx=4)
        if self.profile_mgr.profiles["logcats"]: self.cb_logcats.current(0)
        ttk.Button(f_logcat, text="💾", width=3,
                   command=lambda: self.save_template("logcats", self.cb_logcats)).pack(side="left", padx=1)
        ttk.Button(f_logcat, text="🗑", width=3,
                   command=lambda: self.delete_template("logcats", self.cb_logcats)).pack(side="left", padx=1)
        add_tooltip(self.cb_logcats, "Logcat-Filter (grep). {PID}/{APP_NAME}/{APP_PACKAGE} werden ersetzt.")

        # ===== Konsole mit Steuerung LINKS daneben (spart vertikalen Platz) =====
        ttk.Label(self, text="Gemeinsames Live-Log (App + Logcat + Executable)",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(2, 0))
        console_area = ttk.Frame(self)
        console_area.pack(fill="both", expand=True, padx=10, pady=(0, 5))

        # --- Steuerung (links neben der Konsole, kompakt untereinander) ---
        mid = ttk.LabelFrame(console_area, text="Steuerung")
        mid.pack(side="left", fill="y", padx=(0, 6))

        self.var_ms_app = tk.BooleanVar(value=True)
        self.var_ms_log = tk.BooleanVar(value=True)
        self.var_ms_exe = tk.BooleanVar(value=False)

        def _mk(rw, text, cmd, var, key, tip_btn):
            b = ttk.Button(mid, text=text, width=14, command=cmd)
            b.grid(row=rw, column=0, sticky="we", padx=(6, 2), pady=1)
            add_tooltip(b, tip_btn)
            dot = ttk.Label(mid, text="⚪", width=2)
            dot.grid(row=rw, column=1, padx=(0, 1), pady=1)
            setattr(self, "dot_" + key, dot)
            chk = ttk.Checkbutton(mid, variable=var)
            chk.grid(row=rw, column=2, padx=(0, 2), pady=1)
            add_tooltip(chk, "In „Multi-Start“ einbeziehen")
            setattr(self, "chk_" + key, chk)

        _mk(0, "▶ App starten", self.start_app_action, self.var_ms_app, "app",
            "Startet nur die App (Intent).")
        _mk(1, "▶ Logcat starten", self.start_logcat_action, self.var_ms_log, "log",
            "Startet nur Logcat (Aufzeichnung ins gemeinsame Log).")
        _mk(2, "▶ Exe starten", self.start_exe_action, self.var_ms_exe, "exe",
            "Deployt & startet das im Executable-Bereich gewählte Ziel.")

        tk.Frame(mid, width=2, bg="#8A8A8A").grid(row=0, column=3, rowspan=3, sticky="ns", padx=(2, 4), pady=2)

        self.btn_multi = ttk.Button(mid, text="▶▶ Multi-Start", command=self.start_multi)
        self.btn_multi.grid(row=3, column=0, columnspan=4, sticky="we", padx=6, pady=(3, 1))
        add_tooltip(self.btn_multi, "Startet gemeinsam alles, was angehakt ist "
                                    "(z. B. Logcat + App, Logcat + Exe, oder alle drei).")
        self.btn_stop_all = ttk.Button(mid, text="⏹ Alles stoppen", command=self.stop_all)
        self.btn_stop_all.grid(row=4, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 4))
        add_tooltip(self.btn_stop_all, "Stoppt Logcat/Ghost, das laufende Executable und force-stoppt die App.")

        # --- Konsole (rechts, füllt den restlichen Platz) ---
        f_console = ttk.Frame(console_area)
        f_console.pack(side="left", fill="both", expand=True)

        self.console = tk.Text(f_console, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), wrap="word")

        # Tags für Farbmarkierungen definieren
        self.console.tag_configure("search", background="white", foreground="black")
        self.console.tag_configure("frida_log", foreground="#E5C07B", font=("Consolas", 10, "bold"))
        self.console.tag_configure("error_log", foreground="#E06C75", font=("Consolas", 10, "bold"))
        self.console.tag_configure("ghost_log", foreground="#00FFFF", font=("Consolas", 10, "bold"))  # Helles Cyan
        self.console.tag_configure("exec_out", foreground="#98C379", font=("Consolas", 10, "bold"))   # Gruen (stdout)
        self.console.tag_configure("exec_sys", foreground="#888888", font=("Consolas", 10, "italic"))  # Grau (Status)

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

    def _check_log_filters(self, line: str, inc_query: list, exc_query: list) -> bool:
        lower_line = line.lower()
        if exc_query and any(ex in lower_line for ex in exc_query): return False
        if inc_query and not any(inc in lower_line for inc in inc_query): return False
        return True

    def apply_filter(self, event=None):
        inc_query = self.ent_filter.get().lower().split()
        exc_query = self.ent_exclude.get().lower().split()

        self.console.delete("1.0", tk.END)
        for line in self.raw_logs:
            if self._check_log_filters(line, inc_query, exc_query):
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

    def _resolve_pid(self, adb, base_dir):
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        res = CommandRunner.run_blocking(f'"{adb}" shell pidof {pkg}', cwd=base_dir)
        pid = res.stdout.strip()
        return pid if res.returncode == 0 and pid.isdigit() else None

    def _start_ghost_if_enabled(self, adb, base_dir):
        if self.var_ghost_stream.get():
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            self.console.insert(tk.END, "[*] File-Stream Bypass (Ghost) aktiviert. Horche auf /data/data/...\n",
                                "ghost_log")
            self.ghost_log_service.start_capture(adb, pkg, base_dir)

    def start_app_only(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        intent_cmd = self._format_cmd(self.cb_intents.get().strip())
        if not intent_cmd:
            return messagebox.showerror("Fehler", "Intent Kommando darf nicht leer sein.")
        self._start_ghost_if_enabled(adb, base_dir)
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
                return messagebox.showerror("Fehler", "App läuft nicht! PID konnte nicht ermittelt werden.")
            logcat_cmd = logcat_cmd.replace("{PID}", pid)

        self._start_ghost_if_enabled(adb, base_dir)
        self._start_capture_internal(adb, logcat_cmd, base_dir)

    def start_combined(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        intent_cmd = self._format_cmd(self.cb_intents.get().strip())
        logcat_cmd = self._format_cmd(self.cb_logcats.get().strip())

        if not intent_cmd or not logcat_cmd:
            return messagebox.showerror("Fehler", "Intent und Logcat Kommando dürfen nicht leer sein.")

        self.console.insert(tk.END, "[*] Leere alten Logcat-Puffer auf dem Gerät...\n")
        CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)

        self._start_ghost_if_enabled(adb, base_dir)

        if self.var_early_log.get():
            if "{PID}" in logcat_cmd:
                return messagebox.showerror("Konflikt",
                                            "Early Logging ist nicht möglich, wenn '{PID}' im Filter verwendet wird!")

            self.console.insert(tk.END, "[*] Early Logging: Starte Logcat VOR dem App-Launch...\n")
            self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)
            self._launch_app(adb, intent_cmd, base_dir)
        else:
            self.console.insert(tk.END, "[*] Starte App zuerst...\n")
            self._launch_app(adb, intent_cmd, base_dir)
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
            self.lbl_status.config(text=f"🔴 Recording to: {os.path.basename(log_file_path)}", foreground="red")
            self._refresh_run_status()
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim Starten von Logcat: {e}\n", "error_log")

    def _launch_app(self, adb, intent_cmd, base_dir):
        full_intent_cmd = f'"{adb}" shell {intent_cmd}'
        self.console.insert(tk.END, f"\n[*] Starte App: {full_intent_cmd}\n")
        self.console.see(tk.END)
        try:
            CommandRunner.run_background(full_intent_cmd, cwd=base_dir)
            self._app_launched = True
            self._refresh_run_status()
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim App Start: {e}\n", "error_log")

    def _append_frida_log(self, msg):
        if msg.startswith("[Frida]") or msg.startswith("[Frida ERROR]"):
            self.console.insert(tk.END, msg + "\n", "frida_log")
            self._write_session(msg)
            self.console.see(tk.END)
        elif msg.startswith("[!]"):
            self.console.insert(tk.END, msg + "\n", "error_log")
            self._write_session(msg)
            self.console.see(tk.END)

    def _append_ghost_log(self, line):
        self.raw_logs.append(line)
        self._write_session(line)
        inc_query = self.ent_filter.get().lower().split()
        exc_query = self.ent_exclude.get().lower().split()
        if self._check_log_filters(line, inc_query, exc_query):
            self.console.insert(tk.END, line + "\n", "ghost_log")
            if self.console.yview()[1] >= 0.98:
                self.console.see(tk.END)

    def _exec_color_tag(self, exe):
        """Stabile Farbe je Executable (fuer parallele Laeufe), sofern aktiviert."""
        if not self.app.cfg.config.get("EXEC_COLOR_PER_EXE", True):
            return "exec_out"
        if exe not in self._exec_tags:
            palette = ["#98C379", "#61AFEF", "#C678DD", "#56B6C2", "#D19A66", "#E5C07B"]
            color = palette[abs(hash(exe)) % len(palette)]
            tag = f"exec_c{len(self._exec_tags)}"
            self.console.tag_configure(tag, foreground=color, font=("Consolas", 10, "bold"))
            self._exec_tags[exe] = tag
        return self._exec_tags[exe]

    def _append_exec_output(self, data):
        """EXEC_OUTPUT vom DeviceExecService: farbig + getaggt als '[<exe>] <text>'."""
        exe = (data or {}).get("exe", "exe")
        stream = (data or {}).get("stream", "out")
        text = (data or {}).get("text", "")
        line = f"[{exe}] {text}"
        self.raw_logs.append(line)
        self._write_session(line)
        inc_query = self.ent_filter.get().lower().split()
        exc_query = self.ent_exclude.get().lower().split()
        if not self._check_log_filters(line, inc_query, exc_query):
            return
        if stream == "err":
            tag = "error_log"
        elif stream == "sys":
            tag = "exec_sys"
        else:
            tag = self._exec_color_tag(exe)
        self.console.insert(tk.END, line + "\n", tag)
        if self.console.yview()[1] >= 0.98:
            self.console.see(tk.END)

    # ================= Gemeinsames Log + gefiltertes Speichern =================
    def _session_path(self):
        archive_dir = getattr(self.app, 'current_archive_path', "") or \
            self.app.cfg.paths.get("ARCHIVE_DIR", "")
        os.makedirs(archive_dir, exist_ok=True)
        return os.path.join(archive_dir, f"session_{time.strftime('%Y%m%d-%H%M%S')}.log")

    def _write_session(self, line):
        """Schreibt JEDE Konsolenzeile (App/Logcat/Ghost/Exe) in EIN gemeinsames Session-Log."""
        try:
            if self._session_fh is None:
                self._session_fh = open(self._session_path(), "a", encoding="utf-8", errors="replace")
            self._session_fh.write(line + "\n")
            self._session_fh.flush()
        except Exception:
            pass

    def save_filtered_log(self):
        """Speichert die aktuell angezeigte (gefilterte) Konsole in eine wählbare Datei."""
        content = self.console.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("Speichern", "Konsole ist leer.")
            return
        path = filedialog.asksaveasfilename(
            title="Gefilterte Konsole speichern",
            defaultextension=".log",
            initialfile=f"console_{time.strftime('%Y%m%d-%H%M%S')}.log",
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("Alle", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", errors="replace") as f:
                f.write(content)
            self.lbl_status.config(text=f"Gespeichert: {os.path.basename(path)}", foreground="green")
        except Exception as e:
            messagebox.showerror("Speichern", f"Fehler: {e}")

    # ================= Steuerung: Einzel-Start / Multi-Start / Alles stoppen =================
    def _sys(self, msg):
        """Systemzeile in Konsole (grau) + gemeinsames Log."""
        self.console.insert(tk.END, msg + "\n", "exec_sys")
        self._write_session(msg)
        self.console.see(tk.END)

    def _device_ready(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        r = CommandRunner.run_blocking(f'"{adb}" get-state', ".", timeout=4)
        return r.returncode == 0 and "device" in (r.stdout or "")

    def _guard(self, fn):
        """Führt fn nur aus, wenn ein Gerät erreichbar ist — Gerätecheck im Thread (kein Freeze)."""
        def task():
            if not self._device_ready():
                self.app.after(0, lambda: (
                    self._sys("[!] Kein Gerät verbunden (adb get-state: Timeout/kein Gerät)."),
                    self.lbl_status.config(text="Kein Gerät", foreground="red")))
                return
            self.app.after(0, fn)
        threading.Thread(target=task, daemon=True).start()

    def start_app_action(self):
        self._guard(self.start_app_only)

    def start_logcat_action(self):
        self._guard(self.start_logcat_only)

    def start_exe_action(self):
        self._guard(self._start_exe)

    def _start_exe(self):
        try:
            self.exec_panel.deploy_selected()
        except Exception as e:
            self._sys(f"[!] ExeDeploy: {e}")
        self._refresh_run_status()

    def start_multi(self):
        if not (self.var_ms_app.get() or self.var_ms_log.get() or self.var_ms_exe.get()):
            messagebox.showinfo("Multi-Start", "Nichts ausgewählt — bitte rechts Checkboxen setzen.")
            return

        def run():
            if self.var_ms_app.get() and self.var_ms_log.get():
                self.start_combined()          # App + Logcat (inkl. Early-Logging-Logik)
            elif self.var_ms_app.get():
                self.start_app_only()
            elif self.var_ms_log.get():
                self.start_logcat_only()
            if self.var_ms_exe.get():
                self._start_exe()
            self._refresh_run_status()

        self._guard(run)

    def stop_all(self):
        # Logcat / Ghost sofort stoppen (lokal, kein Gerät nötig)
        try:
            self.stop_capture()
        except Exception:
            pass
        # Executable stoppen (lokal + pkill; pkill läuft mit Timeout im Service)
        try:
            lib = self.exec_panel.selected_lib()
            ctrl = getattr(self.app, "exec_run_controller", None)
            if lib and ctrl:
                ctrl.stop(lib)
        except Exception:
            pass

        # App force-stop (im Thread, mit Timeout — friert nicht ein)
        def task():
            adb = self.app.cfg.paths.get("ADB", "adb")
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            if pkg:
                CommandRunner.run_blocking(f'"{adb}" shell am force-stop {pkg}', ".", timeout=8)
            self._app_launched = False
            self.app.after(0, lambda: (self._sys("[*] Alles gestoppt (Logcat/Exe/App)."),
                                       self._refresh_run_status()))
        threading.Thread(target=task, daemon=True).start()

    # ---- Status-Punkte + Multi-Start-Klammer ----
    def _refresh_run_status(self):
        log_on = bool(getattr(self.logcat_service, "is_running", False))
        exe_on = False
        try:
            lib = self.exec_panel.selected_lib()
            ctrl = getattr(self.app, "exec_run_controller", None)
            exe_on = bool(lib and ctrl and ctrl.exec_service.is_running(lib.name))
        except Exception:
            pass
        if hasattr(self, "dot_log"):
            self.dot_log.config(text="🟢" if log_on else "⚪")
        if hasattr(self, "dot_app"):
            self.dot_app.config(text="🟢" if self._app_launched else "⚪")
        if hasattr(self, "dot_exe"):
            self.dot_exe.config(text="🟢" if exe_on else "⚪")

    def _poll_status(self):
        self._refresh_run_status()
        try:
            self.after(1500, self._poll_status)
        except Exception:
            pass

    def _insert_colored_line(self, line):
        tag = ""
        line_lower = line.lower()
        if "[ghost]" in line_lower:
            tag = "ghost_log"
        elif "frida" in line_lower:
            tag = "frida_log"
        elif "fatal" in line_lower or "crash" in line_lower or " exception " in line_lower:
            tag = "error_log"

        if tag:
            self.console.insert(tk.END, line + "\n", tag)
        else:
            self.console.insert(tk.END, line + "\n")

    def _append_log(self, line):
        self.raw_logs.append(line)
        self._write_session(line)
        inc_query = self.ent_filter.get().lower().split()
        exc_query = self.ent_exclude.get().lower().split()
        if self._check_log_filters(line, inc_query, exc_query):
            self._insert_colored_line(line)
            if self.console.yview()[1] >= 0.98:
                self.console.see(tk.END)

    def stop_capture(self):
        self.logcat_service.stop_capture()
        self.ghost_log_service.stop_capture()
        self.lbl_status.config(text="Status: Gestoppt", foreground="gray")
        self.console.insert(tk.END, "\n[*] Logging gestoppt.\n")
        self.console.see(tk.END)
        self._refresh_run_status()

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
            self.logcat_service.stop_capture()
        self.ghost_log_service.stop_capture()
        if self._session_fh:
            try:
                self._session_fh.close()
            except Exception:
                pass
            self._session_fh = None