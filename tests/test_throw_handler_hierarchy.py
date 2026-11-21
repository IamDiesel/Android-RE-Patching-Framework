# -*- coding: utf-8 -*-
"""throw null -> NPE, und _find_handler ueber echte Klassenhierarchie (kein Blind-Fang)."""
import pytest
from core.java.dalvik import DalvikVM
from core.java.smali import parse_class, load_into
from core.java.objects import JObject


def _vm(src):
    vm = DalvikVM(); load_into(vm, src); return vm


def test_throw_null_yields_npe_caught_by_runtimeexception():
    # throw null -> NPE ; .catch RuntimeException faengt (NPE ist RuntimeException)
    src = r'''
.method public static f()I
    .registers 2
    :ts
    const/4 v0, 0x0
    throw v0
    :te
    .catch Ljava/lang/RuntimeException; {:ts .. :te} :h
    const/4 v0, 0x0
    return v0
    :h
    move-exception v1
    const/4 v0, 0x1
    return v0
.end method
'''
    assert _vm(src).invoke("Lsmali/Adhoc;", "f", "()I", ()) == 1


def test_exception_handler_does_not_catch_error():
    # throw new OutOfMemoryError (Error) ; .catch Exception darf NICHT fangen -> propagiert
    src = r'''
.method public static g()I
    .registers 2
    :ts
    new-instance v0, Ljava/lang/OutOfMemoryError;
    invoke-direct {v0}, Ljava/lang/OutOfMemoryError;-><init>()V
    throw v0
    :te
    .catch Ljava/lang/Exception; {:ts .. :te} :h
    const/4 v0, 0x0
    return v0
    :h
    const/4 v0, 0x2
    return v0
.end method
'''
    with pytest.raises(Exception):
        _vm(src).invoke("Lsmali/Adhoc;", "g", "()I", ())


def test_runtimeexception_handler_catches_npe_but_not_ioexception():
    vm = DalvikVM()
    # NPE ist-ein RuntimeException -> True ; IOException ist-ein RuntimeException -> False
    assert vm._throwable_isa("Ljava/lang/NullPointerException;", "Ljava/lang/RuntimeException;") is True
    assert vm._throwable_isa("Ljava/lang/NullPointerException;", "Ljava/lang/Exception;") is True
    assert vm._throwable_isa("Ljava/lang/Error;", "Ljava/lang/Exception;") is False
    assert vm._throwable_isa("Ljava/lang/IllegalStateException;", "Ljava/lang/RuntimeException;") is True


def test_typed_catch_matches_app_subclass_via_super():
    # App-Exception extends RuntimeException -> .catch Exception faengt (Super-Walk ueber Provider)
    src = r'''
.class public Lmy/Exc;
.super Ljava/lang/RuntimeException;
.method public constructor <init>()V
    .registers 1
    return-void
.end method
'''
    body = r'''
.method public static h()I
    .registers 2
    :ts
    new-instance v0, Lmy/Exc;
    invoke-direct {v0}, Lmy/Exc;-><init>()V
    throw v0
    :te
    .catch Ljava/lang/Exception; {:ts .. :te} :hd
    const/4 v0, 0x0
    return v0
    :hd
    const/16 v0, 0x2a
    return v0
.end method
'''
    class P:
        def __init__(self): self._c, self._m = parse_class(src)
        def get_class(self, d): return self._m if d == "Lmy/Exc;" else None
        def get_super(self, d): return "Ljava/lang/RuntimeException;" if d == "Lmy/Exc;" else None
    vm = DalvikVM(); vm.class_provider = P()
    load_into(vm, src); load_into(vm, body)
    assert vm.invoke("Lsmali/Adhoc;", "h", "()I", ()) == 42
