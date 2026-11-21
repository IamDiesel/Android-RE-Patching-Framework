# -*- coding: utf-8 -*-
"""AP-E1: Tolerant-Modus (Fehler in Aufrufen -> null) + Debugger-Fehlerbericht. Headless."""
import pytest
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger

SRC = """.class public Lt/T;
.super Ljava/lang/Object;
.method public static boom()I
    .locals 1
    const/4 v0, 0x1
    div-int/lit8 v0, v0, 0x0
    return v0
.end method
.method public static caller()I
    .locals 1
    invoke-static {}, Lt/T;->boom()I
    move-result v0
    const/16 v0, 0x63
    return v0
.end method
"""

def _vm():
    vm = DalvikVM()
    _, mds = Sm.parse_class(SRC)
    for md in mds:
        vm.register(md)
    return vm

def test_strict_raises():
    vm = _vm()
    with pytest.raises(Exception):
        vm.invoke("Lt/T;", "caller", "()I", ())

def test_lenient_swallows_callee_error():
    vm = _vm(); vm.lenient = True
    r = vm.invoke("Lt/T;", "caller", "()I", ())
    assert r == 99
    assert len(vm.lenient_errors) == 1
    assert "divide by zero" in vm.lenient_errors[0]["error"]

def test_debugger_reports_real_error():
    vm = _vm()   # strikt
    d = JavaDebugger(vm)
    st = d.start("Lt/T;", "caller", "()I", (), stop_at_entry=False)
    assert st["mode"] == "error"
    assert "divide by zero" in (st.get("error") or "")

def test_debugger_lenient_runs_through():
    vm = _vm(); vm.lenient = True
    d = JavaDebugger(vm)
    st = d.start("Lt/T;", "caller", "()I", (), stop_at_entry=False)
    assert st["mode"] == "done"
    assert st["result"] == 99
