# -*- coding: utf-8 -*-
"""G-OS-1: OsInspectorModel (rein, MVC)."""
from core.application.os_inspector_model import OsInspectorModel

_SNAP = {
    "coverage": {"distinct": 2, "top": [["openat", 3], ["read", 2]]},
    "trace": {"len": 2, "last": [{"n": "openat", "args": [0], "ret": 3}]},
    "policy": {"ptrace": "deny"},
    "mmaps": [{"base": "0x72000000", "size": 4096, "prot": 3, "fd": None}],
    "vfs_top": [["dev", "dir"], ["proc", "dir"], ["sys", "dir"]],
    "sockets_detail": [{"fd": 3, "peer": ["10.0.0.9", 443], "tx": 10, "rx": 20}],
    "net": {"mode": "replay", "peers": ["10.0.0.9:443"]},
    "threads_detail": [{"tid": 1235, "flags": 0}],
    "signals_detail": [{"sig": 11, "handler": "0xbeef"}],
    "auxv_detail": [[6, 4096], [0, 0]],
    "brk_hex": "0x71000000",
}


def test_ingest_and_rows():
    m = OsInspectorModel(); m.ingest(_SNAP)
    assert m.coverage_rows()[0] == ["openat", 3]
    assert m.trace_rows()[0]["ret"] == 3
    assert ["dev", "dir"] in m.vfs_rows()
    assert m.mmap_rows()[0]["size"] == 4096
    assert m.socket_rows()[0]["fd"] == 3
    assert m.net_peers() == ["10.0.0.9:443"]
    assert m.thread_rows()[0]["tid"] == 1235
    assert m.signal_rows()[0]["sig"] == 11
    assert m.auxv_rows()[0] == [6, 4096]
    assert m.brk() == "0x71000000"


def test_unified_policy_rows():
    m = OsInspectorModel(); m.ingest(_SNAP)
    m.set_policy("getpid", "stub")
    m.set_override("getuid", ret=4242)
    rows = {r["name"]: r for r in m.policy_rows()}
    assert rows["ptrace"]["os9"] == "deny"       # aus Snapshot
    assert rows["getpid"]["os9"] == "stub"       # aus Delta
    assert rows["getuid"]["override_ret"] == 4242


def test_config_delta_build():
    m = OsInspectorModel()
    m.set_policy("ptrace", "deny")
    m.set_net_replay("10.0.0.9:443", b"OK")
    m.set_seed(123)
    m.set_trace(True)
    m.set_vfs("/sys/fs/selinux/enforce", "0")
    cfg = m.build_config()
    assert cfg["syscall_policy"]["ptrace"] == "deny"
    assert cfg["net_replay"]["10.0.0.9:443"] == b"OK" and cfg["net_mode"] == "replay"
    assert cfg["rand_seed"] == 123 and cfg["trace_syscalls"] is True
    assert cfg["vfs_mounts"]["/sys/fs/selinux/enforce"] == "0"


def test_allow_clears_policy():
    m = OsInspectorModel()
    m.set_policy("getpid", "deny")
    m.set_policy("getpid", "allow")
    assert "getpid" not in m.build_config().get("syscall_policy", {})


def test_subscribe_notifies():
    m = OsInspectorModel(); hits = []
    m.subscribe(lambda: hits.append(1))
    m.ingest(_SNAP); m.set_seed(1)
    assert len(hits) == 2


def test_load_and_reset():
    m = OsInspectorModel()
    m.load_config({"rand_seed": 9})
    assert m.build_config() == {"rand_seed": 9}
    m.reset_delta()
    assert m.build_config() == {}
