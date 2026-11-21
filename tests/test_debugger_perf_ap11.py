# -*- coding: utf-8 -*-
"""AP 1.1: entkoppelter step_count, record_timeline/record_regs-Flags, _fmt-Zyklenschutz, Fast-Path."""
from core.java.debug import JavaDebugger, _fmt
from core.java.dalvik import DalvikVM
from core.java.objects import JObject


def _sum_prog():
    # Schleife 1..100 summieren -> viele Schritte
    return [("const", 0, 0), ("const", 1, 1), ("const", 2, 100),
            ("add-int/2addr", 0, 1),            # :loop (pc3)
            ("add-int/lit8", 1, 1, 1),
            ("if-le", 1, 2, 3),
            ("return", 0)]


def test_step_count_decoupled_from_timeline():
    vm = DalvikVM()
    vm.register_method("Lt;", "f", "()I", _sum_prog(), nregs=3, nargs=0, static=True)
    jd = JavaDebugger(vm)
    jd.record_timeline = False                 # Fast-Path
    jd.start("Lt;", "f", "()I", ())
    jd.cont()
    import time
    for _ in range(200):
        if jd.mode in ("done", "error", "stopped"): break
        time.sleep(0.01)
    assert jd.mode == "done"
    assert jd.step_count > 100                  # viele Schritte gezaehlt
    assert len(jd.timeline) == 0                # aber NICHTS aufgezeichnet (Fast-Path)


def test_record_timeline_without_regs():
    vm = DalvikVM()
    vm.register_method("Lt;", "f", "()I", _sum_prog(), nregs=3, nargs=0, static=True)
    jd = JavaDebugger(vm)
    jd.record_timeline = True
    jd.record_regs = False
    jd.start("Lt;", "f", "()I", ())
    jd.cont()
    import time
    for _ in range(200):
        if jd.mode in ("done", "error", "stopped"): break
        time.sleep(0.01)
    assert len(jd.timeline) > 100
    assert "regs" not in jd.timeline[0]         # keine Register-Kopie
    jd.timeline.clear()


def test_record_regs_on():
    vm = DalvikVM()
    vm.register_method("Lt;", "f", "()I", _sum_prog(), nregs=3, nargs=0, static=True)
    jd = JavaDebugger(vm); jd.record_timeline = True; jd.record_regs = True
    jd.start("Lt;", "f", "()I", ()); jd.cont()
    import time
    for _ in range(200):
        if jd.mode in ("done", "error", "stopped"): break
        time.sleep(0.01)
    assert "regs" in jd.timeline[0]


def test_fmt_cycle_safe():
    a = JObject("La;"); b = JObject("Lb;")
    a.fields["b"] = b; b.fields["a"] = a         # Zyklus
    out = _fmt(a)
    assert out["t"] == "Object"                  # terminiert (kein RecursionError)
    # Tiefenlimit: tief verschachtelt -> truncated
    deep = JObject("Ld0;"); cur = deep
    for i in range(10):
        nxt = JObject("Ld%d;" % (i + 1)); cur.fields["n"] = nxt; cur = nxt
    o = _fmt(deep)
    s = str(o)
    assert "truncated" in s
