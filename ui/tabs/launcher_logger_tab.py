import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import time
import threading

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus
from ui.panels.exec_run_panel import ExecRunPanel
from ui.widgets.tooltip import add_tooltip


class LauncherLoggerTab(ttk.Frame):
    """Duenne VIEW fuer das Live-Log. Der gesamte Zustand (laufende Captures, Log-Historie,
    Auswahl, Session-Datei) lebt im persistenten LiveLogModel (App-Ebene). Diese View bindet
    sich nur daran und rendert daraus, damit andocken/teilen/abdocken verlustfrei ist."""

    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.app = ws.app
        # Persistentes Modell holen/erzeugen (Zustand NICHT im Widget)
        if getattr(self.app, "live_log_model", None) is None:
            from core.application.live_log_model import LiveLogModel
            self.app.live_log_model = LiveLogModel(self.app)
        self.model = self.app.live_log_model
        self.profile_mgr = self.model.profile_mgr
        self.logcat_service = self.model.logcat_service
        self.ghost_log_service = self.model.ghost_log_service
        self.search_pos = "1.0"
        self._known_tags = set()

        self.create_widgets()
        self._load_selections()
        self._backfill()
        self.model.add_view(self)
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
        f_split.columnconfigure(0, weight=1)
        f_split.columnconfigure(1, weight=1)

        app_box = ttk.LabelFrame(f_split, text="▶ App Start")
        app_box.grid(row=0, column=0, sticky="nsew")
        f_intent = ttk.Frame(app_box)
        f_intent.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(f_intent, text="Intent:", width=8).pack(side="left")
        self.cb_intents = ttk.Combobox(f_intent, values=self.profile_mgr.profiles["intents"])
        self.cb_intents.pack(side="left", fill="x", expand=True, padx=4)
        if self.profile_mgr.profiles["intents"]: self.cb_intents.current(0)
        self.cb_intents.bind("<<ComboboxSelected>>", lambda e: self._save_selections())
        self.cb_intents.bind("<KeyRelease>", lambda e: self._save_selections())
        ttk.Button(f_intent, text="💾", width=3,
                   command=lambda: self.save_template("intents", self.cb_intents)).pack(side="left", padx=1)
        ttk.Button(f_intent, text="🗑", width=3,
                   command=lambda: self.delete_template("intents", self.cb_intents)).pack(side="left", padx=1)

        self.exec_panel = ExecRunPanel(f_split, self.app)
        self.exec_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        # ===== Werkzeugzeile: Suche / Filter / Anzeige =====
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
        ttk.Checkbutton(f_tools, text="Umbruch", variable=self.var_wrap,
                        command=self.toggle_wrap).pack(side="left")
        b_savef = ttk.Button(f_tools, text="💾 Speichern", command=self.save_filtered_log)
        b_savef.pack(side="left", padx=(8, 2))
        add_tooltip(b_savef, "Speichert die AKTUELL angezeigte (gefilterte) Konsole in eine Datei.")
        ttk.Button(f_tools, text="🗑 Anzeige leeren", command=self.clear_console).pack(side="left", padx=2)
        ttk.Button(f_tools, text="📂 Archiv", command=self.open_archive).pack(side="left", padx=2)
        self.lbl_status = ttk.Label(f_tools, text="Status: Bereit",
                                    font=("Segoe UI", 9, "italic"), foreground="gray")
        self.lbl_status.pack(side="right", padx=5)

        # ===== Logcat-Konfiguration =====
        f_logcat = ttk.Frame(self)
        f_logcat.pack(fill="x", padx=10, pady=(0, 3))
        self.var_early_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_logcat, text="Early Logcat", variable=self.var_early_log,
                        command=self._save_selections).pack(side="left", padx=(0, 8))
        self.var_ghost_stream = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_logcat, text="File-Stream (Ghost)", variable=self.var_ghost_stream,
                        command=self._save_selections).pack(side="left", padx=(0, 10))
        ttk.Label(f_logcat, text="Logcat:").pack(side="left")
        self.cb_logcats = ttk.Combobox(f_logcat, values=self.profile_mgr.profiles["logcats"], width=100)
        self.cb_logcats.pack(side="left", padx=4)
        if self.profile_mgr.profiles["logcats"]: self.cb_logcats.current(0)
        self.cb_logcats.bind("<<ComboboxSelected>>", lambda e: self._save_selections())
        self.cb_logcats.bind("<KeyRelease>", lambda e: self._save_selections())
        ttk.Button(f_logcat, text="💾", width=3,
                   command=lambda: self.save_template("logcats", self.cb_logcats)).pack(side="left", padx=1)
        ttk.Button(f_logcat, text="🗑", width=3,
                   command=lambda: self.delete_template("logcats", self.cb_logcats)).pack(side="left", padx=1)
        add_tooltip(self.cb_logcats, "Logcat-Filter (grep). {PID}/{APP_NAME}/{APP_PACKAGE} werden ersetzt.")

        # ===== Konsole mit Steuerung links =====
        ttk.Label(self, text="Gemeinsames Live-Log (App + Logcat + Executable)",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(2, 0))
        console_area = ttk.Frame(self)
        console_area.pack(fill="both", expand=True, padx=10, pady=(0, 5))

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
            chk = ttk.Checkbutton(mid, variable=var, command=self._save_selections)
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

        f_console = ttk.Frame(console_area)
        f_console.pack(side="left", fill="both", expand=True)

        self.console = tk.Text(f_console, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), wrap="word")

        # Statische Tags
        self.console.tag_configure("search", background="white", foreground="black")
        self.console.tag_configure("frida_log", foreground="#E5C07B", font=("Consolas", 10, "bold"))
        self.console.tag_configure("error_log", foreground="#E06C75", font=("Consolas", 10, "bold"))
        self.console.tag_configure("ghost_log", foreground="#00FFFF", font=("Consolas", 10, "bold"))
        self.console.tag_configure("exec_out", foreground="#98C379", font=("Consolas", 10, "bold"))
        self.console.tag_configure("exec_sys", foreground="#888888", font=("Consolas", 10, "italic"))
        for t in ("search", "frida_log", "error_log", "ghost_log", "exec_out", "exec_sys"):
            self._known_tags.add(t)

        scroll_y = ttk.Scrollbar(f_console, orient="vertical", command=self.console.yview)
        scroll_y.pack(side="right", fill="y")
        scroll_x = ttk.Scrollbar(f_console, orient="horizontal", command=self.console.xview)
        scroll_x.pack(side="bottom", fill="x")
        self.console.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.console.pack(side="left", fill="both", expand=True)

    # ================= Auswahl laden/speichern (im Modell) =================
    def _load_selections(self):
        s = self.model.selections
        if not s:
            return
        try:
            if "intent" in s: self.cb_intents.set(s["intent"])
            if "logcat" in s: self.cb_logcats.set(s["logcat"])
            if "filter" in s:
                self.ent_filter.delete(0, tk.END); self.ent_filter.insert(0, s["filter"])
            if "exclude" in s:
                self.ent_exclude.delete(0, tk.END); self.ent_exclude.insert(0, s["exclude"])
            if "wrap" in s: self.var_wrap.set(s["wrap"]); self.toggle_wrap()
            if "ms_app" in s: self.var_ms_app.set(s["ms_app"])
            if "ms_log" in s: self.var_ms_log.set(s["ms_log"])
            if "ms_exe" in s: self.var_ms_exe.set(s["ms_exe"])
            if "early" in s: self.var_early_log.set(s["early"])
            if "ghost" in s: self.var_ghost_stream.set(s["ghost"])
        except Exception:
            pass

    def _save_selections(self, *args):
        try:
            self.model.selections.update({
                "intent": self.cb_intents.get(),
                "logcat": self.cb_logcats.get(),
                "filter": self.ent_filter.get(),
                "exclude": self.ent_exclude.get(),
                "wrap": bool(self.var_wrap.get()),
                "ms_app": bool(self.var_ms_app.get()),
                "ms_log": bool(self.var_ms_log.get()),
                "ms_exe": bool(self.var_ms_exe.get()),
                "early": bool(self.var_early_log.get()),
                "ghost": bool(self.var_ghost_stream.get()),
            })
        except Exception:
            pass

    # ================= Rendering aus dem Modell =================
    def _ensure_tag(self, rtag):
        if not rtag or rtag in self._known_tags:
            return
        color = self.model.rtag_colors.get(rtag)
        if color:
            try:
                self.console.tag_configure(rtag, foreground=color, font=("Consolas", 10, "bold"))
            except Exception:
                pass
        self._known_tags.add(rtag)

    def _passes_filter(self, line):
        inc = self.ent_filter.get().lower().split()
        exc = self.ent_exclude.get().lower().split()
        return self._check_log_filters(line, inc, exc)

    def on_line(self, line, rtag=""):
        """Vom Modell aufgerufen, wenn eine neue Zeile ankommt."""
        try:
            self._ensure_tag(rtag)
            if not self._passes_filter(line):
                return
            at_bottom = True
            try:
                at_bottom = self.console.yview()[1] >= 0.98
            except Exception:
                pass
            if rtag:
                self.console.insert(tk.END, line + "\n", rtag)
            else:
                self.console.insert(tk.END, line + "\n")
            if at_bottom:
                self.console.see(tk.END)
        except Exception:
            pass

    def _backfill(self):
        """Gesamte Historie des Modells (gefiltert) in die Konsole rendern."""
        try:
            self.console.delete("1.0", tk.END)
            for line, rtag in list(self.model.raw_logs):
                self._ensure_tag(rtag)
                if self._passes_filter(line):
                    if rtag:
                        self.console.insert(tk.END, line + "\n", rtag)
                    else:
                        self.console.insert(tk.END, line + "\n")
            self.console.see(tk.END)
        except Exception:
            pass

    def toggle_wrap(self):
        self.console.configure(wrap="word" if self.var_wrap.get() else "none")
        self._save_selections()

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
        self._save_selections()
        self._backfill()
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
            self.model.emit("[*] File-Stream Bypass (Ghost) aktiviert. Horche auf /data/data/...", "ghost_log")
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
        self.model.emit("[*] Leere alten Logcat-Puffer auf dem Gerät...")
        CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)
        self._start_ghost_if_enabled(adb, base_dir)
        if self.var_early_log.get():
            if "{PID}" in logcat_cmd:
                return messagebox.showerror("Konflikt",
                                            "Early Logging ist nicht möglich, wenn '{PID}' im Filter verwendet wird!")
            self.model.emit("[*] Early Logging: Starte Logcat VOR dem App-Launch...")
            self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)
            self._launch_app(adb, intent_cmd, base_dir)
        else:
            self.model.emit("[*] Starte App zuerst...")
            self._launch_app(adb, intent_cmd, base_dir)
            if "{PID}" in logcat_cmd:
                self._delayed_pid_logcat_start(adb, logcat_cmd, base_dir, attempts=6)
            else:
                self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)

    def _delayed_pid_logcat_start(self, adb, logcat_cmd, base_dir, attempts):
        if attempts <= 0:
            self.model.emit("[!] Timeout: Konnte PID der App nach dem Start nicht ermitteln!", "error_log")
            return
        pid = self._resolve_pid(adb, base_dir)
        if pid:
            logcat_cmd = logcat_cmd.replace("{PID}", pid)
            self._start_capture_internal(adb, logcat_cmd, base_dir, skip_clear=True)
        else:
            self.model.emit(f"[*] Warte auf App-Prozess... (noch {attempts} Versuche)", "frida_log")
            self.after(500, lambda: self._delayed_pid_logcat_start(adb, logcat_cmd, base_dir, attempts - 1))

    def _start_capture_internal(self, adb, logcat_cmd, base_dir, skip_clear=False):
        if not skip_clear:
            self.model.emit("[*] Leere alten Logcat-Puffer auf dem Gerät...")
            CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)
        archive_dir = getattr(self.app, 'current_archive_path', self.app.cfg.paths.get("ARCHIVE_DIR", ""))
        try:
            log_file_path = self.logcat_service.start_capture(adb, logcat_cmd, archive_dir)
            self.model.emit(f"[*] Starte Logcat: \"{adb}\" shell \"{logcat_cmd}\"")
            try:
                self.lbl_status.config(text=f"🔴 Recording to: {os.path.basename(log_file_path)}", foreground="red")
            except Exception:
                pass
            self._refresh_run_status()
        except Exception as e:
            self.model.emit(f"[!] Fehler beim Starten von Logcat: {e}", "error_log")

    def _launch_app(self, adb, intent_cmd, base_dir):
        if (self.app.cfg.config.get("MCP_SETTINGS", {}).get("console", {}) or {}).get("clear_on_app_start", True):
            EventBus.publish("CONSOLE_CLEAR_HARD", "live")
        full_intent_cmd = f'"{adb}" shell {intent_cmd}'
        self.model.emit(f"\n[*] Starte App: {full_intent_cmd}")
        try:
            CommandRunner.run_background(full_intent_cmd, cwd=base_dir)
            self.model.app_launched = True
            self._refresh_run_status()
        except Exception as e:
            self.model.emit(f"[!] Fehler beim App Start: {e}", "error_log")

    # ================= Gefiltertes Speichern =================
    def save_filtered_log(self):
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

    # ================= Steuerung: Start / Multi / Stop =================
    def _sys(self, msg):
        self.model.emit(msg, "exec_sys")

    def _device_ready(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        r = CommandRunner.run_blocking(f'"{adb}" get-state', ".", timeout=4)
        return r.returncode == 0 and "device" in (r.stdout or "")

    def _guard(self, fn):
        def task():
            if not self._device_ready():
                self.app.after(0, lambda: (
                    self._sys("[!] Kein Gerät verbunden (adb get-state: Timeout/kein Gerät)."),
                    self._set_status("Kein Gerät", "red")))
                return
            self.app.after(0, fn)
        threading.Thread(target=task, daemon=True).start()

    def _set_status(self, text, color):
        try:
            self.lbl_status.config(text=text, foreground=color)
        except Exception:
            pass

    def start_app_action(self):
        self._save_selections(); self._guard(self.start_app_only)

    def start_logcat_action(self):
        self._save_selections(); self._guard(self.start_logcat_only)

    def start_exe_action(self):
        self._save_selections(); self._guard(self._start_exe)

    def _start_exe(self):
        try:
            self.exec_panel.deploy_selected()
        except Exception as e:
            self._sys(f"[!] ExeDeploy: {e}")
        self._refresh_run_status()

    def start_multi(self):
        self._save_selections()
        if not (self.var_ms_app.get() or self.var_ms_log.get() or self.var_ms_exe.get()):
            messagebox.showinfo("Multi-Start", "Nichts ausgewählt — bitte rechts Checkboxen setzen.")
            return

        def run():
            if self.var_ms_app.get() and self.var_ms_log.get():
                self.start_combined()
            elif self.var_ms_app.get():
                self.start_app_only()
            elif self.var_ms_log.get():
                self.start_logcat_only()
            if self.var_ms_exe.get():
                self._start_exe()
            self._refresh_run_status()

        self._guard(run)

    def stop_all(self):
        try:
            self.stop_capture()
        except Exception:
            pass
        try:
            lib = self.exec_panel.selected_lib()
            ctrl = getattr(self.app, "exec_run_controller", None)
            if lib and ctrl:
                ctrl.stop(lib)
        except Exception:
            pass

        def task():
            adb = self.app.cfg.paths.get("ADB", "adb")
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            if pkg:
                CommandRunner.run_blocking(f'"{adb}" shell am force-stop {pkg}', ".", timeout=8)
            self.model.app_launched = False
            self.app.after(0, lambda: (self._sys("[*] Alles gestoppt (Logcat/Exe/App)."),
                                       self._refresh_run_status()))
        threading.Thread(target=task, daemon=True).start()

    # ---- Status-Punkte ----
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
            self.dot_app.config(text="🟢" if self.model.app_launched else "⚪")
        if hasattr(self, "dot_exe"):
            self.dot_exe.config(text="🟢" if exe_on else "⚪")

    def _poll_status(self):
        self._refresh_run_status()
        self._save_selections()
        try:
            self.after(1500, self._poll_status)
        except Exception:
            pass

    def stop_capture(self):
        self.logcat_service.stop_capture()
        self.ghost_log_service.stop_capture()
        self._set_status("Status: Gestoppt", "gray")
        self.model.emit("\n[*] Logging gestoppt.")
        self._refresh_run_status()

    def clear_console(self):
        self.model.clear_logs()
        self.console.delete("1.0", tk.END)
        EventBus.publish("CONSOLE_CLEARED", "live")

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
        # WICHTIG: beim Zerstoeren der View NICHT die Captures stoppen — der Zustand lebt im
        # Modell weiter (verlustfreies andocken/teilen/abdocken). Nur abmelden.
        if event is not None and getattr(event, "widget", None) is not self:
            return
        try:
            self.model.remove_view(self)
        except Exception:
            pass
