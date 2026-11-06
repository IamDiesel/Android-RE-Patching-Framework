# -*- coding: utf-8 -*-
"""Tests fuer die Ausfuehrungs-Hebel im native_emu_service:
Pointer/Buffer-Argumente, Stubs, Snapshot (alles headless, capstone/arm64mini)."""
import os
import tempfile

from services.native_emu_service import NativeEmuService

# Hand-assemblierte AArch64-Bloecke (little-endian)
LDRB_W0_X0 = bytes.fromhex("0000 4039".replace(" ", ""))   # ldrb w0,[x0]
STRB_W1_X0 = bytes.fromhex("0100 0039".replace(" ", ""))   # strb w1,[x0]
RET = bytes.fromhex("c003 5fd6".replace(" ", ""))          # ret


def _svc():
    return NativeEmuService(publish=False)


def test_pointer_arg_reads_buffer():
    svc = _svc(); eng = svc.new_engine()
    svc.load_blob(eng, 0x1000, LDRB_W0_X0 + RET)
    res = svc.run(eng, 0x1000, [{"ptr": "7a"}])
    assert res["ok"], res
    assert res["ret"] == 0x7a, res


def test_out_buffer_written_by_function():
    svc = _svc(); eng = svc.new_engine()
    svc.load_blob(eng, 0x2000, STRB_W1_X0 + RET)
    res = svc.run(eng, 0x2000, [{"buf": 4, "name": "o"}, 0x5a])
    assert res["ok"], res
    assert "out" in res and res["out"]["o"].startswith("5a"), res


def test_stub_returns_constant():
    svc = _svc(); eng = svc.new_engine()
    svc.load_blob(eng, 0x3000, RET)
    svc.install_stubs(eng, [{"at": 0x3000, "ret": 42}])
    res = svc.run(eng, 0x3000, [])
    assert res["ok"] and res["ret"] == 42, res


def test_snapshot_roundtrip():
    svc = _svc(); eng = svc.new_engine()
    eng.mem.write(0x4000, b"\xAA\xBB\xCC\xDD")
    fd, path = tempfile.mkstemp(suffix=".pkl.xz"); os.close(fd)
    try:
        svc.save_snapshot(eng, path)
        eng.mem.write(0x4000, b"\x00\x00\x00\x00")
        assert svc.read_mem(eng, 0x4000, 4) == b"\x00\x00\x00\x00"
        svc.load_snapshot(eng, path)
        assert svc.read_mem(eng, 0x4000, 4) == b"\xAA\xBB\xCC\xDD"
    finally:
        os.remove(path)


def test_alloc_bump_is_monotonic_and_aligned():
    svc = _svc(); eng = svc.new_engine()
    a = svc.alloc(eng, b"\x01\x02\x03")
    b = svc.alloc(eng, b"\x04")
    assert b > a and (a % 16 == 0) and (b % 16 == 0), (hex(a), hex(b))
    assert svc.read_mem(eng, a, 3) == b"\x01\x02\x03"


def test_breakpoint_pauses_before_ret():
    svc = _svc(); eng = svc.new_engine()
    # BP feuert nur an Basisblock-Eintritts-PCs (nicht mitten im Block) -> Blockgrenze bei 0x5008:
    # 0x5000 ldrb w0,[x0] ; 0x5004 b 0x5008 ; 0x5008 ret  -> BP @0x5008 haelt NACH ldrb, VOR ret
    B_TO_5008 = bytes.fromhex("01000014")   # b 0x5008
    svc.load_blob(eng, 0x5000, LDRB_W0_X0 + B_TO_5008 + RET)
    res = svc.run_with_breaks(eng, 0x5000, [{"ptr": "7a"}], breakpoints=[0x5008])
    assert res["ok"] and res.get("paused") is True, res
    assert res["bp"] == 0x5008, res
    assert res["x"][0] == 0x7a, res          # ldrb lief, ret noch nicht
    assert "ret" not in res, res


def test_breakpoint_not_hit_runs_to_completion():
    svc = _svc(); eng = svc.new_engine()
    svc.load_blob(eng, 0x6000, LDRB_W0_X0 + RET)
    res = svc.run_with_breaks(eng, 0x6000, [{"ptr": "5a"}], breakpoints=[0x6ffc])
    assert res["ok"] and res.get("paused") is False, res
    assert res["ret"] == 0x5a, res
    # Probes muessen nach dem Lauf wieder entfernt sein
    assert 0x6ffc not in eng.cpu.probes, list(eng.cpu.probes)


def test_step_block_finishes_function():
    svc = _svc(); eng = svc.new_engine()
    B_TO_5008 = bytes.fromhex("01000014")  # b 0x5008
    svc.load_blob(eng, 0x5000, LDRB_W0_X0 + B_TO_5008 + RET)
    p = svc.run_with_breaks(eng, 0x5000, [{"ptr": "7a"}], breakpoints=[0x5008])
    assert p["paused"], p
    s = svc.step(eng)                      # ret ausfuehren -> fertig
    assert s["ok"] and s["done"] is True, s
    assert s["ret"] == 0x7a, s


def test_resume_runs_to_completion():
    svc = _svc(); eng = svc.new_engine()
    B_TO_5008 = bytes.fromhex("01000014")  # b 0x5008
    svc.load_blob(eng, 0x5000, LDRB_W0_X0 + B_TO_5008 + RET)
    p = svc.run_with_breaks(eng, 0x5000, [{"ptr": "33"}], breakpoints=[0x5008])
    assert p["paused"], p
    r = svc.resume(eng)
    assert r["ok"] and r.get("paused") is False and r.get("done") is True, r
    assert r["ret"] == 0x33, r


def test_resume_stops_at_next_breakpoint():
    svc = _svc(); eng = svc.new_engine()
    B = bytes.fromhex("01000014")          # b .+4 (naechster Block)
    # 0x5000 ldrb ; 0x5004 b 0x5008 ; 0x5008 b 0x500c ; 0x500c ret
    svc.load_blob(eng, 0x5000, LDRB_W0_X0 + B + B + RET)
    p = svc.run_with_breaks(eng, 0x5000, [{"ptr": "7a"}], breakpoints=[0x5008, 0x500c])
    assert p["paused"] and p["bp"] == 0x5008, p
    r = svc.resume(eng, breakpoints=[0x500c])   # ueber 0x5008 steppen, dann bis 0x500c
    assert r["ok"] and r.get("paused") is True, r
    assert r["bp"] == 0x500c, r
    assert r["x"][0] == 0x7a, r
