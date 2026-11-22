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

    def resolve_phased(self, wants, rcls, cls, name, sig, args, static):
        if hasattr(self.stubs, "resolve_phased"):
            return self.stubs.resolve_phased(wants, rcls, cls, name, sig, args, static)
        for want in wants:
            h, r = self.stubs.resolve(want, name, sig, args, static)
            if h:
                return h, r
        return False, 0


def _is_fatal_vm_exc(exc, include_budget=False):
    from core.java.deviation_journal import DebugStop
    if isinstance(exc, DebugStop):
        return True
    if include_budget:
        from core.java.dalvik import DalvikUnknownOpcode, VmBudgetExceeded
        return isinstance(exc, (DalvikUnknownOpcode, VmBudgetExceeded))
    return False


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
            self.stubs._active_runner = self
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
        if role == "application" and self.stubs is not None and isinstance(getattr(self.stubs, "application", None), JObject) and self.stubs.application.cls == descriptor:
            this = self.stubs.application
        else:
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
                tree.root = None
                tree._compose_recompose = None
                rst = getattr(tree, "_reset_compose_for_new_activity", None)
                if callable(rst):
                    rst()
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
                if role == "activity" and name == "onCreate" and self.stubs is not None:
                    self.stubs._in_activity_oncreate = True
                try:
                    self.vm._run_mdef(md, list(args))         # md ggf. aus Superklasse, this = Subklasse
                finally:
                    if role == "activity" and name == "onCreate" and self.stubs is not None:
                        self.stubs._in_activity_oncreate = False
                phase["called"].append({"m": name, "sig": sig, "in": md.cls})
                if role == "activity":
                    self._fire_activity_callbacks(name, sig, this)
                    if self.stubs is not None and (
                        getattr(self.stubs, "_activity_finished", False)
                        or bool(this.fields.get("_finishing"))
                    ):
                        txs = getattr(self.stubs, "_pending_fragment_txs", None)
                        if isinstance(txs, list):
                            txs.clear()
                        break
                    if name in ("onCreate", "onResume") and self.stubs is not None:
                        fl = getattr(self.stubs, "_flush_fragment_transactions", None)
                        if callable(fl):
                            fl()
                        if name == "onCreate":
                            bind_menu = getattr(self.stubs, "_bind_activity_options_menu", None)
                            if callable(bind_menu):
                                bind_menu(this)
            except Exception as e:                       # noqa
                if _is_fatal_vm_exc(e):
                    raise
                phase["errors"].append({"m": name, "sig": sig, "err": repr(e)})
        if role == "activity" and self.stubs is not None and not (
            getattr(self.stubs, "_activity_finished", False) or bool(this.fields.get("_finishing"))
        ):
            self._recompose_if_needed()
        return phase

    def _recompose_if_needed(self):
        if self.stubs is None or getattr(self, "_in_recompose_sync", False):
            return
        if getattr(self.stubs, "_pending_nav_target", None) or getattr(self.stubs, "_activity_finished", False):
            return
        tree = getattr(self.stubs, "view_tree", None)
        rc_dirty = getattr(tree, "recompose_if_dirty", None) if tree is not None else None
        if not callable(rc_dirty):
            return
        from core.java.dalvik import VmBudgetExceeded
        old_b = getattr(self.vm, "step_budget", None) if self.vm is not None else None
        cur_s = int(getattr(self.vm, "steps", 0) or 0) if self.vm is not None else 0
        self._in_recompose_sync = True
        try:
            if isinstance(old_b, int) and old_b > 0:
                self.vm.step_budget = max(old_b, cur_s + 900_000)
            try:
                rc_dirty()
            except VmBudgetExceeded:
                if not (tree.root is not None and tree.root.children):
                    raise
        finally:
            if isinstance(old_b, int) and old_b > 0 and self.vm is not None:
                rem = max(0, old_b - cur_s)
                self.vm.step_budget = max(old_b, int(getattr(self.vm, "steps", 0) or 0) + min(rem, 100_000))
            self._in_recompose_sync = False

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
                except Exception as _eh:
                    if _is_fatal_vm_exc(_eh):
                        raise
            if cls == "Landroid/os/Handler$MessageDispatchRunnable;" and self.stubs is not None:
                try:
                    self.stubs.resolve(cls, name, sig, list(args), False, allow_default=False)
                except Exception as _es:
                    if _is_fatal_vm_exc(_es):
                        raise
            return
        try:
            self.vm._run_mdef(md, list(args))
        except Exception as _ei:
            if _is_fatal_vm_exc(_ei):
                raise

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
                if r.cls == "Landroid/os/Handler$MessageDispatchRunnable;":
                    orig_d = int(r.fields.get("_orig_delay_ms", 0) or 0)
                    due_ms = int(r.fields.get("_due_ms", 0) or 0)
                    clock_ms = int(getattr(self.stubs, "_clock_ms", 0) or 0)
                    if orig_d >= 1000 and clock_ms < due_ms:
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
        if count > 0:
            self._recompose_if_needed()

    def _maybe_finish_empty_onboarding(self):
        """Wenn ein Splash-Uebergang auf einem leeren ViewPager2-Onboarding mit done/skip-onClick
        landet, diesen Abschluss-Callback ausfuehren, damit die Haupt-Activity sichtbar wird."""
        if self.stubs is None:
            return
        seam_cfg = getattr(self, "seam_config", None) or getattr(self.stubs, "_seam_config", None)
        if seam_cfg is not None and not getattr(seam_cfg, "auto_finish_empty_onboarding", True):
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
                step = int(getattr(self.vm, "steps", 0) or 0) if self.vm is not None else 0
                dev = getattr(self.vm, "deviations", None) if self.vm is not None else None
                if dev is not None and callable(getattr(dev, "record", None)):
                    dev.record(
                        "auto_onboarding_skip",
                        site=f"{act.cls}->{oc}",
                        detail="auto-clicked onboarding completion on empty ViewPager",
                        step=step,
                    )
                dbg = getattr(self.vm, "_dbg", None) if self.vm is not None else None
                if dbg is not None and isinstance(getattr(dbg, "markers", None), list):
                    dbg.markers.append({"step": step, "kind": "auto_onboarding_skip", "label": f"auto_onboarding_skip:{act.cls}->{oc}"})
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
        sv_comp = getattr(tree, "_compose_save_state", None)
        entry = {
            "activity": cur,
            "cls": cur.cls,
            "intent": cur.fields.get("_intent"),
            "root": tree.root,
            "compose_recomp": getattr(tree, "_compose_recompose", None),
            "compose_state": sv_comp() if callable(sv_comp) else None,
            "compose_nav_controller": getattr(self.stubs, "_compose_nav_controller", None),
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
        if "compose_nav_controller" in entry:
            self.stubs._compose_nav_controller = entry.get("compose_nav_controller")
        if isinstance(act, JObject):
            act.fields.pop("_finishing", None)
        tree = getattr(self.stubs, "view_tree", None)
        if tree is not None:
            rs_comp = getattr(tree, "_compose_restore_state", None)
            if entry.get("compose_state") is not None and callable(rs_comp):
                rs_comp(entry.get("compose_state"))
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

    def _cancel_activity_coroutines(self, act):
        if not isinstance(act, JObject) or self.stubs is None:
            return
        cancel_fn = getattr(self.stubs, "_cancel_owner_coroutines", None)
        if callable(cancel_fn):
            try:
                cancel_fn(act)
            except Exception:
                pass

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
                    self._cancel_activity_coroutines(getattr(self.stubs, "_current_activity", None))
                    if self.activity_stack:
                        moved = self.pop_activity() or moved
                    moved = self._deliver_pending_result(rc, rdata) or moved
                break
            man = getattr(self.stubs, "_loaded_manifest", None)
            lmodes = getattr(man, "launch_modes", None) or {}
            lmode = lmodes.get(nxt, "")
            flags = int(intent.fields.get("_flags", 0) or 0) if isinstance(intent, JObject) else 0
            clear_task = bool(flags & 0x00008000)   # FLAG_ACTIVITY_CLEAR_TASK
            clear_top = bool(flags & 0x04000000) or lmode in ("singleTask", "singleInstance")
            single_top = bool(flags & 0x20000000) or lmode in ("singleTop", "singleTask", "singleInstance")
            cur_act = getattr(self.stubs, "_current_activity", None)
            cur_cls = self._as_desc(cur_act.cls) if isinstance(cur_act, JObject) else None
            if finished:
                self._cancel_activity_coroutines(cur_act)
            if single_top and not clear_task and not finished and isinstance(cur_act, JObject) and cur_cls == nxt:
                if intent is not None:
                    cur_act.fields["_intent"] = intent
                self._invoke_on(cur_act, "onNewIntent", "(Landroid/content/Intent;)V", [cur_act, intent])
                self._invoke_on(cur_act, "onResume", "()V", [cur_act])
                self._drain_runnables()
                self._recompose_if_needed()
                tree = getattr(self.stubs, "view_tree", None)
                if tree is not None:
                    tree.touch()
                moved = True
                continue
            if clear_task:
                self._cancel_activity_coroutines(cur_act)
                for _se in list(self.activity_stack):
                    if isinstance(_se, dict):
                        self._cancel_activity_coroutines(_se.get("activity"))
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
                    if (clear_top or single_top) and not finished:
                        self._restore_activity_entry(target_entry)
                        act_r = getattr(self.stubs, "_current_activity", None)
                        if isinstance(act_r, JObject):
                            if intent is not None:
                                act_r.fields["_intent"] = intent
                            self._invoke_on(act_r, "onNewIntent", "(Landroid/content/Intent;)V", [act_r, intent])
                            self._invoke_on(act_r, "onResume", "()V", [act_r])
                        self._drain_runnables()
                        self._recompose_if_needed()
                        tree = getattr(self.stubs, "view_tree", None)
                        if tree is not None:
                            tree.touch()
                        moved = True
                        continue
                elif not finished:
                    self.push_current_activity()
            self.stubs._compose_nav_controller = None
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
        for _ca in ("_flutter_ctrl", "_rn_ctrl", "_hybrid_ctrl"):
            _ic = getattr(getattr(self.stubs, "view_tree", None), _ca, None)
            if _ic is not None and _ic.handle_back():
                return True
        try:
            from core.java.nav_rx_paging_g8 import pop_compose_nav_if_possible
            if pop_compose_nav_if_possible(self.stubs):
                return True
        except Exception as _ehw:
            if _is_fatal_vm_exc(_ehw):
                raise
        act = getattr(self.stubs, "_current_activity", None)
        if not isinstance(act, JObject):
            return self.pop_activity()
        self.stubs._pending_nav_target = None
        self.stubs._pending_nav_intent = None
        self.stubs._activity_finished = False
        self.stubs._back_callback_consumed = False
        if self.vm is not None and hasattr(self.vm, "invoke_virtual"):
            try:
                self.vm.invoke_virtual(act, "onBackPressed", "()V", (act,))
            except Exception as e:
                if _is_fatal_vm_exc(e, include_budget=True):
                    raise
        self._drain_runnables()
        if getattr(self.stubs, "_pending_nav_target", None) or getattr(self.stubs, "_activity_finished", False):
            return self.navigate_to_pending()
        if getattr(self.stubs, "_back_callback_consumed", False):
            self._recompose_if_needed()
            return True
        if self.activity_stack:
            return self.pop_activity()
        return False

    def handle_home(self):
        """Home-Button: Root-Activity vom Stack wiederherstellen."""
        if self.stubs is None:
            return False
        for _ca in ("_flutter_ctrl", "_rn_ctrl", "_hybrid_ctrl"):
            _ic = getattr(getattr(self.stubs, "view_tree", None), _ca, None)
            if _ic is not None and _ic.handle_home():
                return True
        if not self.activity_stack:
            return False
        root_entry = self.activity_stack[0]
        self.activity_stack.clear()
        return self._restore_activity_entry(root_entry)

    def _run_init_providers(self, man, application_desc=None):
        """Initialisiert Bootstrap-ContentProvider (z. B. FirebaseInitProvider) vor Application.onCreate."""
        if man is None or self.stubs is None or self.vm is None:
            return
        from core.java.objects import JString
        providers = getattr(man, "providers", None) or []
        if not providers:
            return
        _init_markers = (
            "InitProvider",
            "InitializationProvider",
            "firebaseinitprovider",
            "androidx-startup",
            "ProcessLifecycleOwner",
            "WorkManagerInitializer",
        )
        cands = []
        for prv in providers:
            if not isinstance(prv, dict) or prv.get("enabled") is False:
                continue
            pcls = str(prv.get("cls") or "")
            auth = str(prv.get("authorities") or "")
            if any(mk in pcls or mk in auth for mk in _init_markers):
                cands.append(prv)
        if not cands:
            return
        cands.sort(key=lambda d: int(d.get("init_order", 0) or 0), reverse=True)
        app_cls = application_desc or "Landroid/app/Application;"
        app_obj = getattr(self.stubs, "application", None)
        if not isinstance(app_obj, JObject):
            app_obj = JObject(app_cls, {})
            self.stubs.application = app_obj
        pkg = getattr(man, "package", None) or "com.example.app"
        for entry in cands:
            pcls = entry.get("cls") or ""
            if not pcls:
                continue
            auth = str(entry.get("authorities") or "").split(";", 1)[0].strip()
            methods = self._methods_of(pcls)
            if not methods and getattr(self.provider, "get_class", None) is None:
                continue
            prv_obj = JObject(pcls, {})
            pinfo = JObject("Landroid/content/pm/ProviderInfo;", {
                "authority": JString(auth),
                "packageName": JString(pkg),
                "name": JString(pcls[1:-1].replace("/", ".") if pcls.startswith("L") and pcls.endswith(";") else pcls),
            })
            old_budget = self.vm.step_budget
            cap = self.vm.steps + 150_000
            self.vm.step_budget = min(old_budget, cap) if old_budget is not None else cap
            try:
                init_md = self.vm._resolve_method(pcls, pcls, "<init>", "()V")
                if init_md is not None:
                    self.vm._run_mdef(init_md, [prv_obj])
                att_md = self.vm._resolve_method(pcls, "Landroid/content/ContentProvider;", "attachInfo", "(Landroid/content/Context;Landroid/content/pm/ProviderInfo;)V")
                if att_md is not None:
                    self.vm._run_mdef(att_md, [prv_obj, app_obj, pinfo])
                else:
                    self.stubs.resolve("Landroid/content/ContentProvider;", "attachInfo", "(Landroid/content/Context;Landroid/content/pm/ProviderInfo;)V", [prv_obj, app_obj, pinfo], False)
            except Exception as _ep:
                if _is_fatal_vm_exc(_ep):
                    raise
            finally:
                self.vm.step_budget = old_budget

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
        if man is not None:
            self._run_init_providers(man, application_desc=application)
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
        # Flutter/React-Native-In-Phone-UI direkt einhaengen (statt leerer Surface-Huelle / RUNTIME-GRENZE)
        try:
            rt_root = studio_root or getattr(self.provider, "root", None) or ""
            vtree = getattr(self.stubs, "view_tree", None) if self.stubs is not None else None
            if rt_root and vtree is not None:
                from core.java.app_runtime import mount_inphone_runtime_if_needed
                mount_inphone_runtime_if_needed(vtree, rt_root, stubs=self.stubs)
        except Exception as _ehw:
            if _is_fatal_vm_exc(_ehw):
                raise
        vtree = getattr(self.stubs, "view_tree", None) if self.stubs is not None else None
        if vtree is not None and hasattr(vtree, "ensure_layout"):
            try:
                vtree.ensure_layout()
            except Exception as _el:
                if _is_fatal_vm_exc(_el):
                    raise
        report["missing"] = self._missing()
        return report
