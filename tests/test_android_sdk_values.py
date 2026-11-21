# -*- coding: utf-8 -*-
"""Spec-treue Android-SDK-Werte (heilt SoLoader -34): Nicht-Null-Sonden statt stummem 0."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs


def _stub():
    vm = DalvikVM(); return AndroidStubs(vm=vm)


def test_viewconfiguration_timeouts_nonzero():
    st = _stub()
    _, tap = st.resolve("Landroid/view/ViewConfiguration;", "getTapTimeout", "()I", [], True)
    assert tap == 100                      # 100 - 34 = 66 (nicht -34)
    _, jump = st.resolve("Landroid/view/ViewConfiguration;", "getJumpTapTimeout", "()I", [], True)
    assert jump == 500
    _, zoom = st.resolve("Landroid/view/ViewConfiguration;", "getZoomControlsTimeout", "()J", [], True)
    assert zoom == 3000


def test_systemclock_positive():
    st = _stub()
    for m in ("uptimeMillis", "elapsedRealtime", "elapsedRealtimeNanos"):
        _, v = st.resolve("Landroid/os/SystemClock;", m, "()J", [], True)
        assert v > 0


def test_textutils_indexof_minus1():
    st = _stub()
    _, v = st.resolve("Landroid/text/TextUtils;", "indexOf", "(Ljava/lang/CharSequence;C)I", [0, 65], True)
    assert v == -1
    _, l = st.resolve("Landroid/text/TextUtils;", "lastIndexOf", "(Ljava/lang/CharSequence;C)I", [0, 65], True)
    assert l == -1


def test_color_argb_bits():
    st = _stub()
    _, c = st.resolve("Landroid/graphics/Color;", "argb", "(IIII)I", [0xff, 0x12, 0x34, 0x56], True)
    assert c == 0xff123456
    _, al = st.resolve("Landroid/graphics/Color;", "alpha", "(I)I", [0xff123456], True)
    assert al == 0xff


def test_measurespec_roundtrip():
    st = _stub()
    _, spec = st.resolve("Landroid/view/View$MeasureSpec;", "makeMeasureSpec", "(II)I",
                         [100, 0x40000000], True)
    _, size = st.resolve("Landroid/view/View$MeasureSpec;", "getSize", "(I)I", [spec], True)
    assert size == 100


def test_process_ids_nonzero():
    st = _stub()
    _, pid = st.resolve("Landroid/os/Process;", "myPid", "()I", [], True)
    assert pid > 0
    _, gid = st.resolve("Landroid/os/Process;", "getGidForName", "(Ljava/lang/String;)I", [0], True)
    assert gid == -1
