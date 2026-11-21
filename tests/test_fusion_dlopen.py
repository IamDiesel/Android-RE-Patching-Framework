# -*- coding: utf-8 -*-
"""#355: natives dlopen/dlsym laedt die von der App selbst entpackte Lib aus dem VFS.
Das OS entpackt nichts. Injizierter Parser/Autoloader; Dispatch ueber die echte FFI-Bruecke."""
import types
from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_jni_env_service import NativeJniEnvService
from core.java.native_link import NativeMethodTable
from core.java.fusion_dlopen import install_native_dlopen


def _setup():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    jni = NativeJniEnvService(publish=False); jni.install(eng, ffi, set_entry_reg=None)
    table = NativeMethodTable()
    return svc, eng, ffi, jni, table


def test_native_dlopen_loads_from_vfs_and_dlsym_resolves():
    svc, eng, ffi, jni, table = _setup()
    loaded = []
    vfs = {"/data/data/app/files/libX.so": b"PLAINTEXT-ELF"}
    fake_img = types.SimpleNamespace(dynsyms={"Java_com_app_nat": types.SimpleNamespace(value=0x5000)})
    st = install_native_dlopen(
        eng, ffi, svc, jni, table,
        vfs_read=lambda p: vfs.get(p),
        image_parser=lambda data: fake_img,
        autoloader=lambda svc, eng, jni, img, table: loaded.append(img))
    # Pfad + Symbolname in den Speicher legen
    svc.load_blob(eng, 0x8000, b"/data/data/app/files/libX.so\x00")
    svc.load_blob(eng, 0x8100, b"Java_com_app_nat\x00")
    dlopen = eng.cpu.stub[ffi.stub_of(eng, "dlopen")]
    dlsym = eng.cpu.stub[ffi.stub_of(eng, "dlsym")]
    # dlopen(path)
    eng.cpu.x[0] = 0x8000
    dlopen(eng.cpu)
    h = int(eng.cpu.x[0])
    assert h >= 0xD000 and loaded and loaded[0] is fake_img
    # dlsym(handle, "Java_com_app_nat") -> 0x5000
    eng.cpu.x[0] = h; eng.cpu.x[1] = 0x8100
    dlsym(eng.cpu)
    assert int(eng.cpu.x[0]) == 0x5000


def test_dlopen_unknown_path_returns_zero():
    svc, eng, ffi, jni, table = _setup()
    install_native_dlopen(eng, ffi, svc, jni, table, vfs_read=lambda p: None,
                          image_parser=lambda d: None, autoloader=lambda *a: None)
    svc.load_blob(eng, 0x8000, b"/nicht/im/vfs.so\x00")
    dlopen = eng.cpu.stub[ffi.stub_of(eng, "dlopen")]
    eng.cpu.x[0] = 0x8000
    dlopen(eng.cpu)
    assert int(eng.cpu.x[0]) == 0     # nichts entpackt -> kein Handle


def test_android_dlopen_ext_registered():
    svc, eng, ffi, jni, table = _setup()
    install_native_dlopen(eng, ffi, svc, jni, table, vfs_read=lambda p: None)
    assert ffi.stub_of(eng, "android_dlopen_ext") is not None
    assert ffi.stub_of(eng, "dlsym") is not None


def test_dlopen_dedupes_same_path():
    """F6: gleicher Pfad -> gleiches Handle, Autoloader nur einmal (kein erneutes JNI_OnLoad)."""
    import types
    svc, eng, ffi, jni, table = _setup()
    loaded = []
    fake_img = types.SimpleNamespace(dynsyms={})
    install_native_dlopen(eng, ffi, svc, jni, table,
                          vfs_read=lambda p: b"ELF",
                          image_parser=lambda d: fake_img,
                          autoloader=lambda *a: loaded.append(1))
    svc.load_blob(eng, 0x8000, b"/data/x/lib.so\x00")
    dlopen = eng.cpu.stub[ffi.stub_of(eng, "dlopen")]
    eng.cpu.x[0] = 0x8000; dlopen(eng.cpu); h1 = int(eng.cpu.x[0])
    eng.cpu.x[0] = 0x8000; dlopen(eng.cpu); h2 = int(eng.cpu.x[0])
    assert h1 == h2 and len(loaded) == 1
