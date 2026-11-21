# -*- coding: utf-8 -*-
"""Test X1-Akzeptanz: DebugSession ueber Java+nativ — Java-Feld UND nativen Speicher am BP aendern."""
import struct
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.debug_unified import DebugSession
from core.java.objects import JObject


def _w(x):
    return struct.pack("<I", x & 0xffffffff)


def test_unified_mutation_both_sides():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)

    # native fn @0x1000: x9 = *(0x2000) ; x0 = x2 + x9 ; ret   (x2 = jint-Arg von J0)
    code = b"".join([_w(0xD284000A),   # movz x10, #0x2000
                     _w(0xF9400149),   # ldr  x9, [x10]
                     _w(0x8B090040),   # add  x0, x2, x9
                     _w(0xD65F03C0)])  # ret
    svc.load_blob(eng, 0x1000, code)
    svc.load_blob(eng, 0x2000, struct.pack("<Q", 10))   # nativer Zustand = 10

    sess = DebugSession(eng, jni)
    # Java: compute(obj) { return native(obj.x); }  obj.x in v2 (nregs=3,nargs=1)
    prog = [("iget", 0, 2, "Lc;", "x"),
            ("invoke-native", 1, 0x1000, "Lc;", 0),
            ("return", 1)]
    sess.vm.register_method("Lc;", "compute", "(Lc;)I", prog, nregs=3, nargs=1, static=True)
    obj = JObject("Lc;", {"x": 5})

    # Referenzlauf ohne Mutation: 5 + 10 = 15
    st = sess.start("Lc;", "compute", "(Lc;)I", (obj,))
    sess.clear_java_breakpoints()
    st = sess.cont()
    assert st["mode"] == "done" and st["result"] == 15

    # Jetzt MIT Mutation: BP am iget (pc=0) -> Java-Feld x:=100 UND nativen Speicher 0x2000:=1000
    obj2 = JObject("Lc;", {"x": 5})
    sess2 = DebugSession(eng, jni, vm=sess.vm)
    sess2.add_java_breakpoint("Lc;", "compute", "(Lc;)I", pc=0)
    st = sess2.start("Lc;", "compute", "(Lc;)I", (obj2,), stop_at_entry=False)
    assert st["mode"] == "paused" and st["pc"] == 0        # am Breakpoint (vor iget)
    assert sess2.set_java_field(2, "x", 100) is True          # Java-Feld
    sess2.native_mem_write(0x2000, struct.pack("<Q", 1000))   # nativer Speicher
    sess2.clear_java_breakpoints()
    st = sess2.cont()
    # native(100) = 100 + 1000 = 1100
    assert st["mode"] == "done" and st["result"] == 1100
