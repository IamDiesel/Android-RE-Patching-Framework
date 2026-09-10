import os
import threading
import time
import subprocess
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus


class LogcatService:
    def __init__(self):
        self.log_process = None
        self.log_file_handle = None
        self.is_running = False

    def start_capture(self, adb_path: str, logcat_cmd: str, archive_dir: str):
        if self.is_running:
            self.stop_capture()

        os.makedirs(archive_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_file_path = os.path.join(archive_dir, f"logcat_trace_{timestamp}.txt")
        self.log_file_handle = open(log_file_path, "w", encoding="utf-8")

        full_logcat_cmd = f'"{adb_path}" shell "{logcat_cmd}"'

        try:
            self.log_process = CommandRunner.run_background(full_logcat_cmd, cwd=".")
            self.is_running = True
            threading.Thread(target=self._read_logs, daemon=True).start()
            return log_file_path
        except Exception as e:
            if self.log_file_handle:
                self.log_file_handle.close()
            raise e

    def _read_logs(self):
        try:
            for line in iter(self.log_process.stdout.readline, ''):
                if not line:
                    continue

                # 1. Datei-Logging: Die rohe Zeile inkl. originaler Umbrüche speichern
                if self.log_file_handle and not self.log_file_handle.closed:
                    self.log_file_handle.write(line)
                    self.log_file_handle.flush()

                # 2. UI-Logging: Ausschließlich den Umbruch am Ende abschneiden
                clean_line = line.rstrip('\r\n')

                # Wenn die Zeile danach komplett leer ist, überspringen wir sie für die UI
                if not clean_line:
                    continue

                # Standard Logcat Line Event (für das Logging Tab)
                EventBus.publish("LOGCAT_LINE", clean_line)

                # NEU: Extrahieren nativer Frida-Logs für die globale Konsole
                # Dies stellt sicher, dass Autark-Modi (Script / ScriptDirectory)
                # ihre Outputs global im Workspace melden.
                lower_line = clean_line.lower()
                if "frida" in lower_line and not "logcat" in lower_line:
                    # Bereinige den typischen Logcat Header (z.B. "09-07 12:34:56.789 1234 5678 I Frida  : ...")
                    # FIX: Wir nutzen hier KEIN .strip() mehr auf der Nachricht, damit deine Einrückungen (Tabs/Spaces) bei Java Stacktraces erhalten bleiben!
                    parts = clean_line.split(":", 3)
                    clean_msg = parts[-1] if len(parts) > 1 else clean_line

                    # Prefix [Frida Device] unterscheidet es von [Frida] (RPC Host Bridge)
                    EventBus.publish("LOG_INFO", f"[Frida Device] {clean_msg}")

        except Exception:
            pass

        except Exception:
            pass

    def stop_capture(self):
        self.is_running = False
        if self.log_process:
            if os.name == 'nt':
                subprocess.run(f"taskkill /F /T /PID {self.log_process.pid}", shell=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            else:
                self.log_process.terminate()
            self.log_process = None

        if self.log_file_handle:
            self.log_file_handle.close()
            self.log_file_handle = None