# -*- coding: utf-8 -*-
"""AP-3: Controller.read_memory liest Register-Arrays als Bytes. Headless."""
from ui.controllers.debugger_controller import DebuggerController
from core.java.objects import JArray, JString

class _Frame:
    def __init__(self, regs): self.v = regs
class _FakeJdb:
    def __init__(self, regs):
        self.mode = "paused"; self.current = {"fr": _Frame(regs)}

def test_read_byte_array():
    c = DebuggerController(None)
    c._jdb = _FakeJdb([JArray("B", [1, 2, 3, 255])])
    base, data = c.read_memory("v0")
    assert base == 0 and data == bytes([1, 2, 3, 255])

def test_read_int_array_le():
    c = DebuggerController(None)
    c._jdb = _FakeJdb([JArray("I", [1, 256])])
    _, data = c.read_memory("v0")
    assert data == bytes([1, 0, 0, 0, 0, 1, 0, 0])

def test_read_string():
    c = DebuggerController(None)
    c._jdb = _FakeJdb([JString("hi")])
    _, data = c.read_memory("v0")
    assert data == b"hi"

def test_read_not_paused():
    c = DebuggerController(None)
    assert c.read_memory("v0") == (0, b"")
