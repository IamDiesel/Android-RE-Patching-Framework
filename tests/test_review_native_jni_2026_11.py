# -*- coding: utf-8 -*-
"""Review-Regressionstests (2026-11): N1, N2, E1, E2, E3, E4, E6 + Trace-Fixes."""
from __future__ import annotations

import struct
import types
import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService, JNI_FUNCS, ENV_OBJ
from services.native_trace_service import NativeTraceService
from core.java.bridge import JavaToNativeBridge
from core.java.dalvik import DalvikVM, DalvikException
from core.java.debug_unified import DebugSession
from core.java.objects import JString, JObject, JArray
from core.java.runtime import JavaRuntime, VmBackedJavaRuntime, sig_param_types, sig_argcount

_M64 = (1 << 64) - 1


def _w(insn: int) -> bytes:
    return struct.pack("<I", insn & 0xFFFFFFFF)


def _setup():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return svc, eng, ffi, jni


# ============================================================================
# N1: JavaToNativeBridge Fehlerbehandlung (lenient / strict / markers)
# ============================================================================
def test_n1_bridge_lenient_does_not_leak_env_ptr_and_records():
    svc, eng, ffi, jni = _setup()
    # Endlosschleife: b #0 -> trifft maxins
    svc.load_blob(eng, 0x1000, _w(0x14000000))

    vm = DalvikVM()
    vm.lenient = True
    dev_calls = []
    vm.deviations = types.SimpleNamespace(record=lambda kind, **kw: dev_calls.append((kind, kw)))
    vm._dbg = types.SimpleNamespace(markers=[], timeline=[0, 1])

    br = JavaToNativeBridge(eng, jni, emu=svc, vm=vm)
    r_int = br.call_native(0x1000, "Lapp/N;", args=(), ret="int", maxins=5,
                           method_name="boomInt", method_sig="()I")
    r_str = br.call_native(0x1000, "Lapp/N;", args=(), ret="string", maxins=5,
                           method_name="boomStr", method_sig="()Ljava/lang/String;")
    r_void = br.call_native(0x1000, "Lapp/N;", args=(), ret="void", maxins=5,
                            method_name="boomVoid", method_sig="()V")
    r_jref = br.call_native(0x1000, "Lapp/N;", args=(), ret="jref", maxins=5,
                            method_name="boomObj", method_sig="()Ljava/lang/Object;")

    assert r_int == 0 and r_int != ENV_OBJ
    assert r_str is None
    assert r_void is None
    assert r_jref == 0
    assert len(br.errors) == 4 and br.errors[0]["why"] == "maxins"
    assert len(vm.lenient_errors) == 4
    assert len(dev_calls) == 4 and dev_calls[0][0] == "native_fail"
    assert len(vm._dbg.markers) == 4 and vm._dbg.markers[0]["kind"] == "native_fail"


def test_n1_bridge_strict_raises_runtime_or_dalvik_exception():
    svc, eng, ffi, jni = _setup()
    svc.load_blob(eng, 0x1000, _w(0x14000000))  # b #0

    # Ohne VM + strict=True -> RuntimeError
    br_no_vm = JavaToNativeBridge(eng, jni, emu=svc)
    br_no_vm.strict = True
    with pytest.raises(RuntimeError):
        br_no_vm.call_native(0x1000, maxins=3)

    # Mit VM (lenient=False, aufgelöst über jni.java_runtime.vm) -> DalvikException
    rt = JavaRuntime(eng, jni)
    rt.vm.lenient = False
    br_via_jni = JavaToNativeBridge(eng, jni, emu=svc)
    with pytest.raises(DalvikException):
        br_via_jni.call_native(0x1000, maxins=3)


# ============================================================================
# N2 + Trace-Fixes: step_insn(n), DebugSession, dispatch_trace, run stopped
# ============================================================================
def test_n2_step_insn_count_and_debugsession_null_guard():
    svc, eng, ffi, jni = _setup()
    # 0x1000: movz x0, #10 ; add x0, x0, #5 ; ret
    code = _w(0xD2800140) + _w(0x91001400) + _w(0xD65F03C0)
    svc.load_blob(eng, 0x1000, code)

    tr = NativeTraceService(publish=False)
    eng.cpu.stopped = True
    eng.cpu.stop_reason = "stale"
    tr.begin(eng, 0x1000)
    assert eng.cpu.stopped is False and eng.cpu.stop_reason is None

    r = tr.step_insn(eng, 2)
    assert r["ok"] and not r["done"]
    assert eng.cpu.x[0] == 15

    # DebugSession.native_step_insn(2) und native_state() mit eng=None
    sess = DebugSession(eng, jni)
    tr.begin(eng, 0x1000)
    r2 = sess.native_step_insn(2)
    assert r2["ok"] and eng.cpu.x[0] == 15

    sess_none = DebugSession(None, jni)
    ns = sess_none.native_state()
    assert isinstance(ns, dict) and ns["available"] is False


def test_trace_string_breakpoints_stopped_and_dispatch_trace_fixes():
    svc, eng, ffi, jni = _setup()
    # 0x1000: movz x0, #1 ; movz x0, #2 ; ret
    svc.load_blob(eng, 0x1000, _w(0xD2800020) + _w(0xD2800040) + _w(0xD65F03C0))

    tr = NativeTraceService(publish=False)
    tr.begin(eng, 0x1000)
    r_bp = tr.run(eng, breakpoints=["0x1008"], mode="insn")
    assert r_bp["ok"] and r_bp.get("paused") and r_bp["bp"] == 0x1008

    # Stub setzt cpu.stopped = True -> run liefert stopped=True
    def _stop_stub(cpu):
        cpu.stopped = True
        cpu.stop_reason = "probe_stop"
        return 0x100C

    eng.cpu.stub[0x1008] = _stop_stub
    r_st = tr.run(eng, mode="insn")
    assert r_st["ok"] and r_st.get("stopped") is True and r_st["stop_reason"] == "probe_stop"

    # dispatch_trace_enable Idempotenz + custom row_stride + cross-page r64 kein Doppel-Log
    vt = 0x137000
    eng.cpu.m.w64(vt + 0x100 + 16, 0xCAFEBABE)
    # Seitenuebergreifende 8-Byte-Adresse bei 0x137ffc (4B Seite 0x137000 + 4B Seite 0x138000)
    eng.cpu.m.w64(0x137FFC, 0x1122334455667788)

    tr.dispatch_trace_enable(eng, vt_base=vt, lo=0x136000, hi=0x139000, row_stride=0x100)
    tr.dispatch_trace_enable(eng, vt_base=vt, lo=0x136000, hi=0x139000, row_stride=0x100)  # doppelt!
    v1 = eng.cpu.m.r64(vt + 0x100 + 16)
    v2 = eng.cpu.m.r64(0x137FFC)
    log = tr.dispatch_trace_get(eng)
    assert v1 == 0xCAFEBABE and v2 == 0x1122334455667788
    assert len(log) == 2, f"Erwartet genau 2 Eintraege (kein Doppel-Log), got {len(log)}"
    assert log[0]["idx1"] == 1 and log[0]["col"] == 2
    tr.dispatch_trace_disable(eng)
    eng.cpu.m.r64(vt + 0x100 + 16)
    assert len(tr.dispatch_trace_get(eng)) == 2


# ============================================================================
# E1: Re-entrancy (native -> JNI -> Java -> native), saved_lr, FFI exc, stopped
# ============================================================================
def test_e1_reentrant_native_java_native_and_stack_isolation():
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)

    # Innere native Funktion @0x4100: (env=x0, this=x1, arg=x2) -> arg*2 + 5
    inner_code = b"".join([
        _w(0xA9BE7BFD),  # stp x29, x30, [sp, #-32]!
        _w(0xD28ACF09),  # movz x9, #0x5678
        _w(0xF9000BE9),  # str x9, [sp, #16]
        _w(0x8B020040),  # add x0, x2, x2
        _w(0x91001400),  # add x0, x0, #5
        _w(0xA8C27BFD),  # ldp x29, x30, [sp], #32
        _w(0xD65F03C0),  # ret
    ])
    svc.load_blob(eng, 0x4100, inner_code)

    # Java-Methode ruft innere native Funktion ueber invoke-native auf (param p0 = v1 bei nregs=2,nargs=1)
    rt.register_method(
        "Lre/Bridge;", "bounce", "(I)I",
        [("invoke-native", 0, 0x4100, "Lre/Bridge;", 1), ("return", 0)],
        nregs=2, nargs=1, static=True,
    )
    clsh = jni._new_handle("jclass", cls="Lre/Bridge;")
    mid = jni._new_handle("jmethodID", cls="Lre/Bridge;", name="bounce", sig="(I)I", static=True)
    slot = JNI_FUNCS.index("CallStaticIntMethod")

    # Aeussere native Funktion @0x4000:
    # legt Sentinel 0x1234 auf Stack, ruft JNI CallStaticIntMethod(clsh, mid, 10),
    # prueft dass Stack-Sentinel intakt ist und addiert 100 -> (10*2 + 5) + 100 = 125.
    outer_code = b"".join([
        _w(0xA9BF7BFD),                                # stp x29, x30, [sp, #-16]!
        _w(0xA9BF53F3),                                # stp x19, x20, [sp, #-16]!
        _w(0xD2824693),                                # movz x19, #0x1234
        _w(0xF90007F3),                                # str x19, [sp, #8]
        _w(0xAA0203E1),                                # mov x1, x2 (clazz)
        _w(0xAA0303E2),                                # mov x2, x3 (methodID)
        _w(0xAA0403E3),                                # mov x3, x4 (arg=10)
        _w(0xF9400008),                                # ldr x8, [x0]
        _w(0xF9400000 | (slot << 10) | (8 << 5) | 9),  # ldr x9, [x8, #slot*8]
        _w(0xD63F0120),                                # blr x9
        _w(0xF94007F4),                                # ldr x20, [sp, #8]
        _w(0xCB130294),                                # sub x20, x20, x19
        _w(0x8B140000),                                # add x0, x0, x20
        _w(0x91019000),                                # add x0, x0, #100
        _w(0xA8C153F3),                                # ldp x19, x20, [sp], #16
        _w(0xA8C17BFD),                                # ldp x29, x30, [sp], #16
        _w(0xD65F03C0),                                # ret
    ])
    svc.load_blob(eng, 0x4000, outer_code)

    assert eng.cpu.sp == 0
    br = JavaToNativeBridge(eng, jni, emu=svc, vm=rt.vm)
    got = br.call_native(0x4000, args=(clsh, mid, 10), ret="int")
    assert got == 125
    assert br.errors == []
    assert rt.bridge.errors == []
    assert eng.cpu.sp == 0x7F000000
    assert getattr(eng, "_run_depth", 0) == 0


def test_e1_ffi_exception_and_saved_lr_and_stop_reasons():
    svc, eng, ffi, jni = _setup()
    stub_addr = ffi.install_named_stub(eng, "boom_fn")

    def _bad(cpu, _ffi):
        cpu.lr = 0xDEAD0000  # Versuch, LR zu korrumpieren
        raise RuntimeError("boom_handler")

    ffi.set_handler(eng, "boom_fn", _bad)
    # 0x4200: stp x29, x30, [sp, #-16]! ; bl stub ; movz x0, #77 ; ldp x29, x30, [sp], #16 ; ret
    # Wait: blr x9 mit x9=stub_addr ueber Argument x0=stub_addr
    code = b"".join([
        _w(0xA9BF7BFD),  # stp x29, x30, [sp, #-16]!
        _w(0xD63F0000),  # blr x0
        _w(0xD28009A0),  # movz x0, #77
        _w(0xA8C17BFD),  # ldp x29, x30, [sp], #16
        _w(0xD65F03C0),  # ret
    ])
    svc.load_blob(eng, 0x4200, code)
    r = svc.run(eng, 0x4200, (stub_addr,))
    assert r["ok"] and r["ret"] == 77
    assert any(d["kind"] == "ffi_exc" and d["site"] == "boom_fn" for d in eng._deviations)

    # user_stop vs exit vs futex_stall
    def _mk_stop(reason):
        def _s(cpu):
            cpu.stopped = True
            cpu.stop_reason = reason
            cpu.x[0] = 9
            return None
        return _s

    eng.cpu.stub[0x4300] = _mk_stop("user_stop")
    r_u = svc.run(eng, 0x4300, ())
    assert r_u["ok"] is False and r_u["why"] == "stopped"

    eng.cpu.stub[0x4304] = _mk_stop("exit")
    r_e = svc.run(eng, 0x4304, ())
    assert r_e["ok"] is True and r_e.get("stopped") is True and r_e.get("stop_reason") == "exit"

    eng.cpu.stub[0x4308] = _mk_stop("futex_stall@0x4308")
    r_f = svc.run(eng, 0x4308, ())
    assert r_f["ok"] is True and eng.cpu.stop_reason == "futex_stall@0x4308"


# ============================================================================
# E2: Deskriptoren, Slash-Norm, this, Nonvirtual, MethodA/V, Float/Double, Stack>5
# ============================================================================
def test_e2_sig_param_types_and_class_normalization():
    assert sig_param_types("()V") == []
    assert sig_param_types("(ZBCISJFDLjava/lang/String;[B[[I[Ljava/lang/Object;)V") == [
        "Z", "B", "C", "I", "S", "J", "F", "D",
        "Ljava/lang/String;", "[B", "[[I", "[Ljava/lang/Object;"
    ]
    assert sig_argcount("([[Ljava/lang/String;[B)I") == 2

    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    rt.register_method("Lcom/foo/Calc;", "add", "(II)I",
                       [("add-int", 0, 0, 1), ("return", 0)],
                       nregs=2, nargs=2, static=True)
    assert rt.has_method("com/foo/Calc", "add", "(II)I")
    assert rt.call_from_native("com/foo/Calc", "add", "(II)I", [19, 23], True) == 42

    vm2 = DalvikVM()
    vm2.register_method("Lcom/foo/Calc;", "add", "(II)I",
                        [("add-int", 0, 0, 1), ("return", 0)],
                        nregs=2, nargs=2, static=True)
    vrt = VmBackedJavaRuntime(vm2, jni)
    assert vrt.has_method("com/foo/Calc", "add", "(II)I")
    assert vrt.call_from_native("com/foo/Calc", "add", "(II)I", [10, 32], True) == 42


def test_e2_instance_this_and_nonvirtual_and_handle_unmarshalling():
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    # Instanzmethode Base.calc(int delta): return this.base + delta
    rt.register_method(
        "Lpkg/Base;", "calc", "(I)I",
        [("iget", 0, 1, "Lpkg/Base;", "base"), ("add-int", 0, 0, 2), ("return", 0)],
        nregs=3, nargs=2, static=False,
    )
    # Sub.calc ueberschreibt: return this.base + delta + 1000
    rt.register_method(
        "Lpkg/Sub;", "calc", "(I)I",
        [("iget", 0, 1, "Lpkg/Base;", "base"),
         ("add-int", 0, 0, 2),
         ("add-int/lit", 0, 0, 1000),
         ("return", 0)],
        nregs=3, nargs=2, static=False,
    )

    sub_obj = JObject("Lpkg/Sub;", {"base": 50})
    obj_h = rt._to_jni(sub_obj)
    base_cls_h = jni._new_handle("jclass", cls="Lpkg/Base;")
    jni.st.classes[base_cls_h] = "Lpkg/Base;"
    mid_base = jni._new_handle("jmethodID", cls="Lpkg/Base;", name="calc", sig="(I)I", static=False)

    # Virtual CallIntMethod auf sub_obj -> trifft Lpkg/Sub; => 50 + 7 + 1000 = 1057
    c = eng.cpu
    c.x[1] = obj_h
    c.x[2] = mid_base
    c.x[3] = 7
    assert jni._dispatch(c, "CallIntMethod", "env") == 1057

    # Nonvirtual CallNonvirtualIntMethod(..., sub_obj, Base.class, mid_base, 7) -> trifft Lpkg/Base; => 57
    c.x[1] = obj_h
    c.x[2] = base_cls_h
    c.x[3] = mid_base
    c.x[4] = 7
    assert jni._dispatch(c, "CallNonvirtualIntMethod", "env") == 57

    # Handle-Unmarshalling fuer jstring / jbyteArray / null(0) + Sign-Extension B/S/I + Mask Z/C
    s_h = jni._new_handle("jstring", text="abc")
    ba_h = jni._new_handle("jbyteArray", data=bytearray(b"XY"))
    rt.register_method(
        "Lpkg/U;", "check", "(Ljava/lang/String;[BLjava/lang/Object;BSIZC)I",
        [
            ("if-nez", 2, 2),  # v2=arg0(s) != null
            ("const", 0, -1),
            ("array-length", 0, 3),  # len([B)=2 (v3)
            ("if-eqz", 4, 5),        # v4=null -> jump +1 (nach pc4)
            ("const", 0, -2),
            # v5=B(-5), v6=S(-10), v7=I(-20), v8=Z(1), v9=C(0x1234)
            ("add-int", 0, 0, 5),    # 2 + (-5) = -3
            ("add-int", 0, 0, 6),    # -13
            ("add-int", 0, 0, 7),    # -33
            ("add-int", 0, 0, 8),    # -32
            ("add-int", 0, 0, 9),    # -32 + 0x1234 = 4628
            ("return", 0),
        ],
        nregs=10, nargs=8, static=True,
    )
    res_u = rt.call_from_native(
        "Lpkg/U;", "check", "(Ljava/lang/String;[BLjava/lang/Object;BSIZC)I",
        [s_h, ba_h, 0, 0xFB, 0xFFF6, 0xFFFFFFEC, 0x201, 0x10001234],
        static=True,
    )
    assert res_u == 4628

    # JArray Return-Metadaten (GetArrayLength / GetByteArrayElements)
    ret_ba_h = rt._to_jni(JArray("B", [1, 2, 3, 4]))
    c.x[1] = ret_ba_h
    assert jni._dispatch(c, "GetArrayLength", "env") == 4
    c.x[1] = ret_ba_h
    c.x[2] = 0
    ptr = jni._dispatch(c, "GetByteArrayElements", "env")
    assert c.m.rb(ptr, 4) == b"\x01\x02\x03\x04"


def test_e2_method_a_method_v_float_double_and_stack_args():
    svc, eng, ffi, jni = _setup()
    rt = JavaRuntime(eng, jni)
    c = eng.cpu
    c.sp = 0x7F000000

    # 1) MethodA: mixD(int i, float f, double d) -> i + f + d
    # nregs=6, nargs=4 -> v2=I, v3=F, v4..v5=D
    rt.register_method(
        "Lpkg/M;", "mixD", "(IFD)D",
        [
            ("int-to-double", 0, 2),
            ("float-to-double", 2, 3),
            ("add-double", 0, 0, 2),
            ("add-double", 0, 0, 4),
            ("return-wide", 0),
        ],
        nregs=6, nargs=4, static=True,
    )
    mid_mix = jni._new_handle("jmethodID", cls="Lpkg/M;", name="mixD", sig="(IFD)D", static=True)
    jval_ptr = 0x610000
    c.m.w64(jval_ptr + 0, 10)
    c.m.w64(jval_ptr + 8, struct.unpack("<I", struct.pack("<f", 2.5))[0])
    c.m.w64(jval_ptr + 16, struct.unpack("<Q", struct.pack("<d", 4.25))[0])
    c.x[1] = 0
    c.x[2] = mid_mix
    c.x[3] = jval_ptr
    bits_a = jni._dispatch(c, "CallStaticDoubleMethodA", "env")
    val_a = struct.unpack("<d", struct.pack("<Q", bits_a))[0]
    assert val_a == pytest.approx(16.75)
    assert struct.unpack("<d", c.vget(0, 8))[0] == pytest.approx(16.75)

    # 2) MethodV: AAPCS64 va_list mit GP + FP Save-Area + Stack-Overflow
    rt.register_method(
        "Lpkg/M;", "vaSum", "(IDIF)D",
        [
            ("int-to-double", 0, 2),      # v0 = (double)p0
            ("add-double", 0, 0, 3),      # + p1 (d)
            ("int-to-double", 4, 5),      # v4 = (double)p2 (slot 5 wegen wide p1@3..4)
            ("add-double", 0, 0, 4),
            ("float-to-double", 4, 6),    # p3 (f)
            ("add-double", 0, 0, 4),
            ("return-wide", 0),
        ],
        nregs=7, nargs=5, static=True,
    )
    mid_va = jni._new_handle("jmethodID", cls="Lpkg/M;", name="vaSum", sig="(IDIF)D", static=True)
    va_ptr = 0x611000
    gr_top = 0x612000
    vr_top = 0x613000
    va_stack = 0x614000
    # nur 1 GP im save area (__gr_offs = -8), 1 FP im save area (__vr_offs = -16), Rest auf __stack!
    c.m.w64(gr_top - 8, 100)                                                     # p0 (GP)
    c.m.wb(vr_top - 16, struct.pack("<d", 1.5))                                  # p1 (D)
    c.m.w64(va_stack + 0, 20)                                                    # p2 (GP overflow)
    c.m.w64(va_stack + 8, struct.unpack("<I", struct.pack("<f", 3.25))[0])       # p3 (FP overflow)
    c.m.w64(va_ptr + 0, va_stack)
    c.m.w64(va_ptr + 8, gr_top)
    c.m.w64(va_ptr + 16, vr_top)
    c.m.w32(va_ptr + 24, (-8) & 0xFFFFFFFF)
    c.m.w32(va_ptr + 28, (-16) & 0xFFFFFFFF)
    c.x[1] = 0
    c.x[2] = mid_va
    c.x[3] = va_ptr
    bits_v = jni._dispatch(c, "CallStaticDoubleMethodV", "env")
    assert struct.unpack("<d", struct.pack("<Q", bits_v))[0] == pytest.approx(124.75)
    # va_list Offsets muessen fortgeschrieben sein:
    assert c.m.r64(va_ptr + 0) == va_stack + 16
    assert c.m.r32(va_ptr + 24) == 0
    assert c.m.r32(va_ptr + 28) == 0

    # 3) Direct variadic CallStaticIntMethod mit >5 GP-Args (x3..x7 + 2 Stack-Args)
    rt.register_method(
        "Lpkg/M;", "sum7", "(IIIIIII)I",
        [
            ("add-int", 0, 1, 2),
            ("add-int", 0, 0, 3),
            ("add-int", 0, 0, 4),
            ("add-int", 0, 0, 5),
            ("add-int", 0, 0, 6),
            ("add-int", 0, 0, 7),
            ("return", 0),
        ],
        nregs=8, nargs=7, static=True,
    )
    mid_7 = jni._new_handle("jmethodID", cls="Lpkg/M;", name="sum7", sig="(IIIIIII)I", static=True)
    c.x[1] = 0
    c.x[2] = mid_7
    c.x[3], c.x[4], c.x[5], c.x[6], c.x[7] = 1, 2, 3, 4, 5
    c.m.w64(c.sp + 0, 6)
    c.m.w64(c.sp + 8, 7)
    assert jni._dispatch(c, "CallStaticIntMethod", "env") == 28

    # 4) Direct variadic CallStaticDoubleMethod mit gemischten GP + FP Args (IFD)D
    c.x[1] = 0
    c.x[2] = mid_mix
    c.x[3] = 5
    c.vset(0, struct.pack("<f", 1.5))
    c.vset(1, struct.pack("<d", 2.25))
    bits_d = jni._dispatch(c, "CallStaticDoubleMethod", "env")
    assert struct.unpack("<d", struct.pack("<Q", bits_d))[0] == pytest.approx(8.75)
    assert struct.unpack("<d", c.vget(0, 8))[0] == pytest.approx(8.75)

    # 5) Float-Rueckgabe in s0 (J2 + RASP-Policy)
    rt.register_method(
        "Lpkg/M;", "fval", "(F)F",
        [("add-float", 0, 1, 1), ("return", 0)],
        nregs=2, nargs=1, static=True,
    )
    mid_f = jni._new_handle("jmethodID", cls="Lpkg/M;", name="fval", sig="(F)F", static=True)
    c.vset(0, struct.pack("<f", 3.5))
    c.x[1] = 0; c.x[2] = mid_f
    bits_f = jni._dispatch(c, "CallStaticFloatMethod", "env")
    assert struct.unpack("<f", struct.pack("<I", bits_f))[0] == pytest.approx(7.0)
    assert struct.unpack("<f", c.vget(0, 4))[0] == pytest.approx(7.0)

    # RASP-Policy-Pfad ohne passenden Registry-Treffer -> schreibt s0 bei Float
    mid_pol = jni._new_handle("jmethodID", cls="Lpkg/Unreg;", name="flt", sig="()F", retchar="F")
    jni.set_override("Lpkg/Unreg;", "flt", "()F", struct.unpack("<I", struct.pack("<f", 1.25))[0])
    c.x[2] = mid_pol
    bits_p = jni._dispatch(c, "CallStaticFloatMethod", "env")
    assert struct.unpack("<f", c.vget(0, 4))[0] == pytest.approx(1.25)
    assert bits_p == struct.unpack("<I", struct.pack("<f", 1.25))[0]


# ============================================================================
# E3: inline_loops=True Interruptibility (maxins, max_wall_s, stopped, probes)
# ============================================================================
def test_e3_inline_loops_interruptible_and_dynamic_probes():
    svc, eng, ffi, jni = _setup()
    assert eng.cpu.inline_loops is True

    # Endlosschleife: cmp x0, x0 ; b.eq 0x1000
    loop = _w(0xEB00001F) + _w(0x54FFFFE0)
    svc.load_blob(eng, 0x1000, loop)

    r_max = svc.run(eng, 0x1000, (), maxins=5)
    assert r_max["ok"] is False and r_max["why"] == "maxins"

    r_wall = svc.run(eng, 0x1000, (), maxins=10_000_000, max_wall_s=0.05)
    assert r_wall["ok"] is False and r_wall["why"] == "timeout"

    # Self-Loop mit svc #0 -> cpu.stopped nach 3 Iterationen
    # 0x2000: sub x0, x0, #1 ; svc #0 ; cbnz x0, 0x2000 ; ret
    loop_svc = _w(0xD1000400) + _w(0xD4000001) + _w(0xB5FFFFC0) + _w(0xD65F03C0)
    svc.load_blob(eng, 0x2000, loop_svc)
    hits = [0]

    def _svc_stop(cpu):
        hits[0] += 1
        if hits[0] >= 3:
            cpu.stopped = True
            cpu.stop_reason = "user_stop"

    eng.cpu._svc_hook = _svc_stop
    r_stp = svc.run(eng, 0x2000, (100,), maxins=1000)
    assert r_stp["ok"] is False and r_stp["why"] == "stopped"
    assert hits[0] == 3 and eng.cpu.x[0] == 97

    # Dynamische Probe, die erst von einem Stub im Lauf gesetzt wird
    eng.cpu._svc_hook = None
    fired = []
    # 0x3000: movz x0, #5 ; ret
    svc.load_blob(eng, 0x3000, _w(0xD28000A0) + _w(0xD65F03C0))

    def _stub_installs_probe(cpu):
        cpu.probes[0x3000] = lambda c: fired.append(c.pc)
        return 0x3000

    eng.cpu.stub[0x2F00] = _stub_installs_probe
    r_pr = svc.run(eng, 0x2F00, ())
    assert r_pr["ok"] and r_pr["ret"] == 5
    assert fired == [0x3000]


# ============================================================================
# E4: Self-Modifying Code Cache-Invalidierung ohne manuelles invalidate_code
# ============================================================================
def test_e4_smc_invalidates_caches_automatically():
    svc, eng, ffi, jni = _setup()
    addr = 0x5000
    svc.load_blob(eng, addr, _w(0xD2800020) + _w(0xD65F03C0))  # movz x0, #1 ; ret
    assert svc.run(eng, addr, ())["ret"] == 1

    # Ueberschreiben via mem.write / w32 ohne invalidate_code oder mprotect
    eng.mem.write(addr, _w(0xD28009A0))  # movz x0, #77
    assert svc.run(eng, addr, ())["ret"] == 77

    # Auch via m.w32 + step_block
    eng.cpu.m.w32(addr, 0xD2800040)  # movz x0, #2
    eng.cpu.pc = addr
    eng.cpu.lr = 0xDEAD0000
    eng.cpu.step_block()
    assert eng.cpu.x[0] == 2

    # Auch via m.w64 / m.w8 / m.w + step_insn (tcache + cache Invalidierung)
    tr = NativeTraceService(publish=False)
    tr.begin(eng, addr)
    assert tr.step_insn(eng)["ok"] and eng.cpu.x[0] == 2
    eng.cpu.m.w64(addr, 0xD65F03C0D28000A0)  # movz x0, #5 ; ret
    tr.begin(eng, addr)
    assert tr.step_insn(eng)["ok"] and eng.cpu.x[0] == 5
    eng.cpu.m.w8(addr, 0xC0)  # movz x0, #6 (low byte 0xA0 -> 0xC0 = imm #6)
    tr.begin(eng, addr)
    assert tr.step_insn(eng)["ok"] and eng.cpu.x[0] == 6
    eng.cpu.m.w(addr, 0xD2800100, 4)  # movz x0, #8
    assert svc.run(eng, addr, ())["ret"] == 8


# ============================================================================
# E6: negs INT_MIN V-Flag, eon, orn, bics, mneg (JIT _emit + Interpreter step)
# ============================================================================
def test_e6_negs_eon_orn_bics_mneg_jit_and_step():
    svc, eng, ffi, jni = _setup()
    c = eng.cpu

    # --- Interpreter step() ---
    # negs w0, w1 mit INT32_MIN -> V=1, N=1, Z=0, C=0
    c.x[1] = 0x80000000
    c.step((0x6000, "negs", "w0, w1"))
    assert c.x[0] == 0x80000000 and c.vf == 1 and c.n == 1 and c.z == 0 and c.c == 0

    # negs x0, x1 mit INT64_MIN -> V=1, N=1, Z=0, C=0
    c.x[1] = 1 << 63
    c.step((0x6004, "negs", "x0, x1"))
    assert c.x[0] == (1 << 63) and c.vf == 1 and c.n == 1 and c.z == 0 and c.c == 0

    # negs x0, x1 mit 0 -> Z=1, C=1, V=0, N=0
    c.x[1] = 0
    c.step((0x6008, "negs", "x0, x1"))
    assert c.x[0] == 0 and c.z == 1 and c.c == 1 and c.vf == 0 and c.n == 0

    # eon, orn, bics, mneg in step()
    c.x[1], c.x[2] = 0xF0F0, 0x0FF0
    c.step((0x600C, "eon", "x0, x1, x2"))
    assert c.x[0] == ((0xF0F0 ^ ~0x0FF0) & _M64)

    c.step((0x6010, "orn", "x0, x1, x2"))
    assert c.x[0] == ((0xF0F0 | ~0x0FF0) & _M64)

    c.step((0x6014, "bics", "x0, x1, x2"))
    assert c.x[0] == (0xF0F0 & ~0x0FF0) and c.z == 0 and c.n == 0

    c.x[1], c.x[2] = 0x1234, 0x1234
    c.step((0x6018, "bics", "xzr, x1, x2"))
    assert c.z == 1 and c.n == 0

    c.x[1], c.x[2] = 3, 7
    c.step((0x601C, "mneg", "x0, x1, x2"))
    assert c.x[0] == ((-21) & _M64)

    # --- JIT (_emit) ---
    # 1) negs x0, x1 ; cset x0, vs ; ret
    svc.load_blob(eng, 0x7000, _w(0xEB0103E0) + _w(0x9A9F77E0) + _w(0xD65F03C0))
    assert svc.run(eng, 0x7000, (0, 1 << 63))["ret"] == 1
    assert svc.run(eng, 0x7000, (0, 5))["ret"] == 0

    # 2) eon x0, x1, x2 ; ret
    svc.load_blob(eng, 0x7100, _w(0xCA220020) + _w(0xD65F03C0))
    assert svc.run(eng, 0x7100, (0, 0xAA, 0x0F))["ret"] == ((0xAA ^ ~0x0F) & _M64)

    # 3) orn x0, x1, x2 ; ret
    svc.load_blob(eng, 0x7200, _w(0xAA220020) + _w(0xD65F03C0))
    assert svc.run(eng, 0x7200, (0, 0xAA, 0x0F))["ret"] == ((0xAA | ~0x0F) & _M64)

    # 4) bics xzr, x1, x2 ; cset x0, eq ; ret
    svc.load_blob(eng, 0x7300, _w(0xEA22003F) + _w(0x9A9F17E0) + _w(0xD65F03C0))
    assert svc.run(eng, 0x7300, (0, 0xFF, 0xFF))["ret"] == 1
    assert svc.run(eng, 0x7300, (0, 0xFF, 0x0F))["ret"] == 0

    # 5) mneg x0, x1, x2 ; ret
    svc.load_blob(eng, 0x7400, _w(0x9B02FC20) + _w(0xD65F03C0))
    assert svc.run(eng, 0x7400, (0, 6, 7))["ret"] == ((-42) & _M64)

    # 6) 32-Bit w-Formen in JIT (_emit): negs w0,w1 + eon/orn/bics/mneg w0,w1,w2
    svc.load_blob(eng, 0x7500, _w(0x6B0103E0) + _w(0x1A9F77E0) + _w(0xD65F03C0))  # negs w0,w1; cset w0,vs; ret
    assert svc.run(eng, 0x7500, (0, 0x80000000))["ret"] == 1
    assert svc.run(eng, 0x7500, (0, 5))["ret"] == 0

    svc.load_blob(eng, 0x7600, _w(0x4A220020) + _w(0xD65F03C0))  # eon w0, w1, w2
    assert svc.run(eng, 0x7600, (0, 0xAA, 0x0F))["ret"] == ((0xAA ^ ~0x0F) & 0xFFFFFFFF)

    svc.load_blob(eng, 0x7700, _w(0x2A220020) + _w(0xD65F03C0))  # orn w0, w1, w2
    assert svc.run(eng, 0x7700, (0, 0xAA, 0x0F))["ret"] == ((0xAA | ~0x0F) & 0xFFFFFFFF)

    svc.load_blob(eng, 0x7800, _w(0x6A220020) + _w(0xD65F03C0))  # bics w0, w1, w2
    assert svc.run(eng, 0x7800, (0, 0xFF, 0x0F))["ret"] == 0xF0

    svc.load_blob(eng, 0x7900, _w(0x1B02FC20) + _w(0xD65F03C0))  # mneg w0, w1, w2
    assert svc.run(eng, 0x7900, (0, 3, 7))["ret"] == ((-21) & 0xFFFFFFFF)
