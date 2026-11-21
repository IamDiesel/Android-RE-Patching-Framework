# -*- coding: utf-8 -*-
"""AP-E7: findViewById(R.id.x) liefert den echten Knoten; setText darauf aktualisiert ihn. Headless."""
from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.dex_loader import AndroidStubs
from core.java.lifecycle import _StubRuntime
from core.java.view_tree import install_view_stubs
from core.java.objects import JObject

LAYOUT = """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">
    <TextView android:id="@+id/value" android:text="--" />
</LinearLayout>
"""

SRC = """.class public Lc/A;
.super Landroid/app/Activity;
.method public onCreate(Landroid/os/Bundle;)V
    .locals 3
    const v0, 0x7f0b0001
    invoke-virtual {p0, v0}, Lc/A;->setContentView(I)V
    const v0, 0x7f080005
    invoke-virtual {p0, v0}, Lc/A;->findViewById(I)Landroid/view/View;
    move-result-object v0
    const-string v1, "112 mg/dL"
    invoke-virtual {v0, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V
    return-void
.end method
"""

def test_findviewbyid_updates_node(tmp_path):
    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "layout").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="layout" name="main" id="0x7f0b0001" />'
        '<public type="id" name="value" id="0x7f080005" />'
        '</resources>')
    (tmp_path / "res" / "layout" / "main.xml").write_text(LAYOUT)
    vm = DalvikVM(); _, mds = Sm.parse_class(SRC)
    for md in mds: vm.register(md)
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs, app_root=str(tmp_path))
    vm.runtime = _StubRuntime(stubs)
    act = JObject("Lc/A;")
    vm.invoke("Lc/A;", "onCreate", "(Landroid/os/Bundle;)V", (act, None))
    val = tree.root.find("value")
    assert val is not None and val.text == "112 mg/dL"   # per findViewById geaendert
