"""StdJniMangleResolver — Standard-JNI-Namensmangling (JNI-Spec).

Bildet eine native Java-Methode deterministisch auf den exportierten
Symbolnamen ab:

    Java_<klasse_gemangelt>_<methode_gemangelt>            (Kurzname)
    Java_<…>_<…>__<arg_deskriptor_gemangelt>               (Langname, Überladung)

Mangling-Regeln (JNI):
    '/' , '.'  -> '_'      (Paket-Trenner)
    '_'        -> '_1'
    ';'        -> '_2'
    '['        -> '_3'
    non-ASCII  -> '_0xxxx' (4 Hex-Stellen des Codepoints)
Rückgabetyp bleibt außen vor; nur der Klammerinhalt der Signatur zählt.
"""
from __future__ import annotations

from typing import List

from .model import Hit, JniTarget, StrategyInfo
from .symindex import SymbolIndex


def mangle(segment: str) -> str:
    """Mangelt einen Namens-/Signatur-Abschnitt gemäß JNI-Spec."""
    out = []
    for ch in segment:
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif ch in ("/", "."):
            out.append("_")
        elif ch == "_":
            out.append("_1")
        elif ch == ";":
            out.append("_2")
        elif ch == "[":
            out.append("_3")
        else:
            out.append("_0%04x" % ord(ch))
    return "".join(out)


def short_name(target: JniTarget) -> str:
    return "Java_" + mangle(target.class_internal) + "_" + mangle(target.method)


def long_name(target: JniTarget) -> str | None:
    if not target.arg_desc:
        return None
    return short_name(target) + "__" + mangle(target.arg_desc)


class StdJniMangleResolver:
    """Standard-JNI-Mangling + dynsym-Lookup."""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="std_jni_mangle",
            label="Standard-JNI-Mangling",
            enabled=self._enabled,
            source="framework",
            detail="Java_<Klasse>_<Methode> gemäß JNI-Spec (statische Bindung).",
        )

    def resolve(self, target: JniTarget, symindex: SymbolIndex,
                prefer_libs: List[str] | None = None) -> List[Hit]:
        hits: List[Hit] = []
        # Langname (überladungssicher) zuerst, höhere Konfidenz
        ln = long_name(target)
        if ln:
            for lib, addr in symindex.lookup(ln, prefer_libs):
                hits.append(Hit(lib=lib, addr=addr, symbol=ln, mode="static",
                                by="std_jni_mangle", confidence=0.98,
                                note="JNI-Langname"))
        sn = short_name(target)
        for lib, addr in symindex.lookup(sn, prefer_libs):
            hits.append(Hit(lib=lib, addr=addr, symbol=sn, mode="static",
                            by="std_jni_mangle", confidence=0.95,
                            note="JNI-Kurzname"))
        return hits
