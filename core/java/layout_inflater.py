# -*- coding: utf-8 -*-
"""core.java.layout_inflater — Android-XML-Layout -> ViewTree (U-Track Tiefe).

Viele klassische Apps bauen ihre Oberflaeche aus res/layout/*.xml (setContentView(R.layout.x) /
LayoutInflater.inflate). Dieses Modul inflatet solche (apktool-dekodierten) XML-Layouts in
`ViewNode`-Baeume, damit echte App-Screens gerendert werden. Rein + headless testbar; die
Resource-Aufloesung nutzt res/values/public.xml.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from core.java.view_tree import ViewNode

_NS = "{http://schemas.android.com/apk/res/android}"
_NS_APP = "{http://schemas.android.com/apk/res-auto}"
_CLICKABLE = ("Button", "ImageButton")
_FOCUSABLE = ("EditText", "AutoCompleteTextView", "MultiAutoCompleteTextView")


def _attr(el, name):
    return el.get(_NS + name)


def _attr_any(el, name):
    v = el.get(_NS + name)
    if v is not None:
        return v
    v = el.get(_NS_APP + name)
    if v is not None:
        return v
    return el.get(name)


_NON_VISUAL_HELPER = ("Guideline", "Space", "Barrier", "Group")


def _res_dir(app_root):
    if not app_root:
        return ""
    r1 = os.path.join(app_root, "res")
    if os.path.isdir(r1):
        return r1
    r2 = os.path.join(app_root, "resources", "package_1", "res")
    if os.path.isdir(r2):
        return r2
    return r1


def _short_tag(tag):
    if not isinstance(tag, str):
        return "View"
    short = tag.rsplit(".", 1)[-1]
    if short in ("fragment", "FragmentContainerView"):
        return "FrameLayout"
    if short in _NON_VISUAL_HELPER:
        return short
    if short.endswith(("EditText", "AutoCompleteTextView", "AutoCompleteEdit")):
        return "EditText"
    if short.endswith("TextView"):
        return "TextView"
    if short.endswith("ImageButton"):
        return "ImageButton"
    if short.endswith("FilterButton"):
        return short
    if short.endswith("Button"):
        return "Button"
    if short.endswith("ImageView"):
        return "ImageView"
    if short.endswith("CheckBox"):
        return "CheckBox"
    if short in ("SwitchCompat", "MaterialSwitch") or short.endswith("Switch"):
        return "Switch"
    if short.endswith("RecyclerView"):
        return "RecyclerView"
    if short.endswith("Slider"):
        return "SeekBar"
    if short.endswith("DatePicker"):
        return "DatePicker"
    return short


def _node_from_el(el):
    cls = _short_tag(el.tag)
    n = ViewNode(cls=cls)
    raw_tag = str(el.tag or "")
    if cls in _NON_VISUAL_HELPER:
        n.props["non_visual"] = True
        n.visible = False
    elif cls in ("BottomNavigationView", "NavigationBarView"):
        cls = "BottomNavigationView"
        n = ViewNode(cls=cls)
        n.props["role"] = "navbar"
        n.props["orientation"] = "horizontal"
        n.props.setdefault("h", 56)
        _menu_ref = _attr_any(el, "menu")
        if _menu_ref:
            n.props["menu_ref"] = _menu_ref
    elif cls in ("Toolbar", "MaterialToolbar"):
        n.props["orientation"] = "horizontal"
    if raw_tag == "fragment" or raw_tag.endswith(".fragment") or raw_tag.endswith("FragmentContainerView"):
        fname = _attr_any(el, "name") or el.get("class")
        if fname:
            n.props["fragment_name"] = fname
    if raw_tag == "include":
        inc = el.get("layout")
        if inc:
            n.props["include_layout"] = inc
    tid = _attr(el, "id")
    if tid:
        n.vid = tid.split("/")[-1]
    txt = _attr(el, "text")
    if txt:
        n.text = txt
    hint = _attr_any(el, "hint") or _attr_any(el, "title_text")
    if hint:
        n.props["hint"] = hint
    inp_type = (_attr_any(el, "inputType") or _attr_any(el, "password") or "").lower()
    if "password" in inp_type or inp_type == "true":
        n.props["is_password"] = True
    vis = (_attr(el, "visibility") or "").lower()
    if vis in ("gone", "invisible"):
        n.visible = False
    desc = _attr(el, "contentDescription")
    if desc:
        n.props["desc"] = desc
    src_ref = _attr_any(el, "src") or _attr_any(el, "srcCompat")
    if src_ref:
        if src_ref.strip().lower() in ("@null", "@empty"):
            n.props["src_null"] = True
        else:
            n.props["src_ref"] = src_ref
            if src_ref.startswith(("@drawable/", "@mipmap/")):
                n.props["img"] = src_ref.split("/", 1)[1]
    bg_ref = _attr_any(el, "background") or _attr_any(el, "input_backgroundColor")
    if bg_ref and bg_ref.strip().lower() not in ("@null", "@empty"):
        n.props["bg_ref"] = bg_ref
    if cls in ("CardView", "MaterialCardView"):
        card_bg = _attr_any(el, "cardBackgroundColor")
        if card_bg and card_bg.strip().lower() not in ("@null", "@empty"):
            n.props["card_bg_ref"] = card_bg
        card_rad = (_attr_any(el, "cardCornerRadius") or "").strip()
        if card_rad:
            n.props["card_radius_ref"] = card_rad
    tint_ref = _attr_any(el, "tint")
    if tint_ref and tint_ref.strip().lower() not in ("@null", "@empty"):
        n.props["tint_ref"] = tint_ref
    bg_tint_ref = _attr_any(el, "backgroundTint")
    if bg_tint_ref and bg_tint_ref.strip().lower() not in ("@null", "@empty"):
        n.props["bg_tint_ref"] = bg_tint_ref
    alpha_raw = (_attr_any(el, "alpha") or "").strip()
    if alpha_raw:
        try:
            _av = float(alpha_raw)
            n.props["alpha"] = _av
            n.props["bg_alpha"] = _av
        except Exception:
            pass
    for _d_attr, _p_key, _m_key, _r_key in (
        ("layout_width", "xml_w", "xml_w_mode", "xml_w_ref"),
        ("layout_height", "xml_h", "xml_h_mode", "xml_h_ref"),
    ):
        _dv = (_attr_any(el, _d_attr) or "").strip()
        if _dv:
            _dl = _dv.lower()
            if _dl in ("match_parent", "fill_parent", "-1"):
                n.props[_m_key] = "match"
            elif _dl in ("wrap_content", "-2"):
                n.props[_m_key] = "wrap"
            elif _dl in ("0dp", "0.0dp", "0dip", "0.0dip", "0px", "0"):
                n.props[_m_key] = "zero"
            elif _dv.startswith(("@dimen/", "@android:dimen/", "?")):
                n.props[_r_key] = _dv
            for _suf in ("dip", "dp", "px"):
                if _dv.endswith(_suf):
                    try:
                        _fv = float(_dv[: -len(_suf)])
                        if _fv > 0:
                            n.props[_p_key] = int(round(_fv))
                    except Exception:
                        pass
                    break
    col_ref = _attr_any(el, "textColor")
    if col_ref:
        n.props["color_ref"] = col_ref
    for cd_attr in ("drawableLeft", "drawableLeftCompat", "drawableStart", "drawableStartCompat", "drawableTop", "drawableRight", "drawableEnd"):
        cd_val = _attr_any(el, cd_attr)
        if cd_val:
            n.props["compound_drawable_ref"] = cd_val
            break
    oc = _attr(el, "onClick")
    clickable = (_attr(el, "clickable") or "").lower() == "true"
    if oc or clickable or cls in _CLICKABLE:
        n.clickable = True
        if oc:
            n.props["onClick"] = oc
    if cls in _FOCUSABLE:
        n.focusable = True
    lw = (_attr(el, "layout_weight") or "").strip()
    if lw:
        try:
            n.props["layout_weight"] = float(lw)
        except Exception:
            pass
    lb = _attr(el, "layout_below")
    if lb:
        n.props["layout_below"] = lb.split("/")[-1]
    la = _attr(el, "layout_above")
    if la:
        n.props["layout_above"] = la.split("/")[-1]
    if (_attr(el, "layout_alignParentBottom") or "").lower() == "true":
        n.props["align_parent_bottom"] = True
    if (_attr(el, "layout_alignParentTop") or "").lower() == "true":
        n.props["align_parent_top"] = True
    l_to_left = _attr(el, "layout_toLeftOf") or _attr(el, "layout_toStartOf")
    if l_to_left:
        n.props["layout_to_left"] = l_to_left.split("/")[-1]
    if ((_attr(el, "layout_alignParentRight") or "").lower() == "true"
            or (_attr(el, "layout_alignParentEnd") or "").lower() == "true"):
        n.props["align_parent_end"] = True
    def _parse_dp_val(raw_s):
        rs = (raw_s or "").strip()
        for _suf in ("dip", "dp", "px"):
            if rs.endswith(_suf):
                try:
                    return int(round(float(rs[: -len(_suf)])))
                except Exception:
                    return None
        return None

    _m_all = (_attr(el, "layout_margin") or "").strip()
    if _m_all:
        if _m_all.startswith(("@dimen/", "@android:dimen/", "?")):
            n.props["xml_margin_ref"] = _m_all
        else:
            _mv = _parse_dp_val(_m_all)
            if _mv is not None and _mv > 0:
                n.props["xml_margin"] = _mv
    if "card_radius_ref" in n.props:
        _cr_dp = _parse_dp_val(n.props["card_radius_ref"])
        if _cr_dp is not None and _cr_dp >= 0:
            n.props["card_radius"] = _cr_dp
            n.props.pop("card_radius_ref", None)
    if "card_bg_ref" in n.props and "bg_drawable" not in n.props:
        _cb_s = str(n.props["card_bg_ref"]).strip().lower()
        _lit_c = None
        if _cb_s in ("@color/white", "@android:color/white"):
            _lit_c = "#ffffff"
        elif _cb_s in ("@color/black", "@android:color/black"):
            _lit_c = "#000000"
        elif _cb_s.startswith("#") and len(_cb_s) in (7, 9):
            if len(_cb_s) == 7 or int(_cb_s[1:3], 16) > 12:
                _lit_c = "#" + _cb_s[-6:]
        if _lit_c is not None:
            n.props["bg_drawable"] = {
                "kind": "shape",
                "shape": "rectangle",
                "fill": _lit_c,
                "outline": _lit_c,
                "radius": float(n.props.get("card_radius", 8) or 8),
            }
    _pad_all = (_attr(el, "padding") or "").strip()
    _pad_h = (_attr(el, "paddingHorizontal") or _attr(el, "paddingStart") or _attr(el, "paddingLeft") or _pad_all).strip()
    _pad_v = (_attr(el, "paddingVertical") or _attr(el, "paddingTop") or _pad_all).strip()
    _pad_b = (_attr(el, "paddingBottom") or _pad_v or _pad_all).strip()
    if _pad_h:
        if _pad_h.startswith(("@dimen/", "@android:dimen/", "?")):
            n.props["xml_pad_x_ref"] = _pad_h
        else:
            _px_v = _parse_dp_val(_pad_h)
            if _px_v is not None and _px_v > 0:
                n.props["$pad_x"] = _px_v
    if _pad_v:
        if _pad_v.startswith(("@dimen/", "@android:dimen/", "?")):
            n.props["xml_pad_y_ref"] = _pad_v
        else:
            _py_v = _parse_dp_val(_pad_v)
            if _py_v is not None and _py_v > 0:
                n.props["$pad_y"] = _py_v
    if _pad_b:
        if _pad_b.startswith(("@dimen/", "@android:dimen/", "?")):
            n.props["xml_pad_b_ref"] = _pad_b
        else:
            _pb_v = _parse_dp_val(_pad_b)
            if _pb_v is not None and _pb_v > 0:
                n.props["xml_pad_b"] = _pb_v
    orient = _attr(el, "orientation")
    if orient:
        n.props["orientation"] = orient
    elif cls == "LinearLayout":
        weighted = sum(1 for c in el if isinstance(c.tag, str) and _attr(c, "layout_weight"))
        has_pref_kids = any(
            isinstance(c.tag, str)
            and ((_attr(c, "id") or "").endswith(("widget_frame", "icon_frame"))
                 or (_attr(c, "layout_width") or "").strip().startswith("0"))
            for c in el
        )
        if weighted >= 2 or (weighted >= 1 and has_pref_kids) or has_pref_kids:
            n.props["orientation"] = "horizontal"
    grav = (_attr(el, "gravity") or "").lower()
    if grav:
        tokens = [t.strip() for t in grav.replace("|", " ").split() if t.strip()]
        if any(t in ("right", "end") for t in tokens):
            n.props["gravity"] = 5
        elif any(t in ("left", "start") for t in tokens):
            n.props["gravity"] = 3
        elif any(t in ("center", "center_horizontal") for t in tokens):
            n.props["gravity"] = 1
        if "bottom" in tokens:
            n.props["v_gravity"] = "bottom"
        elif any(t in ("center", "center_vertical") for t in tokens):
            n.props["v_gravity"] = "center"
    l_grav = (_attr_any(el, "layout_gravity") or "").lower()
    if l_grav:
        ltokens = [t.strip() for t in l_grav.replace("|", " ").split() if t.strip()]
        if "bottom" in ltokens:
            n.props["l_v_gravity"] = "bottom"
        elif "top" in ltokens:
            n.props["l_v_gravity"] = "top"
        elif any(t in ("center", "center_vertical") for t in ltokens):
            n.props["l_v_gravity"] = "center"
        if any(t in ("right", "end") for t in ltokens):
            n.props["l_h_gravity"] = "end"
        elif any(t in ("left", "start") for t in ltokens):
            n.props["l_h_gravity"] = "start"
        elif any(t in ("center", "center_horizontal") for t in ltokens):
            n.props["l_h_gravity"] = "center"
    if "." in str(el.tag or ""):
        n.props["full_cls"] = str(el.tag)
    style_val = el.get("style") or ""
    if style_val:
        n.props["style"] = style_val
    if cls == "SeekBar":
        n.clickable = True
        for _sk, _pk, _def in (
            ("valueFrom", "slider_from", 0.0),
            ("valueTo", "slider_to", 4.0),
            ("stepSize", "slider_step", 1.0),
            ("value", "slider_val", 0.0),
        ):
            _sv = _attr_any(el, _sk)
            try:
                n.props[_pk] = float(_sv) if _sv is not None else _def
            except Exception:
                n.props[_pk] = _def
        _rng = max(1.0, float(n.props["slider_to"]) - float(n.props["slider_from"]))
        n.props["progress"] = int(max(0.0, min(100.0, (float(n.props["slider_val"]) - float(n.props["slider_from"])) * 100.0 / _rng)))
    if cls == "DatePicker":
        n.clickable = True
        n.props.setdefault("year", 1990)
        n.props.setdefault("month", 0)
        n.props.setdefault("day", 1)
        if not (n.text or "").strip():
            n.text = "📅 01.01.1990"
    _top_p = _attr_any(el, "layout_constraintTop_toTopOf")
    _bot_p = _attr_any(el, "layout_constraintBottom_toBottomOf")
    _bot_top = _attr_any(el, "layout_constraintBottom_toTopOf")
    _top_bot = _attr_any(el, "layout_constraintTop_toBottomOf")
    _start_p = _attr_any(el, "layout_constraintStart_toStartOf") or _attr_any(el, "layout_constraintLeft_toLeftOf")
    _end_p = _attr_any(el, "layout_constraintEnd_toEndOf") or _attr_any(el, "layout_constraintRight_toRightOf")
    _start_end = _attr_any(el, "layout_constraintStart_toEndOf") or _attr_any(el, "layout_constraintLeft_toRightOf")
    _end_start = _attr_any(el, "layout_constraintEnd_toStartOf") or _attr_any(el, "layout_constraintRight_toLeftOf")
    if _top_p == "parent":
        n.props["$cl_top_parent"] = True
    if _bot_p == "parent":
        n.props["$cl_bot_parent"] = True
    if _start_p == "parent":
        n.props["$cl_start_parent"] = True
    elif _start_p:
        n.props["$cl_start_to_start"] = str(_start_p).split("/")[-1]
    if _end_p == "parent":
        n.props["$cl_end_parent"] = True
    elif _end_p:
        n.props["$cl_end_to_end"] = str(_end_p).split("/")[-1]
    if _start_end:
        n.props["$cl_start_to_end"] = str(_start_end).split("/")[-1]
    if _end_start:
        n.props["$cl_end_to_start"] = str(_end_start).split("/")[-1]
    if _top_p and _top_p != "parent" and _top_p == _bot_p:
        n.props["$cl_same_v_target"] = str(_top_p).split("/")[-1]
    else:
        if _top_p and _top_p != "parent":
            n.props["$cl_top_to_top"] = str(_top_p).split("/")[-1]
        if _bot_p and _bot_p != "parent":
            n.props["$cl_bot_to_bot"] = str(_bot_p).split("/")[-1]
    if _start_p and _start_p != "parent" and _start_p == _end_p:
        n.props["$cl_same_h_target"] = str(_start_p).split("/")[-1]
    if _bot_top:
        n.props["$cl_bot_to_top"] = str(_bot_top).split("/")[-1]
    if _top_bot:
        n.props["$cl_top_to_bot"] = str(_top_bot).split("/")[-1]
    _cl_ratio = (_attr_any(el, "layout_constraintDimensionRatio") or "").strip()
    if _cl_ratio:
        n.props["$cl_ratio"] = _cl_ratio
    _cl_hb = (_attr_any(el, "layout_constraintHorizontal_bias") or "").strip()
    if _cl_hb:
        try:
            n.props["$cl_h_bias"] = max(0.0, min(1.0, float(_cl_hb)))
        except Exception:
            pass
    _cl_vb = (_attr_any(el, "layout_constraintVertical_bias") or "").strip()
    if _cl_vb:
        try:
            n.props["$cl_v_bias"] = max(0.0, min(1.0, float(_cl_vb)))
        except Exception:
            pass
    _cl_hchain = (_attr_any(el, "layout_constraintHorizontal_chainStyle") or "").strip()
    if _cl_hchain:
        n.props["$cl_h_chain"] = _cl_hchain
    if cls == "Guideline":
        n.props["$cl_guide_orient"] = (_attr(el, "orientation") or "vertical").lower()
        _gp = (_attr_any(el, "layout_constraintGuide_percent") or "").strip()
        if _gp:
            try:
                n.props["$cl_guide_pct"] = max(0.0, min(1.0, float(_gp)))
            except Exception:
                pass
        _gb = (_attr_any(el, "layout_constraintGuide_begin") or "").strip()
        if _gb:
            _gbv = _parse_dp_val(_gb)
            n.props["$cl_guide_begin"] = _gbv if _gbv is not None else _gb
        _ge = (_attr_any(el, "layout_constraintGuide_end") or "").strip()
        if _ge:
            _gev = _parse_dp_val(_ge)
            n.props["$cl_guide_end"] = _gev if _gev is not None else _ge
    for child in el:
        if isinstance(child.tag, str):
            n.add(_node_from_el(child))
    if cls == "ConstraintLayout" and len(n.children) >= 3:
        _reorder_constraintlayout_children(n)
    elif cls == "RelativeLayout" and len(n.children) >= 2:
        _reorder_relativelayout_children(n)
    return n


def _reorder_relativelayout_children(node):
    """Sortiert oben/oben-drueber/unten-verankerte Kinder in RelativeLayout (z. B. title_view above text_view)."""
    kids = list(node.children)
    n = len(kids)
    for _ in range(n * n):
        moved = False
        for i in range(n - 1):
            a, b = kids[i], kids[i + 1]
            swap = False
            if b.props.get("align_parent_top") and not a.props.get("align_parent_top"):
                swap = True
            elif a.vid and b.props.get("layout_above") == a.vid:
                swap = True
            elif b.vid and a.props.get("layout_below") == b.vid:
                swap = True
            elif (a.props.get("align_parent_bottom") and not b.props.get("align_parent_bottom")
                  and not b.props.get("layout_above") and b.props.get("layout_below") != a.vid):
                swap = True
            if swap:
                kids[i], kids[i + 1] = b, a
                moved = True
        if not moved:
            break
    node.children = kids


def _reorder_constraintlayout_children(node):
    """Sortiert oben/unten verankerte Kinder in ConstraintLayout (z. B. Progress oben, CTA-Buttons unten)."""
    kids = list(node.children)
    bot_ids = set()
    bot_nodes = []
    for c in kids:
        if (c.props.get("$cl_bot_parent") and not c.props.get("$cl_top_parent")
                and not c.props.get("$cl_top_to_bot") and not c.props.get("non_visual")):
            bot_nodes.append(c)
            if c.vid:
                bot_ids.add(c.vid)
    changed = True
    while changed:
        changed = False
        for c in kids:
            if c in bot_nodes or c.props.get("non_visual"):
                continue
            if (not c.props.get("$cl_top_parent") and not c.props.get("$cl_top_to_bot")
                    and c.props.get("$cl_bot_to_top") in bot_ids
                    and "ViewPager" not in c.cls and c.cls not in ("RecyclerView", "ScrollView", "FrameLayout")):
                bot_nodes.append(c)
                if c.vid:
                    bot_ids.add(c.vid)
                changed = True
    top_nodes = [
        c for c in kids
        if c not in bot_nodes and c.props.get("$cl_top_parent")
        and not c.props.get("$cl_bot_parent") and not c.props.get("$cl_bot_to_top")
        and not c.props.get("non_visual")
        and "ViewPager" not in c.cls and c.cls not in ("RecyclerView", "ScrollView")
    ]
    if not (top_nodes and bot_nodes):
        return
    # Innerhalb bot_nodes: Elemente mit $cl_bot_to_top == other.vid vor other platzieren
    if len(bot_nodes) >= 2:
        ordered_bot = list(bot_nodes)
        for _ in range(len(ordered_bot)):
            swapped = False
            for i in range(len(ordered_bot) - 1):
                for j in range(i + 1, len(ordered_bot)):
                    if ordered_bot[j].props.get("$cl_bot_to_top") == ordered_bot[i].vid:
                        ordered_bot[i], ordered_bot[j] = ordered_bot[j], ordered_bot[i]
                        swapped = True
            if not swapped:
                break
        bot_nodes = ordered_bot
    mid_nodes = [c for c in kids if c not in top_nodes and c not in bot_nodes]
    node.children = top_nodes + mid_nodes + bot_nodes


def _propagate_card_button_labels(node):
    """Kopiert Geschwister-TextView-Labels auf unbeschriftete Overlay-/Icon-Buttons in Karten."""
    if node.cls in ("RelativeLayout", "FrameLayout", "CardView", "LinearLayout"):
        empty_btns = [c for c in node.children if c.cls == "Button" and not (c.text or "").strip()]
        labeled_tvs = [c for c in node.children if c.cls == "TextView" and (c.text or "").strip()]
        if len(empty_btns) == 1 and labeled_tvs:
            btn = empty_btns[0]
            lbl_txt = labeled_tvs[0].text.strip()
            bg_spec = btn.props.get("bg_drawable")
            bg_ref = str(btn.props.get("bg_ref") or "")
            is_icon_btn = (
                (isinstance(bg_spec, dict) and bg_spec.get("kind") in ("vector", "raster"))
                or (bg_spec is None and bg_ref.startswith(("@drawable/", "@mipmap/")))
            )
            if is_icon_btn and labeled_tvs[0].visible:
                btn.props["a11y_label"] = lbl_txt
            else:
                btn.text = lbl_txt
    for c in node.children:
        _propagate_card_button_labels(c)


def parse_layout_xml(xml_text):
    """Android-XML-Layout-Text -> Wurzel-ViewNode (oder None)."""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    node = _node_from_el(root)
    if node is not None:
        _propagate_card_button_labels(node)
    return node


def build_resmap(app_root):
    """layout res_id(int) -> Layout-Name, aus res/values/public.xml (apktool/apkeditor)."""
    idmap = {}
    pub = os.path.join(_res_dir(app_root), "values", "public.xml")
    if not os.path.isfile(pub):
        return idmap
    try:
        t = ET.parse(pub)
        for el in t.getroot():
            if el.get("type") == "layout":
                rid = el.get("id")
                name = el.get("name")
                try:
                    idmap[int(rid, 16)] = name
                except Exception:
                    pass
    except Exception:
        pass
    return idmap


_ANDROID_R_IDS = {
    0x01020001: "checkbox",
    0x01020002: "content",
    0x01020004: "empty",
    0x01020006: "icon",
    0x0102000A: "list",
    0x0102000B: "message",
    0x01020010: "summary",
    0x01020014: "text1",
    0x01020015: "text2",
    0x01020016: "title",
    0x01020018: "widget_frame",
    0x01020019: "button1",
    0x0102001A: "button2",
    0x0102001B: "button3",
    0x0102002C: "home",
    0x0102003E: "icon_frame",
    0x0102003F: "list_container",
    0x01020040: "switch_widget",
}


def build_id_map(app_root):
    """id res_id(int) -> id-Name, aus res/values/public.xml (type='id') + android.R.id."""
    idmap = dict(_ANDROID_R_IDS)
    pub = os.path.join(_res_dir(app_root), "values", "public.xml")
    if not os.path.isfile(pub):
        return idmap
    try:
        t = ET.parse(pub)
        for el in t.getroot():
            if el.get("type") == "id":
                try:
                    idmap[int(el.get("id"), 16)] = el.get("name")
                except Exception:
                    pass
    except Exception:
        pass
    return idmap


def build_drawable_map(app_root):
    """drawable/mipmap res_id(int) -> Name, aus res/values/public.xml."""
    idmap = {}
    pub = os.path.join(_res_dir(app_root), "values", "public.xml")
    if not os.path.isfile(pub):
        return idmap
    try:
        t = ET.parse(pub)
        for el in t.getroot():
            if el.get("type") in ("drawable", "mipmap"):
                try:
                    idmap[int(el.get("id"), 16)] = el.get("name")
                except Exception:
                    pass
    except Exception:
        pass
    return idmap


def _clean_xml_string(el):
    txt = "".join(el.itertext())
    if len(txt) >= 2 and txt.startswith('"') and txt.endswith('"'):
        txt = txt[1:-1]
    return txt


def _resolve_string_chain(val, name2text, max_hops=5):
    cur = val
    for _ in range(max_hops):
        if not isinstance(cur, str) or not cur.startswith("@string/"):
            break
        key = cur[len("@string/"):]
        nxt = name2text.get(key)
        if nxt is None or nxt == cur:
            break
        cur = nxt
    return cur


def build_string_map(app_root):
    """(id2text, name2text): string res_id(int)->Text via public.xml(type=string) + strings.xml."""
    rdir = _res_dir(app_root)
    id2name = {}
    pub = os.path.join(rdir, "values", "public.xml")
    if os.path.isfile(pub):
        try:
            t = ET.parse(pub)
            for el in t.getroot():
                if el.get("type") == "string":
                    try:
                        id2name[int(el.get("id"), 16)] = el.get("name")
                    except Exception:
                        pass
        except Exception:
            pass
    name2text = {}
    sx = os.path.join(rdir, "values", "strings.xml")
    if os.path.isfile(sx):
        try:
            t = ET.parse(sx)
            for el in t.getroot():
                if el.tag == "string" and el.get("name") is not None:
                    name2text[el.get("name")] = _clean_xml_string(el)
        except Exception:
            pass
    for k, v in list(name2text.items()):
        if isinstance(v, str) and v.startswith("@string/"):
            name2text[k] = _resolve_string_chain(v, name2text)
    id2text = {rid: name2text.get(nm, nm) for rid, nm in id2name.items()}
    return id2text, name2text


def build_menu_map(app_root):
    """menu res_id(int) -> menu XML name via res/values/public.xml (type='menu')."""
    _, id2name = _public_type_map(app_root, "menu")
    return id2name


def parse_menu_xml(app_root, menu_ref_or_id, name2text=None, rev_idmap=None, menu_idmap=None):
    """Parst `res/menu/<name>.xml` -> Liste von Item-Dicts (vid, res_id, title, icon_ref, visible, ...)."""
    if not app_root or menu_ref_or_id in (None, "", 0):
        return []
    mname = None
    if isinstance(menu_ref_or_id, int):
        mmap = menu_idmap if menu_idmap is not None else build_menu_map(app_root)
        mname = mmap.get(int(menu_ref_or_id))
    else:
        s = str(menu_ref_or_id).strip()
        if s.startswith("@menu/"):
            mname = s[len("@menu/"):]
        elif s.isdigit():
            mmap = menu_idmap if menu_idmap is not None else build_menu_map(app_root)
            mname = mmap.get(int(s))
        elif s:
            mname = s.split("/", 1)[-1]
    if not mname:
        return []
    mpath = os.path.join(_res_dir(app_root), "menu", f"{mname}.xml")
    if not os.path.isfile(mpath):
        return []
    if name2text is None:
        try:
            _, name2text = build_string_map(app_root)
        except Exception:
            name2text = {}
    if rev_idmap is None:
        try:
            rev_idmap = {nm: rid for rid, nm in build_id_map(app_root).items()}
        except Exception:
            rev_idmap = {}
    try:
        root = ET.parse(mpath).getroot()
    except Exception:
        return []
    items = []

    def _collect(parent_el):
        for el in parent_el:
            if not isinstance(el.tag, str):
                continue
            tag = el.tag.rsplit(".", 1)[-1]
            if tag == "item":
                raw_id = (_attr(el, "id") or "").strip()
                vid = raw_id.split("/")[-1] if raw_id else ""
                try:
                    res_id = int(rev_idmap.get(vid, 0) or 0) if vid else 0
                except Exception:
                    res_id = 0
                raw_title = (_attr(el, "title") or "").strip()
                title = _resolve_string_chain(raw_title, name2text) if raw_title.startswith("@string/") else raw_title
                icon_ref = (_attr_any(el, "icon") or "").strip()
                if icon_ref.lower() in ("@null", "@empty"):
                    icon_ref = ""
                icon_name = icon_ref.split("/", 1)[-1] if "/" in icon_ref else ""
                visible = (_attr(el, "visible") or "true").strip().lower() != "false"
                enabled = (_attr(el, "enabled") or "true").strip().lower() != "false"
                show_as = (_attr_any(el, "showAsAction") or "").strip()
                try:
                    order = int((_attr(el, "orderInCategory") or "0").strip())
                except Exception:
                    order = 0
                items.append({
                    "vid": vid,
                    "res_id": res_id,
                    "title": str(title or ""),
                    "icon_ref": icon_ref,
                    "icon_name": icon_name,
                    "visible": visible,
                    "enabled": enabled,
                    "show_as_action": show_as,
                    "order": order,
                })
                for sub in el:
                    if isinstance(sub.tag, str) and sub.tag.rsplit(".", 1)[-1] in ("menu", "group"):
                        _collect(sub)
            elif tag in ("menu", "group"):
                _collect(el)

    _collect(root)
    return items


def _expand_menus(app_root, node, name2text=None, rev_idmap=None, menu_idmap=None):
    """Expandiert `@menu/...` auf BottomNavigationView/NavigationBarView zu klickbaren Tab-Kindern."""
    if node is None or not app_root:
        return
    if node.props.get("menu_ref") and not node.children:
        items = parse_menu_xml(
            app_root,
            node.props["menu_ref"],
            name2text=name2text,
            rev_idmap=rev_idmap,
            menu_idmap=menu_idmap,
        )
        vis_idx = 0
        for it in items:
            if not it.get("visible", True):
                continue
            tprops = {
                "menu_item_id": int(it.get("res_id") or 0),
                "$nav_tab": True,
                "role": "navitem",
                "selected": (vis_idx == 0),
                "enabled": bool(it.get("enabled", True)),
            }
            if it.get("icon_ref"):
                tprops["compound_drawable_ref"] = it["icon_ref"]
            if it.get("icon_name"):
                tprops["img"] = it["icon_name"]
            btn = ViewNode(
                cls="Button",
                vid=it.get("vid") or f"nav_tab_{vis_idx}",
                text=it.get("title") or it.get("vid") or f"Tab {vis_idx + 1}",
                clickable=True,
                props=tprops,
            )
            node.add(btn)
            vis_idx += 1
    for c in list(node.children):
        _expand_menus(app_root, c, name2text=name2text, rev_idmap=rev_idmap, menu_idmap=menu_idmap)


def build_array_map(app_root, name2text=None):
    """(id2arr, name2arr): array res_id(int)->list[str] via public.xml(type=array) + arrays.xml."""
    rdir = _res_dir(app_root)
    if name2text is None:
        _, name2text = build_string_map(app_root)
    id2name = {}
    pub = os.path.join(rdir, "values", "public.xml")
    if os.path.isfile(pub):
        try:
            t = ET.parse(pub)
            for el in t.getroot():
                if el.get("type") == "array":
                    try:
                        id2name[int(el.get("id"), 16)] = el.get("name")
                    except Exception:
                        pass
        except Exception:
            pass
    name2arr = {}
    ax = os.path.join(rdir, "values", "arrays.xml")
    if os.path.isfile(ax):
        try:
            t = ET.parse(ax)
            for el in t.getroot():
                if el.tag in ("string-array", "integer-array", "array") and el.get("name"):
                    items = []
                    for it in el:
                        if it.tag == "item":
                            raw = _clean_xml_string(it).strip()
                            if raw.startswith("@string/"):
                                raw = _resolve_string_chain(raw, name2text)
                            items.append(raw)
                    name2arr[el.get("name")] = items
        except Exception:
            pass
    id2arr = {rid: list(name2arr[nm]) for rid, nm in id2name.items() if nm in name2arr}
    return id2arr, name2arr


def _public_type_map(app_root, res_type):
    rdir = _res_dir(app_root)
    id2name = {}
    pub = os.path.join(rdir, "values", "public.xml")
    if os.path.isfile(pub):
        try:
            for el in ET.parse(pub).getroot():
                if el.get("type") == res_type and el.get("name") and el.get("id"):
                    try:
                        id2name[int(el.get("id"), 16)] = el.get("name")
                    except Exception:
                        pass
        except Exception:
            pass
    return rdir, id2name


def build_bool_map(app_root):
    """(id2bool, name2bool): bool res_id(int)->0/1 via public.xml(type=bool) + bools.xml."""
    rdir, id2name = _public_type_map(app_root, "bool")
    raw_map = {}
    bx = os.path.join(rdir, "values", "bools.xml")
    if os.path.isfile(bx):
        try:
            for el in ET.parse(bx).getroot():
                if (el.tag == "bool" or (el.tag == "item" and el.get("type") == "bool")) and el.get("name"):
                    raw_map[el.get("name")] = (el.text or "").strip().lower()
        except Exception:
            pass
    name2bool = {}
    for k, val in raw_map.items():
        cur = val
        for _ in range(5):
            if cur.startswith("@bool/"):
                nxt = raw_map.get(cur[len("@bool/"):])
                if nxt is None or nxt == cur:
                    break
                cur = nxt
            else:
                break
        if cur in ("true", "1", "yes"):
            name2bool[k] = True
        elif cur in ("false", "0", "no"):
            name2bool[k] = False
    id2bool = {rid: (1 if name2bool[nm] else 0) for rid, nm in id2name.items() if nm in name2bool}
    return id2bool, name2bool


def build_integer_map(app_root):
    """(id2int, name2int): integer res_id(int)->int via public.xml(type=integer) + integers.xml."""
    rdir, id2name = _public_type_map(app_root, "integer")
    raw_map = {}
    ix = os.path.join(rdir, "values", "integers.xml")
    if os.path.isfile(ix):
        try:
            for el in ET.parse(ix).getroot():
                if (el.tag == "integer" or (el.tag == "item" and el.get("type") == "integer")) and el.get("name"):
                    raw_map[el.get("name")] = (el.text or "").strip()
        except Exception:
            pass
    name2int = {}
    for k, val in raw_map.items():
        cur = val
        for _ in range(5):
            if cur.startswith("@integer/"):
                nxt = raw_map.get(cur[len("@integer/"):])
                if nxt is None or nxt == cur:
                    break
                cur = nxt
            else:
                break
        try:
            name2int[k] = int(cur, 0)
        except Exception:
            pass
    id2int = {rid: name2int[nm] for rid, nm in id2name.items() if nm in name2int}
    return id2int, name2int


def _resolve_refs(node, name2text, resolver=None):
    v = getattr(node, "text", None)
    if isinstance(v, str) and v.startswith("@string/"):
        node.text = _resolve_string_chain(v, name2text)
    for pkey in ("hint", "desc"):
        hv = node.props.get(pkey)
        if isinstance(hv, str) and hv.startswith("@string/"):
            node.props[pkey] = _resolve_string_chain(hv, name2text)
    if resolver is not None:
        for _rk, _pk in (
            ("xml_w_ref", "xml_w"),
            ("xml_h_ref", "xml_h"),
            ("xml_pad_x_ref", "$pad_x"),
            ("xml_pad_y_ref", "$pad_y"),
            ("xml_pad_b_ref", "xml_pad_b"),
            ("xml_margin_ref", "xml_margin"),
            ("card_radius_ref", "card_radius"),
        ):
            _rv = node.props.get(_rk)
            if _rv and _pk not in node.props and hasattr(resolver, "resolve_dimen"):
                _dv = resolver.resolve_dimen(_rv)
                if _dv is not None and _dv >= 0:
                    node.props[_pk] = int(round(_dv))
        for _gk in ("$cl_guide_begin", "$cl_guide_end"):
            _gv = node.props.get(_gk)
            if isinstance(_gv, str) and hasattr(resolver, "resolve_dimen"):
                _gdv = resolver.resolve_dimen(_gv)
                if _gdv is not None:
                    node.props[_gk] = int(round(_gdv))
        _st = {
            "selected": bool(node.props.get("selected")),
            "checked": bool(node.props.get("checked")),
            "enabled": bool(node.props.get("enabled", True)),
        }
        _has_non_default_state = bool(node.props.get("selected") or node.props.get("checked") or node.props.get("enabled") is False)
        _st_arg = _st if _has_non_default_state else None
        src_ref = node.props.get("src_ref")
        if src_ref and "drawable" not in node.props:
            sp = resolver.resolve(src_ref, state=_st_arg)
            if sp is not None:
                node.props["drawable"] = sp
        bg_ref = node.props.get("bg_ref")
        if bg_ref and "bg_drawable" not in node.props:
            sp = resolver.resolve(bg_ref, state=_st_arg)
            if sp is not None:
                node.props["bg_drawable"] = sp
        _color_fn = getattr(resolver, "resolve_color_for_state", None)
        card_bg_ref = node.props.get("card_bg_ref")
        if card_bg_ref and "bg_drawable" not in node.props:
            c_rgb, c_alpha = _color_fn(card_bg_ref, state=_st) if callable(_color_fn) else resolver.resolve_color(card_bg_ref)
            if c_rgb is not None and c_alpha > 0.05:
                c_rad = float(node.props.get("card_radius", 8) or 8)
                c_spec = {
                    "kind": "shape",
                    "shape": "rectangle",
                    "fill": c_rgb,
                    "outline": c_rgb,
                    "radius": c_rad,
                }
                if c_alpha < 0.999:
                    c_spec["alpha"] = float(c_alpha)
                node.props["bg_drawable"] = c_spec
        elif card_bg_ref and "card_radius" in node.props and isinstance(node.props.get("bg_drawable"), dict):
            node.props["bg_drawable"]["radius"] = float(node.props["card_radius"])
        col_ref = node.props.get("color_ref")
        if col_ref and "color" not in node.props:
            rgb, alpha = _color_fn(col_ref, state=_st) if callable(_color_fn) else resolver.resolve_color(col_ref)
            if rgb is not None and alpha > 0.05:
                node.props["color"] = rgb
        tint_ref = node.props.get("tint_ref")
        if tint_ref and "tint" not in node.props:
            rgb, alpha = _color_fn(tint_ref, state=_st) if callable(_color_fn) else resolver.resolve_color(tint_ref)
            if rgb is not None and alpha > 0.05:
                node.props["tint"] = rgb
        bg_tint_ref = node.props.get("bg_tint_ref")
        if bg_tint_ref and "bg_tint" not in node.props:
            rgb, alpha = _color_fn(bg_tint_ref, state=_st) if callable(_color_fn) else resolver.resolve_color(bg_tint_ref)
            if rgb is not None and alpha > 0.05:
                node.props["bg_tint"] = rgb
                node.props["bg_tint_alpha"] = alpha
        cd_ref = node.props.get("compound_drawable_ref")
        if cd_ref and "compound_drawable" not in node.props:
            sp = resolver.resolve(cd_ref, state=_st_arg)
            if sp is not None:
                node.props["compound_drawable"] = sp
    for c in node.children:
        _resolve_refs(c, name2text, resolver=resolver)


def _expand_includes(app_root, node, depth=0):
    if depth >= 5 or node is None:
        return node
    rdir = _res_dir(app_root)
    new_children = []
    for c in node.children:
        inc = c.props.get("include_layout")
        if inc and isinstance(inc, str):
            lname = inc.split("/", 1)[-1] if "/" in inc else inc.lstrip("@")
            ipath = os.path.join(rdir, "layout", lname + ".xml")
            if os.path.isfile(ipath):
                try:
                    with open(ipath, encoding="utf-8", errors="replace") as f:
                        inc_node = parse_layout_xml(f.read())
                    if inc_node is not None:
                        if c.vid and not inc_node.vid:
                            inc_node.vid = c.vid
                        _expand_includes(app_root, inc_node, depth + 1)
                        inc_node._parent = node
                        new_children.append(inc_node)
                        continue
                except Exception:
                    pass
        _expand_includes(app_root, c, depth + 1)
        new_children.append(c)
    node.children = new_children
    return node


def inflate_res(app_root, res_id, idmap=None, _depth=0):
    """res_id(int) -> ViewNode-Baum aus res/layout/<name>.xml (oder None)."""
    if idmap is None:
        idmap = build_resmap(app_root)
    try:
        name = idmap.get(int(res_id))
    except Exception:
        name = None
    if not name:
        return None
    path = os.path.join(_res_dir(app_root), "layout", name + ".xml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            node = parse_layout_xml(f.read())
        if node is not None:
            _expand_includes(app_root, node, _depth)
            name2text = {}
            try:
                _, name2text = build_string_map(app_root)
            except Exception:
                name2text = {}
            try:
                rev_idmap = {nm: rid for rid, nm in build_id_map(app_root).items()}
            except Exception:
                rev_idmap = {}
            try:
                menu_idmap = build_menu_map(app_root)
            except Exception:
                menu_idmap = {}
            _expand_menus(
                app_root, node,
                name2text=name2text,
                rev_idmap=rev_idmap,
                menu_idmap=menu_idmap,
            )
            resolver = None
            try:
                from core.java.drawable_loader import get_drawable_resolver
                resolver = get_drawable_resolver(app_root)
            except Exception:
                resolver = None
            if name2text or resolver is not None:
                _resolve_refs(node, name2text, resolver=resolver)
            _propagate_card_button_labels(node)
        return node
    except Exception:
        return None


_PREF_STYLE_CACHE = {}


def build_pref_style_map(app_root):
    """Loest Preference-Theme-Styles (layout, widgetLayout) ueber attr_id & Namen aus styles.xml/public.xml auf."""
    if not app_root:
        return {"by_attr": {}, "by_layout_name": {}}
    if app_root in _PREF_STYLE_CACHE:
        return _PREF_STYLE_CACHE[app_root]
    rdir = _res_dir(app_root)
    attr_by_name = {}
    layout_by_name = {}
    pub = os.path.join(rdir, "values", "public.xml")
    if os.path.isfile(pub):
        try:
            for el in ET.parse(pub).getroot():
                tp = el.get("type")
                nm = el.get("name")
                rid = el.get("id")
                if not nm or not rid:
                    continue
                try:
                    val = int(rid, 16)
                except Exception:
                    continue
                if tp == "attr":
                    attr_by_name[nm] = val
                elif tp == "layout":
                    layout_by_name[nm] = val
        except Exception:
            pass

    styles = {}
    st_path = os.path.join(rdir, "values", "styles.xml")
    if os.path.isfile(st_path):
        try:
            for st_el in ET.parse(st_path).getroot().findall("style"):
                sname = st_el.get("name")
                if not sname:
                    continue
                parent = st_el.get("parent") or ""
                if parent.startswith("@style/"):
                    parent = parent[len("@style/"):]
                items = {}
                for it in st_el.findall("item"):
                    iname = it.get("name")
                    ival = (it.text or "").strip()
                    if iname and ival:
                        items[iname] = ival
                styles[sname] = {"parent": parent, "items": items}
        except Exception:
            pass

    def _resolve_style_prop(sname, prop_keys, depth=0):
        if depth > 8 or not sname:
            return None
        entry = styles.get(sname)
        if not entry:
            return None
        for pk in prop_keys:
            if pk in entry["items"]:
                return entry["items"][pk]
        if entry["parent"]:
            return _resolve_style_prop(entry["parent"], prop_keys, depth + 1)
        if "." in sname:
            return _resolve_style_prop(sname.rsplit(".", 1)[0], prop_keys, depth + 1)
        return None

    style_attr_to_style = {}
    for _sn, entry in styles.items():
        for k, v in entry["items"].items():
            if k.endswith("Style") and v.startswith("@style/"):
                style_attr_to_style.setdefault(k, v[len("@style/"):])

    by_attr = {}
    for aname, aid in attr_by_name.items():
        target_style = style_attr_to_style.get(aname)
        if not target_style:
            continue
        lay_ref = _resolve_style_prop(target_style, ("android:layout", "layout"))
        wid_ref = _resolve_style_prop(target_style, ("android:widgetLayout", "widgetLayout"))
        info = {}
        if lay_ref and lay_ref.startswith("@layout/"):
            lid = layout_by_name.get(lay_ref[len("@layout/"):])
            if lid:
                info["layout"] = lid
        if wid_ref and wid_ref.startswith("@layout/"):
            wid = layout_by_name.get(wid_ref[len("@layout/"):])
            if wid:
                info["widgetLayout"] = wid
        if info:
            by_attr[aid] = info

    out = {"by_attr": by_attr, "by_layout_name": layout_by_name, "by_attr_name": attr_by_name}
    _PREF_STYLE_CACHE[app_root] = out
    return out
