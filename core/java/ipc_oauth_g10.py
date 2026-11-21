# -*- coding: utf-8 -*-
"""core.java.ipc_oauth_g10 — G10: Uri/Uri.Builder, OAuth2/CustomTabs DeepLinks & Lokales Android-IPC."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from core.java.objects import JArray, JObject, JString


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject):
        if "text" in v.fields and isinstance(v.fields["text"], str):
            return v.fields["text"]
        if "$str" in v.fields:
            return str(v.fields["$str"])
        if "value" in v.fields and v.cls == "Ljava/lang/String;":
            return str(v.fields["value"])
    return str(v)


def _uri_str(u: Any) -> str:
    if isinstance(u, JObject):
        return str(u.fields.get("$uri") or u.fields.get("_uri") or "")
    return _txt(u)


def make_uri(uri_text: str) -> JObject:
    return JObject("Landroid/net/Uri;", {"$uri": str(uri_text or "")})


def _make_uri_builder(uri_text: str = "") -> JObject:
    p = urlparse(str(uri_text or ""))
    q_pairs = parse_qsl(p.query, keep_blank_values=True)
    return JObject("Landroid/net/Uri$Builder;", {
        "_scheme": p.scheme or "",
        "_authority": p.netloc or "",
        "_path": p.path or "",
        "_query": list(q_pairs),
        "_fragment": p.fragment or "",
    })


def resolve_deeplink_target(manifest: Any, uri_str: str) -> Optional[str]:
    if manifest is None or not uri_str:
        return None
    dls = getattr(manifest, "deep_links", None) or ()
    p = urlparse(uri_str)
    scheme = (p.scheme or "").lower()
    host = (p.netloc or "").split(":", 1)[0].lower()
    path = p.path or ""
    best = None
    best_score = -1
    for entry in dls:
        es = str(entry.get("scheme") or "").lower()
        if es != scheme:
            continue
        eh = str(entry.get("host") or "").lower()
        if eh and eh != host:
            continue
        ep = str(entry.get("path_prefix") or "")
        if ep and not path.startswith(ep):
            continue
        sc = (2 if eh else 0) + len(ep)
        if sc > best_score:
            best_score = sc
            best = entry.get("activity")
    return best


def deliver_deeplink(stubs: Any, runner: Any, uri_str: str, manifest: Any = None) -> bool:
    """Routet eine URI anhand Manifest-intent-filter direkt in die Ziel-Activity (onNewIntent/Navigation)."""
    if stubs is None or not uri_str:
        return False
    man = manifest
    if man is None:
        man = getattr(stubs, "_loaded_manifest", None)
    if man is None:
        app_root = getattr(getattr(runner, "provider", None), "root", None) if runner is not None else None
        if app_root:
            try:
                from core.java.manifest import parse_manifest
                man = parse_manifest(app_root)
            except Exception:
                man = None
    tgt = resolve_deeplink_target(man, uri_str)
    if not tgt:
        cur = getattr(stubs, "_current_activity", None)
        tgt = cur.cls if isinstance(cur, JObject) else None
    if not tgt:
        return False
    intent = JObject("Landroid/content/Intent;", {
        "action": "android.intent.action.VIEW",
        "$target": tgt,
        "_data_uri": str(uri_str),
        "_flags": 0x20000000,  # FLAG_ACTIVITY_SINGLE_TOP
        "extras": {},
        "_extras": {},
    })
    cur = getattr(stubs, "_current_activity", None)
    vm = getattr(stubs, "vm", None) or (getattr(runner, "vm", None) if runner is not None else None)
    stubs.__dict__.setdefault("_ipc_log", []).append({"kind": "deeplink", "uri": str(uri_str), "target": tgt})
    if isinstance(cur, JObject) and cur.cls == tgt:
        cur.fields["_intent"] = intent
        if runner is not None and hasattr(runner, "_invoke_on"):
            runner._invoke_on(cur, "onNewIntent", "(Landroid/content/Intent;)V", [cur, intent])
            runner._invoke_on(cur, "onResume", "()V", [cur])
            runner._drain_runnables()
        elif vm is not None and hasattr(vm, "invoke_virtual"):
            try:
                vm.invoke_virtual(cur, "onNewIntent", "(Landroid/content/Intent;)V", (cur, intent))
            except Exception:
                pass
        tr = getattr(stubs, "view_tree", None)
        if tr is not None:
            tr.touch()
        return True
    stubs._pending_nav_target = tgt
    stubs._pending_nav_intent = intent
    if runner is not None and hasattr(runner, "navigate_to_pending"):
        return bool(runner.navigate_to_pending())
    return True


def trigger_broadcast(
    stubs: Any,
    vm: Any,
    action: str,
    extras: Optional[Dict[str, Any]] = None,
    manifest: Any = None,
) -> int:
    """Loest dynamisch registrierte UND Manifest-BroadcastReceiver fuer `action` deterministisch aus."""
    if stubs is None or vm is None:
        return 0
    ex_map = {}
    for k, v in (extras or {}).items():
        ex_map[str(k)] = JString(v) if isinstance(v, str) else v
    intent = JObject("Landroid/content/Intent;", {
        "action": str(action or ""),
        "extras": ex_map,
        "_extras": dict(ex_map),
    })
    ctx = getattr(stubs, "_current_activity", None) or getattr(stubs, "application", None)
    if ctx is None and hasattr(stubs, "make_context"):
        ctx = stubs.make_context()
    fired = 0
    for rec in list(getattr(stubs, "_broadcast_receivers", ()) or ()):
        acts = rec.get("actions") or ()
        if not acts or action in acts:
            recv = rec.get("receiver")
            if isinstance(recv, JObject):
                try:
                    vm.invoke_virtual(recv, "onReceive", "(Landroid/content/Context;Landroid/content/Intent;)V", (recv, ctx, intent))
                    fired += 1
                except Exception:
                    pass
    man = manifest or getattr(stubs, "_loaded_manifest", None)
    if man is not None:
        for ment in (getattr(man, "receivers", None) or ()):
            macts = ment.get("actions") or ()
            if macts and action not in macts:
                continue
            rcls = ment.get("cls")
            if rcls:
                try:
                    if hasattr(vm, "_lazy_class"):
                        vm._lazy_class(rcls)
                    robj = JObject(rcls, {})
                    if (rcls, "<init>", "()V") in vm.registry:
                        vm.invoke(rcls, "<init>", "()V", (robj,))
                    vm.invoke_virtual(robj, "onReceive", "(Landroid/content/Context;Landroid/content/Intent;)V", (robj, ctx, intent))
                    fired += 1
                except Exception:
                    pass
    stubs.__dict__.setdefault("_ipc_log", []).append({"kind": "broadcast", "action": str(action or ""), "fired": fired})
    return fired


def _start_service_internal(stubs: Any, vm: Any, intent: Any, foreground: bool = False) -> Optional[JObject]:
    if stubs is None:
        return None
    scls = ""
    if isinstance(intent, JObject):
        scls = str(intent.fields.get("$target") or "")
    if not scls:
        return None
    if not (scls.startswith("L") and scls.endswith(";")):
        scls = "L" + scls.replace(".", "/") + ";"
    svc_map = stubs.__dict__.setdefault("_active_services", {})
    svc = svc_map.get(scls)
    created = svc is None
    if svc is None:
        svc = JObject(scls, {})
        svc_map[scls] = svc
    if vm is not None:
        try:
            if hasattr(vm, "_lazy_class"):
                vm._lazy_class(scls)
            if created:
                if (scls, "<init>", "()V") in vm.registry:
                    vm.invoke(scls, "<init>", "()V", (svc,))
                vm.invoke_virtual(svc, "onCreate", "()V", (svc,))
            vm.invoke_virtual(svc, "onStartCommand", "(Landroid/content/Intent;II)I", (svc, intent, 0, 1))
        except Exception:
            pass
    stubs.__dict__.setdefault("_ipc_log", []).append({"kind": "foreground_service" if foreground else "service", "cls": scls})
    return JObject("Landroid/content/ComponentName;", {"pkg": getattr(getattr(stubs, "profile", None), "package", ""), "cls": scls})


def fire_pending_intent(stubs: Any, vm: Any, runner: Any, pi_or_idx: Any = 0) -> bool:
    """Loest ein geplantes Alarm-/PendingIntent (Activity/Broadcast/Service) sofort aus."""
    if stubs is None:
        return False
    pi = pi_or_idx
    if isinstance(pi_or_idx, int):
        alarms = getattr(stubs, "_scheduled_alarms", None) or []
        if 0 <= pi_or_idx < len(alarms):
            pi = alarms[pi_or_idx].get("operation")
        else:
            pis = getattr(stubs, "_pending_intents", None) or []
            if 0 <= pi_or_idx < len(pis):
                pi = pis[pi_or_idx]
    if not isinstance(pi, JObject):
        return False
    pkind = pi.fields.get("_pi_kind", "activity")
    intent = pi.fields.get("_intent")
    if pkind == "broadcast":
        act = intent.fields.get("action", "") if isinstance(intent, JObject) else ""
        ex = intent.fields.get("_extras") if isinstance(intent, JObject) else {}
        return trigger_broadcast(stubs, vm, act, ex) > 0
    if pkind == "service":
        return _start_service_internal(stubs, vm, intent, foreground=bool(pi.fields.get("_fg"))) is not None
    tgt = intent.fields.get("$target") if isinstance(intent, JObject) else None
    if tgt:
        stubs._pending_nav_target = tgt
        stubs._pending_nav_intent = intent
        if runner is not None and hasattr(runner, "navigate_to_pending"):
            return bool(runner.navigate_to_pending())
        return True
    return False


def install_g10_stubs(stubs: Any) -> None:
    """Registriert Uri/Uri$Builder, Intent getData, CustomTabsIntent, Broadcast, Service & AlarmManager."""
    if stubs is None or getattr(stubs, "_g10_installed", False):
        return
    stubs._g10_installed = True
    stubs.__dict__.setdefault("_broadcast_receivers", [])
    stubs.__dict__.setdefault("_active_services", {})
    stubs.__dict__.setdefault("_scheduled_alarms", [])
    stubs.__dict__.setdefault("_pending_intents", [])
    stubs.__dict__.setdefault("_ipc_log", [])
    stubs._g10_deliver_deeplink = lambda uri_str, runner=None, manifest=None: deliver_deeplink(stubs, runner, uri_str, manifest)

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # Uri & Uri$Builder
    URI = "Landroid/net/Uri;"
    UB = "Landroid/net/Uri$Builder;"
    _add(URI, "parse", f"(Ljava/lang/String;){URI}", lambda a: make_uri(_txt(a[0]) if a else ""))
    _add(URI, "toString", "()Ljava/lang/String;", lambda a: JString(_uri_str(a[0]) if a else ""))
    _add(URI, "getScheme", "()Ljava/lang/String;", lambda a: JString(urlparse(_uri_str(a[0])).scheme) if a else JString(""))
    _add(URI, "getHost", "()Ljava/lang/String;", lambda a: JString((urlparse(_uri_str(a[0])).netloc or "").split(":", 1)[0]) if a else JString(""))
    _add(URI, "getAuthority", "()Ljava/lang/String;", lambda a: JString(urlparse(_uri_str(a[0])).netloc) if a else JString(""))
    _add(URI, "getPath", "()Ljava/lang/String;", lambda a: JString(urlparse(_uri_str(a[0])).path) if a else JString(""))
    _add(URI, "getQuery", "()Ljava/lang/String;", lambda a: (lambda q: JString(q) if q else None)(urlparse(_uri_str(a[0])).query) if a else None)
    _add(URI, "getFragment", "()Ljava/lang/String;", lambda a: (lambda f: JString(f) if f else None)(urlparse(_uri_str(a[0])).fragment) if a else None)

    def _get_qp(a):
        if len(a) < 2:
            return None
        qs = parse_qs(urlparse(_uri_str(a[0])).query, keep_blank_values=True)
        vals = qs.get(_txt(a[1]))
        return JString(vals[0]) if vals else None

    _add(URI, "getQueryParameter", "(Ljava/lang/String;)Ljava/lang/String;", _get_qp)
    _add(URI, "getBooleanQueryParameter", "(Ljava/lang/String;Z)Z",
         lambda a: (lambda v: 0 if (v and v.text.lower() in ("false", "0")) else (1 if v else (1 if (len(a) > 2 and a[2]) else 0)))(_get_qp(a[:2])))
    _add(URI, "getQueryParameterNames", "()Ljava/util/Set;",
         lambda a: JObject("Ljava/util/LinkedHashSet;", {"__items": [JString(k) for k in parse_qs(urlparse(_uri_str(a[0])).query, keep_blank_values=True).keys()]}) if a else JObject("Ljava/util/LinkedHashSet;", {"__items": []}))
    _add(URI, "getPathSegments", "()Ljava/util/List;",
         lambda a: JObject("Ljava/util/ArrayList;", {"__items": [JString(s) for s in urlparse(_uri_str(a[0])).path.strip("/").split("/") if s]}) if a else JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(URI, "getLastPathSegment", "()Ljava/lang/String;",
         lambda a: (lambda segs: JString(segs[-1]) if segs else None)([s for s in urlparse(_uri_str(a[0])).path.strip("/").split("/") if s]) if a else None)
    _add(URI, "buildUpon", f"(){UB}", lambda a: _make_uri_builder(_uri_str(a[0]) if a else ""))

    _add(UB, "<init>", "()V", lambda a: a[0].fields.update({"_scheme": "", "_authority": "", "_path": "", "_query": [], "_fragment": ""}) if a else None)
    _add(UB, "scheme", f"(Ljava/lang/String;){UB}", lambda a: (a[0].fields.__setitem__("_scheme", _txt(a[1])), a[0])[1])
    _add(UB, "authority", f"(Ljava/lang/String;){UB}", lambda a: (a[0].fields.__setitem__("_authority", _txt(a[1])), a[0])[1])
    _add(UB, "path", f"(Ljava/lang/String;){UB}", lambda a: (a[0].fields.__setitem__("_path", _txt(a[1])), a[0])[1])
    _add(UB, "appendPath", f"(Ljava/lang/String;){UB}",
         lambda a: (a[0].fields.__setitem__("_path", (a[0].fields.get("_path", "").rstrip("/") + "/" + _txt(a[1]).lstrip("/"))), a[0])[1])
    _add(UB, "appendQueryParameter", f"(Ljava/lang/String;Ljava/lang/String;){UB}",
         lambda a: (a[0].fields.setdefault("_query", []).append((_txt(a[1]), _txt(a[2]))), a[0])[1])

    def _ub_build(a):
        if not a or not isinstance(a[0], JObject):
            return make_uri("")
        f = a[0].fields
        q = urlencode(f.get("_query") or ())
        return make_uri(urlunparse((f.get("_scheme", ""), f.get("_authority", ""), f.get("_path", ""), "", q, f.get("_fragment", ""))))

    _add(UB, "build", f"(){URI}", _ub_build)

    # Intent getData / setData / getDataString
    IT = "Landroid/content/Intent;"
    _add(IT, "getData", f"(){URI}",
         lambda a: (lambda u: make_uri(u) if u else None)(a[0].fields.get("_data_uri")) if a and isinstance(a[0], JObject) else None)
    _add(IT, "getDataString", "()Ljava/lang/String;",
         lambda a: (lambda u: JString(u) if u else None)(a[0].fields.get("_data_uri")) if a and isinstance(a[0], JObject) else None)

    # CustomTabsIntent
    CTI = "Landroidx/browser/customtabs/CustomTabsIntent;"
    CTIB = "Landroidx/browser/customtabs/CustomTabsIntent$Builder;"
    _add(CTIB, "<init>", "()V", lambda a: a[0])
    _add(CTIB, "<init>", "(Landroidx/browser/customtabs/CustomTabsSession;)V", lambda a: a[0])
    _add(CTIB, "setShowTitle", f"(Z){CTIB}", lambda a: a[0])
    _add(CTIB, "setToolbarColor", f"(I){CTIB}", lambda a: a[0])
    _add(CTIB, "build", f"(){CTI}", lambda a: JObject(CTI, {"intent": JObject(IT, {"action": "android.intent.action.VIEW"})}))

    def _launch_custom_tab(a):
        u = _uri_str(a[2] if len(a) > 2 else (a[1] if len(a) > 1 else ""))
        tr = getattr(stubs, "view_tree", None)
        if tr is not None:
            tr.__dict__.setdefault("web_events", []).append({"kind": "custom_tabs_launch", "url": u})
        stubs.__dict__.setdefault("_ipc_log", []).append({"kind": "custom_tab", "url": u})
        redir_map = getattr(stubs, "_oauth_redirects", None) or {}
        for pref, cb_uri in redir_map.items():
            if u.startswith(pref):
                deliver_deeplink(stubs, getattr(stubs, "_active_runner", None), cb_uri)
                break
        return None

    _add(CTI, "launchUrl", f"(Landroid/content/Context;{URI})V", _launch_custom_tab)

    # PendingIntent & AlarmManager & Broadcast & Service
    PI = "Landroid/app/PendingIntent;"
    for _m, _k, _fg in (
        ("getActivity", "activity", False),
        ("getBroadcast", "broadcast", False),
        ("getService", "service", False),
        ("getForegroundService", "service", True),
    ):
        def _mk_pi(a, kind=_k, fg=_fg):
            pi_obj = JObject(PI, {"_pi_kind": kind, "_intent": a[2] if len(a) > 2 else None, "_req": int(a[1]) if len(a) > 1 else 0, "_fg": fg})
            stubs._pending_intents.append(pi_obj)
            return pi_obj
        _add(PI, _m, f"(Landroid/content/Context;ILandroid/content/Intent;I){PI}", _mk_pi)
    _add(PI, "send", "()V", lambda a: fire_pending_intent(stubs, getattr(stubs, "vm", None), getattr(stubs, "_active_runner", None), a[0]) if a else None)

    AM = "Landroid/app/AlarmManager;"
    def _am_set(a):
        if len(a) > 3:
            stubs._scheduled_alarms.append({"type": int(a[1]), "trigger_ms": int(a[2]), "operation": a[3]})
        return None
    for _am_m in ("set", "setExact", "setExactAndAllowWhileIdle", "setAndAllowWhileIdle", "setInexactRepeating"):
        _add(AM, _am_m, f"(IJ{PI})V", _am_set)
    _add(AM, "setRepeating", f"(IJJ{PI})V",
         lambda a: stubs._scheduled_alarms.append({"type": int(a[1]), "trigger_ms": int(a[2]), "interval_ms": int(a[3]), "operation": a[4]}) if len(a) > 4 else None)
    _add(AM, "cancel", f"({PI})V",
         lambda a: stubs._scheduled_alarms[:] if not a else [stubs._scheduled_alarms.remove(x) for x in list(stubs._scheduled_alarms) if x.get("operation") is a[1]])

    # LocalBroadcastManager & Context IPC
    LBM = "Landroidx/localbroadcastmanager/content/LocalBroadcastManager;"
    lbm_inst = JObject(LBM, {})
    _add(LBM, "getInstance", f"(Landroid/content/Context;){LBM}", lambda a: lbm_inst)

    def _reg_rec(a):
        recv = a[1] if len(a) > 1 else None
        flt = a[2] if len(a) > 2 else None
        acts = list(flt.fields.get("_actions") or ()) if isinstance(flt, JObject) else []
        if isinstance(recv, JObject):
            stubs._broadcast_receivers.append({"receiver": recv, "actions": acts})
        return None

    def _send_bcast(a):
        it = a[1] if len(a) > 1 else None
        act = it.fields.get("action", "") if isinstance(it, JObject) else ""
        ex = it.fields.get("_extras") if isinstance(it, JObject) else {}
        return trigger_broadcast(stubs, getattr(stubs, "vm", None), act, ex)

    _add(LBM, "registerReceiver", "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;)V", _reg_rec)
    _add(LBM, "unregisterReceiver", "(Landroid/content/BroadcastReceiver;)V",
         lambda a: [stubs._broadcast_receivers.remove(r) for r in list(stubs._broadcast_receivers) if len(a) > 1 and r.get("receiver") is a[1]])
    _add(LBM, "sendBroadcast", "(Landroid/content/Intent;)Z", lambda a: 1 if _send_bcast(a) else 1)
    _add(LBM, "sendBroadcastSync", "(Landroid/content/Intent;)V", lambda a: (_send_bcast(a), None)[1])

    IFLT = "Landroid/content/IntentFilter;"
    _add(IFLT, "<init>", "()V", lambda a: a[0].fields.setdefault("_actions", []) if a and isinstance(a[0], JObject) else None)
    _add(IFLT, "<init>", "(Ljava/lang/String;)V", lambda a: a[0].fields.setdefault("_actions", [_txt(a[1])]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(IFLT, "addAction", "(Ljava/lang/String;)V", lambda a: a[0].fields.setdefault("_actions", []).append(_txt(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else None)

    for _ctx_c in ("Landroid/content/Context;", "Landroid/content/ContextWrapper;", "Landroid/app/Activity;", "Landroidx/appcompat/app/AppCompatActivity;", "Landroidx/fragment/app/FragmentActivity;"):
        _add(_ctx_c, "registerReceiver", "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;)Landroid/content/Intent;", lambda a: (_reg_rec(a), None)[1])
        _add(_ctx_c, "registerReceiver", "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;I)Landroid/content/Intent;", lambda a: (_reg_rec(a), None)[1])
        _add(_ctx_c, "unregisterReceiver", "(Landroid/content/BroadcastReceiver;)V",
             lambda a: [stubs._broadcast_receivers.remove(r) for r in list(stubs._broadcast_receivers) if len(a) > 1 and r.get("receiver") is a[1]])
        _add(_ctx_c, "sendBroadcast", "(Landroid/content/Intent;)V", lambda a: (_send_bcast(a), None)[1])
        _add(_ctx_c, "startService", "(Landroid/content/Intent;)Landroid/content/ComponentName;",
             lambda a: _start_service_internal(stubs, getattr(stubs, "vm", None), a[1] if len(a) > 1 else None, foreground=False))
        _add(_ctx_c, "startForegroundService", "(Landroid/content/Intent;)Landroid/content/ComponentName;",
             lambda a: _start_service_internal(stubs, getattr(stubs, "vm", None), a[1] if len(a) > 1 else None, foreground=True))
        _add(_ctx_c, "stopService", "(Landroid/content/Intent;)Z",
             lambda a: 1 if stubs._active_services.pop(a[1].fields.get("$target", "") if len(a) > 1 and isinstance(a[1], JObject) else "", None) is not None else 0)
