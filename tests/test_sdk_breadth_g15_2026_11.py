# -*- coding: utf-8 -*-
"""Unit & integration tests for G15 core Java toString/Enum fixes & SDK breadth."""
from __future__ import annotations

import gzip
import os
import pytest

from core.java.dalvik import DalvikException, DalvikUnknownOpcode, DalvikVM, MethodDef, VmBudgetExceeded
from core.java.deviation_journal import DebugStop
from core.java.dex_loader import AndroidStubs, VirtualFS
from core.java.fs_bridge import install_fs_stubs
from core.java.objects import JArray, JObject, JString
from core.native.vfs import VFS


def _mk_vm(with_runtime: bool = False):
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    if with_runtime:
        class _Rt:
            def __init__(self, st):
                self.stubs = st
        vm.runtime = _Rt(stubs)
    return vm, stubs


def test_enum_complete_contracts():
    vm, st = _mk_vm()

    class _EnumProv:
        def get_class(self, desc):
            return [] if desc in ("Lt/Color;", "Lt/Color$1;") else None

        def get_super(self, desc):
            return {
                "Lt/Color;": "Ljava/lang/Enum;",
                "Lt/Color$1;": "Lt/Color;",
            }.get(desc)

    vm.class_provider = _EnumProv()
    red = JObject("Lt/Color;", {"__ename": "RED", "__ordinal": 0})
    green = JObject("Lt/Color$1;", {"__ename": "GREEN", "__ordinal": 1})
    blue = JObject("Lt/Color;", {"__ename": "BLUE", "__ordinal": 2})
    vm.statics["Lt/Color;"] = {"RED": red, "GREEN": green, "BLUE": blue}

    # Enum.toString via wants hierarchy (both with superclass and without)
    assert st._obj_to_str(red) == "RED"
    assert st._obj_to_str(green) == "GREEN"
    synth = JObject("Lt/NoSuperEnum;", {"__ename": "SYNTH"})
    assert st._obj_to_str(synth) == "SYNTH"
    assert st.resolve("Lt/NoSuperEnum;", "toString", "()Ljava/lang/String;", [synth], False)[1].text == "SYNTH"

    # name, ordinal, hashCode, compareTo(Enum) & bridge compareTo(Object)
    assert st.resolve_phased(vm._build_wants(red.cls, "Ljava/lang/Enum;"), red.cls, "Ljava/lang/Enum;", "name", "()Ljava/lang/String;", [red], False)[1].text == "RED"
    assert st.resolve_phased(vm._build_wants(green.cls, "Ljava/lang/Enum;"), green.cls, "Ljava/lang/Enum;", "ordinal", "()I", [green], False)[1] == 1
    h_red = st.resolve("Ljava/lang/Enum;", "hashCode", "()I", [red], False)[1]
    assert isinstance(h_red, int)
    assert st.resolve("Ljava/lang/Enum;", "compareTo", "(Ljava/lang/Enum;)I", [red, blue], False)[1] < 0
    assert st.resolve("Ljava/lang/Enum;", "compareTo", "(Ljava/lang/Object;)I", [blue, red], False)[1] > 0

    # getDeclaringClass handles constant-specific anonymous enum body Lt/Color$1;
    ok_dc, dc_red = st.resolve("Ljava/lang/Enum;", "getDeclaringClass", "()Ljava/lang/Class;", [red], False)
    ok_dc2, dc_green = st.resolve("Ljava/lang/Enum;", "getDeclaringClass", "()Ljava/lang/Class;", [green], False)
    assert ok_dc and ok_dc2
    assert dc_red.fields.get("name") == "t.Color"
    assert dc_green.fields.get("name") == "t.Color"

    # valueOf resolves seeded static constant first, falls back cleanly otherwise
    ok_vo, res_vo = st.resolve("Ljava/lang/Enum;", "valueOf", "(Ljava/lang/Class;Ljava/lang/String;)Ljava/lang/Enum;", [dc_red, JString("BLUE")], True)
    assert ok_vo and res_vo is blue

    # clone() throws CloneNotSupportedException
    with pytest.raises(DalvikException, match="CloneNotSupportedException"):
        st.resolve("Ljava/lang/Enum;", "clone", "()Ljava/lang/Object;", [red], False)


def test_obj_to_str_smali_cycle_and_fatal_reraise():
    vm, st = _mk_vm(with_runtime=False)  # explicitly no vm.runtime set!

    class _StrProv:
        def get_class(self, desc):
            if desc == "Lt/Person;":
                return [
                    MethodDef("Lt/Person;", "toString", "()Ljava/lang/String;", [
                        ("const-string", 0, "Alice"),
                        ("return-object", 0),
                    ], nregs=2, nargs=1),
                ]
            if desc == "Lt/SelfCycle;":
                return [
                    MethodDef("Lt/SelfCycle;", "toString", "()Ljava/lang/String;", [
                        ("invoke-static", (1,), "Ljava/lang/String;", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;"),
                        ("move-result-object", 0),
                        ("return-object", 0),
                    ], nregs=2, nargs=1),
                ]
            return None

        def get_super(self, desc):
            return "Ljava/lang/Object;"

    vm.class_provider = _StrProv()

    # Smali override works without vm.runtime
    alice = JObject("Lt/Person;", {})
    assert st._obj_to_str(alice) == "Alice"
    assert st.resolve("Ljava/lang/String;", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;", [alice], True)[1].text == "Alice"

    # Null, String, StringBuilder, StringBuffer, String.format
    assert st.resolve("Ljava/lang/String;", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;", [None], True)[1].text == "null"
    assert st.resolve("Ljava/lang/String;", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;", [0], True)[1].text == "null"
    assert st.resolve("Ljava/lang/String;", "valueOf", "(I)Ljava/lang/String;", [0], True)[1].text == "0"

    sb = JObject("Ljava/lang/StringBuilder;", {})
    st.resolve("Ljava/lang/StringBuilder;", "<init>", "()V", [sb], False)
    st.resolve("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;", [sb, 0], False)
    st.resolve("Ljava/lang/StringBuilder;", "append", "(I)Ljava/lang/StringBuilder;", [sb, 0], False)
    st.resolve("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/Object;)Ljava/lang/StringBuilder;", [sb, alice], False)
    assert st.resolve("Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", [sb], False)[1].text == "null0Alice"

    sbuf = JObject("Ljava/lang/StringBuffer;", {})
    st.resolve("Ljava/lang/StringBuffer;", "<init>", "()V", [sbuf], False)
    st.resolve("Ljava/lang/StringBuffer;", "append", "(I)Ljava/lang/StringBuffer;", [sbuf, 0], False)
    st.resolve("Ljava/lang/StringBuffer;", "append", "(Ljava/lang/Object;)Ljava/lang/StringBuffer;", [sbuf, None], False)
    st.resolve("Ljava/lang/StringBuffer;", "append", "(Ljava/lang/Object;)Ljava/lang/StringBuffer;", [sbuf, alice], False)
    assert st.resolve("Ljava/lang/StringBuffer;", "toString", "()Ljava/lang/String;", [sbuf], False)[1].text == "0nullAlice"

    fmt_res = st.resolve(
        "Ljava/lang/String;", "format", "(Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/String;",
        [JString("%s:%s:%d"), JArray("[Ljava/lang/Object;", [alice, None, JObject("Ljava/lang/Integer;", {"value": 7})])],
        True,
    )[1]
    assert fmt_res.text == "Alice:null:7"

    # Numeric wrappers Float/Double/Short/Byte toString + Integer JString compatibility
    assert st.resolve("Ljava/lang/Double;", "toString", "(D)Ljava/lang/String;", [3.5], True)[1].text == "3.5"
    assert st.resolve("Ljava/lang/Float;", "toString", "()Ljava/lang/String;", [JObject("Ljava/lang/Float;", {"value": float("nan")})], False)[1].text == "NaN"
    assert st.resolve("Ljava/lang/Short;", "toString", "(S)Ljava/lang/String;", [-12], True)[1].text == "-12"
    assert st.resolve("Ljava/lang/Byte;", "toString", "(B)Ljava/lang/String;", [42], True)[1].text == "42"
    assert st.resolve("Ljava/lang/Integer;", "toString", "()Ljava/lang/String;", [JObject("Ljava/lang/Integer;", {"value": JString("99")})], False)[1].text == "99"

    # Self-referential cycle guard (if Smali calls String.valueOf(self) -> terminates safely)
    vm_rt, st_rt = _mk_vm(with_runtime=True)
    vm_rt.class_provider = _StrProv()
    cyc = JObject("Lt/SelfCycle;", {})
    assert st_rt._obj_to_str(cyc) == "Lt/SelfCycle;"

    # Fatal exceptions MUST NEVER be swallowed by _obj_to_str
    for exc_factory in (
        lambda: VmBudgetExceeded("steps"),
        lambda: DalvikUnknownOpcode("bad", 0),
        lambda: DebugStop("bp"),
    ):
        class _FailVM:
            def _resolve_method(self, *a, **kw):
                return MethodDef("Lt/Boom;", "toString", "()Ljava/lang/String;", [], 1, 1)

            def _run_mdef(self, md, args):
                raise exc_factory()

        _, st_fail = _mk_vm()
        st_fail.vm = _FailVM()
        with pytest.raises((VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)):
            st_fail._obj_to_str(JObject("Lt/Boom;", {}))


def test_bundle_sync_and_iterator_items_pos():
    vm, st = _mk_vm()
    b = JObject("Landroid/os/Bundle;", {"$map": {"k": "hello"}})
    assert st.resolve("Landroid/os/Bundle;", "getString", "(Ljava/lang/String;)Ljava/lang/String;", [b, JString("k")], False)[1].text == "hello"
    st.resolve("Landroid/os/Bundle;", "putInt", "(Ljava/lang/String;I)V", [b, JString("num"), 42], False)
    assert b.fields["map"] is b.fields["$map"]
    assert st.resolve("Landroid/os/Bundle;", "getString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", [b, JString("missing"), JString("fallback")], False)[1].text == "fallback"
    assert "EMPTY" in vm.statics["Landroid/os/Bundle;"]

    # Iterator constructed with direct __items + __pos
    it = JObject("Ljava/util/Iterator;", {"__items": [JString("a"), JString("b")], "__pos": 0})
    assert st.resolve("Ljava/util/Iterator;", "hasNext", "()Z", [it], False)[1] == 1
    assert st.resolve("Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [it], False)[1].text == "a"
    assert it.fields["__pos"] == 1
    st.resolve("Ljava/util/Iterator;", "remove", "()V", [it], False)
    assert len(it.fields["__items"]) == 1 and it.fields["__pos"] == 0
    assert st.resolve("Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [it], False)[1].text == "b"
    assert st.resolve("Ljava/util/Iterator;", "hasNext", "()Z", [it], False)[1] == 0


def test_file_vfs_isolation_parent_and_bytebuffer_channel_unification():
    # Pure-Java mode + unified fs_bridge mode must both NEVER touch host FS and handle getParent
    for use_fs_bridge in (False, True):
        vm, st = _mk_vm()
        if use_fs_bridge:
            class _Eng:
                class _OS:
                    fs = VFS()
                _os = _OS()
            st._shared_fs = _Eng._os.fs
            install_fs_stubs(st, _Eng())

        # Host paths MUST NOT leak through File.exists()
        host_existing = os.path.abspath(__file__)
        hf = JObject("Ljava/io/File;", {})
        st.resolve("Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", [hf, JString(host_existing)], False)
        assert st.resolve("Ljava/io/File;", "exists", "()Z", [hf], False)[1] == 0

        # getParent / getParentFile for single relative segment, "/", and nested path
        rel = JObject("Ljava/io/File;", {})
        st.resolve("Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", [rel, JString("foo")], False)
        assert st.resolve("Ljava/io/File;", "getParent", "()Ljava/lang/String;", [rel], False)[1] is None
        assert st.resolve("Ljava/io/File;", "getParentFile", "()Ljava/io/File;", [rel], False)[1] is None

        root = JObject("Ljava/io/File;", {})
        st.resolve("Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", [root, JString("/")], False)
        assert st.resolve("Ljava/io/File;", "getParent", "()Ljava/lang/String;", [root], False)[1] is None

        sub = JObject("Ljava/io/File;", {})
        st.resolve("Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", [sub, JString("/a/b")], False)
        assert st.resolve("Ljava/io/File;", "getParent", "()Ljava/lang/String;", [sub], False)[1].text == "/a"

        # ByteBuffer order/putInt/flip/getInt across fs_bridge + g14 + g15 + FileChannel
        bb = st.resolve("Ljava/nio/ByteBuffer;", "allocate", "(I)Ljava/nio/ByteBuffer;", [16], True)[1]
        le = vm.statics["Ljava/nio/ByteOrder;"]["LITTLE_ENDIAN"]
        assert le.fields["_le"] is True and le.fields["order"] == "little"
        st.resolve("Ljava/nio/ByteBuffer;", "order", "(Ljava/nio/ByteOrder;)Ljava/nio/ByteBuffer;", [bb, le], False)
        st.resolve("Ljava/nio/ByteBuffer;", "putInt", "(I)Ljava/nio/ByteBuffer;", [bb, 0x12345678], False)
        st.resolve("Ljava/nio/ByteBuffer;", "flip", "()Ljava/nio/Buffer;", [bb], False)
        assert st.resolve("Ljava/nio/ByteBuffer;", "getInt", "()I", [bb], False)[1] == 0x12345678

        # Write via FileOutputStream.getChannel() -> read via FileInputStream.getChannel()
        p = "/data/data/com.example.app/files/ch_test.bin"
        fos = JObject("Ljava/io/FileOutputStream;", {})
        st.resolve("Ljava/io/FileOutputStream;", "<init>", "(Ljava/lang/String;)V", [fos, JString(p)], False)
        fc_w = st.resolve("Ljava/io/FileOutputStream;", "getChannel", "()Ljava/nio/channels/FileChannel;", [fos], False)[1]
        st.resolve("Ljava/nio/ByteBuffer;", "rewind", "()Ljava/nio/Buffer;", [bb], False)
        st.resolve("Ljava/nio/ByteBuffer;", "limit", "(I)Ljava/nio/Buffer;", [bb, 4], False)
        st.resolve("Ljava/nio/channels/FileChannel;", "write", "(Ljava/nio/ByteBuffer;)I", [fc_w, bb], False)
        st.resolve("Ljava/nio/channels/FileChannel;", "close", "()V", [fc_w], False)

        fis = JObject("Ljava/io/FileInputStream;", {})
        st.resolve("Ljava/io/FileInputStream;", "<init>", "(Ljava/lang/String;)V", [fis, JString(p)], False)
        fc_r = st.resolve("Ljava/io/FileInputStream;", "getChannel", "()Ljava/nio/channels/FileChannel;", [fis], False)[1]
        bb2 = st.resolve("Ljava/nio/ByteBuffer;", "allocate", "(I)Ljava/nio/ByteBuffer;", [4], True)[1]
        st.resolve("Ljava/nio/ByteBuffer;", "order", "(Ljava/nio/ByteOrder;)Ljava/nio/ByteBuffer;", [bb2, le], False)
        assert st.resolve("Ljava/nio/channels/FileChannel;", "read", "(Ljava/nio/ByteBuffer;)I", [fc_r, bb2], False)[1] == 4
        st.resolve("Ljava/nio/ByteBuffer;", "flip", "()Ljava/nio/Buffer;", [bb2], False)
        assert st.resolve("Ljava/nio/ByteBuffer;", "getInt", "()I", [bb2], False)[1] == 0x12345678


def test_parcel_and_handler_callbacks():
    vm, st = _mk_vm(with_runtime=True)
    p = st.resolve("Landroid/os/Parcel;", "obtain", "()Landroid/os/Parcel;", [], True)[1]
    b = JObject("Landroid/os/Bundle;", {})
    st.resolve("Landroid/os/Bundle;", "putString", "(Ljava/lang/String;Ljava/lang/String;)V", [b, JString("x"), JString("y")], False)

    st.resolve("Landroid/os/Parcel;", "writeInt", "(I)V", [p, 1234], False)
    st.resolve("Landroid/os/Parcel;", "writeString", "(Ljava/lang/String;)V", [p, JString("hi")], False)
    st.resolve("Landroid/os/Parcel;", "writeBundle", "(Landroid/os/Bundle;)V", [p, b], False)
    st.resolve("Landroid/os/Parcel;", "writeStringList", "(Ljava/util/List;)V", [p, JObject("Ljava/util/ArrayList;", {"__items": [JString("u"), JString("v")]})], False)

    raw = st.resolve("Landroid/os/Parcel;", "marshall", "()[B", [p], False)[1]
    p2 = st.resolve("Landroid/os/Parcel;", "obtain", "()Landroid/os/Parcel;", [], True)[1]
    st.resolve("Landroid/os/Parcel;", "unmarshall", "([BII)V", [p2, raw, 0, len(raw.data)], False)
    st.resolve("Landroid/os/Parcel;", "setDataPosition", "(I)V", [p2, 0], False)
    assert st.resolve("Landroid/os/Parcel;", "readInt", "()I", [p2], False)[1] == 1234
    assert st.resolve("Landroid/os/Parcel;", "readString", "()Ljava/lang/String;", [p2], False)[1].text == "hi"
    b2 = st.resolve("Landroid/os/Parcel;", "readBundle", "()Landroid/os/Bundle;", [p2], False)[1]
    assert st.resolve("Landroid/os/Bundle;", "getString", "(Ljava/lang/String;)Ljava/lang/String;", [b2, JString("x")], False)[1].text == "y"
    sl2 = st.resolve("Landroid/os/Parcel;", "createStringArrayList", "()Ljava/util/ArrayList;", [p2], False)[1]
    assert [x.text for x in sl2.fields["__items"]] == ["u", "v"]

    # Handler + Callback.handleMessage queued runnable dispatch
    seen = []

    class _CbProv:
        def get_class(self, desc):
            if desc == "Lt/MyCb;":
                return [
                    MethodDef("Lt/MyCb;", "handleMessage", "(Landroid/os/Message;)Z", [
                        ("invoke-static", (2,), "Lt/Rec;", "hit", "(Landroid/os/Message;)V"),
                        ("const", 0, 1),
                        ("return", 0),
                    ], nregs=3, nargs=2),
                ]
            return None

        def get_super(self, desc):
            return "Ljava/lang/Object;"

    vm.class_provider = _CbProv()
    st.register("Lt/Rec;", "hit", "(Landroid/os/Message;)V", lambda a: seen.append(a[0].fields.get("what")))
    cb = JObject("Lt/MyCb;", {})
    h = JObject("Landroid/os/Handler;", {})
    looper = st.resolve("Landroid/os/Looper;", "getMainLooper", "()Landroid/os/Looper;", [], True)[1]
    st.resolve("Landroid/os/Handler;", "<init>", "(Landroid/os/Looper;Landroid/os/Handler$Callback;)V", [h, looper, cb], False)
    msg = JObject("Landroid/os/Message;", {"what": 77})
    st.resolve("Landroid/os/Handler;", "sendMessageDelayed", "(Landroid/os/Message;J)Z", [h, msg, 150], False)
    assert len(st.pending_runnables) == 1
    r_item = st.pending_runnables.pop(0)
    run_obj = r_item[0] if isinstance(r_item, tuple) else r_item
    st.resolve(run_obj.cls, "run", "()V", [run_obj], False)
    assert seen == [77]


def test_gzip_deflater_math_biginteger_executor_resources_ssl_notif_cert():
    vm, st = _mk_vm()

    # GZIP roundtrip via FileOutputStream/FileInputStream + (OutputStream;I)V constructor
    gz_path = "/data/data/com.example.app/files/sample.gz"
    fos = JObject("Ljava/io/FileOutputStream;", {})
    st.resolve("Ljava/io/FileOutputStream;", "<init>", "(Ljava/lang/String;)V", [fos, JString(gz_path)], False)
    go = JObject("Ljava/util/zip/GZIPOutputStream;", {})
    st.resolve("Ljava/util/zip/GZIPOutputStream;", "<init>", "(Ljava/io/OutputStream;I)V", [go, fos, 4096], False)
    payload = b"hello G15 gzip world" * 3
    st.resolve("Ljava/util/zip/GZIPOutputStream;", "write", "([B)V", [go, JArray("[B", list(payload))], False)
    st.resolve("Ljava/util/zip/GZIPOutputStream;", "finish", "()V", [go], False)

    fis = JObject("Ljava/io/FileInputStream;", {})
    st.resolve("Ljava/io/FileInputStream;", "<init>", "(Ljava/lang/String;)V", [fis, JString(gz_path)], False)
    gi = JObject("Ljava/util/zip/GZIPInputStream;", {})
    st.resolve("Ljava/util/zip/GZIPInputStream;", "<init>", "(Ljava/io/InputStream;I)V", [gi, fis, 4096], False)
    out = JArray("[B", [0] * len(payload))
    n = st.resolve("Ljava/util/zip/GZIPInputStream;", "read", "([B)I", [gi, out], False)[1]
    assert bytes(out.data[:n]) == payload

    # Deflater / Inflater
    df = JObject("Ljava/util/zip/Deflater;", {})
    st.resolve("Ljava/util/zip/Deflater;", "<init>", "()V", [df], False)
    st.resolve("Ljava/util/zip/Deflater;", "setInput", "([B)V", [df, JArray("[B", list(payload))], False)
    st.resolve("Ljava/util/zip/Deflater;", "finish", "()V", [df], False)
    cbuf = JArray("[B", [0] * 256)
    clen = st.resolve("Ljava/util/zip/Deflater;", "deflate", "([B)I", [df, cbuf], False)[1]
    inf = JObject("Ljava/util/zip/Inflater;", {})
    st.resolve("Ljava/util/zip/Inflater;", "<init>", "()V", [inf], False)
    st.resolve("Ljava/util/zip/Inflater;", "setInput", "([BII)V", [inf, cbuf, 0, clen], False)
    dbuf = JArray("[B", [0] * len(payload))
    dlen = st.resolve("Ljava/util/zip/Inflater;", "inflate", "([B)I", [inf, dbuf], False)[1]
    assert bytes(dbuf.data[:dlen]) == payload

    # Math / StrictMath + *Exact overflow
    assert abs(vm.statics["Ljava/lang/Math;"]["PI"] - 3.14159265) < 1e-6
    assert st.resolve("Ljava/lang/Math;", "floorDiv", "(II)I", [-7, 2], True)[1] == -4
    assert st.resolve("Ljava/lang/StrictMath;", "hypot", "(DD)D", [3.0, 4.0], True)[1] == 5.0
    with pytest.raises(DalvikException, match="ArithmeticException"):
        st.resolve("Ljava/lang/Math;", "addExact", "(II)I", [0x7FFFFFFF, 1], True)

    # BigInteger modPow & BigDecimal divide with RoundingMode.HALF_UP
    bi_base = st.resolve("Ljava/math/BigInteger;", "valueOf", "(J)Ljava/math/BigInteger;", [2], True)[1]
    bi_exp = st.resolve("Ljava/math/BigInteger;", "valueOf", "(J)Ljava/math/BigInteger;", [10], True)[1]
    bi_mod = st.resolve("Ljava/math/BigInteger;", "valueOf", "(J)Ljava/math/BigInteger;", [1000], True)[1]
    bi_res = st.resolve("Ljava/math/BigInteger;", "modPow", "(Ljava/math/BigInteger;Ljava/math/BigInteger;)Ljava/math/BigInteger;", [bi_base, bi_exp, bi_mod], False)[1]
    assert st.resolve("Ljava/math/BigInteger;", "intValue", "()I", [bi_res], False)[1] == 24

    bd1 = JObject("Ljava/math/BigDecimal;", {})
    st.resolve("Ljava/math/BigDecimal;", "<init>", "(Ljava/lang/String;)V", [bd1, JString("10")], False)
    bd2 = JObject("Ljava/math/BigDecimal;", {})
    st.resolve("Ljava/math/BigDecimal;", "<init>", "(Ljava/lang/String;)V", [bd2, JString("3")], False)
    rm = vm.statics["Ljava/math/RoundingMode;"]["HALF_UP"]
    bd_q = st.resolve("Ljava/math/BigDecimal;", "divide", "(Ljava/math/BigDecimal;ILjava/math/RoundingMode;)Ljava/math/BigDecimal;", [bd1, bd2, 2, rm], False)[1]
    assert st.resolve("Ljava/math/BigDecimal;", "toString", "()Ljava/lang/String;", [bd_q], False)[1].text == "3.33"

    # ExecutorService.submit(Callable) -> Future.get()
    class _CallProv:
        def get_class(self, desc):
            if desc == "Lt/MyCall;":
                return [
                    MethodDef("Lt/MyCall;", "call", "()Ljava/lang/Object;", [
                        ("const-string", 0, "done-val"),
                        ("return-object", 0),
                    ], nregs=1, nargs=1),
                ]
            return None

        def get_super(self, desc):
            return "Ljava/lang/Object;"

    vm.class_provider = _CallProv()
    ex = st.resolve("Ljava/util/concurrent/Executors;", "newSingleThreadExecutor", "()Ljava/util/concurrent/ExecutorService;", [], True)[1]
    fut = st.resolve("Ljava/util/concurrent/ExecutorService;", "submit", "(Ljava/util/concurrent/Callable;)Ljava/util/concurrent/Future;", [ex, JObject("Lt/MyCall;", {})], False)[1]
    assert st.resolve("Ljava/util/concurrent/Future;", "get", "()Ljava/lang/Object;", [fut], False)[1].text == "done-val"

    # Resources.newTheme + TypedValue.applyDimension + SSL + NotificationManager + CertificateFactory
    res = JObject("Landroid/content/res/Resources;", {})
    theme = st.resolve("Landroid/content/res/Resources;", "newTheme", "()Landroid/content/res/Resources$Theme;", [res], False)[1]
    assert isinstance(theme, JObject)
    dm = st.resolve("Landroid/content/res/Resources;", "getDisplayMetrics", "()Landroid/util/DisplayMetrics;", [res], False)[1]
    px = st.resolve("Landroid/util/TypedValue;", "applyDimension", "(IFLandroid/util/DisplayMetrics;)F", [1, 10.0, dm], True)[1]
    assert px > 10.0

    sslc = st.resolve("Ljavax/net/ssl/SSLContext;", "getInstance", "(Ljava/lang/String;)Ljavax/net/ssl/SSLContext;", [JString("TLS")], True)[1]
    sf = st.resolve("Ljavax/net/ssl/SSLContext;", "getSocketFactory", "()Ljavax/net/ssl/SSLSocketFactory;", [sslc], False)[1]
    sock = st.resolve("Ljavax/net/ssl/SSLSocketFactory;", "createSocket", "(Ljava/net/Socket;Ljava/lang/String;IZ)Ljava/net/Socket;", [sf, None, JString("api.example.com"), 443, 1], False)[1]
    sess = st.resolve("Ljavax/net/ssl/SSLSocket;", "getSession", "()Ljavax/net/ssl/SSLSession;", [sock], False)[1]
    certs = st.resolve("Ljavax/net/ssl/SSLSession;", "getPeerCertificates", "()[Ljava/security/cert/Certificate;", [sess], False)[1]
    assert len(certs.data) >= 1

    nm = JObject("Landroid/app/NotificationManager;", {})
    ch = JObject("Landroid/app/NotificationChannel;", {"id": "ch1"})
    ch_list = JObject("Ljava/util/ArrayList;", {"__items": [ch]})
    st.resolve("Landroid/app/NotificationManager;", "createNotificationChannels", "(Ljava/util/List;)V", [nm, ch_list], False)
    assert st.resolve("Landroid/app/NotificationManager;", "getNotificationChannel", "(Ljava/lang/String;)Landroid/app/NotificationChannel;", [nm, JString("ch1")], False)[1] is ch

    cf = st.resolve("Ljava/security/cert/CertificateFactory;", "getInstance", "(Ljava/lang/String;)Ljava/security/cert/CertificateFactory;", [JString("X.509")], True)[1]
    bais = JObject("Ljava/io/ByteArrayInputStream;", {})
    st.resolve("Ljava/io/ByteArrayInputStream;", "<init>", "([B)V", [bais, JArray("[B", [0x30, 0x82, 0x01, 0x00])], False)
    ccoll = st.resolve("Ljava/security/cert/CertificateFactory;", "generateCertificates", "(Ljava/io/InputStream;)Ljava/util/Collection;", [cf, bais], False)[1]
    cit = st.resolve("Ljava/util/Collection;", "iterator", "()Ljava/util/Iterator;", [ccoll], False)[1]
    assert st.resolve("Ljava/util/Iterator;", "hasNext", "()Z", [cit], False)[1] == 1
    assert isinstance(st.resolve("Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", [cit], False)[1], JObject)


def test_dalvik_float_bit_unpacking_and_system_properties_override():
    _, st = _mk_vm()

    # Dalvik float32 IEEE-754 bit patterns: 0x42960000 = 75.0f, 0x4447f99a ~= 799.9f
    f_75 = st.resolve("Ljava/lang/Float;", "valueOf", "(F)Ljava/lang/Float;", [0x42960000], True)[1]
    assert st.resolve("Ljava/lang/Float;", "floatValue", "()F", [f_75], False)[1] == 75.0
    assert st.resolve("Ljava/lang/Float;", "toString", "(F)Ljava/lang/String;", [0x42960000], True)[1].text == "75.0"
    assert float(st.resolve("Ljava/lang/Float;", "toString", "(F)Ljava/lang/String;", [0x4447F99A], True)[1].text) == pytest.approx(799.9, rel=1e-4)
    assert st.resolve("Ljava/lang/String;", "valueOf", "(F)Ljava/lang/String;", [0x42960000], True)[1].text == "75.0"
    assert float(st.resolve("Ljava/lang/String;", "valueOf", "(F)Ljava/lang/String;", [0x4447F99A], True)[1].text) == pytest.approx(799.9, rel=1e-4)

    # Double 0x400C000000000000 = 3.5
    d_35 = st.resolve("Ljava/lang/Double;", "valueOf", "(D)Ljava/lang/Double;", [0x400C000000000000], True)[1]
    assert st.resolve("Ljava/lang/Double;", "doubleValue", "()D", [d_35], False)[1] == 3.5
    assert st.resolve("Ljava/lang/Double;", "toString", "(D)Ljava/lang/String;", [0x400C000000000000], True)[1].text == "3.5"

    # Math.min(FF)F and SharedPreferences putFloat/getFloat with Dalvik IEEE-754 int bits
    assert st.resolve("Ljava/lang/Math;", "min", "(FF)F", [0x4447F99A, 0x42960000], True)[1] == 75.0
    sp = st.resolve("Landroid/content/Context;", "getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;", [None, JString("flt_test"), 0], False)[1]
    ed = st.resolve("Landroid/content/SharedPreferences;", "edit", "()Landroid/content/SharedPreferences$Editor;", [sp], False)[1]
    st.resolve("Landroid/content/SharedPreferences$Editor;", "putFloat", "(Ljava/lang/String;F)Landroid/content/SharedPreferences$Editor;", [ed, JString("w"), 0x4447F99A], False)
    assert st.resolve("Landroid/content/SharedPreferences;", "getFloat", "(Ljava/lang/String;F)F", [sp, JString("w"), 0], False)[1] == pytest.approx(799.9, rel=1e-4)
    assert st.resolve("Landroid/content/SharedPreferences;", "getFloat", "(Ljava/lang/String;F)F", [sp, JString("missing_w"), 0x42960000], False)[1] == 75.0

    # System.getProperties().setProperty overrides default System.getProperty values
    props = st.resolve("Ljava/lang/System;", "getProperties", "()Ljava/util/Properties;", [], True)[1]
    st.resolve("Ljava/util/Properties;", "setProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/Object;", [props, JString("os.name"), JString("Android")], False)
    assert st.resolve("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", [JString("os.name")], True)[1].text == "Android"
