import os
import socket
from core.infrastructure.command_runner import CommandRunner


class AdbNetworkService:
    """ADB-Netzwerk-Helfer: mitmproxy-CA aufs Geraet pushen + Proxy-Routing.

    Hinweise:
    - Die mitmproxy-CA entsteht erst, wenn mitmproxy/mitmdump auf DIESEM Rechner
      mindestens einmal lief (~/.mitmproxy/mitmproxy-ca-cert.cer).
    - Jeder Rechner hat seine EIGENE CA -> Ziel-Dateiname wird pro Host eindeutig
      gemacht (mitmproxy-<hostname>.cer), damit sich zwei Rechner im Download-Ordner
      nicht gegenseitig ueberschreiben und beide CAs am Handy koexistieren koennen.
    - Ein *stilles* Installieren in den Trust-Store ist auf einem NICHT gerooteten
      Geraet nicht moeglich (Android-Sicherheit). open_cert_install() oeffnet daher
      nur die passenden Einstellungen; der letzte Tap bleibt manuell.
    """

    CERT_SRC = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.cer")
    DL_DIR = "/storage/emulated/0/Download"
    # Menuepfad zur CA-Installation (geraetespezifisch; hier Daniels Pixel / Android 16)
    INSTALL_PATH = ("Einstellungen → Datenschutz und Sicherheit → „Mehr Sicherheit & Datenschutz“ "
                    "→ Sicherheit → „Verschluesselung & Anmeldedaten“ → „Zertifikat installieren“ "
                    "→ „CA-Zertifikat“")

    # ---------- intern ----------
    @staticmethod
    def _device_ready():
        """Schneller Vorabcheck (verhindert 30-s-Haenger, wenn kein Geraet da ist)."""
        r = CommandRunner.run_blocking("adb get-state", ".", timeout=6)
        return r.returncode == 0 and "device" in (r.stdout or "")

    @staticmethod
    def _host_tag() -> str:
        host = socket.gethostname().split(".")[0]
        safe = "".join(c for c in host if c.isalnum() or c in "-_")
        return safe or "pc"

    @classmethod
    def cert_dst_name(cls) -> str:
        return f"mitmproxy-{cls._host_tag()}.cer"

    @classmethod
    def cert_available(cls) -> bool:
        return os.path.exists(cls.CERT_SRC)

    # ---------- Cert push ----------
    @classmethod
    def push_cert(cls):
        """Pusht die CA dieses Rechners nach Download/<eindeutiger Name>.
        Rueckgabe: (ok: bool, msg: str)."""
        if not cls.cert_available():
            return (False,
                    "Keine mitmproxy-CA gefunden:\n"
                    f"{cls.CERT_SRC}\n\n"
                    "Bitte den Proxy auf DIESEM Rechner einmal starten (erzeugt die CA), "
                    "danach erneut 'Push Cert'.")
        if not cls._device_ready():
            return (False, "Kein Geraet per adb erreichbar (get-state).\n"
                           "USB/Debugging pruefen bzw. Geraet autorisieren, dann erneut 'Push Cert'.")
        dst_name = cls.cert_dst_name()
        dst = f"{cls.DL_DIR}/{dst_name}"

        # Push (ueberschreibt eine gleichnamige Datei am Handy bewusst).
        r = CommandRunner.run_blocking(f'adb push "{cls.CERT_SRC}" "{dst}"', ".", timeout=30)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            if "no devices" in err or "device" in err and "not found" in err:
                return (False, "Kein Geraet erreichbar (adb). USB/Debugging pruefen.")
            return (False, f"adb push fehlgeschlagen:\n{err or 'unbekannter Fehler'}")

        # Verifizieren, dass die Datei jetzt da ist (Groesse/Existenz via ls).
        v = CommandRunner.run_blocking(f'adb shell ls -l "{dst}"', ".", timeout=15)
        verified = (v.returncode == 0 and dst_name in (v.stdout or "")
                    and "No such file" not in (v.stdout or ""))
        msg = (f"CA gepusht -> Download/{dst_name}"
               + (" (verifiziert)." if verified else " (Verifikation unklar).")
               + "\n\nAm Handy installieren:\n" + cls.INSTALL_PATH
               + f"\n→ Datei „Download/{dst_name}“ waehlen.")
        return (True, msg)

    @classmethod
    def open_cert_install(cls):
        """Oeffnet die Sicherheits-Einstellungen als Sprungbrett zur CA-Installation.
        Ein direktes/stilles Installieren ist ohne Root nicht moeglich.
        Rueckgabe: (ok: bool, msg: str)."""
        if not cls._device_ready():
            return (False, "Kein Geraet per adb erreichbar (get-state).")
        r = CommandRunner.run_blocking(
            'adb shell am start -a android.settings.SECURITY_SETTINGS', ".", timeout=15)
        if r.returncode != 0:
            return (False, "Konnte die Sicherheits-Einstellungen nicht oeffnen (adb).")
        return (True,
                "Sicherheits-Einstellungen geoeffnet. Weiter:\n" + cls.INSTALL_PATH
                + f"\n→ Datei „Download/{cls.cert_dst_name()}“ waehlen.")

    # ---------- Proxy-Routing (robust, timeout-tolerant, (ok,msg)) ----------
    @classmethod
    def _adb(cls, args: str, timeout: float):
        """adb-Aufruf, der NIE wirft. Rueckgabe (ok, out). run_blocking liefert bei
        Timeout returncode=-1/stderr='[timeout]' (kein Raise)."""
        r = CommandRunner.run_blocking(f"adb {args}", ".", timeout=timeout)
        return (r.returncode == 0), (r.stderr or r.stdout or "").strip()

    @classmethod
    def _kick_adb(cls):
        """Entspricht 'alle adb-Prozesse killen' + Neustart — loest den verklemmten
        adb-Server (Grund fuers GUI-Haengen beim Route-Reset)."""
        if os.name == "nt":
            CommandRunner.run_blocking("taskkill /F /IM adb.exe", ".", timeout=10)
        else:
            CommandRunner.run_blocking("adb kill-server", ".", timeout=10)
        CommandRunner.run_blocking("adb start-server", ".", timeout=15)

    @classmethod
    def route_usb(cls):
        ok, out = cls._adb("reverse tcp:8080 tcp:8080", 12)
        if not ok:
            cls._kick_adb()
            cls._adb("reverse tcp:8080 tcp:8080", 12)
        ok2, out2 = cls._adb('shell settings put global http_proxy 127.0.0.1:8080', 12)
        return (ok2, "USB-Route aktiv (127.0.0.1:8080)." if ok2 else f"USB-Route fehlgeschlagen: {out2}")

    @classmethod
    def route_wlan(cls, ip: str):
        ok, out = cls._adb(f'shell settings put global http_proxy {ip}:8080', 12)
        return (ok, f"WLAN-Route aktiv ({ip}:8080)." if ok else f"WLAN-Route fehlgeschlagen: {out}")

    @classmethod
    def reset_route(cls):
        """Wichtig zuerst: Geraete-Proxy AUS (stellt Internet wieder her). Reverse-Tunnel
        best-effort; bei verklemmtem adb-Server automatischer Kick (kill+start), danach
        ist der Reverse-Tunnel ohnehin weg."""
        ok, out = cls._adb('shell settings put global http_proxy :0', 8)
        if not ok:
            cls._kick_adb()
            ok, out = cls._adb('shell settings put global http_proxy :0', 12)
            note = " (adb-Server neu gestartet)"
        else:
            cls._adb("reverse --remove-all", 6)
            note = ""
        if ok:
            return (True, "Route zurueckgesetzt: Geraete-Proxy AUS" + note + ".")
        return (False, f"Konnte Proxy nicht abschalten ({out}). Manuell: "
                       f"adb shell settings put global http_proxy :0")
