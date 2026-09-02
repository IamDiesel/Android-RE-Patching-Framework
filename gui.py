import os
import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import threading

from core.infrastructure.config_manager import ConfigManager
from core.pipeline.engine import PipelineEngine
from services.history_service import HistoryManager
from services.callgraph_service import CallGraphManager

# In gui.py ganz oben:
from ui.tabs.api_inspector_tab import APIInspectorTab
from ui.tabs.app_manager_tab import AppManagerTab
from ui.tabs.workspace_tab import WorkspaceTab
from ui.tabs.history_tab import HistoryTab
from ui.tabs.settings_tab import SettingsTab

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
        self.history = HistoryManager(self.cfg)
        self.cg = CallGraphManager()

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

        if os.name == 'nt':
            self.state('zoomed')
        else:
            self.attributes('-zoomed', True)

        # Tools asynchron im Hintergrund prüfen/laden und in PATH injizieren
        base_dir = self.cfg.config.get("BASE_DIR", os.getcwd())
        threading.Thread(target=lambda: ToolManager.setup_tools(base_dir), daemon=True).start()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Tabs initialisieren
        self.app_manager_tab = AppManagerTab(self.notebook, self.cfg.paths.get("SOURCE_DIR", "source"),
                                             self.handle_app_imported)
        self.workspace_tab = WorkspaceTab(self.notebook, self)
        self.api_tab = APIInspectorTab(self.notebook, self.cfg)
        self.history_tab = HistoryTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)

        # Tabs zum Notebook hinzufügen
        self.notebook.add(self.app_manager_tab, text="📱 App Manager")
        self.notebook.add(self.workspace_tab, text="🔧 Workspace")
        self.notebook.add(self.api_tab, text="🌐 API Inspector")
        self.notebook.add(self.history_tab, text="📊 Test Management")
        self.notebook.add(self.settings_tab, text="⚙️ Einstellungen")

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

    def log(self, msg):
        def _append():
            if hasattr(self, 'workspace_tab') and hasattr(self.workspace_tab, 'console'):
                self.workspace_tab.console.insert("end", msg + "\n")
                self.workspace_tab.console.see("end")

        self.after(0, _append)

if __name__ == "__main__":
    app = ReFrameworkApp()
    app.mainloop()