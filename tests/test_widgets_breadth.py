# -*- coding: utf-8 -*-
"""AP-E12 (#297): Widget-Breite — CheckBox/Switch/ProgressBar (Render + Zustand + Toggle). Headless."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.view_tree import install_view_stubs, ViewTree, ViewNode, paint_commands
from core.java.objects import JObject


def _mk(stubs, cls, sig="(Landroid/content/Context;)V", args=None):
    jo = JObject(cls)
    stubs.resolve(cls, "<init>", sig, [jo] + list(args or [None]), False)
    return jo, jo.fields["$view"]


def _render(node):
    t = ViewTree()
    root = ViewNode("LinearLayout"); root.add(node); t.set_root(root)
    return paint_commands(t)


def test_checkbox_state_and_render():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    jo, node = _mk(stubs, "Landroid/widget/CheckBox;")
    assert node.clickable                      # CheckBox ist klickbar
    assert stubs.resolve("x", "isChecked", "()Z", [jo], False)[1] == 0
    stubs.resolve("x", "setChecked", "(Z)V", [jo, 1], False)
    assert node.props["checked"] is True
    assert stubs.resolve("x", "isChecked", "()Z", [jo], False)[1] == 1
    texts = [c.get("text", "") for c in _render(node) if c["op"] == "text"]
    assert "✓" in texts                   # Haken wird gezeichnet, wenn checked


def test_checkbox_click_toggles():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    _, node = _mk(stubs, "Landroid/widget/CheckBox;")
    assert not node.props.get("checked")
    node.on_click()                            # Klick -> Toggle
    assert node.props["checked"] is True
    node.on_click()
    assert node.props["checked"] is False


def test_switch_knob_moves():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    jo, node = _mk(stubs, "Landroid/widget/Switch;")
    node.bounds = (0, 0, 200, 44)
    off = [c for c in paint_commands(_tree(node)) if c["op"] == "rect"]
    stubs.resolve("x", "setChecked", "(Z)V", [jo, 1], False)
    on = [c for c in paint_commands(_tree(node)) if c["op"] == "rect"]
    # Track-Farbe wechselt auf aktiv (#2f6df0) wenn an
    assert any(c["fill"] == "#2f6df0" for c in on)
    assert not any(c["fill"] == "#2f6df0" for c in off)


def test_progressbar_fill_width():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    jo, node = _mk(stubs, "Landroid/widget/ProgressBar;", sig="()V", args=[])
    stubs.resolve("x", "setProgress", "(I)V", [jo, 60], False)
    assert node.props["progress"] == 60
    node.bounds = (0, 0, 104, 44)              # nutzbare Breite 100
    fills = [c for c in paint_commands(_tree(node))
             if c["op"] == "rect" and c["fill"] == "#2f6df0"]
    assert fills and 55 <= fills[0]["w"] <= 65  # ~60% Fuellung


def _tree(node):
    t = ViewTree(); root = ViewNode("LinearLayout"); root.add(node)
    t.root = root
    # Bounds des Knotens bewahren (nicht neu layouten), Root grob setzen
    root.bounds = (0, 0, t.width, 60)
    return t


def test_gravity_text_anchor():
    """AP-E13: setGravity steuert die Textausrichtung (links/zentriert/rechts)."""
    from core.java.dalvik import DalvikVM
    from core.java.dex_loader import AndroidStubs
    from core.java.view_tree import install_view_stubs, ViewTree, ViewNode, paint_commands
    from core.java.objects import JObject

    def anchor_for(gval):
        vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
        jo = JObject("Landroid/widget/TextView;")
        stubs.resolve(jo.cls, "<init>", "(Landroid/content/Context;)V", [jo, None], False)
        node = jo.fields["$view"]; node.text = "Hallo"
        if gval is not None:
            stubs.resolve("x", "setGravity", "(I)V", [jo, gval], False)
        t = ViewTree(); root = ViewNode("LinearLayout"); root.add(node); t.set_root(node if False else root)
        node.bounds = (0, 0, 200, 44)
        cmd = [c for c in paint_commands(t) if c["op"] == "text" and c["text"] == "Hallo"][0]
        return cmd["anchor"]

    assert anchor_for(None) == "w"    # default links
    assert anchor_for(3) == "w"       # LEFT
    assert anchor_for(1) == "c"       # CENTER_HORIZONTAL
    assert anchor_for(17) == "c"      # CENTER
    assert anchor_for(5) == "e"       # RIGHT
