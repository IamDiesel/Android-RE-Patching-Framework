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
NATIVE_EMU_BREAK = "NATIVE_EMU_BREAK"


class BreakpointHit(Exception):
    """Wird von einem Breakpoint-Probe geworfen, um cpu.run sauber anzuhalten (A2)."""

    def __init__(self, pc):
        super().__init__("breakpoint@0x%x" % pc)
        self.pc = pc

_M64 = (1 << 64) - 1


def _classify_run_exc(e):
    """Klassifiziert eine Run-Ausnahme fuer das Ergebnis-Dict.

    'maxins@0x..' / 'timeout@0x..' / 'stall@0x..' sind KEINE Abstuerze, sondern kontrollierte
    Budgets/Limits -> why='maxins'|'timeout'|'stall' mit Zieladresse (at/pc).
    Alles andere bleibt why='error'. Rein additiv; aendert keinen Erfolgspfad."""
    s = repr(e)
    for tag, why in (("maxins@", "maxins"), ("timeout@", "timeout"), ("stall@", "stall")):
        if tag in s:
            pc = None
            try:
                frag = s.split(tag, 1)[1]
                if frag[:2] in ("0x", "0X"):
                    hexs = "0x"
                    for ch in frag[2:]:
                        if ch in "0123456789abcdefABCDEF":
                            hexs += ch
                        else:
                            break
                    pc = int(hexs, 16)
            except Exception:
                pc = None
            d = {"why": why, "error": s}
            if pc is not None:
                d["at"] = f"0x{pc:x}"
                d["pc"] = pc & _M64
            return d
    return {"why": "error", "error": s}
_ARENA_BASE = 0x70000000  # Scratch-Arena fuer Pointer/Buffer-Argumente (weit ueber den Lade-Segmenten)


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

    def load_image(self, eng: Arm64Engine, image, apply_relocs: bool = False,
                   load_bias: int = 0) -> int:
        """ElfImage in die Engine laden (Segmente + Permissions). apply_relocs=True wendet
        zusaetzlich die Relocations an (fuellt Dispatch-/Call-Zeiger obfuskierter Libs) —
        Default aus, damit der WB-Krypto-Pfad bit-exakt bleibt. Rueckgabe: Anzahl Relocs."""
        if int(load_bias):
            eng.load_image(image, load_bias=int(load_bias))
        else:
            eng.load_image(image)
        if apply_relocs:
            try:
                if int(load_bias):
                    return eng.apply_relocations(image, load_bias=int(load_bias))
                return eng.apply_relocations(image)
            except Exception:
                return 0
        return 0

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
            maxins: int = 2_000_000, image=None,
            max_wall_s: Optional[float] = None) -> dict:
        """Fuehrt ab `entry` aus und liefert {ok, ret, x[], sp, lr, pc, flags}.
        Publiziert PROGRESS(start) / DONE bzw. UNKNOWN_INSTR / ERROR."""
        depth = int(getattr(eng, "_run_depth", 0) or 0)
        saved = None
        if depth > 0:
            c = eng.cpu
            saved = (
                list(c.x),
                int(c.sp),
                int(c.lr),
                int(getattr(c, "pc", 0) or 0),
                int(c.n),
                int(c.z),
                int(c.c),
                int(c.vf),
                bool(getattr(c, "stopped", False)),
                getattr(c, "stop_reason", None),
            )
        eng._run_depth = depth + 1
        try:
            try:
                addr = self.resolve_entry(entry, image)
            except Exception as e:
                self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
                return {"ok": False, "why": "entry", "error": repr(e)}
            try:
                self.apply_mem(eng, mem_overrides)
                if sp is not None:
                    eng.cpu.sp = int(sp)
                elif depth > 0:
                    cur_sp = int(eng.cpu.sp) if int(eng.cpu.sp) else 0x7F000000
                    eng.cpu.sp = (cur_sp - 0x4000) & ~0xF
                elif eng.cpu.sp == 0:
                    eng.cpu.sp = 0x7F000000
            except Exception as e:
                self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
                return {"ok": False, "why": "setup", "error": repr(e)}

            try:
                int_args, outbufs = self.materialize_args(eng, args)
            except Exception as e:
                self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
                return {"ok": False, "why": "args", "error": repr(e)}

            self._emit(NATIVE_EMU_PROGRESS,
                       {"stage": "start", "entry": f"0x{addr:x}", "args": [a & _M64 for a in int_args]})
            try:
                if max_wall_s is not None:
                    ret = eng.run_function(addr, tuple(int_args), maxins, max_wall_s=max_wall_s)
                else:
                    ret = eng.run_function(addr, tuple(int_args), maxins)
            except iface.UnknownInstructionError as e:
                # Einheitliche Felder fuer Coverage-Loop + GUI (pc/raw/mnem/encoding).
                d = {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None),
                     "mnem": getattr(e, "mnemonic", None), "encoding": getattr(e, "encoding", "")}
                self._emit(NATIVE_UNKNOWN_INSTR, d)
                return {"ok": False, "why": "unknown_instr", **d}
            except Exception as e:
                self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
                return {"ok": False, **_classify_run_exc(e)}

            stopped = bool(getattr(eng.cpu, "stopped", False))
            stop_reason = getattr(eng.cpu, "stop_reason", None)
            if stopped and str(stop_reason or "") == "user_stop":
                pc_now = int(getattr(eng.cpu, "pc", 0) or 0) & _M64
                self._emit(NATIVE_EMU_ERROR, {"error": "user_stop", "pc": pc_now})
                return {"ok": False, "why": "stopped", "pc": pc_now,
                        "stopped": True, "stop_reason": "user_stop"}

            snap = self.snapshot_regs(eng)
            out = {}
            for name, a, n in outbufs:
                try:
                    out[name] = self.read_mem(eng, a, n).hex()
                except Exception:
                    pass
            self._emit(NATIVE_EMU_DONE, {"ret": int(ret) & _M64})
            res = {"ok": True, "ret": int(ret) & _M64, **snap}
            if stopped and not str(stop_reason or "").startswith("futex_stall"):
                res["stopped"] = True
                res["stop_reason"] = stop_reason or "stopped"
            if out:
                res["out"] = out
            return res
        finally:
            if saved is not None:
                c = eng.cpu
                sx, ssp, slr, spc, sn, sz, sc, svf, sstopped, sreason = saved
                c.x[:] = sx
                c.sp = ssp
                c.lr = slr
                c.pc = spc
                c.n, c.z, c.c, c.vf = sn, sz, sc, svf
                c.stopped = sstopped
                c.stop_reason = sreason
            eng._run_depth = depth

    # ---- Ausfuehrung mit Breakpoints (A2, kontraktneutral) ----------------
    def run_with_breaks(self, eng: Arm64Engine, entry, args: Sequence[int] = (),
                        mem_overrides=None, sp: Optional[int] = None,
                        maxins: int = 2_000_000, breakpoints=(), image=None,
                        max_wall_s: Optional[float] = None) -> dict:
        """Wie run(), haelt aber VOR einer gesetzten Breakpoint-Adresse an und liefert den
        Pause-Zustand. Nutzt ausschliesslich den additiven engine.set_probe-Hook — der
        eingefrorene cpu.run bleibt unberuehrt. Rueckgabe zusaetzlich: paused(bool)+bp(pc)."""
        try:
            addr = self.resolve_entry(entry, image)
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, "why": "entry", "error": repr(e)}
        try:
            self.apply_mem(eng, mem_overrides)
            if sp is not None:
                eng.cpu.sp = int(sp)
            int_args, outbufs = self.materialize_args(eng, args)
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, "why": "setup", "error": repr(e)}

        bps = []
        for b in (breakpoints or []):
            try:
                bps.append(self.resolve_entry(b, image))
            except Exception:
                pass

        def _mk(pc):
            def _p(cpu):
                raise BreakpointHit(pc)
            return _p
        for pc in bps:
            eng.set_probe(pc, _mk(pc))

        self._emit(NATIVE_EMU_PROGRESS,
                   {"stage": "start", "entry": f"0x{addr:x}", "breakpoints": [f"0x{p:x}" for p in bps]})
        try:
            if max_wall_s is not None:
                ret = eng.run_function(addr, tuple(int_args), maxins, max_wall_s=max_wall_s)
            else:
                ret = eng.run_function(addr, tuple(int_args), maxins)
        except BreakpointHit as bh:
            snap = self.snapshot_regs(eng)
            self._emit(NATIVE_EMU_BREAK, {"pc": f"0x{bh.pc:x}"})
            return {"ok": True, "paused": True, "bp": int(bh.pc) & _M64, **snap}
        except iface.UnknownInstructionError as e:
            # Einheitliche Felder fuer Coverage-Loop + GUI (pc/raw/mnem/encoding).
            d = {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None),
                 "mnem": getattr(e, "mnemonic", None), "encoding": getattr(e, "encoding", "")}
            self._emit(NATIVE_UNKNOWN_INSTR, d)
            return {"ok": False, "why": "unknown_instr", **d}
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, **_classify_run_exc(e)}
        finally:
            for pc in bps:
                try:
                    del eng.cpu.probes[pc]
                except Exception:
                    pass

        snap = self.snapshot_regs(eng)
        out = {}
        for name, a, n in outbufs:
            try:
                out[name] = self.read_mem(eng, a, n).hex()
            except Exception:
                pass
        self._emit(NATIVE_EMU_DONE, {"ret": int(ret) & _M64})
        res = {"ok": True, "paused": False, "ret": int(ret) & _M64, **snap}
        if out:
            res["out"] = out
        return res

    # ---- Step / Resume (A2) -----------------------------------------------
    _SENT = 0xdead0000

    def step(self, eng: Arm64Engine) -> dict:
        """Fuehrt genau EINEN Basisblock/Stub am aktuellen pc aus (expliziter Schritt,
        ignoriert Breakpoints). Rueckgabe: {ok, done(bool), **regs}."""
        try:
            pc = eng.cpu.step_block()
        except iface.UnknownInstructionError as e:
            # Einheitliche Felder fuer Coverage-Loop + GUI (pc/raw/mnem/encoding).
            d = {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None),
                 "mnem": getattr(e, "mnemonic", None), "encoding": getattr(e, "encoding", "")}
            self._emit(NATIVE_UNKNOWN_INSTR, d)
            return {"ok": False, "why": "unknown_instr", **d}
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, **_classify_run_exc(e)}
        done = (int(pc) == self._SENT)
        snap = self.snapshot_regs(eng)
        res = {"ok": True, "done": done, **snap}
        if done:
            res["ret"] = int(eng.cpu.x[0]) & _M64
            self._emit(NATIVE_EMU_DONE, {"ret": res["ret"]})
        return res

    def resume(self, eng: Arm64Engine, breakpoints=(), maxins: int = 2_000_000, image=None) -> dict:
        """Setzt am aktuellen pc fort bis zum naechsten Breakpoint oder bis zum Ende.
        Springt zuerst EINEN Schritt ueber den aktuellen pc (sonst wuerde ein dort gesetzter
        Breakpoint sofort wieder ausloesen); nutzt cpu.run_from (additiv)."""
        if int(eng.cpu.pc) == self._SENT:
            return {"ok": True, "paused": False, "done": True,
                    "ret": int(eng.cpu.x[0]) & _M64, **self.snapshot_regs(eng)}
        bps = []
        for b in (breakpoints or []):
            try:
                bps.append(self.resolve_entry(b, image))
            except Exception:
                pass
        # 1) ein Schritt ueber den aktuellen pc
        try:
            pc = eng.cpu.step_block()
        except iface.UnknownInstructionError as e:
            # Einheitliche Felder fuer Coverage-Loop + GUI (pc/raw/mnem/encoding).
            d = {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None),
                 "mnem": getattr(e, "mnemonic", None), "encoding": getattr(e, "encoding", "")}
            self._emit(NATIVE_UNKNOWN_INSTR, d)
            return {"ok": False, "why": "unknown_instr", **d}
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, **_classify_run_exc(e)}
        if int(pc) == self._SENT:
            self._emit(NATIVE_EMU_DONE, {"ret": int(eng.cpu.x[0]) & _M64})
            return {"ok": True, "paused": False, "done": True,
                    "ret": int(eng.cpu.x[0]) & _M64, **self.snapshot_regs(eng)}
        # 2) Breakpoints setzen und weiterlaufen
        def _mk(p):
            def _f(cpu):
                raise BreakpointHit(p)
            return _f
        for p in bps:
            eng.set_probe(p, _mk(p))
        self._emit(NATIVE_EMU_PROGRESS, {"stage": "resume", "pc": f"0x{int(pc):x}"})
        try:
            ret = eng.cpu.run_from(maxins)
        except BreakpointHit as bh:
            self._emit(NATIVE_EMU_BREAK, {"pc": f"0x{bh.pc:x}"})
            return {"ok": True, "paused": True, "bp": int(bh.pc) & _M64, **self.snapshot_regs(eng)}
        except iface.UnknownInstructionError as e:
            # Einheitliche Felder fuer Coverage-Loop + GUI (pc/raw/mnem/encoding).
            d = {"pc": getattr(e, "pc", None), "raw": getattr(e, "raw", None),
                 "mnem": getattr(e, "mnemonic", None), "encoding": getattr(e, "encoding", "")}
            self._emit(NATIVE_UNKNOWN_INSTR, d)
            return {"ok": False, "why": "unknown_instr", **d}
        except Exception as e:
            self._emit(NATIVE_EMU_ERROR, {"error": repr(e)})
            return {"ok": False, **_classify_run_exc(e)}
        finally:
            for p in bps:
                try:
                    del eng.cpu.probes[p]
                except Exception:
                    pass
        self._emit(NATIVE_EMU_DONE, {"ret": int(ret) & _M64})
        return {"ok": True, "paused": False, "done": True, "ret": int(ret) & _M64, **self.snapshot_regs(eng)}

    # ---- Scratch-Arena + Argument-Materialisierung (Pointer/Buffer) -------
    def alloc(self, eng: Arm64Engine, data: bytes = b"", size: Optional[int] = None, align: int = 16) -> int:
        """Reserviert einen Scratch-Bereich im Emu-Speicher (Bump-Allokator je Engine),
        schreibt optional `data` und markiert ihn RW. Rueckgabe = Startadresse."""
        cur = getattr(eng, "_emu_arena", None)
        if cur is None:
            cur = _ARENA_BASE
        cur = (cur + align - 1) & ~(align - 1)
        data = bytes(data or b"")
        n = size if size is not None else len(data)
        if data:
            eng.mem.write(cur, data)
        try:
            eng.mem.map(cur, max(n, 1), iface.PERM_RW)
        except Exception:
            pass
        eng._emu_arena = cur + max(n, align)
        return cur

    def materialize_args(self, eng: Arm64Engine, args):
        """Wandelt gemischte Argumente in Integer-Register-Werte.
        Jedes Arg: int | '0x..'/dez-String | dict:
          {'int': v}              -> Zahl
          {'ptr'|'bytes': b/hex}  -> Bytes in Arena, Adresse uebergeben (+'out':True -> nach Lauf lesen)
          {'buf': n}              -> n Null-Bytes (Out-Puffer), Adresse uebergeben, nach Lauf lesen
        Rueckgabe: (int_args, outbufs=[(name, addr, size), ...])."""
        ints, outbufs = [], []
        for i, a in enumerate(args or ()):
            if isinstance(a, dict):
                if "int" in a:
                    ints.append(int(a["int"]) & _M64)
                elif "ptr" in a or "bytes" in a:
                    data = a.get("ptr", a.get("bytes"))
                    if isinstance(data, str):
                        data = bytes.fromhex(data.replace(" ", ""))
                    data = bytes(data)
                    addr = self.alloc(eng, data)
                    ints.append(addr)
                    if a.get("out"):
                        outbufs.append((a.get("name", f"arg{i}"), addr, len(data)))
                elif "buf" in a:
                    n = int(a["buf"])
                    addr = self.alloc(eng, b"\x00" * n)
                    ints.append(addr)
                    outbufs.append((a.get("name", f"arg{i}"), addr, n))
                else:
                    ints.append(0)
            elif isinstance(a, str):
                ints.append(int(a, 0) & _M64)
            else:
                ints.append(int(a) & _M64)
        return ints, outbufs

    # ---- Stubs (Imports/Funktionen durch Python ersetzen) -----------------
    def install_stubs(self, eng: Arm64Engine, stubs, image=None):
        """stubs: Liste {'at': addr|'0x..'|Symbol, 'ret': int|None}. Ersetzt die Funktion:
        setzt optional x0=ret und kehrt ueber lr zurueck. Rueckgabe: gesetzte Adressen."""
        installed = []
        for spc in (stubs or []):
            try:
                addr = self.resolve_entry(spc.get("at"), image)
            except Exception:
                continue
            r = spc.get("ret")

            def _mk(rr):
                def _fn(cpu):
                    if rr is not None:
                        cpu.x[0] = int(rr) & _M64
                    return None  # -> Rueckkehr ueber lr
                return _fn
            eng.cpu.stub[addr] = _mk(r)
            installed.append(addr)
        return installed

    # ---- Snapshot (Mem-Seiten + Register) ---------------------------------
    def save_snapshot(self, eng: Arm64Engine, path: str) -> str:
        """Speichert Mem-Seiten + Register nach `path` (lzma-komprimiertes pickle)."""
        import pickle, lzma
        m = eng.mem.m
        pages = {int(p): bytes(b) for p, b in getattr(m, "pages", {}).items()}
        c = eng.cpu
        regs = {"x": [int(c.x[i]) & _M64 for i in range(31)],
                "sp": int(c.sp) & _M64, "lr": int(c.lr) & _M64}
        with lzma.open(path, "wb", preset=1) as f:
            pickle.dump({"v": 1, "pages": pages, "regs": regs}, f, protocol=4)
        return path

    def load_snapshot(self, eng: Arm64Engine, path: str) -> dict:
        """Stellt Mem-Seiten + Register aus `path` wieder her (invalidiert Block-Caches)."""
        import pickle, lzma
        with lzma.open(path, "rb") as f:
            d = pickle.load(f)
        m = eng.mem.m
        m.pages = {int(p): bytearray(b) for p, b in d.get("pages", {}).items()}
        for cache in ("u32", "u64"):
            if hasattr(m, cache):
                try:
                    setattr(m, cache, {})
                except Exception:
                    pass
        r = d.get("regs", {})
        c = eng.cpu
        for i, v in enumerate(r.get("x", [])[:31]):
            c.x[i] = int(v) & _M64
        if "sp" in r:
            c.sp = int(r["sp"]) & _M64
        if "lr" in r:
            c.lr = int(r["lr"]) & _M64
        try:
            c.cache = {}; c.tcache = {}; c.bb = {}
            if hasattr(m, "_code_pages"):
                m._code_pages = set()
        except Exception:
            pass
        return r

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
