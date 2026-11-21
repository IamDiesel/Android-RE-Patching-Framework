# -*- coding: utf-8 -*-
"""Tests fuer Paket 6C & 6D: NetCondition (Online/Gedrosselt/Offline/Timeout),
OS-8-Kopplung, DbReplayResponder, OkHttp3, RN Networking & MCP Rules/Glass-Tools."""
from __future__ import annotations

import json
import time
import pytest

from core.java.dalvik import DalvikVM, DalvikException
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.net_recorder import (
    NetCondition,
    NetLog,
    NetResponse,
    DbReplayResponder,
    ReplayResponder,
    install_net_stubs,
)
from core.java.objects import JObject, JString
from core.java.rn_hermes_bridge import TurboModuleRouter
from services.api_db_service import ApiDbService
from mcp_server import tools


def _mk_url_stubs(net_condition=None, responder=None, api_db=None, rules_path=None, os_eng=None):
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    log = NetLog()
    install_net_stubs(
        stubs,
        responder=responder,
        net_log=log,
        api_db=api_db,
        rules_path=rules_path,
        net_condition=net_condition,
        os_eng=os_eng,
    )
    return vm, stubs, log


def test_net_condition_offline_raises_io_and_syncs_os8():
    class _FakeEng:
        class _Os:
            net = {"mode": "online", "log": []}
        _os = _Os()

    eng = _FakeEng()
    nc = NetCondition(mode="offline", offline_error="Offline test")
    vm, stubs, log = _mk_url_stubs(net_condition=nc, os_eng=eng)
    assert eng._os.net["mode"] == "offline"

    _, url_obj = stubs.resolve("Ljava/net/URL;", "<init>", "(Ljava/lang/String;)V",
                               [JObject("Ljava/net/URL;", {}), JString("https://api.example/x")], False)
    _, conn = stubs.resolve("Ljava/net/URL;", "openConnection", "()Ljava/net/URLConnection;", [url_obj], False)
    with pytest.raises(DalvikException) as exc:
        stubs.resolve("Ljava/net/HttpURLConnection;", "getResponseCode", "()I", [conn], False)
    assert "IOException" in str(exc.value)
    assert len(log) == 1
    assert log.entries[0]["resp"].code == 0
    assert log.entries[0]["resp"].message == "OFFLINE"

    # Toggle back to online -> OS-8 follows
    stubs.set_net_condition({"mode": "online"})
    assert eng._os.net["mode"] == "online"
    _, conn2 = stubs.resolve("Ljava/net/URL;", "openConnection", "()Ljava/net/URLConnection;", [url_obj], False)
    ok, code = stubs.resolve("Ljava/net/HttpURLConnection;", "getResponseCode", "()I", [conn2], False)
    assert ok and code == 200


def test_net_condition_throttled_applies_latency():
    nc = NetCondition(mode="throttled", latency_ms=80, kbps=0)
    _vm, stubs, log = _mk_url_stubs(
        net_condition=nc,
        responder=ReplayResponder({"/slow": NetResponse(200, {}, b"hello", "OK")}),
    )
    _, u = stubs.resolve("Ljava/net/URL;", "<init>", "(Ljava/lang/String;)V",
                         [JObject("Ljava/net/URL;", {}), JString("https://example.org/slow")], False)
    _, conn = stubs.resolve("Ljava/net/URL;", "openConnection", "()Ljava/net/URLConnection;", [u], False)
    t0 = time.monotonic()
    ok, code = stubs.resolve("Ljava/net/HttpURLConnection;", "getResponseCode", "()I", [conn], False)
    dt = time.monotonic() - t0
    assert ok and code == 200
    assert dt >= 0.05
    assert len(log) == 1


def test_db_replay_responder_and_okhttp_and_rn_networking(tmp_path):
    db_path = str(tmp_path / "api_traffic.db")
    db = ApiDbService(db_path)
    db.insert_packet({
        "timestamp": time.time(),
        "method": "POST",
        "url": "https://api.test/login",
        "status": 201,
        "req_headers": "{}",
        "req_body": "{}",
        "res_headers": json.dumps({"X-From": "db"}),
        "res_body": '{"token":"abc"}',
        "comment": "seed",
    })
    vm, stubs, log = _mk_url_stubs(responder=DbReplayResponder(db), api_db=None)

    # 1) OkHttp3 pipeline
    _, b = stubs.resolve("Lokhttp3/Request$Builder;", "<init>", "()V", [JObject("Lokhttp3/Request$Builder;", {})], False)
    _, b = stubs.resolve("Lokhttp3/Request$Builder;", "url", "(Ljava/lang/String;)Lokhttp3/Request$Builder;",
                         [JObject("Lokhttp3/Request$Builder;", {}), JString("https://api.test/login")], False)
    _, b = stubs.resolve("Lokhttp3/Request$Builder;", "method",
                         "(Ljava/lang/String;Lokhttp3/RequestBody;)Lokhttp3/Request$Builder;",
                         [b, JString("POST"), None], False)
    _, req = stubs.resolve("Lokhttp3/Request$Builder;", "build", "()Lokhttp3/Request;", [b], False)
    _, call = stubs.resolve("Lokhttp3/OkHttpClient;", "newCall", "(Lokhttp3/Request;)Lokhttp3/Call;",
                            [JObject("Lokhttp3/OkHttpClient;", {}), req], False)
    _, resp = stubs.resolve("Lokhttp3/RealCall;", "execute", "()Lokhttp3/Response;", [call], False)
    _, code = stubs.resolve("Lokhttp3/Response;", "code", "()I", [resp], False)
    _, body = stubs.resolve("Lokhttp3/Response;", "body", "()Lokhttp3/ResponseBody;", [resp], False)
    _, txt = stubs.resolve("Lokhttp3/ResponseBody;", "string", "()Ljava/lang/String;", [body], False)
    assert code == 201
    assert txt.text == '{"token":"abc"}'

    # 2) RN Networking TurboModule
    router = TurboModuleRouter(vm=vm)
    res = router.call("Networking", "sendRequest", [{"method": "POST", "url": "https://api.test/login"}])
    assert res["status"] == 201
    assert "abc" in res["body"]
    assert len(log) == 2


def test_mcp_api_rules_and_glass_run_net_condition(tmp_path):
    class _Cfg:
        paths = {
            "API_RULES": str(tmp_path / "rules.json"),
            "API_DB": str(tmp_path / "api.db"),
        }
        config = {"APP_PACKAGE": "pkg.test"}

    class _Ctx:
        cfg = _Cfg()

    ctx = _Ctx()
    set_res = tools.api_rules_set(ctx, json.dumps([
        {"url_match": "/ping", "action": "replace_res_body", "payload": '{"pong":1}'}
    ]))
    assert set_res["ok"] and set_res["count"] == 1
    lst = tools.api_rules_list(ctx)
    assert lst["ok"] and lst["rules"][0]["url_match"] == "/ping"

    # glass_run with offline net_condition fails HTTP connection gracefully in lenient VM
    app = tmp_path / "app"
    (app / "smali" / "t").mkdir(parents=True)
    (app / "smali" / "t" / "NetAct.smali").write_text(
        ".class public Lt/NetAct;\n"
        ".super Landroid/app/Activity;\n"
        ".method public onCreate(Landroid/os/Bundle;)V\n"
        "    .registers 4\n"
        "    new-instance v0, Ljava/net/URL;\n"
        "    const-string v1, \"https://api.test/ping\"\n"
        "    invoke-direct {v0, v1}, Ljava/net/URL;-><init>(Ljava/lang/String;)V\n"
        "    invoke-virtual {v0}, Ljava/net/URL;->openConnection()Ljava/net/URLConnection;\n"
        "    move-result-object v2\n"
        "    invoke-virtual {v2}, Ljava/net/HttpURLConnection;->getResponseCode()I\n"
        "    return-void\n"
        ".end method\n",
        encoding="utf-8",
    )
    gr = tools.native_glass_run(
        ctx,
        studio_root=str(app),
        zone="original",
        entry="Lt/NetAct;",
        net_condition=json.dumps({"mode": "offline"}),
    )
    assert gr["ok"] is True
    assert gr["net_count"] >= 1
    assert gr["net_log"][0]["resp"]["code"] == 0
    assert gr["net_condition"]["mode"] == "offline"
