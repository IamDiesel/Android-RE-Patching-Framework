# -*- coding: utf-8 -*-
"""tests/test_testing_feedback_fixes.py — Verifikation der 6 Testing-Feedback-Punkte:
1. Branding: "Android RE Patching Framework" statt "Kippy RE V8"
2. Babycam Einstellungsmenü: Kategorien, Switches, korrekte Ausrichtung & Toggle-Interaktion
3. Virtuagym Login: Pattern/Matcher/Locale/CASE_INSENSITIVE_ORDER + 200 Navigation + 401 Dialog
4. Kippy Netz-Transparenz & Browser/Inspect: Statische Asset-URLs, WebView-Hooks, DOM/API-Inspektor, Native-Socket-Sync
5. Vereinheitlichter Multi-Tool-Favoritenmanager: LibForge/LibCarve/Smali/Hex/Lib-Replace + Kombinieren + MCP-Parität
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# 1. Branding ("Android RE Patching Framework")
# ---------------------------------------------------------------------------
def test_branding_gui_title_and_no_kippy_re():
    gui_path = os.path.join(_ROOT, "gui.py")
    with open(gui_path, encoding="utf-8") as f:
        src = f.read()
    assert "Android RE Patching Framework" in src
    assert "Kippy RE V8" not in src
    assert "Kippy RE-Framework" not in src


# ---------------------------------------------------------------------------
# 2. Babycam Einstellungsmenü (Preferences / Switches / Toggle)
# ---------------------------------------------------------------------------
def test_babycam_settings_menu_layout_and_switch_toggle():
    babycam_root = os.path.join(_ROOT, "source", "com.arjonasoftware.babycam", "base_unpacked_apktool")
    if not os.path.isdir(babycam_root):
        pytest.skip("Babycam APK nicht lokal entpackt vorhanden")

    from core.java.glass_runner import GlassAppRunner
    from core.java.surface import SceneGraphSurface

    gr = GlassAppRunner(babycam_root, zone="original")
    gr.build(native_fusion=False)
    gr.run(entry=None, max_steps=400000, native_fusion=False)
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf.paint()

    btn = gr.tree.root.find("button_settings")
    assert btn is not None
    bx, by, bw, bh = btn.bounds
    gr.vm.steps = 0
    gr.vm.max_steps = 400000
    gr.vm.step_budget = 400000
    assert surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2}) is True

    gr.vm.steps = 0
    gr.vm.max_steps = 800000
    gr.vm.step_budget = 800000
    assert gr.runner.navigate_to_pending() is True
    cmds = surf.paint()

    all_nodes = list(gr.tree.root.walk())
    switches = [n for n in all_nodes if "Switch" in n.cls or "CheckBox" in n.cls]
    assert len(switches) >= 15, f"Erwartet >=15 Preference-Switches, gefunden: {len(switches)}"

    literal_switch_cmds = [c for c in cmds if c.get("kind") == "text" and c.get("text") == "Switch"]
    assert not literal_switch_cmds, f"Literaler 'Switch'-Text darf nicht gerendert werden: {literal_switch_cmds}"

    assert gr.tree.root.bounds[3] > 900, f"Settings-Scrollhoehe zu klein: {gr.tree.root.bounds}"

    # Switch "Show the clock" (clickable Row) anklicken und nach Adapter-Rebind prüfen -> checked wechselt
    clk_tv = next((n for n in all_nodes if "Show the clock" in (getattr(n, "text", "") or "")), None)
    assert clk_tv is not None
    row_node = clk_tv
    while getattr(row_node, "_parent", None) is not None and not row_node.clickable:
        row_node = row_node._parent
    target_sw = next((x for x in row_node.walk() if "Switch" in x.cls or "CheckBox" in x.cls), None)
    assert row_node is not None and row_node.clickable and target_sw is not None
    before_chk = bool(target_sw.props.get("checked", False))
    gr.vm.steps = 0
    gr.vm.max_steps = 400000
    gr.vm.step_budget = 400000
    bx, by, bw, bh = row_node.bounds
    assert surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2}) is True
    gr.runner._drain_runnables()
    surf.paint()
    clk_tv_after = next((n for n in gr.tree.root.walk() if "Show the clock" in (getattr(n, "text", "") or "")), None)
    row_after = clk_tv_after
    while getattr(row_after, "_parent", None) is not None and not row_after.clickable:
        row_after = row_after._parent
    sw_after = next((x for x in row_after.walk() if "Switch" in x.cls or "CheckBox" in x.cls), None)
    assert sw_after is not None
    assert bool(sw_after.props.get("checked", False)) != before_chk


# ---------------------------------------------------------------------------
# 3. Virtuagym Login + Java Regex / Locale / CASE_INSENSITIVE_ORDER
# ---------------------------------------------------------------------------
def test_java_regex_locale_and_case_insensitive_stubs():
    from core.java.dalvik import DalvikVM, JString
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)

    def call_stub(cls, name, sig, args, static=False):
        ok, res = stubs.resolve(cls, name, sig, args, static=static)
        assert ok, f"Stub nicht gefunden: {cls}->{name}{sig}"
        return res

    pat = call_stub("Ljava/util/regex/Pattern;", "compile", "(Ljava/lang/String;)Ljava/util/regex/Pattern;", (JString(r"\|"),), static=True)
    assert pat is not None
    parts = call_stub("Ljava/util/regex/Pattern;", "split", "(Ljava/lang/CharSequence;)[Ljava/lang/String;", (pat, JString("100|200|300")))
    assert [x.text for x in parts.data] == ["100", "200", "300"]

    mat = call_stub("Ljava/util/regex/Pattern;", "matcher", "(Ljava/lang/CharSequence;)Ljava/util/regex/Matcher;", (pat, JString("a|b")))
    assert call_stub("Ljava/util/regex/Matcher;", "find", "()Z", (mat,)) == 1
    repl = call_stub("Ljava/util/regex/Matcher;", "replaceAll", "(Ljava/lang/String;)Ljava/lang/String;", (mat, JString(",")))
    assert repl.text == "a,b"

    loc = call_stub("Ljava/util/Locale;", "getDefault", "()Ljava/util/Locale;", (), static=True)
    lang = call_stub("Ljava/util/Locale;", "getLanguage", "()Ljava/lang/String;", (loc,))
    assert lang is not None and lang.text == "de"

    cmp_obj = vm.statics["Ljava/lang/String;"]["CASE_INSENSITIVE_ORDER"]
    assert call_stub("Ljava/lang/String$CaseInsensitiveComparator;", "compare", "(Ljava/lang/String;Ljava/lang/String;)I",
                     (cmp_obj, JString("Content-Type"), JString("content-type"))) == 0


def test_virtuagym_login_200_navigates_and_401_shows_dialog():
    vg_root = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")
    if not os.path.isdir(vg_root):
        pytest.skip("Virtuagym APK nicht lokal entpackt vorhanden")

    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.surface import SceneGraphSurface

    entry = "Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;"

    def _run_login(status_code: int):
        def responder(_req):
            if status_code == 200:
                body = json.dumps({
                    "statuscode": 200, "statusmessage": "OK", "result_count": 1, "timestamp": 1700000000,
                    "result": {
                        "id": 12345, "username": "testuser", "email": "test@example.com",
                        "first_name": "Max", "last_name": "Mustermann", "gender": "m",
                        "club_list": "100|200", "pro": 1,
                    },
                }).encode("utf-8")
                return NetResponse(200, {"Content-Type": "application/json"}, body, "OK")
            body = json.dumps({"statuscode": 401, "statusmessage": "Invalid credentials"}).encode("utf-8")
            return NetResponse(401, {"Content-Type": "application/json"}, body, "Unauthorized")

        gr = GlassAppRunner(vg_root, zone="original")
        gr.build(net_responder=responder, native_fusion=False)
        gr.run(entry=entry, max_steps=400000, native_fusion=False)
        tree, stubs, runner = gr.tree, gr.stubs, gr.runner
        surf = SceneGraphSurface(tree, vm=gr.vm)
        surf.paint()

        open_btn = tree.root.find("log_in_text_button")
        assert open_btn is not None
        stubs._clock_ms = getattr(stubs, "_clock_ms", 1700000000000) + 2000
        gr.vm.steps = 0
        gr.vm.max_steps = 400000
        gr.vm.step_budget = 400000
        bx, by, bw, bh = open_btn.bounds
        surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
        runner._drain_runnables()
        surf.paint()

        for vid, val in (("email", "test@example.com"), ("password", "secret123")):
            nd = tree.root.find(vid)
            assert nd is not None
            nd.text = val
            for sub in nd.walk():
                if sub.cls == "EditText":
                    sub.text = val

        sub_btn = tree.root.find("login_button")
        assert sub_btn is not None
        stubs._clock_ms = getattr(stubs, "_clock_ms", 1700000000000) + 2000
        gr.vm.steps = 0
        gr.vm.max_steps = 900000
        gr.vm.step_budget = 900000
        bx, by, bw, bh = sub_btn.bounds
        surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
        runner._drain_runnables()
        return gr, surf

    gr200, _ = _run_login(200)
    assert len(gr200.net_log.entries) >= 1
    pending = getattr(gr200.stubs, "_pending_nav_target", "") or ""
    assert "FitnessOnboardingActivity" in pending
    assert gr200.runner.navigate_to_pending() is True
    assert "FitnessOnboardingActivity" in getattr(getattr(gr200.stubs, "_current_activity", None), "cls", "")

    gr401, surf401 = _run_login(401)
    assert getattr(gr401.stubs, "_pending_nav_target", None) is None
    txts = [c.get("text", "") for c in surf401.paint() if c.get("text")]
    assert "Error logging in" in txts


# ---------------------------------------------------------------------------
# 4. Kippy Netz-Transparenz & Browser/Inspect + Native-Socket-Sync
# ---------------------------------------------------------------------------
def test_kippy_runtime_labels_webview_capture_and_inspector(tmp_path):
    from core.java.app_runtime import inspect_engine_details
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.view_tree import install_view_stubs
    from services.webview_inspector_service import WebViewInspectorService
    from core.java.net_recorder import NetLog
    from ui.controllers.debugger_controller import DebuggerController

    # 4a) Statische Flutter Asset-URLs klar als 0 Live-Calls markiert
    cfg_dir = tmp_path / "assets" / "flutter_assets" / "assets"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / ".env").write_text("BASE_URL=https://api.kippy.example/v1\n", encoding="utf-8")
    details = inspect_engine_details(str(tmp_path))
    assert any("Statische Asset-URLs (0 Live-Calls):" in ln for ln in details)

    # 4b) WebView.loadUrl Aufzeichnung in tree.web_events
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    wv = JObject("Landroid/webkit/WebView;", {})
    handled = False
    for mh in vm._method_hooks:
        ok_h, _res = mh("Landroid/webkit/WebView;", "Landroid/webkit/WebView;", "loadUrl", "(Ljava/lang/String;)V",
                        [wv, JString("https://app.kippy.eu/login")], False, vm)
        if ok_h:
            handled = True
            break
    assert handled is True
    assert len(tree.web_events) == 1
    assert tree.web_events[0]["url"] == "https://app.kippy.eu/login"

    # 4c) WebViewInspectorService DOM/Links/Forms/API-Extraktion auf lokalem HTML-Fixture
    html = """<!doctype html><html><head><title>Kippy Web Login</title></head><body>
    <form action="/oauth/token" method="POST"><input name="email" type="email"/><button type="submit">Anmelden</button></form>
    <a href="https://api.kippy.eu/v2/pets">Pets</a>
    <script>fetch("https://api.kippy.eu/v2/auth/session", {method:"POST"});</script>
    </body></html>"""
    html_path = tmp_path / "kippy_login.html"
    html_path.write_text(html, encoding="utf-8")
    p_info = WebViewInspectorService().fetch_and_inspect(str(html_path))
    assert p_info["ok"] is True
    assert p_info["title"] == "Kippy Web Login"
    assert len(p_info["forms"]) == 1 and p_info["forms"][0]["action"].endswith("/oauth/token")
    assert any("api.kippy.eu/v2/auth/session" in u for u in p_info["api_urls"])

    # 4d) Native Socket Sync in DebuggerController
    ctrl = DebuggerController.__new__(DebuggerController)
    ctrl.net_log = NetLog()
    ctrl._fusion = {"eng": type("_Eng", (), {"_os": type("_Os", (), {"net": {"log": [("connect", "10.0.2.2", 443)]}})()})()}
    ctrl._synced_os_net_count = 0
    ctrl._sync_fusion_os_net_to_log()
    assert len(ctrl.net_log.entries) == 1
    assert ctrl.net_log.entries[0]["req"].method == "CONNECT"
    assert ctrl.net_log.entries[0]["req"].url == "tcp://10.0.2.2:443"
    # Idempotenz bei wiederholtem Sync
    ctrl._sync_fusion_os_net_to_log()
    assert len(ctrl.net_log.entries) == 1


# ---------------------------------------------------------------------------
# 5. Vereinheitlichter Multi-Tool-Favoritenmanager & MCP-Parität
# ---------------------------------------------------------------------------
def test_unified_multitool_favorites_and_mcp(tmp_path):
    from core.application.session_state import SessionState
    from services.favorite_service import FavoriteService
    from services.active_session_service import ActiveSessionService
    from services.native_lib_service import get_shared_manager
    from mcp_server import tools

    SessionState.clear_session()
    try:
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)

        class _CfgObj:
            paths = {"APP_SOURCE_DIR": str(tmp_path), "EXTRACT_DIR": str(tmp_path)}
            config = {"BASE_DIR": str(tmp_path), "APP_PACKAGE": "com.example.demo", "MCP_SETTINGS": {"enabled": True}}

        class _DummyCtx:
            cfg = _CfgObj()

            def __init__(self):
                self._fav = FavoriteService(str(tmp_path))
                self._act = ActiveSessionService(str(tmp_path))
                self.native_lib_mgr = None

            def favorites(self):
                return self._fav

            def active_session(self):
                return self._act

            def libs(self):
                return get_shared_manager(self)

        ctx = _DummyCtx()

        # 5a) Einzel-Favorit Smali + Einzel-Favorit LibForge anlegen
        r1 = tools.favorites_add(ctx, json.dumps({
            "name": "Smali Hook",
            "version": "1.0",
            "description": "Demo Smali Patch",
            "patches": [{"type": "smali", "file": "a.smali", "orig": "const/4 v0, 0x0", "edit": "const/4 v0, 0x1"}],
        }))
        assert "smali" in r1

        r2 = tools.favorites_add(ctx, json.dumps({
            "name": "LibForge AuthBypass",
            "version": "1.0",
            "description": "Custom Hook Lib",
            "patches": [{
                "type": "libforge",
                "name": "authhook",
                "abi": "arm64-v8a",
                "source_code": "void hook(void) {}\n",
                "link_libs": "log dl",
                "output_kind": "shared",
            }],
        }))
        assert "libforge" in r2

        # 5b) favorites_append -> macht Einzel-Favorit zu Multi-Tool-Favorit
        app_res = tools.favorites_append(ctx, "Smali Hook", json.dumps({
            "type": "hex", "file": "libflutter.so", "ram": "1234", "base": "1000", "orig": "00", "patch": "1F",
        }))
        assert app_res["patch_count"] == 2
        assert app_res["is_multi_tool"] is True

        # 5c) favorites_list mit tool_type Filter
        all_favs = tools.favorites_list(ctx)
        assert len(all_favs) == 2
        kombi_only = tools.favorites_list(ctx, tool_type="kombi")
        assert len(kombi_only) == 1 and kombi_only[0]["name"] == "Smali Hook"
        lf_only = tools.favorites_list(ctx, tool_type="libforge")
        assert len(lf_only) == 1 and lf_only[0]["name"] == "LibForge AuthBypass"

        # 5d) favorites_combine -> kombiniert mehrere Favoriten zu einem neuen Kombi-Favoriten
        comb = tools.favorites_combine(ctx, json.dumps(["Smali Hook", "LibForge AuthBypass"]), "Komplett-Bypass", version="2.0")
        assert comb["patch_count"] == 3
        assert comb["is_multi_tool"] is True

        # 5e) patch_apply_favorite wendet Hex + LibForge an (Smali optional je Source-Baum)
        apply_res = tools.patch_apply_favorite(ctx, "LibForge AuthBypass")
        assert apply_res["applied_ok"] == 1
        libs = ctx.libs().get_all()
        assert any(l.name == "authhook" and "void hook" in l.source_code for l in libs)
    finally:
        SessionState.clear_session()


def test_unified_favorites_dialog_tk_smoke(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Kein Display fuer Tk: {e}")

    from core.application.session_state import SessionState
    from services.favorite_service import FavoriteService
    from ui.tabs.workspace_tab import WorkspaceTab
    from ui.dialogs.favorite_patches_dialog import FavoritePatchesDialog, format_fav_tools

    SessionState.clear_session()
    try:
        class _Cfg:
            paths = {"APP_SOURCE_DIR": str(tmp_path), "DEST_DIR": str(tmp_path), "TOOLS_DIR": str(tmp_path)}
            config = {"BASE_DIR": str(tmp_path), "APP_PACKAGE": "com.example.demo", "APP_NAME": "Demo", "APP_VERSION": "1.0"}

        class _DummyCG:
            nodes = {}
            edges = {}
            reverse_edges = {}

            def get_methods_in_file(self, _f):
                return []

        class _DummyApp:
            current_id = "TEST-ID"
            cfg = _Cfg()
            smali_cache = []
            cg = _DummyCG()
            favorite_svc = FavoriteService(str(tmp_path))

            def after(self, _ms, _fn=None):
                pass

            def log(self, _msg):
                pass

            def persist_active_session(self):
                pass

        app = _DummyApp()
        app.favorite_svc.favs = [
            {"name": "MultiBundle", "version": "1.0", "patches": [
                {"type": "smali", "file": "a.smali", "orig": "x", "edit": "y"},
                {"type": "libforge", "name": "myhook", "abi": "arm64-v8a", "source_code": "int x = 1;"},
            ]},
            {"name": "OnlyHex", "version": "1.0", "patches": [
                {"type": "hex", "file": "libdemo.so", "ram": "10", "base": "0", "orig": "00", "patch": "1F"},
            ]},
        ]
        ws = WorkspaceTab(root, app)
        app.workspace_tab = ws

        badge, tset, cnt = format_fav_tools(app.favorite_svc.favs[0])
        assert badge == "Smali + LibForge" and tset == {"Smali", "LibForge"} and cnt == 2

        dlg = FavoritePatchesDialog(root, ws, initial_filter="LibForge")
        items = dlg.tree_favs.get_children()
        assert len(items) == 1
        assert dlg.tree_favs.item(items[0], "values")[0] == "MultiBundle"
        dlg.cmb_filter.set("Kombi (Multi-Tool)")
        dlg.populate_list()
        assert len(dlg.tree_favs.get_children()) == 1
        dlg.cmb_filter.set("Alle")
        dlg.populate_list()
        assert len(dlg.tree_favs.get_children()) == 2
        dlg.destroy()
    finally:
        SessionState.clear_session()
        try:
            root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. Kippy (Flutter) + In-Phone Browser direkt in der 380x800 Handy-Simulation
# ---------------------------------------------------------------------------
def test_kippy_renders_interactive_ui_in_phone_and_logs_network(tmp_path):
    from core.java.app_runtime import is_unrendered_surface_shell
    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.surface import SceneGraphSurface

    # 6a) Generischer Flutter-Mini-Root (garantiert CI-Lauffähigkeit unabhängig von lokalen APKs)
    mini = tmp_path / "mini_flutter"
    (mini / "lib" / "arm64-v8a").mkdir(parents=True)
    (mini / "lib" / "arm64-v8a" / "libflutter.so").write_bytes(b"\x7fELF")
    (mini / "lib" / "arm64-v8a" / "libapp.so").write_bytes(b"\x7fELFpetlink.demo@datamars.com")
    tr_dir = mini / "assets" / "flutter_assets" / "assets" / "translations"
    tr_dir.mkdir(parents=True)
    (mini / "assets" / "flutter_assets" / ".env.prod").write_text(
        "BASE_URL=https://api.demo.invalid/graphql\nAPI_KEY=da2-abcdefghijklmnopqrstuvwx\n",
        encoding="utf-8",
    )
    (mini / "assets" / "flutter_assets" / "config.json").write_text(
        json.dumps({"UserPoolID": "eu-west-1_AbCdEfG12", "ClientID": "1234567890abcdefghijklmnop", "appBrand": "DemoPet"}),
        encoding="utf-8",
    )
    (tr_dir / "de-DemoPet.json").write_text(
        json.dumps({
            "login": {"welcome": "Willkommen bei DemoPet", "login": "Anmelden", "email": "E-Mail", "password": "Passwort"},
            "demo": {"try_demo": "Demo testen", "start": "Demo starten"},
            "bottom_bar": {"home": "HEIM", "position": "POSITION", "activity": "AKTIVITÄT", "menu": "MENÜ"},
            "urls": {"shop": "https://shop.demo.invalid/de"},
        }),
        encoding="utf-8",
    )
    (mini / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.pet">'
        '<application><activity android:name="com.demo.pet.MainActivity">'
        '<intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )

    def _mock_resp(req):
        if "cognito" in req.url:
            return NetResponse(200, {"Content-Type": "application/json", "X-Glass-Source": "mock-rule"},
                               json.dumps({"AuthenticationResult": {"AccessToken": "tok-123"}}).encode("utf-8"), "OK")
        if "shop." in req.url or "kippy.eu" in req.url:
            html = "<html><head><title>In-Phone Shop</title></head><body><h1>Hallo Handy</h1><a href='https://shop.demo.invalid/cat'>Katalog</a></body></html>"
            return NetResponse(200, {"Content-Type": "text/html", "X-Glass-Source": "mock-rule"}, html.encode("utf-8"), "OK")
        return NetResponse(200, {"Content-Type": "application/json", "X-Glass-Source": "mock-rule"},
                           json.dumps({"data": {"ok": True}}).encode("utf-8"), "OK")

    def _exercise(app_root: str, expect_welcome_substr: str, expect_images: bool):
        gr = GlassAppRunner(app_root, zone="original")
        gr.build(net_responder=_mock_resp, native_fusion=False)
        gr.run(max_steps=400000, native_fusion=False)
        assert is_unrendered_surface_shell(gr.tree) is False
        surf = SceneGraphSurface(gr.tree, vm=gr.vm)
        cmds = surf.paint()
        txts = [c.get("text", "") for c in cmds if c.get("op") == "text"]
        assert any(expect_welcome_substr in t for t in txts)
        assert "Anmelden" in txts
        if expect_images:
            assert any(c.get("op") == "image" for c in cmds)

        open_btn = gr.tree.root.find("btn_open_login")
        assert open_btn is not None
        bx, by, _, _ = open_btn.bounds
        surf.dispatch({"type": "tap", "x": bx + 5, "y": by + 5})
        assert gr.tree._flutter_ctrl.screen == "login"

        gr.tree.root.find("login_email").text = "user@example.com"
        gr.tree.root.find("login_password").text = "secret123"
        sub = gr.tree.root.find("btn_login_submit")
        sx, sy, _, _ = sub.bounds
        surf.dispatch({"type": "tap", "x": sx + 5, "y": sy + 5})

        assert gr.tree._flutter_ctrl.screen == "home"
        urls = [e["req"].url for e in gr.net_log.entries]
        assert any("cognito-idp." in u for u in urls)
        assert any("graphql" in u for u in urls)

        # In-Phone-Browser im selben ViewTree (ohne Auslagerung) + Back zurück zur App
        gr.tree._flutter_ctrl.open_browser("https://shop.demo.invalid/de")
        assert gr.tree._flutter_ctrl.screen == "browser"
        btxts = [c.get("text", "") for c in surf.paint() if c.get("op") == "text"]
        assert any("In-Phone Shop" in t or "Hallo Handy" in t for t in btxts)
        assert gr.runner.handle_back() is True
        assert gr.tree._flutter_ctrl.screen == "home"

    _exercise(str(mini), "Willkommen bei DemoPet", expect_images=False)

    # 6b) Reales entpacktes Kippy (falls im Workspace vorhanden)
    kippy_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "source", "com.datamars.kippynew", "base_unpacked_apktool")
    if os.path.isdir(kippy_root):
        _exercise(kippy_root, "Willkommen bei Kippy", expect_images=True)


def test_display_tab_browser_stays_inside_phone_canvas(tmp_path):
    import tkinter as tk
    from core.java.surface import SceneGraphSurface
    from core.java.view_tree import ViewNode, ViewTree, paint_commands
    from ui.tabs.display_tab import DisplayTab

    p2 = tmp_path / "details.html"
    p2.write_text("<html><head><title>Tracker Details</title></head><body><p>GPS aktiv</p></body></html>", encoding="utf-8")
    p1 = tmp_path / "shop.html"
    p1.write_text(
        f'<html><head><title>Shop Start</title></head><body><h1>Kippy Portal</h1><a href="{p2.as_uri()}">Details öffnen</a></body></html>',
        encoding="utf-8",
    )

    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError:
        pytest.skip("Kein Tk-Display verfuegbar")

    class _MinApp:
        cfg = type("_Cfg", (), {"paths": {}, "config": {"BASE_DIR": str(tmp_path)}})()

        def after(self, _ms, fn=None):
            if callable(fn):
                fn()

        def log(self, _m):
            pass

    app = _MinApp()
    try:
        for emb in (True, False):
            dt = DisplayTab(tk_root, app, embedded=emb)
            orig_tree = ViewTree()
            orig_root = ViewNode("LinearLayout", vid="app_root")
            orig_root.add(ViewNode("TextView", vid="orig_lbl", text="Original-App"))
            orig_tree.set_root(orig_root)
            dt.set_tree(orig_tree)

            dt.toggle_browser_view(str(p1))
            assert dt._browser_win is None
            assert dt.canvas.winfo_manager() == "pack"
            assert dt._browser_mode is True
            assert dt.tree is not orig_tree

            cmds = paint_commands(dt.tree)
            txts = [c.get("text", "") for c in cmds if c.get("op") == "text"]
            assert any("Shop Start" in t or "Kippy Portal" in t for t in txts)

            # Link-Tap navigiert innerhalb desselben Handy-Canvas zur Detailseite
            link_btn = dt.tree.root.find("browser_link_0")
            assert link_btn is not None
            surf = SceneGraphSurface(dt.tree)
            surf.paint()
            lx, ly, _, _ = link_btn.bounds
            assert surf.dispatch({"type": "tap", "x": lx + 4, "y": ly + 4}) is True
            txts2 = [c.get("text", "") for c in paint_commands(dt.tree) if c.get("op") == "text"]
            assert any("Tracker Details" in t or "GPS aktiv" in t for t in txts2)

            # Erneuter Toggle kehrt zum App-Baum im Handy zurück
            dt.toggle_browser_view()
            assert dt._browser_mode is False
            assert dt.tree is orig_tree
            assert dt.canvas.winfo_manager() == "pack"
            dt.destroy()
    finally:
        try:
            tk_root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. Virtuagym Onboarding (ViewPager2 + ConstraintLayout + Slider + subSequence)
# ---------------------------------------------------------------------------
def test_viewpager2_fragment_adapter_and_subsequence_unit():
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.layout_inflater import _reorder_constraintlayout_children
    from core.java.view_tree import ViewNode, ViewTree, install_view_stubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.resolve_invoke = stubs.resolve
    vm.runtime = stubs
    tree = install_view_stubs(stubs)

    # 6a) CharSequence.subSequence Stub
    ok_sub, res_sub = stubs.resolve("Ljava/lang/CharSequence;", "subSequence", "(II)Ljava/lang/CharSequence;", [JString("  Alex  "), 2, 6], False)
    assert ok_sub and isinstance(res_sub, JString) and res_sub.text == "Alex"

    # 6b) ConstraintLayout-Reordering: Top-Anker -> Mitte -> Bottom-Anker
    cl = ViewNode("ConstraintLayout", vid="cl")
    btn = ViewNode("Button", vid="primary_button", text="Next")
    btn.props["$cl_bot_parent"] = True
    vp = ViewNode("ViewPager2", vid="view_pager")
    vp.props["$cl_top_parent"] = True
    vp.props["$cl_bot_to_top"] = "primary_button"
    prog = ViewNode("LinearLayout", vid="progress")
    prog.props["$cl_top_parent"] = True
    cl.add(btn)
    cl.add(vp)
    cl.add(prog)
    _reorder_constraintlayout_children(cl)
    order = [c.vid for c in cl.children]
    assert order.index("progress") < order.index("view_pager") < order.index("primary_button")

    # 6c) ViewPager2 FragmentStateAdapter-Materialisierung + findFragmentByTag
    root = ViewNode("LinearLayout", vid="root")
    vp_node = ViewNode("ViewPager2", vid="pager")
    root.add(vp_node)
    tree.set_root(root)
    def _vhook_call(rcls, cls, name, sig, logical):
        for h in list(vm._method_hooks):
            ok, val = h(rcls, cls, name, sig, logical, False, vm)
            if ok:
                return ok, val
        return False, None

    f0_obj = JObject("Landroidx/fragment/app/Fragment;", {})
    f1_obj = JObject("Landroidx/fragment/app/Fragment;", {})
    # Synthetic Adapter with createFragment + getItemCount via AndroidStubs
    stubs.register("Ltest/TestVpAdapter;", "getItemCount", "()I", lambda _a: 2)
    stubs.register(
        "Ltest/TestVpAdapter;", "createFragment", "(I)Landroidx/fragment/app/Fragment;",
        lambda a: f0_obj if int(a[1] or 0) == 0 else f1_obj,
    )
    stubs.register(
        "Landroidx/fragment/app/Fragment;", "onCreateView",
        "(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;",
        lambda a: JObject("Landroid/widget/TextView;", {"$view": ViewNode("TextView", text="Step-0" if a[0] is f0_obj else "Step-1")}),
    )
    adp = JObject("Ltest/TestVpAdapter;", {})
    vp_jobj = JObject("Landroidx/viewpager2/widget/ViewPager2;", {"$view": vp_node})
    handled, _ = _vhook_call("Landroidx/viewpager2/widget/ViewPager2;", "Landroidx/viewpager2/widget/ViewPager2;", "setAdapter", "(Landroidx/recyclerview/widget/RecyclerView$Adapter;)V", [vp_jobj, adp])
    assert handled is True
    assert any(n.text == "Step-0" for n in vp_node.walk())

    fm = JObject("Landroidx/fragment/app/FragmentManager;", {"$synth_fm": True})
    ok_t0, got0 = _vhook_call("Landroidx/fragment/app/FragmentManager;", "Landroidx/fragment/app/FragmentManager;", "findFragmentByTag", "(Ljava/lang/String;)Landroidx/fragment/app/Fragment;", [fm, JString("f0")])
    assert ok_t0 and got0 is f0_obj

    _vhook_call("Landroidx/viewpager2/widget/ViewPager2;", "Landroidx/viewpager2/widget/ViewPager2;", "setCurrentItem", "(IZ)V", [vp_jobj, 1, 0])
    assert any(n.text == "Step-1" for n in vp_node.walk())
    ok_t1, got1 = _vhook_call("Landroidx/fragment/app/FragmentManager;", "Landroidx/fragment/app/FragmentManager;", "findFragmentByTag", "(Ljava/lang/String;)Landroidx/fragment/app/Fragment;", [fm, JString("f1")])
    assert ok_t1 and got1 is f1_obj


def test_virtuagym_onboarding_viewpager2_questions_and_next_navigation():
    vg_root = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")
    if not os.path.isdir(vg_root):
        pytest.skip("Virtuagym APK nicht lokal entpackt vorhanden")

    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.surface import SceneGraphSurface

    entry = "Ldigifit/android/virtuagym/presentation/screen/onboarding/view/FitnessOnboardingActivity;"
    gr = GlassAppRunner(vg_root, zone="original")
    gr.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    gr.run(entry=entry, max_steps=600000, native_fusion=False)
    tree, stubs, runner = gr.tree, gr.stubs, gr.runner
    surf = SceneGraphSurface(tree, vm=gr.vm)

    def _tap_node(node):
        assert node is not None
        bx, by, bw, bh = node.bounds
        assert bw > 0 and bh > 0
        gr.vm.steps = 0
        gr.vm.max_steps = 800000
        gr.vm.step_budget = 800000
        assert surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2}) is True
        runner._drain_runnables()

    # Seite 0: Fragebogen-Inhalt + Next unten unter progress
    txts0 = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert "Hi! What is your first name?" in txts0
    assert "First name" in txts0
    assert "Next" in txts0
    prog0 = tree.root.find("progress")
    btn0 = tree.root.find("primary_button")
    assert prog0 is not None and btn0 is not None
    assert prog0.bounds[1] < btn0.bounds[1]

    # Leerer Name -> bleibt auf Seite 0
    _tap_node(btn0)
    txts_empty = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert "Hi! What is your first name?" in txts_empty

    # Name eingeben & Next -> Seite 1 (GoalIntakeFragment)
    name_node = tree.root.find("name")
    assert name_node is not None and callable(name_node.props.get("$on_change_fn"))
    name_node.props["$on_change_fn"]("Alex")
    _tap_node(tree.root.find("primary_button"))

    txts1 = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert "What is your main goal?" in txts1
    assert "Lose Weight" in txts1
    assert "Build Muscle" in txts1

    # Karte antippen & Next -> Seite 2 (LevelIntakeFragment mit StepSlider)
    goal_node = next((n for n in tree.root.walk() if n.text == "Build Muscle"), None)
    _tap_node(goal_node)
    _tap_node(tree.root.find("primary_button"))

    txts2 = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert "What is your fitness level?" in txts2
    assert "Beginner" in txts2

    # StepSlider antippen -> Wechsel weg von Beginner, dann Next -> LoadingIntakeActivity
    slider = next((n for n in tree.root.walk() if n.cls == "SeekBar"), None)
    assert slider is not None
    _tap_node(slider)
    txts2_after = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert "Beginner" not in txts2_after

    _tap_node(tree.root.find("primary_button"))
    pending = getattr(stubs, "_pending_nav_target", "") or ""
    assert "LoadingIntakeActivity" in pending
    assert runner.navigate_to_pending() is True
    txts_loading = [c.get("text", "") for c in surf.paint() if c.get("text")]
    assert (
        "Personalizing your experience" in txts_loading
        or "Allow us to help you reach your goal" in txts_loading
    )


# ---------------------------------------------------------------------------
# 7. Debugger Netz-Tab: Vertikaler Splitter zwischen Liste/Details & Mock-Panel
# ---------------------------------------------------------------------------
def test_debugger_net_tab_has_vertical_resizable_panedwindow(tmp_path):
    import tkinter as tk
    from ui.tabs.debugger_tab import DebuggerTab

    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError:
        pytest.skip("Kein Tk-Display verfuegbar")

    class _MinApp:
        cfg = type("_Cfg", (), {"paths": {}, "config": {"BASE_DIR": str(tmp_path)}})()

        def after(self, _ms, fn=None):
            if callable(fn):
                fn()

        def log(self, _m):
            pass

    app = _MinApp()
    dt = None
    try:
        dt = DebuggerTab(tk_root, app)
        net_frame = dt.dm.get_view_widget("netz") if hasattr(dt.dm, "get_view_widget") else None
        if net_frame is None and hasattr(dt.dm, "_views"):
            v = dt.dm._views.get("netz")
            net_frame = getattr(v, "widget", None) or (v.get("widget") if isinstance(v, dict) else None)
        if net_frame is None:
            net_frame = dt._v_net(tk_root)
        vsplit = getattr(net_frame, "_net_vsplit", None)
        assert isinstance(vsplit, tk.PanedWindow)
        assert str(vsplit.cget("orient")) == "vertical"
        assert len(vsplit.panes()) == 2
        net_frame.pack(fill="both", expand=True)
        tk_root.geometry("900x520")
        tk_root.update_idletasks()
        vsplit.sash_place(0, 0, 220)
        tk_root.update_idletasks()
        _, y_after = vsplit.sash_coord(0)
        assert y_after >= 100
    finally:
        if dt is not None:
            try:
                dt.destroy()
            except Exception:
                pass
        try:
            tk_root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 8. Virtuagym Startscreen-Fidelity, Spinner-Vektorbogen & Coroutine-Hang-Fix
# ---------------------------------------------------------------------------
def test_atomic_field_updater_null_cas_and_vector_open_arc_unit(tmp_path):
    import io
    from PIL import Image
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.drawable_loader import DrawableResolver, rasterize_vector_to_png

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    # 8a) AtomicReferenceFieldUpdater Null-CAS (0 vs None) & monotonic nanoTime/LockSupport
    arfu_cls = "Ljava/util/concurrent/atomic/AtomicReferenceFieldUpdater;"
    obj_cls = vm.intern_class("Lkotlinx/coroutines/EventLoopImplBase;")
    val_cls = vm.intern_class("Ljava/lang/Object;")
    ok, upd = stubs.resolve(
        arfu_cls, "newUpdater",
        "(Ljava/lang/Class;Ljava/lang/Class;Ljava/lang/String;)Ljava/util/concurrent/atomic/AtomicReferenceFieldUpdater;",
        [obj_cls, val_cls, JString("_delayed")], True,
    )
    assert ok and isinstance(upd, JObject)
    target = JObject("Lkotlinx/coroutines/EventLoopImplBase;", {})
    q_obj = JObject("Lkotlinx/coroutines/EventLoopImplBase$DelayedTaskQueue;", {})
    # Unset field holds None, Dalvik loads null as int 0
    ok_cas, cas_res = stubs.resolve(
        arfu_cls, "compareAndSet", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Z",
        [upd, target, 0, q_obj], False,
    )
    assert ok_cas and cas_res == 1
    ok_get, got = stubs.resolve(arfu_cls, "get", "(Ljava/lang/Object;)Ljava/lang/Object;", [upd, target], False)
    assert ok_get and got is q_obj

    _, t1 = stubs.resolve("Ljava/lang/System;", "nanoTime", "()J", [], True)
    stubs.resolve("Ljava/util/concurrent/locks/LockSupport;", "parkNanos", "(Ljava/lang/Object;J)V", [target, 5_000_000], True)
    _, t2 = stubs.resolve("Ljava/lang/System;", "nanoTime", "()J", [], True)
    assert t2 > t1

    # 8b) <gradient> + fillColor="#00000000" open stroked vector arc -> transparent center
    res_dir = tmp_path / "res" / "drawable"
    res_dir.mkdir(parents=True)
    (res_dir / "$arc_grad.xml").write_text(
        '<gradient xmlns:android="http://schemas.android.com/apk/res/android">'
        '<item android:color="#ffff7000" android:offset="0.0" />'
        '</gradient>',
        encoding="utf-8",
    )
    (res_dir / "arc_spinner.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:width="40dp" android:height="40dp" android:viewportWidth="40" android:viewportHeight="40">'
        '<path android:fillColor="#00000000" android:pathData="M20,4C29,4 36,11 36,20C36,29 29,36 20,36" '
        'android:strokeColor="@drawable/$arc_grad" android:strokeWidth="4.0" />'
        '</vector>',
        encoding="utf-8",
    )
    dr = DrawableResolver(str(tmp_path))
    spec = dr.resolve("@drawable/arc_spinner")
    assert spec is not None and spec["kind"] == "vector"
    assert spec["paths"][0]["fill"] is None
    assert spec["paths"][0]["stroke"] == "#ff7000"
    png = rasterize_vector_to_png(spec, 64, 64, dark_bg=False)
    assert png is not None
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    assert im.getpixel((im.width // 2, im.height // 2))[3] == 0
    assert max(im.getpixel((px, py))[3] for px in range(im.width) for py in range(im.height)) > 0


def test_virtuagym_startup_layout_and_loading_intake_no_hang():
    vg_root = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")
    if not os.path.isdir(vg_root):
        pytest.skip("Virtuagym APK nicht lokal entpackt vorhanden")

    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.surface import SceneGraphSurface

    # 1) AccessActivity Startscreen & Login Overlay
    gr = GlassAppRunner(vg_root, zone="original")
    gr.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;",
        max_steps=600000,
        native_fusion=False,
    )
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    cmds0 = surf.paint()
    hero = next((c for c in cmds0 if c.get("op") == "image" and c.get("name") == "fitness_frontpage_bg1"), None)
    assert hero is not None and hero["h"] >= 700
    t_smart = next((c for c in cmds0 if c.get("text") == "Workout smarter"), None)
    t_guide = next((c for c in cmds0 if c.get("text") == "Guiding you towards a healthier and more active life"), None)
    assert t_smart is not None and t_guide is not None and t_smart["y"] < t_guide["y"]
    t_signup = next((c for c in cmds0 if c.get("text") == "Sign up"), None)
    t_login = next((c for c in cmds0 if c.get("text") == "Log in"), None)
    assert t_signup is not None and t_login is not None and t_signup["y"] >= 680 and t_login["y"] >= 680
    assert not any(c.get("op") == "rect" and c.get("fill") == "#eef3ff" for c in cmds0)

    btn = gr.tree.root.find("log_in_text_button")
    assert btn is not None
    bx, by, bw, bh = btn.bounds
    gr.vm.steps = 0
    gr.vm.max_steps = 400000
    gr.vm.step_budget = 400000
    assert surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2}) is True
    gr.runner._drain_runnables()
    cmds_login = surf.paint()
    txts_login = [c.get("text", "") for c in cmds_login if c.get("text")]
    assert "Email" in txts_login and "Password" in txts_login
    assert "Forgot your password?" in txts_login
    cancel = gr.tree.root.find("cancel_button")
    assert cancel is not None and cancel.bounds[0] < 100 and cancel.bounds[1] < 100
    login_btn = gr.tree.root.find("login_button")
    assert login_btn is not None and login_btn.props.get("bg_tint", "").lower() == "#ffffff"

    # 2) LoadingIntakeActivity: kein 76M-Step-Hang & Weiterleitung auf NotificationPermission -> BecomePro/Home
    gr2 = GlassAppRunner(vg_root, zone="original")
    gr2.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    res2 = gr2.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/onboarding/loading/view/LoadingIntakeActivity;",
        max_steps=600000,
        native_fusion=False,
    )
    assert int(res2.get("steps", 0) or 0) < 500000
    assert "FitnessNotificationPermissionActivity" in getattr(getattr(gr2.stubs, "_current_activity", None), "cls", "")
    surf2 = SceneGraphSurface(gr2.tree, vm=gr2.vm)
    cmds_perm = surf2.paint()
    perm_txts = [c.get("text", "") for c in cmds_perm if c.get("text")]
    assert "Allow" in perm_txts and "Maybe later" in perm_txts
    maybe = next((n for n in gr2.tree.root.walk() if n.text == "Maybe later"), None)
    assert maybe is not None and maybe.clickable is True
    mx, my, mw, mh = maybe.bounds
    gr2.vm.steps = 0
    gr2.vm.max_steps = 800000
    gr2.vm.step_budget = 800000
    assert surf2.dispatch({"type": "tap", "x": mx + mw // 2, "y": my + mh // 2}) is True
    gr2.runner._drain_runnables()
    gr2.runner.navigate_to_pending()
    cur_after = getattr(getattr(gr2.stubs, "_current_activity", None), "cls", "")
    assert cur_after and "FitnessNotificationPermissionActivity" not in cur_after


# ---------------------------------------------------------------------------
# 9. Intent Extras/Flags, GridLayoutManager, Tint, Compose Isolation & Virtuagym Flow
# ---------------------------------------------------------------------------
def test_intent_extras_flags_grid_and_compose_isolation_unit():
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import LifecycleRunner
    from core.java.view_tree import ViewNode, install_view_stubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.resolve_invoke = stubs.resolve
    vm.runtime = stubs
    tree = install_view_stubs(stubs)

    # 9a) Intent typed extras & CLEAR_TASK flag handling
    ic = "Landroid/content/Intent;"
    intent = JObject(ic, {})
    ok_b, ret_b = stubs.resolve(ic, "putExtra", "(Ljava/lang/String;Z)Landroid/content/Intent;", [intent, JString("extra_from_onboarding"), 1], False)
    assert ok_b and ret_b is intent
    ok_s, _ = stubs.resolve(ic, "putExtra", "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;", [intent, JString("route"), JString("home")], False)
    assert ok_s
    ok_g, val_b = stubs.resolve(ic, "getBooleanExtra", "(Ljava/lang/String;Z)Z", [intent, JString("extra_from_onboarding"), 0], False)
    assert ok_g and val_b == 1
    ok_m, val_miss = stubs.resolve(ic, "getBooleanExtra", "(Ljava/lang/String;Z)Z", [intent, JString("missing"), 0], False)
    assert ok_m and val_miss == 0
    ok_gs, val_s = stubs.resolve(ic, "getStringExtra", "(Ljava/lang/String;)Ljava/lang/String;", [intent, JString("route")], False)
    assert ok_gs and isinstance(val_s, JString) and val_s.text == "home"
    ok_he, has_e = stubs.resolve(ic, "hasExtra", "(Ljava/lang/String;)Z", [intent, JString("route")], False)
    assert ok_he and has_e == 1
    ok_af, ret_af = stubs.resolve(ic, "addFlags", "(I)Landroid/content/Intent;", [intent, 0x10008000], False)
    assert ok_af and ret_af is intent
    ok_gf, flg = stubs.resolve(ic, "getFlags", "()I", [intent], False)
    assert ok_gf and flg == 0x10008000

    lifecycle = LifecycleRunner(vm, stubs)
    lifecycle.activity_stack.append({"cls": "Ltest/PrevActivity;", "activity": JObject("Ltest/PrevActivity;", {})})
    stubs._pending_nav_target = "Ltest/NextActivity;"
    stubs._pending_nav_intent = intent
    assert lifecycle.navigate_to_pending() is True
    assert lifecycle.activity_stack == []

    def _call_vhook(rcls, cls, name, sig, logical, static=False):
        for h in list(vm._method_hooks):
            ok, val = h(rcls, cls, name, sig, logical, static, vm)
            if ok:
                return ok, val
        return False, None

    # 9b) GridLayoutManager(ctx, 2) + click_overlay Z-order coverage
    rv_node = ViewNode(cls="RecyclerView", vid="grid")
    rv_obj = JObject("Landroidx/recyclerview/widget/RecyclerView;", {"$view": rv_node})
    glm = JObject("Landroidx/recyclerview/widget/GridLayoutManager;", {})
    _call_vhook(glm.cls, glm.cls, "<init>", "(Landroid/content/Context;I)V", [glm, JObject("Landroid/content/Context;", {}), 2])
    _call_vhook(rv_obj.cls, rv_obj.cls, "setLayoutManager", "(Landroidx/recyclerview/widget/RecyclerView$LayoutManager;)V", [rv_obj, glm])
    assert rv_node.props.get("span_count") == 2

    cards = []
    overlays = []
    for title in ("Opt A", "Opt B", "Opt C"):
        item = ViewNode(cls="ConstraintLayout")
        card = ViewNode(cls="CardView")
        card.add(ViewNode(cls="TextView", text=title))
        ov = ViewNode(cls="View", vid="click_overlay", clickable=True)
        item.add(card)
        item.add(ov)
        rv_node.add(item)
        cards.append(item)
        overlays.append(ov)
    tree.set_root(rv_node)
    assert cards[0].bounds[1] == cards[1].bounds[1]
    assert cards[0].bounds[0] < cards[1].bounds[0]
    assert cards[2].bounds[1] > cards[0].bounds[1]
    assert overlays[0].bounds == cards[0].bounds

    # 9c) View.getBackground().mutate().setColorFilter & clearColorFilter
    c_wrap = JObject("Landroidx/cardview/widget/CardView;", {"$view": cards[0]})
    _, drv = _call_vhook(c_wrap.cls, c_wrap.cls, "getBackground", "()Landroid/graphics/drawable/Drawable;", [c_wrap])
    assert isinstance(drv, JObject)
    _, mut = _call_vhook(drv.cls, drv.cls, "mutate", "()Landroid/graphics/drawable/Drawable;", [drv])
    bmcf = JObject("Landroid/graphics/BlendModeColorFilter;", {})
    stubs.resolve("Landroid/graphics/BlendModeColorFilter;", "<init>", "(ILandroid/graphics/BlendMode;)V", [bmcf, 0xFF42A5F5, None], False)
    _call_vhook(mut.cls, mut.cls, "setColorFilter", "(Landroid/graphics/ColorFilter;)V", [mut, bmcf])
    assert cards[0].props.get("bg_drawable", {}).get("fill") == "#42a5f5"
    _call_vhook(mut.cls, mut.cls, "clearColorFilter", "()V", [mut])
    assert "bg_drawable" not in cards[0].props or cards[0].props["bg_drawable"].get("fill") != "#42a5f5"

    # 9d) ComposeView.setContent inside XML root must not overwrite tree.root or tree._compose_recompose
    from core.java import smali as Sm

    coord = ViewNode(cls="CoordinatorLayout", props={"full_cls": "androidx.coordinatorlayout.widget.CoordinatorLayout"})
    main_pager = ViewNode(cls="ViewPager", vid="pager", props={"xml_h_mode": "match"})
    main_pager.add(ViewNode(cls="TextView", text="Main Tab"))
    cv_node = ViewNode(cls="ComposeView", vid="user_level_card", props={"full_cls": "androidx.compose.ui.platform.ComposeView"})
    coord.add(main_pager)
    coord.add(cv_node)
    sentinel_recomp = lambda: None
    tree.set_root(coord)
    tree._compose_recompose = sentinel_recomp

    cv_obj = JObject("Landroidx/compose/ui/platform/ComposeView;", {"$view": cv_node})
    fn2_cls = "Ltest/ComposeSlotFn2;"
    Sm.load_into(
        vm,
        """
        .class public Ltest/ComposeSlotFn2;
        .super Ljava/lang/Object;

        .method public constructor <init>()V
            .locals 1
            invoke-direct {p0}, Ljava/lang/Object;-><init>()V
            return-void
        .end method

        .method public invoke(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;
            .locals 2
            const-string v0, "Embedded Card"
            const/4 v1, 0x0
            invoke-static {v0, v1}, Landroidx/compose/material3/TextKt;->Text--4IGK_g(Ljava/lang/String;Landroidx/compose/ui/Modifier;)V
            return-object v1
        .end method
        """,
    )
    fn2_obj = JObject(fn2_cls, {})
    _call_vhook(
        cv_obj.cls, cv_obj.cls, "setContent", "(Lkotlin/jvm/functions/Function2;)V",
        [cv_obj, fn2_obj], static=False,
    )
    assert tree.root is coord
    assert tree._compose_recompose is sentinel_recomp
    assert any(ch.text == "Embedded Card" for ch in cv_node.walk())


def test_virtuagym_goal_grid_notification_pro_close_and_home():
    vg_root = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")
    if not os.path.isdir(vg_root):
        pytest.skip("Virtuagym APK nicht lokal entpackt vorhanden")

    from core.java.glass_runner import GlassAppRunner
    from core.java.net_recorder import NetResponse
    from core.java.objects import JString
    from core.java.surface import SceneGraphSurface

    def _tap(runner, surf, node):
        assert node is not None
        bx, by, bw, bh = node.bounds
        assert bw > 0 and bh > 0
        runner.vm.steps = 0
        runner.vm.max_steps = 800000
        runner.vm.step_budget = 800000
        assert surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2}) is True
        runner.runner._drain_runnables()

    # 1) GoalIntakeFragment 2-Spalten-Grid, Header, sichtbarer Next-Button & Selektions-Tint
    gr1 = GlassAppRunner(vg_root, zone="original")
    gr1.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    gr1.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/onboarding/view/FitnessOnboardingActivity;",
        max_steps=600000,
        native_fusion=False,
    )
    surf1 = SceneGraphSurface(gr1.tree, vm=gr1.vm)
    name_nd = gr1.tree.root.find("name")
    assert name_nd is not None and callable(name_nd.props.get("$on_change_fn"))
    name_nd.props["$on_change_fn"]("Alex")
    _tap(gr1, surf1, gr1.tree.root.find("primary_button"))

    bk = gr1.tree.root.find("back_button")
    nxt = gr1.tree.root.find("primary_button")
    rv = next((n for n in gr1.tree.root.walk() if n.cls == "RecyclerView"), None)
    assert bk is not None and bk.bounds[2] <= 60
    assert nxt is not None and nxt.bounds[1] + nxt.bounds[3] <= 760
    assert rv is not None and rv.props.get("span_count") == 2
    assert len(rv.children) >= 4
    assert rv.children[0].bounds[1] == rv.children[1].bounds[1]
    assert rv.children[0].bounds[0] < rv.children[1].bounds[0]

    muscle_before = next((n for n in rv.children if any(s.text == "Build Muscle" for s in n.walk())), None)
    assert muscle_before is not None
    _tap(gr1, surf1, muscle_before)
    muscle = next((n for n in rv.children if any(s.text == "Build Muscle" for s in n.walk())), None)
    assert muscle is not None
    m_fills = [s.props.get("bg_drawable", {}).get("fill") for s in muscle.walk() if s.props.get("bg_drawable")]
    m_tv = next((s for s in muscle.walk() if s.text == "Build Muscle"), None)
    lose = next((n for n in rv.children if any(s.text == "Lose Weight" for s in n.walk())), None)
    assert lose is not None
    l_fills = [s.props.get("bg_drawable", {}).get("fill") for s in lose.walk() if s.props.get("bg_drawable")]
    assert "#42a5f5" in m_fills
    assert m_tv is not None and m_tv.props.get("color") == "#ffffff"
    assert "#f5f5f5" in l_fills

    # 2) NotificationPermission -> BecomePro -> [X] -> Home/Access ohne Loop
    gr2 = GlassAppRunner(vg_root, zone="original")
    gr2.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    gr2.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/onboarding/loading/view/LoadingIntakeActivity;",
        max_steps=600000,
        native_fusion=False,
    )
    surf2 = SceneGraphSurface(gr2.tree, vm=gr2.vm)
    assert gr2.tree.root.bounds == (0, 0, 420, 760)
    allow_btn = next((n for n in gr2.tree.root.walk() if n.text == "Allow"), None)
    maybe_lbl = next((n for n in gr2.tree.root.walk() if n.text == "Maybe later"), None)
    assert allow_btn is not None and maybe_lbl is not None
    assert allow_btn.bounds[3] <= 60
    assert allow_btn.bounds[1] + allow_btn.bounds[3] <= maybe_lbl.bounds[1]

    _tap(gr2, surf2, maybe_lbl)
    assert gr2.runner.navigate_to_pending() is True
    assert "BecomeProActivity" in getattr(getattr(gr2.stubs, "_current_activity", None), "cls", "")
    assert gr2.runner.activity_stack == []

    x_btn = next((n for n in gr2.tree.root.walk() if n.cls == "Button" and n.bounds[2] <= 56 and n.bounds[1] < 160), None)
    assert x_btn is not None and x_btn.bounds[2] <= 48
    assert x_btn.text != "top_left_action"
    long_tvs = [n for n in gr2.tree.root.walk() if n.cls == "TextView" and len(n.text or "") > 40]
    assert long_tvs and all(n.bounds[2] >= 240 for n in long_tvs)

    _tap(gr2, surf2, x_btn)
    assert getattr(gr2.vm, "last_exc", None) is None
    assert "HomeActivity" in (getattr(gr2.stubs, "_pending_nav_target", "") or "")
    assert gr2.runner.navigate_to_pending() is True
    assert "AccessActivity" in getattr(getattr(gr2.stubs, "_current_activity", None), "cls", "")
    assert "BecomeProActivity" not in [e.get("cls", "") for e in gr2.runner.activity_stack]

    # 3) Eingeloggte HomeActivity erhaelt CoordinatorLayout + BottomNavigation trotz ComposeViews
    gr3 = GlassAppRunner(vg_root, zone="original")
    gr3.build(net_responder=lambda _r: NetResponse(200, {}, b"{}", "OK"), native_fusion=False)
    vp = gr3.stubs._prefs_store.setdefault("VirtuaGym.prefs", {})
    vp["profile.authtype"] = JString("authtype.vg_oauth")
    vp["profile.userid"] = 12345
    gr3.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;",
        max_steps=600000,
        native_fusion=False,
    )
    assert "HomeActivity" in getattr(getattr(gr3.stubs, "_current_activity", None), "cls", "")
    assert gr3.tree.root is not None and gr3.tree.root.cls == "CoordinatorLayout"
    bnav = gr3.tree.root.find("bottom_navigation_holder")
    assert bnav is not None and bnav.bounds[1] >= 660
