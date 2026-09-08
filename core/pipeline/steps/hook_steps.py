import os
import shutil
import json
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from core.infrastructure.command_runner import CommandRunner
from services.frida_service import FridaManager
from services.frida_compiler_service import FridaCompilerService
from core.domain.frida_models import FridaConfig


class FridaInjectStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        folder_name = engine_context.get_unpacked_dir_name()
        lib_dir_base = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name, "lib")

        # --- Tarnnamen definieren ---
        gadget_name = "libmetrics.so"
        config_name = "libmetrics.config.so"
        script_name = "libmetrics.script.so"

        # --- Intelligentes Aufräumen ---
        if not engine_context.cfg.config.get("INJECT_FRIDA", False):
            engine_context.log("[*] Frida Injection deaktiviert. Prüfe auf alte Artefakte...")
            cleaned = False

            if os.path.exists(lib_dir_base):
                for arch in os.listdir(lib_dir_base):
                    arch_path = os.path.join(lib_dir_base, arch)
                    if os.path.isdir(arch_path):
                        # Löscht sowohl die Standardnamen als auch die getarnten Dateien
                        for f in ["libfrida-gadget.so", "libfrida-gadget.config.so", "libfrida-script.so",
                                  gadget_name, config_name, script_name]:
                            f_path = os.path.join(arch_path, f)
                            if os.path.exists(f_path):
                                try:
                                    os.remove(f_path)
                                    cleaned = True
                                except Exception:
                                    pass

            if cleaned:
                engine_context.log("[-] Frida-Dateien wurden restlos aus dem Build-Ordner entfernt.")
            return True

        # --- Reguläre Frida Injection basierend auf FridaConfig ---
        engine_context.log("[*] Bereite getarnte Frida Injection (v17+) vor...")

        try:
            lib_dir = os.path.join(lib_dir_base, "arm64-v8a")
            os.makedirs(lib_dir, exist_ok=True)

            gadget_src = os.path.join(engine_context.cfg.config.get("BASE_DIR", ""), "tools", "libfrida-gadget.so")
            if not os.path.exists(gadget_src):
                engine_context.log("[!] libfrida-gadget.so (v17+) fehlt in 'tools/'!")
                return False

            # Gadget getarnt in die APK kopieren
            shutil.copy(gadget_src, os.path.join(lib_dir, gadget_name))

            frida_cfg: FridaConfig = engine_context.cfg.frida_config
            pkg_name = engine_context.cfg.config.get("APP_PACKAGE", "")

            gadget_config_dict = {}

            # --- Dynamische Generierung der Gadget-Config ---
            # Setzt das Lade-Verhalten basierend auf dem neuen UI-Schalter
            load_behavior = "wait" if frida_cfg.pause_on_load else "resume"

            if frida_cfg.mode == "listen":
                gadget_config_dict = {
                    "interaction": {
                        "type": "listen",
                        "address": frida_cfg.host,
                        "port": frida_cfg.port,
                        "on_load": load_behavior
                    }
                }
            elif frida_cfg.mode == "connect":
                gadget_config_dict = {
                    "interaction": {
                        "type": "connect",
                        "address": frida_cfg.host,
                        "port": frida_cfg.port,
                        "on_load": load_behavior
                    }
                }
            elif frida_cfg.mode == "script":
                fm = FridaManager(engine_context.cfg.config.get("BASE_DIR", ""))
                js_code = fm.get_active_code()
                if not js_code:
                    engine_context.log("[!] 'script'-Modus gewählt, aber kein aktives Skript gefunden!")
                    return False

                engine_context.log("[*] Kompiliere Agent für Standalone-Betrieb...")
                compiled_path = FridaCompilerService.compile_script(js_code, engine_context.cfg.paths["ARCHIVE_DIR"])

                if not compiled_path:
                    engine_context.log("[!] Kompilierung fehlgeschlagen.")
                    return False

                target_script_path = os.path.join(lib_dir, script_name)
                shutil.copy(compiled_path, target_script_path)

                gadget_config_dict = {
                    "interaction": {
                        "type": "script",
                        "path": script_name,
                        "on_change": "ignore",  # Verhindert den SELinux-Crash
                        "on_load": load_behavior
                    }
                }
            elif frida_cfg.mode == "script_directory":
                target_dir = frida_cfg.script_directory_path.replace("{APP_PACKAGE}", pkg_name)
                gadget_config_dict = {
                    "interaction": {
                        "type": "script-directory",
                        "path": target_dir,
                        "on_change": "ignore",
                        # Zwingend für SELinux, erfordert aber App-Neustart bei Skript-Änderungen
                        "on_load": load_behavior
                    }
                }

            # Config-Datei getarnt ablegen
            config_path = os.path.join(lib_dir, config_name)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(gadget_config_dict, f, indent=4)

            engine_context.log(
                f"[+] Frida Gadget getarnt als '{gadget_name}' injiziert! Modus: {frida_cfg.mode.upper()}")
            return True

        except Exception as e:
            engine_context.log(f"[!] Schwerer Fehler bei Frida-Injection: {e}")
            return False


class LSPatchInjectStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        if not engine_context.cfg.config.get("INJECT_LSPATCH", False):
            engine_context.log("[*] LSPatch Injection deaktiviert. Überspringe...")
            return True

        engine_context.log("[*] Bereite LSPatch (Non-Root Xposed) vor...")

        lspatch_path = engine_context.cfg.config.get("LSPATCH_JAR", os.path.join("tools", "lspatch.jar"))
        lspatch_jar = os.path.join(engine_context.cfg.config.get("BASE_DIR", ""), lspatch_path)

        module_path = engine_context.cfg.config.get("TRUSTMEALREADY_APK", os.path.join("tools", "TrustMeAlready.apk"))
        module_apk = os.path.join(engine_context.cfg.config.get("BASE_DIR", ""), module_path)

        if not os.path.exists(lspatch_jar) or not os.path.exists(module_apk):
            engine_context.log("[!] lspatch.jar oder TrustMeAlready.apk fehlt im Ordner 'tools'!")
            return False

        dest_dir = engine_context.cfg.paths["DEST_DIR"]
        base_apk = os.path.join(dest_dir, "base.apk")

        if not os.path.exists(base_apk):
            engine_context.log(f"[!] Originale base.apk nicht in {dest_dir} gefunden. (Build fehlgeschlagen?)")
            return False

        cmd = f'java -jar "{lspatch_jar}" "{base_apk}" -m "{module_apk}" -o "{dest_dir}"'
        engine_context.log(f"[*] Starte LSPatch Injection: {cmd}")

        log_file = os.path.join(engine_context.cfg.paths["ARCHIVE_DIR"], "live_cmd_log.txt")
        if not CommandRunner.run_live(engine_context.format_cmd(cmd), engine_context.format_cmd("{BASE_DIR}"),
                                      engine_context.log, log_file):
            engine_context.log("[!] Fehler beim Ausführen von LSPatch.")
            return False

        patched_file = None
        for f in os.listdir(dest_dir):
            if "-lspatched" in f and f.endswith(".apk"):
                patched_file = f
                break

        if patched_file:
            try:
                os.remove(base_apk)
                os.rename(os.path.join(dest_dir, patched_file), base_apk)
            except Exception as e:
                engine_context.log(f"[!] Dateisystem-Fehler beim Umbenennen der LSPatch APK: {e}")
                return False

            engine_context.log("[+] LSPatch erfolgreich angewendet.")

            native_strategy = engine_context.cfg.config.get("NATIVE_LIB_STRATEGY", "zipalign")
            if native_strategy == "zipalign":
                engine_context.log("[*] Stelle Speicher-Alignment nach LSPatch-Eingriff wieder her...")
                cmd_zip = 'zipalign -p -f 4 "base.apk" "aligned_base.apk"'
                if CommandRunner.run_live(engine_context.format_cmd(cmd_zip), dest_dir, engine_context.log, log_file):
                    cmd_move = 'move /Y "aligned_base.apk" "base.apk"' if os.name == 'nt' else 'mv -f "aligned_base.apk" "base.apk"'
                    CommandRunner.run_live(engine_context.format_cmd(cmd_move), dest_dir, engine_context.log, log_file)
                    engine_context.log("[+] Zipalign erfolgreich abgeschlossen.")

            return True
        else:
            engine_context.log("[!] Konnte die gepatchte APK von LSPatch nicht finden.")
            return False