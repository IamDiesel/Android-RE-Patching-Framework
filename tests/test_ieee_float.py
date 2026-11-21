# -*- coding: utf-8 -*-
"""Fix 2: IEEE-754-Rohbits in float/double-Arithmetik + Java-Saettigung bei Casts."""
import struct
from core.java.dalvik import DalvikVM, _f32, _f64, _f2i, _f2l


def test_raw_bits_interpreted_as_float():
    # 0xbf800000 = -1.0f  (nicht +3.21e9)
    assert abs(_f32(0xbf800000) - (-1.0)) < 1e-9
    assert abs(_f64(0xbff0000000000000) - (-1.0)) < 1e-12
    assert _f32(0) == 0.0 and _f64(0) == 0.0     # Null bleibt 0.0


def test_cmpl_float_on_raw_bits():
    vm = DalvikVM()
    # const v0, 0xbf800000 (-1.0f) ; const v1, 0x3f800000 (+1.0f) ; cmpl-float -> -1
    r = vm.run_method([("const", 0, 0xbf800000), ("const", 1, 0x3f800000),
                       ("cmpl-float", 2, 0, 1), ("return", 2)], 3)
    assert r == -1


def test_add_float_on_raw_bits():
    vm = DalvikVM()
    # -1.0f + 1.0f = 0.0
    r = vm.run_method([("const", 0, 0xbf800000), ("const", 1, 0x3f800000),
                       ("add-float", 2, 0, 1), ("return", 2)], 3)
    assert abs(r) < 1e-9


def test_cmpg_double_nan():
    vm = DalvikVM()
    nan_bits = struct.unpack("<Q", struct.pack("<d", float("nan")))[0]
    r = vm.run_method([("const", 0, nan_bits), ("const", 2, 0),
                       ("int-to-double", 2, 2), ("cmpg-double", 4, 0, 2), ("return", 4)], 5)
    assert r == 1       # cmpg: NaN -> +1


def test_float_to_int_saturation():
    assert _f2i(float("nan")) == 0
    assert _f2i(1e30) == 2147483647
    assert _f2i(-1e30) == -2147483648
    assert _f2i(3.9) == 3 and _f2i(-3.9) == -3
    assert _f2l(float("nan")) == 0
    assert _f2l(1e40) == 9223372036854775807


def test_float_to_int_opcode_saturates():
    vm = DalvikVM()
    big = struct.unpack("<I", struct.pack("<f", 1e30))[0]
    r = vm.run_method([("const", 0, big), ("float-to-int", 1, 0), ("return", 1)], 2)
    assert r == 2147483647
