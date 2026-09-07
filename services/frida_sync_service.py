import os
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus
from core.domain.frida_models import FridaConfig


class FridaSyncService:
    """
    Verwaltet das Synchronisieren (Push/List/Delete) von Frida-Skripten
    in das private App-Datenverzeichnis (via 'run-as').
    Nutzt den CommandRunner für thread-sichere Ausführung.
    """

    @classmethod
    def _get_base_cmd(cls, adb_path: str, pkg_name: str, cmd: str) -> str:
        """Kapselt Befehle mit run-as, um App-Rechte zu erlangen."""
        return f'"{adb_path}" shell run-as {pkg_name} {cmd}'

    @classmethod
    def list_scripts(cls, adb_path: str, pkg_name: str, config: FridaConfig) -> list:
        target_dir = config.script_directory_path.replace("{APP_PACKAGE}", pkg_name)

        # Ordner sicherstellen
        mkdir_cmd = cls._get_base_cmd(adb_path, pkg_name, f"mkdir -p {target_dir}")
        CommandRunner.run_blocking(mkdir_cmd, ".")

        ls_cmd = cls._get_base_cmd(adb_path, pkg_name, f"ls -1 {target_dir}")
        res = CommandRunner.run_blocking(ls_cmd, ".")

        if res.returncode != 0:
            # Falls Ordner komplett leer ist, gibt ls u.U. einen Fehler aus. Wir fangen das still ab.
            return []

        # Nur .js Dateien berücksichtigen
        scripts = [line.strip() for line in res.stdout.splitlines() if line.strip().endswith(".js")]
        return scripts

    @classmethod
    def push_script(cls, adb_path: str, pkg_name: str, local_compiled_path: str, config: FridaConfig) -> bool:
        """
        Pusht ein kompiliertes Skript auf das Smartphone.
        Zweistufiger Prozess (Tmp -> run-as copy) zur Umgehung von Permissions.
        """
        if not os.path.exists(local_compiled_path):
            EventBus.publish("LOG_INFO", f"[!] Kompiliertes Skript nicht gefunden: {local_compiled_path}")
            return False

        filename = os.path.basename(local_compiled_path)
        tmp_path = f"/data/local/tmp/{filename}"

        target_dir = config.script_directory_path.replace("{APP_PACKAGE}", pkg_name)
        final_path = f"{target_dir}/{filename}"

        EventBus.publish("LOG_INFO", f"[*] Pushe {filename} auf das Gerät...")

        # 1. Sicherstellen, dass das Zielverzeichnis existiert (behebt "No such file or directory")
        mkdir_cmd = cls._get_base_cmd(adb_path, pkg_name, f"mkdir -p {target_dir}")
        CommandRunner.run_blocking(mkdir_cmd, ".")

        # 2. Push nach /data/local/tmp/
        res_push = CommandRunner.run_blocking(f'"{adb_path}" push "{local_compiled_path}" "{tmp_path}"', ".")
        if res_push.returncode != 0:
            EventBus.publish("LOG_INFO", f"[!] Fehler beim ADB Push (tmp): {res_push.stderr}")
            return False

        # 3. Kopieren ins App-Verzeichnis
        cp_cmd = cls._get_base_cmd(adb_path, pkg_name, f"cp {tmp_path} {final_path}")
        res_cp = CommandRunner.run_blocking(cp_cmd, ".")

        if res_cp.returncode != 0:
            EventBus.publish("LOG_INFO", f"[!] Fehler beim 'run-as' Kopieren: {res_cp.stderr}")
            return False

        # 4. Gadget Leserechte setzen
        CommandRunner.run_blocking(cls._get_base_cmd(adb_path, pkg_name, f"chmod 755 {final_path}"), ".")

        # 5. Aufräumen
        CommandRunner.run_blocking(f'"{adb_path}" shell rm "{tmp_path}"', ".")

        EventBus.publish("LOG_INFO", f"[+] Skript erfolgreich installiert: {final_path}")
        return True

    @classmethod
    def delete_script(cls, adb_path: str, pkg_name: str, filename: str, config: FridaConfig) -> bool:
        target_dir = config.script_directory_path.replace("{APP_PACKAGE}", pkg_name)
        final_path = f"{target_dir}/{filename}"

        rm_cmd = cls._get_base_cmd(adb_path, pkg_name, f"rm {final_path}")
        res = CommandRunner.run_blocking(rm_cmd, ".")

        if res.returncode == 0:
            EventBus.publish("LOG_INFO", f"[-] Skript vom Gerät gelöscht: {final_path}")
            return True

        EventBus.publish("LOG_INFO", f"[!] Fehler beim Löschen: {res.stderr}")
        return False