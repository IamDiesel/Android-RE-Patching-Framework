# -*- coding: utf-8 -*-
"""core.java.canvas_recorder — Render-Produzent Familie B: android.graphics.Canvas-Zeichenbefehle
aufzeichnen (Jetpack Compose, custom View.onDraw, 2D). Immediate-mode: die App zeichnet selbst
ueber Canvas; wir fangen die drawX-Aufrufe ab und bauen eine Display-Liste (paint_commands) +
Hit-Regionen -> `DisplayListSurface`. Transform-Stack (2D-Matrix: save/restore/translate/scale/rotate),
Paint-Farbe/Metriken aus dem Paint-Objekt. Zielagnostisch.

Ein Flush je Frame: `surface()` liefert die aktuelle Display-Liste (Compute-Budget)."""
from __future__ import annotations

import math
import struct as _struct

from core.java.objects import JArray, JObject, JString
from core.java.surface import DisplayListSurface


_IDENT_MAT = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _f(v, default=0.0):
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, float):
        return v if v == v else default
    if isinstance(v, int):
        if v == 0:
            return 0.0
        if abs(v) >= 0x01000000:
            try:
                f = _struct.unpack("<f", _struct.pack("<I", v & 0xFFFFFFFF))[0]
                if f == f:
                    return float(f)
            except Exception:
                pass
        return float(v)
    try:
        f = float(v)
        return f if f == f else default
    except Exception:
        return default


def _s(v):
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject):
        for k in ("$text", "text", "buf", "value"):
            fv = v.fields.get(k)
            if fv is not None:
                return fv.text if isinstance(fv, JString) else str(fv)
        return ""
    return "" if v is None else str(v)


def _argb_to_hex(v):
    v = int(v) & 0xFFFFFFFF
    return "#%02x%02x%02x" % ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _paint_color(paint, default="#000000"):
    try:
        if isinstance(paint, JObject):
            c = paint.fields.get("$color")
            return _argb_to_hex(c) if c is not None else default
    except Exception:
        pass
    return default


def _paint_text_size(paint, default=12.0):
    if isinstance(paint, JObject):
        sz = paint.fields.get("$textSize")
        if sz is not None:
            return max(1.0, _f(sz, default))
    return default


def _mat_mul(a, b):
    return [
        a[0] * b[0] + a[1] * b[3] + a[2] * b[6],
        a[0] * b[1] + a[1] * b[4] + a[2] * b[7],
        a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
        a[3] * b[0] + a[4] * b[3] + a[5] * b[6],
        a[3] * b[1] + a[4] * b[4] + a[5] * b[7],
        a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
        a[6] * b[0] + a[7] * b[3] + a[8] * b[6],
        a[6] * b[1] + a[7] * b[4] + a[8] * b[7],
        a[6] * b[2] + a[7] * b[5] + a[8] * b[8],
    ]


def _rect_coords(rect_obj):
    if not isinstance(rect_obj, JObject):
        return 0.0, 0.0, 0.0, 0.0
    f = rect_obj.fields
    return (
        _f(f.get("left", 0.0)),
        _f(f.get("top", 0.0)),
        _f(f.get("right", 0.0)),
        _f(f.get("bottom", 0.0)),
    )


def _char_slice(seq, start=0, end=None):
    if isinstance(seq, JArray):
        chars = [chr(int(c) & 0xFFFF) if isinstance(c, int) else str(c) for c in seq.data]
        txt = "".join(chars)
    else:
        txt = _s(seq)
    s0 = max(0, int(start or 0))
    if end is None:
        return txt[s0:]
    e0 = max(s0, min(len(txt), int(end)))
    return txt[s0:e0]


def _char_count_slice(chars_arr, index=0, count=0):
    if isinstance(chars_arr, JArray):
        i0 = max(0, int(index or 0))
        c0 = max(0, int(count or 0))
        seg = chars_arr.data[i0:i0 + c0]
        return "".join(chr(int(c) & 0xFFFF) if isinstance(c, int) else str(c) for c in seg)
    return ""


class CanvasRecorder:
    """Sammelt Canvas-Zeichenbefehle als paint_commands (immediate-mode)."""

    def __init__(self, width=380, height=800):
        self.width = width
        self.height = height
        self.cmds = []
        self.regions = []
        self._stack = [list(_IDENT_MAT)]  # 3x3 2D-Transform-Stack

    # --- Transform-Stack ---
    def save(self):
        cnt = len(self._stack)
        self._stack.append(list(self._stack[-1]))
        return cnt

    def saveLayer(self, *args):
        return self.save()

    def restore(self):
        if len(self._stack) > 1:
            self._stack.pop()

    def restoreToCount(self, save_count):
        target = max(1, int(save_count or 1))
        while len(self._stack) > target and len(self._stack) > 1:
            self._stack.pop()

    def getSaveCount(self):
        return len(self._stack)

    def translate(self, dx, dy):
        self._stack[-1] = _mat_mul(
            self._stack[-1],
            [1.0, 0.0, _f(dx), 0.0, 1.0, _f(dy), 0.0, 0.0, 1.0],
        )

    def scale(self, sx, sy, px=None, py=None):
        sx_f, sy_f = _f(sx, 1.0), _f(sy, 1.0)
        if px is not None and py is not None:
            px_f, py_f = _f(px), _f(py)
            self.translate(px_f, py_f)
            self.scale(sx_f, sy_f)
            self.translate(-px_f, -py_f)
            return
        self._stack[-1] = _mat_mul(
            self._stack[-1],
            [sx_f, 0.0, 0.0, 0.0, sy_f, 0.0, 0.0, 0.0, 1.0],
        )

    def rotate(self, degrees, px=None, py=None):
        if px is not None and py is not None:
            px_f, py_f = _f(px), _f(py)
            self.translate(px_f, py_f)
            self.rotate(degrees)
            self.translate(-px_f, -py_f)
            return
        rad = math.radians(_f(degrees))
        c, s = math.cos(rad), math.sin(rad)
        self._stack[-1] = _mat_mul(
            self._stack[-1],
            [c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0],
        )

    def skew(self, kx, ky):
        self._stack[-1] = _mat_mul(
            self._stack[-1],
            [1.0, _f(kx), 0.0, _f(ky), 1.0, 0.0, 0.0, 0.0, 1.0],
        )

    def concat(self, mat9):
        if mat9 and len(mat9) >= 9:
            self._stack[-1] = _mat_mul(self._stack[-1], [float(x) for x in mat9[:9]])

    def setMatrix(self, mat9=None):
        if mat9 and len(mat9) >= 9:
            self._stack[-1] = [float(x) for x in mat9[:9]]
        else:
            self._stack[-1] = list(_IDENT_MAT)

    def getMatrix(self):
        return list(self._stack[-1])

    @property
    def _tx(self):
        return self._stack[-1][2]

    @property
    def _ty(self):
        return self._stack[-1][5]

    def _xform_pt(self, x, y):
        m = self._stack[-1]
        return m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]

    def _xform_rect(self, l, t, r, b):
        p1 = self._xform_pt(l, t)
        p2 = self._xform_pt(r, t)
        p3 = self._xform_pt(r, b)
        p4 = self._xform_pt(l, b)
        xs = (p1[0], p2[0], p3[0], p4[0])
        ys = (p1[1], p2[1], p3[1], p4[1])
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return x0, y0, x1 - x0, y1 - y0

    # --- Zeichenbefehle ---
    def rect(self, l, t, r, b, color="#000000"):
        x, y, w, h = self._xform_rect(_f(l), _f(t), _f(r), _f(b))
        self.cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                          "fill": color, "outline": ""})
        self.regions.append({"x": x, "y": y, "w": w, "h": h})

    def drawRoundRect(self, l, t, r, b, rx=0.0, ry=0.0, color="#000000"):
        x, y, w, h = self._xform_rect(_f(l), _f(t), _f(r), _f(b))
        self.cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                          "rx": _f(rx), "ry": _f(ry), "fill": color, "outline": ""})
        self.regions.append({"x": x, "y": y, "w": w, "h": h})

    def circle(self, cx, cy, rad, color="#000000"):
        cx_f, cy_f, r_f = _f(cx), _f(cy), _f(rad)
        x, y, w, h = self._xform_rect(cx_f - r_f, cy_f - r_f, cx_f + r_f, cy_f + r_f)
        self.cmds.append({"op": "oval", "x": x, "y": y, "w": w, "h": h,
                          "fill": color, "outline": ""})

    def drawOval(self, l, t, r, b, color="#000000"):
        x, y, w, h = self._xform_rect(_f(l), _f(t), _f(r), _f(b))
        self.cmds.append({"op": "oval", "x": x, "y": y, "w": w, "h": h,
                          "fill": color, "outline": ""})

    def drawArc(self, l, t, r, b, start_angle=0.0, sweep_angle=360.0, use_center=False, color="#000000"):
        x, y, w, h = self._xform_rect(_f(l), _f(t), _f(r), _f(b))
        self.cmds.append({
            "op": "arc", "x": x, "y": y, "w": w, "h": h,
            "start": _f(start_angle), "sweep": _f(sweep_angle),
            "use_center": bool(use_center), "fill": color, "outline": color,
        })

    def line(self, x1, y1, x2, y2, color="#000000", width=1.0):
        px1, py1 = self._xform_pt(_f(x1), _f(y1))
        px2, py2 = self._xform_pt(_f(x2), _f(y2))
        cmd = {"op": "line", "x": px1, "y": py1,
               "x2": px2, "y2": py2, "color": color}
        w_f = _f(width, 1.0)
        if w_f > 0.0:
            cmd["width"] = w_f
        self.cmds.append(cmd)

    def text(self, s, x, y, color="#000000", anchor="w", font_size=None):
        px, py = self._xform_pt(_f(x), _f(y))
        cmd = {"op": "text", "x": px, "y": py,
               "text": s, "color": color, "anchor": anchor or "w"}
        if font_size is not None and _f(font_size, 0.0) > 0.0:
            cmd["size"] = _f(font_size)
        self.cmds.append(cmd)

    def paint(self):
        return list(self.cmds)

    def drawPath(self, path_obj=None, color="#000000"):
        pts = path_obj.fields.get("_pts", ()) if isinstance(path_obj, JObject) else ()
        if pts:
            xformed = [self._xform_pt(_f(px), _f(py)) for px, py in pts]
            xs = [p[0] for p in xformed]
            ys = [p[1] for p in xformed]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            w, h = x1 - x0, y1 - y0
        else:
            x0, y0 = self._xform_pt(0.0, 0.0)
            w, h = 0.0, 0.0
        self.cmds.append({"op": "path", "x": x0, "y": y0, "w": w, "h": h,
                          "fill": color, "outline": color, "points": len(pts)})

    def drawBitmap(self, bmp=None, x=0.0, y=0.0, w=None, h=None, color="#000000"):
        bw = _f(w) if w is not None else float(bmp.fields.get("_w", 64) if isinstance(bmp, JObject) else 64)
        bh = _f(h) if h is not None else float(bmp.fields.get("_h", 64) if isinstance(bmp, JObject) else 64)
        x0, y0 = _f(x), _f(y)
        rx, ry, rw, rh = self._xform_rect(x0, y0, x0 + bw, y0 + bh)
        self.cmds.append({"op": "bitmap", "x": rx, "y": ry, "w": rw, "h": rh})

    def drawPoint(self, x, y, color="#000000"):
        px, py = self._xform_pt(_f(x), _f(y))
        self.cmds.append({"op": "point", "x": px, "y": py, "color": color})

    def drawPoints(self, pts, offset=0, count=None, color="#000000"):
        seq = list(pts.data) if isinstance(pts, JArray) else list(pts or ())
        off = max(0, int(offset or 0))
        lim = len(seq) if count is None else min(len(seq), off + max(0, int(count)))
        drawn = 0
        for i in range(off, lim - 1, 2):
            if drawn >= 256:
                break
            self.drawPoint(seq[i], seq[i + 1], color=color)
            drawn += 1

    def drawARGB(self, a, r, g, b):
        argb = ((int(a) & 0xFF) << 24) | ((int(r) & 0xFF) << 16) | ((int(g) & 0xFF) << 8) | (int(b) & 0xFF)
        self.rect(0, 0, self.width, self.height, _argb_to_hex(argb))

    def drawPaint(self, color="#000000"):
        self.rect(0, 0, self.width, self.height, color)

    # --- Ergebnis ---
    def surface(self):
        return DisplayListSurface(self.cmds, self.regions, width=self.width, height=self.height)


def install_canvas_stubs(stubs, rec=None):
    """Haengt android.graphics.Canvas/Paint/TextPaint-Stubs in `AndroidStubs` ein.
    Rueckgabe: der `CanvasRecorder` (auch `stubs.canvas_recorder`)."""
    rec = rec if rec is not None else CanvasRecorder()
    stubs.canvas_recorder = rec
    bn = stubs.register_by_name

    def _rec(cobj=None):
        if isinstance(cobj, JObject):
            local_rec = cobj.fields.get("$rec")
            if isinstance(local_rec, CanvasRecorder):
                return local_rec
        return getattr(stubs, "canvas_recorder", rec) or rec

    def _r(a):
        return _rec(a[0] if a else None)

    def _paint_anchor(pobj):
        if not isinstance(pobj, JObject):
            return "w"
        al = pobj.fields.get("$textAlign")
        nm = ""
        if isinstance(al, JObject):
            nm = str(al.fields.get("name") or al.fields.get("$name") or "")
        elif al is not None:
            nm = str(al)
        nm_u = nm.upper()
        if "CENTER" in nm_u:
            return "center"
        if "RIGHT" in nm_u or "END" in nm_u:
            return "e"
        return "w"

    def _paint_stroke_w(pobj, default=1.0):
        if isinstance(pobj, JObject):
            w = _f(pobj.fields.get("$strokeWidth", 0.0), 0.0)
            if w > 0.0:
                return w
        return default

    def _emit_text(a, txt, x, y, pobj):
        _r(a).text(txt, x, y, _paint_color(pobj),
                   anchor=_paint_anchor(pobj), font_size=_paint_text_size(pobj))
        return None

    def _add(cls, name, sig, fn):
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    CV = "Landroid/graphics/Canvas;"
    PT = "Landroid/graphics/Paint;"
    TP = "Landroid/text/TextPaint;"

    def _canvas_dims(cobj):
        r = _rec(cobj)
        if isinstance(cobj, JObject):
            w = cobj.fields.get("_w")
            h = cobj.fields.get("_h")
            if w and h:
                return int(w), int(h)
            bmp = cobj.fields.get("_bitmap")
            if isinstance(bmp, JObject):
                return int(bmp.fields.get("_w", r.width) or r.width), int(bmp.fields.get("_h", r.height) or r.height)
        return int(r.width), int(r.height)

    def _canvas_set_bitmap(a):
        cobj = a[0] if a and isinstance(a[0], JObject) else None
        bmp = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        if cobj is not None:
            r = _rec(cobj)
            cobj.fields["_bitmap"] = bmp
            if bmp is not None:
                cobj.fields["_w"] = int(bmp.fields.get("_w", r.width) or r.width)
                cobj.fields["_h"] = int(bmp.fields.get("_h", r.height) or r.height)
        return cobj

    # --- Paint & TextPaint Helpers ---
    def _paint_init(a):
        if a and isinstance(a[0], JObject):
            f = a[0].fields
            f.setdefault("$color", 0xFF000000)
            f.setdefault("$alpha", 255)
            f.setdefault("$textSize", 12.0)
            f.setdefault("$strokeWidth", 0.0)
            f.setdefault("$strokeMiter", 4.0)
            f.setdefault("$textScaleX", 1.0)
            f.setdefault("$textSkewX", 0.0)
            f.setdefault("$letterSpacing", 0.0)
            f.setdefault("$flags", 0)
            if len(a) > 1:
                if isinstance(a[1], int):
                    f["$flags"] = int(a[1])
                    f["$antiAlias"] = 1 if (int(a[1]) & 1) else 0
                elif isinstance(a[1], JObject):
                    for k, v in a[1].fields.items():
                        f[k] = v
        return a[0] if a else None

    def _paint_set(a):
        if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject):
            for k, v in a[1].fields.items():
                a[0].fields[k] = v
        return None

    def _paint_reset(a):
        if a and isinstance(a[0], JObject):
            a[0].fields.clear()
            _paint_init([a[0]])
        return None

    def _paint_set_color(a):
        if a and len(a) > 1 and isinstance(a[0], JObject):
            try:
                ci = int(a[1]) & 0xFFFFFFFF
                a[0].fields["$color"] = ci
                a[0].fields["$alpha"] = (ci >> 24) & 0xFF
            except Exception:
                pass
        return None

    def _paint_set_argb(a):
        if len(a) >= 5 and isinstance(a[0], JObject):
            ci = ((int(a[1]) & 0xFF) << 24) | ((int(a[2]) & 0xFF) << 16) | ((int(a[3]) & 0xFF) << 8) | (int(a[4]) & 0xFF)
            a[0].fields["$color"] = ci
            a[0].fields["$alpha"] = int(a[1]) & 0xFF
        return None

    def _paint_set_alpha(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            al = max(0, min(255, int(a[1] or 0)))
            a[0].fields["$alpha"] = al
            col = int(a[0].fields.get("$color", 0xFF000000)) & 0x00FFFFFF
            a[0].fields["$color"] = (al << 24) | col
        return None

    def _paint_set_field_ret(key, conv=None, return_arg=False):
        def _fn(a):
            if len(a) > 1 and isinstance(a[0], JObject):
                val = conv(a[1]) if conv is not None else a[1]
                a[0].fields[key] = val
                return val if return_arg else None
            return a[1] if (return_arg and len(a) > 1) else None
        return _fn

    def _paint_set_text_size(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["$textSize"] = max(1.0, _f(a[1], 12.0))
        return None

    def _paint_set_stroke_w(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["$strokeWidth"] = _f(a[1], 0.0)
        return None

    def _paint_metrics(pobj, int_mode=False):
        sz = _paint_text_size(pobj)
        top = -1.05 * sz
        ascent = -0.9 * sz
        descent = 0.25 * sz
        bottom = 0.3 * sz
        if int_mode:
            return {
                "top": int(math.floor(top)),
                "ascent": int(math.floor(ascent)),
                "descent": int(math.ceil(descent)),
                "bottom": int(math.ceil(bottom)),
                "leading": 0,
            }
        return {"top": top, "ascent": ascent, "descent": descent, "bottom": bottom, "leading": 0.0}

    def _paint_get_fm_obj(a, int_mode=False):
        m = _paint_metrics(a[0] if a else None, int_mode=int_mode)
        if len(a) > 1 and isinstance(a[1], JObject):
            a[1].fields.update(m)
            sz = _paint_text_size(a[0] if a else None)
            return int(round(1.2 * sz)) if int_mode else float(1.2 * sz)
        cls_nm = "Landroid/graphics/Paint$FontMetricsInt;" if int_mode else "Landroid/graphics/Paint$FontMetrics;"
        return JObject(cls_nm, m)

    def _paint_measure_text(a, sig=""):
        sz = _paint_text_size(a[0] if a else None)
        if "(Ljava/lang/String;)" in sig or "(Ljava/lang/CharSequence;)" in sig:
            txt = _s(a[1]) if len(a) > 1 else ""
        elif "([CII)" in sig:
            txt = _char_count_slice(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else 0)
        else:
            txt = _char_slice(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else None)
        return float(len(txt) * sz * 0.55)

    def _paint_text_widths(a, sig=""):
        sz = _paint_text_size(a[0] if a else None)
        cw = float(sz * 0.55)
        if sig.startswith("(Ljava/lang/String;[F)"):
            txt = _s(a[1]) if len(a) > 1 else ""
            arr = a[2] if len(a) > 2 else None
        elif sig.startswith("([CII[F)"):
            txt = _char_count_slice(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else 0)
            arr = a[4] if len(a) > 4 else None
        else:
            txt = _char_slice(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else None)
            arr = a[4] if len(a) > 4 else None
        n = len(txt)
        if isinstance(arr, JArray):
            n = min(n, len(arr.data))
            for i in range(n):
                arr.data[i] = cw
        return n

    def _paint_text_bounds(a, sig=""):
        sz = _paint_text_size(a[0] if a else None)
        if sig.startswith("([C"):
            txt = _char_count_slice(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else 0)
            rect = a[4] if len(a) > 4 else None
        else:
            txt = _char_slice(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else None)
            rect = a[4] if len(a) > 4 else None
        if isinstance(rect, JObject):
            rect.fields.update({
                "left": 0,
                "top": int(math.floor(-0.9 * sz)),
                "right": max(0, int(round(len(txt) * sz * 0.55))),
                "bottom": max(1, int(math.ceil(0.25 * sz))),
            })
        return None

    def _paint_break_text(a, sig=""):
        sz = _paint_text_size(a[0] if a else None)
        cw = max(1.0, sz * 0.55)
        if sig.startswith("(Ljava/lang/String;ZF[F)"):
            txt = _s(a[1]) if len(a) > 1 else ""
            max_w = _f(a[3] if len(a) > 3 else 0.0)
            measured = a[4] if len(a) > 4 else None
        elif sig.startswith("([CIIF[F)"):
            txt = _char_count_slice(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else 0)
            max_w = _f(a[4] if len(a) > 4 else 0.0)
            measured = a[5] if len(a) > 5 else None
        else:
            txt = _char_slice(a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else 0, a[3] if len(a) > 3 else None)
            max_w = _f(a[5] if len(a) > 5 else 0.0)
            measured = a[6] if len(a) > 6 else None
        fit = min(len(txt), max(0, int(max_w // cw)))
        if isinstance(measured, JArray) and len(measured.data) > 0:
            measured.data[0] = float(fit * cw)
        return fit

    # Bestehende _by_name-Helfer bewahren (Kompatibilitaet fuer Subklassen/direkte Calls)
    bn("setColor", "(I)V", _paint_set_color)
    bn("setStyle", "(Landroid/graphics/Paint$Style;)V", _paint_set_field_ret("$style"))
    bn("setStrokeWidth", "(F)V", _paint_set_stroke_w)
    bn("setTextSize", "(F)V", _paint_set_text_size)
    bn("setAntiAlias", "(Z)V", _paint_set_field_ret("$antiAlias", lambda v: 1 if v else 0))

    # Explizite Paint + TextPaint Stubs
    for _pcls in (PT, TP):
        for _psig in ("()V", "(I)V", "(Landroid/graphics/Paint;)V"):
            _add(_pcls, "<init>", _psig, _paint_init)
        _add(_pcls, "reset", "()V", _paint_reset)
        _add(_pcls, "set", "(Landroid/graphics/Paint;)V", _paint_set)
        _add(_pcls, "setColor", "(I)V", _paint_set_color)
        _add(_pcls, "getColor", "()I", lambda a: int(a[0].fields.get("$color", 0xFF000000)) if a and isinstance(a[0], JObject) else 0xFF000000)
        _add(_pcls, "setARGB", "(IIII)V", _paint_set_argb)
        _add(_pcls, "setAlpha", "(I)V", _paint_set_alpha)
        _add(_pcls, "getAlpha", "()I", lambda a: int(a[0].fields.get("$alpha", 255)) if a and isinstance(a[0], JObject) else 255)
        _add(_pcls, "setFlags", "(I)V", _paint_set_field_ret("$flags", lambda v: int(v or 0)))
        _add(_pcls, "getFlags", "()I", lambda a: int(a[0].fields.get("$flags", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setAntiAlias", "(Z)V", _paint_set_field_ret("$antiAlias", lambda v: 1 if v else 0))
        _add(_pcls, "isAntiAlias", "()Z", lambda a: int(a[0].fields.get("$antiAlias", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setDither", "(Z)V", _paint_set_field_ret("$dither", lambda v: 1 if v else 0))
        _add(_pcls, "isDither", "()Z", lambda a: int(a[0].fields.get("$dither", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setFilterBitmap", "(Z)V", _paint_set_field_ret("$filterBitmap", lambda v: 1 if v else 0))
        _add(_pcls, "isFilterBitmap", "()Z", lambda a: int(a[0].fields.get("$filterBitmap", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setStyle", "(Landroid/graphics/Paint$Style;)V", _paint_set_field_ret("$style"))
        _add(_pcls, "getStyle", "()Landroid/graphics/Paint$Style;", lambda a: a[0].fields.get("$style") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setStrokeWidth", "(F)V", _paint_set_stroke_w)
        _add(_pcls, "getStrokeWidth", "()F", lambda a: _f(a[0].fields.get("$strokeWidth", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
        _add(_pcls, "setStrokeCap", "(Landroid/graphics/Paint$Cap;)V", _paint_set_field_ret("$strokeCap"))
        _add(_pcls, "getStrokeCap", "()Landroid/graphics/Paint$Cap;", lambda a: a[0].fields.get("$strokeCap") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setStrokeJoin", "(Landroid/graphics/Paint$Join;)V", _paint_set_field_ret("$strokeJoin"))
        _add(_pcls, "getStrokeJoin", "()Landroid/graphics/Paint$Join;", lambda a: a[0].fields.get("$strokeJoin") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setStrokeMiter", "(F)V", _paint_set_field_ret("$strokeMiter", lambda v: _f(v, 4.0)))
        _add(_pcls, "getStrokeMiter", "()F", lambda a: _f(a[0].fields.get("$strokeMiter", 4.0)) if a and isinstance(a[0], JObject) else 4.0)
        _add(_pcls, "setShader", "(Landroid/graphics/Shader;)Landroid/graphics/Shader;", _paint_set_field_ret("$shader", return_arg=True))
        _add(_pcls, "getShader", "()Landroid/graphics/Shader;", lambda a: a[0].fields.get("$shader") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setColorFilter", "(Landroid/graphics/ColorFilter;)Landroid/graphics/ColorFilter;", _paint_set_field_ret("$colorFilter", return_arg=True))
        _add(_pcls, "getColorFilter", "()Landroid/graphics/ColorFilter;", lambda a: a[0].fields.get("$colorFilter") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setXfermode", "(Landroid/graphics/Xfermode;)Landroid/graphics/Xfermode;", _paint_set_field_ret("$xfermode", return_arg=True))
        _add(_pcls, "getXfermode", "()Landroid/graphics/Xfermode;", lambda a: a[0].fields.get("$xfermode") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setPathEffect", "(Landroid/graphics/PathEffect;)Landroid/graphics/PathEffect;", _paint_set_field_ret("$pathEffect", return_arg=True))
        _add(_pcls, "getPathEffect", "()Landroid/graphics/PathEffect;", lambda a: a[0].fields.get("$pathEffect") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setMaskFilter", "(Landroid/graphics/MaskFilter;)Landroid/graphics/MaskFilter;", _paint_set_field_ret("$maskFilter", return_arg=True))
        _add(_pcls, "getMaskFilter", "()Landroid/graphics/MaskFilter;", lambda a: a[0].fields.get("$maskFilter") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setBlendMode", "(Landroid/graphics/BlendMode;)V", _paint_set_field_ret("$blendMode"))
        _add(_pcls, "getBlendMode", "()Landroid/graphics/BlendMode;", lambda a: a[0].fields.get("$blendMode") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setTypeface", "(Landroid/graphics/Typeface;)Landroid/graphics/Typeface;", _paint_set_field_ret("$typeface", return_arg=True))
        _add(_pcls, "getTypeface", "()Landroid/graphics/Typeface;", lambda a: a[0].fields.get("$typeface") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setTextSize", "(F)V", _paint_set_text_size)
        _add(_pcls, "getTextSize", "()F", lambda a: _paint_text_size(a[0] if a else None))
        _add(_pcls, "setTextAlign", "(Landroid/graphics/Paint$Align;)V", _paint_set_field_ret("$textAlign"))
        _add(_pcls, "getTextAlign", "()Landroid/graphics/Paint$Align;", lambda a: a[0].fields.get("$textAlign") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setTextScaleX", "(F)V", _paint_set_field_ret("$textScaleX", lambda v: _f(v, 1.0)))
        _add(_pcls, "getTextScaleX", "()F", lambda a: _f(a[0].fields.get("$textScaleX", 1.0)) if a and isinstance(a[0], JObject) else 1.0)
        _add(_pcls, "setTextSkewX", "(F)V", _paint_set_field_ret("$textSkewX", lambda v: _f(v, 0.0)))
        _add(_pcls, "getTextSkewX", "()F", lambda a: _f(a[0].fields.get("$textSkewX", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
        _add(_pcls, "setLetterSpacing", "(F)V", _paint_set_field_ret("$letterSpacing", lambda v: _f(v, 0.0)))
        _add(_pcls, "getLetterSpacing", "()F", lambda a: _f(a[0].fields.get("$letterSpacing", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
        _add(_pcls, "setUnderlineText", "(Z)V", _paint_set_field_ret("$underline", lambda v: 1 if v else 0))
        _add(_pcls, "isUnderlineText", "()Z", lambda a: int(a[0].fields.get("$underline", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setStrikeThruText", "(Z)V", _paint_set_field_ret("$strikeThru", lambda v: 1 if v else 0))
        _add(_pcls, "isStrikeThruText", "()Z", lambda a: int(a[0].fields.get("$strikeThru", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setFakeBoldText", "(Z)V", _paint_set_field_ret("$fakeBold", lambda v: 1 if v else 0))
        _add(_pcls, "isFakeBoldText", "()Z", lambda a: int(a[0].fields.get("$fakeBold", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setSubpixelText", "(Z)V", _paint_set_field_ret("$subpixel", lambda v: 1 if v else 0))
        _add(_pcls, "isSubpixelText", "()Z", lambda a: int(a[0].fields.get("$subpixel", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setLinearText", "(Z)V", _paint_set_field_ret("$linearText", lambda v: 1 if v else 0))
        _add(_pcls, "isLinearText", "()Z", lambda a: int(a[0].fields.get("$linearText", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_pcls, "setShadowLayer", "(FFFI)V",
             lambda a: a[0].fields.__setitem__("$shadow", (_f(a[1]), _f(a[2]), _f(a[3]), int(a[4]))) if len(a) > 4 and isinstance(a[0], JObject) else None)
        _add(_pcls, "clearShadowLayer", "()V",
             lambda a: a[0].fields.pop("$shadow", None) if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "setFontFeatureSettings", "(Ljava/lang/String;)V", _paint_set_field_ret("$fontFeat", _s))
        _add(_pcls, "getFontFeatureSettings", "()Ljava/lang/String;",
             lambda a: JString(a[0].fields["$fontFeat"]) if a and isinstance(a[0], JObject) and "$fontFeat" in a[0].fields else None)
        _add(_pcls, "setFontVariationSettings", "(Ljava/lang/String;)Z",
             lambda a: (a[0].fields.__setitem__("$fontVar", _s(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, 1)[1])
        _add(_pcls, "getFontVariationSettings", "()Ljava/lang/String;",
             lambda a: JString(a[0].fields["$fontVar"]) if a and isinstance(a[0], JObject) and "$fontVar" in a[0].fields else None)
        _add(_pcls, "setTextLocale", "(Ljava/util/Locale;)V", _paint_set_field_ret("$locale"))
        _add(_pcls, "getTextLocale", "()Ljava/util/Locale;",
             lambda a: a[0].fields.get("$locale") if a and isinstance(a[0], JObject) else None)
        _add(_pcls, "hasGlyph", "(Ljava/lang/String;)Z", lambda a: 1 if (len(a) > 1 and _s(a[1])) else 0)
        _add(_pcls, "ascent", "()F", lambda a: float(-0.9 * _paint_text_size(a[0] if a else None)))
        _add(_pcls, "descent", "()F", lambda a: float(0.25 * _paint_text_size(a[0] if a else None)))
        _add(_pcls, "getFontSpacing", "()F", lambda a: float(1.2 * _paint_text_size(a[0] if a else None)))
        _add(_pcls, "getFontMetrics", "()Landroid/graphics/Paint$FontMetrics;", lambda a: _paint_get_fm_obj(a, False))
        _add(_pcls, "getFontMetrics", "(Landroid/graphics/Paint$FontMetrics;)F", lambda a: _paint_get_fm_obj(a, False))
        _add(_pcls, "getFontMetricsInt", "()Landroid/graphics/Paint$FontMetricsInt;", lambda a: _paint_get_fm_obj(a, True))
        _add(_pcls, "getFontMetricsInt", "(Landroid/graphics/Paint$FontMetricsInt;)I", lambda a: _paint_get_fm_obj(a, True))
        for _msig in ("(Ljava/lang/String;)F", "(Ljava/lang/String;II)F", "(Ljava/lang/CharSequence;II)F", "([CII)F"):
            _add(_pcls, "measureText", _msig, lambda a, _s0=_msig: _paint_measure_text(a, _s0))
        for _wsig in ("(Ljava/lang/String;[F)I", "(Ljava/lang/String;II[F)I", "(Ljava/lang/CharSequence;II[F)I", "([CII[F)I"):
            _add(_pcls, "getTextWidths", _wsig, lambda a, _s0=_wsig: _paint_text_widths(a, _s0))
        for _bsig in ("(Ljava/lang/String;IILandroid/graphics/Rect;)V", "([CIILandroid/graphics/Rect;)V"):
            _add(_pcls, "getTextBounds", _bsig, lambda a, _s0=_bsig: _paint_text_bounds(a, _s0))
        for _brsig in ("(Ljava/lang/String;ZF[F)I", "(Ljava/lang/CharSequence;IIZF[F)I", "([CIIF[F)I"):
            _add(_pcls, "breakText", _brsig, lambda a, _s0=_brsig: _paint_break_text(a, _s0))
    _add(TP, "set", "(Landroid/text/TextPaint;)V", _paint_set)

    # --- Canvas: bestehende _by_name-Helfer bewahren ---
    bn("save", "()I", lambda a: _r(a).save())
    bn("restore", "()V", lambda a: _r(a).restore())
    bn("translate", "(FF)V", lambda a: _r(a).translate(a[1], a[2]) if len(a) > 2 else None)
    bn("clipRect", "(FFFF)Z", lambda a: 1)

    def _draw_rect(a):
        if len(a) > 5:
            _r(a).rect(a[1], a[2], a[3], a[4], _paint_color(a[5]))
        return None
    bn("drawRect", "(FFFFLandroid/graphics/Paint;)V", _draw_rect)

    def _draw_circle(a):
        if len(a) > 4:
            _r(a).circle(a[1], a[2], a[3], _paint_color(a[4]))
        return None
    bn("drawCircle", "(FFFLandroid/graphics/Paint;)V", _draw_circle)

    def _draw_line(a):
        if len(a) > 5:
            _r(a).line(a[1], a[2], a[3], a[4], _paint_color(a[5]), width=_paint_stroke_w(a[5]))
        return None
    bn("drawLine", "(FFFFLandroid/graphics/Paint;)V", _draw_line)

    def _draw_text(a):
        if len(a) > 4:
            _emit_text(a, _s(a[1]), a[2], a[3], a[4])
        return None
    bn("drawText", "(Ljava/lang/String;FFLandroid/graphics/Paint;)V", _draw_text)
    bn("drawColor", "(I)V", lambda a: _r(a).rect(0, 0, *_canvas_dims(a[0] if a else None), _argb_to_hex(a[1])) if len(a) > 1 else None)

    # --- Explizite Canvas Stubs ---
    _add(CV, "<init>", "()V", lambda a: a[0] if a else None)
    _add(CV, "<init>", "(Landroid/graphics/Bitmap;)V", _canvas_set_bitmap)
    _add(CV, "setBitmap", "(Landroid/graphics/Bitmap;)V", _canvas_set_bitmap)
    _add(CV, "getWidth", "()I", lambda a: _canvas_dims(a[0] if a else None)[0])
    _add(CV, "getHeight", "()I", lambda a: _canvas_dims(a[0] if a else None)[1])
    _add(CV, "isHardwareAccelerated", "()Z", lambda a: 1)
    _add(CV, "getDensity", "()I", lambda a: int(a[0].fields.get("_density", 440)) if a and isinstance(a[0], JObject) else 440)
    _add(CV, "setDensity", "(I)V", lambda a: a[0].fields.__setitem__("_density", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    _add(CV, "save", "()I", lambda a: _r(a).save())
    _add(CV, "save", "(I)I", lambda a: _r(a).save())
    for _slsig in (
        "(FFFFLandroid/graphics/Paint;)I",
        "(FFFFLandroid/graphics/Paint;I)I",
        "(Landroid/graphics/RectF;Landroid/graphics/Paint;)I",
        "(Landroid/graphics/RectF;Landroid/graphics/Paint;I)I",
        "(FFFFI)I",
        "(Landroid/graphics/RectF;I)I",
    ):
        _add(CV, "saveLayer" if "Paint" in _slsig else "saveLayerAlpha", _slsig, lambda a: _r(a).saveLayer())
    _add(CV, "restore", "()V", lambda a: _r(a).restore())
    _add(CV, "restoreToCount", "(I)V", lambda a: _r(a).restoreToCount(a[1]) if len(a) > 1 else None)
    _add(CV, "getSaveCount", "()I", lambda a: _r(a).getSaveCount())

    _add(CV, "translate", "(FF)V", lambda a: _r(a).translate(a[1], a[2]) if len(a) > 2 else None)
    _add(CV, "scale", "(FF)V", lambda a: _r(a).scale(a[1], a[2]) if len(a) > 2 else None)
    _add(CV, "scale", "(FFFF)V", lambda a: _r(a).scale(a[1], a[2], a[3], a[4]) if len(a) > 4 else None)
    _add(CV, "rotate", "(F)V", lambda a: _r(a).rotate(a[1]) if len(a) > 1 else None)
    _add(CV, "rotate", "(FFF)V", lambda a: _r(a).rotate(a[1], a[2], a[3]) if len(a) > 3 else None)
    _add(CV, "skew", "(FF)V", lambda a: _r(a).skew(a[1], a[2]) if len(a) > 2 else None)

    def _canvas_concat(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            m = a[1].fields.get("_m")
            if m:
                _r(a).concat(m)
        return None
    _add(CV, "concat", "(Landroid/graphics/Matrix;)V", _canvas_concat)

    def _canvas_set_matrix(a):
        m = a[1].fields.get("_m") if (len(a) > 1 and isinstance(a[1], JObject)) else None
        _r(a).setMatrix(m)
        return None
    _add(CV, "setMatrix", "(Landroid/graphics/Matrix;)V", _canvas_set_matrix)

    def _canvas_get_matrix_void(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            a[1].fields["_m"] = _r(a).getMatrix()
        return None
    _add(CV, "getMatrix", "(Landroid/graphics/Matrix;)V", _canvas_get_matrix_void)
    _add(CV, "getMatrix", "()Landroid/graphics/Matrix;",
         lambda a: JObject("Landroid/graphics/Matrix;", {"_m": _r(a).getMatrix()}))

    # Clipping
    for _csig in (
        "(IIII)Z", "(FFFF)Z", "(FFFFLandroid/graphics/Region$Op;)Z",
        "(Landroid/graphics/Rect;)Z", "(Landroid/graphics/Rect;Landroid/graphics/Region$Op;)Z",
        "(Landroid/graphics/RectF;)Z", "(Landroid/graphics/RectF;Landroid/graphics/Region$Op;)Z",
    ):
        _add(CV, "clipRect", _csig, lambda a: 1)
    for _csig in ("(IIII)Z", "(FFFF)Z", "(Landroid/graphics/Rect;)Z", "(Landroid/graphics/RectF;)Z"):
        _add(CV, "clipOutRect", _csig, lambda a: 1)
    for _psig in ("(Landroid/graphics/Path;)Z", "(Landroid/graphics/Path;Landroid/graphics/Region$Op;)Z"):
        _add(CV, "clipPath", _psig, lambda a: 1)
    _add(CV, "clipOutPath", "(Landroid/graphics/Path;)Z", lambda a: 1)

    def _canvas_get_clip(a):
        w, h = _canvas_dims(a[0] if a else None)
        if len(a) > 1 and isinstance(a[1], JObject):
            a[1].fields.update({"left": 0, "top": 0, "right": w, "bottom": h})
            return 1
        return JObject("Landroid/graphics/Rect;", {"left": 0, "top": 0, "right": w, "bottom": h})
    _add(CV, "getClipBounds", "(Landroid/graphics/Rect;)Z", _canvas_get_clip)
    _add(CV, "getClipBounds", "()Landroid/graphics/Rect;", _canvas_get_clip)

    for _qsig in (
        "(FFFF)Z", "(FFFFLandroid/graphics/Canvas$EdgeType;)Z",
        "(Landroid/graphics/RectF;)Z", "(Landroid/graphics/RectF;Landroid/graphics/Canvas$EdgeType;)Z",
        "(Landroid/graphics/Path;)Z", "(Landroid/graphics/Path;Landroid/graphics/Canvas$EdgeType;)Z",
    ):
        _add(CV, "quickReject", _qsig, lambda a: 0)

    # Drawing
    _add(CV, "drawColor", "(I)V",
         lambda a: _r(a).rect(0, 0, *_canvas_dims(a[0] if a else None), _argb_to_hex(a[1])) if len(a) > 1 else None)
    _add(CV, "drawColor", "(ILandroid/graphics/PorterDuff$Mode;)V",
         lambda a: _r(a).rect(0, 0, *_canvas_dims(a[0] if a else None), _argb_to_hex(a[1])) if len(a) > 1 else None)
    _add(CV, "drawColor", "(ILandroid/graphics/BlendMode;)V",
         lambda a: _r(a).rect(0, 0, *_canvas_dims(a[0] if a else None), _argb_to_hex(a[1])) if len(a) > 1 else None)
    _add(CV, "drawRGB", "(III)V",
         lambda a: _r(a).drawARGB(255, a[1], a[2], a[3]) if len(a) > 3 else None)
    _add(CV, "drawARGB", "(IIII)V",
         lambda a: _r(a).drawARGB(a[1], a[2], a[3], a[4]) if len(a) > 4 else None)
    _add(CV, "drawPaint", "(Landroid/graphics/Paint;)V",
         lambda a: _r(a).drawPaint(_paint_color(a[1])) if len(a) > 1 else None)

    _add(CV, "drawPoint", "(FFLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawPoint(a[1], a[2], _paint_color(a[3])) if len(a) > 3 else None)
    _add(CV, "drawPoints", "([FLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawPoints(a[1], 0, None, _paint_color(a[2])) if len(a) > 2 else None)
    _add(CV, "drawPoints", "([FIILandroid/graphics/Paint;)V",
         lambda a: _r(a).drawPoints(a[1], a[2], a[3], _paint_color(a[4])) if len(a) > 4 else None)

    _add(CV, "drawLine", "(FFFFLandroid/graphics/Paint;)V", _draw_line)
    def _draw_lines(a, with_range=False):
        pts = a[1] if len(a) > 1 else None
        seq = list(pts.data) if isinstance(pts, JArray) else list(pts or ())
        off = max(0, int(a[2] or 0)) if with_range and len(a) > 2 else 0
        cnt = int(a[3] or 0) if with_range and len(a) > 3 else len(seq) - off
        p = a[4] if with_range and len(a) > 4 else (a[2] if len(a) > 2 else None)
        col = _paint_color(p)
        sw = _paint_stroke_w(p)
        lim = min(len(seq), off + max(0, cnt))
        r_obj = _r(a)
        for i in range(off, lim - 3, 4):
            r_obj.line(seq[i], seq[i + 1], seq[i + 2], seq[i + 3], col, width=sw)
        return None
    _add(CV, "drawLines", "([FLandroid/graphics/Paint;)V", lambda a: _draw_lines(a, False))
    _add(CV, "drawLines", "([FIILandroid/graphics/Paint;)V", lambda a: _draw_lines(a, True))

    _add(CV, "drawRect", "(FFFFLandroid/graphics/Paint;)V", _draw_rect)
    _add(CV, "drawRect", "(Landroid/graphics/Rect;Landroid/graphics/Paint;)V",
         lambda a: _r(a).rect(*_rect_coords(a[1]), _paint_color(a[2])) if len(a) > 2 else None)
    _add(CV, "drawRect", "(Landroid/graphics/RectF;Landroid/graphics/Paint;)V",
         lambda a: _r(a).rect(*_rect_coords(a[1]), _paint_color(a[2])) if len(a) > 2 else None)

    _add(CV, "drawRoundRect", "(FFFFFFLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawRoundRect(a[1], a[2], a[3], a[4], a[5], a[6], _paint_color(a[7])) if len(a) > 7 else None)
    _add(CV, "drawRoundRect", "(Landroid/graphics/RectF;FFLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawRoundRect(*_rect_coords(a[1]), a[2], a[3], _paint_color(a[4])) if len(a) > 4 else None)

    _add(CV, "drawCircle", "(FFFLandroid/graphics/Paint;)V", _draw_circle)
    _add(CV, "drawOval", "(FFFFLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawOval(a[1], a[2], a[3], a[4], _paint_color(a[5])) if len(a) > 5 else None)
    _add(CV, "drawOval", "(Landroid/graphics/RectF;Landroid/graphics/Paint;)V",
         lambda a: _r(a).drawOval(*_rect_coords(a[1]), _paint_color(a[2])) if len(a) > 2 else None)

    _add(CV, "drawArc", "(FFFFFFZLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawArc(a[1], a[2], a[3], a[4], a[5], a[6], a[7], _paint_color(a[8])) if len(a) > 8 else None)
    _add(CV, "drawArc", "(Landroid/graphics/RectF;FFZLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawArc(*_rect_coords(a[1]), a[2], a[3], a[4], _paint_color(a[5])) if len(a) > 5 else None)

    _add(CV, "drawPath", "(Landroid/graphics/Path;Landroid/graphics/Paint;)V",
         lambda a: _r(a).drawPath(a[1], _paint_color(a[2])) if len(a) > 2 else None)

    _add(CV, "drawBitmap", "(Landroid/graphics/Bitmap;FFLandroid/graphics/Paint;)V",
         lambda a: _r(a).drawBitmap(a[1], a[2], a[3], None, None, _paint_color(a[4] if len(a) > 4 else None)) if len(a) > 3 else None)
    def _draw_bmp_rect(a):
        if len(a) > 3 and isinstance(a[3], JObject):
            l, t, r, b = _rect_coords(a[3])
            _r(a).drawBitmap(a[1], l, t, max(0.0, r - l), max(0.0, b - t), _paint_color(a[4] if len(a) > 4 else None))
        elif len(a) > 1:
            _r(a).drawBitmap(a[1], 0.0, 0.0)
        return None
    _add(CV, "drawBitmap", "(Landroid/graphics/Bitmap;Landroid/graphics/Rect;Landroid/graphics/Rect;Landroid/graphics/Paint;)V", _draw_bmp_rect)
    _add(CV, "drawBitmap", "(Landroid/graphics/Bitmap;Landroid/graphics/Rect;Landroid/graphics/RectF;Landroid/graphics/Paint;)V", _draw_bmp_rect)
    _add(CV, "drawBitmap", "(Landroid/graphics/Bitmap;Landroid/graphics/Matrix;Landroid/graphics/Paint;)V",
         lambda a: _r(a).drawBitmap(a[1], 0.0, 0.0, None, None, _paint_color(a[3] if len(a) > 3 else None)) if len(a) > 1 else None)

    _add(CV, "drawText", "(Ljava/lang/String;FFLandroid/graphics/Paint;)V", _draw_text)
    _add(CV, "drawText", "(Ljava/lang/String;IIFFLandroid/graphics/Paint;)V",
         lambda a: _emit_text(a, _char_slice(a[1], a[2], a[3]), a[4], a[5], a[6]) if len(a) > 6 else None)
    _add(CV, "drawText", "(Ljava/lang/CharSequence;IIFFLandroid/graphics/Paint;)V",
         lambda a: _emit_text(a, _char_slice(a[1], a[2], a[3]), a[4], a[5], a[6]) if len(a) > 6 else None)
    _add(CV, "drawText", "([CIIFFLandroid/graphics/Paint;)V",
         lambda a: _emit_text(a, _char_count_slice(a[1], a[2], a[3]), a[4], a[5], a[6]) if len(a) > 6 else None)
    _add(CV, "drawTextOnPath", "(Ljava/lang/String;Landroid/graphics/Path;FFLandroid/graphics/Paint;)V",
         lambda a: _emit_text(a, _s(a[1]), a[3], a[4], a[5]) if len(a) > 5 else None)
    _add(CV, "drawPicture", "(Landroid/graphics/Picture;)V", lambda a: None)

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
