# -*- coding: utf-8 -*-
"""Tests fuer die geteilte Debug-Sitzung: save -> peek -> load stellt pc/Flags/SIMD/Register/
Speicher/Trace exakt wieder her, und Weiter-Stepping funktioniert nach dem Laden."""
from services.native_emu_service import NativeEmuService
from services.native_trace_service import NativeTraceService
from services.native_debug_session_service import NativeDebugSessionService

LOOP = bytes.fromhex("600080d2" "000400d1" "e0ffffb5" "c0035fd6")  # movz x0,#3; sub; cbnz; ret


def _engine_stepped(emu):
    eng = emu.new_engine(); emu.load_blob(eng, 0x1000, LOOP)
    tr = NativeTraceService(publish=False)
    tr.begin(eng, 0x1000, [], faithful=True)
    tr.step_block(eng); tr.step_block(eng)   # 2 Schritte -> pc=0x1004, x0 veraendert
    return eng, tr


def test_save_peek_load_roundtrip(tmp_path):
    emu = NativeEmuService(publish=False)
    eng, tr = _engine_stepped(emu)
    # etwas SIMD + Flags setzen, um die Wiederherstellung zu pruefen
    eng.cpu.v[3] = bytearray(range(16)); eng.cpu.z = 1
    ref_pc = eng.cpu.pc; ref_x = list(eng.cpu.x); ref_v3 = bytes(eng.cpu.v[3])
    ref_trace = [e["pc"] for e in tr.get_trace(eng)]

    sess = NativeDebugSessionService()
    folder = str(tmp_path / "dbg")
    r = sess.save(eng, folder, meta={"entry": "0x1000", "relocs": False}, writer="gui")
    assert r["ok"] and r["seq"] == 1 and r["trace"] == len(ref_trace), r

    # leichtes peek (ohne Snapshot)
    pk = sess.peek(folder)
    assert pk["meta"]["pc"] == ref_pc and pk["meta"]["entry"] == "0x1000"
    assert [e["pc"] for e in pk["trace"]] == ref_trace

    # in frische Engine laden
    eng2 = emu.new_engine(); emu.load_blob(eng2, 0x1000, LOOP)
    ld = sess.load(eng2, folder)
    assert ld["ok"] and ld["pc"] == f"0x{ref_pc:x}", ld
    assert eng2.cpu.pc == ref_pc
    assert list(eng2.cpu.x) == ref_x
    assert eng2.cpu.z == 1
    assert bytes(eng2.cpu.v[3]) == ref_v3
    assert [e["pc"] for e in eng2._trace] == ref_trace


def test_continue_stepping_after_load(tmp_path):
    emu = NativeEmuService(publish=False)
    eng, tr = _engine_stepped(emu)
    sess = NativeDebugSessionService()
    folder = str(tmp_path / "dbg")
    sess.save(eng, folder, meta={"entry": "0x1000"}, writer="gui")

    eng2 = emu.new_engine(); emu.load_blob(eng2, 0x1000, LOOP)
    sess.load(eng2, folder)
    tr2 = NativeTraceService(publish=False)
    # Weiter-Stepping schreibt an den geladenen Trace an und laeuft zu Ende
    r = tr2.run(eng2, maxins=50, mode="block")
    assert r["ok"] and r.get("done"), r
    assert eng2.cpu.x[0] == 0            # Schleife zu Ende gelaufen
    # Trace wuchs ueber den geladenen Stand hinaus
    assert len(tr2.get_trace(eng2)) >= 3


def test_seq_increments_on_resave(tmp_path):
    emu = NativeEmuService(publish=False)
    eng, _ = _engine_stepped(emu)
    sess = NativeDebugSessionService()
    folder = str(tmp_path / "dbg")
    assert sess.save(eng, folder, writer="gui")["seq"] == 1
    assert sess.save(eng, folder, writer="ki")["seq"] == 2
    assert sess.peek(folder, with_trace=False)["meta"]["writer"] == "ki"


def test_conflict_guard_blocks_overwrite(tmp_path):
    emu = NativeEmuService(publish=False)
    eng, _ = _engine_stepped(emu)
    sess = NativeDebugSessionService()
    folder = str(tmp_path / "dbg")
    assert sess.save(eng, folder, writer="ki")["seq"] == 1
    assert sess.save(eng, folder, writer="ki")["seq"] == 2
    c = sess.save(eng, folder, writer="gui", base_seq=1)          # Datei ist schon seq 2 -> conflict
    assert not c["ok"] and c["why"] == "conflict" and c["file_seq"] == 2, c
    f = sess.save(eng, folder, writer="gui", base_seq=1, force=True)
    assert f["ok"] and f["seq"] == 3, f


def test_backup_history_created(tmp_path):
    import os
    emu = NativeEmuService(publish=False)
    eng, _ = _engine_stepped(emu)
    sess = NativeDebugSessionService()
    folder = str(tmp_path / "dbg")
    sess.save(eng, folder, writer="ki")                           # seq 1 (nichts zu sichern)
    sess.save(eng, folder, writer="ki")                           # seq 2 (sichert seq 1)
    hist = os.path.join(folder, "history")
    assert os.path.isdir(hist)
    assert any(d.startswith("seq_1_") for d in os.listdir(hist)), os.listdir(hist)
