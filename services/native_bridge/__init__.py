"""native_bridge — generische Smali→Native-Auflösung (Strategie-Kette).

IP-neutral: kennt kein LLU. Das Framework liefert Standard-Mangling +
declarative Transform-Regeln; Plugins steuern eigene Python-Strategien bei.

Typische Nutzung (im Controller, Stufe 1):

    from native_bridge import build_chain, JniTarget, SymbolIndex

    chain = build_chain(rules_path="data/native/<plugin>/jni_rules.json")
    symidx = SymbolIndex(app.get_loaded_native_images())
    hits = chain.resolve(JniTarget("Lcom/foo/Bar;", "decrypt"), symidx,
                         prefer_libs=find_library_names(class_content))
"""
from __future__ import annotations

from typing import List

from .model import Hit, JniTarget, StrategyInfo
from .symindex import SymbolIndex
from .resolver import NativeResolver, ResolverChain, ResolverRegistry, REGISTRY
from .std_mangle import StdJniMangleResolver, mangle, short_name, long_name
from .rule_based import RuleBasedResolver
from .callable_strategy import PluginCallableResolver
from .register_natives import RegisterNativesResolver, scan_register_natives
from .libnames import find_library_names

__all__ = [
    "Hit", "JniTarget", "StrategyInfo", "SymbolIndex",
    "NativeResolver", "ResolverChain", "ResolverRegistry", "REGISTRY",
    "StdJniMangleResolver", "RuleBasedResolver", "PluginCallableResolver",
    "RegisterNativesResolver", "scan_register_natives",
    "mangle", "short_name", "long_name", "find_library_names", "build_chain",
]


def build_chain(rules: dict | None = None, rules_path: str | None = None,
                include_plugins: bool = True, include_register_natives: bool = True,
                extra: List[NativeResolver] | None = None) -> ResolverChain:
    """Baut die Default-Kette: Standard-Mangling → Transform-Regeln →
    RegisterNatives-Scan → Plugins.

    rules / rules_path       : optionale declarative Regeln (dict oder JSON-Datei).
    include_register_natives : JNINativeMethod[]-Scan fuer dynamische Bindung (Stufe 3).
    include_plugins          : registrierte Plugin-Strategien aus REGISTRY aufnehmen.
    extra                    : zusätzliche, direkt übergebene Strategien.
    """
    chain = ResolverChain([StdJniMangleResolver()])
    if rules is not None or rules_path:
        chain.add(RuleBasedResolver(rules=rules, path=rules_path))
    if include_register_natives:
        chain.add(RegisterNativesResolver())
    if include_plugins:
        for r in REGISTRY.build_all():
            chain.add(r)
    for r in (extra or []):
        chain.add(r)
    return chain
