# -*- coding: utf-8 -*-
"""core.java.seam_config — Konfigurierbare 2-Ebenen-Seam-Architektur (Paket 1).

Trennung zwischen:
  - Ebene 1 (App-/Loader-Entpackmodus): ``faithful`` (echtes Self-Unpacking ins VFS),
    ``self_cache`` (cached nur von der App selbst entpackte ELFs nach data/vfs_selfcache),
    ``noop_native`` (leichtgewichtig ohne nativen Code).
  - Ebene 2 (Deklarative Nahtstellen / Cutoffs):
      * ``java_cutoffs``: gezielte Java-Methoden-Overrides in ``vm.method_overrides``
      * ``jni_cutoffs``:  Grenz-Aktionspunkte mit ``skip_native=True`` an ``JniBoundaryHook``

100 % generisch, ip_guard-konform (``allow_external_dec_cache=False`` per Default).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.java.objects import JString


SOLOADER_OVERRIDE_KEYS = (
    ("Lcom/facebook/soloader/SoLoader;", "loadLibrary", "(Ljava/lang/String;)Z"),
    ("Lcom/facebook/soloader/SoLoader;", "loadLibrary", "(Ljava/lang/String;I)Z"),
    ("Lcom/facebook/soloader/SoLoader;", "init", "(Landroid/content/Context;I)V"),
    ("Lcom/facebook/soloader/SoLoader;", "init",
     "(Landroid/content/Context;ILcom/facebook/soloader/SoFileLoader;)V"),
    ("Lcom/facebook/soloader/nativeloader/NativeLoader;", "loadLibrary",
     "(Ljava/lang/String;)Z"),
)


def _wrap_java_ret(ret_val: Any, sig: str):
    """Normalisiert einen Java-Cutoff-Rueckgabewert fuer ``vm.method_overrides``.

    DalvikVM prueft ``if ov is not None:``; daher wird auch ``None`` als Callable
    gekapstelt, damit ein expliziter Void-/Null-Cutoff die Smali-Methode abkuerzt.
    """
    if callable(ret_val):
        return ret_val
    if isinstance(ret_val, bool):
        v = 1 if ret_val else 0
        return lambda _a, _v=v: _v
    if isinstance(ret_val, str) and sig and sig.endswith("Ljava/lang/String;"):
        return lambda _a, _s=ret_val: JString(_s)
    return lambda _a, _v=ret_val: _v


@dataclass
class SeamConfig:
    """Konfigurierbares 2-Ebenen-Seam-Modell fuer glaeserne App-Laeufe."""

    unpack_mode: str = "self_cache"                  # "faithful" | "self_cache" | "noop_native"
    allow_external_dec_cache: bool = False           # ip_guard: Default False
    self_cache_dir: str = "data/vfs_selfcache"
    appdata_dir: str = "data/vfs_appdata"
    persist_appdata: bool = True
    java_cutoffs: List[Dict[str, Any]] = field(default_factory=list)
    jni_cutoffs: List[Dict[str, Any]] = field(default_factory=list)
    native_preload_libs: Optional[List[str]] = None  # None = alle JNI_OnLoad erlauben; Liste = Filter

    PROFILES: Dict[str, Dict[str, Any]] = field(init=False, repr=False, default_factory=dict)

    _PROFILES = {
        "faithful": {
            "unpack_mode": "faithful",
            "allow_external_dec_cache": False,
            "persist_appdata": True,
        },
        "self_cache": {
            "unpack_mode": "self_cache",
            "allow_external_dec_cache": False,
            "persist_appdata": True,
        },
        "noop_native": {
            "unpack_mode": "noop_native",
            "allow_external_dec_cache": False,
            "persist_appdata": True,
        },
    }

    def __post_init__(self):
        if self.unpack_mode not in ("faithful", "self_cache", "noop_native"):
            self.unpack_mode = "self_cache"
        self.PROFILES = copy.deepcopy(self._PROFILES)

    @classmethod
    def from_profile(cls, name: str) -> "SeamConfig":
        key = (name or "self_cache").strip().lower()
        # Freundliche GUI-Labels mit normalisieren
        if "faithful" in key:
            key = "faithful"
        elif "noop" in key or "java" in key:
            key = "noop_native"
        elif "cache" in key:
            key = "self_cache"
        spec = cls._PROFILES.get(key, cls._PROFILES["self_cache"])
        return cls(**copy.deepcopy(spec))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unpack_mode": self.unpack_mode,
            "allow_external_dec_cache": bool(self.allow_external_dec_cache),
            "self_cache_dir": self.self_cache_dir,
            "appdata_dir": self.appdata_dir,
            "persist_appdata": bool(self.persist_appdata),
            "java_cutoffs": copy.deepcopy(self.java_cutoffs),
            "jni_cutoffs": copy.deepcopy(self.jni_cutoffs),
            "native_preload_libs": (list(self.native_preload_libs)
                                    if self.native_preload_libs is not None else None),
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SeamConfig":
        if not isinstance(d, dict):
            return cls()
        return cls(
            unpack_mode=str(d.get("unpack_mode", "self_cache")),
            allow_external_dec_cache=bool(d.get("allow_external_dec_cache", False)),
            self_cache_dir=str(d.get("self_cache_dir", "data/vfs_selfcache")),
            appdata_dir=str(d.get("appdata_dir", "data/vfs_appdata")),
            persist_appdata=bool(d.get("persist_appdata", True)),
            java_cutoffs=copy.deepcopy(d.get("java_cutoffs") or []),
            jni_cutoffs=copy.deepcopy(d.get("jni_cutoffs") or []),
            native_preload_libs=(list(d["native_preload_libs"])
                                 if d.get("native_preload_libs") is not None else None),
        )

    def should_run_onload(self, lib_name: Optional[str]) -> bool:
        """Darf JNI_OnLoad dieser Lib nativ laufen? None = immer erlauben."""
        if self.unpack_mode == "noop_native":
            return False
        if self.native_preload_libs is None:
            return True
        if not lib_name:
            return False
        norm = str(lib_name).strip()
        return norm in self.native_preload_libs or ("lib%s.so" % norm) in self.native_preload_libs

    def apply_to_vm(self, vm, native_fusion: bool = True, jni_hook=None) -> Dict[str, int]:
        """Wendet Ebene 1 (Loader-Overrides) und Ebene 2 (Java/JNI-Cutoffs) auf VM & Hook an."""
        stats = {"soloader_removed": 0, "java_cutoffs": 0, "jni_cutoffs": 0}
        if vm is not None and hasattr(vm, "method_overrides"):
            if native_fusion and self.unpack_mode in ("faithful", "self_cache"):
                for k in SOLOADER_OVERRIDE_KEYS:
                    if k in vm.method_overrides:
                        vm.method_overrides.pop(k, None)
                        stats["soloader_removed"] += 1
            else:
                # noop_native / kein Fusion -> SoLoader-Kurzschluss aktiv halten
                stubs = getattr(getattr(vm, "runtime", None), "stubs", None)
                if stubs is not None and hasattr(stubs, "_install_glass_overrides"):
                    try:
                        stubs._install_glass_overrides()
                    except Exception:
                        pass

            for jc in self.java_cutoffs:
                if not isinstance(jc, dict) or not jc.get("enabled", True):
                    continue
                c, n, s = jc.get("cls"), jc.get("name"), jc.get("sig")
                if not (c and n and s):
                    continue
                vm.method_overrides[(c, n, s)] = _wrap_java_ret(jc.get("ret"), s)
                stats["java_cutoffs"] += 1

        if jni_hook is not None and hasattr(jni_hook, "add_action_point"):
            for ac in self.jni_cutoffs:
                if not isinstance(ac, dict) or not ac.get("enabled", True):
                    continue
                try:
                    jni_hook.add_action_point(
                        fnptr=ac.get("fnptr"),
                        this_cls=ac.get("this_cls"),
                        method_name=ac.get("method_name"),
                        method_sig=ac.get("method_sig"),
                        cond=ac.get("cond"),
                        arg_assigns=ac.get("arg_assigns"),
                        ret_expr=ac.get("ret_expr"),
                        skip_native=bool(ac.get("skip_native", True)),
                        one_shot=bool(ac.get("one_shot", False)),
                        hit=ac.get("hit"),
                    )
                    stats["jni_cutoffs"] += 1
                except Exception:
                    pass
        return stats
