import os
import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import threading

from core.infrastructure.config_manager import ConfigManager
from core.pipeline.engine import PipelineEngine
from services.history_service import HistoryManager
from services.callgraph_service import CallGraphManager
from services.adb_devices import apply_android_serial

# In gui.py ganz oben:
from ui.tabs.native_studio_tab import NativeStudioTab
from ui.tabs.debugger_tab import DebuggerTab
from ui.tabs.display_tab import DisplayTab
from ui.tabs.api_inspector_tab import APIInspectorTab
from ui.tabs.app_manager_tab import AppManagerTab
from ui.tabs.workspace_tab import WorkspaceTab
from ui.tabs.history_tab import HistoryTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.device_file_manager_tab import DeviceFileManagerTab
from ui.tabs.mcp_settings_tab import MCPSettingsTab
from core.application.ki_exchange_model import KiExchangeModel

# Core Module
from core.application.event_bus import EventBus
from core.infrastructure.tool_manager import ToolManager


class ReFrameworkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Android RE Patching Framework")

        # ConsoleBus: geteilter Log-Puffer + Senken. Damit laesst sich die Main Console
        # ueberall (Dock, abgedocktes Fenster) aus demselben Puffer neu aufbauen.
        self._log_buf = []
        self._log_sinks = []
        self._LOG_CAP = 5000
        self._console_pop = None
        self._console_tab_state = {}
        self._prev_main_tab = None
        self.geometry("1400x950")

        # Core Models (State & Logic)
        self.cfg = ConfigManager()
        # Mehrgeraet: USB-Default (bzw. konfiguriertes Geraet) als ANDROID_SERIAL setzen,
        # damit adb-Aufrufe bei parallelem WLAN-Geraet nicht mehr scheitern.
        try:
            apply_android_serial(self.cfg, self.cfg.paths.get("ADB", "adb"), probe=True)
        except Exception as _e:
            print(f"[ADB] Geraeteauswahl beim Start: {_e}")
        self.history = HistoryManager(self.cfg)
        self.cg = CallGraphManager()
        # Persistentes Live-Log-Modell (Zustand lebt hier, nicht im Widget) frueh anlegen,
        # damit es alle Events ab Start mitschreibt und die View sich nur daran bindet.
        try:
            from core.application.live_log_model import LiveLogModel
            self.live_log_model = LiveLogModel(self)
        except Exception as _e:
            self.live_log_model = None
            print(f"[LiveLogModel] nicht initialisiert: {_e}")
        # App-Ende: laufende Captures sauber stoppen (haengt NICHT mehr an einem Widget).
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_app_exit)
        except Exception:
            pass
        # Native-Bridge (Smali->Native JNI-Resolver, Strategie-Kette)
        self.native_bridge = None
        try:
            from services.native_bridge import build_chain
            self.native_bridge = build_chain(include_plugins=True)
        except Exception as _e:
            print(f"[NativeBridge] nicht initialisiert: {_e}")

        # Konsolen-Spiegel: schreibt beide EventBus-Konsolen zusaetzlich nach data/console_*.log
        try:
            from services.console_mirror_service import ConsoleMirrorService
            self.console_mirror = ConsoleMirrorService(self.cfg)
        except Exception as _e:
            print(f"[ConsoleMirror] nicht gestartet: {_e}")

        # GUI abonniert den Event-Bus für globale Log-Nachrichten
        EventBus.subscribe("LOG_INFO", self.log)

        # Engine Initialisierung (ohne UI Callback Parameter)
        self.engine = PipelineEngine(self.cfg, self.get_current_archive_path)

        # Global Session State
        self.is_unpacking = False
        self.current_id = ""
        self.current_archive_path = ""

        self.create_widgets()
        self.generate_new_id()

        # Persistierte aktive Patch-Liste (MCP <-> GUI) laden + periodisch spiegeln
        self._init_active_session_reflection()

        if os.name == 'nt':
            self.state('zoomed')
        else:
            self.attributes('-zoomed', True)

        # Tools asynchron im Hintergrund prüfen/laden und in PATH injizieren
        base_dir = self.cfg.config.get("BASE_DIR", os.getcwd())
        threading.Thread(target=lambda: ToolManager.setup_tools(base_dir), daemon=True).start()

    def get_loaded_native_images(self):
        """{lib_name: ElfImage} der aktuell im Native Studio oder LibCarve geladenen Lib(s)."""
        imgs = {}
        try:
            ctrl = self.native_studio.controller
            img = getattr(ctrl, "image", None)
            if img is not None:
                lib = os.path.basename(getattr(ctrl, "image_path", "") or "lib.so")
                imgs[lib] = img
        except Exception:
            pass
        try:
            lc = getattr(getattr(self, "workspace_tab", None), "lib_carve", None)
            lc_ctrl = getattr(lc, "controller", None)
            limg = getattr(lc_ctrl, "image", None)
            if limg is not None:
                llib = os.path.basename(getattr(lc_ctrl, "image_path", "") or "lib.so")
                imgs.setdefault(llib, limg)
        except Exception:
            pass
        return imgs

    def focus_libcarve(self, lib=None, vaddr=None):
        """Selektiert Workspace -> LibCarve (optional mit Lib + VAddr)."""
        try:
            if hasattr(self, "notebook") and hasattr(self, "workspace_tab"):
                self.notebook.select(self.workspace_tab)
            ws = getattr(self, "workspace_tab", None)
            lc = getattr(ws, "lib_carve", None)
            pb = getattr(ws, "patch_book", None)
            if pb is not None and lc is not None:
                pb.select(lc)
            if lc is not None and vaddr is not None and lc.controller.image is not None:
                lc.controller.navigate_to(int(vaddr), add_to_history=True)
        except Exception as _e:
            print(f"[LibCarve] focus_libcarve: {_e}")

    def focus_native_studio(self, lib=None):
        """Selektiert den Analyse-Tab und darin das Native Studio."""
        try:
            self.notebook.select(self.native_studio)
        except Exception as _e:
            print(f"[NativeBridge] focus_native_studio: {_e}")

    def _attach_tooltip(self, widget, text):
        """Zeigt einen kompakten Hover-Tooltip fuer Steuerelemente in der Titelleiste."""
        tip_win = {"w": None}

        def _show(_e=None):
            if tip_win["w"] is not None:
                return
            try:
                tw = tk.Toplevel(widget)
                tw.wm_overrideredirect(True)
                try:
                    tw.wm_attributes("-topmost", True)
                except Exception:
                    pass
                x = widget.winfo_rootx()
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                tw.wm_geometry("+%d+%d" % (x, y))
                lbl = tk.Label(
                    tw, text=text, justify="left", background="#ffffe0",
                    foreground="#111111", relief="solid", borderwidth=1,
                    padx=6, pady=4, font=("Segoe UI", 8),
                )
                lbl.pack()
                tip_win["w"] = tw
            except Exception:
                tip_win["w"] = None

        def _hide(_e=None):
            tw = tip_win.get("w")
            tip_win["w"] = None
            if tw is not None:
                try:
                    tw.destroy()
                except Exception:
                    pass

        try:
            widget.bind("<Enter>", _show, add="+")
            widget.bind("<Leave>", _hide, add="+")
            widget.bind("<ButtonPress>", _hide, add="+")
        except Exception:
            pass

    def _show_top_bar_help(self):
        """Erklaert den Unterschied zwischen lokaler Sandbox (Fork/Merge/Projekt) und KI-Sync (MCP)."""
        msg = (
            "In der oberen Fensterleiste gibt es zwei klar getrennte Bereiche:\n\n"
            "1. Sandbox & Projekt (links: App · Stand · Instanz · Fork · Merge · Projekt)\n"
            "   Verwaltet DEINE lokalen App-Arbeitszustaende und Experimente:\n"
            "   • Stand (Original / Live-Patches / Build-Stand): Waehlt die ausgewaehlte Quelle:\n"
            "     - Original: unveraendertes source/ ohne Patches\n"
            "     - Live-Patches: sauberes source/ + aktive Smali/LibForge/LibCarve/Hex-Patches im RAM/VFS (ohne Build)\n"
            "     - Build-Stand: physisch nach destination/ gebauter App-Stand\n"
            "   • 🔀 Fork: Oeffnet eine isolierte Parallel-Instanz (eigene App-Daten im VFS, eigene Tests),\n"
            "     ohne deine Hauptinstanz 'main' zu veraendern.\n"
            "   • ✅ Merge: Uebernimmt einen erfolgreichen Fork zurueck in die Hauptinstanz 'main'.\n"
            "   • 💾 Projekt: Speichert oder laedt komplette Projektstaende dauerhaft unter data/projects/.\n\n"
            "2. 🤖 KI-Sync (rechts: An KI · Von KI · Abgleich · Reiter)\n"
            "   Synchronisiert die GUI ueber MCP mit dem KI-Assistenten:\n"
            "   • ⬆ An KI: Exportiert deinen aktuellen GUI-Stand der gewaehlten Reiter fuer die KI.\n"
            "   • ⬇ Von KI: Holt neue Befunde, Patches oder Sessions, die die KI erstellt hat, in die GUI.\n"
            "   • ⇅ Abgleich: Zeigt Unterschiede zwischen GUI und KI-Austauschpuffer, ohne etwas zu ueberschreiben.\n"
            "   • Reiter ▾: Legt fest, welche Tabs am KI-Abgleich teilnehmen."
        )
        messagebox.showinfo("Sandbox/Projekt vs. KI-Sync", msg, parent=self)

    def _init_context_vars(self):
        if not hasattr(self, "_ctx_app_var"):
            self._ctx_app_var = tk.StringVar(value=self.cfg.config.get("APP_PACKAGE") or "(keine App)")
        if not hasattr(self, "_ctx_stand_var"):
            self._ctx_stand_var = tk.StringVar(value="Original")
        if not hasattr(self, "_ctx_inst_var"):
            self._ctx_inst_var = tk.StringVar(value="main")
        if not hasattr(self, "_ctx_pos_var"):
            self._ctx_pos_var = tk.StringVar(value="-")

    def _ctx_on_stand(self, _e=None):
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        if ctrl is None:
            return
        sel = (self._ctx_stand_var.get() or "Original").strip()
        zone_map = {
            "Original": "original",
            "Live-Patches": "live",
            "Build-Stand": "destination",
            "Patched": "live",
        }
        zone = zone_map.get(sel, "original")
        if hasattr(ctrl, "set_source_zone"):
            ctrl.set_source_zone(zone, log=False)
        else:
            ctrl.zone = zone
            ctrl.apply_patches = (zone in ("live", "patched"))
            if getattr(ctrl, "instance_state", None) is not None:
                ctrl.instance_state.zone = zone
                ctrl.instance_state.apply_patches = ctrl.apply_patches
        self.log(f"[Kontext] Quelle / Stand: {sel} (zone={zone})")

    def _ctx_fork_now(self):
        from tkinter import simpledialog
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        svc = getattr(ctrl, "snapshot_service", None)
        if svc is None:
            return
        lbl = simpledialog.askstring("Instanz forken", "Name/Notiz fuer den Fork (optional):", parent=self)
        inst = svc.create_fork(ctrl, label=(lbl or ""))
        self._update_context_bar()
        self.log(f"[Fork] Neue isolierte Instanz: {inst.instance_id}")

    def _ctx_merge_now(self):
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        svc = getattr(ctrl, "snapshot_service", None)
        inst = getattr(ctrl, "instance_state", None)
        if svc is None or inst is None or not inst.is_fork():
            messagebox.showinfo("Merge", "Aktive Instanz ist bereits 'main'.", parent=self)
            return
        res = svc.merge_fork(inst.instance_id, target=ctrl)
        self._update_context_bar()
        self.log(f"[Merge] Fork nach main uebernommen: {res.get('merged_fork')}")

    def _ctx_save_project(self):
        from tkinter import simpledialog
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        svc = getattr(ctrl, "snapshot_service", None)
        if svc is None:
            return
        nm = simpledialog.askstring("Projekt speichern", "Name des Projektstands:", parent=self)
        if not nm:
            return
        p = svc.save_project(nm, ctrl)
        self.log(f"[Projekt] Gespeichert unter: {p}")

    def _ctx_load_project(self, pkg, name):
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        svc = getattr(ctrl, "snapshot_service", None)
        if svc is None:
            return
        res = svc.load_project(pkg, name, controller=ctrl)
        self._update_context_bar()
        self.log(f"[Projekt] Geladen: {pkg}/{name} ({len(res.get('smali_patches') or {})} Smali-Patches)")

    def _populate_project_menu(self):
        m = getattr(self, "_proj_menu", None)
        if m is None:
            return
        m.delete(0, "end")
        m.add_command(label="💾 Projektstand speichern…", command=self._ctx_save_project)
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        svc = getattr(ctrl, "snapshot_service", None) if ctrl is not None else None
        projs = []
        if svc is not None:
            try:
                pkg = getattr(ctrl, "_resolve_pkg", lambda: "")() or None
                projs = svc.list_projects(pkg if pkg and pkg != "default.app" else None) or []
            except Exception:
                projs = []
        if projs:
            m.add_separator()
            m.add_command(label="📂 Projektstand laden:", state="disabled")
            for pr in projs:
                ppkg = pr.get("package") or "default.app"
                pname = pr.get("name") or "snapshot"
                m.add_command(
                    label=f"   {ppkg} / {pname}",
                    command=lambda _p=ppkg, _n=pname: self._ctx_load_project(_p, _n),
                )

    def _build_context_controls(self, parent):
        """Baut App/Stand/Instanz/Fork/Merge/Projekt kompakt fuer die Fensterleiste."""
        self._init_context_vars()
        self._context_controls_built = True

        lbl_app_icon = ttk.Label(parent, text="📱")
        lbl_app_icon.pack(side="left", padx=(0, 1))
        lbl_app = ttk.Label(parent, textvariable=self._ctx_app_var, font=("Segoe UI", 9, "bold"))
        lbl_app.pack(side="left", padx=(0, 8))
        self._attach_tooltip(lbl_app, "Aktuell geladene Ziel-App (ueber alle Tabs aktiv).")

        ttk.Label(parent, text="Stand:").pack(side="left")
        cb_stand = ttk.Combobox(
            parent, textvariable=self._ctx_stand_var, width=12, state="readonly",
            values=("Original", "Live-Patches", "Build-Stand"),
        )
        cb_stand.pack(side="left", padx=(2, 8))
        cb_stand.bind("<<ComboboxSelected>>", self._ctx_on_stand)
        self._attach_tooltip(
            cb_stand,
            "Code-/Lib-Quelle (synchron mit 'Zone:' im Debugger):\n"
            "• Original: Unverändertes source/\n"
            "• Live-Patches: Aktive Patches (Smali/LibForge/LibCarve/Hex) direkt im RAM/VFS ohne Build\n"
            "• Build-Stand: Physisch nach destination/ gebaute App",
        )

        ttk.Label(parent, text="🌿").pack(side="left")
        lbl_inst = ttk.Label(parent, textvariable=self._ctx_inst_var, font=("Segoe UI", 9, "bold"))
        lbl_inst.pack(side="left", padx=(2, 6))
        self._attach_tooltip(lbl_inst, "Aktive Sandbox-Instanz ('main' oder isolierter 'fork-...').")

        b_fork = ttk.Button(parent, text="🔀 Fork", width=7, command=self._ctx_fork_now)
        b_fork.pack(side="left", padx=1)
        self._attach_tooltip(b_fork, "Lokale Sandbox: Erstellt eine isolierte Kopie (Fork) zum gefahrlosen Testen ohne 'main' zu aendern.")

        b_merge = ttk.Button(parent, text="✅ Merge", width=8, command=self._ctx_merge_now)
        b_merge.pack(side="left", padx=1)
        self._attach_tooltip(b_merge, "Lokale Sandbox: Uebernimmt den aktiven Fork zurueck in die Hauptinstanz 'main'.")

        self._proj_mb = ttk.Menubutton(parent, text="💾 Projekt ▾")
        self._proj_menu = tk.Menu(self._proj_mb, tearoff=0, postcommand=self._populate_project_menu)
        self._proj_mb.configure(menu=self._proj_menu)
        self._proj_mb.pack(side="left", padx=(2, 4))
        self._attach_tooltip(self._proj_mb, "Projektstand (VFS, Config, Patches, Rules) lokal speichern oder wieder laden.")

    def _build_ki_controls(self, parent):
        """Baut die KI-Buttons + Reiter-Dropdown + Hilfe in 'parent'."""
        import tkinter as _tk
        ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=(2, 8), pady=2)
        lbl_ki = ttk.Label(parent, text="🤖 KI-Sync:")
        lbl_ki.pack(side="left", padx=(0, 4))
        self._attach_tooltip(lbl_ki, "MCP-Synchronisation zwischen deiner GUI und dem KI-Agenten (getrennt von Fork/Merge/Projekt).")

        b_push = ttk.Button(parent, text="⬆ An KI", width=8, command=lambda: self._ki_run("push"))
        b_push.pack(side="left", padx=1)
        self._attach_tooltip(b_push, "Exportiert deinen aktuellen GUI-Zustand der gewaehlten Reiter fuer die KI (MCP).")

        b_pull = ttk.Button(parent, text="⬇ Von KI", width=8, command=lambda: self._ki_run("pull"))
        b_pull.pack(side="left", padx=1)
        self._attach_tooltip(b_pull, "Uebernimmt neue Ergebnisse, Sessions oder Patches der KI live in deine GUI.")

        b_stat = ttk.Button(parent, text="⇅ Abgleich", width=10, command=lambda: self._ki_run("status"))
        b_stat.pack(side="left", padx=1)
        self._attach_tooltip(b_stat, "Vergleicht GUI-Stand und KI-Stand ohne etwas zu ueberschreiben.")

        self._ki_mb = ttk.Menubutton(parent, text="Reiter ▾")
        self._ki_menu = _tk.Menu(self._ki_mb, tearoff=0)
        self._ki_mb.configure(menu=self._ki_menu)
        self._ki_mb.pack(side="left", padx=(4, 2))
        self._attach_tooltip(self._ki_mb, "Waehlt, welche Tabs beim KI-Sync einbezogen werden.")

        b_help = ttk.Button(parent, text="?", width=2, command=self._show_top_bar_help)
        b_help.pack(side="left", padx=(2, 0))
        self._attach_tooltip(b_help, "Unterschied zwischen Sandbox (Fork/Merge/Projekt) und KI-Sync (An KI/Von KI/Abgleich) erklaeren.")

    def _build_ki_bar(self):
        """Zentrale Top-Leiste in der Fensterzeile: links Titel + Projekt/Sandbox, rechts KI-Sync + Fensterknoepfe."""
        import tkinter as _tk
        self._init_context_vars()
        self.ki_model = KiExchangeModel()
        self._ki_vars = {}
        self._ki_all_var = _tk.BooleanVar(value=True)
        self._has_custom_titlebar = False
        _force_native = bool(os.environ.get("ANDRE_NATIVE_TITLEBAR") or os.environ.get("KIPPY_NATIVE_TITLEBAR"))
        try:
            if _force_native:
                raise RuntimeError("native Titelleiste erzwungen (ANDRE_NATIVE_TITLEBAR/KIPPY_NATIVE_TITLEBAR)")
            self._build_custom_titlebar()
            self._has_custom_titlebar = True
        except Exception as e:
            try:
                self.overrideredirect(False)
            except Exception:
                pass
            bar = ttk.Frame(self, relief="groove", padding=(6, 2))
            bar.pack(side="top", fill="x")
            self._build_context_controls(bar)
            pos_lbl = ttk.Label(bar, textvariable=self._ctx_pos_var, foreground="#555")
            pos_lbl.pack(side="left", fill="x", expand=True, padx=6)
            self._build_ki_controls(bar)
            try:
                print("[KI] Titelleisten-Einbau fehlgeschlagen, Streifen-Fallback: %r" % (e,))
            except Exception:
                pass

    def _build_custom_titlebar(self):
        """Eigene Titelleiste (frameless): Titel + Sandbox/Projekt links, KI-Sync + Fensterknoepfe rechts."""
        import tkinter as _tk
        self._win_maxed = False
        self._win_prev_geom = "1280x800+80+60"
        try:
            self.state('normal')
        except Exception:
            pass
        self.overrideredirect(True)
        self.after(10, self._win_taskbar_fix)
        self.after(20, self._win_dwm_chrome)
        self.bind("<Alt-F4>", lambda e: self._win_close())
        tb = tk.Frame(self, bg="#e8e8e8")
        tb.pack(side="top", fill="x")
        self._titlebar = tb
        # Fensterknoepfe ganz rechts (Schliessen aussen)
        tk.Button(tb, text="✕", width=4, relief="flat", bg="#e8e8e8",
                  activebackground="#e81123", activeforeground="white",
                  command=self._win_close).pack(side="right")
        tk.Button(tb, text="□", width=4, relief="flat", bg="#e8e8e8",
                  activebackground="#cccccc", command=self._win_toggle_max).pack(side="right")
        tk.Button(tb, text="–", width=4, relief="flat", bg="#e8e8e8",
                  activebackground="#cccccc", command=self._win_minimize).pack(side="right")
        # KI-Sync rechts (neben den Fensterknoepfen)
        kifr = ttk.Frame(tb)
        kifr.pack(side="right", padx=(6, 10), pady=2)
        self._build_ki_controls(kifr)
        # App-Titel links + Sandbox/Projekt direkt daneben in der Fensterzeile
        title = tk.Label(tb, text="  Android RE Patching Framework", bg="#e8e8e8",
                         font=("Segoe UI", 9, "bold"), anchor="w")
        title.pack(side="left", padx=(2, 8))
        ctxfr = ttk.Frame(tb)
        ctxfr.pack(side="left", padx=(2, 6), pady=2)
        self._build_context_controls(ctxfr)
        pos_lbl = tk.Label(tb, textvariable=self._ctx_pos_var, bg="#e8e8e8",
                           fg="#555555", anchor="w", font=("Segoe UI", 8))
        pos_lbl.pack(side="left", fill="x", expand=True, padx=(4, 6))
        # Ziehen am Titel/Leiste/Fokusfeld (nicht an Buttons), Doppelklick = max/restore
        for w in (tb, title, pos_lbl):
            w.bind("<Button-1>", self._win_drag_start)
            w.bind("<B1-Motion>", self._win_drag_move)
            w.bind("<ButtonRelease-1>", self._win_drag_end)
            w.bind("<Double-Button-1>", lambda e: self._win_toggle_max())
            w.bind("<Button-3>", self._win_sysmenu)
        # Resize-Griffe an den 8 Raendern (frameless hat keine nativen)
        self.after(40, self._win_add_resize_grips)
        # Beim Start maximiert (Arbeitsbereich ohne Taskbar)
        self.after(80, self._win_start_maximized)

    def _win_start_maximized(self):
        try:
            if not self._win_maxed:
                self._win_toggle_max()
        except Exception:
            pass

    def _win_workarea(self):
        return self._win_monitor_work(None, None)

    def _win_toggle_max(self):
        try:
            if self._win_maxed:
                if self._win_prev_geom:
                    self.geometry(self._win_prev_geom)
                self._win_maxed = False
            else:
                try:
                    self._win_prev_geom = self.geometry()
                except Exception:
                    pass
                # Gleicher zuverlaessiger Pfad wie Snap: Monitor unter dem
                # Fenstermittelpunkt (MonitorFromPoint), nicht MonitorFromWindow.
                try:
                    cx = self.winfo_x() + max(1, self.winfo_width()) // 2
                    cy = self.winfo_y() + max(1, self.winfo_height()) // 2
                except Exception:
                    cx, cy = 10, 10
                x, y, w, h = self._win_monitor_work(cx, cy)
                self.geometry("%dx%d+%d+%d" % (w, h, x, y))
                self._win_maxed = True
        except Exception:
            pass

    def _win_minimize(self):
        # overrideredirect-Fenster: kurz Deko an, iconify, bei Wiederherstellung Deko wieder weg.
        try:
            self.overrideredirect(False)
            self.wm_iconify()
            self.bind("<Map>", self._win_on_restore)
        except Exception:
            pass

    def _win_on_restore(self, e=None):
        try:
            self.unbind("<Map>")
            self.overrideredirect(True)
            self.after(10, self._win_taskbar_fix)
        except Exception:
            pass

    def _win_taskbar_fix(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            st = (st & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
            self.wm_withdraw()
            self.after(10, self.wm_deiconify)
        except Exception:
            pass

    def _win_close(self):
        try:
            self.destroy()
        except Exception:
            pass

    def _win_drag_start(self, e):
        self._win_dx = e.x_root - self.winfo_x()
        self._win_dy = e.y_root - self.winfo_y()
        self._win_drag_x0 = e.x_root
        self._win_drag_y0 = e.y_root
        self._win_dragging = True
        self._win_moved = False

    def _win_drag_move(self, e):
        try:
            self._win_moved = True
            if getattr(self, "_win_maxed", False):
                # Erst ab kleiner Bewegung aus dem Vollbild loesen (wie nativ "abreissen").
                if abs(e.x_root - self._win_drag_x0) + abs(e.y_root - self._win_drag_y0) < 6:
                    return
                geo = self._win_prev_geom or "1280x800+80+60"
                try:
                    rw, rh = (int(v) for v in geo.split("+")[0].split("x"))
                except Exception:
                    rw, rh = 1280, 800
                frac = e.x_root / max(1, self.winfo_screenwidth())
                nx = int(e.x_root - rw * frac)
                ny = max(0, e.y_root - 15)
                self.geometry("%dx%d+%d+%d" % (rw, rh, nx, ny))
                self._win_maxed = False
                self._win_dx = e.x_root - self.winfo_x()
                self._win_dy = e.y_root - self.winfo_y()
                return
            self.geometry("+%d+%d" % (e.x_root - self._win_dx, e.y_root - self._win_dy))
        except Exception:
            pass

    def _win_add_resize_grips(self):
        """8 duenne Griff-Flaechen an den Raendern fuer Groessenaenderung (frameless)."""
        try:
            if getattr(self, "_grips", None):
                return
        except Exception:
            pass
        specs = [
            ("n",  "sb_v_double_arrow", dict(relx=0, rely=0, relwidth=1, height=4, anchor="nw")),
            ("s",  "sb_v_double_arrow", dict(relx=0, rely=1, relwidth=1, height=4, anchor="sw")),
            ("w",  "sb_h_double_arrow", dict(relx=0, rely=0, relheight=1, width=4, anchor="nw")),
            ("e",  "sb_h_double_arrow", dict(relx=1, rely=0, relheight=1, width=4, anchor="ne")),
            ("nw", "size_nw_se", dict(relx=0, rely=0, width=10, height=10, anchor="nw")),
            ("ne", "size_ne_sw", dict(relx=1, rely=0, width=10, height=10, anchor="ne")),
            ("sw", "size_ne_sw", dict(relx=0, rely=1, width=10, height=10, anchor="sw")),
            ("se", "size_nw_se", dict(relx=1, rely=1, width=10, height=10, anchor="se")),
        ]
        self._grips = []
        for edge, cursor, place in specs:
            try:
                g = tk.Frame(self, cursor=cursor, bg="#d9d9d9")
            except Exception:
                g = tk.Frame(self, bg="#d9d9d9")
            try:
                g.place(**place)
                g.lift()
            except Exception:
                continue
            g.bind("<Button-1>", lambda e, ed=edge: self._win_resize_start(e, ed))
            g.bind("<B1-Motion>", lambda e, ed=edge: self._win_resize_move(e, ed))
            self._grips.append(g)

    def _win_resize_start(self, e, edge):
        self._rz = {"x": e.x_root, "y": e.y_root, "gx": self.winfo_x(), "gy": self.winfo_y(),
                    "gw": self.winfo_width(), "gh": self.winfo_height()}
        if getattr(self, "_win_maxed", False):
            self._win_maxed = False

    def _win_resize_move(self, e, edge):
        r = getattr(self, "_rz", None)
        if not r:
            return
        try:
            dx = e.x_root - r["x"]
            dy = e.y_root - r["y"]
            x, y, w, h = r["gx"], r["gy"], r["gw"], r["gh"]
            minw, minh = 700, 450
            if "e" in edge:
                w = max(minw, r["gw"] + dx)
            if "s" in edge:
                h = max(minh, r["gh"] + dy)
            if "w" in edge:
                neww = max(minw, r["gw"] - dx)
                x = r["gx"] + (r["gw"] - neww)
                w = neww
            if "n" in edge:
                newh = max(minh, r["gh"] - dy)
                y = r["gy"] + (r["gh"] - newh)
                h = newh
            self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            pass

    def _win_monitor_work(self, px=None, py=None):
        """Arbeitsbereich (ohne Taskbar) des Monitors am Punkt (px,py) oder am Fenster."""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            if px is not None and py is not None:
                class _PT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                mon = u.MonitorFromPoint(_PT(int(px), int(py)), 2)
            else:
                hwnd = u.GetParent(self.winfo_id()) or self.winfo_id()
                mon = u.MonitorFromWindow(hwnd, 2)
            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                w = mi.rcWork
                return w.left, w.top, w.right - w.left, w.bottom - w.top
        except Exception:
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _win_drag_end(self, e):
        """Aero-Snap beim Loslassen: oben = maximieren, Seiten = Haelfte, Ecken = Viertel
        (auf dem Monitor unter dem Cursor)."""
        try:
            moved = getattr(self, "_win_moved", False)
            self._win_dragging = False
            self._win_moved = False
            if not moved or getattr(self, "_win_maxed", False):
                return
            wx, wy, ww, wh = self._win_monitor_work(e.x_root, e.y_root)
            m = 16
            near_left = e.x_root <= wx + m
            near_right = e.x_root >= wx + ww - m
            near_top = e.y_root <= wy + m
            if near_top and not (near_left or near_right):
                self._win_prev_geom = self.geometry()
                self.geometry("%dx%d+%d+%d" % (ww, wh, wx, wy))
                self._win_maxed = True
                return
            if near_left or near_right:
                self._win_prev_geom = self.geometry()
                half = ww // 2
                gx = wx if near_left else wx + half
                band = wh // 3
                if e.y_root <= wy + band:
                    gy, gh = wy, wh // 2
                elif e.y_root >= wy + wh - band:
                    gy, gh = wy + wh // 2, wh - wh // 2
                else:
                    gy, gh = wy, wh
                self.geometry("%dx%d+%d+%d" % (half, gh, gx, gy))
        except Exception:
            pass

    def _win_sysmenu(self, e):
        """Rechtsklick-Systemmenue (Verschieben/Groesse gibt es per Maus; hier Fensterbefehle)."""
        import tkinter as _tk
        try:
            m = _tk.Menu(self, tearoff=0)
            if getattr(self, "_win_maxed", False):
                m.add_command(label="Wiederherstellen", command=self._win_toggle_max)
            else:
                m.add_command(label="Maximieren", command=self._win_toggle_max)
            m.add_command(label="Minimieren", command=self._win_minimize)
            m.add_separator()
            m.add_command(label="Schliessen            Alt+F4", command=self._win_close)
            m.tk_popup(e.x_root, e.y_root)
        except Exception:
            pass
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _win_dwm_chrome(self):
        """Win11: abgerundete Ecken (no-op auf aelteren Systemen)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def _ki_register_providers(self):
        nsc = getattr(getattr(self, "native_studio", None), "controller", None)
        def _m(obj, name):
            f = getattr(obj, name, None)
            return f if callable(f) else None
        self.ki_model.register("native_studio", "\U0001f9ec Native Studio",
                               push=_m(nsc, "studio_session_push"), pull=_m(nsc, "studio_session_pull"),
                               status=_m(nsc, "studio_session_status"))
        dbg_ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        self.ki_model.register("debugger", "\U0001f41e Debugger",
                               push=_m(dbg_ctrl, "dbg_sync"), pull=_m(dbg_ctrl, "dbg_load_session"),
                               status=_m(dbg_ctrl, "dbg_status"))

        def _ws_sync():
            from services.ki_workspace_sync_service import KiWorkspaceSyncService
            cfg_d = getattr(getattr(self, "cfg", None), "config", None) or {}
            return KiWorkspaceSyncService(cfg_d.get("BASE_DIR", os.getcwd()))

        self.ki_model.register(
            "workspace", "\U0001f527 Workspace",
            push=lambda: _ws_sync().workspace_push(self),
            pull=lambda: _ws_sync().workspace_pull(
                self,
                ask_overwrite=lambda msg: messagebox.askyesno("KI-Sync Überschreibschutz", msg, parent=self),
            ),
            status=lambda: _ws_sync().workspace_status(self),
        )
        self.ki_model.register(
            "api", "\U0001f310 API Inspector",
            push=lambda: _ws_sync().api_push(self),
            pull=lambda: _ws_sync().api_pull(self),
            status=lambda: _ws_sync().api_status(self),
        )
        for key, name in (("app_manager", "\U0001f4f1 App Manager"), ("file_explorer", "\U0001f4c2 File Explorer"),
                          ("test_mgmt", "\U0001f4ca Test Management"), ("settings", "⚙ Einstellungen"),
                          ("mcp", "\U0001f50c MCP")):
            self.ki_model.register(key, name)
        self._ki_populate_menu()

    def _ki_populate_menu(self):
        import tkinter as _tk
        m = self._ki_menu
        m.delete(0, "end")
        m.add_checkbutton(label="Alle", variable=self._ki_all_var, command=self._ki_on_all)
        m.add_separator()
        for p in self.ki_model.providers():
            var = _tk.BooleanVar(value=True)
            self._ki_vars[p["key"]] = var
            lbl = p["name"] + ("" if p["has_state"] else "  (kein Zustand)")
            m.add_checkbutton(label=lbl, variable=var,
                              command=lambda k=p["key"]: self._ki_on_toggle(k))

    def _ki_on_all(self):
        on = bool(self._ki_all_var.get())
        self.ki_model.set_all(on)
        for var in self._ki_vars.values():
            var.set(on)

    def _ki_on_toggle(self, key):
        var = self._ki_vars.get(key)
        self.ki_model.toggle(key, bool(var.get()) if var else False)
        self._ki_all_var.set(self.ki_model.is_all())

    def _ki_run(self, op):
        try:
            res = self.ki_model.run(op)
            msg = self.ki_model.format_result(op, res)
        except Exception as e:
            msg = "KI-Abgleich FEHLER: %r" % (e,)
        logf = getattr(self, "log", None)
        (logf if callable(logf) else print)("[KI] " + msg)

    def _build_global_context_bar(self):
        """Abonniert Kontext-Updates (Steuerelemente sitzen jetzt direkt in der Top-Fensterleiste)."""
        if not getattr(self, "_context_controls_built", False):
            bar = ttk.Frame(self, relief="groove", padding=(6, 2))
            bar.pack(side="top", fill="x", padx=10, pady=(2, 0))
            self._build_context_controls(bar)
            ttk.Label(bar, textvariable=self._ctx_pos_var, foreground="#555").pack(side="right", padx=6)

        if not getattr(self, "_ctx_bus_subscribed", False):
            self._ctx_bus_subscribed = True
            for ev in ("APP_IMPORTED", "GLASS_STARTED", "GLASS_STATE", "DEBUG_PAUSED"):
                EventBus.subscribe(ev, lambda _d=None: self.after(0, self._update_context_bar))

    def _update_context_bar(self):
        try:
            self._init_context_vars()
            ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
            pkg = (getattr(ctrl, "_resolve_pkg", lambda: "")() or self.cfg.config.get("APP_PACKAGE") or "(keine App)")
            if pkg == "default.app" and self.cfg.config.get("APP_PACKAGE"):
                pkg = self.cfg.config.get("APP_PACKAGE")
            self._ctx_app_var.set(pkg)
            if ctrl is not None:
                z = (getattr(ctrl, "zone", "original") or "original").lower()
                if z == "live" or (z == "original" and getattr(ctrl, "apply_patches", False)):
                    self._ctx_stand_var.set("Live-Patches")
                elif z in ("destination", "patched"):
                    self._ctx_stand_var.set("Build-Stand")
                else:
                    self._ctx_stand_var.set("Original")
                inst = getattr(ctrl, "instance_state", None)
                self._ctx_inst_var.set(getattr(inst, "instance_id", "main") if inst else "main")
                cm = ctrl.current_method() if hasattr(ctrl, "current_method") else None
                if cm and len(cm) > 46:
                    cm = cm[:43] + "..."
                self._ctx_pos_var.set(cm or "-")
        except Exception:
            pass

    def create_widgets(self):
        self._build_ki_bar()
        self._build_global_context_bar()
        self.root_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.root_paned.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.notebook = ttk.Notebook(self.root_paned)
        self.root_paned.add(self.notebook, weight=6)
        self.console_host = ttk.Frame(self.root_paned)
        self.root_paned.add(self.console_host, weight=1)

        # Tabs initialisieren
        self.app_manager_tab = AppManagerTab(self.notebook, self.cfg.paths.get("SOURCE_DIR", "source"),
                                             self.handle_app_imported, cfg=self.cfg)
        self.workspace_tab = WorkspaceTab(self.notebook, self)
        self._build_global_console()   # geteilte Konsole global unten (vor uebrigen Tabs -> deren Startup-Logs sichtbar)
        self.native_studio = NativeStudioTab(self.notebook, self)  # eigener Haupt-Reiter (GUI-Refactoring)
        self.debugger_tab = DebuggerTab(self.notebook, self)      # eigener Haupt-Reiter (X1/MS-6)
        self.display_tab = DisplayTab(self.notebook, self)        # Anzeige/UI-Saeule (U-Track)
        self.api_tab = APIInspectorTab(self.notebook, self.cfg)    # eigener Haupt-Reiter; Alias app.api_tab bleibt gueltig
        self.history_tab = HistoryTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.device_file_tab = DeviceFileManagerTab(self.notebook, self)
        self.mcp_settings_tab = MCPSettingsTab(self.notebook, self)

        # Tabs zum Notebook hinzufügen
        self.notebook.add(self.app_manager_tab, text="📱 App Manager")
        self.notebook.add(self.workspace_tab, text="🔧 Workspace")
        self.notebook.add(self.native_studio, text="🧬 Native Studio")
        self.notebook.add(self.debugger_tab, text="🐞 Debugger")
        self.notebook.add(self.display_tab, text="📺 Anzeige")
        self.notebook.add(self.api_tab, text="🌐 API Inspector")
        self.notebook.add(self.device_file_tab, text="📂 File Explorer")
        self.notebook.add(self.history_tab, text="📊 Test Management")
        self.notebook.add(self.settings_tab, text="⚙️ Einstellungen")
        self.notebook.add(self.mcp_settings_tab, text="🔌 MCP")
        self._ki_register_providers()

        # MCP-Server-Autostart (HTTP) — nur wenn in den MCP-Settings aktiviert.
        # Deferred, damit die GUI zuerst vollstaendig steht; nutzt denselben
        # Service wie die MCP-Seite (app.mcp_server_svc), damit der Status passt.
        try:
            _srv = (self.cfg.config.get("MCP_SETTINGS", {}) or {}).get("server", {}) or {}
            _svc = getattr(self, "mcp_server_svc", None)
            if _srv.get("autostart") and _svc is not None and not _svc.is_running():
                self.after(1200, self._mcp_autostart)
        except Exception as _e:
            print(f"[MCP autostart] uebersprungen: {_e}")

    def _mcp_autostart(self):
        svc = getattr(self, "mcp_server_svc", None)
        if svc is None:
            return
        try:
            ok, msg = svc.start()
            self.log(f"[MCP] Autostart: {msg}")
            if hasattr(self, "mcp_settings_tab") and hasattr(self.mcp_settings_tab, "_refresh_status"):
                try:
                    self.mcp_settings_tab._refresh_status()
                except Exception:
                    pass
        except Exception as e:
            self.log(f"[MCP] Autostart-Fehler: {e}")

    # ---- Active-Session-Reflektion (MCP-seitige Patches im GUI sichtbar machen) ----
    def _init_active_session_reflection(self):
        """Aktive Patch-Liste aus data/active_session.json laden, in die Workspace-View
        spiegeln und periodisch auf Aenderungen durch den MCP-Prozess pollen."""
        try:
            from services.active_session_service import get_shared_active_session
            self.active_session_svc = get_shared_active_session(self)
            # T4: Start-Reconciliation statt stiller Uebernahme
            if self.cfg.config.get("CLEAR_ACTIVE_ON_START", False):
                try:
                    from core.application.session_state import SessionState
                    SessionState.clear_session()
                    self.active_session_svc.clear()
                    self.log("[ActiveSession] Aktive Session beim Start geleert (CLEAR_ACTIVE_ON_START).")
                except Exception as _e:
                    print(f"[ActiveSession] Start-Clear fehlgeschlagen: {_e}")
            else:
                _n = len(self.active_session_svc.patches())
                if _n:
                    self.log(f"[ActiveSession] {_n} Patch(es) aus vorheriger Session uebernommen "
                             f"- Button 'Aktive Session leeren' entfernt sie.")
            self._reflect_active_session(initial=True)
        except Exception as e:
            print(f"[ActiveSession] Init uebersprungen: {e}")
        self.after(1500, self._poll_active_session)

    def _poll_gui_cmd_queue(self):
        """Verarbeitet asynchrone UI-Steuerkommandos von MCP (`data/gui_cmd_queue.json`)."""
        import json as _json
        base = self.cfg.config.get("BASE_DIR", os.getcwd())
        qpath = os.path.join(base, "data", "gui_cmd_queue.json")
        if not os.path.isfile(qpath):
            return
        try:
            with open(qpath, "r", encoding="utf-8") as fh:
                cmds = _json.load(fh)
            os.remove(qpath)
        except Exception:
            return
        if not isinstance(cmds, list):
            cmds = [cmds]
        dbg = getattr(self, "debugger_tab", None)
        ctrl = getattr(dbg, "ctrl", None)
        for cmd in cmds:
            if not isinstance(cmd, dict):
                continue
            op = cmd.get("cmd")
            if op == "open_debugger" and dbg is not None:
                self.notebook.select(dbg)
            elif op == "set_bp" and ctrl is not None:
                ctrl.add_app_bp(cmd.get("cls"), cmd.get("name"), cmd.get("sig", ""), cmd.get("pc", 0))
            elif op == "set_net_condition" and ctrl is not None:
                ctrl.set_net_condition(cmd.get("mode"), cmd.get("latency_ms"), cmd.get("kbps"))
            elif op == "load_fork" and ctrl is not None and getattr(ctrl, "snapshot_service", None) is not None:
                ctrl.snapshot_service.load_fork(cmd.get("fork_id", ""), controller=ctrl)
                self._update_context_bar()
            elif op == "start_app" and ctrl is not None:
                ctrl.start_app()
            elif op == "cont" and ctrl is not None:
                ctrl.cont()
            elif op == "pause" and ctrl is not None:
                ctrl.pause()
            elif op == "stop" and ctrl is not None:
                ctrl.stop()
            elif op == "step" and ctrl is not None:
                ctrl.step()
            elif op == "step_over" and ctrl is not None:
                ctrl.step_over()
            elif op == "step_out" and ctrl is not None:
                ctrl.step_out()
            elif op == "refresh_ui" and ctrl is not None:
                ctrl.refresh_interactive_ui()
            elif op == "ui_tap" and ctrl is not None:
                ctrl.dispatch_ui_event("tap", **{k: v for k, v in cmd.items() if k != "cmd"})
            elif op == "ui_key" and ctrl is not None:
                ctrl.dispatch_ui_event("key", **{k: v for k, v in cmd.items() if k != "cmd"})
            elif op == "ui_back" and ctrl is not None:
                ctrl.dispatch_ui_event("back")
            elif op == "set_zone" and ctrl is not None:
                z = str(cmd.get("zone") or "live").strip()
                if hasattr(ctrl, "set_source_zone"):
                    ctrl.set_source_zone(z)
                else:
                    ctrl.zone = z
                self._update_context_bar()
            elif op == "open_libcarve":
                ws = getattr(self, "workspace_tab", None)
                lc = getattr(ws, "lib_carve", None) if ws is not None else None
                tgt_lib = str(cmd.get("lib") or "").strip()
                tgt_vaddr = cmd.get("vaddr")
                va_int = None
                if tgt_vaddr not in (None, ""):
                    try:
                        va_int = int(tgt_vaddr, 0) if isinstance(tgt_vaddr, str) else int(tgt_vaddr)
                    except Exception:
                        va_int = None
                draft_code = str(cmd.get("code") or "")
                mode = str(cmd.get("mode") or "asm")
                self.focus_libcarve(lib=tgt_lib or None, vaddr=va_int)
                if lc is not None:
                    def _apply_focus_draft():
                        if draft_code and not getattr(lc.editor, "is_dirty", False):
                            if hasattr(lc.editor, "var_patch_mode"):
                                lc.editor.var_patch_mode.set(mode)
                            lc.editor.set_edit_text(draft_code, mark_dirty=False)
                    cur_img = getattr(lc.controller, "image_path", "") or ""
                    if tgt_lib and (lc.controller.image is None or os.path.basename(cur_img) != os.path.basename(tgt_lib)):
                        cands = lc.controller.discover_libraries()
                        hit = next((c for c in cands if c.get("name") == os.path.basename(tgt_lib)), None)
                        if hit:
                            lc.controller.load_so_path(hit["path"], target_vaddr=va_int, on_ready=_apply_focus_draft)
                    else:
                        if va_int is not None and lc.controller.image is not None:
                            lc.controller.navigate_to(va_int, add_to_history=True)
                        _apply_focus_draft()

    def _poll_active_session(self):
        try:
            svc = getattr(self, "active_session_svc", None)
            if svc is not None and svc.reload_if_changed():
                self._reflect_active_session(initial=False)
            self._poll_gui_cmd_queue()
        except Exception as e:
            print(f"[ActiveSession] Poll-Fehler: {e}")
        finally:
            self.after(1500, self._poll_active_session)

    def _reflect_active_session(self, initial=False):
        svc = getattr(self, "active_session_svc", None)
        if svc is None or not hasattr(self, "workspace_tab"):
            return
        patches = svc.patches()
        # append=False -> View wird komplett auf den Datei-Stand gesetzt; select_tab=False
        # -> kein Fokus-Klau beim periodischen Poll.
        self.workspace_tab.load_patches_from_record(
            {"patches": patches}, append=False, select_tab=False)
        if not initial:
            self.log(f"[MCP] Aktive Patch-Liste aktualisiert ({len(patches)} Patch(es)).")

    def persist_active_session(self):
        """T1: Schreibt die aktive Patch-Liste (SessionState) konfliktsicher nach
        data/active_session.json zurueck -> GUI wird Write-Client (Single Source
        of Truth), damit Add/Remove/Apply Neustarts ueberleben."""
        try:
            svc = getattr(self, "active_session_svc", None)
            if svc is not None:
                from core.application.session_state import SessionState
                svc.save(SessionState.to_patch_list())
        except Exception as e:
            print(f"[ActiveSession] Persist-Fehler: {e}")

    def on_app_switched(self, pkg):
        """Synchronisiert alle Tabs/Indizes/Controller nach einem App-Wechsel."""
        self.cfg._update_paths()
        self.generate_new_id()
        if hasattr(self, "settings_tab") and hasattr(self.settings_tab, "populate_settings"):
            self.settings_tab.populate_settings()
        if hasattr(self, "workspace_tab"):
            if hasattr(self.workspace_tab, "load_current_workspace_meta"):
                self.workspace_tab.load_current_workspace_meta()
            studio = getattr(self.workspace_tab, "smali_studio", None)
            if studio is not None and hasattr(studio, "on_app_changed"):
                studio.on_app_changed(pkg)
            lc = getattr(self.workspace_tab, "lib_carve", None)
            if lc is not None and hasattr(lc.controller, "on_app_changed"):
                lc.controller.on_app_changed(pkg)
        ctrl = getattr(getattr(self, "debugger_tab", None), "ctrl", None)
        if ctrl is not None and hasattr(ctrl, "on_app_changed"):
            ctrl.on_app_changed(pkg)
        if hasattr(self, "display_tab") and hasattr(self.display_tab, "on_app_changed"):
            self.display_tab.on_app_changed(pkg)
        nat_ctrl = getattr(getattr(self, "native_studio", None), "controller", None)
        if nat_ctrl is not None and hasattr(nat_ctrl, "on_app_changed"):
            nat_ctrl.on_app_changed(pkg)
        if hasattr(self, "device_file_tab") and hasattr(self.device_file_tab, "on_app_changed"):
            self.device_file_tab.on_app_changed(pkg)
        EventBus.publish("APP_IMPORTED", {"package": pkg})
        self._update_context_bar()

    def handle_app_imported(self, session_data):
        pkg = session_data["package_name"]
        arch = session_data["architecture"]
        self.log(f"[*] Wende Auto-Config für {pkg} ({arch}) an...")

        self.cfg.config["APP_PACKAGE"] = pkg
        if arch == "ARM64":
            self.cfg.config["SPLIT_NAME"] = "split_config.arm64_v8a"
        elif arch == "x86_64":
            self.cfg.config["SPLIT_NAME"] = "split_config.x86_64"
        elif arch == "LOCAL":
            self.cfg.config["SPLIT_NAME"] = session_data.get("local_split_name", pkg)

        self.cfg.save()
        self.on_app_switched(pkg)
        self.log(f"[+] Workspace für {pkg} konfiguriert. Du kannst nun loslegen.")

    def check_lock(self):
        if self.is_unpacking:
            messagebox.showwarning("Bitte warten",
                                   "Apktool entpackt gerade die App. Bitte warte, bis der Vorgang abgeschlossen ist.")
            return True
        return False

    def get_current_archive_path(self):
        return self.current_archive_path

    def generate_new_id(self):
        self.current_id = f"PID-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"

        if "ARCHIVE_DIR" in self.cfg.paths:
            self.current_archive_path = os.path.join(self.cfg.paths["ARCHIVE_DIR"], self.current_id)
            os.makedirs(self.current_archive_path, exist_ok=True)
        else:
            self.current_archive_path = ""

        if hasattr(self, 'workspace_tab'):
            self.workspace_tab.lbl_id.config(text=self.current_id)

    def _build_global_console(self):
        """Geteilte Konsole (Main Console + Start & Live-Log) als globales Boden-Panel.
        EINZEILIGE Kopfzeile: Notebook-Tabs links, Buttons (Abdocken/ausblenden) als Overlay
        oben rechts in DERSELBEN Zeile -> spart die separate Titelzeile (Hoehe)."""
        host = self.console_host
        # Notebook fuellt den Host direkt (keine eigene Titelzeile mehr).
        try:
            self.workspace_tab.build_console(host)
            self.console = self.workspace_tab.console
            self.console_notebook = self.workspace_tab.console_notebook
            self.register_log_sink(self.console)
        except Exception as e:
            print(f"[Console] Aufbau fehlgeschlagen: {e}"); return
        nb = self.console_notebook
        self._console_visible = True
        # Voll sichtbarer, klickbarer Einblend-Streifen (statt winzigem Icon) fuer den
        # eingeklappten Zustand: volle Breite unten, deutliche Kontur, zentriert.
        restore_bar = ttk.Label(host, text="\u25b8  Konsole einblenden", anchor="center",
                                 relief="solid", borderwidth=1, cursor="hand2", padding=4)
        try:
            ttk.Style().configure("Slim.TButton", padding=(3, 0), font=("Segoe UI", 7))
        except Exception:
            pass

        def _toggle_vis():
            if getattr(self, "_console_visible", True):
                try:
                    self._console_sash_prev = self.root_paned.sashpos(0)
                except Exception:
                    self._console_sash_prev = None
                nb.pack_forget()
                try:
                    btns.place_forget()
                except Exception:
                    pass
                restore_bar.pack(side="bottom", fill="x")
                self._console_visible = False

                def _shrink():
                    try:
                        h = self.root_paned.winfo_height()
                        if h > 70:
                            self.root_paned.sashpos(0, h - 30)
                    except Exception:
                        pass
                self.root_paned.after_idle(_shrink)
            else:
                restore_bar.pack_forget()
                nb.pack(fill="both", expand=True, padx=5, pady=5)
                try:
                    btns.place(relx=1.0, x=-6, y=3, anchor="ne"); btns.lift()
                except Exception:
                    pass
                ic_vis.config(text="\u2013")
                self._console_visible = True
                prev = getattr(self, "_console_sash_prev", None)

                def _restore():
                    try:
                        h = self.root_paned.winfo_height()
                        pos = prev if (prev and prev > 0) else (h * 6 // 7)
                        self.root_paned.sashpos(0, pos)
                    except Exception:
                        pass
                self.root_paned.after_idle(_restore)

        restore_bar.bind("<Button-1>", lambda e: _toggle_vis())

        # Abdocken (nur Main Console, Live-Log bleibt fest) + Ausblenden als kleine Icons.
        def _console_dock_back():
            top = getattr(self, "_console_pop", None)
            if top is None:
                return
            try:
                self.unregister_log_sink(getattr(self, "_console_pop_text", None))
            except Exception:
                pass
            try:
                top.destroy()   # zerstoert auch die abgedockte Live-Log-View (meldet sich beim Modell ab)
            except Exception:
                pass
            self._console_pop = None
            self._console_pop_text = None
            # beide Konsolen-Tabs unten wieder einblenden
            for attr in ("f_main_console", "launcher_logger_tab"):
                try:
                    child = getattr(self.workspace_tab, attr, None)
                    if child is not None:
                        nb.add(child)
                except Exception:
                    pass
            try:
                nb.select(getattr(self.workspace_tab, "f_main_console"))
            except Exception:
                pass
            ic_dock.config(text="\u29c9")

        def _console_abdock():
            if getattr(self, "_console_pop", None) is not None:
                _console_dock_back(); return
            try:
                top = tk.Toplevel(self)
                top.title("\U0001f5a5\ufe0f Konsole & Live-Logging")
                top.geometry("1000x600")
                pop_nb = ttk.Notebook(top)
                pop_nb.pack(fill="both", expand=True)
                # Main Console: eigene bus-gespeiste Textsenke
                f_mc = ttk.Frame(pop_nb)
                pop_nb.add(f_mc, text="\U0001f5a5\ufe0f Main Console")
                txt = tk.Text(f_mc, bg="black", fg="lightgreen")
                txt.pack(fill="both", expand=True, padx=4, pady=4)
                self.register_log_sink(txt)
                self._console_pop = top
                self._console_pop_text = txt
                # Live-Log: neue View, bindet ans persistente Modell -> verlustfrei
                try:
                    from ui.tabs.launcher_logger_tab import LauncherLoggerTab
                    live = LauncherLoggerTab(pop_nb, self.workspace_tab)
                    pop_nb.add(live, text="\U0001f680 Start & Live-Log (App + Exe)")
                except Exception as e:
                    try:
                        self.log(f"[!] Live-Log im Abdock-Fenster: {e}")
                    except Exception:
                        pass
                # beide Tabs unten ausblenden (Zustand bleibt im Puffer/Modell)
                for attr in ("f_main_console", "launcher_logger_tab"):
                    try:
                        child = getattr(self.workspace_tab, attr, None)
                        if child is not None:
                            nb.hide(child)
                    except Exception:
                        pass
                top.protocol("WM_DELETE_WINDOW", _console_dock_back)
                ic_dock.config(text="\u2927")
            except Exception as e:
                try:
                    self.log(f"[!] Konsole abdocken fehlgeschlagen: {e}")
                except Exception:
                    pass

        btns = ttk.Frame(host)
        ic_dock = ttk.Label(btns, text="\u29c9", cursor="hand2", font=("Segoe UI", 9))
        ic_vis = ttk.Label(btns, text="\u2013", cursor="hand2", font=("Segoe UI", 11))
        ic_dock.bind("<Button-1>", lambda e: _console_abdock())
        ic_vis.bind("<Button-1>", lambda e: _toggle_vis())
        ic_dock.pack(side="left", padx=4)
        ic_vis.pack(side="left", padx=4)
        btns.place(relx=1.0, x=-6, y=3, anchor="ne")
        btns.lift()

        # Steuer-Referenzen + pro-Haupttab-Zustandslogik aktivieren
        self._console_ctl = {"toggle_vis": _toggle_vis, "abdock": _console_abdock,
                             "dock_back": _console_dock_back}
        try:
            self.notebook.bind("<<NotebookTabChanged>>", self._on_main_tab_changed, add="+")
            self._prev_main_tab = self.notebook.select()
        except Exception:
            pass

    def log(self, msg):
        line = str(msg) + "\n"
        def _append():
            try:
                self._log_buf.append(line)
                n = len(self._log_buf)
                if n > self._LOG_CAP:
                    del self._log_buf[:n - self._LOG_CAP]
            except Exception:
                pass
            dead = []
            for t in list(getattr(self, "_log_sinks", [])):
                try:
                    t.insert("end", line); t.see("end")
                except Exception:
                    dead.append(t)
            for t in dead:
                try:
                    self._log_sinks.remove(t)
                except Exception:
                    pass
        self.after(0, _append)

    def register_log_sink(self, text_widget):
        """Ein Text-Widget als Log-Senke anmelden; der Puffer wird sofort nachgefuellt."""
        if text_widget is None:
            return
        try:
            if text_widget not in self._log_sinks:
                self._log_sinks.append(text_widget)
            text_widget.insert("end", "".join(self._log_buf)); text_widget.see("end")
        except Exception:
            pass

    def unregister_log_sink(self, text_widget):
        try:
            if text_widget in self._log_sinks:
                self._log_sinks.remove(text_widget)
        except Exception:
            pass

    def _on_app_exit(self):
        try:
            if getattr(self, "live_log_model", None):
                self.live_log_model.shutdown()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _current_console_state(self):
        """Aktueller Zustand der globalen Console als (panel, floating)."""
        panel = "bottom" if getattr(self, "_console_visible", True) else "hidden"
        floating = getattr(self, "_console_pop", None) is not None
        return (panel, floating)

    def _apply_console_state(self, state):
        ctl = getattr(self, "_console_ctl", None)
        if not ctl:
            return
        try:
            panel, floating = state
            is_float = getattr(self, "_console_pop", None) is not None
            if floating and not is_float:
                ctl["abdock"]()
            elif (not floating) and is_float:
                ctl["dock_back"]()
            vis = getattr(self, "_console_visible", True)
            if (panel == "bottom") != vis:
                ctl["toggle_vis"]()
        except Exception:
            pass

    def _on_main_tab_changed(self, event=None):
        """Beim Wechsel des Haupttabs: Zustand des alten Tabs sichern, Zustand des neuen anwenden.
        So merkt sich jeder Haupttab, ob die Console unten, ausgeblendet oder abgedockt war."""
        if not getattr(self, "_console_ctl", None):
            return
        try:
            cur = self.notebook.select()
        except Exception:
            return
        dbg = str(getattr(self, "debugger_tab", "")) or None
        prev = getattr(self, "_prev_main_tab", None)
        if prev and prev != cur and prev != dbg:
            try:
                self._console_tab_state[prev] = self._current_console_state()
            except Exception:
                pass
        self._prev_main_tab = cur
        is_dbg = (dbg is not None and cur == dbg)
        # Der Debugger hat eine eigene Dock-Console (panel-Zone). Dort die globale Boden-Console
        # entfernen, damit nicht zwei Konsolen uebereinander stehen; sonst wieder einfuegen.
        try:
            panes = [str(pp) for pp in self.root_paned.panes()]
            if is_dbg and str(self.console_host) in panes:
                self.root_paned.forget(self.console_host)
            elif (not is_dbg) and str(self.console_host) not in panes:
                self.root_paned.add(self.console_host, weight=1)
        except Exception:
            pass
        if not is_dbg:
            target = self._console_tab_state.get(cur, ("bottom", False))
            self._apply_console_state(target)

if __name__ == "__main__":
    app = ReFrameworkApp()
    app.mainloop()