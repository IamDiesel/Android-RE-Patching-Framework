# -*- coding: utf-8 -*-
"""Tests fuer Paket 2: Generische Reflection, Unboxing, Wrapper.TYPE, TextUtils & DataInputStream."""
from __future__ import annotations

from types import SimpleNamespace

from core.java import smali as Sm
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.fs_bridge import install_fs_stubs
from core.java.lifecycle import _StubRuntime
from core.java.objects import JArray, JObject, JString
from core.native.vfs import VFS


def test_primitive_type_statics_and_reflection_unboxing():
    code = """
.class public Lt/Calc;
.super Ljava/lang/Object;
.method public static combine(CII)I
    .locals 1
    add-int/2addr p1, p2
    add-int/2addr p0, p1
    return p0
.end method
"""
    vm = DalvikVM()
    _, mds = Sm.parse_class(code)
    for md in mds:
        vm.registry[(md.cls, md.name, md.sig)] = md
    stubs = AndroidStubs(vm=vm)
    vm.runtime = _StubRuntime(stubs)
    stubs.seed_framework_statics(vm)

    c_type = vm._sget("Ljava/lang/Character;", "TYPE")
    i_type = vm._sget("Ljava/lang/Integer;", "TYPE")
    assert isinstance(c_type, JObject) and c_type.fields.get("primitive") == "C"
    assert isinstance(i_type, JObject) and i_type.fields.get("primitive") == "I"

    cls_obj = stubs._cls_forname([JString("t.Calc")])
    meth = stubs._cls_getmethod([cls_obj, JString("combine"), JArray("[Ljava/lang/Class;", [c_type, i_type, i_type])])
    args = JArray("[Ljava/lang/Object;", [
        JObject("Ljava/lang/Character;", {"value": 65}),
        JObject("Ljava/lang/Integer;", {"value": 10}),
        JObject("Ljava/lang/Integer;", {"value": 5}),
    ])
    # Static call mit obj=0 (Dalvik null) darf NICHT obj als erstes Argument uebergeben!
    res = stubs._method_invoke([meth, 0, args])
    assert isinstance(res, JObject) and res.cls == "Ljava/lang/Integer;"
    assert res.fields["value"] == 80


def test_framework_reflection_overloads_boxing_and_ctors():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    fs = VFS()
    eng = SimpleNamespace(_os=SimpleNamespace(fs=fs))
    install_fs_stubs(stubs, eng, pkg="com.demo.pkg")
    vm.runtime = _StubRuntime(stubs)

    # String.lastIndexOf(int) + String.substring(int, int) via reflection
    i_type = vm._sget("Ljava/lang/Integer;", "TYPE")
    str_cls = stubs._cls_forname([JString("java.lang.String")])
    m_lio = stubs._cls_getmethod([str_cls, JString("lastIndexOf"), JArray("[Ljava/lang/Class;", [i_type])])
    res_lio = stubs._method_invoke([m_lio, JString("/a/b/c.so"), JArray("[Ljava/lang/Object;", [JObject("Ljava/lang/Integer;", {"value": ord("/")})])])
    assert isinstance(res_lio, JObject) and res_lio.cls == "Ljava/lang/Integer;"
    assert res_lio.fields["value"] == 4

    m_sub = stubs._cls_getmethod([str_cls, JString("substring"), JArray("[Ljava/lang/Class;", [i_type, i_type])])
    res_sub = stubs._method_invoke([m_sub, JString("abcdef"), JArray("[Ljava/lang/Object;", [JObject("Ljava/lang/Integer;", {"value": 1}), JObject("Ljava/lang/Integer;", {"value": 4})])])
    assert isinstance(res_sub, JString) and res_sub.text == "bcd"

    # File Constructor.newInstance + canWrite() boxed as Boolean
    file_cls = stubs._cls_forname([JString("java.io.File")])
    s_cls = stubs._cls_forname([JString("java.lang.String")])
    ctor = stubs._cls_getctor([file_cls, JArray("[Ljava/lang/Class;", [s_cls])])
    fs.add_file("/data/data/com.demo.pkg/files/test.bin", b"ok")
    fobj = stubs._ctor_newinstance([ctor, JArray("[Ljava/lang/Object;", [JString("/data/data/com.demo.pkg/files/test.bin")])])
    assert isinstance(fobj, JObject) and fobj.fields.get("path") == "/data/data/com.demo.pkg/files/test.bin"
    m_cw = stubs._cls_getmethod([file_cls, JString("canWrite"), JArray("[Ljava/lang/Class;", [])])
    res_cw = stubs._method_invoke([m_cw, fobj, JArray("[Ljava/lang/Object;", [])])
    assert isinstance(res_cw, JObject) and res_cw.cls == "Ljava/lang/Boolean;"
    assert res_cw.fields["value"] == 1

    # Array.newInstance(Byte.TYPE, 4)
    b_type = vm._sget("Ljava/lang/Byte;", "TYPE")
    arr = stubs._reflect_array_new([b_type, JObject("Ljava/lang/Integer;", {"value": 4})])
    assert isinstance(arr, JArray) and arr.eltype == "[B" and len(arr.data) == 4


def test_textutils_clamping_and_datainputstream():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    fs = VFS()
    eng = SimpleNamespace(_os=SimpleNamespace(fs=fs))
    install_fs_stubs(stubs, eng, pkg="com.demo.pkg")

    # TextUtils.getOffsetAfter("", 0) == 0 (nicht 1!)
    _, r_after = stubs.resolve("Landroid/text/TextUtils;", "getOffsetAfter", "(Ljava/lang/CharSequence;I)I", [JString(""), 0], True)
    assert r_after == 0
    _, r_idx = stubs.resolve("Landroid/text/TextUtils;", "indexOf", "(Ljava/lang/CharSequence;C)I", [JString("hello"), ord("e")], True)
    assert r_idx == 1

    # DataInputStream readInt / readUnsignedShort / readFully
    bis = JObject("Ljava/io/ByteArrayInputStream;", {})
    payload = bytes([0x00, 0x00, 0x01, 0x02, 0xAA, 0xBB, 0x11, 0x22, 0x33])
    stubs.resolve("Ljava/io/ByteArrayInputStream;", "<init>", "([B)V", [bis, JArray("[B", list(payload))], False)
    dis = JObject("Ljava/io/DataInputStream;", {})
    stubs.resolve("Ljava/io/DataInputStream;", "<init>", "(Ljava/io/InputStream;)V", [dis, bis], False)
    _, iv = stubs.resolve("Ljava/io/DataInputStream;", "readInt", "()I", [dis], False)
    assert iv == 0x0102
    _, usv = stubs.resolve("Ljava/io/DataInputStream;", "readUnsignedShort", "()I", [dis], False)
    assert usv == 0xAABB
    out = JArray("[B", [0, 0, 0])
    stubs.resolve("Ljava/io/DataInputStream;", "readFully", "([B)V", [dis, out], False)
    assert out.data == [0x11, 0x22, 0x33]


def test_runtime_load_two_arg_reflected():
    vm = DalvikVM()
    loaded = []
    vm._fusion_load_path = lambda p: loaded.append(p)
    stubs = AndroidStubs(vm=vm)
    rt = stubs._runtime_get([])
    rt_cls = stubs._cls_forname([JString("java.lang.Runtime")])
    s_cls = stubs._cls_forname([JString("java.lang.String")])
    cl_cls = stubs._cls_forname([JString("java.lang.ClassLoader")])
    m = stubs._cls_getmethod([rt_cls, JString("load"), JArray("[Ljava/lang/Class;", [s_cls, cl_cls])])
    stubs._method_invoke([m, rt, JArray("[Ljava/lang/Object;", [JString("/data/local/tmp/libfoo.so"), JObject("Ljava/lang/ClassLoader;", {})])])
    assert loaded == ["/data/local/tmp/libfoo.so"]
