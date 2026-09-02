import os
from typing import Dict, Any
from core.pipeline.step_interface import PipelineStep
from core.infrastructure.command_runner import CommandRunner


class TraceStartStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        app_pkg = engine_context.cfg.config.get("APP_PACKAGE", "")
        adb_bin = engine_context.cfg.paths.get("ADB", "adb")

        res = CommandRunner.run_blocking(f'"{adb_bin}" shell pidof {app_pkg}', "{BASE_DIR}")
        pid = res.stdout.strip()

        if not pid:
            engine_context.log("[!] PID nicht gefunden. Läuft die App?")
            return False

        archive_path = engine_context.get_archive_path()
        if not archive_path:
            archive_path = engine_context.cfg.paths["ARCHIVE_DIR"]

        trace_file = os.path.join(archive_path, "trace.txt")
        engine_context.logcat_out = open(trace_file, "w")

        cmd = engine_context.format_cmd(step_config.get("cmd", ""), {"PID": pid})
        cwd = engine_context.format_cmd(step_config.get("cwd", "{BASE_DIR}"))

        engine_context.log(f"> [{cwd}]\n> {cmd}")
        engine_context.logcat_process = CommandRunner.run_background(cmd, cwd, out_file=engine_context.logcat_out)
        engine_context.log(f"[*] Trace gestartet (PID: {pid}). Output in {trace_file}")
        return True


class TraceStopStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        if engine_context.logcat_process:
            engine_context.logcat_process.terminate()
            if engine_context.logcat_out:
                engine_context.logcat_out.close()
            engine_context.logcat_process = None
            engine_context.log("[*] Trace gestoppt.")
        else:
            engine_context.log("[*] Kein aktiver Trace zum Stoppen.")
        return True