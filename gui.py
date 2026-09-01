import os
import tkinter as tk
from tkinter import ttk, messagebox
import datetime

from config import ConfigManager
from pipeline_engine import PipelineEngine
from history import HistoryManager
from cg_manager import CallGraphManager

from api_inspector import APIInspectorTab
from app_manager import AppManagerTab
from ui_workspace_tab import WorkspaceTab
from ui_history_tab import HistoryTab
from ui_settings_tab import SettingsTab

class KippyReFrameworkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kippy RE-Framework V8 - DAST & Proxy Suite")
        self.geometry("1400x950")

        # Core Models (State & Logic)
        self.cfg = ConfigManager()
        self.history = HistoryManager(self.cfg)
        self.cg = CallGraphManager()
        self.engine = PipelineEngine(self.cfg, self.log, self.get_patch_data, self.get_current_archive_path)

        # Global Session State
        self.is_unpacking = False
        self.current_id = ""
        self.current_archive_path = ""

        self.create_widgets()
        self.generate_new_id()

        # Vollbild / Maximiertes Fenster beim Start:
        if os.name == 'nt':
            self.state('zoomed')  # Für Windows (Maximiert mit Fensterleiste)
        else:
            self.attributes('-zoomed', True)  # Für Linux/macOS

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Initialize Sub-Tabs (KEIN eigenständiges Smali Studio mehr hier!)
        self.app_manager_tab = AppManagerTab(self.notebook, self.cfg.paths.get("SOURCE_DIR", "source"), self.log, self.handle_app_imported)
        self.workspace_tab = WorkspaceTab(self.notebook, self)
        self.api_tab = APIInspectorTab(self.notebook, self.cfg, self.log)
        self.history_tab = HistoryTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)

        # Pack Notebook
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
        """Verhindert Operationen während die APK entpackt wird."""
        if self.is_unpacking:
            messagebox.showwarning("Bitte warten", "Apktool entpackt gerade die App. Bitte warte, bis der Vorgang abgeschlossen ist.")
            return True
        return False

    def get_patch_data(self):
        """Orchestriert die Patch-Daten aus dem Workspace."""
        return self.workspace_tab.get_all_patches()

    def get_current_archive_path(self):
        return self.current_archive_path

    def generate_new_id(self):
        # 1. Neue ID basierend auf Zeit generieren
        self.current_id = f"PID-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"

        # 2. FEHLENDE LOGIK REKONSTRUIEREN: Archiv-Verzeichnis berechnen & anlegen
        if "ARCHIVE_DIR" in self.cfg.paths:
            self.current_archive_path = os.path.join(self.cfg.paths["ARCHIVE_DIR"], self.current_id)
            os.makedirs(self.current_archive_path, exist_ok=True)
        else:
            self.current_archive_path = ""

        # 3. UI updaten
        if hasattr(self, 'workspace_tab'):
            self.workspace_tab.lbl_id.config(text=self.current_id)

    def log(self, msg):
        """Globale Log-Funktion, leitet in den Workspace weiter (Thread-Safe)."""
        def _append():
            if hasattr(self, 'workspace_tab') and hasattr(self.workspace_tab, 'console'):
                self.workspace_tab.console.insert("end", msg + "\n")
                self.workspace_tab.console.see("end")

        self.after(0, _append)

if __name__ == "__main__":
    app = KippyReFrameworkApp()
    app.mainloop()