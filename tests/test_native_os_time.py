# -*- coding: utf-8 -*-
"""OS-7 Tests: nanosleep/clock_nanosleep (virtuelle Uhr) + clock_getres."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_NANOSLEEP = 101
NR_CLOCK_NANOSLEEP = 115
NR_CLOCK_GETRES = 114


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
def test_nanosleep_advances_virtual_clock():
    eng = _booted(); cpu = eng.cpu
    env = eng._os.env
    before = env._clock
    req = _SC
    cpu.m.wb(req, struct.pack("<QQ", 5, 0))         # 5 Sekunden
    assert _svc(cpu, NR_NANOSLEEP, req, 0) == 0
    assert env._clock - before >= 5_000_000_000


def test_clock_nanosleep_advances():
    eng = _booted(); cpu = eng.cpu
    env = eng._os.env
    before = env._clock
    req = _SC
    cpu.m.wb(req, struct.pack("<QQ", 0, 250_000_000))   # 250 ms
    assert _svc(cpu, NR_CLOCK_NANOSLEEP, 1, 0, req, 0) == 0
    assert env._clock - before >= 250_000_000


def test_clock_getres_1ns():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_CLOCK_GETRES, 1, _SC) == 0
    sec, nsec = struct.unpack("<QQ", bytes(cpu.m.rb(_SC, 16)))
    assert sec == 0 and nsec == 1
