"""Tests for G16 View, Graphics, Canvas, Text, Widget & Accessibility Breadth."""

from __future__ import annotations

import math

import pytest

from core.java.canvas_recorder import CanvasRecorder, install_canvas_stubs
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.objects import JArray, JObject, JString
from core.java.view_breadth_g16 import install_g16_stubs
from core.java.view_tree import ViewNode, install_view_stubs


def _make_env(with_view_tree: bool = False):
    stubs = AndroidStubs()
    tree = None
    if with_view_tree:
        tree = install_view_stubs(stubs)
    else:
        install_g16_stubs(stubs)
    vm = DalvikVM(runtime=_StubRuntime(stubs))
    return stubs, vm, tree


def _invoke(vm: DalvikVM, rcls: str, cls: str, name: str, sig: str, args=(), static: bool = False):
    wants = vm._build_wants(rcls, cls)
    ok, val = vm._resolve_runtime_invoke(wants, rcls, cls, name, sig, list(args), static)
    assert ok, f"Stub resolution failed for {rcls}->{name}{sig} (decl {cls})"
    return val


def test_g16_idempotent_and_view_tree_integration():
    stubs = AndroidStubs()
    tree = install_view_stubs(stubs)
    assert getattr(stubs, "_g16_installed", False) is True
    assert isinstance(getattr(stubs, "canvas_recorder", None), CanvasRecorder)
    before = len(stubs._stubs)
    install_g16_stubs(stubs)
    assert len(stubs._stubs) == before
    assert stubs.view_tree is tree


def test_dalvik_view_super_and_iface_want_chains():
    _, vm, _ = _make_env(with_view_tree=False)

    assert "Landroid/graphics/Paint;" in vm._build_wants("Landroid/text/TextPaint;", "Landroid/text/TextPaint;")
    for lay in ("Landroid/text/StaticLayout;", "Landroid/text/BoringLayout;", "Landroid/text/DynamicLayout;"):
        assert "Landroid/text/Layout;" in vm._build_wants(lay, lay)

    rip_wants = vm._build_wants("Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/RippleDrawable;")
    assert "Landroid/graphics/drawable/LayerDrawable;" in rip_wants
    assert "Landroid/graphics/drawable/Drawable;" in rip_wants
    assert rip_wants.index("Landroid/graphics/drawable/LayerDrawable;") < rip_wants.index("Landroid/graphics/drawable/Drawable;")

    for drw in (
        "Landroid/graphics/drawable/StateListDrawable;",
        "Landroid/graphics/drawable/AnimationDrawable;",
        "Landroid/graphics/drawable/InsetDrawable;",
        "Landroid/graphics/drawable/GradientDrawable;",
    ):
        assert "Landroid/graphics/drawable/Drawable;" in vm._build_wants(drw, drw)

    sb_wants = vm._build_wants("Landroid/widget/SeekBar;", "Landroid/widget/SeekBar;")
    for anc in (
        "Landroid/widget/AbsSeekBar;",
        "Landroid/widget/ProgressBar;",
        "Landroid/view/View;",
    ):
        assert anc in sb_wants

    lv_wants = vm._build_wants("Landroid/widget/ListView;", "Landroid/widget/ListView;")
    for anc in (
        "Landroid/widget/AbsListView;",
        "Landroid/widget/AdapterView;",
        "Landroid/view/ViewGroup;",
        "Landroid/view/View;",
    ):
        assert anc in lv_wants
    assert vm._is_subclass("Landroid/view/ViewGroup;", "Landroid/view/ViewParent;")
    assert vm._is_subclass("Landroid/view/ViewGroup;", "Landroid/view/ViewManager;")
    assert vm._is_subclass("Landroid/widget/ListView;", "Landroid/view/ViewParent;")
    assert vm._is_subclass("Landroid/widget/ListView;", "Landroid/view/ViewManager;")

    sp_wants = vm._build_wants("Landroid/widget/Spinner;", "Landroid/widget/Spinner;")
    assert "Landroid/widget/AbsSpinner;" in sp_wants
    assert "Landroid/widget/AdapterView;" in sp_wants

    hsv_wants = vm._build_wants("Landroid/widget/HorizontalScrollView;", "Landroid/widget/HorizontalScrollView;")
    assert "Landroid/widget/FrameLayout;" in hsv_wants

    oa_wants = vm._build_wants("Landroid/animation/ObjectAnimator;", "Landroid/animation/ObjectAnimator;")
    assert "Landroid/animation/ValueAnimator;" in oa_wants
    assert "Landroid/animation/Animator;" in oa_wants
    assert "Landroid/animation/Animator;" in vm._build_wants("Landroid/animation/AnimatorSet;", "Landroid/animation/AnimatorSet;")

    ae_wants = vm._build_wants("Landroid/view/accessibility/AccessibilityEvent;", "Landroid/view/accessibility/AccessibilityEvent;")
    assert "Landroid/view/accessibility/AccessibilityRecord;" in ae_wants

    acd_wants = vm._build_wants("Landroidx/appcompat/app/AlertDialog;", "Landroidx/appcompat/app/AlertDialog;")
    assert "Landroidx/appcompat/app/AppCompatDialog;" in acd_wants
    assert "Landroid/app/Dialog;" in acd_wants
    assert "Landroid/app/Dialog;" in vm._build_wants("Landroid/app/AlertDialog;", "Landroid/app/AlertDialog;")

    ssb_wants = vm._build_wants("Landroid/text/SpannableStringBuilder;", "Landroid/text/SpannableStringBuilder;")
    assert "Landroid/text/Editable;" in ssb_wants
    for iface in (
        "Landroid/text/Editable;",
        "Landroid/text/Spannable;",
        "Landroid/text/Spanned;",
        "Ljava/lang/CharSequence;",
        "Ljava/lang/Appendable;",
    ):
        assert vm._is_subclass("Landroid/text/SpannableStringBuilder;", iface)


def test_bare_view_geometry_state_and_rects_without_view_tree():
    _, vm, _ = _make_env(with_view_tree=False)
    v = JObject("Landroid/view/View;", {"_m_w": 120, "_m_h": 48, "$w": 120, "$h": 48})

    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getMeasuredWidth", "()I", [v]) == 120
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getMeasuredHeight", "()I", [v]) == 48
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getWidth", "()I", [v]) == 120
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getHeight", "()I", [v]) == 48

    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "measure", "(II)V", [v, (100 | (1 << 30)), (60 | (1 << 30))])
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getMeasuredWidth", "()I", [v]) == 100
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getMeasuredHeight", "()I", [v]) == 60

    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "layout", "(IIII)V", [v, 10, 20, 210, 100])
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getLeft", "()I", [v]) == 10
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getTop", "()I", [v]) == 20
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getRight", "()I", [v]) == 210
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getBottom", "()I", [v]) == 100
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getWidth", "()I", [v]) == 200
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getHeight", "()I", [v]) == 80
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "isLaidOut", "()Z", [v]) == 1

    loc = JArray("[I", [0, 0])
    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getLocationOnScreen", "([I)V", [v, loc])
    assert loc.data == [10, 20]

    grect = JObject("Landroid/graphics/Rect;", {})
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getGlobalVisibleRect", "(Landroid/graphics/Rect;)Z", [v, grect]) == 1
    assert (grect.fields["left"], grect.fields["top"], grect.fields["right"], grect.fields["bottom"]) == (10, 20, 210, 100)

    lrect = JObject("Landroid/graphics/Rect;", {})
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getLocalVisibleRect", "(Landroid/graphics/Rect;)Z", [v, lrect]) == 1
    assert (lrect.fields["left"], lrect.fields["top"], lrect.fields["right"], lrect.fields["bottom"]) == (0, 0, 200, 80)

    hrect = JObject("Landroid/graphics/Rect;", {})
    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getHitRect", "(Landroid/graphics/Rect;)V", [v, hrect])
    assert (hrect.fields["left"], hrect.fields["top"], hrect.fields["right"], hrect.fields["bottom"]) == (10, 20, 210, 100)

    drect = JObject("Landroid/graphics/Rect;", {})
    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getDrawingRect", "(Landroid/graphics/Rect;)V", [v, drect])
    assert (drect.fields["left"], drect.fields["top"], drect.fields["right"], drect.fields["bottom"]) == (0, 0, 200, 80)

    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getVisibility", "()I", [v]) == 0
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "isShown", "()Z", [v]) == 1
    _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "setVisibility", "(I)V", [v, 4])
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getVisibility", "()I", [v]) == 4
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "isShown", "()Z", [v]) == 0
    assert _invoke(vm, "Landroid/view/View;", "Landroid/view/View;", "getGlobalVisibleRect", "(Landroid/graphics/Rect;)Z", [v, grect]) == 0


def test_view_tree_visibility_0_4_8_and_decor_view():
    stubs, vm, tree = _make_env(with_view_tree=True)

    win = JObject("Landroid/view/Window;", {})
    decor_before = _invoke(vm, "Landroid/view/Window;", "Landroid/view/Window;", "getDecorView", "()Landroid/view/View;", [win])
    assert isinstance(decor_before, JObject)
    assert isinstance(decor_before.fields.get("$view"), ViewNode)

    node = ViewNode(cls="Landroid/widget/Button;", text="Tap", clickable=True)
    node._bounds = (0, 10, 200, 60)
    btn = JObject("Landroid/widget/Button;", {"$view": node})
    node._tree = tree
    root = ViewNode(cls="Landroid/widget/FrameLayout;")
    root._bounds = (0, 0, 400, 800)
    root.children = [node]
    tree.root = root
    node.parent = root
    tree._root_bounds = (0, 0, 400, 800)
    tree._layout_dirty = False

    decor_after = _invoke(vm, "Landroid/view/Window;", "Landroid/view/Window;", "getDecorView", "()Landroid/view/View;", [win])
    assert decor_after.fields.get("$view") is tree.root

    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getWidth", "()I", [btn]) == 200
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getHeight", "()I", [btn]) == 60
    loc = JArray("[I", [0, 0])
    _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getLocationOnScreen", "([I)V", [btn, loc])
    assert loc.data == [0, 10]

    _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "setVisibility", "(I)V", [btn, 0])
    assert node.visible is True
    assert node.props["visibility"] == 0
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getVisibility", "()I", [btn]) == 0
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "isShown", "()Z", [btn]) == 1

    _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "setVisibility", "(I)V", [btn, 4])
    assert node.visible is False
    assert node.props["visibility"] == 4
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getVisibility", "()I", [btn]) == 4
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "isShown", "()Z", [btn]) == 0

    _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "setVisibility", "(I)V", [btn, 8])
    assert node.visible is False
    assert node.props["visibility"] == 8
    assert _invoke(vm, "Landroid/widget/Button;", "Landroid/view/View;", "getVisibility", "()I", [btn]) == 8


def test_canvas_recorder_and_paint_text_paint_stubs():
    stubs = AndroidStubs()
    rec = install_canvas_stubs(stubs, CanvasRecorder(800, 600))
    vm = DalvikVM(runtime=_StubRuntime(stubs))

    c_default = JObject("Landroid/graphics/Canvas;", {})
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "<init>", "()V", [c_default])
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getWidth", "()I", [c_default]) == 800
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getHeight", "()I", [c_default]) == 600

    bmp = JObject("Landroid/graphics/Bitmap;", {"_w": 320, "_h": 240})
    c_bmp = JObject("Landroid/graphics/Canvas;", {})
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "<init>", "(Landroid/graphics/Bitmap;)V", [c_bmp, bmp])
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getWidth", "()I", [c_bmp]) == 320
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getHeight", "()I", [c_bmp]) == 240

    s1 = _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "save", "()I", [c_default])
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getSaveCount", "()I", [c_default]) == s1 + 1
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "translate", "(FF)V", [c_default, 10.0, 20.0])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "scale", "(FF)V", [c_default, 2.0, 3.0])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "rotate", "(F)V", [c_default, 90.0])

    paint = JObject("Landroid/text/TextPaint;", {})
    _invoke(vm, "Landroid/text/TextPaint;", "Landroid/text/TextPaint;", "<init>", "(I)V", [paint, 1])
    _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "setTextSize", "(F)V", [paint, 20.0])
    _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "setColor", "(I)V", [paint, -16776961])
    assert _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "getTextSize", "()F", [paint]) == pytest.approx(20.0)
    w = _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "measureText", "(Ljava/lang/String;)F", [paint, JString("Hello")])
    assert w > 0.0

    bounds = JObject("Landroid/graphics/Rect;", {})
    _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "getTextBounds", "(Ljava/lang/String;IILandroid/graphics/Rect;)V", [paint, JString("Hello"), 0, 5, bounds])
    assert bounds.fields["right"] > 0

    fm = JObject("Landroid/graphics/Paint$FontMetrics;", {})
    spacing = _invoke(vm, "Landroid/text/TextPaint;", "Landroid/graphics/Paint;", "getFontMetrics", "(Landroid/graphics/Paint$FontMetrics;)F", [paint, fm])
    assert spacing > 0.0
    assert fm.fields["ascent"] < 0.0 and fm.fields["descent"] > 0.0

    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawRoundRect", "(FFFFFFLandroid/graphics/Paint;)V", [c_default, 0.0, 0.0, 10.0, 5.0, 2.0, 2.0, paint])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawOval", "(FFFFLandroid/graphics/Paint;)V", [c_default, 0.0, 0.0, 4.0, 4.0, paint])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawArc", "(FFFFFFZLandroid/graphics/Paint;)V", [c_default, 0.0, 0.0, 8.0, 8.0, 0.0, 90.0, 1, paint])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawPath", "(Landroid/graphics/Path;Landroid/graphics/Paint;)V", [c_default, JObject("Landroid/graphics/Path;", {"_pts": [(0.0, 0.0), (1.0, 2.0)]}), paint])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawBitmap", "(Landroid/graphics/Bitmap;FFLandroid/graphics/Paint;)V", [c_default, bmp, 5.0, 6.0, paint])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "drawARGB", "(IIII)V", [c_default, 255, 10, 20, 30])
    _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "restoreToCount", "(I)V", [c_default, s1])
    assert _invoke(vm, "Landroid/graphics/Canvas;", "Landroid/graphics/Canvas;", "getSaveCount", "()I", [c_default]) == s1

    ops = {c["op"] for c in rec.cmds}
    assert {"rect", "oval", "arc", "path", "bitmap"}.issubset(ops)


def test_matrix_2d_math_and_path():
    _, vm, _ = _make_env(with_view_tree=False)
    m = JObject("Landroid/graphics/Matrix;", {})
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "<init>", "()V", [m])
    assert _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "isIdentity", "()Z", [m]) == 1

    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "setTranslate", "(FF)V", [m, 10.0, 20.0])
    assert _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "isIdentity", "()Z", [m]) == 0
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "postScale", "(FF)Z", [m, 2.0, 3.0])

    pts = JArray("[F", [1.0, 2.0])
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "mapPoints", "([F)V", [m, pts])
    assert pts.data[0] == pytest.approx(22.0)
    assert pts.data[1] == pytest.approx(66.0)

    rf = JObject("Landroid/graphics/RectF;", {"left": 0.0, "top": 0.0, "right": 5.0, "bottom": 4.0})
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "mapRect", "(Landroid/graphics/RectF;)Z", [m, rf])
    assert rf.fields["left"] == pytest.approx(20.0)
    assert rf.fields["top"] == pytest.approx(60.0)
    assert rf.fields["right"] == pytest.approx(30.0)
    assert rf.fields["bottom"] == pytest.approx(72.0)

    inv = JObject("Landroid/graphics/Matrix;", {})
    assert _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "invert", "(Landroid/graphics/Matrix;)Z", [m, inv]) == 1
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "mapPoints", "([F)V", [inv, pts])
    assert pts.data[0] == pytest.approx(1.0)
    assert pts.data[1] == pytest.approx(2.0)

    vals = JArray("[F", [0.0] * 9)
    _invoke(vm, "Landroid/graphics/Matrix;", "Landroid/graphics/Matrix;", "getValues", "([F)V", [m, vals])
    assert vals.data[0] == pytest.approx(2.0) and vals.data[2] == pytest.approx(20.0)
    assert vals.data[4] == pytest.approx(3.0) and vals.data[5] == pytest.approx(60.0)

    path = JObject("Landroid/graphics/Path;", {})
    _invoke(vm, "Landroid/graphics/Path;", "Landroid/graphics/Path;", "<init>", "()V", [path])
    _invoke(vm, "Landroid/graphics/Path;", "Landroid/graphics/Path;", "moveTo", "(FF)V", [path, 0.0, 0.0])
    _invoke(vm, "Landroid/graphics/Path;", "Landroid/graphics/Path;", "lineTo", "(FF)V", [path, 3.0, 4.0])
    pbounds = JObject("Landroid/graphics/RectF;", {})
    _invoke(vm, "Landroid/graphics/Path;", "Landroid/graphics/Path;", "computeBounds", "(Landroid/graphics/RectF;Z)V", [path, pbounds, 1])
    assert (pbounds.fields["left"], pbounds.fields["top"], pbounds.fields["right"], pbounds.fields["bottom"]) == (0.0, 0.0, 3.0, 4.0)

    pm = JObject("Landroid/graphics/PathMeasure;", {})
    _invoke(vm, "Landroid/graphics/PathMeasure;", "Landroid/graphics/PathMeasure;", "<init>", "(Landroid/graphics/Path;Z)V", [pm, path, 0])
    assert _invoke(vm, "Landroid/graphics/PathMeasure;", "Landroid/graphics/PathMeasure;", "getLength", "()F", [pm]) == pytest.approx(5.0)
    pos = JArray("[F", [0.0, 0.0])
    tan = JArray("[F", [0.0, 0.0])
    assert _invoke(vm, "Landroid/graphics/PathMeasure;", "Landroid/graphics/PathMeasure;", "getPosTan", "(F[F[F)Z", [pm, 2.5, pos, tan]) == 1
    assert pos.data[0] == pytest.approx(1.5) and pos.data[1] == pytest.approx(2.0)


def test_static_layout_builder_and_layout():
    _, vm, _ = _make_env(with_view_tree=False)
    tp = JObject("Landroid/text/TextPaint;", {"$textSize": 16.0})
    builder = _invoke(
        vm,
        "Landroid/text/StaticLayout$Builder;",
        "Landroid/text/StaticLayout$Builder;",
        "obtain",
        "(Ljava/lang/CharSequence;IILandroid/text/TextPaint;I)Landroid/text/StaticLayout$Builder;",
        [JString("Hello\nWorld"), 0, 11, tp, 400],
        static=True,
    )
    _invoke(vm, "Landroid/text/StaticLayout$Builder;", "Landroid/text/StaticLayout$Builder;", "setMaxLines", "(I)Landroid/text/StaticLayout$Builder;", [builder, 3])
    _invoke(vm, "Landroid/text/StaticLayout$Builder;", "Landroid/text/StaticLayout$Builder;", "setIncludePad", "(Z)Landroid/text/StaticLayout$Builder;", [builder, 1])
    sl = _invoke(vm, "Landroid/text/StaticLayout$Builder;", "Landroid/text/StaticLayout$Builder;", "build", "()Landroid/text/StaticLayout;", [builder])

    assert sl.cls == "Landroid/text/StaticLayout;"
    assert _invoke(vm, "Landroid/text/StaticLayout;", "Landroid/text/Layout;", "getLineCount", "()I", [sl]) == 2
    assert _invoke(vm, "Landroid/text/StaticLayout;", "Landroid/text/Layout;", "getWidth", "()I", [sl]) == 400
    assert _invoke(vm, "Landroid/text/StaticLayout;", "Landroid/text/Layout;", "getHeight", "()I", [sl]) > 0
    assert _invoke(vm, "Landroid/text/StaticLayout;", "Landroid/text/Layout;", "getLineRight", "(I)F", [sl, 0]) > 0.0


def test_accessibility_manager_node_info_and_event():
    _, vm, _ = _make_env(with_view_tree=False)
    ctx = JObject("Landroid/content/Context;", {})
    am = _invoke(vm, "Landroid/view/accessibility/AccessibilityManager;", "Landroid/view/accessibility/AccessibilityManager;", "getInstance", "(Landroid/content/Context;)Landroid/view/accessibility/AccessibilityManager;", [ctx], static=True)
    assert _invoke(vm, "Landroid/view/accessibility/AccessibilityManager;", "Landroid/view/accessibility/AccessibilityManager;", "isEnabled", "()Z", [am]) == 0
    assert _invoke(vm, "Landroid/view/accessibility/AccessibilityManager;", "Landroid/view/accessibility/AccessibilityManager;", "isTouchExplorationEnabled", "()Z", [am]) == 0

    ani_cls = "Landroid/view/accessibility/AccessibilityNodeInfo;"
    ani = _invoke(vm, ani_cls, ani_cls, "obtain", f"(){ani_cls}", [], static=True)
    _invoke(vm, ani_cls, ani_cls, "addAction", "(I)V", [ani, 16])
    act = JObject("Landroid/view/accessibility/AccessibilityNodeInfo$AccessibilityAction;", {"_id": 16})
    _invoke(vm, ani_cls, ani_cls, "addAction", "(Landroid/view/accessibility/AccessibilityNodeInfo$AccessibilityAction;)V", [ani, act])
    assert _invoke(vm, ani_cls, ani_cls, "getActions", "()I", [ani]) == 16
    act_list = _invoke(vm, ani_cls, ani_cls, "getActionList", "()Ljava/util/List;", [ani])
    assert len(act_list.fields["__items"]) == 1

    in_r = JObject("Landroid/graphics/Rect;", {"left": 1, "top": 2, "right": 50, "bottom": 60})
    out_r = JObject("Landroid/graphics/Rect;", {})
    _invoke(vm, ani_cls, ani_cls, "setBoundsInScreen", "(Landroid/graphics/Rect;)V", [ani, in_r])
    _invoke(vm, ani_cls, ani_cls, "getBoundsInScreen", "(Landroid/graphics/Rect;)V", [ani, out_r])
    assert (out_r.fields["left"], out_r.fields["top"], out_r.fields["right"], out_r.fields["bottom"]) == (1, 2, 50, 60)
    _invoke(vm, ani_cls, ani_cls, "setClassName", "(Ljava/lang/CharSequence;)V", [ani, JString("android.widget.Button")])
    assert _invoke(vm, ani_cls, ani_cls, "getClassName", "()Ljava/lang/CharSequence;", [ani]).text == "android.widget.Button"

    ev_cls = "Landroid/view/accessibility/AccessibilityEvent;"
    ev = _invoke(vm, ev_cls, ev_cls, "obtain", f"(I){ev_cls}", [32], static=True)
    assert _invoke(vm, ev_cls, ev_cls, "getEventType", "()I", [ev]) == 32
    _invoke(vm, ev_cls, "Landroid/view/accessibility/AccessibilityRecord;", "setItemCount", "(I)V", [ev, 7])
    _invoke(vm, ev_cls, "Landroid/view/accessibility/AccessibilityRecord;", "getText", "()Ljava/util/List;", [ev])


def test_overscroller_popup_motion_event_and_view_configuration():
    _, vm, _ = _make_env(with_view_tree=False)
    ctx = JObject("Landroid/content/Context;", {})

    vc = _invoke(vm, "Landroid/view/ViewConfiguration;", "Landroid/view/ViewConfiguration;", "get", "(Landroid/content/Context;)Landroid/view/ViewConfiguration;", [ctx], static=True)
    assert _invoke(vm, "Landroid/view/ViewConfiguration;", "Landroid/view/ViewConfiguration;", "getScaledTouchSlop", "()I", [vc]) == 8
    assert _invoke(vm, "Landroid/view/ViewConfiguration;", "Landroid/view/ViewConfiguration;", "hasPermanentMenuKey", "()Z", [vc]) == 0

    me = _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "obtain", "(JJIFFI)Landroid/view/MotionEvent;", [100, 105, 0x0202, 12.5, 24.5, 0], static=True)
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "getActionMasked", "()I", [me]) == 2
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "getActionIndex", "()I", [me]) == 2
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "getPointerCount", "()I", [me]) == 1
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "findPointerIndex", "(I)I", [me, 0]) == 0
    _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "offsetLocation", "(FF)V", [me, 2.5, -4.5])
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "getX", "()F", [me]) == pytest.approx(15.0)
    assert _invoke(vm, "Landroid/view/MotionEvent;", "Landroid/view/MotionEvent;", "getY", "()F", [me]) == pytest.approx(20.0)

    scr = JObject("Landroid/widget/OverScroller;", {})
    _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "<init>", "(Landroid/content/Context;)V", [scr, ctx])
    _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "startScroll", "(IIIII)V", [scr, 0, 0, 100, 200, 250])
    assert _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "computeScrollOffset", "()Z", [scr]) == 1
    assert _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "getCurrX", "()I", [scr]) == 100
    assert _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "getFinalY", "()I", [scr]) == 200
    assert _invoke(vm, "Landroid/widget/OverScroller;", "Landroid/widget/OverScroller;", "isFinished", "()Z", [scr]) == 1

    pw = JObject("Landroid/widget/PopupWindow;", {})
    anchor = JObject("Landroid/view/View;", {})
    _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "<init>", "(Landroid/content/Context;)V", [pw, ctx])
    _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "setWidth", "(I)V", [pw, 240])
    _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "setHeight", "(I)V", [pw, 120])
    _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "showAsDropDown", "(Landroid/view/View;)V", [pw, anchor])
    assert _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "isShowing", "()Z", [pw]) == 1
    assert _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "getWidth", "()I", [pw]) == 240
    _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "dismiss", "()V", [pw])
    assert _invoke(vm, "Landroid/widget/PopupWindow;", "Landroid/widget/PopupWindow;", "isShowing", "()Z", [pw]) == 0


def test_drawable_bounds_tint_and_constant_state():
    _, vm, _ = _make_env(with_view_tree=False)
    layer0 = JObject("Landroid/graphics/drawable/ColorDrawable;", {})
    rd = JObject("Landroid/graphics/drawable/RippleDrawable;", {})
    _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/RippleDrawable;", "<init>", "(Landroid/content/res/ColorStateList;Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;)V", [rd, None, layer0, None])
    assert _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/LayerDrawable;", "getNumberOfLayers", "()I", [rd]) == 1

    _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/Drawable;", "setBounds", "(IIII)V", [rd, 4, 8, 64, 48])
    _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/Drawable;", "setTint", "(I)V", [rd, -65536])
    b = _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/Drawable;", "getBounds", "()Landroid/graphics/Rect;", [rd])
    assert (b.fields["left"], b.fields["top"], b.fields["right"], b.fields["bottom"]) == (4, 8, 64, 48)
    copied = JObject("Landroid/graphics/Rect;", {})
    _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/Drawable;", "copyBounds", "(Landroid/graphics/Rect;)V", [rd, copied])
    assert (copied.fields["left"], copied.fields["right"]) == (4, 64)

    cs = _invoke(vm, "Landroid/graphics/drawable/RippleDrawable;", "Landroid/graphics/drawable/Drawable;", "getConstantState", "()Landroid/graphics/drawable/Drawable$ConstantState;", [rd])
    cloned = _invoke(vm, "Landroid/graphics/drawable/Drawable$ConstantState;", "Landroid/graphics/drawable/Drawable$ConstantState;", "newDrawable", "()Landroid/graphics/drawable/Drawable;", [cs])
    assert cloned.fields.get("_tint_int") == -65536


def test_spannable_string_builder_spans_and_edits():
    _, vm, _ = _make_env(with_view_tree=False)
    ssb_cls = "Landroid/text/SpannableStringBuilder;"
    ssb = JObject(ssb_cls, {"$text": "Hello"})
    span_a = JObject("Landroid/text/style/StyleSpan;", {})
    span_b = JObject("Landroid/text/style/ForegroundColorSpan;", {})

    _invoke(vm, ssb_cls, ssb_cls, "append", f"(C){ssb_cls}", [ssb, ord("!")])
    assert ssb.fields["$text"] == "Hello!"
    _invoke(vm, ssb_cls, ssb_cls, "append", f"(Ljava/lang/CharSequence;II){ssb_cls}", [ssb, JString(" World!!"), 0, 6])
    assert ssb.fields["$text"] == "Hello! World"
    _invoke(vm, ssb_cls, ssb_cls, "append", f"(Ljava/lang/CharSequence;Ljava/lang/Object;I){ssb_cls}", [ssb, JString(" Bold"), span_a, 33])
    assert ssb.fields["$text"] == "Hello! World Bold"
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanStart", "(Ljava/lang/Object;)I", [ssb, span_a]) == 12
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanEnd", "(Ljava/lang/Object;)I", [ssb, span_a]) == 17
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanFlags", "(Ljava/lang/Object;)I", [ssb, span_a]) == 33

    _invoke(vm, ssb_cls, ssb_cls, "setSpan", "(Ljava/lang/Object;III)V", [ssb, span_b, 0, 5, 17])
    _invoke(vm, ssb_cls, ssb_cls, "insert", f"(ILjava/lang/CharSequence;){ssb_cls}", [ssb, 0, JString("Hi ")])
    assert ssb.fields["$text"].startswith("Hi Hello!")
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanStart", "(Ljava/lang/Object;)I", [ssb, span_b]) == 3
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanEnd", "(Ljava/lang/Object;)I", [ssb, span_b]) == 8

    _invoke(vm, ssb_cls, ssb_cls, "delete", f"(II){ssb_cls}", [ssb, 0, 3])
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanStart", "(Ljava/lang/Object;)I", [ssb, span_b]) == 0
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanEnd", "(Ljava/lang/Object;)I", [ssb, span_b]) == 5

    obj_cls = JObject("Ljava/lang/Class;", {"name": "android.text.style.StyleSpan"})
    got = _invoke(vm, ssb_cls, ssb_cls, "getSpans", "(IILjava/lang/Class;)[Ljava/lang/Object;", [ssb, 0, 20, obj_cls])
    assert got.data == [span_a]

    nxt = _invoke(vm, ssb_cls, ssb_cls, "nextSpanTransition", "(IILjava/lang/Class;)I", [ssb, 0, 20, None])
    assert nxt == 5

    buf = JArray("[C", [0] * 5)
    _invoke(vm, ssb_cls, ssb_cls, "getChars", "(II[CI)V", [ssb, 0, 5, buf, 0])
    assert "".join(chr(c) for c in buf.data) == "Hello"

    _invoke(vm, ssb_cls, ssb_cls, "removeSpan", "(Ljava/lang/Object;)V", [ssb, span_b])
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanStart", "(Ljava/lang/Object;)I", [ssb, span_b]) == -1
    _invoke(vm, ssb_cls, ssb_cls, "clearSpans", "()V", [ssb])
    assert _invoke(vm, ssb_cls, ssb_cls, "getSpanStart", "(Ljava/lang/Object;)I", [ssb, span_a]) == -1
