import os
import json
import shutil
import tempfile
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus


class FridaCompilerService:
    """
    Kompiliert Frida-Code mittels Node.js und frida-compile in ein
    ausführbares Agent-Skript und gibt den Fortschritt live an die GUI weiter.
    """

    @classmethod
    def _setup_node_workspace(cls, workspace_dir: str, log_file: str) -> bool:
        if os.path.exists(workspace_dir) and os.path.exists(os.path.join(workspace_dir, ".latest_success")):
            return True

        EventBus.publish("LOG_INFO", f"[*] Initialisiere Frida Node.js Workspace in: {workspace_dir} ...")
        shutil.rmtree(workspace_dir, ignore_errors=True)
        os.makedirs(workspace_dir, exist_ok=True)

        pkg_json_path = os.path.join(workspace_dir, "package.json")
        pkg_data = {
            "name": "re_frida_agent",
            "private": True,
            "dependencies": {},
            "devDependencies": {"frida-compile": "latest", "@types/frida-gum": "latest"}
        }
        with open(pkg_json_path, "w", encoding="utf-8") as f:
            json.dump(pkg_data, f, indent=4)

        tsconfig_data = {
            "compilerOptions": {
                "target": "es2020", "lib": ["es2020", "dom"], "strict": False,
                "moduleResolution": "node", "types": ["frida-gum"]
            }
        }
        with open(os.path.join(workspace_dir, "tsconfig.json"), "w", encoding="utf-8") as f:
            json.dump(tsconfig_data, f, indent=4)

        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        EventBus.publish("LOG_INFO", "[*] Führe 'npm install' aus (Live-Log folgt)...")

        # Das Live-Logging fängt jede Ausgabe von npm ab und zeigt sie im Workspace an
        success = CommandRunner.run_live(
            f"{npm_cmd} install",
            workspace_dir,
            lambda l: EventBus.publish("LOG_INFO", f"[NPM] {l.strip()}"),
            log_file
        )

        if success:
            with open(os.path.join(workspace_dir, ".latest_success"), "w") as f:
                f.write("ok")
            EventBus.publish("LOG_INFO", "[+] Node.js Workspace erfolgreich initialisiert.")
        else:
            EventBus.publish("LOG_INFO", "[!] NPM Install fehlgeschlagen!")

        return success

    @classmethod
    def compile_script(cls, raw_js_code: str, archive_dir: str) -> str:
        if not raw_js_code:
            EventBus.publish("LOG_INFO", "[!] Leerer Code übergeben. Kompilierung abgebrochen.")
            return ""

        frida_proj_dir = os.path.join(tempfile.gettempdir(), "re_frida_workspace")
        log_file = os.path.join(archive_dir, "frida_compile_log.txt")

        if not cls._setup_node_workspace(frida_proj_dir, log_file):
            EventBus.publish("LOG_INFO", "[!] Fehler beim Einrichten des Node.js Workspaces.")
            return ""

        raw_script_path = os.path.join(frida_proj_dir, "index.js")
        with open(raw_script_path, "w", encoding="utf-8") as f:
            f.write(raw_js_code)

        EventBus.publish("LOG_INFO", "[*] Kompiliere Frida-Agent (Live-Log folgt)...")
        compiled_out_path = os.path.join(frida_proj_dir, "agent_compiled.js")

        # Aufruf direkt über die lokale Installation
        node_cmd = "node.exe" if os.name == "nt" else "node"
        bin_script = os.path.join(frida_proj_dir, "node_modules", "frida-compile", "bin", "compile.js")

        if os.path.exists(bin_script):
            compile_cmd = f'"{node_cmd}" "{bin_script}" index.js -o agent_compiled.js'
        else:
            bin_name = "frida-compile.cmd" if os.name == "nt" else "frida-compile"
            abs_bin_path = os.path.join(frida_proj_dir, "node_modules", ".bin", bin_name)
            compile_cmd = f'"{abs_bin_path}" index.js -o agent_compiled.js'

        # Live-Logging für den Compiler-Prozess
        success = CommandRunner.run_live(
            compile_cmd,
            frida_proj_dir,
            lambda l: EventBus.publish("LOG_INFO", f"[COMPILER] {l.strip()}"),
            log_file
        )

        if success and os.path.exists(compiled_out_path):
            EventBus.publish("LOG_INFO", "[+] Kompilierung erfolgreich!")
            return compiled_out_path

        EventBus.publish("LOG_INFO", "[!] Kompilierung fehlgeschlagen!")
        return ""