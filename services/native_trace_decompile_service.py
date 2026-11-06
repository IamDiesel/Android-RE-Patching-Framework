# -*- coding: utf-8 -*-
"""native_trace_decompile_service — "Trace -> de-flattened Pseudocode/C" + Pfad-Lift zu einer Funktion.

Nimmt die von NativeTraceService aufgezeichnete **Block-Einstiegsfolge** (geordnet, mit
Wiederholungen) und erzeugt daraus lesbaren Pseudocode des TATSAECHLICH gelaufenen Pfades:

  1. eindeutige Bloecke in Erst-Auftritt-Reihenfolge via NativeLiftService.lift_block liften,
  2. aufeinanderfolgende Zyklen (Self-Loops wie auch mehr-Block-Schleifen) zu ``loop (xN)`` falten,
  3. Flow + Pseudocode rendern.

L2 (``lift_to_function``): dieselbe Spur zu EINER lauffaehigen Python-Funktion zusammenfuegen —
treuer Trace-Replay (Blockruempfe in Ausfuehrungsreihenfolge, Terminatoren entfallen, da der Trace
die Spruenge bereits entschieden hat; gefaltete Schleifen als ``for _ in range(N)``). ``run_lifted``
fuehrt sie gegen eine Engine aus (fuer das End-to-End-Gate und spaeter die GUI). Luecken werden vorab
ueber NativePyLiftService inline aufgeloest; verbleibt eine, wird ``ok=False`` (residual) gemeldet.

So wird OLLVM-Control-Flow-Flattening "herausgefaltet": der Dispatcher ist im gelaufenen Pfad bereits
aufgeloest, wir sehen die echte Block-Sequenz statt der Zustandsmaschine.

Ehrliche Grenzen: der GENOMMENE Pfad (ein Input -> eine Spur), nicht alle Pfade; mehrere Traces
mergebar. Schleifen haben die beobachtete Iterationszahl (Replay), nicht ``while <cond>`` (Polierpunkt).

SOA: stateless; der Aufrufer haelt die (geladene) Engine.
"""
from __future__ import annotations

import re
import sys
from typing import List, Optional, Sequence

from services.native_lift_service import NativeLiftService

_M64 = (1 << 64) - 1
_BL_TERM = re.compile(r"^cpu\.lr=(\d+);\s*return\b")


def _as_pc(x) -> Optional[int]:
    """Normalisiert einen Trace-Eintrag (int / '0x..' / {'pc':..}) zu int."""
    if isinstance(x, dict):
        x = x.get("pc", x.get("pc_hex"))
    if isinstance(x, int):
        return x & _M64
    if isinstance(x, str):
        try:
            return int(x, 0) & _M64
        except Exception:
            return None
    return None


def _fold_cycles(seq: List[int]) -> list:
    """Faltet aufeinanderfolgende Zyklen. Rueckgabe: Liste aus
    {'kind':'block','pc':int} oder {'kind':'loop','reps':int,'body':[int,...]}.
    Greedy kleinste Zykluslaenge L>=1 mit >=2 Wiederholungen (engste Schleife zuerst)."""
    out = []
    i, n = 0, len(seq)
    while i < n:
        best = None
        max_l = (n - i) // 2
        for L in range(1, max_l + 1):
            seg = seq[i:i + L]
            reps = 1
            while seq[i + reps * L: i + (reps + 1) * L] == seg:
                reps += 1
            if reps >= 2:
                best = (L, reps)
                break  # kleinstes L, das sich wiederholt -> engste Schleife
        if best:
            L, reps = best
            out.append({"kind": "loop", "reps": reps, "body": seq[i:i + L]})
            i += L * reps
        else:
            out.append({"kind": "block", "pc": seq[i]})
            i += 1
    return out


class NativeTraceDecompileService:
    """De-flattened Pseudocode + Pfad-Lift aus einer aufgezeichneten Block-Einstiegsfolge."""

    def __init__(self):
        self._lift = NativeLiftService()

    def _sequence_from(self, eng, trace) -> List[int]:
        if trace is None:
            trace = getattr(eng, "_trace", None) or []
        out = []
        for e in trace:
            pc = _as_pc(e)
            if pc is not None:
                out.append(pc)
        return out

    def decompile_trace(self, eng, trace: Optional[Sequence] = None) -> dict:
        """Haupteinstieg. ``trace`` optional (sonst eng._trace). Liefert flow/blocks/pseudocode."""
        seq = self._sequence_from(eng, trace)
        if not seq:
            return {"ok": False, "why": "empty", "error": "leere Trace-Sequenz"}

        blocks = {}
        order = []
        for pc in seq:
            key = f"0x{pc:x}"
            if key in blocks:
                continue
            order.append(key)
            r = self._lift.lift_block(eng, pc)
            blocks[key] = {"ok": bool(r.get("ok")),
                           "source": r.get("source"),
                           "successors": r.get("successors", []),
                           "error": r.get("error")}

        flow = _fold_cycles(seq)
        flow_out = []
        for it in flow:
            if it["kind"] == "block":
                flow_out.append({"kind": "block", "pc": f"0x{it['pc']:x}"})
            else:
                flow_out.append({"kind": "loop", "reps": it["reps"],
                                 "body": [f"0x{p:x}" for p in it["body"]]})

        pseudocode = self._render(seq, flow_out, blocks, order)
        return {"ok": True,
                "entry": f"0x{seq[0]:x}",
                "visits": len(seq),
                "unique": len(order),
                "sequence": [f"0x{p:x}" for p in seq],
                "order": order,
                "flow": flow_out,
                "blocks": blocks,
                "pseudocode": pseudocode}

    def decompile_sequence(self, eng, pcs: Sequence) -> dict:
        return self.decompile_trace(eng, pcs)

    # ---- L2: Pfad-Lift zu EINER Funktion --------------------------------
    @staticmethod
    def _term_to_stmt(term: str) -> Optional[str]:
        """Terminator-Zeile -> Reststatement (oder None=weglassen). bl behaelt das cpu.lr=<ret>."""
        t = term.strip()
        m = _BL_TERM.match(t)
        if m:
            return f"cpu.lr={m.group(1)}"
        if t.startswith("return"):
            return None
        return t  # defensiv (sollte bei inline_loops=False nicht vorkommen)

    def _block_body(self, inline_src: str) -> List[str]:
        """Rumpf-Statements eines (lueckenfreien) Block-Lifts, Terminator transformiert, col-0."""
        lines = inline_src.splitlines()
        body = lines[2:]  # 'def _blk(cpu):' + ' x=cpu.x; m=cpu.m' weg
        stmts = [(ln[1:] if ln.startswith(" ") else ln) for ln in body]
        stmts = [s for s in stmts if s.strip()]
        if stmts:
            repl = self._term_to_stmt(stmts[-1])
            stmts = stmts[:-1] + ([repl] if repl else [])
        return stmts

    def lift_to_function(self, eng, trace: Optional[Sequence] = None,
                         func_name: str = "lifted") -> dict:
        """Baut aus der Spur EINE lauffaehige Python-Funktion (treuer Replay, Schleifen gefaltet)."""
        from services.native_pylift_service import NativePyLiftService
        seq = self._sequence_from(eng, trace)
        if not seq:
            return {"ok": False, "why": "empty"}
        pyl = NativePyLiftService()
        bodies, residual = {}, []
        for pc in set(seq):
            r = pyl.lift_block_inline(eng, pc)
            if not r.get("ok"):
                return {"ok": False, "why": "lift_failed", "addr": f"0x{pc:x}"}
            if not r["hole_free"]:
                residual.append({"pc": f"0x{pc:x}", "holes": r["residual_holes"]})
            bodies[pc] = self._block_body(r["source"])
        if residual:
            return {"ok": False, "why": "residual_holes", "residual": residual}

        flow = _fold_cycles(seq)
        lines = [f"def {func_name}(cpu):", " x=cpu.x; m=cpu.m"]

        def emit(pc, depth):
            pad = " " * (depth + 1)
            lines.append(pad + f"# ---- block 0x{pc:x} ----")
            for st in bodies[pc]:
                lines.append(pad + st)

        li = 0
        for it in flow:
            if it["kind"] == "block":
                emit(it["pc"], 0)
            else:
                lines.append(f" for _i{li} in range({it['reps']}):   # loop 0x{it['body'][0]:x}")
                for pc in it["body"]:
                    emit(pc, 1)
                li += 1
        lines.append(" return cpu.x[0]")
        return {"ok": True, "entry": f"0x{seq[0]:x}", "func_name": func_name,
                "visits": len(seq), "unique": len(set(seq)), "flow": len(flow),
                "source": "\n".join(lines)}

    def run_lifted(self, eng, source: str, func_name: str = "lifted") -> int:
        """Fuehrt die geliftete Funktion gegen eng.cpu aus (arm64mini-Modulglobals als Scope)."""
        cls = type(eng.cpu)
        modg = getattr(getattr(cls, "__init__", None), "__globals__", None) or {}
        g = dict(modg)
        g.setdefault("M64", _M64)
        exec(source, g)
        g[func_name](eng.cpu)
        return eng.cpu.x[0] & _M64

    # ---- Rendering ------------------------------------------------------
    def _render(self, seq, flow, blocks, order) -> str:
        lines = []
        lines.append(f"// De-flattened ausgefuehrter Pfad — entry 0x{seq[0]:x}")
        lines.append(f"// {len(seq)} Block-Besuche, {len(order)} eindeutige Bloecke, "
                     f"Flow-Elemente: {len(flow)}")
        lines.append("// Hinweis: GENOMMENER Pfad fuer den Input, nicht alle Pfade. "
                     "'C' = lifter-Pseudocode, nicht compilierbar.")
        lines.append("")

        def emit_block(pc_hex, indent):
            pad = " " * indent
            b = blocks.get(pc_hex) or {}
            lines.append(f"{pad}// ---- block {pc_hex} ----")
            src = b.get("source")
            if not src:
                lines.append(f"{pad}// (kein Source: {b.get('error')})")
                return
            for ln in src.splitlines():
                lines.append(pad + ln)

        for it in flow:
            if it["kind"] == "block":
                emit_block(it["pc"], 0)
            else:
                body = it["body"]
                lines.append(f"loop (x{it['reps']}) {{   // {', '.join(body)}")
                for pc_hex in body:
                    emit_block(pc_hex, 2)
                lines.append("}")
            lines.append("")
        return "\n".join(lines)
