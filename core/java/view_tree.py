# -*- coding: utf-8 -*-
"""core.java.view_tree — ViewTree-Modell fuer die Anzeige-/UI-Saeule (Vision Saeule 4).

Pures, zielagnostisches Modell des von der App aufgebauten View-Baums (Struktur + Texte + Props +
Bounds). Dient dem Anzeige-Fenster (Rendering) und der Input-Injektion. `install_view_stubs` haengt
generische android.widget/view-Stubs in `AndroidStubs` ein, die den Baum beim App-Lauf aufzeichnen.
Headless testbar (Layout = einfache vertikale Stapelung; hit_test).
"""
from __future__ import annotations

import copy
import math
import struct as _struct
import threading

from core.java.dalvik import DalvikUnknownOpcode, DebugStop, VmBudgetExceeded

_VM_STOP_EXCS = (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)

DEFAULT_W, DEFAULT_H = 420, 760
_ROW_H = 44
_PAD = 10
_GAP = 8
_LEAF = ("TextView", "Button", "EditText", "ImageView", "ImageButton", "CheckBox", "Switch",
         "ToggleButton", "CompoundButton", "ProgressBar", "SeekBar", "RadioButton", "DatePicker", "ComposeCanvas")
_CONTAINER_CLS = (
    "LinearLayout", "RelativeLayout", "FrameLayout", "ConstraintLayout",
    "CoordinatorLayout", "ScrollView", "NestedScrollView", "HorizontalScrollView",
    "ViewGroup", "CardView", "MaterialCardView", "RadioGroup",
    "TableLayout", "TableRow", "GridLayout", "DrawerLayout",
    "SwipeRefreshLayout", "AppBarLayout", "CollapsingToolbarLayout",
    "ViewPager", "ViewPager2", "DisableableViewPager", "RecyclerView", "ListView",
)


def _paint_widget_cls(raw_cls: str) -> str:
    """Normalisiert AppCompat-/Material-/BrandAware-Subklassen auf Basis-Widget-Typen fuer Messung & Rendering."""
    c = str(raw_cls or "")
    if c in _LEAF or c == "CardView":
        return c
    if c.endswith(("EditText", "AutoCompleteTextView", "AutoCompleteEdit")):
        return "EditText"
    if c.endswith(("ImageButton", "FloatingActionButton", "NavigationFab")):
        return "ImageButton"
    if c.endswith("RadioButton"):
        return "RadioButton"
    if c.endswith("CheckBox"):
        return "CheckBox"
    if c.endswith(("Switch", "SwitchCompat", "MaterialSwitch")):
        return "Switch"
    if c.endswith("ToggleButton"):
        return "ToggleButton"
    if c.endswith("FilterButton"):
        return c
    if c.endswith("Button"):
        return "Button"
    if c.endswith(("ImageView", "IconView")):
        return "ImageView"
    if c.endswith("TextView"):
        return "TextView"
    if c.endswith("SeekBar"):
        return "SeekBar"
    if c.endswith(("ProgressBar", "ProgressIndicator")):
        return "ProgressBar"
    if c.endswith("CardView"):
        return "CardView"
    return c


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
        self._bounds = (0, 0, 0, 0)  # x, y, w, h (nach layout)
        self.on_click = None         # Listener-Objekt (fuer Input-Dispatch)
        self._parent = None          # Rueckverweis (fuer RadioGroup-Single-Select)
        self._tree = None            # Besitzer-ViewTree (fuer Lazy-Layout)
        self._jobj = None            # Gecachtes Android-View-JObject

    @property
    def bounds(self):
        t = self._tree
        if t is None:
            cur = self._parent
            while cur is not None:
                if cur._tree is not None:
                    t = cur._tree
                    self._tree = t
                    break
                cur = cur._parent
        if t is not None and getattr(t, "_layout_dirty", False) and not getattr(t, "_in_layout", False):
            t.ensure_layout()
        return self._bounds

    @bounds.setter
    def bounds(self, val):
        self._bounds = tuple(val)

    def add(self, child):
        if child is None:
            return None
        cur = self
        while cur is not None:
            if cur is child:
                raise ValueError("Cycle in ViewTree")
            cur = cur._parent
        if child._parent is not None and child._parent is not self:
            try:
                child._parent.children.remove(child)
            except ValueError:
                pass
        child._parent = self
        if self._tree is not None:
            for nd in child.walk():
                nd._tree = self._tree
        if child in self.children:
            return child
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
        wcls = _paint_widget_cls(self.cls)
        if wcls in ("EditText", "Button") and self.children:
            return False
        if self.cls in _CONTAINER_CLS or self.cls.endswith(("Layout", "ViewGroup", "ViewPager", "ScrollView", "CardView")):
            return False
        if self.children and wcls not in _LEAF:
            return False
        return wcls in _LEAF or not self.children

    def short(self):
        ident = ("#%s" % self.vid) if self.vid is not None else ""
        t = (" '%s'" % self.text) if self.text else ""
        return "%s%s%s" % (self.cls, ident, t)

    def to_dict(self):
        return {"cls": self.cls, "id": self.vid, "text": self.text,
                "clickable": self.clickable, "visible": self.visible, "bounds": self.bounds,
                "children": [c.to_dict() for c in self.children]}


def _is_indicator_dot(node):
    if _paint_widget_cls(node.cls) != "ImageView" or node.clickable or (node.text or "").strip():
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
    if "Toolbar" in (node.cls or "") or "ActionMenuView" in (node.cls or ""):
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


def _build_pref_cache(root):
    """Einmaliger Bottom-Up + Top-Down-Durchlauf fuer Preference- und Compact-Flags je Node-ID."""
    if root is None:
        return {}
    order = []
    stack = [root]
    visited = set()
    while stack:
        nd = stack.pop()
        nid = id(nd)
        if nid in visited:
            continue
        visited.add(nid)
        order.append(nd)
        for c in nd.children:
            stack.append(c)
    raw = {}
    for nd in reversed(order):
        ht = nd.vid == "title"
        hs = nd.vid == "summary"
        hw = nd.vid == "widget_frame"
        for c in nd.children:
            ce = raw.get(id(c))
            if ce is not None:
                if ce[0]:
                    ht = True
                if ce[1]:
                    hs = True
                if ce[2]:
                    hw = True
        ip = bool(hw or (nd.cls == "LinearLayout" and ht and hs))
        raw[id(nd)] = (ht, hs, hw, ip)
    cache = {}
    for nd in order:
        nid = id(nd)
        ht, hs, hw, ip = raw[nid]
        self_compact = bool(
            nd.props.get("role") in ("card", "sticky_header", "topbar")
            or nd.props.get("$reusable_node")
            or ip
            or _in_compact_grid_item(nd)
        )
        par = getattr(nd, "_parent", None)
        par_compact = cache[id(par)][4] if (par is not None and id(par) in cache) else False
        cache[nid] = (ht, hs, hw, ip, self_compact or par_compact)
    return cache


def _has_vid_in_subtree(node, vid):
    if node is None:
        return False
    t = getattr(node, "_tree", None)
    pc = getattr(t, "_pref_cache", None) if t is not None else None
    if pc is not None:
        entry = pc.get(id(node))
        if entry is not None:
            if vid == "title":
                return entry[0]
            if vid == "summary":
                return entry[1]
            if vid == "widget_frame":
                return entry[2]
    return node.find(vid) is not None


def _is_pref_row(node):
    if node is None:
        return False
    t = getattr(node, "_tree", None)
    pc = getattr(t, "_pref_cache", None) if t is not None else None
    if pc is not None:
        entry = pc.get(id(node))
        if entry is not None:
            return entry[3]
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
    if role in ("scaffold", "surface", "lazy_list") or node.vid == 0xC000 or node.props.get("$compose_root"):
        return 0, 0
    if node.props.get("$reusable_node"):
        return 0, 0
    if "FilterButton" in str(node.props.get("full_cls") or node.cls):
        return 6, 3
    par_nd = getattr(node, "_parent", None)
    if par_nd is not None and "FilterButton" in str(par_nd.props.get("full_cls") or par_nd.cls):
        return 12, 4
    if _in_compact_grid_item(node):
        return 3, 2
    if _is_indicator_row(node):
        return 6, 3
    if node.cls == "RelativeLayout" and _has_vid_in_subtree(node, "title") and _has_vid_in_subtree(node, "summary"):
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


def _node_pad4(node):
    dx, dy = _node_pad(node)
    pl = int(node.props["$pad_l"]) if "$pad_l" in node.props else dx
    pt = int(node.props["$pad_t"]) if "$pad_t" in node.props else dy
    pr = int(node.props["$pad_r"]) if "$pad_r" in node.props else dx
    pb = int(node.props["$pad_b"]) if "$pad_b" in node.props else dy
    return pl, pt, pr, pb


def _node_gap(node):
    role = node.props.get("role")
    if role in ("card", "sticky_header"):
        return 4
    if role == "scaffold":
        return 2
    if node.props.get("$reusable_node") or _in_compact_grid_item(node):
        return 4
    if node.cls == "RelativeLayout" and _has_vid_in_subtree(node, "title") and _has_vid_in_subtree(node, "summary"):
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
    if "ActionMenuView" in (node.cls or "") and not node.children:
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


def _pack_grid_rows(grid_kids, cols):
    rows = []
    cur = []
    used = 0
    for c in grid_kids:
        sp = max(1, min(cols, int(c.props.get("span_size") or 1)))
        if cur and used + sp > cols:
            rows.append(cur)
            cur = []
            used = 0
        cur.append((c, used, sp))
        used += sp
    if cur:
        rows.append(cur)
    return rows


def _is_border_divider(k, anchor_vid):
    if k.cls != "View" or k.children:
        return False
    vid_s = str(k.vid or "").lower()
    if k.props.get("$cl_top_to_bot") == anchor_vid or "border" in vid_s:
        return True
    xh = k.props.get("xml_h")
    xw = k.props.get("xml_w")
    return (xh is not None and int(xh) <= 6 and not k.props.get("$cl_top_parent")) or (
        xw is not None and int(xw) <= 6 and not k.props.get("$cl_start_parent")
    )


def _cl_ratio_anchor(node, kids):
    if node.cls != "ConstraintLayout" or not kids:
        return None
    for anchor in node.children:
        if not anchor.vid or not anchor.props.get("$cl_ratio") or not anchor.props.get("$cl_top_parent"):
            continue
        rat = _parse_cl_ratio(anchor.props.get("$cl_ratio"))
        if not rat:
            continue
        avid = anchor.vid
        if anchor not in kids and not any(
            k.props.get("$cl_same_v_target") == avid or k.props.get("$cl_top_to_top") == avid
            for k in kids
        ):
            continue
        ok = True
        for k in kids:
            if k is anchor:
                continue
            if k.props.get("$cl_start_to_end") == avid:
                ok = False
                break
            if not (
                _is_overlay_scrim_or_click(k, node)
                or _is_concentric_overlay(k, node)
                or k.props.get("$cl_same_v_target") == avid
                or k.props.get("$cl_top_to_top") == avid
                or k.props.get("$cl_start_to_start") == avid
                or (k.props.get("$cl_bot_to_top") and not k.props.get("$cl_top_to_bot"))
                or _is_border_divider(k, avid)
            ):
                ok = False
                break
        if ok:
            return anchor, rat
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


def _is_fixed_y_node(node):
    cur = node
    while cur is not None:
        if (getattr(cur, "props", None) or {}).get("$fixed_y"):
            return True
        cur = getattr(cur, "_parent", None)
    return False


def _is_cl_icon_strip(node):
    if _is_horizontal(node):
        vis = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True) and not _is_collapsed_node(c)]
        if 1 <= len(vis) <= 5 and all(_paint_widget_cls(c.cls) in ("ImageView", "ImageButton") for c in vis):
            return vis
    return []


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
    same_top = (
        (bool(a.props.get("$cl_top_parent")) and bool(b.props.get("$cl_top_parent")))
        or (a.vid and b.props.get("$cl_top_to_top") == a.vid)
        or (b.vid and a.props.get("$cl_top_to_top") == b.vid)
    )
    same_bot = (
        (a.vid and b.props.get("$cl_bot_to_bot") == a.vid)
        or (b.vid and a.props.get("$cl_bot_to_bot") == b.vid)
    )
    if same_top and same_bot and a.props.get("$cl_start_parent") and (
        b.props.get("$cl_end_parent") or b.props.get("$cl_end_to_start")
    ):
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
        ic_vis = _is_cl_icon_strip(p) if p.props.get("xml_w_mode") == "wrap" else []
        if ic_vis:
            for ic in ic_vis:
                ic.props.setdefault("h", 28)
                ic.props.setdefault("xml_w", 28)
            pw = min(cw // 2, len(ic_vis) * 32 + max(0, len(ic_vis) - 1) * 6 + 8)
            fixed[idx] = pw
            used += pw
        elif p.props.get("w") or (p.props.get("xml_w") and p.props.get("xml_w_mode") != "zero"):
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


def _cl_lead_thumb_split(cl_node, seq_kids, cw):
    if cl_node.cls != "ConstraintLayout" or len(seq_kids) < 3:
        return None
    lead = seq_kids[0]
    if (
        not lead.vid
        or _paint_widget_cls(lead.cls) not in ("ImageView", "ImageButton")
        or not lead.props.get("$cl_start_parent")
        or lead.props.get("$cl_end_parent")
    ):
        return None
    rest = seq_kids[1:]
    if not all(c.props.get("$cl_start_to_end") == lead.vid for c in rest):
        return None
    if lead.props.get("w") or (lead.props.get("xml_w") and lead.props.get("xml_w_mode") != "zero"):
        raw_lw = int(lead.props.get("w") or lead.props.get("xml_w"))
    elif lead.props.get("$cl_ratio"):
        rat = _parse_cl_ratio(lead.props.get("$cl_ratio")) or 1.0
        raw_lw = int(round(92 * rat))
    else:
        raw_lw = 88
    lead_w = max(40, min(int(float(cw) * 0.42), raw_lw))
    if lead.props.get("h") or (lead.props.get("xml_h") and lead.props.get("xml_h_mode") != "zero"):
        lead_h = int(lead.props.get("h") or lead.props.get("xml_h"))
    elif lead.props.get("$cl_ratio"):
        rat = _parse_cl_ratio(lead.props.get("$cl_ratio")) or 1.0
        lead_h = max(72, int(round(float(lead_w) / rat)))
    else:
        lead_h = lead_w
    col_gap = 12
    col_w = max(40, int(cw) - lead_w - col_gap)
    rhs = []
    for rc in rest:
        if rc.is_leaf() and _paint_widget_cls(rc.cls) == "TextView" and "h" not in rc.props:
            ln = _wrap_line_count(rc.text, col_w, font_size=9)
            rhs.append(22 if ln <= 1 else (10 + ln * 16))
        else:
            rhs.append(_measure(rc, col_w))
    rgap = 3
    nz = [h_i for h_i in rhs if h_i > 0]
    right_h = sum(nz) + rgap * max(0, len(nz) - 1)
    inner_h = max(lead_h, right_h)
    return lead, lead_w, lead_h, rest, col_gap, col_w, rhs, rgap, right_h, inner_h


def _measure(node, avail_w=DEFAULT_W):
    t = getattr(node, "_tree", None)
    mcache = getattr(t, "_mcache", None) if t is not None else None
    key = (id(node), int(DEFAULT_W if avail_w is None else avail_w)) if mcache is not None else None
    if key is not None and key in mcache:
        return mcache[key]
    res = _measure_uncached(node, avail_w, t)
    if mcache is not None and key is not None:
        mcache[key] = res
    return res


def _measure_uncached(node, avail_w=DEFAULT_W, tree=None):
    _sync_composite_edittext(node)
    if _is_collapsed_node(node):
        return 0
    if node.is_leaf():
        wcls = _paint_widget_cls(node.cls)
        if "h" in node.props:
            return int(node.props["h"])
        if wcls == "ComposeCanvas":
            return max(40, int(node.props.get("xml_h") or 220))
        if node.props.get("$cl_ratio") and avail_w and (
            node.props.get("xml_h_mode") == "zero" or "xml_h" not in node.props
        ):
            _rat = _parse_cl_ratio(node.props.get("$cl_ratio"))
            if _rat:
                _weff = int(node.props.get("w") or node.props.get("xml_w") or avail_w)
                return max(16, int(round(float(_weff) / _rat)))
        if wcls in ("ImageView", "ImageButton"):
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
        if wcls == "TextView":
            if node.vid == "bottom_navigation_item_title":
                return 16
            par = getattr(node, "_parent", None)
            pc = getattr(tree, "_pref_cache", None) if tree is not None else None
            if par is not None and pc is not None and id(par) in pc:
                in_compact = pc[id(par)][4]
            else:
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
        if wcls == "Button":
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
    pl, pt_pad, pr, pb_pad = _node_pad4(node)
    gap = _node_gap(node)
    cw = max(20, float(avail_w or DEFAULT_W) - pl - pr)
    v_pad = pt_pad + pb_pad
    if any(c.vid == "selected_state" and c.visible and not _is_collapsed_node(c) for c in kids):
        kids = [c for c in kids if c.vid != "unselected_state"]
    if _is_indicator_row(node):
        return 20
    cols = int(node.props.get("span_count") or 1)
    if cols >= 2:
        grid_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        if not grid_kids:
            return 0
        cell_w = max(40.0, (cw - gap * (cols - 1)) / cols)
        gh = pt_pad
        rows_nz = 0
        for row_items in _pack_grid_rows(grid_kids, cols):
            row_h = max(
                (_measure(c, cw if sp == cols else (cell_w * sp + gap * (sp - 1))) for c, _, sp in row_items),
                default=0,
            )
            if row_h > 0:
                gh += row_h + gap
                rows_nz += 1
        return max(_ROW_H, gh + pb_pad - gap) if rows_nz else 0
    if _is_horizontal(node):
        widths = ViewTree._calc_horizontal_widths(node, kids, cw, gap)
        mh = max(_measure(c, max(4, w_c)) for c, w_c in zip(kids, widths)) + v_pad
        if node.props.get("role") == "topbar" or "Toolbar" in (node.cls or ""):
            return max(48, mh)
        if node.props.get("role") == "navbar":
            return max(54, mh)
        return mh
    _ra = _cl_ratio_anchor(node, kids)
    if _ra is not None:
        return max(32, int(round(float(cw) / _ra[1]))) + v_pad
    if node.props.get("compose_kind") == "Box" and len(kids) >= 1:
        box_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        if box_kids:
            def _box_kid_h(c):
                ch0 = _measure(c, cw)
                if c.is_leaf():
                    ch0 += int(c.props.get("$pad_t", 0) or 0) + int(c.props.get("$pad_b", 0) or 0)
                return ch0
            return max((_box_kid_h(c) for c in box_kids), default=0) + v_pad
    if node.cls == "ConstraintLayout" and len(kids) >= 3:
        cl_flow_early = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        lt_early = _cl_lead_thumb_split(node, cl_flow_early, cw)
        if lt_early is not None:
            return max(_ROW_H, lt_early[9] + v_pad)
    has_cl_v_chain = node.cls == "ConstraintLayout" and any(c.props.get("$cl_top_to_bot") for c in kids)
    if node.cls in ("ConstraintLayout", "FrameLayout") and len(kids) >= 2 and not has_cl_v_chain:
        z_m = [c for c in kids if _is_zstack_layer(c) or c.vid in ("images_holder",)]
        if len(z_m) >= 2 or (len(z_m) >= 1 and any(c.props.get("$cl_bot_parent") or c.vid in ("constraint_layout", "indicator_holder") for c in kids)):
            return max(_measure(c, cw) for c in kids) + v_pad
    if node.cls == "ConstraintLayout" and len(kids) >= 2:
        cl_flow = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        lt_sp = _cl_lead_thumb_split(node, cl_flow, cw)
        if lt_sp is not None:
            return max(_ROW_H, lt_sp[9] + v_pad)
        rows, guides = _cl_group_rows(node, cl_flow, cw)
        if any(len(r) >= 2 for r in rows):
            h = pt_pad
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
            return max(_ROW_H, h + pb_pad - gap) if non_zero else 0
    if node.cls in ("CardView", "MaterialCardView", "FrameLayout"):
        flow_m = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
        if len(flow_m) == 1 and flow_m[0].props.get("xml_margin"):
            m_in = min(24, max(0, int(flow_m[0].props.get("xml_margin") or 0)))
            ch0 = _measure(flow_m[0], max(16, cw - 2 * m_in))
            if ch0 > 0:
                return max(24, ch0 + 2 * m_in + v_pad)
    h = pt_pad
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
        or "FilterButton" in str(node.props.get("full_cls") or node.cls)
        or _in_compact_grid_item(node)
    ) else _ROW_H)
    return max(min_h, h + pb_pad - gap)


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
        self.render_source = "executed"
        self._lock = threading.RLock()
        self._layout_dirty = False
        self._in_layout = False
        self._laid_out_size = (width, height)
        self._pref_cache = None
        self._mcache = None

    def effective_dark(self):
        if self.window_bg is not None and not self.window_bg_dark:
            rrole = self.root.props.get("role") if self.root is not None else None
            if rrole != "scaffold":
                return False
        return bool(self.dark_mode)

    def set_root(self, node):
        with self._lock:
            self.root = node
            if node is not None:
                for nd in node.walk():
                    nd._tree = self
            self.layout()
            self.version += 1

    def touch(self):
        with self._lock:
            if self.root is not None and self.root._tree is None:
                self.root._tree = self
            self._layout_dirty = True
            self.version += 1

    def ensure_layout(self):
        with self._lock:
            if self.root is None or self._in_layout:
                return
            if self._layout_dirty or (
                self._laid_out_size != (self.width, self.height)
                and getattr(self.root, "_laid_once", False)
            ):
                self.layout()

    def layout(self):
        with self._lock:
            if self.root is None:
                self._layout_dirty = False
                self._laid_out_size = (self.width, self.height)
                return
            if self._in_layout:
                return
            self._in_layout = True
            try:
                for n in self.root.walk():
                    n._tree = self
                    n.props.pop("$occluded_by_blur", None)
                    n.props.pop("$fill_h", None)
                    n.props.pop("$fixed_y", None)
                    n.props.pop("$draw_center_divider_y", None)
                if callable(getattr(self, "_custom_layout_fn", None)):
                    self._custom_layout_fn(self.root, self.width, self.height)
                    self.root._laid_once = True
                    return
                self._pref_cache = _build_pref_cache(self.root)
                self._mcache = {}
                self._lay(self.root, 0, 0, self.width)
                self.root._laid_once = True
            finally:
                self._mcache = None
                self._pref_cache = None
                self._layout_dirty = False
                self._laid_out_size = (self.width, self.height)
                self._in_layout = False

    @staticmethod
    def _order_toolbar_kids(node, kids):
        if not ("Toolbar" in (node.cls or "") or node.props.get("role") == "topbar") or len(kids) < 2:
            return kids
        nav_k, title_k, menu_k = [], [], []
        for k in kids:
            kc = _paint_widget_cls(k.cls)
            if k.props.get("$opt_menu_btn") or "ActionMenuView" in (k.cls or ""):
                menu_k.append(k)
            elif kc == "ImageButton" and not nav_k:
                nav_k.append(k)
            elif kc == "TextView":
                title_k.append(k)
            else:
                menu_k.append(k)
        if title_k and (nav_k or menu_k):
            return nav_k + title_k + menu_k
        return kids

    @staticmethod
    def _calc_horizontal_widths(node, kids, inner, gap):
        role = node.props.get("role")
        n = len(kids)
        if (role == "topbar" or "Toolbar" in (node.cls or "")) and n >= 2:
            title_idxs = [i for i, k in enumerate(kids) if _paint_widget_cls(k.cls) == "TextView"]
            if title_idxs:
                t_idx = title_idxs[0]
                out_tb = [0] * n
                used = gap * max(0, n - 1)
                for i, k in enumerate(kids):
                    if i == t_idx:
                        continue
                    w_prop = int(k.props.get("w") or 0)
                    if w_prop > 0:
                        aw = min(max(40, inner // 2), w_prop)
                    elif _paint_widget_cls(k.cls) == "ImageButton":
                        aw = 40
                    else:
                        t = (k.text or "")
                        aw = 40 if len(t) <= 3 else min(90, max(54, 14 + len(t) * 8))
                    out_tb[i] = aw
                    used += aw
                out_tb[t_idx] = max(60, inner - used)
                return out_tb
        # Week navigation pattern: [Button("<"), TextView("16.11. - 22.11."), Button(">")]
        if (n == 3 and _paint_widget_cls(kids[0].cls) == "Button" and _paint_widget_cls(kids[2].cls) == "Button"
                and _paint_widget_cls(kids[1].cls) == "TextView" and len(kids[0].text or "") <= 3 and len(kids[2].text or "") <= 3):
            bw = 54
            kids[1].props.setdefault("gravity", 1)
            return [bw, max(60, inner - 2 * bw - 2 * gap), bw]
        # Bullet list row: [' • ', 'Customisable nutrition plan'] (Compose / XML rows)
        if (n == 2 and _paint_widget_cls(kids[0].cls) == "TextView"
                and (kids[0].text or "").strip() in ("•", "-", "*", "·", "✓", "✔", "✕", "×")):
            bw = 18
            return [bw, max(40, inner - bw - gap)]
        # Event card time/capacity row: [TextView("17.11. | ..."), TextView("12 / 15")]
        if n == 2 and _paint_widget_cls(kids[0].cls) == "TextView" and _paint_widget_cls(kids[1].cls) == "TextView":
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
                        _paint_widget_cls(sub.cls) in ("ImageView", "ImageButton")
                        and (sub.props.get("drawable") or sub.props.get("img_bytes") or (sub.props.get("img") or "").strip())
                        for sub in k.walk()
                    )
                    fixed[idx] = 40 if has_vis_ic else 0
                elif k.vid == "widget_frame":
                    fixed[idx] = 54 if any(c.visible for c in k.children) else 0
                elif _paint_widget_cls(k.cls) in ("Switch", "CheckBox", "RadioButton") and not (k.text or "").strip():
                    fixed[idx] = 50
                elif "w" in k.props and int(k.props["w"]) <= 6 and k.cls == "View":
                    fixed[idx] = max(1, int(k.props["w"]))
                elif "w" in k.props or "xml_w" in k.props:
                    fixed[idx] = int(k.props.get("w") or k.props.get("xml_w") or 48)
                else:
                    txts_w = [sub.text.strip() for sub in k.walk() if sub.visible and (sub.text or "").strip()]
                    if txts_w:
                        fixed[idx] = min(max(48, inner // 3), max(48, 22 + len(" ".join(txts_w)) * 7))
                    else:
                        fixed[idx] = 48
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
        if all("FilterButton" in str(k.props.get("full_cls") or k.cls) for k in kids):
            out_fb = []
            for k in kids:
                txts = [sub.text.strip() for sub in k.walk() if sub.visible and (sub.text or "").strip()]
                lbl_s = " ".join(txts)
                out_fb.append(min(max(108, 56 + len(lbl_s) * 7), max(156, inner // max(1, n))))
            return out_fb
        if node.props.get("role") == "lazy_list" and _is_horizontal(node):
            out_lr = []
            for k in kids:
                if "w" in k.props:
                    out_lr.append(max(40, int(k.props["w"])))
                else:
                    txts = [sub.text.strip() for sub in k.walk() if sub.visible and (sub.text or "").strip()]
                    lbl_s = " ".join(txts)
                    out_lr.append(max(64, min(160, 28 + len(lbl_s) * 7)))
            tot_lr = sum(out_lr) + gap * max(0, n - 1)
            if tot_lr > inner and tot_lr > 0:
                sc_lr = inner / float(tot_lr)
                out_lr = [max(48, int(w_i * sc_lr)) for w_i in out_lr]
            return out_lr
        if any("w" in k.props for k in kids):
            fixed = {}
            flex_idxs = []
            used = gap * max(0, n - 1)
            for idx, k in enumerate(kids):
                if "w" in k.props and not k.props.get("$fill_max_w"):
                    raw_kw = int(k.props["w"])
                    kw = max(1, raw_kw) if (raw_kw <= 6 and k.cls == "View") else max(20, min(inner // 2, raw_kw))
                    fixed[idx] = kw
                    used += kw
                else:
                    flex_idxs.append(idx)
            if not flex_idxs and any(k.props.get("$fill_max_w") for k in kids):
                for idx, k in enumerate(kids):
                    if k.props.get("$fill_max_w"):
                        used -= fixed.pop(idx, 0)
                        flex_idxs.append(idx)
            rem = max(20, inner - used)
            per_flex = max(16, rem // len(flex_idxs)) if flex_idxs else 0
            return [fixed.get(i, per_flex) for i in range(n)]
        if any(k.props.get("$fill_max_w") for k in kids) and not all(k.props.get("$fill_max_w") for k in kids):
            fixed = {}
            flex_idxs = []
            used = gap * max(0, n - 1)
            for idx, k in enumerate(kids):
                if k.props.get("$fill_max_w"):
                    flex_idxs.append(idx)
                elif _paint_widget_cls(k.cls) in ("ImageView", "ImageButton"):
                    fw = min(48, max(24, int(k.props.get("xml_w") or 36)))
                    fixed[idx] = fw
                    used += fw
                else:
                    txts = [sub.text.strip() for sub in k.walk() if sub.visible and (sub.text or "").strip()]
                    fw = min(inner // 3, max(36, 16 + len(" ".join(txts)) * 7))
                    fixed[idx] = fw
                    used += fw
            rem = max(40, inner - used)
            per_flex = max(40, rem // len(flex_idxs))
            return [fixed.get(i, per_flex) for i in range(n)]
        # Compact icon + text row (e.g. Compose Row { Icon(...); Text(...) } or XML ImageView + TextView)
        if n == 2 and role not in ("navbar", "topbar"):
            k0c = _paint_widget_cls(kids[0].cls)
            k1c = _paint_widget_cls(kids[1].cls)
            k0_ic = k0c in ("ImageView", "ImageButton") or (
                k0c == "TextView" and len((kids[0].text or "").strip()) <= 2 and k1c != "ImageView"
            )
            k1_ic = k1c in ("ImageView", "ImageButton")
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
        if node is not self.root:
            self._lay_body(node, x, y, w, forced_h=forced_h)
            return
        all_kids = [c for c in node.children if c.visible and not _is_collapsed_node(c)]
        dlg_kids = [c for c in all_kids if c.props.get("role") == "dialog"]
        flow_root_kids = [c for c in all_kids if c.props.get("role") != "dialog"]
        self._lay_body(node, x, y, w, forced_h=forced_h, kids_override=flow_root_kids)
        if dlg_kids:
            _, ry, rw, rh = node.bounds
            vh = max(int(self.height), int(rh or 0))
            if int(node.bounds[3] or 0) < int(self.height):
                node.bounds = (x, y, w, int(self.height))
                rh = int(self.height)
            dw = max(220, int(w) - 32)
            dx = int(x) + max(0, (int(w) - dw) // 2)
            for dc in dlg_kids:
                dh = _measure(dc, dw)
                dy = int(y) + max(24, (min(vh, int(self.height)) - dh) // 2)
                dc.props["$fixed_y"] = True
                self._lay_body(dc, dx, dy, dw)

    def _lay_body(self, node, x, y, w, forced_h=None, kids_override=None):
        mh = _measure(node, w)
        if node is self.root and mh <= 0:
            mh = _ROW_H
        kids = (
            list(kids_override)
            if kids_override is not None
            else [c for c in node.children if c.visible and not _is_collapsed_node(c)]
        )
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
        if any(c.vid == "selected_state" and c.visible and not _is_collapsed_node(c) for c in kids):
            for c in kids:
                if c.vid == "unselected_state":
                    c.bounds = (x, y, 0, 0)
                    c.props["$occluded_by_blur"] = True
                elif c.vid == "selected_state" and not c.props.get("bg_drawable"):
                    c.props["bg_drawable"] = {
                        "kind": "shape", "shape": "rectangle",
                        "fill": "#4b5563" if self.effective_dark() else "#e2e8f0",
                        "radius": 18.0,
                    }
            kids = [c for c in kids if c.vid != "unselected_state"]
        if (
            "FilterButton" in str(node.props.get("full_cls") or node.cls)
            and not node.props.get("bg_drawable")
            and not any(ch.props.get("bg_drawable") for ch in node.children)
        ):
            node.props["bg_drawable"] = {
                "kind": "shape", "shape": "rectangle",
                "outline": "#94a3b8" if self.effective_dark() else "#64748b",
                "stroke_w": 1.0,
                "radius": 18.0,
            }
        if not kids:
            return
        pl, pt_pad, pr, pb_pad = _node_pad4(node)
        px, py = pl, pt_pad
        gap = _node_gap(node)
        is_full_h = (node is self.root and bool(node.props.get("$fill_h"))) or bool(node.props.get("$fill_h")) or (forced_h is not None)
        if (is_full_h and (node.cls in ("ConstraintLayout", "FrameLayout", "DisableableViewPager", "ViewPager", "ViewPager2") or "ViewPager" in node.cls)
                and len(kids) == 1 and _is_zstack_layer(kids[0])):
            kids[0].props["$fill_h"] = True
            self._lay(kids[0], x, y, w, forced_h=mh)
            return
        _ra = _cl_ratio_anchor(node, kids) if node is not self.root else None
        if _ra is not None:
            anchor = _ra[0]
            bx = x + pl
            by = y + pt_pad
            bw = max(20, w - pl - pr)
            bh = max(32, mh - pt_pad - pb_pad)
            if anchor.visible:
                anchor.bounds = (bx, by, bw, bh)
            has_lbl = any(
                k is not anchor and _paint_widget_cls(k.cls) == "TextView" and (k.text or "").strip()
                for k in kids
            )
            for k in kids:
                if k is anchor:
                    continue
                kcls = _paint_widget_cls(k.cls)
                if _is_border_divider(k, anchor.vid):
                    xw_d = k.props.get("xml_w")
                    xh_d = k.props.get("xml_h")
                    if xw_d is not None and int(xw_d) <= 6 and not k.props.get("$cl_start_parent"):
                        dw = max(1, min(4, int(xw_d)))
                        k.bounds = (bx + bw - dw, by, dw, bh)
                    else:
                        dh = max(1, min(4, int(xh_d or 1)))
                        k.bounds = (bx, by + bh - dh, bw, dh)
                elif k.cls == "View" or _is_overlay_scrim_or_click(k, node):
                    k.bounds = (bx, by, bw, bh)
                elif kcls == "TextView" and not k.props.get("$cl_top_parent") and not k.props.get("$cl_top_to_top"):
                    lh = min(bh // 2, max(20, int(k.props.get("xml_h") or 32)))
                    k.bounds = (bx + 4, by + bh - lh - 2, max(16, bw - 8), lh)
                elif kcls in ("ImageView", "ImageButton"):
                    if has_lbl:
                        lbl_h = min(bh // 2, 34)
                        ic_sz = max(24, min(int(bw * 0.46), bh - lbl_h - 10))
                        k.props["scale_type"] = "FIT_CENTER"
                        k.bounds = (
                            bx + (bw - ic_sz) // 2,
                            by + max(6, (bh - lbl_h - ic_sz) // 2 + 2),
                            ic_sz,
                            ic_sz,
                        )
                    else:
                        ix = max(16, int(bw * 0.12))
                        it = max(24, min(44, int(bh * 0.22)))
                        ib = max(12, int(bh * 0.10))
                        k.bounds = (bx + ix, by + it, max(32, bw - 2 * ix), max(24, bh - it - ib))
                else:
                    self._lay(k, bx, by, bw, forced_h=bh)
            return
        has_cl_v_chain = node.cls == "ConstraintLayout" and any(c.props.get("$cl_top_to_bot") for c in kids)
        z_layers = [c for c in kids if _is_zstack_layer(c) or c.vid in ("images_holder",)]
        if (is_full_h and node.cls in ("ConstraintLayout", "FrameLayout") and not has_cl_v_chain
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
                    if c.props.get("$cl_top_parent") and not c.props.get("$cl_bot_parent"):
                        cw_c = min(w, int(c.props.get("w") or c.props.get("xml_w") or w))
                        ch_c = int(c.props.get("h") or c.props.get("xml_h") or _measure(c, cw_c))
                        mt_c = int(c.props.get("margin_t") or 12)
                        if c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent") and cw_c < w:
                            cx_c = x + w - cw_c - int(c.props.get("margin_e") or 8)
                        elif c.props.get("$cl_start_parent") and not c.props.get("$cl_end_parent") and cw_c < w:
                            cx_c = x + int(c.props.get("margin_s") or 8)
                        else:
                            cx_c = x + max(0, (w - cw_c) // 2)
                        self._lay(c, cx_c, y + mt_c, cw_c, forced_h=ch_c)
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
            def _is_fab_overlay(c):
                fcls = str(c.props.get("full_cls") or c.cls or "")
                return (
                    ("FloatingActionButton" in fcls or c.cls.endswith(("FloatingActionButton", "NavigationFab")))
                    and c.props.get("xml_w_mode") != "match"
                )
            fab_kids = [c for c in flow_kids if _is_fab_overlay(c)]
            flow_non_fab = [c for c in flow_kids if c not in fab_kids]
            bot_bars = [
                c for c in flow_non_fab
                if c.props.get("l_v_gravity") == "bottom"
                or c.vid == "bottom_navigation_holder"
                or c.props.get("role") == "navbar"
            ]
            top_bars = [
                c for c in flow_non_fab
                if c not in bot_bars
                and ("AppBarLayout" in str(c.props.get("full_cls") or c.cls) or c.props.get("role") == "topbar")
            ]
            main_kids = [c for c in flow_non_fab if c not in bot_bars and c not in top_bars]
            top_end = y + py
            for c in top_bars:
                ch = _measure(c, cw)
                if ch > 0:
                    c.props["$fixed_y"] = True
                    self._lay(c, x + px, top_end, cw)
                    top_end += ch + gap
            vp_h = max(mh, int(self.height)) if node is self.root else mh
            bot_heights = [_measure(c, cw) for c in bot_bars]
            bot_tot = sum(hi for hi in bot_heights if hi > 0) + gap * max(0, sum(1 for hi in bot_heights if hi > 0) - 1)
            bot_start = max(top_end, y + min(mh, int(self.height)) - py - bot_tot)
            cy_b = bot_start
            for c, ch in zip(bot_bars, bot_heights):
                if ch <= 0:
                    c.bounds = (x + px, cy_b, cw, 0)
                    continue
                c.props["$fixed_y"] = True
                if not c.props.get("bg_drawable") and not c.props.get("role"):
                    c.props["role"] = "navbar"
                self._lay(c, x + px, cy_b, cw)
                cy_b += ch + gap
            avail_mid = max(0, bot_start - top_end - (gap if bot_bars and main_kids else 0))
            cy_m = top_end
            for c in main_kids:
                if len(main_kids) == 1 or "ViewPager" in c.cls or c.props.get("xml_h_mode") == "match":
                    c.props["$fill_h"] = True
                    eff_h = max(_measure(c, cw), avail_mid) if (len(main_kids) == 1 or "ViewPager" in c.cls or c.props.get("xml_h_mode") == "match") else avail_mid
                    self._lay(c, x + px, cy_m, cw, forced_h=eff_h)
                    cy_m += eff_h + gap
                else:
                    ch = _measure(c, cw)
                    self._lay(c, x + px, cy_m, cw)
                    if ch > 0:
                        cy_m += ch + gap
            total_coord_h = max(mh, vp_h, (cy_m - y) + (bot_tot if bot_bars else 0) + py)
            node.bounds = (x, y, w, total_coord_h)
            for c in fab_kids:
                fab_w = max(44, min(64, int(c.props.get("w") or 56)))
                fab_h = max(44, min(64, int(c.props.get("h") or 56)))
                l_h = c.props.get("l_h_gravity")
                if l_h == "start":
                    fx = x + px
                elif l_h == "center":
                    fx = x + px + max(0, (cw - fab_w) // 2)
                else:
                    fx = x + px + max(0, cw - fab_w)
                mb = max(int(c.props.get("margin_b") or c.props.get("xml_margin_b") or 0), (bot_tot + 8) if bot_bars else 12)
                fy = max(y + py, y + min(mh, int(self.height)) - mb - fab_h)
                c.props["$fixed_y"] = True
                if c.children:
                    self._lay(c, fx, fy, fab_w, forced_h=fab_h)
                c.bounds = (fx, fy, fab_w, fab_h)
            for c in kids:
                if _is_overlay_scrim_or_click(c, node):
                    c.bounds = (x, y, w, total_coord_h)
            return
        cols = int(node.props.get("span_count") or 1)
        if cols >= 2:
            grid_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
            cw = max(20, w - 2 * px)
            cell_w = max(40, int((cw - gap * (cols - 1)) // cols))
            cy_g = y + py
            for row_items in _pack_grid_rows(grid_kids, cols):
                row_h = max(
                    (_measure(c, cw if sp == cols else (cell_w * sp + gap * (sp - 1))) for c, _, sp in row_items),
                    default=0,
                )
                if row_h <= 0:
                    for c, col_off, sp in row_items:
                        iw = cw if sp == cols else int(cell_w * sp + gap * (sp - 1))
                        c.bounds = (x + px + col_off * (cell_w + gap), cy_g, iw, 0)
                    continue
                for c, col_off, sp in row_items:
                    iw = cw if sp == cols else int(cell_w * sp + gap * (sp - 1))
                    cx_g = x + px + col_off * (cell_w + gap)
                    self._lay(c, cx_g, cy_g, iw, forced_h=row_h)
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
            kids = self._order_toolbar_kids(node, kids)
            inner = max(20, w - pl - pr)
            widths = self._horizontal_widths(node, kids, inner, gap)
            cx = x + pl
            cy_row = y
            if is_full_h and any(c.props.get("$cl_bot_parent") for c in kids):
                row_h = max(_measure(c, cw) for c, cw in zip(kids, widths))
                cy_row = y + max(0, mh - row_h - 14)
                node.props["$draw_center_divider_y"] = (cy_row + 6, max(8, row_h - 12))
            for c, cw in zip(kids, widths):
                ch = _measure(c, cw)
                cy_off = (0 if cy_row > y else (max(0, (mh - ch) // 2) if node.props.get("role") in ("topbar", "navbar") or "Toolbar" in (node.cls or "") or len(kids) >= 2 else pt_pad))
                self._lay(c, cx, cy_row + cy_off, cw)
                cx += cw + gap
        else:
            cy = y + pt_pad
            cw = max(20, w - pl - pr)
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
                    xh_ic = int(c.props.get("xml_h") or 0)
                    if is_ic and c.clickable and not w_prop and xw and int(xw) <= 36 and 16 <= xh_ic <= 36:
                        bw = max(32, int(xw))
                    else:
                        bw = int(xw) if (w_prop and 0 < w_prop <= 56) else (44 if is_wrap else max(48, min(cw_avail, int(xw))))
                    if "$cl_h_bias" in c.props:
                        bx = x + px + int(round((cw_avail - bw) * float(c.props["$cl_h_bias"])))
                    elif c.props.get("$cl_start_parent") and not c.props.get("$cl_end_parent"):
                        bx = x + px + (4 if bw <= 36 and px <= 10 else 0)
                    elif c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent"):
                        bx = x + px + cw_avail - bw - (4 if bw <= 36 and px <= 10 else 0)
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
                def _is_cl_bot_bar(c):
                    if "ViewPager" in c.cls or c.cls in ("RecyclerView", "ScrollView"):
                        return False
                    return bool(
                        c.props.get("$cl_bot_parent")
                        and not c.props.get("$cl_top_parent")
                        and not c.props.get("$cl_top_to_bot")
                        and not c.props.get("$cl_top_to_top")
                    )

                bot_ids = {
                    c.vid for c in node.children
                    if c.vid and (
                        _is_cl_bot_bar(c)
                        or (c.props.get("non_visual") and "bot" in str(c.vid).lower())
                    )
                }
                bot_set = set()
                for c in kids:
                    if _is_cl_bot_bar(c):
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
                        if not is_scroll_main and ((btt in bot_ids and not c.props.get("$cl_top_parent") and not ttb) or (ttb in bot_ids and not btt)):
                            bot_set.add(id(c))
                            if c.vid:
                                bot_ids.add(c.vid)
                            changed_b = True
                top_k = [c for c in kids if id(c) not in bot_set and c.props.get("$cl_top_parent") and not c.props.get("$cl_bot_parent") and not c.props.get("$cl_bot_to_top") and "ViewPager" not in c.cls and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                top_ids = {c.vid for c in top_k if c.vid}
                changed_t = True
                while changed_t and top_ids:
                    changed_t = False
                    for c in kids:
                        if c in top_k or id(c) in bot_set or _is_concentric_overlay(c, node) or _is_overlay_scrim_or_click(c, node):
                            continue
                        ttb = c.props.get("$cl_top_to_bot")
                        if (
                            ttb in top_ids
                            and not c.props.get("$cl_bot_parent")
                            and not c.props.get("$cl_bot_to_top")
                            and c.props.get("xml_h_mode") not in ("zero", "match")
                            and c.cls not in ("RecyclerView", "ScrollView")
                        ):
                            top_k.append(c)
                            if c.vid:
                                top_ids.add(c.vid)
                            changed_t = True
                bot_k = [c for c in kids if id(c) in bot_set and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                mid_k = [c for c in kids if c not in top_k and c not in bot_k and not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
                packed_v_chain = (
                    not top_k and not bot_k and len(mid_k) >= 2
                    and any(c.props.get("$cl_top_parent") and c.props.get("$cl_bot_to_top") for c in mid_k)
                    and any(c.props.get("$cl_bot_parent") and c.props.get("$cl_top_to_bot") for c in mid_k)
                )
                if top_k or bot_k or packed_v_chain:
                    _cl_guides_all = _cl_guide_splits(node, w)

                    def _single_slot_for(c, cw_avail):
                        act = _is_compact_action(c, cw_avail)
                        if act is not None:
                            bw, bx = act
                            return bx, bw, True
                        if c.props.get("xml_w_mode") == "zero" and _cl_guides_all:
                            gs = c.props.get("$cl_start_to_start")
                            ge = c.props.get("$cl_end_to_end")
                            if (gs in _cl_guides_all) or (ge in _cl_guides_all):
                                g0 = _cl_guides_all.get(gs, float(pl) / max(1.0, float(w)))
                                g1 = _cl_guides_all.get(ge, float(w - pr) / max(1.0, float(w)))
                                if g1 > g0 + 0.08:
                                    gx = x + int(round(w * g0))
                                    gw = max(40, int(round(w * (g1 - g0))))
                                    return gx, gw, False
                        if (
                            c.props.get("xml_w_mode") == "wrap"
                            and c.props.get("$cl_start_parent")
                            and c.props.get("$cl_end_parent")
                            and c.cls in ("CardView", "MaterialCardView", "FrameLayout")
                        ):
                            vis_gc = [gc for gc in c.children if gc.visible and not _is_collapsed_node(gc)]
                            if vis_gc and all((gc.props.get("w") or gc.props.get("xml_w")) for gc in vis_gc):
                                cpl, _, cpr, _ = _node_pad4(c)
                                nat_w = max(
                                    int(gc.props.get("w") or gc.props.get("xml_w")) + 2 * min(24, int(gc.props.get("xml_margin") or 0))
                                    for gc in vis_gc
                                ) + cpl + cpr
                                if 36 <= nat_w < cw_avail:
                                    return x + pl + (cw_avail - nat_w) // 2, nat_w, False
                        return x + pl, cw_avail, False

                    def _place_single(c, cy_pos, cw_avail, max_h_hint=None):
                        sx, sw, is_act = _single_slot_for(c, cw_avail)
                        if is_act and max_h_hint is None:
                            ch_i = _measure(c, sw)
                            xh_raw = int(c.props.get("xml_h") or 0)
                            fh_act = sw if (c.clickable and c.cls in ("ImageView", "ImageButton") and sw <= 36 and 16 <= xh_raw <= 36) else None
                            cy_act = (cy_pos + 4) if (fh_act is not None and cy_pos <= y + 10) else cy_pos
                            self._lay(c, sx, cy_act, sw, forced_h=fh_act)
                            return int(fh_act if fh_act is not None else ch_i) + (4 if cy_act > cy_pos else 0)
                        if (has_icon_tile or node.props.get("gravity") == 1 or packed_v_chain) and c.cls == "TextView":
                            c.props.setdefault("gravity", 1)
                        if node.props.get("gravity") == 1 and not c.is_leaf():
                            c.props.setdefault("gravity", 1)
                        ch_i = _measure(c, sw)
                        self._lay(c, sx, cy_pos, sw, forced_h=max_h_hint)
                        return int(max_h_hint if max_h_hint is not None else ch_i)

                    top_end = y + pt_pad
                    if (len(top_k) == 2 and top_k[0].vid == "back_button"
                            and top_k[1].cls in ("ProgressBar", "SeekBar")):
                        bw_bk = min(44, max(36, int(top_k[0].props.get("xml_w") or 40)))
                        self._lay(top_k[0], x + pl, top_end, bw_bk, forced_h=36)
                        top_k[0].bounds = (x + pl, top_end, bw_bk, 36)
                        px_pb = x + pl + bw_bk + 8
                        pw_pb = max(60, cw - bw_bk - 8)
                        self._lay(top_k[1], px_pb, top_end + 8, pw_pb, forced_h=20)
                        top_end += 36 + gap
                    else:
                        for c in top_k:
                            ch = _place_single(c, top_end, cw)
                            if ch > 0:
                                top_end += ch + gap
                    bot_heights = [_measure(c, _single_slot_for(c, cw)[1]) for c in bot_k]
                    bot_tot = sum(h_i for h_i in bot_heights if h_i > 0) + gap * max(0, sum(1 for h_i in bot_heights if h_i > 0) - 1)
                    bot_start = max(top_end, y + mh - pb_pad - bot_tot)
                    cy_b = bot_start
                    for c in bot_k:
                        ch = _place_single(c, cy_b, cw)
                        if ch > 0:
                            cy_b += ch + gap
                    avail_mid = max(0, bot_start - top_end - (gap if bot_k and top_end > y + pt_pad else 0))
                    mid_heights = [_measure(c, _single_slot_for(c, cw)[1]) for c in mid_k]
                    mid_tot = sum(h_i for h_i in mid_heights if h_i > 0) + gap * max(0, sum(1 for h_i in mid_heights if h_i > 0) - 1)
                    non_empty_mid = [
                        i for i, (_mc, mh_i) in enumerate(zip(mid_k, mid_heights))
                        if mh_i > 0
                    ]
                    expanding_mid = {
                        i for i, c in enumerate(mid_k)
                        if (
                            ("ViewPager" in c.cls and len(mid_k) == 1 and avail_mid > mid_heights[0])
                            or (
                                len(non_empty_mid) == 1
                                and i == non_empty_mid[0]
                                and c.props.get("xml_h_mode") in ("zero", "match")
                                and avail_mid > mid_heights[i]
                            )
                        )
                    }
                    ctr_cands = [
                        i for i in non_empty_mid
                        if mid_k[i].vid and mid_k[i].props.get("$cl_top_parent") and mid_k[i].props.get("$cl_bot_parent")
                    ]
                    if (
                        len(ctr_cands) == 1
                        and not expanding_mid
                        and len(non_empty_mid) >= 2
                        and all(
                            (i == ctr_cands[0])
                            or (i < ctr_cands[0] and mid_k[i].props.get("$cl_bot_to_top") == mid_k[ctr_cands[0]].vid)
                            or (i > ctr_cands[0] and mid_k[i].props.get("$cl_top_to_bot") == mid_k[ctr_cands[0]].vid)
                            for i in non_empty_mid
                        )
                    ):
                        ci_ctr = ctr_cands[0]
                        above_idxs = [i for i in non_empty_mid if i < ci_ctr]
                        below_idxs = [i for i in non_empty_mid if i > ci_ctr]
                        ctr_h = mid_heights[ci_ctr]
                        above_tot = sum(mid_heights[i] for i in above_idxs) + gap * max(0, len(above_idxs) - 1)
                        below_tot = sum(mid_heights[i] for i in below_idxs) + gap * max(0, len(below_idxs) - 1)
                        ctr_bias = float(mid_k[ci_ctr].props.get("$cl_v_bias", 0.5))
                        ctr_y = y + int(round(max(0, mh - ctr_h) * ctr_bias))
                        max_ctr_y = max(top_end, bot_start - (below_tot + gap if below_idxs else 0) - ctr_h)
                        min_ctr_y = top_end + (above_tot + gap if above_idxs else 0)
                        if min_ctr_y > max_ctr_y:
                            ctr_y = max_ctr_y
                        else:
                            ctr_y = max(min_ctr_y, min(max_ctr_y, ctr_y))
                        for i_z in range(len(mid_k)):
                            if mid_heights[i_z] <= 0:
                                mid_k[i_z].bounds = (x + pl, top_end, cw, 0)
                        if above_idxs:
                            avail_ab = max(24, ctr_y - gap - top_end)
                            ab_bias = float(mid_k[above_idxs[0]].props.get("$cl_v_bias", 0.5))
                            cy_ab = top_end + int(round(max(0, avail_ab - above_tot) * ab_bias))
                            for ia in above_idxs:
                                h_cap = min(mid_heights[ia], avail_ab) if len(above_idxs) == 1 else None
                                ch_a = _place_single(mid_k[ia], cy_ab, cw, max_h_hint=h_cap)
                                cy_ab += ch_a + gap
                        _place_single(mid_k[ci_ctr], ctr_y, cw)
                        if below_idxs:
                            bel_top = ctr_y + ctr_h + gap
                            avail_bl = max(24, bot_start - bel_top)
                            bl_bias = float(mid_k[below_idxs[0]].props.get("$cl_v_bias", 0.5))
                            cy_bl = bel_top + int(round(max(0, avail_bl - below_tot) * bl_bias))
                            for ib in below_idxs:
                                ch_b = _place_single(mid_k[ib], cy_bl, cw)
                                cy_bl += ch_b + gap
                        _apply_overlays(mh)
                        return
                    should_center = (
                        len(mid_k) >= 1
                        and mid_tot > 0
                        and mid_tot < avail_mid - 20
                        and not expanding_mid
                        and all("ViewPager" not in c.cls and c.cls not in ("RecyclerView", "ScrollView") for c in mid_k)
                    )
                    vb_vals = [float(c.props["$cl_v_bias"]) for c in mid_k if "$cl_v_bias" in c.props]
                    if vb_vals and avail_mid > mid_tot and not expanding_mid:
                        cy_m = top_end + int(round(max(0, avail_mid - mid_tot) * vb_vals[0]))
                    else:
                        cy_m = top_end + max(0, (avail_mid - mid_tot) // 2) if should_center else top_end
                    for idx_m, c in enumerate(mid_k):
                        if mid_heights[idx_m] <= 0 and idx_m not in expanding_mid:
                            c.bounds = (x + pl, cy_m, cw, 0)
                            continue
                        if idx_m in expanding_mid:
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
                self._lay(c, x + pl, cy_pos, cw_avail, forced_h=forced_ch)
                return int(forced_ch if forced_ch is not None else ch_i)

            flow_kids = [c for c in kids if not _is_concentric_overlay(c, node) and not _is_overlay_scrim_or_click(c, node)]
            if (
                node.cls in ("CardView", "MaterialCardView", "FrameLayout")
                and len(flow_kids) == 1
                and flow_kids[0].is_leaf()
                and (flow_kids[0].props.get("xml_margin") or "w" in flow_kids[0].props)
            ):
                fc0 = flow_kids[0]
                m_in0 = min(24, max(0, int(fc0.props.get("xml_margin") or 0)))
                avail_fc_w = max(16, w - pl - pr - 2 * m_in0)
                fc_w = min(avail_fc_w, int(fc0.props["w"])) if ("w" in fc0.props and int(fc0.props["w"]) > 0) else avail_fc_w
                fc_h = _measure(fc0, fc_w)
                cx_fc = x + max(pl + m_in0, (w - fc_w) // 2)
                cy_fc = y + max(pt_pad + m_in0, (mh - fc_h) // 2)
                self._lay(fc0, cx_fc, cy_fc, fc_w, forced_h=fc_h)
                _apply_overlays(mh)
                return
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
                self._lay(fc, x + pl, y + pt_pad, cw, forced_h=max(0, mh - pt_pad - pb_pad))
                _apply_overlays(mh)
                return

            if node.props.get("compose_kind") == "Box" and len(flow_kids) >= 1 and (
                len(flow_kids) >= 2
                or any(k in flow_kids[0].props for k in ("$box_align", "w", "$pad_l", "$pad_t", "$pad_r", "$pad_b"))
            ):
                box_h = max(0, mh - pt_pad - pb_pad)
                center_box = bool(
                    node.props.get("$compose_align") == "center"
                    or any(k.cls == "ComposeCanvas" for k in flow_kids)
                )
                for c in flow_kids:
                    if (c.props.get("$fill_max_h") or c.props.get("$match_parent") or c.cls == "ComposeCanvas") and not c.props.get("$aspect_ratio"):
                        c.props["$fill_h"] = True
                        self._lay(c, x + pl, y + pt_pad, cw, forced_h=box_h)
                    else:
                        c_al = str(c.props.get("$box_align") or "")
                        if c.is_leaf() and "w" in c.props and not c.props.get("$fill_max_w"):
                            w_vis = max(1, min(cw, int(c.props["w"])))
                            ml_c = int(c.props.get("$pad_l", 0) or 0)
                            mt_c = int(c.props.get("$pad_t", 0) or 0)
                            mr_c = int(c.props.get("$pad_r", 0) or 0)
                            mb_c = int(c.props.get("$pad_b", 0) or 0)
                            ch_vis = _measure(c, w_vis)
                            if "bottom" in c_al:
                                cy_c = y + pt_pad + max(0, box_h - ch_vis - mb_c)
                            elif ("center" in c_al and "top" not in c_al) or (not c_al and center_box and not c.props.get("$cl_start_parent")):
                                cy_c = y + pt_pad + max(0, (box_h - ch_vis) // 2)
                            else:
                                cy_c = y + pt_pad + mt_c
                            if "end" in c_al or (not c_al and c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent")):
                                cx_c = x + pl + max(0, cw - w_vis - mr_c)
                            elif c_al in ("top_center", "center", "bottom_center") or (not c_al and center_box and not c.props.get("$cl_start_parent")):
                                cx_c = x + pl + max(0, (cw - w_vis) // 2)
                            else:
                                cx_c = x + pl + ml_c
                            self._lay(c, cx_c, cy_c, w_vis, forced_h=ch_vis)
                            continue
                        cw_c = int(c.props["w"]) if ("w" in c.props and 0 < int(c.props["w"]) < cw) else cw
                        if (cw_c == cw and c.props.get("compose_kind") == "Box"
                                and not c.props.get("$fill_max_w") and not c.props.get("$match_parent")):
                            gc_vis = [gc for gc in c.children if gc.visible and not _is_collapsed_node(gc)]
                            if gc_vis and all(gc.is_leaf() and "w" in gc.props and not gc.props.get("$fill_max_w") for gc in gc_vis):
                                nat_w = max(
                                    int(gc.props["w"]) + int(gc.props.get("$pad_l", 0) or 0) + int(gc.props.get("$pad_r", 0) or 0)
                                    for gc in gc_vis
                                ) + int(c.props.get("$pad_l", 0) or 0) + int(c.props.get("$pad_r", 0) or 0)
                                if 0 < nat_w < cw:
                                    cw_c = nat_w
                        ch_c = _measure(c, cw_c)
                        if "bottom" in c_al:
                            cy_c = y + pt_pad + max(0, box_h - ch_c)
                        elif ("center" in c_al and "top" not in c_al) or (not c_al and center_box and not c.props.get("$cl_start_parent")):
                            cy_c = y + pt_pad + (max(0, (box_h - ch_c) // 2) if ch_c < box_h else 0)
                        else:
                            cy_c = y + pt_pad
                        if cw_c < cw:
                            if "end" in c_al or (not c_al and c.props.get("$cl_end_parent") and not c.props.get("$cl_start_parent")):
                                cx_c = x + pl + cw - cw_c
                            elif c_al in ("top_center", "center", "bottom_center") or (not c_al and center_box and not c.props.get("$cl_start_parent")):
                                cx_c = x + pl + (cw - cw_c) // 2
                            else:
                                cx_c = x + pl
                            self._lay(c, cx_c, cy_c, cw_c)
                        else:
                            _place_v_child(c, cy_c, cw)
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
                avail_wt = max(0, (mh - pt_pad - pb_pad) - fixed_h - gap * max(0, len(flow_kids) - 1))
                cy_w = y + pt_pad
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
                    if cand.props.get("$compose_spacer") and tail_start < len(flow_kids):
                        tail_start -= 1
                        continue
                    has_btn_or_click = any(s.cls == "Button" or (s.cls == "TextView" and s.clickable) for s in cand.walk())
                    has_card = any(s.props.get("role") == "card" for s in cand.walk())
                    if has_btn_or_click and not has_card:
                        tail_start -= 1
                    else:
                        break
                while tail_start < len(flow_kids) and flow_kids[tail_start].props.get("$compose_spacer"):
                    tail_start += 1
                if tail_start < len(flow_kids):
                    ch_list = [_measure(c, cw) for c in flow_kids]
                    nat_tot = sum(hi for hi in ch_list if hi > 0) + gap * max(0, sum(1 for hi in ch_list if hi > 0) - 1)
                    if nat_tot < (mh - pt_pad - pb_pad) - 80:
                        top_k_c = flow_kids[:1] if tail_start >= 1 else []
                        mid_k_c = flow_kids[1:tail_start] if tail_start > 1 else []
                        tail_k_c = flow_kids[tail_start:]
                        top_end = y + pt_pad
                        for c in top_k_c:
                            ch_i = _place_v_child(c, top_end, cw)
                            if ch_i > 0:
                                top_end += ch_i + gap
                        tail_hs = [_measure(c, cw) for c in tail_k_c]
                        tail_tot = sum(hi for hi in tail_hs if hi > 0) + gap * max(0, sum(1 for hi in tail_hs if hi > 0) - 1)
                        bot_start = max(top_end, y + mh - pb_pad - tail_tot - 12)
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
                lt_sp = _cl_lead_thumb_split(node, flow_kids, cw)
                if lt_sp is not None:
                    lead, lead_w, lead_h, rest_c, col_gap, col_w, rhs, rgap, right_h, inner_h = lt_sp
                    ly = cy + max(0, (inner_h - lead_h) // 2)
                    self._lay(lead, x + pl, ly, lead_w, forced_h=lead_h)
                    ry = cy + max(0, (inner_h - right_h) // 2)
                    rx = x + pl + lead_w + col_gap
                    for rc, rh_i in zip(rest_c, rhs):
                        if rh_i <= 0:
                            rc.bounds = (rx, ry, col_w, 0)
                            continue
                        self._lay(rc, rx, ry, col_w, forced_h=rh_i)
                        ry += rh_i + rgap
                    _apply_overlays(max(mh, cy + inner_h + pb_pad - y))
                    return
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
        with self._lock:
            return list(self.root.walk()) if self.root else []

    def visible_nodes(self):
        with self._lock:
            return list(self.root.walk_visible()) if self.root else []

    def content_height(self):
        """Gesamthoehe des gelayouteten Inhalts (fuer vertikalen Scroll)."""
        with self._lock:
            if self.root is None:
                return 0
            self.ensure_layout()
            return self.root._bounds[3]

    def by_id(self, vid):
        """Sucht einen Knoten anhand seines String- oder Integer-Ressourcen-ID."""
        with self._lock:
            if self.root is None or vid is None:
                return None
            if isinstance(vid, int):
                idmap = getattr(self, "_idmap_id_ref", None)
                if idmap is None:
                    stubs = getattr(self, "_stubs_ref", None)
                    idmap = getattr(stubs, "_res_idmap", None) if stubs is not None else None
                if isinstance(idmap, dict):
                    mapped = idmap.get(vid)
                    if mapped is not None:
                        found = self.root.find(mapped)
                        if found is not None:
                            return found
            return self.root.find(vid)

    @staticmethod
    def _hit_node_in(nodes, x, y):
        hit = None
        for n in nodes:
            if not n.visible:
                continue
            bx, by, bw, bh = n.bounds
            if bx <= x < bx + bw and by <= y < by + bh:
                hit = n
        return hit

    def _hit_clickable_in(self, nodes, x, y):
        best = None
        for n in nodes:
            if not (n.visible and n.clickable):
                continue
            bx, by, bw, bh = n.bounds
            if bx <= x < bx + bw and by <= y < by + bh:
                best = n
        if best is None:
            hit = self._hit_node_in(nodes, x, y)
            cur = getattr(hit, "_parent", None) if hit is not None else None
            hops = 0
            while cur is not None and hops < 4:
                overlay = [
                    s for s in getattr(cur, "children", ()) or ()
                    if s.visible and s.clickable and (s.vid == "click_overlay" or (s.cls == "View" and not s.children))
                ]
                if len(overlay) == 1:
                    return overlay[0]
                if hops == 0 and len(cur.children) <= 3:
                    c_sibs = [s for s in cur.children if s.visible and s.clickable]
                    if len(c_sibs) == 1 and (
                        _paint_widget_cls(c_sibs[0].cls)
                        in ("Switch", "CheckBox", "RadioButton", "ToggleButton", "CompoundButton")
                        or _is_pref_row(cur)
                        or (
                            cur.cls in ("RelativeLayout", "FrameLayout")
                            and _paint_widget_cls(c_sibs[0].cls) in ("Button", "ImageButton")
                            and not (c_sibs[0].text or "").strip()
                        )
                    ):
                        return c_sibs[0]
                cur = getattr(cur, "_parent", None)
                hops += 1
        return best

    def fixed_node_at(self, vx, vy):
        """Tiefster sichtbarer fixierter Knoten ($fixed_y) bei Viewport-(vx,vy) oder None."""
        with self._lock:
            self.ensure_layout()
            return self._hit_node_in([n for n in self.visible_nodes() if _is_fixed_y_node(n)], vx, vy)

    def fixed_clickable_at(self, vx, vy):
        """Tiefster sichtbarer, klickbarer fixierter Knoten ($fixed_y) bei Viewport-(vx,vy) oder None."""
        with self._lock:
            self.ensure_layout()
            return self._hit_clickable_in([n for n in self.visible_nodes() if _is_fixed_y_node(n)], vx, vy)

    def node_at(self, x, y):
        """Tiefster sichtbarer Knoten, dessen Bounds (x,y) enthalten."""
        with self._lock:
            self.ensure_layout()
            return self._hit_node_in(self.visible_nodes(), x, y)

    def clickable_at(self, x, y):
        """Tiefster sichtbarer, klickbarer Knoten bei (x,y) (fuer Input-Injektion)."""
        with self._lock:
            self.ensure_layout()
            return self._hit_clickable_in(self.visible_nodes(), x, y)


def paint_commands(tree):
    """Reine Zeichenbefehl-Liste fuer das Anzeige-Fenster (headless testbar).
    Befehle: {op:'rect',x,y,w,h,fill,outline,r} und {op:'text',x,y,text,color,anchor}.
    Container vor Kindern (Pre-Order) -> Kinder liegen oben."""
    if tree is None or tree.root is None:
        return []
    lock = getattr(tree, "_lock", None)
    if lock is not None:
        lock.acquire()
    try:
        if hasattr(tree, "ensure_layout"):
            tree.ensure_layout()
        return _paint_commands_locked(tree)
    finally:
        if lock is not None:
            lock.release()


def _paint_commands_locked(tree):
    cmds = []
    if tree is None or tree.root is None:
        return cmds
    _pc = _build_pref_cache(tree.root)
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
                    if _paint_widget_cls(sub.cls) in ("ImageView", "ImageButton"):
                        sp = sub.props.get("drawable")
                        if sub.props.get("img_bytes") or (isinstance(sp, dict) and sp.get("kind") == "raster"):
                            return True
                    sbs = sub.props.get("bg_drawable")
                    sa = float(sub.props.get("bg_alpha", 1.0) if sub.props.get("bg_alpha") is not None else 1.0)
                    sa *= float(sub.props.get("alpha", 1.0) if (sub.cls != "View" and sub.props.get("alpha") is not None) else 1.0)
                    if isinstance(sbs, dict) and sa >= 0.25:
                        if sbs.get("kind") in ("color", "shape"):
                            scol = sbs.get("color") if sbs.get("kind") == "color" else sbs.get("fill")
                            if scol:
                                lum = _hex_luma(scol, require_opaque=False)
                                sa2 = float(sbs.get("alpha", 1.0) if sbs.get("alpha") is not None else 1.0)
                                if lum is not None and lum < 140.0 and sa * sa2 >= 0.25:
                                    return True
                        elif sbs.get("kind") == "raster":
                            return True
            child_on_path = cur
            cur = getattr(cur, "_parent", None)
        return False

    def _is_light_hex(c):
        lum = _hex_luma(c)
        return bool(lum is not None and lum > 210.0)

    _vis_paint_nodes = [
        n for n in tree.visible_nodes()
        if n.visible and not _is_collapsed_node(n) and not n.props.get("$occluded_by_blur")
    ]
    for _fixed_pass, _pass_nodes in (
        (False, [n for n in _vis_paint_nodes if not _is_fixed_y_node(n)]),
        (True, [n for n in _vis_paint_nodes if _is_fixed_y_node(n)]),
    ):
      _pass_cmd_start = len(cmds)
      for n in _pass_nodes:
        x, y, w, h = n.bounds
        if laid_out and (w <= 0 or h <= 0):
            continue
        cls = _paint_widget_cls(n.cls)
        if cls == "EditText" and n.children:
            _sync_composite_edittext(n)
            continue
        if cls == "Button" and n.children and not (n.text or "").strip():
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
                    ph = min(max(20, h - 10), 26)
                    px0 = x + (w - pw) / 2
                    py0 = y + (h - ph) / 2
                    cmds.append({"op": "rect", "x": px0, "y": py0, "w": pw, "h": ph,
                                 "fill": "#364156" if dark else "#d8e3ff",
                                 "outline": "#506282" if dark else "#9bb5f5", "r": ph / 2})
                cmds.append({"op": "text", "x": x + w / 2, "y": y + h / 2,
                             "text": lbl,
                             "color": ("#d8e2ff" if sel else "#9ca3af") if dark else ("#1b4fb8" if sel else "#4b5563"),
                             "anchor": "c", "font_size": 9, "bold": sel})
            elif prole == "topbar":
                is_ic_only = bool(bg_spec and bg_spec.get("kind") in ("vector", "raster") and not (n.text or "").strip())
                if not (is_ic_only and "Borderless" in str(n.props.get("style") or "")):
                    cmds.append({"op": "rect", "x": x + 2, "y": y + 2, "w": w - 4, "h": h - 4,
                                 "fill": "#222731" if dark else "#eef1f6",
                                 "outline": "#2e3543" if dark else "#d0d6e2", "r": 6})
                if is_ic_only:
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
                    elif isinstance(n.props.get("bg"), str) and n.props["bg"].startswith("#"):
                        b_fill = n.props["bg"]
                        b_out = n.props.get("outline") or ("#9bb5f5" if _is_light_hex(b_fill) else b_fill)
                    from core.java.drawable_loader import _is_very_light
                    if _is_light_hex(b_fill) or _is_very_light(b_fill):
                        cand_c = n.props.get("color")
                        b_txt_col = "#1b2230" if (not cand_c or _is_light_hex(cand_c) or _is_very_light(cand_c)) else cand_c
                        if b_out == b_fill:
                            b_out = "#c3c9d6"
                    elif n.props.get("color"):
                        cand_c = n.props["color"]
                        b_txt_col = "#1b2230" if (_is_very_light(cand_c) and _is_light_hex(b_fill)) else cand_c
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
            iv_dark = True if cls == "ImageButton" else (dark if in_card_iv else (True if (not win_dark and _has_dark_backdrop(n)) else win_dark))
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
                    or (not ib_bg_ref and (
                        _has_dark_backdrop(n)
                        or (par is not None and ("Toolbar" in (par.cls or "") or par.props.get("role") == "topbar"))
                    ))
                )
                iv_tint = n.props.get("tint") or (spec.get("tint") if isinstance(spec, dict) else None)
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
            tv_dark = dark if (in_card or prole in ("topbar", "navbar")) else (True if (not win_dark and _has_dark_backdrop(n)) else win_dark)
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
            elif n.vid == "title" and par is not None and (_pc[id(par)][1] if id(par) in _pc else par.find("summary") is not None):
                fsize, bold = 10, False
                anc, tx = "w", x + 4
            elif n.vid == "title" and par is not None and par.cls == "LinearLayout" and not (_pc[id(par)][2] if id(par) in _pc else par.find("widget_frame") is not None):
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
        elif cls == "ComposeCanvas":
            crec = n.props.get("_compose_canvas_rec")
            cw0, ch0 = n.props.get("_compose_canvas_size") or (float(w or 1), float(h or 1))
            sx_c = float(w) / float(cw0) if cw0 > 0 else 1.0
            sy_c = float(h) / float(ch0) if ch0 > 0 else 1.0
            if crec is not None and hasattr(crec, "paint"):
                for rc in crec.paint():
                    c2 = dict(rc)
                    if "x" in c2:
                        c2["x"] = x + float(c2["x"]) * sx_c
                    if "y" in c2:
                        c2["y"] = y + float(c2["y"]) * sy_c
                    if "w" in c2:
                        c2["w"] = float(c2["w"]) * sx_c
                    if "h" in c2:
                        c2["h"] = float(c2["h"]) * sy_c
                    if "x2" in c2:
                        c2["x2"] = x + float(c2["x2"]) * sx_c
                    if "y2" in c2:
                        c2["y2"] = y + float(c2["y2"]) * sy_c
                    if c2.get("op") == "text" and "size" in c2 and "font_size" not in c2:
                        c2["font_size"] = max(7, min(14, int(round(float(c2["size"]) * 0.75))))
                    cmds.append(c2)
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
                _n_al = float(n.props.get("alpha", 1.0) if (n.cls != "View" and n.props.get("alpha") is not None) else 1.0)
                bg_alpha = float(n.props.get("bg_alpha", 1.0) if n.props.get("bg_alpha") is not None else 1.0) * _n_al
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
      if _fixed_pass:
          for _fc in cmds[_pass_cmd_start:]:
              _fc["fixed_y"] = True
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
    tree._stubs_ref = stubs
    stubs._app_root = app_root

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
            for k in ("$text", "text", "value", "$uri", "_uri"):
                tv = v.fields.get(k)
                if tv is not None:
                    return tv.text if isinstance(tv, JString) else str(tv)
            return ""
        return str(v)

    def _f32_val(raw, default=1.0):
        if isinstance(raw, float):
            return default if math.isnan(raw) else raw
        if isinstance(raw, int):
            if raw == 0:
                return 0.0
            if -1 <= raw <= 10:
                return float(raw)
            try:
                fval = float(_struct.unpack("<f", _struct.pack("<I", raw & 0xFFFFFFFF))[0])
                return default if math.isnan(fval) else fval
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
                v = a[1]
                n.text = "" if (v is None or (isinstance(v, int) and not isinstance(v, bool) and v == 0)) else _text(v)
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
                if n.cls == "View":
                    n.props["bg_alpha"] = al
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
                try:
                    p.add(c)
                except ValueError:
                    from core.java.dalvik import DalvikException
                    raise DalvikException("java.lang.IllegalStateException: child cycle or already has parent")
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
    tree._idmap_id_ref = _idmap_id_ref
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

    def _is_act_finishing():
        ca = getattr(stubs, "_current_activity", None)
        return bool(getattr(stubs, "_activity_finished", False)) or (
            isinstance(ca, JObject) and bool(ca.fields.get("_finishing"))
        )

    def _mount_fragment_instance(frag, container_node, tag=None):
        if _is_act_finishing():
            return False
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
        if _is_act_finishing():
            if txs:
                txs.clear()
            return False
        if not txs:
            return False
        did = False
        vm_obj = getattr(stubs, "vm", None)
        cur_act = getattr(stubs, "_current_activity", None)
        while txs:
            tx = txs.pop(0)
            fm = tx.get("fm")
            host_node = fm.fields.get("_host_node") if isinstance(fm, JObject) else None
            is_child_fm = bool(fm.fields.get("_is_child_fm")) if isinstance(fm, JObject) else False
            fm_list = fm.fields.setdefault("_fragments", []) if isinstance(fm, JObject) else None
            fm_by_id = fm.fields.setdefault("_by_id", {}) if isinstance(fm, JObject) else None
            fm_by_tag = fm.fields.setdefault("_by_tag", {}) if isinstance(fm, JObject) else None
            for op in tx.get("ops", []):
                kind = op[0]
                if kind in ("replace", "add"):
                    cid = op[1]
                    frag = op[2]
                    ftag = op[3] if len(op) > 3 else None
                    if isinstance(fm, JObject) and isinstance(frag, JObject):
                        if kind == "replace" and cid is not None:
                            prev = fm_by_id.get(cid)
                            if prev is None and isinstance(cid, int) and cid in _idmap_id_ref:
                                prev = fm_by_id.get(_idmap_id_ref[cid])
                            if isinstance(prev, JObject) and prev is not frag and prev in fm_list:
                                fm_list.remove(prev)
                        if cid is not None and frag not in fm_list:
                            fm_list.append(frag)
                        frag.fields["_parent_fm"] = fm
                        if ftag:
                            fm_by_tag[str(ftag)] = frag
                            if not is_child_fm:
                                if not hasattr(stubs, "_fragments_by_tag"):
                                    stubs._fragments_by_tag = {}
                                stubs._fragments_by_tag[str(ftag)] = frag
                        if cid is not None:
                            fm_by_id[cid] = frag
                            if isinstance(cid, int) and cid in _idmap_id_ref:
                                fm_by_id[_idmap_id_ref[cid]] = frag
                            if not is_child_fm:
                                stubs._fragments_by_id[cid] = frag
                                if isinstance(cid, int) and cid in _idmap_id_ref:
                                    stubs._fragments_by_id[_idmap_id_ref[cid]] = frag
                    if cid is None:
                        if isinstance(frag, JObject):
                            frag.fields["$is_fragment"] = True
                            frag.fields.setdefault("_host_act", cur_act)
                            if vm_obj is not None and not frag.fields.get("$frag_created"):
                                frag.fields["$frag_created"] = True
                                _safe_frag_call(vm_obj, frag, "onAttach", "(Landroid/content/Context;)V", (frag, cur_act))
                                _safe_frag_call(vm_obj, frag, "onCreate", "(Landroid/os/Bundle;)V", (frag, None))
                                _safe_frag_call(vm_obj, frag, "onStart", "()V", (frag,))
                                _safe_frag_call(vm_obj, frag, "onResume", "()V", (frag,))
                            did = True
                        continue
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
                        _mount_fragment_instance(frag, cnode, tag=ftag)
                        did = True
                elif kind == "remove" and len(op) > 1 and isinstance(op[1], JObject):
                    rfrag = op[1]
                    if fm_list is not None and rfrag in fm_list:
                        fm_list.remove(rfrag)
                    if fm_by_id is not None:
                        for dk in [k for k, v in fm_by_id.items() if v is rfrag]:
                            fm_by_id.pop(dk, None)
                    if fm_by_tag is not None:
                        for tk in [k for k, v in fm_by_tag.items() if v is rfrag]:
                            fm_by_tag.pop(tk, None)
                    vo = rfrag.fields.get("_view")
                    if isinstance(vo, JObject) and "$view" in vo.fields:
                        vn_r = vo.fields["$view"]
                        par = getattr(vn_r, "_parent", None)
                        if par is not None and vn_r in par.children:
                            par.children.remove(vn_r)
                            tree.touch()
                    did = True
                elif kind in ("show", "hide") and isinstance(op[1], JObject):
                    vo = op[1].fields.get("_view")
                    if isinstance(vo, JObject) and "$view" in vo.fields:
                        vo.fields["$view"].visible = (kind == "show")
                        vo.fields["$view"].props["visibility"] = 0 if kind == "show" else 8
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
    def _sync_lp_to_node(vn_tgt, lp_obj):
        if vn_tgt is None or not isinstance(lp_obj, JObject):
            return
        dr = lp_obj.fields.get("dimensionRatio")
        if dr is not None:
            dr_s = _text(dr).strip()
            if dr_s:
                vn_tgt.props["$cl_ratio"] = dr_s
        tm = lp_obj.fields.get("topMargin")
        if isinstance(tm, int) and tm > 0:
            vn_tgt.props["margin_t"] = min(64, int(tm))
        tw_vp = max(320, int(getattr(tree, "width", 380) or 380))
        th_vp = max(480, int(getattr(tree, "height", 720) or 720))
        lp_w = lp_obj.fields.get("width")
        lp_h = lp_obj.fields.get("height")
        par_vn = getattr(vn_tgt, "_parent", None)
        in_wrap_card = (
            par_vn is not None
            and par_vn.cls in ("CardView", "MaterialCardView", "FrameLayout")
            and par_vn.props.get("xml_w_mode") == "wrap"
        )
        if isinstance(lp_w, int) and lp_w > 0:
            dp_w = int(round(lp_w * (tw_vp / 1080.0))) if lp_w > tw_vp else int(lp_w)
            if in_wrap_card and isinstance(lp_h, int) and lp_h == lp_w:
                dp_w = min(224, max(40, dp_w))
            else:
                dp_w = max(12, min(tw_vp, dp_w))
            vn_tgt.props["w"] = dp_w
        if isinstance(lp_h, int) and lp_h > 0:
            if isinstance(lp_w, int) and lp_w == lp_h and "w" in vn_tgt.props:
                vn_tgt.props["h"] = int(vn_tgt.props["w"])
            else:
                dp_h = int(round(lp_h * (tw_vp / 1080.0))) if (lp_h > th_vp or (isinstance(lp_w, int) and lp_w > tw_vp)) else int(lp_h)
                vn_tgt.props["h"] = max(12, min(th_vp, dp_h))
        tree.touch()

    stubs.register_by_name("setLayoutParams", "(Landroid/view/ViewGroup$LayoutParams;)V",
                           lambda a: (a[0].fields.__setitem__("_lp", a[1]),
                                      _sync_lp_to_node(a[0].fields.get("$view"), a[1]))[1]
                           if (a and len(a) > 1 and isinstance(a[0], JObject)) else None)
    def _lp_set_margins(a):
        if len(a) >= 5 and isinstance(a[0], JObject):
            a[0].fields["leftMargin"] = int(a[1] or 0)
            a[0].fields["topMargin"] = int(a[2] or 0)
            a[0].fields["rightMargin"] = int(a[3] or 0)
            a[0].fields["bottomMargin"] = int(a[4] or 0)
        return None
    stubs.register_by_name("setMargins", "(IIII)V", _lp_set_margins)

    def _refresh_decor_after_root():
        cands = [getattr(stubs, "_window_singleton", None)]
        cur_act = getattr(stubs, "_current_activity", None)
        if isinstance(cur_act, JObject):
            cands.append(cur_act.fields.get("_window"))
        for w_obj in cands:
            if isinstance(w_obj, JObject):
                dc = w_obj.fields.get("decor")
                if isinstance(dc, JObject) and dc.fields.get("$decor_placeholder") and tree.root is not None:
                    dc.fields["$view"] = tree.root
                    dc.fields.pop("$decor_placeholder", None)

    def _ensure_decor_view(win_obj):
        if not isinstance(win_obj, JObject):
            win_obj = stubs._mk_window() if hasattr(stubs, "_mk_window") else JObject("Landroid/view/Window;", {})
        deco = win_obj.fields.get("decor")
        if not isinstance(deco, JObject):
            deco = JObject("Landroid/view/View;", {"__role": "DecorView"})
            win_obj.fields["decor"] = deco
        if tree.root is not None:
            if deco.fields.get("$view") is None or deco.fields.get("$decor_placeholder"):
                deco.fields["$view"] = tree.root
                deco.fields.pop("$decor_placeholder", None)
        elif deco.fields.get("$view") is None:
            vn = ViewNode(cls="FrameLayout", vid="decor")
            vn._jobj = deco
            vn._tree = tree
            deco.fields["$view"] = vn
            deco.fields["$decor_placeholder"] = True
        return deco

    def _get_decor_view(a):
        win = (
            a[0]
            if (a and isinstance(a[0], JObject) and (
                a[0].cls == "Landroid/view/Window;" or "Window" in (a[0].cls or "") or "decor" in a[0].fields
            ))
            else (stubs._mk_window(a) if hasattr(stubs, "_mk_window") else JObject("Landroid/view/Window;", {}))
        )
        return _ensure_decor_view(win)

    stubs.register("Landroid/view/Window;", "getDecorView", "()Landroid/view/View;", _get_decor_view)
    stubs.register_by_name("getDecorView", "()Landroid/view/View;", _get_decor_view)
    stubs.register(
        "Landroid/view/Window;",
        "peekDecorView",
        "()Landroid/view/View;",
        lambda a: _get_decor_view(a) if (
            tree.root is not None or (a and isinstance(a[0], JObject) and isinstance(a[0].fields.get("decor"), JObject))
        ) else None,
    )

    def _is_dialog_obj(obj):
        if not isinstance(obj, JObject):
            return False
        c = obj.cls or ""
        if "Dialog" in c:
            return True
        vm_i = getattr(stubs, "vm", None)
        return bool(vm_i is not None and vm_i._is_subclass(c, "Landroid/app/Dialog;"))

    def _dialog_content_node(obj):
        if not isinstance(obj, JObject):
            return None
        dv = obj.fields.get("$dlg_view")
        if isinstance(dv, ViewNode):
            return dv
        if _is_dialog_obj(obj) or "$dlg_title" in obj.fields:
            for fv in obj.fields.values():
                if isinstance(fv, JObject) and isinstance(fv.fields.get("$view"), ViewNode):
                    return fv.fields["$view"]
        return None

    def _set_content(a):
        if a and len(a) > 1:
            n = _node(a[1], "View")
            if n is not None:
                if isinstance(a[0], JObject) and _is_dialog_obj(a[0]) and not a[0].fields.get("$is_fragment"):
                    _init_custom_views(n)
                    _mount_fragments(n)
                    a[0].fields["$dlg_view"] = n
                    tree.touch()
                    return None
                tree._compose_recompose = None
                if tree.window_bg is not None and not tree.window_bg_dark:
                    tree.dark_mode = False
                _init_custom_views(n)
                _mount_fragments(n)
                tree.set_root(n)
                _refresh_decor_after_root()
        return None
    stubs.register_by_name("setContentView", "(Landroid/view/View;)V", _set_content)
    stubs.register_by_name("setContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", _set_content)

    # --- weitere UI-Setter (Breite fuer echten onCreate-Durchlauf) ---
    def _set_vis(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                try:
                    vis = int(a[1])
                    n.visible = (vis == 0)   # VISIBLE=0, INVISIBLE=4, GONE=8
                    n.props["visibility"] = vis
                except Exception:
                    n.visible = True
                    n.props["visibility"] = 0
                tree.touch()
        return None
    stubs.register_by_name("setVisibility", "(I)V", _set_vis)

    def _prop_setter(key, as_text=False):
        def fn(a):
            if a and len(a) > 1:
                n = _node(a[0])
                if n is not None:
                    v = a[1]
                    if as_text:
                        n.props[key] = "" if (v is None or (isinstance(v, int) and not isinstance(v, bool) and v == 0)) else _text(v)
                    else:
                        n.props[key] = v
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

    def _set_text_size(a):
        if not a:
            return None
        n = _node(a[0])
        if n is not None and len(a) >= 2:
            raw_sz = _f32_val(a[-1], 12.0)
            unit = int(a[1]) if (len(a) >= 3 and isinstance(a[1], int)) else 2
            sp_sz = (raw_sz / 2.75) if unit == 0 else raw_sz
            n.props["textSize"] = round(max(8.0, min(48.0, sp_sz)), 1)
            tree.touch()
        return None
    stubs.register_by_name("setTextSize", "(F)V", _set_text_size)
    stubs.register_by_name("setTextSize", "(IF)V", _set_text_size)

    def _set_text_res(a):
        if a and len(a) > 1:
            n = _node(a[0])
            if n is not None:
                try:
                    rid = int(a[1])
                except Exception:
                    rid = None
                if rid is not None and rid <= 0:
                    n.text = ""
                else:
                    sm = getattr(stubs, "_strmap_id", None)
                    txt = sm.get(rid) if (sm and rid is not None) else None
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

    def _drain_after_ui_event():
        _flush_fragment_transactions()
        ar = getattr(stubs, "_active_runner", None)
        dr = getattr(ar, "_drain_runnables", None) if ar is not None else None
        if callable(dr):
            dr()

    def _invoke_dlg_listener(dlg_obj, blst, which):
        vm_i = getattr(stubs, "vm", None)
        if not isinstance(blst, JObject) or vm_i is None:
            return
        old_b = getattr(vm_i, "step_budget", None)
        cur_s = int(getattr(vm_i, "steps", 0) or 0)
        if isinstance(old_b, int) and old_b > 0:
            vm_i.step_budget = max(old_b, cur_s + 300_000)
        try:
            sig_dl = "(Landroid/content/DialogInterface;I)V"
            md = vm_i._resolve_method(blst.cls, blst.cls, "onClick", sig_dl) if hasattr(vm_i, "_resolve_method") else None
            if md is not None:
                vm_i._run_mdef(md, [blst, dlg_obj, int(which)])
            elif hasattr(vm_i, "registry"):
                if hasattr(vm_i, "_lazy_class"):
                    try:
                        vm_i._lazy_class(blst.cls)
                    except Exception:
                        pass
                for (mcls, _mnm, msig), md2 in list(vm_i.registry.items()):
                    if mcls == blst.cls and msig == sig_dl and md2.insns:
                        vm_i._run_mdef(md2, [blst, dlg_obj, int(which)])
                        break
        except _VM_STOP_EXCS:
            raise
        except Exception:
            pass
        finally:
            if isinstance(old_b, int) and old_b > 0:
                end_s = int(getattr(vm_i, "steps", 0) or 0)
                rem = max(0, old_b - cur_s)
                vm_i.step_budget = max(old_b, end_s + min(rem, 100_000))
        _drain_after_ui_event()
        tree.touch()

    def _mount_dialog_overlay(dlg_obj, title="", msg="", custom_vn=None, buttons=None):
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
        if isinstance(custom_vn, ViewNode):
            old_p = getattr(custom_vn, "_parent", None)
            if old_p is not None and custom_vn in old_p.children:
                old_p.children.remove(custom_vn)
            custom_vn.visible = True
            _init_custom_views(custom_vn)
            card.add(custom_vn)
        if msg:
            card.add(ViewNode("TextView", vid="dlg_msg", text=str(msg)))
        elif not title and not isinstance(custom_vn, ViewNode):
            card.add(ViewNode("TextView", vid="dlg_msg", text="Dialog"))
        btn_list = list(buttons or ())
        if btn_list:
            brow = ViewNode("LinearLayout", props={"orientation": "horizontal"})
            order_map = {-2: 0, -3: 1, -1: 2}
            ordered = sorted(btn_list, key=lambda b: order_map.get(int(b[0]), 9))
            for idx_b, (which, btxt, blst) in enumerate(ordered):
                lbl_b = str(btxt or "").strip() or ("Cancel" if int(which) == -2 else "OK")
                bid = { -1: "button1", -2: "button2", -3: "button3" }.get(int(which), f"dlg_btn_{idx_b}")
                btn_n = ViewNode("Button", vid=bid, text=lbl_b, clickable=True)
                def _on_btn(_v=None, _d=dlg_obj, _card=card, _w=int(which), _lst=blst):
                    if tree.root is not None and _card in tree.root.children:
                        tree.root.children.remove(_card)
                    _d.fields["_showing"] = 0
                    tree.touch()
                    if isinstance(_lst, JObject):
                        _invoke_dlg_listener(_d, _lst, _w)
                btn_n.on_click = _on_btn
                brow.add(btn_n)
            card.add(brow)
        else:
            ok_btn = ViewNode("Button", vid="dlg_ok", text="OK", clickable=True)
            def _close_dlg(_v=None, _d=dlg_obj, _card=card):
                if tree.root is not None and _card in tree.root.children:
                    tree.root.children.remove(_card)
                _d.fields["_showing"] = 0
                tree.touch()
            ok_btn.on_click = _close_dlg
            card.add(ok_btn)
        tree.root.add(card)
        dlg_obj.fields["_showing"] = 1
        tree.touch()

    def _dismiss_dialog_overlay(dlg_obj):
        if tree.root is None:
            return
        before = len(tree.root.children)
        tree.root.children = [
            c for c in tree.root.children
            if not (c.props.get("role") == "dialog" and (dlg_obj is None or c.props.get("$dlg") is dlg_obj))
        ]
        if isinstance(dlg_obj, JObject):
            dlg_obj.fields["_showing"] = 0
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
        ac = next(
            (fv for fv in recv.fields.values() if isinstance(fv, JObject) and "AlertController" in (fv.cls or "")),
            None,
        )
        dlg_vn = _dialog_content_node(recv)
        ac_vn = None
        if ac is not None:
            g_obj = ac.fields.get("g")
            if isinstance(g_obj, JObject) and isinstance(g_obj.fields.get("$view"), ViewNode):
                ac_vn = g_obj.fields["$view"]
        custom_vn = ac_vn if ac_vn is not None else dlg_vn
        buttons = list((ac.fields.get("$dlg_buttons") if ac is not None else None) or recv.fields.get("$dlg_buttons") or ())
        if ac is None and not buttons and isinstance(dlg_vn, ViewNode) and dlg_vn.children and tree.root is not None:
            tree.root.children = [
                c for c in tree.root.children
                if not (c.props.get("role") == "dialog" and c.props.get("$dlg") is recv)
            ]
            old_p = getattr(dlg_vn, "_parent", None)
            if old_p is not None and dlg_vn in old_p.children:
                old_p.children.remove(dlg_vn)
            dlg_vn.visible = True
            card = ViewNode("LinearLayout", props={"role": "dialog", "$dlg": recv})
            card.add(dlg_vn)
            tree.root.add(card)
            recv.fields["_showing"] = 1
            tree.touch()
            on_show = recv.fields.get("_on_show")
            vm_i = getattr(stubs, "vm", None)
            if isinstance(on_show, JObject) and vm_i is not None:
                md_os = vm_i._resolve_method(on_show.cls, "Landroid/content/DialogInterface$OnShowListener;", "onShow", "(Landroid/content/DialogInterface;)V")
                if md_os is not None:
                    try:
                        vm_i._run_mdef(md_os, [on_show, recv])
                    except Exception:
                        pass
            return None
        title = (_text(ac.fields.get("d")).strip() if ac is not None else "") or str(recv.fields.get("$dlg_title") or "").strip()
        msg = (_text(ac.fields.get("e")).strip() if ac is not None else "") or str(recv.fields.get("$dlg_msg") or "").strip()
        if not msg and custom_vn is None:
            for fv in recv.fields.values():
                if isinstance(fv, JString) and fv.text.strip():
                    if not title:
                        title = fv.text.strip()
                    else:
                        msg = fv.text.strip()
                        break
        _mount_dialog_overlay(recv, title=title, msg=msg, custom_vn=custom_vn, buttons=buttons)
        on_show = recv.fields.get("_on_show")
        vm_i = getattr(stubs, "vm", None)
        if isinstance(on_show, JObject) and vm_i is not None:
            md_os = vm_i._resolve_method(on_show.cls, "Landroid/content/DialogInterface$OnShowListener;", "onShow", "(Landroid/content/DialogInterface;)V")
            if md_os is not None:
                try:
                    vm_i._run_mdef(md_os, [on_show, recv])
                except Exception:
                    pass
        return None

    stubs.register_by_name("show", "()V", _generic_show)
    stubs.register_by_name("dismiss", "()V", lambda a: _dismiss_dialog_overlay(a[0] if a else None))
    stubs.register_by_name("cancel", "()V", lambda a: _dismiss_dialog_overlay(a[0] if a else None))

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
        def _ab_set_btn(which):
            def _fn(a):
                if a and isinstance(a[0], JObject):
                    btxt = (_strmap_id_lookup(a[1]) if (len(a) > 1 and isinstance(a[1], int)) else (_text(a[1]) if len(a) > 1 else ""))
                    blst = a[2] if (len(a) > 2 and isinstance(a[2], JObject)) else None
                    blist = a[0].fields.setdefault("$dlg_buttons", [])
                    blist[:] = [b for b in blist if b[0] != which]
                    blist.append((which, btxt, blst))
                return a[0] if a else None
            return _fn
        def _ab_set_view(a):
            if a and len(a) > 1 and isinstance(a[0], JObject):
                vn_d = _node(a[1]) if isinstance(a[1], JObject) else None
                if isinstance(vn_d, ViewNode):
                    a[0].fields["$dlg_view"] = vn_d
            return a[0] if a else None
        for _msig in ("(Ljava/lang/CharSequence;)%s" % _ab_cls, "(I)%s" % _ab_cls):
            stubs.register(_ab_cls, "setTitle", _msig, _ab_set_str("$dlg_title"))
            stubs.register(_ab_cls, "setMessage", _msig, _ab_set_str("$dlg_msg"))
        for _bsig in (
            "(Ljava/lang/CharSequence;Landroid/content/DialogInterface$OnClickListener;)%s" % _ab_cls,
            "(ILandroid/content/DialogInterface$OnClickListener;)%s" % _ab_cls,
        ):
            stubs.register(_ab_cls, "setPositiveButton", _bsig, _ab_set_btn(-1))
            stubs.register(_ab_cls, "setNegativeButton", _bsig, _ab_set_btn(-2))
            stubs.register(_ab_cls, "setNeutralButton", _bsig, _ab_set_btn(-3))
        stubs.register(_ab_cls, "setView", "(Landroid/view/View;)%s" % _ab_cls, _ab_set_view)
        def _ab_create(a):
            b = a[0] if a and isinstance(a[0], JObject) else JObject(_ab_cls)
            dfields = {
                "$dlg_title": b.fields.get("$dlg_title", ""),
                "$dlg_msg": b.fields.get("$dlg_msg", ""),
            }
            if b.fields.get("$dlg_buttons"):
                dfields["$dlg_buttons"] = list(b.fields["$dlg_buttons"])
            if isinstance(b.fields.get("$dlg_view"), ViewNode):
                dfields["$dlg_view"] = b.fields["$dlg_view"]
            return JObject("Landroid/app/AlertDialog;", dfields)
        stubs.register(_ab_cls, "create", "()Landroid/app/AlertDialog;", _ab_create)
        stubs.register(_ab_cls, "create", "()Landroidx/appcompat/app/AlertDialog;", _ab_create)
        def _ab_show(a):
            d = _ab_create(a)
            _mount_dialog_overlay(
                d,
                d.fields.get("$dlg_title", ""),
                d.fields.get("$dlg_msg", ""),
                custom_vn=d.fields.get("$dlg_view"),
                buttons=d.fields.get("$dlg_buttons"),
            )
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
                if int(a[1] or 0) == 0:
                    n.props.pop("bg_drawable", None)
                    tree.touch()
                    return None
                _nm, spec = _resolve_drawable_id(a[1])
                if spec is not None:
                    n.props["bg_drawable"] = spec
                    tree.touch()
        return None
    stubs.register_by_name("setBackgroundResource", "(I)V", _set_bg_res)

    def _apply_bmp_to_node(n, bmp):
        if n is None or not isinstance(bmp, JObject):
            return False
        raw = bmp.fields.get("_bytes")
        if not raw and ("_pixels" in bmp.fields or "_erase" in bmp.fields):
            try:
                from core.java.view_breadth_g16 import encode_bitmap_png
                raw = encode_bitmap_png(bmp)
            except Exception:
                raw = b""
        if raw:
            n.props.pop("src_null", None)
            n.props["img_bytes"] = bytes(raw)
            return True
        if bmp.fields.get("_drawable") is not None:
            n.props.pop("src_null", None)
            sp = bmp.fields["_drawable"]
            n.props["drawable"] = copy.deepcopy(sp) if isinstance(sp, dict) else sp
            return True
        if bmp.fields.get("_path"):
            n.props.pop("src_null", None)
            n.props["drawable"] = {"kind": "raster", "path": bmp.fields["_path"], "name": bmp.fields.get("_name", "")}
            return True
        return False

    def _set_image_bitmap(a):
        if a and len(a) > 1 and isinstance(a[1], JObject):
            n = _node(a[0])
            if _apply_bmp_to_node(n, a[1]):
                tree.touch()
        return None
    stubs.register_by_name("setImageBitmap", "(Landroid/graphics/Bitmap;)V", _set_image_bitmap)

    def _set_image_drawable(a):
        if a and len(a) > 1 and isinstance(a[1], JObject):
            n = _node(a[0])
            drw = a[1]
            if n is not None:
                n.props.pop("src_null", None)
                bmp_f = drw.fields.get("_bmp") or drw.fields.get("mBitmap")
                if isinstance(bmp_f, JObject) and _apply_bmp_to_node(n, bmp_f):
                    pass
                elif drw.fields.get("_bytes"):
                    n.props["img_bytes"] = bytes(drw.fields["_bytes"])
                elif drw.fields.get("_drawable") is not None:
                    sp = drw.fields["_drawable"]
                    n.props["drawable"] = copy.deepcopy(sp) if isinstance(sp, dict) else sp
                if drw.fields.get("_tint"):
                    n.props["tint"] = drw.fields["_tint"]
                else:
                    n.props.pop("tint", None)
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
    _name2str = {}
    _menu_idmap = {}
    _parse_menu_fn = None
    if app_root:
        from core.java.layout_inflater import (
            build_resmap, inflate_res, build_id_map, build_string_map,
            build_drawable_map, build_array_map, build_pref_style_map,
            build_bool_map, build_integer_map, _public_type_map,
            build_menu_map, parse_menu_xml,
        )
        _parse_menu_fn = parse_menu_xml
        _idmap = build_resmap(app_root)
        _idmap_id = build_id_map(app_root)
        _idmap_id_ref.update(_idmap_id)
        for _rid, _rnm in _idmap_id.items():
            _rev_idmap[_rnm] = _rid
        _strmap_id, _name2str = build_string_map(app_root)
        _, _str_id2name = _public_type_map(app_root, "string")
        _menu_idmap = build_menu_map(app_root)
        _arr_idmap_tmp, _ = build_array_map(app_root, name2text=_name2str)
        _arr_idmap.update(_arr_idmap_tmp)
        _bool_idmap, _ = build_bool_map(app_root)
        _int_idmap, _ = build_integer_map(app_root)
        _, _dim_id2name = _public_type_map(app_root, "dimen")
        stubs._drawmap = build_drawable_map(app_root)
        stubs._res_idmap = _idmap_id
        stubs._rev_idmap = _rev_idmap
        stubs._strmap_id = _strmap_id
        stubs._strmap_name = _name2str
        stubs._str_rev_idmap = {nm: rid for rid, nm in _str_id2name.items()}
        stubs._draw_rev_idmap = {nm: rid for rid, nm in (stubs._drawmap or {}).items()}
        stubs._menu_idmap = _menu_idmap
        stubs._bool_idmap = _bool_idmap
        stubs._int_idmap = _int_idmap
        stubs._app_root = app_root
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
                    if isinstance(a[0], JObject) and _is_dialog_obj(a[0]) and not a[0].fields.get("$is_fragment"):
                        _init_custom_views(n)
                        _mount_fragments(n)
                        a[0].fields["$dlg_view"] = n
                        tree.touch()
                        return None
                    tree._compose_recompose = None
                    if tree.window_bg is not None and not tree.window_bg_dark:
                        tree.dark_mode = False
                    _init_custom_views(n)
                    _mount_fragments(n)
                    tree.set_root(n)
                    _refresh_decor_after_root()
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
                    elif a and isinstance(a[0], JObject):
                        root_search = _dialog_content_node(a[0]) or tree.root
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
        def _get_bool(a):
            if len(a) > 1 and isinstance(a[1], int):
                return int(_bool_idmap.get(int(a[1]), 0))
            return 0

        def _get_integer(a):
            if len(a) > 1 and isinstance(a[1], int):
                return int(_int_idmap.get(int(a[1]), 0))
            return 0

        def _get_dimension(a, as_int=False):
            rid = int(a[1]) if (len(a) > 1 and isinstance(a[1], int)) else 0
            dname = _dim_id2name.get(rid)
            resv = getattr(stubs, "_drawable_resolver", None)
            val = None
            if dname and resv is not None and hasattr(resv, "resolve_dimen"):
                val = resv.resolve_dimen(f"@dimen/{dname}", None)
            if val is None:
                val = 16.0
            return int(round(float(val))) if as_int else float(val)

        stubs.register_by_name("getString", "(I)Ljava/lang/String;", _get_string)
        stubs.register_by_name("getText", "(I)Ljava/lang/CharSequence;", _get_string)
        stubs.register_by_name("getString", "(I[Ljava/lang/Object;)Ljava/lang/String;", _get_string_fmt)
        stubs.register_by_name("getStringArray", "(I)[Ljava/lang/String;", _get_str_arr)
        stubs.register_by_name("getIntArray", "(I)[I", _get_int_arr)
        stubs.register_by_name("getBoolean", "(I)Z", _get_bool)
        stubs.register_by_name("getInteger", "(I)I", _get_integer)
        stubs.register_by_name("getDimension", "(I)F", lambda a: _get_dimension(a, as_int=False))
        stubs.register_by_name("getDimensionPixelSize", "(I)I", lambda a: _get_dimension(a, as_int=True))
        stubs.register_by_name("getDimensionPixelOffset", "(I)I", lambda a: _get_dimension(a, as_int=True))
        stubs.register_by_name("obtainTypedArray", "(I)Landroid/content/res/TypedArray;",
                               lambda a: JObject("Landroid/content/res/TypedArray;", {"_rid": a[1] if len(a) > 1 else 0}))
    else:
        _get_string = _get_string_fmt = _get_str_arr = _get_int_arr = None
        _get_bool = _get_integer = _get_dimension = None

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
                    u_str = x.fields.get("$uri") or x.fields.get("uri") or ""
                    a[0].fields["$uri"] = u_str
                    a[0].fields["_data_uri"] = u_str
                    a[0].fields["data"] = x
            if len(a) > 2 and isinstance(a[2], JString) and a[2].text:
                a[0].fields["$target"] = a[2].text
            elif len(a) >= 2 and isinstance(a[1], JString):
                s1 = a[1].text
                if "action." in s1 or s1.startswith("android."):
                    a[0].fields["$action"] = s1
        return a[0] if a else None
    stubs.register("Landroid/content/Intent;", "<init>", "(Ljava/lang/String;)V", _intent_target)
    stubs.register("Landroid/content/Intent;", "<init>", "(Ljava/lang/String;Landroid/net/Uri;)V", _intent_target)
    stubs.register("Landroid/content/Intent;", "<init>",
                   "(Landroid/content/Context;Ljava/lang/Class;)V", _intent_target)
    stubs.register_by_name("setAction", "(Ljava/lang/String;)Landroid/content/Intent;", _intent_target)
    stubs.register_by_name("setData", "(Landroid/net/Uri;)Landroid/content/Intent;", _intent_target)
    stubs.register_by_name("setComponent", "(Landroid/content/ComponentName;)Landroid/content/Intent;", _intent_target)
    stubs.register("Landroid/net/Uri;", "parse", "(Ljava/lang/String;)Landroid/net/Uri;",
                   lambda a: (lambda s: JObject("Landroid/net/Uri;", {"$uri": s, "uri": s}))(_text(a[0]) if a else ""))
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
        frags = adp.fields.setdefault("__vp2_frags", {})
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
            if isinstance(adp, JObject):
                if is_vp2:
                    _vp2_materialize_adapter(vm_cur, recv, adp)
                else:
                    _vp1_materialize_adapter(vm_cur, recv, adp)
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

    def _rv_materialize_adapter(_vm, recv, adp, changed_range=None):
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
        if not (isinstance(adp, JObject) and _vm is not None and hasattr(_vm, "invoke_virtual")):
            rv_node.children.clear()
            tree.touch()
            return
        old_b = getattr(_vm, "step_budget", None)
        cur_s = getattr(_vm, "steps", 0)
        _vm.step_budget = (cur_s + 500_000) if old_b is None else max(old_b, cur_s + 500_000)
        try:
            try:
                cnt = int(_vm.invoke_virtual(adp, "getItemCount", "()I", (adp,)) or 0)
            except _VM_STOP_EXCS:
                raise
            except Exception:
                cnt = 0
            limit_cnt = min(cnt, 40)
            holders = adp.fields.get("__rv_holders")
            cached_bind_sig = adp.fields.get("__rv_bind_sig")
            if (
                changed_range is not None
                and isinstance(holders, dict)
                and cached_bind_sig
                and len(rv_node.children) == limit_cnt
            ):
                c_start, c_count = int(changed_range[0]), max(0, int(changed_range[1]))
                sc = int(recv.fields.get("__span_count", 1) or 1) if isinstance(recv, JObject) else 1
                lm = recv.fields.get("__layout_manager") if isinstance(recv, JObject) else None
                ssl = lm.fields.get("__span_lookup") if isinstance(lm, JObject) else None
                indices = [idx for idx in range(c_start, c_start + c_count) if 0 <= idx < limit_cnt]
                can_rebind = True
                for idx in indices:
                    entry = holders.get(idx)
                    if not entry:
                        can_rebind = False
                        break
                    try:
                        vt_now = int(_vm.invoke_virtual(adp, "getItemViewType", "(I)I", (adp, idx)) or 0)
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        vt_now = 0
                    if vt_now != entry[0]:
                        can_rebind = False
                        break
                if can_rebind:
                    for idx in indices:
                        _, vh_cached, item_n_cached = holders[idx]
                        try:
                            _vm.invoke_virtual(adp, "onBindViewHolder", cached_bind_sig, (adp, vh_cached, idx))
                            if item_n_cached is not None and isinstance(ssl, JObject) and sc >= 2:
                                try:
                                    ss = int(_vm.invoke_virtual(ssl, "getSpanSize", "(I)I", (ssl, idx)) or 1)
                                    item_n_cached.props["span_size"] = max(1, min(sc, ss))
                                except _VM_STOP_EXCS:
                                    raise
                                except Exception:
                                    item_n_cached.props["span_size"] = 1
                        except _VM_STOP_EXCS:
                            raise
                        except Exception:
                            break
                    tree.touch()
                    return
            rv_node.children.clear()
            new_holders = {}
            adp.fields["__rv_holders"] = new_holders
            create_sig = None
            bind_sig = None
            cur = adp.cls
            seen_c = set()
            while cur and cur not in seen_c and (not create_sig or not bind_sig):
                seen_c.add(cur)
                if hasattr(_vm, "_lazy_class"):
                    _vm._lazy_class(cur)
                elif hasattr(_vm, "_ensure_loaded"):
                    _vm._ensure_loaded(cur)
                for (mc, mn, ms) in list(getattr(_vm, "registry", {}).keys()):
                    if mc == cur:
                        if mn == "onCreateViewHolder" and ms.startswith("(Landroid/view/ViewGroup;I)L") and not create_sig:
                            create_sig = ms
                        elif mn == "onBindViewHolder" and ms.startswith("(L") and ms.endswith(";I)V") and not bind_sig:
                            bind_sig = ms
                cur = _vm._superclass(cur) if hasattr(_vm, "_superclass") else None
            adp.fields["__rv_bind_sig"] = bind_sig
            if create_sig and bind_sig and cnt > 0:
                sc = int(recv.fields.get("__span_count", 1) or 1) if isinstance(recv, JObject) else 1
                lm = recv.fields.get("__layout_manager") if isinstance(recv, JObject) else None
                ssl = lm.fields.get("__span_lookup") if isinstance(lm, JObject) else None
                for i in range(limit_cnt):
                    try:
                        vt = int(_vm.invoke_virtual(adp, "getItemViewType", "(I)I", (adp, i)) or 0)
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        vt = 0
                    try:
                        vh = _vm.invoke_virtual(adp, "onCreateViewHolder", create_sig, (adp, recv, vt))
                        if not isinstance(vh, JObject):
                            continue
                        vh.fields["_adapter_pos"] = i
                        _vm.invoke_virtual(adp, "onBindViewHolder", bind_sig, (adp, vh, i))
                        iv = vh.fields.get("itemView")
                        if not (isinstance(iv, JObject) and "$view" in iv.fields):
                            iv = next((fv for fv in vh.fields.values() if isinstance(fv, JObject) and "$view" in fv.fields), None)
                        if not (isinstance(iv, JObject) and "$view" in iv.fields):
                            for fv in vh.fields.values():
                                if isinstance(fv, JObject):
                                    iv = next((gv for gv in fv.fields.values() if isinstance(gv, JObject) and "$view" in gv.fields), None)
                                    if iv is not None:
                                        break
                        item_n = iv.fields["$view"] if (isinstance(iv, JObject) and "$view" in iv.fields) else None
                        if item_n is not None:
                            if isinstance(ssl, JObject) and sc >= 2:
                                try:
                                    ss = int(_vm.invoke_virtual(ssl, "getSpanSize", "(I)I", (ssl, i)) or 1)
                                    item_n.props["span_size"] = max(1, min(sc, ss))
                                except _VM_STOP_EXCS:
                                    raise
                                except Exception:
                                    item_n.props["span_size"] = 1
                            if item_n not in rv_node.children:
                                rv_node.add(item_n)
                        new_holders[i] = (vt, vh, item_n)
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        continue
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
                        frags = adp.fields.setdefault("__vp1_frags", {})
                        item = frags.get(pos)
                        if not isinstance(item, JObject):
                            item = _vm.invoke_virtual(adp, "getItem", "(I)Landroidx/fragment/app/Fragment;", (adp, pos))
                            if isinstance(item, JObject):
                                frags[pos] = item
                        if isinstance(item, JObject):
                            if not hasattr(stubs, "_fragments_by_tag"):
                                stubs._fragments_by_tag = {}
                            ftag = f"vp1:{id(recv)}:{pos}"
                            stubs._fragments_by_tag[ftag] = item
                            _mount_fragment_instance(item, vp_node, tag=ftag)
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
                vis_i = int(vis)
                tgt.visible = (vis_i == 0)
                tgt.props["visibility"] = vis_i
        for vid_id, al in alpha_map.items():
            vname = _idmap_id_ref.get(vid_id) if isinstance(vid_id, int) else vid_id
            tgt = root_n.find(vname) if vname else None
            if tgt is not None:
                tgt.props["alpha"] = float(al)
        tree.touch()

    def _mk_menu_item(item_id=0, gid=0, vid="", title="", visible=True, enabled=True, icon_ref="", icon_name="", node=None):
        return JObject("Landroid/view/MenuItem;", {
            "$synth_mi": True,
            "$item_id": int(item_id or 0),
            "_id": int(item_id or 0),
            "_gid": int(gid or 0),
            "$vid": str(vid or ""),
            "$title": str(title or ""),
            "_title": str(title or ""),
            "$visible": 1 if visible else 0,
            "_vis": 1 if visible else 0,
            "$enabled": 1 if enabled else 0,
            "_en": 1 if enabled else 0,
            "$checked": 0,
            "$icon_ref": str(icon_ref or ""),
            "$icon_name": str(icon_name or ""),
            "$node": node,
        })

    def _inflate_menu_res(res_id, menu_obj):
        if not isinstance(menu_obj, JObject):
            return
        items = menu_obj.fields.setdefault("_items", [])
        menu_obj.fields["$items"] = items
        menu_obj.fields["$synth_menu"] = True
        if _parse_menu_fn is None or not app_root:
            return
        try:
            rid_i = int(res_id or 0)
        except Exception:
            return
        parsed = _parse_menu_fn(
            app_root,
            rid_i,
            name2text=_name2str,
            rev_idmap=_rev_idmap,
            menu_idmap=_menu_idmap,
        )
        for it in parsed:
            items.append(_mk_menu_item(
                item_id=it.get("res_id", 0),
                vid=it.get("vid", ""),
                title=it.get("title", ""),
                visible=it.get("visible", True),
                enabled=it.get("enabled", True),
                icon_ref=it.get("icon_ref", ""),
                icon_name=it.get("icon_name", ""),
            ))

    def _bind_activity_options_menu(act):
        if not isinstance(act, JObject) or tree.root is None:
            return
        vm_cur = getattr(stubs, "vm", None)
        if vm_cur is None or not hasattr(vm_cur, "_resolve_method"):
            return
        md = vm_cur._resolve_method(act.cls, act.cls, "onCreateOptionsMenu", "(Landroid/view/Menu;)Z")
        _fw_bases = (
            "Landroid/app/Activity;",
            "Landroidx/activity/ComponentActivity;",
            "Landroidx/fragment/app/FragmentActivity;",
            "Landroidx/appcompat/app/AppCompatActivity;",
        )
        if md is None or md.cls in _fw_bases:
            return
        menu_obj = JObject("Landroid/view/Menu;", {"$synth_menu": True, "_items": []})
        menu_obj.fields["$items"] = menu_obj.fields["_items"]
        old_b = getattr(vm_cur, "step_budget", None)
        cur_s = int(getattr(vm_cur, "steps", 0) or 0)
        if isinstance(old_b, int) and old_b > 0:
            vm_cur.step_budget = max(old_b, cur_s + 150_000)
        try:
            vm_cur._run_mdef(md, [act, menu_obj])
        except _VM_STOP_EXCS:
            raise
        except Exception:
            return
        finally:
            if isinstance(old_b, int) and old_b > 0:
                end_s = int(getattr(vm_cur, "steps", 0) or 0)
                rem = max(0, old_b - cur_s)
                vm_cur.step_budget = max(old_b, end_s + min(rem, 100_000))
        tb = next(
            (n for n in tree.root.walk() if "Toolbar" in (n.cls or "") or n.vid == "toolbar"),
            None,
        )
        if tb is None:
            return
        amv = next((c for c in tb.walk() if c is not tb and "ActionMenuView" in (c.cls or "")), None)
        target_bar = amv if amv is not None else tb
        target_bar.children = [c for c in target_bar.children if not c.props.get("$opt_menu_btn")]
        resv = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
        vis_cnt = 0
        for idx_m, mi in enumerate(menu_obj.fields.get("_items") or ()):
            if not isinstance(mi, JObject):
                continue
            vid_m = str(mi.fields.get("$vid") or f"menu_{mi.fields.get('$item_id', idx_m)}")
            title_m = str(mi.fields.get("$title") or mi.fields.get("_title") or "").strip()
            vis_m = bool(mi.fields.get("$visible", mi.fields.get("_vis", 1)))
            icon_ref = str(mi.fields.get("$icon_ref") or "").strip()
            icon_spec = None
            if icon_ref and resv is not None and hasattr(resv, "resolve"):
                try:
                    icon_spec = resv.resolve(icon_ref)
                except Exception:
                    icon_spec = None
            bprops = {"$opt_menu_btn": True, "style": "Borderless"}
            if icon_spec is not None:
                bprops["bg_drawable"] = icon_spec
                if mi.fields.get("$icon_name"):
                    bprops["img"] = mi.fields["$icon_name"]
                bprops["a11y_label"] = title_m or vid_m
                bprops["desc"] = title_m or vid_m
                btxt = ""
            else:
                btxt = title_m or ("Search" if "search" in vid_m.lower() else vid_m)
                if title_m:
                    bprops["a11y_label"] = title_m
            btn = ViewNode(cls="Button", vid=vid_m, text=btxt, visible=vis_m, clickable=True, props=bprops)
            if vis_m:
                vis_cnt += 1
            mi.fields["$node"] = btn
            def _on_opt(_v=None, _act=act, _mi=mi):
                vm_i = getattr(stubs, "vm", None)
                if vm_i is None:
                    return
                ob = getattr(vm_i, "step_budget", None)
                cs = int(getattr(vm_i, "steps", 0) or 0)
                if isinstance(ob, int) and ob > 0:
                    vm_i.step_budget = max(ob, cs + 300_000)
                try:
                    if hasattr(vm_i, "invoke_virtual"):
                        vm_i.invoke_virtual(_act, "onOptionsItemSelected", "(Landroid/view/MenuItem;)Z", (_act, _mi))
                    else:
                        vm_i.invoke(_act.cls, "onOptionsItemSelected", "(Landroid/view/MenuItem;)Z", (_act, _mi))
                except _VM_STOP_EXCS:
                    raise
                except Exception:
                    pass
                finally:
                    if isinstance(ob, int) and ob > 0:
                        es = int(getattr(vm_i, "steps", 0) or 0)
                        rm = max(0, ob - cs)
                        vm_i.step_budget = max(ob, es + min(rm, 100_000))
                _drain_after_ui_event()
                tree.touch()
            btn.on_click = _on_opt
            target_bar.add(btn)
        if target_bar is amv:
            amv.props["orientation"] = "horizontal"
            amv.props["w"] = max(44, max(1, vis_cnt) * 42)
        tree.touch()

    stubs._bind_activity_options_menu = _bind_activity_options_menu

    def _lifecycle_resumed_state(_vm, st_cls):
        if _vm is not None:
            for fn_nm in ("_ensure_loaded", "_clinit"):
                fn = getattr(_vm, fn_nm, None)
                if callable(fn):
                    try:
                        fn(st_cls)
                    except Exception:
                        pass
        sf = getattr(_vm, "static_fields", {}).get(st_cls, {}) if _vm is not None else {}
        for v in sf.values():
            if isinstance(v, JObject) and (
                str(v.fields.get("__ename") or "").upper() == "RESUMED"
                or str(_text(v.fields.get("name"))).upper() == "RESUMED"
            ):
                return v
        for cand_k in ("RESUMED", "f"):
            v = sf.get(cand_k)
            if isinstance(v, JObject):
                return v
        return JObject(st_cls, {"__ename": "RESUMED", "__ordinal": 4, "name": JString("RESUMED"), "ordinal": 4})

    def _vm_creation_extras_for(_vm, ret_cls):
        if _vm is not None and isinstance(ret_cls, str) and ret_cls.startswith("L") and ret_cls.endswith(";"):
            sub_empty = ret_cls[:-1] + "$a;"
            for fn_nm in ("_ensure_loaded", "_clinit"):
                fn = getattr(_vm, fn_nm, None)
                if callable(fn):
                    try:
                        fn(sub_empty)
                    except Exception:
                        pass
            sf = getattr(_vm, "static_fields", {}).get(sub_empty, {})
            for fk in ("b", "a", "INSTANCE", "Empty"):
                fv = sf.get(fk)
                if isinstance(fv, JObject):
                    return fv
            for fv in sf.values():
                if isinstance(fv, JObject) and (fv.cls == sub_empty or fv.cls == ret_cls):
                    return fv
        return JObject(ret_cls or "Ljava/lang/Object;", {"a": JObject("Ljava/util/LinkedHashMap;", {"__map": {}})})

    def _vm_create_for(_vm, cls_arg):
        if not isinstance(cls_arg, JObject):
            return None
        jcls_obj = cls_arg.fields.get("$jclass") or cls_arg.fields.get("jClass") or cls_arg.fields.get("a") or cls_arg
        raw_n = jcls_obj.fields.get("name") or jcls_obj.fields.get("$name") or cls_arg.fields.get("$name") or ""
        raw_s = raw_n.text if isinstance(raw_n, JString) else str(raw_n or "")
        if not raw_s:
            return None
        desc = raw_s if (raw_s.startswith("L") and raw_s.endswith(";")) else ("L" + raw_s.replace(".", "/") + ";")
        if _vm is not None and hasattr(_vm, "_ensure_loaded"):
            try:
                _vm._ensure_loaded(desc)
            except Exception:
                pass
        vm_inst = JObject(desc, {})
        if _vm is not None:
            app_obj = getattr(stubs, "application", None)
            if _vm._resolve_method(desc, desc, "<init>", "(Landroid/app/Application;)V") is not None:
                _safe_frag_call(_vm, vm_inst, "<init>", "(Landroid/app/Application;)V", (vm_inst, app_obj))
            elif _vm._resolve_method(desc, desc, "<init>", "()V") is not None:
                _safe_frag_call(_vm, vm_inst, "<init>", "()V", (vm_inst,))
        return vm_inst

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

        def _make_drawable_jobj(rid):
            rid_i = int(rid or 0)
            if rid_i <= 0:
                return None
            nm, spec = _resolve_drawable_id(rid_i)
            is_vec = (isinstance(spec, dict) and spec.get("kind") == "vector") or ("vector" in (nm or "").lower())
            dcls = "Landroid/graphics/drawable/VectorDrawable;" if is_vec else "Landroid/graphics/drawable/BitmapDrawable;"
            sp_copy = copy.deepcopy(spec) if isinstance(spec, dict) else spec
            orig_copy = copy.deepcopy(spec) if isinstance(spec, dict) else None
            return JObject(dcls, {"res_id": rid_i, "_drawable": sp_copy, "_orig_drawable": orig_copy})

        def _apply_drawable_tint(drv_obj, color_arg):
            if not isinstance(drv_obj, JObject):
                return
            _, hex_col = _extract_color_int(color_arg)
            if not hex_col:
                return
            spec = drv_obj.fields.get("_drawable")
            if "_orig_drawable" not in drv_obj.fields:
                drv_obj.fields["_orig_drawable"] = copy.deepcopy(spec) if isinstance(spec, dict) else None
            if not isinstance(spec, dict) and drv_obj.fields.get("res_id"):
                _, resolved_sp = _resolve_drawable_id(drv_obj.fields.get("res_id"))
                if isinstance(resolved_sp, dict):
                    spec = copy.deepcopy(resolved_sp)
                    drv_obj.fields["_orig_drawable"] = copy.deepcopy(resolved_sp)
            if isinstance(spec, dict):
                spec = copy.deepcopy(spec)
                if spec.get("kind") in ("shape", "color"):
                    spec["fill"] = hex_col
                    spec["outline"] = hex_col
                else:
                    spec["tint"] = hex_col
                drv_obj.fields["_drawable"] = spec
            elif drv_obj.fields.get("_owner_view") is not None or any(
                k in (drv_obj.cls or "") for k in ("GradientDrawable", "ColorDrawable", "ShapeDrawable")
            ):
                spec = {"kind": "shape", "shape": "rectangle", "radius": 8, "fill": hex_col, "outline": hex_col}
                drv_obj.fields["_drawable"] = spec
            else:
                drv_obj.fields["_tint"] = hex_col
                return
            owner = drv_obj.fields.get("_owner_view")
            if owner is not None:
                owner.props["bg_drawable"] = dict(spec)
                tree.touch()

        def _rv_set_layout_manager(rv_obj, rv_vn, logical_args):
            lm = logical_args[1] if len(logical_args) > 1 else None
            rv_obj.fields["__layout_manager"] = lm
            if isinstance(lm, JObject):
                lm.fields["__rv"] = rv_obj
            sc = int(lm.fields.get("__span_count", 1) or 1) if isinstance(lm, JObject) else 1
            rv_obj.fields["__span_count"] = sc
            if rv_vn is not None:
                if sc >= 2:
                    rv_vn.props["span_count"] = sc
                else:
                    rv_vn.props.pop("span_count", None)
                tree.touch()
            adp = rv_obj.fields.get("__adapter") if isinstance(rv_obj, JObject) else None
            if isinstance(adp, JObject):
                _rv_materialize_adapter(vm_obj, rv_obj, adp)

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

        def _build_zxing_bitmatrix(content_s, fmt_obj, req_w, req_h):
            import hashlib as _hl_zx
            from core.java.objects import JArray as _JArrZx
            fmt_name = ""
            if isinstance(fmt_obj, JObject):
                fmt_name = str(fmt_obj.fields.get("__ename") or _text(fmt_obj.fields.get("name")) or "").upper()
            seed_bytes = (str(content_s or "qr")).encode("utf-8", "replace")
            if fmt_name in ("CODE_128", "CODE_39", "CODE_93", "EAN_13", "EAN_8", "UPC_A", "UPC_E", "ITF", "CODABAR"):
                bw, bh = 128, 48
                digest = _hl_zx.sha256(seed_bytes).digest() + _hl_zx.sha256(seed_bytes + b":1d").digest()
                row_1d = bytearray(bw)
                for x_i in range(6, bw - 6):
                    b_idx = (x_i - 6) // 2
                    bit_v = 1 if x_i in (6, 7, 9, bw - 7, bw - 8, bw - 10) else ((digest[(b_idx >> 3) % len(digest)] >> (b_idx & 7)) & 1)
                    if 4 <= x_i < bw - 4:
                        row_1d[x_i] = bit_v
                fast_rows = [bytearray(row_1d) if 4 <= y_i < bh - 4 else bytearray(bw) for y_i in range(bh)]
            else:
                n_mod = 29
                mat = [[0] * n_mod for _ in range(n_mod)]
                res = [[False] * n_mod for _ in range(n_mod)]

                def _put_finder(ox_f, oy_f):
                    for dy_f in range(-1, 8):
                        yy = oy_f + dy_f
                        if not (0 <= yy < n_mod):
                            continue
                        for dx_f in range(-1, 8):
                            xx = ox_f + dx_f
                            if not (0 <= xx < n_mod):
                                continue
                            res[yy][xx] = True
                            if 0 <= dx_f <= 6 and 0 <= dy_f <= 6:
                                mat[yy][xx] = 1 if (dx_f in (0, 6) or dy_f in (0, 6) or (2 <= dx_f <= 4 and 2 <= dy_f <= 4)) else 0
                            else:
                                mat[yy][xx] = 0

                _put_finder(0, 0)
                _put_finder(n_mod - 7, 0)
                _put_finder(0, n_mod - 7)
                for dy_a in range(-2, 3):
                    for dx_a in range(-2, 3):
                        res[22 + dy_a][22 + dx_a] = True
                        mat[22 + dy_a][22 + dx_a] = 1 if max(abs(dx_a), abs(dy_a)) in (0, 2) else 0
                for t_i in range(8, n_mod - 8):
                    v_t = 1 if (t_i % 2 == 0) else 0
                    res[6][t_i] = True
                    mat[6][t_i] = v_t
                    res[t_i][6] = True
                    mat[t_i][6] = v_t
                res[n_mod - 8][8] = True
                mat[n_mod - 8][8] = 1
                fmt_digest = _hl_zx.sha256(b"fmt:" + seed_bytes).digest()
                for f_i in range(9):
                    if not res[8][f_i]:
                        res[8][f_i] = True
                        mat[8][f_i] = (fmt_digest[0] >> (f_i & 7)) & 1
                    if not res[f_i][8]:
                        res[f_i][8] = True
                        mat[f_i][8] = (fmt_digest[1] >> (f_i & 7)) & 1
                stream = bytearray()
                ctr_b = 0
                while len(stream) < 128:
                    stream.extend(_hl_zx.sha256(seed_bytes + bytes([ctr_b])).digest())
                    ctr_b += 1
                bit_pos = 0
                for my in range(n_mod):
                    for mx in range(n_mod):
                        if not res[my][mx]:
                            mat[my][mx] = (stream[bit_pos >> 3] >> (bit_pos & 7)) & 1
                            bit_pos += 1
                max_req = max(int(req_w or 0), int(req_h or 0))
                sc = 3 if max_req >= 87 else (2 if max_req >= 58 else 1)
                qz = sc
                bw = bh = n_mod * sc + 2 * qz
                fast_rows = [bytearray(bw) for _ in range(bh)]
                for my in range(n_mod):
                    for mx in range(n_mod):
                        if mat[my][mx]:
                            y_base = qz + my * sc
                            x_base = qz + mx * sc
                            for sy_m in range(sc):
                                r_buf = fast_rows[y_base + sy_m]
                                for sx_m in range(sc):
                                    r_buf[x_base + sx_m] = 1
            row_size = (bw + 31) // 32
            bits: list[int] = [0] * (row_size * bh)
            for ry_b in range(bh):
                rb = fast_rows[ry_b]
                r_off = ry_b * row_size
                for rx_b in range(bw):
                    if rb[rx_b]:
                        idx_w = r_off + (rx_b >> 5)
                        u_w = (bits[idx_w] & 0xFFFFFFFF) | (1 << (rx_b & 31))
                        bits[idx_w] = u_w - 0x100000000 if u_w >= 0x80000000 else u_w
            bits_arr = _JArrZx("[I", bits)
            return JObject("Lcom/google/zxing/common/BitMatrix;", {
                "a": bw, "b": bh, "c": row_size, "d": bits_arr,
                "width": bw, "height": bh, "rowSize": row_size, "bits": bits_arr,
                "_fast_rows": fast_rows,
            })

        def _vhook(rcls, cls, name, sig, logical, static, _vm):
            if static and cls == "Landroidx/transition/TransitionManager;" and name == "beginDelayedTransition":
                return True, None
            if static and cls == "Lcom/google/android/gms/maps/MapsInitializer;":
                return True, (0 if sig.endswith(")I") else None)
            if not static and cls == "Lcom/google/android/gms/maps/MapView;" and (name == "<init>" or sig.endswith(")V")):
                if logical and isinstance(logical[0], JObject):
                    _node(logical[0], "FrameLayout")
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
            if not static and cls in ("Landroid/graphics/PorterDuffColorFilter;", "Landroid/graphics/BlendModeColorFilter;") and name == "<init>" and len(logical) > 1 and isinstance(logical[0], JObject):
                if isinstance(logical[1], int):
                    logical[0].fields["$color"] = int(logical[1])
                return True, None
            if static and name == "getDrawable" and cls in (
                "Landroidx/core/content/ContextCompat;",
                "Landroidx/appcompat/content/res/AppCompatResources;",
                "Landroidx/core/content/res/ResourcesCompat;",
            ) and len(logical) > 1 and isinstance(logical[1], int):
                return True, _make_drawable_jobj(logical[1])
            if name == "getFont" and cls in (
                "Landroidx/core/content/res/ResourcesCompat;",
                "Landroid/content/res/Resources;",
            ):
                tf_obj = JObject("Landroid/graphics/Typeface;", {"style": 0})
                for arg_f in logical:
                    if isinstance(arg_f, JObject) and "FontCallback" in (arg_f.cls or ""):
                        try:
                            _vm.invoke_virtual(arg_f, "onFontRetrieved", "(Landroid/graphics/Typeface;)V", (arg_f, tf_obj))
                        except Exception:
                            pass
                return True, (None if sig.endswith(")V") else tf_obj)
            if static and cls == "Landroidx/core/graphics/drawable/DrawableCompat;" and name in ("wrap", "unwrap") and logical:
                return True, logical[0]
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
            if cls == "Landroidx/recyclerview/widget/RecyclerView$ViewHolder;" and name == "<init>" and sig == "(Landroid/view/View;)V":
                if len(logical) > 1 and isinstance(logical[1], JObject):
                    recv.fields["itemView"] = logical[1]
                return True, None
            if name in ("getAdapterPosition", "getBindingAdapterPosition", "getAbsoluteAdapterPosition", "getLayoutPosition") and sig == "()I" and "itemView" in recv.fields:
                return True, int(recv.fields.get("_adapter_pos", 0) or 0)
            if name == "run" and sig == "()V" and "$pre_draw_listener" in recv.fields:
                lst_obj = recv.fields.get("$pre_draw_listener")
                vto_obj = recv.fields.get("$pre_draw_vto")
                cur_lst = vto_obj.fields.get("_pre_draw", []) if isinstance(vto_obj, JObject) else [lst_obj]
                if isinstance(lst_obj, JObject) and lst_obj in cur_lst:
                    try:
                        _vm.invoke_virtual(lst_obj, "onPreDraw", "()Z", (lst_obj,))
                    except Exception:
                        pass
                    if isinstance(cur_lst, list) and lst_obj in cur_lst:
                        try:
                            cur_lst.remove(lst_obj)
                        except ValueError:
                            pass
                    _flush_fragment_transactions()
                    tree.touch()
                return True, None
            if (cls == "Lcom/squareup/picasso/Utils$1;" or recv.cls == "Lcom/squareup/picasso/Utils$1;") and name == "handleMessage":
                return True, None
            if (
                cls in ("Lcom/google/zxing/MultiFormatWriter;", "Lcom/google/zxing/qrcode/QRCodeWriter;", "Lcom/google/zxing/Writer;")
                or recv.cls in ("Lcom/google/zxing/MultiFormatWriter;", "Lcom/google/zxing/qrcode/QRCodeWriter;")
            ) and sig.endswith(")Lcom/google/zxing/common/BitMatrix;") and len(logical) >= 5:
                content_z = _text(logical[1])
                if not content_z:
                    return False, None
                fmt_z = logical[2]
                rw_z = int(logical[3] or 0) if isinstance(logical[3], int) else 0
                rh_z = int(logical[4] or 0) if isinstance(logical[4], int) else 0
                return True, _build_zxing_bitmatrix(content_z, fmt_z, rw_z, rh_z)
            if cls == "Lcom/google/zxing/common/BitMatrix;" or recv.cls == "Lcom/google/zxing/common/BitMatrix;":
                if name in ("b", "get") and sig == "(II)Z" and len(logical) >= 3:
                    fx_z, fy_z = int(logical[1] or 0), int(logical[2] or 0)
                    fr_z = recv.fields.get("_fast_rows")
                    if isinstance(fr_z, list):
                        return True, (1 if (0 <= fy_z < len(fr_z) and 0 <= fx_z < len(fr_z[fy_z]) and fr_z[fy_z][fx_z]) else 0)
                    rs_z = int(recv.fields.get("c") or recv.fields.get("rowSize") or 0)
                    da_z = recv.fields.get("d") or recv.fields.get("bits")
                    if rs_z > 0 and hasattr(da_z, "data") and fy_z >= 0 and fx_z >= 0:
                        bi_z = fy_z * rs_z + (fx_z >> 5)
                        if 0 <= bi_z < len(da_z.data):
                            return True, ((int(da_z.data[bi_z]) >> (fx_z & 31)) & 1)
                    return True, 0
                if name == "getWidth" and sig == "()I":
                    return True, int(recv.fields.get("a") or recv.fields.get("width") or 0)
                if name == "getHeight" and sig == "()I":
                    return True, int(recv.fields.get("b") or recv.fields.get("height") or 0)
            if (cls == "Lcom/journeyapps/barcodescanner/BarcodeEncoder;" or recv.cls == "Lcom/journeyapps/barcodescanner/BarcodeEncoder;") and name == "createBitmap" and sig == "(Lcom/google/zxing/common/BitMatrix;)Landroid/graphics/Bitmap;" and len(logical) >= 2 and isinstance(logical[1], JObject):
                bm_in = logical[1]
                bw_e = max(1, int(bm_in.fields.get("a") or bm_in.fields.get("width") or 64))
                bh_e = max(1, int(bm_in.fields.get("b") or bm_in.fields.get("height") or 64))
                fr_e = bm_in.fields.get("_fast_rows")
                pxs_e = [-1] * (bw_e * bh_e)
                if isinstance(fr_e, list):
                    for yy_e in range(min(bh_e, len(fr_e))):
                        rb_e = fr_e[yy_e]
                        for xx_e in range(min(bw_e, len(rb_e))):
                            if rb_e[xx_e]:
                                pxs_e[yy_e * bw_e + xx_e] = -0x1000000
                out_bmp_e = JObject("Landroid/graphics/Bitmap;", {"_w": bw_e, "_h": bh_e, "_pixels": pxs_e, "_mut": 1, "_alpha": 1})
                try:
                    from core.java.view_breadth_g16 import encode_bitmap_png
                    encode_bitmap_png(out_bmp_e)
                except Exception:
                    pass
                return True, out_bmp_e
            if (cls == "Lcom/squareup/picasso/Picasso;" or "Picasso" in (recv.cls or "")) and name in ("load", "d") and sig.endswith(")Lcom/squareup/picasso/RequestCreator;") and len(logical) > 1:
                if isinstance(logical[1], int):
                    rid_p = int(logical[1])
                    return True, JObject("Lcom/squareup/picasso/RequestCreator;", {
                        "a": recv,
                        "b": JObject("Lcom/squareup/picasso/Request$Builder;", {"b": rid_p}),
                        "_res_id": rid_p,
                        "e": 0,
                    })
                u_str = _text(logical[1])
                u_obj = logical[1] if (isinstance(logical[1], JObject) and logical[1].cls == "Landroid/net/Uri;") else JObject("Landroid/net/Uri;", {"$uri": u_str})
                return True, JObject("Lcom/squareup/picasso/RequestCreator;", {
                    "a": recv,
                    "b": JObject("Lcom/squareup/picasso/Request$Builder;", {"a": u_obj, "b": 0}),
                    "_url": u_str,
                    "_res_id": 0,
                    "e": 0,
                })
            if (cls == "Lcom/squareup/picasso/RequestCreator;" or recv.cls == "Lcom/squareup/picasso/RequestCreator;") and name in ("into", "c") and len(logical) > 1 and isinstance(logical[1], JObject):
                iv = logical[1]
                cb = logical[2] if (len(logical) > 2 and isinstance(logical[2], JObject)) else None
                req_b = recv.fields.get("b")
                uri_o = req_b.fields.get("a") if isinstance(req_b, JObject) else None
                url_s = str(recv.fields.get("_url") or _text(uri_o) or "").strip()
                res_id = int((req_b.fields.get("b") if isinstance(req_b, JObject) else 0) or recv.fields.get("_res_id") or 0)
                err_id = int(recv.fields.get("e") or 0)

                def _fire_picasso_cb():
                    if not isinstance(cb, JObject):
                        return
                    for cm in ("onSuccess", "a"):
                        if _vm._resolve_method(cb.cls, cb.cls, cm, "()V") is not None:
                            try:
                                _vm.invoke_virtual(cb, cm, "()V", (cb,))
                            except Exception:
                                pass
                            break

                loaded_ok = False
                if url_s.startswith(("http://", "https://")):
                    cache = stubs.__dict__.setdefault("_picasso_img_cache", {})
                    if url_s not in cache:
                        nf = getattr(stubs, "net_fire", None)
                        raw_b = b""
                        if callable(nf):
                            try:
                                resp = nf("GET", url_s, {}, b"")
                                raw_b = resp.body if hasattr(resp, "body") else (resp[1] if isinstance(resp, tuple) and len(resp) > 1 else b"")
                            except Exception:
                                raw_b = b""
                        is_img = (
                            isinstance(raw_b, (bytes, bytearray))
                            and len(raw_b) >= 24
                            and (
                                raw_b[:4] == b"\x89PNG"
                                or raw_b[:2] == b"\xff\xd8"
                                or (raw_b[:4] == b"RIFF" and raw_b[8:12] == b"WEBP")
                                or raw_b[:4] == b"GIF8"
                            )
                        )
                        cache[url_s] = bytes(raw_b) if is_img else None
                    img_b = cache.get(url_s)
                    if img_b:
                        iv_n = _node(iv, "ImageView")
                        if iv_n is not None:
                            iv_n.props.pop("src_null", None)
                            iv_n.props["img_bytes"] = img_b
                            tree.touch()
                        loaded_ok = True
                        _fire_picasso_cb()
                if not loaded_ok and res_id > 0:
                    _set_image_res([iv, res_id])
                    loaded_ok = True
                    _fire_picasso_cb()
                elif not loaded_ok and err_id > 0:
                    _set_image_res([iv, err_id])
                return True, None
            if cls == "Landroid/view/ViewTreeObserver;" or "ViewTreeObserver" in (recv.cls or ""):
                if name == "addOnPreDrawListener" and len(logical) > 1 and isinstance(logical[1], JObject):
                    lst = recv.fields.setdefault("_pre_draw", [])
                    if logical[1] not in lst:
                        lst.append(logical[1])
                    if hasattr(stubs, "_queue_runnable"):
                        stubs._queue_runnable(
                            JObject("Ljava/lang/Runnable;", {"$pre_draw_listener": logical[1], "$pre_draw_vto": recv}),
                            0,
                        )
                    return True, None
                if name == "removeOnPreDrawListener" and len(logical) > 1:
                    lst = recv.fields.get("_pre_draw")
                    if isinstance(lst, list) and logical[1] in lst:
                        lst.remove(logical[1])
                    return True, None
                if name == "isAlive":
                    return True, 1
            if cls == "Landroidx/recyclerview/widget/GridLayoutManager$SpanSizeLookup;" and name == "<init>":
                return True, None
            _is_typed_array = ("TypedArray" in (recv.cls or "")) or ("TypedArray" in (cls or ""))
            if _is_typed_array and name in ("getDrawable", "getDrawableIfKnown") and len(logical) > 1:
                idx_a = int(logical[1] or 0) if isinstance(logical[1], int) else -1
                wrapped_ta = recv.fields.get("mWrapped")
                amap = recv.fields.get("_attrs") or (wrapped_ta.fields.get("_attrs") if isinstance(wrapped_ta, JObject) else None)
                if isinstance(amap, dict) and idx_a in amap and isinstance(amap[idx_a], int) and amap[idx_a] >= 0x01000000:
                    return True, _make_drawable_jobj(amap[idx_a])
                return True, None
            if "GridLayoutManager" in (cls or "") or "GridLayoutManager" in (recv.cls or ""):
                if name == "<init>":
                    if len(logical) >= 3 and isinstance(logical[2], int):
                        recv.fields["__span_count"] = max(1, int(logical[2]))
                    return True, None
                if name == "setSpanCount" and len(logical) > 1:
                    sc = max(1, int(logical[1] or 1))
                    recv.fields["__span_count"] = sc
                    rv_o = recv.fields.get("__rv")
                    if isinstance(rv_o, JObject):
                        rv_o.fields["__span_count"] = sc
                        rv_vn = _node(rv_o, "RecyclerView")
                        if rv_vn is not None:
                            if sc >= 2:
                                rv_vn.props["span_count"] = sc
                            else:
                                rv_vn.props.pop("span_count", None)
                            tree.touch()
                        adp = rv_o.fields.get("__adapter")
                        if isinstance(adp, JObject):
                            _rv_materialize_adapter(_vm, rv_o, adp)
                    return True, None
                if name == "getSpanCount":
                    return True, int(recv.fields.get("__span_count", 1) or 1)
                if name == "setSpanSizeLookup" and len(logical) > 1:
                    recv.fields["__span_lookup"] = logical[1]
                    return True, None
                if name == "getSpanSizeLookup":
                    return True, recv.fields.get("__span_lookup")
            _rc_s = recv.cls or ""
            _c_s = cls or ""
            _is_non_res_recv = (
                _is_typed_array
                or _rc_s in ("Landroid/os/Bundle;", "Landroid/os/BaseBundle;", "Landroid/os/PersistableBundle;")
                or "SharedPreferences" in _rc_s
                or "SharedPreferences" in _c_s
                or "Cursor" in _rc_s
                or "Cursor" in _c_s
                or "SQLite" in _rc_s
                or "SQLite" in _c_s
                or "JSON" in _rc_s
                or "JSON" in _c_s
                or "Parcel" in _rc_s
                or "Parcel" in _c_s
                or "Drawable" in _rc_s
                or "Drawable" in _c_s
            )
            if name == "getColor" and sig in ("(I)I", "(ILandroid/content/res/Resources$Theme;)I") and not _is_non_res_recv and len(logical) > 1 and isinstance(logical[1], int):
                resv_c = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                col_i = resv_c.resolve_color_int(int(logical[1]), default=0xFF2F6DF0) if resv_c is not None else 0xFF2F6DF0
                return True, (0xFF2F6DF0 if col_i is None else col_i)
            if cls == "Landroidx/appcompat/app/AlertController;" and sig.startswith("(ILjava/lang/CharSequence;Landroid/content/DialogInterface$OnClickListener;") and len(logical) >= 4:
                raw_w = int(logical[1] or 0)
                which = ((raw_w + 0x80000000) & 0xFFFFFFFF) - 0x80000000
                btxt = _text(logical[2])
                blst = logical[3] if isinstance(logical[3], JObject) else None
                blist = recv.fields.setdefault("$dlg_buttons", [])
                blist[:] = [b for b in blist if b[0] != which]
                if btxt or isinstance(blst, JObject):
                    blist.append((which, btxt, blst))
                return False, None
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
            elif name == "setContentView" and not _is_act_recv and not recv.fields.get("$is_fragment") and _is_dialog_obj(recv):
                if cls == "Landroid/app/Dialog;" or _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
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
            elif name == "getDecorView" and sig == "()Landroid/view/View;" and (
                cls == "Landroid/view/Window;" or "Window" in (recv.cls or "") or "decor" in recv.fields
            ):
                return True, _ensure_decor_view(recv)
            elif name == "peekDecorView" and sig == "()Landroid/view/View;" and (
                cls == "Landroid/view/Window;" or "Window" in (recv.cls or "") or "decor" in recv.fields
            ):
                if tree.root is not None or isinstance(recv.fields.get("decor"), JObject):
                    return True, _ensure_decor_view(recv)
                return True, None
            elif name == "getLayoutInflater" and sig == "()Landroid/view/LayoutInflater;":
                return True, JObject("Landroid/view/LayoutInflater;", {})
            elif name == "getMenuInflater" and sig == "()Landroid/view/MenuInflater;":
                return True, JObject("Landroid/view/MenuInflater;", {"$synth_mi_inf": True})
            elif (
                "$synth_mi_inf" in recv.fields
                or (cls == "Landroid/view/MenuInflater;" and name == "inflate" and sig == "(ILandroid/view/Menu;)V")
            ):
                if name == "inflate" and len(logical) > 2:
                    _inflate_menu_res(logical[1], logical[2])
                return True, None
            elif "$synth_menu" in recv.fields:
                items = recv.fields.setdefault("_items", [])
                if recv.fields.get("$items") is not items:
                    recv.fields["$items"] = items
                if name == "findItem" and len(logical) > 1:
                    tid = int(logical[1] or 0)
                    return True, next(
                        (x for x in items if isinstance(x, JObject) and int(x.fields.get("$item_id", x.fields.get("_id", 0)) or 0) == tid),
                        None,
                    )
                if name == "size" and sig == "()I":
                    return True, len(items)
                if name == "getItem" and len(logical) > 1:
                    idx_m = int(logical[1] or 0)
                    return True, (items[idx_m] if 0 <= idx_m < len(items) else _mk_menu_item())
                if name == "clear":
                    items.clear()
                    return True, None
                if name == "removeItem" and len(logical) > 1:
                    tid = int(logical[1] or 0)
                    items[:] = [
                        x for x in items
                        if not (isinstance(x, JObject) and int(x.fields.get("$item_id", x.fields.get("_id", 0)) or 0) == tid)
                    ]
                    return True, None
                if name == "add":
                    if len(logical) >= 5:
                        mi_n = _mk_menu_item(item_id=logical[2], gid=logical[1], title=_text(logical[4]))
                    elif len(logical) >= 2:
                        mi_n = _mk_menu_item(title=_text(logical[1]))
                    else:
                        mi_n = _mk_menu_item()
                    items.append(mi_n)
                    return True, mi_n
                if name in ("setGroupDividerEnabled", "setQwertyMode"):
                    return True, None
            elif "$synth_mi" in recv.fields:
                if name == "getItemId":
                    return True, int(recv.fields.get("$item_id", recv.fields.get("_id", 0)) or 0)
                if name == "getGroupId":
                    return True, int(recv.fields.get("_gid", 0) or 0)
                if name == "getTitle":
                    return True, JString(str(recv.fields.get("$title", recv.fields.get("_title", "")) or ""))
                if name == "setTitle" and len(logical) > 1:
                    if isinstance(logical[1], int):
                        t_s = (_strmap_id.get(int(logical[1]), "") if _strmap_id else "")
                    else:
                        t_s = _text(logical[1])
                    recv.fields["$title"] = t_s
                    recv.fields["_title"] = t_s
                    nd_m = recv.fields.get("$node")
                    if isinstance(nd_m, ViewNode) and (nd_m.text or "").strip():
                        nd_m.text = t_s
                        tree.touch()
                    return True, recv
                if name == "isVisible":
                    return True, (1 if recv.fields.get("$visible", recv.fields.get("_vis", 1)) else 0)
                if name == "setVisible" and len(logical) > 1:
                    v_flag = bool(int(logical[1] or 0))
                    recv.fields["$visible"] = 1 if v_flag else 0
                    recv.fields["_vis"] = 1 if v_flag else 0
                    nd_m = recv.fields.get("$node")
                    if isinstance(nd_m, ViewNode):
                        nd_m.visible = v_flag
                        nd_m.props["visibility"] = 0 if v_flag else 8
                        par_m = getattr(nd_m, "_parent", None)
                        if par_m is not None and "ActionMenuView" in (par_m.cls or ""):
                            vc = sum(1 for c in par_m.children if c.visible)
                            par_m.props["w"] = max(44, max(1, vc) * 42)
                        tree.touch()
                    return True, recv
                if name == "isEnabled":
                    return True, (1 if recv.fields.get("$enabled", recv.fields.get("_en", 1)) else 0)
                if name == "setEnabled" and len(logical) > 1:
                    e_flag = bool(int(logical[1] or 0))
                    recv.fields["$enabled"] = 1 if e_flag else 0
                    recv.fields["_en"] = 1 if e_flag else 0
                    nd_m = recv.fields.get("$node")
                    if isinstance(nd_m, ViewNode):
                        nd_m.props["enabled"] = e_flag
                        tree.touch()
                    return True, recv
                if name == "isChecked":
                    return True, (1 if recv.fields.get("$checked") else 0)
                if name == "setChecked" and len(logical) > 1:
                    c_flag = bool(int(logical[1] or 0))
                    recv.fields["$checked"] = 1 if c_flag else 0
                    nd_m = recv.fields.get("$node")
                    if isinstance(nd_m, ViewNode):
                        nd_m.props["selected"] = c_flag
                        tree.touch()
                    return True, recv
                if name == "setIcon" and len(logical) > 1:
                    recv.fields["_icon"] = logical[1]
                    return True, recv
                if name == "getIcon":
                    return True, recv.fields.get("_icon")
                if name == "getActionView":
                    return True, recv.fields.get("_av")
                if name == "setActionView" and len(logical) > 1:
                    recv.fields["_av"] = logical[1]
                    return True, recv
                if name in ("expandActionView", "collapseActionView", "isActionViewExpanded"):
                    return True, 0
                if name == "setShowAsAction":
                    return True, None
                if sig.endswith(")Landroid/view/MenuItem;"):
                    return True, recv
            elif name == "cloneInContext" and sig == "(Landroid/content/Context;)Landroid/view/LayoutInflater;":
                return True, recv
            elif name == "inflate" and sig in ("(ILandroid/view/ViewGroup;)Landroid/view/View;",
                                               "(ILandroid/view/ViewGroup;Z)Landroid/view/View;") and _inflate_fn is not None:
                return True, _inflate_fn(logical)
            elif name == "getDrawable" and sig in (
                "(I)Landroid/graphics/drawable/Drawable;",
                "(ILandroid/content/res/Resources$Theme;)Landroid/graphics/drawable/Drawable;",
            ) and not _is_non_res_recv:
                rid = int(logical[1] or 0) if len(logical) > 1 else 0
                return True, _make_drawable_jobj(rid)
            elif name == "getStringArray" and sig == "(I)[Ljava/lang/String;" and _get_str_arr is not None and not _is_non_res_recv:
                return True, _get_str_arr(logical)
            elif name == "getIntArray" and sig == "(I)[I" and _get_int_arr is not None and not _is_non_res_recv:
                return True, _get_int_arr(logical)
            elif name == "getBoolean" and sig == "(I)Z" and _get_bool is not None and not _is_non_res_recv:
                return True, _get_bool(logical)
            elif name == "getInteger" and sig == "(I)I" and _get_integer is not None and not _is_non_res_recv:
                return True, _get_integer(logical)
            elif name in ("getDimension", "getDimensionPixelSize", "getDimensionPixelOffset") and sig in ("(I)F", "(I)I") and _get_dimension is not None and not _is_non_res_recv:
                return True, _get_dimension(logical, as_int=(name != "getDimension"))
            elif name == "getString" and sig == "(I[Ljava/lang/Object;)Ljava/lang/String;" and _get_string_fmt is not None and not _is_non_res_recv:
                return True, _get_string_fmt(logical)
            elif name == "getString" and sig == "(I)Ljava/lang/String;" and _get_string is not None and not _is_non_res_recv:
                if _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    return True, _get_string(logical)
            elif name == "getText" and sig == "(I)Ljava/lang/CharSequence;" and _get_string is not None and not _is_non_res_recv and len(logical) > 1 and isinstance(logical[1], int):
                if _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    return True, _get_string(logical)
            elif name == "<init>" and sig == "(Landroidx/fragment/app/FragmentManager;)V" and len(logical) > 1 and isinstance(logical[1], JObject) and logical[1].fields.get("$synth_fm"):
                recv.fields["$fm"] = logical[1]
                recv.fields["$ops"] = []
                return True, None
            elif name in ("getSupportFragmentManager", "getChildFragmentManager", "getParentFragmentManager", "getFragmentManager"):
                _std_fm_sig = sig in ("()Landroidx/fragment/app/FragmentManager;", "()Landroid/app/FragmentManager;")
                if ((_is_act_recv or recv.fields.get("$is_fragment")) and _std_fm_sig) or _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    if name == "getParentFragmentManager" and recv.fields.get("$is_fragment"):
                        pfm = recv.fields.get("_parent_fm")
                        if isinstance(pfm, JObject) and pfm.fields.get("$synth_fm"):
                            return True, pfm
                        cur_act = getattr(stubs, "_current_activity", None)
                        if isinstance(cur_act, JObject) and isinstance(cur_act.fields.get("$synth_fm_inst"), JObject):
                            pfm = cur_act.fields["$synth_fm_inst"]
                            if tree.root is not None:
                                pfm.fields["_host_node"] = tree.root
                            return True, pfm
                    is_child_fm = (name == "getChildFragmentManager")
                    cache_k = "$synth_child_fm_inst" if is_child_fm else "$synth_fm_inst"
                    cached_fm = recv.fields.get(cache_k)
                    if isinstance(cached_fm, JObject) and cached_fm.fields.get("$synth_fm"):
                        if _is_act_recv and tree.root is not None:
                            cached_fm.fields["_host_node"] = tree.root
                        return True, cached_fm
                    hview = recv.fields.get("_view")
                    hnode = (tree.root if _is_act_recv else (hview.fields.get("$view") if isinstance(hview, JObject) else None))
                    fm_obj = JObject("Landroidx/fragment/app/FragmentManager;", {
                        "$synth_fm": True,
                        "_host_node": hnode,
                        "_is_child_fm": is_child_fm,
                        "_fragments": [],
                        "_by_id": {},
                        "_by_tag": {},
                    })
                    recv.fields[cache_k] = fm_obj
                    return True, fm_obj
                return False, None
            elif (recv.cls == "Landroidx/fragment/app/FragmentManager;" and recv.fields.get("$synth_fm")) or recv.fields.get("$synth_fm"):
                if name == "beginTransaction" or sig in ("()Landroidx/fragment/app/FragmentTransaction;", "()Landroidx/fragment/app/d0;"):
                    return True, JObject("Landroidx/fragment/app/FragmentTransaction;", {"$fm": recv, "$ops": []})
                if name in ("executePendingTransactions", "execPendingActions"):
                    return True, 1 if _flush_fragment_transactions() else 0
                if name in ("isStateSaved", "isDestroyed", "getBackStackEntryCount", "popBackStackImmediate"):
                    return True, 0
                if name in (
                    "popBackStack", "registerFragmentLifecycleCallbacks", "unregisterFragmentLifecycleCallbacks",
                    "addOnBackStackChangedListener", "removeOnBackStackChangedListener",
                    "addFragmentOnAttachListener", "removeFragmentOnAttachListener", "saveFragmentInstanceState",
                ):
                    return True, None
                if (name == "findFragmentByTag" or sig == "(Ljava/lang/String;)Landroidx/fragment/app/Fragment;") and len(logical) > 1:
                    t_arg = logical[1]
                    t_s = (
                        t_arg if isinstance(t_arg, str)
                        else (str(t_arg.text) if hasattr(t_arg, "text")
                              else (str(t_arg.fields.get("$str", "")) if isinstance(t_arg, JObject) else ""))
                    )
                    frag = (recv.fields.get("_by_tag") or {}).get(t_s)
                    if frag is None and not recv.fields.get("_is_child_fm"):
                        frag = getattr(stubs, "_fragments_by_tag", {}).get(t_s)
                    return True, frag
                if name == "getFragments":
                    own = [fv for fv in (recv.fields.get("_fragments") or ()) if isinstance(fv, JObject)]
                    return True, JObject("Ljava/util/ArrayList;", {"__items": own, "$items": own})
                if (name == "findFragmentById" or sig == "(I)Landroidx/fragment/app/Fragment;") and len(logical) > 1:
                    fid = logical[1]
                    fby = recv.fields.get("_by_id") or {}
                    frag = fby.get(fid) or (fby.get(_idmap_id_ref[fid]) if fid in _idmap_id_ref else None)
                    if frag is None and not recv.fields.get("_is_child_fm"):
                        fmap = getattr(stubs, "_fragments_by_id", {})
                        frag = fmap.get(fid) or (fmap.get(_idmap_id_ref[fid]) if fid in _idmap_id_ref else None)
                    return True, frag
            elif "$ops" in recv.fields:
                if (
                    name in ("replace", "add")
                    or sig in (
                        "(ILandroidx/fragment/app/Fragment;Ljava/lang/String;)V",
                        "(ILandroidx/fragment/app/Fragment;Ljava/lang/String;I)V",
                    )
                    or sig.startswith("(ILandroidx/fragment/app/Fragment;)")
                ) and len(logical) > 2:
                    if isinstance(logical[1], JObject):
                        t_s = _text(logical[2]) if logical[2] not in (None, 0) else ""
                        recv.fields["$ops"].append(("add", None, logical[1], t_s))
                    else:
                        op_kind = name
                        if sig == "(ILandroidx/fragment/app/Fragment;Ljava/lang/String;)V" or sig.startswith("(ILandroidx/fragment/app/Fragment;)"):
                            op_kind = "replace" if name != "add" else "add"
                        elif sig == "(ILandroidx/fragment/app/Fragment;Ljava/lang/String;I)V" and len(logical) > 4:
                            op_kind = "replace" if int(logical[4] or 0) == 2 else "add"
                        t_s = _text(logical[3]) if (len(logical) > 3 and isinstance(logical[3], (JObject, JString, str)) and logical[3] not in (None, 0)) else ""
                        recv.fields["$ops"].append((op_kind, logical[1], logical[2], t_s))
                    return True, (None if sig.endswith(")V") else recv)
                if name == "remove" and len(logical) > 1:
                    recv.fields["$ops"].append(("remove", logical[1]))
                    return True, (None if sig.endswith(")V") else recv)
                if name in ("show", "hide") and len(logical) > 1:
                    recv.fields["$ops"].append((name, logical[1]))
                    return True, (None if sig.endswith(")V") else recv)
                if name in (
                    "addToBackStack", "setReorderingAllowed", "setCustomAnimations",
                    "setTransition", "attach", "detach", "setMaxLifecycle",
                    "setPrimaryNavigationFragment", "runOnCommit",
                ):
                    return True, (None if sig.endswith(")V") else recv)
                if name in ("commit", "commitAllowingStateLoss") or sig in ("()I", "(ZZ)I"):
                    stubs._pending_fragment_txs.append({"fm": recv.fields.get("$fm"), "ops": list(recv.fields.get("$ops", []))})
                    if tree.root is not None and not getattr(stubs, "_in_activity_oncreate", False):
                        _flush_fragment_transactions()
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
                fby = recv.fields.get("_by_id") or {}
                frag = fby.get(fid) or (fby.get(_idmap_id_ref[fid]) if fid in _idmap_id_ref else None)
                if frag is None and not recv.fields.get("_is_child_fm"):
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
                frag = (recv.fields.get("_by_tag") or {}).get(t_s)
                if frag is None and not recv.fields.get("_is_child_fm"):
                    frag = getattr(stubs, "_fragments_by_tag", {}).get(t_s)
                if frag is not None:
                    return True, frag
                return False, None
            elif name == "getFragments" and sig == "()Ljava/util/List;" and "FragmentManager" in (cls or recv.cls or ""):
                own = [fv for fv in (recv.fields.get("_fragments") or ()) if isinstance(fv, JObject)]
                return True, JObject("Ljava/util/ArrayList;", {"__items": own, "$items": own})
            elif "$synth_lo" in recv.fields:
                if name == "getLifecycle":
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/Lifecycle;"
                    return True, recv.fields.setdefault("_lc", JObject(ret_c, {"$synth_lc": True, "_owner": recv}))
            elif "$synth_lc" in recv.fields:
                if name == "getCurrentState":
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/Lifecycle$State;"
                    return True, _lifecycle_resumed_state(_vm, ret_c)
                if name == "addObserver" and len(logical) > 1 and isinstance(logical[1], JObject):
                    obs = logical[1]
                    recv.fields.setdefault("_observers", []).append(obs)
                    if _vm._resolve_method(obs.cls, obs.cls, "b", "(Z)V") is not None:
                        _safe_frag_call(_vm, obs, "b", "(Z)V", (obs, 1))
                    elif _vm._resolve_method(obs.cls, obs.cls, "activeStateChanged", "(Z)V") is not None:
                        _safe_frag_call(_vm, obs, "activeStateChanged", "(Z)V", (obs, 1))
                    return True, None
                if name == "removeObserver":
                    obs_list = recv.fields.get("_observers")
                    if isinstance(obs_list, list) and len(logical) > 1 and logical[1] in obs_list:
                        try:
                            obs_list.remove(logical[1])
                        except ValueError:
                            pass
                    return True, None
                if sig.endswith(")V"):
                    return True, None
            elif "$synth_vm_factory" in recv.fields:
                if name == "create" and len(logical) > 1:
                    vm_inst = _vm_create_for(_vm, logical[1])
                    if vm_inst is not None:
                        return True, vm_inst
            elif recv.fields.get("$is_fragment") or _vm._is_subclass(recv.cls, "Landroidx/fragment/app/Fragment;"):
                if cls == "Landroidx/fragment/app/Fragment;" and name in (
                    "<init>", "onCreate", "onViewCreated", "onStart", "onResume", "onAttach"
                ):
                    return True, None
                if name == "setArguments" and sig == "(Landroid/os/Bundle;)V":
                    recv.fields["$is_fragment"] = True
                    recv.fields["mArguments"] = logical[1] if len(logical) > 1 else None
                    return True, None
                if name in ("getActivity", "requireActivity", "getContext", "requireContext", "getHost"):
                    return True, (recv.fields.get("_host_act") or getattr(stubs, "_current_activity", None))
                if name in ("getLayoutInflater", "onGetLayoutInflater"):
                    return True, JObject("Landroid/view/LayoutInflater;", {})
                if name in ("getView", "requireView"):
                    return True, recv.fields.get("_view")
                if name in ("getArguments", "requireArguments"):
                    b = recv.fields.get("mArguments") or recv.fields.get("_args")
                    return True, (b if isinstance(b, JObject) else stubs.make_bundle())
                if name == "getViewLifecycleOwner":
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/LifecycleOwner;"
                    return True, recv.fields.setdefault("_vlo", JObject(ret_c, {"$synth_lo": True, "_frag": recv}))
                if name == "getLifecycle":
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/Lifecycle;"
                    return True, recv.fields.setdefault("_lc", JObject(ret_c, {"$synth_lc": True, "_owner": recv}))
                if name == "getViewModelStore":
                    vms = recv.fields.get("_vm_store")
                    if not isinstance(vms, JObject):
                        ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/ViewModelStore;"
                        vms = JObject(ret_c, {})
                        if _vm._resolve_method(ret_c, ret_c, "<init>", "()V") is not None:
                            _safe_frag_call(_vm, vms, "<init>", "()V", (vms,))
                        recv.fields["_vm_store"] = vms
                    return True, vms
                if name == "getDefaultViewModelCreationExtras":
                    cached_ex = recv.fields.get("_vm_extras")
                    if isinstance(cached_ex, JObject):
                        return True, cached_ex
                    ret_c = sig.split(")")[1] if ")" in sig else "Ljava/lang/Object;"
                    ex_obj = _vm_creation_extras_for(_vm, ret_c)
                    recv.fields["_vm_extras"] = ex_obj
                    return True, ex_obj
                if name == "getDefaultViewModelProviderFactory":
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/ViewModelProvider$Factory;"
                    return True, recv.fields.setdefault("_vm_factory", JObject(ret_c, {"$synth_vm_factory": True, "_owner": recv}))
                if name == "isAdded":
                    return True, 1
                if name in ("isDetached", "isRemoving"):
                    return True, 0
                if name == "show" and (
                    "DialogFragment" in (recv.cls or "") or _vm._is_subclass(recv.cls, "Landroidx/fragment/app/DialogFragment;")
                ):
                    if sig == "(Landroidx/fragment/app/FragmentManager;Ljava/lang/String;)V":
                        return True, None
                    if sig == "(Landroidx/fragment/app/FragmentTransaction;Ljava/lang/String;)I":
                        return True, 1
            elif _is_act_recv and name in ("getViewModelStore", "getDefaultViewModelProviderFactory", "getDefaultViewModelCreationExtras", "getLifecycle"):
                if _vm._resolve_method(recv.cls, recv.cls, name, sig) is None:
                    if name == "getLifecycle":
                        ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/Lifecycle;"
                        return True, recv.fields.setdefault("_lc", JObject(ret_c, {"$synth_lc": True, "_owner": recv}))
                    if name == "getViewModelStore":
                        vms = recv.fields.get("_vm_store")
                        if not isinstance(vms, JObject):
                            ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/ViewModelStore;"
                            vms = JObject(ret_c, {})
                            if _vm._resolve_method(ret_c, ret_c, "<init>", "()V") is not None:
                                _safe_frag_call(_vm, vms, "<init>", "()V", (vms,))
                            recv.fields["_vm_store"] = vms
                        return True, vms
                    if name == "getDefaultViewModelCreationExtras":
                        ret_c = sig.split(")")[1] if ")" in sig else "Ljava/lang/Object;"
                        return True, recv.fields.setdefault("_vm_extras", _vm_creation_extras_for(_vm, ret_c))
                    ret_c = sig.split(")")[1] if ")" in sig else "Landroidx/lifecycle/ViewModelProvider$Factory;"
                    return True, recv.fields.setdefault("_vm_factory", JObject(ret_c, {"$synth_vm_factory": True, "_owner": recv}))
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
                                    vis_m[_rev_idmap[nd.vid]] = int(nd.props.get("visibility", 0 if nd.visible else 4))
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
                if (
                    "ViewPager" in (recv.cls or "")
                    and "ViewPager2" not in (recv.cls or "")
                    and "RecyclerView" not in (recv.cls or "")
                    and vn.cls != "RecyclerView"
                ):
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
                    if name == "addOnPageChangeListener" and len(logical) > 1 and isinstance(logical[1], JObject):
                        recv.fields.setdefault("__vp_callbacks", []).append(logical[1])
                        return True, None
                    if name == "clearOnPageChangeListeners":
                        recv.fields["__vp_callbacks"] = []
                        return True, None
                    if name == "setOffscreenPageLimit":
                        return True, None
                if ("Toolbar" in (recv.cls or "") or vn.vid == "toolbar") and name in ("inflateMenu", "setOnMenuItemClickListener"):
                    if name == "setOnMenuItemClickListener" and len(logical) > 1:
                        recv.fields["_menu_click_listener"] = logical[1]
                        return True, None
                    if name == "inflateMenu" and len(logical) > 1:
                        menu_obj = recv.fields.setdefault("_menu", JObject("Landroid/view/Menu;", {"$synth_menu": True, "_items": []}))
                        menu_obj.fields["$items"] = menu_obj.fields.setdefault("_items", [])
                        _inflate_menu_res(logical[1], menu_obj)
                        vn.children = [c for c in vn.children if not c.props.get("$opt_menu_btn")]
                        resv = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                        for idx_m, mi in enumerate(menu_obj.fields.get("_items") or ()):
                            if not isinstance(mi, JObject):
                                continue
                            vid_m = str(mi.fields.get("$vid") or f"menu_{mi.fields.get('$item_id', idx_m)}")
                            title_m = str(mi.fields.get("$title") or mi.fields.get("_title") or "").strip()
                            vis_m = bool(mi.fields.get("$visible", mi.fields.get("_vis", 1)))
                            icon_ref = str(mi.fields.get("$icon_ref") or "").strip()
                            icon_spec = resv.resolve(icon_ref) if (icon_ref and resv is not None and hasattr(resv, "resolve")) else None
                            bprops = {"$opt_menu_btn": True, "style": "Borderless", "w": 38, "h": 38}
                            if icon_spec is not None:
                                bprops["bg_drawable"] = icon_spec
                                bprops["a11y_label"] = title_m or vid_m
                                bprops["desc"] = title_m or vid_m
                                btxt = ""
                            else:
                                btxt = title_m or vid_m
                            btn = ViewNode(cls="Button", vid=vid_m, text=btxt, visible=vis_m, clickable=True, props=bprops)
                            mi.fields["$node"] = btn
                            def _on_tb_menu(_v=None, _tb=recv, _mi=mi):
                                vm_i = getattr(stubs, "vm", None)
                                lsn = _tb.fields.get("_menu_click_listener")
                                act_c = getattr(stubs, "_current_activity", None)
                                if vm_i is not None:
                                    try:
                                        if isinstance(lsn, JObject):
                                            vm_i.invoke_virtual(lsn, "onMenuItemClick", "(Landroid/view/MenuItem;)Z", (lsn, _mi))
                                        elif isinstance(act_c, JObject):
                                            vm_i.invoke_virtual(act_c, "onOptionsItemSelected", "(Landroid/view/MenuItem;)Z", (act_c, _mi))
                                    except _VM_STOP_EXCS:
                                        raise
                                    except Exception:
                                        pass
                                _drain_after_ui_event()
                            btn.on_click = _on_tb_menu
                            vn.add(btn)
                        tree.touch()
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
                if name in ("setColorFilter", "setImageTintList") and len(logical) > 1:
                    if logical[1] in (None, 0):
                        vn.props.pop("tint", None)
                    else:
                        _, hex_col = _extract_color_int(logical[1])
                        if hex_col:
                            vn.props["tint"] = hex_col
                    tree.touch()
                    return True, None
                if name == "clearColorFilter":
                    vn.props.pop("tint", None)
                    tree.touch()
                    return True, None
                if name in ("getWidth", "getHeight", "getMeasuredWidth", "getMeasuredHeight"):
                    tree.ensure_layout()
                    is_w = name in ("getWidth", "getMeasuredWidth")
                    return True, int(vn.bounds[2 if is_w else 3] or (tree.width if is_w else 200))
                if name in ("getPaddingLeft", "getPaddingTop", "getPaddingRight", "getPaddingBottom"):
                    return True, 0
                if name in ("setPadding", "setPaddingRelative", "setTranslationX", "setTranslationY"):
                    return True, None
                if name == "requestFocus":
                    return True, 1
                if name == "getViewTreeObserver" and sig == "()Landroid/view/ViewTreeObserver;":
                    return True, recv.fields.setdefault("_vto", JObject("Landroid/view/ViewTreeObserver;", {"_pre_draw": [], "_owner": recv}))
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
                    ci, hex_col = _extract_color_int(logical[1])
                    if ci is not None and ((ci >> 24) & 0xFF) <= 10 and (ci > 0xFFFFFF or ci == 0):
                        vn.props.pop("bg_drawable", None)
                        recv.fields.pop("_bg_drv_obj", None)
                        tree.touch()
                    elif hex_col:
                        spec = {
                            "kind": "shape", "shape": "rectangle",
                            "fill": hex_col, "outline": hex_col,
                            "radius": 0 if vn.cls == "View" else (8 if vn.cls == "CardView" else 4),
                        }
                        if ci is not None and ci > 0xFFFFFF and ((ci >> 24) & 0xFF) < 255:
                            spec["alpha"] = ((ci >> 24) & 0xFF) / 255.0
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
                    elif par_cls in ("FrameLayout", "CardView", "MaterialCardView"):
                        lp_cls = "Landroid/widget/FrameLayout$LayoutParams;"
                    elif par_cls == "RelativeLayout":
                        lp_cls = "Landroid/widget/RelativeLayout$LayoutParams;"
                    else:
                        lp_cls = "Landroidx/constraintlayout/widget/ConstraintLayout$LayoutParams;"
                    return True, recv.fields.setdefault("_lp", JObject(lp_cls, {}))
                if name == "setLayoutParams" and sig == "(Landroid/view/ViewGroup$LayoutParams;)V":
                    if len(logical) > 1:
                        recv.fields["_lp"] = logical[1]
                        _sync_lp_to_node(vn, logical[1])
                    return True, None
                if (
                    "BottomNavigationView" in (cls or "")
                    or "BottomNavigationView" in (recv.cls or "")
                    or "NavigationBarView" in (cls or "")
                    or "NavigationBarView" in (recv.cls or "")
                    or vn.props.get("role") == "navbar"
                ):
                    def _invoke_nav_tab(nav_obj, nav_node, tab_node, reselect=False):
                        item_id = int(tab_node.props.get("menu_item_id") or 0)
                        for sib in nav_node.children:
                            if sib.props.get("$nav_tab"):
                                sib.props["selected"] = (sib is tab_node)
                        nav_obj.fields["$selected_id"] = item_id
                        _refresh_node_state_visuals(nav_node)
                        mi = _mk_menu_item(item_id=item_id, vid=tab_node.vid or "", title=tab_node.text or "", node=tab_node)
                        lst = nav_obj.fields.get("$nav_reselect_listener") if (reselect and isinstance(nav_obj.fields.get("$nav_reselect_listener"), JObject)) else nav_obj.fields.get("$nav_listener")
                        if isinstance(lst, JObject):
                            cand_names = (
                                ("onNavigationItemReselected", "onItemReselected") if (reselect and lst is nav_obj.fields.get("$nav_reselect_listener"))
                                else ("onNavigationItemSelected", "onItemSelected")
                            )
                            old_b = getattr(_vm, "step_budget", None)
                            cur_s = int(getattr(_vm, "steps", 0) or 0)
                            if isinstance(old_b, int) and old_b > 0:
                                _vm.step_budget = max(old_b, cur_s + 300_000)
                            try:
                                invoked = False
                                for mnm in cand_names:
                                    for lsig in ("(Landroid/view/MenuItem;)Z", "(Landroid/view/MenuItem;)V"):
                                        md = _vm._resolve_method(lst.cls, lst.cls, mnm, lsig)
                                        if md is not None:
                                            _vm._run_mdef(md, [lst, mi])
                                            invoked = True
                                            break
                                    if invoked:
                                        break
                                if not invoked and hasattr(_vm, "registry"):
                                    for (mcls, _mnm, msig), md2 in list(_vm.registry.items()):
                                        if mcls == lst.cls and md2.insns and msig in ("(Landroid/view/MenuItem;)Z", "(Landroid/view/MenuItem;)V"):
                                            _vm._run_mdef(md2, [lst, mi])
                                            break
                            except _VM_STOP_EXCS:
                                raise
                            except Exception:
                                pass
                            finally:
                                if isinstance(old_b, int) and old_b > 0:
                                    end_s = int(getattr(_vm, "steps", 0) or 0)
                                    rem = max(0, old_b - cur_s)
                                    _vm.step_budget = max(old_b, end_s + min(rem, 100_000))
                        if not getattr(stubs, "_in_activity_oncreate", False):
                            _drain_after_ui_event()
                        tree.touch()

                    def _bind_navbar_tabs(nav_obj, nav_node):
                        for tab_n in nav_node.children:
                            if not tab_n.props.get("$nav_tab"):
                                continue
                            def _on_tab(_v=None, _no=nav_obj, _nn=nav_node, _tn=tab_n):
                                was_sel = bool(_tn.props.get("selected"))
                                _invoke_nav_tab(_no, _nn, _tn, reselect=was_sel)
                            tab_n.on_click = _on_tab

                    if name in ("setOnNavigationItemSelectedListener", "setOnItemSelectedListener") and len(logical) > 1:
                        recv.fields["$nav_listener"] = logical[1]
                        _bind_navbar_tabs(recv, vn)
                        return True, None
                    if name in ("setOnNavigationItemReselectedListener", "setOnItemReselectedListener") and len(logical) > 1:
                        recv.fields["$nav_reselect_listener"] = logical[1]
                        _bind_navbar_tabs(recv, vn)
                        return True, None
                    if name == "setSelectedItemId" and len(logical) > 1:
                        sel_id = int(logical[1] or 0)
                        recv.fields["$selected_id"] = sel_id
                        match_tab = None
                        for tab_n in vn.children:
                            if tab_n.props.get("$nav_tab"):
                                is_m = (int(tab_n.props.get("menu_item_id") or 0) == sel_id)
                                tab_n.props["selected"] = is_m
                                if is_m:
                                    match_tab = tab_n
                        if match_tab is not None and isinstance(recv.fields.get("$nav_listener"), JObject):
                            _invoke_nav_tab(recv, vn, match_tab, reselect=False)
                        else:
                            _refresh_node_state_visuals(vn)
                            tree.touch()
                        return True, None
                    if name == "getSelectedItemId":
                        if "$selected_id" in recv.fields:
                            return True, int(recv.fields["$selected_id"] or 0)
                        for tab_n in vn.children:
                            if tab_n.props.get("$nav_tab") and tab_n.props.get("selected"):
                                return True, int(tab_n.props.get("menu_item_id") or 0)
                        return True, 0
                    if name == "getMenu" and sig == "()Landroid/view/Menu;":
                        mitems = [
                            _mk_menu_item(
                                item_id=tab_n.props.get("menu_item_id", 0),
                                vid=tab_n.vid or "",
                                title=tab_n.text or "",
                                visible=tab_n.visible,
                                enabled=bool(tab_n.props.get("enabled", True)),
                                node=tab_n,
                            )
                            for tab_n in vn.children
                            if tab_n.props.get("$nav_tab")
                        ]
                        m_obj = JObject("Landroid/view/Menu;", {"$synth_menu": True, "_items": mitems})
                        m_obj.fields["$items"] = mitems
                        return True, m_obj
                if "RecyclerView" in (recv.cls or "") or vn.cls == "RecyclerView":
                    if name == "setLayoutManager":
                        _rv_set_layout_manager(recv, vn, logical)
                        return True, None
                    if name == "getLayoutManager":
                        return True, recv.fields.get("__layout_manager")
                    if name in (
                        "scrollToPosition", "smoothScrollToPosition",
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
            if "Drawable" in (recv.cls or "") or "_drawable" in recv.fields:
                if name == "mutate":
                    return True, recv
                if name == "getState":
                    from core.java.objects import JArray
                    return True, JArray("[I", [])
                if name == "setState":
                    return True, 0
                if name in ("getIntrinsicWidth", "getIntrinsicHeight"):
                    return True, 24
                if name == "getNumberOfLayers":
                    return True, max(1, len(recv.fields.get("_layers") or [recv]))
                if name in ("getDrawable", "findDrawableByLayerId"):
                    l_idx = int(logical[1] or 0) if len(logical) > 1 and isinstance(logical[1], int) else 0
                    l_key = f"_layer_{l_idx}"
                    sub_drv = recv.fields.get(l_key)
                    if not isinstance(sub_drv, JObject):
                        base_sp = recv.fields.get("_drawable")
                        init_sp = dict(base_sp) if isinstance(base_sp, dict) else {
                            "kind": "shape", "shape": "rectangle", "fill": "", "outline": "#cbd5e1", "radius": 18.0,
                        }
                        sub_drv = JObject("Landroid/graphics/drawable/GradientDrawable;", {
                            "_owner_view": recv.fields.get("_owner_view"),
                            "_drawable": init_sp,
                            "_orig_drawable": recv.fields.get("_orig_drawable"),
                        })
                        recv.fields[l_key] = sub_drv
                    else:
                        sub_drv.fields["_owner_view"] = recv.fields.get("_owner_view")
                    return True, sub_drv
                if name == "setColor" and len(logical) > 1:
                    ci_c, hex_c = _extract_color_int(logical[1])
                    sp_c = dict(recv.fields.get("_drawable") or {"kind": "shape", "shape": "rectangle", "radius": 8.0})
                    if sp_c.get("kind") not in ("shape", "color"):
                        sp_c = {"kind": "shape", "shape": "rectangle", "radius": 18.0}
                    if ci_c is not None and ((ci_c >> 24) & 0xFF) == 0:
                        sp_c["fill"] = ""
                    elif hex_c:
                        sp_c["fill"] = hex_c
                    recv.fields["_drawable"] = sp_c
                    owner_c = recv.fields.get("_owner_view")
                    if owner_c is not None:
                        owner_c.props["bg_drawable"] = dict(sp_c)
                        tree.touch()
                    return True, None
                if name == "setStroke" and len(logical) > 2:
                    sw_c = max(0, int(logical[1] or 0)) if isinstance(logical[1], (int, float)) else 1
                    ci_s, hex_s = _extract_color_int(logical[2])
                    sp_s = dict(recv.fields.get("_drawable") or {"kind": "shape", "shape": "rectangle", "radius": 18.0})
                    if sp_s.get("kind") not in ("shape", "color"):
                        sp_s = {"kind": "shape", "shape": "rectangle", "radius": 18.0}
                    sp_s["stroke_w"] = float(sw_c)
                    if sw_c <= 0 or (ci_s is not None and ((ci_s >> 24) & 0xFF) == 0):
                        sp_s["outline"] = ""
                    elif hex_s:
                        sp_s["outline"] = hex_s
                    recv.fields["_drawable"] = sp_s
                    owner_s = recv.fields.get("_owner_view")
                    if owner_s is not None:
                        owner_s.props["bg_drawable"] = dict(sp_s)
                        tree.touch()
                    return True, None
                if name in ("setCornerRadius", "setCornerRadii") and len(logical) > 1:
                    sp_r = dict(recv.fields.get("_drawable") or {"kind": "shape", "shape": "rectangle"})
                    if name == "setCornerRadius" and isinstance(logical[1], (int, float)):
                        sp_r["radius"] = max(0.0, float(logical[1]))
                    recv.fields["_drawable"] = sp_r
                    owner_r = recv.fields.get("_owner_view")
                    if owner_r is not None:
                        owner_r.props["bg_drawable"] = dict(sp_r)
                        tree.touch()
                    return True, None
                def _restore_drawable_filter():
                    if "_orig_drawable" in recv.fields:
                        orig = recv.fields.get("_orig_drawable")
                        if isinstance(orig, dict):
                            restored = copy.deepcopy(orig)
                            recv.fields["_drawable"] = restored
                        else:
                            restored = None
                            recv.fields.pop("_drawable", None)
                    elif isinstance(recv.fields.get("_drawable"), dict):
                        restored = copy.deepcopy(recv.fields["_drawable"])
                        restored.pop("tint", None)
                        recv.fields["_drawable"] = restored
                    else:
                        restored = None
                    recv.fields.pop("_tint", None)
                    owner = recv.fields.get("_owner_view")
                    if owner is not None:
                        if isinstance(restored, dict):
                            owner.props["bg_drawable"] = dict(restored)
                        else:
                            owner.props.pop("bg_drawable", None)
                        tree.touch()
                if name in ("setColorFilter", "setTint", "setTintList"):
                    if len(logical) > 1 and logical[1] not in (None, 0):
                        _apply_drawable_tint(recv, logical[1])
                    else:
                        _restore_drawable_filter()
                    return True, None
                if name == "clearColorFilter":
                    _restore_drawable_filter()
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
                    chg_rng = None
                    if name == "notifyItemChanged" and len(logical) > 1 and isinstance(logical[1], int):
                        chg_rng = (int(logical[1]), 1)
                    elif (
                        name == "notifyItemRangeChanged"
                        and len(logical) > 2
                        and isinstance(logical[1], int)
                        and isinstance(logical[2], int)
                    ):
                        chg_rng = (int(logical[1]), int(logical[2]))
                    try:
                        _rv_materialize_adapter(_vm, recv.fields["__rv_recv"], recv, changed_range=chg_rng)
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
    try:
        from core.java.view_breadth_g16 import install_g16_stubs
        install_g16_stubs(stubs)
    except Exception:
        pass
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
    stubs._compose_saveable_active = True
    from core.java.objects import JObject, JString, JArray
    try:
        from core.java.compose_grundset import unpack_compose_color as _unpack_compose_color_fn
    except Exception:
        _unpack_compose_color_fn = None

    _M3_LIGHT_COLS = {
        "primary": 0xFF6650A4, "onprimary": 0xFFFFFFFF, "primarycontainer": 0xFFEADDFF,
        "secondary": 0xFF625B71, "onsecondary": 0xFFFFFFFF, "secondarycontainer": 0xFFE8DEF8,
        "tertiary": 0xFF7D5260, "ontertiary": 0xFFFFFFFF,
        "background": 0xFFFFFBFE, "onbackground": 0xFF1C1B1F,
        "surface": 0xFFFFFBFE, "onsurface": 0xFF1C1B1F,
        "surfacevariant": 0xFFE7E0EC, "onsurfacevariant": 0xFF49454F,
        "surfacecontainer": 0xFFF3EDF7, "surfacecontainerlow": 0xFFF7F2FA, "surfacecontainerhigh": 0xFFECE6F0,
        "outline": 0xFF79747E, "outlinevariant": 0xFFCAC4D0,
        "error": 0xFFB3261E, "onerror": 0xFFFFFFFF,
    }
    _M3_DARK_COLS = {
        "primary": 0xFFD0BCFF, "onprimary": 0xFF381E72, "primarycontainer": 0xFF4F378B,
        "secondary": 0xFFCCC2DC, "onsecondary": 0xFF332D41, "secondarycontainer": 0xFF4A4458,
        "tertiary": 0xFFEFB8C8, "ontertiary": 0xFF492532,
        "background": 0xFF1C1B1F, "onbackground": 0xFFE6E1E5,
        "surface": 0xFF1C1B1F, "onsurface": 0xFFE6E1E5,
        "surfacevariant": 0xFF49454F, "onsurfacevariant": 0xFFCAC4D0,
        "surfacecontainer": 0xFF211F26, "surfacecontainerlow": 0xFF1D1B20, "surfacecontainerhigh": 0xFF2B2930,
        "outline": 0xFF938F99, "outlinevariant": 0xFF49454F,
        "error": 0xFFF2B8B5, "onerror": 0xFF601410,
    }

    def _pack_compose_argb(argb32):
        return (int(argb32) & 0xFFFFFFFF) << 32

    def _argb32_from_compose(clong):
        if isinstance(clong, JObject):
            clong = clong.fields.get("v", clong.fields.get("value", 0))
        try:
            u64 = int(clong or 0) & 0xFFFFFFFFFFFFFFFF
        except Exception:
            return 0xFF000000
        if u64 == 0:
            return 0
        if u64 == 16:
            return 0xFF1C1B1F
        high32 = (u64 >> 32) & 0xFFFFFFFF
        low32 = u64 & 0xFFFFFFFF
        if high32 != 0 and low32 <= 63:
            return high32
        if u64 <= 0xFFFFFFFF or high32 == 0xFFFFFFFF:
            return low32
        return high32 if low32 == 0 else low32

    def _compose_color_hex_alpha(clong):
        if _unpack_compose_color_fn is not None:
            h, al = _unpack_compose_color_fn(clong)
            if h:
                return h, float(al)
        argb = _argb32_from_compose(clong)
        a = ((argb >> 24) & 0xFF) / 255.0
        return f"#{argb & 0xFFFFFF:06x}", a

    def _blend_canvas_hex(hex_rgb, alpha_f):
        if not hex_rgb or alpha_f >= 0.95:
            return hex_rgb or "#000000"
        try:
            r = int(hex_rgb[1:3], 16)
            g = int(hex_rgb[3:5], 16)
            b = int(hex_rgb[5:7], 16)
            bg = (28, 32, 40) if tree.dark_mode else (255, 255, 255)
            eff_a = max(0.12, min(1.0, float(alpha_f)))
            nr = int(round(bg[0] + (r - bg[0]) * eff_a))
            ng = int(round(bg[1] + (g - bg[1]) * eff_a))
            nb = int(round(bg[2] + (b - bg[2]) * eff_a))
            return f"#{nr:02x}{ng:02x}{nb:02x}"
        except Exception:
            return hex_rgb

    def _pack_2f(f1, f2):
        b1 = _struct.unpack("<I", _struct.pack("<f", float(f1)))[0]
        b2 = _struct.unpack("<I", _struct.pack("<f", float(f2)))[0]
        return (b1 << 32) | b2

    def _unpack_2f(jval):
        try:
            u64 = int(jval or 0) & 0xFFFFFFFFFFFFFFFF
            f1 = float(_struct.unpack("<f", _struct.pack("<I", (u64 >> 32) & 0xFFFFFFFF))[0])
            f2 = float(_struct.unpack("<f", _struct.pack("<I", u64 & 0xFFFFFFFF))[0])
            return (0.0 if math.isnan(f1) else f1), (0.0 if math.isnan(f2) else f2)
        except Exception:
            return 0.0, 0.0

    def _intern_kclass(jcls_arg):
        if isinstance(jcls_arg, JObject):
            raw = jcls_arg.fields.get("name") or jcls_arg.fields.get("$name") or jcls_arg.cls or "Ljava/lang/Object;"
            desc = raw if (raw.startswith("L") and raw.endswith(";")) else f"L{str(raw).replace('.', '/')};"
            jcls_obj = jcls_arg if jcls_arg.cls == "Ljava/lang/Class;" else JObject("Ljava/lang/Class;", {"name": desc})
        else:
            raw = str(jcls_arg or "Ljava/lang/Object;")
            desc = raw if (raw.startswith("L") and raw.endswith(";")) else f"L{raw.replace('.', '/')};"
            jcls_obj = JObject("Ljava/lang/Class;", {"name": desc})
        kc_cache = state.setdefault("kclasses", {})
        if desc not in kc_cache:
            kc_cache[desc] = JObject("Lkotlin/jvm/internal/ClassReference;", {
                "$jclass": jcls_obj,
                "jClass": jcls_obj,
                "a": jcls_obj,
                "$name": desc,
            })
        return kc_cache[desc]

    _MISSING = object()
    empty_sentinel = JObject("Landroidx/compose/runtime/Composer$Companion$Empty;")
    suspended_sentinel = JObject("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {"name": "COROUTINE_SUSPENDED"})
    composer_obj = JObject("Landroidx/compose/runtime/ComposerImpl;")
    row_scope = JObject("Landroidx/compose/foundation/layout/RowScopeInstance;")
    col_scope = JObject("Landroidx/compose/foundation/layout/ColumnScopeInstance;")
    box_scope = JObject("Landroidx/compose/foundation/layout/BoxScopeInstance;")
    lazy_item_scope = JObject("Landroidx/compose/foundation/lazy/LazyItemScope;")
    local_ctx_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$key": "ctx", "$local_kind": "ctx"})
    local_act_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "activity"})
    local_lc_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "lifecycle"})
    local_ss_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "saved_state"})
    local_view_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "view"})
    local_cfg_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "config"})
    local_res_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "resources"})
    local_ldir_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "layout_dir"})
    local_dens_key = JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$local_kind": "density"})
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
        "active_jobs": [],
        "mutation_seq": 0,
        "cv_recomposers": [],
        "coil_cache": {},
    }

    def _mark_compose_mutation():
        state["mutation_seq"] = int(state.get("mutation_seq", 0)) + 1

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
        if isinstance(v, JObject):
            for fk in ("$val", "text", "$text"):
                if fk in v.fields and v.fields[fk] is not v:
                    return _txt(v.fields[fk])
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
        except _VM_STOP_EXCS:
            raise
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
        except _VM_STOP_EXCS:
            raise
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
        except _VM_STOP_EXCS:
            raise
        except Exception:
            pass
        try:
            vm.invoke(fn.cls, "invoke",
                      "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                      (fn, lazy_item_scope, composer_obj, _boxed_int(0)))
        except _VM_STOP_EXCS:
            raise
        except Exception:
            pass

    def _invoke_fn0(fn_obj):
        fn = _unwrap_fn(fn_obj)
        if not isinstance(fn, JObject):
            return
        try:
            vm.invoke(fn.cls, "invoke", "()Ljava/lang/Object;", (fn,))
        except _VM_STOP_EXCS:
            raise
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
            "last_seq": -1,
            "slots": {},
            "slot_occ": {},
            "saveable_occ": {},
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
            comp_state["saveable_occ"] = {}
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
                    if isinstance(comp_state["act"], JObject):
                        comp_state["act"].fields["_compose_back_handlers"] = []
                    state["root_comp"] = comp_state
                    state["slot_occ"] = {}
                    state["saveable_occ"] = {}
                    state["changed_occ"] = {}
                    state["last_slot_by_method"] = {}
                    root = ViewNode(cls="LinearLayout", vid=0xC000, props={"orientation": 1, "$compose_root": True})
                    state["stack"] = [root]
                    tree.set_root(root)
                comp_state["last_seq"] = state["mutation_seq"]
                _invoke_fn2(comp_state["fn2"])
                tree.touch()
            finally:
                state["depth"] -= 1
                state["stack"] = prev_stack if prev_stack is not None else []
                state["cur_comp"] = prev_comp
                state["activity"] = prev_act

        _do_recompose._comp_state = comp_state
        comp_state["recomp_fn"] = _do_recompose
        return _do_recompose

    def _recompose():
        fn2 = state.get("root_fn2")
        if fn2 is None:
            return
        _make_recomposer(state.get("activity"), fn2, target_vn=None)()

    def _recompose_if_dirty():
        if state.get("depth", 0) > 0:
            return False
        did = False
        cur_seq = int(state.get("mutation_seq", 0))
        rc_comp = state.get("root_comp")
        if (
            isinstance(rc_comp, dict)
            and rc_comp.get("last_seq", -1) != cur_seq
            and tree.root is not None
            and tree.root.props.get("$compose_root")
            and callable(rc_comp.get("recomp_fn"))
        ):
            rc_comp["recomp_fn"]()
            did = True
        if tree.root is not None and state.get("cv_recomposers"):
            cur_nodes = set(tree.root.walk())
            for cv_comp in list(state.get("cv_recomposers") or ()):
                if (
                    isinstance(cv_comp, dict)
                    and cv_comp.get("last_seq", -1) != cur_seq
                    and cv_comp.get("target_vn") in cur_nodes
                    and callable(cv_comp.get("recomp_fn"))
                ):
                    cv_comp["recomp_fn"]()
                    did = True
        return did

    def _compose_save_state():
        return {
            "activity": state.get("activity"),
            "root_fn2": state.get("root_fn2"),
            "root_comp": state.get("root_comp"),
            "cur_comp": state.get("cur_comp"),
            "cv_recomposers": list(state.get("cv_recomposers") or ()),
            "tree_recomp": getattr(tree, "_compose_recompose", None),
        }

    def _compose_restore_state(snap):
        if not isinstance(snap, dict):
            return
        state["activity"] = snap.get("activity")
        state["root_fn2"] = snap.get("root_fn2")
        state["root_comp"] = snap.get("root_comp")
        state["cur_comp"] = snap.get("cur_comp")
        state["cv_recomposers"] = list(snap.get("cv_recomposers") or ())
        state["stack"] = []
        if "tree_recomp" in snap:
            tree._compose_recompose = snap.get("tree_recomp")

    def _reset_compose_for_new_activity():
        state["activity"] = getattr(stubs, "_current_activity", None)
        state["root_fn2"] = None
        state["root_comp"] = None
        state["cur_comp"] = None
        state["stack"] = []
        state["cv_recomposers"] = []

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
    tree.recompose_if_dirty = _recompose_if_dirty
    tree._compose_save_state = _compose_save_state
    tree._compose_restore_state = _compose_restore_state
    tree._reset_compose_for_new_activity = _reset_compose_for_new_activity

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

    def _cancel_coro_job(job_obj, _seen=None):
        if not isinstance(job_obj, JObject):
            return
        if _seen is None:
            _seen = set()
        jid = id(job_obj)
        if jid in _seen:
            return
        _seen.add(jid)
        job_obj.fields["$cancelled"] = True
        job_obj.fields["$active"] = False
        subs = job_obj.fields.pop("__flow_subs", None) or ()
        for flow_obj, coll_obj in list(subs):
            if isinstance(flow_obj, JObject):
                colls = flow_obj.fields.get("__collectors")
                if isinstance(colls, list):
                    flow_obj.fields["__collectors"] = [c for c in colls if c is not coll_obj]
            if coll_obj is not None:
                ds_colls = state.get("ds_collectors")
                if isinstance(ds_colls, list):
                    state["ds_collectors"] = [c for c in ds_colls if c is not coll_obj]
        children = list(job_obj.fields.get("__jobs") or ())
        job_obj.fields["__jobs"] = []
        for ch in children:
            _cancel_coro_job(ch, _seen)

    def _cancel_owner_coroutines(owner_obj):
        if not isinstance(owner_obj, JObject):
            return
        if owner_obj.fields.get("$is_process_owner"):
            return
        cands = [owner_obj]
        for fk in ("_owner", "lifecycleRegistry", "mLifecycleRegistry"):
            rel = owner_obj.fields.get(fk)
            if isinstance(rel, JObject) and not rel.fields.get("$is_process_owner"):
                cands.append(rel)
        for cand in cands:
            sc = cand.fields.get("$lifecycle_scope")
            if isinstance(sc, JObject) and not sc.fields.get("$is_process_owner"):
                _cancel_coro_job(sc)

    stubs._cancel_owner_coroutines = _cancel_owner_coroutines

    def _get_back_dispatcher(act_o):
        if isinstance(act_o, JObject):
            return act_o.fields.setdefault(
                "_back_dispatcher",
                JObject("Landroidx/activity/OnBackPressedDispatcher;", {"_owner_act": act_o}),
            )
        return JObject("Landroidx/activity/OnBackPressedDispatcher;", {})

    def _is_back_cb_enabled(_vm, cb):
        if not isinstance(cb, JObject) or cb.fields.get("$removed"):
            return False
        if "$enabled" in cb.fields:
            return bool(cb.fields["$enabled"])
        if "isEnabled" in cb.fields:
            return bool(cb.fields["isEnabled"])
        try:
            if _vm is not None and _vm._resolve_method(cb.cls, cb.cls, "isEnabled", "()Z") is not None:
                return bool(_vm.invoke_virtual(cb, "isEnabled", "()Z", (cb,)))
        except _VM_STOP_EXCS:
            raise
        except Exception:
            pass
        c_str = str(cb.cls or "")
        if c_str.startswith(("Landroidx/fragment/app/", "Landroidx/navigation/")):
            return False
        return True

    def _dispatch_back_press(_vm, act_o):
        act_cur = act_o if isinstance(act_o, JObject) else (
            getattr(stubs, "_current_activity", None) or state.get("activity")
        )
        try:
            from core.java.nav_rx_paging_g8 import pop_compose_nav_if_possible
            if pop_compose_nav_if_possible(stubs):
                stubs._back_callback_consumed = True
                return
        except _VM_STOP_EXCS:
            raise
        except Exception:
            pass
        if isinstance(act_cur, JObject):
            for en, fn_h in list(reversed(act_cur.fields.get("_compose_back_handlers") or ())):
                if en and isinstance(fn_h, JObject):
                    _invoke_fn0(fn_h)
                    if not (
                        getattr(stubs, "_pending_nav_target", None)
                        or getattr(stubs, "_activity_finished", False)
                    ):
                        rc = getattr(tree, "_compose_recompose", None)
                        if callable(rc):
                            rc()
                    stubs._back_callback_consumed = True
                    return
            for cb in list(reversed(act_cur.fields.get("_back_callbacks") or ())):
                if isinstance(cb, JObject) and _is_back_cb_enabled(_vm, cb):
                    try:
                        if _vm is not None and hasattr(_vm, "invoke_virtual"):
                            _vm.invoke_virtual(cb, "handleOnBackPressed", "()V", (cb,))
                        elif _vm is not None:
                            _vm.invoke(cb.cls, "handleOnBackPressed", "()V", (cb,))
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        pass
                    if not getattr(stubs, "_activity_finished", False):
                        stubs._back_callback_consumed = True
                    return
        fin = getattr(stubs, "_finish_act", None)
        if callable(fin):
            fin([act_cur] if act_cur is not None else [])
        elif act_cur is not None:
            stubs._activity_finished = True

    stubs._dispatch_back_press = lambda act_o=None: _dispatch_back_press(getattr(stubs, "vm", None), act_o)

    def _coil_fetch_img_bytes(url_s):
        if not url_s or not str(url_s).startswith(("http://", "https://")):
            return None
        s_raw = str(url_s).strip()
        scheme, rest = s_raw.split("://", 1)
        while "//" in rest:
            rest = rest.replace("//", "/")
        norm_url = f"{scheme}://{rest}"
        cache = state.setdefault("coil_cache", {})
        cands = [norm_url]
        if norm_url != s_raw:
            cands.append(s_raw)
        if "/images/profile_pic.png" in norm_url and "/thumb/userpic/" not in norm_url:
            host_p = rest.split("/", 1)[0]
            cands.append(f"{scheme}://{host_p}/thumb/userpic/l/images/profile_pic.png")
        for cand_u in cands:
            if cand_u not in cache:
                nf = getattr(stubs, "net_fire", None)
                raw_b = b""
                if callable(nf):
                    try:
                        resp = nf("GET", cand_u, {}, b"")
                        raw_b = resp.body if hasattr(resp, "body") else (
                            resp[1] if isinstance(resp, tuple) and len(resp) > 1 else b""
                        )
                    except Exception:
                        raw_b = b""
                is_img = (
                    isinstance(raw_b, (bytes, bytearray))
                    and len(raw_b) >= 24
                    and (
                        raw_b[:4] == b"\x89PNG"
                        or raw_b[:2] == b"\xff\xd8"
                        or (raw_b[:4] == b"RIFF" and raw_b[8:12] == b"WEBP")
                        or raw_b[:4] == b"GIF8"
                    )
                )
                cache[cand_u] = bytes(raw_b) if is_img else None
            if cache.get(cand_u) is not None:
                return cache[cand_u]
        return None

    def _coil_extract_model(raw_m):
        fb_rid = 0
        m = raw_m
        if isinstance(m, JObject) and "AsyncImageState" in (m.cls or ""):
            m = m.fields.get("a") if "a" in m.fields else m.fields.get("$data")
        if isinstance(m, JObject) and "ImageRequest" in (m.cls or ""):
            fb_rid = int(m.fields.get("$fallback_res") or 0)
            m = m.fields.get("$data") if "$data" in m.fields else (m.fields.get("b") or m.fields.get("data"))
        rid = 0
        url_s = ""
        if isinstance(m, int) and m > 0:
            rid = int(m)
        elif isinstance(m, JObject) and m.cls == "Ljava/lang/Integer;" and isinstance(m.fields.get("value"), int):
            rid = int(m.fields.get("value") or 0)
        elif isinstance(m, JObject) and m.cls == "Landroid/net/Uri;":
            url_s = str(m.fields.get("$uri") or m.fields.get("uri") or "").strip()
        elif isinstance(m, (JString, str, JObject)):
            url_s = _txt(m).strip()
        img_b = _coil_fetch_img_bytes(url_s) if url_s else None
        return img_b, rid, url_s, fb_rid

    def _compose_hook(rcls, cls, name, sig, args, static, _vm):
        # 1) Kotlin Reflection / Coroutines / ViewModel / SharedFlow / WorkManager
        if (
            (cls == "Lkotlin/jvm/internal/Reflection;" and name == "getOrCreateKotlinClass")
            or (cls in ("Lkotlin/jvm/internal/Reflection;", "Lkotlin/jvm/internal/ReflectionFactory;")
                and sig.endswith(")Lkotlin/reflect/KClass;"))
        ):
            k_arg = next(
                (a for a in args if isinstance(a, JObject) and a.cls == "Ljava/lang/Class;"),
                args[-1] if args else None,
            )
            return True, _intern_kclass(k_arg)

        # 1b) Activity BackDispatcher / NavigationEvent / Compose BackHandler & Coil
        if cls in (
            "Landroidx/activity/ComponentActivity;",
            "Landroidx/fragment/app/FragmentActivity;",
            "Landroidx/appcompat/app/AppCompatActivity;",
        ) and name == "getOnBackPressedDispatcher":
            return True, _get_back_dispatcher(args[0] if args else None)

        if cls == "Landroidx/activity/OnBackPressedDispatcher;":
            if name == "<init>":
                return True, None
            if name == "addCallback" and args:
                disp = args[0] if isinstance(args[0], JObject) else None
                cb = next(
                    (
                        a for a in args[1:]
                        if isinstance(a, JObject) and (
                            "Callback" in (a.cls or "")
                            or _vm._is_subclass(a.cls, "Landroidx/activity/OnBackPressedCallback;")
                        )
                    ),
                    args[-1] if len(args) > 1 else None,
                )
                act_o = (disp.fields.get("_owner_act") if isinstance(disp, JObject) else None) or getattr(stubs, "_current_activity", None)
                if isinstance(act_o, JObject) and isinstance(cb, JObject):
                    lst = act_o.fields.setdefault("_back_callbacks", [])
                    if cb not in lst:
                        lst.append(cb)
                return True, None
            if name == "onBackPressed" and sig == "()V":
                act_o = args[0].fields.get("_owner_act") if (args and isinstance(args[0], JObject)) else None
                _dispatch_back_press(_vm, act_o)
                return True, None

        if cls == "Landroidx/activity/OnBackPressedCallback;" and not static and args and isinstance(args[0], JObject):
            if name == "setEnabled" and len(args) > 1:
                args[0].fields["$enabled"] = bool(args[1])
                return False, None
            if name == "remove":
                args[0].fields["$removed"] = True
                return True, None

        if cls == "Landroidx/navigationevent/DirectNavigationEventInput;" and name == "backCompleted":
            _dispatch_back_press(_vm, getattr(stubs, "_current_activity", None) or state.get("activity"))
            return True, None

        if cls in ("Landroidx/activity/compose/BackHandlerKt;", "Landroidx/activity/compose/PredictiveBackHandlerKt;") and "BackHandler" in name:
            pairs = _args_by_type(sig, static, args)
            if name.endswith("$default") and args and isinstance(args[-1], int) and (args[-1] & 1):
                enabled = True
            else:
                enabled = bool(next((v for pt, v in pairs if pt == "Z"), 1))
            fn_cb = next((v for pt, v in pairs if ("Function0" in pt or "Function2" in pt) and isinstance(v, JObject)), None)
            act_cur = state.get("activity") or getattr(stubs, "_current_activity", None)
            if isinstance(act_cur, JObject):
                act_cur.fields.setdefault("_compose_back_handlers", []).append((enabled, fn_cb))
            return True, None

        if cls == "Landroidx/activity/compose/LocalOnBackPressedDispatcherOwner;" and name == "getCurrent":
            return True, (
                state.get("activity")
                or getattr(stubs, "_current_activity", None)
                or JObject("Landroidx/activity/OnBackPressedDispatcherOwner;")
            )

        if cls == "Lcoil/request/ImageRequest$Builder;" and not static and args and isinstance(args[0], JObject):
            b = args[0]
            if name == "<init>":
                return True, None
            if name in ("build", "a") and sig == "()Lcoil/request/ImageRequest;":
                return True, JObject("Lcoil/request/ImageRequest;", {
                    "$data": b.fields.get("$data", b.fields.get("c")),
                    "$fallback_res": b.fields.get("$fallback_res", 0),
                })
            if name in ("error", "placeholder", "fallback") and len(args) > 1 and isinstance(args[1], int):
                b.fields["$fallback_res"] = int(args[1])
                return True, b
            if name in ("data", "c") and len(args) > 1:
                b.fields["$data"] = args[1]
                return True, b
            return True, (None if sig.endswith(")V") else b)

        if cls in ("Lcoil/compose/SingletonAsyncImagePainterKt;", "Lcoil/compose/AsyncImagePainterKt;"):
            if name == "<clinit>":
                return True, None
            img_b, rid, url_s, fb_rid = _coil_extract_model(args[0] if args else None)
            if not fb_rid and len(args) > 1:
                for extra_a in args[1:]:
                    if isinstance(extra_a, JObject) and extra_a.fields.get("$res_id"):
                        fb_rid = int(extra_a.fields["$res_id"])
                        break
            return True, JObject("Lcoil/compose/AsyncImagePainter;", {
                "$img_bytes": img_b,
                "$res_id": rid or fb_rid,
                "$coil_url": url_s,
            })

        if cls == "Lcoil/compose/AsyncImagePainter;":
            if name == "<clinit>":
                return True, None
            if name == "getState" or "State" in sig.rsplit(")", 1)[-1]:
                return True, JObject("Lcoil/compose/AsyncImagePainter$State$Empty;")
            return True, (None if sig.endswith(")V") else 0)

        if cls in (
            "Lcoil/compose/AsyncImageKt;",
            "Lcoil/compose/SingletonAsyncImageKt;",
            "Lcoil/compose/SubcomposeAsyncImageKt;",
            "Lcoil/compose/SingletonSubcomposeAsyncImageKt;",
        ):
            if name == "<clinit>":
                return True, None
            pairs = _args_by_type(sig, static, args)
            img_b, rid, url_s, fb_rid = _coil_extract_model(args[0] if args else None)
            desc = next((_txt(v) for i, (pt, v) in enumerate(pairs) if i >= 1 and "String" in pt), "")
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            spec = None
            img_name = url_s or desc or "coil_image"
            eff_rid = rid or fb_rid
            if not img_b and eff_rid:
                dm = getattr(stubs, "_drawmap", None)
                try:
                    nm_r = dm.get(int(eff_rid)) if dm else None
                except Exception:
                    nm_r = None
                resv = getattr(stubs, "_drawable_resolver", None)
                if resv is not None:
                    try:
                        spec = resv.resolve(nm_r if nm_r is not None else int(eff_rid))
                    except Exception:
                        spec = None
                img_name = nm_r or f"res#{eff_rid}"
            if img_b or spec is not None:
                props = {"img": img_name, "desc": desc, "h": 84}
                if img_b:
                    props["img_bytes"] = img_b
                if spec is not None:
                    props["drawable"] = spec
                iv_n = ViewNode(cls="ImageView", props=props)
                _apply_mod_layout_props(iv_n, mod)
                _cur_parent().add(iv_n)
                tree.touch()
            return True, None

        if cls == "Lkotlin/jvm/JvmClassMappingKt;":
            if name == "getKotlinClass":
                return True, _intern_kclass(args[0] if args else None)
            if name in ("getJavaClass", "getJavaObjectType") and args and isinstance(args[0], JObject):
                jc = args[0].fields.get("$jclass") or args[0].fields.get("jClass") or args[0].fields.get("a")
                if isinstance(jc, JObject):
                    return True, jc
                return True, JObject("Ljava/lang/Class;", {"name": args[0].cls})

        if (cls in ("Lkotlin/jvm/internal/ClassBasedDeclarationContainer;",
                    "Lkotlin/jvm/internal/ClassReference;", "Lkotlin/reflect/KClass;")
                or (not static and args and isinstance(args[0], JObject)
                    and ("$jclass" in args[0].fields or "a" in args[0].fields)
                    and name in ("getJClass", "getSimpleName", "getQualifiedName"))):
            recv_k = args[0] if args and isinstance(args[0], JObject) else None
            if recv_k is not None:
                jc = recv_k.fields.get("$jclass") or recv_k.fields.get("jClass") or recv_k.fields.get("a")
                desc = str(recv_k.fields.get("$name") or (jc.fields.get("name") if isinstance(jc, JObject) else "") or "")
                if name == "getJClass":
                    return True, (jc if isinstance(jc, JObject) else JObject("Ljava/lang/Class;", {"name": desc or "Ljava/lang/Object;"}))
                qual = desc[1:-1].replace("/", ".") if (desc.startswith("L") and desc.endswith(";")) else desc.replace("/", ".")
                if name == "getSimpleName":
                    return True, JString(qual.split(".")[-1].split("$")[-1])
                if name == "getQualifiedName":
                    return True, JString(qual)

        if cls.startswith("Lkotlin/coroutines/intrinsics/IntrinsicsKt") and name == "getCOROUTINE_SUSPENDED":
            return True, _coro_suspended(_vm)

        if cls == "Lkotlinx/coroutines/flow/SharedFlowKt;" and name.startswith("MutableSharedFlow"):
            return True, JObject("Lkotlinx/coroutines/flow/SharedFlowImpl;", {"__collectors": []})

        if (cls in ("Lkotlinx/coroutines/flow/SharedFlowImpl;", "Lkotlinx/coroutines/flow/MutableSharedFlow;", "Lkotlinx/coroutines/flow/SharedFlow;")
                or (not static and args and isinstance(args[0], JObject)
                    and ("SharedFlow" in args[0].cls or ("__collectors" in args[0].fields and "$val" not in args[0].fields and "StateFlow" not in (args[0].cls or ""))))):
            recv_f = args[0] if (args and isinstance(args[0], JObject)) else None
            if recv_f is not None:
                if name == "collect" and len(args) > 1 and isinstance(args[1], JObject):
                    cur_job = state["active_jobs"][-1] if state["active_jobs"] else None
                    if isinstance(cur_job, JObject) and cur_job.fields.get("$cancelled"):
                        return True, _coro_suspended(_vm)
                    colls = recv_f.fields.setdefault("__collectors", [])
                    if args[1] not in colls:
                        colls.append(args[1])
                    if isinstance(cur_job, JObject):
                        cur_job.fields.setdefault("__flow_subs", []).append((recv_f, args[1]))
                    return True, _coro_suspended(_vm)
                if name in ("emit", "tryEmit") and len(args) > 1:
                    ev = args[1]
                    recv_f.fields["$last"] = ev
                    cont = args[2] if (len(args) > 2 and isinstance(args[2], JObject)) else JObject("Lkotlin/coroutines/Continuation;")
                    for coll in list(recv_f.fields.get("__collectors") or ()):
                        if not any(c is coll for c in (recv_f.fields.get("__collectors") or ())):
                            continue
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
                    prev_wm = state.get("in_work_manager", False)
                    try:
                        state["in_work_manager"] = True
                        _vm.lenient = True
                        _vm.step_budget = (_vm.steps + 800_000) if old_b is None else max(old_b, _vm.steps + 800_000)
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
                                    if end_md is None or mn == "doWork":
                                        end_md = cand
                                    if mn == "doWork":
                                        break
                            if end_md is not None and getattr(end_md, "name", None) == "doWork":
                                break
                            cur_c = _vm._superclass(cur_c)
                        dw = _vm._resolve_method(wcls, wcls, "doWork", "(Lkotlin/coroutines/Continuation;)Ljava/lang/Object;")
                        if dw is not None:
                            res_dw = None
                            try:
                                res_dw = _vm._run_mdef(dw, _vm._slots_from_logical(dw.sig, False, [worker, JObject("Lkotlin/coroutines/Continuation;")]))
                            except Exception:
                                res_dw = None
                            is_wrk_res = isinstance(res_dw, JObject) and "ListenableWorker$Result" in str(res_dw.cls or "")
                            if end_md is not None and not is_wrk_res:
                                _vm._run_mdef(end_md, [worker])
                        elif end_md is not None:
                            _vm._run_mdef(end_md, [worker])
                    except Exception:
                        pass
                    finally:
                        state["in_work_manager"] = prev_wm
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
            if name == "then" and not static and args and isinstance(args[0], JObject):
                reqs = list(args[0].fields.get("$requests") or [])
                for ar in args[1:]:
                    if isinstance(ar, JObject):
                        if "$worker_cls" in ar.fields:
                            reqs.append(ar)
                        elif "__items" in ar.fields:
                            reqs.extend(ar.fields["__items"])
                args[0].fields["$requests"] = reqs
                return True, args[0]
            if name in ("enqueue", "enqueueUniqueWork", "enqueueUniquePeriodicWork"):
                if not static and args and isinstance(args[0], JObject):
                    reqs = list(args[0].fields.get("$requests") or [])
                    if not reqs:
                        for ar in args[1:]:
                            if isinstance(ar, JObject):
                                if "$worker_cls" in ar.fields:
                                    reqs.append(ar)
                                elif "__items" in ar.fields:
                                    reqs.extend(ar.fields["__items"])
                    _run_work_requests(reqs)
                return True, JObject("Landroidx/work/Operation;", {})
            if "LiveData" in sig:
                return True, JObject("Landroidx/lifecycle/MutableLiveData;", {"_value": JObject("Ljava/util/ArrayList;", {"__items": []})})
            return True, JObject("Landroidx/work/Operation;", {})

        def _emit_flow_val(flow_o, ev_val, cont=None):
            flow_o.fields["$val"] = ev_val
            flow_o.fields["$state_val"] = ev_val
            _mark_compose_mutation()
            cont_o = cont if isinstance(cont, JObject) else JObject("Lkotlin/coroutines/Continuation;")
            for coll in list(flow_o.fields.get("__collectors") or ()):
                if not any(c is coll for c in (flow_o.fields.get("__collectors") or ())):
                    continue
                try:
                    _vm.invoke_virtual(
                        coll, "emit",
                        "(Ljava/lang/Object;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                        (coll, ev_val, cont_o),
                    )
                except Exception:
                    pass

        if cls == "Lkotlinx/coroutines/flow/StateFlowKt;" and (
                name == "MutableStateFlow"
                or (static and sig == "(Ljava/lang/Object;)Lkotlinx/coroutines/flow/MutableStateFlow;")):
            return True, JObject("Lkotlinx/coroutines/flow/MutableStateFlow;", {"$val": args[0] if args else None, "__collectors": []})

        if cls.startswith("Lkotlinx/coroutines/flow/FlowKt") and name == "asStateFlow" and args:
            return True, args[0]

        if not static and args and isinstance(args[0], JObject) and "$statein_tgt" in args[0].fields and name == "emit":
            tgt_sf = args[0].fields["$statein_tgt"]
            if isinstance(tgt_sf, JObject):
                _emit_flow_val(tgt_sf, args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
            return True, JObject("Lkotlin/Unit;")

        if cls.startswith("Lkotlinx/coroutines/flow/FlowKt") and name.startswith("stateIn") and args:
            init_v = args[-1] if len(args) >= 3 else None
            sf = JObject("Lkotlinx/coroutines/flow/MutableStateFlow;", {"$val": init_v, "__collectors": []})
            src_fl = args[0] if isinstance(args[0], JObject) else None
            if src_fl is not None:
                if "$val" in src_fl.fields:
                    sf.fields["$val"] = src_fl.fields["$val"]
                else:
                    sink_si = JObject("Lkotlinx/coroutines/flow/FlowCollector;", {"$statein_tgt": sf})
                    si_job = JObject("Lkotlinx/coroutines/Job;", {"$cancelled": False, "$active": True, "__flow_subs": []})
                    state["active_jobs"].append(si_job)
                    try:
                        _vm.invoke(
                            src_fl.cls, "collect",
                            "(Lkotlinx/coroutines/flow/FlowCollector;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                            (src_fl, sink_si, JObject("Lkotlin/coroutines/Continuation;")),
                        )
                    except Exception:
                        pass
                    finally:
                        if state["active_jobs"] and state["active_jobs"][-1] is si_job:
                            state["active_jobs"].pop()
            return True, sf

        if (cls in ("Landroidx/compose/runtime/State;", "Landroidx/compose/runtime/MutableState;",
                    "Lkotlinx/coroutines/flow/StateFlow;", "Lkotlinx/coroutines/flow/MutableStateFlow;",
                    "Lkotlinx/coroutines/flow/StateFlowImpl;", "Lkotlinx/coroutines/flow/ReadonlyStateFlow;")
                or (not static and args and isinstance(args[0], JObject)
                    and ("$val" in args[0].fields or "$source_flow" in args[0].fields or "$state_val" in args[0].fields)
                    and name in ("getValue", "setValue", "emit", "tryEmit", "compareAndSet", "collect"))):
            recv_s = args[0] if args and isinstance(args[0], JObject) else None
            if recv_s is not None:
                if name == "getValue":
                    src_f = recv_s.fields.get("$source_flow")
                    if isinstance(src_f, JObject) and "$val" in src_f.fields:
                        return True, src_f.fields["$val"]
                    if "$val" in recv_s.fields:
                        return True, recv_s.fields["$val"]
                    if "$state_val" in recv_s.fields:
                        return True, recv_s.fields["$state_val"]
                    return True, recv_s.fields.get("value")
                if name in ("setValue", "emit", "tryEmit") and len(args) > 1:
                    _emit_flow_val(recv_s, args[1], args[2] if len(args) > 2 else None)
                    return True, (1 if name == "tryEmit" else (JObject("Lkotlin/Unit;") if name == "emit" else None))
                if name == "compareAndSet" and len(args) > 2:
                    _emit_flow_val(recv_s, args[2])
                    return True, 1
                if name == "collect" and len(args) > 1 and isinstance(args[1], JObject):
                    cur_job = state["active_jobs"][-1] if state["active_jobs"] else None
                    if isinstance(cur_job, JObject) and cur_job.fields.get("$cancelled"):
                        return True, _coro_suspended(_vm)
                    colls = recv_s.fields.setdefault("__collectors", [])
                    if args[1] not in colls:
                        colls.append(args[1])
                    if isinstance(cur_job, JObject):
                        cur_job.fields.setdefault("__flow_subs", []).append((recv_s, args[1]))
                    cur_v = recv_s.fields.get("$val")
                    cont_c = args[2] if (len(args) > 2 and isinstance(args[2], JObject)) else JObject("Lkotlin/coroutines/Continuation;")
                    try:
                        _vm.invoke_virtual(
                            args[1], "emit",
                            "(Ljava/lang/Object;Lkotlin/coroutines/Continuation;)Ljava/lang/Object;",
                            (args[1], cur_v, cont_c),
                        )
                    except Exception:
                        pass
                    return True, _coro_suspended(_vm)

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
            ms = int(args[0] or 0) if args and isinstance(args[0], int) else 0
            cur_job = state["active_jobs"][-1] if state["active_jobs"] else None
            dh = 0
            if isinstance(cur_job, JObject):
                dh = int(cur_job.fields.get("$delay_hits", 0)) + 1
                cur_job.fields["$delay_hits"] = dh
            if ms >= 1000 or dh >= 2:
                return True, _coro_suspended(_vm)
            return True, JObject("Lkotlin/Unit;")

        if cls == "Landroidx/compose/runtime/saveable/RememberSaveableKt;" and name == "rememberSaveable":
            pairs = _args_by_type(sig, static, args)
            init_fn = next((_unwrap_fn(v) for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
            if init_fn is None:
                init_fn = next((_unwrap_fn(a) for a in reversed(args) if isinstance(a, JObject) and ("Lambda" in a.cls or "Function" in a.cls)), None)
            comp = state.get("cur_comp") or state.get("root_comp") or state
            occ_map = comp.setdefault("saveable_occ", {})
            base = ("saveable", _caller_site(_vm), init_fn.cls if isinstance(init_fn, JObject) else sig)
            occ = occ_map.get(base, 0)
            occ_map[base] = occ + 1
            slot_k = (base[0], base[1], base[2], occ)
            if slot_k in comp.setdefault("slots", {}):
                return True, comp["slots"][slot_k]
            val = None
            if isinstance(init_fn, JObject):
                try:
                    val = _vm.invoke(init_fn.cls, "invoke", "()Ljava/lang/Object;", (init_fn,))
                except Exception:
                    val = None
            comp["slots"][slot_k] = val
            return True, val

        if cls == "Landroidx/compose/runtime/SnapshotFloatStateKt;" and name.startswith("mutableFloatStateOf"):
            v = _f32_num(args[0], 0.0) if args else 0.0
            return True, JObject("Landroidx/compose/runtime/ParcelableSnapshotMutableFloatState;", {"value": v, "$val": v})

        if cls in ("Landroidx/compose/runtime/ParcelableSnapshotMutableFloatState;", "Landroidx/compose/runtime/MutableFloatState;"):
            if name in ("getFloatValue", "getValue") and args and isinstance(args[0], JObject):
                return True, float(args[0].fields.get("value", args[0].fields.get("$val", 0.0)))
            if name in ("setFloatValue", "setValue") and len(args) > 1 and isinstance(args[0], JObject):
                v = _f32_num(args[1], 0.0)
                args[0].fields["value"] = v
                args[0].fields["$val"] = v
                _mark_compose_mutation()
                return True, None

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
            except _VM_STOP_EXCS:
                raise
            except Exception:
                pass
            if not coll.fields.get("$first_sink") and rk:
                state.setdefault("ds_deps", {})[id(coll)] = {k: _pref_sig(state["prefs"].get(k)) for k in rk}

        if (args and isinstance(args[0], JObject) and args[0].fields.get("$ds_flow")
                and name == "collect" and len(args) > 1):
            coll = args[1]
            cont = args[2] if len(args) > 2 else JObject("Lkotlin/coroutines/Continuation;")
            is_first = isinstance(coll, JObject) and bool(coll.fields.get("$first_sink"))
            cur_job = state["active_jobs"][-1] if state["active_jobs"] else None
            if isinstance(cur_job, JObject) and cur_job.fields.get("$cancelled"):
                return True, JObject("Lkotlin/Unit;")
            if coll is not None and not is_first and coll not in state["ds_collectors"]:
                state["ds_collectors"].append(coll)
            if isinstance(cur_job, JObject) and coll is not None and not is_first:
                cur_job.fields.setdefault("__flow_subs", []).append((args[0], coll))
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
                except _VM_STOP_EXCS:
                    raise
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

        if cls == "Landroidx/appcompat/app/AppCompatDelegate;":
            if name == "setDefaultNightMode" and args:
                nm_mode = int(args[0] or -1)
                stubs._night_mode_setting = nm_mode
                resv_n = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                if nm_mode == 2:
                    tree.dark_mode = True
                    tree.window_bg_dark = True
                    tree.window_bg = "#000000"
                    if resv_n is not None:
                        resv_n.night_mode = True
                elif nm_mode == 1:
                    tree.dark_mode = False
                    tree.window_bg_dark = False
                    tree.window_bg = "#ffffff"
                    if resv_n is not None:
                        resv_n.night_mode = False
                tree.touch()
                return True, None
            if name == "getDefaultNightMode":
                return True, int(getattr(stubs, "_night_mode_setting", -1))

        if cls in (
            "Landroidx/compose/runtime/MonotonicFrameClockKt;",
            "Landroidx/compose/runtime/MonotonicFrameClock;",
            "Landroidx/compose/ui/platform/InfiniteAnimationPolicyKt;",
        ) and name.startswith(("withFrameNanos", "withFrameMillis", "withInfiniteAnimationFrame")):
            return True, _coro_suspended(_vm)

        if cls in (
            "Landroidx/compose/material/pullrefresh/PullRefreshIndicatorKt;",
            "Landroidx/compose/material3/pulltorefresh/PullToRefreshKt;",
            "Landroidx/compose/material3/pulltorefresh/PullToRefreshDefaults;",
        ) and ("Indicator" in name or name.startswith("CircularArrowProgressIndicator")):
            is_ref = next((bool(v) for pt, v in _args_by_type(sig, static, args) if pt == "Z"), False)
            if not is_ref:
                return True, None

        if cls == "Landroidx/compose/animation/core/InfiniteTransition;" and name.startswith("run"):
            return True, None

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
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        pass
                    finally:
                        state["coroutine_depth"] -= 1
            return True, None

        if (not static and args and isinstance(args[0], JObject)
                and name == "run" and sig == "()V" and "$coro_fn2" in args[0].fields):
            blk = args[0].fields.get("$coro_fn2")
            scope = args[0].fields.get("$coro_scope") or JObject("Lkotlinx/coroutines/CoroutineScope;")
            cjob = args[0].fields.get("$coro_job")
            if isinstance(cjob, JObject) and cjob.fields.get("$cancelled"):
                return True, None
            if isinstance(scope, JObject) and scope.fields.get("$cancelled"):
                return True, None
            if isinstance(blk, JObject) and state["coroutine_depth"] < 8:
                state["coroutine_depth"] += 1
                if isinstance(cjob, JObject):
                    state["active_jobs"].append(cjob)
                try:
                    cont = JObject("Lkotlin/coroutines/Continuation;")
                    _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                               (blk, scope, cont))
                except _VM_STOP_EXCS:
                    raise
                except Exception:
                    pass
                finally:
                    if isinstance(cjob, JObject) and state["active_jobs"] and state["active_jobs"][-1] is cjob:
                        state["active_jobs"].pop()
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
            is_deferred = name.startswith("async") or sig.endswith(")Lkotlinx/coroutines/Deferred;")
            is_blocking = name.startswith(("withContext", "runBlocking")) or sig.endswith(")Ljava/lang/Object;")
            job_cls = "Lkotlinx/coroutines/Deferred;" if is_deferred else "Lkotlinx/coroutines/Job;"
            job_obj = None if is_blocking else JObject(job_cls, {
                "$res": None, "__flow_subs": [], "$cancelled": False, "$active": True,
            })
            scope = args[0] if args and isinstance(args[0], JObject) else JObject("Lkotlinx/coroutines/CoroutineScope;")
            if job_obj is not None and isinstance(scope, JObject):
                scope.fields.setdefault("__jobs", []).append(job_obj)
                if scope.fields.get("$cancelled"):
                    job_obj.fields["$cancelled"] = True
                    job_obj.fields["$active"] = False
                    return True, job_obj
            if (isinstance(blk, JObject) and args and isinstance(args[0], JObject)
                    and args[0].fields.get("$is_process_owner")
                    and getattr(stubs, "_current_activity", None) is None
                    and not is_blocking):
                q = getattr(stubs, "pending_runnables", None)
                if isinstance(q, list):
                    q.append(JObject("Ljava/lang/Runnable;", {
                        "_bg_thread": True, "_defer_until_activity": True,
                        "$coro_fn2": blk, "$coro_scope": args[0], "$coro_job": job_obj,
                    }))
                return True, job_obj
            coro_res = None
            if isinstance(blk, JObject) and state["coroutine_depth"] < 24:
                state["coroutine_depth"] += 1
                if job_obj is not None:
                    state["active_jobs"].append(job_obj)
                sub_old_b = _vm.step_budget if (is_deferred and state.get("in_work_manager")) else None
                try:
                    if sub_old_b is not None:
                        _vm.step_budget = min(sub_old_b, _vm.steps + 250_000)
                    cont = JObject("Lkotlin/coroutines/Continuation;")
                    invoke_scope = (
                        scope
                        if sig.startswith("(Lkotlinx/coroutines/CoroutineScope;")
                        else JObject("Lkotlinx/coroutines/CoroutineScope;")
                    )
                    coro_res = _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                                          (blk, invoke_scope, cont))
                except _VM_STOP_EXCS:
                    if sub_old_b is None:
                        raise
                except Exception:
                    pass
                finally:
                    if sub_old_b is not None:
                        _vm.step_budget = sub_old_b
                    if job_obj is not None and state["active_jobs"] and state["active_jobs"][-1] is job_obj:
                        state["active_jobs"].pop()
                    state["coroutine_depth"] -= 1
            if is_blocking:
                return True, coro_res
            job_obj.fields["$res"] = coro_res
            return True, job_obj

        if cls == "Lkotlinx/coroutines/AwaitKt;":
            if (
                name == "joinAll"
                or sig.startswith("([Lkotlinx/coroutines/Job;")
                or (name == "c" and sig.startswith("(Ljava/util/Collection;"))
            ):
                return True, JObject("Lkotlin/Unit;")
            if (
                name == "awaitAll"
                or sig.startswith("([Lkotlinx/coroutines/Deferred;")
                or (name in ("a", "b") and sig.startswith("(Ljava/util/Collection;"))
            ):
                col = args[0] if args else None
                if isinstance(col, JObject):
                    raw_items = col.fields["__items"] if "__items" in col.fields else col.fields.get("$items", [])
                elif isinstance(col, JArray):
                    raw_items = col.data
                else:
                    raw_items = []
                results = [(it.fields.get("$res") if isinstance(it, JObject) and "$res" in it.fields else it)
                           for it in raw_items]
                return True, JObject("Ljava/util/ArrayList;", {"__items": results, "$items": list(results)})

        if (
            (
                name == "await"
                or (cls == "Lkotlinx/coroutines/Deferred;" and "Lkotlin/coroutines/Continuation" in sig and sig.endswith(")Ljava/lang/Object;"))
            )
            and args and isinstance(args[0], JObject) and "$res" in args[0].fields
        ):
            return True, args[0].fields.get("$res")

        _JOB_SCOPE_CLASSES = (
            "Lkotlinx/coroutines/Job;",
            "Lkotlinx/coroutines/JobSupport;",
            "Lkotlinx/coroutines/CompletableJob;",
            "Lkotlinx/coroutines/SupervisorJobImpl;",
            "Lkotlinx/coroutines/Deferred;",
            "Lkotlinx/coroutines/CoroutineScope;",
            "Lkotlinx/coroutines/internal/ContextScope;",
            "Landroidx/lifecycle/LifecycleCoroutineScope;",
            "Landroidx/lifecycle/LifecycleCoroutineScopeImpl;",
        )
        _target_job = args[0] if (args and isinstance(args[0], JObject)) else None
        if _target_job is not None and (
            cls in _JOB_SCOPE_CLASSES
            or cls in ("Lkotlinx/coroutines/JobKt;", "Lkotlinx/coroutines/Job$DefaultImpls;", "Lkotlinx/coroutines/CoroutineScopeKt;")
            or "__flow_subs" in _target_job.fields
            or "__jobs" in _target_job.fields
        ):
            if name in ("cancel", "cancel$default", "cancelChildren", "cancelChildren$default"):
                already_cancelled = bool(_target_job.fields.get("$cancelled"))
                _cancel_coro_job(_target_job)
                if sig.endswith(")Z"):
                    return True, (0 if already_cancelled else 1)
                return True, None
            if name == "isCancelled" and sig.endswith(")Z"):
                return True, (1 if _target_job.fields.get("$cancelled") else 0)
            if name == "isActive" and sig.endswith(")Z"):
                return True, (0 if _target_job.fields.get("$cancelled") else 1)
            if name == "start" and sig == "()Z":
                return True, (0 if _target_job.fields.get("$cancelled") else 1)
            if cls != "Lkotlinx/coroutines/Deferred;" and (
                name == "join" or (sig.startswith("(Lkotlin/coroutines/") and sig.endswith(")Ljava/lang/Object;"))
            ):
                return True, JObject("Lkotlin/Unit;")
            if sig.endswith(")Lkotlinx/coroutines/DisposableHandle;"):
                return True, JObject("Lkotlinx/coroutines/DisposableHandle;", {})

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

        if cls == "Landroidx/compose/foundation/DarkThemeKt;" and name.startswith("isSystemInDarkTheme"):
            return True, (1 if bool(tree.window_bg_dark) else 0)

        if cls.startswith(("Landroidx/compose/ui/graphics/colorspace/", "Landroidx/compose/material3/ColorSchemeKt",
                           "Landroidx/compose/material3/DynamicTonalPaletteKt", "Landroidx/compose/material/ColorsKt")):
            if name == "<clinit>":
                return True, None
            if name == "<init>":
                return True, args[0] if args else None
            if "ColorScheme" in cls or "ColorScheme" in name or "TonalPalette" in cls:
                nl = name.lower()
                is_d = ("dark" in nl) if ("dark" in nl or "light" in nl) else bool(tree.dark_mode)
                pal = _M3_DARK_COLS if is_d else _M3_LIGHT_COLS
                cs_fields = {"dark": is_d}
                for pk, pargb in pal.items():
                    cs_fields[pk] = _pack_compose_argb(pargb)
                if args and isinstance(args[0], int) and args[0] != 0:
                    cs_fields["primary"] = args[0]
                cs_obj = JObject("Landroidx/compose/material3/ColorScheme;", cs_fields)
                if "CompositionLocal;" in sig:
                    return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": cs_obj})
                return True, cs_obj
            return True, JObject("Landroidx/compose/ui/graphics/colorspace/Rgb;")

        if cls == "Landroidx/compose/material3/MaterialTheme;" and name == "getColorScheme":
            is_d = bool(tree.dark_mode)
            pal = _M3_DARK_COLS if is_d else _M3_LIGHT_COLS
            cs_fields = {"dark": is_d, **{pk: _pack_compose_argb(pargb) for pk, pargb in pal.items()}}
            return True, JObject("Landroidx/compose/material3/ColorScheme;", cs_fields)

        if cls in ("Landroidx/compose/ui/graphics/ColorScheme;", "Landroidx/compose/material3/ColorScheme;"):
            if name == "<init>":
                return True, args[0] if args else None
            if name.startswith("get") and sig.endswith(")J") and args and isinstance(args[0], JObject):
                pk = name[3:].split("-", 1)[0].lower()
                if pk in args[0].fields:
                    return True, int(args[0].fields[pk])
                is_d = bool(args[0].fields.get("dark", tree.dark_mode))
                pal = _M3_DARK_COLS if is_d else _M3_LIGHT_COLS
                if pk in pal:
                    return True, _pack_compose_argb(pal[pk])
                return True, _pack_compose_argb(pal["surface"] if "surface" in pk or "background" in pk else pal["onsurface"])
            return True, (0 if sig.endswith((")J", ")I", ")F", ")Z")) else JObject(cls))

        if cls in ("Landroidx/compose/ui/graphics/Color;", "Landroidx/compose/ui/graphics/Color$Companion;",
                   "Landroidx/compose/ui/graphics/ColorKt;"):
            if name == "<clinit>":
                return True, None
            if name == "box-impl":
                return True, JObject("Landroidx/compose/ui/graphics/Color;", {"v": int(args[0] or 0) if args else 0})
            if name == "unbox-impl":
                return True, int(args[0].fields.get("v", 0)) if args and isinstance(args[0], JObject) else 0
            if name == "constructor-impl":
                return True, int(args[0] or 0) if args else 0
            if name == "Color":
                if len(args) == 1:
                    if isinstance(args[0], int):
                        return True, _pack_compose_argb(args[0])
                elif len(args) >= 4 and "(IIII" in sig:
                    r, g, b, al = int(args[0]) & 0xFF, int(args[1]) & 0xFF, int(args[2]) & 0xFF, int(args[3]) & 0xFF
                    return True, _pack_compose_argb((al << 24) | (r << 16) | (g << 8) | b)
                elif len(args) >= 4:
                    r = max(0, min(255, int(round(_f32_num(args[0], 0.0) * 255))))
                    g = max(0, min(255, int(round(_f32_num(args[1], 0.0) * 255))))
                    b = max(0, min(255, int(round(_f32_num(args[2], 0.0) * 255))))
                    al = max(0, min(255, int(round(_f32_num(args[3], 1.0) * 255))))
                    return True, _pack_compose_argb((al << 24) | (r << 16) | (g << 8) | b)
                return True, _pack_compose_argb(0xFF000000)
            if name.startswith("toArgb"):
                argb32 = _argb32_from_compose(args[0] if args else 0)
                return True, (argb32 - 0x100000000 if argb32 >= 0x80000000 else argb32)
            if name.startswith("copy"):
                argb32 = _argb32_from_compose(args[0] if args else 0)
                ca = ((argb32 >> 24) & 0xFF) / 255.0
                cr = ((argb32 >> 16) & 0xFF) / 255.0
                cg = ((argb32 >> 8) & 0xFF) / 255.0
                cb = (argb32 & 0xFF) / 255.0
                mask = int(args[5] or 0) if (name.endswith("$default") and len(args) > 5) else 0
                na = ca if (mask & 1) else (_f32_num(args[1], ca) if len(args) > 1 else ca)
                nr = cr if (mask & 2) else (_f32_num(args[2], cr) if len(args) > 2 else cr)
                ng = cg if (mask & 4) else (_f32_num(args[3], cg) if len(args) > 3 else cg)
                nb = cb if (mask & 8) else (_f32_num(args[4], cb) if len(args) > 4 else cb)
                ia = max(0, min(255, int(round(na * 255))))
                ir = max(0, min(255, int(round(nr * 255))))
                ig = max(0, min(255, int(round(ng * 255))))
                ib = max(0, min(255, int(round(nb * 255))))
                return True, _pack_compose_argb((ia << 24) | (ir << 16) | (ig << 8) | ib)
            if name.startswith("luminance"):
                argb32 = _argb32_from_compose(args[0] if args else 0)
                r, g, b = ((argb32 >> 16) & 0xFF) / 255.0, ((argb32 >> 8) & 0xFF) / 255.0, (argb32 & 0xFF) / 255.0
                return True, float(0.2126 * r + 0.7152 * g + 0.0722 * b)
            _comp_cols = {
                "getWhite": 0xFFFFFFFF, "getBlack": 0xFF000000, "getTransparent": 0x00000000,
                "getRed": 0xFFFF0000, "getGreen": 0xFF00FF00, "getBlue": 0xFF0000FF,
                "getGray": 0xFF888888, "getLightGray": 0xFFCCCCCC, "getDarkGray": 0xFF444444,
                "getYellow": 0xFFFFFF00, "getCyan": 0xFF00FFFF, "getMagenta": 0xFFFF00FF,
            }
            for cp_pre, cp_val in _comp_cols.items():
                if name.startswith(cp_pre):
                    return True, _pack_compose_argb(cp_val)
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
                c_fields = {}
                try:
                    from core.java.compose_grundset import unpack_compose_color as _uc_def
                    d_pairs = _args_by_type(sig, static, args)
                    for pt_d, pv_d in d_pairs:
                        if pt_d == "J":
                            rgb_d, al_d = _uc_def(pv_d)
                            if rgb_d is not None and al_d > 0.01:
                                c_fields["$container_color"] = rgb_d
                                c_fields["$container_alpha"] = al_d
                                break
                except Exception:
                    pass
                return True, JObject(ret, c_fields)
            return True, 0

        if cls in ("Landroidx/compose/ui/graphics/ColorFilter;", "Landroidx/compose/ui/graphics/ColorFilter$Companion;"):
            if name == "<clinit>":
                return True, None
            if name.startswith("tint"):
                cf_fields = {}
                try:
                    from core.java.compose_grundset import unpack_compose_color as _uc_cf
                    cf_pairs = _args_by_type(sig, static, args)
                    col_cf = next((v for pt, v in cf_pairs if pt == "J"), None)
                    rgb_cf, al_cf = _uc_cf(col_cf)
                    if rgb_cf is not None and al_cf > 0.05:
                        cf_fields["$tint"] = rgb_cf
                except Exception:
                    pass
                return True, JObject("Landroidx/compose/ui/graphics/ColorFilter;", cf_fields)
            return True, JObject("Landroidx/compose/ui/graphics/ColorFilter;")

        if cls == "Landroidx/compose/ui/res/PrimitiveResources_androidKt;":
            resv_p = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
            rid_p = int(args[0]) if (args and isinstance(args[0], int)) else 0
            if name.startswith("dimensionResource"):
                if resv_p is not None and hasattr(resv_p, "resolve_dimen_px"):
                    return True, float(resv_p.resolve_dimen_px(rid_p, default=8.0))
                return True, 8.0
            if name.startswith("booleanResource"):
                bm = getattr(stubs, "_boolmap", None)
                if isinstance(bm, dict) and rid_p in bm:
                    return True, (1 if bm[rid_p] else 0)
                return True, 0
            if name.startswith("integerResource"):
                im = getattr(stubs, "_intmap", None)
                if isinstance(im, dict) and rid_p in im:
                    return True, int(im[rid_p])
                return True, 0
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
            if name.startswith("colorResource"):
                rid_c = int(args[0]) if (args and isinstance(args[0], int)) else 0
                resv_c = getattr(tree, "_drawable_resolver", None) or getattr(stubs, "_drawable_resolver", None)
                if resv_c is not None and hasattr(resv_c, "resolve_color_hex_alpha"):
                    hex_c, al_c = resv_c.resolve_color_hex_alpha(rid_c, night=bool(tree.dark_mode))
                    if hex_c:
                        rgb_i = int(hex_c.lstrip("#")[:6], 16)
                        a_i = max(0, min(255, int(round(float(al_c) * 255.0))))
                        return True, _pack_compose_argb((a_i << 24) | rgb_i)
                return True, _pack_compose_argb(0xFFFFFFFF if tree.dark_mode else 0xFF000000)
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
            owner = args[0] if (args and isinstance(args[0], JObject)) else None
            if isinstance(owner, JObject) and "_owner" in owner.fields and isinstance(owner.fields.get("_owner"), JObject):
                owner = owner.fields["_owner"]
            is_proc = bool(isinstance(owner, JObject) and owner.fields.get("$is_process_owner"))
            if isinstance(owner, JObject):
                existing_sc = owner.fields.get("$lifecycle_scope")
                if isinstance(existing_sc, JObject) and not existing_sc.fields.get("$cancelled"):
                    return True, existing_sc
                sc_fields = {"$owner": owner, "__jobs": []}
                if is_proc:
                    sc_fields["$is_process_owner"] = True
                sc_obj = JObject("Landroidx/lifecycle/LifecycleCoroutineScopeImpl;", sc_fields)
                owner.fields["$lifecycle_scope"] = sc_obj
                return True, sc_obj
            return True, JObject("Landroidx/lifecycle/LifecycleCoroutineScopeImpl;", {"$is_process_owner": True} if is_proc else {"__jobs": []})

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
            jcls = (kcls.fields.get("$jclass") or kcls.fields.get("jClass") or kcls.fields.get("a")) if isinstance(kcls, JObject) else None
            raw_tcls = jcls.fields.get("name") if isinstance(jcls, JObject) else None
            tcls = (raw_tcls if (not raw_tcls or (raw_tcls.startswith("L") and raw_tcls.endswith(";")))
                    else f"L{raw_tcls.replace('.', '/')};")
            vm_key = (id(state.get("activity")), tcls) if tcls else None
            if vm_key and vm_key in state["viewmodels"]:
                return True, state["viewmodels"][vm_key]
            if factory is not None and jcls is not None:
                extras = next((v for t, v in pairs if "CreationExtras" in t and isinstance(v, JObject)), None)
                if extras is None:
                    extras = JObject("Landroidx/lifecycle/viewmodel/CreationExtras$Empty;")
                try:
                    inst = _vm.invoke(
                        factory.cls, "create",
                        "(Ljava/lang/Class;Landroidx/lifecycle/viewmodel/CreationExtras;)Landroidx/lifecycle/ViewModel;",
                        (factory, jcls, extras),
                    )
                    if vm_key and isinstance(inst, JObject):
                        state["viewmodels"][vm_key] = inst
                        return True, inst
                except Exception:
                    pass
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
                    cv_list = state.setdefault("cv_recomposers", [])
                    cv_list[:] = [c for c in cv_list if c.get("target_vn") is not tgt_vn]
                    cv_list.append(recomp._comp_state)
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
                except _VM_STOP_EXCS:
                    raise
                except Exception:
                    try:
                        # Generischer (Object, Object)Object / (Object, Object, Object)Object / Function4 Fallback
                        if len(args) == 3:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], _boxed_int(0)))
                        if len(args) == 4:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], args[2], _boxed_int(0)))
                        if len(args) == 5:
                            return True, _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (blk, args[1], args[2], args[3], _boxed_int(0)))
                    except _VM_STOP_EXCS:
                        raise
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
                    vcls = str(val.cls or "")
                    if "$orientation" in val.fields:
                        top.props["orientation"] = val.fields["$orientation"]
                        top.props["compose_kind"] = val.fields.get("$kind", "")
                    elif "CollapsingToolbar" in vcls and "Scaffold" not in vcls:
                        top.props["compose_kind"] = "Box"
                        top.props["$compose_align"] = "top_start"
                    if "$compose_align" in val.fields:
                        top.props["$compose_align"] = val.fields["$compose_align"]
                    _apply_mod_layout_props(top, val)
                    if "$on_click" in val.fields and val.fields["$on_click"] is not None:
                        _cb = val.fields["$on_click"]
                        _rc = (state.get("cur_comp") or {}).get("recomp_fn")
                        top.clickable = True
                        top.on_click = lambda _v=None, _fn=_cb, _rc0=_rc: _compose_click(_fn, _rc0)
                return True, None
            return True, None

        if cls in ("Landroidx/compose/runtime/Composer;", "Landroidx/compose/runtime/ComposerImpl;") or rcls.startswith("Landroidx/compose/runtime/Composer"):
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
                loc = args[1] if (len(args) > 1 and isinstance(args[1], JObject)) else None
                if loc is not None:
                    lk = loc.fields.get("$local_kind")
                    if lk in ("ctx", "activity", "lifecycle", "saved_state"):
                        return True, (
                            state.get("activity")
                            or getattr(stubs, "_current_activity", None)
                            or getattr(stubs, "application", None)
                        )
                    if lk == "view":
                        return True, (_wrap_node(tree.root) if tree.root is not None else JObject("Landroid/view/View;"))
                    if lk == "config":
                        return True, JObject(
                            "Landroid/content/res/Configuration;",
                            {"uiMode": 0x20 if tree.dark_mode else 0x10, "screenWidthDp": 380, "screenHeightDp": 800, "densityDpi": 160},
                        )
                    if lk == "resources":
                        act_r = state.get("activity") or getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)
                        if isinstance(act_r, JObject) and isinstance(act_r.fields.get("mResources"), JObject):
                            return True, act_r.fields["mResources"]
                        return True, JObject("Landroid/content/res/Resources;", {})
                    if lk == "layout_dir":
                        ld = _vm._sget("Landroidx/compose/ui/unit/LayoutDirection;", "Ltr") if _vm is not None else None
                        if isinstance(ld, JObject):
                            return True, ld
                        return True, JObject("Landroidx/compose/ui/unit/LayoutDirection;", {"__ename": "Ltr", "__ordinal": 0})
                    if lk == "density":
                        return True, JObject(
                            "Landroidx/compose/ui/unit/Density;",
                            {"$is_compose_density": True, "density": 1.0, "fontScale": 1.0},
                        )
                    if "$val" in loc.fields:
                        return True, loc.fields["$val"]
                    dfn = loc.fields.get("$default_fn")
                    if isinstance(dfn, JObject):
                        cval = None
                        for csig, cargs in (
                            ("()Ljava/lang/Object;", (dfn,)),
                            ("(Ljava/lang/Object;)Ljava/lang/Object;", (dfn, composer_obj)),
                        ):
                            try:
                                if _vm._resolve_method(dfn.cls, dfn.cls, "invoke", csig) is not None:
                                    cval = _vm.invoke(dfn.cls, "invoke", csig, cargs)
                                    break
                            except _VM_STOP_EXCS:
                                raise
                            except Exception:
                                pass
                        loc.fields["$val"] = cval
                        return True, cval
                    for fk in ("defaultValueHolder", "a"):
                        holder = loc.fields.get(fk)
                        if not isinstance(holder, JObject):
                            continue
                        if "value" in holder.fields:
                            return True, holder.fields["value"]
                        for hfv in list(holder.fields.values()):
                            if isinstance(hfv, JObject) and (
                                "Function0" in hfv.cls
                                or "Lambda" in hfv.cls
                                or _vm._resolve_method(hfv.cls, hfv.cls, "invoke", "()Ljava/lang/Object;") is not None
                            ):
                                try:
                                    cval = _vm.invoke(hfv.cls, "invoke", "()Ljava/lang/Object;", (hfv,))
                                    loc.fields["$val"] = cval
                                    return True, cval
                                except _VM_STOP_EXCS:
                                    raise
                                except Exception:
                                    pass
                return True, JObject("Ljava/lang/Object;")
            return True, None

        if cls == "Landroidx/compose/runtime/ComposerKt;":
            return True, 0 if name == "isTraceInProgress" else None
        if cls in ("Landroidx/compose/runtime/ScopeUpdateScope;", "Landroidx/compose/runtime/RecomposeScopeImplKt;", "Landroidx/compose/runtime/ComposablesKt;"):
            if name == "updateChangedFlags":
                return True, args[0] if args else 0
            return True, None
        if cls == "Landroidx/activity/compose/LocalActivityKt;" and name == "getLocalActivity":
            return True, local_act_key
        if cls == "Landroidx/lifecycle/compose/LocalLifecycleOwnerKt;" and name == "getLocalLifecycleOwner":
            return True, local_lc_key
        if cls == "Landroidx/compose/ui/platform/AndroidCompositionLocals_androidKt;":
            if name == "getLocalContext":
                return True, local_ctx_key
            if name == "getLocalLifecycleOwner":
                return True, local_lc_key
            if name == "getLocalSavedStateRegistryOwner":
                return True, local_ss_key
            if name == "getLocalView":
                return True, local_view_key
            if name == "getLocalConfiguration":
                return True, local_cfg_key
            if name == "getLocalResources":
                return True, local_res_key
        if cls == "Landroidx/compose/ui/platform/CompositionLocalsKt;":
            if name == "getLocalLayoutDirection":
                return True, local_ldir_key
            if name == "getLocalDensity":
                return True, local_dens_key
            if name.startswith("getLocal"):
                return True, JObject(
                    "Landroidx/compose/runtime/ProvidableCompositionLocal;",
                    {"$val": JObject("Landroidx/compose/ui/platform/StubLocalService;", {"$stub_local_svc": True})},
                )
        if not static and args and isinstance(args[0], JObject) and args[0].fields.get("$stub_local_svc"):
            if sig.endswith(")Z"):
                return True, 1
            if sig.endswith(")I"):
                return True, 0
            if sig.endswith(")V"):
                return True, None
            return True, JObject("Ljava/lang/Object;")
        if cls == "Landroidx/compose/ui/unit/Density;" or (
            not static and args and isinstance(args[0], JObject) and args[0].fields.get("$is_compose_density")
        ):
            if name in ("getDensity", "getFontScale"):
                return True, 1.0
            if name.startswith("roundToPx"):
                return True, (int(round(_f32_num(args[1], 0.0))) if len(args) > 1 else 0)
            if name.startswith(("toPx", "toDp")):
                return True, (_f32_num(args[1], 0.0) if len(args) > 1 else 0.0)
            if name.startswith("toSp"):
                v_sp = _f32_num(args[1], 12.0) if len(args) > 1 else 12.0
                return True, (0x100000000 | _struct.unpack("<I", _struct.pack("<f", v_sp))[0])
            if name.startswith("toSize"):
                sz_f = _f32_num(args[1], 0.0) if len(args) > 1 else 0.0
                return True, _pack_2f(sz_f, sz_f)
        if cls == "Landroidx/compose/runtime/CompositionLocalKt;":
            if name in (
                "compositionLocalOf",
                "compositionLocalOf$default",
                "staticCompositionLocalOf",
                "compositionLocalWithComputedDefaultOf",
            ):
                fn = next(
                    (
                        _unwrap_fn(v)
                        for pt, v in _args_by_type(sig, static, args)
                        if ("Function0" in pt or "Function1" in pt) and isinstance(v, JObject)
                    ),
                    None,
                )
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$default_fn": fn})
            if name == "CompositionLocalProvider":
                fn2 = next((v for pt, v in _args_by_type(sig, static, args) if "Function2" in pt and isinstance(v, JObject)), None)
                saved_vals = []
                for ar in args:
                    pvs = ar.data if isinstance(ar, JArray) else ([ar] if isinstance(ar, JObject) and ar.cls == "Landroidx/compose/runtime/ProvidedValue;" else ())
                    for pv in pvs:
                        if isinstance(pv, JObject):
                            lobj = pv.fields.get("$local")
                            if isinstance(lobj, JObject):
                                had_v = "$val" in lobj.fields
                                old_v = lobj.fields.get("$val")
                                saved_vals.append((lobj, had_v, old_v))
                                lobj.fields["$val"] = pv.fields.get("$val")
                try:
                    if fn2 is not None:
                        _invoke_fn2(fn2)
                finally:
                    for lobj, had_v, old_v in reversed(saved_vals):
                        if had_v:
                            lobj.fields["$val"] = old_v
                        else:
                            lobj.fields.pop("$val", None)
                return True, None
        if cls in ("Landroidx/compose/runtime/ProvidableCompositionLocal;", "Landroidx/compose/runtime/CompositionLocal;") and name in ("provides", "providesDefault"):
            return True, JObject(
                "Landroidx/compose/runtime/ProvidedValue;",
                {"$local": args[0] if args else None, "$val": args[1] if len(args) > 1 else None},
            )

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
                _mark_compose_mutation()
                return True, None

        # 4b) Inlined Compose MeasurePolicies, Modifier.clickable & Modifier-Chain Passthrough
        if cls == "Landroidx/compose/ui/Alignment$Companion;" and name.startswith("get"):
            _al_map = {
                "getTopStart": "top_start",
                "getTopCenter": "top_center",
                "getTopEnd": "top_end",
                "getCenterStart": "center_start",
                "getCenter": "center",
                "getCenterEnd": "center_end",
                "getBottomStart": "bottom_start",
                "getBottomCenter": "bottom_center",
                "getBottomEnd": "bottom_end",
            }
            if name in _al_map:
                return True, JObject("Landroidx/compose/ui/Alignment;", {"$align": _al_map[name]})
        if cls == "Landroidx/compose/foundation/layout/RowKt;" and name == "rowMeasurePolicy":
            return True, JObject("Landroidx/compose/ui/layout/MeasurePolicy;", {"$orientation": "horizontal", "$kind": "Row"})
        if cls == "Landroidx/compose/foundation/layout/ColumnKt;" and name == "columnMeasurePolicy":
            return True, JObject("Landroidx/compose/ui/layout/MeasurePolicy;", {"$orientation": "vertical", "$kind": "Column"})
        if cls == "Landroidx/compose/foundation/layout/BoxKt;" and name in ("maybeCachedBoxMeasurePolicy", "rememberBoxMeasurePolicy"):
            is_center = False
            if args and isinstance(args[0], JObject):
                if args[0].fields.get("$align") in ("center", "center_start", "center_end"):
                    is_center = True
                else:
                    vals = [
                        v for k, v in args[0].fields.items()
                        if not k.startswith("$") and isinstance(v, float)
                    ]
                    if vals and all(abs(v) < 1e-4 for v in vals):
                        is_center = True
            return True, JObject(
                "Landroidx/compose/ui/layout/MeasurePolicy;",
                {
                    "$orientation": "vertical",
                    "$kind": "Box",
                    "$compose_align": "center" if is_center else "top_start",
                },
            )

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
        except _VM_STOP_EXCS:
            raise
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
                elif m_base == "fillMaxSize":
                    fields["$fill_max_h"] = True
                    fields["$fill_max_w"] = True
                elif m_base == "fillMaxHeight":
                    fields["$fill_max_h"] = True
                elif m_base == "fillMaxWidth":
                    fields["$fill_max_w"] = True
                elif m_base == "aspectRatio" and f_vals and f_vals[0] > 0.05:
                    fields["$aspect_ratio"] = round(float(f_vals[0]), 3)
                elif m_base == "align":
                    al_o = next((v for _pt, v in m_pairs if isinstance(v, JObject) and "$align" in v.fields), None)
                    if al_o is not None:
                        fields["$box_align"] = al_o.fields["$align"]
                elif m_base in ("height", "requiredHeight") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$h"] = max(1, int(round(f_vals[0])))
                elif m_base in ("width", "requiredWidth") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$w"] = max(1, int(round(f_vals[0])))
                elif m_base in ("size", "requiredSize") and f_vals and 0 < f_vals[0] < 2000:
                    fields["$w"] = max(1, int(round(f_vals[0])))
                    fields["$h"] = max(1, int(round(f_vals[1] if len(f_vals) > 1 else f_vals[0])))
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

        # 4d) Compose Geometry / Units / CanvasKt / DrawScope / AndroidCanvas
        if cls in (
            "Landroidx/compose/ui/geometry/Offset;", "Landroidx/compose/ui/geometry/OffsetKt;",
            "Landroidx/compose/ui/geometry/Size;", "Landroidx/compose/ui/geometry/SizeKt;",
            "Landroidx/compose/ui/geometry/CornerRadius;", "Landroidx/compose/ui/geometry/CornerRadiusKt;",
        ):
            if name == "<clinit>":
                return True, None
            if name == "constructor-impl" and args:
                return True, int(args[0] or 0)
            if name in ("Offset", "Size", "CornerRadius", "CornerRadius$default") and len(args) >= 2:
                return True, _pack_2f(_f32_num(args[0], 0.0), _f32_num(args[1], 0.0))
            if name.startswith(("getX-impl", "getWidth-impl")) and args:
                return True, _unpack_2f(args[0])[0]
            if name.startswith(("getY-impl", "getHeight-impl")) and args:
                return True, _unpack_2f(args[0])[1]
            if name.startswith("getMinDimension-impl") and args:
                w0, h0 = _unpack_2f(args[0])
                return True, min(w0, h0)
            if name.startswith("getMaxDimension-impl") and args:
                w0, h0 = _unpack_2f(args[0])
                return True, max(w0, h0)
            if name.startswith("isEmpty-impl") and args:
                w0, h0 = _unpack_2f(args[0])
                return True, (1 if (w0 <= 0.0 or h0 <= 0.0) else 0)
            if name.startswith(("getZero", "getUnspecified")):
                return True, 0
            if name.startswith("box-impl") and args:
                return True, JObject(cls, {"v": int(args[0] or 0)})
            if name.startswith("unbox-impl") and args and isinstance(args[0], JObject):
                return True, int(args[0].fields.get("v", 0))
            return True, 0

        if cls in ("Landroidx/compose/ui/unit/TextUnitKt;", "Landroidx/compose/ui/unit/TextUnit;"):
            if name == "<clinit>":
                return True, None
            if name == "getSp" and args:
                v_sp = _f32_num(args[0], 12.0) if sig.startswith("(F") else float(args[0] or 12.0)
                sp_bits = _struct.unpack("<I", _struct.pack("<f", v_sp))[0]
                return True, (0x100000000 | sp_bits)
            if name.startswith("getValue-impl") and args:
                bits = int(args[0] or 0) & 0xFFFFFFFF
                try:
                    return True, float(_struct.unpack("<f", _struct.pack("<I", bits))[0])
                except Exception:
                    return True, 12.0
            if name.startswith("constructor-impl") and args:
                return True, int(args[0] or 0)
            return True, 0

        if cls in ("Landroidx/compose/ui/unit/Dp;", "Landroidx/compose/ui/unit/DpKt;"):
            if name == "<clinit>":
                return True, None
            if name in ("constructor-impl", "getDp") and args:
                return True, _f32_num(args[0], 0.0)
            if sig.endswith(")F") and args:
                return True, _f32_num(args[0], 0.0)
            return True, 0.0

        if cls in ("Landroidx/compose/ui/graphics/ClipOp$Companion;",
                   "Landroidx/compose/ui/graphics/StrokeCap$Companion;",
                   "Landroidx/compose/ui/graphics/StrokeJoin$Companion;",
                   "Landroidx/compose/ui/graphics/BlendMode$Companion;"):
            return True, (None if name in ("<clinit>", "<init>") else 0)

        if cls == "Landroidx/compose/foundation/CanvasKt;" and name == "Canvas":
            pairs = _args_by_type(sig, static, args)
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            on_draw = next((_unwrap_fn(v) for pt, v in pairs if "Function1" in pt and isinstance(v, JObject)), None)
            cn = ViewNode(cls="ComposeCanvas", props={})
            _apply_mod_layout_props(cn, mod)
            cw_px = float(cn.props.get("w") or max(280, tree.width - 32))
            ch_px = float(cn.props.get("h") or 220)
            cn.props.setdefault("h", int(round(ch_px)))
            from core.java.canvas_recorder import CanvasRecorder
            crec = CanvasRecorder(int(round(cw_px)), int(round(ch_px)))
            cn.props["_compose_canvas_rec"] = crec
            cn.props["_compose_canvas_size"] = (cw_px, ch_px)
            native_cv = JObject("Landroid/graphics/Canvas;", {"$rec": crec, "_w": int(round(cw_px)), "_h": int(round(ch_px))})
            ui_cv = JObject("Landroidx/compose/ui/graphics/Canvas;", {"$native": native_cv, "$rec": crec})
            xform = JObject("Landroidx/compose/ui/graphics/drawscope/DrawTransform;", {"$rec": crec})
            size_j = _pack_2f(cw_px, ch_px)
            dctx = JObject("Landroidx/compose/ui/graphics/drawscope/DrawContext;", {
                "$canvas": ui_cv, "$transform": xform, "$size": size_j, "$rec": crec,
            })
            dscope = JObject("Landroidx/compose/ui/graphics/drawscope/DrawScope;", {
                "$ctx": dctx, "$rec": crec, "$size": size_j, "$density": 1.0,
            })
            _cur_parent().add(cn)
            if isinstance(on_draw, JObject):
                old_b = getattr(_vm, "step_budget", None)
                cur_s = getattr(_vm, "steps", 0)
                _vm.step_budget = (cur_s + 250_000) if old_b is None else max(old_b, cur_s + 250_000)
                try:
                    _vm.invoke(on_draw.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (on_draw, dscope))
                except Exception:
                    pass
                finally:
                    _vm.step_budget = old_b
            tree.touch()
            return True, None

        if cls == "Landroidx/compose/ui/graphics/AndroidCanvas_androidKt;" and name in ("getNativeCanvas", "ActualCanvas"):
            cv_o = args[0] if args and isinstance(args[0], JObject) else None
            if cv_o is not None:
                return True, (cv_o.fields.get("$native") or cv_o)
            return True, JObject("Landroid/graphics/Canvas;")

        if cls == "Landroidx/compose/ui/graphics/Canvas;" or (
            not static and args and isinstance(args[0], JObject) and "$native" in args[0].fields and "$rec" in args[0].fields
        ):
            recv_c = args[0] if args and isinstance(args[0], JObject) else None
            crec = recv_c.fields.get("$rec") if recv_c is not None else None
            if crec is not None:
                if name == "save":
                    crec.save()
                elif name == "restore":
                    crec.restore()
                elif name == "translate" and len(args) > 2:
                    crec.translate(_f32_num(args[1], 0.0), _f32_num(args[2], 0.0))
                elif name == "scale" and len(args) > 2:
                    crec.scale(_f32_num(args[1], 1.0), _f32_num(args[2], 1.0))
            return True, None

        if cls == "Landroidx/compose/ui/graphics/drawscope/DrawContext;" or (
            not static and args and isinstance(args[0], JObject) and "$canvas" in args[0].fields
        ):
            recv_dc = args[0] if args and isinstance(args[0], JObject) else None
            if recv_dc is not None:
                if name == "getCanvas":
                    return True, recv_dc.fields.get("$canvas")
                if name == "getTransform":
                    return True, recv_dc.fields.get("$transform")
                if name.startswith("getSize"):
                    return True, int(recv_dc.fields.get("$size", 0))
                if name.startswith("setSize") and len(args) > 1:
                    recv_dc.fields["$size"] = int(args[1] or 0)
                    return True, None
            return True, None

        if cls == "Landroidx/compose/ui/graphics/drawscope/DrawTransform;":
            recv_dt = args[0] if args and isinstance(args[0], JObject) else None
            crec = recv_dt.fields.get("$rec") if recv_dt is not None else None
            if crec is not None:
                if name.startswith("translate") and len(args) > 2:
                    crec.translate(_f32_num(args[1], 0.0), _f32_num(args[2], 0.0))
                elif name.startswith("inset") and len(args) > 2:
                    crec.translate(_f32_num(args[1], 0.0), _f32_num(args[2], 0.0))
                elif name.startswith("scale") and len(args) > 2:
                    crec.scale(_f32_num(args[1], 1.0), _f32_num(args[2], 1.0))
            return True, None

        if cls == "Landroidx/compose/ui/graphics/drawscope/DrawScope;" or (
            args and isinstance(args[0], JObject) and "$ctx" in args[0].fields and "$rec" in args[0].fields
        ):
            dscope = args[0] if args and isinstance(args[0], JObject) else None
            crec = dscope.fields.get("$rec") if dscope is not None else None
            sz_j = int(dscope.fields.get("$size", 0)) if dscope is not None else 0
            sw_s, sh_s = _unpack_2f(sz_j)
            if name.startswith("getSize"):
                return True, sz_j
            if name.startswith("getCenter"):
                return True, _pack_2f(sw_s * 0.5, sh_s * 0.5)
            if name == "getDrawContext" and dscope is not None:
                return True, dscope.fields.get("$ctx")
            if name in ("getDensity", "getFontScale"):
                return True, float(dscope.fields.get("$density", 1.0)) if dscope is not None else 1.0
            if name == "toPx-0680j_4":
                return True, _f32_num(args[1] if len(args) > 1 else 0.0, 0.0)
            if name == "toPx--R2X_6o":
                if len(args) > 1:
                    bits = int(args[1] or 0) & 0xFFFFFFFF
                    try:
                        f = float(_struct.unpack("<f", _struct.pack("<I", bits))[0])
                        if not math.isnan(f) and 1.0 <= f <= 200.0:
                            return True, f
                    except Exception:
                        pass
                return True, 12.0
            if name.startswith("drawLine") and len(args) > 3:
                is_def = name.endswith("$default")
                mask = int(args[10] or 0) if (is_def and len(args) > 10) else 0
                hcol, cal = _compose_color_hex_alpha(args[1])
                x1, y1 = (0.0, 0.0) if (mask & 2) else _unpack_2f(args[2])
                x2, y2 = (0.0, 0.0) if (mask & 4) else _unpack_2f(args[3])
                sw = 1.0 if (mask & 8) else _f32_num(args[4] if len(args) > 4 else 1.5, 1.5)
                ef_a = cal * (1.0 if (not is_def or (mask & 64) or len(args) <= 7) else _f32_num(args[7], 1.0))
                if crec is not None and ef_a > 0.01:
                    crec.line(x1, y1, x2, y2, _blend_canvas_hex(hcol, ef_a) if ef_a < 0.7 else hcol, width=max(1.0, sw))
                return True, None
            if name.startswith("drawRect") and len(args) > 1:
                is_def = name.endswith("$default")
                mask = int(args[8] or 0) if (is_def and len(args) > 8) else 0
                hcol, cal = _compose_color_hex_alpha(args[1])
                rx, ry = (0.0, 0.0) if (mask & 2 or len(args) <= 2) else _unpack_2f(args[2])
                rw, rh = (sw_s, sh_s) if (mask & 4 or len(args) <= 3) else _unpack_2f(args[3])
                ef_a = cal * (1.0 if (not is_def or (mask & 8) or len(args) <= 4) else _f32_num(args[4], 1.0))
                if crec is not None and ef_a > 0.01 and rw > 0 and rh > 0:
                    crec.rect(rx, ry, rx + rw, ry + rh, _blend_canvas_hex(hcol, ef_a))
                return True, None
            if name.startswith("drawRoundRect") and len(args) > 1:
                is_def = name.endswith("$default")
                mask = int(args[9] or 0) if (is_def and len(args) > 9) else 0
                hcol, cal = _compose_color_hex_alpha(args[1])
                rx, ry = (0.0, 0.0) if (mask & 2 or len(args) <= 2) else _unpack_2f(args[2])
                rw, rh = (sw_s, sh_s) if (mask & 4 or len(args) <= 3) else _unpack_2f(args[3])
                crx, cry = (0.0, 0.0) if (mask & 8 or len(args) <= 4) else _unpack_2f(args[4])
                ef_a = cal * (1.0 if (not is_def or (mask & 32) or len(args) <= 6) else _f32_num(args[6], 1.0))
                if crec is not None and ef_a > 0.01 and rw > 0 and rh > 0:
                    crec.drawRoundRect(rx, ry, rx + rw, ry + rh, crx, cry, _blend_canvas_hex(hcol, ef_a))
                return True, None
            if name.startswith("drawCircle") and len(args) > 1:
                is_def = name.endswith("$default")
                mask = int(args[8] or 0) if (is_def and len(args) > 8) else 0
                hcol, cal = _compose_color_hex_alpha(args[1])
                rad = min(sw_s, sh_s) * 0.5 if (mask & 2 or len(args) <= 2) else _f32_num(args[2], 4.0)
                cx, cy = (sw_s * 0.5, sh_s * 0.5) if (mask & 4 or len(args) <= 3) else _unpack_2f(args[3])
                ef_a = cal * (1.0 if (not is_def or (mask & 8) or len(args) <= 4) else _f32_num(args[4], 1.0))
                if crec is not None and ef_a > 0.01 and rad > 0:
                    crec.circle(cx, cy, rad, _blend_canvas_hex(hcol, ef_a) if ef_a < 0.7 else hcol)
                return True, None
            if name.startswith("draw"):
                return True, None

        # 5) Material / Material3 / Foundation Compose UI Widgets
        if cls.startswith(("Landroidx/compose/material", "Landroidx/compose/foundation/")):
            pairs = _args_by_type(sig, static, args)

            if name == "MaterialTheme":
                cs = next((v for pt, v in pairs if "ColorScheme" in pt and isinstance(v, JObject)), None)
                if isinstance(cs, JObject) and "dark" in cs.fields:
                    is_d = bool(cs.fields["dark"])
                    tree.dark_mode = is_d
                    tree.window_bg = "#121418" if is_d else "#ffffff"
                    tree.window_bg_dark = is_d
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
                cf_obj = next((v for pt, v in pairs if "ColorFilter" in pt and isinstance(v, JObject)), None)
                tint_hex = cf_obj.fields.get("$tint") if isinstance(cf_obj, JObject) else None
                if not tint_hex and name.startswith("Icon") and _g2_unpack_col is not None:
                    col_ic = next((v for pt, v in pairs if pt == "J"), None)
                    if col_ic is not None:
                        _trgb, _tal = _g2_unpack_col(col_ic)
                        if _trgb and _tal > 0.05:
                            tint_hex = _trgb
                spec = None
                img_name = ""
                if isinstance(painter, JObject):
                    if painter.fields.get("$img_bytes"):
                        img_b = painter.fields["$img_bytes"]
                        img_url = str(painter.fields.get("$coil_url") or desc or "coil_image")
                        if state["collect_depth"] > 0:
                            if desc:
                                _cur_parent().add(ViewNode(cls="TextView", text="", props={"desc": desc, "img": img_url}))
                            return True, None
                        alpha = _extract_compose_alpha(name, pairs)
                        props = {
                            "img_bytes": img_b,
                            "img": img_url,
                            "desc": desc,
                            "h": 28 if name.startswith("Icon") else 84,
                            "img_alpha": alpha,
                        }
                        if name.startswith("Icon"):
                            props["w"] = 28
                        iv_n = ViewNode(cls="ImageView", props=props)
                        _apply_mod_layout_props(iv_n, mod)
                        if iv_n.props.get("$fill_max_w") and "w" not in iv_n.props:
                            iv_n.props.setdefault("scale_type", "CENTER_CROP")
                        _cur_parent().add(iv_n)
                        tree.touch()
                        return True, None
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
                        if tint_hex:
                            props["tint"] = tint_hex
                        iv_n = ViewNode(cls="ImageView", props=props)
                        _apply_mod_layout_props(iv_n, mod)
                        par.add(iv_n)
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
                cont_hex = None
                cont_al = 1.0
                for pt_c, val_c in pairs:
                    if isinstance(val_c, JObject) and val_c.fields.get("$container_color"):
                        cont_hex = val_c.fields["$container_color"]
                        cont_al = float(val_c.fields.get("$container_alpha", 1.0))
                        break
                if cont_hex is None and _g2_unpack_col is not None:
                    for pt_c, val_c in pairs:
                        if pt_c == "J":
                            _ch, _ca = _g2_unpack_col(val_c)
                            if _ch is not None and _ca > 0.05:
                                cont_hex, cont_al = _ch, _ca
                            break
                if cont_hex and cont_al > 0.05:
                    if role == "scaffold" and cont_al > 0.9:
                        tree.window_bg = cont_hex
                        try:
                            from core.java.drawable_loader import DrawableResolver
                            tree.window_bg_dark = DrawableResolver._is_dark_hex(cont_hex)
                            tree.dark_mode = tree.window_bg_dark
                        except Exception:
                            pass
                    elif not box.props.get("bg_drawable"):
                        box.props["bg_drawable"] = {
                            "kind": "shape", "shape": "rectangle",
                            "fill": cont_hex,
                            "radius": 12.0 if role == "card" else 0.0,
                        }
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
                    elif role == "topbar":
                        f2s = [val for pt, val in pairs if "Function2" in pt and isinstance(val, JObject)]
                        f3s = [val for pt, val in pairs if "Function3" in pt and isinstance(val, JObject)]
                        if len(f2s) >= 2:
                            _invoke_fn2(f2s[1])
                        if f2s:
                            before_t = len(box.children)
                            _invoke_fn2(f2s[0])
                            for tc in box.children[before_t:]:
                                tc.props.setdefault("layout_weight", 1.0)
                        for val in f2s[2:]:
                            _invoke_fn2(val)
                        for val in f3s:
                            _invoke_fn3(val, row_scope)
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
                    except _VM_STOP_EXCS:
                        raise
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
