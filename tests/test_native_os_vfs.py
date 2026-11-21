# -*- coding: utf-8 -*-
"""OS-2 Tests: VFS 2.0 hinter den Datei-Syscalls.

Deckt getdents64, faccessat, statx, /dev-Geraete und /proc-Dynknoten ab.
Gleiches hermetisches Muster wie test_native_os (cpu._svc_hook direkt).
"""
import struct

from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SCRATCH = 0x70000
_AT_FDCWD = 0xFFFFFF9C

# Syscall-Nummern (AArch64)
NR_OPENAT = 56
NR_READ = 63
NR_GETDENTS64 = 61
NR_FACCESSAT = 48
NR_NEWFSTATAT = 79
NR_STATX = 291


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


def _booted(**cfg):
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng)
    if cfg:
        eng._os.config.update(cfg)
        eng._os.fs = None   # VFS neu bauen, damit config greift
    return eng, os_svc


# --------------------------------------------------------------------------
def test_faccessat_existing_and_missing():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/dev/null")
    assert _svc(cpu, NR_FACCESSAT, _AT_FDCWD, _SCRATCH, 0, 0) == 0
    _put_cstr(cpu, _SCRATCH, "/nope/missing")
    rc = _svc(cpu, NR_FACCESSAT, _AT_FDCWD, _SCRATCH, 0, 0)
    assert rc == (-2) & _M64    # -ENOENT


def test_getdents64_lists_dev():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/dev")
    fd = _svc(cpu, NR_OPENAT, _AT_FDCWD, _SCRATCH, 0)
    assert fd >= 3
    buf = _SCRATCH + 0x2000
    n = _svc(cpu, NR_GETDENTS64, fd, buf, 0x1000)
    assert n > 0
    blob = bytes(cpu.m.rb(buf, n))
    # d_reclen@16(u16), name@19.. ; grob die Namen einsammeln
    names, off = set(), 0
    while off + 19 < len(blob):
        reclen = struct.unpack_from("<H", blob, off + 16)[0]
        if reclen == 0:
            break
        nm = blob[off + 19:off + reclen].split(b"\x00", 1)[0]
        names.add(nm.decode("latin1"))
        off += reclen
    assert "null" in names and "zero" in names and "urandom" in names


def test_statx_dev_null_mode_and_ok():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/dev/null")
    sbuf = _SCRATCH + 0x3000
    rc = _svc(cpu, NR_STATX, _AT_FDCWD, _SCRATCH, 0, 0, sbuf)
    assert rc == 0
    mode = struct.unpack_from("<H", bytes(cpu.m.rb(sbuf + 28, 2)), 0)[0]
    assert mode != 0                      # Modus gesetzt (S_IFCHR o.ae.)


def test_statx_missing_enoent():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/nope")
    rc = _svc(cpu, NR_STATX, _AT_FDCWD, _SCRATCH, 0, 0, _SCRATCH + 0x3000)
    assert rc == (-2) & _M64


def test_newfstatat_stat_bytes_nonzero_size():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/proc/version")
    sbuf = _SCRATCH + 0x3000
    rc = _svc(cpu, NR_NEWFSTATAT, _AT_FDCWD, _SCRATCH, sbuf, 0)
    assert rc == 0
    size = struct.unpack_from("<Q", bytes(cpu.m.rb(sbuf + 48, 8)), 0)[0]
    assert size > 0                       # /proc/version hat Inhalt


def test_proc_cmdline_follows_package():
    eng, _ = _booted(package="com.example.app")
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/proc/self/cmdline")
    fd = _svc(cpu, NR_OPENAT, _AT_FDCWD, _SCRATCH, 0)
    buf = _SCRATCH + 0x2000
    n = _svc(cpu, NR_READ, fd, buf, 64)
    assert bytes(cpu.m.rb(buf, n)).startswith(b"com.example.app\x00")


def test_dev_urandom_reads_bytes():
    eng, _ = _booted()
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/dev/urandom")
    fd = _svc(cpu, NR_OPENAT, _AT_FDCWD, _SCRATCH, 0)
    assert fd >= 3
    buf = _SCRATCH + 0x2000
    n = _svc(cpu, NR_READ, fd, buf, 16)
    assert n == 16
