# -*- coding: utf-8 -*-
"""P1 Gläserner App-Lauf: der Debugger treibt den ganzen Lauf (run_fn) mit View-Recorder am selben VM;
ein Breakpoint in einem Callee zündet mitten im Lauf -> Pause, der View-Baum ist da. Headless."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.view_tree import install_view_stubs
from core.java.debug import JavaDebugger
from core.java.objects import JObject

ACT = """.class public Lt/Act;
.super Ljava/lang/Object;
.method public onCreate(Landroid/os/Bundle;)V
    .registers 4
    new-instance v0, Landroid/widget/LinearLayout;
    invoke-direct {v0}, Landroid/widget/LinearLayout;-><init>()V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1}, Landroid/widget/TextView;-><init>()V
    const-string v2, "Hallo"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/String;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    invoke-virtual {p0, v0}, Lt/Act;->setContentView(Landroid/view/View;)V
    invoke-direct {p0}, Lt/Act;->work()V
    return-void
.end method
.method private work()V
    .registers 1
    const v0, 0x5
    return-void
.end method
"""


def _glass_vm():
    vm = DalvikVM(); vm.lenient = True
    for md in Sm.parse_class(ACT)[1]:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    try: stubs.seed_framework_statics(vm)
    except Exception: pass
    tree = install_view_stubs(stubs)          # View-Recorder am selben VM
    vm.runtime = _StubRuntime(stubs)
    return vm, tree


def test_breakpoint_fires_midrun_and_view_recorded():
    vm, tree = _glass_vm()
    jdb = JavaDebugger(vm)
    jdb.add_breakpoint("Lt/Act;", "work", "()V", 0)   # BP im Callee
    act = JObject("Lt/Act;")
    st = jdb.start_app(lambda: vm.invoke("Lt/Act;", "onCreate", "(Landroid/os/Bundle;)V", (act, None)),
                       stop_at_entry=False)
    assert st["mode"] == "paused"                      # am BP angehalten, MITTEN im Lauf
    # Der View-Baum wurde VOR dem BP aufgebaut (setContentView in onCreate)
    assert tree.root is not None
    texts = [n.text for n in tree.nodes() if n.text]
    assert "Hallo" in texts
    jdb.cont()
    assert jdb.mode in ("done", "stopped")


def test_start_app_stop_at_entry():
    vm, tree = _glass_vm()
    jdb = JavaDebugger(vm)
    act = JObject("Lt/Act;")
    st = jdb.start_app(lambda: vm.invoke("Lt/Act;", "onCreate", "(Landroid/os/Bundle;)V", (act, None)),
                       stop_at_entry=True)
    assert st["mode"] == "paused"                      # haelt an der 1. Instruktion
    jdb.cont()
    assert jdb.mode in ("done", "stopped")
    assert tree.root is not None                        # Lauf hat die UI gebaut
