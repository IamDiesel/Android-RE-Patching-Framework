"""Unit-Tests für native_bridge (Stufe 1) — reine Logik, ohne GUI/App."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.native_bridge import (  # noqa: E402
    JniTarget, SymbolIndex, build_chain, mangle, short_name, long_name,
    StdJniMangleResolver, RuleBasedResolver, PluginCallableResolver,
    RegisterNativesResolver, scan_register_natives,
    ResolverChain, Hit, find_library_names, REGISTRY,
)


# --- Synthetisches ELF-Mock fuer RegisterNatives (segments/relocs/data) ---
class _Seg:
    def __init__(self, vaddr, offset, filesz, flags, memsz=None):
        self.vaddr = vaddr; self.offset = offset; self.filesz = filesz
        self.flags = flags; self.memsz = memsz if memsz is not None else filesz


class _ElfMock:
    """offset == vaddr (Datei-Offset = virtuelle Adresse), damit _v2o trivial ist."""
    def __init__(self):
        size = 0x5000
        buf = bytearray(size)
        # Strings in RW-Daten
        name = b"cppName\x00"; sig = b"(J)Ljava/lang/String;\x00"
        buf[0x4100:0x4100+len(name)] = name
        buf[0x4120:0x4120+len(sig)] = sig
        self.data = bytes(buf)
        self.segments = [
            _Seg(0x1000, 0x1000, 0x1000, 0x1),  # TEXT (X)
            _Seg(0x4000, 0x4000, 0x1000, 0x6),  # RW (R|W)
        ]
        # JNINativeMethod @0x4200: name@0x4100, sig@0x4120, fn@0x1200(text)
        self.relocs = [
            (0x4200, 0x4100, "reloc"),
            (0x4208, 0x4120, "reloc"),
            (0x4210, 0x1200, "reloc"),
        ]
        self.dynsyms = {}


# --- Mock-Image: ElfImage-ähnlich mit .dynsyms {name -> Symbol(.value)} ---
class _Sym:
    def __init__(self, value):
        self.value = value


class _Img:
    def __init__(self, syms: dict):
        self.dynsyms = {n: _Sym(v) for n, v in syms.items()}


# ============================ Mangling ============================

def test_mangle_simple():
    t = JniTarget("Lcom/foo/Bar;", "baz")
    assert short_name(t) == "Java_com_foo_Bar_baz"


def test_mangle_underscore_escape():
    t = JniTarget("Lcom/foo/Ba_r;", "a_b")
    assert short_name(t) == "Java_com_foo_Ba_1r_a_1b"


def test_mangle_overload_longname():
    t = JniTarget("Lcom/foo/Bar;", "baz", arg_desc="Ljava/lang/String;I")
    # ';' -> _2, '/' -> _, '[' -> _3
    assert long_name(t) == "Java_com_foo_Bar_baz__Ljava_lang_String_2I"


def test_mangle_array_and_nonascii():
    assert mangle("[") == "_3"
    assert mangle(";") == "_2"
    assert mangle("ä") == "_000e4"


# ======================= StdJniMangleResolver =======================

def test_std_resolver_static_hit():
    img = _Img({"Java_com_foo_Bar_decrypt": 0x1000})
    idx = SymbolIndex({"libwb.so": img})
    chain = ResolverChain([StdJniMangleResolver()])
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), idx)
    assert len(hits) == 1
    assert hits[0].addr == 0x1000
    assert hits[0].lib == "libwb.so"
    assert hits[0].mode == "static"


def test_std_resolver_no_hit():
    idx = SymbolIndex({"libwb.so": _Img({"something_else": 1})})
    chain = ResolverChain([StdJniMangleResolver()])
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), idx)
    assert hits == []


def test_prefer_libs_order():
    a = _Img({"Java_com_foo_Bar_decrypt": 0xAAA})
    b = _Img({"Java_com_foo_Bar_decrypt": 0xBBB})
    idx = SymbolIndex({"liba.so": a, "libb.so": b})
    chain = ResolverChain([StdJniMangleResolver()])
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), idx,
                         prefer_libs=["libb.so"])
    # beide Treffer, aber libb zuerst (gleiche Konfidenz -> Reihenfolge Lookup)
    libs = [h.lib for h in hits]
    assert "libb.so" in libs and "liba.so" in libs


# ========================= RuleBasedResolver =========================

def test_rule_regex_to_symbol():
    rules = {"version": 1, "rules": [
        {"match": {"class": "^Lcom/abbott/(.*);$", "method": ".*"},
         "to_symbol": "Java_com_abbott_{1}_{method}", "confidence": 0.9},
    ]}
    img = _Img({"Java_com_abbott_Crypto_sign": 0x2000})
    idx = SymbolIndex({"libx.so": img})
    chain = ResolverChain([RuleBasedResolver(rules=rules)])
    hits = chain.resolve(JniTarget("Lcom/abbott/Crypto;", "sign"), idx)
    assert len(hits) == 1 and hits[0].addr == 0x2000 and hits[0].mode == "rule"


def test_rule_transform_strip_prefix():
    rules = {"version": 1, "rules": [
        {"match": {"class": ".*", "method": "^native_.*$"},
         "transform": {"method": "strip_prefix:native_"},
         "to_symbol": "njni_{method}", "confidence": 0.6},
    ]}
    idx = SymbolIndex({"libx.so": _Img({"njni_decrypt": 0x3000})})
    chain = ResolverChain([RuleBasedResolver(rules=rules)])
    hits = chain.resolve(JniTarget("Lx/Y;", "native_decrypt"), idx)
    assert len(hits) == 1 and hits[0].addr == 0x3000


def test_rule_alias_to_address():
    rules = {"version": 1, "rules": [
        {"alias": {"Lx/y/Z;->decrypt": "sub_1A2B0"}},
    ]}
    idx = SymbolIndex({"libx.so": _Img({"unrelated": 1})})
    chain = ResolverChain([RuleBasedResolver(rules=rules)])
    hits = chain.resolve(JniTarget("Lx/y/Z;", "decrypt"), idx)
    assert len(hits) == 1
    assert hits[0].addr == 0x1A2B0 and hits[0].mode == "alias"


def test_rule_alias_to_symbol():
    rules = {"version": 1, "rules": [
        {"alias": {"Lx/y/Z;->decrypt": "my_real_symbol"}},
    ]}
    idx = SymbolIndex({"libx.so": _Img({"my_real_symbol": 0x4000})})
    chain = ResolverChain([RuleBasedResolver(rules=rules)])
    hits = chain.resolve(JniTarget("Lx/y/Z;", "decrypt"), idx)
    assert len(hits) == 1 and hits[0].addr == 0x4000


# ====================== PluginCallableResolver ======================

def test_plugin_callable_strategy():
    def my_fn(target, symindex, prefer_libs):
        if target.method == "special":
            return [{"lib": "libp.so", "addr": 0x5000, "symbol": "custom",
                     "confidence": 0.99}]
        return []
    strat = PluginCallableResolver(my_fn, name="llu_custom", label="LLU Custom")
    idx = SymbolIndex({"libp.so": _Img({})})
    chain = ResolverChain([strat])
    hits = chain.resolve(JniTarget("La/B;", "special"), idx)
    assert len(hits) == 1 and hits[0].addr == 0x5000 and hits[0].by == "llu_custom"


def test_broken_strategy_does_not_break_chain():
    class Boom:
        def info(self):
            from services.native_bridge import StrategyInfo
            return StrategyInfo(name="boom", label="Boom")
        def resolve(self, *a, **k):
            raise RuntimeError("kaputt")
    img = _Img({"Java_com_foo_Bar_decrypt": 0x1000})
    idx = SymbolIndex({"libwb.so": img})
    chain = ResolverChain([Boom(), StdJniMangleResolver()])
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), idx)
    assert len(hits) == 1 and hits[0].addr == 0x1000


# ===================== Kette / Konfidenz / Dedupe =====================

def test_chain_confidence_sorting_and_dedupe():
    # Std-Mangle und Regel treffen dieselbe Adresse -> dedupe, höhere Konfidenz
    rules = {"version": 1, "rules": [
        {"match": {"class": ".*", "method": ".*"},
         "to_symbol": "Java_com_foo_Bar_decrypt", "confidence": 0.5}]}
    img = _Img({"Java_com_foo_Bar_decrypt": 0x1000})
    idx = SymbolIndex({"libwb.so": img})
    chain = build_chain(rules=rules, include_plugins=False)
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), idx)
    assert len(hits) == 1
    # std-mangle (0.95) gewinnt gegen Regel (0.5)
    assert hits[0].confidence == 0.95 and hits[0].by == "std_jni_mangle"


def test_registry_plugin_pickup():
    def fac():
        def fn(t, s, p):
            return [{"lib": "libr.so", "addr": 0x6000, "symbol": "reg"}]
        return PluginCallableResolver(fn, name="from_registry", label="Reg")
    REGISTRY.register("from_registry", fac)
    try:
        idx = SymbolIndex({"libr.so": _Img({})})
        chain = build_chain(include_plugins=True)
        hits = chain.resolve(JniTarget("La/B;", "x"), idx)
        assert any(h.addr == 0x6000 for h in hits)
    finally:
        REGISTRY._factories.pop("from_registry", None)


# =========================== SymbolIndex-Formen ===========================

def test_symindex_accepts_plain_dict_and_syms_list():
    # plain dict {name->addr}
    idx1 = SymbolIndex({"liba.so": {"Java_x_Y_z": 0x10}})
    assert idx1.lookup("Java_x_Y_z") == [("liba.so", 0x10)]

    # NativeIndex-ähnlich: .syms [(addr, name)]
    class _NI:
        syms = [(0x20, "Java_x_Y_z")]
    idx2 = SymbolIndex({"libb.so": _NI()})
    assert idx2.lookup("Java_x_Y_z") == [("libb.so", 0x20)]


# ========================= RegisterNatives (Stufe 3) =========================

def test_scan_register_natives_finds_entry():
    m = scan_register_natives(_ElfMock())
    assert "cppName" in m
    assert m["cppName"][0]["addr"] == 0x1200
    assert m["cppName"][0]["sig"] == "(J)Ljava/lang/String;"


def test_register_natives_resolver_hit():
    idx = SymbolIndex({"librive.so": _ElfMock()})
    chain = ResolverChain([RegisterNativesResolver()])
    hits = chain.resolve(JniTarget("Lapp/rive/runtime/kotlin/core/Artboard;", "cppName"), idx)
    assert len(hits) == 1
    assert hits[0].addr == 0x1200 and hits[0].lib == "librive.so"
    assert hits[0].mode == "register_natives"


def test_register_natives_signature_boosts_confidence():
    idx = SymbolIndex({"librive.so": _ElfMock()})
    r = RegisterNativesResolver()
    base = r.resolve(JniTarget("Lx/Y;", "cppName"), idx)[0]
    boosted = r.resolve(JniTarget("Lx/Y;", "cppName", arg_desc="J"), idx)[0]
    assert boosted.confidence > base.confidence


def test_register_natives_no_false_hit():
    idx = SymbolIndex({"librive.so": _ElfMock()})
    chain = ResolverChain([RegisterNativesResolver()])
    assert chain.resolve(JniTarget("Lx/Y;", "doesNotExist"), idx) == []


def test_chain_static_beats_register_natives():
    # Lib hat sowohl Java_-Export als auch RegisterNatives-Eintrag -> statisch (0.95) gewinnt
    class _Both(_ElfMock):
        def __init__(self):
            super().__init__()
            class _S:
                def __init__(s, v): s.value = v
            self.dynsyms = {"Java_x_Y_cppName": _S(0x1200)}
    idx = SymbolIndex({"librive.so": _Both()})
    chain = build_chain(include_plugins=False)
    hits = chain.resolve(JniTarget("Lx/Y;", "cppName"), idx)
    assert hits[0].addr == 0x1200
    assert hits[0].by == "std_jni_mangle" and hits[0].confidence == 0.95


# =============================== libnames ===============================

def test_find_library_names():
    smali = (
        ".method static constructor <clinit>()V\n"
        '    const-string v0, "wbcrypto"\n'
        "    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n"
        ".end method\n"
    )
    assert find_library_names(smali) == ["wbcrypto"]


def test_find_library_names_none_when_absent():
    smali = '.method x()V\n const-string v0, "not_a_lib"\n.end method\n'
    assert find_library_names(smali) == []
