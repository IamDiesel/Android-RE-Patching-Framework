# -*- coding: utf-8 -*-
"""Tests fuer das begrenzte Smali-Frontend (J1): parse .method/.class -> Tupel-IR -> DalvikVM."""
import pytest

from core.java.dalvik import DalvikVM
from core.java.smali import parse_method, parse_class, load_into, SmaliUnsupported


def test_parse_static_add_with_params():
    src = """
.method public static add(II)I
    .locals 1
    add-int v0, p0, p1
    return v0
.end method
"""
    md = parse_method(src, cls="Lx;")
    vm = DalvikVM()
    vm.register(md)
    assert vm.invoke("Lx;", "add", "(II)I", (3, 4)) == 7
    assert md.nregs == 3 and md.nargs == 2 and md.param_base == 1


def test_parse_loop_sum():
    src = """
.method public static sumTo(I)I
    .locals 2
    const/4 v0, 0x0
    const/4 v1, 0x1
    :loop
    if-gt v1, p0, :done
    add-int/2addr v0, v1
    add-int/lit8 v1, v1, 0x1
    goto :loop
    :done
    return v0
.end method
"""
    md = parse_method(src, cls="Lx;")
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke("Lx;", "sumTo", "(I)I", (5,)) == 15     # 1+2+3+4+5
    assert vm.invoke("Lx;", "sumTo", "(I)I", (0,)) == 0


def test_parse_object_field():
    src = """
.method public static mk()I
    .locals 2
    new-instance v0, Lp;
    const/16 v1, 0x2a
    iput v1, v0, Lp;->n:I
    iget v1, v0, Lp;->n:I
    return v1
.end method
"""
    vm = DalvikVM(); vm.register(parse_method(src, cls="Lx;"))
    assert vm.invoke("Lx;", "mk", "()I", ()) == 42


def test_parse_class_cross_invoke():
    src = """
.class public Lmymath;
.super Ljava/lang/Object;
.method public static dbl(I)I
    .locals 1
    add-int v0, p0, p0
    return v0
.end method
.method public static quad(I)I
    .locals 1
    invoke-static {p0}, Lmymath;->dbl(I)I
    move-result v0
    invoke-static {v0}, Lmymath;->dbl(I)I
    move-result v0
    return v0
.end method
"""
    vm = DalvikVM()
    cls, mds = parse_class(src)
    assert cls == "Lmymath;" and len(mds) == 2
    load_into(vm, src)
    assert vm.invoke("Lmymath;", "quad", "(I)I", (3,)) == 12


def test_unsupported_opcode_raises():
    # monitor-enter u. a. werden inzwischen unterstuetzt; ein wirklich unbekannter Opcode muss werfen.
    src = """
.method public static x()V
    .locals 1
    frobnicate-xyz v0
    return-void
.end method
"""
    with pytest.raises(SmaliUnsupported) as ei:
        parse_method(src)
    assert ei.value.op == "frobnicate-xyz"
