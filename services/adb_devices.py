"""
adb_devices — Geraete-Aufloesung fuer Mehrgeraet-Betrieb.

Zentral ueber die Umgebungsvariable ANDROID_SERIAL: adb respektiert sie fuer
JEDEN Aufruf, daher wirkt die Auswahl in allen Services/Tools ohne Aenderung
der einzelnen Aufrufstellen.

Default = USB-Geraet. Ein WLAN-/mDNS-Eintrag (\":\" bzw. \"._tcp\" im Serial)
stoert damit nicht mehr. Explizite Auswahl via config ADB_DEVICE_SERIAL.
"""
import os
from core.infrastructure.command_runner import CommandRunner


def classify(serial: str) -> str:
    s = serial or ""
    if (":" in s) or ("._tcp" in s) or s.startswith("adb-"):
        return "network"
    return "usb"


def list_devices(adb_path: str):
    """[{serial, state, kind}] aus 'adb devices'. Ignoriert ANDROID_SERIAL."""
    r = CommandRunner.run_blocking(f'"{adb_path}" devices', ".", timeout=8)
    out = []
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if not ln or ln.lower().startswith("list of devices"):
            continue
        parts = ln.split()
        if len(parts) >= 2:
            serial, state = parts[0], parts[1]
            out.append({"serial": serial, "state": state, "kind": classify(serial)})
    return out


def resolve_serial(cfg, adb_path: str):
    """Gewaehltes Geraet: config ADB_DEVICE_SERIAL (falls online) -> sonst USB -> sonst einziges."""
    devs = [d for d in list_devices(adb_path) if d["state"] == "device"]
    if not devs:
        return None
    want = (cfg.config.get("ADB_DEVICE_SERIAL", "") or "").strip()
    if want:
        for d in devs:
            if d["serial"] == want:
                return want
    for d in devs:
        if d["kind"] == "usb":
            return d["serial"]
    return devs[0]["serial"]


def apply_android_serial(cfg, adb_path: str, probe: bool = True):
    """Setzt os.environ['ANDROID_SERIAL'] entsprechend der Auswahl.

    probe=False: nur die explizite config-Auswahl anwenden (guenstig, kein 'adb devices').
    probe=True : Geraete abfragen und ggf. USB-Default setzen.
    Rueckgabe: der gesetzte Serial oder None.
    """
    want = (cfg.config.get("ADB_DEVICE_SERIAL", "") or "").strip()
    if want:
        os.environ["ANDROID_SERIAL"] = want
        return want
    if not probe:
        return os.environ.get("ANDROID_SERIAL")
    sel = resolve_serial(cfg, adb_path)
    if sel:
        os.environ["ANDROID_SERIAL"] = sel
    else:
        os.environ.pop("ANDROID_SERIAL", None)
    return sel
