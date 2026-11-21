# -*- coding: utf-8 -*-
"""OS-3 Tests: brk, mremap, file-backed mmap (Unpack-Pattern)."""
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SCRATCH = 0x70000
_AT_FDCWD = 0xFFFFFF9C

NR_OPENAT = 56
NR_WRITE = 64
NR_MMAP = 222
NR_BRK = 214
NR_MREMAP = 216


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
def test_brk_query_then_grow():
    eng = _booted(); cpu = eng.cpu
    cur = _svc(cpu, NR_BRK, 0)
    assert cur != 0
    newb = (cur + 0x4000) & _M64
    got = _svc(cpu, NR_BRK, newb)
    assert got == newb                       # Erfolg -> neuer Break
    # Heap beschreibbar
    cpu.m.wb(cur, b"\xAB" * 16)
    assert bytes(cpu.m.rb(cur, 4)) == b"\xAB\xAB\xAB\xAB"


def test_brk_failure_returns_current():
    eng = _booted(); cpu = eng.cpu
    cur = _svc(cpu, NR_BRK, 0)
    # weit jenseits der Arena -> Fehlschlag, alter Break
    got = _svc(cpu, NR_BRK, (cur + 0x40000000) & _M64)
    assert got == cur


def test_file_backed_mmap_unpack_pattern():
    """App schreibt entschluesselte Bytes in tmp-fd, mmapt ihn -> Bytes im Speicher."""
    eng = _booted(); cpu = eng.cpu
    # Datei anlegen ueber VFS-Mount (legacy) damit openat sie findet
    eng._os.config.setdefault("links", {})
    eng._os.vfs = {"/data/local/tmp/x.so": b""}
    eng._os.fs = None
    _put_cstr(cpu, _SCRATCH, "/data/local/tmp/x.so")
    fd = _svc(cpu, NR_OPENAT, _AT_FDCWD, _SCRATCH, 0)
    assert fd >= 3
    payload = b"\x7fELF" + bytes(range(64))
    src = _SCRATCH + 0x1000
    cpu.m.wb(src, payload)
    n = _svc(cpu, NR_WRITE, fd, src, len(payload))
    assert n == len(payload)
    # mmap(NULL, len, PROT_READ, MAP_PRIVATE=0x2, fd, 0)
    base = _svc(cpu, NR_MMAP, 0, len(payload), 0x1, 0x2, fd, 0)
    assert base != 0
    assert bytes(cpu.m.rb(base, len(payload))) == payload


def test_anon_mmap_still_zero():
    eng = _booted(); cpu = eng.cpu
    base = _svc(cpu, NR_MMAP, 0, 0x2000, 0x3, 0x22, (-1) & _M64, 0)  # MAP_ANON|PRIVATE
    assert base != 0
    assert bytes(cpu.m.rb(base, 8)) == b"\x00" * 8


def test_mremap_copies_old_bytes():
    eng = _booted(); cpu = eng.cpu
    base = _svc(cpu, NR_MMAP, 0, 0x1000, 0x3, 0x22, (-1) & _M64, 0)
    cpu.m.wb(base, b"HELLO-MREMAP")
    nb = _svc(cpu, NR_MREMAP, base, 0x1000, 0x4000, 1)  # MREMAP_MAYMOVE
    assert nb != 0
    assert bytes(cpu.m.rb(nb, 12)) == b"HELLO-MREMAP"
