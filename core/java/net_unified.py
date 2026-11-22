# -*- coding: utf-8 -*-
"""core.java.net_unified — vereinheitlichte Netz-Sicht: Java (net_recorder) UND nativ (OS-8-Sockets).

Normalisiert beide Quellen in **eine** quellen-markierte Liste, sodass die „Netz"-Sicht und Reports
den HTTP-Verkehr der Java-Ebene (`HttpURLConnection`/`Socket` via `net_recorder`) und den rohen
Socket-Verkehr der nativen Ebene (`os_linux_arm64`: connect/send/recv mit Record/Replay) gemeinsam
zeigen. Zielagnostisch; rein lesend/transformierend.
"""
from __future__ import annotations


def _b(x):
    try:
        return len(x) if isinstance(x, (bytes, bytearray)) else int(x)
    except Exception:
        return 0


def normalize_java(net_log):
    """NetLog (oder dessen .to_list()/.entries) → Liste vereinheitlichter Einträge (source='java')."""
    rows = []
    entries = getattr(net_log, "entries", None)
    if entries is None:
        entries = net_log or []
    for e in entries:
        req = e.get("req") if isinstance(e, dict) else getattr(e, "req", None)
        resp = e.get("resp") if isinstance(e, dict) else getattr(e, "resp", None)
        req_d = req.to_dict() if hasattr(req, "to_dict") else (dict(req) if isinstance(req, dict) else {})
        resp_d = resp.to_dict() if hasattr(resp, "to_dict") else (dict(resp) if isinstance(resp, dict) else {})
        if hasattr(req, "body") and isinstance(getattr(req, "body", None), (bytes, bytearray)):
            req_d.setdefault("body_raw", bytes(req.body[:65536]))
        if hasattr(resp, "body") and isinstance(getattr(resp, "body", None), (bytes, bytearray)):
            resp_d.setdefault("body_raw", bytes(resp.body[:65536]))
        # req/resp koennen Objekte (NetRequest/NetResponse) oder dicts (to_dict) sein
        method = req_d.get("method", "")
        url = req_d.get("url", "")
        code = resp_d.get("code", "") if resp_d else ""
        rb = resp_d.get("body_len", _b(resp_d.get("body", b""))) if resp_d else 0
        g_info = _extract_graphql_info(url, req_d.get("body") or req_d.get("body_preview") or "")
        if g_info and g_info.get("op_name"):
            lbl = "%s %s [GQL: %s]  ->  %s (%sB)" % (method, url, g_info["op_name"], code, rb)
        else:
            lbl = "%s %s  ->  %s (%sB)" % (method, url, code, rb)
        rows.append({"source": "java", "kind": "http",
                     "label": lbl,
                     "method": method, "url": url, "code": code, "resp_bytes": rb,
                     "req": req_d, "resp": resp_d})
    return rows


def normalize_native(os_net):
    """OS-8-Netz-Log (`{"log":[("connect",host,port),("send",fd,bytes),...]}` oder die Liste selbst)
    → Liste vereinheitlichter Einträge (source='native')."""
    log = os_net.get("log") if isinstance(os_net, dict) else (os_net or [])
    rows = []
    for rec in (log or []):
        if not rec:
            continue
        kind = rec[0] if isinstance(rec, (list, tuple)) else str(rec)
        raw_rec = list(rec) if isinstance(rec, (list, tuple)) else rec
        if kind == "connect":
            host = rec[1] if len(rec) > 1 else "?"
            port = rec[2] if len(rec) > 2 else "?"
            rows.append({"source": "native", "kind": "connect",
                         "label": "connect %s:%s" % (host, port), "host": host, "port": port,
                         "raw": raw_rec})
        elif kind in ("send", "sendto", "sendmsg", "ssl_send", "recv", "recvfrom"):
            payload = rec[2] if len(rec) > 2 else (rec[1] if len(rec) > 1 else b"")
            n = _b(payload)
            row = {
                "source": "native", "kind": kind,
                "label": "%s (%dB)" % (kind, n), "bytes": n,
                "raw": raw_rec,
            }
            if isinstance(payload, (bytes, bytearray)) and kind in ("send", "sendto", "sendmsg", "ssl_send"):
                try:
                    from core.native.boringssl_seam import parse_http1_request
                    parsed = parse_http1_request(bytes(payload))
                except Exception:
                    parsed = None
                if parsed is not None:
                    tag = "native/ssl" if kind == "ssl_send" else "native"
                    row["label"] = "[%s] %s %s (%dB)" % (tag, parsed["method"], parsed["url"], n)
                    row["method"] = parsed["method"]
                    row["url"] = parsed["url"]
                    row["req_headers"] = parsed["headers"]
                    row["req_body"] = parsed["body"].decode("utf-8", "replace")
            rows.append(row)
        elif kind == "curl_send":
            url = rec[1] if len(rec) > 1 else ""
            payload = rec[2] if len(rec) > 2 else b""
            n = _b(payload)
            msg_txt = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload or url)
            rows.append({
                "source": "native", "kind": "curl_send",
                "label": "[native/curl] %s (%dB)" % (msg_txt or url, n),
                "url": str(url), "bytes": n, "raw": raw_rec,
            })
        else:
            rows.append({"source": "native", "kind": str(kind), "label": str(kind), "raw": raw_rec})
    return rows


def unified(net_log=None, os_net=None):
    """Zusammengeführte, quellen-markierte Netz-Liste (Java zuerst, dann nativ)."""
    return normalize_java(net_log) + normalize_native(os_net)


def _try_decode_protobuf(body_raw_or_txt, hdrs):
    """Versucht gRPC/Protobuf-Wire-Body als JSON darzustellen (G7)."""
    import json as _json
    if not body_raw_or_txt:
        return ""
    ct = " ".join(str(v) for k, v in (hdrs or {}).items() if str(k).lower() == "content-type").lower()
    raw = None
    if isinstance(body_raw_or_txt, (bytes, bytearray)):
        raw = bytes(body_raw_or_txt)
    elif ("grpc" in ct or "protobuf" in ct) and isinstance(body_raw_or_txt, str):
        try:
            raw = body_raw_or_txt.encode("latin1")
        except Exception:
            raw = body_raw_or_txt.encode("utf-8", "replace")
    if not raw or ("grpc" not in ct and "protobuf" not in ct):
        return ""
    try:
        from core.java.net_protocols_g7 import decode_protobuf_wire
        # Optionales gRPC 5-Byte-Framing (1 Flag + 4 Len) abstreifen falls konsistent
        payload = raw
        if "grpc" in ct and len(raw) >= 5 and raw[0] in (0, 1):
            flen = int.from_bytes(raw[1:5], "big")
            if 0 < flen <= len(raw) - 5:
                payload = raw[5:5 + flen]
        dec = decode_protobuf_wire(payload)
        if dec:
            return "[Protobuf/gRPC Wire-Decode]:\n" + _json.dumps(dec, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return ""
    return ""


def _extract_graphql_info(url, body_text):
    """Erkennt GraphQL-Requests und extrahiert Operation, Query & Variablen lesbar."""
    import json as _json
    import re as _re

    u_low = str(url or "").lower()
    raw = str(body_text or "").strip()
    if not raw and "/graphql" not in u_low:
        return None
    obj = None
    if raw.startswith("{"):
        try:
            obj = _json.loads(raw)
        except Exception:
            obj = None
    if isinstance(obj, dict) and isinstance(obj.get("query"), str):
        q_raw = obj["query"].strip()
    elif "/graphql" in u_low and raw and any(k in raw for k in ("query ", "mutation ", "subscription ", "{")):
        q_raw = raw
        obj = {}
    else:
        return None
    if not q_raw:
        return None
    if "/graphql" not in u_low and not _re.search(r"^\s*(?:query|mutation|subscription|\{)", q_raw):
        return None

    m_head = _re.search(r"\b(query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)", q_raw)
    op_type = m_head.group(1) if m_head else "query"
    op_name = str((obj or {}).get("operationName") or (m_head.group(2) if m_head else "") or "").strip()
    if not op_name:
        m_f = _re.search(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)", q_raw)
        if m_f:
            op_name = m_f.group(1)

    try:
        from services.flutter_api_extractor_service import format_graphql_query
        q_pretty = format_graphql_query(q_raw)
    except Exception:
        q_pretty = q_raw

    variables = (obj or {}).get("variables")
    if not isinstance(variables, dict):
        variables = {}
    return {
        "op_type": op_type,
        "op_name": op_name,
        "query_pretty": q_pretty or q_raw,
        "variables": variables,
    }


def _format_body_pretty(body_text, body_len=0):
    """Formatiert JSON lesbar mit Einrueckung oder liefert Klartext."""
    import json as _json
    txt = "" if body_text is None else str(body_text)
    if not txt and not body_len:
        return "  <leer (0 Bytes)>"
    if not txt and body_len:
        return "  <binaer / nicht dekodierbar (%s Bytes)>" % body_len
    s = txt.strip()
    if s.startswith(("{", "[")):
        try:
            return _json.dumps(_json.loads(s), indent=2, ensure_ascii=False)
        except Exception:
            pass
    return txt


def format_net_entry_details(entry, net_mode=""):
    """Erzeugt eine lesbare Request/Response-Detailansicht fuer einen Netz-Eintrag."""
    from urllib.parse import parse_qsl, urlsplit

    if not isinstance(entry, dict):
        return ""
    src = entry.get("source", "java")
    if src == "native":
        lines = [
            "Quelle: [nativ] (OS-8 Socket / BoringSSL)",
            "Typ: %s" % entry.get("kind", "?"),
            "Details: %s" % entry.get("label", "?"),
        ]
        if entry.get("method") and entry.get("url"):
            lines.extend(["", "=== REQUEST ===", "%s %s" % (entry["method"], entry["url"])])
            hdrs = entry.get("req_headers") or {}
            if hdrs:
                lines.append("Request-Header:")
                for hk, hv in hdrs.items():
                    lines.append("  %s: %s" % (hk, hv))
            if entry.get("req_body"):
                lines.append("Request-Body:")
                lines.append(_format_body_pretty(entry["req_body"], len(str(entry["req_body"]))))
        if entry.get("raw") is not None:
            lines.append("Rohdaten: %r" % (entry.get("raw"),))
        return "\n".join(lines)

    req = entry.get("req") if isinstance(entry.get("req"), dict) else {}
    resp = entry.get("resp") if isinstance(entry.get("resp"), dict) else {}
    method = req.get("method") or entry.get("method") or "?"
    url = req.get("url") or entry.get("url") or "?"
    req_hdrs = req.get("headers") if isinstance(req.get("headers"), dict) else {}
    req_body = req.get("body") if req.get("body") is not None else req.get("body_preview", "")
    req_blen = req.get("body_len")
    if req_blen is None:
        req_blen = len(str(req_body or "").encode("utf-8", "replace"))

    code = resp.get("code", entry.get("code", "?"))
    msg = resp.get("message", "")
    res_hdrs = resp.get("headers") if isinstance(resp.get("headers"), dict) else {}
    res_body = resp.get("body") if resp.get("body") is not None else resp.get("body_preview", "")
    res_blen = resp.get("body_len")
    if res_blen is None:
        res_blen = entry.get("resp_bytes", len(str(res_body or "").encode("utf-8", "replace")))

    glass_src = res_hdrs.get("X-Glass-Source")
    is_mock_empty = (
        int(res_blen or 0) == 0
        and str(code) == "200"
        and (
            (isinstance(glass_src, str) and "mock-default" in glass_src)
            or (
                glass_src is None
                and str(net_mode or "").strip().lower() in ("mock", "mock/replay", "")
                and not res_hdrs
            )
        )
    )
    resp_source_label = glass_src or (str(net_mode) if net_mode else "-")

    lines = ["Quelle: [java]   |   Responder: %s" % resp_source_label]
    if is_mock_empty:
        lines.extend([
            "",
            "⚠ HINWEIS: Dieser Request wurde im Offline-Modus 'Mock/Replay' ohne passende Mock-Regel "
            "mit einem leeren 200-OK-Dummy (0 Bytes) beantwortet. Stelle unten 'Modus: Live-HTTP' ein, "
            "um echte Server-Antworten abzurufen!",
        ])

    g_info = _extract_graphql_info(url, req_body)
    if g_info:
        import json as _json
        op_hdr = ("%s %s" % (g_info["op_type"], g_info["op_name"])).strip()
        lines.extend([
            "",
            "=== GRAPHQL ===",
            "Operation: %s" % (op_hdr or "query"),
            "GraphQL-Query:",
        ])
        for qline in str(g_info["query_pretty"]).splitlines():
            lines.append("  " + qline)
        if g_info.get("variables"):
            lines.append("Variablen:")
            for vline in _json.dumps(g_info["variables"], indent=2, ensure_ascii=False).splitlines():
                lines.append("  " + vline)
        res_s = str(res_body or "").strip()
        if res_s.startswith("{"):
            try:
                res_obj = _json.loads(res_s)
                if isinstance(res_obj, dict):
                    if res_obj.get("errors"):
                        errs = res_obj["errors"]
                        e0 = errs[0] if isinstance(errs, list) and errs else errs
                        emsg = e0.get("message") or e0.get("errorType") or e0 if isinstance(e0, dict) else str(e0)
                        lines.append("GraphQL-Antwort: FEHLER — %s" % emsg)
                    elif "data" in res_obj:
                        d = res_obj["data"]
                        keys = ", ".join(str(k) for k in d.keys()) if isinstance(d, dict) else type(d).__name__
                        lines.append("GraphQL-Antwort: data (%s)" % (keys or "ok"))
            except Exception:
                pass

    lines.extend(["", "=== REQUEST ===", "%s %s" % (method, url)])
    try:
        qparams = parse_qsl(urlsplit(str(url)).query, keep_blank_values=True)
    except Exception:
        qparams = []
    if qparams:
        lines.append("Query-Parameter:")
        for qk, qv in qparams:
            lines.append("  %s = %s" % (qk, qv))

    lines.append("Request-Header:")
    if req_hdrs:
        for hk, hv in req_hdrs.items():
            lines.append("  %s: %s" % (hk, hv))
    else:
        lines.append("  <keine>")

    lines.append("Request-Body (%s Bytes):" % req_blen)
    lines.append(_format_body_pretty(req_body, int(req_blen or 0)))
    _pb_req = _try_decode_protobuf(req.get("body_raw") if "body_raw" in req else req_body, req_hdrs)
    if _pb_req:
        lines.append(_pb_req)

    status_line = "Status: %s%s (%s Bytes)" % (code, (" " + str(msg)) if msg else "", res_blen)
    lines.extend(["", "=== RESPONSE ===", status_line, "Response-Header:"])
    if res_hdrs:
        for hk, hv in res_hdrs.items():
            lines.append("  %s: %s" % (hk, hv))
    else:
        lines.append("  <keine>")

    lines.append("Response-Body (%s Bytes):" % res_blen)
    lines.append(_format_body_pretty(res_body, int(res_blen or 0)))
    _pb_res = _try_decode_protobuf(resp.get("body_raw") if "body_raw" in resp else res_body, res_hdrs)
    if _pb_res:
        lines.append(_pb_res)
    return "\n".join(lines)
