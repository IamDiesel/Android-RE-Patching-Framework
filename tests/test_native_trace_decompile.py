# -*- coding: utf-8 -*-
"""Tests fuer native_trace_decompile_service (Trace -> de-flattened Pseudocode), headless.

Nutzt dieselbe Zaehlschleife wie test_native_trace: Block-Einstiegsfolge
[0x1000, 0x1004, 0x1004, 0x100c] -> erwartet block / loop(x2) / block.
"""
from services.native_emu_service import NativeEmuService
from services.native_trace_service import NativeTraceService
from services.native_trace_decompile_service import (
    NativeTraceDecompileService, _fold_cycles)

CODE = bytes.fromhex("600080d2" "000400d1" "e0ffffb5" "c0035fd6")
BASE = 0x1000


def _engine_with_trace():
    emu = NativeEmuService(publish=False)
    eng = emu.new_engine()
    emu.load_blob(eng, BASE, CODE)
    NativeTraceService(publish=False).debug_run(eng, BASE, [], maxins=1000, mode="block")
    return eng


def test_fold_cycles_basic():
    # [A,B,B,C] -> block A, loop[B]x2, block C
    assert _fold_cycles([1, 2, 2, 3]) == [
        {"kind": "block", "pc": 1},
        {"kind": "loop", "reps": 2, "body": [2]},
        {"kind": "block", "pc": 3},
    ]


def test_fold_cycles_multiblock():
    # [A,B,A,B,A,B,C] -> loop[A,B]x3, block C
    assert _fold_cycles([1, 2, 1, 2, 1, 2, 9]) == [
        {"kind": "loop", "reps": 3, "body": [1, 2]},
        {"kind": "block", "pc": 9},
    ]


def test_decompile_trace_flow_and_blocks():
    eng = _engine_with_trace()
    dec = NativeTraceDecompileService().decompile_trace(eng)
    assert dec["ok"], dec
    assert dec["entry"] == "0x1000"
    assert dec["visits"] == 4 and dec["unique"] == 3, dec
    kinds = [(f["kind"], f.get("reps")) for f in dec["flow"]]
    assert kinds == [("block", None), ("loop", 2), ("block", None)], kinds
    # Schleifenkoerper ist der doppelte Block 0x1004
    loop = [f for f in dec["flow"] if f["kind"] == "loop"][0]
    assert loop["body"] == ["0x1004"], loop
    # jeder eindeutige Block wurde geliftet (hat Source)
    for k in ("0x1000", "0x1004", "0x100c"):
        assert dec["blocks"][k]["ok"] and dec["blocks"][k]["source"], (k, dec["blocks"][k])


def test_pseudocode_text_renders_loop_and_blocks():
    eng = _engine_with_trace()
    dec = NativeTraceDecompileService().decompile_trace(eng)
    pc = dec["pseudocode"]
    assert "loop (x2)" in pc, pc
    assert "block 0x1000" in pc and "block 0x1004" in pc and "block 0x100c" in pc, pc
    assert "De-flattened" in pc


def test_empty_trace_is_handled():
    emu = NativeEmuService(publish=False)
    eng = emu.new_engine()
    dec = NativeTraceDecompileService().decompile_trace(eng, [])
    assert not dec["ok"] and dec["why"] == "empty", dec
