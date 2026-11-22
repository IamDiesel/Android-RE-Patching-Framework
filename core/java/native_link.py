# -*- coding: utf-8 -*-
"""core.java.native_link — verbindet native Java-Methoden mit echtem Code auf der arm64-Engine.

Im gläsernen Lauf fallen Aufrufe nativer Methoden sonst in den Stub-Pfad (Rueckgabe 0, die echte
Library laeuft nicht). Dieser Router schiebt sich VOR die Stubs: steht `(cls,name,sig)` in der
`NativeMethodTable`, wird `JavaToNativeBridge.call_native(fnptr, ...)` gerufen -> die Funktion
laeuft wirklich in `arm64mini`, inklusive des Grenz-Hooks (`jni_boundary`), also beobachtbar und
manipulierbar. Trifft nichts zu, delegiert er an die bestehende Runtime (Stubs).

Die Tabelle wird gefuellt durch RegisterNatives/JNI_OnLoad-Emulation oder - fuer Tests/Bootstrap -
direkt. `System.loadLibrary` kann ueber einen Callback das Laden + JNI_OnLoad anstossen.
Zielagnostisch, additiv, Default inaktiv."""
from __future__ import annotations

from core.java.objects import JObject

_M64 = (1 << 64) - 1


class NativeMethodTable:
    """Abbildung registrierter nativer Methoden auf Funktionszeiger im geladenen Code."""

    def __init__(self):
        self._exact = {}   # (cls,name,sig) -> dict(fnptr, ret, static)
        self._by_cn = {}   # (cls,name) -> dict(...)  (statisch geerntete Java_-Symbole, sig unbekannt)

    def register(self, cls, name, sig, fnptr, ret="int", static=True):
        self._exact[(cls, name, sig)] = {"fnptr": int(fnptr) & _M64, "ret": ret, "static": bool(static)}
        return self._exact[(cls, name, sig)]

    def register_static_symbol(self, cls, name, fnptr, ret="int", static=True):
        """AP2.1 Naht 3: exportiertes Java_<Class>_<method>-Symbol (sig unbekannt) -> Name-Fallback."""
        self._by_cn.setdefault((cls, name), {"fnptr": int(fnptr) & _M64, "ret": ret, "static": bool(static)})
        return self._by_cn[(cls, name)]

    def lookup(self, cls, name, sig):
        ent = self._exact.get((cls, name, sig))
        if ent is None:
            ent = self._by_cn.get((cls, name))   # Name-Fallback (Merged-SO lib*_so()I etc.)
        return ent

    def __len__(self):
        return len(self._exact)

    def entries(self):
        return dict(self._exact)


class NativeLinkRuntime:
    """VM-`runtime`-Adapter: Tabelle zuerst (echte native Ausfuehrung), sonst `inner` (Stubs)."""

    def __init__(self, inner, bridge, table):
        self.inner = inner
        self.bridge = bridge
        self.table = table
        self.stubs = getattr(inner, "stubs", None)

    def resolve_invoke(self, cls, name, sig, args, static):
        ent = self.table.lookup(cls, name, sig)
        if ent is not None:
            # Instanz vs. statisch: bei Instanzmethoden ist args[0] = this (jobject fuer die JNI-Seite)
            if not ent["static"] and args and isinstance(args[0], JObject):
                this_cls = args[0].cls
                native_args = list(args[1:])
            else:
                this_cls = cls
                native_args = list(args)
            cn = self.bridge.call_native
            co = getattr(cn, "__code__", None)
            if co is not None and "method_name" in getattr(co, "co_varnames", ()):
                ret = cn(int(ent["fnptr"]), this_cls, native_args, ret=ent["ret"],
                         method_name=name, method_sig=sig)
            else:
                ret = cn(int(ent["fnptr"]), this_cls, native_args, ret=ent["ret"])
            return True, ret
        if self.inner is not None and hasattr(self.inner, "resolve_invoke"):
            return self.inner.resolve_invoke(cls, name, sig, args, static)
        return False, 0


def install_native_link(vm, eng, jni, table=None, emu=None, on_load_library=None, on_load_path=None):
    """Haengt den Native-Router an eine VM. Rueckgabe: (table, bridge).

    `on_load_library(name)` wird von `System.loadLibrary(name)`/`Runtime.loadLibrary*` gerufen (eine
    GEBUNDENE Lib, die der Linker laedt). `on_load_path(path)` wird von `System.load(absPfad)`/
    `Runtime.load*` gerufen - DAS ist der app-eigene Entpack-Weg: die App entpackt ihre gepackte Lib
    SELBST, schreibt die Klartext-`.so` ins lokale (virtuelle) Dateisystem und laedt genau diesen
    Pfad. Das OS entpackt NICHTS fuer die App; es stellt nur VFS + Loader bereit."""
    from core.java.bridge import JavaToNativeBridge
    table = table if table is not None else NativeMethodTable()
    bridge = JavaToNativeBridge(eng, jni, emu)
    vm.bridge = bridge
    vm.runtime = NativeLinkRuntime(getattr(vm, "runtime", None), bridge, table)
    vm.native_table = table
    vm._fusion_load_library = on_load_library
    vm._fusion_load_path = on_load_path
    if on_load_library is not None or on_load_path is not None:
        _install_loadlibrary_stub(vm, on_load_library, on_load_path)
    return table, bridge


def _strval(v):
    try:
        from core.java.objects import JString
        return v.text if isinstance(v, JString) else v
    except Exception:
        return v


def _install_loadlibrary_stub(vm, on_load_library, on_load_path=None):
    """Trennt `loadLibrary(name)` (gebundene Lib) von `load(absPfad)` (app-entpackte Lib aus VFS).
    Faengt System.* und Runtime.* ab."""
    inner = getattr(vm, "runtime", None)
    _LIBNAME = ("loadLibrary", "loadLibrary0")
    _LOADPATH = ("load", "load0", "nativeLoad")

    def _cb(cls, name, sig, args, static):
        if cls in ("Ljava/lang/System;", "Ljava/lang/Runtime;"):
            arg = next((_strval(x) for x in (args or ()) if isinstance(_strval(x), str)), None)
            if name in _LIBNAME and on_load_library is not None:
                try: on_load_library(arg)
                except Exception: pass
                return True, None
            if name in _LOADPATH and on_load_path is not None:
                try: on_load_path(arg)
                except Exception: pass
                return True, None
        if inner is not None and hasattr(inner, "resolve_invoke"):
            return inner.resolve_invoke(cls, name, sig, args, static)
        return False, 0

    _orig_inner = inner

    class _LoadLibWrap:
        inner = _orig_inner
        stubs = getattr(_orig_inner, "stubs", None)

        def resolve_invoke(self, cls, name, sig, args, static):
            return _cb(cls, name, sig, args, static)

    vm.runtime = _LoadLibWrap()
