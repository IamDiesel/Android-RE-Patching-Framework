import os
import shutil
import json
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from core.infrastructure.command_runner import CommandRunner
from services.frida_service import FridaManager


class FridaInjectStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        if not engine_context.cfg.config.get("INJECT_FRIDA", False):
            engine_context.log("[*] Frida Injection deaktiviert. Überspringe...")
            return True

        engine_context.log("[*] Bereite Frida Injection (v17+ via frida-compile) vor...")

        try:
            import tempfile
            folder_name = engine_context.get_unpacked_dir_name()
            lib_dir = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name, "lib", "arm64-v8a")
            os.makedirs(lib_dir, exist_ok=True)

            gadget_src = os.path.join(engine_context.cfg.config.get("BASE_DIR", ""), "tools", "libfrida-gadget.so")
            if not os.path.exists(gadget_src):
                engine_context.log("[!] libfrida-gadget.so (v17+) fehlt in 'tools/'!")
                return False
            shutil.copy(gadget_src, os.path.join(lib_dir, "libfrida-gadget.so"))

            frida_proj_dir = os.path.join(tempfile.gettempdir(), "re_frida_project_2")
            if os.path.exists(frida_proj_dir) and not os.path.exists(os.path.join(frida_proj_dir, ".latest_success")):
                engine_context.log("[*] Bereinige fehlerhaften Node.js Workspace...")
                shutil.rmtree(frida_proj_dir, ignore_errors=True)

            os.makedirs(frida_proj_dir, exist_ok=True)
            pkg_json_path = os.path.join(frida_proj_dir, "package.json")
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            npx_cmd = "npx.cmd" if os.name == "nt" else "npx"

            if not os.path.exists(pkg_json_path):
                engine_context.log(f"[*] Initialisiere stabilen Workspace in: {frida_proj_dir} ...")
                pkg_data = {
                    "name": "re_frida_agent", "private": True,
                    "dependencies": {"frida-java-bridge": "latest"},
                    "devDependencies": {"frida-compile": "latest", "@types/frida-gum": "latest"}
                }
                with open(pkg_json_path, "w", encoding="utf-8") as f: json.dump(pkg_data, f, indent=4)

                tsconfig_data = {
                    "compilerOptions": {"target": "es2020", "lib": ["es2020", "dom"], "strict": False,
                                        "moduleResolution": "node", "types": ["frida-gum"]}
                }
                with open(os.path.join(frida_proj_dir, "tsconfig.json"), "w", encoding="utf-8") as f: json.dump(
                    tsconfig_data, f, indent=4)

                engine_context.log("[*] Führe 'npm install' aus (das dauert kurz)...")
                CommandRunner.run_live(f"{npm_cmd} install", frida_proj_dir, lambda l: engine_context.log(f"[NPM] {l}"))
                with open(os.path.join(frida_proj_dir, ".latest_success"), "w") as f: f.write("ok")

            fm = FridaManager(engine_context.cfg.config.get("BASE_DIR", ""))
            js_code = fm.get_active_code()
            if not js_code:
                engine_context.log("[!] Kein aktives Frida-Skript im Manager gefunden!")
                return False

            raw_script_path = os.path.join(frida_proj_dir, "index.ts")
            with open(raw_script_path, "w", encoding="utf-8") as f:
                f.write(js_code)

            engine_context.log("[*] Kompiliere Agent mit frida-compile (latest)...")
            compiled_out_path = os.path.join(frida_proj_dir, "agent_compiled.js")
            compile_cmd = f"{npx_cmd} --yes frida-compile index.ts -o agent_compiled.js -c"

            success = CommandRunner.run_live(compile_cmd, frida_proj_dir,
                                             lambda l: engine_context.log(f"[frida-compile] {l}"))
            if not success:
                engine_context.log("[!] Kompilierung fehlgeschlagen! Siehe Fehlermeldung oben.")
                return False

            for ext in ["libfrida-gadget.script.so", "libfrida-script.so"]:
                f_path = os.path.join(lib_dir, ext)
                if os.path.exists(f_path): os.remove(f_path)

            config_path = os.path.join(lib_dir, "libfrida-gadget.config.so")
            listen_config = {
                "interaction": {"type": "listen", "address": "127.0.0.1", "port": 27042, "on_load": "wait"}}
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(listen_config, f, indent=4)

            engine_context.cfg.paths["COMPILED_FRIDA_SCRIPT"] = compiled_out_path
            engine_context.log("[+] Frida 17 Gadget mit expliziter Listen-Config injiziert!")
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

        # Nutzen des Loggers direkt über _run_cmd_step equivalent in Step
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