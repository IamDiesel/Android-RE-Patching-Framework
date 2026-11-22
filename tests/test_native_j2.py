# -*- coding: utf-8 -*-
"""Tests fuer J2: native->Java-Rueckruf ueber die JNIEnv-Fassade + Zwei-Wege-Loop (mit J0).

Hermetisch, zielagnostisch: eine winzige hand-assemblierte native Trampoline ruft ueber die
JNI-Funktionstabelle `CallStaticIntMethod` auf -> die Fassade delegiert an eine registrierte
Dalvik-Methode (kein libdb0c).
"""
import struct

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService, JNI_FUNCS, ENV_OBJ
from core.java.bridge import JavaToNativeBridge
from core.java.runtime import JavaRuntime, sig_argcount


def _setup():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return svc, eng, ffi, jni


def _w(x):
    return struct.pack("<I", x & 0xffffffff)


def test_sig_argcount():
    assert sig_argcount("()I") == 0
    assert sig_argcount("(I)I") == 1
    assert sig_argcount("(ILjava/lang/String;[B)V") == 3


def test_runtime_call_dalvik():
    """Service-Ebene: JavaRuntime fuehrt eine registrierte Dalvik-Methode aus."""
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    rt.register_method("Lm;", "dbl", "(I)I", [("add-int", 0, 0, 0), ("return", 0)],
                       nregs=1, nargs=1, static=True)
    assert rt.call("Lm;", "dbl", "(I)I", (21,)) == 42


def test_facade_hook_delegates_direct():
    """Direkter Fassaden-Hook: _dispatch(CallStaticIntMethod) -> Dalvik-Methode statt Policy."""
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    rt.register_method("Lm;", "dbl", "(I)I", [("add-int", 0, 0, 0), ("return", 0)],
                       nregs=1, nargs=1, static=True)
    mid = jni._new_handle("jmethodID", cls="Lm;", name="dbl", sig="(I)I", static=True)
    c = eng.cpu
    c.x[2] = mid       # methodID
    c.x[3] = 21        # jint arg
    ret = jni._dispatch(c, "CallStaticIntMethod", "env")
    assert ret == 42


def test_no_runtime_keeps_policy():
    """Regression: ohne angehaengte Runtime bleibt der RASP-Policy-Pfad (kein Delegieren)."""
    svc, eng, ffi, jni = _setup()
    mid = jni._new_handle("jmethodID", cls="Lchk;", name="isRooted", sig="()Z",
                          proposed=0, category="root", retchar="Z")
    c = eng.cpu
    c.x[2] = mid
    ret = jni._dispatch(c, "CallBooleanMethod", "env")
    assert ret == 0        # saubere Policy-Antwort (nicht erkannt), kein J2
    assert getattr(jni, "java_runtime", None) is None


def test_end_to_end_native_calls_back_into_java():
    """Zwei-Wege-Loop: J0 ruft native Trampoline -> die ruft ueber JNI CallStaticIntMethod
    nach Java zurueck (J2) -> registrierte Dalvik-Methode dbl(21)=42."""
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    rt.register_method("Lfoo;", "dbl", "(I)I", [("add-int", 0, 0, 0), ("return", 0)],
                       nregs=1, nargs=1, static=True)
    clsh = jni._new_handle("jclass", cls="Lfoo;")
    mid = jni._new_handle("jmethodID", cls="Lfoo;", name="dbl", sig="(I)I", static=True)

    slot = JNI_FUNCS.index("CallStaticIntMethod")
    # Trampoline: Argumente von J0 kommen als x2=arg0(cls), x3=arg1(mid), x4=arg2(jint).
    #   CallStaticIntMethod(env=x0, clazz, methodID, jint) erwartet x1=clazz, x2=mid, x3=jint.
    code = b"".join([
        _w(0xA9BF7BFD),                               # stp x29, x30, [sp, #-16]!
        _w(0xAA0203E1),                               # mov x1, x2   (clazz)
        _w(0xAA0303E2),                               # mov x2, x3   (methodID)
        _w(0xAA0403E3),                               # mov x3, x4   (jint)
        _w(0xF9400008),                               # ldr x8, [x0]        ; ENV_TABLE
        _w(0xF9400000 | (slot << 10) | (8 << 5) | 9), # ldr x9, [x8, #slot*8]
        _w(0xD63F0120),                               # blr x9
        _w(0xA8C17BFD),                               # ldp x29, x30, [sp], #16
        _w(0xD65F03C0),                               # ret
    ])
    svc.load_blob(eng, 0x4000, code)
    br = JavaToNativeBridge(eng, jni)
    # Java->native (J0) mit (clsHandle, midHandle, 21); native->Java (J2) verdoppelt -> 42
    assert br.call_native(0x4000, args=(clsh, mid, 21), ret="int") == 42
    assert br.errors == []
