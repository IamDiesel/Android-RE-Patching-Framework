# -*- coding: utf-8 -*-
"""AP-E10: String-Ressourcen (@string, setText(resId), getString). Headless."""
from core.java.layout_inflater import inflate_res
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.view_tree import install_view_stubs
from core.java.objects import JObject, JString

def _mkapp(tmp_path):
    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "layout").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources>'
        '<public type="layout" name="main" id="0x7f0b0001" />'
        '<public type="id" name="t" id="0x7f080003" />'
        '<public type="string" name="hello" id="0x7f0f0002" />'
        '</resources>')
    (tmp_path / "res" / "values" / "strings.xml").write_text(
        '<resources><string name="hello">Hallo Welt</string></resources>')
    (tmp_path / "res" / "layout" / "main.xml").write_text(
        '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android">'
        '<TextView android:id="@+id/t" android:text="@string/hello" /></LinearLayout>')

def test_inflate_resolves_string_ref(tmp_path):
    _mkapp(tmp_path)
    node = inflate_res(str(tmp_path), 0x7f0b0001)
    assert node.find("t").text == "Hallo Welt"   # @string/hello aufgeloest

def test_settext_resid_and_getstring(tmp_path):
    _mkapp(tmp_path)
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs, app_root=str(tmp_path))
    tv = JObject("Landroid/widget/TextView;")
    stubs.resolve("Landroid/widget/TextView;", "setText", "(I)V", [tv, 0x7f0f0002], False)
    assert tv.fields["$view"].text == "Hallo Welt"
    handled, ret = stubs.resolve("Landroid/content/Context;", "getString",
                                 "(I)Ljava/lang/String;", [None, 0x7f0f0002], False)
    assert handled and isinstance(ret, JString) and ret.text == "Hallo Welt"

def test_unknown_string_fallback(tmp_path):
    _mkapp(tmp_path)
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs, app_root=str(tmp_path))
    tv = JObject("Landroid/widget/TextView;")
    stubs.resolve("Landroid/widget/TextView;", "setText", "(I)V", [tv, 0xdeadbeef], False)
    assert tv.fields["$view"].text == "res#3735928559"   # unbekannt -> Platzhalter
