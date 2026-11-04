"""PluginCallableResolver — eine vom Plugin gelieferte Python-Strategie.

Für Fälle, die zu komplex für declarative Regeln sind (eigene Heuristiken,
Nachschlagen in projektspezifischen Tabellen, Kombinationen). Ein Plugin
stellt ein Callable bereit:

    fn(target: JniTarget, symindex: SymbolIndex,
       prefer_libs: list[str] | None) -> Iterable[Hit | dict] | None

Rückgaben dürfen ``Hit``-Objekte **oder** dicts sein (werden normalisiert).
Registriert wird über ``native_bridge.resolver.REGISTRY`` oder direkt in die
Kette gehängt. GUI-Metadaten kommen aus Name/Label, die das Plugin angibt.
"""
from __future__ import annotations

from typing import Callable, Iterable, List

from .model import Hit, JniTarget, StrategyInfo
from .symindex import SymbolIndex


def _normalize(obj, default_by: str) -> Hit | None:
    if isinstance(obj, Hit):
        return obj
    if isinstance(obj, dict):
        try:
            return Hit(
                lib=str(obj["lib"]),
                addr=int(obj["addr"]),
                symbol=str(obj.get("symbol", f"sub_{int(obj['addr']):x}")),
                mode=str(obj.get("mode", "plugin")),
                by=str(obj.get("by", default_by)),
                confidence=float(obj.get("confidence", 0.7)),
                note=str(obj.get("note", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


class PluginCallableResolver:
    """Hüllt ein vom Plugin geliefertes Callable als Strategie."""

    def __init__(self, fn: Callable, name: str, label: str | None = None,
                 source: str = "plugin", detail: str = "", enabled: bool = True):
        self._fn = fn
        self._name = name
        self._label = label or name
        self._source = source if source.startswith("plugin") else f"plugin:{source}"
        self._detail = detail or "Vom Plugin gelieferte Auflösungs-Strategie."
        self._enabled = enabled

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name=self._name,
            label=self._label,
            enabled=self._enabled,
            source=self._source,
            detail=self._detail,
        )

    def resolve(self, target: JniTarget, symindex: SymbolIndex,
                prefer_libs: List[str] | None = None) -> List[Hit]:
        raw: Iterable = self._fn(target, symindex, prefer_libs) or []
        out: List[Hit] = []
        for item in raw:
            h = _normalize(item, self._name)
            if h is not None:
                out.append(h)
        return out
