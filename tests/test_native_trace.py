# -*- coding: utf-8 -*-
"""Tests fuer native_trace_service (Debugger-Schleife + Pfad-Recorder), headless.

Testprogramm @0x1000 (hand-assembliert, little-endian) — eine Zaehlschleife:
    0x1000  movz x0, #3        60 00 80 d2
    0x1004  sub  x0, x0, #1    00 04 00 d1
    0x1008  cbnz x0, 0x1004    e0 ff ff b5
    0x100c  ret                c0 03 5f d6

Bloecke (inline_loops=False): A@0x1000[movz,sub,cbnz] -> B@0x1004[sub,cbnz] (Self-Loop) -> C@0x100c[ret].
Erwartete Block-Einstiegsfolge: [0x1000, 0x1004, 0x1004, 0x100c]  (0x1004 doppelt = Schleife sichtbar).
"""
from services.native_emu_service import NativeEmuService
from services.native_trace_service import NativeTraceService

CODE = bytes.fromhex("600080d2" "000400d1" "e0ffffb5" "c0035fd6")
BASE = 0x1000
EXPECTED = [0x1000, 0x1004, 0x1004, 0x100c]


def _engine():
    emu = NativeEmuService(publish=False)
    eng = emu.new_engine()
    emu.load_blob(eng, BASE, CODE)
    return eng


def _pcs(tr, eng):
    return [e["pc"] for e in tr.get_trace(eng)]


def test_block_trace_shows_loop_and_returns_zero():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    r = tr.debug_run(eng, BASE, [], maxins=1000, mode="block")
    assert r["ok"] and r.get("done"), r
    assert _pcs(tr, eng) == EXPECTED, _pcs(tr, eng)
    assert eng.cpu.x[0] == 0, eng.cpu.x[0]


def test_insn_trace_reconstructs_same_block_entries():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    r = tr.debug_run(eng, BASE, [], maxins=1000, mode="insn")
    assert r["ok"] and r.get("done"), r
    assert _pcs(tr, eng) == EXPECTED, _pcs(tr, eng)
    assert eng.cpu.x[0] == 0, eng.cpu.x[0]


def test_breakpoint_pauses_then_resume_finishes():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    tr.begin(eng, BASE, [])
    r = tr.run(eng, breakpoints=[0x100c], mode="block")
    assert r.get("paused") and r["bp"] == 0x100c, r
    assert _pcs(tr, eng) == [0x1000, 0x1004, 0x1004], _pcs(tr, eng)  # BP-Block noch NICHT ausgefuehrt
    r2 = tr.resume(eng, breakpoints=[0x100c], mode="block")
    assert r2.get("done"), r2
    assert _pcs(tr, eng) == EXPECTED, _pcs(tr, eng)
    assert eng.cpu.x[0] == 0, eng.cpu.x[0]


def test_single_block_stepping():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    tr.begin(eng, BASE, [])
    assert tr.step_block(eng)["pc"] == 0x1004           # nach Block A
    assert tr.step_block(eng)["pc"] == 0x1004           # Schleife, 1. Iteration B
    assert tr.step_block(eng)["pc"] == 0x100c           # Schleife verlassen
    last = tr.step_block(eng)                            # ret -> SENT
    assert last.get("done"), last
    assert _pcs(tr, eng) == EXPECTED, _pcs(tr, eng)
    assert eng.cpu.x[0] == 0, eng.cpu.x[0]


def test_single_instruction_stepping_and_regs():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    tr.begin(eng, BASE, [])
    s1 = tr.step_insn(eng)                               # movz x0,#3
    assert s1["insn"]["mnem"] in ("mov", "movz") and eng.cpu.x[0] == 3, (s1, eng.cpu.x[0])
    s2 = tr.step_insn(eng)                               # sub x0,x0,#1 -> 2
    assert s2["insn"]["mnem"] == "sub" and eng.cpu.x[0] == 2, (s2, eng.cpu.x[0])
    s3 = tr.step_insn(eng)                               # cbnz (genommen) -> 0x1004
    assert s3["insn"]["mnem"] == "cbnz" and s3["pc"] == 0x1004, s3


def test_budget_stops_without_crash():
    tr = NativeTraceService(publish=False)
    eng = _engine()
    r = tr.debug_run(eng, BASE, [], maxins=2, mode="block")  # zu klein -> Budget
    assert r["ok"] and r.get("budget") and r["steps"] == 2, r


def test_export_import_roundtrip(tmp_path):
    tr = NativeTraceService(publish=False)
    eng = _engine()
    tr.debug_run(eng, BASE, [], maxins=1000, mode="block")
    p = str(tmp_path / "trace.json")
    ex = tr.export_trace(eng, p)
    assert ex["ok"] and ex["count"] == 4, ex
    im = tr.load_trace(p)
    assert im["ok"] and [e["pc"] for e in im["trace"]] == EXPECTED, im
