# -*- coding: utf-8 -*-
"""native_debug_session_service — GETEILTE Debug-Sitzung auf Platte (Mensch + KI).

Eine Wahrheit, zwei Haende: die GUI schreibt ihren Debug-Zustand als Sitzungsordner,
die KI (headless oder per MCP) liest/fuehrt denselben Zustand weiter und schreibt zurueck.
Ordnerinhalt:
  state.pkl.xz  — Speicherseiten + x/sp/lr (ueber NativeEmuService.save_snapshot)
  meta.json     — pc, Flags (NZCV), SIMD v[0..31], entry/args/sp/relocs, breakpoints,
                  mode, seq (Fortschreibungszaehler), writer (gui|ki), ts
  trace.json    — besuchte Blockfolge (NativeTraceService-Format)

Der volle Snapshot ist schwer -> wird nur bewusst geschrieben (GUI-Knopf bzw. KI-Anfrage
mit Freigabe), nie automatisch. Der headless-Pfad bleibt davon unberuehrt.
SOA: stateless; der Aufrufer haelt die Engine.
"""
from __future__ import annotations

import json
import os
import time

from services.native_emu_service import NativeEmuService

_M64 = (1 << 64) - 1


class NativeDebugSessionService:
    STATE = "state.pkl.xz"
    TRACE = "trace.json"
    META = "meta.json"

    def __init__(self):
        self._emu = NativeEmuService(publish=False)

    def _p(self, folder, name):
        return os.path.join(folder, name)

    # ---- Schreiben ------------------------------------------------------
    def save(self, eng, folder: str, meta: dict = None, writer: str = "gui",
             base_seq=None, force: bool = False) -> dict:
        """Schreibt den vollstaendigen Debug-Zustand (Snapshot + pc/Flags/SIMD + Trace + meta).

        Clobber-Schutz: ist ``base_seq`` gesetzt und die Datei bereits weiter (seq > base_seq)
        und nicht ``force`` -> KEIN Schreiben, Rueckgabe why='conflict'. Vor dem Ueberschreiben
        wird der bisherige Stand nach ``history/seq_<n>_<writer>/`` gesichert."""
        os.makedirs(folder, exist_ok=True)
        prev = self.peek(folder, with_trace=False).get("meta") or {}
        prev_seq = int(prev.get("seq", 0) or 0)
        if base_seq is not None and not force and prev_seq > int(base_seq):
            return {"ok": False, "why": "conflict", "file_seq": prev_seq,
                    "file_writer": prev.get("writer"), "base_seq": int(base_seq)}
        if prev:
            self._backup(folder, prev_seq, prev.get("writer"))
        self._emu.save_snapshot(eng, self._p(folder, self.STATE))
        c = eng.cpu
        trace = list(getattr(eng, "_trace", []) or [])
        with open(self._p(folder, self.TRACE), "w", encoding="utf-8") as f:
            json.dump({"count": len(trace), "trace": trace}, f)
        m = dict(meta or {})
        if "entry" not in m:
            m["entry"] = prev.get("entry")
        m["pc"] = int(getattr(c, "pc", 0)) & _M64
        m["flags"] = {"N": int(c.n), "Z": int(c.z), "C": int(c.c), "V": int(c.vf)}
        m["v"] = [bytes(c.v[i]).hex() for i in range(32)]
        m["seq"] = prev_seq + 1
        m["writer"] = writer
        m["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self._p(folder, self.META), "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
        return {"ok": True, "folder": folder, "seq": m["seq"],
                "pc": f"0x{m['pc']:x}", "trace": len(trace), "writer": writer}

    def _backup(self, folder, seq, writer):
        """Sichert den bisherigen Stand nach history/seq_<n>_<writer>/ (vor dem Ueberschreiben)."""
        import shutil
        hist = os.path.join(folder, "history")
        try:
            os.makedirs(hist, exist_ok=True)
            dst = os.path.join(hist, f"seq_{seq}_{writer or 'na'}")
            os.makedirs(dst, exist_ok=True)
            for name in (self.STATE, self.TRACE, self.META):
                src = self._p(folder, name)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(dst, name))
            self._prune_history(hist, keep=20)
        except Exception:
            pass

    def _prune_history(self, hist, keep=20):
        import shutil
        try:
            dirs = [os.path.join(hist, d) for d in os.listdir(hist)]
            dirs = [d for d in dirs if os.path.isdir(d)]
            dirs.sort(key=lambda d: os.path.getmtime(d))
            for d in dirs[:-keep]:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

    # ---- Lesen (leicht) -------------------------------------------------
    def peek(self, folder: str, with_trace: bool = True) -> dict:
        """Nur meta (+ optional trace) lesen, OHNE den schweren Snapshot zu laden."""
        out = {"ok": True}
        try:
            with open(self._p(folder, self.META), encoding="utf-8") as f:
                out["meta"] = json.load(f)
        except Exception:
            out["meta"] = None
        if with_trace:
            try:
                with open(self._p(folder, self.TRACE), encoding="utf-8") as f:
                    out["trace"] = json.load(f).get("trace", [])
            except Exception:
                out["trace"] = []
        return out

    # ---- Laden (voll) ---------------------------------------------------
    def load(self, eng, folder: str) -> dict:
        """Stellt den vollen Zustand in `eng` wieder her (Snapshot + pc/Flags/SIMD + Trace)."""
        meta = self.peek(folder, with_trace=False).get("meta") or {}
        self._emu.load_snapshot(eng, self._p(folder, self.STATE))
        c = eng.cpu
        if "pc" in meta:
            c.pc = int(meta["pc"]) & _M64
        fl = meta.get("flags") or {}
        c.n, c.z, c.c, c.vf = (int(fl.get("N", 0)), int(fl.get("Z", 0)),
                               int(fl.get("C", 0)), int(fl.get("V", 0)))
        for i, h in enumerate((meta.get("v") or [])[:32]):
            try:
                c.v[i] = bytearray(bytes.fromhex(h))
            except Exception:
                pass
        try:
            c.inline_loops = False
            c.bb = {}
        except Exception:
            pass
        trace = self.peek(folder).get("trace") or []
        eng._trace = list(trace)
        eng._trace_last_term = True
        return {"ok": True, "meta": meta, "trace_len": len(trace),
                "pc": f"0x{int(getattr(c, 'pc', 0)) & _M64:x}", "seq": meta.get("seq")}
