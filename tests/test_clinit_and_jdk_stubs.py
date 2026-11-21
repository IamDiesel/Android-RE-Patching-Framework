# -*- coding: utf-8 -*-
"""Tor-2-Enabler: Dalvik <clinit>-on-first-use (Klasseninit bei Erstnutzung) + JDK-Laufzeit-Stubs
(String<->Bytes, System.arraycopy), die der app-eigene Obfuskator-String-Decrypt braucht.
Faithful: wir fuehren den app-eigenen Initializer aus und modellieren JDK-Semantik - kein Faken."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JArray, JObject


def test_clinit_runs_before_first_method_use():
    vm = DalvikVM()
    vm.register_method("Lt;", "<clinit>", "()V",
                       [("const", 0, 7), ("sput", 0, "Lt;", "$g"), ("return-void",)],
                       nregs=1, nargs=0, static=True)
    vm.register_method("Lt;", "getG", "()I",
                       [("sget", 0, "Lt;", "$g"), ("return", 0)], nregs=1, nargs=1, static=True)
    assert vm.invoke("Lt;", "getG", "()I", ()) == 7      # <clinit> lief bei Erstnutzung
    assert "Lt;" in vm._clinit_done


def test_clinit_sets_static_avoids_divzero_trap():
    """Opaque-Predicate-Falle wie bei LLU: Methode dividiert durch einen Static, den <clinit> setzt.
    Ohne Klasseninit -> divide by zero; mit <clinit>-on-first-use -> benigner Pfad."""
    vm = DalvikVM()
    vm.register_method("Lo;", "<clinit>", "()V",
                       [("const", 0, 5), ("sput", 0, "Lo;", "$g"), ("return-void",)],
                       nregs=1, nargs=0, static=True)
    vm.register_method("Lo;", "trap", "()I",
                       [("const", 0, 10), ("sget", 1, "Lo;", "$g"),
                        ("div-int", 2, 0, 1), ("return", 2)], nregs=3, nargs=1, static=True)
    assert vm.invoke("Lo;", "trap", "()I", ()) == 2      # 10/5, keine ArithmeticException


def test_clinit_runs_once_only():
    vm = DalvikVM()
    vm.register_method("Lc;", "<clinit>", "()V",
                       [("sget", 0, "Lc;", "n"), ("add-int/lit", 0, 0, 1),
                        ("sput", 0, "Lc;", "n"), ("return-void",)], nregs=1, nargs=0, static=True)
    vm.register_method("Lc;", "ping", "()V", [("return-void",)], nregs=1, nargs=1, static=True)
    vm.invoke("Lc;", "ping", "()V", ())
    vm.invoke("Lc;", "ping", "()V", ())
    assert vm.statics.get("Lc;", {}).get("n") == 1       # genau einmal initialisiert


def test_jdk_string_bytes_and_arraycopy():
    st = AndroidStubs()
    h, arr = st.resolve("Ljava/lang/String;", "getBytes", "(Ljava/lang/String;)[B",
                        [JString("AB"), JString("ISO-8859-1")], False)
    assert h and isinstance(arr, JArray) and arr.data == [65, 66]

    this = JObject("Ljava/lang/String;")
    st.resolve("Ljava/lang/String;", "<init>", "([BLjava/lang/String;)V",
               [this, JArray("[B", [72, 105]), JString("ISO-8859-1")], False)
    assert this.fields.get("text") == "Hi"               # konstruierter String traegt Text

    this2 = JObject("Ljava/lang/String;")
    st.resolve("Ljava/lang/String;", "<init>", "([BI)V",
               [this2, JArray("[B", [65, 66, 67]), 0], False)   # deprecated hibyte=0 -> Latin-1
    assert this2.fields.get("text") == "ABC"

    src = JArray("[B", [1, 2, 3, 4]); dst = JArray("[B", [0, 0, 0, 0])
    st.resolve("Ljava/lang/System;", "arraycopy", "(Ljava/lang/Object;ILjava/lang/Object;II)V",
               [src, 1, dst, 0, 2], True)
    assert dst.data[:2] == [2, 3]


def test_map_interface_get_fallback():
    st = AndroidStubs()
    m = JObject("Ljava/util/HashMap;", {"map": {5: JString("ok")}})
    h, v = st.resolve("Ljava/util/Map;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", [m, 5], False)
    assert h and isinstance(v, JString) and v.text == "ok"
