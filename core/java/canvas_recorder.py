# -*- coding: utf-8 -*-
"""core.java.canvas_recorder — Render-Produzent Familie B: android.graphics.Canvas-Zeichenbefehle
aufzeichnen (Jetpack Compose, custom View.onDraw, 2D). Immediate-mode: die App zeichnet selbst
ueber Canvas; wir fangen die drawX-Aufrufe ab und bauen eine Display-Liste (paint_commands) +
Hit-Regionen -> `DisplayListSurface`. Transform-Stack (save/restore/translate), Paint-Farbe aus dem
Paint-Objekt. Zielagnostisch; registriert sich beim Import in der ProducerRegistry.

Ein Flush je Frame: `surface()` liefert die aktuelle Display-Liste (Compute-Budget)."""
from __future__ import annotations

from core.java.objects import JString
from core.java.surface import DisplayListSurface, ProducerRegistry


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _s(v):
    return v.text if isinstance(v, JString) else ("" if v is None else str(v))


def _argb_to_hex(v):
    v = int(v) & 0xFFFFFFFF
    return "#%02x%02x%02x" % ((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff)


def _paint_color(paint, default="#000000"):
    try:
        c = paint.fields.get("$color")
        return _argb_to_hex(c) if c is not None else default
    except Exception:
        return default


class CanvasRecorder:
    """Sammelt Canvas-Zeichenbefehle als paint_commands (immediate-mode)."""

    def __init__(self, width=380, height=800):
        self.width = width
        self.height = height
        self.cmds = []
        self.regions = []
        self._stack = [(0.0, 0.0)]      # Translations-Stack

    # --- Transform-Stack ---
    def save(self):
        self._stack.append(self._stack[-1])

    def restore(self):
        if len(self._stack) > 1:
            self._stack.pop()

    def translate(self, dx, dy):
        x, y = self._stack[-1]
        self._stack[-1] = (x + _f(dx), y + _f(dy))

    @property
    def _tx(self):
        return self._stack[-1][0]

    @property
    def _ty(self):
        return self._stack[-1][1]

    # --- Zeichenbefehle ---
    def rect(self, l, t, r, b, color="#000000"):
        x, y = _f(l) + self._tx, _f(t) + self._ty
        w, h = _f(r) - _f(l), _f(b) - _f(t)
        self.cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                          "fill": color, "outline": ""})
        self.regions.append({"x": x, "y": y, "w": w, "h": h})

    def circle(self, cx, cy, rad, color="#000000"):
        x, y = _f(cx) - _f(rad) + self._tx, _f(cy) - _f(rad) + self._ty
        d = 2 * _f(rad)
        self.cmds.append({"op": "oval", "x": x, "y": y, "w": d, "h": d,
                          "fill": color, "outline": ""})

    def line(self, x1, y1, x2, y2, color="#000000"):
        self.cmds.append({"op": "line", "x": _f(x1) + self._tx, "y": _f(y1) + self._ty,
                          "x2": _f(x2) + self._tx, "y2": _f(y2) + self._ty, "color": color})

    def text(self, s, x, y, color="#000000"):
        self.cmds.append({"op": "text", "x": _f(x) + self._tx, "y": _f(y) + self._ty,
                          "text": s, "color": color, "anchor": "w"})

    # --- Ergebnis ---
    def surface(self):
        return DisplayListSurface(self.cmds, self.regions, width=self.width, height=self.height)


def install_canvas_stubs(stubs, rec=None):
    """Haengt android.graphics.Canvas/Paint-Zeichen-Stubs in `AndroidStubs` ein. Rueckgabe: der
    `CanvasRecorder` (auch `stubs.canvas_recorder`)."""
    rec = rec if rec is not None else CanvasRecorder()
    stubs.canvas_recorder = rec
    bn = stubs.register_by_name

    # Paint: Farbe/Stil merken
    def _set_color(a):
        if a and len(a) > 1:
            try:
                a[0].fields["$color"] = int(a[1]) & 0xFFFFFFFF
            except Exception:
                pass
        return None
    bn("setColor", "(I)V", _set_color)
    bn("setStyle", "(Landroid/graphics/Paint$Style;)V", lambda a: None)
    bn("setStrokeWidth", "(F)V", lambda a: None)
    bn("setTextSize", "(F)V", lambda a: None)
    bn("setAntiAlias", "(Z)V", lambda a: None)

    # Canvas: Transform
    bn("save", "()I", lambda a: (rec.save() or 0))
    bn("restore", "()V", lambda a: rec.restore())
    bn("translate", "(FF)V", lambda a: rec.translate(a[1], a[2]) if len(a) > 2 else None)
    bn("clipRect", "(FFFF)Z", lambda a: True)

    # Canvas: Zeichnen
    def _draw_rect(a):
        if len(a) > 5:
            rec.rect(a[1], a[2], a[3], a[4], _paint_color(a[5]))
        return None
    bn("drawRect", "(FFFFLandroid/graphics/Paint;)V", _draw_rect)

    def _draw_circle(a):
        if len(a) > 4:
            rec.circle(a[1], a[2], a[3], _paint_color(a[4]))
        return None
    bn("drawCircle", "(FFFLandroid/graphics/Paint;)V", _draw_circle)

    def _draw_line(a):
        if len(a) > 5:
            rec.line(a[1], a[2], a[3], a[4], _paint_color(a[5]))
        return None
    bn("drawLine", "(FFFFLandroid/graphics/Paint;)V", _draw_line)

    def _draw_text(a):
        if len(a) > 4:
            rec.text(_s(a[1]), a[2], a[3], _paint_color(a[4]))
        return None
    bn("drawText", "(Ljava/lang/String;FFLandroid/graphics/Paint;)V", _draw_text)
    bn("drawColor", "(I)V", lambda a: rec.rect(0, 0, rec.width, rec.height, _argb_to_hex(a[1])) if len(a) > 1 else None)
    return rec


# --- Open/Closed: Produzent in der Registry (Compose / custom-draw) ---
def canvas_producer(ctx):
    """ctx: dict mit 'stubs' -> installiert Canvas-Stubs; Rueckgabe: CanvasRecorder."""
    stubs = ctx.get("stubs") if isinstance(ctx, dict) else None
    return install_canvas_stubs(stubs) if stubs is not None else CanvasRecorder()


# Registrierung zentral in core.java.producer_wiring.ensure_default_producers (kein Import-Seiteneffekt).


def demo():
    """Beispiel-Zeichnung (Familie B) fuer die Studio-Anzeige: Hintergrund, Karte, Kreis, Texte."""
    rec = CanvasRecorder()
    rec.rect(0, 0, 380, 760, "#101418")
    rec.save(); rec.translate(20, 40)
    rec.rect(0, 0, 340, 120, "#1e2630")
    rec.text("Compose-Canvas (Familie B)", 16, 44, "#e6edf3")
    rec.circle(300, 60, 24, "#2f6df0")
    rec.restore()
    rec.text("drawRect / drawText / drawCircle aufgezeichnet", 20, 200, "#9aa5b5")
    rec.text("-> DisplayListSurface (immediate-mode)", 20, 230, "#9aa5b5")
    return rec
