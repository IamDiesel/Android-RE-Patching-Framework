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
import struct
from typing import Any, Dict, List, Optional
import uuid as _py_uuid

from core.java.objects import JArray, JObject, JString

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

    # ---- 5. java.nio.ByteBuffer & ByteOrder --------------------------------
    BB = "Ljava/nio/ByteBuffer;"
    BO = "Ljava/nio/ByteOrder;"

    def _bb_new(buf: bytearray, limit: Optional[int] = None) -> JObject:
        return JObject(BB, {"_buf": buf, "_pos": 0, "_lim": len(buf) if limit is None else int(limit), "_big": True})

    _add(BB, "allocate", "(I)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(max(0, int(a[0] if a else 0)))))
    _add(BB, "allocateDirect", "(I)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(max(0, int(a[0] if a else 0)))))
    _add(BB, "wrap", "([B)Ljava/nio/ByteBuffer;", lambda a: _bb_new(bytearray(_bytes_from(a[0])) if a else bytearray()))
    _add(BB, "wrap", "([BII)Ljava/nio/ByteBuffer;", lambda a: (lambda b: _bb_new(bytearray(b[int(a[1]):int(a[1]) + int(a[2])])))(_bytes_from(a[0])) if len(a) > 2 else _bb_new(bytearray()))
    _add(BB, "order", f"({BO}){BB}", lambda a: (a[0].fields.__setitem__("_big", not (len(a) > 1 and isinstance(a[1], JObject) and a[1].fields.get("_le"))), a[0])[1])
    _add(BB, "position", "()I", lambda a: int(a[0].fields.get("_pos", 0)))
    _add(BB, "position", f"(I){BB}", lambda a: (a[0].fields.__setitem__("_pos", int(a[1])), a[0])[1])
    _add(BB, "limit", "()I", lambda a: int(a[0].fields.get("_lim", len(a[0].fields.get("_buf", b"")))))
    _add(BB, "capacity", "()I", lambda a: len(a[0].fields.get("_buf", b"")))
    _add(BB, "remaining", "()I", lambda a: max(0, int(a[0].fields.get("_lim", 0)) - int(a[0].fields.get("_pos", 0))))
    _add(BB, "hasRemaining", "()Z", lambda a: 1 if int(a[0].fields.get("_pos", 0)) < int(a[0].fields.get("_lim", 0)) else 0)
    _add(BB, "flip", f"(){BB}", lambda a: (a[0].fields.update({"_lim": int(a[0].fields.get("_pos", 0)), "_pos": 0}), a[0])[1])
    _add(BB, "clear", f"(){BB}", lambda a: (a[0].fields.update({"_pos": 0, "_lim": len(a[0].fields.get("_buf", b""))}), a[0])[1])
    _add(BB, "rewind", f"(){BB}", lambda a: (a[0].fields.__setitem__("_pos", 0), a[0])[1])
    _add(BB, "array", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_buf", b""))))

    def _bb_read(obj: JObject, fmt: str):
        sz = struct.calcsize(fmt)
        pos = int(obj.fields.get("_pos", 0))
        buf = obj.fields.get("_buf", bytearray())
        end = ">" if obj.fields.get("_big", True) else "<"
        chunk = bytes(buf[pos:pos + sz])
        if len(chunk) < sz:
            return 0
        obj.fields["_pos"] = pos + sz
        return struct.unpack(end + fmt, chunk)[0]

    def _bb_write(obj: JObject, fmt: str, val):
        sz = struct.calcsize(fmt)
        pos = int(obj.fields.get("_pos", 0))
        buf = obj.fields.setdefault("_buf", bytearray())
        end = ">" if obj.fields.get("_big", True) else "<"
        if pos + sz > len(buf):
            buf.extend(b"\x00" * (pos + sz - len(buf)))
        buf[pos:pos + sz] = struct.pack(end + fmt, val)
        obj.fields["_pos"] = pos + sz
        obj.fields["_lim"] = max(int(obj.fields.get("_lim", 0)), pos + sz)
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
        pos = int(a[0].fields.get("_pos", 0))
        buf = a[0].fields.get("_buf", bytearray())
        dst = getattr(a[1], "data", None)
        if dst is not None:
            n = len(dst)
            for i in range(n):
                dst[i] = buf[pos + i] if pos + i < len(buf) else 0
            a[0].fields["_pos"] = pos + n
        return a[0]

    def _bb_put_arr(a):
        src = _bytes_from(a[1])
        pos = int(a[0].fields.get("_pos", 0))
        buf = a[0].fields.setdefault("_buf", bytearray())
        if pos + len(src) > len(buf):
            buf.extend(b"\x00" * (pos + len(src) - len(buf)))
        buf[pos:pos + len(src)] = src
        a[0].fields["_pos"] = pos + len(src)
        a[0].fields["_lim"] = max(int(a[0].fields.get("_lim", 0)), pos + len(src))
        return a[0]

    _add(BB, "get", f"([B){BB}", _bb_get_arr)
    _add(BB, "put", f"([B){BB}", _bb_put_arr)
    vm_ref = getattr(stubs, "vm", None)
    if vm_ref is not None and hasattr(vm_ref, "statics"):
        vm_ref.statics.setdefault(BO, {}).setdefault("BIG_ENDIAN", JObject(BO, {"_le": False}))
        vm_ref.statics.setdefault(BO, {}).setdefault("LITTLE_ENDIAN", JObject(BO, {"_le": True}))

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
            f = getattr(a[1], "fields", {}) if isinstance(a[1], JObject) else {}
            src = f.get("buf") if "buf" in f else (f.get("_buf") if "_buf" in f else f.get("data"))
            raw = _bytes_from(src)
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
            out_s.fields["buf"] = bytearray(comp)
            out_s.fields["_buf"] = bytearray(comp)
            out_s.fields["data"] = bytearray(comp)
        return None

    _add(GO, "<init>", "(Ljava/io/OutputStream;)V", _go_init)
    _add(GO, "write", "(I)V", _go_write)
    _add(GO, "write", "([B)V", _go_write)
    _add(GO, "write", "([BII)V", _go_write)
    _add(GO, "finish", "()V", _go_finish)
    _add(GO, "close", "()V", _go_finish)

    # ---- 7. PackageManager Ergaenzungen (ApplicationInfo + metaData + checkPermission)
    for _pm_cls in ("Landroid/content/pm/PackageManager;", "Landroid/app/ApplicationPackageManager;"):
        _add(_pm_cls, "checkPermission", "(Ljava/lang/String;Ljava/lang/String;)I", lambda a: 0)
        def _get_app_info(a):
            pkg = _txt(a[1]) if len(a) > 1 else ""
            meta = dict(getattr(stubs, "_manifest_meta_data", {}) or {})
            bd = JObject("Landroid/os/Bundle;", {"map": {str(k): JString(str(v)) for k, v in meta.items()}})
            return JObject("Landroid/content/pm/ApplicationInfo;", {
                "packageName": JString(pkg or getattr(getattr(stubs, "profile", None), "package", "com.example.app")),
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
         lambda a: JObject(BM, {"_w": max(1, int(a[0] if a else 64)), "_h": max(1, int(a[1] if len(a) > 1 else 64)), "_bytes": b""}))
    _add(BM, "getWidth", "()I", lambda a: int(a[0].fields.get("_w", 64)) if a and isinstance(a[0], JObject) else 64)
    _add(BM, "getHeight", "()I", lambda a: int(a[0].fields.get("_h", 64)) if a and isinstance(a[0], JObject) else 64)

    def _bm_compress(a):
        if len(a) > 3 and isinstance(a[3], JObject):
            raw = a[0].fields.get("_bytes") or b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
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
                v = float(args[0] or 0.0) if args else 0.0
                return True, JObject("Landroidx/compose/runtime/ParcelableSnapshotMutableFloatState;", {"value": v})
            if name == "rememberSaveable":
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
                return True, float(args[0].fields.get("value", 0.0))
            if name in ("setFloatValue", "setValue") and len(args) > 1 and isinstance(args[0], JObject):
                args[0].fields["value"] = float(args[1] or 0.0)
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
