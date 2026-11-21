"""iface — Verträge/Abstraktionen des Native-Analysekerns (Entkopplungs-Seam).

Hier liegt die Grenze zwischen **generischem Kern** (Emulator, Loader, Snapshot)
und **zielspezifischem Glue** (`TargetPlugin`). Der Kern programmiert nur gegen
diese ABCs; konkrete Ziele (White-Box, RASP ...) implementieren sie, ohne dass
der Kern sie kennt.

Abgebildete Work-Streams (restudio-generalisierung.md §4):
  WS-E1 Instruktions-Abdeckung  -> Decoder + UnknownInstructionError + Coverage
  WS-E2 Speicher/MMU            -> MemoryBackend (+ Perm-Flags)
  WS-E3 OS/Prozess (svc/Env)    -> SyscallHandler + Environment (fälschbar)
  WS-E4/E5 Threads/Signale      -> (Hooks am Emulator, spätere Erweiterung)
  WS-E7 Harness-Ergonomie       -> CoverageReport + Listener-Events
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Events: der Kern bleibt EventBus-frei und meldet über einen Callback.
# Die Service-Schicht mappt diese Namen auf die GUI-EventBus-Events
# (NATIVE_EMU_PROGRESS / NATIVE_STEP / NATIVE_UNKNOWN_INSTR / NATIVE_LIFT_DONE).
# ---------------------------------------------------------------------------
EV_PROGRESS = "native.progress"
EV_STEP = "native.step"
EV_UNKNOWN_INSTR = "native.unknown_instr"
EV_LIFT_DONE = "native.lift_done"

Listener = Callable[[str, dict], None]


def null_listener(event: str, payload: dict) -> None:  # pragma: no cover - trivial
    """Default-Listener: verwirft Ereignisse (headless, kein Abonnent)."""
    return None


# ---------------------------------------------------------------------------
# Speicher-Permissions (WS-E2)
# ---------------------------------------------------------------------------
PERM_R = 0b100
PERM_W = 0b010
PERM_X = 0b001
PERM_RW = PERM_R | PERM_W
PERM_RX = PERM_R | PERM_X
PERM_RWX = PERM_R | PERM_W | PERM_X


def perm_str(perms: int) -> str:
    return ("r" if perms & PERM_R else "-") + \
           ("w" if perms & PERM_W else "-") + \
           ("x" if perms & PERM_X else "-")


# ---------------------------------------------------------------------------
# Strukturierte Fehler + Coverage (WS-E1 / WS-E7 / FR-4)
# ---------------------------------------------------------------------------
class NativeError(Exception):
    """Basisklasse aller Kern-Fehler."""


class UnknownInstructionError(NativeError):
    """Eine (noch) nicht implementierte Instruktion — nie stiller Abbruch.

    Trägt genug Kontext, um den „unknown-instr -> implement"-Loop zu bedienen:
    Programmzähler, Rohwort, optionale Dekodierung.
    """

    def __init__(self, pc: int, raw: int, encoding: str = "", mnemonic: str = ""):
        self.pc = int(pc)
        self.raw = int(raw) & 0xFFFFFFFF
        self.encoding = encoding
        self.mnemonic = mnemonic
        super().__init__(str(self))

    def __str__(self) -> str:
        m = f" {self.mnemonic}" if self.mnemonic else ""
        e = f" [{self.encoding}]" if self.encoding else ""
        return f"unknown instruction @0x{self.pc:x}: raw=0x{self.raw:08x}{m}{e}"

    def as_dict(self) -> dict:
        return {"pc": self.pc, "raw": self.raw,
                "encoding": self.encoding, "mnemonic": self.mnemonic}


class MemoryFault(NativeError):
    """Zugriff verletzt Mapping/Permissions (WS-E2)."""

    def __init__(self, addr: int, size: int, need: int, have: int):
        self.addr, self.size, self.need, self.have = addr, size, need, have
        super().__init__(
            f"memory fault @0x{addr:x}+{size}: need {perm_str(need)}, have {perm_str(have)}")


@dataclass
class CoverageReport:
    """Welche Opcodes liefen, welche fehlen (WS-E7 — Steuergröße der Vervollständigung)."""
    seen: Dict[str, int] = field(default_factory=dict)          # mnemonic -> count
    unknown: List[Tuple[int, int]] = field(default_factory=list)  # (pc, raw)
    steps: int = 0

    def record(self, mnemonic: str) -> None:
        self.seen[mnemonic] = self.seen.get(mnemonic, 0) + 1
        self.steps += 1

    def record_unknown(self, pc: int, raw: int) -> None:
        self.unknown.append((int(pc), int(raw) & 0xFFFFFFFF))

    def merge(self, other: "CoverageReport") -> None:
        for k, v in other.seen.items():
            self.seen[k] = self.seen.get(k, 0) + v
        self.unknown.extend(other.unknown)
        self.steps += other.steps

    def summary(self) -> str:
        uniq = len(self.seen)
        unk = len({r for _, r in self.unknown})
        return (f"steps={self.steps} distinct_opcodes={uniq} "
                f"unknown_raw={unk} unknown_sites={len(self.unknown)}")


# ---------------------------------------------------------------------------
# CPU-Zustand (konkret; WS-E6-Details später)
# ---------------------------------------------------------------------------
@dataclass
class CpuState:
    x: List[int] = field(default_factory=lambda: [0] * 31)  # x0..x30
    sp: int = 0
    pc: int = 0
    nzcv: int = 0                                            # N,Z,C,V in Bits 31..28
    v: List[int] = field(default_factory=lambda: [0] * 32)  # v0..v31 (128-bit als int)

    def clone(self) -> "CpuState":
        return CpuState(x=list(self.x), sp=self.sp, pc=self.pc,
                        nzcv=self.nzcv, v=list(self.v))


# ---------------------------------------------------------------------------
# Backends / Umgebung (ABCs = Entkopplungs-Seam)
# ---------------------------------------------------------------------------
class MemoryBackend(abc.ABC):
    """Adressraum mit Permissions (WS-E2). Implementierung z. B. page-sparse."""

    @abc.abstractmethod
    def map(self, addr: int, size: int, perms: int = PERM_RW) -> None: ...
    @abc.abstractmethod
    def protect(self, addr: int, size: int, perms: int) -> None: ...
    @abc.abstractmethod
    def perms(self, addr: int) -> int: ...
    @abc.abstractmethod
    def read(self, addr: int, size: int) -> bytes: ...
    @abc.abstractmethod
    def write(self, addr: int, data: bytes) -> None: ...


class Decoder(abc.ABC):
    """Instruktionsdekodierung (arch-pluggbar; capstone-Backend später)."""

    @abc.abstractmethod
    def decode(self, raw: int, pc: int) -> "Insn": ...


@dataclass
class Insn:
    pc: int
    raw: int
    mnemonic: str = ""
    op_str: str = ""
    size: int = 4


class Environment(abc.ABC):
    """Kontrollierbar fälschbare Prozess-Umgebung (WS-E3) — zentral für RASP."""

    @abc.abstractmethod
    def proc_self_maps(self) -> str: ...
    @abc.abstractmethod
    def thread_list(self) -> List[dict]: ...
    @abc.abstractmethod
    def clock_ns(self) -> int: ...


class FakeEnvironment(Environment):
    """Default-Umgebung mit vollständig setzbaren Werten — deterministisch (FR-5).

    Timing ist standardmäßig ein monoton wachsender, fixierbarer Zähler, damit
    zeitbasierte RASP-Checks reproduzierbar durchlaufen (NFR-DETERM).
    """

    def __init__(self, maps: str = "", threads: Optional[List[dict]] = None,
                 clock_start_ns: int = 0, clock_step_ns: int = 1000):
        self._maps = maps
        self._threads = list(threads or [])
        self._clock = int(clock_start_ns)
        self._clock_step = int(clock_step_ns)
        self._real_clock = clock_step_ns < 0  # negativer Step => echte Zeit

    def set_maps(self, maps: str) -> None:
        self._maps = maps

    def set_threads(self, threads: List[dict]) -> None:
        self._threads = list(threads)

    def proc_self_maps(self) -> str:
        return self._maps

    def thread_list(self) -> List[dict]:
        return list(self._threads)

    def clock_ns(self) -> int:
        if self._real_clock:
            return time.monotonic_ns()
        now = self._clock
        self._clock += self._clock_step
        return now

    def advance_clock(self, ns: int) -> None:
        """Virtuelle Uhr um ns vorstellen (OS-7: nanosleep ohne echten Sleep)."""
        if not self._real_clock:
            self._clock += max(int(ns), 0)


class SyscallHandler(abc.ABC):
    """svc-Dispatch (WS-E3). Rückgabe True = behandelt, sonst Kern-Default/Fehler."""

    @abc.abstractmethod
    def dispatch(self, num: int, cpu: CpuState, mem: MemoryBackend,
                 env: Environment) -> bool: ...


# ---------------------------------------------------------------------------
# Zielspezifischer Glue: die einzige Andockstelle fürs konkrete Ziel.
# ---------------------------------------------------------------------------
class TargetPlugin(abc.ABC):
    """Vertrag für zielspezifisches Wissen (White-Box, RASP ...).

    Der Kern ruft diese Haken, kennt aber keine Ziel-Interna. So bleibt
    `ale_pure`/WB ein *Beispiel-Plugin*, nicht Teil des Kerns (NFR-ARCH).
    """

    name: str = "target"

    def on_load(self, emu: "EmulatorABC") -> None:
        """Nach dem Laden des Images: Setup, Relocation-Nachsorge, Konstanten."""
        return None

    def address_hooks(self) -> Dict[int, Callable[["EmulatorABC"], None]]:
        """addr -> Python-Hook (z. B. Leaf-Reimpl / Mid-Function-Stub)."""
        return {}

    def entry_points(self) -> Dict[str, int]:
        """benannte Einsprünge (z. B. {'pss_sign': 0x...})."""
        return {}

    def syscalls(self) -> Optional[SyscallHandler]:
        """optionaler zielspezifischer svc-Handler."""
        return None


class EmulatorABC(abc.ABC):
    """Minimaler Emulator-Vertrag, gegen den Plugins/Services programmieren."""

    cpu: CpuState
    mem: MemoryBackend
    coverage: CoverageReport

    @abc.abstractmethod
    def step(self) -> Insn: ...
    @abc.abstractmethod
    def run(self, max_steps: int = 1_000_000) -> int: ...
    @abc.abstractmethod
    def attach_plugin(self, plugin: TargetPlugin) -> None: ...
