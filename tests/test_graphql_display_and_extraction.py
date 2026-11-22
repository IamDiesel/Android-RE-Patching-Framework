# -*- coding: utf-8 -*-
"""Regressionstests fuer GraphQL-Extraktion, In-Phone-UI-Darstellung & Netz-Formatierung."""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _make_synthetic_flutter_gql_app(root_dir, *, sibling_lib: bool = False):
    fa = root_dir / "assets" / "flutter_assets"
    (fa / "assets" / "translations").mkdir(parents=True, exist_ok=True)
    (fa / "assets" / "icon").mkdir(parents=True, exist_ok=True)
    (root_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.demo.pet">'
        '<application><activity android:name="com.demo.pet.MainActivity"/></application></manifest>',
        encoding="utf-8",
    )
    (fa / ".env.prod").write_text(
        "BASE_URL=https://xyz.appsync-api.eu-west-1.amazonaws.com/graphql\n"
        "API_KEY=da2-1234567890abcdefghijklmnop\n",
        encoding="utf-8",
    )
    # Blur-Teaser vs. scharfe Icons
    for fn in (
        "blur_activity_page.png",
        "blur_position_page.png",
        "blur_gauge_cat.png",
        "steps_counter_pet.png",
        "gps_banner.png",
        "battery_full.png",
        "kippy_main_logo.png",
    ):
        (fa / "assets" / "icon" / fn).write_bytes(b"\x89PNG\r\n\x1a\n")

    # libapp.so Strings: nested balanced GQL + Dart graphql_codegen + italienischer Prosa-Noise
    so_blob = (
        b"\x7fELF\x02\x01\x01\x00"
        b"subscription non\x00subscription entro\x00subscription per\x00subscription stop\x00"
        b"query GetWorkouts($id: ID!) { getWorkouts(id: $id) { items { id duration } } }\x00"
        b"Query$getActivities$getActivities$activityReport$calories\x00"
        b"Query$getActivities$getActivities$activityReport$steps\x00"
        b"Query$getPets$getPets$pets$image\x00"
        b"Query$getPetlinkGps$getPetlinkGps$petlinkGps$lastKnownPosition\x00"
        b"Query$getUser$getUser$user$settings\x00"
    )
    if sibling_lib:
        sib_lib = root_dir.parent / "base_unpacked_apktool" / "lib" / "arm64-v8a"
        sib_lib.mkdir(parents=True, exist_ok=True)
        (sib_lib / "libapp.so").write_bytes(so_blob)
    else:
        lib_dir = root_dir / "lib" / "arm64-v8a"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "libapp.so").write_bytes(so_blob)


def test_graphql_balanced_and_dart_codegen_extraction_and_kippy_real(tmp_path):
    from core.java.engine_deep_g12 import analyze_flutter_libapp_deep
    from core.java.flutter_app_producer import extract_flutter_app_spec
    from services.flutter_api_extractor_service import FlutterApiExtractorService

    app_base = tmp_path / "app_pkg" / "base_unpacked"
    app_base.mkdir(parents=True)
    _make_synthetic_flutter_gql_app(app_base, sibling_lib=True)

    # Geschwister-libapp.so wird auch aus base_unpacked heraus gefunden
    scan = FlutterApiExtractorService.extract_from_app_root(str(app_base))
    gql_tpls = [t for t in scan["templates"] if t["category"] == "GraphQL"]
    op_names = set()
    bodies_by_op = {}
    for tpl in gql_tpls:
        obj = json.loads(tpl["body"])
        op = obj.get("operationName") or ""
        op_names.add(op)
        bodies_by_op[op] = obj.get("query") or ""

    assert {"GetWorkouts", "getActivities", "getPets", "getPetlinkGps", "getUser"}.issubset(op_names)
    assert "\n" in bodies_by_op["GetWorkouts"]
    assert "items {" in bodies_by_op["GetWorkouts"]
    assert "activityReport {" in bodies_by_op["getActivities"]
    assert "calories {" in bodies_by_op["getActivities"]

    # Deep-Analyzer filtert "subscription non"/"stop" heraus, behaelt echte IDs + codegen
    sib_so = app_base.parent / "base_unpacked_apktool" / "lib" / "arm64-v8a" / "libapp.so"
    deep = analyze_flutter_libapp_deep(str(sib_so))
    gql_list = deep["graphql_queries"]
    assert "query GetWorkouts" in gql_list
    assert "query getActivities" in gql_list
    assert not any(x in gql_list for x in ("subscription non", "subscription entro", "subscription per", "subscription stop"))

    # Blur-Bilder werden zugunsten scharfer Assets gemieden
    spec = extract_flutter_app_spec(str(app_base))
    assert "blur_" not in os.path.basename(spec["images"]["activity"]).lower()
    assert "blur_" not in os.path.basename(spec["images"]["position"]).lower()
    assert "blur_" not in os.path.basename(spec["images"]["health"]).lower()

    # Optionale Real-APK-Pruefung auf Kippy im Workspace
    kippy_root = os.path.join(_ROOT, "source", "com.datamars.kippynew", "base_unpacked_apktool")
    if os.path.isdir(kippy_root):
        kspec = extract_flutter_app_spec(kippy_root)
        assert "blur_" not in os.path.basename(kspec["images"]["activity"]).lower()
        assert "blur_" not in os.path.basename(kspec["images"]["position"]).lower()
        assert "blur_" not in os.path.basename(kspec["images"]["health"]).lower()
        kgql = kspec["graphql_templates"]
        assert len(kgql) >= 10
        kops = {json.loads(t["body"]).get("operationName") for t in kgql}
        assert "getActivities" in kops and "getPets" in kops


def test_flutter_in_phone_honest_graphql_status_and_tab_ops(tmp_path):
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.flutter_app_producer import mount_flutter_app_into_tree
    from core.java.net_recorder import NetLog, NetResponse, install_net_stubs
    from core.java.view_tree import install_view_stubs

    app_dir = tmp_path / "flutter_gql"
    app_dir.mkdir(parents=True)
    _make_synthetic_flutter_gql_app(app_dir, sibling_lib=False)

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs, app_root=str(app_dir))
    net_log = NetLog()

    mode = {"kind": "mock_default"}

    def _responder(req):
        if mode["kind"] == "mock_default":
            return NetResponse(200, {"X-Glass-Source": "mock-default (0B)"}, b"", "OK")
        if mode["kind"] == "gql_error":
            return NetResponse(
                200,
                {"Content-Type": "application/json", "X-Glass-Source": "live-http"},
                json.dumps({"errors": [{"message": "Unauthorized"}]}).encode("utf-8"),
                "OK",
            )
        return NetResponse(
            200,
            {"Content-Type": "application/json", "X-Glass-Source": "live-http"},
            json.dumps({"data": {"getActivities": {"steps": 8420}}}).encode("utf-8"),
            "OK",
        )

    install_net_stubs(stubs, responder=_responder, net_log=net_log)
    ctrl = mount_flutter_app_into_tree(tree, str(app_dir), stubs=stubs)
    ctrl.enter_demo()

    # 1) Offline-Mock (0 B) darf KEIN falsches haekchen-OK anzeigen
    assert tree.root.find("home_gql_card") is not None
    assert tree.root.find("home_gql_query") is not None
    status_node = tree.root.find("home_gql_status")
    assert status_node is not None
    assert "Offline-Mock" in status_node.text and "✅" not in status_node.text

    # 2) Tab-Wechsel zu AKTIVITAET waehlt getActivities & echte Daten zeigen ✅ + Nutzdaten
    tree.root.find("nav_tab_2").on_click(None)
    assert "getActivities" in (tree.root.find("home_gql_query").text or "")
    mode["kind"] = "gql_data"
    tree.root.find("btn_home_sync").on_click(None)
    assert "✅" in tree.root.find("home_gql_status").text
    assert "8420" in tree.root.find("home_gql_preview").text

    # 3) GraphQL-Fehler-Antwort ueber Chip-Button klar als Fehler anzeigen
    mode["kind"] = "gql_error"
    tree.root.find("gql_op_btn_0").on_click(None)
    assert "❌ GraphQL-Fehler" in tree.root.find("home_gql_status").text
    assert "Unauthorized" in tree.root.find("home_gql_preview").text


def test_net_unified_graphql_label_and_details_formatting():
    from core.java.net_recorder import NetLog, NetRequest, NetResponse
    from core.java.net_unified import format_net_entry_details, normalize_java

    log = NetLog()
    req_gql = NetRequest(
        "POST",
        "https://xyz.appsync-api.eu-west-1.amazonaws.com/graphql",
        {"Content-Type": "application/json", "x-api-key": "da2-test"},
        json.dumps({
            "operationName": "getActivities",
            "query": "query getActivities($petId: ID!) { getActivities(petId: $petId) { steps calories } }",
            "variables": {"petId": "pet-42"},
        }).encode("utf-8"),
    )
    resp_gql = NetResponse(
        200,
        {"Content-Type": "application/json", "X-Glass-Source": "live-http"},
        json.dumps({"data": {"getActivities": {"steps": 1200}}}).encode("utf-8"),
        "OK",
    )
    req_rest = NetRequest("GET", "https://api.example.com/v1/status", {}, b"")
    resp_rest = NetResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}', "OK")

    log.record(req_gql, resp_gql)
    log.record(req_rest, resp_rest)

    rows = normalize_java(log)
    assert "[GQL: getActivities]" in rows[0]["label"]
    assert rows[1]["label"] == "GET https://api.example.com/v1/status  ->  200 (11B)"

    details = format_net_entry_details(rows[0], net_mode="live")
    assert "=== GRAPHQL ===" in details
    assert "Operation: query getActivities" in details
    assert "steps" in details and "calories" in details
    assert '"petId": "pet-42"' in details
    assert "GraphQL-Antwort: data (getActivities)" in details
