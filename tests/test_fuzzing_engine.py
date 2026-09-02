import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.fuzzing_engine import FuzzingEngine


def test_fuzz_by_method_name():
    ram_cache = [
        ("smali/com/app/Test.smali", ".method public test()V\n    return-void\n.end method\n"),
        ("smali/com/app/Target.smali",
         ".method private hiddenCall()Z\n    const/4 v0, 0x1\n    return v0\n.end method\n")
    ]

    candidates = FuzzingEngine.fuzz_by_method_name("hiddenCall", ram_cache)

    assert len(candidates) == 1
    assert candidates[0]["file"] == "smali/com/app/Target.smali"
    assert candidates[0]["sig"] == "private hiddenCall()Z"


def test_fuzz_by_content_snippet_with_register_changes():
    """Prüft, ob der Fuzzer Opcodes erkennt, selbst wenn sich Register (v0 -> v2) ändern."""
    ram_cache = [
        ("smali/com/app/New.smali",
         ".method public newMethod()V\n    .line 42\n    const-string v2, \"SuperSecret\"\n    return-void\n.end method\n")
    ]

    # Der originale Favoriten-Patch hatte Register v0 und keine Zeilennummer
    orig_code = """
    .method public obsolete()V
        const-string v0, "SuperSecret"
        return-void
    .end method
    """

    # threshold auf 0.85 gesetzt (sollte locker matchen, da nur der Method-Name und Register variieren)
    candidates = FuzzingEngine.fuzz_by_content_snippet(orig_code, "smali/com/app/New.smali", ram_cache, threshold=0.85)

    assert len(candidates) == 1
    assert candidates[0]["file"] == "smali/com/app/New.smali"
    assert candidates[0]["sig"] == "public newMethod()V"
    assert candidates[0]["score"] > 0.85