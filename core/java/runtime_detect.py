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

# Marker (Teilstring, case-insensitive) -> Produzent-Schluessel. Reihenfolge = Prioritaet.
_MARKERS = [
    ("flutter",            ["libflutter.so", "libapp.so", "flutter_assets", "kernel_blob.bin",
                            "isolate_snapshot_data", "vm_snapshot_data"]),
    ("react native (js)",  ["libhermes.so", "libjsc.so", "index.android.bundle", "libreactnative.so",
                            "libreactnativejni.so", "hermes_executor", "libjscexecutor.so"]),
    ("jetpack compose",    ["androidx.compose", "androidx/compose", "compose_compiler",
                            "androidx.compose.ui", "ComposableSingletons"]),
]
_CLASSIC = "classic"


def detect_from_names(names):
    """names: iterable von Datei-/Eintragsnamen (apk-Entries, lib-Dateien, Smali-Pfade, Strings).
    Rueckgabe: Produzent-Schluessel (ProducerRegistry) oder 'classic'."""
    low = [str(n).lower() for n in names if n]
    for key, markers in _MARKERS:
        for m in markers:
            ml = m.lower()
            if any(ml in n for n in low):
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
    for root, _dirs, files in os.walk(app_dir):
        for f in files:
            collected.append(f)
            # ganze relative Pfade auch pruefen (androidx/compose/... in smali-Baeumen)
            collected.append(os.path.join(os.path.basename(root), f))
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
