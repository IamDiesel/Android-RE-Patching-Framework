# -*- coding: utf-8 -*-
"""AP-E3: Runtime-Erkennung anhand von Markerdateien. Headless."""
import os
from core.java.app_runtime import detect_runtime

def _mk(tmp_path, *rel):
    p = tmp_path.joinpath(*rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    return p

def test_react_native(tmp_path):
    _mk(tmp_path, "assets", "index.android.bundle")
    label, why = detect_runtime(str(tmp_path))
    assert label == "React Native" and why

def test_react_native_smali(tmp_path):
    (tmp_path / "smali" / "com" / "facebook" / "react").mkdir(parents=True)
    assert detect_runtime(str(tmp_path))[0] == "React Native"

def test_nativescript(tmp_path):
    (tmp_path / "assets" / "app").mkdir(parents=True)
    assert detect_runtime(str(tmp_path))[0] == "NativeScript"

def test_flutter(tmp_path):
    _mk(tmp_path, "lib", "arm64-v8a", "libflutter.so")
    assert detect_runtime(str(tmp_path))[0] == "Flutter"

def test_classic_unknown(tmp_path):
    (tmp_path / "smali" / "com" / "app").mkdir(parents=True)
    assert detect_runtime(str(tmp_path)) == ("", "")
