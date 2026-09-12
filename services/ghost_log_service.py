import time
import datetime
import threading
from core.infrastructure.command_runner import CommandRunner
from core.application.event_bus import EventBus

class GhostLogService:
    def __init__(self):
        self.process = None
        self.is_running = False

    def start_capture(self, adb_path: str, package: str, base_dir: str):
        if self.is_running: return
        self.is_running = True

        def task():
            log_file = f"/data/data/{package}/ghost.log"

            # 1. Modification Time der Datei ermitteln (Sekunden seit Epoch)
            stat_cmd = f'"{adb_path}" shell "run-as {package} stat -c %Y {log_file}"'
            stat_res = CommandRunner.run_blocking(stat_cmd, cwd=base_dir)
            try:
                mtime = int(stat_res.stdout.strip())
                file_time = datetime.datetime.fromtimestamp(mtime).strftime('%H:%M:%S (File)')
            except:
                file_time = "OLD"

            # 2. Kompletten Inhalt lesen und danach streamen (-c +1 startet ab Byte 1)
            tail_cmd = f'"{adb_path}" shell "run-as {package} tail -c +1 -f {log_file}"'
            self.process = CommandRunner.run_background(tail_cmd, cwd=base_dir)

            start_time = time.time()

            if self.process.stdout:
                for line in self.process.stdout:
                    if not self.is_running: break

                    # FIX: striktes strip() entfernt \r, \n und Leerzeilen zuverlässig
                    clean_line = line.strip()

                    # Leere Zeilen aus dem Stream ignorieren wir konsequent
                    if clean_line:
                        current_time = time.time()

                        # Alles was in der ersten Sekunde gepusht wird, ist Altbestand
                        if current_time - start_time < 1.0:
                            EventBus.publish("GHOST_LOG_LINE", f"[{file_time}] [GHOST] {clean_line}")
                        else:
                            now = datetime.datetime.now().strftime('%H:%M:%S')
                            EventBus.publish("GHOST_LOG_LINE", f"[{now}] [GHOST] {clean_line}")

        threading.Thread(target=task, daemon=True).start()

    def stop_capture(self):
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
            except: pass
            self.process = None