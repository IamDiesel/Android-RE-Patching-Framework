# -*- coding: utf-8 -*-
"""core.java.net_protocols_g7 — G7: WebSocket, Protobuf/gRPC Wire-Decoder, Cronet & libcurl FFI-Seam."""
from __future__ import annotations

import base64
import json
import os
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

from core.java.objects import JArray, JObject, JString

_M64 = (1 << 64) - 1


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _bytes_from(v: Any) -> bytes:
    if v is None:
        return b""
    data = getattr(v, "data", None)
    if data is not None:
        return bytes(int(x) & 0xFF for x in data)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, JObject) and "_data" in v.fields:
        return bytes(v.fields["_data"] or b"")
    if isinstance(v, JString):
        return (v.text or "").encode("utf-8", errors="replace")
    return b""


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    res = 0
    shift = 0
    n = len(buf)
    while pos < n and shift <= 63:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7
    raise ValueError("truncated varint")


def decode_protobuf_wire(
    raw: bytes,
    schema: Optional[Dict[int, str]] = None,
    depth: int = 0,
) -> Dict[str, Any]:
    """Dekodiert gRPC-Frames (5B Prefix) und Protobuf-Wire-Felder deterministisch zu JSON-faehigem Dict."""
    if not raw or depth > 4:
        return {}
    data = bytes(raw)
    grpc_meta: Dict[str, Any] = {}
    if len(data) >= 5 and data[0] in (0, 1):
        flen = struct.unpack(">I", data[1:5])[0]
        if 0 <= flen <= len(data) - 5 and flen > 0:
            grpc_meta = {"_grpc_compressed": bool(data[0]), "_grpc_len": flen}
            data = data[5:5 + flen]

    out: Dict[str, Any] = {}
    if grpc_meta:
        out.update(grpc_meta)
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _read_varint(data, pos)
        except Exception:
            break
        field_no = tag >> 3
        wire_type = tag & 0x7
        if field_no <= 0:
            break
        key = (schema or {}).get(field_no, f"f{field_no}")
        if wire_type == 0:
            try:
                val, pos = _read_varint(data, pos)
            except Exception:
                break
        elif wire_type == 1:
            if pos + 8 > n:
                break
            val = struct.unpack("<Q", data[pos:pos + 8])[0]
            pos += 8
        elif wire_type == 2:
            try:
                ln, pos = _read_varint(data, pos)
            except Exception:
                break
            if ln < 0 or pos + ln > n:
                break
            sub = data[pos:pos + ln]
            pos += ln
            try:
                s = sub.decode("utf-8")
                if all(ch.isprintable() or ch in "\r\n\t" for ch in s):
                    val: Any = s
                else:
                    nested = decode_protobuf_wire(sub, None, depth + 1)
                    val = nested if nested and "_parse_err" not in nested else {"hex": sub[:64].hex(), "len": len(sub)}
            except Exception:
                nested = decode_protobuf_wire(sub, None, depth + 1) if len(sub) >= 2 else {}
                val = nested if (nested and len(nested) >= 1 and "_parse_err" not in nested) else {"hex": sub[:64].hex(), "len": len(sub)}
        elif wire_type == 5:
            if pos + 4 > n:
                break
            val = struct.unpack("<I", data[pos:pos + 4])[0]
            pos += 4
        else:
            break
        if key in out:
            prev = out[key]
            out[key] = (prev + [val]) if isinstance(prev, list) else [prev, val]
        else:
            out[key] = val
    return out


_SMALI_FIELD_RE = re.compile(r'const-string\s+\w+,\s*"([a-zA-Z_][a-zA-Z0-9_]{1,40})_?"')


def extract_smali_protobuf_schema(smali_text: str) -> Dict[int, str]:
    """Extrahiert Feldnamen aus Smali-`newMessageInfo`- / Descriptor-Arrays heuristisch."""
    if not smali_text:
        return {}
    names: List[str] = []
    in_info = False
    for ln in smali_text.splitlines():
        if "newMessageInfo" in ln or "dynamicMethod" in ln:
            in_info = True
        if in_info:
            m = _SMALI_FIELD_RE.search(ln)
            if m:
                nm = m.group(1).rstrip("_")
                if nm not in names and not nm.startswith(("zz", "bitField")):
                    names.append(nm)
        if ".end method" in ln:
            in_info = False
    return {i + 1: nm for i, nm in enumerate(names)}


def _record_ws_netlog(stubs: Any, method: str, url: str, headers: Dict[str, str], body: bytes, resp_code: int = 101) -> None:
    if stubs is None:
        return
    try:
        from core.java.net_recorder import NetRequest, NetResponse
        nl = getattr(stubs, "_net_log", None) or getattr(stubs, "net_log", None)
        if nl is not None and hasattr(nl, "record"):
            nl.record(NetRequest(method, url, headers, body), NetResponse(resp_code, headers, b"", "WS"))
    except Exception:
        pass


def inject_websocket_message(stubs: Any, ws_or_url: Any = None, payload: Any = "") -> bool:
    """Injiziert eine eingehende WebSocket-Nachricht (str/bytes) direkt an `WebSocketListener.onMessage`."""
    if stubs is None:
        return False
    sockets = getattr(stubs, "_websockets", None) or []
    target = None
    if isinstance(ws_or_url, JObject):
        target = next((s for s in sockets if s.get("ws") is ws_or_url), None)
    elif isinstance(ws_or_url, str) and ws_or_url:
        target = next((s for s in reversed(sockets) if ws_or_url in s.get("url", "")), None)
    elif sockets:
        target = sockets[-1]
    if target is None:
        return False
    ws = target["ws"]
    listener = target.get("listener")
    url = target.get("url", "")
    is_bin = isinstance(payload, (bytes, bytearray))
    raw_b = bytes(payload) if is_bin else str(payload).encode("utf-8", errors="replace")
    target.setdefault("rx", []).append(payload)
    _record_ws_netlog(stubs, "WS_RECV", url, {}, raw_b, resp_code=200)
    vm = getattr(stubs, "vm", None)
    if isinstance(listener, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
        try:
            if is_bin:
                bs = JObject("Lokio/ByteString;", {"_data": raw_b})
                vm.invoke_virtual(listener, "onMessage", "(Lokhttp3/WebSocket;Lokio/ByteString;)V", (listener, ws, bs))
            else:
                vm.invoke_virtual(listener, "onMessage", "(Lokhttp3/WebSocket;Ljava/lang/String;)V", (listener, ws, JString(str(payload))))
            return True
        except Exception:
            return False
    return True


def install_g7_java_stubs(stubs: Any) -> None:
    """Registriert okio.ByteString, OkHttp WebSocket, Cronet & gRPC Java-Stubs."""
    if stubs is None or getattr(stubs, "_g7_java_installed", False):
        return
    stubs._g7_java_installed = True
    stubs.__dict__.setdefault("_websockets", [])

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # okio.ByteString
    BS = "Lokio/ByteString;"
    _add(BS, "encodeUtf8", f"(Ljava/lang/String;){BS}", lambda a: JObject(BS, {"_data": _txt(a[0]).encode("utf-8") if a else b""}))
    _add(BS, "of", f"([B){BS}", lambda a: JObject(BS, {"_data": _bytes_from(a[0]) if a else b""}))
    _add(BS, "decodeHex", f"(Ljava/lang/String;){BS}", lambda a: JObject(BS, {"_data": bytes.fromhex(_txt(a[0])) if a else b""}))
    _add(BS, "decodeBase64", f"(Ljava/lang/String;){BS}", lambda a: JObject(BS, {"_data": base64.b64decode(_txt(a[0]) or b"") if a else b""}))
    _add(BS, "utf8", "()Ljava/lang/String;", lambda a: JString((a[0].fields.get("_data", b"") if a and isinstance(a[0], JObject) else b"").decode("utf-8", "replace")))
    _add(BS, "hex", "()Ljava/lang/String;", lambda a: JString((a[0].fields.get("_data", b"") if a and isinstance(a[0], JObject) else b"").hex()))
    _add(BS, "base64", "()Ljava/lang/String;", lambda a: JString(base64.b64encode(a[0].fields.get("_data", b"") if a and isinstance(a[0], JObject) else b"").decode("ascii")))
    _add(BS, "toByteArray", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_data", b""))) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(BS, "size", "()I", lambda a: len(a[0].fields.get("_data", b"")) if a and isinstance(a[0], JObject) else 0)

    # OkHttp WebSocket
    WS = "Lokhttp3/WebSocket;"
    OK = "Lokhttp3/OkHttpClient;"

    def _new_ws(a):
        req = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        listener = a[2] if len(a) > 2 else None
        url = ""
        if req is not None:
            url = str(req.fields.get("url") or req.fields.get("_url") or "")
            if not url and isinstance(req.fields.get("$url"), JObject):
                url = str(req.fields["$url"].fields.get("_uri", ""))
        ws_obj = JObject(WS, {"_url": url, "_req": req, "_listener": listener, "_tx": [], "_rx": [], "_closed": False})
        stubs._websockets.append({"ws": ws_obj, "url": url, "listener": listener, "tx": ws_obj.fields["_tx"], "rx": ws_obj.fields["_rx"]})
        _record_ws_netlog(stubs, "WS_OPEN", url, {}, b"", resp_code=101)
        vm = getattr(stubs, "vm", None)
        if isinstance(listener, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                resp = JObject("Lokhttp3/Response;", {"code": 101, "message": JString("Switching Protocols")})
                vm.invoke_virtual(listener, "onOpen", f"({WS}Lokhttp3/Response;)V", (listener, ws_obj, resp))
            except Exception:
                pass
        return ws_obj

    _add(OK, "newWebSocket", f"(Lokhttp3/Request;Lokhttp3/WebSocketListener;){WS}", _new_ws)

    def _ws_send(a, is_bytes=False):
        if len(a) < 2 or not isinstance(a[0], JObject):
            return 0
        ws = a[0]
        url = ws.fields.get("_url", "")
        raw_b = _bytes_from(a[1]) if is_bytes else _txt(a[1]).encode("utf-8", errors="replace")
        ws.fields.setdefault("_tx", []).append(raw_b if is_bytes else _txt(a[1]))
        _record_ws_netlog(stubs, "WS_SEND", url, {}, raw_b, resp_code=101)
        if callable(getattr(stubs, "net_fire", None)) and url.startswith(("http://", "https://", "ws://", "wss://")):
            try:
                stubs.net_fire("WS_SEND", url, {}, raw_b)
            except Exception:
                pass
        return 1

    _add(WS, "send", "(Ljava/lang/String;)Z", lambda a: _ws_send(a, False))
    _add(WS, "send", f"({BS})Z", lambda a: _ws_send(a, True))
    _add(WS, "close", "(ILjava/lang/String;)Z", lambda a: (a[0].fields.__setitem__("_closed", True), _record_ws_netlog(stubs, "WS_CLOSE", a[0].fields.get("_url", ""), {}, b"", resp_code=1000), 1)[2] if a and isinstance(a[0], JObject) else 1)
    _add(WS, "cancel", "()V", lambda a: a[0].fields.__setitem__("_closed", True) if a and isinstance(a[0], JObject) else None)
    _add(WS, "request", "()Lokhttp3/Request;", lambda a: a[0].fields.get("_req") if a and isinstance(a[0], JObject) else None)

    # Cronet Engine
    CB = "Lorg/chromium/net/CronetEngine$Builder;"
    CE = "Lorg/chromium/net/CronetEngine;"
    URB = "Lorg/chromium/net/UrlRequest$Builder;"
    UR = "Lorg/chromium/net/UrlRequest;"
    _add(CB, "<init>", "(Landroid/content/Context;)V", lambda a: a[0])
    _add(CB, "build", f"(){CE}", lambda a: JObject(CE, {}))
    _add(CE, "newUrlRequestBuilder", f"(Ljava/lang/String;Lorg/chromium/net/UrlRequest$Callback;Ljava/util/concurrent/Executor;){URB}",
         lambda a: JObject(URB, {"_url": _txt(a[1]) if len(a) > 1 else "", "_cb": a[2] if len(a) > 2 else None, "_method": "GET", "_headers": {}, "_body": b""}))
    _add(URB, "setHttpMethod", f"(Ljava/lang/String;){URB}", lambda a: (a[0].fields.__setitem__("_method", _txt(a[1])), a[0])[1])
    _add(URB, "addHeader", f"(Ljava/lang/String;Ljava/lang/String;){URB}", lambda a: (a[0].fields.setdefault("_headers", {}).__setitem__(_txt(a[1]), _txt(a[2])), a[0])[1])
    _add(URB, "build", f"(){UR}", lambda a: JObject(UR, dict(a[0].fields) if a and isinstance(a[0], JObject) else {}))

    def _cronet_start(a):
        if not a or not isinstance(a[0], JObject):
            return None
        f = a[0].fields
        url = f.get("_url", "")
        meth = f.get("_method", "GET")
        hdrs = f.get("_headers") or {}
        if callable(getattr(stubs, "net_fire", None)) and url:
            resp = stubs.net_fire(meth, url, hdrs, f.get("_body") or b"")
            cb = f.get("_cb")
            vm = getattr(stubs, "vm", None)
            if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
                info = JObject("Lorg/chromium/net/UrlResponseInfo;", {"_code": int(getattr(resp, "code", 200) or 200), "_url": url})
                try:
                    vm.invoke_virtual(cb, "onResponseStarted", f"({UR}Lorg/chromium/net/UrlResponseInfo;)V", (cb, a[0], info))
                    vm.invoke_virtual(cb, "onSucceeded", f"({UR}Lorg/chromium/net/UrlResponseInfo;)V", (cb, a[0], info))
                except Exception:
                    pass
        return None

    _add(UR, "start", "()V", _cronet_start)
    _add(UR, "followRedirect", "()V", lambda a: None)
    _add(UR, "read", "(Ljava/nio/ByteBuffer;)V", lambda a: None)
    _add("Lorg/chromium/net/UrlResponseInfo;", "getHttpStatusCode", "()I", lambda a: int(a[0].fields.get("_code", 200)) if a and isinstance(a[0], JObject) else 200)

    # gRPC ManagedChannel + ClientCalls
    MCB = "Lio/grpc/ManagedChannelBuilder;"
    MC = "Lio/grpc/ManagedChannel;"
    _add(MCB, "forTarget", f"(Ljava/lang/String;){MCB}", lambda a: JObject(MCB, {"_target": _txt(a[0]) if a else "localhost:443"}))
    _add(MCB, "forAddress", f"(Ljava/lang/String;I){MCB}", lambda a: JObject(MCB, {"_target": f"{_txt(a[0])}:{int(a[1])}" if len(a) > 1 else "localhost:443"}))
    _add(MCB, "usePlaintext", f"(){MCB}", lambda a: a[0] if a else None)
    _add(MCB, "build", f"(){MC}", lambda a: JObject(MC, {"_target": a[0].fields.get("_target", "localhost") if a and isinstance(a[0], JObject) else "localhost"}))

    def _grpc_unary(a):
        ch = a[0] if a else None
        md = a[1] if len(a) > 1 else None
        req_obj = a[3] if len(a) > 3 else None
        target = ch.fields.get("_target", "localhost") if isinstance(ch, JObject) else "localhost"
        mname = md.fields.get("_name", "Service/Call") if isinstance(md, JObject) else "Service/Call"
        url = f"https://{target.rstrip('/')}/{str(mname).lstrip('/')}"
        raw = req_obj.fields.get("_bytes", b"") if isinstance(req_obj, JObject) else _bytes_from(req_obj)
        if callable(getattr(stubs, "net_fire", None)):
            resp = stubs.net_fire("POST", url, {"Content-Type": "application/grpc"}, raw)
            rb = getattr(resp, "body", b"") or b""
            return JObject("Lcom/google/protobuf/GeneratedMessageLite;", {"_bytes": bytes(rb)})
        return req_obj

    _add("Lio/grpc/stub/ClientCalls;", "blockingUnaryCall",
         "(Lio/grpc/Channel;Lio/grpc/MethodDescriptor;Lio/grpc/CallOptions;Ljava/lang/Object;)Ljava/lang/Object;",
         _grpc_unary)


def install_libcurl_seam(eng: Any, ffi: Any, stubs: Any = None, log_fn=None) -> Dict[str, Any]:
    """Registriert native libcurl-FFI-Handler (`curl_easy_*`) fuer ARM64 -> NetLog & stubs.net_fire."""
    existing = getattr(eng, "_libcurl_seam", None)
    if isinstance(existing, dict):
        if stubs is not None:
            existing["stubs"] = stubs
        return existing

    state: Dict[str, Any] = {"next_ptr": 0x7F6600010000, "handles": {}, "slists": {}, "stubs": stubs}
    eng._libcurl_seam = state

    def _read_cstr(ptr: int, max_len: int = 2048) -> str:
        if not ptr:
            return ""
        mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
        if mem is None:
            return ""
        out = bytearray()
        for i in range(max_len):
            b = bytes(mem.read((ptr & _M64) + i, 1)) if hasattr(mem, "read") else bytes(mem.rb((ptr & _M64) + i, 1))
            if not b or b[0] == 0:
                break
            out.append(b[0])
        return bytes(out).decode("utf-8", errors="replace")

    def _curl_init(_cpu, _ffi):
        p = state["next_ptr"]
        state["next_ptr"] += 0x100
        state["handles"][p] = {"url": "", "method": "GET", "headers": {}, "body": b"", "http_code": 200, "resp_body": b"", "rx_buf_ptr": 0}
        return p

    def _curl_setopt(cpu, _ffi):
        h = state["handles"].get(int(cpu.x[0]) & _M64)
        if h is None:
            return 0
        opt = int(cpu.x[1]) & 0xFFFFFFFF
        arg = int(cpu.x[2]) & _M64
        # Standard libcurl CURLOPT_* constants
        if opt == 10002:   # CURLOPT_URL
            h["url"] = _read_cstr(arg)
        elif opt == 10015: # CURLOPT_POSTFIELDS
            sz = int(h.get("postfields_size", 0))
            mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
            if sz > 0 and mem is not None:
                h["body"] = bytes(mem.read(arg, sz)) if hasattr(mem, "read") else bytes(mem.rb(arg, sz))
            else:
                h["body"] = _read_cstr(arg).encode("utf-8", errors="replace")
            if h["method"] == "GET":
                h["method"] = "POST"
        elif opt == 60:    # CURLOPT_POSTFIELDSIZE
            h["postfields_size"] = arg
        elif opt == 47 and arg: # CURLOPT_POST
            h["method"] = "POST"
        elif opt == 10023: # CURLOPT_HTTPHEADER (curl_slist*)
            for ln in (state["slists"].get(arg) or ()):
                if ":" in ln:
                    hk, hv = ln.split(":", 1)
                    h["headers"][hk.strip()] = hv.strip()
        elif opt == 10036: # CURLOPT_CUSTOMREQUEST
            h["method"] = _read_cstr(arg).upper() or h["method"]
        elif opt == 10001: # CURLOPT_WRITEDATA
            h["rx_buf_ptr"] = arg
        return 0

    def _curl_slist_append(cpu, _ffi):
        ptr = int(cpu.x[0]) & _M64
        txt = _read_cstr(int(cpu.x[1]) & _M64)
        if not ptr or ptr not in state["slists"]:
            ptr = state["next_ptr"]
            state["next_ptr"] += 0x40
            state["slists"][ptr] = []
        if txt:
            state["slists"][ptr].append(txt)
        return ptr

    def _curl_perform(cpu, _ffi):
        h = state["handles"].get(int(cpu.x[0]) & _M64)
        if h is None:
            return 0
        url = h.get("url") or ""
        meth = h.get("method") or "GET"
        hdrs = dict(h.get("headers") or {})
        body = bytes(h.get("body") or b"")
        code = 200
        rbody = b"{}"
        cur_stubs = state.get("stubs")
        if cur_stubs is not None and callable(getattr(cur_stubs, "net_fire", None)) and url:
            resp = cur_stubs.net_fire(meth, url, hdrs, body)
            if resp is not None:
                code = int(getattr(resp, "code", 200) or 200)
                rb = getattr(resp, "body", b"")
                rbody = bytes(rb) if isinstance(rb, (bytes, bytearray)) else str(rb or "").encode("utf-8")
        h["http_code"] = code
        h["resp_body"] = rbody
        if h.get("rx_buf_ptr"):
            mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
            if mem is not None:
                try:
                    if hasattr(mem, "write"):
                        mem.write(h["rx_buf_ptr"], rbody)
                    elif hasattr(mem, "wb"):
                        mem.wb(h["rx_buf_ptr"], rbody)
                except Exception:
                    pass
        try:
            os_st = getattr(eng, "_os", None)
            if os_st is not None:
                if getattr(os_st, "net", None) is None:
                    os_st.net = {"mode": "record", "replay": {}, "default": None, "log": []}
                os_st.net.setdefault("log", []).append(("curl_send", url, f"{meth} {url} -> {code}".encode("utf-8")))
        except Exception:
            pass
        if callable(log_fn):
            log_fn(f"> [libcurl] {meth} {url} -> {code} ({len(rbody)} B)")
        return 0

    def _curl_getinfo(cpu, _ffi):
        h = state["handles"].get(int(cpu.x[0]) & _M64)
        info = int(cpu.x[1]) & 0xFFFFFFFF
        out_p = int(cpu.x[2]) & _M64
        if h is not None and out_p and info in (0x200002, 2097154): # CURLINFO_RESPONSE_CODE
            mem = getattr(eng, "mem", None) or getattr(getattr(eng, "cpu", None), "m", None)
            if mem is not None:
                b8 = struct.pack("<Q", int(h.get("http_code", 200)))
                if hasattr(mem, "write"):
                    mem.write(out_p, b8)
                elif hasattr(mem, "wb"):
                    mem.wb(out_p, b8)
        return 0

    for sym, fn in (
        ("curl_global_init", lambda _c, _f: 0),
        ("curl_global_cleanup", lambda _c, _f: 0),
        ("curl_easy_init", _curl_init),
        ("curl_easy_setopt", _curl_setopt),
        ("curl_slist_append", _curl_slist_append),
        ("curl_slist_free_all", lambda _c, _f: 0),
        ("curl_easy_perform", _curl_perform),
        ("curl_easy_getinfo", _curl_getinfo),
        ("curl_easy_cleanup", lambda _c, _f: 0),
        ("curl_easy_strerror", lambda _c, _f: 0),
    ):
        ffi.set_handler(eng, sym, fn)
    return state
