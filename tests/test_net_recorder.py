# -*- coding: utf-8 -*-
"""P3 Netz-Säule: Java-Netz-Recorder (HttpURLConnection) — Record + Replay + Modell. Headless."""
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString, JArray
from core.java.net_recorder import (install_net_stubs, NetRequest, NetResponse, NetLog,
                                    ReplayResponder, default_responder)


def _conn(stubs, url="https://api.example/x"):
    u = JObject("Ljava/net/URL;")
    stubs.resolve("Ljava/net/URL;", "<init>", "(Ljava/lang/String;)V", [u, JString(url)], False)
    _, c = stubs.resolve("Ljava/net/URL;", "openConnection", "()Ljava/net/URLConnection;", [u], False)
    return c


def _read_all(stubs, conn):
    _, ins = stubs.resolve("x", "getInputStream", "()Ljava/io/InputStream;", [conn], False)
    buf = JArray("B", [0] * 256)
    _, n = stubs.resolve("x", "read", "([BII)I", [ins, buf, 0, 256], False)
    return bytes(buf.data[:n]) if n > 0 else b""


def test_model_basics():
    r = NetRequest("POST", "http://x", {"A": "1"}, b"hi")
    assert r.to_dict()["method"] == "POST" and r.to_dict()["body_len"] == 2
    resp = NetResponse(200, {}, "ok")
    assert resp.body == b"ok"


def test_record_request_roundtrip():
    stubs = AndroidStubs()
    resp = NetResponse(200, {"Content-Type": "application/json"}, b'{"glucose":100}')
    log = install_net_stubs(stubs, responder=ReplayResponder({"https://api.example/x": resp}))
    conn = _conn(stubs)
    stubs.resolve("x", "setRequestMethod", "(Ljava/lang/String;)V", [conn, JString("POST")], False)
    stubs.resolve("x", "setRequestProperty", "(Ljava/lang/String;Ljava/lang/String;)V",
                  [conn, JString("Authorization"), JString("Bearer t")], False)
    _, out = stubs.resolve("x", "getOutputStream", "()Ljava/io/OutputStream;", [conn], False)
    stubs.resolve("x", "write", "([B)V", [out, JArray("B", list(b'{"email":"a"}'))], False)
    _, code = stubs.resolve("x", "getResponseCode", "()I", [conn], False)
    assert code == 200
    assert len(log) == 1
    req = log.entries[0]["req"]
    assert req.method == "POST" and req.url == "https://api.example/x"
    assert req.headers.get("Authorization") == "Bearer t"
    assert req.body == b'{"email":"a"}'          # Request-Body aufgezeichnet
    assert _read_all(stubs, conn) == b'{"glucose":100}'   # konfigurierte Antwort geliefert


def test_replay_responder_url_and_substring():
    rr = ReplayResponder()
    rr.add("GET https://a/latest", NetResponse(200, {}, b"EXACT"))
    rr.add("/glucose", NetResponse(200, {}, b"SUBSTR"))
    assert rr(NetRequest("GET", "https://a/latest")).body == b"EXACT"
    assert rr(NetRequest("GET", "https://a/v1/glucose/now")).body == b"SUBSTR"
    assert rr(NetRequest("GET", "https://a/unknown")).code == 200   # Default


def test_record_mode_default_logs():
    stubs = AndroidStubs()
    log = install_net_stubs(stubs)                 # kein Responder -> Record-Default 200
    conn = _conn(stubs, "https://api.example/ping")
    _, code = stubs.resolve("x", "getResponseCode", "()I", [conn], False)
    assert code == 200 and len(log) == 1
    assert log.entries[0]["req"].url == "https://api.example/ping"


def test_net_event_published():
    from core.application.event_bus import EventBus
    seen = []
    EventBus.subscribe("NET_REQUEST", lambda d: seen.append(d))
    stubs = AndroidStubs()
    install_net_stubs(stubs, responder=ReplayResponder({"/x": NetResponse(201, {}, b"")}))
    conn = _conn(stubs, "https://h/x")
    stubs.resolve("x", "getResponseCode", "()I", [conn], False)
    assert seen and seen[-1]["resp"]["code"] == 201
    EventBus.clear()


def test_net_recorded_during_vm_run():
    """HTTP-Call aus laufendem Smali (wie im gläsernen App-Lauf) landet im NetLog."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.lifecycle import _StubRuntime
    NET = """.class public Lt/NetAct;
.super Ljava/lang/Object;
.method public static run()I
    .registers 4
    new-instance v0, Ljava/net/URL;
    const-string v1, "https://api/glucose"
    invoke-direct {v0, v1}, Ljava/net/URL;-><init>(Ljava/lang/String;)V
    invoke-virtual {v0}, Ljava/net/URL;->openConnection()Ljava/net/URLConnection;
    move-result-object v2
    const-string v3, "POST"
    invoke-virtual {v2, v3}, Ljava/net/HttpURLConnection;->setRequestMethod(Ljava/lang/String;)V
    invoke-virtual {v2}, Ljava/net/HttpURLConnection;->getResponseCode()I
    move-result v3
    return v3
.end method
"""
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(NET)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    log = install_net_stubs(stubs, responder=ReplayResponder(
        {"/glucose": NetResponse(200, {}, b'{"value":112}')}))
    vm.runtime = _StubRuntime(stubs)
    code = vm.invoke("Lt/NetAct;", "run", "()I", ())
    assert code == 200
    assert len(log) == 1
    assert log.entries[0]["req"].method == "POST"
    assert log.entries[0]["req"].url == "https://api/glucose"


def test_net_marker_during_debugged_run():
    """Netz-Call unter laufendem Debugger erzeugt einen 'net'-Marker auf der Zeitachse."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.lifecycle import _StubRuntime
    from core.java.debug import JavaDebugger
    NET = """.class public Lt/NetDbg;
.super Ljava/lang/Object;
.method public static run()I
    .registers 4
    new-instance v0, Ljava/net/URL;
    const-string v1, "https://api/glucose"
    invoke-direct {v0, v1}, Ljava/net/URL;-><init>(Ljava/lang/String;)V
    invoke-virtual {v0}, Ljava/net/URL;->openConnection()Ljava/net/URLConnection;
    move-result-object v2
    invoke-virtual {v2}, Ljava/net/HttpURLConnection;->getResponseCode()I
    move-result v3
    return v3
.end method
"""
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(NET)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    install_net_stubs(stubs, responder=ReplayResponder(
        {"/glucose": NetResponse(200, {}, b'{"value":112}')}))
    vm.runtime = _StubRuntime(stubs)
    jdb = JavaDebugger(vm)
    jdb.start("Lt/NetDbg;", "run", "()I", (), stop_at_entry=False)
    for _ in range(10):
        if jdb.mode in ("done", "stopped", "error"):
            break
        jdb.cont()
    nm = [m for m in jdb.marker_list() if m["kind"] == "net"]
    assert len(nm) == 1
    assert "glucose" in nm[0]["label"]
    assert "200" in nm[0]["label"]
