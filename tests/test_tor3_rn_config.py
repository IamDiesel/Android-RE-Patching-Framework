# -*- coding: utf-8 -*-
"""Tor-3: BuildConfig-Feld-Defaults (true/false, F/D) + Boolean/Long-Boxing (RN New-Architecture-Config)."""
from core.java.dalvik import DalvikVM
from core.java.smali import parse_field_defaults
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString


def test_field_defaults_bool_true_false():
    d = parse_field_defaults(
        ".field public static final IS_NEW_ARCHITECTURE_ENABLED:Z = true\n"
        ".field public static final DEBUG:Z = false\n"
        ".field public static final RATE:F = 1.5f\n"
        ".field public static final VC:I = 0x7b\n")
    assert d["IS_NEW_ARCHITECTURE_ENABLED"] == 1
    assert d["DEBUG"] == 0
    assert abs(d["RATE"] - 1.5) < 1e-9
    assert d["VC"] == 123


def test_boolean_boxing_roundtrip():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); vm.runtime = stubs
    h, box = stubs.resolve("Ljava/lang/Boolean;", "valueOf", "(Z)Ljava/lang/Boolean;", [1], True)
    assert h and isinstance(box, JObject)
    h2, val = stubs.resolve("Ljava/lang/Boolean;", "booleanValue", "()Z", [box], False)
    assert h2 and val == 1
    # false
    _, box0 = stubs.resolve("Ljava/lang/Boolean;", "valueOf", "(Z)Ljava/lang/Boolean;", [0], True)
    _, val0 = stubs.resolve("Ljava/lang/Boolean;", "booleanValue", "()Z", [box0], False)
    assert val0 == 0


def test_long_boxing_roundtrip():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    _, box = stubs.resolve("Ljava/lang/Long;", "valueOf", "(J)Ljava/lang/Long;", [0x100000000], True)
    _, val = stubs.resolve("Ljava/lang/Long;", "longValue", "()J", [box], False)
    assert val == 0x100000000


def test_unbox_handles_raw_and_wrapper():
    assert AndroidStubs._unbox([JObject("Ljava/lang/Boolean;", {"value": 1})]) == 1
    assert AndroidStubs._unbox([7]) == 7
    assert AndroidStubs._unbox([None]) == 0
