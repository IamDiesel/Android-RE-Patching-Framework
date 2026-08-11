import os
import subprocess
from tkinter import messagebox

class PipelineEngine:
    def __init__(self, config_mgr, logger_func, get_patches_func, get_archive_path_func):
        self.cfg = config_mgr
        self.log = logger_func
        self.get_patches = get_patches_func
        self.get_archive_path = get_archive_path_func
        self.logcat_process = None
        self.logcat_out = None

    def run_pipeline(self, pipeline_name):
        pipeline = self.cfg.config.get("PIPELINES", {}).get(pipeline_name, [])
        if not pipeline:
            self.log(f"[!] Pipeline '{pipeline_name}' ist leer oder nicht definiert.")
            return False
        
        self.log(f"=== STARTE PIPELINE: {pipeline_name} ===")
        
        for step in pipeline:
            step_name = step.get("name", "Unnamed Step")
            step_type = step.get("type", "cmd")
            self.log(f"\n--- Schritt: {step_name} ---")

            success = False
            if step_type == "cmd":
                success = self._run_cmd_step(step)
            elif step_type == "anchor_patch":
                success = self._apply_hex_patches()
            elif step_type == "trace_start":
                success = self._start_trace_step(step)
            elif step_type == "trace_stop":
                success = self._stop_trace_step()
            else:
                self.log(f"[!] Unbekannter Schritt-Typ: {step_type}")

            if not success:
                self.log(f"\n[!] FEHLER: Pipeline bei Schritt '{step_name}' abgebrochen.")
                messagebox.showerror("Pipeline Fehler", f"Der Schritt '{step_name}' ist fehlgeschlagen.")
                return False
        
        self.log(f"\n=== PIPELINE {pipeline_name} ERFOLGREICH ===")
        return True

    def _run_cmd_step(self, step):
        cmd_template = step.get("cmd", "")
        cwd_template = step.get("cwd", "{BASE_DIR}")
        
        extra_vars = {}
        if "{SIGNED_APKS}" in cmd_template:
            dest = self.cfg.paths["DEST_DIR"]
            apks = [f for f in os.listdir(dest) if f.endswith("-aligned-debugSigned.apk")]
            if not apks:
                self.log("[!] Keine signierten APKs für {SIGNED_APKS} gefunden.")
                return False
            extra_vars["SIGNED_APKS"] = " ".join(apks)

        cmd = self._format(cmd_template, extra_vars)
        cwd = self._format(cwd_template)

        if not os.path.exists(cwd):
            os.makedirs(cwd, exist_ok=True)

        self.log(f"> [{cwd}]\n> {cmd}")
        try:
            result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
            if result.stdout: self.log(result.stdout)
            if result.stderr: self.log(f"WARN/ERR: {result.stderr}")
            return result.returncode == 0
        except Exception as e:
            self.log(f"[!] Systemfehler: {e}")
            return False

    def _apply_hex_patches(self):
        lib_path = os.path.join(self.cfg.paths["EXTRACT_DIR"], "lib", "arm64-v8a", "libflutter.so")
        if not os.path.exists(lib_path):
            self.log(f"[!] Bibliothek nicht gefunden: {lib_path}")
            return False
        patches = self.get_patches()
        if not patches:
            self.log("[*] Keine Patches definiert, überspringe.")
            return True
        try:
            with open(lib_path, "r+b") as f:
                for idx, p in enumerate(patches):
                    ram_val = p["ram"].strip()
                    if not ram_val: continue
                    ram_val = int(ram_val, 16)
                    base_val = int(p["base"].strip(), 16)
                    offset = ram_val - base_val
                    patch_hex = p["patch"].replace(" ", "")
                    self.log(f"[*] Patch {idx + 1}: Offset 0x{offset:X}")
                    f.seek(offset)
                    f.write(bytes.fromhex(patch_hex))
            return True
        except Exception as e:
            self.log(f"[!] Patch-Fehler: {e}")
            return False

    def _start_trace_step(self, step):
        app_pkg = self.cfg.config.get("APP_PACKAGE", "")
        pid_res = subprocess.run(f"adb shell pidof {app_pkg}", shell=True, capture_output=True, text=True)
        pid = pid_res.stdout.strip()
        
        if not pid:
            self.log("[!] PID nicht gefunden. Läuft die App?")
            return False

        archive_path = self.get_archive_path()
        if not archive_path: archive_path = self.cfg.paths["ARCHIVE_DIR"]
        trace_file = os.path.join(archive_path, "trace.txt")
        
        self.logcat_out = open(trace_file, "w")
        cmd = self._format(step.get("cmd", ""), {"PID": pid})
        cwd = self._format(step.get("cwd", "{BASE_DIR}"))
        
        self.log(f"> [{cwd}]\n> {cmd}")
        self.logcat_process = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=self.logcat_out, stderr=subprocess.STDOUT)
        self.log(f"[*] Trace gestartet (PID: {pid}). Output in {trace_file}")
        return True

    def _stop_trace_step(self):
        if self.logcat_process:
            self.logcat_process.terminate()
            self.logcat_out.close()
            self.logcat_process = None
            self.log("[*] Trace gestoppt.")
        else:
            self.log("[*] Kein aktiver Trace zum Stoppen.")
        return True

    def _format(self, text, extra_vars=None):
        vars_dict = self.cfg.get_format_vars()
        if extra_vars: vars_dict.update(extra_vars)
        for k, v in vars_dict.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text
