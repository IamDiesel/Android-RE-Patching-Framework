# -*- coding: utf-8 -*-
"""G-OS-5 MCP: native.os_config_get / native.os_config_set (Mensch<->KI)."""
import json
from mcp_server import tools


def test_config_get_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "none"))
    r = tools.native_os_config_get(None)
    assert r["ok"] is False


def test_config_set_then_get(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "os"))
    r = tools.native_os_config_set(None, {"rand_seed": 42, "syscall_policy": {"ptrace": "deny"}})
    assert r["ok"] and r["seq"] == 1 and r["writer"] == "ki"
    g = tools.native_os_config_get(None)
    assert g["ok"] and g["config"]["rand_seed"] == 42
    assert g["config"]["syscall_policy"]["ptrace"] == "deny"


def test_config_set_json_string(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "os"))
    r = tools.native_os_config_set(None, json.dumps({"net_mode": "replay"}))
    assert r["ok"]
    assert tools.native_os_config_get(None)["config"]["net_mode"] == "replay"


def test_config_set_clobber(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "os"))
    tools.native_os_config_set(None, {"a": 1})              # seq 1
    tools.native_os_config_set(None, {"a": 2})              # seq 2
    r = tools.native_os_config_set(None, {"a": 3}, base_seq=1)
    assert r["ok"] is False and r["why"] == "conflict"


def test_config_set_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_OS_SESSION_FOLDER", str(tmp_path / "os"))
    assert tools.native_os_config_set(None, "nicht-json{")["ok"] is False
    assert tools.native_os_config_set(None, 123)["ok"] is False
