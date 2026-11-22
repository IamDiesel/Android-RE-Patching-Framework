# -*- coding: utf-8 -*-
"""core.java.runtime_detect — erkennt das UI-Toolkit einer entpackten App und waehlt den passenden
Render-Produzenten (Nordstern: JEDE App). Erkennung am Abfangpunkt (Taxonomie nach Interception):
  Familie A  android.view  -> klassisch / React Native (Fabric-Mount-Grenze)
  Familie B  android.graphics.Canvas -> Jetpack Compose
  Familie C  Skia/Impeller -> Flutter
Signatur = Dateien/Libs/Assets, die das jeweilige Toolkit mitbringt. Rein lesend, heuristisch;
liefert einen in ProducerRegistry bekannten Schluessel. Grundlage: Konzept Render-Produzenten."""
from __future__ import annotations

import os

# Marker-Regeln -> Produzent-Schluessel. Reihenfolge = Prioritaet (abgeglichen mit app_runtime).
RUNTIME_PRIORITY = (
    "flutter",
    "react native (js)",
    "hybrid web",
    "nativescript",
    "jetpack compose",
)

KEY_TO_APP_LABEL = {
    "flutter": "Flutter",
    "react native (js)": "React Native",
    "hybrid web": "Hybrid Web",
    "nativescript": "NativeScript",
    "jetpack compose": "Jetpack Compose",
    "classic": "",
}

_RUNTIME_RULES = {
    "flutter": (
        frozenset({
            "libflutter.so", "libapp.so", "kernel_blob.bin",
            "isolate_snapshot_data", "vm_snapshot_data",
        }),
        ("flutter_assets/", "io/flutter/"),
    ),
    "react native (js)": (
        frozenset({
            "libhermes.so", "libhermesvm.so", "libjsc.so", "index.android.bundle",
            "libreactnative.so", "libreactnativejni.so", "libjscexecutor.so",
            "libhermes_executor.so", "hermes_executor",
        }),
        ("com/facebook/react/",),
    ),
    "hybrid web": (
        frozenset({
            "dcloud_control.xml", "cordova.js", "capacitor.config.json",
        }),
        ("assets/www/", "assets/public/", "io/dcloud/", "org/apache/cordova/", "com/getcapacitor/"),
    ),
    "nativescript": (
        frozenset({
            "libnativescript.so",
        }),
        ("com/tns/",),
    ),
    "jetpack compose": (
        frozenset({
            "compose_compiler",
        }),
        ("androidx/compose/", "androidx.compose", "composablesingletons", "compose_compiler"),
    ),
}
_MARKERS = [
    (k, sorted(_RUNTIME_RULES[k][0]) + list(_RUNTIME_RULES[k][1]))
    for k in RUNTIME_PRIORITY
]
_CLASSIC = "classic"


def _matches_rule(norm: str, base: str, exact_bases: frozenset[str], prefixes: tuple[str, ...]) -> bool:
    if base in exact_bases:
        return True
    for pref in prefixes:
        if pref.endswith("/"):
            bare = pref[:-1]
            if (
                norm.startswith(pref)
                or ("/" + pref) in norm
                or norm.startswith("l" + pref)
                or norm == bare
                or norm.endswith("/" + bare)
            ):
                return True
        elif pref == "androidx.compose":
            if (
                norm == pref
                or norm.startswith(pref + ".")
                or ("." + pref + ".") in norm
                or ("/" + pref) in norm
                or base == pref
                or base.startswith(pref + ".")
            ):
                return True
        elif pref in ("composablesingletons", "compose_compiler"):
            if (
                base == pref
                or base.startswith(pref + "$")
                or base.startswith(pref + ".")
                or base.startswith(pref + "_")
                or ("/" + pref + "$") in norm
                or ("/" + pref + ".") in norm
                or ("/" + pref + "/") in norm
            ):
                return True
    return False


def detect_from_names(names):
    """names: iterable von Datei-/Eintragsnamen (apk-Entries, lib-Dateien, Smali-Pfade, Strings).
    Rueckgabe: Produzent-Schluessel (ProducerRegistry) oder 'classic'."""
    normalized = []
    for n in names:
        if not n:
            continue
        norm = str(n).replace("\\", "/").strip().lower()
        if norm:
            normalized.append((norm, norm.rstrip("/").rsplit("/", 1)[-1]))
    for key in RUNTIME_PRIORITY:
        exact_bases, prefixes = _RUNTIME_RULES[key]
        if any(_matches_rule(norm, base, exact_bases, prefixes) for norm, base in normalized):
            return key
    return _CLASSIC


def detect_runtime(app_dir=None, names=None, max_files=20000):
    """Scant ein entpacktes App-Verzeichnis (oder nimmt eine fertige Namensliste) und erkennt das
    Toolkit. Fuer RN: unterscheidet nicht Alt-/Neu-Arch - der RN-Schluessel zeigt in der
    ProducerRegistry auf den echten C++-Hermes-Pfad (rn_hermes_bridge), der ohne Host/dist sauber
    auf den Node-Mock (rn_js_bridge) zurueckfaellt; die native Mount-Grenze (rn_producer) ebenso.
    Rueckgabe: Produzent-Schluessel."""
    if names is not None:
        return detect_from_names(names)
    if not app_dir or not os.path.isdir(app_dir):
        return _CLASSIC
    collected = []
    n = 0
    for root, dirs, files in os.walk(app_dir):
        for d in dirs:
            rel_d = os.path.relpath(os.path.join(root, d), app_dir).replace("\\", "/")
            if rel_d != ".":
                collected.append(rel_d + "/")
        for f in files:
            collected.append(f)
            # ganze relative Pfade auch pruefen (androidx/compose, io/dcloud, assets/www in APK-Baeumen)
            collected.append(os.path.relpath(os.path.join(root, f), app_dir).replace("\\", "/"))
            n += 1
            if n >= max_files:
                return detect_from_names(collected)
    return detect_from_names(collected)


def producer_for_app(app_dir=None, names=None, ctx=None):
    """Erkennt das Toolkit und liefert (schluessel, producer_callable_or_None). producer bekommt ctx
    beim Aufruf. 'classic' hat (bewusst) keinen eigenen Produzenten - der klassische android.view-Weg
    laeuft direkt ueber den ViewTree/Debugger."""
    from core.java.surface import producers
    key = detect_runtime(app_dir=app_dir, names=names)
    prod = producers.producer_for(key)
    return key, prod
