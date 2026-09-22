import os
import json

from core.infrastructure import json_store


def _fav_key(d):
    """Stabiler Schluessel eines Favoriten: der Name (projektweit eindeutig)."""
    return d.get("name")


class FavoriteService:
    def __init__(self, base_dir: str):
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        self.fav_file = os.path.join(data_dir, "favorite_patches.json")
        self.favs = []
        self._baseline = {}
        self._mtime = None
        self.load()

    # ---------- Persistenz ----------
    def _read_disk(self) -> list:
        if os.path.exists(self.fav_file):
            try:
                with open(self.fav_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def load(self):
        """Von Platte laden + Baseline/mtime setzen. Gibt self.favs zurueck."""
        self.favs = self._read_disk()
        self._baseline = json_store.snapshot_fingerprints(self.favs, _fav_key)
        self._mtime = json_store.file_mtime(self.fav_file)
        return self.favs

    # Rueckwaerts-kompatibel (alte Aufrufer erwarten die Liste als Rueckgabe)
    def load_favs(self):
        return self.load()

    def reload_if_changed(self) -> bool:
        """Nur neu laden, wenn ein anderer Prozess die Datei geaendert hat."""
        if json_store.file_mtime(self.fav_file) != self._mtime:
            self.load()
            return True
        return False

    def save_favs(self):
        """Konfliktsicher speichern: frische Platten-Version lesen, eigene Deltas
        (ggue. Baseline, key=name) mergen, atomar zurueckschreiben. Verhindert, dass
        GUI und MCP sich gegenseitig ueberschreiben."""
        disk = self._read_disk()
        merged, conflicts = json_store.three_way_merge(
            disk, self._baseline, self.favs, _fav_key)
        json_store.atomic_write_text(
            self.fav_file, json.dumps(merged, indent=4))
        self.favs = merged
        self._baseline = json_store.snapshot_fingerprints(merged, _fav_key)
        self._mtime = json_store.file_mtime(self.fav_file)
        if conflicts:
            print(f"[favorite_service] WARN gleichzeitige Aenderung an Favorit(en) "
                  f"{conflicts} — eigene Version gewann, fremde evtl. ueberschrieben.")

    # ---------- CRUD (Signaturen unveraendert) ----------
    def add_favorite(self, fav_dict):
        self.favs.append(fav_dict)
        self.save_favs()

    def delete_favorite(self, idx):
        if 0 <= idx < len(self.favs):
            del self.favs[idx]
            self.save_favs()


def get_shared_favorites(app) -> "FavoriteService":
    """Liefert die EINE app-weite FavoriteService-Instanz.

    Analog get_shared_manager (Libs): verhindert, dass mehrere unabhaengig geladene
    FavoriteService-Instanzen (GUI-Dialoge, Controller, MCP) einander ueberschreiben."""
    fs = getattr(app, "favorite_svc", None)
    if fs is None:
        base_dir = app.cfg.config.get("BASE_DIR", os.getcwd())
        fs = FavoriteService(base_dir)
        app.favorite_svc = fs
    return fs
