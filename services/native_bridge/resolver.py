"""Resolver-Kern: Protokoll, Kette und Registry (IP-neutral, GUI-ready).

Die Bridge ist bewusst **keine** fest verdrahtete Mangling-Funktion, sondern
eine priorisierte **Strategie-Kette**. Jede Strategie implementiert
``NativeResolver`` und liefert Treffer (``Hit``) mit Provenienz + Konfidenz.
Die ``ResolverChain`` sammelt alle Treffer, dedupliziert und sortiert nach
Konfidenz. Der Aufrufer (Controller) nimmt den besten oder zeigt bei
Mehrdeutigkeit eine Auswahl.

GUI-Readiness: jede Strategie liefert ``info()`` (``StrategyInfo``) und ist
einzeln an-/abschaltbar. Plugins registrieren eigene Strategien über die
``ResolverRegistry`` — so kann eine spätere Oberfläche sie auflisten/toggeln.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Protocol, runtime_checkable

from .model import Hit, JniTarget, StrategyInfo
from .symindex import SymbolIndex


@runtime_checkable
class NativeResolver(Protocol):
    """Eine Auflösungs-Strategie Smali-native-Methode -> natives Symbol."""

    def info(self) -> StrategyInfo: ...

    def resolve(self, target: JniTarget, symindex: SymbolIndex,
                prefer_libs: List[str] | None = None) -> List[Hit]: ...


class ResolverChain:
    """Führt mehrere Strategien priorisiert aus und aggregiert die Treffer."""

    def __init__(self, resolvers: List[NativeResolver] | None = None):
        self._resolvers: List[NativeResolver] = list(resolvers or [])

    # --- Verwaltung (für GUI) ---
    def add(self, resolver: NativeResolver, index: int | None = None) -> None:
        if index is None:
            self._resolvers.append(resolver)
        else:
            self._resolvers.insert(index, resolver)

    def strategies(self) -> List[StrategyInfo]:
        return [r.info() for r in self._resolvers]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        for r in self._resolvers:
            inf = r.info()
            if inf.name == name:
                inf.enabled = enabled
                return True
        return False

    # --- Auflösung ---
    def resolve(self, target: JniTarget, symindex: SymbolIndex,
                prefer_libs: List[str] | None = None) -> List[Hit]:
        seen: Dict[tuple, Hit] = {}
        for r in self._resolvers:
            if not r.info().enabled:
                continue
            try:
                for hit in r.resolve(target, symindex, prefer_libs) or []:
                    k = hit.key()
                    # besten (höchste Konfidenz) pro Ziel behalten
                    if k not in seen or hit.confidence > seen[k].confidence:
                        seen[k] = hit
            except Exception:
                # Eine fehlerhafte Strategie darf die Kette nie sprengen.
                continue
        return sorted(seen.values(), key=lambda h: h.confidence, reverse=True)


class ResolverRegistry:
    """Sammelstelle für Plugin-Strategien (Factory je Name).

    Ein Plugin registriert hier eine Factory; der Chain-Builder zieht daraus
    die zusätzlichen Strategien. Eine GUI kann die registrierten Namen
    auflisten und einzeln aktivieren.
    """

    def __init__(self):
        self._factories: Dict[str, Callable[[], NativeResolver]] = {}

    def register(self, name: str, factory: Callable[[], NativeResolver]) -> None:
        self._factories[name] = factory

    def names(self) -> List[str]:
        return list(self._factories.keys())

    def build_all(self) -> List[NativeResolver]:
        out: List[NativeResolver] = []
        for name, fac in self._factories.items():
            try:
                out.append(fac())
            except Exception:
                continue
        return out


# Prozessweite Default-Registry, in die Plugins beim Import schreiben können.
REGISTRY = ResolverRegistry()
