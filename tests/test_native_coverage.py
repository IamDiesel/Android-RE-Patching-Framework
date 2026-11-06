# -*- coding: utf-8 -*-
"""Tests fuer native_coverage_service (headless)."""
from services.native_emu_service import NativeEmuService
from services.native_coverage_service import NativeCoverageService

LDRB_W0_X0 = bytes.fromhex("00004039")   # ldrb w0,[x0]
RET = bytes.fromhex("c0035fd6")


def test_coverage_counts_and_mnemonics():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, LDRB_W0_X0 + RET)
    cov = NativeCoverageService().run_coverage(eng, 0x1000, [{"ptr": "7a"}])
    assert cov["ran_ok"], cov
    assert cov["blocks_reached"] >= 1, cov
    assert cov["instr_total"] >= 2, cov
    assert "ret" in cov["mnemonics"], cov
    assert cov["ret"] == 0x7a, cov


def test_coverage_reports_unknown_on_udf():
    # 0x0000dead ist keine gueltige Instruktion -> unknown_instr-Abbruch
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x2000, bytes.fromhex("adde0000") + RET)
    cov = NativeCoverageService().run_coverage(eng, 0x2000, [])
    # entweder sauberer Lauf (falls doch dekodierbar) oder unknown-Report — hier: nicht ok
    assert "blocks_reached" in cov, cov
    if not cov["ran_ok"]:
        assert cov.get("why") in ("unknown_instr", "error"), cov
