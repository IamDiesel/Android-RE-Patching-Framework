# -*- coding: utf-8 -*-
"""AP2.1 Naht 1+2: DexGuard-char[]-String-Decrypt + reflektives Runtime.nativeLoad-Routing.
Naht 1: new String(char[]) / intern / toCharArray / normalisierte String-Methoden auf
konstruierten String-JObjects. Naht 2: reflektiver Aufruf Runtime.nativeLoad -> Fusion-Pfad-Loader.
Hermetisch."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JObject, JArray


def _mk():
    vm = DalvikVM(); st = AndroidStubs(vm=vm)
    return vm, st


def _call(st, cls, n, sig, args):
    ok, r = st.resolve(cls, n, sig, args, static=False); assert ok, (cls, n, sig); return r


# ---- Naht 1: char[]-String-Decrypt ----
def test_new_string_from_chararray():
    vm, st = _mk()
    this = JObject("Ljava/lang/String;", {})
    _call(st, "Ljava/lang/String;", "<init>", "([C)V",
          [this, JArray("[C", [ord(c) for c in "nativeLoad"])])
    assert st._txt(this) == "nativeLoad"
    # char[] mit offset/count
    this2 = JObject("Ljava/lang/String;", {})
    _call(st, "Ljava/lang/String;", "<init>", "([CII)V",
          [this2, JArray("[C", [ord(c) for c in "XXnativeLoadYY"]), 2, 10])
    assert st._txt(this2) == "nativeLoad"


def test_constructed_string_methods_match_jstring():
    vm, st = _mk()
    this = JObject("Ljava/lang/String;", {})
    _call(st, "Ljava/lang/String;", "<init>", "([C)V",
          [this, JArray("[C", [ord(c) for c in "java.lang.Runtime"])])
    assert _call(st, "Ljava/lang/String;", "length", "()I", [this]) == 17
    assert _call(st, "Ljava/lang/String;", "equals", "(Ljava/lang/Object;)Z",
                 [this, JString("java.lang.Runtime")]) == 1
    # Java-String.hashCode (deterministisch, = echtes Android)
    h = 0
    for ch in "java.lang.Runtime": h = (31 * h + ord(ch)) & 0xFFFFFFFF
    ref = h - 0x100000000 if h >= 0x80000000 else h
    assert _call(st, "Ljava/lang/String;", "hashCode", "()I", [this]) == ref
    # intern -> echte JString
    it = _call(st, "Ljava/lang/String;", "intern", "()Ljava/lang/String;", [this])
    assert isinstance(it, JString) and it.text == "java.lang.Runtime"
    # toCharArray roundtrip
    ca = _call(st, "Ljava/lang/String;", "toCharArray", "()[C", [it])
    assert "".join(chr(c) for c in ca.data) == "java.lang.Runtime"


def test_reflective_string_ctor_chararray():
    vm, st = _mk()
    # _ctor_newinstance mit [C statt [B
    ctor = JObject("Ljava/lang/reflect/Constructor;",
                   {"owner": JObject("Ljava/lang/Class;", {"name": "java.lang.String"})})
    r = st._ctor_newinstance([ctor, JArray("[Ljava/lang/Object;",
                                           [JArray("[C", [ord(c) for c in "forName"])])])
    assert isinstance(r, JString) and r.text == "forName"


# ---- Naht 2: reflektives Runtime.nativeLoad -> Fusion-Pfad-Loader ----
def test_runtime_getruntime_singleton():
    vm, st = _mk()
    r1 = _call(st, "Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", [])
    r2 = _call(st, "Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", [])
    assert isinstance(r1, JObject) and r1 is r2 and r1.cls == "Ljava/lang/Runtime;"


def test_reflective_nativeload_routes_to_fusion_path():
    vm, st = _mk()
    loaded = []
    vm._fusion_load_path = lambda p: loaded.append(p)
    runtime = _call(st, "Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", [])
    # Method-Objekt wie aus Runtime.class.getDeclaredMethod("nativeLoad", ...)
    method = _call(st, "Ljava/lang/Class;", "getDeclaredMethod",
                   "(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;",
                   [JObject("Ljava/lang/Class;", {"name": "java.lang.Runtime"}),
                    JString("nativeLoad"), JArray("[Ljava/lang/Class;", [])])
    # method.invoke(runtime, "/data/app/x/lib/arm64/libfoo.so", null)
    ret = st._method_invoke([method, runtime,
                             JArray("[Ljava/lang/Object;",
                                    [JString("/data/app/x/lib/arm64/libfoo.so"), None])])
    assert ret is None                               # null = kein UnsatisfiedLinkError
    assert loaded == ["/data/app/x/lib/arm64/libfoo.so"]


def test_reflective_nativeload_null_without_hook():
    vm, st = _mk()   # kein _fusion_load_path -> No-Op, kein Fehler
    runtime = _call(st, "Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", [])
    method = _call(st, "Ljava/lang/Class;", "getDeclaredMethod",
                   "(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;",
                   [JObject("Ljava/lang/Class;", {"name": "java.lang.Runtime"}),
                    JString("nativeLoad"), JArray("[Ljava/lang/Class;", [])])
    assert st._method_invoke([method, runtime,
                              JArray("[Ljava/lang/Object;", [JString("/x/lib.so"), None])]) is None


def test_system_load_routes_to_fusion_path():
    vm, st = _mk()
    loaded = []
    vm._fusion_load_path = lambda p: loaded.append(p)
    _call(st, "Ljava/lang/System;", "load", "(Ljava/lang/String;)V", [JString("/x/lib/libY.so")])
    assert loaded == ["/x/lib/libY.so"]


def test_system_loadlibrary_routes_to_fusion_name():
    vm, st = _mk()
    names = []
    vm._fusion_load_library = lambda n: names.append(n)
    _call(st, "Ljava/lang/System;", "loadLibrary", "(Ljava/lang/String;)V", [JString("hermes")])
    assert names == ["hermes"]


# ---- Naht 3: Merged-SO statische Java_-Symbol-Ernte + Name-Fallback ----
def test_demangle_jni_merged_so():
    from core.java.fusion_autoload import _demangle_jni
    assert _demangle_jni("Java_com_facebook_react_soloader_OpenSourceMergedSoMapping_libreactnative_1so") == \
        ("Lcom/facebook/react/soloader/OpenSourceMergedSoMapping;", "libreactnative_so")
    assert _demangle_jni("Java_com_example_Foo_bar") == ("Lcom/example/Foo;", "bar")
    assert _demangle_jni("not_a_jni") is None


def test_table_name_fallback_lookup():
    from core.java.native_link import NativeMethodTable
    t = NativeMethodTable()
    t.register_static_symbol("Lp/C;", "m", 0x4000, ret="int")
    # Name-Fallback greift auch bei unbekannter sig
    assert t.lookup("Lp/C;", "m", "()I")["fnptr"] == 0x4000
    assert t.lookup("Lp/C;", "m", "(LX;)V")["fnptr"] == 0x4000
    assert t.lookup("Lp/C;", "other", "()I") is None
    # exakte Registrierung gewinnt
    t.register("Lp/C;", "m", "()I", 0x5000)
    assert t.lookup("Lp/C;", "m", "()I")["fnptr"] == 0x5000
