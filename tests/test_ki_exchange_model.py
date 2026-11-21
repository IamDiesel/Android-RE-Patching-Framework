# -*- coding: utf-8 -*-
from core.application.ki_exchange_model import KiExchangeModel

def test_register_and_select_all():
    m = KiExchangeModel()
    m.register("a", "A", push=lambda: {"ok": True, "msg": "pa"})
    m.register("b", "B")  # no-op provider (kein teilbarer Zustand)
    assert [p["key"] for p in m.providers()] == ["a", "b"]
    assert m.is_all() and m.is_selected("a") and m.is_selected("b")
    assert [p["name"] for p in m.selected()] == ["A", "B"]

def test_toggle_subset():
    m = KiExchangeModel()
    m.register("a", "A", push=lambda: {"msg": "pa"})
    m.register("b", "B", push=lambda: {"msg": "pb"})
    m.toggle("b", False)
    assert not m.is_all()
    assert [p["key"] for p in m.selected()] == ["a"]
    m.toggle("b", True)
    assert m.is_all()

def test_run_aggregates_and_noop():
    m = KiExchangeModel()
    m.register("a", "A", push=lambda: {"ok": True, "msg": "ok-a"})
    m.register("b", "B")  # kein push
    res = m.run("push")
    assert res[0] == {"name": "A", "ok": True, "msg": "ok-a"}
    assert res[1]["msg"] == "(kein teilbarer Zustand)" and res[1]["ok"] is None

def test_run_catches_exceptions():
    m = KiExchangeModel()
    def boom(): raise RuntimeError("x")
    m.register("a", "A", pull=boom)
    res = m.run("pull")
    assert res[0]["ok"] is False and "FEHLER" in res[0]["msg"]

def test_run_none_return_is_ausgefuehrt():
    m = KiExchangeModel()
    m.register("a", "A", status=lambda: None)  # GUI-Handler loggt selbst, gibt None
    res = m.run("status")
    assert res[0]["ok"] is True and res[0]["msg"] == "(ausgefuehrt)"

def test_empty_selection():
    m = KiExchangeModel()
    m.register("a", "A", push=lambda: {"msg": "x"})
    m.set_all(False)
    res = m.run("push")
    assert res[0]["msg"] == "kein Reiter ausgewaehlt"

def test_format_result():
    m = KiExchangeModel()
    m.register("a", "A", push=lambda: {"msg": "ok-a"})
    s = m.format_result("push", m.run("push"))
    assert "An KI" in s and "ok-a" in s


def test_top_titlebar_integrates_context_and_ki_controls():
    import tkinter as tk
    from types import SimpleNamespace
    from gui import ReFrameworkApp

    root = tk.Tk()
    root.withdraw()
    try:
        # Stub minimal attributes expected by _build_ki_bar / _build_global_context_bar
        root.cfg = SimpleNamespace(config={"APP_PACKAGE": "com.example.kueckbox"})
        root._win_taskbar_fix = lambda: None
        root._win_dwm_chrome = lambda: None
        root._win_add_resize_grips = lambda: None
        root._win_start_maximized = lambda: None
        root._win_close = lambda: None
        root._win_toggle_max = lambda: None
        root._win_minimize = lambda: None
        root._win_drag_start = lambda e: None
        root._win_drag_move = lambda e: None
        root._win_drag_end = lambda e: None
        root._win_sysmenu = lambda e: None
        root.log = lambda _m: None

        ReFrameworkApp._init_context_vars(root)
        # Bind helper methods directly onto root to build the unified titlebar
        for name in (
            "_attach_tooltip", "_show_top_bar_help", "_init_context_vars",
            "_ctx_on_stand", "_ctx_fork_now", "_ctx_merge_now",
            "_ctx_save_project", "_ctx_load_project", "_populate_project_menu",
            "_build_context_controls", "_build_ki_controls", "_build_custom_titlebar",
            "_build_ki_bar", "_build_global_context_bar", "_update_context_bar",
        ):
            setattr(root, name, getattr(ReFrameworkApp, name).__get__(root, tk.Tk))

        root._build_ki_bar()
        root._build_global_context_bar()

        children_before = list(root.winfo_children())
        assert getattr(root, "_has_custom_titlebar", False) is True
        assert getattr(root, "_context_controls_built", False) is True
        assert len(children_before) == 1 and children_before[0] is root._titlebar

        def _all_texts(w):
            out = []
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                    if t:
                        out.append(str(t))
                except Exception:
                    pass
                out.extend(_all_texts(c))
            return out

        texts = _all_texts(root._titlebar)
        assert any("Fork" in t for t in texts)
        assert any("Merge" in t for t in texts)
        assert any("Projekt" in t for t in texts)
        assert any("An KI" in t for t in texts)
        assert any("Von KI" in t for t in texts)
        assert any("Abgleich" in t for t in texts)
        assert "?" in texts
    finally:
        try:
            root.destroy()
        except Exception:
            pass
