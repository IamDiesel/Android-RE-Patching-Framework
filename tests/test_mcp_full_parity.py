# -*- coding: utf-8 -*-
"""Comprehensive verification of MCP <-> Studio parity (182 catalog tools, Virtual Clock, Rev/Goto, Deep Inspect, API/WebView/Manifest/Fuzzy/AppManager)."""
import ast
import json
import os
import subprocess
import sys
import types
import zipfile

import pytest

from core.application.session_state import SessionState
from core.java.ui_mcp_bridge import build_ui_snapshot_dict
from mcp_server import gating, registry, runtime, tools
from mcp_server.app_context import AppContext
from mcp_server.server import build_server
from ui.controllers.debugger_controller import DebuggerController


def _make_isolated_ctx(tmp_path, monkeypatch):
    ctx = AppContext()
    cfg_file = tmp_path / "config.json"
    ctx.cfg.config_file = str(cfg_file)
    ctx.cfg.config["BASE_DIR"] = str(tmp_path)
    ctx.cfg.config["APP_PACKAGE"] = "com.test.app"
    ctx.cfg.config["SPLIT_NAME"] = "com.test.app"
    ctx.cfg._update_paths()
    ctx.cfg.save()
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path / "glass"))
    return ctx


@pytest.fixture(autouse=True)
def _restore_tools_globals():
    orig_ui = dict(tools._LAST_UI)
    orig_glass = dict(tools._LAST_GLASS)
    orig_clock = tools._DEFAULT_VIRTUAL_CLOCK
    orig_cache = tools._RAM_CACHE
    try:
        yield
    finally:
        tools._LAST_UI.clear()
        tools._LAST_UI.update(orig_ui)
        tools._LAST_GLASS.clear()
        tools._LAST_GLASS.update(orig_glass)
        tools._DEFAULT_VIRTUAL_CLOCK = orig_clock
        tools._RAM_CACHE = orig_cache


def _build_rich_smali_app(app_dir, *, pkg="com.test.parity", method_reg="v0", include_sqlite=True):
    (app_dir / "smali" / "com" / "test").mkdir(parents=True, exist_ok=True)
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{pkg}" android:versionCode="42" android:versionName="4.2.0">
  <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34" />
  <uses-permission android:name="android.permission.INTERNET" />
  <uses-permission android:name="android.permission.CAMERA" />
  <application android:name="{pkg}.App" android:debuggable="true" android:allowBackup="false">
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
    </activity>
    <service android:name=".SyncService" android:exported="true" />
  </application>
</manifest>
"""
    (app_dir / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")

    sqlite_block = ""
    if include_sqlite:
        sqlite_block = f"""
    const-string {method_reg}, "app.db"
    const/4 v2, 0x0
    invoke-static {{{method_reg}, v2}}, Landroid/database/sqlite/SQLiteDatabase;->openOrCreateDatabase(Ljava/lang/String;Landroid/database/sqlite/SQLiteDatabase$CursorFactory;)Landroid/database/sqlite/SQLiteDatabase;
    move-result-object v3
    const-string v4, "CREATE TABLE items (id INTEGER, title TEXT)"
    invoke-virtual {{v3, v4}}, Landroid/database/sqlite/SQLiteDatabase;->execSQL(Ljava/lang/String;)V
    const-string v4, "INSERT INTO items VALUES (1, 'Milk')"
    invoke-virtual {{v3, v4}}, Landroid/database/sqlite/SQLiteDatabase;->execSQL(Ljava/lang/String;)V
"""

    act_smali = f""".class public Lcom/test/MainActivity;
.super Landroid/app/Activity;

.method public helper()V
    .registers 3
    new-instance v0, Lcom/missing/MissingHelper;
    invoke-direct {{v0}}, Lcom/missing/MissingHelper;-><init>()V
    invoke-virtual {{v0}}, Lcom/missing/MissingHelper;->ping()V
    return-void
.end method

.method public onCreate(Landroid/os/Bundle;)V
    .registers 8
    invoke-static {{}}, Ljava/lang/System;->currentTimeMillis()J
    move-result-wide v0
    const-string v2, "prefs_main"
    const/4 v3, 0x0
    invoke-virtual {{p0, v2, v3}}, Lcom/test/MainActivity;->getSharedPreferences(Ljava/lang/String;I)Landroid/content/SharedPreferences;
    move-result-object v4
    invoke-interface {{v4}}, Landroid/content/SharedPreferences;->edit()Landroid/content/SharedPreferences$Editor;
    move-result-object v5
    const-string v6, "boot_ms"
    invoke-interface {{v5, v6, v0, v1}}, Landroid/content/SharedPreferences$Editor;->putLong(Ljava/lang/String;J)Landroid/content/SharedPreferences$Editor;
    invoke-interface {{v5}}, Landroid/content/SharedPreferences$Editor;->commit()Z
{sqlite_block}
    invoke-virtual {{p0}}, Lcom/test/MainActivity;->helper()V
    new-instance v2, Landroid/widget/TextView;
    invoke-direct {{v2}}, Landroid/widget/TextView;-><init>()V
    const-string v3, "Parity Ready"
    invoke-virtual {{v2, v3}}, Landroid/widget/TextView;->setText(Ljava/lang/String;)V
    invoke-virtual {{p0, v2}}, Lcom/test/MainActivity;->setContentView(Landroid/view/View;)V
    return-void
.end method
"""
    (app_dir / "smali" / "com" / "test" / "MainActivity.smali").write_text(act_smali, encoding="utf-8")
    return act_smali


def _build_hybrid_web_app(app_dir):
    data_dir = app_dir / "assets" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dcloud_control.xml").write_text('<hbuilder version="1.9.9"><apps><app appid="H5TEST"/></apps></hbuilder>', encoding="utf-8")
    www = app_dir / "assets" / "apps" / "H5TEST" / "www"
    www.mkdir(parents=True, exist_ok=True)
    html = """<!doctype html>
<html>
<head><title>Smart Fridge Control</title></head>
<body>
  <h1>Control Panel</h1>
  <a href="https://api.example.org/help">Help Docs</a>
  <form action="https://api.example.org/v1/device/login" method="POST">
    <input name="email" type="text" />
  </form>
  <script>fetch('https://api.example.org/v1/fridge/status');</script>
</body>
</html>
"""
    (www / "index.html").write_text(html, encoding="utf-8")
    (app_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.hybrid.fridge">'
        '<application><activity android:name="io.dcloud.PandoraEntry" android:exported="true">'
        '<intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )


def test_catalog_server_tools_full_parity(tmp_path, monkeypatch):
    srv_src = open("mcp_server/server.py", encoding="utf-8").read()
    catalog_ids = [t.id for t in registry.TOOL_CATALOG]
    assert len(catalog_ids) >= 182
    assert len(catalog_ids) == len(set(catalog_ids))

    missing_srv = [tid for tid in catalog_ids if f'"{tid}"' not in srv_src]
    missing_fn = [tid for tid in catalog_ids if not callable(getattr(tools, tid.replace(".", "_"), None))]
    assert not missing_srv, f"In registry.py aber nicht in server.py: {missing_srv}"
    assert not missing_fn, f"In registry.py aber nicht in tools.py: {missing_fn}"

    # FastMCP server builds and exposes every single tool name
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)
    mcp = build_server(ctx, "127.0.0.1", 0)
    assert mcp is not None


def test_cold_native_ui_detect_hybrid_web(tmp_path):
    app_dir = tmp_path / "hw_app"
    _build_hybrid_web_app(app_dir)
    # Cold check in subprocess without prior producer imports
    code = (
        "import json, sys; sys.path.insert(0, '.'); "
        "from mcp_server import tools; "
        f"res = tools.native_ui_detect(None, studio_root={str(app_dir)!r}); "
        "print(json.dumps(res))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    assert res["ok"] is True
    assert res["runtime"] == "hybrid web"
    assert res["producer"] == "hybrid web"
    assert res["has_producer"] is True


def test_virtual_clock_and_glass_clock_tool(tmp_path, monkeypatch):
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)
    app_dir = tmp_path / "vc_app"
    _build_rich_smali_app(app_dir)

    c_set = tools.native_glass_clock(ctx, op="set", iso="2031-11-21 18:30:00", tz="UTC", frozen=True)
    assert c_set["ok"] is True
    assert c_set["clock"]["frozen"] is True
    assert c_set["queued_to_gui"] is True

    # Run inherits preset virtual clock automatically
    res = tools.native_glass_run(
        ctx, studio_root=str(app_dir), zone="original", entry="Lcom/test/MainActivity;",
        max_steps=50000, record_timeline=True,
    )
    assert res["ok"] is True, res.get("hinweis")
    assert res["virtual_clock"]["iso"] == "2031-11-21 18:30:00"
    assert res["virtual_clock"]["tz"] == "UTC"
    assert res["app_state"]["shared_prefs"]["prefs_main"]["boot_ms"] == res["virtual_clock"]["epoch_ms"]

    snap = tools.native_ui_snapshot(ctx)
    assert snap["ok"] is True
    assert "18:30" in snap["status_clock"]

    # Shift +2 hours updates live headless surface + live_state.json + status_clock
    c_shift = tools.native_glass_clock(ctx, op="shift", shift_hours=2)
    assert c_shift["ok"] is True
    assert "20:30:00" in c_shift["clock"]["iso"]
    snap2 = tools.native_ui_snapshot(ctx)
    assert "20:30" in snap2["status_clock"]

    c_get = tools.native_glass_clock(ctx, op="get")
    assert c_get["ok"] is True and c_get["source"] == "headless"

    c_rst = tools.native_glass_clock(ctx, op="reset")
    assert c_rst["ok"] is True and c_rst["clock"]["custom"] is False


def test_glass_control_headless_step_reverse_step_goto_and_controller(tmp_path, monkeypatch):
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)
    app_dir = tmp_path / "dbg_app"
    _build_rich_smali_app(app_dir)

    res = tools.native_glass_run(
        ctx, studio_root=str(app_dir), zone="original", entry="Lcom/test/MainActivity;",
        max_steps=50000, record_timeline=True,
    )
    assert res["ok"] is True
    assert res["timeline_count"] > 5

    # Goto step 5 -> forward step -> reverse_step -> back to 5
    r_goto = tools.native_glass_control(ctx, "goto", json.dumps({"step": 5}), target="headless")
    assert r_goto["ok"] is True and r_goto["headless"]["step"] == 5

    r_step = tools.native_glass_control(ctx, "step", target="headless")
    assert r_step["ok"] is True and r_step["headless"]["step"] == 6

    r_rev = tools.native_glass_control(ctx, "reverse_step", target="headless")
    assert r_rev["ok"] is True and r_rev["headless"]["step"] == 5

    r_mock = tools.native_glass_control(
        ctx, "add_net_mock",
        json.dumps({"url": "/ping", "code": 201, "body": '{"pong":1}', "method": "GET"}),
        target="headless",
    )
    assert r_mock["ok"] is True and "GET /ping" in str(r_mock["headless"])

    r_bef = tools.native_glass_control(ctx, "generate_befund", target="both")
    assert r_bef["ok"] is True and os.path.isfile(r_bef["headless"]["path"])

    # Controller methods + queue check
    ctrl = DebuggerController(app=None)
    clk_st = ctrl.set_virtual_clock_spec({"iso": "2030-01-02 09:45:00", "tz": "UTC", "frozen": True})
    assert clk_st["iso_str"] == "2030-01-02 09:45:00"
    ctrl.set_run_options(inspect_mode="glass_arm64", budget="500k", entry_stop=True, record_timeline=True, stop_on_exception=True)
    assert ctrl.native_fusion is True
    assert ctrl._parse_budget_steps() == 500_000
    assert ctrl.entry_stop is True and ctrl.record_timeline is True and ctrl.stop_on_exception is True

    # GUI queue file was written into tmp_path/data/gui_cmd_queue.json (isolated BASE_DIR)
    qpath = tmp_path / "data" / "gui_cmd_queue.json"
    assert qpath.is_file()
    queued = json.loads(qpath.read_text(encoding="utf-8"))
    assert any(it.get("cmd") == "generate_befund" for it in queued)


def test_glass_inspect_all_sections(tmp_path, monkeypatch):
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)
    app_dir = tmp_path / "insp_app"
    _build_rich_smali_app(app_dir)

    res = tools.native_glass_run(
        ctx, studio_root=str(app_dir), zone="original", entry="Lcom/test/MainActivity;",
        max_steps=50000, record_timeline=True,
    )
    assert res["ok"] is True

    i_sp = tools.native_glass_inspect(ctx, section="prefs")
    assert i_sp["ok"] is True and "prefs_main" in i_sp["shared_prefs"]

    i_sql = tools.native_glass_inspect(ctx, section="sqlite", query="SELECT id, title FROM items")
    assert i_sql["ok"] is True and "app.db" in i_sql["sqlite_dbs"]
    assert i_sql["sqlite_dbs"]["app.db"]["tables"]["items"] == 1
    assert i_sql["sqlite_dbs"]["app.db"]["query_rows"] == [{"id": 1, "title": "Milk"}]

    i_miss = tools.native_glass_inspect(ctx, section="missing", query="ping")
    assert i_miss["ok"] is True and i_miss["count"] >= 1

    i_tl = tools.native_glass_inspect(ctx, section="timeline", limit=10)
    assert i_tl["ok"] is True and i_tl["recorded_len"] > 0

    i_heap = tools.native_glass_inspect(ctx, section="heap")
    assert i_heap["ok"] is True and isinstance(i_heap["statics"], dict)

    i_bef = tools.native_glass_inspect(ctx, section="befund")
    assert i_bef["ok"] is True and "Glass-Debugger Befund" in i_bef["markdown"]


def test_restored_catalog_tools_app_import_callgraph_lib_import_logcat(tmp_path, monkeypatch):
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)

    # workspace.renew_id
    r_id = tools.workspace_renew_id(ctx)
    assert r_id["ok"] is True and r_id["patch_id"].startswith("PID-")
    assert os.path.isdir(r_id["archive_dir"])

    # app.import + app.list_local
    ext_app = tmp_path / "external_drop" / "my_pkg_dir"
    unp = ext_app / "base_unpacked_apktool"
    _build_rich_smali_app(unp, pkg="com.imported.demo")
    imp = tools.app_import(ctx, str(ext_app), switch_active=True)
    assert imp["ok"] is True and imp["package"] == "com.imported.demo"
    lst = tools.app_list_local(ctx)
    assert lst["ok"] is True and any(a["package"] == "com.imported.demo" and a["active"] for a in lst["apps"])

    # smali.callgraph
    cg = tools.smali_callgraph(ctx, symbol="onCreate", depth=2)
    assert cg["ok"] is True
    assert any("helper" in e["to"] for e in cg["edges"])
    cg_helper = tools.smali_callgraph(ctx, symbol="helper", depth=1)
    assert any("onCreate" in c["id"] for c in cg_helper["callers"])

    # lib.import_so + lib.set_active
    so_src = tmp_path / "libcustom.so"
    so_src.write_bytes(b"\x7fELF" + b"\x00" * 60)
    li = tools.lib_import_so(ctx, name="customhook", so_path=str(so_src), version="1.2", description="Prebuilt SO")
    assert li["ok"] is True and li["name"] == "customhook"
    la = tools.lib_set_active(ctx, "customhook", active=True)
    assert la["ok"] is True and la["active"] is True

    # logcat.capture (mocked ADB)
    def _fake_adb(cmd, cwd=".", timeout=15):
        ns = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pidof" in cmd:
            ns.stdout = "4321\n"
        elif "logcat" in cmd:
            ns.stdout = (
                "11-21 09:00:00.100  4321  4321 D DemoTag: Hello com.imported.demo\n"
                "11-21 09:00:01.200  9999  9999 I Other: Unrelated noise\n"
            )
        return ns

    monkeypatch.setattr(tools.CommandRunner, "run_blocking", staticmethod(_fake_adb))
    lc = tools.logcat_capture(ctx, lines=50, filter_str="Hello", package_only=True)
    assert lc["ok"] is True and lc["pid"] == "4321" and lc["count"] == 1
    assert "Hello com.imported.demo" in lc["lines"][0]


def test_new_studio_parity_tools_api_webview_manifest_fuzzy_gating(tmp_path, monkeypatch):
    ctx = _make_isolated_ctx(tmp_path, monkeypatch)

    # Hybrid-Web unpacked app + base.apk containing META-INF/CERT.RSA
    app_pkg_dir = tmp_path / "source" / "com.hybrid.fridge"
    unp = app_pkg_dir / "base_unpacked_apktool"
    _build_hybrid_web_app(unp)
    _build_rich_smali_app(unp, pkg="com.hybrid.fridge", include_sqlite=False)
    apk_path = app_pkg_dir / "base.apk"
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("META-INF/CERT.RSA", b"FAKE-PKCS7-CERT-BYTES")

    ctx.cfg.config["APP_PACKAGE"] = "com.hybrid.fridge"
    ctx.cfg.config["MANIFEST_STRATEGY"] = "apktool"
    ctx.cfg._update_paths()
    ctx.cfg.save()

    # api.extract_engine_static -> api.import_templates -> api.update -> api.clear (gated)
    ext = tools.api_extract_engine_static(ctx, app_root=str(unp), engine="auto")
    assert ext["ok"] is True and ext["runtime"] == "hybrid web" and ext["count"] >= 2
    ids = [ext["templates"][0]["id"], ext["templates"][1]["id"]]
    imp = tools.api_import_templates(ctx, json.dumps(ids), app_root=str(unp))
    assert imp["ok"] is True and imp["imported"] == 2

    q = tools.api_query(ctx, filter="", limit=10)
    rid = q["requests"][0]["id"]
    upd = tools.api_update(ctx, rid, comment="Audited endpoint", req_body='{"check":1}')
    assert upd["ok"] is True and upd["record"]["comment"] == "Audited endpoint"

    # api.clear requires delete_ops + confirm
    dec_denied = gating.check(ctx.cfg, "api.clear", confirm=False)
    assert dec_denied.allowed is False
    ctx.cfg.config["MCP_SETTINGS"]["tools"]["api.clear"] = True
    ctx.cfg.config["MCP_SETTINGS"]["caps"]["delete_ops"] = True
    dec_ok = gating.check(ctx.cfg, "api.clear", confirm=True)
    assert dec_ok.allowed is True
    clr = tools.api_clear(ctx)
    assert clr["ok"] is True and clr["cleared"] is True

    # native.webview_inspect: discover + parse local asset HTML
    wv_disc = tools.native_webview_inspect(ctx, url="", app_root=str(unp))
    assert wv_disc["ok"] is True and wv_disc["count"] >= 1
    wv_dom = tools.native_webview_inspect(ctx, url="assets/apps/H5TEST/www/index.html", app_root=str(unp))
    assert wv_dom["ok"] is True
    assert wv_dom["title"] == "Smart Fridge Control"
    assert len(wv_dom["forms"]) == 1 and "device/login" in wv_dom["forms"][0]["action"]

    # app.manifest_info
    man = tools.app_manifest_info(ctx, app_root=str(unp))
    assert man["ok"] is True
    assert man["version_name"] == "4.2.0"
    assert "android.permission.CAMERA" in man["permissions"]
    assert any(c["name"].endswith("SyncService") for c in man["exported_components"])
    assert len(man["apk_certs"]) == 1 and len(man["apk_certs"][0]["sha256"]) == 64

    # patch.fuzzy_match finds moved/register-shifted helper method
    moved_orig = """.method public movedRenamed()V
    .registers 5
    new-instance v4, Lcom/missing/MissingHelper;
    invoke-direct {v4}, Lcom/missing/MissingHelper;-><init>()V
    invoke-virtual {v4}, Lcom/missing/MissingHelper;->ping()V
    return-void
.end method"""
    fuz = tools.patch_fuzzy_match(ctx, orig=moved_orig, file="smali/com/test/OldFile.smali", deep_search=True, threshold=0.70)
    assert fuz["ok"] is True and fuz["count"] >= 1
    assert "helper()" in fuz["candidates"][0]["sig"]

    # app.start / app.stop gating + ADB execution
    assert gating.check(ctx.cfg, "app.start", confirm=False).allowed is False
    ctx.cfg.config["MCP_SETTINGS"]["tools"]["app.start"] = True
    ctx.cfg.config["MCP_SETTINGS"]["caps"]["app_start"] = True
    assert gating.check(ctx.cfg, "app.start", confirm=True).allowed is True

    cmds_seen = []

    def _rec_adb(cmd, cwd=".", timeout=15):
        cmds_seen.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr(tools.CommandRunner, "run_blocking", staticmethod(_rec_adb))
    r_start = tools.app_start(ctx, stop_first=True)
    r_stop = tools.app_stop(ctx)
    assert r_start["ok"] is True and r_stop["ok"] is True
    assert any("force-stop com.hybrid.fridge" in c for c in cmds_seen)


def test_mcp_settings_tab_labels_tooltips_filter_and_merge_safe_save(tmp_path):
    import tkinter as tk
    from core.infrastructure.config_manager import DEFAULT_CONFIG
    from ui.tabs.mcp_settings_tab import CAP_LABELS, MCPSettingsTab

    assert set(registry.CAP_KEYS) == set(DEFAULT_CONFIG["MCP_SETTINGS"]["caps"].keys())
    assert set(registry.CAP_KEYS) == set(CAP_LABELS.keys())

    ctx = AppContext()
    cfg_file = tmp_path / "config.json"
    ctx.cfg.config_file = str(cfg_file)
    ctx.cfg.config["BASE_DIR"] = str(tmp_path)
    ctx.cfg.config["APP_PACKAGE"] = "com.initial.pkg"
    ctx.cfg._update_paths()
    ctx.cfg.save()

    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tk display not available")

    try:
        app_stub = types.SimpleNamespace(cfg=ctx.cfg)
        tab = MCPSettingsTab(root, app_stub)

        # Alle 182 Tools + lokale Scroll-Bindung (kein globales bind_all)
        assert len(tab.tool_vars) == len(registry.TOOL_CATALOG)
        assert not tab._matrix_canvas.bind_all("<MouseWheel>")

        # Presets + Filter
        tab._apply_preset("all_g_on")
        assert tab.tool_vars["app.pull_apk"].get() is True
        assert tab.tool_vars["api.clear"].get() is True
        tab._apply_preset("defaults")
        assert tab.tool_vars["app.pull_apk"].get() is False
        assert tab.tool_vars["api.clear"].get() is False

        tab.filter_var.set("glass_clock")
        root.update_idletasks()
        assert tab._tool_cbs["native.glass_clock"].winfo_manager() == "pack"
        assert tab._tool_cbs["workspace.status"].winfo_manager() == ""
        tab.filter_var.set("")

        # Merge-schuetzendes Speichern erhaelt fremde Disk-Keys (z.B. APP_PACKAGE vom MCP-Server)
        on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        on_disk["APP_PACKAGE"] = "com.concurrent.mcp"
        cfg_file.write_text(json.dumps(on_disk, indent=4), encoding="utf-8")

        tab.opt_vars["audit_log_args"].set(False)
        tab.srv_vars["transport"].set("stdio")
        tab.tool_vars["app.pull_apk"].set(True)
        assert tab._persist_mcp_settings(quiet=True) is True

        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["APP_PACKAGE"] == "com.concurrent.mcp"
        assert saved["MCP_SETTINGS"]["audit"]["log_args"] is False
        assert saved["MCP_SETTINGS"]["server"]["transport"] == "stdio"
        assert saved["MCP_SETTINGS"]["tools"]["app.pull_apk"] is True
    finally:
        root.destroy()
