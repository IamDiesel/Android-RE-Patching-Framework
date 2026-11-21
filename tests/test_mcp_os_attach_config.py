# -*- coding: utf-8 -*-
"""MCP-OS-2: _merge_os_config (os_attach-Durchreiche)."""
from mcp_server.tools import _merge_os_config


def test_net_replay_str_to_bytes_and_default_mode():
    c = _merge_os_config({}, {"1.2.3.4:443": "RESP"}, None, False, 4096, None)
    assert c["net_replay"]["1.2.3.4:443"] == b"RESP"
    assert c["net_mode"] == "replay"


def test_explicit_net_mode_wins():
    c = _merge_os_config({}, {"h:1": "x"}, "record", False, 4096, None)
    assert c["net_mode"] == "record"


def test_trace_and_policy():
    c = _merge_os_config({"uid": 10234}, None, None, True, 128, {"getpid": "deny"})
    assert c["uid"] == 10234
    assert c["trace_syscalls"] is True and c["trace_max"] == 128
    assert c["syscall_policy"] == {"getpid": "deny"}


def test_empty_passthrough_noop():
    c = _merge_os_config({"pid": 1}, None, None, False, 4096, None)
    assert c == {"pid": 1}
