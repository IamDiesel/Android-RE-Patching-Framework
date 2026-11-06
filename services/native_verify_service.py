# -*- coding: utf-8 -*-
"""native_verify_service — Bit-Exakt-Regression fuer native Emulation.

Speichert pro Lib benannte „Golden"-Zustaende (ret/Register/Out) einer Funktions-
Emulation und vergleicht spaetere Laeufe dagegen — generisches Regress-Werkzeug fuer
Framework-Aenderungen, Patches und Lifts (verallgemeinert aus _sign_regress &Co.).

Sidecar-JSON pro Lib: {schema, cases: {name: {golden, meta}}}. IP-neutral (nur Zahlen/
Register des Nutzer-Laufs). Teil des Native-Studio-Projekt-States. SOA: der Aufrufer
emuliert (native_emu_service), dieser Service speichert/vergleicht nur.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional

# Vergleichs-relevante Felder aus einem run()-Ergebnis.
_CMP_FIELDS = ("ret", "x", "sp", "lr", "flags", "out")


class NativeVerifyService:
    def __init__(self, path: str):
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("cases"), dict):
                return d
        except Exception:
            pass
        return {"schema": 1, "cases": {}}

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

    def reduce(self, res: dict) -> dict:
        """Reduziert ein run()-Ergebnis auf die vergleichbaren Felder."""
        return {k: res[k] for k in _CMP_FIELDS if k in res}

    def list(self) -> List[str]:
        return sorted(self._load().get("cases", {}).keys())

    def get(self, name: str) -> Optional[dict]:
        return self._load().get("cases", {}).get(name)

    def set(self, name: str, golden: dict, meta: Optional[dict] = None) -> None:
        d = self._load()
        d.setdefault("cases", {})[name] = {"golden": golden, "meta": meta or {}}
        self._save(d)

    def delete(self, name: str) -> None:
        d = self._load()
        if name in d.get("cases", {}):
            del d["cases"][name]
            self._save(d)

    def compare(self, entry, res: dict) -> dict:
        """Vergleicht ein run()-Ergebnis gegen einen Golden-Eintrag (oder das Golden-Dict).
        Rueckgabe: {match: bool, diffs: [{field, golden, got}]}."""
        golden = entry.get("golden", entry) if isinstance(entry, dict) else entry
        cur = self.reduce(res)
        diffs = []
        for k, gv in (golden or {}).items():
            cv = cur.get(k)
            if cv != gv:
                diffs.append({"field": k, "golden": gv, "got": cv})
        return {"match": not diffs, "diffs": diffs}
