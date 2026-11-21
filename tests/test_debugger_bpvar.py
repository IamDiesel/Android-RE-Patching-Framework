# -*- coding: utf-8 -*-
"""AP-1: Breakpoint-Varianten (bedingt / Hit-Count / One-Shot) + safe_eval. Headless."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger, safe_eval

SRC = """.method public static loop(I)I
    .locals 2
    const/4 v0, 0x0
    const/4 v1, 0x0
    :top
    add-int v0, v0, v1
    add-int/lit8 v1, v1, 0x1
    if-lt v1, p0, :top
    return v0
.end method
"""
# pc: 0 const v0 | 1 const v1 | 2 add-int(:top) | 3 add-int/lit8 | 4 if-lt | 5 return

def _dbg():
    vm = DalvikVM()
    md = Sm.parse_method(SRC)
    vm.register(md)
    d = JavaDebugger(vm)
    return d, md

def test_safe_eval_basic():
    assert safe_eval("v1 == 3", {"v1": 3}) is True
    assert safe_eval("v0 > 10 and v1 < 2", {"v0": 20, "v1": 1}) is True
    assert safe_eval("(v0 + v1) * 2", {"v0": 3, "v1": 4}) == 14
    assert safe_eval("v0 & 0xff", {"v0": 0x1234}) == 0x34

def test_safe_eval_rejects_calls():
    import pytest
    for bad in ("__import__('os')", "open('x')", "v0.text", "a[0]"):
        with pytest.raises(Exception):
            safe_eval(bad, {"v0": 1, "a": [1]})

def test_conditional_bp():
    d, md = _dbg()
    d.add_breakpoint(md.cls, md.name, md.sig, 2, cond="v1 == 3")
    st = d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=False)
    assert st["mode"] == "paused"
    assert st["pc"] == 2
    assert d.registers()[1] == 3          # i == 3 an der Haltestelle
    d.cont()

def test_hitcount_bp():
    d, md = _dbg()
    d.add_breakpoint(md.cls, md.name, md.sig, 2, hit=3)   # ab 3. Treffer
    st = d.start(md.cls, md.name, md.sig, (6,), stop_at_entry=False)
    assert st["mode"] == "paused"
    assert d.registers()[1] == 2          # 3. Erreichen von pc2 -> i==2
    d.cont()

def test_oneshot_bp():
    d, md = _dbg()
    d.add_breakpoint(md.cls, md.name, md.sig, 2, one_shot=True)
    st = d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=False)
    assert st["mode"] == "paused"
    assert d.registers()[1] == 0          # 1. Treffer
    st2 = d.cont()                         # danach entfernt -> laeuft durch
    assert st2["mode"] == "done"
    assert st2["result"] == 10            # sum(0..4)
    assert len(d.breakpoints) == 0
