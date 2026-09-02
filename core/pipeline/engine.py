import os
from typing import Dict, Any, Callable, Optional
from core.application.event_bus import EventBus
from core.domain.exceptions import PatchConflictException


class PipelineEngine:
    def __init__(self, config_mgr: Any, get_archive_path_func: Callable[[], str]) -> None:
        self.cfg = config_mgr
        self.get_archive_path = get_archive_path_func

        # State für Tracing & Frida
        self.logcat_process = None
        self.logcat_out = None
        self.frida_session = None
        self.frida_script = None

        self._steps = {}
        self._register_steps()

    def _register_steps(self):
        """Registriert alle verfügbaren Pipeline-Schritte."""
        from .steps.basic_steps import CmdStep, MirrorWorkspaceStep
        from .steps.trace_steps import TraceStartStep, TraceStopStep
        from .steps.apk_steps import DecompileStep, MergeSplitsStep, ManifestBuildStep
        from .steps.patch_steps import SmartPatchStep, AnchorPatchStep, InjectCustomLibsStep
        from .steps.hook_steps import FridaInjectStep, LSPatchInjectStep

        self._steps = {
            "cmd": CmdStep(),
            "mirror_workspace": MirrorWorkspaceStep(),
            "trace_start": TraceStartStep(),
            "trace_stop": TraceStopStep(),
            "decompile": DecompileStep(),
            "merge_splits": MergeSplitsStep(),
            "manifest_and_build": ManifestBuildStep(),
            "smart_patch": SmartPatchStep(),
            "anchor_patch": AnchorPatchStep(),
            "inject_custom_libs": InjectCustomLibsStep(),
            "inject_frida": FridaInjectStep(),
            "apply_lspatch": LSPatchInjectStep()
        }

    def log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

    def get_unpacked_dir_name(self) -> str:
        strategy = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        return "base_unpacked_apkeditor" if strategy == "apkeditor" else "base_unpacked_apktool"

    def format_cmd(self, text: str, extra_vars: Optional[Dict[str, Any]] = None) -> str:
        vars_dict = self.cfg.get_format_vars()
        if extra_vars: vars_dict.update(extra_vars)
        for k, v in vars_dict.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text

    def run_pipeline(self, pipeline_name: str) -> bool:
        pipeline = self.cfg.config.get("PIPELINES", {}).get(pipeline_name, [])
        if not pipeline:
            self.log(f"[!] Pipeline '{pipeline_name}' ist leer oder nicht definiert.")
            return False

        self.log(f"=== STARTE PIPELINE: {pipeline_name} ===")

        for step_config in pipeline:
            step_name = step_config.get("name", "Unnamed Step")
            step_type = step_config.get("type", "cmd")
            self.log(f"\n--- Schritt: {step_name} ---")

            if step_type not in self._steps:
                self.log(f"[!] Unbekannter Schritt-Typ (oder noch nicht migriert): {step_type}")
                return False

            step_handler = self._steps[step_type]

            try:
                success = step_handler.execute(step_config, self)
            except PatchConflictException as pce:
                self.log(f"[!] Konflikt erkannt in Patch {pce.patch_index + 1}. Warte auf Benutzereingabe...")
                EventBus.publish("PIPELINE_CONFLICT_DETECTED", pce)
                return False
            except Exception as e:
                self.log(f"[!] Systemfehler im Schritt '{step_name}': {e}")
                return False

            if not success:
                self.log(f"\n[!] FEHLER: Pipeline bei Schritt '{step_name}' abgebrochen.")
                EventBus.publish("LOG_INFO", f"[!] Der Schritt '{step_name}' ist fehlgeschlagen.")
                return False

        self.log(f"\n=== PIPELINE {pipeline_name} ERFOLGREICH ===")
        return True

    def attach_frida_usb(self) -> bool:
        """Bleibt in der Engine, da es asynchron per Button aus der UI (nicht Pipeline) aufgerufen wird."""
        script_path = self.cfg.paths.get("COMPILED_FRIDA_SCRIPT", "")
        if not script_path:
            import tempfile
            script_path = os.path.join(tempfile.gettempdir(), "re_frida_project_2", "agent_compiled.js")

        if not os.path.exists(script_path):
            self.log("[!] Kompiliertes Frida-Skript nicht gefunden. Bitte baue die App zuerst neu.")
            return False

        self.log("\n[*] Verbinde mit Frida Gadget über USB...")
        try:
            import frida
            with open(script_path, "r", encoding="utf-8") as f:
                source = f.read()
            device = frida.get_usb_device(timeout=5)
            self.log("[*] USB Gerät gefunden. Suche Gadget...")

            self.frida_session = device.attach("Gadget")
            self.frida_script = self.frida_session.create_script(source)

            def on_message(message: Dict[str, Any], data: Any) -> None:
                if message['type'] == 'send':
                    self.log(f"[Frida] {message['payload']}")
                elif message['type'] == 'error':
                    self.log(f"[Frida ERROR] {message['stack']}")
                else:
                    self.log(f"[Frida] {message}")

            self.frida_script.on('message', on_message)
            self.frida_script.load()
            self.log("[+] Skript erfolgreich in den RAM injiziert! App wird fortgesetzt (Resume)...")
            device.resume("Gadget")
            return True
        except Exception as e:
            self.log(f"[!] Fehler beim Verbinden mit Frida: {e}")
            return False