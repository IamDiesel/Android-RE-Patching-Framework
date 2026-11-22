# -*- coding: utf-8 -*-
"""Regressions- & End-to-End-Tests fuer Virtuagym Terminkalender (ScheduleActivity),
Mein Profil (UserProfileActivity) und Einstellungen (FitnessSettingsActivity / InfiniteTransition)
inkl. Date/Calendar-, Collection.toString-, Dalvik-Array-Kovarianz-, Drawable-Alpha- und Compose-Fixes (2026-11)."""
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
from core.java.drawable_loader import rasterize_vector_to_png  # noqa: E402
from core.java.glass_runner import GlassAppRunner, InstanceState  # noqa: E402
from core.java.lifecycle import _StubRuntime  # noqa: E402
from core.java.net_recorder import NetResponse  # noqa: E402
from core.java.objects import JArray, JObject, JString  # noqa: E402
from core.java.seam_config import SeamConfig  # noqa: E402
from core.java.surface import SceneGraphSurface  # noqa: E402
from core.java.view_tree import ViewNode, _install_compose_bridge, install_view_stubs  # noqa: E402


_VG_UNPACKED = os.path.join(_ROOT, "source", "digifit.virtuagym.client.android", "base_unpacked_apktool")


def _invoke_hook_or_stub(vm, cls: str, name: str, sig: str, args, static: bool):
    for hk in list(vm._method_hooks):
        handled, ret = hk(cls, cls, name, sig, list(args), static, vm)
        if handled:
            return ret
    ok, ret = vm.runtime.resolve_invoke(cls, name, sig, list(args), static)
    assert ok, f"Missing stub/hook: {cls}->{name}{sig}"
    return ret


def _make_png(w: int = 24, h: int = 24, rgba=(60, 80, 110, 255)) -> bytes:
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


# ---------------------------------------------------------------------------
# 1. Unit-Tests (ohne entpacktes APK lauffaehig)
# ---------------------------------------------------------------------------
def test_calendar_monday_week_and_standalone_day_tokens():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    tz_cet = datetime.timezone(datetime.timedelta(hours=1))
    sun_ms = int(datetime.datetime(2026, 11, 22, 16, 45, 0, tzinfo=tz_cet).timestamp() * 1000)
    stubs._clock_ms = sun_ms
    stubs._clock_tz = "Europe/Berlin"

    cal = _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "getInstance", "()Ljava/util/Calendar;", [], True)
    _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "setFirstDayOfWeek", "(I)V", [cal, 2], False)
    assert _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "getFirstDayOfWeek", "()I", [cal], False) == 2

    # Auf Sonntag 2026-11-22 mit firstDayOfWeek=MONDAY(2) muss set(DAY_OF_WEEK, MONDAY) den 16.11.2026 liefern
    _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "set", "(II)V", [cal, 7, 2], False)
    assert _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "get", "(I)I", [cal, 5], False) == 16
    assert _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "get", "(I)I", [cal, 2], False) == 10  # Nov (0-based)

    # Und set(DAY_OF_WEEK, SUNDAY=1) in derselben ISO-Woche muss Sonntag 22.11.2026 liefern
    _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "set", "(II)V", [cal, 7, 1], False)
    assert _invoke_hook_or_stub(vm, "Ljava/util/Calendar;", "get", "(I)I", [cal, 5], False) == 22

    # Standalone-Wochentagsmuster "cccccc" auf Deutsch -> Mo..So
    de_loc = JObject("Ljava/util/Locale;", {"lang": "de", "country": "DE"})
    pat = _invoke_hook_or_stub(
        vm,
        "Landroid/text/format/DateFormat;",
        "getBestDateTimePattern",
        "(Ljava/util/Locale;Ljava/lang/String;)Ljava/lang/String;",
        [de_loc, JString("cccccc")],
        True,
    )
    sdf = JObject("Ljava/text/SimpleDateFormat;", {})
    _invoke_hook_or_stub(
        vm,
        "Ljava/text/SimpleDateFormat;",
        "<init>",
        "(Ljava/lang/String;Ljava/util/Locale;)V",
        [sdf, pat, de_loc],
        False,
    )
    mon_dt = JObject("Ljava/util/Date;", {})
    mon_ms = int(datetime.datetime(2026, 11, 16, 12, 0, 0, tzinfo=tz_cet).timestamp() * 1000)
    _invoke_hook_or_stub(vm, "Ljava/util/Date;", "<init>", "(J)V", [mon_dt, mon_ms], False)
    fmt_mon = _invoke_hook_or_stub(
        vm, "Ljava/text/DateFormat;", "format", "(Ljava/util/Date;)Ljava/lang/String;", [sdf, mon_dt], False
    )
    assert fmt_mon.text.rstrip(".") == "Mo"

    # DateUtils.isToday
    assert _invoke_hook_or_stub(vm, "Landroid/text/format/DateUtils;", "isToday", "(J)Z", [sun_ms], True) == 1
    assert _invoke_hook_or_stub(
        vm, "Landroid/text/format/DateUtils;", "isToday", "(J)Z", [sun_ms - 86_400_000], True
    ) == 0


def test_collection_tostring_and_dalvik_reference_array_covariance():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    arr_list = JObject(
        "Ljava/util/ArrayList;",
        {
            "__items": [
                JObject("Ljava/lang/Long;", {"value": 501}),
                JObject("Ljava/lang/Long;", {"value": 502}),
                JObject("Ljava/lang/Long;", {"value": 503}),
            ]
        },
    )
    # invoke-virtual {list}, Ljava/lang/Object;->toString()Ljava/lang/String; (wie in SqlQueryBuilder.g)
    wants = vm._build_wants(arr_list.cls, "Ljava/lang/Object;")
    ok, res = stubs.resolve_phased(wants, arr_list.cls, "Ljava/lang/Object;", "toString", "()Ljava/lang/String;", [arr_list], False)
    assert ok and res.text == "[501, 502, 503]"

    # Selbstreferenz-Schutz analog AbstractCollection.toString()
    cyc_list = JObject("Ljava/util/ArrayList;", {"__items": []})
    cyc_list.fields["__items"].append(cyc_list)
    ok_c, res_c = stubs.resolve("Ljava/util/ArrayList;", "toString", "()Ljava/lang/String;", [cyc_list], False)
    assert ok_c and res_c.text == "[(this Collection)]"

    # Stack -> Vector -> AbstractList -> AbstractCollection Vererbung
    stk = JObject("Ljava/util/Stack;", {"__items": [JString("SELECT"), JString("*")]})
    assert vm._instance_of(stk, "Ljava/util/AbstractCollection;") == 1
    assert vm._instance_of(stk, "Ljava/util/Vector;") == 1

    # Referenz-Array-Kovarianz: [LMyEnum; ist instanceof [Ljava/lang/Object;, primitives [I aber NICHT!
    enum_arr = JArray("[Ldigifit/android/features/habits/domain/model/habit/HabitType;", [])
    int_arr = JArray("[I", [1, 2, 3])
    assert vm._instance_of(enum_arr, "[Ljava/lang/Object;") == 1
    assert vm._instance_of(int_arr, "[Ljava/lang/Object;") == 0


def test_vector_fill_alpha_and_infinite_transition_suspension():
    spec = {
        "kind": "vector",
        "vw": 24.0,
        "vh": 24.0,
        "path_groups": [
            {
                "subpaths": [([(2.0, 2.0), (22.0, 2.0), (22.0, 22.0), (2.0, 22.0)], True)],
                "fill": "#000000",
                "fill_alpha": 0.16,
                "stroke": None,
                "stroke_alpha": 1.0,
                "stroke_w": 0.0,
                "fill_type": "nonZero",
            }
        ],
    }
    import io
    from PIL import Image

    png_bytes = rasterize_vector_to_png(spec, 24, 24)
    assert png_bytes and png_bytes.startswith(b"\x89PNG")
    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    alpha_mid = im.getpixel((12, 12))[3]
    assert 20 <= alpha_mid <= 65

    # InfiniteTransition / withInfiniteAnimationFrameNanos suspendiert sofort statt Endlosschleife
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    tree_c = install_view_stubs(stubs)
    _install_compose_bridge(stubs, tree_c)

    ret = _invoke_hook_or_stub(
        vm,
        "Landroidx/compose/ui/platform/InfiniteAnimationPolicyKt;",
        "withInfiniteAnimationFrameNanos",
        "(Lkotlin/jvm/functions/Function1;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
        [JObject("Lkotlin/jvm/functions/Function1;", {}), JObject("Lkotlin/coroutines/Continuation;", {})],
        True,
    )
    assert isinstance(ret, JObject) and "CoroutineSingletons" in (ret.cls or "")


def test_set_text_null_layer_drawable_and_cl_lead_thumb_layout():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    tree = install_view_stubs(stubs)

    # TextView.setText((CharSequence) null) -> "" (niemals "0"!)
    tv_node = ViewNode("TextView", vid="event_title", text="initial")
    tv_obj = JObject("Landroid/widget/TextView;", {"$view": tv_node})
    _invoke_hook_or_stub(vm, "Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V", [tv_obj, 0], False)
    assert tv_node.text == ""
    _invoke_hook_or_stub(vm, "Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V", [tv_obj, None], False)
    assert tv_node.text == ""

    # LayerDrawable.getDrawable(0) -> GradientDrawable.setStroke / setColor
    ripple = JObject("Landroid/graphics/drawable/RippleDrawable;", {"_drawable": {"kind": "shape", "radius": 18.0}})
    child_drw = _invoke_hook_or_stub(
        vm, "Landroid/graphics/drawable/LayerDrawable;", "getDrawable", "(I)Landroid/graphics/drawable/Drawable;", [ripple, 0], False
    )
    assert isinstance(child_drw, JObject) and child_drw.cls == "Landroid/graphics/drawable/GradientDrawable;"
    _invoke_hook_or_stub(vm, "Landroid/graphics/drawable/GradientDrawable;", "setStroke", "(II)V", [child_drw, 2, 0xFF334455], False)
    assert child_drw.fields["_drawable"]["outline"] == "#334455"

    # ConstraintLayout Lead-Thumbnail links + gestapelte TextViews rechts
    card = ViewNode("ConstraintLayout", vid="root_view", props={"pad_l": 16, "pad_r": 16, "pad_t": 4, "pad_b": 4})
    img = ViewNode(
        "ImageView",
        vid="event_image",
        props={"$cl_start_parent": True, "$cl_top_parent": True, "$cl_bot_parent": True, "$cl_ratio": "1.16:1", "xml_w_mode": "zero", "xml_h_mode": "zero"},
    )
    t1 = ViewNode("TextView", vid="event_title", text="FIGHTER TRAINING", props={"$cl_start_to_end": "event_image"})
    t2 = ViewNode("TextView", vid="event_duration", text="19:15 • 60 min", props={"$cl_start_to_end": "event_image"})
    t3 = ViewNode("TextView", vid="event_instructor", text="Pawel Danilow", props={"$cl_start_to_end": "event_image"})
    t4 = ViewNode("TextView", vid="event_participants", text="Completed", props={"$cl_start_to_end": "event_image"})
    for ch in (img, t1, t2, t3, t4):
        card.add(ch)
    rv = ViewNode("RecyclerView", vid="list")
    rv.add(card)
    tree.set_root(rv)
    tree.ensure_layout()
    assert t1.bounds[0] >= img.bounds[0] + img.bounds[2] + 8
    assert t2.bounds[0] == t1.bounds[0]
    assert t3.bounds[0] == t1.bounds[0]
    assert t4.bounds[0] == t1.bounds[0]
    assert t1.bounds[1] < t2.bounds[1] < t3.bounds[1] < t4.bounds[1]
    assert card.bounds[3] <= 130


# ---------------------------------------------------------------------------
# 2. Virtuagym End-to-End-Tests (Terminkalender, Mein Profil, Einstellungen)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Virtuagym unpacked fixture not available")
def test_virtuagym_schedule_profile_and_settings_end_to_end(tmp_path):
    png_bg = _make_png(64, 64, (24, 28, 36, 255))
    png_cover = _make_png(120, 60, (90, 95, 105, 255))
    png_avatar = _make_png(64, 64, (190, 195, 200, 255))
    png_icon = _make_png(24, 24, (255, 255, 255, 255))

    tz_cet = datetime.timezone(datetime.timedelta(hours=1))
    sun_dt = datetime.datetime(2026, 11, 22, 16, 45, 0, tzinfo=tz_cet)
    sun_ms = int(sun_dt.timestamp() * 1000)

    fri_1915 = int(datetime.datetime(2026, 11, 20, 19, 15, 0, tzinfo=tz_cet).timestamp())
    fri_2015 = int(datetime.datetime(2026, 11, 20, 20, 15, 0, tzinfo=tz_cet).timestamp())
    sat_1100 = int(datetime.datetime(2026, 11, 21, 11, 0, 0, tzinfo=tz_cet).timestamp())
    sat_1200 = int(datetime.datetime(2026, 11, 21, 12, 0, 0, tzinfo=tz_cet).timestamp())
    sat_1400 = int(datetime.datetime(2026, 11, 21, 14, 0, 0, tzinfo=tz_cet).timestamp())

    tiles = [
        ("Community", "virtuagym://app.virtuagym.com/fitness/group/29319"),
        ("Schreib etwas", "virtuagym://app.virtuagym.com/fitness/writepost"),
        ("Mein Profil", "virtuagym://app.virtuagym.com/fitness/user"),
        ("Terminplan", "virtuagym://app.virtuagym.com/fitness/club/schedule"),
        ("Aktivitätskalender", "virtuagym://app.virtuagym.com/fitness/calendar"),
        ("Fortschritt", "virtuagym://app.virtuagym.com/fitness/progresstracker"),
        ("Challenge", "virtuagym://app.virtuagym.com/fitness/challenge"),
        ("Workouts", "virtuagym://app.virtuagym.com/fitness/workout"),
        ("Ernährung", "virtuagym://app.virtuagym.com/fitness/app/food"),
        ("Check-In", "virtuagym://app.virtuagym.com/fitness/club/qrcheckin"),
        ("Boxring Stuttgart", "virtuagym://app.virtuagym.com/fitness/club"),
        ("Einstellungen", "virtuagym://app.virtuagym.com/fitness/settings"),
    ]

    club_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 1,
        "timestamp": int(sun_ms // 1000),
        "result": [
            {
                "id": 34314,
                "name": "Boxring Stuttgart",
                "color": "181C24",
                "accent_color": "E53935",
                "gradient_light": "222222",
                "gradient_dark": "111111",
                "logo": "https://cdn.example.test/img/logo.png",
                "print_logo": "https://cdn.example.test/img/logo.png",
                "background": "https://cdn.example.test/img/bg.png",
                "country_code": "DE",
                "city": "Stuttgart",
                "street": "Teststrasse 1",
                "zipcode": "70173",
                "website": "https://example.test",
                "features": {
                    "enable_custom_homescreen": True,
                    "enable_clubfinder": True,
                    "enable_community": True,
                    "enable_activity_calendar": True,
                    "enable_my_plan": True,
                    "enable_flexible_planning": False,
                    "enable_challenges": True,
                    "enable_workouts": True,
                    "enable_nutrition": True,
                    "enable_progress": True,
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
                "club_id": 34314,
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
                        "home_screen_label": label,
                        "home_screen_order": i + 1,
                        "home_screen_picture_android": f"icon_{i + 1}.png",
                        "item_background_color": "00000000",
                        "pro_only": 0,
                        "android_link": link,
                        "deleted": 0,
                    }
                    for i, (label, link) in enumerate(tiles)
                ],
            }
        ],
    }

    def _mk_act_def(aid: int, name: str, descr: str, typ: int, dur: int, order: int) -> dict:
        return {
            "id": aid,
            "name": name,
            "description": descr,
            "url_id": "",
            "addable": 1,
            "searchfield": name.lower(),
            "type": typ,
            "content_type": None,
            "difficulty": 2,
            "equipment": "",
            "equipment_keys": [],
            "primary_muscle_groups": "",
            "primary_muscle_groups_keys": [],
            "secondary_muscle_groups": "",
            "secondary_muscle_groups_keys": [],
            "duration": dur,
            "video": None,
            "video_female": None,
            "video_dm": None,
            "video_female_dm": None,
            "img": "act.png",
            "img_female": "act.png",
            "thumb": "act.png",
            "thumb_female": "act.png",
            "order": order,
            "gps_trackable": 0,
            "met": 7.5,
            "club_id": 34314,
            "pro": 0,
            "uses_weights": 0,
            "instructions": [],
            "read_only": 1,
            "is_class": 1,
            "has_distance": 0,
            "available_on_kiosk": 0,
            "kiosk_rotation": 0.0,
            "avatar_scale": 1.0,
            "yoga_exercise": 0,
            "standing_animation": 1,
            "reps": [],
            "time_reps": [],
            "rest_sets": [],
            "time_based": True,
            "rest_period": 0,
            "rest_after_exercise": 0,
            "youtube_id": None,
            "youtube_id_female": None,
            "external_content_id": None,
            "category": [],
            "deleted": 0,
        }

    act_defs_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 3,
        "timestamp": int(sun_ms // 1000),
        "result": [
            _mk_act_def(501, "FIGHTER TRAINING", "Wettkampftraining", 0, 60, 1),
            _mk_act_def(502, "Kickboxen", "Kickboxen Kurs", 1, 60, 2),
            _mk_act_def(503, "OPEN GYM", "Freies Training", 0, 120, 3),
        ],
    }

    event_filters_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result": {
            "locations": [],
            "instructors": [
                {"member_id": 701, "name": "Pawel Danilow", "picture": ""},
                {"member_id": 702, "name": "Simon Bart", "picture": ""},
                {"member_id": 703, "name": "Pana Stamkopoulos", "picture": ""},
            ],
            "activities": [{"activity_id": 501}, {"activity_id": 502}, {"activity_id": 503}],
            "schedules": [],
        },
    }

    schedule_events_payload = {
        "statuscode": 200,
        "statusmessage": "OK",
        "result_count": 3,
        "timestamp": int(sun_ms // 1000),
        "result": [
            {
                "event_id": "ev_1001",
                "activity_id": 501,
                "schedule_id": "10",
                "event_start": fri_1915,
                "event_end": fri_2015,
                "attendees": 12,
                "max_attendees": 20,
                "joined": 0,
                "joinable": 0,
                "deleted": 0,
                "bookable_from_timestamp": 0,
                "enough_credits": True,
                "hide_participants_amount": 0,
                "instructor_id": 701,
                "instructor_member_id": 701,
                "is_full": False,
                "is_instructor_pic": False,
                "service_cost": 1,
                "service_name": "FIGHTER TRAINING",
                "service_id": "1",
                "is_too_late": True,
                "can_join_waiting_list": False,
                "hide_from_client_view": False,
                "cancel_before_duration": 0,
                "is_refundable": True,
                "instructors": [{"user_id": 7010, "member_id": 701, "name": "Pawel Danilow", "picture": ""}],
            },
            {
                "event_id": "ev_1002",
                "activity_id": 502,
                "schedule_id": "10",
                "event_start": sat_1100,
                "event_end": sat_1200,
                "attendees": 14,
                "max_attendees": 20,
                "joined": 0,
                "joinable": 0,
                "deleted": 0,
                "bookable_from_timestamp": 0,
                "enough_credits": True,
                "hide_participants_amount": 0,
                "instructor_id": 702,
                "instructor_member_id": 702,
                "is_full": False,
                "is_instructor_pic": False,
                "service_cost": 1,
                "service_name": "Kickboxen",
                "service_id": "1",
                "is_too_late": True,
                "can_join_waiting_list": False,
                "hide_from_client_view": False,
                "cancel_before_duration": 0,
                "is_refundable": True,
                "instructors": [{"user_id": 7020, "member_id": 702, "name": "Simon Bart", "picture": ""}],
            },
            {
                "event_id": "ev_1003",
                "activity_id": 503,
                "schedule_id": "10",
                "event_start": sat_1200,
                "event_end": sat_1400,
                "attendees": 8,
                "max_attendees": 25,
                "joined": 0,
                "joinable": 0,
                "deleted": 0,
                "bookable_from_timestamp": 0,
                "enough_credits": True,
                "hide_participants_amount": 0,
                "instructor_id": 703,
                "instructor_member_id": 703,
                "is_full": False,
                "is_instructor_pic": False,
                "service_cost": 1,
                "service_name": "OPEN GYM",
                "service_id": "1",
                "is_too_late": True,
                "can_join_waiting_list": False,
                "hide_from_client_view": False,
                "cancel_before_duration": 0,
                "is_refundable": True,
                "instructors": [{"user_id": 7030, "member_id": 703, "name": "Pana Stamkopoulos", "picture": ""}],
            },
        ],
    }

    seen_urls: list[str] = []

    def responder(req):
        url = getattr(req, "url", "") or ""
        seen_urls.append(url)
        if "cover_photo" in url or "cover-photo" in url:
            return NetResponse(200, {"Content-Type": "image/png"}, png_cover, "OK")
        if "profile_pic" in url or "userpic" in url:
            return NetResponse(200, {"Content-Type": "image/png"}, png_avatar, "OK")
        if url.endswith(".png") or "img/" in url or "/thumb/" in url:
            blob = png_bg if ("bg" in url or "logo" in url) else png_icon
            return NetResponse(200, {"Content-Type": "image/png"}, blob, "OK")
        if "user/current" in url:
            body = {
                "statuscode": 200,
                "statusmessage": "OK",
                "result_count": 1,
                "timestamp": int(sun_ms // 1000),
                "result": {
                    "id": 99001,
                    "username": "demouser",
                    "email": "demo@example.com",
                    "first_name": "Daniel",
                    "last_name": "Test",
                    "displayname": "Daniel",
                    "gender": "m",
                    "club_ids": [34314],
                    "member_ids": [{"club_id": 34314, "superclub_id": 0, "member_id": 88001, "member_pro": 1}],
                    "pro": 1,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        if "activity/definition/club" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(act_defs_payload).encode("utf-8"), "OK")
        if "activity/definition" in url:
            return NetResponse(
                200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[],"result_count":0,"timestamp":1795362300}', "OK"
            )
        if "mobile_app/settings" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(settings_payload).encode("utf-8"), "OK")
        if "/eventfilters" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(event_filters_payload).encode("utf-8"), "OK")
        if "/event/" in url and "/to/" in url:
            if "2026%2F11%2F16" in url or "2026/11/16" in url:
                return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(schedule_events_payload).encode("utf-8"), "OK")
            return NetResponse(
                200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[],"result_count":0,"timestamp":1795362300}', "OK"
            )
        if "v0/club" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(club_payload).encode("utf-8"), "OK")
        if "/api/user/99001/messages" in url or "/api/v0/user/99001/messages" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[]}', "OK")
        if "/api/user/99001" in url:
            body = {
                "statuscode": 200,
                "result": {
                    "user_id": 99001,
                    "is_online": True,
                    "user_displayname": "Daniel",
                    "user_avatar": "/images/profile_pic.png",
                    "cover_photo": "/images/cover_photos/cover-photo-3.png",
                    "pro": 1,
                    "gender": "m",
                    "nr_likes": 0,
                    "privacy_contact": False,
                    "country": "Deutschland",
                    "city": "Renningen",
                    "total_kcal": 0,
                    "total_min": 0,
                    "total_km": 0,
                    "fitness_points": 0,
                    "user_liked": False,
                    "user_following": False,
                    "nr_following": 0,
                    "nr_followers": 0,
                },
            }
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8"), "OK")
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[]}', "OK")

    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    sc.clock_epoch_ms = sun_ms
    sc.timezone = "Europe/Berlin"
    sc.locale = "de-DE"
    inst = InstanceState(
        instance_id="test_vg_sched_prof_set",
        package="digifit.virtuagym.client.android",
        app_root=_VG_UNPACKED,
        zone="original",
        appdata_root=str(tmp_path / "appdata"),
        selfcache_root=str(tmp_path / "selfcache"),
    )
    gr = GlassAppRunner(_VG_UNPACKED, zone="original", seam_config=sc, instance_state=inst)
    gr.build(net_responder=responder, native_fusion=False)
    gr.stubs._clock_ms = sun_ms
    gr.stubs._clock_tz = "Europe/Berlin"

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
    gr.vm.steps = 0
    gr.vm.max_steps = 3_000_000
    gr.vm.step_budget = 3_000_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()

    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/home/overview/view/HomeActivity;",
        max_steps=1_500_000,
        max_wall_s=90.0,
        native_fusion=False,
    )

    def _open_tile(idx: int) -> int:
        surf_t = SceneGraphSurface(gr.tree, vm=gr.vm)
        surf_t.paint()
        touch_views = [n for n in gr.tree.visible_nodes() if n.vid == "touch_view" and n.clickable]
        assert len(touch_views) == 12
        tv = touch_views[idx]
        tx, ty, tw, th = tv.bounds
        gr.vm.steps = 0
        gr.vm.max_steps = 1_500_000
        gr.vm.step_budget = 1_500_000
        surf_t.dispatch({"type": "tap", "x": tx + tw // 2, "y": ty + th // 5})
        gr.runner.navigate_to_pending()
        gr.runner._drain_runnables()
        used = gr.vm.steps
        SceneGraphSurface(gr.tree, vm=gr.vm).paint()
        return used

    def _back_home():
        gr.vm.steps = 0
        gr.vm.max_steps = 1_000_000
        gr.vm.step_budget = 1_000_000
        gr.runner.handle_back()

    # --- 1) Terminkalender (ScheduleActivity) ---
    before_sched_urls = len(seen_urls)
    sched_steps = _open_tile(3)
    assert sched_steps < 500_000
    assert "ScheduleActivity" in (getattr(gr.stubs._current_activity, "cls", "") or "")
    sched_urls = seen_urls[before_sched_urls:]
    assert any("2026%2F11%2F16" in u or "2026/11/16" in u for u in sched_urls)

    vis_sched = gr.tree.visible_nodes()
    sched_texts = {(n.text or "").strip() for n in vis_sched if (n.text or "").strip()}
    assert "Schedule" in sched_texts
    for d_lbl in ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"):
        assert d_lbl in sched_texts
    for d_num in ("16", "17", "18", "19", "20", "21", "22"):
        assert d_num in sched_texts
    assert "All activities" in sched_texts
    assert "All instructors" in sched_texts
    assert "Button" not in sched_texts
    assert "FIGHTER TRAINING" in sched_texts
    assert "Kickboxen" in sched_texts
    assert "OPEN GYM" in sched_texts
    assert "Pawel Danilow" in sched_texts
    assert "Simon Bart" in sched_texts
    assert "Pana Stamkopoulos" in sched_texts
    assert "Load next week" in sched_texts

    wp_node = gr.tree.root.find("week_pager")
    assert wp_node is not None and wp_node.bounds[1] < 200
    ev_cards = [n for n in vis_sched if n.vid == "root_view" and n.find("event_image") is not None]
    assert len(ev_cards) >= 3
    first_img = ev_cards[0].find("event_image")
    first_dur = ev_cards[0].find("event_duration")
    assert first_img is not None and first_dur is not None
    assert first_dur.bounds[0] >= first_img.bounds[0] + first_img.bounds[2]
    _back_home()

    # --- 2) Mein Profil (UserProfileActivity) ---
    prof_steps = _open_tile(2)
    assert prof_steps < 400_000
    assert "UserProfileActivity" in (getattr(gr.stubs._current_activity, "cls", "") or "")
    vis_prof = gr.tree.visible_nodes()
    prof_texts = {(n.text or "").strip(): n for n in vis_prof if (n.text or "").strip()}
    assert "Daniel" in prof_texts
    assert "Renningen" in prof_texts
    assert prof_texts["Daniel"].bounds[0] >= 90
    assert prof_texts["Daniel"].bounds[2] >= 180
    for stat_lbl in ("Following", "Followers", "Likes"):
        assert stat_lbl in prof_texts
        assert prof_texts[stat_lbl].bounds[2] >= 80
    pro_badges = [
        n for n in vis_prof
        if isinstance(n.props.get("drawable"), dict) and "pro_label" in str(n.props["drawable"].get("name", ""))
    ]
    assert pro_badges and pro_badges[0].bounds[2] == 32 and pro_badges[0].bounds[3] == 20 and pro_badges[0].bounds[1] < 220
    for tab_lbl in ("Posts", "Updates", "Achievements"):
        assert tab_lbl in prof_texts
    assert not any(n.cls == "ProgressBar" for n in vis_prof)
    _back_home()

    # --- 3) Einstellungen (FitnessSettingsActivity / kein InfiniteTransition-Loop) ---
    set_steps = _open_tile(11)
    assert set_steps < 300_000
    assert "FitnessSettingsActivity" in (getattr(gr.stubs._current_activity, "cls", "") or "")
    vis_set = gr.tree.visible_nodes()
    set_texts = {(n.text or "").strip(): n for n in vis_set if (n.text or "").strip()}
    for row_lbl in (
        "Settings",
        "Account",
        "Training",
        "Notifications",
        "Devices & connections",
        "Dark mode",
        "Privacy",
        "Help",
        "Sync",
        "Just now",
        "Log out",
        "App Version 12.4.2",
    ):
        assert row_lbl in set_texts
    assert set_texts["Just now"].bounds[2] >= 65
    top_btns = [n for n in vis_set if n.bounds[1] < 60 and n.clickable and n is not set_texts["Settings"]]
    assert top_btns and top_btns[0].bounds[0] < set_texts["Settings"].bounds[0]
