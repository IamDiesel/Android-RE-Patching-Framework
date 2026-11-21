# -*- coding: utf-8 -*-
"""core.java.net_recorder — Netz-Säule (K-Track) für die gläserne App-Emulation.

Zeichnet den HTTP-/Socket-Verkehr auf, den die App über die Standard-Java-APIs macht
(`java.net.URL`/`HttpURLConnection`, `java.net.Socket`), und liefert **konfigurierbare Antworten**
(Record/Replay/Mock). Zielagnostisch, wie der View-Recorder ein Consumer am selben `DalvikVM`:
`install_net_stubs(stubs, responder, net_log)` hängt generische Stubs in `AndroidStubs` ein.
Headless testbar. Kein echter Netzzugriff — alles synthetisch + deterministisch.
"""
from __future__ import annotations

from dataclasses import dataclass
import json as _json
import time as _time


def _to_bytes(x):
    """JArray[B] / bytes / bytearray / list[int] -> bytes."""
    if x is None:
        return b""
    data = getattr(x, "data", None)
    if data is not None:                       # JArray
        return bytes([int(v) & 0xff for v in data])
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, list):
        return bytes([int(v) & 0xff for v in x])
    return b""


@dataclass
class NetCondition:
    """Netz-Bedingungen fuer den glaesernen Lauf: Online, Drosselung (Latenz + kbit/s),
    Offline (IOException + OS-8 ENETUNREACH) oder Timeout (SocketTimeoutException)."""

    mode: str = "online"          # "online" | "throttled" | "offline" | "timeout"
    latency_ms: int = 0           # kuenstliche Latenz je Request in ms
    kbps: int = 0                 # Bandbreiten-Limit in kbit/s (0 = unbegrenzt)
    offline_error: str = "Network is unreachable"

    def __post_init__(self):
        m = str(self.mode or "online").strip().lower()
        if m not in ("online", "throttled", "offline", "timeout"):
            m = "online"
        self.mode = m
        self.latency_ms = max(0, int(self.latency_ms or 0))
        self.kbps = max(0, int(self.kbps or 0))
        self.offline_error = str(self.offline_error or "Network is unreachable")

    def delay_seconds(self, nbytes: int = 0) -> float:
        if self.mode == "online":
            return 0.0
        base = self.latency_ms / 1000.0
        bw = (float(max(0, nbytes)) * 8.0 / (self.kbps * 1000.0)) if self.kbps > 0 else 0.0
        return min(30.0, max(0.0, base + bw))

    def to_dict(self):
        return {
            "mode": self.mode,
            "latency_ms": int(self.latency_ms),
            "kbps": int(self.kbps),
            "offline_error": self.offline_error,
        }

    @classmethod
    def from_dict(cls, d):
        if isinstance(d, NetCondition):
            return cls(**d.to_dict())
        if not isinstance(d, dict):
            return cls()
        return cls(
            mode=d.get("mode", "online"),
            latency_ms=d.get("latency_ms", 0),
            kbps=d.get("kbps", 0),
            offline_error=d.get("offline_error", "Network is unreachable"),
        )

    def apply_to_os(self, eng=None):
        """Synchronisiert das native OS-8-Netzwerkmodell mit diesem Java-Netz-Zustand."""
        if eng is None:
            return
        st = getattr(eng, "_os", None)
        if st is None:
            return
        net = getattr(st, "net", None)
        if isinstance(net, dict):
            net["mode"] = "offline" if self.mode in ("offline", "timeout") else "online"


class NetRequest:
    def __init__(self, method="GET", url="", headers=None, body=b""):
        self.method = method or "GET"
        self.url = url or ""
        self.headers = dict(headers or {})
        self.body = body or b""

    def to_dict(self):
        return {"method": self.method, "url": self.url, "headers": dict(self.headers),
                "body_len": len(self.body),
                "body_preview": self.body[:200].decode("utf-8", "replace"),
                "body": self.body[:65536].decode("utf-8", "replace")}

    def __repr__(self):
        return "NetRequest(%s %s, hdr=%d, body=%dB)" % (
            self.method, self.url, len(self.headers), len(self.body))


class NetResponse:
    def __init__(self, code=200, headers=None, body=b"", message="OK"):
        self.code = int(code)
        self.headers = dict(headers or {})
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body or b""
        self.message = message

    def to_dict(self):
        return {"code": self.code, "headers": dict(self.headers),
                "message": self.message,
                "body_len": len(self.body),
                "body_preview": self.body[:200].decode("utf-8", "replace"),
                "body": self.body[:65536].decode("utf-8", "replace")}


class NetLog:
    """Aufzeichnung aller Request/Response-Paare (Zeitachse/BP-Quelle)."""
    def __init__(self):
        self.entries = []          # list[{"req": NetRequest, "resp": NetResponse}]

    def record(self, req, resp):
        self.entries.append({"req": req, "resp": resp})
        return resp

    def to_list(self):
        return [{"req": e["req"].to_dict(), "resp": e["resp"].to_dict()} for e in self.entries]

    def __len__(self):
        return len(self.entries)


def default_responder(req):
    """Record-Modus-Default: nimmt jeden Request an, antwortet leer 200."""
    return NetResponse(200, {}, b"", "OK")


class ReplayResponder:
    """Liefert vordefinierte Antworten (Mock/Replay). Schluessel: exakt 'METHOD URL',
    nur URL, oder URL-Teilstring (fallback). Sonst `default` (oder leeres 200)."""
    def __init__(self, mapping=None, default=None):
        self.mapping = dict(mapping or {})      # key -> NetResponse
        self.default = default

    def add(self, key, resp):
        self.mapping[key] = resp
        return self

    def match(self, req):
        """Sucht einen passenden Mock-Eintrag (exakt oder Teilstring, optional 'METHOD /teil')."""
        req_m = (getattr(req, "method", "GET") or "GET").upper()
        req_u = getattr(req, "url", "") or ""
        req_full = "%s %s" % (req_m, req_u)
        for key in (req_full, req_u):
            if key in self.mapping:
                return self.mapping[key]
        for key, resp in self.mapping.items():
            if not key:
                continue
            if " " in key:
                km, kp = key.split(" ", 1)
                if km.upper() == req_m and kp and kp in req_u:
                    return resp
            elif key in req_u:
                return resp
        return None

    def __call__(self, req):
        hit = self.match(req)
        if hit is not None:
            return hit
        if callable(self.default):
            return self.default(req)
        return self.default if isinstance(self.default, NetResponse) else NetResponse(200, {}, b"", "OK")


class DbReplayResponder:
    """Liest aufgezeichnete Antworten aus `ApiDbService` (`api_traffic.db`) als Offline-Replay."""

    def __init__(self, api_db=None, fallback=None):
        self.api_db = api_db
        self.fallback = fallback

    def __call__(self, req):
        if self.api_db is not None:
            try:
                rows = self.api_db.get_requests(filter_text="")
            except Exception:
                rows = []
            want_m = (req.method or "GET").upper()
            want_u = req.url or ""
            # rows: id, ts, method, url, status, req_h, req_b, res_h, res_b, comment (neueste zuerst oder aufsteigend)
            for row in reversed(rows or ()):
                try:
                    rm = str(row[2] or "").upper()
                    ru = str(row[3] or "")
                    if ru == want_u and (not rm or rm == want_m):
                        hdrs = {}
                        try:
                            hdrs = _json.loads(row[7]) if row[7] else {}
                        except Exception:
                            hdrs = {}
                        return NetResponse(int(row[4] or 200), hdrs, row[8] or "", "Replay-DB")
                except Exception:
                    continue
            for row in reversed(rows or ()):
                try:
                    ru = str(row[3] or "")
                    if want_u and ru and (want_u in ru or ru in want_u):
                        hdrs = {}
                        try:
                            hdrs = _json.loads(row[7]) if row[7] else {}
                        except Exception:
                            hdrs = {}
                        return NetResponse(int(row[4] or 200), hdrs, row[8] or "", "Replay-DB")
                except Exception:
                    continue
        if callable(self.fallback):
            return self.fallback(req)
        return default_responder(req)


def install_net_stubs(stubs, responder=None, net_log=None, api_db=None, rules_path=None,
                      net_condition=None, url_breakpoints=None, os_eng=None):
    """Hängt generische Java-Netz-Stubs (URLConnection + OkHttp3 + RN-Bruecke) in `AndroidStubs` ein."""
    from core.java.objects import JObject, JString, JArray
    from core.java.dalvik import DalvikException

    log = net_log if net_log is not None else NetLog()
    cond_obj = NetCondition.from_dict(net_condition) if net_condition is not None else NetCondition()
    cond_obj.apply_to_os(os_eng)
    stubs.net_condition = cond_obj
    stubs.url_breakpoints = set(url_breakpoints or ())
    stubs._net_os_eng = os_eng

    def set_net_condition(new_cond):
        c = NetCondition.from_dict(new_cond)
        stubs.net_condition = c
        c.apply_to_os(getattr(stubs, "_net_os_eng", None))
        return c

    stubs.set_net_condition = set_net_condition

    def _mark_net(req, resp):
        vm = getattr(stubs, "vm", None)
        dbg = getattr(vm, "_dbg", None) if vm is not None else None
        if dbg is None or not hasattr(dbg, "markers"):
            return
        try:
            step = len(getattr(dbg, "timeline", []))
            lbl = "%s %s -> %s" % (req.method, req.url, getattr(resp, "code", ""))
            dbg.markers.append({"step": step, "kind": "net", "label": lbl})
        except Exception:
            pass
    resp_fn = responder if callable(responder) else default_responder
    stubs.net_log = log
    stubs.net_responder = resp_fn

    def _rules():
        if not rules_path:
            return []
        try:
            with open(rules_path, encoding="utf-8") as _fh:
                return _json.load(_fh) or []
        except Exception:
            return []

    def _s(v):
        return v.text if isinstance(v, JString) else ("" if v is None else str(v))

    # --- java.net.URL -----------------------------------------------------
    def _url_init(a):
        if a:
            u = a[0]
            if isinstance(u, JObject):
                u.fields["$url"] = _s(a[1]) if len(a) > 1 else ""
        return a[0] if a else None
    stubs.register("Ljava/net/URL;", "<init>", "(Ljava/lang/String;)V", _url_init)

    def _open_conn(a):
        u = a[0] if a else None
        url = ""
        raw_bytes = None
        if isinstance(u, JObject):
            url = u.fields.get("$url") or u.fields.get("path") or ""
            raw_bytes = u.fields.get("_bytes")
        conn = JObject("Ljava/net/HttpURLConnection;")
        fields = {"$url": url, "$method": "GET", "$headers": {},
                  "$body": bytearray(), "$resp": None}
        if raw_bytes is not None:
            fields["_bytes"] = raw_bytes
        conn.fields.update(fields)
        return conn
    stubs.register_by_name("openConnection", "()Ljava/net/URLConnection;", _open_conn)
    stubs.register_by_name("openConnection", "()Ljava/net/HttpURLConnection;", _open_conn)

    def _url_open_stream(a):
        u = a[0] if a else None
        if isinstance(u, JObject) and u.fields.get("_bytes") is not None:
            return JObject("Ljava/io/FileInputStream;", {
                "path": u.fields.get("path", ""), "pos": 0, "_mem": bytes(u.fields["_bytes"] or b"")
            })
        return _get_in([_open_conn(a)])
    stubs.register("Ljava/net/URL;", "openStream", "()Ljava/io/InputStream;", _url_open_stream)
    stubs.register_by_name("openStream", "()Ljava/io/InputStream;", _url_open_stream)

    # --- HttpURLConnection-Setter ----------------------------------------
    def _set_method(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            a[0].fields["$method"] = _s(a[1]).upper() or "GET"
        return None
    stubs.register_by_name("setRequestMethod", "(Ljava/lang/String;)V", _set_method)

    def _set_prop(a):
        if a and isinstance(a[0], JObject) and len(a) > 2:
            a[0].fields.setdefault("$headers", {})[_s(a[1])] = _s(a[2])
        return None
    stubs.register_by_name("setRequestProperty", "(Ljava/lang/String;Ljava/lang/String;)V", _set_prop)
    stubs.register_by_name("addRequestProperty", "(Ljava/lang/String;Ljava/lang/String;)V", _set_prop)

    for _n, _sig in (("setDoOutput", "(Z)V"), ("setDoInput", "(Z)V"), ("setUseCaches", "(Z)V"),
                     ("setConnectTimeout", "(I)V"), ("setReadTimeout", "(I)V"),
                     ("setInstanceFollowRedirects", "(Z)V"), ("connect", "()V"),
                     ("disconnect", "()V"), ("setChunkedStreamingMode", "(I)V"),
                     ("setFixedLengthStreamingMode", "(I)V")):
        stubs.register_by_name(_n, _sig, lambda a: None)

    # --- Output-Stream (Request-Body aufzeichnen) ------------------------
    def _get_out(a):
        conn = a[0] if a else None
        os_ = JObject("Ljava/io/OutputStream;")
        os_.fields["$conn"] = conn
        return os_

    def _out_target(a):
        if a and isinstance(a[0], JObject):
            conn = a[0].fields.get("$conn")
            if isinstance(conn, JObject):
                return conn.fields.setdefault("$body", bytearray())
        return None

    def _write_b(a):
        buf = _out_target(a)
        if buf is not None and len(a) > 1:
            buf.extend(_to_bytes(a[1]))
        return None

    def _write_bii(a):
        buf = _out_target(a)
        if buf is not None and len(a) > 3:
            data = _to_bytes(a[1]); off = int(a[2]); ln = int(a[3])
            buf.extend(data[off:off + ln])
        return None

    def _write_i(a):
        buf = _out_target(a)
        if buf is not None and len(a) > 1:
            buf.append(int(a[1]) & 0xff)
        return None
    stubs.register_by_name("getOutputStream", "()Ljava/io/OutputStream;", _get_out)
    stubs.register_by_name("write", "([B)V", _write_b)
    stubs.register_by_name("write", "([BII)V", _write_bii)
    stubs.register_by_name("write", "(I)V", _write_i)
    stubs.register_by_name("flush", "()V", lambda a: None)
    stubs.register_by_name("close", "()V", lambda a: None)

    # --- Request abfeuern (NetCondition + URL-BP + Rules + Responder + DB) ---
    def _publish_and_record(req, resp):
        log.record(req, resp)
        if api_db is not None:
            try:
                api_db.insert_packet({
                    "timestamp": _time.time(), "method": req.method, "url": req.url,
                    "status": resp.code,
                    "req_headers": _json.dumps(dict(req.headers)),
                    "req_body": req.body.decode("utf-8", "replace"),
                    "res_headers": _json.dumps(dict(resp.headers)),
                    "res_body": resp.body.decode("utf-8", "replace"),
                    "comment": "emuliert (Glass-Box)"})
            except Exception:
                pass
        try:
            from core.application.event_bus import EventBus
            EventBus.publish("NET_REQUEST", {"req": req.to_dict(), "resp": resp.to_dict()})
        except Exception:
            pass
        _mark_net(req, resp)

    def _fire_req(req):
        vm = getattr(stubs, "vm", None)
        dbg = getattr(vm, "_dbg", None) if vm is not None else None
        # URL-Breakpoint-Pruefung
        url_bps = getattr(stubs, "url_breakpoints", None) or ()
        if dbg is not None and url_bps:
            for pat in list(url_bps):
                if pat and str(pat) in (req.url or ""):
                    try:
                        dbg.pause_reason = "url_breakpoint"
                        dbg.pause()
                    except Exception:
                        pass
                    break

        _rl = _rules()
        for _r in _rl:
            if _r.get("url_match") and _r.get("url_match") in (req.url or ""):
                act = _r.get("action")
                if act == "replace_req_body":
                    req.body = (_r.get("payload", "") or "").encode("utf-8")
                elif act == "drop":
                    resp = NetResponse(0, {}, b"", "DROPPED")
                    _publish_and_record(req, resp)
                    raise DalvikException(
                        "java.io.IOException: Dropped by intercept rule",
                        payload=JObject("Ljava/io/IOException;", {"message": "Dropped by intercept rule"}),
                    )

        # Netz-Zustand (online | throttled | offline | timeout)
        nc = getattr(stubs, "net_condition", None)
        if nc is not None:
            nc.apply_to_os(getattr(stubs, "_net_os_eng", None))
            if nc.mode == "offline":
                resp = NetResponse(0, {}, b"", "OFFLINE")
                _publish_and_record(req, resp)
                raise DalvikException(
                    "java.io.IOException: " + nc.offline_error,
                    payload=JObject("Ljava/io/IOException;", {"message": nc.offline_error}),
                )
            if nc.mode == "timeout":
                d = nc.delay_seconds(len(req.body))
                if d > 0:
                    _time.sleep(min(d, 5.0))
                resp = NetResponse(0, {}, b"", "TIMEOUT")
                _publish_and_record(req, resp)
                raise DalvikException(
                    "java.net.SocketTimeoutException: timed out",
                    payload=JObject("Ljava/net/SocketTimeoutException;", {"message": "timed out"}),
                )

        try:
            resp = resp_fn(req)
        except Exception:
            resp = NetResponse(200, {}, b"", "OK")
        if not isinstance(resp, NetResponse):
            resp = NetResponse(200, {}, b"", "OK")

        for _r in _rl:
            if _r.get("url_match") and _r.get("url_match") in (req.url or ""):
                act = _r.get("action")
                if act == "replace_res_body":
                    resp.body = (_r.get("payload", "") or "").encode("utf-8")
                elif act == "replace_status":
                    try:
                        resp.code = int(_r.get("payload", resp.code))
                    except Exception:
                        pass

        if nc is not None and nc.mode == "throttled":
            d = nc.delay_seconds(len(req.body) + len(resp.body))
            if d > 0:
                _time.sleep(d)

        _publish_and_record(req, resp)
        return resp

    def _fire_direct(method="GET", url="", headers=None, body=b""):
        req = NetRequest(method, url, headers or {}, body or b"")
        return _fire_req(req)

    stubs.net_fire = _fire_direct

    def _fire(conn):
        if not isinstance(conn, JObject):
            return NetResponse(0, {}, b"", "")
        if conn.fields.get("$resp") is not None:
            return conn.fields["$resp"]
        req = NetRequest(conn.fields.get("$method", "GET"), conn.fields.get("$url", ""),
                         conn.fields.get("$headers", {}), bytes(conn.fields.get("$body", b"")))
        resp = _fire_req(req)
        conn.fields["$resp"] = resp
        return resp

    # --- OkHttp3-Stubs (speisen dasselbe `_fire_req` -> NetCondition + API-DB) ---
    stubs.register("Lokhttp3/Request$Builder;", "<init>", "()V",
                   lambda a: a[0].fields.update({"$method": "GET", "$url": "", "$headers": {}, "$body": b""}) if a and isinstance(a[0], JObject) else None)
    stubs.register("Lokhttp3/Request$Builder;", "url", "(Ljava/lang/String;)Lokhttp3/Request$Builder;",
                   lambda a: (a[0].fields.__setitem__("$url", _s(a[1])) or a[0]) if a and isinstance(a[0], JObject) and len(a) > 1 else (a[0] if a else None))
    stubs.register("Lokhttp3/Request$Builder;", "header", "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/Request$Builder;",
                   lambda a: (a[0].fields.setdefault("$headers", {}).__setitem__(_s(a[1]), _s(a[2])) or a[0]) if a and isinstance(a[0], JObject) and len(a) > 2 else (a[0] if a else None))
    stubs.register("Lokhttp3/Request$Builder;", "addHeader", "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/Request$Builder;",
                   lambda a: (a[0].fields.setdefault("$headers", {}).__setitem__(_s(a[1]), _s(a[2])) or a[0]) if a and isinstance(a[0], JObject) and len(a) > 2 else (a[0] if a else None))
    stubs.register("Lokhttp3/Request$Builder;", "method", "(Ljava/lang/String;Lokhttp3/RequestBody;)Lokhttp3/Request$Builder;",
                   lambda a: (a[0].fields.update({"$method": _s(a[1]).upper(), "$body": a[2].fields.get("$body", b"") if len(a) > 2 and isinstance(a[2], JObject) else b""}) or a[0]) if a and isinstance(a[0], JObject) else (a[0] if a else None))
    stubs.register("Lokhttp3/Request$Builder;", "build", "()Lokhttp3/Request;",
                   lambda a: JObject("Lokhttp3/Request;", dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject("Lokhttp3/Request;", {}))
    stubs.register("Lokhttp3/RequestBody;", "create", "(Lokhttp3/MediaType;Ljava/lang/String;)Lokhttp3/RequestBody;",
                   lambda a: JObject("Lokhttp3/RequestBody;", {"$body": (_s(a[1]) if len(a) > 1 else "").encode("utf-8")}))
    stubs.register("Lokhttp3/OkHttpClient;", "<init>", "()V", lambda a: None)
    stubs.register("Lokhttp3/OkHttpClient;", "newCall", "(Lokhttp3/Request;)Lokhttp3/Call;",
                   lambda a: JObject("Lokhttp3/RealCall;", {"$req": a[1] if len(a) > 1 else None}))

    def _okhttp_execute(a):
        call = a[0] if a else None
        req_obj = call.fields.get("$req") if isinstance(call, JObject) else None
        flds = req_obj.fields if isinstance(req_obj, JObject) else {}
        resp = _fire_direct(flds.get("$method", "GET"), flds.get("$url", ""), flds.get("$headers", {}), flds.get("$body", b""))
        body_obj = JObject("Lokhttp3/ResponseBody;", {"$body": resp.body})
        return JObject("Lokhttp3/Response;", {"$code": resp.code, "$msg": resp.message, "$body": body_obj, "$headers": resp.headers})
    stubs.register_by_name("execute", "()Lokhttp3/Response;", _okhttp_execute)
    stubs.register("Lokhttp3/Response;", "code", "()I", lambda a: int(a[0].fields.get("$code", 200)) if a and isinstance(a[0], JObject) else 0)
    stubs.register("Lokhttp3/Response;", "isSuccessful", "()Z", lambda a: 1 if a and isinstance(a[0], JObject) and 200 <= int(a[0].fields.get("$code", 0)) < 300 else 0)
    stubs.register("Lokhttp3/Response;", "message", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("$msg", "OK")) if a and isinstance(a[0], JObject) else JString(""))
    stubs.register("Lokhttp3/Response;", "body", "()Lokhttp3/ResponseBody;", lambda a: a[0].fields.get("$body") if a and isinstance(a[0], JObject) else None)
    stubs.register("Lokhttp3/ResponseBody;", "string", "()Ljava/lang/String;", lambda a: JString((a[0].fields.get("$body", b"") or b"").decode("utf-8", "replace")) if a and isinstance(a[0], JObject) else JString(""))
    stubs.register("Lokhttp3/ResponseBody;", "bytes", "()[B", lambda a: JArray("[B", list(a[0].fields.get("$body", b"") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))
    stubs.register("Lokhttp3/ResponseBody;", "close", "()V", lambda a: None)

    def _get_code(a):
        return _fire(a[0] if a else None).code
    stubs.register_by_name("getResponseCode", "()I", _get_code)

    def _get_msg(a):
        return JString(_fire(a[0] if a else None).message)
    stubs.register_by_name("getResponseMessage", "()Ljava/lang/String;", _get_msg)

    def _get_hdr(a):
        resp = _fire(a[0] if a else None)
        key = _s(a[1]) if len(a) > 1 else ""
        v = resp.headers.get(key)
        return JString(v) if v is not None else 0
    stubs.register_by_name("getHeaderField", "(Ljava/lang/String;)Ljava/lang/String;", _get_hdr)

    # --- Input-Stream (Response-Body liefern) ----------------------------
    def _get_in(a):
        conn = a[0] if a else None
        if isinstance(conn, JObject) and conn.fields.get("_bytes") is not None:
            return JObject("Ljava/io/FileInputStream;", {
                "path": conn.fields.get("$url", ""), "pos": 0, "_mem": bytes(conn.fields["_bytes"] or b"")
            })
        resp = _fire(conn)
        ins = JObject("Ljava/io/InputStream;")
        ins.fields["$data"] = resp.body
        ins.fields["$pos"] = 0
        return ins
    stubs.register_by_name("getInputStream", "()Ljava/io/InputStream;", _get_in)
    stubs.register_by_name("getErrorStream", "()Ljava/io/InputStream;", _get_in)

    def _read_i(a):
        s = a[0] if a else None
        if isinstance(s, JObject):
            data = s.fields.get("$data", b""); pos = s.fields.get("$pos", 0)
            if pos < len(data):
                s.fields["$pos"] = pos + 1
                return data[pos]
        return -1

    def _read_b(a):
        s = a[0] if a else None
        if isinstance(s, JObject) and len(a) > 1 and getattr(a[1], "data", None) is not None:
            data = s.fields.get("$data", b""); pos = s.fields.get("$pos", 0)
            dst = a[1].data
            n = min(len(dst), len(data) - pos)
            if n <= 0:
                return -1
            for i in range(n):
                dst[i] = data[pos + i]
            s.fields["$pos"] = pos + n
            return n
        return -1

    def _read_bii(a):
        s = a[0] if a else None
        if isinstance(s, JObject) and len(a) > 3 and getattr(a[1], "data", None) is not None:
            data = s.fields.get("$data", b""); pos = s.fields.get("$pos", 0)
            dst = a[1].data; off = int(a[2]); ln = int(a[3])
            n = min(ln, len(data) - pos)
            if n <= 0:
                return -1
            for i in range(n):
                dst[off + i] = data[pos + i]
            s.fields["$pos"] = pos + n
            return n
        return -1
    stubs.register_by_name("read", "()I", _read_i)
    stubs.register_by_name("read", "([B)I", _read_b)
    stubs.register_by_name("read", "([BII)I", _read_bii)

    # --- java.net.Socket / ServerSocket / DatagramSocket (LAN/P2P sichtbar) ---
    def _record_sock_ev(method, url, body=b"", status_msg="Socket"):
        try:
            _publish_and_record(NetRequest(method, url, {}, body), NetResponse(200, {}, b"", status_msg))
        except Exception:
            pass

    def _ia_init_host(addr):
        h = addr.fields.get("$host") if isinstance(addr, JObject) else None
        return str(h or "0.0.0.0")

    def _isa_init(a):
        if a and isinstance(a[0], JObject):
            if len(a) > 2:
                a[0].fields["$host"] = _s(a[1]) or "0.0.0.0"
                a[0].fields["$port"] = int(a[2]) if isinstance(a[2], int) else 0
            elif len(a) > 1 and isinstance(a[1], int):
                a[0].fields["$host"] = "0.0.0.0"
                a[0].fields["$port"] = int(a[1])
        return a[0] if a else None

    stubs.register("Ljava/net/InetSocketAddress;", "<init>", "(Ljava/lang/String;I)V", _isa_init)
    stubs.register("Ljava/net/InetSocketAddress;", "<init>", "(I)V", _isa_init)
    stubs.register("Ljava/net/InetSocketAddress;", "<init>", "(Ljava/net/InetAddress;I)V",
                   lambda a: (a[0].fields.update({
                       "$host": _ia_init_host(a[1]),
                       "$port": int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0,
                   }), a[0])[1] if a and isinstance(a[0], JObject) else None)
    stubs.register("Ljava/net/InetAddress;", "getByName", "(Ljava/lang/String;)Ljava/net/InetAddress;",
                   lambda a: JObject("Ljava/net/InetAddress;", {"$host": _s(a[0]) if a else "127.0.0.1"}))
    stubs.register("Ljava/net/InetAddress;", "getLocalHost", "()Ljava/net/InetAddress;",
                   lambda a: JObject("Ljava/net/InetAddress;", {"$host": "192.168.1.2"}))
    stubs.register("Ljava/net/InetAddress;", "getHostAddress", "()Ljava/lang/String;",
                   lambda a: JString(_ia_init_host(a[0] if a else None)))

    def _sock_init(a):
        s = a[0] if a else None
        if isinstance(s, JObject) and len(a) > 2:
            host = _s(a[1]); port = int(a[2]) if isinstance(a[2], int) else 0
            s.fields["$host"] = host; s.fields["$port"] = port
            _record_sock_ev("CONNECT", f"tcp://{host}:{port}")
            try:
                from core.application.event_bus import EventBus
                EventBus.publish("NET_SOCKET", {"host": host, "port": port})
            except Exception:
                pass
        return s
    stubs.register("Ljava/net/Socket;", "<init>", "(Ljava/lang/String;I)V", _sock_init)

    def _sock_connect(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            host = str(a[1].fields.get("$host") or "0.0.0.0")
            port = int(a[1].fields.get("$port") or 0)
            if isinstance(a[0], JObject):
                a[0].fields["$host"] = host; a[0].fields["$port"] = port
            _record_sock_ev("CONNECT", f"tcp://{host}:{port}")
        return None
    stubs.register("Ljava/net/Socket;", "connect", "(Ljava/net/SocketAddress;)V", _sock_connect)
    stubs.register("Ljava/net/Socket;", "connect", "(Ljava/net/SocketAddress;I)V", _sock_connect)

    def _srv_init(a):
        if a and isinstance(a[0], JObject):
            port = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            a[0].fields["$port"] = port
            _record_sock_ev("LISTEN", f"tcp://0.0.0.0:{port}")
        return a[0] if a else None
    stubs.register("Ljava/net/ServerSocket;", "<init>", "(I)V", _srv_init)
    stubs.register("Ljava/net/ServerSocket;", "<init>", "(II)V", _srv_init)
    stubs.register("Ljava/net/ServerSocket;", "bind", "(Ljava/net/SocketAddress;)V",
                   lambda a: _record_sock_ev(
                       "LISTEN",
                       f"tcp://{a[1].fields.get('$host', '0.0.0.0')}:{int(a[1].fields.get('$port', 0))}"
                   ) if (len(a) > 1 and isinstance(a[1], JObject)) else None)

    def _dgram_pkt_init(a):
        if not a or not isinstance(a[0], JObject):
            return None
        pkt = a[0]
        if len(a) > 1:
            raw_b = _to_bytes(a[1])
            if len(a) >= 4 and isinstance(a[2], int) and isinstance(a[3], int):
                off = max(0, int(a[2])); ln = max(0, int(a[3]))
                pkt.fields["$data"] = raw_b[off:off + ln]
            elif len(a) >= 3 and isinstance(a[2], int):
                pkt.fields["$data"] = raw_b[:max(0, int(a[2]))]
            else:
                pkt.fields["$data"] = raw_b
        last = a[-1]
        second_last = a[-2] if len(a) >= 2 else None
        if isinstance(last, JObject):
            pkt.fields["$host"] = str(last.fields.get("$host") or "255.255.255.255")
            pkt.fields["$port"] = int(last.fields.get("$port") or 0)
        elif isinstance(last, int) and isinstance(second_last, JObject):
            pkt.fields["$host"] = _ia_init_host(second_last)
            pkt.fields["$port"] = int(last)
        return pkt

    for _dpsig in (
        "([BI)V",
        "([BII)V",
        "([BILjava/net/SocketAddress;)V",
        "([BIILjava/net/SocketAddress;)V",
        "([BILjava/net/InetAddress;I)V",
        "([BIILjava/net/InetAddress;I)V",
    ):
        stubs.register("Ljava/net/DatagramPacket;", "<init>", _dpsig, _dgram_pkt_init)

    def _dgram_send(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            host = str(a[1].fields.get("$host") or "255.255.255.255")
            port = int(a[1].fields.get("$port") or 0)
            body = bytes(a[1].fields.get("$data") or b"")
            _record_sock_ev("UDP", f"udp://{host}:{port}", body=body[:512])
        return None
    stubs.register("Ljava/net/DatagramSocket;", "send", "(Ljava/net/DatagramPacket;)V", _dgram_send)
    stubs.register("Ljava/net/MulticastSocket;", "send", "(Ljava/net/DatagramPacket;)V", _dgram_send)

    _install_okhttp_retrofit_hook(stubs, _fire_direct, _s)
    return log


def _sig_param_descriptors(msig):
    """Zerlegt Smali-Methodenparameter '(...)R' in eine Liste von Typ-Deskriptoren."""
    if "(" not in msig or ")" not in msig:
        return []
    inner = msig[msig.index("(") + 1:msig.index(")")]
    out = []
    i = 0
    while i < len(inner):
        start = i
        c = inner[i]
        if c == "[":
            while i < len(inner) and inner[i] == "[":
                i += 1
            if i < len(inner) and inner[i] == "L":
                semi = inner.find(";", i)
                i = (semi + 1) if semi != -1 else len(inner)
            else:
                i += 1
            out.append(inner[start:i])
        elif c == "L":
            semi = inner.find(";", i)
            i = (semi + 1) if semi != -1 else len(inner)
            out.append(inner[start:i])
        else:
            out.append(c)
            i += 1
    return out


def _parse_retrofit_routes(smali_src):
    """Parst @GET/@POST/@PUT/@DELETE/@PATCH, @Path/@Query/@Body und generische Return-Signatur
    aus einer Retrofit-Service-Smali-Datei."""
    import re as _re
    if not smali_src:
        return {}
    routes = {}
    blocks = _re.split(r"(?=^\s*\.method\b)", smali_src, flags=_re.MULTILINE)
    hdr_re = _re.compile(r"^\s*\.method\b[^\n]*?\s+([^\s(]+)(\([^)]*\)\S+)")
    http_re = _re.compile(
        r'\.annotation\s+runtime\s+Lretrofit2/http/(GET|POST|PUT|DELETE|PATCH|HEAD);'
        r'(?:(?!\.end annotation).)*?value\s*=\s*"([^"]*)"',
        _re.DOTALL,
    )
    param_blk_re = _re.compile(
        r'\.param\s+(p\d+)\b[^\n]*\n(.*?)(?=\.end param|\.method|\Z)',
        _re.DOTALL,
    )
    ann_pq_re = _re.compile(
        r'\.annotation\s+runtime\s+Lretrofit2/http/(Path|Query|Header|Body);'
        r'(?:(?:(?!\.end annotation).)*?value\s*=\s*"([^"]*)")?',
        _re.DOTALL,
    )
    sig_re = _re.compile(r'\.annotation\s+system\s+Ldalvik/annotation/Signature;\s*\n\s*value\s*=\s*\{(.*?)\}', _re.DOTALL)
    for blk in blocks:
        m = hdr_re.match(blk)
        if not m:
            continue
        mname, msig = m.group(1), m.group(2)
        hm = http_re.search(blk)
        if not hm:
            continue
        http_meth, path = hm.group(1), hm.group(2)
        slot_to_logical = {}
        slot = 0
        for lidx, pt in enumerate(_sig_param_descriptors(msig)):
            slot_to_logical[slot] = (lidx, pt)
            slot += 2 if pt in ("J", "D") else 1
        params = {}
        for pm in param_blk_re.finditer(blk):
            slot_idx = int(pm.group(1)[1:]) - 1
            am = ann_pq_re.search(pm.group(2))
            if am and slot_idx >= 0:
                lidx, pt = slot_to_logical.get(slot_idx, (slot_idx, ""))
                params[lidx] = (am.group(1), am.group(2) or "", pt)
        type_chain = []
        sig_joined = ""
        sm = sig_re.search(blk)
        if sm:
            sig_joined = "".join(_re.findall(r'"([^"]*)"', sm.group(1)))
            if "Lkotlin/coroutines/Continuation<" in sig_joined:
                ret_part = sig_joined.split("Lkotlin/coroutines/Continuation<", 1)[1]
            elif ")" in sig_joined:
                ret_part = sig_joined.rsplit(")", 1)[1]
            else:
                ret_part = sig_joined
            for cm in _re.finditer(r"L[^;<>\s]+(?=[;<])", ret_part):
                tc = cm.group(0) + ";"
                if not tc.startswith(("Lretrofit2/", "Ljava/util/", "Ljava/lang/", "Lkotlin/")):
                    type_chain.append(tc)
        ret_desc = msig.rsplit(")", 1)[-1] if ")" in msig else ""
        if not type_chain and ret_desc.startswith("L") and not ret_desc.startswith(("Lretrofit2/", "Ljava/", "Lkotlin/")):
            type_chain.append(ret_desc)
        wraps_response = ("Lretrofit2/Response" in sig_joined) or msig.endswith(")Lretrofit2/Response;")
        if not sig_joined and msig.endswith(")Ljava/lang/Object;"):
            wraps_response = True
        route_info = {
            "http": http_meth,
            "path": path,
            "sig": msig,
            "params": params,
            "type_chain": type_chain,
            "wraps_response": wraps_response,
        }
        routes[(mname, msig)] = route_info
        routes.setdefault(mname, route_info)
    return routes


def _unwrap_generic_classes(smali_src):
    """Liest nicht-statische Instanz-Feldtypen (inkl. package-private '.field a:T') aus Smali-Quelle."""
    if not smali_src:
        return {}
    out = {}
    for line in smali_src.splitlines():
        s = line.strip()
        if not s.startswith(".field "):
            continue
        head = s.split("=", 1)[0].strip()
        parts = head.split()
        if len(parts) < 2 or "static" in parts[1:-1]:
            continue
        nt = parts[-1]
        if ":" in nt:
            fn, ft = nt.split(":", 1)
            out[fn] = ft
    return out


def _collect_dto_schema(prov, cls_desc):
    """Sammelt (field_types, json_to_field) entlang der Superklassenkette inkl. Moshi/Gson-Annotationen."""
    import re as _re
    field_types = {}
    json_map = {}
    if prov is None or not cls_desc or not hasattr(prov, "get_source"):
        return field_types, json_map

    def _snake_to_camel(s):
        parts = str(s or "").split("_")
        if len(parts) <= 1:
            return s
        return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)

    cur = cls_desc
    seen = set()
    chain = []
    for _ in range(16):
        if not cur or cur in seen or cur == "Ljava/lang/Object;":
            break
        seen.add(cur)
        src = ""
        try:
            src = prov.get_source(cur) or ""
        except Exception:
            src = ""
        if not src:
            break
        chain.append(src)
        sup = None
        for ln in src.splitlines()[:25]:
            st = ln.strip()
            if st.startswith(".super "):
                sup = st.split()[-1]
                break
        if not sup and hasattr(prov, "get_super"):
            try:
                sup = prov.get_super(cur)
            except Exception:
                sup = None
        cur = sup

    for src in reversed(chain):
        lines = src.splitlines()
        i = 0
        while i < len(lines):
            st = lines[i].strip()
            if st.startswith(".field "):
                head = st.split("=", 1)[0].strip()
                parts = head.split()
                if len(parts) >= 2 and "static" not in parts[1:-1]:
                    nt = parts[-1]
                    if ":" in nt:
                        fn, ft = nt.split(":", 1)
                        field_types[fn] = ft
                        j = i + 1
                        while j < len(lines) and not lines[j].strip().startswith((".end field", ".field", ".method")):
                            ann_l = lines[j]
                            if "Lcom/squareup/moshi/Json;" in ann_l or "SerializedName" in ann_l:
                                block = "\n".join(lines[j:min(j + 8, len(lines))])
                                m = _re.search(r'(?:name|value)\s*=\s*"([^"]+)"', block)
                                if m:
                                    json_map[m.group(1)] = fn
                            j += 1
            elif st.startswith(".method ") and "$annotations()" in st:
                m_prop = _re.search(r'\bget([A-Za-z0-9_]+)\$annotations\(', st)
                if m_prop:
                    prop_raw = m_prop.group(1)
                    prop_cand = prop_raw[:1].lower() + prop_raw[1:]
                    j = i + 1
                    while j < len(lines) and not lines[j].strip().startswith(".end method"):
                        if "Lcom/squareup/moshi/Json;" in lines[j] or "SerializedName" in lines[j]:
                            block = "\n".join(lines[j:min(j + 8, len(lines))])
                            m = _re.search(r'(?:name|value)\s*=\s*"([^"]+)"', block)
                            if m:
                                json_map[m.group(1)] = prop_cand
                        j += 1
            i += 1

    for fn in list(field_types.keys()):
        json_map.setdefault(fn, fn)
    return field_types, json_map


def _coerce_dto_field(val, fhint):
    """Koerziert deserialisierte JSON-Werte (Gson-artig) anhand der Smali-Felddeklaration."""
    from core.java.objects import JString
    if not fhint:
        return val
    if fhint in ("I", "S", "B", "J"):
        if val is None:
            return 0
        if isinstance(val, JString):
            try:
                return int(str(val.text).strip())
            except Exception:
                return 0
        if isinstance(val, str):
            try:
                return int(val.strip())
            except Exception:
                return 0
        if isinstance(val, (bool, float)):
            return int(val)
        return val
    if fhint == "Z":
        if val is None:
            return 0
        if isinstance(val, JString):
            return 1 if str(val.text).strip().lower() in ("true", "1") else 0
        if isinstance(val, str):
            return 1 if val.strip().lower() in ("true", "1") else 0
        if isinstance(val, bool):
            return 1 if val else 0
        return 1 if val else 0
    if fhint in ("F", "D"):
        if val is None:
            return 0.0
        if isinstance(val, JString):
            try:
                return float(str(val.text).strip())
            except Exception:
                return 0.0
        if isinstance(val, str):
            try:
                return float(val.strip())
            except Exception:
                return 0.0
        if isinstance(val, (int, bool)):
            return float(val)
        return val
    if fhint == "Ljava/lang/String;":
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return JString(str(val))
    return val


def _json_to_jobj(data, cls_hint=None, type_chain=None, vm=None, _idx=0):
    """Konvertiert JSON (dict/list/Skalar) in passende JObject/ArrayList/JString-Struktur."""
    from core.java.objects import JObject, JString
    if data is None:
        return None
    if isinstance(data, bool):
        return 1 if data else 0
    if isinstance(data, int):
        return data
    if isinstance(data, float):
        return data
    if isinstance(data, str):
        return JString(data)
    if isinstance(data, list):
        items = []
        next_cls = type_chain[_idx] if (type_chain and _idx < len(type_chain)) else None
        for el in data:
            if isinstance(el, bool):
                items.append(JObject("Ljava/lang/Boolean;", {"value": 1 if el else 0}))
            elif isinstance(el, int):
                items.append(JObject("Ljava/lang/Integer;", {"value": el}))
            else:
                items.append(_json_to_jobj(el, next_cls, type_chain, vm, _idx + 1))
        return JObject("Ljava/util/ArrayList;", {"__items": items})
    if isinstance(data, dict):
        tcls = cls_hint or (type_chain[_idx] if (type_chain and _idx < len(type_chain)) else "Ljava/util/HashMap;")
        if tcls == "Ljava/util/HashMap;":
            return JObject("Ljava/util/HashMap;", {"map": {k: _json_to_jobj(v, None, type_chain, vm, _idx) for k, v in data.items()}})
        field_types = {}
        json_map = {}
        prov = getattr(vm, "class_provider", None) if vm is not None else None
        if prov is not None and hasattr(prov, "get_source"):
            try:
                field_types, json_map = _collect_dto_schema(prov, tcls)
            except Exception:
                field_types, json_map = {}, {}
        flds = {}
        for fn, ft in field_types.items():
            if ft == "Ljava/lang/String;":
                flds[fn] = JString("")
            elif ft in ("Ljava/util/List;", "Ljava/util/ArrayList;", "Ljava/util/Collection;"):
                flds[fn] = JObject("Ljava/util/ArrayList;", {"__items": []})
        next_idx = _idx + 1
        for k, v in data.items():
            target_fn = json_map.get(k)
            if target_fn is None:
                camel = k.split("_")[0] + "".join(p[:1].upper() + p[1:] for p in k.split("_")[1:] if p) if "_" in k else k
                target_fn = camel if camel in field_types else k
            fhint = field_types.get(target_fn) or field_types.get(k)
            child_hint = fhint if (fhint and fhint.startswith("L") and not fhint.startswith(("Ljava/", "Lkotlin/"))) else None
            if isinstance(v, dict) and child_hint is None and type_chain and next_idx < len(type_chain):
                if not field_types or target_fn in field_types or k in field_types:
                    child_hint = type_chain[next_idx]
            val = _json_to_jobj(v, child_hint, type_chain, vm, next_idx)
            coerced = _coerce_dto_field(val, fhint)
            flds[target_fn] = coerced
            if target_fn != k and k not in flds:
                flds[k] = coerced
        return JObject(tcls, flds)
    return data


def _install_okhttp_retrofit_hook(stubs, fire_direct, s_fn):
    """Generischer VM-Hook fuer APK-seitiges OkHttp3 (Call/Interceptor/Request) und Retrofit2 (inkl. R8)."""
    vm = getattr(stubs, "vm", None)
    if vm is None or not hasattr(vm, "_method_hooks"):
        return
    from core.java.objects import JObject, JString, JArray
    from urllib.parse import (
        quote as _q,
        urlsplit as _urlsplit,
        urlunsplit as _urlunsplit,
        parse_qs as _parse_qs,
        parse_qsl as _parse_qsl,
        urlencode as _urlencode,
    )

    _field_cache = {}

    def _hdr_get(hdrs, key):
        if not hdrs or not key:
            return None
        if key in hdrs:
            return hdrs[key]
        kl = str(key).lower()
        for hk, hv in hdrs.items():
            if str(hk).lower() == kl:
                return hv
        return None

    def _decl_fields(_vm, cls_desc):
        if not cls_desc:
            return {}
        if cls_desc in _field_cache:
            return _field_cache[cls_desc]
        prov = getattr(_vm, "class_provider", None) if _vm is not None else None
        src = ""
        if prov is not None and hasattr(prov, "get_source"):
            try:
                src = prov.get_source(cls_desc) or ""
            except Exception:
                src = ""
        parsed = _unwrap_generic_classes(src)
        _field_cache[cls_desc] = parsed
        return parsed

    def _make_http_url(_vm, url_str):
        u = str(url_str or "")
        is_https = u.startswith("https")
        try:
            host = _urlsplit(u).hostname or ""
        except Exception:
            host = ""
        obj = JObject("Lokhttp3/HttpUrl;", {"$url": u})
        df = _decl_fields(_vm, "Lokhttp3/HttpUrl;")
        str_fields = [fn for fn, ft in df.items() if ft == "Ljava/lang/String;"]
        for fn, ft in df.items():
            if ft == "Ljava/lang/String;":
                obj.fields[fn] = JString("")
            elif ft == "I":
                obj.fields[fn] = 443 if is_https else 80
            elif ft in ("Ljava/util/ArrayList;", "Ljava/util/List;"):
                obj.fields[fn] = JObject("Ljava/util/ArrayList;", {"__items": [JString("")]})
        if str_fields:
            obj.fields[str_fields[0]] = JString("https" if is_https else "http")
            if len(str_fields) >= 4:
                obj.fields[str_fields[3]] = JString(host)
            obj.fields[str_fields[-1]] = JString(u)
        if "host" in df:
            obj.fields["host"] = JString(host)
        if "url" in df:
            obj.fields["url"] = JString(u)
        if "i" in df and df["i"] == "Ljava/lang/String;":
            obj.fields["i"] = JString(u)
        return obj

    def _make_resp_body(raw_bytes):
        return JObject("Lokhttp3/ResponseBody;", {"$body": bytes(raw_bytes or b"")})

    def _make_ok_request(_vm, method, url_str, headers, body_bytes):
        m = str(method or "GET").upper() or "GET"
        u = str(url_str or "")
        hdrs = dict(headers or {})
        req = JObject("Lokhttp3/Request;", {
            "$method": m,
            "$url": u,
            "$headers": hdrs,
            "$body": bytes(body_bytes or b""),
        })
        http_url = _make_http_url(_vm, u)
        hdr_obj = JObject("Lokhttp3/Headers;", {"$headers": hdrs})
        for fn, ft in _decl_fields(_vm, "Lokhttp3/Request;").items():
            if ft == "Lokhttp3/HttpUrl;":
                req.fields[fn] = http_url
            elif ft == "Ljava/lang/String;":
                req.fields[fn] = JString(m)
            elif ft == "Lokhttp3/Headers;":
                req.fields[fn] = hdr_obj
        return req

    def _make_ok_response(_vm, req_obj, resp):
        code = int(getattr(resp, "code", 200))
        msg = str(getattr(resp, "message", "OK") or "OK")
        is_ok = 200 <= code < 300
        hdrs = dict(getattr(resp, "headers", {}) or {})
        body_obj = _make_resp_body(getattr(resp, "body", b""))
        ok_resp = JObject("Lokhttp3/Response;", {
            "$code": code,
            "$msg": msg,
            "$body": body_obj,
            "$headers": hdrs,
            "$net_resp": resp,
            "$req": req_obj,
        })
        hdr_obj = JObject("Lokhttp3/Headers;", {"$headers": hdrs})
        for fn, ft in _decl_fields(_vm, "Lokhttp3/Response;").items():
            if ft == "Lokhttp3/Request;":
                ok_resp.fields[fn] = req_obj
            elif ft == "Z":
                ok_resp.fields[fn] = 1 if is_ok else 0
            elif ft == "I":
                ok_resp.fields[fn] = code
            elif ft == "Ljava/lang/String;":
                ok_resp.fields[fn] = JString(msg)
            elif ft == "Lokhttp3/ResponseBody;":
                ok_resp.fields[fn] = body_obj
            elif ft == "Lokhttp3/Headers;":
                ok_resp.fields[fn] = hdr_obj
        return ok_resp

    def _make_retrofit_response(_vm, ok_resp, code, msg, raw_b, parsed_obj):
        is_ok = 200 <= int(code) < 300
        err_body = None if is_ok else _make_resp_body(raw_b)
        r_resp = JObject("Lretrofit2/Response;", {
            "$code": int(code),
            "$ok": is_ok,
            "$msg": str(msg or "OK"),
            "$raw_body": bytes(raw_b or b""),
            "$body_obj": parsed_obj,
        })
        for fn, ft in _decl_fields(_vm, "Lretrofit2/Response;").items():
            if ft == "Lokhttp3/Response;":
                r_resp.fields[fn] = ok_resp
            elif ft == "Ljava/lang/Object;":
                r_resp.fields[fn] = parsed_obj
            elif ft == "Lokhttp3/ResponseBody;":
                r_resp.fields[fn] = err_body
        return r_resp

    def _harvest_interceptors(builder_obj):
        out = []
        seen = set()
        if not isinstance(builder_obj, JObject):
            return out

        def _push(ic):
            if isinstance(ic, JObject) and id(ic) not in seen:
                if "HttpLoggingInterceptor" in getattr(ic, "cls", ""):
                    return
                seen.add(id(ic))
                out.append(ic)

        for ic in list(builder_obj.fields.get("$interceptors", ()) or ()):
            _push(ic)
        for v in builder_obj.fields.values():
            if isinstance(v, JObject):
                items = v.fields.get("__items")
                if isinstance(items, list):
                    for it in items:
                        _push(it)
        return out

    def _find_intercept_method(_vm, ccls):
        default_sig = "(Lokhttp3/Interceptor$Chain;)Lokhttp3/Response;"
        if _vm is None or not ccls:
            return "intercept", default_sig
        target_sigs = (
            "(Lokhttp3/Interceptor$Chain;)Lokhttp3/Response;",
            "(Lokhttp3/internal/http/RealInterceptorChain;)Lokhttp3/Response;",
        )
        try:
            if hasattr(_vm, "_ensure_class_init"):
                _vm._ensure_class_init(ccls)
        except Exception:
            pass
        reg = getattr(_vm, "registry", None)
        if isinstance(reg, dict):
            for ts in target_sigs:
                if (ccls, "intercept", ts) in reg:
                    return "intercept", ts
            for (rc, mn, ms) in reg.keys():
                if rc == ccls and ms in target_sigs:
                    return mn, ms
        prov = getattr(_vm, "class_provider", None)
        if prov is not None and hasattr(prov, "get_class"):
            try:
                mds = prov.get_class(ccls) or ()
                for md in mds:
                    if getattr(md, "sig", "") in target_sigs:
                        return md.name, md.sig
            except Exception:
                pass
        return "intercept", default_sig

    def _make_chain(_vm, req_cur, ic_list, idx):
        ch = JObject("Lokhttp3/internal/http/RealInterceptorChain;", {
            "$req": req_cur,
            "$ics": list(ic_list or ()),
            "$idx": int(idx),
            "$resp": None,
        })
        for fn, ft in _decl_fields(_vm, "Lokhttp3/internal/http/RealInterceptorChain;").items():
            if ft == "Lokhttp3/Request;":
                ch.fields[fn] = req_cur
            elif ft == "Lokhttp3/internal/connection/Exchange;":
                ch.fields[fn] = None
        return ch

    def _proceed_chain(_vm, ic_list, idx, req_cur):
        active_vm = _vm or vm
        n_ics = len(ic_list or ())
        while idx < n_ics:
            ic = ic_list[idx]
            idx += 1
            if not isinstance(ic, JObject):
                continue
            ccls = getattr(ic, "cls", "")
            if "HttpLoggingInterceptor" in ccls:
                continue
            sub_chain = _make_chain(active_vm, req_cur, ic_list, idx)
            imeth, isig = _find_intercept_method(active_vm, ccls)
            prev_budget = getattr(active_vm, "step_budget", None) if active_vm is not None else None
            try:
                if active_vm is not None:
                    cap = active_vm.steps + 25_000
                    active_vm.step_budget = min(prev_budget, cap) if prev_budget is not None else cap
                resp = active_vm.invoke(ccls, imeth, isig, (ic, sub_chain))
                if isinstance(resp, JObject) and resp.cls == "Lokhttp3/Response;":
                    return resp
                if sub_chain.fields.get("$resp") is not None:
                    return sub_chain.fields["$resp"]
                if isinstance(sub_chain.fields.get("$req"), JObject):
                    req_cur = sub_chain.fields["$req"]
            except Exception:
                if sub_chain.fields.get("$resp") is not None:
                    return sub_chain.fields["$resp"]
                if isinstance(sub_chain.fields.get("$req"), JObject):
                    req_cur = sub_chain.fields["$req"]
            finally:
                if active_vm is not None:
                    active_vm.step_budget = prev_budget
        if not isinstance(req_cur, JObject):
            req_cur = _make_ok_request(active_vm, "GET", "", {}, b"")
        flds = req_cur.fields
        resp = fire_direct(flds.get("$method", "GET"), flds.get("$url", ""), flds.get("$headers", {}), flds.get("$body", b""))
        return _make_ok_response(active_vm, req_cur, resp)

    def _run_okhttp_call(req_obj, interceptors, _vm=None):
        return _proceed_chain(_vm or vm, list(interceptors or ()), 0, req_obj)

    def _net_hook(rcls, cls, name, sig, args, static, _vm):
        # 0) Moshi (Moshi$Builder / Moshi / JsonAdapter / KotlinJsonAdapterFactory)
        if cls.startswith("Lcom/squareup/moshi/"):
            if name == "<clinit>":
                return True, None
            if cls == "Lcom/squareup/moshi/Moshi$Builder;":
                if name == "<init>":
                    return True, None
                if sig.endswith(")Lcom/squareup/moshi/Moshi;"):
                    return True, JObject("Lcom/squareup/moshi/Moshi;", {})
                if sig.endswith(")V"):
                    return True, None
                return True, (args[0] if args else JObject(cls))
            if cls == "Lcom/squareup/moshi/Moshi;":
                if name == "<init>":
                    return True, None
                if sig.endswith(")Lcom/squareup/moshi/JsonAdapter;"):
                    return True, JObject("Lcom/squareup/moshi/JsonAdapter;", {"$type": args[1] if len(args) > 1 else None})
            if cls == "Lcom/squareup/moshi/JsonAdapter;":
                if name == "<init>":
                    return True, None
                if name == "fromJson" and sig == "(Ljava/lang/String;)Ljava/lang/Object;" and len(args) > 1:
                    raw_s = s_fn(args[1])
                    raw_t = args[0].fields.get("$type") if isinstance(args[0], JObject) else None
                    tcls = raw_t.fields.get("name") if isinstance(raw_t, JObject) else None
                    if tcls and not (tcls.startswith("L") and tcls.endswith(";")):
                        tcls = "L" + tcls.replace(".", "/") + ";"
                    try:
                        parsed = _json.loads(raw_s)
                        return True, _json_to_jobj(parsed, cls_hint=tcls, vm=_vm)
                    except Exception:
                        return True, None
                if name == "toJson" and sig.endswith(")Ljava/lang/String;"):
                    return True, JString("{}")
                if sig.endswith(")Lcom/squareup/moshi/JsonAdapter;"):
                    return True, args[0] if args else JObject(cls)
                if sig.endswith(")V"):
                    return True, None
            if cls.endswith("KotlinJsonAdapterFactory;") and name == "<init>":
                return True, None

        # 1) OkHttp3 (LoggingInterceptor, HttpUrl, Headers, ResponseBody, Client, Call, Chain, Request, Response)
        if cls.startswith("Lokhttp3/logging/HttpLoggingInterceptor"):
            if name == "<init>":
                return True, None if sig.endswith(")V") else (args[0] if args else None)
            return True, None if sig.endswith(")V") else (args[0] if args else None)

        if cls == "Lokhttp3/internal/_UtilJvmKt;" and sig.endswith(")I"):
            return True, (int(args[0] or 30) * 1000 if args and isinstance(args[0], int) else 30000)

        if cls in ("Lokhttp3/HttpUrl;", "Lokhttp3/HttpUrl$Companion;"):
            if sig == "(Ljava/lang/String;)Lokhttp3/HttpUrl;":
                raw_u = s_fn(args[0] if static else (args[1] if len(args) > 1 else ""))
                return True, _make_http_url(_vm, raw_u)
            if not static and args and isinstance(args[0], JObject) and "$url" in args[0].fields:
                u_str = str(args[0].fields.get("$url", ""))
                if sig == "(Ljava/lang/String;)Ljava/lang/String;" and len(args) > 1 and name != "toString":
                    try:
                        vals = _parse_qs(_urlsplit(u_str).query, keep_blank_values=True).get(s_fn(args[1]))
                    except Exception:
                        vals = None
                    return True, (JString(vals[0]) if vals else None)
                if name == "toString" or sig == "()Ljava/lang/String;":
                    return True, JString(u_str)
                if name == "newBuilder" or sig == "()Lokhttp3/HttpUrl$Builder;":
                    return True, JObject("Lokhttp3/HttpUrl$Builder;", {"$url": u_str})

        if cls == "Lokhttp3/HttpUrl$Builder;" and args and isinstance(args[0], JObject):
            hb = args[0]
            u_cur = str(hb.fields.get("$url", ""))
            if (
                name in ("removeAllQueryParameters", "removeAllEncodedQueryParameters")
                or (sig == "(Ljava/lang/String;)V" and len(args) > 1)
            ) and len(args) > 1:
                rm_k = s_fn(args[1])
                try:
                    sp = _urlsplit(u_cur)
                    qlist = [(k, v) for (k, v) in _parse_qsl(sp.query, keep_blank_values=True) if k != rm_k]
                    hb.fields["$url"] = _urlunsplit((sp.scheme, sp.netloc, sp.path, _urlencode(qlist), sp.fragment))
                except Exception:
                    pass
                return True, (None if sig.endswith(")V") else hb)
            if (
                name in ("addQueryParameter", "addEncodedQueryParameter", "setQueryParameter")
                or sig.startswith("(Ljava/lang/String;Ljava/lang/String;)")
            ) and len(args) > 2:
                sep = "&" if "?" in u_cur else "?"
                hb.fields["$url"] = u_cur + sep + "%s=%s" % (_q(s_fn(args[1]), safe=""), _q(s_fn(args[2]), safe=""))
                return True, (None if sig.endswith(")V") else hb)
            if name == "build" or sig == "()Lokhttp3/HttpUrl;":
                return True, _make_http_url(_vm, u_cur)

        if cls == "Lokhttp3/Headers;" and not static and args and isinstance(args[0], JObject):
            hdrs = args[0].fields.get("$headers") or {}
            if name == "size" or sig == "()I":
                return True, len(hdrs)
            if sig == "(Ljava/lang/String;)Ljava/lang/String;" and len(args) > 1 and name != "toString":
                hv = _hdr_get(hdrs, s_fn(args[1]))
                return True, (JString(str(hv)) if hv is not None else None)
            if sig == "(I)Ljava/lang/String;" and len(args) > 1:
                items = list(hdrs.items())
                idx = int(args[1] or 0)
                if 0 <= idx < len(items):
                    return True, JString(str(items[idx][0 if name in ("name", "b") else 1]))
                return True, JString("")

        if cls.startswith("Lokhttp3/ResponseBody") or (
            not static and args and isinstance(args[0], JObject)
            and str(getattr(args[0], "cls", "")).startswith("Lokhttp3/ResponseBody")
        ):
            if name == "close":
                return True, None
            if name == "string" or (not static and sig == "()Ljava/lang/String;" and name != "toString"):
                raw = (args[0].fields.get("$body", b"") if args and isinstance(args[0], JObject) else b"") or b""
                return True, JString(raw.decode("utf-8", "replace"))
            if name == "bytes" or (not static and sig == "()[B"):
                raw = (args[0].fields.get("$body", b"") if args and isinstance(args[0], JObject) else b"") or b""
                return True, JArray("[B", [b - 256 if b >= 128 else b for b in raw])

        if cls == "Lokhttp3/OkHttpClient$Builder;":
            b = args[0] if (args and isinstance(args[0], JObject)) else None
            if name == "<init>":
                if b is not None:
                    b.fields["$interceptors"] = []
                    for fn, ft in _decl_fields(_vm, cls).items():
                        if ft in ("Ljava/util/ArrayList;", "Ljava/util/List;") and fn not in b.fields:
                            b.fields[fn] = JObject("Ljava/util/ArrayList;", {"__items": []})
                return True, None if sig.endswith(")V") else b
            if b is not None and (
                name in ("addInterceptor", "addNetworkInterceptor")
                or (not static and len(args) > 1 and sig.startswith("(Lokhttp3/Interceptor;)"))
            ):
                if len(args) > 1 and args[1] is not None:
                    b.fields.setdefault("$interceptors", []).append(args[1])
                return True, None if sig.endswith(")V") else b
            if b is not None and (name == "build" or sig == "()Lokhttp3/OkHttpClient;"):
                return True, JObject("Lokhttp3/OkHttpClient;", {"$interceptors": _harvest_interceptors(b)})
            if b is not None and sig.endswith(")Lokhttp3/OkHttpClient$Builder;"):
                return True, b
            if sig.endswith(")V"):
                return True, None
            return True, b

        if cls == "Lokhttp3/OkHttpClient;" and args and isinstance(args[0], JObject):
            cli = args[0]
            if name == "<init>":
                if len(args) > 1 and isinstance(args[1], JObject):
                    cli.fields["$interceptors"] = _harvest_interceptors(args[1])
                else:
                    cli.fields.setdefault("$interceptors", [])
                return True, None
            if name == "newBuilder" or (not static and sig == "()Lokhttp3/OkHttpClient$Builder;"):
                nb = JObject("Lokhttp3/OkHttpClient$Builder;", {
                    "$interceptors": list(cli.fields.get("$interceptors", [])),
                })
                for fn, ft in _decl_fields(_vm, "Lokhttp3/OkHttpClient$Builder;").items():
                    if ft in ("Ljava/util/ArrayList;", "Ljava/util/List;"):
                        nb.fields[fn] = JObject("Ljava/util/ArrayList;", {"__items": list(cli.fields.get("$interceptors", []))})
                return True, nb
            if name == "newCall" or sig == "(Lokhttp3/Request;)Lokhttp3/Call;":
                req_obj = args[1] if len(args) > 1 else None
                incs = cli.fields.get("$interceptors", [])
                return True, JObject("Lokhttp3/RealCall;", {"$req": req_obj, "$interceptors": list(incs)})

        if cls in ("Lokhttp3/Call;", "Lokhttp3/RealCall;") or rcls in ("Lokhttp3/RealCall;",):
            if (name == "execute" or sig == "()Lokhttp3/Response;") and args and isinstance(args[0], JObject):
                return True, _run_okhttp_call(args[0].fields.get("$req"), args[0].fields.get("$interceptors", []), _vm)

        if cls in ("Lokhttp3/Interceptor$Chain;", "Lokhttp3/internal/http/RealInterceptorChain;"):
            if static and sig.endswith(")Lokhttp3/internal/http/RealInterceptorChain;") and args and isinstance(args[0], JObject):
                base_ch = args[0]
                new_req = (
                    args[3]
                    if (len(args) > 3 and isinstance(args[3], JObject) and args[3].cls == "Lokhttp3/Request;")
                    else base_ch.fields.get("$req")
                )
                return True, _make_chain(_vm, new_req, base_ch.fields.get("$ics") or [], base_ch.fields.get("$idx", 0))
            if not static and args and isinstance(args[0], JObject):
                ch = args[0]
                if name == "request" or sig == "()Lokhttp3/Request;":
                    return True, ch.fields.get("$req") or _make_ok_request(_vm, "GET", "", {}, b"")
                if (name == "proceed" or sig == "(Lokhttp3/Request;)Lokhttp3/Response;") and len(args) > 1:
                    req_obj = args[1]
                    ch.fields["$req"] = req_obj
                    ok_resp = _proceed_chain(_vm, ch.fields.get("$ics") or [], int(ch.fields.get("$idx", 0)), req_obj)
                    ch.fields["$resp"] = ok_resp
                    return True, ok_resp

        if cls == "Lokhttp3/Request;" and args and isinstance(args[0], JObject):
            rf = args[0].fields
            if name == "newBuilder" or sig == "()Lokhttp3/Request$Builder;":
                return True, JObject("Lokhttp3/Request$Builder;", {
                    "$method": rf.get("$method", "GET"),
                    "$url": rf.get("$url", ""),
                    "$headers": dict(rf.get("$headers", {})),
                    "$body": rf.get("$body", b""),
                })
            if name == "url" or sig == "()Lokhttp3/HttpUrl;":
                return True, _make_http_url(_vm, rf.get("$url", ""))
            if (name == "header" or sig == "(Ljava/lang/String;)Ljava/lang/String;") and len(args) > 1 and name != "toString":
                hv = _hdr_get(rf.get("$headers"), s_fn(args[1]))
                return True, JString(hv) if hv is not None else None

        if cls == "Lokhttp3/Request$Builder;" and args and isinstance(args[0], JObject):
            b = args[0]
            if name == "<init>":
                if len(args) > 1 and isinstance(args[1], JObject):
                    sf = args[1].fields or {}
                    b.fields.update({
                        "$method": sf.get("$method", "GET"),
                        "$url": sf.get("$url", ""),
                        "$headers": dict(sf.get("$headers") or {}),
                        "$body": sf.get("$body", b""),
                    })
                else:
                    b.fields.update({"$method": "GET", "$url": "", "$headers": {}, "$body": b""})
                return True, None if sig.endswith(")V") else b
            if len(args) > 1 and isinstance(args[1], JObject) and "$url" in args[1].fields:
                b.fields["$url"] = str(args[1].fields.get("$url", ""))
                return True, None if sig.endswith(")V") else b
            if (name == "url" or (len(args) == 2 and sig.startswith("(Ljava/lang/String;)"))) and len(args) > 1:
                b.fields["$url"] = s_fn(args[1])
                return True, None if sig.endswith(")V") else b
            if (name in ("header", "addHeader") or sig == "(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/Request$Builder;") and len(args) > 2:
                b.fields.setdefault("$headers", {})[s_fn(args[1])] = s_fn(args[2])
                return True, None if sig.endswith(")V") else b
            if (name == "method" or sig.startswith("(Ljava/lang/String;Lokhttp3/RequestBody;)")) and len(args) > 2:
                b.fields["$method"] = s_fn(args[1]).upper() or "GET"
                if isinstance(args[2], JObject):
                    b.fields["$body"] = args[2].fields.get("$body", b"")
                return True, None if sig.endswith(")V") else b
            if name == "build" or sig == "()Lokhttp3/Request;":
                return True, _make_ok_request(_vm, b.fields.get("$method", "GET"), b.fields.get("$url", ""), b.fields.get("$headers", {}), b.fields.get("$body", b""))

        if cls == "Lokhttp3/Response;" and args and isinstance(args[0], JObject):
            rf = args[0].fields
            if name == "code" or (not static and sig == "()I"):
                return True, int(rf.get("$code", 200))
            if name == "isSuccessful" or (not static and sig == "()Z"):
                return True, 1 if 200 <= int(rf.get("$code", 0)) < 300 else 0
            if name == "message" or (not static and sig == "()Ljava/lang/String;" and name != "toString"):
                return True, JString(str(rf.get("$msg", "OK")))
            if name == "body" or (not static and sig == "()Lokhttp3/ResponseBody;"):
                return True, rf.get("$body")
            if name == "request" or (not static and sig == "()Lokhttp3/Request;"):
                return True, rf.get("$req")

        # 2) Retrofit2 Converter/Builder/Create/Response (inkl. R8-Signaturen)
        if cls in ("Lretrofit2/converter/gson/GsonConverterFactory;",
                   "Lretrofit2/converter/moshi/MoshiConverterFactory;",
                   "Lretrofit2/converter/scalars/ScalarsConverterFactory;") or cls.startswith("Lretrofit2/converter/"):
            if name == "<init>":
                return True, None
            if (name == "create" or (static and sig.endswith(")" + cls))):
                return True, JObject(cls)

        if cls == "Lretrofit2/Retrofit$Builder;" and args and isinstance(args[0], JObject):
            rb = args[0]
            if name == "<init>":
                rb.fields.update({"$base_url": "", "$client": None})
                for fn, ft in _decl_fields(_vm, cls).items():
                    if ft in ("Ljava/util/ArrayList;", "Ljava/util/List;") and fn not in rb.fields:
                        rb.fields[fn] = JObject("Ljava/util/ArrayList;", {"__items": []})
                return True, None if sig.endswith(")V") else rb
            if len(args) > 1 and isinstance(args[1], JObject) and "$url" in args[1].fields:
                rb.fields["$base_url"] = str(args[1].fields.get("$url", ""))
                return True, None if sig.endswith(")V") else rb
            if (name == "baseUrl" or sig.startswith("(Ljava/lang/String;)")) and len(args) > 1:
                rb.fields["$base_url"] = s_fn(args[1])
                return True, None if sig.endswith(")V") else rb
            if (name == "client" or sig.startswith(("(Lokhttp3/OkHttpClient;", "(Lokhttp3/Call$Factory;"))) and len(args) > 1:
                rb.fields["$client"] = args[1]
                return True, None if sig.endswith(")V") else rb
            if name in ("addConverterFactory", "addCallAdapterFactory") or sig.startswith(("(Lretrofit2/Converter$Factory;", "(Lretrofit2/CallAdapter$Factory;")):
                return True, None if sig.endswith(")V") else rb
            if name == "build" or sig == "()Lretrofit2/Retrofit;":
                client = rb.fields.get("$client")
                if client is None:
                    for v in rb.fields.values():
                        if isinstance(v, JObject) and v.cls == "Lokhttp3/OkHttpClient;":
                            client = v
                            break
                base_url = rb.fields.get("$base_url") or ""
                if not base_url:
                    for v in rb.fields.values():
                        if isinstance(v, JObject) and v.cls == "Lokhttp3/HttpUrl;":
                            base_url = str(v.fields.get("$url", ""))
                            if base_url:
                                break
                        elif isinstance(v, JString) and str(v.text).startswith(("http://", "https://")):
                            base_url = str(v.text)
                            break
                return True, JObject("Lretrofit2/Retrofit;", {"$base_url": base_url, "$client": client})

        if cls == "Lretrofit2/Retrofit;" and (name == "create" or (not static and sig == "(Ljava/lang/Class;)Ljava/lang/Object;")) and args and isinstance(args[0], JObject):
            raw_svc = args[1].fields.get("name", "Ljava/lang/Object;") if len(args) > 1 and isinstance(args[1], JObject) else "Ljava/lang/Object;"
            svc_cls = raw_svc if (raw_svc.startswith("L") and raw_svc.endswith(";")) else f"L{raw_svc.replace('.', '/')};"
            prov = getattr(_vm, "class_provider", None)
            src = prov.get_source(svc_cls) if (prov is not None and hasattr(prov, "get_source")) else ""
            routes = _parse_retrofit_routes(src or "")
            rf_obj = args[0]
            base_url = rf_obj.fields.get("$base_url", "") or ""
            if not base_url:
                for v in rf_obj.fields.values():
                    if isinstance(v, JObject) and v.cls == "Lokhttp3/HttpUrl;":
                        base_url = str(v.fields.get("$url", ""))
                        if base_url:
                            break
                    elif isinstance(v, JString) and str(v.text).startswith(("http://", "https://")):
                        base_url = str(v.text)
                        break
            client = rf_obj.fields.get("$client")
            if client is None:
                for v in rf_obj.fields.values():
                    if isinstance(v, JObject) and v.cls == "Lokhttp3/OkHttpClient;":
                        client = v
                        break
            return True, JObject(svc_cls, {
                "$retrofit_base": base_url,
                "$retrofit_client": client,
                "$retrofit_routes": routes,
            })

        if cls == "Lretrofit2/Response;" and args and isinstance(args[0], JObject):
            rf = args[0].fields
            if name == "isSuccessful" or (not static and sig == "()Z"):
                return True, 1 if rf.get("$ok") else 0
            if name == "code" or (not static and sig == "()I"):
                return True, int(rf.get("$code", 200))
            if name == "message" or (not static and sig == "()Ljava/lang/String;" and name != "toString"):
                return True, JString(str(rf.get("$msg", "OK")))
            if name == "body" or (not static and sig == "()Ljava/lang/Object;"):
                return True, rf.get("$body_obj")
            if name == "errorBody" or (not static and sig == "()Lokhttp3/ResponseBody;"):
                return True, None if rf.get("$ok") else _make_resp_body(rf.get("$raw_body", b""))
            if name == "raw" or (not static and sig == "()Lokhttp3/Response;"):
                return True, rf.get("$raw_resp")

        # 3) Dynamischer Retrofit-Proxy-Call
        if not static and args and isinstance(args[0], JObject) and "$retrofit_routes" in args[0].fields:
            proxy = args[0]
            routes = proxy.fields.get("$retrofit_routes") or {}
            rt = routes.get((name, sig)) or routes.get(name)
            if rt is not None:
                base = (proxy.fields.get("$retrofit_base") or "").rstrip("/")
                path = (rt["path"] or "").lstrip("/")
                queries = []
                headers = {}
                body_bytes = b""

                def _param_str(aval):
                    if isinstance(aval, JString):
                        return s_fn(aval)
                    if isinstance(aval, JObject) and "value" in (aval.fields or {}):
                        v = aval.fields["value"]
                        if aval.cls == "Ljava/lang/Boolean;":
                            return "true" if v else "false"
                        return str(v)
                    return str(aval)

                for pidx, pinfo in sorted(rt["params"].items()):
                    if 1 + pidx >= len(args):
                        continue
                    kind, pname = pinfo[0], pinfo[1]
                    ptype = pinfo[2] if len(pinfo) > 2 else ""
                    aval = args[1 + pidx]
                    if kind in ("Query", "Header"):
                        if aval is None:
                            continue
                        if aval == 0 and (not ptype or ptype.startswith(("L", "["))):
                            continue
                    if kind == "Path":
                        path = path.replace("{" + pname + "}", _q(_param_str(aval), safe=""))
                    elif kind == "Query":
                        sv = _param_str(aval)
                        queries.append("%s=%s" % (_q(pname, safe=""), _q(sv, safe="")))
                    elif kind == "Header":
                        headers[pname] = _param_str(aval)
                    elif kind == "Body" and isinstance(aval, JObject):
                        try:
                            d = {k: (v.text if isinstance(v, JString) else v) for k, v in aval.fields.items() if not k.startswith("$")}
                            body_bytes = _json.dumps(d).encode("utf-8")
                            headers.setdefault("Content-Type", "application/json")
                        except Exception:
                            pass
                url = (base + "/" + path) if base else path
                if queries:
                    url += ("&" if "?" in url else "?") + "&".join(queries)
                client = proxy.fields.get("$retrofit_client")
                incs = client.fields.get("$interceptors", []) if isinstance(client, JObject) else []
                req_obj = _make_ok_request(_vm, rt["http"], url, headers, body_bytes)
                ok_resp = _run_okhttp_call(req_obj, incs, _vm)
                rf = ok_resp.fields if isinstance(ok_resp, JObject) else {}
                code = int(rf.get("$code", 200))
                raw_b = (rf.get("$body").fields.get("$body", b"") if isinstance(rf.get("$body"), JObject) else b"") or b""
                parsed_obj = None
                if raw_b:
                    try:
                        parsed_json = _json.loads(raw_b.decode("utf-8", "replace"))
                        tchain = rt.get("type_chain") or []
                        parsed_obj = _json_to_jobj(parsed_json, tchain[0] if tchain else None, tchain, _vm, 0)
                    except Exception:
                        parsed_obj = None
                r_resp = _make_retrofit_response(_vm, ok_resp, code, rf.get("$msg", "OK"), raw_b, parsed_obj)
                r_resp.fields["$raw_resp"] = ok_resp
                ret_desc = sig.rsplit(")", 1)[-1] if ")" in sig else ""
                if rt.get("wraps_response", True) or ret_desc == "Lretrofit2/Response;":
                    return True, r_resp
                if ret_desc not in ("Ljava/lang/Object;", "Lretrofit2/Call;"):
                    return True, parsed_obj
                return True, parsed_obj

        return False, None

    vm._method_hooks.append(_net_hook)
