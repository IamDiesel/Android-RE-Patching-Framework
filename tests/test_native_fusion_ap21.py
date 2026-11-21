# -*- coding: utf-8 -*-
"""AP2.1 (Native Fusion): SoLoader-Override fusionsfaehig, wire_imports in autoload_library,
J2-Rueckkanal (VmBackedJavaRuntime), VFS O_CREAT/O_TRUNC + lookup/read.
Hermetisch, zielagnostisch (synthetische Klassen/Fakes, keine echte .so)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.objects import JString, JObject

_M64 = (1 << 64) - 1


# ============ B.1: SoLoader-Override fusionsfaehig ============
def test_soloader_override_noop_without_fusion():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    ov = vm.method_overrides[("Lcom/facebook/soloader/SoLoader;", "loadLibrary", "(Ljava/lang/String;)Z")]
    assert callable(ov)
    # ohne vm._fusion_load_library -> No-Op-Erfolg (1), kein Fehler
    assert ov([JString("reactnative")]) == 1


def test_soloader_override_forwards_to_fusion_hook():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    seen = []
    vm._fusion_load_library = lambda name: seen.append(name)
    ov = vm.method_overrides[("Lcom/facebook/soloader/SoLoader;", "loadLibrary", "(Ljava/lang/String;)Z")]
    assert ov([JString("hermes")]) == 1
    assert seen == ["hermes"]
    # NativeLoader-Pfad identisch
    ov2 = vm.method_overrides[("Lcom/facebook/soloader/nativeloader/NativeLoader;",
                               "loadLibrary", "(Ljava/lang/String;)Z")]
    assert ov2([JString("fbjni")]) == 1
    assert seen == ["hermes", "fbjni"]


def test_soloader_override_hook_exception_is_swallowed():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    def boom(name): raise RuntimeError("x")
    vm._fusion_load_library = boom
    ov = vm.method_overrides[("Lcom/facebook/soloader/SoLoader;", "loadLibrary", "(Ljava/lang/String;)Z")]
    assert ov([JString("a")]) == 1   # Fehler im Hook reisst loadLibrary nicht


# ============ B.2: wire_imports zwischen load_image und JNI_OnLoad ============
class _FakeSvc:
    def __init__(self, order): self.order = order
    def load_image(self, eng, img, apply_relocs=True):
        self.order.append(("load_image", apply_relocs)); return 7
    def run(self, eng, addr, args, a, b, maxins):
        self.order.append(("run_onload", addr))

class _FakeFfi:
    def __init__(self, order): self.order = order
    def wire_imports(self, eng, img):
        self.order.append(("wire_imports",)); return {"count": 3}

class _FakeEng:
    class _cpu:  x = [0]*32
    cpu = _cpu()

class _FakeJni:
    class st: natives = []

def test_autoload_wire_imports_between_load_and_onload(monkeypatch):
    import core.java.fusion_autoload as FA
    order = []
    svc = _FakeSvc(order); ffi = _FakeFfi(order); jni = _FakeJni()
    monkeypatch.setattr(FA, "find_jni_onload", lambda img: 0x1000)
    monkeypatch.setattr(FA, "harvest_registered_natives", lambda jni, table, **k: 0)
    out = FA.autoload_library(svc, _FakeEng(), jni, img=object(), table=object(), ffi=ffi)
    names = [o[0] for o in order]
    assert names == ["load_image", "wire_imports", "run_onload"], names
    assert out["wired"] == 3


def test_autoload_without_ffi_skips_wire(monkeypatch):
    import core.java.fusion_autoload as FA
    order = []
    svc = _FakeSvc(order); jni = _FakeJni()
    monkeypatch.setattr(FA, "find_jni_onload", lambda img: 0x1000)
    monkeypatch.setattr(FA, "harvest_registered_natives", lambda jni, table, **k: 0)
    out = FA.autoload_library(svc, _FakeEng(), jni, img=object(), table=object(), ffi=None)
    assert [o[0] for o in order] == ["load_image", "run_onload"]
    assert out["wired"] == 0


# ============ B.3: J2-Rueckkanal (VmBackedJavaRuntime) ============
_SMALI = r'''
.class public Lgen/J2;
.super Ljava/lang/Object;
.method public static add(II)I
    .locals 1
    add-int v0, p0, p1
    return v0
.end method
'''

class _FakeJniEnv:
    def __init__(self): self._h = 1000
    def _new_handle(self, kind, **kw):
        self._h += 1; return self._h

def test_vmbacked_runtime_has_and_calls_registered_method():
    from core.java.smali import load_into
    from core.java.runtime import VmBackedJavaRuntime
    vm = DalvikVM()
    load_into(vm, _SMALI)
    rt = VmBackedJavaRuntime(vm, _FakeJniEnv())
    assert rt.has_method("Lgen/J2;", "add", "(II)I")
    assert not rt.has_method("Lgen/J2;", "nope", "()V")
    # native arg-words -> echte Dalvik-Methode auf der LAUFENDEN VM
    ret = rt.call_from_native("Lgen/J2;", "add", "(II)I", [5, 37], static=True)
    assert ret == 42


def test_vmbacked_runtime_lazy_loads_via_provider():
    from core.java.runtime import VmBackedJavaRuntime
    from core.java import smali as Sm
    vm = DalvikVM()
    class _Prov:
        def get_class(self, d):
            if d == "Lgen/Lazy;":
                _c, mds = Sm.parse_class(_SMALI.replace("Lgen/J2;", "Lgen/Lazy;"))
                return mds
            return None
    vm.class_provider = _Prov()
    rt = VmBackedJavaRuntime(vm, _FakeJniEnv())
    assert rt.has_method("Lgen/Lazy;", "add", "(II)I")   # lazy nachgeladen


def test_vmbacked_runtime_marshals_object_return_to_handle():
    from core.java.runtime import VmBackedJavaRuntime
    vm = DalvikVM()
    jni = _FakeJniEnv()
    rt = VmBackedJavaRuntime(vm, jni)
    h = rt._to_jni(JString("hi"))
    assert h in rt._h2py and isinstance(rt._h2py[h], JString)
    assert rt._to_py(h) is rt._h2py[h]           # Rueck-Marshalling ueber Handle-Tabelle
    assert rt._to_jni(None) == 0
    assert rt._to_jni(123) == 123


# ============ B.4: VFS O_CREAT/O_TRUNC + _fusion_vfs_read ============
def _os_eng():
    from services.native_emu_service import NativeEmuService
    from services.native_os_service import NativeOsService
    eng = NativeEmuService(publish=False).new_engine()
    osv = NativeOsService(publish=False); osv.attach_env(eng); osv.install(eng)
    return eng

def _svc(cpu, nr, *args):
    for i, a in enumerate(args): cpu.x[i] = a & _M64
    cpu.x[8] = nr & _M64; cpu._svc_hook(cpu); return cpu.x[0]

def _put(cpu, addr, s): cpu.m.wb(addr, s.encode() + b"\x00")

NR_OPENAT, NR_CLOSE, NR_READ, NR_WRITE = 56, 57, 63, 64
O_CREAT, O_TRUNC, O_WRONLY, O_RDONLY = 0o100, 0o1000, 1, 0
SCR = 0x70000; BUF = 0x71000

def _fs(eng, cpu):
    """VFS 2.0 erzwingen (lazy): ein harmloser openat triggert _fs(); dann liegt eng._os.fs."""
    _put(cpu, SCR, "/proc/self/maps"); _svc(cpu, NR_OPENAT, 0xFFFFFF9C, SCR, O_RDONLY)
    return eng._os.fs

def test_openat_missing_without_creat_is_enoent():
    eng = _os_eng(); cpu = eng.cpu
    _put(cpu, SCR, "/data/app/new.bin")
    fd = _svc(cpu, NR_OPENAT, 0xFFFFFF9C, SCR, O_RDONLY)
    assert fd > _M64 - 0x10000       # negativer errno (ENOENT)

def test_openat_ocreat_creates_and_persists_write():
    eng = _os_eng(); cpu = eng.cpu
    _put(cpu, SCR, "/data/app/created.so")
    fd = _svc(cpu, NR_OPENAT, 0xFFFFFF9C, SCR, O_CREAT | O_WRONLY)
    assert fd < 0x10000              # gueltiger fd
    cpu.m.wb(BUF, b"HELLO")
    n = _svc(cpu, NR_WRITE, fd, BUF, 5); assert n == 5
    _svc(cpu, NR_CLOSE, fd)
    # Datei ist jetzt im VFS -> inode traegt die Bytes
    ino = eng._os.fs.lookup("/data/app/created.so")
    assert ino is not None and bytes(eng._os.fs.read(ino)) == b"HELLO"

def test_openat_otrunc_empties_existing():
    eng = _os_eng(); cpu = eng.cpu
    fs = _fs(eng, cpu)
    fs.add_file("/data/app/pre.bin", b"OLDDATA")
    _put(cpu, SCR, "/data/app/pre.bin")
    fd = _svc(cpu, NR_OPENAT, 0xFFFFFF9C, SCR, O_TRUNC | O_WRONLY)
    assert fd < 0x10000
    ino = eng._os.fs.lookup("/data/app/pre.bin")
    assert bytes(eng._os.fs.read(ino)) == b""

def test_fusion_vfs_read_uses_lookup_and_read():
    # DebuggerController._fusion_vfs_read ohne GUI: nur die Methode isoliert testen
    from ui.controllers.debugger_controller import DebuggerController
    eng = _os_eng()
    fs = _fs(eng, eng.cpu)
    fs.add_file("/data/local/tmp/libx.so", b"\x7fELF-bytes")
    ctrl = DebuggerController.__new__(DebuggerController)   # ohne __init__/GUI
    data = ctrl._fusion_vfs_read(eng, "/data/local/tmp/libx.so")
    assert data == b"\x7fELF-bytes"
    assert ctrl._fusion_vfs_read(eng, "/nope") is None
