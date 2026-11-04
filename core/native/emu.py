"""emu — Emulator-Gerüst (M1-Skelett).

Bindet die iface-Backends zusammen und trägt die Plugin-Registrierung,
Coverage und Listener-Events. Die eigentliche Instruktionsausführung
(`cpu.step`/JIT-Schnellpfad) wird in einem Folge-Increment aus `arm64mini`
hinter die iface-Backends gezogen (WS-E1). Bis dahin ist der Kontrakt
testbar: Laden, Plugin-Haken, strukturierte Unknown-Instr, Coverage, Events.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import iface
from .iface import (CpuState, CoverageReport, Insn, MemoryBackend, Decoder,
                    Environment, FakeEnvironment, SyscallHandler, TargetPlugin,
                    EmulatorABC, UnknownInstructionError, Listener, null_listener,
                    EV_STEP, EV_UNKNOWN_INSTR, EV_PROGRESS)
from .loader import ElfImage


class Emulator(EmulatorABC):
    def __init__(self, mem: MemoryBackend, *, decoder: Optional[Decoder] = None,
                 env: Optional[Environment] = None,
                 syscalls: Optional[SyscallHandler] = None,
                 listener: Listener = null_listener):
        self.mem = mem
        self.cpu = CpuState()
        self.decoder = decoder
        self.env = env or FakeEnvironment()
        self.syscalls = syscalls
        self.listener = listener
        self.coverage = CoverageReport()
        self.image: Optional[ElfImage] = None
        self.plugins: List[TargetPlugin] = []
        self._hooks: Dict[int, Callable[["Emulator"], None]] = {}
        self._stop = False

    # ---- Aufbau -----------------------------------------------------------
    def load_image(self, image: ElfImage) -> None:
        """Segmente ins Mapping legen (Ausführung/Relocation: Folge-Increment)."""
        self.image = image
        for seg in image.segments:
            perms = 0
            if seg.flags & 0x4: perms |= iface.PERM_R
            if seg.flags & 0x2: perms |= iface.PERM_W
            if seg.flags & 0x1: perms |= iface.PERM_X
            self.mem.map(seg.vaddr, max(seg.memsz, 1), perms or iface.PERM_R)
            if seg.filesz:
                self.mem.write(seg.vaddr, image.data[seg.offset:seg.offset + seg.filesz])
        self.cpu.pc = image.entry
        self.listener(EV_PROGRESS, {"stage": "loaded", "segments": len(image.segments)})

    def attach_plugin(self, plugin: TargetPlugin) -> None:
        self.plugins.append(plugin)
        self._hooks.update(plugin.address_hooks())
        if plugin.syscalls() is not None:
            self.syscalls = plugin.syscalls()
        plugin.on_load(self)
        self.listener(EV_PROGRESS, {"stage": "plugin", "name": plugin.name,
                                    "hooks": len(plugin.address_hooks())})

    # ---- Ausführung -------------------------------------------------------
    def step(self) -> Insn:
        pc = self.cpu.pc
        # Plugin-Hook an dieser Adresse? -> Python übernimmt (Leaf/Stub).
        hook = self._hooks.get(pc)
        if hook is not None:
            hook(self)
            self.coverage.record("<hook>")
            self.listener(EV_STEP, {"pc": pc, "hook": True})
            return Insn(pc=pc, raw=0, mnemonic="<hook>")

        raw_bytes = self.mem.read(pc, 4)
        raw = int.from_bytes(raw_bytes, "little")
        if self.decoder is None:
            # Noch kein Dekoder angeschlossen -> ehrliche, strukturierte Meldung.
            self.coverage.record_unknown(pc, raw)
            self.listener(EV_UNKNOWN_INSTR, {"pc": pc, "raw": raw, "reason": "no_decoder"})
            raise UnknownInstructionError(pc, raw, encoding="no decoder attached")

        insn = self.decoder.decode(raw, pc)
        # Ausführung folgt im nächsten Increment; bis dahin: Coverage + Event.
        self.coverage.record(insn.mnemonic or "<decoded>")
        self.listener(EV_STEP, {"pc": pc, "raw": raw, "mnemonic": insn.mnemonic})
        self.cpu.pc = pc + insn.size
        return insn

    def run(self, max_steps: int = 1_000_000) -> int:
        self._stop = False
        n = 0
        while n < max_steps and not self._stop:
            self.step()
            n += 1
        return n

    def stop(self) -> None:
        self._stop = True
