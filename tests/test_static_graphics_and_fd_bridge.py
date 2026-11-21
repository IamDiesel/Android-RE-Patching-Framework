# -*- coding: utf-8 -*-
"""Tests fuer FD-Bruecke (/proc/.../fd/<N>), Reflection-Int-Unboxing, ConcurrentHashMap.putIfAbsent,
Startup-Wall-Budget, DrawableResolver (PNG/.9.png/XML-<shape>/<selector>/<layer-list>/<vector>)
und paint_commands/BitmapFactory/RN-Image-Integration."""
from __future__ import annotations

import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM, DalvikException
from core.java.dex_loader import AndroidStubs
from core.java.drawable_loader import DrawableResolver, parse_svg_path_data, resolve_color, drawable_to_paint_commands
from core.java.fs_bridge import install_fs_stubs, get_shared_fs
from core.java.fusion_loader import NativeFusionLoader
from core.java.layout_inflater import inflate_res, parse_layout_xml
from core.java.native_link import NativeMethodTable
from core.java.objects import JArray, JObject, JString
from core.java.rn_producer import RnMountRecorder
from core.java.view_tree import ViewNode, ViewTree, install_view_stubs, paint_commands
from core.native.linker import get_linker, MODULE_ARENA_BASE
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from services.native_os_service import NativeOsService
from tests.test_generic_unpack_linker import _build_aarch64_elf, _movz_x0_ret


def test_fd_bridge_end_to_end_loads_unlinked_elf_via_proc_fd():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    fs = install_fs_stubs(stubs, eng, pkg="com.fd.test")

    elf = _build_aarch64_elf({"probe": _movz_x0_ret(77)})
    tmp_path = "/data/data/com.fd.test/libsecret.so_0"

    # FileOutputStream schreiben -> getFD() -> Os.dup() -> Os.remove() -> Field.get -> Integer.intValue
    fos = JObject("Ljava/io/FileOutputStream;", {})
    stubs.resolve("Ljava/io/FileOutputStream;", "<init>", "(Ljava/lang/String;)V", [fos, JString(tmp_path)], False)
    stubs.resolve("Ljava/io/FileOutputStream;", "write", "([B)V", [fos, JArray("[B", list(elf))], False)
    ok_fd, fd_obj = stubs.resolve("Ljava/io/FileOutputStream;", "getFD", "()Ljava/io/FileDescriptor;", [fos], False)
    assert ok_fd and isinstance(fd_obj, JObject)

    ok_dup, dup_fd = stubs.resolve("Landroid/system/Os;", "dup", "(Ljava/io/FileDescriptor;)Ljava/io/FileDescriptor;", [fd_obj], True)
    assert ok_dup and isinstance(dup_fd, JObject)
    stubs.resolve("Landroid/system/Os;", "remove", "(Ljava/lang/String;)V", [JString(tmp_path)], True)
    assert fs.lookup(tmp_path) is None

    ok_tid, tid = stubs.resolve("Landroid/system/Os;", "gettid", "()I", [], True)
    assert ok_tid and tid == 1234

    fd_cls = stubs._cls_forname([JString("java.io.FileDescriptor")])
    fd_field = stubs._cls_getfield([fd_cls, JString("descriptor")])
    boxed_fd = stubs._field_get([fd_field, dup_fd], "L")
    assert isinstance(boxed_fd, JObject) and boxed_fd.cls == "Ljava/lang/Integer;"
    ok_iv, fd_int = stubs.resolve("Ljava/lang/Integer;", "intValue", "()I", [boxed_fd], False)
    assert ok_iv and fd_int >= 10

    proc_path = f"/proc/self/task/{tid}/fd/{fd_int}"
    logs = []
    tbl = NativeMethodTable()
    loader = NativeFusionLoader(
        svc=svc, eng=eng, jni=jni, table=tbl, ffi=ffi, stubs=stubs,
        pkg="com.fd.test", log_fn=logs.append,
    )
    loader.load_path(proc_path)

    lnk = get_linker(eng)
    assert len(lnk.modules) == 1
    mod = lnk.modules[0]
    assert mod.name == "libsecret.so"
    assert mod.load_bias >= MODULE_ARENA_BASE
    assert "probe" in mod.symbols
    assert any("libsecret.so" in ln for ln in logs)


def test_concurrent_hashmap_put_if_absent():
    stubs = AndroidStubs()
    m = JObject("Ljava/util/concurrent/ConcurrentHashMap;", {})
    stubs.resolve(m.cls, "<init>", "()V", [m], False)
    ok1, r1 = stubs.resolve(m.cls, "putIfAbsent", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                            [m, JString("k1"), JString("v1")], False)
    assert ok1 and r1 is None
    ok2, r2 = stubs.resolve(m.cls, "putIfAbsent", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                            [m, JString("k1"), JString("v2")], False)
    assert ok2 and stubs._txt(r2) == "v1"
    _, got = stubs.resolve(m.cls, "get", "(Ljava/lang/Object;)Ljava/lang/Object;", [m, JString("k1")], False)
    assert stubs._txt(got) == "v1"


def test_fusion_loader_cumulative_budget_triggers_lazy_init():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    tbl = NativeMethodTable()
    os_svc = NativeOsService(publish=False)
    os_svc.attach_env(eng)
    os_svc.install(eng)
    stubs = AndroidStubs(vm=DalvikVM())
    fs = install_fs_stubs(stubs, eng, pkg="com.budget.app")

    loader = NativeFusionLoader(
        svc=svc, eng=eng, jni=jni, table=tbl, ffi=ffi, stubs=stubs,
        pkg="com.budget.app", max_total_init_wall_s=0.1,
    )
    loader._native_init_spent_s = 0.2
    assert loader._budget_kwargs() == {"lazy_init": True}

    elf = _build_aarch64_elf({"Java_com_budget_app_Native_ping": _movz_x0_ret(99)})
    fs.add_file("/data/data/com.budget.app/liblazy.so", elf)
    logs = []
    loader.log_fn = logs.append
    loader.load_path("/data/data/com.budget.app/liblazy.so")
    lnk = get_linker(eng)
    assert len(lnk.modules) == 1 and lnk.modules[0].name == "liblazy.so"
    assert tbl.lookup("Lcom/budget/app/Native;", "ping", "()I") is not None
    assert any("lazy" in ln for ln in logs)


def test_dalvik_wall_deadline_breaks_infinite_loop():
    vm = DalvikVM()
    vm.wall_deadline = time.monotonic() + 0.08
    t0 = time.monotonic()
    try:
        vm.run_method([("goto", 0)], 1)
        assert False, "WallBudget haette ausloesen muessen"
    except DalvikException as e:
        assert "WallBudget" in str(e)
    assert time.monotonic() - t0 < 0.6


def test_drawable_resolver_and_svg_vector_tessellation(tmp_path):
    res = tmp_path / "res"
    (res / "values").mkdir(parents=True)
    (res / "drawable-mdpi").mkdir(parents=True)
    (res / "drawable-xxhdpi").mkdir(parents=True)
    (res / "drawable-nodpi").mkdir(parents=True)
    (res / "drawable-anydpi-v24").mkdir(parents=True)
    (res / "drawable").mkdir(parents=True)

    (res / "values" / "colors.xml").write_text(
        '<resources><color name="brand_blue">#ff2f6df0</color>'
        '<color name="btn_tint">@color/brand_blue</color></resources>',
        encoding="utf-8",
    )
    (res / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="drawable" name="logo" id="0x7f080001" />'
        '<public type="drawable" name="ic_vec" id="0x7f080002" />'
        '</resources>',
        encoding="utf-8",
    )

    # Raster vs XML Namensgleichheit -> xxhdpi-PNG gewinnt vor anydpi-XML
    png_stub = b"\x89PNG\r\n\x1a\n"
    (res / "drawable-mdpi" / "logo.png").write_bytes(png_stub)
    (res / "drawable-xxhdpi" / "logo.png").write_bytes(png_stub)
    (res / "drawable-anydpi-v24" / "logo.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:viewportWidth="24" android:viewportHeight="24">'
        '<path android:fillColor="#ffffffff" android:pathData="M0,0 L24,0 L24,24 Z"/></vector>',
        encoding="utf-8",
    )
    (res / "drawable-nodpi" / "bubble.9.png").write_bytes(png_stub)

    (res / "drawable-anydpi-v24" / "ic_vec.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:viewportWidth="24" android:viewportHeight="24">'
        '<group android:translateX="2" android:scaleX="1.2">'
        '<path android:fillColor="#ffffffff" android:pathData="M2,4 L10,4 C12,4 12,10 10,10 Q6,12 2,10 A2,2 0 102,4 Z"/>'
        '</group></vector>',
        encoding="utf-8",
    )
    (res / "drawable" / "bg_btn.xml").write_text(
        '<selector xmlns:android="http://schemas.android.com/apk/res/android">'
        '<item android:state_pressed="true"><shape><solid android:color="#ff0000"/></shape></item>'
        '<item><shape android:shape="rectangle"><solid android:color="@color/btn_tint"/><corners android:radius="8dp"/></shape></item>'
        '</selector>',
        encoding="utf-8",
    )
    (res / "drawable" / "layers.xml").write_text(
        '<layer-list xmlns:android="http://schemas.android.com/apk/res/android">'
        '<item android:drawable="@android:color/transparent"/>'
        '<item><shape android:shape="oval"><solid android:color="#ff44aa22"/></shape></item>'
        '</layer-list>',
        encoding="utf-8",
    )

    rsv = DrawableResolver(str(tmp_path))
    assert resolve_color("@color/btn_tint", rsv.colors) == ("#2f6df0", 1.0)
    assert resolve_color("#00ffffff")[1] == 0.0

    sp_logo = rsv.resolve("@drawable/logo")
    assert sp_logo and sp_logo["kind"] == "raster"
    assert "drawable-xxhdpi" in sp_logo["path"]

    sp_9 = rsv.resolve("bubble")
    assert sp_9 and sp_9["kind"] == "raster" and sp_9["is_9patch"] is True

    sp_vec = rsv.resolve(0x7F080002)
    assert sp_vec and sp_vec["kind"] == "vector" and len(sp_vec["paths"]) == 1
    assert len(sp_vec["paths"][0]["points"]) > 15
    vcmds = drawable_to_paint_commands(sp_vec, 10, 10, 40, 40, dark_bg=False)
    assert vcmds
    if vcmds[0]["op"] == "image":
        assert vcmds[0]["data"].startswith(b"\x89PNG")
        from PIL import Image
        import io
        rim = Image.open(io.BytesIO(vcmds[0]["data"])).convert("RGBA")
        vis_px = [p for p in (rim.get_flattened_data() if hasattr(rim, "get_flattened_data") else rim.getdata()) if p[3] > 200]
        assert vis_px and all(p[0] < 200 for p in vis_px)  # weiss auf hell abgedunkelt (#3b4963)
    else:
        assert vcmds[0]["op"] == "polygon" and vcmds[0]["fill"] != "#ffffff"

    sp_sel = rsv.resolve("@drawable/bg_btn")
    assert sp_sel and sp_sel["kind"] == "shape" and sp_sel["fill"] == "#2f6df0" and sp_sel["radius"] == 8.0

    sp_lay = rsv.resolve("@drawable/layers")
    assert sp_lay and sp_lay["kind"] == "shape" and sp_lay["shape"] == "oval"

    # Kompakt aneinandergeklebte Arc-Flags (a3,3 0 100,-6)
    subs = parse_svg_path_data("M10,10 a3,3 0 100,-6 Z")
    assert len(subs) == 1 and len(subs[0][0]) >= 5 and subs[0][1] is True


def test_layout_inflater_and_view_tree_bitmap_and_rn_source(tmp_path):
    res = tmp_path / "res"
    (res / "values").mkdir(parents=True)
    (res / "layout").mkdir(parents=True)
    (res / "drawable").mkdir(parents=True)
    (res / "values" / "colors.xml").write_text('<resources><color name="acc">#ff123456</color></resources>', encoding="utf-8")
    (res / "values" / "public.xml").write_text(
        '<resources><public type="layout" name="screen" id="0x7f0b0001" /></resources>',
        encoding="utf-8",
    )
    (res / "drawable" / "card_bg.xml").write_text(
        '<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">'
        '<solid android:color="#ffabcdef"/><corners android:radius="10dp"/></shape>',
        encoding="utf-8",
    )
    (res / "drawable" / "icon.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:viewportWidth="24" android:viewportHeight="24">'
        '<path android:fillColor="#ff222222" android:pathData="M2,2 L22,2 L12,22 Z"/></vector>',
        encoding="utf-8",
    )
    (res / "layout" / "screen.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" '
        'xmlns:app="http://schemas.android.com/apk/res-auto" android:orientation="vertical">'
        '<ImageView android:id="@+id/pic" app:srcCompat="@drawable/icon" />'
        '<Button android:id="@+id/ok" android:text="Go" android:background="@drawable/card_bg" />'
        '<TextView android:id="@+id/lbl" android:text="Hi" android:textColor="@color/acc" />'
        '</LinearLayout>',
        encoding="utf-8",
    )

    node = inflate_res(str(tmp_path), 0x7F0B0001)
    assert node is not None
    tree = ViewTree()
    tree.set_root(node)
    cmds = paint_commands(tree)
    assert any(c["op"] in ("polygon", "image") for c in cmds)
    assert any(c["op"] == "rect" and c.get("fill") == "#abcdef" for c in cmds)
    assert any(c["op"] == "text" and c.get("color") == "#123456" for c in cmds)

    # BitmapFactory.decodeByteArray -> ImageView.setImageBitmap
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))
    iv = JObject("Landroid/widget/ImageView;", {})
    stubs.resolve(iv.cls, "<init>", "(Landroid/content/Context;)V", [iv, None], False)
    ok_bm, bmp = stubs.resolve(
        "Landroid/graphics/BitmapFactory;", "decodeByteArray", "([BII)Landroid/graphics/Bitmap;",
        [JArray("[B", list(b"\x89PNG_BYTES")), 0, 10], True,
    )
    assert ok_bm and isinstance(bmp, JObject)
    stubs.resolve(iv.cls, "setImageBitmap", "(Landroid/graphics/Bitmap;)V", [iv, bmp], False)
    rt = ViewNode("LinearLayout"); rt.add(iv.fields["$view"]); vt.set_root(rt)
    bcmds = paint_commands(vt)
    assert any(c["op"] == "image" and c.get("data") == b"\x89PNG_BYTES" for c in bcmds)

    # RN source dict extraction
    rec = RnMountRecorder()
    n = rec.create_view(1, "RCTImageView", {"source": {"uri": "@drawable/icon"}})
    assert n.props.get("img") == "@drawable/icon"


def test_kotlin_coroutine_charset_and_float_bits_stubs():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)

    # Continuation.getContext + resumeWith
    cont = JObject("Lkotlin/coroutines/Continuation;", {})
    ok_ctx, cctx = stubs.resolve("Lkotlin/coroutines/Continuation;", "getContext",
                                 "()Lkotlin/coroutines/CoroutineContext;", [cont], False)
    assert ok_ctx and isinstance(cctx, JObject)
    ok_res, _ = stubs.resolve("Lkotlin/coroutines/Continuation;", "resumeWith",
                              "(Ljava/lang/Object;)V", [cont, JString("ok")], False)
    assert ok_res and stubs._txt(cont.fields.get("$result")) == "ok"

    # Charset.forName + StandardCharsets + Charsets Statics
    ok_cs, cs = stubs.resolve("Ljava/nio/charset/Charset;", "forName",
                              "(Ljava/lang/String;)Ljava/nio/charset/Charset;", [JString("utf-8")], True)
    assert ok_cs and isinstance(cs, JObject) and cs.fields["name"] == "UTF-8"
    assert vm.statics["Ljava/nio/charset/StandardCharsets;"]["UTF_8"].fields["name"] == "UTF-8"
    assert vm.statics["Lkotlin/text/Charsets;"]["UTF_8"].fields["name"] == "UTF-8"

    # Float / Double Bits
    ok_f, fval = stubs.resolve("Ljava/lang/Float;", "intBitsToFloat", "(I)F", [0x3E19999A], True)
    assert ok_f and abs(fval - 0.15) < 0.001
    ok_bi, bits = stubs.resolve("Ljava/lang/Float;", "floatToIntBits", "(F)I", [0.15], True)
    assert ok_bi and bits == 0x3E19999A


def test_theme_window_bg_vector_hole_cutout_white_contrast_and_compose_image(tmp_path):
    from PIL import Image
    import io
    from core.java.drawable_loader import rasterize_vector_to_png, prepare_pil_rgba

    res = tmp_path / "res"
    (res / "values").mkdir(parents=True)
    (res / "drawable").mkdir(parents=True)
    (res / "layout").mkdir(parents=True)
    (tmp_path / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="t.app">'
        '<application android:theme="@style/AppTheme"><activity android:name=".Main">'
        '<intent-filter><action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/></intent-filter>'
        '</activity></application></manifest>',
        encoding="utf-8",
    )
    (res / "values" / "colors.xml").write_text(
        '<resources><color name="bg_blue">#ff5687c1</color></resources>', encoding="utf-8"
    )
    (res / "values" / "styles.xml").write_text(
        '<resources><style name="AppTheme" parent="Base"><item name="android:windowBackground">@color/bg_blue</item></style></resources>',
        encoding="utf-8",
    )
    (res / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="drawable" name="donut" id="0x7f080010" />'
        '<public type="drawable" name="banner" id="0x7f080011" />'
        '<public type="layout" name="tile" id="0x7f0b0010" />'
        '</resources>',
        encoding="utf-8",
    )
    # Vector mit innerem Loch (Outer 2..22, Inner-Loch 8..16) -> Mitte muss transparent bleiben!
    (res / "drawable" / "donut.xml").write_text(
        '<vector xmlns:android="http://schemas.android.com/apk/res/android" '
        'android:viewportWidth="24" android:viewportHeight="24">'
        '<path android:fillColor="#ffffffff" '
        'android:pathData="M2,2 L22,2 L22,22 L2,22 Z M8,8 L16,8 L16,16 L8,16 Z"/>'
        '</vector>',
        encoding="utf-8",
    )
    # Weisses Raster-Icon + Layout mit Icon-Button & sichtbarem Label
    white_im = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
    for x in range(4, 20):
        for y in range(4, 20):
            white_im.putpixel((x, y), (255, 255, 255, 255))
    white_im.save(res / "drawable" / "banner.png")
    (res / "layout" / "tile.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" android:orientation="vertical">'
        '<Button android:id="@+id/b" android:layout_width="80dp" android:layout_height="80dp" android:background="@drawable/donut"/>'
        '<TextView android:id="@+id/t" android:text="Parents"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )

    rsv = DrawableResolver(str(tmp_path))
    wbg, wdark = rsv.resolve_window_background()
    assert wbg == "#5687c1" and wdark is True

    # Loch-Ausschnitt im Donut pruefen (Mitte transparent alpha==0, Ring alpha>200)
    spec_d = rsv.resolve("donut")
    png_b = rasterize_vector_to_png(spec_d, 64, 64, dark_bg=True)
    assert png_b
    dim = Image.open(io.BytesIO(png_b)).convert("RGBA")
    assert dim.getpixel((32, 32))[3] == 0
    assert dim.getpixel((10, 32))[3] > 200 and dim.getpixel((10, 32))[:3] == (255, 255, 255)

    # Pure-White-Silhouette Kontrastschutz auf hellem vs dunklem Hintergrund
    tinted_light = prepare_pil_rgba(white_im, dark_bg=False)
    assert tinted_light.getpixel((12, 12))[:3] == (59, 73, 99)
    untinted_dark = prepare_pil_rgba(white_im, dark_bg=True)
    assert untinted_dark.getpixel((12, 12))[:3] == (255, 255, 255)

    # Icon-Button dupliziert Geschwister-TextView NICHT, behaelt a11y_label
    node = inflate_res(str(tmp_path), 0x7F0B0010)
    assert node.children[0].text == ""
    assert node.children[0].props.get("a11y_label") == "Parents"
    assert node.children[1].text == "Parents"

    # Compose ImageKt.Image (matchParentSize Background + direktes ImageView)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))
    assert vt.window_bg == "#5687c1" and vt.window_bg_dark is True
    # Compose Box starten & Image mit painterResource(0x7F080011) + matchParentSize aufrufen
    comp_cls = "Landroidx/compose/runtime/ComposerImpl;"
    comp_obj = JObject(comp_cls, {})
    def _cdispatch(rcls, cls, name, sig, args, static=False):
        for h in vm._method_hooks:
            ok, res = h(rcls, cls, name, sig, args, static, vm)
            if ok:
                return res
        return None
    _cdispatch(comp_cls, comp_cls, "startReusableNode", "(Lkotlin/jvm/functions/Function0;)V", (comp_obj, None), False)
    mod_base = _cdispatch("Landroidx/compose/ui/Modifier$Companion;", "Landroidx/compose/ui/Modifier;", "then",
                          "(Landroidx/compose/ui/Modifier;)Landroidx/compose/ui/Modifier;", (None,), False)
    mod_mp = _cdispatch("Landroidx/compose/foundation/layout/BoxScope;", "Landroidx/compose/foundation/layout/BoxScope;",
                        "matchParentSize", "(Landroidx/compose/ui/Modifier;)Landroidx/compose/ui/Modifier;",
                        (None, mod_base), False)
    painter = _cdispatch("Landroidx/compose/ui/res/PainterResources_androidKt;",
                         "Landroidx/compose/ui/res/PainterResources_androidKt;", "painterResource",
                         "(ILandroidx/compose/runtime/Composer;I)Landroidx/compose/ui/graphics/painter/Painter;",
                         (0x7F080011, comp_obj, 0), True)
    _cdispatch("Landroidx/compose/foundation/ImageKt;", "Landroidx/compose/foundation/ImageKt;", "Image",
               "(Landroidx/compose/ui/graphics/painter/Painter;Ljava/lang/String;Landroidx/compose/ui/Modifier;Ljava/lang/Object;Ljava/lang/Object;FLjava/lang/Object;Landroidx/compose/runtime/Composer;II)V",
               (painter, JString("BG"), mod_mp, 0, 0, 0x3E19999A, 0, comp_obj, 0, 0x48), True)
    # Zweites foreground Image ohne matchParentSize
    _cdispatch("Landroidx/compose/foundation/ImageKt;", "Landroidx/compose/foundation/ImageKt;", "Image",
               "(Landroidx/compose/ui/graphics/painter/Painter;Ljava/lang/String;Landroidx/compose/ui/Modifier;Ljava/lang/Object;Ljava/lang/Object;FLjava/lang/Object;Landroidx/compose/runtime/Composer;II)V",
               (painter, JString("Logo"), mod_base, 0, 0, 0, 0, comp_obj, 0, 0x60), True)
    pcmds = paint_commands(vt)
    bg_imgs = [c for c in pcmds if c.get("op") == "image" and abs(float(c.get("alpha", 1.0)) - 0.15) < 0.02]
    fg_imgs = [c for c in pcmds if c.get("op") == "image" and float(c.get("alpha", 1.0)) > 0.9]
    assert bg_imgs and fg_imgs


def test_hidden_parent_hides_children_from_paint_and_hit_testing():
    root = ViewNode("LinearLayout", props={"orientation": 1})
    hidden_box = ViewNode("LinearLayout", visible=False)
    ghost_btn = ViewNode("Button", vid="ghost_btn", text="Ghost", clickable=True)
    ghost_tv = ViewNode("TextView", vid="ghost_tv", text="Update available")
    hidden_box.add(ghost_btn)
    hidden_box.add(ghost_tv)
    vis_tv = ViewNode("TextView", vid="vis_tv", text="Visible")
    root.add(hidden_box)
    root.add(vis_tv)

    tree = ViewTree()
    tree.set_root(root)
    tree.layout()
    # find()/nodes() behalten GONE-Nodes fuer findViewById/setVisibility, aber visible_nodes/paint/hit_test nicht
    assert tree.root.find("ghost_btn") is ghost_btn
    assert ghost_tv not in tree.visible_nodes()
    cmds = paint_commands(tree)
    assert all(c.get("text") not in ("Ghost", "Update available") for c in cmds)
    assert tree.clickable_at(5, 5) is None


def test_invoke_virtual_superclass_and_stubs():
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime

    class _Prov:
        def get_class(self, desc):
            if desc == "Lbase/Act;":
                return [MethodDef("Lbase/Act;", "onBackPressed", "()V", [
                    ("const", 0, 1),
                    ("iput", 0, 1, "Lbase/Act;", "called"),
                    ("return-void",),
                ], nregs=2, nargs=1, static=False)]
            return []

        def get_super(self, desc):
            return {
                "Lsub/ChildAct;": "Lbase/Act;",
                "Lbase/Act;": "Landroid/app/Activity;",
                "Lplain/Act;": "Landroid/app/Activity;",
            }.get(desc, "Ljava/lang/Object;")

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    child = JObject("Lsub/ChildAct;", {})
    vm.invoke_virtual(child, "onBackPressed", "()V", (child,))
    assert child.fields.get("called") == 1

    child2 = JObject("Lsub/ChildAct;", {})
    vm.invoke("Lsub/ChildAct;", "onBackPressed", "()V", (child2,))
    assert child2.fields.get("called") == 1

    assert vm.invoke_virtual(child, "doesNotExist", "()V", (child,)) in (None, 0)
    vm_bare = DalvikVM()
    assert vm_bare.invoke_virtual(child, "doesNotExist", "()V", (child,)) is None

    plain = JObject("Lplain/Act;", {})
    stubs._current_activity = plain
    vm.invoke_virtual(plain, "onBackPressed", "()V", (plain,))
    assert stubs._activity_finished is True
    assert plain.fields.get("_finishing") == 1


def test_per_activity_window_and_intent_isolation():
    from core.java.dalvik import DalvikVM
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    a1 = JObject("Lapp/A1;", {})
    a2 = JObject("Lapp/A2;", {})
    w1 = stubs._mk_window((a1,))
    w2 = stubs._mk_window((a2,))
    assert w1 is not w2
    assert w1.fields["callback"] is not w2.fields["callback"]
    assert stubs._mk_window((a1,)) is w1

    intent2 = JObject("Landroid/content/Intent;", {"action": "CUSTOM"})
    a2.fields["_intent"] = intent2
    assert stubs._mk_intent((a2,)) is intent2
    assert stubs._mk_intent((a1,)) is not intent2


def test_activity_back_stack_home_and_asynctask():
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import LifecycleRunner, _StubRuntime
    from core.java.view_tree import ViewTree, ViewNode, install_view_stubs

    trace = []

    class _Prov:
        def get_class(self, desc):
            if desc == "Lapp/MainAct;":
                return [MethodDef("Lapp/MainAct;", "onCreate", "(Landroid/os/Bundle;)V", [
                    ("const", 0, 1),
                    ("iput", 0, 1, "Lapp/MainAct;", "created"),
                    ("return-void",),
                ], nregs=3, nargs=2, static=False)]
            if desc == "Lapp/SubAct;":
                return [
                    MethodDef("Lapp/SubAct;", "onCreate", "(Landroid/os/Bundle;)V", [
                        ("const", 0, 1),
                        ("iput", 0, 1, "Lapp/SubAct;", "created"),
                        ("return-void",),
                    ], nregs=3, nargs=2, static=False),
                ]
            if desc == "Lapp/ChildAct;":
                return [
                    MethodDef("Lapp/ChildAct;", "onCreate", "(Landroid/os/Bundle;)V", [
                        ("const", 0, 1),
                        ("iput", 0, 1, "Lapp/ChildAct;", "created"),
                        ("return-void",),
                    ], nregs=3, nargs=2, static=False),
                    # Simuliert explizites startActivity(MainAct) + finish() beim Back (wie AndroidX OnBackPressedCallback)
                    MethodDef("Lapp/ChildAct;", "onBackPressed", "()V", [
                        ("new-instance", 0, "Landroid/content/Intent;"),
                        ("const-class", 1, "Lapp/MainAct;"),
                        ("invoke-direct", [0, 3, 1], "Landroid/content/Intent;", "<init>", "(Landroid/content/Context;Ljava/lang/Class;)V"),
                        ("invoke-virtual", [3, 0], "Landroid/app/Activity;", "startActivity", "(Landroid/content/Intent;)V"),
                        ("invoke-virtual", [3], "Landroid/app/Activity;", "finish", "()V"),
                        ("return-void",),
                    ], nregs=4, nargs=1, static=False),
                ]
            if desc == "Lapp/Task;":
                return [
                    MethodDef("Lapp/Task;", "onPreExecute", "()V", [
                        ("const-string", 0, "pre"),
                        ("invoke-static", [0], "Lapp/Hook;", "log", "(Ljava/lang/String;)V"),
                        ("return-void",),
                    ], nregs=2, nargs=1, static=False),
                    MethodDef("Lapp/Task;", "doInBackground", "([Ljava/lang/Object;)Ljava/lang/Object;", [
                        ("const-string", 0, "bg"),
                        ("invoke-static", [0], "Lapp/Hook;", "log", "(Ljava/lang/String;)V"),
                        ("return-object", 0),
                    ], nregs=3, nargs=2, static=False),
                    MethodDef("Lapp/Task;", "onPostExecute", "(Ljava/lang/Object;)V", [
                        ("const-string", 0, "post"),
                        ("invoke-static", [0], "Lapp/Hook;", "log", "(Ljava/lang/String;)V"),
                        ("return-void",),
                    ], nregs=3, nargs=2, static=False),
                ]
            return []

        def get_super(self, desc):
            return {
                "Lapp/MainAct;": "Landroid/app/Activity;",
                "Lapp/SubAct;": "Landroid/app/Activity;",
                "Lapp/ChildAct;": "Landroid/app/Activity;",
                "Lapp/Task;": "Landroid/os/AsyncTask;",
            }.get(desc, "Ljava/lang/Object;")

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    vt = install_view_stubs(stubs)
    stubs.register("Lapp/Hook;", "log", "(Ljava/lang/String;)V",
                   lambda a: trace.append(stubs._txt(a[0])))

    runner = LifecycleRunner(vm, stubs=stubs)
    runner._run_class("Lapp/MainAct;", runner.SEQ_ACTIVITY, role="activity")
    main_root = ViewNode("LinearLayout")
    main_root.add(ViewNode("TextView", text="MainScreen"))
    vt.set_root(main_root)

    # Navigation zu SubAct -> push MainAct, neuer Screen sub_root
    stubs._pending_nav_target = "Lapp/SubAct;"
    assert runner.navigate_to_pending() is True
    assert len(runner.activity_stack) == 1
    assert stubs._current_activity.cls == "Lapp/SubAct;"
    sub_root = ViewNode("LinearLayout")
    sub_root.add(ViewNode("TextView", text="SubScreen"))
    vt.set_root(sub_root)

    # Zurueck per Activity.onBackPressed() Stub -> pop -> MainScreen wiederhergestellt
    assert runner.handle_back() is True
    assert stubs._current_activity.cls == "Lapp/MainAct;"
    assert vt.root is main_root
    assert runner.activity_stack == []

    # Navigation MainAct -> SubAct -> ChildAct -> Back (via startActivity(MainAct)+finish())
    stubs._pending_nav_target = "Lapp/SubAct;"
    runner.navigate_to_pending()
    vt.set_root(sub_root)
    stubs._pending_nav_target = "Lapp/ChildAct;"
    runner.navigate_to_pending()
    assert len(runner.activity_stack) == 2
    assert runner.handle_back() is True
    assert stubs._current_activity.cls == "Lapp/MainAct;"
    assert runner.activity_stack == []  # Stack bereinigt (kein Main -> Sub -> Child -> Main Zyklus)
    vt.set_root(main_root)  # MainAct.onCreate haette setContentView ausgefuehrt

    # Home von tiefem Stack stellt Root (MainScreen) wieder her
    stubs._pending_nav_target = "Lapp/SubAct;"
    runner.navigate_to_pending()
    vt.set_root(sub_root)
    stubs._pending_nav_target = "Lapp/ChildAct;"
    runner.navigate_to_pending()
    assert runner.handle_home() is True
    assert stubs._current_activity.cls == "Lapp/MainAct;"
    assert vt.root is main_root
    assert runner.activity_stack == []

    # AsyncTask.execute fuehrt onPreExecute -> doInBackground -> onPostExecute aus
    task = JObject("Lapp/Task;", {})
    stubs._asynctask_exec((task, None))
    assert trace == ["pre", "bg", "post"]


def test_string_reader_writer_and_buffered_readline():
    from core.java.dalvik import DalvikVM
    from core.java.objects import JObject, JString, JArray
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    # 1. StringReader inkl. Surrogate-Paar (Emoji) + echtes EOF (-1, nie 0)
    sr = JObject("Ljava/io/StringReader;", {})
    ok, _ = stubs.resolve("Ljava/io/StringReader;", "<init>", "(Ljava/lang/String;)V",
                          (sr, JString("Hi \U0001f46b!")), False)
    assert ok
    cbuf = JArray("[C", [0] * 16)
    ok, n = stubs.resolve("Ljava/io/Reader;", "read", "([CII)I", (sr, cbuf, 0, 16), False)
    assert ok and n == 6
    b = bytearray()
    for u in cbuf.data[:n]:
        b.extend((u & 0xFF, (u >> 8) & 0xFF))
    assert b.decode("utf-16-le") == "Hi \U0001f46b!"
    ok, eof = stubs.resolve("Ljava/io/Reader;", "read", "([CII)I", (sr, cbuf, 0, 16), False)
    assert ok and eof == -1

    # 2. BufferedReader.readLine() mit \r\n + EOF -> None
    sr2 = JObject("Ljava/io/StringReader;", {})
    stubs.resolve("Ljava/io/StringReader;", "<init>", "(Ljava/lang/String;)V",
                  (sr2, JString("line1\r\nline2")), False)
    br = JObject("Ljava/io/BufferedReader;", {})
    stubs.resolve("Ljava/io/BufferedReader;", "<init>", "(Ljava/io/Reader;)V", (br, sr2), False)
    ok, l1 = stubs.resolve("Ljava/io/BufferedReader;", "readLine", "()Ljava/lang/String;", (br,), False)
    assert ok and stubs._txt(l1) == "line1"
    ok, l2 = stubs.resolve("Ljava/io/BufferedReader;", "readLine", "()Ljava/lang/String;", (br,), False)
    assert ok and stubs._txt(l2) == "line2"
    ok, l3 = stubs.resolve("Ljava/io/BufferedReader;", "readLine", "()Ljava/lang/String;", (br,), False)
    assert ok and l3 is None

    # 3. StringWriter Round-Trip
    sw = JObject("Ljava/io/StringWriter;", {})
    stubs.resolve("Ljava/io/StringWriter;", "<init>", "()V", (sw,), False)
    stubs.resolve("Ljava/io/StringWriter;", "write", "(Ljava/lang/String;)V", (sw, JString("AB")), False)
    stubs.resolve("Ljava/io/StringWriter;", "write", "(I)V", (sw, ord("!")), False)
    ok, out = stubs.resolve("Ljava/io/StringWriter;", "toString", "()Ljava/lang/String;", (sw,), False)
    assert ok and stubs._txt(out) == "AB!"


def test_class_modifiers_and_fragment_checks():
    from core.java.dalvik import DalvikVM
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    cls_top = JObject("Ljava/lang/Class;", {"name": "com.example.SettingsFragment"})
    ok, mod = stubs.resolve("Ljava/lang/Class;", "getModifiers", "()I", (cls_top,), False)
    assert ok and mod == 1
    assert stubs.resolve("Ljava/lang/reflect/Modifier;", "isPublic", "(I)Z", (mod,), True) == (True, 1)
    assert stubs.resolve("Ljava/lang/Class;", "isAnonymousClass", "()Z", (cls_top,), False) == (True, 0)
    assert stubs.resolve("Ljava/lang/Class;", "isMemberClass", "()Z", (cls_top,), False) == (True, 0)

    cls_inner = JObject("Ljava/lang/Class;", {"name": "com.example.Host$InnerFragment"})
    ok, imod = stubs.resolve("Ljava/lang/Class;", "getModifiers", "()I", (cls_inner,), False)
    assert ok and imod == 9
    assert stubs.resolve("Ljava/lang/Class;", "isMemberClass", "()Z", (cls_inner,), False) == (True, 1)
    assert stubs.resolve("Ljava/lang/reflect/Modifier;", "isStatic", "(I)Z", (imod,), True) == (True, 1)

    cls_anon = JObject("Ljava/lang/Class;", {"name": "com.example.Host$1"})
    assert stubs.resolve("Ljava/lang/Class;", "isAnonymousClass", "()Z", (cls_anon,), False) == (True, 1)

    # Hintergrund-Activity.finish() beendet nur alten Stack-Eintrag, nicht die neue Top-Activity
    old_act = JObject("Lapp/OldAct;", {})
    new_act = JObject("Lapp/NewAct;", {})
    stubs._current_activity = new_act
    stubs._activity_finished = False
    stubs._activity_stack = [{"activity": old_act, "cls": "Lapp/OldAct;", "root": None}]
    stubs._finish_act((old_act,))
    assert stubs._activity_finished is False
    assert old_act.fields.get("_finishing") == 1
    assert stubs._activity_stack == []


def test_class_getname_equality_and_aget_bounds():
    from core.java.dalvik import DalvikVM, MethodDef, _eq
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    c1 = vm.intern_class("Lcom/example/Foo;")
    c2 = vm.intern_class("com.example.Foo")
    assert c1 is c2
    assert _eq(JObject("Ljava/lang/Class;", {"name": "Lcom/example/Foo;"}), c2)

    ok, gname = stubs.resolve("Ljava/lang/Class;", "getName", "()Ljava/lang/String;", (c1,), False)
    assert ok and gname.text == "com.example.Foo"
    ok, sname = stubs.resolve("Ljava/lang/Class;", "getSimpleName", "()Ljava/lang/String;", (c1,), False)
    assert ok and sname.text == "Foo"

    # Out-of-bounds aget wirft ArrayIndexOutOfBoundsException statt Endlosschleife mit 0
    vm.register_method(
        "Lt/Arr;", "oob", "()I",
        [
            ("const", 0, 1),
            ("new-array", 1, 0, "[I"),
            ("const", 2, 1),
            ("aget", 3, 1, 2),
            ("return", 3),
        ],
        nregs=4, nargs=0, static=True,
    )
    vm.registry[("Lt/Arr;", "oob", "()I")].handlers = [(3, 4, "Ljava/lang/ArrayIndexOutOfBoundsException;", 5)]
    vm.registry[("Lt/Arr;", "oob", "()I")].insns.extend([("const", 3, 77), ("return", 3)])
    assert vm.invoke("Lt/Arr;", "oob", "()I", ()) == 77


def test_multiline_textview_and_background_viewport_clipping(tmp_path):
    from PIL import Image
    from core.java.view_tree import ViewTree, ViewNode, paint_commands, _measure
    from core.java.surface import SceneGraphSurface
    from tools.surface_raster import render_surface

    bg_png = tmp_path / "logo_bg.png"
    Image.new("RGBA", (200, 200), (255, 120, 40, 255)).save(bg_png)

    root = ViewNode("FrameLayout", props={
        "bg_drawable": {"kind": "raster", "path": str(bg_png), "name": "logo_bg", "is_9patch": False},
        "bg_alpha": 0.25,
        "bg_scale": "fit",
        "bg_viewport": True,
    })
    long_tv = ViewNode("TextView", text=(
        "This is a deliberately very long paragraph that must wrap cleanly across multiple lines "
        "inside the 380px viewport instead of clipping outside the screen border."
    ), props={"gravity": 1})
    root.add(long_tv)
    for idx in range(25):
        root.add(ViewNode("TextView", text=f"Row {idx}"))

    tree = ViewTree(width=380, height=776)
    tree.set_root(root)
    tree.layout()

    assert _measure(long_tv, 360) > 60
    cmds = paint_commands(tree)
    bg_imgs = [c for c in cmds if c.get("op") == "image"]
    assert bg_imgs and bg_imgs[0]["scale"] == "fit" and bg_imgs[0].get("fixed_y") is True
    tv_cmds = [c for c in cmds if c.get("op") == "text" and "deliberately" in c.get("text", "")]
    assert tv_cmds and tv_cmds[0]["max_w"] > 100 and tv_cmds[0]["justify"] == "center"

    im = render_surface(SceneGraphSurface(tree), scroll_y=200)
    # Navigationsleiste ganz unten (BEZEL + SCREEN_H = 816..872) darf NICHT vom Hintergrund uebermalt sein
    px_nav = im.getpixel((100, 840))
    assert px_nav == (21, 24, 29)


def test_debugger_net_log_save_load_and_fragment_mounting(tmp_path):
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.objects import JObject
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.net_recorder import NetLog, NetRequest, NetResponse
    from ui.controllers.debugger_controller import DebuggerController

    # 1. Netz-Log Save/Load + Replay-Aktivierung
    ctrl = DebuggerController(app=None)
    ctrl.net_log = NetLog()
    ctrl.net_log.record(NetRequest("POST", "https://api.test/login?v=1", {"Accept": "application/json"}, b"{}"),
                        NetResponse(201, {"Content-Type": "application/json"}, b'{"ok":true}', "Created"))
    out_json = tmp_path / "net_export.json"
    assert ctrl.save_net_log_json(str(out_json)) == 1

    ctrl2 = DebuggerController(app=None)
    assert ctrl2.load_net_log_file(str(out_json), as_replay=True) == 1
    assert ctrl2.net_mode == "mock"
    matched = ctrl2._match_net_mock(NetRequest("POST", "https://api.test/login?v=1", {}, b""))
    assert matched is not None and matched.code == 201 and b'"ok":true' in matched.body

    # 2. Fragment-Mounting + ViewGroup getChildCount/findViewById
    layout_dir = tmp_path / "res" / "layout"
    values_dir = tmp_path / "res" / "values"
    layout_dir.mkdir(parents=True)
    values_dir.mkdir(parents=True)
    (values_dir / "public.xml").write_text(
        '<resources>'
        '<public type="layout" name="act_main" id="0x7f0d0001" />'
        '<public type="layout" name="frag_view" id="0x7f0d0002" />'
        '<public type="id" name="frag_slot" id="0x7f0a0001" />'
        '<public type="id" name="frag_btn" id="0x7f0a0002" />'
        '</resources>',
        encoding="utf-8",
    )
    (layout_dir / "act_main.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<fragment android:id="@+id/frag_slot" android:name="app.TestFrag" />'
        '</LinearLayout>',
        encoding="utf-8",
    )
    (layout_dir / "frag_view.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<Button android:id="@+id/frag_btn" android:text="InsideFrag" />'
        '</LinearLayout>',
        encoding="utf-8",
    )

    class _FragProv:
        def get_class(self, desc):
            if desc == "Lapp/TestFrag;":
                return [
                    MethodDef("Lapp/TestFrag;", "<init>", "()V", [("return-void",)], nregs=1, nargs=1, static=False),
                    MethodDef("Lapp/TestFrag;", "onCreateView",
                              "(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;",
                              [
                                  ("const", 0, 0x7F0D0002),
                                  ("const", 1, 0),
                                  ("invoke-virtual", [3, 0, 5, 1], "Landroid/view/LayoutInflater;",
                                   "inflate", "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"),
                                  ("move-result-object", 0),
                                  ("return-object", 0),
                              ],
                              nregs=7, nargs=4, static=False),
                ]
            return []

        def get_super(self, desc):
            return "Landroidx/fragment/app/Fragment;" if desc == "Lapp/TestFrag;" else "Ljava/lang/Object;"

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _FragProv()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))

    act = JObject("Landroid/app/Activity;", {})
    stubs._current_activity = act
    stubs.resolve("Landroid/app/Activity;", "setContentView", "(I)V", (act, 0x7F0D0001), False)
    assert vt.root is not None
    assert vt.root.find("frag_btn") is not None
    assert vt.root.find("frag_btn").text == "InsideFrag"
    assert "frag_slot" in stubs._fragments_by_id
    assert 0x7F0A0001 in stubs._fragments_by_id


def test_sparsearray_typedarray_android_r_ids_and_recyclerview_adapter(tmp_path):
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.objects import JObject, JString
    from core.java.layout_inflater import build_id_map, inflate_res
    from core.java.view_tree import ViewNode, ViewTree, install_view_stubs

    # 1. android.R.id.* verfuegbar + clickable_at delegiert Kachel-Label auf Einzel-Button
    idmap = build_id_map(str(tmp_path))
    assert idmap.get(0x0102003F) == "list_container"
    assert idmap.get(0x01020016) == "title"
    assert idmap.get(0x01020010) == "summary"

    vt_tile = ViewTree()
    root_tile = ViewNode("LinearLayout")
    tile = root_tile.add(ViewNode("RelativeLayout"))
    btn = tile.add(ViewNode("Button", vid="b_set", text="", clickable=True))
    lbl = tile.add(ViewNode("TextView", vid="t_set", text="Settings", clickable=False))
    vt_tile.set_root(root_tile)
    lx, ly, lw, lh = lbl.bounds
    assert vt_tile.clickable_at(lx + lw // 2, ly + lh // 2) is btn

    # 2. SparseArray, TypedArray-Defaults & RecyclerView.setAdapter-Materialisierung
    layout_dir = tmp_path / "res" / "layout"
    values_dir = tmp_path / "res" / "values"
    layout_dir.mkdir(parents=True)
    values_dir.mkdir(parents=True)
    (values_dir / "public.xml").write_text(
        '<resources>'
        '<public type="layout" name="row_item" id="0x7f0d0010" />'
        '</resources>',
        encoding="utf-8",
    )
    (layout_dir / "row_item.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<TextView android:id="@android:id/title" android:text="Placeholder"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )

    class _RvProv:
        def get_class(self, desc):
            if desc == "Lapp/TestAdapter;":
                return [
                    MethodDef(desc, "getItemCount", "()I", [("const", 0, 2), ("return", 0)], nregs=2, nargs=1),
                    MethodDef(desc, "getItemViewType", "(I)I", [("const", 0, 0), ("return", 0)], nregs=3, nargs=2),
                    MethodDef(
                        desc, "onCreateViewHolder", "(Landroid/view/ViewGroup;I)Lapp/VH;",
                        [
                            ("const", 0, 0x7F0D0010),
                            ("const", 1, 0),
                            ("invoke-static", [3], "Landroid/view/LayoutInflater;", "from",
                             "(Landroid/content/Context;)Landroid/view/LayoutInflater;"),
                            ("move-result-object", 2),
                            ("invoke-virtual", [2, 0, 4, 1], "Landroid/view/LayoutInflater;", "inflate",
                             "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"),
                            ("move-result-object", 2),
                            ("new-instance", 0, "Lapp/VH;"),
                            ("invoke-direct", [0, 2], "Lapp/VH;", "<init>", "(Landroid/view/View;)V"),
                            ("return-object", 0),
                        ],
                        nregs=6, nargs=3,
                    ),
                    MethodDef(
                        desc, "onBindViewHolder", "(Lapp/VH;I)V",
                        [
                            ("iget-object", 0, 4, "Lapp/VH;", "itemView", "Landroid/view/View;"),
                            ("const", 1, 0x01020016),
                            ("invoke-virtual", [0, 1], "Landroid/view/View;", "findViewById", "(I)Landroid/view/View;"),
                            ("move-result-object", 0),
                            ("const-string", 1, "Eintrag"),
                            ("invoke-virtual", [0, 1], "Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V"),
                            ("return-void",),
                        ],
                        nregs=6, nargs=3,
                    ),
                ]
            if desc == "Lapp/VH;":
                return [
                    MethodDef(
                        desc, "<init>", "(Landroid/view/View;)V",
                        [
                            ("iput-object", 1, 0, desc, "itemView", "Landroid/view/View;"),
                            ("return-void",),
                        ],
                        nregs=2, nargs=2,
                    )
                ]
            return []

        def get_super(self, desc):
            return "Ljava/lang/Object;"

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _RvProv()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))

    # SparseArray + TypedArray
    sa = JObject("Landroid/util/SparseArray;", {})
    stubs.resolve("Landroid/util/SparseArray;", "<init>", "()V", [sa], False)
    stubs.resolve("Landroid/util/SparseArray;", "put", "(ILjava/lang/Object;)V", [sa, 0x01020016, JString("T")], False)
    ok_g, got = stubs.resolve("Landroid/util/SparseArray;", "get", "(I)Ljava/lang/Object;", [sa, 0x01020016], False)
    assert ok_g and got.text == "T"

    act = JObject("Landroid/app/Activity;", {})
    ok_ta, ta = stubs.resolve("Landroid/content/Context;", "obtainStyledAttributes",
                              "([I)Landroid/content/res/TypedArray;", [act, None], False)
    assert ok_ta and isinstance(ta, JObject)
    ok_rid, rid_val = stubs.resolve("Landroid/content/res/TypedArray;", "getResourceId", "(II)I", [ta, 0, 0x7F0D0010], False)
    assert ok_rid and rid_val == 0x7F0D0010

    # RecyclerView.setAdapter materialisiert Items in den ViewTree
    rv_node = ViewNode("RecyclerView", vid="recycler_view")
    vt.set_root(rv_node)
    rv_obj = JObject("Landroidx/recyclerview/widget/RecyclerView;", {"$view": rv_node})
    adp_obj = JObject("Lapp/TestAdapter;", {})
    for h in vm._method_hooks:
        ok_rv, _ = h(rv_obj.cls, rv_obj.cls, "setAdapter", "(Landroidx/recyclerview/widget/RecyclerView$Adapter;)V",
                      [rv_obj, adp_obj], False, vm)
        if ok_rv:
            break
    texts = [n.text for n in rv_node.walk() if n.text]
    assert texts == ["Eintrag", "Eintrag"]


def test_arrays_chained_strings_collapsed_nodes_and_contrast_guard(tmp_path):
    from core.java.layout_inflater import build_array_map, build_string_map, inflate_res
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import ViewNode, ViewTree, install_view_stubs, paint_commands
    from core.java.objects import JObject

    # APKEditor-Layout unter resources/package_1/res + verkettete @string-Aliase + arrays.xml
    pkg_res = tmp_path / "resources" / "package_1" / "res"
    (pkg_res / "values").mkdir(parents=True)
    (pkg_res / "layout").mkdir(parents=True)
    (pkg_res / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="array" name="hero_titles" id="0x7f030001" />'
        '<public type="layout" name="screen_main" id="0x7f0d0020" />'
        '</resources>',
        encoding="utf-8",
    )
    (pkg_res / "values" / "strings.xml").write_text(
        '<resources>'
        '<string name="base_forgot">Forgot your password?</string>'
        '<string name="action_forgot">@string/base_forgot</string>'
        '</resources>',
        encoding="utf-8",
    )
    (pkg_res / "values" / "arrays.xml").write_text(
        '<resources>'
        '<string-array name="hero_titles"><item>Slide 1</item><item>Slide 2</item></string-array>'
        '</resources>',
        encoding="utf-8",
    )
    (pkg_res / "layout" / "screen_main.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<androidx.constraintlayout.widget.Guideline android:id="@+id/gl" android:layout_width="0dp" android:layout_height="wrap_content"/>'
        '<ImageView android:id="@+id/logo" android:src="@null" android:layout_width="100dp" android:layout_height="40dp"/>'
        '<View android:id="@+id/divider" android:layout_width="match_parent" android:layout_height="1dp"/>'
        '<TextView android:id="@+id/forgot" android:text="@string/action_forgot" android:textColor="#ffffff" android:clickable="true"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )

    _, name2text = build_string_map(str(tmp_path))
    assert name2text["action_forgot"] == "Forgot your password?"
    id2arr, _ = build_array_map(str(tmp_path))
    assert id2arr[0x7F030001] == ["Slide 1", "Slide 2"]

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))
    res_obj = JObject("Landroid/content/res/Resources;", {})
    vm.register_method(
        "Lapp/ResCaller;", "arr", "(Landroid/content/res/Resources;)[Ljava/lang/String;",
        [
            ("const", 0, 0x7F030001),
            ("invoke-virtual", [1, 0], "Landroid/content/res/Resources;", "getStringArray", "(I)[Ljava/lang/String;"),
            ("move-result-object", 0),
            ("return-object", 0),
        ],
        nregs=2, nargs=1, static=True,
    )
    jarr = vm.invoke("Lapp/ResCaller;", "arr", "(Landroid/content/res/Resources;)[Ljava/lang/String;", (res_obj,))
    assert jarr is not None and [x.text for x in jarr.data] == ["Slide 1", "Slide 2"]

    inflated = inflate_res(str(tmp_path), 0x7F0D0020)
    assert inflated is not None
    forgot_n = inflated.find("forgot")
    assert forgot_n is not None and forgot_n.text == "Forgot your password?"
    assert inflated.find("gl").visible is False
    assert inflated.find("logo").props.get("src_null") is True

    vt.set_root(inflated)
    vt.layout()
    cmds = paint_commands(vt)
    # Kein bogus "IMG"-Platzhalter fuer android:src="@null"
    assert not any(c.get("op") == "text" and c.get("text") == "IMG" for c in cmds)
    # Contrast-Guard verdunkelt #ffffff-Text auf hellem Hintergrund automatisch
    forgot_txt = [c for c in cmds if c.get("op") == "text" and "Forgot your password?" in str(c.get("text", ""))]
    assert forgot_txt and forgot_txt[0]["color"].lower() != "#ffffff"


def test_collection_addall_spannable_queued_fragment_viewpager_and_constraintset(tmp_path):
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.objects import JObject, JString
    from core.java.view_tree import ViewNode, install_view_stubs

    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "layout").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="layout" name="host_vp" id="0x7f0d0030" />'
        '<public type="layout" name="frag_page" id="0x7f0d0031" />'
        '<public type="layout" name="page_card" id="0x7f0d0032" />'
        '<public type="id" name="slot" id="0x7f0a0030" />'
        '<public type="id" name="vp" id="0x7f0a0031" />'
        '<public type="id" name="secret_box" id="0x7f0a0032" />'
        '</resources>',
        encoding="utf-8",
    )
    (tmp_path / "res" / "layout" / "host_vp.xml").write_text(
        '<androidx.constraintlayout.widget.ConstraintLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<FrameLayout android:id="@+id/slot" android:layout_width="match_parent" android:layout_height="match_parent"/>'
        '</androidx.constraintlayout.widget.ConstraintLayout>',
        encoding="utf-8",
    )
    (tmp_path / "res" / "layout" / "frag_page.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<androidx.viewpager.widget.ViewPager android:id="@+id/vp" android:layout_width="match_parent" android:layout_height="200dp"/>'
        '<TextView android:id="@+id/secret_box" android:text="LoginForm" android:visibility="gone"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )
    (tmp_path / "res" / "layout" / "page_card.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<TextView android:id="@+id/card_lbl" android:text="Workout Smarter"/>'
        '</LinearLayout>',
        encoding="utf-8",
    )

    class _Prov:
        def get_class(self, desc):
            if desc == "Lapp/CarouselFrag;":
                return [
                    MethodDef(desc, "<init>", "()V", [("return-void",)], nregs=1, nargs=1),
                    MethodDef(
                        desc, "onCreateView",
                        "(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;",
                        [
                            ("const", 0, 0x7F0D0031),
                            ("const", 1, 0),
                            ("invoke-virtual", [3, 0, 5, 1], "Landroid/view/LayoutInflater;",
                             "inflate", "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"),
                            ("move-result-object", 0),
                            ("return-object", 0),
                        ],
                        nregs=7, nargs=4,
                    ),
                ]
            if desc == "Lapp/PagerAdapter;":
                return [
                    MethodDef(desc, "<init>", "()V", [("return-void",)], nregs=1, nargs=1),
                    MethodDef(desc, "getCount", "()I", [("const", 0, 1), ("return", 0)], nregs=2, nargs=1),
                    MethodDef(
                        desc, "instantiateItem", "(Landroid/view/ViewGroup;I)Ljava/lang/Object;",
                        [
                            ("const", 0, 0x7F0D0032),
                            ("const", 1, 0),
                            ("invoke-static", [3], "Landroid/view/LayoutInflater;", "from",
                             "(Landroid/content/Context;)Landroid/view/LayoutInflater;"),
                            ("move-result-object", 2),
                            ("invoke-virtual", [2, 0, 4, 1], "Landroid/view/LayoutInflater;",
                             "inflate", "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"),
                            ("move-result-object", 2),
                            ("invoke-virtual", [4, 2], "Landroid/view/ViewGroup;", "addView", "(Landroid/view/View;)V"),
                            ("return-object", 2),
                        ],
                        nregs=6, nargs=3,
                    ),
                ]
            return []

        def get_super(self, desc):
            if desc == "Lapp/CarouselFrag;":
                return "Landroidx/fragment/app/Fragment;"
            if desc == "Lapp/PagerAdapter;":
                return "Landroidx/viewpager/widget/PagerAdapter;"
            return "Ljava/lang/Object;"

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    vt = install_view_stubs(stubs, app_root=str(tmp_path))

    # 1. ArrayList.addAll + SpannableString
    l1 = JObject("Ljava/util/ArrayList;", {"__items": [JString("A")]})
    l2 = JObject("Ljava/util/ArrayList;", {"__items": [JString("B"), JString("C")]})
    ok_aa, changed = stubs.resolve("Ljava/util/ArrayList;", "addAll", "(Ljava/util/Collection;)Z", [l1, l2], False)
    assert ok_aa and changed == 1 and len(l1.fields["__items"]) == 3
    sp = JObject("Landroid/text/SpannableString;", {})
    stubs.resolve("Landroid/text/SpannableString;", "<init>", "(Ljava/lang/CharSequence;)V", [sp, JString("Bold")], False)
    assert stubs._txt(sp) == "Bold"

    # 2. Queued FragmentTransaction + ViewPager Adapter + ConstraintSet (ueber VM-_method_hooks)
    def _vm_call(rcls, cls, name, sig, args):
        for h in vm._method_hooks:
            ok_h, res_h = h(rcls, cls, name, sig, args, False, vm)
            if ok_h:
                return res_h
        raise AssertionError(f"Method hook missing for {cls}->{name}{sig}")

    act = JObject("Landroidx/fragment/app/FragmentActivity;", {})
    stubs._current_activity = act
    stubs.resolve("Landroid/app/Activity;", "setContentView", "(I)V", (act, 0x7F0D0030), False)
    fm = _vm_call(act.cls, act.cls, "getSupportFragmentManager",
                  "()Landroidx/fragment/app/FragmentManager;", [act])
    tx = _vm_call(fm.cls, fm.cls, "beginTransaction",
                  "()Landroidx/fragment/app/FragmentTransaction;", [fm])
    frag = JObject("Lapp/CarouselFrag;", {})
    _vm_call(tx.cls, tx.cls, "replace",
             "(ILandroidx/fragment/app/Fragment;)Landroidx/fragment/app/FragmentTransaction;", [tx, 0x7F0A0030, frag])
    _vm_call(tx.cls, tx.cls, "commitAllowingStateLoss", "()I", [tx])
    stubs._flush_fragment_transactions()

    vp_node = vt.root.find("vp")
    assert vp_node is not None and vt.root.find("secret_box").visible is False

    vp_obj = JObject("Landroidx/viewpager/widget/ViewPager;", {"$view": vp_node})
    adp_obj = JObject("Lapp/PagerAdapter;", {})
    _vm_call(vp_obj.cls, vp_obj.cls, "setAdapter", "(Landroidx/viewpager/widget/PagerAdapter;)V", [vp_obj, adp_obj])
    assert vt.root.find("card_lbl") is not None and vt.root.find("card_lbl").text == "Workout Smarter"

    # ConstraintSet.clone + setVisibility + applyTo offenbart secret_box
    cs = JObject("Landroidx/constraintlayout/widget/ConstraintSet;", {})
    root_cl_obj = JObject("Landroidx/constraintlayout/widget/ConstraintLayout;", {"$view": vt.root})
    _vm_call(cs.cls, cs.cls, "clone", "(Landroidx/constraintlayout/widget/ConstraintLayout;)V", [cs, root_cl_obj])
    _vm_call(cs.cls, cs.cls, "setVisibility", "(II)V", [cs, 0x7F0A0032, 0])
    _vm_call(cs.cls, cs.cls, "applyTo", "(Landroidx/constraintlayout/widget/ConstraintLayout;)V", [cs, root_cl_obj])
    assert vt.root.find("secret_box").visible is True


def test_r8_okhttp_retrofit_fields_and_suspend_network_recording():
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.objects import JObject, JString
    from core.java.net_recorder import (
        install_net_stubs, ReplayResponder, NetResponse, _parse_retrofit_routes,
    )

    api_smali = '''
.class public interface abstract Lapp/AuthApi;
.super Ljava/lang/Object;

.method public abstract currentUser(Ljava/lang/String;Ljava/lang/String;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;
    .annotation runtime Lretrofit2/http/GET;
        value = "v0/user/current"
    .end annotation
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/lang/String;",
            "Ljava/lang/String;",
            "Lkotlin/coroutines/Continuation<",
            "-",
            "Lretrofit2/Response<",
            "Lapp/UserDto;",
            ">;>;)",
            "Ljava/lang/Object;"
        }
    .end annotation
    .param p1    # Ljava/lang/String;
        .annotation runtime Lretrofit2/http/Header;
            value = "Authorization"
        .end annotation
    .end param
    .param p2    # Ljava/lang/String;
        .annotation runtime Lretrofit2/http/Query;
            value = "club"
        .end annotation
    .end param
.end method
'''
    okhttp_builder_smali = '.class public final Lokhttp3/OkHttpClient$Builder;\n.field public final c:Ljava/util/ArrayList;\n'
    ok_resp_smali = '.class public final Lokhttp3/Response;\n.field public final a:Lokhttp3/Request;\n.field public final c:Ljava/lang/String;\n.field public final d:I\n.field public final y:Lokhttp3/ResponseBody;\n.field public final W:Z\n'
    ret_resp_smali = '.class public final Lretrofit2/Response;\n.field public final a:Lokhttp3/Response;\n.field public final b:Ljava/lang/Object;\n.field public final c:Lokhttp3/ResponseBody;\n'
    dto_smali = '.class public final Lapp/UserDto;\n.field public final name:Ljava/lang/String;\n'

    routes = _parse_retrofit_routes(api_smali)
    assert "currentUser" in routes and routes["currentUser"]["wraps_response"] is True

    class _Prov:
        def get_source(self, desc):
            return {
                "Lapp/AuthApi;": api_smali,
                "Lokhttp3/OkHttpClient$Builder;": okhttp_builder_smali,
                "Lokhttp3/Response;": ok_resp_smali,
                "Lretrofit2/Response;": ret_resp_smali,
                "Lapp/UserDto;": dto_smali,
            }.get(desc, "")

        def get_class(self, desc):
            return []

        def get_super(self, desc):
            return "Ljava/lang/Object;"

        def get_interfaces(self, desc):
            return []

    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    resp_map = ReplayResponder({
        "GET https://api.test.app/v0/user/current": NetResponse(200, {"Content-Type": "application/json"}, b'{"name":"Alice"}', "OK")
    })
    net_log = install_net_stubs(stubs, responder=resp_map)

    vm.register_method(
        "Lapp/Runner;", "run", "()Lretrofit2/Response;",
        [
            ("new-instance", 0, "Lokhttp3/OkHttpClient$Builder;"),
            ("invoke-direct", [0], "Lokhttp3/OkHttpClient$Builder;", "<init>", "()V"),
            ("new-instance", 1, "Lokhttp3/OkHttpClient;"),
            ("invoke-direct", [1, 0], "Lokhttp3/OkHttpClient;", "<init>", "(Lokhttp3/OkHttpClient$Builder;)V"),
            ("new-instance", 2, "Lretrofit2/Retrofit$Builder;"),
            ("invoke-direct", [2], "Lretrofit2/Retrofit$Builder;", "<init>", "()V"),
            ("iput-object", 1, 2, "Lretrofit2/Retrofit$Builder;", "a", "Lokhttp3/OkHttpClient;"),
            ("const-string", 3, "https://api.test.app/"),
            ("invoke-virtual", [2, 3], "Lretrofit2/Retrofit$Builder;", "b", "(Ljava/lang/String;)V"),
            ("invoke-virtual", [2], "Lretrofit2/Retrofit$Builder;", "c", "()Lretrofit2/Retrofit;"),
            ("move-result-object", 2),
            ("const-class", 3, "Lapp/AuthApi;"),
            ("invoke-virtual", [2, 3], "Lretrofit2/Retrofit;", "b", "(Ljava/lang/Class;)Ljava/lang/Object;"),
            ("move-result-object", 2),
            ("const-string", 3, "Basic dGVzdA=="),
            ("const", 4, 0),
            ("const", 5, 0),
            ("invoke-interface", [2, 3, 4, 5], "Lapp/AuthApi;", "currentUser",
             "(Ljava/lang/String;Ljava/lang/String;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;"),
            ("move-result-object", 0),
            ("return-object", 0),
        ],
        nregs=6, nargs=0, static=True,
    )
    ret_resp = vm.invoke("Lapp/Runner;", "run", "()Lretrofit2/Response;", ())
    assert len(net_log.entries) == 1
    req_e = net_log.entries[0]["req"]
    assert req_e.method == "GET" and req_e.url == "https://api.test.app/v0/user/current"
    assert req_e.headers.get("Authorization") == "Basic dGVzdA=="
    # R8-Felder auf retrofit2/Response & okhttp3/Response befuellt
    ok_r = ret_resp.fields.get("a")
    assert ok_r is not None and ok_r.fields.get("W") == 1 and ok_r.fields.get("d") == 200
    dto = ret_resp.fields.get("b")
    assert dto is not None and isinstance(dto.fields.get("name"), JString) and dto.fields["name"].text == "Alice"


def test_timeunit_atomic_int_updater_moshi_chained_r8_interceptors_and_light_window_bg(tmp_path):
    from core.java.dalvik import DalvikVM, MethodDef
    from core.java.dex_loader import AndroidStubs
    from core.java.smali import parse_class as _parse_smali
    from core.java.lifecycle import _StubRuntime
    from core.java.net_recorder import ReplayResponder, NetResponse, install_net_stubs
    from core.java.objects import JObject, JString
    from core.java.view_tree import ViewNode, ViewTree, paint_commands

    # 1. TimeUnit + AtomicIntegerFieldUpdater + Moshi + chained R8 RealInterceptorChain
    chain_smali = ".class public final Lokhttp3/internal/http/RealInterceptorChain;\n.super Ljava/lang/Object;\n.field public final e:Lokhttp3/Request;\n"
    req_smali = ".class public final Lokhttp3/Request;\n.super Ljava/lang/Object;\n.field public final a:Lokhttp3/HttpUrl;\n.field public final b:Ljava/lang/String;\n.field public final c:Lokhttp3/Headers;\n"
    httpurl_smali = ".class public final Lokhttp3/HttpUrl;\n.super Ljava/lang/Object;\n.field public final i:Ljava/lang/String;\n"
    api_smali = '''
.class public interface abstract Lapp/V0Api;
.super Ljava/lang/Object;

.method public abstract me(Lkotlin/coroutines/Continuation;)Ljava/lang/Object;
    .annotation runtime Lretrofit2/http/GET;
        value = "v0/user/current?old=1"
    .end annotation
.end method
'''
    ic_src = '''
.class public final Lapp/KeyAndHeaderInterceptor;
.super Ljava/lang/Object;
.implements Lokhttp3/Interceptor;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V
    return-void
.end method

.method public final a(Lokhttp3/internal/http/RealInterceptorChain;)Lokhttp3/Response;
    .locals 4
    iget-object v0, p1, Lokhttp3/internal/http/RealInterceptorChain;->e:Lokhttp3/Request;
    iget-object v1, v0, Lokhttp3/Request;->a:Lokhttp3/HttpUrl;
    invoke-virtual {v1}, Lokhttp3/HttpUrl;->g()Lokhttp3/HttpUrl$Builder;
    move-result-object v1
    const-string v2, "old"
    invoke-virtual {v1, v2}, Lokhttp3/HttpUrl$Builder;->d(Ljava/lang/String;)V
    const-string v2, "api_key"
    const-string v3, "k123"
    invoke-virtual {v1, v2, v3}, Lokhttp3/HttpUrl$Builder;->a(Ljava/lang/String;Ljava/lang/String;)V
    invoke-virtual {v1}, Lokhttp3/HttpUrl$Builder;->b()Lokhttp3/HttpUrl;
    move-result-object v1
    new-instance v2, Lokhttp3/Request$Builder;
    invoke-direct {v2, v0}, Lokhttp3/Request$Builder;-><init>(Lokhttp3/Request;)V
    invoke-virtual {v2, v1}, Lokhttp3/Request$Builder;->url(Lokhttp3/HttpUrl;)Lokhttp3/Request$Builder;
    move-result-object v2
    const-string v0, "X-Test"
    const-string v1, "ok"
    invoke-virtual {v2, v0, v1}, Lokhttp3/Request$Builder;->header(Ljava/lang/String;Ljava/lang/String;)Lokhttp3/Request$Builder;
    move-result-object v2
    invoke-virtual {v2}, Lokhttp3/Request$Builder;->build()Lokhttp3/Request;
    move-result-object v0
    invoke-virtual {p1, v0}, Lokhttp3/internal/http/RealInterceptorChain;->b(Lokhttp3/Request;)Lokhttp3/Response;
    move-result-object v0
    return-object v0
.end method
'''

    class _Prov:
        def get_source(self, desc):
            return {
                "Lokhttp3/internal/http/RealInterceptorChain;": chain_smali,
                "Lokhttp3/Request;": req_smali,
                "Lokhttp3/HttpUrl;": httpurl_smali,
                "Lapp/V0Api;": api_smali,
                "Lapp/KeyAndHeaderInterceptor;": ic_src,
            }.get(desc, "")

        def get_class(self, desc):
            if desc == "Lapp/KeyAndHeaderInterceptor;":
                _, mds = _parse_smali(ic_src)
                return mds
            return []

        def get_super(self, desc):
            return "Ljava/lang/Object;"

        def get_interfaces(self, desc):
            if desc == "Lapp/KeyAndHeaderInterceptor;":
                return ["Lokhttp3/Interceptor;"]
            return []

    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vm.runtime = _StubRuntime(stubs)
    resp_map = ReplayResponder({
        "GET https://api.test.app/v0/user/current?api_key=k123": NetResponse(
            200, {"Content-Type": "application/json"}, b'{"ok":true}', "OK"
        ),
    })
    net_log = install_net_stubs(stubs, responder=resp_map)

    # TimeUnit enums + conversions
    tu_ms = vm.statics["Ljava/util/concurrent/TimeUnit;"]["MILLISECONDS"]
    tu_sec = vm.statics["Ljava/util/concurrent/TimeUnit;"]["SECONDS"]
    ok_ts, secs = stubs.resolve("Ljava/util/concurrent/TimeUnit;", "toSeconds", "(J)J", (tu_ms, 5000), False)
    ok_cv, ms = stubs.resolve("Ljava/util/concurrent/TimeUnit;", "convert", "(JLjava/util/concurrent/TimeUnit;)J", (tu_ms, 3, tu_sec), False)
    assert ok_ts and secs == 5
    assert ok_cv and ms == 3000

    # AtomicIntegerFieldUpdater
    cls_obj = JObject("Ljava/lang/Class;", {"name": "Lapp/State;"})
    ok_u, up = stubs.resolve(
        "Ljava/util/concurrent/atomic/AtomicIntegerFieldUpdater;",
        "newUpdater",
        "(Ljava/lang/Class;Ljava/lang/String;)Ljava/util/concurrent/atomic/AtomicIntegerFieldUpdater;",
        (cls_obj, JString("state")),
        True,
    )
    target = JObject("Lapp/State;", {"state": 0})
    assert ok_u and stubs.resolve(up.cls, "compareAndSet", "(Ljava/lang/Object;II)Z", (up, target, 0, 7), False)[1] == 1
    assert stubs.resolve(up.cls, "getAndIncrement", "(Ljava/lang/Object;)I", (up, target), False)[1] == 7
    assert target.fields["state"] == 8

    # Moshi$Builder + R8 Retrofit + R8 RealInterceptorChain
    vm.register_method(
        "Lapp/FlowRunner;", "call", "()Lretrofit2/Response;",
        [
            ("new-instance", 0, "Lcom/squareup/moshi/Moshi$Builder;"),
            ("invoke-direct", [0], "Lcom/squareup/moshi/Moshi$Builder;", "<init>", "()V"),
            ("new-instance", 1, "Ljava/lang/Object;"),
            ("invoke-virtual", [0, 1], "Lcom/squareup/moshi/Moshi$Builder;", "b", "(Ljava/lang/Object;)V"),
            ("new-instance", 0, "Lokhttp3/OkHttpClient$Builder;"),
            ("invoke-direct", [0], "Lokhttp3/OkHttpClient$Builder;", "<init>", "()V"),
            ("new-instance", 1, "Lapp/KeyAndHeaderInterceptor;"),
            ("invoke-direct", [1], "Lapp/KeyAndHeaderInterceptor;", "<init>", "()V"),
            ("invoke-virtual", [0, 1], "Lokhttp3/OkHttpClient$Builder;", "a", "(Lokhttp3/Interceptor;)Lokhttp3/OkHttpClient$Builder;"),
            ("move-result-object", 0),
            ("new-instance", 1, "Lokhttp3/OkHttpClient;"),
            ("invoke-direct", [1, 0], "Lokhttp3/OkHttpClient;", "<init>", "(Lokhttp3/OkHttpClient$Builder;)V"),
            ("new-instance", 2, "Lretrofit2/Retrofit$Builder;"),
            ("invoke-direct", [2], "Lretrofit2/Retrofit$Builder;", "<init>", "()V"),
            ("invoke-virtual", [2, 1], "Lretrofit2/Retrofit$Builder;", "client", "(Lokhttp3/OkHttpClient;)Lretrofit2/Retrofit$Builder;"),
            ("move-result-object", 2),
            ("const-string", 3, "https://api.test.app/"),
            ("invoke-virtual", [2, 3], "Lretrofit2/Retrofit$Builder;", "b", "(Ljava/lang/String;)V"),
            ("invoke-virtual", [2], "Lretrofit2/Retrofit$Builder;", "c", "()Lretrofit2/Retrofit;"),
            ("move-result-object", 2),
            ("const-class", 3, "Lapp/V0Api;"),
            ("invoke-virtual", [2, 3], "Lretrofit2/Retrofit;", "b", "(Ljava/lang/Class;)Ljava/lang/Object;"),
            ("move-result-object", 2),
            ("const", 3, 0),
            ("invoke-interface", [2, 3], "Lapp/V0Api;", "me", "(Lkotlin/coroutines/Continuation;)Ljava/lang/Object;"),
            ("move-result-object", 0),
            ("return-object", 0),
        ],
        nregs=5, nargs=0, static=True,
    )
    vm.invoke("Lapp/FlowRunner;", "call", "()Lretrofit2/Response;", ())
    assert len(net_log.entries) == 1
    req_rec = net_log.entries[0]["req"]
    assert req_rec.url == "https://api.test.app/v0/user/current?api_key=k123"
    assert req_rec.headers.get("X-Test") == "ok"

    # 2. effective_dark(), childless FrameLayout collapsing, and centered indicator row
    tree = ViewTree(width=380, height=776)
    tree.window_bg = "#ffffff"
    tree.window_bg_dark = False
    tree.dark_mode = True
    assert tree.effective_dark() is False

    root = ViewNode("ConstraintLayout", vid="root")
    overlay = ViewNode("FrameLayout", vid="overlay", props={"bg_drawable": {"kind": "shape", "fill": "#888888"}})
    root.add(overlay)
    dots = ViewNode("RelativeLayout", vid="indicator_holder")
    dots.add(ViewNode("ImageView", props={"drawable": {"kind": "raster", "name": "filled_indicator", "path": "x.png"}}))
    dots.add(ViewNode("ImageView", props={"drawable": {"kind": "raster", "name": "empty_indicator", "path": "y.png"}}))
    root.add(dots)
    title = ViewNode("TextView", vid="t", text="Workout smarter", props={"color": "#ffffff"})
    root.add(title)
    tree.set_root(root)

    assert overlay.bounds[3] == 0
    assert dots.bounds[3] == 20
    t_cmds = [c for c in paint_commands(tree) if c.get("op") == "text" and c.get("text") == "Workout smarter"]
    assert t_cmds and t_cmds[0]["color"].lower() != "#ffffff"
