# -*- coding: utf-8 -*-
"""native_favorites_service — persistente Run-Favoriten ("Arbeitsansichten") je Lib/Plugin.

Speichert benannte Run-Konfigurationen (Entry/Args/Mem/SP/maxins/Stubs) atomar als JSON.
Teil des Native-Studio-Projekt-States (siehe konzept-re-studio-projekt.md). IP-neutral.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional


class NativeFavoritesService:
    """Benannte Run-Konfigurationen in einer JSON-Datei (atomar, mit version/description)."""

    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("items"), list):
                return d
        except Exception:
            pass
        return {"schema": 1, "items": []}

    def _save(self, d: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def list(self) -> List[str]:
        return [it.get("name", "") for it in self._load().get("items", [])]

    def get(self, name: str) -> Optional[dict]:
        for it in self._load().get("items", []):
            if it.get("name") == name:
                return it.get("config")
        return None

    def add(self, name: str, config: dict, description: str = "") -> str:
        d = self._load()
        items = d.setdefault("items", [])
        for it in items:
            if it.get("name") == name:
                it["config"] = config
                it["description"] = description or it.get("description", "")
                it["version"] = int(it.get("version", 1)) + 1
                self._save(d)
                return "aktualisiert"
        items.append({"name": name, "config": config, "description": description, "version": 1})
        self._save(d)
        return "gespeichert"

    def delete(self, name: str) -> None:
        d = self._load()
        d["items"] = [it for it in d.get("items", []) if it.get("name") != name]
        self._save(d)
