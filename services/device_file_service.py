import os
import base64
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus


class DeviceFileService:
    def __init__(self, cfg_manager):
        self.cfg = cfg_manager

    def _get_adb(self):
        return self.cfg.paths.get("ADB", "adb")

    def list_packages(self):
        res = CommandRunner.run_blocking(f'"{self._get_adb()}" shell pm list packages -3', ".")
        if res.returncode == 0:
            return [line.replace("package:", "").strip() for line in res.stdout.strip().split('\n') if
                    line.startswith("package:")]
        return []

    def list_dir(self, pkg: str, path: str):
        cmd = f'"{self._get_adb()}" shell "run-as {pkg} ls -la \'{path}\'"'
        res = CommandRunner.run_blocking(cmd, ".")
        return res.stdout.strip().split('\n') if res.returncode == 0 else []

    def resolve_files_for_download(self, pkg: str, remote_paths: list) -> list:
        """
        Nimmt eine Liste von Pfaden (Dateien und/oder Ordner) und löst sie rekursiv
        in eine flache Liste aller beinhalteten Dateien inkl. ihrer Dateigröße auf.
        """
        paths_str = ' '.join([f"\\\"{p}\\\"" for p in remote_paths])
        # Shell-Skript, das im App-Kontext ausgeführt wird. Differenziert zwischen Datei und Ordner.
        script = f"for p in {paths_str}; do if [ -d \"$p\" ]; then find \"$p\" -type f -exec ls -ln {{}} +; else ls -ln \"$p\"; fi; done"
        cmd = f'"{self._get_adb()}" shell "run-as {pkg} sh -c \'{script}\'"'

        res = CommandRunner.run_blocking(cmd, ".")
        results = []

        for line in res.stdout.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith("total ") or "Permission denied" in line: continue

            # Format: -rw-rw---- 1 10123 10123 4096 2023-10-10 10:10 /data/data/pkg/file.txt
            parts = line.split(maxsplit=7)
            if len(parts) >= 8:
                try:
                    size = int(parts[4])
                    filepath = parts[7]
                    results.append({"remote_path": filepath, "size": size})
                except ValueError:
                    pass
        return results

    def pull_file(self, pkg: str, remote_path: str, local_path: str) -> bool:
        cmd = f'"{self._get_adb()}" shell "run-as {pkg} base64 \'{remote_path}\'"'
        res = CommandRunner.run_blocking(cmd, ".")

        if res.returncode == 0 and res.stdout.strip():
            try:
                decoded = base64.b64decode(res.stdout.strip())
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(decoded)
                return True
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Base64 Decode Fehler bei '{remote_path}': {e}")
        return False

    def push_file(self, pkg: str, local_path: str, remote_path: str) -> bool:
        tmp_path = f"/data/local/tmp/{os.path.basename(local_path)}"
        res_push = CommandRunner.run_blocking(f'"{self._get_adb()}" push "{local_path}" "{tmp_path}"', ".")
        if res_push.returncode != 0: return False

        CommandRunner.run_blocking(f'"{self._get_adb()}" shell "chmod 644 \'{tmp_path}\'"', ".")
        res = CommandRunner.run_blocking(
            f'"{self._get_adb()}" shell "run-as {pkg} cp \'{tmp_path}\' \'{remote_path}\'"', ".")
        CommandRunner.run_blocking(f'"{self._get_adb()}" shell "rm \'{tmp_path}\'"', ".")
        return res.returncode == 0

    def delete_file(self, pkg: str, remote_path: str) -> bool:
        res = CommandRunner.run_blocking(f'"{self._get_adb()}" shell "run-as {pkg} rm -r \'{remote_path}\'"', ".")
        return res.returncode == 0