# -*- coding: utf-8 -*-
"""Tests fuer den native_lift_service (Block->Python-Lifter, headless/arm64mini)."""
from services.native_emu_service import NativeEmuService
from services.native_lift_service import NativeLiftService

LDRB_W0_X0 = bytes.fromhex("00004039")   # ldrb w0,[x0]
B_TO_7008 = bytes.fromhex("01000014")    # b .+4  (0x7004 -> 0x7008)
RET = bytes.fromhex("c0035fd6")          # ret


def _loaded():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    # 0x7000 ldrb ; 0x7004 b 0x7008 ; 0x7008 ret
    svc.load_blob(eng, 0x7000, LDRB_W0_X0 + B_TO_7008 + RET)
    return eng


def test_lift_block_source_and_successor():
    eng = _loaded()
    lift = NativeLiftService()
    r = lift.lift_block(eng, 0x7000)
    assert r["ok"], r
    assert "def _blk(cpu):" in r["source"], r["source"]
    assert "0x7008" in r["successors"], r


def test_lift_block_ret_has_no_static_successor():
    eng = _loaded()
    lift = NativeLiftService()
    r = lift.lift_block(eng, 0x7008)      # ret
    assert r["ok"], r
    assert r["successors"] == [], r       # cpu.lr -> kein statischer Nachfolger


def test_lift_function_walks_blocks():
    eng = _loaded()
    lift = NativeLiftService()
    r = lift.lift_function(eng, 0x7000, max_blocks=16)
    assert r["ok"] and r["count"] == 2, r
    assert r["order"] == ["0x7000", "0x7008"], r
    assert r["truncated"] is False, r


CBZ_W0_8008 = bytes.fromhex("40000034")  # cbz w0, 0x8008


def test_lift_conditional_terminator_both_targets():
    svc = NativeEmuService(publish=False); eng = svc.new_engine()
    # 0x8000 cbz w0,0x8008 ; 0x8004 ret ; 0x8008 ret
    svc.load_blob(eng, 0x8000, CBZ_W0_8008 + RET + RET)
    r = NativeLiftService().lift_block(eng, 0x8000)
    assert r["ok"], r
    assert set(r["successors"]) == {"0x8004", "0x8008"}, r   # genommenes UND Fallthrough-Ziel


def test_lift_inline_select_not_a_successor():
    # 'x[0]=(1 if cond else 0)' im Rumpf darf 0x0 NICHT als Nachfolger erzeugen.
    src = ("def _blk(cpu):\n x=cpu.x; m=cpu.m\n"
           " x[0]=(1 if (cpu.n==cpu.vf) else 0)&0xffffffff\n return (425320 if (cpu.z==0) else 425308)\n")
    succ = NativeLiftService()._successors(src)
    assert succ == [425308, 425320], succ           # 0x67d5c + 0x67d68, KEIN 0x0
