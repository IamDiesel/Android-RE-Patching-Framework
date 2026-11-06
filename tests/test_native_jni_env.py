# -*- coding: utf-8 -*-
"""Tests fuer die JNI-Fassade (A3.2, native_jni_env_service): Stufen 1-3.
- Stufe 1: Tabellenlayout + JNIEnv-Doppel-Deref (GetVersion), FindClass/GetMethodID/RegisterNatives
  ueber die Fassade; Smali-Resolver gegen das echte App-Smali (adaptiv, skip wenn Baum fehlt).
- Stufe 2: Klassifikator + Trivial-Evaluator auf konstruierten Bodies.
- Stufe 3: Policy (auto-pass / Override / Watchlist-pending).
Headless (capstone/arm64mini)."""
import os
import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_trace_service import NativeTraceService
from services import native_jni_env_service as J
from services.native_jni_env_service import NativeJniEnvService

_SENT = 0xDEAD0000
# Hand-assembliert (little-endian)
LDR_X9_X0      = bytes.fromhex("09004 0f9".replace(" ", ""))   # ldr x9,[x0]
LDR_X8_X9_0x20 = bytes.fromhex("281140f9")                     # ldr x8,[x9,#0x20]
BR_X8          = bytes.fromhex("00011fd6")                     # br x8


def _mk():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi)          # setzt x0 = ENV_OBJ
    return svc, eng, ffi, jni, NativeTraceService(publish=False)


# ---------- Stufe 1: Struktur ----------
def test_tables_wired():
    svc, eng, ffi, jni, tr = _mk()
    m = eng.cpu.m
    assert int.from_bytes(m.rb(J.ENV_OBJ, 8), "little") == J.ENV_TABLE
    stub6 = int.from_bytes(m.rb(J.ENV_TABLE + 6 * 8, 8), "little")
    assert ffi.name_of(eng, stub6) == "JNIEnv::FindClass"
    assert int.from_bytes(m.rb(J.VM_OBJ, 8), "little") == J.VM_TABLE
    assert eng.cpu.x[0] == J.ENV_OBJ


def test_double_deref_getversion():
    """Beweist das Tabellenlayout end-to-end: ldr x9,[x0]; ldr x8,[x9,#0x20]; br x8 -> GetVersion."""
    svc, eng, ffi, jni, tr = _mk()
    svc.load_blob(eng, 0x1000, LDR_X9_X0 + LDR_X8_X9_0x20 + BR_X8)
    eng.cpu.pc = 0x1000
    eng.cpu.lr = _SENT
    r = tr.run(eng, maxins=50, mode="block")
    assert r["ok"] and r.get("done"), r
    assert eng.cpu.x[0] == 0x00010006, hex(eng.cpu.x[0])


def _fire(eng, ffi, tr, fnname):
    eng.cpu.pc = ffi.stub_of(eng, "JNIEnv::" + fnname)
    eng.cpu.lr = _SENT
    return tr.step_block(eng)


def test_findclass_and_methodid_and_register():
    svc, eng, ffi, jni, tr = _mk()
    # FindClass
    eng.cpu.m.wb(0x70000, b"com/foo/Bar\x00")
    eng.cpu.x[1] = 0x70000
    _fire(eng, ffi, tr, "FindClass")
    clsh = eng.cpu.x[0]
    assert clsh != 0
    assert jni.get_calls()[-1]["class"] == "com/foo/Bar"
    # GetMethodID
    eng.cpu.m.wb(0x70100, b"doThing\x00")
    eng.cpu.m.wb(0x70200, b"()Z\x00")
    eng.cpu.x[1] = clsh; eng.cpu.x[2] = 0x70100; eng.cpu.x[3] = 0x70200
    _fire(eng, ffi, tr, "GetMethodID")
    mid = eng.cpu.x[0]
    assert mid != 0 and jni.st.handles[mid]["name"] == "doThing"
    # RegisterNatives
    eng.cpu.m.wb(0x71000, b"nativeCheck\x00")
    eng.cpu.m.wb(0x71100, b"()Z\x00")
    arr = 0x72000
    eng.cpu.m.wb(arr, (0x71000).to_bytes(8, "little") + (0x71100).to_bytes(8, "little")
                 + (0x1234).to_bytes(8, "little"))
    eng.cpu.x[1] = clsh; eng.cpu.x[2] = arr; eng.cpu.x[3] = 1
    _fire(eng, ffi, tr, "RegisterNatives")
    assert eng.cpu.x[0] == 0
    nat = jni.get_natives()
    assert len(nat) == 1 and nat[0]["name"] == "nativeCheck" and nat[0]["fnptr"] == "0x1234"


# ---------- Stufe 1: Resolver gegen echtes Smali (adaptiv) ----------
def _find_sample_class(jni):
    for root in jni.smali_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".smali"):
                    p = os.path.join(dirpath, f)
                    rel = os.path.relpath(p, root)[:-6].replace(os.sep, "/")
                    return rel, p
    return None, None


def test_resolver_real_smali():
    svc, eng, ffi, jni, tr = _mk()
    cls, path = _find_sample_class(jni)
    if not cls:
        pytest.skip("kein Smali-Baum vorhanden")
    assert jni._class_file(cls) == path
    assert jni._class_file("does/not/Exist___") is None
    # Erste echte Methode der Klasse finden und aufloesen
    import re
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^\.method\b.*?\s(\w+)(\([^\n]*\)\S+)\s*$", txt, re.MULTILINE)
    if m:
        body = jni._method_body(path, m.group(1), m.group(2))
        assert body is not None


# ---------- Stufe 2: Klassifikator + Trivial-Evaluator ----------
def test_classifier():
    svc, eng, ffi, jni, tr = _mk()
    assert jni._classify('    const-string v0, "/system/xbin/su"\n') == "root"
    assert jni._classify('    invoke-virtual {v0}, Landroid/os/Debug;->isDebuggerConnected()Z\n') == "debug"
    assert jni._classify('    const-string v0, "frida-server"\n') == "frida"
    assert jni._classify('    const-string v0, "GET_SIGNATURES"\n') == "signature"
    assert jni._classify('    nop\n    return-void\n') is None


def test_trivial_eval():
    svc, eng, ffi, jni, tr = _mk()
    assert jni._trivial_eval("    const/4 v0, 0x0\n    return v0\n", "Z") == 0
    assert jni._trivial_eval("    const/16 v0, 0x2a\n    return v0\n", "I") == 0x2a
    assert jni._trivial_eval("    return-void\n", "V") == 0
    # nicht trivial (Methodenaufruf vor return)
    assert jni._trivial_eval("    invoke-static {}, Lx;->y()I\n    move-result v0\n    return v0\n", "I") is None


# ---------- Stufe 3: Policy ----------
def test_policy_autopass_override_watchlist():
    svc, eng, ffi, jni, tr = _mk()
    # methodID-Handle direkt anlegen (Kategorie root -> Watchlist)
    mid = jni._new_handle("jmethodID", cls="com/x/RootCheck", name="isRooted", sig="()Z",
                          category="root", proposed=0, retchar="Z", resolved=True)
    eng.cpu.x[1] = jni._new_handle("jobject")  # obj
    eng.cpu.x[2] = mid
    _fire(eng, ffi, tr, "CallBooleanMethod")
    c = jni.get_calls()[-1]
    assert eng.cpu.x[0] == 0 and c["final"] == 0 and c["pending"] is True and c["overridden"] is False
    # Override auf 1 (manipulieren) -> Call liefert 1
    jni.set_override("com/x/RootCheck", "isRooted", "()Z", 1)
    eng.cpu.x[1] = jni._new_handle("jobject"); eng.cpu.x[2] = mid
    _fire(eng, ffi, tr, "CallBooleanMethod")
    c = jni.get_calls()[-1]
    assert eng.cpu.x[0] == 1 and c["final"] == 1 and c["overridden"] is True


def test_getversion_and_getjavavm_direct():
    svc, eng, ffi, jni, tr = _mk()
    _fire(eng, ffi, tr, "GetVersion")
    assert eng.cpu.x[0] == 0x00010006
    eng.cpu.x[1] = 0x73000
    _fire(eng, ffi, tr, "GetJavaVM")
    assert int.from_bytes(eng.cpu.m.rb(0x73000, 8), "little") == J.VM_OBJ
