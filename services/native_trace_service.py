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
            _sv = getattr(eng, "_survive", None)
            if _sv is not None and pc < _sv["min"]:
                lr = int(eng.cpu.lr) & _M64
                _sv["log"].append({"dead_pc": f"0x{pc:x}", "ret_to": f"0x{lr:x}", "steps": steps})
                if len(_sv["log"]) >= _sv["cap"]:
                    return {"ok": True, "survive_cap": True, "steps": steps,
                            "pc": pc, "pc_hex": f"0x{pc:x}"}
                if lr == pc or lr < _sv["min"] or lr == _SENT:
                    return {"ok": True, "done": True, "survived_end": True, "steps": steps,
                            "pc": lr, "pc_hex": f"0x{lr:x}"}
                eng.cpu.pc = lr
                continue
            if pc in bps and not (first and skip_first_bp):
                return {"ok": True, "paused": True, "bp": pc, "steps": steps,
                        "pc": pc, "pc_hex": f"0x{pc:x}"}
            r = tick(eng)
            first = False
            if not r.get("ok"):
                _sv2 = getattr(eng, "_survive", None)
                if _sv2 is not None and len(_sv2["log"]) < _sv2["cap"]:
                    _lr = int(eng.cpu.lr) & _M64
                    _pc = int(eng.cpu.pc) & _M64
                    _sv2["log"].append({"err": r.get("error"), "at": f"0x{_pc:x}",
                                        "ret_to": f"0x{_lr:x}", "steps": steps})
                    if _lr >= _sv2["min"] and _lr != _SENT and _lr != _pc:
                        eng.cpu.pc = _lr
                        continue
                    return {"ok": True, "done": True, "survived_end": True, "steps": steps,
                            "pc": _lr, "pc_hex": f"0x{_lr:x}"}
                r["steps"] = steps
                return r
            steps += 1
            # G-OS-0: Syscall-Breakpoint -> an der Block-/Insn-Grenze pausieren
            sb = getattr(eng.cpu, "_svc_break", None)
            if sb is not None:
                eng.cpu._svc_break = None
                pcn = int(eng.cpu.pc) & _M64
                return {"ok": True, "paused_syscall": True, "syscall": sb, "steps": steps,
                        "pc": pcn, "pc_hex": f"0x{pcn:x}"}
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

    # ---- CFF-Dispatch-Tracer (2D-vtable 0x137740) -----------------------
    # Fassaden-Bereiche: Handles 0x55.., JNIEnv/JavaVM 0x66.., FFI-Stubs 0x7a..
    _DISP_TAINT = ((0x55000000, 0x56000000), (0x66000000, 0x67000000),
                   (0x7a000000, 0x7b000000))

    @classmethod
    def _disp_taint(cls, v):
        v &= _M64
        for a, b in cls._DISP_TAINT:
            if a <= v < b:
                return True
        return False

    def dispatch_trace_enable(self, eng, vt_base: int = 0x137740,
                              lo: int = 0x136000, hi: int = 0x13a000,
                              cap: int = 200000) -> dict:
        """Read-Watchpoint auf die CFF-vtable-Region [lo,hi). Jeder 8-Byte-Read dort ist ein
        Dispatch-Fetch und wird als {pc,slot,idx1,col,val,empty,val_taint,x19,x20,x20_taint}
        an eng._disp['log'] protokolliert. Additiv, bit-neutral (nur Lesen). disable() stellt
        die Originale wieder her."""
        st = {"vt": int(vt_base), "lo": int(lo), "hi": int(hi), "cap": int(cap), "log": []}
        eng._disp = st
        eng._disp_orig = {}
        m = eng.cpu.m
        log = st["log"]
        taint = self._disp_taint

        def rec(ai, val):
            if len(log) >= st["cap"]:
                return
            cpu = eng.cpu
            off = ai - st["vt"]
            idx1 = off // 0x7f0 if off >= 0 else -((-off) // 0x7f0 + 1)
            col = (off - idx1 * 0x7f0) // 8
            v = int(val) & _M64
            x20 = int(cpu.x[20]) & _M64
            log.append({"pc": int(getattr(cpu, "pc", 0) or 0) & _M64, "slot": ai, "idx1": idx1, "col": col,
                        "val": v, "empty": (v == 0), "val_taint": taint(v),
                        "x19": int(cpu.x[19]) & _M64, "x20": x20, "x20_taint": taint(x20)})

        orig64 = getattr(m, "r64", None)
        if orig64 is not None:
            eng._disp_orig["r64"] = orig64

            def w64(a):
                val = orig64(a)
                if st["lo"] <= a < st["hi"]:
                    rec(a, val)
                return val
            m.r64 = w64

        origr = getattr(m, "r", None)
        if origr is not None:
            eng._disp_orig["r"] = origr

            def wr(a, sz):
                val = origr(a, sz)
                if sz == 8 and st["lo"] <= a < st["hi"]:
                    rec(a, val)
                return val
            m.r = wr
        return {"ok": True, "lo": hex(lo), "hi": hex(hi), "vt": hex(vt_base)}

    def dispatch_trace_get(self, eng) -> list:
        st = getattr(eng, "_disp", None)
        return list(st["log"]) if st else []

    def dispatch_trace_disable(self, eng) -> dict:
        m = eng.cpu.m
        for meth, orig in (getattr(eng, "_disp_orig", {}) or {}).items():
            setattr(m, meth, orig)
        eng._disp_orig = {}
        return {"ok": True}

    def dispatch_trace_summary(self, eng) -> dict:
        """Kompakt: Anzahl Dispatches, leere Fetches (Trap-Kandidaten), getaintete, und die
        ERSTE getaintete bzw. erste leere Stelle (= Divergenz-Kandidat)."""
        log = self.dispatch_trace_get(eng)
        empties = [e for e in log if e["empty"]]
        tainted = [e for e in log if e.get("val_taint") or e.get("x20_taint")]
        def hexify(e):
            return None if e is None else {k: (hex(e[k]) if k in ("pc", "slot", "val", "x19", "x20") else e[k]) for k in e}
        return {"dispatches": len(log), "empty_fetches": len(empties),
                "tainted": len(tainted),
                "first_tainted": hexify(tainted[0] if tainted else None),
                "first_empty": hexify(empties[0] if empties else None)}

    # ---- RASP Survival Mode (tote Anti-Tamper-Dispatches überleben) ------
    def survive_enable(self, eng, min_pc: int = 0x1000, cap: int = 50000) -> dict:
        """Sprünge auf tote Ziele (pc < min_pc, z.B. leere Vtable-Lücke -> 0) werden
        protokolliert und per Rücksprung nach lr übersprungen statt zu crashen. So läuft der
        Code HINTER der Anti-Tamper-Trap weiter und wird über FFI-/JNI-/Dispatch-Logs sichtbar.
        Additiv; disable() schaltet zurück."""
        eng._survive = {"min": int(min_pc), "cap": int(cap), "log": []}
        return {"ok": True, "min": hex(min_pc), "cap": int(cap)}

    def survive_get(self, eng) -> list:
        st = getattr(eng, "_survive", None)
        return list(st["log"]) if st else []

    def survive_disable(self, eng) -> dict:
        eng._survive = None
        return {"ok": True}
