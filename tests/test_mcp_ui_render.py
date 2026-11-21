# -*- coding: utf-8 -*-
import os
import pytest
pytest.importorskip("PIL")   # Surface->PNG braucht Pillow; sonst ueberspringen
from mcp_server import tools
from core.java.demo_login import build_login_app
from core.java.surface import SceneGraphSurface


def _install_demo_surface():
    ctx = build_login_app()
    surf = SceneGraphSurface(ctx["tree"])
    tools._LAST_UI["surface"] = surf
    return surf


def test_ui_render_writes_png(tmp_path):
    _install_demo_surface()
    out = str(tmp_path / "ui.png")
    res = tools.native_ui_render({}, path=out, title="Test")
    assert res["ok"] and res["path"] == out
    assert os.path.getsize(out) > 500      # echte Grafik
    assert res["width"] and res["width"] > 0


def test_ui_render_without_run():
    tools._LAST_UI["surface"] = None
    # kein Lauf, keine last_ui.json im tmp -> freundlicher Hinweis statt Absturz
    import mcp_server.tools as T
    gf = T._GLASS_SESSION_FOLDER
    p = os.path.join(gf, "last_ui.json")
    had = os.path.isfile(p)
    if not had:
        res = tools.native_ui_render({})
        assert res["ok"] and (res.get("path") is None or os.path.isfile(res["path"]))


def test_ui_tap_and_key():
    _install_demo_surface()
    r = tools.native_ui_tap({}, 60, 40)      # irgendwo oben
    assert "handled" in r and r["ok"]
    rk = tools.native_ui_key({}, "x")
    assert rk["ok"] and "handled" in rk


def test_ui_tap_without_run():
    tools._LAST_UI["surface"] = None
    r = tools.native_ui_tap({}, 1, 1)
    assert r["ok"] and r["handled"] is False
