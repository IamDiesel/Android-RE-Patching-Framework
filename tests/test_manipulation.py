# -*- coding: utf-8 -*-
"""P2 Aktionspunkte: bedingte/funktionale/getriggerte Datenmanipulation + Sicherheit. Headless."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.debug import JavaDebugger

# run(): v0=5, v1=3, return v0   -> ohne Eingriff 5. Instruktions-PCs: 0 const v0, 1 const v1, 2 return
RUN = """.class public Lt/M;
.super Ljava/lang/Object;
.method public static run()I
    .registers 2
    const v0, 0x5
    const v1, 0x3
    return v0
.end method
"""


def _dbg():
    vm = DalvikVM()
    for md in Sm.parse_class(RUN)[1]:
        vm.register(md)
    return JavaDebugger(vm), vm


def _run_to_done(jdb, vm):
    st = jdb.start("Lt/M;", "run", "()I", (), stop_at_entry=False)
    # falls an einem (pause-)Aktionspunkt gehalten: weiterlaufen bis fertig
    for _ in range(10):
        if jdb.mode in ("done", "stopped", "error"):
            break
        jdb.cont()
    return jdb.result


def test_constant_manipulation_continue():
    jdb, vm = _dbg()
    # an pc=2 (vor 'return v0') v0 := 99 setzen, OHNE Halt
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "v0", "value": 99, "const": True}],
                         when_continue=True)
    assert _run_to_done(jdb, vm) == 99


def test_functional_manipulation():
    jdb, vm = _dbg()
    # funktional: v0 := v0 + v1*10  (= 5 + 30 = 35)
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "v0", "value": "v0 + v1*10", "const": False}],
                         when_continue=True)
    assert _run_to_done(jdb, vm) == 35


def test_conditional_fires_and_skips():
    jdb, vm = _dbg()
    jdb.add_action_point("Lt/M;", "run", "()I", 2, cond="v1 == 3",
                         assigns=[{"target": "v0", "value": 7, "const": True}], when_continue=True)
    assert _run_to_done(jdb, vm) == 7            # Bedingung erfuellt -> gefeuert
    jdb2, vm2 = _dbg()
    jdb2.add_action_point("Lt/M;", "run", "()I", 2, cond="v1 == 999",
                          assigns=[{"target": "v0", "value": 7, "const": True}], when_continue=True)
    assert _run_to_done(jdb2, vm2) == 5          # Bedingung falsch -> kein Eingriff


def test_triggered_pause_default():
    jdb, vm = _dbg()
    # ohne when_continue -> haelt an (Pause) beim Zuenden
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "v0", "value": 42, "const": True}])
    st = jdb.start("Lt/M;", "run", "()I", (), stop_at_entry=False)
    assert st["mode"] == "paused"                # getriggert + Halt
    jdb.cont()
    assert jdb.result == 42                       # Manipulation hat gewirkt


def test_one_shot_and_hitcount():
    jdb, vm = _dbg()
    ap = jdb.add_action_point("Lt/M;", "run", "()I", 2,
                              assigns=[{"target": "v0", "value": 1, "const": True}],
                              when_continue=True, one_shot=True)
    assert _run_to_done(jdb, vm) == 1
    assert ap not in jdb.action_points           # one_shot entfernt


def test_security_no_calls_in_value():
    jdb, vm = _dbg()
    # bösartiger Ausdruck -> safe_eval lehnt Calls ab -> Zuweisung uebersprungen, Ergebnis unveraendert
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "v0", "value": "__import__('os').system('x')", "const": False}],
                         when_continue=True)
    assert _run_to_done(jdb, vm) == 5            # kein Eingriff


def test_static_target():
    jdb, vm = _dbg()
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "Lt/M;.flag", "value": 123, "const": True}],
                         when_continue=True)
    _run_to_done(jdb, vm)
    assert vm.statics.get("Lt/M;", {}).get("flag") == 123   # Static gesetzt


def test_marker_recorded_on_manipulation():
    """Manipulation hinterlaesst einen Zeitachsen-Marker (fuer den Timeline-Scrubber)."""
    jdb, vm = _dbg()
    jdb.add_action_point("Lt/M;", "run", "()I", 2,
                         assigns=[{"target": "v0", "value": 99, "const": True}],
                         when_continue=True)
    _run_to_done(jdb, vm)
    ms = [m for m in jdb.marker_list() if m["kind"] == "manip"]
    assert len(ms) == 1
    assert "v0" in ms[0]["label"]
    assert isinstance(ms[0]["step"], int)
