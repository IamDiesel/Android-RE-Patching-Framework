# -*- coding: utf-8 -*-
"""Tests fuer Hybrid-Web-Engine, Offline-APK-Stubs (G15) und Visual/Layout-Fixes."""
from __future__ import annotations

import json
import os
import sys

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


def test_hybrid_web_synthetic_dcloud_and_cordova(tmp_path):
    from core.java.app_runtime import detect_runtime
    from core.java.hybrid_web_producer import (
        analyze_hybrid_web_app,
        hybrid_web_producer,
        mount_hybrid_web_app_into_tree,
    )
    from core.java.producer_wiring import ensure_default_producers
    from core.java.runtime_detect import detect_runtime as detect_runtime_family
    from core.java.surface import ProducerRegistry
    from core.java.view_tree import ViewTree

    # 1) Synthetische DCloud-App
    dc = tmp_path / "dcloud_app"
    (dc / "assets" / "data").mkdir(parents=True)
    (dc / "assets" / "data" / "dcloud_control.xml").write_text(
        '<hbuilder><apps><app appid="H123456" appver="1.0.0"/></apps></hbuilder>',
        encoding="utf-8",
    )
    www = dc / "assets" / "apps" / "H123456" / "www"
    (www / "src" / "i18n").mkdir(parents=True)
    (www / "src" / "template" / "control").mkdir(parents=True)
    (www / "manifest.json").write_text(
        json.dumps({
            "id": "H123456",
            "name": "Generic IoT Panel",
            "version": {"name": "2.1.0"},
            "launch_path": "src/template/control/main.html",
            "plus": {"statusbar": {"background": "#00A8E7"}},
            "permissions": {"Bluetooth": {}, "SQLite": {}},
        }),
        encoding="utf-8",
    )
    (www / "src" / "i18n" / "strings_en.properties").write_text(
        "TITLE_MAIN=Dashboard Control\nBTN_SAVE=Apply Settings\n",
        encoding="utf-8",
    )
    (www / "src" / "template" / "control" / "main.html").write_text(
        '<!doctype html><html><head><title>Dashboard</title></head>'
        '<body><header class="mui-title" data-locale="TITLE_MAIN">Title</header>'
        '<button data-locale="BTN_SAVE">Save</button>'
        '<script>var api = "https://api.example.invalid/v1/status";</script></body></html>',
        encoding="utf-8",
    )

    lbl, _ = detect_runtime(str(dc))
    assert lbl == "Hybrid Web"
    assert detect_runtime_family(str(dc)) == "hybrid web"

    spec = analyze_hybrid_web_app(str(dc))
    assert spec["framework"] == "DCloud HTML5+"
    assert spec["app_name"] == "Generic IoT Panel"
    assert spec["archetype"] == "device_control"
    assert any(p["title"] == "Dashboard Control" for p in spec["pages"])
    assert "https://api.example.invalid/v1/status" in spec["urls"]

    tree = ViewTree(width=380, height=776)
    ctrl = mount_hybrid_web_app_into_tree(tree, str(dc))
    assert tree.root is not None
    assert tree.root.find("hw_pwr_btn") is not None

    # Interaktive Steuerung: Temperatur / Power / Seiten / WebView / Back / Home
    t0 = ctrl.target_temp
    tree.root.find("hw_temp_inc").on_click()
    assert ctrl.target_temp == t0 + 1
    tree.root.find("hw_pwr_btn").on_click()
    assert ctrl.power_on is False
    tree.root.find("hw_nav_pages").on_click()
    assert ctrl.screen == "pages"
    tree.root.find("hw_nav_about").on_click()
    assert ctrl.screen == "about"
    ctrl.open_browser()
    assert ctrl.screen == "browser"
    assert tree.root.find("in_phone_browser_root") is not None
    assert ctrl.handle_back() is True
    assert ctrl.screen == "about"
    assert ctrl.handle_home() is True
    assert ctrl.screen == "home"

    reg = ProducerRegistry()
    ensure_default_producers(reg)
    surf = hybrid_web_producer({"app_root": str(dc)})
    assert surf.tree.root is not None

    # 2) Synthetische Cordova-App
    cdv = tmp_path / "cordova_app"
    (cdv / "assets" / "www").mkdir(parents=True)
    (cdv / "res" / "xml").mkdir(parents=True)
    (cdv / "res" / "xml" / "config.xml").write_text(
        '<widget id="org.demo.web" version="1.2.3"><name>Demo Portal</name>'
        '<content src="index.html"/><feature name="NetworkStatus"/></widget>',
        encoding="utf-8",
    )
    (cdv / "assets" / "www" / "cordova.js").write_text("// cordova", encoding="utf-8")
    (cdv / "assets" / "www" / "index.html").write_text(
        "<html><head><title>Portal</title></head><body><h1>Welcome</h1></body></html>",
        encoding="utf-8",
    )
    cdv_lbl, _ = detect_runtime(str(cdv))
    assert cdv_lbl == "Hybrid Web"
    cdv_spec = analyze_hybrid_web_app(str(cdv))
    assert cdv_spec["framework"] == "Cordova"
    assert cdv_spec["app_name"] == "Demo Portal"


def _run_app_headless(app_root: str, step_budget: int = 250_000):
    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import LifecycleRunner, _StubRuntime
    from core.java.net_recorder import NetLog, NetResponse, install_net_stubs
    from core.java.view_tree import install_view_stubs

    vm = DalvikVM()
    vm.lenient = True
    vm.step_budget = step_budget
    vm.class_provider = StudioClassProvider(app_root, zone="original")
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vtree = install_view_stubs(stubs, app_root=app_root)
    install_net_stubs(
        stubs,
        responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b"{}", "OK"),
        net_log=NetLog(),
    )
    vm.runtime = _StubRuntime(stubs)
    runner = LifecycleRunner(vm, provider=vm.class_provider, with_stubs=False, stubs=stubs)
    res = runner.run_app(studio_root=app_root)
    return vtree, res, stubs


def test_hybrid_web_scinan_real_if_present():
    scinan = os.path.join(_ROOT, "source", "com.scinan.TieFuZX.Refrigerator", "base_unpacked_apktool")
    if not os.path.isdir(scinan):
        pytest.skip("Offline Scinan APK directory not present")

    from core.java.app_runtime import detect_runtime
    from core.java.hybrid_web_producer import analyze_hybrid_web_app

    lbl, _ = detect_runtime(scinan)
    assert lbl == "Hybrid Web"
    spec = analyze_hybrid_web_app(scinan)
    assert spec["framework"] == "DCloud HTML5+"
    assert len(spec["pages"]) >= 5

    vtree, res, _stubs = _run_app_headless(scinan, step_budget=200_000)
    assert getattr(vtree, "_hybrid_ctrl", None) is not None
    assert len(list(vtree.root.walk())) >= 12
    assert res is not None


def test_create_configuration_context_distinct_and_serversocket():
    from core.java.dalvik import DalvikException
    from core.java.objects import JObject, JString

    _vm, stubs = _make_vm_stubs()
    CFG = "Landroid/content/res/Configuration;"
    CTX = "Landroid/content/Context;"
    ACT = "Landroid/app/Activity;"

    act = JObject(ACT, {"__pkg__": "com.test.app"})
    cfg = JObject(CFG, {})
    for cls in (CTX, ACT, "Landroid/content/ContextWrapper;", "Landroid/app/Application;"):
        ok, derived = stubs.resolve(cls, "createConfigurationContext", f"({CFG}){CTX}", [act, cfg], False)
        assert ok
        assert isinstance(derived, JObject)
        assert derived is not act
        assert derived.cls == CTX
        assert derived.fields.get("__base__") is act

    # ServerSocket.accept: 2x SocketTimeoutException, dann 3. Aufruf SocketException + closed
    SS = "Ljava/net/ServerSocket;"
    ss = JObject(SS, {})
    stubs.resolve(SS, "<init>", "(I)V", [ss, 8080], False)
    for _attempt in range(2):
        with pytest.raises(DalvikException) as ei:
            stubs.resolve(SS, "accept", "()Ljava/net/Socket;", [ss], False)
        assert "SocketTimeoutException" in str(ei.value)

    with pytest.raises(DalvikException) as ei3:
        stubs.resolve(SS, "accept", "()Ljava/net/Socket;", [ss], False)
    assert "SocketException" in str(ei3.value)
    assert stubs.resolve(SS, "isClosed", "()Z", [ss], False)[1] == 1

    # Socket connect & close
    SO = "Ljava/net/Socket;"
    so = JObject(SO, {})
    stubs.resolve(SO, "<init>", "(Ljava/lang/String;I)V", [so, JString("127.0.0.1"), 9000], False)
    assert stubs.resolve(SO, "isConnected", "()Z", [so], False)[1] == 1
    assert stubs.resolve(SO, "getPort", "()I", [so], False)[1] == 9000
    stubs.resolve(SO, "close", "()V", [so], False)
    assert stubs.resolve(SO, "isClosed", "()Z", [so], False)[1] == 1


def test_real_apks_no_stackoverflow_on_boot():
    for rel_dir in (
        os.path.join("com.arjonasoftware.babycam", "base_unpacked_apktool"),
        os.path.join("com.alpicoolneutral.fridge.controller", "base_unpacked_apktool"),
        os.path.join("com.datamars.kippynew", "base_unpacked_apktool"),
    ):
        full = os.path.join(_ROOT, "source", rel_dir)
        if not os.path.isdir(full):
            continue
        _vtree, res, _stubs = _run_app_headless(full, step_budget=250_000)
        errs = " | ".join(str(e) for e in (res.get("phase_errors") or ()))
        assert "StackOverflow" not in errs, f"StackOverflow in {rel_dir}: {errs}"
        if "babycam" in rel_dir:
            miss_sigs = {(m.get("cls"), m.get("name")) for m in (res.get("missing") or [])}
            assert ("Ljava/net/ServerSocket;", "accept") not in miss_sigs


def test_new_jdk_and_android_stubs(tmp_path):
    from core.java.objects import JArray, JObject, JString
    from core.java.view_tree import install_view_stubs

    _vm, stubs = _make_vm_stubs()
    tree = install_view_stubs(stubs)

    # StringBuilder Appendable bridge & StringBuffer
    sb = JObject("Ljava/lang/StringBuilder;", {})
    stubs.resolve("Ljava/lang/StringBuilder;", "<init>", "()V", [sb], False)
    stubs.resolve("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/CharSequence;)Ljava/lang/Appendable;", [sb, JString("ab")], False)
    stubs.resolve("Ljava/lang/StringBuilder;", "append", "(C)Ljava/lang/Appendable;", [sb, ord("!")], False)
    assert stubs.resolve("Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", [sb], False)[1].text == "ab!"

    sbuf = JObject("Ljava/lang/StringBuffer;", {})
    stubs.resolve("Ljava/lang/StringBuffer;", "<init>", "(Ljava/lang/String;)V", [sbuf, JString("x")], False)
    stubs.resolve("Ljava/lang/StringBuffer;", "append", "(I)Ljava/lang/StringBuffer;", [sbuf, 42], False)
    stubs.resolve("Ljava/lang/StringBuffer;", "append", "(Z)Ljava/lang/StringBuffer;", [sbuf, 1], False)
    assert stubs.resolve("Ljava/lang/StringBuffer;", "toString", "()Ljava/lang/String;", [sbuf], False)[1].text == "x42true"

    # Integer/Long radix parse, Runtime, TextUtils.equals
    assert stubs.resolve("Ljava/lang/Integer;", "parseInt", "(Ljava/lang/String;I)I", [JString("ff"), 16], True)[1] == 255
    assert stubs.resolve("Ljava/lang/Long;", "parseLong", "(Ljava/lang/String;I)J", [JString("1010"), 2], True)[1] == 10
    _, rt = stubs.resolve("Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", [], True)
    assert stubs.resolve("Ljava/lang/Runtime;", "availableProcessors", "()I", [rt], False)[1] >= 1
    TU = "Landroid/text/TextUtils;"
    assert stubs.resolve(TU, "equals", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Z", [None, None], True)[1] == 1
    assert stubs.resolve(TU, "equals", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Z", [None, JString("a")], True)[1] == 0
    assert stubs.resolve(TU, "equals", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Z", [JString("a"), JString("a")], True)[1] == 1

    # Stack, Properties, Pair, LongSparseArray, AtomicReferenceArray
    stk = JObject("Ljava/util/Stack;", {})
    stubs.resolve("Ljava/util/Stack;", "<init>", "()V", [stk], False)
    stubs.resolve("Ljava/util/Stack;", "push", "(Ljava/lang/Object;)Ljava/lang/Object;", [stk, JString("first")], False)
    assert stubs.resolve("Ljava/util/Stack;", "peek", "()Ljava/lang/Object;", [stk], False)[1].text == "first"
    assert stubs.resolve("Ljava/util/Stack;", "pop", "()Ljava/lang/Object;", [stk], False)[1].text == "first"
    assert stubs.resolve("Ljava/util/Stack;", "empty", "()Z", [stk], False)[1] == 1

    pr = JObject("Ljava/util/Properties;", {})
    stubs.resolve("Ljava/util/Properties;", "<init>", "()V", [pr], False)
    bais = JObject("Ljava/io/ByteArrayInputStream;", {"buf": b"host=demo\n# comment\nport=443\n"})
    stubs.resolve("Ljava/util/Properties;", "load", "(Ljava/io/InputStream;)V", [pr, bais], False)
    assert stubs.resolve("Ljava/util/Properties;", "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", [pr, JString("host")], False)[1].text == "demo"
    assert stubs.resolve("Ljava/util/Properties;", "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [pr, JString("missing"), JString("def")], False)[1].text == "def"

    _, pair_obj = stubs.resolve("Landroidx/core/util/Pair;", "create", "(Ljava/lang/Object;Ljava/lang/Object;)Landroidx/core/util/Pair;", [JString("k"), JString("v")], True)
    assert pair_obj.fields["first"].text == "k" and pair_obj.fields["second"].text == "v"

    lsa = JObject("Landroid/util/LongSparseArray;", {})
    stubs.resolve("Landroid/util/LongSparseArray;", "<init>", "()V", [lsa], False)
    stubs.resolve("Landroid/util/LongSparseArray;", "put", "(JLjava/lang/Object;)V", [lsa, 99, JString("item99")], False)
    assert stubs.resolve("Landroid/util/LongSparseArray;", "get", "(J)Ljava/lang/Object;", [lsa, 99], False)[1].text == "item99"
    assert stubs.resolve("Landroid/util/LongSparseArray;", "indexOfKey", "(J)I", [lsa, 99], False)[1] == 0

    ara = JObject("Ljava/util/concurrent/atomic/AtomicReferenceArray;", {})
    stubs.resolve("Ljava/util/concurrent/atomic/AtomicReferenceArray;", "<init>", "(I)V", [ara, 3], False)
    assert stubs.resolve("Ljava/util/concurrent/atomic/AtomicReferenceArray;", "compareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", [ara, 1, None, JString("v1")], False)[1] == 1
    assert stubs.resolve("Ljava/util/concurrent/atomic/AtomicReferenceArray;", "get", "(I)Ljava/lang/Object;", [ara, 1], False)[1].text == "v1"

    # Unsafe field offset + volatile/CAS
    U = "Lsun/misc/Unsafe;"
    _, unsafe = stubs.resolve(U, "getUnsafe", f"(){U}", [], True)
    fld = JObject("Ljava/lang/reflect/Field;", {"owner": "Lcom/Demo;", "name": "state"})
    _, off = stubs.resolve(U, "objectFieldOffset", "(Ljava/lang/reflect/Field;)J", [unsafe, fld], False)
    target = JObject("Lcom/Demo;", {})
    stubs.resolve(U, "putObjectVolatile", "(Ljava/lang/Object;JLjava/lang/Object;)V", [unsafe, target, off, JString("ready")], False)
    assert stubs.resolve(U, "getObjectVolatile", "(Ljava/lang/Object;J)Ljava/lang/Object;", [unsafe, target, off], False)[1].text == "ready"
    assert stubs.resolve(U, "compareAndSwapObject", "(Ljava/lang/Object;JLjava/lang/Object;Ljava/lang/Object;)Z", [unsafe, target, off, JString("ready"), JString("done")], False)[1] == 1
    assert target.fields["state"].text == "done"

    # Resources, Bundle, Intent, TextView.setTextSize
    RES = "Landroid/content/res/Resources;"
    res = JObject(RES, {})
    dm = stubs.resolve(RES, "getDisplayMetrics", "()Landroid/util/DisplayMetrics;", [res], False)[1]
    assert dm.fields["widthPixels"] == 1080
    assert stubs.resolve(RES, "getDimension", "(I)F", [res, 1], False)[1] == 48.0
    assert stubs.resolve(RES, "getIdentifier", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I", [res, JString("app_name"), JString("string"), JString("pkg")], False)[1] != 0

    b = JObject("Landroid/os/Bundle;", {"$map": {"env": JString("prod")}})
    assert stubs.resolve("Landroid/os/Bundle;", "getString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [b, JString("env"), JString("dev")], False)[1].text == "prod"
    assert stubs.resolve("Landroid/os/Bundle;", "getString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [b, JString("absent"), JString("dev")], False)[1].text == "dev"

    tv_obj = JObject("Landroid/widget/TextView;", {})
    stubs.resolve("Landroid/widget/TextView;", "setTextSize", "(IF)V", [tv_obj, 0, 27.5], False)
    assert tv_obj.fields["$view"].props["textSize"] == 10.0


def test_visual_fixes_width_contrast_pill_and_emoji():
    from core.java.ui_mcp_bridge import build_ui_snapshot_dict
    from core.java.view_tree import ViewNode, ViewTree, paint_commands
    from tools.surface_raster import render_snapshot

    tree = ViewTree()  # Standard 420x760
    root = ViewNode("LinearLayout", vid="r", props={"orientation": "vertical"})
    btn = ViewNode(
        "Button", vid="light_btn", text="📖 Pairing-Anleitung", clickable=True,
        props={"bg_drawable": {"kind": "shape", "fill": "#e8eef9", "outline": "#9bb5f5", "radius": 6}},
    )
    nav = ViewNode("LinearLayout", vid="nb", props={"orientation": "horizontal", "role": "navbar", "h": 42})
    nav_it = ViewNode("Button", vid="nav0", text="Geräte", clickable=True, props={"role": "navitem", "selected": True, "layout_weight": 1.0})
    nav.add(nav_it)
    root.add(btn)
    root.add(nav)
    tree.set_root(root)
    assert tree.root.bounds[2] == 420

    snap = build_ui_snapshot_dict(tree=tree)
    assert tree.root.bounds[2] == 380
    assert snap["width"] == 380

    cmds = paint_commands(tree)
    btn_txt_cmds = [c for c in cmds if c.get("op") == "text" and "Pairing-Anleitung" in c.get("text", "")]
    assert btn_txt_cmds and btn_txt_cmds[0]["color"] == "#1b2230"

    # Navitem Pill muss um y + h/2 zentriert sein
    ny, nh = nav_it.bounds[1], nav_it.bounds[3]
    pill_cmds = [c for c in cmds if c.get("op") == "rect" and c.get("fill") == "#d8e3ff"]
    assert pill_cmds
    pill = pill_cmds[0]
    assert abs((pill["y"] + pill["h"] / 2) - (ny + nh / 2)) <= 1.0

    # Rasterizer muss mit Emoji und Ueberbreite-Rechteck ohne Fehler rendern
    snap["paint"].append({"op": "rect", "x": 350, "y": 10, "w": 120, "h": 20, "fill": "#ff0000", "outline": "#ff0000"})
    im = render_snapshot(snap, annotate=True)
    assert im.width > 380 and im.height > 800
