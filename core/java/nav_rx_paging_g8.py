# -*- coding: utf-8 -*-
"""core.java.nav_rx_paging_g8 — G8: Jetpack Navigation (XML & Compose), Paging 3 & RxJava 1/2/3."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from core.java.objects import JObject, JString

try:
    from core.java.dalvik import DalvikUnknownOpcode, DebugStop, VmBudgetExceeded
    _VM_STOP_EXCS = (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)
except Exception:
    _VM_STOP_EXCS = ()


def _sig_param_types_g8(sig: str) -> List[str]:
    try:
        inner = sig[sig.index("(") + 1:sig.index(")")]
    except ValueError:
        return []
    out: List[str] = []
    i = 0
    while i < len(inner):
        st = i
        while i < len(inner) and inner[i] == "[":
            i += 1
        if i < len(inner) and inner[i] == "L":
            end = inner.index(";", i) + 1
            out.append(inner[st:end])
            i = end
        else:
            out.append(inner[st:i + 1])
            i += 1
    return out

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
    def _invoke_rx_cb(vm: Any, fn: Any, candidates: tuple[tuple[str, str], ...], args_fn) -> tuple[bool, Any]:
        if not isinstance(fn, JObject) or vm is None:
            return False, None
        rcls = fn.cls
        resolve_m = getattr(vm, "_resolve_method", None)
        if callable(resolve_m):
            for mnm, msig in candidates:
                if resolve_m(rcls, rcls, mnm, msig) is not None:
                    try:
                        return True, vm.invoke_virtual(fn, mnm, msig, args_fn(fn))
                    except Exception as exc:
                        if type(exc).__name__ == "DebugStop":
                            raise
                        return False, None
        hooks = getattr(vm, "_method_hooks", None)
        if hooks:
            call_args = args_fn(fn)
            for mnm, msig in candidates:
                for hk in list(hooks):
                    try:
                        handled, ret = hk(rcls, rcls, mnm, msig, call_args, False, vm)
                    except Exception as exc:
                        if type(exc).__name__ == "DebugStop":
                            raise
                        handled, ret = False, None
                    if handled:
                        return True, ret
        if hasattr(vm, "invoke_virtual"):
            mnm, msig = candidates[0]
            try:
                return True, vm.invoke_virtual(fn, mnm, msig, args_fn(fn))
            except Exception as exc:
                if type(exc).__name__ == "DebugStop":
                    raise
        return False, None

    def _rx_eval(src_obj: Any, vm: Any):
        if not isinstance(src_obj, JObject):
            return []
        if src_obj.fields.get("_rx_err") is not None:
            return []
        vals = src_obj.fields.get("_rx_vals")
        if vals is not None:
            vals = list(vals)
        else:
            cb = src_obj.fields.get("_rx_callable")
            if isinstance(cb, JObject) and vm is not None:
                ok, res = _invoke_rx_cb(
                    vm,
                    _unwrap_fn(cb),
                    (("call", "()Ljava/lang/Object;"), ("invoke", "()Ljava/lang/Object;")),
                    lambda o: (o,),
                )
                if ok:
                    vals = [res]
            elif isinstance(src_obj.fields.get("_rx_action0"), JObject) and vm is not None:
                _invoke_rx_cb(
                    vm,
                    _unwrap_fn(src_obj.fields.get("_rx_action0")),
                    (("call", "()V"), ("run", "()V"), ("invoke", "()Ljava/lang/Object;")),
                    lambda o: (o,),
                )
                vals = []
        if vals is None:
            vals = []
        for kind, fn_obj in (src_obj.fields.get("_rx_ops") or ()):
            fn = _unwrap_fn(fn_obj)
            if not isinstance(fn, JObject) or vm is None:
                continue
            nxt_vals = []
            for item in vals:
                _ok, res = _invoke_rx_cb(
                    vm,
                    fn,
                    (
                        ("apply", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                        ("call", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                        ("invoke", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                    ),
                    lambda o, it=item: (o, it),
                )
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

    def _rx_convert(src: Any, target_cls: str) -> JObject:
        if isinstance(src, JObject):
            return JObject(target_cls, {
                "_rx_vals": list(src.fields["_rx_vals"]) if src.fields.get("_rx_vals") is not None else None,
                "_rx_callable": src.fields.get("_rx_callable"),
                "_rx_action0": src.fields.get("_rx_action0"),
                "_rx_err": src.fields.get("_rx_err"),
                "_rx_ops": list(src.fields.get("_rx_ops") or ()),
            })
        return JObject(target_cls, {"_rx_vals": [], "_rx_ops": []})

    def _rx_op(a, opk: str, c: str) -> JObject:
        base = a[0] if a and isinstance(a[0], JObject) else None
        ops = list(base.fields.get("_rx_ops") or ()) if base else []
        ops.append((opk, a[1] if len(a) > 1 else None))
        return JObject(c, {
            "_rx_vals": base.fields.get("_rx_vals") if base else None,
            "_rx_callable": base.fields.get("_rx_callable") if base else None,
            "_rx_action0": base.fields.get("_rx_action0") if base else None,
            "_rx_err": base.fields.get("_rx_err") if base else None,
            "_rx_ops": ops,
        })

    def _rx_sub(a, d: str, action0: bool = False, ret_void: bool = False):
        vm_cur = getattr(stubs, "vm", None)
        src = a[0] if a else None
        vals = _rx_eval(src, vm_cur)
        err_obj = src.fields.get("_rx_err") if isinstance(src, JObject) else None
        cb = a[1] if len(a) > 1 else None
        err_cb = a[2] if len(a) > 2 else None
        if err_obj is not None:
            if isinstance(err_cb, JObject) and vm_cur is not None:
                _invoke_rx_cb(
                    vm_cur,
                    _unwrap_fn(err_cb),
                    (
                        ("accept", "(Ljava/lang/Object;)V"),
                        ("onError", "(Ljava/lang/Throwable;)V"),
                        ("call", "(Ljava/lang/Object;)V"),
                        ("invoke", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                    ),
                    lambda o, er=err_obj: (o, er),
                )
            elif isinstance(cb, JObject) and vm_cur is not None:
                fn_cb = _unwrap_fn(cb)
                rm = getattr(vm_cur, "_resolve_method", None)
                if callable(rm) and rm(fn_cb.cls, fn_cb.cls, "onError", "(Ljava/lang/Throwable;)V") is not None:
                    _invoke_rx_cb(vm_cur, fn_cb, (("onError", "(Ljava/lang/Throwable;)V"),), lambda o, er=err_obj: (o, er))
        elif isinstance(cb, JObject) and vm_cur is not None:
            fn = _unwrap_fn(cb)
            if action0:
                _invoke_rx_cb(
                    vm_cur,
                    fn,
                    (("call", "()V"), ("onCompleted", "()V"), ("run", "()V"), ("invoke", "()Ljava/lang/Object;")),
                    lambda o: (o,),
                )
            else:
                cands = (
                    ("accept", "(Ljava/lang/Object;)V"),
                    ("accept", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                    ("onSuccess", "(Ljava/lang/Object;)V"),
                    ("onNext", "(Ljava/lang/Object;)V"),
                    ("call", "(Ljava/lang/Object;)V"),
                    ("call", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                    ("invoke", "(Ljava/lang/Object;)Ljava/lang/Object;"),
                    ("invoke", "(Ljava/lang/Object;)V"),
                )
                for item in vals:
                    _invoke_rx_cb(vm_cur, fn, cands, lambda o, it=item: (o, it))
                rm = getattr(vm_cur, "_resolve_method", None)
                if callable(rm) and rm(fn.cls, fn.cls, "onCompleted", "()V") is not None:
                    _invoke_rx_cb(vm_cur, fn, (("onCompleted", "()V"),), lambda o: (o,))
        if ret_void:
            return None
        return JObject(d, {"_unsubscribed": False} if d == "Lrx/Subscription;" else {})

    for _pkg in ("Lio/reactivex/rxjava3/core", "Lio/reactivex", "Lrx"):
        cd_cls = ("Lio/reactivex/rxjava3/disposables/CompositeDisposable;"
                  if "rxjava3" in _pkg else ("Lio/reactivex/disposables/CompositeDisposable;" if _pkg == "Lio/reactivex" else "Lrx/subscriptions/CompositeSubscription;"))
        d_cls = ("Lio/reactivex/rxjava3/disposables/Disposable;"
                 if "rxjava3" in _pkg else ("Lio/reactivex/disposables/Disposable;" if _pkg == "Lio/reactivex" else "Lrx/Subscription;"))
        _add(cd_cls, "<init>", "()V", lambda a: a[0].fields.setdefault("_disposed", False) if a else None)
        _add(cd_cls, "add", f"({d_cls})Z", lambda a: 1)
        _add(cd_cls, "clear", "()V", lambda a: None)
        _add(cd_cls, "dispose", "()V", lambda a: a[0].fields.__setitem__("_disposed", True) if a and isinstance(a[0], JObject) else None)
        _add(cd_cls, "unsubscribe", "()V", lambda a: a[0].fields.__setitem__("_disposed", True) if a and isinstance(a[0], JObject) else None)

        sch_cls = f"{_pkg}/schedulers/Schedulers;" if _pkg == "Lrx" else f"{_pkg.rsplit('/core', 1)[0]}/schedulers/Schedulers;"
        tramp = JObject(f"{_pkg}/Scheduler;", {})
        for sm in ("io", "computation", "newThread", "single", "trampoline", "immediate", "mainThread"):
            _add(sch_cls, sm, f"(){_pkg}/Scheduler;", lambda a, t=tramp: t)
            if _pkg == "Lio/reactivex/rxjava3/core":
                _add("Lio/reactivex/rxjava3/android/schedulers/AndroidSchedulers;", sm, "()Lio/reactivex/rxjava3/core/Scheduler;", lambda a, t=tramp: t)
            elif _pkg == "Lio/reactivex":
                _add("Lio/reactivex/android/schedulers/AndroidSchedulers;", sm, "()Lio/reactivex/Scheduler;", lambda a, t=tramp: t)
            else:
                _add("Lrx/android/schedulers/AndroidSchedulers;", sm, "()Lrx/Scheduler;", lambda a, t=tramp: t)

        for _kind in ("Single", "Observable", "Flowable", "Maybe", "Completable"):
            cls_d = f"{_pkg}/{_kind};"
            _add(cls_d, "just", f"(Ljava/lang/Object;){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_vals": [a[0] if a else None], "_rx_ops": []}))
            _add(cls_d, "fromCallable", f"(Ljava/util/concurrent/Callable;){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_callable": a[0] if a else None, "_rx_ops": []}))
            _add(cls_d, "empty", f"(){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_vals": [], "_rx_ops": []}))
            _add(cls_d, "error", f"(Ljava/lang/Throwable;){cls_d}", lambda a, c=cls_d: JObject(c, {"_rx_vals": [], "_rx_err": a[0] if a else None, "_rx_ops": []}))

            for _fn_cls in ("Lio/reactivex/rxjava3/functions/Function;", "Lio/reactivex/functions/Function;", "Lrx/functions/Func1;", "Lkotlin/jvm/functions/Function1;"):
                _add(cls_d, "map", f"({_fn_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "map", c))
                _add(cls_d, "flatMap", f"({_fn_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "flatMap", c))
            for _pr_cls in ("Lio/reactivex/rxjava3/functions/Predicate;", "Lio/reactivex/functions/Predicate;", "Lrx/functions/Func1;", "Lkotlin/jvm/functions/Function1;"):
                _add(cls_d, "filter", f"({_pr_cls}){cls_d}", lambda a, c=cls_d: _rx_op(a, "filter", c))
            _add(cls_d, "subscribeOn", f"({_pkg}/Scheduler;){cls_d}", lambda a: a[0] if a else None)
            _add(cls_d, "observeOn", f"({_pkg}/Scheduler;){cls_d}", lambda a: a[0] if a else None)
            _add(cls_d, "unsubscribeOn", f"({_pkg}/Scheduler;){cls_d}", lambda a: a[0] if a else None)

            for _cs_sig in (
                "(Lio/reactivex/rxjava3/functions/Consumer;)",
                "(Lio/reactivex/rxjava3/functions/Consumer;Lio/reactivex/rxjava3/functions/Consumer;)",
                "(Lio/reactivex/functions/Consumer;)",
                "(Lio/reactivex/functions/Consumer;Lio/reactivex/functions/Consumer;)",
                "(Lrx/functions/Action1;)",
                "(Lrx/functions/Action1;Lrx/functions/Action1;)",
                "(Lkotlin/jvm/functions/Function1;)",
            ):
                _add(cls_d, "subscribe", f"{_cs_sig}{d_cls}", lambda a, d=d_cls: _rx_sub(a, d))
            _add(cls_d, "subscribe", f"(){d_cls}", lambda a, d=d_cls: _rx_sub(a, d))
            _add(cls_d, "blockingGet", "()Ljava/lang/Object;", lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0] if a else None, getattr(stubs, "vm", None))))
            _add(cls_d, "blockingFirst", "()Ljava/lang/Object;", lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0] if a else None, getattr(stubs, "vm", None))))

    # ---- 3b. RxJava 1 Fallback-Stubs (additiv via _add; DEX hat Vorrang) --
    _add("Lrx/schedulers/Schedulers;", "from", "(Ljava/util/concurrent/Executor;)Lrx/Scheduler;", lambda a: JObject("Lrx/Scheduler;", {}))
    _add("Lrx/android/schedulers/AndroidSchedulers;", "from", "(Landroid/os/Looper;)Lrx/Scheduler;", lambda a: JObject("Lrx/Scheduler;", {}))
    _add("Lrx/Scheduler;", "createWorker", "()Lrx/Scheduler$Worker;", lambda a: JObject("Lrx/Scheduler$Worker;", {"_unsubscribed": False}))
    _add("Lrx/Scheduler;", "now", "()J", lambda a: 0)

    def _rx1_worker_sched(a):
        vm_cur = getattr(stubs, "vm", None)
        act = a[1] if len(a) > 1 else None
        if isinstance(act, JObject) and vm_cur is not None:
            _invoke_rx_cb(vm_cur, _unwrap_fn(act), (("call", "()V"), ("run", "()V")), lambda o: (o,))
        return JObject("Lrx/Subscription;", {"_unsubscribed": False})

    _add("Lrx/Scheduler$Worker;", "schedule", "(Lrx/functions/Action0;)Lrx/Subscription;", _rx1_worker_sched)
    _add("Lrx/Scheduler$Worker;", "schedule", "(Lrx/functions/Action0;JLjava/util/concurrent/TimeUnit;)Lrx/Subscription;", _rx1_worker_sched)
    _add("Lrx/Scheduler$Worker;", "unsubscribe", "()V", lambda a: a[0].fields.__setitem__("_unsubscribed", True) if a and isinstance(a[0], JObject) else None)
    _add("Lrx/Scheduler$Worker;", "isUnsubscribed", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_unsubscribed")) else 0)
    _add("Lrx/Scheduler$Worker;", "now", "()J", lambda a: 0)

    _add("Lrx/Subscription;", "unsubscribe", "()V", lambda a: a[0].fields.__setitem__("_unsubscribed", True) if a and isinstance(a[0], JObject) else None)
    _add("Lrx/Subscription;", "isUnsubscribed", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_unsubscribed")) else 0)

    _add("Lrx/subscriptions/CompositeSubscription;", "<init>", "([Lrx/Subscription;)V", lambda a: a[0].fields.setdefault("_disposed", False) if a and isinstance(a[0], JObject) else None)
    _add("Lrx/subscriptions/CompositeSubscription;", "add", "(Lrx/Subscription;)V", lambda a: None)
    _add("Lrx/subscriptions/CompositeSubscription;", "remove", "(Lrx/Subscription;)V", lambda a: None)
    _add("Lrx/subscriptions/CompositeSubscription;", "isUnsubscribed", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_disposed")) else 0)
    _add("Lrx/subscriptions/CompositeSubscription;", "hasSubscriptions", "()Z", lambda a: 0)
    _add("Lrx/subscriptions/Subscriptions;", "unsubscribed", "()Lrx/Subscription;", lambda a: JObject("Lrx/Subscription;", {"_unsubscribed": True}))
    _add("Lrx/subscriptions/Subscriptions;", "empty", "()Lrx/Subscription;", lambda a: JObject("Lrx/Subscription;", {"_unsubscribed": False}))
    _add("Lrx/subscriptions/Subscriptions;", "create", "(Lrx/functions/Action0;)Lrx/Subscription;", lambda a: JObject("Lrx/Subscription;", {"_unsubscribed": False}))

    # Rx1 Single / Observable / Completable Ergänzungen
    _add("Lrx/Single;", "create", "(Lrx/Single$OnSubscribe;)Lrx/Single;", lambda a: JObject("Lrx/Single;", {"_rx_vals": [], "_rx_on_subscribe": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Single;", "defer", "(Ljava/util/concurrent/Callable;)Lrx/Single;", lambda a: JObject("Lrx/Single;", {"_rx_callable": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Single;", "toCompletable", "()Lrx/Completable;", lambda a: _rx_convert(a[0] if a else None, "Lrx/Completable;"))
    _add("Lrx/Single;", "toObservable", "()Lrx/Observable;", lambda a: _rx_convert(a[0] if a else None, "Lrx/Observable;"))
    _add("Lrx/Single;", "flatMapCompletable", "(Lrx/functions/Func1;)Lrx/Completable;", lambda a: _rx_op(a, "flatMap", "Lrx/Completable;"))
    _add("Lrx/Single;", "flatMapObservable", "(Lrx/functions/Func1;)Lrx/Observable;", lambda a: _rx_op(a, "flatMap", "Lrx/Observable;"))
    _add("Lrx/Single;", "subscribe", "(Lrx/SingleSubscriber;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;"))
    _add("Lrx/Single;", "subscribe", "(Lrx/Observer;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;"))
    _add("Lrx/Single;", "toBlocking", "()Lrx/singles/BlockingSingle;", lambda a: JObject("Lrx/singles/BlockingSingle;", {"_src": a[0] if a else None}))
    _add("Lrx/Single;", "onErrorReturn", "(Lrx/functions/Func1;)Lrx/Single;", lambda a: a[0] if a else None)
    _add("Lrx/Single;", "doOnSuccess", "(Lrx/functions/Action1;)Lrx/Single;", lambda a: a[0] if a else None)
    _add("Lrx/Single;", "doOnError", "(Lrx/functions/Action1;)Lrx/Single;", lambda a: a[0] if a else None)

    def _rx1_obs_from_iter(a):
        raw = a[0] if a else None
        items = list(raw.fields.get("__items") or ()) if isinstance(raw, JObject) else []
        return JObject("Lrx/Observable;", {"_rx_vals": items, "_rx_ops": []})

    def _rx1_obs_from_arr(a):
        raw = a[0] if a else None
        items = list(getattr(raw, "data", None) or ()) if raw is not None else []
        return JObject("Lrx/Observable;", {"_rx_vals": items, "_rx_ops": []})

    _add("Lrx/Observable;", "create", "(Lrx/Observable$OnSubscribe;)Lrx/Observable;", lambda a: JObject("Lrx/Observable;", {"_rx_vals": [], "_rx_on_subscribe": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Observable;", "unsafeCreate", "(Lrx/Observable$OnSubscribe;)Lrx/Observable;", lambda a: JObject("Lrx/Observable;", {"_rx_vals": [], "_rx_on_subscribe": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Observable;", "from", "(Ljava/lang/Iterable;)Lrx/Observable;", _rx1_obs_from_iter)
    _add("Lrx/Observable;", "from", "([Ljava/lang/Object;)Lrx/Observable;", _rx1_obs_from_arr)
    _add("Lrx/Observable;", "toSingle", "()Lrx/Single;", lambda a: _rx_convert(a[0] if a else None, "Lrx/Single;"))
    _add("Lrx/Observable;", "toCompletable", "()Lrx/Completable;", lambda a: _rx_convert(a[0] if a else None, "Lrx/Completable;"))
    _add("Lrx/Observable;", "flatMapCompletable", "(Lrx/functions/Func1;)Lrx/Completable;", lambda a: _rx_op(a, "flatMap", "Lrx/Completable;"))
    _add("Lrx/Observable;", "flatMapSingle", "(Lrx/functions/Func1;)Lrx/Single;", lambda a: _rx_op(a, "flatMap", "Lrx/Single;"))
    _add("Lrx/Observable;", "subscribe", "(Lrx/functions/Action1;Lrx/functions/Action1;Lrx/functions/Action0;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;"))
    _add("Lrx/Observable;", "subscribe", "(Lrx/Subscriber;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;"))
    _add("Lrx/Observable;", "subscribe", "(Lrx/Observer;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;"))
    _add("Lrx/Observable;", "toBlocking", "()Lrx/observables/BlockingObservable;", lambda a: JObject("Lrx/observables/BlockingObservable;", {"_src": a[0] if a else None}))
    _add("Lrx/Observable;", "doOnNext", "(Lrx/functions/Action1;)Lrx/Observable;", lambda a: a[0] if a else None)
    _add("Lrx/Observable;", "doOnError", "(Lrx/functions/Action1;)Lrx/Observable;", lambda a: a[0] if a else None)
    _add("Lrx/Observable;", "doOnCompleted", "(Lrx/functions/Action0;)Lrx/Observable;", lambda a: a[0] if a else None)

    _add("Lrx/Completable;", "create", "(Lrx/Completable$OnSubscribe;)Lrx/Completable;", lambda a: JObject("Lrx/Completable;", {"_rx_vals": [], "_rx_on_subscribe": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Completable;", "complete", "()Lrx/Completable;", lambda a: JObject("Lrx/Completable;", {"_rx_vals": [], "_rx_ops": []}))
    _add("Lrx/Completable;", "fromAction", "(Lrx/functions/Action0;)Lrx/Completable;", lambda a: JObject("Lrx/Completable;", {"_rx_action0": a[0] if a else None, "_rx_ops": []}))
    _add("Lrx/Completable;", "toSingle", "(Lrx/functions/Func0;)Lrx/Single;", lambda a: JObject("Lrx/Single;", {"_rx_callable": a[1] if len(a) > 1 else None, "_rx_ops": []}))
    _add("Lrx/Completable;", "toSingleDefault", "(Ljava/lang/Object;)Lrx/Single;", lambda a: JObject("Lrx/Single;", {"_rx_vals": [a[1] if len(a) > 1 else None], "_rx_ops": []}))
    _add("Lrx/Completable;", "toObservable", "()Lrx/Observable;", lambda a: _rx_convert(a[0] if a else None, "Lrx/Observable;"))
    _add("Lrx/Completable;", "andThen", "(Lrx/Single;)Lrx/Single;", lambda a: a[1] if len(a) > 1 and isinstance(a[1], JObject) else JObject("Lrx/Single;", {"_rx_vals": [], "_rx_ops": []}))
    _add("Lrx/Completable;", "andThen", "(Lrx/Observable;)Lrx/Observable;", lambda a: a[1] if len(a) > 1 and isinstance(a[1], JObject) else JObject("Lrx/Observable;", {"_rx_vals": [], "_rx_ops": []}))
    _add("Lrx/Completable;", "andThen", "(Lrx/Completable;)Lrx/Completable;", lambda a: a[1] if len(a) > 1 and isinstance(a[1], JObject) else (a[0] if a else None))
    _add("Lrx/Completable;", "subscribe", "(Lrx/functions/Action0;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;", action0=True))
    _add("Lrx/Completable;", "subscribe", "(Lrx/functions/Action0;Lrx/functions/Action1;)Lrx/Subscription;", lambda a: _rx_sub(a, "Lrx/Subscription;", action0=True))
    _add("Lrx/Completable;", "subscribe", "(Lrx/CompletableSubscriber;)V", lambda a: _rx_sub(a, "Lrx/Subscription;", action0=True, ret_void=True))
    _add("Lrx/Completable;", "await", "()V", lambda a: (_rx_eval(a[0] if a else None, getattr(stubs, "vm", None)), None)[1])
    _add("Lrx/Completable;", "get", "()Ljava/lang/Throwable;", lambda a: (_rx_eval(a[0] if a else None, getattr(stubs, "vm", None)), a[0].fields.get("_rx_err") if a and isinstance(a[0], JObject) else None)[1])

    _add("Lrx/singles/BlockingSingle;", "value", "()Ljava/lang/Object;",
         lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0].fields.get("_src") if a and isinstance(a[0], JObject) else None, getattr(stubs, "vm", None))))
    _add("Lrx/observables/BlockingObservable;", "first", "()Ljava/lang/Object;",
         lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0].fields.get("_src") if a and isinstance(a[0], JObject) else None, getattr(stubs, "vm", None))))
    _add("Lrx/observables/BlockingObservable;", "single", "()Ljava/lang/Object;",
         lambda a: (lambda vs: vs[0] if vs else None)(_rx_eval(a[0].fields.get("_src") if a and isinstance(a[0], JObject) else None, getattr(stubs, "vm", None))))
    _add("Lrx/observables/BlockingObservable;", "last", "()Ljava/lang/Object;",
         lambda a: (lambda vs: vs[-1] if vs else None)(_rx_eval(a[0].fields.get("_src") if a and isinstance(a[0], JObject) else None, getattr(stubs, "vm", None))))
    _add("Lrx/observables/BlockingObservable;", "firstOrDefault", "(Ljava/lang/Object;)Ljava/lang/Object;",
         lambda a: (lambda vs: vs[0] if vs else (a[1] if len(a) > 1 else None))(_rx_eval(a[0].fields.get("_src") if a and isinstance(a[0], JObject) else None, getattr(stubs, "vm", None))))


def attach_g8_vm_hook(stubs: Any, vm: Any) -> None:
    """Haengt Compose-NavHost / rememberNavController / collectAsLazyPagingItems Hook ein."""
    if vm is None or getattr(vm, "_g8_hook_attached", False):
        return
    vm._g8_hook_attached = True

    def _g8_hook(rcls, cls, name, sig, args, static, _vm):
        if cls.startswith("Landroidx/navigation/compose/NavHostControllerKt") and name in ("rememberNavController", "rememberNavController$default"):
            cur_act = getattr(stubs, "_current_activity", None)
            nc = cur_act.fields.get("_compose_nav_controller") if isinstance(cur_act, JObject) else None
            if not isinstance(nc, JObject):
                nc = _make_nav_ctrl(stubs)
                if isinstance(cur_act, JObject):
                    cur_act.fields["_compose_nav_controller"] = nc
            else:
                stubs._compose_nav_controller = nc
            return True, nc

        if cls.startswith("Landroidx/navigation/compose/NavHostKt") and name in ("NavHost", "NavHost$default"):
            nc = next((a for a in args if isinstance(a, JObject) and "_back_stack" in a.fields), None)
            if nc is None:
                cur_act = getattr(stubs, "_current_activity", None)
                nc = cur_act.fields.get("_compose_nav_controller") if isinstance(cur_act, JObject) else None
                if not isinstance(nc, JObject):
                    nc = getattr(stubs, "_compose_nav_controller", None) or _make_nav_ctrl(stubs)
            start_dest = next((_txt(a) for a in args if isinstance(a, JString)), "")
            if start_dest and nc.fields.get("_current_route") == "start":
                nc.fields["_current_route"] = start_dest
                if nc.fields.get("_back_stack"):
                    nc.fields["_back_stack"][0].fields["_route"] = start_dest
            ptypes = _sig_param_types_g8(sig)
            pairs = [(ptypes[i] if i < len(ptypes) else "", args[i]) for i in range(len(args))]
            builder_fn = next(
                (_unwrap_fn(v) for pt, v in reversed(pairs) if "Function1" in pt and isinstance(v, JObject)),
                None,
            )
            if builder_fn is None:
                builder_fn = next(
                    (_unwrap_fn(a) for a in reversed(args)
                     if isinstance(a, JObject) and ("Lambda" in a.cls or "Function" in a.cls or "$block" in a.fields)),
                    None,
                )
            ngb = JObject("Landroidx/navigation/NavGraphBuilder;", {"_routes": nc.fields.setdefault("_routes", {})})
            if isinstance(builder_fn, JObject):
                try:
                    _vm.invoke(builder_fn.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (builder_fn, ngb))
                except _VM_STOP_EXCS:
                    raise
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
                    except _VM_STOP_EXCS:
                        raise
                    except Exception:
                        continue
            return True, None

        if cls.startswith("Landroidx/navigation/compose/NavGraphBuilderKt") and name in ("composable", "composable$default", "dialog", "dialog$default"):
            ngb = next((a for a in args if isinstance(a, JObject) and "_routes" in a.fields), None)
            route = next((_txt(a) for a in args if isinstance(a, JString)), "")
            ptypes = _sig_param_types_g8(sig)
            pairs = [(ptypes[i] if i < len(ptypes) else "", args[i]) for i in range(len(args))]
            content = next(
                (v for pt, v in reversed(pairs)
                 if ("Function4" in pt or "Function3" in pt or "Function2" in pt) and isinstance(v, JObject)),
                None,
            )
            if content is None:
                content = next(
                    (a for a in reversed(args)
                     if isinstance(a, JObject) and ("Lambda" in a.cls or "Function" in a.cls or "$block" in a.fields)),
                    None,
                )
            if ngb is not None and route and content is not None:
                ngb.fields["_routes"][route] = content
            return True, None

        if cls.startswith("Landroidx/paging/compose/LazyPagingItemsKt") and name == "collectAsLazyPagingItems":
            flow = args[0] if args else None
            items = list(flow.fields.get("__items") or ()) if isinstance(flow, JObject) else []
            return True, JObject("Landroidx/paging/compose/LazyPagingItems;", {"__items": items})

        return False, None

    vm._method_hooks.append(_g8_hook)
