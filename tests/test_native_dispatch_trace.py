# -*- coding: utf-8 -*-
"""Tests für den CFF-Dispatch-Tracer in native_trace_service (Read-Watchpoint auf die
2D-vtable 0x137740). Synthetisch, ohne echte Lib."""
from services.native_emu_service import NativeEmuService
from services.native_trace_service import NativeTraceService

VT = 0x137740


def _eng():
    svc = NativeEmuService(publish=False)
    return svc.new_engine()


def test_records_vtable_reads_and_empty():
    eng = _eng()
    eng.cpu.m.w64(VT + 139 * 8, 0xabe54)   # gefüllter Handler
    eng.cpu.m.w64(VT + 140 * 8, 0x0)       # echte Lücke
    tr = NativeTraceService(publish=False)
    tr.dispatch_trace_enable(eng)
    eng.cpu.x[20] = 139
    v1 = eng.cpu.m.r64(VT + 139 * 8)
    eng.cpu.x[20] = 140
    v2 = eng.cpu.m.r64(VT + 140 * 8)
    tr.dispatch_trace_disable(eng)
    assert v1 == 0xabe54 and v2 == 0
    log = tr.dispatch_trace_get(eng)
    assert len(log) == 2
    assert log[0]["col"] == 139 and log[0]["idx1"] == 0 and not log[0]["empty"]
    assert log[1]["col"] == 140 and log[1]["empty"]
    s = tr.dispatch_trace_summary(eng)
    assert s["dispatches"] == 2 and s["empty_fetches"] == 1
    assert s["first_empty"] and s["first_empty"]["col"] == 140


def test_disable_restores_and_stops_logging():
    eng = _eng()
    tr = NativeTraceService(publish=False)
    tr.dispatch_trace_enable(eng)
    eng.cpu.m.r64(VT + 8)
    assert len(tr.dispatch_trace_get(eng)) == 1
    tr.dispatch_trace_disable(eng)
    eng.cpu.m.r64(VT + 8)                   # nach disable: kein neuer Eintrag
    assert len(tr.dispatch_trace_get(eng)) == 1


def test_facade_taint_flagged():
    eng = _eng()
    eng.cpu.m.w64(VT + 50 * 8, 0x7a000f90)  # Fassaden-Stub-Adresse als Slot-Wert
    tr = NativeTraceService(publish=False)
    tr.dispatch_trace_enable(eng)
    eng.cpu.m.r64(VT + 50 * 8)
    tr.dispatch_trace_disable(eng)
    log = tr.dispatch_trace_get(eng)
    assert log and log[0]["val_taint"] is True


def test_outside_region_not_logged():
    eng = _eng()
    tr = NativeTraceService(publish=False)
    tr.dispatch_trace_enable(eng)
    eng.cpu.m.r64(0x100000)                 # außerhalb [0x136000,0x13a000)
    tr.dispatch_trace_disable(eng)
    assert tr.dispatch_trace_get(eng) == []
