# -*- coding: utf-8 -*-
"""S3-Hermes-Integration: ProducerRegistry/runtime_detect + MCP-Bindung (native.ui_tap/ui_key)."""
import sys
import pathlib
import pytest
from core.java.surface import snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "tools" / "rn_hermes_host" / "dist"
RUN = DIST / "run_host.sh"
RNREAL = DIST / "rnreal.hbc"


def _import_producers():
    import core.java.rn_js_bridge          # registriert RN-Keys (Node-Mock)
    import core.java.rn_hermes_bridge      # ueberschreibt sie mit dem echten Hermes-Pfad
    return core.java.rn_hermes_bridge


def test_registry_rn_maps_to_hermes():
    # rn-hermes zeigt immer auf den Hermes-Produzenten; react native(+js) adaptiv:
    # mit verfuegbarem Host -> Hermes, sonst Zero-Config-Fallback auf den Node-Mock.
    rh = _import_producers()
    from core.java.surface import producers
    from core.java.rn_js_bridge import rn_js_producer
    cond = rh.rn_hermes_producer if rh.hermes_host_available() else rn_js_producer
    assert producers.producer_for("rn-hermes") is rh.rn_hermes_producer
    assert producers.producer_for("react native") is cond
    assert producers.producer_for("react native (js)") is cond


def test_runtime_detect_rn_resolves_to_hermes():
    # detect_runtime(libhermesvm.so/index.android.bundle) -> RN-Key -> Hermes-Produzent.
    _import_producers()
    from core.java.runtime_detect import producer_for_app
    import core.java.rn_hermes_bridge as rh
    key, prod = producer_for_app(names=["libhermesvm.so", "index.android.bundle"])
    assert "react native" in key, key
    from core.java.rn_js_bridge import rn_js_producer
    assert prod is (rh.rn_hermes_producer if rh.hermes_host_available() else rn_js_producer)


def test_hermes_producer_fallback_to_node(monkeypatch):
    # Ohne Host/dist faellt der Produzent sauber auf den Node-Mock zurueck (ohne Node zu brauchen).
    rh = _import_producers()
    monkeypatch.setattr(rh, "hermes_host_available", lambda hbc=None: False)
    import core.java.rn_js_bridge as js
    called = {}
    monkeypatch.setattr(js, "rn_js_producer", lambda ctx: (called.__setitem__("ctx", ctx), "FALLBACK_SURFACE")[1])
    out = rh.rn_hermes_producer({"foo": "bar"})
    assert out == "FALLBACK_SURFACE" and called["ctx"] == {"foo": "bar"}


@pytest.mark.skipif(sys.platform == "win32" or not (RUN.exists() and RNREAL.exists()), reason="Hermes-Host/dist nicht vorhanden / Linux-bash-Host")
def test_hermes_producer_boots_real_surface():
    # Der Produzent bootet eine echte Hermes-Surface (ueber dist/) mit lebendem Client am Surface.
    rh = _import_producers()
    surf = rh.rn_hermes_producer({"hbc": str(RNREAL)})
    try:
        assert surf is not None
        assert "LibreLinkUp (ReactFabric)" in " ".join(snapshot(surf)["text"])
        assert getattr(surf, "_hermes_client", None) is not None
    finally:
        c = getattr(surf, "_hermes_client", None)
        if c is not None:
            c.stop()


@pytest.mark.skipif(sys.platform == "win32" or not (RUN.exists() and RNREAL.exists()), reason="Hermes-Host/dist nicht vorhanden / Linux-bash-Host")
def test_mcp_ui_tap_key_through_hermes():
    # native.ui_tap/ui_key routen ueber den lebenden Hermes-Client -> echter Re-Commit.
    rh = _import_producers()
    import mcp_server.tools as T
    surf = rh.rn_hermes_producer({"hbc": str(RNREAL)})
    client = getattr(surf, "_hermes_client", None)
    try:
        T._LAST_UI["surface"] = surf
        # Text ins Eingabefeld (ohne Koordinaten -> erstes Eingabefeld)
        rk = T.native_ui_key({}, "dia@beto.de")
        assert rk.get("ok") and rk.get("handled"), rk
        # Button druecken (Anmelden-Mitte)
        btn = [n for n in snapshot(T._LAST_UI["surface"])["nodes"] if n.get("text") == "Anmelden"][0]["bounds"]
        rt = T.native_ui_tap({}, btn[0] + btn[2] / 2, btn[1] + btn[3] / 2)
        assert rt.get("ok") and rt.get("handled"), rt
        texts = " ".join(snapshot(T._LAST_UI["surface"])["text"])
        assert "Zeile 1" in texts and "dia@beto.de" in texts, texts
    finally:
        T._LAST_UI["surface"] = None
        if client is not None:
            client.stop()
