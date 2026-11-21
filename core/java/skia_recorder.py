# -*- coding: utf-8 -*-
"""core.java.skia_recorder — Render-Produzent Familie C (Flutter/GPU): Skia-SkCanvas-Zeichenbefehle
aufzeichnen. Flutter/Impeller zeichnen eigene Pixel ueber Skia (Dart ist AOT-nativ, libapp.so) am
android.*-System vorbei. Wir fangen die SkCanvas-Draw-Ops ab (wie Skias eigenes SkPicture/DisplayList)
und bauen eine Display-Liste (paint_commands) -> `DisplayListSurface` -> selbe Render-Pipeline wie S2,
nur eine Ebene tiefer. Voller Affin-Matrix-Stack (save/restore/saveLayer/translate/scale/rotate/concat).
IP-neutral; registriert sich fuer 'Flutter'. (S4b: echten Skia-Lauf abfangen.)"""
from __future__ import annotations

import math

from core.java.surface import DisplayListSurface, ProducerRegistry

_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)   # (a,b,c,d,e,f): x'=a*x+c*y+e, y'=b*x+d*y+f


def _mul(A, B):
    a, b, c, d, e, f = A
    a2, b2, c2, d2, e2, f2 = B
    return (a * a2 + c * b2, b * a2 + d * b2,
            a * c2 + c * d2, b * c2 + d * d2,
            a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def _apply(M, x, y):
    a, b, c, d, e, f = M
    return (a * x + c * y + e, b * x + d * y + f)


def _argb_to_hex(v):
    v = int(v) & 0xFFFFFFFF
    return "#%02x%02x%02x" % ((v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff)


def _flatten_gradient(colors):
    """Lineares/Radial-Gradient -> eine Mittelfarbe (Brief: Mittelwert der Stops; verlustarm fuer UI)."""
    vs = [int(c) & 0xFFFFFFFF for c in colors if c is not None]
    if not vs:
        return None
    n = len(vs)
    a = sum((v >> 24) & 0xff for v in vs) // n
    r = sum((v >> 16) & 0xff for v in vs) // n
    g = sum((v >> 8) & 0xff for v in vs) // n
    b = sum(v & 0xff for v in vs) // n
    return "#%02x%02x%02x" % (r, g, b)


def _paint_color(paint, default="#000000"):
    if isinstance(paint, dict):
        if paint.get("gradient"):
            fl = _flatten_gradient(paint["gradient"])
            if fl:
                return fl
        c = paint.get("color")
        return _argb_to_hex(c) if c is not None else (paint.get("hex") or default)
    return default


class SkiaRecorder:
    """Zeichnet SkCanvas-Befehle als paint_commands (immediate-mode) mit Matrix-Stack."""

    def __init__(self, width=380, height=800):
        self.width = width
        self.height = height
        self.cmds = []
        self.regions = []
        self._stack = [_IDENT]

    # --- Matrix-/Save-Stack ---
    def save(self):
        self._stack.append(self._stack[-1])

    def save_layer(self):
        self._stack.append(self._stack[-1])   # Offscreen ignoriert; CTM erhalten

    def restore(self):
        if len(self._stack) > 1:
            self._stack.pop()

    def restore_to_count(self, n):
        while len(self._stack) > max(1, int(n)):
            self._stack.pop()

    def translate(self, dx, dy):
        self._stack[-1] = _mul(self._stack[-1], (1, 0, 0, 1, float(dx), float(dy)))

    def scale(self, sx, sy):
        self._stack[-1] = _mul(self._stack[-1], (float(sx), 0, 0, float(sy), 0, 0))

    def rotate(self, deg):
        r = math.radians(float(deg)); cs, sn = math.cos(r), math.sin(r)
        self._stack[-1] = _mul(self._stack[-1], (cs, sn, -sn, cs, 0, 0))

    def concat(self, m):
        if isinstance(m, (tuple, list)) and len(m) == 6:
            self._stack[-1] = _mul(self._stack[-1], tuple(float(x) for x in m))

    @property
    def save_count(self):
        return len(self._stack)

    def _bbox(self, l, t, r, b):
        M = self._stack[-1]
        pts = [_apply(M, l, t), _apply(M, r, t), _apply(M, r, b), _apply(M, l, b)]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)

    # --- Zeichenbefehle (SkCanvas) ---
    def draw_rect(self, l, t, r, b, paint=None, rounded=0):
        x, y, w, h = self._bbox(float(l), float(t), float(r), float(b))
        cmd = {"op": "rect", "x": x, "y": y, "w": w, "h": h,
               "fill": _paint_color(paint), "outline": ""}
        if rounded:
            cmd["r"] = rounded
        self.cmds.append(cmd)
        self.regions.append({"x": x, "y": y, "w": w, "h": h})

    def draw_rrect(self, l, t, r, b, radius=8, paint=None):
        self.draw_rect(l, t, r, b, paint, rounded=radius)

    def draw_circle(self, cx, cy, rad, paint=None):
        self.draw_rect(cx - rad, cy - rad, cx + rad, cy + rad, paint, rounded=0)
        self.cmds[-1]["op"] = "oval"

    def draw_line(self, x1, y1, x2, y2, paint=None):
        a = _apply(self._stack[-1], float(x1), float(y1))
        b = _apply(self._stack[-1], float(x2), float(y2))
        self.cmds.append({"op": "line", "x": a[0], "y": a[1], "x2": b[0], "y2": b[1],
                          "color": _paint_color(paint)})

    def draw_path_bbox(self, l, t, r, b, paint=None):
        """Pfad vereinfacht als Bounding-Box (S4a). Echte Pfade: S4b."""
        self.draw_rect(l, t, r, b, paint)

    def draw_paragraph(self, text, x, y, paint=None):
        p = _apply(self._stack[-1], float(x), float(y))
        self.cmds.append({"op": "text", "x": p[0], "y": p[1], "text": str(text),
                          "color": _paint_color(paint), "anchor": "w"})

    draw_text = draw_paragraph

    def draw_color(self, color):
        self.cmds.append({"op": "rect", "x": 0, "y": 0, "w": self.width, "h": self.height,
                          "fill": _argb_to_hex(color), "outline": ""})

    # --- DlOpReceiver-Aliasnamen (Flutter-DisplayList-Seam) ---
    draw_round_rect = draw_rrect
    draw_paint = draw_color

    def draw_diff_round_rect(self, ol, ot, or_, ob, il, it, ir, ib, radius=8, paint=None):
        """Ring (aussen minus innen) -> Umriss-Rechteck (vereinfacht)."""
        x, y, w, h = self._bbox(float(ol), float(ot), float(or_), float(ob))
        self.cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h, "r": radius,
                          "fill": "", "outline": _paint_color(paint)})

    def draw_arc(self, l, t, r, b, start=0, sweep=360, paint=None):
        """Bogen/Spinner -> vereinfachtes Oval (Bounding-Box)."""
        x, y, w, h = self._bbox(float(l), float(t), float(r), float(b))
        self.cmds.append({"op": "oval", "x": x, "y": y, "w": w, "h": h,
                          "fill": "", "outline": _paint_color(paint)})

    def transform_2d_affine(self, a, b, c, d, e, f):
        self.concat((a, b, c, d, e, f))

    def transform_reset(self):
        self._stack[-1] = _IDENT

    def clip_rect(self, *a):
        return None

    clip_round_rect = clip_rect
    clip_path = clip_rect

    def surface(self):
        return DisplayListSurface(self.cmds, self.regions, width=self.width, height=self.height)


def skia_producer(ctx):
    return SkiaRecorder()


# Registrierung zentral in core.java.producer_wiring.ensure_default_producers (kein Import-Seiteneffekt).


def demo():
    """Flutter-aehnlicher Frame (Login) fuer die Studio-Anzeige -> DisplayListSurface."""
    r = SkiaRecorder()
    r.draw_color(0xFF0E141B)
    r.save(); r.translate(16, 40)
    r.draw_rrect(0, 0, 348, 160, 14, {"color": 0xFF1E2630})
    r.draw_paragraph("Anmelden (Flutter/Skia)", 16, 44, {"color": 0xFFE6EDF3})
    r.draw_rrect(16, 70, 332, 104, 8, {"color": 0xFF0E141B})
    r.draw_paragraph("Benutzername", 24, 92, {"color": 0xFF7D8694})
    r.draw_rrect(16, 112, 332, 146, 8, {"color": 0xFF0E141B})
    r.draw_paragraph("Passwort", 24, 134, {"color": 0xFF7D8694})
    r.restore()
    r.save(); r.translate(16, 220)
    r.draw_rrect(0, 0, 348, 46, 10, {"color": 0xFF2F6DF0})
    r.draw_paragraph("Login", 150, 28, {"color": 0xFFFFFFFF})
    r.restore()
    r.draw_circle(190, 320, 16, {"color": 0xFF2F6DF0})
    return r
