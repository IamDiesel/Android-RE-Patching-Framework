# -*- coding: utf-8 -*-
"""Dock-Framework: LayoutModel-Zustandslogik (headless, ohne tkinter)."""
import os
import tempfile

from ui.dock.layout_model import LayoutModel, ZONES


def _model():
    m = LayoutModel()
    m.register_view("transport", "Transport", "top", "S")
    m.register_view("editor", "Smali", "editor", "S")
    m.register_view("regs", "Register", "right", "S")
    m.register_view("xref", "XREF", "left", "D-")   # standardmaessig aus
    m.register_view("missing", "missing", "panel", "D+")
    return m


def test_register_and_default_visibility():
    m = _model()
    assert set(m.views) == {"transport", "editor", "regs", "xref", "missing"}
    assert m.placement["xref"]["visible"] is False   # D- -> aus
    assert m.placement["missing"]["visible"] is True  # D+ -> an
    assert "editor" in m.views_in("editor")
    assert "xref" not in m.views_in("left")           # ausgeblendet
    assert "xref" in m.views_in("left", visible_only=False)


def test_move_hide_pop():
    m = _model()
    m.set_zone("missing", "left")
    assert "missing" in m.views_in("left")
    assert "missing" not in m.views_in("panel")
    assert m.toggle_visible("regs") is False
    assert "regs" not in m.views_in("right")
    m.set_popped("editor", True)
    assert "editor" not in m.views_in("editor")          # pop-out zaehlt nicht zur Zone
    assert "editor" in m.views_in("editor", include_popped=True)


def test_preset_roundtrip():
    m = _model()
    m.save_preset("Kern")               # Default-Zustand
    m.set_zone("xref", "panel"); m.set_visible("xref", True); m.set_visible("regs", False)
    m.save_preset("Research")
    m.load_preset("Kern")
    assert m.placement["xref"]["zone"] == "left" and m.placement["regs"]["visible"] is True
    m.load_preset("Research")
    assert m.placement["xref"]["zone"] == "panel" and m.placement["regs"]["visible"] is False
    assert set(m.list_presets()) == {"Kern", "Research"}
    assert m.delete_preset("Research") and "Research" not in m.list_presets()


def test_json_persistence():
    m = _model()
    m.set_zone("missing", "left"); m.save_preset("P1")
    d = tempfile.mkdtemp(); path = os.path.join(d, "ui_layouts", "layout.json")
    m.save_json(path)
    m2 = _model()
    assert m2.load_json(path)
    assert m2.placement["missing"]["zone"] == "left"
    assert "P1" in m2.list_presets()


def test_reset_defaults():
    m = _model()
    m.set_zone("regs", "left"); m.set_visible("regs", False)
    m.reset_defaults()
    assert m.placement["regs"]["zone"] == "right" and m.placement["regs"]["visible"] is True


def test_groups_split():
    m = _model()
    # editor-Zone in zwei Gruppen splitten: 'disasm' hinzufuegen und in Gruppe 1
    m.register_view("disasm", "Disasm", "editor", "D+")
    assert m.groups_in("editor") == [0]
    g = m.next_group("editor"); assert g == 1
    m.set_group("disasm", g)
    assert m.groups_in("editor") == [0, 1]
    assert m.views_in("editor", group=0) == ["editor"]
    assert m.views_in("editor", group=1) == ["disasm"]


def test_group_state_hidden_popped():
    m = _model()
    assert not m.group_hidden("left", 0) and not m.group_popped("left", 0)
    m.set_group_hidden("left", 0, True); m.set_group_popped("editor", 1, True)
    assert m.group_hidden("left", 0) and m.group_popped("editor", 1)
    import json
    m2 = LayoutModel(); m2.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.group_hidden("left", 0) and m2.group_popped("editor", 1)
