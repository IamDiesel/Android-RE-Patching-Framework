"""snapshot — Snapshot-Gerüst (M1-Skelett).

Friert den initialisierten Emulator-Zustand ein (Speicherseiten, CPU, später
Decode-Cache), damit teure Einmal-Konstruktion nicht pro Lauf wiederholt wird
(vgl. lipy state_prov_c0). M1 definiert nur den Vertrag + eine einfache
CPU-Serialisierung; die page-sparse Speicher-Serialisierung und der Decode-
Cache folgen mit dem Emulator-Port.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .iface import CpuState


@dataclass
class Snapshot:
    cpu: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    # memory/decode-cache: Folge-Increment (page-sparse Serialisierung)

    @classmethod
    def capture(cls, emu, meta: Dict[str, Any] | None = None) -> "Snapshot":
        c = emu.cpu
        return cls(cpu=dict(x=list(c.x), sp=c.sp, pc=c.pc, nzcv=c.nzcv, v=list(c.v)),
                   meta=dict(meta or {}))

    def restore_into(self, emu) -> None:
        c = CpuState(x=list(self.cpu["x"]), sp=self.cpu["sp"], pc=self.cpu["pc"],
                     nzcv=self.cpu["nzcv"], v=list(self.cpu["v"]))
        emu.cpu = c
