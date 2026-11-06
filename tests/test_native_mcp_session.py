# -*- coding: utf-8 -*-
"""Headless-Test der MCP-Session-Logik fuer native.step/native.resume."""
from services.native_emu_service import NativeEmuService
import mcp_server.tools as T

LDRB_W0_X0 = bytes.fromhex("00004039")
B_TO_7008 = bytes.fromhex("01000014")   # b .+4 (0x7004 -> 0x7008)
RET = bytes.fromhex("c0035fd6")


def test_session_step_to_done():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x7000, LDRB_W0_X0 + B_TO_7008 + RET)   # 0x7008 ret
    p = svc.run_with_breaks(eng, 0x7000, [{"ptr": "7a"}], breakpoints=[0x7008])
    assert p["paused"], p
    sid = T._session_put({"svc": svc, "eng": eng, "img": None, "rp": "x"})
    r = T.native_step(None, sid)                               # ret ausfuehren -> fertig
    assert r["ok"] and r.get("done") is True, r
    assert r.get("session_ended") is True, r
    assert r["ret"] == "0x7a", r
    assert sid not in T._NATIVE_SESSIONS                       # Session nach done geraeumt


def test_session_unknown_id():
    r = T.native_step(None, "does_not_exist")
    assert r["ok"] is False, r


def test_resume_session_to_completion():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x7000, LDRB_W0_X0 + B_TO_7008 + RET)
    p = svc.run_with_breaks(eng, 0x7000, [{"ptr": "33"}], breakpoints=[0x7008])
    assert p["paused"], p
    sid = T._session_put({"svc": svc, "eng": eng, "img": None, "rp": "x"})
    r = T.native_resume(None, sid)
    assert r["ok"] and r.get("done") is True, r
    assert r["ret"] == "0x33", r
