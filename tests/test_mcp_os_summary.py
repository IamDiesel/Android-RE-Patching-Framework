# -*- coding: utf-8 -*-
"""MCP-OS-3: native.os_summary (read-only)."""
import json, os
from mcp_server import tools


def test_os_summary_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "none"))
    r = tools.native_os_summary(None)
    assert r["ok"] is False and "os_attach" in r["hinweis"]


def test_os_summary_roundtrip(tmp_path, monkeypatch):
    folder = str(tmp_path / "os")
    os.makedirs(folder, exist_ok=True)
    payload = {"ts": 1.0, "entry": "JNI_OnLoad", "stop": "done",
               "summary": {"coverage": {"distinct": 3, "top": [["openat", 2]]},
                           "trace": {"len": 5, "last": []},
                           "net": {"mode": "replay", "peers": ["10.0.0.9:443"]}}}
    with open(os.path.join(folder, "last_summary.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", folder)
    r = tools.native_os_summary(None)
    assert r["ok"] is True
    assert r["summary"]["coverage"]["distinct"] == 3
    assert r["summary"]["net"]["peers"] == ["10.0.0.9:443"]
