# -*- coding: utf-8 -*-
import os
import core.java.canvas_recorder, core.java.rn_producer, core.java.skia_recorder, core.java.rn_js_bridge  # noqa: F401
from core.java.runtime_detect import detect_from_names, detect_runtime, producer_for_app


def test_detect_flutter():
    assert detect_from_names(["libflutter.so", "classes.dex"]) == "flutter"
    assert detect_from_names(["assets/flutter_assets/kernel_blob.bin"]) == "flutter"


def test_detect_react_native():
    assert detect_from_names(["lib/arm64-v8a/libhermes.so"]) == "react native (js)"
    assert detect_from_names(["assets/index.android.bundle"]) == "react native (js)"


def test_detect_compose():
    assert detect_from_names(["smali/androidx/compose/ui/platform/Foo.smali"]) == "jetpack compose"


def test_detect_classic_default():
    assert detect_from_names(["classes.dex", "AndroidManifest.xml", "res/layout/main.xml"]) == "classic"


def test_priority_flutter_over_rn():
    # Flutter-App kann auch JS-Reste haben; Flutter gewinnt (frueher in der Liste)
    assert detect_from_names(["libflutter.so", "libjsc.so"]) == "flutter"


def test_producer_resolves():
    k, p = producer_for_app(names=["libhermes.so"])
    assert k == "react native (js)" and p is not None
    k2, p2 = producer_for_app(names=["classes.dex"])
    assert k2 == "classic" and p2 is None  # klassisch laeuft direkt ueber ViewTree


def test_detect_from_dir(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "libflutter.so").write_bytes(b"\x00")
    assert detect_runtime(app_dir=str(tmp_path)) == "flutter"
