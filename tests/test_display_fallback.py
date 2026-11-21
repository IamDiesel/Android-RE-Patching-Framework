# -*- coding: utf-8 -*-
"""AP-E11 (#298): Definierte App-Runtime-Grenze -> lesbarer Info-Screen statt leer. Headless.

Testet die reinen Bausteine hinter dem DisplayTab-Fallback: detect_runtime erkennt die Runtime,
info_screen baut einen rendernden ViewTree. (Die GUI-Verdrahtung selbst ist py_compile + Sichtpruefung.)
"""
import os
from core.java.app_runtime import detect_runtime, is_unrendered_surface_shell, inspect_engine_details
from core.java.view_tree import ViewNode, ViewTree, info_screen, paint_commands


def _texts(tree):
    return " | ".join(n.text for n in tree.nodes() if n.text)


def test_detect_nativescript(tmp_path):
    (tmp_path / "assets" / "app").mkdir(parents=True)   # NativeScript-Marker
    label, why = detect_runtime(str(tmp_path))
    assert label == "NativeScript" and "Laufzeit" in why


def test_detect_react_native(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index.android.bundle").write_text("// js")
    label, why = detect_runtime(str(tmp_path))
    assert label == "React Native"


def test_detect_classic_empty(tmp_path):
    (tmp_path / "res").mkdir()
    label, why = detect_runtime(str(tmp_path))
    assert label == ""   # klassische/unbekannte App -> keine Grenze


def test_info_screen_renders_boundary():
    label, why = "NativeScript", "UI zur Laufzeit aus JS gebaut."
    tree = info_screen(label + " erkannt", [why, "", "JS-Engine-Bruecke noetig."],
                       badge="RUNTIME-GRENZE")
    assert tree.root is not None
    txt = _texts(tree)
    assert "NativeScript erkannt" in txt
    assert "RUNTIME-GRENZE" in txt
    # rendert tatsaechlich Zeichenbefehle mit dem Erklaertext
    cmds = paint_commands(tree)
    joined = " ".join(c.get("text", "") for c in cmds if c["op"] == "text")
    assert "JS-Engine-Bruecke noetig." in joined


def test_info_screen_not_clickable():
    tree = info_screen("Kein View-Baum", ["Zeile"], badge="HINWEIS")
    # Info-Screen hat keine klickbaren Knoten (reine Anzeige)
    assert tree.clickable_at(10, 10) is None


def test_unrendered_surface_shell_and_flutter_engine_details(tmp_path):
    # Reine FlutterView -> FlutterSurfaceView Huelle gilt als unrendered shell
    shell_root = ViewNode("FlutterView")
    shell_root.add(ViewNode("FlutterSurfaceView"))
    t_shell = ViewTree()
    t_shell.set_root(shell_root)
    assert is_unrendered_surface_shell(t_shell) is True

    # Echter ViewTree mit interaktivem Button ist KEINE leere Huelle
    real_root = ViewNode("LinearLayout")
    real_root.add(ViewNode("Button", vid="ok", text="Anmelden", clickable=True))
    t_real = ViewTree()
    t_real.set_root(real_root)
    assert is_unrendered_surface_shell(t_real) is False

    # inspect_engine_details erkennt Flutter-Libs, Wrapper-App, Web-Activities und Endpunkte ohne API-Keys
    (tmp_path / "lib" / "arm64-v8a").mkdir(parents=True)
    (tmp_path / "lib" / "arm64-v8a" / "libflutter.so").write_bytes(b"ELF")
    (tmp_path / "lib" / "arm64-v8a" / "libapp.so").write_bytes(b"ELF")
    (tmp_path / "assets" / "flutter_assets").mkdir(parents=True)
    (tmp_path / "assets" / "flutter_assets" / ".env.prod").write_text(
        "HOST=api.example.invalid\nBASE_URL=https://api.example.invalid/graphql\nAPI_KEY=secret-should-not-leak\n",
        encoding="utf-8",
    )
    (tmp_path / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="demo.flutter">'
        '<application android:name="com.pairip.application.Application">'
        '<activity android:name="demo.InAppBrowserActivity"/>'
        '</application></manifest>',
        encoding="utf-8",
    )
    details = inspect_engine_details(str(tmp_path))
    joined = "\n".join(details)
    assert "libflutter.so" in joined and "libapp.so" in joined
    assert "com.pairip.application.Application" in joined
    assert "InAppBrowserActivity" in joined
    assert "api.example.invalid" in joined
    assert "secret-should-not-leak" not in joined
