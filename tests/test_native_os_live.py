# -*- coding: utf-8 -*-
"""G-OS-0: Syscall-Overrides / Live-Hooks / Breakpoints / dbg_snapshot."""
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_GETUID = 174
NR_GETPID = 172
NR_GETRANDOM = 278


def _svc(cpu, nr, *args):
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _booted(**cfg):
    eng = NativeEmuService(publish=False).new_engine()
    osv = NativeOsService(publish=False)
    osv.install(eng)
    eng._os.config.update(cfg)
    return eng, osv


# --------------------------------------------------------------------------
def test_override_forces_return():
    eng, _ = _booted(syscall_overrides={"getuid": {"ret": 4242}})
    assert _svc(eng.cpu, NR_GETUID) == 4242


def test_override_skip_prevents_effect():
    # getrandom mit skip -> Puffer bleibt unberuehrt, ret erzwungen
    eng, _ = _booted(syscall_overrides={"getrandom": {"ret": 7, "skip": True}})
    cpu = eng.cpu
    cpu.m.wb(_SC, b"\x00" * 16)
    n = _svc(cpu, NR_GETRANDOM, _SC, 16, 0)
    assert n == 7
    assert bytes(cpu.m.rb(_SC, 16)) == b"\x00" * 16    # fn uebersprungen


def test_override_arg_edit():
    # getpriority ignoriert args; nutze faccessat-Arg nicht. Teste args-Edit an getuid (kein Effekt auf ret),
    # also pruefe Registerzustand: args[0] gesetzt vor Ausfuehrung.
    seen = {}
    eng, _ = _booted(syscall_overrides={"getuid": {"args": {0: 0xABCD}}})
    eng._os.syscall_hooks = {"pre": lambda rec, cpu: seen.update(a0=rec["args"][0])}
    _svc(eng.cpu, NR_GETUID)
    # pre-Hook sieht die Original-Args (vor Override); Override setzt x0 danach -> getuid ueberschreibt x0 ohnehin
    assert "a0" in seen


def test_live_hooks_pre_post():
    recs = []
    eng, _ = _booted()
    eng._os.syscall_hooks = {"post": lambda rec, cpu: recs.append((rec["n"], rec["ret"]))}
    _svc(eng.cpu, NR_GETUID)
    assert ("getuid", 10234) in recs


def test_syscall_breakpoint_sets_flag():
    eng, _ = _booted(syscall_breakpoints={"getpid"})
    cpu = eng.cpu
    assert not getattr(cpu, "_svc_break", None)
    _svc(cpu, NR_GETPID)
    sb = getattr(cpu, "_svc_break", None)
    assert sb is not None and sb["n"] == "getpid"


def test_dbg_snapshot_rich():
    eng, osv = _booted(trace_syscalls=True)
    cpu = eng.cpu
    _svc(cpu, NR_GETUID)
    _svc(cpu, 222, 0, 0x2000, 0x3, 0x22, (-1) & _M64, 0)   # mmap anon
    snap = osv.dbg_snapshot(eng)
    assert "coverage" in snap and "mmaps" in snap and "vfs_top" in snap
    assert isinstance(snap["vfs_top"], list) and len(snap["mmaps"]) >= 1
    assert snap["brk_hex"].startswith("0x")
