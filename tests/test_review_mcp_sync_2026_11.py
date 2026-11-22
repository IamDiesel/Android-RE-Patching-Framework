# -*- coding: utf-8 -*-
"""Review-Verifikation (2026-11): X1 Typing-Import, N4 Glass-Run Wall-Timeout & Z4 Atomare GUI-Queue / Live-State."""
import inspect
import json
import os
import pathlib
import threading
import time
import types
from typing import Optional

import pytest

import gui
from core.java.dalvik import DalvikVM
from core.java.debug import JavaDebugger
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import LifecycleRunner
from core.java.net_recorder import NetLog
from core.java.view_tree import ViewNode, ViewTree
from mcp_server import tools
from mcp_server.app_context import AppContext


@pytest.fixture(autouse=True)
def _restore_tools_state():
    orig_ui = dict(tools._LAST_UI)
    orig_glass = dict(tools._LAST_GLASS)
    orig_clock = tools._DEFAULT_VIRTUAL_CLOCK
    orig_cache = tools._RAM_CACHE
    try:
        yield
    finally:
        prev_dbg = tools._LAST_GLASS.get("jdb")
        if prev_dbg is not None and prev_dbg is not orig_glass.get("jdb"):
            try:
                prev_dbg.stop(wait_timeout=1.0)
            except Exception:
                pass
        tools._LAST_UI.clear()
        tools._LAST_UI.update(orig_ui)
        tools._LAST_GLASS.clear()
        tools._LAST_GLASS.update(orig_glass)
        tools._DEFAULT_VIRTUAL_CLOCK = orig_clock
        tools._RAM_CACHE = orig_cache


def _make_ctx(tmp_path, monkeypatch):
    ctx = AppContext()
    cfg_file = tmp_path / "config.json"
    ctx.cfg.config_file = str(cfg_file)
    ctx.cfg.config["BASE_DIR"] = str(tmp_path)
    ctx.cfg.config["APP_PACKAGE"] = "com.review.sync"
    ctx.cfg.config["SPLIT_NAME"] = "com.review.sync"
    ctx.cfg._update_paths()
    ctx.cfg.save()
    monkeypatch.setattr(tools, "_GLASS_SESSION_FOLDER", str(tmp_path / "glass"))
    return ctx


def _write_smali_app(app_dir, cls_name, body_lines):
    rel = cls_name[1:-1] + ".smali"
    fpath = app_dir / "smali" / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join([
        f".class public {cls_name}",
        ".super Landroid/app/Activity;",
        ".method public onCreate(Landroid/os/Bundle;)V",
        "    .registers 4",
        *body_lines,
        ".end method",
        "",
    ])
    fpath.write_text(text, encoding="utf-8")
    return str(app_dir)


def test_x1_tools_typing_imports_and_annotations():
    src = pathlib.Path(tools.__file__).read_text(encoding="utf-8")
    assert "from typing import" in src and "Optional" in src
    ann = inspect.get_annotations(tools._lc_resolve_addr, eval_str=True)
    assert ann.get("return") == Optional[int]

    for name, obj in vars(tools).items():
        if callable(obj) and getattr(obj, "__module__", "") == tools.__name__:
            inspect.get_annotations(obj, eval_str=True)


def test_n4_native_glass_run_stops_stalled_dalvik_loop_and_previous_jdb(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    root = _write_smali_app(
        tmp_path / "loop_app",
        "Lt/LoopAct;",
        [
            "    :loop",
            "    goto :loop",
            "    return-void",
        ],
    )

    stopped_prev = []

    class _ActivePrevDbg:
        mode = "paused"
        _thread = None

        def stop(self, wait_timeout=None):
            stopped_prev.append(wait_timeout)
            self.mode = "stopped"
            return {"mode": "stopped"}

    tools._LAST_GLASS.clear()
    tools._LAST_GLASS["jdb"] = _ActivePrevDbg()

    t0 = time.monotonic()
    res = tools.native_glass_run(
        ctx,
        studio_root=root,
        zone="original",
        entry="Lt/LoopAct;",
        max_steps=10_000_000,
        max_wall_s=0.35,
    )
    elapsed = time.monotonic() - t0

    assert stopped_prev == [1.0]
    assert res["ok"] is True, res.get("hinweis")
    assert res.get("timed_out") is True
    assert res["mode"] == "stopped"
    assert elapsed < 3.0
    dbg = tools._LAST_GLASS["jdb"]
    assert dbg._thread is None or not dbg._thread.is_alive()


def test_n4_native_glass_run_stops_gt_1_5s_stalled_worker(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    root = _write_smali_app(
        tmp_path / "slow_app",
        "Lt/SlowAct;",
        ["    return-void"],
    )

    def _stalled_run_app(self, studio_root=None, application=None, launcher=None):
        time.sleep(1.8)
        return {"package": "com.review.sync", "missing": []}

    monkeypatch.setattr(LifecycleRunner, "run_app", _stalled_run_app)

    res = tools.native_glass_run(
        ctx,
        studio_root=root,
        zone="original",
        entry="Lt/SlowAct;",
        max_wall_s=0.35,
    )
    assert res["ok"] is True, res.get("hinweis")
    assert res.get("timed_out") is True
    dbg = tools._LAST_GLASS["jdb"]
    assert dbg._thread is None or not dbg._thread.is_alive()


def test_z4_concurrent_queue_writers_and_gui_claim_zero_loss(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    claimed = []
    claim_lock = threading.Lock()
    stop_poller = threading.Event()
    errors = []

    class _RecordCtrl:
        _busy = False

        def add_app_bp(self, cls, name, sig="", pc=0):
            with claim_lock:
                claimed.append((cls, name, pc))

    fake_app = types.SimpleNamespace(
        cfg=ctx.cfg,
        debugger_tab=types.SimpleNamespace(ctrl=_RecordCtrl()),
    )

    n_threads = 6
    per_thread = 15

    def _writer(tid):
        try:
            for idx in range(per_thread):
                tools._queue_gui_cmd(
                    ctx,
                    {"cmd": "set_bp", "cls": "Lt/A;", "name": f"m_{tid}_{idx}", "pc": idx},
                )
        except Exception as exc:
            errors.append(exc)

    def _poller():
        try:
            while not stop_poller.is_set():
                gui.ReFrameworkApp._poll_gui_cmd_queue(fake_app)
                time.sleep(0.005)
        except Exception as exc:
            errors.append(exc)

    p_thr = threading.Thread(target=_poller, daemon=True)
    p_thr.start()
    writers = [threading.Thread(target=_writer, args=(t,)) for t in range(n_threads)]
    for w in writers:
        w.start()
    for w in writers:
        w.join(timeout=10.0)
    stop_poller.set()
    p_thr.join(timeout=5.0)
    gui.ReFrameworkApp._poll_gui_cmd_queue(fake_app)

    assert not errors
    expected = {("Lt/A;", f"m_{t}_{i}", i) for t in range(n_threads) for i in range(per_thread)}
    assert len(claimed) == n_threads * per_thread
    assert set(claimed) == expected
    assert not (tmp_path / "data" / "gui_cmd_queue.json").exists()
    ack = json.loads((tmp_path / "data" / "gui_cmd_ack.json").read_text(encoding="utf-8"))
    assert ack["remaining"] == 0
    assert ack["last_seq"] >= n_threads * per_thread


def test_z4_gui_poll_requeues_remaining_mid_batch_when_busy(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)
    executed = []

    class _BusyCtrl:
        _busy = False

        def add_app_bp(self, cls, name, sig="", pc=0):
            executed.append(("set_bp", name))

        def start_app(self):
            executed.append(("start_app",))
            self._busy = True

        def dispatch_ui_event(self, kind, **kw):
            executed.append((f"ui_{kind}", kw))

    ctrl = _BusyCtrl()
    fake_app = types.SimpleNamespace(
        cfg=ctx.cfg,
        debugger_tab=types.SimpleNamespace(ctrl=ctrl),
    )

    tools._queue_gui_cmd(ctx, {"cmd": "set_bp", "cls": "Lt/A;", "name": "boot", "pc": 0})
    tools._queue_gui_cmd(ctx, {"cmd": "start_app"})
    tools._queue_gui_cmd(ctx, {"cmd": "ui_tap", "x": 42, "y": 84})

    qfile = tmp_path / "data" / "gui_cmd_queue.json"
    queued_before = json.loads(qfile.read_text(encoding="utf-8"))
    seqs = [item["seq"] for item in queued_before]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3

    gui.ReFrameworkApp._poll_gui_cmd_queue(fake_app)
    assert executed == [("set_bp", "boot"), ("start_app",)]
    assert qfile.is_file()
    rem = json.loads(qfile.read_text(encoding="utf-8"))
    assert [c["cmd"] for c in rem] == ["ui_tap"]
    ack1 = json.loads((tmp_path / "data" / "gui_cmd_ack.json").read_text(encoding="utf-8"))
    assert ack1["processed"] == 2
    assert ack1["remaining"] == 1
    assert ack1["last_seq"] == seqs[1]

    tools._queue_gui_cmd(ctx, {"cmd": "ui_key", "text": "ok"})
    ctrl._busy = False
    gui.ReFrameworkApp._poll_gui_cmd_queue(fake_app)
    assert [e[0] for e in executed] == ["set_bp", "start_app", "ui_tap", "ui_key"]
    assert not qfile.exists()
    ack2 = json.loads((tmp_path / "data" / "gui_cmd_ack.json").read_text(encoding="utf-8"))
    assert ack2["processed"] == 2
    assert ack2["remaining"] == 0
    assert ack2["last_seq"] > seqs[2]


def test_z4_atomic_json_surrogates_stale_lock_and_live_state_missing_merge(tmp_path, monkeypatch):
    ctx = _make_ctx(tmp_path, monkeypatch)

    # 1. Lone UTF-16 surrogates cleaned + no .tmp left behind
    target_json = tmp_path / "surrogate.json"
    tools._atomic_write_json(str(target_json), {"msg": "hi-\ud800-\udfff", "nested": ["ok\ud801"]})
    loaded = json.loads(target_json.read_text(encoding="utf-8"))
    assert "\ufffd" in loaded["msg"]
    assert not list(tmp_path.glob("surrogate.json.*.tmp"))

    # 2. Stale lock (>10s mtime) cleaned automatically
    lock_file = tmp_path / "stale.lock"
    lock_file.write_text("99999:1\n", encoding="ascii")
    stale_ts = time.time() - 15.0
    os.utime(lock_file, (stale_ts, stale_ts))
    with tools._file_lock(str(lock_file), timeout=1.0):
        assert lock_file.exists()
    assert not lock_file.exists()

    # 3. _sync_headless_glass_outputs merges vm.missing + stubs.missing, writes deviations + writer + views_count
    vm = DalvikVM()
    vm.missing = [
        {"cls": "Lcom/app/A;", "name": "alpha", "sig": "()V"},
        {"cls": "Lcom/shared/Dup;", "name": "both", "sig": "()I"},
    ]

    class _FakeDevs:
        def to_list(self):
            return [{"kind": "stub", "target": "Landroid/os/Debug;->isDebuggerConnected()Z"}]

    vm.deviations = _FakeDevs()
    stubs = AndroidStubs(vm=vm)
    stubs.missing = [
        {"cls": "Lcom/shared/Dup;", "name": "both", "sig": "()I"},
        {"cls": "Landroid/os/Debug;", "name": "isDebuggerConnected", "sig": "()Z"},
    ]
    dbg = JavaDebugger(vm)
    tree = ViewTree()
    root_node = ViewNode("LinearLayout")
    root_node.add(ViewNode("TextView", text="Sync"))
    tree.set_root(root_node)

    tools._LAST_GLASS.clear()
    tools._LAST_GLASS.update({
        "jdb": dbg,
        "stubs": stubs,
        "tree": tree,
        "net_log": NetLog(),
        "package": "com.review.sync",
        "zone": "original",
        "missing": [{"cls": "Lcom/app/A;", "name": "alpha", "sig": "()V"}],
    })

    payload = tools._sync_headless_glass_outputs(ctx)
    live_path = tmp_path / "glass" / "live_state.json"
    assert live_path.is_file()
    disk_live = json.loads(live_path.read_text(encoding="utf-8"))
    assert payload["writer"] == "mcp_headless"
    assert disk_live["writer"] == "mcp_headless"
    assert disk_live["views"] == len(tree.nodes())
    assert disk_live["views_count"] == len(tree.nodes())
    assert disk_live["deviations"] == [{"kind": "stub", "target": "Landroid/os/Debug;->isDebuggerConnected()Z"}]
    assert disk_live["missing_count"] == 3

    insp = tools.native_glass_inspect(ctx, section="missing")
    assert insp["ok"] is True
    assert insp["count"] == 3

    # 4. End-to-end native_glass_run merges vm.missing + stubs.missing instead of short-circuiting
    e2e_root = _write_smali_app(
        tmp_path / "miss_app",
        "Lt/MissAct;",
        [
            "    new-instance v0, Lcom/missing/AppHelper;",
            "    invoke-direct {v0}, Lcom/missing/AppHelper;-><init>()V",
            "    invoke-virtual {v0}, Lcom/missing/AppHelper;->ping()V",
            "    invoke-static {}, Landroid/os/Debug;->isDebuggerConnected()Z",
            "    return-void",
        ],
    )

    run_res = tools.native_glass_run(
        ctx,
        studio_root=e2e_root,
        zone="original",
        entry="Lt/MissAct;",
        max_steps=50_000,
    )
    assert run_res["ok"] is True, run_res.get("hinweis")
    assert run_res["missing_count"] >= 2
    run_miss = tools.native_glass_inspect(ctx, section="missing")
    sigs = {(m["cls"], m["name"], m["sig"]) for m in run_miss["missing"]}
    assert ("Lcom/missing/AppHelper;", "ping", "()V") in sigs
    assert ("Landroid/os/Debug;", "isDebuggerConnected", "()Z") in sigs
    e2e_live = json.loads((tmp_path / "glass" / "live_state.json").read_text(encoding="utf-8"))
    assert e2e_live["writer"] == "mcp_headless"
    assert isinstance(e2e_live.get("deviations"), list)
