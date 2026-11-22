# -*- coding: utf-8 -*-
"""Regressions- & End-to-End-Tests fuer AtomicReferenceArray/Unsafe-CAS, java.time-Pattern-Parsing,
ConstraintLayout-Ratio-Anker sowie Virtuagym-Custom-Home-Screen & GlucoBridge-Login/Graph (2026-11)."""
from __future__ import annotations

import datetime
import json
import os
import struct
import sys
import zlib

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.dalvik import DalvikVM  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.glass_runner import GlassAppRunner, InstanceState  # noqa: E402
from core.java.lifecycle import _StubRuntime  # noqa: E402
from core.java.net_recorder import NetResponse  # noqa: E402
from core.java.objects import JObject, JString  # noqa: E402
from core.java.seam_config import SeamConfig  # noqa: E402
from core.java.surface import SceneGraphSurface  # noqa: E402
from core.java.view_tree import ViewNode, ViewTree, install_view_stubs, paint_commands  # noqa: E402
from core.java.virtual_clock import format_java_pattern, seed_calendar_statics  # noqa: E402


_VG_UNPACKED = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")
_GB_UNPACKED = os.path.join(_ROOT, "source", "de.glucobridge.app", "base_unpacked_apktool")


def _invoke_hook_or_stub(vm, cls: str, name: str, sig: str, args, static: bool):
    for hk in list(vm._method_hooks):
        handled, ret = hk(cls, cls, name, sig, list(args), static, vm)
        if handled:
            return ret
    ok, ret = vm.runtime.resolve_invoke(cls, name, sig, list(args), static)
    assert ok, f"Missing stub/hook: {cls}->{name}{sig}"
    return ret


def _make_png(w: int = 16, h: int = 16, rgba=(30, 40, 55, 255)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _isolated_runner(app_root: str, pkg: str, tmp_path) -> GlassAppRunner:
    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    inst = InstanceState(
        instance_id=f"test_{pkg}",
        package=pkg,
        app_root=str(app_root),
        zone="original",
        appdata_root=str(tmp_path / "appdata"),
        selfcache_root=str(tmp_path / "selfcache"),
    )
    return GlassAppRunner(str(app_root), zone="original", seam_config=sc, instance_state=inst)


# ---------------------------------------------------------------------------
# 1. Unit- & Engine-Regressions-Tests (ohne entpacktes APK lauffaehig)
# ---------------------------------------------------------------------------
def test_atomic_reference_array_and_unsafe_dalvik_null_zero_cas():
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    ara_cls = "Ljava/util/concurrent/atomic/AtomicReferenceArray;"
    ara = JObject(ara_cls, {})
    _invoke_hook_or_stub(vm, ara_cls, "<init>", "(I)V", [ara, 4], False)

    item = JObject("Ljava/lang/Object;", {"tag": "seg0"})
    # Dalvik uebergibt fuer null oft 0: CAS(0, 0, item) muss gegen initial None treffen
    assert _invoke_hook_or_stub(
        vm, ara_cls, "compareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", [ara, 0, 0, item], False
    ) == 1
    assert _invoke_hook_or_stub(vm, ara_cls, "get", "(I)Ljava/lang/Object;", [ara, 0], False) is item
    # Nicht-null gegen expected=0 schlaegt fehl
    assert _invoke_hook_or_stub(
        vm, ara_cls, "compareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", [ara, 0, 0, item], False
    ) == 0
    # Zuruecksetzen auf Dalvik-null 0 normalisiert auf None
    assert _invoke_hook_or_stub(
        vm, ara_cls, "compareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", [ara, 0, item, 0], False
    ) == 1
    assert _invoke_hook_or_stub(vm, ara_cls, "get", "(I)Ljava/lang/Object;", [ara, 0], False) is None

    u_cls = "Lsun/misc/Unsafe;"
    unsafe_obj = _invoke_hook_or_stub(vm, u_cls, "getUnsafe", f"(){u_cls}", [], True)
    holder = JObject("Ljava/lang/Object;", {})
    assert _invoke_hook_or_stub(
        vm, u_cls, "compareAndSwapObject", "(Ljava/lang/Object;JLjava/lang/Object;Ljava/lang/Object;)Z",
        [unsafe_obj, holder, 16, 0, item], False,
    ) == 1
    assert _invoke_hook_or_stub(
        vm, u_cls, "compareAndSwapObject", "(Ljava/lang/Object;JLjava/lang/Object;Ljava/lang/Object;)Z",
        [unsafe_obj, holder, 16, item, 0], False,
    ) == 1
    assert _invoke_hook_or_stub(
        vm, u_cls, "compareAndSwapInt", "(Ljava/lang/Object;JII)Z", [unsafe_obj, holder, 32, 0, 7], False
    ) == 1
    assert _invoke_hook_or_stub(
        vm, u_cls, "getInt", "(Ljava/lang/Object;J)I", [unsafe_obj, holder, 32], False
    ) == 7


def test_java_util_stack_shares_collection_backing_items():
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    stk_cls = "Ljava/util/Stack;"
    stk = JObject(stk_cls, {})
    _invoke_hook_or_stub(vm, stk_cls, "<init>", "()V", [stk], False)

    assert _invoke_hook_or_stub(
        vm, "Ljava/util/AbstractCollection;", "add", "(Ljava/lang/Object;)Z", [stk, JString("SELECT name")], False
    ) == 1
    assert _invoke_hook_or_stub(
        vm, "Ljava/util/AbstractCollection;", "add", "(Ljava/lang/Object;)Z", [stk, JString(", ")], False
    ) == 1

    popped = _invoke_hook_or_stub(vm, stk_cls, "pop", "()Ljava/lang/Object;", [stk], False)
    assert isinstance(popped, JString) and popped.text == ", "

    _invoke_hook_or_stub(
        vm, stk_cls, "push", "(Ljava/lang/Object;)Ljava/lang/Object;", [stk, JString(" FROM club_feature")], False
    )
    assert _invoke_hook_or_stub(
        vm, stk_cls, "search", "(Ljava/lang/Object;)I", [stk, JString("SELECT name")], False
    ) == 2

    it = _invoke_hook_or_stub(vm, stk_cls, "iterator", "()Ljava/util/Iterator;", [stk], False)
    parts = []
    while _invoke_hook_or_stub(vm, "Ljava/util/Iterator;", "hasNext", "()Z", [it], False):
        el = _invoke_hook_or_stub(vm, "Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [it], False)
        parts.append(el.text if isinstance(el, JString) else str(el))
    assert "".join(parts) == "SELECT name FROM club_feature"


def test_java_time_pattern_am_pm_and_ldt_to_instant_utc():
    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    seed_calendar_statics(vm)
    vm.runtime = _StubRuntime(stubs)

    loc_us = vm.statics["Ljava/util/Locale;"]["US"]
    zo_utc = vm.statics["Ljava/time/ZoneOffset;"]["UTC"]
    fmt = _invoke_hook_or_stub(
        vm,
        "Ljava/time/format/DateTimeFormatter;",
        "ofPattern",
        "(Ljava/lang/String;Ljava/util/Locale;)Ljava/time/format/DateTimeFormatter;",
        [JString("M/d/yyyy h:mm:ss a"), loc_us],
        True,
    )

    samples = [
        ("11/21/2026 10:25:00 PM", datetime.datetime(2026, 11, 21, 22, 25, 0, tzinfo=datetime.timezone.utc)),
        ("11/5/2026 9:05:07 AM", datetime.datetime(2026, 11, 5, 9, 5, 7, tzinfo=datetime.timezone.utc)),
        ("11/5/2026 12:05:00 AM", datetime.datetime(2026, 11, 5, 0, 5, 0, tzinfo=datetime.timezone.utc)),
        ("11/5/2026 12:05:00 PM", datetime.datetime(2026, 11, 5, 12, 5, 0, tzinfo=datetime.timezone.utc)),
    ]
    for ts_str, exp_dt in samples:
        ldt = _invoke_hook_or_stub(
            vm,
            "Ljava/time/LocalDateTime;",
            "parse",
            "(Ljava/lang/CharSequence;Ljava/time/format/DateTimeFormatter;)Ljava/time/LocalDateTime;",
            [JString(ts_str), fmt],
            True,
        )
        inst = _invoke_hook_or_stub(
            vm,
            "Ljava/time/LocalDateTime;",
            "toInstant",
            "(Ljava/time/ZoneOffset;)Ljava/time/Instant;",
            [ldt, zo_utc],
            False,
        )
        epoch_ms = _invoke_hook_or_stub(vm, "Ljava/time/Instant;", "toEpochMilli", "()J", [inst], False)
        assert epoch_ms == int(exp_dt.timestamp() * 1000)


def test_constraintlayout_ratio_anchor_not_hijacked_by_grid_forced_height():
    tree = ViewTree()
    root = ViewNode("LinearLayout", vid="root")
    rv = root.add(
        ViewNode(
            "RecyclerView",
            vid="list",
            props={
                "span_count": 3,
                "$span_fn": lambda pos: 3 if pos == 0 else 1,
            },
        )
    )
    # Header mit unsichtbarem 5:3-Anker & darauf referenziertem club_logo
    hdr = rv.add(ViewNode("ConstraintLayout", vid="hdr_item", props={"span_size": 3}))
    hdr.add(
        ViewNode(
            "ImageView",
            vid="header_background_image",
            visible=False,
            props={
                "xml_w_mode": "zero",
                "xml_h_mode": "zero",
                "$cl_top_parent": True,
                "$cl_start_parent": True,
                "$cl_end_parent": True,
                "$cl_ratio": "5:3",
            },
        )
    )
    logo = hdr.add(
        ViewNode(
            "ImageView",
            vid="club_logo",
            visible=True,
            props={
                "xml_w_mode": "zero",
                "xml_h_mode": "zero",
                "$cl_same_v_target": "header_background_image",
                "$cl_start_to_start": "header_background_image",
                "$cl_end_to_end": "header_background_image",
            },
        )
    )
    touch_views = []
    labels = []
    for i in range(6):
        tile = rv.add(ViewNode("ConstraintLayout", vid=f"tile_{i}"))
        tile.add(
            ViewNode(
                "View",
                vid="tile_size",
                props={
                    "xml_w_mode": "zero",
                    "xml_h_mode": "zero",
                    "$cl_top_parent": True,
                    "$cl_bot_parent": True,
                    "$cl_start_parent": True,
                    "$cl_end_parent": True,
                    "$cl_ratio": "1:1",
                },
            )
        )
        tile.add(
            ViewNode(
                "ImageView",
                vid="tile_icon",
                props={
                    "xml_w_mode": "zero",
                    "xml_h_mode": "zero",
                    "$cl_top_to_top": "tile_size",
                    "$cl_bot_to_bot": "tile_size",
                    "$cl_start_to_start": "tile_size",
                    "$cl_end_to_end": "tile_size",
                },
            )
        )
        lbl = tile.add(
            ViewNode(
                "TextView",
                vid="tile_label",
                text=f"Tile {i+1}",
                props={
                    "xml_w_mode": "zero",
                    "xml_h": 48,
                    "$cl_start_to_start": "tile_size",
                    "$cl_end_to_end": "tile_size",
                },
            )
        )
        tv = tile.add(
            ViewNode(
                "View",
                vid="touch_view",
                clickable=True,
                props={
                    "xml_w_mode": "zero",
                    "xml_h_mode": "zero",
                    "$cl_top_to_top": "tile_size",
                    "$cl_bot_to_bot": "tile_size",
                    "$cl_start_to_start": "tile_size",
                    "$cl_end_to_end": "tile_size",
                },
            )
        )
        labels.append(lbl)
        touch_views.append(tv)

    tree.set_root(root)
    paint_commands(tree)

    assert hdr.bounds[2] >= 340 and hdr.bounds[3] >= 180
    assert logo.bounds[2] >= 200 and logo.bounds[3] >= 100
    assert len({lb.bounds[0] for lb in labels[:3]}) == 3
    for tv in touch_views:
        assert tv.bounds[2] >= 90 and tv.bounds[3] >= 90
        assert abs(tv.bounds[2] - tv.bounds[3]) <= 4


def test_view_stubs_do_not_hijack_cursor_or_jsonarray_get_string(tmp_path):
    from core.java.sqlite_prefs_bridge import _make_cursor

    val_dir = tmp_path / "res" / "values"
    val_dir.mkdir(parents=True)
    (val_dir / "public.xml").write_text(
        '<resources><public type="string" name="app_name" id="0x7f110001"/></resources>',
        encoding="utf-8",
    )
    (val_dir / "strings.xml").write_text(
        '<resources><string name="app_name">DemoApp</string></resources>',
        encoding="utf-8",
    )

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    install_view_stubs(stubs, app_root=str(tmp_path))

    res_obj = JObject("Landroid/content/res/Resources;", {})
    res_str = _invoke_hook_or_stub(
        vm, "Landroid/content/res/Resources;", "getString", "(I)Ljava/lang/String;", [res_obj, 0x7F110001], False
    )
    assert res_str.text == "DemoApp"

    cur = _make_cursor([("Tile 1",)], ["home_screen_label"])
    assert _invoke_hook_or_stub(vm, "Landroid/database/Cursor;", "moveToFirst", "()Z", [cur], False) == 1
    cur_str = _invoke_hook_or_stub(
        vm, "Landroid/database/Cursor;", "getString", "(I)Ljava/lang/String;", [cur, 0], False
    )
    assert cur_str.text == "Tile 1"

    jarr = JObject("Lorg/json/JSONArray;", {})
    _invoke_hook_or_stub(vm, "Lorg/json/JSONArray;", "<init>", "(Ljava/lang/String;)V", [jarr, JString('["Tile 1"]')], False)
    jarr_str = _invoke_hook_or_stub(
        vm, "Lorg/json/JSONArray;", "getString", "(I)Ljava/lang/String;", [jarr, 0], False
    )
    assert jarr_str.text == "Tile 1"


# ---------------------------------------------------------------------------
# 2. Virtuagym: Club-Sync (v0/club + mobile_app/settings) & Custom Home Screen
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_club_sync_and_custom_home_screen_layout(tmp_path):
    png_bg = _make_png(32, 32, (24, 28, 36, 255))
    png_icon = _make_png(24, 24, (255, 255, 255, 255))

    club_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "timestamp": 1760000000,
        "result": [
            {
                "id": 77001,
                "name": "Synthetic Gym",
                "color": "181C24",
                "accent_color": "E53935",
                "gradient_light": "222222",
                "gradient_dark": "111111",
                "logo": "https://cdn.example.test/img/logo.png",
                "print_logo": "https://cdn.example.test/img/logo.png",
                "background": "https://cdn.example.test/img/bg.png",
                "features": {
                    "enable_custom_homescreen": True,
                    "enable_clubfinder": True,
                    "enable_community": True,
                    "enable_activity_calendar": True,
                },
            }
        ],
    }

    settings_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "result": [
            {
                "club_id": 77001,
                "custom_homescreen_settings": {
                    "items_per_row": 3,
                    "header_background_type": "NONE",
                    "use_background_image": True,
                    "background_color": "181C24",
                    "background_image": "bg.png",
                    "background_overlay_color": "000000",
                    "background_overlay_opacity": 0.55,
                    "horizontal_text_alignment": "center",
                    "text_shadow_enabled": True,
                    "homescreen_items_shape": "square",
                    "homescreen_tile_border_color": "00000000",
                },
                "navigation_items": [
                    {
                        "navigation_item_id": 100 + i,
                        "home_screen_visible": True,
                        "home_screen_label": f"Tile {i + 1}",
                        "home_screen_order": i + 1,
                        "home_screen_picture_android": f"icon_{i + 1}.png",
                        "item_background_color": "00000000",
                        "pro_only": 0,
                        "android_link": f"virtuagym://app.virtuagym.com/fitness/tile{i + 1}",
                        "deleted": 0,
                    }
                    for i in range(12)
                ],
            }
        ],
    }

    seen_urls = []

    def responder(req):
        url = getattr(req, "url", "") or ""
        seen_urls.append(url)
        if url.endswith(".png") or "img/" in url or "/thumb/" in url:
            blob = png_bg if ("bg" in url or "logo" in url) else png_icon
            return NetResponse(200, {"Content-Type": "image/png"}, blob, "OK")
        if "user/current" in url:
            body = {
                "statuscode": 200,
                "statusmessage": "OK",
                "result_count": 1,
                "timestamp": 1760000000,
                "result": {
                    "id": 99001,
                    "username": "demouser",
                    "email": "demo@example.com",
                    "first_name": "Demo",
                    "last_name": "Member",
                    "gender": "m",
                    "club_ids": [77001, 77002],
                    "member_ids": [
                        {"club_id": 77001, "superclub_id": 0, "member_id": 88001, "member_pro": 1}
                    ],
                    "pro": 1,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "mobile_app/settings" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(settings_payload).encode("utf-8"), "OK")
        if "v0/club" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(club_payload).encode("utf-8"), "OK")
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[]}', "OK")

    gr = _isolated_runner(_VG_UNPACKED, "digifit.virtuagym.client.android", tmp_path)
    gr.build(net_responder=responder, native_fusion=False)
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;",
        max_steps=400_000,
        max_wall_s=60.0,
        native_fusion=False,
    )
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf.paint()

    open_btn = gr.tree.root.find("log_in_text_button")
    assert open_btn is not None
    bx, by, bw, bh = open_btn.bounds
    gr.stubs._clock_ms = 1760000002000
    gr.vm.steps = 0
    gr.vm.max_steps = 400_000
    gr.vm.step_budget = 400_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()
    surf.paint()

    for vid, val in (("email", "demo@example.com"), ("password", "syn-pass-123")):
        nd = gr.tree.root.find(vid)
        assert nd is not None
        nd.text = val
        for sub in nd.walk():
            if sub.cls == "EditText":
                sub.text = val

    sub_btn = gr.tree.root.find("login_button")
    assert sub_btn is not None
    bx, by, bw, bh = sub_btn.bounds
    gr.stubs._clock_ms += 2000
    gr.vm.steps = 0
    gr.vm.max_steps = 2_000_000
    gr.vm.step_budget = 2_000_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()

    assert any("v0/club" in u for u in seen_urls)
    assert any("v1/club/mine/mobile_app/settings" in u for u in seen_urls)

    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;",
        max_steps=1_200_000,
        max_wall_s=90.0,
        native_fusion=False,
    )
    pcmds = SceneGraphSurface(gr.tree, vm=gr.vm).paint()
    vis = gr.tree.visible_nodes()

    bg = gr.tree.root.find("screen_bg_image")
    assert bg is not None and bg.visible
    assert bg.bounds[2] >= 360 and bg.bounds[3] >= 500
    assert bool(bg.props.get("img_bytes"))

    ov = gr.tree.root.find("bg_overlay")
    assert ov is not None and ov.visible
    scrim_imgs = [c for c in pcmds if c.get("op") == "image" and c.get("w", 0) >= 360 and c.get("h", 0) >= 500]
    assert len(scrim_imgs) >= 2

    logo = gr.tree.root.find("club_logo")
    assert logo is not None and logo.visible
    assert logo.bounds[2] >= 180 and logo.bounds[3] >= 80
    assert bool(logo.props.get("img_bytes"))

    sw = gr.tree.root.find("switch_club")
    assert sw is not None and sw.visible
    assert sw.bounds[1] < 90 and sw.bounds[0] > 260

    tile_labels = [n for n in vis if n.vid == "tile_label"]
    assert [n.text for n in tile_labels] == [f"Tile {i + 1}" for i in range(12)]
    assert len({n.bounds[0] for n in tile_labels}) == 3

    tile_icons = [n for n in vis if n.vid == "tile_icon"]
    assert len(tile_icons) == 12
    assert all(bool(ic.props.get("img_bytes")) for ic in tile_icons)

    touch_views = [n for n in vis if n.vid == "touch_view"]
    assert len(touch_views) == 12
    for tv in touch_views:
        assert tv.clickable
        assert tv.bounds[2] >= 90 and tv.bounds[3] >= 90


# ---------------------------------------------------------------------------
# 3. GlucoBridge: Cold Start Light Mode -> Login -> Graph ComposeCanvas
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(_GB_UNPACKED), reason="Entpackte GlucoBridge-App nicht vorhanden")
def test_glucobridge_login_and_glucose_chart_canvas(tmp_path):
    gr = _isolated_runner(_GB_UNPACKED, "de.glucobridge.app", tmp_path)

    def responder(req):
        url = getattr(req, "url", "") or ""
        now_ms = int(gr.stubs.current_time_ms(advance=False))
        now_dt = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=datetime.timezone.utc).replace(tzinfo=None)
        if "/llu/auth/login" in url:
            body = {
                "status": 0,
                "data": {
                    "authTicket": {"token": "syn-tok-1", "expires": 1999999999, "duration": 86400},
                    "user": {"id": "syn-user-1", "country": "DE"},
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if url.endswith("/llu/connections"):
            body = {
                "status": 0,
                "data": [
                    {
                        "patientId": "syn-pat-1",
                        "firstName": "Demo",
                        "lastName": "User",
                        "glucoseMeasurement": {
                            "Timestamp": format_java_pattern(now_dt, "M/d/yyyy h:mm:ss a"),
                            "ValueInMgPerDl": 118,
                            "TrendArrow": 3,
                        },
                    }
                ],
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "/graph" in url:
            vals = [102, 108, 115, 124, 121, 118]
            graph_data = []
            for idx, v in enumerate(vals):
                dt_pt = now_dt - datetime.timedelta(minutes=10 * (len(vals) - 1 - idx))
                graph_data.append(
                    {
                        "Timestamp": format_java_pattern(dt_pt, "M/d/yyyy h:mm:ss a"),
                        "ValueInMgPerDl": v,
                        "TrendArrow": 3,
                    }
                )
            body = {
                "status": 0,
                "data": {
                    "connection": {
                        "patientId": "syn-pat-1",
                        "glucoseMeasurement": {
                            "Timestamp": format_java_pattern(now_dt, "M/d/yyyy h:mm:ss a"),
                            "ValueInMgPerDl": 118,
                            "TrendArrow": 3,
                        },
                    },
                    "graphData": graph_data,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        return NetResponse(404, {}, b"{}", "Not Found")

    gr.build(net_responder=responder, native_fusion=False)
    gr.run(max_steps=400_000, max_wall_s=60.0, native_fusion=False)

    assert gr.tree.dark_mode is False
    vis0 = gr.tree.visible_nodes()
    texts0 = [n.text.strip() for n in vis0 if (n.text or "").strip()]
    assert "Anmelden" in texts0 and "Login" in texts0
    assert "⚙" not in texts0

    edits0 = [n for n in vis0 if n.cls == "EditText"]
    assert len(edits0) == 2
    edits0[0].props["$on_change_fn"]("demo@example.com")

    vis1 = gr.tree.visible_nodes()
    edits1 = [n for n in vis1 if n.cls == "EditText"]
    assert edits1[0].text == "demo@example.com"
    edits1[1].props["$on_change_fn"]("syn-pass-123")

    vis2 = gr.tree.visible_nodes()
    login_btn = next(n for n in vis2 if n.clickable and (n.text or "").strip() == "Login")
    gr.vm.steps = 0
    gr.vm.max_steps = 800_000
    gr.vm.step_budget = 800_000
    login_btn.on_click()

    vis3 = gr.tree.visible_nodes()
    texts3 = [n.text.strip() for n in vis3 if (n.text or "").strip()]
    assert "GlucoBridge" in texts3
    assert "⚙" in texts3
    assert "118" in texts3
    assert "→" in texts3
    assert "Aktualisieren" in texts3
    assert "Noch kein Verlauf" not in texts3

    cvs = [n for n in vis3 if n.cls == "ComposeCanvas"]
    assert len(cvs) == 1
    assert cvs[0].bounds[2] >= 250 and cvs[0].bounds[3] >= 150
    crec = cvs[0].props.get("_compose_canvas_rec")
    assert crec is not None
    raw_cmds = crec.paint()
    assert sum(1 for c in raw_cmds if c.get("op") == "rect") >= 1
    assert sum(1 for c in raw_cmds if c.get("op") == "line") >= 10
    assert sum(1 for c in raw_cmds if c.get("op") == "text") >= 4

    pcmds = SceneGraphSurface(gr.tree, vm=gr.vm).paint()
    assert sum(1 for c in pcmds if c.get("op") == "line") >= 10
    assert any(c.get("op") == "rect" for c in pcmds)
    assert any(c.get("op") == "text" and c.get("text") in ("100", "150") for c in pcmds)


def test_compose_box_overlap_and_column_tail_spacer_grouping():
    root = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "$compose_root": True, "$fill_h": True})
    root_col = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "Column", "$fill_max_h": True})

    top_col = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "Column", "layout_weight": 1.0})
    top_col.add(ViewNode(cls="TextView", text="Allow us to help you reach your goal"))
    top_col.add(ViewNode(cls="TextView", text="Based on your plan we will provide you with tips, reminders and rewards"))

    mid_box = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "Box", "layout_weight": 1.0})
    canvas = ViewNode(cls="ComposeCanvas", props={"$fill_max_h": True, "h": 220})
    card = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "role": "card"})
    card.add(ViewNode(cls="TextView", text="Virtuagym now"))
    card.add(ViewNode(cls="TextView", text="Your new workout plan is ready!"))
    mid_box.add(canvas)
    mid_box.add(card)

    bot_col = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "Column", "layout_weight": 1.0})
    allow_btn = ViewNode(cls="Button", text="Allow", clickable=True)
    spacer = ViewNode(cls="View", props={"$compose_spacer": True, "h": 12})
    maybe_lbl = ViewNode(cls="TextView", text="Maybe later", clickable=True)
    bot_col.add(allow_btn)
    bot_col.add(spacer)
    bot_col.add(maybe_lbl)

    root_col.add(top_col)
    root_col.add(mid_box)
    root_col.add(bot_col)
    root.add(root_col)

    tree = ViewTree(width=420, height=760)
    tree.set_root(root)

    # Box-Kinder überlappen statt vertikal gestapelt zu werden
    _, cy0, _, ch0 = canvas.bounds
    _, ky0, _, kh0 = card.bounds
    assert ky0 >= cy0 and ky0 + kh0 <= cy0 + ch0

    # Trailing Buttons + $compose_spacer bleiben im Viewport eng beieinander
    _, ay, _, ah = allow_btn.bounds
    _, my, _, mh = maybe_lbl.bounds
    assert 0 <= my < my + mh <= 760
    assert 0 <= my - (ay + ah) <= 30


@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Entpackte Virtuagym-App nicht vorhanden")
def test_virtuagym_notification_permission_maybe_later_visible_and_clickable(tmp_path):
    gr = _isolated_runner(_VG_UNPACKED, "digifit.virtuagym.client.android", tmp_path)
    gr.build(native_fusion=False)
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/notification/FitnessNotificationPermissionActivity;",
        max_steps=800_000,
        max_wall_s=60.0,
        native_fusion=False,
    )

    vis = gr.tree.visible_nodes()
    allow_node = next((n for n in vis if (n.text or "").strip() == "Allow"), None)
    maybe_node = next((n for n in vis if (n.text or "").strip() == "Maybe later"), None)
    assert allow_node is not None and allow_node.clickable
    assert maybe_node is not None and maybe_node.clickable

    vh = int(gr.tree.height)
    _, ay, _, ah = allow_node.bounds
    mx, my, mw, mh = maybe_node.bounds
    assert 0 <= ay < ay + ah <= vh
    assert 0 <= my < my + mh <= vh
    assert 0 <= my - (ay + ah) <= 40

    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    gr.stubs._pending_nav_target = None
    gr.vm.steps = 0
    gr.vm.max_steps = 800_000
    gr.vm.step_budget = 800_000
    assert surf.dispatch({"type": "tap", "x": mx + max(1, mw // 2), "y": my + max(1, mh // 2)})
    gr.runner._drain_runnables()
    assert getattr(gr.stubs, "_pending_nav_target", None) is not None
