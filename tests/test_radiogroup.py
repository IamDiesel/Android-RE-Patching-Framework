# -*- coding: utf-8 -*-
"""AP-E14: RadioGroup/RadioButton — Single-Select innerhalb der Gruppe. Headless."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.view_tree import install_view_stubs, ViewTree, ViewNode, paint_commands
from core.java.objects import JObject


def _build_group():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    grp = JObject("Landroid/widget/RadioGroup;")
    stubs.resolve(grp.cls, "<init>", "(Landroid/content/Context;)V", [grp, None], False)
    rbs = []
    for i in range(3):
        rb = JObject("Landroid/widget/RadioButton;")
        stubs.resolve(rb.cls, "<init>", "(Landroid/content/Context;)V", [rb, None], False)
        stubs.resolve(grp.cls, "addView", "(Landroid/view/View;)V", [grp, rb], False)
        rbs.append(rb.fields["$view"])
    return grp.fields["$view"], rbs


def test_single_select_within_group():
    grpnode, rbs = _build_group()
    assert grpnode.cls == "RadioGroup" and len(grpnode.children) == 3
    rbs[0].on_click()
    assert [bool(r.props.get("checked")) for r in rbs] == [True, False, False]
    rbs[2].on_click()                       # Auswahl wechselt -> vorige wird abgewaehlt
    assert [bool(r.props.get("checked")) for r in rbs] == [False, False, True]


def test_radio_render_oval_dot_when_checked():
    grpnode, rbs = _build_group()
    rbs[1].on_click()
    t = ViewTree(); t.set_root(grpnode)
    ovals = [c for c in paint_commands(t) if c["op"] == "oval"]
    # 3 Ringe + 1 gefuellter Punkt (ausgewaehlt) = 4 Ovale, genau einer in Akzentfarbe
    assert len(ovals) == 4
    assert sum(1 for c in ovals if c["fill"] == "#2f6df0") == 1


def test_radio_without_group_selfselect():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    rb = JObject("Landroid/widget/RadioButton;")
    stubs.resolve(rb.cls, "<init>", "()V", [rb], False)
    node = rb.fields["$view"]
    node.on_click()                         # ohne Gruppe -> selbst aktivieren
    assert node.props["checked"] is True
