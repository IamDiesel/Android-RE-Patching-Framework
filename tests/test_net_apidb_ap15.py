# -*- coding: utf-8 -*-
"""AP 1.5: intercept_rules (replace_req/res_body) + insert_packet in api_traffic.db beim Glass-Lauf."""
import json, tempfile, os
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.net_recorder import install_net_stubs, NetRequest, NetResponse
from core.java.objects import JObject, JString


class _FakeDb:
    def __init__(self): self.packets = []
    def insert_packet(self, d): self.packets.append(d)


def _rules_file(rules):
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rules, fh)
    return p


def test_replace_res_body_and_insert_packet():
    st = AndroidStubs(vm=DalvikVM())
    db = _FakeDb()
    rules = [{"url_match": "example.com/api", "action": "replace_res_body", "payload": "{\"glukose\":120}"}]
    rp = _rules_file(rules)
    try:
        # Responder liefert zunaechst etwas anderes -> Regel ueberschreibt
        install_net_stubs(st, responder=lambda req: NetResponse(200, {}, b"ORIGINAL", "OK"),
                          api_db=db, rules_path=rp)
        # HttpURLConnection-artiges conn-Objekt mit URL, dann getResponseCode-Stub ausloesen
        conn = JObject("Ljava/net/HttpURLConnection;")
        conn.fields["$url"] = "https://example.com/api/glucose"
        conn.fields["$method"] = "GET"
        h, code = st.resolve("Ljava/net/HttpURLConnection;", "getResponseCode", "()I", [conn], False)
        assert code == 200
        resp = conn.fields.get("$resp")
        assert resp is not None and resp.body == b'{"glukose":120}'      # replace_res_body wirkte
        assert len(db.packets) == 1
        pkt = db.packets[0]
        assert pkt["url"] == "https://example.com/api/glucose"
        assert pkt["res_body"] == '{"glukose":120}'
        assert pkt["status"] == 200
    finally:
        os.unlink(rp)


def test_replace_req_body():
    st = AndroidStubs(vm=DalvikVM())
    seen = {}
    rules = [{"url_match": "login", "action": "replace_req_body", "payload": "PATCHED"}]
    rp = _rules_file(rules)
    try:
        def _resp(req):
            seen["body"] = req.body
            return NetResponse(200, {}, b"ok", "OK")
        install_net_stubs(st, responder=_resp, rules_path=rp)
        conn = JObject("Ljava/net/HttpURLConnection;")
        conn.fields["$url"] = "https://x/login"; conn.fields["$method"] = "POST"
        conn.fields["$body"] = b"ORIG"
        st.resolve("Ljava/net/HttpURLConnection;", "getResponseCode", "()I", [conn], False)
        assert seen["body"] == b"PATCHED"      # Request-Body wurde vor dem Responder ersetzt
    finally:
        os.unlink(rp)
