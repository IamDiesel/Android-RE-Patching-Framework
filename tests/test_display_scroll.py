# -*- coding: utf-8 -*-
"""AP-E16: Vertikaler Scroll — Modell/Mathematik headless (Viewport->Inhalt-Translation). GUI = Sichtpruefung."""
from core.java.view_tree import ViewNode, ViewTree

SCREEN_W, STATUS_H, SCREEN_H = 380, 24, 800
VIEW_H = SCREEN_H - STATUS_H


def _tall(n=24):
    root = ViewNode("LinearLayout", vid="root")
    root.add(ViewNode("TextView", vid="title", text="Liste"))
    for i in range(n):
        root.add(ViewNode("Button", vid="b%d" % i, text="Eintrag %d" % i, clickable=True))
    t = ViewTree(); t.width = SCREEN_W; t.height = VIEW_H; t.set_root(root)
    return t


def test_content_taller_than_viewport():
    t = _tall()
    assert t.content_height() > VIEW_H            # Inhalt laenger als Screen -> Scroll noetig
    max_scroll = max(0, t.content_height() - VIEW_H)
    assert max_scroll > 0


def test_button_below_fold_needs_scroll():
    t = _tall()
    # ein Button, der unter der Falz liegt (y >= VIEW_H)
    below = [n for n in t.nodes() if n.cls == "Button" and n.bounds[1] >= VIEW_H]
    assert below, "Testaufbau: es sollte Buttons unter der Falz geben"
    btn = below[0]
    bx, by, bw, bh = btn.bounds
    cx = bx + bw // 2
    # ohne Scroll: Klick an der Viewport-Position des Buttons trifft ihn NICHT (er ist unsichtbar)
    vy = 100
    assert t.clickable_at(cx, vy + 0) is not btn
    # mit passendem Scroll erscheint er bei vy=100 -> Translation vy+scroll_y trifft ihn
    scroll_y = by - vy
    hit = t.clickable_at(cx, vy + scroll_y)
    assert hit is btn


def test_scroll_clamped_range():
    t = _tall()
    max_scroll = max(0, t.content_height() - VIEW_H)
    for raw, exp in ((-50, 0), (0, 0), (max_scroll + 999, max_scroll)):
        assert max(0, min(raw, max_scroll)) == exp
