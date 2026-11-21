# -*- coding: utf-8 -*-
"""AP2.1: spec-treue AOSP-/JDK-Standard-APIs (generisch, KEINE App-Heuristik).
Deckt die im obfuskierten String-Decoder/Kontrollfluss genutzten Methoden ab."""
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JObject, JArray


def _s():
    vm = DalvikVM(); return AndroidStubs(vm=vm)


def _c(st, cls, n, sig, args):
    ok, r = st.resolve(cls, n, sig, args, static=False); assert ok, (cls, n, sig); return r


def test_stringbuilder_append_char_builds_string():
    st = _s()
    sb = JObject("Ljava/lang/StringBuilder;", {})
    _c(st, "Ljava/lang/StringBuilder;", "<init>", "()V", [sb])
    for ch in "nativeLoad":
        _c(st, "Ljava/lang/StringBuilder;", "append", "(C)Ljava/lang/StringBuilder;", [sb, ord(ch)])
    assert st._txt(_c(st, "Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", [sb])) == "nativeLoad"
    assert _c(st, "Ljava/lang/StringBuilder;", "length", "()I", [sb]) == 10
    sbs = JObject("Ljava/lang/StringBuilder;", {})
    _c(st, "Ljava/lang/StringBuilder;", "<init>", "(Ljava/lang/String;)V", [sbs, JString("ab")])
    _c(st, "Ljava/lang/StringBuilder;", "append", "(Z)Ljava/lang/StringBuilder;", [sbs, 1])
    assert st._txt(_c(st, "Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", [sbs])) == "abtrue"


def test_character_and_codepoint():
    st = _s()
    ch = _c(st, "Ljava/lang/Character;", "valueOf", "(C)Ljava/lang/Character;", [0x41])
    assert _c(st, "Ljava/lang/Character;", "charValue", "()C", [ch]) == 0x41
    assert st._txt(_c(st, "Ljava/lang/Character;", "toString", "(C)Ljava/lang/String;", [0x7A])) == "z"
    assert _c(st, "Ljava/lang/String;", "codePointAt", "(I)I", [JString("ABC"), 1]) == ord("B")


def test_view_resolve_size_and_state():
    st = _s()
    AT_MOST = 0x80000000; EXACTLY = 0x40000000
    # EXACTLY -> specSize
    assert _c(st, "Landroid/view/View;", "resolveSizeAndState", "(III)I", [100, EXACTLY | 50, 0]) == 50
    # AT_MOST, specSize<size -> specSize | TOO_SMALL
    assert _c(st, "Landroid/view/View;", "resolveSizeAndState", "(III)I", [100, AT_MOST | 50, 0]) == (50 | 0x01000000)
    # AT_MOST, specSize>=size -> size
    assert _c(st, "Landroid/view/View;", "resolveSizeAndState", "(III)I", [40, AT_MOST | 50, 0]) == 40
    # resolveSize maskiert MEASURED_STATE
    assert _c(st, "Landroid/view/View;", "resolveSize", "(II)I", [100, EXACTLY | 50]) == 50


def test_expandable_packed_position_type():
    st = _s()
    assert _c(st, "Landroid/widget/ExpandableListView;", "getPackedPositionType", "(J)I", [0xFFFFFFFF]) == 2   # NULL
    assert _c(st, "Landroid/widget/ExpandableListView;", "getPackedPositionType", "(J)I", [0x8000000000000000]) == 1  # CHILD
    assert _c(st, "Landroid/widget/ExpandableListView;", "getPackedPositionType", "(J)I", [0x0000000100000002]) == 0  # GROUP


def test_complex_to_float():
    st = _s()
    # complex = (mantissa<<8) | (radix<<4); radix 0 -> *1/256
    val = _c(st, "Landroid/util/TypedValue;", "complexToFloat", "(I)F", [(5 << 8) | (0 << 4)])
    assert abs(val - (5 * 256) / 256.0) < 1e-6   # mant=0x500=1280 -> *1/256 = 5.0
    assert abs(val - 5.0) < 1e-6


def test_drawable_resolve_opacity():
    st = _s()
    f = lambda o1, o2: _c(st, "Landroid/graphics/drawable/Drawable;", "resolveOpacity", "(II)I", [o1, o2])
    assert f(-1, -1) == -1       # beide OPAQUE
    assert f(-1, 0) == 0         # UNKNOWN dominiert
    assert f(-1, -3) == -3       # TRANSLUCENT
    assert f(-1, -2) == -2       # TRANSPARENT


def test_quartsec_to_deg_and_maplibraryname():
    st = _s()
    assert abs(_c(st, "Landroid/telephony/cdma/CdmaCellLocation;", "convertQuartSecToDecDegrees", "(I)D",
                  [14400]) - 1.0) < 1e-9
    assert st._txt(_c(st, "Ljava/lang/System;", "mapLibraryName", "(Ljava/lang/String;)Ljava/lang/String;",
                      [JString("reactnative")])) == "libreactnative.so"


def test_atomic_reference_field_updater():
    st = _s()
    ARFU = "Ljava/util/concurrent/atomic/AtomicReferenceFieldUpdater;"
    upd = _c(st, ARFU, "newUpdater",
             "(Ljava/lang/Class;Ljava/lang/Class;Ljava/lang/String;)L%s;" % ARFU[1:-1],
             [JObject("Ljava/lang/Class;", {}), JObject("Ljava/lang/Class;", {}), JString("val")])
    obj = JObject("Lx/Y;", {})
    _c(st, ARFU, "set", "(Ljava/lang/Object;Ljava/lang/Object;)V", [upd, obj, JString("hi")])
    got = _c(st, ARFU, "get", "(Ljava/lang/Object;)Ljava/lang/Object;", [upd, obj])
    assert st._txt(got) == "hi"
