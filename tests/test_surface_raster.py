# -*- coding: utf-8 -*-
"""Render-Rasterizer: SurfaceSource -> PNG (Grafik kommt raus). Headless (PIL)."""
import os
import pytest
pytest.importorskip("PIL")   # ohne Pillow (z.B. frische Python-Installation) -> Test ueberspringen statt Suite abbrechen
from core.java.surface import SceneGraphSurface, DisplayListSurface
from core.java.view_tree import ViewTree, ViewNode
from tools.surface_raster import render_surface, render_surface_to_png


def _tree():
    root = ViewNode(cls="LinearLayout")
    root.children.append(ViewNode(cls="Button", text="OK", clickable=True))
    t = ViewTree(); t.set_root(root); return t


def test_render_scenegraph_png(tmp_path):
    p = str(tmp_path / "sg.png")
    render_surface_to_png(SceneGraphSurface(_tree()), p)
    assert os.path.isfile(p) and os.path.getsize(p) > 500


def test_render_displaylist_image():
    s = DisplayListSurface([{"op": "rect", "x": 10, "y": 10, "w": 50, "h": 20, "fill": "#2f6df0"},
                            {"op": "text", "x": 12, "y": 40, "text": "Hallo", "color": "#000"}])
    im = render_surface(s)
    assert im.size[0] > 380 and im.size[1] > 700   # Telefonrahmen
