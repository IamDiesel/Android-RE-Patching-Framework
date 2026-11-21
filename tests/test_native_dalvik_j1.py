# -*- coding: utf-8 -*-
"""Tests fuer J1-Vollausbau: Opcode-Breite, Objektmodell, Intra-Dalvik-Methodenaufruf."""
import pytest

from core.java.dalvik import DalvikVM, DalvikException, MethodDef
from core.java.objects import JString, JObject, JArray


def test_arith_div_rem_bitops():
    vm = DalvikVM()
    # (20 / 6) = 3 ; (20 % 6) = 2 ; 3 & 2 = 2 ; 3 | 2 = 3 ; 3 ^ 2 = 1
    assert vm.run_method([("const", 0, 20), ("const", 1, 6), ("div-int", 2, 0, 1), ("return", 2)], 3) == 3
    assert vm.run_method([("const", 0, 20), ("const", 1, 6), ("rem-int", 2, 0, 1), ("return", 2)], 3) == 2
    assert vm.run_method([("const", 0, 3), ("const", 1, 2), ("and-int", 2, 0, 1), ("return", 2)], 3) == 2
    assert vm.run_method([("const", 0, 3), ("const", 1, 2), ("or-int", 2, 0, 1), ("return", 2)], 3) == 3
    assert vm.run_method([("const", 0, 3), ("const", 1, 2), ("xor-int", 2, 0, 1), ("return", 2)], 3) == 1


def test_arith_signed_div_and_shifts():
    vm = DalvikVM()
    # -7 / 2 = -3 (Richtung 0) ; 1 << 4 = 16 ; -16 >> 2 = -4 (arithmetisch) ; -1 ushr 28 = 15
    assert vm.run_method([("const", 0, -7), ("const", 1, 2), ("div-int", 2, 0, 1), ("return", 2)], 3) == -3
    assert vm.run_method([("const", 0, 1), ("const", 1, 4), ("shl-int", 2, 0, 1), ("return", 2)], 3) == 16
    assert vm.run_method([("const", 0, -16), ("const", 1, 2), ("shr-int", 2, 0, 1), ("return", 2)], 3) == -4
    assert vm.run_method([("const", 0, -1), ("const", 1, 28), ("ushr-int", 2, 0, 1), ("return", 2)], 3) == 15


def test_lit_2addr_neg_not():
    vm = DalvikVM()
    assert vm.run_method([("const", 0, 5), ("add-int/lit", 1, 0, 10), ("return", 1)], 2) == 15
    assert vm.run_method([("const", 0, 5), ("rsub-int/lit", 1, 0, 10), ("return", 1)], 2) == 5    # 10 - 5
    assert vm.run_method([("const", 0, 7), ("const", 1, 3), ("add-int/2addr", 0, 1), ("return", 0)], 2) == 10
    assert vm.run_method([("const", 0, 5), ("neg-int", 1, 0), ("return", 1)], 2) == -5
    assert vm.run_method([("const", 0, 0), ("not-int", 1, 0), ("return", 1)], 2) == -1


def test_div_by_zero_raises():
    vm = DalvikVM()
    with pytest.raises(DalvikException):
        vm.run_method([("const", 0, 1), ("const", 1, 0), ("div-int", 2, 0, 1), ("return", 2)], 3)


def test_compare_branches_max():
    vm = DalvikVM()
    # max(a,b): if-ge a,b -> return a else return b
    prog = [("if-ge", 0, 1, 4), ("return", 1), ("nop",), ("nop",), ("return", 0)]
    assert vm.run_method(prog, 2, args=(9, 4)) == 9
    assert vm.run_method(prog, 2, args=(4, 9)) == 9


def test_object_field_roundtrip():
    vm = DalvikVM()
    # new Lp; ; iput #42 -> .n ; iget -> return
    prog = [("new-instance", 0, "Lp;"), ("const", 1, 42), ("iput", 1, 0, "Lp;", "n"),
            ("iget", 2, 0, "Lp;", "n"), ("return", 2)]
    assert vm.run_method(prog, 3) == 42


def test_array_ops():
    vm = DalvikVM()
    prog = [("const", 0, 3), ("new-array", 1, 0, "[I"),
            ("const", 2, 1), ("const", 3, 77), ("aput", 3, 1, 2),
            ("aget", 4, 1, 2), ("return", 4)]
    assert vm.run_method(prog, 5) == 77
    # array-length
    prog2 = [("const", 0, 5), ("new-array", 1, 0, "[I"), ("array-length", 2, 1), ("return", 2)]
    assert vm.run_method(prog2, 3) == 5


def test_const_string_return_object():
    vm = DalvikVM()
    r = vm.run_method([("const-string", 0, "hallo"), ("return-object", 0)], 1)
    assert isinstance(r, JString) and r.text == "hallo"


def test_statics():
    vm = DalvikVM()
    vm.run_method([("const", 0, 99), ("sput", 0, "Lcfg;", "K")], 1)
    assert vm.run_method([("sget", 0, "Lcfg;", "K"), ("return", 0)], 1) == 99


def test_invoke_static_registry():
    vm = DalvikVM()
    vm.register_method("Lmath;", "sum", "(II)I",
                       [("add-int", 0, 0, 1), ("return", 0)], nregs=2, nargs=2, static=True)
    caller = [("const", 0, 3), ("const", 1, 4),
              ("invoke-static", (0, 1), "Lmath;", "sum", "(II)I"),
              ("move-result", 2), ("return", 2)]
    assert vm.run_method(caller, 3) == 7


def test_invoke_virtual_dispatch_on_receiver_class():
    vm = DalvikVM()
    # Basisklasse val()=0, Subklasse val()=field n ; Dispatch nach Laufzeit-Klasse
    vm.register_method("Lbase;", "val", "()I", [("const", 0, 0), ("return", 0)], nregs=1, nargs=1)
    vm.register_method("Lsub;", "val", "()I",
                       [("iget", 0, 0, "Lsub;", "n"), ("return", 0)], nregs=1, nargs=1)
    caller = [("new-instance", 0, "Lsub;"), ("const", 1, 5), ("iput", 1, 0, "Lsub;", "n"),
              ("invoke-virtual", (0,), "Lbase;", "val", "()I"), ("move-result", 2), ("return", 2)]
    assert vm.run_method(caller, 3) == 5


def test_invoke_recursion_factorial():
    vm = DalvikVM()
    # fact(n) = n<=1 ? 1 : n*fact(n-1) ; nregs=4,nargs=1 -> Parameter n liegt in v3 (param_base=3)
    body = [("const", 0, 1),                            # 0: v0 = 1
            ("if-le", 3, 0, 7),                         # 1: if n<=1 goto 7
            ("add-int/lit", 1, 3, -1),                  # 2: v1 = n-1
            ("invoke-static", (1,), "Lf;", "fact", "(I)I"),  # 3
            ("move-result", 2),                         # 4: v2 = fact(n-1)
            ("mul-int", 0, 3, 2),                       # 5: v0 = n * v2
            ("return", 0),                              # 6
            ("const", 0, 1),                            # 7: v0 = 1
            ("return", 0),                              # 8
            ]
    vm.register_method("Lf;", "fact", "(I)I", body, nregs=4, nargs=1, static=True)
    assert vm.invoke("Lf;", "fact", "(I)I", (5,)) == 120
    assert vm.invoke("Lf;", "fact", "(I)I", (1,)) == 1


def test_coverage_and_missing():
    vm = DalvikVM()
    caller = [("invoke-static", (), "Lx;", "gone", "()I"), ("move-result", 0), ("return", 0)]
    assert vm.run_method(caller, 1) == 0           # nicht aufgeloest -> 0
    assert vm.missing and vm.missing[0]["name"] == "gone"
    assert vm.coverage.get("invoke-static") == 1
