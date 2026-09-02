import tkinter as tk
from tkinter import ttk, messagebox
import os
import sys

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus

# NEU: Services importieren
from services.profile_manager_service import ProfileManagerService
from services.logcat_service import LogcatService


class LauncherLoggerTab(ttk.Frame):
    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.app = ws.app
        self.raw_logs = []
        self.search_pos = "1.0"

        # NEU: Services initialisieren
        data_dir = os.path.join(self.app.cfg.config.get("BASE_DIR", ""), "data")
        config_file = os.path.join(data_dir, "logger_profiles.json")
        self.profile_mgr = ProfileManagerService(config_file)
        self.logcat_service = LogcatService()

        # NEU: EventBus Subscription für Logs (Löst manuelles Threading im Tab ab)
        EventBus.subscribe("LOGCAT_LINE", lambda line: self.after(0, self._append_log, line))

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

        # --- Controls Row ---
        f_ctrl = ttk.Frame(f_top)
        f_ctrl.pack(fill="x", pady=5)

        self.btn_start = ttk.Button(f_ctrl, text="▶ Logging & App Starten", command=self.start_capture)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(f_ctrl, text="⏹ Stop Logging", command=self.stop_capture, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        ttk.Separator(f_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Button(f_ctrl, text="🗑 Anzeige leeren", command=self.clear_console).pack(side="left", padx=5)
        ttk.Button(f_ctrl, text="📂 Archiv (PID) öffnen", command=self.open_archive).pack(side="right", padx=5)

        # --- Status ---
        self.lbl_status = ttk.Label(f_top, text="Status: Bereit", font=("Segoe UI", 9, "italic"), foreground="gray")
        self.lbl_status.pack(anchor="w", padx=5, pady=2)

        # --- Live Console with Scrollbars ---
        f_console = ttk.Frame(self)
        f_console.pack(fill="both", expand=True, padx=10, pady=5)

        self.console = tk.Text(f_console, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), wrap="word")

        scroll_y = ttk.Scrollbar(f_console, orient="vertical", command=self.console.yview)
        scroll_y.pack(side="right", fill="y")

        scroll_x = ttk.Scrollbar(f_console, orient="horizontal", command=self.console.xview)
        scroll_x.pack(side="bottom", fill="x")

        self.console.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.console.pack(side="left", fill="both", expand=True)
        self.console.tag_configure("search", background="yellow", foreground="black")

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
            self.console.insert(tk.END, "".join(self.raw_logs))
        else:
            filtered = [line for line in self.raw_logs if query in line.lower()]
            self.console.insert(tk.END, "".join(filtered))
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

    def start_capture(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        intent_cmd = self._format_cmd(self.cb_intents.get().strip())
        logcat_cmd = self._format_cmd(self.cb_logcats.get().strip())

        if not intent_cmd or not logcat_cmd:
            return messagebox.showerror("Fehler", "Intent und Logcat Kommando dürfen nicht leer sein.")

        self.console.insert(tk.END, "[*] Leere alten Logcat-Puffer auf dem Gerät...\n")
        CommandRunner.run_blocking(f'"{adb}" logcat -c', cwd=base_dir)

        archive_dir = getattr(self.app, 'current_archive_path', self.app.cfg.paths.get("ARCHIVE_DIR", ""))

        try:
            # NEU: Start des Logcats über den Service
            log_file_path = self.logcat_service.start_capture(adb, logcat_cmd, archive_dir)
            self.console.insert(tk.END, f"[*] Starte Logcat: \"{adb}\" shell \"{logcat_cmd}\"\n")

            # App-Start Verzögerung
            self.after(500, lambda: self._launch_app(adb, intent_cmd, base_dir))

            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.lbl_status.config(text=f"🔴 Recording to: {log_file_path}", foreground="red")
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim Starten von Logcat: {e}\n")

    def _launch_app(self, adb, intent_cmd, base_dir):
        full_intent_cmd = f'"{adb}" shell {intent_cmd}'
        self.console.insert(tk.END, f"\n[*] Starte App: {full_intent_cmd}\n")
        self.console.see(tk.END)
        try:
            CommandRunner.run_background(full_intent_cmd, cwd=base_dir)
        except Exception as e:
            self.console.insert(tk.END, f"[!] Fehler beim App Start: {e}\n")

    def _append_log(self, line):
        self.raw_logs.append(line)
        query = self.ent_filter.get().lower()
        if not query or query in line.lower():
            self.console.insert(tk.END, line)
            if self.console.yview()[1] >= 0.98:
                self.console.see(tk.END)

    def stop_capture(self):
        # NEU: Stop über den Service
        self.logcat_service.stop_capture()

        self.btn_start.config(state="normal")
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