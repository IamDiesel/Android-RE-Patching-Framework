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
from ui.tabs.analyse_tab import AnalyseTab
from ui.tabs.app_manager_tab import AppManagerTab
from ui.tabs.workspace_tab import WorkspaceTab
from ui.tabs.history_tab import HistoryTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.device_file_manager_tab import DeviceFileManagerTab
from ui.tabs.mcp_settings_tab import MCPSettingsTab

# Core Module
from core.application.event_bus import EventBus
from core.infrastructure.tool_manager import ToolManager


class ReFrameworkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kippy RE-Framework V8 - DAST & Proxy Suite")
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
        """{lib_name: ElfImage} der aktuell im Native Studio geladenen Lib(s)."""
        try:
            ctrl = self.native_studio.controller
            img = getattr(ctrl, "image", None)
            if img is None:
                return {}
            lib = os.path.basename(getattr(ctrl, "image_path", "") or "lib.so")
            return {lib: img}
        except Exception:
            return {}

    def focus_native_studio(self, lib=None):
        """Selektiert den Analyse-Tab und darin das Native Studio."""
        try:
            self.notebook.select(self.analyse_tab)
            self.analyse_tab._nb.select(self.analyse_tab.native_studio_tab)
        except Exception as _e:
            print(f"[NativeBridge] focus_native_studio: {_e}")

    def create_widgets(self):
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
        self.analyse_tab = AnalyseTab(self.notebook, self)
        self.api_tab = self.analyse_tab.api_tab  # Alias: bestehende app.api_tab-Referenzen bleiben gueltig
        self.native_studio = self.analyse_tab.native_studio_tab  # Alias: Smali->Native-Bruecke
        self.history_tab = HistoryTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.device_file_tab = DeviceFileManagerTab(self.notebook, self)
        self.mcp_settings_tab = MCPSettingsTab(self.notebook, self)

        # Tabs zum Notebook hinzufügen
        self.notebook.add(self.app_manager_tab, text="📱 App Manager")
        self.notebook.add(self.workspace_tab, text="🔧 Workspace")
        self.notebook.add(self.analyse_tab, text="🔬 Analyse")
        self.notebook.add(self.device_file_tab, text="📂 File Explorer")
        self.notebook.add(self.history_tab, text="📊 Test Management")
        self.notebook.add(self.settings_tab, text="⚙️ Einstellungen")
        self.notebook.add(self.mcp_settings_tab, text="🔌 MCP")

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

    def _poll_active_session(self):
        try:
            svc = getattr(self, "active_session_svc", None)
            if svc is not None and svc.reload_if_changed():
                self._reflect_active_session(initial=False)
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
        self.settings_tab.populate_settings()
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
        except Exception as e:
            print(f"[Console] Aufbau fehlgeschlagen: {e}"); return
        nb = self.console_notebook
        self._console_visible = True
        # Schmaler Platzhalter fuer den eingeklappten Zustand (haelt die Button-Zeile sichtbar).
        spacer = ttk.Frame(host, height=16)
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
                nb.pack_forget(); spacer.pack(side="top", fill="x")
                btn_vis.config(text="🖥 Konsole ▸"); self._console_visible = False

                def _shrink():
                    try:
                        h = self.root_paned.winfo_height()
                        if h > 60:
                            self.root_paned.sashpos(0, h - 22)
                    except Exception:
                        pass
                self.root_paned.after_idle(_shrink)
            else:
                spacer.pack_forget(); nb.pack(fill="both", expand=True, padx=5, pady=5)
                btn_vis.config(text="▾ ausblenden"); self._console_visible = True
                prev = getattr(self, "_console_sash_prev", None)

                def _restore():
                    try:
                        h = self.root_paned.winfo_height()
                        pos = prev if (prev and prev > 0) else (h * 6 // 7)
                        self.root_paned.sashpos(0, pos)
                    except Exception:
                        pass
                self.root_paned.after_idle(_restore)

        # Buttons in EINER Zeile mit den Tabs: Overlay oben rechts ueber dem Tab-Streifen.
        btns = ttk.Frame(host)
        btn_dock = ttk.Button(btns, text="⧉ Abdocken", width=10, style="Slim.TButton")
        btn_dock.config(command=lambda: self.workspace_tab.toggle_dock(
            host, "\U0001f5a5️ Konsole & Live-Logging", host, self.root_paned, 1, btn_dock))
        btn_vis = ttk.Button(btns, text="▾ ausblenden", width=12, command=_toggle_vis, style="Slim.TButton")
        btn_dock.pack(side="left", padx=2)
        btn_vis.pack(side="left", padx=2)
        btns.place(relx=1.0, x=-6, y=4, anchor="ne")
        btns.lift()

    def log(self, msg):
        def _append():
            if hasattr(self, 'workspace_tab') and hasattr(self.workspace_tab, 'console'):
                self.workspace_tab.console.insert("end", msg + "\n")
                self.workspace_tab.console.see("end")

        self.after(0, _append)

if __name__ == "__main__":
    app = ReFrameworkApp()
    app.mainloop()