# -*- coding: utf-8 -*-
"""native_thread_service — kooperative Threads + Signalzustellung (N4 / WS-E4/E5).

Bounded Erst-Inkrement (Spec §C.3/C.4): die `iface`-Reserve „Threads/Signale = Hooks am Emulator"
wird als **kooperativer** Scheduler + **Signalzustellung** konkretisiert — jeder Thread hat einen
**isolierten Stack** und eigene Argumente; Signale rufen registrierte Handler auf.

Scope (bewusst bounded): Threads laufen **run-to-completion** (kooperativ, ohne Präemption/Yield —
jeder Thread, der nicht yieldet, läuft bis `ret`). Echtes **quantum-basiertes Interleaving**,
TLS-Register (`TPIDR_EL0`) und `longjmp` sind als Folgeschritt notiert. Nicht-invasiv, additiv:
Zustand am `eng` (`eng._threads`), nutzt den bestehenden `NativeEmuService`-Lauf.
"""
from __future__ import annotations

from core.native import iface

_M64 = (1 << 64) - 1
_THREAD_STACK_ARENA = 0x68000000     # frei (unter SCRATCH 0x67/0x70, mmap 0x72, stub 0x7A)
_THREAD_STACK_SIZE = 0x20000
_SIGNAL_STACK = 0x6A000000


class _Thread:
    __slots__ = ("tid", "entry", "arg", "sp", "started", "done", "ret")

    def __init__(self, tid, entry, arg, sp):
        self.tid = tid
        self.entry = int(entry) & _M64
        self.arg = int(arg) & _M64
        self.sp = int(sp) & _M64
        self.started = False
        self.done = False
        self.ret = None


class NativeThreadService:
    def __init__(self, publish: bool = True):
        self.publish = publish
        from services.native_emu_service import NativeEmuService
        self.emu = NativeEmuService(publish=False)

    def _st(self, eng) -> dict:
        st = getattr(eng, "_threads", None)
        if st is None:
            st = {"threads": [], "next_tid": 1, "signals": {}, "sig_log": []}
            eng._threads = st
        return st

    # ---- Threads (WS-E4) ---------------------------------------------------
    def thread_create(self, eng, entry, arg=0, stack_size: int = _THREAD_STACK_SIZE) -> int:
        """Erzeugt einen Thread-Deskriptor mit eigenem, gemappten Stack. Rückgabe: tid.
        Der Thread läuft erst bei `run_all`/`run_thread`."""
        st = self._st(eng)
        tid = st["next_tid"]
        st["next_tid"] += 1
        base = _THREAD_STACK_ARENA + (tid - 1) * stack_size
        try:
            eng.mem.map(base, stack_size, iface.PERM_RW)
        except Exception:
            pass
        sp = (base + stack_size - 16) & ~0xF
        st["threads"].append(_Thread(tid, entry, arg, sp))
        return tid

    def run_thread(self, eng, tid, maxins: int = 200000) -> dict:
        st = self._st(eng)
        t = next((x for x in st["threads"] if x.tid == tid), None)
        if t is None:
            return {"ok": False, "hinweis": f"tid {tid} unbekannt"}
        if t.done:
            return {"ok": True, "tid": tid, "ret": t.ret, "already": True}
        t.started = True
        # entry(arg): pthread-Konvention -> arg in x0; eigener Stack
        self.emu.run(eng, t.entry, (t.arg,), None, t.sp, int(maxins))
        t.ret = int(eng.cpu.x[0]) & _M64
        t.done = True
        return {"ok": True, "tid": tid, "ret": t.ret}

    def run_all(self, eng, maxins: int = 200000) -> dict:
        """Kooperativ (run-to-completion) alle offenen Threads nacheinander mit isoliertem Stack."""
        st = self._st(eng)
        for t in list(st["threads"]):
            if not t.done:
                self.run_thread(eng, t.tid, maxins)
        return {"ok": True, "threads": [{"tid": t.tid, "ret": t.ret, "sp": hex(t.sp)}
                                        for t in st["threads"]]}

    def threads(self, eng) -> list:
        return [{"tid": t.tid, "done": t.done, "ret": t.ret, "sp": hex(t.sp)}
                for t in self._st(eng)["threads"]]

    # ---- Signale (WS-E5) ---------------------------------------------------
    def signal_handler(self, eng, signo, handler_pc) -> dict:
        """Registriert einen Signal-Handler (handler_pc wird bei signal_raise(signo) aufgerufen)."""
        self._st(eng)["signals"][int(signo)] = int(handler_pc) & _M64
        return {"ok": True, "signo": int(signo), "handler": hex(int(handler_pc) & _M64)}

    def signal_raise(self, eng, signo, maxins: int = 100000) -> dict:
        """Stellt ein Signal zu: ruft den registrierten Handler `handler(signo)` auf einem
        Signal-Stack auf. Ohne Handler: Default (nicht behandelt, protokolliert)."""
        st = self._st(eng)
        h = st["signals"].get(int(signo))
        if h is None:
            st["sig_log"].append({"signo": int(signo), "delivered": False})
            return {"ok": True, "delivered": False, "signo": int(signo)}
        try:
            eng.mem.map(_SIGNAL_STACK - 0x8000, 0x8000, iface.PERM_RW)
        except Exception:
            pass
        ssp = (_SIGNAL_STACK - 16) & ~0xF
        self.emu.run(eng, int(h), (int(signo),), None, ssp, int(maxins))
        ret = int(eng.cpu.x[0]) & _M64
        st["sig_log"].append({"signo": int(signo), "delivered": True, "handler": hex(h), "ret": ret})
        return {"ok": True, "delivered": True, "signo": int(signo), "ret": ret}

    def signal_log(self, eng) -> list:
        return list(self._st(eng)["sig_log"])

    def summary(self, eng) -> dict:
        st = self._st(eng)
        return {"threads": len(st["threads"]), "done": sum(1 for t in st["threads"] if t.done),
                "handlers": sorted(st["signals"].keys()), "signals_delivered": len(st["sig_log"])}
