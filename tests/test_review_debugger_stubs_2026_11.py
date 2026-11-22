# -*- coding: utf-8 -*-
"""Tests for Worker B tasks: Concurrency/Determinism, DeviationJournal, Dalvik & Stubs (2026-11)."""
from __future__ import annotations

from pathlib import Path
import threading
import time
import pytest

from core.java.class_provider import TextClassProvider
from core.java.dalvik import (
    DalvikException,
    DalvikUnknownOpcode,
    DalvikVM,
    MethodDef,
    VmBudgetExceeded,
)
from core.java.debug import DebugStop, JavaDebugger, RingTimeline, safe_eval
from core.java.deviation_journal import DebugStop as JournalDebugStop, DeviationJournal
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.objects import JArray, JObject, JString
from core.java import smali as Sm


def test_deviation_journal_threadsafe_dedup_and_cap():
    assert DebugStop is JournalDebugStop
    assert issubclass(DebugStop, BaseException)
    assert not issubclass(DebugStop, Exception)

    try:
        try:
            raise DebugStop("stop")
        except Exception:
            pytest.fail("except Exception must not swallow DebugStop")
    except DebugStop:
        pass

    journal = DeviationJournal(max_entries=5)
    errors = []

    def worker(tid):
        try:
            for i in range(20):
                journal.record("missing_stub", f"Lcls{i % 3};->m()V", detail="d", step=tid * 100 + i)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    entries = journal.to_list()
    assert len(entries) == 3
    assert sum(e["count"] for e in entries) == 80
    summ = journal.summary()
    assert summ["unique"] == 3
    assert summ["total"] == 80
    assert summ["by_kind"]["missing_stub"] == 80

    # Cap enforcement
    for idx in range(10):
        journal.record("k", f"site_{idx}")
    assert len(journal.to_list()) == 5
    journal.clear()
    assert journal.to_list() == []


def test_h1_h2_stop_prevents_paused_instruction_and_run_interaction():
    src = (
        ".class public Ltest/StopSafe;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static run()I\n"
        "    .locals 1\n"
        "    const/4 v0, 0x1\n"
        "    sput v0, Ltest/StopSafe;->hit:I\n"
        "    return v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    for md in Sm.parse_class(src)[1]:
        vm.register(md)
    dbg = JavaDebugger(vm)
    dbg.add_breakpoint("Ltest/StopSafe;", "run", "()I", pc=1)
    st = dbg.start("Ltest/StopSafe;", "run", "()I", (), stop_at_entry=False)
    assert st["mode"] == "paused" and st["pc"] == 1
    assert vm.statics.get("Ltest/StopSafe;", {}).get("hit", 0) == 0

    # While paused, run_interaction returns current paused state without launching over it
    st_inter = dbg.run_interaction(lambda: 42)
    assert st_inter["mode"] == "paused"

    st_stop = dbg.stop()
    assert st_stop["mode"] == "stopped"
    # Instruction at paused pc=1 (sput) MUST NOT have executed
    assert vm.statics.get("Ltest/StopSafe;", {}).get("hit", 0) == 0

    # Launching with no entry/run_fn returns idle cleanly
    dbg2 = JavaDebugger(DalvikVM())
    st_empty = dbg2._launch()
    assert st_empty["mode"] == "idle"


def test_h2_stale_worker_generation_isolated():
    vm = DalvikVM()
    vm.register_method(
        "Lt/Gen;", "loop", "()I",
        [("const", 0, 1), ("goto", 0)],
        nregs=1, nargs=0, static=True,
    )
    dbg = JavaDebugger(vm)
    # Start a long loop asynchronously
    t = threading.Thread(
        target=lambda: dbg.start("Lt/Gen;", "loop", "()I", (), stop_at_entry=False, wait_timeout=0.05),
        daemon=True,
    )
    t.start()
    time.sleep(0.05)
    # Launching a different target supersedes active generation cleanly
    vm.register_method("Lt/Gen;", "fast", "()I", [("const", 0, 77), ("return", 0)], nregs=1, nargs=0, static=True)
    st = dbg.start("Lt/Gen;", "fast", "()I", (), stop_at_entry=False)
    t.join(timeout=2.0)
    assert st["mode"] == "done" and st["result"] == 77


def test_h3_goto_zero_deterministic_on_static_int_array():
    vm = DalvikVM()
    vm.statics["Lt/Arr;"] = {"nums": JArray("[I", [1, 10])}
    # Read nums[0], increment nums[0], return old nums[0]
    insns = [
        ("sget", 0, "Lt/Arr;", "nums"),
        ("const", 1, 0),
        ("aget", 2, 0, 1),
        ("add-int/lit", 3, 2, 1),
        ("aput", 3, 0, 1),
        ("return", 2),
    ]
    vm.register_method("Lt/Arr;", "inc", "()I", insns, nregs=4, nargs=0, static=True)
    dbg = JavaDebugger(vm)

    r1 = dbg.start("Lt/Arr;", "inc", "()I", (), stop_at_entry=False)
    assert r1["mode"] == "done" and r1["result"] == 1
    assert vm.statics["Lt/Arr;"]["nums"].data[0] == 2

    dbg.goto(0)
    r2 = dbg.cont()
    assert r2["mode"] == "done" and r2["result"] == 1

    dbg.goto(0)
    r3 = dbg.cont()
    assert r3["mode"] == "done" and r3["result"] == 1

    # Entry-arg JArray mutation also reset by goto(0), and coverage reset cleanly
    vm2 = DalvikVM()
    vm2.register_method(
        "Lt/ArgArr;", "incArg", "([I)I",
        [("const", 0, 0), ("aget", 1, 3, 0), ("add-int/lit", 2, 1, 1), ("aput", 2, 3, 0), ("return", 1)],
        nregs=4, nargs=1, static=True,
    )
    dbg2 = JavaDebugger(vm2)
    arr_arg = JArray("[I", [1])
    assert dbg2.start("Lt/ArgArr;", "incArg", "([I)I", (arr_arg,), stop_at_entry=False)["result"] == 1
    cov_after_first = dict(vm2.coverage)
    dbg2.goto(0)
    assert dbg2.cont()["result"] == 1
    assert vm2.coverage == cov_after_first


def test_m1_exception_pause_counts_caught_and_uncaught():
    # Outer catches RuntimeException from inner -> 0 pauses with exception_unhandled_only=True
    src = (
        ".class public Lt/Exc;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static inner()V\n"
        "    .locals 1\n"
        "    new-instance v0, Ljava/lang/RuntimeException;\n"
        "    throw v0\n"
        ".end method\n"
        ".method public static mid()V\n"
        "    .locals 0\n"
        "    invoke-static {}, Lt/Exc;->inner()V\n"
        "    return-void\n"
        ".end method\n"
        ".method public static outerCaught()I\n"
        "    .locals 1\n"
        "    :try_s\n"
        "    invoke-static {}, Lt/Exc;->mid()V\n"
        "    :try_e\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        "    :catch_h\n"
        "    move-exception v0\n"
        "    const/4 v0, 0x7\n"
        "    return v0\n"
        "    .catch Ljava/lang/RuntimeException; {:try_s .. :try_e} :catch_h\n"
        ".end method\n"
        ".method public static outerUncaught()I\n"
        "    .locals 1\n"
        "    invoke-static {}, Lt/Exc;->mid()V\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    for md in Sm.parse_class(src)[1]:
        vm.register(md)

    dbg = JavaDebugger(vm)
    dbg.stop_on_exception = True
    dbg.exception_unhandled_only = True

    st_caught = dbg.start("Lt/Exc;", "outerCaught", "()I", (), stop_at_entry=False)
    assert st_caught["mode"] == "done"
    assert st_caught["result"] == 7

    # Uncaught across 3 frames (outerUncaught -> mid -> inner) must pause ONCE, not 3 times
    st_uncaught = dbg.start("Lt/Exc;", "outerUncaught", "()I", (), stop_at_entry=False)
    assert st_uncaught["mode"] == "paused"
    assert st_uncaught["reason"] == "exception"
    st_end = dbg.cont()
    assert st_end["mode"] == "error"


def test_m1_rethrow_caught_exception_reports_again():
    src = (
        ".class public Lt/Rethrow;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static run()V\n"
        "    .locals 1\n"
        "    :ts\n"
        "    new-instance v0, Ljava/lang/RuntimeException;\n"
        "    throw v0\n"
        "    :te\n"
        "    :ch\n"
        "    move-exception v0\n"
        "    throw v0\n"
        "    .catch Ljava/lang/RuntimeException; {:ts .. :te} :ch\n"
        ".end method\n"
    )
    vm = DalvikVM()
    for md in Sm.parse_class(src)[1]:
        vm.register(md)
    dbg = JavaDebugger(vm)
    dbg.stop_on_exception = True
    dbg.exception_unhandled_only = True

    st = dbg.start("Lt/Rethrow;", "run", "()V", (), stop_at_entry=False)
    assert st["mode"] == "paused"
    assert st["reason"] == "exception"
    assert st["pc"] == 3   # second throw outside try/catch
    st2 = dbg.cont()
    assert st2["mode"] == "error"


def test_m2_safe_eval_bounds_bp_error_and_max_steps():
    # Bounds checks
    with pytest.raises(ValueError):
        safe_eval("2**9**9", {})
    with pytest.raises(ValueError):
        safe_eval("1 << 300", {})
    with pytest.raises(ValueError):
        safe_eval("'a' * 5000", {})
    with pytest.raises(ValueError):
        safe_eval("1" * 600, {})
    with pytest.raises(ValueError):
        safe_eval(" + ".join(["1"] * 40), {})  # > 64 AST nodes

    vm = DalvikVM()
    vm.register_method("Lt/Bp;", "f", "()I", [("const", 0, 5), ("return", 0)], nregs=1, nargs=0, static=True)
    dbg = JavaDebugger(vm)
    with pytest.raises(ValueError):
        dbg.add_breakpoint("Lt/Bp;", "f", "()I", pc=0, cond="v0 ==")
    with pytest.raises(ValueError):
        dbg.add_action_point("Lt/Bp;", "f", "()I", pc=0, cond="v0 ==")

    dbg.add_breakpoint("Lt/Bp;", "f", "()I", pc=1, cond="v9 == 5")
    st = dbg.start("Lt/Bp;", "f", "()I", (), stop_at_entry=False)
    assert st["mode"] == "paused"
    assert st["reason"] == "bp_error"
    assert dbg.last_bp_error is not None and "v9" in dbg.last_bp_error["error"]
    assert any(m["kind"] == "bp_error" for m in dbg.marker_list())
    dbg.cont()

    # max_steps sets reason="max_steps"
    vm2 = DalvikVM()
    vm2.register_method("Lt/Loop;", "spin", "()V", [("goto", 0)], nregs=1, nargs=0, static=True)
    dbg2 = JavaDebugger(vm2, max_steps=10)
    st2 = dbg2.start("Lt/Loop;", "spin", "()V", (), stop_at_entry=False)
    assert st2["mode"] == "stopped"
    assert st2["reason"] == "max_steps"


def test_m5_java_stackoverflowerror_caught_but_vm_budget_not_caught():
    # Java StackOverflowError thrown by bytecode MUST be catchable by .catch
    src = (
        ".class public Lt/M5;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static catchJavaSOF()I\n"
        "    .locals 1\n"
        "    :ts\n"
        "    new-instance v0, Ljava/lang/StackOverflowError;\n"
        "    throw v0\n"
        "    :te\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        "    :ch\n"
        "    move-exception v0\n"
        "    const/16 v0, 0x2a\n"
        "    return v0\n"
        "    .catch Ljava/lang/StackOverflowError; {:ts .. :te} :ch\n"
        ".end method\n"
        ".method public static catchAllLoop()I\n"
        "    .locals 1\n"
        "    :ts\n"
        "    :l\n"
        "    goto :l\n"
        "    :te\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        "    :ch\n"
        "    move-exception v0\n"
        "    const/16 v0, 0x63\n"
        "    return v0\n"
        "    .catchall {:ts .. :te} :ch\n"
        ".end method\n"
        ".method public static callerLenient()I\n"
        "    .locals 1\n"
        "    invoke-static {}, Lt/M5;->catchAllLoop()I\n"
        "    move-result v0\n"
        "    return v0\n"
        ".end method\n"
    )
    vm = DalvikVM()
    for md in Sm.parse_class(src)[1]:
        vm.register(md)
    assert vm.invoke("Lt/M5;", "catchJavaSOF", "()I", ()) == 42

    # StepBudget & WallBudget (VmBudgetExceeded) MUST NOT be caught by .catchall or lenient mode
    vm.step_budget = 25
    vm.lenient = True
    with pytest.raises(VmBudgetExceeded):
        vm.invoke("Lt/M5;", "callerLenient", "()I", ())

    vm_wall = DalvikVM()
    for md in Sm.parse_class(src)[1]:
        vm_wall.register(md)
    vm_wall.lenient = True
    vm_wall.wall_deadline = time.monotonic() + 0.03
    with pytest.raises(VmBudgetExceeded):
        vm_wall.invoke("Lt/M5;", "callerLenient", "()I", ())


def test_m6_ring_timeline_bp_index_and_flight_path(tmp_path):
    vm = DalvikVM()
    vm.register_method(
        "Lt/Perf;", "run", "()I",
        [("const", 0, 0), ("add-int/lit", 0, 0, 1), ("const", 1, 10), ("if-lt", 0, 1, 1), ("return", 0)],
        nregs=2, nargs=0, static=True,
    )
    dbg = JavaDebugger(vm, timeline_cap=5)
    dbg.record_timeline = True
    expected_root_flight = str(Path(__file__).resolve().parents[1] / "data" / "glass_flight.jsonl")
    assert dbg.flight_path == expected_root_flight

    dbg.flight_path = str(tmp_path / "flight.jsonl")
    dbg.flight_recorder_enabled = True
    dbg.add_breakpoint("Lt/Perf;", "run", "()I", pc=4)
    st = dbg.start("Lt/Perf;", "run", "()I", (), stop_at_entry=False)
    assert st["mode"] == "paused" and st["pc"] == 4
    assert ("Lt/Perf;", "run", "()I") in dbg._bp_by_m
    assert dbg.remove_breakpoint("Lt/Perf;", "run", "()I", pc=4) is True
    assert ("Lt/Perf;", "run", "()I") not in dbg._bp_by_m

    dbg.set_breakpoints([("Lt/Perf;", "run", "()I", 4)])
    assert ("Lt/Perf;", "run", "()I") in dbg._bp_by_m
    dbg.clear_breakpoints()
    assert dbg._bp_by_m == {} and dbg._bp_wild == set()

    st2 = dbg.cont()
    assert st2["mode"] == "done" and st2["result"] == 10
    assert isinstance(dbg.timeline, RingTimeline)
    assert len(dbg.timeline) == 5
    sliced = dbg.timeline[1:4]
    assert isinstance(sliced, list) and len(sliced) == 3
    tail_sliced = dbg.timeline[-3:]
    assert isinstance(tail_sliced, list) and len(tail_sliced) == 3
    assert dbg.timeline[-1]["step"] == dbg.step_count - 1
    assert (tmp_path / "flight.jsonl").read_text(encoding="utf-8").strip() != ""


def test_s1_hierarchy_button_animate_and_activity_context_stub():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    btn = JObject("Landroid/widget/Button;", {})
    src = (
        ".class public Lt/Ui;\n"
        ".super Ljava/lang/Object;\n"
        ".method public static checkAnim(Landroid/widget/Button;)Landroid/view/ViewPropertyAnimator;\n"
        "    .locals 1\n"
        "    invoke-virtual {p0}, Landroid/widget/Button;->animate()Landroid/view/ViewPropertyAnimator;\n"
        "    move-result-object v0\n"
        "    return-object v0\n"
        ".end method\n"
        ".method public static checkPkg(Landroid/app/Activity;)Ljava/lang/String;\n"
        "    .locals 1\n"
        "    invoke-virtual {p0}, Landroid/app/Activity;->getPackageName()Ljava/lang/String;\n"
        "    move-result-object v0\n"
        "    return-object v0\n"
        ".end method\n"
    )
    for md in Sm.parse_class(src)[1]:
        vm.register(md)

    anim_obj = vm.invoke("Lt/Ui;", "checkAnim", "(Landroid/widget/Button;)Landroid/view/ViewPropertyAnimator;", (btn,))
    assert isinstance(anim_obj, JObject) and anim_obj.cls == "Landroid/view/ViewPropertyAnimator;"

    act = JObject("Landroid/app/Activity;", {})
    pkg_obj = vm.invoke("Lt/Ui;", "checkPkg", "(Landroid/app/Activity;)Ljava/lang/String;", (act,))
    assert isinstance(pkg_obj, JString) and len(pkg_obj.text) > 0
    assert not stubs.missing

    # Hierarchy checks: AppCompatActivity -> Context, CheckBox -> Button -> View, Integer -> Number
    assert vm._instance_of(JObject("Landroidx/appcompat/app/AppCompatActivity;", {}), "Landroid/content/Context;") == 1
    assert vm._instance_of(JObject("Landroid/widget/CheckBox;", {}), "Landroid/widget/Button;") == 1
    assert vm._instance_of(JObject("Ljava/lang/Integer;", {}), "Ljava/lang/Number;") == 1


def test_s2_missing_deduplication_vm_and_stubs():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    insns = [
        ("invoke-static", (), "Landroid/nope/Ghost;", "ping", "()I"),
        ("invoke-static", (), "Landroid/nope/Ghost;", "ping", "()I"),
        ("invoke-static", (), "Lapp/missing/Foo;", "bar", "()I"),
        ("invoke-static", (), "Lapp/missing/Foo;", "bar", "()I"),
        ("invoke-static", (), "Lapp/missing/Foo;", "bar", "()I"),
        ("return-void",),
    ]
    vm.run_method(insns, 1)

    assert len(stubs.missing) == 1
    assert stubs.missing[0]["cls"] == "Landroid/nope/Ghost;"
    assert stubs.missing[0]["count"] == 2

    assert len(vm.missing) == 1
    assert vm.missing[0]["cls"] == "Lapp/missing/Foo;"
    assert vm.missing[0]["count"] == 3

    # Deviations recorded for both
    kinds = {d["kind"]: d["count"] for d in vm.deviations.to_list()}
    assert kinds["missing_stub"] == 2
    assert kinds["missing_method"] == 3


def test_s3_handler_delay_order_removecallbacks_and_asynctask_cancel():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)

    handler = JObject("Landroid/os/Handler;", {})
    r_a = JObject("Ljava/lang/Runnable;", {"tag": "a"})
    r_b = JObject("Ljava/lang/Runnable;", {"tag": "b"})
    r_c = JObject("Ljava/lang/Runnable;", {"tag": "c"})
    r_drop = JObject("Ljava/lang/Runnable;", {"tag": "drop"})

    assert stubs.resolve("Landroid/os/Handler;", "postDelayed", "(Ljava/lang/Runnable;J)Z", [handler, r_a, 2000], False) == (True, 1)
    assert stubs.resolve("Landroid/os/Handler;", "post", "(Ljava/lang/Runnable;)Z", [handler, r_b], False) == (True, 1)
    assert stubs.resolve("Landroid/os/Handler;", "postDelayed", "(Ljava/lang/Runnable;J)Z", [handler, r_c, 500], False) == (True, 1)
    # Order: r_b (0ms), r_c (500ms), r_a (2000ms)
    assert [r.fields["tag"] for r in stubs.pending_runnables] == ["b", "c", "a"]

    # removeCallbacks removes r_c
    stubs.resolve("Landroid/os/Handler;", "removeCallbacks", "(Ljava/lang/Runnable;)V", [handler, r_c], False)
    assert [r.fields["tag"] for r in stubs.pending_runnables] == ["b", "a"]

    # delay > 60_000 dropped & recorded in deviations
    assert stubs.resolve("Landroid/os/Handler;", "postDelayed", "(Ljava/lang/Runnable;J)Z", [handler, r_drop, 120_000], False) == (True, 0)
    assert any(d["kind"] == "dropped_runnable" for d in vm.deviations.to_list())

    # AsyncTask cancel -> onCancelled instead of onPostExecute + doInBackground exception -> deviations
    trace = []
    class _TaskProv:
        def get_class(self, desc):
            if desc == "Lt/CancelTask;":
                return [
                    MethodDef("Lt/CancelTask;", "onPreExecute", "()V", [
                        ("const-string", 0, "pre"),
                        ("invoke-static", (0,), "Lt/Log;", "put", "(Ljava/lang/String;)V"),
                        ("return-void",),
                    ], nregs=2, nargs=1),
                    MethodDef("Lt/CancelTask;", "doInBackground", "([Ljava/lang/Object;)Ljava/lang/Object;", [
                        ("const", 0, 1),
                        ("invoke-virtual", (1, 0), "Lt/CancelTask;", "cancel", "(Z)Z"),
                        ("invoke-virtual", (1,), "Lt/CancelTask;", "isCancelled", "()Z"),
                        ("move-result", 0),
                        ("if-eqz", 0, 6),
                        ("const-string", 0, "bg_cancelled"),
                        ("return-object", 0),
                        ("const-string", 0, "bg_ok"),
                        ("return-object", 0),
                    ], nregs=3, nargs=2),
                    MethodDef("Lt/CancelTask;", "onPostExecute", "(Ljava/lang/Object;)V", [
                        ("const-string", 0, "post"),
                        ("invoke-static", (0,), "Lt/Log;", "put", "(Ljava/lang/String;)V"),
                        ("return-void",),
                    ], nregs=3, nargs=2),
                    MethodDef("Lt/CancelTask;", "onCancelled", "(Ljava/lang/Object;)V", [
                        ("const-string", 0, "cancelled"),
                        ("invoke-static", (0,), "Lt/Log;", "put", "(Ljava/lang/String;)V"),
                        ("return-void",),
                    ], nregs=3, nargs=2),
                ]
            if desc == "Lt/CrashTask;":
                return [
                    MethodDef("Lt/CrashTask;", "doInBackground", "([Ljava/lang/Object;)Ljava/lang/Object;", [
                        ("const", 0, 1),
                        ("const", 1, 0),
                        ("div-int", 0, 0, 1),
                        ("return-object", 0),
                    ], nregs=3, nargs=2),
                ]
            return None

        def get_super(self, desc):
            return {"Lt/CancelTask;": "Landroid/os/AsyncTask;", "Lt/CrashTask;": "Landroid/os/AsyncTask;"}.get(desc)

    vm.class_provider = _TaskProv()
    stubs.register("Lt/Log;", "put", "(Ljava/lang/String;)V", lambda a: trace.append(stubs._txt(a[0])))
    t_obj = JObject("Lt/CancelTask;", {})
    stubs._asynctask_exec((t_obj, None))
    assert trace == ["pre", "cancelled"]

    c_obj = JObject("Lt/CrashTask;", {})
    stubs._asynctask_exec((c_obj, None))
    assert any(d["kind"] == "asynctask_error" for d in vm.deviations.to_list())


def test_e6_check_cast_safe_closed_world():
    # Integer -> String raises ClassCastException
    vm = DalvikVM()
    vm.register_method(
        "Lt/Cast;", "badJdk", "()V",
        [("new-instance", 0, "Ljava/lang/Integer;"), ("check-cast", 0, "Ljava/lang/String;"), ("return-void",)],
        nregs=1, nargs=0, static=True,
    )
    with pytest.raises(DalvikException, match="ClassCastException"):
        vm.invoke("Lt/Cast;", "badJdk", "()V", ())

    # null / SystemService / View / $synth_fm / interfaces pass cleanly
    vm.run_method([("const", 0, 0), ("check-cast", 0, "Ljava/lang/String;"), ("return-void",)], 1)
    vm.run_method([("new-instance", 0, "Landroid/os/SystemService;"), ("check-cast", 0, "Landroid/net/ConnectivityManager;"), ("return-void",)], 1)
    vm.run_method([("new-instance", 0, "Landroid/view/View;"), ("check-cast", 0, "Landroid/widget/Button;"), ("return-void",)], 1)
    synth = JObject("Landroidx/fragment/app/FragmentManager;", {"$synth_fm": True})
    vm.run_method([("check-cast", 0, "Ljava/lang/String;"), ("return-void",)], 1, args=(synth,))

    # JString cast checks
    vm.run_method([("const-string", 0, "ok"), ("check-cast", 0, "Ljava/lang/CharSequence;"), ("return-void",)], 1)
    with pytest.raises(DalvikException, match="ClassCastException"):
        vm.run_method([("const-string", 0, "bad"), ("check-cast", 0, "Ljava/lang/Integer;"), ("return-void",)], 1)

    # JArray mismatch throws
    with pytest.raises(DalvikException, match="ClassCastException"):
        vm.run_method([("check-cast", 0, "[B"), ("return-void",)], 1, args=(JArray("[I", [1, 2]),))

    # Closed DEX concrete vs concrete throws; concrete vs interface passes
    smali_classes = (
        ".class public Lt/A;\n.super Ljava/lang/Object;\n"
        ".method public constructor <init>()V\n.registers 1\nreturn-void\n.end method\n"
    )
    smali_b = (
        ".class public Lt/B;\n.super Ljava/lang/Object;\n"
        ".method public constructor <init>()V\n.registers 1\nreturn-void\n.end method\n"
    )

    class _TwoProv:
        def __init__(self):
            self._p = {
                "Lt/A;": TextClassProvider(smali_classes),
                "Lt/B;": TextClassProvider(smali_b),
            }
        def get_class(self, d): return self._p[d].get_class(d) if d in self._p else None
        def get_super(self, d): return self._p[d].get_super(d) if d in self._p else None
        def get_source(self, d):
            if d == "Lt/A;": return smali_classes
            if d == "Lt/B;": return smali_b
            if d == "Lt/Iface;": return ".class public interface abstract Lt/Iface;\n.super Ljava/lang/Object;\n"
            return None

    vm2 = DalvikVM()
    vm2.class_provider = _TwoProv()
    with pytest.raises(DalvikException, match="ClassCastException"):
        vm2.run_method([("new-instance", 0, "Lt/A;"), ("check-cast", 0, "Lt/B;"), ("return-void",)], 1)
    # Interface target does not throw
    vm2.run_method([("new-instance", 0, "Lt/A;"), ("check-cast", 0, "Lt/Iface;"), ("return-void",)], 1)


def test_s5_overridden_stubs_and_expandable_list_view():
    stubs = AndroidStubs()
    assert ("Landroid/widget/ExpandableListView;", "getPackedPositionGroup", "(J)I") not in stubs._overridden_stubs
    ok, ret = stubs.resolve("Landroid/widget/ExpandableListView;", "getPackedPositionGroup", "(J)I", [7 << 32], True)
    assert ok and ret == 7

    stubs.register("Lt/Custom;", "m", "()I", lambda a: 1)
    stubs.register("Lt/Custom;", "m", "()I", lambda a: 2)
    assert ("Lt/Custom;", "m", "()I") in stubs._overridden_stubs
    assert stubs.resolve("Lt/Custom;", "m", "()I", [], True) == (True, 2)


def test_s4_auto_finish_onboarding_toggle_and_deviation_journal():
    from core.java.lifecycle import LifecycleRunner
    from core.java.seam_config import SeamConfig
    from core.java.view_tree import ViewNode, install_view_stubs

    cfg = SeamConfig.from_dict({"unpack_mode": "self_cache", "auto_finish_empty_onboarding": False})
    assert cfg.auto_finish_empty_onboarding is False
    assert cfg.to_dict()["auto_finish_empty_onboarding"] is False
    assert SeamConfig().auto_finish_empty_onboarding is True

    def _run_case(auto_finish):
        vm = DalvikVM()
        stubs = AndroidStubs(vm=vm)
        tree = install_view_stubs(stubs)
        vm.runtime = _StubRuntime(stubs)
        dbg = JavaDebugger(vm)
        vm._dbg = dbg

        calls = []
        vm.register_method("Lt/OnbAct;", "skip", "(Landroid/view/View;)V",
                           [("const", 0, 1), ("return-void",)], nregs=2, nargs=2, static=False)
        orig_invoke = vm._run_mdef

        def _spy(md, args):
            if md.cls == "Lt/OnbAct;" and md.name == "skip":
                calls.append(md.name)
            return orig_invoke(md, args)

        vm._run_mdef = _spy
        act = JObject("Lt/OnbAct;", {})
        stubs._current_activity = act
        root = ViewNode("LinearLayout")
        root.add(ViewNode("ViewPager2", vid="pager"))
        root.add(ViewNode("Button", vid="skip_btn", props={"onClick": "skip"}))
        tree.set_root(root)

        runner = LifecycleRunner(vm, with_stubs=False, stubs=stubs)
        sc = SeamConfig(auto_finish_empty_onboarding=auto_finish)
        runner.seam_config = sc
        stubs._seam_config = sc
        runner._maybe_finish_empty_onboarding()
        return calls, vm.deviations.to_list(), dbg.markers

    c_off, d_off, m_off = _run_case(False)
    assert c_off == [] and d_off == [] and m_off == []

    c_on, d_on, m_on = _run_case(True)
    assert c_on == ["skip"]
    assert any(d["kind"] == "auto_onboarding_skip" for d in d_on)
    assert any(m["kind"] == "auto_onboarding_skip" for m in m_on)


def test_z1_ffi_handler_exception_forwarded_to_vm_deviations():
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_jni_env_service import NativeJniEnvService
    from core.java.bridge import JavaToNativeBridge

    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi)

    def _boom(_cpu, _ffi):
        raise ValueError("ffi boom")

    ffi.set_handler(eng, "boom_sym", _boom)
    stub_addr = ffi.install_named_stub(eng, "boom_sym")
    # Native function calls stub_addr and returns 7:
    # stp x29,x30,[sp,#-16]!; movz x9,#lo16(stub_addr); ... simpler: branch directly via stub_addr with BLR not needed if bridge calls stub_addr directly
    # Here we test stub invocation directly via code calling x9 = stub_addr (loaded via 4 movz/movk insns)
    lo = stub_addr & 0xFFFF
    hi = (stub_addr >> 16) & 0xFFFF
    def _w(insn): return int(insn).to_bytes(4, "little")
    code = b"".join([
        _w(0xA9BF7BFD),                     # stp x29, x30, [sp, #-16]!
        _w(0xD2800009 | (lo << 5)),         # movz x9, #lo
        _w(0xF2A00009 | (hi << 5)),         # movk x9, #hi, lsl #16
        _w(0xD63F0120),                     # blr x9
        _w(0xD28000E0),                     # movz x0, #7
        _w(0xA8C17BFD),                     # ldp x29, x30, [sp], #16
        _w(0xD65F03C0),                     # ret
    ])
    svc.load_blob(eng, 0x4200, code)
    vm = DalvikVM()
    br = JavaToNativeBridge(eng, jni, emu=svc, vm=vm)
    ret = br.call_native(0x4200, ret="int")
    assert ret == 7
    assert any(d["kind"] == "ffi_exc" and d["site"] == "boom_sym" for d in vm.deviations.to_list())


def test_m3_m4_n3_controller_after_safety_run_seq_and_shared_glass_build(tmp_path):
    from ui.controllers.debugger_controller import DebuggerController

    called_in_worker = []

    class _FailingTkApp:
        class cfg:
            config = {}
            paths = {}

        def after(self, _ms, fn=None):
            raise RuntimeError("main thread not in main loop")

        def after_cancel(self, _):
            pass

        def log(self, _):
            pass

    ctrl = DebuggerController(_FailingTkApp())
    err = []

    def _worker_call():
        try:
            ctrl._after(lambda: called_in_worker.append(threading.get_ident()))
        except Exception as exc:
            err.append(exc)

    th = threading.Thread(target=_worker_call)
    th.start()
    th.join(timeout=2.0)
    assert not err
    assert called_in_worker == []

    # Headless app (app=None) still invokes inline synchronously
    ctrl_hl = DebuggerController(None)
    hl_ran = []
    ctrl_hl._after(lambda: hl_ran.append("ok"))
    assert hl_ran == ["ok"]

    # N3 + M3: _launch_glass_app_async uses shared GlassAppRunner.build and stops cleanly
    app_dir = tmp_path / "shared_app"
    (app_dir / "smali" / "t").mkdir(parents=True)
    (app_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="t.shared">'
        '<application><activity android:name=".Act"><intent-filter>'
        '<action android:name="android.intent.action.MAIN"/>'
        '<category android:name="android.intent.category.LAUNCHER"/>'
        '</intent-filter></activity></application></manifest>',
        encoding="utf-8",
    )
    (app_dir / "smali" / "t" / "Act.smali").write_text(
        '.class public Lt/shared/Act;\n'
        '.super Landroid/app/Activity;\n'
        '.method public onCreate(Landroid/os/Bundle;)V\n'
        '    .registers 2\n'
        '    return-void\n'
        '.end method\n',
        encoding="utf-8",
    )

    class _InlineApp:
        class cfg:
            config = {"APP_SOURCE_DIR": str(app_dir), "BASE_DIR": str(tmp_path)}
            paths = {}

        def after(self, _ms, fn=None):
            if callable(_ms):
                _ms()
            elif callable(fn):
                fn()
            return 1

        def after_cancel(self, _):
            pass

        def log(self, _):
            pass

    ctrl2 = DebuggerController(_InlineApp())
    ctrl2.native_fusion = False
    ctrl2.start_app()
    t0 = time.monotonic()
    while ctrl2._busy and time.monotonic() - t0 < 5.0:
        time.sleep(0.02)
    assert ctrl2._active_pkg == "t.shared"
    assert ctrl2._jdb is not None and ctrl2._jdb.mode in ("done", "paused")
    res_clr = ctrl2.clear_active_appdata()
    assert res_clr["ok"] is True and res_clr["package"] == "t.shared"


def test_collection_toarray_and_arrays_copy_preserve_eltype():
    from core.java.dalvik import DalvikVM, JArray, JObject, JString
    from core.java.dex_loader import AndroidStubs

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)

    al = JObject("Ljava/util/ArrayList;", {"__items": [JString("a"), JString("b")], "$items": [JString("a"), JString("b")]})
    target = JArray("[Ljava/lang/String;", [])
    ok, res_arr = stubs.resolve(
        "Ljava/util/List;", "toArray", "([Ljava/lang/Object;)[Ljava/lang/Object;", [al, target], False
    )
    assert ok and isinstance(res_arr, JArray)
    assert res_arr.eltype == "[Ljava/lang/String;"
    assert [s.text for s in res_arr.data] == ["a", "b"]
    vm._check_cast(res_arr, "[Ljava/lang/String;")

    st0 = JObject("Ljava/lang/StackTraceElement;", {})
    st1 = JObject("Ljava/lang/StackTraceElement;", {})
    src_st = JArray("[Ljava/lang/StackTraceElement;", [st0, st1])
    ok_r, ranged = stubs.resolve(
        "Ljava/util/Arrays;", "copyOfRange", "([Ljava/lang/Object;II)[Ljava/lang/Object;", [src_st, 1, 2], True
    )
    assert ok_r and isinstance(ranged, JArray)
    assert ranged.eltype == "[Ljava/lang/StackTraceElement;"
    assert ranged.data == [st1]
    vm._check_cast(ranged, "[Ljava/lang/StackTraceElement;")

    cls_obj = vm.intern_class("[Ljava/lang/StackTraceElement;")
    raw_obj = JArray("[Ljava/lang/Object;", [st0, st1])
    ok_c, copied = stubs.resolve(
        "Ljava/util/Arrays;",
        "copyOf",
        "([Ljava/lang/Object;ILjava/lang/Class;)[Ljava/lang/Object;",
        [raw_obj, 2, cls_obj],
        True,
    )
    assert ok_c and isinstance(copied, JArray)
    assert copied.eltype == "[Ljava/lang/StackTraceElement;"
    vm._check_cast(copied, "[Ljava/lang/StackTraceElement;")


def test_scene_graph_surface_dispatch_advances_step_budget():
    from core.java.dalvik import DalvikVM, JObject
    from core.java.surface import SceneGraphSurface
    from core.java.view_tree import ViewNode, ViewTree

    vm = DalvikVM()
    vm.register_method(
        "Lcom/demo/BtnClick;",
        "onClick",
        "(Landroid/view/View;)V",
        [
            ("const/4", 2, 0),
            ("const/4", 3, 1),
            ("add-int", 2, 2, 3),
            ("add-int", 2, 2, 3),
            ("add-int", 2, 2, 3),
            ("add-int", 2, 2, 3),
            ("add-int", 2, 2, 3),
            ("return-void",),
        ],
        nregs=4,
        nargs=2,
    )
    vm.max_steps = 1000
    vm.step_budget = 1000
    vm.steps = 998  # nur 2 Steps Restbudget vor Dispatch

    tree = ViewTree()
    root = ViewNode("FrameLayout")
    cb_obj = JObject("Lcom/demo/BtnClick;", {})
    btn = ViewNode("Button", vid="ok", text="OK", clickable=True)
    btn.on_click = cb_obj
    root.add(btn)
    tree.set_root(root)
    tree.ensure_layout()
    root.bounds = (0, 0, 420, 760)
    btn.bounds = (10, 10, 120, 44)

    surf = SceneGraphSurface(tree, vm=vm)
    assert surf.dispatch({"type": "click", "x": 30, "y": 25}) is True
    assert vm.steps >= 1006
    assert vm.step_budget >= vm.steps


def test_class_provider_and_dalvik_type_cache_invalidate_on_patch(tmp_path):
    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM

    sm = tmp_path / "smali" / "com" / "demo"
    sm.mkdir(parents=True)
    for base in ("BaseA", "BaseB"):
        (sm / f"{base}.smali").write_text(
            f".class public Lcom/demo/{base};\n.super Ljava/lang/Object;\n",
            encoding="utf-8",
        )
    sub_file = sm / "Sub.smali"
    sub_file.write_text(
        ".class public Lcom/demo/Sub;\n.super Lcom/demo/BaseA;\n",
        encoding="utf-8",
    )

    prov = StudioClassProvider(str(tmp_path))
    vm = DalvikVM()
    vm.class_provider = prov

    assert vm._superclass("Lcom/demo/Sub;") == "Lcom/demo/BaseA;"
    assert vm._is_subclass("Lcom/demo/Sub;", "Lcom/demo/BaseA;") is True
    assert prov._read_source("Lcom/demo/Missing;")[0] is None
    assert "Lcom/demo/Missing;" in prov._source_cache

    prov.set_patch(
        "smali/com/demo/Sub.smali",
        ".class public Lcom/demo/Sub;\n.super Lcom/demo/BaseB;\n",
    )
    assert vm._superclass("Lcom/demo/Sub;") == "Lcom/demo/BaseB;"
    assert vm._is_subclass("Lcom/demo/Sub;", "Lcom/demo/BaseB;") is True
    assert vm._is_subclass("Lcom/demo/Sub;", "Lcom/demo/BaseA;") is False

    prov.clear_patches()
    assert vm._superclass("Lcom/demo/Sub;") == "Lcom/demo/BaseA;"
    prov.patches["Lcom/demo/Sub;"] = ".class public Lcom/demo/Sub;\n.super Lcom/demo/BaseB;\n"
    assert vm._superclass("Lcom/demo/Sub;") == "Lcom/demo/BaseB;"
