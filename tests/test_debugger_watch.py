# -*- coding: utf-8 -*-
"""AP-5: Watch-Auswertung (eval_in_frame) + Locals-Namensparsing. Headless."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger
from ui.controllers.debugger_controller import DebuggerController

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

LOCALSRC = """.method static x()V
    .locals 2
    .local v0, "sum":I
    .local v1, "i":I
    return-void
.end method
"""

def test_eval_in_frame_at_entry():
    vm = DalvikVM(); md = Sm.parse_method(SRC); vm.register(md)
    d = JavaDebugger(vm)
    d.start(md.cls, md.name, md.sig, (5,), stop_at_entry=True)
    assert d.eval_in_frame("p0") == 5
    assert d.eval_in_frame("p0 * 2") == 10
    assert isinstance(d.eval_in_frame("nichtda"), str)
    d.cont()

def test_watch_values_via_controller():
    vm = DalvikVM(); md = Sm.parse_method(SRC); vm.register(md)
    c = DebuggerController(None)
    c._jdb = JavaDebugger(vm)
    c._jdb.start(md.cls, md.name, md.sig, (3,), stop_at_entry=True)
    c.add_watch("p0")
    vals = dict(c.watch_values())
    assert vals["p0"] == 3
    c._jdb.cont()

def test_locals_names_parsing():
    c = DebuggerController(None)
    c.source = LOCALSRC
    names = c.locals_names()
    assert names.get("v0") == "sum" and names.get("v1") == "i"
