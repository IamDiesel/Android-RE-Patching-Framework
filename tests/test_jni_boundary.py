# -*- coding: utf-8 -*-
"""Thin Slice: Java<->nativ-Schnittstelle beobachtbar + manipulierbar, in EINEM Lauf.

Echter arm64-Aufruf auf der Engine (add x0,x2,#1 ; ret). Beobachtung (Log/Event/Marker) und
Manipulation (Eingangs-Args + Rueckgabe, bedingt/funktional/konstant) ueber jni_boundary."""
import types
from core.java.dalvik import DalvikVM
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.bridge import JavaToNativeBridge
from core.java.jni_boundary import install_jni_boundary
from core.application.event_bus import EventBus

# native: add x0, x2, #1 ; ret   -> gibt (erstes Java-Arg) + 1 zurueck
_BLOB = "40040091c0035fd6"
_PROG = [("const", 0, 41),
         ("invoke-native", 1, 0x1000, "Lcom/example/Foo;", 0),
         ("return", 1)]


def _slice():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, 0x1000, bytes.fromhex(_BLOB))
    br = JavaToNativeBridge(eng, jni)
    vm = DalvikVM(bridge=br)
    return vm, br


def test_boundary_observed_without_manipulation():
    vm, br = _slice()
    hook = install_jni_boundary(br, vm=vm)
    assert vm.run_method(_PROG, nregs=2) == 42          # 41 -> native -> 42, unveraendert
    assert len(hook.log) == 1
    e = hook.log.entries[0]
    assert e["fnptr"] == 0x1000 and e["cls"] == "Lcom/example/Foo;"
    assert e["args_in"] == [41] and e["args_eff"] == [41]
    assert e["ret_in"] == 42 and e["ret_eff"] == 42


def test_manipulate_input_const():
    vm, br = _slice()
    hook = install_jni_boundary(br, vm=vm)
    hook.add_action_point(fnptr=0x1000, arg_assigns=[{"index": 0, "value": 100, "const": True}])
    assert vm.run_method(_PROG, nregs=2) == 101          # native(100)+1
    e = hook.log.entries[0]
    assert e["args_in"] == [41] and e["args_eff"] == [100]


def test_manipulate_input_functional():
    vm, br = _slice()
    hook = install_jni_boundary(br, vm=vm)
    hook.add_action_point(fnptr=0x1000, arg_assigns=[{"index": 0, "expr": "a0 + 9"}])
    assert vm.run_method(_PROG, nregs=2) == 51            # (41+9) -> native -> 51


def test_manipulate_return():
    vm, br = _slice()
    hook = install_jni_boundary(br, vm=vm)
    hook.add_action_point(fnptr=0x1000, ret_expr="ret & 0xff | 0")  # hier: Rueckgabe auf 0 zwingen
    hook.add_action_point(fnptr=0x1000, ret_expr="0")
    assert vm.run_method(_PROG, nregs=2) == 0
    e = hook.log.entries[0]
    assert e["ret_in"] == 42 and e["ret_eff"] == 0


def test_conditional_fires_and_skips():
    vm, br = _slice()
    hook = install_jni_boundary(br, vm=vm)
    hook.add_action_point(fnptr=0x1000, cond="a0 == 41",
                          arg_assigns=[{"index": 0, "value": 7, "const": True}])
    assert vm.run_method(_PROG, nregs=2) == 8             # cond trifft -> native(7)+1
    vm2, br2 = _slice()
    hook2 = install_jni_boundary(br2, vm=vm2)
    hook2.add_action_point(fnptr=0x1000, cond="a0 == 999",
                           arg_assigns=[{"index": 0, "value": 7, "const": True}])
    assert vm2.run_method(_PROG, nregs=2) == 42           # cond trifft nicht -> unveraendert


def test_marker_recorded_for_boundary():
    vm, br = _slice()
    vm._dbg = types.SimpleNamespace(markers=[], timeline=[0, 1, 2],
                                    on_step=lambda *a, **k: None)
    install_jni_boundary(br, vm=vm)
    vm.run_method(_PROG, nregs=2)
    jm = [m for m in vm._dbg.markers if m["kind"] == "jni"]
    assert len(jm) == 1 and jm[0]["step"] == 3 and "fn@0x1000" in jm[0]["label"]


def test_event_published():
    vm, br = _slice()
    install_jni_boundary(br, vm=vm)
    got = []
    cb = lambda d: got.append(d)
    EventBus.subscribe("JNI_CALL", cb)
    try:
        vm.run_method(_PROG, nregs=2)
    finally:
        EventBus.unsubscribe("JNI_CALL", cb)
    assert got and got[0]["fnptr"] == 0x1000 and got[0]["ret_eff"] == 42
