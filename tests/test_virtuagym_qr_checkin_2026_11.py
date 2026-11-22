# -*- coding: utf-8 -*-
"""Regressions- & End-to-End-Tests fuer Virtuagym QR-Check-In (QrCodeGeneratorActivity):
Pixel-basierte Bitmap-Operationen (setPixels/getPixels/createBitmap-Crop/PNG),
schnelle deterministische ZXing-QR-Generierung, Picasso-Handler-Entkopplung sowie
CardView-/Guideline-/ConstraintLayout-Geometrie und Schliessen-Interaktion (2026-11)."""
from __future__ import annotations

import datetime
import io
import json
import os
import struct
import sys
import zlib

from PIL import Image
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.java.dalvik import DalvikVM  # noqa: E402
from core.java.dex_loader import AndroidStubs  # noqa: E402
from core.java.glass_runner import GlassAppRunner, InstanceState  # noqa: E402
from core.java.layout_inflater import parse_layout_xml  # noqa: E402
from core.java.lifecycle import _StubRuntime  # noqa: E402
from core.java.net_recorder import NetResponse  # noqa: E402
from core.java.objects import JArray, JObject, JString  # noqa: E402
from core.java.seam_config import SeamConfig  # noqa: E402
from core.java.surface import SceneGraphSurface  # noqa: E402
from core.java.view_breadth_g16 import encode_bitmap_png  # noqa: E402
from core.java.view_tree import install_view_stubs, paint_commands  # noqa: E402


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
def test_bitmap_pixel_operations_crop_and_png_encoding():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)

    cfg = JObject("Landroid/graphics/Bitmap$Config;", {"__ename": "ARGB_8888"})
    bmp = _invoke_hook_or_stub(
        vm,
        "Landroid/graphics/Bitmap;",
        "createBitmap",
        "(IILandroid/graphics/Bitmap$Config;)Landroid/graphics/Bitmap;",
        [16, 16, cfg],
        True,
    )
    assert isinstance(bmp, JObject)

    # 10x10 schwarzer Block bei (3..12, 3..12) auf transparentem Grund;
    # ueberprueft auch zu gross uebergebene Stride-Konstanten (z. B. 512 bei 16x16-Puffer).
    px_in = [0] * (16 * 16)
    for yy in range(3, 13):
        for xx in range(3, 13):
            px_in[yy * 16 + xx] = -0x1000000
    in_arr = JArray("[I", px_in)
    _invoke_hook_or_stub(
        vm,
        "Landroid/graphics/Bitmap;",
        "setPixels",
        "([IIIIIII)V",
        [bmp, in_arr, 0, 512, 0, 0, 16, 16],
        False,
    )

    out_arr = JArray("[I", [0] * (16 * 16))
    _invoke_hook_or_stub(
        vm,
        "Landroid/graphics/Bitmap;",
        "getPixels",
        "([IIIIIII)V",
        [bmp, out_arr, 0, 16, 0, 0, 16, 16],
        False,
    )
    assert out_arr.data[0] == 0
    assert out_arr.data[3 * 16 + 3] == -0x1000000
    assert out_arr.data[12 * 16 + 12] == -0x1000000

    cropped = _invoke_hook_or_stub(
        vm,
        "Landroid/graphics/Bitmap;",
        "createBitmap",
        "(Landroid/graphics/Bitmap;IIII)Landroid/graphics/Bitmap;",
        [bmp, 3, 3, 10, 10],
        True,
    )
    assert isinstance(cropped, JObject)
    assert cropped.fields.get("_w") == 10
    assert cropped.fields.get("_h") == 10
    png_raw = encode_bitmap_png(cropped)
    assert png_raw.startswith(b"\x89PNG")
    im = Image.open(io.BytesIO(png_raw)).convert("RGBA")
    assert im.getpixel((0, 0)) == (0, 0, 0, 255)
    assert im.getpixel((im.width - 1, im.height - 1)) == (0, 0, 0, 255)


def test_zxing_writer_bitmatrix_and_picasso_leak_handler_stub():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)

    writer = JObject("Lcom/google/zxing/MultiFormatWriter;", {})
    qr_fmt = JObject("Lcom/google/zxing/BarcodeFormat;", {"__ename": "QR_CODE"})
    hints = JObject("Ljava/util/HashMap;", {"__map": {}})

    bm1 = _invoke_hook_or_stub(
        vm,
        "Lcom/google/zxing/MultiFormatWriter;",
        "a",
        "(Ljava/lang/String;Lcom/google/zxing/BarcodeFormat;IILjava/util/Map;)Lcom/google/zxing/common/BitMatrix;",
        [writer, JString("vg_checkin_qr=abc123"), qr_fmt, 512, 512, hints],
        False,
    )
    bm2 = _invoke_hook_or_stub(
        vm,
        "Lcom/google/zxing/MultiFormatWriter;",
        "a",
        "(Ljava/lang/String;Lcom/google/zxing/BarcodeFormat;IILjava/util/Map;)Lcom/google/zxing/common/BitMatrix;",
        [writer, JString("vg_checkin_qr=xyz987"), qr_fmt, 512, 512, hints],
        False,
    )
    assert isinstance(bm1, JObject)
    w = bm1.fields["a"]
    h = bm1.fields["b"]
    assert w == h == 93

    # Quiet-Zone ist 0, alle 3 Finder-Ecken (oben links, oben rechts, unten links) sind 1
    assert _invoke_hook_or_stub(vm, "Lcom/google/zxing/common/BitMatrix;", "b", "(II)Z", [bm1, 0, 0], False) == 0
    assert _invoke_hook_or_stub(vm, "Lcom/google/zxing/common/BitMatrix;", "b", "(II)Z", [bm1, 3, 3], False) == 1
    assert _invoke_hook_or_stub(vm, "Lcom/google/zxing/common/BitMatrix;", "b", "(II)Z", [bm1, w - 4, 3], False) == 1
    assert _invoke_hook_or_stub(vm, "Lcom/google/zxing/common/BitMatrix;", "b", "(II)Z", [bm1, 3, h - 4], False) == 1
    assert bm1.fields["d"].data != bm2.fields["d"].data

    # Picasso Utils$1.handleMessage darf sich nicht endlos per sendMessageDelayed neu einreihen
    stubs.pending_runnables.clear()
    pic_h = JObject("Lcom/squareup/picasso/Utils$1;", {})
    msg = JObject("Landroid/os/Message;", {})
    _invoke_hook_or_stub(
        vm,
        "Lcom/squareup/picasso/Utils$1;",
        "handleMessage",
        "(Landroid/os/Message;)V",
        [pic_h, msg],
        False,
    )
    assert stubs.pending_runnables == []


def test_qr_code_activity_layout_cardview_guidelines_and_lp_sizing():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)
    tree.dark_mode = True

    xml = """<?xml version="1.0" encoding="utf-8"?>
    <androidx.constraintlayout.widget.ConstraintLayout
        xmlns:android="http://schemas.android.com/apk/res/android"
        xmlns:app="http://schemas.android.com/apk/res-auto"
        android:id="@id/screen_container"
        android:layout_width="match_parent"
        android:layout_height="match_parent">
        <ImageView android:id="@id/cancel_button" android:clickable="true"
            android:layout_width="24.0dp" android:layout_height="24.0dp"
            app:layout_constraintStart_toStartOf="parent" app:layout_constraintTop_toTopOf="parent" />
        <androidx.constraintlayout.widget.Guideline android:orientation="vertical"
            android:id="@id/guideline_left" android:layout_width="wrap_content"
            android:layout_height="wrap_content" app:layout_constraintGuide_percent="0.1" />
        <androidx.constraintlayout.widget.Guideline android:orientation="vertical"
            android:id="@id/guideline_right" android:layout_width="wrap_content"
            android:layout_height="wrap_content" app:layout_constraintGuide_percent="0.9" />
        <androidx.constraintlayout.widget.Guideline android:orientation="vertical"
            android:id="@id/guideline_text_left" android:layout_width="wrap_content"
            android:layout_height="wrap_content" app:layout_constraintGuide_percent="0.25" />
        <androidx.constraintlayout.widget.Guideline android:orientation="vertical"
            android:id="@id/guideline_text_right" android:layout_width="wrap_content"
            android:layout_height="wrap_content" app:layout_constraintGuide_percent="0.75" />
        <ImageView android:id="@id/club_logo" android:layout_width="0.0dp" android:layout_height="0.0dp"
            app:layout_constraintBottom_toTopOf="@id/qr_code_border"
            app:layout_constraintDimensionRatio="2:1"
            app:layout_constraintEnd_toEndOf="@id/guideline_right"
            app:layout_constraintStart_toStartOf="@id/guideline_left"
            app:layout_constraintTop_toTopOf="@id/cancel_button"
            app:layout_constraintVertical_bias="0.0" />
        <androidx.cardview.widget.CardView android:id="@id/qr_code_border"
            android:layout_width="wrap_content" android:layout_height="wrap_content"
            app:cardBackgroundColor="@color/white" app:cardCornerRadius="12dp"
            app:layout_constraintBottom_toBottomOf="parent" app:layout_constraintEnd_toEndOf="parent"
            app:layout_constraintStart_toStartOf="parent" app:layout_constraintTop_toTopOf="parent">
            <ImageView android:id="@id/qr_code" android:layout_width="0.0dp" android:layout_height="0.0dp"
                android:layout_margin="16dp" />
        </androidx.cardview.widget.CardView>
        <TextView android:id="@id/qr_code_text" android:textColor="@color/white" android:gravity="center"
            android:layout_width="0.0dp" android:layout_height="wrap_content"
            android:text="Scan QR-Code to check in"
            app:layout_constraintBottom_toBottomOf="parent"
            app:layout_constraintEnd_toEndOf="@id/guideline_text_right"
            app:layout_constraintStart_toStartOf="@id/guideline_text_left"
            app:layout_constraintTop_toBottomOf="@id/qr_code_border" />
    </androidx.constraintlayout.widget.ConstraintLayout>
    """
    root = parse_layout_xml(xml)
    tree.set_root(root)

    qr_node = root.find("qr_code")
    qr_jobj = JObject("Landroid/widget/ImageView;", {"$view": qr_node})
    lp = _invoke_hook_or_stub(
        vm, "Landroid/view/View;", "getLayoutParams", "()Landroid/view/ViewGroup$LayoutParams;", [qr_jobj], False
    )
    assert lp.cls == "Landroid/widget/FrameLayout$LayoutParams;"
    lp.fields["width"] = 800
    lp.fields["height"] = 800
    _invoke_hook_or_stub(
        vm, "Landroid/view/View;", "setLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)V", [qr_jobj, lp], False
    )

    tree.width = 380
    tree.height = 776
    tree.layout()

    cb = root.find("cancel_button")
    logo = root.find("club_logo")
    card = root.find("qr_code_border")
    txt = root.find("qr_code_text")

    assert cb.bounds == (14, 14, 32, 32)
    assert logo.bounds[0] == 38 and logo.bounds[2] == 304 and logo.bounds[3] == 152
    assert card.props.get("bg_drawable", {}).get("fill") == "#ffffff"
    assert card.bounds[2] == card.bounds[3] == 276
    assert qr_node.bounds[2] == qr_node.bounds[3] == 224
    assert txt.bounds[1] > card.bounds[1] + card.bounds[3]
    assert abs((txt.bounds[0] + txt.bounds[2] / 2) - 190) <= 2


# ---------------------------------------------------------------------------
# 2. Virtuagym E2E-Test (mit synthetischen Mock-Daten, ohne persistente Appdata)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(_VG_UNPACKED), reason="Virtuagym unpacked APK not present")
def test_virtuagym_qr_checkin_e2e(tmp_path):
    png_bg = _make_png(120, 60, (40, 44, 52, 255))
    png_logo = _make_png(200, 100, (220, 35, 35, 255))
    png_icon = _make_png(24, 24, (255, 255, 255, 255))

    tz_cet = datetime.timezone(datetime.timedelta(hours=1))
    sun_ms = int(datetime.datetime(2026, 11, 22, 16, 45, 0, tzinfo=tz_cet).timestamp() * 1000)

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
                    "enable_qr_code_for_clients": True,
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

    def responder(req):
        url = getattr(req, "url", "") or ""
        if url.endswith(".png") or "img/" in url or "/thumb/" in url:
            if "logo" in url:
                return NetResponse(200, {"Content-Type": "image/png"}, png_logo, "OK")
            return NetResponse(200, {"Content-Type": "image/png"}, png_bg if "bg" in url else png_icon, "OK")
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
        if "mobile_app/settings" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(settings_payload).encode("utf-8"), "OK")
        if "v0/club" in url:
            return NetResponse(200, {"Content-Type": "application/json"}, json.dumps(club_payload).encode("utf-8"), "OK")
        return NetResponse(200, {"Content-Type": "application/json"}, b'{"statuscode":200,"result":[]}', "OK")

    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    sc.clock_epoch_ms = sun_ms
    sc.timezone = "Europe/Berlin"
    sc.locale = "de-DE"
    inst = InstanceState(
        instance_id="test_vg_qr_checkin",
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

    # Login + Home
    gr.run(
        entry="Ldigifit/android/virtuagym/presentation/screen/access/virtuagym/view/AccessActivity;",
        max_steps=400_000,
        max_wall_s=60.0,
        native_fusion=False,
    )
    surf = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf.paint()
    open_btn = gr.tree.root.find("log_in_text_button")
    bx, by, bw, bh = open_btn.bounds
    gr.vm.steps = 0
    gr.vm.max_steps = 400_000
    gr.vm.step_budget = 400_000
    surf.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 2})
    gr.runner._drain_runnables()
    surf.paint()
    for vid, val in (("email", "demo@example.com"), ("password", "syn-pass-123")):
        nd = gr.tree.root.find(vid)
        nd.text = val
        for sub in nd.walk():
            if sub.cls == "EditText":
                sub.text = val
    sub_btn = gr.tree.root.find("login_button")
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

    # Check-In Kachel (Index 9) antippen
    surf_home = SceneGraphSurface(gr.tree, vm=gr.vm)
    surf_home.paint()
    touch_views = [n for n in gr.tree.visible_nodes() if n.vid == "touch_view" and n.clickable]
    assert len(touch_views) >= 12
    tv = touch_views[9]
    bx, by, bw, bh = tv.bounds
    gr.vm.steps = 0
    gr.vm.max_steps = 500_000
    gr.vm.step_budget = 500_000
    surf_home.dispatch({"type": "tap", "x": bx + bw // 2, "y": by + bh // 5})
    gr.runner.navigate_to_pending()
    gr.runner._drain_runnables()

    assert gr.vm.steps < 250_000
    assert "QrCodeGeneratorActivity" in (getattr(gr.stubs._current_activity, "cls", "") or "")

    surf_qr = SceneGraphSurface(gr.tree, vm=gr.vm)
    cmds = paint_commands(gr.tree)
    surf_qr.paint()

    cancel_btn = gr.tree.root.find("cancel_button")
    club_logo = gr.tree.root.find("club_logo")
    qr_card = gr.tree.root.find("qr_code_border")
    qr_iv = gr.tree.root.find("qr_code")
    loader = gr.tree.root.find("loader")
    qr_txt = gr.tree.root.find("qr_code_text")

    assert cancel_btn is not None and cancel_btn.visible and cancel_btn.clickable
    assert cancel_btn.bounds == (14, 14, 32, 32)
    assert isinstance(cancel_btn.props.get("drawable"), dict)

    assert club_logo is not None and club_logo.visible
    assert club_logo.bounds[2] >= 280 and club_logo.bounds[1] < 100
    assert club_logo.props.get("img_bytes")

    assert loader is not None and not loader.visible

    assert qr_card is not None and qr_card.visible
    assert qr_card.bounds[2] == qr_card.bounds[3]
    assert str(qr_card.props.get("bg_drawable", {}).get("fill", "")).lower() == "#ffffff"

    assert qr_iv is not None and qr_iv.visible
    assert qr_iv.bounds[2] == qr_iv.bounds[3] >= 200
    qr_png = qr_iv.props.get("img_bytes")
    assert qr_png and qr_png.startswith(b"\x89PNG")
    qr_im = Image.open(io.BytesIO(qr_png)).convert("RGBA")
    assert qr_im.width >= 160 and qr_im.height >= 160
    assert abs(qr_im.width - qr_im.height) <= 4
    # Volle QR-Hoehe (oben links, oben rechts und unten links schwarze Finder-Pixel, kein Streifen)
    assert qr_im.getpixel((4, 4)) == (0, 0, 0, 255)
    assert qr_im.getpixel((qr_im.width - 4, 4)) == (0, 0, 0, 255)
    assert qr_im.getpixel((4, qr_im.height - 4)) == (0, 0, 0, 255)

    assert qr_txt is not None and qr_txt.visible
    assert (qr_txt.text or "").strip() == "Scan QR-Code to check in"
    assert qr_txt.bounds[1] > qr_card.bounds[1] + qr_card.bounds[3]
    assert abs((qr_txt.bounds[0] + qr_txt.bounds[2] / 2) - (gr.tree.width / 2)) <= 2

    assert not any(c.get("op") == "text" and c.get("text") == "IMG" for c in cmds)

    # 'X' oben links schliesst QrCodeGeneratorActivity zurueck zur HomeActivity
    cx, cy, cw, ch = cancel_btn.bounds
    gr.vm.steps = 0
    gr.vm.max_steps = 200_000
    gr.vm.step_budget = 200_000
    surf_qr.dispatch({"type": "tap", "x": cx + cw // 2, "y": cy + ch // 2})
    gr.runner.navigate_to_pending()
    gr.runner._drain_runnables()
    assert gr.vm.steps < 10_000
    assert "HomeActivity" in (getattr(gr.stubs._current_activity, "cls", "") or "")
