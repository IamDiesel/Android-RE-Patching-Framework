# -*- coding: utf-8 -*-
"""OS-9 Tests: Syscall-Policy (deny/stub) + Trace-Ring + Coverage/Determinismus."""
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_GETPID = 172
NR_GETUID = 174
NR_GETPPID = 173


def _eng():
    return NativeEmuService(publish=False).new_engine()


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _booted(**cfg):
    eng = _eng()
    NativeOsService(publish=False).install(eng)
    eng._os.config.update(cfg)
    return eng


# --------------------------------------------------------------------------
def test_policy_deny_returns_eperm():
    eng = _booted(syscall_policy={"getpid": "deny"})
    cpu = eng.cpu
    assert _svc(cpu, NR_GETPID) == (-1) & _M64        # -EPERM
    # ungesperrter Syscall laeuft normal
    assert _svc(cpu, NR_GETUID) == 10234


def test_policy_stub_returns_zero():
    eng = _booted(syscall_policy={"getppid": "stub"})
    cpu = eng.cpu
    assert _svc(cpu, NR_GETPPID) == 0


def test_trace_records_args_and_ret():
    eng = _booted(trace_syscalls=True)
    cpu = eng.cpu
    _svc(cpu, NR_GETUID)
    tr = eng._os.syscall_trace
    assert any(r["n"] == "getuid" and r["ret"] == 10234 for r in tr)


def test_trace_off_by_default():
    eng = _booted()
    cpu = eng.cpu
    _svc(cpu, NR_GETUID)
    assert len(eng._os.syscall_trace) == 0


def test_trace_ring_bounded():
    eng = _booted(trace_syscalls=True, trace_max=5)
    cpu = eng.cpu
    for _ in range(20):
        _svc(cpu, NR_GETUID)
    assert len(eng._os.syscall_trace) == 5


def test_coverage_counts():
    eng = _booted()
    cpu = eng.cpu
    _svc(cpu, NR_GETUID); _svc(cpu, NR_GETUID); _svc(cpu, NR_GETPID)
    cov = eng._os.syscall_cov
    assert cov.get("getuid") == 2 and cov.get("getpid") == 1


def test_determinism_same_seed_same_random():
    # Zwei Instanzen, gleicher Seed -> gleicher getrandom-Strom
    import struct
    NR_GETRANDOM = 278
    outs = []
    for _ in range(2):
        eng = _booted()
        cpu = eng.cpu
        _svc(cpu, NR_GETRANDOM, _SC, 32, 0)
        outs.append(bytes(cpu.m.rb(_SC, 32)))
    assert outs[0] == outs[1]
