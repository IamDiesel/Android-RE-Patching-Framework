# -*- coding: utf-8 -*-
"""Dalvik-Spec-Fixes (Review 2026-11-18): 64-bit const/return, IEEE-Float, invoke/instance-of."""
from core.java.dalvik import DalvikVM


# ---- Fix 1: const-wide/return-wide 64-bit ----
def test_const_wide_keeps_64bit_cmp_long_negative():
    vm = DalvikVM()
    # const-wide v0,-1 ; const-wide v2,0 ; cmp-long v4,v0,v2 ; return v4  -> -1 (<0), nicht +1
    r = vm.run_method([("const", 0, -1), ("const", 2, 0), ("cmp-long", 4, 0, 2), ("return", 4)], 5)
    assert r == -1


def test_return_wide_keeps_high_bits():
    vm = DalvikVM()
    vm.register_method("Lw;", "big", "()J",
                       [("const", 0, 0x100000000), ("return-wide", 0)], nregs=1, nargs=0, static=True)
    assert vm.invoke("Lw;", "big", "()J", ()) == 0x100000000


def test_int_op_still_32bit_after_u64_const():
    vm = DalvikVM()
    # add-int wraps auf 32 bit trotz 64-bit-Speicherung
    r = vm.run_method([("const", 0, 0x7fffffff), ("const", 1, 1),
                       ("add-int", 2, 0, 1), ("return", 2)], 3)
    assert r == -0x80000000     # 32-bit overflow
