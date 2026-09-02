import os
import shutil
from typing import Dict, Any
from core.pipeline.step_interface import PipelineStep
from core.infrastructure.command_runner import CommandRunner

class CmdStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        cmd_template = step_config.get("cmd", "")
        cwd_template = step_config.get("cwd", "{BASE_DIR}")

        extra_vars = {}
        if "{SIGNED_APKS}" in cmd_template:
            dest = engine_context.cfg.paths["DEST_DIR"]
            apks = [f for f in os.listdir(dest) if f.endswith("-debugSigned.apk")]
            if not apks:
                engine_context.log("[!] Keine signierten APKs gefunden.")
                return False
            extra_vars["SIGNED_APKS"] = " ".join(apks)

        cmd = engine_context.format_cmd(cmd_template, extra_vars)
        cwd = engine_context.format_cmd(cwd_template)

        if not os.path.exists(cwd):
            os.makedirs(cwd, exist_ok=True)

        engine_context.log(f"> [{cwd}]\n> {cmd}")
        log_file = os.path.join(engine_context.cfg.paths["ARCHIVE_DIR"], "live_cmd_log.txt")
        return CommandRunner.run_live(cmd, cwd, engine_context.log, log_file)

class MirrorWorkspaceStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        folder_name = engine_context.get_unpacked_dir_name()
        src = os.path.join(engine_context.cfg.paths["APP_SOURCE_DIR"], folder_name)
        dst = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name)

        if not os.path.exists(src):
            engine_context.log(f"[!] Original-Ordner '{folder_name}' fehlt in Source.")
            return False

        engine_context.log(f"[*] Synchronisiere '{folder_name}' in den Destination-Workspace...")
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            engine_context.log("[+] Arbeitskopie erfolgreich synchronisiert.")
            return True
        except Exception as e:
            engine_context.log(f"[!] Fehler beim Spiegeln: {e}")
            return False