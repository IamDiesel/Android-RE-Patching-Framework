# -*- coding: utf-8 -*-
"""Fix 3: Wide-Stub-Args, Superklassen-Walk im Dispatch, instance-of ueber Hierarchie."""
import pytest
from core.java.dalvik import DalvikVM
from core.java.smali import parse_class, load_into
from core.java.objects import JObject, JString, JArray


def test_logical_args_collapse_wide_for_stub():
    # Stub Foo.bar(JI)V muss (long, int) als 2 logische Args sehen, nicht 3 Slots
    seen = {}
    class RT:
        def resolve_invoke(self, cls, name, sig, args, static):
            if name == "bar":
                seen["args"] = list(args); return True, None
            return False, None
    vm = DalvikVM(); vm.runtime = RT()
    # invoke-static {v0,v1,v2}, Foo.bar(JI)V ; v0=long(0x7), v1=junk, v2=int(5)
    vm.run_method([("const", 0, 0x7), ("const", 1, 0xdead), ("const", 2, 5),
                   ("invoke-static", (0, 1, 2), "LFoo;", "bar", "(JI)V"), ("return-void",)], 3)
    assert seen["args"] == [0x7, 5]       # Zweit-Slot (v1) uebersprungen


def test_slots_from_logical_roundtrip():
    vm = DalvikVM()
    # wide-Param-Methode via invoke: logische Args -> korrekte Slot-Platzierung
    src = (".class public LW;\n.super Ljava/lang/Object;\n"
           ".method public static f(JI)I\n.registers 5\n"   # p0:p1=long, p2=int -> v2,v3,v4
           "add-long/2addr p0, p0\n"   # nur Platzhalter-Logik; wir lesen p2
           "move v0, p2\nreturn v0\n.end method\n")
    for md in parse_class(src)[1]:
        vm.register(md)
    assert vm.invoke("LW;", "f", "(JI)I", (0x100000000, 42)) == 42   # int-Param korrekt bei p2


def test_superclass_walk_dispatch():
    # Methode nur in Superklasse definiert -> virtueller Aufruf findet sie
    base = (".class public LBase;\n.super Ljava/lang/Object;\n"
            ".method public greet()I\n.registers 2\nconst/16 v0, 0x63\nreturn v0\n.end method\n")
    sub = (".class public LSub;\n.super LBase;\n"
           ".method public constructor <init>()V\n.registers 1\nreturn-void\n.end method\n")
    caller = (".method public static run()I\n.registers 2\n"
              "new-instance v0, LSub;\ninvoke-direct {v0}, LSub;-><init>()V\n"
              "invoke-virtual {v0}, LSub;->greet()I\nmove-result v1\nreturn v1\n.end method\n")
    class P:
        def __init__(self):
            self.src = {"LBase;": base, "LSub;": sub}
        def get_class(self, d):
            return parse_class(self.src[d])[1] if d in self.src else None
        def get_super(self, d):
            return {"LSub;": "LBase;", "LBase;": "Ljava/lang/Object;"}.get(d)
    vm = DalvikVM(); vm.class_provider = P()
    load_into(vm, caller)
    assert vm.invoke("Lsmali/Adhoc;", "run", "()I", ()) == 0x63


def test_instance_of_jstring_jarray_and_super():
    vm = DalvikVM()
    assert vm._instance_of(JString("x"), "Ljava/lang/String;") == 1
    assert vm._instance_of(JString("x"), "Ljava/lang/CharSequence;") == 1
    assert vm._instance_of(JString("x"), "Ljava/lang/Integer;") == 0
    assert vm._instance_of(JArray("[I", [0]), "Ljava/lang/Object;") == 1
    assert vm._instance_of(JObject("Ljava/lang/NullPointerException;"), "Ljava/lang/RuntimeException;") == 1
    assert vm._instance_of(JObject("Ljava/lang/Error;"), "Ljava/lang/Exception;") == 0
    assert vm._instance_of(0, "Ljava/lang/Object;") == 0      # null


def test_ensure_class_init_does_not_swallow_budget():
    # clinit, das das StepBudget sprengt -> propagiert (wird NICHT zu _clinit_errors)
    src = (".class public LLoop;\n.super Ljava/lang/Object;\n"
           ".method public static constructor <clinit>()V\n.registers 1\n"
           ":l\ngoto :l\n.end method\n")
    class P:
        def get_class(self, d): return parse_class(src)[1] if d == "LLoop;" else None
        def get_super(self, d): return None
    vm = DalvikVM(); vm.class_provider = P(); vm.step_budget = 5000
    from core.java.dalvik import DalvikException
    with pytest.raises(DalvikException):
        vm._ensure_class_init("LLoop;")
    assert not any(e.get("cls") == "LLoop;" for e in vm._clinit_errors)
