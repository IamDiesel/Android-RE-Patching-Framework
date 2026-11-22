# -*- coding: utf-8 -*-
"""Tests fuer Map-Backing-Views, Hybrid-Web-Netztraffic, Responsive Debugger-Pause/Stop, Single-Step (In/Over/Out/Rev), Tooltips & TopBar-Hilfe."""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _make_vm_stubs():
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    return vm, stubs


def test_hashmap_backing_views_mutate_map_and_hybrid_web_network_bridge(tmp_path):
    from core.java.hybrid_web_producer import analyze_hybrid_web_app, mount_hybrid_web_app_into_tree
    from core.java.net_recorder import NetLog, NetResponse, install_net_stubs
    from core.java.objects import JObject, JString
    from core.java.view_tree import ViewTree

    _vm, stubs = _make_vm_stubs()

    # 1) Map.values().remove(val) + keySet().remove(k) + entrySet().remove(e) + Iterator.remove()
    hm = JObject("Ljava/util/HashMap;", {})
    stubs.resolve("Ljava/util/HashMap;", "<init>", "()V", [hm], False)
    stubs.resolve("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", [hm, JString("a"), None], False)
    stubs.resolve("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", [hm, JString("b"), JString("null")], False)
    stubs.resolve("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", [hm, JString("c"), JString("ok")], False)

    _, vals = stubs.resolve("Ljava/util/HashMap;", "values", "()Ljava/util/Collection;", [hm], False)
    assert stubs.resolve("Ljava/util/Collection;", "remove", "(Ljava/lang/Object;)Z", [vals, None], False)[1] == 1
    assert stubs.resolve("Ljava/util/Collection;", "remove", "(Ljava/lang/Object;)Z", [vals, None], False)[1] == 0
    assert stubs.resolve("Ljava/util/Collection;", "remove", "(Ljava/lang/Object;)Z", [vals, JString("null")], False)[1] == 1
    assert stubs.resolve("Ljava/util/HashMap;", "size", "()I", [hm], False)[1] == 1

    stubs.resolve("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", [hm, JString("d"), JString("v4")], False)
    _, ks = stubs.resolve("Ljava/util/HashMap;", "keySet", "()Ljava/util/Set;", [hm], False)
    assert stubs.resolve("Ljava/util/Set;", "remove", "(Ljava/lang/Object;)Z", [ks, JString("d")], False)[1] == 1
    assert "d" not in hm.fields.get("map", {})

    _, es = stubs.resolve("Ljava/util/HashMap;", "entrySet", "()Ljava/util/Set;", [hm], False)
    _, it = stubs.resolve("Ljava/util/Set;", "iterator", "()Ljava/util/Iterator;", [es], False)
    assert stubs.resolve("Ljava/util/Iterator;", "hasNext", "()Z", [it], False)[1] == 1
    stubs.resolve("Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [it], False)
    stubs.resolve("Ljava/util/Iterator;", "remove", "()V", [it], False)
    assert stubs.resolve("Ljava/util/HashMap;", "size", "()I", [hm], False)[1] == 0

    # 2) Hybrid-Web JS API -> NetLog Durchleitung
    app_dir = tmp_path / "hybrid_net_app"
    www = app_dir / "assets" / "www"
    www.mkdir(parents=True)
    (www / "index.html").write_text("<html><head><title>Fridge</title></head><body>Hi</body></html>", encoding="utf-8")
    (www / "app.js").write_text(
        'var base = "https://api.example.invalid/v2";\n'
        'var r1 = "/update/app";\n'
        'var r2 = "/group/device/control";\n',
        encoding="utf-8",
    )
    spec = analyze_hybrid_web_app(str(app_dir))
    assert any(ep["path"] == "/update/app" for ep in spec["api_endpoints"])

    nlog = NetLog()
    install_net_stubs(stubs, responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', "OK"), net_log=nlog)
    vtree = ViewTree(width=380, height=776)
    ctrl = mount_hybrid_web_app_into_tree(vtree, str(app_dir), stubs=stubs)
    assert len(nlog.entries) >= 1
    assert "api.example.invalid" in nlog.entries[0]["req"].url

    n0 = len(nlog.entries)
    ctrl._go("apis")
    send_btn = vtree.root.find("hw_ep_send_0")
    assert send_btn is not None
    send_btn.on_click()
    assert len(nlog.entries) == n0 + 1


def test_real_scinan_fridge_finishes_loop_and_records_network_if_present():
    scinan = os.path.join(_ROOT, "source", "com.scinan.TieFuZX.Refrigerator", "base_unpacked_apktool")
    if not os.path.isdir(scinan):
        pytest.skip("Offline Scinan APK directory not present")

    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import LifecycleRunner, _StubRuntime
    from core.java.net_recorder import NetLog, NetResponse, install_net_stubs
    from core.java.view_tree import install_view_stubs

    vm = DalvikVM()
    vm.lenient = True
    vm.step_budget = 300_000
    vm.class_provider = StudioClassProvider(scinan, zone="original")
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vtree = install_view_stubs(stubs, app_root=scinan)
    nlog = NetLog()
    install_net_stubs(stubs, responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"ret":0}', "OK"), net_log=nlog)
    vm.runtime = _StubRuntime(stubs)
    LifecycleRunner(vm, provider=vm.class_provider, with_stubs=False, stubs=stubs).run_app(studio_root=scinan)

    assert vm.steps < 300_000
    urls = [e["req"].url for e in nlog.entries]
    assert any("er.dcloud.io" in u for u in urls)
    assert any("scinan.com" in u for u in urls)
    assert getattr(vtree, "_hybrid_ctrl", None) is not None


def test_lifecycle_debug_stop_propagates_and_single_step_in_over_out_rev(tmp_path):
    from core.java.debug import JavaDebugger
    from ui.controllers.debugger_controller import DebuggerController

    # Synthetische App mit Multi-Phase-Lifecycle + Callee fuer In / Over / Out / Rev
    app_dir = tmp_path / "step_app"
    (app_dir / "smali" / "test").mkdir(parents=True)
    (app_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="test.step">'
        '<application android:label="StepApp">'
        '<activity android:name="test.StepAct">'
        '<intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )
    (app_dir / "smali" / "test" / "StepAct.smali").write_text(
        ".class public Ltest/StepAct;\n"
        ".super Landroid/app/Activity;\n"
        ".field public static marker:I\n"
        ".method public constructor <init>()V\n"
        "    .locals 1\n"
        "    invoke-direct {p0}, Landroid/app/Activity;-><init>()V\n"
        "    return-void\n"
        ".end method\n"
        ".method private static helper()I\n"
        "    .locals 2\n"
        "    const/4 v0, 0x5\n"
        "    const/4 v1, 0x2\n"
        "    add-int/2addr v0, v1\n"
        "    return v0\n"
        ".end method\n"
        ".method protected onCreate(Landroid/os/Bundle;)V\n"
        "    .locals 2\n"
        "    invoke-static {}, Ltest/StepAct;->helper()I\n"
        "    move-result v0\n"
        "    const/4 v1, 0x1\n"
        "    add-int/2addr v0, v1\n"
        "    sput v0, Ltest/StepAct;->marker:I\n"
        "    return-void\n"
        ".end method\n"
        ".method protected onStart()V\n"
        "    .locals 1\n"
        "    const/16 v0, 0x2a\n"
        "    sput v0, Ltest/StepAct;->marker:I\n"
        "    return-void\n"
        ".end method\n"
        ".method protected onResume()V\n"
        "    .locals 1\n"
        "    const/16 v0, 0x63\n"
        "    sput v0, Ltest/StepAct;->marker:I\n"
        "    return-void\n"
        ".end method\n",
        encoding="utf-8",
    )

    class _FakeApp:
        class cfg:
            config = {}
            paths = {}

        def after(self, _ms, fn=None):
            if callable(_ms):
                _ms()
            elif callable(fn):
                fn()
            return 1

        def after_cancel(self, _):
            pass

        def log(self, _msg):
            pass

    _FakeApp.cfg.config = {"APP_SOURCE_DIR": str(app_dir)}

    ctrl = DebuggerController(_FakeApp())
    ctrl.native_fusion = False
    ctrl.entry_stop = True
    ctrl.record_timeline = False  # Rev muss auch OHNE vorab aktivierte Timeline funktionieren!
    ctrl.start_app()

    def _wait_idle(timeout=4.0):
        t0 = time.monotonic()
        while ctrl._busy and time.monotonic() - t0 < timeout:
            time.sleep(0.02)

    _wait_idle()
    assert ctrl._jdb is not None and ctrl._jdb.mode == "paused"
    assert ctrl._jdb.step_count == 0
    assert ctrl._jdb.current["fr"].md.name == "<init>"

    # In -> naechste Instruktion
    ctrl.step()
    _wait_idle()
    assert ctrl._jdb.mode == "paused"
    assert ctrl._jdb.step_count == 1

    # Out aus depth==1 (<init>) -> muss direkt in onCreate halten (nicht durchlaufen bis done!)
    ctrl.step_out()
    _wait_idle()
    assert ctrl._jdb.mode == "paused"
    assert ctrl._jdb.current["fr"].md.name == "onCreate"

    # Over ueber invoke-static helper() -> ueberspringt helper und haelt an move-result v0 in onCreate
    s_before_over = ctrl._jdb.step_count
    ctrl.step_over()
    _wait_idle()
    assert ctrl._jdb.mode == "paused"
    assert ctrl._jdb.current["fr"].md.name == "onCreate"
    assert ctrl._jdb.step_count > s_before_over + 1

    # Rev -> deterministisch 1 Schritt zurueck trotz record_timeline=False
    cur_s = ctrl._jdb.step_count
    ctrl.reverse_step()
    _wait_idle()
    assert ctrl._jdb.mode == "paused"
    assert ctrl._jdb.step_count == cur_s - 1

    # Stop bricht direkt in stopped ab und laeuft NICHT in onStart/onResume weiter
    ctrl.stop()
    assert ctrl._jdb.mode == "stopped"
    assert ctrl._jdb.vm.statics.get("Ltest/StepAct;", {}).get("marker", 0) != 0x63

    # Step-Guard bei Endlosschleife in Einzelmethode
    (app_dir / "smali" / "test" / "InfLoop.smali").write_text(
        ".class public Ltest/InfLoop;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static spin()V\n"
        "    .locals 1\n"
        ":top\n"
        "    const/4 v0, 0x0\n"
        "    goto :top\n"
        ".end method\n",
        encoding="utf-8",
    )
    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM

    vm2 = DalvikVM()
    vm2.class_provider = StudioClassProvider(str(app_dir))
    jdb2 = JavaDebugger(vm2, max_steps=10_000_000)
    jdb2.set_budget(10_000_000, pause_on_budget=True)
    jdb2.start("Ltest/InfLoop;", "spin", "()V", stop_at_entry=True)
    st = jdb2.step_out()
    assert st["mode"] == "paused" and st["reason"] == "step_limit"
    jdb2.stop()


def test_unified_2s_tooltip_and_topbar_help():
    from ui.widgets.tooltip import DEFAULT_TOOLTIP_DELAY_MS, Tooltip
    assert DEFAULT_TOOLTIP_DELAY_MS == 500

    import tkinter as tk
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tk display not available")
    try:
        btn = tk.Button(root, text="Test")
        tip = Tooltip(btn, "Erklaerung")
        assert tip.delay == 500
    finally:
        root.destroy()


def test_ui_inspect_button_label_cursor_and_status():
    import tkinter as tk
    from types import SimpleNamespace
    from core.java.view_tree import ViewTree, ViewNode
    from ui.tabs.display_tab import DisplayTab

    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tk display not available")
    try:
        mock_app = SimpleNamespace(cfg=SimpleNamespace(config={}))
        tab = DisplayTab(root, mock_app, embedded=True)
        vt = ViewTree()
        rn = ViewNode("LinearLayout")
        btn_nd = ViewNode("Button", vid="btn_login", text="Anmelden", clickable=True)
        rn.add(btn_nd)
        vt.set_root(rn)
        vt.layout()
        tab.set_tree(vt, vm="glass")

        assert tab._ui_inspect_btn.cget("text") == "🎯 UI-Lupe"
        tab.toggle_ui_inspect()
        assert tab._ui_inspect_btn.cget("text") == "🟢 UI-Lupe: AN"
        assert tab.canvas.cget("cursor") == "crosshair"
        assert "UI-Lupe aktiv" in tab.status.cget("text")

        vt._highlight_node = btn_nd
        tab.render()
        st_txt = tab.status.cget("text")
        assert "Button" in st_txt and "#btn_login" in st_txt and "Anmelden" in st_txt

        tab.toggle_ui_inspect()
        assert tab._ui_inspect_btn.cget("text") == "🎯 UI-Lupe"
        assert tab.canvas.cget("cursor") == ""
        assert vt._highlight_node is None
    finally:
        root.destroy()
