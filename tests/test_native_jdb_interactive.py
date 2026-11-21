# -*- coding: utf-8 -*-
"""X1/MS-6: interaktive Debugger-Drive-Semantik (genau der Pfad, den die GUI-Buttons bedienen).
Start/Halt@Eintritt -> Step -> Breakpoint -> set_register (Live-Mutation) -> Continue -> Zeitreise goto()."""
import pytest

from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger

SMALI = """.method public static sumto(I)I
    .locals 2
    const/4 v0, 0x0
    const/4 v1, 0x0
:loop
    if-ge v1, p0, :done
    add-int v0, v0, v1
    add-int/lit8 v1, v1, 0x1
    goto :loop
:done
    return v0
.end method"""


def _build():
    vm = DalvikVM()
    md = Sm.parse_method(SMALI)
    vm.register(md)
    return vm, md


def test_start_step_continue():
    vm, md = _build()
    d = JavaDebugger(vm)
    st = d.start(md.cls, md.name, md.sig, (4,), stop_at_entry=True)
    assert st["mode"] == "paused" and st["pc"] == 0 and st["step"] == 0
    for _ in range(3):
        st = d.step()
    assert st["mode"] == "paused"
    st = d.cont()
    assert st["mode"] == "done" and st["result"] == 6   # 0+1+2+3


def test_breakpoint_live_mutation():
    # pc des add-int ermitteln
    vm, md = _build()
    d = JavaDebugger(vm)
    d.record_timeline = True                   # AP1.1: Timeline fuer pc-Suche einschalten
    d.start(md.cls, md.name, md.sig, (4,), stop_at_entry=False); d.cont()
    add_pc = next(r["pc"] for r in d.timeline if r["op"] == "add-int")
    # frischer Lauf mit Breakpoint + Mutation des Akkus v0
    vm, md = _build()
    d = JavaDebugger(vm)
    d.add_breakpoint(md.cls, md.name, md.sig, add_pc)
    st = d.start(md.cls, md.name, md.sig, (4,), stop_at_entry=False)
    assert st["mode"] == "paused" and st["pc"] == add_pc
    assert d.set_register(0, 100) is True
    st = d.cont()
    while st["mode"] == "paused":
        st = d.cont()
    assert st["mode"] == "done" and st["result"] == 106   # 6 + 100 (Live-Mutation sichtbar)


def test_time_travel_goto():
    vm, md = _build()
    d = JavaDebugger(vm)
    d.start(md.cls, md.name, md.sig, (4,), stop_at_entry=False); d.cont()
    st = d.goto(2)
    assert st["mode"] == "paused" and st["step"] == 2
    # Reproduzierbar: nochmal goto(2) liefert denselben Zustand
    st2 = d.goto(2)
    assert st2["step"] == 2 and st2["pc"] == st["pc"]
