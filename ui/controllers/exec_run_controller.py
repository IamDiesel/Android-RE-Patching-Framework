import os
import json
import threading

from services.native_lib_service import NativeLibManager, get_shared_manager
from services.device_exec_service import DeviceExecService
from services.device_sync_service import DeviceSyncService

# Felder, die ein Run-Profil ausmachen (fuer benannte Presets speichern/laden)
PROFILE_FIELDS = ("run_dir", "run_args", "run_env", "run_cwd",
                  "runtime_deps", "stage_inputs", "pull_globs", "cleanup_after")


class ExecRunController:
    """Gemeinsame Logik fuer den ExeDeploy-Runner (vom Live-Log-Reiter genutzt).

    Haelt EIN NativeLibManager/Exec/Sync-Set; wird als app.exec_run_controller einmalig
    instanziiert, damit mehrere Bedien-Orte denselben Zustand teilen (DRY, MVC)."""

    def __init__(self, app):
        self.app = app
        self.base_dir = app.cfg.config.get("BASE_DIR", os.getcwd())
        # Gemeinsamer app-weiter Manager (kein zweiter, unabhaengiger Store mehr).
        self.mgr = get_shared_manager(app)
        self.exec_service = DeviceExecService(app.cfg)
        self.sync_service = DeviceSyncService(app.cfg)
        self.last_target_id = None
        self._presets_file = os.path.join(self.base_dir, "data", "exec_run_profiles.json")

    # ---------- Targets ----------
    def refresh(self):
        # Gemeinsamer Manager ist stets aktuell (LibForge speichert dorthin) -> kein
        # Disk-Reload noetig; ein Reload wuerde nur ungespeicherte Aenderungen verwerfen.
        pass

    def list_targets(self):
        # Modell A: nur AKTIVE Executables (pro Ausgabe-Name genau eines) -> das Dropdown
        # ist eindeutig, auch bei vielen gleichnamigen Build-Iterationen. Solange (noch)
        # kein Executable aktiv ist (z.B. Alt-Daten vor diesem Update), Fallback auf alle,
        # damit man nie vor einer leeren Liste steht.
        dep = self.mgr.get_deployable_executables()
        return dep if dep else self.mgr.get_executables()

    def get_target(self, lib_id):
        return self.mgr.get(lib_id)

    # ---------- Benannte Presets (Speichern als / Laden) ----------
    def _load_presets(self) -> dict:
        if os.path.exists(self._presets_file):
            try:
                with open(self._presets_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("presets", {})
            except Exception:
                return {}
        return {}

    def list_presets(self) -> list:
        return sorted(self._load_presets().keys())

    def save_preset(self, name: str, lib) -> None:
        if not name or not lib:
            return
        presets = self._load_presets()
        presets[name] = {k: getattr(lib, k) for k in PROFILE_FIELDS}
        os.makedirs(os.path.dirname(self._presets_file), exist_ok=True)
        with open(self._presets_file, "w", encoding="utf-8") as f:
            json.dump({"presets": presets}, f, indent=4)

    def apply_preset(self, name: str, lib) -> bool:
        data = self._load_presets().get(name)
        if not data or not lib:
            return False
        for k in PROFILE_FIELDS:
            if k in data:
                setattr(lib, k, data[k])
        self.mgr.update(lib)
        return True

    # ---------- Deploy & Run ----------
    def deploy_and_run(self, lib):
        if not lib:
            return
        self.last_target_id = lib.id
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        binary = self.mgr.abspath(lib.so_relpath)
        run_dir = (lib.run_dir or self.app.cfg.config.get("EXEC_RUN_DIR_DEFAULT", "/data/local/tmp")).rstrip("/")

        def task():
            if not self.exec_service.deploy(lib.name, binary, run_dir, list(lib.runtime_deps or [])):
                return
            if not self.exec_service.stage_inputs(lib.name, pkg, list(lib.stage_inputs or []), run_dir):
                return
            self.exec_service.run(lib.name, run_dir, lib.run_cwd or run_dir, lib.run_env or "", lib.run_args or "")

        threading.Thread(target=task, daemon=True).start()

    def stop(self, lib):
        if lib:
            self.exec_service.stop(lib.name)

    def cleanup(self, lib):
        if not lib:
            return
        run_dir = (lib.run_dir or "/data/local/tmp").rstrip("/")
        names = [lib.name]
        names += [os.path.basename(d) for d in (lib.runtime_deps or [])]
        names += [os.path.basename(i) for i in (lib.stage_inputs or [])]
        names += [os.path.basename(g) for g in (lib.pull_globs or [])]

        def task():
            self.exec_service.cleanup(lib.name, run_dir, names)

        threading.Thread(target=task, daemon=True).start()

    def cleanup_targets(self, lib) -> list:
        """Liste der Dateien, die Cleanup im Run-Dir entfernt (fuer Tooltip/Anzeige)."""
        if not lib:
            return []
        out = [lib.name]
        out += [os.path.basename(d) for d in (lib.runtime_deps or [])]
        out += [os.path.basename(i) for i in (lib.stage_inputs or [])]
        out += [os.path.basename(g) for g in (lib.pull_globs or [])]
        return out

    def pull_result(self, lib):
        if not lib or not lib.pull_globs:
            return
        run_dir = (lib.run_dir or "/data/local/tmp").rstrip("/")
        local_dir = self.mgr.lib_dir(lib)

        def task():
            self.exec_service.pull_result(lib.name, run_dir, list(lib.pull_globs), local_dir)

        threading.Thread(target=task, daemon=True).start()

    def result_dir(self, lib) -> str:
        return self.mgr.lib_dir(lib) if lib else ""

    # ---------- Sync ----------
    def sync(self, lib, direction="push"):
        if not lib:
            return
        local_dir = self.mgr.lib_dir(lib)
        remote_dir = (lib.run_dir or "/data/local/tmp").rstrip("/")
        keep = int(self.app.cfg.config.get("EXEC_BACKUP_KEEP", 10))

        def task():
            self.sync_service.sync(local_dir, remote_dir, direction=direction,
                                   pkg=None, keep=keep, tag=lib.name or "exec")

        threading.Thread(target=task, daemon=True).start()

    # ---------- Filemanager-Shortcuts ----------
    def _select_file_explorer(self):
        try:
            self.app.notebook.select(self.app.device_file_tab)
        except Exception:
            pass

    def open_executable_folder(self, lib):
        run_dir = (lib.run_dir if lib else None) or self.app.cfg.config.get("EXEC_RUN_DIR_DEFAULT", "/data/local/tmp")
        if not run_dir.endswith("/"):
            run_dir += "/"
        self._select_file_explorer()
        try:
            self.app.device_file_tab.controller.open_at(run_dir, domain="shell")
        except Exception:
            pass

    def open_app_folder(self):
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        self._select_file_explorer()
        try:
            self.app.device_file_tab.controller.open_at(f"/data/data/{pkg}/", domain="runas")
        except Exception:
            pass


def get_shared_controller(app):
    """Liefert die (einmalige) gemeinsame ExecRunController-Instanz am App-Objekt."""
    ctrl = getattr(app, "exec_run_controller", None)
    if ctrl is None:
        ctrl = ExecRunController(app)
        app.exec_run_controller = ctrl
    return ctrl
