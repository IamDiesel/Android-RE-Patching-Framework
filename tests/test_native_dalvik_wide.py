# -*- coding: utf-8 -*-
"""Tests fuer MS-4 Opcode-Breite: long/float/double-Arithmetik, cmp-Familie, casts, switch, throw, fill-array-data."""
import pytest
from core.java.dalvik import DalvikVM, DalvikException


def test_long_arithmetic():
    vm = DalvikVM()
    assert vm.run_method([("const", 0, 5), ("const", 1, 3), ("add-long", 2, 0, 1), ("return", 2)], 3) == 8
    assert vm.run_method([("const", 0, 0xfffffff9), ("int-to-long", 0, 0), ("const", 1, 2),
                          ("div-long", 2, 0, 1), ("return", 2)], 3) == -3
    # add-long/2addr
    assert vm.run_method([("const", 0, 7), ("const", 1, 4), ("add-long/2addr", 0, 1), ("return", 0)], 2) == 11


def test_float_arithmetic():
    vm = DalvikVM()
    r = vm.run_method([("const", 0, 3), ("int-to-float", 1, 0), ("const", 2, 2), ("int-to-float", 3, 2),
                       ("mul-float", 4, 1, 3), ("return", 4)], 5)
    assert abs(r - 6.0) < 1e-9


def test_cmp_family():
    vm = DalvikVM()
    assert vm.run_method([("const", 0, 5), ("const", 1, 3), ("cmp-long", 2, 0, 1), ("return", 2)], 3) == 1
    assert vm.run_method([("const", 0, 3), ("const", 1, 5), ("cmp-long", 2, 0, 1), ("return", 2)], 3) == -1
    assert vm.run_method([("const", 0, 4), ("const", 1, 4), ("cmp-long", 2, 0, 1), ("return", 2)], 3) == 0


def test_throw_raises():
    vm = DalvikVM()
    with pytest.raises(DalvikException):
        vm.run_method([("const", 0, 0), ("throw", 0)], 1)


def test_packed_switch():
    vm = DalvikVM()
    sw = [("packed-switch", 0, 0, (3, 5)), ("const", 1, 99), ("return", 1),
          ("const", 1, 10), ("return", 1), ("const", 1, 20), ("return", 1)]
    assert vm.run_method(sw, 2, args=(0,)) == 10
    assert vm.run_method(sw, 2, args=(1,)) == 20
    assert vm.run_method(sw, 2, args=(5,)) == 99


def test_sparse_switch():
    vm = DalvikVM()
    sw = [("sparse-switch", 0, (10, 20), (3, 5)), ("const", 1, 0), ("return", 1),
          ("const", 1, 1), ("return", 1), ("const", 1, 2), ("return", 1)]
    assert vm.run_method(sw, 2, args=(10,)) == 1
    assert vm.run_method(sw, 2, args=(20,)) == 2
    assert vm.run_method(sw, 2, args=(99,)) == 0


def test_fill_array_data():
    vm = DalvikVM()
    fa = [("const", 0, 3), ("new-array", 1, 0, "[I"), ("fill-array-data", 1, (7, 8, 9)),
          ("const", 2, 1), ("aget", 3, 1, 2), ("return", 3)]
    assert vm.run_method(fa, 4) == 8


def test_real_dex_zero_unsupported_sample():
    import os
    dexp = "secrets/probe/mcp_native/dex/classes.dex"
    if not os.path.isfile(dexp):
        pytest.skip("classes.dex nicht vorhanden")
    from core.java.dex import DexFile
    from core.java.dex_code import decode_code
    dx = DexFile(open(dexp, "rb").read())
    unsup = 0; checked = 0
    for cd in dx.class_defs():
        for m in dx.class_methods(cd):
            if not m.code_off or m.is_native or m.is_abstract:
                continue
            md = decode_code(dx, dx.code_item(m.code_off), m.cls, m.name, m.sig, m.access)
            for ins in md.insns:
                if isinstance(ins[0], str) and ins[0].startswith("dex-unsupported"):
                    unsup += 1
            checked += 1
            if checked >= 1500:
                break
        if checked >= 1500:
            break
    assert unsup == 0
