# -*- coding: utf-8 -*-
"""native_lift_service — generischer „Block -> Python"-Lifter fuer nativen AArch64-Code.

Greift den bit-neutralen bbsrc-Dump-Hook von arm64mini ab: ist cpu.bbsrc ein dict,
legt _compile_block den fuer einen Basisblock erzeugten Python-Quelltext dort ab. Dieser
Service aktiviert den Hook, loest die Kompilierung eines Blocks aus und liefert den
Quelltext + die statischen Nachfolger (intra-funktional).

IP-neutral: hebt beliebigen nativen Code (keine LLU-Spezifik). Teil der RE-Studio-
Generalisierung (siehe forschung-backlog.md / restudio-generalisierung.md). SOA:
der Aufrufer haelt die (geladene) Engine; dieser Service kapselt nur das Lifting.
"""
from __future__ import annotations

import re
from typing import Optional

from core.native.engine_arm64 import Arm64Engine

# Nachfolger werden NUR aus Kontrolltransfer-Zeilen gezogen (return.. / 'cpu.lr=..; return..'),
# nie aus Rumpf-Zeilen wie 'x[0]=(1 if cond else 0)' (inline-Select, kein Sprung).
_CALL_RE = re.compile(r"^cpu\.lr=(\d+);\s*return\s+\d+")                 # bl: Fallthrough=<ret>, Callee ignoriert
_RET_COND_RE = re.compile(r"^return\s+\((\d+)\s+if\s+.*\s+else\s+(\d+)\)\s*$")  # b.cc/cbz/tbz: then+fallthrough
_RET_INT_RE = re.compile(r"^return\s+(\d+)\s*$")                           # b / Loop-Exit: ein Ziel


class NativeLiftService:
    """Hebt Basisbloecke nativen AArch64-Codes nach Python (ueber den bbsrc-Hook)."""

    def _successors(self, src: str) -> list:
        succ = set()
        for line in src.splitlines():
            ls = line.strip()
            mc = _CALL_RE.match(ls)
            if mc:
                succ.add(int(mc.group(1)))
                continue
            if not ls.startswith("return"):
                continue                               # Rumpf-Zeilen (inkl. inline if/else) ueberspringen
            mcond = _RET_COND_RE.match(ls)
            if mcond:
                succ.add(int(mcond.group(1))); succ.add(int(mcond.group(2)))
                continue
            m1 = _RET_INT_RE.match(ls)
            if m1:
                succ.add(int(m1.group(1)))
            # 'return cpu.lr' / 'return <expr>' -> dynamisch/kein statischer Nachfolger
        return sorted(succ)

    def lift_block(self, eng: Arm64Engine, addr) -> dict:
        """Liefert den generierten Python-Quelltext fuer den Basisblock an `addr`
        + die statischen Nachfolger. `eng` muss die .so bereits geladen haben."""
        a = int(addr)
        cpu = eng.cpu
        if getattr(cpu, "bbsrc", None) is None:
            cpu.bbsrc = {}
        try:
            cpu._compile_block(a)
        except Exception as e:
            return {"ok": False, "addr": f"0x{a:x}", "error": repr(e)}
        src = cpu.bbsrc.get(a)
        if not src:
            return {"ok": False, "addr": f"0x{a:x}", "error": "kein Block-Source (bbsrc leer)"}
        succ = self._successors(src)
        return {"ok": True, "addr": f"0x{a:x}", "source": src,
                "successors": [f"0x{s:x}" for s in succ]}

    def lift_function(self, eng: Arm64Engine, entry, max_blocks: int = 64) -> dict:
        """BFS ab `entry` ueber die statischen Nachfolger (intra-funktional, ohne Callees),
        bis `max_blocks` erreicht ist. Liefert Reihenfolge + je Block den Python-Quelltext."""
        start = int(entry)
        seen, order, queue, blocks = set(), [], [start], {}
        while queue and len(order) < int(max_blocks):
            a = queue.pop(0)
            if a in seen:
                continue
            seen.add(a); order.append(a)
            r = self.lift_block(eng, a)
            blocks[f"0x{a:x}"] = r
            if r.get("ok"):
                for s in r.get("successors", []):
                    sa = int(s, 16)
                    if sa not in seen:
                        queue.append(sa)
        return {"ok": True, "entry": f"0x{start:x}", "count": len(order),
                "order": [f"0x{a:x}" for a in order], "blocks": blocks,
                "truncated": bool(queue)}
