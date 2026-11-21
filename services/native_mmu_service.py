# -*- coding: utf-8 -*-
"""native_mmu_service — opt-in W^X-/Permissions-Enforcement im heißen Pfad (N3 / WS-E2).

Hintergrund (Spec §C.2): `Arm64Memory` hält ein Perms-Overlay (`map/protect/perms`), das im
heißen CPU-Pfad bisher **nicht** konsultiert wird — W^X/Fault werden also nicht erzwungen. Dieser
Service verdrahtet die Erzwingung als **opt-in** (Default aus), ohne die lipy-Kontrakt-Mem-Klasse zu
ändern: bei `enable()` werden die Write-Methoden der konkreten `Mem`-Instanz (`eng.cpu.m`) mit einem
Perms-Guard umhüllt; bei `disable()` werden die Originale wiederhergestellt → null Overhead im Default.

Semantik: ein Schreibzugriff auf eine Seite ohne `PERM_W` meldet einen `MemoryFault`
(`iface.MemoryFault`) bzw. ruft den `on_fault(eng, addr, "write")`-Hook; gibt dieser truthy zurück,
wird der Schreibzugriff dennoch zugelassen (Beobachtungsmodus), sonst wird der Fault geworfen.

Scope (N3, bewusst bounded): **Write-Seite** (SMC/Tamper-Erkennung, RASP-relevant) + `mprotect` +
Fault-Modell + `on_fault`. Die **Exec-Seite** (Ausführen aus Nicht-X-Seiten) und SMC-Cache-Invalidierung
sind als Folgeschritt notiert (brauchen einen Fetch-/Block-Entry-Hook). Nicht-invasiv, additiv.
"""
from __future__ import annotations

from core.native import iface

# Write-Methoden der Mem-Instanz (kein gemeinsamer Funnel -> alle umhüllen; w16 existiert i.d.R. nicht).
_WRITE_METHODS = ("wb", "w", "w8", "w16", "w32", "w64")


def _nbytes(name, args):
    if name == "wb":
        return len(args[0]) if args else 0
    if name == "w":
        return int(args[1]) if len(args) > 1 else 0
    return {"w8": 1, "w16": 2, "w32": 4, "w64": 8}.get(name, 1)


class NativeMmuService:
    """Opt-in Permissions-Enforcement. Zustand am ``eng`` (``eng._mmu``)."""

    def __init__(self, publish: bool = True):
        self.publish = publish

    def _st(self, eng) -> dict:
        st = getattr(eng, "_mmu", None)
        if st is None:
            st = {"enabled": False, "orig": {}, "faults": [], "on_fault": None, "max_faults": 1000}
            eng._mmu = st
        return st

    # ---- Enforcement an/aus ------------------------------------------------
    def enable(self, eng, on_fault=None, max_faults: int = 1000) -> dict:
        """Perms-Guard auf die Write-Methoden von ``eng.cpu.m`` legen. Idempotent."""
        st = self._st(eng)
        st["on_fault"] = on_fault
        st["max_faults"] = int(max_faults)
        if st["enabled"]:
            return {"ok": True, "already": True, "guarded": list(st["orig"].keys())}
        mem_overlay = eng.mem            # Arm64Memory: perms()/protect()
        raw = eng.cpu.m                  # die rohe Mem-Instanz im heißen Pfad

        def make(name, orig):
            def guarded(a, *args):
                nb = _nbytes(name, args)
                addr = int(a)
                if not (mem_overlay.perms(addr) & iface.PERM_W):
                    self._fault(eng, addr, nb)
                return orig(a, *args)
            return guarded

        for name in _WRITE_METHODS:
            orig = getattr(raw, name, None)
            if orig is None or not callable(orig):
                continue
            st["orig"][name] = orig
            setattr(raw, name, make(name, orig))
        st["enabled"] = True
        return {"ok": True, "guarded": list(st["orig"].keys())}

    def disable(self, eng) -> dict:
        st = self._st(eng)
        raw = eng.cpu.m
        for name, orig in st["orig"].items():
            try:
                setattr(raw, name, orig)
            except Exception:
                pass
        st["orig"] = {}
        st["enabled"] = False
        return {"ok": True}

    # ---- Fault-Behandlung --------------------------------------------------
    def _fault(self, eng, addr: int, nb: int) -> None:
        st = self._st(eng)
        have = eng.mem.perms(addr)
        if len(st["faults"]) < st["max_faults"]:
            st["faults"].append({"addr": addr, "size": nb,
                                 "need": iface.PERM_W, "have": have})
        allow = False
        cb = st["on_fault"]
        if cb is not None:
            try:
                allow = bool(cb(eng, addr, "write"))
            except Exception:
                allow = False
        if not allow:
            raise iface.MemoryFault(addr, nb, iface.PERM_W, have)

    # ---- Perms / mprotect --------------------------------------------------
    def mprotect(self, eng, addr: int, size: int, perms: int) -> dict:
        eng.mem.protect(int(addr), int(size), int(perms))
        return {"ok": True, "addr": int(addr), "size": int(size), "perms": int(perms)}

    def map(self, eng, addr: int, size: int, perms: int = iface.PERM_RW) -> dict:
        eng.mem.map(int(addr), int(size), int(perms))
        return {"ok": True}

    def perms(self, eng, addr: int) -> int:
        return eng.mem.perms(int(addr))

    # ---- Beobachtung -------------------------------------------------------
    def faults(self, eng) -> list:
        return list(self._st(eng)["faults"])

    def clear_faults(self, eng) -> int:
        st = self._st(eng)
        n = len(st["faults"])
        st["faults"] = []
        return n

    def summary(self, eng) -> dict:
        st = self._st(eng)
        return {"enabled": st["enabled"], "faults": len(st["faults"]),
                "guarded": list(st["orig"].keys())}
