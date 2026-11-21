# -*- coding: utf-8 -*-
"""Generische Reflection in der DalvikVM: Class.forName/getMethod + Method.invoke (+ Boxing).
Zielagnostisch (synthetische Klasse), kein App-Bezug."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime

REFL = r'''
.class public Lgen/Refl;
.super Ljava/lang/Object;
.method public static secret()I
    .locals 1
    const/16 v0, 0x2a
    return v0
.end method
.method public static run()I
    .locals 5
    const-string v0, "gen.Refl"
    invoke-static {v0}, Ljava/lang/Class;->forName(Ljava/lang/String;)Ljava/lang/Class;
    move-result-object v0
    const-string v1, "secret"
    const/4 v2, 0x0
    new-array v2, v2, [Ljava/lang/Class;
    invoke-virtual {v0, v1, v2}, Ljava/lang/Class;->getMethod(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;
    move-result-object v0
    const/4 v2, 0x0
    new-array v2, v2, [Ljava/lang/Object;
    const/4 v3, 0x0
    invoke-virtual {v0, v3, v2}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Ljava/lang/Integer;
    invoke-virtual {v0}, Ljava/lang/Integer;->intValue()I
    move-result v0
    return v0
.end method
'''


def _vm_with(smali_text):
    vm = DalvikVM(max_depth=64)
    _cls, mds = Sm.parse_class(smali_text)
    for md in mds:
        vm.registry[(md.cls, md.name, md.sig)] = md
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    return vm


def test_reflection_static_invoke_with_boxing():
    vm = _vm_with(REFL)
    res = vm.invoke("Lgen/Refl;", "run", "()I", ())
    assert res == 42


def test_reflection_missing_method_absorbed():
    # Unbekannte/Framework-Methode reflektiv -> 0 (kein Crash)
    vm = _vm_with(REFL)
    stubs = vm.runtime.stubs
    cls = stubs._cls_forname([__import__("core.java.objects", fromlist=["JString"]).JString("java.lang.Nope")])
    meth = stubs._cls_getmethod([cls, __import__("core.java.objects", fromlist=["JString"]).JString("ghost"), None])
    assert stubs._method_invoke([meth, None, None]) == 0


def test_step_budget_bounds_run():
    vm = _vm_with(REFL)
    vm.step_budget = 3   # sehr klein -> muss abbrechen
    try:
        vm.invoke("Lgen/Refl;", "run", "()I", ())
        assert False, "Step-Budget haette greifen muessen"
    except Exception as e:
        assert "StepBudget" in repr(e)
