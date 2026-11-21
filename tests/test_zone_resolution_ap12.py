# -*- coding: utf-8 -*-
"""AP 1.2: echte Zonen-Aufloesung (original->APP_SOURCE_DIR, patched->DEST_DIR) + Staging-Sequenz."""
import os, tempfile, shutil
import pytest


class _Cfg:
    def __init__(self, src, dst):
        self.paths = {"APP_SOURCE_DIR": src, "DEST_DIR": dst}
        self.config = {"MANIFEST_STRATEGY": "smali_only"}


class _App:
    def __init__(self, cfg):
        self.cfg = cfg
        self._logs = []
    def after(self, *a, **k):
        pass


def _mk_unpacked(base):
    """Legt <base>/base_unpacked_apktool/smali an (minimale Zone)."""
    root = os.path.join(base, "base_unpacked_apktool")
    os.makedirs(os.path.join(root, "smali"), exist_ok=True)
    return root


def test_zone_resolution_original_vs_patched():
    from ui.controllers.debugger_controller import DebuggerController
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "source", "pkg"); dst = os.path.join(tmp, "destination", "pkg")
        os.makedirs(src); os.makedirs(dst)
        src_root = _mk_unpacked(src)         # original gestaged
        # dst NICHT gestaged
        c = DebuggerController(app=_App(_Cfg(src, dst)))
        c.zone = "original"
        assert c._resolve_root() == src_root
        # patched ohne Staging -> Fallback auf original (mit Hinweis)
        c.zone = "patched"
        assert c._resolve_root() == src_root
        # jetzt patched stagen
        dst_root = _mk_unpacked(dst)
        assert c._resolve_root() == dst_root  # patched jetzt aufgeloest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unpacked_name_apkeditor():
    from ui.controllers.debugger_controller import DebuggerController
    cfg = _Cfg("/s", "/d"); cfg.config["MANIFEST_STRATEGY"] = "apkeditor"
    c = DebuggerController(app=_App(cfg))
    assert c._unpacked_name() == "base_unpacked_apkeditor"


def test_engine_stage_sequence_order():
    """run_stage_destination feuert genau die 4 Staging-Steps in Reihenfolge, ohne APK-Build."""
    from core.pipeline.engine import PipelineEngine
    called = []
    class _FakeStep:
        def __init__(self, name): self.name = name
        def execute(self, cfg, eng): called.append(self.name); return True
    class _CfgMgr:
        config = {}; paths = {}
    eng = PipelineEngine.__new__(PipelineEngine)
    eng.cfg = _CfgMgr()
    eng._steps = {k: _FakeStep(k) for k in
                  ("mirror_workspace", "smart_patch", "inject_custom_libs", "inject_added_libs")}
    eng.log = lambda m: None
    assert eng.run_stage_destination() is True
    assert called == ["mirror_workspace", "smart_patch", "inject_custom_libs", "inject_added_libs"]
