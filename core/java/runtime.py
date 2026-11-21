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

from core.java.dalvik import DalvikVM
from core.java.bridge import JavaToNativeBridge
from core.java.smali import load_into
from core.java.dex_loader import DexClassLoader, AndroidStubs
from core.java.objects import JString, JObject, JArray

_M64 = (1 << 64) - 1


def sig_argcount(sig: str) -> int:
    """Anzahl der Parameter einer JNI-Signatur ``(args)ret`` (ohne ``this``)."""
    inner = sig[sig.index("(") + 1:sig.index(")")]
    n = 0
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "[":
            i += 1
            continue
        if c == "L":
            i = inner.index(";", i) + 1
        else:
            i += 1
        n += 1
    return n


class JavaRuntime:
    """Geteilter Java-Laufzeitzustand: ein DalvikVM + Handle-Tabelle; an die JNI-Fassade gekoppelt."""

    def __init__(self, eng, jni, emu=None, attach: bool = True):
        self.eng = eng
        self.jni = jni
        self.vm = DalvikVM()
        self.bridge = JavaToNativeBridge(eng, jni, emu)
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
        return (cls, name, sig) in self.vm.registry

    def call_from_native(self, cls, name, sig, arg_words, static: bool):
        """Von der JNIEnv-Fassade gerufen: marshallt native Argumentworte -> Python, fuehrt die
        Dalvik-Methode aus, marshallt die Rueckgabe zurueck in ein jvalue (int/Handle)."""
        args = [self._to_py(w) for w in arg_words]
        ret = self.vm.invoke(cls, name, sig, args)
        return self._to_jni(ret)

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
    def _to_py(self, word):
        if word in self._h2py:
            return self._h2py[word]
        return int(word) & _M64

    def _to_jni(self, val):
        if val is None:
            return 0
        if isinstance(val, int):
            return val & _M64
        # Referenz -> neues Handle ueber die Fassade, in der Tabelle hinterlegen
        kind = "jstring" if isinstance(val, JString) else ("jobject" if isinstance(val, JObject) else "jarray")
        kw = {"text": val.text} if isinstance(val, JString) else ({"cls": val.cls} if isinstance(val, JObject) else {})
        h = int(self.jni._new_handle(kind, **kw)) & _M64
        self._h2py[h] = val
        return h


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
        if (cls, name, sig) in self.vm.registry:
            return True
        prov = getattr(self.vm, "class_provider", None)
        if prov is not None:                       # lazy: Klasse (inkl. Methoden) nachladen
            try:
                for md in (prov.get_class(cls) or []):
                    self.vm.registry.setdefault((md.cls, md.name, md.sig), md)
            except Exception:
                pass
        return (cls, name, sig) in self.vm.registry

    def call_from_native(self, cls, name, sig, arg_words, static: bool):
        """Native Argumentworte -> Python marshallen, Dalvik-Methode auf der laufenden VM ausfuehren,
        Rueckgabe zurueck in ein jvalue (int/Handle) marshallen."""
        args = [self._to_py(w) for w in arg_words]
        ret = self.vm.invoke(cls, name, sig, args)
        return self._to_jni(ret)

    # ---- Marshalling -------------------------------------------------------
    def _to_py(self, word):
        if word in self._h2py:
            return self._h2py[word]
        return int(word) & _M64

    def _to_jni(self, val):
        if val is None:
            return 0
        if isinstance(val, int):
            return val & _M64
        kind = ("jstring" if isinstance(val, JString)
                else ("jobject" if isinstance(val, JObject) else "jarray"))
        kw = ({"text": val.text} if isinstance(val, JString)
              else ({"cls": val.cls} if isinstance(val, JObject) else {}))
        try:
            h = int(self.jni._new_handle(kind, **kw)) & _M64
        except Exception:
            return 0
        self._h2py[h] = val
        return h
