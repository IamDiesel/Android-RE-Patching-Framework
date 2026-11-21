# -*- coding: utf-8 -*-
"""#354: Autoload einer echten Library (IP-neutral). RegisterNatives-Ernte -> routbare native
Ausfuehrung; JNI_OnLoad finden + ausfuehren. Ohne LLU, nur geladene ELF-Struktur + Registrierungen."""
import types
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.native_link import install_native_link, NativeMethodTable
from core.java.fusion_autoload import (ret_of_sig, _to_dalvik, find_jni_onload,
                                       run_jni_onload, harvest_registered_natives)

_ADD = "40040091c0035fd6"                    # add x0,x2,#1 ; ret
_ONLOAD = "c00080d22000a0f2c0035fd6"         # movz x0,#6 ; movk x0,#1,lsl16 ; ret -> 0x10006

NAT = """.class public Lcom/app/Nat;
.super Ljava/lang/Object;
.method public static run()I
    .registers 2
    const v0, 0x29
    invoke-static {v0}, Lcom/app/Nat;->nget(I)I
    move-result v1
    return v1
.end method
"""


def test_ret_of_sig():
    assert ret_of_sig("(I)V") == "void"
    assert ret_of_sig("(II)I") == "int"
    assert ret_of_sig("(I)J") == "int"
    assert ret_of_sig("()Ljava/lang/String;") == "jref"
    assert ret_of_sig("(I)[B") == "jref"


def test_to_dalvik():
    assert _to_dalvik("com/app/Nat") == "Lcom/app/Nat;"
    assert _to_dalvik("com.app.Nat") == "Lcom/app/Nat;"
    assert _to_dalvik("Lcom/app/Nat;") == "Lcom/app/Nat;"


def test_harvest_registered_natives_routes_to_engine():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, 0x1000, bytes.fromhex(_ADD))
    # simuliert: JNI_OnLoad hat RegisterNatives aufgerufen
    jni.st.natives.append({"class": "com/app/Nat", "name": "nget", "sig": "(I)I", "fnptr": "0x1000"})
    table = NativeMethodTable()
    assert harvest_registered_natives(jni, table) == 1
    assert table.lookup("Lcom/app/Nat;", "nget", "(I)I")["fnptr"] == 0x1000
    # geerntete Methode ist wirklich ausfuehrbar aus Java heraus
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(NAT)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm); vm.runtime = _StubRuntime(stubs)
    install_native_link(vm, eng, jni, table=table)
    assert vm.invoke("Lcom/app/Nat;", "run", "()I", ()) == 42


def test_find_and_run_jni_onload():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    svc.load_blob(eng, 0x2000, bytes.fromhex(_ONLOAD))
    img = types.SimpleNamespace(dynsyms={"JNI_OnLoad": types.SimpleNamespace(value=0x2000)})
    addr = find_jni_onload(img)
    assert addr == 0x2000
    assert run_jni_onload(svc, eng, addr) == 0x10006     # JNI_VERSION_1_6


def test_find_jni_onload_missing():
    img = types.SimpleNamespace(dynsyms={})
    assert find_jni_onload(img) is None


def test_load_path_vs_loadlibrary_dispatch():
    """App-eigener Entpack-Weg: System.load(absPfad) -> Pfad-Loader (VFS), loadLibrary(name) ->
    gebundene Lib. Das OS entpackt nichts; die App liefert den Pfad ihrer selbst-entpackten Lib."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_jni_env_service import NativeJniEnvService
    from core.java.native_link import install_native_link
    BOOT = """.class public Lt/Boot;
.super Ljava/lang/Object;
.method public static boot()V
    .registers 1
    const-string v0, "/data/data/app/files/libX.so"
    invoke-static {v0}, Ljava/lang/System;->load(Ljava/lang/String;)V
    const-string v0, "y"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    return-void
.end method
"""
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(BOOT)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm); vm.runtime = _StubRuntime(stubs)
    paths, libs = [], []
    install_native_link(vm, eng, jni,
                        on_load_library=lambda n: libs.append(n),
                        on_load_path=lambda p: paths.append(p))
    vm.invoke("Lt/Boot;", "boot", "()V", ())
    assert paths == ["/data/data/app/files/libX.so"]
    assert libs == ["y"]
