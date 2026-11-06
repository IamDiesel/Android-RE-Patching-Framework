# -*- coding: utf-8 -*-
"""Tests fuer Plugin-Dynamik Stufe 2: UnpackRegistry.discover sammelt gui_contributions()
aus Plugin-Modulen (synthetisches Plugin, ohne das echte IP-Plugin anzufassen)."""
from core.native.unpack import UnpackRegistry

PLUGIN_SRC = '''
from core.native.unpack import UnpackPlugin


class _Dummy(UnpackPlugin):
    name = "dummy"
    def unpack(self, packed_path, out_path=None):
        return {"ok": False, "why": "test"}


def register(reg):
    reg.register(_Dummy())


def gui_contributions():
    return [{"kind": "button", "label": "Dummy-Aktion", "action": "noop", "tooltip": "t", "plugin": "dummy"}]
'''


def test_discover_collects_contributions(tmp_path):
    d = tmp_path / "plugins"; d.mkdir()
    (d / "dummy_plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    reg = UnpackRegistry()
    n = reg.discover(str(d))
    assert n == 1, n
    assert reg.names() == ["dummy"], reg.names()
    cs = reg.contributions()
    assert len(cs) == 1, cs
    assert cs[0]["label"] == "Dummy-Aktion" and cs[0]["action"] == "noop"


def test_plugin_without_manifest_ok(tmp_path):
    d = tmp_path / "p2"; d.mkdir()
    (d / "bare_plugin.py").write_text("def register(reg):\n    pass\n", encoding="utf-8")
    reg = UnpackRegistry()
    reg.discover(str(d))
    assert reg.contributions() == []


def test_no_plugins_dir_ok():
    reg = UnpackRegistry()
    assert reg.discover("/does/not/exist___") == 0
    assert reg.contributions() == []
