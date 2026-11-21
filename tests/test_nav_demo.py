# -*- coding: utf-8 -*-
"""AP-NAV: Zwei-Screen-Navigation via startActivity. Headless."""
from core.java.demo_app import build_nav_demo
from core.java.objects import JObject

def test_navigates_to_second_screen():
    tree, vm, main, nav = build_nav_demo()
    assert any(n.text == "Startseite" for n in tree.nodes())
    assert not any(n.text == "Zweiter Screen" for n in tree.nodes())
    view = JObject("Landroid/view/View;")
    vm.invoke("Lcom/demo/Main2;", "onClick", "(Landroid/view/View;)V", (main, view))
    assert any(n.text == "Zweiter Screen" for n in tree.nodes())
    assert not any(n.text == "Startseite" for n in tree.nodes())

def test_back_stack():
    tree, vm, main, nav = build_nav_demo()
    assert any(n.text == "Startseite" for n in tree.nodes())
    vm.invoke("Lcom/demo/Main2;", "onClick", "(Landroid/view/View;)V", (main, JObject("Landroid/view/View;")))
    assert any(n.text == "Zweiter Screen" for n in tree.nodes())
    assert nav.back() is True
    assert any(n.text == "Startseite" for n in tree.nodes())
    assert nav.back() is False
