# -*- coding: utf-8 -*-
"""S1b: gläserne Surface-Interaktion (snapshot/tap/key) — eine Logik fuer GUI + MCP. Headless."""
from core.java.view_tree import ViewTree, ViewNode
from core.java.surface import (SceneGraphSurface, DisplayListSurface, snapshot, tap, key)


def _tree():
    root = ViewNode(cls="LinearLayout")
    tv = ViewNode(cls="TextView", text="Anmelden")
    btn = ViewNode(cls="Button", text="OK", clickable=True)
    for n in (tv, btn):
        root.children.append(n)
    t = ViewTree(); t.set_root(root)
    return t, btn


def test_snapshot_scenegraph():
    t, btn = _tree()
    snap = snapshot(SceneGraphSurface(t))
    assert snap["width"] == t.width and snap["height"] == t.height
    assert "Anmelden" in snap["text"]
    classes = [n["cls"] for n in snap["nodes"]]
    assert "Button" in classes and "TextView" in classes


def test_tap_scenegraph_dispatch():
    t, btn = _tree()
    fired = []
    btn.on_click = lambda n: fired.append(n)
    s = SceneGraphSurface(t)
    bx, by, bw, bh = btn.bounds
    assert tap(s, bx + 2, by + 2) == {"handled": True} and fired == [btn]
    assert tap(s, 9999, 9999) == {"handled": False}


def test_snapshot_displaylist():
    cmds = [{"op": "rect", "x": 0, "y": 0, "w": 10, "h": 10, "fill": "#fff"},
            {"op": "text", "x": 1, "y": 1, "text": "Hallo", "color": "#000"}]
    s = DisplayListSurface(cmds, width=120, height=240)
    snap = snapshot(s)
    assert snap["paint"] == cmds and snap["text"] == ["Hallo"]
    assert snap["nodes"] == []            # immediate-mode hat keinen Baum


def test_tap_displaylist_region():
    clicked = []
    s = DisplayListSurface([{"op": "rect", "x": 0, "y": 0, "w": 50, "h": 20}],
                           [{"x": 0, "y": 0, "w": 50, "h": 20, "on": lambda e: clicked.append(e)}])
    assert tap(s, 10, 10)["handled"] and len(clicked) == 1
