"""Tests für den nativen XREF/CallGraph-Index (core.native.nindex). Braucht capstone."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.native import loader
from core.native.loader import ElfImage, Segment, Symbol
from core.native.nindex import NativeIndex, _parse_target, _is_branch


def _img():
    # Code @0x1000: bl 0x2000 ; b 0x1000 (self-loop) ; nop ; @0x2000 ret
    # bl  0x2000 (pc 0x1000, off +0x1000 -> imm26=0x400)      = 0x94000400
    # b   0x1000 (pc 0x1004, off -4 -> imm26=0x3ffffff)       = 0x17ffffff
    # nop                                                     = 0xd503201f
    # ... fill to 0x2000 with nops, ret at 0x2000
    code = bytearray()
    code += struct.pack("<I", 0x94000400)  # 0x1000 bl 0x2000
    code += struct.pack("<I", 0x17ffffff)  # 0x1004 b  0x1000
    code += struct.pack("<I", 0xd503201f)  # 0x1008 nop
    # pad bis Offset 0x1000 (= vaddr 0x2000) relativ zum Segmentstart (vaddr 0x1000)
    while len(code) < 0x1000:
        code += struct.pack("<I", 0xd503201f)
    code += struct.pack("<I", 0xd65f03c0)  # 0x2000 ret
    img = ElfImage(data=bytes(code), is_aarch64=True, entry=0x1000)
    img.segments = [Segment(vaddr=0x1000, offset=0, filesz=len(code), memsz=len(code), flags=0x5, align=0x1000)]
    img.dynsyms = {"func_a": Symbol("func_a", 0x1000, 0, 0, 0),
                   "func_b": Symbol("func_b", 0x2000, 0, 0, 0)}
    return img


def test_parse_target():
    assert _parse_target("#0x2000") == 0x2000
    assert _parse_target("x0, #0x27ec") == 0x27ec      # cbz-Form: letztes imm = Ziel
    assert _parse_target("w8, #3, #0x5100") == 0x5100  # tbz-Form
    assert _parse_target("x16") is None


def test_is_branch():
    assert _is_branch("bl") and _is_branch("b") and _is_branch("cbz")
    assert _is_branch("b.ne") and _is_branch("tbz")
    assert not _is_branch("add") and not _is_branch("ret")


def test_build_edges_and_xref():
    idx = NativeIndex().build(_img())
    assert idx.count > 0
    edict = {(s, m, t) for s, m, t in idx.edges}
    assert (0x1000, "bl", 0x2000) in edict
    assert (0x1004, "b", 0x1000) in edict
    assert idx.xrefs_to(0x2000) == [(0x1000, "bl")]
    assert idx.xrefs_to(0x1000) == [(0x1004, "b")]


def test_callgraph_and_callers():
    idx = NativeIndex().build(_img())
    # bl 0x1000(func_a) -> 0x2000(func_b)
    assert ("func_a", "func_b") in idx.callgraph_edges()
    assert "func_a" in idx.callers_of("func_b")
    assert idx.func_at(0x1008) == "func_a" and idx.func_at(0x2000) == "func_b"


def test_search():
    idx = NativeIndex().build(_img())
    assert any(m == "bl" for _, m, _ in idx.search("bl"))
    assert any(t == 0x2000 for _, _, t in idx.search("0x2000"))


def test_cache_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        img = _img()
        a = NativeIndex().build(img, cache_dir=d)
        # Cache-Datei existiert?
        assert any(fn.startswith(".nidx_") for fn in os.listdir(d))
        b = NativeIndex().build(img, cache_dir=d)  # 2. Mal: aus Cache
        assert b.edges == a.edges and b.count == a.count and b.xref == a.xref


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
