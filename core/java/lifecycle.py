# -*- coding: utf-8 -*-
"""core.java.lifecycle — Ganze-App-Einstieg (MS-4): treibt eine App generisch durch den
Android-Lifecycle ueber die DalvikVM + einen ClassProvider.

Prinzip (siehe konzept-app-debug-schnittstellen): „ganze App" = derselbe Mechanismus wie „Funktion
samt Callees", nur der Einstieg ist der App-Lifecycle. GENERISCH: Einstieg (Application + Launcher-
Activity) kommt aus dem Manifest, nicht fest verdrahtet. Callees lazy ueber `vm.class_provider`.
Report meldet je Phase called/skipped/errors + global missing (Coverage-Loop).
"""
from __future__ import annotations

import os

from core.java.objects import JObject


class _StubRuntime:
    """Adapter: macht AndroidStubs als VM-`runtime` (resolve_invoke) nutzbar."""
    def __init__(self, stubs):
        self.stubs = stubs

    def resolve_invoke(self, cls, name, sig, args, static):
        return self.stubs.resolve(cls, name, sig, args, static)


class LifecycleRunner:
    # (Methode, Signatur, Anzahl Stub-Argumente nach `this`)
    SEQ_APPLICATION = [
        ("<init>", "()V", 0),
        ("attachBaseContext", "(Landroid/content/Context;)V", 1),
        ("onCreate", "()V", 0),
    ]
    SEQ_ACTIVITY = [
        ("<init>", "()V", 0),
        ("attachBaseContext", "(Landroid/content/Context;)V", 1),
        ("onCreate", "(Landroid/os/Bundle;)V", 1),
        ("onCreate", "()V", 0),
        ("onStart", "()V", 0),
        ("onResume", "()V", 0),
    ]
    # Rueckwaertskompatibel: der alte Default-Lauf (eine Entry-Klasse, Activity-artig).
    SEQUENCE = SEQ_ACTIVITY

    def __init__(self, vm, provider=None, with_stubs=True, stubs=None):
        self.vm = vm
        self.provider = provider or getattr(vm, "class_provider", None)
        if getattr(self.vm, "class_provider", None) is None and self.provider is not None:
            self.vm.class_provider = self.provider
        self.stubs = stubs
        if self.stubs is None and with_stubs and getattr(vm, "runtime", None) is None:
            try:
                from core.java.dex_loader import AndroidStubs
                self.stubs = AndroidStubs(vm=vm)
                self.stubs.seed_framework_statics(vm)
                vm.runtime = _StubRuntime(self.stubs)
            except Exception:
                self.stubs = None
        elif self.stubs is None:
            rt = getattr(vm, "runtime", None)
            self.stubs = (
                getattr(rt, "stubs", None)
                or getattr(getattr(rt, "prev", None), "stubs", None)
                or getattr(getattr(rt, "_inner", None), "stubs", None)
                or getattr(vm, "_stubs_ref", None)
            )
            if self.stubs is not None and getattr(self.stubs, "vm", None) is None:
                try:
                    self.stubs.vm = vm
                except Exception:
                    pass
        existing_stack = getattr(self.stubs, "_activity_stack", None) if self.stubs is not None else None
        self.activity_stack = existing_stack if isinstance(existing_stack, list) else []
        if self.stubs is not None:
            self.stubs._activity_stack = self.activity_stack
        # Echter Context fuer attachBaseContext (statt None)
        self._context = None
        try:
            if self.stubs is not None and hasattr(self.stubs, "make_context"):
                self._context = self.stubs.make_context()
        except Exception:
            self._context = None

    # ---- intern -----------------------------------------------------------
    def _methods_of(self, descriptor):
        """Entry-Klasse (lazy) laden und ihre Methoden als {(name,sig): md} zurueckgeben."""
        if self.provider is not None:
            try:
                mds = self.provider.get_class(descriptor)
            except Exception:
                mds = None
            for md in (mds or []):
                self.vm.registry.setdefault((md.cls, md.name, md.sig), md)
        return {(k[1], k[2]): v for k, v in self.vm.registry.items() if k[0] == descriptor}

    def _run_class(self, descriptor, sequence, role, intent=None):
        """Eine Klasse durch eine Lifecycle-Sequenz treiben; Phasen-Report zurueckgeben."""
        phase = {"class": descriptor, "role": role, "called": [], "skipped": [], "errors": []}
        methods = self._methods_of(descriptor)
        if not methods:
            label = "Entry-Klasse nicht gefunden" if role == "entry" else "Klasse nicht gefunden"
            phase["skipped"].append({"m": "*", "sig": "*", "grund": label})
            return phase
        this = JObject(descriptor, {})
        if intent is not None:
            this.fields["_intent"] = intent
        if role == "application" and self.stubs is not None:
            self.stubs.application = this     # AP1.3: Application-Instanz fuer Activity-Phase bewahren
        elif role == "activity" and self.stubs is not None:
            self.stubs._activity_finished = False
            self.stubs._current_activity = this
            self.stubs._current_activity_obj = this
            fmap = getattr(self.stubs, "_fragments_by_id", None)
            if isinstance(fmap, dict):
                fmap.clear()
            ftag = getattr(self.stubs, "_fragments_by_tag", None)
            if isinstance(ftag, dict):
                ftag.clear()
            tree = getattr(self.stubs, "view_tree", None)
            if tree is not None:
                tree._compose_recompose = None
        for name, sig, extra in sequence:
            md = methods.get((name, sig))
            if md is None:                                # geerbte Lifecycle-Methode (z.B. ReactActivity)
                try:
                    md = self.vm._resolve_method(descriptor, descriptor, name, sig)
                except Exception:
                    md = None
            if md is None:
                phase["skipped"].append({"m": name, "sig": sig, "grund": "nicht definiert"})
                continue
            args = tuple([this] + self._extra_args(name, extra))
            try:
                self.vm._run_mdef(md, list(args))         # md ggf. aus Superklasse, this = Subklasse
                phase["called"].append({"m": name, "sig": sig, "in": md.cls})
                if role == "activity":
                    self._fire_activity_callbacks(name, sig, this)
                    if name in ("onCreate", "onResume") and self.stubs is not None:
                        fl = getattr(self.stubs, "_flush_fragment_transactions", None)
                        if callable(fl):
                            fl()
            except Exception as e:                       # noqa
                phase["errors"].append({"m": name, "sig": sig, "err": repr(e)})
        return phase

    _ACT_CB = {
        ("onCreate", "(Landroid/os/Bundle;)V"):
            ("onActivityCreated", "(Landroid/app/Activity;Landroid/os/Bundle;)V", [None]),
        ("onStart", "()V"):
            ("onActivityStarted", "(Landroid/app/Activity;)V", []),
        ("onResume", "()V"):
            ("onActivityResumed", "(Landroid/app/Activity;)V", []),
        ("onPause", "()V"):
            ("onActivityPaused", "(Landroid/app/Activity;)V", []),
        ("onStop", "()V"):
            ("onActivityStopped", "(Landroid/app/Activity;)V", []),
    }

    def _fire_activity_callbacks(self, name, sig, activity):
        """Registrierte ActivityLifecycleCallbacks beim Durchlauf der Activity-Phase real aufrufen."""
        info = self._ACT_CB.get((name, sig))
        if info is None or self.stubs is None:
            return
        cbname, cbsig, extra = info
        for cb in list(getattr(self.stubs, "activity_callbacks", []) or []):
            if isinstance(cb, JObject):
                self._invoke_on(cb, cbname, cbsig, [cb, activity] + extra)

    def _invoke_on(self, obj, name, sig, args):
        """Methode auf einem Objekt robust aufrufen (lazy-load + Superklassen-Dispatch, fehlertolerant)."""
        cls = obj.cls if isinstance(obj, JObject) else None
        if cls is None:
            return
        md = None
        try:
            md = self.vm._resolve_method(cls, cls, name, sig)
        except Exception:
            md = None
        if md is None:
            for _hk in list(getattr(self.vm, "_method_hooks", ()) or ()):
                try:
                    ok_h, _ = _hk(cls, cls, name, sig, list(args), False, self.vm)
                    if ok_h:
                        return
                except Exception:
                    pass
            return
        try:
            self.vm._run_mdef(md, list(args))
        except Exception:
            pass

    def _extra_args(self, name, extra):
        """Zusatzargumente einer Lifecycle-Methode: echter Context fuer attachBaseContext,
        sonst None (z. B. onCreate(Bundle): savedInstanceState = null, wie beim echten Erststart)."""
        if extra <= 0:
            return []
        if name == "attachBaseContext" and self._context is not None:
            return [self._context] + [None] * (extra - 1)
        return [None] * extra

    def _missing(self):
        return list(getattr(self.vm, "missing", []) or []) + \
               (list(self.stubs.missing) if self.stubs else [])

    # ---- oeffentlich ------------------------------------------------------
    def run(self, entry, sequence=None):
        """Eine Entry-Klasse (Deskriptor) durch eine Sequenz treiben (Default: Activity-artig).
        Rueckwaertskompatibel: `report` enthaelt die flachen called/skipped/errors der Entry-Phase."""
        seq = sequence or self.SEQ_ACTIVITY
        phase = self._run_class(entry, seq, role="entry")
        report = {"entry": entry, "called": phase["called"], "skipped": phase["skipped"],
                  "errors": phase["errors"], "phases": [phase], "missing": self._missing()}
        return report

    def _manifest(self, studio_root=None):
        root = studio_root or getattr(self.provider, "root", None)
        if not root:
            return None
        try:
            from core.java.manifest import parse_manifest
            return parse_manifest(root)
        except Exception:
            return None

    def _as_desc(self, target):
        if not target:
            return None
        t = str(target).strip()
        if not t:
            return None
        return t if (t.startswith("L") and t.endswith(";")) else ("L" + t.replace(".", "/") + ";")

    def _drain_runnables(self, max_items=64):
        """Fuehrt geplante Handler/View/UI-Runnables aus pending_runnables deterministisch aus."""
        if self.stubs is None:
            return
        fl = getattr(self.stubs, "_flush_fragment_transactions", None)
        if callable(fl):
            fl()
        q = getattr(self.stubs, "pending_runnables", None)
        if not q:
            return
        count = 0
        deferred = []
        try:
            while q and count < max_items:
                r = q.pop(0)
                if not isinstance(r, JObject):
                    continue
                if r.fields.get("_defer_until_activity") and getattr(self.stubs, "_current_activity", None) is None:
                    deferred.append(r)
                    continue
                count += 1
                if r.fields.get("_bg_thread") and self.vm is not None:
                    old_budget = self.vm.step_budget
                    cap = self.vm.steps + 50_000
                    self.vm.step_budget = min(old_budget, cap) if old_budget is not None else cap
                    try:
                        self._invoke_on(r, "run", "()V", [r])
                    finally:
                        self.vm.step_budget = old_budget
                else:
                    self._invoke_on(r, "run", "()V", [r])
        finally:
            if deferred:
                q[0:0] = deferred

    def _maybe_finish_empty_onboarding(self):
        """Wenn ein Splash-Uebergang auf einem leeren ViewPager2-Onboarding mit done/skip-onClick
        landet, diesen Abschluss-Callback ausfuehren, damit die Haupt-Activity sichtbar wird."""
        if self.stubs is None:
            return
        tree = getattr(self.stubs, "view_tree", None)
        act = getattr(self.stubs, "_current_activity", None)
        if tree is None or getattr(tree, "root", None) is None or not isinstance(act, JObject):
            return
        has_empty_pager = any(
            "ViewPager" in (getattr(n, "cls", "") or "") and not getattr(n, "children", ())
            for n in tree.nodes()
        )
        if not has_empty_pager:
            return
        for n in tree.nodes():
            oc = (getattr(n, "props", None) or {}).get("onClick")
            if oc in ("done", "skip", "finish", "continue"):
                view_obj = JObject("Landroid/view/View;", {"$view": n})
                self._invoke_on(act, oc, "(Landroid/view/View;)V", [act, view_obj])
                return

    def push_current_activity(self):
        """Aktuelle Activity + ViewTree-Snapshot auf den Back-Stack legen."""
        if self.stubs is None:
            return False
        cur = getattr(self.stubs, "_current_activity", None)
        tree = getattr(self.stubs, "view_tree", None)
        if not isinstance(cur, JObject) or tree is None or getattr(tree, "root", None) is None:
            return False
        entry = {
            "activity": cur,
            "cls": cur.cls,
            "intent": cur.fields.get("_intent"),
            "root": tree.root,
            "compose_recomp": getattr(tree, "_compose_recompose", None),
        }
        if self.activity_stack and self.activity_stack[-1].get("activity") is cur:
            self.activity_stack[-1] = entry
        else:
            self.activity_stack.append(entry)
        return True

    def _restore_activity_entry(self, entry):
        if not entry or self.stubs is None:
            return False
        act = entry.get("activity")
        self.stubs._current_activity = act
        self.stubs._current_activity_obj = act
        self.stubs._activity_finished = False
        if isinstance(act, JObject):
            act.fields.pop("_finishing", None)
        tree = getattr(self.stubs, "view_tree", None)
        if tree is not None:
            tree._compose_recompose = entry.get("compose_recomp")
            if entry.get("root") is not None:
                tree.set_root(entry["root"])
            else:
                tree.touch()
        return True

    def _deliver_pending_result(self, result_code, result_data):
        if self.stubs is None or self.vm is None:
            return False
        cb = getattr(self.stubs, "_pending_act_result_cb", None)
        self.stubs._pending_act_result_cb = None
        if isinstance(cb, JObject):
            ar = JObject("Landroidx/activity/result/ActivityResult;",
                         {"mResultCode": int(result_code or 0), "mData": result_data})
            self._invoke_on(cb, "onActivityResult", "(Ljava/lang/Object;)V", [cb, ar])
            return True
        return False

    def pop_activity(self):
        """Vorherige Activity vom Back-Stack wiederherstellen."""
        if not self.activity_stack or self.stubs is None:
            return False
        return self._restore_activity_entry(self.activity_stack.pop())

    def navigate_to_pending(self, max_hops=3):
        """Anstehendes startActivity() oder finish() auf dem Activity-Stack ausfuehren."""
        if self.stubs is None:
            return False
        moved = False
        for _ in range(max_hops):
            nxt = self._as_desc(getattr(self.stubs, "_pending_nav_target", None))
            intent = getattr(self.stubs, "_pending_nav_intent", None)
            self.stubs._pending_nav_target = None
            self.stubs._pending_nav_intent = None
            finished = bool(getattr(self.stubs, "_activity_finished", False))
            self.stubs._activity_finished = False
            rc = int(getattr(self.stubs, "_last_result_code", 0) or 0)
            rdata = getattr(self.stubs, "_last_result_data", None)
            self.stubs._last_result_code = 0
            self.stubs._last_result_data = None
            if not nxt:
                if finished:
                    if self.activity_stack:
                        moved = self.pop_activity() or moved
                    moved = self._deliver_pending_result(rc, rdata) or moved
                break
            flags = int(intent.fields.get("_flags", 0) or 0) if isinstance(intent, JObject) else 0
            clear_task = bool(flags & 0x00008000)   # FLAG_ACTIVITY_CLEAR_TASK
            clear_top = bool(flags & 0x04000000)    # FLAG_ACTIVITY_CLEAR_TOP
            single_top = bool(flags & 0x20000000)   # FLAG_ACTIVITY_SINGLE_TOP
            cur_act = getattr(self.stubs, "_current_activity", None)
            cur_cls = self._as_desc(cur_act.cls) if isinstance(cur_act, JObject) else None
            if single_top and not clear_task and not finished and isinstance(cur_act, JObject) and cur_cls == nxt:
                if intent is not None:
                    cur_act.fields["_intent"] = intent
                self._invoke_on(cur_act, "onNewIntent", "(Landroid/content/Intent;)V", [cur_act, intent])
                self._invoke_on(cur_act, "onResume", "()V", [cur_act])
                self._drain_runnables()
                tree = getattr(self.stubs, "view_tree", None)
                if tree is not None:
                    tree.touch()
                moved = True
                continue
            if clear_task:
                self.activity_stack.clear()
            else:
                existing_idx = next(
                    (i for i in range(len(self.activity_stack) - 1, -1, -1)
                     if self._as_desc(self.activity_stack[i].get("cls")) == nxt),
                    None,
                )
                if existing_idx is not None:
                    target_entry = self.activity_stack[existing_idx]
                    self.activity_stack[:] = self.activity_stack[:existing_idx]
                    if clear_top and single_top and not finished:
                        self._restore_activity_entry(target_entry)
                        act_r = getattr(self.stubs, "_current_activity", None)
                        if isinstance(act_r, JObject):
                            if intent is not None:
                                act_r.fields["_intent"] = intent
                            self._invoke_on(act_r, "onNewIntent", "(Landroid/content/Intent;)V", [act_r, intent])
                            self._invoke_on(act_r, "onResume", "()V", [act_r])
                        self._drain_runnables()
                        tree = getattr(self.stubs, "view_tree", None)
                        if tree is not None:
                            tree.touch()
                        moved = True
                        continue
                elif not finished:
                    self.push_current_activity()
            self._run_class(nxt, self.SEQ_ACTIVITY, role="activity", intent=intent)
            self._drain_runnables()
            tree = getattr(self.stubs, "view_tree", None)
            if tree is not None:
                tree.touch()
            moved = True
        return moved

    def handle_back(self):
        """Hardware-Zurueck: ruft Activity.onBackPressed()V ueber Vererbung auf und pflegt Back-Stack."""
        if self.stubs is None:
            return False
        fctrl = getattr(getattr(self.stubs, "view_tree", None), "_flutter_ctrl", None)
        if fctrl is not None and fctrl.handle_back():
            return True
        try:
            from core.java.nav_rx_paging_g8 import pop_compose_nav_if_possible
            if pop_compose_nav_if_possible(self.stubs):
                return True
        except Exception:
            pass
        act = getattr(self.stubs, "_current_activity", None)
        if not isinstance(act, JObject):
            return self.pop_activity()
        self.stubs._pending_nav_target = None
        self.stubs._pending_nav_intent = None
        self.stubs._activity_finished = False
        if self.vm is not None and hasattr(self.vm, "invoke_virtual"):
            try:
                self.vm.invoke_virtual(act, "onBackPressed", "()V", (act,))
            except Exception as e:
                emsg = str(e)
                if type(e).__name__ in ("DebugStop", "DalvikUnknownOpcode") or any(
                    k in emsg for k in ("StepBudget", "WallBudget", "StackOverflow", "max_depth")
                ):
                    raise
        self._drain_runnables()
        if getattr(self.stubs, "_pending_nav_target", None) or getattr(self.stubs, "_activity_finished", False):
            return self.navigate_to_pending()
        return False

    def handle_home(self):
        """Home-Button: Root-Activity vom Stack wiederherstellen."""
        if self.stubs is None:
            return False
        fctrl = getattr(getattr(self.stubs, "view_tree", None), "_flutter_ctrl", None)
        if fctrl is not None and fctrl.handle_home():
            return True
        if not self.activity_stack:
            return False
        root_entry = self.activity_stack[0]
        self.activity_stack.clear()
        return self._restore_activity_entry(root_entry)

    def run_app(self, studio_root=None, application=None, launcher=None):
        """GENERISCHER App-Lauf: Application-Phase (falls vorhanden) + Launcher-Activity-Phase.
        Einstieg aus dem Manifest (Application android:name + MAIN/LAUNCHER-Activity), sofern nicht
        explizit uebergeben. Report fuehrt beide Phasen + globales missing."""
        self.activity_stack.clear()
        if self.stubs is not None:
            self.stubs._pending_nav_target = None
            self.stubs._pending_nav_intent = None
            self.stubs._activity_finished = False
            if hasattr(self.stubs, "_ensure_view_vm_hook"):
                try:
                    self.stubs._ensure_view_vm_hook(self.vm)
                except Exception:
                    pass
            try:
                from core.java.sqlite_prefs_bridge import attach_g3_vm_hook
                attach_g3_vm_hook(self.stubs, self.vm)
            except Exception:
                pass
            for _hmod, _hfn in (
                ("core.java.engine_stubs_g14", "attach_g14_vm_hook"),
                ("core.java.nav_rx_paging_g8", "attach_g8_vm_hook"),
            ):
                try:
                    _m = __import__(_hmod, fromlist=[_hfn])
                    getattr(_m, _hfn)(self.stubs, self.vm)
                except Exception:
                    pass
            try:
                from core.java.fs_bridge import _ensure_fs_stream_hook
                _ensure_fs_stream_hook(self.stubs)
            except Exception:
                pass
            self.stubs._active_runner = self
        man = self._manifest(studio_root)
        if man is not None:
            application = application or man.application
            launcher = launcher or man.entry()
            # Geraete-Profil mit dem echten Paketname speisen (generisch, kein Hardcode)
            if self.stubs is not None:
                self.stubs._loaded_manifest = man
                self.stubs._manifest_meta_data = dict(getattr(man, "meta_data", {}) or {})
                if getattr(man, "package", None):
                    try:
                        from core.java.device_profile import DeviceProfile
                        self.stubs.set_profile(DeviceProfile(package=man.package), vm=self.vm)
                    except Exception:
                        pass
        report = {
            "package": getattr(man, "package", None),
            "application": application,
            "launcher": launcher,
            "activity": launcher,
            "phases": [],
            "missing": [],
        }
        if application:
            report["phases"].append(self._run_class(application, self.SEQ_APPLICATION, role="application"))
            self._drain_runnables()
        if launcher:
            visited = {launcher}
            current = launcher
            report["phases"].append(self._run_class(current, self.SEQ_ACTIVITY, role="activity"))
            self._drain_runnables()
            hops = 0
            while self.stubs is not None and hops < 3:
                nxt = self._as_desc(getattr(self.stubs, "_pending_nav_target", None))
                intent = getattr(self.stubs, "_pending_nav_intent", None)
                if self.stubs is not None:
                    self.stubs._pending_nav_target = None
                    self.stubs._pending_nav_intent = None
                if nxt and nxt not in visited:
                    visited.add(nxt)
                    if not getattr(self.stubs, "_activity_finished", False):
                        self.push_current_activity()
                    self.stubs._activity_finished = False
                    current = nxt
                    report["activity"] = current
                    report["phases"].append(self._run_class(current, self.SEQ_ACTIVITY, role="activity", intent=intent))
                    self._drain_runnables()
                    if hops == 0:
                        self._maybe_finish_empty_onboarding()
                        self._drain_runnables()
                    hops += 1
                else:
                    break
        if not application and not launcher:
            report["phases"].append({"class": None, "role": "none", "called": [], "skipped": [],
                                     "errors": [{"m": "*", "sig": "*", "err": "kein Einstieg im Manifest"}]})
        # Flutter-In-Phone-UI direkt in FlutterView einhängen (statt leerer Surface-Huelle / Textwand)
        try:
            rt_root = studio_root or getattr(self.provider, "root", None) or ""
            vtree = getattr(self.stubs, "view_tree", None) if self.stubs is not None else None
            if rt_root and vtree is not None:
                from core.java.app_runtime import detect_runtime, is_unrendered_surface_shell
                if (vtree.root is None or is_unrendered_surface_shell(vtree)) and detect_runtime(rt_root)[0] == "Flutter":
                    from core.java.flutter_app_producer import mount_flutter_app_into_tree
                    mount_flutter_app_into_tree(vtree, rt_root, stubs=self.stubs)
        except Exception:
            pass
        report["missing"] = self._missing()
        return report
