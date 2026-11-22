# -*- coding: utf-8 -*-
"""core.java.debug_unified — vereinheitlichte Debug-Sitzung über Java (DalvikVM) UND nativ (arm64mini).

Eine `DebugSession` haelt den `JavaDebugger` (Java-Seite) und die native Engine zusammen und bietet
EIN Modell: Breakpoints/Step/Inspect/Mutation auf beiden Seiten, über die J0-Brücke hinweg. Die native
Hälfte nutzt die bestehende Infra (`arm64mini`-Engine + `native_trace_service`). Zielagnostisch.

Akzeptanz (X1/MS-6): an einem Breakpoint ein Java-Feld UND nativen Speicher ändern, weiterlaufen,
Effekt im Ergebnis sichtbar.
"""
from __future__ import annotations

from core.java.debug import JavaDebugger

_M64 = (1 << 64) - 1


class DebugSession:
    def __init__(self, eng, jni, vm=None, emu=None):
        self.eng = eng
        self.jni = jni
        from core.java.dalvik import DalvikVM
        from core.java.bridge import JavaToNativeBridge
        self.vm = vm if vm is not None else DalvikVM()
        if self.vm.bridge is None:
            self.vm.bridge = JavaToNativeBridge(eng, jni, emu, vm=self.vm)
        self.jdbg = JavaDebugger(self.vm)
        from services.native_trace_service import NativeTraceService
        self.ntr = NativeTraceService(publish=False)

    # ---- Java-Seite (Delegation) ------------------------------------------
    def start(self, cls, name, sig, args=(), stop_at_entry=True):
        return self.jdbg.start(cls, name, sig, args, stop_at_entry)

    def step(self):
        return self.jdbg.step()

    def cont(self):
        return self.jdbg.cont()

    def goto(self, n):
        return self.jdbg.goto(n)

    def step_over(self):
        return self.jdbg.step_over()

    def step_out(self):
        return self.jdbg.step_out()

    def add_java_breakpoint(self, cls, name, sig, pc=None):
        self.jdbg.add_breakpoint(cls, name, sig, pc)

    def clear_java_breakpoints(self):
        self.jdbg.clear_breakpoints()

    def inspect(self):
        st = self.jdbg.inspect()
        st["native"] = self.native_state()
        return st

    def set_java_field(self, obj_reg, field, value):
        return self.jdbg.set_field(obj_reg, field, value)

    def set_java_register(self, i, value):
        return self.jdbg.set_register(i, value)

    def set_java_static(self, cls, field, value):
        return self.jdbg.set_static(cls, field, value)

    # ---- Native Seite -----------------------------------------------------
    def native_state(self):
        if self.eng is None or getattr(self.eng, "cpu", None) is None:
            return {"available": False, "pc": "0x0", "x0": "0x0", "sp": "0x0", "lr": "0x0"}
        c = self.eng.cpu
        return {"pc": hex(int(getattr(c, "pc", 0) or 0) & _M64), "x0": hex(int(c.x[0]) & _M64),
                "sp": hex(int(c.sp) & _M64), "lr": hex(int(c.lr) & _M64)}

    def native_mem_read(self, addr, n=8):
        return bytes(self.eng.cpu.m.rb(int(addr) & _M64, int(n)))

    def native_mem_write(self, addr, data):
        self.eng.cpu.m.wb(int(addr) & _M64, bytes(data))
        return True

    def native_reg(self, i):
        return int(self.eng.cpu.x[int(i)]) & _M64

    def native_set_reg(self, i, value):
        self.eng.cpu.x[int(i)] = int(value) & _M64
        return True

    def native_step_insn(self, n=1):
        """Eine/mehrere native Instruktionen einzeln ausfuehren (bestehende Trace-Infra)."""
        return self.ntr.step_insn(self.eng, int(n))
