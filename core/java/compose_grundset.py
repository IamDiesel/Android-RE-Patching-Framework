# -*- coding: utf-8 -*-
"""core.java.compose_grundset — G2: Jetpack Compose Modifier, Shapes, Color, Flow & Widgets."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from core.java.objects import JObject

try:
    from core.java.dalvik import DalvikUnknownOpcode, DebugStop, VmBudgetExceeded
    _VM_STOP_EXCS = (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)
except Exception:
    _VM_STOP_EXCS = ()


def _c_pack(max_w: int, max_h: int) -> int:
    w = max(0, min(0x7FFFFFFF, int(max_w)))
    h = max(0, min(0x7FFFFFFF, int(max_h)))
    return ((w & 0xFFFFFFFF) << 32) | (h & 0xFFFFFFFF)


def _c_unpack(val: Any, tree: Any) -> Tuple[int, int]:
    def_w = int(getattr(tree, "width", 420) or 420)
    def_h = int(getattr(tree, "height", 760) or 760)
    if isinstance(val, JObject):
        val = val.fields.get("v", 0)
    try:
        u64 = int(val or 0) & 0xFFFFFFFFFFFFFFFF
    except Exception:
        return def_w, def_h
    if u64 == 0:
        return def_w, def_h
    w = (u64 >> 32) & 0xFFFFFFFF
    h = u64 & 0xFFFFFFFF
    return (w if 0 < w < 0x100000 else def_w), (h if 0 < h < 0x100000 else def_h)


def _ltr_enum(vm: Any) -> JObject:
    try:
        v = vm._sget("Landroidx/compose/ui/unit/LayoutDirection;", "Ltr")
        if isinstance(v, JObject):
            return v
    except Exception:
        pass
    return JObject("Landroidx/compose/ui/unit/LayoutDirection;", {"__ename": "Ltr", "__ordinal": 0})


def _handle_subcompose_and_constraints(
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
    """Behandelt Constraints, SubcomposeLayout, BoxWithConstraints, Measurable, Placeable und MeasureScope."""
    ViewNode = helpers["ViewNode"]
    _args_by_type = helpers["_args_by_type"]
    _f32_num = helpers["_f32_num"]
    _cur_parent = helpers["_cur_parent"]
    _apply_mod_layout_props = helpers["_apply_mod_layout_props"]
    _invoke_fn2 = helpers["_invoke_fn2"]
    _invoke_fn3 = helpers["_invoke_fn3"]
    _unwrap_fn = helpers["_unwrap_fn"]

    if cls in ("Landroidx/compose/ui/unit/Constraints;", "Landroidx/compose/ui/unit/ConstraintsKt;"):
        if name == "<clinit>":
            return True, None
        if name == "box-impl":
            return True, JObject("Landroidx/compose/ui/unit/Constraints;", {"v": int(args[0] or 0) if args else 0})
        if name == "unbox-impl":
            if args and isinstance(args[0], JObject):
                return True, int(args[0].fields.get("v", 0))
            return True, int(args[0] or 0) if args else 0
        if name.startswith(("getMinWidth-impl", "getMinHeight-impl")):
            return True, 0
        if name.startswith("getMaxWidth-impl"):
            mw, _ = _c_unpack(args[0] if args else 0, tree)
            return True, mw
        if name.startswith("getMaxHeight-impl"):
            _, mh = _c_unpack(args[0] if args else 0, tree)
            return True, mh
        if name.startswith(("getHasBounded", "getHasFixed")):
            return True, 1
        if name.startswith("copy"):
            ow, oh = _c_unpack(args[0] if args else 0, tree)
            mask = int(args[5] or 0) if (name.endswith("$default") and len(args) > 5) else 0
            nw = ow if (mask & 2 or len(args) <= 2) else int(args[2] or ow)
            nh = oh if (mask & 8 or len(args) <= 4) else int(args[4] or oh)
            if not (0 < nw < 100_000):
                nw = ow
            if not (0 < nh < 100_000):
                nh = oh
            return True, _c_pack(nw, nh)
        return True, 0

    if cls in ("Landroidx/compose/ui/layout/SubcomposeLayoutState;", "Landroidx/compose/ui/layout/SubcomposeLayoutState$Companion;"):
        if name in ("<clinit>", "<init>", "forceRecomposeChildren$ui") or sig.endswith(")V"):
            return True, None
        if "Function2" in sig:
            return True, JObject("Lkotlin/jvm/functions/Function2;")
        return True, None

    if cls == "Landroidx/compose/ui/layout/SubcomposeLayoutKt;" and name.startswith("SubcomposeLayout"):
        pairs = _args_by_type(sig, static, args)
        mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
        fn2 = next((_unwrap_fn(v) for pt, v in pairs if "Function2" in pt and isinstance(v, JObject)), None)
        sbox = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "SubcomposeLayout"})
        _apply_mod_layout_props(sbox, mod)
        _cur_parent().add(sbox)
        if isinstance(fn2, JObject):
            c_raw = _c_pack(getattr(tree, "width", 420) or 420, getattr(tree, "height", 760) or 760)
            scope = JObject("Landroidx/compose/ui/layout/SubcomposeMeasureScope;", {"$box": sbox})
            c_obj = JObject("Landroidx/compose/ui/unit/Constraints;", {"v": c_raw})
            state["stack"].append(sbox)
            try:
                for call_args in ((fn2, scope, c_obj), (fn2, scope, c_raw)):
                    try:
                        vm.invoke(fn2.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", call_args)
                        break
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        continue
            finally:
                if state["stack"] and state["stack"][-1] is sbox:
                    state["stack"].pop()
        tree.touch()
        return True, None

    if cls == "Landroidx/compose/foundation/layout/BoxWithConstraintsKt;" and name.startswith("BoxWithConstraints"):
        pairs = _args_by_type(sig, static, args)
        mod = next((v for pt, v in pairs if "Modifier" in pt and isinstance(v, JObject)), None)
        fn3 = next((_unwrap_fn(v) for pt, v in pairs if "Function3" in pt and isinstance(v, JObject)), None)
        sbox = ViewNode(cls="LinearLayout", props={"orientation": "vertical", "compose_kind": "Box"})
        _apply_mod_layout_props(sbox, mod)
        _cur_parent().add(sbox)
        if isinstance(fn3, JObject):
            bwc_scope = JObject("Landroidx/compose/foundation/layout/BoxWithConstraintsScopeImpl;", {
                "$w": float(getattr(tree, "width", 420) or 420),
                "$h": float(getattr(tree, "height", 760) or 760),
            })
            state["stack"].append(sbox)
            try:
                _invoke_fn3(fn3, bwc_scope)
            finally:
                if state["stack"] and state["stack"][-1] is sbox:
                    state["stack"].pop()
        tree.touch()
        return True, None

    if cls in (
        "Landroidx/compose/foundation/layout/BoxWithConstraintsScope;",
        "Landroidx/compose/foundation/layout/BoxWithConstraintsScopeImpl;",
    ) or (not static and args and isinstance(args[0], JObject) and "$w" in args[0].fields and "$h" in args[0].fields and "BoxWithConstraintsScope" in (args[0].cls or "")):
        sc = args[0] if args and isinstance(args[0], JObject) else None
        if name.startswith("getMaxWidth"):
            return True, float(sc.fields.get("$w", getattr(tree, "width", 420) or 420)) if sc else 420.0
        if name.startswith("getMaxHeight"):
            return True, float(sc.fields.get("$h", getattr(tree, "height", 760) or 760)) if sc else 760.0
        if name.startswith(("getMinWidth", "getMinHeight")):
            return True, 0.0
        if name.startswith("getConstraints"):
            return True, _c_pack(
                int(sc.fields.get("$w", getattr(tree, "width", 420) or 420)) if sc else 420,
                int(sc.fields.get("$h", getattr(tree, "height", 760) or 760)) if sc else 760,
            )

    if cls in (
        "Landroidx/compose/ui/layout/SubcomposeMeasureScope;",
        "Landroidx/compose/ui/layout/MeasureScope;",
        "Landroidx/compose/ui/layout/IntrinsicMeasureScope;",
    ) or (not static and args and isinstance(args[0], JObject) and "$box" in args[0].fields):
        recv_s = args[0] if args and isinstance(args[0], JObject) else None
        if name == "subcompose" and recv_s is not None and "$box" in recv_s.fields:
            sbox = recv_s.fields["$box"]
            fn2 = args[2] if len(args) > 2 else None
            n0 = len(sbox.children)
            if isinstance(fn2, JObject):
                state["stack"].append(sbox)
                try:
                    _invoke_fn2(fn2)
                finally:
                    if state["stack"] and state["stack"][-1] is sbox:
                        state["stack"].pop()
            added = sbox.children[n0:]
            meas = [JObject("Landroidx/compose/ui/layout/Measurable;", {"$view": ch}) for ch in added]
            return True, JObject("Ljava/util/ArrayList;", {"__items": meas, "$items": list(meas)})
        if name == "getLayoutDirection":
            return True, _ltr_enum(vm)
        if name in ("getDensity", "getFontScale"):
            return True, 1.0
        if name.startswith("toPx"):
            return True, _f32_num(args[1] if len(args) > 1 else 0.0, 0.0)
        if name.startswith("roundToPx"):
            return True, int(round(_f32_num(args[1] if len(args) > 1 else 0.0, 0.0)))
        if name.startswith("layout"):
            return True, JObject("Landroidx/compose/ui/layout/MeasureResult;")

    if cls in ("Landroidx/compose/ui/layout/Measurable;", "Landroidx/compose/ui/layout/IntrinsicMeasurable;"):
        if name.startswith("measure") and args and isinstance(args[0], JObject):
            ch = args[0].fields.get("$view")
            pw = int((ch.props.get("w") if ch is not None else None) or 56)
            ph = int((ch.props.get("h") if ch is not None else None) or 56)
            return True, JObject("Landroidx/compose/ui/layout/Placeable;", {"$w": pw, "$h": ph, "$view": ch})
        if name == "getParentData":
            return True, None

    if cls == "Landroidx/compose/ui/layout/Placeable;" or (
        not static and args and isinstance(args[0], JObject) and (args[0].cls or "") == "Landroidx/compose/ui/layout/Placeable;"
    ):
        po = args[0] if args and isinstance(args[0], JObject) else None
        if name in ("getWidth", "getMeasuredWidth"):
            return True, int(po.fields.get("$w", 56)) if po is not None else 56
        if name in ("getHeight", "getMeasuredHeight"):
            return True, int(po.fields.get("$h", 56)) if po is not None else 56
        if name.startswith("place"):
            return True, None

    if cls.startswith("Landroidx/compose/ui/layout/Placeable$PlacementScope"):
        return True, None

    if cls == "Landroidx/compose/ui/layout/MeasureResult;":
        if name == "getWidth":
            return True, int(getattr(tree, "width", 420) or 420)
        if name == "getHeight":
            return True, int(getattr(tree, "height", 760) or 760)
        if name == "getAlignmentLines":
            return True, JObject("Ljava/util/HashMap;", {"map": {}})
        return True, None

    return False, None


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
    if f.get("$fill_max_w"):
        vn.props["$fill_max_w"] = True
    if "$box_align" in f:
        vn.props["$box_align"] = f["$box_align"]
    if "$h" in f:
        vn.props["h"] = f["$h"]
    if "$w" in f:
        vn.props["w"] = f["$w"]
    if "$aspect_ratio" in f:
        vn.props["$aspect_ratio"] = float(f["$aspect_ratio"])
        vn.props["$cl_ratio"] = str(f["$aspect_ratio"])
        if "$h" not in f:
            vn.props.pop("h", None)
    for pk in ("$pad_l", "$pad_t", "$pad_r", "$pad_b"):
        if pk in f:
            vn.props[pk] = int(f[pk])
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

    # 1b) SubcomposeLayout, BoxWithConstraints, Constraints, Measurable, Placeable
    sc_ok, sc_val = _handle_subcompose_and_constraints(
        cls, rcls, name, sig, args, static, vm, state, tree, helpers
    )
    if sc_ok:
        return True, sc_val

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
            pl = int(fields.get("$pad_l", fields.get("$pad_x", 0)) or 0)
            pt_v = int(fields.get("$pad_t", fields.get("$pad_y", 0)) or 0)
            pr = int(fields.get("$pad_r", fields.get("$pad_x", 0)) or 0)
            pb = int(fields.get("$pad_b", fields.get("$pad_y", 0)) or 0)
            if len(f_vals) >= 4:
                pl = max(0, min(180, pl + max(0, int(round(f_vals[0])))))
                pt_v = max(0, min(180, pt_v + max(0, int(round(f_vals[1])))))
                pr = max(0, min(180, pr + max(0, int(round(f_vals[2])))))
                pb = max(0, min(180, pb + max(0, int(round(f_vals[3])))))
            elif len(f_vals) >= 2:
                dx = max(0, int(round(f_vals[0])))
                dy = max(0, int(round(f_vals[1])))
                pl = max(0, min(180, pl + dx))
                pr = max(0, min(180, pr + dx))
                pt_v = max(0, min(180, pt_v + dy))
                pb = max(0, min(180, pb + dy))
            elif len(f_vals) == 1:
                pv = max(0, int(round(f_vals[0])))
                pl = max(0, min(180, pl + pv))
                pr = max(0, min(180, pr + pv))
                pt_v = max(0, min(180, pt_v + pv))
                pb = max(0, min(180, pb + pv))
            fields["$pad_l"] = pl
            fields["$pad_t"] = pt_v
            fields["$pad_r"] = pr
            fields["$pad_b"] = pb
            fields["$pad_x"] = (pl + pr) // 2
            fields["$pad_y"] = (pt_v + pb) // 2
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
            if "$val" in flow_obj.fields:
                cur_val = flow_obj.fields.get("$val")
            elif "$state_val" in flow_obj.fields:
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
        return True, JObject("Landroidx/compose/runtime/MutableState;", {"$val": cur_val, "$state_val": cur_val, "$source_flow": flow_obj})

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
