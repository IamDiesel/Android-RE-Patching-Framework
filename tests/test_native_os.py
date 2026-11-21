# -*- coding: utf-8 -*-
"""Tests fuer native_os_service (N2): Env/Syscall-Konvergenz ueber EINEN Pfad.

Hermetisch (keine .so): baut eine echte Engine ueber NativeEmuService, setzt den
os-Hook und feuert ``cpu._svc_hook`` direkt — wie test_native_syscall_policy.
"""
import os
import struct

import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_trace_service import NativeTraceService
from services.native_os_service import NativeOsService, MMAP_ARENA
from core.native import iface
from core.native.os_linux_arm64 import LinuxArm64SyscallHandler

_M64 = (1 << 64) - 1
_SCRATCH = 0x70000           # Daten-Scratch im normalen Adressraum


def _eng():
    return NativeEmuService(publish=False).new_engine()


def _svc(cpu, nr, *args):
    """Setzt x0..xN, x8=nr und feuert den Syscall-Hook. Rueckgabe: x0."""
    for i, a in enumerate(args):
        cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64
    cpu._svc_hook(cpu)
    return cpu.x[0]


def _put_cstr(cpu, addr, s):
    cpu.m.wb(addr, s.encode("utf-8") + b"\x00")


# ---------------------------------------------------------------------------
def test_install_defaults_env_and_summary():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    r = os_svc.install(eng)
    assert r["ok"] and r["handler"] == "LinuxArm64SyscallHandler"
    assert r["env"] == "FakeEnvironment"
    assert isinstance(eng._os.env, iface.FakeEnvironment)
    assert os_svc.summary(eng)["calls"] == 0


def test_openat_read_proc_self_maps_roundtrip():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    maps = "72000000-72001000 r-xp 00000000 00:00 0  [anon:libdb0c]\n"
    os_svc.attach_env(eng, maps=maps)
    os_svc.install(eng)
    cpu = eng.cpu

    _put_cstr(cpu, _SCRATCH, "/proc/self/maps")
    fd = _svc(cpu, 56, 0xFFFFFF9C, _SCRATCH, 0)   # openat(AT_FDCWD, path, O_RDONLY)
    assert fd >= 3, hex(fd)

    buf = _SCRATCH + 0x1000
    n = _svc(cpu, 63, fd, buf, 4096)              # read(fd, buf, 4096)
    assert n == len(maps.encode())
    assert bytes(cpu.m.rb(buf, n)) == maps.encode()

    # zweiter read => EOF (0)
    n2 = _svc(cpu, 63, fd, buf, 4096)
    assert n2 == 0
    # close
    assert _svc(cpu, 57, fd) == 0


def test_openat_missing_path_enoent():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng)
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/does/not/exist")
    r = _svc(cpu, 56, 0xFFFFFF9C, _SCRATCH, 0)
    assert r == ((-2) & _M64)                     # -ENOENT


def test_vfs_mount_openat_read():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng)
    os_svc.vfs_mount(eng, {"/sys/fs/selinux/enforce": b"0"})
    cpu = eng.cpu
    _put_cstr(cpu, _SCRATCH, "/sys/fs/selinux/enforce")
    fd = _svc(cpu, 56, 0xFFFFFF9C, _SCRATCH, 0)
    assert fd >= 3
    buf = _SCRATCH + 0x1000
    n = _svc(cpu, 63, fd, buf, 16)
    assert n == 1 and bytes(cpu.m.rb(buf, 1)) == b"0"


def test_mmap_distinct_regions_and_bookkeeping():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng)
    cpu = eng.cpu
    # mmap(NULL, 0x1000, PROT_READ|WRITE=3, MAP_PRIVATE|ANON, -1, 0)
    a = _svc(cpu, 222, 0, 0x1000, 3, 0x22, (-1) & _M64, 0)
    b = _svc(cpu, 222, 0, 0x3000, 3, 0x22, (-1) & _M64, 0)
    assert a == MMAP_ARENA
    assert b == MMAP_ARENA + 0x1000            # erste Region 1 Page
    assert a % 0x1000 == 0 and b % 0x1000 == 0
    regs = eng._os.mmaps
    assert len(regs) == 2 and regs[0]["base"] == a and regs[1]["size"] == 0x3000
    # munmap der ersten
    assert _svc(cpu, 215, a, 0x1000) == 0
    assert [m["base"] for m in eng._os.mmaps] == [b]


def test_clock_gettime_deterministic_monotonic():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng, clock_start_ns=1000, clock_step_ns=5)
    os_svc.install(eng)
    cpu = eng.cpu
    ts = _SCRATCH
    assert _svc(cpu, 113, 1, ts) == 0           # clock_gettime(MONOTONIC, ts)
    sec0 = struct.unpack_from("<Q", bytes(cpu.m.rb(ts, 8)))[0]
    nsec0 = struct.unpack_from("<Q", bytes(cpu.m.rb(ts + 8, 8)))[0]
    assert (sec0, nsec0) == (0, 1000)
    _svc(cpu, 113, 1, ts)
    nsec1 = struct.unpack_from("<Q", bytes(cpu.m.rb(ts + 8, 8)))[0]
    assert nsec1 == 1005                         # monoton, fixierbarer Schritt


def test_getrandom_deterministic_same_seed():
    out = []
    for _ in range(2):
        eng = _eng()
        os_svc = NativeOsService(publish=False)
        os_svc.install(eng, config={"rand_seed": 0xABCDEF})
        cpu = eng.cpu
        _svc(cpu, 278, _SCRATCH, 24, 0)         # getrandom(buf, 24, 0)
        out.append(bytes(cpu.m.rb(_SCRATCH, 24)))
    assert out[0] == out[1]                      # reproduzierbar
    assert len(set(out[0])) > 1                  # nicht konstant/null


def test_identity_from_config():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng, config={"uid": 10234, "pid": 4321, "tid": 4399})
    cpu = eng.cpu
    assert _svc(cpu, 174) == 10234               # getuid
    assert _svc(cpu, 172) == 4321                # getpid
    assert _svc(cpu, 178) == 4399                # gettid


def test_seed_policy_overrides_handler():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    # Policy fuer newfstatat (79): ret 0 + statbuf (x2) nullen — gewinnt gegen Handler
    os_svc.install(eng, seed_policy={79: {"ret": 0, "zero_arg": 2, "zero_len": 16}},
                   default_ret=0)
    cpu = eng.cpu
    cpu.m.wb(_SCRATCH, b"\xff" * 16)
    assert _svc(cpu, 79, 0xFFFFFF9C, 0, _SCRATCH, 0) == 0
    assert bytes(cpu.m.rb(_SCRATCH, 16)) == b"\x00" * 16
    assert os_svc.summary(eng)["by_policy"] == 1


def test_unknown_syscall_falls_to_default():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng, default_ret=7)
    cpu = eng.cpu
    assert _svc(cpu, 9999, 0, 0, 0) == 7
    s = os_svc.summary(eng)
    assert s["by_default"] == 1 and s["by_handler"] == 0


def test_log_schema_and_handled_by():
    eng = _eng()
    os_svc = NativeOsService(publish=False)
    os_svc.install(eng)
    cpu = eng.cpu
    _svc(cpu, 174)                               # getuid -> handler
    log = os_svc.get_syscall_log(eng)
    assert log and set(log[0]) == {"nr", "args", "pc", "ret", "handled_by"}
    assert log[0]["handled_by"] == "handler" and log[0]["nr"] == 174


def test_handler_dispatch_unknown_returns_false():
    # Direkter Vertragstest des Handlers (ohne Service).
    eng = _eng()
    st = type("S", (), {})()
    st.vfs = {}; st.fds = {}; st.next_fd = 3; st.mmaps = []
    st.mmap_next = MMAP_ARENA
    st.config = {"uid": 1, "pid": 2, "tid": 3, "statbuf_len": 128, "links": {}}
    st.rng = 1
    h = LinuxArm64SyscallHandler(st)
    assert h.dispatch(9999, eng.cpu, eng.mem, iface.FakeEnvironment()) is False
    assert h.dispatch(174, eng.cpu, eng.mem, iface.FakeEnvironment()) is True
    assert eng.cpu.x[0] == 1                      # uid aus config


# ---- libdb0c-Smoke (uebersprungen, wenn Lib fehlt) ------------------------
def _find_libdb0c():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = [
        os.path.join(here, "tools", "lib", "libdb0c.so"),
        os.path.join(here, "source", "org.nativescript.LibreLinkUp_5_1_1",
                     "base_unpacked_apktool", "lib", "arm64-v8a", "libdb0c.so"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def test_os_service_does_not_break_jni_onload():
    """Konvergenz-Smoke am echten Pruefstein: der os-Service liefert die Syscall-Schicht
    (statt ffi.install_syscall_hook); JNI_OnLoad laeuft mind. auf bekanntem Niveau weiter."""
    path = _find_libdb0c()
    if not path:
        pytest.skip("libdb0c.so nicht gefunden")
    from core.native.loader import ElfImage
    img = ElfImage.from_file(path)
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    NativeFfiService(publish=False).wire_imports(eng, img)
    eng.mem.write(0x60000000, b"\x00" * 4096)        # JNIEnv-Platzhalter

    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng, maps="72000000-72001000 r-xp 0 00:00 0 [anon:libdb0c]\n")
    r_inst = os_svc.install(eng)
    assert r_inst["ok"] and r_inst["handler"] == "LinuxArm64SyscallHandler"

    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    r = tr.debug_run(eng, entry, args=[0x60000000], maxins=4000, mode="block")
    assert r.get("ok"), r
    assert r.get("trace_len", 0) >= 50, r            # kein Rueckschritt ggue. ffi-Smoke
    # Syscall-Log ist wohlgeformt (Anzahl budgetabhaengig, daher kein >0-Assert).
    for e in os_svc.get_syscall_log(eng):
        assert set(e) == {"nr", "args", "pc", "ret", "handled_by"}
        assert e["handled_by"] in ("handler", "policy", "default")
