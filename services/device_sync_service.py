import os
import time
import shutil

from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus
from services.device_file_service import DeviceFileService


class DeviceSyncService:
    """Bidirektionaler Local<->Remote-Abgleich mit lokaler, versionierter Sicherung.

    - direction="push": lokaler Ordner -> Geraet. Vorher Snapshot der Quelldateien (Nachvollzug,
      *was* deployt wurde).
    - direction="pull": Geraet -> lokaler Ordner. Vorher Snapshot der zu ueberschreibenden
      lokalen Dateien (Rollback).
    - pkg=None  -> Remote = shell-Domain (z.B. /data/local/tmp) via `adb push/pull`.
    - pkg gesetzt -> Remote = App-Home via `run-as` (wiederverwendete DeviceFileService-Bridge).

    Backups: data/exec_backups/<tag>/<UTC-Zeitstempel>/ ; nur die letzten `keep` Staende bleiben.
    Kein Root, niemals `su`.
    """

    def __init__(self, cfg_manager):
        self.cfg = cfg_manager
        self.file_service = DeviceFileService(cfg_manager)
        base_dir = cfg_manager.config.get("BASE_DIR", os.getcwd())
        self.backup_root = os.path.join(base_dir, "data", "exec_backups")

    def _adb(self) -> str:
        return self.cfg.paths.get("ADB", "adb")

    def _log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

    # ---------- Backup / Retention ----------
    def _backup_local(self, tag: str, files: list, base_dir: str) -> str:
        """Snapshot einer Dateiliste (absolute Pfade) unter Beibehaltung der Relativstruktur."""
        files = [f for f in (files or []) if f and os.path.isfile(f)]
        if not files:
            return ""
        ts = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
        dest = os.path.join(self.backup_root, self._safe(tag), ts)
        for f in files:
            try:
                rel = os.path.relpath(f, base_dir)
            except ValueError:
                rel = os.path.basename(f)
            if rel.startswith(".."):
                rel = os.path.basename(f)
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(f, out)
        self._log(f"[*] Sync-Backup: {len(files)} Datei(en) -> {dest}")
        return dest

    def _prune(self, tag: str, keep: int) -> None:
        d = os.path.join(self.backup_root, self._safe(tag))
        if not os.path.isdir(d):
            return
        stamps = sorted([p for p in os.listdir(d) if os.path.isdir(os.path.join(d, p))])
        for old in stamps[:-keep] if keep > 0 else []:
            shutil.rmtree(os.path.join(d, old), ignore_errors=True)

    @staticmethod
    def _safe(tag: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in (tag or "sync"))

    @staticmethod
    def _walk(local_dir: str) -> list:
        out = []
        for root, _dirs, files in os.walk(local_dir):
            for fn in files:
                out.append(os.path.join(root, fn))
        return out

    # ---------- Sync ----------
    def sync(self, local_dir: str, remote_dir: str, direction: str = "push",
             pkg: str = None, keep: int = 10, tag: str = None) -> bool:
        tag = tag or os.path.basename(os.path.normpath(remote_dir)) or "sync"
        if direction == "push":
            ok = self._push(local_dir, remote_dir, pkg, tag)
        elif direction == "pull":
            ok = self._pull(local_dir, remote_dir, pkg, tag)
        else:
            self._log(f"[!] Sync: unbekannte Richtung '{direction}'.")
            return False
        self._prune(tag, keep)
        return ok

    def _push(self, local_dir: str, remote_dir: str, pkg: str, tag: str) -> bool:
        if not os.path.isdir(local_dir):
            self._log(f"[!] Sync-Push: lokaler Ordner fehlt: {local_dir}")
            return False
        files = self._walk(local_dir)
        if not files:
            self._log("[*] Sync-Push: nichts zu uebertragen.")
            return True
        self._backup_local(tag, files, local_dir)   # Nachvollzug: was wurde deployt

        adb = self._adb()
        ok = True
        for f in files:
            rel = os.path.relpath(f, local_dir).replace("\\", "/")
            remote = f"{remote_dir}/{rel}"
            if pkg:
                if not self.file_service.push_file(pkg, f, remote):
                    ok = False
                    self._log(f"[!] Sync-Push (run-as) fehlgeschlagen: {rel}")
            else:
                sub = os.path.dirname(remote)
                if sub:
                    CommandRunner.run_blocking(f'"{adb}" shell "mkdir -p {sub}"', ".")
                r = CommandRunner.run_blocking(f'"{adb}" push "{f}" "{remote}"', ".")
                if r.returncode != 0:
                    ok = False
                    self._log(f"[!] Sync-Push fehlgeschlagen: {rel}")
        self._log(f"[+] Sync-Push abgeschlossen ({len(files)} Datei(en)) -> {remote_dir}")
        return ok

    def _pull(self, local_dir: str, remote_dir: str, pkg: str, tag: str) -> bool:
        os.makedirs(local_dir, exist_ok=True)
        # vor Ueberschreiben: bestehende lokale Staende sichern (Rollback)
        self._backup_local(tag, self._walk(local_dir), local_dir)

        adb = self._adb()
        if pkg:
            entries = self.file_service.resolve_files_for_download(pkg, [remote_dir])
            ok = True
            for e in entries:
                remote = e["remote_path"]
                rel = remote[len(remote_dir):].lstrip("/") if remote.startswith(remote_dir) \
                    else os.path.basename(remote)
                local = os.path.join(local_dir, rel)
                if not self.file_service.pull_file(pkg, remote, local):
                    ok = False
                    self._log(f"[!] Sync-Pull (run-as) fehlgeschlagen: {rel}")
            self._log(f"[+] Sync-Pull abgeschlossen ({len(entries)} Datei(en)) -> {local_dir}")
            return ok
        else:
            r = CommandRunner.run_blocking(f'"{adb}" pull "{remote_dir}/." "{local_dir}"', ".")
            if r.returncode != 0:
                self._log(f"[!] Sync-Pull fehlgeschlagen: {r.stderr.strip() or r.stdout.strip()}")
                return False
            self._log(f"[+] Sync-Pull abgeschlossen -> {local_dir}")
            return True
