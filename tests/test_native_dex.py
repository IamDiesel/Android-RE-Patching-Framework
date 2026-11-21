# -*- coding: utf-8 -*-
"""Tests fuer J3: DEX-Bytecode-Decoder -> Tupel-IR -> DalvikVM (synthetisch + echte classes.dex)."""
import os
import pytest

from core.java.dalvik import DalvikVM
from core.java.dex_code import decode_code


class _StubDex:
    def string(self, i): return {0: "hallo", 1: "welt"}.get(i, "s%d" % i)
    def type(self, i): return "Ljava/lang/String;"
    def method(self, i): return ("Lm;", "sum", "(II)I")
    def field(self, i): return ("Lc;", "f", "I")


def _ci(insns, regs, ins=0):
    return {"registers_size": regs, "ins_size": ins, "outs_size": 0,
            "tries_size": 0, "insns_size": len(insns), "insns": insns}


def test_decode_arithmetic():
    # const/4 v0,#1 ; add-int/lit8 v0,v0,#5 ; return v0  -> 6
    insns = [0x1012, 0x00d8, 0x0500, 0x000f]
    md = decode_code(_StubDex(), _ci(insns, 1), "Lx;", "m", "()I", 0x8)
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke("Lx;", "m", "()I", ()) == 6


def test_decode_loop_sum():
    # sum 1..3 ueber if-ge + goto
    insns = [0x0012, 0x1112, 0x4212, 0x2135, 0x0006, 0x10b0, 0x01d8, 0x0101, 0xfb28, 0x000f]
    md = decode_code(_StubDex(), _ci(insns, 3), "Lx;", "sumTo3", "()I", 0x8)
    vm = DalvikVM(); vm.register(md)
    assert vm.invoke("Lx;", "sumTo3", "()I", ()) == 6
    # Branch-Ziele sind gueltige IR-Indizes
    for ins in md.insns:
        if ins[0] == "goto":
            assert 0 <= ins[1] <= len(md.insns)
        if ins[0] in ("if-ge",):
            assert 0 <= ins[-1] <= len(md.insns)


def test_decode_const_string():
    # const-string v0, @0 ; return-object v0
    insns = [0x001a, 0x0000, 0x0011]
    md = decode_code(_StubDex(), _ci(insns, 1), "Lx;", "s", "()Ljava/lang/String;", 0x8)
    vm = DalvikVM(); vm.register(md)
    from core.java.objects import JString
    r = vm.invoke("Lx;", "s", "()Ljava/lang/String;", ())
    assert isinstance(r, JString) and r.text == "hallo"


def test_decode_invoke_static():
    # invoke-static {} Lm;->sum(II)I via stub-dex ; move-result v0 ; return v0
    # 35c: op0x71, A=count(0), method@0 ; u2=0 ; dann move-result v0 ; return v0
    insns = [0x0071, 0x0000, 0x0000, 0x000a, 0x000f]
    md = decode_code(_StubDex(), _ci(insns, 1), "Lx;", "c", "()I", 0x8)
    # Ziel registrieren
    vm = DalvikVM()
    vm.register_method("Lm;", "sum", "(II)I", [("const", 0, 99), ("return", 0)], nregs=2, nargs=2, static=True)
    vm.register(md)
    assert vm.invoke("Lx;", "c", "()I", ()) == 99


_DEX = "secrets/probe/mcp_native/dex/classes.dex"


@pytest.mark.skipif(not os.path.isfile(_DEX), reason="classes.dex nicht vorhanden")
def test_real_dex_decodes_without_crash():
    from core.java.dex import DexFile
    dx = DexFile(open(_DEX, "rb").read())
    decoded = 0
    unsupported = 0
    checked = 0
    for cd in dx.class_defs():
        for m in dx.class_methods(cd):
            if not m.code_off or m.is_native or m.is_abstract:
                continue
            ci = dx.code_item(m.code_off)
            md = decode_code(dx, ci, m.cls, m.name, m.sig, m.access)
            decoded += 1
            for ins in md.insns:
                if isinstance(ins[0], str) and ins[0].startswith("dex-unsupported"):
                    unsupported += 1
                # Branch-Ziele im gueltigen Bereich
                if ins[0] in ("goto", "if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le",
                              "if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez"):
                    assert 0 <= ins[-1] <= len(md.insns)
            checked += 1
            if checked >= 2000:
                break
        if checked >= 2000:
            break
    assert decoded > 100
    print("\n    decoded=%d methods, unsupported-opcode-instanzen=%d" % (decoded, unsupported))
