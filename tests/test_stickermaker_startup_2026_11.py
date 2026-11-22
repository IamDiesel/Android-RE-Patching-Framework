# -*- coding: utf-8 -*-
"""Regressionstests fuer com.marsvard.stickermakerforwhatsapp (Sticker Maker) Startup, UI, Dialog & Realm."""
from __future__ import annotations

import os
import pytest

from core.java.dalvik import DalvikVM, MethodDef
from core.java.dex_loader import AndroidStubs
from core.java.engine_stubs_g14 import attach_g14_vm_hook
from core.java.glass_runner import GlassAppRunner, InstanceState
from core.java.layout_inflater import inflate_res
from core.java.lifecycle import LifecycleRunner
from core.java.manifest import parse_manifest_text
from core.java.objects import JObject, JString
from core.java.seam_config import SeamConfig
from core.java.surface import SceneGraphSurface
from core.java.view_tree import ViewNode, ViewTree, install_view_stubs


STICKERMAKER_ROOT = os.path.join(
    "source", "com.marsvard.stickermakerforwhatsapp", "base_unpacked_apktool"
)


def _find_node(node, pred):
    if node is None:
        return None
    if pred(node):
        return node
    for ch in getattr(node, "children", ()) or ():
        found = _find_node(ch, pred)
        if found is not None:
            return found
    return None


def _collect_nodes(node, pred=None):
    out = []
    if node is None:
        return out
    if pred is None or pred(node):
        out.append(node)
    for ch in getattr(node, "children", ()) or ():
        out.extend(_collect_nodes(ch, pred))
    return out


def _visible_texts(root):
    out = []
    if root is None or not getattr(root, "visible", True):
        return out
    t = str(getattr(root, "text", "") or "").strip()
    if t:
        out.append((root, t))
    for ch in getattr(root, "children", ()) or ():
        out.extend(_visible_texts(ch))
    return out


def test_string_and_pattern_split_negative_limit():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = stubs

    # Dalvik gibt int -1 teils als unsigned 32-bit (0xFFFFFFFF) oder sign-extended 64-bit weiter.
    for neg_limit in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        ok, arr = stubs.resolve(
            "Ljava/lang/String;",
            "split",
            "(Ljava/lang/String;I)[Ljava/lang/String;",
            [JString("a,b,,"), JString(","), neg_limit],
            False,
        )
        assert ok
        assert [s.text for s in arr.data] == ["a", "b", "", ""]

    ok, arr_zero = stubs.resolve(
        "Ljava/lang/String;",
        "split",
        "(Ljava/lang/String;I)[Ljava/lang/String;",
        [JString("a,b,,"), JString(","), 0],
        False,
    )
    assert ok
    assert [s.text for s in arr_zero.data] == ["a", "b"]

    ok, pat = stubs.resolve(
        "Ljava/util/regex/Pattern;",
        "compile",
        "(Ljava/lang/String;)Ljava/util/regex/Pattern;",
        [JString(",")],
        True,
    )
    assert ok
    for neg_limit in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        ok, parr = stubs.resolve(
            "Ljava/util/regex/Pattern;",
            "split",
            "(Ljava/lang/CharSequence;I)[Ljava/lang/String;",
            [pat, JString("x,y,,"), neg_limit],
            False,
        )
        assert ok
        assert [s.text for s in parr.data] == ["x", "y", "", ""]

    ok, parr_zero = stubs.resolve(
        "Ljava/util/regex/Pattern;",
        "split",
        "(Ljava/lang/CharSequence;I)[Ljava/lang/String;",
        [pat, JString("x,y,,"), 0],
        False,
    )
    assert ok
    assert [s.text for s in parr_zero.data] == ["x", "y"]


def test_manifest_provider_metadata_and_pre_app_oncreate_bootstrap(tmp_path):
    xml = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.bootstrap">
    <application android:name=".DemoApplication">
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <service android:name="com.example.ComponentDiscoveryService">
            <meta-data android:name="com.example.Registrar" android:value="com.example.Init" />
        </service>
        <provider
            android:name="com.example.DemoInitProvider"
            android:authorities="com.example.bootstrap.demoinitprovider"
            android:exported="false"
            android:initOrder="100">
            <meta-data android:name="provider_key" android:value="provider_val" />
        </provider>
    </application>
</manifest>"""
    man = parse_manifest_text(xml)
    assert len(man.services) == 1
    assert man.services[0]["meta_data"] == {"com.example.Registrar": "com.example.Init"}
    assert len(man.providers) == 1
    assert man.providers[0]["init_order"] == 100
    assert man.providers[0]["meta_data"] == {"provider_key": "provider_val"}
    (tmp_path / "AndroidManifest.xml").write_text(xml, encoding="utf-8")

    call_log = []

    class _Provider:
        def get_class(self, desc):
            return [m for (c, _, _), m in vm.registry.items() if c == desc]

    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = stubs
    install_view_stubs(stubs)

    vm.method_overrides[("Lcom/example/Probe;", "mark", "(Ljava/lang/String;)V")] = (
        lambda a: call_log.append(a[0].text if a and hasattr(a[0], "text") else str(a[0]))
    )

    for cls, name, sig, tag, ret_bool in (
        ("Lcom/example/DemoInitProvider;", "<init>", "()V", "prov_init", False),
        ("Lcom/example/DemoInitProvider;", "onCreate", "()Z", "prov_oncreate", True),
        ("Lcom/example/bootstrap/DemoApplication;", "<init>", "()V", "app_init", False),
        ("Lcom/example/bootstrap/DemoApplication;", "onCreate", "()V", "app_oncreate", False),
        ("Lcom/example/bootstrap/MainActivity;", "<init>", "()V", "act_init", False),
    ):
        insns = [
            ("const-string", 0, tag),
            ("invoke-static", (0,), "Lcom/example/Probe;", "mark", "(Ljava/lang/String;)V"),
        ]
        if ret_bool:
            insns.extend([("const/4", 0, 1), ("return", 0)])
        else:
            insns.append(("return-void",))
        md = MethodDef(cls=cls, name=name, sig=sig, insns=insns, nregs=2, nargs=1)
        vm.register(md)

    runner = LifecycleRunner(vm, provider=_Provider(), with_stubs=False, stubs=stubs)
    report = runner.run_app(studio_root=str(tmp_path))
    assert report["package"] == "com.example.bootstrap"
    assert "prov_oncreate" in call_log and "app_oncreate" in call_log
    assert call_log.index("prov_oncreate") < call_log.index("app_oncreate")


def test_bottom_nav_menu_expansion_and_modal_alert_dialog_layout(tmp_path):
    res_dir = tmp_path / "res"
    (res_dir / "values").mkdir(parents=True)
    (res_dir / "menu").mkdir(parents=True)
    (res_dir / "layout").mkdir(parents=True)

    (res_dir / "values" / "public.xml").write_text(
        """<resources>
  <public type="layout" name="activity_demo" id="0x7f0c0001" />
  <public type="menu" name="bottom_nav" id="0x7f0d0001" />
  <public type="id" name="tab_one" id="0x7f0a0001" />
  <public type="id" name="tab_two" id="0x7f0a0002" />
  <public type="id" name="container" id="0x7f0a0003" />
  <public type="id" name="navigationBar" id="0x7f0a0004" />
  <public type="string" name="tab_one_lbl" id="0x7f110001" />
  <public type="string" name="tab_two_lbl" id="0x7f110002" />
</resources>""",
        encoding="utf-8",
    )
    (res_dir / "values" / "strings.xml").write_text(
        """<resources>
  <string name="tab_one_lbl">Tab One</string>
  <string name="tab_two_lbl">Tab Two</string>
</resources>""",
        encoding="utf-8",
    )
    (res_dir / "menu" / "bottom_nav.xml").write_text(
        """<menu xmlns:android="http://schemas.android.com/apk/res/android">
  <item android:id="@id/tab_one" android:title="@string/tab_one_lbl" />
  <item android:id="@id/tab_two" android:title="@string/tab_two_lbl" />
</menu>""",
        encoding="utf-8",
    )
    (res_dir / "layout" / "activity_demo.xml").write_text(
        """<androidx.constraintlayout.widget.ConstraintLayout xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto"
    android:layout_width="match_parent"
    android:layout_height="match_parent">
  <FrameLayout android:id="@id/container"
      android:layout_width="0dp"
      android:layout_height="0dp"
      app:layout_constraintTop_toTopOf="parent"
      app:layout_constraintBottom_toTopOf="@id/navigationBar" />
  <com.google.android.material.bottomnavigation.BottomNavigationView
      android:id="@id/navigationBar"
      android:layout_width="match_parent"
      android:layout_height="wrap_content"
      app:menu="@menu/bottom_nav"
      app:layout_constraintBottom_toBottomOf="parent" />
</androidx.constraintlayout.widget.ConstraintLayout>""",
        encoding="utf-8",
    )

    root = inflate_res(str(tmp_path), 0x7F0C0001)
    assert root is not None
    navbar = _find_node(root, lambda n: n.vid == "navigationBar")
    assert navbar is not None
    assert navbar.props.get("role") == "navbar"
    assert [c.text for c in navbar.children] == ["Tab One", "Tab Two"]
    assert all(c.props.get("role") == "navitem" for c in navbar.children)

    container = _find_node(root, lambda n: n.vid == "container")
    container.add(ViewNode(cls="TextView", vid="pageText", text="Hello Fragment"))

    dialog = ViewNode(cls="LinearLayout", vid="parentPanel", props={"role": "dialog"})
    custom_panel = ViewNode(cls="FrameLayout", vid="custom")
    custom_panel.add(ViewNode(cls="EditText", vid="packName", text=""))
    dialog.add(custom_panel)
    btn = ViewNode(cls="Button", vid="button1", text="Create", clickable=True)
    dialog.add(btn)
    root.add(dialog)

    tree = ViewTree(width=420, height=760)
    tree.set_root(root)

    _, cy, cw, ch = container.bounds
    assert cw >= 380 and ch >= 500
    dx, dy, dw, dh = dialog.bounds
    assert 0 < dx < 120 and 0 < dy < 500
    assert 200 <= dw <= 400 and dh > 40
    # Modal dialog darf container nicht verdrängen (container bleibt oberhalb navbar)
    _, ny, _, _ = navbar.bounds
    assert cy + ch <= ny


def test_realm_live_results_refresh_on_insert_and_delete():
    vm = DalvikVM()
    stubs = AndroidStubs(vm=vm)
    vm.runtime = stubs
    attach_g14_vm_hook(stubs, vm)

    realm = vm.run_method(
        [
            ("invoke-static", (), "Lio/realm/i0;", "w", "()Lio/realm/i0;"),
            ("move-result-object", 0),
            ("return-object", 0),
        ],
        nregs=1,
    )
    assert isinstance(realm, JObject)
    pack_cls = JObject("Ljava/lang/Class;", {"name": "com.example.Pack"})
    rq = vm.invoke_virtual(realm, "N", "(Ljava/lang/Class;)Lio/realm/RealmQuery;", (realm, pack_cls))
    rr = vm.invoke_virtual(rq, "f", "()Lio/realm/y0;", (rq,))

    assert vm.invoke_virtual(rr, "size", "()I", (rr,)) == 0
    assert vm.invoke_virtual(rr, "isEmpty", "()Z", (rr,)) == 1

    item = JObject("Lcom/example/Pack;", {"name": JString("Alpha Pack"), "publisher": JString("Alice")})
    vm.invoke_virtual(realm, "t", "(Lio/realm/t0;)Lio/realm/t0;", (realm, item))

    # Vorab abgefragte RealmResults muss sich automatisch live aktualisieren
    assert vm.invoke_virtual(rr, "size", "()I", (rr,)) == 1
    assert vm.invoke_virtual(rr, "isEmpty", "()Z", (rr,)) == 0
    got = vm.invoke_virtual(rr, "get", "(I)Ljava/lang/Object;", (rr, 0))
    assert got is item

    # Filter-Query auf bestehenden RealmResults
    fq = JObject("Lio/realm/RealmQuery;", {})
    vm.invoke_virtual(fq, "<init>", "(Lio/realm/y0;Ljava/lang/Class;)V", (fq, rr, pack_cls))
    vm.invoke_virtual(fq, "a", "(Ljava/lang/String;Ljava/lang/String;)Lio/realm/RealmQuery;", (fq, JString("name"), JString("Alpha")))
    filtered_rr = vm.invoke_virtual(fq, "f", "()Lio/realm/y0;", (fq,))
    assert vm.invoke_virtual(filtered_rr, "size", "()I", (filtered_rr,)) == 1

    # Loeschen aktualisiert sowohl Basis- als auch gefilterte Live-Results
    vm.run_method(
        [
            ("invoke-static", (0,), "Lio/realm/i0;", "q", "(Lio/realm/t0;)V"),
            ("return-void",),
        ],
        nregs=1,
        args=[item],
    )
    assert vm.invoke_virtual(rr, "size", "()I", (rr,)) == 0
    assert vm.invoke_virtual(filtered_rr, "size", "()I", (filtered_rr,)) == 0


@pytest.mark.skipif(not os.path.isdir(STICKERMAKER_ROOT), reason="Sticker Maker APK unpack not present")
def test_stickermaker_full_startup_tabs_and_create_pack_e2e():
    sc = SeamConfig.from_profile("self_cache")
    sc.persist_appdata = False
    inst = InstanceState(
        package="com.marsvard.stickermakerforwhatsapp",
        app_root=STICKERMAKER_ROOT,
        zone="original",
        apply_patches=False,
        net_mode="mock",
    )
    gr = GlassAppRunner(STICKERMAKER_ROOT, zone="original", seam_config=sc, instance_state=inst)
    gr.build(
        native_fusion=True,
        net_mode="mock",
        tolerant=True,
        step_budget=4_000_000,
        apply_overlays=False,
        fallback_java_on_fusion_err=True,
    )
    gr.run(max_steps=4_000_000, max_wall_s=30.0, native_fusion=True)

    assert gr.tree is not None and gr.tree.root is not None
    surf = SceneGraphSurface(gr.tree, vm=gr.vm, activity=gr.stubs._current_activity)

    def _tap(vid):
        node = _find_node(gr.tree.root, lambda n: n.vid == vid)
        assert node is not None, f"View nicht gefunden: {vid}"
        bx, by, bw, bh = node.bounds
        return surf.dispatch({"type": "tap", "x": bx + max(1, bw // 2), "y": by + max(1, bh // 2)})

    # 1. Branded Toolbar + Optionsmenü + Container + BottomNavigationView
    assert _find_node(gr.tree.root, lambda n: str(getattr(n, "text", "") or "").upper() == "STICKER MAKER") is not None
    for mid in ("action_about", "action_settings", "action_search"):
        mnode = _find_node(gr.tree.root, lambda n, _m=mid: n.vid == _m)
        assert mnode is not None and bool(mnode.visible) is True

    container = _find_node(gr.tree.root, lambda n: n.vid == "container")
    navbar = _find_node(gr.tree.root, lambda n: n.vid == "navigationBar")
    assert container is not None and navbar is not None
    assert container.bounds[2] >= 300 and container.bounds[3] >= 400
    assert container.bounds[1] + container.bounds[3] <= navbar.bounds[1]

    tab_my = _find_node(gr.tree.root, lambda n: n.vid == "my_stickers")
    tab_comm = _find_node(gr.tree.root, lambda n: n.vid == "community_stickers")
    assert tab_my is not None and tab_my.text == "My stickers"
    assert tab_comm is not None and tab_comm.text == "Community"
    assert bool(tab_comm.props.get("selected")) is True
    assert _find_node(gr.tree.root, lambda n: n.vid == "pullToRefresh") is not None

    # 2. Tab-Wechsel zu My stickers -> Leerzustand + Search ausgeblendet
    _tap("my_stickers")
    assert bool(_find_node(gr.tree.root, lambda n: n.vid == "my_stickers").props.get("selected")) is True
    assert bool(_find_node(gr.tree.root, lambda n: n.vid == "action_search").visible) is False
    assert _find_node(gr.tree.root, lambda n: "You don't have any stickers yet." in str(getattr(n, "text", "") or "")) is not None
    assert _find_node(gr.tree.root, lambda n: n.vid == "createNewStickerPackButton") is not None

    # 3. Dialog oeffnen -> Felder fuellen -> Create klicken -> Pack in Realm & UI sichtbar
    _tap("createNewStickerPackButton")
    dlg = _find_node(gr.tree.root, lambda n: n.props.get("role") == "dialog")
    assert dlg is not None and dlg.bounds[2] >= 200 and dlg.bounds[3] >= 80
    pn = _find_node(dlg, lambda n: n.vid == "packName")
    pa = _find_node(dlg, lambda n: n.vid == "stickerpackAuthor")
    btn_create = _find_node(dlg, lambda n: n.vid == "button1")
    btn_cancel = _find_node(dlg, lambda n: n.vid == "button2")
    assert pn is not None and pa is not None and btn_create is not None and btn_cancel is not None

    pn.text = "Test Pack"
    pa.text = "Test Author"
    if pn._jobj is not None:
        pn._jobj.fields["$text"] = "Test Pack"
    if pa._jobj is not None:
        pa._jobj.fields["$text"] = "Test Author"

    _tap("button1")

    assert _find_node(gr.tree.root, lambda n: n.props.get("role") == "dialog") is None
    packs = gr.stubs.__dict__.get("_realm_store", {}).get(
        "Lcom/marsvard/stickermakerforwhatsapp/architecture/LocalStickerpack;", []
    )
    assert len(packs) == 1
    assert getattr(packs[0].fields.get("name"), "text", packs[0].fields.get("name")) == "Test Pack"
    assert getattr(packs[0].fields.get("publisher"), "text", packs[0].fields.get("publisher")) == "Test Author"

    rv = _find_node(gr.tree.root, lambda n: n.vid == "stickerpacksContainer")
    assert rv is not None
    empty_view = _find_node(rv, lambda n: n.vid == "emptyView")
    assert empty_view is None or not bool(empty_view.visible)
    vis_txts = [t for _, t in _visible_texts(rv)]
    assert "Test Pack" in vis_txts
    assert "Test Author" in vis_txts

    # 4. Zurueck zu Community tab
    _tap("community_stickers")
    assert bool(_find_node(gr.tree.root, lambda n: n.vid == "community_stickers").props.get("selected")) is True
    assert _find_node(gr.tree.root, lambda n: n.vid == "pullToRefresh") is not None
