# -*- coding: utf-8 -*-
"""native_annotations_service — eigene Namen/Notizen je Lib (sub_… -> pss_sign).

Sidecar-JSON pro Plugin: {schema, libs: {sha8: {addr_hex: {name, note}}}}.
Teil des Native-Studio-Projekt-States. IP-neutral (nur Nutzer-Beschriftungen).
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional


class NativeAnnotationsService:
    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("libs"), dict):
                return d
        except Exception:
            pass
        return {"schema": 1, "libs": {}}

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

    def all(self, sha: str) -> dict:
        return self._load().get("libs", {}).get(sha, {})

    def get(self, sha: str, addr: str) -> Optional[dict]:
        return self.all(sha).get(addr)

    def set(self, sha: str, addr: str, name: str, note: str = "") -> None:
        d = self._load()
        libs = d.setdefault("libs", {})
        m = libs.setdefault(sha, {})
        m[addr] = {"name": name, "note": note}
        self._save(d)

    def delete(self, sha: str, addr: str) -> None:
        d = self._load()
        libs = d.get("libs", {})
        if sha in libs and addr in libs[sha]:
            del libs[sha][addr]
            self._save(d)
