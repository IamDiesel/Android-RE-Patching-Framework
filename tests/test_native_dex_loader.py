# -*- coding: utf-8 -*-
"""Tests fuer J3: DexClassLoader (clinit/Registrierung) + AndroidStubs (Coverage-Loop) + Runtime-Wiring."""
import os
import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.runtime import JavaRuntime
from core.java.dex_loader import AndroidStubs
from core.java.dex import Method
from core.java.objects import JString, JObject


def _rt():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return JavaRuntime(eng, jni)


def test_android_stubs_builtin():
    st = AndroidStubs()
    ok, r = st.resolve("Landroid/util/Log;", "d", "(Ljava/lang/String;Ljava/lang/String;)I",
                       [JString("TAG"), JString("msg")], True)
    assert ok and r == 0
    # StringBuilder-Round-Trip
    sb = JObject("Ljava/lang/StringBuilder;")
    st.resolve("Ljava/lang/StringBuilder;", "<init>", "()V", [sb], False)
    st.resolve("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;",
               [sb, JString("ab")], False)
    st.resolve("Ljava/lang/StringBuilder;", "append", "(I)Ljava/lang/StringBuilder;", [sb, 12], False)
    ok, s = st.resolve("Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", [sb], False)
    assert ok and isinstance(s, JString) and s.text == "ab12"


def test_android_stubs_unknown_recorded():
    st = AndroidStubs()
    ok, r = st.resolve("Landroid/foo/Bar;", "baz", "()I", [], True)
    assert ok and r == 0
    assert st.missing and st.missing[0]["cls"] == "Landroid/foo/Bar;"
    # Nicht-Framework bleibt unbehandelt
    ok2, _ = st.resolve("Lmy/App;", "x", "()I", [], True)
    assert ok2 is False


def test_runtime_framework_call_via_stub():
    rt = _rt()
    caller = [("const-string", 0, "TAG"), ("const-string", 1, "msg"),
              ("invoke-static", (0, 1), "Landroid/util/Log;", "d",
               "(Ljava/lang/String;Ljava/lang/String;)I"),
              ("move-result", 2), ("return", 2)]
    rt.register_method("Lx;", "f", "()I", caller, nregs=3, nargs=0, static=True)
    assert rt.call("Lx;", "f", "()I", ()) == 0


class _FakeDex:
    def class_defs(self):
        yield {"name": "Lcfg;", "access": 0, "super": None, "class_data_off": 1}
    def class_methods(self, cd):
        return [Method("Lcfg;", "<clinit>", "()V", 0x8, 1)]
    def code_item(self, off):
        # const/16 v0,#42 ; sput v0, Lcfg;->K:I ; return-void
        return {"registers_size": 1, "ins_size": 0, "outs_size": 0, "tries_size": 0,
                "insns_size": 5, "insns": [0x0013, 0x002a, 0x0067, 0x0000, 0x000e]}
    def field(self, i): return ("Lcfg;", "K", "I")


def test_dex_loader_clinit_sets_static():
    rt = _rt()
    rt.attach_dex(_FakeDex())
    rt.load_dex_class("Lcfg;")
    assert rt.vm.statics.get("Lcfg;", {}).get("K") == 42


_DEX = "secrets/probe/mcp_native/dex/classes.dex"


@pytest.mark.skipif(not os.path.isfile(_DEX), reason="classes.dex nicht vorhanden")
def test_real_dex_load_class_registers_methods():
    from core.java.dex import DexFile
    rt = _rt()
    dx = DexFile(open(_DEX, "rb").read())
    rt.attach_dex(dx)
    # eine reale Klasse mit Methoden laden
    name = "L_COROUTINE/ArtificialStackFrames;"
    mds = rt.load_dex_class(name)
    assert len(mds) >= 1
    assert any(k[0] == name for k in rt.vm.registry)
