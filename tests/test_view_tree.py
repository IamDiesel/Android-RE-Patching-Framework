# -*- coding: utf-8 -*-
"""AP-U1: ViewTree-Modell (Layout/hit_test) + Recorder-Stubs. Headless."""
from core.java.view_tree import ViewNode, ViewTree, demo_screen, install_view_stubs
from core.java.dex_loader import AndroidStubs
from core.java.objects import JObject, JString

def test_layout_vertical_stack():
    t = demo_screen()
    root = t.root
    assert root.bounds[0] == 0 and root.bounds[2] == t.width
    kids = root.children
    # vertikal gestapelt, aufsteigende y, gleiche Breite
    ys = [c.bounds[1] for c in kids]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)
    assert all(c.bounds[2] == root.bounds[2] - 20 for c in kids)   # minus 2*PAD(10)

def test_find_and_walk():
    t = demo_screen()
    assert t.root.find("refresh").cls == "Button"
    assert t.root.find("nope") is None
    assert len(t.nodes()) == 5   # root + 4 Kinder

def test_hit_test_and_clickable():
    t = demo_screen()
    btn = t.root.find("refresh")
    bx, by, bw, bh = btn.bounds
    cx, cy = bx + bw // 2, by + bh // 2
    assert t.node_at(cx, cy) is btn
    assert t.clickable_at(cx, cy) is btn
    # Titel ist nicht klickbar
    tt = t.root.find("title")
    tx, ty, tw, th = tt.bounds
    assert t.clickable_at(tx + 2, ty + 2) is None

def test_recorder_builds_tree():
    stubs = AndroidStubs()
    tree = install_view_stubs(stubs)
    act = JObject("Lcom/app/MainActivity;")
    layout = JObject("Landroid/widget/LinearLayout;")
    btn = JObject("Landroid/widget/Button;")
    listener = JObject("Lcom/app/MainActivity$1;")
    # new LinearLayout(ctx); new Button(ctx)
    assert stubs.resolve("Landroid/widget/LinearLayout;", "<init>", "(Landroid/content/Context;)V", [layout], False)[0]
    stubs.resolve("Landroid/widget/Button;", "<init>", "(Landroid/content/Context;)V", [btn], False)
    # btn.setText("Klick") -- an App-fremder Klasse, by_name
    stubs.resolve("Landroid/widget/Button;", "setText", "(Ljava/lang/CharSequence;)V", [btn, JString("Klick")], False)
    # btn.setOnClickListener(listener)
    stubs.resolve("Landroid/widget/Button;", "setOnClickListener", "(Landroid/view/View$OnClickListener;)V", [btn, listener], False)
    # layout.addView(btn)
    stubs.resolve("Landroid/widget/LinearLayout;", "addView", "(Landroid/view/View;)V", [layout, btn], False)
    # activity.setContentView(layout) -- Empfaenger ist App-Activity (by_name!)
    stubs.resolve("Lcom/app/MainActivity;", "setContentView", "(Landroid/view/View;)V", [act, layout], False)

    assert tree.root is not None and tree.root.cls == "LinearLayout"
    assert len(tree.root.children) == 1
    b = tree.root.children[0]
    assert b.cls == "Button" and b.text == "Klick" and b.clickable is True
    assert b.on_click is listener

def test_paint_commands():
    from core.java.view_tree import paint_commands, demo_screen
    cmds = paint_commands(demo_screen())
    ops = [c["op"] for c in cmds]
    assert "rect" in ops and "text" in ops
    # Button liefert Rect(blau) + zentrierten Text
    btn_rects = [c for c in cmds if c["op"] == "rect" and c.get("fill") == "#2f6df0"]
    assert len(btn_rects) == 1
    txt = [c for c in cmds if c["op"] == "text" and c["text"] == "Aktualisieren"]
    assert txt and txt[0]["anchor"] == "c"

def test_paint_empty():
    from core.java.view_tree import paint_commands, ViewTree
    assert paint_commands(ViewTree()) == []
    assert paint_commands(None) == []

def test_demo_button_callback_effect():
    from core.java.view_tree import demo_screen
    t = demo_screen()
    btn = t.root.find("refresh")
    val = t.root.find("value")
    assert callable(btn.on_click)
    btn.on_click(btn)
    assert "mg/dL" in val.text and val.text != "-- mg/dL"

def test_edittext_focusable_text_injection():
    from core.java.view_tree import demo_screen
    t = demo_screen()
    note = t.root.find("note")
    assert note.focusable is True
    # simuliert, was _on_key tut
    note.text += "A"; note.text += "b"
    t.touch()
    assert note.text == "Ab"

def test_view_stub_breadth():
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.objects import JObject
    SRC = """.class public Lc/E;
.super Landroid/app/Activity;
.method public go()V
    .locals 3
    new-instance v0, Landroid/widget/TextView;
    invoke-direct {v0}, Landroid/widget/TextView;-><init>()V
    const-string v1, "Tippe..."
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setHint(Ljava/lang/CharSequence;)V
    const/16 v1, 0x8
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setVisibility(I)V
    invoke-virtual {p0, v0}, Lc/E;->setContentView(Landroid/view/View;)V
    return-void
.end method
"""
    vm = DalvikVM(); _, mds = Sm.parse_class(SRC)
    for md in mds: vm.register(md)
    stubs = AndroidStubs(vm=vm); tree = install_view_stubs(stubs); vm.runtime = _StubRuntime(stubs)
    act = JObject("Lc/E;")
    vm.invoke("Lc/E;", "go", "()V", (act,))
    assert tree.root is not None and tree.root.cls == "TextView"
    assert tree.root.props.get("hint") == "Tippe..."
    assert tree.root.visible is False     # GONE

def test_horizontal_layout():
    from core.java.view_tree import ViewNode, ViewTree
    root = ViewNode("LinearLayout")
    root.props["orientation"] = "horizontal"
    a = ViewNode("Button", text="A"); b = ViewNode("Button", text="B")
    root.add(a); root.add(b)
    t = ViewTree(width=200); t.set_root(root)
    # nebeneinander: gleiche y, aufsteigende x, je ~halbe Breite
    assert a.bounds[1] == b.bounds[1]
    assert a.bounds[0] < b.bounds[0]
    assert abs(a.bounds[2] - b.bounds[2]) <= 2
    # Reihenhoehe ~ Zeilenhoehe + Padding, nicht Summe
    assert root.bounds[3] < 120

def test_vertical_still_default():
    from core.java.view_tree import ViewNode, ViewTree
    root = ViewNode("LinearLayout")   # ohne orientation -> vertikal
    a = ViewNode("Button", text="A"); b = ViewNode("Button", text="B")
    root.add(a); root.add(b)
    t = ViewTree(width=200); t.set_root(root)
    assert a.bounds[1] < b.bounds[1]
    assert a.bounds[0] == b.bounds[0]
