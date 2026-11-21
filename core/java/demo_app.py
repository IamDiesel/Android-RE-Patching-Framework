# -*- coding: utf-8 -*-
"""core.java.demo_app — kleine, ECHTE Beispiel-App (klassische android.view-Hierarchie).

Zweck: den App-Start grafisch nachweisen. `build_demo_tree()` fuehrt onCreate dieser Smali-Activity
tatsaechlich in der DalvikVM aus; dabei zeichnet `install_view_stubs` den View-Baum auf (kein
Hardcoding). Der Button-Listener ist die Activity selbst; sein onClick aktualisiert ein TextView-Feld.
Zielagnostisch (nur android.widget/view), dient dem Anzeige-Reiter und den Tests.
"""
from __future__ import annotations

DEMO_SMALI = """.class public Lcom/demo/MainActivity;
.super Landroid/app/Activity;

.field private tv:Landroid/widget/TextView;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Activity;-><init>()V
    return-void
.end method

.method public onCreate(Landroid/os/Bundle;)V
    .locals 4
    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V
    new-instance v0, Landroid/widget/LinearLayout;
    invoke-direct {v0, p0}, Landroid/widget/LinearLayout;-><init>(Landroid/content/Context;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Beispiel-App"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Wert: --"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    iput-object v1, p0, Lcom/demo/MainActivity;->tv:Landroid/widget/TextView;
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/Button;
    invoke-direct {v1, p0}, Landroid/widget/Button;-><init>(Landroid/content/Context;)V
    const-string v2, "Messen"
    invoke-virtual {v1, v2}, Landroid/widget/Button;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v1, p0}, Landroid/widget/Button;->setOnClickListener(Landroid/view/View$OnClickListener;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    invoke-virtual {p0, v0}, Lcom/demo/MainActivity;->setContentView(Landroid/view/View;)V
    return-void
.end method

.method public onClick(Landroid/view/View;)V
    .locals 2
    iget-object v0, p0, Lcom/demo/MainActivity;->tv:Landroid/widget/TextView;
    const-string v1, "Wert: 112 mg/dL"
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    return-void
.end method
"""

ENTRY = "Lcom/demo/MainActivity;"


def build_demo_tree():
    """Fuehrt onCreate der Beispiel-Activity aus und liefert (ViewTree, vm, activity_jobject)."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.objects import JObject

    vm = DalvikVM()
    _, mds = Sm.parse_class(DEMO_SMALI)
    for md in mds:
        vm.register(md)
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs)
    try:
        stubs.seed_framework_statics(vm)
    except Exception:
        pass
    vm.runtime = _StubRuntime(stubs)
    vm.lenient = True
    activity = JObject(ENTRY)
    vm.invoke(ENTRY, "onCreate", "(Landroid/os/Bundle;)V", (activity, None))
    return tree, vm, activity


XML_DEMO = """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:orientation="vertical">
    <TextView android:id="@+id/title" android:text="Glukose (XML-Layout)" />
    <TextView android:id="@+id/value" android:text="Wert: --" />
    <EditText android:id="@+id/note" android:hint="Notiz eingeben..." />
    <Button android:id="@+id/go" android:text="Messen" />
</LinearLayout>
"""


def build_xml_demo_tree():
    """Inflatet ein echtes Android-XML-Layout und macht den Button interaktiv (Python-Callback)."""
    import itertools
    from core.java.layout_inflater import parse_layout_xml
    from core.java.view_tree import ViewTree
    root = parse_layout_xml(XML_DEMO)
    t = ViewTree()
    t.set_root(root)
    value = root.find("value")
    go = root.find("go")
    _seq = itertools.cycle([97, 112, 128, 143, 119])

    def _go(view=None):
        if value is not None:
            value.text = "Wert: %d mg/dL" % next(_seq)
    if go is not None:
        go.on_click = _go
    return t


NAV_MAIN_SMALI = """.class public Lcom/demo/Main2;
.super Landroid/app/Activity;
.method public onCreate(Landroid/os/Bundle;)V
    .locals 3
    new-instance v0, Landroid/widget/LinearLayout;
    invoke-direct {v0, p0}, Landroid/widget/LinearLayout;-><init>(Landroid/content/Context;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Startseite"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    new-instance v1, Landroid/widget/Button;
    invoke-direct {v1, p0}, Landroid/widget/Button;-><init>(Landroid/content/Context;)V
    const-string v2, "Weiter"
    invoke-virtual {v1, v2}, Landroid/widget/Button;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v1, p0}, Landroid/widget/Button;->setOnClickListener(Landroid/view/View$OnClickListener;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    invoke-virtual {p0, v0}, Lcom/demo/Main2;->setContentView(Landroid/view/View;)V
    return-void
.end method
.method public onClick(Landroid/view/View;)V
    .locals 3
    new-instance v0, Landroid/content/Intent;
    const-class v1, Lcom/demo/Second;
    invoke-direct {v0, p0, v1}, Landroid/content/Intent;-><init>(Landroid/content/Context;Ljava/lang/Class;)V
    invoke-virtual {p0, v0}, Lcom/demo/Main2;->startActivity(Landroid/content/Intent;)V
    return-void
.end method
"""

NAV_SECOND_SMALI = """.class public Lcom/demo/Second;
.super Landroid/app/Activity;
.method public onCreate(Landroid/os/Bundle;)V
    .locals 3
    new-instance v0, Landroid/widget/LinearLayout;
    invoke-direct {v0, p0}, Landroid/widget/LinearLayout;-><init>(Landroid/content/Context;)V
    new-instance v1, Landroid/widget/TextView;
    invoke-direct {v1, p0}, Landroid/widget/TextView;-><init>(Landroid/content/Context;)V
    const-string v2, "Zweiter Screen"
    invoke-virtual {v1, v2}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    invoke-virtual {v0, v1}, Landroid/widget/LinearLayout;->addView(Landroid/view/View;)V
    invoke-virtual {p0, v0}, Lcom/demo/Second;->setContentView(Landroid/view/View;)V
    return-void
.end method
"""


def build_nav_demo():
    """Zwei-Screen-App: Button 'Weiter' -> startActivity -> zweiter Screen (echte Navigation)."""
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.objects import JObject

    vm = DalvikVM()
    for src in (NAV_MAIN_SMALI, NAV_SECOND_SMALI):
        _, mds = Sm.parse_class(src)
        for md in mds:
            vm.register(md)
    stubs = AndroidStubs(vm=vm)
    from core.java.view_tree import ViewTree, NavController
    _pretree = ViewTree()
    nav = NavController(vm, _pretree)
    tree = install_view_stubs(stubs, on_navigate=nav.navigate)
    nav.tree = tree
    vm.runtime = _StubRuntime(stubs)
    vm.lenient = True
    nav.set_initial("Lcom/demo/Main2;")
    main = JObject("Lcom/demo/Main2;")
    vm.invoke("Lcom/demo/Main2;", "onCreate", "(Landroid/os/Bundle;)V", (main, None))
    return tree, vm, main, nav
