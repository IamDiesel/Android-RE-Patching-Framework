# -*- coding: utf-8 -*-
"""Tests fuer native_verify_service (Bit-Exakt-Regression, headless)."""
import os
import tempfile

from services.native_emu_service import NativeEmuService
from services.native_verify_service import NativeVerifyService

MOVZ_X0_42 = bytes.fromhex("400580d2")   # movz x0,#0x2a
MOVZ_X0_7 = bytes.fromhex("e00080d2")    # movz x0,#7
RET = bytes.fromhex("c0035fd6")


def _run(code):
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, code)
    return svc.run(eng, 0x1000, [])


def _store():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(p)
    return NativeVerifyService(p), p


def test_golden_roundtrip_and_match():
    vs, p = _store()
    try:
        r1 = _run(MOVZ_X0_42 + RET)
        vs.set("c", vs.reduce(r1))
        r2 = _run(MOVZ_X0_42 + RET)          # identischer Lauf
        cmp = vs.compare(vs.get("c"), r2)
        assert cmp["match"] and cmp["diffs"] == [], cmp
    finally:
        if os.path.exists(p):
            os.remove(p)


def test_mismatch_reports_diff():
    vs, p = _store()
    try:
        vs.set("c", vs.reduce(_run(MOVZ_X0_42 + RET)))   # Golden ret=0x2a
        r = _run(MOVZ_X0_7 + RET)                         # ret=0x7
        cmp = vs.compare(vs.get("c"), r)
        assert not cmp["match"], cmp
        assert any(d["field"] == "ret" for d in cmp["diffs"]), cmp
    finally:
        if os.path.exists(p):
            os.remove(p)
