# -*- coding: utf-8 -*-
"""Tests fuer J1 (DalvikVM-Kern, Scaffold): Register-VM Kern-Opcodes + Dalvik→native (J0-Bruecke)."""
import pytest

from core.java.dalvik import DalvikVM, DalvikUnknownOpcode
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.bridge import JavaToNativeBridge


def test_arithmetic():
    vm = DalvikVM()
    prog = [("const", 0, 5), ("const", 1, 10), ("add-int", 2, 0, 1), ("return", 2)]
    assert vm.run_method(prog, nregs=3) == 15


def test_loop_sum_1_to_5():
    vm = DalvikVM()
    prog = [
        ("const", 0, 0),            # 0: acc=0
        ("const", 1, 1),            # 1: i=1
        ("const", 2, 6),            # 2: limit=6
        ("if-lt", 1, 2, 5),         # 3: if i<6 goto 5
        ("goto", 8),                # 4: else -> return
        ("add-int", 0, 0, 1),       # 5: acc += i
        ("add-int/lit", 1, 1, 1),   # 6: i++
        ("goto", 3),                # 7: loop
        ("return", 0),              # 8: return acc
    ]
    assert vm.run_method(prog, nregs=3) == 15      # 1+2+3+4+5


def test_args_and_return_signed():
    vm = DalvikVM()
    prog = [("sub-int", 0, 0, 1), ("return", 0)]   # arg0 - arg1
    assert vm.run_method(prog, nregs=2, args=(3, 10)) == -7


def test_unknown_opcode_raises_structured():
    vm = DalvikVM()
    prog = [("const", 0, 1), ("const-method-handle", 1, 0), ("return", 1)]
    with pytest.raises(DalvikUnknownOpcode) as ei:
        vm.run_method(prog, nregs=2)
    assert ei.value.op == "const-method-handle" and ei.value.pc == 1


def test_dalvik_calls_native_via_bridge():
    """Dalvik → JNI → native → Dalvik: const 41; invoke-native (arg+1); return -> 42."""
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    svc.load_blob(eng, 0x1000, bytes.fromhex("40040091") + bytes.fromhex("c0035fd6"))  # add x0,x2,#1 ; ret
    vm = DalvikVM(bridge=JavaToNativeBridge(eng, jni))
    prog = [("const", 0, 41),
            ("invoke-native", 1, 0x1000, "Lcom/example/Foo;", 0),
            ("return", 1)]
    assert vm.run_method(prog, nregs=2) == 42


def test_coverage_collected():
    vm = DalvikVM()
    vm.run_method([("const", 0, 1), ("return", 0)], nregs=1)
    assert vm.coverage.get("const") == 1 and vm.coverage.get("return") == 1
