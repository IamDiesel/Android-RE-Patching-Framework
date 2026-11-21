# -*- coding: utf-8 -*-
"""Tests fuer N4 / WS-E4/E5: kooperative Threads (isolierte Stacks) + Signalzustellung."""
from services.native_emu_service import NativeEmuService
from services.native_thread_service import NativeThreadService

_RET = bytes.fromhex("c0035fd6")              # ret
_ADD_X0_10 = bytes.fromhex("00280091")         # add x0, x0, #10
_ADD_X0_X0 = bytes.fromhex("0000008b")         # add x0, x0, x0   (x0*2)
_ADD_X0_1 = bytes.fromhex("00040091")          # add x0, x0, #1
# str x0,[sp] ; add x0,x0,#100 ; ldr x0,[sp]  -> gibt den Ausgangswert zurueck (ueber den Stack)
_STACK_RT = (bytes.fromhex("e00300f9") + bytes.fromhex("00900191")
             + bytes.fromhex("e00340f9"))


def _eng():
    return NativeEmuService(publish=False).new_engine()


def test_two_threads_independent():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, _ADD_X0_10 + _RET)   # fnA: arg+10
    svc.load_blob(eng, 0x2000, _ADD_X0_X0 + _RET)   # fnB: arg*2
    th = NativeThreadService(publish=False)
    t1 = th.thread_create(eng, 0x1000, arg=5)
    t2 = th.thread_create(eng, 0x2000, arg=7)
    r = th.run_all(eng)
    rets = {d["tid"]: d["ret"] for d in r["threads"]}
    assert rets[t1] == 15 and rets[t2] == 14
    sps = {d["tid"]: d["sp"] for d in r["threads"]}
    assert sps[t1] != sps[t2]                        # isolierte Stacks


def test_thread_uses_own_stack():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, _STACK_RT + _RET)     # Stack-Roundtrip: gibt arg zurueck
    th = NativeThreadService(publish=False)
    t1 = th.thread_create(eng, 0x1000, arg=0x55)
    r = th.run_thread(eng, t1)
    assert r["ret"] == 0x55                           # Store/Load ueber den eigenen Stack ok


def test_signal_delivery():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x3000, _ADD_X0_1 + _RET)     # handler(signo) = signo+1
    th = NativeThreadService(publish=False)
    th.signal_handler(eng, 9, 0x3000)
    r = th.signal_raise(eng, 9)
    assert r["delivered"] is True and r["ret"] == 10   # handler(9) -> 10
    r2 = th.signal_raise(eng, 11)                      # kein Handler
    assert r2["delivered"] is False
    log = th.signal_log(eng)
    assert any(e["signo"] == 9 and e["delivered"] for e in log)


def test_summary():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, _ADD_X0_10 + _RET)
    th = NativeThreadService(publish=False)
    th.thread_create(eng, 0x1000, arg=1)
    th.signal_handler(eng, 2, 0x1000)
    th.run_all(eng)
    s = th.summary(eng)
    assert s["threads"] == 1 and s["done"] == 1 and 2 in s["handlers"]
