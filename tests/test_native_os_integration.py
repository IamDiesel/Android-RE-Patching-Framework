# -*- coding: utf-8 -*-
"""OS-Substrat Integration: realistische Startsequenz einer generischen App.

Spielt OS-1..OS-9 in einem Lauf zusammen und prueft Coverage-Breite.
"""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000
_AT_FDCWD = 0xFFFFFF9C


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _put(cpu, addr, s):
    cpu.m.wb(addr, s.encode() + b"\x00")


def test_generic_app_startup_sequence():
    eng = NativeEmuService(publish=False).new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng, maps="72000000-72001000 r-xp 0 00:00 0 [anon:libc]\n")
    os_svc.install(eng)
    cpu = eng.cpu
    eng._os.config.update({
        "package": "com.generic.app", "trace_syscalls": True,
        "net_mode": "replay", "net_replay": {"10.0.0.9:443": b"HTTP/1.1 200 OK\r\n\r\nOK"},
    })
    eng._os.fs = None; eng._os.net = None

    # 1) OS-5: Startstack
    cpu.sp = 0x7ffffff000
    sp = eng._os.handler.setup_initial_stack(
        cpu, argv=["/system/bin/app_process64", "com.generic.app"],
        envp=["ANDROID_DATA=/data"])
    assert sp % 16 == 0

    # 2) OS-2: RASP-Sonden (/sys/fs/selinux/enforce, /proc/self/maps, /proc/self/auxv)
    for path in ("/sys/fs/selinux/enforce", "/proc/self/maps", "/proc/self/auxv"):
        _put(cpu, _SC, path)
        fd = _svc(cpu, 56, _AT_FDCWD, _SC, 0)       # openat
        assert fd >= 3
        n = _svc(cpu, 63, fd, _SC + 0x2000, 256)    # read
        assert n > 0
        _svc(cpu, 57, fd)                           # close

    # 3) OS-5: prlimit64 (RLIMIT_STACK) + OS-5 getppid
    _svc(cpu, 261, 0, 3, 0, _SC + 0x3000)
    assert _svc(cpu, 173) == 1

    # 4) OS-3: brk-Heap waechst
    cur = _svc(cpu, 214, 0)
    assert _svc(cpu, 214, (cur + 0x8000) & _M64) == (cur + 0x8000) & _M64

    # 5) OS-4: prctl(PR_SET_NAME), futex-Fastpath, clone
    _put(cpu, _SC, "main\x00")
    _svc(cpu, 167, 15, _SC, 0, 0, 0)                # PR_SET_NAME
    cpu.m.wb(_SC + 0x10, struct.pack("<I", 0))
    assert _svc(cpu, 98, _SC + 0x10, 1 | 128, 1, 0) == 0   # FUTEX_WAKE
    child = _svc(cpu, 220, 0x1000, 0, 0, 0, 0)      # clone
    assert child > 1234

    # 6) OS-6: SIGSEGV-Handler installieren (Anti-Debug-Muster)
    cpu.m.wb(_SC, struct.pack("<QQ", 0xBEEF00, 0x4000000) + b"\x00" * 8)
    assert _svc(cpu, 134, 11, _SC, 0, 8) == 0

    # 7) OS-7: nanosleep (virtuelle Uhr)
    cpu.m.wb(_SC, struct.pack("<QQ", 0, 1_000_000))
    assert _svc(cpu, 101, _SC, 0) == 0

    # 8) OS-8: Socket -> connect -> recv (Replay)
    s = _svc(cpu, 198, 2, 1, 0)
    cpu.m.wb(_SC, struct.pack("<H", 2) + struct.pack(">H", 443) +
             bytes([10, 0, 0, 9]) + b"\x00" * 8)
    assert _svc(cpu, 203, s, _SC, 16) == 0
    n = _svc(cpu, 207, s, _SC + 0x2000, 256, 0, 0, 0)
    assert bytes(cpu.m.rb(_SC + 0x2000, n)) == b"HTTP/1.1 200 OK\r\n\r\nOK"

    # 9) OS-9: Coverage-Breite + Trace vorhanden
    cov = eng._os.syscall_cov
    for name in ("openat", "read", "brk", "prctl", "futex", "clone",
                 "rt_sigaction", "nanosleep", "socket", "connect", "recvfrom"):
        assert name in cov, "fehlt in Coverage: " + name
    assert len(eng._os.syscall_trace) > 0
    # mindestens 14 verschiedene Syscalls im Lauf (OS-1..OS-9 kombiniert)
    assert len(cov) >= 14
