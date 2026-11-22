# -*- coding: utf-8 -*-
"""core.java.drawable_loader — Generischer Asset-/XML-Drawable-Resolver & Vector-Tessellator.

Loest Android-Drawables (``@drawable/...``, ``@mipmap/...``, ``@color/...``, ``#AARRGGBB``,
Resource-IDs, Asset-Pfade) aus entpackten APKs (apktool-Struktur) rein mit Python-Stdlib auf:
  * Raster-Bilder: ``.png``, ``.9.png``, ``.webp``, ``.jpg``, ``.gif`` (mit Dichte-Priorisierung)
  * XML-Drawables: ``<vector>`` (kompletter SVG-``pathData``-Parser inkl. Bezier, Ellipsenboegen &
    ``<group>``-Transformationen), ``<shape>``, ``<selector>``, ``<layer-list>``, ``<adaptive-icon>``,
    ``<bitmap>``
  * Ausgabe direkt im ``paint_commands``-Format (``op='image'|'polygon'|'polyline'|'rect'|'oval'``).

100 % zielagnostisch, headless testbar, kein UI-Zwang und keine harte Pillow-Abhaengigkeit.
"""
from __future__ import annotations

import hashlib
import io
import math
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

_NS_ANDROID = "{http://schemas.android.com/apk/res/android}"

_ANDROID_COLORS: Dict[str, Tuple[str, float]] = {
    "white": ("#ffffff", 1.0),
    "black": ("#000000", 1.0),
    "transparent": ("#000000", 0.0),
    "darker_gray": ("#aaaaaa", 1.0),
    "holo_blue_light": ("#33b5e5", 1.0),
    "holo_blue_dark": ("#0099cc", 1.0),
    "holo_blue_bright": ("#00ddff", 1.0),
    "holo_green_light": ("#99cc00", 1.0),
    "holo_green_dark": ("#669900", 1.0),
    "holo_red_light": ("#ff4444", 1.0),
    "holo_red_dark": ("#cc0000", 1.0),
    "holo_orange_light": ("#ffbb33", 1.0),
    "holo_orange_dark": ("#ff8800", 1.0),
    "holo_purple": ("#aa66cc", 1.0),
    "background_dark": ("#000000", 1.0),
    "background_light": ("#ffffff", 1.0),
    "primary_text_dark": ("#ffffff", 1.0),
    "primary_text_light": ("#000000", 1.0),
    "secondary_text_dark": ("#bebebe", 1.0),
    "secondary_text_light": ("#323232", 1.0),
}

_DENSITY_PRIORITY = {
    "drawable-xxhdpi": 100,
    "drawable-xhdpi": 95,
    "drawable-xxxhdpi": 90,
    "drawable-hdpi": 85,
    "drawable-nodpi": 82,
    "drawable-mdpi": 80,
    "drawable": 75,
    "drawable-ldpi": 65,
    "mipmap-xxhdpi": 60,
    "mipmap-xhdpi": 55,
    "mipmap-xxxhdpi": 52,
    "mipmap-hdpi": 50,
    "mipmap-nodpi": 48,
    "mipmap-mdpi": 45,
    "mipmap": 40,
}

_XML_FOLDER_PRIORITY = {
    "drawable-anydpi-v26": 60,
    "drawable-anydpi-v24": 58,
    "drawable-anydpi-v21": 55,
    "drawable-anydpi": 52,
    "drawable-v26": 45,
    "drawable-v24": 43,
    "drawable-v21": 40,
    "drawable": 35,
    "mipmap-anydpi-v26": 30,
    "mipmap-anydpi": 28,
    "mipmap": 20,
}

_RASTER_EXTS = (".png", ".webp", ".jpg", ".jpeg", ".gif")


def _attr(el: ET.Element, name: str, default: Optional[str] = None) -> Optional[str]:
    v = el.get(_NS_ANDROID + name)
    if v is not None:
        return v
    return el.get(name, default)


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_float(val: Optional[str], default: float = 0.0) -> float:
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    for suf in ("dip", "dp", "sp", "px", "pt", "mm", "in"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    try:
        return float(s)
    except Exception:
        return default


def resolve_color(
    val: Optional[str],
    colors_map: Optional[Dict[str, str]] = None,
    _depth: int = 0,
) -> Tuple[Optional[str], float]:
    """Loest Android-Farben (#RGB, #ARGB, #RRGGBB, #AARRGGBB, @color/..., @android:color/...) auf.

    Rueckgabe: ``(hex_rgb, alpha_0_to_1)`` bzw. ``(None, 1.0)`` wenn nicht aufloesbar.
    """
    if not val or _depth > 6:
        return None, 1.0
    s = str(val).strip()
    if not s or s.startswith("?"):
        return None, 1.0
    if s.startswith("@android:color/"):
        key = s[len("@android:color/"):].strip()
        if key in _ANDROID_COLORS:
            return _ANDROID_COLORS[key]
        return None, 1.0
    if s.startswith("@color/"):
        key = s[len("@color/"):].strip()
        if colors_map and key in colors_map:
            return resolve_color(colors_map[key], colors_map, _depth + 1)
        return None, 1.0
    if s.startswith("#"):
        h = s[1:]
        try:
            if len(h) == 3:
                rgb = f"#{h[0]*2}{h[1]*2}{h[2]*2}".lower()
                return rgb, 1.0
            if len(h) == 4:
                a = int(h[0] * 2, 16) / 255.0
                rgb = f"#{h[1]*2}{h[2]*2}{h[3]*2}".lower()
                return rgb, a
            if len(h) == 6:
                int(h, 16)
                return f"#{h.lower()}", 1.0
            if len(h) == 8:
                a = int(h[:2], 16) / 255.0
                rgb = h[2:].lower()
                int(rgb, 16)
                return f"#{rgb}", a
        except Exception:
            return None, 1.0
    return None, 1.0


def _is_very_light(hex_rgb: Optional[str]) -> bool:
    if not hex_rgb or not hex_rgb.startswith("#") or len(hex_rgb) != 7:
        return False
    try:
        r = int(hex_rgb[1:3], 16)
        g = int(hex_rgb[3:5], 16)
        b = int(hex_rgb[5:7], 16)
        return r >= 235 and g >= 235 and b >= 235
    except Exception:
        return False


# ---- SVG / Android pathData Tokenizer & Tessellator ------------------------

_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _sample_cubic(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    steps: int = 8,
) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    for k in range(1, steps + 1):
        t = k / steps
        mt = 1.0 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _sample_quad(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    steps: int = 6,
) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    for k in range(1, steps + 1):
        t = k / steps
        mt = 1.0 - t
        x = (mt**2) * p0[0] + 2 * mt * t * p1[0] + (t**2) * p2[0]
        y = (mt**2) * p0[1] + 2 * mt * t * p1[1] + (t**2) * p2[1]
        pts.append((x, y))
    return pts


def _sample_arc(
    p0: Tuple[float, float],
    rx: float,
    ry: float,
    phi_deg: float,
    large_arc: int,
    sweep: int,
    p1: Tuple[float, float],
    steps: int = 10,
) -> List[Tuple[float, float]]:
    """SVG F.6.5/F.6.6 Mittelpunkt-Parametrisierung fuer elliptische Boegen."""
    x1, y1 = p0
    x2, y2 = p1
    if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-6 or ry < 1e-6:
        return [(x2, y2)]

    phi = math.radians(phi_deg % 360.0)
    cp, sp = math.cos(phi), math.sin(phi)
    dx2 = (x1 - x2) * 0.5
    dy2 = (y1 - y2) * 0.5
    x1p = cp * dx2 + sp * dy2
    y1p = -sp * dx2 + cp * dy2

    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    num = max(0.0, (rx * rx * ry * ry) - (rx * rx * y1p * y1p) - (ry * ry * x1p * x1p))
    den = (rx * rx * y1p * y1p) + (ry * ry * x1p * x1p)
    factor = math.sqrt(num / den) if den > 1e-12 else 0.0
    if bool(large_arc) == bool(sweep):
        factor = -factor
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)

    cx = cp * cxp - sp * cyp + (x1 + x2) * 0.5
    cy = sp * cxp + cp * cyp + (y1 + y2) * 0.5

    def _vec_angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        len_p = math.hypot(ux, uy) * math.hypot(vx, vy)
        c = max(-1.0, min(1.0, dot / len_p)) if len_p > 1e-12 else 1.0
        ang = math.acos(c)
        return -ang if (ux * vy - uy * vx) < 0 else ang

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = _vec_angle(1.0, 0.0, ux, uy)
    dtheta = _vec_angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2.0 * math.pi

    pts: List[Tuple[float, float]] = []
    n_seg = max(4, min(16, int(abs(dtheta) / (math.pi / 8.0)) + 2))
    for k in range(1, n_seg):
        th = theta1 + dtheta * (k / n_seg)
        ex = rx * math.cos(th)
        ey = ry * math.sin(th)
        pts.append((cp * ex - sp * ey + cx, sp * ex + cp * ey + cy))
    pts.append((x2, y2))
    return pts


def parse_svg_path_data(d: str) -> List[Tuple[List[Tuple[float, float]], bool]]:
    """Zerlegt einen SVG-/Android-Vector-``pathData``-String in Subpaths ``[(points, closed), ...]``."""
    if not d or not isinstance(d, str):
        return []
    subpaths: List[Tuple[List[Tuple[float, float]], bool]] = []
    cur_pts: List[Tuple[float, float]] = []
    cx = cy = 0.0
    sx = sy = 0.0
    last_c2: Optional[Tuple[float, float]] = None
    last_q1: Optional[Tuple[float, float]] = None
    last_cmd = ""

    n = len(d)
    pos = 0

    def _skip_sep():
        nonlocal pos
        while pos < n and d[pos] in " \t\r\n,":
            pos += 1

    def _next_num() -> Optional[float]:
        nonlocal pos
        _skip_sep()
        if pos >= n:
            return None
        m = _NUM_RE.match(d, pos)
        if m is None:
            return None
        pos = m.end()
        try:
            return float(m.group(0))
        except Exception:
            return None

    def _next_flag() -> Optional[int]:
        nonlocal pos
        _skip_sep()
        if pos >= n:
            return None
        if d[pos] in ("0", "1"):
            v = int(d[pos])
            pos += 1
            return v
        num = _next_num()
        return int(bool(num)) if num is not None else None

    def _flush(closed: bool = False):
        nonlocal cur_pts
        if len(cur_pts) >= 2:
            subpaths.append((cur_pts, closed))
        cur_pts = []

    cmd = ""
    while pos < n:
        _skip_sep()
        if pos >= n:
            break
        ch = d[pos]
        if ch.isalpha():
            cmd = ch
            pos += 1
            if cmd in ("Z", "z"):
                _flush(closed=True)
                cx, cy = sx, sy
                last_c2 = None
                last_q1 = None
                last_cmd = cmd
                cmd = ""
                continue
        elif not cmd:
            # Ungueltiges Zeichen ueberspringen, Endlosschleife verhindern
            pos += 1
            continue

        rel = cmd.islower()
        uc = cmd.upper()

        if uc == "M":
            x = _next_num()
            y = _next_num()
            if x is None or y is None:
                pos += 1
                cmd = ""
                continue
            if rel and (cur_pts or subpaths):
                cx += x
                cy += y
            else:
                cx, cy = x, y
            _flush(closed=False)
            sx, sy = cx, cy
            cur_pts = [(cx, cy)]
            last_c2 = last_q1 = None
            last_cmd = cmd
            # Folgekoordinaten nach M/m sind implizite L/l
            cmd = "l" if rel else "L"

        elif uc == "L":
            x = _next_num()
            y = _next_num()
            if x is None or y is None:
                cmd = ""
                continue
            cx = (cx + x) if rel else x
            cy = (cy + y) if rel else y
            cur_pts.append((cx, cy))
            last_c2 = last_q1 = None
            last_cmd = cmd

        elif uc == "H":
            x = _next_num()
            if x is None:
                cmd = ""
                continue
            cx = (cx + x) if rel else x
            cur_pts.append((cx, cy))
            last_c2 = last_q1 = None
            last_cmd = cmd

        elif uc == "V":
            y = _next_num()
            if y is None:
                cmd = ""
                continue
            cy = (cy + y) if rel else y
            cur_pts.append((cx, cy))
            last_c2 = last_q1 = None
            last_cmd = cmd

        elif uc == "C":
            vals = [_next_num() for _ in range(6)]
            if any(v is None for v in vals):
                cmd = ""
                continue
            x1, y1, x2, y2, x, y = vals  # type: ignore[misc]
            if rel:
                x1, y1 = cx + x1, cy + y1
                x2, y2 = cx + x2, cy + y2
                x, y = cx + x, cy + y
            cur_pts.extend(_sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
            last_c2 = (x2, y2)
            last_q1 = None
            cx, cy = x, y
            last_cmd = cmd

        elif uc == "S":
            vals = [_next_num() for _ in range(4)]
            if any(v is None for v in vals):
                cmd = ""
                continue
            x2, y2, x, y = vals  # type: ignore[misc]
            if rel:
                x2, y2 = cx + x2, cy + y2
                x, y = cx + x, cy + y
            if last_cmd in ("C", "c", "S", "s") and last_c2 is not None:
                x1, y1 = 2 * cx - last_c2[0], 2 * cy - last_c2[1]
            else:
                x1, y1 = cx, cy
            cur_pts.extend(_sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
            last_c2 = (x2, y2)
            last_q1 = None
            cx, cy = x, y
            last_cmd = cmd

        elif uc == "Q":
            vals = [_next_num() for _ in range(4)]
            if any(v is None for v in vals):
                cmd = ""
                continue
            x1, y1, x, y = vals  # type: ignore[misc]
            if rel:
                x1, y1 = cx + x1, cy + y1
                x, y = cx + x, cy + y
            cur_pts.extend(_sample_quad((cx, cy), (x1, y1), (x, y)))
            last_q1 = (x1, y1)
            last_c2 = None
            cx, cy = x, y
            last_cmd = cmd

        elif uc == "T":
            x = _next_num()
            y = _next_num()
            if x is None or y is None:
                cmd = ""
                continue
            if rel:
                x, y = cx + x, cy + y
            if last_cmd in ("Q", "q", "T", "t") and last_q1 is not None:
                x1, y1 = 2 * cx - last_q1[0], 2 * cy - last_q1[1]
            else:
                x1, y1 = cx, cy
            cur_pts.extend(_sample_quad((cx, cy), (x1, y1), (x, y)))
            last_q1 = (x1, y1)
            last_c2 = None
            cx, cy = x, y
            last_cmd = cmd

        elif uc == "A":
            rx = _next_num()
            ry = _next_num()
            rot = _next_num()
            laf = _next_flag()
            sf = _next_flag()
            x = _next_num()
            y = _next_num()
            if None in (rx, ry, rot, laf, sf, x, y):
                cmd = ""
                continue
            nx = (cx + x) if rel else x  # type: ignore[operator]
            ny = (cy + y) if rel else y  # type: ignore[operator]
            cur_pts.extend(_sample_arc((cx, cy), rx, ry, rot, laf, sf, (nx, ny)))  # type: ignore[arg-type]
            cx, cy = nx, ny
            last_c2 = last_q1 = None
            last_cmd = cmd

        else:
            cmd = ""

    _flush(closed=False)
    return subpaths


def _apply_transforms(
    pts: List[Tuple[float, float]],
    transforms: List[Dict[str, float]],
) -> List[Tuple[float, float]]:
    if not transforms or not pts:
        return pts
    res = list(pts)
    for tr in reversed(transforms):
        px = tr.get("pivotX", 0.0)
        py = tr.get("pivotY", 0.0)
        sx = tr.get("scaleX", 1.0)
        sy = tr.get("scaleY", 1.0)
        rot = tr.get("rotation", 0.0)
        tx = tr.get("translateX", 0.0)
        ty = tr.get("translateY", 0.0)
        rad = math.radians(rot) if rot else 0.0
        cr, sr = (math.cos(rad), math.sin(rad)) if rot else (1.0, 0.0)
        out: List[Tuple[float, float]] = []
        for x, y in res:
            lx = (x - px) * sx
            ly = (y - py) * sy
            if rot:
                rx = cr * lx - sr * ly
                ry = sr * lx + cr * ly
            else:
                rx, ry = lx, ly
            out.append((rx + px + tx, ry + py + ty))
        res = out
    return res


_MATERIAL_ATTR_DEFAULTS: Dict[str, str] = {
    "colorPrimary": "#2563eb",
    "colorPrimaryVariant": "#1d4ed8",
    "colorPrimaryDark": "#1e40af",
    "colorSecondary": "#0ea5e9",
    "colorSecondaryContainer": "#dbeafe",
    "colorOnSecondaryContainer": "#1e3a8a",
    "colorAccent": "#2563eb",
    "colorOnPrimary": "#ffffff",
    "colorSurface": "#ffffff",
    "colorSurfaceVariant": "#f1f5f9",
    "colorSurfaceContainer": "#f8fafc",
    "colorBackground": "#ffffff",
    "windowBackground": "#ffffff",
    "colorOnSurface": "#111827",
    "colorOnSurfaceVariant": "#64748b",
    "colorOnBackground": "#111827",
    "textColorPrimary": "#111827",
    "textColorSecondary": "#64748b",
    "colorOutline": "#cbd5e1",
    "colorError": "#dc2626",
    "selectableItemBackground": "#00000000",
    "selectableItemBackgroundBorderless": "#00000000",
}


class DrawableResolver:
    """Indexiert ``res/drawable*``, ``res/mipmap*``, ``colors.xml``, ``dimens``, ``styles``, ``public.xml`` & ``assets/``."""

    def __init__(self, app_root: Optional[str]):
        self.app_root = os.path.abspath(app_root) if app_root else ""
        self.colors: Dict[str, str] = {}
        self.night_colors: Dict[str, str] = {}
        self.dimens: Dict[str, str] = {}
        self.styles: Dict[str, Dict[str, Any]] = {}
        self.night_styles: Dict[str, Dict[str, Any]] = {}
        self.night_mode: bool = False
        self.id2name: Dict[int, str] = {}
        self.attr_id2name: Dict[int, str] = {}
        self.raster_index: Dict[str, Tuple[int, str, bool]] = {}
        self.xml_index: Dict[str, Tuple[int, str]] = {}
        self.color_xml_index: Dict[str, str] = {}
        self._spec_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._window_bg_cache: Optional[Tuple[Optional[str], bool]] = None
        if self.app_root and os.path.isdir(self.app_root):
            self._index()

    def _res_root_dir(self) -> str:
        r1 = os.path.join(self.app_root, "res")
        if os.path.isdir(r1):
            return r1
        r2 = os.path.join(self.app_root, "resources", "package_1", "res")
        if os.path.isdir(r2):
            return r2
        return r1

    def _index(self) -> None:
        res_dir = self._res_root_dir()
        if os.path.isdir(res_dir):
            self._index_values(res_dir)
            self._index_res_dirs(res_dir)

    def _index_values(self, res_dir: str) -> None:
        vdir = os.path.join(res_dir, "values")
        if not os.path.isdir(vdir):
            return
        pub = os.path.join(vdir, "public.xml")
        if os.path.isfile(pub):
            try:
                tree = ET.parse(pub)
                for el in tree.getroot():
                    tp = el.get("type")
                    nm = el.get("name") or ""
                    try:
                        rid = int(el.get("id") or "0", 16)
                    except Exception:
                        continue
                    if tp in ("drawable", "mipmap", "color", "style", "dimen"):
                        self.id2name[rid] = nm
                    elif tp == "attr":
                        self.attr_id2name[rid] = nm
            except Exception:
                pass
        try:
            xml_files = sorted(f for f in os.listdir(vdir) if f.lower().endswith(".xml") and f.lower() != "public.xml")
        except Exception:
            xml_files = ["colors.xml", "styles.xml", "themes.xml"]
        for fname in xml_files:
            xp = os.path.join(vdir, fname)
            if not os.path.isfile(xp):
                continue
            try:
                tree = ET.parse(xp)
                for el in tree.getroot():
                    tag = _strip_ns(el.tag).lower()
                    nm = el.get("name") or ""
                    if not nm:
                        continue
                    if tag == "color" and el.text:
                        self.colors[nm] = el.text.strip()
                    elif (tag == "dimen" or (tag == "item" and (el.get("type") or "").lower() == "dimen")) and el.text:
                        self.dimens[nm] = el.text.strip()
                    elif tag == "style":
                        items: Dict[str, str] = {}
                        for ch in el:
                            if _strip_ns(ch.tag).lower() == "item" and ch.get("name") and ch.text:
                                items[ch.get("name", "")] = ch.text.strip()
                        cur = self.styles.setdefault(nm, {"parent": "", "items": {}})
                        if el.get("parent"):
                            cur["parent"] = el.get("parent", "")
                        cur["items"].update(items)
            except Exception:
                pass
        nvdir = os.path.join(res_dir, "values-night")
        if os.path.isdir(nvdir):
            try:
                n_files = sorted(f for f in os.listdir(nvdir) if f.lower().endswith(".xml") and f.lower() != "public.xml")
            except Exception:
                n_files = ["colors.xml", "styles.xml", "themes.xml"]
            for fname in n_files:
                xp = os.path.join(nvdir, fname)
                if not os.path.isfile(xp):
                    continue
                try:
                    tree = ET.parse(xp)
                    for el in tree.getroot():
                        tag = _strip_ns(el.tag).lower()
                        nm = el.get("name") or ""
                        if not nm:
                            continue
                        if tag == "color" and el.text:
                            self.night_colors[nm] = el.text.strip()
                        elif tag == "style":
                            items_n: Dict[str, str] = {}
                            for ch in el:
                                if _strip_ns(ch.tag).lower() == "item" and ch.get("name") and ch.text:
                                    items_n[ch.get("name", "")] = ch.text.strip()
                            cur_n = self.night_styles.setdefault(nm, {"parent": "", "items": {}})
                            if el.get("parent"):
                                cur_n["parent"] = el.get("parent", "")
                            cur_n["items"].update(items_n)
                except Exception:
                    pass

    def active_colors(self, night: Optional[bool] = None) -> Dict[str, str]:
        use_night = self.night_mode if night is None else bool(night)
        if use_night and self.night_colors:
            merged = dict(self.colors)
            merged.update(self.night_colors)
            return merged
        return self.colors

    @staticmethod
    def _is_dark_hex(hex_rgb: Optional[str]) -> bool:
        if not hex_rgb or not hex_rgb.startswith("#") or len(hex_rgb) != 7:
            return False
        try:
            r = int(hex_rgb[1:3], 16)
            g = int(hex_rgb[3:5], 16)
            b = int(hex_rgb[5:7], 16)
            return (0.299 * r + 0.587 * g + 0.114 * b) < 150.0
        except Exception:
            return False

    def resolve_color_hex_alpha(
        self, res_id: int, night: Optional[bool] = None
    ) -> Tuple[Optional[str], float]:
        nm = self.id2name.get(int(res_id))
        cmap = self.active_colors(night=night)
        if not nm:
            return None, 1.0
        if nm in cmap:
            return resolve_color(cmap[nm], cmap)
        if nm in self.color_xml_index:
            sp = self.resolve(nm)
            if isinstance(sp, dict):
                col = sp.get("color") or sp.get("fill")
                if col:
                    return str(col), float(sp.get("alpha", 1.0) or 1.0)
        return None, 1.0

    def resolve_dimen_px(self, res_id_or_name: Any, default: float = 0.0) -> float:
        nm = (
            self.id2name.get(int(res_id_or_name))
            if isinstance(res_id_or_name, int)
            else str(res_id_or_name or "").strip()
        )
        for _ in range(8):
            if not nm:
                return default
            for pref in ("@dimen/", "dimen/"):
                if nm.startswith(pref):
                    nm = nm[len(pref):]
            raw = self.dimens.get(nm)
            if not raw:
                return default
            s = raw.strip()
            if s.startswith("@dimen/") or s.startswith("dimen/"):
                nm = s
                continue
            m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*(dp|dip|sp|px)?$", s, re.IGNORECASE)
            if m:
                return float(m.group(1))
            return _parse_float(s, default)
        return default

    def resolve_color_int(self, res_id: int, default: Optional[int] = None) -> Optional[int]:
        hex_rgb, alpha = self.resolve_color_hex_alpha(int(res_id))
        if not hex_rgb:
            return default
        try:
            rgb = int(hex_rgb.lstrip("#")[:6], 16)
            a_byte = max(0, min(255, int(round(float(alpha) * 255))))
            val = (a_byte << 24) | rgb
            return val - 0x100000000 if val >= 0x80000000 else val
        except Exception:
            return default

    def resolve_window_background(self) -> Tuple[Optional[str], bool]:
        """Ermittelt die Theme-``windowBackground``-Farbe aus AndroidManifest + styles.xml."""
        if self._window_bg_cache is not None:
            return self._window_bg_cache
        if not self.app_root:
            self._window_bg_cache = (None, False)
            return self._window_bg_cache
        manifest = os.path.join(self.app_root, "AndroidManifest.xml")
        if not os.path.isfile(manifest):
            alt_manifest = os.path.join(self.app_root, "resources", "package_1", "AndroidManifest.xml")
            if os.path.isfile(alt_manifest):
                manifest = alt_manifest
        theme_ref: Optional[str] = None
        if os.path.isfile(manifest):
            try:
                root = ET.parse(manifest).getroot()
                app_el = root.find("application")
                app_theme = _attr(app_el, "theme") if app_el is not None else None
                act_theme: Optional[str] = None
                if app_el is not None:
                    for act in app_el.findall("activity"):
                        is_main = False
                        for inf in act.findall("intent-filter"):
                            for sub in inf:
                                sname = _attr(sub, "name") or ""
                                if "MAIN" in sname or "LAUNCHER" in sname:
                                    is_main = True
                                    break
                            if is_main:
                                break
                        if is_main:
                            act_theme = _attr(act, "theme")
                            break
                theme_ref = act_theme or app_theme
            except Exception:
                theme_ref = None
        if not theme_ref:
            self._window_bg_cache = (None, False)
            return self._window_bg_cache

        style_name = str(theme_ref).strip()
        for pref in ("@style/", "style/"):
            if style_name.startswith(pref):
                style_name = style_name[len(pref):]
                break

        seen = set()
        bg_val: Optional[str] = None
        cur_name = style_name
        for _ in range(8):
            if not cur_name or cur_name in seen:
                break
            seen.add(cur_name)
            st = self.styles.get(cur_name)
            if st is None and cur_name.startswith("@"):
                cur_name = cur_name.split("/", 1)[-1]
                st = self.styles.get(cur_name)
            if st is not None:
                items = st.get("items") or {}
                cand = items.get("android:windowBackground") or items.get("windowBackground")
                if cand:
                    bg_val = cand
                    break
                parent = (st.get("parent") or "").strip()
                for pref in ("@style/", "@android:style/", "style/"):
                    if parent.startswith(pref):
                        parent = parent[len(pref):]
                        break
                if not parent and "." in cur_name:
                    parent = cur_name.rsplit(".", 1)[0]
                cur_name = parent
            elif "." in cur_name:
                cur_name = cur_name.rsplit(".", 1)[0]
            else:
                break

        hex_rgb: Optional[str] = None
        if bg_val:
            rgb, alpha = resolve_color(bg_val, self.colors)
            if rgb and alpha > 0.2:
                hex_rgb = rgb
            else:
                sp = self.resolve(bg_val)
                if sp and sp.get("kind") == "color" and sp.get("alpha", 1.0) > 0.2:
                    hex_rgb = sp.get("color")
                elif sp and sp.get("kind") == "shape" and sp.get("fill"):
                    hex_rgb = sp.get("fill")
        self._window_bg_cache = (hex_rgb, self._is_dark_hex(hex_rgb))
        return self._window_bg_cache

    @staticmethod
    def _folder_score(folder: str, mapping: Dict[str, int]) -> int:
        if folder in mapping:
            return mapping[folder]
        base = folder.split("-", 1)[0]
        # Dichte-Matching toleriert Qualifizierer wie -v4 / -night
        for k, sc in mapping.items():
            if "-" in k and folder.startswith(k):
                return sc - 1
        return mapping.get(base, 10)

    def _index_res_dirs(self, res_dir: str) -> None:
        try:
            entries = sorted(os.listdir(res_dir))
        except Exception:
            return
        for folder in entries:
            if not (folder.startswith("drawable") or folder.startswith("mipmap") or folder.startswith("color")):
                continue
            if "-night" in folder:
                continue
            fdir = os.path.join(res_dir, folder)
            if not os.path.isdir(fdir):
                continue
            is_color_dir = folder.startswith("color")
            r_base_score = self._folder_score(folder, _DENSITY_PRIORITY)
            x_score = self._folder_score(folder, _XML_FOLDER_PRIORITY)
            try:
                fns = sorted(os.listdir(fdir))
            except Exception:
                continue
            for fn in fns:
                fp = os.path.join(fdir, fn)
                if not os.path.isfile(fp):
                    continue
                low = fn.lower()
                if is_color_dir:
                    if low.endswith(".xml"):
                        self.color_xml_index.setdefault(fn[:-4], fp)
                    continue
                if low.endswith(".9.png"):
                    stem = fn[:-6]
                    sc = r_base_score - 2
                    prev = self.raster_index.get(stem)
                    if prev is None or sc > prev[0]:
                        self.raster_index[stem] = (sc, fp, True)
                elif low.endswith(".xml"):
                    stem = fn[:-4]
                    prev = self.xml_index.get(stem)
                    if prev is None or x_score > prev[0]:
                        self.xml_index[stem] = (x_score, fp)
                else:
                    stem, ext = os.path.splitext(fn)
                    if ext.lower() in _RASTER_EXTS:
                        ext_bonus = 3 if ext.lower() == ".png" else 1
                        sc = r_base_score + ext_bonus
                        prev = self.raster_index.get(stem)
                        if prev is None or sc > prev[0]:
                            self.raster_index[stem] = (sc, fp, False)

    def resolve_color(self, val: Optional[str]) -> Tuple[Optional[str], float]:
        return resolve_color(val, self.colors)

    def resolve_dimen(self, val: Any, default: Optional[float] = None, _depth: int = 0) -> Optional[float]:
        """Loest `@dimen/...` / `dp` / `dip` / `sp` / `px` / `?attr/...` zu einer float-Zahl auf."""
        if _depth > 8 or val is None:
            return default
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        s = str(val).strip()
        if not s:
            return default
        if s.startswith("?"):
            av = self.resolve_attr(s, _depth + 1)
            return self.resolve_dimen(av, default, _depth + 1) if av else default
        if s.startswith(("@dimen/", "@android:dimen/")):
            key = s.split("/", 1)[1]
            raw = self.dimens.get(key)
            if raw is not None:
                return self.resolve_dimen(raw, default, _depth + 1)
            return default
        return _parse_float(s, default)

    def _ordered_theme_names(self) -> List[str]:
        names = list(self.styles.keys())
        def _prio(nm: str) -> Tuple[int, str]:
            low = nm.lower()
            if nm == "AppTheme" or nm == "Theme.App":
                return (0, nm)
            if low.startswith("theme.") and ("noactionbar" in low or "daynight" in low or "material" in low):
                return (1, nm)
            if low.startswith("theme"):
                return (2, nm)
            if "theme" in low:
                return (3, nm)
            return (4, nm)
        return sorted(names, key=_prio)

    def _lookup_style_item(self, style_name: str, key: str, _depth: int = 0) -> Optional[str]:
        if _depth > 8 or not style_name:
            return None
        sname = style_name[7:] if style_name.startswith("@style/") else style_name
        entry = self.styles.get(sname)
        if entry:
            items = entry.get("items") or {}
            for k in (key, f"android:{key}" if not key.startswith("android:") else key[8:]):
                if k in items:
                    return items[k]
            par = entry.get("parent") or ""
            if par:
                hit = self._lookup_style_item(par, key, _depth + 1)
                if hit is not None:
                    return hit
        if "." in sname:
            return self._lookup_style_item(sname.rsplit(".", 1)[0], key, _depth + 1)
        return None

    def resolve_attr(self, attr_ref: Any, _depth: int = 0) -> Optional[str]:
        """Loest `?attr/foo`, `?foo`, `?android:attr/foo` oder eine Attr-Res-ID ueber Styles/Material-Defaults auf."""
        if _depth > 8 or attr_ref is None:
            return None
        if isinstance(attr_ref, int):
            nm = self.attr_id2name.get(attr_ref) or self.id2name.get(attr_ref)
            if not nm:
                return None
            key = nm
        else:
            s = str(attr_ref).strip()
            if not s.startswith("?"):
                return s
            key = s[1:]
            if "/" in key:
                ns_part, key_part = key.split("/", 1)
                key = f"android:{key_part}" if ns_part.startswith("android:") else key_part
        bare = key.split(":", 1)[-1]
        for sname in self._ordered_theme_names():
            val = self._lookup_style_item(sname, key)
            if val is not None:
                if val.startswith("?"):
                    chained = self.resolve_attr(val, _depth + 1)
                    if chained:
                        return chained
                return val
        return _MATERIAL_ATTR_DEFAULTS.get(bare)

    @staticmethod
    def _selector_item_matches(it: ET.Element, state: Optional[Dict[str, bool]]) -> bool:
        st = {"selected": False, "checked": False, "enabled": True, "pressed": False}
        if state:
            st.update({k: bool(v) for k, v in state.items()})
        for k, v in it.attrib.items():
            sk = _strip_ns(k)
            if not sk.startswith("state_"):
                continue
            s_name = sk[len("state_"):]
            want = str(v).strip().lower() == "true"
            cur = bool(st.get(s_name, False))
            if cur != want:
                return False
        return True

    def _eval_color_selector_xml(self, path: str, state: Optional[Dict[str, bool]]) -> Tuple[Optional[str], float]:
        try:
            root = ET.parse(path).getroot()
        except Exception:
            return None, 1.0
        if _strip_ns(root.tag).lower() != "selector":
            return None, 1.0
        items = [ch for ch in root if _strip_ns(ch.tag).lower() == "item"]
        for it in items:
            if not self._selector_item_matches(it, state):
                continue
            cref = _attr(it, "color")
            if not cref:
                continue
            if cref.startswith("?"):
                cref = self.resolve_attr(cref)
            rgb, alpha = resolve_color(cref, self.colors)
            if rgb is not None:
                a_attr = _attr(it, "alpha")
                if a_attr is not None:
                    alpha *= _parse_float(a_attr, 1.0)
                return rgb, alpha
        return None, 1.0

    def resolve_color_for_state(
        self, ref: Any, state: Optional[Dict[str, bool]] = None, _depth: int = 0
    ) -> Tuple[Optional[str], float]:
        """Loest Farben inkl. `?attr/...` und `res/color/*.xml`-Selector zustandsabhaengig auf."""
        if _depth > 8 or ref is None:
            return None, 1.0
        s = str(ref).strip()
        if not s:
            return None, 1.0
        if s.startswith("?"):
            av = self.resolve_attr(s, _depth + 1)
            if not av:
                return None, 1.0
            return self.resolve_color_for_state(av, state=state, _depth=_depth + 1)
        if s.startswith("@color/"):
            cname = s[len("@color/"):]
            cx = self.color_xml_index.get(cname)
            if cx:
                rgb, a = self._eval_color_selector_xml(cx, state)
                if rgb is not None:
                    return rgb, a
        return resolve_color(s, self.colors)

    def resolve(self, ref: Any, _depth: int = 0, state: Optional[Dict[str, bool]] = None) -> Optional[Dict[str, Any]]:
        """Loest ``ref`` (ID, ``@drawable/...``, ``?attr/...``, ``#color``, Asset-Pfad, Name) zu Drawable-Spec auf."""
        if _depth > 8 or ref is None:
            return None
        if isinstance(ref, int):
            nm = self.id2name.get(ref)
            if not nm:
                return None
            ref = nm

        s = str(ref).strip()
        if not s:
            return None
        if s.startswith("?"):
            av = self.resolve_attr(s, _depth + 1)
            if not av:
                return None
            s = av

        if _depth == 0 and not state and s in self._spec_cache:
            return self._spec_cache[s]

        res = self._resolve_uncached(s, _depth, state=state)
        if _depth == 0 and not state:
            self._spec_cache[s] = res
        return res

    def _resolve_uncached(
        self, s: str, _depth: int, state: Optional[Dict[str, bool]] = None
    ) -> Optional[Dict[str, Any]]:
        # Direkte Farbe oder @color/* / @android:color/* (inkl. res/color/*.xml-Selektoren)
        if s.startswith(("#", "@color/", "@android:color/")):
            rgb, alpha = self.resolve_color_for_state(s, state=state)
            if rgb is None:
                return None
            return {"kind": "color", "color": rgb, "alpha": alpha, "name": s}

        if s.startswith("@android:drawable/"):
            return None

        name = s
        for prefix in ("@drawable/", "@mipmap/"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break

        # Datei-/Asset-Pfad (z. B. file://... oder assets/logo.png)
        if name.startswith("file://"):
            p = name[7:].lstrip("/")
            if os.path.isfile(p):
                return {"kind": "raster", "path": p, "name": os.path.basename(p), "is_9patch": p.lower().endswith(".9.png")}
        if self.app_root and ("/" in name or "\\" in name):
            for base in (os.path.join(self.app_root, "assets"), self.app_root):
                cand = os.path.join(base, name.lstrip("/\\"))
                if os.path.isfile(cand):
                    ext = os.path.splitext(cand)[1].lower()
                    if ext == ".xml":
                        return self._parse_xml_file(cand, os.path.basename(cand), _depth + 1, state=state)
                    if ext in _RASTER_EXTS:
                        return {"kind": "raster", "path": cand, "name": os.path.basename(cand), "is_9patch": cand.lower().endswith(".9.png")}

        # XML vor Raster nur wenn explizit state uebergeben und Selector existiert
        if state and name in self.xml_index:
            _, fp = self.xml_index[name]
            got_xml = self._parse_xml_file(fp, name, _depth + 1, state=state)
            if got_xml is not None:
                return got_xml

        # Raster vor XML bei Namensgleichheit (z. B. vollfarbiges App-Icon statt Notif-Outline)
        r_hit = self.raster_index.get(name)
        if r_hit is not None:
            _, fp, is_9 = r_hit
            return {"kind": "raster", "path": fp, "name": name, "is_9patch": is_9}

        x_hit = self.xml_index.get(name)
        if x_hit is not None:
            _, fp = x_hit
            return self._parse_xml_file(fp, name, _depth + 1, state=state)

        if name in self.colors or name in self.color_xml_index:
            rgb, alpha = self.resolve_color_for_state(f"@color/{name}", state=state)
            if rgb is not None:
                return {"kind": "color", "color": rgb, "alpha": alpha, "name": name}
        return None

    def _parse_xml_file(
        self, path: str, name: str, depth: int, state: Optional[Dict[str, bool]] = None
    ) -> Optional[Dict[str, Any]]:
        if depth > 8:
            return None
        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except Exception:
            return None
        return self._parse_xml_el(root, name, depth, state=state)

    def _parse_xml_el(
        self, el: ET.Element, name: str, depth: int, state: Optional[Dict[str, bool]] = None
    ) -> Optional[Dict[str, Any]]:
        if depth > 8:
            return None
        tag = _strip_ns(el.tag).lower()
        if tag == "vector":
            return self._parse_vector(el, name)
        if tag == "shape":
            return self._parse_shape(el, name, state=state)
        if tag == "bitmap":
            src = _attr(el, "src")
            return self.resolve(src, depth + 1, state=state) if src else None
        if tag == "color":
            col = _attr(el, "color")
            rgb, alpha = self.resolve_color_for_state(col, state=state)
            return {"kind": "color", "color": rgb, "alpha": alpha, "name": name} if rgb else None
        if tag == "gradient":
            for ak in ("startColor", "centerColor", "endColor"):
                rgb, alpha = resolve_color(_attr(el, ak), self.colors)
                if rgb is not None and alpha > 0.01:
                    return {"kind": "color", "color": rgb, "alpha": alpha, "name": name}
            for ch in el:
                if _strip_ns(ch.tag).lower() == "item":
                    rgb, alpha = resolve_color(_attr(ch, "color"), self.colors)
                    if rgb is not None and alpha > 0.01:
                        return {"kind": "color", "color": rgb, "alpha": alpha, "name": name}
            return None
        if tag == "selector":
            return self._parse_selector(el, name, depth, state=state)
        if tag == "layer-list":
            return self._parse_layer_list(el, name, depth)
        if tag == "adaptive-icon":
            for pref in ("foreground", "background"):
                for ch in el:
                    if _strip_ns(ch.tag).lower() == pref:
                        dref = _attr(ch, "drawable")
                        if dref:
                            got = self.resolve(dref, depth + 1, state=state)
                            if got is not None:
                                return got
                        for sub in ch:
                            got = self._parse_xml_el(sub, name, depth + 1, state=state)
                            if got is not None:
                                return got
        if tag == "inset":
            dref = _attr(el, "drawable")
            if dref:
                return self.resolve(dref, depth + 1, state=state)
            for ch in el:
                got = self._parse_xml_el(ch, name, depth + 1, state=state)
                if got is not None:
                    return got
        return None

    def _parse_vector(self, el: ET.Element, name: str) -> Dict[str, Any]:
        vw = _parse_float(_attr(el, "viewportWidth"), 24.0) or 24.0
        vh = _parse_float(_attr(el, "viewportHeight"), 24.0) or 24.0
        tint, tint_a = self.resolve_color_for_state(_attr(el, "tint"))
        if tint_a <= 0.01:
            tint = None
        paths: List[Dict[str, Any]] = []
        path_groups: List[Dict[str, Any]] = []

        def _resolve_vec_color(raw_val: Optional[str]) -> Tuple[Optional[str], float]:
            rgb, a = self.resolve_color_for_state(raw_val)
            if rgb is not None:
                return rgb, a
            if isinstance(raw_val, str) and raw_val.strip().startswith(("@drawable/", "@color/", "@mipmap/", "?")):
                sp = self.resolve(raw_val.strip(), 1)
                if isinstance(sp, dict):
                    col = sp.get("color") or sp.get("fill")
                    if col:
                        return col, float(sp.get("alpha", 1.0) or 1.0)
            return None, 1.0

        def _walk(node: ET.Element, stack: List[Dict[str, float]]):
            for ch in node:
                t = _strip_ns(ch.tag).lower()
                if t == "group":
                    tr = {
                        "translateX": _parse_float(_attr(ch, "translateX"), 0.0),
                        "translateY": _parse_float(_attr(ch, "translateY"), 0.0),
                        "scaleX": _parse_float(_attr(ch, "scaleX"), 1.0),
                        "scaleY": _parse_float(_attr(ch, "scaleY"), 1.0),
                        "pivotX": _parse_float(_attr(ch, "pivotX"), 0.0),
                        "pivotY": _parse_float(_attr(ch, "pivotY"), 0.0),
                        "rotation": _parse_float(_attr(ch, "rotation"), 0.0),
                        "alpha": _parse_float(_attr(ch, "alpha"), 1.0),
                    }
                    _walk(ch, stack + [tr])
                elif t == "path":
                    pd = _attr(ch, "pathData") or ""
                    subpaths = parse_svg_path_data(pd)
                    if not subpaths:
                        continue
                    grp_alpha = 1.0
                    for g_tr in stack:
                        grp_alpha *= float(g_tr.get("alpha", 1.0))
                    raw_fill = _attr(ch, "fillColor")
                    fill, fill_a = _resolve_vec_color(raw_fill)
                    fa_attr = _parse_float(_attr(ch, "fillAlpha"), 1.0)
                    eff_fill_a = max(0.0, min(1.0, fill_a * fa_attr * grp_alpha))
                    if eff_fill_a <= 0.01:
                        fill = None
                    explicit_no_fill = (raw_fill is not None) and (fill is None)
                    raw_stroke = _attr(ch, "strokeColor")
                    stroke, st_a = _resolve_vec_color(raw_stroke)
                    sa_attr = _parse_float(_attr(ch, "strokeAlpha"), 1.0)
                    eff_stroke_a = max(0.0, min(1.0, st_a * sa_attr * grp_alpha))
                    if eff_stroke_a <= 0.01:
                        stroke = None
                    sw = _parse_float(_attr(ch, "strokeWidth"), 0.0 if not raw_stroke else 1.0)
                    if explicit_no_fill and (raw_stroke is not None or sw > 0):
                        fill = None
                        if stroke is None:
                            stroke = tint or "#3b4963"
                            eff_stroke_a = max(0.05, grp_alpha)
                        sw = max(1.0, sw)
                    elif fill is None and stroke is None:
                        fill = tint or "#3b4963"
                        eff_fill_a = max(0.05, fa_attr * grp_alpha)
                    elif tint and fill is not None:
                        fill = tint
                    fill_type = (_attr(ch, "fillType") or "nonZero").strip()
                    t_subs: List[Tuple[List[Tuple[float, float]], bool]] = []
                    for pts, closed in subpaths:
                        tpts = _apply_transforms(pts, stack)
                        t_subs.append((tpts, closed))
                        paths.append({
                            "points": tpts,
                            "closed": closed,
                            "fill": fill,
                            "fill_alpha": eff_fill_a,
                            "stroke": stroke,
                            "stroke_alpha": eff_stroke_a,
                            "stroke_w": sw,
                        })
                    path_groups.append({
                        "subpaths": t_subs,
                        "fill": fill,
                        "fill_alpha": eff_fill_a,
                        "stroke": stroke,
                        "stroke_alpha": eff_stroke_a,
                        "stroke_w": sw,
                        "fill_type": fill_type,
                    })

        _walk(el, [])
        return {"kind": "vector", "name": name, "vw": vw, "vh": vh, "tint": tint,
                "paths": paths, "path_groups": path_groups}

    def _parse_shape(
        self, el: ET.Element, name: str, state: Optional[Dict[str, bool]] = None
    ) -> Dict[str, Any]:
        shape = (_attr(el, "shape") or "rectangle").strip().lower()
        fill: Optional[str] = None
        fill_alpha = 1.0
        outline: Optional[str] = None
        stroke_w = 1.0
        radius = 0.0
        for ch in el:
            t = _strip_ns(ch.tag).lower()
            if t == "solid":
                rgb, a = self.resolve_color_for_state(_attr(ch, "color"), state=state)
                fill = rgb
                fill_alpha = a
            elif t == "gradient" and fill is None:
                rgb, a = self.resolve_color_for_state(_attr(ch, "startColor") or _attr(ch, "centerColor"), state=state)
                fill = rgb
                fill_alpha = a
            elif t == "stroke":
                rgb, a = self.resolve_color_for_state(_attr(ch, "color"), state=state)
                if a > 0.01:
                    outline = rgb
                stroke_w = max(1.0, float(self.resolve_dimen(_attr(ch, "width"), 1.0) or 1.0))
            elif t == "corners":
                r_raw = _attr(ch, "radius") or _attr(ch, "topLeftRadius") or "0"
                radius = max(0.0, float(self.resolve_dimen(r_raw, 0.0) or 0.0))
        if fill_alpha <= 0.01:
            fill = None
        return {
            "kind": "shape",
            "name": name,
            "shape": shape,
            "fill": fill,
            "outline": outline,
            "stroke_w": stroke_w,
            "radius": radius,
        }

    def _parse_selector(
        self, el: ET.Element, name: str, depth: int, state: Optional[Dict[str, bool]] = None
    ) -> Optional[Dict[str, Any]]:
        items = [ch for ch in el if _strip_ns(ch.tag).lower() == "item"]
        if state:
            ordered = [it for it in items if self._selector_item_matches(it, state)]
        else:
            # Standardzustand bevorzugen: erstes Item ohne explizites state_*="true"
            ordered = [it for it in items if not any(k.endswith("state_") or "state_" in k and v == "true" for k, v in it.attrib.items())]
            if not ordered and items:
                ordered = list(reversed(items))
        for it in ordered + [x for x in items if x not in ordered]:
            dref = _attr(it, "drawable")
            if dref:
                got = self.resolve(dref, depth + 1, state=state)
                if got is not None and not (got.get("kind") == "color" and got.get("alpha", 1.0) <= 0.01):
                    return got
            for sub in it:
                got = self._parse_xml_el(sub, name, depth + 1, state=state)
                if got is not None:
                    return got
        return None

    def _parse_layer_list(self, el: ET.Element, name: str, depth: int) -> Optional[Dict[str, Any]]:
        best: Optional[Dict[str, Any]] = None
        for ch in el:
            if _strip_ns(ch.tag).lower() != "item":
                continue
            cand: Optional[Dict[str, Any]] = None
            dref = _attr(ch, "drawable")
            if dref:
                cand = self.resolve(dref, depth + 1)
            else:
                for sub in ch:
                    cand = self._parse_xml_el(sub, name, depth + 1)
                    if cand is not None:
                        break
            if cand is None:
                continue
            if cand.get("kind") == "color" and cand.get("alpha", 1.0) <= 0.01:
                continue
            if cand.get("kind") in ("vector", "raster"):
                return cand
            if best is None:
                best = cand
        return best


_RESOLVER_CACHE: Dict[str, DrawableResolver] = {}


def get_drawable_resolver(app_root: Optional[str]) -> Optional[DrawableResolver]:
    if not app_root:
        return None
    norm = os.path.abspath(app_root)
    if norm not in _RESOLVER_CACHE:
        _RESOLVER_CACHE[norm] = DrawableResolver(norm)
    return _RESOLVER_CACHE[norm]


_VECTOR_PNG_CACHE: Dict[Tuple[Any, ...], bytes] = {}
_COLOR_SCRIM_CACHE: Dict[Tuple[str, int], bytes] = {}


def _hex_to_rgba(hex_rgb: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = (hex_rgb or "#3b4963").lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha
    return 59, 73, 99, alpha


def _signed_area(pts: List[Tuple[float, float]]) -> float:
    area = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def _bbox(pts: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _inside_bbox(inner: Tuple[float, float, float, float], outer: Tuple[float, float, float, float]) -> bool:
    return (inner[0] >= outer[0] + 0.05 and inner[1] >= outer[1] + 0.05
            and inner[2] <= outer[2] - 0.05 and inner[3] <= outer[3] - 0.05)


def rasterize_vector_to_png(
    spec: Dict[str, Any],
    w: float,
    h: float,
    *,
    dark_bg: bool = False,
    alpha: float = 1.0,
    tint: Optional[str] = None,
) -> Optional[bytes]:
    """Rasterisiert ein geparstes ``<vector>`` mit 2x-Supersampling & Subpath-Lochmasken nach PNG."""
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        return None
    tw = max(4, int(round(w)))
    th = max(4, int(round(h)))
    a_clamped = max(0.0, min(1.0, float(alpha)))
    groups = spec.get("path_groups")
    if not groups:
        groups = [
            {
                "subpaths": [(p.get("points") or [], p.get("closed", True))],
                "fill": p.get("fill"),
                "stroke": p.get("stroke"),
                "stroke_w": p.get("stroke_w", 1.0),
                "fill_type": "nonZero",
            }
            for p in (spec.get("paths") or [])
        ]
    if not groups:
        return None
    sig_raw = repr((
        spec.get("vw"), spec.get("vh"), spec.get("tint"),
        [(g.get("fill"), g.get("stroke"), g.get("fill_type"), len(g.get("subpaths") or ()),
          tuple(tuple((round(pt[0], 2), round(pt[1], 2)) for pt in sp[:4]) for sp, _ in (g.get("subpaths") or ())))
         for g in groups]
    )).encode("utf-8", "replace")
    ckey = (hashlib.sha1(sig_raw).hexdigest()[:16], tw, th, bool(dark_bg), round(a_clamped, 2), tint or "")
    cached = _VECTOR_PNG_CACHE.get(ckey)
    if cached is not None:
        return cached

    sw, sh = max(8, min(512, tw * 2)), max(8, min(512, th * 2))
    vw = float(spec.get("vw", 24.0) or 24.0)
    vh = float(spec.get("vh", 24.0) or 24.0)
    pad = min(4.0, min(sw, sh) * 0.06)
    sc = min(max(4.0, sw - 2 * pad) / vw, max(4.0, sh - 2 * pad) / vh)
    ox = (sw - vw * sc) * 0.5
    oy = (sh - vh * sc) * 0.5

    canvas = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    for grp in groups:
        fill = grp.get("fill") or ""
        stroke = grp.get("stroke") or ""
        if tint:
            if stroke and not fill:
                stroke = tint
            elif fill:
                fill = tint
        if fill and not dark_bg and _is_very_light(fill):
            fill = "#3b4963"
        if stroke and not dark_bg and _is_very_light(stroke):
            stroke = "#3b4963"
        subs = [(pts, closed) for pts, closed in (grp.get("subpaths") or ()) if len(pts) >= 2]
        if not subs:
            continue
        fill_subs = [(pts, closed) for pts, closed in subs if len(pts) >= 3]
        if fill and fill_subs:
            mask = Image.new("L", (sw, sh), 0)
            root_sign = 0.0
            outer_boxes: List[Tuple[float, float, float, float]] = []
            even_odd = str(grp.get("fill_type", "")).lower() == "evenodd"
            for idx, (pts, _closed) in enumerate(fill_subs):
                spts = [(ox + px * sc, oy + py * sc) for px, py in pts]
                sub_m = Image.new("L", (sw, sh), 0)
                ImageDraw.Draw(sub_m).polygon(spts, fill=255)
                cur_sign = _signed_area(pts)
                cur_box = _bbox(pts)
                if idx == 0:
                    mask = sub_m
                    root_sign = cur_sign
                    outer_boxes.append(cur_box)
                elif even_odd or (root_sign != 0.0 and cur_sign != 0.0 and (root_sign > 0) != (cur_sign > 0)) or any(
                        _inside_bbox(cur_box, ob) for ob in outer_boxes):
                    mask = ImageChops.difference(mask, sub_m)
                else:
                    mask = ImageChops.lighter(mask, sub_m)
                    outer_boxes.append(cur_box)
            fill_a_byte = max(1, min(255, int(round(255.0 * float(grp.get("fill_alpha", 1.0))))))
            solid = Image.new("RGBA", (sw, sh), _hex_to_rgba(fill, fill_a_byte))
            layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            layer.paste(solid, (0, 0), mask)
            canvas = Image.alpha_composite(canvas, layer)
            draw = ImageDraw.Draw(canvas)
        if stroke:
            stroke_a_byte = max(1, min(255, int(round(255.0 * float(grp.get("stroke_alpha", 1.0))))))
            s_rgba = _hex_to_rgba(stroke, stroke_a_byte)
            lw = max(1, int(round(float(grp.get("stroke_w", 1.0)) * sc)))
            for pts, closed in subs:
                spts = [(ox + px * sc, oy + py * sc) for px, py in pts]
                if closed and len(spts) >= 3:
                    spts = spts + [spts[0]]
                try:
                    draw.line(spts, fill=s_rgba, width=lw, joint="curve")
                except TypeError:
                    draw.line(spts, fill=s_rgba, width=lw)

    out_im = canvas.resize((tw, th), Image.LANCZOS)
    if a_clamped < 0.999:
        r, g, b, al = out_im.split()
        al = al.point(lambda p: int(round(p * a_clamped)))
        out_im = Image.merge("RGBA", (r, g, b, al))
    buf = io.BytesIO()
    out_im.save(buf, format="PNG")
    out_bytes = buf.getvalue()
    if len(_VECTOR_PNG_CACHE) >= 512:
        _VECTOR_PNG_CACHE.clear()
    _VECTOR_PNG_CACHE[ckey] = out_bytes
    return out_bytes


def prepare_pil_rgba(
    src,
    *,
    is_9patch: bool = False,
    dark_bg: bool = False,
    pre_tinted: bool = False,
    alpha: float = 1.0,
):
    """Normalisiert RGBA-Bilder fuer DisplayTab & surface_raster (9-Patch, Weiss-auf-Hell, Alpha)."""
    im = src.convert("RGBA")
    if is_9patch and im.width > 2 and im.height > 2:
        im = im.crop((1, 1, im.width - 1, im.height - 1))
    if not dark_bg and not pre_tinted:
        from PIL import Image
        probe = im if (im.width * im.height <= 4096) else im.resize(
            (min(32, im.width), min(32, im.height)), Image.NEAREST
        )
        pxs = list(probe.get_flattened_data()) if hasattr(probe, "get_flattened_data") else list(probe.getdata())
        if pxs:
            min_a = min(p[3] for p in pxs)
            vis = [p for p in pxs if p[3] > 32]
            if min_a < 200 and vis and all(p[0] >= 235 and p[1] >= 235 and p[2] >= 235 for p in vis):
                _, _, _, al = im.split()
                im = Image.merge("RGBA", (
                    al.point(lambda _p: 59),
                    al.point(lambda _p: 73),
                    al.point(lambda _p: 99),
                    al,
                ))
    a_clamped = max(0.0, min(1.0, float(alpha)))
    if a_clamped < 0.999:
        r, g, b, al = im.split()
        al = al.point(lambda p: int(round(p * a_clamped)))
        from PIL import Image
        im = Image.merge("RGBA", (r, g, b, al))
    return im


def drawable_to_paint_commands(
    spec: Optional[Dict[str, Any]],
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    is_background: bool = False,
    dark_bg: bool = False,
    alpha: float = 1.0,
    scale: Optional[str] = None,
    fixed_y: bool = False,
    tint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Konvertiert einen aufgeloesten Drawable-Spec in `paint_commands`-Eintraege."""
    if not spec or w <= 1 or h <= 1:
        return []
    kind = spec.get("kind")
    if kind == "raster":
        is_9p = bool(spec.get("is_9patch"))
        eff_scale = scale or ("stretch" if (is_background and is_9p) else "fit")
        cmd: Dict[str, Any] = {
            "op": "image",
            "x": x, "y": y, "w": w, "h": h,
            "path": spec.get("path", ""),
            "name": spec.get("name", ""),
            "is_9patch": is_9p,
            "scale": eff_scale,
            "dark_bg": bool(dark_bg),
        }
        if alpha < 0.999:
            cmd["alpha"] = float(alpha)
        if fixed_y:
            cmd["fixed_y"] = True
        return [cmd]
    def _color_scrim_cmd(col, eff_a, name="scrim"):
        try:
            from PIL import Image
            a_byte = max(1, min(255, int(round(eff_a * 255))))
            scrim_key = (str(col).lower(), a_byte)
            png_bytes = _COLOR_SCRIM_CACHE.get(scrim_key)
            if png_bytes is None:
                im = Image.new("RGBA", (4, 4), _hex_to_rgba(col, a_byte))
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                png_bytes = buf.getvalue()
                if len(_COLOR_SCRIM_CACHE) >= 256:
                    _COLOR_SCRIM_CACHE.clear()
                _COLOR_SCRIM_CACHE[scrim_key] = png_bytes
            return [{
                "op": "image",
                "x": x, "y": y, "w": w, "h": h,
                "data": png_bytes,
                "name": name,
                "scale": "stretch",
                "pre_tinted": True,
                "dark_bg": True,
            }]
        except Exception:
            return None

    if kind == "color":
        eff_a = max(0.0, min(1.0, float(spec.get("alpha", 1.0)) * float(alpha)))
        col = spec.get("color")
        if eff_a <= 0.01 or not col:
            return []
        if eff_a < 0.96:
            scrim = _color_scrim_cmd(col, eff_a, spec.get("name", "scrim"))
            if scrim:
                return scrim
        return [{"op": "rect", "x": x, "y": y, "w": w, "h": h,
                 "fill": col, "outline": "", "r": 0}]
    if kind == "shape":
        fill = spec.get("fill") or ""
        outline = spec.get("outline") or ""
        rad = float(spec.get("radius", 0.0) or 0.0)
        if not fill and not outline:
            return []
        spec_a = float(spec.get("alpha", 1.0) if spec.get("alpha") is not None else 1.0)
        if isinstance(fill, str) and len(fill) == 9 and fill.startswith("#"):
            try:
                spec_a *= int(fill[1:3], 16) / 255.0
            except Exception:
                pass
        eff_a = max(0.0, min(1.0, spec_a * float(alpha)))
        if eff_a <= 0.01 and not outline:
            return []
        if 0.01 < eff_a < 0.96 and fill and rad <= 4.0 and spec.get("shape") != "oval":
            scrim = _color_scrim_cmd(fill, eff_a, spec.get("name", "scrim"))
            if scrim:
                return scrim
        if is_background and not dark_bg and fill and not outline and _is_very_light(fill) and rad > 0:
            if fill.lower() in ("#ffffff", "#ffffffff", "#fafafa", "#f9fafb"):
                fill = "#f1f5f9"
            outline = "#cbd5e1"
        return [{
            "op": "oval" if spec.get("shape") == "oval" else "rect",
            "x": x, "y": y, "w": w, "h": h,
            "fill": fill,
            "outline": outline,
            "r": rad,
        }]
    if kind == "vector":
        vw_px = w * 2.0 if (w < 40.0 and h < 40.0) else w
        vh_px = h * 2.0 if (w < 40.0 and h < 40.0) else h
        png_bytes = rasterize_vector_to_png(spec, vw_px, vh_px, dark_bg=dark_bg, alpha=alpha, tint=tint)
        if png_bytes:
            vcmd: Dict[str, Any] = {
                "op": "image",
                "x": x, "y": y, "w": w, "h": h,
                "data": png_bytes,
                "name": spec.get("name", "vector"),
                "scale": "fit",
                "pre_tinted": True,
                "dark_bg": bool(dark_bg),
            }
            if fixed_y:
                vcmd["fixed_y"] = True
            return [vcmd]
        vw = float(spec.get("vw", 24.0) or 24.0)
        vh = float(spec.get("vh", 24.0) or 24.0)
        pad = min(4.0, min(w, h) * 0.12)
        aw = max(4.0, w - 2 * pad)
        ah = max(4.0, h - 2 * pad)
        sc = min(aw / vw, ah / vh)
        ox = x + (w - vw * sc) * 0.5
        oy = y + (h - vh * sc) * 0.5
        cmds: List[Dict[str, Any]] = []
        for p in (spec.get("paths") or []):
            pts = p.get("points") or []
            if len(pts) < 2:
                continue
            flat: List[float] = []
            for px, py in pts:
                flat.append(ox + px * sc)
                flat.append(oy + py * sc)
            fill = p.get("fill") or ""
            stroke = p.get("stroke") or ""
            if tint:
                if stroke and not fill:
                    stroke = tint
                elif fill:
                    fill = tint
            if fill and not dark_bg and _is_very_light(fill):
                fill = "#3b4963"
            if stroke and not dark_bg and _is_very_light(stroke):
                stroke = "#3b4963"
            if len(pts) >= 3 and (p.get("closed", True) or fill):
                cmds.append({
                    "op": "polygon",
                    "points": flat,
                    "fill": fill,
                    "outline": stroke,
                    "width": max(1.0, float(p.get("stroke_w", 1.0)) * min(1.5, sc)),
                })
            else:
                cmds.append({
                    "op": "polyline",
                    "points": flat,
                    "color": stroke or fill or "#3b4963",
                    "width": max(1.0, float(p.get("stroke_w", 1.0)) * min(1.5, sc)),
                })
        return cmds
    return []
