# -*- coding: utf-8 -*-
"""MS-4 erste Slice: LifecycleRunner treibt eine Entry-Klasse durch die Android-Lifecycle-Sequenz;
Callees werden lazy aufgeloest; Report meldet called/skipped/missing."""
import os
import tempfile

from core.java.dalvik import DalvikVM
from core.java.class_provider import StudioClassProvider, TextClassProvider
from core.java.lifecycle import LifecycleRunner

MAIN = """.class public Lcom/foo/MainActivity;
.super Landroid/app/Activity;
.field public static mark:I
.method public constructor <init>()V
    .locals 0
    return-void
.end method
.method public onCreate(Landroid/os/Bundle;)V
    .locals 2
    const/4 v0, 0x5
    invoke-static {v0}, Lcom/foo/Helper;->triple(I)I
    move-result v1
    sput v1, Lcom/foo/MainActivity;->mark:I
    return-void
.end method
"""
HELPER = """.class public Lcom/foo/Helper;
.super Ljava/lang/Object;
.method public static triple(I)I
    .locals 1
    const/4 v0, 0x3
    mul-int v0, p0, v0
    return v0
.end method
"""


def _tree():
    d = tempfile.mkdtemp()
    sdir = os.path.join(d, "smali", "com", "foo")
    os.makedirs(sdir)
    with open(os.path.join(sdir, "MainActivity.smali"), "w", encoding="utf-8") as f:
        f.write(MAIN)
    with open(os.path.join(sdir, "Helper.smali"), "w", encoding="utf-8") as f:
        f.write(HELPER)
    return d


def test_lifecycle_sequence_and_lazy_callee():
    vm = DalvikVM()
    rep = LifecycleRunner(vm, StudioClassProvider(_tree(), zone="original")).run("Lcom/foo/MainActivity;")
    called = [c["m"] for c in rep["called"]]
    assert "<init>" in called and "onCreate" in called
    assert vm.statics["Lcom/foo/MainActivity;"]["mark"] == 15   # onCreate rief Helper (triple(5))
    assert not rep["missing"]
    skipped = [s["m"] for s in rep["skipped"]]
    assert "onStart" in skipped and "onResume" in skipped


def test_lifecycle_entry_not_found():
    vm = DalvikVM()
    rep = LifecycleRunner(vm, TextClassProvider(HELPER)).run("Lcom/foo/MainActivity;")
    assert rep["called"] == []
    assert any(s["grund"].startswith("Entry-Klasse nicht") for s in rep["skipped"])


def test_lifecycle_patched_zone_changes_result():
    patched = HELPER.replace("const/4 v0, 0x3", "const/4 v0, 0x4")
    d = tempfile.mkdtemp()
    sdir = os.path.join(d, "smali", "com", "foo")
    os.makedirs(sdir)
    with open(os.path.join(sdir, "MainActivity.smali"), "w", encoding="utf-8") as f:
        f.write(MAIN)
    with open(os.path.join(sdir, "Helper.smali"), "w", encoding="utf-8") as f:
        f.write(patched)
    vm = DalvikVM()
    LifecycleRunner(vm, StudioClassProvider(d, zone="patched")).run("Lcom/foo/MainActivity;")
    assert vm.statics["Lcom/foo/MainActivity;"]["mark"] == 20   # 5*4 (Patch wirkt im Lifecycle)
