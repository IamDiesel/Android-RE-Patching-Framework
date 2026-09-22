import os
import json
from typing import List, Optional
from core.domain.frida_models import FridaScript, FridaCollection
from core.infrastructure import json_store


def _script_to_dict(s: "FridaScript") -> dict:
    return dict(s.__dict__)


def _script_from_dict(d: dict) -> "FridaScript":
    fields = set(FridaScript.__dataclass_fields__.keys())
    return FridaScript(**{k: v for k, v in (d or {}).items() if k in fields})


def _col_to_dict(c: "FridaCollection") -> dict:
    return dict(c.__dict__)


def _col_from_dict(d: dict) -> "FridaCollection":
    fields = set(FridaCollection.__dataclass_fields__.keys())
    return FridaCollection(**{k: v for k, v in (d or {}).items() if k in fields})


class FridaManager:
    """Verwaltet persistente Frida-Skripte und Sammlungen (Collections).

    Persistenz konfliktsicher (analog native_lib_service/favorite_service): beim
    Speichern wird die frische Platten-Version gelesen und nur die eigenen Deltas
    (Skripte/Collections gekeyt an id, active_script_id skalar) gemergt + atomar
    geschrieben. So ueberschreiben sich GUI und MCP nicht gegenseitig.
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.json_file = os.path.join(data_dir, "frida_scripts.json")

        self.scripts: List[FridaScript] = []
        self.collections: List[FridaCollection] = []
        self.active_script_id: Optional[str] = None
        self._base_scripts = {}
        self._base_cols = {}
        self._base_active = None
        self._mtime = None
        self.load()

    # ---------- Persistenz ----------
    def _read_disk(self) -> dict:
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    return d if isinstance(d, dict) else {}
            except Exception:
                return {}
        return {}

    def _snapshot(self, scripts_d, cols_d, active):
        self._base_scripts = json_store.snapshot_fingerprints(scripts_d, lambda d: d.get("id"))
        self._base_cols = json_store.snapshot_fingerprints(cols_d, lambda d: d.get("id"))
        self._base_active = active
        self._mtime = json_store.file_mtime(self.json_file)

    def load(self):
        data = self._read_disk()
        scripts_d = data.get("scripts", [])
        cols_d = data.get("collections", [])
        self.scripts = [_script_from_dict(s) for s in scripts_d]
        self.collections = [_col_from_dict(c) for c in cols_d]
        self.active_script_id = data.get("active_script_id")
        self._snapshot(scripts_d, cols_d, self.active_script_id)
        if not self.scripts:
            self._create_default_script()

    def reload_if_changed(self) -> bool:
        """Nur neu laden, wenn ein anderer Prozess die Datei geaendert hat."""
        if json_store.file_mtime(self.json_file) != self._mtime:
            self.load()
            return True
        return False

    def save(self):
        """Konfliktsicher speichern: frische Platten-Version lesen, eigene Deltas
        (Skripte/Collections key=id, active_script_id skalar) mergen, atomar schreiben."""
        disk = self._read_disk()
        disk_scripts = disk.get("scripts", []) if isinstance(disk.get("scripts"), list) else []
        disk_cols = disk.get("collections", []) if isinstance(disk.get("collections"), list) else []

        mem_scripts = [_script_to_dict(s) for s in self.scripts]
        mem_cols = [_col_to_dict(c) for c in self.collections]

        merged_scripts, conf_s = json_store.three_way_merge(
            disk_scripts, self._base_scripts, mem_scripts, lambda d: d.get("id"))
        merged_cols, conf_c = json_store.three_way_merge(
            disk_cols, self._base_cols, mem_cols, lambda d: d.get("id"))

        # active_script_id (skalar): haben WIR ihn geaendert -> unserer gewinnt, sonst Platte
        if self.active_script_id != self._base_active:
            active = self.active_script_id
        else:
            active = disk.get("active_script_id", self.active_script_id)

        out = {"active_script_id": active, "scripts": merged_scripts, "collections": merged_cols}
        json_store.atomic_write_text(self.json_file, json.dumps(out, indent=4))

        self.scripts = [_script_from_dict(s) for s in merged_scripts]
        self.collections = [_col_from_dict(c) for c in merged_cols]
        self.active_script_id = active
        self._snapshot(merged_scripts, merged_cols, active)
        if conf_s or conf_c:
            print(f"[frida_service] WARN gleichzeitige Aenderung (scripts={conf_s} collections={conf_c}) "
                  f"— eigene Version gewann.")

    def _create_default_script(self):
        default_code = """import Java from "frida-java-bridge";

console.log("[*] Native RASP Hunter gestartet.");

// 1. Dateizugriffe (I/O) überwachen
const openPtr = Module.findExportByName("libc.so", "open");
if (openPtr) {
    Interceptor.attach(openPtr, {
        onEnter: function (args) {
            try {
                this.path = args[0].readUtf8String();
                if (this.path && (this.path.includes("frida") || this.path.includes("magisk"))) {
                    console.log("[RASP-SCAN] Dateizugriff erkannt: " + this.path);
                }
            } catch (e) {}
        }
    });
}
"""
        self.scripts.append(FridaScript(id="default_rasp", name="Native RASP Hunter (I/O)", code=default_code))
        self.active_script_id = "default_rasp"
        self.save()

    def get_script_by_id(self, script_id: str) -> Optional[FridaScript]:
        return next((s for s in self.scripts if s.id == script_id), None)

    def get_active_code(self) -> str:
        script = self.get_script_by_id(self.active_script_id)
        return script.code if script else ""


def get_shared_frida(app) -> "FridaManager":
    """Liefert die EINE app-weite FridaManager-Instanz (analog get_shared_manager/
    get_shared_favorites): verhindert, dass unabhaengig geladene Instanzen einander
    ueberschreiben."""
    fm = getattr(app, "frida_manager", None)
    if fm is None:
        base_dir = app.cfg.config.get("BASE_DIR", os.getcwd())
        fm = FridaManager(base_dir)
        app.frida_manager = fm
    return fm
