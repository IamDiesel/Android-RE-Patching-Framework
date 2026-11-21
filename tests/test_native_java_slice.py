# -*- coding: utf-8 -*-
"""Tests fuer J0 (Thin Vertical Slice): Java→JNI→native→Java (Rueckgabe) ueber die Bruecke.

Hermetisch, zielagnostisch: winzige hand-assemblierte native Funktionen (kein libdb0c).
"""
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from services.native_java_service import NativeJavaService
from core.java.bridge import JavaToNativeBridge

# Hand-assemblierte AArch64-Funktionen (little-endian):
_ADD_X0_X2_1 = bytes.fromhex("40040091")   # add x0, x2, #1
_MOV_X0_X2 = bytes.fromhex("e00302aa")      # mov x0, x2   (orr x0, xzr, x2)
_RET = bytes.fromhex("c0035fd6")            # ret


def _setup():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return svc, eng, ffi, jni


def test_int_round_trip():
    """native: int f(env,this,int x){ return x+1; } -> Java sieht 42 fuer 41."""
    svc, eng, ffi, jni = _setup()
    svc.load_blob(eng, 0x1000, _ADD_X0_X2_1 + _RET)
    br = JavaToNativeBridge(eng, jni)
    assert br.call_native(0x1000, args=(41,), ret="int") == 42


def test_string_arg_marshalled_and_returned():
    """native: String f(env,this,String s){ return s; } -> Java-str geht als jstring-Handle rein
    und kommt als Python-str zurueck (Marshalling beidseitig)."""
    svc, eng, ffi, jni = _setup()
    svc.load_blob(eng, 0x2000, _MOV_X0_X2 + _RET)
    br = JavaToNativeBridge(eng, jni)
    assert br.call_native(0x2000, args=("hallo welt",), ret="string") == "hallo welt"


def test_this_jref_passed():
    """native: Object f(env,this,_){ return this; } -> die 'this'-jref geht als x1 rein; wird sie
    (ueber x0=this zurueckgegeben) als gueltiges jobject-Handle aufgeloest?"""
    svc, eng, ffi, jni = _setup()
    # mov x0, x1 ; ret   (x1 = this)
    svc.load_blob(eng, 0x3000, bytes.fromhex("e00301aa") + _RET)
    br = JavaToNativeBridge(eng, jni)
    h = br.call_native(0x3000, this_cls="Lcom/example/Foo;", ret="jref")
    rec = jni.st.handles.get(h)
    assert rec is not None and rec.get("kind") == "jobject"
    assert rec.get("cls") == "Lcom/example/Foo;"


def test_service_wrapper():
    svc, eng, ffi, jni = _setup()
    svc.load_blob(eng, 0x1000, _ADD_X0_X2_1 + _RET)
    js = NativeJavaService(publish=False)
    assert js.call_native(eng, jni, 0x1000, args=(7,), ret="int") == 8
