# -*- coding: utf-8 -*-
"""Tests fuer MS-4 / 4c: erweiterte android.*/java.*-Stubs, Collections, VirtualFS, Framework-Statics."""
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.runtime import JavaRuntime
from core.java.dex_loader import AndroidStubs, VirtualFS
from core.java.objects import JString, JObject


def _rt():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False); jni = NativeJniEnvService(publish=False)
    jni.install(eng, ffi, set_entry_reg=None)
    return JavaRuntime(eng, jni)


def test_string_methods():
    st = AndroidStubs()
    _, eq = st.resolve("Ljava/lang/String;", "equals", "(Ljava/lang/Object;)Z",
                       [JString("ab"), JString("ab")], False)
    assert eq == 1
    _, sub = st.resolve("Ljava/lang/String;", "substring", "(II)Ljava/lang/String;",
                        [JString("hallo"), 1, 3], False)
    assert isinstance(sub, JString) and sub.text == "al"
    _, sw = st.resolve("Ljava/lang/String;", "startsWith", "(Ljava/lang/String;)Z",
                       [JString("hallo"), JString("ha")], False)
    assert sw == 1


def test_collections():
    st = AndroidStubs()
    lst = JObject("Ljava/util/ArrayList;")
    st.resolve("Ljava/util/ArrayList;", "<init>", "()V", [lst], False)
    st.resolve("Ljava/util/ArrayList;", "add", "(Ljava/lang/Object;)Z", [lst, JString("x")], False)
    st.resolve("Ljava/util/ArrayList;", "add", "(Ljava/lang/Object;)Z", [lst, JString("y")], False)
    _, sz = st.resolve("Ljava/util/ArrayList;", "size", "()I", [lst], False)
    assert sz == 2
    _, g = st.resolve("Ljava/util/ArrayList;", "get", "(I)Ljava/lang/Object;", [lst, 1], False)
    assert isinstance(g, JString) and g.text == "y"

    m = JObject("Ljava/util/HashMap;")
    st.resolve("Ljava/util/HashMap;", "<init>", "()V", [m], False)
    st.resolve("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
               [m, JString("k"), 42], False)
    _, val = st.resolve("Ljava/util/HashMap;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;",
                        [m, JString("k")], False)
    assert val == 42


def test_shared_prefs_default():
    st = AndroidStubs()
    _, v = st.resolve("Landroid/content/SharedPreferences;", "getInt", "(Ljava/lang/String;I)I",
                      [JObject("Lp;"), JString("key"), 7], False)
    assert v == 7


def test_virtualfs():
    vfs = VirtualFS()
    vfs.mount("config/x.json", b'{"a":1}')
    assert vfs.exists("config/x.json")
    assert vfs.read("config/x.json") == b'{"a":1}'
    assert vfs.list("config/") == ["config/x.json"]


def test_runtime_seeds_build_statics():
    rt = _rt()
    # sget-object Landroid/os/Build;->MODEL ueber eine Dalvik-Methode
    rt.register_method("Lx;", "model", "()Ljava/lang/String;",
                       [("sget-object", 0, "Landroid/os/Build;", "MODEL"), ("return-object", 0)],
                       nregs=1, nargs=0, static=True)
    r = rt.call("Lx;", "model", "()Ljava/lang/String;", ())
    assert isinstance(r, JString) and r.text == "Pixel 9 Pro"
