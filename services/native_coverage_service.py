# -*- coding: utf-8 -*-
"""native_coverage_service — Coverage- & Unknown-Instr-Report fuer native Emulation.

Fuehrt eine Funktion aus und berichtet, welche Instruktionen/Mnemonics erreicht wurden,
welche nur ueber den langsamen `cpu.step`-Pfad liefen (Kandidaten fuer einen schnellen
Emit) und ob ein harter Abbruch durch eine unbekannte Instruktion auftrat. Grundlage fuer
den „unknown -> implement"-Loop bei generischen Zielen. IP-neutral.

Nutzt den bit-neutralen bbsrc-Dump-Hook von arm64mini: jeder waehrend des Laufs
kompilierte Basisblock liegt danach als Python-Quelltext in cpu.bbsrc. Die je Block
langsam interpretierten Instruktionen stehen dort als `cpu.step(INS[k])`.
"""
from __future__ import annotations

import re

from services.native_emu_service import NativeEmuService

_STEP_RE = re.compile(r"cpu\.step\(INS\[(\d+)\]\)")


class NativeCoverageService:
    def analyze(self, eng) -> dict:
        """Wertet cpu.bbsrc (nach einem Lauf) aus: Mnemonic-Zaehlung + Slow-Pfad-Mnemonics."""
        cpu = eng.cpu
        bbsrc = getattr(cpu, "bbsrc", {}) or {}
        jterm = set(getattr(cpu, "_JTERM", ()))
        mnem, slow, total = {}, {}, 0
        for pc0, src in bbsrc.items():
            slow_idx = {int(x) for x in _STEP_RE.findall(src)}
            pc, k = int(pc0), 0
            while True:
                try:
                    ins = cpu.fetch(pc)
                except Exception:
                    break
                m = ins[1]
                mnem[m] = mnem.get(m, 0) + 1
                total += 1
                if k in slow_idx:
                    slow[m] = slow.get(m, 0) + 1
                if m in jterm or m[:2] == "b.":
                    break
                pc += 4; k += 1
                if k > 400:
                    break
        return {"blocks_reached": len(bbsrc), "instr_total": total,
                "mnemonics": mnem, "slow_path": slow}

    def run_coverage(self, eng, entry, args=(), sp=None, maxins: int = 2_000_000, image=None) -> dict:
        """Aktiviert den bbsrc-Hook, fuehrt aus (via NativeEmuService) und analysiert.
        `eng` sollte frisch sein (sonst zaehlen bereits gecachte Bloecke nicht mit)."""
        eng.cpu.bbsrc = {}
        res = NativeEmuService(publish=False).run(eng, entry, args, None, sp, int(maxins), image=image)
        cov = self.analyze(eng)
        cov["ran_ok"] = bool(res.get("ok"))
        if not res.get("ok"):
            cov["why"] = res.get("why")
            if res.get("why") == "unknown_instr":
                cov["unknown"] = {k: res.get(k) for k in ("pc", "raw", "mnem") if k in res}
        else:
            cov["ret"] = res.get("ret")
        return cov
