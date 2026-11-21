# -*- coding: utf-8 -*-
"""native_os_service — Env/Syscall-Konvergenz fuer die arm64mini-Engine (N2 / WS-E3).

Hintergrund (Spec §C.1): bisher existierten **zwei** Syscall-Pfade nebeneinander —
(1) der pragmatische Live-Pfad ``ffi.install_syscall_hook`` -> ``cpu._svc_hook`` + flache
Policy, und (2) die sauberen, aber ungenutzten ``iface``-Vertraege (``Environment`` /
``SyscallHandler``). Dieser Service **vereint** beide: er setzt ``cpu._svc_hook`` auf einen
Dispatcher, der die ``iface``-Vertraege erstmals im heissen Lauf verdrahtet und eine echte,
deterministische Umgebung (``FakeEnvironment`` + VFS + mmap-Buchhaltung) unter den Lauf zieht.

**Dispatch-Reihenfolge (ein Pfad):**
  1. **Seed-Policy** (``{nr: int | {"ret","zero_arg","zero_len"}}``) — explizite Overrides
     gewinnen; identische Semantik zum alten flachen Hook => **keine Regression**, und die
     Presets ``genuine``/``clear`` (MCP) bleiben wirksam.
  2. **Handler** (``SyscallHandler.dispatch``) — genuine, env/VFS/mmap-gestuetzte Antwort
     fuer alles Nicht-ueberschriebene (Default: ``LinuxArm64SyscallHandler``).
  3. **default_ret** — fuer Unbehandeltes.

SOA (A.0): stateless Service; der Zustand haengt am ``eng`` (``eng._os``), damit GUI/MCP/Tests
dieselbe Sicht haben. ``ffi.install_syscall_hook`` bleibt unveraendert bestehen; dieser Service
ist der neue, uebergeordnete Weg (wer nur eine flache Policy will, nutzt ``seed_policy`` ohne
Handler-Treffer). Engine-Kontrakt bleibt additiv: nur ``cpu._svc_hook`` + ``eng._os``/``eng._svc_log``.

**Scope-Grenze (N2):** kein W^X-Enforcement (N3), keine Threads/Signale (N4), kein
Trap-Crossing (N5 — die RASP-Trap ist nachweislich nicht syscall-getrieben, s.
gate2-syscall-operanden-befund).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from core.native import iface
from core.native.os_linux_arm64 import LinuxArm64SyscallHandler

_M64 = (1 << 64) - 1

# Reservierte mmap-Arena: zwischen Scratch-Arena (0x70000000, B.3) und STUB_BASE (0x7A000000).
MMAP_ARENA = 0x72000000


class _OsState:
    """Am eng gehaltener OS-Zustand (eine Quelle der Wahrheit)."""

    __slots__ = ("env", "vfs", "fds", "next_fd", "mmaps", "mmap_next",
                 "config", "rng", "handler", "seed_policy", "default_ret",
                 "max_log", "log",
                 # OS-Substrat-Erweiterungen (OS-1..OS-9)
                 "syscall_cov", "fs", "brk", "brk_base", "threads", "futexes",
                 "_futex_spins", "next_tid", "signals", "sigmask", "sockets", "next_sock", "net",
                 "auxv", "tls", "syscall_trace", "syscall_policy", "syscall_hooks")

    def __init__(self):
        self.env = None                      # iface.Environment
        self.vfs: Dict[str, bytes] = {}      # Pfad -> Bytes (Legacy; von fs importiert)
        self.fds: Dict[int, dict] = {}       # fd -> {path, data, pos, ...}
        self.next_fd: int = 3
        self.mmaps: List[dict] = []          # [{base,size,prot,fd?}]
        self.mmap_next: int = MMAP_ARENA
        self.config: Dict[str, object] = {
            "uid": 10234, "pid": 1234, "tid": 1234,
            "statbuf_len": 128, "links": {},
            "rand_seed": 0x9E3779B97F4A7C15,
            "unknown_syscall": "fallthrough",   # fallthrough|enosys|zero
        }
        self.rng: int = 0x9E3779B97F4A7C15
        self.handler: Optional[iface.SyscallHandler] = None
        self.seed_policy: Dict[int, object] = {}
        self.default_ret: int = 0
        self.max_log: int = 100000
        self.log: List[dict] = []
        # ---- OS-Substrat-Erweiterungen ----
        self.syscall_cov: Dict[str, int] = {}   # OS-1: name -> count
        self.fs = None                           # OS-2: VFS 2.0 (core.native.vfs.VFS)
        self.brk: int = 0                        # OS-3: aktueller Heap-Break
        self.brk_base: int = 0
        self.threads: Dict[int, dict] = {}       # OS-4: tid -> {name, tls, ...}
        self.futexes: Dict[int, list] = {}       # OS-4: uaddr -> waiters
        self._futex_spins: dict = {}             # OS-4: Stall-Detektor fuer einthreadiges FUTEX_WAIT
        self.next_tid: int = 0
        self.signals: Dict[int, dict] = {}       # OS-6: signum -> {handler, flags, mask}
        self.sigmask: int = 0
        self.sockets: Dict[int, dict] = {}       # OS-8: fd -> socket
        self.next_sock: int = 0
        self.net = None                          # OS-8: virtueller Netz-Stack
        self.auxv: List = []                     # OS-5: auxv-Vektor
        self.tls: int = 0                        # OS-4: TPIDR_EL0
        self.syscall_trace: List[dict] = []      # OS-9: Debugger-/Proben-Zeitachse
        self.syscall_policy: Dict[int, str] = {} # OS-9: nr -> allow|deny|fake|record
        self.syscall_hooks: dict = {}       # G-OS-0: Live-Hooks pre/post


class NativeOsService:
    """Stateless OS-/Syscall-Service. ``publish`` ist fuer optionale Events reserviert."""

    def __init__(self, publish: bool = True):
        self.publish = publish

    # ---- Zustand am eng -------------------------------------------------
    def _st(self, eng) -> _OsState:
        st = getattr(eng, "_os", None)
        if st is None:
            st = _OsState()
            eng._os = st
        return st

    # ---- Umgebung / VFS -------------------------------------------------
    def attach_env(self, eng, env=None, maps: str = "", threads=None,
                   clock_start_ns: int = 0, clock_step_ns: int = 1000):
        """Haengt eine ``Environment`` an ``eng._os.env`` (Default: deterministische
        ``FakeEnvironment``). Rueckgabe: das Env-Objekt."""
        st = self._st(eng)
        if env is None:
            env = iface.FakeEnvironment(maps=maps, threads=threads,
                                        clock_start_ns=clock_start_ns,
                                        clock_step_ns=clock_step_ns)
        st.env = env
        return env

    def vfs_mount(self, eng, files: Dict[str, bytes]) -> int:
        """Seed/erweitert das VFS (Pfad -> Bytes). Rueckgabe: VFS-Groesse."""
        st = self._st(eng)
        fs = getattr(st, "fs", None)
        for k, v in dict(files or {}).items():
            val = v if isinstance(v, (bytes, bytearray)) else str(v).encode("utf-8")
            st.vfs[str(k)] = val
            if fs is not None and hasattr(fs, "add_file"):
                fs.add_file(str(k), val)
        return len(st.vfs)

    def set_env_maps(self, eng, maps: str) -> None:
        st = self._st(eng)
        if st.env is None:
            self.attach_env(eng)
        st.env.set_maps(maps)

    def set_env_threads(self, eng, threads: list) -> None:
        st = self._st(eng)
        if st.env is None:
            self.attach_env(eng)
        st.env.set_threads(threads)

    # ---- Installation (ein Pfad) ----------------------------------------
    def install(self, eng, handler: Optional[iface.SyscallHandler] = None,
                seed_policy: Optional[dict] = None, default_ret: int = 0,
                max_log: int = 100000, config: Optional[dict] = None) -> dict:
        """Setzt ``cpu._svc_hook`` auf den 3-Stufen-Dispatcher (Policy -> Handler -> Default)."""
        st = self._st(eng)
        if st.env is None:
            self.attach_env(eng)
        if config:
            st.config.update(dict(config))
            if "rand_seed" in config:
                st.rng = int(config["rand_seed"]) & _M64
        if handler is None:
            handler = LinuxArm64SyscallHandler(st)
        st.handler = handler

        pol: Dict[int, object] = {}
        for k, v in dict(seed_policy or {}).items():
            try:
                pol[int(k)] = v
            except Exception:
                pass
        st.seed_policy = pol
        st.default_ret = int(default_ret) & _M64
        st.max_log = int(max_log)

        def hook(cpu):
            nr = int(cpu.x[8]) & _M64
            args = [int(cpu.x[i]) & _M64 for i in range(6)]
            spec = st.seed_policy.get(nr)
            if spec is not None:
                # Stufe 1: explizite Policy gewinnt (Alt-Semantik 1:1)
                if isinstance(spec, dict):
                    val = int(spec.get("ret", st.default_ret))
                    za = spec.get("zero_arg")
                    if za is not None:
                        try:
                            cpu.m.wb(int(cpu.x[int(za)]) & _M64,
                                     b"\x00" * int(spec.get("zero_len", 128)))
                        except Exception:
                            pass
                else:
                    val = int(spec)
                cpu.x[0] = val & _M64
                handled_by = "policy"
            else:
                # Stufe 2: genuiner Handler (env/VFS/mmap)
                ok = False
                try:
                    ok = bool(st.handler.dispatch(nr, cpu, eng.mem, st.env))
                except Exception:
                    ok = False  # Handler darf den Lauf nie sprengen
                if ok:
                    handled_by = "handler"
                else:
                    # Stufe 3: Default
                    cpu.x[0] = st.default_ret
                    handled_by = "default"
            if len(st.log) < st.max_log:
                st.log.append({"nr": nr, "args": args,
                               "pc": int(getattr(cpu, "pc", 0) or 0) & _M64,
                               "ret": int(cpu.x[0]) & _M64,
                               "handled_by": handled_by})

        eng.cpu._svc_hook = hook
        eng._svc_log = st.log  # Kompat: ffi.get_syscall_log liest eng._svc_log
        return {"ok": True, "handler": type(handler).__name__,
                "policy_size": len(pol), "env": type(st.env).__name__}

    # ---- Beobachtung ----------------------------------------------------
    def get_syscall_log(self, eng) -> list:
        return list(self._st(eng).log)

    def clear_log(self, eng) -> int:
        st = self._st(eng)
        n = len(st.log)
        st.log = []
        eng._svc_log = st.log
        return n

    def summary(self, eng, cov_top: int = 20, trace_last: int = 50) -> dict:
        from collections import Counter
        st = self._st(eng)
        c = Counter(e["handled_by"] for e in st.log)
        out = {"calls": len(st.log), "by_handler": c.get("handler", 0),
               "by_policy": c.get("policy", 0), "by_default": c.get("default", 0),
               "mmaps": len(st.mmaps), "open_fds": len(st.fds), "vfs": len(st.vfs)}
        # --- OS-9 Observability (additiv) ---
        cov = dict(getattr(st, "syscall_cov", {}) or {})
        out["coverage"] = {"distinct": len(cov),
                           "top": sorted(cov.items(), key=lambda kv: -kv[1])[:int(cov_top)]}
        tr = list(getattr(st, "syscall_trace", []) or [])
        out["trace"] = {"len": len(tr), "last": tr[-int(trace_last):] if trace_last else []}
        net = getattr(st, "net", None) or {}
        peers = sorted({ "%s:%s" % (e[1], e[2]) for e in (net.get("log") or [])
                         if isinstance(e, (list, tuple)) and len(e) >= 3 and e[0] == "connect" })
        out["net"] = {"mode": net.get("mode"), "log_len": len(net.get("log") or []),
                      "peers": peers, "sockets": len(getattr(st, "sockets", {}) or {})}
        out["threads"] = len(getattr(st, "threads", {}) or {})
        out["futexes"] = len(getattr(st, "futexes", {}) or {})
        out["signals"] = len(getattr(st, "signals", {}) or {})
        out["auxv"] = len(getattr(st, "auxv", []) or [])
        out["brk"] = int(getattr(st, "brk", 0) or 0)
        out["policy"] = dict((st.config or {}).get("syscall_policy", {}) or {})
        return out

    def dbg_snapshot(self, eng, trace_last: int = 100) -> dict:
        """G-OS-0: reicher OS-Zustand fuer den geteilten Debug-Zustand (dbg_state).
        Summary + lesbare Detailsichten (mmap-Regionen, Sockets, Threads, Signale, auxv, VFS-Top)."""
        st = self._st(eng)
        snap = self.summary(eng, trace_last=trace_last)
        snap["mmaps"] = [{"base": "0x%x" % m.get("base", 0), "size": m.get("size"),
                          "prot": m.get("prot"), "fd": m.get("fd")}
                         for m in (st.mmaps or [])[:64]]
        snap["sockets_detail"] = [{"fd": fd, "peer": so.get("peer"),
                                   "tx": len(so.get("tx", b"")), "rx": len(so.get("rx", b""))}
                                  for fd, so in (getattr(st, "sockets", {}) or {}).items()]
        snap["threads_detail"] = [{"tid": t, "flags": d.get("flags")}
                                  for t, d in (getattr(st, "threads", {}) or {}).items()]
        snap["signals_detail"] = [{"sig": sig, "handler": "0x%x" % (d.get("handler", 0))}
                                  for sig, d in (getattr(st, "signals", {}) or {}).items()]
        snap["auxv_detail"] = [[int(t), int(v)] for (t, v) in (getattr(st, "auxv", []) or [])][:32]
        try:
            fs = self._fs_of(st)
            snap["vfs_top"] = fs.listdir("/") if fs is not None else []
        except Exception:
            snap["vfs_top"] = []
        snap["brk_hex"] = "0x%x" % int(getattr(st, "brk", 0) or 0)
        return snap

    @staticmethod
    def _fs_of(st):
        if getattr(st, "fs", None) is None:
            try:
                from core.native.vfs import VFS
                st.fs = VFS.from_state(st)
            except Exception:
                return None
        return st.fs
