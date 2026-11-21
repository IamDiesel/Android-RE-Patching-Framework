# -*- coding: utf-8 -*-
"""core.java.rn_yoga_seams — G5: 2D Flexbox/Yoga Layout, Styles, AsyncStorage & Networking-Seam fuer RN."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.java.objects import JArray, JObject, JString

_NAMED_COLORS = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ef4444",
    "green": "#22c55e",
    "blue": "#3b82f6",
    "gray": "#6b7280",
    "grey": "#6b7280",
    "yellow": "#eab308",
    "orange": "#f97316",
    "purple": "#a855f7",
    "cyan": "#06b6d4",
    "transparent": "#000000",
}
_RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([0-9.]+))?\s*\)", re.I)


def flatten_rn_style(style_prop: Any) -> Dict[str, Any]:
    """Reduziert verschachtelte RN-Style-Listen/Dicts deterministisch zu einem flachen Dict."""
    out: Dict[str, Any] = {}
    if isinstance(style_prop, dict):
        out.update(style_prop)
    elif isinstance(style_prop, (list, tuple)):
        for item in style_prop:
            if item:
                out.update(flatten_rn_style(item))
    return out


def parse_rn_color(raw: Any) -> Tuple[Optional[str], float]:
    if not raw or not isinstance(raw, str):
        return None, 1.0
    s = raw.strip()
    low = s.lower()
    if low == "transparent":
        return "#000000", 0.0
    if low in _NAMED_COLORS:
        return _NAMED_COLORS[low], 1.0
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            r, g, b = (int(c * 2, 16) for c in h)
            return f"#{r:02x}{g:02x}{b:02x}", 1.0
        if len(h) == 6:
            return f"#{h.lower()}", 1.0
        if len(h) == 8:
            # RN CSS #RRGGBBAA
            r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
            return f"#{r:02x}{g:02x}{b:02x}", round(a / 255.0, 3)
    m = _RGBA_RE.match(s)
    if m:
        r, g, b = max(0, min(255, int(m.group(1)))), max(0, min(255, int(m.group(2)))), max(0, min(255, int(m.group(3))))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return f"#{r:02x}{g:02x}{b:02x}", max(0.0, min(1.0, a))
    return None, 1.0


def apply_rn_visual_style(node: Any, style_dict: Dict[str, Any], props: Optional[Dict[str, Any]] = None) -> None:
    """Uebertraegt visuelle RN-Styles (Farben, Borders, Radii, TextAlign, Opacity) auf `ViewNode`."""
    if not style_dict:
        return
    node.props["rn_style"] = dict(style_dict)
    bg_hex, bg_a = parse_rn_color(style_dict.get("backgroundColor"))
    brd_hex, brd_a = parse_rn_color(style_dict.get("borderColor"))
    brd_w = style_dict.get("borderWidth")
    rad = style_dict.get("borderRadius")
    if bg_hex or brd_hex or rad is not None or brd_w is not None:
        shape = dict(node.props.get("bg_drawable") or {"kind": "shape", "shape": "rectangle"})
        if bg_hex and bg_a > 0.01:
            shape["fill"] = bg_hex
            if bg_a < 0.99:
                node.props["bg_alpha"] = bg_a
        if brd_hex and brd_a > 0.01:
            shape["outline"] = brd_hex
            shape["stroke_w"] = max(1.0, float(brd_w or 1.0))
        elif brd_w:
            shape["stroke_w"] = max(1.0, float(brd_w))
        if rad is not None:
            try:
                shape["radius"] = max(0.0, float(rad))
            except Exception:
                pass
        node.props["bg_drawable"] = shape

    fg_hex, fg_a = parse_rn_color(style_dict.get("color"))
    if fg_hex and fg_a > 0.01:
        node.props["color"] = fg_hex
    if "opacity" in style_dict:
        try:
            op = max(0.0, min(1.0, float(style_dict["opacity"])))
            node.props["alpha"] = op
            node.props["bg_alpha"] = op
        except Exception:
            pass
    ta = str(style_dict.get("textAlign") or "").lower()
    if ta == "center":
        node.props["gravity"] = 1
    elif ta in ("right", "end"):
        node.props["gravity"] = 5
    elif ta in ("left", "start"):
        node.props["gravity"] = 3
    if "fontSize" in style_dict:
        try:
            node.props["font_size"] = float(style_dict["fontSize"])
        except Exception:
            pass


def _num_or_pct(val: Any, ref: float) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s.endswith("%"):
        try:
            return (float(s[:-1]) / 100.0) * float(ref)
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None


def _edges(st: Dict[str, Any], prefix: str, ref_w: float, ref_h: float) -> Tuple[float, float, float, float]:
    """(top, right, bottom, left) aus RN padding/margin-Styles."""
    base = _num_or_pct(st.get(prefix), ref_w) or 0.0
    h = _num_or_pct(st.get(f"{prefix}Horizontal"), ref_w)
    v = _num_or_pct(st.get(f"{prefix}Vertical"), ref_h)
    top = _num_or_pct(st.get(f"{prefix}Top"), ref_h)
    bot = _num_or_pct(st.get(f"{prefix}Bottom"), ref_h)
    left = _num_or_pct(st.get(f"{prefix}Left") if f"{prefix}Left" in st else st.get(f"{prefix}Start"), ref_w)
    right = _num_or_pct(st.get(f"{prefix}Right") if f"{prefix}Right" in st else st.get(f"{prefix}End"), ref_w)
    return (
        top if top is not None else (v if v is not None else base),
        right if right is not None else (h if h is not None else base),
        bot if bot is not None else (v if v is not None else base),
        left if left is not None else (h if h is not None else base),
    )


def _node_style(node: Any) -> Dict[str, Any]:
    return dict(node.props.get("rn_style") or {})


def _measure_rn_intrinsic(node: Any, avail_w: float, avail_h: float) -> Tuple[float, float]:
    st = _node_style(node)
    ew = _num_or_pct(st.get("width"), avail_w)
    eh = _num_or_pct(st.get("height"), avail_h)
    pt, pr, pb, pl = _edges(st, "padding", avail_w, avail_h)
    kids = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True) and _node_style(c).get("position") != "absolute"]
    if not kids:
        if node.cls == "TextView":
            txt = str(node.text or "")
            fsize = float(st.get("fontSize") or 14)
            iw = max(24.0, min(avail_w, len(txt) * (fsize * 0.56) + pl + pr + 8)) if ew is None else ew
            eh_def = max(22.0, fsize * 1.6 + pt + pb)
            return iw, (eh if eh is not None else eh_def)
        if node.cls == "EditText":
            return (ew if ew is not None else avail_w), (eh if eh is not None else 40.0)
        if node.cls == "ImageView":
            return (ew if ew is not None else 64.0), (eh if eh is not None else 64.0)
        if node.cls == "Switch":
            return (ew if ew is not None else 52.0), (eh if eh is not None else 32.0)
        w_def = ew if ew is not None else (avail_w if not node.children else 80.0)
        h_def = eh if eh is not None else (38.0 if node.clickable else 24.0)
        return w_def, h_def

    is_row = str(st.get("flexDirection") or "column").startswith("row")
    gap_key = "columnGap" if is_row else "rowGap"
    gap = float(st.get(gap_key, st.get("gap", 0)) or 0)
    inner_w = max(0.0, (ew if ew is not None else avail_w) - pl - pr)
    inner_h = max(0.0, (eh if eh is not None else avail_h) - pt - pb)
    sum_main = 0.0
    max_cross = 0.0
    for idx, c in enumerate(kids):
        cst = _node_style(c)
        mt, mr, mb, ml = _edges(cst, "margin", inner_w, inner_h)
        cw, ch = _measure_rn_intrinsic(c, max(0.0, inner_w - ml - mr), max(0.0, inner_h - mt - mb))
        if is_row:
            sum_main += cw + ml + mr + (gap if idx > 0 else 0.0)
            max_cross = max(max_cross, ch + mt + mb)
        else:
            sum_main += ch + mt + mb + (gap if idx > 0 else 0.0)
            max_cross = max(max_cross, cw + ml + mr)
    calc_w = (sum_main + pl + pr) if is_row else max(inner_w + pl + pr, max_cross + pl + pr)
    calc_h = (max_cross + pt + pb) if is_row else (sum_main + pt + pb)
    return (ew if ew is not None else min(avail_w, calc_w)), (eh if eh is not None else calc_h)


def _layout_rn_node(node: Any, x: float, y: float, w: float, h: float) -> None:
    node.bounds = (float(round(x, 2)), float(round(y, 2)), max(0.0, float(round(w, 2))), max(0.0, float(round(h, 2))))
    kids = [c for c in getattr(node, "children", ()) or () if getattr(c, "visible", True)]
    if not kids:
        return
    st = _node_style(node)
    pt, pr, pb, pl = _edges(st, "padding", w, h)
    cw = max(0.0, w - pl - pr)
    ch = max(0.0, h - pt - pb)
    cx0, cy0 = x + pl, y + pt

    flow = [c for c in kids if _node_style(c).get("position") != "absolute"]
    abs_kids = [c for c in kids if _node_style(c).get("position") == "absolute"]

    if flow:
        fdir = str(st.get("flexDirection") or "column").lower()
        is_row = fdir.startswith("row")
        is_rev = fdir.endswith("reverse")
        ordered = list(reversed(flow)) if is_rev else flow
        gap = float(st.get("columnGap" if is_row else "rowGap", st.get("gap", 0)) or 0)
        justify = str(st.get("justifyContent") or "flex-start").lower()
        align_items = str(st.get("alignItems") or "stretch").lower()

        main_avail = cw if is_row else ch
        cross_avail = ch if is_row else cw
        n = len(ordered)

        child_infos: List[Dict[str, Any]] = []
        total_flex = 0.0
        used_main = gap * max(0, n - 1)
        for c in ordered:
            cst = _node_style(c)
            mt, mr, mb, ml = _edges(cst, "margin", cw, ch)
            flex_v = float(cst.get("flexGrow", cst.get("flex", 0)) or 0)
            ew = _num_or_pct(cst.get("width"), cw)
            eh = _num_or_pct(cst.get("height"), ch)
            explicit_main = ew if is_row else eh
            explicit_cross = eh if is_row else ew
            if explicit_main is not None:
                base_main = max(0.0, explicit_main)
            elif flex_v > 0:
                base_main = 0.0
            else:
                iw, ih = _measure_rn_intrinsic(c, max(0.0, cw - ml - mr), max(0.0, ch - mt - mb))
                base_main = iw if is_row else ih
            m_before = ml if is_row else mt
            m_after = mr if is_row else mb
            c_before = mt if is_row else ml
            c_after = mb if is_row else mr
            used_main += base_main + m_before + m_after
            if flex_v > 0 and explicit_main is None:
                total_flex += flex_v
            child_infos.append({
                "node": c, "st": cst,
                "flex": flex_v if explicit_main is None else 0.0,
                "main": base_main, "explicit_cross": explicit_cross,
                "m0": m_before, "m1": m_after, "c0": c_before, "c1": c_after,
            })

        rem = max(0.0, main_avail - used_main)
        if total_flex > 0 and rem > 0:
            for inf in child_infos:
                if inf["flex"] > 0:
                    inf["main"] += rem * (inf["flex"] / total_flex)
            free = 0.0
        else:
            free = max(0.0, rem)

        cursor = 0.0
        extra_gap = 0.0
        if free > 0:
            if justify == "center":
                cursor = free / 2.0
            elif justify == "flex-end":
                cursor = free
            elif justify == "space-between" and n > 1:
                extra_gap = free / float(n - 1)
            elif justify == "space-around" and n > 0:
                step = free / float(n)
                cursor = step / 2.0
                extra_gap = step
            elif justify == "space-evenly" and n > 0:
                step = free / float(n + 1)
                cursor = step
                extra_gap = step

        for idx, inf in enumerate(child_infos):
            c = inf["node"]
            cst = inf["st"]
            m_pos = cursor + inf["m0"]
            m_sz = inf["main"]
            c_avail_net = max(0.0, cross_avail - inf["c0"] - inf["c1"])
            align = str(cst.get("alignSelf") or align_items or "stretch").lower()
            if align == "auto":
                align = align_items
            if inf["explicit_cross"] is not None:
                c_sz = max(0.0, inf["explicit_cross"])
            elif align == "stretch":
                c_sz = c_avail_net
            else:
                iw, ih = _measure_rn_intrinsic(
                    c,
                    m_sz if is_row else c_avail_net,
                    c_avail_net if is_row else m_sz,
                )
                c_sz = ih if is_row else iw

            if align == "center":
                c_pos = inf["c0"] + max(0.0, (c_avail_net - c_sz) / 2.0)
            elif align == "flex-end":
                c_pos = inf["c0"] + max(0.0, c_avail_net - c_sz)
            else:
                c_pos = inf["c0"]

            if is_row:
                _layout_rn_node(c, cx0 + m_pos, cy0 + c_pos, m_sz, c_sz)
            else:
                _layout_rn_node(c, cx0 + c_pos, cy0 + m_pos, c_sz, m_sz)
            cursor += inf["m0"] + m_sz + inf["m1"] + gap + extra_gap

    for ac in abs_kids:
        ast = _node_style(ac)
        iw, ih = _measure_rn_intrinsic(ac, w, h)
        aw = _num_or_pct(ast.get("width"), w)
        ah = _num_or_pct(ast.get("height"), h)
        aw = iw if aw is None else aw
        ah = ih if ah is None else ah
        left_v = _num_or_pct(ast.get("left"), w)
        right_v = _num_or_pct(ast.get("right"), w)
        top_v = _num_or_pct(ast.get("top"), h)
        bot_v = _num_or_pct(ast.get("bottom"), h)
        ax = left_v if left_v is not None else (max(0.0, w - aw - right_v) if right_v is not None else 0.0)
        ay = top_v if top_v is not None else (max(0.0, h - ah - bot_v) if bot_v is not None else 0.0)
        _layout_rn_node(ac, x + ax, y + ay, aw, ah)


def layout_rn_flexbox(root: Any, width: float = 380.0, height: float = 800.0) -> None:
    """Berechnet 2D Yoga-Flexbox-Bounds rekursiv auf dem RN-ViewTree."""
    if root is None:
        return
    st = _node_style(root)
    rw = _num_or_pct(st.get("width"), width) or float(width)
    rh = _num_or_pct(st.get("height"), height) or float(height)
    _layout_rn_node(root, 0.0, 0.0, rw, rh)


class RnAsyncStorage:
    """Persistenter Key-Value-Store fuer React Native AsyncStorage (pro Lauf / AppData)."""

    def __init__(self, stubs: Any = None, backing_path: Optional[str] = None):
        self.stubs = stubs
        self.backing_path = backing_path
        if stubs is not None:
            self.data: Dict[str, Optional[str]] = stubs.__dict__.setdefault("_rn_async_storage", {})
        else:
            self.data = {}
        if backing_path and os.path.isfile(backing_path) and not self.data:
            self.load()

    def _save_if_configured(self) -> None:
        path = self.backing_path
        if not path and self.stubs is not None and getattr(self.stubs, "_appdata_dir", None):
            path = os.path.join(self.stubs._appdata_dir, "rn_async_storage.json")
        if not path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def load(self) -> int:
        path = self.backing_path
        if not path or not os.path.isfile(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self.data.update({str(k): (None if v is None else str(v)) for k, v in loaded.items()})
                return len(loaded)
        except Exception:
            pass
        return 0

    def get_item(self, key: str) -> Optional[str]:
        return self.data.get(str(key))

    def set_item(self, key: str, value: Any) -> None:
        self.data[str(key)] = None if value is None else str(value)
        self._save_if_configured()

    def remove_item(self, key: str) -> None:
        self.data.pop(str(key), None)
        self._save_if_configured()

    def clear(self) -> None:
        self.data.clear()
        self._save_if_configured()

    def get_all_keys(self) -> List[str]:
        return list(self.data.keys())


class RnNetworkingBridge:
    """Leitet RN `NetworkingModule` / `fetch` Anfragen in `stubs.net_fire` (`NetLog`)."""

    def __init__(self, stubs: Any = None):
        self.stubs = stubs

    def send_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
    ) -> Dict[str, Any]:
        m = str(method or "GET").upper()
        u = str(url or "")
        hdrs = dict(headers or {})
        raw_b = body if isinstance(body, (bytes, bytearray)) else str(body or "").encode("utf-8")
        res = {"method": m, "url": u, "code": 200, "headers": {}, "body": b""}
        if self.stubs is not None and callable(getattr(self.stubs, "net_fire", None)):
            try:
                resp = self.stubs.net_fire(m, u, hdrs, bytes(raw_b))
            except TypeError:
                resp = self.stubs.net_fire(m, u, hdrs, bytes(raw_b))
            if resp is not None:
                res["code"] = int(getattr(resp, "code", 200) or 200)
                res["headers"] = dict(getattr(resp, "headers", None) or {})
                rb = getattr(resp, "body", b"")
                res["body"] = bytes(rb) if isinstance(rb, (bytes, bytearray)) else str(rb or "").encode("utf-8")
        if self.stubs is not None:
            self.stubs.__dict__.setdefault("_rn_net_requests", []).append(res)
        return res


def _jstr(v: Any) -> str:
    if isinstance(v, JString):
        return v.text or ""
    return "" if v is None else str(v)


def install_rn_native_seams(stubs: Any, rec: Any = None, backing_path: Optional[str] = None) -> Dict[str, Any]:
    """Registriert `AsyncStorageModule` und `NetworkingModule` Stubs fuer React Native (G5)."""
    if stubs is None:
        return {}
    storage = getattr(stubs, "_rn_storage_seam", None)
    if storage is None:
        storage = RnAsyncStorage(stubs=stubs, backing_path=backing_path)
        stubs._rn_storage_seam = storage
    net_bridge = getattr(stubs, "_rn_net_bridge", None)
    if net_bridge is None:
        net_bridge = RnNetworkingBridge(stubs=stubs)
        stubs._rn_net_bridge = net_bridge

    if getattr(stubs, "_g5_seams_installed", False):
        return {"storage": storage, "net": net_bridge}
    stubs._g5_seams_installed = True
    R = stubs.register

    def _invoke_cb(cb_obj: Any, *cb_args: Any) -> None:
        vm = getattr(stubs, "vm", None)
        if not isinstance(cb_obj, JObject) or vm is None or not hasattr(vm, "invoke_virtual"):
            return
        arr = JArray("[Ljava/lang/Object;", list(cb_args))
        try:
            vm.invoke_virtual(cb_obj, "invoke", "([Ljava/lang/Object;)V", (cb_obj, arr))
        except Exception:
            pass

    for as_cls in (
        "Lcom/reactnativecommunity/asyncstorage/AsyncStorageModule;",
        "Lcom/facebook/react/modules/storage/AsyncStorageModule;",
    ):
        def _multi_get(a):
            keys_obj = a[1] if len(a) > 1 else None
            cb = a[2] if len(a) > 2 else None
            keys = keys_obj.fields.get("__items", []) if isinstance(keys_obj, JObject) else []
            rows = []
            for k in keys:
                ks = _jstr(k)
                val = storage.get_item(ks)
                rows.append(JObject("Ljava/util/ArrayList;", {"__items": [JString(ks), JString(val) if val is not None else None]}))
            _invoke_cb(cb, None, JObject("Ljava/util/ArrayList;", {"__items": rows}))
            return None

        def _multi_set(a):
            pairs_obj = a[1] if len(a) > 1 else None
            cb = a[2] if len(a) > 2 else None
            pairs = pairs_obj.fields.get("__items", []) if isinstance(pairs_obj, JObject) else []
            for pr in pairs:
                items = pr.fields.get("__items", []) if isinstance(pr, JObject) else []
                if len(items) >= 2:
                    storage.set_item(_jstr(items[0]), None if items[1] is None else _jstr(items[1]))
            _invoke_cb(cb, None)
            return None

        def _multi_rm(a):
            keys_obj = a[1] if len(a) > 1 else None
            cb = a[2] if len(a) > 2 else None
            keys = keys_obj.fields.get("__items", []) if isinstance(keys_obj, JObject) else []
            for k in keys:
                storage.remove_item(_jstr(k))
            _invoke_cb(cb, None)
            return None

        def _clear(a):
            cb = a[1] if len(a) > 1 else None
            storage.clear()
            _invoke_cb(cb, None)
            return None

        def _get_keys(a):
            cb = a[1] if len(a) > 1 else None
            _invoke_cb(cb, None, JObject("Ljava/util/ArrayList;", {"__items": [JString(k) for k in storage.get_all_keys()]}))
            return None

        R(as_cls, "multiGet", "(Lcom/facebook/react/bridge/ReadableArray;Lcom/facebook/react/bridge/Callback;)V", _multi_get)
        R(as_cls, "multiSet", "(Lcom/facebook/react/bridge/ReadableArray;Lcom/facebook/react/bridge/Callback;)V", _multi_set)
        R(as_cls, "multiRemove", "(Lcom/facebook/react/bridge/ReadableArray;Lcom/facebook/react/bridge/Callback;)V", _multi_rm)
        R(as_cls, "clear", "(Lcom/facebook/react/bridge/Callback;)V", _clear)
        R(as_cls, "getAllKeys", "(Lcom/facebook/react/bridge/Callback;)V", _get_keys)

    NM = "Lcom/facebook/react/modules/network/NetworkingModule;"
    R(
        NM,
        "sendRequest",
        "(Ljava/lang/String;Ljava/lang/String;ILcom/facebook/react/bridge/ReadableArray;Lcom/facebook/react/bridge/ReadableMap;Ljava/lang/String;ZIZ)V",
        lambda a: net_bridge.send_request(_jstr(a[1]) if len(a) > 1 else "GET", _jstr(a[2]) if len(a) > 2 else ""),
    )
    R(
        NM,
        "sendRequest",
        "(Ljava/lang/String;Ljava/lang/String;)V",
        lambda a: net_bridge.send_request(_jstr(a[1]) if len(a) > 1 else "GET", _jstr(a[2]) if len(a) > 2 else ""),
    )
    return {"storage": storage, "net": net_bridge}
