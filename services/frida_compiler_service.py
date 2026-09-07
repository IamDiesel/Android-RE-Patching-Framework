import os
import json
import shutil
import tempfile
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus


class FridaCompilerService:
    """
    Zustandsloser Service, der rohen JS/TS Frida-Code mittels Node.js und
    frida-compile in ein ausführbares Agent-Skript übersetzt.
    """

    @classmethod
    def _setup_node_workspace(cls, workspace_dir: str, log_file: str) -> bool:
        """Initialisiert den Node.js Workspace, falls er nicht existiert."""
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
        EventBus.publish("LOG_INFO", "[*] Führe 'npm install' aus (das dauert kurz)...")

        success = CommandRunner.run_live(
            f"{npm_cmd} install",
            workspace_dir,
            lambda l: EventBus.publish("LOG_INFO", f"[NPM] {l}"),
            log_file
        )

        if success:
            with open(os.path.join(workspace_dir, ".latest_success"), "w") as f:
                f.write("ok")
        return success

    @classmethod
    def compile_script(cls, raw_js_code: str, archive_dir: str) -> str:
        """
        Kompiliert das übergebene Skript synchron.
        Gibt den absoluten Dateipfad zum kompilierten Skript zurück.
        Bei Fehler wird ein leerer String zurückgegeben.
        """
        if not raw_js_code:
            EventBus.publish("LOG_INFO", "[!] Leerer Code übergeben. Kompilierung abgebrochen.")
            return ""

        frida_proj_dir = os.path.join(tempfile.gettempdir(), "re_frida_workspace")
        log_file = os.path.join(archive_dir, "frida_compile_log.txt")

        # 1. Workspace sicherstellen
        if not cls._setup_node_workspace(frida_proj_dir, log_file):
            EventBus.publish("LOG_INFO", "[!] Fehler beim Einrichten des Node.js Workspaces.")
            return ""

        # 2. Raw Code schreiben
        raw_script_path = os.path.join(frida_proj_dir, "index.js")
        with open(raw_script_path, "w", encoding="utf-8") as f:
            f.write(raw_js_code)

        # 3. Kompilieren
        EventBus.publish("LOG_INFO", "[*] Kompiliere Frida-Agent...")
        compiled_out_path = os.path.join(frida_proj_dir, "agent_compiled.js")
        npx_cmd = "npx.cmd" if os.name == "nt" else "npx"
        compile_cmd = f"{npx_cmd} --yes frida-compile index.js -o agent_compiled.js -c"

        success = CommandRunner.run_live(
            compile_cmd,
            frida_proj_dir,
            lambda l: EventBus.publish("LOG_INFO", f"[frida-compile] {l}"),
            log_file
        )

        if success and os.path.exists(compiled_out_path):
            EventBus.publish("LOG_INFO", "[+] Kompilierung erfolgreich!")
            return compiled_out_path

        EventBus.publish("LOG_INFO", "[!] Kompilierung fehlgeschlagen!")
        return ""