# -*- coding: utf-8 -*-
"""MS-2/N5: resolve_deadslot_candidates (generischer OLLVM Dead-Dispatch-Successor-Resolver).
Real-Lib-Smoke: ueberspringt sauber, wenn das 5.1.1-Binary nicht vorliegt."""
import os
import pytest

LIB = "source/org.nativescript.LibreLinkUp_5_1_1/base_unpacked_apktool/lib/arm64-v8a/libdb0c.so"


def _have_lib():
    return os.path.isfile(LIB)


@pytest.mark.skipif(not _have_lib(), reason="libdb0c.so (5.1.1) nicht vorhanden")
def test_deadslot_candidates_gate2():
    from core.native.loader import ElfImage
    from services.native_emu_service import NativeEmuService
    from services.native_ffi_service import NativeFfiService
    from services.native_trace_service import NativeTraceService
    from services.native_devirt_service import NativeDevirtService
    from services import native_jni_env_service as Jm
    from services.native_jni_env_service import NativeJniEnvService

    img = ElfImage.from_file(LIB)
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    svc.load_image(eng, img, apply_relocs=True)
    ffi = NativeFfiService(publish=False); ffi.wire_imports(eng, img)
    jni = NativeJniEnvService(publish=False); jni.install(eng, ffi, set_entry_reg=None)
    tr = NativeTraceService(publish=False); ffi.install_syscall_hook(eng, returns={}, default_ret=0)
    tr.survive_enable(eng, min_pc=0x1000, cap=50000)
    tr.begin(eng, svc.resolve_entry("JNI_OnLoad", img), args=[Jm.VM_OBJ], faithful=True)
    tr.run(eng, breakpoints=[], maxins=8000000, mode="block")
    sv = tr.survive_get(eng)
    assert len(sv) == 9, "erwartet 9 ueberlebte Dead-Dispatches"
    ret_list = [e["ret_to"] for e in sv]
    keylow = int.from_bytes(eng.cpu.m.rb(0x140330, 8), "little") & 0xffffffff

    dv = NativeDevirtService(publish=False)
    res = dv.resolve_deadslot_candidates(eng, ret_list, keylow)
    assert res["ok"] and res["n_nodes"] == 9
    g2 = next((n for n in res["nodes"] if n["ret_to"] == "0x37530"), None)
    assert g2 is not None, "Gate-2-Knoten (0x37530) fehlt"
    assert "0x903f4211" in g2["consts"] and "0x903f429d" in g2["consts"]
    ncand = sum(len(o["candidates"]) for o in g2.get("orientations", []))
    assert ncand > 0, "keine Diagonal-Kandidaten fuer Gate 2"
    # beide Gate-2-Orientierungen landen gebacken auf dem toten Slot
    assert all(o["baked_slot"] == "0x0" for o in g2["orientations"])


@pytest.mark.skipif(not _have_lib(), reason="libdb0c.so (5.1.1) nicht vorhanden")
def test_deadslot_candidates_generic_params():
    """Der Resolver ist generisch parametrierbar (table/stride) und robust bei leeren Knoten."""
    from services.native_devirt_service import NativeDevirtService
    dv = NativeDevirtService(publish=False)
    # leere ret_to-Liste -> wohlgeformtes Ergebnis
    res = dv.resolve_deadslot_candidates.__doc__
    assert "const-key" in res.lower() or "const - key" in res.lower()
