# -*- coding: utf-8 -*-
"""AP 1.3: Collection/Iterator/Atomic-Stubs + Application/Thread-Accessoren (AndroidX-Lifecycle)."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString, JArray


def _st():
    return AndroidStubs(vm=DalvikVM())


def _call(st, cls, name, sig, args):
    h, r = st.resolve(cls, name, sig, args, False)
    return r


def test_set_dedup_and_iterator():
    st = _st()
    s = JObject("Ljava/util/concurrent/CopyOnWriteArraySet;")
    _call(st, s.cls, "<init>", "()V", [s])
    for e in ("a", "a", "b"):
        _call(st, s.cls, "add", "(Ljava/lang/Object;)Z", [s, JString(e)])
    assert _call(st, s.cls, "size", "()I", [s]) == 2
    it = _call(st, s.cls, "iterator", "()Ljava/util/Iterator;", [s])
    seen = []
    while _call(st, "Ljava/util/Iterator;", "hasNext", "()Z", [it]):
        seen.append(_call(st, "Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [it]).text)
    assert seen == ["a", "b"]


def test_arraylist_add_get_remove():
    st = _st()
    l = JObject("Ljava/util/ArrayList;")
    _call(st, l.cls, "<init>", "()V", [l])
    _call(st, l.cls, "add", "(Ljava/lang/Object;)Z", [l, JString("x")])
    _call(st, l.cls, "add", "(Ljava/lang/Object;)Z", [l, JString("x")])   # List erlaubt Duplikat
    assert _call(st, l.cls, "size", "()I", [l]) == 2
    assert _call(st, l.cls, "get", "(I)Ljava/lang/Object;", [l, 0]).text == "x"
    _call(st, l.cls, "remove", "(Ljava/lang/Object;)Z", [l, JString("x")])
    assert _call(st, l.cls, "size", "()I", [l]) == 1


def test_atomic_integer():
    st = _st()
    ai = JObject("Ljava/util/concurrent/atomic/AtomicInteger;")
    _call(st, ai.cls, "<init>", "(I)V", [ai, 5])
    assert _call(st, ai.cls, "getAndIncrement", "()I", [ai]) == 5
    assert _call(st, ai.cls, "get", "()I", [ai]) == 6
    assert _call(st, ai.cls, "compareAndSet", "(II)Z", [ai, 6, 10]) == 1
    assert _call(st, ai.cls, "get", "()I", [ai]) == 10


def test_atomic_boolean_and_reference():
    st = _st()
    ab = JObject("Ljava/util/concurrent/atomic/AtomicBoolean;")
    _call(st, ab.cls, "<init>", "(Z)V", [ab, 1])
    assert _call(st, ab.cls, "get", "()Z", [ab]) == 1
    ar = JObject("Ljava/util/concurrent/atomic/AtomicReference;")
    _call(st, ar.cls, "<init>", "()V", [ar])
    o = JObject("Lfoo;")
    _call(st, ar.cls, "set", "(Ljava/lang/Object;)V", [ar, o])
    assert _call(st, ar.cls, "get", "()Ljava/lang/Object;", [ar]) is o


def test_application_and_thread_accessors():
    st = _st()
    app = JObject("Lcom/app/MainApplication;")
    st.application = app
    # getApplication / getApplicationContext liefern die echte Instanz
    assert _call(st, "Landroid/app/Activity;", "getApplication", "()Landroid/app/Application;", [JObject("LA;")]) is app
    assert _call(st, "Landroid/app/Activity;", "getApplicationContext", "()Landroid/content/Context;", [JObject("LA;")]) is app
    # getIntent non-null mit MAIN-Action
    intent = _call(st, "Landroid/app/Activity;", "getIntent", "()Landroid/content/Intent;", [JObject("LA;")])
    assert isinstance(intent, JObject) and intent.fields.get("action") == "android.intent.action.MAIN"
    # getWindow non-null + DecorView
    win = _call(st, "Landroid/app/Activity;", "getWindow", "()Landroid/view/Window;", [JObject("LA;")])
    assert isinstance(win, JObject) and isinstance(win.fields.get("decor"), JObject)
    # Thread.currentThread == Looper.getMainLooper().getThread()  -> isMainThread true
    cur = _call(st, "Ljava/lang/Thread;", "currentThread", "()Ljava/lang/Thread;", [])
    ml = _call(st, "Landroid/os/Looper;", "getMainLooper", "()Landroid/os/Looper;", [])
    mthr = _call(st, "Landroid/os/Looper;", "getThread", "()Ljava/lang/Thread;", [ml])
    assert cur is mthr


def test_method_override_skips_smali():
    """AP 1.3: vm.method_overrides liefert feste Rueckgabe und ueberspringt den Smali-Koerper."""
    from core.java.smali import parse_class, load_into
    src = (".class public LSo;\n.super Ljava/lang/Object;\n"
           ".method public static load(Ljava/lang/String;)Z\n.registers 2\n"
           "const/4 v0, 0x0\nreturn v0\n.end method\n")   # echte Methode liefert 0
    vm = DalvikVM()
    load_into(vm, src)
    assert vm.invoke("LSo;", "load", "(Ljava/lang/String;)Z", (0,)) == 0
    vm.method_overrides[("LSo;", "load", "(Ljava/lang/String;)Z")] = 1   # Override -> 1
    # Aufruf ueber _do_invoke (invoke-static im Rahmen)
    r = vm.run_method([("const", 0, 0),
                       ("invoke-static", (0,), "LSo;", "load", "(Ljava/lang/String;)Z"),
                       ("move-result", 1), ("return", 1)], 2)
    assert r == 1       # Override greift, Smali (0) uebersprungen
