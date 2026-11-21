# -*- coding: utf-8 -*-
"""AP-E15: ImageView rendert eine beschriftete Platzhalter-Box; setImageResource loest Drawable-Namen. Headless."""
from core.java.dalvik import DalvikVM
from core.java.dex_loader import AndroidStubs
from core.java.view_tree import install_view_stubs, ViewTree, ViewNode, paint_commands
from core.java.objects import JObject


def _render(node):
    t = ViewTree(); root = ViewNode("LinearLayout"); root.add(node); t.set_root(root)
    return paint_commands(t)


def test_imageview_placeholder_box_and_label():
    node = ViewNode("ImageView", text="")
    node.props["desc"] = "Logo"
    cmds = _render(node)
    assert any(c["op"] == "rect" and c["fill"] == "#eef1f6" for c in cmds)   # Platzhalter-Box
    assert any(c["op"] == "text" and c["text"] == "Logo" for c in cmds)       # Label (contentDescription)


def test_set_image_resource_without_map():
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm); install_view_stubs(stubs)
    iv = JObject("Landroid/widget/ImageView;")
    stubs.resolve(iv.cls, "<init>", "(Landroid/content/Context;)V", [iv, None], False)
    stubs.resolve("x", "setImageResource", "(I)V", [iv, 0x7f020001], False)
    assert iv.fields["$view"].props["img"] == "img#2130837505"   # ohne Map -> Platzhalter-ID


def test_set_image_resource_with_drawable_map(tmp_path):
    (tmp_path / "res" / "values").mkdir(parents=True)
    (tmp_path / "res" / "values" / "public.xml").write_text(
        '<resources><public type="drawable" name="ic_logo" id="0x7f020001" /></resources>')
    vm = DalvikVM(); stubs = AndroidStubs(vm=vm)
    install_view_stubs(stubs, app_root=str(tmp_path))
    iv = JObject("Landroid/widget/ImageView;")
    stubs.resolve(iv.cls, "<init>", "(Landroid/content/Context;)V", [iv, None], False)
    stubs.resolve("x", "setImageResource", "(I)V", [iv, 0x7f020001], False)
    node = iv.fields["$view"]
    assert node.props["img"] == "ic_logo"                        # echter Drawable-Name
    cmds = _render(node)
    assert any(c["op"] == "text" and c["text"] == "ic_logo" for c in cmds)
