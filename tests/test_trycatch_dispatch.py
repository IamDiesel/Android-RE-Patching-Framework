# -*- coding: utf-8 -*-
"""Try/Catch-Dispatch: .catch/.catchall -> Handler-Tabelle -> move-exception.
Deckt reviewer-Punkt 4 (Exceptions als Control-Flow, A1-Blocker) ab."""
import pytest
from core.java.dalvik import DalvikVM
from core.java.smali import parse_class, load_into


def _vm(src):
    vm = DalvikVM()
    load_into(vm, src)
    return vm


def test_catchall_catches_throw_and_move_exception_nonnull():
    src = r'''
.method public static f()I
    .registers 3
    :try_start_0
    new-instance v0, Ljava/lang/Exception;
    invoke-direct {v0}, Ljava/lang/Exception;-><init>()V
    throw v0
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0
    const/4 v1, 0x0
    return v1
    :catchall_0
    move-exception v2
    if-eqz v2, :was_null
    const/4 v1, 0x1
    return v1
    :was_null
    const/4 v1, 0x2
    return v1
.end method
'''
    assert _vm(src).invoke("Lsmali/Adhoc;", "f", "()I", ()) == 1


def test_typed_catch_matches_exception():
    src = r'''
.method public static g()I
    .registers 3
    :try_start_1
    new-instance v0, Ljava/lang/RuntimeException;
    invoke-direct {v0}, Ljava/lang/RuntimeException;-><init>()V
    throw v0
    :try_end_1
    .catch Ljava/lang/Exception; {:try_start_1 .. :try_end_1} :catch_1
    const/4 v1, 0x0
    return v1
    :catch_1
    move-exception v2
    const/16 v1, 0x2a
    return v1
.end method
'''
    assert _vm(src).invoke("Lsmali/Adhoc;", "g", "()I", ()) == 42


def test_no_handler_when_throw_outside_try():
    src = r'''
.method public static h()I
    .registers 2
    new-instance v0, Ljava/lang/Exception;
    invoke-direct {v0}, Ljava/lang/Exception;-><init>()V
    throw v0
.end method
'''
    with pytest.raises(Exception):
        _vm(src).invoke("Lsmali/Adhoc;", "h", "()I", ())


def test_handler_table_built_on_methoddef():
    _, mds = parse_class(r'''
.class public LT4;
.super Ljava/lang/Object;
.method public static k()V
    .registers 1
    :try_start_0
    nop
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0
    return-void
    :catchall_0
    return-void
.end method
''')
    md = next(m for m in mds if m.name == "k")
    assert len(md.handlers) == 1
    start, end, typ, hpc = md.handlers[0]
    assert typ is None and start < end and hpc >= end
