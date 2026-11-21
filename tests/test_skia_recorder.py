# -*- coding: utf-8 -*-
"""S4a: Skia-Rekorder (Familie C, Flutter) — DlOpReceiver-/SkCanvas-Ops -> DisplayListSurface.
Matrix-Stack, Gradient-Flatten, DisplayList-Seam-Namen. Headless."""
from core.java.skia_recorder import SkiaRecorder, _flatten_gradient
from core.java.surface import DisplayListSurface, ProducerRegistry, snapshot, producers


def test_rrect_and_translate_matrix():
    r = SkiaRecorder()
    r.save(); r.translate(10, 20); r.scale(2, 1)
    r.draw_round_rect(0, 0, 50, 10, 8, {"color": 0xFF2F6DF0})
    r.restore()
    cmd = [c for c in r.surface().paint() if c["op"] == "rect"][0]
    # (0,0)->(10,20) ; (50,10)*scale(2,1)+translate -> (110,30) => x=10,y=20,w=100,h=10
    assert (cmd["x"], cmd["y"], cmd["w"], cmd["h"]) == (10, 20, 100, 10)
    assert cmd["fill"] == "#2f6df0" and cmd.get("r") == 8


def test_gradient_flatten():
    assert _flatten_gradient([0xFF000000, 0xFFFFFFFF]) == "#7f7f7f"
    r = SkiaRecorder()
    r.draw_rect(0, 0, 10, 10, {"gradient": [0xFFFF0000, 0xFF0000FF]})
    assert r.surface().paint()[0]["fill"] == "#7f007f"


def test_circle_and_arc_and_diffrrect():
    r = SkiaRecorder()
    r.draw_circle(50, 50, 10, {"color": 0xFF112233})
    r.draw_arc(0, 0, 20, 20, 0, 270, {"color": 0xFF445566})
    r.draw_diff_round_rect(0, 0, 40, 40, 5, 5, 35, 35, 6, {"color": 0xFF778899})
    ops = [c["op"] for c in r.surface().paint()]
    assert ops == ["oval", "oval", "rect"]


def test_demo_surface_and_registry():
    from core.java.skia_recorder import demo, skia_producer
    surf = demo().surface()
    assert isinstance(surf, DisplayListSurface)
    snap = snapshot(surf)
    assert any("Anmelden" in t for t in snap["text"])
    assert producers.producer_for("Flutter") is skia_producer
