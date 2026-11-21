# -*- coding: utf-8 -*-
"""Tests: RASP Survival Mode (tote Dispatches überleben) + Syscall-Hook (rohe svc)."""
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_trace_service import NativeTraceService

_SENT = 0xDEAD0000


def _eng():
    return NativeEmuService(publish=False).new_engine()


def test_survive_skips_null_dispatch_to_lr_end():
    eng = _eng()
    tr = NativeTraceService(publish=False)
    tr.survive_enable(eng, min_pc=0x1000, cap=10)
    eng.cpu.pc = 0
    eng.cpu.lr = _SENT                      # lr == SENT -> Survival endet sauber
    r = tr.run(eng, breakpoints=[], maxins=100, mode="block")
    assert r.get("ok") and (r.get("survived_end") or r.get("done"))
    log = tr.survive_get(eng)
    assert len(log) == 1 and log[0]["dead_pc"] == "0x0"
    tr.survive_disable(eng)
    assert getattr(eng, "_survive", None) is None


def test_svc_hook_logs_and_returns():
    eng = _eng()
    ffi = NativeFfiService(publish=False)
    ffi.install_syscall_hook(eng, returns={172: 4242})   # getpid -> 4242
    cpu = eng.cpu
    cpu.pc = 0x2c000
    cpu.x[8] = 172
    cpu.x[0] = 1; cpu.x[1] = 2
    cpu._svc_hook(cpu)
    assert cpu.x[0] == 4242
    log = ffi.get_syscall_log(eng)
    assert len(log) == 1 and log[0]["nr"] == 172 and log[0]["args"][0] == 1


def test_svc_hook_default_zero():
    eng = _eng()
    ffi = NativeFfiService(publish=False)
    ffi.install_syscall_hook(eng)
    cpu = eng.cpu
    cpu.pc = 0x2c000
    cpu.x[8] = 56; cpu.x[0] = 99
    cpu._svc_hook(cpu)
    assert cpu.x[0] == 0                     # Default-Rueckgabe 0
    assert ffi.get_syscall_log(eng)[0]["nr"] == 56
