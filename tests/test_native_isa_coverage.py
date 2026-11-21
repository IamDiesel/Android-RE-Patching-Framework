# -*- coding: utf-8 -*-
"""Tests fuer N1 / WS-E1 / FR-4: strukturierte Unknown-Instruktion statt stillem/generischem
Abbruch, durchgereicht bis in den Coverage-Loop.
"""
import pytest

from services.native_emu_service import NativeEmuService
from services.native_coverage_service import NativeCoverageService
from core.native import iface


def _eng():
    return NativeEmuService(publish=False).new_engine()


def test_step_raises_structured_unknown():
    cpu = _eng().cpu
    with pytest.raises(iface.UnknownInstructionError) as ei:
        cpu.step((0x2000, "frobnicate", "x0, x1"))
    e = ei.value
    assert e.pc == 0x2000
    assert e.mnemonic == "frobnicate"
    assert isinstance(e.raw, int)                 # raw aus dem Speicher gelesen (FR-4)
    d = e.as_dict()
    assert d["mnemonic"] == "frobnicate" and d["pc"] == 0x2000


def _boom_stub(pc=0x1234, raw=0xdeadbeef, mnem="frob", enc="x0, x1"):
    def _s(cpu):
        raise iface.UnknownInstructionError(pc, raw, mnemonic=mnem, encoding=enc)
    return _s


def test_emu_service_classifies_unknown():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    eng.cpu.stub[0x1000] = _boom_stub()
    r = svc.run(eng, 0x1000, (), None, None, 100)
    assert r["ok"] is False and r["why"] == "unknown_instr", r
    assert r["pc"] == 0x1234 and r["mnem"] == "frob" and r["raw"] == 0xdeadbeef
    assert r["encoding"] == "x0, x1"


def test_coverage_service_reports_unknown():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    eng.cpu.stub[0x1000] = _boom_stub(pc=0x55, raw=0x99, mnem="frob2")
    cov = NativeCoverageService().run_coverage(eng, 0x1000, args=(), maxins=100)
    assert cov["ran_ok"] is False and cov.get("why") == "unknown_instr", cov
    assert cov["unknown"]["mnem"] == "frob2" and cov["unknown"]["pc"] == 0x55


def test_known_opcode_does_not_raise_unknown():
    # Regression: eine implementierte Instruktion (negs) wirft NICHT UnknownInstructionError.
    cpu = _eng().cpu
    cpu.x[1] = 5
    cpu.step((0x3000, "negs", "x0, x1"))          # x0 = -x1, Flags gesetzt
    assert cpu.x[0] == ((-5) & ((1 << 64) - 1))
