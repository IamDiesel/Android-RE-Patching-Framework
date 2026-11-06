# -*- coding: utf-8 -*-
"""Integrationstests der JNI-Fassade gegen das echte libdb0c (A3.2 Durchbruch):
- schnell: JavaVM::GetEnv liefert den JNIEnv zurueck (Semantik x0=JavaVM).
- langsam (RUN_SLOW=1): JNI_OnLoad mit x0=JavaVM erreicht GetEnv + FindClass(echte Namen) +
  >=40 RegisterNatives (Regressionsschutz fuer den x0=JavaVM-Fix)."""
import os
import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_trace_service import NativeTraceService
from services import native_jni_env_service as J
from services.native_jni_env_service import NativeJniEnvService

_SENT = 0xDEAD0000


def _lib():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "tools", "lib", "libdb0c.so")
    return p if os.path.isfile(p) else None


def test_vm_getenv_returns_env():
    """JavaVM::GetEnv(vm, &env, ver) schreibt ENV_OBJ nach *env und gibt 0 (JNI_OK)."""
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi)
    eng.cpu.m.wb(0x73000, b"\x00" * 8)
    eng.cpu.x[1] = 0x73000
    eng.cpu.pc = ffi.stub_of(eng, "JavaVM::GetEnv"); eng.cpu.lr = _SENT
    NativeTraceService(publish=False).step_block(eng)
    assert int.from_bytes(eng.cpu.m.rb(0x73000, 8), "little") == J.ENV_OBJ
    assert eng.cpu.x[0] == 0


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="slow integration (RUN_SLOW=1)")
def test_jni_onload_javavm_reaches_registration():
    from core.native.loader import ElfImage
    p = _lib()
    if not p:
        pytest.skip("libdb0c.so fehlt")
    img = ElfImage.from_file(p)
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False); ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False); jni.install(eng, ffi, set_entry_reg=None)
    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    tr.begin(eng, entry, args=[J.VM_OBJ], faithful=True)     # x0 = JavaVM* (der Fix)
    tr.run(eng, breakpoints=[], maxins=1_200_000, mode="block")
    s = jni.summary(); nat = jni.get_natives()
    assert s["by_fn"].get("GetEnv", 0) >= 1, s
    assert s["by_fn"].get("RegisterNatives", 0) >= 40, s
    assert len(nat) >= 40
    assert any(n["class"] and "/" in n["class"] for n in nat)   # echte Klassennamen
