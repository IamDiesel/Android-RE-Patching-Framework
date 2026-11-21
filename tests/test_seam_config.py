# -*- coding: utf-8 -*-
"""Tests fuer Paket 1: SeamConfig, skip_native Boundary-APs, VFS-2.0 AssetBridge, Self-Cache & Fork/Snapshot."""
from __future__ import annotations

import os
from types import SimpleNamespace

from core.java.asset_bridge import install_asset_stubs, seed_assets_from_dir
from core.java.bridge import JavaToNativeBridge
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.glass_runner import (
    InstanceState,
    ProjectSnapshotService,
    persist_appdata_from_vfs,
    sync_appdata_into_vfs,
)
from core.java.jni_boundary import JniBoundaryHook
from core.java.native_link import NativeLinkRuntime, NativeMethodTable
from core.java.objects import JArray, JString
from core.java.seam_config import SOLOADER_OVERRIDE_KEYS, SeamConfig
from core.native.vfs import VFS


def test_seam_config_profiles_and_cutoffs():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = SimpleNamespace(stubs=stubs)
    assert SOLOADER_OVERRIDE_KEYS[0] in vm.method_overrides

    hook = JniBoundaryHook()
    cfg = SeamConfig.from_profile("faithful")
    cfg.java_cutoffs = [
        {"cls": "Lt/Sec;", "name": "isRooted", "sig": "()Z", "ret": False},
        {"cls": "Lt/Sec;", "name": "noop", "sig": "()V", "ret": None},
        {"cls": "Lt/Sec;", "name": "tag", "sig": "()Ljava/lang/String;", "ret": "clean"},
    ]
    cfg.jni_cutoffs = [
        {"this_cls": "Lt/Native;", "method_name": "checkEnv", "method_sig": "()I",
         "skip_native": True, "ret_expr": "42"},
    ]
    stats = cfg.apply_to_vm(vm, native_fusion=True, jni_hook=hook)
    assert stats["soloader_removed"] == len(SOLOADER_OVERRIDE_KEYS)
    assert SOLOADER_OVERRIDE_KEYS[0] not in vm.method_overrides
    assert vm.method_overrides[("Lt/Sec;", "isRooted", "()Z")]([]) == 0
    assert vm.method_overrides[("Lt/Sec;", "noop", "()V")]([]) is None
    ret_s = vm.method_overrides[("Lt/Sec;", "tag", "()Ljava/lang/String;")]([])
    assert isinstance(ret_s, JString) and ret_s.text == "clean"
    assert len(hook.action_points) == 1
    assert hook.action_points[0]["skip_native"] is True

    # noop_native restores SoLoader overrides & disables JNI_OnLoad
    noop = SeamConfig.from_profile("noop_native")
    assert noop.should_run_onload("foo") is False
    noop.apply_to_vm(vm, native_fusion=False)
    assert SOLOADER_OVERRIDE_KEYS[0] in vm.method_overrides


def test_jni_boundary_skip_native_short_circuits_emu():
    calls = []

    class FakeEmu:
        @staticmethod
        def run(eng, fnptr, args, ret, sp, maxins):
            calls.append((fnptr, args))
            eng.cpu.x[0] = 999

    class FakeJni:
        def __init__(self):
            self.st = SimpleNamespace(handles={})

        def _new_handle(self, kind, cls=""):
            return 0x1000

    eng = SimpleNamespace(cpu=SimpleNamespace(x={0: 0}))
    bridge = JavaToNativeBridge(eng, FakeJni(), emu=FakeEmu)
    hook = JniBoundaryHook()
    hook.add_action_point(this_cls="Lt/Auth;", method_name="verify",
                          method_sig="(I)I", skip_native=True, ret_expr="7")
    bridge.boundary_hook = hook

    tbl = NativeMethodTable()
    tbl.register("Lt/Auth;", "verify", "(I)I", 0x5000, ret="int", static=True)
    rt = NativeLinkRuntime(None, bridge, tbl)

    ok, res = rt.resolve_invoke("Lt/Auth;", "verify", "(I)I", [12], True)
    assert ok is True
    assert res == 7
    assert calls == []  # ARM64-Emulation wurde uebersprungen!

    # Nicht passende Methode laeuft normal durch FakeEmu
    tbl.register("Lt/Auth;", "other", "(I)I", 0x5004, ret="int", static=True)
    ok2, res2 = rt.resolve_invoke("Lt/Auth;", "other", "(I)I", [12], True)
    assert ok2 is True
    assert res2 == 999
    assert len(calls) == 1


def test_asset_bridge_reads_and_lists_from_vfs2(tmp_path):
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    fs = VFS()
    fs.add_file("/data/app/assets/index.android.bundle", b"HERMES_V2")
    fs.add_file("/data/app/assets/sub/cfg.json", b'{"ok":true}')
    eng = SimpleNamespace(_os=SimpleNamespace(fs=fs))

    install_asset_stubs(stubs, eng=eng)
    asset_mgr = object()
    stream = stubs._by_name[("open", "(Ljava/lang/String;)Ljava/io/InputStream;")](
        [asset_mgr, JString("index.android.bundle")]
    )
    assert bytes(stream.fields["$data"]) == b"HERMES_V2"

    listed = stubs._by_name[("list", "(Ljava/lang/String;)[Ljava/lang/String;")](
        [asset_mgr, JString("sub")]
    )
    assert isinstance(listed, JArray)
    assert [x.text for x in listed.data] == ["cfg.json"]

    # seed_assets_from_dir spiegelt sowohl stubs.vfs als auch VFS 2.0
    adir = tmp_path / "assets"
    adir.mkdir()
    (adir / "extra.bin").write_bytes(b"EXTRA")
    n = seed_assets_from_dir(stubs, str(tmp_path), eng=eng)
    assert n == 1
    ino = fs.lookup("/data/app/assets/extra.bin")
    assert ino is not None and fs.read(ino) == b"EXTRA"


def test_project_snapshot_and_fork_merge(tmp_path):
    svc = ProjectSnapshotService(base_dir=str(tmp_path))
    main = InstanceState(package="com.demo.app", zone="patched", apply_patches=True)
    pkg_dir = main.pkg_appdata_dir(str(tmp_path))
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, "prefs.xml"), "w", encoding="utf-8") as fh:
        fh.write("<map><string name='env'>prod</string></map>")

    ctrl = SimpleNamespace(
        instance_state=main, zone="patched", zone_root="/tmp/app",
        apply_patches=True, app_entry="com.demo.Main", relpath="",
        bps=[2, 6], bp_meta={2: {"cond": "v0 == 1"}},
        app_bps={("Lcom/demo/Main;", "go", "()V", 2): {"cond": "v0 == 1"}},
        aps=[], jni_aps=[], watches=["v0"], net_mode="mock",
        native_fusion=True, seam_config=SeamConfig.from_profile("self_cache"),
        smali_patches={},
    )
    proj_path = svc.save_project("baseline", ctrl)
    assert os.path.isdir(proj_path)
    projs = svc.list_projects("com.demo.app")
    assert len(projs) == 1 and projs[0]["name"] == "baseline"

    # Fork erstellen & isoliert modifizieren
    fork_inst = svc.create_fork(ctrl, label="experiment-A")
    assert fork_inst.is_fork()
    fork_prefs = os.path.join(fork_inst.pkg_appdata_dir(str(tmp_path)), "prefs.xml")
    with open(fork_prefs, "w", encoding="utf-8") as fh:
        fh.write("<map><string name='env'>staging</string></map>")
    # Main ist unveraendert!
    with open(os.path.join(pkg_dir, "prefs.xml"), "r", encoding="utf-8") as fh:
        assert "prod" in fh.read()

    assert len(svc.list_forks("com.demo.app")) == 1
    res = svc.merge_fork(fork_inst.instance_id, ctrl)
    assert res["ok"] is True
    with open(os.path.join(pkg_dir, "prefs.xml"), "r", encoding="utf-8") as fh:
        assert "staging" in fh.read()

    # VFS AppData Sync Roundtrip
    fs = VFS()
    assert sync_appdata_into_vfs(fs, "com.demo.app", pkg_dir) >= 1
    fs.add_file("/data/data/com.demo.app/files/token.txt", b"tok123")
    written = persist_appdata_from_vfs(fs, "com.demo.app", pkg_dir)
    assert written >= 1
    assert (tmp_path / "data" / "vfs_appdata" / "com.demo.app" / "files" / "token.txt").read_bytes() == b"tok123"
    assert svc.discard_fork(fork_inst.instance_id) is True
