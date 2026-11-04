"""native_emu_service — EventBus-faehige Service-Naht um die generische Arm64Engine.

Phase A / A0: fuehrt eine native Funktion **headless** aus (run-to-completion) und
liefert einen Zustands-Snapshot (Register/Flags), ohne GUI. Lange Laeufe laufen
beim Aufrufer (Controller) im Daemon-Thread; dieser Service publiziert
Fortschritt/Ergebnis ueber den **EventBus** (README §1 SOA/EventBus) und schreibt
selbst nie ins UI.

Stateless: der Aufrufer haelt die Engine-Instanz (fuer spaeteres Step/Inspect in
A2); der Service kapselt nur Engine-Operationen + Event-Emission. IP-neutral —
kein LLU-Bezug; die Zielspezifik kommt spaeter als iface.TargetPlugin (Phase B).
"""
from __future__ import annotations

from typing import Optional, Sequence

from core.native.engine_arm64 import Arm64Engine
from core.native import iface

try:
    from core.application.event_bus import EventBus
except Exception:  # headless/Teststand ohne App
    EventBus = None

# EventBus-Ereignisse (vgl. restudio-generalisierung.md §8)
NATIVE_EMU_PROGRESS = "NATIVE_EMU_PROGRESS"
NATIVE_EMU_DONE = "NATIVE_EMU_DONE"
NATIVE_UNKNOWN_INSTR = "NATIVE_UNKNOWN_INSTR"
NATIVE_EMU_ERROR = "NATIVE_EMU_ERROR"

_M64 = (1 << 64) - 1


class NativeEmuService:
    """Kapselt Laden + Ausfuehren + Snapshot der Arm64Engine, EventBus-faehig."""

    def __init__(self, publish: bool = True):
        self._publish = publish

    def _emit(self, ev: str, data) -> None:
        if self._publish and EventBus is not None:
            try:
                EventBus.publish(ev, data)
            except Exception:
                pass  # EventBus darf einen Lauf nie sprengen

    # ---- Engine / Laden ---------------------------------------------------
    def new_engine(self) -> Arm64Engine:
        return Arm64Engine()

    def load_image(self, eng: Arm64Engine, image) -> None:
        """ElfImage (loader.ElfImage) in die Engine laden (Segmente + Permissions)."""
        eng.load_image(image)

    def load_blob(self, eng: Arm64Engine, base: int, code: bytes) -> None:
        """Rohbytes als ausfuehrbares Segment laden (fuer Tests/Mikro-Laeufe)."""
        eng.load_blob(int(base), bytes(code))

    # ---- Eingaben ---------------------------------------------------------
    def resolve_entry(self, entry, image=None) -> int:
        """entry = int | '0x..' | 'sub_..' | Dezimal | Symbolname (ueber image.dynsyms)."""
        if isinstance(entry, int):
            return entry
        s = str(entry).strip()
        if image is not None and getattr(image, "dynsyms", None) and s in image.dynsyms:
            return int(image.dynsyms[s].value)
        low = s.lower()
        if low.startswith("0x"):
            return int(low, 16)
        if low.startswith("sub_"):
            return int(low[4:], 16)
        if s.isdigit():
            return int(s)
        raise ValueError(f"entry nicht aufloesbar: {entry!r}")

    def apply_mem(self, eng: Arm64Engine, mem_overrides) -> None:
        """mem_overrides: Liste {addr:int|'0x..', bytes:bytes|hexstr} — Speicher-Vorbelegung."""
        for ov in (mem_overrides or []):
            a = ov["addr"]
            addr = int(a, 0) if isinstance(a, str) else int(a)
            data = ov.get("bytes", b"")
            if isinstance(data, str):
                data = bytes.fromhex(data)
            eng.mem.write(addr, bytes(data))

    # ---- Ausfuehrung (run-to-completion, A0) ------------------------------
    def run(self, eng: Arm64Engine, entry, args: Sequence[int] = (),
            mem_overrides=None, sp: Optional[int] = None,
            maxins: int = 2_000_000, image=None) -> dict:
        """Fuehrt ab `entry` aus und liefert {ok, ret, x[], sp, lr, pc, flags}.
        Publiziert PROGRESS(start) / DONE bzw. UNKNOWN_INSTR / ERROR."""
        try:
            addr = self.resolve_entry(entry, image)
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, "why": "entry", "error": repr(e)}
        try:
            self.apply_mem(eng, mem_overrides)
            if sp is not None:
                eng.cpu.sp = int(sp)
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, "why": "setup", "error": repr(e)}

        self._emit(NATIVE_EMU_PROGRESS,
                   {"stage": "start", "entry": f"0x{addr:x}", "args": [int(a) & _M64 for a in args]})
        try:
            ret = eng.run_function(addr, tuple(int(a) & _M64 for a in args), maxins)
        except iface.UnknownInstructionError as e:
            d = e.to_dict() if hasattr(e, "to_dict") else {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None)}
            self._emit(NATIVE_UNKNOWN_INSTR, d)
            return {"ok": False, "why": "unknown_instr", **d}
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, "why": "error", "error": repr(e)}

        snap = self.snapshot_regs(eng)
        self._emit(NATIVE_EMU_DONE, {"ret": int(ret) & _M64})
        return {"ok": True, "ret": int(ret) & _M64, **snap}

    # ---- Beobachtung (billige Reads fuer die Panes) -----------------------
    def snapshot_regs(self, eng: Arm64Engine) -> dict:
        c = eng.cpu
        return {
            "x": [int(c.x[i]) & _M64 for i in range(31)],
            "sp": int(c.sp) & _M64, "lr": int(c.lr) & _M64, "pc": int(getattr(c, "pc", 0)) & _M64,
            "flags": {"N": int(c.n), "Z": int(c.z), "C": int(c.c), "V": int(c.vf)},
        }

    def read_mem(self, eng: Arm64Engine, addr: int, n: int) -> bytes:
        return bytes(eng.mem.read(int(addr), int(n)))
