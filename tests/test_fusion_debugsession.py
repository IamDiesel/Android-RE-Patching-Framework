# -*- coding: utf-8 -*-
"""Stufe 3: Fusion-Debugger (DebugSession) — Java-Breakpoint haelt, native Seite (Engine-Register/
Speicher) im selben Halt beobachtbar + manipulierbar; Grenz-Hook markiert den Uebergang."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.native_link import install_native_link, NativeMethodTable
from core.java.jni_boundary import install_jni_boundary
from core.java.debug_unified import DebugSession

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


def _fusion_vm():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, 0x1000, bytes.fromhex(_BLOB))
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(APP)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm); vm.runtime = _StubRuntime(stubs)
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register("Lt/App;", "nget", "(I)I", 0x1000, ret="int")
    install_jni_boundary(vm.bridge, vm=vm)
    return vm, eng, jni


def test_debugsession_wraps_fusion_vm():
    vm, eng, jni = _fusion_vm()
    sess = DebugSession(eng, jni, vm=vm)
    assert sess.vm is vm
    assert sess.jdbg.vm is vm            # EIN Debugger ueber denselben VM
    assert vm.bridge is not None and getattr(vm.bridge, "boundary_hook", None) is not None


def test_java_hold_exposes_native_side():
    vm, eng, jni = _fusion_vm()
    sess = DebugSession(eng, jni, vm=vm)
    sess.add_java_breakpoint("Lt/App;", "run", "()I", 3)   # vor 'return v1'
    sess.start("Lt/App;", "run", "()I", (), stop_at_entry=False)
    assert sess.jdbg.mode == "paused"
    # native Seite im selben Halt lesbar: x0 traegt die native Rueckgabe (41+1=42)
    st = sess.native_state()
    assert int(st["x0"], 16) == 42
    # native Seite manipulierbar
    assert sess.native_set_reg(0, 0x1234)
    assert sess.native_reg(0) == 0x1234
    sess.native_mem_write(0x2000, b"\xaa\xbb")
    assert sess.native_mem_read(0x2000, 2) == b"\xaa\xbb"
    # Java-Seite im selben Halt ebenfalls (Register v1 noch nicht geschrieben -> setzbar)
    sess.set_java_register(1, 7)
    sess.cont()
    assert sess.jdbg.result == 7
    # der Java<->nativ-Uebergang ist als jni-Marker auf der Zeitachse vermerkt
    jm = [m for m in sess.jdbg.marker_list() if m["kind"] == "jni"]
    assert len(jm) == 1
