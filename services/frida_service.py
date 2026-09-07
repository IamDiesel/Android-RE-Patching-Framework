import os
import json
from typing import List, Optional
from core.domain.frida_models import FridaScript, FridaCollection


class FridaManager:
    """Verwaltet persistente Frida-Skripte und Sammlungen (Collections)."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.json_file = os.path.join(data_dir, "frida_scripts.json")

        self.scripts: List[FridaScript] = []
        self.collections: List[FridaCollection] = []
        self.active_script_id: Optional[str] = None
        self.load()

    def load(self):
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    self.scripts = [FridaScript(**s) for s in data.get("scripts", [])]
                    self.collections = [FridaCollection(**c) for c in data.get("collections", [])]
                    self.active_script_id = data.get("active_script_id")
            except Exception:
                self.scripts = []
                self.collections = []
                self.active_script_id = None

        if not self.scripts:
            self._create_default_script()

    def save(self):
        data = {
            "active_script_id": self.active_script_id,
            "scripts": [s.__dict__ for s in self.scripts],
            "collections": [c.__dict__ for c in self.collections]
        }
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

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