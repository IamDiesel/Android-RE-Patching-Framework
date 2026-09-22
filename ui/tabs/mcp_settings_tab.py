"""
MCPSettingsTab — Einstellungsseite „🔌 MCP" (Schritt 1).

Verwaltet:
  - den MCP-Server (Start/Stop/Restart/Status) via McpServerService,
  - die Pro-Tool-Matrix (JEDE Funktion einzeln an/aus, aus mcp_server.registry),
  - die Kategorie-Gates (caps) + „confirm pro Aufruf",
  - den Log-Export-Pfad,
  - den Audit-Viewer (Live-Tail von data/mcp_audit.jsonl) + Export.

Schreibt alles in config.json -> MCP_SETTINGS. Aendert keinen core/-Code.
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from core.application.event_bus import EventBus
from services.mcp_server_service import McpServerService
from mcp_server.registry import (
    TOOL_CATALOG, GROUP_ORDER, GROUP_NAMES, CAP_KEYS, tier_default,
)

CAP_LABELS = {
    "build": "Bauen (LibForge + BUILD_NATIVE)",
    "flash": "Flashen (adb install)",
    "app_start": "Apps starten",
    "exec_run": "Executables starten (ExeDeploy)",
    "file_manager": "File Manager (Geraete-Dateizugriff)",
    "delete_ops": "Loeschen (Patches/Libs/Executables)",
}


class MCPSettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        # ein geteilter Service-Instanz am app-Objekt (analog native_lib_mgr)
        self.svc = getattr(app, "mcp_server_svc", None)
        if self.svc is None:
            self.svc = McpServerService(app.cfg)
            app.mcp_server_svc = self.svc

        self.tool_vars = {}     # tool_id -> BooleanVar
        self.cap_vars = {}      # cap    -> BooleanVar
        self.srv_vars = {}      # host/port/autostart
        self.opt_vars = {}      # require_confirm / log_export_dir

        self.create_widgets()
        self.populate_settings()
        self._schedule_status()

    # ================================================================= UI
    def create_widgets(self):
        # ---- Server-Verwaltung ----
        srv = ttk.LabelFrame(self, text="MCP-Server")
        srv.pack(fill="x", padx=10, pady=(8, 4))

        row = ttk.Frame(srv); row.pack(fill="x", padx=6, pady=4)
        self.lbl_status = ttk.Label(row, text="● unbekannt", width=42)
        self.lbl_status.pack(side="left")
        ttk.Button(row, text="Start", command=self._on_start).pack(side="left", padx=3)
        ttk.Button(row, text="Stop", command=self._on_stop).pack(side="left", padx=3)
        ttk.Button(row, text="Restart", command=self._on_restart).pack(side="left", padx=3)
        ttk.Button(row, text="Server-Log", command=self._open_server_log).pack(side="left", padx=3)

        row2 = ttk.Frame(srv); row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row2, text="Host:").pack(side="left")
        self.srv_vars["host"] = tk.StringVar()
        ttk.Entry(row2, textvariable=self.srv_vars["host"], width=14).pack(side="left", padx=(2, 10))
        ttk.Label(row2, text="Port:").pack(side="left")
        self.srv_vars["port"] = tk.StringVar()
        ttk.Entry(row2, textvariable=self.srv_vars["port"], width=8).pack(side="left", padx=(2, 10))
        self.srv_vars["autostart"] = tk.BooleanVar()
        ttk.Checkbutton(row2, text="Autostart beim GUI-Start",
                        variable=self.srv_vars["autostart"]).pack(side="left", padx=6)

        self.lbl_msg = ttk.Label(srv, text="", wraplength=1000, foreground="#555")
        self.lbl_msg.pack(fill="x", padx=6, pady=(0, 4))

        # ---- Optionen ----
        opt = ttk.LabelFrame(self, text="Optionen")
        opt.pack(fill="x", padx=10, pady=4)
        self.opt_vars["require_confirm"] = tk.BooleanVar()
        ttk.Checkbutton(opt, text="confirm=true pro genehmigungspflichtigem Aufruf verlangen",
                        variable=self.opt_vars["require_confirm"]).pack(anchor="w", padx=6, pady=2)
        orow = ttk.Frame(opt); orow.pack(fill="x", padx=6, pady=2)
        ttk.Label(orow, text="Log-Export-Ordner:").pack(side="left")
        self.opt_vars["log_export_dir"] = tk.StringVar()
        ttk.Entry(orow, textvariable=self.opt_vars["log_export_dir"], width=48).pack(side="left", padx=4)
        ttk.Button(orow, text="...", width=3, command=self._pick_export_dir).pack(side="left")

        # ---- Konsolen-Spiegel ----
        cons = ttk.LabelFrame(self, text="Konsolen-Spiegel (Live-Log fuer den MCP-Zugriff)")
        cons.pack(fill="x", padx=10, pady=4)
        crow = ttk.Frame(cons); crow.pack(fill="x", padx=6, pady=3)
        ttk.Label(crow, text="Max. Groesse je Datei (MB):").pack(side="left")
        self.opt_vars["console_max_mb"] = tk.StringVar()
        ttk.Entry(crow, textvariable=self.opt_vars["console_max_mb"], width=8).pack(side="left", padx=(2, 12))
        self.opt_vars["clear_on_app_start"] = tk.BooleanVar()
        ttk.Checkbutton(crow, text="Beim App-Start Live-Spiegel hart leeren",
                        variable=self.opt_vars["clear_on_app_start"]).pack(side="left", padx=6)
        crow2 = ttk.Frame(cons); crow2.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(crow2, text="Live-Spiegel jetzt leeren",
                   command=lambda: self._hard_clear("live")).pack(side="left", padx=2)
        ttk.Button(crow2, text="Main-Spiegel jetzt leeren",
                   command=lambda: self._hard_clear("main")).pack(side="left", padx=2)

        # ---- Kategorie-Gates (caps) ----
        cap = ttk.LabelFrame(self, text="Kategorie-Freigaben (fuer genehmigungspflichtige Tools)")
        cap.pack(fill="x", padx=10, pady=4)
        capgrid = ttk.Frame(cap); capgrid.pack(fill="x", padx=6, pady=4)
        for i, key in enumerate(CAP_KEYS):
            self.cap_vars[key] = tk.BooleanVar()
            ttk.Checkbutton(capgrid, text=CAP_LABELS.get(key, key),
                            variable=self.cap_vars[key]).grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=1)

        # ---- Pro-Tool-Matrix (scrollbar) ----
        matrix = ttk.LabelFrame(self, text="Funktionen (jede einzeln abschaltbar)  —  P/O frei · G genehmigungspflichtig")
        matrix.pack(fill="both", expand=True, padx=10, pady=4)
        canvas = tk.Canvas(matrix, highlightthickness=0, height=240)
        sb = ttk.Scrollbar(matrix, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self._build_tool_matrix(inner)

        # ---- Audit-Viewer ----
        aud = ttk.LabelFrame(self, text="Audit-Log (jede Ausfuehrung wird protokolliert)")
        aud.pack(fill="both", expand=True, padx=10, pady=4)
        arow = ttk.Frame(aud); arow.pack(fill="x", padx=6, pady=2)
        ttk.Button(arow, text="Aktualisieren", command=self._refresh_audit).pack(side="left", padx=3)
        ttk.Button(arow, text="Audit exportieren", command=self._export_audit).pack(side="left", padx=3)
        self.txt_audit = tk.Text(aud, height=7, font=("Courier", 8), wrap="none")
        self.txt_audit.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # ---- Save ----
        bar = ttk.Frame(self); bar.pack(fill="x", padx=10, pady=(2, 10))
        ttk.Label(bar, text="Standard: P/O frei, G aus. Aenderungen erst nach Speichern aktiv.").pack(side="left")
        ttk.Button(bar, text="Speichern", command=self.save_settings).pack(side="right", padx=5)

    def _build_tool_matrix(self, parent):
        NCOLS = 3
        cols = []
        for c in range(NCOLS):
            f = ttk.Frame(parent)
            f.grid(row=0, column=c, sticky="nw", padx=4)
            parent.grid_columnconfigure(c, weight=1)
            cols.append([f, 0])  # [Spalten-Frame, geschaetzte Hoehe]

        groups = {}
        for spec in TOOL_CATALOG:
            groups.setdefault(spec.group, []).append(spec)

        for g in GROUP_ORDER:
            specs = groups.get(g, [])
            if not specs:
                continue
            # in die aktuell kuerzeste Spalte einsortieren -> balanciert, wenig Scrollen
            target = min(cols, key=lambda cw: cw[1])
            lf = ttk.LabelFrame(target[0], text=f"{g} — {GROUP_NAMES.get(g, '')}")
            lf.pack(fill="x", padx=2, pady=3, anchor="n")
            hdr = ttk.Frame(lf); hdr.pack(fill="x")
            ttk.Button(hdr, text="an", width=5,
                       command=lambda gg=g: self._group_set(gg, True)).pack(side="left", padx=2, pady=1)
            ttk.Button(hdr, text="aus", width=5,
                       command=lambda gg=g: self._group_set(gg, False)).pack(side="left", padx=2)
            for spec in specs:
                var = tk.BooleanVar()
                self.tool_vars[spec.id] = var
                tag = spec.tier
                if spec.tier == "G" and spec.caps:
                    tag = "G:" + ",".join(spec.caps)
                ttk.Checkbutton(lf, text=f"[{tag}] {spec.id}", variable=var).pack(anchor="w", padx=10, pady=0)
            target[1] += len(specs) + 2  # Header + Titel als Gewicht

    # ================================================================= Daten
    def _mcp(self) -> dict:
        return self.app.cfg.config.setdefault("MCP_SETTINGS", {})

    def populate_settings(self):
        m = self._mcp()
        srv = m.get("server", {}) or {}
        self.srv_vars["host"].set(srv.get("host", "127.0.0.1"))
        self.srv_vars["port"].set(str(srv.get("port", 8765)))
        self.srv_vars["autostart"].set(bool(srv.get("autostart", False)))

        self.opt_vars["require_confirm"].set(bool(m.get("require_confirm_each_call", True)))
        self.opt_vars["log_export_dir"].set(m.get("log_export_dir", os.path.join("Claude outputs", "logs")))

        c = m.get("console", {}) or {}
        self.opt_vars["console_max_mb"].set(str(int(c.get("max_bytes", 20 * 1024 * 1024)) // (1024 * 1024)))
        self.opt_vars["clear_on_app_start"].set(bool(c.get("clear_on_app_start", True)))

        caps = m.get("caps", {}) or {}
        for key in CAP_KEYS:
            self.cap_vars[key].set(bool(caps.get(key, False)))

        tools = m.get("tools", {}) or {}
        for spec in TOOL_CATALOG:
            val = tools[spec.id] if spec.id in tools else tier_default(spec.tier)
            self.tool_vars[spec.id].set(bool(val))

        self._refresh_audit()

    def _sync_config_from_ui(self) -> bool:
        m = self._mcp()
        try:
            port = int(self.srv_vars["port"].get().strip() or "8765")
        except ValueError:
            messagebox.showerror("MCP", "Port muss eine Zahl sein.")
            return False
        m.setdefault("server", {})
        m["server"].update({
            "host": self.srv_vars["host"].get().strip() or "127.0.0.1",
            "port": port,
            "autostart": bool(self.srv_vars["autostart"].get()),
            "transport": m["server"].get("transport", "http"),
            "enabled": self.svc.is_running(),
        })
        m["require_confirm_each_call"] = bool(self.opt_vars["require_confirm"].get())
        m["log_export_dir"] = self.opt_vars["log_export_dir"].get().strip()
        try:
            mb = int(float(self.opt_vars["console_max_mb"].get().strip() or "20"))
        except ValueError:
            mb = 20
        m["console"] = {
            "max_bytes": max(1, mb) * 1024 * 1024,
            "clear_on_app_start": bool(self.opt_vars["clear_on_app_start"].get()),
        }
        m["caps"] = {key: bool(self.cap_vars[key].get()) for key in CAP_KEYS}
        # Pro-Tool: nur Abweichungen vom Tier-Default speichern -> kompakte config
        tools = {}
        for spec in TOOL_CATALOG:
            val = bool(self.tool_vars[spec.id].get())
            if val != tier_default(spec.tier):
                tools[spec.id] = val
        m["tools"] = tools
        return True

    def save_settings(self):
        if self._sync_config_from_ui():
            self.app.cfg.save()
            messagebox.showinfo("MCP", "MCP-Einstellungen gespeichert.")

    # ================================================================= Aktionen
    def _group_set(self, group: str, value: bool):
        for spec in TOOL_CATALOG:
            if spec.group == group and spec.id in self.tool_vars:
                self.tool_vars[spec.id].set(value)

    def _pick_export_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.opt_vars["log_export_dir"].set(d)

    def _hard_clear(self, scope):
        if not messagebox.askyesno("Konsolen-Spiegel",
                                   f"{scope}-Spiegel-Datei wirklich leeren? (Historie geht verloren)"):
            return
        EventBus.publish("CONSOLE_CLEAR_HARD", scope)
        messagebox.showinfo("Konsolen-Spiegel", f"{scope}-Spiegel geleert.")

    def _show_msg(self, ok, msg):
        first = (msg or "").split("\n")[0]
        self.lbl_msg.config(text=first, foreground=("#1a7f37" if ok else "#b00020"))
        if (msg and "\n" in msg) or not ok:
            (messagebox.showinfo if ok else messagebox.showwarning)("MCP-Server", msg or "")

    def _on_start(self):
        # Host/Port zuerst in die Config uebernehmen, damit der Service sie nutzt
        self._sync_config_from_ui()
        ok, msg = self.svc.start()
        self._show_msg(ok, msg)
        self._refresh_status()

    def _on_stop(self):
        ok, msg = self.svc.stop()
        self._show_msg(ok, msg)
        self._refresh_status()

    def _on_restart(self):
        self._sync_config_from_ui()
        ok, msg = self.svc.restart()
        self._show_msg(ok, msg)
        self._refresh_status()

    def _open_server_log(self):
        path = self.svc.log_path
        if os.path.exists(path):
            try:
                os.startfile(path)  # Windows
            except Exception:
                messagebox.showinfo("MCP", f"Server-Log: {path}")
        else:
            messagebox.showinfo("MCP", "Noch kein Server-Log vorhanden.")

    def _schedule_status(self):
        self._refresh_status()
        self.after(2000, self._schedule_status)

    def _refresh_status(self):
        try:
            st = self.svc.status()
        except Exception:
            return
        if st["running"]:
            self.lbl_status.config(
                text=f"● laeuft  ·  {st['host']}:{st['port']}  ·  PID {st['pid']}  ·  {st['uptime_s']}s",
                foreground="#1a7f37")
        else:
            self.lbl_status.config(text=f"● gestoppt  ·  {st['host']}:{st['port']}", foreground="#b00020")

    def _audit_path(self) -> str:
        m = self._mcp()
        rel = (m.get("audit", {}) or {}).get("file", os.path.join("data", "mcp_audit.jsonl"))
        return rel if os.path.isabs(rel) else os.path.join(self.app.cfg.config.get("BASE_DIR", "."), rel)

    def _refresh_audit(self):
        path = self._audit_path()
        self.txt_audit.delete("1.0", tk.END)
        if not os.path.exists(path):
            self.txt_audit.insert("1.0", "(noch keine Audit-Eintraege)")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-200:]
            self.txt_audit.insert("1.0", "".join(lines))
            self.txt_audit.see("end")
        except Exception as e:
            self.txt_audit.insert("1.0", f"(Audit nicht lesbar: {e})")

    def _export_audit(self):
        src = self._audit_path()
        if not os.path.exists(src):
            messagebox.showinfo("MCP", "Kein Audit-Log vorhanden.")
            return
        out_dir = self.opt_vars["log_export_dir"].get().strip() or os.path.join("Claude outputs", "logs")
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(self.app.cfg.config.get("BASE_DIR", "."), out_dir)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, "mcp_audit_export.jsonl")
        try:
            import shutil
            shutil.copy2(src, dst)
            messagebox.showinfo("MCP", f"Audit exportiert:\n{dst}")
        except Exception as e:
            messagebox.showerror("MCP", f"Export fehlgeschlagen: {e}")
