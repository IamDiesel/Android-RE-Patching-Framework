# -*- coding: utf-8 -*-
"""Faithful Reflection + statische Feld-Defaults + cmpl-float/double-Dispatch.
Deckt die Tor-2-Durchbruch-Fixes (2026-11-18) ab: onCreate laeuft durch die Division."""
import os, pytest
from core.java.dalvik import DalvikVM
from core.java.smali import parse_class, parse_field_defaults, load_into
from core.java.objects import JObject, JArray, JString


# ---- cmpl-float/double darf NICHT als Float-Arithmetik fehlgeleitet werden ----
def test_cmpl_double_not_routed_to_fbin():
    vm = DalvikVM()
    # cmpl-double v2,v0,v1 : 1.0 vs 2.0 -> -1 (kein KeyError('cmpl'))
    r = vm.run_method([("const", 0, 1), ("int-to-double", 0, 0),
                       ("const", 1, 2), ("int-to-double", 1, 1),
                       ("cmpl-double", 2, 0, 1), ("return", 2)], 3)
    assert r == -1


def test_cmpg_float_still_works():
    vm = DalvikVM()
    r = vm.run_method([("const", 0, 5), ("int-to-float", 0, 0),
                       ("const", 1, 2), ("int-to-float", 1, 1),
                       ("cmpg-float", 2, 0, 1), ("return", 2)], 3)
    assert r == 1


def test_add_float_still_routes_to_fbin():
    vm = DalvikVM()
    r = vm.run_method([("const", 0, 3), ("int-to-float", 0, 0),
                       ("const", 1, 4), ("int-to-float", 1, 1),
                       ("add-float", 2, 0, 1), ("return", 2)], 3)
    assert abs(r - 7.0) < 1e-9


# ---- Statische Feld-Defaults ----
def test_parse_field_defaults_basic():
    d = parse_field_defaults(
        ".field private static $11:I = 0x1\n"
        ".field private static $10:I = 0x0\n"
        ".field private static final $$e:I\n")
    assert d["$11"] == 1 and d["$10"] == 0 and "$$e" not in d   # impliziter Default nicht gelistet


class _Prov:
    """Minimaler Provider mit Feld-Defaults fuer eine Klasse."""
    def __init__(self, src):
        self._cls, self._mds = parse_class(src)
        self._fd = parse_field_defaults(src)
    def get_class(self, desc):
        return self._mds if desc == self._cls else None
    def get_field_defaults(self, desc):
        return self._fd if desc == self._cls else {}


def test_field_default_applied_before_use():
    src = (".class public LD;\n.super Ljava/lang/Object;\n"
           ".field public static F:I = 0x2a\n"
           ".method public static get()I\n.registers 1\n"
           "sget v0, LD;->F:I\nreturn v0\n.end method\n")
    vm = DalvikVM(); vm.class_provider = _Prov(src)
    for md in parse_class(src)[1]:
        vm.register(md)
    assert vm.invoke("LD;", "get", "()I", ()) == 0x2a   # Default 42 vor erstem sput


# ---- Faithful Reflection: Field getInt/setInt + Constructor newInstance laeuft <init> ----
def test_reflective_field_roundtrip_and_ctor_init():
    from core.java.dex_loader import AndroidStubs
    src = (".class public LRefl;\n.super Ljava/lang/Object;\n"
           ".field public static S:I = 0x7\n"
           ".field public inst:I\n"
           ".method public constructor <init>(I)V\n.registers 2\n"
           "iput p1, p0, LRefl;->inst:I\nreturn-void\n.end method\n")
    vm = DalvikVM()
    vm.class_provider = _Prov(src)   # liefert Feld-Default S=7 vor clinit
    stubs = AndroidStubs(vm=vm)
    vm.runtime = stubs
    for md in parse_class(src)[1]:
        vm.register(md)
    clsobj = JObject("Ljava/lang/Class;", {"name": "LRefl;"})
    # getDeclaredField + getInt -> liest statisches Feld (Default 7)
    fobj = stubs._cls_getfield([clsobj, JString("S")])
    assert stubs._field_get([fobj, None], "I") == 7
    # setInt -> schreibt
    stubs._field_set([fobj, None, 99])
    assert vm.statics.get("LRefl;", {}).get("S") == 99
    # newInstance mit Arg laeuft <init> (setzt Instanzfeld)
    ctor = stubs._cls_getctor([clsobj, JArray("[Ljava/lang/Class;", [0])])
    obj = stubs._ctor_newinstance([ctor, JArray("[Ljava/lang/Object;", [55])])
    assert isinstance(obj, JObject) and obj.cls == "LRefl;" and obj.fields.get("inst") == 55
