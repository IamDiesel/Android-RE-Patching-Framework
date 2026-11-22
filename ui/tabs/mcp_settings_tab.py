"""
MCPSettingsTab — Einstellungsseite „🔌 MCP" (Schritt 1).

Verwaltet:
  - den MCP-Server (Start/Stop/Restart/Status, Host/Port/Transport/Autostart) via McpServerService,
  - die Pro-Tool-Matrix (JEDE Funktion einzeln an/aus, Suchfilter, Presets, 500ms-Tooltips),
  - die Kategorie-Gates (caps) + „confirm pro Aufruf",
  - Konsolen-Spiegel, Log-Export-Pfad und Audit-Einstellungen (audit.enabled / audit.log_args),
  - den Audit-Viewer (Live-Tail von data/mcp_audit.jsonl) + Export.

Schreibt merge-sicher via update_keys({"MCP_SETTINGS": ...}) in config.json.
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from core.application.event_bus import EventBus
from services.mcp_server_service import McpServerService
from mcp_server.registry import (
    TOOL_CATALOG, GROUP_ORDER, GROUP_NAMES, CAP_KEYS, tier_default,
)

try:
    from ui.widgets.tooltip import add_tooltip
except Exception:
    def add_tooltip(_w, _t):
        return None


CAP_LABELS = {
    "build": "Bauen (LibForge + BUILD_NATIVE)",
    "flash": "Flashen (adb install)",
    "app_start": "Apps starten / stoppen",
    "exec_run": "Executables starten (ExeDeploy)",
    "file_manager": "File Manager (Geraete-Dateizugriff)",
    "delete_ops": "Loeschen (Patches/Libs/Executables/DB)",
    "dbg_write": "Debug-Sitzung schreiben (KI treibt Live-Sitzung)",
    "session_write": "Session schreiben (Native Studio / LibCarve Sync)",
    "os_write": "OS-Inspektor schreiben (Syscall-Policy / VFS / Overrides)",
}

CAP_TIPS = {
    "build": "Erlaubt Gated-Builds per MCP (pipeline.run, lib.build, libcarve.compile).",
    "flash": "Erlaubt adb install der gebauten APK ueber MCP (pipeline.flash).",
    "app_start": "Erlaubt Starten & Beenden der Ziel-App auf dem Geraet (app.start, app.stop).",
    "exec_run": "Erlaubt Deploy & Start von Executables auf dem Geraet (exec.deploy_run).",
    "file_manager": "Erlaubt Dateizugriff, Pulls, Pushes und Proxy-Setup am ADB-Geraet.",
    "delete_ops": "Erlaubt das Loeschen von Favoriten, Libs, Frida-Skripten, Geraetedateien oder Traffic-DB.",
    "dbg_write": "Erlaubt das Weitertreiben der geteilten Debug-Sitzung (native.dbg_step / dbg_resume).",
    "session_write": "Erlaubt das Zurueckschreiben geteilter Studio-Sessions (native.session_set, libcarve.session_set).",
    "os_write": "Erlaubt das Setzen von OS-Inspektor-Policies/Overrides (native.os_config_set).",
}


class MCPSettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.svc = getattr(app, "mcp_server_svc", None)
        if self.svc is None:
            self.svc = McpServerService(app.cfg)
            app.mcp_server_svc = self.svc

        self.tool_vars = {}        # tool_id -> BooleanVar
        self.cap_vars = {}         # cap -> BooleanVar
        self.srv_vars = {}         # host/port/transport/autostart
        self.opt_vars = {}         # require_confirm/log_export_dir/console_max_mb/clear_on_app_start/audit_*
        self.filter_var = tk.StringVar()
        self._tool_cbs = {}        # tool_id -> ttk.Checkbutton
        self._group_frames = {}    # group -> ttk.LabelFrame
        self._group_specs = {}     # group -> [ToolSpec, ...]
        self._matrix_canvas = None
        self._status_after_id = None

        self.create_widgets()
        self.populate_settings()
        self.bind("<Destroy>", self._on_destroy)
        self._schedule_status()

    # ================================================================= UI
    def _bind_matrix_wheel(self, widget):
        try:
            widget.bind("<MouseWheel>", self._on_matrix_wheel, add="+")
        except Exception:
            pass

    def _on_matrix_wheel(self, e):
        c = getattr(self, "_matrix_canvas", None)
        if c is None:
            return
        try:
            if c.winfo_exists():
                delta = getattr(e, "delta", 0) or 0
                step = -1 if delta > 0 else (1 if delta < 0 else 0)
                if abs(delta) >= 120:
                    step = int(-delta / 120)
                if step:
                    c.yview_scroll(step, "units")
                return "break"
        except Exception:
            pass

    def create_widgets(self):
        # ---- Server-Verwaltung ----
        srv = ttk.LabelFrame(self, text="MCP-Server")
        srv.pack(fill="x", padx=10, pady=(8, 4))

        row = ttk.Frame(srv); row.pack(fill="x", padx=6, pady=4)
        self.lbl_status = ttk.Label(row, text="● unbekannt", width=42)
        self.lbl_status.pack(side="left")
        b_start = ttk.Button(row, text="Start", command=self._on_start)
        b_start.pack(side="left", padx=3)
        b_stop = ttk.Button(row, text="Stop", command=self._on_stop)
        b_stop.pack(side="left", padx=3)
        b_rst = ttk.Button(row, text="Restart", command=self._on_restart)
        b_rst.pack(side="left", padx=3)
        b_log = ttk.Button(row, text="Server-Log", command=self._open_server_log)
        b_log.pack(side="left", padx=3)
        add_tooltip(b_start, "Uebernimmt die aktuellen MCP-Einstellungen auf Platte und startet den lokalen MCP-Server.")
        add_tooltip(b_stop, "Beendet den laufenden lokalen MCP-Server-Prozess.")
        add_tooltip(b_rst, "Speichert die aktuellen Einstellungen und startet den MCP-Server sauber neu.")
        add_tooltip(b_log, "Oeffnet die Server-Ausgabedatei data/mcp_server.log.")

        row2 = ttk.Frame(srv); row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row2, text="Host:").pack(side="left")
        self.srv_vars["host"] = tk.StringVar()
        e_host = ttk.Entry(row2, textvariable=self.srv_vars["host"], width=14)
        e_host.pack(side="left", padx=(2, 10))
        add_tooltip(e_host, "Bind-Adresse des lokalen HTTP-MCP-Servers (Standard: 127.0.0.1).")

        ttk.Label(row2, text="Port:").pack(side="left")
        self.srv_vars["port"] = tk.StringVar()
        e_port = ttk.Entry(row2, textvariable=self.srv_vars["port"], width=8)
        e_port.pack(side="left", padx=(2, 10))
        add_tooltip(e_port, "TCP-Port des lokalen HTTP-MCP-Servers (Standard: 8765).")

        ttk.Label(row2, text="Transport:").pack(side="left")
        self.srv_vars["transport"] = tk.StringVar(value="http")
        cb_tr = ttk.Combobox(row2, textvariable=self.srv_vars["transport"], width=7,
                             state="readonly", values=("http", "stdio"))
        cb_tr.pack(side="left", padx=(2, 10))
        add_tooltip(cb_tr, "MCP-Transport: 'http' (lokaler streamable-HTTP Server) oder 'stdio' (fuer CLI-Starter).")

        self.srv_vars["autostart"] = tk.BooleanVar()
        ck_auto = ttk.Checkbutton(row2, text="Autostart beim GUI-Start", variable=self.srv_vars["autostart"])
        ck_auto.pack(side="left", padx=6)
        add_tooltip(ck_auto, "Startet den lokalen MCP-Server automatisch nach dem Oeffnen der Studio-GUI.")

        self.lbl_msg = ttk.Label(srv, text="", wraplength=1000, foreground="#555")
        self.lbl_msg.pack(fill="x", padx=6, pady=(0, 4))

        # ---- Optionen ----
        opt = ttk.LabelFrame(self, text="Optionen")
        opt.pack(fill="x", padx=10, pady=4)
        self.opt_vars["require_confirm"] = tk.BooleanVar()
        ck_conf = ttk.Checkbutton(
            opt,
            text="confirm=true pro genehmigungspflichtigem Aufruf verlangen",
            variable=self.opt_vars["require_confirm"],
        )
        ck_conf.pack(anchor="w", padx=6, pady=2)
        add_tooltip(ck_conf, "Zusaetzlicher Schutz fuer G-Tools: KI muss jeden G-Aufruf explizit mit confirm=true bestaetigen.")
        orow = ttk.Frame(opt); orow.pack(fill="x", padx=6, pady=2)
        ttk.Label(orow, text="Log-Export-Ordner:").pack(side="left")
        self.opt_vars["log_export_dir"] = tk.StringVar()
        e_exp = ttk.Entry(orow, textvariable=self.opt_vars["log_export_dir"], width=48)
        e_exp.pack(side="left", padx=4)
        add_tooltip(e_exp, "Zielverzeichnis fuer log.export und den Audit-Export.")
        b_pick = ttk.Button(orow, text="...", width=3, command=self._pick_export_dir)
        b_pick.pack(side="left")
        add_tooltip(b_pick, "Log-Export-Ordner per Dialog auswaehlen.")

        # ---- Konsolen-Spiegel ----
        cons = ttk.LabelFrame(self, text="Konsolen-Spiegel (Live-Log fuer den MCP-Zugriff)")
        cons.pack(fill="x", padx=10, pady=4)
        crow = ttk.Frame(cons); crow.pack(fill="x", padx=6, pady=3)
        ttk.Label(crow, text="Max. Groesse je Datei (MB):").pack(side="left")
        self.opt_vars["console_max_mb"] = tk.StringVar()
        e_mb = ttk.Entry(crow, textvariable=self.opt_vars["console_max_mb"], width=8)
        e_mb.pack(side="left", padx=(2, 12))
        add_tooltip(e_mb, "Maximale Ring-Dateigroesse fuer Main-/Live-Konsolenspiegel (console.*_tail).")
        self.opt_vars["clear_on_app_start"] = tk.BooleanVar()
        ck_clr = ttk.Checkbutton(crow, text="Beim App-Start Live-Spiegel hart leeren",
                                 variable=self.opt_vars["clear_on_app_start"])
        ck_clr.pack(side="left", padx=6)
        add_tooltip(ck_clr, "Leert den Live-Konsolenspiegel automatisch bei jedem Studio-Start.")
        crow2 = ttk.Frame(cons); crow2.pack(fill="x", padx=6, pady=(0, 4))
        b_cl_live = ttk.Button(crow2, text="Live-Spiegel jetzt leeren",
                               command=lambda: self._hard_clear("live"))
        b_cl_live.pack(side="left", padx=2)
        b_cl_main = ttk.Button(crow2, text="Main-Spiegel jetzt leeren",
                               command=lambda: self._hard_clear("main"))
        b_cl_main.pack(side="left", padx=2)
        add_tooltip(b_cl_live, "Loescht die aktuelle Live-Spiegel-Historie fuer console.live_tail.")
        add_tooltip(b_cl_main, "Loescht die aktuelle Main-Spiegel-Historie fuer console.main_tail.")

        # ---- Kategorie-Gates (caps) ----
        cap = ttk.LabelFrame(self, text="Kategorie-Freigaben (fuer genehmigungspflichtige Tools)")
        cap.pack(fill="x", padx=10, pady=4)
        capgrid = ttk.Frame(cap); capgrid.pack(fill="x", padx=6, pady=4)
        for i, key in enumerate(CAP_KEYS):
            self.cap_vars[key] = tk.BooleanVar()
            cb_cap = ttk.Checkbutton(capgrid, text=CAP_LABELS.get(key, key), variable=self.cap_vars[key])
            cb_cap.grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=1)
            add_tooltip(cb_cap, CAP_TIPS.get(key, key))

        # ---- Pro-Tool-Matrix (Filter + Presets + scrollbar) ----
        matrix = ttk.LabelFrame(self, text="Funktionen (jede einzeln abschaltbar)  —  P/O frei · G genehmigungspflichtig")
        matrix.pack(fill="both", expand=True, padx=10, pady=4)

        mbar = ttk.Frame(matrix); mbar.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Label(mbar, text="Filter:").pack(side="left")
        e_flt = ttk.Entry(mbar, textvariable=self.filter_var, width=22)
        e_flt.pack(side="left", padx=(3, 2))
        add_tooltip(e_flt, "Filtert Tools nach ID, Beschreibung, Gruppe oder Tier (z. B. glass, api, G:).")
        b_flt_clr = ttk.Button(mbar, text="✕", width=3, command=lambda: self.filter_var.set(""))
        b_flt_clr.pack(side="left", padx=(0, 8))
        add_tooltip(b_flt_clr, "Filter leeren.")
        self.filter_var.trace_add("write", lambda *_: self._apply_tool_filter())

        for lbl_p, mode_p, tip_p in (
            ("Standard (P/O)", "defaults", "Setzt alle Tools auf den Standard: Prepare/Observe AN, Gated AUS."),
            ("Alle G an", "all_g_on", "Schaltet alle genehmigungspflichtigen (G) Tools ein (erfordert Speichern + Caps)."),
            ("Alle G aus", "all_g_off", "Schaltet alle genehmigungspflichtigen (G) Tools wieder aus."),
            ("Alle aus", "all_off", "Deaktiviert alle MCP-Tools."),
        ):
            bp = ttk.Button(mbar, text=lbl_p, command=lambda mp=mode_p: self._apply_preset(mp))
            bp.pack(side="left", padx=2)
            add_tooltip(bp, tip_p)

        self.lbl_tool_summary = ttk.Label(mbar, text="", foreground="#334155")
        self.lbl_tool_summary.pack(side="right", padx=4)

        canvas = tk.Canvas(matrix, highlightthickness=0, height=240)
        self._matrix_canvas = canvas
        sb = ttk.Scrollbar(matrix, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._bind_matrix_wheel(canvas)
        self._bind_matrix_wheel(inner)
        self._build_tool_matrix(inner)

        # ---- Audit-Viewer ----
        aud = ttk.LabelFrame(self, text="Audit-Log (jede Ausfuehrung wird protokolliert)")
        aud.pack(fill="both", expand=True, padx=10, pady=4)
        arow = ttk.Frame(aud); arow.pack(fill="x", padx=6, pady=2)
        b_aud_ref = ttk.Button(arow, text="Aktualisieren", command=self._refresh_audit)
        b_aud_ref.pack(side="left", padx=3)
        b_aud_exp = ttk.Button(arow, text="Audit exportieren", command=self._export_audit)
        b_aud_exp.pack(side="left", padx=3)
        add_tooltip(b_aud_ref, "Laedt die letzten 200 Eintraege aus data/mcp_audit.jsonl neu.")
        add_tooltip(b_aud_exp, "Kopiert das Audit-Log in den konfigurierten Log-Export-Ordner.")

        self.opt_vars["audit_enabled"] = tk.BooleanVar()
        ck_aud_on = ttk.Checkbutton(arow, text="Audit aktiv", variable=self.opt_vars["audit_enabled"])
        ck_aud_on.pack(side="left", padx=(12, 4))
        add_tooltip(ck_aud_on, "Protokolliert jeden erlaubten und verweigerten MCP-Aufruf nach data/mcp_audit.jsonl.")

        self.opt_vars["audit_log_args"] = tk.BooleanVar()
        ck_aud_args = ttk.Checkbutton(arow, text="Argumente loggen", variable=self.opt_vars["audit_log_args"])
        ck_aud_args.pack(side="left", padx=4)
        add_tooltip(ck_aud_args, "Speichert Parameter-Zusammenfassungen im Audit-Log (sonst '<hidden>').")

        self.txt_audit = tk.Text(aud, height=7, font=("Courier", 8), wrap="none")
        self.txt_audit.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # ---- Save ----
        bar = ttk.Frame(self); bar.pack(fill="x", padx=10, pady=(2, 10))
        ttk.Label(bar, text="Standard: P/O frei, G aus. Aenderungen greifen live nach Speichern (Start/Restart speichert automatisch).").pack(side="left")
        b_save = ttk.Button(bar, text="Speichern", command=self.save_settings)
        b_save.pack(side="right", padx=5)
        add_tooltip(b_save, "Speichert MCP_SETTINGS merge-sicher in config.json (sofort live im laufenden MCP-Server wirksam).")

    def _build_tool_matrix(self, parent):
        NCOLS = 3
        cols = []
        for c in range(NCOLS):
            f = ttk.Frame(parent)
            f.grid(row=0, column=c, sticky="nw", padx=4)
            parent.grid_columnconfigure(c, weight=1)
            self._bind_matrix_wheel(f)
            cols.append([f, 0])  # [Spalten-Frame, geschaetzte Hoehe]

        groups = {}
        for spec in TOOL_CATALOG:
            groups.setdefault(spec.group, []).append(spec)
        self._group_specs = groups

        for g in GROUP_ORDER:
            specs = groups.get(g, [])
            if not specs:
                continue
            target = min(cols, key=lambda cw: cw[1])
            lf = ttk.LabelFrame(target[0], text=f"{g} — {GROUP_NAMES.get(g, '')}")
            lf.pack(fill="x", padx=2, pady=3, anchor="n")
            self._group_frames[g] = lf
            self._bind_matrix_wheel(lf)

            hdr = ttk.Frame(lf); hdr.pack(fill="x")
            self._bind_matrix_wheel(hdr)
            b_on = ttk.Button(hdr, text="an", width=5, command=lambda gg=g: self._group_set(gg, True))
            b_on.pack(side="left", padx=2, pady=1)
            b_off = ttk.Button(hdr, text="aus", width=5, command=lambda gg=g: self._group_set(gg, False))
            b_off.pack(side="left", padx=2)
            add_tooltip(b_on, f"Alle Tools in Gruppe {g} ({GROUP_NAMES.get(g, '')}) aktivieren.")
            add_tooltip(b_off, f"Alle Tools in Gruppe {g} ({GROUP_NAMES.get(g, '')}) deaktivieren.")

            for spec in specs:
                var = tk.BooleanVar()
                var.trace_add("write", lambda *_: self._update_tool_summary())
                self.tool_vars[spec.id] = var
                tag = spec.tier
                if spec.tier == "G" and spec.caps:
                    tag = "G:" + ",".join(spec.caps)
                cb = ttk.Checkbutton(lf, text=f"[{tag}] {spec.id}", variable=var)
                cb.pack(anchor="w", padx=10, pady=0)
                self._tool_cbs[spec.id] = cb
                self._bind_matrix_wheel(cb)
                cap_txt = f" · Caps: {', '.join(spec.caps)}" if spec.caps else ""
                tip = f"{spec.id}  [Tier {spec.tier}{cap_txt}]\n{spec.summary}" if spec.summary else spec.id
                add_tooltip(cb, tip)
            target[1] += len(specs) + 2

    def _update_tool_summary(self):
        lbl = getattr(self, "lbl_tool_summary", None)
        if lbl is None:
            return
        total = len(TOOL_CATALOG)
        enabled = sum(1 for sp in TOOL_CATALOG if bool(self.tool_vars.get(sp.id) and self.tool_vars[sp.id].get()))
        g_on = sum(
            1 for sp in TOOL_CATALOG
            if sp.tier == "G" and bool(self.tool_vars.get(sp.id) and self.tool_vars[sp.id].get())
        )
        g_total = sum(1 for sp in TOOL_CATALOG if sp.tier == "G")
        try:
            lbl.config(text=f"{enabled}/{total} Tools aktiv ({g_on}/{g_total} G)")
        except Exception:
            pass

    @staticmethod
    def _matches_filter(spec, needle: str) -> bool:
        if not needle:
            return True
        hay = f"{spec.id} {spec.summary} {spec.group} {spec.group_name} {spec.tier} {','.join(spec.caps)}".lower()
        return all(tok in hay for tok in needle.split())

    def _apply_tool_filter(self):
        q = (self.filter_var.get() or "").strip().lower()
        for g in GROUP_ORDER:
            lf = self._group_frames.get(g)
            if lf is not None:
                lf.pack_forget()
        for g in GROUP_ORDER:
            lf = self._group_frames.get(g)
            if lf is None:
                continue
            specs = self._group_specs.get(g, [])
            for sp in specs:
                cb = self._tool_cbs.get(sp.id)
                if cb is not None:
                    cb.pack_forget()
            matched_any = False
            for sp in specs:
                cb = self._tool_cbs.get(sp.id)
                if cb is not None and self._matches_filter(sp, q):
                    cb.pack(anchor="w", padx=10, pady=0)
                    matched_any = True
            if matched_any:
                lf.pack(fill="x", padx=2, pady=3, anchor="n")

    def _apply_preset(self, mode: str):
        for spec in TOOL_CATALOG:
            var = self.tool_vars.get(spec.id)
            if var is None:
                continue
            if mode == "defaults":
                var.set(tier_default(spec.tier))
            elif mode == "all_g_on":
                if spec.tier == "G":
                    var.set(True)
            elif mode == "all_g_off":
                if spec.tier == "G":
                    var.set(False)
            elif mode == "all_off":
                var.set(False)
        self._update_tool_summary()

    # ================================================================= Daten
    def _mcp(self) -> dict:
        return self.app.cfg.config.setdefault("MCP_SETTINGS", {})

    def populate_settings(self):
        m = self._mcp()
        srv = m.get("server", {}) or {}
        self.srv_vars["host"].set(srv.get("host", "127.0.0.1"))
        self.srv_vars["port"].set(str(srv.get("port", 8765)))
        self.srv_vars["transport"].set(srv.get("transport", "http") or "http")
        self.srv_vars["autostart"].set(bool(srv.get("autostart", False)))

        self.opt_vars["require_confirm"].set(bool(m.get("require_confirm_each_call", True)))
        self.opt_vars["log_export_dir"].set(m.get("log_export_dir", os.path.join("Claude outputs", "logs")))

        aud = m.get("audit", {}) or {}
        self.opt_vars["audit_enabled"].set(bool(aud.get("enabled", True)))
        self.opt_vars["audit_log_args"].set(bool(aud.get("log_args", True)))

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

        self._update_tool_summary()
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
            "transport": (self.srv_vars["transport"].get() or m["server"].get("transport", "http")).strip() or "http",
            "enabled": self.svc.is_running(),
        })
        m["require_confirm_each_call"] = bool(self.opt_vars["require_confirm"].get())
        m["log_export_dir"] = self.opt_vars["log_export_dir"].get().strip()
        aud = dict(m.get("audit", {}) or {})
        aud.setdefault("file", os.path.join("data", "mcp_audit.jsonl"))
        aud["enabled"] = bool(self.opt_vars["audit_enabled"].get())
        aud["log_args"] = bool(self.opt_vars["audit_log_args"].get())
        m["audit"] = aud
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

    def _persist_mcp_settings(self, quiet: bool = False) -> bool:
        if not self._sync_config_from_ui():
            return False
        cfg = self.app.cfg
        if hasattr(cfg, "update_keys"):
            cfg.update_keys({"MCP_SETTINGS": self._mcp()})
        else:
            cfg.save()
        if not quiet:
            self.lbl_msg.config(text="MCP-Einstellungen gespeichert.", foreground="#1a7f37")
        return True

    def save_settings(self):
        if self._persist_mcp_settings():
            messagebox.showinfo("MCP", "MCP-Einstellungen gespeichert.")

    # ================================================================= Aktionen
    def _group_set(self, group: str, value: bool):
        for spec in TOOL_CATALOG:
            if spec.group == group and spec.id in self.tool_vars:
                self.tool_vars[spec.id].set(value)
        self._update_tool_summary()

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
        if not self._persist_mcp_settings(quiet=True):
            return
        ok, msg = self.svc.start()
        self._persist_mcp_settings(quiet=True)
        self._show_msg(ok, msg)
        self._refresh_status()

    def _on_stop(self):
        ok, msg = self.svc.stop()
        self._persist_mcp_settings(quiet=True)
        self._show_msg(ok, msg)
        self._refresh_status()

    def _on_restart(self):
        if not self._persist_mcp_settings(quiet=True):
            return
        ok, msg = self.svc.restart()
        self._persist_mcp_settings(quiet=True)
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

    def _on_destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        aid = getattr(self, "_status_after_id", None)
        self._status_after_id = None
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass

    def _schedule_status(self):
        self._refresh_status()
        try:
            self._status_after_id = self.after(2000, self._schedule_status)
        except Exception:
            self._status_after_id = None

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
