import threading
import frida
from typing import Optional
from core.application.event_bus import EventBus
from core.infrastructure.command_runner import CommandRunner
from core.domain.frida_models import FridaConfig


class FridaServerService:
    """
    Kapselt den Frida PortalService für den 'Connect'-Modus (Reverse Connection).
    Beinhaltet ADB-Tunneling und RPC-Session Management im Hintergrund-Thread.
    """

    def __init__(self, frida_config: FridaConfig):
        self.config = frida_config
        self._portal: Optional[frida.PortalService] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.is_running = False

        # Referenzen auf aktive Client-Sitzungen
        self.active_sessions = []

    def start_server(self, adb_path: str = "adb"):
        if self.is_running:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_server, args=(adb_path,), daemon=True)
        self._thread.start()

    def stop_server(self):
        self._stop_event.set()
        if self.is_running:
            EventBus.publish("LOG_INFO", "[*] Fahre Frida Server herunter...")

    def _run_server(self, adb_path: str):
        self.is_running = True
        host = self.config.host
        port = self.config.port

        EventBus.publish("LOG_INFO", f"[*] Starte Frida PortalService auf {host}:{port} ...")

        # 1. Tunneling: Gerät verbindet sich auf TCP Port (z.B. 27042) und ADB tunnelt das an unseren Host.
        CommandRunner.run_blocking(f'"{adb_path}" reverse tcp:{port} tcp:{port}', ".")

        try:
            # 2. Portal hochfahren
            endpoint = frida.EndpointParameters(address=host, port=port)
            self._portal = frida.PortalService(cluster=endpoint)
            self._portal.start()

            # Events mappen, um Verbindungsabbrüche in der GUI zu protokollieren
            self._portal.device.on("child-added", self._on_device_connected)
            self._portal.device.on("child-removed", self._on_device_disconnected)

            EventBus.publish("LOG_INFO", f"[+] Frida Server horcht. Warte auf App-Verbindung (Connect-Modus).")

            # Blockiere den Thread, bis das stop_event gesetzt wird
            self._stop_event.wait()

        except Exception as e:
            EventBus.publish("LOG_INFO", f"[!] Schwerer Fehler im Frida Server: {e}")
        finally:
            if self._portal:
                self._portal.stop()

            # Tunnel wieder sauber abbauen
            CommandRunner.run_blocking(f'"{adb_path}" reverse --remove tcp:{port}', ".")
            self.is_running = False
            self.active_sessions.clear()
            EventBus.publish("LOG_INFO", "[-] Frida Server vollständig gestoppt.")

    def _on_device_connected(self, child):
        EventBus.publish("LOG_INFO", f"[Frida Server] 🟢 Client verbunden: {child}")

    def _on_device_disconnected(self, child):
        EventBus.publish("LOG_INFO", f"[Frida Server] 🔴 Client getrennt: {child}")
        # Aufräumen ungültiger Sessions
        self.active_sessions = [s for s in self.active_sessions if not s.is_detached]

    def push_script_live(self, compiled_source: str):
        """Sendet das fertig kompilierte Skript live an alle verbundenen Portal-Clients."""
        if not self._portal or not self.is_running:
            EventBus.publish("LOG_INFO", "[!] Server läuft nicht. Live-Push abgebrochen.")
            return

        # Holen aller verbundenen Geräte (Clients) über das Portal
        devices = self._portal.device.enumerate_processes()
        if not devices:
            EventBus.publish("LOG_INFO", "[!] Keine aktiven App-Clients verbunden.")
            return

        for proc in devices:
            try:
                session = self._portal.device.attach(proc.pid)
                self.active_sessions.append(session)

                script = session.create_script(compiled_source)
                script.on('message', self._on_rpc_message)
                script.load()

                EventBus.publish("LOG_INFO", f"[+] Skript erfolgreich an PID {proc.pid} gepusht!")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler beim Live-Push an PID {proc.pid}: {e}")

    def _on_rpc_message(self, message, data):
        """Routet Skript-Ausgaben in die Main Console und Android Console."""
        if message['type'] == 'send':
            EventBus.publish("LOG_INFO", f"[Frida] {message['payload']}")
        elif message['type'] == 'error':
            EventBus.publish("LOG_INFO", f"[Frida ERROR] {message['stack']}")
        else:
            EventBus.publish("LOG_INFO", f"[Frida] {message}")