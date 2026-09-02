import os
import subprocess
import threading
from core.application.event_bus import EventBus


class ProxyService:
    def __init__(self, db_path: str, rules_path: str):
        self.db_path = db_path
        self.rules_path = rules_path
        self.proxy_process = None

    def start_proxy(self, addon_path: str) -> bool:
        if self.proxy_process and self.proxy_process.poll() is None:
            return True

        if not os.path.exists(addon_path):
            raise FileNotFoundError(f"Addon fehlt: {addon_path}")

        cmd = f"mitmdump --listen-host 0.0.0.0 -s \"{addon_path}\" --set api_db=\"{self.db_path}\" --set rules_file=\"{self.rules_path}\""

        self.proxy_process = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        threading.Thread(target=self._read_proxy_output, daemon=True).start()
        return True

    def _read_proxy_output(self):
        try:
            for line in iter(self.proxy_process.stdout.readline, ''):
                if line:
                    EventBus.publish("PROXY_LOG", line.strip())
        except:
            pass

    def stop_proxy(self):
        if self.proxy_process:
            if os.name == 'nt':
                subprocess.run(f"taskkill /F /T /PID {self.proxy_process.pid}", shell=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            else:
                self.proxy_process.terminate()
            self.proxy_process = None

    def is_running(self) -> bool:
        return self.proxy_process is not None and self.proxy_process.poll() is None