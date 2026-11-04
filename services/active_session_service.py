"""
active_session_service — konfliktsichere Persistenz der AKTIVEN Patch-Liste.

Zweck (prozessuebergreifende Uebergabe): Der MCP-stdio-Server und der GUI-Prozess
halten je eine eigene RAM-`SessionState`. Damit MCP-seitig angewendete Patches im
GUI-Fenster „aktive Patches" sichtbar werden (und Neustarts ueberleben), wird die
aktive Liste zusaetzlich in `data/active_session.json` persistiert — nach exakt
demselben Muster wie `favorite_service.py` (atomarer Write + Drei-Wege-Merge, damit
GUI und MCP sich nicht gegenseitig ueberschreiben; mtime-Reload-Guard).

Format der Datei:  {"patches": [ <patch>, ... ]}
Ein <patch> ist ein Dict mit "type" (smali|hex|lib_replace|new_file) + Feldern,
wie es SessionState.get_all_patches()/load_patches() erwartet.
"""
import os
import json

from core.infrastructure import json_store


def _patch_key(p) -> str:
    """Stabiler, prozessweit eindeutiger Schluessel eines aktiven Patches
    (fuer den Drei-Wege-Merge). Bewusst tolerant gegen fehlende Felder."""
    t = p.get("type", "smali")
    if t == "lib_replace":
        return f"lib|{p.get('target','')}|{p.get('source','')}"
    if t == "hex":
        return f"hex|{p.get('file','')}|{p.get('ram','')}|{p.get('patch','')}"
    # smali / new_file: Datei + Scope + Anfang des orig-Blocks reichen zur Unterscheidung
    return f"{t}|{p.get('file','')}|{p.get('scope','method')}|{(p.get('orig','') or '')[:64]}"


class ActiveSessionService:
    def __init__(self, base_dir: str):
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.file = os.path.join(data_dir, "active_session.json")
        self.items = []
        self._baseline = {}
        self._mtime = None
        self.load()

    # ---------- Persistenz ----------
    def _read_disk(self) -> list:
        if os.path.exists(self.file):
            try:
                with open(self.file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("patches", [])
                return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def load(self) -> list:
        """Von Platte laden + Baseline/mtime setzen. Gibt die Patch-Liste zurueck."""
        self.items = self._read_disk()
        self._baseline = json_store.snapshot_fingerprints(self.items, _patch_key)
        self._mtime = json_store.file_mtime(self.file)
        return self.items

    def reload_if_changed(self) -> bool:
        """Nur neu laden, wenn ein anderer Prozess die Datei geaendert hat."""
        if json_store.file_mtime(self.file) != self._mtime:
            self.load()
            return True
        return False

    def patches(self) -> list:
        """Aktuelle aktive Patch-Liste (Kopie)."""
        return list(self.items)

    def save(self, patches) -> list:
        """Konfliktsicher speichern: frische Platten-Version lesen, eigene Deltas
        (ggue. Baseline) mergen, atomar zurueckschreiben. Verhindert, dass GUI und
        MCP sich gegenseitig ueberschreiben. Gibt die gemergte Liste zurueck."""
        self.items = list(patches or [])
        disk = self._read_disk()
        merged, conflicts = json_store.three_way_merge(
            disk, self._baseline, self.items, _patch_key)
        json_store.atomic_write_text(
            self.file, json.dumps({"patches": merged}, indent=2, ensure_ascii=False))
        self.items = merged
        self._baseline = json_store.snapshot_fingerprints(merged, _patch_key)
        self._mtime = json_store.file_mtime(self.file)
        if conflicts:
            print(f"[active_session] WARN gleichzeitige Aenderung an Patch(es) "
                  f"{conflicts} — eigene Version gewann, fremde evtl. ueberschrieben.")
        return merged

    def clear(self) -> None:
        """T2: Leert die aktive Session HART (umgeht den Drei-Wege-Merge, der sonst
        Disk-Eintraege behaelt). Setzt Datei, items, baseline und mtime konsistent
        zurueck -- der einzige Weg, die Liste wirklich zu leeren."""
        json_store.atomic_write_text(
            self.file, json.dumps({"patches": []}, indent=2, ensure_ascii=False))
        self.items = []
        self._baseline = json_store.snapshot_fingerprints(self.items, _patch_key)
        self._mtime = json_store.file_mtime(self.file)


def get_shared_active_session(app) -> "ActiveSessionService":
    """Liefert die EINE app-weite ActiveSessionService-Instanz (wie get_shared_favorites)."""
    svc = getattr(app, "active_session_svc", None)
    if svc is None:
        base_dir = app.cfg.config.get("BASE_DIR", os.getcwd())
        svc = ActiveSessionService(base_dir)
        app.active_session_svc = svc
    return svc
