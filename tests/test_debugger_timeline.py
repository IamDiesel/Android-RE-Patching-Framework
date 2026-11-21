# -*- coding: utf-8 -*-
"""AP-4: Timeline-Scrubber-Mapping + Lesezeichen. Headless."""
from ui.controllers.debugger_controller import DebuggerController

def test_frac_to_step():
    f = DebuggerController.frac_to_step
    assert f(0.0, 10) == 0
    assert f(1.0, 10) == 9
    assert f(0.5, 9) == 4       # 0.5*8 = 4.0
    assert f(0.25, 5) == 1      # 0.25*4 = 1.0
    assert f(2.0, 10) == 9      # geklemmt
    assert f(-1, 10) == 0
    assert f(0.5, 0) == 0       # leere Timeline

def test_bookmark_roundtrip():
    c = DebuggerController(None)
    c.toggle_bookmark(3)
    c.toggle_bookmark(7)
    assert c.bookmarks_list() == [3, 7]
    c.toggle_bookmark(3)
    assert c.bookmarks_list() == [7]
