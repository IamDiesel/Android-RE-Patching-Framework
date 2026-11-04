# native_bridge — Smali→Native Resolver (Stufe 1)

IP-neutrales Paket für das RE Studio. Löst eine aus Smali stammende native
Methode auf ein natives Symbol/eine Adresse auf — über eine **Strategie-Kette**.

## Zielort im Framework
`services/native_bridge/` (neues Paket). Tests nach `tests/test_native_bridge.py`.
Projektspezifische Regeln (nicht hier!) nach `native_plugins/<plugin>/jni_rules.json`.

## Strategien (Reihenfolge der Kette)
1. **StdJniMangleResolver** — Standard-JNI-Mangling (`Java_<Klasse>_<Methode>`), Konfidenz 0.95/0.98.
2. **RuleBasedResolver** — declarative Regeln aus `jni_rules.json` (Regex/Transform/Alias).
3. **PluginCallableResolver** — vom Plugin geliefertes Python-Callable (über `REGISTRY`).

Erster/bester Treffer (höchste Konfidenz) gewinnt; Dedupe über (lib, addr, symbol).
Eine fehlerhafte Strategie sprengt die Kette nicht.

## GUI-Readiness
Jede Strategie liefert `info() -> StrategyInfo` (name, label, enabled, source, detail).
`ResolverChain.strategies()` / `.set_enabled(name, bool)` → eine spätere Oberfläche
kann Strategien auflisten und einzeln schalten.

## Nutzung (Controller, Stufe 1)
```python
from native_bridge import build_chain, JniTarget, SymbolIndex, find_library_names

chain  = build_chain(rules_path="native_plugins/llu/jni_rules.json")   # einmal bauen
symidx = SymbolIndex(app.get_loaded_native_images())                    # {lib: ElfImage}
target = JniTarget("Lcom/foo/Bar;", "decrypt")                          # aus Smali
hits   = chain.resolve(target, symidx, prefer_libs=find_library_names(class_src))
# hits[0] -> {lib, addr, symbol, mode, by, confidence}
```

## Plugin-Strategie registrieren
```python
from native_bridge import REGISTRY, PluginCallableResolver

def _resolve(target, symindex, prefer_libs):
    # eigene Heuristik; gib Hit-dicts oder native_bridge.Hit zurück
    return [{"lib": "libwb.so", "addr": 0x1234, "symbol": "custom", "confidence": 0.8}]

REGISTRY.register("llu_custom", lambda: PluginCallableResolver(_resolve,
                  name="llu_custom", label="LLU Custom", source="plugin:llu"))
```

## Tests
`python -m pytest tests/ -q` → 18 passed. Reine Logik, keine GUI/App-Abhängigkeit.
