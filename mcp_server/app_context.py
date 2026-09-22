"""
AppContext — Tk-freier Ersatz fuer die GUI-App.

Haelt genau das, was Engine/Services erwarten (cfg, native_lib_mgr, engine,
get_archive_path), damit der MCP-Server dieselben Bausteine wie die GUI nutzt,
ohne Tkinter zu starten.
"""
import os

from core.infrastructure.config_manager import ConfigManager
from core.pipeline.engine import PipelineEngine
from services.native_lib_service import get_shared_manager
from services.favorite_service import FavoriteService, get_shared_favorites
from services.frida_service import get_shared_frida
from services.device_file_service import DeviceFileService
from services.history_service import HistoryManager
from services.adb_devices import apply_android_serial


class AppContext:
    def __init__(self):
        self.cfg = ConfigManager()
        try:
            apply_android_serial(self.cfg, self.cfg.paths.get("ADB", "adb"), probe=True)
        except Exception:
            pass
        self._archive_path = os.path.join(self.cfg.paths.get("ARCHIVE_DIR", "."), "mcp_session")
        os.makedirs(self._archive_path, exist_ok=True)
        self.engine = PipelineEngine(self.cfg, self.get_archive_path)
        self.native_lib_mgr = None       # von get_shared_manager(self) gesetzt
        self._favorites = None
        self.frida_manager = None
        self._device_files = None
        self._history = None

    def get_archive_path(self) -> str:
        return self._archive_path

    # ---- Lazy-Manager (teilen sich Instanzen wie in der GUI) ----
    def libs(self):
        return get_shared_manager(self)   # setzt self.native_lib_mgr

    def favorites(self) -> FavoriteService:
        return get_shared_favorites(self)   # setzt self.favorite_svc

    def frida(self):
        return get_shared_frida(self)       # setzt self.frida_manager

    def device_files(self) -> DeviceFileService:
        if self._device_files is None:
            self._device_files = DeviceFileService(self.cfg)
        return self._device_files

    def history(self) -> HistoryManager:
        if self._history is None:
            self._history = HistoryManager(self.cfg)
        return self._history

    def reload_config(self) -> None:
        """Config neu einlesen (die GUI koennte sie zwischenzeitlich geaendert haben)."""
        self.cfg.load()
        try:
            apply_android_serial(self.cfg, self.cfg.paths.get("ADB", "adb"), probe=False)
        except Exception:
            pass
