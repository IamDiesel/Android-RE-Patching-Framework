# -*- coding: utf-8 -*-
"""AP2.1 generisch: VFS-Selbstentpack-Kreislauf (KEIN App-Bezug).
Streams (Filter/ByteArray) -> Context-App-Speicher (openFileOutput/getDir) -> File.renameTo
-> Lesen via nativem openat/read UND via Runtime.nativeLoad/_fusion_vfs_read. Rein AOSP/JDK-spec.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JObject, JArray

_M64 = (1 << 64) - 1
PKG = "com.generic.app"
DATA = "/data/data/%s" % PKG


def _mk():
    from services.native_emu_service import NativeEmuService
    from services.native_os_service import NativeOsService
    from core.java.fs_bridge import install_fs_stubs
    eng = NativeEmuService(publish=False).new_engine()
    NativeOsService(publish=False).install(eng)
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    fs = install_fs_stubs(stubs, eng, pkg=PKG, native_lib_dir="/data/app/g/lib/arm64")
    return eng, vm, stubs, fs


def _c(stubs, cls, n, sig, args):
    ok, r = stubs.resolve(cls, n, sig, args, static=False); assert ok, (cls, n, sig); return r


def _bais(stubs, data):
    o = JObject("Ljava/io/ByteArrayInputStream;", {})
    _c(stubs, "Ljava/io/ByteArrayInputStream;", "<init>", "([B)V", [o, JArray("[B", list(data))])
    return o


# ---- Streams ----
def test_bytearray_in_out_roundtrip():
    _, _, st, _ = _mk()
    bo = JObject("Ljava/io/ByteArrayOutputStream;", {})
    _c(st, "Ljava/io/ByteArrayOutputStream;", "<init>", "()V", [bo])
    _c(st, "Ljava/io/ByteArrayOutputStream;", "write", "([B)V", [bo, JArray("[B", list(b"ABCDE"))])
    _c(st, "Ljava/io/ByteArrayOutputStream;", "write", "(I)V", [bo, 0x21])
    assert _c(st, "Ljava/io/ByteArrayOutputStream;", "size", "()I", [bo]) == 6
    out = _c(st, "Ljava/io/ByteArrayOutputStream;", "toByteArray", "()[B", [bo])
    assert bytes(out.data) == b"ABCDE!"


def test_filterinputstream_delegates_to_inner():
    _, _, st, _ = _mk()
    inner = _bais(st, b"HELLOWORLD")
    f = JObject("Ljava/io/FilterInputStream;", {})
    _c(st, "Ljava/io/FilterInputStream;", "<init>", "(Ljava/io/InputStream;)V", [f, inner])
    buf = JArray("[B", [0] * 16)
    n = _c(st, "Ljava/io/FilterInputStream;", "read", "([B)I", [f, buf])
    assert n == 10 and bytes(buf.data[:n]) == b"HELLOWORLD"
    # BufferedInputStream nutzt denselben Delegationspfad
    inner2 = _bais(st, b"XY")
    b = JObject("Ljava/io/BufferedInputStream;", {})
    _c(st, "Ljava/io/BufferedInputStream;", "<init>", "(Ljava/io/InputStream;)V", [b, inner2])
    assert _c(st, "Ljava/io/BufferedInputStream;", "read", "()I", [b]) == ord("X")


# ---- Context-App-Speicher + renameTo + native/VFS-Reload ----
def test_full_vfs_cycle_store_rename_nativeread():
    eng, vm, st, fs = _mk()
    ctx = JObject("Landroid/content/Context;", {"__pkg__": PKG})
    payload = b"\x7fELF-generic-payload-bytes"

    # 1) getDir legt App-Verzeichnis im VFS an
    d = _c(st, "Landroid/content/Context;", "getDir", "(Ljava/lang/String;I)Ljava/io/File;",
           [ctx, JString("native"), 0])
    assert d.fields["path"] == "%s/app_native" % DATA
    assert fs.lookup(d.fields["path"]) is not None

    # 2) App entpackt: InputStream lesen -> openFileOutput schreiben
    src = _bais(st, payload)
    fos = _c(st, "Landroid/content/Context;", "openFileOutput",
             "(Ljava/lang/String;I)Ljava/io/FileOutputStream;", [ctx, JString("lib.tmp"), 0])
    buf = JArray("[B", [0] * 64)
    n = _c(st, "Ljava/io/ByteArrayInputStream;", "read", "([B)I", [src, buf])
    _c(st, "Ljava/io/FileOutputStream;", "write", "([BII)V", [fos, buf, 0, n])
    _c(st, "Ljava/io/FileOutputStream;", "close", "()V", [fos])
    tmp_path = "%s/files/lib.tmp" % DATA
    assert bytes(fs.read(fs.lookup(tmp_path))) == payload

    # 3) atomar umbenennen .tmp -> .so (wie SoLoader/Packer)
    so = JObject("Ljava/io/File;", {"path": "%s/files/lib.so" % DATA})
    tmpf = JObject("Ljava/io/File;", {"path": tmp_path})
    assert _c(st, "Ljava/io/File;", "renameTo", "(Ljava/io/File;)Z", [tmpf, so]) == 1
    assert fs.lookup(tmp_path) is None
    so_path = "%s/files/lib.so" % DATA
    assert bytes(fs.read(fs.lookup(so_path))) == payload

    # 4a) nativ lesen (openat/read-Syscalls sehen DIESELBE Datei)
    cpu = eng.cpu
    def svc(nr, *args):
        for i, x in enumerate(args): cpu.x[i] = x & _M64
        cpu.x[8] = nr & _M64; cpu._svc_hook(cpu); return cpu.x[0]
    cpu.m.wb(0x90000, so_path.encode() + b"\x00")
    fd = svc(56, 0xFFFFFF9C, 0x90000, 0)        # openat O_RDONLY
    assert fd < 0x10000
    rn = svc(63, fd, 0x91000, 64)                # read
    assert bytes(cpu.m.rb(0x91000, rn)) == payload

    # 4b) Runtime.nativeLoad-Pfad: _fusion_vfs_read liefert exakt diese Bytes
    from ui.controllers.debugger_controller import DebuggerController
    ctrl = DebuggerController.__new__(DebuggerController)
    assert ctrl._fusion_vfs_read(eng, so_path) == payload


def test_inmemory_dexclassloader_plumbing():
    _, _, st, _ = _mk()
    bb = _c(st, "Ljava/nio/ByteBuffer;", "wrap", "([B)Ljava/nio/ByteBuffer;",
            [JArray("[B", list(b"not-a-real-dex"))])
    dcl = JObject("Ldalvik/system/InMemoryDexClassLoader;", {})
    _c(st, "Ldalvik/system/InMemoryDexClassLoader;", "<init>",
       "(Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V", [dcl, bb, None])
    assert "_dcls" in dcl.fields     # Plumbing steht (leere Liste bei Garbage-Bytes ok)
    cls = _c(st, "Ldalvik/system/InMemoryDexClassLoader;", "loadClass",
             "(Ljava/lang/String;)Ljava/lang/Class;", [dcl, JString("com.x.Y")])
    assert isinstance(cls, JObject) and cls.fields.get("name") == "com.x.Y"
