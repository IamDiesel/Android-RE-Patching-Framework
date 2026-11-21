# -*- coding: utf-8 -*-
"""X1-Erweiterung: Step Into / Over / Out ueber die Aufruf-Tiefe (Bereich = Funktion samt Callees)."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger
from core.java.class_provider import TextClassProvider

ENTRY = """.method public static run(I)I
    .locals 2
    invoke-static {p0}, Lcom/foo/Helper;->triple(I)I
    move-result v0
    add-int/lit8 v0, v0, 0x1
    return v0
.end method"""
HELPER = """.class public Lcom/foo/Helper;
.super Ljava/lang/Object;
.method public static triple(I)I
    .locals 2
    const/4 v0, 0x3
    mul-int v0, p0, v0
    return v0
.end method"""


def _dbg():
    vm = DalvikVM()
    md = Sm.parse_method(ENTRY)
    vm.register(md)
    vm.class_provider = TextClassProvider(HELPER)
    return JavaDebugger(vm), md


def test_step_into_enters_callee():
    d, md = _dbg()
    d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=True)   # @ invoke
    st = d.step()
    assert st["name"] == "triple"                                # in den Callee hinein


def test_step_over_skips_callee():
    d, md = _dbg()
    d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=True)   # @ invoke
    st = d.step_over()
    assert st["name"] == "run" and st["pc"] == 1                 # Callee ausgefuehrt, zurueck in run
    st = d.cont()
    assert st["result"] == 16                                    # triple(5)=15, +1


def test_step_out_returns_to_caller():
    d, md = _dbg()
    d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=True)
    d.step()                                                     # in triple
    assert d.state()["name"] == "triple"
    st = d.step_out()
    assert st["name"] == "run"                                   # zurueck im Caller
