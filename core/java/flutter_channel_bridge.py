# -*- coding: utf-8 -*-
"""core.java.flutter_channel_bridge — G6: Bidirektionale Flutter MethodChannel/EventChannel-Bridge."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.java.objects import JObject, JString


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject) and "text" in v.fields:
        return str(v.fields["text"] or "")
    return str(v)


def _to_py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject):
        if "map" in v.fields and isinstance(v.fields["map"], dict):
            return {str(_to_py(k)): _to_py(val) for k, val in v.fields["map"].items()}
        if "__items" in v.fields and isinstance(v.fields["__items"], list):
            return [_to_py(x) for x in v.fields["__items"]]
        if "value" in v.fields:
            return _to_py(v.fields["value"])
    return v


def _to_java(v: Any) -> Any:
    if v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, str):
        return JString(v)
    if isinstance(v, dict):
        m = {str(k): _to_java(val) for k, val in v.items()}
        rkeys = {str(k): JString(str(k)) for k in m.keys()}
        return JObject("Ljava/util/HashMap;", {"map": m, "__raw_keys": rkeys})
    if isinstance(v, (list, tuple)):
        return JObject("Ljava/util/ArrayList;", {"__items": [_to_java(x) for x in v]})
    return v


def install_flutter_channel_bridge(stubs: Any, tree: Any = None) -> None:
    """Registriert Flutter Engine/BinaryMessenger/MethodChannel/EventChannel/BasicMessageChannel."""
    if stubs is None or getattr(stubs, "_flutter_bridge_installed", False):
        return
    stubs._flutter_bridge_installed = True
    stubs.__dict__.setdefault("_flutter_method_channels", {})
    stubs.__dict__.setdefault("_flutter_event_channels", {})
    stubs.__dict__.setdefault("_flutter_message_channels", {})
    stubs.__dict__.setdefault("_flutter_channel_log", [])
    R = stubs.register

    MC = "Lio/flutter/plugin/common/MethodChannel;"
    def _mc_init(a):
        if a and isinstance(a[0], JObject):
            nm = _s(a[2]) if len(a) > 2 else ""
            a[0].fields["_channel"] = nm or ""
            a[0].fields["_messenger"] = a[1] if len(a) > 1 else None
        return None

    R(MC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;)V", _mc_init)
    R(MC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;Lio/flutter/plugin/common/MethodCodec;)V", _mc_init)
    R(MC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;Lio/flutter/plugin/common/MethodCodec;Lio/flutter/plugin/common/BinaryMessenger$TaskQueue;)V", _mc_init)

    def _mc_set_handler(a):
        if a and isinstance(a[0], JObject):
            ch = a[0].fields.get("_channel") or ""
            h = a[1] if len(a) > 1 else None
            if ch:
                if h is None:
                    stubs._flutter_method_channels.pop(ch, None)
                else:
                    stubs._flutter_method_channels[ch] = h
            a[0].fields["_handler"] = h
        return None

    R(MC, "setMethodCallHandler", "(Lio/flutter/plugin/common/MethodChannel$MethodCallHandler;)V", _mc_set_handler)
    R(MC, "invokeMethod", "(Ljava/lang/String;Ljava/lang/Object;)V",
      lambda a: invoke_flutter_channel(stubs, a[0].fields.get("_channel", "") if a and isinstance(a[0], JObject) else "", _s(a[1]) or "", a[2] if len(a) > 2 else None))
    R(MC, "invokeMethod", "(Ljava/lang/String;Ljava/lang/Object;Lio/flutter/plugin/common/MethodChannel$Result;)V",
      lambda a: _invoke_with_cb(stubs, a))

    MCall = "Lio/flutter/plugin/common/MethodCall;"
    R(MCall, "<init>", "(Ljava/lang/String;Ljava/lang/Object;)V",
      lambda a: a[0].fields.update({"method": a[1], "arguments": a[2] if len(a) > 2 else None}) if a and isinstance(a[0], JObject) else None)
    R(MCall, "argument", "(Ljava/lang/String;)Ljava/lang/Object;", lambda a: _mcall_arg(a))
    R(MCall, "hasArgument", "(Ljava/lang/String;)Z", lambda a: 1 if _mcall_has(a) else 0)
    R(MCall, "arguments", "()Ljava/lang/Object;",
      lambda a: a[0].fields.get("arguments") if a and isinstance(a[0], JObject) else None)

    MRes = "Lio/flutter/plugin/common/MethodChannel$Result;"
    R(MRes, "success", "(Ljava/lang/Object;)V",
      lambda a: a[0].fields.update({"_status": "success", "_value": _to_py(a[1] if len(a) > 1 else None)}) if a and isinstance(a[0], JObject) else None)
    R(MRes, "error", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Object;)V",
      lambda a: a[0].fields.update({"_status": "error", "_code": _s(a[1]), "_msg": _s(a[2]), "_details": _to_py(a[3] if len(a) > 3 else None)}) if a and isinstance(a[0], JObject) else None)
    R(MRes, "notImplemented", "()V",
      lambda a: a[0].fields.update({"_status": "notImplemented"}) if a and isinstance(a[0], JObject) else None)

    EC = "Lio/flutter/plugin/common/EventChannel;"
    R(EC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;)V", _mc_init)
    R(EC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;Lio/flutter/plugin/common/MethodCodec;)V", _mc_init)
    R(EC, "setStreamHandler", "(Lio/flutter/plugin/common/EventChannel$StreamHandler;)V",
      lambda a: stubs._flutter_event_channels.__setitem__(a[0].fields.get("_channel", ""), a[1]) if a and len(a) > 1 and isinstance(a[0], JObject) else None)

    BMC = "Lio/flutter/plugin/common/BasicMessageChannel;"
    R(BMC, "<init>", "(Lio/flutter/plugin/common/BinaryMessenger;Ljava/lang/String;Lio/flutter/plugin/common/MessageCodec;)V", _mc_init)
    R(BMC, "setMessageHandler", "(Lio/flutter/plugin/common/BasicMessageChannel$MessageHandler;)V",
      lambda a: stubs._flutter_message_channels.__setitem__(a[0].fields.get("_channel", ""), a[1]) if a and len(a) > 1 and isinstance(a[0], JObject) else None)
    R(BMC, "send", "(Ljava/lang/Object;)V",
      lambda a: invoke_flutter_channel(stubs, a[0].fields.get("_channel", "") if a and isinstance(a[0], JObject) else "", "__message__", a[1] if len(a) > 1 else None))

    FE = "Lio/flutter/embedding/engine/FlutterEngine;"
    DE = "Lio/flutter/embedding/engine/dart/DartExecutor;"
    R(FE, "getDartExecutor", f"(){DE}", lambda a: JObject(DE, {}))
    R(DE, "getBinaryMessenger", "()Lio/flutter/plugin/common/BinaryMessenger;",
      lambda a: JObject("Lio/flutter/plugin/common/BinaryMessenger;", {}))


def _mcall_arg(a):
    if not a or not isinstance(a[0], JObject) or len(a) < 2:
        return None
    key = _s(a[1]) or ""
    args_obj = a[0].fields.get("arguments")
    if isinstance(args_obj, JObject):
        m = args_obj.fields.get("map")
        if isinstance(m, dict):
            return m.get(key)
    return None


def _mcall_has(a) -> bool:
    if not a or not isinstance(a[0], JObject) or len(a) < 2:
        return False
    key = _s(a[1]) or ""
    args_obj = a[0].fields.get("arguments")
    if isinstance(args_obj, JObject):
        m = args_obj.fields.get("map")
        if isinstance(m, dict):
            return key in m
    return False


def _invoke_with_cb(stubs, a):
    ch = a[0].fields.get("_channel", "") if a and isinstance(a[0], JObject) else ""
    m = _s(a[1]) or "" if len(a) > 1 else ""
    arg = a[2] if len(a) > 2 else None
    cb = a[3] if len(a) > 3 else None
    out = invoke_flutter_channel(stubs, ch, m, arg)
    vm = getattr(stubs, "vm", None)
    if isinstance(cb, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
        try:
            vm.invoke_virtual(cb, "success", "(Ljava/lang/Object;)V", (cb, _to_java(out.get("result"))))
        except Exception:
            pass
    return None


def _builtin_platform_channel(stubs: Any, channel: str, method: str, arguments: Any) -> Optional[Dict[str, Any]]:
    py_args = _to_py(arguments) if isinstance(arguments, JObject) else arguments
    if channel == "plugins.flutter.io/shared_preferences":
        store = stubs.__dict__.setdefault("_prefs_store", {}).setdefault("FlutterSharedPreferences", {})
        if method == "getAll":
            return {"status": "success", "result": {k: _to_py(v) for k, v in store.items()}}
        if method in ("setString", "setInt", "setDouble", "setBool") and isinstance(py_args, dict):
            k = str(py_args.get("key") or "")
            v = py_args.get("value")
            if k:
                store[k] = JString(v) if isinstance(v, str) else v
            return {"status": "success", "result": True}
        if method == "remove" and isinstance(py_args, dict):
            store.pop(str(py_args.get("key") or ""), None)
            return {"status": "success", "result": True}
        if method == "clear":
            store.clear()
            return {"status": "success", "result": True}
    if channel in ("plugins.flutter.io/package_info", "dev.fluttercommunity.plus/package_info") and method == "getAll":
        prof = getattr(stubs, "profile", None)
        pkg = getattr(prof, "package", "com.example.app") if prof else "com.example.app"
        return {
            "status": "success",
            "result": {"appName": pkg.rsplit(".", 1)[-1], "packageName": pkg, "version": "1.0.0", "buildNumber": "1"},
        }
    if channel == "plugins.flutter.io/path_provider":
        prof = getattr(stubs, "profile", None)
        pkg = getattr(prof, "package", "com.example.app") if prof else "com.example.app"
        if method == "getApplicationDocumentsDirectory":
            return {"status": "success", "result": f"/data/user/0/{pkg}/app_flutter"}
        if method == "getTemporaryDirectory":
            return {"status": "success", "result": f"/data/user/0/{pkg}/cache"}
    if channel == "plugins.it_nomads.com/flutter_secure_storage":
        sec = stubs.__dict__.setdefault("_prefs_store", {}).setdefault("flutter_secure_storage", {})
        if method == "write" and isinstance(py_args, dict):
            k = str(py_args.get("key") or "")
            if k:
                sec[k] = str(py_args.get("value") or "")
            return {"status": "success", "result": None}
        if method == "read" and isinstance(py_args, dict):
            val = sec.get(str(py_args.get("key") or ""))
            return {"status": "success", "result": _to_py(val)}
        if method == "readAll":
            return {"status": "success", "result": {k: _to_py(v) for k, v in sec.items()}}
        if method == "containsKey" and isinstance(py_args, dict):
            return {"status": "success", "result": str(py_args.get("key") or "") in sec}
        if method == "delete" and isinstance(py_args, dict):
            sec.pop(str(py_args.get("key") or ""), None)
            return {"status": "success", "result": None}
        if method == "deleteAll":
            sec.clear()
            return {"status": "success", "result": None}
    if channel == "plugins.flutter.io/url_launcher":
        if method == "canLaunch":
            return {"status": "success", "result": True}
        if method in ("launch", "launchUrl") and isinstance(py_args, dict):
            u = str(py_args.get("url") or "")
            tr = getattr(stubs, "view_tree", None)
            if tr is not None:
                tr.__dict__.setdefault("web_events", []).append({"kind": "flutter_url_launcher", "url": u})
            dl = getattr(stubs, "_g10_deliver_deeplink", None)
            if callable(dl) and "://" in u and not u.startswith(("http://", "https://")):
                dl(u)
            return {"status": "success", "result": True}
    if channel in ("dev.fluttercommunity.plus/device_info", "plugins.flutter.io/device_info") and method in ("getAndroidDeviceInfo", "deviceInfo"):
        return {"status": "success", "result": {"model": "Pixel 8", "manufacturer": "Google", "brand": "google", "version": {"sdkInt": 34, "release": "14"}, "isPhysicalDevice": True}}
    if channel in ("dev.fluttercommunity.plus/connectivity", "plugins.flutter.io/connectivity") and method == "check":
        return {"status": "success", "result": "wifi"}
    if channel == "flutter/navigation":
        return {"status": "success", "result": None}
    if channel == "flutter/platform":
        clip = stubs.__dict__.setdefault("_flutter_clipboard", "")
        if method == "Clipboard.setData" and isinstance(py_args, dict):
            stubs._flutter_clipboard = str(py_args.get("text") or "")
            return {"status": "success", "result": None}
        if method == "Clipboard.getData":
            return {"status": "success", "result": {"text": clip}}
        if method in ("SystemNavigator.pop", "SystemChrome.setPreferredOrientations", "HapticFeedback.vibrate"):
            return {"status": "success", "result": None}
    return None


def invoke_flutter_channel(stubs: Any, channel: str, method: str, arguments: Any = None) -> Dict[str, Any]:
    """Ruft registrierten Java-MethodCallHandler oder Builtin-Plattform-Kanal synchron auf."""
    if stubs is None:
        return {"status": "notImplemented", "result": None}
    ch = str(channel or "")
    m = str(method or "")
    handler = (getattr(stubs, "_flutter_method_channels", None) or {}).get(ch)
    out: Dict[str, Any] = {"channel": ch, "method": m, "arguments": _to_py(arguments), "status": "notImplemented", "result": None}
    vm = getattr(stubs, "vm", None)
    if isinstance(handler, JObject) and vm is not None and hasattr(vm, "invoke_virtual"):
        jarg = arguments if isinstance(arguments, (JObject, JString)) else _to_java(arguments)
        call_obj = JObject("Lio/flutter/plugin/common/MethodCall;", {"method": JString(m), "arguments": jarg})
        res_obj = JObject("Lio/flutter/plugin/common/MethodChannel$Result;", {"_status": "pending", "_value": None})
        try:
            vm.invoke_virtual(
                handler,
                "onMethodCall",
                "(Lio/flutter/plugin/common/MethodCall;Lio/flutter/plugin/common/MethodChannel$Result;)V",
                (handler, call_obj, res_obj),
            )
            st = res_obj.fields.get("_status")
            if st != "pending":
                out["status"] = st
                out["result"] = res_obj.fields.get("_value")
                if st == "error":
                    out["error_code"] = res_obj.fields.get("_code")
                    out["error_message"] = res_obj.fields.get("_msg")
        except Exception as e:
            out["status"] = "error"
            out["error_message"] = repr(e)
    if out["status"] == "notImplemented":
        builtin = _builtin_platform_channel(stubs, ch, m, arguments)
        if builtin is not None:
            out.update(builtin)
    stubs.__dict__.setdefault("_flutter_channel_log", []).append(out)
    return out
