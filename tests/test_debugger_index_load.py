# -*- coding: utf-8 -*-
"""Index-Laden: Methodenblock aus Dateiinhalt extrahieren + als Debug-Ziel setzen. Headless."""
from ui.controllers.debugger_controller import DebuggerController
from core.java import smali as Sm

CONTENT = """.class public Lcom/app/Foo;
.super Ljava/lang/Object;

.method public static a(I)I
    .locals 1
    add-int v0, p0, p0
    return v0
.end method

.method public static b(I)I
    .locals 1
    mul-int/lit8 v0, p0, 0x3
    return v0
.end method
"""

def _line_of(sub):
    for i, ln in enumerate(CONTENT.splitlines(), start=1):
        if sub in ln:
            return i
    raise AssertionError(sub)

def test_extract_second_method():
    c = DebuggerController(None)
    tl = _line_of("mul-int/lit8")
    ok = c.load_method_block(CONTENT, "smali/com/app/Foo.smali", tl, smali_dir="/proj/smali")
    assert ok is True
    assert c.name == "b"
    assert ".method public static b(I)I" in c.source
    assert ".end method" in c.source and "add-int" not in c.source   # NUR Methode b
    assert c.zone_root == "/proj/smali"
    assert c.relpath == "smali/com/app/Foo.smali"
    # source_map muss auf dem extrahierten Block laufen
    sm = Sm.source_map(c.source, c.name)
    assert [e["op"] for e in sm] == ["mul-int/lit8", "return"]

def test_extract_first_method():
    c = DebuggerController(None)
    tl = _line_of("add-int v0")
    assert c.load_method_block(CONTENT, "x.smali", tl)
    assert c.name == "a"
    assert "mul-int" not in c.source

def test_target_line_on_method_header():
    c = DebuggerController(None)
    tl = _line_of(".method public static b(I)I")
    assert c.load_method_block(CONTENT, "x.smali", tl)
    assert c.name == "b"

APP = """.class public final Lcom/app/MainApplication;
.super Landroid/app/Application;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Application;-><init>()V
    return-void
.end method

.method public onCreate()V
    .locals 1
    const/4 v0, 0x1
    return-void
.end method
"""

def _appline(sub):
    for i, ln in enumerate(APP.splitlines(), start=1):
        if sub in ln:
            return i
    raise AssertionError(sub)

def test_class_line_loads_whole_class_with_default_entry():
    c = DebuggerController(None)
    ok = c.load_method_block(APP, "smali/com/app/MainApplication.smali", 1, smali_dir="/p/smali")
    assert ok is True
    assert c.name == "onCreate"                 # Default-Entry bevorzugt
    assert "<init>" in c.source and "onCreate" in c.source   # ganze Klasse
    assert c.relpath.endswith("MainApplication.smali")

def test_constructor_hit_loads_init_only():
    c = DebuggerController(None)
    tl = _appline("invoke-direct")
    assert c.load_method_block(APP, "x.smali", tl)
    assert c.name == "<init>"
    assert "onCreate" not in c.source           # nur der Konstruktor

def test_class_without_methods_rejected():
    c = DebuggerController(None)
    only = ".class public Lcom/app/Empty;\n.super Ljava/lang/Object;\n.field x:I\n"
    assert c.load_method_block(only, "e.smali", 1) is False
