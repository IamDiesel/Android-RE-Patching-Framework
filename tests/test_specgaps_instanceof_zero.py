# -*- coding: utf-8 -*-
"""Spec-Luecken (Review 2026-11-18): exaktes Array-instance-of, konstruierte String-Interfaces,
Stub-Superklassen-Walk, _zero(0.0)."""
from core.java.dalvik import DalvikVM, _zero
from core.java.objects import JObject, JString, JArray


def test_array_instance_of_exact_not_crosstype():
    vm = DalvikVM()
    ai = JArray("[I", [0, 0]); ab = JArray("[B", [0])
    assert vm._instance_of(ai, "[I") == 1
    assert vm._instance_of(ai, "[B") == 0          # int[] ist NICHT byte[]
    assert vm._instance_of(ab, "[I") == 0
    assert vm._instance_of(ai, "Ljava/lang/Object;") == 1
    assert vm._instance_of(ai, "Ljava/lang/Cloneable;") == 1


def test_array_reference_covariance():
    # [LSub; is-a [LBase;  (Referenz-Array-Kovarianz)
    class P:
        def get_super(self, d): return {"LSub;": "LBase;", "LBase;": "Ljava/lang/Object;"}.get(d)
        def get_class(self, d): return None
    vm = DalvikVM(); vm.class_provider = P()
    arr = JArray("[LSub;", [])
    assert vm._instance_of(arr, "[LBase;") == 1
    assert vm._instance_of(arr, "[LOther;") == 0


def test_constructed_string_jobject_interfaces():
    vm = DalvikVM()
    s = JObject("Ljava/lang/String;", {"text": "hi"})     # ueber new-instance konstruiert
    for t in ("Ljava/lang/String;", "Ljava/lang/CharSequence;", "Ljava/lang/Comparable;",
              "Ljava/io/Serializable;", "Ljava/lang/Object;"):
        assert vm._instance_of(s, t) == 1, t
    assert vm._instance_of(s, "Ljava/lang/Integer;") == 0


def test_zero_handles_float():
    assert _zero(0.0) is True
    assert _zero(-0.0) is True
    assert _zero(1.5) is False
    assert _zero(0) is True
    assert _zero(None) is True


def test_if_eqz_on_float_zero():
    vm = DalvikVM()
    # 1.0 - 1.0 = 0.0 ; if-eqz -> nimmt den Zweig
    r = vm.run_method([("const", 0, 0x3f800000), ("const", 1, 0x3f800000),
                       ("sub-float", 2, 0, 1), ("if-eqz", 2, 6),
                       ("const", 3, 0x63), ("return", 3),
                       ("const", 3, 0x2a), ("return", 3)], 4)
    assert r == 0x2a       # 0.0 -> if-eqz genommen


def test_stub_superclass_walk_for_inherited():
    # App-Subklasse einer Framework-Klasse trifft geerbten Stub ueber Superklassen-Walk
    from core.java.smali import parse_class, load_into
    hit = {}
    class RT:
        def resolve_invoke(self, cls, name, sig, args, static):
            if cls == "Landroid/app/Application;" and name == "fwMethod":
                hit["cls"] = cls; return True, 7
            return False, 0
    app = (".class public Lcom/app/MyApp;\n.super Landroid/app/Application;\n"
           ".method public static run()I\n.registers 2\n"
           "new-instance v0, Lcom/app/MyApp;\n"
           "invoke-virtual {v0}, Lcom/app/MyApp;->fwMethod()I\nmove-result v1\nreturn v1\n.end method\n")
    class P:
        def get_class(self, d): return parse_class(app)[1] if d == "Lcom/app/MyApp;" else None
        def get_super(self, d): return "Landroid/app/Application;" if d == "Lcom/app/MyApp;" else None
    vm = DalvikVM(); vm.class_provider = P(); vm.runtime = RT()
    load_into(vm, app)
    assert vm.invoke("Lcom/app/MyApp;", "run", "()I", ()) == 7
    assert hit.get("cls") == "Landroid/app/Application;"
