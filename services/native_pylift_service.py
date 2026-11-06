# -*- coding: utf-8 -*-
"""native_pylift_service — automatisierter dynamischer Lifter (Assembly -> echtes Inline-Python).

Nimmt die aus der AUSFUEHRUNG gewonnene Block-Quelle (NativeLiftService / bbsrc-Hook) und
ersetzt die verbliebenen ``cpu.step(INS[k])``-Fallbacks durch **Inline-Python**, sodass ein
gelaufener Block als lueckenfreier Python-Code vorliegt. Form-Abdeckung ist bewusst additiv
(waechst mit den real auftretenden Luecken); nicht abgedeckte Formen bleiben als
``cpu.step(...)``-Fallback erhalten (korrekt, nur nicht inline) und werden als ``residual`` gemeldet.

WICHTIG: beruehrt ``arm64mini`` NICHT. Die emittierte Semantik spiegelt die des Interpreters
(``rd``/``wr``-Masking, ``_memaddr`` fuer Adressen) und wird per bit-exaktem Gate (Tests) gegen
``cpu.step`` gegengeprueft — der Emulator bleibt die Wahrheitsquelle.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from services.native_lift_service import NativeLiftService

_M64 = (1 << 64) - 1
# Zeile, die komplett ein cpu.step(INS[k]) ist (Rumpf oder Terminator-Fallback):
_HOLE = re.compile(r"^(\s*)(return\s+)?cpu\.step\(INS\[(\d+)\]\)\s*$")


class NativePyLiftService:
    """Lueckenfuellender Lifter auf Basis der ausgefuehrten Block-Quelle."""

    def __init__(self):
        self._lift = NativeLiftService()

    # ---- Operanden-Helfer (spiegeln arm64mini.rd/wr-Masking) ------------
    @staticmethod
    def _ops_regs(ops: str) -> List[str]:
        """Register-Operanden vor einer evtl. Speicherklammer, komma-getrennt."""
        pre = ops.split("[", 1)[0]
        return [t.strip() for t in pre.split(",") if t.strip()]

    @staticmethod
    def _bracket(ops: str) -> str:
        i = ops.index("[")
        return ops[i:]

    @staticmethod
    def _is32(reg: str) -> bool:
        return reg.strip().lower().startswith("w")

    def _rd(self, reg: str) -> str:
        r = reg.strip().lower()
        if r in ("xzr", "wzr"):
            return "0"
        if r in ("sp", "wsp", "xsp"):
            return "(cpu.sp)"
        n = int(r[1:])
        return f"(x[{n}]&0xffffffff)" if r[0] == "w" else f"x[{n}]"

    def _wr(self, reg: str, expr: str) -> str:
        r = reg.strip().lower()
        if r in ("xzr", "wzr"):
            return f"pass  # {reg} verworfen"
        if r in ("sp", "wsp", "xsp"):
            return f"cpu.sp=({expr})&M64"
        n = int(r[1:])
        if r[0] == "w":
            return f"x[{n}]=({expr})&0xffffffff"
        return f"x[{n}]=({expr})&M64"

    @staticmethod
    def _imm(tok: str) -> int:
        return int(tok.strip().lstrip("#").split(",")[0].strip(), 0)

    # ---- Einzel-Instruktion -> Inline-Python ----------------------------
    def emit_insn(self, mnem: str, ops: str) -> Tuple[List[str], bool]:
        """Liefert (code_zeilen, ok). ok=False -> Form nicht abgedeckt (Fallback behalten)."""
        m = mnem.strip().lower()
        T = self._ops_regs(ops)
        try:
            if m == "mrs":                                   # Systemregister -> 0 (tpidr_el0/Canary)
                return ([self._wr(T[0], "0") + f"  # mrs {ops}"], True)
            if m == "udiv":                                  # unsigned div (b==0 -> 0)
                e = f"(({self._rd(T[1])})//({self._rd(T[2])}) if ({self._rd(T[2])}) else 0)"
                return ([self._wr(T[0], e) + f"  # udiv {ops}"], True)
            if m in ("ldar", "ldaxr", "ldxr", "ldarb", "ldarh"):   # load-(acquire/exclusive)
                sz = 1 if m.endswith("b") else 2 if m.endswith("h") else (4 if self._is32(T[0]) else 8)
                br = self._bracket(ops)
                return ([self._wr(T[0], f"m.r(cpu._memaddr({br!r}),{sz})") + f"  # {m} {ops}"], True)
            if m in ("stlr", "stlrb", "stlrh"):              # store-release
                sz = 1 if m == "stlrb" else 2 if m == "stlrh" else (4 if self._is32(T[0]) else 8)
                br = self._bracket(ops)
                return ([f"m.w(cpu._memaddr({br!r}),{self._rd(T[0])},{sz})  # {m} {ops}"], True)
            if m in ("stlxr", "stxr"):                       # store-exclusive: status T[0]=0 (Erfolg)
                sz = 4 if self._is32(T[1]) else 8
                br = self._bracket(ops)
                return ([f"m.w(cpu._memaddr({br!r}),{self._rd(T[1])},{sz})  # {m} {ops}",
                         self._wr(T[0], "0")], True)
            if m in ("sub", "add") and self._has_sp(T):      # sp-Arithmetik (imm oder reg)
                third = T[2]
                if third.startswith("#"):
                    rhs = str(self._imm(third))
                else:
                    rhs = self._rd(third)
                op = "-" if m == "sub" else "+"
                return ([self._wr(T[0], f"({self._rd(T[1])}){op}({rhs})") + f"  # {m} {ops}"], True)
            if m == "mov" and self._has_sp(T):               # mov sp,reg / mov reg,sp
                return ([self._wr(T[0], self._rd(T[1])) + f"  # mov {ops}"], True)
        except Exception:
            return ([], False)
        return ([], False)

    @staticmethod
    def _has_sp(T: List[str]) -> bool:
        return any(t.strip().lower() in ("sp", "wsp", "xsp") for t in T)

    # ---- Block -> lueckenfreies Inline-Python ---------------------------
    def lift_block_inline(self, eng, pc) -> dict:
        """Liftet den Block an ``pc`` und ersetzt abgedeckte ``cpu.step``-Luecken durch Inline-Python."""
        a = int(pc)
        r = self._lift.lift_block(eng, a)
        if not r.get("ok"):
            return r
        out, residual = [], []
        for ln in (r["source"] or "").splitlines():
            m = _HOLE.match(ln)
            if m:
                indent, is_ret, k = m.group(1), m.group(2), int(m.group(3))
                ins = eng.cpu.fetch(a + 4 * k)          # INS[k] liegt bei pc0 + 4*k (blockintern linear)
                code, ok = self.emit_insn(ins[1], ins[2])
                if ok and not is_ret:
                    out.extend(indent + c for c in code)
                    continue
                residual.append({"k": k, "mnem": ins[1], "ops": ins[2],
                                 "terminator": bool(is_ret)})
            out.append(ln)
        return {"ok": True, "addr": f"0x{a:x}", "source": "\n".join(out),
                "residual_holes": residual, "hole_free": not residual}
