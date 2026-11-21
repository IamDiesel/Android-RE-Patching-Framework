# -*- coding: utf-8 -*-
"""GUI-Komfort P347: Mock/Replay-Helfer des Debugger-Controllers (ohne tkinter)."""
from core.java.net_recorder import ReplayResponder, NetRequest


class _Ctl:
    """Minimaler Stub mit den Mix-in-Methoden des echten Controllers."""
    def __init__(self):
        self._net_responder = None
        self._logs = []
    def _log(self, m): self._logs.append(m)

# Methoden aus dem echten Controller anziehen (Duck-Typing, kein tkinter noetig)
from ui.controllers.debugger_controller import DebuggerController
_Ctl.add_net_mock = DebuggerController.add_net_mock
_Ctl.clear_net_mocks = DebuggerController.clear_net_mocks
_Ctl.net_mocks = DebuggerController.net_mocks


def test_add_and_list_mock():
    c = _Ctl()
    key = c.add_net_mock("https://api/glucose", 200, '{"v":112}', method="POST")
    assert key == "POST https://api/glucose"
    assert isinstance(c._net_responder, ReplayResponder)
    assert key in c.net_mocks()
    # Responder liefert den Mock
    resp = c._net_responder(NetRequest("POST", "https://api/glucose", {}, b""))
    assert resp.code == 200
    assert b"112" in resp.body


def test_substring_mock_and_clear():
    c = _Ctl()
    c.add_net_mock("/glucose", 503, "", method=None)
    resp = c._net_responder(NetRequest("GET", "https://host/v1/glucose/now", {}, b""))
    assert resp.code == 503
    c.clear_net_mocks()
    assert c.net_mocks() == {}
    assert c._net_responder is None
