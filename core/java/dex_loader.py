# -*- coding: utf-8 -*-
"""core.java.dex_loader — DEX-Klassen-Loader + android.*-Stub-Harness (J3 / MS-4).

- `DexClassLoader`: registriert die Methoden einer DEX-Klasse (via `dex_code.decode_code`) in einer
  `DalvikVM`, mit lazy `<clinit>`-Ausfuehrung (statische Initialisierung in Reihenfolge der ersten Nutzung).
- `AndroidStubs`: bedarfsgetriebene Framework-Stubs (Coverage-Loop wie WS-E7). Nicht aufgeloeste
  `Landroid/*` / `Ljava/*` / `Lkotlin/*`-Aufrufe werden protokolliert (`.missing`) statt zu crashen;
  ein kleiner Satz haeufiger Stubs ist eingebaut. Zielagnostisch.
"""
from __future__ import annotations

import math as _math
import re as _re
import struct as _struct

from core.java.dex_code import decode_code
from core.java.dalvik import DalvikException, DalvikUnknownOpcode, VmBudgetExceeded
from core.java.deviation_journal import DebugStop
from core.java.objects import JString, JObject, JArray
from core.java.device_profile import DeviceProfile

_FRAMEWORK_PREFIXES = ("Landroid/", "Ljava/", "Ljavax/", "Lkotlin/", "Lkotlinx/", "Ldalvik/",
                       "Lsun/", "Lorg/json/", "Lcom/google/")
_TIME_UNITS = (
    ("NANOSECONDS", "toNanos", 1),
    ("MICROSECONDS", "toMicros", 1_000),
    ("MILLISECONDS", "toMillis", 1_000_000),
    ("SECONDS", "toSeconds", 1_000_000_000),
    ("MINUTES", "toMinutes", 60_000_000_000),
    ("HOURS", "toHours", 3_600_000_000_000),
    ("DAYS", "toDays", 86_400_000_000_000),
)


def _unpack_ieee_float(val, is_f32: bool) -> float:
    """Decode Dalvik IEEE-754 int bit-patterns (or boxed/float values) into Python float."""
    if isinstance(val, JObject):
        return _unpack_ieee_float(val.fields.get("value", 0.0), is_f32)
    if isinstance(val, float):
        return val
    if isinstance(val, int) and not isinstance(val, bool):
        fmt, mask, out_fmt = ("<I", 0xFFFFFFFF, "<f") if is_f32 else ("<Q", 0xFFFFFFFFFFFFFFFF, "<d")
        return _struct.unpack(out_fmt, _struct.pack(fmt, val & mask))[0]
    try:
        return float(val or 0.0)
    except Exception:
        return 0.0


def _as_py_f32(val) -> float:
    return _unpack_ieee_float(val, True)


def _as_py_f64(val) -> float:
    return _unpack_ieee_float(val, False)


def _fmt_java_float(fv: float) -> str:
    if _math.isnan(fv):
        return "NaN"
    if _math.isinf(fv):
        return "-Infinity" if fv < 0 else "Infinity"
    return str(fv)


def is_framework(cls: str) -> bool:
    return bool(cls) and cls.startswith(_FRAMEWORK_PREFIXES)


class DexClassLoader:
    """Laedt DEX-Klassen in eine DalvikVM. `<clinit>` lazy bei erster Registrierung."""

    def __init__(self, dex, vm=None):
        self.dex = dex
        self._vm = vm
        self._index = None          # name -> class_def
        self.loaded = set()         # registrierte Klassen
        self.initialized = set()    # <clinit> gelaufen
        self._supers = {}
        self._ifaces = {}

    def _ensure_index(self):
        if self._index is None:
            self._index = {}
            for cd in self.dex.class_defs():
                self._index.setdefault(cd["name"], cd)
        return self._index

    def has_class(self, name) -> bool:
        return name in self._ensure_index()

    def ensure_class(self, name) -> bool:
        if self._vm is not None and self.has_class(name):
            self.register_class(self._vm, name, run_clinit=False)
            return True
        return False

    def register_class(self, vm, name, run_clinit=True):
        """Registriert alle (nicht-nativen, nicht-abstrakten) Methoden der Klasse in der VM."""
        self._vm = vm
        idx = self._ensure_index()
        cd = idx.get(name)
        if cd is None:
            return []
        if name in self.loaded:
            return [m for m in vm.registry if m[0] == name]
        mds = []
        for m in self.dex.class_methods(cd):
            if not m.code_off or m.is_native or m.is_abstract:
                continue
            ci = self.dex.code_item(m.code_off)
            if ci is None:
                continue
            md = decode_code(self.dex, ci, m.cls, m.name, m.sig, m.access)
            vm.register(md)
            mds.append(md)
        # DEX static_values_off + .super/.implements vor <clinit> einpflegen (Dalvik-Semantik)
        if cd.get("super"):
            self._supers[name] = cd["super"]
            if hasattr(vm, "class_provider") and vm.class_provider is not None:
                sc = getattr(vm.class_provider, "_super_cache", None)
                if isinstance(sc, dict):
                    sc.setdefault(name, cd["super"])
        if cd.get("interfaces"):
            self._ifaces[name] = list(cd["interfaces"])
            if hasattr(vm, "class_provider") and vm.class_provider is not None:
                ic = getattr(vm.class_provider, "_iface_cache", None)
                if isinstance(ic, dict):
                    ic.setdefault(name, list(cd["interfaces"]))
        if hasattr(self.dex, "static_field_values"):
            try:
                svals = self.dex.static_field_values(cd)
                if svals:
                    st = vm.statics.setdefault(name, {})
                    for fn, fv in svals.items():
                        if isinstance(fv, tuple) and len(fv) == 2:
                            tag, inner = fv
                            if tag == "string":
                                fv = JString(inner)
                            elif tag == "type":
                                dotted = (inner[1:-1].replace("/", ".")
                                          if isinstance(inner, str) and inner.startswith("L") and inner.endswith(";")
                                          else inner)
                                fv = JObject("Ljava/lang/Class;", {"name": dotted})
                        st.setdefault(fn, fv)
            except Exception:
                pass
        self.loaded.add(name)
        if run_clinit:
            self.run_clinit(vm, name)
        return mds

    def run_clinit(self, vm, name):
        """Fuehrt `<clinit>()V` der Klasse aus (einmalig), falls vorhanden."""
        if name in self.initialized:
            return
        self.initialized.add(name)
        if hasattr(vm, "_clinit_done"):
            vm._clinit_done.add(name)
        if (name, "<clinit>", "()V") in vm.registry:
            try:
                vm.invoke(name, "<clinit>", "()V", ())
            except Exception:
                pass   # clinit-Fehler sind bounded (Coverage-Loop), nicht fatal


class VirtualFS:
    """Minimales virtuelles Dateisystem fuer Assets/Ressourcen (J3/4c). Pfad -> bytes."""

    def __init__(self):
        self.files = {}

    def mount(self, path, data):
        self.files[str(path)] = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")

    def mount_zip(self, zip_path, prefix="assets/"):
        """Alle Eintraege unter prefix aus einem ZIP/APK ins VFS laden (ohne prefix als Schluessel)."""
        import zipfile
        n = 0
        with zipfile.ZipFile(zip_path) as z:
            for nm in z.namelist():
                if nm.startswith(prefix) and not nm.endswith("/"):
                    self.files[nm[len(prefix):]] = z.read(nm)
                    n += 1
        return n

    def exists(self, path):
        return str(path) in self.files

    def read(self, path):
        return self.files.get(str(path))

    def list(self, prefix=""):
        return sorted(k for k in self.files if k.startswith(prefix))


class AndroidStubs:
    """Bedarfsgetriebene Framework-Stubs + Coverage-Loop. In JavaRuntime.resolve_invoke eingehaengt."""

    def __init__(self, vfs=None, vm=None, profile=None):
        self.missing = []
        self._missing_idx = {}
        self._stubs = {}
        self._overridden_stubs = []
        self._q_seq = 0
        self.activity_callbacks = []   # registrierte Application$ActivityLifecycleCallbacks
        self.pending_runnables = []    # geplante Handler/View/UI-Runnables
        self._prefs_store = {}         # In-Memory SharedPreferences
        self._dynamic_dcls = []        # dynamisch nachgeladene DexClassLoader
        self.application = None         # AP1.3: echte Application-Instanz aus Phase 1
        self._intent_singleton = None
        self._window_singleton = None
        self._activity_finished = False
        self._activity_stack = []
        import time as _t_init
        self._pending_nav_intent = None
        self._clock_ms = int(_t_init.time() * 1000)
        self._clock_tz = None
        self._clock_frozen = False
        self._clock_step_ms = 100
        self._clock_offset_ms = 0
        self._clock_custom = False
        self._nano_seq = 0
        self.vm = vm                 # VM-Referenz fuer reflektiven Dispatch (generisch)
        self.profile = profile or DeviceProfile()   # konfigurierbare android.*-Konstanten
        self._by_name = {}           # (name,sig) -> fn: vererbungs-tolerante Framework-Methoden
        self._tostr_guard = set()
        self.vfs = vfs if vfs is not None else VirtualFS()
        self._install_builtins()
        self._install_reflection()
        self._install_android_sdk()
        self._install_aosp_gaps()
        self._install_regex()
        self._install_collections()
        self._install_profile_consts()
        self._install_android_objects()
        self._install_activity_ctx()   # AP1.3: zuletzt -> gewinnt gegen Context-Default-Stubs
        self._install_glass_overrides()
        try:
            from core.java.sqlite_prefs_bridge import install_sqlite_and_prefs_grundset, attach_g3_vm_hook
            install_sqlite_and_prefs_grundset(self)
        except Exception:
            attach_g3_vm_hook = None
        for _mod_name, _fn_name in (
            ("core.java.engine_stubs_g14", "install_g14_stubs"),
            ("core.java.ipc_oauth_g10", "install_g10_stubs"),
            ("core.java.crypto_billing_g11", "install_g11_stubs"),
            ("core.java.net_protocols_g7", "install_g7_java_stubs"),
            ("core.java.nav_rx_paging_g8", "install_g8_stubs"),
            ("core.java.hardware_media_g9", "install_g9_stubs"),
            ("core.java.virtual_clock", "install_virtual_clock_stubs"),
            ("core.java.sdk_breadth_g15", "install_g15_stubs"),
            ("core.java.view_breadth_g16", "install_g16_stubs"),
        ):
            try:
                _m = __import__(_mod_name, fromlist=[_fn_name])
                getattr(_m, _fn_name)(self)
            except Exception:
                pass
        self._apply_profile_clock()
        if vm is not None:
            vm._stubs_ref = self
            self._seed_primitive_types(vm)
            if attach_g3_vm_hook is not None:
                attach_g3_vm_hook(self, vm)
            for _hmod, _hfn in (
                ("core.java.engine_stubs_g14", "attach_g14_vm_hook"),
                ("core.java.nav_rx_paging_g8", "attach_g8_vm_hook"),
            ):
                try:
                    _m = __import__(_hmod, fromlist=[_hfn])
                    getattr(_m, _hfn)(self, vm)
                except Exception:
                    pass

    def register(self, cls, name, sig, fn):
        key = (cls, name, sig)
        if key in self._stubs:
            self._overridden_stubs.append(key)
        self._stubs[key] = fn

    def register_by_name(self, name, sig, fn):
        """Framework-Methode vererbungs-tolerant (unabhaengig von der deklarierenden Klasse)."""
        self._by_name[(name, sig)] = fn

    def _record_missing(self, cls, name, sig):
        if len(self.missing) == 0 and self._missing_idx:
            self._missing_idx.clear()
        key = (cls, name, sig)
        idx = self._missing_idx.get(key)
        if idx is not None and (
            idx >= len(self.missing)
            or (self.missing[idx].get("cls"), self.missing[idx].get("name"), self.missing[idx].get("sig")) != key
        ):
            self._missing_idx = {
                (m.get("cls"), m.get("name"), m.get("sig")): i
                for i, m in enumerate(self.missing)
                if isinstance(m, dict)
            }
            idx = self._missing_idx.get(key)
        if idx is not None:
            self.missing[idx]["count"] = int(self.missing[idx].get("count", 1)) + 1
        elif len(self.missing) < 5000:
            self._missing_idx[key] = len(self.missing)
            self.missing.append({"cls": cls, "name": name, "sig": sig, "count": 1})
        vm = getattr(self, "vm", None)
        if vm is not None and hasattr(vm, "deviations"):
            vm.deviations.record("missing_stub", f"{cls}->{name}{sig}", step=getattr(vm, "steps", 0))

    def resolve(self, cls, name, sig, args, static, allow_default: bool = True, allow_by_name: bool = True):
        fn = None
        if not static and cls == "Ljava/lang/Object;" and name != "<init>" and args and isinstance(args[0], JObject):
            fn = self._stubs.get((args[0].cls, name, sig))
        if fn is None:
            fn = self._stubs.get((cls, name, sig))
        if fn is None and allow_by_name:
            fn = self._by_name.get((name, sig))   # geerbte Framework-Methode (Context, Object, ...)
        if fn is not None:
            return True, fn(args)
        if not static and cls == "Landroid/content/Intent;" and name == "putExtra" and args:
            return True, self._intent_put_extra(args, sig)
        if not static and cls == "Landroid/content/Intent;" and name == "putExtras" and args:
            return True, self._intent_put_extras(args)
        if allow_default and is_framework(cls):
            # bedarfsgetrieben: protokollieren + sauberer Default (0/null)
            self._record_missing(cls, name, sig)
            # Konstruktoren liefern den Empfaenger zurueck (args[0]) statt 0
            if name == "<init>" and args:
                return True, args[0]
            return True, 0
        return False, 0

    def resolve_phased(self, wants, rcls, cls, name, sig, args, static):
        """Mehrphasige Stub-Aufloesung: erst explizite Stubs ueber wants, dann _by_name, dann Default."""
        for want in wants:
            handled, ret = self.resolve(want, name, sig, args, static, allow_default=False, allow_by_name=False)
            if handled:
                return True, ret
        fn_bn = self._by_name.get((name, sig))
        if fn_bn is not None:
            return True, fn_bn(args)
        if not static and (cls == "Landroid/content/Intent;" or rcls == "Landroid/content/Intent;") and args:
            if name == "putExtra":
                return True, self._intent_put_extra(args, sig)
            if name == "putExtras":
                return True, self._intent_put_extras(args)
        target = cls if is_framework(cls) else (rcls if is_framework(rcls) else None)
        if target is not None:
            self._record_missing(target, name, sig)
            if name == "<init>" and args:
                return True, args[0]
            return True, 0
        return False, 0

    @staticmethod
    def _bundle_map(obj):
        if not isinstance(obj, JObject):
            return {}
        m_a = obj.fields.get("map")
        m_b = obj.fields.get("$map")
        if isinstance(m_a, dict) and isinstance(m_b, dict):
            if m_a is not m_b:
                m_a.update(m_b)
                obj.fields["$map"] = m_a
            return m_a
        if isinstance(m_a, dict):
            obj.fields["$map"] = m_a
            return m_a
        if isinstance(m_b, dict):
            obj.fields["map"] = m_b
            return m_b
        m = {}
        obj.fields["map"] = m
        obj.fields["$map"] = m
        return m

    @staticmethod
    def _intent_extras_map(intent_obj):
        if isinstance(intent_obj, JObject):
            ex = intent_obj.fields.get("_extras")
            if not isinstance(ex, dict):
                ex = {}
                intent_obj.fields["_extras"] = ex
            bd = intent_obj.fields.get("extras")
            if isinstance(bd, JObject):
                bmap = AndroidStubs._bundle_map(bd)
                if bmap is not ex:
                    ex.update(bmap)
                bd.fields["map"] = ex
                bd.fields["$map"] = ex
            return ex
        return {}

    def _intent_get_extras_bundle(self, a):
        if a and isinstance(a[0], JObject):
            ex = self._intent_extras_map(a[0])
            bd = a[0].fields.get("extras")
            if not isinstance(bd, JObject):
                bd = JObject("Landroid/os/Bundle;", {"map": ex, "$map": ex})
                a[0].fields["extras"] = bd
            else:
                self._bundle_map(bd)
                bd.fields["map"] = ex
                bd.fields["$map"] = ex
            return bd
        return self.make_bundle()

    def _intent_put_extra(self, a, sig=""):
        if a and isinstance(a[0], JObject):
            ex = self._intent_extras_map(a[0])
            key = self._bundle_key(a)
            if key:
                val = a[2] if len(a) > 2 else None
                if sig.endswith(";Z)Landroid/content/Intent;") or sig.startswith("(Ljava/lang/String;Z)"):
                    val = 1 if val else 0
                ex[key] = val
        return a[0] if a else None

    def _intent_put_extras(self, a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JObject):
            ex = self._intent_extras_map(a[0])
            src = a[1]
            if src.cls == "Landroid/content/Intent;":
                ex.update(self._intent_extras_map(src))
            elif isinstance(src.fields.get("map"), dict):
                ex.update(src.fields["map"])
        return a[0] if a else None

    def _apply_profile_clock(self):
        prof = getattr(self, "profile", None)
        if prof is None or not hasattr(prof, "get"):
            return
        tz = prof.get("clock_tz")
        ep = prof.get("clock_epoch_ms")
        iso = prof.get("clock_iso")
        frz = bool(prof.get("clock_frozen", False))
        if tz is not None or ep is not None or iso is not None or frz:
            try:
                from core.java.virtual_clock import set_virtual_clock
                set_virtual_clock(self, epoch_ms=ep, iso_str=iso, tz_name=tz, frozen=frz)
            except Exception:
                pass

    def current_time_ms(self, advance=False, step_ms=100):
        cur = int(getattr(self, "_clock_ms", 1700000000000))
        if advance and not getattr(self, "_clock_frozen", False):
            st = int(getattr(self, "_clock_step_ms", step_ms) if step_ms == 100 else step_ms)
            cur += st
            self._clock_ms = cur
        return cur

    def current_datetime(self, tz_name=None):
        import datetime as _dt
        ms = int(self.current_time_ms(advance=False))
        eff = tz_name or getattr(self, "_clock_tz", None)
        if eff:
            from core.java.virtual_clock import resolve_tzinfo
            return _dt.datetime.fromtimestamp(ms / 1000.0, tz=resolve_tzinfo(eff))
        return _dt.datetime.fromtimestamp(ms / 1000.0)

    def format_status_clock(self):
        dt = self.current_datetime()
        if getattr(self, "_clock_custom", False):
            return f"{dt:%d.%m.%Y %H:%M}" + (" ❄️" if getattr(self, "_clock_frozen", False) else "")
        return f"{dt:%H:%M}"

    def _tick_ms(self, step=100):
        return self.current_time_ms(advance=True, step_ms=step)

    # ---- eingebaute Stubs --------------------------------------------------
    def _install_builtins(self):
        def _log(args): return 0
        for lvl in ("d", "e", "i", "w", "v"):
            self.register("Landroid/util/Log;", lvl, "(Ljava/lang/String;Ljava/lang/String;)I", _log)
        self.register("Ljava/lang/System;", "currentTimeMillis", "()J", lambda a: self._tick_ms(100))
        self.register("Ljava/lang/Object;", "<init>", "()V", lambda a: None)
        self.register("Ljava/lang/Object;", "clone", "()Ljava/lang/Object;", self._obj_clone)
        # --- Enum (ordinal/name fuer Kotlin when-Switch) ---
        def _enum_name(a):
            return JString(str(a[0].fields.get("__ename", "")) if a and isinstance(a[0], JObject) else "")

        def _enum_ord(a):
            return a[0].fields.get("__ordinal", 0) if a and isinstance(a[0], JObject) else 0

        def _enum_hash(a):
            if a and isinstance(a[0], JObject):
                return self._java_strhash(
                    f"{a[0].cls}:{a[0].fields.get('__ename', '')}:{a[0].fields.get('__ordinal', 0)}"
                )
            return 0

        def _enum_decl_cls(a):
            if not a or not isinstance(a[0], JObject):
                return None
            c = a[0].cls
            if self.vm is not None:
                cur = c
                for _ in range(8):
                    sup = self.vm._superclass(cur)
                    if sup in (None, "Ljava/lang/Enum;", "Ljava/lang/Object;"):
                        break
                    cur = sup
                c = cur
                if hasattr(self.vm, "intern_class"):
                    return self.vm.intern_class(c)
            dotted = c[1:-1].replace("/", ".") if c.startswith("L") and c.endswith(";") else c
            return JObject("Ljava/lang/Class;", {"name": dotted})

        def _enum_value_of(a):
            cls_obj = a[0] if a else None
            ename = self._txt(a[1]) if len(a) > 1 else ""
            edesc = self._desc_from_class_obj(cls_obj) if cls_obj is not None else None
            if edesc and self.vm is not None:
                cur = self.vm.statics.get(edesc, {}).get(ename)
                if isinstance(cur, JObject):
                    return cur
            return JObject(edesc or "Ljava/lang/Enum;", {"__ename": ename or "", "__ordinal": 0})

        def _enum_clone(a):
            raise DalvikException("java.lang.CloneNotSupportedException")

        self.register("Ljava/lang/Enum;", "<init>", "(Ljava/lang/String;I)V", self._enum_init)
        self.register("Ljava/lang/Enum;", "toString", "()Ljava/lang/String;", _enum_name)
        self.register("Ljava/lang/Enum;", "name", "()Ljava/lang/String;", _enum_name)
        self.register("Ljava/lang/Enum;", "ordinal", "()I", _enum_ord)
        self.register("Ljava/lang/Enum;", "hashCode", "()I", _enum_hash)
        self.register("Ljava/lang/Enum;", "getDeclaringClass", "()Ljava/lang/Class;", _enum_decl_cls)
        self.register("Ljava/lang/Enum;", "valueOf", "(Ljava/lang/Class;Ljava/lang/String;)Ljava/lang/Enum;",
                      _enum_value_of)
        self.register("Ljava/lang/Enum;", "clone", "()Ljava/lang/Object;", _enum_clone)
        self.register_by_name("ordinal", "()I", _enum_ord)
        self.register_by_name("name", "()Ljava/lang/String;", _enum_name)
        self.register("Ljava/lang/Integer;", "valueOf", "(I)Ljava/lang/Integer;",
                      lambda a: JObject("Ljava/lang/Integer;", {"value": int(a[0]) if a and isinstance(a[0], (int, bool)) else 0}))
        self.register("Ljava/lang/Integer;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Integer;",
                      lambda a: JObject("Ljava/lang/Integer;", {"value": self._parse_java_int(a[0] if a else None, 10, 32)}))
        self.register("Ljava/lang/Integer;", "intValue", "()I", self._unbox)
        self.register("Ljava/lang/Boolean;", "valueOf", "(Z)Ljava/lang/Boolean;",
                      lambda a: JObject("Ljava/lang/Boolean;", {"value": 1 if (a and a[0]) else 0}))
        self.register("Ljava/lang/Boolean;", "booleanValue", "()Z", self._unbox)
        self.register("Ljava/lang/Boolean;", "parseBoolean", "(Ljava/lang/String;)Z",
                      lambda a: 1 if (a and self._txt(a[0]) == "true") else 0)
        self.register("Ljava/lang/Long;", "valueOf", "(J)Ljava/lang/Long;",
                      lambda a: JObject("Ljava/lang/Long;", {"value": a[0] if a else 0}))
        self.register("Ljava/lang/Long;", "longValue", "()J", self._unbox)
        self.register("Ljava/lang/Byte;", "valueOf", "(B)Ljava/lang/Byte;",
                      lambda a: JObject("Ljava/lang/Byte;", {"value": int(a[0]) if a else 0}))
        self.register("Ljava/lang/Byte;", "byteValue", "()B", self._unbox)
        self.register("Ljava/lang/Short;", "valueOf", "(S)Ljava/lang/Short;",
                      lambda a: JObject("Ljava/lang/Short;", {"value": int(a[0]) if a else 0}))
        self.register("Ljava/lang/Short;", "shortValue", "()S", self._unbox)
        self.register("Ljava/lang/Float;", "valueOf", "(F)Ljava/lang/Float;",
                      lambda a: JObject("Ljava/lang/Float;", {"value": _as_py_f32(a[0]) if a else 0.0}))
        self.register("Ljava/lang/Float;", "floatValue", "()F", self._unbox)
        self.register("Ljava/lang/Double;", "valueOf", "(D)Ljava/lang/Double;",
                      lambda a: JObject("Ljava/lang/Double;", {"value": _as_py_f64(a[0]) if a else 0.0}))
        self.register("Ljava/lang/Double;", "doubleValue", "()D", self._unbox)

        def _f32_bits(val, norm_nan=False):
            if isinstance(val, JObject):
                val = val.fields.get("value", 0.0)
            if isinstance(val, float):
                if norm_nan and _math.isnan(val):
                    return 0x7FC00000
                try:
                    raw = _struct.unpack("<I", _struct.pack("<f", val))[0]
                except OverflowError:
                    raw = 0xFF800000 if val < 0 else 0x7F800000
            else:
                raw = int(val or 0) & 0xFFFFFFFF
                if norm_nan and ((raw & 0x7F800000) == 0x7F800000) and (raw & 0x007FFFFF):
                    raw = 0x7FC00000
            return raw - 0x100000000 if raw >= 0x80000000 else raw

        def _f64_bits(val, norm_nan=False):
            if isinstance(val, JObject):
                val = val.fields.get("value", 0.0)
            if isinstance(val, float):
                if norm_nan and _math.isnan(val):
                    return 0x7FF8000000000000
                try:
                    raw = _struct.unpack("<Q", _struct.pack("<d", val))[0]
                except OverflowError:
                    raw = 0xFFF0000000000000 if val < 0 else 0x7FF0000000000000
            else:
                raw = int(val or 0) & 0xFFFFFFFFFFFFFFFF
                if norm_nan and ((raw & 0x7FF0000000000000) == 0x7FF0000000000000) and (raw & 0x000FFFFFFFFFFFFF):
                    raw = 0x7FF8000000000000
            return raw - 0x10000000000000000 if raw >= 0x8000000000000000 else raw

        self.register("Ljava/lang/Float;", "intBitsToFloat", "(I)F",
                      lambda a: _struct.unpack("<f", _struct.pack("<I", int(a[0] or 0) & 0xFFFFFFFF))[0] if a else 0.0)
        self.register("Ljava/lang/Float;", "floatToIntBits", "(F)I",
                      lambda a: _f32_bits(a[0], norm_nan=True) if a else 0)
        self.register("Ljava/lang/Float;", "floatToRawIntBits", "(F)I",
                      lambda a: _f32_bits(a[0], norm_nan=False) if a else 0)
        self.register("Ljava/lang/Float;", "isNaN", "(F)Z",
                      lambda a: 1 if (a and _math.isnan(_as_py_f32(a[0]))) else 0)
        self.register("Ljava/lang/Float;", "isInfinite", "(F)Z",
                      lambda a: 1 if (a and _math.isinf(_as_py_f32(a[0]))) else 0)
        self.register("Ljava/lang/Float;", "parseFloat", "(Ljava/lang/String;)F",
                      lambda a: self._parse_float(a))
        self.register("Ljava/lang/Double;", "longBitsToDouble", "(J)D",
                      lambda a: _struct.unpack("<d", _struct.pack("<Q", int(a[0] or 0) & 0xFFFFFFFFFFFFFFFF))[0] if a else 0.0)
        self.register("Ljava/lang/Double;", "doubleToLongBits", "(D)J",
                      lambda a: _f64_bits(a[0], norm_nan=True) if a else 0)
        self.register("Ljava/lang/Double;", "doubleToRawLongBits", "(D)J",
                      lambda a: _f64_bits(a[0], norm_nan=False) if a else 0)
        self.register("Ljava/lang/Double;", "isNaN", "(D)Z",
                      lambda a: 1 if (a and _math.isnan(_as_py_f64(a[0]))) else 0)
        self.register("Ljava/lang/Double;", "isInfinite", "(D)Z",
                      lambda a: 1 if (a and _math.isinf(_as_py_f64(a[0]))) else 0)
        self.register("Ljava/lang/Double;", "parseDouble", "(Ljava/lang/String;)D",
                      lambda a: self._parse_float(a))
        # --- Charset & Kotlin Coroutines Basis-Stubs ---
        def _make_charset(cs_name="UTF-8"):
            raw = (str(cs_name or "UTF-8").strip() or "UTF-8")
            canon = {
                "utf8": "UTF-8", "utf-8": "UTF-8",
                "iso88591": "ISO-8859-1", "iso-8859-1": "ISO-8859-1", "latin1": "ISO-8859-1",
                "usascii": "US-ASCII", "us-ascii": "US-ASCII", "ascii": "US-ASCII",
                "utf16": "UTF-16", "utf-16": "UTF-16",
                "utf16be": "UTF-16BE", "utf-16be": "UTF-16BE",
                "utf16le": "UTF-16LE", "utf-16le": "UTF-16LE",
                "utf32": "UTF-32", "utf-32": "UTF-32",
                "utf32be": "UTF-32BE", "utf-32be": "UTF-32BE",
                "utf32le": "UTF-32LE", "utf-32le": "UTF-32LE",
            }.get(raw.replace("_", "-").lower(), raw)
            return JObject("Ljava/nio/charset/Charset;", {"name": canon, "text": canon})

        self._make_charset = _make_charset
        self.register("Ljava/nio/charset/Charset;", "forName",
                      "(Ljava/lang/String;)Ljava/nio/charset/Charset;",
                      lambda a: _make_charset(self._txt(a[0]) if a else "UTF-8"))
        self.register("Ljava/nio/charset/Charset;", "defaultCharset",
                      "()Ljava/nio/charset/Charset;", lambda a: _make_charset("UTF-8"))
        self.register("Ljava/nio/charset/Charset;", "name",
                      "()Ljava/lang/String;",
                      lambda a: JString(self._txt(a[0]) or "UTF-8"))
        self.register("Ljava/nio/charset/Charset;", "displayName",
                      "()Ljava/lang/String;",
                      lambda a: JString(self._txt(a[0]) or "UTF-8"))
        self.register("Ljava/nio/charset/Charset;", "isSupported", "(Ljava/lang/String;)Z", lambda a: 1)

        def _get_coro_ctx(a):
            obj = a[0] if a else None
            if isinstance(obj, JObject):
                ctx = obj.fields.get("$ctx") or obj.fields.get("_context")
                if isinstance(ctx, JObject):
                    return ctx
            return JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {})

        def _resume_with(a):
            if a and isinstance(a[0], JObject):
                a[0].fields["$result"] = a[1] if len(a) > 1 else None
            return None

        self.register("Lkotlin/coroutines/Continuation;", "getContext",
                      "()Lkotlin/coroutines/CoroutineContext;", _get_coro_ctx)
        self.register_by_name("getContext", "()Lkotlin/coroutines/CoroutineContext;", _get_coro_ctx)
        self.register("Lkotlin/coroutines/Continuation;", "resumeWith", "(Ljava/lang/Object;)V", _resume_with)
        self.register_by_name("resumeWith", "(Ljava/lang/Object;)V", _resume_with)
        for _ccls in ("Lkotlin/coroutines/CoroutineContext;", "Lkotlin/coroutines/EmptyCoroutineContext;"):
            self.register(_ccls, "plus",
                          "(Lkotlin/coroutines/CoroutineContext;)Lkotlin/coroutines/CoroutineContext;",
                          lambda a: a[1] if len(a) > 1 and isinstance(a[1], JObject) else (a[0] if a else JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {})))
            self.register(_ccls, "minusKey",
                          "(Lkotlin/coroutines/CoroutineContext$Key;)Lkotlin/coroutines/CoroutineContext;",
                          lambda a: a[0] if a and isinstance(a[0], JObject) else JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {}))
            self.register(_ccls, "get",
                          "(Lkotlin/coroutines/CoroutineContext$Key;)Lkotlin/coroutines/CoroutineContext$Element;",
                          lambda a: None)
            self.register(_ccls, "fold",
                          "(Ljava/lang/Object;Lkotlin/jvm/functions/Function2;)Ljava/lang/Object;",
                          lambda a: a[1] if len(a) > 1 else None)
        self.register_by_name("plus", "(Lkotlin/coroutines/CoroutineContext;)Lkotlin/coroutines/CoroutineContext;",
                              lambda a: a[1] if len(a) > 1 and isinstance(a[1], JObject) else (a[0] if a else JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {})))
        self.register_by_name("minusKey", "(Lkotlin/coroutines/CoroutineContext$Key;)Lkotlin/coroutines/CoroutineContext;",
                              lambda a: a[0] if a and isinstance(a[0], JObject) else JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {}))
        self.register_by_name("get", "(Lkotlin/coroutines/CoroutineContext$Key;)Lkotlin/coroutines/CoroutineContext$Element;",
                              lambda a: None)
        self.register("Lkotlin/ResultKt;", "throwOnFailure", "(Ljava/lang/Object;)V", lambda a: None)
        self.register("Ljava/lang/Integer;", "parseInt", "(Ljava/lang/String;)I",
                      lambda a: self._parse_java_int(a[0] if a else None, 10, 32))
        self.register("Ljava/lang/Long;", "parseLong", "(Ljava/lang/String;)J",
                      lambda a: self._parse_java_int(a[0] if a else None, 10, 64))
        self.register("Ljava/lang/Long;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Long;",
                      lambda a: JObject("Ljava/lang/Long;", {"value": self._parse_java_int(a[0] if a else None, 10, 64)}))
        self.register("Ljava/lang/String;", "length", "()I",
                      lambda a: len(self._txt(a[0]) or ""))
        self.register("Ljava/lang/String;", "valueOf", "(I)Ljava/lang/String;",
                      lambda a: JString(str(a[0] if a else 0)))
        # --- String<->Bytes + System.arraycopy (Obfuskator-String-Decrypt, faithful) ---
        self.register("Ljava/lang/String;", "getBytes", "(Ljava/lang/String;)[B", self._str_getbytes)
        self.register("Ljava/lang/String;", "getBytes", "()[B", self._str_getbytes)
        self.register("Ljava/lang/String;", "<init>", "([B)V", self._str_init_b)
        self.register("Ljava/lang/String;", "<init>", "([BI)V", self._str_init_b_hibyte)
        self.register("Ljava/lang/String;", "<init>", "([BII)V", self._str_init_b_off_cnt)
        self.register("Ljava/lang/String;", "<init>", "([BLjava/lang/String;)V", self._str_init_b_charset)
        self.register("Ljava/lang/String;", "<init>", "([BIILjava/lang/String;)V", self._str_init_b_off_cnt_charset)
        self.register("Ljava/lang/String;", "<init>", "([BLjava/nio/charset/Charset;)V", self._str_init_b_charset)
        self.register("Ljava/lang/String;", "<init>", "([BIILjava/nio/charset/Charset;)V", self._str_init_b_off_cnt_charset)
        # --- DexGuard char[]-String-Decrypt: new String(char[]) + .intern() (faithful) ---
        self.register("Ljava/lang/String;", "<init>", "([C)V", self._str_init_c)
        self.register("Ljava/lang/String;", "<init>", "([CII)V", self._str_init_c)
        self.register("Ljava/lang/String;", "<init>", "([III)V", self._str_init_ci)
        self.register("Ljava/lang/String;", "<init>", "(Ljava/lang/String;)V",
                      lambda a: self._str_set(a[0], (self._txt(a[1]) or "") if len(a) > 1 else ""))
        self.register("Ljava/lang/String;", "<init>", "(Ljava/lang/StringBuilder;)V",
                      lambda a: self._str_set(a[0], (self._txt(a[1]) or "") if len(a) > 1 else ""))
        self.register("Ljava/lang/String;", "intern", "()Ljava/lang/String;",
                      lambda a: JString(self._txt(a[0]) or ""))
        self.register("Ljava/lang/String;", "valueOf", "([C)Ljava/lang/String;",
                      lambda a: JString(self._chars_to_text(a[0] if a else None)))
        self.register("Ljava/lang/String;", "valueOf", "([CII)Ljava/lang/String;",
                      lambda a: JString(self._chars_to_text(a[0] if a else None,
                                        int(a[1]) if len(a) > 1 else 0, int(a[2]) if len(a) > 2 else None)))
        self.register("Ljava/lang/String;", "copyValueOf", "([C)Ljava/lang/String;",
                      lambda a: JString(self._chars_to_text(a[0] if a else None)))
        self.register("Ljava/lang/String;", "copyValueOf", "([CII)Ljava/lang/String;",
                      lambda a: JString(self._chars_to_text(a[0] if a else None,
                                        int(a[1]) if len(a) > 1 else 0, int(a[2]) if len(a) > 2 else None)))
        self.register("Ljava/lang/String;", "valueOf", "(Ljava/lang/Object;)Ljava/lang/String;",
                      lambda a: JString(self._obj_to_str(a[0] if a else None)))
        self.register("Ljava/lang/String;", "valueOf", "(Z)Ljava/lang/String;",
                      lambda a: JString("true" if a and a[0] else "false"))
        self.register("Ljava/lang/String;", "valueOf", "(J)Ljava/lang/String;",
                      lambda a: JString(str(int(a[0]) if a and a[0] is not None else 0)))
        self.register("Ljava/lang/String;", "valueOf", "(F)Ljava/lang/String;",
                      lambda a: JString(_fmt_java_float(_as_py_f32(a[0]) if a and a[0] is not None else 0.0)))
        self.register("Ljava/lang/String;", "valueOf", "(D)Ljava/lang/String;",
                      lambda a: JString(_fmt_java_float(_as_py_f64(a[0]) if a and a[0] is not None else 0.0)))
        self.register("Ljava/lang/String;", "valueOf", "(C)Ljava/lang/String;",
                      lambda a: JString(chr(int(a[0]) & 0xFFFF) if a and isinstance(a[0], int) else str(a[0] if a else "")))
        self.register("Ljava/lang/String;", "toCharArray", "()[C",
                      lambda a: JArray("[C", [ord(c) for c in (self._txt(a[0]) or "")]))
        self.register("Ljava/lang/String;", "equalsIgnoreCase", "(Ljava/lang/String;)Z",
                      lambda a: 1 if (self._txt(a[0]) or "").lower() == ((self._txt(a[1]) or "") if len(a) > 1 else "").lower() else 0)
        self.register("Ljava/lang/System;", "arraycopy", "(Ljava/lang/Object;ILjava/lang/Object;II)V", self._sys_arraycopy)
        self.register("Ljava/lang/System;", "loadLibrary", "(Ljava/lang/String;)V", self._fusion_name_load)
        self.register("Ljava/lang/System;", "load", "(Ljava/lang/String;)V", self._fusion_path_load)
        # --- Runtime: getRuntime-Singleton + echter Lade-Pfad (reflektiv via _method_invoke erreichbar) ---
        self.register("Ljava/lang/Runtime;", "getRuntime", "()Ljava/lang/Runtime;", self._runtime_get)
        self.register("Ljava/lang/Runtime;", "nativeLoad",
                      "(Ljava/lang/String;Ljava/lang/ClassLoader;)Ljava/lang/String;", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "nativeLoad",
                      "(Ljava/lang/String;Ljava/lang/ClassLoader;Ljava/lang/Class;)Ljava/lang/String;", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "load", "(Ljava/lang/String;)V", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "load",
                      "(Ljava/lang/String;Ljava/lang/ClassLoader;)V", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "load0", "(Ljava/lang/Class;Ljava/lang/String;)V", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "load0",
                      "(Ljava/lang/ClassLoader;Ljava/lang/String;)V", self._fusion_path_load)
        self.register("Ljava/lang/Runtime;", "loadLibrary", "(Ljava/lang/String;)V", self._fusion_name_load)
        self.register("Ljava/lang/Runtime;", "loadLibrary0", "(Ljava/lang/ClassLoader;Ljava/lang/String;)V", self._fusion_name_load)
        self.register("Ljava/lang/Runtime;", "loadLibrary0", "(Ljava/lang/Class;Ljava/lang/String;)V", self._fusion_name_load)
        # --- ClassLoader & Array & Exception-Konstruktoren ---
        self.register("Ljava/lang/ClassLoader;", "findLibrary", "(Ljava/lang/String;)Ljava/lang/String;", self._cl_find_library)
        self.register("Ldalvik/system/BaseDexClassLoader;", "findLibrary", "(Ljava/lang/String;)Ljava/lang/String;", self._cl_find_library)
        def _cl_get_parent(a):
            if a and isinstance(a[0], JObject):
                p = a[0].fields.get("_parent")
                if isinstance(p, JObject) and p is not a[0]:
                    return p
            return None
        self.register("Ljava/lang/ClassLoader;", "getParent", "()Ljava/lang/ClassLoader;", _cl_get_parent)
        self.register_by_name("getParent", "()Ljava/lang/ClassLoader;", _cl_get_parent)
        self.register("Ljava/lang/ClassLoader;", "getResource", "(Ljava/lang/String;)Ljava/net/URL;", lambda a: None)
        self.register("Ljava/lang/ClassLoader;", "getResourceAsStream", "(Ljava/lang/String;)Ljava/io/InputStream;", lambda a: None)
        self.register("Ljava/lang/reflect/Array;", "newInstance",
                      "(Ljava/lang/Class;I)Ljava/lang/Object;", self._reflect_array_new)
        self.register("Ljava/lang/reflect/Array;", "newInstance",
                      "(Ljava/lang/Class;[I)Ljava/lang/Object;", self._reflect_array_new_multi)
        self.register("Ljava/lang/reflect/Array;", "getLength", "(Ljava/lang/Object;)I",
                      lambda a: len(a[0].data) if a and isinstance(a[0], JArray) else 0)
        self.register("Ljava/lang/reflect/Array;", "get", "(Ljava/lang/Object;I)Ljava/lang/Object;",
                      lambda a: (a[0].data[int(a[1])] if a and isinstance(a[0], JArray) and 0 <= int(a[1]) < len(a[0].data) else None))
        self.register("Ljava/lang/reflect/Array;", "set", "(Ljava/lang/Object;ILjava/lang/Object;)V", self._reflect_array_set)
        for _ex in ("Ljava/lang/Throwable;", "Ljava/lang/Exception;",
                    "Ljava/lang/RuntimeException;", "Ljava/io/IOException;"):
            self.register(_ex, "<init>", "()V", lambda a: None)
            self.register(_ex, "<init>", "(Ljava/lang/String;)V", self._ex_init)
            self.register(_ex, "<init>", "(Ljava/lang/Throwable;)V", self._ex_init)
            self.register(_ex, "<init>", "(Ljava/lang/String;Ljava/lang/Throwable;)V", self._ex_init)
        # --- Map-Interface-Fallback (invoke-interface Map.get/put auf konkreten HashMap-JObjects) ---
        self.register("Ljava/util/Map;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", self._map_get)
        self.register("Ljava/util/Map;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._map_put)
        self.register("Ljava/util/Map$Entry;", "getKey", "()Ljava/lang/Object;",
                      lambda a: a[0].fields.get("key") if a and isinstance(a[0], JObject) else None)
        self.register("Ljava/util/Map$Entry;", "getValue", "()Ljava/lang/Object;",
                      lambda a: a[0].fields.get("value") if a and isinstance(a[0], JObject) else None)
        self.register_by_name("getKey", "()Ljava/lang/Object;",
                              lambda a: a[0].fields.get("key") if a and isinstance(a[0], JObject) else None)
        self.register_by_name("getValue", "()Ljava/lang/Object;",
                              lambda a: a[0].fields.get("value") if a and isinstance(a[0], JObject) else None)
        # --- Kotlin Lazy (Holder; getValue ruft die Function0 lazy) ---
        self.register("Lkotlin/LazyKt;", "lazy", "(Lkotlin/jvm/functions/Function0;)Lkotlin/Lazy;", self._kotlin_lazy)
        self.register("Lkotlin/LazyKt__LazyJVMKt;", "lazy", "(Lkotlin/jvm/functions/Function0;)Lkotlin/Lazy;", self._kotlin_lazy)
        # StringBuilder (minimal)
        self.register("Ljava/lang/StringBuilder;", "<init>", "()V", self._sb_init)
        self.register("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;",
                      self._sb_append_str)
        self.register("Ljava/lang/StringBuilder;", "append", "(I)Ljava/lang/StringBuilder;", self._sb_append)
        self.register("Ljava/lang/StringBuilder;", "toString", "()Ljava/lang/String;", self._sb_tostr)
        # --- String (erweitert) ---
        self.register("Ljava/lang/String;", "equals", "(Ljava/lang/Object;)Z", self._str_equals)
        self.register("Ljava/lang/String;", "charAt", "(I)C",
                      lambda a: (lambda t: ord(t[a[1]]) if 0 <= a[1] < len(t) else 0)(self._txt(a[0]) or ""))
        self.register("Ljava/lang/String;", "substring", "(I)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "")[a[1]:]))
        self.register("Ljava/lang/String;", "substring", "(II)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "")[a[1]:a[2]]))
        _subseq_fn = lambda a: JString((self._txt(a[0]) or "")[max(0, int(a[1] or 0)):max(0, int(a[2] or 0))]) if len(a) >= 3 else JString("")
        self.register("Ljava/lang/String;", "subSequence", "(II)Ljava/lang/CharSequence;", _subseq_fn)
        self.register_by_name("subSequence", "(II)Ljava/lang/CharSequence;", _subseq_fn)
        self.register("Ljava/lang/String;", "indexOf", "(Ljava/lang/String;)I", self._str_indexof)
        self.register("Ljava/lang/String;", "startsWith", "(Ljava/lang/String;)Z", self._str_startswith)
        self.register("Ljava/lang/String;", "startsWith", "(Ljava/lang/String;I)Z",
                      lambda a: (lambda s, p, o: 1 if (0 <= o <= len(s) and s[o:].startswith(p)) else 0)(
                          (self._txt(a[0]) or "") if a else "",
                          (self._txt(a[1]) or "") if len(a) > 1 else "",
                          int(a[2] or 0) if len(a) > 2 else -1,
                      ))
        self.register("Ljava/lang/String;", "contains", "(Ljava/lang/CharSequence;)Z",
                      lambda a: 1 if (len(a) > 1 and a[1] is not None and (self._txt(a[1]) or "") in (self._txt(a[0]) or "")) else 0)
        _str_ce = lambda a: 1 if (len(a) > 1 and a[1] is not None and (self._txt(a[0]) or "") == self._txt(a[1])) else 0
        self.register("Ljava/lang/String;", "contentEquals", "(Ljava/lang/CharSequence;)Z", _str_ce)
        self.register("Ljava/lang/String;", "contentEquals", "(Ljava/lang/StringBuffer;)Z", _str_ce)
        def _str_matches(a):
            s = (self._txt(a[0]) or "") if a else ""
            pat = (self._txt(a[1]) or "") if len(a) > 1 and a[1] is not None else None
            if pat is None:
                return 0
            try:
                return 1 if _re.fullmatch(pat, s) is not None else 0
            except Exception:
                return 1 if s == pat else 0
        self.register("Ljava/lang/String;", "matches", "(Ljava/lang/String;)Z", _str_matches)
        def _str_region_matches(a, has_ic=False):
            if len(a) < (6 if has_ic else 5):
                return 0
            ic = bool(a[1]) if has_ic else False
            oi = 2 if has_ic else 1
            s = self._txt(a[0]) or ""
            toff = int(a[oi] or 0)
            other = self._txt(a[oi + 1])
            ooff = int(a[oi + 2] or 0)
            ln = int(a[oi + 3] or 0)
            if other is None or toff < 0 or ooff < 0 or toff + ln > len(s) or ooff + ln > len(other):
                return 0
            if ln <= 0:
                return 1
            sub1 = s[toff : toff + ln]
            sub2 = other[ooff : ooff + ln]
            return 1 if ((sub1.lower() == sub2.lower()) if ic else (sub1 == sub2)) else 0
        self.register("Ljava/lang/String;", "regionMatches", "(ILjava/lang/String;II)Z",
                      lambda a: _str_region_matches(a, False))
        self.register("Ljava/lang/String;", "regionMatches", "(ZILjava/lang/String;II)Z",
                      lambda a: _str_region_matches(a, True))
        self.register("Ljava/lang/String;", "replace", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "").replace(
                          (self._txt(a[1]) or "") if len(a) > 1 else "",
                          (self._txt(a[2]) or "") if len(a) > 2 else "",
                      )))
        def _str_re_replace(a, count=0):
            s = (self._txt(a[0]) or "") if a else ""
            pat = (self._txt(a[1]) or "") if len(a) > 1 else ""
            repl = (self._txt(a[2]) or "") if len(a) > 2 else ""
            py_repl = _re.sub(r"\$(\d+)", r"\\\1", repl)
            try:
                return JString(_re.sub(pat, py_repl, s, count=count))
            except Exception:
                return JString(s.replace(pat, repl, 1) if count == 1 else s.replace(pat, repl))
        self.register("Ljava/lang/String;", "replaceAll", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                      lambda a: _str_re_replace(a, 0))
        self.register("Ljava/lang/String;", "replaceFirst", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                      lambda a: _str_re_replace(a, 1))
        def _str_cmp(a, ic=False):
            s1 = (self._txt(a[0]) or "") if a else ""
            s2 = (self._txt(a[1]) or "") if len(a) > 1 else ""
            if ic:
                s1, s2 = s1.lower(), s2.lower()
            return -1 if s1 < s2 else (1 if s1 > s2 else 0)
        self.register("Ljava/lang/String;", "compareTo", "(Ljava/lang/String;)I", lambda a: _str_cmp(a, False))
        self.register("Ljava/lang/String;", "compareTo", "(Ljava/lang/Object;)I", lambda a: _str_cmp(a, False))
        self.register("Ljava/lang/String;", "compareToIgnoreCase", "(Ljava/lang/String;)I", lambda a: _str_cmp(a, True))
        self.register("Ljava/lang/String;", "toLowerCase", "()Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "").lower()))
        self.register("Ljava/lang/String;", "toLowerCase", "(Ljava/util/Locale;)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "").lower()))
        self.register("Ljava/lang/String;", "toUpperCase", "()Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "").upper()))
        self.register("Ljava/lang/String;", "toUpperCase", "(Ljava/util/Locale;)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "").upper()))
        self.register("Ljava/lang/String;", "isBlank", "()Z",
                      lambda a: 1 if not (self._txt(a[0]) or "").strip() else 0)
        self.register("Ljava/lang/String;", "concat", "(Ljava/lang/String;)Ljava/lang/String;",
                      lambda a: JString((self._txt(a[0]) or "") + ((self._txt(a[1]) or "") if len(a) > 1 else "")))
        self.register("Ljava/lang/String;", "toString", "()Ljava/lang/String;",
                      lambda a: a[0] if a and isinstance(a[0], JString) else JString(self._txt(a[0]) or ""))
        self.register("Ljava/lang/String;", "hashCode", "()I",
                      lambda a: self._java_strhash(self._txt(a[0]) or ""))
        # --- ArrayList: generische Collection-Stubs (siehe _install_collections, __items-Backing) ---
        # --- HashMap (minimal) ---
        self.register("Ljava/util/HashMap;", "<init>", "()V", lambda a: self._coll_init(a, "map", {}))
        self.register("Ljava/util/HashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._map_put)
        self.register("Ljava/util/HashMap;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", self._map_get)
        self.register("Ljava/util/HashMap;", "size", "()I",
                      lambda a: len(a[0].fields.get("map", {})) if a and isinstance(a[0], JObject) else 0)
        # --- SharedPreferences + Editor (In-Memory-Persistenz pro Lauf) ---
        def _sp_s32(v):
            if isinstance(v, (int, bool)):
                x = int(v) & 0xFFFFFFFF
                return x - 0x100000000 if (x & 0x80000000) else x
            return 0

        def _sp_s64(v):
            if isinstance(v, (int, bool)):
                x = int(v) & 0xFFFFFFFFFFFFFFFF
                return x - (1 << 64) if (x & (1 << 63)) else x
            return 0

        def _sp_store(sp_obj):
            if isinstance(sp_obj, JObject):
                nm = sp_obj.fields.get("__prefs_name") or "default"
                return self._prefs_store.setdefault(nm, {})
            return self._prefs_store.setdefault("default", {})

        def _sp_get(a, fallback=0, norm=None):
            key = self._txt(a[1]) if len(a) > 1 else None
            default_v = a[2] if len(a) > 2 else fallback
            store = _sp_store(a[0] if a else None)
            val = store[key] if (key is not None and key in store) else default_v
            return norm(val) if callable(norm) else val

        def _sp_editor(a):
            sp_obj = a[0] if a else None
            nm = sp_obj.fields.get("__prefs_name", "default") if isinstance(sp_obj, JObject) else "default"
            return JObject("Landroid/content/SharedPreferences$Editor;", {"__prefs_name": nm})

        def _sp_put(a, is_bool=False, norm=None):
            ed = a[0] if a else None
            key = self._txt(a[1]) if len(a) > 1 else None
            raw_v = a[2] if len(a) > 2 else None
            if is_bool:
                val = (1 if raw_v else 0) if len(a) > 2 else 0
            elif callable(norm):
                val = norm(raw_v) if len(a) > 2 else 0
            else:
                val = raw_v
            if key is not None:
                _sp_store(ed)[key] = val
            return ed

        def _sp_get_stringset(a):
            key = self._txt(a[1]) if len(a) > 1 else None
            default_v = a[2] if len(a) > 2 else None
            store = _sp_store(a[0] if a else None)
            if key is not None and key in store:
                raw = store[key]
                if isinstance(raw, JObject) and ("__items" in (raw.fields or {}) or "$items" in (raw.fields or {})):
                    items = list(raw.fields.get("__items") or raw.fields.get("$items") or ())
                    jitems = [x if isinstance(x, JString) else JString(self._txt(x) or "") for x in items]
                    return JObject("Ljava/util/LinkedHashSet;", {"__items": list(jitems), "$items": list(jitems)})
                if isinstance(raw, (list, tuple, set)):
                    jitems = [x if isinstance(x, JString) else JString(self._txt(x) or "") for x in raw]
                    return JObject("Ljava/util/LinkedHashSet;", {"__items": list(jitems), "$items": list(jitems)})
            return default_v

        def _sp_put_stringset(a):
            ed = a[0] if a else None
            key = self._txt(a[1]) if len(a) > 1 else None
            raw_v = a[2] if len(a) > 2 else None
            if key is not None:
                if raw_v is None or raw_v == 0:
                    _sp_store(ed).pop(key, None)
                else:
                    ext = self._extract_coll_items(raw_v)
                    jitems = [x if isinstance(x, JString) else JString(self._txt(x) or "") for x in ext]
                    _sp_store(ed)[key] = JObject("Ljava/util/LinkedHashSet;", {"__items": list(jitems), "$items": list(jitems)})
            return ed

        def _sp_get_all_base(a):
            raw = dict(_sp_store(a[0] if a else None))
            m = {str(k): (JString(v) if isinstance(v, str) else v) for k, v in raw.items()}
            rk = {str(k): JString(str(k)) for k in raw.keys()}
            return JObject("Ljava/util/HashMap;", {"map": m, "__raw_keys": rk})

        def _sp_get_float(a):
            key = self._txt(a[1]) if len(a) > 1 else None
            store = _sp_store(a[0] if a else None)
            if key is not None and key in store:
                val = store[key]
                if isinstance(val, float):
                    return val
                if isinstance(val, int) and not isinstance(val, bool) and (val & 0x7FFFFFFF) >= 0x00800000:
                    return _as_py_f32(val)
                try:
                    return float(val or 0.0)
                except Exception:
                    return 0.0
            return _as_py_f32(a[2]) if len(a) > 2 else 0.0

        for _sp_cls in ("Landroid/content/SharedPreferences;",):
            self.register(_sp_cls, "getString",
                          "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                          lambda a: _sp_get(a, JString("")))
            self.register(_sp_cls, "getInt", "(Ljava/lang/String;I)I", lambda a: _sp_get(a, 0, norm=_sp_s32))
            self.register(_sp_cls, "getLong", "(Ljava/lang/String;J)J", lambda a: _sp_get(a, 0, norm=_sp_s64))
            self.register(_sp_cls, "getFloat", "(Ljava/lang/String;F)F", _sp_get_float)
            self.register(_sp_cls, "getBoolean", "(Ljava/lang/String;Z)Z", lambda a: _sp_get(a, 0))
            self.register(_sp_cls, "getStringSet",
                          "(Ljava/lang/String;Ljava/util/Set;)Ljava/util/Set;", _sp_get_stringset)
            self.register(_sp_cls, "getAll", "()Ljava/util/Map;", _sp_get_all_base)
            self.register(_sp_cls, "contains", "(Ljava/lang/String;)Z",
                          lambda a: 1 if (len(a) > 1 and (self._txt(a[1]) or "") in _sp_store(a[0])) else 0)
            self.register(_sp_cls, "edit", "()Landroid/content/SharedPreferences$Editor;", _sp_editor)

        _ED = "Landroid/content/SharedPreferences$Editor;"
        self.register(_ED, "putString",
                      "(Ljava/lang/String;Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;",
                      lambda a: _sp_put(a))
        self.register(_ED, "putInt",
                      "(Ljava/lang/String;I)Landroid/content/SharedPreferences$Editor;",
                      lambda a: _sp_put(a, norm=_sp_s32))
        self.register(_ED, "putLong",
                      "(Ljava/lang/String;J)Landroid/content/SharedPreferences$Editor;",
                      lambda a: _sp_put(a, norm=_sp_s64))
        self.register(_ED, "putFloat",
                      "(Ljava/lang/String;F)Landroid/content/SharedPreferences$Editor;",
                      lambda a: _sp_put(a, norm=_as_py_f32))
        self.register(_ED, "putBoolean",
                      "(Ljava/lang/String;Z)Landroid/content/SharedPreferences$Editor;",
                      lambda a: _sp_put(a, is_bool=True))
        self.register(_ED, "putStringSet",
                      "(Ljava/lang/String;Ljava/util/Set;)Landroid/content/SharedPreferences$Editor;",
                      _sp_put_stringset)
        self.register(_ED, "remove",
                      "(Ljava/lang/String;)Landroid/content/SharedPreferences$Editor;",
                      lambda a: (_sp_store(a[0]).pop(self._txt(a[1]) or "", None), a[0])[1] if a else None)
        self.register(_ED, "clear",
                      "()Landroid/content/SharedPreferences$Editor;",
                      lambda a: (_sp_store(a[0]).clear(), a[0])[1] if a else None)
        self.register(_ED, "apply", "()V", lambda a: None)
        self.register(_ED, "commit", "()Z", lambda a: 1)
        self.register_by_name("getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
                              lambda a: JObject("Landroid/content/SharedPreferences;",
                                                {"__prefs_name": (self._txt(a[1]) if len(a) > 1 else None) or "default", "map": {}}))
        self.register("Landroid/preference/PreferenceManager;", "getDefaultSharedPreferences",
                      "(Landroid/content/Context;)Landroid/content/SharedPreferences;",
                      lambda a: JObject("Landroid/content/SharedPreferences;", {"__prefs_name": "default", "map": {}}))
        # --- Context (minimal) ---
        self.register("Landroid/content/Context;", "getPackageName", "()Ljava/lang/String;",
                      lambda a: JString(self.profile.package or "com.example.app"))
        # --- java.io Reader / Writer (StringReader/BufferedReader/StringWriter mit echtem EOF -1) ---
        self._install_io_readers_writers()

    def _install_io_readers_writers(self):
        """java.io Reader/Writer-Stubs mit echtem UTF-16 Cursor und EOF (-1 / null)."""
        R = self.register

        def _str_to_u16(s):
            b = (s or "").encode("utf-16-le", errors="surrogatepass")
            return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]

        def _u16_to_str(units):
            b = bytearray()
            for u in units:
                v = int(u) & 0xFFFF
                b.extend((v & 0xFF, (v >> 8) & 0xFF))
            return b.decode("utf-16-le", errors="replace")

        def _stream_bytes(inp):
            for k in ("_mem", "buf", "$data"):
                raw = inp.fields.get(k)
                if raw is not None:
                    if isinstance(raw, (bytes, bytearray)):
                        return bytes(raw)
                    if isinstance(raw, JArray):
                        return bytes(int(x) & 0xFF for x in raw.data)
            p = inp.fields.get("path")
            if p and self.vfs is not None:
                data = self.vfs.files.get(str(p))
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
            return b""

        def _resolve_reader(obj):
            if not isinstance(obj, JObject):
                return None
            cur = obj
            for _ in range(8):
                inner = cur.fields.get("_reader")
                if not isinstance(inner, JObject):
                    break
                cur = inner
            if "_u16" not in cur.fields and isinstance(cur.fields.get("_in"), JObject):
                raw = _stream_bytes(cur.fields["_in"])
                enc = cur.fields.get("_enc") or "utf-8"
                try:
                    txt = raw.decode(enc, errors="replace")
                except LookupError:
                    txt = raw.decode("utf-8", errors="replace")
                cur.fields["_u16"] = _str_to_u16(txt)
                cur.fields.setdefault("_pos", 0)
            cur.fields.setdefault("_u16", [])
            cur.fields.setdefault("_pos", 0)
            return cur

        def _sr_init(a):
            if a and isinstance(a[0], JObject):
                txt = self._txt(a[1]) if len(a) > 1 else ""
                a[0].fields["_u16"] = _str_to_u16(txt)
                a[0].fields["_pos"] = 0
                a[0].fields["_mark"] = 0
            return None

        def _car_init(a):
            if a and isinstance(a[0], JObject):
                arr = a[1] if len(a) > 1 else None
                data = arr.data if isinstance(arr, JArray) else []
                off = max(0, int(a[2])) if len(a) > 3 else 0
                cnt = max(0, int(a[3])) if len(a) > 3 else len(data)
                a[0].fields["_u16"] = [int(x) & 0xFFFF for x in data[off:off + cnt]]
                a[0].fields["_pos"] = 0
                a[0].fields["_mark"] = 0
            return None

        def _isr_init(a):
            if a and isinstance(a[0], JObject):
                a[0].fields["_in"] = a[1] if len(a) > 1 else None
                enc = "utf-8"
                if len(a) > 2 and a[2] is not None:
                    if isinstance(a[2], JObject):
                        enc = str(a[2].fields.get("name") or "utf-8")
                    else:
                        enc = self._txt(a[2]) or "utf-8"
                a[0].fields["_enc"] = enc
                a[0].fields.pop("_u16", None)
                a[0].fields["_pos"] = 0
            return None

        def _wrap_reader_init(a):
            if a and isinstance(a[0], JObject):
                a[0].fields["_reader"] = a[1] if len(a) > 1 else None
            return None

        def _r_read1(a):
            t = _resolve_reader(a[0] if a else None)
            if t is None:
                return -1
            u16 = t.fields["_u16"]
            pos = int(t.fields["_pos"])
            if pos >= len(u16):
                return -1
            t.fields["_pos"] = pos + 1
            return u16[pos] & 0xFFFF

        def _r_read_arr(a):
            t = _resolve_reader(a[0] if a else None)
            arr = a[1] if len(a) > 1 else None
            if t is None or not isinstance(arr, JArray):
                return -1
            u16 = t.fields["_u16"]
            pos = int(t.fields["_pos"])
            if pos >= len(u16):
                return -1
            off = max(0, int(a[2])) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(arr.data)
            avail = min(cnt, len(arr.data) - off)
            if avail <= 0:
                return -1
            n = min(avail, len(u16) - pos)
            for i in range(n):
                arr.data[off + i] = u16[pos + i]
            t.fields["_pos"] = pos + n
            return n

        def _r_skip(a):
            t = _resolve_reader(a[0] if a else None)
            if t is None:
                return 0
            u16 = t.fields["_u16"]
            pos = int(t.fields["_pos"])
            want = max(0, int(a[1])) if len(a) > 1 else 0
            n = min(want, max(0, len(u16) - pos))
            t.fields["_pos"] = pos + n
            return n

        def _r_ready(a):
            t = _resolve_reader(a[0] if a else None)
            return 1 if (t is not None and int(t.fields["_pos"]) < len(t.fields["_u16"])) else 0

        def _r_mark(a):
            t = _resolve_reader(a[0] if a else None)
            if t is not None:
                t.fields["_mark"] = int(t.fields.get("_pos", 0))
            return None

        def _r_reset(a):
            t = _resolve_reader(a[0] if a else None)
            if t is not None:
                t.fields["_pos"] = int(t.fields.get("_mark", 0))
            return None

        def _r_readline(a):
            t = _resolve_reader(a[0] if a else None)
            if t is None:
                return None
            u16 = t.fields["_u16"]
            pos = int(t.fields["_pos"])
            if pos >= len(u16):
                return None
            end = pos
            while end < len(u16) and u16[end] not in (0x0A, 0x0D):
                end += 1
            line = _u16_to_str(u16[pos:end])
            if end < len(u16):
                if u16[end] == 0x0D and end + 1 < len(u16) and u16[end + 1] == 0x0A:
                    end += 2
                else:
                    end += 1
            t.fields["_pos"] = end
            return JString(line)

        R("Ljava/io/Reader;", "<init>", "()V", lambda a: None)
        R("Ljava/io/Reader;", "<init>", "(Ljava/lang/Object;)V", lambda a: None)
        R("Ljava/io/StringReader;", "<init>", "(Ljava/lang/String;)V", _sr_init)
        R("Ljava/io/CharArrayReader;", "<init>", "([C)V", _car_init)
        R("Ljava/io/CharArrayReader;", "<init>", "([CII)V", _car_init)
        for _isr_sig in (
            "(Ljava/io/InputStream;)V",
            "(Ljava/io/InputStream;Ljava/lang/String;)V",
            "(Ljava/io/InputStream;Ljava/nio/charset/Charset;)V",
            "(Ljava/io/InputStream;Ljava/nio/charset/CharsetDecoder;)V",
        ):
            R("Ljava/io/InputStreamReader;", "<init>", _isr_sig, _isr_init)
        R("Ljava/io/BufferedReader;", "<init>", "(Ljava/io/Reader;)V", _wrap_reader_init)
        R("Ljava/io/BufferedReader;", "<init>", "(Ljava/io/Reader;I)V", _wrap_reader_init)
        R("Ljava/io/BufferedReader;", "readLine", "()Ljava/lang/String;", _r_readline)

        for _rcls in (
            "Ljava/io/Reader;",
            "Ljava/io/StringReader;",
            "Ljava/io/CharArrayReader;",
            "Ljava/io/InputStreamReader;",
            "Ljava/io/BufferedReader;",
        ):
            R(_rcls, "read", "()I", _r_read1)
            R(_rcls, "read", "([C)I", _r_read_arr)
            R(_rcls, "read", "([CII)I", _r_read_arr)
            R(_rcls, "skip", "(J)J", _r_skip)
            R(_rcls, "ready", "()Z", _r_ready)
            R(_rcls, "markSupported", "()Z", lambda a: 1)
            R(_rcls, "mark", "(I)V", _r_mark)
            R(_rcls, "reset", "()V", _r_reset)
            R(_rcls, "close", "()V", lambda a: None)

        # --- Writer / StringWriter / BufferedWriter / OutputStreamWriter ---
        def _resolve_writer(obj):
            if not isinstance(obj, JObject):
                return None
            cur = obj
            for _ in range(8):
                inner = cur.fields.get("_writer")
                if not isinstance(inner, JObject):
                    break
                cur = inner
            return cur

        def _w_buf(obj):
            t = _resolve_writer(obj)
            if t is None:
                return []
            return t.fields.setdefault("_wbuf", [])

        def _w_write_ch(a):
            if a:
                _w_buf(a[0]).append(_u16_to_str([int(a[1]) & 0xFFFF]) if len(a) > 1 else "")
            return None

        def _w_write_arr(a):
            arr = a[1] if len(a) > 1 else None
            if a and isinstance(arr, JArray):
                off = max(0, int(a[2])) if len(a) > 3 else 0
                cnt = max(0, int(a[3])) if len(a) > 3 else len(arr.data)
                _w_buf(a[0]).append(_u16_to_str(arr.data[off:off + cnt]))
            return None

        def _w_write_str(a):
            if a:
                s = (self._txt(a[1]) if len(a) > 1 else None) or ""
                if len(a) > 3:
                    off = max(0, int(a[2]))
                    cnt = max(0, int(a[3]))
                    s = s[off:off + cnt]
                _w_buf(a[0]).append(s)
            return None

        def _w_append_cs(a):
            if a:
                s = (self._txt(a[1]) if len(a) > 1 else None)
                if s is None:
                    s = "null"
                if len(a) > 3:
                    s = s[max(0, int(a[2])):max(0, int(a[3]))]
                _w_buf(a[0]).append(s)
                return a[0]
            return None

        def _w_append_ch(a):
            _w_write_ch(a)
            return a[0] if a else None

        R("Ljava/io/Writer;", "<init>", "()V", lambda a: None)
        R("Ljava/io/StringWriter;", "<init>", "()V", lambda a: a[0].fields.__setitem__("_wbuf", []) if a else None)
        R("Ljava/io/StringWriter;", "<init>", "(I)V", lambda a: a[0].fields.__setitem__("_wbuf", []) if a else None)
        R("Ljava/io/StringWriter;", "toString", "()Ljava/lang/String;",
          lambda a: JString("".join(_w_buf(a[0]))) if a else JString(""))
        R("Ljava/io/StringWriter;", "getBuffer", "()Ljava/lang/StringBuffer;",
          lambda a: JObject("Ljava/lang/StringBuffer;", {"buf": "".join(_w_buf(a[0]))}) if a else None)
        R("Ljava/io/BufferedWriter;", "<init>", "(Ljava/io/Writer;)V",
          lambda a: a[0].fields.__setitem__("_writer", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        R("Ljava/io/BufferedWriter;", "<init>", "(Ljava/io/Writer;I)V",
          lambda a: a[0].fields.__setitem__("_writer", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        R("Ljava/io/BufferedWriter;", "newLine", "()V",
          lambda a: _w_buf(a[0]).append("\n") if a else None)
        for _osw_sig in (
            "(Ljava/io/OutputStream;)V",
            "(Ljava/io/OutputStream;Ljava/lang/String;)V",
            "(Ljava/io/OutputStream;Ljava/nio/charset/Charset;)V",
        ):
            R("Ljava/io/OutputStreamWriter;", "<init>", _osw_sig, lambda a: None)

        for _wcls in (
            "Ljava/io/Writer;",
            "Ljava/io/StringWriter;",
            "Ljava/io/BufferedWriter;",
            "Ljava/io/OutputStreamWriter;",
        ):
            R(_wcls, "write", "(I)V", _w_write_ch)
            R(_wcls, "write", "([C)V", _w_write_arr)
            R(_wcls, "write", "([CII)V", _w_write_arr)
            R(_wcls, "write", "(Ljava/lang/String;)V", _w_write_str)
            R(_wcls, "write", "(Ljava/lang/String;II)V", _w_write_str)
            R(_wcls, "append", "(C)Ljava/io/Writer;", _w_append_ch)
            R(_wcls, "append", "(Ljava/lang/CharSequence;)Ljava/io/Writer;", _w_append_cs)
            R(_wcls, "append", "(Ljava/lang/CharSequence;II)Ljava/io/Writer;", _w_append_cs)
            R(_wcls, "flush", "()V", lambda a: None)
            R(_wcls, "close", "()V", lambda a: None)

    # ---- Android-Objekte (Context/Bundle/Resources/Prefs; vererbungs-tolerant) ----
    def make_context(self):
        return JObject("Landroid/content/Context;", {"__pkg__": self.profile.package or "com.example.app"})

    def make_bundle(self):
        m = {}
        return JObject("Landroid/os/Bundle;", {"map": m, "$map": m})

    def _make_file(self, path):
        return JObject("Ljava/io/File;", {"path": path})

    @staticmethod
    def _make_dex_path_list():
        return JObject("Ldalvik/system/DexPathList;", {
            "dexElements": JArray("[Ldalvik/system/DexPathList$Element;", []),
            "nativeLibraryDirectories": JObject("Ljava/util/ArrayList;", {"__items": []}),
            "systemNativeLibraryDirectories": JObject("Ljava/util/ArrayList;", {"__items": []}),
            "nativeLibraryPathElements": JArray("[Ldalvik/system/DexPathList$Element;", []),
        })

    @staticmethod
    def _obj_class_obj(obj):
        if isinstance(obj, JObject):
            c = obj.cls or "Ljava/lang/Object;"
            nm = c[1:-1].replace("/", ".") if (c.startswith("L") and c.endswith(";")) else c
            return JObject("Ljava/lang/Class;", {"name": nm})
        if isinstance(obj, JArray):
            return JObject("Ljava/lang/Class;", {"name": obj.eltype or "[Ljava/lang/Object;"})
        if isinstance(obj, JString):
            return JObject("Ljava/lang/Class;", {"name": "java.lang.String"})
        return JObject("Ljava/lang/Class;", {"name": "java.lang.Object"})

    def _install_android_objects(self):
        bn = self.register_by_name
        # Context-Zugriffe (geerbt auf App/Activity/Application) -> brauchbare Objekte
        bn("getApplicationContext", "()Landroid/content/Context;", lambda a: a[0] if a else self.make_context())
        bn("getBaseContext", "()Landroid/content/Context;", lambda a: a[0] if a else self.make_context())
        bn("getResources", "()Landroid/content/res/Resources;",
           lambda a: JObject("Landroid/content/res/Resources;", {}))
        bn("getAssets", "()Landroid/content/res/AssetManager;",
           lambda a: JObject("Landroid/content/res/AssetManager;", {}))
        bn("getContentResolver", "()Landroid/content/ContentResolver;",
           lambda a: JObject("Landroid/content/ContentResolver;", {}))
        bn("getPackageManager", "()Landroid/content/pm/PackageManager;",
           lambda a: JObject("Landroid/content/pm/PackageManager;", {}))
        bn("getClassLoader", "()Ljava/lang/ClassLoader;",
           lambda a: JObject("Ldalvik/system/PathClassLoader;", {"pathList": self._make_dex_path_list()}))
        self.register("Ljava/lang/Class;", "getClassLoader", "()Ljava/lang/ClassLoader;",
                      lambda a: JObject("Ldalvik/system/PathClassLoader;", {"pathList": self._make_dex_path_list()}))
        bn("getSystemService", "(Ljava/lang/String;)Ljava/lang/Object;",
           lambda a: JObject("Landroid/os/SystemService;",
                             {"name": a[1].text if len(a) > 1 and isinstance(a[1], JString) else ""}))
        bn("getSystemService", "(Ljava/lang/Class;)Ljava/lang/Object;",
           lambda a: JObject("Landroid/os/SystemService;", {}))
        bn("getSharedPreferences", "(Ljava/lang/String;I)Landroid/content/SharedPreferences;",
           lambda a: JObject("Landroid/content/SharedPreferences;",
                             {"__prefs_name": (self._txt(a[1]) if len(a) > 1 else None) or "default", "map": {}}))
        bn("getFilesDir", "()Ljava/io/File;",
           lambda a: self._make_file("/data/data/%s/files" % (self.profile.package or "app")))
        bn("getCacheDir", "()Ljava/io/File;",
           lambda a: self._make_file("/data/data/%s/cache" % (self.profile.package or "app")))
        bn("getString", "(I)Ljava/lang/String;", lambda a: JString(""))
        bn("getPackageName", "()Ljava/lang/String;",
           lambda a: JString(self.profile.package or "com.example.app"))
        # Object-Basis (vererbungs-tolerant)
        bn("toString", "()Ljava/lang/String;",
           lambda a: JString(str(a[0].fields["__ename"]))
           if a and isinstance(a[0], JObject) and "__ename" in a[0].fields
           else (JString(a[0].cls) if a and isinstance(a[0], JObject) else JString("")))
        bn("hashCode", "()I", lambda a: (id(a[0]) & 0x7fffffff) if a else 0)
        bn("equals", "(Ljava/lang/Object;)Z", lambda a: 1 if len(a) > 1 and a[0] is a[1] else 0)
        bn("getClass", "()Ljava/lang/Class;", lambda a: self._obj_class_obj(a[0] if a else None))
        self.register("Ljava/lang/Object;", "getClass", "()Ljava/lang/Class;",
                      lambda a: self._obj_class_obj(a[0] if a else None))
        # Bundle (exakt)
        def _bundle_get_def(a, default=None):
            if a and isinstance(a[0], JObject):
                m = self._bundle_map(a[0])
                k = self._bundle_key(a)
                if k in m:
                    return m[k]
            return default

        self.register("Landroid/os/Bundle;", "<init>", "()V",
                      lambda a: (AndroidStubs._bundle_map(a[0]), None)[1] if a and isinstance(a[0], JObject) else None)
        self.register("Landroid/os/Bundle;", "putString",
                      "(Ljava/lang/String;Ljava/lang/String;)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putInt", "(Ljava/lang/String;I)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putBoolean", "(Ljava/lang/String;Z)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putLong", "(Ljava/lang/String;J)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putFloat", "(Ljava/lang/String;F)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putDouble", "(Ljava/lang/String;D)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putLongArray", "(Ljava/lang/String;[J)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putIntArray", "(Ljava/lang/String;[I)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putByteArray", "(Ljava/lang/String;[B)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putStringArray", "(Ljava/lang/String;[Ljava/lang/String;)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putStringArrayList", "(Ljava/lang/String;Ljava/util/ArrayList;)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putParcelableArrayList", "(Ljava/lang/String;Ljava/util/ArrayList;)V", self._bundle_put)
        def _b_as_jstr(v):
            if v is None:
                return JString("")
            return v if isinstance(v, JString) else JString(str(v))

        def _b_unbox(v):
            if isinstance(v, JObject) and v.cls in (
                "Ljava/lang/Boolean;", "Ljava/lang/Byte;", "Ljava/lang/Short;",
                "Ljava/lang/Character;", "Ljava/lang/Integer;", "Ljava/lang/Long;",
                "Ljava/lang/Float;", "Ljava/lang/Double;",
            ) and "value" in v.fields:
                return v.fields["value"]
            return v

        def _b_s32(v):
            v = _b_unbox(v)
            if isinstance(v, (int, bool)):
                x = int(v) & 0xFFFFFFFF
                return x - (1 << 32) if (x & (1 << 31)) else x
            try:
                x = int(v) & 0xFFFFFFFF
                return x - (1 << 32) if (x & (1 << 31)) else x
            except Exception:
                return 0

        def _b_s64(v):
            v = _b_unbox(v)
            if isinstance(v, (int, bool)):
                x = int(v) & 0xFFFFFFFFFFFFFFFF
                return x - (1 << 64) if (x & (1 << 63)) else x
            try:
                x = int(v) & 0xFFFFFFFFFFFFFFFF
                return x - (1 << 64) if (x & (1 << 63)) else x
            except Exception:
                return 0

        def _b_f(v, default=0.0):
            v = _b_unbox(v)
            try:
                return float(v) if v is not None else float(default)
            except Exception:
                return float(default)

        self.register("Landroid/os/Bundle;", "getString",
                      "(Ljava/lang/String;)Ljava/lang/String;", lambda a: _b_as_jstr(self._bundle_get(a)))
        self.register("Landroid/os/Bundle;", "getString",
                      "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
                      lambda a: (lambda v: (v if isinstance(v, JString) else JString(str(v))) if v is not None else (a[2] if len(a) > 2 else JString("")))(_bundle_get_def(a, None)))
        self.register("Landroid/os/Bundle;", "getInt", "(Ljava/lang/String;)I", lambda a: _b_s32(self._bundle_get(a) or 0))
        self.register("Landroid/os/Bundle;", "getInt", "(Ljava/lang/String;I)I",
                      lambda a: _b_s32(_bundle_get_def(a, a[2] if len(a) > 2 else 0)))
        self.register("Landroid/os/Bundle;", "getLong", "(Ljava/lang/String;)J",
                      lambda a: _b_s64(self._bundle_get(a) or 0))
        self.register("Landroid/os/Bundle;", "getLong", "(Ljava/lang/String;J)J",
                      lambda a: _b_s64(_bundle_get_def(a, a[2] if len(a) > 2 else 0)))
        self.register("Landroid/os/Bundle;", "getFloat", "(Ljava/lang/String;)F",
                      lambda a: _b_f(self._bundle_get(a), 0.0))
        self.register("Landroid/os/Bundle;", "getFloat", "(Ljava/lang/String;F)F",
                      lambda a: _b_f(_bundle_get_def(a, a[2] if len(a) > 2 else 0.0), a[2] if len(a) > 2 else 0.0))
        self.register("Landroid/os/Bundle;", "getDouble", "(Ljava/lang/String;)D",
                      lambda a: _b_f(self._bundle_get(a), 0.0))
        self.register("Landroid/os/Bundle;", "getDouble", "(Ljava/lang/String;D)D",
                      lambda a: _b_f(_bundle_get_def(a, a[2] if len(a) > 2 else 0.0), a[2] if len(a) > 2 else 0.0))
        self.register("Landroid/os/Bundle;", "getLongArray", "(Ljava/lang/String;)[J", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getIntArray", "(Ljava/lang/String;)[I", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getByteArray", "(Ljava/lang/String;)[B", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getStringArray", "(Ljava/lang/String;)[Ljava/lang/String;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getStringArrayList", "(Ljava/lang/String;)Ljava/util/ArrayList;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getParcelableArrayList", "(Ljava/lang/String;)Ljava/util/ArrayList;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getBoolean", "(Ljava/lang/String;)Z",
                      lambda a: 1 if _b_unbox(self._bundle_get(a)) else 0)
        self.register("Landroid/os/Bundle;", "getBoolean", "(Ljava/lang/String;Z)Z",
                      lambda a: (1 if _b_unbox(self._bundle_get(a)) else 0) if (a and isinstance(a[0], JObject) and self._bundle_key(a) in self._bundle_map(a[0])) else (1 if len(a) > 2 and _b_unbox(a[2]) else 0))
        self.register("Landroid/os/Bundle;", "containsKey", "(Ljava/lang/String;)Z", self._bundle_has)
        self.register("Landroid/os/Bundle;", "putParcelable",
                      "(Ljava/lang/String;Landroid/os/Parcelable;)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "putSerializable",
                      "(Ljava/lang/String;Ljava/io/Serializable;)V", self._bundle_put)
        self.register("Landroid/os/Bundle;", "getParcelable",
                      "(Ljava/lang/String;)Landroid/os/Parcelable;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getParcelable",
                      "(Ljava/lang/String;Ljava/lang/Class;)Ljava/lang/Object;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getSerializable",
                      "(Ljava/lang/String;)Ljava/io/Serializable;", self._bundle_get)
        self.register("Landroid/os/Bundle;", "getSerializable",
                      "(Ljava/lang/String;Ljava/lang/Class;)Ljava/io/Serializable;", self._bundle_get)

        # Intent (Extras & Flags vollstaendig)
        def _intent_get_extra(a, fallback=None, kind="raw"):
            if a and isinstance(a[0], JObject):
                ex = self._intent_extras_map(a[0])
                k = self._bundle_key(a)
                if k in ex:
                    v = ex[k]
                    if kind == "bool":
                        return 1 if _b_unbox(v) else 0
                    if kind == "int":
                        u = _b_unbox(v)
                        try:
                            return int(u) if u is not None else (int(_b_unbox(fallback)) if fallback is not None else 0)
                        except Exception:
                            return int(_b_unbox(fallback)) if fallback is not None else 0
                    if kind == "float":
                        u = _b_unbox(v)
                        try:
                            return float(u) if u is not None else float(_b_unbox(fallback) or 0.0)
                        except Exception:
                            return float(_b_unbox(fallback) or 0.0)
                    if kind == "str":
                        if isinstance(v, JString):
                            return v
                        return JString(str(v)) if v not in (None, 0) else None
                    return v if v not in (0,) else None
            if kind == "bool":
                return 1 if _b_unbox(fallback) else 0
            if kind == "int":
                try:
                    return int(_b_unbox(fallback)) if fallback is not None else 0
                except Exception:
                    return 0
            if kind == "float":
                try:
                    return float(_b_unbox(fallback) or 0.0)
                except Exception:
                    return 0.0
            return fallback

        def _intent_add_flags(a, replace=False):
            if a and isinstance(a[0], JObject):
                val = int(a[1] or 0) if len(a) > 1 else 0
                cur = 0 if replace else int(a[0].fields.get("_flags", 0) or 0)
                a[0].fields["_flags"] = cur | val
            return a[0] if a else None

        IC = "Landroid/content/Intent;"
        self.register(IC, "getExtras", "()Landroid/os/Bundle;", self._intent_get_extras_bundle)
        self.register(IC, "getBooleanExtra", "(Ljava/lang/String;Z)Z",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0, "bool"))
        self.register(IC, "getIntExtra", "(Ljava/lang/String;I)I",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0, "int"))
        self.register(IC, "getLongExtra", "(Ljava/lang/String;J)J",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0, "int"))
        self.register(IC, "getShortExtra", "(Ljava/lang/String;S)S",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0, "int"))
        self.register(IC, "getByteExtra", "(Ljava/lang/String;B)B",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0, "int"))
        self.register(IC, "getFloatExtra", "(Ljava/lang/String;F)F",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0.0, "float"))
        self.register(IC, "getDoubleExtra", "(Ljava/lang/String;D)D",
                      lambda a: _intent_get_extra(a, a[2] if len(a) > 2 else 0.0, "float"))
        self.register(IC, "getStringExtra", "(Ljava/lang/String;)Ljava/lang/String;",
                      lambda a: _intent_get_extra(a, None, "str"))
        self.register(IC, "getCharSequenceExtra", "(Ljava/lang/String;)Ljava/lang/CharSequence;",
                      lambda a: _intent_get_extra(a, None, "str"))
        self.register(IC, "getSerializableExtra", "(Ljava/lang/String;)Ljava/io/Serializable;",
                      lambda a: _intent_get_extra(a, None, "raw"))
        self.register(IC, "getSerializableExtra", "(Ljava/lang/String;Ljava/lang/Class;)Ljava/io/Serializable;",
                      lambda a: _intent_get_extra(a, None, "raw"))
        self.register(IC, "getParcelableExtra", "(Ljava/lang/String;)Landroid/os/Parcelable;",
                      lambda a: _intent_get_extra(a, None, "raw"))
        self.register(IC, "getParcelableExtra", "(Ljava/lang/String;Ljava/lang/Class;)Ljava/lang/Object;",
                      lambda a: _intent_get_extra(a, None, "raw"))
        self.register(IC, "getBundleExtra", "(Ljava/lang/String;)Landroid/os/Bundle;",
                      lambda a: _intent_get_extra(a, None, "raw"))
        self.register(IC, "hasExtra", "(Ljava/lang/String;)Z",
                      lambda a: 1 if (a and isinstance(a[0], JObject) and self._bundle_key(a) in self._intent_extras_map(a[0])) else 0)
        self.register(IC, "removeExtra", "(Ljava/lang/String;)V",
                      lambda a: (self._intent_extras_map(a[0]).pop(self._bundle_key(a), None) if a and isinstance(a[0], JObject) else None, None)[1])
        self.register(IC, "addFlags", "(I)Landroid/content/Intent;", lambda a: _intent_add_flags(a, False))
        self.register(IC, "setFlags", "(I)Landroid/content/Intent;", lambda a: _intent_add_flags(a, True))
        self.register(IC, "getFlags", "()I",
                      lambda a: int(a[0].fields.get("_flags", 0) or 0) if a and isinstance(a[0], JObject) else 0)
        # ColorFilter-Stubs (u.a. UIExtensionsUtils.F / BlendModeColorFilter)
        self.register("Landroid/graphics/BlendModeColorFilter;", "<init>", "(ILandroid/graphics/BlendMode;)V",
                      lambda a: a[0].fields.__setitem__("color", int(a[1] or 0)) if a and isinstance(a[0], JObject) and len(a) > 1 else None)
        self.register("Landroid/graphics/PorterDuffColorFilter;", "<init>", "(ILandroid/graphics/PorterDuff$Mode;)V",
                      lambda a: a[0].fields.__setitem__("color", int(a[1] or 0)) if a and isinstance(a[0], JObject) and len(a) > 1 else None)

    @staticmethod
    def _bundle_key(a):
        return a[1].text if len(a) > 1 and isinstance(a[1], JString) else (str(a[1]) if len(a) > 1 else "")

    @staticmethod
    def _bundle_put(a):
        if a and isinstance(a[0], JObject):
            AndroidStubs._bundle_map(a[0])[AndroidStubs._bundle_key(a)] = a[2] if len(a) > 2 else None
        return None

    @staticmethod
    def _bundle_get(a):
        if a and isinstance(a[0], JObject):
            return AndroidStubs._bundle_map(a[0]).get(AndroidStubs._bundle_key(a))
        return None

    @staticmethod
    def _bundle_has(a):
        if a and isinstance(a[0], JObject):
            return 1 if AndroidStubs._bundle_key(a) in AndroidStubs._bundle_map(a[0]) else 0
        return 0

    # ---- Reflection (generisch: Class.forName/getMethod, Method.invoke) -----
    # ---- Collections / Iterator / Atomics (echte Backing-Strukturen, AP1.3) --
    @staticmethod
    def _coll_items(obj):
        if isinstance(obj, JObject):
            it = obj.fields.get("__items")
            if it is None:
                it = obj.fields["__items"] = []
            return it
        return []

    def _eq_el(self, a, b):
        if isinstance(a, JString) and isinstance(b, JString):
            return a.text == b.text
        if isinstance(a, JString) or isinstance(b, JString):
            ta = a.text if isinstance(a, JString) else a
            tb = b.text if isinstance(b, JString) else b
            return ta == tb
        if a is b:
            return True
        if isinstance(a, JObject) and isinstance(b, JObject) and a.cls == b.cls:
            if "value" in a.fields and "value" in b.fields and a.cls.startswith("Ljava/lang/"):
                return a.fields["value"] == b.fields["value"]
            vm = getattr(self, "vm", None)
            if vm is not None and a.cls not in ("Ljava/lang/Object;", "Ljava/lang/String;"):
                guard = getattr(self, "_eq_guard", None)
                if guard is None:
                    guard = set()
                    self._eq_guard = guard
                pair = (id(a), id(b))
                if pair not in guard:
                    guard.add(pair)
                    try:
                        rm = vm._resolve_method(a.cls, a.cls, "equals", "(Ljava/lang/Object;)Z") if hasattr(vm, "_resolve_method") else None
                        if rm is not None and getattr(rm, "cls", None) not in (None, "Ljava/lang/Object;"):
                            res = vm.invoke(rm.cls, "equals", "(Ljava/lang/Object;)Z", (a, b))
                            return bool(res)
                    except Exception:
                        pass
                    finally:
                        guard.discard(pair)
        try:
            return a == b
        except Exception:
            return False

    @staticmethod
    def _extract_coll_items(src):
        if isinstance(src, JArray):
            return list(src.data)
        if isinstance(src, JObject):
            for k in ("__items", "list", "set"):
                v = src.fields.get(k)
                if isinstance(v, list):
                    return list(v)
            for v in src.fields.values():
                if isinstance(v, JArray):
                    return list(v.data)
        return []

    def _jcoll_init(self, a):
        obj = a[0] if a else None
        if isinstance(obj, JObject):
            obj.fields["__items"] = []
            if len(a) > 1 and a[1] is not None:
                obj.fields["__items"] = self._extract_coll_items(a[1])
        return obj

    def _is_set(self, obj):
        return isinstance(obj, JObject) and "Set" in obj.cls

    def _coll_add(self, a):
        obj = a[0] if a else None
        e = a[1] if len(a) > 1 else None
        items = self._coll_items(obj)
        if self._is_set(obj) and any(self._eq_el(x, e) for x in items):
            return 0
        items.append(e)
        return 1

    def _coll_add_idx(self, a):
        obj = a[0] if a else None
        try:
            idx = int(a[1]); e = a[2] if len(a) > 2 else None
            self._coll_items(obj).insert(idx, e)
        except Exception:
            pass
        return None

    def _coll_add_all(self, a):
        obj = a[0] if a else None
        src = a[1] if len(a) > 1 else None
        ext = self._extract_coll_items(src)
        if not ext:
            return 0
        items = self._coll_items(obj)
        changed = 0
        for e in ext:
            if self._is_set(obj) and any(self._eq_el(x, e) for x in items):
                continue
            items.append(e)
            changed = 1
        return changed

    def _coll_add_all_idx(self, a):
        obj = a[0] if a else None
        try:
            idx = max(0, int(a[1]))
            src = a[2] if len(a) > 2 else None
            ext = self._extract_coll_items(src)
            if not ext:
                return 0
            items = self._coll_items(obj)
            idx = min(idx, len(items))
            items[idx:idx] = ext
            return 1
        except Exception:
            return 0

    def _sync_backing_map_remove(self, coll_obj, removed_item):
        """Synchronisiert Mutationen auf Map-Views (values/keySet/entrySet) zurueck auf die backing Map."""
        if not isinstance(coll_obj, JObject):
            return False
        bm = coll_obj.fields.get("$backing_map")
        kind = coll_obj.fields.get("$backing_kind")
        if not isinstance(bm, JObject) or not kind:
            return False
        mp = bm.fields.get("map")
        raw = bm.fields.get("__raw_keys")
        if not isinstance(mp, dict):
            return False
        if kind == "values":
            for k, val in list(mp.items()):
                if self._eq_el(val, removed_item):
                    del mp[k]
                    if isinstance(raw, dict):
                        raw.pop(k, None)
                    return True
            return False
        if kind == "keys":
            mk = self._mkey(removed_item)
            if mk in mp:
                del mp[mk]
                if isinstance(raw, dict):
                    raw.pop(mk, None)
                return True
            return False
        if kind == "entries" and isinstance(removed_item, JObject):
            mk = self._mkey(removed_item.fields.get("key"))
            if mk in mp and self._eq_el(mp[mk], removed_item.fields.get("value")):
                del mp[mk]
                if isinstance(raw, dict):
                    raw.pop(mk, None)
                return True
        return False

    def _coll_remove(self, a):
        obj = a[0] if a else None
        e = a[1] if len(a) > 1 else None
        items = self._coll_items(obj)
        for i, x in enumerate(items):
            if self._eq_el(x, e):
                self._sync_backing_map_remove(obj, x)
                del items[i]
                return 1
        if self._sync_backing_map_remove(obj, e):
            return 1
        return 0

    def _coll_contains(self, a):
        obj = a[0] if a else None
        e = a[1] if len(a) > 1 else None
        return 1 if any(self._eq_el(x, e) for x in self._coll_items(obj)) else 0

    def _coll_size(self, a):
        return len(self._coll_items(a[0] if a else None))

    def _coll_isempty(self, a):
        return 1 if not self._coll_items(a[0] if a else None) else 0

    def _coll_clear(self, a):
        obj = a[0] if a else None
        self._coll_items(obj).clear()
        if isinstance(obj, JObject):
            bm = obj.fields.get("$backing_map")
            if isinstance(bm, JObject):
                if isinstance(bm.fields.get("map"), dict):
                    bm.fields["map"].clear()
                if isinstance(bm.fields.get("__raw_keys"), dict):
                    bm.fields["__raw_keys"].clear()
        return None

    def _coll_iterator(self, a):
        return JObject("Ljava/util/Iterator;", {"__src": a[0] if a else None, "__idx": 0})

    def _coll_toarray(self, a):
        el = (
            (getattr(a[1], "eltype", None) or "[Ljava/lang/Object;")
            if len(a) > 1 and isinstance(a[1], JArray)
            else "[Ljava/lang/Object;"
        )
        return JArray(el, list(self._coll_items(a[0] if a else None)))

    def _coll_get(self, a):
        obj = a[0] if a else None
        items = self._coll_items(obj)
        try:
            i = int(a[1])
            return items[i] if 0 <= i < len(items) else 0
        except Exception:
            return 0

    def _coll_set_idx(self, a):
        obj = a[0] if a else None
        items = self._coll_items(obj)
        try:
            i = int(a[1])
            val = a[2] if len(a) > 2 else None
            if 0 <= i < len(items):
                old = items[i]
                items[i] = val
                return old
        except Exception:
            pass
        return 0

    def _coll_remove_idx(self, a):
        obj = a[0] if a else None
        items = self._coll_items(obj)
        try:
            i = int(a[1])
            if 0 <= i < len(items):
                return items.pop(i)
        except Exception:
            pass
        return 0

    def _coll_indexof(self, a):
        obj = a[0] if a else None
        e = a[1] if len(a) > 1 else None
        for i, x in enumerate(self._coll_items(obj)):
            if self._eq_el(x, e):
                return i
        return -1

    def _coll_lastindexof(self, a):
        obj = a[0] if a else None
        e = a[1] if len(a) > 1 else None
        items = self._coll_items(obj)
        for i in range(len(items) - 1, -1, -1):
            if self._eq_el(items[i], e):
                return i
        return -1

    def _coll_tostr(self, a):
        obj = a[0] if a else None
        if not isinstance(obj, JObject):
            return JString("[]")
        items = self._coll_items(obj)
        parts = []
        for x in items:
            if x is obj:
                parts.append("(this Collection)")
            else:
                parts.append(self._obj_to_str(x))
        return JString("[" + ", ".join(parts) + "]")

    def _iter_state(self, it):
        src = it.fields.get("__src")
        if src is None and "__items" in it.fields:
            items = it.fields.get("__items")
            if not isinstance(items, list):
                items = list(items) if items is not None else []
                it.fields["__items"] = items
        else:
            items = self._coll_items(src)
        pos_key = "__pos" if "__pos" in it.fields and "__idx" not in it.fields else "__idx"
        idx = int(it.fields.get(pos_key, it.fields.get("__pos", 0)) or 0)
        return src, items, pos_key, idx

    def _iter_set_pos(self, it, pos_key, val):
        it.fields[pos_key] = val
        if "__pos" in it.fields and pos_key != "__pos":
            it.fields["__pos"] = val

    def _iter_hasnext(self, a):
        it = a[0] if a else None
        if not isinstance(it, JObject):
            return 0
        _, items, _, idx = self._iter_state(it)
        return 1 if idx < len(items) else 0

    def _iter_next(self, a):
        it = a[0] if a else None
        if not isinstance(it, JObject):
            return 0
        _, items, pos_key, i = self._iter_state(it)
        if i >= len(items):
            return 0
        self._iter_set_pos(it, pos_key, i + 1)
        return items[i]

    def _iter_remove(self, a):
        it = a[0] if a else None
        if not isinstance(it, JObject):
            return None
        src, items, pos_key, idx = self._iter_state(it)
        i = idx - 1
        if 0 <= i < len(items):
            if src is not None:
                self._sync_backing_map_remove(src, items[i])
            del items[i]
            self._iter_set_pos(it, pos_key, i)
        return None

    def _atomic_init(self, a):
        obj = a[0] if a else None
        if isinstance(obj, JObject):
            raw = a[1] if len(a) > 1 else (None if obj.cls == "Ljava/util/concurrent/atomic/AtomicReference;" else 0)
            obj.fields["__v"] = self._norm_dalvik_ref(raw) if obj.cls == "Ljava/util/concurrent/atomic/AtomicReference;" else raw
        return obj

    def _atomic_get(self, a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return None if isinstance(a, (list, tuple)) and not a else 0
        if o.cls == "Ljava/util/concurrent/atomic/AtomicReference;":
            v = o.fields.get("__v")
            return None if self._is_dalvik_null(v) else v
        return o.fields.get("__v", 0)

    def _atomic_set(self, a):
        o = a[0] if a else None
        if isinstance(o, JObject):
            raw = a[1] if len(a) > 1 else (None if o.cls == "Ljava/util/concurrent/atomic/AtomicReference;" else 0)
            o.fields["__v"] = self._norm_dalvik_ref(raw) if o.cls == "Ljava/util/concurrent/atomic/AtomicReference;" else raw
        return None

    def _atomic_getadd(self, a, delta, ret_old=True):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return 0
        old = o.fields.get("__v", 0) or 0
        o.fields["__v"] = old + delta
        return old if ret_old else old + delta

    def _atomic_cas(self, a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return 0
        exp = a[1] if len(a) > 1 else None
        new = a[2] if len(a) > 2 else None
        if o.cls == "Ljava/util/concurrent/atomic/AtomicReference;":
            cur = o.fields.get("__v")
            if (self._is_dalvik_null(cur) and self._is_dalvik_null(exp)) or cur is exp or cur == exp:
                o.fields["__v"] = self._norm_dalvik_ref(new)
                return 1
            return 0
        if o.fields.get("__v", 0) == exp:
            o.fields["__v"] = new
            return 1
        return 0

    def _atomic_getset(self, a):
        o = a[0] if a else None
        if not isinstance(o, JObject):
            return 0
        old = o.fields.get("__v", 0)
        o.fields["__v"] = a[1] if len(a) > 1 else 0
        return old

    def _install_collections(self):
        R = self.register
        RB = self.register_by_name
        for cls in ("Ljava/util/concurrent/CopyOnWriteArraySet;", "Ljava/util/HashSet;",
                    "Ljava/util/LinkedHashSet;", "Ljava/util/TreeSet;",
                    "Ljava/util/concurrent/CopyOnWriteArrayList;", "Ljava/util/ArrayList;",
                    "Ljava/util/LinkedList;", "Ljava/util/Vector;"):
            R(cls, "<init>", "()V", self._jcoll_init)
            R(cls, "<init>", "(I)V", self._jcoll_init)
            R(cls, "<init>", "(Ljava/util/Collection;)V", self._jcoll_init)
        for _ccls in (
            "Ljava/util/AbstractCollection;", "Ljava/util/AbstractList;",
            "Ljava/util/AbstractSequentialList;", "Ljava/util/AbstractSet;",
            "Ljava/util/Collection;", "Ljava/util/List;", "Ljava/util/Set;",
            "Ljava/util/ArrayList;", "Ljava/util/LinkedList;", "Ljava/util/Vector;",
            "Ljava/util/Stack;", "Ljava/util/ArrayDeque;",
            "Ljava/util/concurrent/CopyOnWriteArrayList;", "Ljava/util/concurrent/CopyOnWriteArraySet;",
            "Ljava/util/HashSet;", "Ljava/util/LinkedHashSet;", "Ljava/util/TreeSet;",
            "Ljava/util/Arrays$ArrayList;",
            "Ljava/util/Collections$EmptyList;", "Ljava/util/Collections$SingletonList;",
            "Ljava/util/Collections$UnmodifiableList;", "Ljava/util/Collections$UnmodifiableCollection;",
            "Ljava/util/Collections$EmptySet;", "Ljava/util/Collections$SingletonSet;",
            "Lkotlin/collections/EmptyList;", "Lkotlin/collections/EmptySet;",
            "Lkotlin/collections/ArrayDeque;",
        ):
            R(_ccls, "toString", "()Ljava/lang/String;", self._coll_tostr)
        RB("add", "(Ljava/lang/Object;)Z", self._coll_add)
        RB("add", "(ILjava/lang/Object;)V", self._coll_add_idx)
        RB("addAll", "(Ljava/util/Collection;)Z", self._coll_add_all)
        RB("addAll", "(ILjava/util/Collection;)Z", self._coll_add_all_idx)
        RB("offer", "(Ljava/lang/Object;)Z", self._coll_add)
        RB("remove", "(Ljava/lang/Object;)Z", self._coll_remove)
        RB("remove", "(I)Ljava/lang/Object;", self._coll_remove_idx)
        RB("contains", "(Ljava/lang/Object;)Z", self._coll_contains)
        RB("indexOf", "(Ljava/lang/Object;)I", self._coll_indexof)
        RB("lastIndexOf", "(Ljava/lang/Object;)I", self._coll_lastindexof)
        RB("size", "()I", self._coll_size)
        RB("isEmpty", "()Z", self._coll_isempty)
        RB("clear", "()V", self._coll_clear)
        RB("iterator", "()Ljava/util/Iterator;", self._coll_iterator)
        RB("toArray", "()[Ljava/lang/Object;", self._coll_toarray)
        RB("toArray", "([Ljava/lang/Object;)[Ljava/lang/Object;", self._coll_toarray)
        RB("get", "(I)Ljava/lang/Object;", self._coll_get)
        RB("set", "(ILjava/lang/Object;)Ljava/lang/Object;", self._coll_set_idx)
        R("Ljava/util/Iterator;", "hasNext", "()Z", self._iter_hasnext)
        R("Ljava/util/Iterator;", "next", "()Ljava/lang/Object;", self._iter_next)
        R("Ljava/util/Iterator;", "remove", "()V", self._iter_remove)
        RB("hasNext", "()Z", self._iter_hasnext)
        RB("next", "()Ljava/lang/Object;", self._iter_next)
        for cls, gsig in (("Ljava/util/concurrent/atomic/AtomicInteger;", "I"),
                          ("Ljava/util/concurrent/atomic/AtomicLong;", "J"),
                          ("Ljava/util/concurrent/atomic/AtomicBoolean;", "Z"),
                          ("Ljava/util/concurrent/atomic/AtomicReference;", "Ljava/lang/Object;")):
            R(cls, "<init>", "()V", self._atomic_init)
            R(cls, "<init>", "(%s)V" % gsig, self._atomic_init)
            R(cls, "get", "()%s" % gsig, self._atomic_get)
            R(cls, "set", "(%s)V" % gsig, self._atomic_set)
            R(cls, "compareAndSet", "(%s%s)Z" % (gsig, gsig), self._atomic_cas)
            R(cls, "getAndSet", "(%s)%s" % (gsig, gsig), self._atomic_getset)
            if gsig in ("I", "J"):
                R(cls, "getAndIncrement", "()%s" % gsig, lambda a: self._atomic_getadd(a, 1, True))
                R(cls, "incrementAndGet", "()%s" % gsig, lambda a: self._atomic_getadd(a, 1, False))
                R(cls, "getAndDecrement", "()%s" % gsig, lambda a: self._atomic_getadd(a, -1, True))
                R(cls, "decrementAndGet", "()%s" % gsig, lambda a: self._atomic_getadd(a, -1, False))
                R(cls, "getAndAdd", "(%s)%s" % (gsig, gsig), lambda a: self._atomic_getadd(a, int(a[1]) if len(a) > 1 else 0, True))
                R(cls, "addAndGet", "(%s)%s" % (gsig, gsig), lambda a: self._atomic_getadd(a, int(a[1]) if len(a) > 1 else 0, False))

    def _mk_intent(self, a=None):
        if a and isinstance(a[0], JObject):
            it = a[0].fields.get("_intent")
            if isinstance(it, JObject):
                return it
        if self._intent_singleton is None:
            self._intent_singleton = JObject("Landroid/content/Intent;", {
                "action": "android.intent.action.MAIN",
                "categories": ["android.intent.category.LAUNCHER"], "__items": []})
        return self._intent_singleton

    def _mk_window(self, a=None):
        if a and isinstance(a[0], JObject):
            win = a[0].fields.get("_window")
            if win is None:
                deco = JObject("Landroid/view/View;", {"__role": "DecorView"})
                cb = JObject("Landroid/view/Window$Callback;", {})
                win = JObject("Landroid/view/Window;", {"decor": deco, "callback": cb})
                a[0].fields["_window"] = win
            return win
        if self._window_singleton is None:
            deco = JObject("Landroid/view/View;", {"__role": "DecorView"})
            cb = JObject("Landroid/view/Window$Callback;", {})
            self._window_singleton = JObject("Landroid/view/Window;", {"decor": deco, "callback": cb})
        return self._window_singleton

    def _is_curr_act(self, obj):
        if not isinstance(obj, JObject):
            return False
        cur = getattr(self, "_current_activity", None)
        if obj is cur:
            return True
        if self.vm is not None and hasattr(self.vm, "_is_subclass"):
            try:
                return bool(self.vm._is_subclass(obj.cls, "Landroid/app/Activity;"))
            except Exception:
                pass
        return False

    def _finish_act(self, a=None):
        if a and isinstance(a[0], JObject):
            obj = a[0]
            obj.fields["_finishing"] = 1
            if callable(getattr(self, "_cancel_owner_coroutines", None)):
                try:
                    self._cancel_owner_coroutines(obj)
                except Exception:
                    pass
            cur = getattr(self, "_current_activity", None)
            if cur is None:
                if self._is_curr_act(obj):
                    self._activity_finished = True
                    self._last_result_code = int(obj.fields.get("_result_code", 0) or 0)
                    self._last_result_data = obj.fields.get("_result_data")
            elif obj is cur:
                self._activity_finished = True
                self._last_result_code = int(obj.fields.get("_result_code", 0) or 0)
                self._last_result_data = obj.fields.get("_result_data")
            else:
                stk = getattr(self, "_activity_stack", None)
                if isinstance(stk, list):
                    stk[:] = [e for e in stk if e.get("activity") is not obj]
        return None

    def _asynctask_exec(self, a=None):
        task = a[0] if a and isinstance(a[0], JObject) else None
        if task is None or self.vm is None or not hasattr(self.vm, "invoke_virtual"):
            return task
        params = a[2] if len(a) > 2 else (a[1] if len(a) > 1 else None)

        def _call(name, sig, cargs):
            try:
                return self.vm.invoke_virtual(task, name, sig, cargs)
            except Exception as e:
                from core.java.dalvik import DalvikUnknownOpcode, VmBudgetExceeded
                if isinstance(e, (DebugStop, VmBudgetExceeded, DalvikUnknownOpcode)):
                    raise
                if hasattr(self.vm, "deviations"):
                    self.vm.deviations.record(
                        "asynctask_error",
                        f"{getattr(task, 'cls', '?')}->{name}{sig}",
                        repr(e),
                        step=getattr(self.vm, "steps", 0),
                    )
                return None

        if not task.fields.get("_cancelled"):
            _call("onPreExecute", "()V", (task,))
        res = None
        if not task.fields.get("_cancelled"):
            res = _call("doInBackground", "([Ljava/lang/Object;)Ljava/lang/Object;", (task, params))
        if task.fields.get("_cancelled"):
            if (hasattr(self.vm, "_resolve_method")
                    and self.vm._resolve_method(task.cls, task.cls, "onCancelled", "(Ljava/lang/Object;)V") is None
                    and self.vm._resolve_method(task.cls, task.cls, "onCancelled", "()V") is not None):
                _call("onCancelled", "()V", (task,))
            else:
                _call("onCancelled", "(Ljava/lang/Object;)V", (task, res))
        else:
            _call("onPostExecute", "(Ljava/lang/Object;)V", (task, res))
        task.fields["_finished"] = True
        return task

    def _get_application(self, a=None):
        if self.application is None:
            self.application = JObject("Landroid/app/Application;", {})
        return self.application

    def _queue_runnable(self, runnable, delay_ms=0):
        try:
            d = int(delay_ms or 0)
        except Exception:
            d = 0
        if not isinstance(runnable, JObject):
            return 1
        vm = getattr(self, "vm", None)
        if d < 0 or d > 60_000:
            if vm is not None and hasattr(vm, "deviations"):
                vm.deviations.record(
                    "dropped_runnable",
                    getattr(runnable, "cls", "?"),
                    f"delay_ms={d}",
                    step=getattr(vm, "steps", 0),
                )
            return 0
        if len(self.pending_runnables) >= 256:
            if vm is not None and hasattr(vm, "deviations"):
                vm.deviations.record(
                    "dropped_runnable",
                    getattr(runnable, "cls", "?"),
                    "queue_full",
                    step=getattr(vm, "steps", 0),
                )
            return 0
        self._q_seq += 1
        clock_ms = int(getattr(self, "_clock_ms", 0) or 0)
        runnable.fields["_due_ms"] = clock_ms + max(0, d)
        runnable.fields["_q_seq"] = self._q_seq
        self.pending_runnables.append(runnable)
        self.pending_runnables.sort(key=lambda r: (
            int(r.fields.get("_due_ms", 0)) if isinstance(r, JObject) else 0,
            int(r.fields.get("_q_seq", 0)) if isinstance(r, JObject) else 0,
        ))
        return 1

    def _install_activity_ctx(self):
        """AP1.3: Activity/Context-Accessoren liefern die echte Application + nicht-null Intent/Window."""
        R = self.register
        R("Landroid/app/Application;", "<init>", "()V", lambda a: None)
        R("Landroid/app/Application;", "attachBaseContext", "(Landroid/content/Context;)V",
          lambda a: a[0].fields.__setitem__("_base", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        R("Landroid/view/Window;", "getCallback", "()Landroid/view/Window$Callback;",
          lambda a: (a[0].fields.get("callback") if a and isinstance(a[0], JObject)
                     else self._mk_window().fields.get("callback")))
        R("Landroid/view/Window;", "setCallback", "(Landroid/view/Window$Callback;)V",
          lambda a: a[0].fields.__setitem__("callback", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        RB = self.register_by_name
        RB("getApplication", "()Landroid/app/Application;", self._get_application)
        RB("getApplicationContext", "()Landroid/content/Context;", self._get_application)
        RB("getIntent", "()Landroid/content/Intent;", self._mk_intent)
        RB("getWindow", "()Landroid/view/Window;", self._mk_window)
        RB("getCallback", "()Landroid/view/Window$Callback;",
           lambda a: (a[0].fields.get("callback") if a and isinstance(a[0], JObject)
                      else self._mk_window().fields.get("callback")))
        RB("setCallback", "(Landroid/view/Window$Callback;)V",
           lambda a: a[0].fields.__setitem__("callback", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        RB("isTaskRoot", "()Z",
           lambda a: 0 if bool(getattr(getattr(self, "_active_runner", None), "activity_stack", None)) else 1)
        RB("isFinishing", "()Z", lambda a: int(bool(a[0].fields.get("_finishing"))) if a and isinstance(a[0], JObject) else 0)
        RB("isDestroyed", "()Z", lambda a: int(bool(a[0].fields.get("_destroyed"))) if a and isinstance(a[0], JObject) else 0)
        def _act_on_back(a):
            disp = getattr(self, "_dispatch_back_press", None)
            if callable(disp):
                return disp(a[0] if a else None)
            return self._finish_act(a)

        R("Landroid/app/Activity;", "finish", "()V", self._finish_act)
        R("Landroid/app/Activity;", "finishAfterTransition", "()V", self._finish_act)
        R("Landroid/app/Activity;", "finishAffinity", "()V", self._finish_act)
        R("Landroid/app/Activity;", "finishAndRemoveTask", "()V", self._finish_act)
        R("Landroid/app/Activity;", "onBackPressed", "()V", _act_on_back)
        RB("finish", "()V", self._finish_act)
        RB("finishAfterTransition", "()V", self._finish_act)
        RB("finishAffinity", "()V", self._finish_act)
        RB("onBackPressed", "()V", _act_on_back)
        R("Landroid/os/AsyncTask;", "<init>", "()V", lambda a: None)
        R("Landroid/os/AsyncTask;", "execute", "(Ljava/lang/Runnable;)V",
          lambda a: (self._queue_runnable(a[0] if a else None, 0), None)[1])
        R("Landroid/os/AsyncTask;", "execute", "([Ljava/lang/Object;)Landroid/os/AsyncTask;", self._asynctask_exec)
        R("Landroid/os/AsyncTask;", "executeOnExecutor",
          "(Ljava/util/concurrent/Executor;[Ljava/lang/Object;)Landroid/os/AsyncTask;", self._asynctask_exec)
        def _at_cancel(a):
            task = a[0] if a and isinstance(a[0], JObject) else None
            if task is None or task.fields.get("_finished") or task.fields.get("_cancelled"):
                return 0
            task.fields["_cancelled"] = True
            return 1

        def _at_is_cancelled(a):
            task = a[0] if a and isinstance(a[0], JObject) else None
            return 1 if (task is not None and task.fields.get("_cancelled")) else 0

        R("Landroid/os/AsyncTask;", "cancel", "(Z)Z", _at_cancel)
        R("Landroid/os/AsyncTask;", "isCancelled", "()Z", _at_is_cancelled)
        R("Landroid/os/AsyncTask;", "publishProgress", "([Ljava/lang/Object;)V",
          lambda a: (self.vm.invoke_virtual(a[0], "onProgressUpdate", "([Ljava/lang/Object;)V",
                                            (a[0], a[1] if len(a) > 1 else None))
                     if a and isinstance(a[0], JObject) and self.vm and hasattr(self.vm, "invoke_virtual") else None,
                     None)[1])
        RB("execute", "([Ljava/lang/Object;)Landroid/os/AsyncTask;", self._asynctask_exec)
        RB("executeOnExecutor",
           "(Ljava/util/concurrent/Executor;[Ljava/lang/Object;)Landroid/os/AsyncTask;", self._asynctask_exec)
        RB("hasWindowFocus", "()Z", lambda a: 1)
        RB("runOnUiThread", "(Ljava/lang/Runnable;)V",
           lambda a: (self._queue_runnable(a[1] if len(a) > 1 else None, 0), None)[1])
        RB("getWindowManager", "()Landroid/view/WindowManager;",
           lambda a: JObject("Landroid/view/WindowManager;", {}))
        RB("getDecorView", "()Landroid/view/View;",
           lambda a: ((a[0] if a and isinstance(a[0], JObject) and a[0].cls == "Landroid/view/Window;"
                       else self._mk_window(a)).fields.get("decor")))
        RB("getAction", "()Ljava/lang/String;",
           lambda a: JString(a[0].fields.get("action", "")) if a and isinstance(a[0], JObject) else JString(""))

    def _so_load_library(self, args):
        """SoLoader/NativeLoader.loadLibrary-Override. Im Fusions-Modus (vm._fusion_load_library
        gesetzt) wird die ECHTE lib<name>.so in die native Engine geladen (JNI_OnLoad + RegisterNatives)
        -- der Override verschluckt das Laden also NICHT. Ohne Fusion bleibt es ein reiner
        No-Op-Erfolg (1), damit die SoLoader-Recovery (NoBaseApkException) den App-Start nicht reisst.
        Rueckgabe immer Z=1 (loadLibrary erfolgreich)."""
        hook = getattr(self.vm, "_fusion_load_library", None) if self.vm is not None else None
        if callable(hook):
            # letztes String-Argument = Lib-Name (statische Methode: args[0]); defensiv ueber alle args
            name = None
            for x in (args or []):
                t = self._txt(x)
                if t:
                    name = t
            try:
                hook(name)
            except Exception:
                pass
        return 1

    def _sb_push(self, o, text):
        if isinstance(o, JObject):
            o.fields["buf"] = (o.fields.get("buf", "") or "") + (text or "")
        return o

    def _install_aosp_gaps(self):
        """Spec-treue AOSP-/JDK-Standard-APIs, die obfuskierte Apps im String-Decoder/Kontrollfluss
        nutzen (generisch, KEINE App-Heuristik, KEINE Klartext-Kalibrierung)."""
        R = self.register
        SB = "Ljava/lang/StringBuilder;"
        # --- StringBuilder vollstaendig + typkorrekt (append(C) = CHAR!) ---
        R(SB, "<init>", "(Ljava/lang/String;)V", lambda a: self._sb_init_str(a))
        R(SB, "<init>", "(Ljava/lang/CharSequence;)V", lambda a: self._sb_init_str(a))
        R(SB, "<init>", "(I)V", self._sb_init)
        R(SB, "append", "(C)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], chr(int(a[1]) & 0xFFFF)))
        R(SB, "append", "(Z)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], "true" if (len(a) > 1 and a[1]) else "false"))
        R(SB, "append", "(J)Ljava/lang/StringBuilder;", lambda a: self._sb_push(a[0], str(int(a[1]))))
        R(SB, "append", "([C)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], self._chars_to_text(a[1] if len(a) > 1 else None)))
        R(SB, "append", "(Ljava/lang/Object;)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], self._obj_to_str(a[1] if len(a) > 1 else None)))
        R(SB, "append", "(Ljava/lang/CharSequence;)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], (self._txt(a[1]) or "") if len(a) > 1 else ""))
        R(SB, "append", "(F)Ljava/lang/StringBuilder;", lambda a: self._sb_push(a[0], repr(float(a[1]))))
        R(SB, "append", "(D)Ljava/lang/StringBuilder;", lambda a: self._sb_push(a[0], repr(float(a[1]))))
        R(SB, "length", "()I", lambda a: len(a[0].fields.get("buf", "") or "") if a and isinstance(a[0], JObject) else 0)
        R(SB, "charAt", "(I)C",
          lambda a: (lambda t: ord(t[a[1]]) if 0 <= a[1] < len(t) else 0)(a[0].fields.get("buf", "") or ""))
        R(SB, "toString", "()Ljava/lang/String;", self._sb_tostr)
        R(SB, "setLength", "(I)V", self._sb_setlen)
        R(SB, "deleteCharAt", "(I)Ljava/lang/StringBuilder;", self._sb_delat)
        R(SB, "reverse", "()Ljava/lang/StringBuilder;",
          lambda a: self._sb_set(a[0], (a[0].fields.get("buf", "") or "")[::-1]))
        R(SB, "insert", "(ILjava/lang/String;)Ljava/lang/StringBuilder;", self._sb_insert)
        R(SB, "insert", "(IC)Ljava/lang/StringBuilder;",
          lambda a: self._sb_insert([a[0], a[1], JString(chr(int(a[2]) & 0xFFFF))]) if len(a) > 2 else a[0])

        # --- Character ---
        R("Ljava/lang/Character;", "valueOf", "(C)Ljava/lang/Character;",
          lambda a: JObject("Ljava/lang/Character;", {"value": int(a[0]) & 0xFFFF if a else 0}))
        R("Ljava/lang/Character;", "charValue", "()C",
          lambda a: int(a[0].fields.get("value", 0)) if a and isinstance(a[0], JObject) else 0)
        R("Ljava/lang/Character;", "toString", "(C)Ljava/lang/String;",
          lambda a: JString(chr(int(a[0]) & 0xFFFF) if a else ""))
        R("Ljava/lang/Character;", "toChars", "(I)[C",
          lambda a: JArray("[C", [int(a[0]) & 0x10FFFF] if a else []))
        R("Ljava/lang/Character;", "digit", "(CI)I", self._char_digit)
        R("Ljava/lang/Character;", "isDigit", "(C)Z", lambda a: 1 if a and chr(int(a[0]) & 0xFFFF).isdigit() else 0)
        R("Ljava/lang/Character;", "isLetter", "(C)Z", lambda a: 1 if a and chr(int(a[0]) & 0xFFFF).isalpha() else 0)
        R("Ljava/lang/Character;", "isWhitespace", "(C)Z", lambda a: 1 if a and chr(int(a[0]) & 0xFFFF).isspace() else 0)
        R("Ljava/lang/Character;", "toLowerCase", "(C)C", lambda a: ord(chr(int(a[0]) & 0xFFFF).lower()[:1] or "\x00"))
        R("Ljava/lang/Character;", "toUpperCase", "(C)C", lambda a: ord(chr(int(a[0]) & 0xFFFF).upper()[:1] or "\x00"))

        # --- String-Ergaenzungen ---
        R("Ljava/lang/String;", "codePointAt", "(I)I",
          lambda a: (lambda t: ord(t[a[1]]) if 0 <= a[1] < len(t) else 0)(self._txt(a[0]) or ""))
        R("Ljava/lang/String;", "codePointCount", "(II)I",
          lambda a: max(0, (int(a[2]) if len(a) > 2 else 0) - (int(a[1]) if len(a) > 1 else 0)))
        R("Ljava/lang/String;", "isEmpty", "()Z", lambda a: 1 if not (self._txt(a[0]) or "") else 0)
        R("Ljava/lang/String;", "trim", "()Ljava/lang/String;", lambda a: JString((self._txt(a[0]) or "").strip()))
        R("Ljava/lang/String;", "replace", "(CC)Ljava/lang/String;",
          lambda a: JString((self._txt(a[0]) or "").replace(chr(int(a[1]) & 0xFFFF), chr(int(a[2]) & 0xFFFF))))
        R("Ljava/lang/String;", "lastIndexOf", "(Ljava/lang/String;)I",
          lambda a: (self._txt(a[0]) or "").rfind(self._txt(a[1]) or "") if len(a) > 1 else -1)
        R("Ljava/lang/String;", "lastIndexOf", "(Ljava/lang/String;I)I",
          lambda a: (self._txt(a[0]) or "").rfind(self._txt(a[1]) or "", 0, max(0, int(a[2]) + len(self._txt(a[1]) or "")))
          if len(a) > 2 else -1)
        R("Ljava/lang/String;", "lastIndexOf", "(I)I",
          lambda a: (self._txt(a[0]) or "").rfind(chr(int(a[1]) & 0xFFFF)) if len(a) > 1 else -1)
        R("Ljava/lang/String;", "lastIndexOf", "(II)I",
          lambda a: (self._txt(a[0]) or "").rfind(chr(int(a[1]) & 0xFFFF), 0, max(0, int(a[2]) + 1))
          if len(a) > 2 else -1)
        R("Ljava/lang/String;", "endsWith", "(Ljava/lang/String;)Z",
          lambda a: 1 if (self._txt(a[0]) or "").endswith(self._txt(a[1]) or "") else 0)

        # --- System / Class / Throwable / Reference ---
        _SYS_PROPS = {
            "java.io.tmpdir": "/data/local/tmp",
            "file.separator": "/",
            "path.separator": ":",
            "line.separator": "\n",
            "user.dir": "/",
            "user.home": "/data/local/tmp",
            "os.name": "Linux",
            "http.agent": "Dalvik/2.1.0 (Linux; U; Android 13)",
        }

        def _sys_get_prop(a):
            key = self._txt(a[0]) if a else None
            if key in _SYS_PROPS:
                return JString(_SYS_PROPS[key])
            return a[1] if len(a) > 1 else None

        R("Ljava/lang/System;", "identityHashCode", "(Ljava/lang/Object;)I",
          lambda a: (id(a[0]) & 0x7fffffff) if a and a[0] is not None else 0)
        R("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
          _sys_get_prop)
        R("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;)Ljava/lang/String;",
          _sys_get_prop)
        _JLOG = "Ljava/util/logging/Logger;"
        R(_JLOG, "getLogger", "(Ljava/lang/String;)Ljava/util/logging/Logger;",
          lambda a: JObject(_JLOG, {"name": (self._txt(a[0]) if a else None) or ""}))
        for _lm, _lsig in (
            ("log", "(Ljava/util/logging/Level;Ljava/lang/String;)V"),
            ("log", "(Ljava/util/logging/Level;Ljava/lang/String;Ljava/lang/Throwable;)V"),
            ("log", "(Ljava/util/logging/Level;Ljava/lang/String;[Ljava/lang/Object;)V"),
            ("info", "(Ljava/lang/String;)V"),
            ("warning", "(Ljava/lang/String;)V"),
            ("fine", "(Ljava/lang/String;)V"),
            ("setLevel", "(Ljava/util/logging/Level;)V"),
            ("addHandler", "(Ljava/util/logging/Handler;)V"),
        ):
            R(_JLOG, _lm, _lsig, lambda a: None)
        R(_JLOG, "isLoggable", "(Ljava/util/logging/Level;)Z", lambda a: 0)
        R("Ljava/lang/System;", "nanoTime", "()J", self._sys_nanotime)
        self.register_by_name("getCause", "()Ljava/lang/Throwable;",
                              lambda a: a[0].fields.get("cause") if a and isinstance(a[0], JObject) else None)
        self.register_by_name("getMessage", "()Ljava/lang/String;",
                              lambda a: JString(a[0].fields.get("message", "") or "") if a and isinstance(a[0], JObject) else JString(""))
        self.register_by_name("getLocalizedMessage", "()Ljava/lang/String;",
                              lambda a: JString(a[0].fields.get("message", "") or "") if a and isinstance(a[0], JObject) else JString(""))
        R("Ljava/lang/Class;", "getCanonicalName", "()Ljava/lang/String;", self._cls_getname)
        for _ref_cls in ("Ljava/lang/ref/Reference;", "Ljava/lang/ref/WeakReference;", "Ljava/lang/ref/SoftReference;"):
            R(_ref_cls, "<init>", "(Ljava/lang/Object;)V",
              lambda a: a[0].fields.__setitem__("ref", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
            R(_ref_cls, "get", "()Ljava/lang/Object;",
              lambda a: a[0].fields.get("ref") if a and isinstance(a[0], JObject) else None)
            R(_ref_cls, "clear", "()V",
              lambda a: a[0].fields.__setitem__("ref", None) if a and isinstance(a[0], JObject) else None)
        # Throwable/Exception <init>(String) -> message (vererbungstolerant)
        self.register_by_name("<init>", "(Ljava/lang/String;)V", self._throwable_init_msg)
        self.register_by_name("<init>", "(Ljava/lang/String;Ljava/lang/Throwable;)V", self._throwable_init_msg_cause)

        # --- ReentrantReadWriteLock (no-op; Single-Thread-Emulation) ---
        RW = "Ljava/util/concurrent/locks/ReentrantReadWriteLock;"
        R(RW, "<init>", "()V", lambda a: None)
        R(RW, "readLock", "()Ljava/util/concurrent/locks/ReentrantReadWriteLock$ReadLock;",
          lambda a: JObject("Ljava/util/concurrent/locks/ReentrantReadWriteLock$ReadLock;", {}))
        R(RW, "writeLock", "()Ljava/util/concurrent/locks/ReentrantReadWriteLock$WriteLock;",
          lambda a: JObject("Ljava/util/concurrent/locks/ReentrantReadWriteLock$WriteLock;", {}))
        for LK in ("Ljava/util/concurrent/locks/ReentrantReadWriteLock$ReadLock;",
                   "Ljava/util/concurrent/locks/ReentrantReadWriteLock$WriteLock;",
                   "Ljava/util/concurrent/locks/ReentrantLock;"):
            R(LK, "lock", "()V", lambda a: None)
            R(LK, "unlock", "()V", lambda a: None)
            R(LK, "tryLock", "()Z", lambda a: 1)
        R("Ljava/util/concurrent/locks/ReentrantLock;", "<init>", "()V", lambda a: None)

        # --- Collections-Luecken ---
        R("Ljava/util/Collections;", "singleton", "(Ljava/lang/Object;)Ljava/util/Set;",
          lambda a: JObject("Ljava/util/HashSet;", {"__items": [a[1] if len(a) > 1 else (a[0] if a else None)]}))
        R("Ljava/util/Collections;", "singletonList", "(Ljava/lang/Object;)Ljava/util/List;",
          lambda a: JObject("Ljava/util/ArrayList;", {"__items": [a[1] if len(a) > 1 else (a[0] if a else None)]}))
        R("Ljava/util/Collections;", "emptyList", "()Ljava/util/List;",
          lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
        R("Ljava/util/Collections;", "emptySet", "()Ljava/util/Set;",
          lambda a: JObject("Ljava/util/HashSet;", {"__items": []}))
        R("Ljava/util/Collections;", "emptyMap", "()Ljava/util/Map;",
          lambda a: JObject("Ljava/util/HashMap;", {"map": {}}))
        def _collections_add_all(a):
            dst = a[0] if a else None
            arr = a[1] if len(a) > 1 else None
            ext = self._extract_coll_items(arr)
            if not ext:
                return 0
            items = self._coll_items(dst)
            changed = 0
            for e in ext:
                if self._is_set(dst) and any(self._eq_el(x, e) for x in items):
                    continue
                items.append(e)
                changed = 1
            return changed
        R("Ljava/util/Collections;", "addAll",
          "(Ljava/util/Collection;[Ljava/lang/Object;)Z", _collections_add_all)
        for MAP in ("Ljava/util/HashMap;", "Ljava/util/LinkedHashMap;", "Ljava/util/concurrent/ConcurrentHashMap;",
                    "Ljava/util/TreeMap;", "Ljava/util/EnumMap;", "Ljava/util/Map;", "Ljava/util/SortedMap;"):
            if MAP not in ("Ljava/util/Map;", "Ljava/util/SortedMap;", "Ljava/util/EnumMap;"):
                R(MAP, "<init>", "()V", lambda a: self._coll_init(a, "map", {}))
                R(MAP, "<init>", "(I)V", lambda a: self._coll_init(a, "map", {}))
                R(MAP, "<init>", "(IF)V", lambda a: self._coll_init(a, "map", {}))
                R(MAP, "<init>", "(IFI)V", lambda a: self._coll_init(a, "map", {}))
                R(MAP, "<init>", "(Ljava/util/Map;)V",
                  lambda a, _sort=(MAP == "Ljava/util/TreeMap;"): self._map_copy_init(
                      a[0] if a else None, a[1] if len(a) > 1 else None, sort_keys=_sort))
                if MAP == "Ljava/util/TreeMap;":
                    R(MAP, "<init>", "(Ljava/util/SortedMap;)V",
                      lambda a: self._map_copy_init(a[0] if a else None, a[1] if len(a) > 1 else None, sort_keys=True))
            elif MAP == "Ljava/util/EnumMap;":
                R(MAP, "<init>", "(Ljava/lang/Class;)V", lambda a: self._coll_init(a, "map", {}))
                R(MAP, "<init>", "(Ljava/util/EnumMap;)V",
                  lambda a: self._map_copy_init(a[0] if a else None, a[1] if len(a) > 1 else None))
                R(MAP, "<init>", "(Ljava/util/Map;)V",
                  lambda a: self._map_copy_init(a[0] if a else None, a[1] if len(a) > 1 else None))
            R(MAP, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._map_put)
            R(MAP, "putIfAbsent", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._map_put_if_absent)
            R(MAP, "get", "(Ljava/lang/Object;)Ljava/lang/Object;", self._map_get)
            R(MAP, "containsKey", "(Ljava/lang/Object;)Z",
              lambda a: 1 if (a and isinstance(a[0], JObject) and self._mkey(a[1] if len(a) > 1 else None) in a[0].fields.get("map", {})) else 0)
            R(MAP, "size", "()I", lambda a: len(a[0].fields.get("map", {})) if a and isinstance(a[0], JObject) else 0)
            R(MAP, "isEmpty", "()Z", lambda a: 1 if not (a and isinstance(a[0], JObject) and a[0].fields.get("map", {})) else 0)
            R(MAP, "values", "()Ljava/util/Collection;",
              lambda a: JObject("Ljava/util/ArrayList;", {
                  "__items": list((a[0].fields.get("map", {}) if a and isinstance(a[0], JObject) else {}).values()),
                  "$backing_map": a[0] if a and isinstance(a[0], JObject) else None,
                  "$backing_kind": "values",
              }))
            R(MAP, "keySet", "()Ljava/util/Set;", self._map_keyset)
            R(MAP, "entrySet", "()Ljava/util/Set;", self._map_entryset)
            R(MAP, "remove", "(Ljava/lang/Object;)Ljava/lang/Object;",
              lambda a: a[0].fields.get("map", {}).pop(self._mkey(a[1] if len(a) > 1 else None), None) if a and isinstance(a[0], JObject) else None)
            R(MAP, "putAll", "(Ljava/util/Map;)V", self._map_putall)
            R(MAP, "clear", "()V", lambda a: (a[0].fields.get("map", {}).clear(), a[0].fields.get("__raw_keys", {}).clear()) if a and isinstance(a[0], JObject) else None)
        for _mk in ("Lkotlin/collections/MapsKt;", "Lkotlin/collections/MapsKt__MapsJVMKt;"):
            R(_mk, "toSortedMap", "(Ljava/util/Map;)Ljava/util/SortedMap;",
              lambda a: self._map_copy_init(JObject("Ljava/util/TreeMap;", {"map": {}}), a[0] if a else None, sort_keys=True))

        # --- android.os.Trace (no-op), TextUtils, View-Measure, ExpandableListView, PointF, ImageFormat ---
        R("Landroid/os/Trace;", "beginSection", "(Ljava/lang/String;)V", lambda a: None)
        R("Landroid/os/Trace;", "endSection", "()V", lambda a: None)
        R("Landroid/os/Trace;", "isEnabled", "()Z", lambda a: 0)
        TU = "Landroid/text/TextUtils;"
        R(TU, "indexOf", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)I",
          lambda a: (self._txt(a[0]) or "").find(self._txt(a[1]) or "") if len(a) > 1 else -1)
        R(TU, "indexOf", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;I)I",
          lambda a: (self._txt(a[0]) or "").find(self._txt(a[1]) or "", int(a[2]) if len(a) > 2 else 0))
        R(TU, "getTrimmedLength", "(Ljava/lang/CharSequence;)I",
          lambda a: len((self._txt(a[0]) or "").strip()))
        R(TU, "lastIndexOf", "(Ljava/lang/CharSequence;C)I",
          lambda a: (self._txt(a[0]) or "").rfind(chr(int(a[1]) & 0xFFFF)) if len(a) > 1 else -1)
        R("Landroid/view/View;", "resolveSizeAndState", "(III)I", self._view_resolvesize)
        R("Landroid/view/View;", "combineMeasuredStates", "(II)I",
          lambda a: (int(a[0]) | int(a[1])) & 0xFFFFFFFF if len(a) > 1 else 0)
        R("Landroid/widget/ExpandableListView;", "getPackedPositionType", "(J)I", self._elv_packedtype)
        R("Landroid/widget/ExpandableListView;", "getPackedPositionGroup", "(J)I", self._packed_group)
        R("Landroid/view/ViewConfiguration;", "getGlobalActionKeyTimeout", "()J", lambda a: 500)
        R("Landroid/graphics/PointF;", "length", "(FF)F", self._pointf_length)
        R("Landroid/graphics/ImageFormat;", "getBitsPerPixel", "(I)I", self._imgfmt_bpp)

        # --- Batch 2: weitere AOSP-/JDK-Standard-APIs (spec-treu, generisch) ---
        R("Ljava/lang/String;", "indexOf", "(Ljava/lang/String;I)I",
          lambda a: (self._txt(a[0]) or "").find(self._txt(a[1]) or "", int(a[2]) if len(a) > 2 else 0))
        R("Ljava/lang/String;", "indexOf", "(I)I",
          lambda a: (self._txt(a[0]) or "").find(chr(int(a[1]) & 0xFFFF)) if len(a) > 1 else -1)
        R("Ljava/lang/String;", "indexOf", "(II)I",
          lambda a: (self._txt(a[0]) or "").find(chr(int(a[1]) & 0xFFFF), int(a[2]) if len(a) > 2 else 0))
        R("Ljava/lang/String;", "getBytes", "(Ljava/nio/charset/Charset;)[B", self._str_getbytes)
        R("Ljava/lang/StringBuilder;", "append", "(Ljava/lang/CharSequence;II)Ljava/lang/StringBuilder;",
          lambda a: self._sb_push(a[0], (self._txt(a[1]) or "")[int(a[2]) if len(a) > 2 else 0:int(a[3]) if len(a) > 3 else None]))
        def _enum_cmp(a):
            return (int(a[0].fields.get("__ordinal", 0)) - int(a[1].fields.get("__ordinal", 0))) \
                if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else 0

        R("Ljava/lang/Enum;", "compareTo", "(Ljava/lang/Enum;)I", _enum_cmp)
        R("Ljava/lang/Enum;", "compareTo", "(Ljava/lang/Object;)I", _enum_cmp)
        R("Ljava/lang/Enum;", "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if len(a) > 1 and a[0] is a[1] else 0)
        R("Ljava/lang/System;", "mapLibraryName", "(Ljava/lang/String;)Ljava/lang/String;",
          lambda a: JString("lib" + (self._txt(a[0]) or "") + ".so"))
        def _ntz_l(a):
            x = int(a[0]) & ((1 << 64) - 1) if a else 0
            return 64 if x == 0 else (x & -x).bit_length() - 1
        def _ntz_i(a):
            x = int(a[0]) & 0xffffffff if a else 0
            return 32 if x == 0 else (x & -x).bit_length() - 1
        def _nlz_l(a):
            x = int(a[0]) & ((1 << 64) - 1) if a else 0
            return 64 - x.bit_length()
        def _nlz_i(a):
            x = int(a[0]) & 0xffffffff if a else 0
            return 32 - x.bit_length()
        R("Ljava/lang/Long;", "numberOfTrailingZeros", "(J)I", _ntz_l)
        R("Ljava/lang/Integer;", "numberOfTrailingZeros", "(I)I", _ntz_i)
        R("Ljava/lang/Long;", "numberOfLeadingZeros", "(J)I", _nlz_l)
        R("Ljava/lang/Integer;", "numberOfLeadingZeros", "(I)I", _nlz_i)
        def _hob_i(a):
            x = int(a[0]) & 0xffffffff if a else 0
            return (1 << (x.bit_length() - 1)) if x else 0
        def _hob_l(a):
            x = int(a[0]) & 0xffffffffffffffff if a else 0
            return (1 << (x.bit_length() - 1)) if x else 0
        def _rev_bits(x, bits):
            r = 0
            for _ in range(bits):
                r = (r << 1) | (x & 1)
                x >>= 1
            return r
        R("Ljava/lang/Integer;", "highestOneBit", "(I)I", _hob_i)
        R("Ljava/lang/Integer;", "lowestOneBit", "(I)I",
          lambda a: (lambda x: (x & -x) & 0xffffffff)(int(a[0]) & 0xffffffff if a else 0))
        R("Ljava/lang/Integer;", "bitCount", "(I)I",
          lambda a: bin(int(a[0]) & 0xffffffff).count("1") if a else 0)
        R("Ljava/lang/Integer;", "rotateLeft", "(II)I",
          lambda a: (lambda x, n: ((x << (n & 31)) | (x >> (32 - (n & 31)))) & 0xffffffff)(int(a[0]) & 0xffffffff, int(a[1])) if len(a) > 1 else 0)
        R("Ljava/lang/Integer;", "rotateRight", "(II)I",
          lambda a: (lambda x, n: ((x >> (n & 31)) | (x << (32 - (n & 31)))) & 0xffffffff)(int(a[0]) & 0xffffffff, int(a[1])) if len(a) > 1 else 0)
        R("Ljava/lang/Integer;", "signum", "(I)I",
          lambda a: (lambda s: (1 if s > 0 else (-1 & 0xffffffff if s < 0 else 0)))((int(a[0]) & 0xffffffff) - 0x100000000 if (int(a[0]) & 0x80000000) else (int(a[0]) & 0xffffffff)) if a else 0)
        R("Ljava/lang/Integer;", "reverse", "(I)I",
          lambda a: _rev_bits(int(a[0]) & 0xffffffff, 32) if a else 0)
        R("Ljava/lang/Integer;", "reverseBytes", "(I)I",
          lambda a: int.from_bytes((int(a[0]) & 0xffffffff).to_bytes(4, "big"), "little") if a else 0)
        R("Ljava/lang/Integer;", "compare", "(II)I",
          lambda a: (lambda x, y: (1 if x > y else (-1 & 0xffffffff if x < y else 0)))(
              (int(a[0]) & 0xffffffff) - 0x100000000 if (int(a[0]) & 0x80000000) else (int(a[0]) & 0xffffffff),
              (int(a[1]) & 0xffffffff) - 0x100000000 if (int(a[1]) & 0x80000000) else (int(a[1]) & 0xffffffff)) if len(a) > 1 else 0)
        R("Ljava/lang/Integer;", "toUnsignedLong", "(I)J",
          lambda a: int(a[0]) & 0xffffffff if a else 0)
        R("Ljava/lang/Long;", "highestOneBit", "(J)J", _hob_l)
        R("Ljava/lang/Long;", "lowestOneBit", "(J)J",
          lambda a: (lambda x: (x & -x) & 0xffffffffffffffff)(int(a[0]) & 0xffffffffffffffff if a else 0))
        R("Ljava/lang/Long;", "bitCount", "(J)I",
          lambda a: bin(int(a[0]) & 0xffffffffffffffff).count("1") if a else 0)
        R("Ljava/lang/Long;", "rotateLeft", "(JI)J",
          lambda a: (lambda x, n: ((x << (n & 63)) | (x >> (64 - (n & 63)))) & 0xffffffffffffffff)(int(a[0]) & 0xffffffffffffffff, int(a[1])) if len(a) > 1 else 0)
        R("Ljava/lang/Long;", "rotateRight", "(JI)J",
          lambda a: (lambda x, n: ((x >> (n & 63)) | (x << (64 - (n & 63)))) & 0xffffffffffffffff)(int(a[0]) & 0xffffffffffffffff, int(a[1])) if len(a) > 1 else 0)
        R("Ljava/lang/Long;", "signum", "(J)I",
          lambda a: (lambda s: (1 if s > 0 else (-1 & 0xffffffff if s < 0 else 0)))((int(a[0]) & 0xffffffffffffffff) - 0x10000000000000000 if (int(a[0]) & 0x8000000000000000) else (int(a[0]) & 0xffffffffffffffff)) if a else 0)
        R("Ljava/lang/Long;", "reverse", "(J)J",
          lambda a: _rev_bits(int(a[0]) & 0xffffffffffffffff, 64) if a else 0)
        R("Ljava/lang/Long;", "reverseBytes", "(J)J",
          lambda a: int.from_bytes((int(a[0]) & 0xffffffffffffffff).to_bytes(8, "big"), "little") if a else 0)
        R("Ljava/lang/Long;", "compare", "(JJ)I",
          lambda a: (lambda x, y: (1 if x > y else (-1 & 0xffffffff if x < y else 0)))(
              (int(a[0]) & 0xffffffffffffffff) - 0x10000000000000000 if (int(a[0]) & 0x8000000000000000) else (int(a[0]) & 0xffffffffffffffff),
              (int(a[1]) & 0xffffffffffffffff) - 0x10000000000000000 if (int(a[1]) & 0x8000000000000000) else (int(a[1]) & 0xffffffffffffffff)) if len(a) > 1 else 0)

        # Wrapper toString() (z.B. fuer Retrofit @Path Integer/Long IDs)
        def _num_to_str(a):
            v = a[0] if a else 0
            if isinstance(v, JString):
                return v
            if isinstance(v, JObject):
                v = v.fields.get("value", 0)
            if isinstance(v, JString):
                return v
            try:
                return JString(str(int(v)))
            except Exception:
                return JString(str(v) if v is not None else "0")

        def _flt_to_str(a, is_f32=False):
            v = a[0] if a else 0.0
            if isinstance(v, JString):
                return v
            if isinstance(v, JObject):
                is_f32 = (v.cls == "Ljava/lang/Float;")
                v = v.fields.get("value", 0.0)
            if isinstance(v, JString):
                return v
            try:
                fv = _as_py_f32(v) if is_f32 else _as_py_f64(v)
                return JString(_fmt_java_float(fv))
            except Exception:
                return JString(str(v) if v is not None else "0.0")

        def _bool_to_str(a):
            v = a[0] if a else 0
            if isinstance(v, JObject):
                v = v.fields.get("value", 0)
            return JString("true" if v else "false")

        for _num_cls, _psig in (
            ("Ljava/lang/Integer;", "I"),
            ("Ljava/lang/Long;", "J"),
            ("Ljava/lang/Short;", "S"),
            ("Ljava/lang/Byte;", "B"),
        ):
            R(_num_cls, "toString", "()Ljava/lang/String;", _num_to_str)
            R(_num_cls, "toString", f"({_psig})Ljava/lang/String;", _num_to_str)
        R("Ljava/lang/Float;", "toString", "()Ljava/lang/String;", lambda a: _flt_to_str(a, is_f32=True))
        R("Ljava/lang/Float;", "toString", "(F)Ljava/lang/String;", lambda a: _flt_to_str(a, is_f32=True))
        R("Ljava/lang/Double;", "toString", "()Ljava/lang/String;", lambda a: _flt_to_str(a, is_f32=False))
        R("Ljava/lang/Double;", "toString", "(D)Ljava/lang/String;", lambda a: _flt_to_str(a, is_f32=False))
        R("Ljava/lang/Boolean;", "toString", "()Ljava/lang/String;", _bool_to_str)
        R("Ljava/lang/Boolean;", "toString", "(Z)Ljava/lang/String;", _bool_to_str)

        # ---- StringBuilder/StringBuffer + Appendable, Radix Parse, Runtime, TextUtils ----
        def _sb_coerce(val, sig_hint=""):
            if sig_hint == "Z":
                return "true" if val else "false"
            if sig_hint == "C":
                return chr(int(val) & 0xFFFF) if isinstance(val, int) else (str(val or "")[:1])
            if sig_hint in ("I", "J", "F", "D"):
                return str(val if val is not None else 0)
            if isinstance(val, JArray):
                return "".join(chr(int(c) & 0xFFFF) if isinstance(c, int) else str(c) for c in val.data)
            if self._is_dalvik_null(val) or isinstance(val, (JObject, JString, str)):
                return self._obj_to_str(val)
            t = self._txt(val)
            if t is not None:
                return t
            return str(val)

        for _sbcls in ("Ljava/lang/StringBuilder;", "Ljava/lang/StringBuffer;"):
            R(_sbcls, "<init>", "()V", self._sb_init)
            R(_sbcls, "<init>", "(I)V", self._sb_init)
            R(_sbcls, "<init>", "(Ljava/lang/String;)V", self._sb_init_str)
            R(_sbcls, "<init>", "(Ljava/lang/CharSequence;)V", self._sb_init_str)
            R(_sbcls, "toString", "()Ljava/lang/String;", self._sb_tostr)
            R(_sbcls, "length", "()I", lambda a: len(a[0].fields.get("buf", "") or "") if a and isinstance(a[0], JObject) else 0)
            R(_sbcls, "setLength", "(I)V", self._sb_setlen)
            R(_sbcls, "deleteCharAt", f"(I){_sbcls}", self._sb_delat)
            R(_sbcls, "insert", f"(ILjava/lang/String;){_sbcls}", self._sb_insert)
            R(_sbcls, "reverse", f"(){_sbcls}", lambda a: self._sb_set(a[0], (a[0].fields.get("buf", "") or "")[::-1]) if a and isinstance(a[0], JObject) else None)
            for _ret in (_sbcls, "Ljava/lang/Appendable;"):
                R(_sbcls, "append", f"(Ljava/lang/CharSequence;){_ret}",
                  lambda a: self._sb_push(a[0], "null" if (len(a) < 2 or self._is_dalvik_null(a[1])) else (self._txt(a[1]) if self._txt(a[1]) is not None else self._obj_to_str(a[1]))))
                R(_sbcls, "append", f"(Ljava/lang/CharSequence;II){_ret}",
                  lambda a: self._sb_push(a[0], (self._txt(a[1]) or "null")[int(a[2]):int(a[3])] if len(a) > 3 else ""))
                R(_sbcls, "append", f"(C){_ret}",
                  lambda a: self._sb_push(a[0], chr(int(a[1]) & 0xFFFF) if len(a) > 1 else ""))
            if _sbcls == "Ljava/lang/StringBuffer;":
                for _ps in ("Ljava/lang/String;", "Ljava/lang/Object;", "I", "J", "F", "D", "Z", "[C"):
                    R(_sbcls, "append", f"({_ps}){_sbcls}",
                      lambda a, _h=_ps: self._sb_push(a[0], _sb_coerce(a[1] if len(a) > 1 else None, _h)))

        R("Ljava/lang/Integer;", "parseInt", "(Ljava/lang/String;I)I",
          lambda a: self._parse_java_int(a[0] if a else None, int(a[1]) if len(a) > 1 else 10, 32))
        R("Ljava/lang/Integer;", "valueOf", "(Ljava/lang/String;I)Ljava/lang/Integer;",
          lambda a: JObject("Ljava/lang/Integer;", {"value": self._parse_java_int(a[0] if a else None, int(a[1]) if len(a) > 1 else 10, 32)}))
        R("Ljava/lang/Long;", "parseLong", "(Ljava/lang/String;I)J",
          lambda a: self._parse_java_int(a[0] if a else None, int(a[1]) if len(a) > 1 else 10, 64))
        R("Ljava/lang/Long;", "valueOf", "(Ljava/lang/String;I)Ljava/lang/Long;",
          lambda a: JObject("Ljava/lang/Long;", {"value": self._parse_java_int(a[0] if a else None, int(a[1]) if len(a) > 1 else 10, 64)}))

        R("Ljava/lang/Runtime;", "availableProcessors", "()I", lambda a: 4)
        R("Ljava/lang/Runtime;", "maxMemory", "()J", lambda a: 268435456)
        R("Ljava/lang/Runtime;", "totalMemory", "()J", lambda a: 67108864)
        R("Ljava/lang/Runtime;", "freeMemory", "()J", lambda a: 33554432)

        R("Landroid/text/TextUtils;", "equals", "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Z",
          lambda a: 1 if ((None if not a or a[0] is None else self._txt(a[0])) == (None if len(a) < 2 or a[1] is None else self._txt(a[1]))) else 0)

        _WRAP_CLS = {
            "Ljava/lang/Boolean;", "Ljava/lang/Byte;", "Ljava/lang/Short;",
            "Ljava/lang/Character;", "Ljava/lang/Integer;", "Ljava/lang/Long;",
            "Ljava/lang/Float;", "Ljava/lang/Double;",
        }

        def _str_format(a):
            fmt = (self._txt(a[-2]) if len(a) >= 2 else (self._txt(a[0]) if a else "")) or ""
            arr = a[-1] if a else None
            if not isinstance(arr, JArray):
                return JString(fmt)
            vals = []
            for el in arr.data:
                if isinstance(el, JString):
                    vals.append(el.text)
                elif isinstance(el, JObject) and el.cls in _WRAP_CLS and "value" in (getattr(el, "fields", None) or {}):
                    v = el.fields["value"]
                    if el.cls == "Ljava/lang/Boolean;":
                        vals.append(bool(v))
                    elif el.cls == "Ljava/lang/Character;":
                        vals.append(chr(int(v) & 0xFFFF) if isinstance(v, int) else str(v))
                    else:
                        vals.append(v)
                elif isinstance(el, JObject):
                    vals.append(self._obj_to_str(el))
                elif el is None:
                    vals.append("null")
                else:
                    vals.append(el)
            try:
                return JString(fmt % tuple(vals))
            except Exception:
                return JString(fmt)

        R("Ljava/lang/String;", "format", "(Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/String;", _str_format)
        R("Ljava/lang/String;", "format", "(Ljava/util/Locale;Ljava/lang/String;[Ljava/lang/Object;)Ljava/lang/String;", _str_format)

        # Wrapper <init>(primitive)V + hashCode() (z.B. fuer Kotlin Boxing.boxInt etc.)
        def _mk_box_init(psig):
            def _fn(a):
                if a and isinstance(a[0], JObject):
                    raw = a[1] if len(a) > 1 else 0
                    if psig == "Z":
                        val = 1 if raw else 0
                    elif psig in ("F", "D"):
                        val = raw if raw is not None else 0.0
                    else:
                        try:
                            val = int(raw) if raw is not None else 0
                        except Exception:
                            val = 0
                    a[0].fields["value"] = val
                    return a[0]
                return None
            return _fn

        def _mk_box_hash(psig, static_call=False):
            def _fn(a):
                if static_call:
                    v = a[0] if a else 0
                else:
                    v = a[0].fields.get("value", 0) if (a and isinstance(a[0], JObject)) else (a[0] if a else 0)
                if psig == "Z":
                    return 1231 if v else 1237
                if psig == "J":
                    iv = int(v or 0) & 0xFFFFFFFFFFFFFFFF
                    iv = (iv ^ (iv >> 32)) & 0xFFFFFFFF
                    return iv - (1 << 32) if iv >= (1 << 31) else iv
                if psig in ("F", "D"):
                    iv = hash(v or 0.0) & 0xFFFFFFFF
                    return iv - (1 << 32) if iv >= (1 << 31) else iv
                iv = int(v or 0) & 0xFFFFFFFF
                return iv - (1 << 32) if iv >= (1 << 31) else iv
            return _fn

        for _wcls, _wpsig in (
            ("Ljava/lang/Integer;", "I"),
            ("Ljava/lang/Long;", "J"),
            ("Ljava/lang/Byte;", "B"),
            ("Ljava/lang/Short;", "S"),
            ("Ljava/lang/Character;", "C"),
            ("Ljava/lang/Boolean;", "Z"),
            ("Ljava/lang/Float;", "F"),
            ("Ljava/lang/Double;", "D"),
        ):
            R(_wcls, "<init>", f"({_wpsig})V", _mk_box_init(_wpsig))
            R(_wcls, "hashCode", "()I", _mk_box_hash(_wpsig, static_call=False))
            R(_wcls, "hashCode", f"({_wpsig})I", _mk_box_hash(_wpsig, static_call=True))

        # java.time (LocalDate, Instant, ZoneId, ZonedDateTime, DateTimeFormatter) + SimpleDateFormat/Date
        import datetime as _dt, re as _re, time as _time
        _DOW_NAMES = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
        _DE_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
        _DE_WEEKDAYS_SHORT = ("Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So.")
        _EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        _EN_WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        _LD_CACHE = {}

        def _dow_obj(iso_day):
            d_i = max(1, min(7, int(iso_day)))
            return JObject("Ljava/time/DayOfWeek;", {
                "__ordinal": d_i - 1,
                "__ename": _DOW_NAMES[d_i - 1],
                "day": d_i,
            })

        def _ld_wrap(d):
            obj = _LD_CACHE.get(d)
            if obj is None:
                obj = JObject("Ljava/time/LocalDate;", {"year": d.year, "month": d.month, "day": d.day, "__date": d})
                _LD_CACHE[d] = obj
            return obj

        def _ld_unwrap(obj):
            if isinstance(obj, JObject):
                if isinstance(obj.fields.get("__date"), _dt.date):
                    return obj.fields["__date"]
                try:
                    return _dt.date(int(obj.fields.get("year", 1970)),
                                    int(obj.fields.get("month", 1)),
                                    int(obj.fields.get("day", 1)))
                except Exception:
                    pass
            return self.current_datetime().date()

        def _format_java_date(dt_or_d, pattern, locale_obj=None):
            from core.java.virtual_clock import format_java_pattern
            return format_java_pattern(dt_or_d, pattern, locale_obj)

        R("Ljava/time/LocalDate;", "now", "()Ljava/time/LocalDate;", lambda a: _ld_wrap(self.current_datetime().date()))
        R("Ljava/time/LocalDate;", "now", "(Ljava/time/ZoneId;)Ljava/time/LocalDate;",
          lambda a: _ld_wrap(self.current_datetime(
              a[0].fields.get("__id") if (a and isinstance(a[0], JObject) and not a[0].fields.get("__sys_default")) else None
          ).date()))
        R("Ljava/time/LocalDate;", "of", "(III)Ljava/time/LocalDate;",
          lambda a: _ld_wrap(_dt.date(int(a[0]), int(a[1]), int(a[2])) if len(a) >= 3 else self.current_datetime().date()))
        R("Ljava/time/LocalDate;", "parse", "(Ljava/lang/CharSequence;)Ljava/time/LocalDate;",
          lambda a: _ld_wrap(_dt.date.fromisoformat(self._txt(a[0]).strip()) if a and self._txt(a[0]) else self.current_datetime().date()))
        def _s64(v):
            iv = int(v or 0) & 0xFFFFFFFFFFFFFFFF
            return iv - 0x10000000000000000 if (iv & 0x8000000000000000) else iv

        R("Ljava/time/LocalDate;", "plusDays", "(J)Ljava/time/LocalDate;",
          lambda a: _ld_wrap(_ld_unwrap(a[0] if a else None) + _dt.timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
        R("Ljava/time/LocalDate;", "minusDays", "(J)Ljava/time/LocalDate;",
          lambda a: _ld_wrap(_ld_unwrap(a[0] if a else None) - _dt.timedelta(days=_s64(a[1]) if len(a) > 1 else 0)))
        def _ld_with(a):
            d = _ld_unwrap(a[0] if a else None)
            adj = a[1] if len(a) > 1 else None
            if isinstance(adj, JObject) and adj.cls == "Ljava/time/DayOfWeek;":
                target = int(adj.fields.get("day", adj.fields.get("__ordinal", 0) + 1))
                # TemporalAdjusters / DayOfWeek als ISO-Wochentag (1..7)
                delta = target - d.isoweekday()
                return _ld_wrap(d + _dt.timedelta(days=delta))
            return _ld_wrap(d)
        R("Ljava/time/LocalDate;", "with", "(Ljava/time/temporal/TemporalAdjuster;)Ljava/time/LocalDate;", _ld_with)
        R("Ljava/time/LocalDate;", "getDayOfWeek", "()Ljava/time/DayOfWeek;",
          lambda a: _dow_obj(_ld_unwrap(a[0] if a else None).isoweekday()))
        R("Ljava/time/DayOfWeek;", "getValue", "()I",
          lambda a: int(a[0].fields.get("day", a[0].fields.get("__ordinal", 0) + 1)) if a and isinstance(a[0], JObject) else 1)
        R("Ljava/time/LocalDate;", "getYear", "()I", lambda a: _ld_unwrap(a[0] if a else None).year)
        R("Ljava/time/LocalDate;", "getMonthValue", "()I", lambda a: _ld_unwrap(a[0] if a else None).month)
        R("Ljava/time/LocalDate;", "getDayOfMonth", "()I", lambda a: _ld_unwrap(a[0] if a else None).day)
        R("Ljava/time/LocalDate;", "toString", "()Ljava/lang/String;", lambda a: JString(_ld_unwrap(a[0] if a else None).isoformat()))
        R("Ljava/time/LocalDate;", "equals", "(Ljava/lang/Object;)Z",
          lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == "Ljava/time/LocalDate;"
                          and _ld_unwrap(a[0]) == _ld_unwrap(a[1])) else 0)
        R("Ljava/time/LocalDate;", "hashCode", "()I",
          lambda a: hash(_ld_unwrap(a[0] if a else None)) & 0x7FFFFFFF)
        def _ld_cmp(a):
            d0 = _ld_unwrap(a[0] if a else None)
            d1 = _ld_unwrap(a[1] if len(a) > 1 else None)
            return -1 if d0 < d1 else (1 if d0 > d1 else 0)
        R("Ljava/time/LocalDate;", "compareTo", "(Ljava/time/chrono/ChronoLocalDate;)I", _ld_cmp)
        R("Ljava/time/LocalDate;", "compareTo", "(Ljava/lang/Object;)I", _ld_cmp)
        R("Ljava/time/LocalDate;", "isBefore", "(Ljava/time/chrono/ChronoLocalDate;)Z",
          lambda a: 1 if (len(a) > 1 and _ld_unwrap(a[0]) < _ld_unwrap(a[1])) else 0)
        R("Ljava/time/LocalDate;", "isAfter", "(Ljava/time/chrono/ChronoLocalDate;)Z",
          lambda a: 1 if (len(a) > 1 and _ld_unwrap(a[0]) > _ld_unwrap(a[1])) else 0)
        R("Ljava/time/LocalDate;", "isEqual", "(Ljava/time/chrono/ChronoLocalDate;)Z",
          lambda a: 1 if (len(a) > 1 and _ld_unwrap(a[0]) == _ld_unwrap(a[1])) else 0)
        R("Ljava/time/LocalDate;", "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
          lambda a: JString(_format_java_date(
              _ld_unwrap(a[0] if a else None),
              a[1].fields.get("__pattern", "yyyy-MM-dd") if len(a) > 1 and isinstance(a[1], JObject) else "yyyy-MM-dd",
              a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None,
          )))
        R("Ljava/time/format/DateTimeFormatter;", "ofPattern", "(Ljava/lang/String;)Ljava/time/format/DateTimeFormatter;",
          lambda a: JObject("Ljava/time/format/DateTimeFormatter;", {"__pattern": self._txt(a[0]) or "yyyy-MM-dd"}))
        R("Ljava/time/format/DateTimeFormatter;", "ofPattern", "(Ljava/lang/String;Ljava/util/Locale;)Ljava/time/format/DateTimeFormatter;",
          lambda a: JObject("Ljava/time/format/DateTimeFormatter;", {
              "__pattern": (self._txt(a[0]) or "yyyy-MM-dd") if a else "yyyy-MM-dd",
              "__locale": a[1] if len(a) > 1 else None,
          }))
        R("Ljava/time/Instant;", "ofEpochSecond", "(J)Ljava/time/Instant;",
          lambda a: JObject("Ljava/time/Instant;", {"__epoch_sec": int(a[0] or 0) if a else 0, "__epoch_ms": (int(a[0] or 0) * 1000) if a else 0}))
        R("Ljava/time/Instant;", "ofEpochMilli", "(J)Ljava/time/Instant;",
          lambda a: JObject("Ljava/time/Instant;", {"__epoch_sec": (int(a[0] or 0) // 1000) if a else 0, "__epoch_ms": int(a[0] or 0) if a else 0}))
        R("Ljava/time/Instant;", "getEpochSecond", "()J",
          lambda a: int(a[0].fields.get("__epoch_sec", 0)) if a and isinstance(a[0], JObject) else 0)
        R("Ljava/time/Instant;", "toEpochMilli", "()J",
          lambda a: int(a[0].fields.get("__epoch_ms", int(a[0].fields.get("__epoch_sec", 0)) * 1000)) if a and isinstance(a[0], JObject) else 0)
        R("Ljava/time/ZoneId;", "systemDefault", "()Ljava/time/ZoneId;",
          lambda a: JObject("Ljava/time/ZoneId;", {"__id": getattr(self, "_clock_tz", None) or "Europe/Berlin", "__sys_default": getattr(self, "_clock_tz", None) is None}))
        R("Ljava/time/ZoneId;", "of", "(Ljava/lang/String;)Ljava/time/ZoneId;",
          lambda a: JObject("Ljava/time/ZoneId;", {"__id": (self._txt(a[0]) or "UTC") if a else "UTC"}))
        def _inst_at_zone(a):
            ms = int(a[0].fields.get("__epoch_ms", int(a[0].fields.get("__epoch_sec", 0)) * 1000)) if a and isinstance(a[0], JObject) else 0
            sec = ms // 1000
            z_obj = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
            zid = z_obj.fields.get("__id") if (z_obj is not None and not z_obj.fields.get("__sys_default")) else getattr(self, "_clock_tz", None)
            try:
                if zid:
                    from core.java.virtual_clock import resolve_tzinfo
                    dt = _dt.datetime.fromtimestamp(ms / 1000.0, tz=resolve_tzinfo(zid))
                else:
                    dt = _dt.datetime.fromtimestamp(ms / 1000.0)
            except Exception:
                dt = _dt.datetime.fromtimestamp(0)
            return JObject("Ljava/time/ZonedDateTime;", {"__dt": dt, "__date": dt.date(), "__epoch_sec": sec, "__epoch_ms": ms})
        R("Ljava/time/Instant;", "atZone", "(Ljava/time/ZoneId;)Ljava/time/ZonedDateTime;", _inst_at_zone)
        R("Ljava/time/ZonedDateTime;", "toLocalDate", "()Ljava/time/LocalDate;",
          lambda a: _ld_wrap(a[0].fields.get("__date") if a and isinstance(a[0], JObject) and isinstance(a[0].fields.get("__date"), _dt.date) else self.current_datetime().date()))
        R("Ljava/time/ZonedDateTime;", "toLocalDateTime", "()Ljava/time/LocalDateTime;",
          lambda a: JObject("Ljava/time/LocalDateTime;", {"__dt": a[0].fields.get("__dt", self.current_datetime()) if a and isinstance(a[0], JObject) else self.current_datetime()}))
        R("Ljava/time/ZonedDateTime;", "toEpochSecond", "()J",
          lambda a: int(a[0].fields.get("__epoch_sec", 0)) if a and isinstance(a[0], JObject) else 0)
        R("Ljava/time/ZonedDateTime;", "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
          lambda a: JString(_format_java_date(
              a[0].fields.get("__dt", self.current_datetime()) if a and isinstance(a[0], JObject) else self.current_datetime(),
              a[1].fields.get("__pattern", "yyyy-MM-dd") if len(a) > 1 and isinstance(a[1], JObject) else "yyyy-MM-dd",
              a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None,
          )))
        R("Ljava/time/LocalDateTime;", "format", "(Ljava/time/format/DateTimeFormatter;)Ljava/lang/String;",
          lambda a: JString(_format_java_date(
              a[0].fields.get("__dt", self.current_datetime()) if a and isinstance(a[0], JObject) else self.current_datetime(),
              a[1].fields.get("__pattern", "yyyy-MM-dd") if len(a) > 1 and isinstance(a[1], JObject) else "yyyy-MM-dd",
              a[1].fields.get("__locale") if len(a) > 1 and isinstance(a[1], JObject) else None,
          )))
        # java.util.Date + java.text.SimpleDateFormat + java.util.Locale
        def _loc_set(loc, lang, country=""):
            if isinstance(loc, JObject):
                loc.fields["lang"] = lang or "en"
                loc.fields["country"] = country or ""
        R("Ljava/util/Locale;", "<init>", "(Ljava/lang/String;)V",
          lambda a: _loc_set(a[0], self._txt(a[1]) if len(a) > 1 else "en") if a else None)
        R("Ljava/util/Locale;", "<init>", "(Ljava/lang/String;Ljava/lang/String;)V",
          lambda a: _loc_set(a[0], self._txt(a[1]) if len(a) > 1 else "en", self._txt(a[2]) if len(a) > 2 else "") if a else None)
        R("Ljava/util/Locale;", "getDefault", "()Ljava/util/Locale;",
          lambda a: JObject("Ljava/util/Locale;", {"lang": "de", "country": "DE"}))
        R("Ljava/util/Locale;", "getLanguage", "()Ljava/lang/String;",
          lambda a: JString(str(a[0].fields.get("lang", "de") or "de") if a and isinstance(a[0], JObject) else "de"))
        R("Ljava/util/Locale;", "getCountry", "()Ljava/lang/String;",
          lambda a: JString(str(a[0].fields.get("country", "DE") or "") if a and isinstance(a[0], JObject) else "DE"))
        R("Ljava/util/Locale;", "toLanguageTag", "()Ljava/lang/String;",
          lambda a: JString(
              (f"{a[0].fields.get('lang', 'de')}-{a[0].fields.get('country')}"
               if a and isinstance(a[0], JObject) and a[0].fields.get("country")
               else (a[0].fields.get("lang", "de") if a and isinstance(a[0], JObject) else "de"))
          ))
        R("Ljava/util/Locale;", "toString", "()Ljava/lang/String;",
          lambda a: JString(
              (f"{a[0].fields.get('lang', 'de')}_{a[0].fields.get('country')}"
               if a and isinstance(a[0], JObject) and a[0].fields.get("country")
               else (a[0].fields.get("lang", "de") if a and isinstance(a[0], JObject) else "de"))
          ))
        R("Ljava/lang/String$CaseInsensitiveComparator;", "compare", "(Ljava/lang/Object;Ljava/lang/Object;)I",
          lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(
              (self._txt(a[1]) or "").lower() if len(a) > 1 else "",
              (self._txt(a[2]) or "").lower() if len(a) > 2 else "",
          ))
        R("Ljava/lang/String$CaseInsensitiveComparator;", "compare", "(Ljava/lang/String;Ljava/lang/String;)I",
          lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(
              (self._txt(a[1]) or "").lower() if len(a) > 1 else "",
              (self._txt(a[2]) or "").lower() if len(a) > 2 else "",
          ))
        R("Ljava/util/Date;", "<init>", "()V",
          lambda a: a[0].fields.__setitem__("__ms", int(self.current_time_ms(advance=False))) if a and isinstance(a[0], JObject) else None)
        R("Ljava/util/Date;", "<init>", "(J)V",
          lambda a: a[0].fields.__setitem__("__ms", int(a[1] or 0) if len(a) > 1 else 0) if a and isinstance(a[0], JObject) else None)
        R("Ljava/util/Date;", "getTime", "()J",
          lambda a: int(a[0].fields.get("__ms", 0)) if a and isinstance(a[0], JObject) else 0)
        R("Ljava/util/Date;", "setTime", "(J)V",
          lambda a: a[0].fields.__setitem__("__ms", int(a[1] or 0) if len(a) > 1 else 0) if a and isinstance(a[0], JObject) else None)
        def _sdf_format(a):
            pat = a[0].fields.get("__pattern", "yyyy-MM-dd") if a and isinstance(a[0], JObject) else "yyyy-MM-dd"
            loc = a[0].fields.get("__locale") if a and isinstance(a[0], JObject) else None
            tz_name = a[0].fields.get("__tz") if a and isinstance(a[0], JObject) else None
            d_arg = a[1] if len(a) > 1 else None
            if isinstance(d_arg, JObject) and "__ms" in d_arg.fields:
                ms = int(d_arg.fields.get("__ms", 0) or 0)
            elif isinstance(d_arg, JObject) and "value" in d_arg.fields and isinstance(d_arg.fields["value"], (int, float)):
                ms = int(d_arg.fields["value"])
            elif isinstance(d_arg, (int, float)):
                ms = int(d_arg)
            else:
                ms = 0
            eff_tz = tz_name or getattr(self, "_clock_tz", None)
            try:
                if eff_tz:
                    from core.java.virtual_clock import resolve_tzinfo
                    dt = _dt.datetime.fromtimestamp(ms / 1000.0, tz=resolve_tzinfo(eff_tz))
                else:
                    dt = _dt.datetime.fromtimestamp(ms / 1000.0)
            except Exception:
                dt = _dt.datetime.fromtimestamp(0)
            return JString(_format_java_date(dt, pat, loc))
        for _df in ("Ljava/text/SimpleDateFormat;", "Ljava/text/DateFormat;"):
            R(_df, "<init>", "(Ljava/lang/String;)V",
              lambda a: a[0].fields.__setitem__("__pattern", (self._txt(a[1]) or "yyyy-MM-dd") if len(a) > 1 else "yyyy-MM-dd") if a and isinstance(a[0], JObject) else None)
            R(_df, "<init>", "(Ljava/lang/String;Ljava/util/Locale;)V",
              lambda a: (a[0].fields.__setitem__("__pattern", (self._txt(a[1]) or "yyyy-MM-dd") if len(a) > 1 else "yyyy-MM-dd"),
                         a[0].fields.__setitem__("__locale", a[2] if len(a) > 2 else None)) if a and isinstance(a[0], JObject) else None)
            R(_df, "format", "(Ljava/util/Date;)Ljava/lang/String;", _sdf_format)
            R(_df, "format", "(Ljava/lang/Object;)Ljava/lang/String;", _sdf_format)
        R("Ljava/lang/Class;", "getPackage", "()Ljava/lang/Package;",
          lambda a: JObject("Ljava/lang/Package;", {"name": (self._txt(self._cls_getname(a)) or "").rsplit(".", 1)[0]}))
        R("Ljava/lang/Package;", "getName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("name", "") if a and isinstance(a[0], JObject) else ""))
        self.register_by_name("isAccessible", "()Z", lambda a: 1)
        def _b64_bytes(arr):
            data = getattr(arr, "data", None)
            if data is not None:
                return bytes(int(x) & 0xff for x in data)
            if isinstance(arr, (bytes, bytearray)):
                return bytes(arr)
            return b""
        def _b64_encode_str(a):
            import base64 as _b64
            raw = _b64_bytes(a[0]) if a else b""
            flags = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            s = _b64.urlsafe_b64encode(raw).decode("ascii") if (flags & 8) else _b64.b64encode(raw).decode("ascii")
            if flags & 1:
                s = s.rstrip("=")
            elif not (flags & 2):
                s = s + "\n"
            return JString(s)
        def _b64_decode(a):
            import base64 as _b64
            if not a:
                return JArray("[B", [])
            raw = (self._txt(a[0]) or "").encode("ascii", "ignore") if isinstance(a[0], JString) else _b64_bytes(a[0])
            flags = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            pad = raw + b"=" * ((4 - len(raw) % 4) % 4)
            dec = _b64.urlsafe_b64decode(pad) if (flags & 8) else _b64.b64decode(pad)
            return JArray("[B", list(dec))
        R("Landroid/util/Base64;", "encodeToString", "([BI)Ljava/lang/String;", _b64_encode_str)
        R("Landroid/util/Base64;", "decode", "(Ljava/lang/String;I)[B", _b64_decode)
        R("Landroid/util/Base64;", "decode", "([BI)[B", _b64_decode)
        # TypedValue.complexToFloat (echte AOSP-Formel)
        R("Landroid/util/TypedValue;", "complexToFloat", "(I)F", self._complex_to_float)
        R("Landroid/util/TypedValue;", "complexToDimension", "(ILandroid/util/DisplayMetrics;)F",
          lambda a: self._complex_to_float([a[0]]) if a else 0.0)
        # View.resolveSize / Drawable.resolveOpacity / CdmaCellLocation (Opaque Predicates, spec-exakt)
        R("Landroid/view/View;", "resolveSize", "(II)I",
          lambda a: self._view_resolvesize([a[0], a[1], 0]) & 0x00FFFFFF if len(a) > 1 else 0)
        R("Landroid/graphics/drawable/Drawable;", "resolveOpacity", "(II)I", self._resolve_opacity)
        R("Landroid/telephony/cdma/CdmaCellLocation;", "convertQuartSecToDecDegrees", "(I)D",
          self._quartsec_to_deg)
        R("Landroid/widget/ExpandableListView;", "getPackedPositionForChild", "(II)J",
          lambda a: (0x8000000000000000 | ((int(a[0]) & 0xFFFFFFFF) << 32) | (int(a[1]) & 0xFFFFFFFF)) if len(a) > 1 else -1)
        R("Landroid/widget/ExpandableListView;", "getPackedPositionForGroup", "(I)J",
          lambda a: ((int(a[0]) & 0x7FFFFFFF) << 32) if a else 0)
        R("Landroid/media/AudioTrack;", "getMaxVolume", "()F", lambda a: 1.0)
        R("Landroid/media/AudioTrack;", "getMinVolume", "()F", lambda a: 0.0)
        R("Landroid/view/KeyEvent;", "getModifierMetaStateMask", "()I", lambda a: 0x77)
        # --- StackTraceElement + Thread/Throwable.getStackTrace (spec-treu aus vm._frames) ---
        STE = "Ljava/lang/StackTraceElement;"
        R(STE, "getClassName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("cls", "") if a and isinstance(a[0], JObject) else ""))
        R(STE, "getMethodName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("method", "") if a and isinstance(a[0], JObject) else ""))
        R(STE, "getFileName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("file", "SourceFile") if a and isinstance(a[0], JObject) else "SourceFile"))
        R(STE, "getLineNumber", "()I",
          lambda a: int(a[0].fields.get("line", 1)) if a and isinstance(a[0], JObject) else -1)
        R(STE, "isNativeMethod", "()Z", lambda a: 0)
        R("Ljava/lang/Thread;", "getStackTrace", "()[Ljava/lang/StackTraceElement;", self._make_stack_trace)
        self.register_by_name("getStackTrace", "()[Ljava/lang/StackTraceElement;", self._make_stack_trace)
        R("Ljava/net/URL;", "getPath", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("path", "") if a and isinstance(a[0], JObject) else ""))
        R("Ljava/net/URL;", "getFile", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("path", "") if a and isinstance(a[0], JObject) else ""))
        R("Ljava/net/URL;", "toString", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("path", "") if a and isinstance(a[0], JObject) else ""))
        R("Ljava/lang/Class;", "getResource", "(Ljava/lang/String;)Ljava/net/URL;", self._cls_getresource)
        R("Ljava/lang/Class;", "getResourceAsStream", "(Ljava/lang/String;)Ljava/io/InputStream;", self._cls_getresource_stream)
        def _is_assignable_from(a):
            if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
                return 0
            c0 = self._desc_from_dotted(a[0].fields.get("name"))
            c1 = self._desc_from_dotted(a[1].fields.get("name"))
            if not c0 or not c1:
                return 0
            if c0 == c1 or c0 == "Ljava/lang/Object;":
                return 1
            try:
                if getattr(self, "vm", None) is not None and self.vm._is_subclass(c1, c0):
                    return 1
            except Exception:
                pass
            return 0
        R("Ljava/lang/Class;", "isAssignableFrom", "(Ljava/lang/Class;)Z", _is_assignable_from)
        for _cl_cls in ("Ljava/lang/ClassLoader;", "Ldalvik/system/BaseDexClassLoader;",
                        "Ldalvik/system/PathClassLoader;", "Ldalvik/system/DexClassLoader;",
                        "Ldalvik/system/InMemoryDexClassLoader;"):
            R(_cl_cls, "getResource", "(Ljava/lang/String;)Ljava/net/URL;", self._cls_getresource)
            R(_cl_cls, "getResourceAsStream", "(Ljava/lang/String;)Ljava/io/InputStream;", self._cls_getresource_stream)
        self.register_by_name("getResource", "(Ljava/lang/String;)Ljava/net/URL;", self._cls_getresource)
        self.register_by_name("getResourceAsStream", "(Ljava/lang/String;)Ljava/io/InputStream;", self._cls_getresource_stream)
        def _default_url_open_conn(a):
            u = a[0] if a else None
            if isinstance(u, JObject) and u.fields.get("_bytes") is not None:
                return JObject("Ljava/net/URLConnection;", {
                    "$url": u.fields.get("$url") or u.fields.get("path") or "",
                    "_bytes": u.fields["_bytes"],
                })
            fn = self._by_name.get(("openConnection", "()Ljava/net/URLConnection;"))
            if fn is not None:
                return fn(a)
            return JObject("Ljava/net/URLConnection;", {"$url": u.fields.get("$url", "") if isinstance(u, JObject) else ""})
        def _default_url_open_stream(a):
            u = a[0] if a else None
            if isinstance(u, JObject) and u.fields.get("_bytes") is not None:
                return JObject("Ljava/io/FileInputStream;", {
                    "path": u.fields.get("path", ""), "pos": 0, "_mem": bytes(u.fields["_bytes"] or b"")
                })
            conn = _default_url_open_conn(a)
            fn = self._by_name.get(("getInputStream", "()Ljava/io/InputStream;"))
            return fn([conn]) if fn is not None else JObject("Ljava/io/FileInputStream;", {"path": "", "pos": 0, "_mem": b""})
        R("Ljava/net/URL;", "openConnection", "()Ljava/net/URLConnection;", _default_url_open_conn)
        R("Ljava/net/URL;", "openStream", "()Ljava/io/InputStream;", _default_url_open_stream)
        def _default_urlconn_in(a):
            c = a[0] if a else None
            if isinstance(c, JObject) and c.fields.get("_bytes") is None:
                fn = self._by_name.get(("getInputStream", "()Ljava/io/InputStream;"))
                if fn is not None:
                    return fn(a)
            return JObject("Ljava/io/FileInputStream;", {
                "path": c.fields.get("$url", "") if isinstance(c, JObject) else "",
                "pos": 0,
                "_mem": bytes((c.fields.get("_bytes") if isinstance(c, JObject) else None) or b""),
            })
        R("Ljava/net/URLConnection;", "getInputStream", "()Ljava/io/InputStream;", _default_urlconn_in)
        R("Landroid/text/TextUtils;", "lastIndexOf", "(Ljava/lang/CharSequence;CI)I",
          lambda a: (self._txt(a[0]) or "")[:((int(a[2]) + 1) if len(a) > 2 else None)].rfind(chr(int(a[1]) & 0xFFFF)) if len(a) > 1 else -1)
        R("Landroid/os/Process;", "getElapsedCpuTime", "()J", lambda a: 1)
        R("Landroid/os/Process;", "getStartUptimeMillis", "()J", lambda a: 9000)
        R("Ljava/lang/ClassLoader;", "<init>", "()V", lambda a: None)
        R("Ljava/lang/ClassLoader;", "<init>", "(Ljava/lang/ClassLoader;)V",
          lambda a: a[0].fields.__setitem__("_parent", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
        R("Ljava/nio/ByteOrder;", "nativeOrder", "()Ljava/nio/ByteOrder;",
          lambda a: JObject("Ljava/nio/ByteOrder;", {"order": "little", "_le": True}))
        # StrictMode / Environment / Resources (no-op bzw. plausible Objekte)
        R("Landroid/os/StrictMode;", "setThreadPolicy", "(Landroid/os/StrictMode$ThreadPolicy;)V", lambda a: None)
        R("Landroid/os/StrictMode;", "allowThreadDiskReads", "()Landroid/os/StrictMode$ThreadPolicy;",
          lambda a: JObject("Landroid/os/StrictMode$ThreadPolicy;", {}))
        R("Landroid/os/StrictMode;", "allowThreadDiskWrites", "()Landroid/os/StrictMode$ThreadPolicy;",
          lambda a: JObject("Landroid/os/StrictMode$ThreadPolicy;", {}))
        R("Landroid/os/Environment;", "getExternalStorageDirectory", "()Ljava/io/File;",
          lambda a: JObject("Ljava/io/File;", {"path": "/storage/emulated/0"}))
        R("Landroid/os/Environment;", "getDataDirectory", "()Ljava/io/File;",
          lambda a: JObject("Ljava/io/File;", {"path": "/data"}))
        R("Landroid/content/res/Resources;", "getDrawable", "(I)Landroid/graphics/drawable/Drawable;",
          lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {"res_id": int(a[1]) if len(a) > 1 and a[1] is not None else 0}))
        R("Landroid/content/res/Resources;", "getDrawable", "(ILandroid/content/res/Resources$Theme;)Landroid/graphics/drawable/Drawable;",
          lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {"res_id": int(a[1]) if len(a) > 1 and a[1] is not None else 0}))
        R("Landroid/content/Context;", "getDrawable", "(I)Landroid/graphics/drawable/Drawable;",
          lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {"res_id": int(a[1]) if len(a) > 1 and a[1] is not None else 0}))
        self.register_by_name("getConfiguration", "()Landroid/content/res/Configuration;",
                              lambda a: JObject("Landroid/content/res/Configuration;", {}))
        # AtomicReferenceFieldUpdater / AtomicIntegerFieldUpdater / AtomicLongFieldUpdater
        ARFU = "Ljava/util/concurrent/atomic/AtomicReferenceFieldUpdater;"
        R(ARFU, "newUpdater",
          "(Ljava/lang/Class;Ljava/lang/Class;Ljava/lang/String;)Ljava/util/concurrent/atomic/AtomicReferenceFieldUpdater;",
          lambda a: JObject(ARFU, {"field": self._txt(a[2]) if len(a) > 2 else None}))
        R(ARFU, "get", "(Ljava/lang/Object;)Ljava/lang/Object;",
          lambda a: (None if len(a) <= 1 or not isinstance(a[1], JObject) or not isinstance(a[0], JObject)
                     or AndroidStubs._is_dalvik_null(a[1].fields.get(a[0].fields.get("field")))
                     else a[1].fields.get(a[0].fields.get("field"))))
        R(ARFU, "set", "(Ljava/lang/Object;Ljava/lang/Object;)V", self._arfu_set)
        R(ARFU, "lazySet", "(Ljava/lang/Object;Ljava/lang/Object;)V", self._arfu_set)
        R(ARFU, "compareAndSet", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Z", self._arfu_cas)
        R(ARFU, "weakCompareAndSet", "(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Z", self._arfu_cas)
        R(ARFU, "getAndSet", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._arfu_getset)

        AIFU = "Ljava/util/concurrent/atomic/AtomicIntegerFieldUpdater;"
        R(AIFU, "newUpdater",
          "(Ljava/lang/Class;Ljava/lang/String;)Ljava/util/concurrent/atomic/AtomicIntegerFieldUpdater;",
          lambda a: JObject(AIFU, {"field": self._txt(a[1]) if len(a) > 1 else None}))
        R(AIFU, "get", "(Ljava/lang/Object;)I", lambda a: self._anfu_get(a, 0xffffffff))
        R(AIFU, "set", "(Ljava/lang/Object;I)V", lambda a: self._anfu_set(a, 0xffffffff))
        R(AIFU, "lazySet", "(Ljava/lang/Object;I)V", lambda a: self._anfu_set(a, 0xffffffff))
        R(AIFU, "compareAndSet", "(Ljava/lang/Object;II)Z", lambda a: self._anfu_cas(a, 0xffffffff))
        R(AIFU, "weakCompareAndSet", "(Ljava/lang/Object;II)Z", lambda a: self._anfu_cas(a, 0xffffffff))
        R(AIFU, "getAndSet", "(Ljava/lang/Object;I)I", lambda a: self._anfu_getset(a, 0xffffffff))
        R(AIFU, "getAndAdd", "(Ljava/lang/Object;I)I", lambda a: self._anfu_add(a, 0xffffffff, int(a[2] or 0) if len(a) > 2 else 0, False))
        R(AIFU, "addAndGet", "(Ljava/lang/Object;I)I", lambda a: self._anfu_add(a, 0xffffffff, int(a[2] or 0) if len(a) > 2 else 0, True))
        R(AIFU, "getAndIncrement", "(Ljava/lang/Object;)I", lambda a: self._anfu_add(a, 0xffffffff, 1, False))
        R(AIFU, "incrementAndGet", "(Ljava/lang/Object;)I", lambda a: self._anfu_add(a, 0xffffffff, 1, True))
        R(AIFU, "getAndDecrement", "(Ljava/lang/Object;)I", lambda a: self._anfu_add(a, 0xffffffff, -1, False))
        R(AIFU, "decrementAndGet", "(Ljava/lang/Object;)I", lambda a: self._anfu_add(a, 0xffffffff, -1, True))

        ALFU = "Ljava/util/concurrent/atomic/AtomicLongFieldUpdater;"
        R(ALFU, "newUpdater",
          "(Ljava/lang/Class;Ljava/lang/String;)Ljava/util/concurrent/atomic/AtomicLongFieldUpdater;",
          lambda a: JObject(ALFU, {"field": self._txt(a[1]) if len(a) > 1 else None}))
        R(ALFU, "get", "(Ljava/lang/Object;)J", lambda a: self._anfu_get(a, 0xffffffffffffffff))
        R(ALFU, "set", "(Ljava/lang/Object;J)V", lambda a: self._anfu_set(a, 0xffffffffffffffff))
        R(ALFU, "lazySet", "(Ljava/lang/Object;J)V", lambda a: self._anfu_set(a, 0xffffffffffffffff))
        R(ALFU, "compareAndSet", "(Ljava/lang/Object;JJ)Z", lambda a: self._anfu_cas(a, 0xffffffffffffffff))
        R(ALFU, "weakCompareAndSet", "(Ljava/lang/Object;JJ)Z", lambda a: self._anfu_cas(a, 0xffffffffffffffff))
        R(ALFU, "getAndSet", "(Ljava/lang/Object;J)J", lambda a: self._anfu_getset(a, 0xffffffffffffffff))
        R(ALFU, "getAndAdd", "(Ljava/lang/Object;J)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, int(a[2] or 0) if len(a) > 2 else 0, False))
        R(ALFU, "addAndGet", "(Ljava/lang/Object;J)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, int(a[2] or 0) if len(a) > 2 else 0, True))
        R(ALFU, "getAndIncrement", "(Ljava/lang/Object;)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, 1, False))
        R(ALFU, "incrementAndGet", "(Ljava/lang/Object;)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, 1, True))
        R(ALFU, "getAndDecrement", "(Ljava/lang/Object;)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, -1, False))
        R(ALFU, "decrementAndGet", "(Ljava/lang/Object;)J", lambda a: self._anfu_add(a, 0xffffffffffffffff, -1, True))

        # java.util.concurrent.TimeUnit
        TU = "Ljava/util/concurrent/TimeUnit;"
        def _tu_ns(obj, default=1_000_000):
            return int(obj.fields.get("_ns", default)) if isinstance(obj, JObject) else default
        for _uname, _mname, _target_ns in _TIME_UNITS:
            R(TU, _mname, "(J)J",
              lambda a, _tns=_target_ns: (int(a[1] or 0) * _tu_ns(a[0]) // _tns) if len(a) > 1 else 0)
        R(TU, "convert", "(JLjava/util/concurrent/TimeUnit;)J",
          lambda a: (int(a[1] or 0) * _tu_ns(a[2]) // max(1, _tu_ns(a[0]))) if len(a) > 2 else 0)
        R(TU, "sleep", "(J)V", lambda a: None)
        _tu_objs = [JObject(TU, {"__ename": _un, "__ordinal": _idx, "_ns": _ns})
                    for _idx, (_un, _mn, _ns) in enumerate(_TIME_UNITS)]
        R(TU, "values", "()[Ljava/util/concurrent/TimeUnit;", lambda a, _vals=_tu_objs: JArray("[" + TU, list(_vals)))
        R(TU, "valueOf", "(Ljava/lang/String;)Ljava/util/concurrent/TimeUnit;",
          lambda a, _by_nm={_un: o for (_un, _mn, _ns), o in zip(_TIME_UNITS, _tu_objs)}: _by_nm.get(
              self._txt(a[0]) if a else "", _by_nm["MILLISECONDS"]))

        # LockSupport + Notifications/Permissions
        LS = "Ljava/util/concurrent/locks/LockSupport;"
        def _park_ns(ns):
            try:
                v = int(ns or 0)
            except Exception:
                v = 0
            if v > 0:
                self._tick_ms(min(120_000, max(1, (v + 999_999) // 1_000_000)))
            return None
        R(LS, "parkNanos", "(Ljava/lang/Object;J)V", lambda a: _park_ns(a[1] if len(a) > 1 else 0))
        R(LS, "parkNanos", "(J)V", lambda a: _park_ns(a[0] if a else 0))
        R(LS, "park", "(Ljava/lang/Object;)V", lambda a: (self._tick_ms(50), None)[1])
        R(LS, "park", "()V", lambda a: (self._tick_ms(50), None)[1])
        R(LS, "unpark", "(Ljava/lang/Thread;)V", lambda a: None)

        NMC = "Landroidx/core/app/NotificationManagerCompat;"
        R(NMC, "from", "(Landroid/content/Context;)Landroidx/core/app/NotificationManagerCompat;",
          lambda a: JObject(NMC, {}))
        R(NMC, "areNotificationsEnabled", "()Z",
          lambda a: 1 if getattr(self, "_notif_enabled", False) else 0)
        R("Landroidx/core/content/ContextCompat;", "checkSelfPermission",
          "(Landroid/content/Context;Ljava/lang/String;)I", lambda a: 0)

        # Weitere Collections
        R("Ljava/util/WeakHashMap;", "<init>", "()V", lambda a: self._coll_init(a, "map", {}))
        R("Ljava/util/WeakHashMap;", "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", self._map_put)
        R("Ljava/util/WeakHashMap;", "get", "(Ljava/lang/Object;)Ljava/lang/Object;", self._map_get)
        R("Ljava/util/LinkedHashMap;", "<init>", "(I)V", lambda a: self._coll_init(a, "map", {}))
        R("Ljava/util/LinkedHashMap;", "<init>", "(IFZ)V", lambda a: self._coll_init(a, "map", {}))
        R("Ljava/util/Collections;", "synchronizedMap", "(Ljava/util/Map;)Ljava/util/Map;",
          lambda a: a[1] if len(a) > 1 else (a[0] if a else JObject("Ljava/util/HashMap;", {"map": {}})))
        R("Ljava/util/Collections;", "synchronizedList", "(Ljava/util/List;)Ljava/util/List;",
          lambda a: a[1] if len(a) > 1 else (a[0] if a else JObject("Ljava/util/ArrayList;", {"__items": []})))
        R("Ljava/util/Collections;", "unmodifiableMap", "(Ljava/util/Map;)Ljava/util/Map;",
          lambda a: a[1] if len(a) > 1 else (a[0] if a else None))
        R("Ljava/util/Collections;", "unmodifiableList", "(Ljava/util/List;)Ljava/util/List;",
          lambda a: a[1] if len(a) > 1 else (a[0] if a else None))
        R("Ljava/util/Collections;", "newSetFromMap", "(Ljava/util/Map;)Ljava/util/Set;",
          lambda a: JObject("Ljava/util/HashSet;", {"__items": []}))
        R("Ljava/util/Arrays;", "asList", "([Ljava/lang/Object;)Ljava/util/List;",
          lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(a[0].data) if a and isinstance(a[0], JArray) else []}))
        def _arr_cast_val(tc, raw):
            if tc == "Ljava/lang/Object;":
                return raw
            if tc in ("F", "D"):
                return raw if raw is not None else 0.0
            if tc == "Z":
                return 1 if raw else 0
            try:
                iv = int(raw) if raw is not None else 0
            except Exception:
                iv = 0
            return (iv & 0xFF) if tc == "B" else iv

        def _arrays_fill_impl(a):
            if not a or not isinstance(a[0], JArray):
                return None
            arr = a[0]
            el = getattr(arr, "eltype", "") or ""
            tc = "B" if el in ("B", "[B") else ("Ljava/lang/Object;" if el.startswith(("L", "[L")) else el.lstrip("["))
            if len(a) >= 4:
                s = max(0, int(a[1] or 0))
                e = min(len(arr.data), int(a[2] or 0))
                val = _arr_cast_val(tc, a[3])
                if e > s:
                    arr.data[s:e] = [val] * (e - s)
            elif len(a) >= 2:
                val = _arr_cast_val(tc, a[1])
                arr.data[:] = [val] * len(arr.data)
            return None

        def _arrays_copy_impl(default_el):
            def _fn(a):
                if len(a) > 1 and isinstance(a[0], JArray):
                    el = getattr(a[0], "eltype", default_el) or default_el
                    nlen = max(0, int(a[1] or 0))
                    pad = None if "Object" in el else (0.0 if el in ("F", "D", "[F", "[D") else 0)
                    return JArray(el, (list(a[0].data) + [pad] * nlen)[:nlen])
                return JArray(default_el, [])
            return _fn

        for _atc in ("Z", "B", "S", "C", "I", "J", "F", "D", "Ljava/lang/Object;"):
            R("Ljava/util/Arrays;", "fill", f"([{_atc}{_atc})V", _arrays_fill_impl)
            R("Ljava/util/Arrays;", "fill", f"([{_atc}II{_atc})V", _arrays_fill_impl)
            R("Ljava/util/Arrays;", "copyOf", f"([{_atc}I)[{_atc}", _arrays_copy_impl(f"[{_atc}"))
        def _arrays_copy_cls(a):
            if len(a) > 2 and isinstance(a[2], JObject):
                td = self._desc_from_class_obj(a[2])
                if isinstance(td, str):
                    td = td.replace(".", "/")
                if isinstance(td, str) and td.startswith("["):
                    if len(a) > 1 and isinstance(a[0], JArray):
                        nlen = max(0, int(a[1] or 0))
                        return JArray(td, (list(a[0].data) + [None] * nlen)[:nlen])
                    return JArray(td, [])
            return _arrays_copy_impl("[Ljava/lang/Object;")(a)

        R("Ljava/util/Arrays;", "copyOf", "([Ljava/lang/Object;ILjava/lang/Class;)[Ljava/lang/Object;",
          _arrays_copy_cls)
        R("Ljava/util/Arrays;", "copyOfRange", "([Ljava/lang/Object;II)[Ljava/lang/Object;",
          lambda a: JArray(getattr(a[0], "eltype", None) or "[Ljava/lang/Object;", list(a[0].data[int(a[1]):int(a[2])]))
          if len(a) > 2 and isinstance(a[0], JArray) else JArray("[Ljava/lang/Object;", []))
        R("Ljava/util/Arrays;", "copyOfRange", "([BII)[B",
          lambda a: JArray("[B", list(a[0].data[int(a[1]):int(a[2])]))
          if len(a) > 2 and isinstance(a[0], JArray) else JArray("[B", []))
        R("Ljava/util/Arrays;", "equals", "([B[B)Z",
          lambda a: 1 if (len(a) > 1 and isinstance(a[0], JArray) and isinstance(a[1], JArray)
                          and a[0].data == a[1].data) else 0)
        import math as _math, struct as _struct
        def _as_f64(x):
            if isinstance(x, float):
                return x
            try:
                return _struct.unpack("<d", _struct.pack("<Q", int(x) & 0xFFFFFFFFFFFFFFFF))[0]
            except Exception:
                return float(x)
        def _as_f32(x):
            if isinstance(x, float):
                return x
            try:
                return _struct.unpack("<f", _struct.pack("<I", int(x) & 0xFFFFFFFF))[0]
            except Exception:
                return float(x)
        def _s32(v):
            n = int(v) & 0xffffffff
            return n - 0x100000000 if (n & 0x80000000) else n

        def _s64(v):
            n = int(v) & 0xffffffffffffffff
            return n - 0x10000000000000000 if (n & 0x8000000000000000) else n

        R("Ljava/lang/Math;", "min", "(II)I",
          lambda a: (min(_s32(a[0]), _s32(a[1])) & 0xffffffff) if len(a) > 1 else 0)
        R("Ljava/lang/Math;", "min", "(JJ)J",
          lambda a: (min(_s64(a[0]), _s64(a[1])) & 0xffffffffffffffff) if len(a) > 1 else 0)
        R("Ljava/lang/Math;", "max", "(II)I",
          lambda a: (max(_s32(a[0]), _s32(a[1])) & 0xffffffff) if len(a) > 1 else 0)
        R("Ljava/lang/Math;", "max", "(JJ)J",
          lambda a: (max(_s64(a[0]), _s64(a[1])) & 0xffffffffffffffff) if len(a) > 1 else 0)
        R("Ljava/lang/Math;", "abs", "(I)I",
          lambda a: (abs(_s32(a[0])) & 0xffffffff) if a else 0)
        R("Ljava/lang/Math;", "abs", "(J)J",
          lambda a: (abs(_s64(a[0])) & 0xffffffffffffffff) if a else 0)
        R("Ljava/lang/Math;", "pow", "(DD)D",
          lambda a: float(_math.pow(_as_f64(a[0]), _as_f64(a[1]))) if len(a) > 1 else 0.0)
        R("Ljava/lang/Math;", "sqrt", "(D)D",
          lambda a: float(_math.sqrt(max(0.0, _as_f64(a[0])))) if a else 0.0)

        def _next_rand_int(bound=None):
            self._rng_seq = (getattr(self, "_rng_seq", 0) + 1) & 0x7FFFFFFF
            val = ((self._rng_seq * 1103515245 + 12345) >> 1) & 0x7FFFFFFF
            if bound is not None:
                b = int(bound)
                return (val % b) if b > 0 else 0
            return val

        for _rc in ("Ljava/util/Random;", "Ljava/security/SecureRandom;",
                    "Ljava/util/concurrent/ThreadLocalRandom;"):
            R(_rc, "<init>", "()V", lambda a: None)
            R(_rc, "<init>", "(J)V", lambda a: None)
            R(_rc, "nextInt", "()I", lambda a: _next_rand_int())
            R(_rc, "nextInt", "(I)I", lambda a: _next_rand_int(a[1] if len(a) > 1 else 1))
            R(_rc, "nextInt", "(II)I",
              lambda a: (int(a[1]) + _next_rand_int(int(a[2]) - int(a[1]))) if len(a) > 2 else _next_rand_int())
            R(_rc, "nextLong", "()J", lambda a: (_next_rand_int() << 32) | _next_rand_int())
            R(_rc, "nextBoolean", "()Z", lambda a: _next_rand_int(2))
            R(_rc, "nextFloat", "()F", lambda a: 0)
            R(_rc, "nextDouble", "()D", lambda a: 0)
            R(_rc, "setSeed", "(J)V", lambda a: None)
        R("Ljava/util/concurrent/ThreadLocalRandom;", "current",
          "()Ljava/util/concurrent/ThreadLocalRandom;",
          lambda a: JObject("Ljava/util/concurrent/ThreadLocalRandom;", {}))
        R("Ljava/util/ArrayDeque;", "<init>", "()V", lambda a: self._coll_init(a, "__items", []))
        R("Ljava/util/ArrayDeque;", "<init>", "(I)V", lambda a: self._coll_init(a, "__items", []))
        R("Ljava/util/ArrayDeque;", "<init>", "(Ljava/util/Collection;)V", self._jcoll_init)
        R("Ljava/util/ArrayDeque;", "addLast", "(Ljava/lang/Object;)V", lambda a: (self._coll_add(a), None)[1])
        R("Ljava/util/ArrayDeque;", "addFirst", "(Ljava/lang/Object;)V", lambda a: self._coll_add_idx([a[0], 0, a[1] if len(a) > 1 else None]) if a else None)
        R("Ljava/util/ArrayDeque;", "removeFirst", "()Ljava/lang/Object;", lambda a: self._coll_items(a[0]).pop(0) if (a and self._coll_items(a[0])) else None)
        R("Ljava/util/ArrayDeque;", "pollFirst", "()Ljava/lang/Object;", lambda a: self._coll_items(a[0]).pop(0) if (a and self._coll_items(a[0])) else None)

        self._uuid_ctr = 0
        def _uuid_random(a):
            self._uuid_ctr += 1
            return JObject("Ljava/util/UUID;", {"text": "00000000-0000-4000-8000-%012x" % self._uuid_ctr})
        R("Ljava/util/UUID;", "randomUUID", "()Ljava/util/UUID;", _uuid_random)
        R("Ljava/util/UUID;", "toString", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("text", "00000000-0000-4000-8000-000000000001")) if (a and isinstance(a[0], JObject)) else JString(""))

        # --- PackageManager / PackageInfo / Signature / SigningInfo (APK-Cert-Fidelity) ---
        def _default_cert_bytes():
            cb = getattr(self, "_apk_cert_bytes", None)
            if cb:
                return bytes(cb)
            return b"\x30\x82\x01\x00" + (b"\x5a" * 60)

        def _resolve_apk_version():
            v_name = self.profile.get("version_name")
            v_code = self.profile.get("version_code")
            if v_name is not None and v_code is not None:
                return str(v_name), int(v_code)
            cached = getattr(self, "_cached_apk_version", None)
            ar = getattr(self, "_app_root", None) or ""
            if cached and cached[0] == ar:
                return (str(v_name) if v_name is not None else cached[1]), (int(v_code) if v_code is not None else cached[2])
            det_name, det_code = "1.0.0", 1
            if ar:
                import os as _os
                import re as _re
                yml_p = _os.path.join(ar, "apktool.yml")
                if _os.path.isfile(yml_p):
                    try:
                        with open(yml_p, encoding="utf-8", errors="ignore") as _yf:
                            ytxt = _yf.read(8192)
                        mn = _re.search(r"^\s*versionName:\s*['\"]?([^'\"\r\n]+)['\"]?", ytxt, _re.M)
                        mc = _re.search(r"^\s*versionCode:\s*['\"]?(\d+)['\"]?", ytxt, _re.M)
                        if mn and mn.group(1).strip():
                            det_name = mn.group(1).strip()
                        if mc:
                            det_code = int(mc.group(1))
                    except Exception:
                        pass
                if det_name == "1.0.0":
                    man_p = _os.path.join(ar, "AndroidManifest.xml")
                    if _os.path.isfile(man_p):
                        try:
                            with open(man_p, encoding="utf-8", errors="ignore") as _mf:
                                mtxt = _mf.read(8192)
                            mn2 = _re.search(r'versionName\s*=\s*"([^"]+)"', mtxt)
                            mc2 = _re.search(r'versionCode\s*=\s*"(\d+)"', mtxt)
                            if mn2 and mn2.group(1).strip() and not mn2.group(1).startswith("@"):
                                det_name = mn2.group(1).strip()
                            if mc2:
                                det_code = int(mc2.group(1))
                        except Exception:
                            pass
            self._cached_apk_version = (ar, det_name, det_code)
            return (str(v_name) if v_name is not None else det_name), (int(v_code) if v_code is not None else det_code)

        def _make_pkg_info(pkg_name: str):
            raw_cert = _default_cert_bytes()
            sig_obj = JObject("Landroid/content/pm/Signature;", {"_bytes": raw_cert})
            sig_arr = JArray("[Landroid/content/pm/Signature;", [sig_obj])
            sinfo = JObject("Landroid/content/pm/SigningInfo;", {"_signers": sig_arr})
            app_info = (
                self._mk_appinfo()
                if callable(getattr(self, "_mk_appinfo", None))
                else JObject("Landroid/content/pm/ApplicationInfo;", {"packageName": JString(pkg_name)})
            )
            vn_str, vc_int = _resolve_apk_version()
            return JObject("Landroid/content/pm/PackageInfo;", {
                "packageName": JString(pkg_name),
                "versionName": JString(vn_str),
                "versionCode": int(vc_int),
                "longVersionCode": int(vc_int),
                "applicationInfo": app_info,
                "signatures": sig_arr,
                "signingInfo": sinfo,
            })

        R("Landroid/content/pm/PackageInfo;", "getLongVersionCode", "()J",
          lambda a: int(a[0].fields.get("longVersionCode", a[0].fields.get("versionCode", 1)) or 1) if a and isinstance(a[0], JObject) else 1)

        R("Landroid/content/pm/PackageManager;", "getPackageInfo",
          "(Ljava/lang/String;I)Landroid/content/pm/PackageInfo;",
          lambda a: _make_pkg_info(self._txt(a[1]) or getattr(self.profile, "package", "com.example.app") if len(a) > 1 else "com.example.app"))
        R("Landroid/content/pm/PackageManager;", "getPackageInfo",
          "(Landroid/content/pm/VersionedPackage;I)Landroid/content/pm/PackageInfo;",
          lambda a: _make_pkg_info(getattr(self.profile, "package", "com.example.app")))
        _installer_pkg = lambda: str(self.profile.get("installer_package", "com.android.vending") or "com.android.vending")
        R("Landroid/content/pm/PackageManager;", "getInstallerPackageName",
          "(Ljava/lang/String;)Ljava/lang/String;",
          lambda a: JString(_installer_pkg()))
        R("Landroid/content/pm/PackageManager;", "getInstallSourceInfo",
          "(Ljava/lang/String;)Landroid/content/pm/InstallSourceInfo;",
          lambda a: JObject("Landroid/content/pm/InstallSourceInfo;", {
              "installing": _installer_pkg(),
              "initiating": _installer_pkg(),
              "originating": None,
          }))
        R("Landroid/content/pm/InstallSourceInfo;", "getInstallingPackageName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("installing") or "com.android.vending") if a and isinstance(a[0], JObject) else JString("com.android.vending"))
        R("Landroid/content/pm/InstallSourceInfo;", "getInitiatingPackageName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("initiating") or "com.android.vending") if a and isinstance(a[0], JObject) else JString("com.android.vending"))
        R("Landroid/content/pm/InstallSourceInfo;", "getOriginatingPackageName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields["originating"]) if a and isinstance(a[0], JObject) and a[0].fields.get("originating") else None)

        def _sig_bytes(o):
            if isinstance(o, JObject) and o.fields.get("_bytes") is not None:
                return bytes(o.fields["_bytes"])
            return _default_cert_bytes()

        import zlib as _zlib
        R("Landroid/content/pm/Signature;", "toByteArray", "()[B",
          lambda a: JArray("[B", list(_sig_bytes(a[0] if a else None))))
        R("Landroid/content/pm/Signature;", "toCharsString", "()Ljava/lang/String;",
          lambda a: JString(_sig_bytes(a[0] if a else None).hex()))
        R("Landroid/content/pm/Signature;", "hashCode", "()I",
          lambda a: (lambda h: h - 0x100000000 if h & 0x80000000 else h)(_zlib.crc32(_sig_bytes(a[0] if a else None)) & 0xFFFFFFFF))
        R("Landroid/content/pm/Signature;", "equals", "(Ljava/lang/Object;)Z",
          lambda a: 1 if (len(a) > 1 and _sig_bytes(a[0]) == _sig_bytes(a[1])) else 0)
        R("Landroid/content/pm/SigningInfo;", "getApkContentsSigners", "()[Landroid/content/pm/Signature;",
          lambda a: a[0].fields.get("_signers") if a and isinstance(a[0], JObject) and a[0].fields.get("_signers") is not None else JArray("[Landroid/content/pm/Signature;", [JObject("Landroid/content/pm/Signature;", {"_bytes": _default_cert_bytes()})]))
        R("Landroid/content/pm/SigningInfo;", "getSigningCertificateHistory", "()[Landroid/content/pm/Signature;",
          lambda a: a[0].fields.get("_signers") if a and isinstance(a[0], JObject) and a[0].fields.get("_signers") is not None else JArray("[Landroid/content/pm/Signature;", [JObject("Landroid/content/pm/Signature;", {"_bytes": _default_cert_bytes()})]))
        R("Landroid/content/pm/SigningInfo;", "hasMultipleSigners", "()Z", lambda a: 0)
        R("Landroid/content/pm/SigningInfo;", "hasPastSigningCertificates", "()Z", lambda a: 0)

    # ---- AOSP-Helfer (spec-treu) -------------------------------------------
    def _sb_init_str(self, a):
        if a and isinstance(a[0], JObject):
            a[0].fields["buf"] = (self._txt(a[1]) or "") if len(a) > 1 else ""
        return None

    def _sb_set(self, o, text):
        if isinstance(o, JObject):
            o.fields["buf"] = text
        return o

    def _sb_setlen(self, a):
        o = a[0]; n = int(a[1]) if len(a) > 1 else 0
        if isinstance(o, JObject):
            b = o.fields.get("buf", "") or ""
            o.fields["buf"] = (b[:n] if n <= len(b) else b + "\x00" * (n - len(b)))
        return None

    def _sb_delat(self, a):
        o = a[0]; i = int(a[1]) if len(a) > 1 else 0
        if isinstance(o, JObject):
            b = o.fields.get("buf", "") or ""
            if 0 <= i < len(b):
                o.fields["buf"] = b[:i] + b[i + 1:]
        return o

    def _sb_insert(self, a):
        o = a[0]; i = int(a[1]) if len(a) > 1 else 0
        t = self._txt(a[2]) if len(a) > 2 else ""
        if isinstance(o, JObject):
            b = o.fields.get("buf", "") or ""
            i = max(0, min(i, len(b)))
            o.fields["buf"] = b[:i] + (t or "") + b[i:]
        return o

    @staticmethod
    def _char_digit(a):
        try:
            c = chr(int(a[0]) & 0xFFFF); radix = int(a[1]) if len(a) > 1 else 10
            v = int(c, radix)
            return v if 0 <= v < radix else -1
        except Exception:
            return -1

    def _throwable_init_msg(self, a):
        if a and isinstance(a[0], JObject):
            a[0].fields["message"] = self._txt(a[1]) if len(a) > 1 else None
        return a[0] if a else None

    def _throwable_init_msg_cause(self, a):
        if a and isinstance(a[0], JObject):
            a[0].fields["message"] = self._txt(a[1]) if len(a) > 1 else None
            a[0].fields["cause"] = a[2] if len(a) > 2 else None
        return a[0] if a else None

    def _map_keyset(self, a):
        if not (a and isinstance(a[0], JObject)):
            return JObject("Ljava/util/HashSet;", {"__items": []})
        m = a[0].fields.get("map", {})
        raw = a[0].fields.get("__raw_keys", {})
        keys = [raw.get(k, JString(k) if isinstance(k, str) else k) for k in m.keys()]
        return JObject("Ljava/util/HashSet;", {"__items": keys, "$backing_map": a[0], "$backing_kind": "keys"})

    def _map_entryset(self, a):
        if not (a and isinstance(a[0], JObject)):
            return JObject("Ljava/util/HashSet;", {"__items": []})
        m = a[0].fields.get("map", {})
        raw = a[0].fields.get("__raw_keys", {})
        ents = [
            JObject("Ljava/util/Map$Entry;", {
                "key": raw.get(k, JString(k) if isinstance(k, str) else k),
                "value": v,
            })
            for k, v in m.items()
        ]
        return JObject("Ljava/util/HashSet;", {"__items": ents, "$backing_map": a[0], "$backing_kind": "entries"})

    @classmethod
    def _map_copy_init(cls, dst, src, sort_keys=False):
        if not isinstance(dst, JObject):
            return dst
        src_map = src.fields.get("map", {}) if isinstance(src, JObject) else {}
        src_raw = src.fields.get("__raw_keys", {}) if isinstance(src, JObject) else {}
        items = list(src_map.items())
        if sort_keys:
            def _sort_k(kv):
                k = kv[0]
                rk = src_raw.get(k, k)
                if isinstance(rk, JObject) and rk.cls == "Ljava/time/LocalDate;":
                    return (0, int(rk.fields.get("year", 0)), int(rk.fields.get("month", 0)), int(rk.fields.get("day", 0)))
                if isinstance(k, tuple) and len(k) == 4 and k[0] == "LocalDate":
                    return (0, int(k[1]), int(k[2]), int(k[3]))
                if isinstance(rk, (int, float)):
                    return (1, float(rk), "")
                t = cls._txt(rk)
                return (2, 0, t if t is not None else str(k))
            items.sort(key=_sort_k)
        dst.fields["map"] = dict(items)
        dst.fields["__raw_keys"] = {k: src_raw[k] for k, _ in items if k in src_raw}
        return dst

    def _map_putall(self, a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JObject):
            dst = a[0].fields.setdefault("map", {})
            dst.update(a[1].fields.get("map", {}))
            a[0].fields.setdefault("__raw_keys", {}).update(a[1].fields.get("__raw_keys", {}))
        return None

    @staticmethod
    def _view_resolvesize(a):
        size = int(a[0]) if a else 0
        spec = int(a[1]) if len(a) > 1 else 0
        state = int(a[2]) if len(a) > 2 else 0
        MODE_MASK = 0x3 << 30
        mode = spec & MODE_MASK
        spec_size = spec & ~MODE_MASK & 0xFFFFFFFF
        if mode == (0x80000000 & 0xFFFFFFFF) or mode == 0x80000000:   # AT_MOST
            result = (spec_size | 0x01000000) if spec_size < size else size
        elif mode == 0x40000000:                                     # EXACTLY
            result = spec_size
        else:                                                        # UNSPECIFIED
            result = size
        return (result | (state & 0xFF000000)) & 0xFFFFFFFF

    @staticmethod
    def _elv_packedtype(a):
        packed = int(a[0]) & ((1 << 64) - 1) if a else 0
        if packed == 0x00000000FFFFFFFF:     # PACKED_POSITION_VALUE_NULL
            return 2                          # TYPE_NULL
        return 1 if (packed & 0x8000000000000000) else 0   # CHILD : GROUP

    @staticmethod
    def _pointf_length(a):
        import math
        x = float(a[0]) if a else 0.0; y = float(a[1]) if len(a) > 1 else 0.0
        return math.sqrt(x * x + y * y)

    @staticmethod
    def _imgfmt_bpp(a):
        fmt = int(a[0]) if a else 0
        table = {0x1: 8, 0x4: 16, 0x10: 16, 0x11: 12, 0x14: 16, 0x20: 16, 0x23: 12,
                 0x25: 10, 0x26: 12, 0x27: 14, 0x32315659: 12, 0x100: 24, 0x101: 8}
        return table.get(fmt, -1)

    @staticmethod
    def _complex_to_float(a):
        complex_ = int(a[0]) if a else 0
        mant = complex_ & (0xFFFFFF << 8)
        if mant >= 0x80000000:          # signed 32-bit
            mant -= 0x100000000
        radix = (complex_ >> 4) & 0x3
        mult = (1.0 / (1 << 8), 1.0 / (1 << 15), 1.0 / (1 << 23), 1.0 / (1 << 31))[radix]
        return float(mant) * mult

    @staticmethod
    def _resolve_opacity(a):
        op1 = int(a[0]) if a else 0
        op2 = int(a[1]) if len(a) > 1 else 0
        if op1 == op2: return op1
        if op1 == 0 or op2 == 0: return 0            # UNKNOWN
        if op1 == -3 or op2 == -3: return -3         # TRANSLUCENT
        if op1 == -2 or op2 == -2: return -2         # TRANSPARENT
        return -1                                    # OPAQUE

    def _quartsec_to_deg(self, a):
        q = int(a[0]) if a else 0
        if q < -2592000 or q > 2592000:
            raise DalvikException("throw IllegalArgumentException: quartSec not in range",
                                  payload=JObject("Ljava/lang/IllegalArgumentException;", {}))
        return float(q) / (3600.0 * 4.0)

    def _sys_nanotime(self, _a=None):
        self._nano_seq = getattr(self, "_nano_seq", 0) + 1_000
        return int(getattr(self, "_clock_ms", 1700000000000)) * 1_000_000 + self._nano_seq

    @staticmethod
    def _is_dalvik_null(x):
        return x is None or (type(x) is int and x == 0)

    @staticmethod
    def _norm_dalvik_ref(x):
        return None if AndroidStubs._is_dalvik_null(x) else x

    @staticmethod
    def _arfu_set(a):
        if len(a) > 2 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            a[1].fields[a[0].fields.get("field")] = AndroidStubs._norm_dalvik_ref(a[2])
        return None

    @staticmethod
    def _arfu_cas(a):
        if len(a) > 3 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            fn = a[0].fields.get("field")
            cur = a[1].fields.get(fn)
            exp = a[2]
            if (AndroidStubs._is_dalvik_null(cur) and AndroidStubs._is_dalvik_null(exp)) or cur is exp or cur == exp:
                a[1].fields[fn] = AndroidStubs._norm_dalvik_ref(a[3])
                return 1
        return 0

    @staticmethod
    def _arfu_getset(a):
        if len(a) > 2 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            fn = a[0].fields.get("field"); prev = a[1].fields.get(fn)
            a[1].fields[fn] = AndroidStubs._norm_dalvik_ref(a[2])
            return None if AndroidStubs._is_dalvik_null(prev) else prev
        return None

    @staticmethod
    def _anfu_signed(val, mask):
        v = int(val or 0) & mask
        sign = (mask + 1) >> 1
        return v - (mask + 1) if (v & sign) else v

    @staticmethod
    def _anfu_get(a, mask):
        if len(a) > 1 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            return AndroidStubs._anfu_signed(a[1].fields.get(a[0].fields.get("field"), 0), mask)
        return 0

    @staticmethod
    def _anfu_set(a, mask):
        if len(a) > 2 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            a[1].fields[a[0].fields.get("field")] = int(a[2] or 0) & mask
        return None

    @staticmethod
    def _anfu_cas(a, mask):
        if len(a) > 3 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            fn = a[0].fields.get("field")
            cur = int(a[1].fields.get(fn, 0) or 0) & mask
            exp = int(a[2] or 0) & mask
            if cur == exp:
                a[1].fields[fn] = int(a[3] or 0) & mask
                return 1
        return 0

    @staticmethod
    def _anfu_getset(a, mask):
        if len(a) > 2 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            fn = a[0].fields.get("field")
            prev = AndroidStubs._anfu_signed(a[1].fields.get(fn, 0), mask)
            a[1].fields[fn] = int(a[2] or 0) & mask
            return prev
        return 0

    @staticmethod
    def _anfu_add(a, mask, delta, return_new):
        if len(a) > 1 and isinstance(a[1], JObject) and isinstance(a[0], JObject):
            fn = a[0].fields.get("field")
            prev = AndroidStubs._anfu_signed(a[1].fields.get(fn, 0), mask)
            nxt = (prev + int(delta)) & mask
            a[1].fields[fn] = nxt
            return AndroidStubs._anfu_signed(nxt, mask) if return_new else prev
        return 0

    def _install_regex(self):
        """Implementiert java.util.regex.Pattern / Matcher / MatchResult auf Basis von Python re."""
        import re as _re
        PAT = "Ljava/util/regex/Pattern;"
        MAT = "Ljava/util/regex/Matcher;"
        MR = "Ljava/util/regex/MatchResult;"
        R = self.register

        def _compile_rx(pat_str, jflags=0):
            s = pat_str if isinstance(pat_str, str) else ""
            py_flags = 0
            if jflags & 0x02:
                py_flags |= _re.IGNORECASE
            if jflags & 0x04:
                py_flags |= _re.VERBOSE
            if jflags & 0x08:
                py_flags |= _re.MULTILINE
            if jflags & 0x20:
                py_flags |= _re.DOTALL
            if jflags & 0x40:
                py_flags |= _re.UNICODE
            if jflags & 0x10:
                s = _re.escape(s)
            try:
                return _re.compile(s, py_flags)
            except _re.error:
                # Java besitzt possessive Quantifier (*+, ++, ?+) und \Q..\E
                cleaned = _re.sub(r"([*?+}])\+", r"\1", s)
                try:
                    return _re.compile(cleaned, py_flags)
                except _re.error:
                    return _re.compile(_re.escape(s), py_flags)

        def _make_pattern(pat_str, jflags=0):
            s = pat_str if isinstance(pat_str, str) else ""
            return JObject(PAT, {
                "__pat": s,
                "__flags": int(jflags or 0),
                "__rx": _compile_rx(s, int(jflags or 0)),
            })

        def _pat_rx(pat_obj):
            if isinstance(pat_obj, JObject):
                rx = pat_obj.fields.get("__rx")
                if rx is not None:
                    return rx
                return _compile_rx(pat_obj.fields.get("__pat", ""), pat_obj.fields.get("__flags", 0))
            return _compile_rx("")

        def _make_matcher(pat_obj, cs_obj):
            txt = (self._txt(cs_obj) or "") if cs_obj is not None else ""
            return JObject(MAT, {
                "__pat": pat_obj,
                "__rx": _pat_rx(pat_obj),
                "__text": txt,
                "__pos": 0,
                "__reg_start": 0,
                "__reg_end": len(txt),
                "__match": None,
                "__anchoring": True,
                "__transparent": False,
            })

        def _java_split(pat_obj, cs_obj, limit=0):
            txt = (self._txt(cs_obj) or "") if cs_obj is not None else ""
            rx = _pat_rx(pat_obj)
            lim_i = 0 if limit is None else (((int(limit) + 0x80000000) & 0xFFFFFFFF) - 0x80000000)
            # Java Pattern.split(input, limit)
            if lim_i > 0:
                parts = rx.split(txt, maxsplit=max(0, lim_i - 1))
                # Python re.split gibt Capture-Gruppen mit aus; bei Java Pattern.split nicht
                if rx.groups:
                    parts = _re.split(f"(?:{rx.pattern})", txt, maxsplit=max(0, lim_i - 1), flags=rx.flags) if rx.pattern else [txt]
            else:
                if rx.groups:
                    parts = _re.split(f"(?:{rx.pattern})", txt, flags=rx.flags) if rx.pattern else [txt]
                else:
                    parts = rx.split(txt)
                if lim_i == 0:
                    while len(parts) > 1 and parts[-1] == "":
                        parts.pop()
            return JArray("[Ljava/lang/String;", [JString(p) for p in parts])

        def _to_py_repl(jrepl):
            # Wandelt Java $0/$1/\$ in Python \g<0>/\g<1>/$ um
            out = []
            i = 0
            n = len(jrepl)
            while i < n:
                ch = jrepl[i]
                if ch == "\\" and i + 1 < n:
                    out.append(jrepl[i + 1])
                    i += 2
                elif ch == "$" and i + 1 < n and jrepl[i + 1].isdigit():
                    j = i + 1
                    while j < n and jrepl[j].isdigit():
                        j += 1
                    out.append(f"\\g<{jrepl[i + 1:j]}>")
                    i = j
                else:
                    if ch == "\\":
                        out.append("\\\\")
                    else:
                        out.append(ch)
                    i += 1
            return "".join(out)

        def _m_matches(a):
            m_obj = a[0] if a and isinstance(a[0], JObject) else None
            if m_obj is None:
                return 0
            rx = m_obj.fields.get("__rx") or _compile_rx("")
            txt = m_obj.fields.get("__text", "")
            rs = int(m_obj.fields.get("__reg_start", 0))
            re_ = int(m_obj.fields.get("__reg_end", len(txt)))
            res = rx.fullmatch(txt, rs, re_)
            m_obj.fields["__match"] = res
            return 1 if res is not None else 0

        def _m_looking_at(a):
            m_obj = a[0] if a and isinstance(a[0], JObject) else None
            if m_obj is None:
                return 0
            rx = m_obj.fields.get("__rx") or _compile_rx("")
            txt = m_obj.fields.get("__text", "")
            rs = int(m_obj.fields.get("__reg_start", 0))
            re_ = int(m_obj.fields.get("__reg_end", len(txt)))
            res = rx.match(txt, rs, re_)
            m_obj.fields["__match"] = res
            return 1 if res is not None else 0

        def _m_find(a):
            m_obj = a[0] if a and isinstance(a[0], JObject) else None
            if m_obj is None:
                return 0
            rx = m_obj.fields.get("__rx") or _compile_rx("")
            txt = m_obj.fields.get("__text", "")
            rs = int(m_obj.fields.get("__reg_start", 0))
            re_ = int(m_obj.fields.get("__reg_end", len(txt)))
            if len(a) > 1 and isinstance(a[1], int):
                pos = max(0, int(a[1]))
                rs = 0
                re_ = len(txt)
                m_obj.fields["__reg_start"] = rs
                m_obj.fields["__reg_end"] = re_
            else:
                pos = int(m_obj.fields.get("__pos", rs))
            if pos > re_:
                m_obj.fields["__match"] = None
                return 0
            res = rx.search(txt, max(rs, pos), re_)
            m_obj.fields["__match"] = res
            if res is None:
                m_obj.fields["__pos"] = re_ + 1
                return 0
            m_obj.fields["__pos"] = res.end() + (1 if res.end() == res.start() else 0)
            return 1

        def _m_res(a):
            m_obj = a[0] if a and isinstance(a[0], JObject) else None
            return m_obj.fields.get("__match") if m_obj is not None else None

        def _m_replace(a, count=0):
            m_obj = a[0] if a and isinstance(a[0], JObject) else None
            if m_obj is None:
                return JString("")
            rx = m_obj.fields.get("__rx") or _compile_rx("")
            txt = m_obj.fields.get("__text", "")
            repl = (self._txt(a[1]) or "") if len(a) > 1 else ""
            py_repl = _to_py_repl(repl)
            try:
                out = rx.sub(py_repl, txt, count=count)
            except Exception:
                out = rx.sub(lambda _m: repl, txt, count=count)
            return JString(out)

        R(PAT, "compile", "(Ljava/lang/String;)Ljava/util/regex/Pattern;",
          lambda a: _make_pattern(self._txt(a[0]) if a else "", 0))
        R(PAT, "compile", "(Ljava/lang/String;I)Ljava/util/regex/Pattern;",
          lambda a: _make_pattern(self._txt(a[0]) if a else "", int(a[1] or 0) if len(a) > 1 else 0))
        R(PAT, "matches", "(Ljava/lang/String;Ljava/lang/CharSequence;)Z",
          lambda a: 1 if (_compile_rx(self._txt(a[0]) if a else "").fullmatch(
              (self._txt(a[1]) or "") if len(a) > 1 else ""
          ) is not None) else 0)
        R(PAT, "quote", "(Ljava/lang/String;)Ljava/lang/String;",
          lambda a: JString("\\Q" + ((self._txt(a[0]) or "") if a else "").replace("\\E", "\\E\\\\E\\Q") + "\\E"))
        R(PAT, "pattern", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("__pat", "") if a and isinstance(a[0], JObject) else ""))
        R(PAT, "toString", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("__pat", "") if a and isinstance(a[0], JObject) else ""))
        R(PAT, "flags", "()I",
          lambda a: int(a[0].fields.get("__flags", 0)) if a and isinstance(a[0], JObject) else 0)
        R(PAT, "matcher", "(Ljava/lang/CharSequence;)Ljava/util/regex/Matcher;",
          lambda a: _make_matcher(a[0] if a else None, a[1] if len(a) > 1 else None))
        R(PAT, "split", "(Ljava/lang/CharSequence;)[Ljava/lang/String;",
          lambda a: _java_split(a[0] if a else None, a[1] if len(a) > 1 else None, 0))
        R(PAT, "split", "(Ljava/lang/CharSequence;I)[Ljava/lang/String;",
          lambda a: _java_split(a[0] if a else None, a[1] if len(a) > 1 else None, int(a[2] or 0) if len(a) > 2 else 0))

        R(MAT, "matches", "()Z", _m_matches)
        R(MAT, "lookingAt", "()Z", _m_looking_at)
        R(MAT, "find", "()Z", _m_find)
        R(MAT, "find", "(I)Z", _m_find)
        R(MAT, "replaceAll", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: _m_replace(a, count=0))
        R(MAT, "replaceFirst", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: _m_replace(a, count=1))
        R(MAT, "region", "(II)Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__reg_start", max(0, int(a[1]))),
                     a[0].fields.__setitem__("__reg_end", max(0, int(a[2]))),
                     a[0].fields.__setitem__("__pos", max(0, int(a[1]))),
                     a[0].fields.__setitem__("__match", None),
                     a[0])[-1] if a and isinstance(a[0], JObject) and len(a) > 2 else (a[0] if a else None))
        R(MAT, "regionStart", "()I",
          lambda a: int(a[0].fields.get("__reg_start", 0)) if a and isinstance(a[0], JObject) else 0)
        R(MAT, "regionEnd", "()I",
          lambda a: int(a[0].fields.get("__reg_end", 0)) if a and isinstance(a[0], JObject) else 0)
        R(MAT, "reset", "()Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__reg_start", 0),
                     a[0].fields.__setitem__("__reg_end", len(a[0].fields.get("__text", ""))),
                     a[0].fields.__setitem__("__pos", 0),
                     a[0].fields.__setitem__("__match", None),
                     a[0])[-1] if a and isinstance(a[0], JObject) else None)
        R(MAT, "reset", "(Ljava/lang/CharSequence;)Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__text", (self._txt(a[1]) or "") if len(a) > 1 else ""),
                     a[0].fields.__setitem__("__reg_start", 0),
                     a[0].fields.__setitem__("__reg_end", len(a[0].fields.get("__text", ""))),
                     a[0].fields.__setitem__("__pos", 0),
                     a[0].fields.__setitem__("__match", None),
                     a[0])[-1] if a and isinstance(a[0], JObject) else None)
        R(MAT, "usePattern", "(Ljava/util/regex/Pattern;)Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__pat", a[1] if len(a) > 1 else None),
                     a[0].fields.__setitem__("__rx", _pat_rx(a[1] if len(a) > 1 else None)),
                     a[0])[-1] if a and isinstance(a[0], JObject) else None)
        R(MAT, "useAnchoringBounds", "(Z)Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__anchoring", bool(a[1]) if len(a) > 1 else True), a[0])[-1] if a and isinstance(a[0], JObject) else None)
        R(MAT, "useTransparentBounds", "(Z)Ljava/util/regex/Matcher;",
          lambda a: (a[0].fields.__setitem__("__transparent", bool(a[1]) if len(a) > 1 else False), a[0])[-1] if a and isinstance(a[0], JObject) else None)
        R(MAT, "hasAnchoringBounds", "()Z",
          lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("__anchoring", True)) else 0)
        R(MAT, "hasTransparentBounds", "()Z",
          lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("__transparent", False)) else 0)
        R(MAT, "hitEnd", "()Z", lambda a: 0)
        R(MAT, "requireEnd", "()Z", lambda a: 0)
        R(MAT, "pattern", "()Ljava/util/regex/Pattern;",
          lambda a: a[0].fields.get("__pat") if a and isinstance(a[0], JObject) else None)
        R(MAT, "toMatchResult", "()Ljava/util/regex/MatchResult;",
          lambda a: a[0] if a else None)

        for _cls_mr in (MAT, MR):
            R(_cls_mr, "start", "()I", lambda a: (_m_res(a).start() if _m_res(a) is not None else -1))
            R(_cls_mr, "start", "(I)I", lambda a: (_m_res(a).start(int(a[1])) if _m_res(a) is not None and len(a) > 1 else -1))
            R(_cls_mr, "end", "()I", lambda a: (_m_res(a).end() if _m_res(a) is not None else -1))
            R(_cls_mr, "end", "(I)I", lambda a: (_m_res(a).end(int(a[1])) if _m_res(a) is not None and len(a) > 1 else -1))
            R(_cls_mr, "group", "()Ljava/lang/String;", lambda a: JString(_m_res(a).group(0)) if _m_res(a) is not None else None)
            R(_cls_mr, "group", "(I)Ljava/lang/String;",
              lambda a: (lambda g: JString(g) if g is not None else None)(_m_res(a).group(int(a[1]))) if _m_res(a) is not None and len(a) > 1 else None)
            R(_cls_mr, "groupCount", "()I",
              lambda a: int(a[0].fields.get("__rx").groups) if a and isinstance(a[0], JObject) and a[0].fields.get("__rx") is not None else 0)

    def _install_glass_overrides(self):
        """AP1.3/AP2.1: VM-Methoden-Overrides fuer Glass-Box. SoLoader/NativeLoader.loadLibrary laufen
        ueber _so_load_library: im Fusions-Modus echte .so-Ladung (ueber vm._fusion_load_library),
        sonst No-Op-Erfolg. init() bleibt ein No-Op (keine echte Base-APK -> NoBaseApkException vermeiden)."""
        vm = self.vm
        if vm is None or not hasattr(vm, "method_overrides"):
            return
        SO = "Lcom/facebook/soloader/SoLoader;"
        vm.method_overrides.update({
            (SO, "loadLibrary", "(Ljava/lang/String;)Z"): self._so_load_library,
            (SO, "loadLibrary", "(Ljava/lang/String;I)Z"): self._so_load_library,
            (SO, "init", "(Landroid/content/Context;I)V"): None,
            (SO, "init", "(Landroid/content/Context;ILcom/facebook/soloader/SoFileLoader;)V"): None,
            ("Lcom/facebook/soloader/nativeloader/NativeLoader;", "loadLibrary",
             "(Ljava/lang/String;)Z"): self._so_load_library,
        })

    def _install_android_sdk(self):
        """Spec-treue Standard-Android/JDK-Methoden mit REALEN Nicht-Null-Werten.
        Ohne diese fallen Umgebungssonden stumm auf 0 -> z.B. SoLoader `0 - 34 = -34`
        (NegativeArraySizeException) statt `100 - 34 = 66`."""
        R = self.register
        # --- ViewConfiguration: statische Timeouts/Groessen (reale Android-Defaults) ---
        VC = "Landroid/view/ViewConfiguration;"
        for name, val in (("getTapTimeout", 100), ("getJumpTapTimeout", 500),
                          ("getPressedStateDuration", 64), ("getKeyRepeatDelay", 50),
                          ("getKeyRepeatTimeout", 400), ("getScrollDefaultDelay", 250),
                          ("getScrollBarSize", 10), ("getScrollBarFadeDuration", 250),
                          ("getScrollDefaultDelay", 250), ("getFadingEdgeLength", 12),
                          ("getWindowTouchSlop", 16), ("getTouchSlop", 8),
                          ("getDoubleTapTimeout", 300), ("getLongPressTimeout", 500),
                          ("getEdgeSlop", 12), ("getMaximumDrawingCacheSize", 480000),
                          ("getMaximumFlingVelocity", 8000), ("getMinimumFlingVelocity", 50),
                          ("getGlobalActionKeyTimeout", 500)):
            R(VC, name, "()I", (lambda v: (lambda a: v))(val))
        R(VC, "getZoomControlsTimeout", "()J", lambda a: 3000)
        R(VC, "getScrollFriction", "()F", lambda a: 0.015)
        # --- SystemClock: monotone, positive Zeit ---
        SC = "Landroid/os/SystemClock;"
        R(SC, "uptimeMillis", "()J", lambda a: self._tick_ms(100))
        R(SC, "elapsedRealtime", "()J", lambda a: self._tick_ms(100))
        R(SC, "elapsedRealtimeNanos", "()J", lambda a: self._tick_ms(100) * 1000000)
        R(SC, "currentThreadTimeMillis", "()J", lambda a: 1)
        # --- Process: feste, plausible IDs ---
        PR = "Landroid/os/Process;"
        R(PR, "myPid", "()I", lambda a: 12345)
        R(PR, "myTid", "()I", lambda a: 12345)
        R(PR, "myUid", "()I", lambda a: 10123)
        R(PR, "is64Bit", "()Z", lambda a: 1)
        R(PR, "getGidForName", "(Ljava/lang/String;)I", lambda a: -1)
        R(PR, "getUidForName", "(Ljava/lang/String;)I", lambda a: -1)
        # --- TextUtils: echte String-Suche + geklemmte Cursor-Offsets ---
        TU = "Landroid/text/TextUtils;"
        for sig in ("(Ljava/lang/CharSequence;CI)I", "(Ljava/lang/CharSequence;CII)I",
                    "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;I)I",
                    "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;II)I",
                    "(Ljava/lang/CharSequence;C)I"):
            def _tu_idx(a, _sig=sig):
                s = self._txt(a[0]) or "" if a else ""
                needle = (chr(int(a[1]) & 0xFFFF) if "(Ljava/lang/CharSequence;C" in _sig
                          else (self._txt(a[1]) or "")) if len(a) > 1 else ""
                start = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
                end = int(a[3]) if len(a) > 3 and isinstance(a[3], int) else len(s)
                if not needle and "(Ljava/lang/CharSequence;C" not in _sig:
                    return start if 0 <= start <= end else -1
                return s.find(needle, max(0, start), min(len(s), end))
            R(TU, "indexOf", sig, _tu_idx)
        for sig in ("(Ljava/lang/CharSequence;C)I", "(Ljava/lang/CharSequence;CII)I",
                    "(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)I"):
            def _tu_ridx(a, _sig=sig):
                s = self._txt(a[0]) or "" if a else ""
                needle = (chr(int(a[1]) & 0xFFFF) if "(Ljava/lang/CharSequence;C" in _sig
                          else (self._txt(a[1]) or "")) if len(a) > 1 else ""
                start = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else len(s)
                end = int(a[3]) if len(a) > 3 and isinstance(a[3], int) else None
                if end is not None:
                    return s.rfind(needle, max(0, start), min(len(s), end))
                return s.rfind(needle, 0, min(len(s), start + 1)) if len(a) > 2 else s.rfind(needle)
            R(TU, "lastIndexOf", sig, _tu_ridx)
        R(TU, "getOffsetAfter", "(Ljava/lang/CharSequence;I)I",
          lambda a: min(len(self._txt(a[0]) or ""), max(0, int(a[1]) + 1))
          if len(a) > 1 and isinstance(a[1], int) else 0)
        R(TU, "getOffsetBefore", "(Ljava/lang/CharSequence;I)I",
          lambda a: max(0, min(len(self._txt(a[0]) or ""), int(a[1])) - 1)
          if len(a) > 1 and isinstance(a[1], int) else 0)
        R(TU, "getCapsMode", "(Ljava/lang/CharSequence;II)I", lambda a: 0)
        R(TU, "isEmpty", "(Ljava/lang/CharSequence;)Z",
          lambda a: 1 if (not a or not self._txt(a[0])) else 0)
        # --- Color: echte Bit-Arithmetik ---
        CO = "Landroid/graphics/Color;"
        R(CO, "argb", "(IIII)I", lambda a: ((int(a[0]) & 0xff) << 24) | ((int(a[1]) & 0xff) << 16)
                                           | ((int(a[2]) & 0xff) << 8) | (int(a[3]) & 0xff) if len(a) >= 4 else 0)
        R(CO, "rgb", "(III)I", lambda a: (0xff << 24) | ((int(a[0]) & 0xff) << 16)
                                        | ((int(a[1]) & 0xff) << 8) | (int(a[2]) & 0xff) if len(a) >= 3 else 0)
        R(CO, "alpha", "(I)I", lambda a: (int(a[0]) >> 24) & 0xff if a else 0)
        R(CO, "red", "(I)I", lambda a: (int(a[0]) >> 16) & 0xff if a else 0)
        R(CO, "green", "(I)I", lambda a: (int(a[0]) >> 8) & 0xff if a else 0)
        R(CO, "blue", "(I)I", lambda a: int(a[0]) & 0xff if a else 0)
        # --- View.MeasureSpec ---
        MS = "Landroid/view/View$MeasureSpec;"
        R(MS, "makeMeasureSpec", "(II)I",
          lambda a: ((int(a[0]) & 0x3fffffff) | (int(a[1]) & 0xc0000000)) & 0xffffffff if len(a) > 1 else 0)
        R(MS, "getSize", "(I)I", lambda a: int(a[0]) & 0x3fffffff if a else 0)
        R(MS, "getMode", "(I)I", lambda a: int(a[0]) & 0xc0000000 if a else 0)
        R("Landroid/view/View;", "getDefaultSize", "(II)I",
          lambda a: (int(a[1]) & 0x3fffffff) if (len(a) > 1 and (int(a[1]) & 0xc0000000)) else (int(a[0]) if a else 0))
        # --- KeyEvent / Gravity / sonstige kleine ---
        KE = "Landroid/view/KeyEvent;"
        R(KE, "getMaxKeyCode", "()I", lambda a: 289)
        R(KE, "keyCodeFromString", "(Ljava/lang/String;)I", lambda a: 0)
        R(KE, "normalizeMetaState", "(I)I", lambda a: int(a[0]) if a else 0)
        R(KE, "getDeadChar", "(II)I", lambda a: 0)
        R("Landroid/view/Gravity;", "getAbsoluteGravity", "(II)I", lambda a: int(a[0]) if a else 0)
        R("Landroid/text/AndroidCharacter;", "getMirror", "(C)C", lambda a: int(a[0]) if a else 0)
        R("Landroid/util/Log;", "isLoggable", "(Ljava/lang/String;I)Z", lambda a: 0)
        R("Landroid/util/TypedValue;", "complexToFraction", "(IFF)F", lambda a: 0.0)
        R("Landroid/os/Process;", "setThreadPriority", "(I)V", lambda a: None)
        # MotionEvent axis
        R("Landroid/view/MotionEvent;", "axisFromString", "(Ljava/lang/String;)I", lambda a: -1)
        R("Landroid/view/MotionEvent;", "axisToString", "(I)Ljava/lang/String;", lambda a: JString(""))
        # --- Looper/Handler: Main-Thread-Singleton (FragmentManager-Thread-Checks) ---
        self._looper_main = JObject("Landroid/os/Looper;", {})
        self._looper_thread = JObject("Ljava/lang/Thread;", {"name": "main"})
        self._looper_main.fields["thread"] = self._looper_thread
        R("Landroid/os/Looper;", "getMainLooper", "()Landroid/os/Looper;", lambda a: self._looper_main)
        R("Landroid/os/Looper;", "myLooper", "()Landroid/os/Looper;", lambda a: self._looper_main)
        R("Landroid/os/Looper;", "getThread", "()Ljava/lang/Thread;", lambda a: self._looper_thread)
        R("Ljava/lang/Thread;", "currentThread", "()Ljava/lang/Thread;", lambda a: self._looper_thread)
        R("Ljava/lang/Thread;", "getName", "()Ljava/lang/String;",
          lambda a: JString(a[0].fields.get("name", "main")) if a and isinstance(a[0], JObject) else JString("main"))
        R("Ljava/lang/Thread;", "getId", "()J", lambda a: 1)
        R("Ljava/lang/Thread;", "isInterrupted", "()Z", lambda a: 0)
        R("Ljava/lang/Thread;", "setName", "(Ljava/lang/String;)V", lambda a: None)
        R("Landroid/os/Looper;", "isCurrentThread", "()Z", lambda a: 1)
        R("Landroid/os/Looper;", "prepare", "()V", lambda a: None)
        R("Landroid/os/Looper;", "prepareMainLooper", "()V", lambda a: None)
        R("Landroid/os/Looper;", "loop", "()V", lambda a: None)
        R("Landroid/os/Looper;", "quit", "()V", lambda a: None)
        R("Landroid/os/Looper;", "quitSafely", "()V", lambda a: None)
        R("Landroid/os/Handler;", "getLooper", "()Landroid/os/Looper;", lambda a: self._looper_main)
        for _hsig in ("()V", "(Landroid/os/Looper;)V", "(Landroid/os/Handler$Callback;)V",
                      "(Landroid/os/Looper;Landroid/os/Handler$Callback;)V"):
            R("Landroid/os/Handler;", "<init>", _hsig, lambda a: None)
        R("Landroid/os/Handler;", "post", "(Ljava/lang/Runnable;)Z",
          lambda a: self._queue_runnable(a[1] if len(a) > 1 else None, 0))
        R("Landroid/os/Handler;", "postDelayed", "(Ljava/lang/Runnable;J)Z",
          lambda a: self._queue_runnable(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0))
        R("Landroid/os/Handler;", "postAtFrontOfQueue", "(Ljava/lang/Runnable;)Z",
          lambda a: self._queue_runnable(a[1] if len(a) > 1 else None, 0))
        self.register_by_name("post", "(Ljava/lang/Runnable;)Z",
                              lambda a: self._queue_runnable(a[1] if len(a) > 1 else None, 0))
        self.register_by_name("postDelayed", "(Ljava/lang/Runnable;J)Z",
                              lambda a: self._queue_runnable(a[1] if len(a) > 1 else None, a[2] if len(a) > 2 else 0))
        for _ex_cls in ("Ljava/util/concurrent/Executor;", "Ljava/util/concurrent/ExecutorService;",
                        "Ljava/util/concurrent/ThreadPoolExecutor;",
                        "Ljava/util/concurrent/ScheduledThreadPoolExecutor;"):
            R(_ex_cls, "execute", "(Ljava/lang/Runnable;)V",
              lambda a: (self._queue_runnable(a[1] if len(a) > 1 else None, 0), None)[1])
            R(_ex_cls, "submit", "(Ljava/lang/Runnable;)Ljava/util/concurrent/Future;",
              lambda a: (self._queue_runnable(a[1] if len(a) > 1 else None, 0),
                         JObject("Ljava/util/concurrent/Future;", {}))[1])
        self.register_by_name("execute", "(Ljava/lang/Runnable;)V",
                              lambda a: (self._queue_runnable(a[1] if len(a) > 1 else None, 0), None)[1])
        def _remove_callbacks(a):
            r = a[1] if len(a) > 1 else None
            tok = a[2] if len(a) > 2 else None
            if r is not None:
                kept = []
                for x in self.pending_runnables:
                    if x is r:
                        continue
                    if (
                        isinstance(x, JObject)
                        and x.cls == "Landroid/os/Handler$MessageDispatchRunnable;"
                        and isinstance(x.fields.get("_msg"), JObject)
                        and x.fields["_msg"].fields.get("callback") is r
                        and (tok is None or x.fields["_msg"].fields.get("obj") is tok)
                    ):
                        x.fields["_msg"].fields["_cancelled"] = True
                        continue
                    kept.append(x)
                self.pending_runnables[:] = kept

        R("Landroid/os/Handler;", "removeCallbacks", "(Ljava/lang/Runnable;)V", _remove_callbacks)
        R("Landroid/os/Handler;", "removeCallbacks", "(Ljava/lang/Runnable;Ljava/lang/Object;)V", _remove_callbacks)
        R("Landroid/os/Handler;", "removeCallbacksAndMessages", "(Ljava/lang/Object;)V", lambda a: None)
        R("Landroid/os/Handler;", "sendMessage", "(Landroid/os/Message;)Z", lambda a: 1)
        R("Landroid/os/Handler;", "sendEmptyMessage", "(I)Z", lambda a: 1)
        R("Landroid/os/Handler;", "hasMessages", "(I)Z", lambda a: 0)
        def _mk_msg(a):
            what = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
            arg1 = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
            arg2 = int(a[3]) if len(a) > 3 and isinstance(a[3], int) else 0
            obj = a[4] if len(a) > 4 else (a[2] if len(a) == 3 and not isinstance(a[2], int) else None)
            return JObject("Landroid/os/Message;", {"what": what, "arg1": arg1, "arg2": arg2, "obj": obj, "target": a[0] if a else None})
        for _msig in ("()Landroid/os/Message;", "(I)Landroid/os/Message;", "(ILjava/lang/Object;)Landroid/os/Message;",
                      "(III)Landroid/os/Message;", "(IIILjava/lang/Object;)Landroid/os/Message;"):
            R("Landroid/os/Handler;", "obtainMessage", _msig, _mk_msg)
            R("Landroid/os/Message;", "obtain", _msig, _mk_msg)
        R("Landroid/os/Message;", "<init>", "()V", lambda a: None)
        R("Landroid/os/Message;", "obtain", "()Landroid/os/Message;", _mk_msg)
        R("Landroid/os/Message;", "recycle", "()V", lambda a: None)
        R("Landroid/os/Message;", "sendToTarget", "()V", lambda a: None)

        # --- ReferenceQueue (stoppt Cleaner/WeakHashMap-Daemon-Schleifen sauber) ---
        RQ = "Ljava/lang/ref/ReferenceQueue;"
        R(RQ, "<init>", "()V", lambda a: None)
        R(RQ, "poll", "()Ljava/lang/ref/Reference;", lambda a: None)
        def _rq_remove(a):
            cnt = 3
            if a and isinstance(a[0], JObject):
                cnt = int(a[0].fields.get("_polls", 0)) + 1
                a[0].fields["_polls"] = cnt
                if cnt <= 1:
                    return None
            from core.java.dalvik import DalvikException
            if cnt == 2:
                raise DalvikException("java.lang.InterruptedException",
                                      payload=JObject("Ljava/lang/InterruptedException;", {"message": "interrupted"}))
            # Nach einmaligem InterruptedException: viele Daemon-Schleifen (z.B. Glide ActiveResources)
            # fangen InterruptedException in `while (!isShutdown)` ab. ThreadDeath beendet sie deterministisch.
            raise DalvikException("java.lang.ThreadDeath",
                                  payload=JObject("Ljava/lang/ThreadDeath;", {"message": "daemon queue parked"}))
        R(RQ, "remove", "()Ljava/lang/ref/Reference;", _rq_remove)
        R(RQ, "remove", "(J)Ljava/lang/ref/Reference;", _rq_remove)

        # --- SpannableString / SpannableStringBuilder ---
        def _sp_init(a):
            if a and isinstance(a[0], JObject):
                s = (self._txt(a[1]) or "") if len(a) > 1 else ""
                if len(a) > 3 and isinstance(a[2], int) and isinstance(a[3], int):
                    s = s[max(0, int(a[2])):max(0, int(a[3]))]
                a[0].fields["$text"] = s
            return a[0] if a else None
        for _scls in ("Landroid/text/SpannableString;", "Landroid/text/SpannableStringBuilder;"):
            R(_scls, "<init>", "()V", _sp_init)
            R(_scls, "<init>", "(Ljava/lang/CharSequence;)V", _sp_init)
            R(_scls, "<init>", "(Ljava/lang/CharSequence;II)V", _sp_init)
            R(_scls, "setSpan", "(Ljava/lang/Object;III)V", lambda a: None)
            R(_scls, "removeSpan", "(Ljava/lang/Object;)V", lambda a: None)
            R(_scls, "length", "()I", lambda a: len(self._txt(a[0]) or "") if a else 0)
            R(_scls, "charAt", "(I)C", lambda a: (lambda t: ord(t[int(a[1])]) if 0 <= int(a[1]) < len(t) else 0)(self._txt(a[0]) or ""))
            R(_scls, "subSequence", "(II)Ljava/lang/CharSequence;",
              lambda a: JString((self._txt(a[0]) or "")[max(0, int(a[1])):max(0, int(a[2]))]) if len(a) > 2 else JString(""))
            R(_scls, "toString", "()Ljava/lang/String;", lambda a: JString((self._txt(a[0]) or "") if a else ""))
        R("Landroid/text/SpannableString;", "valueOf", "(Ljava/lang/CharSequence;)Landroid/text/SpannableString;",
          lambda a: JObject("Landroid/text/SpannableString;", {"$text": (self._txt(a[0]) or "") if a else ""}))
        for _asig in ("(Ljava/lang/CharSequence;)Landroid/text/SpannableStringBuilder;",
                      "(Ljava/lang/String;)Landroid/text/SpannableStringBuilder;"):
            R("Landroid/text/SpannableStringBuilder;", "append", _asig,
              lambda a: (a[0].fields.__setitem__("$text", (a[0].fields.get("$text") or "") + (self._txt(a[1]) or "")), a[0])[1] if a and isinstance(a[0], JObject) else None)

        def _sp_replace(a):
            if not (a and isinstance(a[0], JObject)):
                return a[0] if a else None
            cur = str(a[0].fields.get("$text") or "")
            st = max(0, min(len(cur), int(a[1] or 0))) if len(a) > 1 else 0
            en = max(st, min(len(cur), int(a[2] or 0))) if len(a) > 2 else st
            ins = (self._txt(a[3]) or "") if len(a) > 3 else ""
            if len(a) > 5:
                s0 = max(0, min(len(ins), int(a[4] or 0)))
                e0 = max(s0, min(len(ins), int(a[5] or 0)))
                ins = ins[s0:e0]
            new_s = cur[:st] + ins + cur[en:]
            a[0].fields["$text"] = new_s
            nd = a[0].fields.get("$node")
            if nd is not None and hasattr(nd, "text"):
                nd.text = new_s
            return a[0]

        for _rcls in ("Landroid/text/SpannableStringBuilder;", "Landroid/text/Editable;"):
            R(_rcls, "replace", "(IILjava/lang/CharSequence;)Landroid/text/Editable;", _sp_replace)
            R(_rcls, "replace", "(IILjava/lang/CharSequence;)Landroid/text/SpannableStringBuilder;", _sp_replace)
            R(_rcls, "replace", "(IILjava/lang/CharSequence;II)Landroid/text/Editable;", _sp_replace)
            R(_rcls, "replace", "(IILjava/lang/CharSequence;II)Landroid/text/SpannableStringBuilder;", _sp_replace)

        # --- ColorStateList & XmlPullParser (verhindert Endlos-Loops bei XML-Inflate) ---
        CSL = "Landroid/content/res/ColorStateList;"
        R(CSL, "valueOf", "(I)Landroid/content/res/ColorStateList;",
          lambda a: JObject(CSL, {"color": int(a[0]) if a else 0xFF2F6DF0}))
        R(CSL, "getDefaultColor", "()I",
          lambda a: int(a[0].fields.get("color", 0xFF2F6DF0)) if a and isinstance(a[0], JObject) else 0xFF2F6DF0)
        R(CSL, "getColorForState", "([II)I",
          lambda a: int(a[0].fields.get("color", a[2] if len(a) > 2 else 0xFF2F6DF0)) if a and isinstance(a[0], JObject) else 0xFF2F6DF0)
        R(CSL, "withAlpha", "(I)Landroid/content/res/ColorStateList;",
          lambda a: a[0] if a and isinstance(a[0], JObject) else JObject(CSL, {"color": 0xFF2F6DF0}))
        R(CSL, "isStateful", "()Z", lambda a: 0)

        for _xcls in ("Lorg/xmlpull/v1/XmlPullParser;", "Landroid/content/res/XmlResourceParser;"):
            R(_xcls, "next", "()I", lambda a: 1)
            R(_xcls, "nextToken", "()I", lambda a: 1)
            R(_xcls, "getEventType", "()I", lambda a: 1)
            R(_xcls, "getAttributeCount", "()I", lambda a: 0)
            R(_xcls, "getDepth", "()I", lambda a: 0)
            R(_xcls, "getName", "()Ljava/lang/String;", lambda a: None)
            R(_xcls, "getNamespace", "()Ljava/lang/String;", lambda a: JString(""))
            R(_xcls, "getPositionDescription", "()Ljava/lang/String;", lambda a: JString("xml:1"))
        # --- Activity-Lifecycle-Callbacks (echt registrieren) ---
        R("Landroid/app/Application;", "registerActivityLifecycleCallbacks",
          "(Landroid/app/Application$ActivityLifecycleCallbacks;)V", self._reg_act_cb)
        R("Landroid/app/Application;", "unregisterActivityLifecycleCallbacks",
          "(Landroid/app/Application$ActivityLifecycleCallbacks;)V", self._unreg_act_cb)
        self._install_net_thread_camera_media_stubs()

    def _install_net_thread_camera_media_stubs(self):
        """Connectivity/WiFi, Thread/Executors/ThreadLocal sowie sichere Kamera-/MediaPlayer-Stubs."""
        R = self.register
        RB = self.register_by_name

        def _is_offline():
            nc = getattr(self, "net_condition", None)
            return getattr(nc, "mode", "online") == "offline"

        def _net_info(a=None):
            if _is_offline():
                return None
            return JObject("Landroid/net/NetworkInfo;", {"connected": 1, "type": 1})

        def _act_net(a=None):
            if _is_offline():
                return None
            return JObject("Landroid/net/Network;", {})

        def _net_caps(a=None):
            if _is_offline() or (len(a or ()) > 1 and a[1] is None):
                return None
            return JObject("Landroid/net/NetworkCapabilities;", {})

        def _wifi_info(a=None):
            return JObject("Landroid/net/wifi/WifiInfo;", {})

        for _ccls in ("Landroid/net/ConnectivityManager;", "Landroid/os/SystemService;"):
            R(_ccls, "getActiveNetworkInfo", "()Landroid/net/NetworkInfo;", _net_info)
            R(_ccls, "getNetworkInfo", "(I)Landroid/net/NetworkInfo;", _net_info)
            R(_ccls, "getActiveNetwork", "()Landroid/net/Network;", _act_net)
            R(_ccls, "getNetworkCapabilities",
              "(Landroid/net/Network;)Landroid/net/NetworkCapabilities;", _net_caps)
        RB("getActiveNetworkInfo", "()Landroid/net/NetworkInfo;", _net_info)
        RB("getNetworkInfo", "(I)Landroid/net/NetworkInfo;", _net_info)
        RB("getActiveNetwork", "()Landroid/net/Network;", _act_net)
        RB("getNetworkCapabilities", "(Landroid/net/Network;)Landroid/net/NetworkCapabilities;", _net_caps)

        NI = "Landroid/net/NetworkInfo;"
        R(NI, "isConnected", "()Z", lambda a: 0 if _is_offline() else 1)
        R(NI, "isConnectedOrConnecting", "()Z", lambda a: 0 if _is_offline() else 1)
        R(NI, "isAvailable", "()Z", lambda a: 0 if _is_offline() else 1)
        R(NI, "getType", "()I", lambda a: 1)
        R(NI, "getTypeName", "()Ljava/lang/String;", lambda a: JString("WIFI"))
        NC = "Landroid/net/NetworkCapabilities;"
        R(NC, "hasCapability", "(I)Z", lambda a: 0 if _is_offline() else 1)
        R(NC, "hasTransport", "(I)Z", lambda a: 0 if _is_offline() else 1)

        for _wcls in ("Landroid/net/wifi/WifiManager;", "Landroid/os/SystemService;"):
            R(_wcls, "isWifiEnabled", "()Z", lambda a: 0 if _is_offline() else 1)
            R(_wcls, "getWifiState", "()I", lambda a: 1 if _is_offline() else 3)
            R(_wcls, "getConnectionInfo", "()Landroid/net/wifi/WifiInfo;", _wifi_info)
        RB("isWifiEnabled", "()Z", lambda a: 0 if _is_offline() else 1)
        RB("getWifiState", "()I", lambda a: 1 if _is_offline() else 3)
        RB("getConnectionInfo", "()Landroid/net/wifi/WifiInfo;", _wifi_info)
        WI = "Landroid/net/wifi/WifiInfo;"
        R(WI, "getIpAddress", "()I", lambda a: 0 if _is_offline() else 0x0201A8C0)
        R(WI, "getSSID", "()Ljava/lang/String;", lambda a: JString('"HomeWiFi"'))

        # --- Thread, Executors, ThreadLocal ---
        def _thread_init(a):
            if not a or not isinstance(a[0], JObject):
                return None
            for arg in a[1:]:
                if isinstance(arg, JObject) and arg.cls != "Ljava/lang/ThreadGroup;":
                    a[0].fields["_target"] = arg
                elif isinstance(arg, JString):
                    a[0].fields["name"] = arg.text
            return None

        for _tsig in (
            "()V",
            "(Ljava/lang/String;)V",
            "(Ljava/lang/Runnable;)V",
            "(Ljava/lang/Runnable;Ljava/lang/String;)V",
            "(Ljava/lang/ThreadGroup;Ljava/lang/Runnable;)V",
            "(Ljava/lang/ThreadGroup;Ljava/lang/Runnable;Ljava/lang/String;)V",
            "(Ljava/lang/ThreadGroup;Ljava/lang/Runnable;Ljava/lang/String;J)V",
        ):
            R("Ljava/lang/Thread;", "<init>", _tsig, _thread_init)

        def _thread_start(a):
            if a and isinstance(a[0], JObject):
                tgt = a[0].fields.get("_target") or a[0]
                if isinstance(tgt, JObject):
                    tgt.fields["_bg_thread"] = True
                    self._queue_runnable(tgt, 0)
            return None

        R("Ljava/lang/Thread;", "start", "()V", _thread_start)
        R("Ljava/lang/Thread;", "setPriority", "(I)V", lambda a: None)
        R("Ljava/lang/Thread;", "setDaemon", "(Z)V", lambda a: None)
        R("Ljava/lang/Thread;", "join", "()V", lambda a: None)
        R("Ljava/lang/Thread;", "join", "(J)V", lambda a: None)
        RB("start", "()V", _thread_start)

        EX = "Ljava/util/concurrent/Executors;"
        _mk_es = lambda a: JObject("Ljava/util/concurrent/ExecutorService;", {})
        for _ename, _esig in (
            ("newFixedThreadPool", "(I)Ljava/util/concurrent/ExecutorService;"),
            ("newFixedThreadPool", "(ILjava/util/concurrent/ThreadFactory;)Ljava/util/concurrent/ExecutorService;"),
            ("newSingleThreadExecutor", "()Ljava/util/concurrent/ExecutorService;"),
            ("newSingleThreadExecutor", "(Ljava/util/concurrent/ThreadFactory;)Ljava/util/concurrent/ExecutorService;"),
            ("newCachedThreadPool", "()Ljava/util/concurrent/ExecutorService;"),
            ("newCachedThreadPool", "(Ljava/util/concurrent/ThreadFactory;)Ljava/util/concurrent/ExecutorService;"),
            ("unconfigurableExecutorService", "(Ljava/util/concurrent/ExecutorService;)Ljava/util/concurrent/ExecutorService;"),
            ("newScheduledThreadPool", "(I)Ljava/util/concurrent/ScheduledExecutorService;"),
            ("newScheduledThreadPool", "(ILjava/util/concurrent/ThreadFactory;)Ljava/util/concurrent/ScheduledExecutorService;"),
        ):
            R(EX, _ename, _esig, _mk_es)
        R(EX, "defaultThreadFactory", "()Ljava/util/concurrent/ThreadFactory;",
          lambda a: JObject("Ljava/util/concurrent/ThreadFactory;", {}))
        R("Ljava/util/concurrent/ThreadPoolExecutor;", "allowCoreThreadTimeOut", "(Z)V", lambda a: None)
        for _tpe_sig in (
            "(IIJLjava/util/concurrent/TimeUnit;Ljava/util/concurrent/BlockingQueue;)V",
            "(IIJLjava/util/concurrent/TimeUnit;Ljava/util/concurrent/BlockingQueue;Ljava/util/concurrent/ThreadFactory;)V",
        ):
            R("Ljava/util/concurrent/ThreadPoolExecutor;", "<init>", _tpe_sig, lambda a: None)

        TL = "Ljava/lang/ThreadLocal;"
        R(TL, "<init>", "()V", lambda a: None)
        def _tl_get(a):
            if not (a and isinstance(a[0], JObject)):
                return None
            f = a[0].fields
            if "_val" in f:
                return f["_val"]
            vm = getattr(self, "vm", None)
            sup = f.get("_supplier")
            if isinstance(sup, JObject) and vm is not None:
                md = vm._resolve_method(sup.cls, "Ljava/util/function/Supplier;", "get", "()Ljava/lang/Object;")
                if md is not None:
                    val = vm._run_mdef(md, [sup])
                    f["_val"] = val
                    return val
            if vm is not None and a[0].cls != TL:
                md = vm._resolve_method(a[0].cls, TL, "initialValue", "()Ljava/lang/Object;")
                if md is not None:
                    val = vm._run_mdef(md, [a[0]])
                    f["_val"] = val
                    return val
            return None
        R(TL, "get", "()Ljava/lang/Object;", _tl_get)
        R(TL, "set", "(Ljava/lang/Object;)V",
          lambda a: a[0].fields.__setitem__("_val", a[1]) if (a and len(a) > 1 and isinstance(a[0], JObject)) else None)
        R(TL, "remove", "()V",
          lambda a: a[0].fields.pop("_val", None) if (a and isinstance(a[0], JObject)) else None)
        R(TL, "withInitial", "(Ljava/util/function/Supplier;)Ljava/lang/ThreadLocal;",
          lambda a: JObject(TL, {"_supplier": a[0] if a else None}))

        # --- dalvik.system.DexFile (z.B. fuer Router-/Plugin-Klassen-Scans) ---
        DXF = "Ldalvik/system/DexFile;"
        R(DXF, "<init>", "(Ljava/lang/String;)V",
          lambda a: a[0].fields.__setitem__("_path", self._txt(a[1]) if len(a) > 1 else "") if a and isinstance(a[0], JObject) else None)
        R(DXF, "<init>", "(Ljava/io/File;)V",
          lambda a: a[0].fields.__setitem__("_path", (a[1].fields.get("path", "") if len(a) > 1 and isinstance(a[1], JObject) else "")) if a and isinstance(a[0], JObject) else None)
        R(DXF, "loadDex", "(Ljava/lang/String;Ljava/lang/String;I)Ldalvik/system/DexFile;",
          lambda a: JObject(DXF, {"_path": self._txt(a[0]) if a else ""}))
        R(DXF, "close", "()V", lambda a: None)
        R(DXF, "loadClass", "(Ljava/lang/String;Ljava/lang/ClassLoader;)Ljava/lang/Class;",
          lambda a: self._cls_forname([a[1] if len(a) > 1 else None]))

        def _dxf_entries(a):
            vm = getattr(self, "vm", None)
            prov = getattr(vm, "class_provider", None) if vm is not None else None
            descs = prov.all_class_descriptors() if (prov is not None and hasattr(prov, "all_class_descriptors")) else ()
            prefixes = set()
            if vm is not None and len(descs) > 3000:
                for fr in list(getattr(vm, "_frames", ()) or ())[-6:]:
                    vals = list(getattr(fr, "v", ()) or ())
                    if vals and isinstance(vals[0], JObject):
                        vals.extend(vals[0].fields.values())
                    for val in vals:
                        if isinstance(val, JString) and val.text:
                            t = val.text.strip()
                            if (
                                "." in t and not t.startswith("/")
                                and not t.endswith((".apk", ".zip", ".dex", ".tmp", ".so"))
                                and " " not in t
                            ):
                                parts = [p for p in t.split(".") if p]
                                if parts and all(p.replace("_", "").replace("$", "").isalnum() for p in parts):
                                    prefixes.add(t)
            items = []
            for d in descs:
                if not (isinstance(d, str) and d.startswith("L") and d.endswith(";")):
                    continue
                dotted = d[1:-1].replace("/", ".")
                if prefixes:
                    if any(dotted.startswith(px) for px in prefixes):
                        items.append(JString(dotted))
                elif len(descs) <= 3000:
                    items.append(JString(dotted))
                else:
                    # R8/ProGuard-Kurzpakete (1-2 Zeichen) ueberspringen, echte Bibliotheks-/Routenpakete behalten
                    head = dotted.split(".", 1)[0]
                    if len(head) >= 3:
                        items.append(JString(dotted))
            return JObject("Ljava/util/Enumeration;", {"__items": items, "__idx": 0})

        R(DXF, "entries", "()Ljava/util/Enumeration;", _dxf_entries)

        # --- Kamera & MediaPlayer (nicht-abstuerzende Hardware-Stubs) ---
        CAM = "Landroid/hardware/Camera;"
        R(CAM, "getNumberOfCameras", "()I", lambda a: 1)
        R(CAM, "open", "()Landroid/hardware/Camera;", lambda a: JObject(CAM, {}))
        R(CAM, "open", "(I)Landroid/hardware/Camera;", lambda a: JObject(CAM, {}))
        R(CAM, "getParameters", "()Landroid/hardware/Camera$Parameters;",
          lambda a: JObject("Landroid/hardware/Camera$Parameters;", {}))
        for _cm, _cs in (
            ("setParameters", "(Landroid/hardware/Camera$Parameters;)V"),
            ("setPreviewDisplay", "(Landroid/view/SurfaceHolder;)V"),
            ("setPreviewTexture", "(Landroid/graphics/SurfaceTexture;)V"),
            ("setPreviewCallback", "(Landroid/hardware/Camera$PreviewCallback;)V"),
            ("setPreviewCallbackWithBuffer", "(Landroid/hardware/Camera$PreviewCallback;)V"),
            ("startPreview", "()V"),
            ("stopPreview", "()V"),
            ("release", "()V"),
            ("setDisplayOrientation", "(I)V"),
        ):
            R(CAM, _cm, _cs, lambda a: None)
        for _cm2 in ("Landroid/hardware/camera2/CameraManager;", "Landroid/os/SystemService;"):
            R(_cm2, "getCameraIdList", "()[Ljava/lang/String;",
              lambda a: JArray("[Ljava/lang/String;", [JString("0")]))
        RB("getCameraIdList", "()[Ljava/lang/String;",
           lambda a: JArray("[Ljava/lang/String;", [JString("0")]))

        MP = "Landroid/media/MediaPlayer;"
        R(MP, "<init>", "()V", lambda a: None)
        R(MP, "create", "(Landroid/content/Context;I)Landroid/media/MediaPlayer;", lambda a: JObject(MP, {}))
        R(MP, "create", "(Landroid/content/Context;Landroid/net/Uri;)Landroid/media/MediaPlayer;", lambda a: JObject(MP, {}))
        for _mm, _ms in (
            ("setDataSource", "(Ljava/lang/String;)V"),
            ("setDataSource", "(Landroid/content/Context;Landroid/net/Uri;)V"),
            ("prepare", "()V"),
            ("prepareAsync", "()V"),
            ("start", "()V"),
            ("pause", "()V"),
            ("stop", "()V"),
            ("reset", "()V"),
            ("release", "()V"),
            ("setLooping", "(Z)V"),
        ):
            R(MP, _mm, _ms, lambda a: None)
        R(MP, "isPlaying", "()Z", lambda a: 0)

        # --- SparseArray / TypedArray (PreferenceFragmentCompat / AndroidX Views) ---
        def _sa_map(a):
            if a and isinstance(a[0], JObject):
                return a[0].fields.setdefault("_sa", {})
            return {}

        def _sa_put(a):
            if len(a) > 2:
                _sa_map(a)[int(a[1] or 0)] = a[2]
            return None

        def _sa_get(a):
            if len(a) > 1:
                m = _sa_map(a)
                k = int(a[1] or 0)
                if k in m:
                    return m[k]
                return a[2] if len(a) > 2 else None
            return None

        def _sa_key_at(a):
            ks = sorted(_sa_map(a).keys())
            idx = int(a[1] or 0) if len(a) > 1 else -1
            return ks[idx] if 0 <= idx < len(ks) else 0

        def _sa_val_at(a):
            m = _sa_map(a)
            ks = sorted(m.keys())
            idx = int(a[1] or 0) if len(a) > 1 else -1
            return m[ks[idx]] if 0 <= idx < len(ks) else None

        def _sa_set_val_at(a):
            m = _sa_map(a)
            ks = sorted(m.keys())
            idx = int(a[1] or 0) if len(a) > 1 else -1
            if 0 <= idx < len(ks) and len(a) > 2:
                m[ks[idx]] = a[2]
            return None

        def _sa_idx_key(a):
            ks = sorted(_sa_map(a).keys())
            k = int(a[1] or 0) if len(a) > 1 else 0
            try:
                return ks.index(k)
            except ValueError:
                return -1

        def _sa_rm(a):
            if len(a) > 1:
                _sa_map(a).pop(int(a[1] or 0), None)
            return None

        def _sa_rm_at(a):
            m = _sa_map(a)
            ks = sorted(m.keys())
            idx = int(a[1] or 0) if len(a) > 1 else -1
            if 0 <= idx < len(ks):
                m.pop(ks[idx], None)
            return None

        for _sac, _vsig in (
            ("Landroid/util/SparseArray;", "Ljava/lang/Object;"),
            ("Landroid/util/SparseIntArray;", "I"),
            ("Landroid/util/SparseBooleanArray;", "Z"),
        ):
            R(_sac, "<init>", "()V", lambda a: (a[0].fields.__setitem__("_sa", {}) if a and isinstance(a[0], JObject) else None))
            R(_sac, "<init>", "(I)V", lambda a: (a[0].fields.__setitem__("_sa", {}) if a and isinstance(a[0], JObject) else None))
            R(_sac, "put", f"(I{_vsig})V", _sa_put)
            R(_sac, "append", f"(I{_vsig})V", _sa_put)
            R(_sac, "get", f"(I){_vsig}", _sa_get)
            R(_sac, "get", f"(I{_vsig}){_vsig}", _sa_get)
            R(_sac, "size", "()I", lambda a: len(_sa_map(a)))
            R(_sac, "keyAt", "(I)I", _sa_key_at)
            R(_sac, "valueAt", f"(I){_vsig}", _sa_val_at)
            if _sac == "Landroid/util/SparseArray;":
                R(_sac, "setValueAt", "(ILjava/lang/Object;)V", _sa_set_val_at)
            R(_sac, "indexOfKey", "(I)I", _sa_idx_key)
            R(_sac, "remove", "(I)V", _sa_rm)
            R(_sac, "delete", "(I)V", _sa_rm)
            R(_sac, "removeAt", "(I)V", _sa_rm_at)
            R(_sac, "clear", "()V", lambda a: _sa_map(a).clear())

        TA = "Landroid/content/res/TypedArray;"

        def _obtain_ta(a):
            attrs = {}
            resv = getattr(self, "_styled_attrs_resolver", None)
            if callable(resv):
                try:
                    attrs = resv(a, getattr(self, "vm", None)) or {}
                except Exception:
                    attrs = {}
            return JObject(TA, {"_attrs": attrs})

        for _osig in (
            "([I)Landroid/content/res/TypedArray;",
            "(Landroid/util/AttributeSet;[I)Landroid/content/res/TypedArray;",
            "(Landroid/util/AttributeSet;[III)Landroid/content/res/TypedArray;",
            "(I[I)Landroid/content/res/TypedArray;",
        ):
            RB("obtainStyledAttributes", _osig, _obtain_ta)
        _ta_def = lambda a: a[2] if len(a) > 2 else 0

        def _ta_res_id(a):
            ta = a[0] if a else None
            idx = a[1] if len(a) > 1 else 0
            defv = a[2] if len(a) > 2 else 0
            if isinstance(ta, JObject):
                amap = ta.fields.get("_attrs")
                if isinstance(amap, dict) and idx in amap:
                    return amap[idx]
            return defv

        R(TA, "getResourceId", "(II)I", _ta_res_id)
        for _tm, _ts in (
            ("getInt", "(II)I"),
            ("getInteger", "(II)I"),
            ("getDimensionPixelSize", "(II)I"),
            ("getDimensionPixelOffset", "(II)I"),
            ("getLayoutDimension", "(II)I"),
            ("getColor", "(II)I"),
            ("getBoolean", "(IZ)Z"),
            ("getFloat", "(IF)F"),
            ("getDimension", "(IF)F"),
        ):
            R(TA, _tm, _ts, _ta_def)
        for _tm, _ts in (
            ("getDrawable", "(I)Landroid/graphics/drawable/Drawable;"),
            ("getString", "(I)Ljava/lang/String;"),
            ("getText", "(I)Ljava/lang/CharSequence;"),
        ):
            R(TA, _tm, _ts, lambda a: None)
        R(TA, "getColorStateList", "(I)Landroid/content/res/ColorStateList;",
          lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": 0xFF2F6DF0}))
        for _tm, _ts in (("hasValue", "(I)Z"), ("length", "()I"), ("getIndexCount", "()I")):
            R(TA, _tm, _ts, lambda a: 0)
        for _tm, _ts in (("recycle", "()V"), ("close", "()V")):
            R(TA, _tm, _ts, lambda a: None)

        # --- NavigableSet / TreeSet, TextUtils.join, PhantomReference, Application.getProcessName, JsonWriter ---
        def _nav_poll(a, first=True):
            items = self._coll_items(a[0] if a else None)
            if not items:
                return None
            return items.pop(0 if first else -1)

        def _nav_peek(a, first=True):
            items = self._coll_items(a[0] if a else None)
            if not items:
                return None
            return items[0 if first else -1]

        for _ns_cls in ("Ljava/util/NavigableSet;", "Ljava/util/TreeSet;"):
            R(_ns_cls, "pollFirst", "()Ljava/lang/Object;", lambda a: _nav_poll(a, True))
            R(_ns_cls, "pollLast", "()Ljava/lang/Object;", lambda a: _nav_poll(a, False))
            R(_ns_cls, "first", "()Ljava/lang/Object;", lambda a: _nav_peek(a, True))
            R(_ns_cls, "last", "()Ljava/lang/Object;", lambda a: _nav_peek(a, False))

        def _tu_join(a):
            delim = (self._txt(a[0]) or "") if a else ""
            src = a[1] if len(a) > 1 else None
            parts = []
            if isinstance(src, JArray):
                items = src.data
            elif isinstance(src, JObject):
                items = self._extract_coll_items(src)
            else:
                items = []
            for it in items:
                parts.append(self._txt(it) or ("" if it in (None, 0) else str(it)))
            return JString(delim.join(parts))

        R("Landroid/text/TextUtils;", "join", "(Ljava/lang/CharSequence;[Ljava/lang/Object;)Ljava/lang/String;", _tu_join)
        R("Landroid/text/TextUtils;", "join", "(Ljava/lang/CharSequence;Ljava/lang/Iterable;)Ljava/lang/String;", _tu_join)

        PR_REF = "Ljava/lang/ref/PhantomReference;"
        R(PR_REF, "<init>", "(Ljava/lang/Object;Ljava/lang/ref/ReferenceQueue;)V", lambda a: None)
        R(PR_REF, "get", "()Ljava/lang/Object;", lambda a: None)
        R(PR_REF, "clear", "()V", lambda a: None)

        R("Landroid/app/Application;", "getProcessName", "()Ljava/lang/String;",
          lambda a: JString(getattr(self.profile, "package", None) or "com.example.app"))

        JW = "Landroid/util/JsonWriter;"
        R(JW, "<init>", "(Ljava/io/Writer;)V", lambda a: a[0] if a else None)
        for _jwm, _jws in (("setIndent", "(Ljava/lang/String;)V"), ("setLenient", "(Z)V"), ("flush", "()V"), ("close", "()V")):
            R(JW, _jwm, _jws, lambda a: None)
        for _jwm, _jws in (
            ("beginObject", f"(){JW}"), ("endObject", f"(){JW}"),
            ("beginArray", f"(){JW}"), ("endArray", f"(){JW}"),
            ("name", f"(Ljava/lang/String;){JW}"),
            ("value", f"(Ljava/lang/String;){JW}"), ("value", f"(Z){JW}"),
            ("value", f"(J){JW}"), ("value", f"(D){JW}"),
            ("value", f"(Ljava/lang/Number;){JW}"), ("nullValue", f"(){JW}"),
        ):
            R(JW, _jwm, _jws, lambda a: a[0] if a else None)

    def _reg_act_cb(self, a):
        cb = a[1] if len(a) > 1 else None
        if cb is not None and cb not in self.activity_callbacks:
            self.activity_callbacks.append(cb)
        return None

    def _unreg_act_cb(self, a):
        cb = a[1] if len(a) > 1 else None
        try:
            self.activity_callbacks.remove(cb)
        except ValueError:
            pass
        return None

    def _install_reflection(self):
        self.register("Ljava/lang/Class;", "forName", "(Ljava/lang/String;)Ljava/lang/Class;", self._cls_forname)
        self.register("Ljava/lang/Class;", "forName",
                      "(Ljava/lang/String;ZLjava/lang/ClassLoader;)Ljava/lang/Class;", self._cls_forname)
        self.register("Ljava/lang/Class;", "getName", "()Ljava/lang/String;", self._cls_getname)
        self.register("Ljava/lang/Class;", "getSimpleName", "()Ljava/lang/String;", self._cls_getsimplename)
        self.register("Ljava/lang/Class;", "getComponentType", "()Ljava/lang/Class;", self._cls_getcomponenttype)
        self.register("Ljava/lang/Class;", "isArray", "()Z",
                      lambda a: 1 if (a and isinstance(a[0], JObject)
                                      and str(a[0].fields.get("name", "")).startswith("[")) else 0)
        self.register("Ljava/lang/Class;", "getMethod",
                      "(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;", self._cls_getmethod)
        self.register("Ljava/lang/Class;", "getDeclaredMethod",
                      "(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;", self._cls_getmethod)
        self.register("Ljava/lang/reflect/Method;", "invoke",
                      "(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;", self._method_invoke)
        self.register("Ljava/lang/reflect/Method;", "setAccessible", "(Z)V", lambda a: None)
        self.register("Ljava/lang/reflect/AccessibleObject;", "setAccessible", "(Z)V", lambda a: None)
        # --- Felder reflektiv (faithful: liest/schreibt echte Statics/Instanzfelder) ---
        self.register("Ljava/lang/Class;", "getDeclaredField",
                      "(Ljava/lang/String;)Ljava/lang/reflect/Field;", self._cls_getfield)
        self.register("Ljava/lang/Class;", "getField",
                      "(Ljava/lang/String;)Ljava/lang/reflect/Field;", self._cls_getfield)
        self.register("Ljava/lang/reflect/Field;", "setAccessible", "(Z)V", lambda a: None)
        self.register("Ljava/lang/reflect/Field;", "getInt", "(Ljava/lang/Object;)I",
                      lambda a: self._field_get(a, "I"))
        self.register("Ljava/lang/reflect/Field;", "getLong", "(Ljava/lang/Object;)J",
                      lambda a: self._field_get(a, "J"))
        self.register("Ljava/lang/reflect/Field;", "getShort", "(Ljava/lang/Object;)S",
                      lambda a: self._field_get(a, "I"))
        self.register("Ljava/lang/reflect/Field;", "getByte", "(Ljava/lang/Object;)B",
                      lambda a: self._field_get(a, "I"))
        self.register("Ljava/lang/reflect/Field;", "getBoolean", "(Ljava/lang/Object;)Z",
                      lambda a: self._field_get(a, "I"))
        self.register("Ljava/lang/reflect/Field;", "get",
                      "(Ljava/lang/Object;)Ljava/lang/Object;", lambda a: self._field_get(a, "L"))
        self.register("Ljava/lang/reflect/Field;", "setInt", "(Ljava/lang/Object;I)V",
                      lambda a: self._field_set(a))
        self.register("Ljava/lang/reflect/Field;", "setLong", "(Ljava/lang/Object;J)V",
                      lambda a: self._field_set(a))
        self.register("Ljava/lang/reflect/Field;", "set",
                      "(Ljava/lang/Object;Ljava/lang/Object;)V", lambda a: self._field_set(a))
        # --- Konstruktoren reflektiv (faithful: baut Objekt + laeuft <init>) ---
        self.register("Ljava/lang/Class;", "getDeclaredConstructor",
                      "([Ljava/lang/Class;)Ljava/lang/reflect/Constructor;", self._cls_getctor)
        self.register("Ljava/lang/Class;", "getConstructor",
                      "([Ljava/lang/Class;)Ljava/lang/reflect/Constructor;", self._cls_getctor)
        self.register("Ljava/lang/reflect/Constructor;", "setAccessible", "(Z)V", lambda a: None)
        self.register("Ljava/lang/reflect/Constructor;", "newInstance",
                      "([Ljava/lang/Object;)Ljava/lang/Object;", self._ctor_newinstance)
        # --- ClassesInfoCache-Naht: Class/Method-Reflection (AndroidX Lifecycle) ---
        # getDeclaredMethods/getMethods MUESSEN ein leeres Method[]-JArray liefern (nie 0/None),
        # sonst NPE/IllegalArgumentException im Array-Loop von ClassesInfoCache.createInfo.
        self.register("Ljava/lang/Class;", "getDeclaredMethods",
                      "()[Ljava/lang/reflect/Method;", self._cls_getdeclaredmethods)
        self.register("Ljava/lang/Class;", "getMethods",
                      "()[Ljava/lang/reflect/Method;", self._cls_getdeclaredmethods)
        self.register("Ljava/lang/Class;", "getSuperclass",
                      "()Ljava/lang/Class;", self._cls_getsuperclass)
        self.register("Ljava/lang/Class;", "getInterfaces",
                      "()[Ljava/lang/Class;", self._cls_getinterfaces)
        # Annotationen: kein Parser (nur veraltete @OnLifecycleEvent-Suche) -> None/0.
        self.register("Ljava/lang/reflect/Method;", "getAnnotation",
                      "(Ljava/lang/Class;)Ljava/lang/annotation/Annotation;", lambda a: None)
        self.register("Ljava/lang/Class;", "getAnnotation",
                      "(Ljava/lang/Class;)Ljava/lang/annotation/Annotation;", lambda a: None)
        self.register("Ljava/lang/reflect/Method;", "isAnnotationPresent",
                      "(Ljava/lang/Class;)Z", lambda a: 0)
        self.register("Ljava/lang/Class;", "isAnnotationPresent",
                      "(Ljava/lang/Class;)Z", lambda a: 0)
        # --- Class-Modifier / Kind & java.lang.reflect.Modifier (FragmentTransaction u. a.) ---
        def _cls_name_str(a):
            return self._to_dotted_name(a[0].fields.get("name", "")) if (a and isinstance(a[0], JObject)) else ""

        def _is_anon(nm):
            return "$" in nm and nm.rsplit("$", 1)[1].isdigit()

        def _is_member(nm):
            return "$" in nm and not nm.rsplit("$", 1)[1].isdigit()

        def _cls_modifiers(a):
            nm = _cls_name_str(a)
            if _is_anon(nm):
                return 0
            return 0x0009 if _is_member(nm) else 0x0001

        def _cls_enclosing(a):
            nm = _cls_name_str(a)
            if "$" not in nm:
                return None
            outer = nm.rsplit("$", 1)[0]
            if self.vm is not None and hasattr(self.vm, "intern_class"):
                return self.vm.intern_class(outer)
            return JObject("Ljava/lang/Class;", {"name": "L" + outer.replace(".", "/") + ";"})

        def _cls_newinstance(a):
            if not (a and isinstance(a[0], JObject)):
                return None
            ctor = self._cls_getctor([a[0], JArray("[Ljava/lang/Class;", [])])
            return self._ctor_newinstance([ctor, JArray("[Ljava/lang/Object;", [])])

        self.register("Ljava/lang/Class;", "getModifiers", "()I", _cls_modifiers)
        self.register("Ljava/lang/Class;", "isAnonymousClass", "()Z", lambda a: int(_is_anon(_cls_name_str(a))))
        self.register("Ljava/lang/Class;", "isMemberClass", "()Z", lambda a: int(_is_member(_cls_name_str(a))))
        self.register("Ljava/lang/Class;", "isLocalClass", "()Z", lambda a: 0)
        self.register("Ljava/lang/Class;", "isInterface", "()Z", lambda a: 0)
        self.register("Ljava/lang/Class;", "isPrimitive", "()Z",
                      lambda a: int(_cls_name_str(a) in (
                          "boolean", "byte", "char", "short", "int", "long", "float", "double", "void")))
        self.register("Ljava/lang/Class;", "getDeclaringClass", "()Ljava/lang/Class;", _cls_enclosing)
        self.register("Ljava/lang/Class;", "getEnclosingClass", "()Ljava/lang/Class;", _cls_enclosing)
        self.register("Ljava/lang/Class;", "newInstance", "()Ljava/lang/Object;", _cls_newinstance)
        for _mcls in ("Ljava/lang/reflect/Method;", "Ljava/lang/reflect/Field;", "Ljava/lang/reflect/Constructor;"):
            self.register(_mcls, "getModifiers", "()I", lambda a: 0x0001)
        _MOD_BITS = {
            "isPublic": 0x0001, "isPrivate": 0x0002, "isProtected": 0x0004,
            "isStatic": 0x0008, "isFinal": 0x0010, "isSynchronized": 0x0020,
            "isVolatile": 0x0040, "isTransient": 0x0080, "isNative": 0x0100,
            "isInterface": 0x0200, "isAbstract": 0x0400,
        }
        for _mn, _mask in _MOD_BITS.items():
            self.register("Ljava/lang/reflect/Modifier;", _mn, "(I)Z",
                          lambda a, m=_mask: 1 if (a and (int(a[0]) & m)) else 0)

    @staticmethod
    def _desc_from_dotted(nm):
        if nm is None:
            return None
        s = AndroidStubs._txt(nm)        # JString ODER konstruierter String-JObject (fields["text"])
        if s is None:
            return None
        s = s.strip()
        if not s:
            return None
        if (s.startswith("L") and s.endswith(";")) or s.startswith("["):
            return s
        return "L" + s.replace(".", "/") + ";"

    def _lazy_load(self, desc):
        if self.vm is None or not desc:
            return
        prov = getattr(self.vm, "class_provider", None)
        if prov is not None:
            try:
                for md in (prov.get_class(desc) or []):
                    self.vm.registry.setdefault((md.cls, md.name, md.sig), md)
            except Exception:
                pass
        for dcl in getattr(self, "_dynamic_dcls", ()):
            try:
                if dcl.has_class(desc):
                    dcl.register_class(self.vm, desc)
            except Exception:
                pass

    def _make_stack_trace(self, a=None):
        frames = list(getattr(self.vm, "_frames", []) or []) if self.vm is not None else []
        elems = []
        for fr in reversed(frames):
            md = getattr(fr, "md", None)
            cls_name = getattr(md, "cls", "Ljava/lang/Object;")
            if cls_name.startswith("L") and cls_name.endswith(";"):
                cls_name = cls_name[1:-1].replace("/", ".")
            elems.append(JObject("Ljava/lang/StackTraceElement;", {
                "cls": cls_name,
                "method": getattr(md, "name", "run"),
                "file": cls_name.rsplit(".", 1)[-1] + ".java",
                "line": max(1, int(getattr(fr, "pc", 1) or 1)),
            }))
        if not elems:
            elems.append(JObject("Ljava/lang/StackTraceElement;", {
                "cls": "android.app.ActivityThread",
                "method": "main",
                "file": "ActivityThread.java",
                "line": 1,
            }))
        return JArray("[Ljava/lang/StackTraceElement;", elems)

    def _resolve_resource(self, raw_name):
        name = self._txt(raw_name) or ""
        if not name or name.startswith("/data/"):
            return None
        fs = getattr(self, "_shared_fs", None)
        if fs is None:
            return None
        if fs.lookup(name) is not None:
            return ("vfs", name, None)
        apk_path = (getattr(self, "_app_info", None) or {}).get("sourceDir") or "/data/app/base.apk"
        z = getattr(self, "_cached_apk_zip", None)
        if z is None:
            ino = fs.lookup(apk_path)
            if ino is not None:
                try:
                    import io as _io, zipfile as _zf
                    z = self._cached_apk_zip = _zf.ZipFile(_io.BytesIO(bytes(fs.read(ino))))
                except Exception:
                    z = None
        if z is not None:
            clean = name.lstrip("/")
            try:
                names = getattr(self, "_cached_apk_names", None)
                if names is None:
                    names = self._cached_apk_names = set(z.namelist())
                if clean in names:
                    return ("apk", f"file:{apk_path}!/{clean}", z.read(clean))
            except Exception:
                pass
        return None

    def _cls_getresource(self, a):
        nm = a[1] if len(a) > 1 else (a[0] if a else None)
        hit = self._resolve_resource(nm)
        if not hit:
            return None
        _kind, path_or_url, data = hit
        return JObject("Ljava/net/URL;", {
            "path": path_or_url, "text": path_or_url, "$url": path_or_url, "_bytes": bytes(data or b"")
        })

    def _cls_getresource_stream(self, a):
        nm = a[1] if len(a) > 1 else (a[0] if a else None)
        hit = self._resolve_resource(nm)
        if not hit:
            return None
        kind, path_or_url, data = hit
        if kind == "vfs":
            return JObject("Ljava/io/FileInputStream;", {"path": path_or_url, "pos": 0})
        return JObject("Ljava/io/FileInputStream;", {"path": "", "pos": 0, "_mem": bytes(data or b"")})

    def _cls_forname(self, a):
        nm = a[0] if a else None
        dotted = self._txt(nm)
        desc = self._desc_from_dotted(dotted)
        cl = a[2] if len(a) > 2 else None
        if desc and isinstance(cl, JObject) and cl.fields.get("_dcls") and self.vm is not None:
            for dcl in cl.fields.get("_dcls") or ():
                try:
                    if dcl.has_class(desc):
                        dcl.register_class(self.vm, desc)
                        break
                except Exception:
                    pass
        self._lazy_load(desc)
        if self.vm is not None and hasattr(self.vm, "intern_class"):
            return self.vm.intern_class(dotted)
        return JObject("Ljava/lang/Class;", {"name": dotted})

    @staticmethod
    def _to_dotted_name(nm):
        s = str(nm or "")
        if s.startswith("L") and s.endswith(";"):
            return s[1:-1].replace("/", ".")
        return s.replace("/", ".")

    def _cls_getname(self, a):
        o = a[0] if a else None
        nm = o.fields.get("name") if isinstance(o, JObject) else None
        return JString(self._to_dotted_name(nm)) if nm else JString("")

    def _cls_getsimplename(self, a):
        o = a[0] if a else None
        nm = o.fields.get("name") if isinstance(o, JObject) else None
        if not nm:
            return JString("")
        s = self._to_dotted_name(nm)
        return JString(s.rsplit(".", 1)[-1].rsplit("$", 1)[-1])

    def _cls_getdeclaredmethods(self, a):
        """Glass-Box: leeres Method[]-Array (kein Reflection-Scan der Smali-Koerper).
        ClassesInfoCache sucht nur veraltete @OnLifecycleEvent-Methoden -> leer ist korrekt."""
        return JArray("[Ljava/lang/reflect/Method;", [])

    def _cls_getinterfaces(self, a):
        """Keine Interface-Tabelle gefuehrt -> leeres Class[] (Rekursion terminiert sauber)."""
        return JArray("[Ljava/lang/Class;", [])

    def _cls_getsuperclass(self, a):
        """Superklasse via class_provider (.super); None bei Ljava/lang/Object; bzw. unbekannt,
        damit die getInfo-Rekursion in ClassesInfoCache garantiert terminiert."""
        o = a[0] if a else None
        nm = o.fields.get("name") if isinstance(o, JObject) else None
        desc = self._desc_from_dotted(nm)
        if desc is None or desc == "Ljava/lang/Object;":
            return None
        sup = None
        if self.vm is not None:
            try:
                sup = self.vm._superclass(desc)
            except Exception:
                sup = None
        if not sup:
            return None          # unbekannte Superklasse -> Rekursion anhalten
        dotted = (sup[1:-1].replace("/", ".")
                  if isinstance(sup, str) and sup.startswith("L") and sup.endswith(";") else sup)
        if self.vm is not None and hasattr(self.vm, "intern_class"):
            return self.vm.intern_class(dotted)
        return JObject("Ljava/lang/Class;", {"name": dotted})

    @staticmethod
    def _desc_from_class_obj(cls_obj):
        if isinstance(cls_obj, JObject):
            prim = cls_obj.fields.get("primitive")
            if prim:
                return prim
            nm = cls_obj.fields.get("name")
            if nm in ("boolean", "byte", "char", "short", "int", "long", "float", "double", "void"):
                return {"boolean": "Z", "byte": "B", "char": "C", "short": "S",
                        "int": "I", "long": "J", "float": "F", "double": "D", "void": "V"}[nm]
            return AndroidStubs._desc_from_dotted(nm)
        if isinstance(cls_obj, str):
            return AndroidStubs._desc_from_dotted(cls_obj)
        return None

    def _extract_ptypes(self, ptypes):
        if isinstance(ptypes, JArray):
            raw = list(ptypes.data)
        elif isinstance(ptypes, list):
            raw = list(ptypes)
        else:
            return None
        return [self._desc_from_class_obj(c) or "Ljava/lang/Object;" for c in raw]

    @staticmethod
    def _split_sig(sig):
        if not sig or "(" not in sig or ")" not in sig:
            return [], "V"
        inner = sig[sig.index("(") + 1 : sig.index(")")]
        rettype = sig[sig.index(")") + 1 :] or "V"
        params = []
        i = 0
        n = len(inner)
        while i < n:
            arr = ""
            while i < n and inner[i] == "[":
                arr += "["
                i += 1
            if i >= n:
                break
            if inner[i] == "L":
                j = inner.find(";", i)
                if j < 0:
                    params.append(arr + inner[i:])
                    break
                params.append(arr + inner[i : j + 1])
                i = j + 1
            else:
                params.append(arr + inner[i])
                i += 1
        return params, rettype

    @staticmethod
    def _unbox_for_param(val, pdesc):
        if pdesc in ("I", "Z", "B", "S", "C", "J", "F", "D"):
            if isinstance(val, JObject) and "value" in val.fields:
                raw = val.fields.get("value", 0)
            else:
                raw = val
            if pdesc == "Z":
                return 1 if raw else 0
            if pdesc == "C":
                return ord(raw) if isinstance(raw, str) and raw else int(raw or 0)
            if pdesc in ("F", "D"):
                try:
                    return float(raw or 0.0)
                except Exception:
                    return 0.0
            try:
                iv = int(raw or 0)
                if pdesc == "I":
                    iv &= 0xffffffff
                    return iv - 0x100000000 if (iv & 0x80000000) else iv
                if pdesc == "S":
                    iv &= 0xffff
                    return iv - 0x10000 if (iv & 0x8000) else iv
                if pdesc == "B":
                    iv &= 0xff
                    return iv - 0x100 if (iv & 0x80) else iv
                if pdesc == "J":
                    iv &= 0xffffffffffffffff
                    return iv - 0x10000000000000000 if (iv & 0x8000000000000000) else iv
                return iv
            except Exception:
                return 0
        return val

    def _unbox_args(self, callargs, sig):
        pdescs, rettype = self._split_sig(sig)
        out = [self._unbox_for_param(arg, pdescs[i]) if i < len(pdescs) else arg
               for i, arg in enumerate(callargs)]
        return out, rettype

    def _cls_getcomponenttype(self, a):
        c = a[0] if a else None
        nm = str(c.fields.get("name", "")) if isinstance(c, JObject) else ""
        if not nm.startswith("["):
            return None
        sub = nm[1:]
        if sub.startswith("L") and sub.endswith(";"):
            return JObject("Ljava/lang/Class;", {"name": sub[1:-1].replace("/", ".")})
        pmap = {"Z": ("boolean", "Z"), "B": ("byte", "B"), "C": ("char", "C"),
                "S": ("short", "S"), "I": ("int", "I"), "J": ("long", "J"),
                "F": ("float", "F"), "D": ("double", "D")}
        if sub in pmap:
            pname, pdesc = pmap[sub]
            return JObject("Ljava/lang/Class;", {"name": pname, "primitive": pdesc})
        return JObject("Ljava/lang/Class;", {"name": sub})

    def _cls_getmethod(self, a):
        owner = a[0] if a else None
        name = a[1] if len(a) > 1 else None
        ptypes = a[2] if len(a) > 2 else None
        plist = self._extract_ptypes(ptypes)
        nparams = len(plist) if plist is not None else 0
        mname = self._txt(name) or ""
        return JObject("Ljava/lang/reflect/Method;",
                       {"owner": owner, "name": mname, "nparams": nparams, "ptypes": plist})

    def _method_invoke(self, a):
        m = a[0] if a else None
        obj = a[1] if len(a) > 1 else None
        argsarr = a[2] if len(a) > 2 else None
        if not isinstance(m, JObject) or self.vm is None:
            return 0
        owner = m.fields.get("owner")
        mname = m.fields.get("name")
        plist = m.fields.get("ptypes")
        dotted = owner.fields.get("name") if isinstance(owner, JObject) else None
        desc = self._desc_from_dotted(dotted)
        if isinstance(argsarr, JArray):
            callargs = list(argsarr.data)
        elif isinstance(argsarr, list):
            callargs = list(argsarr)
        else:
            callargs = []
        if not desc or not mname:
            return 0
        # Virtuelle Dispatch-Kette: Subklasse obj.cls -> Superklassen -> deklarierende Klasse desc
        target_descs = []
        if isinstance(obj, JObject) and obj.cls and obj.cls not in ("Ljava/lang/Class;",):
            cur = obj.cls
            for _ in range(16):
                if cur and cur not in target_descs:
                    target_descs.append(cur)
                if cur == desc or not cur:
                    break
                nxt = None
                try:
                    nxt = self.vm._superclass(cur)
                except Exception:
                    nxt = None
                if not nxt:
                    # Bekannte AOSP-Filter/Input/OutputStream-Kette als Fallback
                    _stream_sup = {
                        "Ljava/io/BufferedInputStream;": "Ljava/io/FilterInputStream;",
                        "Ljava/io/DataInputStream;": "Ljava/io/FilterInputStream;",
                        "Ljava/io/PushbackInputStream;": "Ljava/io/FilterInputStream;",
                        "Ljava/util/zip/ZipInputStream;": "Ljava/io/FilterInputStream;",
                        "Ljava/io/FilterInputStream;": "Ljava/io/InputStream;",
                        "Ljava/io/ByteArrayInputStream;": "Ljava/io/InputStream;",
                        "Ljava/io/FileInputStream;": "Ljava/io/InputStream;",
                        "Ljava/io/BufferedOutputStream;": "Ljava/io/FilterOutputStream;",
                        "Ljava/io/DataOutputStream;": "Ljava/io/FilterOutputStream;",
                        "Ljava/io/FilterOutputStream;": "Ljava/io/OutputStream;",
                        "Ljava/io/ByteArrayOutputStream;": "Ljava/io/OutputStream;",
                        "Ljava/io/FileOutputStream;": "Ljava/io/OutputStream;",
                        "Ljava/io/StringReader;": "Ljava/io/Reader;",
                        "Ljava/io/CharArrayReader;": "Ljava/io/Reader;",
                        "Ljava/io/InputStreamReader;": "Ljava/io/Reader;",
                        "Ljava/io/BufferedReader;": "Ljava/io/Reader;",
                        "Ljava/io/StringWriter;": "Ljava/io/Writer;",
                        "Ljava/io/BufferedWriter;": "Ljava/io/Writer;",
                        "Ljava/io/OutputStreamWriter;": "Ljava/io/Writer;",
                    }
                    nxt = _stream_sup.get(cur)
                cur = nxt
        elif isinstance(obj, JString):
            target_descs.append("Ljava/lang/String;")
        if desc not in target_descs:
            target_descs.append(desc)

        def _pick_md(cands):
            if plist is not None:
                for c in cands:
                    if self._split_sig(c.sig)[0] == plist:
                        return c
            for c in cands:
                if len(self._split_sig(c.sig)[0]) == len(callargs):
                    return c
            return None

        md = None
        fallback_md = None
        for tdesc in target_descs:
            cands_t = [v for (k, v) in self.vm.registry.items() if k[0] == tdesc and k[1] == mname]
            if not cands_t:
                self._lazy_load(tdesc)
                cands_t = [v for (k, v) in self.vm.registry.items() if k[0] == tdesc and k[1] == mname]
            if cands_t:
                if fallback_md is None:
                    fallback_md = cands_t[0]
                md = _pick_md(cands_t)
                if md is not None:
                    break
        if md is not None:
            unboxed, rettype = self._unbox_args(callargs, md.sig)
            invoke_args = (list(unboxed) if (md.static or obj in (None, 0)) else [obj] + list(unboxed))
            for _hk in list(getattr(self.vm, "_method_hooks", ()) or ()):
                try:
                    handled_h, ret_h = _hk(md.cls, md.cls, md.name, md.sig, invoke_args, md.static, self.vm)
                    if handled_h:
                        return self._box(ret_h, rettype)
                except Exception:
                    pass
            try:
                ret = self.vm.invoke(md.cls, md.name, md.sig, invoke_args)
            except Exception:
                return 0
            return self._box(ret, rettype)

        # Native-Method-Table pruefen (JNI_OnLoad / RegisterNatives)
        nt = getattr(self.vm, "native_table", None)
        if nt is not None:
            self.vm._ensure_class_init(desc)
            for tdesc in target_descs:
                nm_cands = [
                    (k[2], ent) for (k, ent) in getattr(nt, "_exact", {}).items()
                    if k[0] == tdesc and k[1] == mname
                ]
                if nm_cands:
                    chosen_n = None
                    if plist is not None:
                        chosen_n = next((it for it in nm_cands if self._split_sig(it[0])[0] == plist), None)
                    if chosen_n is None:
                        chosen_n = next((it for it in nm_cands if len(self._split_sig(it[0])[0]) == len(callargs)), nm_cands[0])
                    nsig, ent = chosen_n
                    unboxed, rettype = self._unbox_args(callargs, nsig)
                    cargs = list(unboxed) if (ent.get("static", True) or obj in (None, 0)) else ([obj] + list(unboxed))
                    try:
                        ok, ret = self.vm.runtime.resolve_invoke(tdesc, mname, nsig, cargs, ent.get("static", True))
                        if ok:
                            return self._box(ret, rettype)
                    except Exception:
                        pass

        # Reflektiver Aufruf einer FRAMEWORK-Methode -> virtuelle Zielklasse zuerst in _stubs,
        # ptypes + nparams matchen, Primitive entpacken, statisches null (obj=0) beachten und boxen.
        def _pick_stub(scands_list):
            if plist is not None:
                for sig, fn in scands_list:
                    if self._split_sig(sig)[0] == plist:
                        return (sig, fn)
            for sig, fn in scands_list:
                if len(self._split_sig(sig)[0]) == len(callargs):
                    return (sig, fn)
            return None

        scands = []
        chosen = None
        eff_desc = desc
        for tdesc in target_descs:
            scands_t = [(k[2], fn) for (k, fn) in self._stubs.items() if k[0] == tdesc and k[1] == mname]
            if scands_t:
                scands = scands or scands_t
                hit = _pick_stub(scands_t)
                if hit is not None:
                    chosen = hit
                    eff_desc = tdesc
                    break
        if chosen is None:
            by_n = [(k[1], fn) for (k, fn) in self._by_name.items() if k[0] == mname]
            if by_n:
                chosen = _pick_stub(by_n) or (by_n[0] if not scands else None)
        if chosen is None and scands:
            chosen = scands[0]
        if chosen is not None:
            msig, fn = chosen
            unboxed, rettype = self._unbox_args(callargs, msig)
            known_static = (
                obj is None or obj == 0
                or eff_desc in ("Ljava/lang/System;", "Ljava/lang/Thread;", "Ljava/lang/Math;",
                                "Landroid/text/TextUtils;", "Ljava/lang/reflect/Array;")
                or (eff_desc == "Ljava/lang/Class;" and mname == "forName")
                or (eff_desc == "Ljava/lang/Runtime;" and mname == "getRuntime")
                or mname in ("valueOf", "parseInt", "parseBoolean")
            )
            call_vec = list(unboxed) if known_static else ([obj] + list(unboxed))
            try:
                ret = fn(call_vec)
            except Exception:
                return 0
            return self._box(ret, rettype)
        if fallback_md is not None:
            unboxed, rettype = self._unbox_args(callargs, fallback_md.sig)
            invoke_args = (list(unboxed) if (fallback_md.static or obj in (None, 0)) else [obj] + list(unboxed))
            try:
                ret = self.vm.invoke(fallback_md.cls, fallback_md.name, fallback_md.sig, invoke_args)
            except Exception:
                return 0
            return self._box(ret, rettype)
        return 0

    # ---- Feld-/Konstruktor-Reflection (faithful) ----------------------------
    def _field_ownerkey(self, fieldobj):
        owner = fieldobj.fields.get("owner") if isinstance(fieldobj, JObject) else None
        dotted = owner.fields.get("name") if isinstance(owner, JObject) else None
        desc = self._desc_from_dotted(dotted)
        name = fieldobj.fields.get("name") if isinstance(fieldobj, JObject) else None
        name = self._txt(name) if name is not None else None
        return desc, name

    def _cls_getfield(self, a):
        owner = a[0] if a else None
        nm = a[1] if len(a) > 1 else None
        fname = self._txt(nm) or ""
        return JObject("Ljava/lang/reflect/Field;", {"owner": owner, "name": fname})

    def _field_get(self, a, kind):
        fieldobj = a[0] if a else None
        obj = a[1] if len(a) > 1 else None
        desc, name = self._field_ownerkey(fieldobj)
        if self.vm is None or not desc or not name:
            return 0
        if isinstance(obj, JObject):
            if obj.cls == "Ljava/io/FileDescriptor;" and name in ("descriptor", "fd"):
                val = int(obj.fields.get("descriptor", obj.fields.get("fd", -1)))
                return JObject("Ljava/lang/Integer;", {"value": val}) if kind == "L" else val
            if name in obj.fields:
                return obj.fields.get(name, 0)     # Instanzfeld
            if name == "pathList" and "ClassLoader" in (obj.cls or ""):
                pl = self._make_dex_path_list()
                obj.fields["pathList"] = pl
                return pl
            if obj.cls == "Ldalvik/system/DexPathList;":
                val = (JObject("Ljava/util/ArrayList;", {"__items": []})
                       if ("Dir" in name or "dir" in name)
                       else JArray("[Ldalvik/system/DexPathList$Element;", []))
                obj.fields[name] = val
                return val
        return self.vm._sget(desc, name)        # statisches Feld (triggert clinit+Defaults)

    def _field_set(self, a):
        fieldobj = a[0] if a else None
        obj = a[1] if len(a) > 1 else None
        val = a[2] if len(a) > 2 else 0
        desc, name = self._field_ownerkey(fieldobj)
        if self.vm is None or not desc or not name:
            return None
        if isinstance(obj, JObject) and obj.cls not in ("Ljava/lang/Class;",):
            obj.fields[name] = val             # Instanzfeld
        else:
            self.vm._sput(desc, name, val)      # statisches Feld
        return None

    def _cls_getctor(self, a):
        owner = a[0] if a else None
        ptypes = a[1] if len(a) > 1 else None
        plist = self._extract_ptypes(ptypes)
        nparams = len(plist) if plist is not None else 0
        return JObject("Ljava/lang/reflect/Constructor;",
                       {"owner": owner, "nparams": nparams, "ptypes": plist})

    def _ctor_newinstance(self, a):
        ctor = a[0] if a else None
        argsarr = a[1] if len(a) > 1 else None
        owner = ctor.fields.get("owner") if isinstance(ctor, JObject) else None
        plist = ctor.fields.get("ptypes") if isinstance(ctor, JObject) else None
        dotted = owner.fields.get("name") if isinstance(owner, JObject) else None
        desc = self._desc_from_dotted(dotted)
        if not desc:
            return None
        if isinstance(argsarr, JArray):
            callargs = list(argsarr.data)
        elif isinstance(argsarr, list):
            callargs = list(argsarr)
        else:
            callargs = []
        if desc == "Ljava/lang/String;":
            # Reflektiv konstruierter String -> dekodierter JString (unboxt Integer-Offsets sauber)
            if callargs:
                arr = callargs[0]
                if isinstance(arr, JArray) and arr.eltype == "[C":   # char[] -> UTF-16-Units
                    off = self._unbox_for_param(callargs[1], "I") if len(callargs) > 2 else 0
                    cnt = self._unbox_for_param(callargs[2], "I") if len(callargs) > 2 else None
                    return JString(self._chars_to_text(arr, off, cnt))
                if isinstance(arr, JArray) and len(callargs) >= 3 and isinstance(
                        self._unbox_for_param(callargs[1], "I"), int):
                    off = self._unbox_for_param(callargs[1], "I")
                    cnt = self._unbox_for_param(callargs[2], "I")
                    _cs = self._txt(callargs[3]) if len(callargs) > 3 else None
                    return JString(self._bytes_to_text(arr, charset=_cs or "utf-8", offset=off, count=cnt))
                _cs = self._txt(callargs[1]) if len(callargs) > 1 else None
                cs = _cs if _cs else "utf-8"
                try:
                    return JString(self._bytes_to_text(arr, charset=cs))
                except Exception:
                    return JString("")
            return JString("")
        obj = JObject(desc, {})
        if "ClassLoader" in desc:
            obj.fields.setdefault("pathList", self._make_dex_path_list())
        if self.vm is not None:
            self._lazy_load(desc)
            cand = [v for (k, v) in self.vm.registry.items()
                    if k[0] == desc and k[1] == "<init>"]
            if cand:
                md = None
                if plist is not None:
                    for c in cand:
                        if self._split_sig(c.sig)[0] == plist:
                            md = c; break
                if md is None:
                    for c in cand:
                        if len(self._split_sig(c.sig)[0]) == len(callargs):
                            md = c; break
                md = md or cand[0]
                unboxed, _ = self._unbox_args(callargs, md.sig)
                try:
                    self.vm.invoke(md.cls, md.name, md.sig, [obj] + list(unboxed))
                except Exception:
                    pass
                return obj
        # Framework-<init>-Stubs (z. B. File, BufferedInputStream, DataInputStream, IOException)
        scands = [(k[2], fn) for (k, fn) in self._stubs.items()
                  if k[0] == desc and k[1] == "<init>"]
        if scands:
            chosen = None
            if plist is not None:
                for sig, fn in scands:
                    if self._split_sig(sig)[0] == plist:
                        chosen = (sig, fn); break
            if chosen is None:
                for sig, fn in scands:
                    if len(self._split_sig(sig)[0]) == len(callargs):
                        chosen = (sig, fn); break
            chosen = chosen or scands[0]
            msig, fn = chosen
            unboxed, _ = self._unbox_args(callargs, msig)
            try:
                fn([obj] + list(unboxed))
            except Exception:
                pass
        return obj

    @staticmethod
    def _obj_clone(a):
        """Object.clone(): Array -> Kopie der Daten, JObject -> flache Feldkopie."""
        x = a[0] if a else None
        if isinstance(x, JArray):
            return JArray(x.eltype, list(x.data))
        if isinstance(x, JObject):
            return JObject(x.cls, dict(x.fields))
        return x

    def _enum_init(self, a):
        """Ljava/lang/Enum;-><init>(name, ordinal): speichert beides auf der Instanz (a[0])."""
        this = a[0] if a else None
        if isinstance(this, JObject):
            this.fields["__ename"] = self._txt(a[1]) if len(a) > 1 else ""
            this.fields["__ordinal"] = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
        return None

    @staticmethod
    def _unbox(a):
        """Wrapper -> primitiver Wert (JObject.value, roher int, sonst 0)."""
        x = a[0] if a else 0
        if isinstance(x, JObject):
            return x.fields.get("value", 0)
        return x if isinstance(x, int) else 0

    @staticmethod
    def _box(ret, rettype):
        """Primitive Rueckgabe wie Java boxen (Method.invoke liefert ein Wrapper-Objekt)."""
        if not rettype or rettype == "V":
            return None if rettype == "V" else ret
        c = rettype[0]
        if c == "Z":
            return JObject("Ljava/lang/Boolean;", {"value": 1 if ret else 0})
        if c == "C":
            val = ord(ret) if isinstance(ret, str) and ret else int(ret or 0)
            return JObject("Ljava/lang/Character;", {"value": val})
        if c == "B":
            return JObject("Ljava/lang/Byte;", {"value": int(ret or 0)})
        if c == "S":
            return JObject("Ljava/lang/Short;", {"value": int(ret or 0)})
        if c == "I":
            return JObject("Ljava/lang/Integer;", {"value": int(ret or 0)})
        if c == "J":
            return JObject("Ljava/lang/Long;", {"value": int(ret or 0)})
        if c == "F":
            return JObject("Ljava/lang/Float;", {"value": float(ret or 0.0)})
        if c == "D":
            return JObject("Ljava/lang/Double;", {"value": float(ret or 0.0)})
        return ret

    def _parse_java_int(self, raw, radix=10, signed_bits=32):
        s = None if (raw is None or self._is_dalvik_null(raw)) else self._txt(raw)
        valid = False
        val = 0
        try:
            r = int(radix)
        except Exception:
            r = -1
        if s is not None and 2 <= r <= 36 and len(s) > 0 and s == s.strip() and "_" not in s and s.isascii():
            body = s[1:] if s[0] in "+-" else s
            if len(body) > 0 and body.isalnum():
                try:
                    val = int(s, r)
                    lo = -(1 << (signed_bits - 1))
                    hi = (1 << (signed_bits - 1)) - 1
                    if lo <= val <= hi:
                        valid = True
                except Exception:
                    valid = False
        if valid:
            return val
        msg = "Cannot parse null string" if s is None else f'For input string: "{s}"'
        exc = JObject("Ljava/lang/NumberFormatException;", {"msg": msg, "detailMessage": JString(msg)})
        vm = getattr(self, "vm", None)
        if vm is not None:
            frames = getattr(vm, "_frames", None) or ()
            has_handler = any(
                vm._find_handler(getattr(_fr, "md", None), getattr(_fr, "cur", 0), exc) is not None
                for _fr in reversed(frames)
            )
            if (not getattr(vm, "lenient", False)) or has_handler:
                raise DalvikException(f"java.lang.NumberFormatException: {msg}", payload=exc)
        return 0

    def _parse_int(self, a):
        return self._parse_java_int(a[0] if a else None, 10, 32)

    @classmethod
    def _parse_float(cls, a):
        try:
            t = cls._txt(a[0]) if a else None
            return float(t.strip()) if t is not None else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _sb_init(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["buf"] = ""
        return None

    @staticmethod
    def _sb_append(a):
        if a and isinstance(a[0], JObject):
            raw = a[1] if len(a) > 1 else ""
            t = AndroidStubs._txt(raw)
            v = t if t is not None else str(raw)
            a[0].fields["buf"] = a[0].fields.get("buf", "") + v
            return a[0]
        return a[0] if a else None

    @staticmethod
    def _sb_append_str(a):
        if a and isinstance(a[0], JObject):
            raw = a[1] if len(a) > 1 else None
            if AndroidStubs._is_dalvik_null(raw):
                v = "null"
            else:
                t = AndroidStubs._txt(raw)
                v = t if t is not None else str(raw)
            a[0].fields["buf"] = a[0].fields.get("buf", "") + v
            return a[0]
        return a[0] if a else None

    @staticmethod
    def _sb_tostr(a):
        return JString(a[0].fields.get("buf", "")) if a and isinstance(a[0], JObject) else JString("")

    @staticmethod
    def _str_equals(a):
        t0 = AndroidStubs._txt(a[0]) if a else None
        t1 = AndroidStubs._txt(a[1]) if len(a) > 1 else None
        if t0 is not None and t1 is not None:
            return 1 if t0 == t1 else 0
        return 0

    @staticmethod
    def _str_indexof(a):
        t0 = AndroidStubs._txt(a[0]) if a else None
        t1 = AndroidStubs._txt(a[1]) if len(a) > 1 else None
        if t0 is not None and t1 is not None:
            return t0.find(t1)
        return -1

    @staticmethod
    def _str_startswith(a):
        t0 = AndroidStubs._txt(a[0]) if a else None
        t1 = AndroidStubs._txt(a[1]) if len(a) > 1 else None
        if t0 is not None and t1 is not None:
            return 1 if t0.startswith(t1) else 0
        return 0

    @staticmethod
    def _coll_init(a, key, empty):
        if a and isinstance(a[0], JObject):
            a[0].fields[key] = list(empty) if isinstance(empty, list) else dict(empty)
        return None

    @staticmethod
    def _list_add(a):
        if a and isinstance(a[0], JObject):
            a[0].fields.setdefault("list", []).append(a[1] if len(a) > 1 else None)
            return 1
        return 0

    @staticmethod
    def _list_get(a):
        if a and isinstance(a[0], JObject):
            lst = a[0].fields.get("list", [])
            i = a[1] if len(a) > 1 else 0
            return lst[i] if 0 <= i < len(lst) else None
        return None

    # ---- Text-Koercion + String/Bytes-Helfer (faithful) -------------------
    @staticmethod
    def _txt(x):
        """Text aus String-artigem Wert: JString, konstruierter String (JObject fields['text']),
        StringBuilder (fields['sb']) oder python str. None sonst."""
        if isinstance(x, JString):
            return x.text
        if isinstance(x, str):
            return x
        if isinstance(x, JObject):
            t = x.fields.get("$text")
            if t is None:
                t = x.fields.get("text")
            if t is not None:
                return t if isinstance(t, str) else str(t)
            for k in ("sb", "buf", "path"):
                v = x.fields.get(k)
                if v is not None:
                    return v if isinstance(v, str) else str(v)
        return None

    def _obj_to_str(self, val):
        """Spezifikationsnahe String-Konvertierung (Smali-toString -> Stub-toString -> Fallback) mit Zyklusschutz."""
        if self._is_dalvik_null(val):
            return "null"
        if isinstance(val, JString):
            return val.text
        if isinstance(val, str):
            return val
        if isinstance(val, JArray):
            return f"{val.eltype}@{id(val):x}"
        if isinstance(val, JObject):
            oid = id(val)
            if oid in self._tostr_guard:
                return str(val.fields.get("__ename") or val.cls)
            self._tostr_guard.add(oid)
            try:
                if self.vm is not None:
                    md = self.vm._resolve_method(val.cls, val.cls, "toString", "()Ljava/lang/String;")
                    if md is not None:
                        res = self.vm._run_mdef(md, [val])
                        t = self._txt(res)
                        if t is not None:
                            return t
                wants = (
                    self.vm._build_wants(val.cls, val.cls)
                    if self.vm is not None
                    else [val.cls, "Ljava/lang/Object;"]
                )
                ok, res = self.resolve_phased(
                    wants, val.cls, val.cls, "toString", "()Ljava/lang/String;", [val], False
                )
                if ok:
                    t = self._txt(res)
                    if t is not None:
                        return t
            except (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop):
                raise
            except Exception:
                pass
            finally:
                self._tostr_guard.discard(oid)
            t = self._txt(val)
            return t if t is not None else str(val.fields.get("__ename") or val.cls)
        return str(val)

    @staticmethod
    def _charset_py(name):
        n = (name or "").replace("-", "").replace("_", "").lower()
        return {"utf8": "utf-8", "usascii": "ascii", "ascii": "ascii", "iso88591": "latin-1",
                "latin1": "latin-1", "cp1252": "cp1252", "windows1252": "cp1252",
                "utf16": "utf-16", "utf16be": "utf-16-be", "utf16le": "utf-16-le"}.get(n, "latin-1")

    @staticmethod
    def _arr_ints(arr):
        if isinstance(arr, JArray):
            return [int(b) & 0xFF for b in arr.data]
        if isinstance(arr, (list, tuple, bytes, bytearray)):
            return [int(b) & 0xFF for b in arr]
        return []

    @classmethod
    def _bytes_to_text(cls, arr, charset=None, offset=0, count=None, hibyte=None):
        data = cls._arr_ints(arr)
        if count is None:
            count = len(data) - offset
        seg = data[offset:offset + max(int(count), 0)]
        if hibyte is not None:
            hb = (int(hibyte) & 0xFF) << 8
            return "".join(chr(hb | b) for b in seg)
        try:
            return bytes(seg).decode(cls._charset_py(charset), errors="replace")
        except Exception:
            return "".join(chr(b) for b in seg)

    def _str_getbytes(self, a):
        t = self._txt(a[0]) or ""
        cs = self._charset_py(self._txt(a[1]) if len(a) > 1 else "utf-8")
        try:
            bs = t.encode(cs, errors="replace")
        except Exception:
            bs = t.encode("latin-1", errors="replace")
        return JArray("[B", [b for b in bs])

    @staticmethod
    def _str_set(this, text):
        if isinstance(this, JObject):
            this.fields["text"] = text
        return this

    @classmethod
    def _chars_to_text(cls, arr, offset=0, count=None):
        """char[] -> str (UTF-16-Code-Units, KEIN &0xFF wie bei Byte-Arrays)."""
        if not isinstance(arr, JArray):
            return ""
        data = arr.data
        seg = data[offset:] if count is None else data[offset:offset + max(int(count), 0)]
        return "".join(chr(int(c) & 0xFFFF) for c in seg)

    def _str_init_c(self, a):
        """new String(char[]) / new String(char[], offset, count)."""
        arr = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else None
        return self._str_set(a[0], self._chars_to_text(arr, off, cnt))

    def _str_init_ci(self, a):
        """new String(int[] codePoints, offset, count)."""
        arr = a[1] if len(a) > 1 else None
        off = int(a[2]) if len(a) > 2 else 0
        cnt = int(a[3]) if len(a) > 3 else None
        if isinstance(arr, JArray):
            seg = arr.data[off:(off + cnt) if cnt is not None else len(arr.data)]
            return self._str_set(a[0], "".join(chr(int(c) & 0x10FFFF) for c in seg))
        return self._str_set(a[0], "")

    @staticmethod
    def _java_strhash(s):
        """Deterministischer Java-String.hashCode (s[0]*31^(n-1)+…), signed 32-bit."""
        h = 0
        for ch in s:
            h = (31 * h + ord(ch)) & 0xFFFFFFFF
        return h - 0x100000000 if h >= 0x80000000 else h

    def _str_init_b(self, a):
        return self._str_set(a[0], self._bytes_to_text(a[1] if len(a) > 1 else None, charset="utf-8"))

    def _str_init_b_hibyte(self, a):
        return self._str_set(a[0], self._bytes_to_text(a[1] if len(a) > 1 else None,
                                                        hibyte=a[2] if len(a) > 2 else 0))

    def _str_init_b_off_cnt(self, a):
        return self._str_set(a[0], self._bytes_to_text(a[1] if len(a) > 1 else None,
                                                        offset=int(a[2]) if len(a) > 2 else 0,
                                                        count=int(a[3]) if len(a) > 3 else None,
                                                        charset="utf-8"))

    def _str_init_b_charset(self, a):
        return self._str_set(a[0], self._bytes_to_text(a[1] if len(a) > 1 else None,
                                                        charset=self._txt(a[2]) if len(a) > 2 else "utf-8"))

    def _str_init_b_off_cnt_charset(self, a):
        return self._str_set(a[0], self._bytes_to_text(a[1] if len(a) > 1 else None,
                                                        offset=int(a[2]) if len(a) > 2 else 0,
                                                        count=int(a[3]) if len(a) > 3 else None,
                                                        charset=self._txt(a[4]) if len(a) > 4 else "utf-8"))

    @staticmethod
    def _sys_arraycopy(a):
        try:
            src, sp, dst, dp, ln = a[0], int(a[1]), a[2], int(a[3]), int(a[4])
            if isinstance(src, JArray) and isinstance(dst, JArray):
                for i in range(ln):
                    if 0 <= sp + i < len(src.data) and 0 <= dp + i < len(dst.data):
                        dst.data[dp + i] = src.data[sp + i]
        except Exception:
            pass
        return None

    @staticmethod
    def _sys_loadlibrary(a):
        return None   # Glass: nativer Lade-Zeitpunkt (Tor 2+), hier beobachtbar/no-op

    def _runtime_get(self, a):
        r = getattr(self, "_runtime_singleton", None)
        if r is None:
            r = self._runtime_singleton = JObject("Ljava/lang/Runtime;", {})
        return r

    def _fusion_path_load(self, a):
        """Runtime.nativeLoad/load + System.load: absoluten .so-Pfad an den Fusions-Pfad-Loader.
        Rueckgabe None (= null; Androids Runtime.nativeLoad meldet so KEINEN Fehler -- ein String
        waere die Fehlermeldung fuer UnsatisfiedLinkError)."""
        path = None
        for x in (a or []):
            t = self._txt(x)
            if t and ("/" in t or t.endswith(".so")):
                path = t
                break
        hook = getattr(self.vm, "_fusion_load_path", None) if self.vm is not None else None
        if callable(hook) and path:
            try:
                hook(path)
            except Exception:
                pass
        return None

    def _fusion_name_load(self, a):
        """Runtime.loadLibrary0 + System.loadLibrary: Lib-Name an den Fusions-Namens-Loader."""
        name = None
        for x in (a or []):
            t = self._txt(x)
            if t and "/" not in t and not t.endswith(".so"):
                name = t
                break
        hook = getattr(self.vm, "_fusion_load_library", None) if self.vm is not None else None
        if callable(hook) and name:
            try:
                hook(name)
            except Exception:
                pass
        return None

    @staticmethod
    def _kotlin_lazy(a):
        return JObject("Lkotlin/Lazy;", {"lazy_fn": a[0] if a else None})

    @classmethod
    def _mkey(cls, k):
        if isinstance(k, JObject):
            if k.cls == "Ljava/lang/Integer;":
                return ("I", int(k.fields.get("value", 0)) & 0xFFFFFFFF)
            if k.cls == "Ljava/lang/Long;":
                return ("J", int(k.fields.get("value", 0)) & 0xFFFFFFFFFFFFFFFF)
            if "value" in k.fields:
                return (k.cls, k.fields.get("value"))
            if k.cls == "Ljava/lang/Class;":
                return ("Class", cls._to_dotted_name(k.fields.get("name")))
            if k.cls == "Ljava/io/File;":
                return ("File", k.fields.get("path"))
            if k.cls == "Ljava/time/LocalDate;":
                return ("LocalDate", int(k.fields.get("year", 0)), int(k.fields.get("month", 0)), int(k.fields.get("day", 0)))
        t = cls._txt(k)
        return t if t is not None else k

    @staticmethod
    def _map_put(a):
        if a and isinstance(a[0], JObject):
            m = a[0].fields.setdefault("map", {})
            raw_k = a[1] if len(a) > 1 else None
            key = AndroidStubs._mkey(raw_k)
            prev = m.get(key)
            m[key] = a[2] if len(a) > 2 else None
            a[0].fields.setdefault("__raw_keys", {})[key] = raw_k
            return prev if prev is not None else 0
        return 0

    @staticmethod
    def _map_put_if_absent(a):
        if a and isinstance(a[0], JObject):
            m = a[0].fields.setdefault("map", {})
            raw_k = a[1] if len(a) > 1 else None
            key = AndroidStubs._mkey(raw_k)
            if key in m and m[key] is not None:
                return m[key]
            m[key] = a[2] if len(a) > 2 else None
            a[0].fields.setdefault("__raw_keys", {})[key] = raw_k
            return None
        return None

    @staticmethod
    def _map_get(a):
        if a and isinstance(a[0], JObject):
            m = a[0].fields.get("map", {})
            return m.get(AndroidStubs._mkey(a[1]) if len(a) > 1 else None, 0)
        return 0

    def _cl_find_library(self, a):
        name = self._txt(a[1]) if len(a) > 1 else ""
        if not name:
            return None
        fs = getattr(self, "_shared_fs", None)
        if fs is None:
            return None
        nld = (getattr(self, "_app_info", None) or {}).get("nativeLibraryDir")
        pkg = getattr(self.profile, "package", None) or "com.example.app"
        cands = []
        if nld:
            cands.append(f"{nld.rstrip('/')}/lib{name}.so")
        cands.append(f"/data/app/{pkg}/lib/arm64/lib{name}.so")
        for cand in cands:
            if fs.lookup(cand) is not None:
                return JString(cand)
        return None

    def _reflect_array_new(self, a):
        cls_obj = a[0] if a else None
        ln = max(0, int(self._unbox_for_param(a[1], "I")) if len(a) > 1 else 0)
        edesc = self._desc_from_class_obj(cls_obj) or "Ljava/lang/Object;"
        default = 0 if edesc in ("I", "Z", "B", "S", "C", "J", "F", "D") else None
        return JArray("[" + edesc, [default] * ln)

    def _reflect_array_new_multi(self, a):
        cls_obj = a[0] if a else None
        dims = a[1].data if len(a) > 1 and isinstance(a[1], JArray) else [0]
        edesc = self._desc_from_class_obj(cls_obj) or "Ljava/lang/Object;"
        def _build(ds, prefix):
            n = max(0, int(ds[0])) if ds else 0
            if len(ds) <= 1:
                d = 0 if edesc in ("I", "Z", "B", "S", "C", "J", "F", "D") else None
                return JArray(prefix + edesc, [d] * n)
            return JArray(prefix * len(ds) + edesc, [_build(ds[1:], "[") for _ in range(n)])
        return _build(dims, "[")

    @staticmethod
    def _reflect_array_set(a):
        if len(a) >= 3 and isinstance(a[0], JArray):
            idx = int(a[1])
            if 0 <= idx < len(a[0].data):
                a[0].data[idx] = a[2]
        return None

    def _ex_init(self, a):
        this = a[0] if a else None
        if isinstance(this, JObject):
            if len(a) > 1:
                arg1 = a[1]
                if isinstance(arg1, JObject) and self._txt(arg1) is None:
                    this.fields["cause"] = arg1
                else:
                    this.fields["message"] = self._txt(arg1) or ""
            if len(a) > 2:
                this.fields["cause"] = a[2]
        return None

    def _seed_primitive_types(self, vm):
        """Seedet Wrapper.TYPE (Integer.TYPE, Character.TYPE, ...) fuer generische Reflection."""
        for wcls, pname, pdesc in (
            ("Ljava/lang/Boolean;", "boolean", "Z"),
            ("Ljava/lang/Byte;", "byte", "B"),
            ("Ljava/lang/Character;", "char", "C"),
            ("Ljava/lang/Short;", "short", "S"),
            ("Ljava/lang/Integer;", "int", "I"),
            ("Ljava/lang/Long;", "long", "J"),
            ("Ljava/lang/Float;", "float", "F"),
            ("Ljava/lang/Double;", "double", "D"),
            ("Ljava/lang/Void;", "void", "V"),
        ):
            vm.statics.setdefault(wcls, {})["TYPE"] = JObject(
                "Ljava/lang/Class;", {"name": pname, "primitive": pdesc}
            )
        vm.statics.setdefault("Ljava/nio/ByteOrder;", {}).update({
            "BIG_ENDIAN": JObject("Ljava/nio/ByteOrder;", {"order": "big", "_le": False}),
            "LITTLE_ENDIAN": JObject("Ljava/nio/ByteOrder;", {"order": "little", "_le": True}),
        })
        vm.statics.setdefault("Ljava/time/DayOfWeek;", {}).update({
            dname: JObject("Ljava/time/DayOfWeek;", {"__ordinal": idx, "__ename": dname, "day": idx + 1})
            for idx, dname in enumerate(("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"))
        })
        vm.statics.setdefault("Ljava/util/Locale;", {}).update({
            "GERMAN": JObject("Ljava/util/Locale;", {"lang": "de", "country": ""}),
            "GERMANY": JObject("Ljava/util/Locale;", {"lang": "de", "country": "DE"}),
            "ENGLISH": JObject("Ljava/util/Locale;", {"lang": "en", "country": ""}),
            "US": JObject("Ljava/util/Locale;", {"lang": "en", "country": "US"}),
            "UK": JObject("Ljava/util/Locale;", {"lang": "en", "country": "GB"}),
            "ROOT": JObject("Ljava/util/Locale;", {"lang": "en", "country": ""}),
            "DEFAULT": JObject("Ljava/util/Locale;", {"lang": "de", "country": "DE"}),
        })
        def _cs_obj(cname):
            return JObject("Ljava/nio/charset/Charset;", {"name": cname, "text": cname})
        vm.statics.setdefault("Ljava/nio/charset/StandardCharsets;", {}).update({
            "UTF_8": _cs_obj("UTF-8"),
            "ISO_8859_1": _cs_obj("ISO-8859-1"),
            "US_ASCII": _cs_obj("US-ASCII"),
            "UTF_16": _cs_obj("UTF-16"),
            "UTF_16BE": _cs_obj("UTF-16BE"),
            "UTF_16LE": _cs_obj("UTF-16LE"),
        })
        vm.statics.setdefault("Lkotlin/text/Charsets;", {}).update({
            "INSTANCE": JObject("Lkotlin/text/Charsets;", {}),
            "UTF_8": _cs_obj("UTF-8"),
            "ISO_8859_1": _cs_obj("ISO-8859-1"),
            "US_ASCII": _cs_obj("US-ASCII"),
            "UTF_16": _cs_obj("UTF-16"),
            "UTF_16BE": _cs_obj("UTF-16BE"),
            "UTF_16LE": _cs_obj("UTF-16LE"),
            "UTF_32": _cs_obj("UTF-32"),
            "UTF_32BE": _cs_obj("UTF-32BE"),
            "UTF_32LE": _cs_obj("UTF-32LE"),
        })
        vm.statics.setdefault("Lkotlin/coroutines/EmptyCoroutineContext;", {}).update({
            "INSTANCE": JObject("Lkotlin/coroutines/EmptyCoroutineContext;", {}),
        })
        vm.statics.setdefault("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {}).update({
            "COROUTINE_SUSPENDED": JObject("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {"name": "COROUTINE_SUSPENDED"}),
            "UNDECIDED": JObject("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {"name": "UNDECIDED"}),
            "RESUMED": JObject("Lkotlin/coroutines/intrinsics/CoroutineSingletons;", {"name": "RESUMED"}),
        })
        vm.statics.setdefault("Ljava/util/concurrent/TimeUnit;", {}).update({
            uname: JObject("Ljava/util/concurrent/TimeUnit;", {"__ename": uname, "__ordinal": idx, "_ns": ns})
            for idx, (uname, _mname, ns) in enumerate(_TIME_UNITS)
        })
        vm.statics.setdefault("Ljava/lang/String;", {}).setdefault(
            "CASE_INSENSITIVE_ORDER", JObject("Ljava/lang/String$CaseInsensitiveComparator;", {})
        )
        vm.statics.setdefault("Ljava/lang/Boolean;", {}).update({
            "TRUE": JObject("Ljava/lang/Boolean;", {"value": 1}),
            "FALSE": JObject("Ljava/lang/Boolean;", {"value": 0}),
        })
        _kunit = JObject("Lkotlin/Unit;", {})
        vm.statics.setdefault("Lkotlin/Unit;", {}).update({
            "INSTANCE": _kunit,
            "a": _kunit,
        })
        vm.statics.setdefault("Ljava/io/File;", {}).update({
            "separator": JString("/"),
            "pathSeparator": JString(":"),
            "separatorChar": ord("/"),
            "pathSeparatorChar": ord(":"),
        })
        vm.statics.setdefault("Ljava/util/Collections;", {}).update({
            "EMPTY_LIST": JObject("Ljava/util/ArrayList;", {"__items": [], "$items": []}),
            "EMPTY_SET": JObject("Ljava/util/LinkedHashSet;", {"__items": [], "$items": []}),
            "EMPTY_MAP": JObject("Ljava/util/HashMap;", {"map": {}}),
        })
        try:
            from core.java.virtual_clock import seed_calendar_statics
            seed_calendar_statics(vm)
        except Exception:
            pass

    def seed_framework_statics(self, vm):
        """Framework-Statics (android.os.Build, Build$VERSION, Wrapper.TYPE, ...) setzen."""
        self._seed_primitive_types(vm)
        for cls, fields in self.profile.build_statics().items():
            vm.statics.setdefault(cls, {}).update(fields)
        at_st = vm.statics.setdefault("Landroid/os/AsyncTask;", {})
        at_st.setdefault("THREAD_POOL_EXECUTOR", JObject("Ljava/util/concurrent/Executor;", {"_name": "THREAD_POOL"}))
        at_st.setdefault("SERIAL_EXECUTOR", JObject("Ljava/util/concurrent/Executor;", {"_name": "SERIAL"}))
        st_cls = "Landroid/widget/ImageView$ScaleType;"
        st_map = vm.statics.setdefault(st_cls, {})
        for _idx, _st_name in enumerate((
            "MATRIX", "FIT_XY", "FIT_START", "FIT_CENTER",
            "FIT_END", "CENTER", "CENTER_CROP", "CENTER_INSIDE",
        )):
            st_map.setdefault(_st_name, JObject(st_cls, {"__ename": _st_name, "__ordinal": _idx}))
        for _m_cls in ("Landroid/graphics/BlendMode;", "Landroid/graphics/PorterDuff$Mode;"):
            _m_map = vm.statics.setdefault(_m_cls, {})
            for _idx, _mn in enumerate((
                "CLEAR", "SRC", "DST", "SRC_OVER", "DST_OVER", "SRC_IN", "DST_IN",
                "SRC_OUT", "DST_OUT", "SRC_ATOP", "DST_ATOP", "XOR", "MULTIPLY", "SCREEN",
            )):
                _m_map.setdefault(_mn, JObject(_m_cls, {"__ename": _mn, "__ordinal": _idx}))
        try:
            from core.java.sdk_breadth_g15 import seed_g15_statics
            seed_g15_statics(self, vm)
        except ImportError:
            pass

    def set_profile(self, profile, vm=None):
        """Geraete-Profil setzen (z. B. mit Paketname aus dem Manifest) und Statics neu seeden."""
        self.profile = profile or DeviceProfile()
        self._apply_profile_clock()
        vm = vm or self.vm
        if vm is not None:
            self.seed_framework_statics(vm)
            try:
                from core.java.sqlite_prefs_bridge import attach_g3_vm_hook
                attach_g3_vm_hook(self, vm)
            except Exception:
                pass
            for _hmod, _hfn in (
                ("core.java.engine_stubs_g14", "attach_g14_vm_hook"),
                ("core.java.nav_rx_paging_g8", "attach_g8_vm_hook"),
            ):
                try:
                    _m = __import__(_hmod, fromlist=[_hfn])
                    getattr(_m, _hfn)(self, vm)
                except Exception:
                    pass

    def _install_profile_consts(self):
        """Faithful, oft als Integritaets-Konstante missbrauchte android.*-Funktionen (generisch)."""
        # Process.getThreadPriority(tid) -> Profilwert
        self.register("Landroid/os/Process;", "getThreadPriority", "(I)I",
                      lambda a: int(self.profile.get("thread_priority", 0)))
        self.register("Landroid/widget/ExpandableListView;", "getPackedPositionChild", "(J)I",
                      self._packed_child)

    @staticmethod
    def _packed_group(a):
        packed = int(a[0]) if a and isinstance(a[0], int) else 0
        if packed == 0xFFFFFFFF or packed == -1:
            return -1
        return int((packed & 0x7FFFFFFF00000000) >> 32)

    @staticmethod
    def _packed_child(a):
        packed = int(a[0]) if a and isinstance(a[0], int) else 0
        if (packed >> 62) & 0x1 == 0:   # PACKED_POSITION_TYPE_GROUP -> kein Child
            return -1
        return int(packed & 0xFFFFFFFF)
