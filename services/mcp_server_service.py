"""
McpServerService — Lebenszyklus des lokalen MCP-Servers (Schritt 1/3).

Startet/stoppt den MCP-Server als EIGENEN Prozess (`python -m mcp_server.server`),
damit die GUI-Seite „MCP" den Server verwalten kann. Prozess-Isolation:
ein Absturz des Servers reisst die GUI nicht mit. Meldet Fortschritt via EventBus.

Transport = lokaler HTTP-Server (127.0.0.1) -> die GUI besitzt Start/Stop.
"""
import os
import sys
import time
import socket
import subprocess

from core.application.event_bus import EventBus


class McpServerService:
    def __init__(self, cfg_manager):
        self.cfg = cfg_manager
        self.proc = None
        self.started_at = None
        self.log_path = os.path.join(self.cfg.config.get("BASE_DIR", "."), "data", "mcp_server.log")
        self._log_fh = None

    # ---------- intern ----------
    def _mcp(self) -> dict:
        return self.cfg.config.get("MCP_SETTINGS", {}) or {}

    def _srv(self) -> dict:
        return self._mcp().get("server", {}) or {}

    @staticmethod
    def _startupinfo():
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            return si
        return None

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _emit(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", f"[MCP] {msg}")

    def _log(self, msg: str) -> None:
        if self._log_fh:
            try:
                self._log_fh.write(msg + "\n")
                self._log_fh.flush()
            except Exception:
                pass

    def _log_tail(self, n: int = 15) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:]).strip() or "(Log leer)"
        except Exception:
            return "(kein Log)"

    # ---------- Steuerung ----------
    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def start(self):
        if self.is_running():
            self._emit("Server laeuft bereits.")
            return True, "Server laeuft bereits."

        srv = self._srv()
        host = srv.get("host", "127.0.0.1")
        port = int(srv.get("port", 8765))
        base_dir = self.cfg.config.get("BASE_DIR", ".")

        # Sofort ins Log schreiben, damit es nach einem Klick nie leer ist.
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            self._log_fh = open(self.log_path, "a", encoding="utf-8", errors="replace")
            self._log_fh.write(f"\n=== START-VERSUCH {time.strftime('%Y-%m-%d %H:%M:%S')} :: {host}:{port} ===\n")
            self._log_fh.flush()
        except Exception:
            self._log_fh = None

        if self._port_open(host, port):
            msg = (f"Port {host}:{port} ist bereits belegt — vermutlich laeuft noch der "
                   f"Platzhalter-Server aus einem frueheren Test. Beende diesen python.exe-Prozess "
                   f"(Task-Manager) ODER waehle hier einen anderen Port (z. B. 8770) und Speichern.")
            self._log(msg)
            self._emit(msg)
            return False, msg

        cmd = [sys.executable, "-m", "mcp_server.server", "--host", host, "--port", str(port)]
        self._log(f"Starte: {' '.join(cmd)}  (cwd={base_dir})")
        self._emit(f"Starte MCP-Server auf {host}:{port} ...")
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=base_dir,
                stdout=self._log_fh or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                startupinfo=self._startupinfo(), close_fds=True)
        except Exception as e:
            msg = f"Start fehlgeschlagen (Popen): {e}"
            self._log(msg)
            self._emit(msg)
            return False, msg

        # auf Port hoch ODER Sofort-Absturz warten
        for _ in range(30):
            if self._port_open(host, port):
                self.started_at = time.time()
                msg = f"Server laeuft auf http://{host}:{port} (PID {self.proc.pid})."
                self._emit(msg)
                return True, msg
            if self.proc.poll() is not None:
                rc = self.proc.returncode
                tail = self._log_tail(15)
                self.proc = None
                msg = (f"Server-Prozess sofort beendet (rc={rc}). Haeufigste Ursache: Paket 'mcp' "
                       f"fehlt oder ist defekt. Log-Auszug:\n{tail}")
                self._emit("Server sofort beendet — Details in der Meldung / im Server-Log.")
                return False, msg
            time.sleep(0.2)

        # Prozess laeuft, aber Port kam (noch) nicht hoch
        self.started_at = time.time()
        msg = (f"Prozess gestartet (PID {self.proc.pid}), aber Port {port} noch nicht erreichbar. "
               f"Log-Auszug:\n{self._log_tail(15)}")
        self._emit(msg)
        return True, msg

    def stop(self):
        if not self.is_running():
            self.proc = None
            return True, "Server laeuft nicht."
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        except Exception as e:
            self._emit(f"Stop-Fehler: {e}")
            return False, f"Stop-Fehler: {e}"
        finally:
            self.proc = None
            self.started_at = None
            if self._log_fh:
                try:
                    self._log_fh.close()
                except Exception:
                    pass
                self._log_fh = None
        self._emit("Server gestoppt.")
        return True, "Server gestoppt."

    def restart(self):
        self.stop()
        time.sleep(0.3)
        return self.start()

    def status(self) -> dict:
        srv = self._srv()
        host = srv.get("host", "127.0.0.1")
        port = int(srv.get("port", 8765))
        running = self.is_running()
        uptime = int(time.time() - self.started_at) if (running and self.started_at) else 0
        return {
            "running": running,
            "pid": self.proc.pid if running else None,
            "host": host,
            "port": port,
            "port_open": self._port_open(host, port),
            "uptime_s": uptime,
            "log_path": self.log_path,
        }
