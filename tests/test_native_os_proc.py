# -*- coding: utf-8 -*-
"""OS-5 Tests: IDs/Limits/Sysinfo + auxv + Initial-Stack."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000
_AT_FDCWD = 0xFFFFFF9C

NR_OPENAT = 56
NR_READ = 63
NR_GETPPID = 173
NR_GETGID = 176
NR_GETGROUPS = 158
NR_PRLIMIT64 = 261
NR_SYSINFO = 179
NR_TIMES = 153
NR_UMASK = 166


def _eng():
    return NativeEmuService(publish=False).new_engine()


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _put_cstr(cpu, addr, s):
    cpu.m.wb(addr, s.encode("utf-8") + b"\x00")


def _booted():
    eng = _eng()
    NativeOsService(publish=False).install(eng)
    return eng


# --------------------------------------------------------------------------
def test_getppid_and_getgid():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_GETPPID) == 1
    eng._os.config["gid"] = 9999; eng._os.fs = None
    assert _svc(cpu, NR_GETGID) == 9999


def test_getgroups_count_and_list():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_GETGROUPS, 0, 0) == 1          # size 0 -> count
    n = _svc(cpu, NR_GETGROUPS, 4, _SC)
    assert n == 1
    assert struct.unpack("<I", bytes(cpu.m.rb(_SC, 4)))[0] != 0


def test_prlimit64_stack_limit():
    eng = _booted(); cpu = eng.cpu
    old = _SC
    assert _svc(cpu, NR_PRLIMIT64, 0, 3, 0, old) == 0   # RLIMIT_STACK
    cur, mx = struct.unpack("<QQ", bytes(cpu.m.rb(old, 16)))
    assert cur == 0x800000


def test_sysinfo_totalram():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_SYSINFO, _SC) == 0
    totalram = struct.unpack("<Q", bytes(cpu.m.rb(_SC + 32, 8)))[0]
    assert totalram > 0


def test_times_returns_ticks():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_TIMES, _SC) > 0


def test_umask_returns_old():
    eng = _booted(); cpu = eng.cpu
    old = _svc(cpu, NR_UMASK, 0o077)
    assert old == 0o22
    assert _svc(cpu, NR_UMASK, 0o22) == 0o077          # vorher gesetzt


def test_setup_initial_stack_layout():
    eng = _booted(); cpu = eng.cpu
    h = eng._os.handler
    cpu.sp = 0x7ffffff000
    sp = h.setup_initial_stack(cpu, argv=["/system/bin/app_process64", "--x"],
                               envp=["PATH=/system/bin", "ANDROID_DATA=/data"])
    assert sp == cpu.sp and sp % 16 == 0
    argc = struct.unpack("<Q", bytes(cpu.m.rb(sp, 8)))[0]
    assert argc == 2
    argv0_ptr = struct.unpack("<Q", bytes(cpu.m.rb(sp + 8, 8)))[0]
    s0 = bytes(cpu.m.rb(argv0_ptr, 40)).split(b"\x00", 1)[0]
    assert s0 == b"/system/bin/app_process64"


def test_proc_self_auxv_node():
    eng = _booted(); cpu = eng.cpu
    eng._os.handler.setup_initial_stack(cpu, argv=["x"], envp=[])
    eng._os.fs = None                                   # auxv_pairs jetzt in config
    _put_cstr(cpu, _SC, "/proc/self/auxv")
    fd = _svc(cpu, NR_OPENAT, _AT_FDCWD, _SC, 0)
    buf = _SC + 0x1000
    n = _svc(cpu, NR_READ, fd, buf, 512)
    assert n >= 16 and n % 16 == 0
    # letztes Paar ist AT_NULL(0,0)
    t, v = struct.unpack("<QQ", bytes(cpu.m.rb(buf + n - 16, 16)))
    assert t == 0 and v == 0
