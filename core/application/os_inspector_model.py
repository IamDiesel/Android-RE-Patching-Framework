# -*- coding: utf-8 -*-
"""OS-Inspektor-Modell (MVC) — Zustand des OS-Substrats fuer die GUI.

Rein (kein tkinter) und headless-testbar. Haelt (a) den letzten Snapshot aus
``NativeOsService.dbg_snapshot``/``summary`` und (b) ein ``config_delta`` mit den
bewussten Benutzer-/KI-Aenderungen, das VOR dem naechsten Lauf in ``_OsState.config``
angewandt wird. Views rendern aus den ``*_rows``-Zugriffen; Edits rufen die Setter.

Generisch: keine Target-Spezifika. Der Zustand liegt im Modell, nicht in den Widgets
(Console-MVC-Prinzip) -> verlustfreies Docken/Abdocken.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List


class OsInspectorModel:
    def __init__(self):
        self.snapshot: Dict[str, Any] = {}
        self.config_delta: Dict[str, Any] = {}
        self._listeners: List[Callable[[], None]] = []

    # ---- Beobachter (Views binden sich an) --------------------------------
    def subscribe(self, fn: Callable[[], None]) -> None:
        if fn not in self._listeners:
            self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    # ---- Aufnahme eines Snapshots -----------------------------------------
    def ingest(self, snap: Dict[str, Any]) -> None:
        self.snapshot = dict(snap or {})
        self._notify()

    # ---- Syscalls ---------------------------------------------------------
    def coverage_rows(self) -> List[list]:
        return list((self.snapshot.get("coverage") or {}).get("top") or [])

    def trace_rows(self) -> List[dict]:
        return list((self.snapshot.get("trace") or {}).get("last") or [])

    def policy_rows(self) -> List[dict]:
        """Vereinheitlicht: pro Syscall die OS-9-Aktion (allow/deny/stub) UND die NR-Seed-Rueckgabe.
        Snapshot-Policy + ausstehendes Delta zusammengefuehrt."""
        os9 = dict(self.snapshot.get("policy") or {})
        os9.update(self.config_delta.get("syscall_policy") or {})
        ov = dict(self.config_delta.get("syscall_overrides") or {})
        seed = dict(self.config_delta.get("seed_policy") or {})   # name -> ret (NR-Ebene, bequem per Name)
        names = sorted(set(os9) | set(ov) | set(seed))
        rows = []
        for n in names:
            rows.append({"name": n, "os9": os9.get(n, ""),
                         "override_ret": (ov.get(n) or {}).get("ret", ""),
                         "nr_ret": seed.get(n, "")})
        return rows

    def set_policy(self, name: str, action: str) -> None:
        if action in ("", "allow"):
            self.config_delta.get("syscall_policy", {}).pop(name, None)
        else:
            self.config_delta.setdefault("syscall_policy", {})[name] = action
        self._notify()

    def set_override(self, name: str, ret=None, args: dict = None, skip: bool = False) -> None:
        ov = self.config_delta.setdefault("syscall_overrides", {})
        entry: Dict[str, Any] = {}
        if ret is not None:
            entry["ret"] = int(ret)
        if args:
            entry["args"] = {int(k): int(v) for k, v in args.items()}
        if skip:
            entry["skip"] = True
        if entry:
            ov[name] = entry
        else:
            ov.pop(name, None)
        self._notify()

    def set_breakpoint(self, name: str, on: bool = True) -> None:
        bps = set(self.config_delta.get("syscall_breakpoints") or [])
        bps.add(name) if on else bps.discard(name)
        if bps:
            self.config_delta["syscall_breakpoints"] = sorted(bps)
        else:
            self.config_delta.pop("syscall_breakpoints", None)
        self._notify()

    # ---- VFS --------------------------------------------------------------
    def vfs_rows(self) -> List[list]:
        return list(self.snapshot.get("vfs_top") or [])

    def set_vfs(self, path: str, content) -> None:
        self.config_delta.setdefault("vfs_mounts", {})[str(path)] = content
        self._notify()

    # ---- Speicher ---------------------------------------------------------
    def mmap_rows(self) -> List[dict]:
        return list(self.snapshot.get("mmaps") or [])

    def brk(self) -> str:
        return self.snapshot.get("brk_hex", "0x0")

    # ---- Threads / Signale / Zeit / auxv ----------------------------------
    def thread_rows(self) -> List[dict]:
        return list(self.snapshot.get("threads_detail") or [])

    def signal_rows(self) -> List[dict]:
        return list(self.snapshot.get("signals_detail") or [])

    def auxv_rows(self) -> List[list]:
        return list(self.snapshot.get("auxv_detail") or [])

    def set_clock_advance(self, ns: int) -> None:
        self.config_delta["clock_advance_ns"] = int(ns)
        self._notify()

    # ---- Netz -------------------------------------------------------------
    def socket_rows(self) -> List[dict]:
        return list(self.snapshot.get("sockets_detail") or [])

    def net_peers(self) -> List[str]:
        return list((self.snapshot.get("net") or {}).get("peers") or [])

    def set_net_replay(self, key: str, data) -> None:
        self.config_delta.setdefault("net_replay", {})[str(key)] = data
        self.config_delta.setdefault("net_mode", "replay")
        self._notify()

    def set_net_mode(self, mode: str) -> None:
        self.config_delta["net_mode"] = str(mode)
        self._notify()

    # ---- Determinismus ----------------------------------------------------
    def set_seed(self, seed: int) -> None:
        self.config_delta["rand_seed"] = int(seed)
        self._notify()

    def set_trace(self, on: bool, trace_max: int = 4096) -> None:
        self.config_delta["trace_syscalls"] = bool(on)
        if on:
            self.config_delta["trace_max"] = int(trace_max)
        self._notify()

    # ---- Ausgabe ----------------------------------------------------------
    def build_config(self) -> Dict[str, Any]:
        """Config-Delta, das vor dem naechsten Lauf in _OsState.config angewandt wird."""
        return dict(self.config_delta)

    def reset_delta(self) -> None:
        self.config_delta = {}
        self._notify()

    def load_config(self, cfg: Dict[str, Any]) -> None:
        """Vom KI-/Sitzungs-Zustand uebernehmen (⬇ OS←KI)."""
        self.config_delta = dict(cfg or {})
        self._notify()
