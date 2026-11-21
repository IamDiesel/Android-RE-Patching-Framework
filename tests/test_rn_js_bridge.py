# -*- coding: utf-8 -*-
import json
from core.java.rn_js_bridge import tree_from_mount_json, demo, node_available, run_js_app
from core.java.surface import snapshot, tap


def _login_json():
    return {"surfaceId": 11, "root": {"tag": 1, "component": "RCTView", "props": {}, "children": [
        {"tag": 2, "component": "Paragraph", "props": {"text": "Anmelden"}, "children": []},
        {"tag": 3, "component": "AndroidTextInput", "props": {"placeholder": "User"}, "children": []},
        {"tag": 5, "component": "RCTView", "props": {"onPress": True},
         "children": [{"tag": 6, "component": "Paragraph", "props": {"text": "Login"}, "children": []}]},
    ]}}


def test_headless_tree_from_dict():
    surface, rec = tree_from_mount_json(_login_json())
    snap = snapshot(surface)
    texts = " ".join(snap["text"])
    assert "Anmelden" in texts and "Login" in texts
    assert rec.root_tag == 1


def test_headless_accepts_json_string():
    surface, rec = tree_from_mount_json(json.dumps(_login_json()))
    assert rec.nodes
    assert len(snapshot(surface)["nodes"]) >= 4


def test_component_mapping_and_relayout():
    # AndroidTextInput -> EditText, Button-View bekommt eine Layout-Box (relayout)
    surface, rec = tree_from_mount_json(_login_json())
    nodes = snapshot(surface)["nodes"]
    assert any(n["cls"] == "EditText" for n in nodes)
    btn = [n for n in nodes if n["clickable"]]
    assert btn and btn[0]["bounds"][2] > 0  # Breite > 0 => positioniert


def test_tap_hits_button():
    surface, rec = tree_from_mount_json(_login_json())
    nodes = snapshot(surface)["nodes"]
    btn = [n for n in nodes if n["clickable"]][0]
    x = btn["bounds"][0] + btn["bounds"][2] / 2
    y = btn["bounds"][1] + btn["bounds"][3] / 2
    res = tap(surface, x, y)
    assert res["handled"]
    assert rec.events and rec.events[-1]["type"] == "press"


def test_demo_builds():
    surface, rec = demo()
    assert rec.root_tag == 1
    assert any("Anmelden" in t for t in snapshot(surface)["text"])


def test_node_integration_if_available():
    if not node_available():
        return
    surface, rec, raw = run_js_app()
    data = json.loads(raw)
    assert data["surfaceId"] == 11
    assert any("Login" in t for t in snapshot(surface)["text"])
