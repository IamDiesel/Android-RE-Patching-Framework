# -*- coding: utf-8 -*-
"""X1-Erweiterung: Lazy ClassProvider — App-interne Callees werden bei `invoke` nachgeladen.
Belegt zugleich die Zonen-Idee: derselbe Aufruf aus der gepatchten Destination liefert ein anderes Ergebnis."""
import os
import tempfile

from core.java.dalvik import DalvikVM
from core.java import smali as Sm
from core.java.class_provider import TextClassProvider, StudioClassProvider

ENTRY = """.method public static run(I)I
    .locals 2
    invoke-static {p0}, Lcom/foo/Helper;->triple(I)I
    move-result v0
    return v0
.end method"""
HELPER = """.class public Lcom/foo/Helper;
.super Ljava/lang/Object;
.method public static triple(I)I
    .locals 2
    const/4 v0, 0x3
    mul-int v0, p0, v0
    return v0
.end method"""


def _vm():
    vm = DalvikVM()
    md = Sm.parse_method(ENTRY)
    vm.register(md)
    return vm, md


def test_text_provider_lazy_callee():
    vm, md = _vm()
    vm.class_provider = TextClassProvider(HELPER)
    assert vm.invoke(md.cls, md.name, md.sig, (7,)) == 21
    assert not vm.missing


def test_studio_provider_lazy_callee():
    d = tempfile.mkdtemp()
    sdir = os.path.join(d, "smali", "com", "foo")
    os.makedirs(sdir)
    with open(os.path.join(sdir, "Helper.smali"), "w", encoding="utf-8") as f:
        f.write(HELPER)
    vm, md = _vm()
    vm.class_provider = StudioClassProvider(d, zone="original")
    assert vm.invoke(md.cls, md.name, md.sig, (10,)) == 30
    assert not vm.missing


def test_zone_patched_changes_result():
    patched = HELPER.replace("const/4 v0, 0x3", "const/4 v0, 0x4")  # triple -> quadruple
    d = tempfile.mkdtemp()
    sdir = os.path.join(d, "smali", "com", "foo")
    os.makedirs(sdir)
    with open(os.path.join(sdir, "Helper.smali"), "w", encoding="utf-8") as f:
        f.write(patched)
    vm, md = _vm()
    vm.class_provider = StudioClassProvider(d, zone="patched")
    assert vm.invoke(md.cls, md.name, md.sig, (10,)) == 40   # Patch wirkt (40 statt 30)


def test_no_provider_is_missing():
    vm, md = _vm()
    vm.invoke(md.cls, md.name, md.sig, (7,))
    assert any(m["name"] == "triple" for m in vm.missing)   # ohne Provider bleibt Callee ungeloest
