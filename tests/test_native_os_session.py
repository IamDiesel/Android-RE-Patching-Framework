# -*- coding: utf-8 -*-
"""G-OS-5: NativeOsSessionService (seq/writer/Clobber)."""
from services.native_os_session_service import NativeOsSessionService


def test_save_peek_seq(tmp_path):
    s = NativeOsSessionService(); f = str(tmp_path)
    r = s.save(f, config={"rand_seed": 7}, writer="ki")
    assert r["ok"] and r["seq"] == 1
    sess = s.peek(f)["session"]
    assert sess["config"]["rand_seed"] == 7 and sess["writer"] == "ki"
    r2 = s.save(f, config={"rand_seed": 8}, writer="gui")
    assert r2["seq"] == 2


def test_clobber_conflict(tmp_path):
    s = NativeOsSessionService(); f = str(tmp_path)
    s.save(f, config={"a": 1}, writer="ki")          # seq 1
    s.save(f, config={"a": 2}, writer="gui")         # seq 2
    # KI schreibt mit veraltetem base_seq=1 -> Konflikt
    r = s.save(f, config={"a": 3}, writer="ki", base_seq=1)
    assert r["ok"] is False and r["why"] == "conflict" and r["file_seq"] == 2
    # force ueberschreibt
    r2 = s.save(f, config={"a": 3}, writer="ki", base_seq=1, force=True)
    assert r2["ok"] and r2["seq"] == 3


def test_summary_preserved_when_only_config(tmp_path):
    s = NativeOsSessionService(); f = str(tmp_path)
    s.save(f, summary={"coverage": {"distinct": 5}}, writer="run")
    s.save(f, config={"x": 1}, writer="ki")          # summary nicht uebergeben -> erhalten
    sess = s.peek(f)["session"]
    assert sess["summary"]["coverage"]["distinct"] == 5 and sess["config"]["x"] == 1
