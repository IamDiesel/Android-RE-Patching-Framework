# -*- coding: utf-8 -*-
"""Tests fuer N5-Werkzeug / M7: Devirt-Hybrid-Merge (statisch ⊕ getracter Zustand).

Hermetisch: testet die generische Merge-Logik mit synthetischen devirt_static- +
deflatten-Ausgaben (kein Engine/keine Lib noetig)."""
from services.native_devirt_service import NativeDevirtService


def test_merge_resolves_branch_and_trap():
    dv = NativeDevirtService(publish=False)
    static = {
        "entry": "0x3ee94", "n_blocks": 3, "n_branch_points": 1, "n_traps": 1,
        "branch_points": ["0xa6d34"],
        "traps": ["0x45eb8"],
        "blocks": {
            "0x3ee94": {"node_type": "index", "successors": ["0xa6d34"], "index": 5},
            "0xa6d34": {"node_type": "branch", "successors": [], "index": None},
            "0x45eb8": {"node_type": "index", "successors": [], "index": None},
        },
    }
    traced = {
        "successors": {
            "0x3ee94": {"0xa6d34": 3},
            "0xa6d34": {"0xb000": 2, "0xc000": 1},   # Branch: 2 reale Nachfolger
            "0x45eb8": {"0xd000": 1},                # Trap -> realer Nachfolger
        },
        "unique_handlers": ["0x3ee94", "0xa6d34", "0x45eb8", "0xb000", "0xc000", "0xd000"],
        "sequence": ["0x3ee94", "0xa6d34", "0xb000"],
    }
    m = dv.merge_hybrid(static, traced)
    assert m["ok"]
    b = m["blocks"]["0xa6d34"]
    assert b["is_branch_point"] and b["resolved"]
    assert {t["target"] for t in b["traced_successors"]} == {"0xb000", "0xc000"}
    assert m["n_resolved_traps"] == 1
    assert m["resolved_traps"][0]["block"] == "0x45eb8"
    assert m["resolved_traps"][0]["traced_successors"] == ["0xd000"]
    assert m["coverage"] == 1.0
    assert m["n_static_branch_points"] == 1 and m["n_static_traps"] == 1


def test_merge_handles_int_addrs_and_multi_format():
    dv = NativeDevirtService(publish=False)
    static = {"entry": 0x100,
              "blocks": {0x100: {"node_type": "index", "successors": [0x200]}},
              "branch_points": [0x100], "traps": []}
    traced = {"successors": {"0x100": {"0x200": {"count": 5, "runs": 2}}},   # deflatten_multi-Format
              "unique_handlers": ["0x100", "0x200"], "sequence": []}
    m = dv.merge_hybrid(static, traced)
    tt = m["blocks"]["0x100"]["traced_successors"]
    assert tt and tt[0]["target"] == "0x200" and tt[0]["count"] == 5
    assert m["coverage"] == 1.0
    assert m["blocks"]["0x100"]["is_branch_point"] is True


def test_merge_no_trace_static_only():
    dv = NativeDevirtService(publish=False)
    static = {"entry": "0x1",
              "blocks": {"0x1": {"node_type": "index", "successors": ["0x2"]}},
              "branch_points": [], "traps": []}
    m = dv.merge_hybrid(static, None)
    assert m["coverage"] == 0.0
    assert m["blocks"]["0x1"]["reached"] is False
    assert m["blocks"]["0x1"]["traced_successors"] == []
    assert m["n_resolved_traps"] == 0


def test_traced_only_block_appears():
    dv = NativeDevirtService(publish=False)
    static = {"entry": "0x1", "blocks": {"0x1": {"node_type": "index", "successors": ["0x2"]}},
              "branch_points": [], "traps": []}
    traced = {"successors": {"0x1": {"0x2": 1}, "0x2": {"0x9": 1}},
              "unique_handlers": ["0x1", "0x2", "0x9"], "sequence": ["0x1", "0x2", "0x9"]}
    m = dv.merge_hybrid(static, traced)
    # 0x2 ist nur im Trace (nicht in static blocks) -> taucht als traced-only auf
    assert "0x2" in m["blocks"]
    assert m["blocks"]["0x2"]["node_type"] == "traced-only"
    # coverage zaehlt nur statische Bloecke
    assert m["n_static_blocks"] == 1 and m["coverage"] == 1.0
