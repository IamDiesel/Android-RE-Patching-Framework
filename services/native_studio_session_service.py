# -*- coding: utf-8 -*-
"""native_studio_session_service — GETEILTER Native-Studio-Zustand (Mensch + KI).

Verallgemeinert die Debug-Sitzung auf den kompletten Analyse-Kontext: welche Lib
geladen ist, die aktuellen Eingaben (Entry/Args/Budget/Preset/Relocs/aktiver
Workflow) und das letzte Ergebnis. LEICHTES JSON-Buendel (kein schwerer
Emu-Snapshot) -> schnell und text-diffbar.

Ordner secrets/probe/out/studio/:
  session.json  — {lib, inputs{...}, seq, writer(gui|ki), ts}
  result.json   — letztes vereinheitlichtes Ergebnis (Workflow-Output / Sections)
  history/seq_<n>_<writer>/  — Sicherung vor jedem Ueberschreiben (letzte 20)

seq/writer/Clobber-Schutz exakt wie die Debug-Sitzung. SOA: stateless.
"""
from __future__ import annotations

import json
import os
import shutil
import time


class NativeStudioSessionService:
    SESSION = "session.json"
    RESULT = "result.json"

    def _p(self, folder, name):
        return os.path.join(folder, name)

    # ---- Lesen (leicht) -------------------------------------------------
    def peek(self, folder: str, with_result: bool = True) -> dict:
        out = {"ok": True}
        try:
            with open(self._p(folder, self.SESSION), encoding="utf-8") as f:
                out["session"] = json.load(f)
        except Exception:
            out["session"] = None
        if with_result:
            try:
                with open(self._p(folder, self.RESULT), encoding="utf-8") as f:
                    out["result"] = json.load(f)
            except Exception:
                out["result"] = None
        return out

    # ---- Schreiben ------------------------------------------------------
    def save(self, folder: str, lib=None, inputs: dict = None, result=None,
             writer: str = "gui", base_seq=None, force: bool = False) -> dict:
        """Studio-Zustand schreiben. Clobber-Schutz ueber base_seq/seq wie die Debug-Sitzung."""
        os.makedirs(folder, exist_ok=True)
        prev = self.peek(folder, with_result=False).get("session") or {}
        prev_seq = int(prev.get("seq", 0) or 0)
        if base_seq is not None and not force and prev_seq > int(base_seq):
            return {"ok": False, "why": "conflict", "file_seq": prev_seq,
                    "file_writer": prev.get("writer"), "base_seq": int(base_seq)}
        if prev:
            self._backup(folder, prev_seq, prev.get("writer"))
        sess = {
            "lib": lib if lib is not None else prev.get("lib"),
            "inputs": dict(inputs) if inputs is not None else (prev.get("inputs") or {}),
            "seq": prev_seq + 1,
            "writer": writer,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with open(self._p(folder, self.SESSION), "w", encoding="utf-8") as f:
            json.dump(sess, f, indent=2)
        if result is not None:
            try:
                payload = dict(result) if isinstance(result, dict) else {"raw": result}
                payload.setdefault("ts", sess["ts"])
                with open(self._p(folder, self.RESULT), "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, default=str)
            except Exception:
                pass
        return {"ok": True, "folder": folder, "seq": sess["seq"], "writer": writer,
                "lib": sess["lib"]}

    def _backup(self, folder, seq, writer):
        hist = os.path.join(folder, "history")
        try:
            os.makedirs(hist, exist_ok=True)
            dst = os.path.join(hist, f"seq_{seq}_{writer or 'na'}")
            os.makedirs(dst, exist_ok=True)
            for name in (self.SESSION, self.RESULT):
                src = self._p(folder, name)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(dst, name))
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
