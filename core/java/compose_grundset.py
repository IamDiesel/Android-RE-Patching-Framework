# -*- coding: utf-8 -*-
"""core.java.compose_grundset — G2: Jetpack Compose Modifier, Shapes, Color, Flow & Widgets."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from core.java.objects import JObject


def unpack_compose_color(val: Any) -> Tuple[Optional[str], float]:
    """Dekodiert Jetpack-Compose 64-Bit-ULong ``Color`` oder 32-Bit ARGB-Int zu ``(#rrggbb, alpha)``."""
    if val is None or isinstance(val, bool):
        return None, 1.0
    if isinstance(val, JObject):
        if "v" in val.fields:
            val = val.fields["v"]
        elif "value" in val.fields:
            val = val.fields["value"]
        else:
            return None, 1.0
    try:
        raw = int(val)
    except Exception:
        return None, 1.0
    u64 = raw & 0xFFFFFFFFFFFFFFFF
    if u64 == 16:  # Color.Unspecified
        return None, 1.0
    if u64 == 0:   # Color.Transparent
        return "#000000", 0.0
    high32 = (u64 >> 32) & 0xFFFFFFFF
    low32 = u64 & 0xFFFFFFFF
    if high32 != 0 and low32 <= 63:
        argb = high32
    elif u64 <= 0xFFFFFFFF or high32 == 0xFFFFFFFF:
        argb = low32
    else:
        argb = high32 if low32 == 0 else low32
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}", round(a / 255.0, 3)


def apply_compose_mod_props(vn: Any, mod_obj: Any) -> None:
    """Uebertraegt erweiterte Modifier-Attribute (Padding, Background, Border, Shape, Alpha) auf ``ViewNode``."""
    if vn is None or not isinstance(mod_obj, JObject):
        return
    f = mod_obj.fields
    if "$weight" in f:
        vn.props["layout_weight"] = f["$weight"]
    if f.get("$fill_max_h"):
        vn.props["$fill_max_h"] = True
    if "$h" in f:
        vn.props["h"] = f["$h"]
    if "$w" in f:
        vn.props["w"] = f["$w"]
    if "$pad_x" in f:
        vn.props["$pad_x"] = int(f["$pad_x"])
    if "$pad_y" in f:
        vn.props["$pad_y"] = int(f["$pad_y"])
    if "$alpha" in f:
        vn.props["alpha"] = float(f["$alpha"])
        vn.props["bg_alpha"] = float(f["$alpha"])
    if "$scroll" in f:
        vn.props["scroll"] = f["$scroll"]
    bg_col = f.get("$bg_color")
    bg_alpha = f.get("$bg_alpha", 1.0)
    brd_col = f.get("$border_color")
    brd_w = f.get("$border_w")
    rad = f.get("$radius")
    if bg_col or brd_col or rad is not None:
        cur = dict(vn.props.get("bg_drawable") or {"kind": "shape", "shape": "rectangle"})
        if bg_col and bg_alpha > 0.01:
            cur["fill"] = bg_col
            if bg_alpha < 0.99:
                vn.props["bg_alpha"] = bg_alpha
        if brd_col:
            cur["outline"] = brd_col
            cur["stroke_w"] = max(1.0, float(brd_w or 1.0))
        if rad is not None:
            cur["radius"] = max(0.0, float(rad))
        vn.props["bg_drawable"] = cur


def _extract_shape_radius(obj: Any) -> Optional[float]:
    if not isinstance(obj, JObject):
        return None
    if "$radius" in obj.fields:
        return float(obj.fields["$radius"])
    if "Circle" in (obj.cls or ""):
        return 999.0
    return None


def _boxed_float(v: float) -> JObject:
    return JObject("Ljava/lang/Float;", {"value": float(v)})


def handle_compose_grundset(
    cls: str,
    rcls: str,
    name: str,
    sig: str,
    args: tuple,
    static: bool,
    vm: Any,
    state: Dict[str, Any],
    tree: Any,
    helpers: Dict[str, Callable[..., Any]],
) -> Tuple[bool, Any]:
    """Behandelt G2-Compose-Grundset (Shapes, Modifiers, Flow, Grids, Pager, Chips, Slider, BottomSheet)."""
    ViewNode = helpers["ViewNode"]
    _args_by_type = helpers["_args_by_type"]
    _f32_num = helpers["_f32_num"]
    _txt = helpers["_txt"]
    _cur_parent = helpers["_cur_parent"]
    _apply_mod_layout_props = helpers["_apply_mod_layout_props"]
    _invoke_fn0 = helpers["_invoke_fn0"]
    _invoke_fn2 = helpers["_invoke_fn2"]
    _invoke_fn3 = helpers["_invoke_fn3"]
    _collect_slot_text = helpers["_collect_slot_text"]
    _compose_click = helpers["_compose_click"]
    _boxed_int = helpers["_boxed_int"]
    _unwrap_fn = helpers["_unwrap_fn"]

    # 1) Shapes (RoundedCornerShape / CircleShape)
    if cls.startswith("Landroidx/compose/foundation/shape/"):
        if name == "<clinit>":
            return True, None
        if name == "getCircleShape" or "Circle" in cls or "Circle" in name:
            return True, JObject("Landroidx/compose/foundation/shape/RoundedCornerShape;", {"$radius": 999.0})
        if name.startswith("RoundedCornerShape") or name == "<init>":
            pairs = _args_by_type(sig, static, args)
            f_vals = [_f32_num(v, 0.0) for pt, v in pairs if pt == "F"]
            i_vals = [int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)]
            rad = f_vals[0] if f_vals else (float(i_vals[0]) if i_vals else 12.0)
            return True, JObject("Landroidx/compose/foundation/shape/RoundedCornerShape;", {"$radius": max(0.0, rad)})

    # 2) Extended Modifiers (PaddingKt, BackgroundKt, BorderKt, ClipKt, ShadowKt, AlphaKt, ScrollKt)
    if cls in (
        "Landroidx/compose/foundation/layout/PaddingKt;",
        "Landroidx/compose/foundation/BackgroundKt;",
        "Landroidx/compose/foundation/BorderKt;",
        "Landroidx/compose/ui/draw/ClipKt;",
        "Landroidx/compose/ui/draw/ShadowKt;",
        "Landroidx/compose/ui/draw/AlphaKt;",
        "Landroidx/compose/foundation/ScrollKt;",
        "Landroidx/compose/foundation/ScrollState;",
        "Landroidx/compose/foundation/ScrollState$Companion;",
    ):
        if cls.startswith("Landroidx/compose/foundation/Scroll") and not sig.endswith(")Landroidx/compose/ui/Modifier;"):
            if name == "rememberScrollState":
                init_p = int(args[0]) if args and isinstance(args[0], int) else 0
                return True, JObject("Landroidx/compose/foundation/ScrollState;", {"$scroll_pos": init_p})
            if name == "getValue" and args and isinstance(args[0], JObject):
                return True, int(args[0].fields.get("$scroll_pos", 0))
            if sig.endswith(")I"):
                return True, 0
            return True, None

        base_mod = args[0] if (args and isinstance(args[0], JObject)) else None
        if not static and base_mod is not None and "Scope" in (base_mod.cls or ""):
            base_mod = args[1] if (len(args) > 1 and isinstance(args[1], JObject)) else None
        fields = dict(base_mod.fields) if isinstance(base_mod, JObject) else {}
        pairs = _args_by_type(sig, static, args)
        m_base = name.split("-")[0].split("$")[0]
        f_vals = [_f32_num(v, 0.0) for pt, v in pairs if pt == "F"]

        if cls == "Landroidx/compose/foundation/layout/PaddingKt;" and m_base == "padding":
            if len(f_vals) >= 4:
                px = int(round(max(f_vals[0], f_vals[2])))
                py = int(round(max(f_vals[1], f_vals[3])))
                fields["$pad_x"] = max(0, min(64, px))
                fields["$pad_y"] = max(0, min(64, py))
            elif len(f_vals) >= 2:
                fields["$pad_x"] = max(0, min(64, int(round(f_vals[0]))))
                fields["$pad_y"] = max(0, min(64, int(round(f_vals[1]))))
            elif len(f_vals) == 1:
                pv = max(0, min(64, int(round(f_vals[0]))))
                fields["$pad_x"] = pv
                fields["$pad_y"] = pv
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/foundation/BackgroundKt;" and m_base == "background":
            col_arg = next((v for pt, v in pairs if pt == "J"), None)
            if col_arg is None:
                col_arg = next((v for pt, v in pairs if isinstance(v, JObject) and "Color" in (v.cls or "")), None)
            rgb, alpha = unpack_compose_color(col_arg)
            if rgb is not None:
                fields["$bg_color"] = rgb
                fields["$bg_alpha"] = alpha
            for _, val in pairs:
                r = _extract_shape_radius(val)
                if r is not None:
                    fields["$radius"] = r
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/foundation/BorderKt;" and m_base == "border":
            col_arg = next((v for pt, v in pairs if pt == "J"), None)
            if col_arg is None:
                col_arg = next((v for pt, v in pairs if isinstance(v, JObject) and "Color" in (v.cls or "")), None)
            rgb, alpha = unpack_compose_color(col_arg)
            if rgb is not None and alpha > 0.01:
                fields["$border_color"] = rgb
            if f_vals:
                fields["$border_w"] = max(1.0, min(12.0, f_vals[0]))
            for _, val in pairs:
                r = _extract_shape_radius(val)
                if r is not None:
                    fields["$radius"] = r
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls in ("Landroidx/compose/ui/draw/ClipKt;", "Landroidx/compose/ui/draw/ShadowKt;"):
            for _, val in pairs:
                r = _extract_shape_radius(val)
                if r is not None:
                    fields["$radius"] = r
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/ui/draw/AlphaKt;" and m_base == "alpha" and f_vals:
            fields["$alpha"] = max(0.0, min(1.0, float(f_vals[0])))
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

        if cls == "Landroidx/compose/foundation/ScrollKt;":
            fields["$scroll"] = "horizontal" if "horizontal" in m_base.lower() else "vertical"
            return True, JObject("Landroidx/compose/ui/Modifier;", fields)

    # 3) FlowExtKt.collectAsStateWithLifecycle
    if cls == "Landroidx/lifecycle/compose/FlowExtKt;" and name.startswith("collectAsStateWithLifecycle"):
        flow_obj = args[0] if args else None
        cur_val = None
        if isinstance(flow_obj, JObject):
            if "$state_val" in flow_obj.fields:
                cur_val = flow_obj.fields.get("$state_val")
            elif "$last" in flow_obj.fields:
                cur_val = flow_obj.fields.get("$last")
            elif len(args) > 1 and args[1] is not None and not isinstance(args[1], JObject):
                cur_val = args[1]
            elif vm is not None:
                try:
                    cur_val = vm.invoke_virtual(flow_obj, "getValue", "()Ljava/lang/Object;", (flow_obj,))
                except Exception:
                    cur_val = flow_obj.fields.get("value")
        if cur_val is None and len(args) > 1 and not (isinstance(args[1], JObject) and "Lifecycle" in (args[1].cls or "")):
            cur_val = args[1]
        return True, JObject("Landroidx/compose/runtime/MutableState;", {"$state_val": cur_val, "$source_flow": flow_obj})

    # 4) Compose Widgets: Spacer, Divider, LazyVerticalGrid, HorizontalPager, ModalBottomSheet, Chip, Slider, RadioButton
    m_base = name.split("-")[0].split("$")[0]
    if cls.startswith(("Landroidx/compose/material", "Landroidx/compose/foundation/")):
        pairs = _args_by_type(sig, static, args)

        # Spacer
        if cls == "Landroidx/compose/foundation/layout/SpacerKt;" and m_base == "Spacer":
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            sp_props: Dict[str, Any] = {"$compose_spacer": True}
            sp = ViewNode(cls="View", props=sp_props)
            _apply_mod_layout_props(sp, mod)
            if "h" not in sp.props and "w" not in sp.props and "layout_weight" not in sp.props:
                sp.props["h"] = 12
            _cur_parent().add(sp)
            tree.touch()
            return True, None

        # Divider / HorizontalDivider / VerticalDivider
        if m_base in ("Divider", "HorizontalDivider", "VerticalDivider") or "DividerKt" in cls:
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            col_arg = next((v for pt, v in pairs if pt == "J"), None)
            rgb, alpha = unpack_compose_color(col_arg)
            fill_c = rgb if (rgb and alpha > 0.01) else "#cbd5e1"
            is_v = "Vertical" in m_base
            dprops: Dict[str, Any] = {
                "h": 24 if is_v else 1,
                "bg_drawable": {"kind": "shape", "shape": "rectangle", "fill": fill_c, "radius": 0},
            }
            if is_v:
                dprops["w"] = 1
            dn = ViewNode(cls="View", props=dprops)
            _apply_mod_layout_props(dn, mod)
            _cur_parent().add(dn)
            tree.touch()
            return True, None

        # GridCells$Fixed / GridCells$Adaptive
        if "GridCells$Fixed" in cls and name == "<init>":
            if args and isinstance(args[0], JObject):
                args[0].fields["$span_count"] = max(1, int(args[1])) if len(args) > 1 else 2
            return True, None
        if "GridCells$Adaptive" in cls and name == "<init>":
            if args and isinstance(args[0], JObject):
                min_dp = max(60.0, _f32_num(args[1], 120.0) if len(args) > 1 else 120.0)
                args[0].fields["$span_count"] = max(2, int((tree.width - 24) // min_dp))
            return True, None

        # LazyVerticalGrid / LazyHorizontalGrid
        if m_base in ("LazyVerticalGrid", "LazyHorizontalGrid"):
            gc = next((v for _, v in pairs if isinstance(v, JObject) and ("GridCells" in (v.cls or "") or "$span_count" in v.fields)), None)
            span_cnt = int(gc.fields.get("$span_count", 2)) if isinstance(gc, JObject) else 2
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            f1 = next((v for pt, v in pairs if "Function1" in pt and isinstance(v, JObject)), None)
            gbox = ViewNode(
                cls="RecyclerView",
                props={
                    "orientation": "vertical" if "Vertical" in m_base else "horizontal",
                    "span_count": max(1, span_cnt),
                    "role": "lazy_grid",
                },
            )
            _apply_mod_layout_props(gbox, mod)
            _cur_parent().add(gbox)
            if f1 is not None:
                state["stack"].append(gbox)
                try:
                    uw = _unwrap_fn(f1)
                    vm.invoke(uw.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;",
                              (uw, JObject("Landroidx/compose/foundation/lazy/grid/LazyGridScope;")))
                except Exception:
                    pass
                finally:
                    if state["stack"] and state["stack"][-1] is gbox:
                        state["stack"].pop()
            tree.touch()
            return True, None

        if "LazyGridScope" in cls or "LazyGridScope" in rcls:
            _invoke_lazy_fn = helpers["_invoke_lazy_fn"]
            if m_base == "item":
                fn = next((v for pt, v in pairs if ("Function3" in pt or "Function4" in pt) and isinstance(v, JObject)), None)
                if fn is not None:
                    _invoke_lazy_fn(fn, 0)
                    tree.touch()
                return True, None
            if m_base == "items":
                count = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)), 0)
                fn = next((v for pt, v in pairs if ("Function4" in pt or "Function3" in pt) and isinstance(v, JObject)), None)
                if fn is not None:
                    for idx in range(max(0, min(count, 60))):
                        _invoke_lazy_fn(fn, idx)
                    tree.touch()
                return True, None

        # PagerStateKt / PagerState / HorizontalPager / VerticalPager
        if cls.startswith("Landroidx/compose/foundation/pager/"):
            if m_base == "rememberPagerState":
                init_p = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)), 0)
                pc_fn = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
                pc = 3
                if pc_fn is not None:
                    try:
                        uw = _unwrap_fn(pc_fn)
                        rv = vm.invoke(uw.cls, "invoke", "()Ljava/lang/Object;", (uw,))
                        if isinstance(rv, JObject) and "value" in rv.fields:
                            pc = int(rv.fields["value"])
                        elif isinstance(rv, int):
                            pc = int(rv)
                    except Exception:
                        pass
                return True, JObject(
                    "Landroidx/compose/foundation/pager/PagerState;",
                    {"$page": init_p, "$page_count": max(1, pc), "$recomp": (state.get("cur_comp") or {}).get("recomp_fn")},
                )
            if "PagerState" in cls and not static and args and isinstance(args[0], JObject):
                ps = args[0]
                if name == "getCurrentPage":
                    return True, int(ps.fields.get("$page", 0))
                if name == "getPageCount":
                    return True, int(ps.fields.get("$page_count", 1))
                if name in ("scrollToPage", "animateScrollToPage"):
                    target_p = next((int(v) for pt, v in pairs if pt == "I" and isinstance(v, int)), 0)
                    ps.fields["$page"] = max(0, min(int(ps.fields.get("$page_count", 1)) - 1, target_p))
                    _rc = ps.fields.get("$recomp") or (state.get("cur_comp") or {}).get("recomp_fn")
                    if callable(_rc):
                        _rc()
                    tree.touch()
                    return True, None
                if name == "getSettledPage" or name == "getTargetPage":
                    return True, int(ps.fields.get("$page", 0))

            if m_base in ("HorizontalPager", "VerticalPager"):
                ps = next((v for _, v in pairs if isinstance(v, JObject) and "PagerState" in (v.cls or "")), None)
                mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
                fn4 = next((v for pt, v in pairs if ("Function4" in pt or "Function3" in pt) and isinstance(v, JObject)), None)
                cur_p = int(ps.fields.get("$page", 0)) if isinstance(ps, JObject) else 0
                cnt = int(ps.fields.get("$page_count", 1)) if isinstance(ps, JObject) else 1
                pnode = ViewNode(
                    cls="DisableableViewPager",
                    props={"orientation": "vertical", "page": cur_p, "page_count": cnt},
                )
                _apply_mod_layout_props(pnode, mod)
                _cur_parent().add(pnode)
                if fn4 is not None:
                    state["stack"].append(pnode)
                    try:
                        uw = _unwrap_fn(fn4)
                        pscope = JObject("Landroidx/compose/foundation/pager/PagerScope;")
                        for msig, margs in (
                            ("(Ljava/lang/Object;ILandroidx/compose/runtime/Composer;I)Ljava/lang/Object;",
                             (uw, pscope, cur_p, JObject("Landroidx/compose/runtime/Composer;"), 0)),
                            ("(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
                             (uw, pscope, _boxed_int(cur_p), JObject("Landroidx/compose/runtime/Composer;"), _boxed_int(0))),
                        ):
                            try:
                                vm.invoke(uw.cls, "invoke", msig, margs)
                                break
                            except Exception:
                                continue
                    finally:
                        if state["stack"] and state["stack"][-1] is pnode:
                            state["stack"].pop()
                tree.touch()
                return True, None

        # ModalBottomSheet
        if m_base == "ModalBottomSheet":
            on_dismiss = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            f3 = next((v for pt, v in pairs if "Function3" in pt and isinstance(v, JObject)), None)
            f2 = next((v for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
            _rc = (state.get("cur_comp") or {}).get("recomp_fn")
            sheet = ViewNode(
                cls="FrameLayout",
                props={
                    "role": "dialog",
                    "orientation": "vertical",
                    "bg_drawable": {"kind": "shape", "shape": "rectangle", "fill": "#ffffff", "outline": "#cbd5e1", "radius": 14},
                },
            )
            if on_dismiss is not None:
                sheet.props["$on_dismiss"] = lambda _fn=on_dismiss, _rc0=_rc: _compose_click(_fn, _rc0)
            _apply_mod_layout_props(sheet, mod)
            _cur_parent().add(sheet)
            state["stack"].append(sheet)
            try:
                if f3 is not None:
                    _invoke_fn3(f3, JObject("Landroidx/compose/foundation/layout/ColumnScope;"))
                elif f2 is not None:
                    _invoke_fn2(f2)
            finally:
                if state["stack"] and state["stack"][-1] is sheet:
                    state["stack"].pop()
            tree.touch()
            return True, None

        # FilterChip / AssistChip / InputChip / SuggestionChip
        if m_base in ("FilterChip", "AssistChip", "InputChip", "SuggestionChip"):
            b_vals = [bool(v) for pt, v in pairs if pt == "Z"]
            is_sel = b_vals[0] if (m_base in ("FilterChip", "InputChip") and b_vals) else False
            on_click = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            f2 = next((v for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
            lbl, _ = _collect_slot_text(f2, fn3=False) if f2 is not None else ("", [])
            _rc = (state.get("cur_comp") or {}).get("recomp_fn")
            ch_node = ViewNode(
                cls="Button",
                text=lbl or m_base.replace("Chip", ""),
                clickable=True,
                props={
                    "selected": is_sel,
                    "h": 34,
                    "bg_drawable": {
                        "kind": "shape",
                        "shape": "rectangle",
                        "fill": "#dbeafe" if is_sel else "#f1f5f9",
                        "outline": "#2563eb" if is_sel else "#cbd5e1",
                        "radius": 16,
                    },
                    "color": "#1e3a8a" if is_sel else "#111827",
                },
            )
            _apply_mod_layout_props(ch_node, mod)
            if on_click is not None:
                ch_node.on_click = lambda _v=None, _fn=on_click, _rc0=_rc: _compose_click(_fn, _rc0)
            _cur_parent().add(ch_node)
            tree.touch()
            return True, None

        # Slider
        if m_base == "Slider":
            f_vals = [_f32_num(v, 0.0) for pt, v in pairs if pt == "F"]
            cur_v = f_vals[0] if f_vals else 0.0
            on_val = next((v for pt, v in pairs if "Function1" in pt and isinstance(v, JObject)), None)
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            rng_obj = next((v for _, v in pairs if isinstance(v, JObject) and "Range" in (v.cls or "")), None)
            v_from = float(rng_obj.fields.get("start", 0.0)) if rng_obj else 0.0
            v_to = float(rng_obj.fields.get("endInclusive", 1.0)) if rng_obj else 1.0
            span = max(0.01, v_to - v_from)
            prog = int(max(0.0, min(100.0, (cur_v - v_from) * 100.0 / span)))
            _rc = (state.get("cur_comp") or {}).get("recomp_fn")
            sl_node = ViewNode(
                cls="SeekBar",
                clickable=True,
                props={"slider_from": v_from, "slider_to": v_to, "slider_val": cur_v, "progress": prog, "h": 34},
            )
            _apply_mod_layout_props(sl_node, mod)
            if on_val is not None:
                def _on_slider_tap(_v=None, _fn=on_val, _nd=sl_node, _f=v_from, _t=v_to, _rc0=_rc):
                    step = max(0.1, (_t - _f) / 4.0)
                    nxt = float(_nd.props.get("slider_val", _f)) + step
                    if nxt > _t + 1e-6:
                        nxt = _f
                    _nd.props["slider_val"] = nxt
                    _nd.props["progress"] = int(max(0.0, min(100.0, (nxt - _f) * 100.0 / max(0.01, _t - _f))))
                    try:
                        uw = _unwrap_fn(_fn)
                        vm.invoke(uw.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (uw, _boxed_float(nxt)))
                    except Exception:
                        pass
                    if callable(_rc0):
                        _rc0()
                    tree.touch()
                sl_node.on_click = _on_slider_tap
            _cur_parent().add(sl_node)
            tree.touch()
            return True, None

        # RadioButton
        if m_base == "RadioButton":
            b_vals = [bool(v) for pt, v in pairs if pt == "Z"]
            is_sel = b_vals[0] if b_vals else False
            on_click = next((v for pt, v in pairs if "Function0" in pt and isinstance(v, JObject)), None)
            mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
            _rc = (state.get("cur_comp") or {}).get("recomp_fn")
            rb_node = ViewNode(
                cls="RadioButton",
                clickable=True,
                props={"checked": is_sel, "h": 32},
            )
            _apply_mod_layout_props(rb_node, mod)
            if on_click is not None:
                rb_node.on_click = lambda _v=None, _fn=on_click, _rc0=_rc: _compose_click(_fn, _rc0)
            _cur_parent().add(rb_node)
            tree.touch()
            return True, None

    return False, None
