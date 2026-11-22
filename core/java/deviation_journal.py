# -*- coding: utf-8 -*-
"""core.java.deviation_journal — DebugStop-Signal & dedupliziertes Abweichungs-Journal.

Erfasst thread-sicher Emulations-Abweichungen (lenient_vm, missing_method, missing_stub,
dropped_runnable, asynctask_error, ...), dedupliziert nach (kind, site) und kappt auf max_entries.
"""
from __future__ import annotations

import threading
from typing import Any


class DebugStop(BaseException):
    """Signal: der Debugger wurde gestoppt (erbt von BaseException, damit `except Exception:` es nicht schluckt)."""


class DeviationJournal:
    """Thread-sicheres, dedupliziertes Journal fuer Emulations-Abweichungen."""

    def __init__(self, max_entries: int = 2000):
        self._lock = threading.RLock()
        self._max_entries = max(1, int(max_entries))
        self._entries: list[dict[str, Any]] = []
        self._index: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, kind: str, site: str, detail: str = "", step: int = 0) -> None:
        key = (str(kind), str(site))
        with self._lock:
            existing = self._index.get(key)
            if existing is not None:
                existing["count"] += 1
                if detail and not existing["detail"]:
                    existing["detail"] = str(detail)
                return
            if len(self._entries) >= self._max_entries:
                return
            entry = {
                "kind": key[0],
                "site": key[1],
                "detail": str(detail or ""),
                "count": 1,
                "first_step": int(step or 0),
            }
            self._index[key] = entry
            self._entries.append(entry)

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._entries]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_kind: dict[str, int] = {}
            total = 0
            for e in self._entries:
                cnt = int(e.get("count", 1))
                total += cnt
                k = e["kind"]
                by_kind[k] = by_kind.get(k, 0) + cnt
            return {
                "unique": len(self._entries),
                "total": total,
                "by_kind": by_kind,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._index.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
