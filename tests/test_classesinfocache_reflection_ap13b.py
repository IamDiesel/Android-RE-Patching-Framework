# -*- coding: utf-8 -*-
"""AP1.3b: ClassesInfoCache-Naht. Class/Method-Reflection-Stubs liefern korrekte,
nie-null Rueckgaben, sodass AndroidX LifecycleRegistry/ClassesInfoCache terminiert.
- getDeclaredMethods/getMethods -> leeres Method[]-JArray (nie 0/None)
- getInterfaces -> leeres Class[]-JArray
- getSuperclass -> None bei Object, sonst .super via class_provider
- getAnnotation -> None, isAnnotationPresent -> 0
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JArray


class _Prov:
    """Minimaler class_provider mit get_super (wie StudioClassProvider)."""
    supers = {"Lcom/x/Sub;": "Lcom/x/Base;", "Lcom/x/Base;": "Ljava/lang/Object;"}
    def get_class(self, d):
        return None
    def get_super(self, d):
        return self.supers.get(d)


def _mk():
    vm = DalvikVM()
    vm.class_provider = _Prov()
    stubs = AndroidStubs(vm=vm)
    return vm, stubs


def _cls(name):
    return JObject("Ljava/lang/Class;", {"name": name})


def _call(stubs, cls, name, sig, args):
    ok, ret = stubs.resolve(cls, name, sig, args, static=False)
    assert ok, (cls, name, sig)
    return ret


def test_getdeclaredmethods_empty_array_never_null():
    vm, stubs = _mk()
    r = _call(stubs, "Ljava/lang/Class;", "getDeclaredMethods",
              "()[Ljava/lang/reflect/Method;", [_cls("com.x.Sub")])
    assert isinstance(r, JArray)
    assert r.eltype == "[Ljava/lang/reflect/Method;"
    assert list(r.data) == []
    # getMethods identisch
    r2 = _call(stubs, "Ljava/lang/Class;", "getMethods",
               "()[Ljava/lang/reflect/Method;", [_cls("com.x.Sub")])
    assert isinstance(r2, JArray) and list(r2.data) == []


def test_getinterfaces_empty_class_array():
    vm, stubs = _mk()
    r = _call(stubs, "Ljava/lang/Class;", "getInterfaces",
              "()[Ljava/lang/Class;", [_cls("com.x.Sub")])
    assert isinstance(r, JArray) and r.eltype == "[Ljava/lang/Class;" and list(r.data) == []


def test_getsuperclass_walks_then_terminates_at_object():
    vm, stubs = _mk()
    sup = _call(stubs, "Ljava/lang/Class;", "getSuperclass",
                "()Ljava/lang/Class;", [_cls("com.x.Sub")])
    assert isinstance(sup, JObject) and sup.fields.get("name") == "com.x.Base"
    # Base -> Object: getSuperclass liefert Object-Class
    sup2 = _call(stubs, "Ljava/lang/Class;", "getSuperclass",
                 "()Ljava/lang/Class;", [_cls("com.x.Base")])
    assert isinstance(sup2, JObject) and sup2.fields.get("name") in ("java.lang.Object",)
    # Object selbst -> None (Rekursion terminiert)
    sup3 = _call(stubs, "Ljava/lang/Class;", "getSuperclass",
                 "()Ljava/lang/Class;", [_cls("java.lang.Object")])
    assert sup3 is None


def test_getsuperclass_unknown_returns_none():
    vm, stubs = _mk()
    sup = _call(stubs, "Ljava/lang/Class;", "getSuperclass",
                "()Ljava/lang/Class;", [_cls("com.x.Unknown")])
    assert sup is None


def test_annotation_stubs_none_and_zero():
    vm, stubs = _mk()
    ann = _call(stubs, "Ljava/lang/Class;", "getAnnotation",
                "(Ljava/lang/Class;)Ljava/lang/annotation/Annotation;",
                [_cls("com.x.Sub"), _cls("x.Ann")])
    assert ann is None
    pres = _call(stubs, "Ljava/lang/reflect/Method;", "isAnnotationPresent",
                 "(Ljava/lang/Class;)Z",
                 [JObject("Ljava/lang/reflect/Method;", {}), _cls("x.Ann")])
    assert pres == 0
