# -*- coding: utf-8 -*-
"""Bit-exaktes Gate fuer native_pylift_service: emittiertes Inline-Python == cpu.step.

Pro Instruktionsform: identischer Startzustand auf zwei frischen CPUs, einmal via
cpu.step(ins) (Referenz/Wahrheit), einmal via emittiertem Inline-Python. Vergleich von
x[0..30], sp, lr, Flags und der betroffenen Speicherstelle.
"""
from services.native_emu_service import NativeEmuService
from services.native_pylift_service import NativePyLiftService

M64 = (1 << 64) - 1
SVC = NativePyLiftService()


def _cpu():
    return NativeEmuService(publish=False).new_engine().cpu


def _snapshot(cpu, memaddrs):
    return {
        "x": list(cpu.x), "sp": cpu.sp & M64, "lr": cpu.lr & M64,
        "flags": (cpu.n, cpu.z, cpu.c, cpu.vf),
        "mem": {a: cpu.m.r(a, 8) for a in memaddrs},
    }


def _run_emit(cpu, mnem, ops):
    code, ok = SVC.emit_insn(mnem, ops)
    assert ok, (mnem, ops, "nicht abgedeckt")
    body = "def _f(cpu):\n x=cpu.x; m=cpu.m\n" + "\n".join(" " + c for c in code) + "\n"
    g = {"M64": M64}
    exec(body, g)
    g["_f"](cpu)


def _gate(mnem, ops, setup, memaddrs=()):
    """setup(cpu) praepariert Register/Speicher identisch; vergleicht step vs emit."""
    ref = _cpu(); setup(ref); ref.step((0, mnem, ops))
    emt = _cpu(); setup(emt); _run_emit(emt, mnem, ops)
    assert _snapshot(ref, memaddrs) == _snapshot(emt, memaddrs), (mnem, ops)


def test_mrs_tpidr_zero():
    def s(c): c.x[8] = 0x1234
    _gate("mrs", "x8, tpidr_el0", s)


def test_udiv_normal_and_div0():
    def s(c): c.x[8] = 100; c.x[14] = 7
    _gate("udiv", "x15, x8, x14", s)

    def s0(c): c.x[8] = 100; c.x[14] = 0
    _gate("udiv", "x15, x8, x14", s0)


def test_sub_sp_imm():
    def s(c): c.sp = 0x1000
    _gate("sub", "sp, sp, #0xa0", s)


def test_mov_sp_reg():
    def s(c): c.x[25] = 0x2000
    _gate("mov", "sp, x25", s)


def test_ldaxr_w():
    def s(c): c.x[8] = 0x8000; c.m.w(0x8000, 0xDEADBEEF, 4)
    _gate("ldaxr", "w8, [x8]", s, memaddrs=(0x8000,))


def test_stlxr_w_sets_status_zero():
    def s(c): c.x[9] = 0x9000; c.x[8] = 0x1234; c.x[10] = 0xFF
    _gate("stlxr", "w10, w8, [x9]", s, memaddrs=(0x9000,))


def test_stlr_wzr_stores_zero():
    def s(c): c.x[8] = 0xA000; c.m.w(0xA000, 0xFFFFFFFF, 4)
    _gate("stlr", "wzr, [x8]", s, memaddrs=(0xA000,))


def test_lift_block_inline_hole_free_on_real_prologue():
    # JNI_OnLoad-Prolog @0x36924 enthaelt sub sp,#..a0 / mrs tpidr_el0 -> muss lueckenfrei werden.
    from core.native.loader import ElfImage
    emu = NativeEmuService(publish=False); eng = emu.new_engine()
    emu.load_image(eng, ElfImage.from_file("tools/lib/libdb0c.so"), apply_relocs=True)
    r = SVC.lift_block_inline(eng, 0x36924)
    assert r["ok"] and r["hole_free"], r.get("residual_holes")
    assert "cpu.step(INS[" not in r["source"], r["source"]
