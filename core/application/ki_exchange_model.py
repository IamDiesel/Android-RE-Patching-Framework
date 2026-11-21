# -*- coding: utf-8 -*-
"""KI-Austausch-Koordinator (MVC, rein/headless-testbar).

Ein zentraler Ort fuer 'An KI' / 'Von KI' / 'KI Abgleich' ueber MEHRERE Hauptreiter.
Jeder Reiter registriert sich als Provider mit optionalen push/pull/status-Callables
(die ihren jeweiligen geteilten Zustand schreiben/laden/vergleichen). Ein Dropdown
waehlt, welche Reiter einbezogen werden (Alle oder eine Teilmenge).

Der Zustand haengt NICHT an der Anzeige: das Modell kennt nur Provider + Auswahl und
fuehrt eine Operation ueber die ausgewaehlten Provider aus. Rueckgaben werden als Liste
aggregiert, Fehler je Provider abgefangen (ein Reiter darf den Rest nicht sprengen).
"""
from __future__ import annotations
from typing import Callable, Optional, List, Dict, Any

OPS = ("push", "pull", "status")
_OP_LABEL = {"push": "⬆ An KI", "pull": "⬇ Von KI", "status": "⇅ KI Abgleich"}


class KiExchangeModel:
    def __init__(self):
        self._providers: List[Dict[str, Any]] = []   # key -> dict(key,name,ops,has_state)
        self._sel: Dict[str, bool] = {}
        self._all: bool = True

    # ---- Registrierung ----
    def register(self, key: str, name: str,
                 push: Optional[Callable] = None,
                 pull: Optional[Callable] = None,
                 status: Optional[Callable] = None) -> "KiExchangeModel":
        ops = {"push": push, "pull": pull, "status": status}
        self._providers.append({"key": key, "name": name, "ops": ops,
                                "has_state": any(ops.values())})
        self._sel.setdefault(key, True)
        return self

    def providers(self) -> List[Dict[str, Any]]:
        return [{"key": p["key"], "name": p["name"], "has_state": p["has_state"]}
                for p in self._providers]

    # ---- Auswahl ----
    def set_all(self, on: bool) -> None:
        self._all = bool(on)
        for k in self._sel:
            self._sel[k] = bool(on)

    def is_all(self) -> bool:
        return self._all

    def toggle(self, key: str, on: bool) -> None:
        self._sel[key] = bool(on)
        self._all = bool(self._providers) and all(
            self._sel.get(p["key"], False) for p in self._providers)

    def is_selected(self, key: str) -> bool:
        return self._all or self._sel.get(key, False)

    def selected(self) -> List[Dict[str, Any]]:
        return [p for p in self._providers if self.is_selected(p["key"])]

    # ---- Ausfuehrung ----
    def op_label(self, op: str) -> str:
        return _OP_LABEL.get(op, op)

    def run(self, op: str) -> List[Dict[str, Any]]:
        if op not in OPS:
            raise ValueError("unbekannte Operation: %r" % (op,))
        out: List[Dict[str, Any]] = []
        sel = self.selected()
        if not sel:
            return [{"name": "(keine Auswahl)", "ok": None, "msg": "kein Reiter ausgewaehlt"}]
        for p in sel:
            fn = p["ops"].get(op)
            if fn is None:
                out.append({"name": p["name"], "ok": None, "msg": "(kein teilbarer Zustand)"})
                continue
            try:
                r = fn()
                if isinstance(r, dict):
                    out.append({"name": p["name"], "ok": r.get("ok", True),
                                "msg": r.get("msg", "(ausgefuehrt)")})
                else:
                    out.append({"name": p["name"], "ok": True, "msg": "(ausgefuehrt)"})
            except Exception as e:
                out.append({"name": p["name"], "ok": False, "msg": "FEHLER %r" % (e,)})
        return out

    def format_result(self, op: str, results: List[Dict[str, Any]]) -> str:
        head = "%s ueber %d Reiter:" % (self.op_label(op), len(results))
        lines = ["  %-18s %s" % (r["name"], r["msg"]) for r in results]
        return head + "\n" + "\n".join(lines)
