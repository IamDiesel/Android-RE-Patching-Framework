# -*- coding: utf-8 -*-
"""native_trace_service — step-getriebener Emulations-Debugger + Pfad-Recorder (A4).

Treibt die arm64mini-CPU block- oder instruktionsweise, prueft Breakpoints, haelt ein
Schritt-Budget ein und zeichnet die besuchten **Block-Einstiege** (geordnet, mit
Wiederholungen) am eng-Objekt auf (``eng._trace``). Grundlage fuer "Trace -> Pseudocode".

Der eingefrorene ``run_function``-Pfad bleibt unberuehrt: hier werden ausschliesslich die
vorhandenen cpu-Primitive genutzt (``step_block`` / ``step`` / ``fetch``). Fuer einen
treuen Debug-Trace wird ``cpu.inline_loops=False`` gesetzt (sichtbare Schleifen-Iterationen,
exakte Block-Counts) und der Block-Cache geleert — das betrifft nur den Debug-Lauf.

SOA: stateless Service, Zustand liegt am eng-Objekt (so sehen GUI-Controller und
MCP-Session dieselbe Spur). Register/Speicher laufen weiter ueber NativeEmuService.
"""
from __future__ import annotations

from typing import Optional, Sequence

_M64 = (1 << 64) - 1
_SENT = 0xDEAD0000
_JTERM = ("b", "bl", "blr", "br", "ret", "cbz", "cbnz", "tbz", "tbnz", "brk", "svc", "hlt")


def _is_term(mnem: str) -> bool:
    m = (mnem or "").strip().lower()
    return m in _JTERM or m[:2] == "b."


class NativeTraceService:
    """Debugger-Schleife + Trace-Recorder. ``publish`` ist fuer optionale Events reserviert."""

    def __init__(self, publish: bool = True):
        self.publish = publish

    # ---- Trace-Zustand am eng -------------------------------------------
    def _trace(self, eng) -> list:
        t = getattr(eng, "_trace", None)
        if t is None:
            t = []
            eng._trace = t
        return t

    def get_trace(self, eng) -> list:
        """Geordnete Liste der besuchten Block-Einstiege: ``[{n, pc, pc_hex}]``."""
        return list(self._trace(eng))

    def clear_trace(self, eng) -> int:
        n = len(self._trace(eng))
        eng._trace = []
        eng._trace_last_term = True  # naechster Schritt beginnt einen Block
        return n

    def _append(self, eng, pc) -> None:
        t = self._trace(eng)
        p = int(pc) & _M64
        t.append({"n": len(t), "pc": p, "pc_hex": f"0x{p:x}"})

    # ---- Setup ----------------------------------------------------------
    def begin(self, eng, entry: int, args: Sequence = (), sp: Optional[int] = None,
              faithful: bool = True) -> dict:
        """Initialisiert einen Debug-Lauf: Register/pc/lr setzen, Trace leeren.

        ``faithful=True`` -> ``inline_loops`` aus + Block-Cache leeren (treue Schritte,
        Schleifen iterieren sichtbar). Nur ganzzahlige Register-Argumente (x0..x7-Konvention
        ueberlaesst die Service-/Controller-Schicht; hier werden Ints direkt gesetzt)."""
        cpu = eng.cpu
        ia = []
        for a in (args or ()):
            try:
                ia.append(int(a) & _M64)
            except Exception:
                ia.append(0)
        for i, a in enumerate(ia[:31]):
            cpu.x[i] = a
        if sp is not None:
            try:
                cpu.sp = int(sp) & _M64
            except Exception:
                pass
        cpu.lr = _SENT
        cpu.pc = int(entry) & _M64
        if faithful:
            try:
                cpu.inline_loops = False
                cpu.bb = {}
            except Exception:
                pass
        self.clear_trace(eng)
        return {"ok": True, "pc": cpu.pc, "pc_hex": f"0x{cpu.pc:x}"}

    # ---- Einzelschritte -------------------------------------------------
    def step_block(self, eng) -> dict:
        """Ein Basisblock. Haengt den Block-Einstieg (= aktueller pc) an den Trace."""
        cpu = eng.cpu
        pc = int(cpu.pc) & _M64
        if pc == _SENT:
            return {"ok": True, "done": True, "pc": pc, "pc_hex": f"0x{pc:x}"}
        self._append(eng, pc)
        eng._trace_last_term = True
        try:
            newpc = int(cpu.step_block()) & _M64
        except Exception as e:
            return {"ok": False, "why": "error", "error": repr(e)}
        return {"ok": True, "done": (newpc == _SENT), "pc": newpc, "pc_hex": f"0x{newpc:x}"}

    def step_insn(self, eng) -> dict:
        """Eine Instruktion (Interpreter-Pfad). Block-Einstieg nur bei Blockwechsel anhaengen."""
        cpu = eng.cpu
        pc = int(cpu.pc) & _M64
        if pc == _SENT:
            return {"ok": True, "done": True, "pc": pc, "pc_hex": f"0x{pc:x}"}
        # A3.1: Fremd-/FFI-Stub am pc? (step_block honoriert Stubs; der Instr-Pfad jetzt auch)
        _stub = getattr(cpu, "stub", None)
        _h = _stub.get(pc) if _stub else None
        if _h is not None:
            if getattr(eng, "_trace_last_term", True):
                self._append(eng, pc)
            try:
                _nxt = _h(cpu)
                cpu.pc = (_nxt if type(_nxt) is int else cpu.lr) & _M64
            except Exception as e:
                return {"ok": False, "why": "error", "error": repr(e)}
            eng._trace_last_term = True
            _ffi = getattr(eng, "_ffi", None)
            _nm = _ffi.by_addr.get(pc, "") if _ffi is not None else ""
            return {"ok": True, "done": (cpu.pc == _SENT), "pc": cpu.pc, "pc_hex": f"0x{cpu.pc:x}",
                    "insn": {"pc_hex": f"0x{pc:x}", "mnem": "<stub>", "ops": _nm}}
        if getattr(eng, "_trace_last_term", True):
            self._append(eng, pc)  # Einstieg eines neuen Blocks
        try:
            ins = cpu.fetch(pc)
            mnem = ins[1]
            newpc = int(cpu.step(ins)) & _M64
            cpu.pc = newpc
        except Exception as e:
            return {"ok": False, "why": "error", "error": repr(e)}
        eng._trace_last_term = _is_term(mnem)
        return {"ok": True, "done": (newpc == _SENT), "pc": newpc, "pc_hex": f"0x{newpc:x}",
                "insn": {"pc_hex": f"0x{pc:x}", "mnem": mnem, "ops": ins[2]}}

    # ---- Lauf bis Breakpoint / Ende / Budget ----------------------------
    def run(self, eng, breakpoints: Sequence = (), maxins: int = 2_000_000,
            mode: str = "block", skip_first_bp: bool = False) -> dict:
        """Treibt Schritte bis: Breakpoint erreicht / Funktion fertig (pc==SENT) / Budget.

        ``mode`` 'block' oder 'insn'. ``skip_first_bp=True`` ignoriert einen Breakpoint am
        Start-pc (fuer Resume, damit nicht sofort wieder angehalten wird)."""
        bps = set()
        for b in (breakpoints or []):
            try:
                bps.add(int(b) & _M64)
            except Exception:
                pass
        tick = self.step_insn if mode == "insn" else self.step_block
        steps = 0
        first = True
        while True:
            pc = int(eng.cpu.pc) & _M64
            if pc == _SENT:
                return {"ok": True, "done": True, "steps": steps, "pc": pc, "pc_hex": f"0x{pc:x}"}
            if pc in bps and not (first and skip_first_bp):
                return {"ok": True, "paused": True, "bp": pc, "steps": steps,
                        "pc": pc, "pc_hex": f"0x{pc:x}"}
            r = tick(eng)
            first = False
            if not r.get("ok"):
                r["steps"] = steps
                return r
            steps += 1
            if r.get("done"):
                return {"ok": True, "done": True, "steps": steps,
                        "pc": r["pc"], "pc_hex": r["pc_hex"]}
            if steps >= maxins:
                pc2 = int(eng.cpu.pc) & _M64
                return {"ok": True, "budget": True, "steps": steps,
                        "pc": pc2, "pc_hex": f"0x{pc2:x}"}

    def resume(self, eng, breakpoints: Sequence = (), maxins: int = 2_000_000,
               mode: str = "block") -> dict:
        """Setzt vom aktuellen pc fort (Breakpoint am Start-pc wird uebersprungen)."""
        return self.run(eng, breakpoints, maxins, mode, skip_first_bp=True)

    def debug_run(self, eng, entry: int, args: Sequence = (), sp: Optional[int] = None,
                  breakpoints: Sequence = (), maxins: int = 2_000_000,
                  mode: str = "block", faithful: bool = True) -> dict:
        """Komfort: begin + run in einem Aufruf (fuer MCP/Tests)."""
        self.begin(eng, entry, args, sp, faithful=faithful)
        r = self.run(eng, breakpoints, maxins, mode)
        r["trace_len"] = len(self.get_trace(eng))
        return r

    # ---- Export / Import ------------------------------------------------
    def export_trace(self, eng, path: str) -> dict:
        import json
        import os
        t = self.get_trace(eng)
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"count": len(t), "trace": t}, f, indent=2)
        return {"ok": True, "path": path, "count": len(t)}

    def load_trace(self, path: str) -> dict:
        import json
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        tr = d.get("trace") or []
        return {"ok": True, "count": len(tr), "trace": tr}
