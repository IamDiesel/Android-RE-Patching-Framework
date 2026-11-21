# -*- coding: utf-8 -*-
"""Stufe 2: Java -> native (Router) fuehrt socket()+connect() auf DERSELBEN Engine aus; OS-8
zeichnet den nativen Socket auf; net_unified zeigt ihn als [nativ] - alles in einem Lauf."""
import struct
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from services.native_os_service import NativeOsService
from core.java.native_link import install_native_link, NativeMethodTable
from core.java.jni_boundary import install_jni_boundary
from core.java.net_unified import unified

# native: mov x19,x2 ; socket(2,1,0) ; mov x1,x19 ; connect(x0,x1,#16) ; ret
_BLOB = ("f30302aa400080d2210080d2020080d2c81880d2010000d4"
         "e10313aa020280d2681980d2010000d4c0035fd6")
_PTR = 0x72002000

NET = """.class public Lt/Net;
.super Ljava/lang/Object;
.method public static run()I
    .registers 2
    const v0, 0x72002000
    invoke-static {v0}, Lt/Net;->nconnect(I)I
    move-result v1
    return v1
.end method
"""


def _fusion():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    osv = NativeOsService(publish=False)
    osv.attach_env(eng, maps="72000000-72010000 rw-p 0 00:00 0 [anon:scratch]\n")
    osv.install(eng)
    svc.load_blob(eng, 0x1000, bytes.fromhex(_BLOB))
    eng.cpu.m.wb(_PTR, struct.pack("<H", 2) + struct.pack(">H", 443) + bytes([1, 2, 3, 4]) + b"\x00" * 8)
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(NET)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm); vm.runtime = _StubRuntime(stubs)
    table = NativeMethodTable()
    install_native_link(vm, eng, jni, table=table)
    table.register("Lt/Net;", "nconnect", "(I)I", 0x1000, ret="int")
    hook = install_jni_boundary(vm.bridge, vm=vm)
    return vm, eng, hook


def test_native_socket_in_fusion_run_shows_unified():
    vm, eng, hook = _fusion()
    vm.invoke("Lt/Net;", "run", "()I", ())
    # OS-8 hat den nativen connect aufgezeichnet
    log = eng._os.net["log"]
    assert ("connect", "1.2.3.4", 443) in log
    # vereinheitlichte Sicht zeigt die native Quelle
    rows = unified(None, eng._os.net)
    nat = [r for r in rows if r["source"] == "native"]
    assert nat and nat[0]["kind"] == "connect" and "1.2.3.4:443" in nat[0]["label"]
    # der Java<->nativ-Uebergang ist zugleich beobachtet (Grenz-Hook)
    assert len(hook.log) == 1 and hook.log.entries[0]["fnptr"] == 0x1000


def test_fusion_os_net_controller_surface():
    """fusion_os_net()-Logik (Controller) liefert das native Netz-Log des Laufs."""
    from ui.controllers.debugger_controller import DebuggerController
    vm, eng, hook = _fusion()
    vm.invoke("Lt/Net;", "run", "()I", ())

    class _Ctl:
        _fusion = {"eng": eng}
    _Ctl.fusion_os_net = DebuggerController.fusion_os_net
    net = _Ctl().fusion_os_net()
    assert net and ("connect", "1.2.3.4", 443) in net["log"]
