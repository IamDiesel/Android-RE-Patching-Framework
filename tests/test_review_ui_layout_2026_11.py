# -*- coding: utf-8 -*-
"""Tests fuer ViewTree Lazy-Layout (A1), Cycle/Single-Parent (A2), Hit-Testing (A4),
Runtime-Detection (U1), In-Phone Render-Source (U2/U3), EventBus & DisplayTab-Fixes (2026-11)."""
from __future__ import annotations

import os
import sys
import threading
import time
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.application.event_bus import EventBus
from core.java.app_runtime import detect_runtime, mount_inphone_runtime_if_needed
from core.java.dalvik import DalvikException, DalvikVM, MethodDef
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.objects import JObject, JString
from core.java.runtime_detect import detect_from_names, detect_runtime as detect_runtime_family
from core.java.ui_mcp_bridge import build_ui_snapshot_dict
from core.java.view_tree import ViewNode, ViewTree, install_view_stubs, paint_commands
from ui.tabs.display_tab import DisplayTab


# ---------------------------------------------------------------------------
# A1: Lazy touch(), ensure_layout(), Cache, Depth-40 & Manual Bounds
# ---------------------------------------------------------------------------
def test_a1_lazy_touch_400_textviews_and_settext_perf():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)

    root_jo = JObject("Landroid/widget/LinearLayout;")
    stubs.resolve("Landroid/widget/LinearLayout;", "<init>", "(Landroid/content/Context;)V", [root_jo, None], False)

    tv_jos = []
    for i in range(400):
        tv = JObject("Landroid/widget/TextView;")
        stubs.resolve("Landroid/widget/TextView;", "<init>", "(Landroid/content/Context;)V", [tv, None], False)
        stubs.resolve("Landroid/widget/LinearLayout;", "addView", "(Landroid/view/View;)V", [root_jo, tv], False)
        tv_jos.append(tv)

    act = JObject("Lcom/app/TestAct;")
    stubs.resolve("Lcom/app/TestAct;", "setContentView", "(Landroid/view/View;)V", [act, root_jo], False)
    assert tree._layout_dirty is False

    t0 = time.perf_counter()
    for i, tv in enumerate(tv_jos):
        stubs.resolve("Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V", [tv, JString(f"Row {i}")], False)
    assert tree._layout_dirty is True
    cmds = paint_commands(tree)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"400 setText + paint took {elapsed:.3f}s (>= 1.0s)"
    assert tree._layout_dirty is False
    assert any(c.get("op") == "text" and c.get("text") == "Row 399" for c in cmds)

    # Trap #1: Direct bounds read after touch() lazily triggers layout without explicit layout()/paint()
    stubs.resolve("Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V",
                  [tv_jos[0], JString("Line1\nLine2\nLine3\nLine4")], False)
    assert tree._layout_dirty is True
    b = tv_jos[0].fields["$view"].bounds
    assert tree._layout_dirty is False
    assert b[3] > 44


def test_a1_deep_nesting_40_layout_perf_and_manual_bounds_preserved():
    t = ViewTree()
    root = ViewNode("LinearLayout", vid="r0")
    cur = root
    for i in range(1, 40):
        nxt = ViewNode("LinearLayout", vid=f"n{i}")
        cur.add(nxt)
        cur = nxt
    leaf = cur.add(ViewNode("TextView", vid="leaf", text="Deep Leaf"))
    t.set_root(root)

    t.touch()
    t0 = time.perf_counter()
    t.ensure_layout()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.1, f"Depth-40 ensure_layout took {elapsed:.4f}s (>= 0.1s)"
    assert leaf.bounds[2] > 0 and leaf.bounds[3] > 0

    # Trap #2a: direct root assignment + manual bounds preserved
    t_manual = ViewTree()
    r_m = ViewNode("LinearLayout")
    c_m = r_m.add(ViewNode("TextView", text="Manual"))
    t_manual.root = r_m
    r_m.bounds = (0, 0, t_manual.width, 80)
    c_m.bounds = (7, 9, 150, 35)
    paint_commands(t_manual)
    assert c_m.bounds == (7, 9, 150, 35)

    # Trap #2b: set_root() followed by manual node.bounds override preserved
    t_set = ViewTree()
    r_s = ViewNode("LinearLayout")
    c_s = r_s.add(ViewNode("TextView", text="Override"))
    t_set.set_root(r_s)
    c_s.bounds = (0, 0, 200, 44)
    paint_commands(t_set)
    assert c_s.bounds == (0, 0, 200, 44)


def test_a1_get_measured_width_height_vhook():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)

    root_jo = JObject("Landroid/widget/LinearLayout;")
    tv_jo = JObject("Landroid/widget/TextView;")
    stubs.resolve("Landroid/widget/LinearLayout;", "<init>", "(Landroid/content/Context;)V", [root_jo, None], False)
    stubs.resolve("Landroid/widget/TextView;", "<init>", "(Landroid/content/Context;)V", [tv_jo, None], False)
    stubs.resolve("Landroid/widget/LinearLayout;", "addView", "(Landroid/view/View;)V", [root_jo, tv_jo], False)
    stubs.resolve("Landroid/app/Activity;", "setContentView", "(Landroid/view/View;)V", [JObject("Lcom/app/A;"), root_jo], False)

    stubs.resolve("Landroid/widget/TextView;", "setText", "(Ljava/lang/CharSequence;)V",
                  [tv_jo, JString("A\nB\nC\nD")], False)
    assert tree._layout_dirty is True
    mw = vm.invoke_virtual(tv_jo, "getMeasuredWidth", "()I", (tv_jo,))
    mh = vm.invoke_virtual(tv_jo, "getMeasuredHeight", "()I", (tv_jo,))
    assert tree._layout_dirty is False
    assert mw > 0 and mh > 44


# ---------------------------------------------------------------------------
# A2: ViewNode.add single-parent detach & cycle detection
# ---------------------------------------------------------------------------
def test_a2_reparent_detaches_and_cycle_rejected_without_recursion_error():
    p1 = ViewNode("LinearLayout", vid="p1")
    p2 = ViewNode("LinearLayout", vid="p2")
    child = ViewNode("TextView", vid="child", text="hi")

    assert p1.add(None) is None
    p1.add(child)
    assert child in p1.children and child._parent is p1

    p2.add(child)
    assert child not in p1.children
    assert child in p2.children
    assert child._parent is p2

    # Self-cycle and ancestor-cycle rejected cleanly
    with pytest.raises(ValueError, match="Cycle"):
        p2.add(p2)
    with pytest.raises(ValueError, match="Cycle"):
        child.add(p2)

    # _add_view stub maps ValueError -> DalvikException(IllegalStateException)
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs)
    a = JObject("Landroid/widget/LinearLayout;")
    b = JObject("Landroid/widget/LinearLayout;")
    stubs.resolve("Landroid/widget/LinearLayout;", "<init>", "(Landroid/content/Context;)V", [a, None], False)
    stubs.resolve("Landroid/widget/LinearLayout;", "<init>", "(Landroid/content/Context;)V", [b, None], False)
    stubs.resolve("Landroid/widget/LinearLayout;", "addView", "(Landroid/view/View;)V", [a, b], False)
    with pytest.raises(DalvikException, match="IllegalStateException"):
        stubs.resolve("Landroid/widget/LinearLayout;", "addView", "(Landroid/view/View;)V", [b, a], False)


# ---------------------------------------------------------------------------
# A4: _hit_clickable_in sibling fallback guard
# ---------------------------------------------------------------------------
def test_a4_sibling_textview_does_not_activate_delete_button_and_pref_row_works():
    for container_cls in ("LinearLayout", "RelativeLayout"):
        row = ViewNode(container_cls, vid="row")
        lbl = row.add(ViewNode("TextView", vid="lbl", text="Account settings", clickable=False))
        btn = row.add(ViewNode("Button", vid="del", text="Konto löschen", clickable=True))
        t = ViewTree()
        t.set_root(row)

        lx, ly, lw, lh = lbl.bounds
        assert t.clickable_at(lx + lw // 2, ly + lh // 2) is None
        bx, by, bw, bh = btn.bounds
        assert t.clickable_at(bx + bw // 2, by + bh // 2) is btn

    # Switch sibling inside preference row still works
    pref = ViewNode("LinearLayout", vid="pref")
    title = pref.add(ViewNode("TextView", vid="title", text="Notifications", clickable=False))
    sw = pref.add(ViewNode("Switch", vid="sw", clickable=True))
    t2 = ViewTree()
    t2.set_root(pref)
    tx, ty, tw, th = title.bounds
    assert t2.clickable_at(tx + tw // 2, ty + th // 2) is sw

    # click_overlay sibling inside FrameLayout still works
    card_wrap = ViewNode("FrameLayout", vid="wrap")
    card_tv = card_wrap.add(ViewNode("TextView", vid="card_tv", text="Tap card", clickable=False))
    overlay = card_wrap.add(ViewNode("View", vid="click_overlay", clickable=True))
    t3 = ViewTree()
    t3.set_root(card_wrap)
    cx, cy, cw, ch = card_tv.bounds
    assert t3.clickable_at(cx + cw // 2, cy + ch // 2) is overlay


# ---------------------------------------------------------------------------
# U1 / U2 / U3: Runtime detection & mount_inphone_runtime_if_needed & snapshot
# ---------------------------------------------------------------------------
def test_u1_runtime_detect_strict_markers_and_priority(tmp_path):
    assert detect_from_names(["flutter_assets_readme.txt"]) == "classic"
    assert detect_from_names(["my_libflutter.so.bak", "app_flutter_helper.so"]) == "classic"
    assert detect_from_names(["libreactnative_test.so", "readme_cordova.js.txt"]) == "classic"
    assert detect_from_names(["assets/flutter_assets/kernel_blob.bin"]) == "flutter"
    assert detect_from_names(["flutter_assets/AssetManifest.json"]) == "flutter"
    assert detect_from_names(["smali/androidx/compose/ui/Foo.smali"]) == "jetpack compose"
    assert detect_from_names(["smali/com/app/ComposableSingletons$Main.smali"]) == "jetpack compose"

    # Priority consistency: Flutter beats RN in both runtime_detect and app_runtime
    (tmp_path / "lib" / "arm64-v8a").mkdir(parents=True)
    (tmp_path / "lib" / "arm64-v8a" / "libflutter.so").write_bytes(b"ELF")
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "index.android.bundle").write_text("// js", encoding="utf-8")
    assert detect_runtime_family(app_dir=str(tmp_path)) == "flutter"
    assert detect_runtime(str(tmp_path))[0] == "Flutter"


def test_u2_u3_mount_inphone_runtime_and_snapshot_render_source(tmp_path):
    (tmp_path / "lib" / "arm64-v8a").mkdir(parents=True)
    (tmp_path / "lib" / "arm64-v8a" / "libflutter.so").write_bytes(b"ELF")
    (tmp_path / "assets" / "flutter_assets").mkdir(parents=True)

    shell_root = ViewNode("FlutterView")
    shell_root.add(ViewNode("FlutterSurfaceView"))
    t_rec = ViewTree()
    t_rec.set_root(shell_root)

    called = []
    cb = lambda: called.append(True)
    mounted, lbl = mount_inphone_runtime_if_needed(t_rec, str(tmp_path), on_async_update=cb)
    assert mounted is True
    assert lbl == "Flutter"
    assert t_rec.render_source == "reconstructed"
    assert getattr(t_rec._flutter_ctrl, "on_async_update", None) is cb
    assert getattr(t_rec._flutter_ctrl.browser, "on_async_update", None) is cb

    snap_rec = build_ui_snapshot_dict(tree=t_rec)
    assert snap_rec["render_source"] == "reconstructed"

    t_exec = ViewTree()
    r_exec = ViewNode("LinearLayout")
    r_exec.add(ViewNode("Button", vid="ok", text="OK", clickable=True))
    t_exec.set_root(r_exec)
    snap_exec = build_ui_snapshot_dict(tree=t_exec)
    assert snap_exec["render_source"] == "executed"


# ---------------------------------------------------------------------------
# EventBus thread-safety & DisplayTab cache, <Destroy> cleanup, _invoke_listener
# ---------------------------------------------------------------------------
def test_eventbus_concurrent_unsubscribe_inside_publish():
    EventBus.clear()
    try:
        seen = []

        def cb1(d):
            seen.append(("cb1", d))
            EventBus.unsubscribe("EV_TEST", cb1)
            EventBus.unsubscribe("EV_TEST", cb2)

        def cb2(d):
            seen.append(("cb2", d))

        def cb3(d):
            seen.append(("cb3", d))

        EventBus.subscribe("EV_TEST", cb1)
        EventBus.subscribe("EV_TEST", cb2)
        EventBus.subscribe("EV_TEST", cb3)

        EventBus.publish("EV_TEST", 1)
        assert ("cb1", 1) in seen and ("cb3", 1) in seen

        seen.clear()
        EventBus.publish("EV_TEST", 2)
        assert seen == [("cb3", 2)]
    finally:
        EventBus.clear()


def test_displaytab_destroy_unsubscribes_and_render_caches_paint(monkeypatch):
    import tkinter as tk
    try:
        tk_root = tk.Tk()
        tk_root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Tkinter display unavailable: {e}")

    EventBus.clear()
    try:
        app = types.SimpleNamespace(after=tk_root.after, log=lambda _m: None)
        tab = DisplayTab(tk_root, app)

        for ev, cb in tab._event_subs:
            assert cb in EventBus._subscribers.get(ev, [])
        assert len(tab._event_subs) == 6

        # Spy on paint_commands inside display_tab module
        import ui.tabs.display_tab as dt_mod
        calls = []
        orig_pc = dt_mod.paint_commands

        def _counting_pc(t):
            calls.append(getattr(t, "version", 0))
            return orig_pc(t)

        monkeypatch.setattr(dt_mod, "paint_commands", _counting_pc)

        t = ViewTree()
        r = ViewNode("LinearLayout")
        for i in range(40):
            r.add(ViewNode("TextView", vid=f"item_{i}", text=f"Item {i}"))
        t.set_root(r)
        t.render_source = "reconstructed"
        tab.set_tree(t, vm="glass")
        assert len(calls) == 1
        assert "[Rekonstruiert]" in str(tab.status.cget("text"))

        # Wheel scroll + hover highlight must reuse cached paint_commands
        tab._on_wheel(types.SimpleNamespace(num=5, delta=-120))
        t._highlight_node = r.children[0]
        tab.render()
        assert len(calls) == 1

        # Destroying tab unsubscribes all 6 EventBus listeners cleanly
        tab.destroy()
        tk_root.update_idletasks()
        for ev, cb in (
            ("GLASS_STARTED", tab._on_glass_event),
            ("GLASS_STATE", tab._on_glass_event),
            ("GLASS_SURFACE", tab._on_surface_event),
            ("DEBUG_PAUSED", tab._on_debug_paused),
            ("WEBVIEW_EVENT", tab._on_webview_event),
            ("APP_IMPORTED", tab._on_app_imported),
        ):
            assert cb not in EventBus._subscribers.get(ev, [])
    finally:
        EventBus.clear()
        try:
            tk_root.destroy()
        except Exception:
            pass


def test_displaytab_invoke_listener_reuses_jobj_identity_and_flushes():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    vm.runtime = _StubRuntime(stubs)

    btn_node = ViewNode("Button", vid="btn", text="Go", clickable=True)
    root = ViewNode("LinearLayout")
    root.add(btn_node)
    tree.set_root(root)

    btn_jobj = JObject("Landroid/widget/Button;", {"$view": btn_node})
    btn_node._jobj = btn_jobj

    seen_views = []
    listener = JObject("Lcom/app/MyListener;")
    vm.register(MethodDef("Lcom/app/MyListener;", "onClick", "(Landroid/view/View;)V", [("return-void",)], nregs=2, nargs=2))

    def _hook(rcls, cls, name, sig, args, static, _vm):
        if cls == "Lcom/app/MyListener;" and name == "onClick":
            seen_views.append(args[1])
            return True, None
        return False, None

    vm._method_hooks.append(_hook)

    flushed = []
    stubs._flush_fragment_transactions = lambda: flushed.append(True) or True

    done_ev = threading.Event()
    fake_tab = types.SimpleNamespace(
        _busy=False,
        vm=vm,
        tree=tree,
        app=types.SimpleNamespace(after=lambda _ms, _fn: done_ev.set()),
        _log=lambda _m: None,
        render=lambda: None,
    )
    v_before = tree.version
    DisplayTab._invoke_listener(fake_tab, listener, btn_node)
    assert done_ev.wait(timeout=2.0)
    assert seen_views == [btn_jobj]
    assert seen_views[0] is btn_jobj
    assert flushed == [True]
    assert tree.version > v_before

    # When _jobj is initially absent, newly created JObject is cached on node._jobj
    btn_node._jobj = None
    done_ev.clear()
    fake_tab._busy = False
    DisplayTab._invoke_listener(fake_tab, listener, btn_node)
    assert done_ev.wait(timeout=2.0)
    assert isinstance(btn_node._jobj, JObject)
    assert seen_views[-1] is btn_node._jobj

    done_ev.clear()
    fake_tab._busy = False
    DisplayTab._invoke_listener(fake_tab, listener, btn_node)
    assert done_ev.wait(timeout=2.0)
    assert seen_views[-1] is btn_node._jobj


def test_tree_by_id_lookup():
    stubs = AndroidStubs()
    tree = install_view_stubs(stubs)
    root = ViewNode("LinearLayout")
    vp = root.add(ViewNode("ViewPager2", vid="pager_slot"))
    tree.set_root(root)
    tree._idmap_id_ref[0x7F0A0042] = "pager_slot"

    assert tree.by_id(0x7F0A0042) is vp
    assert tree.by_id("pager_slot") is vp
    assert tree.by_id(0x7FFFFFFF) is None


# ---------------------------------------------------------------------------
# Regression 2026-11: Native Taskbar Chrome, DockManager Tab-Klick & Budget-Persistenz
# ---------------------------------------------------------------------------
def test_dock_manager_tab_click_and_panel_realize_preserve_top_zone(tmp_path):
    import tkinter as tk
    from tkinter import ttk
    from ui.dock.dock_manager import DockManager

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Tkinter display unavailable: {e}")

    try:
        counts = {"transport": 0, "net": 0, "console": 0}
        transport_widgets = []

        def _mk_transport(parent):
            counts["transport"] += 1
            f = ttk.Frame(parent)
            transport_widgets.append(f)
            return f

        def _mk_net(parent):
            counts["net"] += 1
            return ttk.Frame(parent)

        def _mk_console(parent):
            counts["console"] += 1
            return ttk.Frame(parent)

        dm = DockManager(root, layout_path=str(tmp_path / "dock_test.json"))
        dm.register_view("transport", "Transport", _mk_transport, "top", "S")
        dm.register_view("net", "Netz", _mk_net, "panel", "S")
        dm.register_view("console", "Main Console", _mk_console, "panel", "S")
        dm.build()
        root.update_idletasks()

        assert counts == {"transport": 1, "net": 1, "console": 1}
        orig_transport = transport_widgets[0]
        assert orig_transport.winfo_exists()

        # Release auf derselben Notebook-Tableiste (selbst nach >18px Mausbewegung) darf NICHT neu realisieren
        panel_nb = dm._group_nb[("panel", 0)]
        tabs = panel_nb.tabs()
        assert len(tabs) == 2
        dm._drag = {
            "vid": "net",
            "src": panel_nb,
            "idx": 0,
            "x": 100,
            "y": 100,
            "active": True,
        }
        orig_find_nb = dm._find_nb
        orig_idx = panel_nb.index
        try:
            dm._find_nb = lambda _w: panel_nb
            panel_nb.index = lambda spec: 1 if str(spec).startswith("@") else orig_idx(spec)
            dm._dnd_release(types.SimpleNamespace(x_root=130, y_root=104))
        finally:
            dm._find_nb = orig_find_nb
            panel_nb.index = orig_idx

        assert counts == {"transport": 1, "net": 1, "console": 1}
        assert orig_transport.winfo_exists()

        # Ein- und Ausblenden eines Panel-Views darf die Top-Zone ('transport') NICHT zerstoeren
        dm.toggle_view("console")
        root.update_idletasks()
        assert counts["transport"] == 1
        assert orig_transport.winfo_exists()
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_debugger_budget_persists_syncs_and_live_state_push_runs_off_main_thread(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from core.java.debug import JavaDebugger
    from ui.controllers.debugger_controller import DebuggerController
    from ui.dock.layout_model import LayoutModel
    from ui.tabs.debugger_tab import DebuggerTab

    # 1) build_ui_snapshot_dict darf bestehendes GUI-Layout (380x800) nicht auf 420x684 ueberschreiben
    laid_tree = ViewTree(width=380, height=800)
    r = ViewNode("FrameLayout")
    r.add(ViewNode("Button", vid="b1", text="Tap", clickable=True))
    laid_tree.set_root(r)
    laid_tree.ensure_layout()
    orig_bounds = laid_tree.root.bounds
    snap = build_ui_snapshot_dict(tree=laid_tree)
    assert laid_tree.width == 380 and laid_tree.height == 800
    assert laid_tree.root.bounds == orig_bounds
    assert snap["width"] == 380

    # 2) _schedule_live_state_push laeuft im Hintergrund-Thread statt auf dem Main-Thread
    ctrl = DebuggerController(app=types.SimpleNamespace(after=lambda _ms, fn: fn(), log=lambda _m: None))
    seen_threads = []
    done_ev = threading.Event()

    def _fake_push(st):
        seen_threads.append((threading.current_thread() is threading.main_thread(), st.get("step")))
        done_ev.set()

    ctrl._push_live_state_json = _fake_push
    ctrl._schedule_live_state_push({"mode": "done", "step": 42})
    assert done_ev.wait(timeout=2.0)
    assert seen_threads == [(False, 42)]

    # 3) DebuggerTab behaelt Budget ('∞' / '10M') und synchronisiert sofort mit JavaDebugger
    monkeypatch.setattr(LayoutModel, "load_json", lambda self, _p: False)
    monkeypatch.setattr(LayoutModel, "save_json", lambda self, _p: None)
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Tkinter display unavailable: {e}")

    try:
        app_stub = types.SimpleNamespace(
            after=root.after,
            log=lambda _m: None,
            register_log_sink=lambda _fn: None,
            unregister_log_sink=lambda _fn: None,
            cfg=types.SimpleNamespace(config={"BASE_DIR": str(tmp_path)}),
        )
        tab = DebuggerTab(root, app_stub)
        ctrl2 = tab.ctrl
        vm = DalvikVM()
        ctrl2._jdb = JavaDebugger(vm, max_steps=2_000_000)
        ctrl2._jdb.set_budget(2_000_000, pause_on_budget=True)
        root.update_idletasks()

        assert tab.v_step_budget.get() == "2M"
        tab.v_step_budget.set("\u221e")
        tab._methode_var.set("onClick")
        tab._zone_var.set("live")
        root.update_idletasks()

        assert ctrl2._parse_budget_steps() == 500_000_000
        assert ctrl2._jdb._budget_chunk == 500_000_000
        assert ctrl2._jdb._budget_limit >= 500_000_000

        # Selbst ein erneuter Aufruf von _v_transport / _v_quelle darf die Felder NICHT zuruecksetzen
        tmp_parent = ttk.Frame(root)
        tab._v_transport(tmp_parent)
        tab._v_quelle(tmp_parent)
        assert tab.v_step_budget.get() == "\u221e"
        assert tab._methode_var.get() == "onClick"
        assert tab._zone_var.get() == "live"
    finally:
        EventBus.clear()
        try:
            root.destroy()
        except Exception:
            pass

