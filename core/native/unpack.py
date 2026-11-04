"""unpack — generische Naht für Entpacker/Entschlüsseler-Plugins (M3, IP-neutral).

Das Framework definiert NUR das Plugin-Interface, eine Registry mit Ordner-Scan
und die Pfad-Policy des Analyse-Arbeitsbereichs. Es bringt **kein** konkretes
Plugin mit — zielspezifische Entschlüsseler (z. B. LLU/DexGuard) liegen privat
und git-ignoriert unter `native_plugins/` und werden per Ordner-Scan geladen.

Zonen-Policy (native-studio-impl.md A1/A2): entschlüsselte Artefakte landen im
Analyse-Arbeitsbereich `data/native/<plugin>/…`, nie in `source/` (Original)
oder `destination/` (Build). Stabile Artefakte (entschlüsselte `.so`, Snapshots)
werden plugin-weit per Input-SHA gecacht; Lauf-Ergebnisse liegen session-getrennt.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import struct
import zipfile
from abc import ABC, abstractmethod
from typing import List, Optional


# ---------------------------------------------------------------------------
class UnpackPlugin(ABC):
    """Vertrag für einen zielspezifischen Entpacker/Entschlüsseler (privat).

    Implementierungen leben git-ignoriert unter `native_plugins/` und registrieren
    sich über ein modulweites `register(registry)` (siehe UnpackRegistry.discover).
    """
    name: str = "unpack"
    description: str = ""
    version: str = "1.0"

    def can_handle(self, path: str) -> bool:
        """Grobe Eignung (Dateiname/Signatur). Default: alles (Nutzer wählt)."""
        return True

    @abstractmethod
    def unpack(self, packed_path: str, out_path: Optional[str] = None) -> dict:
        """Entpacken/Entschlüsseln. -> dict mit mindestens {'ok': bool}.
        Bei Erfolg zusätzlich {'out_path': <entschlüsselte .so>, 'info': <str>}."""
        ...


# ---------------------------------------------------------------------------
class UnpackRegistry:
    def __init__(self):
        self._plugins: List[UnpackPlugin] = []

    def register(self, plugin: UnpackPlugin) -> None:
        self._plugins.append(plugin)

    def plugins(self) -> List[UnpackPlugin]:
        return list(self._plugins)

    def names(self) -> List[str]:
        return [p.name for p in self._plugins]

    def find_for(self, path: str) -> List[UnpackPlugin]:
        return [p for p in self._plugins if p.can_handle(path)]

    def discover(self, plugins_dir: str) -> int:
        """Lädt alle `*_decrypter.py` / `*_plugin.py` aus `plugins_dir` (rekursiv,
        1 Ebene) und ruft deren `register(registry)` auf. Fehlt der Ordner, passiert
        nichts (Framework ist dann plugin-frei). Rückgabe: Anzahl registrierter Plugins."""
        before = len(self._plugins)
        if not plugins_dir or not os.path.isdir(plugins_dir):
            return 0
        cands: List[str] = []
        for entry in sorted(os.listdir(plugins_dir)):
            full = os.path.join(plugins_dir, entry)
            if os.path.isfile(full) and (entry.endswith("_decrypter.py") or entry.endswith("_plugin.py")):
                cands.append(full)
            elif os.path.isdir(full):  # eine Unterordner-Ebene (z. B. llu/)
                for sub in sorted(os.listdir(full)):
                    if sub.endswith("_decrypter.py") or sub.endswith("_plugin.py"):
                        cands.append(os.path.join(full, sub))
        for path in cands:
            try:
                modname = "native_plugin_" + hashlib.sha1(path.encode()).hexdigest()[:8]
                spec = importlib.util.spec_from_file_location(modname, path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(self)
            except Exception:
                # Ein defektes privates Plugin darf die Registry nicht sprengen.
                continue
        return len(self._plugins) - before


# ---------------------------------------------------------------------------
# Analyse-Arbeitsbereich — Pfad-Policy (A1/A2)
# ---------------------------------------------------------------------------
def plugin_root(base_dir: str, plugin_name: str) -> str:
    d = os.path.join(base_dir, "data", "native", plugin_name)
    os.makedirs(d, exist_ok=True)
    return d


def lib_cache_dir(base_dir: str, plugin_name: str, input_sha8: str) -> str:
    """Stabiler Cache der entschlüsselten `.so`, per Input-SHA (cross-session)."""
    d = os.path.join(plugin_root(base_dir, plugin_name), "lib", input_sha8)
    os.makedirs(d, exist_ok=True)
    return d


def snapshots_dir(base_dir: str, plugin_name: str) -> str:
    d = os.path.join(plugin_root(base_dir, plugin_name), "snapshots")
    os.makedirs(d, exist_ok=True)
    return d


def session_dir(base_dir: str, plugin_name: str, session_id: str) -> str:
    """Session-getrennte Lauf-Ergebnisse (Coverage, Lifts, Traces, Stubs)."""
    sid = session_id or "default"
    d = os.path.join(plugin_root(base_dir, plugin_name), "sessions", sid)
    os.makedirs(d, exist_ok=True)
    return d


def sha8(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


# ---------------------------------------------------------------------------
# APK/ZIP-Erkennung gepackter nativer Libs (IP-neutral)
# ---------------------------------------------------------------------------
def is_zip(path: str) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except Exception:
        return False


def looks_packed(head: bytes) -> bool:
    """Heuristik: ist dieser .so-Anfang eine GEPACKTE/verschluesselte Lib?
    - kein ELF-Magic -> gepackt (ganze Datei verschluesselt).
    - ELF-Magic, aber Attrappe: erstes PT_LOAD hat filesz==0 (269-B-Fake-Header).
    Echte Libs haben ein PT_LOAD mit filesz>0 -> nicht gepackt."""
    if len(head) < 64:
        return False
    if head[:4] != b"\x7fELF":
        return True
    try:
        e_phoff = struct.unpack_from("<Q", head, 32)[0]
        e_phentsize, e_phnum = struct.unpack_from("<HH", head, 54)
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            if off + 56 > len(head):
                break
            if struct.unpack_from("<I", head, off)[0] == 1:   # PT_LOAD
                return struct.unpack_from("<Q", head, off + 32)[0] == 0   # filesz==0 => Attrappe
    except Exception:
        return False
    return False


def list_packed_libs(apk_path: str, abi: str = "arm64-v8a") -> list:
    """Gepackte native Libs in lib/<abi>/ eines APK (per looks_packed). -> [entry, ...]."""
    out = []
    try:
        with zipfile.ZipFile(apk_path) as z:
            for n in z.namelist():
                if n.startswith("lib/" + abi + "/") and n.endswith(".so"):
                    try:
                        with z.open(n) as f:
                            head = f.read(8192)
                    except Exception:
                        continue
                    if looks_packed(head):
                        out.append(n)
    except Exception:
        pass
    return out


def read_lib_from_apk(apk_path: str, entry: str) -> bytes:
    with zipfile.ZipFile(apk_path) as z:
        return z.read(entry)


def derive_app_root_for_apk(apk_path: str):
    """Findet die entpackte App (…/base_unpacked_*/ mit smali) beim APK — Quelle der Loader-Smali."""
    d = os.path.dirname(os.path.abspath(apk_path))
    for _ in range(3):
        try:
            here = os.listdir(d)
        except OSError:
            break
        if any(s == "smali" or s.startswith("smali_classes") for s in here):
            return d
        for sub in sorted(here):
            full = os.path.join(d, sub)
            if not os.path.isdir(full):
                continue
            try:
                inner = os.listdir(full)
            except OSError:
                continue
            if any(s == "smali" or s.startswith("smali_classes") for s in inner):
                return full
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None
