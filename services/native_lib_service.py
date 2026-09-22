import os
import json
import time
import shutil
from typing import List, Optional

from core.domain.native_models import NativeLib
from core.infrastructure import json_store

# Starter-Vorlage fuer neue Libs (baubar out-of-the-box mit link_libs="log dl")
DEFAULT_TEMPLATE = '''#include <android/log.h>
#include <jni.h>

#define TAG "LIBFORGE"

// Laeuft automatisch beim Laden der Lib (System.loadLibrary)
__attribute__((constructor)) static void on_load(void) {
    __android_log_print(ANDROID_LOG_INFO, TAG, "LibForge lib geladen.");
}

// Optional, falls per System.loadLibrary geladen:
JNIEXPORT jint JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void) vm; (void) reserved;
    return JNI_VERSION_1_6;
}
'''


class NativeLibManager:
    """Verwaltet selbst gebaute / importierte native Libs (persistiert in data/native_libs.json)."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, "data")
        self.libs_dir = os.path.join(self.data_dir, "native_libs")
        os.makedirs(self.libs_dir, exist_ok=True)
        self.json_file = os.path.join(self.data_dir, "native_libs.json")
        self.libs: List[NativeLib] = []
        self.load()

    # ---------- Persistenz ----------
    def _read_disk_dicts(self) -> list:
        """Frische Lib-Dicts von der Platte (leer bei fehlender/kaputter Datei)."""
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("libs", [])
            except Exception:
                return []
        return []

    def load(self) -> None:
        dicts = self._read_disk_dicts()
        self.libs = [NativeLib.from_dict(d) for d in dicts]
        self._baseline = json_store.snapshot_fingerprints(dicts, lambda d: d.get("id"))
        self._mtime = json_store.file_mtime(self.json_file)

    def reload_if_changed(self) -> bool:
        """Nur neu laden, wenn ein anderer Prozess die Datei geaendert hat (mtime-gated)."""
        if json_store.file_mtime(self.json_file) != getattr(self, "_mtime", None):
            self.load()
            return True
        return False

    def save(self) -> None:
        """Konfliktsicher speichern: frische Platten-Version lesen, eigene Deltas
        (ggue. Baseline, key=id) mergen, atomar zurueckschreiben. So gehen parallele
        Aenderungen (GUI vs. MCP) nicht verloren."""
        disk = self._read_disk_dicts()
        mem = [l.to_dict() for l in self.libs]
        merged, conflicts = json_store.three_way_merge(
            disk, getattr(self, "_baseline", {}), mem, lambda d: d.get("id"))
        json_store.atomic_write_text(
            self.json_file, json.dumps({"libs": merged}, indent=4))
        self.libs = [NativeLib.from_dict(d) for d in merged]
        self._baseline = json_store.snapshot_fingerprints(merged, lambda d: d.get("id"))
        self._mtime = json_store.file_mtime(self.json_file)
        if conflicts:
            print(f"[native_lib_service] WARN gleichzeitige Aenderung an id(s) "
                  f"{conflicts} — eigene Version gewann, fremde Feldwerte evtl. ueberschrieben.")

    # ---------- Pfade ----------
    def lib_dir(self, lib: NativeLib) -> str:
        d = os.path.join(self.libs_dir, lib.id)
        os.makedirs(d, exist_ok=True)
        return d

    def abspath(self, relpath: str) -> str:
        return os.path.join(self.base_dir, relpath) if relpath else ""

    def so_output_name(self, lib: NativeLib) -> str:
        # Executable (Weg-3-Orakel u.a.): reiner Name ohne lib-Prefix/.so.
        if getattr(lib, "output_kind", "shared") == "executable":
            return lib.name
        return f"lib{lib.name}.so"

    # ---------- CRUD ----------
    def get_all(self) -> List[NativeLib]:
        return self.libs

    def get(self, lib_id: str) -> Optional[NativeLib]:
        return next((l for l in self.libs if l.id == lib_id), None)

    def get_active(self) -> List[NativeLib]:
        """Nur aktive Libs, deren .so tatsaechlich existiert (Grundlage fuer den Inject-Step)."""
        out = []
        for l in self.libs:
            if l.active and l.so_relpath and os.path.exists(self.abspath(l.so_relpath)):
                out.append(l)
        return out

    def get_executables(self) -> List[NativeLib]:
        """Alle Executable-Targets (output_kind == 'executable') mit vorhandenem Binary.

        Grundlage fuer die ExeDeploy-Ziel-Auswahl (Dropdown im Runner-Panel)."""
        out = []
        for l in self.libs:
            if getattr(l, "output_kind", "shared") == "executable" \
                    and l.so_relpath and os.path.exists(self.abspath(l.so_relpath)):
                out.append(l)
        return out

    # ---------- Aktivierung (Modell A: exklusiv-aktiv pro Ausgabe-Name) ----------
    def get_deployable_executables(self) -> List[NativeLib]:
        """Nur AKTIVE Executable-Targets mit vorhandenem Binary (ExeDeploy-Dropdown).

        Modell A: pro Ausgabe-Name ist genau EINES aktiv -> das Dropdown ist eindeutig,
        auch bei vielen gleichnamigen Build-Iterationen."""
        out = []
        for l in self.libs:
            if getattr(l, "output_kind", "shared") == "executable" and l.active \
                    and l.so_relpath and os.path.exists(self.abspath(l.so_relpath)):
                out.append(l)
        return out

    def deactivate_same_name(self, lib: NativeLib) -> int:
        """Deaktiviert alle ANDEREN Targets mit gleichem Ausgabe-Namen wie `lib`.

        Gemeinsame Basis fuer 'Aktiv/Inaktiv' (Controller) und die Auto-Aktivierung
        nach einem erfolgreichen Executable-Build. Returns Anzahl deaktivierter Targets."""
        n = 0
        tgt = self.so_output_name(lib)
        for l in self.libs:
            if l.id != lib.id and l.active and self.so_output_name(l) == tgt:
                l.active = False
                n += 1
        if n:
            self.save()
        return n

    def create(self, name: str = "", origin: str = "built") -> NativeLib:
        lib = NativeLib.new(name=name, origin=origin)
        if origin == "built":
            lib.source_code = DEFAULT_TEMPLATE
        self.libs.append(lib)
        self.save()
        return lib

    def update(self, lib: NativeLib) -> None:
        for i, l in enumerate(self.libs):
            if l.id == lib.id:
                self.libs[i] = lib
                break
        self.save()

    def toggle_active(self, lib_id: str, active: bool) -> None:
        lib = self.get(lib_id)
        if lib:
            lib.active = active
            self.save()

    def set_build_result(self, lib: NativeLib, ok: bool, so_path: str) -> None:
        lib.last_build_ok = ok
        lib.last_build_at = time.strftime("%Y-%m-%d %H:%M:%S")
        if ok and so_path:
            try:
                lib.so_relpath = os.path.relpath(so_path, self.base_dir)
            except ValueError:
                lib.so_relpath = so_path
        self.update(lib)

    def delete(self, lib_id: str) -> None:
        """Entfernt Eintrag + komplettes Lib-Verzeichnis (.so + Quelle)."""
        lib = self.get(lib_id)
        if not lib:
            return
        d = os.path.join(self.libs_dir, lib.id)
        shutil.rmtree(d, ignore_errors=True)
        self.libs = [l for l in self.libs if l.id != lib_id]
        self.save()

    def import_so(self, name: str, src_path: str, description: str = "",
                  abi: str = "arm64-v8a") -> NativeLib:
        """Importiert eine fertige .so ohne Quellcode/Build."""
        lib = NativeLib.new(name=name, origin="imported")
        lib.description = description
        lib.abi = abi
        d = self.lib_dir(lib)
        dst = os.path.join(d, self.so_output_name(lib))
        shutil.copy2(src_path, dst)
        lib.so_relpath = os.path.relpath(dst, self.base_dir)
        lib.last_build_ok = True
        lib.last_build_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.libs.append(lib)
        self.save()
        return lib


def get_shared_manager(app) -> "NativeLibManager":
    """Liefert die EINE app-weite NativeLibManager-Instanz.

    Fix fuer den Zwei-Manager-Bug: frueher hatten LibForge und ExeDeploy je einen
    eigenen NativeLibManager, die native_libs.json unabhaengig luden und
    zurueckschrieben -> der veraltete Stand des einen konnte den anderen ueberschreiben
    (z.B. 'active' zuruecksetzen). Beide Controller teilen sich jetzt diese Instanz."""
    mgr = getattr(app, "native_lib_mgr", None)
    if mgr is None:
        base_dir = app.cfg.config.get("BASE_DIR", os.getcwd())
        mgr = NativeLibManager(base_dir)
        app.native_lib_mgr = mgr
    return mgr
