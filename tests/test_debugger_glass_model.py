# -*- coding: utf-8 -*-
"""Headless-Tests fuer das glaeserne-Editor-Modell im DebuggerController (kein tkinter)."""
from ui.controllers.debugger_controller import DebuggerController

SRC = """.method public static demo(I)I
    .locals 1
    const/4 v0, 0x0
    :loop
    add-int v0, v0, p0
    if-gtz p0, :loop
    return v0
.end method
"""

def _ctrl():
    c = DebuggerController(app=None)   # app=None -> _after ruft synchron
    c.source = SRC
    c.name = "demo"
    c.refresh_source_map()
    return c

def test_pc_line_mapping():
    c = _ctrl()
    assert len(c._srcmap) == 4
    # const/4 @ Zeile 3 (1-basiert), add-int @5, if-gtz @6, return @7
    assert c.pc_to_line(0) == 3
    assert c.pc_to_line(1) == 5
    assert c.pc_to_line(3) == 7
    assert c.line_to_pcs(5) == [1]
    assert c.line_to_pcs(4) == []   # Label-Zeile -> keine Instruktion

def test_toggle_bp_at_line_roundtrip():
    c = _ctrl()
    c.toggle_bp_at_line(5)          # add-int -> pc1
    assert c.bps == [1]
    assert c.line_has_bp(5) is True
    c.toggle_bp_at_line(5)          # wieder weg
    assert c.bps == []
    assert c.line_has_bp(5) is False

def test_toggle_bp_at_nonins_line():
    c = _ctrl()
    c.toggle_bp_at_line(4)          # Label -> keine Wirkung
    assert c.bps == []

def test_toggle_bp_direct():
    c = _ctrl()
    c.toggle_bp(2)
    c.toggle_bp(0)
    assert c.bps == [0, 2]
    c.toggle_bp(2)
    assert c.bps == [0]

def test_current_pc_line_none_without_run():
    c = _ctrl()
    assert c.current_pc_line() is None
