# -*- coding: utf-8 -*-
"""OS-6 Tests: sigaction/procmask/pending/altstack/kill."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_RT_SIGACTION = 134
NR_RT_SIGPROCMASK = 135
NR_RT_SIGPENDING = 136
NR_SIGALTSTACK = 132
NR_KILL = 129


def _eng():
    return NativeEmuService(publish=False).new_engine()


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _booted():
    eng = _eng()
    NativeOsService(publish=False).install(eng)
    return eng


# --------------------------------------------------------------------------
def test_sigaction_store_and_readback():
    eng = _booted(); cpu = eng.cpu
    act = _SC
    handler = 0x1234560
    cpu.m.wb(act, struct.pack("<QQ", handler, 0x4000000) + b"\x00" * 8)  # SA_SIGINFO
    assert _svc(cpu, NR_RT_SIGACTION, 11, act, 0, 8) == 0               # SIGSEGV
    # oldact lesen
    old = _SC + 0x100
    _svc(cpu, NR_RT_SIGACTION, 11, 0, old, 8)
    h = struct.unpack("<Q", bytes(cpu.m.rb(old, 8)))[0]
    assert h == handler


def test_sigprocmask_setmask_and_old():
    eng = _booted(); cpu = eng.cpu
    setp = _SC
    cpu.m.wb(setp, struct.pack("<Q", 0xDEAD))
    assert _svc(cpu, NR_RT_SIGPROCMASK, 2, setp, 0, 8) == 0   # SIG_SETMASK
    old = _SC + 0x100
    _svc(cpu, NR_RT_SIGPROCMASK, 0, 0, old, 8)                # oldset lesen
    assert struct.unpack("<Q", bytes(cpu.m.rb(old, 8)))[0] == 0xDEAD


def test_sigpending_empty():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_RT_SIGPENDING, _SC, 8) == 0
    assert struct.unpack("<Q", bytes(cpu.m.rb(_SC, 8)))[0] == 0


def test_sigaltstack_roundtrip():
    eng = _booted(); cpu = eng.cpu
    ss = _SC
    cpu.m.wb(ss, struct.pack("<QiI", 0x900000, 0, 0x2000))   # ss_sp, ss_flags, ss_size
    assert _svc(cpu, NR_SIGALTSTACK, ss, 0) == 0
    old = _SC + 0x100
    _svc(cpu, NR_SIGALTSTACK, 0, old)
    assert struct.unpack("<Q", bytes(cpu.m.rb(old, 8)))[0] == 0x900000


def test_kill_no_deliver():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_KILL, 1234, 9) == 0
