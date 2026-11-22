# -*- coding: utf-8 -*-
"""core.java.runtime — JavaRuntime: verbindet DalvikVM (J1) mit der JNIEnv-Fassade fuer BEIDE
Richtungen (J0 + J2).

- **J0 (Java->native):** haelt eine `JavaToNativeBridge`, die DalvikVM nutzt sie fuer ``invoke-native``.
- **J2 (native->Java):** die JNIEnv-Fassade (`native_jni_env_service`) ruft bei einem `Call*Method`/
  `CallStatic*Method` auf eine **im VM registrierte** Dalvik-Methode zurueck (``call_from_native``),
  statt nur den RASP-Policy-Default zu liefern. Damit ist der Zwei-Wege-Loop geschlossen:
  Java -> native (J0) und native -> Java (J2).

Marshalling ist bewusst bounded: jint/jlong direkt; jobject/jstring/Array ueber eine Handle-Tabelle
(native Handle-int <-> Python-Referenz). Zielagnostisch, keine App-Spezifika.
"""
from __future__ import annotations

import re
import struct

from core.java.dalvik import DalvikVM
from core.java.bridge import JavaToNativeBridge
from core.java.smali import load_into
from core.java.dex_loader import DexClassLoader, AndroidStubs
from core.java.objects import JString, JObject, JArray

_M64 = (1 << 64) - 1


def sig_param_types(sig: str) -> list[str]:
    """Zerlegt den Parameter-Teil von ``(args)ret`` in JNI-Typdeskriptoren (ohne ``this``)."""
    if "(" not in sig or ")" not in sig:
        return []
    inner = sig[sig.index("(") + 1:sig.index(")")]
    out = []
    i = 0
    n = len(inner)
    while i < n:
        start = i
        while i < n and inner[i] == "[":
            i += 1
        if i >= n:
            break
        if inner[i] == "L":
            end = inner.find(";", i)
            if end < 0:
                out.append(inner[start:])
                break
            i = end + 1
            out.append(inner[start:i])
        else:
            i += 1
            out.append(inner[start:i])
    return out


def sig_ret_type(sig: str) -> str:
    """Liefert den Rueckgabe-Deskriptor aus ``(args)ret``."""
    if ")" not in sig:
        return "V"
    return sig[sig.rindex(")") + 1:] or "V"


def sig_argcount(sig: str) -> int:
    """Anzahl der Parameter einer JNI-Signatur ``(args)ret`` (ohne ``this``)."""
    return len(sig_param_types(sig))


def _norm_jni_cls(cls: str) -> str:
    """Normalisiert JNI-Klassennamen ('com/foo/Bar' -> 'Lcom/foo/Bar;', Arrays unveraendert)."""
    s = str(cls or "").strip()
    if not s:
        return s
    if s.startswith("["):
        return s.replace(".", "/")
    if s.startswith("L") and s.endswith(";"):
        return "L" + s[1:-1].replace(".", "/") + ";"
    return "L" + s.replace(".", "/") + ";"


def _sx(val: int, bits: int) -> int:
    mask = (1 << bits) - 1
    v = int(val) & mask
    sign = 1 << (bits - 1)
    return v - (1 << bits) if (v & sign) else v


def _unmarshal_arg(word, ptype: str | None, h2py: dict, jni):
    """Konvertiert ein natives JNI-Argumentwort anhand seines Deskriptors in einen Python-/Dalvik-Wert."""
    handles = getattr(getattr(jni, "st", None), "handles", None)
    if ptype is None or ptype[0] in ("L", "["):
        if word is None:
            return None
        if isinstance(word, (JString, JObject, JArray)):
            return word
        if isinstance(word, float):
            return word
        w = int(word) & _M64
        if w == 0:
            return None if ptype is not None else 0
        if w in h2py:
            return h2py[w]
        if isinstance(handles, dict) and w in handles:
            rec = handles[w]
            kind = rec.get("kind")
            if kind == "jstring":
                obj = JString(rec.get("text", ""))
                h2py[w] = obj
                return obj
            if kind in ("jbyteArray", "jarray"):
                el = rec.get("eltype") or ("B" if kind == "jbyteArray" else (ptype or "B"))
                raw = rec.get("data") or ()
                obj = JArray(el, list(raw))
                h2py[w] = obj
                return obj
            if kind in ("jobject", "jclass", "jthrowable"):
                obj = JObject(rec.get("cls") or (ptype if ptype and ptype.startswith("L") else "Ljava/lang/Object;"),
                              rec.get("fields"))
                h2py[w] = obj
                return obj
        return w
    pt0 = ptype[0]
    if pt0 == "F":
        if isinstance(word, float):
            return word
        return struct.unpack("<f", struct.pack("<I", int(word) & 0xffffffff))[0]
    if pt0 == "D":
        if isinstance(word, float):
            return word
        return struct.unpack("<d", struct.pack("<Q", int(word) & _M64))[0]
    if pt0 == "Z":
        return 1 if (int(word) & 0xff) else 0
    if pt0 == "C":
        return int(word) & 0xffff
    if pt0 == "B":
        return _sx(word, 8)
    if pt0 == "S":
        return _sx(word, 16)
    if pt0 == "I":
        return _sx(word, 32)
    return int(word) & _M64


def _marshal_ret(val, ret_type: str | None, h2py: dict, jni, cpu=None) -> int:
    """Konvertiert einen Java-Rueckgabewert in ein natives jvalue-Wort (+ vset fuer F/D)."""
    if ret_type == "V":
        return 0
    if ret_type in ("F", "D") or isinstance(val, float):
        is_f = (ret_type == "F")
        if isinstance(val, float):
            raw = struct.pack("<f" if is_f else "<d", val)
            bits = int.from_bytes(raw, "little")
        else:
            bits = int(val or 0) & (0xffffffff if is_f else _M64)
            raw = struct.pack("<I" if is_f else "<Q", bits)
        if cpu is not None and hasattr(cpu, "vset"):
            cpu.vset(0, raw)
        return bits
    if val is None:
        return 0
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int):
        return val & _M64
    if isinstance(val, str):
        val = JString(val)
    if isinstance(val, JString):
        kind, kw = "jstring", {"text": val.text}
    elif isinstance(val, JObject):
        kind, kw = "jobject", {"cls": val.cls, "fields": dict(val.fields)}
    elif isinstance(val, JArray):
        if val.eltype in ("B", "[B"):
            kind = "jbyteArray"
            kw = {"eltype": val.eltype,
                  "data": bytearray(int(x) & 0xff for x in val.data),
                  "length": len(val.data)}
        else:
            kind = "jarray"
            kw = {"eltype": val.eltype, "data": list(val.data), "length": len(val.data)}
    else:
        kind, kw = "jobject", {}
    try:
        h = int(jni._new_handle(kind, **kw)) & _M64
    except Exception:
        return 0
    h2py[h] = val
    return h


def _prepare_native_args(sig: str, arg_words, static: bool, param_types, h2py: dict, jni):
    ptypes = list(param_types) if param_types is not None else sig_param_types(sig)
    raw = list(arg_words or ())
    args = []
    if not static and len(raw) > len(ptypes):
        args.append(_unmarshal_arg(raw[0], "Ljava/lang/Object;", h2py, jni))
        param_raw = raw[1:]
    else:
        param_raw = raw
    for i, w in enumerate(param_raw):
        pt = ptypes[i] if i < len(ptypes) else None
        args.append(_unmarshal_arg(w, pt, h2py, jni))
    return args


class JavaRuntime:
    """Geteilter Java-Laufzeitzustand: ein DalvikVM + Handle-Tabelle; an die JNI-Fassade gekoppelt."""

    def __init__(self, eng, jni, emu=None, attach: bool = True):
        self.eng = eng
        self.jni = jni
        self.vm = DalvikVM()
        self.bridge = JavaToNativeBridge(eng, jni, emu, vm=self.vm)
        self.vm.bridge = self.bridge
        self.vm.runtime = self
        self._h2py = {}   # native Handle -> Python-Referenz
        self.stubs = AndroidStubs()   # Framework-Stubs + Coverage-Loop (J3)
        self.vfs = self.stubs.vfs     # virtuelles FS (Assets/Ressourcen, 4c)
        self.loader = None            # DexClassLoader, via attach_dex
        self.stubs.seed_framework_statics(self.vm)   # z. B. android.os.Build
        if attach:
            jni.java_runtime = self   # J2-Hook aktivieren

    # ---- Registrierung -----------------------------------------------------
    def load_smali(self, text):
        return load_into(self.vm, text)

    def register(self, md):
        return self.vm.register(md)

    def attach_dex(self, dex):
        """Einen DEX-File anbinden: Klassen werden lazy in die VM geladen (J3)."""
        self.loader = DexClassLoader(dex)
        return self.loader

    def load_dex_class(self, name):
        if self.loader is None:
            raise RuntimeError("kein DEX angebunden (attach_dex)")
        return self.loader.register_class(self.vm, name)

    def run_dex_method(self, cls, name, sig, args=()):
        if self.loader is not None and self.loader.has_class(cls) and cls not in self.loader.loaded:
            self.loader.register_class(self.vm, cls)
        return self.vm.invoke(cls, name, sig, args)

    def register_method(self, *a, **kw):
        return self.vm.register_method(*a, **kw)

    def call(self, cls, name, sig, args=()):
        """Java-seitiger Einstieg (wie ein Java-Aufrufer)."""
        return self.vm.invoke(cls, name, sig, args)

    # ---- J2: Rueckruf aus nativem Code ------------------------------------
    def has_method(self, cls, name, sig) -> bool:
        ncls = _norm_jni_cls(cls)
        return (cls, name, sig) in self.vm.registry or (ncls, name, sig) in self.vm.registry

    def call_from_native(self, cls, name, sig, arg_words, static: bool,
                         param_types=None, ret_type=None, nonvirtual: bool = False):
        """Von der JNIEnv-Fassade gerufen: marshallt native Argumentworte -> Python, fuehrt die
        Dalvik-Methode aus, marshallt die Rueckgabe zurueck in ein jvalue (int/Handle)."""
        ncls = _norm_jni_cls(cls)
        use_cls = cls if (cls, name, sig) in self.vm.registry else ncls
        args = _prepare_native_args(sig, arg_words, static, param_types, self._h2py, self.jni)
        ret = self.vm.invoke(use_cls, name, sig, args)
        rtype = ret_type if ret_type is not None else sig_ret_type(sig)[0]
        cpu = getattr(getattr(self, "eng", None), "cpu", None)
        return _marshal_ret(ret, rtype, self._h2py, self.jni, cpu=cpu)

    # Fallback fuer DalvikVM._do_invoke (nicht registrierte Methoden): nichts behandeln.
    def resolve_invoke(self, cls, name, sig, args, static):
        # 1) lazy DEX-Klassenladung (inkl. <clinit>)
        if self.loader is not None and self.loader.has_class(cls) and cls not in self.loader.loaded:
            self.loader.register_class(self.vm, cls)
            rcls = cls
            if not static and args and hasattr(args[0], "cls"):
                rcls = args[0].cls
            md = self.vm.registry.get((rcls, name, sig)) or self.vm.registry.get((cls, name, sig))
            if md is not None:
                return True, self.vm._run_mdef(md, list(args))
        # 2) Framework-Stubs (android.*/java.* Coverage-Loop)
        if self.stubs is not None:
            return self.stubs.resolve(cls, name, sig, args, static)
        return False, 0

    # ---- Marshalling -------------------------------------------------------
    def _to_py(self, word, ptype=None):
        return _unmarshal_arg(word, ptype, self._h2py, self.jni)

    def _to_jni(self, val, ret_type=None, cpu=None):
        if cpu is None:
            cpu = getattr(getattr(self, "eng", None), "cpu", None)
        return _marshal_ret(val, ret_type, self._h2py, self.jni, cpu=cpu)


class VmBackedJavaRuntime:
    """AP2.1 (J2-Rueckkanal): schlanker Adapter, der eine BEREITS LAUFENDE DalvikVM (mit ihrem
    class_provider, der Registry geladener App-Klassen und den AndroidStubs) an die JNIEnv-Fassade
    koppelt. Anders als `JavaRuntime` baut er KEINE eigene VM -- native `Call*Method`-Aufrufe in
    `JNI_OnLoad` treffen damit die echten, im gläsernen Lauf geladenen Klassen + Stubs.

    Handle-Tabelle (native Handle-int <-> Python-Referenz) analog `JavaRuntime._h2py`/`_to_jni`."""

    def __init__(self, vm, jni):
        self.vm = vm
        self.jni = jni
        self._h2py = {}

    # ---- J2: native -> Java ------------------------------------------------
    def has_method(self, cls, name, sig) -> bool:
        ncls = _norm_jni_cls(cls)
        if (cls, name, sig) in self.vm.registry or (ncls, name, sig) in self.vm.registry:
            return True
        prov = getattr(self.vm, "class_provider", None)
        if prov is not None:                       # lazy: Klasse (inkl. Methoden) nachladen
            for cand in ((ncls, cls) if ncls != cls else (cls,)):
                try:
                    for md in (prov.get_class(cand) or []):
                        self.vm.registry.setdefault((md.cls, md.name, md.sig), md)
                except Exception:
                    pass
        return (cls, name, sig) in self.vm.registry or (ncls, name, sig) in self.vm.registry

    def call_from_native(self, cls, name, sig, arg_words, static: bool,
                         param_types=None, ret_type=None, nonvirtual: bool = False):
        """Native Argumentworte -> Python marshallen, Dalvik-Methode auf der laufenden VM ausfuehren,
        Rueckgabe zurueck in ein jvalue (int/Handle) marshallen."""
        ncls = _norm_jni_cls(cls)
        use_cls = cls if (cls, name, sig) in self.vm.registry else ncls
        args = _prepare_native_args(sig, arg_words, static, param_types, self._h2py, self.jni)
        ret = self.vm.invoke(use_cls, name, sig, args)
        rtype = ret_type if ret_type is not None else sig_ret_type(sig)[0]
        return _marshal_ret(ret, rtype, self._h2py, self.jni)

    # ---- Marshalling -------------------------------------------------------
    def _to_py(self, word, ptype=None):
        return _unmarshal_arg(word, ptype, self._h2py, self.jni)

    def _to_jni(self, val, ret_type=None, cpu=None):
        return _marshal_ret(val, ret_type, self._h2py, self.jni, cpu=cpu)
