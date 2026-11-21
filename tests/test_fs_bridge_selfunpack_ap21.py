# -*- coding: utf-8 -*-
"""AP2.1 (revidiert): Self-Unpacking-Substrat.
- fs_bridge: Java-Datei-I/O (File/FileInputStream/FileOutputStream/ZipFile) auf GEMEINSAMEM VFS
  (eng._os.fs) -> von Java geschriebene Dateien sind nativ sichtbar und umgekehrt.
- mkdirat/unlinkat auf VFS.
- ELF DT_INIT/DT_INIT_ARRAY-Parsing + run_init_funcs (Konstruktoren vor JNI_OnLoad).
Hermetisch, zielagnostisch.
"""
import os, sys, io, zipfile, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JObject, JArray


def _eng_os():
    from services.native_emu_service import NativeEmuService
    from services.native_os_service import NativeOsService
    eng = NativeEmuService(publish=False).new_engine()
    NativeOsService(publish=False).install(eng)
    return eng


def _mk():
    eng = _eng_os()
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    from core.java.fs_bridge import install_fs_stubs
    fs = install_fs_stubs(stubs, eng, pkg="com.x", native_lib_dir="/data/app/x/lib/arm64")
    return eng, vm, stubs, fs


def _call(stubs, cls, name, sig, args):
    ok, ret = stubs.resolve(cls, name, sig, args, static=False)
    assert ok, (cls, name, sig)
    return ret


# ---- fs_bridge: gemeinsames VFS ----
def test_java_write_visible_to_native_fs():
    eng, vm, stubs, fs = _mk()
    f = JObject("Ljava/io/File;", {})
    _call(stubs, "Ljava/io/File;", "<init>", "(Ljava/lang/String;)V",
          [f, JString("/data/data/com.x/app_lib/libfoo.so")])
    fos = JObject("Ljava/io/FileOutputStream;", {})
    _call(stubs, "Ljava/io/FileOutputStream;", "<init>", "(Ljava/io/File;)V", [fos, f])
    _call(stubs, "Ljava/io/FileOutputStream;", "write", "([B)V",
          [fos, JArray("[B", list(b"\x7fELFdata"))])
    _call(stubs, "Ljava/io/FileOutputStream;", "close", "()V", [fos])
    # nativer Blick auf dieselbe Datei
    ino = fs.lookup("/data/data/com.x/app_lib/libfoo.so")
    assert ino is not None and bytes(fs.read(ino)) == b"\x7fELFdata"
    # File.exists / length
    assert _call(stubs, "Ljava/io/File;", "exists", "()Z", [f]) == 1
    assert _call(stubs, "Ljava/io/File;", "length", "()J", [f]) == 8


def test_native_write_visible_to_java_read():
    eng, vm, stubs, fs = _mk()
    fs.add_file("/data/app/x/lib/arm64/libbar.so", b"ABCDEF")   # "nativ" geschrieben
    f = JObject("Ljava/io/File;", {})
    _call(stubs, "Ljava/io/File;", "<init>", "(Ljava/lang/String;)V",
          [f, JString("/data/app/x/lib/arm64/libbar.so")])
    assert _call(stubs, "Ljava/io/File;", "exists", "()Z", [f]) == 1
    fis = JObject("Ljava/io/FileInputStream;", {})
    _call(stubs, "Ljava/io/FileInputStream;", "<init>", "(Ljava/io/File;)V", [fis, f])
    buf = JArray("[B", [0] * 16)
    n = _call(stubs, "Ljava/io/FileInputStream;", "read", "([B)I", [fis, buf])
    assert n == 6 and bytes(buf.data[:n]) == b"ABCDEF"


def test_file_mkdirs_name_and_appinfo():
    eng, vm, stubs, fs = _mk()
    d = JObject("Ljava/io/File;", {})
    _call(stubs, "Ljava/io/File;", "<init>", "(Ljava/lang/String;)V", [d, JString("/data/data/com.x/sub/deep")])
    assert _call(stubs, "Ljava/io/File;", "mkdirs", "()Z", [d]) == 1
    assert _call(stubs, "Ljava/io/File;", "isDirectory", "()Z", [d]) == 1
    assert _call(stubs, "Ljava/io/File;", "getName", "()Ljava/lang/String;", [d]).text == "deep"
    ai = _call(stubs, "Landroid/content/Context;", "getApplicationInfo",
               "()Landroid/content/pm/ApplicationInfo;", [JObject("Landroid/content/Context;", {})])
    assert ai.fields["nativeLibraryDir"].text == "/data/app/x/lib/arm64"


def test_zipfile_reads_mounted_apk():
    eng, vm, stubs, fs = _mk()
    # Mini-APK mit einer lib-Datei ins VFS mounten
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr("lib/arm64-v8a/libzz.so", b"\x7fELFzip")
    fs.add_file("/data/app/base.apk", bio.getvalue())
    zf = JObject("Ljava/util/zip/ZipFile;", {})
    _call(stubs, "Ljava/util/zip/ZipFile;", "<init>", "(Ljava/lang/String;)V", [zf, JString("/data/app/base.apk")])
    ent = _call(stubs, "Ljava/util/zip/ZipFile;", "getEntry",
                "(Ljava/lang/String;)Ljava/util/zip/ZipEntry;", [zf, JString("lib/arm64-v8a/libzz.so")])
    assert ent is not None and ent.fields["name"] == "lib/arm64-v8a/libzz.so"
    ins = _call(stubs, "Ljava/util/zip/ZipFile;", "getInputStream",
                "(Ljava/util/zip/ZipEntry;)Ljava/io/InputStream;", [zf, ent])
    buf = JArray("[B", [0] * 16)
    n = _call(stubs, "Ljava/io/FileInputStream;", "read", "([B)I", [ins, buf])
    assert bytes(buf.data[:n]) == b"\x7fELFzip"


# ---- mkdirat / unlinkat Syscalls ----
_M64 = (1 << 64) - 1
def _svc(cpu, nr, *args):
    for i, a in enumerate(args): cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64; cpu._svc_hook(cpu); return cpu.x[0]
def _put(cpu, addr, s): cpu.m.wb(addr, s.encode() + b"\x00")

def test_mkdirat_unlinkat_on_vfs():
    from core.java.fs_bridge import get_shared_fs
    eng = _eng_os(); cpu = eng.cpu
    fs = get_shared_fs(eng)
    _put(cpu, 0x70000, "/data/data/com.x/newdir")
    assert _svc(cpu, 34, 0xFFFFFF9C, 0x70000, 0o755) == 0          # mkdirat
    assert fs.lookup("/data/data/com.x/newdir") is not None
    fs.add_file("/data/data/com.x/gone.txt", b"x")
    _put(cpu, 0x71000, "/data/data/com.x/gone.txt")
    assert _svc(cpu, 35, 0xFFFFFF9C, 0x71000, 0) == 0              # unlinkat
    assert fs.lookup("/data/data/com.x/gone.txt") is None


# ---- ELF DT_INIT / DT_INIT_ARRAY ----
def test_elf_init_tags_parsed_on_real_lib():
    from core.native.loader import ElfImage
    p = "source/org.nativescript.LibreLinkUp/base_unpacked_apktool/lib/arm64-v8a/libappmodules.so"
    if not os.path.isfile(p):
        import pytest; pytest.skip("libappmodules.so nicht im Workspace")
    img = ElfImage.from_file(p)
    assert img.init_array != 0 and img.init_arraysz >= 8     # echte init_array vorhanden


def test_run_init_funcs_calls_constructors():
    from core.java.fusion_autoload import run_init_funcs
    calls = []
    class _Eng:
        class mem:
            @staticmethod
            def read(addr, n):
                # 2 Zeiger: 0x1111, 0x2222 an init_array
                return {0x5000: (0x1111).to_bytes(8, "little"),
                        0x5008: (0x2222).to_bytes(8, "little")}.get(addr, b"\x00" * 8)
    class _Svc:
        def run(self, eng, addr, args, a, b, maxins): calls.append(addr)
    class _Img:
        init = 0x4000; init_array = 0x5000; init_arraysz = 16
    out = run_init_funcs(_Svc(), _Eng(), _Img())
    assert calls == [0x4000, 0x1111, 0x2222]      # DT_INIT zuerst, dann init_array in Reihenfolge
    assert out["init"] == 1 and out["init_array"] == 2
