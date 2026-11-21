# -*- coding: utf-8 -*-
"""S1: Render-Produzenten-Abstraktion (SurfaceSource) — Szenegraph + Display-Liste + Registry.
Dirty-getrieben (Compute-Budget), Hit-Test, Event-Dispatch. Headless."""
from core.java.view_tree import ViewTree, ViewNode
from core.java.surface import (SurfaceSource, SceneGraphSurface, DisplayListSurface,
                               ProducerRegistry)


def _tree_with_button():
    root = ViewNode(cls="LinearLayout")
    btn = ViewNode(cls="Button", text="OK", clickable=True)
    root.add(btn) if hasattr(root, "add") else root.children.append(btn)
    t = ViewTree()
    t.set_root(root)
    return t, btn


def test_scenegraph_paint_and_dirty():
    t, btn = _tree_with_button()
    s = SceneGraphSurface(t)
    assert s.dirty is True                 # noch nie gezeichnet
    cmds = s.paint()
    assert any(c["op"] == "rect" for c in cmds)
    assert s.dirty is False                # nach paint: sauber (gleiche version)
    t.touch()                              # Aenderung -> dirty
    assert s.dirty is True
    assert s.width == t.width and s.height == t.height


def test_scenegraph_hit_and_dispatch():
    t, btn = _tree_with_button()
    fired = []
    btn.on_click = lambda n: fired.append(n)
    s = SceneGraphSurface(t)
    bx, by, bw, bh = btn.bounds
    node = s.hit(bx + 2, by + 2)
    assert node is btn
    assert s.dispatch({"type": "click", "x": bx + 2, "y": by + 2}) is True
    assert fired == [btn]
    assert s.dispatch({"type": "click", "x": 9999, "y": 9999}) is False


def test_displaylist_paint_hit_frame():
    clicked = []
    cmds = [{"op": "rect", "x": 0, "y": 0, "w": 100, "h": 40, "fill": "#fff"}]
    regions = [{"x": 0, "y": 0, "w": 100, "h": 40, "on": lambda e: clicked.append(e)}]
    s = DisplayListSurface(cmds, regions, width=200, height=400)
    assert s.dirty is True
    assert s.paint() == cmds and s.dirty is False
    assert s.hit(10, 10) is regions[0] and s.hit(150, 10) is None
    assert s.dispatch({"x": 10, "y": 10}) is True and len(clicked) == 1
    s.set_frame([{"op": "text", "x": 1, "y": 1, "text": "x"}])
    assert s.dirty is True and s.paint()[0]["op"] == "text"


def test_producer_registry_open_closed():
    # Instanzbasiert: eigene Registry, kein globaler Zustand und keine Lazy-Default-Verdrahtung.
    reg = ProducerRegistry()
    reg.set_default(lambda ctx: "default")
    reg.register("React Native", lambda ctx: "rn")
    assert reg.producer_for("react native")(None) == "rn"
    assert reg.producer_for("Flutter")(None) == "default"   # Fallback
    assert "react native" in reg.known()


def test_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        SurfaceSource()        # ABC nicht instanziierbar
