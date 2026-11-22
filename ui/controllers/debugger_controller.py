# -*- coding: utf-8 -*-
"""DebuggerController — Logik des Debugger-Hauptreiters (X1/MS-6).

Haelt EINE Live-`JavaDebugger`-Sitzung und allen Zustand (Quelle, Breakpoints, Zone, Entry, Logs).
Die Views (Dock-Framework) sind MVC-Renderer: sie binden sich beim Realisieren an diesen Controller
(`bind_*`) und werden aus dem Zustand aktualisiert (`_refresh`). Debugger-Operationen laufen im
Worker-Thread, UI-Updates ueber `app.after`. Kern aus `core.java` (zielagnostisch, additiv).
"""
from __future__ import annotations

import json
import os
import glob
import threading

DEFAULT_SMALI = (
    "# Smali hier einfuegen oder per 'Aus Studio laden' holen\n"
    ".method public static dbl(I)I\n    .locals 1\n"
    "    add-int v0, p0, p0\n    return v0\n.end method\n"
)


class DebuggerController:
    def __init__(self, app=None):
        self.app = app
        self.source = DEFAULT_SMALI
        self.args = "[]"
        self.name = ""
        self.entry_stop = False
        self.tolerant = True     # Fehler in Aufrufen tolerieren (Stepping ueberlebt Shims)
        self.apply_patches = False  # aktive Smali-Patches vor dem Lauf anwenden (Sandbox, MS-5)
        self.overlay_smali = True
        self.overlay_libcarve = True
        self.overlay_libforge = True
        self.bps = []
        self.bp_meta = {}        # pc -> {cond,hit,one_shot}
        self.app_bps = {}        # (cls, name, sig, pc|None) -> {cond,hit,one_shot} (klassenuebergreifend)
        self.native_bps = []     # [{"lib": str, "vaddr": int, "name": str}]
        self.bookmarks = set()   # Timeline-Lesezeichen (step-Indizes)
        self.watches = []        # Watch-Ausdruecke
        self.zone_root = ""
        self.zone = "original"
        self.relpath = ""
        self.app_entry = ""
        self._jdb = None
        self._jdb_sig = None
        self._session = None   # DebugSession (Fusion Java+nativ), sonst None
        self.aps = []            # Aktionspunkt-Specs (bedingte/funktionale/getriggerte Manipulation)
        self.glass_tree = None   # View-Baum des gläsernen App-Laufs (Anzeige)
        self.glass_surface = None
        self._hermes_client = None
        self.net_log = None      # NetLog des gläsernen Laufs (Netz-Säule K-Track)
        self._net_responder = None  # Responder (None = Record-Modus, leeres 200)
        self.net_mode = "mock"      # AP1.5: "mock" | "live" | "db_replay"
        self._api_db = None
        self._mock_hint_urls = set()
        self.native_fusion = True   # gläserner Lauf mit echter arm64-Engine (Java<->nativ in einem Lauf)
        self.record_timeline = False  # AP1.1: Time-Travel/Reverse aufzeichnen (Default AUS = schnell)
        self.record_regs = False      # AP1.1: Register-Kopie je Schritt in der Timeline
        self.jni_aps = []           # Grenz-Aktionspunkt-Specs (Java<->nativ)
        self._fusion = None         # {eng,jni,table,bridge,hook} des gläsernen Laufs
        self.jni_log = None         # JniBoundaryLog des gläsernen Laufs
        self._state_lock = threading.RLock()
        self._appdata_lock = threading.RLock()
        self._worker_thread = None
        self._main_thread_id = threading.get_ident()
        self._busy = False
        self._run_seq = 0
        self._pause_pending = False
        self._stop_pending = False
        self._last_start_mode = ""
        self._log_lines = []
        # gebundene Widgets des aktuellen Layouts
        self._w = {}
        # glaeserner Editor: Quelltext-Modell + View-Hook
        self.view = None
        self._srcmap = []
        self._line_by_pc = {}
        self._pc_by_line = {}
        self._last_pc = None
        self._loaded_cls = None
        self._active_stack_idx = 0
        self.inspect_mode = "dalvik"   # "dalvik" | "arm64"
        self.stop_on_exception = False
        self.exception_unhandled_only = True
        self.flight_recorder_enabled = False
        self._heartbeat_id = None
        self._stubs = None
        self._clock_preset = None
        self._budget_override = None
        try:
            from core.java.net_recorder import NetCondition
            self.net_condition = NetCondition()
        except Exception:
            self.net_condition = None
        # Paket 1: 2-Ebenen Seam-Konfiguration + Instanzen (Fork/Merge) + Projekt-Snapshots
        try:
            _cfg_map = getattr(getattr(self.app, "cfg", None), "config", None)
            _init_ps = bool(_cfg_map.get("DEBUGGER_PERSIST_SESSION", False)) if isinstance(_cfg_map, dict) else False
        except Exception:
            _init_ps = False
        self.persist_session = _init_ps
        try:
            from core.java.seam_config import SeamConfig
            from core.java.glass_runner import InstanceState, ProjectSnapshotService
            self.seam_config = SeamConfig.from_profile("self_cache")
            self.seam_config.persist_appdata = bool(self.persist_session)
            self.instance_state = InstanceState(zone=self.zone, apply_patches=self.apply_patches)
            self.snapshot_service = ProjectSnapshotService(base_dir=self._base_dir())
        except Exception:
            self.seam_config = None
            self.instance_state = None
            self.snapshot_service = None
        self._active_pkg = ""
        try:
            from core.application.event_bus import EventBus
            EventBus.subscribe("GLASS_SURFACE", self._on_surface_event)
        except Exception:
            pass

    def on_app_changed(self, pkg=None):
        """Setzt die Debugger-Session und Editor/View-Bindungen beim App-Wechsel zurueck."""
        if self._jdb is not None:
            try:
                self.stop()
            except Exception:
                pass
        self._jdb = None
        self._jdb_sig = None
        self._loaded_cls = None
        self._active_pkg = pkg or ""
        self.zone_root = ""
        self.relpath = ""
        self.app_entry = ""
        self.name = ""
        self.source = DEFAULT_SMALI
        self.args = "[]"
        self._srcmap = []
        self._line_by_pc = {}
        self._pc_by_line = {}
        self._last_pc = None
        self._active_stack_idx = 0
        self.glass_tree = None
        self.glass_surface = None
        self._hermes_client = None
        self.net_log = None
        self.bps = []
        self.bp_meta = {}
        self.app_bps = {}
        self.bookmarks = set()
        if getattr(self, "instance_state", None) is not None:
            self.instance_state.package = pkg or ""
        v = getattr(self, "view", None)
        if v is not None:
            for attr, val in (("_zone_root_var", ""), ("_relpath_var", ""),
                              ("_app_entry_var", ""), ("_methode_var", "")):
                tv = getattr(v, attr, None)
                if tv is not None:
                    try:
                        tv.set(val)
                    except Exception:
                        pass
            ed = self._wid("editor")
            if ed is not None:
                try:
                    ed.delete("1.0", "end")
                    ed.insert("1.0", self.source)
                except Exception:
                    pass
            if hasattr(v, "rebuild_code_model"):
                try:
                    v.rebuild_code_model()
                except Exception:
                    pass
        self._refresh({"mode": "idle"})

    # ---- Binding der Views ------------------------------------------------
    def bind(self, key, widget):
        self._w[key] = widget

    def _wid(self, key):
        w = self._w.get(key)
        try:
            if w is not None and w.winfo_exists():
                return w
        except Exception:
            return None
        return None

    def _after(self, fn):
        if not callable(fn):
            return
        app = self.app
        if app is not None and hasattr(app, "after"):
            try:
                app.after(0, fn)
                return
            except Exception:
                # Nie Tk aus Worker-Threads direkt anfassen (M4).
                if threading.get_ident() != getattr(self, "_main_thread_id", -1):
                    return
                return
        fn()

    # ---- Logging / Console ------------------------------------------------
    def _log(self, msg):
        """Logging in die globale Main Console (app.log); Fallback: interner Puffer."""
        line = "[Debugger] %s" % msg
        self._log_lines.append(line)
        self._log_lines = self._log_lines[-500:]
        if self.app is not None and hasattr(self.app, "log"):
            try:
                self.app.log(line); return
            except Exception:
                pass

    # ---- EventBus / Auto-Source-Sync / Live-State --------------------------
    def _on_surface_event(self, data):
        d = data or {}
        if d.get("surface") is not None:
            self.glass_surface = d["surface"]
        if d.get("client") is not None:
            self._hermes_client = d["client"]

    def _start_heartbeat(self):
        self._stop_heartbeat()
        if self.app is None or not hasattr(self.app, "after"):
            return
        def _tick():
            self._heartbeat_id = None
            if not getattr(self, "_busy", False):
                return
            try:
                self._refresh()
                if self.glass_tree is not None:
                    from core.application.event_bus import EventBus
                    st = self._jdb.inspect() if self._jdb else {"mode": "run"}
                    EventBus.publish("GLASS_STATE", {"mode": st.get("mode"), "tree": self.glass_tree})
            except Exception:
                pass
            if getattr(self, "_busy", False) and not getattr(self, "_stop_pending", False):
                try:
                    self._heartbeat_id = self.app.after(400, _tick)
                except Exception:
                    pass
        try:
            self._heartbeat_id = self.app.after(400, _tick)
        except Exception:
            pass

    def _stop_heartbeat(self):
        hid = getattr(self, "_heartbeat_id", None)
        self._heartbeat_id = None
        if hid is not None and self.app is not None and hasattr(self.app, "after_cancel"):
            try:
                self.app.after_cancel(hid)
            except Exception:
                pass

    def _project_bps_for(self, cls, name, sig):
        """Projiziert die klassenuebergreifenden `app_bps` fuer (cls, name, sig) in `self.bps`."""
        app_bps = getattr(self, "app_bps", None) or {}
        pcs = []
        meta = {}
        for (c, n, s, pc), m in app_bps.items():
            if c == cls and n == name and (not sig or s == sig) and pc is not None:
                pcs.append(int(pc))
                if m:
                    meta[int(pc)] = dict(m)
        if pcs:
            self.bps = sorted(set(pcs))
            self.bp_meta = meta

    def _auto_sync_source(self, cls, name, sig=None, prov=None):
        """Laedt am Breakpoint/Pause/Ende automatisch die Klasse/Methode in den Editor."""
        if not cls:
            return
        cur_target = (getattr(self, "_loaded_cls", None), getattr(self, "name", None),
                      (self._jdb_sig[2] if isinstance(getattr(self, "_jdb_sig", None), tuple) and len(self._jdb_sig) >= 3 else ""))
        if cur_target == (cls, name, sig or "") and getattr(self, "_srcmap", None):
            return
        vm = getattr(self._jdb, "vm", None) if self._jdb is not None else None
        if prov is None and vm is not None:
            prov = getattr(vm, "class_provider", None)
        src = None
        if prov is not None and hasattr(prov, "get_source"):
            try:
                src = prov.get_source(cls)
            except Exception:
                src = None
        if not src and vm is not None:
            # Fallback: synthetischer Smali-Block aus decodierter MethodDef
            md = vm.registry.get((cls, name, sig))
            if md is not None:
                lines = [f".class public {cls}", ".super Ljava/lang/Object;", "",
                         f".method public {name}{sig}"]
                for pc_i, ins in enumerate(getattr(md, "insns", ())):
                    lines.append(f"    # pc={pc_i}")
                    lines.append(f"    {ins[0]}")
                lines.append(".end method\n")
                src = "\n".join(lines)
        if not src:
            return
        self._loaded_cls = cls
        rel = (cls[1:-1] + ".smali") if (cls.startswith("L") and cls.endswith(";")) else cls
        self.source = src if src.endswith("\n") else src + "\n"
        self.name = name or ""
        self.relpath = rel
        self._jdb_sig = (cls, name or "", sig or "")
        self._project_bps_for(cls, name or "", sig or "")
        ed = self._wid("editor")
        if ed is not None:
            try:
                ed.delete("1.0", "end"); ed.insert("1.0", self.source)
            except Exception:
                pass
        mv = getattr(getattr(self, "view", None), "_methode_var", None)
        if mv is not None:
            try: mv.set(self.name)
            except Exception: pass
        rv = getattr(getattr(self, "view", None), "_relpath_var", None)
        if rv is not None:
            try: rv.set(self.relpath)
            except Exception: pass
        self.refresh_source_map()
        v = getattr(self, "view", None)
        if v is not None and hasattr(v, "rebuild_code_model"):
            try:
                v.rebuild_code_model()
            except Exception:
                pass

    def _sync_os_view(self):
        v = getattr(self, "view", None)
        f = getattr(self, "_fusion", None)
        if not f or not f.get("os") or not f.get("eng"):
            return
        try:
            snap = f["os"].dbg_snapshot(f["eng"])
            if v is not None and hasattr(v, "feed_os_snapshot"):
                v.feed_os_snapshot(snap)
            from core.application.event_bus import EventBus
            EventBus.publish("OS_SNAPSHOT", snap)
        except Exception:
            pass

    def _schedule_live_state_push(self, st):
        """Schreibt Live-State & UI-Snapshot entkoppelt im Hintergrund-Thread (blockiert nie die Tk-Mainloop)."""
        self._pending_live_state = dict(st) if isinstance(st, dict) else {"mode": "idle"}
        if getattr(self, "_live_state_pushing", False):
            return
        self._live_state_pushing = True

        def _bg():
            try:
                while True:
                    snap = getattr(self, "_pending_live_state", None)
                    self._pending_live_state = None
                    if snap is None:
                        break
                    self._push_live_state_json(snap)
            finally:
                self._live_state_pushing = False

        threading.Thread(target=_bg, daemon=True).start()

    def _push_live_state_json(self, st):
        """Schreibt aktuellen Live-Zustand fuer MCP/KI-Abgleich nach secrets/probe/out/glass/live_state.json."""
        try:
            out_dir = os.path.join(self._base_dir(), "secrets", "probe", "out", "glass")
            os.makedirs(out_dir, exist_ok=True)
            jdb = self._jdb
            views_snap = []
            try:
                tree = getattr(self, "glass_tree", None)
                if tree is not None and getattr(tree, "root", None) is not None:
                    for nd in tree.nodes()[:80]:
                        views_snap.append({
                            "id": getattr(nd, "vid", None),
                            "cls": getattr(nd, "cls", ""),
                            "text": (getattr(nd, "text", None) or "")[:120],
                            "clickable": bool(getattr(nd, "clickable", False)),
                            "focusable": bool(getattr(nd, "focusable", False)),
                            "visible": bool(getattr(nd, "visible", True)),
                            "bounds": list(getattr(nd, "bounds", ()) or ()),
                        })
            except Exception:
                pass
            regs_snap = []
            try:
                if jdb and hasattr(jdb, "registers"):
                    for idx_r, rval in enumerate(jdb.registers()[:40]):
                        regs_snap.append({"reg": "v%d" % idx_r, "val": rval})
            except Exception:
                pass
            net_entries = []
            try:
                self._sync_fusion_os_net_to_log()
                if self.net_log is not None and hasattr(self.net_log, "to_list"):
                    net_entries = self.net_log.to_list()[-30:]
            except Exception:
                pass
            vclock_state = None
            app_state_snap = None
            try:
                from core.java.glass_runner import extract_app_state_snapshot
                app_state_snap = extract_app_state_snapshot(self._stubs, fusion=getattr(self, "_fusion", None))
                vclock_state = app_state_snap.get("virtual_clock")
            except Exception:
                pass
            if vclock_state is None and isinstance(getattr(self, "_clock_preset", None), dict):
                vclock_state = dict(self._clock_preset)
            missing_list = list(getattr(self, "_last_missing", []) or [])
            tl_raw = (getattr(jdb, "timeline", None) or []) if jdb else []
            tl_tail = [
                {
                    "step": r.get("step"),
                    "cls": r.get("cls"),
                    "name": r.get("name"),
                    "pc": r.get("pc"),
                    "op": r.get("op"),
                }
                for r in tl_raw[-30:]
                if isinstance(r, dict)
            ]
            watches_out = []
            try:
                watches_out = self.watch_values()
            except Exception:
                watches_out = []
            vm_cur = getattr(jdb, "vm", None) if jdb is not None else None
            devs = []
            if vm_cur is not None and hasattr(vm_cur, "deviations") and callable(getattr(vm_cur.deviations, "to_list", None)):
                try:
                    devs = vm_cur.deviations.to_list()
                except Exception:
                    devs = []
            payload = {
                "mode": st.get("mode"),
                "step": st.get("step"),
                "reason": st.get("reason"),
                "exception": st.get("exception"),
                "cls": st.get("cls"),
                "name": st.get("name"),
                "sig": st.get("sig"),
                "pc": st.get("pc"),
                "op": st.get("op"),
                "depth": st.get("depth"),
                "zone": self.zone,
                "package": self._resolve_pkg(),
                "instance_id": getattr(getattr(self, "instance_state", None), "instance_id", "main"),
                "seam_profile": getattr(getattr(self, "seam_config", None), "unpack_mode", "self_cache"),
                "persist_session": bool(getattr(self, "persist_session", False)),
                "net_mode": self.net_mode,
                "net_condition": self.net_condition.to_dict() if getattr(self, "net_condition", None) else None,
                "virtual_clock": vclock_state,
                "app_state": app_state_snap,
                "callstack": (jdb.callstack() if jdb and hasattr(jdb, "callstack") else []),
                "loaded_classes_count": len(self.loaded_classes()),
                "net_count": len(getattr(self.net_log, "entries", []) or []) if self.net_log else 0,
                "views": views_snap,
                "views_count": len(views_snap),
                "registers": regs_snap,
                "net_entries": net_entries,
                "missing": missing_list[:100],
                "missing_count": len(missing_list),
                "deviations": devs[:100],
                "timeline_count": len(tl_raw),
                "timeline_tail": tl_tail,
                "jni_entries": self.jni_entries()[-30:],
                "watches": watches_out,
            }
            tmp = os.path.join(out_dir, "live_state.json.tmp")
            dst = os.path.join(out_dir, "live_state.json")
            raw = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            raw = raw.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
            with open(tmp, "w", encoding="utf-8", errors="replace") as fh:
                fh.write(raw)
            os.replace(tmp, dst)
            try:
                tree = getattr(self, "glass_tree", None)
                if tree is not None and getattr(tree, "root", None) is not None:
                    from core.java.ui_mcp_bridge import build_ui_snapshot_dict, persist_live_ui_snapshot
                    persist_live_ui_snapshot(
                        build_ui_snapshot_dict(
                            tree=tree,
                            package=self._resolve_pkg(),
                            source="debugger_live",
                        ),
                        out_dir=out_dir,
                    )
            except Exception:
                pass
        except Exception:
            pass

    # ---- Refresh der Inspektions-Views ------------------------------------
    def _refresh(self, ins=None):
        try:
            self._sync_fusion_os_net_to_log()
        except Exception:
            pass
        def _u():
            st = ins or (self._jdb.inspect() if self._jdb else {"mode": "idle"})
            self._last_pc = st.get("pc") if isinstance(st, dict) else None
            if isinstance(st, dict) and st.get("mode") in ("paused", "done", "stopped", "error"):
                sync_cls = st.get("cls")
                sync_name = st.get("name")
                sync_sig = st.get("sig")
                if st.get("mode") in ("done", "stopped") and (
                        not sync_cls
                        or sync_cls.startswith(("Landroid/", "Landroidx/", "Ljava/", "Lkotlin/", "Lkotlinx/"))
                        or "$$ExternalSyntheticLambda" in sync_cls):
                    l_cls = st.get("logic_cls") or ""
                    if l_cls and not l_cls.startswith(("Landroid/", "Landroidx/", "Ljava/", "Lkotlin/", "Lkotlinx/")) and "$$ExternalSyntheticLambda" not in l_cls:
                        sync_cls = l_cls
                        sync_name = st.get("logic_name")
                        sync_sig = st.get("logic_sig")
                    else:
                        frames = self._jdb.callstack() if (self._jdb and hasattr(self._jdb, "callstack")) else []
                        for fitem in frames:
                            fc = fitem.get("cls") or ""
                            if fc and not fc.startswith(("Landroid/", "Landroidx/", "Ljava/", "Lkotlin/", "Lkotlinx/")) and "$$ExternalSyntheticLambda" not in fc:
                                sync_cls, sync_name, sync_sig = fc, fitem.get("name"), fitem.get("sig")
                                break
                if sync_cls:
                    self._auto_sync_source(sync_cls, sync_name, sync_sig)
            lbl = self._wid("status")
            if lbl is not None:
                try:
                    ex = st.get("exception") or {}
                    _show_ex = bool(
                        ex and (
                            st.get("mode") in ("error", "paused")
                            or (not ex.get("handled") and not ex.get("lenient"))
                        )
                    )
                    rsn = (" (%s)" % st.get("reason")) if st.get("reason") else ""
                    _glass_active = getattr(getattr(self, "glass_tree", None), "root", None) is not None
                    _mode_txt = ("\U0001f7e2 Bereit (UI interaktiv / Event-Loop)"
                                 if st.get("mode") == "done" and _glass_active else st.get("mode"))
                    lbl.config(text="mode=%s%s  step=%s  %s->%s  pc=%s op=%s  depth=%s  result=%r%s%s" % (
                        _mode_txt, rsn, st.get("step"), st.get("cls"), st.get("name"),
                        st.get("pc"), st.get("op"), st.get("depth"), st.get("result"),
                        ("  EXC=%s" % ex.get("cls")) if _show_ex else "",
                        ("  ERR=%s" % st.get("error")) if st.get("error") else ""))
                except Exception:
                    pass
            rt = self._wid("registers")
            if rt is not None:
                try:
                    for x in rt.get_children():
                        rt.delete(x)
                    if getattr(self, "inspect_mode", "dalvik") == "arm64":
                        ns = self.native_state()
                        if isinstance(ns, dict):
                            regs_d = ns.get("regs") or ns
                            for k in ([f"x{i}" for i in range(31)] + ["sp", "pc", "cpsr"]):
                                if k in regs_d:
                                    v_val = regs_d[k]
                                    rt.insert("", "end", text=k,
                                              values=("arm64", hex(v_val) if isinstance(v_val, int) else repr(v_val)))
                        else:
                            rt.insert("", "end", text="-", values=("arm64", "(keine native Session aktiv)"))
                    else:
                        names = self.locals_names()
                        regs = None
                        idx_f = getattr(self, "_active_stack_idx", 0)
                        if idx_f and self._jdb and hasattr(self._jdb, "callstack"):
                            frames = self._jdb.callstack()
                            if 0 <= idx_f < len(frames):
                                regs = frames[idx_f].get("regs", [])
                        if regs is None:
                            regs = self._jdb.registers() if self._jdb else []
                        for i, val in enumerate(regs):
                            rt.insert("", "end", text="v%d" % i,
                                      values=(names.get("v%d" % i, ""), repr(val)))
                except Exception:
                    pass
            tl = (self._jdb.timeline[-60:] if self._jdb else [])
            self._fill_tree("timeline", tl, lambda _i, r: (str(r.get("step")), (r.get("pc"), r.get("op"))))
            tlw = self._wid("timeline")
            if tlw is not None:
                try:
                    for iid in tlw.get_children():
                        stxt = tlw.item(iid, "text")
                        on = stxt.isdigit() and int(stxt) in self.bookmarks
                        tlw.item(iid, tags=("bm",) if on else ())
                except Exception:
                    pass
            cs = self._wid("callstack")
            if cs is not None:
                try:
                    for i in cs.get_children():
                        cs.delete(i)
                    frames = self._jdb.callstack() if (self._jdb and hasattr(self._jdb, "callstack")) else []
                    if frames:
                        for fitem in frames:
                            _suf = " [Ende]" if (fitem.get("historical") and st.get("mode") in ("done", "stopped", "error")) else ""
                            cs.insert("", "end", iid="f%d" % fitem["index"], text=str(fitem["index"]),
                                      values=("%s->%s" % (fitem.get("cls"), fitem.get("name")),
                                              "pc=%s%s" % (fitem.get("pc"), _suf)))
                    elif st.get("cls"):
                        cs.insert("", "end", iid="f0", text="0", values=("%s->%s" % (st.get("cls"), st.get("name")),
                                                                         "pc=%s depth=%s" % (st.get("pc"), st.get("depth"))))
                except Exception:
                    pass
            bl = self._wid("bplist")
            if bl is not None:
                try:
                    for i in bl.get_children():
                        bl.delete(i)
                    seen_cur = set()
                    for pc in self.bps:
                        seen_cur.add(pc)
                        bl.insert("", "end", text=str(pc), values=(self._bp_label(pc),))
                    for (bc, bn, _bs, bpc), _bm in sorted(getattr(self, "app_bps", {}).items()):
                        if bc == getattr(self, "_loaded_cls", None) and bn == self.name and bpc in seen_cur:
                            continue
                        bl.insert("", "end", text=str(bpc if bpc is not None else "*"),
                                  values=("%s->%s" % (bc, bn),))
                except Exception:
                    pass
            self._sync_os_view()
            if not isinstance(st, dict) or st.get("mode") not in ("run", "step", "over", "out"):
                self._schedule_live_state_push(st)
            v = getattr(self, "view", None)
            if v is not None:
                try:
                    v.sync_code_views(st)
                except Exception:
                    pass
        self._after(_u)

    def _fill_tree(self, key, rows, mapper):
        tr = self._wid(key)
        if tr is None:
            return
        try:
            for i in tr.get_children():
                tr.delete(i)
            for i, r in enumerate(rows):
                text, vals = mapper(i, r)
                tr.insert("", "end", text=text, values=vals)
        except Exception:
            pass

    # ---- Quelle / Zone ----------------------------------------------------
    def _unpacked_name(self):
        """Name des entpackten Ordners (apktool/apkeditor) wie in der Pipeline."""
        try:
            strat = self.app.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        except Exception:
            strat = "smali_only"
        return "base_unpacked_apkeditor" if strat == "apkeditor" else "base_unpacked_apktool"

    def _cfg_path(self, key):
        """Pfad aus cfg.paths bzw. cfg.config (robust gegen beide Ablagen)."""
        try:
            p = getattr(self.app.cfg, "paths", None)
            if p and hasattr(p, "get") and p.get(key):
                return p.get(key)
        except Exception:
            pass
        try:
            return self.app.cfg.config.get(key)
        except Exception:
            return None

    @staticmethod
    def _has_smali(root):
        import os as _os, glob as _glob
        try:
            return bool(root) and bool(_glob.glob(_os.path.join(root, "smali*")))
        except Exception:
            return False

    def _zone_base(self, zone):
        """Entpackter App-Root der Zone: original/live->APP_SOURCE_DIR, destination/patched->DEST_DIR."""
        import os as _os
        dst_mode = zone in ("patched", "destination")
        base = self._cfg_path("DEST_DIR" if dst_mode else "APP_SOURCE_DIR")
        if not base:
            return ""
        cand = _os.path.join(base, self._unpacked_name())
        return cand if self._has_smali(cand) else (base if self._has_smali(base) else cand)

    def _resolve_root(self):
        """AP1.2 + Zero-Build: explizites zone_root > Zone (original | live | destination | patched) > Fallback."""
        if (self.zone_root or "").strip():
            return self.zone_root.strip()
        zone = (self.zone or "original")
        if zone in ("original", "live"):
            root = self._zone_base("original")
        else:
            root = self._zone_base(zone)
        if zone in ("patched", "destination") and not self._has_smali(root):
            # Noch nicht gestaged -> klarer Hinweis, Lauf nicht blockieren (Fallback original).
            self._log("> Zone '%s': Destination noch nicht gestaged (%s). Bitte '⚡ Stage → Destination' "
                      "oder Build ausfuehren; nutze vorerst 'original'." % (zone, root or "?"))
            root = self._zone_base("original")
        if self._has_smali(root):
            return root
        # Letzter Fallback: alte Direkt-Keys
        for k in ("APP_SOURCE_DIR", "UNPACKED_DIR", "BASE_DIR"):
            v = self._cfg_path(k)
            if v:
                return v
        return root or ""

    def _should_apply_overlays(self):
        """True wenn In-Memory/VFS-Overlays fuer die gewaehlte Quelle aktiv werden sollen."""
        z = (self.zone or "original").strip().lower()
        if z == "live":
            return True
        if z == "destination":
            return False
        return bool(getattr(self, "apply_patches", False))

    def set_source_zone(self, zone: str, *, log: bool = True):
        """Wechselt zwischen 'original', 'live' (Ohne Build), 'destination' (Build-Stand) oder 'patched'."""
        z = str(zone or "original").strip().lower()
        if z not in ("original", "live", "destination", "patched"):
            z = "original"
        self.zone = z
        if z == "live":
            self.apply_patches = True
        elif z in ("original", "destination"):
            self.apply_patches = False
        if getattr(self, "instance_state", None) is not None:
            self.instance_state.zone = z
            self.instance_state.apply_patches = bool(self.apply_patches)
        v = getattr(self, "view", None)
        if v is not None:
            zv = getattr(v, "_zone_var", None)
            if zv is not None:
                try:
                    if zv.get() != z:
                        zv.set(z)
                except Exception:
                    pass
        if self.app is not None and hasattr(self.app, "_update_context_bar"):
            try:
                self.app._update_context_bar()
            except Exception:
                pass
        if log:
            self._log("> Quelle/Zone: %s" % z)

    def run_live_with_patches(self):
        """Schaltet auf 'live' (Zero-Build Overlay) + Gläsern+ARM64, oeffnet Debugger und startet die App."""
        self.set_source_zone("live", log=False)
        self.native_fusion = True
        v = getattr(self, "view", None)
        if v is not None and hasattr(v, "_mode_var") and v._mode_var is not None:
            try:
                v._mode_var.set("Gläsern+ARM64")
            except Exception:
                pass
        if self.app is not None and hasattr(self.app, "notebook") and hasattr(self.app, "debugger_tab"):
            try:
                self.app.notebook.select(self.app.debugger_tab)
            except Exception:
                pass
        self._log("> Live-Patches (Ohne Build) aktiv — starte App im Simulator...")
        self.start_app()

    def stage_destination(self):
        """AP1.2: Schnelles Staging in destination/ (ohne APK-Build), dann Zone auf 'destination'."""
        try:
            eng = getattr(self.app, "pipeline", None) or getattr(self.app, "engine", None)
            if eng is None:
                self._log("> Stage: keine PipelineEngine am App-Objekt gefunden."); return False
            ok = eng.run_stage_destination()
            if ok:
                self.set_source_zone("destination", log=False)
                self._log("> Stage fertig -> Zone auf 'destination' gesetzt.")
            return ok
        except Exception as e:
            self._log("> Stage-Fehler: %r" % e); return False

    def _build_vm(self):
        from core.java.dalvik import DalvikVM
        from core.java import smali as Sm
        if not (self.source or "").strip():
            raise ValueError("kein Smali-Text")
        vm = DalvikVM()
        try:
            from core.java.dex_loader import AndroidStubs
            from core.java.lifecycle import _StubRuntime
            _stubs = AndroidStubs(vm=vm)
            if isinstance(getattr(self, "_clock_preset", None), dict):
                try:
                    from core.java.virtual_clock import set_virtual_clock
                    set_virtual_clock(_stubs, **self._clock_preset)
                except Exception:
                    pass
            try:
                _stubs.seed_framework_statics(vm)
            except Exception:
                pass
            vm.runtime = _StubRuntime(_stubs)
        except Exception:
            pass
        vm.lenient = bool(getattr(self, "tolerant", True))
        src = self._patched_source()
        mds = Sm.parse_class(src)[1] if ".class" in src else [Sm.parse_method(src)]
        for md in mds:
            vm.register(md)
        if self.zone_root.strip() or self.zone != "original" or self._should_apply_overlays():
            root = self._resolve_root()
            if root:
                from core.java.class_provider import StudioClassProvider
                prov = StudioClassProvider(root, zone=self.zone)
                if self._should_apply_overlays() and getattr(self, "overlay_smali", True):
                    try:
                        from services.workspace_overlay_service import WorkspaceOverlayResolver
                        ov = WorkspaceOverlayResolver.collect(
                            self._base_dir(), self._resolve_pkg(), root, app=self.app,
                            overlay_smali=True, overlay_libcarve=False, overlay_libforge=False,
                        )
                        if ov.get("smali_patches") and hasattr(prov, "apply_smali_patches"):
                            prov.apply_smali_patches(ov["smali_patches"])
                    except Exception:
                        pass
                vm.class_provider = prov
        return vm, mds

    def current_target(self):
        """(cls, name, sig) der aktuell geladenen Methode — fuer Aktionspunkt-Ort. Fehler -> (None,None,None)."""
        try:
            from core.java import smali as Sm
            src = self._patched_source()
            mds = Sm.parse_class(src)[1] if ".class" in src else [Sm.parse_method(src)]
            md = self._target(mds)
            return (md.cls, md.name, md.sig)
        except Exception:
            return (None, None, None)

    def _target(self, mds):
        nm = (self.name or "").strip()
        if nm:
            for md in mds:
                if md.name == nm:
                    return md
            raise ValueError("Methode '%s' nicht gefunden" % nm)
        return mds[0]

    def on_budget_changed(self):
        """Synchronisiert Aenderungen am Budget-Dropdown sofort in die aktive JavaDebugger-Session."""
        steps = self._parse_budget_steps()
        if self._jdb is not None and hasattr(self._jdb, "set_budget"):
            try:
                self._jdb.set_budget(steps, pause_on_budget=True)
            except Exception:
                pass
        v = getattr(self, "view", None)
        if v is not None and hasattr(v, "sync_code_views"):
            try:
                st = self._jdb.inspect() if self._jdb else {"mode": "idle"}
                v.sync_code_views(st)
            except Exception:
                pass

    def _parse_budget_steps(self):
        """Liest das ausgewaehlte Schritt-Budget je Lauf/Weiter-Klick."""
        v = getattr(getattr(self, "view", None), "v_step_budget", None)
        raw = (v.get() if v is not None else getattr(self, "_budget_override", None)) or "200k"
        raw = raw.strip().lower()
        mapping = {
            "50k": 50_000,
            "200k": 200_000,
            "500k": 500_000,
            "2m": 2_000_000,
            "10m": 10_000_000,
            "\u221e": 500_000_000,
            "inf": 500_000_000,
            "unlimited": 500_000_000,
        }
        if raw in mapping:
            return mapping[raw]
        try:
            if raw.endswith("k"):
                return max(1, int(float(raw[:-1]) * 1000))
            if raw.endswith("m"):
                return max(1, int(float(raw[:-1]) * 1_000_000))
            return max(1, int(raw))
        except Exception:
            return 200_000

    def _notify_glass_ui_updated(self):
        """Synchronisiert asynchrone In-Phone-UI/Browser-Updates zurück ins Debugger-Netz-Log + Handy-Anzeige."""
        def _upd():
            try:
                ins = self._jdb.inspect() if self._jdb else {"mode": "done"}
            except Exception:
                ins = {"mode": "done"}
            try:
                self._refresh(ins)
            except Exception:
                pass
            if self.glass_tree is not None:
                try:
                    from core.application.event_bus import EventBus
                    EventBus.publish("GLASS_STATE", {"mode": ins.get("mode"), "tree": self.glass_tree})
                except Exception:
                    pass
        self._after(_upd)

    def _async(self, fn, label):
        lock = getattr(self, "_state_lock", None)
        if lock is not None:
            with lock:
                if self._busy:
                    self._log("Debugger beschaeftigt."); return
                self._busy = True
                self._stop_pending = False
                self._run_seq += 1
                my_seq = self._run_seq
        else:
            if self._busy:
                self._log("Debugger beschaeftigt."); return
            self._busy = True
            self._stop_pending = False
            self._run_seq += 1
            my_seq = self._run_seq
        self._start_heartbeat()

        def _w():
            try:
                fn()
                while (not self._stop_pending) and (self._run_seq == my_seq) and self._jdb is not None and getattr(self._jdb, "mode", None) in ("run", "step", "over", "out"):
                    try:
                        self._jdb._paused.wait(0.20)
                    except Exception:
                        break
                if self._run_seq != my_seq or self._stop_pending:
                    return
                if ("app" in label or label.startswith("ui-")) and self._should_persist_session():
                    self.persist_active_appdata()
                if self.glass_tree is not None and getattr(self._jdb, "mode", None) in ("done", "idle"):
                    try:
                        _rt_root = self._resolve_root()
                        if _rt_root:
                            from core.java.app_runtime import mount_inphone_runtime_if_needed
                            mount_inphone_runtime_if_needed(
                                self.glass_tree,
                                _rt_root,
                                stubs=self._stubs,
                                on_async_update=self._notify_glass_ui_updated,
                            )
                    except Exception:
                        pass
                if self._run_seq != my_seq:
                    return
                ins = (self._jdb.inspect() if self._jdb else {"mode": "idle"})
                self._refresh(ins)
                _m = ins.get("mode")
                try:
                    from core.application.event_bus import EventBus
                    EventBus.publish("DEBUG_PAUSED", {"paused": _m == "paused", "mode": _m})
                except Exception:
                    pass
                if self.glass_tree is not None:
                    try:
                        from core.application.event_bus import EventBus
                        EventBus.publish("GLASS_STATE", {"mode": _m, "tree": self.glass_tree})
                    except Exception:
                        pass
                if _m == "error":
                    self._log("> %s -> error: %s" % (label, ins.get("error")))
                elif _m == "paused" and ins.get("reason") == "budget":
                    self._log("> %s -> Pausiert wegen Schritt-Budget (%s Schritte). Weiter mit ▶ Start/Weiter."
                              % (label, f"{ins.get('step', 0):,}"))
                elif _m == "done" and "app" in label:
                    _b = getattr(self._jdb, "_budget_chunk", self._parse_budget_steps()) if self._jdb else self._parse_budget_steps()
                    self._log("> \u2714 App-Lifecycle nach %s Schritten abgeschlossen (Budget: %s). Handy-Anzeige für UI-Events anklicken."
                              % (f"{ins.get('step', 0):,}", f"{_b:,}"))
                else:
                    self._log("> %s -> %s" % (label, _m))
            except Exception as e:
                if self._run_seq == my_seq:
                    self._log("> FEHLER (%s): %r" % (label, e))
            finally:
                if lock is not None:
                    with lock:
                        cur_match = (self._run_seq == my_seq)
                        if cur_match:
                            self._busy = False
                else:
                    cur_match = (self._run_seq == my_seq)
                    if cur_match:
                        self._busy = False
                if cur_match:
                    self._after(self._stop_heartbeat)

        th = threading.Thread(target=_w, daemon=True)
        if lock is not None:
            with lock:
                if self._run_seq == my_seq:
                    self._worker_thread = th
        else:
            self._worker_thread = th
        th.start()

    # ---- Debugger-Operationen ---------------------------------------------
    def select_stack_frame(self, idx):
        try:
            idx = int(idx)
        except Exception:
            idx = 0
        self._active_stack_idx = max(0, idx)
        if self._jdb and hasattr(self._jdb, "callstack"):
            frames = self._jdb.callstack()
            if 0 <= idx < len(frames):
                f = frames[idx]
                self._auto_sync_source(f.get("cls"), f.get("name"), f.get("sig"))
        self._refresh()

    def set_inspect_mode(self, mode):
        self.inspect_mode = "arm64" if str(mode).lower().startswith("arm") else "dalvik"
        self._refresh()

    def set_stop_on_exception(self, enabled, unhandled_only=True):
        self.stop_on_exception = bool(enabled)
        self.exception_unhandled_only = bool(unhandled_only)
        if self._jdb is not None:
            self._jdb.stop_on_exception = self.stop_on_exception
            self._jdb.exception_unhandled_only = self.exception_unhandled_only
        self._log("> Stop-on-Exception: %s (unhandled=%s)" % (self.stop_on_exception, self.exception_unhandled_only))

    def set_flight_recorder(self, enabled):
        self.flight_recorder_enabled = bool(enabled)
        if self._jdb is not None:
            self._jdb.flight_recorder_enabled = self.flight_recorder_enabled
        self._log("> Flight-Recorder (data/glass_flight.jsonl): %s" % self.flight_recorder_enabled)

    def set_net_condition(self, mode=None, latency_ms=None, kbps=None):
        from core.java.net_recorder import NetCondition
        cur = getattr(self, "net_condition", None) or NetCondition()
        d = cur.to_dict()
        if mode is not None:
            d["mode"] = str(mode).strip().lower()
        if latency_ms is not None:
            try: d["latency_ms"] = max(0, int(latency_ms))
            except Exception: pass
        if kbps is not None:
            try: d["kbps"] = max(0, int(kbps))
            except Exception: pass
        nc = NetCondition.from_dict(d)
        self.net_condition = nc
        st = getattr(self, "_stubs", None)
        if st is not None and hasattr(st, "set_net_condition"):
            st.set_net_condition(nc)
        else:
            f = getattr(self, "_fusion", None)
            if f and f.get("eng"):
                nc.apply_to_os(f["eng"])
        self._log("> Netz-Kondition: mode=%s latenz=%dms kbps=%s" % (nc.mode, nc.latency_ms, nc.kbps or "unlim"))

    def start(self):
        try:
            vm, mds = self._build_vm()
            md = self._target(mds)
            args = json.loads((self.args or "[]").strip() or "[]")
            if not isinstance(args, list):
                raise ValueError("args muss JSON-Liste sein")
        except Exception as e:
            self._log("> Start abgebrochen: %r" % e); return
        from core.java.debug import JavaDebugger
        self._jdb = JavaDebugger(vm)
        self._jdb.set_budget(self._parse_budget_steps(), pause_on_budget=True)
        self._jdb.record_timeline = bool(self.record_timeline)
        self._jdb.record_regs = bool(self.record_regs)
        self._jdb.stop_on_exception = bool(getattr(self, "stop_on_exception", False))
        self._jdb.exception_unhandled_only = bool(getattr(self, "exception_unhandled_only", True))
        self._jdb.flight_recorder_enabled = bool(getattr(self, "flight_recorder_enabled", False))
        self._last_start_mode = "method"
        self._jdb_sig = (md.cls, md.name, md.sig)
        self._loaded_cls = md.cls
        self._register_all_bps_on_jdb()
        self._log("> Start %s->%s%s (BP=%s, Halt@Eintritt=%s, Zone=%s)" % (
            md.cls, md.name, md.sig, self.bps, self.entry_stop, self.zone))
        self._rebuild_view()
        self._async(lambda: self._jdb.start(md.cls, md.name, md.sig, tuple(args),
                                            stop_at_entry=self.entry_stop, wait_timeout=0.05), "start")

    def _run_single_step(self, step_kind):
        """Fuehrt In/Over/Out im glaesernen oder Einzelmethoden-Lauf robust aus."""
        jdb = self._jdb
        if not jdb:
            self._log("> %s: kein Lauf aktiv — zuerst '▶ Start/Weiter' ausfuehren." % step_kind)
            return

        def _exec_step():
            if step_kind == "step":
                self._jdb.step(wait_timeout=0.05)
            elif step_kind == "step_over":
                self._jdb.step_over(wait_timeout=0.05)
            else:
                self._jdb.step_out(wait_timeout=0.05)

        if self._busy:
            if getattr(jdb, "_pause_requested", False) or getattr(jdb, "mode", None) in ("run", "step", "over", "out"):
                def _wait_then_step():
                    deadline = time.monotonic() + 1.5
                    while self._busy and time.monotonic() < deadline and not self._stop_pending:
                        time.sleep(0.02)
                    if not self._busy and self._jdb is jdb and jdb.mode == "paused":
                        self._active_stack_idx = 0
                        self._async(_exec_step, step_kind)
                    else:
                        self._log("> %s: Debugger ist noch im Uebergang zur Pause (bitte kurz warten)." % step_kind)
                threading.Thread(target=_wait_then_step, daemon=True).start()
                return
            self._log("> %s: Debugger arbeitet noch." % step_kind)
            return
        if jdb.mode in ("done", "stopped", "error"):
            self._log("> %s: Lauf bereits beendet (%s) — nutze 'Rev' fuer 1 Schritt zurueck oder '🔄 Neu' mit 'Bei Schritt 0 pausieren'." % (step_kind, jdb.mode))
            return
        if jdb.mode != "paused":
            self._log("> %s: Bitte zuerst mit '⏸ Pause' anhalten (aktuell: %s)." % (step_kind, jdb.mode))
            return
        self._active_stack_idx = 0
        self._async(_exec_step, step_kind)

    def step(self):
        self._run_single_step("step")

    def step_over(self):
        self._run_single_step("step_over")

    def step_out(self):
        self._run_single_step("step_out")

    def reverse_step(self):
        jdb = self._jdb
        if not jdb:
            self._log("> Rev: kein Lauf aktiv.")
            return
        cur_step = int(getattr(jdb, "step_count", 0) or 0)
        if jdb.mode in ("paused", "done", "stopped", "error"):
            if cur_step <= 0:
                self._log("> Rev: bereits bei Schritt 0.")
                return
            self.goto(max(0, cur_step - 1))
        else:
            self._log("> Rev: Bitte zuerst mit '⏸ Pause' anhalten.")

    def pause(self):
        """Pausiert einen laufenden Java+ARM64 Debug-Lauf sauber und aktualisiert Stack/Editor."""
        if not self._jdb:
            if self._busy:
                self._pause_pending = True
                self._log("> Pause vorgemerkt (haelt direkt nach dem Setup bei Schritt 0 an).")
            else:
                self._log("> pause: kein Lauf aktiv")
            return
        jdb = self._jdb
        try:
            self._pause_pending = True
            jdb.pause()
            if self._session is not None and hasattr(self._session, "pause"):
                try:
                    self._session.pause()
                except Exception:
                    pass
            self._log("> Pause angefordert (%s)" % jdb.mode)

            def _wait_and_refresh():
                try:
                    if jdb.mode in ("run", "step", "over", "out"):
                        jdb._paused.wait(1.5)
                except Exception:
                    pass
                try:
                    ins = jdb.inspect()
                    self._refresh(ins)
                    from core.application.event_bus import EventBus
                    self._after(lambda: EventBus.publish("DEBUG_PAUSED", {"paused": ins.get("mode") == "paused", "mode": ins.get("mode")}))
                    if self.glass_tree is not None:
                        self._after(lambda: EventBus.publish("GLASS_STATE", {"mode": ins.get("mode"), "tree": self.glass_tree}))
                except Exception:
                    pass

            threading.Thread(target=_wait_and_refresh, daemon=True).start()
        except Exception as e:
            self._log("> pause -> FEHLER %r" % e)

    def cont(self):
        if self._jdb:
            self._active_stack_idx = 0
            self._jdb.set_budget(self._parse_budget_steps(), pause_on_budget=True)
            self._async(lambda: self._jdb.cont(wait_timeout=0.05), "cont")

    def stop(self, persist=True):
        # Abbruch muss IMMER sofort durchgehen - auch waehrend Setup/JNI/Run (UI nie blockieren).
        lock = getattr(self, "_state_lock", None)
        if lock is not None:
            with lock:
                self._stop_pending = True
                self._pause_pending = False
                self._run_seq += 1
                jdb = self._jdb
        else:
            self._stop_pending = True
            self._pause_pending = False
            self._run_seq += 1
            jdb = self._jdb
        self._stop_heartbeat()
        hc = getattr(self, "_hermes_client", None)
        if hc is not None and hasattr(hc, "close"):
            try:
                hc.close()
            except Exception:
                pass
            self._hermes_client = None
        eng = (getattr(self, "_fusion", None) or {}).get("eng")
        if eng is not None and getattr(eng, "cpu", None) is not None:
            try:
                eng.cpu.stopped = True
            except Exception:
                pass
        if not jdb:
            if lock is not None:
                with lock:
                    self._busy = False
            else:
                self._busy = False
            self._log("> stop -> abgebrochen")
            return
        try:
            jdb.stop(wait_timeout=0.15)
        except Exception as e:
            self._log("> stop -> FEHLER %r" % e); return
        if lock is not None:
            with lock:
                self._busy = False
        else:
            self._busy = False
        self._log("> stop -> %s" % jdb.mode)
        if persist and not getattr(self, "_clearing_appdata", False):
            def _fin_appdata():
                wt = getattr(self, "_worker_thread", None)
                if wt is not None and wt.is_alive() and wt is not threading.current_thread():
                    wt.join(timeout=0.5)
                if self._should_persist_session():
                    self.persist_active_appdata()
                else:
                    self._cleanup_ephemeral_session()
            threading.Thread(target=_fin_appdata, daemon=True).start()
        try:
            self._refresh(jdb.inspect())
        except Exception:
            pass

    def dispatch_ui_event(self, kind, **kw):
        """UI-Interaktion (Tap/Back/Key) im glaesernen Lauf unter Debugger-Kontrolle ausfuehren."""
        hc = getattr(self, "_hermes_client", None)
        if hc is not None:
            def _run_hc():
                try:
                    if kind == "tap":
                        hc.tap(kw.get("x", 0), kw.get("y", 0))
                    elif kind == "key":
                        hc.key(kw.get("x", 0), kw.get("y", 0), kw.get("text", ""))
                    elif kind == "back":
                        jdb = self._jdb
                        if jdb and jdb.vm and getattr(jdb.vm.runtime, "stubs", None):
                            pass
                except Exception as e:
                    self._log("> UI-Dispatch (Hermes) FEHLER: %r" % e)
            threading.Thread(target=_run_hc, daemon=True).start()
            return
        tree = getattr(self, "glass_tree", None)
        jdb = self._jdb
        if tree is None or jdb is None:
            return
        def _interact():
            from core.java.objects import JObject
            def _find_node(prefer_clickable=False, prefer_focusable=False):
                vid = kw.get("vid")
                txt_q = kw.get("text") if prefer_clickable else (kw.get("hint") or kw.get("target"))
                _vis_nodes = tree.visible_nodes() if hasattr(tree, "visible_nodes") else tree.nodes()
                if vid is not None:
                    for nd in tree.nodes():
                        if getattr(nd, "vid", None) == vid:
                            return nd
                if "x" in kw or "y" in kw:
                    x, y = float(kw.get("x", 0)), float(kw.get("y", 0))
                    fix_flag = kw.get("fixed")
                    if fix_flag is True and hasattr(tree, "fixed_clickable_at"):
                        nd = tree.fixed_clickable_at(x, y) if prefer_clickable else tree.fixed_node_at(x, y)
                        if nd is None and not prefer_clickable:
                            nd = tree.fixed_clickable_at(x, y)
                    else:
                        nd = tree.clickable_at(x, y) if prefer_clickable else tree.node_at(x, y)
                        if nd is None and not prefer_clickable:
                            nd = tree.clickable_at(x, y)
                    if nd is not None:
                        return nd
                if txt_q is not None:
                    needle = str(txt_q).lower()
                    for nd in _vis_nodes:
                        if not getattr(nd, "visible", True):
                            continue
                        if prefer_clickable and not getattr(nd, "clickable", False):
                            continue
                        if prefer_focusable and not getattr(nd, "focusable", False):
                            continue
                        props = getattr(nd, "props", None) or {}
                        ntxt = (
                            (getattr(nd, "text", None) or "")
                            + " " + str(props.get("hint", ""))
                            + " " + str(props.get("a11y_label", ""))
                            + " " + str(props.get("desc", ""))
                        ).lower()
                        if needle in ntxt:
                            return nd
                if prefer_focusable:
                    for nd in _vis_nodes:
                        if getattr(nd, "visible", True) and getattr(nd, "focusable", False):
                            return nd
                return None

            runner = getattr(self, "_runner", None)
            stubs = getattr(self, "_stubs", None)
            if stubs is not None:
                stubs._activity_finished = False
                stubs._clock_ms = getattr(stubs, "_clock_ms", 1700000000000) + 1500
            if kind == "tap":
                clk = _find_node(prefer_clickable=True)
                if clk is None:
                    return
                cb = getattr(clk, "on_click", None)
                view = getattr(clk, "_jobj", None)
                if not isinstance(view, JObject):
                    view = JObject("Landroid/view/View;", {"$view": clk})
                    clk._jobj = view
                if callable(cb):
                    cb(clk)
                elif cb is not None and jdb.vm is not None:
                    cls = getattr(cb, "cls", None)
                    if hasattr(jdb.vm, "invoke_virtual"):
                        jdb.vm.invoke_virtual(cb, "onClick", "(Landroid/view/View;)V", (cb, view))
                    elif cls:
                        jdb.vm.invoke(cls, "onClick", "(Landroid/view/View;)V", (cb, view))
                else:
                    xml_oc = getattr(clk, "props", {}).get("onClick") if hasattr(clk, "props") else None
                    act = getattr(stubs, "_current_activity", None)
                    if xml_oc and act is not None and jdb.vm is not None:
                        if hasattr(jdb.vm, "invoke_virtual"):
                            jdb.vm.invoke_virtual(act, xml_oc, "(Landroid/view/View;)V", (act, view))
                        else:
                            jdb.vm.invoke(act.cls, xml_oc, "(Landroid/view/View;)V", (act, view))
                if stubs is not None:
                    fl = getattr(stubs, "_flush_fragment_transactions", None)
                    if callable(fl):
                        fl()
                tree.touch()
            elif kind == "back":
                if runner is not None:
                    runner.handle_back()
                else:
                    act = getattr(stubs, "_current_activity", None)
                    if act is not None and jdb.vm is not None:
                        if hasattr(jdb.vm, "invoke_virtual"):
                            jdb.vm.invoke_virtual(act, "onBackPressed", "()V", (act,))
                        else:
                            jdb.vm.invoke(act.cls, "onBackPressed", "()V", (act,))
            elif kind == "home":
                if runner is not None:
                    runner.handle_home()
            elif kind == "key":
                node = _find_node(prefer_clickable=False, prefer_focusable=True)
                if node is not None and hasattr(node, "text"):
                    new_txt = str(kw.get("text", ""))
                    node.text = new_txt
                    for sub in getattr(node, "walk", lambda: ())():
                        if sub is not node and getattr(sub, "cls", "") == "EditText":
                            sub.text = new_txt
                    par = getattr(node, "_parent", None)
                    if par is not None and getattr(par, "cls", "") == "EditText":
                        par.text = new_txt
                    _oc = node.props.get("$on_change_fn") if hasattr(node, "props") else None
                    if callable(_oc):
                        try:
                            _oc(node.text)
                        except Exception:
                            pass
                    tree.touch()
            # Geplante UI-Runnables & Activity-Uebergaenge ausfuehren
            if kind not in ("back", "home") and runner is not None and stubs is not None:
                try:
                    runner._drain_runnables()
                    navigated = runner.navigate_to_pending()
                    if navigated:
                        tree.touch()
                    else:
                        recomp = getattr(tree, "_compose_recompose", None)
                        if callable(recomp) and jdb.mode not in ("paused", "stopped"):
                            recomp()
                except Exception:
                    pass
        sel_budget = self._parse_budget_steps()
        try:
            jdb.set_budget(sel_budget, pause_on_budget=True)
        except Exception:
            pass
        self._async(
            lambda: jdb.run_interaction(_interact, stop_at_entry=bool(self.entry_stop), min_budget=sel_budget),
            "ui-%s" % kind,
        )

    def refresh_interactive_ui(self):
        """Aktualisiert die laufende interaktive UI-Session ohne App-Reset."""
        tree = getattr(self, "glass_tree", None)
        if tree is None:
            self.start_app()
            return
        def _u():
            try:
                recomp = getattr(tree, "_compose_recompose", None)
                mode = getattr(self._jdb, "mode", None) if self._jdb else None
                if callable(recomp) and mode != "paused":
                    recomp()
                else:
                    tree.touch()
            except Exception:
                try:
                    tree.touch()
                except Exception:
                    pass
        self._async(_u, "ui-refresh")

    # ---- Gläserner App-Lauf (Anzeige + Debugger am selben VM) --------------
    def _launch_glass_app_async(self, pause_at_step=None):
        """Bereitet den glaesernen App-Lauf vollstaendig asynchron vor, ohne die GUI zu blockieren."""
        if self._jdb is not None and getattr(self._jdb, "mode", None) in ("run", "step", "over", "out", "paused"):
            self.stop()
        if self._busy:
            self._log("Debugger beschaeftigt."); return
        try:
            root = self._resolve_root()
        except Exception:
            root = ""
        if not root:
            self._log("> Gläsern: Zonen-Wurzel/App-Root fehlt."); return

        cfg_snap = {
            "root": root,
            "zone": self.zone,
            "zone_root": self.zone_root,
            "entry": (self.app_entry or "").strip() or None,
            "budget": self._parse_budget_steps(),
            "entry_stop": bool(self.entry_stop),
            "tolerant": bool(getattr(self, "tolerant", True)),
            "apply_overlays": bool(self._should_apply_overlays()),
            "overlay_smali": bool(getattr(self, "overlay_smali", True)),
            "overlay_libcarve": bool(getattr(self, "overlay_libcarve", True)),
            "overlay_libforge": bool(getattr(self, "overlay_libforge", True)),
            "apply_patches": bool(getattr(self, "apply_patches", False)),
            "native_fusion": bool(getattr(self, "native_fusion", True)),
            "record_timeline": bool(getattr(self, "record_timeline", False)),
            "record_regs": bool(getattr(self, "record_regs", False)),
            "stop_on_exception": bool(getattr(self, "stop_on_exception", False)),
            "exception_unhandled_only": bool(getattr(self, "exception_unhandled_only", True)),
            "flight_recorder_enabled": bool(getattr(self, "flight_recorder_enabled", False)),
            "pkg_hint": self._resolve_pkg(),
            "pause_at_step": pause_at_step,
        }
        self._last_start_mode = "app"
        self._pause_pending = False

        def _prepare_and_launch():
            from core.java.glass_runner import GlassAppRunner, InstanceState
            from core.java.net_recorder import NetLog
            if self._stop_pending:
                return
            seam_cfg = getattr(self, "seam_config", None)
            if seam_cfg is None:
                from core.java.seam_config import SeamConfig
                seam_cfg = SeamConfig.from_profile("self_cache")
                self.seam_config = seam_cfg
            seam_cfg.persist_appdata = self._should_persist_session()
            inst_state = getattr(self, "instance_state", None)
            if inst_state is None:
                inst_state = InstanceState(zone=cfg_snap["zone"], apply_patches=cfg_snap["apply_patches"])
                self.instance_state = inst_state
            inst_state.app_root = cfg_snap["root"]
            inst_state.zone = cfg_snap["zone"]
            inst_state.apply_patches = cfg_snap["apply_patches"]
            inst_state.seam_profile = seam_cfg.unpack_mode

            self.net_log = NetLog()
            self._synced_os_net_count = 0
            _api_db = None
            _rules_path = None
            try:
                paths = getattr(self.app.cfg, "paths", {}) or {}
                _dbp = paths.get("API_DB") if hasattr(paths, "get") else None
                _rules_path = paths.get("API_RULES") if hasattr(paths, "get") else None
                if _dbp:
                    from services.api_db_service import ApiDbService
                    _api_db = ApiDbService(_dbp)
            except Exception as _edb:
                self._log("> API-DB nicht angebunden: %r" % _edb)
            self._api_db = _api_db
            self._mock_hint_urls = set()

            _cfg_dict = getattr(getattr(self.app, "cfg", None), "config", None) or {}
            _vclock = getattr(self, "_clock_preset", None)
            gr = GlassAppRunner(
                cfg_snap["root"],
                zone=cfg_snap["zone"],
                seam_config=seam_cfg,
                instance_state=inst_state,
                base_dir=self._base_dir(),
                log_fn=self._log,
            )
            built = gr.build(
                native_fusion=cfg_snap["native_fusion"],
                net_mode=getattr(self, "net_mode", "mock"),
                net_responder=self._dispatch_net_request,
                rules_path=_rules_path,
                api_db=_api_db,
                net_log=self.net_log,
                net_condition=getattr(self, "net_condition", None),
                virtual_clock=(_vclock if isinstance(_vclock, dict) else None),
                tolerant=cfg_snap["tolerant"],
                step_budget=100_000_000,
                apply_overlays=cfg_snap["apply_overlays"],
                overlay_opts={
                    "overlay_smali": cfg_snap["overlay_smali"],
                    "overlay_libcarve": cfg_snap["overlay_libcarve"],
                    "overlay_libforge": cfg_snap["overlay_libforge"],
                },
                app_ctx=self.app,
                cfg_dict=_cfg_dict,
                force_sync_appdata=True,
                fallback_java_on_fusion_err=True,
                jni_aps=list(self.jni_aps or ()),
                on_load_library=self._fusion_load_library,
                on_load_path=self._fusion_load_path,
                cancel_check=lambda: bool(self._stop_pending),
            )
            if not built or built.get("cancelled") or self._stop_pending:
                return

            vm = built["vm"]
            prov = built["provider"]
            stubs = built["stubs"]
            tree = built["tree"]
            runner = gr.runner
            jdb = built["jdb"]
            _pkg_det = built.get("man_pkg") or cfg_snap["pkg_hint"] or "com.example.app"
            _man_entry = built.get("man_entry") or ""

            self._stubs = stubs
            self._active_pkg = _pkg_det
            self._nld = built.get("nld") or ("/data/app/%s/lib/arm64" % _pkg_det)
            self._fusion = built.get("fusion")
            if self._fusion and self._fusion.get("hook"):
                self.jni_log = self._fusion["hook"].log
                self._log("> Fusion aktiv: echte arm64-Engine am gläsernen Lauf (Java<->nativ beobachtbar+manipulierbar)")
            else:
                self.jni_log = None
            self._session = built.get("session")
            if self._should_persist_session():
                self._log("> Session-Modus: persistent (AppData aktiv)")
            else:
                self._log("> Session-Modus: clean/temporär (kein Laden alter AppData)")
            self.glass_tree = tree
            self._runner = runner
            self._jdb = jdb

            budget_lim = cfg_snap["budget"]
            if cfg_snap["pause_at_step"] is not None:
                budget_lim = max(budget_lim, int(cfg_snap["pause_at_step"]) + 1)
            jdb.set_budget(budget_lim, pause_on_budget=True)
            jdb.record_timeline = cfg_snap["record_timeline"]
            jdb.record_regs = cfg_snap["record_regs"]
            jdb.stop_on_exception = cfg_snap["stop_on_exception"]
            jdb.exception_unhandled_only = cfg_snap["exception_unhandled_only"]
            jdb.flight_recorder_enabled = cfg_snap["flight_recorder_enabled"]
            self._jdb_sig = None
            self._register_all_bps_on_jdb()
            self._apply_aps()
            entry = cfg_snap["entry"]
            _preload_cls = entry or _man_entry
            if _preload_cls:
                try:
                    self._after(lambda c0=_preload_cls, p0=prov: self._auto_sync_source(c0, "onCreate", "(Landroid/os/Bundle;)V", prov=p0))
                except Exception:
                    pass

            def _run_fn():
                rep = runner.run_app(studio_root=cfg_snap["root"], launcher=entry)
                try:
                    miss = (rep.get("missing") if isinstance(rep, dict) else None) or getattr(stubs, "missing", [])
                    self._fill_missing(miss)
                except Exception:
                    pass
                return rep

            try:
                from core.application.event_bus import EventBus
                self._after(lambda: EventBus.publish("GLASS_STARTED", {"tree": tree, "package": _pkg_det}))
            except Exception:
                pass
            self._log("> Gläsern starten (App=%s, Zone=%s, Entry=%s)" % (_pkg_det, cfg_snap["zone"], entry or _man_entry or "Manifest"))
            self._rebuild_view()
            eff_stop_at_entry = bool(cfg_snap["entry_stop"] or self._pause_pending)
            if self._stop_pending:
                return
            self._pause_pending = False
            if cfg_snap["pause_at_step"] is not None:
                jdb._statics_snap = {c: dict(d) for c, d in vm.statics.items()}
                jdb._clinit_snap = set(getattr(vm, "_clinit_done", ()) or ())
                jdb._entry = None
                jdb._run_fn = _run_fn
                jdb._launch(pause_at_step=max(0, int(cfg_snap["pause_at_step"])), min_budget=budget_lim, wait_timeout=0.05)
            else:
                jdb.start_app(_run_fn, stop_at_entry=eff_stop_at_entry, wait_timeout=0.05)

        lbl = ("goto %d" % int(pause_at_step)) if pause_at_step is not None else "app-lauf"
        self._async(_prepare_and_launch, lbl)

    def start_app(self):
        self._launch_glass_app_async(pause_at_step=None)

    # ---- Aktionspunkte (bedingte/funktionale/getriggerte Manipulation) ----
    def set_virtual_clock_spec(self, spec):
        """Setzt/verschiebt/resettet die virtuelle Uhr live und merkt das Preset fuer kuenftige Laeufe."""
        spec = dict(spec or {})
        if spec.get("reset"):
            self._clock_preset = None
        else:
            preset = dict(getattr(self, "_clock_preset", None) or {})
            iso_v = spec.get("iso")
            if iso_v is None:
                iso_v = spec.get("iso_str")
            if iso_v is None:
                iso_v = spec.get("datetime")
            if iso_v is not None:
                preset["iso_str"] = str(iso_v)
                preset.pop("epoch_ms", None)
            if spec.get("epoch_ms") is not None:
                preset["epoch_ms"] = int(spec["epoch_ms"])
                preset.pop("iso_str", None)
            if "tz" in spec or "tz_name" in spec:
                tz_v = spec.get("tz") if "tz" in spec else spec.get("tz_name")
                preset["tz_name"] = tz_v
            if spec.get("frozen") is not None:
                preset["frozen"] = bool(spec["frozen"])
            if spec.get("step_ms") is not None:
                preset["step_ms"] = int(spec["step_ms"])
            self._clock_preset = preset
        if self._stubs is not None:
            from core.java.glass_runner import apply_virtual_clock_spec
            eng = (getattr(self, "_fusion", None) or {}).get("eng")
            st = apply_virtual_clock_spec(self._stubs, spec, eng=eng)
            if not spec.get("reset"):
                preset = dict(getattr(self, "_clock_preset", None) or {})
                preset["epoch_ms"] = st.get("epoch_ms")
                preset["frozen"] = st.get("frozen")
                if st.get("tz") and st.get("tz") != "System":
                    preset["tz_name"] = st.get("tz")
                preset.pop("iso_str", None)
                self._clock_preset = preset
            self._log("> Virtuelle Uhr: %s (TZ=%s, freeze=%s)" % (st.get("iso"), st.get("tz"), st.get("frozen")))
            self._notify_glass_ui_updated()
            return st
        self._log("> Virtuelle-Uhr-Preset aktualisiert: %r" % (self._clock_preset,))
        return dict(self._clock_preset or {"reset": True})

    def set_run_options(self, **opts):
        """Setzt Ausfuehrungs-, Seam-, Budget- & Trace-Optionen (auch per MCP/Queue)."""
        if "inspect_mode" in opts and opts["inspect_mode"] is not None:
            raw_im = str(opts["inspect_mode"]).strip().lower()
            if "arm" in raw_im and "glass" not in raw_im:
                self.set_inspect_mode("arm64")
            elif raw_im in ("method", "einzelmethode"):
                self.native_fusion = False
                mv = getattr(getattr(self, "view", None), "_mode_var", None)
                if mv is not None:
                    try: mv.set("Einzelmethode")
                    except Exception: pass
            elif raw_im in ("glass_arm64", "gläsern+arm64", "glaesern+arm64"):
                self.native_fusion = True
                mv = getattr(getattr(self, "view", None), "_mode_var", None)
                if mv is not None:
                    try: mv.set("Gläsern+ARM64")
                    except Exception: pass
            elif raw_im in ("glass", "gläsern app", "glaesern app"):
                self.native_fusion = False
                mv = getattr(getattr(self, "view", None), "_mode_var", None)
                if mv is not None:
                    try: mv.set("Gläsern App")
                    except Exception: pass
            else:
                self.set_inspect_mode(raw_im)
        if "native_fusion" in opts and opts["native_fusion"] is not None:
            self.native_fusion = bool(opts["native_fusion"])
        if "budget" in opts and opts["budget"] is not None:
            b_val = str(opts["budget"]).strip()
            self._budget_override = b_val
            v_b = getattr(getattr(self, "view", None), "v_step_budget", None)
            if v_b is not None:
                try: v_b.set(b_val)
                except Exception: pass
            if self._jdb is not None:
                self._jdb.set_budget(self._parse_budget_steps(), pause_on_budget=True)
        if "seam_profile" in opts and opts["seam_profile"] is not None:
            self.set_seam_profile(str(opts["seam_profile"]))
        if "persist_session" in opts and opts["persist_session"] is not None:
            self.set_persist_session(bool(opts["persist_session"]))
        elif "persist_appdata" in opts and opts["persist_appdata"] is not None:
            self.set_persist_session(bool(opts["persist_appdata"]))
        for attr in ("entry_stop", "tolerant", "overlay_smali", "overlay_libcarve",
                     "overlay_libforge", "record_regs", "apply_patches"):
            if attr in opts and opts[attr] is not None:
                setattr(self, attr, bool(opts[attr]))
        if "record_timeline" in opts and opts["record_timeline"] is not None:
            self.record_timeline = bool(opts["record_timeline"])
            if self._jdb is not None:
                self._jdb.record_timeline = self.record_timeline
        if "stop_on_exception" in opts and opts["stop_on_exception"] is not None:
            unh = bool(opts.get("exception_unhandled_only", getattr(self, "exception_unhandled_only", True)))
            self.set_stop_on_exception(bool(opts["stop_on_exception"]), unhandled_only=unh)
        if "flight_recorder" in opts and opts["flight_recorder"] is not None:
            self.set_flight_recorder(bool(opts["flight_recorder"]))
        self._refresh()

    def set_net_mode(self, mode):
        """AP1.5/6C: Netz-Modus umschalten: 'mock' | 'live' | 'db_replay' (greift sofort live)."""
        low = str(mode or "").strip().lower()
        if "live" in low:
            self.net_mode = "live"
        elif "db" in low:
            self.net_mode = "db_replay"
            if getattr(self, "_api_db", None) is None and getattr(self, "app", None) is not None:
                try:
                    dbp = (getattr(self.app, "cfg", None).paths or {}).get("API_DB")
                    if dbp:
                        from services.api_db_service import ApiDbService
                        self._api_db = ApiDbService(dbp)
                except Exception:
                    pass
        else:
            self.net_mode = "mock"
        self._log("> Netz-Modus aktiv (greift sofort live): %s" % self.net_mode)
        self._refresh()

    def _match_net_mock(self, req):
        """Prueft, ob eine explizite Mock-Regel fuer den Request vorliegt."""
        from core.java.net_recorder import NetResponse, ReplayResponder
        rr = getattr(self, "_net_responder", None)
        if rr is None:
            return None
        if isinstance(rr, ReplayResponder):
            rp = rr.match(req)
            if rp is not None:
                hdrs = dict(getattr(rp, "headers", {}) or {})
                hdrs.setdefault("X-Glass-Source", "mock-rule")
                return NetResponse(rp.code, hdrs, rp.body, getattr(rp, "message", "OK"))
            return None
        if callable(rr):
            return rr(req)
        return None

    def _dispatch_net_request(self, req):
        """Dynamischer Netz-Responder: prueft Mocks + `self.net_mode` bei jedem Request neu."""
        from core.java.net_recorder import DbReplayResponder, NetResponse
        mocked = self._match_net_mock(req)
        if mocked is not None:
            return mocked
        mode = str(getattr(self, "net_mode", "mock") or "mock").strip().lower()
        if mode == "live":
            return self._net_responder_live(req)
        if mode == "db_replay":
            rp = DbReplayResponder(getattr(self, "_api_db", None),
                                   fallback=getattr(self, "_net_responder", None))(req)
            if isinstance(rp, NetResponse):
                hdrs = dict(rp.headers or {})
                hdrs.setdefault("X-Glass-Source", "db-replay")
                return NetResponse(rp.code, hdrs, rp.body, rp.message)
            return rp
        url = str(getattr(req, "url", "") or "")
        hint_key = "%s %s" % (getattr(req, "method", "GET"), url.split("?", 1)[0])
        seen = getattr(self, "_mock_hint_urls", None)
        if seen is None:
            seen = set()
            self._mock_hint_urls = seen
        if hint_key not in seen:
            seen.add(hint_key)
            self._log(
                "> [Netz] Mock/Replay ohne Regel fuer %s -> leeres 200 (0B). "
                "Tipp: Im Reiter 'Netz' unten Modus auf 'Live-HTTP' stellen fuer echte Server-Antwort!" % hint_key
            )
        return NetResponse(
            200,
            {"X-Glass-Source": "mock-default (0B — Modus auf 'Live-HTTP' stellen fuer echte Server-Antwort)"},
            b"",
            "OK",
        )

    @staticmethod
    def _decode_live_body(raw_b, hdrs):
        """Entpackt gzip/deflate-komprimierte Server-Antworten transparent."""
        if not raw_b:
            return b""
        ce = ""
        for hk, hv in (hdrs or {}).items():
            if str(hk).lower() == "content-encoding":
                ce = str(hv).lower()
                break
        if "gzip" in ce or raw_b[:2] == b"\x1f\x8b":
            try:
                import gzip as _gzip
                return _gzip.decompress(raw_b)
            except Exception:
                pass
        if "deflate" in ce:
            import zlib as _zlib
            for wbits in (_zlib.MAX_WBITS, -_zlib.MAX_WBITS):
                try:
                    return _zlib.decompress(raw_b, wbits)
                except Exception:
                    pass
        return raw_b

    def _net_responder_live(self, req):
        """AP1.5: Echte HTTP-Anfrage der emulierten App via urllib. Laeuft durch intercept_rules +
        api_traffic.db (in net_recorder._fire), also beobacht-/manipulierbar im API-Inspector."""
        from core.java.net_recorder import NetResponse
        import urllib.request as _u
        import urllib.error as _ue
        try:
            r = _u.Request(req.url, data=(req.body or None), method=req.method or "GET")
            req_hdrs = {str(k): str(v) for k, v in (req.headers or {}).items()}
            if not any(k.lower() == "user-agent" for k in req_hdrs):
                req_hdrs["User-Agent"] = "okhttp/4.12.0"
            if not any(k.lower() == "accept" for k in req_hdrs):
                req_hdrs["Accept"] = "application/json, */*"
            for k, v in req_hdrs.items():
                try:
                    r.add_header(k, v)
                except Exception:
                    pass
            with _u.urlopen(r, timeout=15) as resp:
                hdrs = {k: v for k, v in resp.headers.items()}
                body = self._decode_live_body(resp.read(), hdrs)
                hdrs["X-Glass-Source"] = "live-http"
                self._log("> [Netz] Live-HTTP %s %s -> %s (%d B)" % (
                    getattr(req, "method", "GET"), getattr(req, "url", "?"), resp.status, len(body)))
                return NetResponse(resp.status, hdrs, body, getattr(resp, "reason", "OK"))
        except _ue.HTTPError as he:
            hdrs = {k: v for k, v in (he.headers.items() if he.headers else ())}
            try:
                raw_err = he.read() if hasattr(he, "read") else b""
            except Exception:
                raw_err = b""
            body = self._decode_live_body(raw_err, hdrs)
            hdrs["X-Glass-Source"] = "live-http"
            self._log("> [Netz] Live-HTTP %s %s -> %s (%d B)" % (
                getattr(req, "method", "GET"), getattr(req, "url", "?"), he.code, len(body)))
            return NetResponse(int(he.code), hdrs, body, str(getattr(he, "reason", "HTTP Error")))
        except Exception as e:
            self._log("> Live-HTTP-Fehler (%s): %r" % (getattr(req, "url", "?"), e))
            return NetResponse(502, {"X-Glass-Source": "live-http-error"},
                               str(e).encode("utf-8", "replace"), "Bad Gateway")

    def add_action_point(self, cls, name, sig, pc, assigns, cond=None,
                         when_continue=False, one_shot=False, hit=None):
        spec = dict(cls=cls, name=name, sig=sig, pc=(None if pc in ("", None) else int(pc)),
                    assigns=list(assigns or []), cond=(cond or None),
                    when_continue=bool(when_continue), one_shot=bool(one_shot),
                    hit=(int(hit) if hit else None))
        self.aps.append(spec)
        if self._jdb:
            self._jdb.add_action_point(**spec)
        self._log("> Aktionspunkt + %s->%s pc=%s cond=%s -> %s" % (
            cls, name, spec["pc"], cond or "-", "continue" if when_continue else "pause"))

    def clear_action_points(self):
        self.aps = []
        if self._jdb:
            self._jdb.clear_action_points()
        self._log("> Aktionspunkte geleert")

    # --- Netz-Mock/Replay (speist ReplayResponder fuer den naechsten App-Lauf) ---
    def add_net_mock(self, url, code=200, body="", method=None):
        from core.java.net_recorder import ReplayResponder, NetResponse
        if not isinstance(self._net_responder, ReplayResponder):
            self._net_responder = ReplayResponder()
        key = url if not method else ("%s %s" % (str(method).upper(), url))
        try:
            code = int(code)
        except Exception:
            code = 200
        body_b = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self._net_responder.mapping[key] = NetResponse(code, {}, body_b, "OK")
        self._log("> Netz-Mock + %s -> %s (%d B)" % (key, code, len(body_b)))
        return key

    def clear_net_mocks(self):
        self._net_responder = None
        self._log("> Netz-Mocks geleert (Record-Modus)")

    def net_mocks(self):
        from core.java.net_recorder import ReplayResponder
        if isinstance(self._net_responder, ReplayResponder):
            return dict(self._net_responder.mapping)
        return {}

    def save_net_log_json(self, path):
        """Exportiert aufgezeichnete Netz-Eintraege im API-Inspector-JSON-Format."""
        import time as _time
        out = []
        entries = list(getattr(getattr(self, "net_log", None), "entries", []) or [])
        for idx, e in enumerate(entries, start=1):
            req = e["req"]
            resp = e["resp"]
            req_b = req.body.decode("utf-8", "replace") if isinstance(getattr(req, "body", b""), (bytes, bytearray)) else str(getattr(req, "body", "") or "")
            res_b = resp.body.decode("utf-8", "replace") if isinstance(getattr(resp, "body", b""), (bytes, bytearray)) else str(getattr(resp, "body", "") or "")
            out.append({
                "id": idx,
                "timestamp": _time.time(),
                "method": getattr(req, "method", "GET"),
                "url": getattr(req, "url", ""),
                "status": int(getattr(resp, "code", 200) or 200),
                "req_headers": json.dumps(dict(getattr(req, "headers", {}) or {})),
                "req_body": req_b,
                "res_headers": json.dumps(dict(getattr(resp, "headers", {}) or {})),
                "res_body": res_b,
                "comment": "Debugger-Log",
            })
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
        self._log("> Netz-Log gespeichert: %s (%d Eintraege)" % (path, len(out)))
        return len(out)

    def load_net_log_file(self, path, as_replay=True):
        """Laedt API-Log (JSON aus API-Inspector/Debugger oder SQLite .db) in Netz-Log & Replay-Responder."""
        from core.java.net_recorder import NetLog, NetRequest, NetResponse, ReplayResponder

        def _parse_hdrs(raw):
            if isinstance(raw, dict):
                return dict(raw)
            if isinstance(raw, str) and raw.strip():
                try:
                    d = json.loads(raw)
                    if isinstance(d, dict):
                        return dict(d)
                except Exception:
                    pass
            return {}

        def _to_bytes(val):
            if isinstance(val, (bytes, bytearray)):
                return bytes(val)
            return str(val or "").encode("utf-8", "replace")

        if not path or not os.path.isfile(path):
            self._log("> Netz-Log nicht gefunden: %s" % path)
            return 0

        items = []
        low = path.lower()
        if low.endswith((".db", ".sqlite", ".sqlite3")):
            from services.api_db_service import ApiDbService
            rows = ApiDbService(path).get_requests()
            for r in rows:
                items.append({
                    "method": r[2], "url": r[3], "status": r[4],
                    "req_headers": r[5], "req_body": r[6],
                    "res_headers": r[7], "res_body": r[8],
                })
        else:
            with open(path, "r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
            if isinstance(raw_data, dict):
                raw_list = raw_data.get("entries") or raw_data.get("packets") or [raw_data]
            elif isinstance(raw_data, list):
                raw_list = raw_data
            else:
                raw_list = []
            for row in raw_list:
                if not isinstance(row, dict):
                    continue
                if "req" in row and "resp" in row:
                    rq, rp = row["req"] or {}, row["resp"] or {}
                    items.append({
                        "method": rq.get("method", "GET"),
                        "url": rq.get("url", ""),
                        "status": rp.get("code", 200),
                        "req_headers": rq.get("headers", {}),
                        "req_body": rq.get("body", rq.get("body_preview", "")),
                        "res_headers": rp.get("headers", {}),
                        "res_body": rp.get("body", rp.get("body_preview", "")),
                    })
                else:
                    items.append(row)

        log = NetLog()
        if as_replay and not isinstance(self._net_responder, ReplayResponder):
            self._net_responder = ReplayResponder()
        for it in items:
            meth = str(it.get("method") or "GET").upper()
            url = str(it.get("url") or "")
            if not url:
                continue
            code = int(it.get("status") or it.get("code") or 200)
            req = NetRequest(meth, url, _parse_hdrs(it.get("req_headers")), _to_bytes(it.get("req_body")))
            resp = NetResponse(code, _parse_hdrs(it.get("res_headers")), _to_bytes(it.get("res_body")), "Replay")
            log.record(req, resp)
            if as_replay:
                self._net_responder.mapping["%s %s" % (meth, url)] = resp
                base_u = url.split("?", 1)[0]
                self._net_responder.mapping.setdefault("%s %s" % (meth, base_u), resp)
        self.net_log = log
        if as_replay:
            self.net_mode = "mock"
            if getattr(self, "_stubs", None) is not None:
                self._stubs.net_responder = self._dispatch_net_request
        self._log("> Netz-Log geladen (%d Eintraege -> Replay aktiv)" % len(log.entries))
        return len(log.entries)

    def import_net_from_api_db(self):
        """Laedt aufgezeichnete Requests aus der API-Inspector-SQLite-DB in Netz-Log & Replay."""
        cfg = getattr(self.app, "cfg", None)
        paths = getattr(cfg, "paths", None) or {}
        db_path = paths.get("API_DB") or os.path.join(self._base_dir(), "api_traffic.db")
        if not db_path or not os.path.isfile(db_path):
            self._log("> Keine API-Inspector-DB gefunden (%s)" % db_path)
            return 0
        return self.load_net_log_file(db_path, as_replay=True)

    def markers(self):
        return self._jdb.marker_list() if self._jdb else []

    # --- gläserne UI-Surface (eine Logik fuer GUI + MCP) ---
    # ---- Live-App-State (geladene Klassen + Statics, beobachten + aendern) --
    def cur_vm(self):
        """Aktive VM des Laufs (ueber den JavaDebugger) oder None."""
        jdb = self._jdb
        return getattr(jdb, "vm", None) if jdb is not None else None

    def loaded_classes(self):
        """Sortierte Liste der initialisierten Klassen (vm._clinit_done)."""
        vm = self.cur_vm()
        if vm is None:
            return []
        try:
            return sorted(getattr(vm, "_clinit_done", []) or [])
        except Exception:
            return []

    def app_statics(self, class_filter=None):
        """{'Lcls;->feld': wert} der statischen Felder, optional nach Klassenname gefiltert."""
        vm = self.cur_vm()
        out = {}
        if vm is None:
            return out
        for cls, fields in (getattr(vm, "statics", {}) or {}).items():
            if class_filter and class_filter not in cls:
                continue
            if not isinstance(fields, dict):
                continue
            for fld, val in fields.items():
                out["%s->%s" % (cls, fld)] = val
        return out

    def current_method(self):
        """Aktuelle Methode (am Haltepunkt cls->name, sonst vm.last_method)."""
        jdb = self._jdb
        if jdb is None:
            return None
        try:
            st = jdb.state()
            if st.get("cls"):
                return "%s->%s%s" % (st.get("cls"), st.get("name"), st.get("sig") or "")
        except Exception:
            pass
        vm = self.cur_vm()
        lm = getattr(vm, "last_method", None) if vm is not None else None
        return ("%s->%s%s" % lm) if isinstance(lm, tuple) and len(lm) == 3 else (str(lm) if lm else None)

    def set_static(self, cls, field, value):
        """Statisches Feld LIVE mitten im Lauf aendern (gläsern + manipulierbar)."""
        vm = self.cur_vm()
        if vm is None:
            self._log("> set_static: kein VM aktiv (erst Lauf starten)"); return False
        v = value
        if isinstance(value, str):
            t = value.strip()
            try:
                v = int(t, 0)
            except Exception:
                v = t
        try:
            vm.statics.setdefault(cls, {})[field] = v
            try:
                from core.application.event_bus import EventBus
                EventBus.publish("MANIPULATION_APPLIED",
                                 {"kind": "static", "cls": cls, "field": field, "value": v})
            except Exception:
                pass
            self._log("> set_static %s->%s = %r" % (cls, field, v))
            return True
        except Exception as e:
            self._log("> set_static FEHLER: %r" % e); return False

    def active_surface(self):
        """Aktive renderbare Surface des gläsernen Laufs (SceneGraph ueber glass_tree) oder None."""
        from core.java.surface import SceneGraphSurface
        tree = getattr(self, "glass_tree", None)
        if tree is None:
            return None
        stubs = getattr(self, "_stubs", None)
        return SceneGraphSurface(tree, vm=self.cur_vm(), activity=getattr(stubs, "_current_activity", None))

    def ui_snapshot(self):
        from core.java.surface import snapshot
        return snapshot(self.active_surface())

    def ui_tap(self, x, y):
        from core.java.surface import tap
        return tap(self.active_surface(), x, y)

    # --- Fusion Java<->nativ: Grenz-Aktionspunkte + Lib-Loader ---
    def _base_dir(self):
        """Repo-Basis (enthaelt data/native/...). Aus Config-BASE_DIR, sonst cwd."""
        try:
            b = self.app.cfg.config.get("BASE_DIR")
            if b:
                return b
        except Exception:
            pass
        return os.getcwd()

    def _resolve_pkg(self):
        pkg = getattr(self, "_active_pkg", "") or getattr(getattr(self, "instance_state", None), "package", "")
        if pkg:
            return pkg
        root = getattr(self, "zone_root", "") or ""
        if not root and hasattr(self, "_resolve_root"):
            try:
                root = self._resolve_root() or ""
            except Exception:
                root = ""
        if root:
            try:
                from core.java.manifest import parse_manifest
                mp = getattr(parse_manifest(root), "package", "") or ""
                if mp:
                    return mp
            except Exception:
                pass
            parent = os.path.basename(os.path.dirname(root))
            if "." in parent:
                return parent
        return "default.app"

    def _selfcache_so_path(self, filename):
        seam_cfg = getattr(self, "seam_config", None)
        sc_dir = getattr(seam_cfg, "self_cache_dir", "data/vfs_selfcache") if seam_cfg else "data/vfs_selfcache"
        if not os.path.isabs(sc_dir):
            sc_dir = os.path.join(self._base_dir(), sc_dir)
        pkg = self._resolve_pkg()
        return os.path.join(sc_dir, pkg, os.path.basename(filename))

    def _appdata_target_dir(self, pkg=None):
        """Liefert das AppData-Zielverzeichnis der aktiven Instanz und haelt instance_state.package synchron."""
        pkg_s = pkg or self._resolve_pkg()
        inst = getattr(self, "instance_state", None)
        bdir = self._base_dir()
        if inst is not None:
            if pkg_s and pkg_s != "default.app":
                inst.package = pkg_s
            return inst.pkg_appdata_dir(bdir)
        return os.path.join(bdir, "data", "vfs_appdata", pkg_s)

    def _should_persist_session(self) -> bool:
        inst = getattr(self, "instance_state", None)
        if inst is not None and hasattr(inst, "is_fork") and inst.is_fork():
            return True
        return bool(getattr(self, "persist_session", False))

    def _cleanup_ephemeral_session(self):
        """Raeumt temporaere In-Memory- & nicht-persistente Main-AppData nach Stop/Exit auf."""
        with self._appdata_lock:
            if self._should_persist_session() or getattr(self, "_clearing_appdata", False):
                return
            pkg = self._resolve_pkg()
            inst = getattr(self, "instance_state", None)
            is_fork = bool(inst is not None and hasattr(inst, "is_fork") and inst.is_fork())
            dst = (
                self._appdata_target_dir(pkg)
                if (not is_fork and pkg and pkg != "default.app")
                else None
            )
            stubs = getattr(self, "_stubs", None) or getattr(getattr(self, "_runner", None), "stubs", None)
            try:
                from core.java.sqlite_prefs_bridge import clear_java_appdata_state
                clear_java_appdata_state(
                    stubs=stubs,
                    app_dir=dst,
                    fusion=getattr(self, "_fusion", None),
                    package=pkg,
                )
            except Exception:
                pass

    def set_persist_session(self, enabled: bool, save_cfg: bool = True):
        """Schaltet explizit um, ob App-Session/AppData dauerhaft gespeichert oder nach Stop/Exit verworfen wird."""
        self.persist_session = bool(enabled)
        if getattr(self, "seam_config", None) is not None:
            self.seam_config.persist_appdata = self._should_persist_session()
        vp = getattr(getattr(self, "view", None), "v_persist_session", None)
        if vp is not None:
            try:
                vp.set(self.persist_session)
            except Exception:
                pass
        if save_cfg and self.app is not None and getattr(self.app, "cfg", None) is not None:
            try:
                cfg_d = getattr(self.app.cfg, "config", None)
                if isinstance(cfg_d, dict):
                    cfg_d["DEBUGGER_PERSIST_SESSION"] = self.persist_session
                if hasattr(self.app.cfg, "save"):
                    self.app.cfg.save()
            except Exception:
                pass
        if self.persist_session and getattr(self, "_stubs", None) is not None:
            self.persist_active_appdata()
        self._log(
            "> \U0001f4be Session speichern: AKTIV (App-Daten & Login bleiben erhalten)"
            if self.persist_session
            else "> \U0001f9f9 Session speichern: AUS (nach Stop/Beenden wieder clean)"
        )
        try:
            self._write_live_state_file()
        except Exception:
            pass
        return {"ok": True, "persist_session": self.persist_session}

    def on_studio_exit(self):
        """Beendet laufende Debugger-Sessions synchron beim Schliessen von RE Studio (persistiert oder verworfen)."""
        self._stop_pending = True
        self._stop_heartbeat()
        hc = getattr(self, "_hermes_client", None)
        if hc is not None and hasattr(hc, "close"):
            try:
                hc.close()
            except Exception:
                pass
            self._hermes_client = None
        eng = (getattr(self, "_fusion", None) or {}).get("eng")
        if eng is not None and getattr(eng, "cpu", None) is not None:
            try:
                eng.cpu.stopped = True
            except Exception:
                pass
        if self._jdb is not None:
            try:
                self._jdb.stop(wait_timeout=0.05)
            except Exception:
                pass
        if self._should_persist_session():
            self.persist_active_appdata()
        else:
            self._cleanup_ephemeral_session()

    def persist_active_appdata(self):
        """Schreibt geaenderte AppData-Dateien (Java SharedPrefs/SQLite + VFS 2.0) zurueck in die aktive Instanz."""
        with self._appdata_lock:
            if getattr(self, "_clearing_appdata", False) or not self._should_persist_session():
                return 0
            f = getattr(self, "_fusion", None)
            pkg = self._resolve_pkg()
            dst = self._appdata_target_dir(pkg)
            n = 0
            try:
                stubs = getattr(self, "_stubs", None) or getattr(getattr(self, "_runner", None), "stubs", None)
                if stubs is not None:
                    from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir
                    n += persist_java_appdata_to_dir(stubs, dst)
            except Exception:
                pass
            if f and f.get("eng"):
                try:
                    from core.java.fs_bridge import get_shared_fs
                    from core.java.glass_runner import persist_appdata_from_vfs
                    fs = get_shared_fs(f["eng"])
                    n += persist_appdata_from_vfs(fs, pkg, dst)
                except Exception:
                    pass
            return n

    def clear_active_appdata(self, restart: bool = False, single_method: bool = False):
        """Loescht persistierte AppData (SharedPrefs/SQLite/KeyStore/VFS) der aktiven Instanz fuer einen frischen Start."""
        import time as _t
        self._clearing_appdata = True
        try:
            if getattr(self, "_busy", False) or getattr(self, "_jdb", None) is not None:
                try:
                    self.stop(persist=False)
                except TypeError:
                    self.stop()
                wt = getattr(self, "_worker_thread", None)
                if wt is not None and wt.is_alive() and wt is not threading.current_thread():
                    wt.join(timeout=1.0)
                jdb_cur = getattr(self, "_jdb", None)
                jth = getattr(jdb_cur, "_thread", None) if jdb_cur is not None else None
                if jth is not None and jth.is_alive() and jth is not threading.current_thread():
                    jth.join(timeout=1.0)
                deadline = _t.time() + 0.35
                while getattr(self, "_busy", False) and _t.time() < deadline:
                    _t.sleep(0.02)
            with self._appdata_lock:
                pkg = self._resolve_pkg()
                dst = self._appdata_target_dir(pkg)
                stubs = getattr(self, "_stubs", None) or getattr(getattr(self, "_runner", None), "stubs", None)
                from core.java.sqlite_prefs_bridge import clear_java_appdata_state
                res = clear_java_appdata_state(
                    stubs=stubs,
                    app_dir=dst,
                    fusion=getattr(self, "_fusion", None),
                    package=pkg,
                )
            if getattr(self, "glass_tree", None) is not None:
                try:
                    self.glass_tree.root = None
                    self.glass_tree.touch()
                except Exception:
                    pass
            removed = int(res.get("removed_files", 0))
            self._log("> \U0001f9f9 AppData zurückgesetzt (%s, %d Dateien entfernt)" % (pkg, removed))
        finally:
            self._clearing_appdata = False
        if restart:
            if single_method:
                self.start()
            else:
                self.start_app()
        else:
            try:
                self._refresh()
                self._write_live_state_file()
            except Exception:
                pass
        return {"ok": True, "package": pkg, "appdata_dir": dst, "removed_files": removed, "restarted": bool(restart)}

    def set_seam_profile(self, profile_name):
        """Wechselt Ebene-1-Seam-Profil (faithful | self_cache | noop_native)."""
        try:
            from core.java.seam_config import SeamConfig
            self.seam_config = SeamConfig.from_profile(profile_name)
            self.seam_config.persist_appdata = self._should_persist_session()
            inst = getattr(self, "instance_state", None)
            if inst is not None:
                inst.seam_profile = self.seam_config.unpack_mode
            self._log("> Seam-Profil: %s" % self.seam_config.unpack_mode)
        except Exception as e:
            self._log("> Seam-Profil Fehler: %r" % e)

    def _get_fusion_loader(self, f):
        loader = f.get("loader") if isinstance(f, dict) else None
        if loader is not None:
            loader.seam_config = getattr(self, "seam_config", loader.seam_config)
            return loader
        from core.java.fusion_loader import NativeFusionLoader
        root = self._resolve_root() or getattr(self, "zone_root", "") or ""
        loader = NativeFusionLoader(
            svc=f["svc"], eng=f["eng"], jni=f["jni"], table=f["table"],
            ffi=f.get("ffi"), stubs=f.get("stubs"), app_root=root,
            pkg=self._resolve_pkg(), base_dir=self._base_dir(),
            seam_config=getattr(self, "seam_config", None), log_fn=self._log,
        )
        f["loader"] = loader
        return loader

    def _find_unpacked_twin(self, packed_path):
        """Sucht entpackte Version: erst im App-Self-Cache (data/vfs_selfcache/<pkg>/),
        nur bei explizitem allow_external_dec_cache auch in data/native/<plugin>/lib/<sha>/."""
        from core.java.fusion_loader import NativeFusionLoader
        loader = NativeFusionLoader(
            svc=None, eng=None, jni=None, table=None,
            pkg=self._resolve_pkg(), base_dir=self._base_dir(),
            seam_config=getattr(self, "seam_config", None), log_fn=self._log,
        )
        return loader.find_unpacked_twin(packed_path)

    def _fusion_load_library(self, name):
        """System.loadLibrary im gläsernen Lauf: delegiert an NativeFusionLoader."""
        f = self._fusion
        if not f:
            self._log("> loadLibrary(%s): keine Fusions-Engine aktiv" % name); return
        self._get_fusion_loader(f).load_library(name)

    def _fusion_vfs_read(self, eng, path):
        """Liest Bytes aus dem (virtuellen) lokalen Speicher der App via NativeFusionLoader."""
        from core.java.fusion_loader import NativeFusionLoader
        return NativeFusionLoader.vfs_read(eng, path)

    def _fusion_load_path(self, path):
        """System.load(absPfad): delegiert an NativeFusionLoader."""
        f = self._fusion
        if not f:
            self._log("> System.load(%s): keine Fusions-Engine aktiv" % path); return
        self._get_fusion_loader(f).load_path(path)

    def add_boundary_ap(self, fnptr=None, this_cls=None, cond=None,
                        arg_assigns=None, ret_expr=None, one_shot=False, hit=None):
        spec = dict(fnptr=(None if fnptr in ("", None) else int(fnptr, 0) if isinstance(fnptr, str) else int(fnptr)),
                    this_cls=(this_cls or None), cond=(cond or None),
                    arg_assigns=list(arg_assigns or []), ret_expr=(ret_expr or None),
                    one_shot=bool(one_shot), hit=(int(hit) if hit else None))
        self.jni_aps.append(spec)
        if self._fusion and self._fusion.get("hook"):
            try:
                self._fusion["hook"].add_action_point(**spec)
            except Exception as e:
                self._log("> Grenz-AP-Fehler: %r" % e)
        self._log("> Grenz-Aktionspunkt + fn=%s cls=%s ret=%s" % (spec["fnptr"], spec["this_cls"], ret_expr or "-"))

    def clear_boundary_aps(self):
        self.jni_aps = []
        if self._fusion and self._fusion.get("hook"):
            try:
                self._fusion["hook"].clear_action_points()
            except Exception:
                pass
        self._log("> Grenz-Aktionspunkte geleert")

    def jni_entries(self):
        return self.jni_log.to_list() if self.jni_log is not None else []

    def fusion_os_net(self):
        """Native Netz-Aufzeichnung (OS-8) des gläsernen Laufs: {"log":[("connect",host,port),...]}."""
        f = self._fusion or {}
        eng = f.get("eng")
        if eng is None:
            return None
        return getattr(getattr(eng, "_os", None), "net", None)

    def _sync_fusion_os_net_to_log(self):
        """Spiegelt neue native Socket-Ereignisse aus OS-8 in self.net_log fuer die Netz-Spalte."""
        net = self.fusion_os_net()
        nl = getattr(self, "net_log", None)
        if not isinstance(net, dict) or nl is None:
            return
        log = net.get("log")
        if not isinstance(log, list):
            return
        already = int(getattr(self, "_synced_os_net_count", 0) or 0)
        if already >= len(log):
            return
        from core.java.net_recorder import NetRequest, NetResponse
        for ev in log[already:]:
            if isinstance(ev, (tuple, list)) and len(ev) >= 3:
                op, host, port = str(ev[0]), str(ev[1]), ev[2]
                method = "CONNECT" if op == "connect" else op.upper()
                url = f"tcp://{host}:{port}"
            else:
                method = "SOCKET"
                url = str(ev)
            nl.record(
                NetRequest(method, url, {"X-Glass-Source": "native-socket"}),
                NetResponse(200, {"X-Glass-Source": "native-socket"}, b"", "CONNECTED"),
            )
        self._synced_os_net_count = len(log)

    # --- native Seite am (Java-)Halt: beobachten + manipulieren ---
    def native_state(self):
        return self._session.native_state() if self._session is not None else None

    def native_mem_read(self, addr, n=16):
        return self._session.native_mem_read(addr, n) if self._session is not None else b""

    def native_mem_write(self, addr, data):
        if self._session is None:
            return False
        return self._session.native_mem_write(addr, data)

    def native_reg(self, i):
        return self._session.native_reg(i) if self._session is not None else None

    def native_set_reg(self, i, value):
        if self._session is None:
            return False
        return self._session.native_set_reg(i, value)

    def _apply_aps(self):
        if not self._jdb:
            return
        self._jdb.clear_action_points()
        for s in self.aps:
            try:
                self._jdb.add_action_point(**s)
            except Exception as e:
                self._log("> Aktionspunkt-Fehler: %r" % e)

    def goto(self, n):
        if self._jdb:
            try:
                n = max(0, int(n))
            except Exception:
                self._log("> Goto: ungueltig"); return
            if getattr(self, "_last_start_mode", "") == "app":
                self._launch_glass_app_async(pause_at_step=n)
            else:
                self._async(lambda: self._jdb.goto(n, wait_timeout=0.05), "goto %d" % n)

    def _current_bp_target(self):
        if self._jdb_sig:
            return self._jdb_sig
        c, n, s = self.current_target()
        if c and n:
            return (c, n, s or "")
        return None

    def _register_all_bps_on_jdb(self):
        if not self._jdb:
            return
        self._jdb.clear_breakpoints()
        seen = set()
        for (c, n, s, pc), meta in getattr(self, "app_bps", {}).items():
            try:
                self._jdb.add_breakpoint(c, n, s, pc, **(meta or {}))
                seen.add((c, n, s, pc))
            except Exception:
                pass
        if self._jdb_sig:
            c, n, s = self._jdb_sig
            for pc in self.bps:
                if (c, n, s, pc) not in seen:
                    try:
                        self._jdb.add_breakpoint(c, n, s, pc, **self._meta(pc))
                    except Exception:
                        pass

    def add_app_bp(self, cls, name, sig="", pc=None, cond=None, hit=None, one_shot=False):
        """Setzt einen klassenuebergreifenden Breakpoint (aus Smali Studio oder MCP)."""
        key = (cls, name, sig or "", (None if pc in ("", None) else int(pc)))
        meta = {}
        if cond and str(cond).strip():
            meta["cond"] = str(cond).strip()
        if hit and str(hit).strip():
            meta["hit"] = int(hit)
        if one_shot:
            meta["one_shot"] = True
        self.app_bps[key] = meta
        tgt = self._current_bp_target()
        if tgt and tgt[0] == key[0] and tgt[1] == key[1] and key[3] is not None:
            self.bps = sorted(set(self.bps + [key[3]]))
            if meta:
                self.bp_meta[key[3]] = dict(meta)
        self._register_all_bps_on_jdb()
        self._log("> App-BP+ %s->%s pc=%s" % (cls, name, key[3]))
        self._refresh()
        return key

    def add_bp(self, pc):
        try:
            pc = int(pc)
        except Exception:
            self._log("> BP: ungueltiger pc"); return
        self.bps = sorted(set(self.bps + [pc]))
        tgt = self._current_bp_target()
        if tgt:
            self.app_bps[(tgt[0], tgt[1], tgt[2], pc)] = dict(self._meta(pc))
        if self._jdb:
            self._register_all_bps_on_jdb()
        self._log("> BP gesetzt: %s" % self.bps)
        self._refresh()

    def clear_bp(self):
        self.bps = []
        self.bp_meta = {}
        self.app_bps = {}
        if self._jdb:
            self._jdb.clear_breakpoints()
        self._log("> BP geleert")
        self._refresh()

    def set_reg(self, i, v):
        if not self._jdb:
            self._log("> kein Lauf aktiv"); return
        try:
            i = int(i); v = int(v)
        except Exception:
            self._log("> Set Reg: idx/Wert ganzzahlig"); return
        ok = self._jdb.set_register(i, v)
        self._log("> set_register(%d,%d)->%s" % (i, v, ok))
        self._refresh()

    def studio_load(self):
        root = self._resolve_root()
        if not root:
            self._log("> keine Zonen-Wurzel"); return
        rel = (self.relpath or "").strip()
        if not rel:
            self._log("> relpath/Deskriptor fehlt"); return
        cands = []
        if rel.startswith("L") and rel.endswith(";"):
            sub = rel[1:-1].split("/")
            for d in (sorted(glob.glob(os.path.join(root, "smali*"))) or [root]):
                cands.append(os.path.join(d, *sub) + ".smali")
        else:
            cands.append(os.path.join(root, rel)); cands.append(rel)
        for p in cands:
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        self.source = fh.read()
                except Exception as e:
                    self._log("> Laden FEHLER: %r" % e); return
                ed = self._wid("editor")
                if ed is not None:
                    try:
                        ed.delete("1.0", "end"); ed.insert("1.0", self.source)
                    except Exception:
                        pass
                self._log("> geladen: %s (%d Zeichen)" % (p, len(self.source)))
                self._rebuild_view()
                return
        self._log("> Datei nicht gefunden (%s)" % (cands[0] if cands else rel))

    def lifecycle(self):
        root = self._resolve_root()
        entry = (self.app_entry or "").strip()
        if not root:
            self._log("> keine Zonen-Wurzel"); return
        if not entry:
            self._log("> App-Entry fehlt (Lcom/foo/MainActivity;)"); return
        self._log("> App-Lifecycle %s (Zone %s) …" % (entry, self.zone))

        def _w():
            try:
                from mcp_server import tools
                r = tools.native_app_lifecycle_run(None, studio_root=root, zone=self.zone, entry=entry)
                if not r.get("ok"):
                    self._log("> Lifecycle: %s" % r.get("hinweis")); return
                self._log("> Lifecycle called=%s skipped=%s missing=%d" % (
                    [c["m"] for c in r.get("called", [])],
                    [s["m"] for s in r.get("skipped", [])], len(r.get("missing", []))))
                self._fill_missing(r.get("missing", []))
            except Exception as e:
                self._log("> Lifecycle FEHLER: %r" % e)
        threading.Thread(target=_w, daemon=True).start()

    def _fill_missing(self, miss):
        self._last_missing = list(miss or [])
        def _u():
            tr = self._wid("missing")
            if tr is None:
                return
            try:
                apply_fn = getattr(tr, "_apply_missing_data", None)
                if callable(apply_fn):
                    apply_fn(self._last_missing)
                    return
                for i in tr.get_children():
                    tr.delete(i)
                for m in self._last_missing[:200]:
                    tr.insert("", "end", text=m.get("cls", ""),
                              values=("%s%s" % (m.get("name", ""), m.get("sig", "")),))
            except Exception:
                pass
        self._after(_u)

    # ---- Quelltext-Modell (pc <-> Zeile) fuer den glaesernen Editor --------
    def refresh_source_map(self):
        """source_map der Zielmethode neu berechnen, pc<->Zeile-Indizes aufbauen."""
        self._srcmap = []
        self._line_by_pc = {}
        self._pc_by_line = {}
        try:
            from core.java import smali as Sm
            nm = (self.name or "").strip() or None
            sig = None
            jsig = getattr(self, "_jdb_sig", None)
            if isinstance(jsig, tuple) and len(jsig) >= 3 and jsig[1] == (nm or ""):
                sig = jsig[2] or None
            self._srcmap = Sm.source_map(self.source or "", nm, sig)
        except Exception as e:
            self._log("> source_map FEHLER: %r" % e)
            return
        for e in self._srcmap:
            self._line_by_pc[e["pc"]] = e["lineno"]
            self._pc_by_line.setdefault(e["lineno"], []).append(e["pc"])

    def pc_to_line(self, pc):
        try:
            return self._line_by_pc.get(int(pc))
        except Exception:
            return None

    def line_to_pcs(self, line):
        try:
            return list(self._pc_by_line.get(int(line), []))
        except Exception:
            return []

    def current_pc_line(self):
        return self.pc_to_line(self._last_pc) if self._last_pc is not None else None

    def line_has_bp(self, line):
        return any(pc in self.bps for pc in self.line_to_pcs(line))

    def _resync_bps(self):
        if not self._jdb:
            return
        self._register_all_bps_on_jdb()

    def toggle_bp(self, pc):
        try:
            pc = int(pc)
        except Exception:
            return
        tgt = self._current_bp_target()
        if pc in self.bps:
            self.bps = [p for p in self.bps if p != pc]
            self.bp_meta.pop(pc, None)
            if tgt:
                self.app_bps.pop((tgt[0], tgt[1], tgt[2], pc), None)
            self._resync_bps()
            self._log("> BP entfernt: pc=%d -> %s" % (pc, self.bps))
        else:
            self.bps = sorted(set(self.bps + [pc]))
            if tgt:
                self.app_bps[(tgt[0], tgt[1], tgt[2], pc)] = dict(self._meta(pc))
            self._resync_bps()
            self._log("> BP gesetzt: pc=%d -> %s" % (pc, self.bps))
        self._refresh()

    def toggle_bp_at_line(self, line):
        pcs = self.line_to_pcs(line)
        if not pcs:
            self._log("> Zeile %s ist keine Instruktion" % line)
            return
        for pc in pcs:
            self.toggle_bp(pc)

    def _rebuild_view(self):
        v = getattr(self, "view", None)
        if v is None:
            return
        def _r():
            try:
                v.rebuild_code_model()
            except Exception:
                pass
        self._after(_r)

    # ---- Breakpoint-Varianten (bedingt / hit-count / one-shot) -------------
    def _meta(self, pc):
        m = self.bp_meta.get(pc) or {}
        out = {}
        if m.get("cond"):
            out["cond"] = m["cond"]
        if m.get("hit"):
            out["hit"] = m["hit"]
        if m.get("one_shot"):
            out["one_shot"] = True
        return out

    def _bp_label(self, pc):
        m = self.bp_meta.get(pc) or {}
        parts = []
        if m.get("cond"):
            parts.append("if %s" % m["cond"])
        if m.get("hit"):
            parts.append("hit>=%s" % m["hit"])
        if m.get("one_shot"):
            parts.append("1x")
        return ", ".join(parts) if parts else "pc"

    def add_bp_ex(self, pc, cond=None, hit=None, one_shot=False):
        try:
            pc = int(pc)
        except Exception:
            self._log("> BP: ungueltiger pc"); return
        meta = {}
        if cond and str(cond).strip():
            meta["cond"] = str(cond).strip()
        if hit and str(hit).strip():
            try:
                meta["hit"] = int(hit)
            except Exception:
                pass
        if one_shot:
            meta["one_shot"] = True
        if meta:
            self.bp_meta[pc] = meta
        else:
            self.bp_meta.pop(pc, None)
        self.bps = sorted(set(self.bps + [pc]))
        tgt = self._current_bp_target()
        if tgt:
            self.app_bps[(tgt[0], tgt[1], tgt[2], pc)] = dict(meta)
        self._resync_bps()
        self._log("> BP+ pc=%d (%s)" % (pc, self._bp_label(pc)))
        self._refresh()

    def object_roots(self):
        """Objekt-Baum je Register am Haltepunkt (fuer den Objekt-Inspektor)."""
        try:
            return self._jdb.objects() if self._jdb else []
        except Exception:
            return []

    def read_memory(self, spec):
        """(base, bytes) aus ARM64-Adresse (0x...) oder Register/Frame (auch nach mode=done/stopped)."""
        spec = (spec or "").strip()
        if spec.lower().startswith("0x"):
            try:
                addr = int(spec, 16)
                data = self.native_mem_read(addr, 128)
                return addr, bytes(data or b"")
            except Exception:
                return 0, b""
        if not self._jdb:
            return 0, b""
        regs = None
        if hasattr(self._jdb, "active_raw_regs"):
            try:
                regs = self._jdb.active_raw_regs(getattr(self, "_active_stack_idx", 0))
            except Exception:
                regs = None
        if regs is None:
            if getattr(self._jdb, "mode", None) == "paused":
                try:
                    regs = list(self._jdb.current["fr"].v)
                except Exception:
                    regs = []
            else:
                cur = getattr(self._jdb, "current", None) or {}
                fr = cur.get("fr") or getattr(self._jdb, "_last_fr", None)
                regs = list(getattr(fr, "v", []) or []) if fr is not None else []
        if not regs:
            return 0, b""
        val = None
        reg_idx = None
        if spec[:1] == "v" and spec[1:].isdigit():
            i = int(spec[1:])
            if 0 <= i < len(regs):
                val = regs[i]
                reg_idx = i
        from core.java.objects import JArray, JObject, JString
        if isinstance(val, JString):
            return 0, (val.text or "").encode("utf-8", "replace")
        if isinstance(val, JArray):
            import struct
            width = {"B": 1, "Z": 1, "C": 2, "S": 2, "I": 4, "F": 4, "J": 8, "D": 8}.get(val.eltype, 4)
            fmt = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}[width]
            mask = (1 << (8 * width)) - 1
            out = bytearray()
            for el in val.data:
                try:
                    out += struct.pack(fmt, int(el) & mask)
                except Exception:
                    out += b"\x00" * width
            return 0, bytes(out)
        if isinstance(val, JObject):
            lines = [f"JObject({val.cls})"]
            for fk, fv in list((getattr(val, "fields", None) or {}).items())[:24]:
                lines.append(f"  {fk} = {repr(fv)[:120]}")
            return 0, "\n".join(lines).encode("utf-8", "replace")
        has_other_data = any(r not in (None, 0) for j, r in enumerate(regs) if j != reg_idx)
        if isinstance(val, (int, bool, float)) and not (spec == "v0" and int(val) == 0 and has_other_data):
            import struct
            if isinstance(val, float):
                return 0, struct.pack("<d", val)
            return 0, struct.pack("<q", int(val))
        # Fallback: kompakter Frame-Dump fuer v0=0/None oder spec=""/"frame"
        if val is None or spec in ("", "frame", "v0"):
            flines = ["Frame-Register:"]
            for j, r in enumerate(regs[:32]):
                if r is None and any(x is not None for x in regs[:32]):
                    continue
                if isinstance(r, JString):
                    desc = f'JString({repr((r.text or "")[:80])})'
                elif isinstance(r, JObject):
                    desc = f'JObject({r.cls}, fields={len(getattr(r, "fields", None) or {})})'
                elif isinstance(r, JArray):
                    desc = f'JArray({r.eltype}[{len(r.data)}])'
                else:
                    desc = repr(r)[:80]
                flines.append(f"  v{j} = {desc}")
            return 0, "\n".join(flines).encode("utf-8", "replace")
        return 0, b""

    # ---- Timeline-Scrubber + Lesezeichen ----------------------------------
    def timeline_len(self):
        try:
            return len(self._jdb.timeline) if self._jdb else 0
        except Exception:
            return 0

    @staticmethod
    def frac_to_step(frac, n):
        if n <= 0:
            return 0
        try:
            frac = max(0.0, min(1.0, float(frac)))
        except Exception:
            frac = 0.0
        return int(round(frac * (n - 1)))

    def scrub_to(self, frac):
        n = self.timeline_len()
        if n <= 0:
            self._log("> Scrubber: keine Timeline"); return
        self.goto(self.frac_to_step(frac, n))

    def toggle_bookmark(self, step):
        try:
            step = int(step)
        except Exception:
            return
        if step in self.bookmarks:
            self.bookmarks.discard(step)
        else:
            self.bookmarks.add(step)
        self._log("> Lesezeichen: %s" % sorted(self.bookmarks))
        self._refresh()

    def bookmark_current(self):
        st = self._jdb.state() if self._jdb else {}
        step = st.get("step")
        if step is not None and step >= 0:
            self.toggle_bookmark(step)
        else:
            self._log("> kein aktueller Schritt")

    def bookmarks_list(self):
        return sorted(self.bookmarks)

    # ---- Watch-Ausdruecke + Locals ----------------------------------------
    def add_watch(self, expr):
        e = (expr or "").strip()
        if e and e not in self.watches:
            self.watches.append(e)
            self._refresh()

    def clear_watches(self):
        self.watches = []
        self._refresh()

    def watch_values(self):
        out = []
        for e in self.watches:
            v = self._jdb.eval_in_frame(e) if self._jdb else None
            out.append((e, v))
        return out

    def locals_names(self):
        """vN/pN -> Name aus .local/.param-Direktiven der aktiven Methode oder Quelle."""
        import re
        names = {}
        src = self.source or ""
        if not src:
            return names
        scope_src = src
        cur_name = getattr(self, "name", "") or ""
        if not cur_name and self._jdb is not None:
            try:
                cur_name = (self._jdb.state().get("name") or "")
            except Exception:
                cur_name = ""
        if cur_name:
            mm = re.search(rf'^\s*\.method\b[^\n]*\s{re.escape(cur_name)}\(.*?^\s*\.end method', src, re.M | re.S)
            if mm:
                scope_src = mm.group(0)
        for chunk in (scope_src, src) if scope_src is not src else (src,):
            for m in re.finditer(r'^\s*\.local\s+([vp]\d+)\s*,\s*"([^"]*)"', chunk, re.M):
                names[m.group(1)] = m.group(2)
            for m in re.finditer(r'^\s*\.param\s+([vp]\d+)\s*,\s*"([^"]*)"', chunk, re.M):
                names.setdefault(m.group(1), m.group(2))
            if names:
                break
        return names

    # ---- Aus RAM-Index laden (Smali-Studio-Suche -> Debugger) --------------
    @staticmethod
    def _parse_method_name(method_line):
        try:
            from core.java.smali import _METHOD_RE
            m = _METHOD_RE.match(method_line.strip()) or _METHOD_RE.match(method_line)
            return m.group("name") if m else ""
        except Exception:
            return ""

    @staticmethod
    def _methods_in(content):
        from core.java.smali import _METHOD_RE
        out = []
        for ln in (content or "").splitlines():
            m = _METHOD_RE.match(ln.strip()) or _METHOD_RE.match(ln)
            if m:
                out.append(m.group("name"))
        return out

    @staticmethod
    def _pick_default_entry(names):
        for pref in ("onCreate", "attachBaseContext", "<init>", "<clinit>", "main"):
            if pref in names:
                return pref
        return names[0] if names else ""

    def _apply_source(self, source, name, rel_path, smali_dir, logmsg):
        self.source = source if source.endswith("\n") else source + "\n"
        self.name = name
        if smali_dir:
            self.zone_root = smali_dir
        self.relpath = rel_path
        ed = self._wid("editor")
        if ed is not None:
            try:
                ed.delete("1.0", "end"); ed.insert("1.0", self.source)
            except Exception:
                pass
        self._rebuild_view()
        self._log("> " + logmsg)
        return True

    def load_method_block(self, content, rel_path, target_line, smali_dir=""):
        """Treffer in einer Methode -> genau diese Methode laden. Treffer ausserhalb (z.B. die
        `.class`-Zeile) -> ganze Klasse laden und sinnvolle Startmethode vorwaehlen (ueber
        'Methode:' umstellbar). Konstruktoren (<init>/<clinit>) werden normal unterstuetzt."""
        lines = (content or "").splitlines()
        if not lines:
            self._log("> Index-Treffer leer"); return False
        i = max(0, min(int(target_line) - 1, len(lines) - 1))
        # umschliessende Methode suchen (nach oben, aber nicht ueber ein .end method hinweg)
        start = None
        j = i
        while j >= 0:
            st = lines[j].lstrip()
            if st.startswith(".method"):
                start = j; break
            if st.startswith(".end method") and j != i:
                break
            j -= 1
        if start is not None:
            end = None
            k = start + 1
            while k < len(lines):
                if lines[k].lstrip().startswith(".end method"):
                    end = k; break
                k += 1
            if end is None:
                end = len(lines) - 1
            block = "\n".join(lines[start:end + 1]) + "\n"
            name = self._parse_method_name(lines[start])
            return self._apply_source(block, name, rel_path, smali_dir,
                                      "Funktion geladen: %s  (%s, Zeile %s)" % (name or "?", rel_path, target_line))
        # kein umschliessender .method -> ganze Klasse
        all_names = self._methods_in(content)
        if not all_names:
            self._log("> %s enthaelt keine Methode (nur Klassen-/Feld-Deklarationen)" % rel_path)
            return False
        default = self._pick_default_entry(all_names)
        shown = ", ".join(all_names[:12]) + (" ..." if len(all_names) > 12 else "")
        return self._apply_source(content, default, rel_path, smali_dir,
                                  "Ganze Klasse geladen (%s). Startmethode: %s - ueber 'Methode:' umstellbar. Methoden: %s"
                                  % (rel_path, default, shown))

    def _patched_source(self):
        """Quelltext mit aktiven Smali-Patches (Sandbox, MS-5), falls apply_patches an."""
        src = self.source
        if not getattr(self, "apply_patches", False):
            return src
        try:
            studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
            pats = getattr(studio, "smali_patches", None) if studio is not None else None
            if pats:
                from core.java.sandbox_patch import apply_smali_patches
                src2, n = apply_smali_patches(src, pats, file=(self.relpath or None))
                if n:
                    self._log("> %d Smali-Patch(es) in der Sandbox angewandt" % n)
                    return src2
        except Exception as e:
            self._log("> Patch-Anwendung: %r" % e)
        return src

    # ---- Studio-Trias Bruecke & KI-Abgleich & Befund-Bericht -------------
    def open_in_smali_studio(self):
        """Oeffnet die im Debugger geladene Klasse/Methode direkt im Smali Studio."""
        studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
        ctrl = getattr(studio, "controller", None) if studio is not None else None
        rel = (self.relpath or "").replace("\\", "/")
        if not rel and getattr(self, "_loaded_cls", None):
            c = self._loaded_cls
            if c.startswith("L") and c.endswith(";"):
                rel = c[1:-1] + ".smali"
        if ctrl is not None and rel:
            try:
                sig = self.name if self.name else None
                ctrl.load_method(rel, method_signature=sig, add_as_root=False)
                if hasattr(self.app, "notebook") and hasattr(self.app, "workspace_tab"):
                    self.app.notebook.select(self.app.workspace_tab)
                self._log("> Im Smali Studio geoeffnet: %s (%s)" % (rel, sig or "*"))
                return True
            except Exception as e:
                self._log("> Smali Studio oeffnen FEHLER: %r" % e)
        return False

    def save_as_smali_patch(self):
        """Speichert Aenderungen aus dem Debugger-Editor als Live-Smali-Patch in der aktiven Session."""
        rel = (self.relpath or "").replace("\\", "/")
        if not rel and getattr(self, "_loaded_cls", None):
            c = self._loaded_cls
            if c.startswith("L") and c.endswith(";"):
                rel = "smali/" + c[1:-1] + ".smali"
        if not rel:
            self._log("> Als Smali-Patch speichern: relpath unbekannt"); return False
        try:
            from core.application.session_state import SessionState
            SessionState.active_smali_patches.append({
                "type": "smali",
                "file": rel,
                "orig": "",
                "edit": self.source,
                "scope": "file" if ".class" in (self.source or "") else "method",
            })
            if self.app is not None and hasattr(self.app, "persist_active_session"):
                self.app.persist_active_session()
            vm = self.cur_vm()
            prov = getattr(vm, "class_provider", None) if vm is not None else None
            if prov is not None and hasattr(prov, "set_patch") and ".class" in (self.source or ""):
                prov.set_patch(rel, self.source)
            self._log("> Als Smali-Patch gespeichert: %s" % rel)
            return True
        except Exception as e:
            self._log("> Als Smali-Patch speichern FEHLER: %r" % e)
            return False

    def add_native_bp(self, lib_name: str, vaddr: int, name: str = ""):
        """Registriert einen nativen Adress-/Symbol-Breakpoint (aus LibCarve oder Debugger)."""
        bn = os.path.basename(str(lib_name or "").strip())
        va = int(vaddr, 0) if isinstance(vaddr, str) else int(vaddr)
        entry = {"lib": bn, "vaddr": va, "name": name or f"0x{va:x}"}
        if not any(b.get("lib") == bn and int(b.get("vaddr", -1)) == va for b in self.native_bps):
            self.native_bps.append(entry)
        self._log("> Native-BP+ %s@0x%x (%s)" % (bn or "?", va, entry["name"]))
        self._refresh()
        return entry

    def _resolve_active_native_location(self):
        """Ermittelt (lib_name, vaddr) aus aktivem ARM64-PC, Native-BP oder JNI-NativeBridge."""
        f = getattr(self, "_fusion", None) or {}
        eng = f.get("eng")
        if eng is not None and getattr(eng, "cpu", None) is not None:
            pc = int(getattr(eng.cpu, "pc", 0) or 0)
            if pc:
                try:
                    from core.native.linker import get_linker
                    lnk = get_linker(eng)
                    for mod in getattr(lnk, "modules", ()) or ():
                        if mod.base <= pc < mod.end:
                            return (os.path.basename(mod.name), max(0, pc - int(mod.load_bias)))
                except Exception:
                    pass
        if getattr(self, "native_bps", None):
            last = self.native_bps[-1]
            return (last.get("lib", ""), last.get("vaddr"))
        # Fallback: aktuelle Smali-Methode via native_bridge pruefen
        bridge = getattr(self.app, "native_bridge", None)
        if bridge is not None and getattr(self, "_loaded_cls", None) and getattr(self, "name", None):
            try:
                from services.native_bridge import JniTarget, SymbolIndex
                imgs = self.app.get_loaded_native_images() if hasattr(self.app, "get_loaded_native_images") else {}
                if imgs:
                    sig = self._jdb_sig[2] if isinstance(getattr(self, "_jdb_sig", None), tuple) and len(self._jdb_sig) >= 3 else None
                    hits = bridge.resolve(JniTarget(self._loaded_cls, self.name, sig), SymbolIndex(imgs))
                    if hits:
                        return (hits[0].lib, hits[0].addr)
            except Exception:
                pass
        return (None, None)

    def open_in_libcarve(self, prepare_patch: bool = False) -> bool:
        """Oeffnet die aktive native Funktion/Lib (oder LibCarve direkt) im Workspace -> LibCarve."""
        ws = getattr(self.app, "workspace_tab", None)
        lc = getattr(ws, "lib_carve", None) if ws is not None else None
        if lc is None:
            self._log("> LibCarve nicht verfuegbar.")
            return False
        lib_name, vaddr = self._resolve_active_native_location()
        if hasattr(self.app, "focus_libcarve"):
            self.app.focus_libcarve(lib_name, vaddr)
        ctrl = lc.controller
        if lib_name:
            cur_base = os.path.basename(getattr(ctrl, "image_path", "") or "")
            if cur_base != os.path.basename(lib_name) or ctrl.image is None:
                cands = ctrl.discover_libraries()
                hit = next((c for c in cands if c["name"] == os.path.basename(lib_name)), None)
                so_path = hit["path"] if hit else self._selfcache_so_path(lib_name)
                if so_path and os.path.isfile(so_path):
                    def _on_done():
                        if prepare_patch:
                            lc.editor.copy_asm_to_edit()
                    ctrl.load_so_path(so_path, target_vaddr=vaddr, on_ready=_on_done)
                    return True
        if vaddr is not None and ctrl.image is not None:
            ctrl.navigate_to(vaddr, add_to_history=True)
            if prepare_patch:
                lc.editor.copy_asm_to_edit()
        elif prepare_patch and ctrl.image is not None:
            lc.editor.copy_asm_to_edit()
        return True

    def prepare_libcarve_patch(self) -> bool:
        """Oeffnet LibCarve fuer die aktive native Funktion und uebernimmt den ASM direkt in den Editor."""
        return self.open_in_libcarve(prepare_patch=True)

    def generate_befund_report(self):
        """Erzeugt einen kompakten Markdown-Befundbericht nach secrets/probe/out/glass/befund.md."""
        import datetime as _dt
        jdb = self._jdb
        st = jdb.state() if jdb else {"mode": "idle"}
        out_dir = os.path.join(self._base_dir(), "secrets", "probe", "out", "glass")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "befund.md")
        lines = [
            f"# Glass-Debugger Befund ({_dt.datetime.now().isoformat(timespec='seconds')})",
            "",
            f"- **App / Paket:** `{self._resolve_pkg()}`",
            f"- **Zone / Patches:** `{self.zone}` (apply_patches={self.apply_patches})",
            f"- **Instanz:** `{getattr(getattr(self, 'instance_state', None), 'instance_id', 'main')}`",
            f"- **Seam-Profil:** `{getattr(getattr(self, 'seam_config', None), 'unpack_mode', 'self_cache')}`",
            f"- **Status:** `{st.get('mode')}` ({st.get('reason') or '-'}) @ Step `{st.get('step')}`",
            f"- **Position:** `{st.get('cls')}->{st.get('name')}{st.get('sig') or ''} pc={st.get('pc')}`",
            "",
            "## Netz-Aufzeichnung",
            f"- Modus: `{self.net_mode}` / Kondition: `{self.net_condition.to_dict() if self.net_condition else '-'}`",
            f"- Requests: `{len(getattr(self.net_log, 'entries', []) or []) if self.net_log else 0}`",
        ]
        if self.net_log is not None:
            for e in (self.net_log.entries or [])[-20:]:
                r, rp = e["req"], e["resp"]
                lines.append(f"  - `{r.method} {r.url}` -> `{rp.code}` ({len(rp.body)} B)")
        lines += ["", "## Geladene Klassen", f"- Anzahl: `{len(self.loaded_classes())}`"]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self._log("> Befundbericht geschrieben: %s" % path)
        return path

    def dbg_sync(self):
        st = self._jdb.inspect() if self._jdb else {"mode": "idle"}
        self._push_live_state_json(st)
        return {"ok": True, "summary": "glass:%s step=%s" % (st.get("mode"), st.get("step"))}

    def dbg_load_session(self):
        p = os.path.join(self._base_dir(), "secrets", "probe", "out", "glass", "live_state.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("seam_profile"):
                    self.set_seam_profile(data["seam_profile"])
                if data.get("net_condition"):
                    nc = data["net_condition"]
                    self.set_net_condition(nc.get("mode"), nc.get("latency_ms"), nc.get("kbps"))
                self._refresh()
                return {"ok": True, "summary": "Live-State geladen"}
            except Exception as e:
                return {"ok": False, "summary": repr(e)}
        return {"ok": True, "summary": "kein live_state.json"}

    def dbg_status(self):
        st = self._jdb.inspect() if self._jdb else {"mode": "idle"}
        return {"ok": True, "mode": st.get("mode"), "step": st.get("step"),
                "cls": st.get("cls"), "instance": getattr(getattr(self, "instance_state", None), "instance_id", "main")}
