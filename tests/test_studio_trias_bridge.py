# -*- coding: utf-8 -*-
"""Tests fuer Paket 3, 4, 6A & 6D: StudioClassProvider Live-Patch-Overlay,
DebuggerController Cross-Class-BPs / Call-Stack / UI-Interaction / Pause, MCP Control & Fork."""
from __future__ import annotations

import json
import os
import time

from core.java.class_provider import StudioClassProvider
from core.java.view_tree import ViewNode, ViewTree
from mcp_server import tools
from ui.controllers.debugger_controller import DebuggerController


SMALI_CALLER = """.class public Lt/Caller;
.super Ljava/lang/Object;
.method public static entry()I
    .registers 1
    invoke-static {}, Lt/Callee;->val()I
    move-result v0
    return v0
.end method
"""

SMALI_CALLEE_V1 = """.class public Lt/Callee;
.super Ljava/lang/Object;
.method public static val()I
    .registers 1
    const/4 v0, 0x3
    return v0
.end method
"""

SMALI_CALLEE_V2 = """.class public Lt/Callee;
.super Ljava/lang/Object;
.method public static val()I
    .registers 1
    const/4 v0, 0x9
    return v0
.end method
"""


def test_studio_class_provider_live_patch_overlay_and_new_file(tmp_path):
    root = tmp_path / "app"
    (root / "smali" / "t").mkdir(parents=True)
    (root / "smali" / "t" / "Callee.smali").write_text(SMALI_CALLEE_V1, encoding="utf-8")

    prov = StudioClassProvider(str(root), zone="patched")
    assert "0x3" in prov.get_source("Lt/Callee;")
    assert len(prov.get_class("Lt/Callee;")) == 1

    # In-Memory file patch overrides disk without touching disk
    n = prov.apply_smali_patches([
        {"type": "smali", "file": "smali/t/Callee.smali", "orig": SMALI_CALLEE_V1, "edit": SMALI_CALLEE_V2, "scope": "file"},
        {"type": "new_file", "file": "smali/t/Added.smali", "edit": ".class public Lt/Added;\n.super Ljava/lang/Object;\n"},
    ])
    assert n == 2
    assert "0x9" in prov.get_source("Lt/Callee;")
    assert prov.get_class("Lt/Added;") is not None or prov.get_source("Lt/Added;") is not None
    # Disk remains unchanged
    assert "0x3" in (root / "smali" / "t" / "Callee.smali").read_text(encoding="utf-8")


def test_debugger_controller_cross_class_bps_callstack_and_ui_interaction(tmp_path):
    root = tmp_path / "app"
    (root / "smali" / "t").mkdir(parents=True)
    (root / "smali" / "t" / "Caller.smali").write_text(SMALI_CALLER, encoding="utf-8")
    (root / "smali" / "t" / "Callee.smali").write_text(SMALI_CALLEE_V1, encoding="utf-8")

    c = DebuggerController(app=None)
    c.zone_root = str(root)
    c.source = SMALI_CALLER
    c.name = "entry"
    c.entry_stop = True

    # Cross-class breakpoint on Callee->val @ pc=1
    c.add_app_bp("Lt/Callee;", "val", "()I", pc=1)
    assert ("Lt/Callee;", "val", "()I", 1) in c.app_bps

    c.start()
    for _ in range(80):
        if not c._busy and c._jdb and c._jdb.mode == "paused":
            break
        time.sleep(0.02)

    assert c._jdb is not None and c._jdb.mode == "paused"
    c.cont()
    for _ in range(80):
        if not c._busy and c._jdb.mode in ("paused", "done", "error"):
            break
        time.sleep(0.02)

    assert c._jdb.mode == "paused"
    st = c._jdb.state()
    assert st["cls"] == "Lt/Callee;" and st["name"] == "val" and st["pc"] == 1
    frames = c._jdb.callstack()
    assert len(frames) >= 2
    assert frames[0]["cls"] == "Lt/Callee;"
    assert frames[1]["cls"] == "Lt/Caller;"

    # Auto-Source-Sync loaded Callee into controller
    assert c.name == "val"
    assert "0x3" in c.source

    # Stop resets safely
    c.stop()
    assert c._jdb.mode in ("stopped", "paused", "done")

    # Test UI interaction routing for vm="glass"
    tree = ViewTree()
    clicked = []
    btn = ViewNode(cls="Button", text="Login", clickable=True)
    btn.on_click = lambda _n: clicked.append("tap!")
    btn.bounds = (10, 10, 120, 40)
    tree.root = btn
    c.glass_tree = tree
    from core.java.dalvik import DalvikVM
    from core.java.debug import JavaDebugger
    c._jdb = JavaDebugger(DalvikVM())
    c.dispatch_ui_event("tap", x=30, y=20)
    for _ in range(50):
        if clicked or not c._busy:
            break
        time.sleep(0.02)
    assert clicked == ["tap!"]


def test_mcp_glass_state_control_and_fork():
    st = tools.native_glass_state(None)
    assert st["ok"] is True

    frk = tools.native_glass_fork(None, op="fork", package="pkg.test.mcp", label="mcp-probe")
    assert frk["ok"] is True
    fid = frk["fork"]["instance_id"]
    lst = tools.native_glass_fork(None, op="list", package="pkg.test.mcp")
    assert any(f["instance_id"] == fid for f in lst["forks"])
    assert tools.native_glass_fork(None, op="discard", fork_id=fid)["ok"] is True

    # Cleanup queued file only within test scope if needed
    q = tools.native_glass_control(None, "open_debugger", json.dumps({"reason": "test"}))
    assert q["ok"] is True and q["queued"]["cmd"] == "open_debugger"
    qpath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(tools.__file__))), "data", "gui_cmd_queue.json")
    if os.path.isfile(qpath):
        os.remove(qpath)
