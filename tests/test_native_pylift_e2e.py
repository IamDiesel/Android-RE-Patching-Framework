# -*- coding: utf-8 -*-
"""End-to-End-Gate fuer den Pfad-Lift (lift_to_function): die emittierte Python-Funktion
reproduziert den Endzustand des emulierten Laufs bit-exakt.

Vergleich: nach dem emulierten Trace-Lauf (Referenz) vs. nach Ausfuehrung der gelifteten
Funktion auf einer frischen, identisch initialisierten Engine -> x[0..30] + sp (+ lr).
"""
from core.native.loader import ElfImage
from services.native_emu_service import NativeEmuService
from services.native_trace_service import NativeTraceService
from services.native_trace_decompile_service import NativeTraceDecompileService

SENT = 0xDEAD0000
LOOP = bytes.fromhex("600080d2" "000400d1" "e0ffffb5" "c0035fd6")  # movz x0,#3; sub; cbnz; ret


def test_lift_to_function_small_loop_e2e():
    emu = NativeEmuService(publish=False)
    eng = emu.new_engine(); emu.load_blob(eng, 0x1000, LOOP)
    tr = NativeTraceService(publish=False)
    tr.begin(eng, 0x1000, [], faithful=True)
    tr.run(eng, maxins=1000, mode="block")
    ref_x, ref_sp = list(eng.cpu.x), eng.cpu.sp

    dec = NativeTraceDecompileService()
    r = dec.lift_to_function(eng)
    assert r["ok"], r
    assert "for _i0 in range(2)" in r["source"], r["source"]      # Schleife gefaltet

    eng2 = emu.new_engine(); emu.load_blob(eng2, 0x1000, LOOP)
    eng2.cpu.lr = SENT
    dec.run_lifted(eng2, r["source"])
    assert list(eng2.cpu.x) == ref_x, (eng2.cpu.x[0], ref_x[0])
    assert eng2.cpu.sp == ref_sp
    assert eng2.cpu.x[0] == 0


def test_lift_to_function_libdb0c_e2e():
    emu = NativeEmuService(publish=False)
    img = ElfImage.from_file("tools/lib/libdb0c.so")
    eng = emu.new_engine(); emu.load_image(eng, img, apply_relocs=True)
    buf = emu.alloc(eng, size=4096); stk = emu.alloc(eng, size=0x10000); sp = stk + 0x10000 - 0x100
    tr = NativeTraceService(publish=False)
    tr.begin(eng, 0x36924, [buf], sp=sp, faithful=True)
    tr.run(eng, maxins=250, mode="block")
    ref_x, ref_sp, ref_lr = list(eng.cpu.x), eng.cpu.sp, eng.cpu.lr

    dec = NativeTraceDecompileService()
    r = dec.lift_to_function(eng)
    assert r["ok"], r.get("why") or r

    eng2 = emu.new_engine(); emu.load_image(eng2, img, apply_relocs=True)
    buf2 = emu.alloc(eng2, size=4096); stk2 = emu.alloc(eng2, size=0x10000); sp2 = stk2 + 0x10000 - 0x100
    assert buf2 == buf and sp2 == sp, (hex(buf2), hex(buf), hex(sp2), hex(sp))  # alloc deterministisch
    eng2.cpu.x[0] = buf2; eng2.cpu.sp = sp2; eng2.cpu.lr = SENT
    dec.run_lifted(eng2, r["source"])

    diff = [(i, hex(a), hex(b)) for i, (a, b) in enumerate(zip(eng2.cpu.x, ref_x)) if a != b]
    assert not diff, diff
    assert eng2.cpu.sp == ref_sp, (hex(eng2.cpu.sp), hex(ref_sp))
    assert eng2.cpu.lr == ref_lr, (hex(eng2.cpu.lr), hex(ref_lr))
