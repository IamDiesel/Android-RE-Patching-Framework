# -*- coding: utf-8 -*-
"""Robustes Smali-Frontend: echter baksmali-Output (Annotationen, Payloads, Suffixe, Kommentare,
throw, casts, cmp, long/float-Arithmetik)."""
import pytest
from core.java import smali as Sm
from core.java.dalvik import DalvikVM


def test_skip_annotation_block():
    src = '''.class public Lcom/x/A;
.super Ljava/lang/Object;
# annotations
.annotation runtime Lkotlin/Metadata;
    d1 = { "value = {" }
    value = { "x" }
.end annotation
.method public static f()I
    .locals 1
    const/4 v0, 0x7
    return v0
.end method
'''
    cls, mds = Sm.parse_class(src)
    vm = DalvikVM()
    for md in mds:
        vm.register(md)
    assert vm.invoke(cls, "f", "()I", ()) == 7


def test_long_suffix_and_inline_comment():
    src = '''.method public static g()J
    .locals 2
    const-wide v0, 0x10L    # 16.0
    return-wide v0
.end method'''
    md = Sm.parse_method(src)
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke(md.cls, "g", "()J", ()) == 0x10


def test_packed_switch_payload():
    src = '''.method public static h(I)I
    .locals 1
    packed-switch p0, :pswitch_data_0
    const/4 v0, 0x0
    return v0
    :pswitch_0
    const/4 v0, 0x64
    return v0
    :pswitch_1
    const/4 v0, 0x65
    return v0
    :pswitch_data_0
    .packed-switch 0x0
        :pswitch_0
        :pswitch_1
    .end packed-switch
.end method'''
    md = Sm.parse_method(src)
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke(md.cls, "h", "(I)I", (0,)) == 100
    assert vm.invoke(md.cls, "h", "(I)I", (1,)) == 101
    assert vm.invoke(md.cls, "h", "(I)I", (9,)) == 0   # default


def test_throw_and_cast_and_long_bitwise():
    # Cast + long-Bitwise parsen & laufen
    src = '''.method public static k(J)J
    .locals 2
    const/4 v0, 0x3
    int-to-long v0, v0
    or-long v0, p0, v0
    return-wide v0
.end method'''
    md = Sm.parse_method(src)
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke(md.cls, "k", "(J)J", (8,)) == (8 | 3)
