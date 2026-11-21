# -*- coding: utf-8 -*-
"""Stufe 1: Java ruft echte native Lib-Funktion in EINEM gläsernen Lauf (ueber normalen invoke).

Der Native-Router (native_link) leitet native Methoden an die arm64-Engine; Grenz-Hook macht die
Schnittstelle beobachtbar + manipulierbar. Kein hand-gebautes invoke-native."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.native_link import install_native_link, NativeMethodTable
from core.java.jni_boundary import install_jni_boundary

_BLOB = "40040091c0035fd6"   # add x0,x2,#1 ; ret

APP = """.class public Lt/App;
.super Ljava/lang/Object;
.method public static run()I
    .registers 2
    const v0, 0x29
    invoke-static {v0}, Lt/App;->nget(I)I
    move-result v1
    return v1
.end method
"""

BOOT = """.class public Lt/Boot;
.super Ljava/lang/Object;
.method public static boot()V
    .registers 1
    const-string v0, "db"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    return-void
.end method
"""


def _engine():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, 0x1000, bytes.fromhex(_BLOB))
    return eng, jni


def _glass_vm(src):
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(src)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    return vm


def test_java_calls_real_native_in_one_run():
    vm = _glass_vm(APP)
    eng, jni = _engine()
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register("Lt/App;", "nget", "(I)I", 0x1000, ret="int", static=True)
    hook = install_jni_boundary(vm.bridge, vm=vm)
    assert vm.invoke("Lt/App;", "run", "()I", ()) == 42     # 41 -> echte native Funktion -> 42
    assert len(hook.log) == 1
    e = hook.log.entries[0]
    assert e["fnptr"] == 0x1000 and e["args_in"] == [41] and e["ret_eff"] == 42


def test_boundary_manipulation_through_router():
    vm = _glass_vm(APP)
    eng, jni = _engine()
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register("Lt/App;", "nget", "(I)I", 0x1000, ret="int")
    hook = install_jni_boundary(vm.bridge, vm=vm)
    hook.add_action_point(fnptr=0x1000, ret_expr="0")        # Rueckgabe erzwingen
    assert vm.invoke("Lt/App;", "run", "()I", ()) == 0


def test_input_manipulation_through_router():
    vm = _glass_vm(APP)
    eng, jni = _engine()
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register("Lt/App;", "nget", "(I)I", 0x1000, ret="int")
    hook = install_jni_boundary(vm.bridge, vm=vm)
    hook.add_action_point(fnptr=0x1000, arg_assigns=[{"index": 0, "value": 100, "const": True}])
    assert vm.invoke("Lt/App;", "run", "()I", ()) == 101     # native(100)+1


def test_unregistered_native_falls_through_to_stubs():
    vm = _glass_vm(APP)
    eng, jni = _engine()
    install_native_link(vm, eng, jni)                        # leere Tabelle
    # nget nicht registriert -> Stub-Pfad -> 0 (altes Verhalten bleibt)
    assert vm.invoke("Lt/App;", "run", "()I", ()) == 0


def test_loadlibrary_triggers_loader():
    vm = _glass_vm(BOOT)
    eng, jni = _engine()
    loaded = []
    install_native_link(vm, eng, jni, on_load_library=lambda nm: loaded.append(nm))
    vm.invoke("Lt/Boot;", "boot", "()V", ())
    assert loaded == ["db"]
