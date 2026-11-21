# -*- coding: utf-8 -*-
"""Tests: NativeDevirtService.deflatten — Sequenz, eindeutige Bloecke, Traps, Nachfolger-Graph."""
from services.native_devirt_service import NativeDevirtService


class _FakeIns:
    def __init__(self, mnem="ret", ops="", size=4):
        self.mnemonic = mnem; self.op_str = ops; self.size = size


class _FakeEng:
    """decode_at liefert sofort einen Terminator -> Bloecke sind 1 Instruktion lang."""
    def decode_at(self, p):
        return _FakeIns("ret", "", 4)


def test_deflatten_sequence_unique_traps_successors():
    log = [{"val": 0x100}, {"val": 0x200}, {"val": 0x100}, {"val": 0}, {"val": 0x200}]
    dv = NativeDevirtService(publish=False).deflatten(_FakeEng(), log)
    assert dv["ok"]
    assert dv["n_dispatches"] == 5
    assert dv["n_unique"] == 2
    assert dv["n_traps"] == 1
    assert dv["sequence"] == ["0x100", "0x200", "0x100", "TRAP", "0x200"]
    assert dv["unique_handlers"] == ["0x100", "0x200"]
    # 0x100 folgt auf 0x200 (idx0->1) und auf TRAP (idx2->3)
    assert dv["successors"]["0x100"]["0x200"] == 1
    assert dv["successors"]["0x100"]["TRAP"] == 1
    assert dv["successors"]["0x200"]["0x100"] == 1
    assert "0x100" in dv["blocks"] and "0x200" in dv["blocks"]


def test_deflatten_empty_log():
    dv = NativeDevirtService(publish=False).deflatten(_FakeEng(), [])
    assert dv["ok"] and dv["n_dispatches"] == 0 and dv["n_unique"] == 0


def test_deflatten_max_unique_caps_blocks():
    log = [{"val": 0x100 + i * 8} for i in range(10)]
    dv = NativeDevirtService(publish=False).deflatten(_FakeEng(), log, max_unique=3)
    assert dv["n_unique"] == 10
    assert len(dv["blocks"]) == 3            # Disasm nur fuer die ersten 3


def _mk(val, i1=0, c0=0):
    return {"val": val, "idx1": i1, "col": c0}


def test_deflatten_multi_branch_point_detection():
    # Handler 0x100 -> in Lauf0 nach 0x200, in Lauf1 nach 0x400 => Branch-Point.
    log0 = [_mk(0x100, 0, 0), _mk(0x200, 5, 1), _mk(0x300, 7, 2), _mk(0)]
    log1 = [_mk(0x100, 0, 0), _mk(0x400, 9, 3), _mk(0x300, 7, 2), _mk(0)]
    dv = NativeDevirtService(publish=False).deflatten_multi(_FakeEng(), [log0, log1])
    assert dv["ok"] and dv["n_runs"] == 2
    assert dv["n_unique"] == 4                      # 0x100,0x200,0x300,0x400
    assert dv["n_branch_points"] == 1
    bp = dv["branch_points"][0]
    assert bp["handler"] == "0x100" and bp["n_successors"] == 2
    tgts = {s["target"] for s in bp["successors"]}
    assert tgts == {"0x200", "0x400"}
    # Index-Entscheidungstabelle: [5][1]->0x200, [9][3]->0x400
    tab = {(r["index"], r["target"]) for r in bp["index_table"]}
    assert ("[5][1]", "0x200") in tab and ("[9][3]", "0x400") in tab


def test_deflatten_multi_run_coverage():
    log0 = [_mk(0x100, 0, 0), _mk(0x200, 5, 1), _mk(0x300, 7, 2), _mk(0)]
    log1 = [_mk(0x100, 0, 0), _mk(0x400, 9, 3), _mk(0x300, 7, 2), _mk(0)]
    dv = NativeDevirtService(publish=False).deflatten_multi(_FakeEng(), [log0, log1])
    # Kante 0x300 -> TRAP tritt in BEIDEN Laeufen auf -> runs=2, count=2
    c = dv["successors"]["0x300"]["TRAP"]
    assert c["runs"] == 2 and c["count"] == 2
    # Kante 0x100 -> 0x200 nur in Lauf0 -> runs=1
    assert dv["successors"]["0x100"]["0x200"]["runs"] == 1


def test_deflatten_multi_single_log_no_branch():
    log0 = [_mk(0x100, 0, 0), _mk(0x200, 5, 1), _mk(0x300, 7, 2), _mk(0)]
    dv = NativeDevirtService(publish=False).deflatten_multi(_FakeEng(), [log0])
    assert dv["n_runs"] == 1 and dv["n_branch_points"] == 0 and dv["n_unique"] == 3


# ---------- D-stat: statischer Index-Resolver (hermetisch, Fake-Engine) ----------
class _FIns:
    def __init__(self, mn, ops, size=4):
        self.mnemonic = mn; self.op_str = ops; self.size = size

class _FMem:
    def __init__(self, words):
        self.words = words  # addr -> int (8 Byte)
    def rb(self, a, n):
        return int(self.words.get(int(a), 0)).to_bytes(n, "little")

class _FCpu:
    def __init__(self, mem): self.m = mem

class _FEng:
    def __init__(self, prog, mem):
        self.prog = prog; self.cpu = _FCpu(mem)
    def decode_at(self, p):
        mn, ops = self.prog[int(p)]
        return _FIns(mn, ops)

def test_resolve_index_static_index_node():
    """Index-Knoten: const - getter() -> *(table + stride*index), realistische Werte."""
    from services.native_devirt_service import NativeDevirtService
    prog = {
        0x1000: ("adrp", "x6, #0x40000"),
        0x1004: ("ldr",  "x6, [x6, #0x20]"),         # x6 = *(0x40020) = 0x2000 (getter ptr)
        0x1008: ("blr",  "x6"),                        # x0 = key (via Leaf)
        0x100c: ("mov",  "w10, #0x4249"),
        0x1010: ("movk", "w10, #0x903f, lsl #16"),     # const = 0x903f4249
        0x1014: ("neg",  "w9, w0"),
        0x1018: ("orr",  "w11, w9, w10"),
        0x101c: ("and",  "w9, w9, w10"),
        0x1020: ("add",  "w9, w11, w9"),               # = const - key32 (Opaque Predicate)
        0x1024: ("sxtw", "x9, w9"),
        0x1028: ("adrp", "x8, #0x50000"),              # table
        0x102c: ("mov",  "x10, #8"),
        0x1030: ("madd", "x8, x9, x10, x8"),
        0x1034: ("ldr",  "x8, [x8]"),
        0x1038: ("br",   "x8"),
        # Getter-Leaf
        0x2000: ("adrp", "x5, #0x40000"),
        0x2004: ("ldr",  "x0, [x5, #0x10]"),           # x0 = *(0x40010) = key
        0x2008: ("ret",  ""),
    }
    key = 0x586263c903f4211
    index = (0x903f4249 - (key & 0xffffffff)) & 0xffffffff   # 0x38
    ea = 0x50000 + 8 * index
    mem = _FMem({0x40020: 0x2000, 0x40010: key, ea: 0xabcd0})
    eng = _FEng(prog, mem)
    dv = NativeDevirtService(publish=False)
    r = dv.resolve_index_static(eng, 0x1000)
    assert r["matched"] and r["node_type"] == "index", r
    assert r["table"] == "0x50000" and r["stride"] == 8, r
    assert r["const"] == "0x903f4249", r
    assert r["key"] == hex(key) and r["key_slot"] == "0x40010", r
    assert r["index"] == index, r
    assert r["successor"] == "0xabcd0", r

def test_resolve_index_static_branch_node():
    """Branch-Knoten: csel zwischen zwei .data-Zeigern -> zwei Kandidaten + deren Nachfolger."""
    from services.native_devirt_service import NativeDevirtService
    prog = {
        0x1000: ("adrp", "x9, #0x13c000"),
        0x1004: ("add",  "x9, x9, #0x848"),     # x9 = 0x13c848
        0x1008: ("adrp", "x8, #0x141000"),
        0x100c: ("add",  "x8, x8, #0x928"),     # x8 = 0x141928
        0x1010: ("csel", "x8, x9, x8, eq"),     # Branch: zwei Kandidaten
        0x1014: ("ldr",  "x8, [x8]"),
        0x1018: ("br",   "x8"),
    }
    mem = _FMem({0x13c848: 0xaaaa0, 0x141928: 0xbbbb0})
    eng = _FEng(prog, mem)
    dv = NativeDevirtService(publish=False)
    r = dv.resolve_index_static(eng, 0x1000)
    assert r["matched"] and r["node_type"] == "branch", r
    assert set(r["candidates"]) == {"0x13c848", "0x141928"}, r
    assert set(r["candidate_successors"]) == {"0xaaaa0", "0xbbbb0"}, r


def test_resolve_index_static_smaddl_2d():
    """2D-Vtable-Dispatch (0x137740) via smaddl: Zeile = base + idx1*0x7f0, dann ldr[row, col, lsl#3]."""
    from services.native_devirt_service import NativeDevirtService
    prog = {
        0x1000: ("adrp", "x11, #0x137000"),
        0x1004: ("add",  "x11, x11, #0x740"),
        0x1008: ("mov",  "w8, #3"),
        0x100c: ("mov",  "w10, #0x7f0"),
        0x1010: ("smaddl","x8, w8, w10, x11"),
        0x1014: ("mov",  "w9, #5"),
        0x1018: ("ldr",  "x8, [x8, w9, sxtw #3]"),
        0x101c: ("br",   "x8"),
    }
    ea = 0x137740 + 3 * 0x7f0 + 5 * 8
    dv = NativeDevirtService(publish=False)
    r = dv.resolve_index_static(_FEng(prog, _FMem({ea: 0xdead1})), 0x1000)
    assert r["matched"] and r["node_type"] == "index", r
    assert r["table"] == "0x137740" and r["stride"] == 0x7f0, r
    assert r["successor"] == "0xdead1", r

def test_devirt_static_graph():
    """BFS-Graph: A(direct)->B(trap). n_blocks=2, 1 Trap."""
    from services.native_devirt_service import NativeDevirtService
    prog = {
        0x1000: ("b",   "#0x2000"),
        0x2000: ("mov", "x8, #0"),
        0x2004: ("br",  "x8"),
    }
    dv = NativeDevirtService(publish=False)
    g = dv.devirt_static(_FEng(prog, _FMem({})), 0x1000)
    assert g["ok"] and g["n_blocks"] == 2, g
    assert g["n_traps"] == 1 and "0x2000" in g["traps"], g
    assert g["blocks"]["0x1000"]["successors"] == ["0x2000"], g
