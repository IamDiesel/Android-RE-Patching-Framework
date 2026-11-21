# -*- coding: utf-8 -*-
"""Stufe 1 (Controller): Grenz-Aktionspunkt-Helfer ohne tkinter (Duck-Typing)."""
from ui.controllers.debugger_controller import DebuggerController


class _Ctl:
    def __init__(self):
        self.jni_aps = []
        self._fusion = None
        self.jni_log = None
        self._logs = []
    def _log(self, m): self._logs.append(m)

_Ctl.add_boundary_ap = DebuggerController.add_boundary_ap
_Ctl.clear_boundary_aps = DebuggerController.clear_boundary_aps
_Ctl.jni_entries = DebuggerController.jni_entries
_Ctl._fusion_load_library = DebuggerController._fusion_load_library


def test_boundary_ap_spec_build_and_clear():
    c = _Ctl()
    c.add_boundary_ap(fnptr="0x1000", ret_expr="0")
    assert len(c.jni_aps) == 1
    assert c.jni_aps[0]["fnptr"] == 0x1000 and c.jni_aps[0]["ret_expr"] == "0"
    c.add_boundary_ap(fnptr=0x2000, arg_assigns=[{"index": 0, "value": 7, "const": True}])
    assert len(c.jni_aps) == 2 and c.jni_aps[1]["fnptr"] == 0x2000
    c.clear_boundary_aps()
    assert c.jni_aps == []


def test_jni_entries_empty_default():
    c = _Ctl()
    assert c.jni_entries() == []
    c._fusion_load_library("db")  # darf nur loggen, nicht werfen
    assert any("loadLibrary" in m for m in c._logs)
