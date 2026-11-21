# -*- coding: utf-8 -*-
"""core.java.nav_rx_paging_g8 — G8: Jetpack Navigation (XML & Compose), Paging 3 & RxJava 1/2/3."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from core.java.objects import JObject, JString

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APP_NS = "{http://schemas.android.com/apk/res-auto}"


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _unwrap_fn(fn_obj: Any) -> Any:
    if isinstance(fn_obj, JObject) and "$block" in fn_obj.fields:
        return fn_obj.fields["$block"]
    return fn_obj


def parse_nav_graph_xml(app_root: str, graph_name: str) -> Dict[str, Any]:
    """Parst `res/navigation/<graph_name>.xml` in StartDestination, Destinations & Actions."""
    if not app_root or not graph_name:
        return {}
    clean = str(graph_name).split("/")[-1]
    if not clean.endswith(".xml"):
        clean += ".xml"
    p = os.path.join(app_root, "res", "navigation", clean)
    if not os.path.isfile(p):
        return {}
    try:
        root = ET.parse(p).getroot()
    except Exception:
        return {}
    start_id = (root.get(APP_NS + "startDestination") or "").split("/")[-1]
    dests: Dict[str, Dict[str, Any]] = {}
    for ch in root:
        did = (ch.get(ANDROID_NS + "id") or "").split("/")[-1]
        if not did:
            continue
        actions = {}
        for act in ch.findall("action"):
            aid = (act.get(ANDROID_NS + "id") or "").split("/")[-1]
            dst = (act.get(APP_NS + "destination") or "").split("/")[-1]
            if aid:
                actions[aid] = dst
        dests[did] = {
            "id": did,
            "tag": ch.tag,
            "name": ch.get(ANDROID_NS + "name") or "",
            "label": ch.get(ANDROID_NS + "label") or "",
            "actions": actions,
        }
    return {"start": start_id, "destinations": dests}


def _match_route_template(tmpl: str, route: str) -> Optional[Dict[str, str]]:
    if tmpl == route:
        return {}
    t_parts = tmpl.split("/")
    r_parts = route.split("/")
    if len(t_parts) != len(r_parts):
        return None
    params: Dict[str, str] = {}
    for tp, rp in zip(t_parts, r_parts):
        if tp.startswith("{") and tp.endswith("}"):
            params[tp[1:-1]] = rp
        elif tp != rp:
            return None
    return params


def _bundle_from_params(params: Dict[str, str]) -> JObject:
    m = {str(k): JString(str(v)) for k, v in (params or {}).items()}
    return JObject("Landroid/os/Bundle;", {"map": m})


def _make_nav_ctrl(stubs: Any, start_dest: str = "start") -> JObject:
    NC = "Landroidx/navigation/NavHostController;"
    entry = JObject("Landroidx/navigation/NavBackStackEntry;", {
        "_route": start_dest,
        "_args": _bundle_from_params({}),
    })
    nc = JObject(NC, {
        "_back_stack": [entry],
        "_routes": {},
        "_current_route": start_dest,
        "_graph": {},
    })
    stubs._compose_nav_controller = nc
    return nc


def pop_compose_nav_if_possible(stubs: Any) -> bool:
    """Wird von LifecycleRunner.handle_back() vor Activity.onBackPressed() geprueft."""
    nc = getattr(stubs, "_compose_nav_controller", None)
    if not isinstance(nc, JObject):
        return False
    bs = nc.fields.get("_back_stack")
    if isinstance(bs, list) and len(bs) > 1:
        bs.pop()
        nc.fields["_current_route"] = bs[-1].fields.get("_route", "")
        tr = getattr(stubs, "view_tree", None)
        rc = getattr(tr, "_compose_recompose", None) if tr is not None else None
        if callable(rc):
            rc()
        elif tr is not None:
            tr.touch()
        return True
    return False


def install_g8_stubs(stubs: Any) -> None:
    """Registriert Navigation-, Paging 3- und RxJava 1/2/3-Stubs additiv (DEX behaelt Vorrang!)."""
    if stubs is None or getattr(stubs, "_g8_installed", False):
        return
    stubs._g8_installed = True

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # ---- 1. NavController & NavBackStackEntry -------------------------------
    for _nc in ("Landroidx/navigation/NavController;", "Landroidx/navigation/NavHostController;"):
        def _nav_to(a):
            if len(a) < 2 or not isinstance(a[0], JObject):
                return None
            nc = a[0]
            if isinstance(a[1], int):
                rid = a[1]
                graph = nc.fields.get("_graph") or {}
                cur_id = nc.fields.get("_current_route", graph.get("start", ""))
                dests = graph.get("destinations") or {}
                id_name = (getattr(stubs, "_idmap", None) or {}).get(rid, str(rid))
                nxt = (dests.get(cur_id, {}).get("actions", {}).get(id_name)) or id_name
                route = nxt
            else:
                route = _txt(a[1])
            matched_args: Dict[str, str] = {}
            for tmpl in (nc.fields.get("_routes") or {}):
                mp = _match_route_template(tmpl, route)
                if mp is not None:
                    matched_args = mp
                    break
            entry = JObject("Landroidx/navigation/NavBackStackEntry;", {"_route": route, "_args": _bundle_from_params(matched_args)})
            nc.fields.setdefault("_back_stack", []).append(entry)
            nc.fields["_current_route"] = route
            tr = getattr(stubs, "view_tree", None)
            rc = getattr(tr, "_compose_recompose", None) if tr is not None else None
            if callable(rc):
                rc()
            elif tr is not None:
                tr.touch()
            return None

        _add(_nc, "navigate", "(Ljava/lang/String;)V", _nav_to)
        _add(_nc, "navigate", "(Ljava/lang/String;Lkotlin/jvm/functions/Function1;)V", _nav_to)
        _add(_nc, "navigate", "(Ljava/lang/String;Landroidx/navigation/NavOptions;Landroidx/navigation/Navigator$Extras;)V", _nav_to)
        _add(_nc, "navigate", "(I)V", _nav_to)
        _add(_nc, "navigate", "(ILandroid/os/Bundle;)V", _nav_to)
        _add(_nc, "popBackStack", "()Z", lambda a: 1 if pop_compose_nav_if_possible(stubs) else 0)
        _add(_nc, "navigateUp", "()Z", lambda a: 1 if pop_compose_nav_if_possible(stubs) else 0)
        _add(_nc, "getCurrentBackStackEntry", "()Landroidx/navigation/NavBackStackEntry;",
             lambda a: (a[0].fields.get("_back_stack") or [None])[-1] if a and isinstance(a[0], JObject) else None)
        _add(_nc, "getPreviousBackStackEntry", "()Landroidx/navigation/NavBackStackEntry;",
             lambda a: (lambda bs: bs[-2] if len(bs) >= 2 else None)(a[0].fields.get("_back_stack") or []) if a and isinstance(a[0], JObject) else None)

    NBE = "Landroidx/navigation/NavBackStackEntry;"
    _add(NBE, "getArguments", "()Landroid/os/Bundle;", lambda a: a[0].fields.get("_args") if a and isinstance(a[0], JObject) else _bundle_from_params({}))
    _add(NBE, "getDestination", "()Landroidx/navigation/NavDestination;",
         lambda a: JObject("Landroidx/navigation/NavDestination;", {"_route": a[0].fields.get("_route", "")}) if a and isinstance(a[0], JObject) else None)
    _add("Landroidx/navigation/NavDestination;", "getRoute", "()Ljava/lang/String;",
         lambda a: JString(a[0].fields.get("_route", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add("Landroidx/navigation/Navigation;", "findNavController", "(Landroid/app/Activity;I)Landroidx/navigation/NavController;",
         lambda a: getattr(stubs, "_compose_nav_controller", None) or _make_nav_ctrl(stubs))
    _add("Landroidx/navigation/Navigation;", "findNavController", "(Landroid/view/View;)Landroidx/navigation/NavController;",
         lambda a: getattr(stubs, "_compose_nav_controller", None) or _make_nav_ctrl(stubs))
    _add("Landroidx/navigation/fragment/FragmentKt;", "findNavController", "(Landroidx/fragment/app/Fragment;)Landroidx/navigation/NavController;",
         lambda a: getattr(stubs, "_compose_nav_controller", None) or _make_nav_ctrl(stubs))

    # ---- 2. Paging 3 --------------------------------------------------------
    PD = "Landroidx/paging/PagingData;"
    LPI = "Landroidx/paging/compose/LazyPagingItems;"
    _add(PD, "from", f"(Ljava/util/List;){PD}", lambda a: JObject(PD, {"__items": list((a[0].fields.get("__items") if a and isinstance(a[0], JObject) else ()) or ())}))
    _add(PD, "empty", f"(){PD}", lambda a: JObject(PD, {"__items": []}))
    _add(LPI, "getItemCount", "()I", lambda a: len(a[0].fields.get("__items") or ()) if a and isinstance(a[0], JObject) else 0)
    _add(LPI, "get", "(I)Ljava/lang/Object;",
         lambda a: (lambda lst: lst[int(a[1])] if 0 <= int(a[1]) < len(lst) else None)(a[0].fields.get("__items") or ()) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LPI, "peek", "(I)Ljava/lang/Object;",
         lambda a: (lambda lst: lst[int(a[1])] if 0 <= int(a[1]) < len(lst) else None)(a[0].fields.get("__items") or ()) if len(a) > 1 and isinstance(a[0], JObject) else None)

    # ---- 3. RxJava 1 / 2 / 3 (via stubs.register, damit echte DEX-Klassen Vorrang haben)
    def _rx_eval(src_obj: Any, vm: Any):
        if not isinstance(src_obj, JObject):
            return []
        vals = src_obj.fields.get("_rx_vals")
        if vals is not None:
            vals = list(vals)
        else:
            cb = src_obj.fields.get("_rx_callable")
            if isinstance(cb, JObject) and vm is not None:
                for mnm in ("call", "invoke"):
                    try:
                        res = vm.invoke_virtual(cb, mnm, "()Ljava/lang/Object;", (cb,))
                        vals = [res]
                        break
                    except Exception:
                        continue
        if vals is None:
            vals = []
        for kind, fn_obj in (src_obj.fields.get("_rx_ops") or ()):
            fn = _unwrap_fn(fn_obj)
            if not isinstance(fn, JObject) or vm is None:
                continue
            nxt_vals = []
            for item in vals:
                res = None
                for msig in ("apply", "call", "invoke"):
                    try:
                        res = vm.invoke_virtual(fn, msig, "(Ljava/lang/Object;)Ljava/lang/Object;", (fn, item))
                        break
                    except Exception:
                        res = None
                        continue
                if kind == "map":
                    nxt_vals.append(res)
                elif kind == "flatMap":
                    nxt_vals.extend(_rx_eval(res, vm))
                elif kind == "filter":
                    ok_f = bool(res.fields.get("value")) if isinstance(res, JObject) and "value" in res.fields else bool(res)
                    if ok_f:
                        nxt_vals.append(item)
            vals = nxt_vals
        return vals

    for _pkg in ("Lio/reactivex/rxjava3/core", "Lio/reactivex", "Lrx"):
        cd_cls = ("Lio/reactivex/rxjava3/disposables/CompositeDisposable;"
                  if "rxjava3" in _pkg else ("Lio/reactivex/disposables/CompositeDisposable;" if _pkg == "Lio/reactivex" else "Lrx/subscriptions/CompositeSubscription;"))
        d_cls = ("Lio/reactivex/rxjava3/disposables/Disposable;"
                 if "rxjava3" in _pkg else ("Lio/reactivex/disposables/Disposable;" if _pkg == "Lio/reactivex" else "Lrx/Subscription;"))
        _add(cd_cls, "<init>", "()V", lambda a: a[0].fields.setdefault("_disposed", False) if a else None)
        _add(cd_cls, "add", f"({d_cls})Z", lambda a: 1)
        _add(cd_cls, "clear", "()V", lambda a: None)
        _add(cd_cls, "dispose", "()V", lambda a: None)
        _add(cd_cls, "unsubscribe", "()V", lambda a: None)

        sch_cls = f"{_pkg}/schedulers/Schedulers;" if _pkg == "Lrx" else f"{_pkg.rsplit('/core', 1)[0]}/schedulers/Schedulers;"
        tramp = JObject(f"{_pkg}/Scheduler;", {})
        for sm in ("io", "computation", "newThread", "single", "trampoline", "immediate", "mainThread"):
            _add(sch_cls, sm, f"(){_pkg}/Scheduler;", lambda a, t=tramp: t)
            _add("Lio/reactivex/rxjava3/android/schedulers/AndroidSchedulers;", sm, "()Lio/reactivex/rxjava3/core/Scheduler;", lambda a, t=tramp: t)
            _add("Lio/reactivex/android/schedulers/AndroidSchedulers;", sm, "()Lio/reactivex/Scheduler;", lambda a, t=tramp: t)
            _add("Lrx/android/schedulers/AndroidSchedulers;", sm, "()Lrx/Scheduler;", lambda a, t=tramp: t)

        for _kind in ("Single", "Observable", "Flowable", "Maybe", "Completable"):
            cls_d = f"{_pkg}/{_kind};"
            _add(cls_d, "just", f"(Ljava/lang/Object;){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_vals": [a[0] if a else None], "_rx_ops": []}))
            _add(cls_d, "fromCallable", f"(Ljava/util/concurrent/Callable;){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_callable": a[0] if a else None, "_rx_ops": []}))
            _add(cls_d, "empty", f"(){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_vals": [], "_rx_ops": []}))

            def _rx_op(a, opk: str, c=cls_d):
                base = a[0] if a and isinstance(a[0], JObject) else None
                ops = list(base.fields.get("_rx_ops") or ()) if base else []
                ops.append((opk, a[1] if len(a) > 1 else None))
                return JObject(c, {"_rx_vals": base.fields.get("_rx_vals") if base else None, "_rx_callable": base.fields.get("_rx_callable") if base else None, "_rx_ops": ops})

            for _fn_cls in ("Lio/reactivex/rxjava3/functions/Function;", "Lio/reactivex/functions/Function;", "Lrx/functions/Func1;", "Lkotlin/jvm/functions/Function1;"):
                _add(cls_d, "map", f"({_fn_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "map", c))
                _add(cls_d, "flatMap", f"({_fn_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "flatMap", c))
            for _pr_cls in ("Lio/reactivex/rxjava3/functions/Predicate;", "Lio/reactivex/functions/Predicate;", "Lrx/functions/Func1;", "Lkotlin/jvm/functions/Function1;"):
                _add(cls_d, "filter", f"({_pr_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "filter", c))
            _add(cls_d, "subscribeOn", f"({_pkg}/Scheduler;){cls_d}", lambda a: a[0] if a else None)
            _add(cls_d, "observeOn", f"({_pkg}/Scheduler;){cls_d}", lambda a: a[0] if a else None)

            def _rx_sub(a, d=d_cls):
                vm_cur = getattr(stubs, "vm", None)
                vals = _rx_eval(a[0] if a else None, vm_cur)
                cb = a[1] if len(a) > 1 else None
                if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                    fn = _unwrap_fn(cb)
                    for item in vals:
                        for mnm in ("accept", "onSuccess", "onNext", "call", "invoke"):
                            try:
                                vm_cur.invoke_virtual(fn, mnm, "(Ljava/lang/Object;)V", (fn, item))
                                break
                            except Exception:
                                try:
                                    vm_cur.invoke_virtual(fn, mnm, "(Ljava/lang/Object;)Ljava/lang/Object;", (fn, item))
                                    break
                                except Exception:
                                    continue
                return JObject(d, {})

            for _cs_sig in (
                "(Lio/reactivex/rxjava3/functions/Consumer;)",
                "(Lio/reactivex/rxjava3/functions/Consumer;Lio/reactivex/rxjava3/functions/Consumer;)",
                "(Lio/reactivex/functions/Consumer;)",
                "(Lio/reactivex/functions/Consumer;Lio/reactivex/functions/Consumer;)",
                "(Lrx/functions/Action1;)",
                "(Lrx/functions/Action1;Lrx/functions/Action1;)",
                "(Lkotlin/jvm/functions/Function1;)",
            ):
                _add(cls_d, "subscribe", f"{_cs_sig}{d_cls}", _rx_sub)
            _add(cls_d, "subscribe", f"(){d_cls}", _rx_sub)
            _add(cls_d, "blockingGet", "()Ljava/lang/Object;", lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0] if a else None, getattr(stubs, "vm", None))))
            _add(cls_d, "blockingFirst", "()Ljava/lang/Object;", lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0] if a else None, getattr(stubs, "vm", None))))


def attach_g8_vm_hook(stubs: Any, vm: Any) -> None:
    """Haengt Compose-NavHost / rememberNavController / collectAsLazyPagingItems Hook ein."""
    if vm is None or getattr(vm, "_g8_hook_attached", False):
        return
    vm._g8_hook_attached = True

    def _g8_hook(rcls, cls, name, sig, args, static, _vm):
        if cls.startswith("Landroidx/navigation/compose/NavHostControllerKt") and name == "rememberNavController":
            nc = getattr(stubs, "_compose_nav_controller", None)
            if not isinstance(nc, JObject):
                nc = _make_nav_ctrl(stubs)
            return True, nc

        if cls.startswith("Landroidx/navigation/compose/NavHostKt") and name == "NavHost":
            nc = next((a for a in args if isinstance(a, JObject) and "_back_stack" in a.fields), None)
            if nc is None:
                nc = getattr(stubs, "_compose_nav_controller", None) or _make_nav_ctrl(stubs)
            start_dest = next((_txt(a) for a in args if isinstance(a, JString)), "")
            if start_dest and nc.fields.get("_current_route") == "start":
                nc.fields["_current_route"] = start_dest
                if nc.fields.get("_back_stack"):
                    nc.fields["_back_stack"][0].fields["_route"] = start_dest
            builder_fn = next((_unwrap_fn(a) for a in reversed(args) if isinstance(a, JObject) and ("Lambda" in a.cls or "Function1" in a.cls)), None)
            ngb = JObject("Landroidx/navigation/NavGraphBuilder;", {"_routes": nc.fields.setdefault("_routes", {})})
            if isinstance(builder_fn, JObject):
                try:
                    _vm.invoke(builder_fn.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (builder_fn, ngb))
                except Exception:
                    pass
            cur_route = nc.fields.get("_current_route") or start_dest
            routes_map = nc.fields.get("_routes") or {}
            comp_fn = None
            matched_params: Dict[str, str] = {}
            if cur_route in routes_map:
                comp_fn = routes_map[cur_route]
            else:
                for tmpl, cf in routes_map.items():
                    mp = _match_route_template(tmpl, cur_route)
                    if mp is not None:
                        comp_fn = cf
                        matched_params = mp
                        break
            if isinstance(comp_fn, JObject):
                entry = (nc.fields.get("_back_stack") or [None])[-1]
                if isinstance(entry, JObject):
                    entry.fields["_args"] = _bundle_from_params(matched_params)
                else:
                    entry = JObject("Landroidx/navigation/NavBackStackEntry;", {"_route": cur_route, "_args": _bundle_from_params(matched_params)})
                comp = JObject("Landroidx/compose/runtime/ComposerImpl;")
                scope = JObject("Landroidx/compose/animation/AnimatedContentScope;")
                box_i = JObject("Ljava/lang/Integer;", {"value": 0})
                fn = _unwrap_fn(comp_fn)
                for _c_sig, _c_args in (
                    ("(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (fn, scope, entry, comp, box_i)),
                    ("(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (fn, entry, comp, box_i)),
                ):
                    try:
                        _vm.invoke(fn.cls, "invoke", _c_sig, _c_args)
                        break
                    except Exception:
                        continue
            return True, None

        if cls.startswith("Landroidx/navigation/compose/NavGraphBuilderKt") and name == "composable":
            ngb = next((a for a in args if isinstance(a, JObject) and "_routes" in a.fields), None)
            route = next((_txt(a) for a in args if isinstance(a, JString)), "")
            content = next((a for a in reversed(args) if isinstance(a, JObject) and ("Lambda" in a.cls or "Function" in a.cls)), None)
            if ngb is not None and route and content is not None:
                ngb.fields["_routes"][route] = content
            return True, None

        if cls.startswith("Landroidx/paging/compose/LazyPagingItemsKt") and name == "collectAsLazyPagingItems":
            flow = args[0] if args else None
            items = list(flow.fields.get("__items") or ()) if isinstance(flow, JObject) else []
            return True, JObject("Landroidx/paging/compose/LazyPagingItems;", {"__items": items})

        return False, None

    vm._method_hooks.append(_g8_hook)
