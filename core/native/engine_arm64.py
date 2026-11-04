"""engine_arm64 — Adapter, der die generische `arm64mini`-Engine hinter die
iface-Verträge zieht (B2).

`arm64mini` ist ein erprobter, zielagnostischer AArch64-Nutzermodus-Interpreter
(Block-JIT + capstone-Dekodierung + NEON/Crypto-Ext). Hier wird er NICHT neu
geschrieben, sondern über dünne Adapter angebunden:

  Arm64Memory   -> iface.MemoryBackend  (Mem mit optionalem Perms-Overlay, WS-E2)
  Arm64Decoder  -> iface.Decoder        (capstone disasm_lite -> iface.Insn, WS-E1)
  Arm64Engine   -> Ausführungs-Adapter  (run_function, TargetPlugin->stub, probes)

Zonen-Policy (A1, native-studio-impl.md): WELCHE `.so` geladen wird, entscheidet
die Service-Schicht (Original/Analyse-Arbeitsbereich) — dieser Adapter ist
pfad-neutral.
"""
from __future__ import annotations

import importlib.util
import os
from typing import Callable, List, Optional

from . import iface
from .iface import (CoverageReport, Insn, Listener, null_listener,
                    TargetPlugin, UnknownInstructionError, EV_PROGRESS)
from .loader import ElfImage

_PF_X, _PF_W, _PF_R = 0x1, 0x2, 0x4


def _load_arm64mini():
    """arm64mini neben diesem Modul laden (vendored core/native/arm64mini.py)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "arm64mini.py")
    spec = importlib.util.spec_from_file_location("core.native._arm64mini", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_AM = None


def _am():
    global _AM
    if _AM is None:
        _AM = _load_arm64mini()
    return _AM


# ---------------------------------------------------------------------------
class Arm64Memory(iface.MemoryBackend):
    """iface.MemoryBackend über arm64minis seitenbasiertes `Mem`.

    arm64mini erzwingt keine Permissions (Tempo); wir führen ein optionales
    Perms-Overlay pro Seite, damit das Native Studio W^X/mprotect anzeigen und
    spätere Fault-Checks (WS-E2) darauf aufsetzen können — ohne die Engine zu
    bremsen (Overlay wird im heißen Pfad nicht konsultiert).
    """
    PAGE = 0x1000

    def __init__(self, mem=None):
        self.m = mem if mem is not None else _am().Mem()
        self._perms = {}  # page_base -> perms

    def map(self, addr, size, perms=iface.PERM_RW):
        base = addr & ~(self.PAGE - 1)
        end = (addr + size + self.PAGE - 1) & ~(self.PAGE - 1)
        for p in range(base, end, self.PAGE):
            self._perms[p] = perms

    def protect(self, addr, size, perms):
        self.map(addr, size, perms)

    def perms(self, addr):
        return self._perms.get(addr & ~(self.PAGE - 1), iface.PERM_RWX)

    def read(self, addr, size):
        return self.m.rb(addr, size)

    def write(self, addr, data):
        self.m.wb(addr, bytes(data))


# ---------------------------------------------------------------------------
class Arm64Decoder(iface.Decoder):
    """capstone-Dekoder -> iface.Insn (für die Disassembly-Ansicht, WS-E1)."""

    def __init__(self):
        import capstone
        arch = getattr(capstone, "CS_ARCH_ARM64", None)
        if arch is None:
            arch = getattr(capstone, "CS_ARCH_AARCH64")
        mode = getattr(capstone, "CS_MODE_LITTLE_ENDIAN", 0)
        self._md = capstone.Cs(arch, mode)

    def decode(self, raw, pc):
        code = (int(raw) & 0xFFFFFFFF).to_bytes(4, "little")
        for addr, size, mnem, ops in self._md.disasm_lite(code, pc):
            return Insn(pc=pc, raw=int(raw) & 0xFFFFFFFF,
                        mnemonic=mnem, op_str=ops, size=size or 4)
        raise UnknownInstructionError(pc, int(raw) & 0xFFFFFFFF,
                                      encoding="capstone: not decodable")


# ---------------------------------------------------------------------------
class Arm64Engine:
    """Ausführungs-Adapter um `arm64mini.CPU` (reale Ausführung, B2).

    Produktionspfad = `run_function` (Block-JIT, bit-exakt). Plugin-Adress-Hooks
    werden in arm64minis `stub`-Mechanismus eingehängt; Beobachtung über `probes`.
    """

    def __init__(self, listener: Listener = null_listener):
        self.mem = Arm64Memory()
        self.cpu = _am().CPU(self.mem.m)
        self.listener = listener
        self.coverage = CoverageReport()
        self.plugins: List[TargetPlugin] = []
        self.decoder = Arm64Decoder  # Klasse; lazy-Instanz bei Bedarf
        self._dec = None

    # ---- Laden ------------------------------------------------------------
    def load_blob(self, addr: int, data: bytes, perms: int = iface.PERM_RX):
        self.mem.write(addr, data)
        self.mem.map(addr, len(data), perms)

    def load_image(self, image: ElfImage):
        for seg in image.segments:
            perms = 0
            if seg.flags & _PF_R: perms |= iface.PERM_R
            if seg.flags & _PF_W: perms |= iface.PERM_W
            if seg.flags & _PF_X: perms |= iface.PERM_X
            if seg.filesz:
                self.mem.write(seg.vaddr, image.data[seg.offset:seg.offset + seg.filesz])
            self.mem.map(seg.vaddr, max(seg.memsz, 1), perms or iface.PERM_R)
        self.listener(EV_PROGRESS, {"stage": "loaded", "segments": len(image.segments)})

    # ---- Plugins / Beobachtung -------------------------------------------
    def attach_plugin(self, plugin: TargetPlugin):
        for addr, hook in plugin.address_hooks().items():
            self.cpu.stub[addr] = (lambda h: (lambda cpu: h(self)))(hook)
        plugin.on_load(self)
        self.plugins.append(plugin)
        self.listener(EV_PROGRESS, {"stage": "plugin", "name": plugin.name})

    def set_probe(self, pc: int, fn: Callable):
        """Nicht-invasiver Beobachtungs-Hook (läuft vor der Instruktion an pc)."""
        self.cpu.probes[pc] = fn

    def decode_at(self, pc: int) -> Insn:
        if self._dec is None:
            self._dec = Arm64Decoder()
        raw = self.mem.read(pc, 4)
        return self._dec.decode(int.from_bytes(raw, "little"), pc)

    # ---- Ausführung -------------------------------------------------------
    def run_function(self, entry: int, args=(), maxins: int = 2_000_000_000) -> int:
        ret = self.cpu.run(entry, tuple(args), maxins)
        self.listener(EV_PROGRESS, {"stage": "ran", "entry": entry, "ret": ret})
        return ret

    # bequeme Register-Sicht
    def reg(self, i: int) -> int:
        return self.cpu.x[i]
