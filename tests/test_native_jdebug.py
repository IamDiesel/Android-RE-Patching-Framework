# -*- coding: utf-8 -*-
"""Tests fuer X1: JavaDebugger (Breakpoints, Step, Inspect, Mutation, Per-Step-Replay) ueber der DalvikVM."""
from core.java.dalvik import DalvikVM
from core.java.debug import JavaDebugger

# sum(n) = 1+..+n ; acc=v0, i=v1, n=v2 (nregs=3,nargs=1 -> n in v2)
_PROG = [("const", 0, 0), ("const", 1, 1), ("if-gt", 1, 2, 6),
         ("add-int/2addr", 0, 1), ("add-int/lit", 1, 1, 1), ("goto", 2), ("return", 0)]


def _vm():
    vm = DalvikVM()
    vm.register_method("Lx;", "sum", "(I)I", _PROG, nregs=3, nargs=1, static=True)
    return vm


def test_start_pauses_at_entry():
    dbg = JavaDebugger(_vm())
    st = dbg.start("Lx;", "sum", "(I)I", (3,))
    assert st["mode"] == "paused" and st["step"] == 0 and st["pc"] == 0


def test_breakpoint_and_registers():
    dbg = JavaDebugger(_vm())
    dbg.start("Lx;", "sum", "(I)I", (3,))
    dbg.add_breakpoint("Lx;", "sum", "(I)I", pc=3)   # add-int/2addr
    st = dbg.cont()
    assert st["mode"] == "paused" and st["pc"] == 3
    regs = dbg.registers()
    assert regs[0] == 0 and regs[1] == 1 and regs[2] == 3   # acc=0,i=1,n=3


def test_mutation_changes_result():
    dbg = JavaDebugger(_vm())
    dbg.start("Lx;", "sum", "(I)I", (3,))
    dbg.add_breakpoint("Lx;", "sum", "(I)I", pc=3)
    dbg.cont()
    assert dbg.set_register(0, 100) is True   # acc := 100 am ersten Treffer
    dbg.clear_breakpoints()
    st = dbg.cont()
    assert st["mode"] == "done" and st["result"] == 106   # 100 + 1+2+3


def test_single_step():
    dbg = JavaDebugger(_vm())
    dbg.start("Lx;", "sum", "(I)I", (1,))
    s0 = dbg.state()["step"]
    dbg.step()
    assert dbg.state()["step"] == s0 + 1


def test_timeline_and_replay():
    dbg = JavaDebugger(_vm())
    dbg.record_timeline = True                 # AP1.1: Time-Travel braucht Aufzeichnung
    dbg.start("Lx;", "sum", "(I)I", (3,))
    dbg.clear_breakpoints()
    st = dbg.cont()
    assert st["mode"] == "done" and st["result"] == 6
    total = len(dbg.timeline)
    assert total > 6
    # Time-Travel: zu Schritt 2 zurueck (deterministische Re-Exec)
    st2 = dbg.goto(2)
    assert st2["mode"] == "paused" and st2["step"] == 2
    # und wieder vorwaerts bis Ende
    st3 = dbg.cont()
    assert st3["mode"] == "done" and st3["result"] == 6


def test_stop():
    dbg = JavaDebugger(_vm())
    dbg.start("Lx;", "sum", "(I)I", (5,))
    st = dbg.stop()
    assert st["mode"] == "stopped"
