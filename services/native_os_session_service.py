# -*- coding: utf-8 -*-
"""native_os_session_service — GETEILTER OS-Inspektor-Zustand (Mensch + KI).

Eine Wahrheit auf Platte fuer das OS-Substrat: das config-Delta (Policy/Replay/Seed/
VFS/Trace/...) + die letzte Summary. Leichtes JSON-Buendel mit seq/writer/Clobber-Schutz,
exakt wie Debug-/Studio-Sitzung (G-OS-5). SOA: stateless.

Ordner secrets/probe/out/os/:
  os_session.json  — {config{...}, summary{...}, seq, writer(gui|ki|run), ts}
  history/seq_<n>_<writer>/  — Sicherung vor jedem Ueberschreiben (letzte 20)
"""
from __future__ import annotations

import json
import os
import shutil
import time


class NativeOsSessionService:
    SESSION = "os_session.json"

    def _p(self, folder, name):
        return os.path.join(folder, name)

    def peek(self, folder: str) -> dict:
        try:
            with open(self._p(folder, self.SESSION), encoding="utf-8") as f:
                return {"ok": True, "session": json.load(f)}
        except Exception:
            return {"ok": True, "session": None}

    def save(self, folder: str, config=None, summary=None, writer: str = "ki",
             base_seq=None, force: bool = False) -> dict:
        """OS-Zustand schreiben. Clobber-Schutz ueber base_seq/seq. config/summary werden
        nur ersetzt, wenn uebergeben (sonst Vorwert erhalten)."""
        os.makedirs(folder, exist_ok=True)
        prev = self.peek(folder).get("session") or {}
        prev_seq = int(prev.get("seq", 0) or 0)
        if base_seq is not None and not force and prev_seq > int(base_seq):
            return {"ok": False, "why": "conflict", "file_seq": prev_seq,
                    "file_writer": prev.get("writer"), "base_seq": int(base_seq)}
        if prev:
            self._backup(folder, prev_seq, prev.get("writer"))
        sess = {
            "config": dict(config) if config is not None else (prev.get("config") or {}),
            "summary": summary if summary is not None else prev.get("summary"),
            "seq": prev_seq + 1,
            "writer": writer,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(self._p(folder, self.SESSION), "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2, default=str)
        return {"ok": True, "folder": folder, "seq": sess["seq"], "writer": writer}

    def _backup(self, folder, seq, writer):
        hist = os.path.join(folder, "history")
        try:
            os.makedirs(hist, exist_ok=True)
            dst = os.path.join(hist, "seq_%s_%s" % (seq, writer or "na"))
            os.makedirs(dst, exist_ok=True)
            src = self._p(folder, self.SESSION)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dst, self.SESSION))
            self._prune_history(hist, keep=20)
        except Exception:
            pass

    def _prune_history(self, hist, keep=20):
        try:
            dirs = [os.path.join(hist, d) for d in os.listdir(hist)]
            dirs = [d for d in dirs if os.path.isdir(d)]
            dirs.sort(key=lambda d: os.path.getmtime(d))
            for d in dirs[:-keep]:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
