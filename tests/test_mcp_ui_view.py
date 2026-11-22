# -*- coding: utf-8 -*-
"""Tests fuer multimodale MCP-UI-Sicht (`native_ui_view`), Bild-Serialisierung,
Annotierungen, Live-GUI-Sync (`last_ui.json`) und `mcp 2.x`-Server-Aufruf.
"""
import asyncio
import base64
import io
import json
import os

import pytest
from PIL import Image

from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.surface import SceneGraphSurface
from core.java.ui_mcp_bridge import (
    build_ui_snapshot_dict,
    persist_live_ui_snapshot,
)
from core.java.view_tree import ViewNode, install_view_stubs
from core.java.virtual_clock import set_virtual_clock
from mcp_server import tools
from mcp_server.app_context import AppContext
from mcp_server.server import build_server
from tools.surface_raster import render_snapshot_to_png_bytes


def _sample_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (32, 32), (24, 120, 230, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _build_rich_tree():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    set_virtual_clock(stubs, iso_str="2028-05-01 10:15:00", tz_name="Europe/Berlin", frozen=True)

    root = ViewNode("LinearLayout")
    root.add(ViewNode("TextView", vid="hdr", text="Willkommen"))
    root.add(ViewNode("ImageView", vid="logo", props={"img": "logo", "img_bytes": _sample_png_bytes()}))
    root.add(ViewNode("EditText", vid="email_in", text="", focusable=True, props={"hint": "E-Mail"}))
    root.add(ViewNode("Button", vid="btn_start", text="Start", clickable=True))
    tree.set_root(root)
    tree.width = 380
    tree.height = 776
    tree.layout()
    tree.last_toast = "Test Toast"
    return tree


def _restore_last_ui(orig_ui):
    tools._LAST_UI.clear()
    tools._LAST_UI.update(orig_ui)


def test_ui_mcp_bridge_handles_binary_images_and_interactive_coords(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path))
    orig_ui = dict(tools._LAST_UI)
    try:
        tools._LAST_UI["surface"] = None
        tree = _build_rich_tree()
        surf = SceneGraphSurface(tree)
        snap = build_ui_snapshot_dict(surf, package="com.example.demo", source="headless_test")

        # Binaerbild wurde sauber nach data_b64 konvertiert (kein raw bytes -> JSON moeglich)
        img_cmds = [c for c in snap["paint"] if c.get("op") == "image"]
        assert img_cmds and "data_b64" in img_cmds[0] and "data" not in img_cmds[0]
        assert "01.05.2028 10:15" in snap["status_clock"]
        assert snap["toast_text"] == "Test Toast"

        out_json = persist_live_ui_snapshot(snap, out_dir=str(tmp_path))
        assert os.path.isfile(out_json)
        loaded = json.loads(open(out_json, "r", encoding="utf-8").read())
        els = loaded["interactive_elements"]
        btn = next(e for e in els if e.get("text") == "Start")
        bx, by, bw, bh = btn["bounds"]
        assert btn["tap_xy"] == [bx + bw // 2, by + bh // 2]
        assert btn["in_viewport"] is True

        png_bytes, w, h = render_snapshot_to_png_bytes(loaded, annotate=True)
        assert len(png_bytes) > 1000
        assert (w, h) == (418, 894)
        im = Image.open(io.BytesIO(png_bytes))
        assert im.size == (418, 894)
    finally:
        _restore_last_ui(orig_ui)


def test_live_gui_sync_to_mcp_native_ui_view(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path))
    orig_ui = dict(tools._LAST_UI)
    try:
        tools._LAST_UI["surface"] = None
        tree = _build_rich_tree()
        snap = build_ui_snapshot_dict(tree=tree, package="com.demo.app", source="gui_live")
        persist_live_ui_snapshot(snap, out_dir=str(tmp_path))

        res = tools.native_ui_view({}, annotate=True, source="auto")
        assert res["ok"] is True
        assert res["png_bytes"] and len(res["png_bytes"]) > 1000
        assert Image.open(io.BytesIO(res["png_bytes"])).size == (418, 894)
        meta = res["meta"]
        assert meta["source"] == "gui_live"
        assert meta["package"] == "com.demo.app"
        assert "❄️" in meta["status_clock"]
        assert "Willkommen" in meta["visible_texts"]
        assert any(el.get("text") == "Start" for el in meta["interactive_elements"])

        snap_res = tools.native_ui_snapshot({})
        assert snap_res["ok"] is True
        assert "Willkommen" in snap_res["text"]
        assert snap_res["interactive_elements"]
    finally:
        _restore_last_ui(orig_ui)


def test_mcp_server_call_tool_returns_multimodal_image_and_text_content(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path))
    orig_ui = dict(tools._LAST_UI)
    try:
        tools._LAST_UI["surface"] = None
        tree = _build_rich_tree()
        snap = build_ui_snapshot_dict(tree=tree, package="com.mcp.vision", source="gui_live")
        persist_live_ui_snapshot(snap, out_dir=str(tmp_path))

        ctx = AppContext()
        mcp_inst = build_server(ctx, "127.0.0.1", 8765)
        call_res = asyncio.run(mcp_inst.call_tool("native_ui_view", {"annotate": True}))

        assert call_res.is_error is False
        assert len(call_res.content) >= 2
        img_block = call_res.content[0]
        txt_block = call_res.content[1]
        assert img_block.type == "image"
        assert img_block.mime_type == "image/png"
        decoded_png = base64.b64decode(img_block.data)
        assert Image.open(io.BytesIO(decoded_png)).size == (418, 894)

        assert txt_block.type == "text"
        payload = json.loads(txt_block.text)
        assert payload["ok"] is True
        assert payload["package"] == "com.mcp.vision"
        assert any(el.get("tap_xy") for el in payload["interactive_elements"])
    finally:
        _restore_last_ui(orig_ui)


def test_forward_to_gui_queues_tap_and_scroll(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_REPO", str(tmp_path))
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path))
    orig_ui = dict(tools._LAST_UI)
    try:
        tools._LAST_UI["surface"] = None
        r_tap = tools.native_ui_tap({}, 120, 340, forward_to_gui=True)
        assert r_tap["ok"] is True and r_tap["handled"] is True and r_tap["queued_to_gui"] is True

        r_key = tools.native_ui_key({}, "geheim", forward_to_gui=True)
        assert r_key["ok"] is True and r_key["handled"] is True and r_key["queued_to_gui"] is True

        r_scr = tools.native_glass_control({}, "ui_scroll", '{"dy": 180}')
        assert r_scr["ok"] is True and r_scr["queue_len"] == 3

        qfile = tmp_path / "data" / "gui_cmd_queue.json"
        cmds = json.loads(qfile.read_text(encoding="utf-8"))
        assert [c["cmd"] for c in cmds] == ["ui_tap", "ui_key", "ui_scroll"]
        assert cmds[0]["x"] == 120 and cmds[0]["y"] == 340
        assert cmds[1]["text"] == "geheim"
        assert cmds[2]["dy"] == 180
    finally:
        _restore_last_ui(orig_ui)
