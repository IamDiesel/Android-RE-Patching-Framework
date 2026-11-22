# -*- coding: utf-8 -*-
"""core.java.view_breadth_g16 — G16: View, Graphics, Canvas, Text, Widgets, Animators & Accessibility.

Erweitert `AndroidStubs` additiv (ohne bestehende `view_tree`-/`dex_loader`-/`g14`-Stubs zu
ueberschreiben). Unterstuetzt sowohl `$view`-gestuetzte `ViewNode`s (inkl. `ensure_layout()`-Trigger)
als auch nackte `JObject("Landroid/view/View;", ...)`-Instanzen ohne `$view`.
"""
from __future__ import annotations

import copy
import math
import re
import struct as _struct
from typing import Any

from core.java.objects import JArray, JObject, JString


_SIG_PARAM_RE_CACHE: dict[str, list[str]] = {}
_NEXT_VIEW_ID = [1000]


def _f32(v: Any, default: float = 0.0) -> float:
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


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject):
        for k in ("$text", "text", "buf", "$str", "$val", "value"):
            if k in v.fields and v.fields[k] is not None:
                fv = v.fields[k]
                if k == "$val":
                    return _txt(fv)
                return fv.text if isinstance(fv, JString) else str(fv)
        return ""
    return str(v)


def _s32_px(v: Any) -> int:
    u = int(v or 0) & 0xFFFFFFFF
    return u - 0x100000000 if u >= 0x80000000 else u


def _ensure_bitmap_pixels(bmp: JObject) -> list[int] | None:
    if not isinstance(bmp, JObject):
        return None
    bw = max(1, int(bmp.fields.get("_w", 64) or 64))
    bh = max(1, int(bmp.fields.get("_h", 64) or 64))
    total = bw * bh
    if total > 1_048_576:
        return None
    pxs = bmp.fields.get("_pixels")
    if isinstance(pxs, list):
        if len(pxs) < total:
            fill = _s32_px(bmp.fields.get("_erase", 0))
            pxs.extend([fill] * (total - len(pxs)))
        elif len(pxs) > total:
            del pxs[total:]
        return pxs
    raw = bmp.fields.get("_bytes")
    if raw:
        try:
            import io as _io_bm
            from PIL import Image
            im = Image.open(_io_bm.BytesIO(bytes(raw))).convert("RGBA")
            if im.size != (bw, bh):
                im = im.resize((bw, bh), Image.NEAREST)
            rb = im.tobytes()
            dec = [
                _s32_px((rb[i + 3] << 24) | (rb[i] << 16) | (rb[i + 1] << 8) | rb[i + 2])
                for i in range(0, len(rb), 4)
            ]
            bmp.fields["_pixels"] = dec
            return dec
        except Exception:
            pass
    fill = _s32_px(bmp.fields.get("_erase", 0))
    pxs = [fill] * total
    bmp.fields["_pixels"] = pxs
    return pxs


def encode_bitmap_png(bmp: Any) -> bytes:
    """Kodiert `_pixels` eines `Landroid/graphics/Bitmap;`-JObjects verlustfrei nach PNG (`_bytes`)."""
    if not isinstance(bmp, JObject):
        return b""
    existing = bmp.fields.get("_bytes")
    if existing:
        return bytes(existing)
    pxs = bmp.fields.get("_pixels")
    if not isinstance(pxs, list) or not pxs:
        if "_erase" not in bmp.fields:
            return b""
        pxs = _ensure_bitmap_pixels(bmp)
        if not pxs:
            return b""
    bw = max(1, int(bmp.fields.get("_w", 64) or 64))
    bh = max(1, int(bmp.fields.get("_h", 64) or 64))
    total = bw * bh
    if total > 1_048_576:
        return b""
    try:
        import io as _io_bm
        from PIL import Image
        buf = bytearray(total * 4)
        n_src = len(pxs)
        for idx in range(min(total, n_src)):
            u = int(pxs[idx] or 0) & 0xFFFFFFFF
            off = idx * 4
            buf[off] = (u >> 16) & 0xFF
            buf[off + 1] = (u >> 8) & 0xFF
            buf[off + 2] = u & 0xFF
            buf[off + 3] = (u >> 24) & 0xFF
        im = Image.frombytes("RGBA", (bw, bh), bytes(buf))
        if max(bw, bh) <= 160:
            sc = max(1, 256 // max(1, max(bw, bh)))
            if sc > 1:
                im = im.resize((bw * sc, bh * sc), Image.NEAREST)
        out = _io_bm.BytesIO()
        im.save(out, format="PNG")
        png = out.getvalue()
        bmp.fields["_bytes"] = png
        return png
    except Exception:
        return b""


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


def _mat_get(mobj: Any) -> list[float]:
    if isinstance(mobj, JObject):
        m = mobj.fields.get("_m")
        if isinstance(m, list) and len(m) >= 9:
            return [float(x) for x in m[:9]]
        m = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        mobj.fields["_m"] = m
        return m
    return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _mat_set(mobj: Any, vals) -> None:
    if isinstance(mobj, JObject):
        mobj.fields["_m"] = [float(x) for x in vals[:9]]


def _mat_invert_vals(m: list[float]) -> list[float] | None:
    if abs(m[6]) < 1e-9 and abs(m[7]) < 1e-9 and abs(m[8] - 1.0) < 1e-9:
        a, b, c, d, e, f = m[0], m[1], m[2], m[3], m[4], m[5]
        det = a * e - b * d
        if abs(det) < 1e-12:
            return None
        inv_d = 1.0 / det
        return [
            e * inv_d, -b * inv_d, (b * f - c * e) * inv_d,
            -d * inv_d, a * inv_d, (c * d - a * f) * inv_d,
            0.0, 0.0, 1.0,
        ]
    a, b, c = m[0], m[1], m[2]
    d, e, f = m[3], m[4], m[5]
    g, h, i = m[6], m[7], m[8]
    c00 = e * i - f * h
    c01 = -(d * i - f * g)
    c02 = d * h - e * g
    det = a * c00 + b * c01 + c * c02
    if abs(det) < 1e-12:
        return None
    inv_d = 1.0 / det
    return [
        c00 * inv_d, (-(b * i - c * h)) * inv_d, (b * f - c * e) * inv_d,
        c01 * inv_d, (a * i - c * g) * inv_d, (-(a * f - c * d)) * inv_d,
        c02 * inv_d, (-(a * h - b * g)) * inv_d, (a * e - b * d) * inv_d,
    ]


def _rect_read(robj: Any, as_float: bool = False):
    if not isinstance(robj, JObject):
        return (0.0, 0.0, 0.0, 0.0) if as_float else (0, 0, 0, 0)
    f = robj.fields
    if as_float:
        return (
            _f32(f.get("left", 0.0)),
            _f32(f.get("top", 0.0)),
            _f32(f.get("right", 0.0)),
            _f32(f.get("bottom", 0.0)),
        )
    return (
        int(_f32(f.get("left", 0))),
        int(_f32(f.get("top", 0))),
        int(_f32(f.get("right", 0))),
        int(_f32(f.get("bottom", 0))),
    )


def _rect_write(robj: Any, l, t, r, b, as_float: bool = False) -> None:
    if isinstance(robj, JObject):
        conv = float if as_float else int
        robj.fields["left"] = conv(l)
        robj.fields["top"] = conv(t)
        robj.fields["right"] = conv(r)
        robj.fields["bottom"] = conv(b)


def _compute_text_layout(text_str: str, paint_obj: Any, width_px: int, max_lines: int | None = None):
    sz = _f32(paint_obj.fields.get("$textSize", 12.0), 12.0) if isinstance(paint_obj, JObject) else 12.0
    sz = max(1.0, sz)
    cw = max(1.0, sz * 0.55)
    w = max(1, int(width_px or 1000))
    cpl = max(1, int(w / cw))
    lines: list[tuple[str, float]] = []
    if text_str == "":
        lines.append(("", 0.0))
    else:
        for para in text_str.split("\n"):
            if para == "":
                lines.append(("", 0.0))
                continue
            pos = 0
            while pos < len(para):
                seg = para[pos:pos + cpl]
                lines.append((seg, float(len(seg) * cw)))
                pos += cpl
    if max_lines is not None and max_lines > 0 and len(lines) > max_lines:
        lines = lines[:max_lines]
    line_h = max(1, int(round(sz * 1.2)))
    return {
        "_text": text_str,
        "_paint": paint_obj,
        "_width": w,
        "_line_h": line_h,
        "_lines": lines,
        "_height": max(1, len(lines)) * line_h,
    }


def _compute_line_offsets(lines: list[tuple[str, float]]) -> list[tuple[int, int]]:
    out = []
    off = 0
    for seg, _ in lines:
        nxt = off + len(seg)
        out.append((off, nxt))
        off = nxt
    return out


def install_g16_stubs(stubs: Any) -> None:
    """Registriert G16-Breitenstubs (View, ViewGroup, Widgets, Graphics, Accessibility, Window, Text)."""
    if stubs is None or getattr(stubs, "_g16_installed", False):
        return
    stubs._g16_installed = True

    if getattr(stubs, "canvas_recorder", None) is None:
        from core.java.canvas_recorder import install_canvas_stubs
        install_canvas_stubs(stubs)

    R = stubs.register
    RB = stubs.register_by_name

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            R(cls, name, sig, fn)

    def _vt_bn(name: str, sig: str):
        if getattr(stubs, "view_tree", None) is not None:
            return stubs._by_name.get((name, sig))
        return None

    def _touch_vt() -> None:
        vt = getattr(stubs, "view_tree", None)
        if vt is not None and hasattr(vt, "touch"):
            vt.touch()

    def _vn(obj: Any):
        return obj.fields.get("$view") if isinstance(obj, JObject) else None

    def _view_bounds(obj: Any) -> tuple[int, int, int, int]:
        if not isinstance(obj, JObject):
            return 0, 0, 0, 0
        vn = _vn(obj)
        if vn is not None:
            t = getattr(vn, "_tree", None)
            if t is not None:
                if getattr(t, "_layout_dirty", False) or vn._bounds == (0, 0, 0, 0):
                    try:
                        t.ensure_layout()
                    except Exception:
                        pass
            bx, by, bw, bh = getattr(vn, "_bounds", (0, 0, 0, 0))
            if bw > 0 or bh > 0 or t is not None:
                return int(bx), int(by), int(bw), int(bh)
        f = obj.fields
        l = int(f.get("_left", 0) or 0)
        t_y = int(f.get("_top", 0) or 0)
        if f.get("_laid_out") or "_right" in f or "_bottom" in f:
            r = int(f.get("_right", l) or 0)
            b = int(f.get("_bottom", t_y) or 0)
            return l, t_y, max(0, r - l), max(0, b - t_y)
        w = int(f.get("$w", f.get("_m_w", 0)) or 0)
        h = int(f.get("$h", f.get("_m_h", 0)) or 0)
        return l, t_y, max(0, w), max(0, h)

    def _view_measured(obj: Any) -> tuple[int, int]:
        if not isinstance(obj, JObject):
            return 0, 0
        f = obj.fields
        if "_m_w" in f or "_m_h" in f:
            mw = int(f.get("_m_w", f.get("$w", 0)) or 0)
            mh = int(f.get("_m_h", f.get("$h", 0)) or 0)
            if mw > 0 or mh > 0:
                return mw, mh
        vn = _vn(obj)
        if vn is not None:
            _, _, bw, bh = _view_bounds(obj)
            if bw > 0 or bh > 0:
                return bw, bh
        mw = int(f.get("_m_w", f.get("$w", 0)) or 0)
        mh = int(f.get("_m_h", f.get("$h", 0)) or 0)
        if mw == 0 and mh == 0 and ("_right" in f or "_bottom" in f):
            _, _, bw, bh = _view_bounds(obj)
            return bw, bh
        return mw, mh

    def _view_get_vis(obj: Any) -> int:
        if not isinstance(obj, JObject):
            return 0
        vn = _vn(obj)
        if vn is not None:
            if "visibility" in vn.props:
                return int(vn.props["visibility"])
            if "_visibility" in obj.fields:
                return int(obj.fields["_visibility"])
            if "visibility" in obj.fields:
                return int(obj.fields["visibility"])
            return 0 if getattr(vn, "visible", True) else 8
        if "_visibility" in obj.fields:
            return int(obj.fields["_visibility"])
        return int(obj.fields.get("visibility", 0))

    def _view_set_vis(a):
        if a and len(a) > 1 and isinstance(a[0], JObject):
            vis = int(a[1]) if a[1] is not None else 0
            a[0].fields["_visibility"] = vis
            a[0].fields["visibility"] = vis
            bn_fn = _vt_bn("setVisibility", "(I)V")
            if bn_fn is not None:
                return bn_fn(a)
            vn = _vn(a[0])
            if vn is not None:
                vn.visible = (vis == 0)
                vn.props["visibility"] = vis
                _touch_vt()
        return None

    def _wrap_vn(vn):
        if vn is None:
            return None
        jo = getattr(vn, "_jobj", None)
        if isinstance(jo, JObject):
            jo.fields["$view"] = vn
            return jo
        short = getattr(vn, "cls", "View") or "View"
        wmap = {
            "View": "Landroid/view/View;",
            "ViewGroup": "Landroid/view/ViewGroup;",
            "FrameLayout": "Landroid/widget/FrameLayout;",
            "LinearLayout": "Landroid/widget/LinearLayout;",
            "RelativeLayout": "Landroid/widget/RelativeLayout;",
            "ScrollView": "Landroid/widget/ScrollView;",
        }
        jcls = wmap.get(short, f"Landroid/widget/{short};")
        jo = JObject(jcls, {"$view": vn})
        vn._jobj = jo
        return jo

    def _ctx_default():
        return getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)

    def _measure_dim(spec: int, min_dim: int = 0) -> int:
        mode = int(spec) & 0xC0000000
        size = int(spec) & 0x3FFFFFFF
        if mode == 0x40000000:
            return max(min_dim, size)
        if mode == 0x80000000:
            return max(min_dim, min(size, max(min_dim, size)))
        return max(min_dim, size)

    # =========================================================================
    # 1. Landroid/view/View;
    # =========================================================================
    V = "Landroid/view/View;"

    def _v_init(a):
        if a and isinstance(a[0], JObject):
            f = a[0].fields
            f.setdefault("_visibility", 0)
            f.setdefault("_alpha", 1.0)
            f.setdefault("_scale_x", 1.0)
            f.setdefault("_scale_y", 1.0)
            f.setdefault("_enabled", 1)
            if len(a) > 1 and isinstance(a[1], JObject):
                f.setdefault("_ctx", a[1])
        return a[0] if a else None

    for _vsig in (
        "()V",
        "(Landroid/content/Context;)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;I)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;II)V",
    ):
        _add(V, "<init>", _vsig, _v_init)

    _add(V, "getWidth", "()I", lambda a: _view_bounds(a[0] if a else None)[2])
    _add(V, "getHeight", "()I", lambda a: _view_bounds(a[0] if a else None)[3])
    _add(V, "getMeasuredWidth", "()I", lambda a: _view_measured(a[0] if a else None)[0])
    _add(V, "getMeasuredHeight", "()I", lambda a: _view_measured(a[0] if a else None)[1])
    _add(V, "getMeasuredState", "()I", lambda a: int(a[0].fields.get("_m_state", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getMeasuredWidthAndState", "()I", lambda a: _view_measured(a[0] if a else None)[0])
    _add(V, "getMeasuredHeightAndState", "()I", lambda a: _view_measured(a[0] if a else None)[1])
    _add(V, "getLeft", "()I", lambda a: _view_bounds(a[0] if a else None)[0])
    _add(V, "getTop", "()I", lambda a: _view_bounds(a[0] if a else None)[1])
    _add(V, "getRight", "()I", lambda a: (lambda b: b[0] + b[2])(_view_bounds(a[0] if a else None)))
    _add(V, "getBottom", "()I", lambda a: (lambda b: b[1] + b[3])(_view_bounds(a[0] if a else None)))

    def _v_set_ltrb(a):
        if len(a) >= 5 and isinstance(a[0], JObject):
            l, t_y, r, b = int(a[1]), int(a[2]), int(a[3]), int(a[4])
            a[0].fields.update({"_left": l, "_top": t_y, "_right": r, "_bottom": b, "_laid_out": True})
            vn = _vn(a[0])
            if vn is not None and getattr(vn, "_tree", None) is None:
                vn._bounds = (l, t_y, max(0, r - l), max(0, b - t_y))
        return None
    _add(V, "setLeftTopRightBottom", "(IIII)V", _v_set_ltrb)
    _add(V, "layout", "(IIII)V", _v_set_ltrb)
    _add(V, "onLayout", "(ZIIII)V", lambda a: None)

    def _v_set_measured(a):
        if len(a) >= 3 and isinstance(a[0], JObject):
            a[0].fields["_m_w"] = int(a[1]) & 0x00FFFFFF
            a[0].fields["_m_h"] = int(a[2]) & 0x00FFFFFF
            a[0].fields["_m_state"] = (int(a[1]) & 0xFF000000) | ((int(a[2]) >> 16) & 0xFF00)
        return None
    _add(V, "setMeasuredDimension", "(II)V", _v_set_measured)

    def _v_measure(a):
        if a and isinstance(a[0], JObject):
            ws = int(a[1]) if len(a) > 1 and a[1] is not None else 0
            hs = int(a[2]) if len(a) > 2 and a[2] is not None else 0
            mw = _measure_dim(ws, int(a[0].fields.get("_min_w", 0) or 0))
            mh = _measure_dim(hs, int(a[0].fields.get("_min_h", 0) or 0))
            a[0].fields["_m_w"] = mw
            a[0].fields["_m_h"] = mh
        return None
    _add(V, "measure", "(II)V", _v_measure)
    _add(V, "onMeasure", "(II)V", _v_measure)

    def _v_offset(a, horiz: bool):
        if len(a) > 1 and isinstance(a[0], JObject):
            d = int(a[1] or 0)
            l, t_y, w, h = _view_bounds(a[0])
            if horiz:
                l += d
            else:
                t_y += d
            _v_set_ltrb([a[0], l, t_y, l + w, t_y + h])
        return None
    _add(V, "offsetLeftAndRight", "(I)V", lambda a: _v_offset(a, True))
    _add(V, "offsetTopAndBottom", "(I)V", lambda a: _v_offset(a, False))

    def _v_fset(key: str, default: float = 0.0):
        def _fn(a):
            if len(a) > 1 and isinstance(a[0], JObject):
                a[0].fields[key] = _f32(a[1], default)
            return None
        return _fn

    def _v_fget(key: str, default: float = 0.0):
        return lambda a: float(_f32(a[0].fields.get(key, default), default)) if a and isinstance(a[0], JObject) else default

    _add(V, "getTranslationX", "()F", _v_fget("_tx", 0.0))
    _add(V, "setTranslationX", "(F)V", _v_fset("_tx", 0.0))
    _add(V, "getTranslationY", "()F", _v_fget("_ty", 0.0))
    _add(V, "setTranslationY", "(F)V", _v_fset("_ty", 0.0))
    _add(V, "getTranslationZ", "()F", _v_fget("_tz", 0.0))
    _add(V, "setTranslationZ", "(F)V", _v_fset("_tz", 0.0))
    _add(V, "getElevation", "()F", _v_fget("_elevation", 0.0))
    _add(V, "setElevation", "(F)V", _v_fset("_elevation", 0.0))
    _add(V, "getZ", "()F", lambda a: _f32(a[0].fields.get("_elevation", 0.0)) + _f32(a[0].fields.get("_tz", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(V, "setZ", "(F)V", lambda a: a[0].fields.__setitem__("_tz", _f32(a[1]) - _f32(a[0].fields.get("_elevation", 0.0))) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getX", "()F", lambda a: float(_view_bounds(a[0])[0]) + (_f32(a[0].fields.get("_tx", 0.0)) if isinstance(a[0], JObject) else 0.0) if a else 0.0)
    _add(V, "getY", "()F", lambda a: float(_view_bounds(a[0])[1]) + (_f32(a[0].fields.get("_ty", 0.0)) if isinstance(a[0], JObject) else 0.0) if a else 0.0)
    _add(V, "setX", "(F)V", lambda a: a[0].fields.__setitem__("_tx", _f32(a[1]) - float(_view_bounds(a[0])[0])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "setY", "(F)V", lambda a: a[0].fields.__setitem__("_ty", _f32(a[1]) - float(_view_bounds(a[0])[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getScaleX", "()F", _v_fget("_scale_x", 1.0))
    _add(V, "setScaleX", "(F)V", _v_fset("_scale_x", 1.0))
    _add(V, "getScaleY", "()F", _v_fget("_scale_y", 1.0))
    _add(V, "setScaleY", "(F)V", _v_fset("_scale_y", 1.0))
    _add(V, "getRotation", "()F", _v_fget("_rot", 0.0))
    _add(V, "setRotation", "(F)V", _v_fset("_rot", 0.0))
    _add(V, "getRotationX", "()F", _v_fget("_rot_x", 0.0))
    _add(V, "setRotationX", "(F)V", _v_fset("_rot_x", 0.0))
    _add(V, "getRotationY", "()F", _v_fget("_rot_y", 0.0))
    _add(V, "setRotationY", "(F)V", _v_fset("_rot_y", 0.0))
    _add(V, "getPivotX", "()F", lambda a: _f32(a[0].fields["_pivot_x"]) if a and isinstance(a[0], JObject) and "_pivot_x" in a[0].fields else float(_view_bounds(a[0] if a else None)[2]) / 2.0)
    _add(V, "setPivotX", "(F)V", _v_fset("_pivot_x", 0.0))
    _add(V, "getPivotY", "()F", lambda a: _f32(a[0].fields["_pivot_y"]) if a and isinstance(a[0], JObject) and "_pivot_y" in a[0].fields else float(_view_bounds(a[0] if a else None)[3]) / 2.0)
    _add(V, "setPivotY", "(F)V", _v_fset("_pivot_y", 0.0))
    _add(V, "getTransitionAlpha", "()F", _v_fget("_t_alpha", 1.0))
    _add(V, "setTransitionAlpha", "(F)V", _v_fset("_t_alpha", 1.0))

    def _v_set_alpha(a):
        bn = _vt_bn("setAlpha", "(F)V")
        if bn is not None:
            return bn(a)
        if len(a) > 1 and isinstance(a[0], JObject):
            al = max(0.0, min(1.0, _f32(a[1], 1.0)))
            a[0].fields["_alpha"] = al
            vn = _vn(a[0])
            if vn is not None:
                vn.props["alpha"] = al
                _touch_vt()
        return None

    def _v_get_alpha(a):
        bn = _vt_bn("getAlpha", "()F")
        if bn is not None:
            vn = _vn(a[0]) if a else None
            if vn is not None:
                return bn(a)
        if a and isinstance(a[0], JObject):
            vn = _vn(a[0])
            if vn is not None and "alpha" in vn.props:
                return float(vn.props["alpha"])
            return float(_f32(a[0].fields.get("_alpha", 1.0), 1.0))
        return 1.0
    _add(V, "setAlpha", "(F)V", _v_set_alpha)
    _add(V, "getAlpha", "()F", _v_get_alpha)

    def _v_get_matrix(a):
        if a and isinstance(a[0], JObject):
            tx = _f32(a[0].fields.get("_tx", 0.0))
            ty = _f32(a[0].fields.get("_ty", 0.0))
            sx = _f32(a[0].fields.get("_scale_x", 1.0), 1.0)
            sy = _f32(a[0].fields.get("_scale_y", 1.0), 1.0)
            m = a[0].fields.get("_anim_matrix")
            if isinstance(m, JObject):
                return m
            return JObject("Landroid/graphics/Matrix;", {"_m": [sx, 0.0, tx, 0.0, sy, ty, 0.0, 0.0, 1.0]})
        return JObject("Landroid/graphics/Matrix;", {"_m": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]})
    _add(V, "getMatrix", "()Landroid/graphics/Matrix;", _v_get_matrix)
    _add(V, "setAnimationMatrix", "(Landroid/graphics/Matrix;)V",
         lambda a: a[0].fields.__setitem__("_anim_matrix", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "transformMatrixToGlobal", "(Landroid/graphics/Matrix;)V", lambda a: None)
    _add(V, "transformMatrixToLocal", "(Landroid/graphics/Matrix;)V", lambda a: None)

    # Scroll
    _add(V, "getScrollX", "()I", lambda a: int(a[0].fields.get("_scroll_x", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getScrollY", "()I", lambda a: int(a[0].fields.get("_scroll_y", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "setScrollX", "(I)V", lambda a: a[0].fields.__setitem__("_scroll_x", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "setScrollY", "(I)V", lambda a: a[0].fields.__setitem__("_scroll_y", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "scrollTo", "(II)V", lambda a: a[0].fields.update({"_scroll_x": int(a[1]), "_scroll_y": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(V, "scrollBy", "(II)V",
         lambda a: a[0].fields.update({
             "_scroll_x": int(a[0].fields.get("_scroll_x", 0)) + int(a[1]),
             "_scroll_y": int(a[0].fields.get("_scroll_y", 0)) + int(a[2]),
         }) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(V, "canScrollHorizontally", "(I)Z", lambda a: 0)
    _add(V, "canScrollVertically", "(I)Z", lambda a: 0)

    # Visibility & State
    _add(V, "getVisibility", "()I", lambda a: _view_get_vis(a[0] if a else None))
    _add(V, "setVisibility", "(I)V", _view_set_vis)
    _add(V, "setTransitionVisibility", "(I)V", _view_set_vis)

    def _v_is_shown(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        if _view_get_vis(a[0]) != 0:
            return 0
        vn = _vn(a[0])
        if vn is not None:
            cur = getattr(vn, "_parent", None)
            while cur is not None:
                if not getattr(cur, "visible", True):
                    return 0
                cur = getattr(cur, "_parent", None)
        return 1
    _add(V, "isShown", "()Z", _v_is_shown)
    _add(V, "getWindowVisibility", "()I", lambda a: 0 if _v_is_shown(a) else 8)

    def _v_is_attached(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        if "_attached" in a[0].fields:
            return 1 if a[0].fields["_attached"] else 0
        vn = _vn(a[0])
        if vn is not None:
            if getattr(vn, "_tree", None) is not None or getattr(vn, "_parent", None) is not None:
                return 1
            vt = getattr(stubs, "view_tree", None)
            if vt is not None and vt.root is vn:
                return 1
        return 1 if a[0].fields.get("_parent") is not None else 0
    _add(V, "isAttachedToWindow", "()Z", _v_is_attached)
    _add(V, "isLaidOut", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and (a[0].fields.get("_laid_out") or _view_bounds(a[0])[2] > 0 or _view_bounds(a[0])[3] > 0)) else 0)
    _add(V, "isInLayout", "()Z", lambda a: 0)
    _add(V, "isInEditMode", "()Z", lambda a: 0)
    _add(V, "isLayoutRequested", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_layout_req")) else 0)
    _add(V, "requestLayout", "()V", lambda a: a[0].fields.__setitem__("_layout_req", True) if a and isinstance(a[0], JObject) else None)
    _add(V, "forceLayout", "()V", lambda a: a[0].fields.__setitem__("_layout_req", True) if a and isinstance(a[0], JObject) else None)

    for _flag_k, _gname, _sname, _def in (
        ("_focused", "isFocused", None, 0),
        ("_clickable", "isClickable", "setClickable", 0),
        ("_long_clickable", "isLongClickable", "setLongClickable", 0),
        ("_focusable", "isFocusable", "setFocusable", 0),
        ("_activated", "isActivated", "setActivated", 0),
        ("_pressed", "isPressed", "setPressed", 0),
    ):
        _add(V, _gname, "()Z", lambda a, k=_flag_k, d=_def: int(bool(a[0].fields.get(k, d))) if a and isinstance(a[0], JObject) else d)
        if _sname:
            _add(V, _sname, "(Z)V", lambda a, k=_flag_k: a[0].fields.__setitem__(k, 1 if (len(a) > 1 and a[1]) else 0) if a and isinstance(a[0], JObject) else None)

    _add(V, "setFocusable", "(I)V", lambda a: a[0].fields.__setitem__("_focusable", 1 if (len(a) > 1 and a[1]) else 0) if a and isinstance(a[0], JObject) else None)
    _add(V, "setFocusableInTouchMode", "(Z)V", lambda a: a[0].fields.__setitem__("_focusable", 1 if (len(a) > 1 and a[1]) else 0) if a and isinstance(a[0], JObject) else None)
    _add(V, "hasFocus", "()Z", lambda a: int(bool(a[0].fields.get("_focused", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(V, "hasFocusable", "()Z", lambda a: int(bool(a[0].fields.get("_focusable", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(V, "findFocus", "()Landroid/view/View;", lambda a: a[0] if (a and isinstance(a[0], JObject) and a[0].fields.get("_focused")) else None)
    _add(V, "addFocusables", "(Ljava/util/ArrayList;II)V", lambda a: None)

    def _v_req_focus(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["_focused"] = 1
        return 1
    _add(V, "requestFocus", "()Z", _v_req_focus)
    _add(V, "requestFocus", "(I)Z", _v_req_focus)
    _add(V, "requestFocus", "(ILandroid/graphics/Rect;)Z", _v_req_focus)
    _add(V, "clearFocus", "()V", lambda a: a[0].fields.__setitem__("_focused", 0) if a and isinstance(a[0], JObject) else None)

    def _v_set_selected(a):
        bn = _vt_bn("setSelected", "(Z)V")
        if bn is not None:
            return bn(a)
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_selected"] = 1 if a[1] else 0
        return None

    def _v_is_selected(a):
        bn = _vt_bn("isSelected", "()Z")
        if bn is not None and _vn(a[0] if a else None) is not None:
            return bn(a)
        return int(bool(a[0].fields.get("_selected", 0))) if a and isinstance(a[0], JObject) else 0
    _add(V, "setSelected", "(Z)V", _v_set_selected)
    _add(V, "isSelected", "()Z", _v_is_selected)

    def _v_set_enabled(a):
        bn = _vt_bn("setEnabled", "(Z)V")
        if bn is not None:
            return bn(a)
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_enabled"] = 1 if a[1] else 0
        return None

    def _v_is_enabled(a):
        bn = _vt_bn("isEnabled", "()Z")
        if bn is not None and _vn(a[0] if a else None) is not None:
            return bn(a)
        return int(bool(a[0].fields.get("_enabled", 1))) if a and isinstance(a[0], JObject) else 1
    _add(V, "setEnabled", "(Z)V", _v_set_enabled)
    _add(V, "isEnabled", "()Z", _v_is_enabled)

    # Click & Listeners
    def _v_set_click(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_on_click"] = a[1]
            a[0].fields["_clickable"] = 1
        bn = _vt_bn("setOnClickListener", "(Landroid/view/View$OnClickListener;)V")
        if bn is not None:
            return bn(a)
        return None
    _add(V, "setOnClickListener", "(Landroid/view/View$OnClickListener;)V", _v_set_click)
    _add(V, "hasOnClickListeners", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and (a[0].fields.get("_on_click") is not None or (_vn(a[0]) is not None and _vn(a[0]).on_click is not None))) else 0)

    def _v_perform_click(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        vn = _vn(a[0])
        cb = (vn.on_click if vn is not None else None) or a[0].fields.get("_on_click")
        if callable(cb):
            cb(vn or a[0])
            return 1
        vm_cur = getattr(stubs, "vm", None)
        if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            try:
                vm_cur.invoke_virtual(cb, "onClick", "(Landroid/view/View;)V", (cb, a[0]))
                return 1
            except Exception:
                pass
        return 1 if cb is not None else 0
    _add(V, "performClick", "()Z", _v_perform_click)
    _add(V, "callOnClick", "()Z", _v_perform_click)
    _add(V, "performHapticFeedback", "(I)Z", lambda a: 1)

    for _lset, _lf in (
        ("setOnTouchListener", "(Landroid/view/View$OnTouchListener;)V"),
        ("setOnLongClickListener", "(Landroid/view/View$OnLongClickListener;)V"),
        ("setOnFocusChangeListener", "(Landroid/view/View$OnFocusChangeListener;)V"),
        ("setOnHoverListener", "(Landroid/view/View$OnHoverListener;)V"),
        ("setOnKeyListener", "(Landroid/view/View$OnKeyListener;)V"),
        ("setOnApplyWindowInsetsListener", "(Landroid/view/View$OnApplyWindowInsetsListener;)V"),
        ("addOnAttachStateChangeListener", "(Landroid/view/View$OnAttachStateChangeListener;)V"),
        ("removeOnAttachStateChangeListener", "(Landroid/view/View$OnAttachStateChangeListener;)V"),
        ("addOnLayoutChangeListener", "(Landroid/view/View$OnLayoutChangeListener;)V"),
        ("removeOnLayoutChangeListener", "(Landroid/view/View$OnLayoutChangeListener;)V"),
    ):
        _add(V, _lset, _lf, lambda a, k=f"_{_lset}": a[0].fields.__setitem__(k, a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)

    _add(V, "onTouchEvent", "(Landroid/view/MotionEvent;)Z", lambda a: 0)
    _add(V, "dispatchTouchEvent", "(Landroid/view/MotionEvent;)Z", lambda a: 0)
    _add(V, "cancelPendingInputEvents", "()V", lambda a: None)
    _add(V, "onCheckIsTextEditor", "()Z", lambda a: 0)
    _add(V, "getKeyDispatcherState", "()Landroid/view/KeyEvent$DispatcherState;",
         lambda a: a[0].fields.setdefault("_kds", JObject("Landroid/view/KeyEvent$DispatcherState;", {})) if a and isinstance(a[0], JObject) else JObject("Landroid/view/KeyEvent$DispatcherState;", {}))

    # Screen / Hit / Clip / Drawing Rects
    def _v_location(a):
        if len(a) > 1 and isinstance(a[1], JArray) and len(a[1].data) >= 2 and a and isinstance(a[0], JObject):
            bx, by, _, _ = _view_bounds(a[0])
            a[1].data[0] = int(bx)
            a[1].data[1] = int(by)
        return None
    _add(V, "getLocationOnScreen", "([I)V", _v_location)
    _add(V, "getLocationInWindow", "([I)V", _v_location)

    def _v_global_rect(a):
        if len(a) > 1 and isinstance(a[1], JObject) and a and isinstance(a[0], JObject):
            bx, by, bw, bh = _view_bounds(a[0])
            _rect_write(a[1], bx, by, bx + bw, by + bh)
            return 1 if (_v_is_shown([a[0]]) and bw > 0 and bh > 0) else (1 if _v_is_shown([a[0]]) else 0)
        return 0
    _add(V, "getGlobalVisibleRect", "(Landroid/graphics/Rect;)Z", _v_global_rect)
    _add(V, "getGlobalVisibleRect", "(Landroid/graphics/Rect;Landroid/graphics/Point;)Z", _v_global_rect)

    def _v_local_rect(a):
        if len(a) > 1 and isinstance(a[1], JObject) and a and isinstance(a[0], JObject):
            _, _, bw, bh = _view_bounds(a[0])
            _rect_write(a[1], 0, 0, bw, bh)
            return 1 if _v_is_shown([a[0]]) else 0
        return 0
    _add(V, "getLocalVisibleRect", "(Landroid/graphics/Rect;)Z", _v_local_rect)

    _add(V, "getHitRect", "(Landroid/graphics/Rect;)V",
         lambda a: (lambda b: _rect_write(a[1], b[0], b[1], b[0] + b[2], b[1] + b[3]))(_view_bounds(a[0])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getDrawingRect", "(Landroid/graphics/Rect;)V",
         lambda a: (lambda sx, sy, b: _rect_write(a[1], sx, sy, sx + b[2], sy + b[3]))(
             int(a[0].fields.get("_scroll_x", 0)), int(a[0].fields.get("_scroll_y", 0)), _view_bounds(a[0])
         ) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getWindowVisibleDisplayFrame", "(Landroid/graphics/Rect;)V",
         lambda a: _rect_write(a[1], 0, 0, 1080, 2280) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add(V, "setClipBounds", "(Landroid/graphics/Rect;)V",
         lambda a: a[0].fields.__setitem__("_clip_bounds", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getClipBounds", "()Landroid/graphics/Rect;",
         lambda a: a[0].fields.get("_clip_bounds") if a and isinstance(a[0], JObject) else None)
    _add(V, "getClipBounds", "(Landroid/graphics/Rect;)Z",
         lambda a: (lambda cb: (_rect_write(a[1], *_rect_read(cb)), 1)[1] if isinstance(cb, JObject) and len(a) > 1 else 0)(a[0].fields.get("_clip_bounds")) if a and isinstance(a[0], JObject) else 0)

    # Hierarchy / IDs / Context / Draw / Post / Padding
    def _v_gen_id(a):
        _NEXT_VIEW_ID[0] += 1
        return _NEXT_VIEW_ID[0]
    _add(V, "generateViewId", "()I", _v_gen_id)
    _add(V, "getId", "()I", lambda a: int(a[0].fields.get("id", a[0].fields.get("$id", -1))) if a and isinstance(a[0], JObject) else -1)
    _add(V, "setId", "(I)V", lambda a: a[0].fields.__setitem__("id", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    def _v_find_by_id(a):
        bn = _vt_bn("findViewById", "(I)Landroid/view/View;")
        if bn is not None:
            res = bn(a)
            if res not in (None, 0):
                return res
        if len(a) > 1 and isinstance(a[0], JObject):
            tid = int(a[1] or 0)
            if int(a[0].fields.get("id", a[0].fields.get("$id", -1))) == tid:
                return a[0]
            for ch in a[0].fields.get("_children", ()):
                if isinstance(ch, JObject) and int(ch.fields.get("id", -1)) == tid:
                    return ch
        return None
    _add(V, "findViewById", "(I)Landroid/view/View;", _v_find_by_id)

    def _v_get_parent(a):
        if a and isinstance(a[0], JObject):
            vn = _vn(a[0])
            if vn is not None and getattr(vn, "_parent", None) is not None:
                return _wrap_vn(vn._parent)
            return a[0].fields.get("_parent")
        return None
    _add(V, "getParent", "()Landroid/view/ViewParent;", _v_get_parent)
    _add(V, "getParentForAccessibility", "()Landroid/view/ViewParent;", _v_get_parent)

    def _v_get_root(a):
        if not a or not isinstance(a[0], JObject):
            return None
        vn = _vn(a[0])
        if vn is not None:
            t = getattr(vn, "_tree", None)
            if t is not None and t.root is not None:
                return _wrap_vn(t.root)
            cur = vn
            while getattr(cur, "_parent", None) is not None:
                cur = cur._parent
            return _wrap_vn(cur)
        cur = a[0]
        seen = set()
        while isinstance(cur.fields.get("_parent"), JObject) and id(cur) not in seen:
            seen.add(id(cur))
            cur = cur.fields["_parent"]
        return cur
    _add(V, "getRootView", "()Landroid/view/View;", _v_get_root)

    _add(V, "invalidate", "()V", lambda a: None)
    _add(V, "invalidate", "(IIII)V", lambda a: None)
    _add(V, "invalidate", "(Landroid/graphics/Rect;)V", lambda a: None)
    _add(V, "postInvalidate", "()V", lambda a: None)
    _add(V, "postInvalidate", "(IIII)V", lambda a: None)
    _add(V, "postInvalidateOnAnimation", "()V", lambda a: None)
    _add(V, "postInvalidateOnAnimation", "(IIII)V", lambda a: None)
    _add(V, "draw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add(V, "onDraw", "(Landroid/graphics/Canvas;)V", lambda a: None)

    def _v_post(a, delay: int = 0):
        r = a[1] if len(a) > 1 else None
        q = getattr(stubs, "_queue_runnable", None)
        if callable(q) and r is not None:
            return q(r, delay)
        return 1
    _add(V, "post", "(Ljava/lang/Runnable;)Z", lambda a: _v_post(a, 0))
    _add(V, "postDelayed", "(Ljava/lang/Runnable;J)Z", lambda a: _v_post(a, int(a[2]) if len(a) > 2 else 0))
    _add(V, "postOnAnimation", "(Ljava/lang/Runnable;)V", lambda a: (_v_post(a, 0), None)[1])
    _add(V, "postOnAnimationDelayed", "(Ljava/lang/Runnable;J)V", lambda a: (_v_post(a, int(a[2]) if len(a) > 2 else 0), None)[1])

    def _v_rem_cb(a):
        r = a[1] if len(a) > 1 else None
        pr = getattr(stubs, "pending_runnables", None)
        if isinstance(pr, list) and r is not None:
            pr[:] = [x for x in pr if x is not r]
            return 1
        return 0
    _add(V, "removeCallbacks", "(Ljava/lang/Runnable;)Z", _v_rem_cb)
    _add(V, "getHandler", "()Landroid/os/Handler;", lambda a: JObject("Landroid/os/Handler;", {}))

    def _v_set_pad(a, rel: bool = False):
        if len(a) >= 5 and isinstance(a[0], JObject):
            a[0].fields.update({
                "_pad_l": int(a[1]), "_pad_t": int(a[2]),
                "_pad_r": int(a[3]), "_pad_b": int(a[4]),
                "_pad_rel": bool(rel),
            })
        return None
    _add(V, "setPadding", "(IIII)V", lambda a: _v_set_pad(a, False))
    _add(V, "setPaddingRelative", "(IIII)V", lambda a: _v_set_pad(a, True))
    _add(V, "getPaddingLeft", "()I", lambda a: int(a[0].fields.get("_pad_l", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getPaddingTop", "()I", lambda a: int(a[0].fields.get("_pad_t", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getPaddingRight", "()I", lambda a: int(a[0].fields.get("_pad_r", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getPaddingBottom", "()I", lambda a: int(a[0].fields.get("_pad_b", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getPaddingStart", "()I", lambda a: int(a[0].fields.get("_pad_l", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getPaddingEnd", "()I", lambda a: int(a[0].fields.get("_pad_r", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "isPaddingRelative", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_pad_rel")) else 0)
    _add(V, "getLayoutDirection", "()I", lambda a: int(a[0].fields.get("_layout_dir", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "setLayoutDirection", "(I)V", lambda a: a[0].fields.__setitem__("_layout_dir", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getBaseline", "()I", lambda a: -1)
    _add(V, "getMinimumWidth", "()I", lambda a: int(a[0].fields.get("_min_w", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "setMinimumWidth", "(I)V", lambda a: a[0].fields.__setitem__("_min_w", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getMinimumHeight", "()I", lambda a: int(a[0].fields.get("_min_h", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "setMinimumHeight", "(I)V", lambda a: a[0].fields.__setitem__("_min_h", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    _add(V, "getContext", "()Landroid/content/Context;", lambda a: (a[0].fields.get("_ctx") if a and isinstance(a[0], JObject) else None) or _ctx_default())
    _add(V, "getResources", "()Landroid/content/res/Resources;", lambda a: JObject("Landroid/content/res/Resources;", {}))
    _add(V, "getDisplay", "()Landroid/view/Display;", lambda a: JObject("Landroid/view/Display;", {}))
    _add(V, "getApplicationWindowToken", "()Landroid/os/IBinder;", lambda a: JObject("Landroid/os/IBinder;", {}))
    _add(V, "getWindowToken", "()Landroid/os/IBinder;", lambda a: JObject("Landroid/os/IBinder;", {}))
    _add(V, "getWindowSystemUiVisibility", "()I", lambda a: int(a[0].fields.get("_sys_ui_vis", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(V, "getOverlay", "()Landroid/view/ViewOverlay;", lambda a: JObject("Landroid/view/ViewOverlay;", {}))

    def _v_get_bg(a):
        if a and isinstance(a[0], JObject):
            drv = a[0].fields.get("_bg_drv_obj")
            if isinstance(drv, JObject):
                return drv
            return None
        return None
    _add(V, "getBackground", "()Landroid/graphics/drawable/Drawable;", _v_get_bg)
    _add(V, "setBackground", "(Landroid/graphics/drawable/Drawable;)V",
         lambda a: a[0].fields.__setitem__("_bg_drv_obj", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "setBackgroundDrawable", "(Landroid/graphics/drawable/Drawable;)V",
         lambda a: a[0].fields.__setitem__("_bg_drv_obj", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "setBackgroundColor", "(I)V", lambda a: (_vt_bn("setBackgroundColor", "(I)V")(a) if _vt_bn("setBackgroundColor", "(I)V") else None))
    _add(V, "setBackgroundResource", "(I)V", lambda a: (_vt_bn("setBackgroundResource", "(I)V")(a) if _vt_bn("setBackgroundResource", "(I)V") else None))
    _add(V, "setBackgroundTintList", "(Landroid/content/res/ColorStateList;)V",
         lambda a: a[0].fields.__setitem__("_bg_tint_csl", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getBackgroundTintList", "()Landroid/content/res/ColorStateList;",
         lambda a: a[0].fields.get("_bg_tint_csl") if a and isinstance(a[0], JObject) else None)
    _add(V, "setBackgroundTintMode", "(Landroid/graphics/PorterDuff$Mode;)V",
         lambda a: a[0].fields.__setitem__("_bg_tint_mode", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getBackgroundTintMode", "()Landroid/graphics/PorterDuff$Mode;",
         lambda a: a[0].fields.get("_bg_tint_mode") if a and isinstance(a[0], JObject) else None)

    _add(V, "setTag", "(Ljava/lang/Object;)V", lambda a: a[0].fields.__setitem__("_tag", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getTag", "()Ljava/lang/Object;", lambda a: a[0].fields.get("_tag") if a and isinstance(a[0], JObject) else None)
    _add(V, "setTag", "(ILjava/lang/Object;)V", lambda a: a[0].fields.setdefault("_tags", {}).__setitem__(int(a[1] or 0), a[2]) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(V, "getTag", "(I)Ljava/lang/Object;", lambda a: a[0].fields.get("_tags", {}).get(int(a[1] or 0)) if len(a) > 1 and isinstance(a[0], JObject) else None)

    for _vm_nm, _vm_sig, _vm_ret in (
        ("setWillNotDraw", "(Z)V", None),
        ("willNotDraw", "()Z", 0),
        ("setAutoHandwritingEnabled", "(Z)V", None),
        ("isAutoHandwritingEnabled", "()Z", 0),
        ("setLayerType", "(ILandroid/graphics/Paint;)V", None),
        ("getLayerType", "()I", 0),
        ("setOutlineProvider", "(Landroid/view/ViewOutlineProvider;)V", None),
        ("setClipToOutline", "(Z)V", None),
        ("getClipToOutline", "()Z", 0),
        ("setFitsSystemWindows", "(Z)V", None),
        ("getFitsSystemWindows", "()Z", 0),
        ("setOverScrollMode", "(I)V", None),
        ("getOverScrollMode", "()I", 0),
        ("setNestedScrollingEnabled", "(Z)V", None),
        ("isNestedScrollingEnabled", "()Z", 1),
        ("stopNestedScroll", "()V", None),
        ("setScrollIndicators", "(II)V", None),
        ("setSaveFromParentEnabled", "(Z)V", None),
        ("saveHierarchyState", "(Landroid/util/SparseArray;)V", None),
        ("restoreHierarchyState", "(Landroid/util/SparseArray;)V", None),
        ("saveAttributeDataForStyleable", "(Landroid/content/Context;[ILandroid/util/AttributeSet;Landroid/content/res/TypedArray;II)V", None),
        ("setTransitionName", "(Ljava/lang/String;)V", None),
        ("getTransitionName", "()Ljava/lang/String;", None),
        ("setTooltipText", "(Ljava/lang/CharSequence;)V", None),
        ("setPointerIcon", "(Landroid/view/PointerIcon;)V", None),
        ("setFrameContentVelocity", "(F)V", None),
        ("startAnimation", "(Landroid/view/animation/Animation;)V", None),
        ("clearAnimation", "()V", None),
        ("drawableHotspotChanged", "(FF)V", None),
        ("jumpDrawablesToCurrentState", "()V", None),
        ("drawableStateChanged", "()V", None),
        ("hasOverlappingRendering", "()Z", 1),
        ("hasTransientState", "()Z", 0),
        ("setHasTransientState", "(Z)V", None),
        ("isOpaque", "()Z", 0),
        ("setWindowInsetsAnimationCallback", "(Landroid/view/WindowInsetsAnimation$Callback;)V", None),
        ("requestApplyInsets", "()V", None),
        ("getReceiveContentMimeTypes", "()[Ljava/lang/String;", None),
    ):
        _add(V, _vm_nm, _vm_sig, (lambda rv: (lambda a: rv))(_vm_ret))
    _add(V, "getDrawableState", "()[I", lambda a: JArray("[I", []))
    _add(V, "performReceiveContent", "(Landroid/view/ContentInfo;)Landroid/view/ContentInfo;", lambda a: a[1] if len(a) > 1 else None)
    _add(V, "findOnBackInvokedDispatcher", "()Landroid/window/OnBackInvokedDispatcher;",
         lambda a: JObject("Landroid/window/OnBackInvokedDispatcher;", {}))
    _add(V, "getWindowInsetsController", "()Landroid/view/WindowInsetsController;",
         lambda a: JObject("Landroid/view/WindowInsetsController;", {}))

    # WindowInsets on View
    def _mk_insets_obj(l=0, t=0, r=0, b=0):
        return JObject("Landroid/view/WindowInsets;", {
            "_left": int(l), "_top": int(t), "_right": int(r), "_bottom": int(b),
            "_consumed": 0, "_round": 0,
        })
    _add(V, "getRootWindowInsets", "()Landroid/view/WindowInsets;", lambda a: _mk_insets_obj())
    _add(V, "onApplyWindowInsets", "(Landroid/view/WindowInsets;)Landroid/view/WindowInsets;",
         lambda a: a[1] if len(a) > 1 else _mk_insets_obj())
    _add(V, "dispatchApplyWindowInsets", "(Landroid/view/WindowInsets;)Landroid/view/WindowInsets;",
         lambda a: a[1] if len(a) > 1 else _mk_insets_obj())
    _add(V, "computeSystemWindowInsets", "(Landroid/view/WindowInsets;Landroid/graphics/Rect;)Landroid/view/WindowInsets;",
         lambda a: a[1] if len(a) > 1 else _mk_insets_obj())

    # Accessibility on View
    _add(V, "setAccessibilityDelegate", "(Landroid/view/View$AccessibilityDelegate;)V",
         lambda a: a[0].fields.__setitem__("_a11y_del", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(V, "getAccessibilityDelegate", "()Landroid/view/View$AccessibilityDelegate;",
         lambda a: a[0].fields.get("_a11y_del") if a and isinstance(a[0], JObject) else None)
    _add(V, "createAccessibilityNodeInfo", "()Landroid/view/accessibility/AccessibilityNodeInfo;",
         lambda a: JObject("Landroid/view/accessibility/AccessibilityNodeInfo;", {"_source": a[0] if a else None}))
    for _va_nm, _va_sig, _va_ret in (
        ("onInitializeAccessibilityEvent", "(Landroid/view/accessibility/AccessibilityEvent;)V", None),
        ("onInitializeAccessibilityNodeInfo", "(Landroid/view/accessibility/AccessibilityNodeInfo;)V", None),
        ("onPopulateAccessibilityEvent", "(Landroid/view/accessibility/AccessibilityEvent;)V", None),
        ("sendAccessibilityEvent", "(I)V", None),
        ("sendAccessibilityEventUnchecked", "(Landroid/view/accessibility/AccessibilityEvent;)V", None),
        ("performAccessibilityAction", "(ILandroid/os/Bundle;)Z", 0),
        ("getAccessibilityLiveRegion", "()I", 0),
        ("setAccessibilityLiveRegion", "(I)V", None),
        ("getAccessibilityPaneTitle", "()Ljava/lang/CharSequence;", None),
        ("setAccessibilityPaneTitle", "(Ljava/lang/CharSequence;)V", None),
        ("getImportantForAccessibility", "()I", 0),
        ("setImportantForAccessibility", "(I)V", None),
        ("getImportantForAutofill", "()I", 0),
        ("setImportantForAutofill", "(I)V", None),
        ("isAccessibilityHeading", "()Z", 0),
        ("setAccessibilityHeading", "(Z)V", None),
        ("isScreenReaderFocusable", "()Z", 0),
        ("setScreenReaderFocusable", "(Z)V", None),
        ("getStateDescription", "()Ljava/lang/CharSequence;", None),
        ("setStateDescription", "(Ljava/lang/CharSequence;)V", None),
        ("setLabelFor", "(I)V", None),
    ):
        _add(V, _va_nm, _va_sig, (lambda rv: (lambda a: rv))(_va_ret))

    def _v_set_desc(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_content_desc"] = _txt(a[1])
        bn = _vt_bn("setContentDescription", "(Ljava/lang/CharSequence;)V")
        if bn is not None:
            return bn(a)
        vn = _vn(a[0]) if a else None
        if vn is not None and len(a) > 1:
            vn.props["desc"] = _txt(a[1])
            _touch_vt()
        return None
    _add(V, "setContentDescription", "(Ljava/lang/CharSequence;)V", _v_set_desc)
    _add(V, "getContentDescription", "()Ljava/lang/CharSequence;",
         lambda a: JString(str((_vn(a[0]).props.get("desc") if _vn(a[0]) is not None and "desc" in _vn(a[0]).props else a[0].fields.get("_content_desc", "")) or "")) if a and isinstance(a[0], JObject) else None)

    def _v_get_lp(a):
        bn = _vt_bn("getLayoutParams", "()Landroid/view/ViewGroup$LayoutParams;")
        if bn is not None:
            return bn(a)
        return a[0].fields.setdefault("_lp", JObject("Landroid/view/ViewGroup$LayoutParams;", {"width": -2, "height": -2})) if a and isinstance(a[0], JObject) else JObject("Landroid/view/ViewGroup$LayoutParams;", {})

    def _v_set_lp(a):
        bn = _vt_bn("setLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)V")
        if bn is not None:
            return bn(a)
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_lp"] = a[1]
        return None
    _add(V, "getLayoutParams", "()Landroid/view/ViewGroup$LayoutParams;", _v_get_lp)
    _add(V, "setLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)V", _v_set_lp)

    # =========================================================================
    # 2. ViewGroup / ViewParent / ViewManager / LayoutParams / ViewTreeObserver
    # =========================================================================
    VG = "Landroid/view/ViewGroup;"
    VP = "Landroid/view/ViewParent;"
    VMG = "Landroid/view/ViewManager;"
    LP = "Landroid/view/ViewGroup$LayoutParams;"
    MLP = "Landroid/view/ViewGroup$MarginLayoutParams;"
    VTO = "Landroid/view/ViewTreeObserver;"

    for _vgsig in (
        "()V",
        "(Landroid/content/Context;)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;I)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;II)V",
    ):
        _add(VG, "<init>", _vgsig, _v_init)

    def _vg_add(a):
        if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            return None
        bn = _vt_bn("addView", "(Landroid/view/View;)V")
        if bn is not None:
            bn([a[0], a[1]])
        ch = a[0].fields.setdefault("_children", [])
        if a[1] not in ch:
            idx = int(a[2]) if len(a) > 2 and isinstance(a[2], int) and not isinstance(a[2], bool) and len(a) <= 4 else -1
            if len(a) == 4 and isinstance(a[2], int):
                idx = int(a[2])
            if 0 <= idx <= len(ch):
                ch.insert(idx, a[1])
            else:
                ch.append(a[1])
            a[1].fields["_parent"] = a[0]
        return None

    for _avsig in (
        "(Landroid/view/View;)V",
        "(Landroid/view/View;I)V",
        "(Landroid/view/View;II)V",
        "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V",
        "(Landroid/view/View;ILandroid/view/ViewGroup$LayoutParams;)V",
    ):
        _add(VG, "addView", _avsig, _vg_add)

    def _vg_count(a):
        if a and isinstance(a[0], JObject):
            vn = _vn(a[0])
            if vn is not None:
                return len(vn.children)
            return len(a[0].fields.get("_children", ()))
        return 0
    _add(VG, "getChildCount", "()I", _vg_count)

    def _vg_child_at(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            idx = int(a[1])
            vn = _vn(a[0])
            if vn is not None:
                return _wrap_vn(vn.children[idx]) if 0 <= idx < len(vn.children) else None
            ch = a[0].fields.get("_children", ())
            return ch[idx] if 0 <= idx < len(ch) else None
        return None
    _add(VG, "getChildAt", "(I)Landroid/view/View;", _vg_child_at)

    def _vg_index_of(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            vn = _vn(a[0])
            cvn = _vn(a[1])
            if vn is not None and cvn is not None:
                try:
                    return vn.children.index(cvn)
                except ValueError:
                    return -1
            ch = a[0].fields.get("_children", ())
            try:
                return ch.index(a[1])
            except ValueError:
                return -1
        return -1
    _add(VG, "indexOfChild", "(Landroid/view/View;)I", _vg_index_of)

    def _vg_remove_view(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            vn = _vn(a[0])
            cvn = _vn(a[1])
            if vn is not None and cvn is not None and cvn in vn.children:
                vn.children.remove(cvn)
                cvn._parent = None
                _touch_vt()
            ch = a[0].fields.get("_children", [])
            if a[1] in ch:
                ch.remove(a[1])
                if isinstance(a[1], JObject):
                    a[1].fields["_parent"] = None
        return None
    _add(VG, "removeView", "(Landroid/view/View;)V", _vg_remove_view)
    _add(VG, "removeViewInLayout", "(Landroid/view/View;)V", _vg_remove_view)
    _add(VG, "removeDetachedView", "(Landroid/view/View;Z)V", _vg_remove_view)

    def _vg_remove_at(a, count: int = 1):
        if len(a) > 1 and isinstance(a[0], JObject):
            start = int(a[1])
            cnt = int(a[2]) if len(a) > 2 else count
            vn = _vn(a[0])
            if vn is not None and 0 <= start < len(vn.children):
                for c in vn.children[start:start + max(0, cnt)]:
                    c._parent = None
                del vn.children[start:start + max(0, cnt)]
                _touch_vt()
            ch = a[0].fields.get("_children", [])
            if 0 <= start < len(ch):
                for c in ch[start:start + max(0, cnt)]:
                    if isinstance(c, JObject):
                        c.fields["_parent"] = None
                del ch[start:start + max(0, cnt)]
        return None
    _add(VG, "removeViewAt", "(I)V", lambda a: _vg_remove_at(a, 1))
    _add(VG, "removeViews", "(II)V", lambda a: _vg_remove_at(a, 1))

    def _vg_remove_all(a):
        if a and isinstance(a[0], JObject):
            vn = _vn(a[0])
            if vn is not None:
                for c in vn.children:
                    c._parent = None
                vn.children.clear()
                _touch_vt()
            ch = a[0].fields.get("_children", [])
            for c in ch:
                if isinstance(c, JObject):
                    c.fields["_parent"] = None
            ch.clear()
        return None
    _add(VG, "removeAllViews", "()V", _vg_remove_all)
    _add(VG, "removeAllViewsInLayout", "()V", _vg_remove_all)

    _add(VG, "setClipToPadding", "(Z)V", lambda a: a[0].fields.__setitem__("_clip_pad", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None)
    _add(VG, "getClipToPadding", "()Z", lambda a: int(a[0].fields.get("_clip_pad", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(VG, "setClipChildren", "(Z)V", lambda a: a[0].fields.__setitem__("_clip_ch", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None)
    _add(VG, "getClipChildren", "()Z", lambda a: int(a[0].fields.get("_clip_ch", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(VG, "bringChildToFront", "(Landroid/view/View;)V", lambda a: None)
    _add(VG, "requestDisallowInterceptTouchEvent", "(Z)V", lambda a: None)
    _add(VG, "requestChildFocus", "(Landroid/view/View;Landroid/view/View;)V", lambda a: None)
    _add(VG, "clearChildFocus", "(Landroid/view/View;)V", lambda a: None)
    _add(VG, "getFocusedChild", "()Landroid/view/View;", lambda a: None)
    _add(VG, "focusSearch", "(Landroid/view/View;I)Landroid/view/View;", lambda a: None)
    _add(VG, "requestChildRectangleOnScreen", "(Landroid/view/View;Landroid/graphics/Rect;Z)Z", lambda a: 0)
    _add(VG, "measureChildren", "(II)V", lambda a: None)
    _add(VG, "measureChild", "(Landroid/view/View;II)V", lambda a: _v_measure([a[1], a[2], a[3]]) if len(a) > 3 else None)
    _add(VG, "measureChildWithMargins", "(Landroid/view/View;IIII)V", lambda a: _v_measure([a[1], a[2], a[4]]) if len(a) > 4 else None)
    _add(VG, "getChildMeasureSpec", "(III)I",
         lambda a: (lambda spec, pad, cdim: (cdim & 0x3FFFFFFF) | 0x40000000 if cdim >= 0 else max(0, (int(spec) & 0x3FFFFFFF) - int(pad)) | (int(spec) & 0xC0000000))(a[0], a[1], a[2]) if len(a) >= 3 else 0)
    _add(VG, "dispatchDraw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add(VG, "drawChild", "(Landroid/graphics/Canvas;Landroid/view/View;J)Z", lambda a: 0)
    _add(VG, "dispatchKeyEvent", "(Landroid/view/KeyEvent;)Z", lambda a: 0)
    _add(VG, "onInterceptTouchEvent", "(Landroid/view/MotionEvent;)Z", lambda a: 0)
    _add(VG, "onRequestFocusInDescendants", "(ILandroid/graphics/Rect;)Z", lambda a: 0)
    _add(VG, "drawableStateChanged", "()V", lambda a: None)
    _add(VG, "onAttachedToWindow", "()V", lambda a: a[0].fields.__setitem__("_attached", 1) if a and isinstance(a[0], JObject) else None)
    _add(VG, "onDetachedFromWindow", "()V", lambda a: a[0].fields.__setitem__("_attached", 0) if a and isinstance(a[0], JObject) else None)
    _add(VG, "addFocusables", "(Ljava/util/ArrayList;II)V", lambda a: None)
    _add(VG, "checkLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)Z", lambda a: 1)
    _add(VG, "getChildDrawingOrder", "(II)I", lambda a: int(a[2]) if len(a) > 2 else 0)
    _add(VG, "getOverlay", "()Landroid/view/ViewGroupOverlay;", lambda a: JObject("Landroid/view/ViewGroupOverlay;", {}))
    _add(VG, "isTransitionGroup", "()Z", lambda a: 0)
    _add(VG, "setTransitionGroup", "(Z)V", lambda a: None)
    _add(VG, "setMotionEventSplittingEnabled", "(Z)V", lambda a: None)
    _add(VG, "setOnHierarchyChangeListener", "(Landroid/view/ViewGroup$OnHierarchyChangeListener;)V", lambda a: None)
    _add(VG, "suppressLayout", "(Z)V", lambda a: None)
    _add(VG, "startViewTransition", "(Landroid/view/View;)V", lambda a: None)
    _add(VG, "endViewTransition", "(Landroid/view/View;)V", lambda a: None)

    _add("Landroid/view/ViewOverlay;", "add", "(Landroid/graphics/drawable/Drawable;)V", lambda a: None)
    _add("Landroid/view/ViewGroupOverlay;", "add", "(Landroid/view/View;)V", lambda a: None)
    _add("Landroid/view/ViewGroupOverlay;", "remove", "(Landroid/view/View;)V", lambda a: None)

    # ViewParent & ViewManager
    _add(VP, "getParent", "()Landroid/view/ViewParent;", _v_get_parent)
    _add(VP, "requestLayout", "()V", lambda a: None)
    _add(VP, "isLayoutRequested", "()Z", lambda a: 0)
    _add(VP, "requestDisallowInterceptTouchEvent", "(Z)V", lambda a: None)
    _add(VP, "requestChildFocus", "(Landroid/view/View;Landroid/view/View;)V", lambda a: None)
    _add(VP, "clearChildFocus", "(Landroid/view/View;)V", lambda a: None)
    _add(VP, "bringChildToFront", "(Landroid/view/View;)V", lambda a: None)
    _add(VP, "notifySubtreeAccessibilityStateChanged", "(Landroid/view/View;Landroid/view/View;I)V", lambda a: None)
    _add(VP, "onStartNestedScroll", "(Landroid/view/View;Landroid/view/View;I)Z", lambda a: 0)
    _add(VP, "onNestedScrollAccepted", "(Landroid/view/View;Landroid/view/View;I)V", lambda a: None)
    _add(VP, "onStopNestedScroll", "(Landroid/view/View;)V", lambda a: None)
    _add(VP, "onNestedScroll", "(Landroid/view/View;IIII)V", lambda a: None)
    _add(VP, "onNestedPreScroll", "(Landroid/view/View;II[I)V", lambda a: None)
    _add(VP, "onNestedFling", "(Landroid/view/View;FFZ)Z", lambda a: 0)
    _add(VP, "onNestedPreFling", "(Landroid/view/View;FF)Z", lambda a: 0)
    _add(VP, "requestSendAccessibilityEvent", "(Landroid/view/View;Landroid/view/accessibility/AccessibilityEvent;)Z", lambda a: 0)
    _add(VMG, "addView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", _vg_add)
    _add(VMG, "updateViewLayout", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", lambda a: None)
    _add(VMG, "removeView", "(Landroid/view/View;)V", _vg_remove_view)

    # LayoutParams / MarginLayoutParams
    def _lp_init(a):
        if a and isinstance(a[0], JObject):
            if len(a) >= 3 and isinstance(a[1], int) and isinstance(a[2], int):
                a[0].fields["width"] = int(a[1])
                a[0].fields["height"] = int(a[2])
            elif len(a) >= 2 and isinstance(a[1], JObject):
                a[0].fields.update({k: v for k, v in a[1].fields.items() if k in ("width", "height", "leftMargin", "topMargin", "rightMargin", "bottomMargin", "_m_start", "_m_end")})
            a[0].fields.setdefault("width", -2)
            a[0].fields.setdefault("height", -2)
        return a[0] if a else None

    for _lpc in (LP, MLP, "Landroid/widget/FrameLayout$LayoutParams;", "Landroid/widget/LinearLayout$LayoutParams;"):
        for _lpsig in (
            "()V", "(II)V", "(III)V", "(IIF)V",
            "(Landroid/content/Context;Landroid/util/AttributeSet;)V",
            "(Landroid/view/ViewGroup$LayoutParams;)V",
            "(Landroid/view/ViewGroup$MarginLayoutParams;)V",
        ):
            _add(_lpc, "<init>", _lpsig, _lp_init)
    _add(LP, "resolveLayoutDirection", "(I)V", lambda a: a[0].fields.__setitem__("_dir", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MLP, "setMargins", "(IIII)V", lambda a: a[0].fields.update({"leftMargin": int(a[1]), "topMargin": int(a[2]), "rightMargin": int(a[3]), "bottomMargin": int(a[4])}) if len(a) > 4 and isinstance(a[0], JObject) else None)
    _add(MLP, "setMarginStart", "(I)V", lambda a: a[0].fields.__setitem__("_m_start", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MLP, "setMarginEnd", "(I)V", lambda a: a[0].fields.__setitem__("_m_end", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MLP, "getMarginStart", "()I", lambda a: int(a[0].fields.get("_m_start", a[0].fields.get("leftMargin", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(MLP, "getMarginEnd", "()I", lambda a: int(a[0].fields.get("_m_end", a[0].fields.get("rightMargin", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(MLP, "getLayoutDirection", "()I", lambda a: int(a[0].fields.get("_dir", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(MLP, "resolveLayoutDirection", "(I)V", lambda a: a[0].fields.__setitem__("_dir", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    # ViewTreeObserver & ViewStub & TextureView & View$BaseSavedState & ViewStructure
    _add(VTO, "isAlive", "()Z", lambda a: 1)
    for _vtom, _vtos in (
        ("addOnPreDrawListener", "(Landroid/view/ViewTreeObserver$OnPreDrawListener;)V"),
        ("removeOnPreDrawListener", "(Landroid/view/ViewTreeObserver$OnPreDrawListener;)V"),
        ("addOnGlobalLayoutListener", "(Landroid/view/ViewTreeObserver$OnGlobalLayoutListener;)V"),
        ("removeOnGlobalLayoutListener", "(Landroid/view/ViewTreeObserver$OnGlobalLayoutListener;)V"),
        ("removeGlobalOnLayoutListener", "(Landroid/view/ViewTreeObserver$OnGlobalLayoutListener;)V"),
        ("addOnDrawListener", "(Landroid/view/ViewTreeObserver$OnDrawListener;)V"),
        ("removeOnDrawListener", "(Landroid/view/ViewTreeObserver$OnDrawListener;)V"),
        ("addOnScrollChangedListener", "(Landroid/view/ViewTreeObserver$OnScrollChangedListener;)V"),
        ("removeOnScrollChangedListener", "(Landroid/view/ViewTreeObserver$OnScrollChangedListener;)V"),
    ):
        _add(VTO, _vtom, _vtos, lambda a: None)
    _add("Landroid/view/ViewStub;", "inflate", "()Landroid/view/View;", lambda a: JObject(V, {}))
    _add("Landroid/view/TextureView;", "setSurfaceTextureListener", "(Landroid/view/TextureView$SurfaceTextureListener;)V", lambda a: None)
    _add("Landroid/view/Surface;", "<init>", "(Landroid/graphics/SurfaceTexture;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/View$BaseSavedState;", "<init>", "(Landroid/os/Parcel;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/View$BaseSavedState;", "<init>", "(Landroid/os/Parcelable;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/View$BaseSavedState;", "writeToParcel", "(Landroid/os/Parcel;I)V", lambda a: None)
    _add("Landroid/view/ViewStructure;", "newChild", "(I)Landroid/view/ViewStructure;", lambda a: JObject("Landroid/view/ViewStructure;", {}))

    # =========================================================================
    # 3. Widgets (TextView, EditText, CompoundButton, ImageView, ProgressBar, Spinner, ListView...)
    # =========================================================================
    TV = "Landroid/widget/TextView;"
    ET = "Landroid/widget/EditText;"
    CB = "Landroid/widget/CompoundButton;"
    IV = "Landroid/widget/ImageView;"
    PB = "Landroid/widget/ProgressBar;"
    SP = "Landroid/widget/Spinner;"
    LV = "Landroid/widget/ListView;"
    AV = "Landroid/widget/AdapterView;"

    for _wc in (TV, ET, "Landroid/widget/Button;", "Landroid/widget/AutoCompleteTextView;",
                "Landroid/widget/MultiAutoCompleteTextView;", "Landroid/widget/CheckBox;",
                "Landroid/widget/RadioButton;", "Landroid/widget/CheckedTextView;", CB, IV,
                "Landroid/widget/ImageButton;", PB, "Landroid/widget/SeekBar;",
                "Landroid/widget/RatingBar;", SP, LV, "Landroid/widget/FrameLayout;",
                "Landroid/widget/LinearLayout;"):
        for _wsig in (
            "()V",
            "(Landroid/content/Context;)V",
            "(Landroid/content/Context;Landroid/util/AttributeSet;)V",
            "(Landroid/content/Context;Landroid/util/AttributeSet;I)V",
            "(Landroid/content/Context;Landroid/util/AttributeSet;II)V",
        ):
            _add(_wc, "<init>", _wsig, _v_init)

    def _tv_get_str(obj: Any) -> str:
        if not isinstance(obj, JObject):
            return ""
        vn = _vn(obj)
        if vn is not None and getattr(vn, "text", None):
            return str(vn.text)
        return str(obj.fields.get("_text", "") or "")

    def _tv_set_text(a):
        if a and len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_text"] = _txt(a[1])
            bn = _vt_bn("setText", "(Ljava/lang/CharSequence;)V")
            if bn is not None:
                return bn(a)
            vn = _vn(a[0])
            if vn is not None:
                vn.text = _txt(a[1])
                _touch_vt()
        return None
    _add(TV, "setText", "(Ljava/lang/CharSequence;)V", _tv_set_text)
    _add(TV, "setText", "(Ljava/lang/String;)V", _tv_set_text)
    _add(TV, "setText", "(I)V", lambda a: _vt_bn("setText", "(I)V")(a) if _vt_bn("setText", "(I)V") else None)
    _add(TV, "getText", "()Ljava/lang/CharSequence;", lambda a: _vt_bn("getText", "()Ljava/lang/CharSequence;")(a) if _vt_bn("getText", "()Ljava/lang/CharSequence;") else JString(_tv_get_str(a[0] if a else None)))
    _add(TV, "getText", "()Ljava/lang/String;", lambda a: _vt_bn("getText", "()Ljava/lang/String;")(a) if _vt_bn("getText", "()Ljava/lang/String;") else JString(_tv_get_str(a[0] if a else None)))
    _add(TV, "length", "()I", lambda a: len(_tv_get_str(a[0] if a else None)))

    def _tv_text_size(obj: Any) -> float:
        if isinstance(obj, JObject):
            vn = _vn(obj)
            if vn is not None and "textSize" in vn.props:
                return float(vn.props["textSize"])
            return float(_f32(obj.fields.get("_textSize", 14.0), 14.0))
        return 14.0

    def _tv_get_paint(a):
        if a and isinstance(a[0], JObject):
            tp = a[0].fields.get("_paint")
            if not isinstance(tp, JObject):
                tp = JObject("Landroid/text/TextPaint;", {"$color": 0xFF000000, "$alpha": 255, "$textSize": _tv_text_size(a[0])})
                a[0].fields["_paint"] = tp
            return tp
        return JObject("Landroid/text/TextPaint;", {"$textSize": 14.0})
    _add(TV, "getPaint", "()Landroid/text/TextPaint;", _tv_get_paint)

    def _tv_get_layout(a):
        obj = a[0] if a and isinstance(a[0], JObject) else None
        txt = _tv_get_str(obj)
        tp = _tv_get_paint(a)
        w = max(100, _view_bounds(obj)[2] or 380)
        info = _compute_text_layout(txt, tp, w)
        return JObject("Landroid/text/StaticLayout;", info)
    _add(TV, "getLayout", "()Landroid/text/Layout;", _tv_get_layout)
    _add(TV, "getLineCount", "()I", lambda a: (lambda s: max(1, s.count("\n") + 1) if s else 0)(_tv_get_str(a[0] if a else None)))
    _add(TV, "getLineHeight", "()I", lambda a: max(1, int(round(_tv_text_size(a[0] if a else None) * 1.2))))
    _add(TV, "getTextSize", "()F", lambda a: _tv_text_size(a[0] if a else None))
    _add(TV, "setTextSize", "(F)V", lambda a: (_vt_bn("setTextSize", "(F)V")(a) if _vt_bn("setTextSize", "(F)V") else (a[0].fields.__setitem__("_textSize", _f32(a[1], 14.0)) if len(a) > 1 and isinstance(a[0], JObject) else None)))
    _add(TV, "setTextSize", "(IF)V", lambda a: (_vt_bn("setTextSize", "(IF)V")(a) if _vt_bn("setTextSize", "(IF)V") else (a[0].fields.__setitem__("_textSize", _f32(a[2], 14.0)) if len(a) > 2 and isinstance(a[0], JObject) else None)))
    _add(TV, "getCurrentTextColor", "()I", lambda a: int((_vn(a[0]).props.get("fg") if _vn(a[0]) is not None else None) or a[0].fields.get("_textColor", 0xFF000000)) if a and isinstance(a[0], JObject) else 0xFF000000)
    _add(TV, "getTextColors", "()Landroid/content/res/ColorStateList;",
         lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": int(a[0].fields.get("_textColor", 0xFF000000)) if a and isinstance(a[0], JObject) else 0xFF000000}))
    _add(TV, "getHint", "()Ljava/lang/CharSequence;",
         lambda a: JString(str((_vn(a[0]).props.get("hint") if _vn(a[0]) is not None else None) or a[0].fields.get("_hint", ""))) if a and isinstance(a[0], JObject) else JString(""))
    _add(TV, "getSelectionStart", "()I", lambda a: int(a[0].fields.get("_sel_start", -1)) if a and isinstance(a[0], JObject) else -1)
    _add(TV, "getSelectionEnd", "()I", lambda a: int(a[0].fields.get("_sel_end", -1)) if a and isinstance(a[0], JObject) else -1)
    _add(ET, "setSelection", "(I)V", lambda a: a[0].fields.update({"_sel_start": int(a[1]), "_sel_end": int(a[1])}) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(ET, "setSelection", "(II)V", lambda a: a[0].fields.update({"_sel_start": int(a[1]), "_sel_end": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(ET, "onCheckIsTextEditor", "()Z", lambda a: 1)
    _add(TV, "onCheckIsTextEditor", "()Z", lambda a: 0)
    _add(TV, "addTextChangedListener", "(Landroid/text/TextWatcher;)V",
         lambda a: a[0].fields.setdefault("_tws", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "removeTextChangedListener", "(Landroid/text/TextWatcher;)V",
         lambda a: a[0].fields.get("_tws", []).remove(a[1]) if len(a) > 1 and isinstance(a[0], JObject) and a[1] in a[0].fields.get("_tws", []) else None)
    _add(TV, "setFilters", "([Landroid/text/InputFilter;)V", lambda a: a[0].fields.__setitem__("_filters", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "setTransformationMethod", "(Landroid/text/method/TransformationMethod;)V", lambda a: a[0].fields.__setitem__("_trans_m", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "getTransformationMethod", "()Landroid/text/method/TransformationMethod;", lambda a: a[0].fields.get("_trans_m") if a and isinstance(a[0], JObject) else None)
    _add(TV, "setMovementMethod", "(Landroid/text/method/MovementMethod;)V", lambda a: a[0].fields.__setitem__("_move_m", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "getMovementMethod", "()Landroid/text/method/MovementMethod;", lambda a: a[0].fields.get("_move_m") if a and isinstance(a[0], JObject) else None)
    _add(TV, "setTypeface", "(Landroid/graphics/Typeface;)V", lambda a: a[0].fields.__setitem__("_tf", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "setTypeface", "(Landroid/graphics/Typeface;I)V", lambda a: a[0].fields.__setitem__("_tf", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(TV, "getTypeface", "()Landroid/graphics/Typeface;", lambda a: a[0].fields.get("_tf") if a and isinstance(a[0], JObject) else None)
    _add(TV, "getCompoundDrawables", "()[Landroid/graphics/drawable/Drawable;", lambda a: JArray("[Landroid/graphics/drawable/Drawable;", [None, None, None, None]))
    _add(TV, "getCompoundDrawablesRelative", "()[Landroid/graphics/drawable/Drawable;", lambda a: JArray("[Landroid/graphics/drawable/Drawable;", [None, None, None, None]))
    _add(TV, "getTextLocale", "()Ljava/util/Locale;", lambda a: JObject("Ljava/util/Locale;", {"lang": "en", "country": "US"}))
    _add(TV, "getTextMetricsParams", "()Landroid/text/PrecomputedText$Params;",
         lambda a: JObject("Landroid/text/PrecomputedText$Params;", {"_tp": _tv_get_paint(a)}))
    _add(TV, "getAutoSizeTextAvailableSizes", "()[I", lambda a: JArray("[I", []))
    for _tvm, _tvs, _tvr in (
        ("setEllipsize", "(Landroid/text/TextUtils$TruncateAt;)V", None),
        ("setIncludeFontPadding", "(Z)V", None),
        ("getIncludeFontPadding", "()Z", 1),
        ("setLineSpacing", "(FF)V", None),
        ("getLineSpacingExtra", "()F", 0.0),
        ("getLineSpacingMultiplier", "()F", 1.0),
        ("setLineHeight", "(IF)V", None),
        ("setLineHeight", "(I)V", None),
        ("setMaxLines", "(I)V", None),
        ("getMaxLines", "()I", 2147483647),
        ("getMinLines", "()I", 1),
        ("setSingleLine", "()V", None),
        ("setSingleLine", "(Z)V", None),
        ("setAllCaps", "(Z)V", None),
        ("setTextAppearance", "(I)V", None),
        ("setTextAppearance", "(Landroid/content/Context;I)V", None),
        ("getBreakStrategy", "()I", 0),
        ("setBreakStrategy", "(I)V", None),
        ("getHyphenationFrequency", "()I", 0),
        ("setHyphenationFrequency", "(I)V", None),
        ("getAutoSizeTextType", "()I", 0),
        ("getAutoSizeMinTextSize", "()I", -1),
        ("getAutoSizeMaxTextSize", "()I", -1),
        ("getAutoSizeStepGranularity", "()I", -1),
        ("setAutoSizeTextTypeWithDefaults", "(I)V", None),
        ("setAutoSizeTextTypeUniformWithConfiguration", "(IIII)V", None),
        ("setAutoSizeTextTypeUniformWithPresetSizes", "([II)V", None),
        ("setCompoundDrawableTintList", "(Landroid/content/res/ColorStateList;)V", None),
        ("setCompoundDrawableTintMode", "(Landroid/graphics/PorterDuff$Mode;)V", None),
        ("setCompoundDrawablesRelative", "(Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;)V", None),
        ("setCompoundDrawablesRelativeWithIntrinsicBounds", "(Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;Landroid/graphics/drawable/Drawable;)V", None),
        ("setCompoundDrawablesRelativeWithIntrinsicBounds", "(IIII)V", None),
        ("getCompoundPaddingTop", "()I", 0),
        ("getCompoundPaddingBottom", "()I", 0),
        ("getTotalPaddingLeft", "()I", 0),
        ("getTotalPaddingRight", "()I", 0),
        ("getInputType", "()I", 1),
        ("setFirstBaselineToTopHeight", "(I)V", None),
        ("setLastBaselineToBottomHeight", "(I)V", None),
        ("setCustomSelectionActionModeCallback", "(Landroid/view/ActionMode$Callback;)V", None),
        ("onCreateInputConnection", "(Landroid/view/inputmethod/EditorInfo;)Landroid/view/inputmethod/InputConnection;", None),
        ("onTextChanged", "(Ljava/lang/CharSequence;III)V", None),
        ("onMeasure", "(II)V", None),
        ("onLayout", "(ZIIII)V", None),
        ("drawableStateChanged", "()V", None),
    ):
        _add(TV, _tvm, _tvs, (lambda rv: (lambda a: rv))(_tvr))

    # CompoundButton / CheckedTextView
    def _cb_set_checked(a):
        bn = _vt_bn("setChecked", "(Z)V")
        if bn is not None:
            bn(a)
        elif len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_checked"] = 1 if a[1] else 0
            vn = _vn(a[0])
            if vn is not None:
                vn.props["checked"] = bool(a[1])
                _touch_vt()
        return None

    def _cb_is_checked(a):
        bn = _vt_bn("isChecked", "()Z")
        if bn is not None and _vn(a[0] if a else None) is not None:
            return bn(a)
        if a and isinstance(a[0], JObject):
            vn = _vn(a[0])
            if vn is not None:
                return 1 if vn.props.get("checked") else 0
            return int(bool(a[0].fields.get("_checked", 0)))
        return 0
    _add(CB, "setChecked", "(Z)V", _cb_set_checked)
    _add(CB, "isChecked", "()Z", _cb_is_checked)
    _add(CB, "toggle", "()V", lambda a: _cb_set_checked([a[0], 0 if _cb_is_checked(a) else 1]) if a else None)
    _add(CB, "setOnCheckedChangeListener", "(Landroid/widget/CompoundButton$OnCheckedChangeListener;)V",
         lambda a: a[0].fields.__setitem__("_chk_listener", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(CB, "setButtonDrawable", "(Landroid/graphics/drawable/Drawable;)V",
         lambda a: a[0].fields.__setitem__("_btn_drv", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(CB, "getButtonDrawable", "()Landroid/graphics/drawable/Drawable;", lambda a: a[0].fields.get("_btn_drv") if a and isinstance(a[0], JObject) else None)
    _add(CB, "setButtonTintList", "(Landroid/content/res/ColorStateList;)V", lambda a: a[0].fields.__setitem__("_btn_tint", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(CB, "getButtonTintList", "()Landroid/content/res/ColorStateList;", lambda a: a[0].fields.get("_btn_tint") if a and isinstance(a[0], JObject) else None)
    _add(CB, "setButtonTintMode", "(Landroid/graphics/PorterDuff$Mode;)V", lambda a: a[0].fields.__setitem__("_btn_mode", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(CB, "getButtonTintMode", "()Landroid/graphics/PorterDuff$Mode;", lambda a: a[0].fields.get("_btn_mode") if a and isinstance(a[0], JObject) else None)
    _add(CB, "getCompoundPaddingLeft", "()I", lambda a: 0)
    _add(CB, "getCompoundPaddingRight", "()I", lambda a: 0)
    _add(CB, "onCreateDrawableState", "(I)[I", lambda a: JArray("[I", []))
    _add(CB, "verifyDrawable", "(Landroid/graphics/drawable/Drawable;)Z", lambda a: 1)
    _add(CB, "drawableHotspotChanged", "(FF)V", lambda a: None)
    _add(CB, "drawableStateChanged", "()V", lambda a: None)
    _add(CB, "jumpDrawablesToCurrentState", "()V", lambda a: None)
    _add(CB, "onDraw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add("Landroid/widget/CheckedTextView;", "getCheckMarkDrawable", "()Landroid/graphics/drawable/Drawable;", lambda a: None)
    _add("Landroid/widget/CheckedTextView;", "setCheckMarkTintList", "(Landroid/content/res/ColorStateList;)V", lambda a: None)
    _add("Landroid/widget/CheckedTextView;", "setCheckMarkTintMode", "(Landroid/graphics/PorterDuff$Mode;)V", lambda a: None)
    _add("Landroid/widget/CheckedTextView;", "drawableStateChanged", "()V", lambda a: None)

    # ImageView / ProgressBar / Spinner / AdapterView / ListView / Adapters
    _add(IV, "getDrawable", "()Landroid/graphics/drawable/Drawable;", lambda a: a[0].fields.get("_img_drv") if a and isinstance(a[0], JObject) else None)
    _add(IV, "setImageURI", "(Landroid/net/Uri;)V", lambda a: None)
    _add(IV, "setImageTintList", "(Landroid/content/res/ColorStateList;)V", lambda a: a[0].fields.__setitem__("_img_tint", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(IV, "getImageTintList", "()Landroid/content/res/ColorStateList;", lambda a: a[0].fields.get("_img_tint") if a and isinstance(a[0], JObject) else None)
    _add(IV, "setImageTintMode", "(Landroid/graphics/PorterDuff$Mode;)V", lambda a: a[0].fields.__setitem__("_img_tmode", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(IV, "getImageTintMode", "()Landroid/graphics/PorterDuff$Mode;", lambda a: a[0].fields.get("_img_tmode") if a and isinstance(a[0], JObject) else None)
    _add(IV, "drawableStateChanged", "()V", lambda a: None)
    _add(IV, "hasOverlappingRendering", "()Z", lambda a: 1)
    _add(IV, "onMeasure", "(II)V", _v_measure)

    _add(PB, "setMax", "(I)V", lambda a: a[0].fields.__setitem__("_max", max(1, int(a[1]))) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PB, "getMax", "()I", lambda a: int(a[0].fields.get("_max", 100)) if a and isinstance(a[0], JObject) else 100)
    def _pb_set_prog(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            try:
                a[0].fields["_progress"] = int(a[1])
            except Exception:
                pass
        bn = _vt_bn("setProgress", "(I)V")
        if bn is not None:
            return bn(a)
        vn = _vn(a[0]) if a else None
        if vn is not None and len(a) > 1:
            try:
                vn.props["progress"] = int(a[1])
                _touch_vt()
            except Exception:
                pass
        return None
    _add(PB, "setProgress", "(I)V", _pb_set_prog)
    _add(PB, "setProgress", "(IZ)V", _pb_set_prog)
    _add(PB, "getProgress", "()I", lambda a: int((_vn(a[0]).props.get("progress") if _vn(a[0]) is not None and "progress" in _vn(a[0]).props else a[0].fields.get("_progress", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(PB, "setProgressDrawable", "(Landroid/graphics/drawable/Drawable;)V", lambda a: a[0].fields.__setitem__("_prog_drv", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PB, "setIndeterminateDrawable", "(Landroid/graphics/drawable/Drawable;)V", lambda a: a[0].fields.__setitem__("_indet_drv", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PB, "onAttachedToWindow", "()V", lambda a: None)
    _add(PB, "onDetachedFromWindow", "()V", lambda a: None)
    _add("Landroid/widget/RatingBar;", "onMeasure", "(II)V", _v_measure)

    _add(AV, "getAdapter", "()Landroid/widget/Adapter;", lambda a: a[0].fields.get("_adapter") if a and isinstance(a[0], JObject) else None)
    _add(AV, "setOnItemClickListener", "(Landroid/widget/AdapterView$OnItemClickListener;)V", lambda a: a[0].fields.__setitem__("_item_clk", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(AV, "getSelectedItemPosition", "()I", lambda a: int(a[0].fields.get("_sel_pos", 0)) if a and isinstance(a[0], JObject) else 0)

    _add(SP, "setAdapter", "(Landroid/widget/SpinnerAdapter;)V", lambda a: a[0].fields.__setitem__("_adapter", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getAdapter", "()Landroid/widget/SpinnerAdapter;", lambda a: a[0].fields.get("_adapter") if a and isinstance(a[0], JObject) else None)
    _add(SP, "setPrompt", "(Ljava/lang/CharSequence;)V", lambda a: a[0].fields.__setitem__("_prompt", _txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getPrompt", "()Ljava/lang/CharSequence;", lambda a: JString(a[0].fields["_prompt"]) if a and isinstance(a[0], JObject) and "_prompt" in a[0].fields else None)
    _add(SP, "setPopupBackgroundDrawable", "(Landroid/graphics/drawable/Drawable;)V", lambda a: a[0].fields.__setitem__("_popup_bg", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getPopupBackground", "()Landroid/graphics/drawable/Drawable;", lambda a: a[0].fields.get("_popup_bg") if a and isinstance(a[0], JObject) else None)
    _add(SP, "setDropDownWidth", "(I)V", lambda a: a[0].fields.__setitem__("_dd_w", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getDropDownWidth", "()I", lambda a: int(a[0].fields.get("_dd_w", -2)) if a and isinstance(a[0], JObject) else -2)
    _add(SP, "setDropDownHorizontalOffset", "(I)V", lambda a: a[0].fields.__setitem__("_dd_hx", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getDropDownHorizontalOffset", "()I", lambda a: int(a[0].fields.get("_dd_hx", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SP, "setDropDownVerticalOffset", "(I)V", lambda a: a[0].fields.__setitem__("_dd_vy", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SP, "getDropDownVerticalOffset", "()I", lambda a: int(a[0].fields.get("_dd_vy", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SP, "onMeasure", "(II)V", _v_measure)
    _add(SP, "onDetachedFromWindow", "()V", lambda a: None)
    _add(SP, "onTouchEvent", "(Landroid/view/MotionEvent;)Z", lambda a: 0)
    _add(SP, "performClick", "()Z", lambda a: 1)

    _add(LV, "setAdapter", "(Landroid/widget/ListAdapter;)V", lambda a: a[0].fields.__setitem__("_adapter", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LV, "getAdapter", "()Landroid/widget/ListAdapter;", lambda a: a[0].fields.get("_adapter") if a and isinstance(a[0], JObject) else None)
    _add(LV, "addHeaderView", "(Landroid/view/View;Ljava/lang/Object;Z)V", lambda a: a[0].fields.setdefault("_headers", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LV, "setSelection", "(I)V", lambda a: a[0].fields.__setitem__("_sel_pos", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LV, "dispatchDraw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add(LV, "onDetachedFromWindow", "()V", lambda a: None)

    _add("Landroid/widget/FrameLayout;", "onMeasure", "(II)V", _v_measure)
    _add("Landroid/widget/FrameLayout;", "onLayout", "(ZIIII)V", lambda a: None)
    _add("Landroid/widget/LinearLayout;", "onMeasure", "(II)V", _v_measure)

    _add("Landroid/widget/ArrayAdapter;", "<init>", "(Landroid/content/Context;I[Ljava/lang/Object;)V",
         lambda a: a[0].fields.__setitem__("_items", list(getattr(a[3], "data", ()) or ())) if len(a) > 3 and isinstance(a[0], JObject) else None)
    _add("Landroid/widget/ArrayAdapter;", "<init>", "(Landroid/content/Context;II[Ljava/lang/Object;)V",
         lambda a: a[0].fields.__setitem__("_items", list(getattr(a[4], "data", ()) or ())) if len(a) > 4 and isinstance(a[0], JObject) else None)
    _add("Landroid/widget/ArrayAdapter;", "getView", "(ILandroid/view/View;Landroid/view/ViewGroup;)Landroid/view/View;",
         lambda a: a[2] if len(a) > 2 and isinstance(a[2], JObject) else JObject(TV, {}))
    _add("Landroid/widget/ArrayAdapter;", "setDropDownViewResource", "(I)V", lambda a: None)
    _add("Landroid/widget/BaseAdapter;", "notifyDataSetChanged", "()V", lambda a: None)
    _add("Landroid/widget/ListAdapter;", "areAllItemsEnabled", "()Z", lambda a: 1)
    _add("Landroid/widget/ListAdapter;", "isEnabled", "(I)Z", lambda a: 1)
    _add("Landroid/widget/SpinnerAdapter;", "getDropDownView", "(ILandroid/view/View;Landroid/view/ViewGroup;)Landroid/view/View;",
         lambda a: a[2] if len(a) > 2 and isinstance(a[2], JObject) else JObject(TV, {}))
    _add("Landroid/widget/ThemedSpinnerAdapter;", "getDropDownViewTheme", "()Landroid/content/res/Resources$Theme;", lambda a: None)
    _add("Landroid/widget/ThemedSpinnerAdapter;", "setDropDownViewTheme", "(Landroid/content/res/Resources$Theme;)V", lambda a: None)
    _add("Landroid/widget/HeaderViewListAdapter;", "getHeadersCount", "()I", lambda a: 0)
    _add("Landroid/widget/HeaderViewListAdapter;", "getWrappedAdapter", "()Landroid/widget/ListAdapter;",
         lambda a: a[0].fields.get("_wrapped") if a and isinstance(a[0], JObject) else None)

    # =========================================================================
    # 4. Input, Insets, Scrolling, Popup, Menus, Window, Dialog & Animators
    # =========================================================================
    ME = "Landroid/view/MotionEvent;"
    _add(ME, "obtain", "(JJIFFI)Landroid/view/MotionEvent;",
         lambda a: JObject(ME, {"_down": int(a[0]), "_ev": int(a[1]), "_act": int(a[2]), "_x": _f32(a[3]), "_y": _f32(a[4]), "_meta": int(a[5])}) if len(a) >= 6 else JObject(ME, {}))
    _add(ME, "obtain", "(Landroid/view/MotionEvent;)Landroid/view/MotionEvent;",
         lambda a: JObject(ME, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(ME, {}))
    _add(ME, "obtainNoHistory", "(Landroid/view/MotionEvent;)Landroid/view/MotionEvent;",
         lambda a: JObject(ME, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(ME, {}))
    _add(ME, "recycle", "()V", lambda a: None)
    _add(ME, "getAction", "()I", lambda a: int(a[0].fields.get("_act", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(ME, "setAction", "(I)V", lambda a: a[0].fields.__setitem__("_act", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(ME, "getActionMasked", "()I", lambda a: int(a[0].fields.get("_act", 0)) & 0xFF if a and isinstance(a[0], JObject) else 0)
    _add(ME, "getActionIndex", "()I", lambda a: (int(a[0].fields.get("_act", 0)) >> 8) & 0xFF if a and isinstance(a[0], JObject) else 0)
    _add(ME, "getPointerCount", "()I", lambda a: 1)
    _add(ME, "getPointerId", "(I)I", lambda a: 0)
    _add(ME, "findPointerIndex", "(I)I", lambda a: 0 if (len(a) > 1 and int(a[1]) == 0) else -1)
    _add(ME, "getX", "()F", lambda a: _f32(a[0].fields.get("_x", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getX", "(I)F", lambda a: _f32(a[0].fields.get("_x", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getY", "()F", lambda a: _f32(a[0].fields.get("_y", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getY", "(I)F", lambda a: _f32(a[0].fields.get("_y", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getRawX", "()F", lambda a: _f32(a[0].fields.get("_x", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getRawY", "()F", lambda a: _f32(a[0].fields.get("_y", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(ME, "getAxisValue", "(I)F", lambda a: 0.0)
    _add(ME, "offsetLocation", "(FF)V",
         lambda a: a[0].fields.update({"_x": _f32(a[0].fields.get("_x", 0.0)) + _f32(a[1]), "_y": _f32(a[0].fields.get("_y", 0.0)) + _f32(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(ME, "setLocation", "(FF)V",
         lambda a: a[0].fields.update({"_x": _f32(a[1]), "_y": _f32(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(ME, "getEventTime", "()J", lambda a: int(a[0].fields.get("_ev", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(ME, "getDownTime", "()J", lambda a: int(a[0].fields.get("_down", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(ME, "getSource", "()I", lambda a: int(a[0].fields.get("_src", 0x1002)) if a and isinstance(a[0], JObject) else 0x1002)
    _add(ME, "getDeviceId", "()I", lambda a: 0)
    _add(ME, "getButtonState", "()I", lambda a: 0)
    _add(ME, "getEdgeFlags", "()I", lambda a: 0)
    _add(ME, "getToolType", "(I)I", lambda a: 1)

    KE = "Landroid/view/KeyEvent;"
    _add(KE, "getAction", "()I", lambda a: int(a[0].fields.get("_act", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getKeyCode", "()I", lambda a: int(a[0].fields.get("_code", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getFlags", "()I", lambda a: int(a[0].fields.get("_flags", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getMetaState", "()I", lambda a: int(a[0].fields.get("_meta", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getRepeatCount", "()I", lambda a: int(a[0].fields.get("_rep", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getModifiers", "()I", lambda a: int(a[0].fields.get("_meta", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(KE, "getDeviceId", "()I", lambda a: 0)
    for _kem in ("isAltPressed", "isShiftPressed", "isCtrlPressed", "isCanceled", "isTracking", "isSystem"):
        _add(KE, _kem, "()Z", lambda a: 0)
    _add(KE, "isModifierKey", "(I)Z", lambda a: 0)
    _add(KE, "metaStateHasNoModifiers", "(I)Z", lambda a: 1 if (not a or int(a[0] or 0) == 0) else 0)
    _add(KE, "hasNoModifiers", "()Z", lambda a: 1 if (not a or not isinstance(a[0], JObject) or int(a[0].fields.get("_meta", 0)) == 0) else 0)
    _add(KE, "hasModifiers", "(I)Z", lambda a: 0)
    _add(KE, "getKeyData", "(Landroid/view/KeyCharacterMap$KeyData;)Z", lambda a: 0)
    _add(KE, "dispatch", "(Landroid/view/KeyEvent$Callback;Landroid/view/KeyEvent$DispatcherState;Ljava/lang/Object;)Z", lambda a: 0)

    KDS = "Landroid/view/KeyEvent$DispatcherState;"
    _add(KDS, "<init>", "()V", lambda a: a[0] if a else None)
    _add(KDS, "isTracking", "(Landroid/view/KeyEvent;)Z", lambda a: 0)
    _add(KDS, "startTracking", "(Landroid/view/KeyEvent;Ljava/lang/Object;)V", lambda a: None)
    _add(KDS, "handleUpEvent", "(Landroid/view/KeyEvent;)V", lambda a: None)
    _add("Landroid/view/KeyCharacterMap;", "load", "(I)Landroid/view/KeyCharacterMap;", lambda a: JObject("Landroid/view/KeyCharacterMap;", {}))
    _add("Landroid/view/KeyCharacterMap;", "getKeyboardType", "()I", lambda a: 2)
    _add("Landroid/view/InputDevice;", "getDevice", "(I)Landroid/view/InputDevice;", lambda a: JObject("Landroid/view/InputDevice;", {}))
    _add("Landroid/view/InputDevice;", "getMotionRange", "(II)Landroid/view/InputDevice$MotionRange;", lambda a: None)

    VT = "Landroid/view/VelocityTracker;"
    _add(VT, "obtain", "()Landroid/view/VelocityTracker;", lambda a: JObject(VT, {"_vx": 0.0, "_vy": 0.0}))
    _add(VT, "recycle", "()V", lambda a: None)
    _add(VT, "addMovement", "(Landroid/view/MotionEvent;)V", lambda a: None)
    _add(VT, "computeCurrentVelocity", "(I)V", lambda a: None)
    _add(VT, "computeCurrentVelocity", "(IF)V", lambda a: None)
    _add(VT, "getXVelocity", "()F", lambda a: _f32(a[0].fields.get("_vx", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(VT, "getXVelocity", "(I)F", lambda a: _f32(a[0].fields.get("_vx", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(VT, "getYVelocity", "()F", lambda a: _f32(a[0].fields.get("_vy", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(VT, "getYVelocity", "(I)F", lambda a: _f32(a[0].fields.get("_vy", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(VT, "getAxisVelocity", "(I)F", lambda a: 0.0)

    VC = "Landroid/view/ViewConfiguration;"
    _add(VC, "get", "(Landroid/content/Context;)Landroid/view/ViewConfiguration;", lambda a: JObject(VC, {}))
    for _vcn, _vcv in (
        ("getScaledTouchSlop", 8),
        ("getScaledPagingTouchSlop", 16),
        ("getScaledHoverSlop", 4),
        ("getScaledMaximumFlingVelocity", 8000),
        ("getScaledMinimumFlingVelocity", 50),
    ):
        _add(VC, _vcn, "()I", (lambda val: (lambda a: val))(_vcv))
    _add(VC, "getScaledMaximumFlingVelocity", "(III)I", lambda a: 8000)
    _add(VC, "getScaledMinimumFlingVelocity", "(III)I", lambda a: 50)
    _add(VC, "getScaledHorizontalScrollFactor", "()F", lambda a: 64.0)
    _add(VC, "getScaledVerticalScrollFactor", "()F", lambda a: 64.0)
    _add(VC, "hasPermanentMenuKey", "()Z", lambda a: 0)
    _add(VC, "shouldShowMenuShortcutsWhenKeyboardPresent", "()Z", lambda a: 0)

    # OverScroller / Scroller / EdgeEffect
    OSCR = "Landroid/widget/OverScroller;"
    SCR = "Landroid/widget/Scroller;"
    for _sc_cls in (OSCR, SCR):
        for _sc_sig in ("(Landroid/content/Context;)V", "(Landroid/content/Context;Landroid/view/animation/Interpolator;)V"):
            _add(_sc_cls, "<init>", _sc_sig,
                 lambda a: a[0].fields.update({"_cx": 0, "_cy": 0, "_fx": 0, "_fy": 0, "_fin": True}) if a and isinstance(a[0], JObject) else None)

        def _sc_start(a):
            if len(a) >= 5 and isinstance(a[0], JObject):
                sx, sy, dx, dy = int(a[1]), int(a[2]), int(a[3]), int(a[4])
                a[0].fields.update({"_cx": sx, "_cy": sy, "_fx": sx + dx, "_fy": sy + dy, "_fin": False})
            return None
        _add(_sc_cls, "startScroll", "(IIII)V", _sc_start)
        _add(_sc_cls, "startScroll", "(IIIII)V", _sc_start)

        def _sc_fling(a):
            if len(a) >= 9 and isinstance(a[0], JObject):
                sx, sy = int(a[1]), int(a[2])
                minx, maxx, miny, maxy = int(a[5]), int(a[6]), int(a[7]), int(a[8])
                vx, vy = int(a[3]), int(a[4])
                fx = max(minx, min(maxx, sx + (vx // 10)))
                fy = max(miny, min(maxy, sy + (vy // 10)))
                a[0].fields.update({"_cx": sx, "_cy": sy, "_fx": fx, "_fy": fy, "_fin": False})
            return None
        _add(_sc_cls, "fling", "(IIIIIIII)V", _sc_fling)
        _add(_sc_cls, "fling", "(IIIIIIIIII)V", _sc_fling)
        _add(_sc_cls, "springBack", "(IIIIII)Z",
             lambda a: (a[0].fields.update({"_cx": int(a[1]), "_cy": int(a[2]), "_fx": int(a[1]), "_fy": int(a[2]), "_fin": True}), 0)[1] if len(a) >= 7 and isinstance(a[0], JObject) else 0)

        def _sc_compute(a):
            if a and isinstance(a[0], JObject):
                f = a[0].fields
                if f.get("_fin", True):
                    return 0
                f["_cx"] = int(f.get("_fx", 0))
                f["_cy"] = int(f.get("_fy", 0))
                f["_fin"] = True
                return 1
            return 0
        _add(_sc_cls, "computeScrollOffset", "()Z", _sc_compute)
        _add(_sc_cls, "abortAnimation", "()V", lambda a: a[0].fields.update({"_cx": a[0].fields.get("_fx", 0), "_cy": a[0].fields.get("_fy", 0), "_fin": True}) if a and isinstance(a[0], JObject) else None)
        _add(_sc_cls, "isFinished", "()Z", lambda a: 1 if (not a or not isinstance(a[0], JObject) or a[0].fields.get("_fin", True)) else 0)
        _add(_sc_cls, "getCurrX", "()I", lambda a: int(a[0].fields.get("_cx", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_sc_cls, "getCurrY", "()I", lambda a: int(a[0].fields.get("_cy", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_sc_cls, "getFinalX", "()I", lambda a: int(a[0].fields.get("_fx", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_sc_cls, "getFinalY", "()I", lambda a: int(a[0].fields.get("_fy", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_sc_cls, "getCurrVelocity", "()F", lambda a: 0.0)

    EE = "Landroid/widget/EdgeEffect;"
    _add(EE, "<init>", "(Landroid/content/Context;)V", lambda a: a[0] if a else None)
    _add(EE, "<init>", "(Landroid/content/Context;Landroid/util/AttributeSet;)V", lambda a: a[0] if a else None)
    _add(EE, "setSize", "(II)V", lambda a: None)
    _add(EE, "onPull", "(FF)V", lambda a: a[0].fields.__setitem__("_dist", _f32(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(EE, "onPullDistance", "(FF)F", lambda a: (a[0].fields.__setitem__("_dist", _f32(a[1])), _f32(a[1]))[1] if len(a) > 1 and isinstance(a[0], JObject) else 0.0)
    _add(EE, "onRelease", "()V", lambda a: a[0].fields.__setitem__("_dist", 0.0) if a and isinstance(a[0], JObject) else None)
    _add(EE, "onAbsorb", "(I)V", lambda a: None)
    _add(EE, "isFinished", "()Z", lambda a: 1 if (not a or not isinstance(a[0], JObject) or _f32(a[0].fields.get("_dist", 0.0)) == 0.0) else 0)
    _add(EE, "getDistance", "()F", lambda a: _f32(a[0].fields.get("_dist", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(EE, "draw", "(Landroid/graphics/Canvas;)Z", lambda a: 0)

    # PopupWindow
    PW = "Landroid/widget/PopupWindow;"
    def _pw_init(a):
        if a and isinstance(a[0], JObject):
            f = a[0].fields
            f.setdefault("_w", -2)
            f.setdefault("_h", -2)
            f.setdefault("_showing", False)
            if len(a) > 1 and isinstance(a[1], JObject) and ("View" in a[1].cls or "$view" in a[1].fields):
                f["_content"] = a[1]
            if len(a) >= 4 and isinstance(a[2], int) and isinstance(a[3], int):
                f["_w"], f["_h"] = int(a[2]), int(a[3])
        return a[0] if a else None
    for _pws in (
        "()V", "(Landroid/content/Context;)V", "(Landroid/content/Context;Landroid/util/AttributeSet;)V",
        "(Landroid/content/Context;Landroid/util/AttributeSet;I)V", "(Landroid/content/Context;Landroid/util/AttributeSet;II)V",
        "(Landroid/view/View;)V", "(Landroid/view/View;II)V", "(Landroid/view/View;IIZ)V",
    ):
        _add(PW, "<init>", _pws, _pw_init)
    _add(PW, "setContentView", "(Landroid/view/View;)V", lambda a: a[0].fields.__setitem__("_content", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PW, "getContentView", "()Landroid/view/View;", lambda a: a[0].fields.get("_content") if a and isinstance(a[0], JObject) else None)
    _add(PW, "setWidth", "(I)V", lambda a: a[0].fields.__setitem__("_w", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PW, "getWidth", "()I", lambda a: int(a[0].fields.get("_w", -2)) if a and isinstance(a[0], JObject) else -2)
    _add(PW, "setHeight", "(I)V", lambda a: a[0].fields.__setitem__("_h", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PW, "getHeight", "()I", lambda a: int(a[0].fields.get("_h", -2)) if a and isinstance(a[0], JObject) else -2)
    _add(PW, "setBackgroundDrawable", "(Landroid/graphics/drawable/Drawable;)V", lambda a: a[0].fields.__setitem__("_bg", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PW, "getBackground", "()Landroid/graphics/drawable/Drawable;", lambda a: a[0].fields.get("_bg") if a and isinstance(a[0], JObject) else None)
    _add(PW, "setInputMethodMode", "(I)V", lambda a: a[0].fields.__setitem__("_imm", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PW, "getInputMethodMode", "()I", lambda a: int(a[0].fields.get("_imm", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(PW, "isShowing", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_showing")) else 0)
    for _pws2 in ("showAsDropDown", "showAtLocation"):
        for _pwsig in ("(Landroid/view/View;)V", "(Landroid/view/View;II)V", "(Landroid/view/View;III)V"):
            _add(PW, _pws2, _pwsig, lambda a: a[0].fields.__setitem__("_showing", True) if a and isinstance(a[0], JObject) else None)
    _add(PW, "update", "(Landroid/view/View;IIII)V", lambda a: a[0].fields.update({"_w": int(a[4]), "_h": int(a[5])}) if len(a) > 5 and isinstance(a[0], JObject) else None)
    _add(PW, "dismiss", "()V", lambda a: a[0].fields.__setitem__("_showing", False) if a and isinstance(a[0], JObject) else None)
    for _pwn, _pwsig in (
        ("setOverlapAnchor", "(Z)V"), ("setWindowLayoutType", "(I)V"), ("setAnimationStyle", "(I)V"),
        ("setEnterTransition", "(Landroid/transition/Transition;)V"), ("setExitTransition", "(Landroid/transition/Transition;)V"),
        ("setFocusable", "(Z)V"), ("setOnDismissListener", "(Landroid/widget/PopupWindow$OnDismissListener;)V"),
        ("setOutsideTouchable", "(Z)V"), ("setTouchInterceptor", "(Landroid/view/View$OnTouchListener;)V"),
    ):
        _add(PW, _pwn, _pwsig, lambda a: None)

    # Menus
    MN = "Landroid/view/Menu;"
    MI = "Landroid/view/MenuItem;"
    SMN = "Landroid/view/SubMenu;"
    AP = "Landroid/view/ActionProvider;"
    def _mk_mi(mid=0, gid=0, title=""):
        return JObject(MI, {"_id": int(mid), "_gid": int(gid), "_title": str(title or ""), "_vis": 1, "_en": 1})
    _add(MN, "add", "(IIILjava/lang/CharSequence;)Landroid/view/MenuItem;",
         lambda a: (lambda mi: (a[0].fields.setdefault("_items", []).append(mi) if isinstance(a[0], JObject) else None, mi)[1])(_mk_mi(a[2] if len(a) > 2 else 0, a[1] if len(a) > 1 else 0, _txt(a[4] if len(a) > 4 else ""))))
    _add(MN, "addSubMenu", "(IIILjava/lang/CharSequence;)Landroid/view/SubMenu;",
         lambda a: JObject(SMN, {"_item": _mk_mi(a[2] if len(a) > 2 else 0, a[1] if len(a) > 1 else 0, _txt(a[4] if len(a) > 4 else ""))}))
    _add(MN, "getItem", "(I)Landroid/view/MenuItem;",
         lambda a: (lambda items, idx: items[idx] if 0 <= idx < len(items) else _mk_mi())(a[0].fields.get("_items", []), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else _mk_mi())
    _add(MN, "findItem", "(I)Landroid/view/MenuItem;",
         lambda a: next((x for x in a[0].fields.get("_items", []) if isinstance(x, JObject) and x.fields.get("_id") == int(a[1])), None) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MN, "removeItem", "(I)V", lambda a: a[0].fields.get("_items", []).__setitem__(slice(None), [x for x in a[0].fields.get("_items", []) if not (isinstance(x, JObject) and x.fields.get("_id") == int(a[1]))]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(MN, "performShortcut", "(ILandroid/view/KeyEvent;I)Z", lambda a: 0)
    _add(MN, "setGroupDividerEnabled", "(Z)V", lambda a: None)
    _add(MN, "setQwertyMode", "(Z)V", lambda a: None)
    _add("Landroid/view/MenuInflater;", "<init>", "(Landroid/content/Context;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/MenuInflater;", "inflate", "(ILandroid/view/Menu;)V", lambda a: None)
    _add(SMN, "getItem", "()Landroid/view/MenuItem;", lambda a: a[0].fields.get("_item") if a and isinstance(a[0], JObject) and isinstance(a[0].fields.get("_item"), JObject) else _mk_mi())

    _add(MI, "getItemId", "()I", lambda a: int(a[0].fields.get("_id", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(MI, "getGroupId", "()I", lambda a: int(a[0].fields.get("_gid", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(MI, "getActionView", "()Landroid/view/View;", lambda a: a[0].fields.get("_av") if a and isinstance(a[0], JObject) else None)
    _add(MI, "setActionView", "(Landroid/view/View;)Landroid/view/MenuItem;", lambda a: (a[0].fields.__setitem__("_av", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(MI, "setActionView", "(I)Landroid/view/MenuItem;", lambda a: a[0] if a else None)
    _add(MI, "expandActionView", "()Z", lambda a: (a[0].fields.__setitem__("_exp", 1) if a and isinstance(a[0], JObject) else None, 1)[1])
    _add(MI, "collapseActionView", "()Z", lambda a: (a[0].fields.__setitem__("_exp", 0) if a and isinstance(a[0], JObject) else None, 1)[1])
    _add(MI, "isActionViewExpanded", "()Z", lambda a: int(a[0].fields.get("_exp", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(MI, "setVisible", "(Z)Landroid/view/MenuItem;", lambda a: (a[0].fields.__setitem__("_vis", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(MI, "isVisible", "()Z", lambda a: int(a[0].fields.get("_vis", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(MI, "setEnabled", "(Z)Landroid/view/MenuItem;", lambda a: (a[0].fields.__setitem__("_en", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(MI, "isEnabled", "()Z", lambda a: int(a[0].fields.get("_en", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(MI, "setIcon", "(Landroid/graphics/drawable/Drawable;)Landroid/view/MenuItem;", lambda a: (a[0].fields.__setitem__("_icon", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(MI, "setIcon", "(I)Landroid/view/MenuItem;", lambda a: a[0] if a else None)
    _add(MI, "getIcon", "()Landroid/graphics/drawable/Drawable;", lambda a: a[0].fields.get("_icon") if a and isinstance(a[0], JObject) else None)
    _add(MI, "setIconTintList", "(Landroid/content/res/ColorStateList;)Landroid/view/MenuItem;", lambda a: (a[0].fields.__setitem__("_itint", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(MI, "getIconTintList", "()Landroid/content/res/ColorStateList;", lambda a: a[0].fields.get("_itint") if a and isinstance(a[0], JObject) else None)
    _add(MI, "getSubMenu", "()Landroid/view/SubMenu;", lambda a: a[0].fields.get("_sm") if a and isinstance(a[0], JObject) else None)
    _add(MI, "hasSubMenu", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_sm") is not None) else 0)
    _add(MI, "setShowAsAction", "(I)V", lambda a: None)
    for _min, _misig in (
        ("setAlphabeticShortcut", "(CI)Landroid/view/MenuItem;"),
        ("setNumericShortcut", "(CI)Landroid/view/MenuItem;"),
        ("setContentDescription", "(Ljava/lang/CharSequence;)Landroid/view/MenuItem;"),
        ("setTooltipText", "(Ljava/lang/CharSequence;)Landroid/view/MenuItem;"),
        ("setIconTintMode", "(Landroid/graphics/PorterDuff$Mode;)Landroid/view/MenuItem;"),
        ("setIntent", "(Landroid/content/Intent;)Landroid/view/MenuItem;"),
        ("setOnActionExpandListener", "(Landroid/view/MenuItem$OnActionExpandListener;)Landroid/view/MenuItem;"),
        ("setOnMenuItemClickListener", "(Landroid/view/MenuItem$OnMenuItemClickListener;)Landroid/view/MenuItem;"),
        ("setCheckable", "(Z)Landroid/view/MenuItem;"),
        ("setChecked", "(Z)Landroid/view/MenuItem;"),
        ("setTitleCondensed", "(Ljava/lang/CharSequence;)Landroid/view/MenuItem;"),
    ):
        _add(MI, _min, _misig, lambda a: a[0] if a else None)

    for _apn, _apsig, _apr in (
        ("hasSubMenu", "()Z", 0), ("isVisible", "()Z", 1), ("onCreateActionView", "(Landroid/view/MenuItem;)Landroid/view/View;", None),
        ("onPerformDefaultAction", "()Z", 0), ("onPrepareSubMenu", "(Landroid/view/SubMenu;)V", None),
        ("overridesItemVisibility", "()Z", 0), ("setVisibilityListener", "(Landroid/view/ActionProvider$VisibilityListener;)V", None),
    ):
        _add(AP, _apn, _apsig, (lambda rv: (lambda a: rv))(_apr))

    # Window / Display / Dialog / WindowInsets / BackEvent
    WIN = "Landroid/view/Window;"
    DLG = "Landroid/app/Dialog;"
    WI = "Landroid/view/WindowInsets;"
    WIB = "Landroid/view/WindowInsets$Builder;"
    WIT = "Landroid/view/WindowInsets$Type;"
    WIA = "Landroid/view/WindowInsetsAnimation;"

    _add(WIN, "findViewById", "(I)Landroid/view/View;", _v_find_by_id)
    _add(WIN, "setContentView", "(I)V", lambda a: (_vt_bn("setContentView", "(I)V")(a) if _vt_bn("setContentView", "(I)V") else None))
    _add(WIN, "setContentView", "(Landroid/view/View;)V", lambda a: (_vt_bn("setContentView", "(Landroid/view/View;)V")(a) if _vt_bn("setContentView", "(Landroid/view/View;)V") else None))
    _add(WIN, "getContext", "()Landroid/content/Context;", lambda a: _ctx_default())
    _add(WIN, "getLayoutInflater", "()Landroid/view/LayoutInflater;", lambda a: JObject("Landroid/view/LayoutInflater;", {}))
    _add(WIN, "peekDecorView", "()Landroid/view/View;", lambda a: a[0].fields.get("decor") if a and isinstance(a[0], JObject) else None)
    _add(WIN, "getWindowManager", "()Landroid/view/WindowManager;", lambda a: JObject("Landroid/view/WindowManager;", {}))
    _add(WIN, "getCurrentFocus", "()Landroid/view/View;", lambda a: None)
    _add(WIN, "isFloating", "()Z", lambda a: 0)
    for _wn, _ws, _wr in (
        ("addFlags", "(I)V", None), ("clearFlags", "(I)V", None), ("setFlags", "(II)V", None),
        ("hasFeature", "(I)Z", 0), ("requestFeature", "(I)Z", 1), ("superDispatchKeyEvent", "(Landroid/view/KeyEvent;)Z", 0),
        ("addOnFrameMetricsAvailableListener", "(Landroid/view/Window$OnFrameMetricsAvailableListener;Landroid/os/Handler;)V", None),
        ("removeOnFrameMetricsAvailableListener", "(Landroid/view/Window$OnFrameMetricsAvailableListener;)V", None),
        ("setAttributes", "(Landroid/view/WindowManager$LayoutParams;)V", None),
        ("setBackgroundDrawable", "(Landroid/graphics/drawable/Drawable;)V", None),
        ("setLayout", "(II)V", None), ("setSoftInputMode", "(I)V", None),
    ):
        _add(WIN, _wn, _ws, (lambda rv: (lambda a: rv))(_wr))

    _add("Landroid/view/WindowManager;", "getDefaultDisplay", "()Landroid/view/Display;", lambda a: JObject("Landroid/view/Display;", {}))
    _add("Landroid/view/WindowManager;", "getCurrentWindowMetrics", "()Landroid/view/WindowMetrics;", lambda a: JObject("Landroid/view/WindowMetrics;", {}))
    _add("Landroid/view/WindowMetrics;", "getBounds", "()Landroid/graphics/Rect;", lambda a: JObject("Landroid/graphics/Rect;", {"left": 0, "top": 0, "right": 1080, "bottom": 2280}))
    _add("Landroid/view/WindowManager$LayoutParams;", "<init>", "(IIIIIII)V", lambda a: a[0] if a else None)
    _add("Landroid/view/WindowManager$LayoutParams;", "setTitle", "(Ljava/lang/CharSequence;)V", lambda a: None)
    _add("Landroid/view/Display;", "getRealSize", "(Landroid/graphics/Point;)V", lambda a: a[1].fields.update({"x": 1080, "y": 2280}) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add("Landroid/view/Display;", "getRectSize", "(Landroid/graphics/Rect;)V", lambda a: _rect_write(a[1], 0, 0, 1080, 2280) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add("Landroid/view/Display;", "getMetrics", "(Landroid/util/DisplayMetrics;)V",
         lambda a: a[1].fields.update({"widthPixels": 1080, "heightPixels": 2280, "density": 2.75, "densityDpi": 440, "scaledDensity": 2.75}) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add("Landroid/view/Display;", "getRefreshRate", "()F", lambda a: 60.0)
    _add("Landroid/view/Display;", "getRotation", "()I", lambda a: 0)
    _add("Landroid/hardware/display/DisplayManager;", "getDisplay", "(I)Landroid/view/Display;", lambda a: JObject("Landroid/view/Display;", {}))
    _add("Landroid/hardware/display/DisplayManager;", "getDisplays", "()[Landroid/view/Display;", lambda a: JArray("[Landroid/view/Display;", [JObject("Landroid/view/Display;", {})]))
    for _dcm in ("getSafeInsetBottom", "getSafeInsetLeft", "getSafeInsetRight", "getSafeInsetTop"):
        _add("Landroid/view/DisplayCutout;", _dcm, "()I", lambda a: 0)
    _add("Landroid/view/DisplayCutout;", "getBoundingRects", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))

    # Dialog
    _add(DLG, "<init>", "(Landroid/content/Context;)V", lambda a: a[0] if a else None)
    _add(DLG, "<init>", "(Landroid/content/Context;I)V", lambda a: a[0] if a else None)
    _add(DLG, "getWindow", f"(){WIN}", lambda a: a[0].fields.setdefault("_window", JObject(WIN, {"decor": JObject(V, {"__role": "DecorView"})})) if a and isinstance(a[0], JObject) else JObject(WIN, {}))
    _add(DLG, "findViewById", "(I)Landroid/view/View;", _v_find_by_id)
    _add(DLG, "setContentView", "(I)V", lambda a: None)
    _add(DLG, "setContentView", "(Landroid/view/View;)V", lambda a: a[0].fields.__setitem__("_content", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DLG, "addContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", lambda a: None)
    _add(DLG, "show", "()V", lambda a: (a[0].fields.__setitem__("_showing", True) if a and isinstance(a[0], JObject) else None, _vt_bn("show", "()V")(a) if _vt_bn("show", "()V") else None)[1])
    _add(DLG, "hide", "()V", lambda a: a[0].fields.__setitem__("_showing", False) if a and isinstance(a[0], JObject) else None)
    _add(DLG, "dismiss", "()V", lambda a: (a[0].fields.__setitem__("_showing", False) if a and isinstance(a[0], JObject) else None, _vt_bn("dismiss", "()V")(a) if _vt_bn("dismiss", "()V") else None)[1])
    _add(DLG, "cancel", "()V", lambda a: (a[0].fields.__setitem__("_showing", False) if a and isinstance(a[0], JObject) else None, _vt_bn("cancel", "()V")(a) if _vt_bn("cancel", "()V") else None)[1])
    _add(DLG, "isShowing", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_showing")) else 0)
    for _dnm, _dsig, _dret in (
        ("onCreate", "(Landroid/os/Bundle;)V", None), ("onStart", "()V", None), ("onStop", "()V", None),
        ("onRestoreInstanceState", "(Landroid/os/Bundle;)V", None), ("requestWindowFeature", "(I)Z", 1),
        ("setCancelable", "(Z)V", None), ("setOnCancelListener", "(Landroid/content/DialogInterface$OnCancelListener;)V", None),
        ("setOnDismissListener", "(Landroid/content/DialogInterface$OnDismissListener;)V", None),
        ("setOwnerActivity", "(Landroid/app/Activity;)V", None),
    ):
        _add(DLG, _dnm, _dsig, (lambda rv: (lambda a: rv))(_dret))
    _add(DLG, "onSaveInstanceState", "()Landroid/os/Bundle;", lambda a: JObject("Landroid/os/Bundle;", {"map": {}}))

    # WindowInsets / Builder / Type / Animation / Insets / WindowInsetsCompat
    _add("Landroid/graphics/Insets;", "of", "(IIII)Landroid/graphics/Insets;",
         lambda a: JObject("Landroid/graphics/Insets;", {"left": int(a[0]), "top": int(a[1]), "right": int(a[2]), "bottom": int(a[3])}) if len(a) >= 4 else JObject("Landroid/graphics/Insets;", {"left": 0, "top": 0, "right": 0, "bottom": 0}))
    for _tm, _tv in (
        ("statusBars", 1), ("navigationBars", 2), ("captionBar", 4), ("ime", 8),
        ("systemGestures", 16), ("mandatorySystemGestures", 32), ("tappableElement", 64),
        ("displayCutout", 128), ("systemBars", 7),
    ):
        _add(WIT, _tm, "()I", (lambda val: (lambda a: val))(_tv))

    _add(WI, "<init>", f"({WI})V", lambda a: a[0].fields.update(dict(a[1].fields)) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else None)
    _add(WI, "getInsets", "(I)Landroid/graphics/Insets;",
         lambda a: JObject("Landroid/graphics/Insets;", {"left": int(a[0].fields.get("_left", 0)), "top": int(a[0].fields.get("_top", 0)), "right": int(a[0].fields.get("_right", 0)), "bottom": int(a[0].fields.get("_bottom", 0))}) if a and isinstance(a[0], JObject) else JObject("Landroid/graphics/Insets;", {"left": 0, "top": 0, "right": 0, "bottom": 0}))
    for _inm, _key in (
        ("getSystemWindowInsetLeft", "_left"), ("getSystemWindowInsetTop", "_top"),
        ("getSystemWindowInsetRight", "_right"), ("getSystemWindowInsetBottom", "_bottom"),
        ("getStableInsetLeft", "_left"), ("getStableInsetTop", "_top"),
        ("getStableInsetRight", "_right"), ("getStableInsetBottom", "_bottom"),
    ):
        _add(WI, _inm, "()I", lambda a, k=_key: int(a[0].fields.get(k, 0)) if a and isinstance(a[0], JObject) else 0)
    for _igm in ("getSystemGestureInsets", "getMandatorySystemGestureInsets", "getTappableElementInsets"):
        _add(WI, _igm, "()Landroid/graphics/Insets;", lambda a: JObject("Landroid/graphics/Insets;", {"left": 0, "top": 0, "right": 0, "bottom": 0}))
    _add(WI, "getDisplayCutout", "()Landroid/view/DisplayCutout;", lambda a: None)
    for _icm in ("consumeDisplayCutout", "consumeStableInsets", "consumeSystemWindowInsets"):
        _add(WI, _icm, f"(){WI}", lambda a: _mk_insets_obj())
    _add(WI, "replaceSystemWindowInsets", f"(IIII){WI}", lambda a: _mk_insets_obj(a[1], a[2], a[3], a[4]) if len(a) >= 5 else _mk_insets_obj())
    _add(WI, "inset", f"(IIII){WI}", lambda a: _mk_insets_obj())
    _add(WI, "isConsumed", "()Z", lambda a: int(a[0].fields.get("_consumed", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(WI, "isRound", "()Z", lambda a: int(a[0].fields.get("_round", 0)) if a and isinstance(a[0], JObject) else 0)

    _add(WIB, "<init>", "()V", lambda a: a[0] if a else None)
    _add(WIB, "<init>", f"({WI})V", lambda a: a[0].fields.update(dict(a[1].fields)) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else None)
    _add(WIB, "setInsets", f"(ILandroid/graphics/Insets;){WIB}", lambda a: (a[0].fields.update({f"_{k}": int(a[2].fields.get(k, 0)) for k in ("left", "top", "right", "bottom")}) if len(a) > 2 and isinstance(a[0], JObject) and isinstance(a[2], JObject) else None, a[0] if a else None)[1])
    for _wbm in ("setMandatorySystemGestureInsets", "setStableInsets", "setSystemGestureInsets", "setSystemWindowInsets", "setTappableElementInsets"):
        _add(WIB, _wbm, f"(Landroid/graphics/Insets;){WIB}", lambda a: (a[0].fields.update({f"_{k}": int(a[1].fields.get(k, 0)) for k in ("left", "top", "right", "bottom")}) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else None, a[0] if a else None)[1])
    _add(WIB, "build", f"(){WI}", lambda a: JObject(WI, dict(a[0].fields)) if a and isinstance(a[0], JObject) else _mk_insets_obj())

    _add(WIA, "<init>", "(ILandroid/view/animation/Interpolator;J)V",
         lambda a: a[0].fields.update({"_mask": int(a[1]), "_dur": int(a[3]), "_frac": 0.0}) if len(a) >= 4 and isinstance(a[0], JObject) else None)
    _add(WIA, "getDurationMillis", "()J", lambda a: int(a[0].fields.get("_dur", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(WIA, "getFraction", "()F", lambda a: _f32(a[0].fields.get("_frac", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(WIA, "getInterpolatedFraction", "()F", lambda a: _f32(a[0].fields.get("_frac", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(WIA, "getTypeMask", "()I", lambda a: int(a[0].fields.get("_mask", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(WIA, "setFraction", "(F)V", lambda a: a[0].fields.__setitem__("_frac", _f32(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add("Landroid/view/WindowInsetsAnimation$Bounds;", "<init>", "(Landroid/graphics/Insets;Landroid/graphics/Insets;)V",
         lambda a: a[0].fields.update({"_low": a[1], "_up": a[2]}) if len(a) >= 3 and isinstance(a[0], JObject) else None)
    _add("Landroid/view/WindowInsetsAnimation$Bounds;", "getLowerBound", "()Landroid/graphics/Insets;", lambda a: a[0].fields.get("_low") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/WindowInsetsAnimation$Bounds;", "getUpperBound", "()Landroid/graphics/Insets;", lambda a: a[0].fields.get("_up") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/WindowInsetsAnimation$Callback;", "<init>", "(I)V", lambda a: a[0] if a else None)
    _add("Landroid/view/WindowInsetsController;", "getSystemBarsAppearance", "()I", lambda a: 0)
    _add("Landroid/view/WindowInsetsController;", "hide", "(I)V", lambda a: None)
    _add("Landroid/view/WindowInsetsController;", "show", "(I)V", lambda a: None)
    _add("Landroid/view/WindowInsetsController;", "setSystemBarsBehavior", "(I)V", lambda a: None)

    WIC = "Landroidx/core/view/WindowInsetsCompat;"
    _add(WIC, "getInsets", "(I)Landroidx/core/graphics/Insets;", lambda a: JObject("Landroidx/core/graphics/Insets;", {"left": 0, "top": 0, "right": 0, "bottom": 0}))
    _add(WIC, "getSystemWindowInsetTop", "()I", lambda a: 0)
    _add(WIC, "getSystemWindowInsetBottom", "()I", lambda a: 0)
    _add("Landroid/window/BackEvent;", "getProgress", "()F", lambda a: 0.0)
    _add("Landroid/window/BackEvent;", "getSwipeEdge", "()I", lambda a: 0)
    _add("Landroid/window/BackEvent;", "getTouchX", "()F", lambda a: 0.0)
    _add("Landroid/window/BackEvent;", "getTouchY", "()F", lambda a: 0.0)
    _add("Landroid/window/OnBackInvokedDispatcher;", "registerOnBackInvokedCallback", "(ILandroid/window/OnBackInvokedCallback;)V", lambda a: None)
    _add("Landroid/window/OnBackInvokedDispatcher;", "unregisterOnBackInvokedCallback", "(Landroid/window/OnBackInvokedCallback;)V", lambda a: None)

    # =========================================================================
    # 5. Animators, Interpolators, Choreographer, GestureDetector, Misc View Utils
    # =========================================================================
    AN = "Landroid/animation/Animator;"
    VA = "Landroid/animation/ValueAnimator;"
    OA = "Landroid/animation/ObjectAnimator;"
    AS = "Landroid/animation/AnimatorSet;"
    VPA = "Landroid/view/ViewPropertyAnimator;"

    for _ac in (AN, VA, OA, AS):
        _add(_ac, "<init>", "()V", lambda a: a[0] if a else None)
        _add(_ac, "addListener", "(Landroid/animation/Animator$AnimatorListener;)V",
             lambda a: a[0].fields.setdefault("_listeners", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "removeListener", "(Landroid/animation/Animator$AnimatorListener;)V",
             lambda a: a[0].fields.get("_listeners", []).remove(a[1]) if len(a) > 1 and isinstance(a[0], JObject) and a[1] in a[0].fields.get("_listeners", []) else None)
        _add(_ac, "addPauseListener", "(Landroid/animation/Animator$AnimatorPauseListener;)V", lambda a: None)
        _add(_ac, "setDuration", "(J)Landroid/animation/Animator;", lambda a: a[0] if a else None)
        _add(_ac, "setStartDelay", "(J)V", lambda a: a[0].fields.__setitem__("_delay", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "getStartDelay", "()J", lambda a: int(a[0].fields.get("_delay", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_ac, "setInterpolator", "(Landroid/animation/TimeInterpolator;)V", lambda a: a[0].fields.__setitem__("_interp", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "setTarget", "(Ljava/lang/Object;)V", lambda a: a[0].fields.__setitem__("_target", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "end", "()V", lambda a: None)
        _add(_ac, "pause", "()V", lambda a: None)
        _add(_ac, "resume", "()V", lambda a: None)
        _add(_ac, "isRunning", "()Z", lambda a: 0)
        _add(_ac, "isStarted", "()Z", lambda a: 0)
    _add(VA, "getInterpolator", "()Landroid/animation/TimeInterpolator;", lambda a: a[0].fields.get("_interp") if a and isinstance(a[0], JObject) else None)
    _add(VA, "setFloatValues", "([F)V", lambda a: a[0].fields.__setitem__("_vals", list(getattr(a[1], "data", ()) or ())) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(VA, "setRepeatCount", "(I)V", lambda a: None)
    _add(VA, "setRepeatMode", "(I)V", lambda a: None)
    _add(OA, "ofFloat", f"(Ljava/lang/Object;Landroid/util/Property;[F){OA}",
         lambda a: JObject(OA, {"_target": a[0] if a else None, "_prop": a[1].fields.get("_name", "") if len(a) > 1 and isinstance(a[1], JObject) else "", "_vals": list(getattr(a[2], "data", ()) or ()) if len(a) > 2 else []}))
    _add(OA, "ofObject", f"(Ljava/lang/Object;Landroid/util/Property;Landroid/animation/TypeConverter;Landroid/graphics/Path;){OA}",
         lambda a: JObject(OA, {"_target": a[0] if a else None}))
    _add(OA, "ofPropertyValuesHolder", f"(Ljava/lang/Object;[Landroid/animation/PropertyValuesHolder;){OA}",
         lambda a: JObject(OA, {"_target": a[0] if a else None}))
    _add(OA, "getPropertyName", "()Ljava/lang/String;", lambda a: JString(str(a[0].fields.get("_prop", ""))) if a and isinstance(a[0], JObject) else JString(""))
    _add(OA, "setAutoCancel", "(Z)V", lambda a: None)
    _add(AS, "getChildAnimations", "()Ljava/util/ArrayList;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(a[0].fields.get("_children", ())) if a and isinstance(a[0], JObject) else []}))
    _add(AS, "play", f"({AN})Landroid/animation/AnimatorSet$Builder;", lambda a: JObject("Landroid/animation/AnimatorSet$Builder;", {"_set": a[0] if a else None}))
    _add(AS, "playTogether", "(Ljava/util/Collection;)V", lambda a: None)
    _add("Landroid/animation/AnimatorInflater;", "loadAnimator", f"(Landroid/content/Context;I){AN}", lambda a: JObject(VA, {}))
    _add("Landroid/animation/PropertyValuesHolder;", "ofFloat", "(Landroid/util/Property;[F)Landroid/animation/PropertyValuesHolder;", lambda a: JObject("Landroid/animation/PropertyValuesHolder;", {}))

    _add(VPA, "getDuration", "()J", lambda a: int(a[0].fields.get("_dur", 300)) if a and isinstance(a[0], JObject) else 300)
    for _vpam, _vpas in (
        ("setInterpolator", f"(Landroid/animation/TimeInterpolator;){VPA}"),
        ("setListener", f"(Landroid/animation/Animator$AnimatorListener;){VPA}"),
        ("setStartDelay", f"(J){VPA}"),
        ("setUpdateListener", f"(Landroid/animation/ValueAnimator$AnimatorUpdateListener;){VPA}"),
        ("translationX", f"(F){VPA}"),
        ("translationY", f"(F){VPA}"),
    ):
        _add(VPA, _vpam, _vpas, lambda a: a[0] if a else None)

    for _icls in (
        "Landroid/view/animation/LinearInterpolator;",
        "Landroid/view/animation/AccelerateDecelerateInterpolator;",
        "Landroid/view/animation/AccelerateInterpolator;",
        "Landroid/view/animation/DecelerateInterpolator;",
        "Landroid/view/animation/PathInterpolator;",
    ):
        _add(_icls, "<init>", "()V", lambda a: a[0] if a else None)
        _add(_icls, "<init>", "(F)V", lambda a: a[0] if a else None)
        _add(_icls, "<init>", "(FFFF)V", lambda a: a[0] if a else None)
        _add(_icls, "<init>", "(Landroid/graphics/Path;)V", lambda a: a[0] if a else None)
        _add(_icls, "getInterpolation", "(F)F", lambda a: max(0.0, min(1.0, _f32(a[1]))) if len(a) > 1 else 0.0)

    _add("Landroid/view/animation/Animation;", "hasEnded", "()Z", lambda a: 1)
    _add("Landroid/view/animation/Animation;", "hasStarted", "()Z", lambda a: 1)
    _add("Landroid/view/animation/Animation;", "setAnimationListener", "(Landroid/view/animation/Animation$AnimationListener;)V", lambda a: None)
    _add("Landroid/view/animation/Animation;", "setDuration", "(J)V", lambda a: None)
    _add("Landroid/view/animation/Animation;", "setInterpolator", "(Landroid/view/animation/Interpolator;)V", lambda a: None)
    _add("Landroid/view/animation/AnimationSet;", "<init>", "(Z)V", lambda a: a[0] if a else None)
    _add("Landroid/view/animation/AnimationSet;", "getTransformation", "(JLandroid/view/animation/Transformation;)Z", lambda a: 0)
    _add("Landroid/view/animation/AnimationUtils;", "currentAnimationTimeMillis", "()J", lambda a: 1000)
    _add("Landroid/view/animation/AnimationUtils;", "loadInterpolator", "(Landroid/content/Context;I)Landroid/view/animation/Interpolator;", lambda a: JObject("Landroid/view/animation/LinearInterpolator;", {}))
    _add("Landroid/view/animation/AnimationUtils;", "loadAnimation", "(Landroid/content/Context;I)Landroid/view/animation/Animation;", lambda a: JObject("Landroid/view/animation/Animation;", {}))
    _add("Landroid/transition/Transition;", "addListener", "(Landroid/transition/Transition$TransitionListener;)Landroid/transition/Transition;", lambda a: a[0] if a else None)

    _add("Landroid/view/Choreographer;", "getInstance", "()Landroid/view/Choreographer;", lambda a: JObject("Landroid/view/Choreographer;", {}))
    _add("Landroid/view/Choreographer;", "postFrameCallback", "(Landroid/view/Choreographer$FrameCallback;)V", lambda a: None)
    _add("Landroid/view/Choreographer;", "removeFrameCallback", "(Landroid/view/Choreographer$FrameCallback;)V", lambda a: None)
    _add("Landroid/view/GestureDetector;", "<init>", "(Landroid/content/Context;Landroid/view/GestureDetector$OnGestureListener;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/GestureDetector;", "<init>", "(Landroid/content/Context;Landroid/view/GestureDetector$OnGestureListener;Landroid/os/Handler;)V", lambda a: a[0] if a else None)
    _add("Landroid/view/GestureDetector;", "onTouchEvent", "(Landroid/view/MotionEvent;)Z", lambda a: 0)
    _add("Landroid/view/FocusFinder;", "getInstance", "()Landroid/view/FocusFinder;", lambda a: JObject("Landroid/view/FocusFinder;", {}))
    _add("Landroid/view/FocusFinder;", "findNextFocus", "(Landroid/view/ViewGroup;Landroid/view/View;I)Landroid/view/View;", lambda a: None)
    _add("Landroid/view/FocusFinder;", "findNextFocusFromRect", "(Landroid/view/ViewGroup;Landroid/graphics/Rect;I)Landroid/view/View;", lambda a: None)
    _add("Landroid/view/Gravity;", "apply", "(IIILandroid/graphics/Rect;Landroid/graphics/Rect;I)V",
         lambda a: _rect_write(a[4], _rect_read(a[3])[0], _rect_read(a[3])[1], _rect_read(a[3])[0] + int(a[1]), _rect_read(a[3])[1] + int(a[2])) if len(a) >= 5 and isinstance(a[4], JObject) else None)
    _add("Landroid/view/PointerIcon;", "getSystemIcon", "(Landroid/content/Context;I)Landroid/view/PointerIcon;", lambda a: JObject("Landroid/view/PointerIcon;", {}))
    _add("Landroid/view/ContextThemeWrapper;", "<init>", "(Landroid/content/Context;I)V", lambda a: a[0] if a else None)
    _add("Landroid/view/LayoutInflater;", "getFactory2", "()Landroid/view/LayoutInflater$Factory2;", lambda a: a[0].fields.get("_factory") if a and isinstance(a[0], JObject) else None)
    def _li_inflate(a, sig):
        bn = _vt_bn("inflate", sig)
        if bn is not None:
            res = bn(a)
            if res is not None:
                return res
        if len(a) > 2 and isinstance(a[2], JObject):
            attach = True if len(a) == 3 else bool(a[3])
            if attach:
                return a[2]
        return JObject(V, {})
    _add("Landroid/view/LayoutInflater;", "inflate", "(ILandroid/view/ViewGroup;)Landroid/view/View;", lambda a: _li_inflate(a, "(ILandroid/view/ViewGroup;)Landroid/view/View;"))
    _add("Landroid/view/LayoutInflater;", "inflate", "(ILandroid/view/ViewGroup;Z)Landroid/view/View;", lambda a: _li_inflate(a, "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"))

    # Listener / Callback Interface Defaults (fallback when no concrete DEX implementation)
    _add("Landroid/text/TextWatcher;", "beforeTextChanged", "(Ljava/lang/CharSequence;III)V", lambda a: None)
    _add("Landroid/text/TextWatcher;", "onTextChanged", "(Ljava/lang/CharSequence;III)V", lambda a: None)
    _add("Landroid/text/TextWatcher;", "afterTextChanged", "(Landroid/text/Editable;)V", lambda a: None)
    _add("Landroid/text/SpanWatcher;", "onSpanAdded", "(Landroid/text/Spannable;Ljava/lang/Object;II)V", lambda a: None)
    _add("Landroid/text/SpanWatcher;", "onSpanChanged", "(Landroid/text/Spannable;Ljava/lang/Object;IIII)V", lambda a: None)
    _add("Landroid/text/SpanWatcher;", "onSpanRemoved", "(Landroid/text/Spannable;Ljava/lang/Object;II)V", lambda a: None)
    _add("Landroid/view/ActionMode$Callback;", "onCreateActionMode", "(Landroid/view/ActionMode;Landroid/view/Menu;)Z", lambda a: 1)
    _add("Landroid/view/ActionMode$Callback;", "onPrepareActionMode", "(Landroid/view/ActionMode;Landroid/view/Menu;)Z", lambda a: 0)
    _add("Landroid/view/ActionMode$Callback;", "onActionItemClicked", "(Landroid/view/ActionMode;Landroid/view/MenuItem;)Z", lambda a: 0)
    _add("Landroid/view/ActionMode$Callback;", "onDestroyActionMode", "(Landroid/view/ActionMode;)V", lambda a: None)
    _add("Landroid/view/CollapsibleActionView;", "onActionViewExpanded", "()V", lambda a: None)
    _add("Landroid/view/CollapsibleActionView;", "onActionViewCollapsed", "()V", lambda a: None)
    _add("Landroid/view/MenuItem$OnActionExpandListener;", "onMenuItemActionExpand", "(Landroid/view/MenuItem;)Z", lambda a: 1)
    _add("Landroid/view/MenuItem$OnActionExpandListener;", "onMenuItemActionCollapse", "(Landroid/view/MenuItem;)Z", lambda a: 1)
    _add("Landroid/view/MenuItem$OnMenuItemClickListener;", "onMenuItemClick", "(Landroid/view/MenuItem;)Z", lambda a: 0)
    _add("Landroid/view/View$OnApplyWindowInsetsListener;", "onApplyWindowInsets", "(Landroid/view/View;Landroid/view/WindowInsets;)Landroid/view/WindowInsets;",
         lambda a: a[2] if len(a) > 2 else _mk_insets_obj())
    _add("Landroid/view/View$OnClickListener;", "onClick", "(Landroid/view/View;)V", lambda a: None)
    _add("Landroid/view/ViewGroup$OnHierarchyChangeListener;", "onChildViewAdded", "(Landroid/view/View;Landroid/view/View;)V", lambda a: None)
    _add("Landroid/view/ViewGroup$OnHierarchyChangeListener;", "onChildViewRemoved", "(Landroid/view/View;Landroid/view/View;)V", lambda a: None)
    _add("Landroid/widget/AdapterView$OnItemClickListener;", "onItemClick", "(Landroid/widget/AdapterView;Landroid/view/View;IJ)V", lambda a: None)
    _add("Landroid/widget/PopupWindow$OnDismissListener;", "onDismiss", "()V", lambda a: None)
    _add("Landroid/content/DialogInterface$OnClickListener;", "onClick", "(Landroid/content/DialogInterface;I)V", lambda a: None)
    _add("Landroid/content/DialogInterface$OnKeyListener;", "onKey", "(Landroid/content/DialogInterface;ILandroid/view/KeyEvent;)Z", lambda a: 0)
    WC = "Landroid/view/Window$Callback;"
    for _wcm, _wcs, _wcr in (
        ("dispatchGenericMotionEvent", "(Landroid/view/MotionEvent;)Z", 0),
        ("dispatchKeyEvent", "(Landroid/view/KeyEvent;)Z", 0),
        ("dispatchKeyShortcutEvent", "(Landroid/view/KeyEvent;)Z", 0),
        ("dispatchPopulateAccessibilityEvent", "(Landroid/view/accessibility/AccessibilityEvent;)Z", 0),
        ("dispatchTouchEvent", "(Landroid/view/MotionEvent;)Z", 0),
        ("dispatchTrackballEvent", "(Landroid/view/MotionEvent;)Z", 0),
        ("onActionModeFinished", "(Landroid/view/ActionMode;)V", None),
        ("onActionModeStarted", "(Landroid/view/ActionMode;)V", None),
        ("onAttachedToWindow", "()V", None),
        ("onContentChanged", "()V", None),
        ("onCreatePanelMenu", "(ILandroid/view/Menu;)Z", 1),
        ("onCreatePanelView", "(I)Landroid/view/View;", None),
        ("onDetachedFromWindow", "()V", None),
        ("onMenuItemSelected", "(ILandroid/view/MenuItem;)Z", 0),
        ("onMenuOpened", "(ILandroid/view/Menu;)Z", 1),
        ("onPanelClosed", "(ILandroid/view/Menu;)V", None),
        ("onPointerCaptureChanged", "(Z)V", None),
        ("onPreparePanel", "(ILandroid/view/View;Landroid/view/Menu;)Z", 1),
        ("onProvideKeyboardShortcuts", "(Ljava/util/List;Landroid/view/Menu;I)V", None),
        ("onSearchRequested", "()Z", 0),
        ("onSearchRequested", "(Landroid/view/SearchEvent;)Z", 0),
        ("onWindowAttributesChanged", "(Landroid/view/WindowManager$LayoutParams;)V", None),
        ("onWindowFocusChanged", "(Z)V", None),
        ("onWindowStartingActionMode", "(Landroid/view/ActionMode$Callback;I)Landroid/view/ActionMode;", None),
    ):
        _add(WC, _wcm, _wcs, (lambda rv: (lambda a: rv))(_wcr))
    _add("Landroid/view/DragEvent;", "getAction", "()I", lambda a: 0)
    _add("Landroid/view/DragEvent;", "getClipData", "()Landroid/content/ClipData;", lambda a: None)
    _add("Landroid/view/DragEvent;", "getX", "()F", lambda a: 0.0)
    _add("Landroid/view/DragEvent;", "getY", "()F", lambda a: 0.0)
    _add("Landroid/view/FrameMetrics;", "getMetric", "(I)J", lambda a: 0)
    _add("Landroid/view/ContentInfo$Builder;", "<init>", "(Landroid/content/ClipData;I)V", lambda a: a[0] if a else None)
    _add("Landroid/view/ContentInfo$Builder;", "setExtras", "(Landroid/os/Bundle;)Landroid/view/ContentInfo$Builder;", lambda a: a[0] if a else None)
    _add("Landroid/view/ContentInfo$Builder;", "setFlags", "(I)Landroid/view/ContentInfo$Builder;", lambda a: a[0] if a else None)
    _add("Landroid/view/ContentInfo$Builder;", "setLinkUri", "(Landroid/net/Uri;)Landroid/view/ContentInfo$Builder;", lambda a: a[0] if a else None)
    _add("Landroid/view/ContentInfo$Builder;", "build", "()Landroid/view/ContentInfo;", lambda a: JObject("Landroid/view/ContentInfo;", {}))
    _add("Landroid/view/ContentInfo;", "getClip", "()Landroid/content/ClipData;", lambda a: None)
    _add("Landroid/view/ContentInfo;", "getFlags", "()I", lambda a: 0)
    _add("Landroid/view/ContentInfo;", "getSource", "()I", lambda a: 0)
    _add("Landroid/view/inputmethod/InputMethodManager;", "isActive", "()Z", lambda a: 0)
    _add("Landroid/view/inputmethod/InputMethodManager;", "isActive", "(Landroid/view/View;)Z", lambda a: 0)
    _add("Landroid/view/inputmethod/InputMethodManager;", "restartInput", "(Landroid/view/View;)V", lambda a: None)
    _add("Landroid/view/inputmethod/InputMethodManager;", "updateSelection", "(Landroid/view/View;IIII)V", lambda a: None)
    _add("Landroid/view/inputmethod/InputMethodManager;", "isAcceptingText", "()Z", lambda a: 1)
    RB("restartInput", "(Landroid/view/View;)V", lambda a: None)
    BIC = "Landroid/view/inputmethod/BaseInputConnection;"
    _add(BIC, "<init>", "(Landroid/view/View;Z)V", lambda a: a[0].fields.update({"_target": a[1] if len(a) > 1 else None, "_full": bool(a[2] if len(a) > 2 else False)}) if a and isinstance(a[0], JObject) else None)
    _add(BIC, "getEditable", "()Landroid/text/Editable;", lambda a: JObject("Landroid/text/SpannableStringBuilder;", {"$text": ""}))
    _add(BIC, "beginBatchEdit", "()Z", lambda a: 1)
    _add(BIC, "endBatchEdit", "()Z", lambda a: 1)
    _add(BIC, "finishComposingText", "()Z", lambda a: 1)
    _add(BIC, "commitText", "(Ljava/lang/CharSequence;I)Z", lambda a: 1)
    _add(BIC, "deleteSurroundingText", "(II)Z", lambda a: 1)
    _add(BIC, "sendKeyEvent", "(Landroid/view/KeyEvent;)Z", lambda a: 0)
    TSM = "Landroid/view/textservice/TextServicesManager;"
    _add(TSM, "getEnabledSpellCheckerInfos", "()[Landroid/view/textservice/SpellCheckerInfo;", lambda a: JArray("[Landroid/view/textservice/SpellCheckerInfo;", []))
    _add(TSM, "getEnabledSpellCheckerInfos", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(TSM, "isSpellCheckerEnabled", "()Z", lambda a: 0)
    _add(TSM, "newSpellCheckerSession", "(Landroid/os/Bundle;Ljava/util/Locale;Landroid/view/textservice/SpellCheckerSession$SpellCheckerSessionListener;Z)Landroid/view/textservice/SpellCheckerSession;", lambda a: None)
    RB("getEnabledSpellCheckerInfos", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
    RB("isSpellCheckerEnabled", "()Z", lambda a: 0)
    RB("setWillNotDraw", "(Z)V", lambda a: None)
    _add("Landroid/view/inputmethod/EditorInfo;", "setInitialSurroundingSubText", "(Ljava/lang/CharSequence;I)V", lambda a: None)
    _add("Landroid/view/inputmethod/InputConnectionWrapper;", "<init>", "(Landroid/view/inputmethod/InputConnection;Z)V", lambda a: a[0] if a else None)
    _add("Landroid/view/inputmethod/InputConnectionWrapper;", "commitContent", "(Landroid/view/inputmethod/InputContentInfo;ILandroid/os/Bundle;)Z", lambda a: 0)
    _add("Landroid/view/inputmethod/InputConnectionWrapper;", "performPrivateCommand", "(Ljava/lang/String;Landroid/os/Bundle;)Z", lambda a: 0)
    _add("Landroid/view/inputmethod/InputConnectionWrapper;", "deleteSurroundingText", "(II)Z", lambda a: 1)
    _add("Landroid/view/inputmethod/InputContentInfo;", "<init>", "(Landroid/net/Uri;Landroid/content/ClipDescription;Landroid/net/Uri;)V", lambda a: a[0].fields.update({"_uri": a[1], "_desc": a[2], "_link": a[3]}) if len(a) > 3 and isinstance(a[0], JObject) else None)
    _add("Landroid/view/inputmethod/InputContentInfo;", "getContentUri", "()Landroid/net/Uri;", lambda a: a[0].fields.get("_uri") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/inputmethod/InputContentInfo;", "getDescription", "()Landroid/content/ClipDescription;", lambda a: a[0].fields.get("_desc") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/inputmethod/InputContentInfo;", "getLinkUri", "()Landroid/net/Uri;", lambda a: a[0].fields.get("_link") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/inputmethod/InputContentInfo;", "requestPermission", "()V", lambda a: None)

    # Util classes linked to View / Layout
    _add("Landroid/util/Property;", "<init>", "(Ljava/lang/Class;Ljava/lang/String;)V", lambda a: a[0].fields.__setitem__("_name", _txt(a[2])) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add("Landroid/util/Size;", "<init>", "(II)V", lambda a: a[0].fields.update({"_w": int(a[1]), "_h": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add("Landroid/util/StateSet;", "trimStateSet", "([II)[I", lambda a: JArray("[I", list(a[0].data[:int(a[1])])) if len(a) > 1 and isinstance(a[0], JArray) else JArray("[I", []))
    _add("Landroid/util/StateSet;", "stateSetMatches", "([I[I)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JArray) and isinstance(a[1], JArray) and set(a[0].data).issubset(set(a[1].data))) else 0)
    _add("Landroid/util/TypedValue;", "applyDimension", "(IFLandroid/util/DisplayMetrics;)F",
         lambda a: (lambda unit, val, dm: float(val * {0: 1.0, 1: float(dm.fields.get("density", 2.75) if isinstance(dm, JObject) else 2.75), 2: float(dm.fields.get("scaledDensity", 2.75) if isinstance(dm, JObject) else 2.75), 3: float(dm.fields.get("xdpi", 440.0) if isinstance(dm, JObject) else 440.0) / 72.0, 4: float(dm.fields.get("xdpi", 440.0) if isinstance(dm, JObject) else 440.0), 5: float(dm.fields.get("xdpi", 440.0) if isinstance(dm, JObject) else 440.0) / 25.4}.get(unit, 1.0)))(int(a[0]), _f32(a[1]), a[2] if len(a) > 2 else None) if len(a) >= 2 else 0.0)
    _add("Landroid/util/TypedValue;", "complexToDimensionPixelSize", "(ILandroid/util/DisplayMetrics;)I", lambda a: int(a[0]) if a else 0)
    _add("Landroid/util/TypedValue;", "getDimension", "(Landroid/util/DisplayMetrics;)F", lambda a: 0.0)
    _add("Landroid/util/TypedValue;", "getFloat", "()F", lambda a: 0.0)
    _add("Landroid/util/TypedValue;", "getFraction", "(FF)F", lambda a: 0.0)
    for _as_nm, _as_sig, _as_ret in (
        ("getAttributeBooleanValue", "(IZ)Z", lambda a: int(bool(a[2])) if len(a) > 2 else 0),
        ("getAttributeCount", "()I", lambda a: 0),
        ("getAttributeNameResource", "(I)I", lambda a: 0),
        ("getAttributeValue", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", lambda a: None),
        ("getAttributeIntValue", "(Ljava/lang/String;Ljava/lang/String;I)I", lambda a: int(a[3]) if len(a) > 3 else 0),
        ("getPositionDescription", "()Ljava/lang/String;", lambda a: JString("attrs:1")),
    ):
        _add("Landroid/util/AttributeSet;", _as_nm, _as_sig, _as_ret)

    # =========================================================================
    # 6. Accessibility (Manager, NodeInfo, Record, Event, Delegate)
    # =========================================================================
    AM = "Landroid/view/accessibility/AccessibilityManager;"
    ANI = "Landroid/view/accessibility/AccessibilityNodeInfo;"
    ANIC = "Landroidx/core/view/accessibility/AccessibilityNodeInfoCompat;"
    AA = "Landroid/view/accessibility/AccessibilityNodeInfo$AccessibilityAction;"
    CI = "Landroid/view/accessibility/AccessibilityNodeInfo$CollectionInfo;"
    CII = "Landroid/view/accessibility/AccessibilityNodeInfo$CollectionItemInfo;"
    RI = "Landroid/view/accessibility/AccessibilityNodeInfo$RangeInfo;"
    AR = "Landroid/view/accessibility/AccessibilityRecord;"
    AE = "Landroid/view/accessibility/AccessibilityEvent;"
    AD = "Landroid/view/View$AccessibilityDelegate;"

    _add(AM, "getInstance", f"(Landroid/content/Context;){AM}", lambda a: JObject(AM, {}))
    _add(AM, "isEnabled", "()Z", lambda a: 0)
    _add(AM, "isTouchExplorationEnabled", "()Z", lambda a: 0)
    _add(AM, "getEnabledAccessibilityServiceList", "(I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(AM, "addTouchExplorationStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$TouchExplorationStateChangeListener;)Z", lambda a: 1)
    _add(AM, "removeTouchExplorationStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$TouchExplorationStateChangeListener;)Z", lambda a: 1)
    _add(AM, "addAccessibilityStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$AccessibilityStateChangeListener;)Z", lambda a: 1)
    _add(AM, "removeAccessibilityStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$AccessibilityStateChangeListener;)Z", lambda a: 1)
    _add(AM, "sendAccessibilityEvent", f"({AE})V", lambda a: None)
    RB("isTouchExplorationEnabled", "()Z", lambda a: 0)
    RB("addTouchExplorationStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$TouchExplorationStateChangeListener;)Z", lambda a: 1)
    RB("removeTouchExplorationStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$TouchExplorationStateChangeListener;)Z", lambda a: 1)
    RB("addAccessibilityStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$AccessibilityStateChangeListener;)Z", lambda a: 1)
    RB("removeAccessibilityStateChangeListener", "(Landroid/view/accessibility/AccessibilityManager$AccessibilityStateChangeListener;)Z", lambda a: 1)
    ANP = "Landroid/view/accessibility/AccessibilityNodeProvider;"
    _add(ANP, "<init>", "()V", lambda a: a[0] if a else None)
    _add(ANP, "createAccessibilityNodeInfo", f"(I){ANI}", lambda a: None)
    _add(ANP, "performAction", "(IILandroid/os/Bundle;)Z", lambda a: 0)
    _add(ANP, "findAccessibilityNodeInfosByText", "(Ljava/lang/String;I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(ANP, "findFocus", f"(I){ANI}", lambda a: None)
    _add("Landroid/accessibilityservice/AccessibilityServiceInfo;", "getSettingsActivityName", "()Ljava/lang/String;", lambda a: None)

    _add(AA, "<init>", "(ILjava/lang/CharSequence;)V", lambda a: a[0].fields.update({"_id": int(a[1]), "_label": _txt(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(AA, "getId", "()I", lambda a: int(a[0].fields.get("_id", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(AA, "getLabel", "()Ljava/lang/CharSequence;", lambda a: JString(a[0].fields["_label"]) if a and isinstance(a[0], JObject) and "_label" in a[0].fields else None)

    _add(CI, "obtain", f"(IIZ){CI}", lambda a: JObject(CI, {"_r": int(a[0]), "_c": int(a[1])}))
    _add(CI, "obtain", f"(IIZI){CI}", lambda a: JObject(CI, {"_r": int(a[0]), "_c": int(a[1])}))
    _add(CII, "obtain", f"(IIIIZ){CII}", lambda a: JObject(CII, {}))
    _add(CII, "obtain", f"(IIIIZZ){CII}", lambda a: JObject(CII, {}))
    _add(RI, "obtain", f"(IFFF){RI}", lambda a: JObject(RI, {"_min": _f32(a[1]), "_max": _f32(a[2]), "_cur": _f32(a[3])}) if len(a) >= 4 else JObject(RI, {}))
    _add(RI, "getMin", "()F", lambda a: _f32(a[0].fields.get("_min", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(RI, "getMax", "()F", lambda a: _f32(a[0].fields.get("_max", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(RI, "getCurrent", "()F", lambda a: _f32(a[0].fields.get("_cur", 0.0)) if a and isinstance(a[0], JObject) else 0.0)

    def _ani_obtain(a, cls_d=ANI):
        if a and isinstance(a[0], JObject) and a[0].cls == cls_d:
            return JObject(cls_d, dict(a[0].fields))
        return JObject(cls_d, {"_source": a[0] if a else None, "_actions": 0, "_action_list": []})

    for _anic in (ANI, ANIC):
        _add(_anic, "obtain", f"(){_anic}", lambda a, c=_anic: _ani_obtain([], c))
        _add(_anic, "obtain", f"(Landroid/view/View;){_anic}", lambda a, c=_anic: _ani_obtain(a, c))
        _add(_anic, "obtain", f"({_anic}){_anic}", lambda a, c=_anic: _ani_obtain(a, c))
        _add(_anic, "addAction", "(I)V", lambda a: a[0].fields.__setitem__("_actions", int(a[0].fields.get("_actions", 0)) | int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "addAction", f"({AA})V", lambda a: a[0].fields.setdefault("_action_list", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "removeAction", f"({AA})Z", lambda a: (a[0].fields.get("_action_list", []).remove(a[1]), 1)[1] if len(a) > 1 and isinstance(a[0], JObject) and a[1] in a[0].fields.get("_action_list", []) else 0)
        _add(_anic, "getActions", "()I", lambda a: int(a[0].fields.get("_actions", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_anic, "getActionList", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(a[0].fields.get("_action_list", ())) if a and isinstance(a[0], JObject) else []}))
        for _abn in ("BoundsInParent", "BoundsInScreen", "BoundsInWindow"):
            _k = f"_{_abn}"
            _add(_anic, f"set{_abn}", "(Landroid/graphics/Rect;)V", lambda a, k=_k: a[0].fields.__setitem__(k, _rect_read(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
            _add(_anic, f"get{_abn}", "(Landroid/graphics/Rect;)V", lambda a, k=_k: _rect_write(a[1], *a[0].fields.get(k, (0, 0, 0, 0))) if len(a) > 1 and isinstance(a[0], JObject) else None)
        for _cs_prop in ("ClassName", "ContentDescription", "Error", "PackageName", "HintText", "PaneTitle", "TooltipText", "StateDescription", "ContainerTitle"):
            _k = f"_{_cs_prop}"
            _add(_anic, f"set{_cs_prop}", "(Ljava/lang/CharSequence;)V", lambda a, k=_k: a[0].fields.__setitem__(k, _txt(a[1]) if a[1] is not None else None) if len(a) > 1 and isinstance(a[0], JObject) else None)
            _add(_anic, f"get{_cs_prop}", "()Ljava/lang/CharSequence;", lambda a, k=_k: JString(a[0].fields[k]) if a and isinstance(a[0], JObject) and a[0].fields.get(k) is not None else None)
        for _str_prop in ("ViewIdResourceName", "UniqueId"):
            _k = f"_{_str_prop}"
            _add(_anic, f"set{_str_prop}", "(Ljava/lang/String;)V", lambda a, k=_k: a[0].fields.__setitem__(k, _txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
            _add(_anic, f"get{_str_prop}", "()Ljava/lang/String;", lambda a, k=_k: JString(a[0].fields[k]) if a and isinstance(a[0], JObject) and a[0].fields.get(k) is not None else None)
        for _b_set, _b_get in (
            ("setCheckable", "isCheckable"), ("setClickable", "isClickable"), ("setContextClickable", "isContextClickable"),
            ("setFocusable", "isFocusable"), ("setFocused", "isFocused"), ("setAccessibilityFocused", "isAccessibilityFocused"),
            ("setImportantForAccessibility", "isImportantForAccessibility"), ("setLongClickable", "isLongClickable"),
            ("setPassword", "isPassword"), ("setScrollable", "isScrollable"), ("setShowingHintText", "isShowingHintText"),
            ("setVisibleToUser", "isVisibleToUser"), ("setDismissable", "isDismissable"), ("setHeading", "isHeading"),
            ("setScreenReaderFocusable", "isScreenReaderFocusable"), ("setTextSelectable", "isTextSelectable"),
            ("setAccessibilityDataSensitive", "isAccessibilityDataSensitive"),
        ):
            _bk = f"_{_b_get}"
            _add(_anic, _b_set, "(Z)V", lambda a, k=_bk: a[0].fields.__setitem__(k, 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None)
            _add(_anic, _b_get, "()Z", lambda a, k=_bk: int(a[0].fields.get(k, 1 if k == "_isVisibleToUser" else 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_anic, "getExtras", "()Landroid/os/Bundle;", lambda a: a[0].fields.setdefault("_extras", JObject("Landroid/os/Bundle;", {"map": {}})) if a and isinstance(a[0], JObject) else JObject("Landroid/os/Bundle;", {"map": {}}))
        _add(_anic, "setMaxTextLength", "(I)V", lambda a: a[0].fields.__setitem__("_maxLen", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "getMaxTextLength", "()I", lambda a: int(a[0].fields.get("_maxLen", -1)) if a and isinstance(a[0], JObject) else -1)
        _add(_anic, "setMovementGranularities", "(I)V", lambda a: a[0].fields.__setitem__("_mgran", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "getMovementGranularities", "()I", lambda a: int(a[0].fields.get("_mgran", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_anic, "setCollectionInfo", f"({CI})V", lambda a: a[0].fields.__setitem__("_ci", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "getCollectionInfo", f"(){CI}", lambda a: a[0].fields.get("_ci") if a and isinstance(a[0], JObject) else None)
        _add(_anic, "setCollectionItemInfo", f"({CII})V", lambda a: a[0].fields.__setitem__("_cii", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "setRangeInfo", f"({RI})V", lambda a: a[0].fields.__setitem__("_ri", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_anic, "getRangeInfo", f"(){RI}", lambda a: a[0].fields.get("_ri") if a and isinstance(a[0], JObject) else None)
        for _vm2, _vs2, _vr2 in (
            ("addChild", "(Landroid/view/View;)V", None), ("addChild", "(Landroid/view/View;I)V", None),
            ("setParent", "(Landroid/view/View;)V", None), ("setSource", "(Landroid/view/View;)V", None),
            ("setSource", "(Landroid/view/View;I)V", None), ("setLabelFor", "(Landroid/view/View;)V", None),
            ("setLabeledBy", "(Landroid/view/View;)V", None), ("setTraversalAfter", "(Landroid/view/View;)V", None),
            ("performAction", "(ILandroid/os/Bundle;)Z", 0),
        ):
            _add(_anic, _vm2, _vs2, (lambda rv: (lambda a: rv))(_vr2))

    _add(AR, "getContentDescription", "()Ljava/lang/CharSequence;", lambda a: JString(a[0].fields["_cd"]) if a and isinstance(a[0], JObject) and "_cd" in a[0].fields else None)
    _add(AR, "getText", "()Ljava/util/List;", lambda a: a[0].fields.setdefault("_texts", JObject("Ljava/util/ArrayList;", {"__items": []})) if a and isinstance(a[0], JObject) else JObject("Ljava/util/ArrayList;", {"__items": []}))
    for _arm, _ars in (
        ("setClassName", "(Ljava/lang/CharSequence;)V"), ("setFromIndex", "(I)V"), ("setToIndex", "(I)V"),
        ("setCurrentItemIndex", "(I)V"), ("setItemCount", "(I)V"), ("setMaxScrollX", "(I)V"), ("setMaxScrollY", "(I)V"),
        ("setScrollX", "(I)V"), ("setScrollY", "(I)V"), ("setPassword", "(Z)V"), ("setScrollable", "(Z)V"),
        ("setSource", "(Landroid/view/View;)V"), ("setSource", "(Landroid/view/View;I)V"),
    ):
        _add(AR, _arm, _ars, lambda a: None)

    _add(AE, "obtain", f"(){AE}", lambda a: JObject(AE, {"_type": 0, "_ctypes": 0}))
    _add(AE, "obtain", f"(I){AE}", lambda a: JObject(AE, {"_type": int(a[0]) if a else 0, "_ctypes": 0}))
    _add(AE, "getEventType", "()I", lambda a: int(a[0].fields.get("_type", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(AE, "setEventType", "(I)V", lambda a: a[0].fields.__setitem__("_type", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(AE, "getContentChangeTypes", "()I", lambda a: int(a[0].fields.get("_ctypes", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(AE, "setContentChangeTypes", "(I)V", lambda a: a[0].fields.__setitem__("_ctypes", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(AE, "setPackageName", "(Ljava/lang/CharSequence;)V", lambda a: a[0].fields.__setitem__("_pkg", _txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    for _delc in (AD, "Landroidx/core/view/AccessibilityDelegateCompat;"):
        _add(_delc, "<init>", "()V", lambda a: a[0] if a else None)
        for _adm, _ads, _adr in (
            ("dispatchPopulateAccessibilityEvent", f"({V}{AE})Z", 0),
            ("getAccessibilityNodeProvider", f"({V})Landroid/view/accessibility/AccessibilityNodeProvider;", None),
            ("onInitializeAccessibilityEvent", f"({V}{AE})V", None),
            ("onInitializeAccessibilityNodeInfo", f"({V}{ANI})V", None),
            ("onInitializeAccessibilityNodeInfo", f"({V}{ANIC})V", None),
            ("onPopulateAccessibilityEvent", f"({V}{AE})V", None),
            ("onRequestSendAccessibilityEvent", f"({VG}{V}{AE})Z", 1),
            ("performAccessibilityAction", f"({V}ILandroid/os/Bundle;)Z", 0),
            ("sendAccessibilityEvent", f"({V}I)V", None),
            ("sendAccessibilityEventUnchecked", f"({V}{AE})V", None),
        ):
            _add(_delc, _adm, _ads, (lambda rv: (lambda a: rv))(_adr))

    # =========================================================================
    # 7. Graphics: Matrix, Path, PathMeasure, Drawables, Rect, RectF, Point, Bitmap, Fonts
    # =========================================================================
    MT = "Landroid/graphics/Matrix;"
    _add(MT, "<init>", "()V", lambda a: _mat_set(a[0], [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]) or a[0] if a else None)
    _add(MT, "<init>", f"({MT})V", lambda a: _mat_set(a[0], _mat_get(a[1])) or a[0] if len(a) > 1 else None)
    _add(MT, "reset", "()V", lambda a: _mat_set(a[0], [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]) if a else None)
    _add(MT, "set", f"({MT})V", lambda a: _mat_set(a[0], _mat_get(a[1])) if len(a) > 1 else None)
    _add(MT, "isIdentity", "()Z", lambda a: 1 if all(abs(x - y) < 1e-6 for x, y in zip(_mat_get(a[0] if a else None), (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))) else 0)

    def _m_get_vals(a):
        if len(a) > 1 and isinstance(a[1], JArray):
            m = _mat_get(a[0])
            for i in range(min(9, len(a[1].data))):
                a[1].data[i] = float(m[i])
        return None
    _add(MT, "getValues", "([F)V", _m_get_vals)

    def _m_set_vals(a):
        if len(a) > 1 and isinstance(a[1], JArray) and len(a[1].data) >= 9:
            _mat_set(a[0], [_f32(a[1].data[i]) for i in range(9)])
        return None
    _add(MT, "setValues", "([F)V", _m_set_vals)

    def _m_rot_mat(deg: float, px: float | None = None, py: float | None = None):
        rad = math.radians(deg)
        c, s = math.cos(rad), math.sin(rad)
        if px is not None and py is not None:
            return [c, -s, px - c * px + s * py, s, c, py - s * px - c * py, 0.0, 0.0, 1.0]
        return [c, -s, 0.0, s, c, 0.0, 0.0, 0.0, 1.0]

    def _m_scale_mat(sx: float, sy: float, px: float | None = None, py: float | None = None):
        if px is not None and py is not None:
            return [sx, 0.0, px - sx * px, 0.0, sy, py - sy * py, 0.0, 0.0, 1.0]
        return [sx, 0.0, 0.0, 0.0, sy, 0.0, 0.0, 0.0, 1.0]

    _add(MT, "setTranslate", "(FF)V", lambda a: _mat_set(a[0], [1.0, 0.0, _f32(a[1]), 0.0, 1.0, _f32(a[2]), 0.0, 0.0, 1.0]) if len(a) > 2 else None)
    _add(MT, "setScale", "(FF)V", lambda a: _mat_set(a[0], _m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0))) if len(a) > 2 else None)
    _add(MT, "setScale", "(FFFF)V", lambda a: _mat_set(a[0], _m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0), _f32(a[3]), _f32(a[4]))) if len(a) > 4 else None)
    _add(MT, "setRotate", "(F)V", lambda a: _mat_set(a[0], _m_rot_mat(_f32(a[1]))) if len(a) > 1 else None)
    _add(MT, "setRotate", "(FFF)V", lambda a: _mat_set(a[0], _m_rot_mat(_f32(a[1]), _f32(a[2]), _f32(a[3]))) if len(a) > 3 else None)
    _add(MT, "preTranslate", "(FF)Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), [1.0, 0.0, _f32(a[1]), 0.0, 1.0, _f32(a[2]), 0.0, 0.0, 1.0])), 1)[1] if len(a) > 2 else 0)
    _add(MT, "postTranslate", "(FF)Z", lambda a: (_mat_set(a[0], _mat_mul([1.0, 0.0, _f32(a[1]), 0.0, 1.0, _f32(a[2]), 0.0, 0.0, 1.0], _mat_get(a[0]))), 1)[1] if len(a) > 2 else 0)
    _add(MT, "preScale", "(FF)Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), _m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0)))), 1)[1] if len(a) > 2 else 0)
    _add(MT, "postScale", "(FF)Z", lambda a: (_mat_set(a[0], _mat_mul(_m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0)), _mat_get(a[0]))), 1)[1] if len(a) > 2 else 0)
    _add(MT, "preScale", "(FFFF)Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), _m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0), _f32(a[3]), _f32(a[4])))), 1)[1] if len(a) > 4 else 0)
    _add(MT, "postScale", "(FFFF)Z", lambda a: (_mat_set(a[0], _mat_mul(_m_scale_mat(_f32(a[1], 1.0), _f32(a[2], 1.0), _f32(a[3]), _f32(a[4])), _mat_get(a[0]))), 1)[1] if len(a) > 4 else 0)
    _add(MT, "preRotate", "(F)Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), _m_rot_mat(_f32(a[1])))), 1)[1] if len(a) > 1 else 0)
    _add(MT, "postRotate", "(F)Z", lambda a: (_mat_set(a[0], _mat_mul(_m_rot_mat(_f32(a[1])), _mat_get(a[0]))), 1)[1] if len(a) > 1 else 0)
    _add(MT, "preRotate", "(FFF)Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), _m_rot_mat(_f32(a[1]), _f32(a[2]), _f32(a[3])))), 1)[1] if len(a) > 3 else 0)
    _add(MT, "postRotate", "(FFF)Z", lambda a: (_mat_set(a[0], _mat_mul(_m_rot_mat(_f32(a[1]), _f32(a[2]), _f32(a[3])), _mat_get(a[0]))), 1)[1] if len(a) > 3 else 0)
    _add(MT, "preConcat", f"({MT})Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[0]), _mat_get(a[1]))), 1)[1] if len(a) > 1 else 0)
    _add(MT, "postConcat", f"({MT})Z", lambda a: (_mat_set(a[0], _mat_mul(_mat_get(a[1]), _mat_get(a[0]))), 1)[1] if len(a) > 1 else 0)

    def _m_invert(a):
        if not a:
            return 0
        inv = _mat_invert_vals(_mat_get(a[0]))
        if inv is None:
            return 0
        if len(a) > 1 and isinstance(a[1], JObject):
            _mat_set(a[1], inv)
        return 1
    _add(MT, "invert", f"({MT})Z", _m_invert)

    def _m_map_pts(a, vec_only: bool = False):
        if len(a) < 2 or not isinstance(a[1], JArray):
            return None
        m = _mat_get(a[0])
        src = a[2] if len(a) > 2 and isinstance(a[2], JArray) else a[1]
        dst = a[1]
        tx, ty = (0.0, 0.0) if vec_only else (m[2], m[5])
        n = min(len(src.data), len(dst.data))
        for i in range(0, n - 1, 2):
            x, y = _f32(src.data[i]), _f32(src.data[i + 1])
            dst.data[i] = float(m[0] * x + m[1] * y + tx)
            dst.data[i + 1] = float(m[3] * x + m[4] * y + ty)
        return None
    _add(MT, "mapPoints", "([F)V", lambda a: _m_map_pts(a, False))
    _add(MT, "mapPoints", "([F[F)V", lambda a: _m_map_pts(a, False))
    _add(MT, "mapVectors", "([F)V", lambda a: _m_map_pts(a, True))

    def _m_map_rect(a):
        if len(a) < 2:
            return 0
        m = _mat_get(a[0])
        dst = a[1]
        src = a[2] if len(a) > 2 and isinstance(a[2], JObject) else dst
        l, t_y, r, b = _rect_read(src, as_float=True)
        pts = [(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]) for x, y in ((l, t_y), (r, t_y), (r, b), (l, b))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        _rect_write(dst, min(xs), min(ys), max(xs), max(ys), as_float=True)
        return 1
    _add(MT, "mapRect", "(Landroid/graphics/RectF;)Z", _m_map_rect)
    _add(MT, "mapRect", "(Landroid/graphics/RectF;Landroid/graphics/RectF;)Z", _m_map_rect)

    # Path & PathMeasure
    PTH = "Landroid/graphics/Path;"
    PM = "Landroid/graphics/PathMeasure;"
    def _p_pts(pobj: Any) -> list[tuple[float, float]]:
        return pobj.fields.setdefault("_pts", []) if isinstance(pobj, JObject) else []

    def _p_init(a):
        if a and isinstance(a[0], JObject):
            if len(a) > 1 and isinstance(a[1], JObject):
                a[0].fields["_pts"] = list(a[1].fields.get("_pts", []))
            else:
                a[0].fields.setdefault("_pts", [])
        return a[0] if a else None
    _add(PTH, "<init>", "()V", _p_init)
    _add(PTH, "<init>", f"({PTH})V", _p_init)
    _add(PTH, "reset", "()V", lambda a: _p_pts(a[0]).clear() if a else None)
    _add(PTH, "rewind", "()V", lambda a: _p_pts(a[0]).clear() if a else None)
    _add(PTH, "moveTo", "(FF)V", lambda a: _p_pts(a[0]).append((_f32(a[1]), _f32(a[2]))) if len(a) > 2 else None)
    _add(PTH, "lineTo", "(FF)V", lambda a: _p_pts(a[0]).append((_f32(a[1]), _f32(a[2]))) if len(a) > 2 else None)
    def _p_rel(a, kind="line"):
        if not a:
            return None
        pts = _p_pts(a[0])
        lx, ly = pts[-1] if pts else (0.0, 0.0)
        if kind in ("move", "line") and len(a) > 2:
            pts.append((lx + _f32(a[1]), ly + _f32(a[2])))
        elif kind == "quad" and len(a) > 4:
            pts.append((lx + _f32(a[3]), ly + _f32(a[4])))
        elif kind == "cubic" and len(a) > 6:
            pts.append((lx + _f32(a[5]), ly + _f32(a[6])))
        return None
    _add(PTH, "rMoveTo", "(FF)V", lambda a: _p_rel(a, "move"))
    _add(PTH, "rLineTo", "(FF)V", lambda a: _p_rel(a, "line"))
    _add(PTH, "quadTo", "(FFFF)V", lambda a: _p_pts(a[0]).append((_f32(a[3]), _f32(a[4]))) if len(a) > 4 else None)
    _add(PTH, "rQuadTo", "(FFFF)V", lambda a: _p_rel(a, "quad"))
    _add(PTH, "cubicTo", "(FFFFFF)V", lambda a: _p_pts(a[0]).append((_f32(a[5]), _f32(a[6]))) if len(a) > 6 else None)
    _add(PTH, "rCubicTo", "(FFFFFF)V", lambda a: _p_rel(a, "cubic"))
    _add(PTH, "close", "()V", lambda a: (_p_pts(a[0]).append(_p_pts(a[0])[0]) if a and _p_pts(a[0]) else None))
    _add(PTH, "addRect", "(Landroid/graphics/RectF;Landroid/graphics/Path$Direction;)V",
         lambda a: (lambda l, t, r, b: _p_pts(a[0]).extend([(l, t), (r, t), (r, b), (l, b)]))(*_rect_read(a[1], True)) if len(a) > 1 else None)
    _add(PTH, "addRoundRect", "(Landroid/graphics/RectF;[FLandroid/graphics/Path$Direction;)V",
         lambda a: (lambda l, t, r, b: _p_pts(a[0]).extend([(l, t), (r, t), (r, b), (l, b)]))(*_rect_read(a[1], True)) if len(a) > 1 else None)
    _add(PTH, "offset", "(FF)V",
         lambda a: a[0].fields.__setitem__("_pts", [(x + _f32(a[1]), y + _f32(a[2])) for x, y in _p_pts(a[0])]) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PTH, "transform", f"({MT})V",
         lambda a: (lambda m: a[0].fields.__setitem__("_pts", [(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]) for x, y in _p_pts(a[0])]))(_mat_get(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PTH, "addPath", f"({PTH}{MT})V",
         lambda a: (lambda m: _p_pts(a[0]).extend([(m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]) for x, y in _p_pts(a[1])]))(_mat_get(a[2])) if len(a) > 2 else None)

    def _p_bounds(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            pts = _p_pts(a[0])
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                _rect_write(a[1], min(xs), min(ys), max(xs), max(ys), as_float=True)
            else:
                _rect_write(a[1], 0.0, 0.0, 0.0, 0.0, as_float=True)
        return None
    _add(PTH, "computeBounds", "(Landroid/graphics/RectF;Z)V", _p_bounds)
    _add(PTH, "setFillType", "(Landroid/graphics/Path$FillType;)V", lambda a: None)
    _add(PTH, "op", f"({PTH}Landroid/graphics/Path$Op;)Z", lambda a: 1)
    _add(PTH, "op", f"({PTH}{PTH}Landroid/graphics/Path$Op;)Z", lambda a: 1)

    def _pm_len(a):
        p = a[0].fields.get("_path") if a and isinstance(a[0], JObject) else None
        pts = _p_pts(p)
        tot = 0.0
        for i in range(1, len(pts)):
            tot += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        return float(tot)
    _add(PM, "<init>", f"({PTH}Z)V", lambda a: a[0].fields.__setitem__("_path", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PM, "setPath", f"({PTH}Z)V", lambda a: a[0].fields.__setitem__("_path", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PM, "getLength", "()F", _pm_len)
    def _pm_postan(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        p = a[0].fields.get("_path")
        pts = _p_pts(p)
        if not pts:
            return 0
        dist = max(0.0, _f32(a[1]) if len(a) > 1 else 0.0)
        px, py = pts[0]
        tx, ty = 1.0, 0.0
        acc = 0.0
        for i in range(1, len(pts)):
            dx, dy = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
            seg = math.hypot(dx, dy)
            if seg > 0:
                tx, ty = dx / seg, dy / seg
            if acc + seg >= dist and seg > 0:
                t = (dist - acc) / seg
                px, py = pts[i - 1][0] + t * dx, pts[i - 1][1] + t * dy
                break
            acc += seg
            px, py = pts[i]
        if len(a) > 2 and isinstance(a[2], JArray) and len(a[2].data) >= 2:
            a[2].data[0], a[2].data[1] = float(px), float(py)
        if len(a) > 3 and isinstance(a[3], JArray) and len(a[3].data) >= 2:
            a[3].data[0], a[3].data[1] = float(tx), float(ty)
        return 1
    _add(PM, "getPosTan", "(F[F[F)Z", _pm_postan)
    _add(PM, "getSegment", f"(FF{PTH}Z)Z",
         lambda a: (_p_pts(a[3]).extend(_p_pts(a[0].fields.get("_path"))) if len(a) > 3 and isinstance(a[0], JObject) else None, 1)[1])

    # Drawables
    DR = "Landroid/graphics/drawable/Drawable;"
    CS = "Landroid/graphics/drawable/Drawable$ConstantState;"
    _add(CS, "getChangingConfigurations", "()I", lambda a: 0)
    _add(CS, "canApplyTheme", "()Z", lambda a: 0)
    _add(CS, "newDrawable", f"(){DR}", lambda a: JObject(DR, dict(a[0].fields.get("_proto", {}))) if a and isinstance(a[0], JObject) else JObject(DR, {}))
    _add(CS, "newDrawable", f"(Landroid/content/res/Resources;){DR}", lambda a: JObject(DR, dict(a[0].fields.get("_proto", {}))) if a and isinstance(a[0], JObject) else JObject(DR, {}))
    _add(CS, "newDrawable", f"(Landroid/content/res/Resources;Landroid/content/res/Resources$Theme;){DR}", lambda a: JObject(DR, dict(a[0].fields.get("_proto", {}))) if a and isinstance(a[0], JObject) else JObject(DR, {}))

    def _dr_bounds_rect(dobj):
        if isinstance(dobj, JObject):
            r = dobj.fields.get("_bounds_rect")
            if isinstance(r, JObject):
                return r
            r = JObject("Landroid/graphics/Rect;", {"left": 0, "top": 0, "right": 0, "bottom": 0})
            dobj.fields["_bounds_rect"] = r
            return r
        return JObject("Landroid/graphics/Rect;", {"left": 0, "top": 0, "right": 0, "bottom": 0})

    def _dr_set_bounds(a):
        if not a or not isinstance(a[0], JObject):
            return None
        r = _dr_bounds_rect(a[0])
        if len(a) >= 5:
            _rect_write(r, int(a[1]), int(a[2]), int(a[3]), int(a[4]))
        elif len(a) > 1 and isinstance(a[1], JObject):
            _rect_write(r, *_rect_read(a[1]))
        return None
    _add(DR, "setBounds", "(IIII)V", _dr_set_bounds)
    _add(DR, "setBounds", "(Landroid/graphics/Rect;)V", _dr_set_bounds)
    _add(DR, "getBounds", "()Landroid/graphics/Rect;", lambda a: _dr_bounds_rect(a[0] if a else None))
    _add(DR, "getDirtyBounds", "()Landroid/graphics/Rect;", lambda a: _dr_bounds_rect(a[0] if a else None))
    _add(DR, "copyBounds", "(Landroid/graphics/Rect;)V", lambda a: _rect_write(a[1], *_rect_read(_dr_bounds_rect(a[0]))) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add(DR, "getIntrinsicWidth", "()I", lambda a: int(a[0].fields.get("_iw", a[0].fields.get("_bmp").fields.get("_w", 24) if isinstance(a[0].fields.get("_bmp"), JObject) else 24)) if a and isinstance(a[0], JObject) else 24)
    _add(DR, "getIntrinsicHeight", "()I", lambda a: int(a[0].fields.get("_ih", a[0].fields.get("_bmp").fields.get("_h", 24) if isinstance(a[0].fields.get("_bmp"), JObject) else 24)) if a and isinstance(a[0], JObject) else 24)
    _add(DR, "getMinimumWidth", "()I", lambda a: int(a[0].fields.get("_iw", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(DR, "getMinimumHeight", "()I", lambda a: int(a[0].fields.get("_ih", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(DR, "draw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add(DR, "setAlpha", "(I)V", lambda a: a[0].fields.__setitem__("_alpha", int(a[1]) & 0xFF) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "getAlpha", "()I", lambda a: int(a[0].fields.get("_alpha", 255)) if a and isinstance(a[0], JObject) else 255)
    _add(DR, "setColorFilter", "(Landroid/graphics/ColorFilter;)V", lambda a: a[0].fields.__setitem__("_cf", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "setColorFilter", "(ILandroid/graphics/PorterDuff$Mode;)V", lambda a: a[0].fields.__setitem__("_cf_col", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "getColorFilter", "()Landroid/graphics/ColorFilter;", lambda a: a[0].fields.get("_cf") if a and isinstance(a[0], JObject) else None)
    _add(DR, "clearColorFilter", "()V", lambda a: (a[0].fields.pop("_cf", None), a[0].fields.pop("_cf_col", None), None)[2] if a and isinstance(a[0], JObject) else None)
    _add(DR, "setTint", "(I)V", lambda a: a[0].fields.__setitem__("_tint_int", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "setTintList", "(Landroid/content/res/ColorStateList;)V", lambda a: a[0].fields.__setitem__("_tint_csl", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "setTintMode", "(Landroid/graphics/PorterDuff$Mode;)V", lambda a: a[0].fields.__setitem__("_tint_mode", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "mutate", f"(){DR}", lambda a: a[0] if a else JObject(DR, {}))
    _add(DR, "getCurrent", f"(){DR}", lambda a: a[0] if a else JObject(DR, {}))
    _add(DR, "getConstantState", f"(){CS}", lambda a: JObject(CS, {"_proto": dict(a[0].fields) if a and isinstance(a[0], JObject) else {}}))
    _add(DR, "setState", "([I)Z", lambda a: (a[0].fields.__setitem__("_state", list(a[1].data) if len(a) > 1 and isinstance(a[1], JArray) else []), 0)[1] if a and isinstance(a[0], JObject) else 0)
    _add(DR, "getState", "()[I", lambda a: JArray("[I", list(a[0].fields.get("_state", []))) if a and isinstance(a[0], JObject) else JArray("[I", []))
    _add(DR, "isStateful", "()Z", lambda a: 0)
    _add(DR, "jumpToCurrentState", "()V", lambda a: None)
    _add(DR, "setLevel", "(I)Z", lambda a: (a[0].fields.__setitem__("_level", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, 1)[1])
    _add(DR, "getLevel", "()I", lambda a: int(a[0].fields.get("_level", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(DR, "onLevelChange", "(I)Z", lambda a: 0)
    _add(DR, "setCallback", "(Landroid/graphics/drawable/Drawable$Callback;)V", lambda a: a[0].fields.__setitem__("_cb", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(DR, "getCallback", "()Landroid/graphics/drawable/Drawable$Callback;", lambda a: a[0].fields.get("_cb") if a and isinstance(a[0], JObject) else None)
    _add(DR, "invalidateSelf", "()V", lambda a: None)
    _add(DR, "scheduleSelf", "(Ljava/lang/Runnable;J)V", lambda a: None)
    _add(DR, "unscheduleSelf", "(Ljava/lang/Runnable;)V", lambda a: None)
    _add(DR, "setVisible", "(ZZ)Z", lambda a: (a[0].fields.__setitem__("_vis", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None, 1)[1])
    _add(DR, "isVisible", "()Z", lambda a: int(a[0].fields.get("_vis", 1)) if a and isinstance(a[0], JObject) else 1)
    for _dm, _dsig, _dret in (
        ("applyTheme", "(Landroid/content/res/Resources$Theme;)V", None),
        ("canApplyTheme", "()Z", 0), ("getChangingConfigurations", "()I", 0), ("setChangingConfigurations", "(I)V", None),
        ("getLayoutDirection", "()I", 0), ("setLayoutDirection", "(I)Z", 0), ("getOpacity", "()I", -3),
        ("getOutline", "(Landroid/graphics/Outline;)V", None), ("getPadding", "(Landroid/graphics/Rect;)Z", 0),
        ("getTransparentRegion", "()Landroid/graphics/Region;", None),
        ("inflate", "(Landroid/content/res/Resources;Lorg/xmlpull/v1/XmlPullParser;Landroid/util/AttributeSet;)V", None),
        ("inflate", "(Landroid/content/res/Resources;Lorg/xmlpull/v1/XmlPullParser;Landroid/util/AttributeSet;Landroid/content/res/Resources$Theme;)V", None),
        ("isAutoMirrored", "()Z", 0), ("setAutoMirrored", "(Z)V", None), ("setDither", "(Z)V", None),
        ("setFilterBitmap", "(Z)V", None), ("setHotspot", "(FF)V", None), ("setHotspotBounds", "(IIII)V", None),
        ("onBoundsChange", "(Landroid/graphics/Rect;)V", None),
    ):
        _add(DR, _dm, _dsig, (lambda rv: (lambda a: rv))(_dret))
    _add(DR, "createFromStream", f"(Ljava/io/InputStream;Ljava/lang/String;){DR}", lambda a: JObject(DR, {}))
    _add("Landroid/graphics/drawable/Drawable$Callback;", "invalidateDrawable", f"({DR})V", lambda a: None)
    _add("Landroid/graphics/drawable/Drawable$Callback;", "scheduleDrawable", f"({DR}Ljava/lang/Runnable;J)V", lambda a: None)
    _add("Landroid/graphics/drawable/Drawable$Callback;", "unscheduleDrawable", f"({DR}Ljava/lang/Runnable;)V", lambda a: None)

    LD = "Landroid/graphics/drawable/LayerDrawable;"
    _add(LD, "<init>", f"([{DR})V", lambda a: a[0].fields.update({"_layers": list(getattr(a[1], "data", ()) or ()), "_ids": {}}) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LD, "getNumberOfLayers", "()I", lambda a: len(a[0].fields.get("_layers", ())) if a and isinstance(a[0], JObject) else 0)
    _add(LD, "getDrawable", f"(I){DR}", lambda a: (lambda ls, i: ls[i] if 0 <= i < len(ls) else None)(a[0].fields.get("_layers", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LD, "setId", "(II)V", lambda a: a[0].fields.setdefault("_ids", {}).__setitem__(int(a[1]), int(a[2])) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(LD, "getId", "(I)I", lambda a: int(a[0].fields.get("_ids", {}).get(int(a[1]), -1)) if len(a) > 1 and isinstance(a[0], JObject) else -1)
    _add(LD, "findDrawableByLayerId", f"(I){DR}", lambda a: (lambda ids, ls, lid: next((ls[idx] for idx, v in ids.items() if v == lid and 0 <= idx < len(ls)), None))(a[0].fields.get("_ids", {}), a[0].fields.get("_layers", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/RippleDrawable;", "<init>", f"(Landroid/content/res/ColorStateList;{DR}{DR})V",
         lambda a: a[0].fields.update({"_layers": [d for d in (a[2] if len(a) > 2 else None, a[3] if len(a) > 3 else None) if isinstance(d, JObject)], "_ids": {}}) if a and isinstance(a[0], JObject) else None)

    ADRV = "Landroid/graphics/drawable/AnimationDrawable;"
    _add(ADRV, "<init>", "()V", lambda a: a[0].fields.update({"_frames": [], "_durs": [], "_oneshot": 0}) if a and isinstance(a[0], JObject) else None)
    _add(ADRV, "addFrame", f"({DR}I)V", lambda a: (a[0].fields.setdefault("_frames", []).append(a[1]), a[0].fields.setdefault("_durs", []).append(int(a[2])), None)[2] if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(ADRV, "getNumberOfFrames", "()I", lambda a: len(a[0].fields.get("_frames", ())) if a and isinstance(a[0], JObject) else 0)
    _add(ADRV, "getFrame", f"(I){DR}", lambda a: (lambda fs, i: fs[i] if 0 <= i < len(fs) else None)(a[0].fields.get("_frames", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(ADRV, "getDuration", "(I)I", lambda a: (lambda ds, i: int(ds[i]) if 0 <= i < len(ds) else 0)(a[0].fields.get("_durs", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else 0)
    _add(ADRV, "setOneShot", "(Z)V", lambda a: a[0].fields.__setitem__("_oneshot", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None)
    _add(ADRV, "isOneShot", "()Z", lambda a: int(a[0].fields.get("_oneshot", 0)) if a and isinstance(a[0], JObject) else 0)

    _add("Landroid/graphics/drawable/BitmapDrawable;", "<init>", "(Landroid/content/res/Resources;Landroid/graphics/Bitmap;)V",
         lambda a: a[0].fields.__setitem__("_bmp", a[2]) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/BitmapDrawable;", "<init>", "(Landroid/graphics/Bitmap;)V",
         lambda a: a[0].fields.__setitem__("_bmp", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/BitmapDrawable;", "getBitmap", "()Landroid/graphics/Bitmap;",
         lambda a: a[0].fields.get("_bmp") if a and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/BitmapDrawable;", "getPaint", "()Landroid/graphics/Paint;",
         lambda a: a[0].fields.setdefault("_paint", JObject("Landroid/graphics/Paint;", {})) if a and isinstance(a[0], JObject) else JObject("Landroid/graphics/Paint;", {}))
    _add("Landroid/graphics/drawable/ColorDrawable;", "<init>", "(I)V",
         lambda a: a[0].fields.__setitem__("_color", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/ColorDrawable;", "getColor", "()I",
         lambda a: int(a[0].fields.get("_color", 0)) if a and isinstance(a[0], JObject) else 0)
    _add("Landroid/graphics/drawable/ColorDrawable;", "setColor", "(I)V",
         lambda a: a[0].fields.__setitem__("_color", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/drawable/ClipDrawable;", "<init>", f"({DR}II)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/drawable/ShapeDrawable;", "<init>", "(Landroid/graphics/drawable/shapes/Shape;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/drawable/ShapeDrawable;", "getPaint", "()Landroid/graphics/Paint;",
         lambda a: a[0].fields.setdefault("_paint", JObject("Landroid/graphics/Paint;", {})) if a and isinstance(a[0], JObject) else JObject("Landroid/graphics/Paint;", {}))
    _add("Landroid/graphics/drawable/shapes/RoundRectShape;", "<init>", "([FLandroid/graphics/RectF;[F)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/drawable/Animatable;", "stop", "()V", lambda a: None)
    _add("Landroid/graphics/drawable/AnimatedVectorDrawable;", "isRunning", "()Z", lambda a: 0)
    _add("Landroid/graphics/drawable/AnimatedVectorDrawable;", "stop", "()V", lambda a: None)
    _add("Landroid/graphics/drawable/AnimatedVectorDrawable;", "registerAnimationCallback", "(Landroid/graphics/drawable/Animatable2$AnimationCallback;)V", lambda a: None)
    _add("Landroid/graphics/drawable/AnimatedVectorDrawable;", "unregisterAnimationCallback", "(Landroid/graphics/drawable/Animatable2$AnimationCallback;)Z", lambda a: 1)

    ICN = "Landroid/graphics/drawable/Icon;"
    for _icn_m, _icn_s, _icn_t in (
        ("createWithBitmap", "(Landroid/graphics/Bitmap;)Landroid/graphics/drawable/Icon;", 1),
        ("createWithAdaptiveBitmap", "(Landroid/graphics/Bitmap;)Landroid/graphics/drawable/Icon;", 5),
        ("createWithData", "([BII)Landroid/graphics/drawable/Icon;", 3),
        ("createWithResource", "(Ljava/lang/String;I)Landroid/graphics/drawable/Icon;", 2),
        ("createWithContentUri", "(Ljava/lang/String;)Landroid/graphics/drawable/Icon;", 4),
    ):
        _add(ICN, _icn_m, _icn_s, lambda a, t=_icn_t: JObject(ICN, {"_type": t, "_res_id": int(a[1]) if t == 2 and len(a) > 1 and isinstance(a[1], int) else 0, "_pkg": _txt(a[0]) if t == 2 and a else ""}))
    _add(ICN, "getType", "()I", lambda a: int(a[0].fields.get("_type", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(ICN, "getResId", "()I", lambda a: int(a[0].fields.get("_res_id", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(ICN, "getResPackage", "()Ljava/lang/String;", lambda a: JString(str(a[0].fields.get("_pkg", ""))) if a and isinstance(a[0], JObject) else JString(""))
    _add(ICN, "getUri", "()Landroid/net/Uri;", lambda a: a[0].fields.get("_uri") if a and isinstance(a[0], JObject) else None)
    _add(ICN, "setTintList", f"(Landroid/content/res/ColorStateList;){ICN}", lambda a: a[0] if a else None)
    _add(ICN, "setTintMode", f"(Landroid/graphics/PorterDuff$Mode;){ICN}", lambda a: a[0] if a else None)

    # Rect / RectF / Point / PointF
    RC = "Landroid/graphics/Rect;"
    RCF = "Landroid/graphics/RectF;"
    PT_C = "Landroid/graphics/Point;"
    PTF_C = "Landroid/graphics/PointF;"

    _add(RC, "<init>", f"({RC})V", lambda a: _rect_write(a[0], *_rect_read(a[1])) if len(a) > 1 else None)
    _add(RC, "set", "(IIII)V", lambda a: _rect_write(a[0], a[1], a[2], a[3], a[4]) if len(a) > 4 else None)
    _add(RC, "set", f"({RC})V", lambda a: _rect_write(a[0], *_rect_read(a[1])) if len(a) > 1 else None)
    _add(RC, "setEmpty", "()V", lambda a: _rect_write(a[0], 0, 0, 0, 0) if a else None)
    _add(RC, "isEmpty", "()Z", lambda a: (lambda l, t, r, b: 1 if (l >= r or t >= b) else 0)(*_rect_read(a[0])) if a else 1)
    _add(RC, "centerX", "()I", lambda a: (lambda l, t, r, b: (l + r) >> 1)(*_rect_read(a[0])) if a else 0)
    _add(RC, "centerY", "()I", lambda a: (lambda l, t, r, b: (t + b) >> 1)(*_rect_read(a[0])) if a else 0)
    _add(RC, "exactCenterX", "()F", lambda a: (lambda l, t, r, b: (l + r) * 0.5)(*_rect_read(a[0])) if a else 0.0)
    _add(RC, "exactCenterY", "()F", lambda a: (lambda l, t, r, b: (t + b) * 0.5)(*_rect_read(a[0])) if a else 0.0)
    _add(RC, "inset", "(II)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], l + int(a[1]), t + int(a[2]), r - int(a[1]), b - int(a[2])))(*_rect_read(a[0])) if len(a) > 2 else None)
    _add(RC, "offset", "(II)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], l + int(a[1]), t + int(a[2]), r + int(a[1]), b + int(a[2])))(*_rect_read(a[0])) if len(a) > 2 else None)
    _add(RC, "offsetTo", "(II)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], int(a[1]), int(a[2]), int(a[1]) + (r - l), int(a[2]) + (b - t)))(*_rect_read(a[0])) if len(a) > 2 else None)
    _add(RC, "sort", "()V", lambda a: (lambda l, t, r, b: _rect_write(a[0], min(l, r), min(t, b), max(l, r), max(t, b)))(*_rect_read(a[0])) if a else None)
    _add(RC, "contains", "(II)Z", lambda a: (lambda l, t, r, b: 1 if (l < r and t < b and l <= int(a[1]) < r and t <= int(a[2]) < b) else 0)(*_rect_read(a[0])) if len(a) > 2 else 0)
    _add(RC, "contains", f"({RC})Z", lambda a: (lambda l, t, r, b, l2, t2, r2, b2: 1 if (l < r and t < b and l <= l2 and t <= t2 and r >= r2 and b >= b2) else 0)(*_rect_read(a[0]), *_rect_read(a[1])) if len(a) > 1 else 0)
    def _rc_intersect(a, l2, t2, r2, b2, as_f=False):
        l1, t1, r1, b1 = _rect_read(a[0], as_f)
        nl, nt, nr, nb = max(l1, l2), max(t1, t2), min(r1, r2), min(b1, b2)
        if nl < nr and nt < nb:
            _rect_write(a[0], nl, nt, nr, nb, as_f)
            return 1
        return 0
    _add(RC, "intersect", "(IIII)Z", lambda a: _rc_intersect(a, int(a[1]), int(a[2]), int(a[3]), int(a[4]), False) if len(a) > 4 else 0)
    _add(RC, "intersect", f"({RC})Z", lambda a: _rc_intersect(a, *_rect_read(a[1], False), False) if len(a) > 1 else 0)
    _add(RC, "intersects", "(IIII)Z", lambda a: (lambda l1, t1, r1, b1: 1 if (l1 < int(a[3]) and int(a[1]) < r1 and t1 < int(a[4]) and int(a[2]) < b1) else 0)(*_rect_read(a[0])) if len(a) > 4 else 0)
    _add(RC, "intersects", f"({RC}{RC})Z", lambda a: (lambda l1, t1, r1, b1, l2, t2, r2, b2: 1 if (l1 < r2 and l2 < r1 and t1 < b2 and t2 < b1) else 0)(*_rect_read(a[0]), *_rect_read(a[1])) if len(a) > 1 else 0)
    _add(RC, "union", f"({RC})V", lambda a: (lambda l1, t1, r1, b1, l2, t2, r2, b2: _rect_write(a[0], min(l1, l2), min(t1, t2), max(r1, r2), max(b1, b2)))(*_rect_read(a[0]), *_rect_read(a[1])) if len(a) > 1 else None)
    _add(RC, "toShortString", "()Ljava/lang/String;", lambda a: JString("[%d,%d][%d,%d]" % _rect_read(a[0])) if a else JString("[0,0][0,0]"))

    _add(RCF, "<init>", f"({RC})V", lambda a: _rect_write(a[0], *_rect_read(a[1], False), True) if len(a) > 1 else None)
    _add(RCF, "<init>", f"({RCF})V", lambda a: _rect_write(a[0], *_rect_read(a[1], True), True) if len(a) > 1 else None)
    _add(RCF, "set", "(FFFF)V", lambda a: _rect_write(a[0], _f32(a[1]), _f32(a[2]), _f32(a[3]), _f32(a[4]), True) if len(a) > 4 else None)
    _add(RCF, "set", f"({RC})V", lambda a: _rect_write(a[0], *_rect_read(a[1], False), True) if len(a) > 1 else None)
    _add(RCF, "set", f"({RCF})V", lambda a: _rect_write(a[0], *_rect_read(a[1], True), True) if len(a) > 1 else None)
    _add(RCF, "setEmpty", "()V", lambda a: _rect_write(a[0], 0.0, 0.0, 0.0, 0.0, True) if a else None)
    _add(RCF, "isEmpty", "()Z", lambda a: (lambda l, t, r, b: 1 if (l >= r or t >= b) else 0)(*_rect_read(a[0], True)) if a else 1)
    _add(RCF, "centerX", "()F", lambda a: (lambda l, t, r, b: float((l + r) * 0.5))(*_rect_read(a[0], True)) if a else 0.0)
    _add(RCF, "centerY", "()F", lambda a: (lambda l, t, r, b: float((t + b) * 0.5))(*_rect_read(a[0], True)) if a else 0.0)
    _add(RCF, "contains", "(FF)Z", lambda a: (lambda l, t, r, b: 1 if (l < r and t < b and l <= _f32(a[1]) < r and t <= _f32(a[2]) < b) else 0)(*_rect_read(a[0], True)) if len(a) > 2 else 0)
    _add(RCF, "inset", "(FF)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], l + _f32(a[1]), t + _f32(a[2]), r - _f32(a[1]), b - _f32(a[2]), True))(*_rect_read(a[0], True)) if len(a) > 2 else None)
    _add(RCF, "offset", "(FF)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], l + _f32(a[1]), t + _f32(a[2]), r + _f32(a[1]), b + _f32(a[2]), True))(*_rect_read(a[0], True)) if len(a) > 2 else None)
    _add(RCF, "offsetTo", "(FF)V", lambda a: (lambda l, t, r, b: _rect_write(a[0], _f32(a[1]), _f32(a[2]), _f32(a[1]) + (r - l), _f32(a[2]) + (b - t), True))(*_rect_read(a[0], True)) if len(a) > 2 else None)
    _add(RCF, "sort", "()V", lambda a: (lambda l, t, r, b: _rect_write(a[0], min(l, r), min(t, b), max(l, r), max(t, b), True))(*_rect_read(a[0], True)) if a else None)
    _add(RCF, "intersect", f"({RCF})Z", lambda a: _rc_intersect(a, *_rect_read(a[1], True), True) if len(a) > 1 else 0)
    _add(RCF, "intersects", "(FFFF)Z", lambda a: (lambda l1, t1, r1, b1: 1 if (l1 < _f32(a[3]) and _f32(a[1]) < r1 and t1 < _f32(a[4]) and _f32(a[2]) < b1) else 0)(*_rect_read(a[0], True)) if len(a) > 4 else 0)
    _add(RCF, "union", f"({RCF})V", lambda a: (lambda l1, t1, r1, b1, l2, t2, r2, b2: _rect_write(a[0], min(l1, l2), min(t1, t2), max(r1, r2), max(b1, b2), True))(*_rect_read(a[0], True), *_rect_read(a[1], True)) if len(a) > 1 else None)
    _add(RCF, "round", f"({RC})V", lambda a: _rect_write(a[1], *(int(round(v)) for v in _rect_read(a[0], True)), False) if len(a) > 1 else None)
    _add(RCF, "roundOut", f"({RC})V", lambda a: (lambda l, t, r, b: _rect_write(a[1], int(math.floor(l)), int(math.floor(t)), int(math.ceil(r)), int(math.ceil(b)), False))(*_rect_read(a[0], True)) if len(a) > 1 else None)

    _add(PT_C, "<init>", "()V", lambda a: a[0].fields.update({"x": 0, "y": 0}) if a and isinstance(a[0], JObject) else None)
    _add(PT_C, "<init>", "(II)V", lambda a: a[0].fields.update({"x": int(a[1]), "y": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PT_C, "set", "(II)V", lambda a: a[0].fields.update({"x": int(a[1]), "y": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PT_C, "offset", "(II)V", lambda a: a[0].fields.update({"x": int(a[0].fields.get("x", 0)) + int(a[1]), "y": int(a[0].fields.get("y", 0)) + int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PTF_C, "<init>", "()V", lambda a: a[0].fields.update({"x": 0.0, "y": 0.0}) if a and isinstance(a[0], JObject) else None)
    _add(PTF_C, "<init>", "(FF)V", lambda a: a[0].fields.update({"x": _f32(a[1]), "y": _f32(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PTF_C, "set", "(FF)V", lambda a: a[0].fields.update({"x": _f32(a[1]), "y": _f32(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(PTF_C, "offset", "(FF)V", lambda a: a[0].fields.update({"x": _f32(a[0].fields.get("x", 0.0)) + _f32(a[1]), "y": _f32(a[0].fields.get("y", 0.0)) + _f32(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)

    # Bitmap & Color / Shader / Outline / Picture / Region / Typeface / Fonts
    BM = "Landroid/graphics/Bitmap;"

    def _bm_crop(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 5:
            return JObject(BM, {"_w": 64, "_h": 64, "_bytes": b"", "_mut": 1, "_alpha": 1})
        src = a[0]
        cx = max(0, int(a[1] or 0))
        cy = max(0, int(a[2] or 0))
        cw = max(1, int(a[3] or 1))
        ch = max(1, int(a[4] or 1))
        sw = max(1, int(src.fields.get("_w", cw) or cw))
        sh = max(1, int(src.fields.get("_h", ch) or ch))
        src_px = _ensure_bitmap_pixels(src) if ("_pixels" in src.fields or src.fields.get("_bytes") or "_erase" in src.fields) else None
        out_fields: dict[str, Any] = {"_w": cw, "_h": ch, "_bytes": b"", "_mut": 1, "_alpha": 1, "_cfg": src.fields.get("_cfg")}
        if isinstance(src_px, list) and len(src_px) >= sw * sh and cw * ch <= 1_048_576:
            cropped: list[int] = [0] * (cw * ch)
            max_w = max(0, min(cw, sw - cx))
            max_h = max(0, min(ch, sh - cy))
            for ry in range(max_h):
                s_off = (cy + ry) * sw + cx
                d_off = ry * cw
                cropped[d_off:d_off + max_w] = src_px[s_off:s_off + max_w]
            out_fields["_pixels"] = cropped
        elif src.fields.get("_drawable") is not None:
            out_fields["_drawable"] = copy.deepcopy(src.fields["_drawable"])
        out_bm = JObject(BM, out_fields)
        if "_pixels" in out_fields:
            encode_bitmap_png(out_bm)
        return out_bm

    def _bm_from_int_arr(a, has_offset_stride: bool = False):
        if len(a) < (6 if has_offset_stride else 3):
            return JObject(BM, {"_w": 64, "_h": 64, "_mut": 1})
        raw_data = list(getattr(a[0], "data", ()) or ())
        if has_offset_stride:
            off = int(a[1] or 0)
            stride = int(a[2] or 0)
            bw = max(1, int(a[3] or 1))
            bh = max(1, int(a[4] or 1))
            cfg = a[5]
            pxs = [0] * (bw * bh)
            st = stride if stride != 0 else bw
            for ry in range(bh):
                s0 = off + ry * st
                if 0 <= s0 < len(raw_data):
                    sl = raw_data[s0:min(len(raw_data), s0 + bw)]
                    pxs[ry * bw:ry * bw + len(sl)] = [_s32_px(x) for x in sl]
        else:
            bw = max(1, int(a[1] or 1))
            bh = max(1, int(a[2] or 1))
            cfg = a[3] if len(a) > 3 else None
            need = bw * bh
            pxs = [_s32_px(x) for x in raw_data[:need]]
            if len(pxs) < need:
                pxs.extend([0] * (need - len(pxs)))
        bm = JObject(BM, {"_w": bw, "_h": bh, "_pixels": pxs, "_mut": 1, "_alpha": 1, "_cfg": cfg})
        encode_bitmap_png(bm)
        return bm

    def _bm_scale(a):
        if len(a) < 3 or not isinstance(a[0], JObject):
            return JObject(BM, {"_w": 64, "_h": 64, "_bytes": b"", "_mut": 1})
        src = a[0]
        nw = max(1, int(a[1] or 1))
        nh = max(1, int(a[2] or 1))
        bm = JObject(BM, {"_w": nw, "_h": nh, "_bytes": b"", "_mut": 1, "_alpha": int(src.fields.get("_alpha", 1)), "_cfg": src.fields.get("_cfg")})
        raw = src.fields.get("_bytes") or (encode_bitmap_png(src) if "_pixels" in src.fields else b"")
        if raw:
            try:
                import io as _io_sc
                from PIL import Image
                filt = Image.BILINEAR if (len(a) > 3 and a[3]) else Image.NEAREST
                im = Image.open(_io_sc.BytesIO(bytes(raw))).convert("RGBA").resize((nw, nh), filt)
                buf = _io_sc.BytesIO()
                im.save(buf, format="PNG")
                bm.fields["_bytes"] = buf.getvalue()
            except Exception:
                bm.fields["_bytes"] = bytes(raw)
        elif src.fields.get("_drawable") is not None:
            bm.fields["_drawable"] = copy.deepcopy(src.fields["_drawable"])
        return bm

    def _bm_copy(a):
        if not a or not isinstance(a[0], JObject):
            return JObject(BM, {"_w": 64, "_h": 64})
        src = a[0]
        f = {
            "_w": int(src.fields.get("_w", 64)),
            "_h": int(src.fields.get("_h", 64)),
            "_mut": 1 if (len(a) > 2 and a[2]) else 0,
            "_cfg": a[1] if len(a) > 1 else src.fields.get("_cfg"),
            "_alpha": int(src.fields.get("_alpha", 1)),
        }
        if isinstance(src.fields.get("_pixels"), list):
            f["_pixels"] = list(src.fields["_pixels"])
        if src.fields.get("_bytes"):
            f["_bytes"] = bytes(src.fields["_bytes"])
        if src.fields.get("_drawable") is not None:
            f["_drawable"] = copy.deepcopy(src.fields["_drawable"])
        return JObject(BM, f)

    def _bm_erase(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            col = _s32_px(a[1])
            a[0].fields["_erase"] = col
            bw = max(1, int(a[0].fields.get("_w", 64) or 64))
            bh = max(1, int(a[0].fields.get("_h", 64) or 64))
            if bw * bh <= 1_048_576:
                a[0].fields["_pixels"] = [col] * (bw * bh)
            a[0].fields.pop("_bytes", None)
        return None

    def _bm_get_pixel(a):
        if len(a) > 2 and isinstance(a[0], JObject):
            bw = max(1, int(a[0].fields.get("_w", 64) or 64))
            bh = max(1, int(a[0].fields.get("_h", 64) or 64))
            px, py = int(a[1] or 0), int(a[2] or 0)
            pxs = a[0].fields.get("_pixels")
            if isinstance(pxs, list) and 0 <= px < bw and 0 <= py < bh:
                idx = py * bw + px
                if idx < len(pxs):
                    return _s32_px(pxs[idx])
            return _s32_px(a[0].fields.get("_erase", 0))
        return 0

    def _bm_set_pixel(a):
        if len(a) > 3 and isinstance(a[0], JObject):
            pxs = _ensure_bitmap_pixels(a[0])
            bw = max(1, int(a[0].fields.get("_w", 64) or 64))
            bh = max(1, int(a[0].fields.get("_h", 64) or 64))
            px, py = int(a[1] or 0), int(a[2] or 0)
            if pxs is not None and 0 <= px < bw and 0 <= py < bh:
                pxs[py * bw + px] = _s32_px(a[3])
                a[0].fields.pop("_bytes", None)
        return None

    def _bm_set_pixels(a):
        if len(a) >= 8 and isinstance(a[0], JObject) and isinstance(a[1], JArray):
            bmp = a[0]
            src_data = a[1].data
            off = int(a[2] or 0)
            stride = int(a[3] or 0)
            x0 = int(a[4] or 0)
            y0 = int(a[5] or 0)
            rw = int(a[6] or 0)
            rh = int(a[7] or 0)
            bw = max(1, int(bmp.fields.get("_w", 64) or 64))
            bh = max(1, int(bmp.fields.get("_h", 64) or 64))
            pxs = _ensure_bitmap_pixels(bmp)
            if pxs is not None and rw > 0 and rh > 0:
                st = stride if stride != 0 else rw
                if st > rw and off + (rh - 1) * st + rw > len(src_data) and off + rw * rh <= len(src_data):
                    st = rw
                for ry in range(rh):
                    by = y0 + ry
                    if by < 0 or by >= bh:
                        continue
                    cx0 = max(0, x0)
                    cx1 = min(bw, x0 + rw)
                    if cx1 <= cx0:
                        continue
                    dx = cx0 - x0
                    cnt = cx1 - cx0
                    s_idx = off + ry * st + dx
                    if 0 <= s_idx < len(src_data):
                        chunk = src_data[s_idx:min(len(src_data), s_idx + cnt)]
                        d_idx = by * bw + cx0
                        pxs[d_idx:d_idx + len(chunk)] = [_s32_px(v) for v in chunk]
                bmp.fields.pop("_bytes", None)
        return None

    def _bm_get_pixels(a):
        if len(a) >= 8 and isinstance(a[0], JObject) and isinstance(a[1], JArray):
            bmp = a[0]
            dst_data = a[1].data
            off = int(a[2] or 0)
            stride = int(a[3] or 0)
            x0 = int(a[4] or 0)
            y0 = int(a[5] or 0)
            rw = int(a[6] or 0)
            rh = int(a[7] or 0)
            bw = max(1, int(bmp.fields.get("_w", 64) or 64))
            bh = max(1, int(bmp.fields.get("_h", 64) or 64))
            pxs = _ensure_bitmap_pixels(bmp)
            if pxs is not None and rw > 0 and rh > 0:
                st = stride if stride != 0 else rw
                if st > rw and off + (rh - 1) * st + rw > len(dst_data) and off + rw * rh <= len(dst_data):
                    st = rw
                for ry in range(rh):
                    by = y0 + ry
                    if by < 0 or by >= bh:
                        continue
                    cx0 = max(0, x0)
                    cx1 = min(bw, x0 + rw)
                    if cx1 <= cx0:
                        continue
                    dx = cx0 - x0
                    cnt = cx1 - cx0
                    d_idx = off + ry * st + dx
                    s_idx = by * bw + cx0
                    if 0 <= d_idx < len(dst_data) and 0 <= s_idx < len(pxs):
                        lim = min(cnt, len(dst_data) - d_idx, len(pxs) - s_idx)
                        dst_data[d_idx:d_idx + lim] = [_s32_px(v) for v in pxs[s_idx:s_idx + lim]]
        return None

    _add(BM, "createBitmap", f"(IILandroid/graphics/Bitmap$Config;Z){BM}",
         lambda a: JObject(BM, {"_w": max(1, int(a[0] if a else 64)), "_h": max(1, int(a[1] if len(a) > 1 else 64)), "_bytes": b"", "_mut": 1, "_alpha": 1 if (len(a) <= 3 or a[3]) else 0, "_cfg": a[2] if len(a) > 2 else None}))
    _add(BM, "createBitmap", f"({BM}IIII){BM}", _bm_crop)
    _add(BM, "createBitmap", f"({BM}IIII{MT}Z){BM}", _bm_crop)
    _add(BM, "createBitmap", f"([IIILandroid/graphics/Bitmap$Config;){BM}", lambda a: _bm_from_int_arr(a, has_offset_stride=False))
    _add(BM, "createBitmap", f"([IIIIILandroid/graphics/Bitmap$Config;){BM}", lambda a: _bm_from_int_arr(a, has_offset_stride=True))
    _add(BM, "createBitmap", f"(Landroid/graphics/Picture;){BM}", lambda a: JObject(BM, {"_w": 64, "_h": 64, "_bytes": b""}))
    _add(BM, "createScaledBitmap", f"({BM}IIZ){BM}", _bm_scale)
    _add(BM, "copy", f"(Landroid/graphics/Bitmap$Config;Z){BM}", _bm_copy)
    _add(BM, "getConfig", "()Landroid/graphics/Bitmap$Config;", lambda a: a[0].fields.get("_cfg") or JObject("Landroid/graphics/Bitmap$Config;", {"__ename": "ARGB_8888"}) if a and isinstance(a[0], JObject) else JObject("Landroid/graphics/Bitmap$Config;", {"__ename": "ARGB_8888"}))
    _add(BM, "hasAlpha", "()Z", lambda a: int(a[0].fields.get("_alpha", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(BM, "setHasAlpha", "(Z)V", lambda a: a[0].fields.__setitem__("_alpha", 1 if len(a) > 1 and a[1] else 0) if a and isinstance(a[0], JObject) else None)
    _add(BM, "eraseColor", "(I)V", _bm_erase)
    _add(BM, "getPixel", "(II)I", _bm_get_pixel)
    _add(BM, "setPixel", "(III)V", _bm_set_pixel)
    _add(BM, "getPixels", "([IIIIIII)V", _bm_get_pixels)
    _add(BM, "setPixels", "([IIIIIII)V", _bm_set_pixels)
    _add(BM, "getRowBytes", "()I", lambda a: int(a[0].fields.get("_w", 64)) * 4 if a and isinstance(a[0], JObject) else 256)
    _add(BM, "getByteCount", "()I", lambda a: int(a[0].fields.get("_w", 64)) * int(a[0].fields.get("_h", 64)) * 4 if a and isinstance(a[0], JObject) else 16384)
    _add(BM, "getAllocationByteCount", "()I", lambda a: int(a[0].fields.get("_w", 64)) * int(a[0].fields.get("_h", 64)) * 4 if a and isinstance(a[0], JObject) else 16384)
    _add(BM, "getDensity", "()I", lambda a: int(a[0].fields.get("_density", 440)) if a and isinstance(a[0], JObject) else 440)
    _add(BM, "setDensity", "(I)V", lambda a: a[0].fields.__setitem__("_density", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(BM, "isMutable", "()Z", lambda a: int(a[0].fields.get("_mut", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(BM, "isRecycled", "()Z", lambda a: int(a[0].fields.get("_recycled", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(BM, "recycle", "()V", lambda a: a[0].fields.__setitem__("_recycled", 1) if a and isinstance(a[0], JObject) else None)
    _add("Landroid/graphics/BitmapFactory;", "decodeStream", f"(Ljava/io/InputStream;Landroid/graphics/Rect;Landroid/graphics/BitmapFactory$Options;){BM}",
         lambda a: JObject(BM, {"_w": 64, "_h": 64, "_bytes": b""}))

    def _parse_color(a):
        s = _txt(a[0] if a else "").strip().lower()
        named = {"black": 0xFF000000, "white": 0xFFFFFFFF, "red": 0xFFFF0000, "green": 0xFF00FF00, "blue": 0xFF0000FF,
                 "transparent": 0x00000000, "gray": 0xFF888888, "dkgray": 0xFF444444, "ltgray": 0xFFCCCCCC,
                 "cyan": 0xFF00FFFF, "magenta": 0xFFFF00FF, "yellow": 0xFFFFFF00}
        if s in named:
            v = named[s]
        elif s.startswith("#"):
            hx = s[1:]
            if len(hx) == 6:
                v = 0xFF000000 | int(hx, 16)
            elif len(hx) == 8:
                v = int(hx, 16)
            else:
                v = 0xFF000000
        else:
            v = 0xFF000000
        return v - 0x100000000 if (v & 0x80000000) else v
    _add("Landroid/graphics/Color;", "parseColor", "(Ljava/lang/String;)I", _parse_color)
    _add("Landroid/graphics/ColorMatrix;", "<init>", "()V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/ColorMatrix;", "setSaturation", "(F)V", lambda a: None)
    _add("Landroid/graphics/ColorMatrixColorFilter;", "<init>", "(Landroid/graphics/ColorMatrix;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/ColorSpace;", "get", "(Landroid/graphics/ColorSpace$Named;)Landroid/graphics/ColorSpace;", lambda a: JObject("Landroid/graphics/ColorSpace;", {}))
    _add("Landroid/graphics/BlurMaskFilter;", "<init>", "(FLandroid/graphics/BlurMaskFilter$Blur;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/BitmapShader;", "<init>", "(Landroid/graphics/Bitmap;Landroid/graphics/Shader$TileMode;Landroid/graphics/Shader$TileMode;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/LinearGradient;", "<init>", "(FFFF[I[FLandroid/graphics/Shader$TileMode;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/RadialGradient;", "<init>", "(FFF[I[FLandroid/graphics/Shader$TileMode;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/SweepGradient;", "<init>", "(FF[I[F)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/Shader;", "setLocalMatrix", f"({MT})V", lambda a: None)
    _add("Landroid/graphics/PorterDuff$Mode;", "valueOf", "(Ljava/lang/String;)Landroid/graphics/PorterDuff$Mode;",
         lambda a: JObject("Landroid/graphics/PorterDuff$Mode;", {"__ename": _txt(a[0]) if a else "SRC_IN"}))
    _add("Landroid/graphics/PorterDuffXfermode;", "<init>", "(Landroid/graphics/PorterDuff$Mode;)V", lambda a: a[0] if a else None)
    _add("Landroid/graphics/Outline;", "setRoundRect", "(Landroid/graphics/Rect;F)V", lambda a: None)
    _add("Landroid/graphics/Outline;", "setConvexPath", f"({PTH})V", lambda a: None)
    _add("Landroid/graphics/Picture;", "beginRecording", "(II)Landroid/graphics/Canvas;", lambda a: JObject("Landroid/graphics/Canvas;", {"_w": int(a[1]) if len(a) > 1 else 380, "_h": int(a[2]) if len(a) > 2 else 800}))
    _add("Landroid/graphics/Picture;", "endRecording", "()V", lambda a: None)
    _add("Landroid/graphics/Region;", "set", f"({RC})Z", lambda a: 1)
    _add("Landroid/graphics/Region;", "op", "(Landroid/graphics/Region;Landroid/graphics/Region$Op;)Z", lambda a: 1)

    TF = "Landroid/graphics/Typeface;"
    TFB = "Landroid/graphics/Typeface$Builder;"
    TFCB = "Landroid/graphics/Typeface$CustomFallbackBuilder;"
    _add(TF, "create", f"({TF}I){TF}", lambda a: JObject(TF, {"_style": int(a[1]) if len(a) > 1 else 0}))
    _add(TF, "create", f"({TF}IZ){TF}", lambda a: JObject(TF, {"_style": int(a[1]) if len(a) > 1 else 0}))
    _add(TF, "create", f"(Ljava/lang/String;I){TF}", lambda a: JObject(TF, {"_family": _txt(a[0]), "_style": int(a[1]) if len(a) > 1 else 0}))
    _add(TF, "createFromAsset", f"(Landroid/content/res/AssetManager;Ljava/lang/String;){TF}", lambda a: JObject(TF, {"_asset": _txt(a[1]) if len(a) > 1 else ""}))
    _add(TF, "createFromFile", f"(Ljava/io/File;){TF}", lambda a: JObject(TF, {}))
    _add(TF, "createFromFile", f"(Ljava/lang/String;){TF}", lambda a: JObject(TF, {}))
    _add(TF, "defaultFromStyle", f"(I){TF}", lambda a: JObject(TF, {"_style": int(a[0]) if a else 0}))
    _add(TF, "getStyle", "()I", lambda a: int(a[0].fields.get("_style", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(TF, "isItalic", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and (int(a[0].fields.get("_style", 0)) & 2)) else 0)
    _add(TFB, "<init>", "(Ljava/io/FileDescriptor;)V", lambda a: a[0] if a else None)
    _add(TFB, "setItalic", f"(Z){TFB}", lambda a: a[0] if a else None)
    _add(TFB, "setWeight", f"(I){TFB}", lambda a: a[0] if a else None)
    _add(TFB, "build", f"(){TF}", lambda a: JObject(TF, {"_style": 0}))
    _add(TFCB, "<init>", "(Landroid/graphics/fonts/FontFamily;)V", lambda a: a[0] if a else None)
    _add(TFCB, "addCustomFallback", f"(Landroid/graphics/fonts/FontFamily;){TFCB}", lambda a: a[0] if a else None)
    _add(TFCB, "setStyle", f"(Landroid/graphics/fonts/FontStyle;){TFCB}", lambda a: a[0] if a else None)
    _add(TFCB, "build", f"(){TF}", lambda a: JObject(TF, {"_style": 0}))

    FNT = "Landroid/graphics/fonts/Font;"
    FNTB = "Landroid/graphics/fonts/Font$Builder;"
    FF = "Landroid/graphics/fonts/FontFamily;"
    FFB = "Landroid/graphics/fonts/FontFamily$Builder;"
    FS = "Landroid/graphics/fonts/FontStyle;"
    _add(FNTB, "<init>", "(Landroid/content/res/Resources;I)V", lambda a: a[0] if a else None)
    _add(FNTB, "<init>", "(Landroid/os/ParcelFileDescriptor;)V", lambda a: a[0] if a else None)
    _add(FNTB, "setWeight", f"(I){FNTB}", lambda a: (a[0].fields.__setitem__("_w", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(FNTB, "setSlant", f"(I){FNTB}", lambda a: (a[0].fields.__setitem__("_sl", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(FNTB, "setTtcIndex", f"(I){FNTB}", lambda a: a[0] if a else None)
    _add(FNTB, "setFontVariationSettings", f"(Ljava/lang/String;){FNTB}", lambda a: a[0] if a else None)
    _add(FNTB, "build", f"(){FNT}", lambda a: JObject(FNT, {"_w": int(a[0].fields.get("_w", 400)) if a and isinstance(a[0], JObject) else 400, "_sl": int(a[0].fields.get("_sl", 0)) if a and isinstance(a[0], JObject) else 0}))
    _add(FNT, "getStyle", f"(){FS}", lambda a: JObject(FS, {"_w": int(a[0].fields.get("_w", 400)) if a and isinstance(a[0], JObject) else 400, "_sl": int(a[0].fields.get("_sl", 0)) if a and isinstance(a[0], JObject) else 0}))
    _add(FFB, "<init>", f"({FNT})V", lambda a: a[0].fields.__setitem__("_fonts", [a[1]] if len(a) > 1 else []) if a and isinstance(a[0], JObject) else None)
    _add(FFB, "addFont", f"({FNT}){FFB}", lambda a: (a[0].fields.setdefault("_fonts", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(FFB, "build", f"(){FF}", lambda a: JObject(FF, {"_fonts": list(a[0].fields.get("_fonts", ())) if a and isinstance(a[0], JObject) else []}))
    _add(FF, "getSize", "()I", lambda a: len(a[0].fields.get("_fonts", ())) if a and isinstance(a[0], JObject) else 0)
    _add(FF, "getFont", f"(I){FNT}", lambda a: (lambda fs, i: fs[i] if 0 <= i < len(fs) else JObject(FNT, {}))(a[0].fields.get("_fonts", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else JObject(FNT, {}))
    _add(FS, "<init>", "(II)V", lambda a: a[0].fields.update({"_w": int(a[1]), "_sl": int(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(FS, "getWeight", "()I", lambda a: int(a[0].fields.get("_w", 400)) if a and isinstance(a[0], JObject) else 400)
    _add(FS, "getSlant", "()I", lambda a: int(a[0].fields.get("_sl", 0)) if a and isinstance(a[0], JObject) else 0)
    _add("Landroid/graphics/fonts/FontVariationAxis;", "fromFontVariationSettings", "(Ljava/lang/String;)[Landroid/graphics/fonts/FontVariationAxis;",
         lambda a: JArray("[Landroid/graphics/fonts/FontVariationAxis;", []))

    # =========================================================================
    # 8. Text: Layout, StaticLayout, StaticLayout$Builder, BoringLayout, SpannableStringBuilder
    # =========================================================================
    LY = "Landroid/text/Layout;"
    SL = "Landroid/text/StaticLayout;"
    SLB = "Landroid/text/StaticLayout$Builder;"
    BL = "Landroid/text/BoringLayout;"

    _add(SLB, "obtain", f"(Ljava/lang/CharSequence;IILandroid/text/TextPaint;I){SLB}",
         lambda a: JObject(SLB, {"_text": _txt(a[0])[int(a[1]):int(a[2])] if len(a) >= 3 else _txt(a[0] if a else ""), "_paint": a[3] if len(a) > 3 else None, "_w": int(a[4]) if len(a) > 4 else 1000, "_max_lines": None}))
    for _slbm, _slbs in (
        ("setAlignment", f"(Landroid/text/Layout$Alignment;){SLB}"),
        ("setBreakStrategy", f"(I){SLB}"),
        ("setHyphenationFrequency", f"(I){SLB}"),
        ("setIncludePad", f"(Z){SLB}"),
        ("setLineSpacing", f"(FF){SLB}"),
        ("setTextDirection", f"(Landroid/text/TextDirectionHeuristic;){SLB}"),
        ("setEllipsize", f"(Landroid/text/TextUtils$TruncateAt;){SLB}"),
    ):
        _add(SLB, _slbm, _slbs, lambda a: a[0] if a else None)
    _add(SLB, "setMaxLines", f"(I){SLB}", lambda a: (a[0].fields.__setitem__("_max_lines", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(SLB, "build", f"(){SL}",
         lambda a: JObject(SL, _compute_text_layout(a[0].fields.get("_text", ""), a[0].fields.get("_paint"), a[0].fields.get("_w", 1000), a[0].fields.get("_max_lines"))) if a and isinstance(a[0], JObject) else JObject(SL, _compute_text_layout("", None, 1000)))

    _add(SL, "<init>", "(Ljava/lang/CharSequence;Landroid/text/TextPaint;ILandroid/text/Layout$Alignment;FFZ)V",
         lambda a: a[0].fields.update(_compute_text_layout(_txt(a[1]), a[2], int(a[3]))) if len(a) > 3 and isinstance(a[0], JObject) else None)
    _add(SL, "<init>", "(Ljava/lang/CharSequence;IILandroid/text/TextPaint;ILandroid/text/Layout$Alignment;FFZ)V",
         lambda a: a[0].fields.update(_compute_text_layout(_txt(a[1])[int(a[2]):int(a[3])], a[4], int(a[5]))) if len(a) > 5 and isinstance(a[0], JObject) else None)
    _add(BL, "isBoring", "(Ljava/lang/CharSequence;Landroid/text/TextPaint;)Landroid/text/BoringLayout$Metrics;", lambda a: None)

    for _lc in (LY, SL, BL, "Landroid/text/DynamicLayout;"):
        _add(_lc, "getLineCount", "()I", lambda a: len(a[0].fields.get("_lines", (("", 0.0),))) if a and isinstance(a[0], JObject) else 1)
        _add(_lc, "getWidth", "()I", lambda a: int(a[0].fields.get("_width", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_lc, "getHeight", "()I", lambda a: int(a[0].fields.get("_height", 0)) if a and isinstance(a[0], JObject) else 0)
        _add(_lc, "getPaint", "()Landroid/text/TextPaint;", lambda a: a[0].fields.get("_paint") or JObject("Landroid/text/TextPaint;", {}) if a and isinstance(a[0], JObject) else JObject("Landroid/text/TextPaint;", {}))
        _add(_lc, "getEllipsisCount", "(I)I", lambda a: 0)
        _add(_lc, "getLineLeft", "(I)F", lambda a: 0.0)
        _add(_lc, "getLineRight", "(I)F", lambda a: (lambda ls, i: float(ls[i][1]) if 0 <= i < len(ls) else 0.0)(a[0].fields.get("_lines", ()), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else 0.0)
        _add(_lc, "getLineStart", "(I)I", lambda a: (lambda offs, i: offs[i][0] if 0 <= i < len(offs) else 0)(_compute_line_offsets(a[0].fields.get("_lines", ())), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else 0)
        _add(_lc, "getLineEnd", "(I)I", lambda a: (lambda offs, i: offs[i][1] if 0 <= i < len(offs) else 0)(_compute_line_offsets(a[0].fields.get("_lines", ())), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else 0)
        _add(_lc, "getLineForVertical", "(I)I", lambda a: (lambda n, lh, y: max(0, min(n - 1, int(y) // max(1, lh))))(len(a[0].fields.get("_lines", (("", 0.0),))), int(a[0].fields.get("_line_h", 14)), int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else 0)
        _add(_lc, "draw", "(Landroid/graphics/Canvas;)V", lambda a: None)
    _add(LY, "getDesiredWidth", "(Ljava/lang/CharSequence;Landroid/text/TextPaint;)F",
         lambda a: float(len(_txt(a[0] if a else "")) * (_f32(a[1].fields.get("$textSize", 12.0), 12.0) if len(a) > 1 and isinstance(a[1], JObject) else 12.0) * 0.55))

    # SpannableStringBuilder & Spanned / Spannable / Editable / TextUtils / Spans
    SSB = "Landroid/text/SpannableStringBuilder;"
    SS = "Landroid/text/SpannableString;"
    SPN = "Landroid/text/Spannable;"
    SPD = "Landroid/text/Spanned;"
    EDT = "Landroid/text/Editable;"

    def _sp_list(obj: Any) -> list[list]:
        if isinstance(obj, JObject):
            sp = obj.fields.get("_spans")
            if not isinstance(sp, list):
                sp = []
                obj.fields["_spans"] = sp
            return sp
        return []

    def _sp_set_span(a):
        if len(a) >= 5 and isinstance(a[0], JObject) and a[1] is not None:
            what, st, en, fl = a[1], int(a[2]), int(a[3]), int(a[4])
            spans = _sp_list(a[0])
            for ent in spans:
                if ent[0] is what:
                    ent[1], ent[2], ent[3] = st, en, fl
                    return None
            spans.append([what, st, en, fl])
        return None

    def _sp_remove_span(a):
        if len(a) >= 2 and isinstance(a[0], JObject):
            spans = _sp_list(a[0])
            spans[:] = [ent for ent in spans if ent[0] is not a[1]]
        return None

    # Upgrade dex_loader's no-op placeholder setSpan/removeSpan on SSB/SS so span queries work faithfully
    for _sc in (SSB, SS):
        R(_sc, "setSpan", "(Ljava/lang/Object;III)V", _sp_set_span)
        R(_sc, "removeSpan", "(Ljava/lang/Object;)V", _sp_remove_span)
    _add(SPN, "setSpan", "(Ljava/lang/Object;III)V", _sp_set_span)
    _add(SPN, "removeSpan", "(Ljava/lang/Object;)V", _sp_remove_span)

    def _sp_get_spans(a):
        if len(a) < 3 or not isinstance(a[0], JObject):
            return JArray("[Ljava/lang/Object;", [])
        qstart, qend = int(a[1]), int(a[2])
        kind = a[3] if len(a) > 3 else None
        kname = kind.fields.get("name", "") if isinstance(kind, JObject) else ""
        kdesc = ("L" + kname.replace(".", "/") + ";") if kname and not kname.startswith(("L", "[")) else kname
        vm_cur = getattr(stubs, "vm", None)
        out = []
        for what, st, en, _ in _sp_list(a[0]):
            overlap = (st < qend and en > qstart) or (st == en and qstart <= st <= qend) or (st == qstart and en == qend)
            if not overlap:
                continue
            if kdesc and kdesc != "Ljava/lang/Object;" and isinstance(what, JObject):
                if vm_cur is not None and hasattr(vm_cur, "_is_subclass"):
                    if not vm_cur._is_subclass(what.cls, kdesc):
                        continue
                elif what.cls != kdesc:
                    continue
            out.append(what)
        el = f"[{kdesc}" if (kdesc and kdesc.startswith("L")) else "[Ljava/lang/Object;"
        return JArray(el, out)

    def _sp_span_pos(a, idx: int, default: int):
        if len(a) > 1 and isinstance(a[0], JObject):
            for ent in _sp_list(a[0]):
                if ent[0] is a[1]:
                    return int(ent[idx])
        return default

    def _sp_next_trans(a):
        if len(a) < 3 or not isinstance(a[0], JObject):
            return int(a[2]) if len(a) > 2 else 0
        st, lim = int(a[1]), int(a[2])
        kind = a[3] if len(a) > 3 else None
        kname = kind.fields.get("name", "") if isinstance(kind, JObject) else ""
        kdesc = ("L" + kname.replace(".", "/") + ";") if kname and not kname.startswith(("L", "[")) else kname
        vm_cur = getattr(stubs, "vm", None)
        best = lim
        for what, s0, e0, _ in _sp_list(a[0]):
            if kdesc and kdesc != "Ljava/lang/Object;" and isinstance(what, JObject):
                if vm_cur is not None and hasattr(vm_cur, "_is_subclass"):
                    if not vm_cur._is_subclass(what.cls, kdesc):
                        continue
                elif what.cls != kdesc:
                    continue
            for p in (s0, e0):
                if st < p < best:
                    best = p
        return best

    for _sc2 in (SSB, SS, SPD):
        _add(_sc2, "getSpans", "(IILjava/lang/Class;)[Ljava/lang/Object;", _sp_get_spans)
        _add(_sc2, "getSpanStart", "(Ljava/lang/Object;)I", lambda a: _sp_span_pos(a, 1, -1))
        _add(_sc2, "getSpanEnd", "(Ljava/lang/Object;)I", lambda a: _sp_span_pos(a, 2, -1))
        _add(_sc2, "getSpanFlags", "(Ljava/lang/Object;)I", lambda a: _sp_span_pos(a, 3, 0))
        _add(_sc2, "nextSpanTransition", "(IILjava/lang/Class;)I", _sp_next_trans)

    def _ssb_append_char(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            ch = chr(int(a[1]) & 0xFFFF) if isinstance(a[1], int) else str(a[1])
            a[0].fields["$text"] = (a[0].fields.get("$text") or "") + ch
        return a[0] if a else None

    def _ssb_append_slice(a):
        if len(a) >= 4 and isinstance(a[0], JObject):
            seg = _txt(a[1])[max(0, int(a[2])):max(0, int(a[3]))]
            a[0].fields["$text"] = (a[0].fields.get("$text") or "") + seg
        return a[0] if a else None

    def _ssb_append_span(a):
        if len(a) >= 4 and isinstance(a[0], JObject):
            cur = a[0].fields.get("$text") or ""
            seg = _txt(a[1])
            st = len(cur)
            en = st + len(seg)
            a[0].fields["$text"] = cur + seg
            if a[2] is not None:
                _sp_list(a[0]).append([a[2], st, en, int(a[3])])
        return a[0] if a else None

    def _ssb_insert(a):
        if len(a) >= 3 and isinstance(a[0], JObject):
            cur = a[0].fields.get("$text") or ""
            where = max(0, min(len(cur), int(a[1])))
            ins = _txt(a[2])
            if len(a) >= 5:
                ins = ins[max(0, int(a[3])):max(0, int(a[4]))]
            a[0].fields["$text"] = cur[:where] + ins + cur[where:]
            delta = len(ins)
            for ent in _sp_list(a[0]):
                if ent[1] >= where:
                    ent[1] += delta
                if ent[2] >= where:
                    ent[2] += delta
        return a[0] if a else None

    def _ssb_delete(a):
        if len(a) >= 3 and isinstance(a[0], JObject):
            cur = a[0].fields.get("$text") or ""
            st = max(0, min(len(cur), int(a[1])))
            en = max(st, min(len(cur), int(a[2])))
            a[0].fields["$text"] = cur[:st] + cur[en:]
            d = en - st
            if d > 0:
                for ent in _sp_list(a[0]):
                    if ent[1] >= en:
                        ent[1] -= d
                    elif ent[1] > st:
                        ent[1] = st
                    if ent[2] >= en:
                        ent[2] -= d
                    elif ent[2] > st:
                        ent[2] = st
        return a[0] if a else None

    _add(SSB, "append", f"(C){SSB}", _ssb_append_char)
    _add(SSB, "append", f"(Ljava/lang/CharSequence;II){SSB}", _ssb_append_slice)
    _add(SSB, "append", f"(Ljava/lang/CharSequence;Ljava/lang/Object;I){SSB}", _ssb_append_span)
    _add(SSB, "insert", f"(ILjava/lang/CharSequence;){SSB}", _ssb_insert)
    _add(SSB, "insert", f"(ILjava/lang/CharSequence;II){SSB}", _ssb_insert)
    _add(SSB, "delete", f"(II){SSB}", _ssb_delete)
    _add(EDT, "insert", f"(ILjava/lang/CharSequence;){EDT}", _ssb_insert)
    _add(EDT, "delete", f"(II){EDT}", _ssb_delete)
    _add(SSB, "clear", "()V", lambda a: (a[0].fields.__setitem__("$text", ""), _sp_list(a[0]).clear(), None)[2] if a and isinstance(a[0], JObject) else None)
    _add(SSB, "clearSpans", "()V", lambda a: _sp_list(a[0]).clear() if a and isinstance(a[0], JObject) else None)
    def _ssb_get_chars(a):
        if len(a) >= 5 and isinstance(a[0], JObject) and isinstance(a[3], JArray):
            src = a[0].fields.get("$text") or ""
            s0, e0, dst_off = max(0, int(a[1])), min(len(src), int(a[2])), max(0, int(a[4]))
            for i, ch in enumerate(src[s0:e0]):
                if dst_off + i < len(a[3].data):
                    a[3].data[dst_off + i] = ord(ch)
        return None
    _add(SSB, "getChars", "(II[CI)V", _ssb_get_chars)
    _add("Landroid/text/SpannedString;", "<init>", "(Ljava/lang/CharSequence;)V", lambda a: a[0].fields.__setitem__("$text", _txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    # TextUtils / PrecomputedText / Selection / Html / Linkify / Spans
    TU = "Landroid/text/TextUtils;"
    _add(TU, "getLayoutDirectionFromLocale", "(Ljava/util/Locale;)I",
         lambda a: 1 if (a and isinstance(a[0], JObject) and str(a[0].fields.get("lang", "")).lower() in ("ar", "he", "iw", "fa", "ur")) else 0)
    _add(TU, "substring", "(Ljava/lang/CharSequence;II)Ljava/lang/String;", lambda a: JString(_txt(a[0])[max(0, int(a[1])):max(0, int(a[2]))]) if len(a) >= 3 else JString(""))
    _add(TU, "writeToParcel", "(Ljava/lang/CharSequence;Landroid/os/Parcel;I)V", lambda a: None)
    _add(TU, "concat", "([Ljava/lang/CharSequence;)Ljava/lang/CharSequence;",
         lambda a: JString("".join(_txt(x) for x in (getattr(a[0], "data", ()) or ()))) if a else JString(""))
    _add(TU, "join", "(Ljava/lang/CharSequence;Ljava/lang/Iterable;)Ljava/lang/String;",
         lambda a: JString(_txt(a[0]).join(_txt(x) for x in (a[1].fields.get("__items", a[1].fields.get("list", ())) if len(a) > 1 and isinstance(a[1], JObject) else ()))) if a else JString(""))
    _add(TU, "split", "(Ljava/lang/String;Ljava/lang/String;)[Ljava/lang/String;",
         lambda a: JArray("[Ljava/lang/String;", [JString(p) for p in (re.split(_txt(a[1]), _txt(a[0])) if len(a) > 1 and _txt(a[1]) else [_txt(a[0] if a else "")])]) if a else JArray("[Ljava/lang/String;", []))

    PTP = "Landroid/text/PrecomputedText$Params;"
    PTPB = "Landroid/text/PrecomputedText$Params$Builder;"
    _add(PTPB, "<init>", "(Landroid/text/TextPaint;)V", lambda a: a[0].fields.__setitem__("_tp", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(PTPB, "setBreakStrategy", f"(I){PTPB}", lambda a: (a[0].fields.__setitem__("_bs", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(PTPB, "setHyphenationFrequency", f"(I){PTPB}", lambda a: (a[0].fields.__setitem__("_hf", int(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(PTPB, "setTextDirection", f"(Landroid/text/TextDirectionHeuristic;){PTPB}", lambda a: (a[0].fields.__setitem__("_td", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(PTPB, "build", f"(){PTP}", lambda a: JObject(PTP, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(PTP, {}))
    _add(PTP, "getBreakStrategy", "()I", lambda a: int(a[0].fields.get("_bs", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(PTP, "getHyphenationFrequency", "()I", lambda a: int(a[0].fields.get("_hf", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(PTP, "getTextDirection", "()Landroid/text/TextDirectionHeuristic;", lambda a: a[0].fields.get("_td") if a and isinstance(a[0], JObject) else None)
    _add(PTP, "getTextPaint", "()Landroid/text/TextPaint;", lambda a: a[0].fields.get("_tp") or JObject("Landroid/text/TextPaint;", {}) if a and isinstance(a[0], JObject) else JObject("Landroid/text/TextPaint;", {}))

    _add("Landroid/text/Selection;", "setSelection", "(Landroid/text/Spannable;I)V", lambda a: None)
    _add("Landroid/text/Selection;", "setSelection", "(Landroid/text/Spannable;II)V", lambda a: None)
    _add("Landroid/text/Selection;", "getSelectionStart", "(Ljava/lang/CharSequence;)I", lambda a: -1)
    _add("Landroid/text/Selection;", "getSelectionEnd", "(Ljava/lang/CharSequence;)I", lambda a: -1)
    _from_html = lambda a: JObject(SSB, {"$text": re.sub(r"<[^>]+>", "", _txt(a[0] if a else ""))})
    _add("Landroid/text/Html;", "fromHtml", "(Ljava/lang/String;)Landroid/text/Spanned;", _from_html)
    _add("Landroid/text/Html;", "fromHtml", "(Ljava/lang/String;I)Landroid/text/Spanned;", _from_html)
    _add("Landroid/text/util/Linkify;", "addLinks", "(Landroid/text/Spannable;I)Z", lambda a: 0)
    _add("Landroid/text/method/LinkMovementMethod;", "getInstance", "()Landroid/text/method/MovementMethod;", lambda a: JObject("Landroid/text/method/LinkMovementMethod;", {}))
    _add("Landroid/text/method/MetaKeyKeyListener;", "adjustMetaAfterKeypress", "(Landroid/text/Spannable;)V", lambda a: None)
    _add("Landroid/text/method/TransformationMethod;", "getTransformation", "(Ljava/lang/CharSequence;Landroid/view/View;)Ljava/lang/CharSequence;", lambda a: a[1] if len(a) > 1 else JString(""))
    _add("Landroid/text/style/AbsoluteSizeSpan;", "<init>", "(IZ)V", lambda a: a[0] if a else None)
    _add("Landroid/text/style/BackgroundColorSpan;", "<init>", "(I)V", lambda a: a[0] if a else None)
    _add("Landroid/text/style/ForegroundColorSpan;", "<init>", "(I)V", lambda a: a[0] if a else None)
    _add("Landroid/text/style/StyleSpan;", "<init>", "(I)V", lambda a: a[0] if a else None)
    _add("Landroid/text/style/TextAppearanceSpan;", "<init>", "(Ljava/lang/String;IILandroid/content/res/ColorStateList;Landroid/content/res/ColorStateList;)V", lambda a: a[0] if a else None)
    _add("Landroid/text/style/CharacterStyle;", "updateDrawState", "(Landroid/text/TextPaint;)V", lambda a: None)
    _add("Landroid/text/style/ClickableSpan;", "onClick", "(Landroid/view/View;)V", lambda a: None)
