# -*- coding: utf-8 -*-
"""tests/test_rn_app_producer.py — Regressionstests fuer den generischen React-Native-/Hermes-Producer."""
from __future__ import annotations

import os
import random
import struct
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.app_runtime import is_unrendered_surface_shell  # noqa: E402
from core.java.class_provider import StudioClassProvider  # noqa: E402
from core.java.dalvik import DalvikVM  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.lifecycle import LifecycleRunner, _StubRuntime  # noqa: E402
from core.java.net_recorder import NetLog, NetResponse, install_net_stubs  # noqa: E402
from core.java.rn_app_producer import (  # noqa: E402
    extract_rn_app_spec,
    mount_rn_app_into_tree,
    parse_hermes_hbc_bundle,
)
from core.java.view_tree import install_view_stubs, paint_commands  # noqa: E402


def _make_synthetic_hbc_v98(strings_spec: list[tuple[bool, str]]) -> bytes:
    """Erzeugt eine gueltige HBC-v98-Binaerdatei mit 12B FuncHeaders, StringKinds & StringTable."""
    fn_cnt = 1
    sk_cnt = 1
    id_cnt = 0
    st_cnt = len(strings_spec)
    ov_entries: list[tuple[int, int]] = []
    sst_bytes = bytearray()
    storage = bytearray()

    for is_u16, txt in strings_spec:
        raw = txt.encode("utf-16le") if is_u16 else txt.encode("latin1")
        char_len = len(txt)
        s_off = len(storage)
        storage.extend(raw)
        if char_len >= 255:
            ov_idx = len(ov_entries)
            ov_entries.append((s_off, char_len))
            entry = (1 if is_u16 else 0) | ((ov_idx & 0x7FFFFF) << 1) | (0xFF << 24)
        else:
            entry = (1 if is_u16 else 0) | ((s_off & 0x7FFFFF) << 1) | ((char_len & 0xFF) << 24)
        sst_bytes.extend(struct.pack("<I", entry))

    ov_cnt = len(ov_entries)
    ovt_bytes = b"".join(struct.pack("<II", o, l) for o, l in ov_entries)
    func_hdrs = b"\x00" * (fn_cnt * 12)
    kind_tbl = struct.pack("<I", st_cnt)
    body = func_hdrs + kind_tbl + bytes(sst_bytes) + ovt_bytes + bytes(storage)
    total_len = 128 + len(body)

    hdr = bytearray(128)
    hdr[0:8] = b"\xc6\x1f\xbc\x03\xc1\x03\x19\x1f"
    struct.pack_into("<I", hdr, 8, 98)
    struct.pack_into("<I", hdr, 32, total_len)
    struct.pack_into("<6I", hdr, 40, fn_cnt, sk_cnt, id_cnt, st_cnt, ov_cnt, len(storage))
    return bytes(hdr) + body


def test_hermes_hbc_v98_parser_synthetic_and_alpicool():
    long_url = "https://api.example.invalid/machine/api/activateMachine?" + ("x" * 240)
    raw = _make_synthetic_hbc_v98([
        (False, "flex"),
        (False, "TouchableOpacity"),
        (False, "BleManager"),
        (False, "00001234-0000-1000-8000-00805f9b34fb"),
        (False, long_url),
        (True, "Mein Kühlschrank"),
    ])
    parsed = parse_hermes_hbc_bundle(raw)
    assert parsed["is_hbc"] is True
    assert parsed["version"] == 98
    assert parsed["strings"][0] == "flex"
    assert "Mein Kühlschrank" in parsed["strings"]
    assert "00001234-0000-1000-8000-00805f9b34fb" in parsed["ble_uuids"]
    assert long_url[:40] in (parsed["endpoints"][0] if parsed["endpoints"] else "")

    alpicool_root = os.path.join(_ROOT, "source", "com.alpicoolneutral.fridge.controller", "base_unpacked_apktool")
    if os.path.isdir(alpicool_root):
        spec = extract_rn_app_spec(alpicool_root)
        assert spec["bundle_kind"] == "hbc"
        assert spec["hbc_version"] == 98
        assert spec["archetype"] == "ble_iot"
        assert spec["app_name"] == "CAR FRIDGE FREEZER"
        assert "https://k3plugin.dev.alpicool.com/machine/api/activateMachine" in spec["endpoints"]
        assert "00001234-0000-1000-8000-00805f9b34fb" in spec["ble_uuids"]
        assert spec["images"]["logo"] and os.path.isfile(spec["images"]["logo"])


def _write_synthetic_rn_ble_apk(base_dir):
    (base_dir / "assets").mkdir(parents=True)
    (base_dir / "res" / "values").mkdir(parents=True)
    (base_dir / "smali" / "com" / "demo").mkdir(parents=True)

    (base_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.fridge">'
        '<uses-permission android:name="android.permission.BLUETOOTH_CONNECT"/>'
        '<application android:name=".DemoApp" android:label="Demo Fridge">'
        '<activity android:name=".MainActivity" android:exported="true">'
        '<intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )
    (base_dir / "res" / "values" / "strings.xml").write_text(
        '<resources><string name="app_name">Demo Fridge</string></resources>', encoding="utf-8"
    )
    (base_dir / "smali" / "com" / "demo" / "MainActivity.smali").write_text(
        ".class public Lcom/demo/MainActivity;\n"
        ".super Landroid/app/Activity;\n"
        ".method public constructor <init>()V\n"
        "    .locals 0\n    invoke-direct {p0}, Landroid/app/Activity;-><init>()V\n    return-void\n.end method\n"
        ".method public onCreate(Landroid/os/Bundle;)V\n"
        "    .locals 0\n    return-void\n.end method\n",
        encoding="utf-8",
    )
    hbc = _make_synthetic_hbc_v98([
        (False, "flexDirection"),
        (False, "BleManager"),
        (False, "RNAsyncStorage"),
        (False, "00001234-0000-1000-8000-00805f9b34fb"),
        (False, "00001235-0000-1000-8000-00805f9b34fb"),
        (False, "https://k3plugin.dev.alpicool.com/machine/api/activateMachine"),
        (False, "Add Fridge"),
        (True, "Mein Kühlschrank"),
        (False, "Fridge is under APP controls"),
    ])
    (base_dir / "assets" / "index.android.bundle").write_bytes(hbc)
    return str(base_dir)


def _run_and_verify_ble_flow(app_root: str):
    vm = DalvikVM()
    vm.lenient = True
    vm.step_budget = 5_000_000
    vm.class_provider = StudioClassProvider(app_root, zone="original")
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    tree = install_view_stubs(stubs, app_root=app_root)
    net_log = NetLog()
    install_net_stubs(
        stubs,
        responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"code":0,"msg":"activated"}', "OK"),
        net_log=net_log,
    )
    vm.runtime = _StubRuntime(stubs)
    runner = LifecycleRunner(vm, provider=vm.class_provider, with_stubs=False, stubs=stubs)
    rep = runner.run_app(studio_root=app_root)

    assert tree.root is not None and tree.root.cls == "ReactRootView"
    assert is_unrendered_surface_shell(tree) is False
    tree.layout()
    cmds = paint_commands(tree)
    assert any(c["op"] == "text" for c in cmds)

    # Keine getApplicationInfo/String.split-Missing-Eintraege mehr
    miss_sigs = {(m.get("cls"), m.get("name")) for m in (rep.get("missing") or [])}
    assert ("Landroid/content/Context;", "getApplicationInfo") not in miss_sigs
    assert ("Ljava/lang/String;", "split") not in miss_sigs

    ctrl = getattr(tree, "_rn_ctrl", None)
    assert ctrl is not None and len(ctrl.turbo.log) >= 4

    # Pairing-Anleitung -> Hardware-Zurueck
    guide_btn = tree.root.find("rn_pairing_guide_btn")
    assert guide_btn is not None
    guide_btn.on_click(guide_btn)
    assert ctrl.screen == "guide"
    assert runner.handle_back() is True
    assert ctrl.screen == "scan"

    # Scan -> Verbinden -> Steuerung -> Temp + Slider -> Lock Toast -> Cloud Activate
    scan_btn = tree.root.find("rn_ble_scan_btn")
    scan_btn.on_click(scan_btn)
    conn_btn = tree.root.find("rn_connect_dev_0")
    assert conn_btn is not None
    conn_btn.on_click(conn_btn)
    assert ctrl.screen == "control"

    before_l = ctrl.temp_l
    tree.root.find("rn_temp_plus").on_click(None)
    assert ctrl.temp_l == before_l + 1
    tree.root.find("rn_temp_minus").on_click(None)
    assert ctrl.temp_l == before_l
    slider = tree.root.find("rn_temp_slider")
    assert slider is not None and callable(slider.props.get("$on_progress_fn"))
    slider.props["$on_progress_fn"](75)  # 75% von [-20..20] = +10°C
    assert ctrl.temp_l == 10
    assert any(ev["op"] == "writeChar" for ev in stubs._ble_log)
    assert "last_ble_cmd" in (stubs._prefs_store.get("RNAsyncStorage") or {})

    # ECO + °C/°F + Sperre + Unlock + Cloud-API
    tree.root.find("rn_btn_mode").on_click(None)
    assert ctrl.eco_mode is True
    tree.root.find("rn_btn_unit").on_click(None)
    assert ctrl.unit == "F"
    tree.root.find("rn_btn_lock").on_click(None)
    assert ctrl.locked is True
    tree.root.find("rn_temp_plus").on_click(None)
    assert ctrl.temp_l == 10 and "Bediensperre" in (tree.last_toast or "")
    tree.root.find("rn_btn_lock").on_click(None)

    cbtn = tree.root.find("rn_cloud_activate_btn")
    assert cbtn is not None
    cbtn.on_click(None)
    assert any("activateMachine" in e["req"].url for e in net_log.entries)

    # Browser-Toggle und Zurueck innerhalb von ReactRootView
    ctrl.open_browser("https://k3plugin.dev.alpicool.com/machine/api/activateMachine")
    assert ctrl.screen == "browser" and tree.root.find("in_phone_browser_root") is not None
    assert runner.handle_back() is True
    assert ctrl.screen == "control"


def test_rn_ble_iot_in_phone_controller_and_alpicool_lifecycle(tmp_path):
    synth_root = _write_synthetic_rn_ble_apk(tmp_path / "synth_ble")
    _run_and_verify_ble_flow(synth_root)

    alpicool_root = os.path.join(_ROOT, "source", "com.alpicoolneutral.fridge.controller", "base_unpacked_apktool")
    if os.path.isdir(alpicool_root):
        _run_and_verify_ble_flow(alpicool_root)


def test_rn_auth_api_and_encrypted_bundle_modes(tmp_path):
    # 1) Plain-JS Auth App
    js_app = tmp_path / "js_auth"
    (js_app / "assets").mkdir(parents=True)
    (js_app / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.auth">'
        '<application android:label="AuthRN"><activity android:name=".MainActivity"/></application></manifest>',
        encoding="utf-8",
    )
    (js_app / "assets" / "index.android.bundle").write_text(
        'const EP = "https://auth.demo.local/v1/login"; const A = "Sign In"; const B = "Password";',
        encoding="utf-8",
    )
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs, app_root=str(js_app))
    net_log = NetLog()
    install_net_stubs(stubs, responder=lambda r: NetResponse(200, {"Content-Type": "application/json"}, b'{"jwt":"ok"}', "OK"), net_log=net_log)
    ctrl = mount_rn_app_into_tree(tree, str(js_app), stubs=stubs)
    assert ctrl.archetype == "auth_api" and ctrl.spec["bundle_kind"] == "js"
    tree.root.find("rn_login_user").props["$on_change_fn"]("alice@demo.de")
    tree.root.find("rn_login_pass").props["$on_change_fn"]("pw123")
    tree.root.find("rn_login_submit").on_click(None)
    assert any(e["req"].url == "https://auth.demo.local/v1/login" for e in net_log.entries)
    assert ctrl.screen == "home"

    # 2) Verschluesseltes High-Entropy-Bundle (transparente Kennzeichnung)
    enc_app = tmp_path / "enc_app"
    (enc_app / "assets").mkdir(parents=True)
    rng = random.Random(42)
    (enc_app / "assets" / "index.android.bundle").write_bytes(bytes(rng.randrange(256) for _ in range(4096)))
    t2 = install_view_stubs(stubs, app_root=str(enc_app))
    ctrl2 = mount_rn_app_into_tree(t2, str(enc_app), stubs=stubs)
    assert ctrl2.spec["bundle_kind"] == "encrypted" and ctrl2.archetype == "encrypted"
    assert "Verschlüsseltes RN-Bundle" in t2.root.find("rn_auth_badge").text
