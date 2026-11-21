# -*- coding: utf-8 -*-
"""AP-E9 (MS-5): Smali-Patch aendert das Laufverhalten in der Sandbox. Headless."""
from core.java.sandbox_patch import apply_smali_patches
from core.java.dalvik import DalvikVM
from core.java import smali as Sm

SRC = """.method public static v()I
    .locals 1
    const/16 v0, 0x5
    return v0
.end method
"""

def _run(src):
    vm = DalvikVM()
    md = Sm.parse_method(src)
    vm.register(md)
    return vm.invoke(md.cls, md.name, md.sig, ())

def test_patch_changes_runtime_result():
    assert _run(SRC) == 5
    patched, n = apply_smali_patches(SRC, [{"orig": "const/16 v0, 0x5", "edit": "const/16 v0, 0x63"}])
    assert n == 1
    assert _run(patched) == 99   # Patch wirkt in der Sandbox

def test_file_filter():
    pats = [{"orig": "const/16 v0, 0x5", "edit": "const/16 v0, 0x63", "file": "smali/other/X.smali"}]
    _, n = apply_smali_patches(SRC, pats, file="smali/com/A.smali")
    assert n == 0                # anderer File -> nicht angewandt
    _, n2 = apply_smali_patches(SRC, pats, file="smali/other/X.smali")
    assert n2 == 1

def test_hex_lib_ignored():
    _, n = apply_smali_patches(SRC, [{"type": "hex", "orig": "const/16 v0, 0x5", "edit": "x"}])
    assert n == 0

def test_new_key_alias():
    patched, n = apply_smali_patches(SRC, [{"orig": "0x5", "new": "0x7"}])
    assert n == 1 and "0x7" in patched

def test_controller_patched_source():
    from ui.controllers.debugger_controller import DebuggerController
    class _Studio: smali_patches = [{"orig": "const/16 v0, 0x5", "edit": "const/16 v0, 0x63"}]
    class _WS: smali_studio = _Studio()
    class _App: workspace_tab = _WS()
    c = DebuggerController(_App())
    c.source = SRC
    c.apply_patches = False
    assert "0x5" in c._patched_source()
    c.apply_patches = True
    out = c._patched_source()
    assert "0x63" in out and "0x5" not in out
