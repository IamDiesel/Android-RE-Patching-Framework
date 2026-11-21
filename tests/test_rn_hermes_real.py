# -*- coding: utf-8 -*-
import os, sys, pathlib, shlex
import pytest
from core.java.surface import snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "tools" / "rn_hermes_host" / "dist"
RUN  = DIST / "run_host.sh"
HBC  = DIST / "test_app.hbc"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not (RUN.exists() and HBC.exists()),
    reason="S3-Hermes Linux-bash-Host (dist/run_host.sh) nicht lauffaehig auf Windows / dist fehlt")


def _client():
    from core.java.rn_hermes_bridge import HermesHostClient
    return HermesHostClient(["bash", str(RUN), str(HBC)], width=380, height=800)


def test_real_host_commits_surface():
    c = _client().start()
    try:
        surf = c.run_until_ready()
        assert surf is not None
        texts = " ".join(snapshot(surf)["text"])
        assert "LibreLinkUp" in texts and "Anmelden" in texts
    finally:
        c.stop()


def test_real_host_event_roundtrip():
    # AP1: Tap muss ueber den Hermes-Event-Handler einen ECHTEN zweiten Commit ausloesen,
    # der den Zustand sichtbar aendert (nicht nur ein Pipe-ack).
    c = _client().start()
    try:
        c.run_until_ready()
        assert "Anmelden" in " ".join(snapshot(c.surface)["text"])
        c.send_event("press", tag=5)
        c.pump_until(lambda cl: bool(cl.acks) or cl.done)
        assert c.acks and c.acks[-1]["type"] == "press"
        assert "Angemeldet" in " ".join(snapshot(c.surface)["text"])  # Re-Commit sichtbar
    finally:
        c.stop()


def test_hermes_host_cmd_env():
    # ENV-Weg (HERMES_HOST_CMD), leerzeichenfest via shlex.quote
    os.environ["HERMES_HOST_CMD"] = "bash " + shlex.quote(str(RUN)) + " " + shlex.quote(str(HBC))
    try:
        from core.java.rn_hermes_bridge import HermesHostClient
        c = HermesHostClient(width=380, height=800).start()   # host_cmd=None -> aus ENV
        try:
            assert c.run_until_ready() is not None
        finally:
            c.stop()
    finally:
        os.environ.pop("HERMES_HOST_CMD", None)


HBC_ROW = DIST / "test_app_row.hbc"


@pytest.mark.skipif(not HBC_ROW.exists(), reason="Row-Bundle (AP2) nicht vorhanden")
def test_real_host_yoga_row_layout():
    # AP2: echtes C++-Yoga -> horizontales Flexbox (flexDirection:row) im Render-Output.
    from core.java.rn_hermes_bridge import HermesHostClient
    c = HermesHostClient(["bash", str(RUN), str(HBC_ROW)], width=380, height=800).start()
    try:
        surf = c.run_until_ready()
        nodes = snapshot(surf)["nodes"]
        boxes = [n["bounds"] for n in nodes
                 if round(n["bounds"][2]) == 100 and round(n["bounds"][3]) == 60]
        assert len(boxes) == 3, boxes
        xs = sorted(round(b[0]) for b in boxes)
        assert xs == [10, 110, 210], xs                       # nebeneinander
        assert len({round(b[1]) for b in boxes}) == 1          # gleiche y-Reihe
    finally:
        c.stop()


HBC_RF = DIST / "reactfabric.hbc"


@pytest.mark.skipif(not HBC_RF.exists(), reason="ReactFabric-Bundle (AP3) nicht vorhanden")
def test_real_host_reactfabric_interactive():
    # AP3: ECHTES react 19 + ReactFabric-prod (RN 0.83) auf dem realen Hermes-0.14-Host.
    # Boot -> Login-Commit; Koordinaten-Tap feuert ueber den registrierten Fabric-Event-Handler
    # (nativeFabricUIManager.registerEventHandler) einen echten zweiten Commit -> Dashboard.
    from core.java.rn_hermes_bridge import HermesHostClient
    c = HermesHostClient(["bash", str(RUN), str(HBC_RF)], width=380, height=800).start()
    try:
        surf = c.run_until_ready()
        t1 = snapshot(surf)["text"]
        assert "LibreLinkUp (ReactFabric)" in t1 and "Anmelden" in t1, t1
        btn = [n for n in snapshot(surf)["nodes"] if n.get("text") == "Anmelden"][0]["bounds"]
        r = c.tap(btn[0] + btn[2] / 2, btn[1] + btn[3] / 2, type="topTouchEnd")   # Button-Mitte (layoutfest)
        assert r.get("handled") and r.get("tag") == 10, r
        t2 = snapshot(c.surface)["text"]
        assert "Zeile 1" in t2 and "Zeile 2" in t2, t2    # echter ReactFabric-Re-Commit sichtbar
    finally:
        c.stop()


HBC_RNREAL = DIST / "rnreal.hbc"


@pytest.mark.skipif(not HBC_RNREAL.exists(), reason="Echtes RN-0.83-Bundle (rnreal.hbc) nicht vorhanden")
def test_real_host_rn_real_boot_interactive():
    # Echter RN-0.83-Bootvertrag: InitializeCore + AppRegistry.registerComponent -> Host-runApplication,
    # echte Komponenten (View/Text/TextInput/Pressable), echte Topologie (RCTText + RCTRawText),
    # echtes Responder-/Change-Event-System. key() tippt ins AndroidTextInput, tap() drueckt die Pressable.
    from core.java.rn_hermes_bridge import HermesHostClient
    c = HermesHostClient(["bash", str(RUN), str(HBC_RNREAL)], width=380, height=800).start()
    try:
        surf = c.run_until_ready()
        t1 = snapshot(surf)["text"]
        assert "LibreLinkUp (ReactFabric)" in t1 and "Anmelden" in t1, t1
        assert "E-Mail" in t1 and "Passwort" in t1, t1          # echte TextInput-Platzhalter
        # Echte RN-Text-Topologie (RCTText-Container + RCTRawText-Kind mit props.text) ist dadurch
        # belegt, dass der Label-Text ueberhaupt erscheint: er lebt im RCTRawText-Blatt, das die
        # Textmessung/-extraktion korrekt aggregiert (sonst waere der Text leer).
        # Texteingabe ueber echtes onChangeText
        kr = c.key(190, 53, "dia@beto.de")
        assert kr.get("handled"), kr
        # Druck auf die echte Pressable (Responder-System) -> onPress -> Re-Commit
        btn = [n for n in snapshot(c.surface)["nodes"] if n.get("text") == "Anmelden"][0]["bounds"]
        tr = c.tap(btn[0] + btn[2] / 2, btn[1] + btn[3] / 2)
        assert tr.get("handled"), tr
        t2 = snapshot(c.surface)["text"]
        assert "Zeile 1" in t2 and "dia@beto.de" in t2, t2      # getippte E-Mail im Dashboard sichtbar
    finally:
        c.stop()


HBC_TIMERS = DIST / "timers_test.hbc"


@pytest.mark.skipif(not HBC_TIMERS.exists(), reason="Timer-Test-Bundle nicht vorhanden")
def test_real_host_timer_microtask_separation():
    # Endlosschleifen-Schutz: verzögerte Timer (setInterval 10ms, setTimeout 500ms) feuern NICHT
    # automatisch beim Eval/Drain; nur Sofort-Arbeit (setImmediate, setTimeout 0) läuft. Verzögerte
    # Timer rücken ausschließlich über advance_time(ms) vor.
    from core.java.rn_hermes_bridge import HermesHostClient
    c = HermesHostClient(["bash", str(RUN), str(HBC_TIMERS)], width=380, height=800).start()
    try:
        c.run_until_ready()
        logs = " ".join(m.get("text", "") for m in c.logs)
        assert "immediate0" in logs and "setImmediate" in logs, logs   # Sofort-Arbeit lief
        assert "tick" not in logs, "verzögerter setInterval feuerte beim Auto-Drain (Endlosschleifen-Gefahr)"
        assert "delayed500" not in logs, logs
        # Jetzt virtuelle Zeit vorrücken -> genau die fälligen Ticks (10/20/30 ms)
        c.advance_time(35)
        logs = " ".join(m.get("text", "") for m in c.logs)
        assert "tick 1" in logs and "tick 3" in logs, logs
        assert "tick 4" not in logs, "zu viele Ticks nach +35ms (nur 3 fällig)"
        assert "delayed500" not in logs, "500ms-Timer darf nach +35ms noch nicht feuern"
        # Weiter bis über 500 ms -> delayed500 feuert
        c.advance_time(500)
        logs = " ".join(m.get("text", "") for m in c.logs)
        assert "delayed500" in logs, logs
    finally:
        c.stop()
