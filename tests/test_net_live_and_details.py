# -*- coding: utf-8 -*-
"""Tests fuer dynamisches Netz-Modus-Routing, Live-HTTP (gzip/HTTPError), DTO-Koerzierung und Detail-Ansicht."""
import gzip
import io
import json
import urllib.error
import urllib.request

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.net_recorder import (
    NetLog,
    NetRequest,
    NetResponse,
    _json_to_jobj,
    install_net_stubs,
)
from core.java.net_unified import format_net_entry_details, unified
from core.java.objects import JObject, JString
from ui.controllers.debugger_controller import DebuggerController


class _DummyNetCtrl:
    """Minimal-Stub fuer die Netz-Methoden des DebuggerController."""

    def __init__(self):
        self._net_responder = None
        self.net_mode = "mock"
        self._api_db = None
        self._mock_hint_urls = set()
        self._logs = []

    def _log(self, msg):
        self._logs.append(msg)

    set_net_mode = DebuggerController.set_net_mode
    _match_net_mock = DebuggerController._match_net_mock
    _dispatch_net_request = DebuggerController._dispatch_net_request
    _decode_live_body = staticmethod(DebuggerController._decode_live_body)
    _net_responder_live = DebuggerController._net_responder_live
    add_net_mock = DebuggerController.add_net_mock
    clear_net_mocks = DebuggerController.clear_net_mocks
    net_mocks = DebuggerController.net_mocks


class _FakeProvider:
    def __init__(self, sources):
        self.sources = dict(sources)

    def get_source(self, cls):
        return self.sources.get(cls, "")


class _FakeUrllibResp:
    def __init__(self, status, headers, body_bytes, reason="OK"):
        self.status = status
        self.headers = headers
        self._body = body_bytes
        self.reason = reason

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_set_net_mode_parses_mock_replay_correctly():
    c = _DummyNetCtrl()
    c.set_net_mode("Live-HTTP")
    assert c.net_mode == "live"
    c.set_net_mode("Mock/Replay")
    assert c.net_mode == "mock"
    c.set_net_mode("DB-Replay")
    assert c.net_mode == "db_replay"


def test_dynamic_net_mode_switching_without_reinstall(monkeypatch):
    c = _DummyNetCtrl()
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    log = NetLog()
    install_net_stubs(stubs, responder=c._dispatch_net_request, net_log=log)

    # 1) Default mock mode without rule -> 200 (0B) + X-Glass-Source mock-default
    r1 = stubs.net_fire("GET", "https://api.example.com/user/current?api_key=abc", {}, b"")
    assert r1.code == 200
    assert r1.body == b""
    assert "mock-default" in r1.headers.get("X-Glass-Source", "")
    assert any("Live-HTTP" in m for m in c._logs)

    # 2) Switch live mid-session without calling install_net_stubs again
    captured = {}

    def _fake_urlopen(req, timeout=15):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        payload = json.dumps({"statuscode": 200, "statusmessage": "OK", "result": {"id": 42, "club_ids": [7]}}).encode("utf-8")
        return _FakeUrllibResp(200, {"Content-Encoding": "gzip", "Content-Type": "application/json"}, gzip.compress(payload))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    c.set_net_mode("Live-HTTP")

    r2 = stubs.net_fire("GET", "https://api.example.com/user/current", {"Authorization": "Basic dXNlcjpwdw=="}, b"")
    assert r2.code == 200
    assert json.loads(r2.body.decode("utf-8"))["result"]["id"] == 42
    assert r2.headers.get("X-Glass-Source") == "live-http"
    assert captured["headers"].get("authorization") == "Basic dXNlcjpwdw=="
    assert "okhttp" in captured["headers"].get("user-agent", "").lower()

    # 3) Explicit mock rule overrides even while in live mode, immediately
    c.add_net_mock("/user/current", 200, '{"statuscode":200,"result":{"id":99}}', method="GET")
    r3 = stubs.net_fire("GET", "https://api.example.com/user/current", {}, b"")
    assert json.loads(r3.body.decode("utf-8"))["result"]["id"] == 99
    assert r3.headers.get("X-Glass-Source") == "mock-rule"


def test_net_responder_live_preserves_http_error_status_and_body(monkeypatch):
    c = _DummyNetCtrl()

    def _fake_urlopen_401(req, timeout=15):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":"Invalid credentials"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_401)
    resp = c._net_responder_live(NetRequest("GET", "https://api.example.com/user/current", {}, b""))
    assert resp.code == 401
    assert resp.body == b'{"error":"Invalid credentials"}'
    assert resp.headers.get("X-Glass-Source") == "live-http"


def test_to_dict_unified_and_format_details():
    req = NetRequest(
        "GET",
        "https://api.example.com/api/v0/user/current?api_key=k1&sync_from=0",
        {"Authorization": "Basic secret", "App-Version": "12.4.2"},
        b"",
    )
    resp_empty = NetResponse(200, {"X-Glass-Source": "mock-default"}, b"", "OK")
    log = NetLog()
    log.record(req, resp_empty)

    rows = unified(log, None)
    assert len(rows) == 1
    assert rows[0]["req"]["headers"]["Authorization"] == "Basic secret"
    assert rows[0]["resp"]["body"] == ""

    txt_mock = format_net_entry_details(rows[0], net_mode="mock")
    assert "=== REQUEST ===" in txt_mock
    assert "Authorization: Basic secret" in txt_mock
    assert "api_key = k1" in txt_mock
    assert "=== RESPONSE ===" in txt_mock
    assert "Live-HTTP" in txt_mock

    resp_json = NetResponse(200, {"X-Glass-Source": "live-http"}, b'{"result":{"id":7}}', "OK")
    log2 = NetLog()
    log2.record(req, resp_json)
    txt_live = format_net_entry_details(unified(log2, None)[0], net_mode="live")
    assert '"id": 7' in txt_live
    assert "HINWEIS" not in txt_live


def test_json_to_jobj_dto_field_coercion_and_integer_tostring():
    vm = DalvikVM()
    vm.class_provider = _FakeProvider({
        "Lcom/example/ApiResponse;": (
            ".field private final statuscode:I\n"
            ".field private final statusmessage:Ljava/lang/String;\n"
            ".field private final result:Ljava/lang/Object;\n"
        ),
        "Lcom/example/UserProfile;": (
            ".field private final id:I\n"
            ".field private final club_ids:Ljava/util/List;\n"
        ),
    })
    stubs = AndroidStubs(vm=vm)

    payload = {
        "statuscode": "200",
        "statusmessage": 123,
        "result": {
            "id": "555",
            "club_ids": [101],
        },
    }
    obj = _json_to_jobj(payload, "Lcom/example/ApiResponse;", ["Lcom/example/ApiResponse;", "Lcom/example/UserProfile;"], vm, 0)
    assert isinstance(obj, JObject)
    assert obj.fields["statuscode"] == 200
    assert isinstance(obj.fields["statusmessage"], JString)
    assert obj.fields["statusmessage"].text == "123"

    prof = obj.fields["result"]
    assert isinstance(prof, JObject) and prof.cls == "Lcom/example/UserProfile;"
    assert prof.fields["id"] == 555

    # Integer.toString() if wrapped value is JString
    ok, res = stubs.resolve(
        "Ljava/lang/Integer;",
        "toString",
        "()Ljava/lang/String;",
        (JObject("Ljava/lang/Integer;", {"value": JString("789")}),),
        False,
    )
    assert ok and isinstance(res, JString) and res.text == "789"


def test_debugger_tab_net_split_view_displays_request_response_details():
    import pytest
    tk = pytest.importorskip("tkinter")
    from core.application.event_bus import EventBus
    from ui.tabs.debugger_tab import DebuggerTab

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Keine Tk-Display-Umgebung: {exc}")

    class _App:
        def after(self, ms, fn):
            fn()

    ctrl = _DummyNetCtrl()
    ctrl.net_log = NetLog()
    ctrl.net_log.record(
        NetRequest("GET", "https://api.example.com/v0/user/current?api_key=123", {"Authorization": "Basic abc"}, b""),
        NetResponse(200, {"X-Glass-Source": "live-http"}, b'{"statuscode":200,"result":{"id":7,"club_ids":[3]}}', "OK"),
    )

    class _StubTab:
        def __init__(self):
            self.ctrl = ctrl
            self.app = _App()
            self.os_model = None

    tab = _StubTab()
    frame = DebuggerTab._v_net(tab, root)
    root.update_idletasks()

    # Detail-TextWidget muss initial gefuellt sein
    text_widgets = []

    def _walk(w):
        if isinstance(w, tk.Text):
            text_widgets.append(w)
        for ch in w.winfo_children():
            _walk(ch)

    _walk(frame)
    assert text_widgets, "Detail-Textfeld im Netz-Tab fehlt"
    txt_val = text_widgets[0].get("1.0", "end")
    assert "Authorization: Basic abc" in txt_val
    assert '"club_ids"' in txt_val

    # Neues Event via EventBus -> aktualisiert Detailansicht live
    EventBus.publish("NET_REQUEST", {
        "req": {"method": "POST", "url": "https://api.example.com/join", "headers": {"X-Test": "1"}, "body_len": 2, "body": "{}"},
        "resp": {"code": 201, "message": "Created", "headers": {"X-Glass-Source": "live-http"}, "body_len": 11, "body": '{"ok":true}'},
    })
    root.update_idletasks()
    txt_val2 = text_widgets[0].get("1.0", "end")
    assert "X-Test: 1" in txt_val2
    assert '"ok": true' in txt_val2

    try:
        frame.destroy()
        root.destroy()
    except Exception:
        pass
