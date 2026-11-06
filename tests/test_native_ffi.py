# -*- coding: utf-8 -*-
"""Tests fuer die generische FFI-/Stub-Schicht (A3.1, native_ffi_service):
- synthetischer Dispatch ueber ldr+br durch einen GOT-Slot (deterministisch, ohne ELF),
  einmal im Block- und einmal im Instr-Modus (prueft die Stub-Behandlung in BEIDEN Pfaden);
- libdb0c-Smoke: Import-Relocs-Parser gegen echtes ELF + Wiring bricht die Emulation nicht.
Alles headless (capstone/arm64mini)."""
import os

import pytest

from services.native_emu_service import NativeEmuService
from services.native_ffi_service import NativeFfiService
from services.native_trace_service import NativeTraceService

# Hand-assemblierte AArch64-Instruktionen (little-endian)
LDR_X8_X1 = bytes.fromhex("280040f9")   # ldr x8,[x1]
BR_X8     = bytes.fromhex("00011fd6")   # br  x8   (Tail-Call: lr bleibt = SENT)
_SENT = 0xDEAD0000
_GOT = 0x50000                          # beliebiger Datenslot fuer den Funktionszeiger


def _svc():
    return NativeEmuService(publish=False)


def _setup_call():
    """Engine mit Blob 'ldr x8,[x1]; br x8', einem FFI-Stub 'fake_open' (ret 0x1234)
    und dem GOT-Slot x1 -> Stub. Rueckgabe: (svc, eng, ffi, tr, stub_addr)."""
    svc = _svc(); eng = svc.new_engine()
    ffi = NativeFfiService(publish=False)
    stub = ffi.install_named_stub(eng, "fake_open")
    ffi.set_handler(eng, "fake_open", lambda cpu, f: 0x1234)
    eng.mem.write(_GOT, (stub & ((1 << 64) - 1)).to_bytes(8, "little"))
    svc.load_blob(eng, 0x1000, LDR_X8_X1 + BR_X8)
    return svc, eng, ffi, NativeTraceService(publish=False), stub


def test_dispatch_block_mode():
    svc, eng, ffi, tr, stub = _setup_call()
    tr.begin(eng, 0x1000, [0, _GOT])        # x0=0, x1=GOT
    r = tr.run(eng, maxins=50, mode="block")
    assert r["ok"] and r.get("done"), r
    assert eng.cpu.x[0] == 0x1234, hex(eng.cpu.x[0])
    log = ffi.get_log(eng)
    assert len(log) == 1 and log[0]["name"] == "fake_open", log
    assert log[0]["ret"] == 0x1234


def test_dispatch_insn_mode():
    """Gleicher Aufruf im Instr-Modus — prueft die neue Stub-Behandlung in step_insn."""
    svc, eng, ffi, tr, stub = _setup_call()
    tr.begin(eng, 0x1000, [0, _GOT])
    r = tr.run(eng, maxins=50, mode="insn")
    assert r["ok"] and r.get("done"), r
    assert eng.cpu.x[0] == 0x1234, hex(eng.cpu.x[0])
    log = ffi.get_log(eng)
    assert len(log) == 1 and log[0]["name"] == "fake_open", log


def test_default_return_and_logging():
    """Ohne Handler: Default-Ret greift, Aufruf wird protokolliert."""
    svc, eng, ffi, tr, stub = _setup_call()
    # Handler entfernen, Default auf 0x77 setzen
    eng._ffi.handlers.clear()
    ffi.set_default_return(eng, 0x77)
    tr.begin(eng, 0x1000, [0, _GOT])
    r = tr.run(eng, maxins=50, mode="block")
    assert r["ok"] and r.get("done"), r
    assert eng.cpu.x[0] == 0x77, hex(eng.cpu.x[0])
    assert ffi.summary(eng)["calls"] == 1


# ---- libdb0c-Smoke (uebersprungen, wenn Lib fehlt) ----------------------
def _find_libdb0c():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = [
        os.path.join(here, "tools", "lib", "libdb0c.so"),
        os.path.join(here, "source", "org.nativescript.LibreLinkUp_5_1_1",
                     "base_unpacked_apktool", "lib", "arm64-v8a", "libdb0c.so"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def test_import_relocs_on_libdb0c():
    path = _find_libdb0c()
    if not path:
        pytest.skip("libdb0c.so nicht gefunden")
    from core.native.loader import ElfImage
    img = ElfImage.from_file(path)
    ffi = NativeFfiService(publish=False)
    rels = ffi.import_relocs(img)
    names = {n for _, n in rels}
    assert len(rels) >= 10, f"zu wenige Import-Relocs: {len(rels)}"
    # Mindestens ein paar erwartete libc-Imports muessen dabei sein
    assert names & {"malloc", "free", "pthread_create", "open", "mprotect",
                    "memcpy", "strlen", "fopen", "__errno"}, sorted(names)[:20]


def test_wire_imports_does_not_break_emulation():
    path = _find_libdb0c()
    if not path:
        pytest.skip("libdb0c.so nicht gefunden")
    from core.native.loader import ElfImage
    img = ElfImage.from_file(path)
    svc = _svc(); eng = svc.new_engine()
    n = svc.load_image(eng, img, apply_relocs=True)   # interne Dispatch-Zeiger fuellen
    assert n > 0, "keine Relocs angewandt"
    ffi = NativeFfiService(publish=False)
    w = ffi.wire_imports(eng, img)
    assert w["ok"] and w["count"] > 0, w

    # JNIEnv-Platzhalter (Null-Puffer) wie in den bisherigen Laeufen
    eng.mem.write(0x60000000, b"\x00" * 4096)
    entry = svc.resolve_entry("JNI_OnLoad", img)
    tr = NativeTraceService(publish=False)
    r = tr.debug_run(eng, entry, args=[0x60000000], maxins=2000, mode="block")
    # Verdrahten darf die Emulation nicht schlechter machen: kein Crash, ok=True
    assert r.get("ok"), r
    # Fortschritt mindestens auf dem bekannten Niveau (~170 Bloecke bis Budget)
    assert r.get("trace_len", 0) >= 50, r
