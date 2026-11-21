# -*- coding: utf-8 -*-
"""Tests fuer die Byte-Array-JNI-Fassade (MS-2 / 2c-Prereq): NewByteArray + Regions + Elements."""
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService


def _setup():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return svc, eng, ffi, jni


def test_newbytearray_and_length():
    svc, eng, ffi, jni = _setup()
    c = eng.cpu
    c.x[1] = 8
    arr = jni._dispatch(c, "NewByteArray", "env")
    c.x[1] = arr
    assert jni._dispatch(c, "GetArrayLength", "env") == 8


def test_set_get_region_roundtrip():
    svc, eng, ffi, jni = _setup()
    c = eng.cpu
    c.x[1] = 4
    arr = jni._dispatch(c, "NewByteArray", "env")
    src = jni._alloc(c, bytes([1, 2, 3, 4]))
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 4, src
    jni._dispatch(c, "SetByteArrayRegion", "env")
    dst = jni._alloc(c, b"\x00\x00\x00\x00")
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 4, dst
    jni._dispatch(c, "GetByteArrayRegion", "env")
    assert bytes(c.m.rb(dst, 4)) == bytes([1, 2, 3, 4])


def test_elements_copyback():
    svc, eng, ffi, jni = _setup()
    c = eng.cpu
    c.x[1] = 4
    arr = jni._dispatch(c, "NewByteArray", "env")
    src = jni._alloc(c, bytes([1, 2, 3, 4]))
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 4, src
    jni._dispatch(c, "SetByteArrayRegion", "env")
    # Elements holen, nativ aendern, mit mode=0 zurueckschreiben
    c.x[1], c.x[2] = arr, 0
    ptr = jni._dispatch(c, "GetByteArrayElements", "env")
    assert bytes(c.m.rb(ptr, 4)) == bytes([1, 2, 3, 4])
    c.m.wb(ptr, bytes([9, 9, 9, 9]))
    c.x[1], c.x[2], c.x[3] = arr, ptr, 0           # mode=0 (copy back)
    jni._dispatch(c, "ReleaseByteArrayElements", "env")
    dst = jni._alloc(c, b"\x00\x00\x00\x00")
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 4, dst
    jni._dispatch(c, "GetByteArrayRegion", "env")
    assert bytes(c.m.rb(dst, 4)) == bytes([9, 9, 9, 9])


def test_abort_mode_no_copyback():
    svc, eng, ffi, jni = _setup()
    c = eng.cpu
    c.x[1] = 2
    arr = jni._dispatch(c, "NewByteArray", "env")
    src = jni._alloc(c, bytes([5, 6]))
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 2, src
    jni._dispatch(c, "SetByteArrayRegion", "env")
    c.x[1], c.x[2] = arr, 0
    ptr = jni._dispatch(c, "GetByteArrayElements", "env")
    c.m.wb(ptr, bytes([0, 0]))
    c.x[1], c.x[2], c.x[3] = arr, ptr, 2           # 2 = JNI_ABORT -> kein Rueckschreiben
    jni._dispatch(c, "ReleaseByteArrayElements", "env")
    dst = jni._alloc(c, b"\x00\x00")
    c.x[1], c.x[2], c.x[3], c.x[4] = arr, 0, 2, dst
    jni._dispatch(c, "GetByteArrayRegion", "env")
    assert bytes(c.m.rb(dst, 2)) == bytes([5, 6])
