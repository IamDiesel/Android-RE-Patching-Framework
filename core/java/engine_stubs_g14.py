# -*- coding: utf-8 -*-
"""core.java.engine_stubs_g14 — G14: Systematische Stub-Ausarbeitungen fuer alle 6 Engines.

Erweitert additiv (ohne bestehende DEX-Klassen oder Cert/UUID-Vertraege zu ueberschreiben):
  1. JDK & Datenformate: org.json (JSONObject/JSONArray), java.util.Base64, BigDecimal/BigInteger,
     ByteBuffer/ByteOrder, GZIPInputStream/GZIPOutputStream, PackageManager/ApplicationInfo/UUID.
  2. Android View & System: ValueAnimator/ObjectAnimator/AnimatorSet/ViewPropertyAnimator,
     ListAdapter (RecyclerView), Bitmap/BitmapFactory/Rect/RectF, Toast, ClipboardManager, InputMethodManager.
  3. Jetpack Compose: derivedStateOf, mutableIntStateOf/Long/Float, rememberSaveable,
     DisposableEffect/SideEffect/rememberCoroutineScope, AnimatedVisibility, Crossfade,
     CompositionLocals (LocalDensity, LocalConfiguration, LocalFocusManager, LocalUriHandler,
     LocalSoftwareKeyboardController).
  4. React Native: Builtin-NativeModules Registry (`get_rn_builtin_modules`).
"""
from __future__ import annotations

import base64
from decimal import Decimal, ROUND_HALF_UP
import gzip
import io
import json
import os
import re
import struct
from typing import Any, Dict, List, Optional
import uuid as _py_uuid

from core.java.objects import JArray, JObject, JString
from core.java.sdk_breadth_g15 import (
    bb_buf,
    bb_is_big,
    bb_limit,
    bb_make,
    bb_order_is_big,
    bb_pos,
    bb_set_big,
    bb_set_limit,
    bb_set_pos,
)

_SIG_CACHE: Dict[str, List[str]] = {}


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, JString):
        return v.text or ""
    if isinstance(v, JObject):
        if "text" in v.fields and isinstance(v.fields["text"], str):
            return v.fields["text"]
        if "$str" in v.fields:
            return str(v.fields["$str"])
        if "$val" in v.fields:
            return _txt(v.fields["$val"])
        if v.cls == "Ljava/lang/String;":
            return str(v.fields.get("value", ""))
    return str(v)


def _bytes_from(v: Any) -> bytes:
    if v is None:
        return b""
    data = getattr(v, "data", None)
    if data is not None:
        return bytes(int(x) & 0xFF for x in data)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, JString):
        return (v.text or "").encode("utf-8", errors="replace")
    return b""


def _g14_read_stream_bytes(stubs: Any, stream_obj: Any) -> bytes:
    if not isinstance(stream_obj, JObject):
        return b""
    f = stream_obj.fields
    for key in ("_mem", "$data", "buf", "_buf", "data"):
        val = f.get(key)
        if val is not None and len(val) > 0:
            raw = _bytes_from(val)
            pos = int(f.get("pos", f.get("_pos", f.get("$pos", 0))) or 0)
            return raw[pos:] if pos > 0 else raw
    p = f.get("path")
    if p:
        sh_fs = getattr(stubs, "_shared_fs", None)
        if sh_fs is not None:
            ino = sh_fs.lookup(str(p))
            if ino is not None:
                raw = bytes(sh_fs.read(ino))
                pos = int(f.get("pos", 0) or 0)
                return raw[pos:] if pos > 0 else raw
        vfs = getattr(stubs, "vfs", None)
        if vfs is not None and hasattr(vfs, "read_file"):
            raw = bytes(vfs.read_file(str(p)) or b"")
            pos = int(f.get("pos", 0) or 0)
            return raw[pos:] if pos > 0 else raw
    chunks = bytearray()
    tmp = JArray("[B", [0] * 4096)
    for _ in range(1024):
        ok, n = stubs.resolve(
            stream_obj.cls, "read", "([B)I", [stream_obj, tmp], False,
            allow_default=False, allow_by_name=False,
        )
        if not ok or not isinstance(n, int) or n <= 0:
            break
        chunks.extend(int(b) & 0xFF for b in tmp.data[:n])
    if chunks:
        return bytes(chunks)
    for key in ("_mem", "$data", "buf", "_buf", "data"):
        if key in f and f[key] is not None:
            return _bytes_from(f[key])
    return b""


def _wrap_json_to_py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, JString):
        return v.text
    if isinstance(v, JObject):
        if "$jmap" in v.fields:
            return dict(v.fields["$jmap"])
        if "$jarr" in v.fields:
            return list(v.fields["$jarr"])
        if v.cls == "Ljava/lang/Boolean;":
            return bool(v.fields.get("value"))
        if "value" in v.fields and v.cls.startswith("Ljava/lang/"):
            return v.fields["value"]
        if "map" in v.fields and isinstance(v.fields["map"], dict):
            return {str(_txt(k)): _wrap_json_to_py(val) for k, val in v.fields["map"].items()}
        if "__items" in v.fields and isinstance(v.fields["__items"], list):
            return [_wrap_json_to_py(x) for x in v.fields["__items"]]
    if isinstance(v, JArray):
        return [_wrap_json_to_py(x) for x in (v.data or ())]
    if isinstance(v, (bool, int, float, str)):
        return v
    return _txt(v)


def _py_to_java_json(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return JString(v)
    if isinstance(v, dict):
        return JObject("Lorg/json/JSONObject;", {"$jmap": v})
    if isinstance(v, list):
        return JObject("Lorg/json/JSONArray;", {"$jarr": v})
    return JString(str(v))


def _box_java_obj(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return JObject("Ljava/lang/Boolean;", {"value": 1 if v else 0})
    if isinstance(v, int):
        return JObject("Ljava/lang/Integer;", {"value": v})
    if isinstance(v, float):
        return JObject("Ljava/lang/Double;", {"value": v})
    return _py_to_java_json(v)


def _parse_png_jpeg_dims(raw: bytes) -> tuple[int, int]:
    if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
        w, h = struct.unpack(">II", raw[16:24])
        if 0 < w <= 16384 and 0 < h <= 16384:
            return int(w), int(h)
    if len(raw) >= 10 and raw[:3] == b"GIF":
        w, h = struct.unpack("<HH", raw[6:10])
        if 0 < w <= 16384 and 0 < h <= 16384:
            return int(w), int(h)
    if len(raw) >= 4 and raw[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                if 0 < w <= 16384 and 0 < h <= 16384:
                    return int(w), int(h)
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                i += 2
                continue
            seg_len = struct.unpack(">H", raw[i + 2:i + 4])[0]
            if seg_len < 2:
                break
            i += 2 + seg_len
    return 64, 64


def _sig_param_types(sig: str) -> List[str]:
    cached = _SIG_CACHE.get(sig)
    if cached is not None:
        return cached
    inner = sig[sig.index("(") + 1:sig.index(")")] if "(" in sig and ")" in sig else ""
    out: List[str] = []
    i = 0
    while i < len(inner):
        st = i
        while i < len(inner) and inner[i] == "[":
            i += 1
        if i < len(inner) and inner[i] == "L":
            end = inner.find(";", i)
            i = (end + 1) if end >= 0 else len(inner)
        elif i < len(inner):
            i += 1
        out.append(inner[st:i])
    _SIG_CACHE[sig] = out
    return out


def install_g14_stubs(stubs: Any) -> None:
    """Registriert fehlende G14-Stubs additiv auf `AndroidStubs`."""
    if stubs is None or getattr(stubs, "_g14_installed", False):
        return
    stubs._g14_installed = True

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            stubs.register(cls, name, sig, fn)

    # ---- 1. org.json.JSONObject & JSONArray ---------------------------------
    JO = "Lorg/json/JSONObject;"
    JA = "Lorg/json/JSONArray;"

    def _jmap(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, JObject):
            m = obj.fields.get("$jmap")
            if not isinstance(m, dict):
                m = {}
                obj.fields["$jmap"] = m
            return m
        return {}

    def _jarr(obj: Any) -> List[Any]:
        if isinstance(obj, JObject):
            arr = obj.fields.get("$jarr")
            if not isinstance(arr, list):
                arr = []
                obj.fields["$jarr"] = arr
            return arr
        return []

    def _jo_init(a):
        if not a or not isinstance(a[0], JObject):
            return None
        if len(a) == 1:
            a[0].fields["$jmap"] = {}
        elif isinstance(a[1], JString) or isinstance(a[1], str):
            try:
                parsed = json.loads(_txt(a[1]) or "{}")
                a[0].fields["$jmap"] = dict(parsed) if isinstance(parsed, dict) else {}
            except Exception:
                a[0].fields["$jmap"] = {}
        elif isinstance(a[1], JObject):
            a[0].fields["$jmap"] = dict(_wrap_json_to_py(a[1]) or {})
        else:
            a[0].fields["$jmap"] = {}
        return a[0]

    def _ja_init(a):
        if not a or not isinstance(a[0], JObject):
            return None
        if len(a) == 1:
            a[0].fields["$jarr"] = []
        elif isinstance(a[1], (JString, str)):
            try:
                parsed = json.loads(_txt(a[1]) or "[]")
                a[0].fields["$jarr"] = list(parsed) if isinstance(parsed, list) else []
            except Exception:
                a[0].fields["$jarr"] = []
        elif isinstance(a[1], (JObject, JArray)):
            val = _wrap_json_to_py(a[1])
            a[0].fields["$jarr"] = list(val) if isinstance(val, list) else []
        else:
            a[0].fields["$jarr"] = []
        return a[0]

    for _sig in ("()V", "(Ljava/lang/String;)V", "(Ljava/util/Map;)V"):
        _add(JO, "<init>", _sig, _jo_init)
    for _sig in ("()V", "(Ljava/lang/String;)V", "(Ljava/util/Collection;)V", "(Ljava/lang/Object;)V"):
        _add(JA, "<init>", _sig, _ja_init)

    def _jo_put(a):
        if len(a) > 2 and isinstance(a[0], JObject):
            _jmap(a[0])[_txt(a[1])] = _wrap_json_to_py(a[2])
        return a[0] if a else None

    for _pt in ("Ljava/lang/Object;", "Ljava/lang/String;", "I", "J", "D", "Z"):
        _add(JO, "put", f"(Ljava/lang/String;{_pt})Lorg/json/JSONObject;", _jo_put)
        _add(JO, "putOpt", f"(Ljava/lang/String;{_pt})Lorg/json/JSONObject;", _jo_put)

    def _jo_get_raw(a, default=None):
        if len(a) < 2 or not isinstance(a[0], JObject):
            return default
        k = _txt(a[1])
        m = _jmap(a[0])
        if k in m and m[k] is not None:
            return m[k]
        return default

    _add(JO, "opt", "(Ljava/lang/String;)Ljava/lang/Object;", lambda a: _box_java_obj(_jo_get_raw(a)))
    _add(JO, "get", "(Ljava/lang/String;)Ljava/lang/Object;", lambda a: _box_java_obj(_jo_get_raw(a)))
    _add(JO, "optString", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString("" if (v := _jo_get_raw(a)) is None else str(v)))
    _add(JO, "optString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
         lambda a: JString(str(_jo_get_raw(a)) if _jo_get_raw(a) is not None else (_txt(a[2]) if len(a) > 2 else "")))
    _add(JO, "getString", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString("" if (v := _jo_get_raw(a)) is None else str(v)))
    _add(JO, "optInt", "(Ljava/lang/String;)I", lambda a: int(_jo_get_raw(a, 0) or 0))
    _add(JO, "optInt", "(Ljava/lang/String;I)I", lambda a: int(_jo_get_raw(a)) if _jo_get_raw(a) is not None else (int(a[2]) if len(a) > 2 else 0))
    _add(JO, "getInt", "(Ljava/lang/String;)I", lambda a: int(_jo_get_raw(a, 0) or 0))
    _add(JO, "optLong", "(Ljava/lang/String;)J", lambda a: int(_jo_get_raw(a, 0) or 0))
    _add(JO, "optLong", "(Ljava/lang/String;J)J", lambda a: int(_jo_get_raw(a)) if _jo_get_raw(a) is not None else (int(a[2]) if len(a) > 2 else 0))
    _add(JO, "getLong", "(Ljava/lang/String;)J", lambda a: int(_jo_get_raw(a, 0) or 0))
    _add(JO, "optDouble", "(Ljava/lang/String;)D", lambda a: float(_jo_get_raw(a, 0.0) or 0.0))
    _add(JO, "optDouble", "(Ljava/lang/String;D)D", lambda a: float(_jo_get_raw(a)) if _jo_get_raw(a) is not None else (float(a[2]) if len(a) > 2 else 0.0))
    _add(JO, "getDouble", "(Ljava/lang/String;)D", lambda a: float(_jo_get_raw(a, 0.0) or 0.0))
    _add(JO, "optBoolean", "(Ljava/lang/String;)Z", lambda a: 1 if _jo_get_raw(a, False) else 0)
    _add(JO, "optBoolean", "(Ljava/lang/String;Z)Z", lambda a: (1 if _jo_get_raw(a) else 0) if _jo_get_raw(a) is not None else (1 if (len(a) > 2 and a[2]) else 0))
    _add(JO, "getBoolean", "(Ljava/lang/String;)Z", lambda a: 1 if _jo_get_raw(a, False) else 0)
    _add(JO, "optJSONObject", "(Ljava/lang/String;)Lorg/json/JSONObject;",
         lambda a: JObject(JO, {"$jmap": _jo_get_raw(a)}) if isinstance(_jo_get_raw(a), dict) else None)
    _add(JO, "getJSONObject", "(Ljava/lang/String;)Lorg/json/JSONObject;",
         lambda a: JObject(JO, {"$jmap": _jo_get_raw(a) if isinstance(_jo_get_raw(a), dict) else {}}))
    _add(JO, "optJSONArray", "(Ljava/lang/String;)Lorg/json/JSONArray;",
         lambda a: JObject(JA, {"$jarr": _jo_get_raw(a)}) if isinstance(_jo_get_raw(a), list) else None)
    _add(JO, "getJSONArray", "(Ljava/lang/String;)Lorg/json/JSONArray;",
         lambda a: JObject(JA, {"$jarr": _jo_get_raw(a) if isinstance(_jo_get_raw(a), list) else []}))
    _add(JO, "has", "(Ljava/lang/String;)Z", lambda a: 1 if (len(a) > 1 and _txt(a[1]) in _jmap(a[0])) else 0)
    _add(JO, "isNull", "(Ljava/lang/String;)Z", lambda a: 1 if (len(a) < 2 or _jmap(a[0]).get(_txt(a[1])) is None) else 0)
    _add(JO, "remove", "(Ljava/lang/String;)Ljava/lang/Object;",
         lambda a: _box_java_obj(_jmap(a[0]).pop(_txt(a[1]), None)) if len(a) > 1 else None)
    _add(JO, "length", "()I", lambda a: len(_jmap(a[0])) if a else 0)
    _add(JO, "keys", "()Ljava/util/Iterator;",
         lambda a: JObject("Ljava/util/Iterator;", {"__items": [JString(k) for k in _jmap(a[0]).keys()], "__pos": 0}) if a else None)
    _add(JO, "names", "()Lorg/json/JSONArray;",
         lambda a: JObject(JA, {"$jarr": list(_jmap(a[0]).keys())}) if a else JObject(JA, {"$jarr": []}))
    _add(JO, "toString", "()Ljava/lang/String;", lambda a: JString(json.dumps(_jmap(a[0]), ensure_ascii=False)) if a else JString("{}"))
    _add(JO, "toString", "(I)Ljava/lang/String;",
         lambda a: JString(json.dumps(_jmap(a[0]), indent=max(1, int(a[1])) if len(a) > 1 else None, ensure_ascii=False)) if a else JString("{}"))
    _add(JO, "quote", "(Ljava/lang/String;)Ljava/lang/String;",
         lambda a: JString(json.dumps(_txt(a[0]))) if a and a[0] is not None else JString('""'))

    def _ja_put(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            _jarr(a[0]).append(_wrap_json_to_py(a[1]))
        return a[0] if a else None

    for _pt in ("Ljava/lang/Object;", "Ljava/lang/String;", "I", "J", "D", "Z"):
        _add(JA, "put", f"({_pt})Lorg/json/JSONArray;", _ja_put)
    _add(JA, "length", "()I", lambda a: len(_jarr(a[0])) if a else 0)
    _add(JA, "opt", "(I)Ljava/lang/Object;",
         lambda a: _box_java_obj(_jarr(a[0])[int(a[1])]) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else None)
    _add(JA, "get", "(I)Ljava/lang/Object;",
         lambda a: _box_java_obj(_jarr(a[0])[int(a[1])]) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else None)
    _add(JA, "optString", "(I)Ljava/lang/String;",
         lambda a: JString(str(_jarr(a[0])[int(a[1])])) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else JString(""))
    _add(JA, "getString", "(I)Ljava/lang/String;",
         lambda a: JString(str(_jarr(a[0])[int(a[1])])) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else JString(""))
    _add(JA, "optInt", "(I)I",
         lambda a: int(_jarr(a[0])[int(a[1])] or 0) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else 0)
    _add(JA, "getInt", "(I)I",
         lambda a: int(_jarr(a[0])[int(a[1])] or 0) if len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) else 0)
    _add(JA, "optJSONObject", "(I)Lorg/json/JSONObject;",
         lambda a: JObject(JO, {"$jmap": _jarr(a[0])[int(a[1])]}) if (len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) and isinstance(_jarr(a[0])[int(a[1])], dict)) else None)
    _add(JA, "getJSONObject", "(I)Lorg/json/JSONObject;",
         lambda a: JObject(JO, {"$jmap": _jarr(a[0])[int(a[1])] if (len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) and isinstance(_jarr(a[0])[int(a[1])], dict)) else {}}))
    _add(JA, "optJSONArray", "(I)Lorg/json/JSONArray;",
         lambda a: JObject(JA, {"$jarr": _jarr(a[0])[int(a[1])]}) if (len(a) > 1 and 0 <= int(a[1]) < len(_jarr(a[0])) and isinstance(_jarr(a[0])[int(a[1])], list)) else None)
    _add(JA, "toString", "()Ljava/lang/String;", lambda a: JString(json.dumps(_jarr(a[0]), ensure_ascii=False)) if a else JString("[]"))

    # ---- 2. java.util.Base64 & android.util.Base64.encode ------------------
    _add("Landroid/util/Base64;", "encode", "([BI)[B", lambda a: JArray("[B", list(
        (base64.urlsafe_b64encode(_bytes_from(a[0])) if (len(a) > 1 and int(a[1]) & 8) else base64.b64encode(_bytes_from(a[0])))
        if a else b""
    )))
    JU_ENC = "Ljava/util/Base64$Encoder;"
    JU_DEC = "Ljava/util/Base64$Decoder;"
    _add("Ljava/util/Base64;", "getEncoder", f"(){JU_ENC}", lambda a: JObject(JU_ENC, {"_url": False, "_pad": True}))
    _add("Ljava/util/Base64;", "getUrlEncoder", f"(){JU_ENC}", lambda a: JObject(JU_ENC, {"_url": True, "_pad": True}))
    _add("Ljava/util/Base64;", "getMimeEncoder", f"(){JU_ENC}", lambda a: JObject(JU_ENC, {"_url": False, "_pad": True}))
    _add("Ljava/util/Base64;", "getDecoder", f"(){JU_DEC}", lambda a: JObject(JU_DEC, {"_url": False}))
    _add("Ljava/util/Base64;", "getUrlDecoder", f"(){JU_DEC}", lambda a: JObject(JU_DEC, {"_url": True}))
    _add("Ljava/util/Base64;", "getMimeDecoder", f"(){JU_DEC}", lambda a: JObject(JU_DEC, {"_url": False}))
    _add(JU_ENC, "withoutPadding", f"(){JU_ENC}",
         lambda a: JObject(JU_ENC, {"_url": bool(a[0].fields.get("_url")), "_pad": False}) if a and isinstance(a[0], JObject) else JObject(JU_ENC, {"_pad": False}))

    def _ju_enc_b(a):
        raw = _bytes_from(a[1]) if len(a) > 1 else b""
        url = bool(a[0].fields.get("_url")) if a and isinstance(a[0], JObject) else False
        pad = bool(a[0].fields.get("_pad", True)) if a and isinstance(a[0], JObject) else True
        out = base64.urlsafe_b64encode(raw) if url else base64.b64encode(raw)
        if not pad:
            out = out.rstrip(b"=")
        return out

    _add(JU_ENC, "encode", "([B)[B", lambda a: JArray("[B", list(_ju_enc_b(a))))
    _add(JU_ENC, "encodeToString", "([B)Ljava/lang/String;", lambda a: JString(_ju_enc_b(a).decode("ascii")))

    def _ju_dec_b(a):
        if len(a) < 2:
            return JArray("[B", [])
        raw = _txt(a[1]).encode("ascii", "ignore") if isinstance(a[1], (JString, str)) else _bytes_from(a[1])
        raw_p = raw + b"=" * ((4 - len(raw) % 4) % 4)
        url = bool(a[0].fields.get("_url")) if a and isinstance(a[0], JObject) else False
        try:
            dec = base64.urlsafe_b64decode(raw_p) if (url or b"-" in raw or b"_" in raw) else base64.b64decode(raw_p)
        except Exception:
            dec = b""
        return JArray("[B", list(dec))

    _add(JU_DEC, "decode", "(Ljava/lang/String;)[B", _ju_dec_b)
    _add(JU_DEC, "decode", "([B)[B", _ju_dec_b)

    # ---- 3. UUID Zusatzmethoden (ohne randomUUID/toString zu ueberschreiben) -
    UUID = "Ljava/util/UUID;"
    _add(UUID, "fromString", "(Ljava/lang/String;)Ljava/util/UUID;",
         lambda a: JObject(UUID, {"text": str(_py_uuid.UUID(_txt(a[0])))}) if a else JObject(UUID, {"text": "00000000-0000-4000-8000-000000000001"}))
    _add(UUID, "equals", "(Ljava/lang/Object;)Z",
         lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) and a[0].fields.get("text") == a[1].fields.get("text")) else 0)
    _add(UUID, "hashCode", "()I",
         lambda a: hash(a[0].fields.get("text", "")) & 0x7FFFFFFF if a and isinstance(a[0], JObject) else 0)
    _add(UUID, "getMostSignificantBits", "()J",
         lambda a: int(_py_uuid.UUID(a[0].fields.get("text", "00000000-0000-4000-8000-000000000001")).int >> 64) if a and isinstance(a[0], JObject) else 0)
    _add(UUID, "getLeastSignificantBits", "()J",
         lambda a: int(_py_uuid.UUID(a[0].fields.get("text", "00000000-0000-4000-8000-000000000001")).int & ((1 << 64) - 1)) if a and isinstance(a[0], JObject) else 0)

    # ---- 4. BigDecimal & BigInteger -----------------------------------------
    BD = "Ljava/math/BigDecimal;"
    BI = "Ljava/math/BigInteger;"

    def _dec(o: Any) -> Decimal:
        if isinstance(o, JObject) and "_dec" in o.fields:
            return o.fields["_dec"]
        try:
            return Decimal(str(_txt(o) or "0"))
        except Exception:
            return Decimal(0)

    for _bsig in ("(Ljava/lang/String;)V", "(I)V", "(J)V", "(D)V"):
        _add(BD, "<init>", _bsig, lambda a: (a[0].fields.__setitem__("_dec", Decimal(str(_txt(a[1]) if len(a) > 1 else 0))), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(BD, "valueOf", "(D)Ljava/math/BigDecimal;", lambda a: JObject(BD, {"_dec": Decimal(str(a[0] if a else 0))}))
    _add(BD, "valueOf", "(J)Ljava/math/BigDecimal;", lambda a: JObject(BD, {"_dec": Decimal(int(a[0] if a else 0))}))
    _add(BD, "add", f"({BD}){BD}", lambda a: JObject(BD, {"_dec": _dec(a[0]) + _dec(a[1])}))
    _add(BD, "subtract", f"({BD}){BD}", lambda a: JObject(BD, {"_dec": _dec(a[0]) - _dec(a[1])}))
    _add(BD, "multiply", f"({BD}){BD}", lambda a: JObject(BD, {"_dec": _dec(a[0]) * _dec(a[1])}))
    _add(BD, "divide", f"({BD}){BD}", lambda a: JObject(BD, {"_dec": _dec(a[0]) / (_dec(a[1]) or Decimal(1))}))
    _add(BD, "setScale", f"(II){BD}", lambda a: JObject(BD, {"_dec": _dec(a[0]).quantize(Decimal(10) ** (-int(a[1])), rounding=ROUND_HALF_UP)}))
    _add(BD, "compareTo", f"({BD})I", lambda a: -1 if _dec(a[0]) < _dec(a[1]) else (1 if _dec(a[0]) > _dec(a[1]) else 0))
    _add(BD, "doubleValue", "()D", lambda a: float(_dec(a[0])))
    _add(BD, "floatValue", "()F", lambda a: float(_dec(a[0])))
    _add(BD, "intValue", "()I", lambda a: int(_dec(a[0])))
    _add(BD, "longValue", "()J", lambda a: int(_dec(a[0])))
    _add(BD, "toPlainString", "()Ljava/lang/String;", lambda a: JString(format(_dec(a[0]), "f")))
    _add(BD, "toString", "()Ljava/lang/String;", lambda a: JString(str(_dec(a[0]))))

    _add(BI, "<init>", "(Ljava/lang/String;)V", lambda a: (a[0].fields.__setitem__("_bi", int(_txt(a[1]) or "0")), a[0])[1] if a else None)
    _add(BI, "<init>", "([B)V", lambda a: (a[0].fields.__setitem__("_bi", int.from_bytes(_bytes_from(a[1]), "big", signed=True)), a[0])[1] if a else None)
    _add(BI, "valueOf", "(J)Ljava/math/BigInteger;", lambda a: JObject(BI, {"_bi": int(a[0] if a else 0)}))
    _add(BI, "toByteArray", "()[B", lambda a: JArray("[B", list(int(a[0].fields.get("_bi", 0)).to_bytes(max(1, (int(a[0].fields.get("_bi", 0)).bit_length() + 8) // 8), "big", signed=True))))
    _add(BI, "intValue", "()I", lambda a: int(a[0].fields.get("_bi", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(BI, "longValue", "()J", lambda a: int(a[0].fields.get("_bi", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(BI, "toString", "()Ljava/lang/String;", lambda a: JString(str(a[0].fields.get("_bi", 0))) if a and isinstance(a[0], JObject) else JString("0"))

    # ---- 5. java.nio.ByteBuffer & ByteOrder (unifiziert via sdk_breadth_g15.bb_*) ---
    BB = "Ljava/nio/ByteBuffer;"
    BO = "Ljava/nio/ByteOrder;"

    def _bb_new(buf: bytearray, limit: Optional[int] = None) -> JObject:
        return bb_make(buf, limit)

    _add(BB, "allocate", "(I)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(max(0, int(a[0] if a else 0)))))
    _add(BB, "allocateDirect", "(I)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(max(0, int(a[0] if a else 0)))))
    _add(BB, "wrap", "([B)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(_bytes_from(a[0])) if a else bytearray()))
    _add(BB, "wrap", "([BII)Ljava/nio/ByteBuffer;", lambda a: (lambda b: _bb_new(bytearray(b[int(a[1]):int(a[1]) + int(a[2])])))(_bytes_from(a[0])) if len(a) > 2 else _bb_new(bytearray()))
    _add(BB, "order", f"({BO}){BB}", lambda a: bb_set_big(a[0], bb_order_is_big(a[1] if len(a) > 1 else None)))
    _add(BB, "position", "()I", lambda a: bb_pos(a[0]))
    _add(BB, "position", f"(I){BB}", lambda a: bb_set_pos(a[0], int(a[1])))
    _add(BB, "limit", "()I", lambda a: bb_limit(a[0]))
    _add(BB, "capacity", "()I", lambda a: len(bb_buf(a[0])))
    _add(BB, "remaining", "()I", lambda a: max(0, bb_limit(a[0]) - bb_pos(a[0])))
    _add(BB, "hasRemaining", "()Z", lambda a: 1 if bb_pos(a[0]) < bb_limit(a[0]) else 0)
    _add(BB, "flip", f"(){BB}", lambda a: (bb_set_limit(a[0], bb_pos(a[0])), bb_set_pos(a[0], 0), a[0])[2])
    _add(BB, "clear", f"(){BB}", lambda a: (bb_set_pos(a[0], 0), bb_set_limit(a[0], len(bb_buf(a[0]))), a[0])[2])
    _add(BB, "rewind", f"(){BB}", lambda a: bb_set_pos(a[0], 0))
    _add(BB, "array", "()[B", lambda a: JArray("[B", list(bb_buf(a[0]))))

    def _bb_read(obj: JObject, fmt: str):
        sz = struct.calcsize(fmt)
        pos = bb_pos(obj)
        buf = bb_buf(obj)
        end = ">" if bb_is_big(obj) else "<"
        chunk = bytes(buf[pos:pos + sz])
        if len(chunk) < sz:
            return 0
        bb_set_pos(obj, pos + sz)
        return struct.unpack(end + fmt, chunk)[0]

    def _bb_write(obj: JObject, fmt: str, val):
        sz = struct.calcsize(fmt)
        pos = bb_pos(obj)
        buf = bb_buf(obj)
        end = ">" if bb_is_big(obj) else "<"
        if pos + sz > len(buf):
            buf.extend(b"\x00" * (pos + sz - len(buf)))
        buf[pos:pos + sz] = struct.pack(end + fmt, val)
        bb_set_pos(obj, pos + sz)
        bb_set_limit(obj, max(bb_limit(obj), pos + sz))
        return obj

    _add(BB, "get", "()B", lambda a: _bb_read(a[0], "b"))
    _add(BB, "getShort", "()S", lambda a: _bb_read(a[0], "h"))
    _add(BB, "getInt", "()I", lambda a: _bb_read(a[0], "i"))
    _add(BB, "getLong", "()J", lambda a: _bb_read(a[0], "q"))
    _add(BB, "getFloat", "()F", lambda a: _bb_read(a[0], "f"))
    _add(BB, "getDouble", "()D", lambda a: _bb_read(a[0], "d"))
    _add(BB, "put", f"(B){BB}", lambda a: _bb_write(a[0], "b", int(a[1])))
    _add(BB, "putShort", f"(S){BB}", lambda a: _bb_write(a[0], "h", int(a[1])))
    _add(BB, "putInt", f"(I){BB}", lambda a: _bb_write(a[0], "i", int(a[1])))
    _add(BB, "putLong", f"(J){BB}", lambda a: _bb_write(a[0], "q", int(a[1])))
    _add(BB, "putFloat", f"(F){BB}", lambda a: _bb_write(a[0], "f", float(a[1])))
    _add(BB, "putDouble", f"(D){BB}", lambda a: _bb_write(a[0], "d", float(a[1])))

    def _bb_get_arr(a):
        pos = bb_pos(a[0])
        buf = bb_buf(a[0])
        dst = getattr(a[1], "data", None)
        if dst is not None:
            n = len(dst)
            for i in range(n):
                dst[i] = buf[pos + i] if pos + i < len(buf) else 0
            bb_set_pos(a[0], pos + n)
        return a[0]

    def _bb_put_arr(a):
        src = _bytes_from(a[1])
        pos = bb_pos(a[0])
        buf = bb_buf(a[0])
        if pos + len(src) > len(buf):
            buf.extend(b"\x00" * (pos + len(src) - len(buf)))
        buf[pos:pos + len(src)] = src
        bb_set_pos(a[0], pos + len(src))
        bb_set_limit(a[0], max(bb_limit(a[0]), pos + len(src)))
        return a[0]

    _add(BB, "get", f"([B){BB}", _bb_get_arr)
    _add(BB, "put", f"([B){BB}", _bb_put_arr)
    vm_ref = getattr(stubs, "vm", None)
    if vm_ref is not None and hasattr(vm_ref, "statics"):
        vm_ref.statics.setdefault(BO, {}).setdefault("BIG_ENDIAN", JObject(BO, {"order": "big", "_le": False}))
        vm_ref.statics.setdefault(BO, {}).setdefault("LITTLE_ENDIAN", JObject(BO, {"order": "little", "_le": True}))

    # ---- 6. GZIPInputStream / GZIPOutputStream (+ ByteArray-Stream Fallback)
    BAIS = "Ljava/io/ByteArrayInputStream;"
    BAOS = "Ljava/io/ByteArrayOutputStream;"
    _add(BAIS, "<init>", "([B)V", lambda a: (a[0].fields.update({"buf": _bytes_from(a[1] if len(a) > 1 else b""), "pos": 0}), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "<init>", "()V", lambda a: (a[0].fields.update({"buf": bytearray()}), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "toByteArray", "()[B", lambda a: JArray("[B", list(a[0].fields.get("buf") or a[0].fields.get("_buf") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))

    GI = "Ljava/util/zip/GZIPInputStream;"
    GO = "Ljava/util/zip/GZIPOutputStream;"

    def _gi_init(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            raw = _g14_read_stream_bytes(stubs, a[1])
            try:
                dec = gzip.decompress(raw)
            except Exception:
                dec = raw
            a[0].fields["_dec"] = dec
            a[0].fields["_pos"] = 0
        return a[0] if a else None

    def _gi_read(a):
        if not a or not isinstance(a[0], JObject):
            return -1
        dec = a[0].fields.get("_dec", b"")
        pos = int(a[0].fields.get("_pos", 0))
        if len(a) == 1:
            if pos >= len(dec):
                return -1
            a[0].fields["_pos"] = pos + 1
            return dec[pos]
        dst = getattr(a[1], "data", None)
        if dst is None or pos >= len(dec):
            return -1
        off = int(a[2]) if len(a) > 2 else 0
        ln = int(a[3]) if len(a) > 3 else len(dst) - off
        take = min(ln, len(dec) - pos, max(0, len(dst) - off))
        for i in range(take):
            dst[off + i] = dec[pos + i]
        a[0].fields["_pos"] = pos + take
        return take if take > 0 else -1

    _add(GI, "<init>", "(Ljava/io/InputStream;)V", _gi_init)
    _add(GI, "<init>", "(Ljava/io/InputStream;I)V", _gi_init)
    _add(GI, "read", "()I", _gi_read)
    _add(GI, "read", "([B)I", _gi_read)
    _add(GI, "read", "([BII)I", _gi_read)
    _add(GI, "close", "()V", lambda a: None)

    def _go_init(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            a[0].fields["_out"] = a[1]
            a[0].fields["_raw"] = bytearray()
        return a[0] if a else None

    def _go_write(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2:
            return None
        buf = a[0].fields.setdefault("_raw", bytearray())
        if isinstance(a[1], int):
            buf.append(a[1] & 0xFF)
        else:
            b = _bytes_from(a[1])
            off = int(a[2]) if len(a) > 2 else 0
            ln = int(a[3]) if len(a) > 3 else len(b) - off
            buf.extend(b[off:off + ln])
        return None

    def _go_finish(a):
        if not a or not isinstance(a[0], JObject):
            return None
        if a[0].fields.get("_done"):
            return None
        a[0].fields["_done"] = True
        comp = gzip.compress(bytes(a[0].fields.get("_raw", b"")))
        out_s = a[0].fields.get("_out")
        if isinstance(out_s, JObject):
            arr = JArray("[B", list(comp))
            ok, _ = stubs.resolve(out_s.cls, "write", "([B)V", [out_s, arr], False, allow_default=False, allow_by_name=False)
            if not ok:
                p = out_s.fields.get("path")
                if p:
                    sh_fs = getattr(stubs, "_shared_fs", None)
                    if sh_fs is not None:
                        sh_fs.add_file(str(p), comp)
                    vfs = getattr(stubs, "vfs", None)
                    if vfs is not None and hasattr(vfs, "write_file"):
                        vfs.write_file(str(p), comp)
                out_s.fields["_mem"] = bytearray(comp)
            if not out_s.fields.get("buf"):
                out_s.fields["buf"] = bytearray(comp)
                out_s.fields["_buf"] = bytearray(comp)
                out_s.fields["data"] = bytearray(comp)
        return None

    _add(GO, "<init>", "(Ljava/io/OutputStream;)V", _go_init)
    _add(GO, "<init>", "(Ljava/io/OutputStream;I)V", _go_init)
    _add(GO, "<init>", "(Ljava/io/OutputStream;Z)V", _go_init)
    _add(GO, "<init>", "(Ljava/io/OutputStream;IZ)V", _go_init)
    _add(GO, "write", "(I)V", _go_write)
    _add(GO, "write", "([B)V", _go_write)
    _add(GO, "write", "([BII)V", _go_write)
    _add(GO, "finish", "()V", _go_finish)
    _add(GO, "close", "()V", _go_finish)

    def _resolve_meta_map(raw_meta: dict) -> dict:
        if not raw_meta:
            return {}
        ints_map = getattr(stubs, "_integers_res_map", None)
        if ints_map is None:
            ints_map = {}
            app_root_m = (
                getattr(stubs, "_app_root", None)
                or getattr(getattr(getattr(stubs, "vm", None), "class_provider", None), "root", None)
            )
            if app_root_m:
                import os as _os
                import xml.etree.ElementTree as _ET
                ip = _os.path.join(str(app_root_m), "res", "values", "integers.xml")
                if _os.path.isfile(ip):
                    try:
                        xroot = _ET.parse(ip).getroot()
                        for el in xroot.findall("integer"):
                            nm = el.get("name")
                            if nm and el.text is not None:
                                try:
                                    ints_map[nm] = int(el.text.strip(), 0)
                                except Exception:
                                    pass
                    except Exception:
                        pass
            stubs._integers_res_map = ints_map
        out = {}
        for k, v in raw_meta.items():
            sv = str(v)
            if sv.startswith("@integer/"):
                iname = sv[len("@integer/"):]
                if iname in ints_map:
                    out[str(k)] = ints_map[iname]
                    continue
            out[str(k)] = JString(sv)
        return out

    # ---- 7. PackageManager Ergaenzungen (ApplicationInfo + metaData + checkPermission)
    for _pm_cls in ("Landroid/content/pm/PackageManager;", "Landroid/app/ApplicationPackageManager;"):
        _add(_pm_cls, "checkPermission", "(Ljava/lang/String;Ljava/lang/String;)I", lambda a: 0)
        def _get_app_info(a):
            pkg = _txt(a[1]) if len(a) > 1 else ""
            pkg_eff = pkg or getattr(getattr(stubs, "profile", None), "package", "com.example.app")
            meta = dict(getattr(stubs, "_manifest_meta_data", {}) or {})
            bd = JObject("Landroid/os/Bundle;", {"map": _resolve_meta_map(meta)})
            app_info = getattr(stubs, "_app_info", None) or {}
            src_dir = app_info.get("sourceDir") or f"/data/app/{pkg_eff}/base.apk"
            pub_dir = app_info.get("publicSourceDir") or src_dir
            data_dir = app_info.get("dataDir") or f"/data/user/0/{pkg_eff}"
            nat_dir = app_info.get("nativeLibraryDir") or f"/data/app/{pkg_eff}/lib/arm64"
            return JObject("Landroid/content/pm/ApplicationInfo;", {
                "packageName": JString(pkg_eff),
                "sourceDir": JString(src_dir),
                "publicSourceDir": JString(pub_dir),
                "dataDir": JString(data_dir),
                "nativeLibraryDir": JString(nat_dir),
                "flags": 0,
                "targetSdkVersion": 34,
                "uid": 10123,
                "metaData": bd,
            })
        _add(_pm_cls, "getApplicationInfo", "(Ljava/lang/String;I)Landroid/content/pm/ApplicationInfo;", _get_app_info)

    # ---- 8. Android Animators, ListAdapter, Bitmap, Rect, Toast, Clipboard --
    VA = "Landroid/animation/ValueAnimator;"
    OA = "Landroid/animation/ObjectAnimator;"
    AS = "Landroid/animation/AnimatorSet;"
    VPA = "Landroid/view/ViewPropertyAnimator;"

    def _run_animator_end(anim_obj: Any) -> None:
        if not isinstance(anim_obj, JObject):
            return
        tgt = anim_obj.fields.get("_target")
        prop = str(anim_obj.fields.get("_prop") or "")
        end_v = anim_obj.fields.get("_end_val", 1.0)
        if isinstance(tgt, JObject) and prop == "alpha":
            vn = tgt.fields.get("$view")
            if vn is not None and hasattr(vn, "props"):
                vn.props["alpha"] = float(end_v)
                tr = getattr(stubs, "view_tree", None)
                if tr is not None:
                    tr.touch()
        vm_cur = getattr(stubs, "vm", None)
        for u in list(anim_obj.fields.get("_update_listeners") or ()):
            if isinstance(u, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                try:
                    vm_cur.invoke_virtual(u, "onAnimationUpdate", f"({VA})V", (u, anim_obj))
                except Exception:
                    pass
        for lst in list(anim_obj.fields.get("_listeners") or ()):
            if isinstance(lst, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                for mnm in ("onAnimationStart", "onAnimationEnd"):
                    try:
                        vm_cur.invoke_virtual(lst, mnm, "(Landroid/animation/Animator;)V", (lst, anim_obj))
                    except Exception:
                        pass
        for cb in list(anim_obj.fields.get("_end_actions") or ()):
            if isinstance(cb, JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
                try:
                    vm_cur.invoke_virtual(cb, "run", "()V", (cb,))
                except Exception:
                    pass

    def _va_of_vals(vals_arg, prop="", tgt=None, cls_d=VA):
        vals = list(getattr(vals_arg, "data", ()) or ())
        end_v = vals[-1] if vals else 1.0
        return JObject(cls_d, {"_target": tgt, "_prop": prop, "_vals": vals, "_end_val": end_v, "_update_listeners": [], "_listeners": [], "_end_actions": []})

    _add(VA, "ofFloat", f"([F){VA}", lambda a: _va_of_vals(a[0] if a else None, cls_d=VA))
    _add(VA, "ofInt", f"([I){VA}", lambda a: _va_of_vals(a[0] if a else None, cls_d=VA))
    _add(OA, "ofFloat", f"(Ljava/lang/Object;Ljava/lang/String;[F){OA}",
         lambda a: _va_of_vals(a[2] if len(a) > 2 else None, prop=_txt(a[1]) if len(a) > 1 else "", tgt=a[0] if a else None, cls_d=OA))
    _add(OA, "ofInt", f"(Ljava/lang/Object;Ljava/lang/String;[I){OA}",
         lambda a: _va_of_vals(a[2] if len(a) > 2 else None, prop=_txt(a[1]) if len(a) > 1 else "", tgt=a[0] if a else None, cls_d=OA))
    PVH = "Landroid/animation/PropertyValuesHolder;"
    _add(PVH, "ofFloat", f"(Landroid/util/Property;[F){PVH}",
         lambda a: JObject(PVH, {"_vals": list(getattr(a[1], "data", ()) or ()) if len(a) > 1 else []}))
    _add(PVH, "ofFloat", f"(Ljava/lang/String;[F){PVH}",
         lambda a: JObject(PVH, {"_prop": _txt(a[0]) if a else "", "_vals": list(getattr(a[1], "data", ()) or ()) if len(a) > 1 else []}))
    _add(OA, "ofPropertyValuesHolder", f"(Ljava/lang/Object;[{PVH}){OA}",
         lambda a: _va_of_vals(None, tgt=a[0] if a else None, cls_d=OA))
    for _ac in (VA, OA):
        _add(_ac, "setRepeatCount", "(I)V", lambda a: None)
        _add(_ac, "setRepeatMode", "(I)V", lambda a: None)
    for _ac in (VA, OA, AS):
        _add(_ac, "setDuration", f"(J){_ac}", lambda a: a[0] if a else None)
        _add(_ac, "setDuration", "(J)Landroid/animation/Animator;", lambda a: a[0] if a else None)
        _add(_ac, "setStartDelay", "(J)V", lambda a: None)
        _add(_ac, "setInterpolator", "(Landroid/animation/TimeInterpolator;)V", lambda a: None)
        _add(_ac, "addUpdateListener", f"(Landroid/animation/ValueAnimator$AnimatorUpdateListener;)V",
             lambda a: a[0].fields.setdefault("_update_listeners", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "addListener", "(Landroid/animation/Animator$AnimatorListener;)V",
             lambda a: a[0].fields.setdefault("_listeners", []).append(a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        _add(_ac, "getAnimatedValue", "()Ljava/lang/Object;",
             lambda a: JObject("Ljava/lang/Float;", {"value": float(a[0].fields.get("_end_val", 1.0))}) if a and isinstance(a[0], JObject) else JObject("Ljava/lang/Float;", {"value": 1.0}))
        _add(_ac, "getAnimatedFraction", "()F", lambda a: 1.0)
        _add(_ac, "start", "()V", lambda a: _run_animator_end(a[0]) if a else None)
        _add(_ac, "cancel", "()V", lambda a: None)
    _add(AS, "<init>", "()V", lambda a: a[0].fields.setdefault("_children", []) if a and isinstance(a[0], JObject) else None)
    _add(AS, "playTogether", "([Landroid/animation/Animator;)V",
         lambda a: a[0].fields.setdefault("_children", []).extend(getattr(a[1], "data", ()) or ()) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(AS, "start", "()V", lambda a: [(_run_animator_end(c)) for c in (a[0].fields.get("_children") or ())] if a and isinstance(a[0], JObject) else None)
    _add("Landroid/view/View;", "animate", f"(){VPA}",
         lambda a: JObject(VPA, {"_target": a[0] if a else None, "_end_actions": []}))
    _add(VPA, "alpha", f"(F){VPA}", lambda a: (a[0].fields.update({"_prop": "alpha", "_end_val": float(a[1])}), a[0])[1] if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(VPA, "setDuration", f"(J){VPA}", lambda a: a[0] if a else None)
    _add(VPA, "withEndAction", f"(Ljava/lang/Runnable;){VPA}", lambda a: (a[0].fields.setdefault("_end_actions", []).append(a[1]), a[0])[1] if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(VPA, "start", "()V", lambda a: _run_animator_end(a[0]) if a else None)

    # ListAdapter
    LA = "Landroidx/recyclerview/widget/ListAdapter;"
    _add(LA, "<init>", "(Landroidx/recyclerview/widget/DiffUtil$ItemCallback;)V",
         lambda a: a[0].fields.setdefault("_current_list", []) if a and isinstance(a[0], JObject) else None)

    def _la_submit(a):
        if not a or not isinstance(a[0], JObject):
            return None
        lst = list((a[1].fields.get("__items") if len(a) > 1 and isinstance(a[1], JObject) else ()) or ())
        a[0].fields["_current_list"] = lst
        vm_cur = getattr(stubs, "vm", None)
        if len(a) > 2 and isinstance(a[2], JObject) and vm_cur is not None and hasattr(vm_cur, "invoke_virtual"):
            try:
                vm_cur.invoke_virtual(a[2], "run", "()V", (a[2],))
            except Exception:
                pass
        return None

    _add(LA, "submitList", "(Ljava/util/List;)V", _la_submit)
    _add(LA, "submitList", "(Ljava/util/List;Ljava/lang/Runnable;)V", _la_submit)
    _add(LA, "getCurrentList", "()Ljava/util/List;",
         lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(a[0].fields.get("_current_list") or ())}) if a and isinstance(a[0], JObject) else JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(LA, "getItem", "(I)Ljava/lang/Object;",
         lambda a: (lambda lst: lst[int(a[1])] if 0 <= int(a[1]) < len(lst) else None)(a[0].fields.get("_current_list") or ()) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(LA, "getItemCount", "()I",
         lambda a: len(a[0].fields.get("_current_list") or ()) if a and isinstance(a[0], JObject) else 0)

    # Bitmap & BitmapFactory & Rect/RectF
    BM = "Landroid/graphics/Bitmap;"
    BF = "Landroid/graphics/BitmapFactory;"
    _add(BM, "createBitmap", f"(IILandroid/graphics/Bitmap$Config;){BM}",
         lambda a: JObject(BM, {"_w": max(1, int(a[0] if a else 64)), "_h": max(1, int(a[1] if len(a) > 1 else 64)), "_bytes": b"", "_mut": 1, "_alpha": 1, "_cfg": a[2] if len(a) > 2 else None}))
    _add(BM, "getWidth", "()I", lambda a: int(a[0].fields.get("_w", 64)) if a and isinstance(a[0], JObject) else 64)
    _add(BM, "getHeight", "()I", lambda a: int(a[0].fields.get("_h", 64)) if a and isinstance(a[0], JObject) else 64)

    def _bm_compress(a):
        if len(a) > 3 and isinstance(a[3], JObject):
            raw = a[0].fields.get("_bytes") if isinstance(a[0], JObject) else None
            if not raw and isinstance(a[0], JObject):
                try:
                    from core.java.view_breadth_g16 import encode_bitmap_png
                    raw = encode_bitmap_png(a[0])
                except Exception:
                    raw = b""
            raw = raw or b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
            a[3].fields["_buf"] = bytearray(raw)
            a[3].fields["data"] = bytearray(raw)
            return 1
        return 1

    _add(BM, "compress", "(Landroid/graphics/Bitmap$CompressFormat;ILjava/io/OutputStream;)Z", _bm_compress)

    def _bf_decode_bytes(a):
        raw = _bytes_from(a[0]) if a else b""
        off = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
        ln = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else len(raw) - off
        sub = raw[off:off + ln]
        w, h = _parse_png_jpeg_dims(sub)
        return JObject(BM, {"_w": w, "_h": h, "_bytes": sub})

    _add(BF, "decodeByteArray", f"([BII){BM}", _bf_decode_bytes)
    _add(BF, "decodeResource", f"(Landroid/content/res/Resources;I){BM}", lambda a: JObject(BM, {"_w": 64, "_h": 64, "_bytes": b""}))
    _add(BF, "decodeFile", f"(Ljava/lang/String;){BM}", lambda a: JObject(BM, {"_w": 64, "_h": 64, "_bytes": b""}))

    for _rc_c in ("Landroid/graphics/Rect;", "Landroid/graphics/RectF;"):
        _add(_rc_c, "<init>", "()V", lambda a: a[0].fields.update({"left": 0, "top": 0, "right": 0, "bottom": 0}) if a and isinstance(a[0], JObject) else None)
        _add(_rc_c, "<init>", "(IIII)V", lambda a: a[0].fields.update({"left": a[1], "top": a[2], "right": a[3], "bottom": a[4]}) if len(a) > 4 and isinstance(a[0], JObject) else None)
        _add(_rc_c, "<init>", "(FFFF)V", lambda a: a[0].fields.update({"left": a[1], "top": a[2], "right": a[3], "bottom": a[4]}) if len(a) > 4 and isinstance(a[0], JObject) else None)
        _add(_rc_c, "width", "()I" if _rc_c.endswith("Rect;") else "()F", lambda a: (a[0].fields.get("right", 0) - a[0].fields.get("left", 0)))
        _add(_rc_c, "height", "()I" if _rc_c.endswith("Rect;") else "()F", lambda a: (a[0].fields.get("bottom", 0) - a[0].fields.get("top", 0)))

    # Toast & Clipboard & InputMethodManager
    TST = "Landroid/widget/Toast;"
    _add(TST, "makeText", f"(Landroid/content/Context;Ljava/lang/CharSequence;I){TST}",
         lambda a: JObject(TST, {"_text": _txt(a[1]) if len(a) > 1 else "", "_dur": int(a[2]) if len(a) > 2 else 0}))
    _add(TST, "makeText", f"(Landroid/content/Context;II){TST}",
         lambda a: JObject(TST, {"_text": (getattr(stubs, "_strmap", {}) or {}).get(int(a[1]), f"res#{a[1]}") if len(a) > 1 else ""}))

    def _toast_show(a):
        txt = (a[0].fields.get("_text") or a[0].fields.get("$text") or "") if a and isinstance(a[0], JObject) else ""
        tr = getattr(stubs, "view_tree", None)
        if tr is not None and txt:
            tr.last_toast = str(txt)
            tr.__dict__.setdefault("_toasts", []).append(str(txt))
            tr.touch()
        return None

    _add(TST, "show", "()V", _toast_show)

    CM = "Landroid/content/ClipboardManager;"
    CD = "Landroid/content/ClipData;"
    CDI = "Landroid/content/ClipData$Item;"
    _add(CD, "newPlainText", f"(Ljava/lang/CharSequence;Ljava/lang/CharSequence;){CD}",
         lambda a: JObject(CD, {"_label": _txt(a[0]) if a else "", "_text": _txt(a[1]) if len(a) > 1 else ""}))
    _add(CD, "getItemAt", f"(I){CDI}",
         lambda a: JObject(CDI, {"_text": a[0].fields.get("_text", "") if a and isinstance(a[0], JObject) else ""}))
    _add(CDI, "getText", "()Ljava/lang/CharSequence;",
         lambda a: JString(a[0].fields.get("_text", "") if a and isinstance(a[0], JObject) else ""))
    _add(CM, "setPrimaryClip", f"({CD})V",
         lambda a: stubs.__dict__.__setitem__("_clipboard_text", a[1].fields.get("_text", "") if len(a) > 1 and isinstance(a[1], JObject) else ""))
    _add(CM, "getPrimaryClip", f"(){CD}",
         lambda a: JObject(CD, {"_label": "clip", "_text": stubs.__dict__.get("_clipboard_text", "")}))
    _add(CM, "hasPrimaryClip", "()Z", lambda a: 1 if stubs.__dict__.get("_clipboard_text") else 0)

    IMM = "Landroid/view/inputmethod/InputMethodManager;"
    _add(IMM, "hideSoftInputFromWindow", "(Landroid/os/IBinder;I)Z", lambda a: 1)
    _add(IMM, "showSoftInput", "(Landroid/view/View;I)Z", lambda a: 1)

    # ---- 10. AppCompatActivity / ReactActivity / Context / JDK Base -------
    ACT = "Landroid/app/Activity;"
    APP = "Landroid/app/Application;"
    CTX = "Landroid/content/Context;"
    CTW = "Landroid/content/ContextWrapper;"
    PM = "Landroid/content/pm/PackageManager;"
    CFG = "Landroid/content/res/Configuration;"
    LOC_L = "Landroid/os/LocaleList;"
    THM = "Landroid/content/res/Resources$Theme;"
    WIN = "Landroid/view/Window;"
    LI = "Landroid/view/LayoutInflater;"
    VTO = "Landroid/view/ViewTreeObserver;"
    SPL = "Landroid/window/SplashScreen;"
    PKG = getattr(getattr(stubs, "profile", None), "package", None) or "com.example.app"

    def _live_pkg():
        return getattr(getattr(stubs, "profile", None), "package", None) or PKG

    def _app_info_obj():
        cur_p = _live_pkg()
        bnd = stubs.make_bundle() if hasattr(stubs, "make_bundle") else JObject("Landroid/os/Bundle;", {"map": {}, "$map": {}})
        raw_meta = getattr(stubs, "_manifest_meta_data", None) or getattr(getattr(stubs, "_loaded_manifest", None), "meta_data", None) or {}
        if isinstance(raw_meta, dict) and hasattr(stubs, "_bundle_map"):
            bm = stubs._bundle_map(bnd)
            bm.update(_resolve_meta_map(raw_meta))
        return JObject("Landroid/content/pm/ApplicationInfo;", {
            "packageName": JString(cur_p),
            "sourceDir": JString(f"/data/app/{cur_p}/base.apk"),
            "publicSourceDir": JString(f"/data/app/{cur_p}/base.apk"),
            "nativeLibraryDir": JString(f"/data/app/{cur_p}/lib/arm64"),
            "dataDir": JString(f"/data/user/0/{cur_p}"),
            "flags": 0,
            "targetSdkVersion": 34,
            "metaData": bnd,
        })

    def _comp_name(a):
        cls_nm = getattr(a[0], "cls", PKG + ".MainActivity") if a and isinstance(a[0], JObject) else PKG + ".MainActivity"
        return JObject("Landroid/content/ComponentName;", {"pkg": PKG, "cls": cls_nm})

    _add(APP, "onCreate", "()V", lambda a: None)
    _add(ACT, "<init>", "()V", lambda a: a[0] if a else None)
    _add(ACT, "onCreate", "(Landroid/os/Bundle;)V", lambda a: None)
    _add(ACT, "onStart", "()V", lambda a: None)
    _add(ACT, "onResume", "()V", lambda a: None)
    _add(ACT, "getComponentName", "()Landroid/content/ComponentName;", _comp_name)
    _add(ACT, "getLastNonConfigurationInstance", "()Ljava/lang/Object;", lambda a: None)
    _add(ACT, "getOnBackInvokedDispatcher", "()Landroid/window/OnBackInvokedDispatcher;",
         lambda a: JObject("Landroid/window/OnBackInvokedDispatcher;", {}))
    _add(ACT, "getSplashScreen", f"(){SPL}", lambda a: JObject(SPL, {}))
    _add(ACT, "registerActivityLifecycleCallbacks", "(Landroid/app/Application$ActivityLifecycleCallbacks;)V",
         lambda a: (stubs.activity_callbacks.append(a[1]) if hasattr(stubs, "activity_callbacks") and len(a) > 1 else None))
    _add("Landroid/app/Fragment;", "<init>", "()V", lambda a: a[0] if a else None)

    _add(CTX, "getApplicationInfo", "()Landroid/content/pm/ApplicationInfo;", lambda a: _app_info_obj())
    _add(CTX, "getTheme", f"(){THM}", lambda a: JObject(THM, {}))
    def _derived_ctx(a):
        base = a[0] if a else None
        cfg = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        return JObject(CTX, {"__pkg__": PKG, "__base__": base, "__cfg__": cfg})

    def _attach_base_ctx(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            a[0].fields["__base__"] = a[1]
        return None

    for _ctx_cls in (CTX, CTW, ACT, APP):
        _add(_ctx_cls, "createConfigurationContext", f"({CFG}){CTX}", _derived_ctx)
        _add(_ctx_cls, "createAttributionContext", f"(Ljava/lang/String;){CTX}", _derived_ctx)
        _add(_ctx_cls, "createDeviceProtectedStorageContext", f"(){CTX}", _derived_ctx)
        _add(_ctx_cls, "createDisplayContext", f"(Landroid/view/Display;){CTX}", _derived_ctx)
        _add(_ctx_cls, "createContextForSplit", f"(Ljava/lang/String;){CTX}", _derived_ctx)
        _add(_ctx_cls, "attachBaseContext", f"({CTX})V", _attach_base_ctx)
    _add(CTW, "<init>", f"({CTX})V", lambda a: (a[0].fields.__setitem__("_base_ctx", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None, a[0] if a else None)[1])
    _add(CTW, "attachBaseContext", f"({CTX})V", lambda a: a[0].fields.__setitem__("_base_ctx", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)

    def _pm_comp_info(a, kind="service"):
        cn = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        cur_p = _live_pkg()
        pkg_nm = _txt(cn.fields.get("pkg") or cn.fields.get("mPackage")) if cn else cur_p
        cls_nm = _txt(cn.fields.get("cls") or cn.fields.get("mClass") or cn.fields.get("className") or cn.fields.get("name")) if cn else ""
        default_nm = "Service" if kind == "service" else "Provider"
        si_cls = cls_nm or default_nm
        full_cls = (pkg_nm + si_cls) if (si_cls.startswith(".") and pkg_nm) else si_cls
        tgt_desc = full_cls if (full_cls.startswith("L") and full_cls.endswith(";")) else (f"L{full_cls.replace('.', '/')};" if full_cls else "")
        bnd = stubs.make_bundle() if hasattr(stubs, "make_bundle") else JObject("Landroid/os/Bundle;", {"map": {}, "$map": {}})
        man = getattr(stubs, "_loaded_manifest", None)
        if man is not None and tgt_desc and hasattr(stubs, "_bundle_map"):
            comp_list = getattr(man, "services" if kind == "service" else "providers", ()) or ()
            for entry in comp_list:
                if isinstance(entry, dict) and entry.get("cls") == tgt_desc:
                    bm = stubs._bundle_map(bnd)
                    for mk, mv in (entry.get("meta_data") or {}).items():
                        bm[str(mk)] = JString(str(mv))
                    break
        info_cls = "Landroid/content/pm/ServiceInfo;" if kind == "service" else "Landroid/content/pm/ProviderInfo;"
        fields = {
            "packageName": JString(pkg_nm or cur_p),
            "name": JString(full_cls),
            "metaData": bnd,
        }
        if kind == "provider":
            fields["authority"] = JString("")
        return JObject(info_cls, fields)

    _add(PM, "getActivityInfo", "(Landroid/content/ComponentName;I)Landroid/content/pm/ActivityInfo;",
         lambda a: JObject("Landroid/content/pm/ActivityInfo;", {"packageName": JString(_live_pkg()), "name": JString("MainActivity")}))
    _add(PM, "getServiceInfo", "(Landroid/content/ComponentName;I)Landroid/content/pm/ServiceInfo;",
         lambda a: _pm_comp_info(a, kind="service"))
    _add(PM, "getProviderInfo", "(Landroid/content/ComponentName;I)Landroid/content/pm/ProviderInfo;",
         lambda a: _pm_comp_info(a, kind="provider"))

    _add(CFG, "<init>", "()V", lambda a: a[0].fields.update({"densityDpi": 440, "screenWidthDp": 380, "screenHeightDp": 800}) if a and isinstance(a[0], JObject) else None)
    _add(CFG, "<init>", f"({CFG})V", lambda a: a[0].fields.update(dict(a[1].fields) if len(a) > 1 and isinstance(a[1], JObject) else {}) if a and isinstance(a[0], JObject) else None)
    _add(CFG, "diff", f"({CFG})I", lambda a: 0)
    _add(CFG, "equals", f"({CFG})Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) and a[0].fields == a[1].fields) else 0)
    _add(CFG, "getLocales", f"(){LOC_L}", lambda a: JObject(LOC_L, {"_tags": "de-DE,en-US"}))
    _add(CFG, "setTo", f"({CFG})V", lambda a: a[0].fields.update(dict(a[1].fields) if len(a) > 1 and isinstance(a[1], JObject) else {}) if a and isinstance(a[0], JObject) else None)
    _add(LOC_L, "<init>", "([Ljava/util/Locale;)V", lambda a: a[0] if a else None)
    _add(LOC_L, "toLanguageTags", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_tags", "de-DE,en-US") if a and isinstance(a[0], JObject) else "de-DE,en-US"))

    _add(THM, "resolveAttribute", "(ILandroid/util/TypedValue;Z)Z", lambda a: 1)
    _add("Landroid/util/TypedValue;", "<init>", "()V", lambda a: a[0] if a else None)
    _add("Landroid/os/Bundle;", "getParcelable", "(Ljava/lang/String;)Landroid/os/Parcelable;", lambda a: None)
    _add("Landroid/util/Log;", "e", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I", lambda a: 0)

    _add(LI, "getFactory", "()Landroid/view/LayoutInflater$Factory;", lambda a: a[0].fields.get("_factory") if a and isinstance(a[0], JObject) else None)
    _add(LI, "setFactory2", "(Landroid/view/LayoutInflater$Factory2;)V", lambda a: a[0].fields.__setitem__("_factory", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    def _vto_get(a):
        if a and isinstance(a[0], JObject):
            return a[0].fields.setdefault("_vto", JObject(VTO, {"_pre_draw": []}))
        return JObject(VTO, {"_pre_draw": []})

    def _vto_add_pre(a):
        if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject):
            lst = a[0].fields.setdefault("_pre_draw", [])
            if a[1] not in lst:
                lst.append(a[1])
        return None

    def _vto_rem_pre(a):
        if len(a) > 1 and isinstance(a[0], JObject):
            lst = a[0].fields.get("_pre_draw")
            if isinstance(lst, list) and a[1] in lst:
                lst.remove(a[1])
        return None

    _add("Landroid/view/View;", "getViewTreeObserver", f"(){VTO}", _vto_get)
    _add("Landroid/view/View;", "getSystemUiVisibility", "()I", lambda a: int(a[0].fields.get("_sys_ui_vis", 0)) if a and isinstance(a[0], JObject) else 0)
    _add("Landroid/view/View;", "setSystemUiVisibility", "(I)V", lambda a: a[0].fields.__setitem__("_sys_ui_vis", int(a[1] or 0)) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(VTO, "addOnPreDrawListener", "(Landroid/view/ViewTreeObserver$OnPreDrawListener;)V", _vto_add_pre)
    _add(VTO, "removeOnPreDrawListener", "(Landroid/view/ViewTreeObserver$OnPreDrawListener;)V", _vto_rem_pre)
    _add(VTO, "isAlive", "()Z", lambda a: 1)
    _add("Landroid/view/ViewGroup$LayoutParams;", "<init>", "(II)V", lambda a: a[0].fields.update({"width": int(a[1]) if len(a) > 1 else -1, "height": int(a[2]) if len(a) > 2 else -1}) if a and isinstance(a[0], JObject) else None)

    _add(WIN, "getAttributes", "()Landroid/view/WindowManager$LayoutParams;", lambda a: JObject("Landroid/view/WindowManager$LayoutParams;", {"width": -1, "height": -1, "flags": 0}))
    _add(WIN, "getInsetsController", "()Landroid/view/WindowInsetsController;", lambda a: JObject("Landroid/view/WindowInsetsController;", {}))
    _add(WIN, "setDecorFitsSystemWindows", "(Z)V", lambda a: None)
    _add(WIN, "setStatusBarColor", "(I)V", lambda a: None)
    _add(WIN, "setNavigationBarColor", "(I)V", lambda a: None)
    _add(WIN, "setStatusBarContrastEnforced", "(Z)V", lambda a: None)
    _add(WIN, "setNavigationBarContrastEnforced", "(Z)V", lambda a: None)
    _add("Landroid/view/WindowInsetsController;", "setSystemBarsAppearance", "(II)V", lambda a: None)
    _add(SPL, "setOnExitAnimationListener", "(Landroid/window/SplashScreen$OnExitAnimationListener;)V", lambda a: None)

    def _file_init_parent(a):
        if len(a) >= 3 and isinstance(a[0], JObject):
            par = _txt(a[1].fields.get("path") if isinstance(a[1], JObject) else a[1])
            sub = _txt(a[2])
            a[0].fields["path"] = (par.rstrip("/") + "/" + sub.lstrip("/")) if par else sub
        return a[0] if a else None

    _add("Ljava/io/File;", "<init>", "(Ljava/io/File;Ljava/lang/String;)V", _file_init_parent)
    _add("Ljava/io/File;", "exists", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and os.path.exists(str(a[0].fields.get("path", "")))) else 0)
    _add("Ljava/io/File;", "getCanonicalPath", "()Ljava/lang/String;", lambda a: JString(str(a[0].fields.get("path", "")) if a and isinstance(a[0], JObject) else ""))

    _add("Ljava/lang/Class;", "isInstance", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject)) else 0)
    _add("Ljava/lang/String;", "contains", "(Ljava/lang/CharSequence;)Z",
         lambda a: 1 if (len(a) > 1 and _txt(a[1]) in _txt(a[0])) else 0)

    def _str_split(a):
        s = _txt(a[0]) if a else ""
        pat = _txt(a[1]) if len(a) > 1 else ""
        raw_lim = int(a[2]) if len(a) > 2 and a[2] is not None else 0
        lim = ((raw_lim + 0x80000000) & 0xFFFFFFFF) - 0x80000000
        maxsp = max(0, lim - 1) if lim > 0 else 0
        try:
            parts = re.split(pat, s, maxsplit=maxsp) if pat else list(s)
        except Exception:
            parts = (s.split(pat, maxsp) if maxsp > 0 else s.split(pat)) if pat else [s]
        if lim == 0 and len(parts) > 1:
            while len(parts) > 1 and parts[-1] == "":
                parts.pop()
        return JArray("[Ljava/lang/String;", [JString(p) for p in parts])

    _add("Ljava/lang/String;", "split", "(Ljava/lang/String;)[Ljava/lang/String;", _str_split)
    _add("Ljava/lang/String;", "split", "(Ljava/lang/String;I)[Ljava/lang/String;", _str_split)
    _add("Ljava/lang/System;", "getenv", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: None)
    _add("Ljava/util/Objects;", "requireNonNull", "(Ljava/lang/Object;)Ljava/lang/Object;", lambda a: a[0] if a else None)
    _add("Ljava/util/AbstractList;", "<init>", "()V", lambda a: a[0].fields.setdefault("__items", []) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/ArrayList;", "removeAll", "(Ljava/util/Collection;)Z", lambda a: 0)
    _add("Ljava/util/WeakHashMap;", "<init>", "(I)V", lambda a: a[0].fields.setdefault("map", {}) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/concurrent/ConcurrentLinkedQueue;", "<init>", "()V", lambda a: a[0].fields.setdefault("__items", []) if a and isinstance(a[0], JObject) else None)
    _install_g15_offline_audit_stubs(stubs, _add)


def get_rn_builtin_modules(stubs: Any = None) -> Dict[str, Dict[str, Any]]:
    """Liefert Builtin NativeModules fuer React Native / Hermes."""
    pkg = getattr(getattr(stubs, "profile", None), "package", "com.example.app") if stubs else "com.example.app"
    clip_state = {"text": getattr(stubs, "_clipboard_text", "") if stubs else ""}
    mods: Dict[str, Dict[str, Any]] = {
        "PlatformConstants": {
            "OS": "android",
            "Version": 34,
            "Release": "14",
            "Brand": "google",
            "Model": "Pixel 8",
            "isTesting": False,
        },
        "DeviceInfo": {
            "Dimensions": {"window": {"width": 380, "height": 800, "scale": 2.75, "fontScale": 1.0}},
            "isIPhoneX_deprecated": False,
        },
        "I18nManager": {"isRTL": False, "doLeftAndRightSwapInRTL": True, "localeIdentifier": "de_DE"},
        "LinkingManager": {
            "getInitialURL": lambda: "",
            "canOpenURL": lambda _u=None: True,
            "openURL": lambda url: _rn_open_url(stubs, url),
        },
        "Clipboard": {
            "getString": lambda: clip_state["text"],
            "setString": lambda s: clip_state.__setitem__("text", str(s or "")),
        },
        "StatusBarManager": {"HEIGHT": 24},
        "AppState": {"packageName": pkg, "currentState": "active"},
    }
    return mods


def _rn_open_url(stubs: Any, url: Any) -> bool:
    u = str(url or "")
    if stubs is not None:
        tr = getattr(stubs, "view_tree", None)
        if tr is not None:
            tr.__dict__.setdefault("web_events", []).append({"kind": "rn_linking_openURL", "url": u})
        dl = getattr(stubs, "_g10_deliver_deeplink", None)
        if callable(dl) and "://" in u and not u.startswith(("http://", "https://")):
            return bool(dl(u))
    return True


def _install_g15_offline_audit_stubs(stubs: Any, _add) -> None:
    """Ergänzt high-impact Netzwerk-, Threading-, Security-, Collection- & Resource-Stubs."""
    import zlib
    from core.java.dalvik import DalvikException

    # ---- java.net.ServerSocket & java.net.Socket --------------------------
    SS = "Ljava/net/ServerSocket;"
    SO = "Ljava/net/Socket;"

    def _ss_init(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["port"] = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            a[0].fields["closed"] = 0
            a[0].fields["bound"] = 1 if len(a) > 1 else 0
            a[0].fields["_accept_count"] = 0
        return None

    def _ss_accept(a):
        obj = a[0] if a and isinstance(a[0], JObject) else None
        if obj is not None:
            if obj.fields.get("closed"):
                raise DalvikException(
                    "java.net.SocketException: Socket closed",
                    payload=JObject("Ljava/net/SocketException;", {"detailMessage": JString("Socket closed")}),
                )
            cnt = int(obj.fields.get("_accept_count", 0)) + 1
            obj.fields["_accept_count"] = cnt
            if cnt >= 3:
                obj.fields["closed"] = 1
                raise DalvikException(
                    "java.net.SocketException: Socket closed",
                    payload=JObject("Ljava/net/SocketException;", {"detailMessage": JString("Socket closed")}),
                )
        raise DalvikException(
            "java.net.SocketTimeoutException: accept timed out",
            payload=JObject("Ljava/net/SocketTimeoutException;", {"detailMessage": JString("accept timed out")}),
        )

    for _sig in ("()V", "(I)V", "(II)V", "(IILjava/net/InetAddress;)V"):
        _add(SS, "<init>", _sig, _ss_init)
    _add(SS, "accept", f"(){SO}", _ss_accept)
    _add(SS, "isClosed", "()Z", lambda a: int(a[0].fields.get("closed", 0)) if a and isinstance(a[0], JObject) else 1)
    _add(SS, "isBound", "()Z", lambda a: int(a[0].fields.get("bound", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SS, "getLocalPort", "()I", lambda a: int(a[0].fields.get("port", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SS, "setReuseAddress", "(Z)V", lambda a: None)
    _add(SS, "setSoTimeout", "(I)V", lambda a: None)
    _add(SS, "bind", "(Ljava/net/SocketAddress;)V", lambda a: (a[0].fields.__setitem__("bound", 1) if a and isinstance(a[0], JObject) else None))
    _add(SS, "bind", "(Ljava/net/SocketAddress;I)V", lambda a: (a[0].fields.__setitem__("bound", 1) if a and isinstance(a[0], JObject) else None))
    _add(SS, "close", "()V", lambda a: (a[0].fields.__setitem__("closed", 1) if a and isinstance(a[0], JObject) else None))

    def _so_init(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["host"] = _txt(a[1]) if len(a) > 1 else ""
            a[0].fields["port"] = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
            a[0].fields["closed"] = 0
            a[0].fields["connected"] = 1 if len(a) > 2 else 0
        return None

    for _sig in ("()V", "(Ljava/lang/String;I)V"):
        _add(SO, "<init>", _sig, _so_init)
    _add(SO, "setSoTimeout", "(I)V", lambda a: None)
    _add(SO, "setTcpNoDelay", "(Z)V", lambda a: None)
    _add(SO, "setKeepAlive", "(Z)V", lambda a: None)
    _add(SO, "connect", "(Ljava/net/SocketAddress;)V", lambda a: (a[0].fields.__setitem__("connected", 1) if a and isinstance(a[0], JObject) else None))
    _add(SO, "connect", "(Ljava/net/SocketAddress;I)V", lambda a: (a[0].fields.__setitem__("connected", 1) if a and isinstance(a[0], JObject) else None))
    _add(SO, "isConnected", "()Z", lambda a: int(a[0].fields.get("connected", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SO, "isClosed", "()Z", lambda a: int(a[0].fields.get("closed", 0)) if a and isinstance(a[0], JObject) else 1)
    _add(SO, "close", "()V", lambda a: (a[0].fields.__setitem__("closed", 1) if a and isinstance(a[0], JObject) else None))
    _add(SO, "getPort", "()I", lambda a: int(a[0].fields.get("port", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SO, "getLocalPort", "()I", lambda a: 49152)
    _add(SO, "getInputStream", "()Ljava/io/InputStream;", lambda a: JObject("Ljava/io/ByteArrayInputStream;", {"buf": b"", "pos": 0}))
    _add(SO, "getOutputStream", "()Ljava/io/OutputStream;", lambda a: JObject("Ljava/io/ByteArrayOutputStream;", {"buf": bytearray()}))

    # ---- Thread.sleep (virtuelle Uhr vorspulen) & HandlerThread -----------
    def _thread_sleep(a):
        ms = int(a[0]) if a and isinstance(a[0], (int, float)) else 0
        ms = max(0, min(ms, 5000))
        if ms > 0 and hasattr(stubs, "advance_clock_ms"):
            try:
                stubs.advance_clock_ms(ms)
            except Exception:
                pass
        return None

    _add("Ljava/lang/Thread;", "sleep", "(J)V", _thread_sleep)
    _add("Ljava/lang/Thread;", "sleep", "(JI)V", _thread_sleep)

    HT = "Landroid/os/HandlerThread;"
    def _ht_looper(a):
        return getattr(stubs, "_looper_main", None) or JObject("Landroid/os/Looper;", {})

    _add(HT, "<init>", "(Ljava/lang/String;)V", lambda a: (a[0].fields.__setitem__("name", _txt(a[1]) if len(a) > 1 else "HandlerThread") if a and isinstance(a[0], JObject) else None))
    _add(HT, "<init>", "(Ljava/lang/String;I)V", lambda a: (a[0].fields.__setitem__("name", _txt(a[1]) if len(a) > 1 else "HandlerThread") if a and isinstance(a[0], JObject) else None))
    _add(HT, "start", "()V", lambda a: None)
    _add(HT, "getLooper", "()Landroid/os/Looper;", _ht_looper)
    _add(HT, "getThreadId", "()I", lambda a: 2)
    _add(HT, "getId", "()J", lambda a: 2)
    _add(HT, "quit", "()Z", lambda a: 1)
    _add(HT, "quitSafely", "()Z", lambda a: 1)

    # ---- java.util.Stack, Properties, Capacity-<init>s ---------------------
    STK = "Ljava/util/Stack;"
    def _stk_items(obj):
        if not isinstance(obj, JObject):
            return []
        items = obj.fields.get("__items")
        if not isinstance(items, list):
            legacy = obj.fields.get("list")
            items = legacy if isinstance(legacy, list) else []
            obj.fields["__items"] = items
        obj.fields["list"] = items
        return items

    def _stk_init(a):
        if a and isinstance(a[0], JObject):
            items: list = []
            a[0].fields["__items"] = items
            a[0].fields["list"] = items
        return None

    def _stk_push(a):
        if a and isinstance(a[0], JObject):
            v = a[1] if len(a) > 1 else None
            _stk_items(a[0]).append(v)
            return v
        return None

    def _stk_pop(a):
        lst = _stk_items(a[0]) if a and isinstance(a[0], JObject) else []
        return lst.pop() if lst else None

    def _stk_peek(a):
        lst = _stk_items(a[0]) if a and isinstance(a[0], JObject) else []
        return lst[-1] if lst else None

    def _stk_search(a):
        lst = _stk_items(a[0]) if a and isinstance(a[0], JObject) else []
        tgt = a[1] if len(a) > 1 else None
        eq_fn = getattr(stubs, "_eq_el", None)
        for idx, el in enumerate(reversed(lst), 1):
            if (eq_fn(el, tgt) if callable(eq_fn) else (el == tgt)):
                return idx
        return -1

    def _stk_clear(a):
        if a and isinstance(a[0], JObject):
            _stk_items(a[0]).clear()
        return None

    _add(STK, "<init>", "()V", _stk_init)
    _add(STK, "push", "(Ljava/lang/Object;)Ljava/lang/Object;", _stk_push)
    _add(STK, "pop", "()Ljava/lang/Object;", _stk_pop)
    _add(STK, "peek", "()Ljava/lang/Object;", _stk_peek)
    _add(STK, "empty", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and _stk_items(a[0])) else 1)
    _add(STK, "isEmpty", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and _stk_items(a[0])) else 1)
    _add(STK, "size", "()I", lambda a: len(_stk_items(a[0])) if a and isinstance(a[0], JObject) else 0)
    _add(STK, "clear", "()V", _stk_clear)
    _add(STK, "search", "(Ljava/lang/Object;)I", _stk_search)

    PR = "Ljava/util/Properties;"
    def _props_init(a):
        if a and isinstance(a[0], JObject):
            m = {}
            if len(a) > 1 and isinstance(a[1], JObject):
                m.update(a[1].fields.get("map", {}))
            a[0].fields["map"] = m
        return None
    def _props_get(a):
        if a and isinstance(a[0], JObject):
            k = _txt(a[1]) if len(a) > 1 else ""
            m = a[0].fields.get("map", {})
            if k in m:
                v = m[k]
                return v if isinstance(v, JString) else JString(str(v))
        return a[2] if len(a) > 2 else None
    def _props_set(a):
        if a and isinstance(a[0], JObject) and len(a) > 2:
            m = a[0].fields.setdefault("map", {})
            k = _txt(a[1])
            prev = m.get(k)
            m[k] = a[2]
            return prev
        return None
    def _props_load(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JObject):
            raw = a[1].fields.get("buf") or a[1].fields.get("$data") or b""
            if isinstance(raw, (bytes, bytearray)):
                txt = bytes(raw).decode("utf-8", "replace")
            else:
                txt = str(raw or "")
            m = a[0].fields.setdefault("map", {})
            for line in txt.splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("!") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                m[k.strip()] = JString(v.strip())
        return None

    _add(PR, "<init>", "()V", _props_init)
    _add(PR, "<init>", f"({PR})V", _props_init)
    _add(PR, "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", _props_get)
    _add(PR, "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", _props_get)
    _add(PR, "setProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/Object;", _props_set)
    _add(PR, "load", "(Ljava/io/InputStream;)V", _props_load)
    _add(PR, "load", "(Ljava/io/Reader;)V", _props_load)
    _add(PR, "stringPropertyNames", "()Ljava/util/Set;",
         lambda a: JObject("Ljava/util/HashSet;", {"__items": [JString(str(k)) for k in (a[0].fields.get("map", {}) if a and isinstance(a[0], JObject) else {}).keys()]}))

    for _hm_cls in ("Ljava/util/HashMap;", "Ljava/util/LinkedHashMap;"):
        for _sig in ("(I)V", "(IF)V", "(IFZ)V"):
            _add(_hm_cls, "<init>", _sig, lambda a: stubs._coll_init(a, "map", {}))
        _add(_hm_cls, "<init>", "(Ljava/util/Map;)V",
             lambda a: stubs._map_copy_init(a[0], a[1]) if len(a) > 1 else stubs._coll_init(a, "map", {}))
    _add("Ljava/util/ArrayList;", "<init>", "(I)V", lambda a: stubs._coll_init(a, "__items", []))
    _add("Ljava/util/ArrayList;", "<init>", "(Ljava/util/Collection;)V",
         lambda a: stubs._coll_init(a, "__items", list(a[1].fields.get("__items", a[1].fields.get("list", []))) if len(a) > 1 and isinstance(a[1], JObject) else []))

    # ---- Pair, LongSparseArray, AtomicReferenceArray, Unsafe -------------
    for _pair_cls in ("Landroid/util/Pair;", "Landroidx/core/util/Pair;"):
        def _pair_init(a, _cls=_pair_cls):
            if a and isinstance(a[0], JObject):
                a[0].fields["first"] = a[1] if len(a) > 1 else None
                a[0].fields["second"] = a[2] if len(a) > 2 else None
            return None
        _add(_pair_cls, "<init>", "(Ljava/lang/Object;Ljava/lang/Object;)V", _pair_init)
        _add(_pair_cls, "create", f"(Ljava/lang/Object;Ljava/lang/Object;){_pair_cls}",
             lambda a, _c=_pair_cls: JObject(_c, {"first": a[0] if a else None, "second": a[1] if len(a) > 1 else None}))
        _add(_pair_cls, "equals", "(Ljava/lang/Object;)Z",
             lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject)
                             and a[0].fields.get("first") == a[1].fields.get("first")
                             and a[0].fields.get("second") == a[1].fields.get("second")) else 0)
        _add(_pair_cls, "hashCode", "()I", lambda a: hash((id(a[0].fields.get("first")), id(a[0].fields.get("second")))) & 0x7fffffff if a and isinstance(a[0], JObject) else 0)
        _add(_pair_cls, "toString", "()Ljava/lang/String;", lambda a: JString(f"Pair{{{a[0].fields.get('first')} {a[0].fields.get('second')}}}") if a and isinstance(a[0], JObject) else JString("Pair{}"))

    for _lsa_cls in ("Landroid/util/LongSparseArray;", "Landroidx/collection/LongSparseArray;"):
        _add(_lsa_cls, "<init>", "()V", lambda a: stubs._coll_init(a, "lsa", {}))
        _add(_lsa_cls, "<init>", "(I)V", lambda a: stubs._coll_init(a, "lsa", {}))
        _add(_lsa_cls, "put", "(JLjava/lang/Object;)V",
             lambda a: (a[0].fields.setdefault("lsa", {}).__setitem__(int(a[1]), a[2] if len(a) > 2 else None) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
        _add(_lsa_cls, "append", "(JLjava/lang/Object;)V",
             lambda a: (a[0].fields.setdefault("lsa", {}).__setitem__(int(a[1]), a[2] if len(a) > 2 else None) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
        _add(_lsa_cls, "get", "(J)Ljava/lang/Object;",
             lambda a: a[0].fields.get("lsa", {}).get(int(a[1])) if a and isinstance(a[0], JObject) and len(a) > 1 else None)
        _add(_lsa_cls, "get", "(JLjava/lang/Object;)Ljava/lang/Object;",
             lambda a: a[0].fields.get("lsa", {}).get(int(a[1]), a[2] if len(a) > 2 else None) if a and isinstance(a[0], JObject) and len(a) > 1 else None)
        _add(_lsa_cls, "remove", "(J)V",
             lambda a: (a[0].fields.get("lsa", {}).pop(int(a[1]), None) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
        _add(_lsa_cls, "delete", "(J)V",
             lambda a: (a[0].fields.get("lsa", {}).pop(int(a[1]), None) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
        _add(_lsa_cls, "size", "()I", lambda a: len(a[0].fields.get("lsa", {})) if a and isinstance(a[0], JObject) else 0)
        _add(_lsa_cls, "clear", "()V", lambda a: (a[0].fields.__setitem__("lsa", {}) if a and isinstance(a[0], JObject) else None))
        _add(_lsa_cls, "keyAt", "(I)J",
             lambda a: sorted(a[0].fields.get("lsa", {}).keys())[int(a[1])] if a and isinstance(a[0], JObject) and len(a) > 1 and 0 <= int(a[1]) < len(a[0].fields.get("lsa", {})) else 0)
        _add(_lsa_cls, "valueAt", "(I)Ljava/lang/Object;",
             lambda a: (lambda d, idx: d[sorted(d.keys())[idx]] if 0 <= idx < len(d) else None)(a[0].fields.get("lsa", {}), int(a[1])) if a and isinstance(a[0], JObject) and len(a) > 1 else None)
        _add(_lsa_cls, "indexOfKey", "(J)I",
             lambda a: (lambda ks, k: ks.index(k) if k in ks else -1)(sorted(a[0].fields.get("lsa", {}).keys()), int(a[1])) if a and isinstance(a[0], JObject) and len(a) > 1 else -1)

    ARA = "Ljava/util/concurrent/atomic/AtomicReferenceArray;"
    def _norm_ref(v):
        return None if (v is None or v == 0) else v
    def _ref_eq(x, y) -> bool:
        if (x is None or x == 0) and (y is None or y == 0):
            return True
        return x == y
    def _ara_init_n(a):
        if a and isinstance(a[0], JObject):
            n = max(0, int(a[1])) if len(a) > 1 and isinstance(a[1], int) else 0
            a[0].fields["arr"] = [None] * n
        return None
    def _ara_init_arr(a):
        if a and isinstance(a[0], JObject):
            arr = a[1].data if len(a) > 1 and isinstance(a[1], JArray) else []
            a[0].fields["arr"] = [_norm_ref(x) for x in arr]
        return None
    def _ara_get(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            arr = a[0].fields.get("arr", [])
            i = int(a[1])
            return arr[i] if 0 <= i < len(arr) else None
        return None
    def _ara_set(a):
        if a and isinstance(a[0], JObject) and len(a) > 2:
            arr = a[0].fields.setdefault("arr", [])
            i = int(a[1])
            if 0 <= i < len(arr):
                arr[i] = _norm_ref(a[2])
        return None
    def _ara_get_and_set(a):
        if a and isinstance(a[0], JObject) and len(a) > 2:
            arr = a[0].fields.setdefault("arr", [])
            i = int(a[1])
            if 0 <= i < len(arr):
                old = arr[i]
                arr[i] = _norm_ref(a[2])
                return old
        return None
    def _ara_cas(a):
        if a and isinstance(a[0], JObject) and len(a) > 3:
            arr = a[0].fields.setdefault("arr", [])
            i = int(a[1])
            if 0 <= i < len(arr) and _ref_eq(arr[i], a[2]):
                arr[i] = _norm_ref(a[3])
                return 1
        return 0

    _add(ARA, "<init>", "(I)V", _ara_init_n)
    _add(ARA, "<init>", "([Ljava/lang/Object;)V", _ara_init_arr)
    _add(ARA, "length", "()I", lambda a: len(a[0].fields.get("arr", [])) if a and isinstance(a[0], JObject) else 0)
    _add(ARA, "get", "(I)Ljava/lang/Object;", _ara_get)
    _add(ARA, "set", "(ILjava/lang/Object;)V", _ara_set)
    _add(ARA, "lazySet", "(ILjava/lang/Object;)V", _ara_set)
    _add(ARA, "getAndSet", "(ILjava/lang/Object;)Ljava/lang/Object;", _ara_get_and_set)
    _add(ARA, "compareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", _ara_cas)
    _add(ARA, "weakCompareAndSet", "(ILjava/lang/Object;Ljava/lang/Object;)Z", _ara_cas)

    # sun.misc.Unsafe & jdk.internal.misc.Unsafe
    unsafe_map = stubs.__dict__.setdefault("_unsafe_offsets", {})

    def _unsafe_key(off: int) -> str:
        return unsafe_map.get(int(off), f"$unsafe_{int(off)}")

    def _unsafe_field_offset(a):
        fobj = a[1] if len(a) > 1 else None
        if isinstance(fobj, JObject):
            fname = _txt(fobj.fields.get("name")) or "field"
            owner = _txt(fobj.fields.get("owner")) or ""
            off = 16 + (zlib.crc32(f"{owner}:{fname}".encode("utf-8")) & 0xFFFF)
            unsafe_map[off] = fname
            return off
        return 16

    def _unsafe_get(a, default=None):
        obj = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 2 else 0
        if isinstance(obj, JArray):
            idx = max(0, off // 4)
            return obj.data[idx] if 0 <= idx < len(obj.data) else default
        if isinstance(obj, JObject):
            return obj.fields.get(_unsafe_key(off), default)
        return default

    def _unsafe_put(a):
        obj = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 2 else 0
        val = a[3] if len(a) > 3 else None
        if isinstance(obj, JArray):
            idx = max(0, off // 4)
            if 0 <= idx < len(obj.data):
                obj.data[idx] = val
        elif isinstance(obj, JObject):
            obj.fields[_unsafe_key(off)] = val
        return None

    def _unsafe_cas(a, default=None):
        obj = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 2 else 0
        exp = a[3] if len(a) > 3 else default
        upd = a[4] if len(a) > 4 else default
        eq_fn = _ref_eq if default is None else (lambda x, y: int(x or 0) == int(y or 0))
        norm_fn = _norm_ref if default is None else (lambda v: int(v or 0))
        if isinstance(obj, JArray):
            idx = max(0, off // 4)
            if 0 <= idx < len(obj.data) and eq_fn(obj.data[idx], exp):
                obj.data[idx] = norm_fn(upd)
                return 1
            return 0
        if isinstance(obj, JObject):
            k = _unsafe_key(off)
            cur = obj.fields.get(k, default)
            if eq_fn(cur, exp):
                obj.fields[k] = norm_fn(upd)
                return 1
        return 0

    def _unsafe_get_and_set(a, default=None):
        old = _unsafe_get(a, default=default)
        _unsafe_put(a)
        return old

    def _unsafe_get_and_add(a):
        old = int(_unsafe_get(a, default=0) or 0)
        delta = int(a[3]) if len(a) > 3 else 0
        _unsafe_put([a[0], a[1], a[2], old + delta])
        return old

    for _ucls in ("Lsun/misc/Unsafe;", "Ljdk/internal/misc/Unsafe;"):
        _u_inst = JObject(_ucls, {})
        _add(_ucls, "<init>", "()V", lambda a: None)
        _add(_ucls, "getUnsafe", f"(){_ucls}", lambda a, _u=_u_inst: _u)
        _add(_ucls, "objectFieldOffset", "(Ljava/lang/reflect/Field;)J", _unsafe_field_offset)
        _add(_ucls, "arrayBaseOffset", "(Ljava/lang/Class;)I", lambda a: 0)
        _add(_ucls, "arrayIndexScale", "(Ljava/lang/Class;)I", lambda a: 4)
        _add(_ucls, "addressSize", "()I", lambda a: 8)
        _add(_ucls, "pageSize", "()I", lambda a: 4096)
        for _gm in ("getObject", "getObjectVolatile"):
            _add(_ucls, _gm, "(Ljava/lang/Object;J)Ljava/lang/Object;", lambda a: _unsafe_get(a, None))
        for _pm in ("putObject", "putObjectVolatile", "putOrderedObject"):
            _add(_ucls, _pm, "(Ljava/lang/Object;JLjava/lang/Object;)V", _unsafe_put)
        for _gm in ("getInt", "getIntVolatile"):
            _add(_ucls, _gm, "(Ljava/lang/Object;J)I", lambda a: int(_unsafe_get(a, 0) or 0))
        for _pm in ("putInt", "putIntVolatile", "putOrderedInt"):
            _add(_ucls, _pm, "(Ljava/lang/Object;JI)V", _unsafe_put)
        for _gm in ("getLong", "getLongVolatile"):
            _add(_ucls, _gm, "(Ljava/lang/Object;J)J", lambda a: int(_unsafe_get(a, 0) or 0))
        for _pm in ("putLong", "putLongVolatile", "putOrderedLong"):
            _add(_ucls, _pm, "(Ljava/lang/Object;JJ)V", _unsafe_put)
        _add(_ucls, "compareAndSwapObject", "(Ljava/lang/Object;JLjava/lang/Object;Ljava/lang/Object;)Z",
             lambda a: _unsafe_cas(a, None))
        _add(_ucls, "compareAndSwapInt", "(Ljava/lang/Object;JII)Z", lambda a: _unsafe_cas(a, 0))
        _add(_ucls, "compareAndSwapLong", "(Ljava/lang/Object;JJJ)Z", lambda a: _unsafe_cas(a, 0))
        _add(_ucls, "getAndSetObject", "(Ljava/lang/Object;JLjava/lang/Object;)Ljava/lang/Object;",
             lambda a: _unsafe_get_and_set(a, None))
        _add(_ucls, "getAndSetInt", "(Ljava/lang/Object;JI)I", lambda a: int(_unsafe_get_and_set(a, 0) or 0))
        _add(_ucls, "getAndAddInt", "(Ljava/lang/Object;JI)I", _unsafe_get_and_add)
        _add(_ucls, "getAndAddLong", "(Ljava/lang/Object;JJ)J", _unsafe_get_and_add)

    # ---- NotificationChannel & CertificateFactory / KeyStore / TMF --------
    NC = "Landroid/app/NotificationChannel;"
    NM = "Landroid/app/NotificationManager;"
    _channels = stubs.__dict__.setdefault("_notification_channels", {})
    _add(NC, "<init>", "(Ljava/lang/String;Ljava/lang/CharSequence;I)V",
         lambda a: (a[0].fields.update({"id": _txt(a[1]), "name": _txt(a[2]), "importance": int(a[3]) if len(a) > 3 else 3}) if a and isinstance(a[0], JObject) and len(a) > 2 else None))
    _add(NC, "setDescription", "(Ljava/lang/String;)V", lambda a: (a[0].fields.__setitem__("desc", _txt(a[1])) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
    _add(NC, "enableLights", "(Z)V", lambda a: None)
    _add(NC, "setLightColor", "(I)V", lambda a: None)
    _add(NC, "enableVibration", "(Z)V", lambda a: None)
    _add(NC, "setVibrationPattern", "([J)V", lambda a: None)
    _add(NC, "setShowBadge", "(Z)V", lambda a: None)
    _add(NC, "setSound", "(Landroid/net/Uri;Landroid/media/AudioAttributes;)V", lambda a: None)
    _add(NC, "setImportance", "(I)V", lambda a: (a[0].fields.__setitem__("importance", int(a[1])) if a and isinstance(a[0], JObject) and len(a) > 1 else None))
    _add(NC, "getImportance", "()I", lambda a: int(a[0].fields.get("importance", 3)) if a and isinstance(a[0], JObject) else 3)
    _add(NC, "getId", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("id", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(NC, "getName", "()Ljava/lang/CharSequence;", lambda a: JString(a[0].fields.get("name", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(NC, "setLockscreenVisibility", "(I)V", lambda a: None)
    _add(NC, "setBypassDnd", "(Z)V", lambda a: None)

    def _nm_create_ch(a):
        ch = a[1] if len(a) > 1 else None
        if isinstance(ch, JObject):
            _channels[ch.fields.get("id", "default")] = ch
        return None
    _add(NM, "createNotificationChannel", f"({NC})V", _nm_create_ch)
    def _nm_create_chs(a):
        if len(a) > 1 and isinstance(a[1], JObject):
            ext = getattr(stubs, "_extract_coll_items", None)
            items = (ext(a[1]) if callable(ext) else []) or a[1].fields.get("__items") or a[1].fields.get("list") or []
            for c in items:
                if isinstance(c, JObject):
                    _channels[c.fields.get("id", "default")] = c
        return None
    _add(NM, "createNotificationChannels", "(Ljava/util/List;)V", _nm_create_chs)
    _add(NM, "getNotificationChannel", f"(Ljava/lang/String;){NC}",
         lambda a: _channels.get(_txt(a[1]) if len(a) > 1 else ""))

    CF = "Ljava/security/cert/CertificateFactory;"
    CERT = "Ljava/security/cert/Certificate;"
    X509 = "Ljava/security/cert/X509Certificate;"
    KS = "Ljava/security/KeyStore;"
    TMF = "Ljavax/net/ssl/TrustManagerFactory;"
    _add(CF, "getInstance", f"(Ljava/lang/String;){CF}",
         lambda a: JObject(CF, {"type": _txt(a[0]) if a else "X.509"}))

    def _cf_cert(a):
        raw = _g14_read_stream_bytes(stubs, a[1]) if len(a) > 1 else b""
        return JObject(X509, {"_bytes": raw})

    def _cf_certs(a):
        raw = _g14_read_stream_bytes(stubs, a[1]) if len(a) > 1 else b""
        cert = JObject(X509, {"_bytes": raw})
        return JObject("Ljava/util/ArrayList;", {"__items": [cert], "list": [cert]})

    _add(CF, "generateCertificate", f"(Ljava/io/InputStream;){CERT}", _cf_cert)
    _add(CF, "generateCertificates", "(Ljava/io/InputStream;)Ljava/util/Collection;", _cf_certs)

    def _ks_certs(obj):
        if isinstance(obj, JObject):
            m = obj.fields.get("$certs")
            if not isinstance(m, dict):
                m = {}
                obj.fields["$certs"] = m
            return m
        return {}

    _add(KS, "load", "(Ljava/io/InputStream;[C)V", lambda a: None)
    _add(KS, "setCertificateEntry", f"(Ljava/lang/String;{CERT})V",
         lambda a: (_ks_certs(a[0]).__setitem__(_txt(a[1]), a[2]) if a and len(a) > 2 else None))
    _add(KS, "getCertificate", f"(Ljava/lang/String;){CERT}",
         lambda a: _ks_certs(a[0]).get(_txt(a[1]) if len(a) > 1 else "") if a else None)
    _add(KS, "containsAlias", "(Ljava/lang/String;)Z",
         lambda a: 1 if (a and len(a) > 1 and (_txt(a[1]) in _ks_certs(a[0]) or _txt(a[1]) in stubs.__dict__.get("_keystore_aliases", {}))) else 0)
    _add(KS, "size", "()I", lambda a: len(_ks_certs(a[0])) if a else 0)

    _add(TMF, "getDefaultAlgorithm", "()Ljava/lang/String;", lambda a: JString("X509"))
    _add(TMF, "getInstance", f"(Ljava/lang/String;){TMF}", lambda a: JObject(TMF, {"alg": _txt(a[0]) if a else "X509"}))
    _add(TMF, "init", f"({KS})V", lambda a: None)
    _add(TMF, "getTrustManagers", "()[Ljavax/net/ssl/TrustManager;",
         lambda a: JArray("[Ljavax/net/ssl/TrustManager;", [JObject("Ljavax/net/ssl/X509TrustManager;", {})]))

    # ---- Resources, Process.isIsolated, bindService, Intent, Bundle ------
    RES = "Landroid/content/res/Resources;"
    _dm = JObject("Landroid/util/DisplayMetrics;", {
        "density": 2.75, "scaledDensity": 2.75, "densityDpi": 440,
        "widthPixels": 1080, "heightPixels": 2280, "xdpi": 440.0, "ydpi": 440.0,
    })

    def _res_id_name(rid: Any) -> str:
        try:
            r_int = int(rid)
        except Exception:
            return ""
        idmap = getattr(stubs, "_res_idmap", None) or {}
        if r_int in idmap:
            return str(idmap[r_int])
        rev_global = stubs.__dict__.get("_res_name_by_id") or {}
        return str(rev_global.get(r_int, ""))

    def _res_identifier(a):
        nm = _txt(a[1]) if len(a) > 1 else ""
        tp = _txt(a[2]) if len(a) > 2 else ""
        if not nm:
            return 0
        if tp == "string":
            str_rev = getattr(stubs, "_str_rev_idmap", None)
            if isinstance(str_rev, dict) and nm in str_rev:
                return int(str_rev[nm])
            str_by_name = getattr(stubs, "_strmap_name", None)
            if isinstance(str_by_name, dict) and str_by_name:
                if nm in str_by_name:
                    alloc = stubs.__dict__.setdefault("_synth_str_ids", {})
                    if nm in alloc:
                        return alloc[nm]
                    rid = 0x7F130000 | (zlib.crc32(nm.encode("utf-8")) & 0xFFFF)
                    alloc[nm] = rid
                    if isinstance(getattr(stubs, "_strmap_id", None), dict):
                        stubs._strmap_id.setdefault(rid, str_by_name[nm])
                    if isinstance(str_rev, dict):
                        str_rev.setdefault(nm, rid)
                    return rid
                return 0
        if tp in ("drawable", "mipmap"):
            drev = getattr(stubs, "_draw_rev_idmap", None)
            if isinstance(drev, dict) and nm in drev:
                return int(drev[nm])
        rev = getattr(stubs, "_rev_idmap", None) or {}
        if nm in rev:
            return int(rev[nm])
        if tp and f"{tp}/{nm}" in rev:
            return int(rev[f"{tp}/{nm}"])
        return 0x7F000000 | (zlib.crc32(f"{tp}/{nm}".encode("utf-8")) & 0x00FFFFFF)

    def _open_raw(a):
        raw_bytes = b""
        if len(a) > 1:
            rname = _res_id_name(a[1])
            app_root = getattr(stubs, "_app_root", None) or ""
            if rname and app_root and os.path.isdir(os.path.join(app_root, "res", "raw")):
                raw_dir = os.path.join(app_root, "res", "raw")
                for fn in os.listdir(raw_dir):
                    stem = os.path.splitext(fn)[0]
                    if stem == rname or fn == rname:
                        try:
                            with open(os.path.join(raw_dir, fn), "rb") as fh:
                                raw_bytes = fh.read()
                        except OSError:
                            pass
                        break
        return JObject("Ljava/io/ByteArrayInputStream;", {"buf": raw_bytes, "pos": 0})

    _add(RES, "getDisplayMetrics", "()Landroid/util/DisplayMetrics;", lambda a: _dm)
    _add(RES, "getDimension", "(I)F", lambda a: 48.0)
    _add(RES, "getDimensionPixelSize", "(I)I", lambda a: 48)
    _add(RES, "getDimensionPixelOffset", "(I)I", lambda a: 48)
    _add(RES, "getBoolean", "(I)Z", lambda a: 0)
    _add(RES, "getInteger", "(I)I", lambda a: 1)
    _add(RES, "getIdentifier", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I", _res_identifier)
    _add(RES, "getString", "(I)Ljava/lang/String;",
         lambda a: JString(str((getattr(stubs, "_strmap_id", None) or {}).get(int(a[1]), ""))) if len(a) > 1 and isinstance(a[1], int) else JString(""))
    _add(RES, "getResourceEntryName", "(I)Ljava/lang/String;",
         lambda a: JString(_res_id_name(a[1]) or f"res_{int(a[1]) & 0xffffffff:08x}") if len(a) > 1 else JString(""))
    _add(RES, "getResourceName", "(I)Ljava/lang/String;",
         lambda a: (lambda pk, nm: JString(f"{pk}:id/{nm}"))(getattr(getattr(stubs, "profile", None), "package", None) or "app", _res_id_name(a[1]) or f"res_{int(a[1]) & 0xffffffff:08x}") if len(a) > 1 else JString(""))
    _add(RES, "getResourcePackageName", "(I)Ljava/lang/String;",
         lambda a: JString(getattr(getattr(stubs, "profile", None), "package", None) or "com.example.app"))
    _add(RES, "getResourceTypeName", "(I)Ljava/lang/String;", lambda a: JString("raw"))
    _add(RES, "openRawResource", "(I)Ljava/io/InputStream;", _open_raw)

    _add("Landroid/os/Process;", "isIsolated", "()Z", lambda a: 0)
    for _c_cls in ("Landroid/content/Context;", "Landroid/content/ContextWrapper;", "Landroid/app/Activity;", "Landroid/app/Application;"):
        _add(_c_cls, "bindService", "(Landroid/content/Intent;Landroid/content/ServiceConnection;I)Z", lambda a: 1)
        _add(_c_cls, "bindService", "(Landroid/content/Intent;ILjava/util/concurrent/Executor;Landroid/content/ServiceConnection;)Z", lambda a: 1)
        _add(_c_cls, "unbindService", "(Landroid/content/ServiceConnection;)V", lambda a: None)

    _add("Landroid/content/Intent;", "setPackage", "(Ljava/lang/String;)Landroid/content/Intent;",
         lambda a: (a[0].fields.__setitem__("$package", _txt(a[1]) if len(a) > 1 else ""), a[0])[1] if a and isinstance(a[0], JObject) else None)

    def _bundle_get_str_default(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            k = _txt(a[1])
            m_a = a[0].fields.get("map")
            m_b = a[0].fields.get("$map")
            if isinstance(m_a, dict) and isinstance(m_b, dict):
                if m_a is not m_b:
                    m_a.update(m_b)
                    a[0].fields["$map"] = m_a
                m = m_a
            elif isinstance(m_a, dict):
                a[0].fields["$map"] = m_a
                m = m_a
            elif isinstance(m_b, dict):
                a[0].fields["map"] = m_b
                m = m_b
            else:
                m = None
            if isinstance(m, dict) and k in m and m[k] is not None:
                v = m[k]
                return v if isinstance(v, JString) else JString(str(v))
            if k in a[0].fields and a[0].fields[k] is not None:
                v = a[0].fields[k]
                return v if isinstance(v, JString) else JString(str(v))
        return a[2] if len(a) > 2 else None

    for _b_cls in ("Landroid/os/Bundle;", "Landroid/os/BaseBundle;"):
        _add(_b_cls, "getString", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", _bundle_get_str_default)


def _handle_realm_hook(stubs: Any, rcls: str, cls: str, name: str, sig: str, args: Any, static: bool, _vm: Any):
    """Generischer In-Memory-Fallback fuer io.realm (Realm, RealmQuery, RealmResults, RealmList)."""
    store = stubs.__dict__.setdefault("_realm_store", {})

    def _cls_desc(cobj: Any) -> str:
        if isinstance(cobj, JObject):
            raw = str(cobj.fields.get("name") or cobj.fields.get("$name") or "Ljava/lang/Object;")
        else:
            raw = str(cobj or "Ljava/lang/Object;")
        if raw.startswith("L") and raw.endswith(";"):
            return raw
        return f"L{raw.replace('.', '/')};"

    def _row_key(obj: JObject) -> str:
        for k in ("identifier", "id", "_id", "uuid", "key"):
            if k in obj.fields:
                v = _txt(obj.fields.get(k))
                if v:
                    return f"{k}:{v}"
        return ""

    def _refresh_live_rr() -> None:
        for rr in list(stubs.__dict__.get("_realm_live_rr") or ()):
            if isinstance(rr, JObject):
                _rr_items(rr)

    def _upsert(row_obj: Any) -> None:
        if not isinstance(row_obj, JObject):
            return
        rows = store.setdefault(row_obj.cls, [])
        rk = _row_key(row_obj)
        for idx, existing in enumerate(rows):
            if existing is row_obj or (rk and _row_key(existing) == rk):
                rows[idx] = row_obj
                _refresh_live_rr()
                return
        rows.append(row_obj)
        _refresh_live_rr()

    def _mk_query(realm_obj: Any, cdesc: str, cobj: Any = None, src_rows: Any = None) -> JObject:
        tq = JObject("Lio/realm/internal/TableQuery;", {"$synth_tq": True})
        rq = JObject("Lio/realm/RealmQuery;", {
            "$synth_rq": True,
            "_cls": cdesc,
            "_cls_obj": cobj,
            "_filters": [],
            "_filter_or": False,
            "_sort": [],
            "a": realm_obj,
            "b": tq,
        })
        if src_rows is not None:
            rq.fields["_src_rows"] = list(src_rows)
        tq.fields["$rq"] = rq
        return rq

    def _eval_rq(rq_obj: JObject) -> List[Any]:
        src_rr = rq_obj.fields.get("_src_rr")
        if isinstance(src_rr, JObject):
            base = list(_rr_items(src_rr))
        elif "_src_rows" in rq_obj.fields:
            base = list(rq_obj.fields["_src_rows"])
        else:
            base = list(store.get(rq_obj.fields.get("_cls"), []))
        filters = rq_obj.fields.get("_filters") or []
        use_or = bool(rq_obj.fields.get("_filter_or"))
        if filters:
            out = []
            for row in base:
                if not isinstance(row, JObject):
                    continue
                checks = []
                for op, fld, val in filters:
                    fval = _txt(row.fields.get(fld)) if fld in row.fields else ""
                    if op == "contains":
                        checks.append(val.lower() in fval.lower())
                    else:
                        checks.append(fval == val)
                if (any(checks) if use_or else all(checks)):
                    out.append(row)
            base = out
        sorts = rq_obj.fields.get("_sort") or []
        for fld, asc in reversed(sorts):
            def _skey(r, _f=fld):
                v = r.fields.get(_f, 0) if isinstance(r, JObject) else 0
                return (0, int(v)) if isinstance(v, (int, bool)) else (1, _txt(v))
            try:
                base.sort(key=_skey, reverse=not asc)
            except Exception:
                pass
        return base

    def _rr_items(rr_obj: JObject) -> List[Any]:
        rq = rr_obj.fields.get("_src_rq")
        if isinstance(rq, JObject):
            rows = _eval_rq(rq)
            rr_obj.fields["__items"] = rows
            return rows
        return rr_obj.fields.setdefault("__items", [])

    def _mk_results(rows: List[Any], realm_obj: Any, cdesc: str, cobj: Any = None, rq_obj: Any = None) -> JObject:
        rfields = {
            "$synth_rr": True,
            "__items": list(rows),
            "_cls": cdesc,
            "b": realm_obj,
            "c": cobj,
        }
        if isinstance(rq_obj, JObject):
            rfields["_src_rq"] = rq_obj
        rr = JObject("Lio/realm/y0;", rfields)
        if isinstance(rq_obj, JObject):
            live_list = stubs.__dict__.setdefault("_realm_live_rr", [])
            live_list.append(rr)
            if len(live_list) > 32:
                del live_list[:-32]
        return rr

    if static and cls in ("Lio/realm/i0;", "Lio/realm/Realm;"):
        if name in ("z", "I", "init") and sig == "(Landroid/content/Context;)V":
            _vm.static_fields.setdefault("Lio/realm/a;", {})["h"] = args[0] if args else None
            return True, None
        if name in ("L", "setDefaultConfiguration") and sig == "(Lio/realm/o0;)V":
            stubs._realm_default_cfg = args[0] if args else None
            return True, None
        if name in ("w", "getDefaultInstance", "getInstance") and sig.endswith(")Lio/realm/i0;"):
            cfg = getattr(stubs, "_realm_default_cfg", None) or JObject("Lio/realm/o0;", {"$synth_rcfg": True})
            schema = JObject("Lio/realm/v;", {"$synth_schema": True, "e": JObject("Lio/realm/internal/objectstore/OsKeyPathMapping;", {})})
            return True, JObject("Lio/realm/i0;", {"$synth_realm": True, "d": cfg, "l": schema})
        if name in ("q", "deleteFromRealm") and sig == "(Lio/realm/t0;)V" and args and isinstance(args[0], JObject):
            rows = store.get(args[0].cls, [])
            store[args[0].cls] = [r for r in rows if r is not args[0]]
            _refresh_live_rr()
            return True, None

    if not static and args and isinstance(args[0], JObject):
        recv = args[0]
        if cls == "Lio/realm/o0$a;":
            if name == "<init>":
                recv.fields.update({
                    "a": None, "b": None, "c": 0, "d": None, "e": 0,
                    "f": JObject("Ljava/util/HashSet;", {"__items": []}),
                    "g": JObject("Ljava/util/HashSet;", {"__items": []}),
                    "j": 0x7FFFFFFFFFFFFFFF, "k": 0, "l": 0,
                })
                return True, None
            if name in ("a", "build") and sig == "()Lio/realm/o0;":
                return True, JObject("Lio/realm/o0;", {"$synth_rcfg": True, "_cfg_builder": recv})

        if recv.cls == "Lio/realm/RealmQuery;" and name == "<init>" and len(args) > 1 and isinstance(args[1], JObject):
            if "$synth_rr" in args[1].fields or "$synth_realm" in args[1].fields:
                tq = JObject("Lio/realm/internal/TableQuery;", {"$synth_tq": True})
                tq.fields["$rq"] = recv
                recv.fields.update({
                    "$synth_rq": True,
                    "_filters": [],
                    "_filter_or": False,
                    "_sort": [],
                    "b": tq,
                })
                if "$synth_rr" in args[1].fields:
                    recv.fields["_cls"] = args[1].fields.get("_cls") or _cls_desc(args[2] if len(args) > 2 else args[1].fields.get("c"))
                    recv.fields["_cls_obj"] = args[2] if len(args) > 2 else args[1].fields.get("c")
                    recv.fields["_src_rr"] = args[1]
                    recv.fields["a"] = args[1].fields.get("b")
                else:
                    cobj = args[2] if len(args) > 2 else None
                    recv.fields["_cls"] = _cls_desc(cobj)
                    recv.fields["_cls_obj"] = cobj
                    recv.fields["a"] = args[1]
                return True, None

        if "$synth_realm" in recv.fields:
            if name in ("N", "where") and sig == "(Ljava/lang/Class;)Lio/realm/RealmQuery;":
                cobj = args[1] if len(args) > 1 else None
                return True, _mk_query(recv, _cls_desc(cobj), cobj=cobj)
            if name in ("k", "getSchema") and sig == "()Lio/realm/z0;":
                return True, recv.fields.get("l")
            if name == "e" and sig == "()Lio/realm/a;":
                return True, recv
            if name in ("v", "executeTransaction") and len(args) > 1 and isinstance(args[1], JObject):
                tx_cb = args[1]
                for mnm, msig in (("g", "(Lio/realm/i0;)V"), ("execute", "(Lio/realm/Realm;)V")):
                    md = _vm._resolve_method(tx_cb.cls, tx_cb.cls, mnm, msig)
                    if md is not None:
                        _vm._run_mdef(md, [tx_cb, recv])
                        return True, None
                for (mcls, mnm, msig), md in list(getattr(_vm, "registry", {}).items()):
                    if mcls == tx_cb.cls and md.insns and msig.startswith("(Lio/realm/") and msig.endswith(";)V"):
                        _vm._run_mdef(md, [tx_cb, recv])
                        return True, None
                return True, None
            if name in ("t", "r", "s", "copyToRealmOrUpdate", "copyToRealm") and len(args) > 1:
                _upsert(args[1])
                return True, args[1]
            if name in ("u", "insertOrUpdate", "insert") and len(args) > 1:
                src = args[1]
                if isinstance(src, JObject) and ("__items" in src.fields or "List" in (src.cls or "") or "Collection" in (src.cls or "")):
                    for it in stubs._extract_coll_items(src):
                        _upsert(it)
                else:
                    _upsert(src)
                return True, None
            if name in ("isClosed", "l", "isInTransaction"):
                return True, 0
            if name == "isEmpty":
                return True, (0 if any(store.values()) else 1)
            if sig.endswith(")V") or name in ("d", "c", "o", "close", "beginTransaction", "commitTransaction", "cancelTransaction"):
                return True, (None if sig.endswith(")V") else recv)

        if "$synth_rq" in recv.fields:
            if name in ("b", "count") and sig == "()J":
                return True, len(_eval_rq(recv))
            if name in ("f", "c", "findAll", "findAllAsync") and sig.endswith(")Lio/realm/y0;"):
                rows = _eval_rq(recv)
                return True, _mk_results(rows, recv.fields.get("a"), recv.fields.get("_cls"), recv.fields.get("_cls_obj"), rq_obj=recv)
            if name in ("g", "findFirst", "findFirstAsync") and sig.endswith(")Lio/realm/t0;"):
                rows = _eval_rq(recv)
                return True, (rows[0] if rows else None)
            if name in ("a", "e", "contains", "equalTo") and len(args) >= 3:
                op = "contains" if name in ("a", "contains") else "eq"
                recv.fields.setdefault("_filters", []).append((op, _txt(args[1]), _txt(args[2])))
                return True, (None if sig.endswith(")V") else recv)
            return True, (None if sig.endswith(")V") else recv)

        if "$synth_tq" in recv.fields:
            if name == "c" and sig == "()V":
                rq = recv.fields.get("$rq")
                if isinstance(rq, JObject):
                    rq.fields["_filter_or"] = True
                return True, None
            if name == "e" and len(args) >= 4:
                rq = recv.fields.get("$rq")
                flds = getattr(args[2], "data", None) or ()
                dirs = getattr(args[3], "data", None) or ()
                if isinstance(rq, JObject):
                    slist = []
                    for i, f_el in enumerate(flds):
                        asc = True if i >= len(dirs) else (int(dirs[i]) != 0)
                        slist.append((_txt(f_el), asc))
                    rq.fields["_sort"] = slist
                return True, None
            if name in ("b",) and sig == "()J":
                rq = recv.fields.get("$rq")
                rows = _eval_rq(rq) if isinstance(rq, JObject) else []
                return True, (0 if rows else -1)
            return True, (None if sig.endswith(")V") else recv)

        if "$synth_schema" in recv.fields:
            if sig.endswith(")V"):
                return True, None
            if sig.endswith(")Lio/realm/x0;"):
                return True, JObject("Lio/realm/x0;", {"$synth_schema": True})
            return True, JObject("Lio/realm/internal/OsSchemaInfo;", {"$synth_schema": True})

        if "$synth_rr" in recv.fields:
            items = _rr_items(recv)
            if name == "size" and sig == "()I":
                return True, len(items)
            if name == "isEmpty" and sig == "()Z":
                return True, (1 if not items else 0)
            if name in ("isValid", "isLoaded", "load") and sig == "()Z":
                return True, 1
            if name == "get" and sig == "(I)Ljava/lang/Object;" and len(args) > 1:
                idx = int(args[1] or 0)
                return True, (items[idx] if 0 <= idx < len(items) else 0)
            if name == "contains" and sig == "(Ljava/lang/Object;)Z" and len(args) > 1:
                tgt = args[1]
                return True, (1 if any(x is tgt for x in items) else 0)
            if name in ("iterator", "listIterator") and sig.endswith("Iterator;"):
                return True, JObject("Ljava/util/Iterator;", {"__src": recv, "__idx": 0})
            if name == "toArray":
                return True, JArray("[Ljava/lang/Object;", list(items))
            if sig.endswith(")V"):
                return True, None

        if recv.cls in ("Lio/realm/f0$c;", "Lio/realm/internal/OsResults$a;"):
            if name == "<init>" and len(args) > 1 and isinstance(args[1], JObject) and "$synth_rr" in args[1].fields:
                recv.fields["__src"] = args[1]
                recv.fields["__idx"] = 0
                return True, None
            if "__src" in recv.fields:
                src_o = recv.fields["__src"]
                items = _rr_items(src_o) if "$synth_rr" in src_o.fields else (src_o.fields.get("__items") or [])
                idx = int(recv.fields.get("__idx", 0) or 0)
                if name == "hasNext" and sig == "()Z":
                    return True, (1 if idx < len(items) else 0)
                if name == "next" and sig == "()Ljava/lang/Object;":
                    if idx < len(items):
                        recv.fields["__idx"] = idx + 1
                        return True, items[idx]
                    return True, None

        if cls == "Lio/realm/q0;" or "$synth_rl" in recv.fields:
            if name == "<init>" and sig == "()V":
                recv.fields.setdefault("__items", [])
                recv.fields["$synth_rl"] = True
                return True, None
            if "$synth_rl" in recv.fields:
                items = recv.fields.setdefault("__items", [])
                if name == "add":
                    if len(args) == 2:
                        items.append(args[1])
                        return True, 1
                    if len(args) >= 3:
                        items.insert(max(0, min(len(items), int(args[1] or 0))), args[2])
                        return True, None
                if name == "size" and sig == "()I":
                    return True, len(items)
                if name == "isEmpty" and sig == "()Z":
                    return True, (1 if not items else 0)
                if name == "get" and sig == "(I)Ljava/lang/Object;" and len(args) > 1:
                    idx = int(args[1] or 0)
                    return True, (items[idx] if 0 <= idx < len(items) else 0)
                if name == "clear" and sig == "()V":
                    items.clear()
                    return True, None
                if name == "contains" and sig == "(Ljava/lang/Object;)Z" and len(args) > 1:
                    return True, (1 if any(x is args[1] for x in items) else 0)
                if name == "q" and sig == "()Lio/realm/RealmQuery;":
                    return True, _mk_query(None, "", src_rows=items)
                if name in ("iterator", "listIterator") and sig.endswith("Iterator;"):
                    return True, JObject("Ljava/util/Iterator;", {"__src": recv, "__idx": 0})
                if name == "remove" and len(args) > 1:
                    if sig == "(I)Ljava/lang/Object;":
                        idx = int(args[1] or 0)
                        return True, (items.pop(idx) if 0 <= idx < len(items) else None)
                    for idx, it in enumerate(items):
                        if it is args[1]:
                            items.pop(idx)
                            return True, 1
                    return True, 0

        if cls == "Lio/realm/internal/m;" and name == "b" and sig == "()V":
            return True, None

    return False, None


def attach_g14_vm_hook(stubs: Any, vm: Any) -> None:
    """Haengt Compose-G14-Hooks (SnapshotState, AnimatedVisibility, CompositionLocals) ein."""
    if vm is None or getattr(vm, "_g14_hook_attached", False):
        return
    vm._g14_hook_attached = True

    def _unwrap(fn_obj):
        if isinstance(fn_obj, JObject) and "$block" in fn_obj.fields:
            return fn_obj.fields["$block"]
        return fn_obj

    def _g14_hook(rcls, cls, name, sig, args, static, _vm):
        if cls.startswith("Lio/realm/") or (
            not static and args and isinstance(args[0], JObject)
            and ((args[0].cls or "").startswith("Lio/realm/") or any(k in args[0].fields for k in ("$synth_realm", "$synth_rq", "$synth_rr", "$synth_tq", "$synth_rl", "$synth_schema")))
        ):
            handled_r, res_r = _handle_realm_hook(stubs, rcls, cls, name, sig, args, static, _vm)
            if handled_r:
                return True, res_r
        # Derived state dynamic evaluation on getValue()
        if not static and args and isinstance(args[0], JObject) and "$derived_fn" in args[0].fields and name == "getValue":
            fn = _unwrap(args[0].fields["$derived_fn"])
            if isinstance(fn, JObject):
                try:
                    val = _vm.invoke(fn.cls, "invoke", "()Ljava/lang/Object;", (fn,))
                    return True, val
                except Exception:
                    return True, None

        # AnimatedVisibility / Crossfade
        if cls.startswith("Landroidx/compose/animation/AnimatedVisibilityKt") and name == "AnimatedVisibility":
            ptypes = _sig_param_types(sig)
            offset = 0 if static else 1
            vis = False
            f3 = None
            for i, pt in enumerate(ptypes):
                if offset + i >= len(args):
                    break
                if pt == "Z":
                    vis = bool(args[offset + i])
                elif "Function3" in pt and isinstance(args[offset + i], JObject):
                    f3 = _unwrap(args[offset + i])
            if vis and isinstance(f3, JObject):
                comp = JObject("Landroidx/compose/runtime/ComposerImpl;")
                scope = JObject("Landroidx/compose/animation/AnimatedVisibilityScope;")
                box_i = JObject("Ljava/lang/Integer;", {"value": 0})
                try:
                    _vm.invoke(f3.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (f3, scope, comp, box_i))
                except Exception:
                    pass
            return True, None

        if cls.startswith("Landroidx/compose/animation/CrossfadeKt") and name == "Crossfade":
            ptypes = _sig_param_types(sig)
            offset = 0 if static else 1
            tgt_state = args[offset] if len(args) > offset else None
            f3 = next((_unwrap(args[offset + i]) for i, pt in enumerate(ptypes) if offset + i < len(args) and "Function3" in pt and isinstance(args[offset + i], JObject)), None)
            if isinstance(f3, JObject):
                comp = JObject("Landroidx/compose/runtime/ComposerImpl;")
                box_i = JObject("Ljava/lang/Integer;", {"value": 0})
                try:
                    _vm.invoke(f3.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", (f3, tgt_state, comp, box_i))
                except Exception:
                    pass
            return True, None

        # SnapshotState / mutableIntStateOf / derivedStateOf / rememberSaveable
        def _f32_state(val):
            if isinstance(val, bool) or val is None:
                return 0.0
            if isinstance(val, int) and abs(val) > 10000:
                return struct.unpack("<f", struct.pack("<I", val & 0xFFFFFFFF))[0]
            return float(val)

        if cls in ("Landroidx/compose/runtime/SnapshotStateKt;", "Landroidx/compose/runtime/SnapshotStateKt__DerivedStateKt;",
                   "Landroidx/compose/runtime/SnapshotIntStateKt;", "Landroidx/compose/runtime/SnapshotLongStateKt;",
                   "Landroidx/compose/runtime/SnapshotFloatStateKt;", "Landroidx/compose/runtime/saveable/RememberSaveableKt;"):
            if name == "derivedStateOf":
                fn = next((a for a in args if isinstance(a, JObject)), None)
                return True, JObject("Landroidx/compose/runtime/State;", {"$derived_fn": fn})
            if name.startswith("mutableIntStateOf"):
                v = int(args[0] or 0) if args else 0
                return True, JObject("Landroidx/compose/runtime/ParcelableSnapshotMutableIntState;", {"value": v})
            if name.startswith("mutableLongStateOf"):
                v = int(args[0] or 0) if args else 0
                return True, JObject("Landroidx/compose/runtime/ParcelableSnapshotMutableLongState;", {"value": v})
            if name.startswith("mutableFloatStateOf"):
                v = _f32_state(args[0]) if args else 0.0
                return True, JObject("Landroidx/compose/runtime/ParcelableSnapshotMutableFloatState;", {"value": v, "$val": v})
            if name == "rememberSaveable" and not getattr(stubs, "_compose_saveable_active", False):
                init_fn = next((_unwrap(a) for a in reversed(args) if isinstance(a, JObject) and ("Lambda" in a.cls or "Function" in a.cls)), None)
                if isinstance(init_fn, JObject):
                    try:
                        return True, _vm.invoke(init_fn.cls, "invoke", "()Ljava/lang/Object;", (init_fn,))
                    except Exception:
                        pass
                return True, None

        if cls in ("Landroidx/compose/runtime/ParcelableSnapshotMutableIntState;", "Landroidx/compose/runtime/MutableIntState;"):
            if name in ("getIntValue", "getValue") and args and isinstance(args[0], JObject):
                return True, int(args[0].fields.get("value", 0))
            if name in ("setIntValue", "setValue") and len(args) > 1 and isinstance(args[0], JObject):
                args[0].fields["value"] = int(args[1] or 0)
                return True, None
        if cls in ("Landroidx/compose/runtime/ParcelableSnapshotMutableLongState;", "Landroidx/compose/runtime/MutableLongState;"):
            if name in ("getLongValue", "getValue") and args and isinstance(args[0], JObject):
                return True, int(args[0].fields.get("value", 0))
            if name in ("setLongValue", "setValue") and len(args) > 1 and isinstance(args[0], JObject):
                args[0].fields["value"] = int(args[1] or 0)
                return True, None
        if cls in ("Landroidx/compose/runtime/ParcelableSnapshotMutableFloatState;", "Landroidx/compose/runtime/MutableFloatState;"):
            if name in ("getFloatValue", "getValue") and args and isinstance(args[0], JObject):
                return True, float(args[0].fields.get("value", args[0].fields.get("$val", 0.0)))
            if name in ("setFloatValue", "setValue") and len(args) > 1 and isinstance(args[0], JObject):
                v = _f32_state(args[1])
                args[0].fields["value"] = v
                args[0].fields["$val"] = v
                return True, None

        # Effects & rememberCoroutineScope
        if cls == "Landroidx/compose/runtime/EffectsKt;":
            if name.startswith("DisposableEffect"):
                blk = next((_unwrap(a) for a in args if isinstance(a, JObject) and ("Function1" in a.cls or "Lambda" in a.cls)), None)
                if isinstance(blk, JObject):
                    try:
                        _vm.invoke(blk.cls, "invoke", "(Ljava/lang/Object;)Ljava/lang/Object;", (blk, JObject("Landroidx/compose/runtime/DisposableEffectScope;")))
                    except Exception:
                        pass
                return True, None
            if name == "SideEffect":
                blk = next((_unwrap(a) for a in args if isinstance(a, JObject)), None)
                if isinstance(blk, JObject):
                    try:
                        _vm.invoke(blk.cls, "invoke", "()Ljava/lang/Object;", (blk,))
                    except Exception:
                        pass
                return True, None
            if name.startswith("rememberCoroutineScope"):
                return True, JObject("Lkotlinx/coroutines/CoroutineScope;")

        # CompositionLocals
        if cls in ("Landroidx/compose/ui/platform/CompositionLocalsKt;", "Landroidx/compose/ui/platform/AndroidCompositionLocals_androidKt;"):
            if name == "getLocalDensity":
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": JObject("Landroidx/compose/ui/unit/Density;", {"density": 2.75, "fontScale": 1.0})})
            if name == "getLocalConfiguration":
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": JObject("Landroid/content/res/Configuration;", {"screenWidthDp": 380, "screenHeightDp": 800})})
            if name == "getLocalFocusManager":
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": JObject("Landroidx/compose/ui/focus/FocusManager;")})
            if name == "getLocalSoftwareKeyboardController":
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": JObject("Landroidx/compose/ui/platform/SoftwareKeyboardController;")})
            if name == "getLocalUriHandler":
                return True, JObject("Landroidx/compose/runtime/ProvidableCompositionLocal;", {"$val": JObject("Landroidx/compose/ui/platform/UriHandler;")})

        if cls == "Landroidx/compose/ui/platform/UriHandler;" and name == "openUri" and len(args) > 1:
            u = _txt(args[1])
            tr = getattr(stubs, "view_tree", None)
            if tr is not None:
                tr.__dict__.setdefault("web_events", []).append({"kind": "compose_openUri", "url": u})
            dl = getattr(stubs, "_g10_deliver_deeplink", None)
            if callable(dl) and "://" in u and not u.startswith(("http://", "https://")):
                dl(u)
            return True, None

        if cls in ("Landroidx/compose/ui/focus/FocusManager;", "Landroidx/compose/ui/platform/SoftwareKeyboardController;"):
            return True, None

        return False, None

    vm._method_hooks.append(_g14_hook)
