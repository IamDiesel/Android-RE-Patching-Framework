# -*- coding: utf-8 -*-
"""OS-4 Tests: futex, clone, prctl, set_tid_address, sched_*, rseq."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_FUTEX = 98
NR_CLONE = 220
NR_PRCTL = 167
NR_SET_TID_ADDRESS = 96
NR_SCHED_GETAFFINITY = 123
NR_RSEQ = 293
NR_EXIT_GROUP = 94


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
def test_futex_wait_value_match_returns_zero():
    eng = _booted(); cpu = eng.cpu
    ua = _SC
    cpu.m.wb(ua, struct.pack("<I", 7))
    # FUTEX_WAIT|PRIVATE(128), erwartet 7 -> stimmt -> sofort 0
    assert _svc(cpu, NR_FUTEX, ua, 0 | 128, 7, 0) == 0


def test_futex_wait_value_mismatch_eagain():
    eng = _booted(); cpu = eng.cpu
    ua = _SC
    cpu.m.wb(ua, struct.pack("<I", 7))
    rc = _svc(cpu, NR_FUTEX, ua, 0 | 128, 42, 0)   # erwartet 42, ist 7
    assert rc == (-11) & _M64                       # -EAGAIN


def test_futex_wake_returns_zero():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_FUTEX, _SC, 1 | 128, 1, 0) == 0   # FUTEX_WAKE


def test_futex_wake_op_mutates_uaddr2():
    eng = _booted(); cpu = eng.cpu
    ua2 = _SC + 0x100
    cpu.m.wb(ua2, struct.pack("<I", 0))
    # WAKE_OP(5); encoded op: SET(0)<<28 | oparg=5<<12
    encoded = (0 << 28) | (5 << 12)
    _svc(cpu, NR_FUTEX, _SC, 5 | 128, 1, encoded, ua2)
    assert struct.unpack("<I", bytes(cpu.m.rb(ua2, 4)))[0] == 5


def test_clone_returns_child_tid_and_sets_ctid():
    eng = _booted(); cpu = eng.cpu
    ctid = _SC + 0x200
    flags = 0x01000000                      # CLONE_CHILD_SETTID
    child = _svc(cpu, NR_CLONE, flags, 0, 0, 0, ctid)
    assert child > 1234
    assert struct.unpack("<i", bytes(cpu.m.rb(ctid, 4)))[0] == child


def test_prctl_set_get_name():
    eng = _booted(); cpu = eng.cpu
    nm = _SC
    cpu.m.wb(nm, b"worker-1\x00")
    assert _svc(cpu, NR_PRCTL, 15, nm, 0, 0, 0) == 0     # PR_SET_NAME
    out = _SC + 0x80
    _svc(cpu, NR_PRCTL, 16, out, 0, 0, 0)                # PR_GET_NAME
    assert bytes(cpu.m.rb(out, 8)) == b"worker-1"


def test_prctl_get_dumpable_default():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_PRCTL, 3, 0, 0, 0, 0) == 1       # PR_GET_DUMPABLE


def test_set_tid_address_returns_tid():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_SET_TID_ADDRESS, _SC) == 1234


def test_sched_getaffinity_writes_mask():
    eng = _booted(); cpu = eng.cpu
    buf = _SC
    n = _svc(cpu, NR_SCHED_GETAFFINITY, 0, 8, buf)
    assert n == 8
    assert struct.unpack("<Q", bytes(cpu.m.rb(buf, 8)))[0] != 0


def test_rseq_enosys():
    eng = _booted(); cpu = eng.cpu
    assert _svc(cpu, NR_RSEQ, 0, 0, 0, 0) == (-38) & _M64   # -ENOSYS


def test_exit_group_sets_flag():
    eng = _booted(); cpu = eng.cpu
    _svc(cpu, NR_EXIT_GROUP, 0)
    assert eng._os.config.get("exited") is True
