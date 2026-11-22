# -*- coding: utf-8 -*-
"""Regressionstests fuer BabyCam-Integritaets-/String-/PackageManager-Stubs & APKEditor-Smali-Layout."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_string_comparison_replace_and_installer_origin_stubs():
    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM, JObject, JString
    from core.java.dex_loader import AndroidStubs
    from core.java.objects import JString as _JS

    vm = DalvikVM()
    vm.lenient = True
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)

    # 1) String-Methoden direkt auf AndroidStubs
    s_pkg = JString("com.arjonasoftware.babycam")
    _, eq_ok = stubs.resolve("Ljava/lang/String;", "equals", "(Ljava/lang/Object;)Z", [s_pkg, JString("com.arjonasoftware.babycam")], False)
    _, ct_ok = stubs.resolve("Ljava/lang/String;", "contains", "(Ljava/lang/CharSequence;)Z", [s_pkg, JString("babycam")], False)
    _, ct_no = stubs.resolve("Ljava/lang/String;", "contains", "(Ljava/lang/CharSequence;)Z", [s_pkg, JString("other")], False)
    _, mt_ok = stubs.resolve("Ljava/lang/String;", "matches", "(Ljava/lang/String;)Z", [s_pkg, JString(r"com\.arjonasoftware\.[a-z]+")], False)
    _, ce_ok = stubs.resolve("Ljava/lang/String;", "contentEquals", "(Ljava/lang/CharSequence;)Z", [s_pkg, JString("com.arjonasoftware.babycam")], False)
    _, rm_ok = stubs.resolve("Ljava/lang/String;", "regionMatches", "(ILjava/lang/String;II)Z", [s_pkg, 4, JString("arjonasoftware"), 0, 6], False)
    _, rm_ic = stubs.resolve("Ljava/lang/String;", "regionMatches", "(ZILjava/lang/String;II)Z", [s_pkg, 1, 19, JString("BABYCAM"), 0, 7], False)
    _, sw_off = stubs.resolve("Ljava/lang/String;", "startsWith", "(Ljava/lang/String;I)Z", [s_pkg, JString("arjonasoftware"), 4], False)
    _, rep_cs = stubs.resolve("Ljava/lang/String;", "replace", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Ljava/lang/String;",
                              [JString("BabyCam-cam1"), JString("BabyCam-"), JString("")], False)
    _, rep_all = stubs.resolve("Ljava/lang/String;", "replaceAll", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                               [JString("a1b2"), JString(r"\d"), JString("x")], False)
    _, cmp_res = stubs.resolve("Ljava/lang/String;", "compareToIgnoreCase", "(Ljava/lang/String;)I",
                               [JString("BabyCam"), JString("babycam")], False)
    assert eq_ok == 1 and ct_ok == 1 and ct_no == 0 and mt_ok == 1 and ce_ok == 1
    assert rm_ok == 1 and rm_ic == 1 and sw_off == 1
    assert isinstance(rep_cs, _JS) and rep_cs.text == "cam1"
    assert isinstance(rep_all, _JS) and rep_all.text == "axbx"
    assert cmp_res == 0

    # 2) PackageManager Installer-Origin (Play Store Standardwert)
    ctx = JObject("Landroid/content/Context;")
    _, pm = stubs.resolve("Landroid/content/Context;", "getPackageManager", "()Landroid/content/pm/PackageManager;", [ctx], False)
    _, inst = stubs.resolve("Landroid/content/pm/PackageManager;", "getInstallerPackageName",
                            "(Ljava/lang/String;)Ljava/lang/String;", [pm, s_pkg], False)
    _, src_info = stubs.resolve("Landroid/content/pm/PackageManager;", "getInstallSourceInfo",
                                "(Ljava/lang/String;)Landroid/content/pm/InstallSourceInfo;", [pm, s_pkg], False)
    _, inst2 = stubs.resolve("Landroid/content/pm/InstallSourceInfo;", "getInstallingPackageName",
                             "()Ljava/lang/String;", [src_info], False)
    assert inst.text == "com.android.vending"
    assert inst2.text == "com.android.vending"


def test_apkeditor_smali_classes_layout_and_synthetic_f0_package_check(tmp_path):
    from core.java.class_provider import StudioClassProvider
    from core.java.dalvik import DalvikVM, JObject
    from core.java.dex_loader import AndroidStubs
    from core.java.lifecycle import _StubRuntime
    from core.java.view_tree import install_view_stubs

    # APKEditor-Layout: smali/classes/com/arjonasoftware/babycam/MainActivity.smali
    cls_dir = tmp_path / "smali" / "classes" / "com" / "arjonasoftware" / "babycam"
    cls_dir.mkdir(parents=True)
    main_smali = cls_dir / "MainActivity.smali"
    main_smali.write_text(
        ".class public Lcom/arjonasoftware/babycam/MainActivity;\n"
        ".super Landroid/app/Activity;\n"
        ".method public checkSelf()I\n"
        "    .locals 3\n"
        "    invoke-virtual {p0}, Ljava/lang/Object;->getClass()Ljava/lang/Class;\n"
        "    move-result-object v0\n"
        "    invoke-virtual {v0}, Ljava/lang/Class;->getPackage()Ljava/lang/Package;\n"
        "    move-result-object v0\n"
        "    invoke-virtual {v0}, Ljava/lang/Package;->getName()Ljava/lang/String;\n"
        "    move-result-object v0\n"
        '    const-string v1, "com.arjonasoftware.babycam"\n'
        "    invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z\n"
        "    move-result v2\n"
        "    if-eqz v2, :bad\n"
        "    invoke-virtual {v0, v1}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z\n"
        "    move-result v2\n"
        "    if-nez v2, :ok\n"
        ":bad\n"
        '    const-string v0, "It is not allowed to modify the app"\n'
        "    const/4 v1, 0x1\n"
        "    invoke-static {p0, v0, v1}, Landroid/widget/Toast;->makeText(Landroid/content/Context;Ljava/lang/CharSequence;I)Landroid/widget/Toast;\n"
        "    move-result-object v0\n"
        "    invoke-virtual {v0}, Landroid/widget/Toast;->show()V\n"
        "    invoke-virtual {p0}, Landroid/app/Activity;->finish()V\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n"
        ":ok\n"
        "    const/4 v0, 0x1\n"
        "    return v0\n"
        ".end method\n",
        encoding="utf-8",
    )

    prov = StudioClassProvider(str(tmp_path), zone="original")
    assert prov._desc_from_rel("smali/classes/com/arjonasoftware/babycam/MainActivity.smali") == "Lcom/arjonasoftware/babycam/MainActivity;"
    assert prov.get_class("Lcom/arjonasoftware/babycam/MainActivity;") is not None

    vm = DalvikVM()
    vm.lenient = True
    vm.class_provider = prov
    stubs = AndroidStubs(vm=vm)
    stubs.seed_framework_statics(vm)
    vm.runtime = _StubRuntime(stubs)
    tree = install_view_stubs(stubs, app_root=str(tmp_path))

    act = JObject("Lcom/arjonasoftware/babycam/MainActivity;", {})
    res = vm.invoke("Lcom/arjonasoftware/babycam/MainActivity;", "checkSelf", "()I", [act])
    assert res == 1
    assert not (tree.last_toast or "")
    assert not act.fields.get("__finished")


def test_real_babycam_unpacked_boots_without_false_tamper_toast():
    from core.java.glass_runner import GlassAppRunner

    for sub in ("base_unpacked_apktool", "base_unpacked_apkeditor"):
        root = os.path.join(_ROOT, "source", "com.arjonasoftware.babycam", sub)
        if not os.path.isdir(root):
            continue
        gr = GlassAppRunner(root, zone="original")
        gr.build(native_fusion=False)
        rep = gr.run(max_steps=600000, native_fusion=False)
        toast = (gr.tree.last_toast or "").lower()
        all_toasts = " ".join(getattr(gr.tree, "_toasts", None) or ()).lower()
        assert "not allowed to modify" not in toast and "verboten" not in toast
        assert "not allowed to modify" not in all_toasts and "verboten" not in all_toasts
        assert rep.get("view_nodes", 0) >= 25
