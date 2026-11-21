# -*- coding: utf-8 -*-
"""core.java.view_tree — ViewTree-Modell fuer die Anzeige-/UI-Saeule (Vision Saeule 4).

Pures, zielagnostisches Modell des von der App aufgebauten View-Baums (Struktur + Texte + Props +
Bounds). Dient dem Anzeige-Fenster (Rendering) und der Input-Injektion. `install_view_stubs` haengt
generische android.widget/view-Stubs in `AndroidStubs` ein, die den Baum beim App-Lauf aufzeichnen.
Headless testbar (Layout = einfache vertikale Stapelung; hit_test).
"""
from __future__ import annotations

import struct as _struct

DEFAULT_W, DEFAULT_H = 420, 760
_ROW_H = 44
_PAD = 10
_GAP = 8
_LEAF = ("TextView", "Button", "EditText", "ImageView", "CheckBox", "Switch",
         "ToggleButton", "CompoundButton", "ProgressBar", "SeekBar", "RadioButton", "DatePicker")
_CONTAINER_CLS = (
    "LinearLayout", "RelativeLayout", "FrameLayout", "ConstraintLayout",
    "CoordinatorLayout", "ScrollView", "NestedScrollView", "HorizontalScrollView",
    "ViewGroup", "CardView", "MaterialCardView", "RadioGroup",
    "TableLayout", "TableRow", "GridLayout", "DrawerLayout",
    "SwipeRefreshLayout", "AppBarLayout", "CollapsingToolbarLayout",
    "ViewPager", "ViewPager2", "DisableableViewPager", "RecyclerView", "ListView",
)


class ViewNode:
    def __init__(self, cls="View", vid=None, text="", clickable=False, visible=True,
                 props=None, focusable=False):
        self.cls = cls
        self.vid = vid
        self.text = text
        self.clickable = clickable
        self.visible = visible
        self.focusable = focusable
        self.props = dict(props or {})
        self.children = []
        self.bounds = (0, 0, 0, 0)   # x, y, w, h (nach layout)
        self.on_click = None         # Listener-Objekt (fuer Input-Dispatch)
        self._parent = None          # Rueckverweis (fuer RadioGroup-Single-Select)
        self._jobj = None            # Gecachtes Android-View-JObject

    def add(self, child):
        child._parent = self
        self.children.append(child)
        return child

    def find(self, vid):
        if vid is not None and self.vid == vid:
            return self
        for c in self.children:
            r = c.find(vid)
            if r is not None:
                return r
        return None

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def walk_visible(self):
        if not self.visible:
            return
        yield self
        for c in self.children:
            yield from c.walk_visible()

    def is_leaf(self):
        if self.cls == "EditText" and self.children:
            return False
        if self.cls in _CONTAINER_CLS or self.cls.endswith(("Layout", "ViewGroup", "ViewPager", "ScrollView")):
            return False
        return self.cls in _LEAF or not self.children

    def short(self):
        ident = ("#%s" % self.vid) if self.vid is not None else ""
        t = (" '%s'" % self.text) if self.text else ""
        return "%s%s%s" % (self.cls, ident, t)

    def to_dict(self):
        return {"cls": self.cls, "id": self.vid, "text": self.text,
                "clickable": self.clickable, "visible": self.visible, "bounds": self.bounds,
                "children": [c.to_dict() for c in self.children]}


def _is_indicator_dot(node):
    if node.cls != "ImageView" or node.clickable or (node.text or "").strip():
        return False
    sp = node.props.get("drawable")
    if not isinstance(sp, dict):
        return False
    kind = sp.get("kind")
    if kind in ("shape", "vector"):
        return True
    if kind == "raster":
        nm = str(sp.get("name", "") or "").lower()
        return any(k in nm for k in ("indicator", "dot", "bullet"))
    return False


def _is_indicator_row(node):
    if node.cls not in ("RelativeLayout", "LinearLayout", "FrameLayout"):
        return False
    vis = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True) and not _is_collapsed_node(c)]
    return 2 <= len(vis) <= 8 and all(_is_indicator_dot(c) for c in vis)


def _is_horizontal(node):
    o = node.props.get("orientation")
    if o in ("horizontal", 0, "0"):
        return True
    if "Toolbar" in (node.cls or ""):
        return True
    if _is_indicator_row(node):
        return True
    if node.cls in ("ConstraintLayout", "RelativeLayout"):
        vis = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True) and not _is_collapsed_node(c)]
        if len(vis) == 2 and all(
            c.cls in ("TextView", "Button") and c.clickable and 1 <= len((c.text or "").strip()) <= 20
            for c in vis
        ):
            return True
    return False


def _is_pref_row(node):
    if node is None:
        return False
    return bool(
        node.find("widget_frame") is not None
        or (node.cls == "LinearLayout" and node.find("title") is not None and node.find("summary") is not None)
    )


def _in_compact_grid_item(node):
    if node.vid in ("option", "bottom_navigation_container"):
        return True
    if "ToggleOption" in str(node.props.get("full_cls") or node.cls):
        return True
    p = getattr(node, "_parent", None)
    for _ in range(4):
        if p is None:
            break
        if p.vid in ("option", "bottom_navigation_container") or int(p.props.get("span_count", 1) or 1) >= 2:
            return True
        p = getattr(p, "_parent", None)
    return False


def _default_node_pad(node):
    role = node.props.get("role")
    if role == "topbar":
        return 8, 6
    if role == "navbar":
        return 6, 4
    if role == "sticky_header":
        return 8, 4
    if role == "card":
        return 10, 8
    if role == "scaffold":
        return 0, 0
    if node.props.get("$reusable_node"):
        return 4, 2
    if _in_compact_grid_item(node):
        return 3, 2
    if _is_indicator_row(node):
        return 6, 3
    if node.cls == "RelativeLayout" and node.find("title") is not None and node.find("summary") is not None:
        return 4, 2
    if node.cls in ("RecyclerView", "ListView") or node.vid in ("list_container", "settings_container"):
        return 2, 2
    if node.vid in ("widget_frame", "icon_frame"):
        return 2, 2
    if _is_pref_row(node):
        return 8, 5
    if (getattr(node, "_parent", None) is not None and not role
            and not node.props.get("bg_drawable") and not node.clickable):
        vis = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True) and not _is_collapsed_node(c)]
        if len(vis) == 1 and (not vis[0].is_leaf() or vis[0].cls == "EditText"):
            return 2, 2
    return _PAD, _PAD


def _node_pad(node):
    dx, dy = _default_node_pad(node)
    if "$pad_x" in node.props:
        dx = int(node.props["$pad_x"])
    if "$pad_y" in node.props:
        dy = int(node.props["$pad_y"])
    return dx, dy


def _node_gap(node):
    role = node.props.get("role")
    if role in ("card", "sticky_header"):
        return 4
    if role == "scaffold":
        return 2
    if node.props.get("$reusable_node") or _in_compact_grid_item(node):
        return 4
    if node.cls == "RelativeLayout" and node.find("title") is not None and node.find("summary") is not None:
        return 2
    if _is_pref_row(node):
        return 6
    return _GAP


def _wrap_line_count(text, avail_w, font_size=10):
    s = str(text or "")
    if not s:
        return 1
    max_chars = max(10, int((max(40, float(avail_w or DEFAULT_W)) - 12) / max(5.8, font_size * 0.68)))
    total = 0
    for para in s.split("\n"):
        words = para.split()
        if not words:
            total += 1
            continue
        lines = 1
        cur = 0
        for wd in words:
            wlen = len(wd)
            if cur == 0:
                cur = wlen
            elif cur + 1 + wlen <= max_chars:
                cur += 1 + wlen
            else:
                lines += 1
                cur = wlen
            while cur > max_chars:
                lines += 1
                cur -= max_chars
        total += lines
    return max(1, total)


def _eff_alpha(node):
    if "alpha" in node.props and node.props["alpha"] is not None:
        return float(node.props["alpha"])
    if "img_alpha" in node.props and node.props["img_alpha"] is not None:
        return float(node.props["img_alpha"])
    return 1.0


def _is_collapsed_node(node):
    if not getattr(node, "visible", True):
        return True
    if node.props.get("non_visual"):
        return True
    if "blurview" in str(node.props.get("full_cls") or node.cls).lower() and not node.children:
        return True
    fcls_low = str(node.props.get("full_cls") or node.cls).lower()
    if "composeview" in fcls_low and not node.children and not (node.text or "").strip() and not node.clickable:
        return True
    if "bottomappbar" in fcls_low and not node.children:
        par = getattr(node, "_parent", None)
        if par is not None and any(
            s is not node and s.visible and (s.vid == "bottom_navigation_holder" or s.props.get("role") == "navbar")
            for s in par.children
        ):
            return True
    if _eff_alpha(node) <= 0.01:
        return True
    if node.cls == "ImageView" and node.props.get("src_null"):
        if not node.props.get("drawable") and not node.props.get("img_bytes") and not (node.props.get("img") or "").strip():
            return True
    if node.cls == "ImageView" and node.vid == "icon" and not node.clickable:
        if not node.props.get("drawable") and not node.props.get("img_bytes") and not (node.props.get("img") or "").strip() and not node.props.get("src_ref"):
            return True
    if node.cls == "ProgressBar" and "TransparentBg" in str(node.props.get("style") or ""):
        par = getattr(node, "_parent", None)
        if par is not None and any(_is_indicator_row(s) and s.visible for s in par.children):
            return True
    if node.cls == "View" and not node.children and not (node.text or "").strip():
        if node.props.get("$cl_ratio") or node.props.get("$compose_spacer"):
            return False
        xw = node.props.get("xml_w")
        xh = node.props.get("xml_h")
        if (xw is not None and int(xw) <= 2) or (xh is not None and int(xh) <= 2):
            return True
        has_bg = bool(node.props.get("bg_drawable") or node.props.get("bg") or node.props.get("bg_ref"))
        if not node.clickable and "h" not in node.props and (xh is None or int(xh) <= 2):
            if not (has_bg and node.props.get("xml_h_mode") in ("match", "zero")):
                return True
        if not node.clickable and not has_bg:
            return True
    if node.cls == "TextView" and not (node.text or "").strip() and not node.clickable:
        if not node.props.get("bg_drawable") and not node.props.get("compound_drawable") and "h" not in node.props:
            return True
    return False


def _sync_composite_edittext(node):
    if node.cls == "EditText" and node.children:
        node.focusable = False
        hint = node.props.get("hint")
        for sub in node.walk():
            if sub is not node and sub.cls == "EditText" and not sub.children:
                if hint and not sub.props.get("hint"):
                    sub.props["hint"] = hint
                if node.text and not sub.text:
                    sub.text = node.text
                break


def _is_concentric_overlay(node, parent):
    tgt = node.props.get("$cl_same_v_target")
    if not tgt or parent is None:
        return False
    return any(s is not node and s.vid == tgt and s.visible and not _is_collapsed_node(s) for s in parent.children)


def _is_overlay_scrim_or_click(node, parent):
    """True for overlay scrim/click-capture Views meant to cover siblings in Z-order."""
    if parent is None or parent.cls not in ("FrameLayout", "CardView", "ConstraintLayout", "RelativeLayout"):
        return False
    if node.cls != "View" or node.children or (node.text or "").strip():
        return False
    if not any(s is not node and s.visible and not _is_collapsed_node(s) for s in parent.children):
        return False
    if node.vid == "click_overlay":
        return True
    if node.props.get("xml_h_mode") in ("match", "zero") and (
        (node.props.get("$cl_top_parent") and node.props.get("$cl_bot_parent"))
        or (parent.cls == "FrameLayout" and node.clickable)
    ):
        return True
    return bool(node.clickable and not node.props.get("bg_drawable"))


def _is_zstack_layer(node):
    if node.props.get("xml_h_mode") == "match":
        return True
    if node.props.get("xml_h_mode") == "zero" and node.props.get("$cl_top_parent") and node.props.get("$cl_bot_parent"):
        return True
    return False


def _parse_cl_ratio(raw):
    if not raw:
        return None
    s = str(raw).strip()
    if "," in s:
        s = s.split(",", 1)[-1].strip()
    if ":" in s:
        p0, p1 = s.split(":", 1)
        try:
            a, b = float(p0), float(p1)
            if a > 0 and b > 0:
                return a / b
        except Exception:
            return None
    try:
        v = float(s)
        return v if v > 0 else None
    except Exception:
        return None


def _cl_guide_splits(cl_node, cw):
    out = {}
    for ch in getattr(cl_node, "children", ()) or ():
        if ch.cls == "Guideline" and ch.vid and ch.props.get("$cl_guide_orient", "vertical") == "vertical":
            if "$cl_guide_pct" in ch.props:
                out[ch.vid] = max(0.05, min(0.95, float(ch.props["$cl_guide_pct"])))
            elif "$cl_guide_begin" in ch.props and cw > 0:
                out[ch.vid] = max(0.05, min(0.95, float(ch.props["$cl_guide_begin"]) / float(cw)))
            elif "$cl_guide_end" in ch.props and cw > 0:
                out[ch.vid] = max(0.05, min(0.95, (float(cw) - float(ch.props["$cl_guide_end"])) / float(cw)))
    return out


def _cl_is_h_pair(a, b, guides):
    if a.vid and b.props.get("$cl_start_to_end") == a.vid:
        return True
    if b.vid and a.props.get("$cl_end_to_start") == b.vid:
        return True
    if guides:
        ga = a.props.get("$cl_end_to_start") or a.props.get("$cl_end_to_end")
        gb = b.props.get("$cl_start_to_end") or b.props.get("$cl_start_to_start")
        if ga and ga == gb and ga in guides:
            return True
    return False


def _cl_row_slots(peers, cw, x0, gap, guides):
    n = len(peers)
    if n == 2 and guides:
        ga = peers[0].props.get("$cl_end_to_start") or peers[0].props.get("$cl_end_to_end")
        gb = peers[1].props.get("$cl_start_to_end") or peers[1].props.get("$cl_start_to_start")
        if ga and ga == gb and ga in guides:
            gx = x0 + int(round(cw * guides[ga]))
            w0 = max(20, gx - x0 - gap // 2)
            w1 = max(20, (x0 + cw) - (gx + gap - gap // 2))
            return [(peers[0], x0, w0), (peers[1], x0 + w0 + gap, w1)]
    chain_style = str(peers[0].props.get("$cl_h_chain") or "").lower()
    if chain_style == "packed" and all(p.props.get("xml_w") or p.props.get("w") for p in peers):
        ws = [max(24, int(p.props.get("w") or p.props.get("xml_w"))) for p in peers]
        tot = sum(ws) + gap * (n - 1)
        bias = float(peers[0].props.get("$cl_h_bias", 0.5))
        cx = x0 + int(round(max(0, cw - tot) * bias))
        slots = []
        for p, pw in zip(peers, ws):
            slots.append((p, cx, pw))
            cx += pw + gap
        return slots
    fixed = {}
    used = gap * max(0, n - 1)
    for idx, p in enumerate(peers):
        if p.props.get("w") or (p.props.get("xml_w") and p.props.get("xml_w_mode") != "zero"):
            pw = max(24, min(cw // 2, int(p.props.get("w") or p.props.get("xml_w"))))
            fixed[idx] = pw
            used += pw
    flex = [i for i in range(n) if i not in fixed]
    rem = max(20, cw - used)
    per = max(24, rem // len(flex)) if flex else 0
    slots = []
    cx = x0
    for idx, p in enumerate(peers):
        pw = fixed.get(idx, per)
        slots.append((p, cx, pw))
        cx += pw + gap
    return slots


def _cl_group_rows(cl_node, seq_kids, cw):
    guides = _cl_guide_splits(cl_node, cw)
    rows = []
    i = 0
    while i < len(seq_kids):
        cur = [seq_kids[i]]
        while i + 1 < len(seq_kids) and _cl_is_h_pair(cur[-1], seq_kids[i + 1], guides):
            i += 1
            cur.append(seq_kids[i])
        rows.append(cur)
        i += 1
    return rows, guides


def _measure(node, avail_w=DEFAULT_W):
    _sync_composite_edittext(node)
    if _is_collapsed_node(node):
        return 0
    if node.is_leaf():
        if "h" in node.props:
            return int(node.props["h"])
        if node.props.get("$cl_ratio") and avail_w and (
            node.props.get("xml_h_mode") == "zero" or "xml_h" not in node.props
        ):
            _rat = _parse_cl_ratio(node.props.get("$cl_ratio"))
            if _rat:
                _weff = int(node.props.get("w") or node.props.get("xml_w") or avail_w)
                return max(16, int(round(float(_weff) / _rat)))
        if node.cls in ("ImageView", "ImageButton"):
            if node.vid == "bottom_navigation_item_icon":
                return 24
            par = getattr(node, "_parent", None)
            if par is not None and _is_indicator_dot(node) and _is_horizontal(par):
                return 14
            if "xml_h" in node.props:
                return max(16, min(140, int(node.props["xml_h"])))
            if str(node.props.get("scale_type") or "").upper() == "CENTER_CROP" and node.props.get("drawable"):
                return 180
            return 68
        if node.cls == "TextView":
            if node.vid == "bottom_navigation_item_title":
                return 16
            par = getattr(node, "_parent", None)
            in_compact = False
            p = par
            while p is not None:
                if (
                    p.props.get("role") in ("card", "sticky_header", "topbar")
                    or p.props.get("$reusable_node")
                    or _is_pref_row(p)
                    or _in_compact_grid_item(p)
                ):
                    in_compact = True
                    break
                p = getattr(p, "_parent", None)
            base_h = int(node.props["h"]) if "h" in node.props else (
                20 if node.vid in ("title", "summary") else (22 if in_compact else _ROW_H)
            )
            fnt = 8 if node.vid == "summary" else (9 if in_compact else 10)
            lines = _wrap_line_count(node.text, avail_w, font_size=fnt)
            if lines > 1:
                return max(base_h, (10 if in_compact else 14) + lines * (16 if in_compact else 19))
            return base_h
        if node.cls == "Button":
            if not (node.text or "").strip() and (node.props.get("bg_drawable") or node.props.get("bg_ref")):
                return max(48, min(76, int(node.props.get("xml_h", 60))))
            par = getattr(node, "_parent", None)
            if par is not None and par.props.get("role") == "topbar":
                return 34
            if par is not None and par.props.get("role") == "navbar":
                return 46
            if _is_horizontal(par) if par is not None else False:
                return 36
        if (node.cls == "View" and not node.children and not (node.text or "").strip()
                and node.clickable and not node.props.get("bg") and not node.props.get("bg_drawable")):
            par = getattr(node, "_parent", None)
            if (par is not None and par.cls in ("FrameLayout", "ConstraintLayout", "RelativeLayout")
                    and any(s.visible and s is not node and not _is_collapsed_node(s) for s in par.children)):
                return 0
        return int(node.props.get("h", _ROW_H))
    kids = [c for c in node.children if c.visible and not _is_collapsed_node(c)]
    if not kids:
        if ("h" in node.props or node.clickable
                or node.props.get("role") in ("topbar", "navbar", "card", "dialog")):
            return int(node.props.get("h", _ROW_H))
        return 0
    px, py = _node_pad(node)
    gap = _node_gap(node)
    cw = max(20, float(avail_w or DEFAULT_W) - 2 * px)
    if _is_indicator_row(node):
        return 20
    cols = int(node.props.get("span_count") or 1)
    if cols >= 2:
        grid_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        if not grid_kids:
            return 0
        cell_w = max(40.0, (cw - gap * (cols - 1)) / cols)
        gh = py
        rows_nz = 0
        for i in range(0, len(grid_kids), cols):
            row_h = max((_measure(c, cell_w) for c in grid_kids[i:i + cols]), default=0)
            if row_h > 0:
                gh += row_h + gap
                rows_nz += 1
        return max(_ROW_H, gh + py - gap) if rows_nz else 0
    if _is_horizontal(node):
        widths = ViewTree._calc_horizontal_widths(node, kids, cw, gap)
        mh = max(_measure(c, max(20, w_c)) for c, w_c in zip(kids, widths)) + 2 * py
        if node.props.get("role") == "topbar":
            return max(48, mh)
        if node.props.get("role") == "navbar":
            return max(54, mh)
        return mh
    if node.cls in ("ConstraintLayout", "FrameLayout") and len(kids) >= 2:
        z_m = [c for c in kids if _is_zstack_layer(c) or c.vid in ("images_holder",)]
        if len(z_m) >= 2 or (len(z_m) >= 1 and any(c.props.get("$cl_bot_parent") or c.vid in ("constraint_layout", "indicator_holder") for c in kids)):
            return max(_measure(c, cw) for c in kids) + 2 * py
    if node.cls == "ConstraintLayout" and len(kids) >= 2:
        cl_flow = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        rows, guides = _cl_group_rows(node, cl_flow, cw)
        if any(len(r) >= 2 for r in rows):
            h = py
            non_zero = 0
            for r in rows:
                if len(r) >= 2:
                    slots = _cl_row_slots(r, cw, 0, gap, guides)
                    rh = max((_measure(p, pw) for p, _, pw in slots), default=0)
                else:
                    rh = _measure(r[0], cw)
                if rh > 0:
                    h += rh + gap
                    non_zero += 1
            return max(_ROW_H, h + py - gap) if non_zero else 0
    h = py
    non_zero = 0
    for c in kids:
        if _is_concentric_overlay(c, node) or _is_overlay_scrim_or_click(c, node):
            continue
        ch = _measure(c, cw)
        if ch > 0:
            h += ch + gap
            non_zero += 1
    if non_zero == 0:
        return 0
    min_h = 180 if node.cls == "WebView" else (24 if (
        node.props.get("role") in ("sticky_header", "card")
        or node.props.get("$reusable_node")
        or _in_compact_grid_item(node)
    ) else _ROW_H)
    return max(min_h, h + py - gap)


class ViewTree:
    def __init__(self, width=DEFAULT_W, height=DEFAULT_H):
        self.width = width
        self.height = height
        self.root = None
        self.version = 0
        self.dark_mode = False
        self.window_bg = None
        self.window_bg_dark = False
        self.web_events = []
        self.last_toast = ""

    def effective_dark(self):
        if self.window_bg is not None and not self.window_bg_dark:
            rrole = self.root.props.get("role") if self.root is not None else None
            if rrole != "scaffold":
                return False
        return bool(self.dark_mode)

    def set_root(self, node):
        self.root = node
        self.layout()
        self.version += 1

    def touch(self):
        self.layout()
        self.version += 1

    def layout(self):
        if self.root is not None:
            if callable(getattr(self, "_custom_layout_fn", None)):
                self._custom_layout_fn(self.root, self.width, self.height)
                return
            for n in self.root.walk():
                n.props.pop("$occluded_by_blur", None)
                n.props.pop("$fill_h", None)
                n.props.pop("$draw_center_divider_y", None)
            self._lay(self.root, 0, 0, self.width)

    @staticmethod
    def _calc_horizontal_widths(node, kids, inner, gap):
        role = node.props.get("role")
        n = len(kids)
        if role == "topbar" and n >= 2 and kids[0].cls == "TextView":
            act_ws = []
            for k in kids[1:]:
                t = (k.text or "")
                act_ws.append(42 if len(t) <= 3 else min(90, max(54, 14 + len(t) * 8)))
            used = sum(act_ws) + gap * n
            title_w = max(80, inner - used)
            return [title_w] + act_ws
        # Week navigation pattern: [Button("<"), TextView("16.11. - 22.11."), Button(">")]
        if (n == 3 and kids[0].cls == "Button" and kids[2].cls == "Button"
                and kids[1].cls == "TextView" and len(kids[0].text or "") <= 3 and len(kids[2].text or "") <= 3):
            bw = 54
            kids[1].props.setdefault("gravity", 1)
            return [bw, max(60, inner - 2 * bw - 2 * gap), bw]
        # Bullet list row: [' • ', 'Customisable nutrition plan'] (Compose / XML rows)
        if (n == 2 and kids[0].cls == "TextView"
                and (kids[0].text or "").strip() in ("•", "-", "*", "·", "✓", "✔", "✕", "×")):
            bw = 18
            return [bw, max(40, inner - bw - gap)]
        # Event card time/capacity row: [TextView("17.11. | ..."), TextView("12 / 15")]
        if n == 2 and kids[0].cls == "TextView" and kids[1].cls == "TextView":
            t1 = (kids[1].text or "").strip()
            if len(t1) <= 16 and any(ch.isdigit() for ch in t1):
                p = getattr(node, "_parent", None)
                in_card = False
                while p is not None:
                    if p.props.get("role") == "card":
                        in_card = True
                        break
                    p = getattr(p, "_parent", None)
                if in_card:
                    w1 = min(96, max(56, inner // 4))
                    kids[1].props.setdefault("gravity", 5)
                    return [max(80, inner - w1 - gap), w1]
        if any(k.props.get("layout_weight") or k.vid in ("icon_frame", "widget_frame") for k in kids):
            fixed = {}
            weighted_idxs = []
            total_w = 0.0
            for idx, k in enumerate(kids):
                lw = float(k.props.get("layout_weight") or 0.0)
                if lw > 0:
                    weighted_idxs.append((idx, lw))
                    total_w += lw
                elif k.vid == "icon_frame":
                    has_vis_ic = any(
                        sub.cls in ("ImageView", "ImageButton")
                        and (sub.props.get("drawable") or sub.props.get("img_bytes") or (sub.props.get("img") or "").strip())
                        for sub in k.walk()
                    )
                    fixed[idx] = 40 if has_vis_ic else 0
                elif k.vid == "widget_frame":
                    fixed[idx] = 54 if any(c.visible for c in k.children) else 0
                elif k.cls in ("Switch", "CheckBox", "RadioButton") and not (k.text or "").strip():
                    fixed[idx] = 50
                else:
                    fixed[idx] = int(k.props.get("w") or k.props.get("xml_w") or 48)
            used = sum(fixed.values()) + gap * max(0, n - 1)
            rem = max(40, inner - used)
            out = [0] * n
            for idx, fw in fixed.items():
                out[idx] = fw
            if weighted_idxs and total_w > 0:
                for idx, lw in weighted_idxs:
                    out[idx] = max(40, int(rem * (lw / total_w)))
            else:
                flex = [i for i in range(n) if i not in fixed]
                per = max(40, rem // len(flex)) if flex else 0
                for i in flex:
                    out[i] = per
            return out
        if any("w" in k.props for k in kids):
            fixed = {}
            flex_idxs = []
            used = gap * max(0, n - 1)
            for idx, k in enumerate(kids):
                if "w" in k.props:
                    kw = max(20, min(inner // 2, int(k.props["w"])))
                    fixed[idx] = kw
                    used += kw
                else:
                    flex_idxs.append(idx)
            rem = max(20, inner - used)
            per_flex = max(16, rem // len(flex_idxs)) if flex_idxs else 0
            return [fixed.get(i, per_flex) for i in range(n)]
        # Compact icon + text row (e.g. Compose Row { Icon(...); Text(...) } or XML ImageView + TextView)
        if n == 2 and role not in ("navbar", "topbar"):
            k0_ic = kids[0].cls in ("ImageView", "ImageButton") or (
                kids[0].cls == "TextView" and len((kids[0].text or "").strip()) <= 2 and kids[1].cls != "ImageView"
            )
            k1_ic = kids[1].cls in ("ImageView", "ImageButton")
            if k0_ic and not k1_ic:
                ic_w = min(40, max(20, int(kids[0].props.get("xml_w") or 28)))
                return [ic_w, max(40, inner - ic_w - gap)]
            if k1_ic and not k0_ic:
                ic_w = min(40, max(20, int(kids[1].props.get("xml_w") or 28)))
                return [max(40, inner - ic_w - gap), ic_w]
        base = max(10, (inner - gap * (n - 1)) // n)
        return [base] * n

    def _horizontal_widths(self, node, kids, inner, gap):
        return self._calc_horizontal_widths(node, kids, inner, gap)

    def _lay(self, node, x, y, w, forced_h=None):
        mh = _measure(node, w)
        if node is self.root and mh <= 0:
            mh = _ROW_H
        kids = [c for c in node.children if c.visible and not _is_collapsed_node(c)]
        if forced_h is not None and forced_h > mh:
            mh = int(forced_h)
        elif node is self.root and (
            node.vid == 0xC000
            or node.props.get("$compose_root")
            or "CoordinatorLayout" in str(node.props.get("full_cls") or node.cls)
            or (
                node.cls in ("ConstraintLayout", "FrameLayout", "RelativeLayout") and (
                    node.props.get("xml_h_mode") == "match"
                    or any(_is_zstack_layer(c) for c in kids)
                    or any(c.props.get("$cl_bot_parent") or c.props.get("$cl_bot_to_top") for c in node.children)
                )
            )
        ):
            mh = max(mh, int(self.height))
            node.props["$fill_h"] = True
        node.bounds = (x, y, w, mh)
        if node.is_leaf() or mh <= 0:
            return
        if not kids:
            return
        px, py = _node_pad(node)
        gap = _node_gap(node)
        is_full_h = (node is self.root and bool(node.props.get("$fill_h"))) or bool(node.props.get("$fill_h")) or (forced_h is not None)
        if (is_full_h and (node.cls in ("ConstraintLayout", "FrameLayout", "DisableableViewPager", "ViewPager", "ViewPager2") or "ViewPager" in node.cls)
                and len(kids) == 1 and _is_zstack_layer(kids[0])):
            kids[0].props["$fill_h"] = True
            self._lay(kids[0], x, y, w, forced_h=mh)
            return
        z_layers = [c for c in kids if _is_zstack_layer(c) or c.vid in ("images_holder",)]
        if (is_full_h and node.cls in ("ConstraintLayout", "FrameLayout")
                and len(kids) >= 2 and (len(z_layers) >= 2 or (len(z_layers) >= 1 and any(c.props.get("$cl_bot_parent") or c.vid in ("constraint_layout", "indicator_holder") for c in kids)))):
            blur_active = False
            for c in kids:
                if (c.vid in ("blur", "overlay_view")
                        or (c.cls == "View" and _is_zstack_layer(c) and c.props.get("bg_drawable"))):
                    blur_active = True
                    break
            for c in kids:
                c_fill = _is_zstack_layer(c) or c.vid in ("images_holder",)
                if c.cls == "View" and not c.children and _is_zstack_layer(c):
                    c.props.setdefault("bg_scale", "stretch")
                    c.bounds = (x, y, w, mh)
                elif c_fill:
                    c.props["$fill_h"] = True
                    if c.vid in ("overlay", "blur"):
                        c.props.setdefault("bg_scale", "stretch")
                    self._lay(c, x, y, w, forced_h=mh)
                    if c.vid == "images_holder":
                        for ic in c.children:
                            if ic.visible and ic.cls in ("ImageView", "ImageButton"):
                                ic.props.setdefault("scale_type", "CENTER_CROP")
                                ic.bounds = (x, y, w, mh)
                    if blur_active and c.vid in ("carousel_fragment_holder",):
                        for sub in c.walk():
                            if sub is c or sub.vid in ("images_holder",) or sub.cls in ("ImageView",):
                                par_sub = getattr(sub, "_parent", None)
                                if par_sub is not None and par_sub.vid == "indicator_holder":
                                    sub.props["$occluded_by_blur"] = True
                                elif sub.vid != "images_holder" and getattr(par_sub, "vid", "") != "images_holder":
                                    pass
                            if sub.vid in ("pager", "indicator_holder", "text_view", "title_view", "big_title", "big_subtitle", "front_page_subtitle"):
                                sub.props["$occluded_by_blur"] = True
                else:
                    ch = _measure(c, w)
                    cy_over = y + max(0, mh - ch - (72 if c.vid == "indicator_holder" else 16))
                    self._lay(c, x, cy_over, w)
                    if blur_active and c.vid == "indicator_holder":
                        for sub in c.walk():
                            sub.props["$occluded_by_blur"] = True
            return
        if is_full_h and node.cls == "RelativeLayout" and (
            node.props.get("v_gravity") == "bottom" or any(c.props.get("align_parent_bottom") for c in kids)
        ):
            cw = max(20, w - 2 * px)
            content_h = 0
            non_z = 0
            for c in kids:
                ch = _measure(c, cw)
                if ch > 0:
                    content_h += ch + gap
                    non_z += 1
            if non_z > 0:
                content_h -= gap
            pad_b = min(int(node.props.get("xml_pad_b", py) * 0.55) if node.props.get("xml_pad_b") else 96, max(24, mh // 3))
            cy = max(y + py, y + mh - pad_b - content_h)
            for c in kids:
                ch = _measure(c, cw)
                if ch <= 0:
                    c.bounds = (x + px, cy, cw, 0)
                    continue
                self._lay(c, x + px, cy, cw)
                cy += ch + gap
            return
        if is_full_h and "CoordinatorLayout" in str(node.props.get("full_cls") or node.cls):
            cw = max(20, w - 2 * px)
            flow_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
            bot_bars = [
                c for c in flow_kids
                if c.props.get("l_v_gravity") == "bottom"
                or c.vid == "bottom_navigation_holder"
                or c.props.get("role") == "navbar"
            ]
            top_bars = [
                c for c in flow_kids
                if c not in bot_bars
                and ("AppBarLayout" in str(c.props.get("full_cls") or c.cls) or c.props.get("role") == "topbar")
            ]
            main_kids = [c for c in flow_kids if c not in bot_bars and c not in top_bars]
            top_end = y + py
            for c in top_bars:
                ch = _measure(c, cw)
                if ch > 0:
                    self._lay(c, x + px, top_end, cw)
                    top_end += ch + gap
            bot_heights = [_measure(c, cw) for c in bot_bars]
            bot_tot = sum(hi for hi in bot_heights if hi > 0) + gap * max(0, sum(1 for hi in bot_heights if hi > 0) - 1)
            bot_start = max(top_end, y + mh - py - bot_tot)
            cy_b = bot_start
            for c, ch in zip(bot_bars, bot_heights):
                if ch <= 0:
                    c.bounds = (x + px, cy_b, cw, 0)
                    continue
                self._lay(c, x + px, cy_b, cw)
                cy_b += ch + gap
            avail_mid = max(0, bot_start - top_end - (gap if bot_bars and main_kids else 0))
            cy_m = top_end
            for c in main_kids:
                if len(main_kids) == 1 or "ViewPager" in c.cls or c.props.get("xml_h_mode") == "match":
                    c.props["$fill_h"] = True
                    self._lay(c, x + px, cy_m, cw, forced_h=max(_measure(c, cw), avail_mid) if len(main_kids) == 1 else avail_mid)
                    cy_m += avail_mid + gap
                else:
                    ch = _measure(c, cw)
                    self._lay(c, x + px, cy_m, cw)
                    if ch > 0:
                        cy_m += ch + gap
            for c in kids:
                if _is_overlay_scrim_or_click(c, node):
                    c.bounds = (x, y, w, mh)
            return
        cols = int(node.props.get("span_count") or 1)
        if cols >= 2:
            grid_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
            cw = max(20, w - 2 * px)
            cell_w = max(40, int((cw - gap * (cols - 1)) // cols))
            cy_g = y + py
            for i in range(0, len(grid_kids), cols):
                row = grid_kids[i:i + cols]
                row_h = max((_measure(c, cell_w) for c in row), default=0)
                if row_h <= 0:
                    for c in row:
                        c.bounds = (x + px, cy_g, cell_w, 0)
                    continue
                for col_idx, c in enumerate(row):
                    cx_g = x + px + col_idx * (cell_w + gap)
                    self._lay(c, cx_g, cy_g, cell_w, forced_h=row_h)
                cy_g += row_h + gap
            grid_h = max(mh, cy_g - y + py)
            for c in kids:
                if _is_overlay_scrim_or_click(c, node):
                    c.bounds = (x, y, w, grid_h)
            return
        if _is_indicator_row(node):
            dot_w, dot_gap = 18, 6
            total_dw = len(kids) * dot_w + max(0, len(kids) - 1) * dot_gap
            cx = x + max(px, (w - total_dw) // 2)
            for c in kids:
                ch = _measure(c, dot_w)
                cy_off = max(0, (mh - ch) // 2)
                self._lay(c, cx, y + cy_off, dot_w)
                cx += dot_w + dot_gap
        elif _is_horizontal(node):
            inner = max(20, w - 2 * px)
            widths = self._horizontal_widths(node, kids, inner, gap)
            cx = x + px
            cy_row = y
            if is_full_h and any(c.props.get("$cl_bot_parent") for c in kids):
                row_h = max(_measure(c, cw) for c, cw in zip(kids, widths))
                cy_row = y + max(0, mh - row_h - 14)
                node.props["$draw_center_divider_y"] = (cy_row + 6, max(8, row_h - 12))
            for c, cw in zip(kids, widths):
                ch = _measure(c, cw)
                cy_off = (0 if cy_row > y else (max(0, (mh - ch) // 2) if node.props.get("role") in ("topbar", "navbar") or len(kids) >= 2 else py))
                self._lay(c, cx, cy_row + cy_off, cw)
                cx += cw + gap
        else:
            cy = y + py
            cw = max(20, w - 2 * px)
            role = node.props.get("role")
            has_icon_tile = any(
                (c.cls in ("ImageView", "ImageButton") or (
                    c.cls == "Button" and not (c.text or "").strip()
                    and (c.props.get("bg_drawable") or c.props.get("bg_ref"))
                )) and int(c.props.get("xml_w", 0) or 0) >= 24
                for c in kids
            )
            def _is_compact_action(c, cw_avail):
                w_prop = int(c.props.get("w", 0) or 0)
                is_ic = (
                    c.cls in ("ImageView", "ImageButton")
                    or (c.cls == "Button" and 0 < w_prop <= 56)
                    or (c.cls == "Button" and not (c.text or "").strip()
                        and (c.props.get("bg_drawable") or c.props.get("bg_ref")))
                )
                xw = w_prop or c.props.get("xml_w")
                is_wrap = (
                    is_ic and not xw and c.props.get("xml_w_mode") == "wrap"
                    and (
                        c.clickable
                        or "back" in str(c.vid or "").lower()
                        or "close" in str(c.vid or "").lower()
                        or (c.props.get("$cl_start_parent") and not c.props.get("$cl_end_parent"))
                        or (c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent"))
                    )
                )
                if ((is_ic and xw and 24 <= int(xw) < cw_avail - 12
                        and str(c.props.get("scale_type") or "").upper() != "CENTER_CROP")
                        or is_wrap):
                    bw = int(xw) if (w_prop and 0 < w_prop <= 56) else (44 if is_wrap else max(48, min(cw_avail, int(xw))))
                    if "$cl_h_bias" in c.props:
                        bx = x + px + int(round((cw_avail - bw) * float(c.props["$cl_h_bias"])))
                    elif c.props.get("$cl_start_parent") and not c.props.get("$cl_end_parent"):
                        bx = x + px
                    elif c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent"):
                        bx = x + px + cw_avail - bw
                    else:
                        bx = x + px + (cw_avail - bw) // 2
                    return bw, bx
                if "$cl_h_bias" in c.props and (c.props.get("xml_w") or w_prop):
                    bw_raw = int(w_prop or c.props.get("xml_w") or 0)
                    if 16 <= bw_raw < cw_avail - 8:
                        bx = x + px + int(round((cw_avail - bw_raw) * float(c.props["$cl_h_bias"])))
                        return bw_raw, bx
                return None

            def _apply_overlays(overlay_h):
                for c in kids:
                    if _is_concentric_overlay(c, node):
                        tgt = next((s for s in kids if s is not c and s.vid == c.props.get("$cl_same_v_target")), None)
                        if tgt is not None:
                            tb = tgt.bounds
                            self._lay(c, tb[0], tb[1], tb[2], forced_h=tb[3])
                    elif _is_overlay_scrim_or_click(c, node):
                        c.bounds = (x, y, w, overlay_h)

            if is_full_h and node.cls == "ConstraintLayout" and len(kids) >= 2:
                bot_ids = {
                    c.vid for c in node.children
                    if c.vid and (
                        (c.props.get("$cl_bot_parent") and not c.props.get("$cl_top_parent") and not c.props.get("$cl_top_to_bot"))
                        or (c.props.get("non_visual") and "bot" in str(c.vid).lower())
                    )
                }
                bot_set = set()
                for c in kids:
                    if c.props.get("$cl_bot_parent") and not c.props.get("$cl_top_parent") and not c.props.get("$cl_top_to_bot"):
                        bot_set.add(id(c))
                        if c.vid:
                            bot_ids.add(c.vid)
                changed_b = True
                while changed_b:
                    changed_b = False
                    for c in kids:
                        if id(c) in bot_set:
                            continue
                        btt = c.props.get("$cl_bot_to_top")
                        ttb = c.props.get("$cl_top_to_bot")
                        is_scroll_main = ("ViewPager" in c.cls or c.cls in ("RecyclerView", "ScrollView", "FrameLayout"))
                        if (btt in bot_ids and not c.props.get("$cl_top_parent") and not ttb and not is_scroll_main) or (ttb in bot_ids and not btt):
                            bot_set.add(id(c))
                            if c.vid:
                                bot_ids.add(c.vid)
                            changed_b = True
                top_k = [c for c in kids if id(c) not in bot_set and c.props.get("$cl_top_parent") and not c.props.get("$cl_bot_parent") and not c.props.get("$cl_bot_to_top") and "ViewPager" not in c.cls and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                bot_k = [c for c in kids if id(c) in bot_set and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                mid_k = [c for c in kids if c not in top_k and c not in bot_k and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                if top_k or bot_k:
                    def _place_single(c, cy_pos, cw_avail, max_h_hint=None):
                        act = _is_compact_action(c, cw_avail)
                        if act is not None:
                            bw, bx = act
                            ch_i = _measure(c, bw)
                            self._lay(c, bx, cy_pos, bw)
                            return ch_i
                        if (has_icon_tile or node.props.get("gravity") == 1) and c.cls == "TextView":
                            c.props.setdefault("gravity", 1)
                        if node.props.get("gravity") == 1 and not c.is_leaf():
                            c.props.setdefault("gravity", 1)
                        ch_i = _measure(c, cw_avail)
                        self._lay(c, x + px, cy_pos, cw_avail, forced_h=max_h_hint)
                        return int(max_h_hint if max_h_hint is not None else ch_i)

                    top_end = y + py
                    if (len(top_k) == 2 and top_k[0].vid == "back_button"
                            and top_k[1].cls in ("ProgressBar", "SeekBar")):
                        bw_bk = min(44, max(36, int(top_k[0].props.get("xml_w") or 40)))
                        self._lay(top_k[0], x + px, top_end, bw_bk, forced_h=36)
                        top_k[0].bounds = (x + px, top_end, bw_bk, 36)
                        px_pb = x + px + bw_bk + 8
                        pw_pb = max(60, cw - bw_bk - 8)
                        self._lay(top_k[1], px_pb, top_end + 8, pw_pb, forced_h=20)
                        top_end += 36 + gap
                    else:
                        for c in top_k:
                            ch = _place_single(c, top_end, cw)
                            if ch > 0:
                                top_end += ch + gap
                    bot_heights = [_measure(c, cw) for c in bot_k]
                    bot_tot = sum(h_i for h_i in bot_heights if h_i > 0) + gap * max(0, sum(1 for h_i in bot_heights if h_i > 0) - 1)
                    bot_start = max(top_end, y + mh - py - bot_tot)
                    cy_b = bot_start
                    for c in bot_k:
                        ch = _place_single(c, cy_b, cw)
                        if ch > 0:
                            cy_b += ch + gap
                    avail_mid = max(0, bot_start - top_end - (gap if bot_k and top_end > y + py else 0))
                    mid_heights = [_measure(c, cw) for c in mid_k]
                    mid_tot = sum(h_i for h_i in mid_heights if h_i > 0) + gap * max(0, sum(1 for h_i in mid_heights if h_i > 0) - 1)
                    should_center = (
                        len(mid_k) >= 1
                        and mid_tot > 0
                        and mid_tot < avail_mid - 20
                        and all("ViewPager" not in c.cls and c.cls not in ("RecyclerView", "ScrollView") for c in mid_k)
                    )
                    vb_vals = [float(c.props["$cl_v_bias"]) for c in mid_k if "$cl_v_bias" in c.props]
                    if vb_vals and avail_mid > mid_tot:
                        cy_m = top_end + int(round(max(0, avail_mid - mid_tot) * vb_vals[0]))
                    else:
                        cy_m = top_end + max(0, (avail_mid - mid_tot) // 2) if should_center else top_end
                    for c in mid_k:
                        if "ViewPager" in c.cls and len(mid_k) == 1 and avail_mid > mid_heights[0]:
                            c.props["$fill_h"] = True
                            ch = _place_single(c, cy_m, cw, max_h_hint=avail_mid)
                        else:
                            ch = _place_single(c, cy_m, cw)
                        if ch > 0:
                            cy_m += ch + gap
                    _apply_overlays(mh)
                    return

            def _place_v_child(c, cy_pos, cw_avail, forced_ch=None):
                act = _is_compact_action(c, cw_avail)
                if act is not None:
                    bw, bx = act
                    ch_i = _measure(c, bw)
                    self._lay(c, bx, cy_pos, bw, forced_h=forced_ch)
                    return int(forced_ch if forced_ch is not None else ch_i)
                if (has_icon_tile or node.props.get("gravity") == 1) and c.cls == "TextView":
                    c.props.setdefault("gravity", 1)
                if node.props.get("gravity") == 1 and not c.is_leaf():
                    c.props.setdefault("gravity", 1)
                ch_i = _measure(c, cw_avail)
                self._lay(c, x + px, cy_pos, cw_avail, forced_h=forced_ch)
                return int(forced_ch if forced_ch is not None else ch_i)

            flow_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
            if (is_full_h and len(flow_kids) == 1 and not flow_kids[0].is_leaf() and (
                node.cls in ("ConstraintLayout", "FrameLayout", "CardView")
                or node.vid == 0xC000
                or node.props.get("$compose_root")
                or node.props.get("$reusable_node")
                or node.props.get("role") in ("scaffold", "surface")
                or flow_kids[0].props.get("$fill_max_h")
            )):
                fc = flow_kids[0]
                fc.props["$fill_h"] = True
                self._lay(fc, x + px, y + py, cw, forced_h=max(0, mh - 2 * py))
                _apply_overlays(mh)
                return

            if (is_full_h and node.props.get("compose_kind") != "Box"
                    and len(flow_kids) >= 2 and any(float(c.props.get("layout_weight") or 0) > 0 for c in flow_kids)):
                wt_map = {}
                total_wt = 0.0
                fixed_h = 0
                meas_map = {}
                for c in flow_kids:
                    wt = float(c.props.get("layout_weight") or 0.0)
                    ch_i = _measure(c, cw)
                    meas_map[id(c)] = ch_i
                    if wt > 0:
                        wt_map[id(c)] = wt
                        total_wt += wt
                    else:
                        fixed_h += ch_i
                avail_wt = max(0, (mh - 2 * py) - fixed_h - gap * max(0, len(flow_kids) - 1))
                cy_w = y + py
                for c in flow_kids:
                    if id(c) in wt_map and total_wt > 0:
                        c.props["$fill_h"] = True
                        fh = max(meas_map[id(c)], int(avail_wt * (wt_map[id(c)] / total_wt)))
                        ch_i = _place_v_child(c, cy_w, cw, forced_ch=fh)
                    else:
                        ch_i = _place_v_child(c, cy_w, cw)
                    if ch_i > 0:
                        cy_w += ch_i + gap
                _apply_overlays(max(mh, cy_w - y))
                return

            if (is_full_h and node.props.get("compose_kind") == "Column" and len(flow_kids) >= 2
                    and not any(sub.props.get("role") == "lazy_list" for c in flow_kids for sub in c.walk())):
                tail_start = len(flow_kids)
                while tail_start > 0:
                    cand = flow_kids[tail_start - 1]
                    has_btn_or_click = any(s.cls == "Button" or (s.cls == "TextView" and s.clickable) for s in cand.walk())
                    has_card = any(s.props.get("role") == "card" for s in cand.walk())
                    if has_btn_or_click and not has_card:
                        tail_start -= 1
                    else:
                        break
                if tail_start < len(flow_kids):
                    ch_list = [_measure(c, cw) for c in flow_kids]
                    nat_tot = sum(hi for hi in ch_list if hi > 0) + gap * max(0, sum(1 for hi in ch_list if hi > 0) - 1)
                    if nat_tot < (mh - 2 * py) - 80:
                        top_k_c = flow_kids[:1] if tail_start >= 1 else []
                        mid_k_c = flow_kids[1:tail_start] if tail_start > 1 else []
                        tail_k_c = flow_kids[tail_start:]
                        top_end = y + py
                        for c in top_k_c:
                            ch_i = _place_v_child(c, top_end, cw)
                            if ch_i > 0:
                                top_end += ch_i + gap
                        tail_hs = [_measure(c, cw) for c in tail_k_c]
                        tail_tot = sum(hi for hi in tail_hs if hi > 0) + gap * max(0, sum(1 for hi in tail_hs if hi > 0) - 1)
                        bot_start = max(top_end, y + mh - py - tail_tot - 12)
                        cy_t = bot_start
                        for c in tail_k_c:
                            ch_i = _place_v_child(c, cy_t, cw)
                            if ch_i > 0:
                                cy_t += ch_i + gap
                        avail_mid = max(0, bot_start - top_end - gap)
                        mid_hs = [_measure(c, cw) for c in mid_k_c]
                        mid_tot = sum(hi for hi in mid_hs if hi > 0) + gap * max(0, sum(1 for hi in mid_hs if hi > 0) - 1)
                        cy_m = top_end + max(0, (avail_mid - mid_tot) // 2)
                        for c in mid_k_c:
                            ch_i = _place_v_child(c, cy_m, cw)
                            if ch_i > 0:
                                cy_m += ch_i + gap
                        _apply_overlays(mh)
                        return

            if node.cls == "ConstraintLayout" and len(flow_kids) >= 2:
                cl_rows, cl_guides = _cl_group_rows(node, flow_kids, cw)
                if any(len(r) >= 2 for r in cl_rows):
                    for r in cl_rows:
                        if len(r) >= 2:
                            slots = _cl_row_slots(r, cw, x + px, gap, cl_guides)
                            row_h = max((_measure(p, pw) for p, _, pw in slots), default=0)
                            for p, px_s, pw_s in slots:
                                self._lay(p, px_s, cy, pw_s)
                            if row_h > 0:
                                cy += row_h + gap
                        else:
                            ch = _place_v_child(r[0], cy, cw)
                            if ch > 0:
                                cy += ch + gap
                    _apply_overlays(max(mh, cy - y))
                    return

            for idx, c in enumerate(flow_kids):
                ch = _measure(c, cw)
                if ch <= 0:
                    c.bounds = (x + px, cy, cw, 0)
                    continue
                # Keep bottom NavigationBar pinned to bottom when content fits viewport
                if (role == "scaffold" and idx == len(flow_kids) - 1
                        and c.props.get("role") == "navbar" and cy < y + self.height - ch):
                    cy = y + self.height - ch
                ch = _place_v_child(c, cy, cw)
                cy += ch + gap
            total_h = max(mh, cy - y)
            _apply_overlays(total_h)
            if role == "scaffold" and cy > node.bounds[1] + mh:
                node.bounds = (x, y, w, cy - y + py)

    def nodes(self):
        return list(self.root.walk()) if self.root else []

    def visible_nodes(self):
        return list(self.root.walk_visible()) if self.root else []

    def content_height(self):
        """Gesamthoehe des gelayouteten Inhalts (fuer vertikalen Scroll)."""
        return self.root.bounds[3] if self.root is not None else 0

    def node_at(self, x, y):
        """Tiefster sichtbarer Knoten, dessen Bounds (x,y) enthalten."""
        hit = None
        for n in self.visible_nodes():
            if not n.visible:
                continue
            bx, by, bw, bh = n.bounds
            if bx <= x < bx + bw and by <= y < by + bh:
                hit = n
        return hit

    def clickable_at(self, x, y):
        """Tiefster sichtbarer, klickbarer Knoten bei (x,y) (fuer Input-Injektion)."""
        best = None
        for n in self.visible_nodes():
            if not (n.visible and n.clickable):
                continue
            bx, by, bw, bh = n.bounds
            if bx <= x < bx + bw and by <= y < by + bh:
                best = n
        if best is None:
            hit = self.node_at(x, y)
            cur = getattr(hit, "_parent", None) if hit is not None else None
            hops = 0
            while cur is not None and hops < 4:
                overlay = [
                    s for s in getattr(cur, "children", ()) or ()
                    if s.visible and s.clickable and (s.vid == "click_overlay" or (s.cls == "View" and not s.children))
                ]
                if len(overlay) == 1:
                    return overlay[0]
                if len(cur.children) <= 3:
                    c_sibs = [s for s in cur.children if s.visible and s.clickable]
                    if len(c_sibs) == 1:
                        return c_sibs[0]
                cur = getattr(cur, "_parent", None)
                hops += 1
        return best


def paint_commands(tree):
    """Reine Zeichenbefehl-Liste fuer das Anzeige-Fenster (headless testbar).
    Befehle: {op:'rect',x,y,w,h,fill,outline,r} und {op:'text',x,y,text,color,anchor}.
    Container vor Kindern (Pre-Order) -> Kinder liegen oben."""
    cmds = []
    if tree is None or tree.root is None:
        return cmds
    dark = tree.effective_dark() if hasattr(tree, "effective_dark") else bool(getattr(tree, "dark_mode", False))
    win_dark = dark or bool(getattr(tree, "window_bg_dark", False))
    txt_primary = "#e5e7eb" if dark else "#1b2230"
    txt_muted = "#9ca3af" if dark else "#6b7280"
    inp_bg = "#1e222b" if dark else "#ffffff"
    inp_out = "#3c4352" if dark else "#9aa5b5"

    def _anc_pos(n, x, y, w, h):
        try:
            hg = (int(n.props.get("gravity") or 0)) & 0x07
        except Exception:
            hg = 0
        if hg == 5:
            return "e", x + w - 4
        if hg == 1:
            return "c", x + w / 2
        return "w", x + 4

    laid_out = bool(tree.root.bounds[2] > 0)

    def _hex_luma(c, require_opaque=False):
        if not isinstance(c, str) or not c.startswith("#"):
            return None
        try:
            hx = c[1:]
            if len(hx) == 8:
                if require_opaque and int(hx[0:2], 16) < 96:
                    return None
                hx = hx[2:]
            if len(hx) != 6:
                return None
            r = int(hx[0:2], 16); g = int(hx[2:4], 16); b = int(hx[4:6], 16)
            return 0.299 * r + 0.587 * g + 0.114 * b
        except Exception:
            return None

    def _has_dark_backdrop(node):
        nb = getattr(node, "bounds", (0, 0, 0, 0))
        child_on_path = None
        cur = node
        while cur is not None:
            bs = cur.props.get("bg_drawable")
            bg_alpha = float(cur.props.get("bg_alpha", 1.0) if cur.props.get("bg_alpha") is not None else 1.0)
            if isinstance(bs, dict) and bg_alpha >= 0.4:
                spec_alpha = float(bs.get("fill_alpha", bs.get("alpha", 1.0)) if bs.get("alpha", 1.0) is not None else 1.0)
                if spec_alpha >= 0.4:
                    if bs.get("kind") == "color" and bs.get("color"):
                        lum = _hex_luma(bs["color"], require_opaque=True)
                        if lum is not None and lum < 150.0:
                            return True
                    if bs.get("kind") == "shape" and bs.get("fill"):
                        lum = _hex_luma(bs["fill"], require_opaque=True)
                        if lum is not None and lum < 150.0:
                            return True
            if "dark_bg" in cur.props and cur.props.get("dark_bg"):
                return True
            for sib in getattr(cur, "children", ()) or ():
                if sib is child_on_path:
                    break
                if sib is node or not getattr(sib, "visible", True) or _is_collapsed_node(sib):
                    continue
                for sub in sib.walk():
                    if not getattr(sub, "visible", True) or _is_collapsed_node(sub):
                        continue
                    sb = getattr(sub, "bounds", (0, 0, 0, 0))
                    if not (sb[2] > 0 and sb[3] > 0 and sb[1] < nb[1] + nb[3] and sb[1] + sb[3] > nb[1]):
                        continue
                    if sub.cls in ("ImageView", "ImageButton"):
                        sp = sub.props.get("drawable")
                        if isinstance(sp, dict) and sp.get("kind") == "raster":
                            return True
                    sbs = sub.props.get("bg_drawable")
                    sa = float(sub.props.get("bg_alpha", 1.0) if sub.props.get("bg_alpha") is not None else 1.0)
                    if isinstance(sbs, dict) and sa >= 0.3:
                        if sbs.get("kind") == "color" and sbs.get("color"):
                            lum = _hex_luma(sbs["color"], require_opaque=False)
                            sa2 = float(sbs.get("alpha", 1.0) if sbs.get("alpha", 1.0) is not None else 1.0)
                            if lum is not None and lum < 140.0 and sa * sa2 >= 0.3:
                                return True
                        elif sbs.get("kind") == "raster":
                            return True
            child_on_path = cur
            cur = getattr(cur, "_parent", None)
        return False

    def _is_light_hex(c):
        lum = _hex_luma(c)
        return bool(lum is not None and lum > 210.0)

    for n in tree.visible_nodes():
        if not n.visible or _is_collapsed_node(n) or n.props.get("$occluded_by_blur"):
            continue
        x, y, w, h = n.bounds
        if laid_out and (w <= 0 or h <= 0):
            continue
        cls = n.cls
        if cls == "EditText" and n.children:
            _sync_composite_edittext(n)
            continue
        role = n.props.get("role")
        par = getattr(n, "_parent", None)
        prole = par.props.get("role") if par is not None else None
        if cls == "Button":
            lbl = n.text or cls
            bg_spec = n.props.get("bg_drawable")
            if role == "navitem":
                sel = bool(n.props.get("selected"))
                bg = "#181b20" if dark else "#ebeef5"
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": bg, "outline": bg, "r": 0})
                if sel:
                    pw = min(w - 16, 84)
                    px0 = x + (w - pw) / 2
                    cmds.append({"op": "rect", "x": px0, "y": y + 4, "w": pw, "h": 22,
                                 "fill": "#364156" if dark else "#d8e3ff",
                                 "outline": "#506282" if dark else "#9bb5f5", "r": 11})
                cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                             "text": lbl,
                             "color": ("#d8e2ff" if sel else "#9ca3af") if dark else ("#1b4fb8" if sel else "#4b5563"),
                             "anchor": "c", "font_size": 9, "bold": sel})
            elif prole == "topbar":
                cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": w - 4, "h": h - 4,
                             "fill": "#222731" if dark else "#eef1f6",
                             "outline": "#2e3543" if dark else "#d0d6e2", "r": 6})
                if bg_spec and bg_spec.get("kind") in ("vector", "raster") and not (n.text or "").strip():
                    from core.java.drawable_loader import drawable_to_paint_commands
                    isz = max(12, min(w, h) - 10)
                    cmds.extend(drawable_to_paint_commands(
                        bg_spec, x + (w - isz) / 2, y + (h - isz) / 2, isz, isz,
                        is_background=False, dark_bg=dark,
                    ))
                else:
                    cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                                 "text": lbl, "color": txt_primary, "anchor": "c", "font_size": 9})
            elif dark and lbl in ("<", ">"):
                cmds.append({"op": "rect", "x": x, "y": y + 2, "w": w, "h": h - 4,
                             "fill": "#a8c7fa", "outline": "#8ab4f8", "r": 16})
                cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                             "text": lbl, "color": "#062e6f", "anchor": "c", "font_size": 11, "bold": True})
            elif bg_spec and bg_spec.get("kind") in ("vector", "raster"):
                from core.java.drawable_loader import drawable_to_paint_commands
                style_s = str(n.props.get("style") or "")
                has_txt = bool((n.text or "").strip())
                if "Borderless" in style_s and not has_txt:
                    cmds.extend(drawable_to_paint_commands(
                        bg_spec, x + 6, y + 6, max(8, w - 12), max(8, h - 12),
                        is_background=False, dark_bg=dark,
                    ))
                else:
                    tile_fill = "#3d6ba8" if (not dark and win_dark) else "#2f6df0"
                    tile_out = "#7aa7dc" if (not dark and win_dark) else "#1b4fb8"
                    cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                                 "fill": tile_fill, "outline": tile_out, "r": 6 if has_txt else 12})
                    if has_txt:
                        ih = min(max(16, h - 8), 28)
                        ix = x + 10
                        iy = y + (h - ih) / 2
                        cmds.extend(drawable_to_paint_commands(bg_spec, ix, iy, ih, ih, is_background=False, dark_bg=True))
                        tx = x + max(w / 2, ih + 18) if w > ih * 3 else (ix + ih + 8)
                        anc = "c" if w > ih * 3 else "w"
                        cmds.append({"op": "text", "x": tx, "y": y + h / 2,
                                     "text": n.text.strip(), "color": "#ffffff", "anchor": anc})
                    else:
                        cmds.extend(drawable_to_paint_commands(bg_spec, x + 6, y + 6, max(8, w - 12), max(8, h - 12), is_background=False, dark_bg=True))
            else:
                style_s = str(n.props.get("style") or "")
                bg_ref_s = str(n.props.get("bg_ref") or "")
                dark_bd = _has_dark_backdrop(n)
                is_borderless = ("Borderless" in style_s) or bg_ref_s.startswith(("?selectableItemBackground", "@color/transparent", "@android:color/transparent"))
                if is_borderless and not bg_spec and not n.props.get("bg_tint"):
                    b_col = n.props.get("color") or ("#ffffff" if dark_bd else "#1b4fb8")
                    if not dark_bd and _is_light_hex(b_col):
                        b_col = "#1b4fb8"
                    b_anc = "w" if n.props.get("$cl_start_parent") else "c"
                    b_tx = (x + 6) if b_anc == "w" else (x + w / 2)
                    cmds.append({"op": "text", "x": b_tx, "y": y + h / 2,
                                 "text": lbl, "color": b_col, "anchor": b_anc})
                else:
                    b_fill, b_out, b_rad = "#2f6df0", "#1b4fb8", (min(18, h // 2) if "Rounded" in style_s else 6)
                    b_txt_col = "#ffffff"
                    bg_tint = n.props.get("bg_tint")
                    if bg_tint:
                        b_fill = bg_tint
                        b_out = bg_tint
                    elif bg_spec and bg_spec.get("kind") in ("shape", "color") and bg_spec.get("fill"):
                        b_fill = bg_spec["fill"]
                        b_out = bg_spec.get("outline") or b_fill
                        b_rad = bg_spec.get("radius", b_rad)
                    elif bg_spec and bg_spec.get("kind") == "color" and bg_spec.get("color"):
                        b_fill = bg_spec["color"]
                        b_out = b_fill
                    from core.java.drawable_loader import _is_very_light
                    if _is_very_light(b_fill):
                        b_txt_col = n.props.get("color") or "#1b2230"
                        if b_out == b_fill:
                            b_out = "#c3c9d6"
                    elif n.props.get("color") and not _is_light_hex(b_fill):
                        b_txt_col = n.props["color"] if not (_is_very_light(n.props["color"]) and _is_very_light(b_fill)) else "#1b2230"
                    cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                                 "fill": b_fill, "outline": b_out, "r": b_rad})
                    cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                                 "text": lbl, "color": b_txt_col, "anchor": "c"})
        elif cls == "EditText":
            dark_et = False
            ed_bg = n.props.get("bg_drawable")
            if isinstance(ed_bg, dict) and ed_bg.get("kind") == "color" and ed_bg.get("color"):
                el = _hex_luma(ed_bg["color"], require_opaque=False)
                if el is not None and el < 100.0:
                    dark_et = True
            if not dark_et and _has_dark_backdrop(n) and str(n.props.get("bg_ref") or "").startswith("#"):
                dark_et = True
            et_fill = "#263040" if dark_et else inp_bg
            et_out = "#64748b" if dark_et else inp_out
            et_txt_col = "#ffffff" if (dark_et and n.text) else ("#cbd5e1" if dark_et else (txt_primary if n.text else txt_muted))
            cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                         "fill": et_fill, "outline": et_out, "r": 6 if dark_et else 4})
            if n.text and bool(getattr(tree, "mask_passwords", False)) and (
                bool(n.props.get("is_password"))
                or "password" in str(n.props.get("hint") or "").lower()
            ):
                shown = "\u2022" * len(n.text)
            else:
                shown = n.text or n.props.get("hint", "")
            cmds.append({"op": "text", "x": x + 8, "y": y + h / 2,
                         "text": shown, "color": et_txt_col, "anchor": "w"})
        elif cls in ("ImageView", "ImageButton"):
            drawn = False
            in_card_iv = False
            _pv = par
            while _pv is not None:
                if _pv.props.get("role") in ("card", "dialog", "topbar", "navbar"):
                    in_card_iv = True
                    break
                _pv = getattr(_pv, "_parent", None)
            iv_dark = True if cls == "ImageButton" else (dark if in_card_iv else win_dark)
            iv_alpha = float(n.props.get("img_alpha", 1.0) if n.props.get("img_alpha") is not None else 1.0)
            iv_scale = "crop" if str(n.props.get("scale_type") or "").upper() == "CENTER_CROP" else None
            img_bytes = n.props.get("img_bytes")
            spec = n.props.get("drawable")
            if spec is None and n.props.get("img"):
                resv = getattr(tree, "_drawable_resolver", None)
                if resv is not None:
                    try:
                        spec = resv.resolve(n.props.get("img"))
                    except Exception:
                        spec = None
            if img_bytes:
                icmd = {"op": "image", "x": x + 2, "y": y + 2, "w": max(4, w - 4), "h": max(4, h - 4),
                        "data": bytes(img_bytes), "name": n.props.get("img") or "bitmap", "scale": iv_scale or "fit",
                        "dark_bg": iv_dark}
                if iv_alpha < 0.999:
                    icmd["alpha"] = iv_alpha
                cmds.append(icmd)
                drawn = True
            elif spec is not None:
                from core.java.drawable_loader import drawable_to_paint_commands
                ib_bg_ref = str(n.props.get("bg_ref") or "")
                ib_borderless = cls == "ImageButton" and (
                    ib_bg_ref.startswith(("?selectableItemBackground", "@color/transparent", "@android:color/transparent"))
                    or (not ib_bg_ref and _has_dark_backdrop(n))
                )
                iv_tint = n.props.get("tint")
                if cls == "ImageButton" and spec.get("kind") in ("vector", "raster") and not ib_borderless:
                    cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": max(4, w - 4), "h": max(4, h - 4),
                                 "fill": "#2f6df0", "outline": "#1b4fb8", "r": 6})
                    dcmds = drawable_to_paint_commands(spec, x + 6, y + 6, max(4, w - 12), max(4, h - 12),
                                                       is_background=False, dark_bg=True, alpha=iv_alpha, tint=iv_tint)
                else:
                    use_dark = True if ib_borderless else iv_dark
                    dcmds = drawable_to_paint_commands(spec, x + 2, y + 2, max(4, w - 4), max(4, h - 4),
                                                       is_background=False, dark_bg=use_dark, alpha=iv_alpha, scale=iv_scale, tint=iv_tint)
                if dcmds:
                    cmds.extend(dcmds)
                    drawn = True
            if not drawn:
                cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": w - 4, "h": h - 4,
                             "fill": "#252932" if dark else "#eef1f6",
                             "outline": "#383f4d" if dark else "#c3c9d6", "r": 4})
                lbl = n.props.get("img") or n.props.get("desc") or n.text or "IMG"
                cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                             "text": lbl, "color": txt_muted, "anchor": "c"})
        elif cls == "TextView":
            bg_spec = n.props.get("bg_drawable")
            in_card = False
            in_sticky = False
            p = par
            while p is not None:
                if p.props.get("role") == "card":
                    in_card = True
                    break
                if p.props.get("role") == "sticky_header":
                    in_sticky = True
                    break
                p = getattr(p, "_parent", None)
            tv_dark = dark if (in_card or prole in ("topbar", "navbar")) else win_dark
            if bg_spec is not None:
                from core.java.drawable_loader import drawable_to_paint_commands
                cmds.extend(drawable_to_paint_commands(bg_spec, x, y, w, h, is_background=True, dark_bg=tv_dark))
            anc, tx = _anc_pos(n, x, y, w, h)
            cd_spec = n.props.get("compound_drawable")
            cd_off = 0
            if cd_spec is not None and w > 36:
                from core.java.drawable_loader import drawable_to_paint_commands
                isz = min(18, max(10, h - 8))
                icmds = drawable_to_paint_commands(cd_spec, x + 4, y + (h - isz) / 2, isz, isz, is_background=False, dark_bg=tv_dark)
                if icmds:
                    cmds.extend(icmds)
                    if anc == "w":
                        cd_off = isz + 6
                        tx += cd_off
            t = n.text or ""
            fg_col = n.props.get("color")
            if not fg_col and isinstance(n.props.get("fg"), int):
                _ci = int(n.props["fg"]) & 0xFFFFFFFF
                if ((_ci >> 24) & 0xFF) > 10 or (_ci != 0 and _ci <= 0xFFFFFF):
                    fg_col = f"#{_ci & 0xFFFFFF:06x}"
            tcol = fg_col or ("#ffffff" if (win_dark and not dark and not in_card and not in_sticky and prole != "topbar") else txt_primary)
            fsize = None
            bold = False
            has_bg_spec = bool(isinstance(bg_spec, dict) and float(bg_spec.get("alpha", 1.0) or 1.0) > 0.05)
            if not tv_dark and _is_light_hex(tcol) and not _has_dark_backdrop(n):
                bg_is_dark = False
                if isinstance(bg_spec, dict):
                    fc = bg_spec.get("fill") or bg_spec.get("color")
                    bg_is_dark = bool(fc and isinstance(fc, str) and not _is_light_hex(fc))
                if not bg_is_dark:
                    if n.clickable and not has_bg_spec:
                        cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": max(4, w - 4), "h": max(4, h - 4),
                                     "fill": "#eef3ff", "outline": "#9bb5f5", "r": 6})
                        tcol = "#1b4fb8"
                        bold = True
                    else:
                        tcol = txt_primary
            elif not tv_dark and n.clickable and _is_light_hex(tcol) and not has_bg_spec and not _has_dark_backdrop(n):
                cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": max(4, w - 4), "h": max(4, h - 4),
                             "fill": "#eef3ff", "outline": "#9bb5f5", "r": 6})
                tcol = "#1b4fb8"
                bold = True
            if prole == "topbar":
                fsize, bold = 12, True
            elif n.vid == "summary":
                tcol = "#d8e2f0" if tv_dark else txt_muted
                fsize = 8
                anc, tx = "w", x + 4
            elif n.vid == "title" and par is not None and par.find("summary") is not None:
                fsize, bold = 10, False
                anc, tx = "w", x + 4
            elif n.vid == "title" and par is not None and par.cls == "LinearLayout" and par.find("widget_frame") is None:
                tcol = "#dbeafe" if tv_dark else "#1d4ed8"
                fsize, bold = 10, True
                anc, tx = "w", x + 4
            elif in_sticky:
                tcol = "#cbd5e1" if dark else "#374151"
                fsize, bold = 9, True
            elif in_card:
                if t.startswith("\u2705"):
                    tcol = "#4ade80" if dark else "#15803d"
                    fsize = 9
                elif "|" in t or "/" in t or t.startswith(("\u23f3", "\u274c", "\u2139")):
                    tcol = "#d1d5db" if dark else "#4b5563"
                    fsize = 9
                else:
                    fsize, bold = 11, True
            max_w = max(24, int(w - (cd_off + 8)))
            justify = "center" if anc == "c" else ("right" if anc == "e" else "left")
            cmd = {"op": "text", "x": tx, "y": y + h / 2,
                   "text": t, "color": tcol, "anchor": anc,
                   "max_w": max_w, "justify": justify}
            if fsize is not None:
                cmd["font_size"] = fsize
            if bold:
                cmd["bold"] = True
            cmds.append(cmd)
        elif cls in ("CheckBox", "Switch", "ToggleButton", "CompoundButton"):
            checked = bool(n.props.get("checked"))
            label_txt = (n.text or "").strip()
            if cls == "Switch":
                tw, th = 42, 18
                if label_txt:
                    tx, ty = x + w - tw - 4, y + (h - th) / 2
                else:
                    tx, ty = x + max(2, (w - tw) / 2), y + (h - th) / 2
                cmds.append({"op": "rect", "x": tx, "y": ty, "w": tw, "h": th,
                             "fill": "#2f6df0" if checked else "#c3c9d6",
                             "outline": "#9aa5b5", "r": 9})
                kx = tx + (tw - th) if checked else tx
                cmds.append({"op": "rect", "x": kx, "y": ty, "w": th, "h": th,
                             "fill": "#ffffff", "outline": "#9aa5b5", "r": 9})
                if label_txt:
                    cmds.append({"op": "text", "x": x + 4, "y": y + h / 2,
                                 "text": label_txt, "color": txt_primary, "anchor": "w"})
            else:
                box = min(h - 8, 22)
                bx2 = (x + 2) if label_txt else (x + max(2, (w - box) / 2))
                by2 = y + (h - box) / 2
                cmds.append({"op": "rect", "x": bx2, "y": by2, "w": box, "h": box,
                             "fill": inp_bg, "outline": inp_out, "r": 3})
                if checked:
                    cmds.append({"op": "text", "x": bx2 + box / 2, "y": by2 + box / 2,
                                 "text": "\u2713", "color": "#2f6df0", "anchor": "c"})
                if label_txt:
                    cmds.append({"op": "text", "x": bx2 + box + 8, "y": y + h / 2,
                                 "text": label_txt, "color": txt_primary, "anchor": "w"})
        elif cls == "RadioButton":
            checked = bool(n.props.get("checked"))
            label_txt = (n.text or "").strip()
            d = min(h - 8, 20)
            ox = (x + 2) if label_txt else (x + max(2, (w - d) / 2))
            oy = y + (h - d) / 2
            cmds.append({"op": "oval", "x": ox, "y": oy, "w": d, "h": d,
                         "fill": inp_bg, "outline": inp_out})
            if checked:
                pad = d / 4
                cmds.append({"op": "oval", "x": ox + pad, "y": oy + pad,
                             "w": d - 2 * pad, "h": d - 2 * pad,
                             "fill": "#2f6df0", "outline": "#2f6df0"})
            if label_txt:
                cmds.append({"op": "text", "x": ox + d + 8, "y": y + h / 2,
                             "text": label_txt, "color": txt_primary, "anchor": "w"})
        elif cls in ("ProgressBar", "SeekBar"):
            try:
                prog = max(0, min(100, int(n.props.get("progress", 0) or 0)))
            except Exception:
                prog = 0
            th = min(h - 8, 10)
            ty = y + (h - th) / 2
            cmds.append({"op": "rect", "x": x + 2, "y": ty, "w": w - 4, "h": th,
                         "fill": "#2a2f3a" if dark else "#e3e6ee", "outline": inp_out, "r": 5})
            fw = int((w - 4) * prog / 100)
            if fw > 0:
                cmds.append({"op": "rect", "x": x + 2, "y": ty, "w": fw, "h": th,
                             "fill": "#2f6df0", "outline": "#2f6df0", "r": 5})
            if cls == "SeekBar":
                kx = x + 2 + fw
                kr = 7
                cmds.append({"op": "oval", "x": kx - kr, "y": y + h / 2 - kr,
                             "w": 2 * kr, "h": 2 * kr, "fill": "#ffffff", "outline": "#2f6df0"})
        elif cls == "DatePicker":
            cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                         "fill": inp_bg, "outline": inp_out, "r": 6})
            cmds.append({"op": "text", "x": x + 10, "y": y + h / 2,
                         "text": (n.text or "📅 01.01.1990"), "color": txt_primary, "anchor": "w"})
        else:
            bg_spec = n.props.get("bg_drawable")
            bg_drawn = False
            if role == "topbar":
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#181b20" if dark else "#ffffff",
                             "outline": "#2a2f3a" if dark else "#d3d8e2", "r": 0})
            elif role == "navbar":
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#181b20" if dark else "#ebeef5",
                             "outline": "#2a2f3a" if dark else "#d3d8e2", "r": 0})
            elif role == "sticky_header":
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#1c2028" if dark else "#e5e8f0",
                             "outline": "#252a34" if dark else "#d3d8e2", "r": 2})
            elif role == "card":
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#232730" if dark else "#ffffff",
                             "outline": "#353b48" if dark else "#c9d0dd", "r": 8})
            elif role == "dialog":
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#262b36" if dark else "#ffffff",
                             "outline": "#60a5fa", "r": 10})
            elif bg_spec is None and (role in ("scaffold", "surface") or n.vid == 0xC000 or n.props.get("$compose_root")):
                cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                             "fill": "#121418" if dark else "#ffffff",
                             "outline": "", "r": 0})
            if bg_spec is not None:
                from core.java.drawable_loader import drawable_to_paint_commands
                bg_alpha = float(n.props.get("bg_alpha", 1.0) if n.props.get("bg_alpha") is not None else 1.0)
                bg_vp = bool(n.props.get("bg_viewport"))
                bg_y = max(0, y) if bg_vp else y
                bg_h = min(h, getattr(tree, "height", h)) if bg_vp else h
                bg_cmds = drawable_to_paint_commands(
                    bg_spec, x, bg_y, w, bg_h,
                    is_background=True,
                    dark_bg=(dark if role in ("card", "dialog", "topbar", "navbar") else win_dark),
                    alpha=bg_alpha,
                    scale=n.props.get("bg_scale"),
                    fixed_y=bg_vp,
                )
                if bg_cmds:
                    cmds.extend(bg_cmds)
                    bg_drawn = True
            if not bg_drawn and role not in ("topbar", "navbar", "sticky_header", "card", "dialog", "scaffold", "surface", "lazy_list") and not n.props.get("$reusable_node"):
                par_sibs = getattr(par, "children", ()) if par is not None else ()
                is_selected_card = (
                    cls == "CardView"
                    and any(s.vid == "click_overlay" and not s.visible for s in par_sibs)
                )
                if is_selected_card:
                    cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                                 "fill": "#eef3ff" if not dark else "#22304a",
                                 "outline": "#2f6df0", "r": 8})
                elif n is tree.root or (not n.children and cls not in ("View", "FrameLayout", "Space")):
                    out_col = ("#7aa7dc" if (win_dark and not dark) else ("#2a2f3a" if dark else "#d3d8e2"))
                    cmds.append({"op": "rect", "x": x, "y": y, "w": w, "h": h,
                                 "fill": "", "outline": out_col, "r": 4})
            div_info = n.props.get("$draw_center_divider_y")
            if div_info and w > 40:
                dy0, dh0 = div_info
                cmds.append({"op": "rect", "x": x + w / 2 - 0.5, "y": dy0, "w": 1, "h": max(6, dh0),
                             "fill": "#ffffff", "outline": "", "r": 0})
    hl = getattr(tree, "_highlight_node", None)
    if hl is not None and getattr(hl, "bounds", None):
        hx, hy, hw, hh = hl.bounds
        if hw > 0 and hh > 0:
            cmds.append({"op": "rect", "x": hx, "y": hy, "w": hw, "h": hh,
                         "fill": "", "outline": "#00e5ff", "r": 4, "width": 2})
    toasts = getattr(tree, "_toasts", None)
    last_toast = str(getattr(tree, "last_toast", "") or "")
    t_msg = ""
    if toasts:
        last_t = toasts[-1]
        t_msg = str(last_t.get("text") if isinstance(last_t, dict) else last_t)
    elif last_toast:
        t_msg = last_toast
    if t_msg:
        tw = max(120, (getattr(tree, "width", 380) or 380) - 64)
        tx = 32
        ty = max(40, (getattr(tree, "height", 720) or 720) - 72)
        cmds.append({"op": "rect", "x": tx, "y": ty, "w": tw, "h": 34,
                     "fill": "#22262e", "outline": "#4b5563", "r": 17, "role": "toast", "fixed_y": True})
        cmds.append({"op": "text", "x": tx + tw / 2, "y": ty + 17,
                     "text": t_msg, "color": "#f8fafc", "anchor": "c", "font_size": 10, "fixed_y": True})
    return cmds


def swipe_pager(tree, x, y, direction, stubs=None):
    """Wischt in einem Compose-Pager ($pager_state) oder ViewPager/ViewPager2 um `direction` (+1/-1) weiter."""
    if tree is None or getattr(tree, "root", None) is None:
        return False
    direction = 1 if int(direction or 0) >= 0 else -1
    target = tree.node_at(x, y)
    pnode = None
    cur = target
    while cur is not None:
        if (cur.props or {}).get("$pager_state") is not None or "ViewPager" in (cur.cls or ""):
            pnode = cur
            break
        cur = getattr(cur, "_parent", None)
    if pnode is None:
        for nd in tree.visible_nodes():
            if (nd.props or {}).get("$pager_state") is not None or "ViewPager" in (nd.cls or ""):
                bx, by, bw, bh = nd.bounds
                if bw > 0 and bh > 0 and (x < 0 or (bx <= x < bx + bw and by <= y < by + bh)):
                    pnode = nd
                    break
        if pnode is None:
            for nd in tree.visible_nodes():
                if (nd.props or {}).get("$pager_state") is not None or "ViewPager" in (nd.cls or ""):
                    pnode = nd
                    break
    if pnode is None:
        return False

    pstate = (pnode.props or {}).get("$pager_state")
    if isinstance(pstate, dict) or hasattr(pstate, "fields"):
        st_d = pstate.fields if hasattr(pstate, "fields") else pstate
        cur_p = int(st_d.get("_page", 0) or 0)
        cnt = max(1, int(st_d.get("_page_count", 3) or 3))
        nxt = max(0, min(cnt - 1, cur_p + direction))
        st_d["_page"] = nxt
        pnode.props["$pager_page"] = nxt
        rc = (pnode.props or {}).get("$pager_recomp") or getattr(tree, "_compose_recompose", None)
        if callable(rc):
            try:
                rc()
            except Exception:
                pass
        tree.touch()
        return True

    jobj = (pnode.props or {}).get("$jobj")
    cur_p = int((jobj.fields.get("__item", 0) if hasattr(jobj, "fields") else pnode.props.get("_vp_item", 0)) or 0)
    nxt = max(0, cur_p + direction)
    if hasattr(jobj, "fields"):
        jobj.fields["__item"] = nxt
    pnode.props["_vp_item"] = nxt
    st_ref = stubs or getattr(getattr(tree, "_vm", None), "_stubs_ref", None)
    if jobj is not None and st_ref is not None:
        fn = st_ref.resolve(getattr(jobj, "cls", "Landroidx/viewpager2/widget/ViewPager2;"), "setCurrentItem", "(I)V")
        if callable(fn):
            try:
                fn((jobj, nxt))
            except Exception:
                pass
    tree.touch()
    return True


def demo_screen():
    """Beispiel-Screen, damit das Anzeige-Fenster sofort etwas Bedienbares zeigt.
    Der Button hat einen Python-Callback (on_click), damit der Klick->Effekt-Kreis ohne echte App
    schon sichtbar ist (echte Apps liefern stattdessen ein Listener-JObject)."""
    import itertools
    root = ViewNode("LinearLayout", vid="root")
    root.add(ViewNode("TextView", vid="title", text="Glukose-Monitor (Demo)"))
    value = ViewNode("TextView", vid="value", text="-- mg/dL")
    root.add(value)
    btn = ViewNode("Button", vid="refresh", text="Aktualisieren", clickable=True)
    root.add(btn)
    inp = ViewNode("EditText", vid="note", text="", focusable=True)
    inp.props["hint"] = "Notiz eingeben..."
    root.add(inp)
    _seq = itertools.cycle([97, 112, 128, 143, 119, 104])

    def _refresh(view=None):
        value.text = "%d mg/dL" % next(_seq)
    btn.on_click = _refresh
    t = ViewTree()
    t.set_root(root)
    return t




def widgets_demo():
    """Interaktiver Widget-Showcase (CheckBox/Switch/ProgressBar/Button) fuer das Anzeige-Fenster:
    CheckBox + Switch toggeln per Klick, der Button erhoeht den Fortschrittsbalken. Zielagnostisch,
    ohne echte App (reine Python-Callbacks)."""
    root = ViewNode("LinearLayout", vid="root")
    root.add(ViewNode("TextView", vid="title", text="Widget-Demo"))
    cb = ViewNode("CheckBox", vid="cb", text="Benachrichtigungen", clickable=True)
    sw = ViewNode("Switch", vid="sw", text="Nachtmodus", clickable=True)
    pb = ViewNode("ProgressBar", vid="pb")
    pb.props["progress"] = 20
    btn = ViewNode("Button", vid="more", text="Fortschritt +20%", clickable=True)
    grp = ViewNode("RadioGroup", vid="rg")
    r1 = ViewNode("RadioButton", vid="r1", text="mg/dL", clickable=True)
    r2 = ViewNode("RadioButton", vid="r2", text="mmol/L", clickable=True)
    grp.add(r1); grp.add(r2)
    r1.props["checked"] = True
    for w in (cb, sw, pb, grp, btn):
        root.add(w)
    t = ViewTree()

    def _radio(sel):
        def fn(view=None, _s=sel):
            for sib in grp.children:
                if sib.cls == "RadioButton":
                    sib.props["checked"] = (sib is _s)
            t.touch()
        return fn
    r1.on_click = _radio(r1)
    r2.on_click = _radio(r2)

    def _toggle(node):
        def fn(view=None, _n=node):
            _n.props["checked"] = not bool(_n.props.get("checked"))
            t.touch()
        return fn
    cb.on_click = _toggle(cb)
    sw.on_click = _toggle(sw)

    def _more(view=None):
        pb.props["progress"] = min(100, int(pb.props.get("progress", 0) or 0) + 20)
        t.touch()
    btn.on_click = _more

    t.set_root(root)
    return t


def info_screen(title, lines, badge=None):
    """Informations-Screen fuer das Anzeige-Fenster, wenn ein Lauf KEINEN android.view-Baum liefert
    (definierte App-Runtime-Grenze: React Native / NativeScript / Flutter bauen ihre UI zur Laufzeit
    aus JS/Dart, nicht als android.view-Hierarchie). Zeigt die Grenze lesbar statt leer.

    title: Ueberschrift (z.B. "NativeScript-App erkannt").
    lines: Liste erklaerender Textzeilen.
    badge: optionaler kurzer Status-Tag (z.B. "RUNTIME-GRENZE").
    Rueckgabe: fertig gelayouteter ViewTree (nicht klickbar).
    """
    root = ViewNode("LinearLayout", vid="info_root")
    if badge:
        b = ViewNode("TextView", vid="info_badge", text="[ %s ]" % badge)
        b.props["color"] = "#b15c00"
        root.add(b)
    head = ViewNode("TextView", vid="info_title", text=title or "Hinweis")
    head.props["h"] = 52
    root.add(head)
    for i, ln in enumerate(lines or []):
        tv = ViewNode("TextView", vid="info_%d" % i, text=str(ln))
        root.add(tv)
    t = ViewTree()
    t.set_root(root)
    return t


def install_view_stubs(stubs, app_root=None, on_navigate=None):
    """Haengt generische android.widget/view-Stubs in eine AndroidStubs-Instanz ein, die den
    programmatisch aufgebauten View-Baum aufzeichnen. Rueckgabe: die ViewTree-Instanz (auch
    `stubs.view_tree`)."""
    from core.java.objects import JObject, JString

    tree = ViewTree()
    stubs.view_tree = tree

    def _node(jobj, cls=None):
        # View-Klasse aus der JObject-Klasse ableiten (Landroid/widget/Button; -> Button);
        # robust auch ohne <init>-Stub (die VM kuerzt <init> ab).
        if not isinstance(jobj, JObject):
            return None
        n = jobj.fields.get("$view")
        if n is None:
            src = getattr(jobj, "cls", None) or cls or "View"
            short = src.rsplit("/", 1)[-1].rstrip(";")
            n = ViewNode(cls=short)
            n._jobj = jobj
            jobj.fields["$view"] = n
        return n

    def _wrap_node(node):
        if node is None:
            return None
        jo = getattr(node, "_jobj", None)
        if not isinstance(jo, JObject):
            jo = JObject(_widget_jcls(node), {"$view": node})
            node._jobj = jo
        else:
            jo.fields["$view"] = node
        return jo

    def _text(v):
        if isinstance(v, JString):
            return v.text
        if v is None:
            return ""
        if isinstance(v, JObject):
            for k in ("$text", "text", "value"):
                tv = v.fields.get(k)
                if tv is not None:
                    return tv.text if isinstance(tv, JString) else str(tv)
            return ""
        return str(v)

    def _f32_val(raw, default=1.0):
        if isinstance(raw, float):
            return raw
        if isinstance(raw, int):
            if raw == 0:
                return 0.0
            if -1 <= raw <= 10:
                return float(raw)
            try:
                return float(_struct.unpack("<f", _struct.pack("<I", raw & 0xFFFFFFFF))[0])
            except Exception:
                return default
        return default

    def _init(cls):
        def fn(a):
            if a:
                _node(a[0], cls)
            return a[0] if a else None
        return fn

    widgets = ("Landroid/widget/TextView;", "Landroid/widget/Button;", "Landroid/widget/EditText;",
               "Landroid/widget/LinearLayout;", "Landroid/widget/FrameLayout;",
               "Landroid/widget/ImageView;", "Landroid/view/View;")
    for cls in widgets:
        for sig in ("(Landroid/content/Context;)V", "()V"):
            stubs.register(cls, "<init>", sig, _init(cls))

    def _set_text(a):
        if a:
            n = _node(a[0])
            if n is not None and len(a) > 1:
                n.text = _text(a[1])
                tree.touch()
        return None
    stubs.register_by_name("setText", "(Ljava/lang/CharSequence;)V", _set_text)
    stubs.register_by_name("setText", "(Ljava/lang/String;)V", _set_text)

    def _get_text(a):
        n = _node(a[0]) if a else None
        t = (n.text if n is not None else "") or ""
        if not t and n is not None:
            p = getattr(n, "_parent", None)
            while p is not None:
                if p.cls == "EditText" and (p.text or ""):
                    t = p.text
                    break
                p = getattr(p, "_parent", None)
        return JString(t)
    stubs.register_by_name("getText", "()Ljava/lang/CharSequence;", _get_text)
    stubs.register_by_name("getText", "()Ljava/lang/String;", _get_text)
    def _get_editable(a):
        vn = _node(a[0]) if a else None
        s = _get_text(a).text
        return JObject("Landroid/text/SpannableStringBuilder;", {"$text": s, "$node": vn})
    stubs.register_by_name("getText", "()Landroid/text/Editable;", _get_editable)
    stubs.register_by_name("getEditableText", "()Landroid/text/Editable;", _get_editable)

    def _set_tag(a):
        if a and isinstance(a[0], JObject):
            if len(a) > 2:
                a[0].fields.setdefault("_tags", {})[int(a[1] or 0)] = a[2]
            elif len(a) > 1:
                a[0].fields["_tag"] = a[1]
        return None

    def _get_tag(a):
        if a and isinstance(a[0], JObject):
            if len(a) > 1:
                return a[0].fields.get("_tags", {}).get(int(a[1] or 0))
            return a[0].fields.get("_tag")
        return None
    stubs.register_by_name("setTag", "(Ljava/lang/Object;)V", _set_tag)
    stubs.register_by_name("getTag", "()Ljava/lang/Object;", _get_tag)
    stubs.register_by_name("setTag", "(ILjava/lang/Object;)V", _set_tag)
    stubs.register_by_name("getTag", "(I)Ljava/lang/Object;", _get_tag)

    def _set_alpha(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                al = max(0.0, min(1.0, _f32_val(a[1], 1.0)))
                n.props["alpha"] = al
                if n.cls in ("ImageView", "ImageButton"):
                    n.props["img_alpha"] = al
                tree.touch()
        return None
    stubs.register_by_name("setAlpha", "(F)V", _set_alpha)
    stubs.register_by_name("getAlpha", "()F", lambda a: float(_node(a[0]).props.get("alpha", 1.0)) if (a and _node(a[0])) else 1.0)
    stubs.register_by_name("setTranslationX", "(F)V", lambda a: None)
    stubs.register_by_name("setTranslationY", "(F)V", lambda a: None)
    stubs.register_by_name("requestFocus", "()Z", lambda a: 1)
    stubs.register_by_name("getWindowToken", "()Landroid/os/IBinder;", lambda a: JObject("Landroid/os/IBinder;", {}))
    stubs.register_by_name("setPadding", "(IIII)V", lambda a: None)
    stubs.register_by_name("setPaddingRelative", "(IIII)V", lambda a: None)
    for _pg in ("getPaddingLeft", "getPaddingTop", "getPaddingRight", "getPaddingBottom"):
        stubs.register_by_name(_pg, "()I", lambda a: 0)
    def _set_scale_type(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None and isinstance(a[1], JObject):
                st = a[1].fields.get("__ename") or a[1].fields.get("name")
                if st:
                    n.props["scale_type"] = str(st)
                    tree.touch()
        return None
    stubs.register_by_name("setScaleType", "(Landroid/widget/ImageView$ScaleType;)V", _set_scale_type)

    def _length(a):
        o = a[0] if a else None
        return len(o.text) if isinstance(o, JString) else 0
    stubs.register_by_name("length", "()I", _length)

    def _add_view(a):
        if a and len(a) > 1:
            p = _node(a[0], "ViewGroup")
            c = _node(a[1], "View")
            if p is not None and c is not None and c not in p.children:
                p.add(c)
                tree.touch()
        return None
    stubs.register_by_name("addView", "(Landroid/view/View;)V", _add_view)

    def _set_click(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                n.clickable = True
                n.on_click = a[1]
                tree.touch()
        return None
    stubs.register_by_name("setOnClickListener",
                           "(Landroid/view/View$OnClickListener;)V", _set_click)

    stubs._fragments_by_id = {}
    stubs._fragments_by_tag = {}
    stubs._pending_fragment_txs = []
    _rev_idmap = {}
    _idmap_id_ref = {}
    _arr_idmap = {}

    def _safe_frag_call(vm_obj, obj, mname, sig, args):
        if vm_obj is None or not hasattr(vm_obj, "invoke_virtual"):
            return None
        try:
            return vm_obj.invoke_virtual(obj, mname, sig, args)
        except Exception as e:
            emsg = str(e)
            if type(e).__name__ in ("DebugStop", "DalvikUnknownOpcode") or any(
                k in emsg for k in ("StepBudget", "WallBudget", "StackOverflow", "max_depth")
            ):
                raise
            return None

    def _init_custom_views(root_node):
        if root_node is None:
            return
        vm_obj = getattr(stubs, "vm", None)
        if vm_obj is None or not hasattr(vm_obj, "_resolve_method"):
            return
        ctx = getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)
        for node in list(root_node.walk()):
            fcls = node.props.get("full_cls")
            if not fcls or node.props.get("$custom_init"):
                continue
            node.props["$custom_init"] = True
            if fcls.startswith(("android.", "androidx.", "com.google.android.")):
                continue
            jcls = "L" + str(fcls).replace(".", "/") + ";"
            md2 = vm_obj._resolve_method(jcls, jcls, "<init>", "(Landroid/content/Context;Landroid/util/AttributeSet;)V")
            if md2 is not None:
                jo = _wrap_node(node)
                attrs = JObject("Landroid/util/AttributeSet;", {})
                _safe_frag_call(vm_obj, jo, "<init>", "(Landroid/content/Context;Landroid/util/AttributeSet;)V", (jo, ctx, attrs))
                continue
            md1 = vm_obj._resolve_method(jcls, jcls, "<init>", "(Landroid/content/Context;)V")
            if md1 is not None:
                jo = _wrap_node(node)
                _safe_frag_call(vm_obj, jo, "<init>", "(Landroid/content/Context;)V", (jo, ctx))

    def _mount_fragment_instance(frag, container_node, tag=None):
        if not isinstance(frag, JObject) or container_node is None:
            return False
        vm_obj = getattr(stubs, "vm", None)
        cur_act = getattr(stubs, "_current_activity", None)
        frag.fields["$is_fragment"] = True
        frag.fields.setdefault("_host_act", cur_act)
        if container_node.vid:
            stubs._fragments_by_id[container_node.vid] = frag
            if container_node.vid in _rev_idmap:
                stubs._fragments_by_id[_rev_idmap[container_node.vid]] = frag
        if tag:
            if not hasattr(stubs, "_fragments_by_tag"):
                stubs._fragments_by_tag = {}
            stubs._fragments_by_tag[str(tag)] = frag
        if vm_obj is None:
            return True
        old_budget = getattr(vm_obj, "step_budget", None)
        cur_steps = int(getattr(vm_obj, "steps", 0) or 0)
        if isinstance(old_budget, int) and old_budget > 0:
            vm_obj.step_budget = max(old_budget, cur_steps + 300_000)
        try:
            existing_v = frag.fields.get("_view")
            if isinstance(existing_v, JObject) and "$view" in existing_v.fields:
                vnode = existing_v.fields["$view"]
                if vnode is not None and vnode not in container_node.children:
                    container_node.add(vnode)
                _safe_frag_call(vm_obj, frag, "onStart", "()V", (frag,))
                _safe_frag_call(vm_obj, frag, "onResume", "()V", (frag,))
                _flush_fragment_transactions()
                tree.touch()
                return True
            if not frag.fields.get("$frag_created"):
                frag.fields["$frag_created"] = True
                _safe_frag_call(vm_obj, frag, "onAttach", "(Landroid/content/Context;)V", (frag, cur_act))
                _safe_frag_call(vm_obj, frag, "onCreate", "(Landroid/os/Bundle;)V", (frag, None))
            inflater = JObject("Landroid/view/LayoutInflater;", {})
            container = _wrap_node(container_node)
            v_obj = _safe_frag_call(
                vm_obj, frag, "onCreateView",
                "(Landroid/view/LayoutInflater;Landroid/view/ViewGroup;Landroid/os/Bundle;)Landroid/view/View;",
                (frag, inflater, container, None),
            )
            if isinstance(v_obj, JObject) and "$view" in v_obj.fields:
                vnode = v_obj.fields["$view"]
                if vnode is not None and vnode not in container_node.children:
                    container_node.add(vnode)
                frag.fields["_view"] = v_obj
                _init_custom_views(vnode)
                _safe_frag_call(vm_obj, frag, "onViewCreated", "(Landroid/view/View;Landroid/os/Bundle;)V",
                                (frag, v_obj, None))
                _safe_frag_call(vm_obj, frag, "onStart", "()V", (frag,))
                _safe_frag_call(vm_obj, frag, "onResume", "()V", (frag,))
                _flush_fragment_transactions()
                tree.touch()
        finally:
            if isinstance(old_budget, int) and old_budget > 0:
                end_steps = int(getattr(vm_obj, "steps", 0) or 0)
                rem = max(0, old_budget - cur_steps)
                vm_obj.step_budget = max(old_budget, end_steps + min(rem, 100_000))
        return True

    def _flush_fragment_transactions():
        txs = getattr(stubs, "_pending_fragment_txs", None)
        if not txs:
            return False
        did = False
        while txs:
            tx = txs.pop(0)
            fm = tx.get("fm")
            host_node = fm.fields.get("_host_node") if isinstance(fm, JObject) else None
            for op in tx.get("ops", []):
                kind = op[0]
                if kind in ("replace", "add"):
                    cid, frag = op[1], op[2]
                    vid_name = _idmap_id_ref.get(cid) if isinstance(cid, int) else cid
                    cnode = None
                    if vid_name:
                        if host_node is not None:
                            cnode = host_node.find(vid_name)
                        if cnode is None and tree.root is not None:
                            cnode = tree.root.find(vid_name)
                    if cnode is None and host_node is not None:
                        cnode = host_node
                    if cnode is not None and isinstance(frag, JObject):
                        if kind == "replace":
                            cnode.children.clear()
                        _mount_fragment_instance(frag, cnode)
                        did = True
                elif kind in ("show", "hide") and isinstance(op[1], JObject):
                    vo = op[1].fields.get("_view")
                    if isinstance(vo, JObject) and "$view" in vo.fields:
                        vo.fields["$view"].visible = (kind == "show")
                        tree.touch()
                        did = True
        return did

    stubs._flush_fragment_transactions = _flush_fragment_transactions

    def _mount_fragments(root_node):
        if root_node is None:
            return
        vm_obj = getattr(stubs, "vm", None)
        cur_act = getattr(stubs, "_current_activity", None)
        pkg = getattr(getattr(stubs, "profile", None), "package", None) or ""
        for fc in list(root_node.walk()):
            fname = fc.props.get("fragment_name")
            if not fname or fc.props.get("$frag_mounted"):
                continue
            fc.props["$frag_mounted"] = True
            clean_fn = str(fname).strip()
            if clean_fn.startswith(".") and pkg:
                clean_fn = pkg + clean_fn
            fdesc = clean_fn if (clean_fn.startswith("L") and clean_fn.endswith(";")) else (
                "L" + clean_fn.replace(".", "/") + ";"
            )
            frag = JObject(fdesc, {"$is_fragment": True, "_host_act": cur_act})
            if vm_obj is not None:
                _safe_frag_call(vm_obj, frag, "<init>", "()V", (frag,))
            _mount_fragment_instance(frag, fc)

    stubs.register("Landroid/view/LayoutInflater;", "from",
                   "(Landroid/content/Context;)Landroid/view/LayoutInflater;",
                   lambda a: JObject("Landroid/view/LayoutInflater;", {}))
    stubs.register_by_name("getLayoutInflater", "()Landroid/view/LayoutInflater;",
                           lambda a: JObject("Landroid/view/LayoutInflater;", {}))
    stubs.register_by_name("getChildCount", "()I",
                           lambda a: len(a[0].fields["$view"].children)
                           if (a and isinstance(a[0], JObject) and "$view" in a[0].fields) else 0)
    stubs.register_by_name("getChildAt", "(I)Landroid/view/View;",
                           lambda a: (
                               lambda ch: JObject(_widget_jcls(ch), {"$view": ch})
                           )(a[0].fields["$view"].children[int(a[1])])
                           if (a and len(a) > 1 and isinstance(a[0], JObject) and "$view" in a[0].fields
                               and 0 <= int(a[1]) < len(a[0].fields["$view"].children)) else None)
    stubs.register_by_name("getLayoutParams", "()Landroid/view/ViewGroup$LayoutParams;",
                           lambda a: a[0].fields.setdefault(
                               "_lp", JObject("Landroidx/constraintlayout/widget/ConstraintLayout$LayoutParams;", {})
                           ) if (a and isinstance(a[0], JObject)) else JObject("Landroid/view/ViewGroup$LayoutParams;", {}))
    stubs.register_by_name("setLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)V",
                           lambda a: a[0].fields.__setitem__("_lp", a[1])
                           if (a and len(a) > 1 and isinstance(a[0], JObject)) else None)
    stubs.register_by_name("setMargins", "(IIII)V", lambda a: None)

    def _set_content(a):
        if a and len(a) > 1:
            n = _node(a[1], "View")
            if n is not None:
                tree._compose_recompose = None
                if tree.window_bg is not None and not tree.window_bg_dark:
                    tree.dark_mode = False
                _init_custom_views(n)
                _mount_fragments(n)
                tree.set_root(n)
        return None
    stubs.register_by_name("setContentView", "(Landroid/view/View;)V", _set_content)
    stubs.register_by_name("setContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", _set_content)

    # --- weitere UI-Setter (Breite fuer echten onCreate-Durchlauf) ---
    def _set_vis(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                try:
                    n.visible = (int(a[1]) == 0)   # VISIBLE=0, INVISIBLE=4, GONE=8
                except Exception:
                    n.visible = True
                tree.touch()
        return None
    stubs.register_by_name("setVisibility", "(I)V", _set_vis)

    def _prop_setter(key, as_text=False):
        def fn(a):
            if a and len(a) > 1:
                n = _node(a[0])
                if n is not None:
                    n.props[key] = _text(a[1]) if as_text else a[1]
                    tree.touch()
            return None
        return fn
    def _set_text_color(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                v = a[1]
                ci = None
                if isinstance(v, JObject):
                    raw_c = v.fields.get("$color", v.fields.get("$default", v.fields.get("mDefaultColor")))
                    if raw_c is not None:
                        ci = int(raw_c) & 0xFFFFFFFF
                elif isinstance(v, int):
                    ci = int(v) & 0xFFFFFFFF
                if ci is not None:
                    n.props["fg"] = ci
                    if ((ci >> 24) & 0xFF) > 10 or (ci != 0 and ci <= 0xFFFFFF):
                        n.props["color"] = f"#{ci & 0xFFFFFF:06x}"
                else:
                    n.props["fg"] = v
                tree.touch()
        return None
    stubs.register_by_name("setHint", "(Ljava/lang/CharSequence;)V", _prop_setter("hint", True))
    stubs.register_by_name("setBackgroundColor", "(I)V", _prop_setter("bg"))
    stubs.register_by_name("setTextColor", "(I)V", _set_text_color)
    stubs.register_by_name("setTextColor", "(Landroid/content/res/ColorStateList;)V", _set_text_color)
    stubs.register_by_name("setOrientation", "(I)V", _prop_setter("orientation"))
    stubs.register_by_name("setContentDescription", "(Ljava/lang/CharSequence;)V", _prop_setter("desc", True))
    stubs.register_by_name("setGravity", "(I)V", _prop_setter("gravity"))

    def _set_text_res(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                sm = getattr(stubs, "_strmap_id", None)
                txt = sm.get(int(a[1])) if (sm and isinstance(a[1], int)) else None
                n.text = txt if txt is not None else ("res#%s" % a[1])
                tree.touch()
        return None
    stubs.register_by_name("setText", "(I)V", _set_text_res)

    for _sig in ("(Landroid/view/View;I)V", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V"):
        stubs.register_by_name("addView", _sig, _add_view)

    def _record_web_event(kind, url="", extra=None, node=None):
        ev = {"kind": kind, "url": str(url or "")}
        if extra:
            ev.update(extra)
        tree.web_events.append(ev)
        if node is not None:
            html_inline = str((extra or {}).get("html") or "")
            target_u = ev["url"]
            if target_u:
                node.props["web_url"] = target_u
                node.clickable = True
                node.text = f"\U0001f310 {target_u[:120]}"
            local_html = ""
            if html_inline:
                local_html = html_inline
            elif target_u.startswith("file:///android_asset/") and app_root:
                import os as _os
                rel = target_u[len("file:///android_asset/"):].lstrip("/")
                cand = _os.path.join(app_root, "assets", rel.replace("/", _os.sep))
                if _os.path.isfile(cand):
                    try:
                        local_html = open(cand, "r", encoding="utf-8", errors="replace").read()
                    except Exception:
                        local_html = ""
            if local_html:
                try:
                    from services.webview_inspector_service import _DomInspectorParser
                    prs = _DomInspectorParser(base_url=target_u)
                    prs.feed(local_html)
                    prs.close()
                    node.children.clear()
                    if prs.title:
                        node.add(ViewNode("TextView", vid="wv_title", text=prs.title))
                    for bi, (_tg, btxt) in enumerate(prs.text_blocks[:6]):
                        node.add(ViewNode("TextView", vid=f"wv_txt_{bi}", text=btxt[:200]))
                except Exception:
                    pass
            tree.touch()
        try:
            from core.application.event_bus import EventBus
            EventBus.publish("WEBVIEW_EVENT", ev)
        except Exception:
            pass
        return ev

    stubs._record_web_event = _record_web_event

    def _toast_make(a):
        t = JObject("Landroid/widget/Toast;")
        if len(a) > 1:
            if isinstance(a[1], int):
                sm = getattr(stubs, "_strmap_id", None) or {}
                t.fields["$text"] = sm.get(int(a[1]), "")
            else:
                t.fields["$text"] = _text(a[1])
        return t
    stubs.register("Landroid/widget/Toast;", "makeText",
                   "(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;", _toast_make)
    stubs.register("Landroid/widget/Toast;", "makeText",
                   "(Landroid/content/Context;II)Landroid/widget/Toast;", _toast_make)

    def _mount_dialog_overlay(dlg_obj, title="", msg=""):
        if tree.root is None:
            return
        # Alte Instanz dieses Dialogs entfernen
        tree.root.children = [
            c for c in tree.root.children
            if not (c.props.get("role") == "dialog" and c.props.get("$dlg") is dlg_obj)
        ]
        card = ViewNode("LinearLayout", props={"role": "dialog", "$dlg": dlg_obj})
        if title:
            card.add(ViewNode("TextView", vid="dlg_title", text=str(title)))
        if msg:
            card.add(ViewNode("TextView", vid="dlg_msg", text=str(msg)))
        elif not title:
            card.add(ViewNode("TextView", vid="dlg_msg", text="Dialog"))
        ok_btn = ViewNode("Button", vid="dlg_ok", text="OK", clickable=True)
        def _close_dlg(_v=None, _d=dlg_obj, _card=card):
            if tree.root is not None and _card in tree.root.children:
                tree.root.children.remove(_card)
                tree.touch()
        ok_btn.on_click = _close_dlg
        card.add(ok_btn)
        tree.root.add(card)
        tree.touch()

    def _dismiss_dialog_overlay(dlg_obj):
        if tree.root is None:
            return
        before = len(tree.root.children)
        tree.root.children = [
            c for c in tree.root.children
            if not (c.props.get("role") == "dialog" and (dlg_obj is None or c.props.get("$dlg") is dlg_obj))
        ]
        if len(tree.root.children) != before:
            tree.touch()

    def _generic_show(a):
        if not a or not isinstance(a[0], JObject):
            return None
        recv = a[0]
        if recv.cls == "Landroid/widget/Toast;":
            txt = str(recv.fields.get("$text") or recv.fields.get("_text") or "").strip()
            if txt:
                tree.last_toast = txt
                tree.__dict__.setdefault("_toasts", []).append(txt)
                _mount_dialog_overlay(recv, title="Hinweis", msg=txt)
            return None
        title = str(recv.fields.get("$dlg_title") or "").strip()
        msg = str(recv.fields.get("$dlg_msg") or "").strip()
        if not msg:
            for fv in recv.fields.values():
                if isinstance(fv, JString) and fv.text.strip():
                    if not title:
                        title = fv.text.strip()
                    else:
                        msg = fv.text.strip()
                        break
        _mount_dialog_overlay(recv, title=title, msg=msg)
        return None

    stubs.register_by_name("show", "()V", _generic_show)
    stubs.register_by_name("dismiss", "()V", lambda a: _dismiss_dialog_overlay(a[0] if a else None))
    stubs.register_by_name("cancel", "()V", lambda a: _dismiss_dialog_overlay(a[0] if a else None))
    stubs.register_by_name("setEnabled", "(Z)V", lambda a: None)

    for _ab_cls in ("Landroid/app/AlertDialog$Builder;", "Landroidx/appcompat/app/AlertDialog$Builder;"):
        stubs.register(_ab_cls, "<init>", "(Landroid/content/Context;)V", lambda a: a[0] if a else None)
        stubs.register(_ab_cls, "<init>", "(Landroid/content/Context;I)V", lambda a: a[0] if a else None)
        def _ab_set_str(key):
            def _fn(a):
                if a and len(a) > 1 and isinstance(a[0], JObject):
                    val = (_strmap_id_lookup(a[1]) if isinstance(a[1], int) else _text(a[1]))
                    a[0].fields[key] = val
                return a[0] if a else None
            return _fn
        def _strmap_id_lookup(rid):
            sm = getattr(stubs, "_strmap_id", None) or {}
            return sm.get(int(rid), f"res#{rid}")
        for _msig in ("(Ljava/lang/CharSequence;)%s" % _ab_cls, "(I)%s" % _ab_cls):
            stubs.register(_ab_cls, "setTitle", _msig, _ab_set_str("$dlg_title"))
            stubs.register(_ab_cls, "setMessage", _msig, _ab_set_str("$dlg_msg"))
        for _bsig in (
            "(Ljava/lang/CharSequence;Landroid/content/DialogInterface$OnClickListener;)%s" % _ab_cls,
            "(ILandroid/content/DialogInterface$OnClickListener;)%s" % _ab_cls,
        ):
            stubs.register(_ab_cls, "setPositiveButton", _bsig, lambda a: a[0] if a else None)
            stubs.register(_ab_cls, "setNegativeButton", _bsig, lambda a: a[0] if a else None)
        def _ab_create(a):
            b = a[0] if a and isinstance(a[0], JObject) else JObject(_ab_cls)
            return JObject("Landroid/app/AlertDialog;", {
                "$dlg_title": b.fields.get("$dlg_title", ""),
                "$dlg_msg": b.fields.get("$dlg_msg", ""),
            })
        stubs.register(_ab_cls, "create", "()Landroid/app/AlertDialog;", _ab_create)
        stubs.register(_ab_cls, "create", "()Landroidx/appcompat/app/AlertDialog;", _ab_create)
        def _ab_show(a):
            d = _ab_create(a)
            _mount_dialog_overlay(d, d.fields.get("$dlg_title", ""), d.fields.get("$dlg_msg", ""))
            return d
        stubs.register(_ab_cls, "show", "()Landroid/app/AlertDialog;", _ab_show)
        stubs.register(_ab_cls, "show", "()Landroidx/appcompat/app/AlertDialog;", _ab_show)

    # --- Compound-Widgets (CheckBox/Switch/ToggleButton): klickbar + Toggle-Zustand ---
    def _init_toggle(cls):
        def fn(a):
            n = _node(a[0], cls) if a else None
            if n is not None:
                n.clickable = True
                def _tog(view=None, _n=n):
                    _n.props["checked"] = not bool(_n.props.get("checked"))
                    tree.touch()
                n.on_click = _tog
            return a[0] if a else None
        return fn
    for _c in ("Landroid/widget/CheckBox;", "Landroid/widget/Switch;",
               "Landroid/widget/ToggleButton;", "Landroid/widget/CompoundButton;"):
        for _sig in ("(Landroid/content/Context;)V", "()V"):
            stubs.register(_c, "<init>", _sig, _init_toggle(_c))
    for _c in ("Landroid/widget/ProgressBar;", "Landroid/widget/SeekBar;"):
        for _sig in ("(Landroid/content/Context;)V", "()V"):
            stubs.register(_c, "<init>", _sig, _init(_c))

    def _refresh_node_state_visuals(node_root):
        resv = getattr(stubs, "_drawable_resolver", None)
        if node_root is None or resv is None:
            return
        def _walk_ref(nd, inherited_st=None):
            st = {
                "selected": bool(nd.props["selected"]) if "selected" in nd.props else (inherited_st or {}).get("selected", False),
                "checked": bool(nd.props["checked"]) if "checked" in nd.props else (inherited_st or {}).get("checked", False),
                "enabled": bool(nd.props["enabled"]) if "enabled" in nd.props else (inherited_st or {}).get("enabled", True),
            }
            if any(k in nd.props for k in ("selected", "checked", "enabled")) or inherited_st is not None:
                if nd.props.get("bg_ref"):
                    sp = resv.resolve(nd.props["bg_ref"], state=st)
                    if sp is not None:
                        nd.props["bg_drawable"] = sp
                if nd.props.get("src_ref"):
                    sp = resv.resolve(nd.props["src_ref"], state=st)
                    if sp is not None:
                        nd.props["drawable"] = sp
                col_fn = getattr(resv, "resolve_color_for_state", None)
                if callable(col_fn):
                    if nd.props.get("color_ref"):
                        rgb, alpha = col_fn(nd.props["color_ref"], state=st)
                        if rgb is not None and alpha > 0.05:
                            nd.props["color"] = rgb
                    if nd.props.get("tint_ref"):
                        rgb, alpha = col_fn(nd.props["tint_ref"], state=st)
                        if rgb is not None and alpha > 0.05:
                            nd.props["tint"] = rgb
                    if nd.props.get("bg_tint_ref"):
                        rgb, alpha = col_fn(nd.props["bg_tint_ref"], state=st)
                        if rgb is not None and alpha > 0.05:
                            nd.props["bg_tint"] = rgb
                            nd.props["bg_tint_alpha"] = alpha
            for ch in getattr(nd, "children", ()) or ():
                _walk_ref(ch, st)
        _walk_ref(node_root)

    def _set_checked(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                n.props["checked"] = bool(a[1])
                _refresh_node_state_visuals(n)
                tree.touch()
        return None
    stubs.register_by_name("setChecked", "(Z)V", _set_checked)

    def _is_checked(a):
        n = _node(a[0]) if a else None
        return 1 if (n is not None and n.props.get("checked")) else 0
    stubs.register_by_name("isChecked", "()Z", _is_checked)

    def _set_selected(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                n.props["selected"] = bool(a[1])
                _refresh_node_state_visuals(n)
                tree.touch()
        return None
    stubs.register_by_name("setSelected", "(Z)V", _set_selected)

    def _is_selected(a):
        n = _node(a[0]) if a else None
        return 1 if (n is not None and n.props.get("selected")) else 0
    stubs.register_by_name("isSelected", "()Z", _is_selected)

    def _set_enabled(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                n.props["enabled"] = bool(a[1])
                _refresh_node_state_visuals(n)
                tree.touch()
        return None
    stubs.register_by_name("setEnabled", "(Z)V", _set_enabled)

    def _is_enabled(a):
        n = _node(a[0]) if a else None
        return 1 if (n is None or n.props.get("enabled", True)) else 0
    stubs.register_by_name("isEnabled", "()Z", _is_enabled)

    def _set_progress(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                try:
                    n.props["progress"] = int(a[1])
                except Exception:
                    pass
                tree.touch()
        return None
    stubs.register_by_name("setProgress", "(I)V", _set_progress)
    stubs.register_by_name("setProgress", "(IZ)V", _set_progress)

    def _resolve_drawable_id(res_id):
        dm = getattr(stubs, "_drawmap", None)
        nm = None
        try:
            nm = dm.get(int(res_id)) if dm else None
        except Exception:
            nm = None
        resv = getattr(stubs, "_drawable_resolver", None)
        spec = None
        if resv is not None:
            try:
                spec = resv.resolve(nm if nm is not None else int(res_id))
            except Exception:
                spec = None
        return nm, spec

    def _set_image_res(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                n.props.pop("src_null", None)
                nm, spec = _resolve_drawable_id(a[1])
                n.props["img"] = nm if nm is not None else ("img#%s" % a[1])
                if spec is not None:
                    n.props["drawable"] = spec
                tree.touch()
        return None
    stubs.register_by_name("setImageResource", "(I)V", _set_image_res)

    def _set_bg_res(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                _nm, spec = _resolve_drawable_id(a[1])
                if spec is not None:
                    n.props["bg_drawable"] = spec
                    tree.touch()
        return None
    stubs.register_by_name("setBackgroundResource", "(I)V", _set_bg_res)

    def _set_image_bitmap(a):
        if a and len(a) > 1 and isinstance(a[1], JObject):
            n = _node(a[0])
            bmp = a[1]
            if n is not None:
                n.props.pop("src_null", None)
                raw = bmp.fields.get("_bytes")
                if raw:
                    n.props["img_bytes"] = bytes(raw)
                elif bmp.fields.get("_drawable") is not None:
                    n.props["drawable"] = bmp.fields["_drawable"]
                elif bmp.fields.get("_path"):
                    n.props["drawable"] = {"kind": "raster", "path": bmp.fields["_path"], "name": bmp.fields.get("_name", "")}
                tree.touch()
        return None
    stubs.register_by_name("setImageBitmap", "(Landroid/graphics/Bitmap;)V", _set_image_bitmap)

    def _set_image_drawable(a):
        if a and len(a) > 1 and isinstance(a[1], JObject):
            n = _node(a[0])
            drw = a[1]
            if n is not None:
                n.props.pop("src_null", None)
                if drw.fields.get("_bytes"):
                    n.props["img_bytes"] = bytes(drw.fields["_bytes"])
                elif drw.fields.get("_drawable") is not None:
                    n.props["drawable"] = drw.fields["_drawable"]
                tree.touch()
        return None
    stubs.register_by_name("setImageDrawable", "(Landroid/graphics/drawable/Drawable;)V", _set_image_drawable)

    def _jarr_bytes(arr):
        from core.java.objects import JArray
        if isinstance(arr, JArray):
            return bytes(int(x) & 0xFF for x in arr.data)
        return b""

    def _bf_dec_res(a):
        rid = a[1] if len(a) > 1 else 0
        nm, spec = _resolve_drawable_id(rid)
        fields = {"_name": nm or f"res#{rid}"}
        if spec is not None:
            fields["_drawable"] = spec
            if spec.get("kind") == "raster" and spec.get("path"):
                fields["_path"] = spec["path"]
        return JObject("Landroid/graphics/Bitmap;", fields)

    def _bf_dec_bytes(a):
        raw = _jarr_bytes(a[0] if a else None)
        off = int(a[1]) if len(a) > 1 else 0
        ln = int(a[2]) if len(a) > 2 else len(raw)
        return JObject("Landroid/graphics/Bitmap;", {"_bytes": raw[off:off + ln]})

    def _bf_dec_file(a):
        p = stubs._txt(a[0]) if a else ""
        fields = {"_path": p}
        eng = getattr(stubs, "_vfs_eng", None)
        if eng is not None:
            from core.java.fusion_loader import NativeFusionLoader
            raw = NativeFusionLoader.vfs_read(eng, p or "")
            if raw:
                fields["_bytes"] = raw
        return JObject("Landroid/graphics/Bitmap;", fields)

    def _bf_dec_stream(a):
        st = a[0] if a else None
        raw = b""
        if isinstance(st, JObject):
            if st.fields.get("buf") is not None:
                raw = bytes(st.fields["buf"])
            elif st.fields.get("_mem") is not None:
                raw = bytes(st.fields["_mem"])
            elif isinstance(st.fields.get("__accel_stream"), dict):
                raw = bytes(st.fields["__accel_stream"].get("data", b""))
        return JObject("Landroid/graphics/Bitmap;", {"_bytes": raw})

    stubs.register("Landroid/graphics/BitmapFactory;", "decodeResource",
                   "(Landroid/content/res/Resources;I)Landroid/graphics/Bitmap;", _bf_dec_res)
    stubs.register("Landroid/graphics/BitmapFactory;", "decodeResource",
                   "(Landroid/content/res/Resources;ILandroid/graphics/BitmapFactory$Options;)Landroid/graphics/Bitmap;", _bf_dec_res)
    stubs.register("Landroid/graphics/BitmapFactory;", "decodeByteArray",
                   "([BII)Landroid/graphics/Bitmap;", _bf_dec_bytes)
    stubs.register("Landroid/graphics/BitmapFactory;", "decodeFile",
                   "(Ljava/lang/String;)Landroid/graphics/Bitmap;", _bf_dec_file)
    stubs.register("Landroid/graphics/BitmapFactory;", "decodeStream",
                   "(Ljava/io/InputStream;)Landroid/graphics/Bitmap;", _bf_dec_stream)

    # --- RadioButton / RadioGroup: Single-Select innerhalb der Gruppe ---
    def _init_radio(a):
        n = _node(a[0], "Landroid/widget/RadioButton;") if a else None
        if n is not None:
            n.clickable = True
            def _sel(view=None, _n=n):
                grp = getattr(_n, "_parent", None)
                while grp is not None and grp.cls != "RadioGroup":
                    grp = getattr(grp, "_parent", None)
                if grp is not None:
                    for other in grp.walk():
                        if other.cls == "RadioButton":
                            other.props["checked"] = (other is _n)
                else:
                    _n.props["checked"] = True
                tree.touch()
            n.on_click = _sel
        return a[0] if a else None
    for _sig in ("(Landroid/content/Context;)V", "()V"):
        stubs.register("Landroid/widget/RadioButton;", "<init>", _sig, _init_radio)
        stubs.register("Landroid/widget/RadioGroup;", "<init>", _sig, _init("Landroid/widget/RadioGroup;"))

    def _widget_jcls(node):
        full = node.props.get("full_cls") if node is not None else None
        if full:
            return "L" + str(full).replace(".", "/") + ";"
        short = getattr(node, "cls", "View") if node is not None else "View"
        _wmap = {
            "View": "Landroid/view/View;",
            "ViewGroup": "Landroid/view/ViewGroup;",
            "FrameLayout": "Landroid/widget/FrameLayout;",
            "LinearLayout": "Landroid/widget/LinearLayout;",
            "RelativeLayout": "Landroid/widget/RelativeLayout;",
            "ScrollView": "Landroid/widget/ScrollView;",
            "WebView": "Landroid/webkit/WebView;",
            "ViewPager2": "Landroidx/viewpager2/widget/ViewPager2;",
            "RecyclerView": "Landroidx/recyclerview/widget/RecyclerView;",
            "TabLayout": "Lcom/google/android/material/tabs/TabLayout;",
            "ConstraintLayout": "Landroidx/constraintlayout/widget/ConstraintLayout;",
        }
        if short in _wmap:
            return _wmap[short]
        return "Landroid/widget/%s;" % short

    # --- XML-Layout-Inflation (setContentView(resId) / LayoutInflater.inflate) ---
    _set_content_res = None
    _find_view = None
    _inflate_fn = None
    stubs.register_by_name(
        "cloneInContext", "(Landroid/content/Context;)Landroid/view/LayoutInflater;",
        lambda a: a[0] if (a and isinstance(a[0], JObject)) else JObject("Landroid/view/LayoutInflater;", {}),
    )
    stubs.register_by_name(
        "getContext", "()Landroid/content/Context;",
        lambda a: getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None),
    )
    if app_root:
        from core.java.layout_inflater import (
            build_resmap, inflate_res, build_id_map, build_string_map,
            build_drawable_map, build_array_map, build_pref_style_map,
        )
        _idmap = build_resmap(app_root)
        _idmap_id = build_id_map(app_root)
        _idmap_id_ref.update(_idmap_id)
        for _rid, _rnm in _idmap_id.items():
            _rev_idmap[_rnm] = _rid
        _strmap_id, _name2str = build_string_map(app_root)
        _arr_idmap_tmp, _ = build_array_map(app_root, name2text=_name2str)
        _arr_idmap.update(_arr_idmap_tmp)
        stubs._drawmap = build_drawable_map(app_root)
        _pref_styles = build_pref_style_map(app_root)

        def _resolve_styled_attrs(a, vm_cur):
            def_attr = int(a[3]) if (len(a) > 3 and isinstance(a[3], int)) else 0
            by_attr = _pref_styles.get("by_attr") or {}
            by_lname = _pref_styles.get("by_layout_name") or {}
            info = dict(by_attr.get(def_attr) or {})
            if "widgetLayout" not in info and vm_cur is not None:
                for fr in reversed(getattr(vm_cur, "_frames", ()) or ()):
                    fcls = getattr(getattr(fr, "md", None), "cls", "") or ""
                    if "SwitchPreferenceCompat" in fcls:
                        wid = by_lname.get("preference_widget_switch_compat") or by_lname.get("preference_switch")
                        if wid:
                            info["widgetLayout"] = wid
                        break
                    if "SwitchPreference" in fcls:
                        wid = by_lname.get("preference_widget_switch") or by_lname.get("preference_switch")
                        if wid:
                            info["widgetLayout"] = wid
                        break
                    if "CheckBoxPreference" in fcls:
                        wid = by_lname.get("preference_widget_checkbox")
                        if wid:
                            info["widgetLayout"] = wid
                        break
            out = {}
            if info.get("layout"):
                out[3] = info["layout"]
                out[27] = info["layout"]
            if info.get("widgetLayout"):
                out[9] = info["widgetLayout"]
                out[35] = info["widgetLayout"]
            return out

        stubs._styled_attrs_resolver = _resolve_styled_attrs
        try:
            from core.java.drawable_loader import get_drawable_resolver
            _resv = get_drawable_resolver(app_root)
            stubs._drawable_resolver = _resv
            tree._drawable_resolver = _resv
            if _resv is not None and hasattr(_resv, "resolve_window_background"):
                wbg, wdark = _resv.resolve_window_background()
                if wbg:
                    tree.window_bg = wbg
                    tree.window_bg_dark = bool(wdark)
        except Exception:
            pass

        def _set_content_res(a):
            if a and len(a) > 1:
                n = inflate_res(app_root, a[1], _idmap)
                if n is not None:
                    tree._compose_recompose = None
                    if tree.window_bg is not None and not tree.window_bg_dark:
                        tree.dark_mode = False
                    _init_custom_views(n)
                    _mount_fragments(n)
                    tree.set_root(n)
            return None
        stubs.register_by_name("setContentView", "(I)V", _set_content_res)

        def _inflate(a):
            if len(a) > 1:
                n = inflate_res(app_root, a[1], _idmap)
                if n is not None:
                    _init_custom_views(n)
                    _mount_fragments(n)
                    if len(a) > 2 and isinstance(a[2], JObject) and "$view" in a[2].fields:
                        attach = True if len(a) == 3 else bool(int(a[3] or 0))
                        if attach:
                            parent_node = a[2].fields["$view"]
                            if n.cls == "merge":
                                for _mc in list(n.children):
                                    if _mc not in parent_node.children:
                                        parent_node.add(_mc)
                            elif n not in parent_node.children:
                                parent_node.add(n)
                            tree.touch()
                            return a[2]
                    return _wrap_node(n)
            return None
        _inflate_fn = _inflate
        for _sig in ("(ILandroid/view/ViewGroup;)Landroid/view/View;",
                     "(ILandroid/view/ViewGroup;Z)Landroid/view/View;"):
            stubs.register_by_name("inflate", _sig, _inflate)

        def _find_view(a):
            if len(a) > 1:
                try:
                    name = _idmap_id.get(int(a[1]))
                except Exception:
                    name = None
                if name:
                    root_search = None
                    if a and isinstance(a[0], JObject) and "$view" in a[0].fields:
                        root_search = a[0].fields["$view"]
                    elif tree.root is not None:
                        root_search = tree.root
                    node = root_search.find(name) if root_search is not None else None
                    if node is not None:
                        return _wrap_node(node)
            return 0
        stubs.register_by_name("findViewById", "(I)Landroid/view/View;", _find_view)
        stubs._strmap_id = _strmap_id

        def _get_string(a):
            if len(a) > 1:
                try:
                    return JString(_strmap_id.get(int(a[1]), ""))
                except Exception:
                    return JString("")
            return JString("")
        def _get_string_fmt(a):
            if len(a) > 1:
                try:
                    tmpl = _strmap_id.get(int(a[1]), "")
                except Exception:
                    tmpl = ""
                arr = a[2] if len(a) > 2 else None
                fargs = getattr(arr, "data", None) or []
                for idx, fv in enumerate(fargs, 1):
                    if isinstance(fv, JObject) and "value" in (fv.fields or {}):
                        sval = str(fv.fields["value"])
                    else:
                        sval = _text(fv)
                    tmpl = tmpl.replace(f"%{idx}$s", sval).replace(f"%{idx}$d", sval)
                for fv in fargs:
                    sval = str(fv.fields["value"]) if isinstance(fv, JObject) and "value" in (fv.fields or {}) else _text(fv)
                    if "%s" in tmpl:
                        tmpl = tmpl.replace("%s", sval, 1)
                    elif "%d" in tmpl:
                        tmpl = tmpl.replace("%d", sval, 1)
                return JString(tmpl)
            return JString("")
        def _get_str_arr(a):
            from core.java.objects import JArray
            rid = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            items = _arr_idmap.get(rid, [])
            return JArray("[Ljava/lang/String;", [JString(x) for x in items])
        def _get_int_arr(a):
            from core.java.objects import JArray
            rid = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            out = []
            for x in _arr_idmap.get(rid, []):
                try:
                    out.append(int(str(x).strip(), 0))
                except Exception:
                    out.append(0)
            return JArray("[I", out)
        stubs.register_by_name("getString", "(I)Ljava/lang/String;", _get_string)
        stubs.register_by_name("getString", "(I[Ljava/lang/Object;)Ljava/lang/String;", _get_string_fmt)
        stubs.register_by_name("getStringArray", "(I)[Ljava/lang/String;", _get_str_arr)
        stubs.register_by_name("getIntArray", "(I)[I", _get_int_arr)
        stubs.register_by_name("obtainTypedArray", "(I)Landroid/content/res/TypedArray;",
                               lambda a: JObject("Landroid/content/res/TypedArray;", {"_rid": a[1] if len(a) > 1 else 0}))
    else:
        _get_string = _get_string_fmt = _get_str_arr = _get_int_arr = None

    # --- Navigation: Intent-Zielklasse + startActivity ---
    def _intent_target(a):
        if a and isinstance(a[0], JObject):
            for x in a[1:]:
                if isinstance(x, JObject) and x.cls == "Ljava/lang/Class;":
                    a[0].fields["$target"] = x.fields.get("name")
                elif isinstance(x, JObject) and x.cls == "Landroid/content/ComponentName;":
                    cn = x.fields.get("cls")
                    if cn:
                        a[0].fields["$target"] = cn
                elif isinstance(x, JObject) and x.cls == "Landroid/net/Uri;":
                    a[0].fields["$uri"] = x.fields.get("$uri") or x.fields.get("uri") or ""
            if len(a) >= 2 and isinstance(a[1], JString):
                s1 = a[1].text
                if len(a) > 2 and isinstance(a[2], JString):
                    a[0].fields["$target"] = a[2].text
                elif "action." in s1 or s1.startswith("android."):
                    a[0].fields["$action"] = s1
        return a[0] if a else None
    stubs.register("Landroid/content/Intent;", "<init>", "(Ljava/lang/String;)V", _intent_target)
    stubs.register("Landroid/content/Intent;", "<init>", "(Ljava/lang/String;Landroid/net/Uri;)V", _intent_target)
    stubs.register("Landroid/content/Intent;", "<init>",
                   "(Landroid/content/Context;Ljava/lang/Class;)V", _intent_target)
    stubs.register_by_name("setAction", "(Ljava/lang/String;)Landroid/content/Intent;", _intent_target)
    stubs.register_by_name("setData", "(Landroid/net/Uri;)Landroid/content/Intent;", _intent_target)
    stubs.register("Landroid/net/Uri;", "parse", "(Ljava/lang/String;)Landroid/net/Uri;",
                   lambda a: JObject("Landroid/net/Uri;", {"$uri": _text(a[0]) if a else ""}))
    stubs.register("Landroid/net/Uri;", "toString", "()Ljava/lang/String;",
                   lambda a: JString(str(a[0].fields.get("$uri", ""))) if a and isinstance(a[0], JObject) else JString(""))
    stubs.register_by_name("setClass",
                           "(Landroid/content/Context;Ljava/lang/Class;)Landroid/content/Intent;",
                           _intent_target)
    stubs.register_by_name("setClassName",
                           "(Landroid/content/Context;Ljava/lang/String;)Landroid/content/Intent;",
                           _intent_target)
    stubs.register_by_name("setClassName",
                           "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/Intent;",
                           _intent_target)
    stubs.register("Landroid/content/ComponentName;", "<init>",
                   "(Landroid/content/Context;Ljava/lang/Class;)V",
                   lambda a: a[0].fields.__setitem__("cls", a[2].fields.get("name") if isinstance(a[2], JObject) else None) if len(a) > 2 and isinstance(a[0], JObject) else None)
    stubs.register("Landroid/content/ComponentName;", "<init>",
                   "(Landroid/content/Context;Ljava/lang/String;)V",
                   lambda a: a[0].fields.__setitem__("cls", _text(a[2])) if len(a) > 2 and isinstance(a[0], JObject) else None)

    def _start_activity(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            uri = str(a[1].fields.get("$uri") or "")
            if not uri:
                d_obj = a[1].fields.get("data") or a[1].fields.get("_uri")
                if isinstance(d_obj, JObject):
                    uri = str(d_obj.fields.get("$uri") or d_obj.fields.get("uri") or "")
            if uri.startswith(("http://", "https://", "file:///android_asset/")):
                _record_web_event("action_view", uri)
            tgt = a[1].fields.get("$target")
            if tgt:
                stubs._pending_nav_target = tgt
                stubs._pending_nav_intent = a[1]
                if on_navigate is not None:
                    try:
                        on_navigate(tgt)
                    except Exception:
                        pass
        return None
    stubs.register_by_name("startActivity", "(Landroid/content/Intent;)V", _start_activity)
    stubs.register_by_name("startActivity", "(Landroid/content/Intent;Landroid/os/Bundle;)V", _start_activity)
    stubs.register_by_name("startActivityForResult", "(Landroid/content/Intent;I)V", _start_activity)
    stubs.register_by_name("startActivityForResult", "(Landroid/content/Intent;ILandroid/os/Bundle;)V", _start_activity)

    # --- ViewPager2 Stubs ---
    def _vp2_materialize_adapter(_vm, recv, adp):
        _vm = _vm or getattr(stubs, "vm", None)
        if _vm is None or not isinstance(recv, JObject) or not isinstance(adp, JObject):
            return
        vp_node = recv.fields.get("$view")
        if vp_node is None:
            vid = recv.fields.get("id") or recv.fields.get("$id")
            if isinstance(vid, int) and vid > 0:
                vp_node = tree.by_id(vid)
            if vp_node is None:
                for n in (tree.root.walk() if tree.root is not None else ()):
                    if n.cls == "ViewPager2":
                        vp_node = n
                        break
            if vp_node is not None:
                recv.fields["$view"] = vp_node
        if vp_node is None:
            return
        pos = int(recv.fields.get("__item", 0) or 0)
        cnt = _safe_frag_call(_vm, adp, "getItemCount", "()I", (adp,))
        if isinstance(cnt, int):
            if cnt <= 0:
                return
            if pos >= cnt:
                pos = max(0, cnt - 1)
                recv.fields["__item"] = pos
        frags = recv.fields.setdefault("__vp2_frags", {})
        frag = frags.get(pos)
        if frag is None:
            frag = _safe_frag_call(
                _vm, adp, "createFragment",
                "(I)Landroidx/fragment/app/Fragment;", (adp, pos),
            )
            if isinstance(frag, JObject):
                frags[pos] = frag
        if isinstance(frag, JObject):
            if not hasattr(stubs, "_fragments_by_tag"):
                stubs._fragments_by_tag = {}
            stubs._fragments_by_tag[f"f{pos}"] = frag
            vp_node.children.clear()
            _mount_fragment_instance(frag, vp_node, tag=f"f{pos}")
            tree.touch()
            return
        vh = _safe_frag_call(
            _vm, adp, "onCreateViewHolder",
            "(Landroid/view/ViewGroup;I)Landroidx/recyclerview/widget/RecyclerView$ViewHolder;",
            (adp, _wrap_node(vp_node), 0),
        )
        if isinstance(vh, JObject):
            _safe_frag_call(
                _vm, adp, "onBindViewHolder",
                "(Landroidx/recyclerview/widget/RecyclerView$ViewHolder;I)V",
                (adp, vh, pos),
            )
            iv = vh.fields.get("itemView")
            vnode = iv.fields.get("$view") if isinstance(iv, JObject) else None
            if vnode is not None:
                vp_node.children.clear()
                vp_node.add(vnode)
                tree.touch()

    def _vp_set_adapter(a):
        if a and isinstance(a[0], JObject):
            recv = a[0]
            adp = a[1] if len(a) > 1 else None
            recv.fields["__adapter"] = adp
            vn = recv.fields.get("$view")
            is_vp2 = ("ViewPager2" in (recv.cls or "")) or (vn is not None and vn.cls == "ViewPager2")
            if is_vp2 and isinstance(adp, JObject):
                _vp2_materialize_adapter(getattr(stubs, "vm", None), recv, adp)
        return None

    def _vp_get_adapter(a):
        return a[0].fields.get("__adapter") if a and isinstance(a[0], JObject) else None

    def _vp_set_item(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            recv = a[0]
            pos = int(a[1] or 0)
            recv.fields["__item"] = pos
            vm_cur = getattr(stubs, "vm", None)
            adp = recv.fields.get("__adapter")
            vn = recv.fields.get("$view")
            is_vp2 = ("ViewPager2" in (recv.cls or "")) or (vn is not None and vn.cls == "ViewPager2")
            if is_vp2 and isinstance(adp, JObject):
                _vp2_materialize_adapter(vm_cur, recv, adp)
            for cb in list(recv.fields.get("__vp_callbacks") or ()):
                if isinstance(cb, JObject) and vm_cur is not None:
                    try:
                        vm_cur.invoke(cb.cls, "onPageSelected", "(I)V", (cb, pos))
                    except Exception:
                        pass
        return None

    def _vp_get_item(a):
        return int(a[0].fields.get("__item", 0)) if a and isinstance(a[0], JObject) else 0

    def _vp_reg_cb(a):
        if a and len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject):
            a[0].fields.setdefault("__vp_callbacks", []).append(a[1])
        return None

    stubs.register_by_name("setCurrentItem", "(I)V", _vp_set_item)
    stubs.register_by_name("setCurrentItem", "(IZ)V", _vp_set_item)
    stubs.register_by_name("getCurrentItem", "()I", _vp_get_item)
    stubs.register_by_name("registerOnPageChangeCallback",
                           "(Landroidx/viewpager2/widget/ViewPager2$OnPageChangeCallback;)V", _vp_reg_cb)

    def _rv_materialize_adapter(_vm, recv, adp):
        rv_node = _node(recv, "RecyclerView")
        if rv_node is None:
            return
        if isinstance(recv, JObject):
            sc = int(recv.fields.get("__span_count", 1) or 1)
            if sc >= 2:
                rv_node.props["span_count"] = sc
            else:
                rv_node.props.pop("span_count", None)
        if isinstance(adp, JObject):
            adp.fields["__rv_recv"] = recv
        rv_node.children.clear()
        if not (isinstance(adp, JObject) and _vm is not None and hasattr(_vm, "invoke_virtual")):
            tree.touch()
            return
        old_b = getattr(_vm, "step_budget", None)
        cur_s = getattr(_vm, "steps", 0)
        _vm.step_budget = (cur_s + 500_000) if old_b is None else max(old_b, cur_s + 500_000)
        try:
            try:
                cnt = int(_vm.invoke_virtual(adp, "getItemCount", "()I", (adp,)) or 0)
            except Exception:
                cnt = 0
            create_sig = None
            bind_sig = None
            cur = adp.cls
            seen_c = set()
            while cur and cur not in seen_c and (not create_sig or not bind_sig):
                seen_c.add(cur)
                if hasattr(_vm, "_ensure_loaded"):
                    _vm._ensure_loaded(cur)
                for (mc, mn, ms) in list(getattr(_vm, "registry", {}).keys()):
                    if mc == cur:
                        if mn == "onCreateViewHolder" and ms.startswith("(Landroid/view/ViewGroup;I)L") and not create_sig:
                            create_sig = ms
                        elif mn == "onBindViewHolder" and ms.startswith("(L") and ms.endswith(";I)V") and not bind_sig:
                            bind_sig = ms
                cur = _vm._superclass(cur) if hasattr(_vm, "_superclass") else None
            if create_sig and bind_sig and cnt > 0:
                for i in range(min(cnt, 30)):
                    try:
                        vt = int(_vm.invoke_virtual(adp, "getItemViewType", "(I)I", (adp, i)) or 0)
                    except Exception:
                        vt = 0
                    try:
                        vh = _vm.invoke_virtual(adp, "onCreateViewHolder", create_sig, (adp, recv, vt))
                        if not isinstance(vh, JObject):
                            continue
                        _vm.invoke_virtual(adp, "onBindViewHolder", bind_sig, (adp, vh, i))
                        iv = vh.fields.get("itemView")
                        if not (isinstance(iv, JObject) and "$view" in iv.fields):
                            iv = next((fv for fv in vh.fields.values() if isinstance(fv, JObject) and "$view" in fv.fields), None)
                        if isinstance(iv, JObject) and "$view" in iv.fields:
                            item_n = iv.fields["$view"]
                            if item_n is not None and item_n not in rv_node.children:
                                rv_node.add(item_n)
                    except Exception:
                        break
            tree.touch()
        finally:
            _vm.step_budget = old_b

    def _vp1_materialize_adapter(_vm, recv, adp):
        vp_node = _node(recv, "FrameLayout")
        if vp_node is None:
            return
        vp_node.children.clear()
        if not (isinstance(adp, JObject) and _vm is not None and hasattr(_vm, "invoke_virtual")):
            tree.touch()
            return
        try:
            try:
                cnt = int(_vm.invoke_virtual(adp, "getCount", "()I", (adp,)) or 0)
            except Exception:
                cnt = 0
            if cnt > 0:
                try:
                    pos = int(recv.fields.get("__item", 0) or 0)
                    if not (0 <= pos < cnt):
                        pos = 0
                    p0_view = None
                    if _vm._resolve_method(adp.cls, adp.cls, "getItem", "(I)Landroidx/fragment/app/Fragment;") is not None:
                        item = _vm.invoke_virtual(adp, "getItem", "(I)Landroidx/fragment/app/Fragment;", (adp, pos))
                        if isinstance(item, JObject):
                            _mount_fragment_instance(item, vp_node)
                            p0_view = item.fields.get("_view")
                    elif _vm._resolve_method(adp.cls, adp.cls, "instantiateItem", "(Landroid/view/ViewGroup;I)Ljava/lang/Object;") is not None:
                        inst = _vm.invoke_virtual(adp, "instantiateItem", "(Landroid/view/ViewGroup;I)Ljava/lang/Object;", (adp, recv, pos))
                        if isinstance(inst, JObject):
                            p0_view = inst
                            vn_inst = inst.fields.get("$view")
                            if isinstance(vn_inst, ViewNode) and vn_inst not in vp_node.children:
                                vp_node.add(vn_inst)
                    if p0_view is not None:
                        recv.fields["__page0_view"] = p0_view
                        pt = recv.fields.get("__page_transformer")
                        if isinstance(pt, JObject) and isinstance(p0_view, JObject):
                            _safe_frag_call(_vm, pt, "transformPage", "(Landroid/view/View;F)V", (pt, p0_view, 0))
                except Exception:
                    pass
            tree.touch()
        except Exception:
            pass

    def _apply_cs_to_layout(cs_obj, cl_obj):
        if not isinstance(cs_obj, JObject):
            return
        root_n = cl_obj.fields.get("$view") if (isinstance(cl_obj, JObject) and "$view" in cl_obj.fields) else tree.root
        if root_n is None:
            return
        vis_map = cs_obj.fields.get("$cs_vis") or {}
        alpha_map = cs_obj.fields.get("$cs_alpha") or {}
        for vid_id, vis in vis_map.items():
            vname = _idmap_id_ref.get(vid_id) if isinstance(vid_id, int) else vid_id
            tgt = root_n.find(vname) if vname else None
            if tgt is not None:
                tgt.visible = (int(vis) == 0)
        for vid_id, al in alpha_map.items():
            vname = _idmap_id_ref.get(vid_id) if isinstance(vid_id, int) else vid_id
            tgt = root_n.find(vname) if vname else None
            if tgt is not None:
                tgt.props["alpha"] = float(al)
        tree.touch()

    def _ensure_view_vm_hook(vm_obj):
        if vm_obj is None or not hasattr(vm_obj, "_method_hooks"):
            return
        if getattr(vm_obj, "_view_hooks_attached", False):
            return
        def _extract_color_int(arg):
            if isinstance(arg, int):
                ci = int(arg) & 0xFFFFFFFF
                if ((ci >> 24) & 0xFF) > 10 or (ci != 0 and ci <= 0xFFFFFF):
                    return ci, f"#{ci & 0xFFFFFF:06x}"
                return ci, None
            if isinstance(arg, JObject):
                raw_c = arg.fields.get("color", arg.fields.get("$color", arg.fields.get("$default", arg.fields.get("mDefaultColor"))))
                if raw_c is not None:
                    return _extract_color_int(int(raw_c))
            return None, None

        def _apply_drawable_tint(drv_obj, color_arg):
            if not isinstance(drv_obj, JObject):
                return
            _, hex_col = _extract_color_int(color_arg)
            if not hex_col:
                return
            spec = drv_obj.fields.get("_drawable")
            if "_orig_drawable" not in drv_obj.fields:
                drv_obj.fields["_orig_drawable"] = dict(spec) if isinstance(spec, dict) else None
            spec = dict(spec) if isinstance(spec, dict) else {"kind": "shape", "shape": "rectangle", "radius": 8}
            if spec.get("kind") in ("shape", "color"):
                spec["fill"] = hex_col
                spec["outline"] = hex_col
            else:
                spec["tint"] = hex_col
            drv_obj.fields["_drawable"] = spec
            owner = drv_obj.fields.get("_owner_view")
            if owner is not None:
                owner.props["bg_drawable"] = dict(spec)
                tree.touch()

        def _rv_set_layout_manager(rv_obj, rv_vn, logical_args):
            lm = logical_args[1] if len(logical_args) > 1 else None
            sc = int(lm.fields.get("__span_count", 1) or 1) if isinstance(lm, JObject) else 1
            rv_obj.fields["__span_count"] = sc
            if rv_vn is not None:
                if sc >= 2:
                    rv_vn.props["span_count"] = sc
                else:
                    rv_vn.props.pop("span_count", None)
                tree.touch()

        def _apply_bg_tint_csl(vn_tgt, csl_obj):
            if vn_tgt is None or not isinstance(csl_obj, JObject):
                return
            col = csl_obj.fields.get("$color", csl_obj.fields.get("$default", csl_obj.fields.get("mDefaultColor", 0)))
            try:
                ci = int(col or 0) & 0xFFFFFFFF
            except Exception:
                ci = 0
            a_byte = (ci >> 24) & 0xFF
            if a_byte > 10 or (ci != 0 and ci <= 0xFFFFFF):
                vn_tgt.props["bg_tint"] = f"#{ci & 0xFFFFFF:06x}"
                vn_tgt.props["bg_tint_alpha"] = (a_byte / 255.0) if (ci > 0xFFFFFF or a_byte > 0) else 1.0
                tree.touch()

        def _vhook(rcls, cls, name, sig, logical, static, _vm):
            if static and cls == "Landroidx/transition/TransitionManager;" and name == "beginDelayedTransition":
                return True, None
            if static and cls == "Landroidx/security/crypto/EncryptedSharedPreferences;" and name == "create":
                ctx = next(
                    (a for a in logical if isinstance(a, JObject) and ("Context" in a.cls or "Activity" in a.cls or "Application" in a.cls)),
                    None,
                )
                if ctx is None:
                    ctx = getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)
                ptypes = _sig_param_types(sig)
                fname_arg = logical[1] if (ptypes and ptypes[0] == "Landroid/content/Context;" and len(logical) > 1) else (logical[0] if logical else None)
                fname_str = _text(fname_arg) or "encrypted_prefs"
                _, sp_obj = stubs.resolve(
                    "Landroid/content/Context;",
                    "getSharedPreferences",
                    "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
                    [ctx, JString(fname_str), 0],
                    False,
                )
                return True, sp_obj
            if static and name == "getColor" and sig.endswith(")I") and cls in (
                "Landroidx/core/content/ContextCompat;",
                "Landroidx/core/content/res/ResourcesCompat;",
            ) and len(logical) > 1 and isinstance(logical[1], int):
                resv_c = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                col_i = resv_c.resolve_color_int(int(logical[1]), default=0xFF2F6DF0) if resv_c is not None else 0xFF2F6DF0
                return True, (0xFF2F6DF0 if col_i is None else col_i)
            if static and cls == "Landroidx/core/graphics/drawable/DrawableCompat;" and name in ("setTint", "setTintList") and len(logical) > 1:
                _apply_drawable_tint(logical[0], logical[1])
                return True, None
            if static and cls == "Landroidx/core/view/ViewCompat;" and name == "setBackgroundTintList" and len(logical) > 1:
                vnode = _node(logical[0])
                _apply_bg_tint_csl(vnode, logical[1])
                return True, None
            if static and sig.endswith(")Landroid/content/res/ColorStateList;") and (
                cls in (
                    "Landroidx/core/content/res/ColorStateListInflaterCompat;",
                    "Landroidx/core/content/res/ResourcesCompat;",
                    "Landroidx/appcompat/content/res/AppCompatResources;",
                    "Landroidx/core/content/ContextCompat;",
                )
                or name in ("createFromXml", "createFromXmlInner", "inflate", "getColorStateList")
            ):
                return True, JObject(
                    "Landroid/content/res/ColorStateList;",
                    {"$color": 0xFF2196F3, "$default": 0xFF2196F3, "mDefaultColor": 0xFF2196F3},
                )
            if static or not logical or not isinstance(logical[0], JObject):
                return False, None
            recv = logical[0]
            if "GridLayoutManager" in (cls or "") or "GridLayoutManager" in (recv.cls or ""):
                if name == "<init>":
                    if len(logical) >= 3 and isinstance(logical[2], int):
                        recv.fields["__span_count"] = max(1, int(logical[2]))
                    return True, None
                if name == "setSpanCount" and len(logical) > 1:
                    recv.fields["__span_count"] = max(1, int(logical[1] or 1))
                    return True, None
                if name == "getSpanCount":
                    return True, int(recv.fields.get("__span_count", 1) or 1)
            if name == "getColor" and sig in ("(I)I", "(ILandroid/content/res/Resources$Theme;)I") and recv.cls != "Landroid/content/res/TypedArray;" and len(logical) > 1 and isinstance(logical[1], int):
                resv_c = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                col_i = resv_c.resolve_color_int(int(logical[1]), default=0xFF2F6DF0) if resv_c is not None else 0xFF2F6DF0
                return True, (0xFF2F6DF0 if col_i is None else col_i)
            # Activity/AppCompatActivity Methoden nicht von gebundeltem AndroidX verschlucken lassen
            _is_act_recv = (
                not recv.fields.get("$is_fragment")
                and (
                    recv is getattr(stubs, "_current_activity", None)
                    or "Activity" in (recv.cls or "")
                    or "Activity" in (cls or "")
                )
            )
            if name == "setContentView" and _is_act_recv:
                if sig == "(I)V" and _set_content_res is not None:
                    return True, _set_content_res(logical)
                if sig in ("(Landroid/view/View;)V", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V"):
                    return True, _set_content(logical)
            elif name == "findViewById" and sig == "(I)Landroid/view/View;" and _find_view is not None:
                return True, _find_view(logical)
            elif name in ("startActivity", "startActivityForResult") and sig.startswith("(Landroid/content/Intent;"):
                return True, _start_activity(logical)
            elif name == "runOnUiThread" and sig == "(Ljava/lang/Runnable;)V":
                if hasattr(stubs, "_queue_runnable"):
                    stubs._queue_runnable(logical[1] if len(logical) > 1 else None, 0)
                return True, None
            elif name == "getLayoutInflater" and sig == "()Landroid/view/LayoutInflater;":
                return True, JObject("Landroid/view/LayoutInflater;", {})
            elif name == "cloneInContext" and sig == "(Landroid/content/Context;)Landroid/view/LayoutInflater;":
                return True, recv
            elif name == "inflate" and sig in ("(ILandroid/view/ViewGroup;)Landroid/view/View;",
                                               "(ILandroid/view/ViewGroup;Z)Landroid/view/View;") and _inflate_fn is not None:
                return True, _inflate_fn(logical)
            elif name == "getDrawable" and sig in (
                "(I)Landroid/graphics/drawable/Drawable;",
                "(ILandroid/content/res/Resources$Theme;)Landroid/graphics/drawable/Drawable;",
            ) and recv.cls != "Landroid/content/res/TypedArray;":
                rid = int(logical[1] or 0) if len(logical) > 1 else 0
                nm, spec = _resolve_drawable_id(rid)
                is_vec = (isinstance(spec, dict) and spec.get("kind") == "vector") or ("vector" in (nm or "").lower())
                dcls = "Landroid/graphics/drawable/VectorDrawable;" if is_vec else "Landroid/graphics/drawable/BitmapDrawable;"
                return True, JObject(dcls, {"res_id": rid, "_drawable": spec})
            elif name == "getStringArray" and sig == "(I)[Ljava/lang/String;" and _get_str_arr is not None:
                return True, _get_str_arr(logical)
            elif name == "getIntArray" and sig == "(I)[I" and _get_int_arr is not None:
                return True, _get_int_arr(logical)
            elif name == "getString" and sig == "(I[Ljava/lang/Object;)Ljava/lang/String;" and _get_string_fmt is not None:
                return True, _get_string_fmt(logical)
            elif name == "getString" and sig == "(I)Ljava/lang/String;" and _get_string is not None and recv.cls != "Landroid/os/Bundle;" and "SharedPreferences" not in recv.cls:
                if _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    return True, _get_string(logical)
            elif name in ("getSupportFragmentManager", "getChildFragmentManager", "getParentFragmentManager", "getFragmentManager"):
                if recv.fields.get("$is_fragment") or _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    hview = recv.fields.get("_view")
                    hnode = hview.fields.get("$view") if isinstance(hview, JObject) else None
                    return True, JObject("Landroidx/fragment/app/FragmentManager;", {"$synth_fm": True, "_host_node": hnode})
                return False, None
            elif (recv.cls == "Landroidx/fragment/app/FragmentManager;" and recv.fields.get("$synth_fm")) or recv.fields.get("$synth_fm"):
                if name == "beginTransaction":
                    return True, JObject("Landroidx/fragment/app/FragmentTransaction;", {"$fm": recv, "$ops": []})
                if name == "executePendingTransactions":
                    return True, 1 if _flush_fragment_transactions() else 0
                if name == "findFragmentByTag" and len(logical) > 1:
                    t_arg = logical[1]
                    t_s = (
                        t_arg if isinstance(t_arg, str)
                        else (str(t_arg.text) if hasattr(t_arg, "text")
                              else (str(t_arg.fields.get("$str", "")) if isinstance(t_arg, JObject) else ""))
                    )
                    return True, getattr(stubs, "_fragments_by_tag", {}).get(t_s)
                if name == "getFragments":
                    seen = []
                    for dct in (getattr(stubs, "_fragments_by_tag", {}), getattr(stubs, "_fragments_by_id", {})):
                        for fv in dct.values():
                            if isinstance(fv, JObject) and fv not in seen:
                                seen.append(fv)
                    return True, JObject("Ljava/util/ArrayList;", {"$items": seen})
                if name == "findFragmentById" and len(logical) > 1:
                    fid = logical[1]
                    fmap = getattr(stubs, "_fragments_by_id", {})
                    frag = fmap.get(fid) or (fmap.get(_idmap_id_ref[fid]) if fid in _idmap_id_ref else None)
                    return True, frag
            elif recv.cls == "Landroidx/fragment/app/FragmentTransaction;" and "$ops" in recv.fields:
                if name in ("replace", "add") and len(logical) > 2:
                    recv.fields["$ops"].append((name, logical[1], logical[2]))
                    return True, recv
                if name in ("show", "hide") and len(logical) > 1:
                    recv.fields["$ops"].append((name, logical[1]))
                    return True, recv
                if name in ("remove", "addToBackStack", "setReorderingAllowed", "setCustomAnimations", "setTransition"):
                    return True, recv
                if name in ("commit", "commitAllowingStateLoss"):
                    stubs._pending_fragment_txs.append({"fm": recv.fields.get("$fm"), "ops": list(recv.fields.get("$ops", []))})
                    return True, 1
                if name in ("commitNow", "commitNowAllowingStateLoss"):
                    stubs._pending_fragment_txs.append({"fm": recv.fields.get("$fm"), "ops": list(recv.fields.get("$ops", []))})
                    _flush_fragment_transactions()
                    return True, None
            elif name == "findFragmentById" and sig == "(I)Landroidx/fragment/app/Fragment;" and len(logical) > 1:
                try:
                    fid = int(logical[1])
                except Exception:
                    fid = None
                fmap = getattr(stubs, "_fragments_by_id", {})
                frag = fmap.get(fid)
                if frag is None and fid in _idmap_id_ref:
                    frag = fmap.get(_idmap_id_ref[fid])
                if frag is not None:
                    return True, frag
                if _vm._resolve_method(recv.cls, recv.cls, name, sig) is not None:
                    return False, None
                return True, None
            elif name == "findFragmentByTag" and sig == "(Ljava/lang/String;)Landroidx/fragment/app/Fragment;" and len(logical) > 1:
                t_arg = logical[1]
                t_s = (
                    t_arg if isinstance(t_arg, str)
                    else (str(t_arg.text) if hasattr(t_arg, "text")
                          else (str(t_arg.fields.get("$str", "")) if isinstance(t_arg, JObject) else ""))
                )
                frag = getattr(stubs, "_fragments_by_tag", {}).get(t_s)
                if frag is not None:
                    return True, frag
                return False, None
            elif name == "getFragments" and sig == "()Ljava/util/List;" and "FragmentManager" in (cls or recv.cls or ""):
                seen = []
                for dct in (getattr(stubs, "_fragments_by_tag", {}), getattr(stubs, "_fragments_by_id", {})):
                    for fv in dct.values():
                        if isinstance(fv, JObject) and fv not in seen:
                            seen.append(fv)
                if seen:
                    return True, JObject("Ljava/util/ArrayList;", {"$items": seen})
                return False, None
            elif recv.fields.get("$is_fragment"):
                if cls == "Landroidx/fragment/app/Fragment;" and name in (
                    "<init>", "onCreate", "onViewCreated", "onStart", "onResume", "onAttach"
                ):
                    return True, None
                if name in ("getActivity", "requireActivity", "getContext", "requireContext", "getHost"):
                    return True, (recv.fields.get("_host_act") or getattr(stubs, "_current_activity", None))
                if name in ("getLayoutInflater", "onGetLayoutInflater"):
                    return True, JObject("Landroid/view/LayoutInflater;", {})
                if name in ("getView", "requireView"):
                    return True, recv.fields.get("_view")
                if name in ("getArguments", "requireArguments"):
                    return True, stubs.make_bundle()
                if name == "isAdded":
                    return True, 1
                if name in ("isDetached", "isRemoving"):
                    return True, 0
            elif cls in ("Landroidx/constraintlayout/widget/ConstraintSet;", "Landroidx/transition/AutoTransition;", "Landroidx/transition/TransitionSet;"):
                if cls == "Landroidx/constraintlayout/widget/ConstraintSet;":
                    if name == "<init>":
                        recv.fields.setdefault("$cs_vis", {})
                        recv.fields.setdefault("$cs_alpha", {})
                        return True, recv
                    if name == "clone" and len(logical) > 1:
                        rn = logical[1].fields.get("$view") if isinstance(logical[1], JObject) else None
                        if rn is not None:
                            vis_m = recv.fields.setdefault("$cs_vis", {})
                            for nd in rn.walk():
                                if nd.vid and nd.vid in _rev_idmap:
                                    vis_m[_rev_idmap[nd.vid]] = 0 if nd.visible else 4
                        return True, None
                    if name == "setVisibility" and len(logical) > 2:
                        recv.fields.setdefault("$cs_vis", {})[int(logical[1])] = int(logical[2])
                        return True, None
                    if name == "setAlpha" and len(logical) > 2:
                        recv.fields.setdefault("$cs_alpha", {})[int(logical[1])] = _f32_val(logical[2], 1.0)
                        return True, None
                    if name == "applyTo":
                        _apply_cs_to_layout(recv, logical[1] if len(logical) > 1 else None)
                        return True, None
                    if name in ("clear", "connect", "constrainHeight", "constrainWidth", "setMargin"):
                        return True, None
                else:
                    return True, recv if name in ("<init>", "setOrdering", "setDuration", "addTransition") else None
            elif "$view" in recv.fields:
                vn = recv.fields["$view"]
                if "ViewPager" in (recv.cls or "") and "ViewPager2" not in (recv.cls or ""):
                    if name == "setAdapter" and len(logical) > 1:
                        recv.fields["__adapter"] = logical[1]
                        _vp1_materialize_adapter(_vm, recv, logical[1])
                        return True, None
                    if name == "getAdapter":
                        return True, recv.fields.get("__adapter")
                    if name == "setPageTransformer" and len(logical) > 2:
                        recv.fields["__page_transformer"] = logical[2]
                        p0v = recv.fields.get("__page0_view")
                        if isinstance(logical[2], JObject) and isinstance(p0v, JObject):
                            _safe_frag_call(_vm, logical[2], "transformPage", "(Landroid/view/View;F)V", (logical[2], p0v, 0))
                        return True, None
                    if name == "setCurrentItem" and sig in ("(I)V", "(IZ)V"):
                        return True, _vp_set_item(logical)
                    if name == "getCurrentItem" and sig == "()I":
                        return True, _vp_get_item(logical)
                    if name in ("addOnPageChangeListener", "clearOnPageChangeListeners", "setOffscreenPageLimit"):
                        return True, None
                if name == "getContext" and sig == "()Landroid/content/Context;":
                    return True, (getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None))
                if name == "getParent" and sig == "()Landroid/view/ViewParent;":
                    p = vn._parent
                    return True, (_wrap_node(p) if p is not None else None)
                if name == "addView" and sig.startswith("(Landroid/view/View;"):
                    return True, _add_view(logical)
                if name == "removeAllViews" and sig == "()V":
                    vn.children.clear()
                    tree.touch()
                    return True, None
                if (vn.cls == "TextSwitcher" or "TextSwitcher" in (recv.cls or "")) and name in ("setText", "setCurrentText") and len(logical) > 1:
                    txt_s = _text(logical[1])
                    tv_ch = next((c for c in vn.children if c.cls == "TextView"), None)
                    if tv_ch is not None:
                        tv_ch.visible = True
                        tv_ch.text = txt_s
                    else:
                        vn.text = txt_s
                    tree.touch()
                    return True, None
                _vn_st = recv.fields.get("$view")
                _st_leaf = _vn_st is not None and _vn_st.cls in _LEAF
                if name == "setText" and (_st_leaf or _vm._resolve_method(recv.cls, recv.cls, name, sig) is None):
                    if sig in ("(Ljava/lang/CharSequence;)V", "(Ljava/lang/String;)V"):
                        return True, _set_text(logical)
                    if sig == "(I)V":
                        return True, _set_text_res(logical)
                if name in ("getText", "getEditableText"):
                    if sig == "()Landroid/text/Editable;":
                        return True, _get_editable(logical)
                    if sig in ("()Ljava/lang/CharSequence;", "()Ljava/lang/String;"):
                        return True, _get_text(logical)
                if name == "addTextChangedListener" and sig == "(Landroid/text/TextWatcher;)V" and len(logical) > 1:
                    tw = logical[1]
                    _tw_node = recv.fields.get("$view")
                    if _tw_node is not None and isinstance(tw, JObject):
                        lst = _tw_node.props.setdefault("$text_watchers", [])
                        if tw not in lst:
                            lst.append(tw)
                        def _fire_tw(_arg=None, _tw_lst=lst, _recv=recv, _nd=_tw_node):
                            if isinstance(_arg, str) and _nd is not None:
                                _nd.text = _arg
                            ed = _get_editable((_recv,))
                            vm_cur = getattr(stubs, "vm", None) or _vm
                            for w in list(_tw_lst):
                                if isinstance(w, JObject) and vm_cur is not None:
                                    try:
                                        if hasattr(vm_cur, "invoke_virtual"):
                                            vm_cur.invoke_virtual(w, "afterTextChanged", "(Landroid/text/Editable;)V", (w, ed))
                                        else:
                                            vm_cur.invoke(w.cls, "afterTextChanged", "(Landroid/text/Editable;)V", (w, ed))
                                    except Exception:
                                        pass
                            q = getattr(stubs, "pending_runnables", None)
                            if isinstance(q, list) and q and vm_cur is not None:
                                kept = []
                                ran = 0
                                try:
                                    while q and ran < 16:
                                        r = q.pop(0)
                                        if (not isinstance(r, JObject) or r.fields.get("_bg_thread")
                                                or r.fields.get("_defer_until_activity")):
                                            kept.append(r)
                                            continue
                                        ran += 1
                                        try:
                                            if hasattr(vm_cur, "invoke_virtual"):
                                                vm_cur.invoke_virtual(r, "run", "()V", (r,))
                                            else:
                                                vm_cur.invoke(r.cls, "run", "()V", (r,))
                                        except Exception:
                                            pass
                                finally:
                                    if kept:
                                        q[0:0] = kept
                        _tw_node.props["$on_change_fn"] = _fire_tw
                    return True, None
                if name == "setTag" and len(logical) > 1:
                    return True, _set_tag(logical)
                if name == "getTag":
                    return True, _get_tag(logical)
                if name == "setAlpha" and sig == "(F)V":
                    return True, _set_alpha(logical)
                if name == "getAlpha" and sig == "()F":
                    return True, float(vn.props.get("alpha", 1.0))
                if name == "setScaleType" and len(logical) > 1:
                    return True, _set_scale_type(logical)
                if name == "setImageDrawable" and len(logical) > 1:
                    return True, _set_image_drawable(logical)
                if name == "setImageResource" and len(logical) > 1:
                    return True, _set_image_res(logical)
                if name == "setImageBitmap" and len(logical) > 1:
                    return True, _set_image_bitmap(logical)
                if name in ("getWidth", "getHeight"):
                    return True, int(vn.bounds[2 if name == "getWidth" else 3] or (tree.width if name == "getWidth" else 200))
                if name in ("getPaddingLeft", "getPaddingTop", "getPaddingRight", "getPaddingBottom"):
                    return True, 0
                if name in ("setPadding", "setPaddingRelative", "setTranslationX", "setTranslationY"):
                    return True, None
                if name == "requestFocus":
                    return True, 1
                if name == "getWindowToken":
                    return True, JObject("Landroid/os/IBinder;", {})
                if name == "setChecked" and sig == "(Z)V":
                    return True, _set_checked(logical)
                if name == "isChecked" and sig == "()Z":
                    return True, _is_checked(logical)
                if name == "setSelected" and sig == "(Z)V":
                    return True, _set_selected(logical)
                if name == "isSelected" and sig == "()Z":
                    return True, _is_selected(logical)
                if name == "setEnabled" and sig == "(Z)V":
                    return True, _set_enabled(logical)
                if name == "isEnabled" and sig == "()Z":
                    return True, _is_enabled(logical)
                if name == "setVisibility" and sig == "(I)V":
                    return True, _set_vis(logical)
                if name == "setProgress" and sig in ("(I)V", "(IZ)V"):
                    return True, _set_progress(logical)
                if name == "setTextColor" and sig in ("(I)V", "(Landroid/content/res/ColorStateList;)V") and len(logical) > 1:
                    return True, _set_text_color(logical)
                if name == "getBackground" and sig == "()Landroid/graphics/drawable/Drawable;":
                    drv = recv.fields.get("_bg_drv_obj")
                    if not isinstance(drv, JObject):
                        cur_bg = vn.props.get("bg_drawable")
                        orig_bg = dict(cur_bg) if isinstance(cur_bg, dict) else None
                        init_bg = dict(cur_bg) if isinstance(cur_bg, dict) else {
                            "kind": "shape", "shape": "rectangle",
                            "fill": "#f1f5f9", "outline": "#cbd5e1", "radius": 8,
                        }
                        drv = JObject("Landroid/graphics/drawable/GradientDrawable;", {
                            "_owner_view": vn, "_drawable": init_bg, "_orig_drawable": orig_bg,
                        })
                        recv.fields["_bg_drv_obj"] = drv
                    else:
                        drv.fields["_owner_view"] = vn
                    return True, drv
                if name in ("setBackground", "setBackgroundDrawable") and len(logical) > 1:
                    bg_obj = logical[1]
                    if isinstance(bg_obj, JObject):
                        bg_obj.fields["_owner_view"] = vn
                        recv.fields["_bg_drv_obj"] = bg_obj
                        if isinstance(bg_obj.fields.get("_drawable"), dict):
                            vn.props["bg_drawable"] = dict(bg_obj.fields["_drawable"])
                            tree.touch()
                    else:
                        vn.props.pop("bg_drawable", None)
                        recv.fields.pop("_bg_drv_obj", None)
                        tree.touch()
                    return True, None
                if name in ("setBackgroundColor", "setCardBackgroundColor") and len(logical) > 1:
                    _, hex_col = _extract_color_int(logical[1])
                    if hex_col:
                        spec = {
                            "kind": "shape", "shape": "rectangle",
                            "fill": hex_col, "outline": hex_col,
                            "radius": 8 if vn.cls == "CardView" else 4,
                        }
                        vn.props["bg_drawable"] = spec
                        drv_c = recv.fields.get("_bg_drv_obj")
                        if isinstance(drv_c, JObject):
                            drv_c.fields["_drawable"] = dict(spec)
                        tree.touch()
                    return True, None
                if name == "setBackgroundTintList" and sig == "(Landroid/content/res/ColorStateList;)V" and len(logical) > 1:
                    _apply_bg_tint_csl(vn, logical[1])
                    return True, None
                if name == "setOnClickListener" and sig == "(Landroid/view/View$OnClickListener;)V":
                    return True, _set_click(logical)
                if name == "getChildCount" and sig == "()I":
                    return True, len(vn.children)
                if name == "getChildAt" and sig == "(I)Landroid/view/View;" and len(logical) > 1:
                    idx = int(logical[1])
                    chlist = vn.children
                    if 0 <= idx < len(chlist):
                        return True, _wrap_node(chlist[idx])
                    return True, None
                if name == "getLayoutParams" and sig == "()Landroid/view/ViewGroup$LayoutParams;":
                    par_cls = getattr(getattr(vn, "_parent", None), "cls", "") or ""
                    if par_cls == "LinearLayout":
                        lp_cls = "Landroid/widget/LinearLayout$LayoutParams;"
                    elif par_cls == "FrameLayout":
                        lp_cls = "Landroid/widget/FrameLayout$LayoutParams;"
                    elif par_cls == "RelativeLayout":
                        lp_cls = "Landroid/widget/RelativeLayout$LayoutParams;"
                    else:
                        lp_cls = "Landroidx/constraintlayout/widget/ConstraintLayout$LayoutParams;"
                    return True, recv.fields.setdefault("_lp", JObject(lp_cls, {}))
                if name == "setLayoutParams" and sig == "(Landroid/view/ViewGroup$LayoutParams;)V":
                    if len(logical) > 1:
                        recv.fields["_lp"] = logical[1]
                    return True, None
                if "RecyclerView" in (recv.cls or "") or vn.cls == "RecyclerView":
                    if name == "setLayoutManager":
                        _rv_set_layout_manager(recv, vn, logical)
                        return True, None
                    if name in (
                        "setAccessibilityDelegateCompat", "addItemDecoration",
                        "invalidateItemDecorations", "setItemAnimator", "setHasFixedSize",
                        "setClipToPadding", "setNestedScrollingEnabled",
                    ):
                        return True, None
                    if name == "getAdapter":
                        return True, recv.fields.get("__adapter")
                    if name == "setAdapter" and len(logical) > 1:
                        recv.fields["__adapter"] = logical[1]
                        _rv_materialize_adapter(_vm, recv, logical[1])
                        return True, None
            if "Drawable" in (recv.cls or ""):
                if name == "mutate":
                    return True, recv
                if name == "getState":
                    from core.java.objects import JArray
                    return True, JArray("[I", [])
                if name == "setState":
                    return True, 0
                if name in ("getIntrinsicWidth", "getIntrinsicHeight"):
                    return True, 24
                if name in ("setColorFilter", "setTint", "setTintList"):
                    if len(logical) > 1 and logical[1] not in (None, 0):
                        _apply_drawable_tint(recv, logical[1])
                    else:
                        orig = recv.fields.get("_orig_drawable")
                        if isinstance(orig, dict):
                            recv.fields["_drawable"] = dict(orig)
                        else:
                            recv.fields.pop("_drawable", None)
                        owner = recv.fields.get("_owner_view")
                        if owner is not None:
                            if isinstance(orig, dict):
                                owner.props["bg_drawable"] = dict(orig)
                            else:
                                owner.props.pop("bg_drawable", None)
                            tree.touch()
                    return True, None
                if name == "clearColorFilter":
                    orig = recv.fields.get("_orig_drawable")
                    if isinstance(orig, dict):
                        recv.fields["_drawable"] = dict(orig)
                    else:
                        recv.fields.pop("_drawable", None)
                    owner = recv.fields.get("_owner_view")
                    if owner is not None:
                        if isinstance(orig, dict):
                            owner.props["bg_drawable"] = dict(orig)
                        else:
                            owner.props.pop("bg_drawable", None)
                        tree.touch()
                    return True, None
                if name in ("setBounds", "setAlpha", "setCallback"):
                    return True, None
            if "RecyclerView" in (recv.cls or "") or getattr(recv.fields.get("$view"), "cls", "") == "RecyclerView":
                if name == "getContext" and sig == "()Landroid/content/Context;":
                    return True, (getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None))
                if name == "setLayoutManager":
                    _rv_set_layout_manager(recv, recv.fields.get("$view"), logical)
                    return True, None
                if name in (
                    "setAccessibilityDelegateCompat", "addItemDecoration",
                    "invalidateItemDecorations", "setItemAnimator", "setHasFixedSize",
                    "setClipToPadding", "setNestedScrollingEnabled",
                ):
                    return True, None
                if name == "getAdapter":
                    return True, recv.fields.get("__adapter")
                if name == "setAdapter" and len(logical) > 1:
                    recv.fields["__adapter"] = logical[1]
                    _rv_materialize_adapter(_vm, recv, logical[1])
                    return True, None
            if "__rv_recv" in recv.fields and name in (
                "notifyDataSetChanged", "notifyItemChanged", "notifyItemRangeChanged",
                "notifyItemInserted", "notifyItemRemoved",
            ):
                if not recv.fields.get("__rv_rebind_busy"):
                    recv.fields["__rv_rebind_busy"] = True
                    try:
                        _rv_materialize_adapter(_vm, recv.fields["__rv_recv"], recv)
                    finally:
                        recv.fields["__rv_rebind_busy"] = False
                return True, None
            def _as_float(v, default=0.0):
                if isinstance(v, float):
                    return v
                if isinstance(v, int):
                    try:
                        from core.java.dalvik import _f32
                        return float(_f32(v))
                    except Exception:
                        return float(v)
                try:
                    return float(v)
                except Exception:
                    return default

            def _prop_float(props, key, default=0.0):
                if not props or key not in props or props[key] is None:
                    return default
                try:
                    return float(props[key])
                except Exception:
                    return default

            def _sync_slider_node(sl_obj):
                snode = sl_obj.fields.get("$view")
                vfrom = float(sl_obj.fields.get("$from", 0.0))
                vto = float(sl_obj.fields.get("$to", 100.0))
                vstep = float(sl_obj.fields.get("$step", 1.0))
                vval = float(sl_obj.fields.get("$val", vfrom))
                if snode is not None:
                    snode.clickable = True
                    snode.props["slider_from"] = vfrom
                    snode.props["slider_to"] = vto
                    snode.props["slider_step"] = vstep
                    snode.props["slider_val"] = vval
                    span = max(1e-6, vto - vfrom)
                    snode.props["max"] = 100
                    snode.props["progress"] = max(0, min(100, int(round((vval - vfrom) / span * 100.0))))
                    if not callable(snode.on_click):
                        def _slider_step_click(node, _sl=sl_obj):
                            vf = float(_sl.fields.get("$from", 0.0))
                            vt = float(_sl.fields.get("$to", 100.0))
                            vs = float(_sl.fields.get("$step", 0.0))
                            if vs <= 0.0:
                                vs = max(1.0, (vt - vf) / 4.0)
                            nv = float(_sl.fields.get("$val", vf)) + vs
                            if nv > vt + 1e-6:
                                nv = vf
                            _sl.fields["$val"] = nv
                            _sync_slider_node(_sl)
                            _fire_slider_listeners(getattr(stubs, "vm", None) or _vm, _sl, nv, True)
                            tree.touch()
                        snode.on_click = _slider_step_click

            def _fire_slider_listeners(vm_obj, sl_obj, val_f, from_user):
                if sl_obj.fields.get("$in_slider_cb"):
                    return
                sl_obj.fields["$in_slider_cb"] = True
                try:
                    for lst in list(sl_obj.fields.get("$slider_listeners") or ()):
                        if not isinstance(lst, JObject) or vm_obj is None:
                            continue
                        invoked = False
                        for lsig in (
                            "(Ljava/lang/Object;FZ)V",
                            "(Lcom/google/android/material/slider/Slider;FZ)V",
                        ):
                            try:
                                if hasattr(vm_obj, "_resolve_method") and vm_obj._resolve_method(lst.cls, lst.cls, "onValueChange", lsig) is not None:
                                    vm_obj.invoke(lst.cls, "onValueChange", lsig, (lst, sl_obj, float(val_f), 1 if from_user else 0))
                                    invoked = True
                                    break
                            except Exception:
                                pass
                        if not invoked and hasattr(vm_obj, "registry"):
                            if hasattr(vm_obj, "_lazy_class"):
                                try:
                                    vm_obj._lazy_class(lst.cls)
                                except Exception:
                                    pass
                            for (mcls, mname, msig), md in list(vm_obj.registry.items()):
                                if mcls == lst.cls and md.insns and msig in (
                                    "(Ljava/lang/Object;FZ)V",
                                    "(Lcom/google/android/material/slider/Slider;FZ)V",
                                ):
                                    try:
                                        vm_obj.invoke(lst.cls, mname, msig, (lst, sl_obj, float(val_f), 1 if from_user else 0))
                                        break
                                    except Exception:
                                        pass
                finally:
                    sl_obj.fields["$in_slider_cb"] = False

            if name == "<init>" and cls in (
                "Lcom/google/android/material/slider/Slider;",
                "Lcom/google/android/material/slider/BaseSlider;",
            ):
                snode = recv.fields.get("$view")
                props = snode.props if snode is not None else {}
                recv.fields.setdefault("$from", _prop_float(props, "slider_from", 0.0))
                recv.fields.setdefault("$to", _prop_float(props, "slider_to", 100.0))
                recv.fields.setdefault("$step", _prop_float(props, "slider_step", 1.0))
                recv.fields.setdefault("$val", _prop_float(props, "slider_val", recv.fields["$from"]))
                _sync_slider_node(recv)
                return True, None
            if (
                cls in ("Lcom/google/android/material/slider/Slider;", "Lcom/google/android/material/slider/BaseSlider;")
                or "Slider" in (cls or "")
                or "Slider" in (recv.cls or "")
            ):
                snode = recv.fields.get("$view")
                props = snode.props if snode is not None else {}
                if "$from" not in recv.fields:
                    recv.fields["$from"] = _prop_float(props, "slider_from", 0.0)
                    recv.fields["$to"] = _prop_float(props, "slider_to", 100.0)
                    recv.fields["$step"] = _prop_float(props, "slider_step", 1.0)
                    recv.fields["$val"] = _prop_float(props, "slider_val", recv.fields["$from"])
                if name == "getValue" and sig == "()F":
                    return True, float(recv.fields.get("$val", recv.fields.get("$from", 0.0)))
                if name == "setValue" and sig == "(F)V" and len(logical) > 1:
                    old_v = float(recv.fields.get("$val", 0.0))
                    new_v = _as_float(logical[1], old_v)
                    recv.fields["$val"] = new_v
                    _sync_slider_node(recv)
                    if abs(new_v - old_v) > 1e-6:
                        _fire_slider_listeners(_vm, recv, new_v, False)
                    tree.touch()
                    return True, None
                if name == "getValueFrom" and sig == "()F":
                    return True, float(recv.fields.get("$from", 0.0))
                if name == "setValueFrom" and sig == "(F)V" and len(logical) > 1:
                    recv.fields["$from"] = _as_float(logical[1], 0.0)
                    _sync_slider_node(recv)
                    return True, None
                if name == "getValueTo" and sig == "()F":
                    return True, float(recv.fields.get("$to", 100.0))
                if name == "setValueTo" and sig == "(F)V" and len(logical) > 1:
                    recv.fields["$to"] = _as_float(logical[1], 100.0)
                    _sync_slider_node(recv)
                    return True, None
                if name == "getStepSize" and sig == "()F":
                    return True, float(recv.fields.get("$step", 1.0))
                if name == "setStepSize" and sig == "(F)V" and len(logical) > 1:
                    recv.fields["$step"] = _as_float(logical[1], 1.0)
                    _sync_slider_node(recv)
                    return True, None
                if name == "getThumbRadius" and sig == "()I":
                    return True, 16
                if name in (
                    "setThumbRadius", "setThumbRadiusResource",
                    "setThumbTintList", "setThumbStrokeColor", "setThumbStrokeColorResource", "setThumbStrokeWidth", "setThumbStrokeWidthResource",
                    "setTrackActiveTintList", "setTrackInactiveTintList",
                    "setTickActiveTintList", "setTickInactiveTintList", "setTickVisible",
                    "setHaloTintList", "setHaloRadius", "setLabelBehavior", "setTrackHeight",
                ):
                    return True, None
                if name == "addOnChangeListener" and len(logical) > 1:
                    lst = logical[1]
                    if isinstance(lst, JObject):
                        recv.fields.setdefault("$slider_listeners", []).append(lst)
                    _sync_slider_node(recv)
                    return True, None
                if name == "removeOnChangeListener" and len(logical) > 1:
                    lst = logical[1]
                    if lst in recv.fields.get("$slider_listeners", []):
                        recv.fields["$slider_listeners"].remove(lst)
                    return True, None
                if len(logical) == 2 and isinstance(logical[1], JObject) and sig.startswith("(L") and sig.endswith(";)V"):
                    cand = logical[1]
                    if hasattr(_vm, "_lazy_class"):
                        try:
                            _vm._lazy_class(cand.cls)
                        except Exception:
                            pass
                    ifaces = _vm._interfaces(cand.cls) if hasattr(_vm, "_interfaces") else ()
                    if any("OnChangeListener" in str(ifc) for ifc in (ifaces or ())):
                        recv.fields.setdefault("$slider_listeners", []).append(cand)
                        _sync_slider_node(recv)
                        return True, None

            if (
                "DatePicker" in (cls or "")
                or "DatePicker" in (recv.cls or "")
                or getattr(recv.fields.get("$view"), "cls", "") == "DatePicker"
            ):
                dnode = recv.fields.get("$view")
                def _sync_dp():
                    y = int(recv.fields.get("$dp_year", 1995))
                    m = int(recv.fields.get("$dp_month", 0))
                    d = int(recv.fields.get("$dp_day", 1))
                    if dnode is not None:
                        dnode.props["date_y"] = y
                        dnode.props["date_m"] = m
                        dnode.props["date_d"] = d
                        dnode.text = f"{d:02d}.{m + 1:02d}.{y:04d}"
                if name in ("init", "updateDate") and len(logical) >= 4:
                    recv.fields["$dp_year"] = int(logical[1] or 1995)
                    recv.fields["$dp_month"] = int(logical[2] or 0)
                    recv.fields["$dp_day"] = int(logical[3] or 1)
                    if name == "init" and len(logical) > 4 and isinstance(logical[4], JObject):
                        recv.fields["$dp_listener"] = logical[4]
                    _sync_dp()
                    if name == "updateDate":
                        dl = recv.fields.get("$dp_listener")
                        if isinstance(dl, JObject):
                            try:
                                _vm.invoke(dl.cls, "onDateChanged", "(Landroid/widget/DatePicker;III)V",
                                           (dl, recv, recv.fields["$dp_year"], recv.fields["$dp_month"], recv.fields["$dp_day"]))
                            except Exception:
                                pass
                    tree.touch()
                    return True, None
                if name == "getYear" and sig == "()I":
                    return True, int(recv.fields.get("$dp_year", 1995))
                if name == "getMonth" and sig == "()I":
                    return True, int(recv.fields.get("$dp_month", 0))
                if name == "getDayOfMonth" and sig == "()I":
                    return True, int(recv.fields.get("$dp_day", 1))
                if name in ("setMaxDate", "setMinDate", "setSpinnersShown", "setCalendarViewShown"):
                    return True, None

            if "ViewPager2" in (recv.cls or "") or getattr(recv.fields.get("$view"), "cls", "") == "ViewPager2":
                if name == "setAdapter":
                    return True, _vp_set_adapter(logical)
                if name == "getAdapter":
                    return True, _vp_get_adapter(logical)
                if name == "setCurrentItem" and sig in ("(I)V", "(IZ)V"):
                    return True, _vp_set_item(logical)
                if name == "getCurrentItem" and sig == "()I":
                    return True, _vp_get_item(logical)
                if name == "registerOnPageChangeCallback":
                    return True, _vp_reg_cb(logical)
                if name in ("setUserInputEnabled", "setOffscreenPageLimit", "setOrientation", "unregisterOnPageChangeCallback"):
                    return True, None
            # WebView & CustomTabs Interception (G4)
            if cls == "Landroid/webkit/WebView;" or recv.cls == "Landroid/webkit/WebView;" or getattr(recv.fields.get("$view"), "cls", "") == "WebView":
                from core.java.webview_bridge import handle_webview_vhook
                wv_ok, wv_val = handle_webview_vhook(
                    stubs, _vm, tree, recv, cls, name, sig, logical,
                    _text, _record_web_event, ViewNode, app_root=app_root,
                )
                if wv_ok:
                    return True, wv_val
            if cls == "Landroid/webkit/WebSettings;" or recv.cls == "Landroid/webkit/WebSettings;":
                if name == "getUserAgentString":
                    return True, JString(str(recv.fields.get("$ua", "Mozilla/5.0 Android WebView")))
                return True, (None if sig.endswith(")V") else recv)
            if cls in ("Landroidx/browser/customtabs/CustomTabsIntent;", "Landroidx/browser/customtabs/CustomTabsIntent$Builder;"):
                if name == "build":
                    return True, JObject("Landroidx/browser/customtabs/CustomTabsIntent;", {})
                if name == "launchUrl" and len(logical) > 2:
                    u_obj = logical[2]
                    u = u_obj.fields.get("$uri", "") if isinstance(u_obj, JObject) else _text(u_obj)
                    _record_web_event("custom_tabs", u)
                    return True, None
                return True, (None if sig.endswith(")V") else recv)
            # Generische Dialoge (z.B. Virtuagym OkDialog / AlertDialog)
            if name == "show" and sig == "()V" and ("Dialog" in (recv.cls or "") or _vm._is_subclass(recv.cls, "Landroid/app/Dialog;")):
                return True, _generic_show(logical)
            if name in ("dismiss", "cancel") and sig == "()V" and ("Dialog" in (recv.cls or "") or _vm._is_subclass(recv.cls, "Landroid/app/Dialog;")):
                _dismiss_dialog_overlay(recv)
                return True, None
            return False, None
        vm_obj._method_hooks.append(_vhook)
        vm_obj._view_hooks_attached = True

    stubs._ensure_view_vm_hook = _ensure_view_vm_hook
    _ensure_view_vm_hook(getattr(stubs, "vm", None))
    try:
        from core.java.webview_bridge import install_webview_grundset
        install_webview_grundset(stubs, tree)
    except Exception:
        pass
    try:
        from core.java.flutter_channel_bridge import install_flutter_channel_bridge
        install_flutter_channel_bridge(stubs, tree)
    except Exception:
        pass
    _install_compose_bridge(stubs, tree)
    return tree


def _sig_param_types(sig):
    """Extrahiert die Java-Parametertypen aus einer Methodensignatur."""
    try:
        inner = sig[sig.index("(") + 1:sig.index(")")]
    except ValueError:
        return []
    out = []
    i = 0
    while i < len(inner):
        start = i
        while i < len(inner) and inner[i] == "[":
            i += 1
        if i < len(inner) and inner[i] == "L":
            end = inner.index(";", i) + 1
            out.append(inner[start:end])
            i = end
        elif i < len(inner):
            out.append(inner[start:i + 1])
            i += 1
        else:
            break
    return out


_COMPOSE_MATERIAL_ICONS = {
    "lock": "M18,8h-1V6c0,-2.76 -2.24,-5 -5,-5S7,3.24 7,6v2H6c-1.1,0 -2,0.9 -2,2v10c0,1.1 0.9,2 2,2h12c1.1,0 2,-0.9 2,-2V10c0,-1.1 -0.9,-2 -2,-2zM12,17c-1.1,0 -2,-0.9 -2,-2s0.9,-2 2,-2 2,0.9 2,2 -0.9,2 -2,2zM15.1,8H8.9V6c0,-1.71 1.39,-3.1 3.1,-3.1 1.71,0 3.1,1.39 3.1,3.1v2z",
    "person": "M12,12c2.21,0 4,-1.79 4,-4s-1.79,-4 -4,-4 -4,1.79 -4,4 1.79,4 4,4zM12,14c-2.67,0 -8,1.34 -8,4v2h16v-2c0,-2.66 -5.33,-4 -8,-4z",
    "email": "M20,4H4c-1.1,0 -1.99,0.9 -1.99,2L2,18c0,1.1 0.9,2 2,2h16c1.1,0 2,-0.9 2,-2V6c0,-1.1 -0.9,-2 -2,-2zM20,8l-8,5 -8,-5V6l8,5 8,-5v2z",
    "settings": "M19.14,12.94c0.04,-0.3 0.06,-0.61 0.06,-0.94 0,-0.32 -0.02,-0.64 -0.07,-0.94l2.03,-1.58c0.18,-0.14 0.23,-0.41 0.12,-0.61l-1.92,-3.32c-0.12,-0.22 -0.37,-0.29 -0.59,-0.22l-2.39,0.96c-0.5,-0.38 -1.03,-0.7 -1.62,-0.94L14.4,2.81c-0.04,-0.24 -0.24,-0.41 -0.48,-0.41h-3.84c-0.24,0 -0.43,0.17 -0.47,0.41L9.25,5.35C8.66,5.59 8.12,5.92 7.63,6.29L5.24,5.33c-0.22,-0.08 -0.47,0 -0.59,0.22L2.74,8.87C2.62,9.08 2.66,9.34 2.86,9.48l2.03,1.58C4.84,11.36 4.8,11.69 4.8,12s0.02,0.64 0.07,0.94l-2.03,1.58c-0.18,0.14 -0.23,0.41 -0.12,0.61l1.92,3.32c0.12,0.22 0.37,0.29 0.59,0.22l2.39,-0.96c0.5,0.38 1.03,0.7 1.62,0.94l0.36,2.54c0.05,0.24 0.24,0.41 0.48,0.41h3.84c0.24,0 0.44,-0.17 0.47,-0.41l0.36,-2.54c0.59,-0.24 1.13,-0.56 1.62,-0.94l2.39,0.96c0.22,0.08 0.47,0 0.59,-0.22l1.92,-3.32c0.12,-0.22 0.07,-0.47 -0.12,-0.61l-2.01,-1.58zM12,15.6c-1.98,0 -3.6,-1.62 -3.6,-3.6s1.62,-3.6 3.6,-3.6 3.6,1.62 3.6,3.6 -1.62,3.6 -3.6,3.6z",
    "refresh": "M17.65,6.35C16.2,4.9 14.21,4 12,4c-4.42,0 -7.99,3.58 -7.99,8s3.57,8 7.99,8c3.73,0 6.84,-2.55 7.73,-6h-2.08c-0.82,2.33 -3.04,4 -5.65,4 -3.31,0 -6,-2.69 -6,-6s2.69,-6 6,-6c1.66,0 3.14,0.69 4.22,1.78L13,11h7V4l-2.35,2.35z",
    "check": "M9,16.17L4.83,12l-1.42,1.41L9,19 21,7l-1.41,-1.41z",
    "close": "M19,6.41L17.59,5 12,10.59 6.41,5 5,6.41 10.59,12 5,17.59 6.41,19 12,13.41 17.59,19 19,17.59 13.41,12z",
    "add": "M19,13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    "delete": "M6,19c0,1.1 0.9,2 2,2h8c1.1,0 2,-0.9 2,-2V7H6v12zM19,4h-3.5l-1,-1h-5l-1,1H5v2h14V4z",
    "search": "M15.5,14h-0.79l-0.28,-0.27C15.41,12.59 16,11.11 16,9.5 16,5.91 13.09,3 9.5,3S3,5.91 3,9.5 5.91,16 9.5,16c1.61,0 3.09,-0.59 4.23,-1.57l0.27,0.28v0.79l5,4.99L20.49,19l-4.99,-5zM9.5,14C7.01,14 5,11.99 5,9.5S7.01,5 9.5,5 14,7.01 14,9.5 11.99,14 9.5,14z",
    "arrowback": "M20,11H7.83l5.59,-5.59L12,4l-8,8 8,8 1.41,-1.41L7.83,13H20v-2z",
    "keyboardarrowleft": "M15.41,16.59L10.83,12l4.58,-4.59L14,6l-6,6 6,6 1.41,-1.41z",
    "keyboardarrowright": "M8.59,16.59L13.17,12 8.59,7.41 10,6l6,6 -6,6 -1.41,-1.41z",
    "exittoapp": "M10.09,15.59L11.5,17l5,-5 -5,-5 -1.41,1.41L12.67,11H3v2h9.67l-2.58,2.59zM19,3H5c-1.11,0 -2,0.9 -2,2v4h2V5h14v14H5v-4H3v4c0,1.1 0.89,2 2,2h14c1.1,0 2,-0.9 2,-2V5c0,-1.1 -0.9,-2 -2,-2z",
    "info": "M12,2C6.48,2 2,6.48 2,12s4.48,10 10,10 10,-4.48 10,-10S17.52,2 12,2zM13,17h-2v-6h2v6zM13,9h-2V7h2v2z",
    "home": "M10,20v-6h4v6h5v-8h3L12,3 2,12h3v8z",
    "menu": "M3,18h18v-2H3v2zM3,13h18v-2H3v2zM3,6v2h18V6H3z",
    "morevert": "M12,8c1.1,0 2,-0.9 2,-2s-0.9,-2 -2,-2 -2,0.9 -2,2 0.9,2 2,2zM12,10c-1.1,0 -2,0.9 -2,2s0.9,2 2,2 2,-0.9 2,-2 -0.9,-2 -2,-2zM12,16c-1.1,0 -2,0.9 -2,2s0.9,2 2,2 2,-0.9 2,-2 -0.9,-2 -2,-2z",
    "calendartoday": "M20,3h-1V1h-2v2H7V1H5v2H4c-1.1,0 -2,0.9 -2,2v16c0,1.1 0.9,2 2,2h16c1.1,0 2,-0.9 2,-2V5c0,-1.1 -0.9,-2 -2,-2zM20,19H4V8h16v11z",
    "date_range": "M9,11H7v2h2v-2zM13,11h-2v2h2v-2zM17,11h-2v2h2v-2zM19,4h-1V2h-2v2H8V2H6v2H5c-1.11,0 -1.99,0.9 -1.99,2L3,20c0,1.1 0.89,2 2,2h14c1.1,0 2,-0.9 2,-2V6c0,-1.1 -0.9,-2 -2,-2zM19,20H5V9h14v11z",
}


def _material_icon_spec(icon_name):
    if not icon_name:
        return None
    norm = str(icon_name).lower().replace("_", "")
    pd = _COMPOSE_MATERIAL_ICONS.get(norm)
    if not pd:
        for k, v in _COMPOSE_MATERIAL_ICONS.items():
            if k in norm or norm in k:
                pd = v
                break
    if not pd:
        pd = _COMPOSE_MATERIAL_ICONS["info"]
    try:
        from core.java.drawable_loader import parse_svg_path_data
        subs = parse_svg_path_data(pd)
    except Exception:
        subs = []
    if not subs:
        return None
    paths = [{"points": pts, "closed": cl, "fill": "#ffffff", "stroke": None, "stroke_w": 1.0}
             for pts, cl in subs]
    path_groups = [{"subpaths": subs, "fill": "#ffffff", "stroke": None, "stroke_w": 1.0, "fill_type": "nonZero"}]
    return {"kind": "vector", "name": str(icon_name), "vw": 24.0, "vh": 24.0,
            "tint": None, "paths": paths, "path_groups": path_groups}


def _extract_compose_alpha(name, pairs):
    if "Icon" in name:
        return 1.0
    int_args = [v for pt, v in pairs if pt == "I" and isinstance(v, int)]
    if len(int_args) >= 2 and (int_args[-1] & 0x20):
        return 1.0
    f_vals = [v for pt, v in pairs if pt == "F"]
    if not f_vals:
        return 1.0
    raw = f_vals[0]
    if isinstance(raw, float):
        a = raw
    elif isinstance(raw, int):
        if raw == 0:
            return 1.0 if len(int_args) >= 2 else 0.0
        try:
            a = _struct.unpack("<f", _struct.pack("<I", raw & 0xFFFFFFFF))[0]
        except Exception:
            a = 1.0
    else:
        return 1.0
    if a != a or a <= 0.0:
        return 1.0 if (len(int_args) >= 2 and (int_args[-1] & 0x20)) else 0.0
    return max(0.0, min(1.0, float(a)))


def _install_compose_bridge(stubs, tree):
    """Generische Jetpack-Compose-Bruecke fuer DalvikVM + ViewTree.
    Faengt ComponentActivityKt.setContent / ComposeView.setContent sowie Material/Material3-
    Widgets ab und bildet sie interaktiv in das ViewTree-Modell ab."""
    vm = getattr(stubs, "vm", None)
    if vm is None or not hasattr(vm, "_method_hooks"):
        return
    from core.java.objects import JObject, JString, JArray

    _MISSING = object()
    empty_sentinel = JObject("Landroidx/compose/runtime/Composer$Companion$Empty;")
    suspended_sentinel = JObject("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {"name": "COROUTINE_SUSPENDED"})
    composer_obj = JObject("Landroidx/compose/runtime/ComposerImpl;")
    row_scope = JObject("Landroidx/compose/foundation/layout/RowScopeInstance;")
    col_scope = JObject("Landroidx/compose/foundation/layout/ColumnScopeInstance;")
    box_scope = JObject("Landroidx/compose/foundation/layout/BoxScopeInstance;")
    lazy_item_scope = JObject("Landroidx/compose/foundation/lazy/LazyItemScope;")
    local_ctx_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$key": "ctx"})
    state = {
        "stack": [],
        "root_fn2": None,
        "activity": None,
        "depth": 0,
        "slots": {},
        "slot_occ": {},
        "last_slot_by_method": {},
        "changed_occ": {},
        "changed_keys": {},
        "launched_keys": {},
        "cur_comp": None,
        "root_comp": None,
        "viewmodels": {},
        "coroutine_depth": 0,
        "prefs": {},
        "ds_collectors": [],
        "ds_flow": None,
        "collect_depth": 0,
    }

    def _cur_parent():
        if state["stack"]:
            return state["stack"][-1]
        if tree.root is None:
            r = ViewNode(cls="LinearLayout", vid=0xC000, props={"orientation": 1})
            tree.set_root(r)
        return tree.root

    def _txt(v):
        if isinstance(v, JString):
            return v.text or ""
        if isinstance(v, JObject) and "$val" in v.fields:
            return _txt(v.fields["$val"])
        return "" if v in (None, 0) else str(v)

    def _boxed_int(n=0):
        return JObject("Ljava/lang/Integer;", {"value": int(n)})

    def _unwrap_fn(fn_obj):
        if isinstance(fn_obj, JObject) and "$block" in fn_obj.fields:
            return fn_obj.fields["$block"]
        return fn_obj

    def _caller_site(_vm):
        frs = getattr(_vm, "_frames", None)
        if frs:
            fr = frs[-1]
            md = getattr(fr, "md", None)
            if md is not None:
                return (md.cls, md.name, getattr(fr, "cur", getattr(fr, "pc", 0)))
        return ("<root>", "<root>", 0)

    def _key_sig(arg):
        if isinstance(arg, JObject):
            if "__date" in arg.fields or arg.cls == "Ljava/time/LocalDate;":
                return ("ld", arg.fields.get("year"), arg.fields.get("month"), arg.fields.get("day"))
            if arg.cls.startswith("Ljava/lang/") and "value" in arg.fields:
                return ("box", arg.cls, arg.fields.get("value"))
            return ("obj", id(arg))
        if isinstance(arg, JString):
            return ("s", arg.text)
        return ("p", arg)

    def _invoke_fn2(fn_obj):
        fn = _unwrap_fn(fn_obj)
        if not isinstance(fn, JObject):
            return
        try:
            vm.invoke(fn.cls, "invoke",
                      "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                      (fn, composer_obj, _boxed_int(0)))
        except Exception:
            pass

    def _invoke_fn3(fn_obj, scope_obj=None):
        fn = _unwrap_fn(fn_obj)
        if not isinstance(fn, JObject):
            return
        sc = scope_obj if scope_obj is not None else row_scope
        try:
            vm.invoke(fn.cls, "invoke",
                      "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                      (fn, sc, composer_obj, _boxed_int(0)))
        except Exception:
            pass

    def _invoke_lazy_fn(fn_obj, idx=0):
        fn = _unwrap_fn(fn_obj)
        if not isinstance(fn, JObject):
            return
        try:
            vm.invoke(fn.cls, "invoke",
                      "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                      (fn, lazy_item_scope, _boxed_int(idx), composer_obj, _boxed_int(0)))
            return
        except Exception:
            pass
        try:
            vm.invoke(fn.cls, "invoke",
                      "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                      (fn, lazy_item_scope, composer_obj, _boxed_int(0)))
        except Exception:
            pass

    def _invoke_fn0(fn_obj):
        fn = _unwrap_fn(fn_obj)
        if not isinstance(fn, JObject):
            return
        try:
            vm.invoke(fn.cls, "invoke", "()Ljava/lang/Object;", (fn,))
        except Exception:
            pass

    def _f32_num(v, default=0.0):
        if isinstance(v, float):
            return v
        if isinstance(v, int):
            if abs(v) > 10000:
                try:
                    from core.java.dalvik import _f32
                    return float(_f32(v))
                except Exception:
                    return default
            return float(v)
        try:
            return float(v)
        except Exception:
            return default

    def _make_recomposer(act_obj, fn2_obj, target_vn=None):
        comp_state = {
            "act": act_obj,
            "fn2": fn2_obj,
            "target_vn": target_vn,
            "slots": {},
            "slot_occ": {},
            "last_slot_by_method": {},
            "changed_occ": {},
            "changed_keys": {},
            "launched_keys": {},
        }

        def _do_recompose():
            if comp_state["fn2"] is None or state["depth"] >= 4:
                return
            prev_comp = state.get("cur_comp")
            prev_act = state.get("activity")
            prev_stack = state.get("stack")
            state["cur_comp"] = comp_state
            if comp_state["act"] is not None:
                state["activity"] = comp_state["act"]
            comp_state["slot_occ"] = {}
            comp_state["changed_occ"] = {}
            comp_state["last_slot_by_method"] = {}
            state["depth"] += 1
            try:
                tgt = comp_state["target_vn"]
                if tgt is not None:
                    tgt.children.clear()
                    state["stack"] = [tgt]
                    if tree.root is None:
                        tree.set_root(tgt)
                else:
                    state["root_comp"] = comp_state
                    state["slot_occ"] = {}
                    state["changed_occ"] = {}
                    state["last_slot_by_method"] = {}
                    root = ViewNode(cls="LinearLayout", vid=0xC000, props={"orientation": 1, "$compose_root": True})
                    state["stack"] = [root]
                    tree.set_root(root)
                _invoke_fn2(comp_state["fn2"])
                tree.touch()
            finally:
                state["depth"] -= 1
                state["stack"] = prev_stack if prev_stack is not None else []
                state["cur_comp"] = prev_comp
                state["activity"] = prev_act

        comp_state["recomp_fn"] = _do_recompose
        return _do_recompose

    def _recompose():
        fn2 = state.get("root_fn2")
        if fn2 is None:
            return
        _make_recomposer(state.get("activity"), fn2, target_vn=None)()

    def _compose_click(cb_fn, cur_recomp=None):
        if cb_fn is None:
            return
        _invoke_fn0(cb_fn)
        if getattr(stubs, "_pending_nav_target", None) or getattr(stubs, "_activity_finished", False):
            return
        rc = cur_recomp or getattr(tree, "_compose_recompose", None)
        if callable(rc):
            rc()

    tree._compose_recompose = _recompose

    def _collect_slot_text(fn_obj, fn3=False):
        tmp = ViewNode(cls="LinearLayout")
        state["stack"].append(tmp)
        state["collect_depth"] += 1
        try:
            if fn3:
                _invoke_fn3(fn_obj, row_scope)
            else:
                _invoke_fn2(fn_obj)
        finally:
            state["collect_depth"] -= 1
            if state["stack"] and state["stack"][-1] is tmp:
                state["stack"].pop()
        parts = []
        for nd in tmp.walk():
            if nd is not tmp and nd.text:
                parts.append(nd.text)
        return " ".join(p for p in parts if p).strip(), tmp.children

    def _args_by_type(sig, static, args):
        ptypes = _sig_param_types(sig)
        offset = 0 if static else 1
        return [(pt, args[offset + i]) for i, pt in enumerate(ptypes) if offset + i < len(args)]

    def _apply_mod_layout_props(vn_tgt, mod_obj):
        if vn_tgt is None or not isinstance(mod_obj, JObject):
            return
        try:
            from core.java.compose_grundset import apply_compose_mod_props
            apply_compose_mod_props(vn_tgt, mod_obj)
        except Exception:
            if "$weight" in mod_obj.fields:
                vn_tgt.props["layout_weight"] = mod_obj.fields["$weight"]
            if mod_obj.fields.get("$fill_max_h"):
                vn_tgt.props["$fill_max_h"] = True
            if "$h" in mod_obj.fields:
                vn_tgt.props["h"] = mod_obj.fields["$h"]
            if "$w" in mod_obj.fields:
                vn_tgt.props["w"] = mod_obj.fields["$w"]

    _g2_helpers = {
        "ViewNode": ViewNode,
        "_args_by_type": _args_by_type,
        "_f32_num": _f32_num,
        "_txt": _txt,
        "_cur_parent": _cur_parent,
        "_apply_mod_layout_props": _apply_mod_layout_props,
        "_invoke_fn0": _invoke_fn0,
        "_invoke_fn2": _invoke_fn2,
        "_invoke_fn3": _invoke_fn3,
        "_invoke_lazy_fn": _invoke_lazy_fn,
        "_collect_slot_text": _collect_slot_text,
        "_compose_click": _compose_click,
        "_boxed_int": _boxed_int,
        "_unwrap_fn": _unwrap_fn,
    }

    def _coro_suspended(_vm_c):
        try:
            cs = _vm_c._sget("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", "COROUTINE_SUSPENDED")
            if isinstance(cs, JObject):
                return cs
        except Exception:
            pass
        return suspended_sentinel

    def _compose_hook(rcls, cls, name, sig, args, static, _vm):
        # 1) Kotlin Reflection / Coroutines / ViewModel / SharedFlow / WorkManager
        if cls == "Lkotlin/jvm/internal/Reflection;" and name == "getOrCreateKotlinClass":
            return True, JObject("Lkotlin/reflect/KClass;", {"$jclass": args[0] if args else None})

        if cls.startswith("Lkotlin/coroutines/intrinsics/IntrinsicsKt") and name == "getCOROUTINE_SUSPENDED":
            return True, _coro_suspended(_vm)

        if cls == "Lkotlinx/coroutines/flow/SharedFlowKt;" and name.startswith("MutableSharedFlow"):
            return True, JObject("Lkotlinx/coroutines/flow/SharedFlowImpl;", {"__collectors": []})

        if (cls in ("Lkotlinx/coroutines/flow/SharedFlowImpl;", "Lkotlinx/coroutines/flow/MutableSharedFlow;", "Lkotlinx/coroutines/flow/SharedFlow;")
                or (not static and args and isinstance(args[0], JObject) and ("SharedFlow" in args[0].cls or "__collectors" in args[0].fields))):
            recv_f = args[0] if (args and isinstance(args[0], JObject)) else None
            if recv_f is not None:
                if name == "collect" and len(args) > 1 and isinstance(args[1], JObject):
                    colls = recv_f.fields.setdefault("__collectors", [])
                    if args[1] not in colls:
                        colls.append(args[1])
                    return True, _coro_suspended(_vm)
                if name in ("emit", "tryEmit") and len(args) > 1:
                    ev = args[1]
                    recv_f.fields["$last"] = ev
                    cont = args[2] if (len(args) > 2 and isinstance(args[2], JObject)) else JObject("Lkotlin/coroutines/Continuation;")
                    for coll in list(recv_f.fields.get("__collectors") or ()):
                        try:
                            _vm.invoke_virtual(
                                coll, "emit",
                                "(Ljava/lang/Object;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                                (coll, ev, cont),
                            )
                        except Exception:
                            pass
                    return True, (1 if name == "tryEmit" else JObject("Lkotlin/Unit;"))
                if name == "getReplayCache":
                    last = recv_f.fields.get("$last")
                    return True, JObject("Ljava/util/ArrayList;", {"__items": [last] if last is not None else []})

        if cls == "Landroidx/work/Data$Builder;" and args and isinstance(args[0], JObject):
            b = args[0]
            if name == "<init>":
                b.fields["$data"] = {}
                return True, None
            if name == "build":
                return True, JObject("Landroidx/work/Data;", {"$data": dict(b.fields.get("$data") or {})})
            if len(args) > 2:
                b.fields.setdefault("$data", {})[_txt(args[1])] = args[2]
            return True, b

        if cls == "Landroidx/work/Data;" and args and isinstance(args[0], JObject):
            d = args[0].fields.get("$data") or {}
            if name == "getString" and len(args) > 1:
                v = d.get(_txt(args[1]))
                return True, (v if isinstance(v, JString) else (JString(str(v)) if v is not None else None))
            if name == "getInt" and len(args) > 1:
                try:
                    return True, int(d.get(_txt(args[1]), args[2] if len(args) > 2 else 0))
                except Exception:
                    return True, 0
            if name == "getBoolean" and len(args) > 1:
                return True, (1 if d.get(_txt(args[1]), args[2] if len(args) > 2 else 0) else 0)

        if cls in ("Landroidx/work/OneTimeWorkRequest$Builder;", "Landroidx/work/PeriodicWorkRequest$Builder;", "Landroidx/work/WorkRequest$Builder;"):
            b = args[0] if (args and isinstance(args[0], JObject)) else JObject(cls)
            if name == "<init>":
                raw_cls = ""
                if len(args) > 1 and isinstance(args[1], JObject):
                    raw_cls = str(args[1].fields.get("name") or "")
                if raw_cls and not (raw_cls.startswith("L") and raw_cls.endswith(";")):
                    raw_cls = "L" + raw_cls.replace(".", "/") + ";"
                b.fields["$worker_cls"] = raw_cls
                return True, None
            if name == "setInputData" and len(args) > 1:
                b.fields["$input_data"] = args[1]
                return True, b
            if name == "build":
                return True, JObject("Landroidx/work/OneTimeWorkRequest;", {
                    "$worker_cls": b.fields.get("$worker_cls", ""),
                    "$input_data": b.fields.get("$input_data"),
                })
            return True, (None if sig.endswith(")V") else b)

        if cls in ("Landroidx/work/WorkManager;", "Landroidx/work/WorkManager$Companion;", "Landroidx/work/WorkContinuation;"):
            def _run_work_requests(reqs):
                ctx = getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)
                for req in list(reqs or ()):
                    if not isinstance(req, JObject):
                        continue
                    wcls = req.fields.get("$worker_cls")
                    if not wcls:
                        continue
                    worker = JObject(wcls)
                    wparams = JObject("Landroidx/work/WorkerParameters;", {
                        "$input_data": req.fields.get("$input_data") or JObject("Landroidx/work/Data;", {"$data": {}}),
                    })
                    old_b = _vm.step_budget
                    old_lenient = _vm.lenient
                    try:
                        _vm.lenient = True
                        _vm.step_budget = (_vm.steps + 50_000) if old_b is None else max(old_b, _vm.steps + 50_000)
                        _vm._lazy_class(wcls)
                        init_md = _vm._resolve_method(wcls, wcls, "<init>", "(Landroid/content/Context;Landroidx/work/WorkerParameters;)V")
                        if init_md is not None:
                            _vm._run_mdef(init_md, _vm._slots_from_logical("(Landroid/content/Context;Landroidx/work/WorkerParameters;)V", False, [worker, ctx, wparams]))
                        end_md = None
                        cur_c = wcls
                        for _ in range(12):
                            if not cur_c or cur_c == "Ljava/lang/Object;":
                                break
                            _vm._lazy_class(cur_c)
                            for (mc, mn, ms), cand in list(_vm.registry.items()):
                                if mc == cur_c and ms == "()Landroidx/work/ListenableWorker$Result;" and cand.insns:
                                    end_md = cand
                                    break
                            if end_md is not None:
                                break
                            cur_c = _vm._superclass(cur_c)
                        if end_md is not None:
                            _vm._run_mdef(end_md, [worker])
                        else:
                            dw = _vm._resolve_method(wcls, wcls, "doWork", "(Lkotlin/coroutines/Continuation;)Ljava/lang/Object;")
                            if dw is not None:
                                _vm._run_mdef(dw, _vm._slots_from_logical(dw.sig, False, [worker, JObject("Lkotlin/coroutines/Continuation;")]))
                    except Exception:
                        pass
                    finally:
                        _vm.step_budget = old_b
                        _vm.lenient = old_lenient

            if name == "getInstance":
                return True, JObject("Landroidx/work/WorkManager;", {})
            if name in ("beginUniqueWork", "beginWith"):
                reqs = []
                for ar in args[1:]:
                    if isinstance(ar, JObject):
                        if "$worker_cls" in ar.fields:
                            reqs.append(ar)
                        elif "__items" in ar.fields:
                            reqs.extend(ar.fields["__items"])
                return True, JObject("Landroidx/work/WorkContinuation;", {"$requests": reqs})
            if name == "enqueue":
                if not static and args and isinstance(args[0], JObject):
                    reqs = list(args[0].fields.get("$requests") or [])
                    if not reqs and len(args) > 1 and isinstance(args[1], JObject):
                        reqs = list(args[1].fields.get("__items") or ([args[1]] if "$worker_cls" in args[1].fields else []))
                    _run_work_requests(reqs)
                return True, JObject("Landroidx/work/Operation;", {})
            if "LiveData" in sig:
                return True, JObject("Landroidx/lifecycle/MutableLiveData;", {"_value": JObject("Ljava/util/ArrayList;", {"__items": []})})
            return True, JObject("Landroidx/work/Operation;", {})

        if cls == "Lkotlinx/coroutines/flow/StateFlowKt;" and (
                name == "MutableStateFlow"
                or (static and sig == "(Ljava/lang/Object;)Lkotlinx/coroutines/flow/MutableStateFlow;")):
            return True, JObject("Lkotlinx/coroutines/flow/MutableStateFlow;", {"$val": args[0] if args else None})

        if cls.startswith("Lkotlinx/coroutines/flow/FlowKt") and name == "asStateFlow" and args:
            return True, args[0]

        if args and isinstance(args[0], JObject) and "$first_sink" in args[0].fields and name == "emit":
            sinks = state.setdefault("first_sinks", {})
            box = sinks.get(args[0].fields["$first_sink"])
            if box is not None and not box.get("done"):
                box["val"] = args[1] if len(args) > 1 else None
                box["done"] = True
            return True, JObject("Lkotlin/Unit;")

        if cls.startswith("Lkotlinx/coroutines/flow/FlowKt") and name in ("first", "firstOrNull") and args:
            flow = args[0]
            if isinstance(flow, JObject) and "$val" in flow.fields and "$ds_flow" not in flow.fields:
                return True, flow.fields.get("$val")
            if isinstance(flow, JObject):
                sinks = state.setdefault("first_sinks", {})
                box = {"done": False, "val": None}
                sid = f"__first_{id(box)}"
                sinks[sid] = box
                sink = JObject("Lkotlinx/coroutines/flow/FlowCollector;", {"$first_sink": sid})
                cont = args[-1] if isinstance(args[-1], JObject) else JObject("Lkotlin/coroutines/Continuation;")
                try:
                    _vm.invoke(flow.cls, "collect",
                               "(Lkotlinx/coroutines/flow/FlowCollector;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                               (flow, sink, cont))
                except Exception:
                    pass
                finally:
                    sinks.pop(sid, None)
                return True, box["val"]
            return True, None

        if cls in ("Lkotlinx/coroutines/DelayKt;", "Lkotlinx/coroutines/flow/FlowKt__ReduceKt;") and name == "delay":
            return True, JObject("Lkotlin/Unit;")

        if cls == "Landroidx/datastore/preferences/core/PreferencesKeys;" and args:
            return True, JObject("Landroidx/datastore/preferences/core/Preferences$Key;", {"$k": _txt(args[0])})

        if cls in ("Landroidx/datastore/preferences/core/Preferences;", "Landroidx/datastore/preferences/core/MutablePreferences;") or (
                args and isinstance(args[0], JObject) and "$prefs" in args[0].fields and name in ("get", "set", "remove", "clear", "asMap", "contains")):
            if name == "get" and len(args) > 1 and isinstance(args[1], JObject):
                kn = args[1].fields.get("$k") or str(args[1])
                rk = args[0].fields.get("$read_keys")
                if isinstance(rk, set):
                    rk.add(kn)
                return True, state["prefs"].get(kn)
            if name == "set" and len(args) > 2 and isinstance(args[1], JObject):
                kn = args[1].fields.get("$k") or str(args[1])
                state["prefs"][kn] = args[2]
                return True, None
            if name == "remove" and len(args) > 1 and isinstance(args[1], JObject):
                kn = args[1].fields.get("$k") or str(args[1])
                return True, state["prefs"].pop(kn, None)
            if name == "clear":
                state["prefs"].clear()
                return True, None
            if name == "contains" and len(args) > 1 and isinstance(args[1], JObject):
                kn = args[1].fields.get("$k") or str(args[1])
                return True, (1 if kn in state["prefs"] else 0)
            if name == "asMap":
                return True, JObject("Ljava/util/HashMap;", {"map": dict(state["prefs"])})

        if cls == "Landroidx/datastore/core/DataStore;" and name == "getData":
            if state["ds_flow"] is None:
                state["ds_flow"] = JObject("Lkotlinx/coroutines/flow/MutableStateFlow;", {"$ds_flow": True})
            return True, state["ds_flow"]

        def _pref_sig(val):
            if isinstance(val, JString):
                return ("str", val.text)
            if isinstance(val, JObject):
                if "__items" in val.fields:
                    return ("list", tuple(_pref_sig(x) for x in val.fields["__items"]))
                if "value" in val.fields:
                    return ("val", val.cls, val.fields["value"])
                return ("obj", id(val))
            return ("p", val)

        def _emit_ds_coll(coll, cont):
            rk = set()
            mp = JObject("Landroidx/datastore/preferences/core/MutablePreferences;", {"$prefs": state["prefs"], "$read_keys": rk})
            try:
                _vm.invoke(coll.cls, "emit", "(Ljava/lang/Object;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                           (coll, mp, cont))
            except Exception:
                pass
            if not coll.fields.get("$first_sink") and rk:
                state.setdefault("ds_deps", {})[id(coll)] = {k: _pref_sig(state["prefs"].get(k)) for k in rk}

        if (args and isinstance(args[0], JObject) and args[0].fields.get("$ds_flow")
                and name == "collect" and len(args) > 1):
            coll = args[1]
            cont = args[2] if len(args) > 2 else JObject("Lkotlin/coroutines/Continuation;")
            is_first = isinstance(coll, JObject) and bool(coll.fields.get("$first_sink"))
            if coll is not None and not is_first and coll not in state["ds_collectors"]:
                state["ds_collectors"].append(coll)
            if isinstance(coll, JObject):
                _emit_ds_coll(coll, cont)
            return True, JObject("Lkotlin/Unit;")

        if cls == "Landroidx/datastore/preferences/core/PreferencesKt;" and name == "edit" and len(args) > 1:
            blk = _unwrap_fn(args[1])
            cont = args[2] if len(args) > 2 else JObject("Lkotlin/coroutines/Continuation;")
            before = {k: _pref_sig(v) for k, v in state["prefs"].items()}
            mprefs = JObject("Landroidx/datastore/preferences/core/MutablePreferences;", {"$prefs": state["prefs"]})
            if isinstance(blk, JObject):
                try:
                    _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                               (blk, mprefs, cont))
                except Exception:
                    pass
            after = {k: _pref_sig(v) for k, v in state["prefs"].items()}
            if after != before and not state.get("ds_emitting"):
                state["ds_emitting"] = True
                try:
                    deps_map = state.get("ds_deps", {})
                    for coll in list(state["ds_collectors"]):
                        if not isinstance(coll, JObject):
                            continue
                        deps = deps_map.get(id(coll))
                        if deps is not None and all(_pref_sig(state["prefs"].get(k)) == v for k, v in deps.items()):
                            continue
                        _emit_ds_coll(coll, cont)
                finally:
                    state["ds_emitting"] = False
            return True, mprefs

        if cls == "Landroidx/compose/runtime/EffectsKt;" and name.startswith("LaunchedEffect") and state["coroutine_depth"] < 8:
            pairs = _args_by_type(sig, static, args)
            blk = next((_unwrap_fn(v) for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
            if isinstance(blk, JObject):
                key_vals = tuple(
                    _key_sig(v) for pt, v in pairs
                    if "Function2" not in pt and "Composer" not in pt and pt != "I"
                )
                site = _caller_site(_vm)
                comp = state.get("cur_comp") or state.get("root_comp") or state
                lk_map = comp.setdefault("launched_keys", {})
                prev_k = lk_map.get(site, _MISSING)
                if prev_k != key_vals:
                    lk_map[site] = key_vals
                    state["coroutine_depth"] += 1
                    try:
                        scope = JObject("Lkotlinx/coroutines/CoroutineScope;")
                        cont = JObject("Lkotlin/coroutines/Continuation;")
                        _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                                   (blk, scope, cont))
                    except Exception:
                        pass
                    finally:
                        state["coroutine_depth"] -= 1
            return True, None

        if (not static and args and isinstance(args[0], JObject)
                and name == "run" and sig == "()V" and "$coro_fn2" in args[0].fields):
            blk = args[0].fields.get("$coro_fn2")
            scope = args[0].fields.get("$coro_scope") or JObject("Lkotlinx/coroutines/CoroutineScope;")
            if isinstance(blk, JObject) and state["coroutine_depth"] < 8:
                state["coroutine_depth"] += 1
                try:
                    cont = JObject("Lkotlin/coroutines/Continuation;")
                    _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                               (blk, scope, cont))
                except Exception:
                    pass
                finally:
                    state["coroutine_depth"] -= 1
            return True, None

        if cls in ("Lkotlinx/coroutines/BuildersKt;",
                   "Lkotlinx/coroutines/BuildersKt__Builders_commonKt;",
                   "Lkotlinx/coroutines/BuildersKt__BuildersKt;") and (
                name in ("launch", "launch$default", "async", "async$default",
                         "withContext", "withContext$default", "runBlocking", "runBlocking$default")
                or (static and "Function2" in sig)):
            pairs = _args_by_type(sig, static, args)
            blk = next((_unwrap_fn(v) for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
            if (isinstance(blk, JObject) and args and isinstance(args[0], JObject)
                    and args[0].fields.get("$is_process_owner")
                    and getattr(stubs, "_current_activity", None) is None
                    and not name.startswith(("withContext", "runBlocking"))):
                q = getattr(stubs, "pending_runnables", None)
                if isinstance(q, list):
                    q.append(JObject("Ljava/lang/Runnable;", {
                        "_bg_thread": True, "_defer_until_activity": True,
                        "$coro_fn2": blk, "$coro_scope": args[0],
                    }))
                if name.startswith("async") or sig.endswith(")Lkotlinx/coroutines/Deferred;"):
                    return True, JObject("Lkotlinx/coroutines/Deferred;", {"$res": None})
                return True, JObject("Lkotlinx/coroutines/Job;", {"$res": None})
            coro_res = None
            if isinstance(blk, JObject) and state["coroutine_depth"] < 8:
                state["coroutine_depth"] += 1
                try:
                    scope = args[0] if args and isinstance(args[0], JObject) else JObject("Lkotlinx/coroutines/CoroutineScope;")
                    cont = JObject("Lkotlin/coroutines/Continuation;")
                    coro_res = _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                                          (blk, scope, cont))
                except Exception:
                    pass
                finally:
                    state["coroutine_depth"] -= 1
            if name.startswith("withContext") or name.startswith("runBlocking") or sig.endswith(")Ljava/lang/Object;"):
                return True, coro_res
            if name.startswith("async") or sig.endswith(")Lkotlinx/coroutines/Deferred;"):
                return True, JObject("Lkotlinx/coroutines/Deferred;", {"$res": coro_res})
            return True, JObject("Lkotlinx/coroutines/Job;", {"$res": coro_res})

        if cls == "Lkotlinx/coroutines/AwaitKt;" and name == "awaitAll":
            col = args[0] if args else None
            if isinstance(col, JObject):
                raw_items = col.fields.get("__items", [])
            elif isinstance(col, JArray):
                raw_items = col.data
            else:
                raw_items = []
            results = [(it.fields.get("$res") if isinstance(it, JObject) and "$res" in it.fields else it)
                       for it in raw_items]
            return True, JObject("Ljava/util/ArrayList;", {"__items": results})

        if name == "await" and args and isinstance(args[0], JObject) and "$res" in args[0].fields:
            return True, args[0].fields.get("$res")

        if cls in ("Lkotlinx/coroutines/Dispatchers;", "Lkotlinx/coroutines/scheduling/CoroutineScheduler;",
                   "Lkotlinx/coroutines/scheduling/DefaultScheduler;", "Lkotlinx/coroutines/scheduling/DefaultIoScheduler;",
                   "Lkotlinx/coroutines/MainCoroutineDispatcher;", "Lkotlinx/coroutines/android/HandlerDispatcher;",
                   "Lkotlinx/coroutines/android/HandlerContext;", "Lkotlinx/coroutines/internal/MainDispatcherLoader;"):
            if cls == "Lkotlinx/coroutines/internal/MainDispatcherLoader;" and name == "<clinit>":
                if _vm is not None and hasattr(_vm, "statics"):
                    _vm.statics[("Lkotlinx/coroutines/internal/MainDispatcherLoader;", "a")] = JObject(
                        "Lkotlinx/coroutines/MainCoroutineDispatcher;"
                    )
                return True, None
            if name == "<init>":
                return True, args[0] if args else None
            if name == "getKey" or sig == "()Lkotlin/coroutines/CoroutineContext$Key;":
                return True, JObject("Lkotlin/coroutines/CoroutineContext$Key;")
            if sig.endswith(")Lkotlinx/coroutines/MainCoroutineDispatcher;"):
                return True, JObject("Lkotlinx/coroutines/MainCoroutineDispatcher;")
            return True, JObject("Lkotlinx/coroutines/CoroutineDispatcher;")

        if cls.startswith(("Landroidx/compose/ui/graphics/colorspace/", "Landroidx/compose/material3/ColorSchemeKt",
                           "Landroidx/compose/material3/DynamicTonalPaletteKt", "Landroidx/compose/material/ColorsKt")):
            if name == "<clinit>":
                return True, None
            if name == "<init>":
                return True, args[0] if args else None
            if "ColorScheme" in cls or "ColorScheme" in name or "TonalPalette" in cls:
                if name.startswith("darkColorScheme") and (tree.window_bg is None or tree.window_bg_dark):
                    tree.dark_mode = True
                return True, JObject("Landroidx/compose/material3/ColorScheme;", {"dark": tree.dark_mode})
            return True, JObject("Landroidx/compose/ui/graphics/colorspace/Rgb;")

        if cls in ("Landroidx/compose/ui/graphics/ColorScheme;", "Landroidx/compose/material3/ColorScheme;"):
            return True, (args[0] if name == "<init>" and args else (0 if sig.endswith((")J", ")I", ")F", ")Z")) else JObject(cls)))

        if cls in ("Landroidx/compose/ui/graphics/Color;", "Landroidx/compose/ui/graphics/Color$Companion;",
                   "Landroidx/compose/ui/graphics/ColorKt;"):
            if name == "<clinit>":
                return True, None
            if name == "box-impl":
                return True, JObject("Landroidx/compose/ui/graphics/Color;", {"v": int(args[0] or 0) if args else 0})
            if name == "unbox-impl":
                return True, int(args[0].fields.get("v", 0)) if args and isinstance(args[0], JObject) else 0
            if sig.endswith(")Landroidx/compose/ui/graphics/colorspace/ColorSpace;"):
                return True, JObject("Landroidx/compose/ui/graphics/colorspace/Rgb;")
            if sig.endswith((")J", ")I", ")Z")):
                return True, 0
            if sig.endswith((")F", ")D")):
                return True, 1.0
            return True, 0

        if cls in ("Landroidx/compose/material3/CardDefaults;", "Landroidx/compose/material3/ButtonDefaults;",
                   "Landroidx/compose/material3/TopAppBarDefaults;", "Landroidx/compose/material3/NavigationBarDefaults;",
                   "Landroidx/compose/material3/NavigationBarItemDefaults;", "Landroidx/compose/material3/OutlinedTextFieldDefaults;",
                   "Landroidx/compose/material3/CardColors;", "Landroidx/compose/material3/ButtonColors;",
                   "Landroidx/compose/material3/TopAppBarColors;", "Landroidx/compose/material3/NavigationBarItemColors;",
                   "Landroidx/compose/material3/TextFieldColors;"):
            if name == "<clinit>":
                return True, None
            if name == "<init>":
                return True, args[0] if args else None
            ret = sig.rsplit(")", 1)[-1] if ")" in sig else cls
            if ret.startswith("L") and ret.endswith(";"):
                return True, JObject(ret)
            return True, 0

        if cls in ("Landroidx/compose/ui/res/PainterResources_androidKt;",
                   "Landroidx/compose/ui/res/ImageResources_androidKt;",
                   "Landroidx/compose/ui/res/VectorResources_androidKt;",
                   "Landroidx/compose/ui/res/ColorResources_androidKt;"):
            if name == "painterResource":
                return True, JObject("Landroidx/compose/ui/graphics/painter/BitmapPainter;", {"$res_id": args[0] if args else 0})
            if name == "imageResource":
                return True, JObject("Landroidx/compose/ui/graphics/ImageBitmap;", {"$res_id": args[0] if args else 0})
            if name == "vectorResource":
                return True, JObject("Landroidx/compose/ui/graphics/vector/ImageVector;", {"$res_id": args[0] if args else 0})
            return True, 0

        if cls.startswith("Landroidx/compose/material/icons/"):
            if name == "<clinit>":
                return True, None
            if name.startswith("get") and len(name) > 3:
                return True, JObject("Landroidx/compose/ui/graphics/vector/ImageVector;", {"$icon_name": name[3:]})
            if sig.endswith(")Landroidx/compose/ui/graphics/vector/ImageVector;"):
                return True, JObject("Landroidx/compose/ui/graphics/vector/ImageVector;", {"$icon_name": name})
            return True, JObject(cls)

        if cls == "Landroidx/lifecycle/ViewModelKt;" and name == "getViewModelScope":
            return True, JObject("Lkotlinx/coroutines/CoroutineScope;")

        if cls in ("Landroidx/lifecycle/ProcessLifecycleOwner;", "Landroidx/lifecycle/ProcessLifecycleOwner$Companion;") and name == "get":
            return True, JObject("Landroidx/lifecycle/ProcessLifecycleOwner;", {"$is_process_owner": True})

        if (cls == "Landroidx/lifecycle/ProcessLifecycleOwner;" or (
                args and isinstance(args[0], JObject) and args[0].fields.get("$is_process_owner")
        )) and name == "getLifecycle":
            return True, JObject("Landroidx/lifecycle/LifecycleRegistry;", {"$is_process_owner": True})

        if cls in ("Landroidx/lifecycle/LifecycleKt;", "Landroidx/lifecycle/LifecycleOwnerKt;") and name in (
                "getCoroutineScope", "getLifecycleScope"):
            is_proc = bool(args and isinstance(args[0], JObject) and args[0].fields.get("$is_process_owner"))
            return True, JObject("Landroidx/lifecycle/LifecycleCoroutineScopeImpl;", {"$is_process_owner": True} if is_proc else {})

        if cls == "Landroidx/lifecycle/LifecycleCoroutineScopeImpl;":
            if name in ("<clinit>", "<init>", "register") or sig.endswith(")V"):
                return True, None
            if name == "getCoroutineContext" or sig.endswith(")Lkotlin/coroutines/CoroutineContext;"):
                return True, JObject("Lkotlin/coroutines/CoroutineContext;")
            if sig.endswith(")Lkotlinx/coroutines/CoroutineScope;"):
                return True, args[0] if args else JObject("Landroidx/lifecycle/LifecycleCoroutineScopeImpl;")

        if cls in (
            "Lcom/google/firebase/FirebaseApp;",
            "Lcom/google/firebase/crashlytics/FirebaseCrashlytics;",
            "Lcom/google/firebase/analytics/FirebaseAnalytics;",
            "Lcom/google/firebase/messaging/FirebaseMessaging;",
        ):
            if name in ("<clinit>", "<init>") or sig.endswith(")V"):
                return True, None
            if sig.endswith(")Z"):
                return True, 1
            if sig.endswith(")Ljava/lang/String;"):
                return True, JString("stub-firebase-token")
            if sig.endswith(")Lcom/google/android/gms/tasks/Task;"):
                return True, JObject("Lcom/google/android/gms/tasks/Task;",
                                     {"$done": True, "$res": JString("stub-fcm-token")})
            ret = sig.rsplit(")", 1)[-1] if ")" in sig else cls
            if ret.startswith("L") and ret.endswith(";"):
                return True, JObject(ret)
            return True, 0

        if cls.startswith("Lcom/google/android/gms/tasks/Task"):
            if name in ("<clinit>", "<init>"):
                return True, None
            if name in ("isComplete", "isSuccessful"):
                return True, 1
            if name == "isCanceled":
                return True, 0
            if name == "getException":
                return True, None
            if name == "getResult":
                return True, (args[0].fields.get("$res") if args and isinstance(args[0], JObject) and "$res" in args[0].fields else JString("stub-fcm-token"))
            if "Listener" in name or "Continuation" in sig:
                return True, args[0] if args else JObject("Lcom/google/android/gms/tasks/Task;")
            if sig.endswith(")Z"):
                return True, 1
            return True, args[0] if args else JObject("Lcom/google/android/gms/tasks/Task;")

        if cls == "Landroidx/lifecycle/viewmodel/compose/LocalViewModelStoreOwner;" and name == "getCurrent":
            return True, state["activity"] or JObject("Landroidx/lifecycle/ViewModelStoreOwner;")

        if cls == "Landroidx/lifecycle/viewmodel/compose/ViewModelKt;" and name == "viewModel":
            pairs = _args_by_type(sig, static, args)
            kcls = next((v for t, v in pairs if "KClass" in t and isinstance(v, JObject)), None)
            factory = next((v for t, v in pairs if "Factory" in t and isinstance(v, JObject)), None)
            jcls = kcls.fields.get("$jclass") if isinstance(kcls, JObject) else None
            raw_tcls = jcls.fields.get("name") if isinstance(jcls, JObject) else None
            tcls = (raw_tcls if (not raw_tcls or (raw_tcls.startswith("L") and raw_tcls.endswith(";")))
                    else f"L{raw_tcls.replace('.', '/')};")
            vm_key = (id(state.get("activity")), tcls) if tcls else None
            if vm_key and vm_key in state["viewmodels"]:
                return True, state["viewmodels"][vm_key]
            if factory is not None and jcls is not None:
                try:
                    inst = _vm.invoke(factory.cls, "create", "(Ljava/lang/Class;)Landroidx/lifecycle/ViewModel;", (factory, jcls))
                    if vm_key and isinstance(inst, JObject):
                        state["viewmodels"][vm_key] = inst
                    return True, inst
                except Exception:
                    pass
            if tcls:
                inst = JObject(tcls)
                try:
                    _vm.invoke(tcls, "<init>", "()V", (inst,))
                except Exception:
                    pass
                if vm_key:
                    state["viewmodels"][vm_key] = inst
                return True, inst
            return True, JObject("Landroidx/lifecycle/ViewModel;")

        # 2) Compose Entry: ComponentActivityKt.setContent* / ComposeView.setContent
        is_act_set_content = (
            cls == "Landroidx/activity/compose/ComponentActivityKt;"
            and name in ("setContent", "setContent$default")
        )
        is_cv_set_content = (
            cls in ("Landroidx/compose/ui/platform/ComposeView;", "Landroidx/compose/ui/platform/AbstractComposeView;")
            and name == "setContent"
        )
        if is_act_set_content or is_cv_set_content:
            act_obj = args[0] if (is_act_set_content and args and isinstance(args[0], JObject)) else (
                getattr(stubs, "_current_activity", None) or state.get("activity")
            )
            pairs = _args_by_type(sig, static, args)
            fn2 = next((v for t, v in pairs if "Function2" in t and isinstance(v, JObject)), None)
            if fn2 is not None and state["depth"] < 4:
                if is_act_set_content:
                    state["activity"] = act_obj
                    state["root_fn2"] = fn2
                    recomp = _make_recomposer(act_obj, fn2, target_vn=None)
                    tree._compose_recompose = recomp
                    recomp()
                else:
                    cv_obj = args[0] if (args and isinstance(args[0], JObject)) else None
                    tgt_vn = cv_obj.fields.get("$view") if isinstance(cv_obj, JObject) else None
                    if tgt_vn is None and isinstance(cv_obj, JObject):
                        tgt_vn = ViewNode(cls="ComposeView", props={"orientation": 1})
                        cv_obj.fields["$view"] = tgt_vn
                    had_root = (tree.root is not None)
                    recomp = _make_recomposer(act_obj, fn2, target_vn=tgt_vn)
                    if not had_root:
                        state["activity"] = act_obj
                        state["root_fn2"] = fn2
                        tree._compose_recompose = recomp
                    recomp()
            return True, None

        # 3) ComposableLambdaKt & ComposableLambdaImpl
        if cls == "Landroidx/compose/runtime/internal/ComposableLambdaKt;" and name in (
                "composableLambdaInstance", "rememberComposableLambda", "composableLambda"):
            blk = args[2] if len(args) > 2 else (args[-1] if args else None)
            return True, JObject("Landroidx/compose/runtime/internal/ComposableLambdaImpl;", {"$block": blk})

        if ("ComposableLambda" in rcls or "ComposableLambda" in cls) and name == "invoke" and args:
            blk = args[0].fields.get("$block") if isinstance(args[0], JObject) else None
            if isinstance(blk, JObject):
                call_args = [blk]
                for a in args[1:]:
                    call_args.append(_boxed_int(a) if isinstance(a, int) and len(args) >= 3 else a)
                try:
                    return True, _vm.invoke(blk.cls, "invoke", sig, tuple(call_args))
                except Exception:
                    try:
                        # Generischer (Object, Object)Object / (Object, Object, Object)Object / Function4 Fallback
                        if len(args) == 3:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], _boxed_int(0)))
                        if len(args) == 4:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], args[2], _boxed_int(0)))
                        if len(args) == 5:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], args[2], args[3], _boxed_int(0)))
                    except Exception:
                        pass
            return True, None

        # 4) Compose Runtime / Composer / SnapshotState / Updater
        if cls == "Landroidx/compose/runtime/Composer$Companion;" and name == "getEmpty":
            return True, empty_sentinel

        if cls == "Landroidx/compose/runtime/Updater;":
            if name == "constructor-impl":
                return True, (args[0] if args else composer_obj)
            if name == "set-impl":
                val = args[1] if len(args) > 1 else None
                if isinstance(val, JObject) and state["stack"]:
                    top = state["stack"][-1]
                    if "$orientation" in val.fields:
                        top.props["orientation"] = val.fields["$orientation"]
                        top.props["compose_kind"] = val.fields.get("$kind", "")
                    _apply_mod_layout_props(top, val)
                    if "$on_click" in val.fields and val.fields["$on_click"] is not None:
                        _cb = val.fields["$on_click"]
                        _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                        top.clickable = True
                        top.on_click = lambda _v=None, _fn=_cb, _rc0=_rc: _compose_click(_fn, _rc0)
                return True, None
            return True, None

        if cls in ("Landroidx/compose/runtime/Composer;", "Landroidx/compose/runtime/ComposerImpl;") or "Composer" in rcls:
            comp = state.get("cur_comp") or state.get("root_comp") or state
            if name in ("startRestartGroup",):
                return True, args[0] if args else composer_obj
            if name == "endRestartGroup":
                return True, JObject("Landroidx/compose/runtime/ScopeUpdateScope;")
            if name in ("startReusableNode", "startNode"):
                box = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "$reusable_node": True})
                _cur_parent().add(box)
                state["stack"].append(box)
                return True, None
            if name == "endNode":
                if len(state["stack"]) > 1 and state["stack"][-1].props.get("$reusable_node"):
                    popped = state["stack"].pop()
                    par = state["stack"][-1]
                    keep_spacer = (
                        bool(popped.props.get("$compose_spacer"))
                        or (par.props.get("compose_kind") in ("Column", "Row") and (
                            "layout_weight" in popped.props or "h" in popped.props or "$pad_x" in popped.props
                        ))
                    )
                    if (not popped.children and not popped.clickable and not popped.text
                            and not popped.props.get("bg_drawable") and not keep_spacer):
                        if popped in par.children:
                            par.children.remove(popped)
                    tree.touch()
                return True, None
            if name == "shouldExecute":
                return True, 1
            if name in ("getSkipping", "getDefaultsInvalid", "getInserting"):
                return True, 0
            if name in ("changed", "changedInstance"):
                site = _caller_site(_vm)
                c_occ = comp.setdefault("changed_occ", {})
                occ = c_occ.get(site, 0)
                c_occ[site] = occ + 1
                ckey = (site, occ)
                sig_val = _key_sig(args[1] if len(args) > 1 else None)
                c_keys = comp.setdefault("changed_keys", {})
                prev = c_keys.get(ckey, _MISSING)
                c_keys[ckey] = sig_val
                return True, (0 if prev == sig_val else 1)
            if name == "rememberedValue":
                site = _caller_site(_vm)
                s_occ = comp.setdefault("slot_occ", {})
                occ = s_occ.get(site, 0)
                s_occ[site] = occ + 1
                skey = (site, occ)
                comp.setdefault("last_slot_by_method", {})[(site[0], site[1])] = skey
                return True, comp.setdefault("slots", {}).get(skey, empty_sentinel)
            if name == "updateRememberedValue":
                site = _caller_site(_vm)
                skey = comp.setdefault("last_slot_by_method", {}).get((site[0], site[1]))
                if skey is not None and len(args) > 1:
                    comp.setdefault("slots", {})[skey] = args[1]
                return True, None
            if name == "consume":
                if len(args) > 1 and args[1] is local_ctx_key and state["activity"] is not None:
                    return True, state["activity"]
                return True, JObject("Ljava/lang/Object;")
            return True, None

        if cls == "Landroidx/compose/runtime/ComposerKt;":
            return True, 0 if name == "isTraceInProgress" else None
        if cls in ("Landroidx/compose/runtime/ScopeUpdateScope;", "Landroidx/compose/runtime/RecomposeScopeImplKt;", "Landroidx/compose/runtime/ComposablesKt;"):
            if name == "updateChangedFlags":
                return True, args[0] if args else 0
            return True, None
        if cls == "Landroidx/compose/ui/platform/AndroidCompositionLocals_androidKt;" and name == "getLocalContext":
            return True, local_ctx_key

        if cls == "Landroidx/compose/runtime/SnapshotStateKt;":
            if name.startswith("mutableStateOf"):
                return True, JObject("Landroidx/compose/runtime/MutableState;", {"$val": args[0] if args else None})
            if name == "collectAsState":
                fl = args[0] if args else None
                if isinstance(fl, JObject) and "$val" in fl.fields:
                    val = fl.fields["$val"]
                elif isinstance(fl, JObject):
                    val = None
                    try:
                        val = _vm.invoke_virtual(fl, "getValue", "()Ljava/lang/Object;", (fl,))
                    except Exception:
                        val = None
                    if val is None:
                        val = args[1] if (len(args) > 1 and args[1] not in (None, 0)) else 0
                elif len(args) > 1 and args[1] not in (None, 0):
                    val = args[1]
                else:
                    val = 0
                return True, JObject("Landroidx/compose/runtime/State;", {"$val": val})

        if cls in ("Landroidx/compose/runtime/State;", "Landroidx/compose/runtime/MutableState;",
                   "Lkotlinx/coroutines/flow/StateFlow;", "Lkotlinx/coroutines/flow/MutableStateFlow;") or (
                isinstance(args[0] if args else None, JObject) and "$val" in args[0].fields and name in ("getValue", "setValue")):
            if name == "getValue" and args and isinstance(args[0], JObject):
                return True, args[0].fields.get("$val", 0)
            if name == "setValue" and args and isinstance(args[0], JObject) and len(args) > 1:
                args[0].fields["$val"] = args[1]
                return True, None

        # 4b) Inlined Compose MeasurePolicies, Modifier.clickable & Modifier-Chain Passthrough
        if cls == "Landroidx/compose/foundation/layout/RowKt;" and name == "rowMeasurePolicy":
            return True, JObject("Landroidx/compose/ui/layout/MeasurePolicy;", {"$orientation": "horizontal", "$kind": "Row"})
        if cls == "Landroidx/compose/foundation/layout/ColumnKt;" and name == "columnMeasurePolicy":
            return True, JObject("Landroidx/compose/ui/layout/MeasurePolicy;", {"$orientation": "vertical", "$kind": "Column"})
        if cls == "Landroidx/compose/foundation/layout/BoxKt;" and name in ("maybeCachedBoxMeasurePolicy", "rememberBoxMeasurePolicy"):
            return True, JObject("Landroidx/compose/ui/layout/MeasurePolicy;", {"$orientation": "vertical", "$kind": "Box"})

        if cls == "Landroidx/compose/foundation/ClickableKt;" and name.startswith("clickable"):
            pairs = _args_by_type(sig, static, args)
            fn0 = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
            fields = dict(args[0].fields) if args and isinstance(args[0], JObject) else {}
            if fn0 is not None:
                fields["$on_click"] = fn0
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/ui/ComposedModifierKt;" and name == "materializeModifier":
            if len(args) > 1 and isinstance(args[1], JObject):
                return True, args[1]
            return True, JObject("Landroidx/compose/ui/Modifier;")

        if name == "matchParentSize" and sig.endswith(")Landroidx/compose/ui/Modifier;"):
            base_m = args[1] if len(args) > 1 and isinstance(args[1], JObject) else (args[0] if args and isinstance(args[0], JObject) else None)
            fields = dict(base_m.fields) if isinstance(base_m, JObject) else {}
            fields["$match_parent"] = True
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/foundation/layout/LayoutWeightElement;" and name == "<init>":
            if args and isinstance(args[0], JObject) and len(args) > 1:
                args[0].fields["$weight"] = _f32_num(args[1], 1.0)
            return True, None

        try:
            from core.java.compose_grundset import handle_compose_grundset, unpack_compose_color as _g2_unpack_col
            _g2_ok, _g2_val = handle_compose_grundset(
                cls, rcls, name, sig, args, static, _vm, state, tree, _g2_helpers
            )
            if _g2_ok:
                return True, _g2_val
        except Exception:
            _g2_unpack_col = None

        if cls.startswith("Landroidx/compose/") and sig.endswith(")Landroidx/compose/ui/Modifier;"):
            base_mod = args[0] if (args and isinstance(args[0], JObject)) else None
            if not static and base_mod is not None and "Scope" in (base_mod.cls or ""):
                base_mod = args[1] if (len(args) > 1 and isinstance(args[1], JObject)) else None
            fields = dict(base_mod.fields) if isinstance(base_mod, JObject) else {}
            if name == "then" and len(args) > 1 and isinstance(args[1], JObject):
                fields.update(args[1].fields)
            else:
                m_base = name.split("-")[0].split("$")[0]
                m_pairs = _args_by_type(sig, static, args)
                f_vals = [_f32_num(v, 0.0) for pt, v in m_pairs if pt == "F"]
                if m_base == "weight" and f_vals and f_vals[0] > 0:
                    fields["$weight"] = f_vals[0]
                elif m_base in ("fillMaxSize", "fillMaxHeight"):
                    fields["$fill_max_h"] = True
                elif m_base in ("height", "requiredHeight") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$h"] = max(4, int(round(f_vals[0])))
                elif m_base in ("width", "requiredWidth") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$w"] = max(4, int(round(f_vals[0])))
                elif m_base in ("size", "requiredSize") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$w"] = max(4, int(round(f_vals[0])))
                    fields["$h"] = max(4, int(round(f_vals[1] if len(f_vals) > 1 else f_vals[0])))
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        # 4c) LazyListScope (stickyHeader, items, item)
        if cls.startswith("Landroidx/compose/foundation/lazy/LazyListScope") or "LazyListScope" in rcls:
            pairs = _args_by_type(sig, static, args)
            if name in ("stickyHeader", "stickyHeader$default"):
                hdr = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "role": "sticky_header"})
                _cur_parent().add(hdr)
                state["stack"].append(hdr)
                try:
                    fn = next((v for pt, v in pairs if ("Function4" in pt or "Function3" in pt) and isinstance(v, JObject)), None)
                    if fn is not None:
                        _invoke_lazy_fn(fn, 0)
                finally:
                    if state["stack"] and state["stack"][-1] is hdr:
                        state["stack"].pop()
                tree.touch()
                return True, None
            if name in ("item", "item$default"):
                fn = next((v for pt, v in pairs if ("Function3" in pt or "Function4" in pt) and isinstance(v, JObject)), None)
                if fn is not None:
                    _invoke_lazy_fn(fn, 0)
                    tree.touch()
                return True, None
            if name in ("items", "items$default"):
                count = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)), 0)
                fn = next((v for pt, v in pairs if ("Function4" in pt or "Function3" in pt) and isinstance(v, JObject)), None)
                if fn is not None:
                    for idx in range(max(0, min(count, 60))):
                        _invoke_lazy_fn(fn, idx)
                    tree.touch()
                return True, None

        # 5) Material / Material3 / Foundation Compose UI Widgets
        if cls.startswith(("Landroidx/compose/material", "Landroidx/compose/foundation/")):
            pairs = _args_by_type(sig, static, args)

            if name == "MaterialTheme":
                for pt, val in pairs:
                    if "Function2" in pt and isinstance(val, JObject):
                        _invoke_fn2(val)
                return True, None

            if name == "Text" or name.startswith("Text-"):
                txt_val = next((_txt(v) for pt, v in pairs if "String" in pt or "AnnotatedString" in pt or "CharSequence" in pt), "")
                mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
                cb = mod.fields.get("$on_click") if isinstance(mod, JObject) else None
                if txt_val:
                    tprops = {}
                    col_arg = next((v for pt, v in pairs if pt == "J"), None)
                    if col_arg is not None and _g2_unpack_col is not None:
                        _crgb, _ca = _g2_unpack_col(col_arg)
                        if _crgb and _ca > 0.05:
                            tprops["color"] = _crgb
                    if cb is not None:
                        tprops["gravity"] = 1
                        tprops["h"] = 34
                        tprops.setdefault("color", "#cbd5e1" if tree.dark_mode else "#4b5563")
                    tn = ViewNode(cls="TextView", text=txt_val, clickable=bool(cb is not None), props=tprops)
                    _apply_mod_layout_props(tn, mod)
                    if cb is not None:
                        _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                        tn.on_click = lambda _v=None, _fn=cb, _rc0=_rc: _compose_click(_fn, _rc0)
                    _cur_parent().add(tn)
                    tree.touch()
                return True, None

            if name in ("Button", "TextButton", "OutlinedButton", "ElevatedButton", "FilledTonalButton") or name.startswith(("Button-", "TextButton-", "IconButton", "FloatingActionButton")):
                on_click = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
                mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
                f3 = next((v for pt, v in pairs if "Function3" in pt and isinstance(v, JObject)), None)
                f2 = next((v for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
                lbl, slot_kids = _collect_slot_text(f3, fn3=True) if f3 is not None else (_collect_slot_text(f2, fn3=False) if f2 is not None else ("", []))
                icon_spec = next((sk.props.get("drawable") for sk in slot_kids if isinstance(sk.props.get("drawable"), dict)), None)
                icon_desc = next((sk.props.get("desc") or sk.props.get("img") for sk in slot_kids if sk.props.get("desc") or sk.props.get("img")), "")
                bprops = {}
                if not lbl and name.startswith("IconButton"):
                    bprops["w"] = 40
                    bprops["h"] = 40
                    bprops["style"] = "Borderless"
                    bprops["$cl_start_parent"] = True
                    if icon_spec is not None:
                        bprops["bg_drawable"] = icon_spec
                        bprops["a11y_label"] = icon_desc or "Icon"
                        btext = ""
                    else:
                        btext = "\u2190" if "back" in str(icon_desc or "").lower() else "\u2715"
                else:
                    btext = lbl or name.split("-")[0]
                bn = ViewNode(cls="Button", text=btext, clickable=True, props=bprops)
                _apply_mod_layout_props(bn, mod)
                if icon_spec is not None and lbl and "bg_drawable" not in bn.props:
                    bn.props["bg_drawable"] = icon_spec
                if on_click is not None:
                    _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                    bn.on_click = lambda _v=None, _cb=on_click, _rc0=_rc: _compose_click(_cb, _rc0)
                _cur_parent().add(bn)
                tree.touch()
                return True, None

            if name == "NavigationBarItem":
                selected = bool(next((v for pt, v in pairs if pt == "Z"), 0))
                on_click = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
                f2s = [v for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)]
                parts = []
                for fn2_arg in f2s:
                    t_s, _ = _collect_slot_text(fn2_arg, fn3=False)
                    if t_s:
                        parts.append(t_s)
                lbl = " ".join(parts).strip()
                bn = ViewNode(cls="Button", text=lbl or "Tab", clickable=True,
                              props={"role": "navitem", "selected": selected})
                if on_click is not None:
                    _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                    bn.on_click = lambda _v=None, _cb=on_click, _rc0=_rc: _compose_click(_cb, _rc0)
                _cur_parent().add(bn)
                tree.touch()
                return True, None

            if name in ("OutlinedTextField", "TextField", "BasicTextField") or name.startswith(("OutlinedTextField-", "TextField-", "BasicTextField-")):
                val_str = next((_txt(v) for pt, v in pairs if "String" in pt), "")
                on_chg = next((v for pt, v in pairs if "Function1" in pt and isinstance(v, JObject)), None)
                f2s = [v for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)]
                hint = ""
                for fn2_arg in f2s[:2]:
                    hs, _ = _collect_slot_text(fn2_arg, fn3=False)
                    if hs:
                        hint = hs
                        break
                is_pw = any(
                    "PasswordVisualTransformation" in str(getattr(v, "cls", ""))
                    or "Password" in pt
                    for pt, v in pairs
                )
                tprops = {}
                if hint:
                    tprops["hint"] = hint
                if is_pw:
                    tprops["is_password"] = True
                en = ViewNode(cls="EditText", text=val_str or "", clickable=True, focusable=True,
                              props=tprops)
                if on_chg is not None:
                    en.props["$on_change"] = on_chg
                    _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                    def _chg(new_txt, _cb=on_chg, _rc0=_rc, _en=en):
                        txt_s = str(new_txt or "")
                        _en.text = txt_s
                        fn = _unwrap_fn(_cb)
                        old_budget = getattr(_vm, "step_budget", None)
                        try:
                            if old_budget is not None and getattr(_vm, "steps", 0) + 50_000 > old_budget:
                                _vm.step_budget = _vm.steps + 200_000
                            if isinstance(fn, JObject):
                                try:
                                    if hasattr(_vm, "invoke_virtual"):
                                        _vm.invoke_virtual(fn, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;",
                                                           (fn, JString(txt_s)))
                                    else:
                                        _vm.invoke(fn.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;",
                                                   (fn, JString(txt_s)))
                                except Exception:
                                    pass
                        finally:
                            if old_budget is not None:
                                _vm.step_budget = old_budget
                        if state.get("depth", 0) == 0 and not (
                            getattr(stubs, "_pending_nav_target", None)
                            or getattr(stubs, "_activity_finished", False)
                        ):
                            rc_fn = _rc0 or getattr(tree, "_compose_recompose", None)
                            if callable(rc_fn):
                                rc_fn()
                        tree.touch()
                    en.props["$on_change_fn"] = _chg
                _cur_parent().add(en)
                tree.touch()
                return True, None

            if name in ("Checkbox", "Switch") or name.startswith(("Checkbox-", "Switch-")):
                chk = bool(args[0]) if args else False
                wcls = "CheckBox" if "Checkbox" in name else "Switch"
                wn = ViewNode(cls=wcls, text=wcls, clickable=True, props={"checked": chk})
                _cur_parent().add(wn)
                tree.touch()
                return True, None

            if "ProgressIndicator" in name:
                _cur_parent().add(ViewNode(cls="ProgressBar", props={"progress": 50}))
                tree.touch()
                return True, None

            if name in ("Icon", "Image") or name.startswith(("Icon-", "Image-")):
                desc = next((_txt(v) for pt, v in pairs if "String" in pt), "")
                painter = next((v for pt, v in pairs if any(k in pt for k in ("Painter", "ImageVector", "ImageBitmap")) and isinstance(v, JObject)), None)
                mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
                spec = None
                img_name = ""
                if isinstance(painter, JObject):
                    if "$res_id" in painter.fields and painter.fields["$res_id"]:
                        rid = painter.fields["$res_id"]
                        dm = getattr(stubs, "_drawmap", None)
                        try:
                            nm_r = dm.get(int(rid)) if dm else None
                        except Exception:
                            nm_r = None
                        resv = getattr(stubs, "_drawable_resolver", None)
                        if resv is not None:
                            try:
                                spec = resv.resolve(nm_r if nm_r is not None else int(rid))
                            except Exception:
                                spec = None
                        img_name = nm_r or f"res#{rid}"
                    elif "$icon_name" in painter.fields:
                        img_name = str(painter.fields.get("$icon_name") or "")
                        spec = _material_icon_spec(img_name)
                if state["collect_depth"] > 0:
                    if desc or spec is not None:
                        sprops = {"desc": desc}
                        if spec is not None:
                            sprops["drawable"] = spec
                            sprops["img"] = img_name or desc
                        _cur_parent().add(ViewNode(cls="TextView", text="", props=sprops))
                    return True, None
                if spec is not None:
                    alpha = _extract_compose_alpha(name, pairs)
                    par = _cur_parent()
                    is_bg = bool(
                        (isinstance(mod, JObject) and mod.fields.get("$match_parent"))
                        or (0.01 < alpha <= 0.45 and par.props.get("compose_kind") == "Box")
                    )
                    if is_bg:
                        par.props["bg_drawable"] = spec
                        par.props["bg_alpha"] = alpha
                        par.props["bg_scale"] = "fit"
                        par.props["bg_viewport"] = True
                    else:
                        props = {
                            "drawable": spec,
                            "img": img_name or desc or "image",
                            "desc": desc,
                            "h": 28 if name.startswith("Icon") else 84,
                            "img_alpha": alpha,
                        }
                        if name.startswith("Icon"):
                            props["w"] = 28
                        par.add(ViewNode(cls="ImageView", props=props))
                    tree.touch()
                return True, None

            if name in ("Scaffold", "Surface", "Card", "AlertDialog", "NavigationBar", "TopAppBar", "CenterAlignedTopAppBar") or name.startswith(
                    ("Scaffold-", "Surface-", "Card-", "AlertDialog-", "NavigationBar-", "TopAppBar-", "CenterAlignedTopAppBar-")):
                base = name.split("-")[0]
                is_horiz = base in ("TopAppBar", "CenterAlignedTopAppBar", "NavigationBar")
                role_map = {
                    "TopAppBar": "topbar",
                    "CenterAlignedTopAppBar": "topbar",
                    "NavigationBar": "navbar",
                    "Card": "card",
                    "AlertDialog": "dialog",
                    "Scaffold": "scaffold",
                    "Surface": "surface",
                }
                role = role_map.get(base, "container")
                if role == "surface" and _cur_parent().props.get("role") == "sticky_header":
                    for pt, val in pairs:
                        if "Function2" in pt and isinstance(val, JObject):
                            _invoke_fn2(val)
                    tree.touch()
                    return True, None
                box = ViewNode(cls="LinearLayout", props={"orientation": "horizontal" if is_horiz else 1, "role": role})
                mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
                _apply_mod_layout_props(box, mod)
                if role in ("card", "surface"):
                    cb = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
                    if cb is None and isinstance(mod, JObject):
                        cb = mod.fields.get("$on_click")
                    if cb is not None:
                        _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                        box.clickable = True
                        box.on_click = lambda _v=None, _fn=cb, _rc0=_rc: _compose_click(_fn, _rc0)
                _cur_parent().add(box)
                state["stack"].append(box)
                try:
                    if role == "scaffold":
                        f2s = [val for pt, val in pairs if "Function2" in pt and isinstance(val, JObject)]
                        f3s = [val for pt, val in pairs if "Function3" in pt and isinstance(val, JObject)]
                        if f2s:
                            _invoke_fn2(f2s[0])
                        for val in f3s:
                            _invoke_fn3(val, col_scope)
                        for val in f2s[1:]:
                            _invoke_fn2(val)
                    elif role == "dialog":
                        f2s = [val for pt, val in pairs if "Function2" in pt and isinstance(val, JObject)]
                        for val in f2s[2:]:
                            _invoke_fn2(val)
                        if f2s[:2]:
                            brow = ViewNode(cls="LinearLayout", props={"orientation": "horizontal"})
                            box.add(brow)
                            state["stack"].append(brow)
                            try:
                                for val in reversed(f2s[:2]):
                                    _invoke_fn2(val)
                            finally:
                                if state["stack"] and state["stack"][-1] is brow:
                                    state["stack"].pop()
                    else:
                        for pt, val in pairs:
                            if not isinstance(val, JObject):
                                continue
                            if "Function2" in pt:
                                _invoke_fn2(val)
                            elif "Function3" in pt:
                                _invoke_fn3(val, col_scope if base == "Card" else row_scope)
                finally:
                    if state["stack"] and state["stack"][-1] is box:
                        state["stack"].pop()
                tree.touch()
                return True, None

            if name in ("LazyColumn", "LazyRow") or name.startswith(("LazyColumn", "LazyRow")):
                f1 = next((v for pt, v in pairs if "Function1" in pt and isinstance(v, JObject)), None)
                if f1 is not None:
                    lbox = ViewNode(cls="LinearLayout",
                                    props={"orientation": "horizontal" if "LazyRow" in name else 1, "role": "lazy_list"})
                    _cur_parent().add(lbox)
                    state["stack"].append(lbox)
                    try:
                        _vm.invoke(_unwrap_fn(f1).cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;",
                                   (_unwrap_fn(f1), JObject("Landroidx/compose/foundation/lazy/LazyListScope;")))
                    except Exception:
                        pass
                    finally:
                        if state["stack"] and state["stack"][-1] is lbox:
                            state["stack"].pop()
                    if "LazyRow" in name:
                        vis_c = [c for c in lbox.children if c.visible]
                        if len(vis_c) >= 2 and max(
                            (sum(len(sub.text or "") for sub in c.walk()) for c in vis_c),
                            default=0,
                        ) > 40:
                            lbox.props["orientation"] = 1
                    tree.touch()
                return True, None

            if name in ("rememberPagerState", "rememberPagerState$default") or (
                "Pager" in cls and name.startswith("rememberPagerState")
            ):
                init_p = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)), 0)
                cnt = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int) and v > 0), 3)
                # Falls erstes I init_p war und es noch ein I oder Function0 pageCount gibt
                ints = [int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)]
                if len(ints) >= 2:
                    init_p, cnt = ints[0], max(1, ints[1])
                elif ints:
                    init_p = ints[0]
                for pt, v in pairs:
                    if "Function0" in pt and isinstance(v, JObject):
                        try:
                            rv = _vm.invoke(_unwrap_fn(v).cls, "invoke", "()Ljava/lang/Object;", (_unwrap_fn(v),))
                            if isinstance(rv, int) and rv > 0:
                                cnt = rv
                        except Exception:
                            pass
                comp_st = state.get("cur_comp") or state.get("root_comp") or state
                ps_map = comp_st.setdefault("pager_states", {})
                skey = _caller_site(_vm)
                pstate = ps_map.get(skey)
                if pstate is None:
                    pstate = JObject("Landroidx/compose/foundation/pager/PagerState;",
                                     {"_page": max(0, init_p), "_page_count": max(1, cnt)})
                    ps_map[skey] = pstate
                return True, pstate

            if name in ("HorizontalPager", "VerticalPager") or name.startswith(("HorizontalPager-", "VerticalPager-")):
                pstate = next((v for pt, v in pairs if "PagerState" in pt and isinstance(v, JObject)), None)
                if pstate is None:
                    pstate = JObject("Landroidx/compose/foundation/pager/PagerState;", {"_page": 0, "_page_count": 3})
                cur_p = int(pstate.fields.get("_page", 0) or 0)
                _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                vp = ViewNode(cls="ViewPager2",
                              props={"$pager_state": pstate, "$pager_recomp": _rc, "_vp_item": cur_p})
                _cur_parent().add(vp)
                state["stack"].append(vp)
                try:
                    fn = next((v for pt, v in pairs if ("Function4" in pt or "Function3" in pt) and isinstance(v, JObject)), None)
                    if fn is not None:
                        _invoke_lazy_fn(fn, cur_p)
                finally:
                    if state["stack"] and state["stack"][-1] is vp:
                        state["stack"].pop()
                tree.touch()
                return True, None

        if cls == "Landroidx/compose/foundation/pager/PagerState;" or (
            args and isinstance(args[0], JObject) and "_page" in args[0].fields and "PagerState" in (args[0].cls or "")
        ):
            ps = args[0] if args and isinstance(args[0], JObject) else None
            if name == "getCurrentPage" and ps is not None:
                return True, int(ps.fields.get("_page", 0) or 0)
            if name == "getPageCount" and ps is not None:
                return True, int(ps.fields.get("_page_count", 3) or 3)
            if name in ("scrollToPage", "animateScrollToPage", "scrollToPage$default", "animateScrollToPage$default") and ps is not None:
                for a_val in args[1:]:
                    if isinstance(a_val, int):
                        ps.fields["_page"] = max(0, a_val)
                        break
                tree.touch()
                return True, None

        if cls == "Landroidx/compose/ui/text/input/PasswordVisualTransformation;":
            if name == "<init>":
                return True, args[0] if args else JObject(cls)
            return True, None

        return False, None

    vm._method_hooks.append(_compose_hook)


class NavController:
    """Einfacher Activity-Back-Stack fuer die Anzeige: navigate() pusht + fuehrt onCreate aus,
    back() poppt und rendert den vorigen Screen neu (deterministisch). Zielagnostisch."""
    def __init__(self, vm, tree):
        self.vm = vm
        self.tree = tree
        self.stack = []

    def set_initial(self, target):
        self.stack = [target]

    def _run(self, target):
        from core.java.objects import JObject
        s = str(target or "")
        desc = s if (s.startswith("L") and s.endswith(";")) else ("L" + s.replace(".", "/") + ";" if s else s)
        act = JObject(desc)
        try:
            self.vm.invoke(desc, "onCreate", "(Landroid/os/Bundle;)V", (act, None))
        except Exception:
            pass
        return act

    def navigate(self, target):
        self.stack.append(target)
        return self._run(target)

    def back(self):
        if len(self.stack) <= 1:
            return False
        self.stack.pop()
        self._run(self.stack[-1])
        return True

    def current(self):
        return self.stack[-1] if self.stack else None
