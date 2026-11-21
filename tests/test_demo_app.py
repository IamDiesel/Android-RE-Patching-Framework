# -*- coding: utf-8 -*-
"""AP-E2: Beispiel-App fuehrt echten onCreate aus, baut View-Baum, Klick aktualisiert ihn. Headless."""
from core.java.demo_app import build_demo_tree, ENTRY
from core.java.objects import JObject

def test_onCreate_builds_tree():
    tree, vm, act = build_demo_tree()
    assert tree.root is not None and tree.root.cls == "LinearLayout"
    texts = [n.text for n in tree.nodes()]
    assert "Beispiel-App" in texts
    assert any((t or "").startswith("Wert") for t in texts)
    assert any(n.cls == "Button" and n.text == "Messen" for n in tree.nodes())

def test_button_listener_is_activity():
    tree, vm, act = build_demo_tree()
    btn = [n for n in tree.nodes() if n.cls == "Button"][0]
    assert btn.on_click is act and btn.clickable is True

def test_click_updates_value():
    tree, vm, act = build_demo_tree()
    val = [n for n in tree.nodes() if (n.text or "").startswith("Wert")][0]
    assert val.text == "Wert: --"
    view = JObject("Landroid/view/View;")
    vm.invoke(ENTRY, "onClick", "(Landroid/view/View;)V", (act, view))
    assert val.text == "Wert: 112 mg/dL"
