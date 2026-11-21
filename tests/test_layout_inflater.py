# -*- coding: utf-8 -*-
"""AP-E6: XML-Layout-Inflation (parse + Resource-Map + inflate). Headless."""
import os
from core.java.layout_inflater import parse_layout_xml, build_resmap, inflate_res

LAYOUT = """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:orientation="vertical">
    <TextView android:id="@+id/title" android:text="Glukose" />
    <TextView android:id="@+id/value" android:text="--" android:visibility="gone" />
    <EditText android:id="@+id/note" android:hint="Notiz" />
    <Button android:id="@+id/go" android:text="Messen" android:onClick="onGo" />
</LinearLayout>
"""

def test_parse_layout():
    root = parse_layout_xml(LAYOUT)
    assert root is not None and root.cls == "LinearLayout"
    assert root.props.get("orientation") == "vertical"
    kids = root.children
    assert [k.cls for k in kids] == ["TextView", "TextView", "EditText", "Button"]
    assert root.find("title").text == "Glukose"
    assert root.find("value").visible is False          # gone
    assert root.find("note").focusable is True and root.find("note").props.get("hint") == "Notiz"
    btn = root.find("go")
    assert btn.clickable is True and btn.props.get("onClick") == "onGo"

def test_resmap_and_inflate(tmp_path):
    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "layout").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources><public type="layout" name="activity_main" id="0x7f0b001c" />'
        '<public type="id" name="title" id="0x7f080000" /></resources>')
    (tmp_path / "res" / "layout" / "activity_main.xml").write_text(LAYOUT)
    idmap = build_resmap(str(tmp_path))
    assert idmap.get(0x7f0b001c) == "activity_main"
    node = inflate_res(str(tmp_path), 0x7f0b001c, idmap)
    assert node is not None and node.find("go").text == "Messen"
    assert inflate_res(str(tmp_path), 0xdeadbeef, idmap) is None

def test_setcontentview_res_via_stubs(tmp_path):
    from core.java.dalvik import DalvikVM
    from core.java import smali as Sm
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs
    from core.java.objects import JObject
    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "layout").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources><public type="layout" name="main" id="0x7f0b0001" /></resources>')
    (tmp_path / "res" / "layout" / "main.xml").write_text(LAYOUT)
    SRC = """.class public Lc/A;
.super Landroid/app/Activity;
.method public onCreate(Landroid/os/Bundle;)V
    .locals 1
    const v0, 0x7f0b0001
    invoke-virtual {p0, v0}, Lc/A;->setContentView(I)V
    return-void
.end method
"""
    vm = DalvikVM(); _, mds = Sm.parse_class(SRC)
    for md in mds: vm.register(md)
    stubs = AndroidStubs(vm=vm)
    tree = install_view_stubs(stubs, app_root=str(tmp_path))
    vm.runtime = _StubRuntime(stubs)
    act = JObject("Lc/A;")
    vm.invoke("Lc/A;", "onCreate", "(Landroid/os/Bundle;)V", (act, None))
    assert tree.root is not None and tree.root.cls == "LinearLayout"
    assert tree.root.find("go").text == "Messen"
