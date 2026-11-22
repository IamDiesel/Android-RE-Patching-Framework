# -*- coding: utf-8 -*-
"""core.java.app_runtime — erkennt die UI-Runtime einer entpackten App (klassisch vs. JS/Engine).

Viele reale Apps (React Native, NativeScript, Flutter) bauen ihre Oberflaeche NICHT ueber statische
android.view-Aufrufe, sondern zur Laufzeit aus JS/Dart. `detect_runtime(root)` meldet das anhand von
Markerdateien, damit die Anzeige erklaeren kann, warum beim onCreate-Lauf kein View-Baum entsteht.
"""
from __future__ import annotations

import glob
import json
import os
import re
import xml.etree.ElementTree as ET

_SURFACE_SHELL_CLS = frozenset({
    "View", "ViewGroup", "FrameLayout", "LinearLayout",
    "FlutterView", "FlutterSurfaceView", "FlutterTextureView", "FlutterImageView",
    "ReactRootView", "SurfaceView", "TextureView",
    "SplashView", "WebView", "SystemWebView", "CordovaWebViewImpl",
    "BridgeWebView", "AdaWebView", "DCloudWebView",
})


_RUNTIME_EXPLANATIONS = {
    "flutter": (
        "Flutter",
        "Flutter rendert selbst auf einer Surface (Skia); es gibt keine android.view-Hierarchie. "
        "Netzwerk läuft nativ in libapp.so/BoringSSL außerhalb von Dalvik/OkHttp — "
        "nutze '🔍 Engine-APIs' oder '🌐 Browser & Inspect'.",
    ),
    "react native (js)": (
        "React Native",
        "Die UI wird zur Laufzeit aus JS (index.android.bundle) gebaut; beim onCreate-Lauf "
        "entsteht kein statischer android.view-Baum.",
    ),
    "hybrid web": (
        "Hybrid Web",
        "Die UI basiert auf HTML/JS in Web-Assets (DCloud HTML5+ / Cordova / Capacitor / WebView) "
        "und wird über die WebView-Bridge bzw. den In-Phone-WebView-Controller dargestellt.",
    ),
    "nativescript": (
        "NativeScript",
        "Die UI wird zur Laufzeit aus JS gebaut (NativeScript-Runtime); kein statischer "
        "android.view-Baum.",
    ),
    "jetpack compose": (
        "Jetpack Compose",
        "Die App nutzt Jetpack Compose (deklarative Kotlin-UI); Composables werden "
        "über ComponentActivityKt.setContent / ComposeView komponiert.",
    ),
}


def detect_runtime(root):
    """(label, erklaerung). label == '' = klassische android.view-App (oder unbekannt)."""
    try:
        from core.java.runtime_detect import RUNTIME_PRIORITY

        def has(*parts):
            return os.path.exists(os.path.join(root, *parts))

        def lib(name):
            return bool(glob.glob(os.path.join(root, "lib", "*", name)))

        def smali(sub):
            for d in glob.glob(os.path.join(root, "smali*")):
                if os.path.isdir(os.path.join(d, *sub.split("/"))):
                    return True
            return False

        checks = {
            "flutter": lambda: lib("libflutter.so") or has("assets", "flutter_assets") or smali("io/flutter"),
            "react native (js)": lambda: (
                has("assets", "index.android.bundle")
                or lib("libreactnativejni.so")
                or lib("libhermes.so")
                or lib("libhermesvm.so")
                or smali("com/facebook/react")
            ),
            "hybrid web": lambda: (
                smali("io/dcloud")
                or os.path.isfile(os.path.join(root, "assets", "data", "dcloud_control.xml"))
                or os.path.isfile(os.path.join(root, "assets", "www", "index.html"))
                or os.path.isfile(os.path.join(root, "assets", "public", "index.html"))
                or os.path.isfile(os.path.join(root, "assets", "capacitor.config.json"))
                or smali("org/apache/cordova")
                or smali("com/getcapacitor")
            ),
            "nativescript": lambda: has("assets", "app") or lib("libNativeScript.so") or smali("com/tns"),
            "jetpack compose": lambda: smali("androidx/compose/runtime") or smali("androidx/activity/compose"),
        }
        for key in RUNTIME_PRIORITY:
            fn = checks.get(key)
            if fn is not None and fn():
                return _RUNTIME_EXPLANATIONS[key]
        return ("", "")
    except Exception:
        return ("", "")


def mount_inphone_runtime_if_needed(tree, app_root, stubs=None, on_async_update=None) -> tuple[bool, str]:
    """Montiert bei leerer/unrendered Engine-Shell die passende In-Phone-UI und markiert `render_source`."""
    if tree is None or not app_root:
        return (False, "")
    existing_ctrl = (
        getattr(tree, "_flutter_ctrl", None)
        or getattr(tree, "_rn_ctrl", None)
        or getattr(tree, "_hybrid_ctrl", None)
    )
    if not is_unrendered_surface_shell(tree):
        if existing_ctrl is not None and on_async_update is not None and hasattr(existing_ctrl, "set_async_updater"):
            existing_ctrl.set_async_updater(on_async_update)
        return (False, "")
    label, _why = detect_runtime(app_root)
    if not label:
        return (False, "")
    ctrl = None
    if label == "Flutter":
        from core.java.flutter_app_producer import mount_flutter_app_into_tree
        ctrl = mount_flutter_app_into_tree(
            tree, app_root, stubs=stubs, on_async_update=on_async_update,
        )
    elif label == "React Native":
        from core.java.rn_app_producer import mount_rn_app_into_tree
        ctrl = mount_rn_app_into_tree(
            tree, app_root, stubs=stubs, on_async_update=on_async_update,
        )
    elif label == "Hybrid Web":
        from core.java.hybrid_web_producer import mount_hybrid_web_app_into_tree
        ctrl = mount_hybrid_web_app_into_tree(
            tree, app_root, stubs=stubs, on_async_update=on_async_update,
        )
    else:
        return (False, label)
    if ctrl is not None and on_async_update is not None and hasattr(ctrl, "set_async_updater"):
        ctrl.set_async_updater(on_async_update)
    tree.render_source = "reconstructed"
    return (True, label)


_ENGINE_HOST_CLS = frozenset({
    "FlutterView", "FlutterSurfaceView", "FlutterTextureView", "FlutterImageView",
    "ReactRootView", "SplashView", "WebView", "SystemWebView",
    "CordovaWebViewImpl", "BridgeWebView", "AdaWebView", "DCloudWebView",
})


def is_unrendered_surface_shell(tree) -> bool:
    """True, wenn der ViewTree leer ist oder nur aus leeren Surface-/Splash-/WebView-Huellen besteht."""
    if tree is None or getattr(tree, "root", None) is None:
        return True
    try:
        nodes = list(tree.nodes())
    except Exception:
        return True
    if not nodes:
        return True
    has_engine_host = any(n.cls in _ENGINE_HOST_CLS for n in nodes)
    if has_engine_host:
        for n in nodes:
            if (getattr(n, "text", None) or "").strip() or getattr(n, "clickable", False):
                return False
            if n.cls in ("Button", "EditText", "CheckBox", "Switch", "RadioButton", "TextView"):
                return False
        return True
    for n in nodes:
        if n.cls not in _SURFACE_SHELL_CLS:
            return False
        if (getattr(n, "text", None) or "").strip() or getattr(n, "clickable", False):
            return False
        props = getattr(n, "props", None) or {}
        if props.get("drawable") or props.get("bg_drawable") or props.get("img"):
            return False
    return True


def inspect_engine_details(root) -> list[str]:
    """Liest generische Hinweise zu Native-Engine-Libs, Manifest-WebViews und Endpunkt-Configs."""
    out: list[str] = []
    if not root or not os.path.isdir(root):
        return out
    try:
        libs = sorted({os.path.basename(p) for p in glob.glob(os.path.join(root, "lib", "*", "*.so"))})
        if libs:
            sample = ", ".join(libs[:5]) + (" ..." if len(libs) > 5 else "")
            out.append("Native Libs: %s" % sample)
    except Exception:
        pass
    try:
        man_path = os.path.join(root, "AndroidManifest.xml")
        if os.path.isfile(man_path):
            ans = "{http://schemas.android.com/apk/res/android}"
            r_el = ET.parse(man_path).getroot()
            app_el = r_el.find("application")
            if app_el is not None:
                aname = app_el.get(ans + "name") or ""
                if aname:
                    out.append("Application: %s" % aname)
                web_acts = []
                for act in app_el.findall("activity"):
                    nm = act.get(ans + "name") or ""
                    low = nm.lower()
                    if any(w in low for w in ("webview", "inappbrowser", "customtabs")):
                        web_acts.append(nm.rsplit(".", 1)[-1])
                if web_acts:
                    out.append("Web-Activities: %s" % ", ".join(sorted(set(web_acts))[:3]))
    except Exception:
        pass
    try:
        endpoints = []
        assets_dir = os.path.join(root, "assets")
        if os.path.isdir(assets_dir):
            safe_keys = ("HOST", "BASE_URL", "WEBSOCKET_URL", "USERPOOLID")
            for dp, _dn, fns in os.walk(assets_dir):
                for fn in fns:
                    low = fn.lower()
                    if not (".env" in low or ("config" in low and low.endswith(".json"))):
                        continue
                    fp = os.path.join(dp, fn)
                    if os.path.getsize(fp) > 65536:
                        continue
                    txt = open(fp, "r", encoding="utf-8", errors="replace").read()
                    if low.endswith(".json"):
                        data = json.loads(txt)
                        if isinstance(data, dict):
                            for k, v in data.items():
                                if k.upper() in safe_keys and v:
                                    endpoints.append("%s=%s" % (k, str(v)[:52]))
                    else:
                        for ln in txt.splitlines():
                            m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$", ln)
                            if m and m.group(1).upper() in safe_keys:
                                endpoints.append("%s=%s" % (m.group(1), m.group(2)[:52]))
        if endpoints:
            out.append("Statische Asset-URLs (0 Live-Calls): " + "; ".join(dict.fromkeys(endpoints))[:110])
    except Exception:
        pass
    return out[:4]
