"""Headless-Test der Service-Naht native_emu_service (Phase A / A0).

Prueft: Laden eines Blobs, run-to-completion mit Argumenten, Register-Snapshot,
Speicher-Vorbelegung (mem_overrides) und EventBus-Emission (PROGRESS/DONE).
Keine GUI, keine echte .so noetig.
"""
from services.native_emu_service import (
    NativeEmuService, NATIVE_EMU_PROGRESS, NATIVE_EMU_DONE,
)
from core.application.event_bus import EventBus

# movz x0,#7 ; ret
PROG_CONST = bytes.fromhex("e00080d2") + bytes.fromhex("c0035fd6")
# add x0,x0,x1 ; ret
PROG_ADD = bytes.fromhex("0000018b") + bytes.fromhex("c0035fd6")
BASE = 0x100000


def test_run_const_and_snapshot():
    svc = NativeEmuService()
    eng = svc.new_engine()
    svc.load_blob(eng, BASE, PROG_CONST)
    res = svc.run(eng, BASE)
    assert res["ok"] and res["ret"] == 7
    snap = svc.snapshot_regs(eng)
    assert snap["x"][0] == 7
    assert set(snap["flags"]) == {"N", "Z", "C", "V"}


def test_run_with_args():
    svc = NativeEmuService()
    eng = svc.new_engine()
    svc.load_blob(eng, BASE, PROG_ADD)
    res = svc.run(eng, "0x%x" % BASE, args=(7, 5))   # entry als Hexstring
    assert res["ok"] and res["ret"] == 12


def test_mem_override_and_read():
    svc = NativeEmuService()
    eng = svc.new_engine()
    svc.load_blob(eng, BASE, PROG_CONST)
    svc.run(eng, BASE, mem_overrides=[{"addr": "0x200000", "bytes": "deadbeef"}])
    assert svc.read_mem(eng, 0x200000, 4) == b"\xde\xad\xbe\xef"


def test_eventbus_emission():
    got = {"progress": 0, "done": 0}
    EventBus.subscribe(NATIVE_EMU_PROGRESS, lambda d: got.__setitem__("progress", got["progress"] + 1))
    EventBus.subscribe(NATIVE_EMU_DONE, lambda d: got.__setitem__("done", got["done"] + 1))
    svc = NativeEmuService()
    eng = svc.new_engine()
    svc.load_blob(eng, BASE, PROG_CONST)
    res = svc.run(eng, BASE)
    assert res["ok"]
    assert got["progress"] >= 1 and got["done"] >= 1


def test_bad_entry_is_clean_error():
    svc = NativeEmuService(publish=False)
    eng = svc.new_engine()
    res = svc.run(eng, "nicht_existent")
    assert res["ok"] is False and res["why"] == "entry"
