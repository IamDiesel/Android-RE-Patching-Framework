"""RuleBasedResolver — nutzerdefinierte Transform-Regeln (declarative).

Lädt Regeln aus einer JSON-Quelle (Datei oder dict). Die Regeln bilden
Klassen-/Methodennamen auf Symbolnamen ab — für obfuskierte Builds mit
konsistenter Umbenennung. Für Fälle ohne Regelmuster gibt es ``alias``
(direkte Zuordnung auf Symbol oder Adresse).

Schema (version 1):
    {
      "version": 1,
      "rules": [
        { "match": {"class": "<regex>", "method": "<regex>"},
          "to_symbol": "Java_com_abbott_{1}_{method}",
          "transform": {"method": "strip_prefix:native_"},
          "confidence": 0.9 },
        { "alias": {"Lx/y/Z;->decrypt": "sub_1A2B0"} }
      ]
    }

Platzhalter in ``to_symbol``:
    {class}   -> class_internal (ohne L…;)
    {method}  -> (ggf. transformierter) Methodenname
    {1},{2}…  -> Regex-Gruppen aus dem class-Match (dann {m1},{m2} aus method-Match)

Transform-Primitive (pro Feld "class"/"method"):
    strip_prefix:<s> | strip_suffix:<s> | lower | upper | regex_replace:/pat/repl/
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from .model import Hit, JniTarget, StrategyInfo
from .symindex import SymbolIndex


def _apply_transform(value: str, spec: str) -> str:
    if spec == "lower":
        return value.lower()
    if spec == "upper":
        return value.upper()
    if spec.startswith("strip_prefix:"):
        p = spec[len("strip_prefix:"):]
        return value[len(p):] if value.startswith(p) else value
    if spec.startswith("strip_suffix:"):
        s = spec[len("strip_suffix:"):]
        return value[:-len(s)] if s and value.endswith(s) else value
    if spec.startswith("regex_replace:"):
        body = spec[len("regex_replace:"):]
        # Form: /pattern/replacement/
        if len(body) >= 2 and body[0] == "/":
            parts = body[1:].split("/")
            if len(parts) >= 2:
                pat, repl = parts[0], parts[1]
                return re.sub(pat, repl, value)
    return value


class RuleBasedResolver:
    """Wendet declarative Transform-/Alias-Regeln an."""

    def __init__(self, rules: Dict[str, Any] | None = None,
                 path: str | None = None, source: str | None = None,
                 enabled: bool = True):
        self._enabled = enabled
        self._path = path
        self._source = source or (f"rules:{path}" if path else "rules:inline")
        self._rules: List[dict] = []
        if rules is not None:
            self._rules = list(rules.get("rules", []))
        elif path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._rules = list(json.load(f).get("rules", []))
            except Exception:
                self._rules = []

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="rule_based",
            label="Transform-Regeln",
            enabled=self._enabled,
            source=self._source,
            detail=f"{len(self._rules)} Regel(n) (Regex/Transform/Alias).",
            extra={"count": len(self._rules)},
        )

    def resolve(self, target: JniTarget, symindex: SymbolIndex,
                prefer_libs: List[str] | None = None) -> List[Hit]:
        hits: List[Hit] = []
        dalvik_sig = f"{target.dalvik_class}->{target.method}"

        for rule in self._rules:
            # 1) Alias-Regel: direkte Zuordnung
            alias = rule.get("alias")
            if alias:
                ref = alias.get(dalvik_sig)
                if ref:
                    conf = float(rule.get("confidence", 0.85))
                    for lib, addr, sym in symindex.resolve_target_ref(ref, prefer_libs):
                        hits.append(Hit(lib=lib, addr=addr, symbol=sym,
                                        mode="alias", by="rule_based",
                                        confidence=conf, note=f"alias {ref}"))
                continue

            # 2) Match + to_symbol
            match = rule.get("match") or {}
            cls_pat = match.get("class", ".*")
            met_pat = match.get("method", ".*")
            m_cls = re.search(cls_pat, target.dalvik_class)
            m_met = re.search(met_pat, target.method)
            if not (m_cls and m_met):
                continue

            # Transform der Felder
            transform = rule.get("transform") or {}
            method_val = target.method
            class_val = target.class_internal
            if "method" in transform:
                method_val = _apply_transform(method_val, transform["method"])
            if "class" in transform:
                class_val = _apply_transform(class_val, transform["class"])

            # Platzhalter füllen
            repl = {"class": class_val, "method": method_val}
            for i, g in enumerate(m_cls.groups(), start=1):
                repl[str(i)] = g or ""
            for i, g in enumerate(m_met.groups(), start=1):
                repl[f"m{i}"] = g or ""

            to_symbol = rule.get("to_symbol")
            if not to_symbol:
                continue
            # Eigene Platzhalter-Ersetzung: {1}/{m1}/{class}/{method}.
            # (str.format deutet {1} als Positionsargument — daher manuell.)
            sym = re.sub(r"\{(\w+)\}",
                         lambda mo: repl.get(mo.group(1), mo.group(0)),
                         to_symbol)
            if "{" in sym:  # unaufgelöster Platzhalter -> Regel überspringen
                continue

            conf = float(rule.get("confidence", 0.8))
            for lib, addr, rsym in symindex.resolve_target_ref(sym, prefer_libs):
                hits.append(Hit(lib=lib, addr=addr, symbol=rsym, mode="rule",
                                by="rule_based", confidence=conf,
                                note=f"Regel -> {sym}"))
        return hits
