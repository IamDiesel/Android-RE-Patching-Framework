# -*- coding: utf-8 -*-
"""S2: Canvas-Recorder (Familie B) — android.graphics.Canvas-Zeichenbefehle -> Display-Liste.
Transform-Stack (save/restore/translate), Paint-Farbe, Producer-Registry. Headless."""
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString
from core.java.canvas_recorder import install_canvas_stubs, canvas_producer
from core.java.surface import ProducerRegistry, DisplayListSurface, producers

_CV = "Landroid/graphics/Canvas;"
_PT = "Landroid/graphics/Paint;"


def _setup():
    s = AndroidStubs()
    rec = install_canvas_stubs(s)
    return s, rec, JObject(_CV), JObject(_PT)


def test_draw_rect_with_paint_color():
    s, rec, cv, pt = _setup()
    s.resolve(_PT, "setColor", "(I)V", (pt, 0xFFFF0000), False)   # rot
    s.resolve(_CV, "drawRect", "(FFFFLandroid/graphics/Paint;)V", (cv, 0, 0, 100, 50, pt), False)
    cmds = rec.surface().paint()
    r = [c for c in cmds if c["op"] == "rect"][0]
    assert (r["x"], r["y"], r["w"], r["h"]) == (0, 0, 100, 50)
    assert r["fill"] == "#ff0000"


def test_translate_applies_to_following_ops():
    s, rec, cv, pt = _setup()
    s.resolve(_CV, "translate", "(FF)V", (cv, 10, 20), False)
    s.resolve(_CV, "drawCircle", "(FFFLandroid/graphics/Paint;)V", (cv, 5, 5, 5, pt), False)
    s.resolve(_CV, "drawText", "(Ljava/lang/String;FFLandroid/graphics/Paint;)V",
              (cv, JString("Hi"), 0, 30, pt), False)
    cmds = rec.surface().paint()
    oval = [c for c in cmds if c["op"] == "oval"][0]
    assert (oval["x"], oval["y"], oval["w"], oval["h"]) == (10, 20, 10, 10)   # (cx-r)+tx usw.
    txt = [c for c in cmds if c["op"] == "text"][0]
    assert txt["text"] == "Hi" and (txt["x"], txt["y"]) == (10, 50)


def test_save_restore_isolates_transform():
    s, rec, cv, pt = _setup()
    s.resolve(_CV, "save", "()I", (cv,), False)
    s.resolve(_CV, "translate", "(FF)V", (cv, 100, 0), False)
    s.resolve(_CV, "drawRect", "(FFFFLandroid/graphics/Paint;)V", (cv, 0, 0, 10, 10, pt), False)
    s.resolve(_CV, "restore", "()V", (cv,), False)
    s.resolve(_CV, "drawRect", "(FFFFLandroid/graphics/Paint;)V", (cv, 0, 0, 10, 10, pt), False)
    rects = [c for c in rec.surface().paint() if c["op"] == "rect"]
    assert rects[0]["x"] == 100 and rects[1]["x"] == 0    # restore hebt Translation auf


def test_surface_is_displaylist_with_regions():
    s, rec, cv, pt = _setup()
    s.resolve(_CV, "drawRect", "(FFFFLandroid/graphics/Paint;)V", (cv, 5, 5, 25, 15, pt), False)
    surf = rec.surface()
    assert isinstance(surf, DisplayListSurface)
    assert surf.hit(10, 10) is not None and surf.hit(999, 999) is None


def test_producer_registered():
    assert producers.producer_for("Jetpack Compose") is canvas_producer
    assert producers.producer_for("Compose") is canvas_producer
