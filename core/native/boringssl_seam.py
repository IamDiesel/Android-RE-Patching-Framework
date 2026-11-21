# -*- coding: utf-8 -*-
"""core.native.boringssl_seam — G6: BoringSSL / OpenSSL FFI-Seam (SSL_write/SSL_read/SNI -> NetLog)."""
from __future__ import annotations

import struct
from typing import Any, Callable, Dict, Optional, Tuple

_M64 = (1 << 64) - 1
_SSL_CTRL_SET_TLSEXT_HOSTNAME = 55


def _mem_read(eng: Any, addr: int, n: int) -> bytes:
    if n <= 0 or not addr:
        return b""
    mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
    if mem is None:
        return b""
    if hasattr(mem, "read"):
        return bytes(mem.read(addr & _M64, n))
    if hasattr(mem, "rb"):
        return bytes(mem.rb(addr & _M64, n))
    return b""


def _mem_write(eng: Any, addr: int, data: bytes) -> None:
    if not data or not addr:
        return
    mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
    if mem is None:
        return
    if hasattr(mem, "write"):
        mem.write(addr & _M64, bytes(data))
    elif hasattr(mem, "wb"):
        mem.wb(addr & _M64, bytes(data))


def _read_cstr(eng: Any, ptr: int, max_len: int = 1024) -> str:
    if not ptr:
        return ""
    out = bytearray()
    for i in range(max_len):
        b = _mem_read(eng, (ptr & _M64) + i, 1)
        if not b or b[0] == 0:
            break
        out.append(b[0])
    return bytes(out).decode("utf-8", errors="replace")


def parse_http1_request(raw: bytes, default_host: str = "") -> Optional[Dict[str, Any]]:
    """Parst einen HTTP/1.x-Request aus BoringSSL/Socket-Payload Bytes."""
    if not raw:
        return None
    sep = b"\r\n\r\n" if b"\r\n\r\n" in raw else (b"\n\n" if b"\n\n" in raw else None)
    head_b = raw.split(sep, 1)[0] if sep else raw
    body_b = raw.split(sep, 1)[1] if sep else b""
    head_txt = head_b.decode("latin1", errors="replace")
    lines = head_txt.splitlines()
    if not lines:
        return None
    parts = lines[0].strip().split()
    if len(parts) < 2 or not parts[1].startswith(("/", "http://", "https://")):
        if parts[0].upper() not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            return None
    method = parts[0].upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        return None
    target = parts[1]
    headers: Dict[str, str] = {}
    for ln in lines[1:]:
        if ":" in ln:
            hk, hv = ln.split(":", 1)
            headers[hk.strip()] = hv.strip()
    host = headers.get("Host") or headers.get("host") or default_host or "localhost"
    if target.startswith(("http://", "https://")):
        url = target
    else:
        url = f"https://{host}{target if target.startswith('/') else '/' + target}"
    return {"method": method, "url": url, "headers": headers, "body": body_b, "host": host}


def _format_http1_response(code: int, message: str, headers: Dict[str, str], body: bytes) -> bytes:
    msg = message or ("OK" if int(code) < 400 else "ERR")
    b = bytes(body or b"")
    lines = [f"HTTP/1.1 {int(code)} {msg}"]
    seen_cl = False
    for k, v in (headers or {}).items():
        if str(k).lower() == "content-length":
            seen_cl = True
        lines.append(f"{k}: {v}")
    if not seen_cl:
        lines.append(f"Content-Length: {len(b)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin1", errors="replace") + b


def _record_os_ssl_event(eng: Any, ev: Any) -> None:
    try:
        os_st = getattr(eng, "_os", None)
        if os_st is not None:
            if getattr(os_st, "net", None) is None:
                cfg = getattr(os_st, "config", None) or {}
                os_st.net = {
                    "mode": str(cfg.get("net_mode", "record")),
                    "replay": dict(cfg.get("net_replay", {}) or {}),
                    "default": cfg.get("net_default"),
                    "log": [],
                }
            if isinstance(os_st.net, dict):
                os_st.net.setdefault("log", []).append(ev)
                return
        os_svc = getattr(eng, "os", None)
        if os_svc is not None and hasattr(os_svc, "_net"):
            net = os_svc._net()
            if isinstance(net, dict) and isinstance(net.get("log"), list):
                net["log"].append(ev)
                return
        if os_svc is not None and isinstance(getattr(os_svc, "net_events", None), list):
            os_svc.net_events.append(ev)
    except Exception:
        pass


def install_boringssl_seam(
    eng: Any,
    ffi: Any,
    stubs: Any = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Registriert BoringSSL/OpenSSL-FFI-Handler (`SSL_new`, `SSL_write`, `SSL_read`, `SSL_set_tlsext_host_name`)."""
    existing = getattr(eng, "_boringssl_seam", None)
    if isinstance(existing, dict):
        if stubs is not None:
            existing["stubs"] = stubs
        return existing

    state: Dict[str, Any] = {
        "next_ptr": 0x7F5500010000,
        "ssl_handles": {},
        "bio_to_ssl": {},
        "stubs": stubs,
        "requests": [],
    }
    eng._boringssl_seam = state

    def _alloc_handle() -> int:
        ptr = state["next_ptr"]
        state["next_ptr"] += 0x100
        state["ssl_handles"][ptr] = {
            "host": "",
            "fd": -1,
            "rx": bytearray(),
            "tx": bytearray(),
        }
        return ptr

    def _get_handle(ptr: int, allow_bio: bool = True) -> Optional[Dict[str, Any]]:
        k = int(ptr) & _M64
        if k in state["ssl_handles"]:
            return state["ssl_handles"][k]
        if allow_bio and k in state["bio_to_ssl"]:
            return state["ssl_handles"].get(state["bio_to_ssl"][k])
        return None

    def _ssl_new(cpu, _ffi):
        return _alloc_handle()

    def _ssl_free(cpu, _ffi):
        k = int(cpu.x[0]) & _M64
        state["ssl_handles"].pop(k, None)
        return 0

    def _ssl_set_fd(cpu, _ffi):
        k = int(cpu.x[0]) & _M64
        h = state["ssl_handles"].get(k) or state["ssl_handles"].setdefault(k, {"host": "", "fd": -1, "rx": bytearray(), "tx": bytearray()})
        h["fd"] = int(cpu.x[1])
        return 1

    def _ssl_set_bio(cpu, _ffi):
        k = int(cpu.x[0]) & _M64
        if k not in state["ssl_handles"]:
            state["ssl_handles"][k] = {"host": "", "fd": -1, "rx": bytearray(), "tx": bytearray()}
        for bptr in (int(cpu.x[1]) & _M64, int(cpu.x[2]) & _M64):
            if bptr:
                state["bio_to_ssl"][bptr] = k
        return 0

    def _ssl_set_sni(cpu, _ffi):
        k = int(cpu.x[0]) & _M64
        h = state["ssl_handles"].setdefault(k, {"host": "", "fd": -1, "rx": bytearray(), "tx": bytearray()})
        host = _read_cstr(eng, int(cpu.x[1]))
        if host:
            h["host"] = host
        return 1

    def _ssl_ctrl(cpu, _ffi):
        k = int(cpu.x[0]) & _M64
        cmd = int(cpu.x[1])
        if cmd == _SSL_CTRL_SET_TLSEXT_HOSTNAME and cpu.x[3]:
            h = state["ssl_handles"].setdefault(k, {"host": "", "fd": -1, "rx": bytearray(), "tx": bytearray()})
            host = _read_cstr(eng, int(cpu.x[3]))
            if host:
                h["host"] = host
            return 1
        return 1

    def _ssl_handshake(cpu, _ffi):
        return 1

    def _ssl_verify_res(cpu, _ffi):
        return 0  # X509_V_OK

    def _handle_write(ptr: int, buf_ptr: int, length: int) -> int:
        if length <= 0:
            return 0
        data = _mem_read(eng, buf_ptr, length)
        h = _get_handle(ptr, allow_bio=True)
        if h is None:
            h = state["ssl_handles"].setdefault(int(ptr) & _M64, {"host": "", "fd": -1, "rx": bytearray(), "tx": bytearray()})
        h["tx"].extend(data)
        parsed = parse_http1_request(bytes(h["tx"]), default_host=h.get("host", ""))
        if parsed is None:
            parsed = parse_http1_request(data, default_host=h.get("host", ""))
        if parsed is not None:
            h["tx"].clear()
            method = parsed["method"]
            url = parsed["url"]
            headers = parsed["headers"]
            body = parsed["body"]
            code, msg, rhdrs, rbody = 200, "OK", {"Content-Type": "application/json"}, b"{}"
            cur_stubs = state.get("stubs")
            if cur_stubs is not None and callable(getattr(cur_stubs, "net_fire", None)):
                try:
                    resp = cur_stubs.net_fire(method, url, headers, body, source="native/ssl")
                except TypeError:
                    resp = cur_stubs.net_fire(method, url, headers, body)
                if resp is not None:
                    code = int(getattr(resp, "code", 200) or 200)
                    msg = str(getattr(resp, "message", "OK") or "OK")
                    rhdrs = dict(getattr(resp, "headers", None) or {})
                    rb = getattr(resp, "body", b"")
                    rbody = bytes(rb) if isinstance(rb, (bytes, bytearray)) else str(rb or "").encode("utf-8")
            raw_resp = _format_http1_response(code, msg, rhdrs, rbody)
            h["rx"].extend(raw_resp)
            rec = {
                "source": "native",
                "kind": "ssl_write",
                "method": method,
                "url": url,
                "code": code,
                "req_bytes": len(data),
                "resp_bytes": len(rbody),
                "raw": data[:256],
            }
            state["requests"].append(rec)
            _record_os_ssl_event(eng, ("ssl_send", int(ptr) & _M64, bytes(data)))
            if callable(log_fn):
                log_fn(f"> [BoringSSL] {method} {url} -> {code} ({len(rbody)} B)")
        else:
            _record_os_ssl_event(eng, ("ssl_send", int(ptr) & _M64, bytes(data)))
        return len(data)

    def _ssl_write(cpu, _ffi):
        return _handle_write(int(cpu.x[0]), int(cpu.x[1]), int(cpu.x[2]))

    def _ssl_write_ex(cpu, _ffi):
        n = _handle_write(int(cpu.x[0]), int(cpu.x[1]), int(cpu.x[2]))
        if cpu.x[3]:
            _mem_write(eng, int(cpu.x[3]), struct.pack("<Q", n & _M64))
        return 1 if n > 0 else 0

    def _handle_read(ptr: int, buf_ptr: int, num: int) -> int:
        if num <= 0:
            return 0
        h = _get_handle(ptr, allow_bio=True)
        if h is None or not h["rx"]:
            return 0
        take = min(num, len(h["rx"]))
        chunk = bytes(h["rx"][:take])
        del h["rx"][:take]
        _mem_write(eng, buf_ptr, chunk)
        return len(chunk)

    def _ssl_read(cpu, _ffi):
        return _handle_read(int(cpu.x[0]), int(cpu.x[1]), int(cpu.x[2]))

    def _ssl_read_ex(cpu, _ffi):
        n = _handle_read(int(cpu.x[0]), int(cpu.x[1]), int(cpu.x[2]))
        if cpu.x[3]:
            _mem_write(eng, int(cpu.x[3]), struct.pack("<Q", n & _M64))
        return 1 if n > 0 else 0

    handlers = {
        "SSL_new": _ssl_new,
        "SSL_free": _ssl_free,
        "SSL_set_fd": _ssl_set_fd,
        "SSL_set_bio": _ssl_set_bio,
        "SSL_set_tlsext_host_name": _ssl_set_sni,
        "SSL_ctrl": _ssl_ctrl,
        "SSL_connect": _ssl_handshake,
        "SSL_do_handshake": _ssl_handshake,
        "SSL_get_verify_result": _ssl_verify_res,
        "SSL_write": _ssl_write,
        "SSL_write_ex": _ssl_write_ex,
        "SSL_read": _ssl_read,
        "SSL_read_ex": _ssl_read_ex,
        "BIO_write": _ssl_write,
        "BIO_read": _ssl_read,
    }
    if ffi is not None and hasattr(ffi, "set_handler"):
        for sym, fn in handlers.items():
            ffi.set_handler(eng, sym, fn)
    state["handlers"] = handlers
    return state
