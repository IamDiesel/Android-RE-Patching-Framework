# -*- coding: utf-8 -*-
"""Tests fuer App-Wechsel-Synchronisierung (Smali Studio RAM-Index + alle Tabs)."""
import os
import time
import types

import pytest

from core.application.event_bus import EventBus
from gui import ReFrameworkApp
from services.smali_search_service import SmaliSearchEngine
from ui.controllers.debugger_controller import DEFAULT_SMALI, DebuggerController
from ui.controllers.native_studio_controller import NativeStudioController
from ui.tabs.app_manager_tab import AppManagerTab
from ui.tabs.device_file_manager_tab import DeviceFileManagerTab
from ui.tabs.display_tab import DisplayTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.smali_studio_tab import SmaliStudioTab


def _mk_smali_app(root, pkg, rel_cls_path, body):
    app_src = root / "source" / pkg
    unpacked = app_src / "base_unpacked_apktool"
    target = unpacked / rel_cls_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    dest = root / "destination" / pkg
    dest.mkdir(parents=True, exist_ok=True)
    return app_src, unpacked, dest


def _wait_indexed(engine, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.is_indexed and not engine.is_indexing:
            return True
        time.sleep(0.02)
    return False


def _make_fake_cfg(root, pkg):
    cfg = types.SimpleNamespace(
        config={
            "BASE_DIR": str(root),
            "APP_PACKAGE": pkg,
            "SPLIT_NAME": "",
            "MANIFEST_STRATEGY": "smali_only",
        },
        paths={},
    )

    def _update_paths():
        p = cfg.config["APP_PACKAGE"]
        app_src = os.path.join(str(root), "source", p)
        dest = os.path.join(str(root), "destination", p)
        os.makedirs(app_src, exist_ok=True)
        os.makedirs(dest, exist_ok=True)
        cfg.paths = {
            "SOURCE_DIR": os.path.join(str(root), "source"),
            "APP_SOURCE_DIR": app_src,
            "DEST_DIR": dest,
            "ARCHIVE_DIR": os.path.join(str(root), "archives"),
        }

    cfg._update_paths = _update_paths
    cfg.save = lambda: _update_paths()
    _update_paths()
    return cfg


class _FakeTree:
    def __init__(self):
        self.items = ["item1"]

    def get_children(self):
        return list(self.items)

    def delete(self, item):
        if item in self.items:
            self.items.remove(item)


class _FakeEditor:
    def __init__(self):
        self.orig = "old code"
        self.edit = "old edit"

    def load_code(self, txt):
        self.orig = txt

    def clear_edit(self):
        self.edit = ""


class _FakeVar:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class _FakeText:
    def __init__(self, txt=""):
        self.txt = txt

    def delete(self, *_a):
        self.txt = ""

    def insert(self, _idx, s):
        self.txt += s


class _FakeEntry:
    def __init__(self, txt=""):
        self.txt = txt

    def get(self):
        return self.txt

    def delete(self, *_a):
        self.txt = ""

    def insert(self, _idx, s):
        self.txt += s


class _FakeCombo:
    def __init__(self, values=()):
        self._vals = list(values)
        self._cur = ""

    def cget(self, key):
        return list(self._vals) if key == "values" else ""

    def __setitem__(self, key, val):
        if key == "values":
            self._vals = list(val)

    def set(self, val):
        self._cur = val


def test_search_engine_tracks_and_resets_package(tmp_path):
    _, unp_a, dest_a = _mk_smali_app(
        tmp_path, "com.app.alpha", "smali/com/app/alpha/Alpha.smali", ".class public Lcom/app/alpha/Alpha;\n"
    )
    _, unp_b, dest_b = _mk_smali_app(
        tmp_path, "com.app.beta", "smali/com/app/beta/Beta.smali", ".class public Lcom/app/beta/Beta;\n"
    )

    eng = SmaliSearchEngine()
    eng.build_ram_index(str(unp_a), str(dest_a / "base_unpacked_apktool"), "com.app.alpha", None)
    assert _wait_indexed(eng)
    assert eng.indexed_pkg == "com.app.alpha"
    assert eng.indexed_dir == os.path.abspath(str(unp_a))
    assert any("Alpha.smali" in p for p, _ in eng.ram_cache)

    eng.reset()
    assert eng.is_indexed is False
    assert eng.indexed_pkg is None
    assert eng.indexed_dir is None
    assert eng.ram_cache == []

    eng.build_ram_index(str(unp_b), str(dest_b / "base_unpacked_apktool"), "com.app.beta", None)
    assert _wait_indexed(eng)
    assert eng.indexed_pkg == "com.app.beta"
    assert eng.indexed_dir == os.path.abspath(str(unp_b))
    assert any("Beta.smali" in p for p, _ in eng.ram_cache)
    assert not any("Alpha.smali" in p for p, _ in eng.ram_cache)


def test_full_app_switch_sync_across_studio_debugger_display_native_and_files(tmp_path, monkeypatch):
    _mk_smali_app(
        tmp_path, "com.app.alpha", "smali/com/app/alpha/Alpha.smali", ".class public Lcom/app/alpha/Alpha;\n"
    )
    _, unp_b, _ = _mk_smali_app(
        tmp_path, "com.app.beta", "smali/com/app/beta/Beta.smali", ".class public Lcom/app/beta/Beta;\n"
    )

    cfg = _make_fake_cfg(tmp_path, "com.app.alpha")

    class _DummyCg:
        def __init__(self):
            self.cleared = False

        def clear(self):
            self.cleared = True

    app = types.SimpleNamespace(
        cfg=cfg,
        cg=_DummyCg(),
        logs=[],
        current_id="",
        current_archive_path="",
    )
    app.log = lambda msg: app.logs.append(msg)
    app.after = lambda _ms, fn=None: fn() if callable(fn) else None
    app.generate_new_id = lambda: setattr(app, "current_id", "PID-TEST")
    app._update_context_bar = lambda: setattr(app, "ctx_updated", True)
    app.settings_tab = types.SimpleNamespace(
        populated=0,
        populate_settings=lambda: setattr(app.settings_tab, "populated", app.settings_tab.populated + 1),
    )
    app.workspace_tab = types.SimpleNamespace(
        meta_loaded=0,
        load_current_workspace_meta=lambda: setattr(app.workspace_tab, "meta_loaded", app.workspace_tab.meta_loaded + 1),
    )

    # Studio stub backed by real SmaliStudioTab methods
    eng = SmaliSearchEngine()
    dest_a = os.path.join(cfg.paths["DEST_DIR"], "base_unpacked_apktool")
    unp_a = os.path.join(cfg.paths["APP_SOURCE_DIR"], "base_unpacked_apktool")
    eng.build_ram_index(unp_a, dest_a, "com.app.alpha", None)
    assert _wait_indexed(eng)

    studio_ctrl = types.SimpleNamespace(
        search_window=None,
        _ram_dict={"Alpha.smali": "old"},
        current_smali_file="Alpha.smali",
        current_method_name="foo()V",
        editing_patch_idx=0,
        history_nodes={"h0": {}},
        history_root_id="h0",
        current_history_id="h0",
        history_counter=1,
    )
    studio = types.SimpleNamespace(
        app=app,
        search_engine=eng,
        controller=studio_ctrl,
        editor=_FakeEditor(),
        lbl_smali_file=types.SimpleNamespace(config=lambda **_kw: None),
        lbl_progress_status=types.SimpleNamespace(config=lambda **_kw: None),
        struct_manager=types.SimpleNamespace(smali_dir=unp_a, custom_files=["Custom.smali"]),
        tree_outline=_FakeTree(),
        tree_file=_FakeTree(),
        tree_incoming=_FakeTree(),
        tree_outgoing=_FakeTree(),
        tree_datagraph=_FakeTree(),
        tree_history=_FakeTree(),
        tree_callstack=_FakeTree(),
        tree_custom_structs=_FakeTree(),
        winfo_toplevel=lambda: None,
    )
    studio.get_unpacked_dir_name = lambda: SmaliStudioTab.get_unpacked_dir_name(studio)
    studio.get_smali_dir = lambda: SmaliStudioTab.get_smali_dir(studio)
    studio.update_status = lambda msg: SmaliStudioTab.update_status(studio, msg)
    studio.on_app_changed = lambda pkg=None: SmaliStudioTab.on_app_changed(studio, pkg)
    studio._ensure_index_loaded = lambda parent_widget=None: SmaliStudioTab._ensure_index_loaded(studio, parent_widget)
    app.workspace_tab.smali_studio = studio

    # DebuggerController & View
    dbg = DebuggerController(app)
    dbg.source = ".method public oldMethod()V\n return-void\n.end method\n"
    dbg.zone_root = "/old/root"
    dbg.relpath = "Old.smali"
    dbg.app_entry = "Lcom/app/alpha/Main;"
    dbg.name = "oldMethod"
    dbg.bps = [1, 2]
    dbg.app_bps = {("Lold;", "m", "()V", 1): {}}
    dbg.glass_tree = object()
    dbg_view = types.SimpleNamespace(
        _zone_root_var=_FakeVar("/old/root"),
        _relpath_var=_FakeVar("Old.smali"),
        _app_entry_var=_FakeVar("Lcom/app/alpha/Main;"),
        _methode_var=_FakeVar("oldMethod"),
        rebuilt=False,
        rebuild_code_model=lambda: setattr(dbg_view, "rebuilt", True),
    )
    ed_widget = _FakeText("old")
    dbg.view = dbg_view
    dbg.bind("editor", ed_widget)
    app.debugger_tab = types.SimpleNamespace(ctrl=dbg)

    # DisplayTab stub bound to DisplayTab.on_app_changed
    disp = types.SimpleNamespace(
        tree=object(),
        _dl=object(),
        vm="glass",
        _hermes_client=object(),
        _activity=object(),
        _nav=object(),
        _focus=object(),
        _dl_focus_xy=(10, 10),
        _dl_text="abc",
        _glass_mode="done",
        scroll_y=42,
        _entry_var=_FakeVar("Lcom/app/alpha/Main;"),
        rendered=False,
    )
    disp._alive = lambda: True
    disp.render = lambda: setattr(disp, "rendered", True)
    disp.on_app_changed = lambda d=None: DisplayTab.on_app_changed(disp, d)
    app.display_tab = disp

    # NativeStudioController
    nat_tab = types.SimpleNamespace(cleared=False, clear=lambda: setattr(nat_tab, "cleared", True))
    nat_ctrl = types.SimpleNamespace(
        tab=nat_tab,
        app=app,
        image=object(),
        image_path="/old/libfoo.so",
        index=object(),
        _short2addr={"foo": 0x1000},
        _last_engine=object(),
        _snapshot_path="/old/snap.pkl.xz",
        _outline_base_rows=[("fn", "foo", "0x1000")],
    )
    nat_ctrl.on_app_changed = lambda pkg=None: NativeStudioController.on_app_changed(nat_ctrl, pkg)
    app.native_studio = types.SimpleNamespace(controller=nat_ctrl)

    # DeviceFileManagerTab
    dfm = types.SimpleNamespace(
        cb_pkg=_FakeCombo(["com.app.alpha"]),
        ent_path=_FakeEntry("/data/data/com.app.alpha/"),
        controller=types.SimpleNamespace(current_path="/data/data/com.app.alpha/"),
    )
    dfm.on_app_changed = lambda pkg=None: DeviceFileManagerTab.on_app_changed(dfm, pkg)
    app.device_file_tab = dfm

    app.on_app_switched = lambda pkg: ReFrameworkApp.on_app_switched(app, pkg)

    received_ev = []
    EventBus.subscribe("APP_IMPORTED", lambda d: received_ev.append(d))

    # Execute app import for com.app.beta
    ReFrameworkApp.handle_app_imported(app, {
        "package_name": "com.app.beta",
        "architecture": "LOCAL",
        "local_split_name": "com.app.beta",
    })

    # Verify new RAM index builds and contains Beta (not Alpha)
    assert _wait_indexed(eng)
    assert eng.indexed_pkg == "com.app.beta"
    assert eng.indexed_dir == os.path.abspath(str(unp_b))
    assert any("Beta.smali" in p for p, _ in eng.ram_cache)
    assert not any("Alpha.smali" in p for p, _ in eng.ram_cache)
    assert studio._ensure_index_loaded() is True

    # Verify SmaliStudio state cleared
    assert studio_ctrl.current_smali_file == ""
    assert studio_ctrl.history_nodes == {}
    assert studio.editor.orig == ""
    assert studio.tree_outline.get_children() == []
    assert app.cg.cleared is True

    # Verify Debugger, Display, NativeStudio, DeviceFileManager, Settings, Workspace synced
    assert dbg._active_pkg == "com.app.beta"
    assert dbg.instance_state.package == "com.app.beta"
    assert dbg.source == DEFAULT_SMALI
    assert dbg.relpath == "" and dbg.bps == [] and dbg.app_bps == {} and dbg.glass_tree is None
    assert dbg_view._relpath_var.get() == "" and dbg_view.rebuilt is True

    assert disp.tree is None and disp._dl is None and disp._entry_var.get() == "" and disp.rendered is True
    assert nat_ctrl.image is None and nat_ctrl.image_path is None and nat_tab.cleared is True
    assert dfm.cb_pkg._cur == "com.app.beta" and dfm.ent_path.txt == "/data/data/com.app.beta/"
    assert app.settings_tab.populated == 1 and app.workspace_tab.meta_loaded == 1
    assert any(d == {"package": "com.app.beta"} for d in received_ev)

    # Verify _ensure_index_loaded detects mismatch if config switches silently without callback
    monkeypatch.setattr("ui.tabs.smali_studio_tab.messagebox.showinfo", lambda *a, **kw: None)
    cfg.config["APP_PACKAGE"] = "com.app.alpha"
    cfg._update_paths()
    assert studio._ensure_index_loaded() is False
    assert _wait_indexed(eng)
    assert eng.indexed_pkg == "com.app.alpha"


def test_import_local_apk_accepts_unpacked_dir(tmp_path, monkeypatch):
    app_dir = tmp_path / "source" / "com.demo.unpacked"
    unp_dir = app_dir / "base_unpacked_apktool"
    unp_dir.mkdir(parents=True)

    imported = []
    tab = types.SimpleNamespace(
        source_dir=str(tmp_path / "source"),
        on_app_imported=lambda data: imported.append(data),
        lbl_status=types.SimpleNamespace(config=lambda **_kw: None),
        stat_workspace=types.SimpleNamespace(config=lambda **_kw: None),
        stat_arch=types.SimpleNamespace(config=lambda **_kw: None),
        stat_logcat=types.SimpleNamespace(config=lambda **_kw: None),
        stat_api=types.SimpleNamespace(config=lambda **_kw: None),
        log=lambda _msg: None,
        update_idletasks=lambda: None,
    )

    monkeypatch.setattr("ui.tabs.app_manager_tab.filedialog.askdirectory", lambda **_kw: str(unp_dir))
    monkeypatch.setattr("ui.tabs.app_manager_tab.messagebox.showerror", lambda *a, **kw: pytest.fail(f"Unexpected error: {a}"))

    AppManagerTab.import_local_apk(tab)
    assert len(imported) == 1
    assert imported[0]["package_name"] == "com.demo.unpacked"


def test_settings_tab_triggers_on_app_switched_on_package_change(tmp_path, monkeypatch):
    cfg = _make_fake_cfg(tmp_path, "com.app.one")
    switched = []
    app = types.SimpleNamespace(
        cfg=cfg,
        on_app_switched=lambda pkg: switched.append(pkg),
    )
    tab = types.SimpleNamespace(
        app=app,
        entries={"APP_PACKAGE": _FakeEntry("com.app.two")},
        txt_pipes=types.SimpleNamespace(get=lambda *_a: "{}"),
    )
    tab._sync_config_from_ui = lambda: SettingsTab._sync_config_from_ui(tab)
    monkeypatch.setattr("ui.tabs.settings_tab.messagebox.showinfo", lambda *a, **kw: None)

    SettingsTab.save_settings(tab)
    assert switched == ["com.app.two"]


def test_startup_threads_guard_against_main_thread_not_in_main_loop(tmp_path, monkeypatch):
    import threading
    import tkinter as tk

    # 1) AppManagerTab.__init__ darf load_packages nicht vor mainloop() synchron starten,
    #    sondern muss per after(0, self.load_packages) aufs Mainloop verschieben.
    scheduled = []
    loaded_sync = []
    monkeypatch.setattr("ui.tabs.app_manager_tab.ttk.Frame.__init__", lambda self, _p: None)
    monkeypatch.setattr("ui.tabs.app_manager_tab.AppManagerTab.create_widgets", lambda self: None)
    monkeypatch.setattr("ui.tabs.app_manager_tab.AppManagerTab._load_devices", lambda self: None)
    monkeypatch.setattr("ui.tabs.app_manager_tab.AppManagerTab.load_packages", lambda self: loaded_sync.append(True))
    monkeypatch.setattr("ui.tabs.app_manager_tab.AppManagerTab.after", lambda self, ms, fn=None: scheduled.append((ms, fn)))

    tab = AppManagerTab(None, str(tmp_path / "src"), None)
    assert loaded_sync == []
    assert len(scheduled) == 1 and scheduled[0][0] == 0
    scheduled[0][1]()
    assert loaded_sync == [True]

    # 2) Worker-Thread in load_packages() darf bei RuntimeError("main thread is not in main loop")
    #    weder beim Fehler-Callback noch im except-Fallback eine unbehandelte Thread-Exception werfen.
    monkeypatch.undo()
    thread_errors = []
    old_excepthook = threading.excepthook
    threading.excepthook = lambda args: thread_errors.append(args.exc_value)
    try:
        done_ev = threading.Event()

        def _fake_adb(_cmd, _cwd):
            try:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="offline")
            finally:
                done_ev.set()

        monkeypatch.setattr("ui.tabs.app_manager_tab.CommandRunner.run_blocking", staticmethod(_fake_adb))

        def _raise_main_loop(_ms, _fn=None):
            raise RuntimeError("main thread is not in main loop")

        fake_tab = types.SimpleNamespace(
            source_dir=str(tmp_path),
            listbox=types.SimpleNamespace(delete=lambda *a: None, insert=lambda *a: None),
            packages=[],
            winfo_exists=lambda: True,
            update_idletasks=lambda: None,
            after=_raise_main_loop,
            _handle_adb_error=lambda _e: None,
            _update_list=lambda _p: None,
        )
        fake_tab._safe_after = lambda fn: AppManagerTab._safe_after(fake_tab, fn)
        AppManagerTab.load_packages(fake_tab)
        assert done_ev.wait(timeout=3.0)
        time.sleep(0.05)
        assert thread_errors == []

        # Auch bei zerstörtem Widget (winfo_exists() == False) darf _safe_after Callback nicht ausführen
        ran = []
        fake_tab_dead = types.SimpleNamespace(
            winfo_exists=lambda: False,
            after=lambda _ms, fn: fn(),
        )
        AppManagerTab._safe_after(fake_tab_dead, lambda: ran.append(1))
        assert ran == []

        # TclError in after() wird ebenfalls sauber abgefangen
        fake_tab_tcl = types.SimpleNamespace(
            winfo_exists=lambda: True,
            after=lambda _ms, _fn: (_ for _ in ()).throw(tk.TclError("application has been destroyed")),
        )
        AppManagerTab._safe_after(fake_tab_tcl, lambda: ran.append(2))
        assert ran == []
    finally:
        threading.excepthook = old_excepthook

    # 3) ReFrameworkApp.log() puffert Log-Zeilen sicher weiter, falls after() vor mainloop() scheitert
    fake_app = types.SimpleNamespace(
        _log_buf=[],
        _log_sinks=[],
        _LOG_CAP=3,
        after=lambda _ms, _fn: (_ for _ in ()).throw(RuntimeError("main thread is not in main loop")),
    )
    for msg in ("line1", "line2", "line3", "line4"):
        ReFrameworkApp.log(fake_app, msg)
    assert fake_app._log_buf == ["line2\n", "line3\n", "line4\n"]
