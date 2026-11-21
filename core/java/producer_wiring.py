# -*- coding: utf-8 -*-
"""core.java.producer_wiring — EINE zentrale, idempotente Verdrahtung der Render-Produzenten in eine
ProducerRegistry. Ersetzt die frueheren Import-Seiteneffekt-Registrierungen (deren Reihenfolge den
aufgeloesten Produzenten bestimmte). Wird von der globalen Registry `surface.producers` beim ersten
producer_for()/known() LAZY gezogen. Lazy-Importe vermeiden Zyklen."""


def ensure_default_producers(reg):
    """Registriert die Standard-Produzenten idempotent in `reg`. Familien-Keys (wie von runtime_detect
    geliefert) + eindeutige Einzel-Keys. RN -> echter C++-Hermes-Pfad wenn dist/ oder HERMES_HOST_CMD
    verfuegbar, sonst Node-Mock (rn-js). Reihenfolge-unabhaengig, weil hier ALLE Produzenten importiert
    werden (kein Verlass darauf, dass ein Modul anderswo zufaellig zuletzt importiert wurde)."""
    from core.java.canvas_recorder import canvas_producer
    from core.java.skia_recorder import skia_producer
    from core.java.rn_producer import rn_producer as rn_s3a_producer
    from core.java.rn_js_bridge import rn_js_producer
    from core.java.rn_hermes_bridge import rn_hermes_producer, hermes_host_available

    # Eindeutige Einzel-Keys (kein Ueberschreiben)
    reg.register("compose", canvas_producer)
    reg.register("jetpack compose", canvas_producer)
    reg.register("flutter", skia_producer)
    reg.register("rn-s3a", rn_s3a_producer)
    reg.register("rn-js", rn_js_producer)
    reg.register("rn-hermes", rn_hermes_producer)

    # Familien-Keys (von detect_runtime): RN -> Hermes wenn verfuegbar, sonst Node-Mock
    rn = rn_hermes_producer if hermes_host_available() else rn_js_producer
    reg.register("react native", rn)
    reg.register("react native (js)", rn)
