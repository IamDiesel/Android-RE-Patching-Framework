# -*- coding: utf-8 -*-
"""MCP-OS-1: erweiterte native_os_service.summary() (Coverage/Trace/Net/...)."""
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


def _booted(**cfg):
    eng = NativeEmuService(publish=False).new_engine()
    osv = NativeOsService(publish=False)
    osv.install(eng)
    eng._os.config.update(cfg)
    eng._os.net = None
    return eng, osv


def test_summary_has_new_fields():
    eng, osv = _booted(trace_syscalls=True, syscall_policy={"getpid": "deny"})
    cpu = eng.cpu
    _svc(cpu, 174)                    # getuid
    _svc(cpu, 174)                    # getuid
    _svc(cpu, 172)                    # getpid (deny)
    s = osv.summary(eng)
    assert s["coverage"]["distinct"] >= 2
    names = dict(s["coverage"]["top"])
    assert names.get("getuid") == 2
    assert s["trace"]["len"] > 0 and isinstance(s["trace"]["last"], list)
    assert s["policy"].get("getpid") == "deny"
    assert "net" in s and "threads" in s and "signals" in s and "auxv" in s and "brk" in s


def test_summary_net_peers_after_connect():
    resp = b"OK"
    eng, osv = _booted(net_mode="replay", net_replay={"10.0.0.9:443": resp})
    cpu = eng.cpu
    fd = _svc(cpu, 198, 2, 1, 0)      # socket
    cpu.m.wb(_SC, struct.pack("<H", 2) + struct.pack(">H", 443) + bytes([10, 0, 0, 9]) + b"\x00" * 8)
    _svc(cpu, 203, fd, _SC, 16)       # connect
    s = osv.summary(eng)
    assert s["net"]["mode"] == "replay"
    assert "10.0.0.9:443" in s["net"]["peers"]
    assert s["net"]["sockets"] >= 1


def test_summary_trace_off_empty():
    eng, osv = _booted()
    cpu = eng.cpu
    _svc(cpu, 174)
    s = osv.summary(eng)
    assert s["trace"]["len"] == 0 and s["trace"]["last"] == []
