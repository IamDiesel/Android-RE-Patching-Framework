import os
import uuid
import threading
import frida
from typing import Callable, Optional
from core.application.event_bus import EventBus
from core.domain.frida_models import FridaScript, FridaCollection

from services.frida_compiler_service import FridaCompilerService
from services.frida_server_service import FridaServerService
from services.frida_sync_service import FridaSyncService


class FridaManagerController:
    def __init__(self, app, frida_manager):
        self.app = app
        self.manager = frida_manager

        if not hasattr(self.app, "frida_server"):
            self.app.frida_server = FridaServerService(self.app.cfg.frida_config)
        self.server: FridaServerService = self.app.frida_server

    # --- Konfiguration & Metadaten ---
    def save_config(self, mode: str, host: str, port: str, sync_path: str, pause_on_load: bool):
        cfg = self.app.cfg.frida_config
        cfg.mode = mode
        cfg.host = host
        cfg.port = int(port) if str(port).isdigit() else 27042
        cfg.script_directory_path = sync_path
        cfg.pause_on_load = pause_on_load
        self.app.cfg.save()

        status_msg = "pausiert App (wait)" if pause_on_load else "App läuft weiter (resume)"
        EventBus.publish("LOG_INFO", f"[*] Frida Config gespeichert. Modus: {mode.upper()} | {status_msg}")

    # --- Skript Management ---
    def create_new_script(self) -> FridaScript:
        new_script = FridaScript(
            id=str(uuid.uuid4())[:8],
            name="Neues Skript",
            code="import Java from \"frida-java-bridge\";\n\nconsole.log('Hello Frida!');"
        )
        self.manager.scripts.append(new_script)
        self.manager.save()
        return new_script

    def delete_script(self, script_id: str):
        self.manager.scripts = [s for s in self.manager.scripts if s.id != script_id]
        if self.manager.active_script_id == script_id:
            self.manager.active_script_id = None
        self.manager.save()

    def set_active_script(self, script_id: str):
        self.manager.active_script_id = script_id
        self.manager.save()

    # --- Collections Management ---
    def create_collection(self, name: str, script_ids: list):
        col = FridaCollection(id=str(uuid.uuid4())[:8], name=name, script_ids=script_ids)
        self.manager.collections.append(col)
        self.manager.save()

    def delete_collection(self, collection_id: str):
        self.manager.collections = [c for c in self.manager.collections if c.id != collection_id]
        self.manager.save()

    def rename_collection(self, collection_id: str, new_name: str):
        col = next((c for c in self.manager.collections if c.id == collection_id), None)
        if col:
            col.name = new_name
            self.manager.save()

    def push_collection_to_device(self, collection_id: str, on_start: Callable, on_done: Callable[[list], None]):
        col = next((c for c in self.manager.collections if c.id == collection_id), None)
        if not col: return

        def task():
            self.app.after(0, on_start)
            try:
                adb = self.app.cfg.paths.get("ADB", "adb")
                pkg = self.app.cfg.config.get("APP_PACKAGE", "")

                EventBus.publish("LOG_INFO", f"[*] Pushe Sammlung '{col.name}' auf das Gerät...")

                for script_id in col.script_ids:
                    script = self.manager.get_script_by_id(script_id)
                    if not script: continue

                    compiled_path = FridaCompilerService.compile_script(script.code, self.app.cfg.paths["ARCHIVE_DIR"])
                    if compiled_path:
                        filename = script.name.replace(" ", "_").lower()
                        if not filename.endswith(".js"): filename += ".js"

                        target_path = os.path.join(os.path.dirname(compiled_path), filename)
                        os.replace(compiled_path, target_path)
                        FridaSyncService.push_script(adb, pkg, target_path, self.app.cfg.frida_config)

                EventBus.publish("LOG_INFO", f"[+] Sammlung '{col.name}' erfolgreich synchronisiert!")
            finally:
                self.refresh_device_scripts(on_done)

        threading.Thread(target=task, daemon=True).start()

    # --- Execution Modus 1: Listen (USB Injection) ---
    def fire_usb_listen(self, script_code: str, on_start: Callable, on_done: Callable):
        if not script_code.strip(): return

        def task():
            self.app.after(0, on_start)
            try:
                EventBus.publish("LOG_INFO", "[*] Verbinde mit Frida Gadget über USB (Listen-Modus)...")
                device = frida.get_usb_device(timeout=5)
                session = device.attach("Gadget")

                compiled_path = FridaCompilerService.compile_script(script_code, self.app.cfg.paths["ARCHIVE_DIR"])
                if not compiled_path:
                    return

                with open(compiled_path, "r", encoding="utf-8") as f:
                    compiled_source = f.read()

                script = session.create_script(compiled_source)
                script.on('message', lambda m, d: EventBus.publish("LOG_INFO", f"[Frida] {m['payload']}" if m[
                                                                                                                'type'] == 'send' else f"[Frida ERROR] {m.get('stack', m)}"))
                script.load()

                EventBus.publish("LOG_INFO", "[+] Skript injiziert! Resume Gadget...")
                device.resume("Gadget")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei USB-Injektion: {e}")
            finally:
                self.app.after(0, on_done)

        threading.Thread(target=task, daemon=True).start()

    # --- Execution Modus 2: Connect (Server & Live Push) ---
    def start_server(self):
        adb = self.app.cfg.paths.get("ADB", "adb")
        self.server.config = self.app.cfg.frida_config
        self.server.start_server(adb_path=adb)

    def stop_server(self):
        self.server.stop_server()

    def get_server_status(self) -> dict:
        return {
            "is_running": self.server.is_running,
            "clients": len(self.server.active_sessions)
        }

    def push_live_script(self, script_code: str, on_start: Callable, on_done: Callable):
        if not self.server.is_running:
            EventBus.publish("LOG_INFO", "[!] Server läuft nicht. Bitte zuerst starten.")
            return

        def task():
            self.app.after(0, on_start)
            try:
                compiled_path = FridaCompilerService.compile_script(script_code, self.app.cfg.paths["ARCHIVE_DIR"])
                if compiled_path:
                    with open(compiled_path, "r", encoding="utf-8") as f:
                        compiled_source = f.read()
                    self.server.push_script_live(compiled_source)
            finally:
                self.app.after(0, on_done)

        threading.Thread(target=task, daemon=True).start()

    # --- Execution Modus 4: ScriptDirectory (Device Sync) ---
    def refresh_device_scripts(self, callback: Callable[[list], None]):
        def task():
            adb = self.app.cfg.paths.get("ADB", "adb")
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            scripts = FridaSyncService.list_scripts(adb, pkg, self.app.cfg.frida_config)
            self.app.after(0, lambda: callback(scripts))

        threading.Thread(target=task, daemon=True).start()

    def push_to_device(self, script_code: str, filename: str, on_start: Callable, on_done: Callable[[list], None]):
        if not filename.endswith(".js"): filename += ".js"

        def task():
            self.app.after(0, on_start)
            try:
                adb = self.app.cfg.paths.get("ADB", "adb")
                pkg = self.app.cfg.config.get("APP_PACKAGE", "")

                compiled_path = FridaCompilerService.compile_script(script_code, self.app.cfg.paths["ARCHIVE_DIR"])
                if compiled_path:
                    target_path = os.path.join(os.path.dirname(compiled_path), filename)
                    os.replace(compiled_path, target_path)
                    FridaSyncService.push_script(adb, pkg, target_path, self.app.cfg.frida_config)
            finally:
                self.refresh_device_scripts(on_done)

        threading.Thread(target=task, daemon=True).start()

    def delete_from_device(self, filename: str, callback: Callable[[list], None]):
        def task():
            adb = self.app.cfg.paths.get("ADB", "adb")
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            FridaSyncService.delete_script(adb, pkg, filename, self.app.cfg.frida_config)
            self.refresh_device_scripts(callback)

        threading.Thread(target=task, daemon=True).start()