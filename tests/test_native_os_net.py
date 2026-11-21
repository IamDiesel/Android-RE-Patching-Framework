# -*- coding: utf-8 -*-
"""OS-8 Tests: virtueller Socket-Stack mit Record/Replay."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_os_service import NativeOsService

_M64 = (1 << 64) - 1
_SC = 0x70000

NR_SOCKET = 198
NR_CONNECT = 203
NR_SENDTO = 206
NR_RECVFROM = 207
NR_SENDMSG = 211
NR_GETPEERNAME = 205
NR_READ = 63
NR_WRITE = 64


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
    if cfg:
        eng._os.config.update(cfg)
        eng._os.net = None   # neu bauen mit cfg
    return eng


def _sockaddr_in(cpu, addr, host, port):
    octets = bytes(int(x) for x in host.split("."))
    cpu.m.wb(addr, struct.pack("<H", 2) + struct.pack(">H", port) + octets + b"\x00" * 8)


# --------------------------------------------------------------------------
def test_socket_connect_replay_recv():
    resp = b"HTTP/1.1 200 OK\r\n\r\n{\"glucose\":120}"
    eng = _booted(net_mode="replay", net_replay={"10.0.0.5:443": resp})
    cpu = eng.cpu
    fd = _svc(cpu, NR_SOCKET, 2, 1, 0)       # AF_INET, SOCK_STREAM
    assert fd >= 3
    addr = _SC
    _sockaddr_in(cpu, addr, "10.0.0.5", 443)
    assert _svc(cpu, NR_CONNECT, fd, addr, 16) == 0
    buf = _SC + 0x1000
    n = _svc(cpu, NR_RECVFROM, fd, buf, 512, 0, 0, 0)
    assert n == len(resp)
    assert bytes(cpu.m.rb(buf, n)) == resp


def test_send_logs_tx():
    eng = _booted()
    cpu = eng.cpu
    fd = _svc(cpu, NR_SOCKET, 2, 1, 0)
    payload = b"GET /glucose HTTP/1.1\r\n\r\n"
    src = _SC
    cpu.m.wb(src, payload)
    n = _svc(cpu, NR_SENDTO, fd, src, len(payload), 0, 0, 0)
    assert n == len(payload)
    sock = eng._os.sockets[fd]
    assert bytes(sock["tx"]) == payload


def test_read_write_route_to_socket():
    resp = b"PONG"
    eng = _booted(net_mode="replay", net_replay={"1.1.1.1:80": resp})
    cpu = eng.cpu
    fd = _svc(cpu, NR_SOCKET, 2, 1, 0)
    addr = _SC
    _sockaddr_in(cpu, addr, "1.1.1.1", 80)
    _svc(cpu, NR_CONNECT, fd, addr, 16)
    # write() -> tx
    src = _SC + 0x40
    cpu.m.wb(src, b"PING")
    assert _svc(cpu, NR_WRITE, fd, src, 4) == 4
    assert bytes(eng._os.sockets[fd]["tx"]) == b"PING"
    # read() -> rx (Replay)
    buf = _SC + 0x1000
    n = _svc(cpu, NR_READ, fd, buf, 16)
    assert bytes(cpu.m.rb(buf, n)) == resp


def test_sendmsg_gathers_iov():
    eng = _booted()
    cpu = eng.cpu
    fd = _svc(cpu, NR_SOCKET, 2, 1, 0)
    # zwei iovec-Segmente
    seg1 = _SC + 0x100; seg2 = _SC + 0x200
    cpu.m.wb(seg1, b"AAAA"); cpu.m.wb(seg2, b"BBBBBB")
    iov = _SC + 0x300
    cpu.m.wb(iov, struct.pack("<QQ", seg1, 4) + struct.pack("<QQ", seg2, 6))
    msg = _SC + 0x400
    # msghdr: name=0,namelen=0,iov@16,iovlen@24
    hdr = bytearray(56)
    struct.pack_into("<Q", hdr, 16, iov)
    struct.pack_into("<Q", hdr, 24, 2)
    cpu.m.wb(msg, bytes(hdr))
    n = _svc(cpu, NR_SENDMSG, fd, msg, 0)
    assert n == 10
    assert bytes(eng._os.sockets[fd]["tx"]) == b"AAAABBBBBB"


def test_getpeername_returns_peer():
    eng = _booted()
    cpu = eng.cpu
    fd = _svc(cpu, NR_SOCKET, 2, 1, 0)
    addr = _SC
    _sockaddr_in(cpu, addr, "8.8.4.4", 53)
    _svc(cpu, NR_CONNECT, fd, addr, 16)
    out = _SC + 0x1000; outlen = _SC + 0x1100
    cpu.m.wb(outlen, struct.pack("<I", 16))
    _svc(cpu, NR_GETPEERNAME, fd, out, outlen)
    fam = struct.unpack("<H", bytes(cpu.m.rb(out, 2)))[0]
    port = struct.unpack(">H", bytes(cpu.m.rb(out + 2, 2)))[0]
    ip = ".".join(str(b) for b in bytes(cpu.m.rb(out + 4, 4)))
    assert fam == 2 and port == 53 and ip == "8.8.4.4"
