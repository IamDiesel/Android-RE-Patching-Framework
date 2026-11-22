# -*- coding: utf-8 -*-
"""core.java.dalvik — Dalvik-Register-VM (J1-Vollausbau).

Register-Interpreter mit breitem Kern-Opcode-Satz, Objektmodell (Objekte/Arrays/Strings, via
`core.java.objects`), statischen Feldern, **Intra-Dalvik-Methodenaufruf** (Methoden-Registry +
Frame-Stack, invoke-static/direct/virtual/super/interface) und der Bruecke **Dalvik->native**
(J0 `JavaToNativeBridge`, `invoke-native`). Das Smali/DEX-Frontend (`core.java.smali`) erzeugt das
hier ausgefuehrte Tupel-IR. Unbekannter Opcode -> `DalvikUnknownOpcode` (Coverage-Loop, analog FR-4).

Werte in Registern: Python-``int`` (Primitive, bei Arithmetik auf 32 Bit maskiert) oder eine Referenz
(`JString`/`JObject`/`JArray`) bzw. ``None`` (= null). Zielagnostisch, keine App-Spezifika.

Tupel-IR (Auszug):
  ("const", dst, lit) · ("const-string", dst, text) · ("move", dst, src) · ("move-result", dst)
  ("add-int", dst, a, b) · ("add-int/2addr", dst, src) · ("add-int/lit", dst, a, lit) · ("neg-int", dst, src)
  ("if-eq", a, b, tgt) · ("if-eqz", a, tgt) · ("goto", tgt)
  ("new-instance", dst, cls) · ("iget", dst, obj, cls, field) · ("iput", src, obj, cls, field)
  ("sget", dst, cls, field) · ("sput", src, cls, field)
  ("new-array", dst, size, type) · ("array-length", dst, arr) · ("aget", dst, arr, idx) · ("aput", src, arr, idx)
  ("invoke-static", (regs...), cls, name, sig) · ("invoke-virtual", (regs...), cls, name, sig)
  ("invoke-native", dst, fnptr, this_cls, *argregs)
"""
from __future__ import annotations

import time
from core.java.deviation_journal import DebugStop, DeviationJournal
from core.java.objects import JString, JObject, JArray, default_for

_U32 = (1 << 32) - 1


def _i32(x: int) -> int:
    x &= _U32
    return x - (1 << 32) if x >= (1 << 31) else x


def _u32(x: int) -> int:
    return x & _U32


def _zero(v) -> bool:
    if v is None:
        return True
    if isinstance(v, bool):
        return not v
    if isinstance(v, int):
        return (v & _U32) == 0
    if isinstance(v, float):
        return v == 0.0
    return False


def _as_i(v) -> int:
    return _i32(v) if isinstance(v, int) else 0


def _norm_cls_dotted(raw) -> str:
    """Normalisiert JVM/Dalvik-Klassennamen ('Lfoo/Bar;', 'foo/Bar', '[Lfoo/Bar;') in Java-Punktform."""
    s = str(raw or "")
    if s.startswith("L") and s.endswith(";"):
        return s[1:-1].replace("/", ".")
    return s.replace("/", ".")


def _eq(a, b) -> bool:
    if (a is None or (isinstance(a, int) and _zero(a))) and (b is None or (isinstance(b, int) and _zero(b))):
        return True
    if isinstance(a, int) and isinstance(b, int):
        return _u32(a) == _u32(b)
    if (isinstance(a, JObject) and isinstance(b, JObject)
            and a.cls == "Ljava/lang/Class;" and b.cls == "Ljava/lang/Class;"):
        return _norm_cls_dotted(a.fields.get("name", "")) == _norm_cls_dotted(b.fields.get("name", ""))
    return a is b


def _idiv(a, b) -> int:
    a = _i32(a); b = _i32(b)
    if b == 0:
        raise DalvikException("java.lang.ArithmeticException: divide by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _irem(a, b) -> int:
    a = _i32(a); b = _i32(b)
    if b == 0:
        raise DalvikException("java.lang.ArithmeticException: divide by zero")
    r = abs(a) % abs(b)
    return -r if a < 0 else r


_BIN = {
    "add": lambda a, b: _i32(a) + _i32(b),
    "sub": lambda a, b: _i32(a) - _i32(b),
    "mul": lambda a, b: _i32(a) * _i32(b),
    "div": _idiv,
    "rem": _irem,
    "and": lambda a, b: a & b,
    "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b,
    "shl": lambda a, b: _i32(a) << (b & 31),
    "shr": lambda a, b: _i32(a) >> (b & 31),
    "ushr": lambda a, b: (a & _U32) >> (b & 31),
}

import math as _math
import struct as _struct


def _f32(x):
    """Operand als float32 interpretieren: Python-float direkt, int als IEEE-Rohbits."""
    if isinstance(x, float):
        return x
    return _struct.unpack("<f", _struct.pack("<I", int(x) & _U32))[0]


def _f64(x):
    """Operand als float64 interpretieren: Python-float direkt, int als IEEE-Rohbits."""
    if isinstance(x, float):
        return x
    return _struct.unpack("<d", _struct.pack("<Q", int(x) & _L))[0]


def _f2i(f):
    """float/double -> int mit Java-Semantik: NaN->0, Saettigung auf int-Bereich, Trunkierung."""
    if f != f:
        return 0
    if f >= 2147483647.0:
        return 2147483647
    if f <= -2147483648.0:
        return -2147483648
    return int(f)


def _f2l(f):
    """float/double -> long mit Java-Semantik: NaN->0, Saettigung auf long-Bereich, Trunkierung."""
    if f != f:
        return 0
    if f >= 9223372036854775807.0:
        return 9223372036854775807
    if f <= -9223372036854775808.0:
        return -9223372036854775808
    return int(f)

_L = (1 << 64) - 1
_ARRAY_OOM_LIMIT = 64_000_000   # groessere Allokation -> OutOfMemoryError (statt Python-MemoryError)


def _i64(x):
    x &= _L
    return x - (1 << 64) if x & (1 << 63) else x


def _u64(x):
    return x & _L


def _ldiv(a, b):
    a = _i64(a); b = _i64(b)
    if b == 0:
        raise DalvikException("java.lang.ArithmeticException: divide by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _lrem(a, b):
    a = _i64(a); b = _i64(b)
    if b == 0:
        raise DalvikException("java.lang.ArithmeticException: divide by zero")
    r = abs(a) % abs(b)
    return -r if a < 0 else r


_LBIN = {
    "add": lambda a, b: _i64(a) + _i64(b), "sub": lambda a, b: _i64(a) - _i64(b),
    "mul": lambda a, b: _i64(a) * _i64(b), "div": _ldiv, "rem": _lrem,
    "and": lambda a, b: a & b, "or": lambda a, b: a | b, "xor": lambda a, b: a ^ b,
    "shl": lambda a, b: _i64(a) << (b & 63), "shr": lambda a, b: _i64(a) >> (b & 63),
    "ushr": lambda a, b: (a & _L) >> (b & 63),
}


_FBASES = ("add", "sub", "mul", "div", "rem")   # echte Float/Double-Arithmetik (cmp* faellt durch)


def _fbin(base, a, b, wide=False):
    conv = _f64 if wide else _f32
    a = conv(a); b = conv(b)
    if base == "add": return a + b
    if base == "sub": return a - b
    if base == "mul": return a * b
    if base == "div":
        try:
            return a / b
        except ZeroDivisionError:
            return float("nan") if a == 0 else _math.copysign(float("inf"), a) * _math.copysign(1.0, b)
    if base == "rem":
        try:
            return _math.fmod(a, b)
        except (ZeroDivisionError, ValueError):
            return float("nan")
    raise KeyError(base)


def _cmp(op, x, y):
    if "float" in op or "double" in op:
        conv = _f64 if "double" in op else _f32
        x = conv(x); y = conv(y)
        if x != x or y != y:          # NaN: cmpl->-1, cmpg->+1
            return -1 if op.startswith("cmpl") else 1
        return -1 if x < y else (1 if x > y else 0)
    x = _i64(x); y = _i64(y)
    return -1 if x < y else (1 if x > y else 0)


_JDK_THROWABLE_SUPER = {
    "Ljava/lang/NullPointerException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/ArithmeticException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/NegativeArraySizeException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/ArrayIndexOutOfBoundsException;": "Ljava/lang/IndexOutOfBoundsException;",
    "Ljava/lang/StringIndexOutOfBoundsException;": "Ljava/lang/IndexOutOfBoundsException;",
    "Ljava/lang/IndexOutOfBoundsException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/ClassCastException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/IllegalStateException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/IllegalArgumentException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/NumberFormatException;": "Ljava/lang/IllegalArgumentException;",
    "Ljava/lang/UnsupportedOperationException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/ConcurrentModificationException;": "Ljava/lang/RuntimeException;",
    "Ljava/lang/ClassNotFoundException;": "Ljava/lang/Exception;",
    "Ljava/io/IOException;": "Ljava/lang/Exception;",
    "Ljava/io/FileNotFoundException;": "Ljava/io/IOException;",
    "Ljava/io/UnsupportedEncodingException;": "Ljava/io/IOException;",
    "Ljava/io/InterruptedIOException;": "Ljava/io/IOException;",
    "Ljava/lang/InterruptedException;": "Ljava/lang/Exception;",
    "Ljava/lang/CloneNotSupportedException;": "Ljava/lang/Exception;",
    "Ljava/lang/ReflectiveOperationException;": "Ljava/lang/Exception;",
    "Ljava/lang/NoSuchMethodException;": "Ljava/lang/ReflectiveOperationException;",
    "Ljava/lang/NoSuchFieldException;": "Ljava/lang/ReflectiveOperationException;",
    "Ljava/lang/IllegalAccessException;": "Ljava/lang/ReflectiveOperationException;",
    "Ljava/lang/InstantiationException;": "Ljava/lang/ReflectiveOperationException;",
    "Ljava/lang/LinkageError;": "Ljava/lang/Error;",
    "Ljava/lang/NoClassDefFoundError;": "Ljava/lang/LinkageError;",
    "Ljava/lang/ExceptionInInitializerError;": "Ljava/lang/LinkageError;",
    "Ljava/lang/VirtualMachineError;": "Ljava/lang/Error;",
    "Ljava/lang/RuntimeException;": "Ljava/lang/Exception;",
    "Ljava/lang/Exception;": "Ljava/lang/Throwable;",
    "Ljava/lang/StackOverflowError;": "Ljava/lang/Error;",
    "Ljava/lang/OutOfMemoryError;": "Ljava/lang/Error;",
    "Ljava/lang/AssertionError;": "Ljava/lang/Error;",
    "Ljava/lang/Error;": "Ljava/lang/Throwable;",
    "Ljava/lang/Throwable;": "Ljava/lang/Object;",
}

_EXC_MSG_MAP = (
    ("NullPointer", "Ljava/lang/NullPointerException;"),
    ("OutOfMemory", "Ljava/lang/OutOfMemoryError;"),
    ("divide by zero", "Ljava/lang/ArithmeticException;"),
    ("ArithmeticException", "Ljava/lang/ArithmeticException;"),
    ("NegativeArraySize", "Ljava/lang/NegativeArraySizeException;"),
    ("ArrayIndexOutOfBounds", "Ljava/lang/ArrayIndexOutOfBoundsException;"),
    ("IndexOutOfBounds", "Ljava/lang/IndexOutOfBoundsException;"),
    ("ClassCast", "Ljava/lang/ClassCastException;"),
    ("IllegalState", "Ljava/lang/IllegalStateException;"),
    ("IllegalArgument", "Ljava/lang/IllegalArgumentException;"),
    ("NumberFormat", "Ljava/lang/NumberFormatException;"),
)


def _exc_type_from_msg(msg):
    """Synthetischer Exception-Typ fuer VM-interne DalvikExceptions (anhand der Meldung)."""
    for kw, desc in _EXC_MSG_MAP:
        if kw in msg:
            return desc
    return "Ljava/lang/RuntimeException;"


class DalvikException(Exception):
    """Vom VM ausgeworfene Java-artige Ausnahme (z. B. ArithmeticException)."""
    def __init__(self, msg, payload=None):
        self.msg = msg
        self.payload = payload   # tatsaechlich geworfenes Objekt (throw-Opcode) -> move-exception
        super().__init__(msg)


class VmBudgetExceeded(DalvikException):
    """VM-Ressourcen-/Budgetgrenze (StepBudget, WallBudget, StackOverflow) — nicht durch .catch/lenient fangbar."""


class DalvikUnknownOpcode(Exception):
    """Ein (noch) nicht implementierter Dalvik-Opcode — speist den Opcode-Coverage-Loop (J1)."""
    def __init__(self, op, pc):
        self.op = op
        self.pc = pc
        super().__init__(f"Dalvik-Opcode nicht implementiert: {op!r} @pc={pc}")


class MethodDef:
    """Registrierte Dalvik-Methode: Tupel-IR + Registerzahl + Parameter-Platzierung."""
    __slots__ = ("cls", "name", "sig", "nregs", "nargs", "insns", "static", "handlers")

    def __init__(self, cls, name, sig, insns, nregs, nargs, static=False, handlers=None):
        self.cls = cls
        self.name = name
        self.sig = sig
        self.insns = list(insns)
        self.nregs = max(int(nregs), 1)
        self.nargs = int(nargs)
        self.static = bool(static)
        self.handlers = list(handlers or [])   # [(start_pc,end_pc,exc_type|None,handler_pc)]

    @property
    def param_base(self) -> int:
        return max(self.nregs - self.nargs, 0)


class Frame:
    __slots__ = ("v", "pc", "result", "md", "cur", "exc")

    def __init__(self, nregs: int, args=(), param_base: int = 0):
        self.v = [0] * max(1, nregs)
        for i, a in enumerate(args):
            self.v[param_base + i] = a if not isinstance(a, bool) else (1 if a else 0)
        self.pc = 0
        self.result = None
        self.md = None
        self.cur = 0
        self.exc = None


class DalvikVM:
    """Register-VM-Kern.

    ``bridge`` = optionale `JavaToNativeBridge` fuer ``invoke-native``.
    ``registry`` = (cls,name,sig) -> `MethodDef` fuer Intra-Dalvik-Aufrufe.
    ``statics`` = cls -> {field: value}. ``coverage`` zaehlt ausgefuehrte Opcodes;
    ``missing`` sammelt nicht aufgeloeste Methodenaufrufe (Coverage-Loop)."""

    def __init__(self, bridge=None, runtime=None, max_depth: int = 64):
        self.bridge = bridge
        self.runtime = runtime
        self.coverage = {}
        self.registry = {}
        self.statics = {}
        self.missing = []
        self._missing_idx = {}
        self.deviations = DeviationJournal()
        self.method_overrides = {}     # AP1.3: (cls,name,sig)->Wert/Callable: Smali umgehen (Glass-Box-Override)
        self._method_hooks = []        # Generische Prefixes/Interfaces vor Smali-Resolution (z.B. Compose-UI-Bruecke)
        self.max_depth = int(max_depth)
        self._depth = 0
        self._frames = []   # aktiver Call-Stack (fuer lenient vs. .catch-Vorrang)
        self._dbg = None   # optionaler Debug-Hook (JavaDebugger); Default aus
        self.class_provider = None   # optionale Lazy-Klassenquelle (X1); Default aus
        self.step_budget = None   # optionales Instruktions-Budget (bounded run); None = unbegrenzt
        self.wall_deadline = None # optionaler time.monotonic()-Deadline fuer bounded run
        self.steps = 0            # ausgefuehrte Instruktionen gesamt (fuer Budget + Report)
        self.last_method = None   # zuletzt betretene Methode (cls,name,sig) -- "wo gestoppt"
        self.lenient = False      # Tolerant: Fehler in AUFGERUFENEN Methoden abfangen -> null
        self.lenient_errors = []  # abgefangene Aufruf-Fehler (cls,name,sig,error)
        self._clinit_done = set() # Klassen, deren <clinit> lief (Dalvik: Init bei Erstnutzung)
        self._clinit_errors = []  # abgefangene Fehler aus <clinit>-Laeufen (bounded run stabil)
        self._class_cache = {}
        self._inject_fields_cache = {}
        self._di_singletons = {}
        self._di_depth = 0
        self.last_trap = None
        self.on_trap = None
        self._type_cache_sig = None
        self._lazy_done = set()
        self._super_cache = {}
        self._iface_cache = {}
        self._subclass_cache = {}
        self._class_kind_cache = {}

    def _sync_type_caches(self):
        """Leert abgeleitete Typ-/Hierarchie-Caches bei Wechsel von Provider, Patches oder Loader."""
        prov = self.class_provider
        patches = getattr(prov, "patches", None) if prov is not None else None
        patch_sig = (
            tuple((k, id(v)) for k, v in sorted(patches.items()))
            if isinstance(patches, dict) and patches else ()
        )
        sig = (
            id(prov),
            getattr(prov, "_patch_version", 0) if prov is not None else 0,
            patch_sig,
            tuple(id(d) for d in self._iter_dynamic_dcls()),
            id(getattr(self.runtime, "loader", None)),
        )
        if sig != self._type_cache_sig:
            self._type_cache_sig = sig
            self._lazy_done.clear()
            self._super_cache.clear()
            self._iface_cache.clear()
            self._subclass_cache.clear()
            self._class_kind_cache.clear()

    def intern_class(self, desc_or_name):
        """Liefert ein normalisiertes Class-Singleton fuer Identitaetsvergleiche (if-eq Class)."""
        raw = str(desc_or_name or "")
        dotted = _norm_cls_dotted(raw)
        cached = self._class_cache.get(dotted)
        if cached is None:
            stored = raw.replace(".", "/") if raw.startswith("[") else dotted
            cached = JObject("Ljava/lang/Class;", {"name": stored})
            self._class_cache[dotted] = cached
        return cached

    # ---- Registrierung / Einstieg -----------------------------------------
    def register_method(self, cls, name, sig, insns, nregs, nargs, static=False) -> MethodDef:
        md = MethodDef(cls, name, sig, insns, nregs, nargs, static)
        self.registry[(cls, name, sig)] = md
        self._class_kind_cache.pop(cls, None)
        if self._subclass_cache:
            self._subclass_cache.clear()
        return md

    def register(self, md: MethodDef) -> MethodDef:
        self.registry[(md.cls, md.name, md.sig)] = md
        self._class_kind_cache.pop(md.cls, None)
        if self._subclass_cache:
            self._subclass_cache.clear()
        return md

    def run_method(self, insns, nregs: int, args=()):
        """Hand-IR direkt ausfuehren: Argumente ab v0 (Scaffold-Konvention)."""
        return self._exec(Frame(nregs, args, param_base=0), list(insns))

    def invoke(self, cls, name, sig, args=()):
        """Registrierte Methode aufrufen (Argumente in die Parameter-Register nach Smali-Konvention)."""
        md = self.registry.get((cls, name, sig))
        if md is None:
            self._lazy_class(cls)
            md = self.registry.get((cls, name, sig))
        if md is None and args and isinstance(args[0], JObject):
            rcls = getattr(args[0], "cls", None) or cls
            md = self._resolve_method(rcls, cls, name, sig)
        if md is None:
            raise KeyError(f"Methode nicht registriert: {cls}->{name}{sig}")
        return self._run_mdef(md, self._slots_from_logical(sig, md.static, list(args)))

    def invoke_virtual(self, obj, name, sig, args=()):
        """Virtueller Methodenaufruf ueber Superklassen, Interfaces und Runtime-Stubs (ohne KeyError)."""
        if obj is None:
            return None
        rcls = obj.cls if isinstance(obj, JObject) else ("Ljava/lang/String;" if isinstance(obj, JString) else "Ljava/lang/Object;")
        n_params = len(self._sig_param_kinds(sig))
        raw = list(args) if args else []
        if not raw:
            logical = [obj]
        elif len(raw) == n_params and raw[0] is not obj:
            logical = [obj] + raw
        else:
            logical = raw
        if self._method_hooks:
            for _hk in list(self._method_hooks):
                handled_h, ret_h = _hk(rcls, rcls, name, sig, logical, False, self)
                if handled_h:
                    return ret_h
        md = self._resolve_method(rcls, rcls, name, sig)
        if md is not None:
            return self._run_mdef(md, self._slots_from_logical(sig, md.static, logical))
        if self.runtime is not None:
            wants = self._build_wants(rcls, rcls)
            handled, ret = self._resolve_runtime_invoke(wants, rcls, rcls, name, sig, logical, False)
            if handled:
                return ret
        return None

    # ---- Klasseninitialisierung (Dalvik: <clinit> bei Erstnutzung) --------
    def _ensure_class_init(self, cls):
        """Fuehrt <clinit>()V einer Klasse genau einmal aus, bevor ihre Methoden/Statics benutzt
        werden (JVM/Dalvik-Semantik). Faithful: wir fuehren den app-eigenen Initializer aus und faken
        keine Statics (z.B. Obfuskator-Flattening-States wie $11). Ohne <clinit> passiert nichts.
        Re-Entrancy: die Klasse gilt als initialisiert, sobald der Lauf BEGINNT."""
        if cls is None or cls in self._clinit_done:
            return
        key = (cls, "<clinit>", "()V")
        md = self.registry.get(key)
        if md is None and self.class_provider is not None:
            try:
                prov = self.class_provider.get_class(cls)
            except Exception:
                prov = None
            if prov:
                for pm in prov:
                    self.registry.setdefault((pm.cls, pm.name, pm.sig), pm)
                md = self.registry.get(key)
        self._clinit_done.add(cls)   # VOR dem Lauf markieren (<clinit> darf eigene Statics setzen)
        self._seed_field_defaults(cls)
        if md is None:
            return
        try:
            self._run_mdef(md, [])
        except Exception as e:
            if isinstance(e, (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)):
                raise
            self._clinit_errors.append({"cls": cls, "error": repr(e)})

    def _seed_field_defaults(self, cls):
        """Initialisiert Smali-Static-Feld-Defaults vor einem direkten oder impliziten <clinit>."""
        prov_fd = self.class_provider
        if prov_fd is None or not hasattr(prov_fd, "get_field_defaults"):
            return
        try:
            defs = prov_fd.get_field_defaults(cls)
        except Exception:
            defs = None
        if defs:
            st = self.statics.setdefault(cls, {})
            for _fn, _fv in defs.items():
                st.setdefault(_fn, _fv)

    # ---- Methodenlauf ------------------------------------------------------
    def _run_mdef(self, md: MethodDef, args):
        if md is not None:
            if md.name == "<clinit>" and md.cls:
                if md.cls not in self._clinit_done:
                    self._seed_field_defaults(md.cls)
                self._clinit_done.add(md.cls)
            else:
                self._ensure_class_init(md.cls)
        if self._depth >= self.max_depth:
            raise VmBudgetExceeded("StackOverflow (max_depth)")
        self._depth += 1
        fr = Frame(md.nregs, args, param_base=md.param_base)
        fr.md = md
        self._frames.append(fr)
        self.last_method = (md.cls, md.name, md.sig)
        try:
            return self._exec(fr, md.insns)
        finally:
            self._depth -= 1
            if self._frames:
                self._frames.pop()

    # ---- Statik-Helfer -----------------------------------------------------
    def _sget(self, cls, field):
        self._ensure_class_init(cls)
        return self.statics.get(cls, {}).get(field, 0)

    def _sput(self, cls, field, val):
        self._ensure_class_init(cls)
        self.statics.setdefault(cls, {})[field] = val

    # ---- Interpreter -------------------------------------------------------
    def _exec(self, fr: Frame, insns):
        n = len(insns)
        v = fr.v
        cov = self.coverage
        while True:
            try:
                while 0 <= fr.pc < n:
                    ins = insns[fr.pc]
                    fr.cur = fr.pc
                    op = ins[0]
                    a = ins[1:]
                    fr.pc += 1
                    self.steps += 1
                    if self.step_budget is not None and self.steps > self.step_budget:
                        raise VmBudgetExceeded("StepBudget ueberschritten (%d Schritte)" % self.step_budget)
                    if (self.steps & 4095) == 0 and self.wall_deadline is not None:
                        if time.monotonic() >= self.wall_deadline:
                            raise VmBudgetExceeded("WallBudget ueberschritten")
                    cov[op] = cov.get(op, 0) + 1
                    if self._dbg is not None:
                        self._dbg.on_step(self, fr, insns, ins, op, fr.pc - 1)

                    # ---- Konstanten / Moves -------------------------------------
                    if op == "nop":
                        continue
                    if op in ("const", "const/4", "const/16", "const/high16", "const-wide",
                              "const-wide/16", "const-wide/32", "const-wide/high16"):
                        # voller 64-bit-Wert: int-Ops maskieren via _i32 auf 32 bit, long/cmp-long nutzen 64 bit.
                        # (Das Frontend kollabiert const/const-wide auf "const" und liefert bereits 64-bit-maskiert.)
                        v[a[0]] = _u64(int(a[1]))
                    elif op == "const-string" or op == "const-string/jumbo":
                        v[a[0]] = JString(a[1])
                    elif op == "const-class":
                        v[a[0]] = self.intern_class(a[1])
                    elif op in ("move", "move/from16", "move/16", "move-object",
                                "move-object/from16", "move-object/16", "move-wide", "move-wide/from16"):
                        v[a[0]] = v[a[1]]
                    elif op in ("move-result", "move-result-object", "move-result-wide"):
                        v[a[0]] = fr.result
                    elif op == "move-exception":
                        v[a[0]] = getattr(fr, "exc", None)
                    # ---- Return --------------------------------------------------
                    elif op == "return":
                        return _i32(v[a[0]]) if isinstance(v[a[0]], int) else v[a[0]]
                    elif op == "return-wide":
                        return _i64(v[a[0]]) if isinstance(v[a[0]], int) else v[a[0]]
                    elif op == "return-object":
                        return v[a[0]]
                    elif op == "return-void":
                        return None
                    # ---- Casts (vor den arithmetischen Suffix-Zweigen) ----------
                    elif "-to-" in op:
                        src = v[a[1]]
                        if op == "int-to-byte":
                            x = _i32(src) & 0xff; v[a[0]] = _u32(x - 0x100 if x & 0x80 else x)
                        elif op == "int-to-short":
                            x = _i32(src) & 0xffff; v[a[0]] = _u32(x - 0x10000 if x & 0x8000 else x)
                        elif op == "int-to-char":
                            v[a[0]] = _u32(_i32(src) & 0xffff)
                        elif op == "long-to-int":
                            v[a[0]] = _u32(src if isinstance(src, int) else int(src))
                        elif op in ("int-to-long",):
                            v[a[0]] = _u64(_i32(src))
                        elif op in ("int-to-float", "int-to-double"):
                            v[a[0]] = float(_i32(src))
                        elif op in ("long-to-float", "long-to-double"):
                            v[a[0]] = float(_i64(src) if isinstance(src, int) else src)
                        elif op == "float-to-int":
                            v[a[0]] = _u32(_f2i(_f32(src)))
                        elif op == "double-to-int":
                            v[a[0]] = _u32(_f2i(_f64(src)))
                        elif op == "float-to-long":
                            v[a[0]] = _u64(_f2l(_f32(src)))
                        elif op == "double-to-long":
                            v[a[0]] = _u64(_f2l(_f64(src)))
                        elif op == "float-to-double":
                            v[a[0]] = _f32(src)                      # float32-Wert als double
                        elif op == "double-to-float":
                            v[a[0]] = _struct.unpack("<f", _struct.pack("<f", _f64(src)))[0]
                        else:
                            v[a[0]] = src
                    # ---- Arithmetik int (lit/2addr/3addr) ----------------------
                    elif op.endswith("/lit8") or op.endswith("/lit16") or op.endswith("/lit"):
                        base = op.split("-int", 1)[0]
                        if base == "rsub":
                            v[a[0]] = _u32(_i32(int(a[2])) - _i32(v[a[1]]))
                        else:
                            v[a[0]] = _u32(_BIN[base](v[a[1]], int(a[2])))
                    elif op.endswith("-int/2addr"):
                        base = op.split("-", 1)[0]
                        v[a[0]] = _u32(_BIN[base](v[a[0]], v[a[1]]))
                    elif op.endswith("-int") and op.split("-int", 1)[0] in _BIN:
                        base = op.split("-int", 1)[0]
                        v[a[0]] = _u32(_BIN[base](v[a[1]], v[a[2]]))
                    # ---- Arithmetik long -------------------------------------
                    elif op.endswith("-long/2addr"):
                        base = op.split("-", 1)[0]
                        v[a[0]] = _u64(_LBIN[base](v[a[0]], v[a[1]]))
                    elif op.endswith("-long") and op.split("-long", 1)[0] in _LBIN:
                        base = op.split("-long", 1)[0]
                        v[a[0]] = _u64(_LBIN[base](v[a[1]], v[a[2]]))
                    # ---- Arithmetik float/double -----------------------------
                    elif (op.endswith("-float/2addr") or op.endswith("-double/2addr")) \
                            and op.split("-", 1)[0] in _FBASES:
                        base = op.split("-", 1)[0]
                        v[a[0]] = _fbin(base, v[a[0]], v[a[1]], wide=("double" in op))
                    elif (op.endswith("-float") or op.endswith("-double")) \
                            and op.split("-", 1)[0] in _FBASES:
                        base = op.split("-", 1)[0]
                        v[a[0]] = _fbin(base, v[a[1]], v[a[2]], wide=("double" in op))
                    # ---- Vergleiche (cmp*) -----------------------------------
                    elif op in ("cmp-long", "cmpl-float", "cmpg-float", "cmpl-double", "cmpg-double"):
                        v[a[0]] = _cmp(op, v[a[1]], v[a[2]])
                    # ---- Unary ------------------------------------------------
                    elif op == "neg-int":
                        v[a[0]] = _u32(-_i32(v[a[1]]))
                    elif op == "not-int":
                        v[a[0]] = _u32(~_i32(v[a[1]]))
                    elif op == "neg-long":
                        v[a[0]] = _u64(-_i64(v[a[1]]))
                    elif op == "not-long":
                        v[a[0]] = _u64(~_i64(v[a[1]]))
                    elif op in ("neg-float", "neg-double"):
                        v[a[0]] = -(_f64(v[a[1]]) if op == "neg-double" else _f32(v[a[1]]))
                    # ---- fill-array-data / switch -----------------------------
                    elif op == "fill-array-data":
                        arr = v[a[0]]
                        if isinstance(arr, JArray):
                            vals = a[1]
                            for i in range(min(len(vals), len(arr.data))):
                                arr.data[i] = vals[i]
                    elif op == "packed-switch":
                        key = _i32(v[a[0]]) if isinstance(v[a[0]], int) else 0
                        first, tgts = a[1], a[2]
                        j = key - first
                        if 0 <= j < len(tgts):
                            fr.pc = tgts[j]
                    elif op == "sparse-switch":
                        key = _i32(v[a[0]]) if isinstance(v[a[0]], int) else 0
                        keys, tgts = a[1], a[2]
                        for ki, kk in enumerate(keys):
                            if kk == key:
                                fr.pc = tgts[ki]; break
                    # ---- throw ------------------------------------------------
                    elif op == "throw":
                        _tv = v[a[0]]
                        if not isinstance(_tv, JObject):
                            _tv = JObject("Ljava/lang/NullPointerException;", {"msg": "throw null"})
                        raise DalvikException("throw %r" % (_tv,), payload=_tv)
                    # ---- Verzweigungen ------------------------------------------
                    elif op in ("if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le"):
                        x, y, tgt = v[a[0]], v[a[1]], int(a[2])
                        if _branch2(op, x, y):
                            fr.pc = tgt
                    elif op in ("if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez"):
                        x, tgt = v[a[0]], int(a[1])
                        if _branch1(op, x):
                            fr.pc = tgt
                    elif op in ("goto", "goto/16", "goto/32"):
                        tgt = int(a[0])
                        if tgt < fr.cur and self._try_fast_byte_hash_loop(fr, insns, tgt):
                            continue
                        fr.pc = tgt
                    # ---- Objekte / Felder ---------------------------------------
                    elif op == "new-instance":
                        v[a[0]] = JObject(a[1])
                    elif op.startswith("iget"):
                        obj = v[a[1]]
                        if isinstance(obj, JObject):
                            val = obj.fields.get(a[3], 0)
                            if op == "iget-object" and (val == 0 or val is None) and self.class_provider is not None:
                                val = self._maybe_lazy_inject(obj, a[2], a[3], val)
                            v[a[0]] = val
                        else:
                            v[a[0]] = 0
                    elif op.startswith("iput"):
                        obj = v[a[1]]
                        if isinstance(obj, JObject):
                            obj.fields[a[3]] = v[a[0]]
                    elif op.startswith("sget"):
                        v[a[0]] = self._sget(a[1], a[2])
                    elif op.startswith("sput"):
                        self._sput(a[1], a[2], v[a[0]])
                    # ---- Arrays --------------------------------------------------
                    elif op == "new-array":
                        size = _i32(v[a[1]])
                        if size < 0 or size > _ARRAY_OOM_LIMIT:
                            _md = fr.md
                            self.last_trap = {
                                "kind": "neg_array" if size < 0 else "oom_array",
                                "size": size,
                                "cls": _md.cls if _md is not None else None,
                                "method": _md.name if _md is not None else None,
                                "sig": _md.sig if _md is not None else None,
                                "pc": fr.cur,
                            }
                            if callable(self.on_trap):
                                try:
                                    self.on_trap(self.last_trap)
                                except Exception:
                                    pass
                        if size < 0:                      # Dalvik: negative Groesse wirft (kein Clamp -> kein Hang)
                            raise DalvikException("java.lang.NegativeArraySizeException: %d" % size)
                        if size > _ARRAY_OOM_LIMIT:       # Dalvik: zu grosse Allokation -> fangbarer OOM
                            raise DalvikException("java.lang.OutOfMemoryError: array of %d elements" % size)
                        et = a[2][1:] if a[2].startswith("[") else a[2]
                        v[a[0]] = JArray(a[2], [default_for(et)] * size)
                    elif op == "array-length":
                        arr = v[a[1]]
                        v[a[0]] = len(arr) if isinstance(arr, JArray) else 0
                    elif op.startswith("aget"):
                        arr, idx = v[a[1]], _i32(v[a[2]])
                        if isinstance(arr, JArray):
                            if not (0 <= idx < len(arr.data)):
                                raise DalvikException(
                                    "java.lang.ArrayIndexOutOfBoundsException: length=%d; index=%d" % (len(arr.data), idx)
                                )
                            raw = arr.data[idx]
                            if op == "aget-byte":            # Dalvik: byte ist signed -> sign-extend
                                x = _i32(raw) & 0xff; v[a[0]] = _u32(x - 0x100 if x & 0x80 else x)
                            elif op == "aget-short":
                                x = _i32(raw) & 0xffff; v[a[0]] = _u32(x - 0x10000 if x & 0x8000 else x)
                            elif op == "aget-char":          # char ist unsigned -> zero-extend
                                v[a[0]] = _u32(_i32(raw) & 0xffff)
                            elif op == "aget-boolean":
                                v[a[0]] = _u32(_i32(raw) & 0x1)
                            else:
                                v[a[0]] = raw
                        else:
                            v[a[0]] = 0
                    elif op.startswith("aput"):
                        arr, idx = v[a[1]], _i32(v[a[2]])
                        if isinstance(arr, JArray):
                            if not (0 <= idx < len(arr.data)):
                                raise DalvikException(
                                    "java.lang.ArrayIndexOutOfBoundsException: length=%d; index=%d" % (len(arr.data), idx)
                                )
                            val = v[a[0]]
                            if op == "aput-byte":            # auf Byte-Breite truncaten (0..255 gespeichert)
                                arr.data[idx] = _u32(_i32(val) & 0xff)
                            elif op in ("aput-short", "aput-char"):
                                arr.data[idx] = _u32(_i32(val) & 0xffff)
                            elif op == "aput-boolean":
                                arr.data[idx] = _u32(_i32(val) & 0x1)
                            else:
                                arr.data[idx] = val
                    elif op == "filled-new-array":
                        regs = a[0]
                        fr.result = JArray(a[1], [v[r] for r in regs])
                    elif op == "instance-of":
                        v[a[0]] = self._instance_of(v[a[1]], a[2])
                    elif op == "check-cast":
                        self._check_cast(v[a[0]], a[1])
                    # ---- Invokes -------------------------------------------------
                    elif op in ("invoke-static", "invoke-direct", "invoke-virtual", "invoke-super",
                                "invoke-interface", "invoke-static/range", "invoke-direct/range",
                                "invoke-virtual/range", "invoke-super/range", "invoke-interface/range"):
                        fr.result = self._do_invoke(op, a, v)
                    elif op == "invoke-native":
                        dst, fnptr, this_cls, *argregs = a
                        if self.bridge is None:
                            raise RuntimeError("invoke-native ohne bridge (JavaToNativeBridge)")
                        nargs = [v[r] for r in argregs]
                        ret = self.bridge.call_native(int(fnptr), this_cls, nargs, ret="int")
                        v[dst] = _u32(int(ret))
                    else:
                        raise DalvikUnknownOpcode(op, fr.pc - 1)
                return None
            except DalvikException as e:
                if isinstance(e, (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)):
                    raise
                msg = str(e)
                _pl = getattr(e, "payload", None)
                exc_obj = _pl if isinstance(_pl, JObject) else self._mk_exc(msg)
                if isinstance(exc_obj, JObject) and not isinstance(_pl, JObject):
                    e.payload = exc_obj
                h = self._find_handler(getattr(fr, "md", None), fr.cur, exc_obj)
                if self._dbg is not None:
                    already_reported = isinstance(exc_obj, JObject) and bool(exc_obj.fields.get("_dbg_reported"))
                    if not already_reported:
                        is_handled = (h is not None) or any(
                            self._find_handler(getattr(_fr, "md", None), getattr(_fr, "cur", 0), exc_obj) is not None
                            for _fr in reversed(self._frames[:-1])
                        )
                        if isinstance(exc_obj, JObject):
                            exc_obj.fields["_dbg_reported"] = True
                        on_exc = getattr(self._dbg, "on_exception", None)
                        if callable(on_exc):
                            try:
                                _md = getattr(fr, "md", None)
                                on_exc(self, fr, getattr(_md, "insns", None), int(getattr(fr, "cur", 0)), exc_obj, handled=is_handled)
                            except Exception as dbg_e:
                                if isinstance(dbg_e, DebugStop) or dbg_e.__class__.__name__ == "DebugStop":
                                    raise
                if h is None:
                    raise
                if isinstance(exc_obj, JObject):
                    exc_obj.fields.pop("_dbg_reported", None)
                fr.pc = h
                fr.exc = exc_obj

    def _parse_inject_fields(self, cls_desc):
        """Extrahiert mit @Inject annotierte Instanzfelder einer Smali-Klasse (gecacht)."""
        cache = self._inject_fields_cache
        if cls_desc in cache:
            return cache[cls_desc]
        out = {}
        prov = self.class_provider
        src = ""
        if prov is not None and hasattr(prov, "get_source"):
            try:
                src = prov.get_source(cls_desc) or ""
            except Exception:
                src = ""
        if src:
            lines = src.splitlines()
            i = 0
            while i < len(lines):
                st = lines[i].strip()
                if st.startswith(".field "):
                    head = st.split("=", 1)[0].strip()
                    parts = head.split()
                    if len(parts) >= 2 and "static" not in parts[1:-1] and ":" in parts[-1]:
                        fn, ft = parts[-1].split(":", 1)
                        has_inject = False
                        j = i + 1
                        while j < len(lines) and not lines[j].strip().startswith((".end field", ".field", ".method")):
                            if "Ljavax/inject/Inject;" in lines[j] or "Ljakarta/inject/Inject;" in lines[j]:
                                has_inject = True
                                break
                            j += 1
                        if has_inject:
                            out[fn] = ft
                i += 1
        cache[cls_desc] = out
        return out

    def _maybe_lazy_inject(self, obj, owner_cls, field_name, fallback):
        """Erzeugt fehlende @Inject-Collaborators bei Erstzugriff (DI-Emulation fuer Presenters/Repos)."""
        if self._di_depth >= 3:
            return fallback
        if not obj.fields.get("$frag_created") and (
            obj.fields.get("$is_fragment")
            or self._is_subclass(getattr(obj, "cls", None), "Landroidx/fragment/app/Fragment;")
        ):
            return fallback
        ft = None
        for cand_cls in (owner_cls, getattr(obj, "cls", None)):
            if not cand_cls:
                continue
            imap = self._parse_inject_fields(cand_cls)
            if field_name in imap:
                ft = imap[field_name]
                break
        if not ft or not ft.startswith("L"):
            return fallback
        if ft in ("Landroid/content/Context;", "Landroid/app/Activity;", "Landroid/app/Application;"):
            st = getattr(self, "_stubs_ref", None)
            ctx = getattr(st, "_current_activity", None) if st is not None else None
            if ctx is None and st is not None:
                ctx = getattr(st, "application", None)
            if isinstance(ctx, JObject):
                obj.fields[field_name] = ctx
                return ctx
            return fallback
        inst = self._di_singletons.get(ft)
        if inst is None:
            inst = JObject(ft)
            self._di_singletons[ft] = inst
            # Wenn Collaborator (z.B. Navigator) einen Context/Activity-Feld braucht, direkt vorbelegen
            ctx_obj = getattr(getattr(self, "_stubs_ref", None), "_current_activity", None) or getattr(getattr(self, "_stubs_ref", None), "application", None)
            self._di_depth += 1
            old_b = self.step_budget
            old_lenient = self.lenient
            try:
                self.lenient = True
                if self.step_budget is not None:
                    self.step_budget = max(self.step_budget, self.steps + 4000)
                else:
                    self.step_budget = self.steps + 4000
                self._lazy_class(ft)
                md = self.registry.get((ft, "<init>", "()V"))
                if md is not None:
                    self._run_mdef(md, [inst])
                if isinstance(ctx_obj, JObject):
                    prov = self.class_provider
                    if prov is not None and hasattr(prov, "get_source"):
                        src_c = prov.get_source(ft) or ""
                        for ln in src_c.splitlines():
                            s = ln.strip()
                            if s.startswith(".field ") and "static" not in s:
                                nt = s.split("=", 1)[0].strip().split()[-1]
                                if ":" in nt:
                                    cfn, cft = nt.split(":", 1)
                                    if cft in ("Landroid/content/Context;", "Landroid/app/Activity;", "Landroid/app/Application;") and cfn not in inst.fields:
                                        inst.fields[cfn] = ctx_obj
            except Exception:
                pass
            finally:
                self.step_budget = old_b
                self.lenient = old_lenient
                self._di_depth -= 1
        obj.fields[field_name] = inst
        return inst

    def _iter_dynamic_dcls(self):
        """Liefert dynamisch nachgeladene DexClassLoader unabhaengig von Runtime-Wrappern."""
        dyn = getattr(self.runtime, "_dynamic_dcls", None)
        if not dyn:
            dyn = getattr(getattr(self, "_stubs_ref", None), "_dynamic_dcls", ())
        return list(dyn or ())

    # ---- Exception-Handler (Intra-Method try/catch) -----------------------
    _AOSP_VIEW_SUPER = {
        "Landroid/widget/LinearLayout;": "Landroid/view/ViewGroup;",
        "Landroid/widget/FrameLayout;": "Landroid/view/ViewGroup;",
        "Landroid/widget/RelativeLayout;": "Landroid/view/ViewGroup;",
        "Landroidx/constraintlayout/widget/ConstraintLayout;": "Landroid/view/ViewGroup;",
        "Landroid/widget/ScrollView;": "Landroid/widget/FrameLayout;",
        "Landroid/view/ViewGroup;": "Landroid/view/View;",
        "Landroid/view/View;": "Ljava/lang/Object;",
        "Landroid/widget/TextView;": "Landroid/view/View;",
        "Landroid/widget/ImageView;": "Landroid/view/View;",
        "Landroid/widget/Button;": "Landroid/widget/TextView;",
        "Landroid/widget/EditText;": "Landroid/widget/TextView;",
        "Landroid/widget/CompoundButton;": "Landroid/widget/Button;",
        "Landroid/widget/CheckBox;": "Landroid/widget/CompoundButton;",
        "Landroid/widget/Switch;": "Landroid/widget/CompoundButton;",
        "Landroid/widget/RadioButton;": "Landroid/widget/CompoundButton;",
        "Landroid/widget/ToggleButton;": "Landroid/widget/CompoundButton;",
        "Landroid/widget/ImageButton;": "Landroid/widget/ImageView;",
        "Landroidx/appcompat/app/AppCompatActivity;": "Landroidx/fragment/app/FragmentActivity;",
        "Landroidx/fragment/app/FragmentActivity;": "Landroidx/activity/ComponentActivity;",
        "Landroidx/activity/ComponentActivity;": "Landroid/app/Activity;",
        "Landroid/app/Activity;": "Landroid/view/ContextThemeWrapper;",
        "Landroid/view/ContextThemeWrapper;": "Landroid/content/ContextWrapper;",
        "Landroid/app/Service;": "Landroid/content/ContextWrapper;",
        "Landroid/app/IntentService;": "Landroid/app/Service;",
        "Landroid/app/Application;": "Landroid/content/ContextWrapper;",
        "Landroid/content/ContextWrapper;": "Landroid/content/Context;",
        "Landroid/content/Context;": "Ljava/lang/Object;",
        "Ljava/lang/Integer;": "Ljava/lang/Number;",
        "Ljava/lang/Long;": "Ljava/lang/Number;",
        "Ljava/lang/Double;": "Ljava/lang/Number;",
        "Ljava/lang/Float;": "Ljava/lang/Number;",
        "Ljava/lang/Short;": "Ljava/lang/Number;",
        "Ljava/lang/Byte;": "Ljava/lang/Number;",
        "Ljava/lang/Number;": "Ljava/lang/Object;",
        "Ljava/lang/Boolean;": "Ljava/lang/Object;",
        "Ljava/lang/Character;": "Ljava/lang/Object;",
        "Ljava/lang/String;": "Ljava/lang/Object;",
        "Ljava/lang/StringBuilder;": "Ljava/lang/Object;",
        "Ljava/lang/StringBuffer;": "Ljava/lang/Object;",
        "Ljava/util/ArrayList;": "Ljava/lang/Object;",
        "Ljava/util/HashMap;": "Ljava/lang/Object;",
        "Landroidx/constraintlayout/widget/ConstraintLayout$LayoutParams;": "Landroid/view/ViewGroup$MarginLayoutParams;",
        "Landroid/widget/LinearLayout$LayoutParams;": "Landroid/view/ViewGroup$MarginLayoutParams;",
        "Landroid/widget/FrameLayout$LayoutParams;": "Landroid/view/ViewGroup$MarginLayoutParams;",
        "Landroid/widget/RelativeLayout$LayoutParams;": "Landroid/view/ViewGroup$MarginLayoutParams;",
        "Landroid/view/ViewGroup$MarginLayoutParams;": "Landroid/view/ViewGroup$LayoutParams;",
        "Landroid/view/ViewGroup$LayoutParams;": "Ljava/lang/Object;",
        "Landroid/graphics/drawable/VectorDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/BitmapDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/ColorDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/Drawable;": "Ljava/lang/Object;",
        "Landroid/text/SpannableString;": "Landroid/text/Spanned;",
        "Landroid/text/Spanned;": "Ljava/lang/CharSequence;",
        "Landroid/text/SpannableStringBuilder;": "Landroid/text/Editable;",
        "Landroid/text/Editable;": "Ljava/lang/CharSequence;",
        "Landroid/text/TextPaint;": "Landroid/graphics/Paint;",
        "Landroid/text/StaticLayout;": "Landroid/text/Layout;",
        "Landroid/text/BoringLayout;": "Landroid/text/Layout;",
        "Landroid/text/DynamicLayout;": "Landroid/text/Layout;",
        "Landroid/text/Layout;": "Ljava/lang/Object;",
        "Landroid/text/style/ClickableSpan;": "Landroid/text/style/CharacterStyle;",
        "Landroid/graphics/drawable/LayerDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/StateListDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/AnimationDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/InsetDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/RippleDrawable;": "Landroid/graphics/drawable/LayerDrawable;",
        "Landroid/graphics/drawable/GradientDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/ShapeDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/ClipDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/drawable/AnimatedVectorDrawable;": "Landroid/graphics/drawable/Drawable;",
        "Landroid/graphics/BitmapShader;": "Landroid/graphics/Shader;",
        "Landroid/graphics/LinearGradient;": "Landroid/graphics/Shader;",
        "Landroid/graphics/RadialGradient;": "Landroid/graphics/Shader;",
        "Landroid/graphics/SweepGradient;": "Landroid/graphics/Shader;",
        "Landroid/graphics/ColorMatrixColorFilter;": "Landroid/graphics/ColorFilter;",
        "Landroid/graphics/PorterDuffXfermode;": "Landroid/graphics/Xfermode;",
        "Landroid/graphics/BlurMaskFilter;": "Landroid/graphics/MaskFilter;",
        "Landroid/widget/AbsListView;": "Landroid/widget/AdapterView;",
        "Landroid/widget/ListView;": "Landroid/widget/AbsListView;",
        "Landroid/widget/Spinner;": "Landroid/widget/AbsSpinner;",
        "Landroid/widget/AbsSpinner;": "Landroid/widget/AdapterView;",
        "Landroid/widget/AdapterView;": "Landroid/view/ViewGroup;",
        "Landroid/widget/ProgressBar;": "Landroid/view/View;",
        "Landroid/widget/SeekBar;": "Landroid/widget/AbsSeekBar;",
        "Landroid/widget/AbsSeekBar;": "Landroid/widget/ProgressBar;",
        "Landroid/widget/RatingBar;": "Landroid/widget/AbsSeekBar;",
        "Landroid/widget/HorizontalScrollView;": "Landroid/widget/FrameLayout;",
        "Landroid/widget/CheckedTextView;": "Landroid/widget/TextView;",
        "Landroid/widget/AutoCompleteTextView;": "Landroid/widget/EditText;",
        "Landroid/widget/MultiAutoCompleteTextView;": "Landroid/widget/AutoCompleteTextView;",
        "Landroid/widget/RadioGroup;": "Landroid/widget/LinearLayout;",
        "Landroid/widget/ArrayAdapter;": "Landroid/widget/BaseAdapter;",
        "Landroid/view/TextureView;": "Landroid/view/View;",
        "Landroid/view/ViewStub;": "Landroid/view/View;",
        "Landroid/animation/ValueAnimator;": "Landroid/animation/Animator;",
        "Landroid/animation/ObjectAnimator;": "Landroid/animation/ValueAnimator;",
        "Landroid/animation/AnimatorSet;": "Landroid/animation/Animator;",
        "Landroid/view/animation/AnimationSet;": "Landroid/view/animation/Animation;",
        "Landroid/view/accessibility/AccessibilityEvent;": "Landroid/view/accessibility/AccessibilityRecord;",
        "Landroid/app/AlertDialog;": "Landroid/app/Dialog;",
        "Landroidx/appcompat/app/AlertDialog;": "Landroidx/appcompat/app/AppCompatDialog;",
        "Landroidx/appcompat/app/AppCompatDialog;": "Landroid/app/Dialog;",
        "Ljava/util/GregorianCalendar;": "Ljava/util/Calendar;",
        "Ljava/text/SimpleDateFormat;": "Ljava/text/DateFormat;",
        "Ljava/lang/Enum;": "Ljava/lang/Object;",
        "Ljava/util/ArrayList;": "Ljava/util/AbstractList;",
        "Ljava/util/LinkedList;": "Ljava/util/AbstractSequentialList;",
        "Ljava/util/AbstractSequentialList;": "Ljava/util/AbstractList;",
        "Ljava/util/Vector;": "Ljava/util/AbstractList;",
        "Ljava/util/Stack;": "Ljava/util/Vector;",
        "Ljava/util/AbstractList;": "Ljava/util/AbstractCollection;",
        "Ljava/util/HashSet;": "Ljava/util/AbstractSet;",
        "Ljava/util/LinkedHashSet;": "Ljava/util/HashSet;",
        "Ljava/util/TreeSet;": "Ljava/util/AbstractSet;",
        "Ljava/util/AbstractSet;": "Ljava/util/AbstractCollection;",
        "Ljava/util/AbstractCollection;": "Ljava/lang/Object;",
        "Ljava/util/HashMap;": "Ljava/util/AbstractMap;",
        "Ljava/util/LinkedHashMap;": "Ljava/util/HashMap;",
        "Ljava/util/TreeMap;": "Ljava/util/AbstractMap;",
        "Ljava/util/AbstractMap;": "Ljava/lang/Object;",
    }

    _CLOSED_JDK_CLASSES = frozenset({
        "Ljava/lang/String;",
        "Ljava/lang/Integer;",
        "Ljava/lang/Long;",
        "Ljava/lang/Double;",
        "Ljava/lang/Float;",
        "Ljava/lang/Short;",
        "Ljava/lang/Byte;",
        "Ljava/lang/Character;",
        "Ljava/lang/Boolean;",
        "Ljava/lang/Number;",
        "Ljava/lang/StringBuilder;",
        "Ljava/lang/StringBuffer;",
        "Ljava/util/ArrayList;",
        "Ljava/util/HashMap;",
    })

    def _superclass(self, desc):
        """Direkte Superklasse: JDK-Throwable-Tabelle, sonst class_provider (.super), dyn. DEX oder AOSP-View."""
        self._sync_type_caches()
        if desc in self._super_cache:
            return self._super_cache[desc]
        sup = _JDK_THROWABLE_SUPER.get(desc)
        if sup is not None:
            self._super_cache[desc] = sup
            return sup
        self._lazy_class(desc)
        prov = self.class_provider
        if prov is not None and hasattr(prov, "get_super"):
            try:
                res = prov.get_super(desc)
                if res is not None:
                    self._super_cache[desc] = res
                    return res
            except Exception:
                pass
        for dyn_dcl in self._iter_dynamic_dcls():
            smap = getattr(dyn_dcl, "_supers", None)
            if smap and desc in smap:
                res = smap[desc]
                self._super_cache[desc] = res
                return res
        res = self._AOSP_VIEW_SUPER.get(desc)
        self._super_cache[desc] = res
        return res

    def _compute_class_kind_info(self, desc):
        dcls = self._iter_dynamic_dcls()
        ldr = getattr(self.runtime, "loader", None)
        if ldr is not None and ldr not in dcls:
            dcls.append(ldr)
        for dcl in dcls:
            try:
                idx = dcl._ensure_index() if hasattr(dcl, "_ensure_index") else None
                if idx and desc in idx:
                    acc = int(idx[desc].get("access", 0) or 0)
                    return True, bool(acc & 0x0200)
            except Exception:
                pass
        prov = self.class_provider
        if prov is not None:
            if hasattr(prov, "get_source"):
                try:
                    src = prov.get_source(desc)
                except Exception:
                    src = None
                if src:
                    for ln in src.splitlines():
                        st = ln.strip()
                        if st.startswith(".class"):
                            toks = st.split()
                            return True, ("interface" in toks[:-1])
            try:
                mds = prov.get_class(desc)
            except Exception:
                mds = None
            if mds:
                has_concrete = any(getattr(m, "insns", None) or m.name == "<init>" for m in mds)
                return True, (not has_concrete)
            if hasattr(prov, "get_super"):
                try:
                    if prov.get_super(desc) is not None:
                        return True, True
                except Exception:
                    pass
        for k, md in self.registry.items():
            if k[0] == desc and (getattr(md, "insns", None) or k[1] == "<init>"):
                return True, False
        return False, False

    def _class_kind_info(self, desc):
        """Liefert (is_loaded_dex_or_provider, is_interface) fuer einen Klassendeskriptor."""
        if not desc:
            return False, False
        self._sync_type_caches()
        cached = self._class_kind_cache.get(desc)
        if cached is not None:
            return cached
        res = self._compute_class_kind_info(desc)
        self._class_kind_cache[desc] = res
        return res

    def _has_closed_object_chain(self, desc):
        """True wenn desc eine vollstaendig bekannte Superklassen-Kette bis Ljava/lang/Object; besitzt."""
        cur = desc
        for _ in range(64):
            if cur == "Ljava/lang/Object;":
                return True
            nxt = self._superclass(cur)
            if nxt is None or nxt == cur:
                return False
            cur = nxt
        return False

    def _check_cast(self, obj, typ):
        """Konservativer Closed-World check-cast: prueft Strings, Arrays und geschlossene konkrete Klassen."""
        if obj is None or obj == 0 or obj is False or not typ:
            return
        if typ == "Ljava/lang/Object;":
            return
        if isinstance(obj, JString):
            if typ not in self._STR_IFACES:
                raise DalvikException(f"java.lang.ClassCastException: Ljava/lang/String; cannot be cast to {typ}")
            return
        if isinstance(obj, JObject) and obj.cls == "Ljava/lang/String;":
            if typ not in self._STR_IFACES:
                raise DalvikException(f"java.lang.ClassCastException: {obj.cls} cannot be cast to {typ}")
            return
        if typ in ("Ljava/io/Serializable;", "Ljava/lang/Cloneable;"):
            return
        if isinstance(obj, JArray):
            if self._instance_of(obj, typ) == 0:
                raise DalvikException(f"java.lang.ClassCastException: {obj.eltype} cannot be cast to {typ}")
            return
        if not isinstance(obj, JObject):
            return
        if obj.cls in ("Landroid/os/SystemService;", "Landroid/view/View;") or obj.fields.get("$synth_fm"):
            return
        self._lazy_class(obj.cls)
        self._lazy_class(typ)
        if self._instance_of(obj, typ) != 0:
            return
        src_loaded, _ = self._class_kind_info(obj.cls)
        src_closed = (obj.cls in self._CLOSED_JDK_CLASSES) or (src_loaded and self._has_closed_object_chain(obj.cls))
        if not src_closed:
            return
        tgt_loaded, tgt_iface = self._class_kind_info(typ)
        tgt_closed = (typ in self._CLOSED_JDK_CLASSES) or (tgt_loaded and not tgt_iface)
        if tgt_closed:
            raise DalvikException(f"java.lang.ClassCastException: {obj.cls} cannot be cast to {typ}")

    def _throwable_isa(self, sub, sup):
        """Ist sub (Klassen-Deskriptor der geworfenen Exception) ein sup? Superklassen-Walk.
        Unbekannte Superklasse -> konservativ nur fuer Throwable matchen (kein Blind-Fang)."""
        if sub is None or sup is None:
            return False
        cur = sub
        for _ in range(64):
            if cur == sup:
                return True
            if cur == "Ljava/lang/Object;":
                return False
            nxt = self._superclass(cur)
            if nxt is None:
                # Unbekannte Framework-Klasse: Suffix-Heuristik (Checked->Exception, *Error->Error)
                if (cur.startswith("Ljava/") or cur.startswith("Landroid/")
                        or cur.startswith("Ljavax/") or cur.startswith("Lkotlin/")):
                    if cur.endswith("Exception;") and cur != "Ljava/lang/Exception;":
                        nxt = "Ljava/lang/Exception;"
                    elif cur.endswith("Error;") and cur != "Ljava/lang/Error;":
                        nxt = "Ljava/lang/Error;"
                if nxt is None or nxt == cur:
                    return sup == "Ljava/lang/Throwable;"
            cur = nxt
        return False

    def _find_handler(self, md, pc, exc_obj):
        """Erster passender Catch-Handler fuer pc (Dalvik-Reihenfolge wie im Smali notiert).
        .catchall faengt alles; typisierte Catches ueber echte Klassenhierarchie des geworfenen
        Objekts (exc_obj.cls), inkl. Superklassen/JDK-Throwable-Kette."""
        handlers = getattr(md, "handlers", None) if md is not None else None
        if not handlers:
            return None
        exc_cls = exc_obj.cls if isinstance(exc_obj, JObject) else "Ljava/lang/Throwable;"
        for (start, end, exc_type, hpc) in handlers:
            if not (start <= pc < end):
                continue
            if exc_type is None:
                return hpc
            if self._throwable_isa(exc_cls, exc_type):
                return hpc
        return None

    def _mk_exc(self, msg):
        """Synthetisches Exception-JObject fuer VM-interne Fehler (typisiert nach Meldung)."""
        return JObject(_exc_type_from_msg(msg), {"msg": msg})

    # ---- Invoke-Aufloesung -------------------------------------------------
    # ---- Argument-Breite (J/D = 2 Register-Slots) --------------------------
    @staticmethod
    def _sig_param_kinds(sig):
        """Liste der Param-Breiten: 2 fuer J/D, sonst 1 (Objekt/Array/primitive)."""
        inner = sig[sig.index("(") + 1:sig.index(")")]
        kinds = []
        i = 0
        while i < len(inner):
            c = inner[i]
            if c == "[":
                i += 1
                while i < len(inner) and inner[i] == "[":
                    i += 1
                if i < len(inner) and inner[i] == "L":
                    i = inner.index(";", i) + 1
                else:
                    i += 1
                kinds.append(1)
            elif c == "L":
                i = inner.index(";", i) + 1
                kinds.append(1)
            elif c in ("J", "D"):
                i += 1
                kinds.append(2)
            else:
                i += 1
                kinds.append(1)
        return kinds

    def _logical_args(self, sig, static, regs, v):
        """Slot-ausgerichtete Registerliste -> logische Args (ein Wert je Java-Param, wide=low reg).
        Fuer Stubs/resolve_invoke, die logische Argumente erwarten."""
        out = []
        i = 0
        if not static and i < len(regs):
            out.append(v[regs[i]]); i += 1
        for k in self._sig_param_kinds(sig):
            if i >= len(regs):
                break
            out.append(v[regs[i]])
            i += k
        return out

    def _slots_from_logical(self, sig, static, logical):
        """Logische Args -> slot-ausgerichtete Liste (Platzhalter nach jedem J/D) fuer Frame."""
        out = []
        k = 0
        if not static and k < len(logical):
            out.append(logical[k]); k += 1
        for kind in self._sig_param_kinds(sig):
            if k >= len(logical):
                break
            out.append(logical[k]); k += 1
            if kind == 2:
                out.append(0)          # Zweit-Slot des wide-Werts (unser Wert liegt im Low-Reg)
        return out

    def _try_fast_byte_hash_loop(self, fr: Frame, insns, target_pc: int) -> bool:
        """Beschleunigt enge Byte-Array-SDBM-Hash-Schleifen (h = b + (h<<6) + (h<<16) - h) in Python."""
        loop_end = int(fr.cur)
        if not (0 <= target_pc < loop_end and 8 <= (loop_end - target_pc) <= 24):
            return False
        head = insns[target_pc]
        nxt = insns[target_pc + 1]
        if head[0] != "if-ge" or nxt[0] != "aget-byte":
            return False
        _, idx_reg, len_reg, exit_pc = head
        _, _elem_reg, arr_reg, aidx_reg = nxt
        if aidx_reg != idx_reg:
            return False
        v = fr.v
        arr = v[arr_reg]
        if not isinstance(arr, JArray) or arr.eltype not in ("B", "[B"):
            return False
        # Pruefen: reiner SDBM-Rumpf ohne Aufrufe/Side-Effects/Spruenge
        consts: dict[int, int] = {}
        shifts: list[tuple[int, int]] = []
        sub_acc = None
        has_inc = False
        for k in range(target_pc + 2, loop_end):
            ins = insns[k]
            iop = ins[0]
            if (iop.startswith("invoke") or iop.startswith("aput") or iop.startswith("iput")
                    or iop.startswith("sput") or iop.startswith("if-") or iop.startswith("goto")
                    or iop == "throw"):
                return False
            if iop in ("const", "const/4", "const/16", "const/high16"):
                consts[int(ins[1])] = int(ins[2])
            elif iop == "shl-long":
                sr = int(ins[3])
                if sr in consts:
                    shifts.append((int(ins[2]), consts[sr]))
            elif iop == "sub-long":
                if int(ins[1]) == int(ins[3]):
                    sub_acc = int(ins[1])
            elif iop == "add-int/lit8" and int(ins[1]) == idx_reg and int(ins[2]) == idx_reg and int(ins[3]) == 1:
                has_inc = True
            elif iop == "xor-int/lit8" and int(ins[1]) == idx_reg and int(ins[2]) == idx_reg and int(ins[3]) == 1:
                has_inc = True
        if sub_acc is None or not has_inc:
            return False
        if (sub_acc, 6) not in shifts or (sub_acc, 16) not in shifts:
            return False
        idx = _i32(v[idx_reg]) if isinstance(v[idx_reg], int) else -1
        limit = _i32(v[len_reg]) if isinstance(v[len_reg], int) else -1
        data = arr.data
        if not (0 <= idx < limit <= len(data)):
            return False
        h = int(v[sub_acc]) & _L
        for pos in range(idx, limit):
            raw = data[pos]
            b = (int(raw) & 0xFF) if isinstance(raw, int) else 0
            if b & 0x80:
                b -= 0x100
            h = (b + ((h << 6) & _L) + ((h << 16) & _L) - h) & _L
        v[sub_acc] = h
        v[idx_reg] = _u32(limit)
        fr.pc = int(exit_pc)
        return True

    _AOSP_VIEW_IFACES = {
        "Landroid/widget/CompoundButton;": ("Landroid/widget/Checkable;",),
        "Landroid/widget/CheckBox;": ("Landroid/widget/Checkable;",),
        "Landroid/widget/Switch;": ("Landroid/widget/Checkable;",),
        "Landroid/widget/RadioButton;": ("Landroid/widget/Checkable;",),
        "Landroid/widget/ToggleButton;": ("Landroid/widget/Checkable;",),
        "Landroid/widget/CheckedTextView;": ("Landroid/widget/Checkable;",),
        "Landroidx/appcompat/widget/SwitchCompat;": ("Landroid/widget/Checkable;",),
        "Lcom/google/android/material/materialswitch/MaterialSwitch;": ("Landroid/widget/Checkable;",),
        "Landroid/view/ViewGroup;": ("Landroid/view/ViewParent;", "Landroid/view/ViewManager;"),
        "Landroid/text/SpannableStringBuilder;": (
            "Landroid/text/Editable;", "Landroid/text/Spannable;",
            "Landroid/text/Spanned;", "Ljava/lang/CharSequence;", "Ljava/lang/Appendable;",
        ),
        "Landroid/view/SubMenu;": ("Landroid/view/Menu;",),
        "Landroid/widget/BaseAdapter;": ("Landroid/widget/ListAdapter;", "Landroid/widget/SpinnerAdapter;"),
        "Landroid/widget/ArrayAdapter;": ("Landroid/widget/ListAdapter;", "Landroid/widget/SpinnerAdapter;"),
    }

    def _interfaces(self, desc):
        """Direkt implementierte Interfaces ueber class_provider (.implements), dyn. DEX oder AOSP-View."""
        if not desc:
            return []
        self._sync_type_caches()
        if desc in self._iface_cache:
            return list(self._iface_cache[desc])
        self._lazy_class(desc)
        out: list[str] = []
        prov = self.class_provider
        if prov is not None and hasattr(prov, "get_interfaces"):
            try:
                out.extend(prov.get_interfaces(desc) or ())
            except Exception:
                pass
        for dyn_dcl in self._iter_dynamic_dcls():
            imap = getattr(dyn_dcl, "_ifaces", None)
            if imap and desc in imap:
                for it in imap[desc]:
                    if it not in out:
                        out.append(it)
        for aif in self._AOSP_VIEW_IFACES.get(desc, ()):
            if aif not in out:
                out.append(aif)
        self._iface_cache[desc] = tuple(out)
        return out

    def _lazy_class(self, desc):
        """App-Klasse ueber den Provider (oder dynamische DexClassLoader) in die Registry laden."""
        if desc is None:
            return
        self._sync_type_caches()
        if desc in self._lazy_done:
            return
        self._lazy_done.add(desc)
        if self.class_provider is not None:
            try:
                prov = self.class_provider.get_class(desc)
            except Exception:
                prov = None
            if prov:
                for pm in prov:
                    self.registry.setdefault((pm.cls, pm.name, pm.sig), pm)
                return
        for dyn_dcl in self._iter_dynamic_dcls():
            try:
                if hasattr(dyn_dcl, "ensure_class") and dyn_dcl.ensure_class(desc):
                    return
            except Exception:
                pass

    def _resolve_method(self, rcls, cls, name, sig):
        """Methode aufloesen: konkrete Implementierung ab rcls durch Superklassen, dann cls, dann Interfaces."""
        chain: list[str] = []
        seen: set[str] = set()
        for start in (rcls, cls):
            cur = start
            for _ in range(64):
                if not cur or cur in seen:
                    break
                seen.add(cur)
                chain.append(cur)
                if cur == "Ljava/lang/Object;":
                    break
                cur = self._superclass(cur)
        # Default-Methoden in Interfaces nach Superklassen pruefen
        ifaces_queue = list(chain)
        for cur in ifaces_queue:
            for iface in self._interfaces(cur):
                if iface and iface not in seen:
                    seen.add(iface)
                    chain.append(iface)
                    ifaces_queue.append(iface)
        for want in chain:
            md = self.registry.get((want, name, sig))
            if md is not None and md.insns:
                return md
            self._lazy_class(want)
            md = self.registry.get((want, name, sig))
            if md is not None and md.insns:
                return md
        return None

    def _is_subclass(self, sub, sup):
        """Subklassen- und Interface-Test ueber Superklassen- und .implements-Graph."""
        if sub is None or sup is None:
            return False
        if sub == sup:
            return True
        self._sync_type_caches()
        key = (sub, sup)
        cached = self._subclass_cache.get(key)
        if cached is not None:
            return cached
        res = False
        queue = [sub]
        seen: set[str] = set()
        while queue and len(seen) < 96:
            cur = queue.pop(0)
            if not cur or cur in seen:
                continue
            if cur == sup:
                res = True
                break
            seen.add(cur)
            if cur != "Ljava/lang/Object;":
                nxt = self._superclass(cur)
                if nxt and nxt not in seen:
                    queue.append(nxt)
            for iface in self._interfaces(cur):
                if iface == sup:
                    res = True
                    break
                if iface and iface not in seen:
                    queue.append(iface)
            if res:
                break
        self._subclass_cache[key] = res
        return res

    _STR_IFACES = ("Ljava/lang/String;", "Ljava/lang/CharSequence;",
                   "Ljava/lang/Comparable;", "Ljava/io/Serializable;",
                   "Ljava/lang/Object;")

    def _instance_of(self, obj, typ):
        """instance-of: String (JString ODER konstruiertes String-JObject), Array (exakter
        Deskriptor + Referenz-Kovarianz), JObject ueber exakte Klasse + Superklassen-Walk. null -> 0."""
        if obj is None or obj == 0 or obj is False:
            return 0
        if isinstance(obj, JString):
            return 1 if typ in self._STR_IFACES else 0
        if isinstance(obj, JArray):
            ad = obj.eltype                       # voller Array-Deskriptor, z.B. "[I" / "[Lcom/..;"
            if typ in ("Ljava/lang/Object;", "Ljava/io/Serializable;", "Ljava/lang/Cloneable;"):
                return 1
            if ad == typ:                         # exakt: int[] matcht nur int[], nicht byte[]
                return 1
            if typ == "[Ljava/lang/Object;" and len(ad) >= 2 and ad[1] in ("L", "["):
                return 1
            if ad.startswith("[L") and ad.endswith(";") and typ.startswith("[L") and typ.endswith(";"):
                return 1 if self._is_subclass(ad[1:], typ[1:]) else 0   # Kovarianz nur Referenz-Arrays
            return 0
        if isinstance(obj, JObject):
            if obj.cls == "Ljava/lang/String;":   # konstruierter String wie JString behandeln
                return 1 if typ in self._STR_IFACES else 0
            if typ == "Ljava/lang/Object;" or obj.cls == typ:
                return 1
            return 1 if self._is_subclass(obj.cls, typ) else 0
        return 0

    def _do_invoke(self, op, a, v):
        regs, cls, name, sig = a[0], a[1], a[2], a[3]
        args = [v[r] for r in regs]            # slot-ausgerichtet (fuer _run_mdef)
        static = op.startswith("invoke-static")
        if self.method_overrides:              # Glass-Box-Override: feste Rueckgabe, Smali-Koerper ueberspringen
            ov = self.method_overrides.get((cls, name, sig))
            if ov is not None:
                return ov(args) if callable(ov) else ov
        # Virtueller Dispatch: Laufzeitklasse des Empfaengers (JObject/JString/JArray).
        rcls = cls
        if op in ("invoke-virtual", "invoke-interface",
                  "invoke-virtual/range", "invoke-interface/range") and args:
            recv = args[0]
            if isinstance(recv, JObject):
                rcls = recv.cls
            elif isinstance(recv, JString):
                rcls = "Ljava/lang/String;"
            elif isinstance(recv, JArray):
                rcls = "Ljava/lang/Object;"
        if self._method_hooks:
            logical_h = self._logical_args(sig, static, regs, v)
            for _hk in list(self._method_hooks):
                handled_h, ret_h = _hk(rcls, cls, name, sig, logical_h, static, self)
                if handled_h:
                    return ret_h
        md = self._resolve_method(rcls, cls, name, sig)
        if md is not None:
            if self.lenient:
                try:
                    if not static and (not args or not isinstance(args[0], (JObject, JString, JArray))):
                        return 0
                    return self._run_mdef(md, args)
                except Exception as e:
                    if isinstance(e, (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop)):
                        raise
                    emsg = str(e)
                    # Aktive Java-.catch-Bloecke auf dem Call-Stack haben Vorrang vor dem lenient-Fallback:
                    # wuerde irgendein Frame die Exception fangen, normal propagieren (-> _exec/_find_handler),
                    # statt sie hier auf 0 zu absorbieren. Nur Emulations-Luecken (kein aktiver Handler) schlucken.
                    if isinstance(e, DalvikException):
                        _pl = getattr(e, "payload", None)
                        _exc = _pl if isinstance(_pl, JObject) else self._mk_exc(emsg)
                        for _fr in reversed(self._frames):
                            if self._find_handler(getattr(_fr, "md", None), getattr(_fr, "cur", 0), _exc) is not None:
                                raise
                    if self._dbg is not None and isinstance(getattr(self._dbg, "last_exception", None), dict):
                        self._dbg.last_exception["lenient"] = True
                    self.lenient_errors.append({"cls": cls, "name": name, "sig": sig, "error": repr(e)})
                    self.deviations.record("lenient_vm", f"{cls}->{name}{sig}", repr(e), step=self.steps)
                    return 0
            return self._run_mdef(md, args)
        # Runtime (Stubs/native): logische Args (J/D-Paare kollabiert), rcls dann cls.
        if self.runtime is not None:
            logical = self._logical_args(sig, static, regs, v)
            wants = self._build_wants(rcls, cls)
            handled, ret = self._resolve_runtime_invoke(wants, rcls, cls, name, sig, logical, static)
            if handled:
                return ret
        if name == "<init>":
            return None
        self._record_missing(cls, name, sig)
        return 0

    def _build_wants(self, rcls, cls):
        wants = []
        for _w in (rcls, cls):
            if _w and _w not in wants:
                wants.append(_w)
        for start in (rcls, cls):
            if not start:
                continue
            _cur = start
            for _ in range(64):
                _sup = self._superclass(_cur)
                if _sup is None or _sup == "Ljava/lang/Object;":
                    break
                if _sup not in wants:
                    wants.append(_sup)
                _cur = _sup
        if "Ljava/lang/Object;" not in wants:
            wants.append("Ljava/lang/Object;")
        return wants

    def _find_runtime_stubs(self):
        rt = self.runtime
        seen = set()
        while rt is not None and id(rt) not in seen:
            seen.add(id(rt))
            st = getattr(rt, "stubs", None)
            if st is not None:
                return st
            rt = getattr(rt, "inner", None) or getattr(rt, "prev", None)
        return None

    def _resolve_runtime_invoke(self, wants, rcls, cls, name, sig, logical, static):
        rt = self.runtime
        if rt is None:
            return False, 0
        if (cls in ("Ljava/lang/System;", "Ljava/lang/Runtime;")
                and name in ("loadLibrary", "loadLibrary0", "load", "load0", "nativeLoad")
                and (getattr(self, "_fusion_load_library", None) is not None
                     or getattr(self, "_fusion_load_path", None) is not None)
                and hasattr(rt, "resolve_invoke")):
            handled, ret = rt.resolve_invoke(cls, name, sig, logical, static)
            if handled:
                return True, ret
        nt = getattr(self, "native_table", None)
        if nt is None:
            cur_rt = rt
            seen_rt = set()
            while cur_rt is not None and id(cur_rt) not in seen_rt:
                seen_rt.add(id(cur_rt))
                nt = getattr(cur_rt, "table", None)
                if nt is not None:
                    break
                cur_rt = getattr(cur_rt, "inner", None) or getattr(cur_rt, "prev", None)
        if nt is not None and hasattr(rt, "resolve_invoke"):
            for want in wants:
                if nt.lookup(want, name, sig) is not None:
                    handled, ret = rt.resolve_invoke(want, name, sig, logical, static)
                    if handled:
                        return True, ret
        ldr = getattr(rt, "loader", None)
        if ldr is not None and hasattr(rt, "resolve_invoke"):
            for want in wants:
                if ldr.has_class(want) and want not in ldr.loaded:
                    handled, ret = rt.resolve_invoke(want, name, sig, logical, static)
                    if handled:
                        return True, ret
        stubs_obj = self._find_runtime_stubs()
        if stubs_obj is not None and hasattr(stubs_obj, "resolve_phased"):
            return stubs_obj.resolve_phased(wants, rcls, cls, name, sig, logical, static)
        if hasattr(rt, "resolve_invoke"):
            for want in wants:
                handled, ret = rt.resolve_invoke(want, name, sig, logical, static)
                if handled:
                    return True, ret
        elif stubs_obj is not None and hasattr(stubs_obj, "resolve"):
            for want in wants:
                handled, ret = stubs_obj.resolve(want, name, sig, logical, static)
                if handled:
                    return True, ret
        return False, 0

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
        self.coverage["__missing__"] = self.coverage.get("__missing__", 0) + 1
        self.deviations.record("missing_method", f"{cls}->{name}{sig}", step=self.steps)

def _branch1(op, x):
    xi = _as_i(x)
    if op == "if-eqz":
        return _zero(x)
    if op == "if-nez":
        return not _zero(x)
    if op == "if-ltz":
        return xi < 0
    if op == "if-gez":
        return xi >= 0
    if op == "if-gtz":
        return xi > 0
    if op == "if-lez":
        return xi <= 0
    return False


def _branch2(op, x, y):
    if op == "if-eq":
        return _eq(x, y)
    if op == "if-ne":
        return not _eq(x, y)
    xi, yi = _as_i(x), _as_i(y)
    if op == "if-lt":
        return xi < yi
    if op == "if-ge":
        return xi >= yi
    if op == "if-gt":
        return xi > yi
    if op == "if-le":
        return xi <= yi
    return False
