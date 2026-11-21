# -*- coding: utf-8 -*-
"""core.java.rn_js_bridge — S3-JS-Weg: echtes App-JS auf echter JS-Engine (Node jetzt, eingebettetes
Hermes spaeter) laeuft gegen einen Mock von `global.nativeFabricUIManager` (tools/rn_js_host/host.js).
Der Mock zeichnet die Fabric-Mount-/Commit-Calls auf und liefert den committeten Baum als JSON:
    {"surfaceId": int, "root": {"tag","component","props","children":[...]}}
Diese Bruecke konsumiert dieses JSON und baut daraus ueber den SurfaceMountingManager-Shim
(`RnMountRecorder`) einen ViewTree. Da der JS-Host KEIN Yoga faehrt (kein updateLayout-Strom),
positioniert unser eigener Layout-Engine (relayout=True, vertikaler Stack).

Autoritative Fabric-Hostschnittstelle (nativeFabricUIManager), verifiziert gegen
react-native 0.85 UIManagerBinding.cpp + Reacts eigenen Fabric-Test-Mock:
  createNode(tag, componentName, surfaceId, props, instanceHandle) -> node
  appendChild(parent, child); createChildSet() -> []; appendChildToSet(set, node)
  completeRoot(surfaceId, childSet)  <- Commit-Ausloeser -> Baum
Re-Render: cloneNodeWithNewProps/cloneNodeWithNewChildren + neuer createChildSet/completeRoot
(= "ersetze den Baum dieser Surface"). Glaesern: jeder Mount-Call ist im Recorder nachvollziehbar.
Grundlage: [[recherche-rn-fabric-mount-grenze-2026-11-12]], Engineering-Brief RN-JS-Host."""
from __future__ import annotations

import json
import os
import subprocess

from core.java.rn_producer import RnMountRecorder
from core.java.surface import ProducerRegistry

_HOST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "tools", "rn_js_host")


def _walk(rec, node, parent_tag=None, index=0):
    """Spielt den committeten Baum als SurfaceMountingManager-Sequenz nach: createView je Knoten,
    addViewAt zum Parent - exakt die Mount-Semantik, nur ohne Yoga-Layout (kommt per relayout)."""
    if not isinstance(node, dict):
        return
    tag = int(node["tag"])
    rec.create_view(tag, node.get("component", "View"), node.get("props") or {})
    _n0 = rec.nodes.get(tag)
    if _n0 is not None:
        _n0._rn_tag = tag                     # RN-Tag am Knoten (Koordinaten->Tag beim Tap)
    lay = node.get("layout")
    if isinstance(lay, dict):                      # AP2: echte Yoga-Boxen aus dem C++-Host
        rec.update_layout(tag, lay.get("x", 0), lay.get("y", 0), lay.get("width", 0), lay.get("height", 0))
        rec._has_layout = True
    n = rec.nodes.get(tag)
    if n is not None and getattr(n, "clickable", False):
        # Glaesern + manipulierbar: Tap -> Press-Event im Recorder protokollieren (entspricht
        # Fabrics EventEmitterWrapper.dispatchEvent(eventTarget, "topPress", payload) -> onPress).
        comp = node.get("component", "View")
        def _press(_node, _tag=tag, _comp=comp):
            rec.events.append({"type": "press", "tag": _tag, "component": _comp})
        n.on_click = _press
    if parent_tag is None:
        rec.set_root(tag)
    else:
        rec.add_view_at(parent_tag, tag, index)
    for i, child in enumerate(node.get("children") or []):
        _walk(rec, child, tag, i)


def tree_from_mount_json(data, width=380, height=800):
    """Mount-JSON (dict ODER JSON-String) -> SceneGraphSurface. Headless, kein Node noetig -
    damit in Tests deterministisch pruefbar. relayout=True: unser Layout-Engine positioniert."""
    if isinstance(data, (str, bytes)):
        data = json.loads(data)
    root = data.get("root") if isinstance(data, dict) else None
    rec = RnMountRecorder(width, height)
    rec.events = []
    rec._has_layout = False
    if root is not None:
        _walk(rec, root)
    # Yoga-Boxen (C++-Host) -> relayout=False; JS-Host ohne Yoga -> vertikaler Stack (relayout=True)
    return rec.surface(relayout=not rec._has_layout), rec


def run_js_app(script="run.js", node="node", timeout=30, host_dir=None):
    """Startet die echte JS-Engine (Node) mit dem Host-Mock + App-JS, parst das emittierte
    Mount-JSON und baut die Surface. Rueckgabe: (surface, recorder, raw_json). ps_session-Logging
    uebernimmt der Aufrufer (MCP/PowerShell); hier reiner In-Container-Lauf."""
    hd = host_dir or _HOST_DIR
    path = os.path.join(hd, script)
    out = subprocess.run([node, path], capture_output=True, text=True, timeout=timeout, cwd=hd)
    if out.returncode != 0:
        raise RuntimeError("node host failed: " + (out.stderr or "")[:400])
    raw = out.stdout.strip()
    surface, rec = tree_from_mount_json(raw)
    return surface, rec, raw


def node_available(node="node"):
    try:
        subprocess.run([node, "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def rn_js_producer(ctx):
    """ProducerRegistry-Fabrik. ctx kann 'mount_json' (dict/str) ODER 'script' (Node-Lauf) tragen."""
    ctx = ctx if isinstance(ctx, dict) else {}
    if ctx.get("mount_json") is not None:
        surface, _ = tree_from_mount_json(ctx["mount_json"])
        return surface
    surface, _, _ = run_js_app(ctx.get("script", "run.js"))
    return surface


# Registrierung zentral in core.java.producer_wiring.ensure_default_producers (kein Import-Seiteneffekt).


def demo():
    """Deterministischer Login-Baum ohne Node (dieselbe Struktur wie tools/rn_js_host/app_login.js)."""
    data = {"surfaceId": 11, "root": {"tag": 1, "component": "RCTView", "props": {}, "children": [
        {"tag": 2, "component": "Paragraph", "props": {"text": "Anmelden (React Native / JS)"}, "children": []},
        {"tag": 3, "component": "AndroidTextInput", "props": {"placeholder": "Benutzername"}, "children": []},
        {"tag": 4, "component": "AndroidTextInput", "props": {"placeholder": "Passwort"}, "children": []},
        {"tag": 5, "component": "RCTView", "props": {"accessibilityRole": "button", "onPress": True},
         "children": [{"tag": 6, "component": "Paragraph", "props": {"text": "Login"}, "children": []}]},
    ]}}
    surface, rec = tree_from_mount_json(data)
    return surface, rec
