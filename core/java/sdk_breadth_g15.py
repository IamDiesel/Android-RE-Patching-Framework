# -*- coding: utf-8 -*-
"""core.java.sdk_breadth_g15 — G15: Breite JDK- & Android-SDK-Stubs (non-View/Canvas).

Bietet deterministische, spezifikationstreue Emulation fuer:
  - ByteBuffer-Backing-Unifizierung (`bb_*` Helfer fuer `fs_bridge` + `engine_stubs_g14`)
  - `Math` & `StrictMath` (Trigonometrie, Rundung, `*Exact` mit Overflow-Exception)
  - `java.io.*` (VFS-isoliertes `File`, Streams, `RandomAccessFile`, `PrintWriter`, `Object*Stream`)
  - `java.lang.*` (`Character`, `Number`, `Object.wait/notify`, `Throwable`, `Thread`, `Class`, `reflect.*`)
  - `java.util.concurrent.*`, `BitSet`, `Objects`, `ListIterator`, `Arrays`, `Collections`
  - `java.util.zip.*` (`Inflater`, `Deflater`, `InflaterInputStream`, `DeflaterOutputStream`, `CRC32`, `Adler32`)
  - `java.math.*` (`BigInteger`, `BigDecimal`, `RoundingMode`, `MathContext`)
  - `android.os.*` (`Parcel`, `Bundle`, `Intent`, `Handler`/`Looper`/`Message`, `Binder`, `ParcelFileDescriptor`)
  - `Context`, `ContextCompat`, `PackageManager`, `ContentResolver`, `Settings`, `Activity`, `Dialog`, `Fragment`
  - `Resources`, `Resources$Theme`, `TypedArray`, `AttributeSet`, `TypedValue`
  - `Notification*`, `FileChannel`, `SSLContext`/`SSLSocket`/`SSLSession`, `InetAddress`, `Uri`
"""
from __future__ import annotations

from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
)
import math
import posixpath
import re
import struct
import unicodedata
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse, urlunparse
import uuid as _py_uuid
import zlib

from core.java.dalvik import DalvikException, DalvikUnknownOpcode, VmBudgetExceeded
from core.java.deviation_journal import DebugStop
from core.java.objects import JArray, JObject, JString


# ---------------------------------------------------------------------------
# Shared ByteBuffer / Buffer Backing Helpers (fs_bridge <-> g14 <-> g15)
# ---------------------------------------------------------------------------

def bb_order_is_big(order_obj) -> bool:
    if isinstance(order_obj, JObject):
        if "_le" in order_obj.fields:
            return not bool(order_obj.fields["_le"])
        return order_obj.fields.get("order", "big") != "little"
    return True


def bb_buf(obj) -> bytearray:
    if not isinstance(obj, JObject):
        return bytearray()
    f = obj.fields
    ba = f.get("buf")
    if ba is None:
        ba = f.get("_buf")
    if ba is None and isinstance(f.get("array"), JArray):
        ba = bytearray(int(x) & 0xFF for x in f["array"].data)
    if isinstance(ba, bytearray):
        f["buf"] = ba
        f["_buf"] = ba
        return ba
    if isinstance(ba, bytes):
        out = bytearray(ba)
        f["buf"] = out
        f["_buf"] = out
        return out
    if isinstance(ba, JArray):
        out = bytearray(int(x) & 0xFF for x in ba.data)
        f["buf"] = out
        f["_buf"] = out
        return out
    out = bytearray()
    f["buf"] = out
    f["_buf"] = out
    return out


def bb_pos(obj) -> int:
    if not isinstance(obj, JObject):
        return 0
    f = obj.fields
    if "pos" in f and "_pos" not in f:
        p = max(0, int(f["pos"] or 0))
        f["_pos"] = p
        return p
    if "_pos" in f and "pos" not in f:
        p = max(0, int(f["_pos"] or 0))
        f["pos"] = p
        return p
    return max(0, int(f.get("pos", f.get("_pos", 0)) or 0))


def bb_set_pos(obj, pos: int):
    if isinstance(obj, JObject):
        p = max(0, int(pos))
        obj.fields["pos"] = p
        obj.fields["_pos"] = p
    return obj


def bb_limit(obj) -> int:
    if not isinstance(obj, JObject):
        return 0
    f = obj.fields
    if "limit" in f and "_lim" not in f:
        lim = max(0, int(f["limit"]))
        f["_lim"] = lim
        return lim
    if "_lim" in f and "limit" not in f:
        lim = max(0, int(f["_lim"]))
        f["limit"] = lim
        return lim
    if "limit" in f:
        return max(0, int(f["limit"]))
    lim = len(bb_buf(obj))
    f["limit"] = lim
    f["_lim"] = lim
    return lim


def bb_set_limit(obj, lim: int):
    if isinstance(obj, JObject):
        val = max(0, int(lim))
        obj.fields["limit"] = val
        obj.fields["_lim"] = val
    return obj


def bb_is_big(obj) -> bool:
    if not isinstance(obj, JObject):
        return True
    f = obj.fields
    if "order" in f and "_big" not in f:
        b = f["order"] != "little"
        f["_big"] = b
        return b
    if "_big" in f and "order" not in f:
        b = bool(f["_big"])
        f["order"] = "big" if b else "little"
        return b
    if "order" in f:
        return f["order"] != "little"
    f["order"] = "big"
    f["_big"] = True
    return True


def bb_set_big(obj, big: bool):
    if isinstance(obj, JObject):
        b = bool(big)
        obj.fields["_big"] = b
        obj.fields["order"] = "big" if b else "little"
    return obj


def bb_make(buf=b"", limit=None, big: bool = True, cls: str = "Ljava/nio/ByteBuffer;") -> JObject:
    ba = buf if isinstance(buf, bytearray) else bytearray(buf or b"")
    lim = len(ba) if limit is None else max(0, int(limit))
    return JObject(cls, {
        "buf": ba,
        "_buf": ba,
        "pos": 0,
        "_pos": 0,
        "limit": lim,
        "_lim": lim,
        "order": "big" if big else "little",
        "_big": bool(big),
        "_mark": -1,
        "_ro": False,
        "_offset": 0,
    })


# ---------------------------------------------------------------------------
# Common Primitive / Text / Exception / VFS Helpers
# ---------------------------------------------------------------------------

def _is_null(x) -> bool:
    return x is None or (type(x) is int and x == 0)


def _txt(x) -> str | None:
    if _is_null(x):
        return None
    if isinstance(x, JString):
        return x.text
    if isinstance(x, str):
        return x
    if isinstance(x, JObject):
        f = x.fields
        for k in ("$text", "text", "sb", "buf", "path", "$uri", "$str"):
            v = f.get(k)
            if v is not None:
                return v if isinstance(v, str) else str(v)
    return None


def _str_or_empty(x) -> str:
    t = _txt(x)
    return t if t is not None else ""


def _bytes_from(v) -> bytes:
    if _is_null(v):
        return b""
    data = getattr(v, "data", None)
    if data is not None:
        return bytes(int(x) & 0xFF for x in data)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, JString):
        return (v.text or "").encode("utf-8", errors="replace")
    return b""


def _s32(v) -> int:
    n = int(v or 0) & 0xFFFFFFFF
    return n - 0x100000000 if (n & 0x80000000) else n


def _s64(v) -> int:
    n = int(v or 0) & 0xFFFFFFFFFFFFFFFF
    return n - 0x10000000000000000 if (n & 0x8000000000000000) else n


def _s8(v) -> int:
    n = int(v or 0) & 0xFF
    return n - 0x100 if (n & 0x80) else n


def _unpack_ieee(x, is_f32: bool) -> float:
    """Decode Dalvik IEEE-754 int bit-patterns (or boxed/float values) into Python float."""
    if isinstance(x, float):
        return x
    if isinstance(x, JObject) and "value" in x.fields:
        return _unpack_ieee(x.fields.get("value"), is_f32)
    if isinstance(x, int) and not isinstance(x, bool):
        fmt, mask = ("<I", 0xFFFFFFFF) if is_f32 else ("<Q", 0xFFFFFFFFFFFFFFFF)
        out_fmt = "<f" if is_f32 else "<d"
        try:
            return struct.unpack(out_fmt, struct.pack(fmt, x & mask))[0]
        except Exception:
            pass
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def _as_f64(x) -> float:
    return _unpack_ieee(x, False)


def _as_f32(x) -> float:
    return _unpack_ieee(x, True)


def _raise_exc(cls_desc: str, msg: str = "") -> None:
    dotted = cls_desc[1:-1].replace("/", ".") if cls_desc.startswith("L") and cls_desc.endswith(";") else cls_desc
    raise DalvikException(
        f"{dotted}: {msg}" if msg else dotted,
        payload=JObject(cls_desc, {"message": msg, "detailMessage": JString(msg)}),
    )


def _desc_of_class(stubs, cls_obj) -> str | None:
    if hasattr(stubs, "_desc_from_class_obj"):
        return stubs._desc_from_class_obj(cls_obj)
    if isinstance(cls_obj, JObject):
        nm = cls_obj.fields.get("name")
        if nm:
            s = str(nm)
            if s.startswith("L") or s.startswith("["):
                return s
            return "L" + s.replace(".", "/") + ";"
    return None


def _intern_class(stubs, desc_or_dotted: str) -> JObject:
    vm = getattr(stubs, "vm", None)
    if vm is not None and hasattr(vm, "intern_class"):
        return vm.intern_class(desc_or_dotted)
    s = str(desc_or_dotted or "")
    if s.startswith("L") and s.endswith(";"):
        s = s[1:-1].replace("/", ".")
    return JObject("Ljava/lang/Class;", {"name": s})


def _invoke_vm_or_stub(stubs, recv, cls_hint: str, name: str, sig: str, call_args: list):
    vm = getattr(stubs, "vm", None)
    if vm is not None and isinstance(recv, JObject):
        md = vm._resolve_method(recv.cls, recv.cls, name, sig)
        if md is not None:
            return True, vm._run_mdef(md, [recv] + list(call_args))
    if isinstance(recv, JObject):
        wants = vm._build_wants(recv.cls, cls_hint) if vm is not None else [recv.cls, cls_hint, "Ljava/lang/Object;"]
        return stubs.resolve_phased(wants, recv.cls, cls_hint, name, sig, [recv] + list(call_args), False)
    return False, None


def _bundle_map(obj) -> dict:
    if not isinstance(obj, JObject):
        return {}
    m_a = obj.fields.get("map")
    m_b = obj.fields.get("$map")
    if isinstance(m_a, dict) and isinstance(m_b, dict):
        if m_a is not m_b:
            merged = dict(m_a)
            merged.update(m_b)
            m_a.clear()
            m_a.update(merged)
            obj.fields["$map"] = m_a
        return m_a
    m = m_a if isinstance(m_a, dict) else (m_b if isinstance(m_b, dict) else {})
    obj.fields["map"] = m
    obj.fields["$map"] = m
    return m


# ---------------------------------------------------------------------------
# Unified VFS Helpers (works both with fs_bridge._shared_fs and VirtualFS)
# ---------------------------------------------------------------------------

def _norm_path(p) -> str:
    if p is None:
        return ""
    s = str(p).replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


def _parent_str(p: str | None) -> str | None:
    if not p or p == "/":
        return None
    d = posixpath.dirname(p)
    return d if d else None


def _vfs_dirs(stubs) -> set[str]:
    return stubs.__dict__.setdefault("_vfs_dirs", {
        "/", "/data", "/data/data", "/data/local", "/data/local/tmp", "/storage", "/storage/emulated", "/storage/emulated/0",
    })


def _vfs_mtimes(stubs) -> dict[str, int]:
    return stubs.__dict__.setdefault("_vfs_mtimes", {})


def _vfs_exists(stubs, p: str) -> bool:
    if not p:
        return False
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        try:
            return fs.lookup(p) is not None or fs.exists(p)
        except Exception:
            return False
    if p in _vfs_dirs(stubs):
        return True
    vfs = getattr(stubs, "vfs", None)
    if vfs is not None:
        return vfs.exists(p)
    return False


def _vfs_is_file(stubs, p: str) -> bool:
    if not p:
        return False
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        ino = fs.lookup(p)
        return ino is not None and getattr(ino, "kind", None) == "file"
    vfs = getattr(stubs, "vfs", None)
    return vfs is not None and vfs.exists(p)


def _vfs_is_dir(stubs, p: str) -> bool:
    if not p:
        return False
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        ino = fs.lookup(p)
        return ino is not None and getattr(ino, "kind", None) == "dir"
    if p in _vfs_dirs(stubs):
        return True
    vfs = getattr(stubs, "vfs", None)
    if vfs is not None:
        pref = "/" if p == "/" else p + "/"
        return any(k.startswith(pref) for k in vfs.files)
    return False


def _vfs_read_bytes(stubs, p: str) -> bytes | None:
    if not p:
        return None
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        ino = fs.lookup(p)
        if ino is not None:
            try:
                return bytes(fs.read(ino))
            except Exception:
                return b""
        ret = getattr(fs, "_retained_files", None) or {}
        if p in ret:
            return bytes(ret[p])
        return None
    vfs = getattr(stubs, "vfs", None)
    if vfs is not None and p in vfs.files:
        return bytes(vfs.files[p])
    return None


def _vfs_write_bytes(stubs, p: str, data: bytes) -> None:
    if not p:
        return
    raw = bytes(data)
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        try:
            fs.add_file(p, raw)
        except Exception:
            pass
    else:
        vfs = getattr(stubs, "vfs", None)
        if vfs is not None:
            vfs.mount(p, raw)
        _vfs_mkdirs(stubs, _parent_str(p) or "")
    _vfs_mtimes(stubs)[p] = int(getattr(stubs, "_clock_ms", 1700000000000))


def _vfs_mkdirs(stubs, p: str, parents: bool = True) -> bool:
    if not p:
        return False
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        try:
            if not parents:
                par = _parent_str(p)
                if par and par != "/" and fs.lookup(par) is None:
                    return False
            fs.add_dir(p)
            return True
        except Exception:
            return False
    dirs = _vfs_dirs(stubs)
    if p in dirs or _vfs_is_file(stubs, p):
        return p in dirs
    if not parents:
        par = _parent_str(p)
        if par and par != "/" and par not in dirs:
            return False
        dirs.add(p)
        return True
    parts = [s for s in p.split("/") if s]
    cur = "/" if p.startswith("/") else ""
    if cur:
        dirs.add(cur)
    for seg in parts:
        cur = (cur.rstrip("/") + "/" + seg) if cur else seg
        dirs.add(cur)
    return True


def _vfs_delete(stubs, p: str) -> bool:
    if not p:
        return False
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        try:
            return bool(fs.unlink(p))
        except Exception:
            return False
    removed = False
    vfs = getattr(stubs, "vfs", None)
    if vfs is not None and p in vfs.files:
        del vfs.files[p]
        removed = True
    dirs = _vfs_dirs(stubs)
    if p in dirs and p != "/":
        dirs.discard(p)
        removed = True
    _vfs_mtimes(stubs).pop(p, None)
    return removed


def _vfs_listdir(stubs, p: str) -> list[str]:
    if not p:
        return []
    fs = getattr(stubs, "_shared_fs", None)
    if fs is not None:
        try:
            return [(n[0] if isinstance(n, tuple) else n) for n in fs.listdir(p)]
        except Exception:
            return []
    base = "/" if p == "/" else p.rstrip("/") + "/"
    names: set[str] = set()
    vfs = getattr(stubs, "vfs", None)
    if vfs is not None:
        for k in vfs.files:
            if k.startswith(base):
                rest = k[len(base):]
                if rest:
                    names.add(rest.split("/", 1)[0])
    for d in _vfs_dirs(stubs):
        if d != p and d.startswith(base):
            rest = d[len(base):]
            if rest:
                names.add(rest.split("/", 1)[0])
    return sorted(names)


def _read_stream_bytes(stubs, inp, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Liest Rohbytes aus einem InputStream (VFS / _mem / $data / buf / Smali read)."""
    cur = inp
    seen = set()
    while isinstance(cur, JObject) and id(cur) not in seen:
        seen.add(id(cur))
        f = cur.fields
        for k in ("_mem", "$data", "buf", "_buf"):
            raw = f.get(k)
            if raw is not None:
                data = _bytes_from(raw)
                pos = int(f.get("pos", f.get("$pos", f.get("_pos", 0))) or 0)
                return data[max(0, pos):]
        p = f.get("path")
        if p:
            vb = _vfs_read_bytes(stubs, _norm_path(p))
            if vb is not None:
                pos = int(f.get("pos", 0) or 0)
                return vb[max(0, pos):]
        nxt = f.get("in") or f.get("_in")
        if isinstance(nxt, JObject):
            cur = nxt
            continue
        break
    if isinstance(inp, JObject):
        out = bytearray()
        tmp = JArray("[B", [0] * 4096)
        while len(out) < max_bytes:
            ok, k = _invoke_vm_or_stub(stubs, inp, "Ljava/io/InputStream;", "read", "([BII)I", [tmp, 0, 4096])
            if not ok or not isinstance(k, int) or k <= 0:
                break
            out.extend(int(x) & 0xFF for x in tmp.data[:k])
        return bytes(out)
    return b""


def _write_stream_bytes(stubs, out_s, data: bytes) -> None:
    if not isinstance(out_s, JObject) or not data:
        return
    arr = JArray("[B", list(data))
    vm = getattr(stubs, "vm", None)
    if vm is not None and vm._resolve_method(out_s.cls, out_s.cls, "write", "([B)V") is not None:
        vm.invoke_virtual(out_s, "write", "([B)V", (out_s, arr))
        return
    if (out_s.cls, "write", "([B)V") in stubs._stubs:
        stubs.resolve(out_s.cls, "write", "([B)V", [out_s, arr], False)
        return
    ba = out_s.fields.get("buf")
    if not isinstance(ba, bytearray):
        ba = bytearray(_bytes_from(ba))
        out_s.fields["buf"] = ba
    ba.extend(data)
    out_s.fields["_buf"] = ba
    p = out_s.fields.get("path")
    if p:
        _vfs_write_bytes(stubs, _norm_path(p), bytes(ba))


# ---------------------------------------------------------------------------
# 1. Math & StrictMath
# ---------------------------------------------------------------------------

def _install_math_strictmath(stubs, _add) -> None:
    def _safe_f(fn, *xs) -> float:
        try:
            return float(fn(*xs))
        except Exception:
            return float("nan")

    def _fmin(x: float, y: float) -> float:
        if math.isnan(x) or math.isnan(y):
            return float("nan")
        if x == 0.0 and y == 0.0:
            return -0.0 if (math.copysign(1.0, x) < 0 or math.copysign(1.0, y) < 0) else 0.0
        return x if x < y else y

    def _fmax(x: float, y: float) -> float:
        if math.isnan(x) or math.isnan(y):
            return float("nan")
        if x == 0.0 and y == 0.0:
            return 0.0 if (math.copysign(1.0, x) > 0 or math.copysign(1.0, y) > 0) else -0.0
        return x if x > y else y

    def _round_f(x: float) -> int:
        if math.isnan(x):
            return 0
        v = math.floor(x + 0.5)
        if v >= 2147483647.0:
            return 2147483647
        if v <= -2147483648.0:
            return -2147483648
        return int(v)

    def _round_d(x: float) -> int:
        if math.isnan(x):
            return 0
        v = math.floor(x + 0.5)
        if v >= 9223372036854775807.0:
            return 9223372036854775807
        if v <= -9223372036854775808.0:
            return -9223372036854775808
        return int(v)

    def _signum(x: float) -> float:
        if math.isnan(x) or x == 0.0:
            return x
        return 1.0 if x > 0.0 else -1.0

    def _cbrt(x: float) -> float:
        if math.isnan(x) or math.isinf(x) or x == 0.0:
            return x
        return math.copysign(abs(x) ** (1.0 / 3.0), x)

    def _ulp_d(x: float) -> float:
        if math.isnan(x):
            return float("nan")
        if math.isinf(x):
            return float("inf")
        ax = abs(x)
        if ax == 0.0:
            return 5e-324
        return abs(math.nextafter(ax, float("inf")) - ax)

    def _ulp_f(x: float) -> float:
        if math.isnan(x):
            return float("nan")
        if math.isinf(x):
            return float("inf")
        ax = abs(x)
        if ax == 0.0:
            return 1.4e-45
        _, e = math.frexp(ax)
        return math.ldexp(1.0, max(-149, e - 24))

    def _get_exp(x: float, max_e: int, min_e: int) -> int:
        if math.isnan(x) or math.isinf(x):
            return max_e
        if x == 0.0:
            return min_e
        _, e = math.frexp(abs(x))
        return max(min_e, e - 1)

    def _floor_div(a: int, b: int) -> int:
        if b == 0:
            _raise_exc("Ljava/lang/ArithmeticException;", "/ by zero")
        return a // b

    def _floor_mod(a: int, b: int) -> int:
        if b == 0:
            _raise_exc("Ljava/lang/ArithmeticException;", "/ by zero")
        return a % b

    def _chk_i32(val: int) -> int:
        if val < -0x80000000 or val > 0x7FFFFFFF:
            _raise_exc("Ljava/lang/ArithmeticException;", "integer overflow")
        return val

    def _chk_i64(val: int) -> int:
        if val < -0x8000000000000000 or val > 0x7FFFFFFFFFFFFFFF:
            _raise_exc("Ljava/lang/ArithmeticException;", "long overflow")
        return val

    def _rand(_a=None) -> float:
        seq = (getattr(stubs, "_math_rng_seq", 0) + 1) & 0x7FFFFFFF
        stubs._math_rng_seq = seq
        val = ((seq * 1103515245 + 12345) & 0x7FFFFFFF) / float(0x80000000)
        return float(val)

    for mc in ("Ljava/lang/Math;", "Ljava/lang/StrictMath;"):
        _add(mc, "abs", "(I)I", lambda a: (_s32(abs(_s32(a[0]))) if a else 0))
        _add(mc, "abs", "(J)J", lambda a: (_s64(abs(_s64(a[0]))) if a else 0))
        _add(mc, "abs", "(F)F", lambda a: abs(_as_f32(a[0])) if a else 0.0)
        _add(mc, "abs", "(D)D", lambda a: abs(_as_f64(a[0])) if a else 0.0)
        _add(mc, "min", "(II)I", lambda a: min(_s32(a[0]), _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "min", "(JJ)J", lambda a: min(_s64(a[0]), _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "min", "(FF)F", lambda a: _fmin(_as_f32(a[0]), _as_f32(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "min", "(DD)D", lambda a: _fmin(_as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "max", "(II)I", lambda a: max(_s32(a[0]), _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "max", "(JJ)J", lambda a: max(_s64(a[0]), _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "max", "(FF)F", lambda a: _fmax(_as_f32(a[0]), _as_f32(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "max", "(DD)D", lambda a: _fmax(_as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "round", "(F)I", lambda a: _round_f(_as_f32(a[0])) if a else 0)
        _add(mc, "round", "(D)J", lambda a: _round_d(_as_f64(a[0])) if a else 0)
        _add(mc, "floor", "(D)D", lambda a: float(math.floor(_as_f64(a[0]))) if a and not math.isnan(_as_f64(a[0])) and not math.isinf(_as_f64(a[0])) else (_as_f64(a[0]) if a else 0.0))
        _add(mc, "ceil", "(D)D", lambda a: float(math.ceil(_as_f64(a[0]))) if a and not math.isnan(_as_f64(a[0])) and not math.isinf(_as_f64(a[0])) else (_as_f64(a[0]) if a else 0.0))
        _add(mc, "rint", "(D)D", lambda a: float(round(_as_f64(a[0]))) if a and not math.isnan(_as_f64(a[0])) and not math.isinf(_as_f64(a[0])) else (_as_f64(a[0]) if a else 0.0))
        _add(mc, "pow", "(DD)D", lambda a: _safe_f(math.pow, _as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "sqrt", "(D)D", lambda a: _safe_f(math.sqrt, _as_f64(a[0])) if a else 0.0)
        _add(mc, "cbrt", "(D)D", lambda a: _cbrt(_as_f64(a[0])) if a else 0.0)
        _add(mc, "sin", "(D)D", lambda a: _safe_f(math.sin, _as_f64(a[0])) if a else 0.0)
        _add(mc, "cos", "(D)D", lambda a: _safe_f(math.cos, _as_f64(a[0])) if a else 0.0)
        _add(mc, "tan", "(D)D", lambda a: _safe_f(math.tan, _as_f64(a[0])) if a else 0.0)
        _add(mc, "asin", "(D)D", lambda a: _safe_f(math.asin, _as_f64(a[0])) if a else 0.0)
        _add(mc, "acos", "(D)D", lambda a: _safe_f(math.acos, _as_f64(a[0])) if a else 0.0)
        _add(mc, "atan", "(D)D", lambda a: _safe_f(math.atan, _as_f64(a[0])) if a else 0.0)
        _add(mc, "atan2", "(DD)D", lambda a: _safe_f(math.atan2, _as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "sinh", "(D)D", lambda a: _safe_f(math.sinh, _as_f64(a[0])) if a else 0.0)
        _add(mc, "cosh", "(D)D", lambda a: _safe_f(math.cosh, _as_f64(a[0])) if a else 0.0)
        _add(mc, "tanh", "(D)D", lambda a: _safe_f(math.tanh, _as_f64(a[0])) if a else 0.0)
        _add(mc, "toRadians", "(D)D", lambda a: math.radians(_as_f64(a[0])) if a else 0.0)
        _add(mc, "toDegrees", "(D)D", lambda a: math.degrees(_as_f64(a[0])) if a else 0.0)
        _add(mc, "hypot", "(DD)D", lambda a: _safe_f(math.hypot, _as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "exp", "(D)D", lambda a: (float("inf") if _as_f64(a[0]) > 709.0 else _safe_f(math.exp, _as_f64(a[0]))) if a else 1.0)
        _add(mc, "expm1", "(D)D", lambda a: (float("inf") if _as_f64(a[0]) > 709.0 else _safe_f(math.expm1, _as_f64(a[0]))) if a else 0.0)
        _add(mc, "log", "(D)D", lambda a: (float("-inf") if _as_f64(a[0]) == 0.0 else _safe_f(math.log, _as_f64(a[0]))) if a else float("-inf"))
        _add(mc, "log10", "(D)D", lambda a: (float("-inf") if _as_f64(a[0]) == 0.0 else _safe_f(math.log10, _as_f64(a[0]))) if a else float("-inf"))
        _add(mc, "log1p", "(D)D", lambda a: (float("-inf") if _as_f64(a[0]) == -1.0 else _safe_f(math.log1p, _as_f64(a[0]))) if a else 0.0)
        _add(mc, "random", "()D", _rand)
        _add(mc, "signum", "(F)F", lambda a: _signum(_as_f32(a[0])) if a else 0.0)
        _add(mc, "signum", "(D)D", lambda a: _signum(_as_f64(a[0])) if a else 0.0)
        _add(mc, "copySign", "(FF)F", lambda a: math.copysign(_as_f32(a[0]), _as_f32(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "copySign", "(DD)D", lambda a: math.copysign(_as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "ulp", "(F)F", lambda a: _ulp_f(_as_f32(a[0])) if a else 1.4e-45)
        _add(mc, "ulp", "(D)D", lambda a: _ulp_d(_as_f64(a[0])) if a else 5e-324)
        _add(mc, "nextAfter", "(DD)D", lambda a: _safe_f(math.nextafter, _as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "nextAfter", "(FD)F", lambda a: _safe_f(math.nextafter, _as_f32(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "nextUp", "(D)D", lambda a: _safe_f(math.nextafter, _as_f64(a[0]), float("inf")) if a else 0.0)
        _add(mc, "nextUp", "(F)F", lambda a: _safe_f(math.nextafter, _as_f32(a[0]), float("inf")) if a else 0.0)
        _add(mc, "nextDown", "(D)D", lambda a: _safe_f(math.nextafter, _as_f64(a[0]), float("-inf")) if a else 0.0)
        _add(mc, "nextDown", "(F)F", lambda a: _safe_f(math.nextafter, _as_f32(a[0]), float("-inf")) if a else 0.0)
        _add(mc, "scalb", "(DI)D", lambda a: _safe_f(math.ldexp, _as_f64(a[0]), _s32(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "scalb", "(FI)F", lambda a: _safe_f(math.ldexp, _as_f32(a[0]), _s32(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "getExponent", "(D)I", lambda a: _get_exp(_as_f64(a[0]), 1024, -1023) if a else -1023)
        _add(mc, "getExponent", "(F)I", lambda a: _get_exp(_as_f32(a[0]), 128, -127) if a else -127)
        _add(mc, "IEEEremainder", "(DD)D", lambda a: _safe_f(math.remainder, _as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0.0)
        _add(mc, "fma", "(DDD)D", lambda a: _as_f64(a[0]) * _as_f64(a[1]) + _as_f64(a[2]) if len(a) > 2 else 0.0)
        _add(mc, "fma", "(FFF)F", lambda a: _as_f32(a[0]) * _as_f32(a[1]) + _as_f32(a[2]) if len(a) > 2 else 0.0)
        _add(mc, "floorDiv", "(II)I", lambda a: _floor_div(_s32(a[0]), _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "floorDiv", "(JJ)J", lambda a: _floor_div(_s64(a[0]), _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "floorMod", "(II)I", lambda a: _floor_mod(_s32(a[0]), _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "floorMod", "(JJ)J", lambda a: _floor_mod(_s64(a[0]), _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "addExact", "(II)I", lambda a: _chk_i32(_s32(a[0]) + _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "addExact", "(JJ)J", lambda a: _chk_i64(_s64(a[0]) + _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "subtractExact", "(II)I", lambda a: _chk_i32(_s32(a[0]) - _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "subtractExact", "(JJ)J", lambda a: _chk_i64(_s64(a[0]) - _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "multiplyExact", "(II)I", lambda a: _chk_i32(_s32(a[0]) * _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "multiplyExact", "(JJ)J", lambda a: _chk_i64(_s64(a[0]) * _s64(a[1])) if len(a) > 1 else 0)
        _add(mc, "multiplyExact", "(JI)J", lambda a: _chk_i64(_s64(a[0]) * _s32(a[1])) if len(a) > 1 else 0)
        _add(mc, "incrementExact", "(I)I", lambda a: _chk_i32(_s32(a[0]) + 1) if a else 1)
        _add(mc, "incrementExact", "(J)J", lambda a: _chk_i64(_s64(a[0]) + 1) if a else 1)
        _add(mc, "decrementExact", "(I)I", lambda a: _chk_i32(_s32(a[0]) - 1) if a else -1)
        _add(mc, "decrementExact", "(J)J", lambda a: _chk_i64(_s64(a[0]) - 1) if a else -1)
        _add(mc, "negateExact", "(I)I", lambda a: _chk_i32(-_s32(a[0])) if a else 0)
        _add(mc, "negateExact", "(J)J", lambda a: _chk_i64(-_s64(a[0])) if a else 0)
        _add(mc, "toIntExact", "(J)I", lambda a: _chk_i32(_s64(a[0])) if a else 0)


# ---------------------------------------------------------------------------
# 2. java.io.* & java.nio.file.* & java.net.URI / URL / IDN
# ---------------------------------------------------------------------------

def _uri_text(u) -> str:
    if isinstance(u, JObject):
        return str(u.fields.get("$uri") or u.fields.get("_uri") or u.fields.get("text") or "")
    return _str_or_empty(u)


def _install_io_and_vfs(stubs, _add, R) -> None:
    FL = "Ljava/io/File;"

    def _fpath(o) -> str:
        return str(o.fields.get("path", "") or "") if isinstance(o, JObject) else ""

    def _f_init1(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["path"] = _norm_path(_txt(a[1]) if len(a) > 1 else "")
        return a[0] if a else None

    def _f_init2(a):
        if a and isinstance(a[0], JObject):
            par = _fpath(a[1]) if len(a) > 1 and isinstance(a[1], JObject) else (_txt(a[1]) if len(a) > 1 else "")
            child = (_txt(a[2]) if len(a) > 2 else "") or ""
            if par:
                joined = par.rstrip("/") + "/" + child.lstrip("/") if child else par
            else:
                joined = child
            a[0].fields["path"] = _norm_path(joined)
        return a[0] if a else None

    def _f_init_uri(a):
        if a and isinstance(a[0], JObject):
            u = _uri_text(a[1] if len(a) > 1 else None)
            p = urlparse(u).path if u else ""
            a[0].fields["path"] = _norm_path(unquote(p))
        return a[0] if a else None

    # R(...) fuer exists / <init>(File, String) / getCanonicalPath -> entfernt g14 host os.path.exists!
    _add(FL, "<init>", "(Ljava/lang/String;)V", _f_init1)
    R(FL, "<init>", "(Ljava/io/File;Ljava/lang/String;)V", _f_init2)
    _add(FL, "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", _f_init2)
    _add(FL, "<init>", "(Ljava/net/URI;)V", _f_init_uri)

    R(FL, "exists", "()Z", lambda a: 1 if (a and _vfs_exists(stubs, _fpath(a[0]))) else 0)
    _add(FL, "canRead", "()Z", lambda a: 1 if (a and _vfs_exists(stubs, _fpath(a[0]))) else 0)
    _add(FL, "canWrite", "()Z", lambda a: 1 if (a and _vfs_exists(stubs, _fpath(a[0]))) else 0)
    _add(FL, "canExecute", "()Z", lambda a: 1 if (a and _vfs_exists(stubs, _fpath(a[0]))) else 0)
    _add(FL, "isFile", "()Z", lambda a: 1 if (a and _vfs_is_file(stubs, _fpath(a[0]))) else 0)
    _add(FL, "isDirectory", "()Z", lambda a: 1 if (a and _vfs_is_dir(stubs, _fpath(a[0]))) else 0)
    _add(FL, "isAbsolute", "()Z", lambda a: 1 if (a and _fpath(a[0]).startswith("/")) else 0)
    _add(FL, "getPath", "()Ljava/lang/String;", lambda a: JString(_fpath(a[0])) if a else JString(""))
    _add(FL, "toString", "()Ljava/lang/String;", lambda a: JString(_fpath(a[0])) if a else JString(""))
    _add(FL, "getAbsolutePath", "()Ljava/lang/String;", lambda a: JString(_fpath(a[0])) if a else JString(""))
    R(FL, "getCanonicalPath", "()Ljava/lang/String;", lambda a: JString(_fpath(a[0])) if a else JString(""))
    _add(FL, "getAbsoluteFile", f"(){FL}", lambda a: JObject(FL, {"path": _fpath(a[0])}) if a else JObject(FL, {"path": ""}))
    _add(FL, "getCanonicalFile", f"(){FL}", lambda a: JObject(FL, {"path": _fpath(a[0])}) if a else JObject(FL, {"path": ""}))
    _add(FL, "getName", "()Ljava/lang/String;", lambda a: JString(posixpath.basename(_fpath(a[0]))) if a else JString(""))
    R(FL, "getParent", "()Ljava/lang/String;", lambda a: (lambda p: JString(p) if p is not None else None)(_parent_str(_fpath(a[0])) if a else None))
    R(FL, "getParentFile", f"(){FL}", lambda a: (lambda p: JObject(FL, {"path": p}) if p is not None else None)(_parent_str(_fpath(a[0])) if a else None))
    _add(FL, "length", "()J", lambda a: (lambda b: len(b) if b is not None else 0)(_vfs_read_bytes(stubs, _fpath(a[0])) if a else None))
    _add(FL, "mkdir", "()Z", lambda a: 1 if (a and _vfs_mkdirs(stubs, _fpath(a[0]), parents=False)) else 0)
    _add(FL, "mkdirs", "()Z", lambda a: 1 if (a and _vfs_mkdirs(stubs, _fpath(a[0]), parents=True)) else 0)

    def _f_create_new(a):
        p = _fpath(a[0]) if a else ""
        if not p or _vfs_exists(stubs, p):
            return 0
        _vfs_write_bytes(stubs, p, b"")
        return 1

    def _f_create_temp(a):
        pref = _str_or_empty(a[0] if a else None) or "tmp"
        suf = _txt(a[1]) if len(a) > 1 else None
        if suf is None:
            suf = ".tmp"
        dobj = a[2] if len(a) > 2 and isinstance(a[2], JObject) else None
        ddir = _fpath(dobj) if dobj is not None else "/data/local/tmp"
        _vfs_mkdirs(stubs, ddir or "/data/local/tmp", parents=True)
        seq = int(getattr(stubs, "_tmp_file_seq", 0)) + 1
        stubs._tmp_file_seq = seq
        full = _norm_path(f"{ddir.rstrip('/')}/{pref}{seq}{suf}")
        _vfs_write_bytes(stubs, full, b"")
        return JObject(FL, {"path": full})

    def _f_rename(a):
        src = _fpath(a[0]) if a else ""
        dst = _fpath(a[1]) if len(a) > 1 else ""
        if not src or not dst:
            return 0
        raw = _vfs_read_bytes(stubs, src)
        if raw is None:
            return 0
        _vfs_write_bytes(stubs, dst, raw)
        _vfs_delete(stubs, src)
        return 1

    def _f_list(a):
        p = _fpath(a[0]) if a else ""
        if not _vfs_is_dir(stubs, p):
            return None
        return JArray("[Ljava/lang/String;", [JString(nm) for nm in _vfs_listdir(stubs, p)])

    def _f_list_files(a):
        p = _fpath(a[0]) if a else ""
        if not _vfs_is_dir(stubs, p):
            return None
        flt = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        out = []
        for nm in _vfs_listdir(stubs, p):
            fp = _norm_path(("/" + nm) if p == "/" else (p + "/" + nm))
            fobj = JObject(FL, {"path": fp})
            if flt is not None:
                vm = getattr(stubs, "vm", None)
                if vm is not None and vm._resolve_method(flt.cls, flt.cls, "accept", "(Ljava/io/File;Ljava/lang/String;)Z") is not None:
                    try:
                        if not vm.invoke_virtual(flt, "accept", "(Ljava/io/File;Ljava/lang/String;)Z", (flt, a[0], JString(nm))):
                            continue
                    except Exception:
                        pass
                elif vm is not None and vm._resolve_method(flt.cls, flt.cls, "accept", "(Ljava/io/File;)Z") is not None:
                    try:
                        if not vm.invoke_virtual(flt, "accept", "(Ljava/io/File;)Z", (flt, fobj)):
                            continue
                    except Exception:
                        pass
            out.append(fobj)
        return JArray("[Ljava/io/File;", out)

    _add(FL, "createNewFile", "()Z", _f_create_new)
    _add(FL, "createTempFile", f"(Ljava/lang/String;Ljava/lang/String;){FL}", _f_create_temp)
    _add(FL, "createTempFile", f"(Ljava/lang/String;Ljava/lang/String;{FL}){FL}", _f_create_temp)
    _add(FL, "delete", "()Z", lambda a: 1 if (a and _vfs_delete(stubs, _fpath(a[0]))) else 0)
    _add(FL, "renameTo", f"({FL})Z", _f_rename)
    _add(FL, "list", "()[Ljava/lang/String;", _f_list)
    _add(FL, "listFiles", f"()[{FL}", _f_list_files)
    _add(FL, "listFiles", f"(Ljava/io/FilenameFilter;)[{FL}", _f_list_files)
    _add(FL, "listFiles", f"(Ljava/io/FileFilter;)[{FL}", _f_list_files)
    _add(FL, "lastModified", "()J", lambda a: int(_vfs_mtimes(stubs).get(_fpath(a[0]), 0)) if a else 0)
    _add(FL, "setLastModified", "(J)Z", lambda a: (_vfs_mtimes(stubs).__setitem__(_fpath(a[0]), int(a[1])) if len(a) > 1 and a else None, 1)[1])
    _add(FL, "toURI", "()Ljava/net/URI;", lambda a: JObject("Ljava/net/URI;", {"$uri": "file://" + _fpath(a[0])}) if a else JObject("Ljava/net/URI;", {"$uri": "file:///"}))
    _add(FL, "toPath", "()Ljava/nio/file/Path;", lambda a: JObject("Ljava/nio/file/Path;", {"path": _fpath(a[0])}) if a else JObject("Ljava/nio/file/Path;", {"path": ""}))
    _add(FL, "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == FL and _fpath(a[0]) == _fpath(a[1])) else 0)
    _add(FL, "hashCode", "()I", lambda a: stubs._java_strhash(_fpath(a[0])) if a else 0)

    # FileDescriptor, FileInputStream, FileOutputStream, RandomAccessFile (mit Pure-Java VFS Fallback)
    FD = "Ljava/io/FileDescriptor;"
    FIS = "Ljava/io/FileInputStream;"
    FOS = "Ljava/io/FileOutputStream;"
    RAF = "Ljava/io/RandomAccessFile;"
    FC = "Ljava/nio/channels/FileChannel;"

    _add(FD, "<init>", "()V", lambda a: a[0].fields.setdefault("descriptor", -1) if a and isinstance(a[0], JObject) else None)
    _add(FD, "sync", "()V", lambda a: None)
    _add(FD, "valid", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("descriptor", a[0].fields.get("fd", -1))) >= 0) else 0)

    def _fis_init(a):
        if a and isinstance(a[0], JObject):
            src = a[1] if len(a) > 1 else None
            if isinstance(src, JObject) and src.cls == FD:
                a[0].fields["path"] = str(src.fields.get("_path") or "")
                a[0].fields["_fd"] = src
                if src.fields.get("_bytes") is not None:
                    a[0].fields["_mem"] = bytes(src.fields["_bytes"])
            else:
                p = _fpath(src) if isinstance(src, JObject) and src.cls == FL else (_txt(src) or "")
                a[0].fields["path"] = _norm_path(p)
            a[0].fields["pos"] = 0
        return a[0] if a else None

    def _fis_data(o: JObject) -> bytes:
        for k in ("$data", "_mem"):
            if k in o.fields and o.fields[k] is not None:
                return _bytes_from(o.fields[k])
        p = o.fields.get("path", "")
        raw = _vfs_read_bytes(stubs, p)
        return raw if raw is not None else b""

    def _fis_read1(a):
        if not a or not isinstance(a[0], JObject):
            return -1
        data = _fis_data(a[0])
        pos = int(a[0].fields.get("pos", 0) or 0)
        if pos >= len(data):
            return -1
        a[0].fields["pos"] = pos + 1
        return data[pos] & 0xFF

    def _fis_readb(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2 or not isinstance(a[1], JArray):
            return -1
        data = _fis_data(a[0])
        pos = int(a[0].fields.get("pos", 0) or 0)
        if pos >= len(data):
            return -1
        arr = a[1]
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else len(arr.data)
        n = min(max(0, cnt), len(arr.data) - off, len(data) - pos)
        if n <= 0:
            return -1
        for i in range(n):
            arr.data[off + i] = data[pos + i]
        a[0].fields["pos"] = pos + n
        return n

    def _fis_skip(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        data = _fis_data(a[0])
        pos = int(a[0].fields.get("pos", 0) or 0)
        n = max(0, int(a[1] if len(a) > 1 else 0))
        adv = min(n, max(0, len(data) - pos))
        a[0].fields["pos"] = pos + adv
        return adv

    def _mk_channel(stream_obj, writable: bool, readable: bool):
        p = stream_obj.fields.get("path", "") if isinstance(stream_obj, JObject) else ""
        return JObject(FC, {
            "_stream": stream_obj,
            "path": p,
            "_pos": int(stream_obj.fields.get("pos", 0) or 0) if isinstance(stream_obj, JObject) else 0,
            "_w": writable,
            "_r": readable,
            "_open": True,
        })

    _add(FIS, "<init>", f"({FL})V", _fis_init)
    _add(FIS, "<init>", "(Ljava/lang/String;)V", _fis_init)
    _add(FIS, "<init>", f"({FD})V", _fis_init)
    _add(FIS, "read", "()I", _fis_read1)
    _add(FIS, "read", "([B)I", _fis_readb)
    _add(FIS, "read", "([BII)I", _fis_readb)
    _add(FIS, "skip", "(J)J", _fis_skip)
    _add(FIS, "available", "()I", lambda a: max(0, len(_fis_data(a[0])) - int(a[0].fields.get("pos", 0) or 0)) if a and isinstance(a[0], JObject) else 0)
    _add(FIS, "close", "()V", lambda a: None)
    _add(FIS, "getFD", f"(){FD}", lambda a: a[0].fields.setdefault("_fd", JObject(FD, {"descriptor": 10, "_path": _fpath(a[0])})) if a and isinstance(a[0], JObject) else JObject(FD, {"descriptor": -1}))
    _add(FIS, "getChannel", f"(){FC}", lambda a: _mk_channel(a[0] if a else None, False, True))

    def _fos_init(a):
        if a and isinstance(a[0], JObject):
            dst = a[1] if len(a) > 1 else None
            if isinstance(dst, JObject) and dst.cls == FD:
                p = str(dst.fields.get("_path") or "")
            else:
                p = _fpath(dst) if isinstance(dst, JObject) and dst.cls == FL else (_txt(dst) or "")
            p = _norm_path(p)
            append = bool(a[2]) if len(a) > 2 and isinstance(a[2], int) else False
            init_b = (_vfs_read_bytes(stubs, p) or b"") if append else b""
            a[0].fields["path"] = p
            a[0].fields["buf"] = bytearray(init_b)
            _vfs_write_bytes(stubs, p, bytes(init_b))
        return a[0] if a else None

    def _fos_write1(a):
        if a and isinstance(a[0], JObject):
            ba = a[0].fields.setdefault("buf", bytearray())
            ba.append(int(a[1] if len(a) > 1 else 0) & 0xFF)
            _vfs_write_bytes(stubs, a[0].fields.get("path", ""), bytes(ba))
        return None

    def _fos_writeb(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(a[1].data)
            chunk = bytes(int(x) & 0xFF for x in a[1].data[off:off + max(0, cnt)])
            ba = a[0].fields.setdefault("buf", bytearray())
            ba.extend(chunk)
            _vfs_write_bytes(stubs, a[0].fields.get("path", ""), bytes(ba))
        return None

    def _fos_flush(a):
        if a and isinstance(a[0], JObject):
            _vfs_write_bytes(stubs, a[0].fields.get("path", ""), bytes(a[0].fields.get("buf", b"")))
        return None

    _add(FOS, "<init>", f"({FL})V", _fos_init)
    _add(FOS, "<init>", f"({FL}Z)V", _fos_init)
    _add(FOS, "<init>", "(Ljava/lang/String;)V", _fos_init)
    _add(FOS, "<init>", "(Ljava/lang/String;Z)V", _fos_init)
    _add(FOS, "<init>", f"({FD})V", _fos_init)
    _add(FOS, "write", "(I)V", _fos_write1)
    _add(FOS, "write", "([B)V", _fos_writeb)
    _add(FOS, "write", "([BII)V", _fos_writeb)
    _add(FOS, "flush", "()V", _fos_flush)
    _add(FOS, "close", "()V", _fos_flush)
    _add(FOS, "getFD", f"(){FD}", lambda a: a[0].fields.setdefault("_fd", JObject(FD, {"descriptor": 11, "_path": _fpath(a[0])})) if a and isinstance(a[0], JObject) else JObject(FD, {"descriptor": -1}))
    _add(FOS, "getChannel", f"(){FC}", lambda a: _mk_channel(a[0] if a else None, True, False))

    def _raf_init(a):
        if a and isinstance(a[0], JObject):
            src = a[1] if len(a) > 1 else None
            p = _fpath(src) if isinstance(src, JObject) and src.cls == FL else (_txt(src) or "")
            p = _norm_path(p)
            mode = _str_or_empty(a[2] if len(a) > 2 else "r")
            a[0].fields["path"] = p
            a[0].fields["pos"] = 0
            a[0].fields["_mode"] = mode
            if "w" in mode and not _vfs_exists(stubs, p):
                _vfs_write_bytes(stubs, p, b"")
        return a[0] if a else None

    def _raf_write(a):
        if not a or not isinstance(a[0], JObject):
            return None
        p = a[0].fields.get("path", "")
        cur = bytearray(_vfs_read_bytes(stubs, p) or b"")
        pos = int(a[0].fields.get("pos", 0) or 0)
        if len(a) > 1 and isinstance(a[1], JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(a[1].data)
            chunk = bytes(int(x) & 0xFF for x in a[1].data[off:off + max(0, cnt)])
        else:
            chunk = bytes([int(a[1] if len(a) > 1 else 0) & 0xFF])
        end = pos + len(chunk)
        if end > len(cur):
            cur.extend(b"\x00" * (end - len(cur)))
        cur[pos:end] = chunk
        a[0].fields["pos"] = end
        _vfs_write_bytes(stubs, p, bytes(cur))
        return None

    def _raf_setlen(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            p = a[0].fields.get("path", "")
            nlen = max(0, int(a[1] or 0))
            cur = bytearray(_vfs_read_bytes(stubs, p) or b"")
            if nlen < len(cur):
                del cur[nlen:]
            elif nlen > len(cur):
                cur.extend(b"\x00" * (nlen - len(cur)))
            _vfs_write_bytes(stubs, p, bytes(cur))
            if int(a[0].fields.get("pos", 0)) > nlen:
                a[0].fields["pos"] = nlen
        return None

    _add(RAF, "<init>", f"({FL}Ljava/lang/String;)V", _raf_init)
    _add(RAF, "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", _raf_init)
    _add(RAF, "read", "()I", _fis_read1)
    _add(RAF, "read", "([B)I", _fis_readb)
    _add(RAF, "read", "([BII)I", _fis_readb)
    _add(RAF, "write", "(I)V", _raf_write)
    _add(RAF, "write", "([B)V", _raf_write)
    _add(RAF, "write", "([BII)V", _raf_write)
    _add(RAF, "seek", "(J)V", lambda a: a[0].fields.__setitem__("pos", max(0, int(a[1] if len(a) > 1 else 0))) if a and isinstance(a[0], JObject) else None)
    _add(RAF, "getFilePointer", "()J", lambda a: int(a[0].fields.get("pos", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(RAF, "length", "()J", lambda a: len(_fis_data(a[0])) if a and isinstance(a[0], JObject) else 0)
    _add(RAF, "setLength", "(J)V", _raf_setlen)
    _add(RAF, "getFD", f"(){FD}", lambda a: a[0].fields.setdefault("_fd", JObject(FD, {"descriptor": 12, "_path": _fpath(a[0])})) if a and isinstance(a[0], JObject) else JObject(FD, {"descriptor": -1}))
    _add(RAF, "getChannel", f"(){FC}", lambda a: _mk_channel(a[0] if a else None, True, True))
    _add(RAF, "close", "()V", lambda a: None)

    # ByteArrayInputStream / ByteArrayOutputStream (Pure-Java fallback)
    BAIS = "Ljava/io/ByteArrayInputStream;"
    BAOS = "Ljava/io/ByteArrayOutputStream;"

    def _bais_init(a):
        if a and isinstance(a[0], JObject):
            raw = _bytes_from(a[1] if len(a) > 1 else b"")
            off = max(0, int(a[2])) if len(a) > 3 else 0
            cnt = max(0, int(a[3])) if len(a) > 3 else len(raw) - off
            a[0].fields["buf"] = raw[off:off + cnt]
            a[0].fields["pos"] = 0
            a[0].fields["mark"] = 0
        return a[0] if a else None

    def _bais_read1(a):
        if not a or not isinstance(a[0], JObject):
            return -1
        buf = a[0].fields.get("buf", b"")
        pos = int(a[0].fields.get("pos", 0))
        if pos >= len(buf):
            return -1
        a[0].fields["pos"] = pos + 1
        return buf[pos] & 0xFF

    def _bais_readb(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2 or not isinstance(a[1], JArray):
            return -1
        buf = a[0].fields.get("buf", b"")
        pos = int(a[0].fields.get("pos", 0))
        if pos >= len(buf):
            return -1
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else len(a[1].data)
        n = min(max(0, cnt), len(a[1].data) - off, len(buf) - pos)
        if n <= 0:
            return -1
        for i in range(n):
            a[1].data[off + i] = buf[pos + i]
        a[0].fields["pos"] = pos + n
        return n

    _add(BAIS, "<init>", "([B)V", _bais_init)
    _add(BAIS, "<init>", "([BII)V", _bais_init)
    _add(BAIS, "read", "()I", _bais_read1)
    _add(BAIS, "read", "([B)I", _bais_readb)
    _add(BAIS, "read", "([BII)I", _bais_readb)
    _add(BAIS, "available", "()I", lambda a: max(0, len(a[0].fields.get("buf", b"")) - int(a[0].fields.get("pos", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(BAIS, "skip", "(J)J", lambda a: (lambda o, n: (adv := min(max(0, int(n)), max(0, len(o.fields.get("buf", b"")) - int(o.fields.get("pos", 0)))), o.fields.__setitem__("pos", int(o.fields.get("pos", 0)) + adv), adv)[2])(a[0], a[1] if len(a) > 1 else 0) if a and isinstance(a[0], JObject) else 0)
    _add(BAIS, "mark", "(I)V", lambda a: a[0].fields.__setitem__("mark", int(a[0].fields.get("pos", 0))) if a and isinstance(a[0], JObject) else None)
    _add(BAIS, "reset", "()V", lambda a: a[0].fields.__setitem__("pos", int(a[0].fields.get("mark", 0))) if a and isinstance(a[0], JObject) else None)
    _add(BAIS, "markSupported", "()Z", lambda a: 1)
    _add(BAIS, "close", "()V", lambda a: None)

    def _baos_writeb(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(a[1].data)
            ba = a[0].fields.setdefault("buf", bytearray())
            ba.extend(bytes(int(x) & 0xFF for x in a[1].data[off:off + max(0, cnt)]))
            a[0].fields["_buf"] = ba
        return None

    _add(BAOS, "<init>", "()V", lambda a: a[0].fields.update({"buf": bytearray()}) if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "<init>", "(I)V", lambda a: a[0].fields.update({"buf": bytearray()}) if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "write", "(I)V", lambda a: a[0].fields.setdefault("buf", bytearray()).append(int(a[1] if len(a) > 1 else 0) & 0xFF) if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "write", "([B)V", _baos_writeb)
    _add(BAOS, "write", "([BII)V", _baos_writeb)
    _add(BAOS, "toByteArray", "()[B", lambda a: JArray("[B", list(a[0].fields.get("buf") or a[0].fields.get("_buf") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(BAOS, "size", "()I", lambda a: len(a[0].fields.get("buf") or a[0].fields.get("_buf") or b"") if a and isinstance(a[0], JObject) else 0)
    _add(BAOS, "reset", "()V", lambda a: a[0].fields.update({"buf": bytearray()}) if a and isinstance(a[0], JObject) else None)
    _add(BAOS, "toString", "()Ljava/lang/String;", lambda a: JString(bytes(a[0].fields.get("buf") or b"").decode("utf-8", "replace")) if a and isinstance(a[0], JObject) else JString(""))
    _add(BAOS, "toString", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString(bytes(a[0].fields.get("buf") or b"").decode(_str_or_empty(a[1]) or "utf-8", "replace")) if a and isinstance(a[0], JObject) else JString(""))
    _add(BAOS, "writeTo", "(Ljava/io/OutputStream;)V", lambda a: _write_stream_bytes(stubs, a[1], bytes(a[0].fields.get("buf") or b"")) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(BAOS, "flush", "()V", lambda a: None)
    _add(BAOS, "close", "()V", lambda a: None)

    # InputStream / OutputStream / Filter* / Data* (Pure-Java fallback)
    IS = "Ljava/io/InputStream;"
    OS = "Ljava/io/OutputStream;"
    FIS_FLT = "Ljava/io/FilterInputStream;"
    FOS_FLT = "Ljava/io/FilterOutputStream;"
    BIS = "Ljava/io/BufferedInputStream;"
    BOS = "Ljava/io/BufferedOutputStream;"
    DIS = "Ljava/io/DataInputStream;"
    DOS = "Ljava/io/DataOutputStream;"

    _add(IS, "<init>", "()V", lambda a: None)
    _add(IS, "read", "()I", _fis_read1)
    _add(IS, "read", "([B)I", _fis_readb)
    _add(IS, "read", "([BII)I", _fis_readb)
    _add(IS, "skip", "(J)J", _fis_skip)
    _add(IS, "available", "()I", lambda a: 0)
    _add(IS, "mark", "(I)V", lambda a: None)
    _add(IS, "reset", "()V", lambda a: None)
    _add(IS, "markSupported", "()Z", lambda a: 0)
    _add(IS, "close", "()V", lambda a: None)
    _add(OS, "<init>", "()V", lambda a: None)
    _add(OS, "write", "(I)V", _fos_write1)
    _add(OS, "write", "([B)V", _fos_writeb)
    _add(OS, "write", "([BII)V", _fos_writeb)
    _add(OS, "flush", "()V", lambda a: None)
    _add(OS, "close", "()V", lambda a: None)

    def _flt_in_op(name, sig):
        def _fn(a):
            o = a[0] if a else None
            inner = o.fields.get("in") if isinstance(o, JObject) else None
            if not isinstance(inner, JObject):
                return -1 if sig.endswith(("I", "J")) else None
            ok, res = _invoke_vm_or_stub(stubs, inner, IS, name, sig, list(a[1:]))
            return res if ok else (-1 if sig.endswith(("I", "J")) else None)
        return _fn

    for _fc in (FIS_FLT, BIS, DIS):
        _add(_fc, "<init>", f"({IS})V", lambda a: a[0].fields.__setitem__("in", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        _add(_fc, "<init>", f"({IS}I)V", lambda a: a[0].fields.__setitem__("in", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        for _nm, _sg in (("read", "()I"), ("read", "([B)I"), ("read", "([BII)I"), ("skip", "(J)J"), ("available", "()I"), ("mark", "(I)V"), ("reset", "()V"), ("close", "()V")):
            _add(_fc, _nm, _sg, _flt_in_op(_nm, _sg))
        _add(_fc, "markSupported", "()Z", lambda a: 1)

    def _dis_read_exact(o, n: int) -> bytes:
        inner = o.fields.get("in") if isinstance(o, JObject) else None
        if not isinstance(inner, JObject) or n <= 0:
            return b""
        buf = JArray("[B", [0] * n)
        got = 0
        while got < n:
            ok, k = _invoke_vm_or_stub(stubs, inner, IS, "read", "([BII)I", [buf, got, n - got])
            if ok and isinstance(k, int) and k > 0:
                got += k
            else:
                ok1, b0 = _invoke_vm_or_stub(stubs, inner, IS, "read", "()I", [])
                if not ok1 or not isinstance(b0, int) or b0 < 0:
                    break
                buf.data[got] = b0 & 0xFF
                got += 1
        return bytes(int(x) & 0xFF for x in buf.data[:got])

    def _dis_read_fully(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JArray):
            off = int(a[2]) if len(a) > 3 else 0
            ln = int(a[3]) if len(a) > 3 else len(a[1].data)
            raw = _dis_read_exact(a[0], max(0, ln))
            for i, b in enumerate(raw):
                if off + i < len(a[1].data):
                    a[1].data[off + i] = b
        return None

    def _dis_num(a, fmt: str):
        sz = struct.calcsize(fmt)
        raw = _dis_read_exact(a[0] if a else None, sz)
        if len(raw) < sz:
            return 0
        return struct.unpack(">" + fmt, raw)[0]

    _add(DIS, "readFully", "([B)V", _dis_read_fully)
    _add(DIS, "readFully", "([BII)V", _dis_read_fully)
    _add(DIS, "readBoolean", "()Z", lambda a: 1 if _dis_num(a, "b") else 0)
    _add(DIS, "readByte", "()B", lambda a: _dis_num(a, "b"))
    _add(DIS, "readUnsignedByte", "()I", lambda a: _dis_num(a, "B"))
    _add(DIS, "readShort", "()S", lambda a: _dis_num(a, "h"))
    _add(DIS, "readUnsignedShort", "()I", lambda a: _dis_num(a, "H"))
    _add(DIS, "readInt", "()I", lambda a: _dis_num(a, "i"))
    _add(DIS, "readLong", "()J", lambda a: _dis_num(a, "q"))
    _add(DIS, "readFloat", "()F", lambda a: _dis_num(a, "f"))
    _add(DIS, "readDouble", "()D", lambda a: _dis_num(a, "d"))
    _add(DIS, "readUTF", "()Ljava/lang/String;", lambda a: JString(_dis_read_exact(a[0], _dis_num(a, "H")).decode("utf-8", "replace")) if a else JString(""))
    _add(DIS, "skipBytes", "(I)I", lambda a: int(_flt_in_op("skip", "(J)J")([a[0], int(a[1] if len(a) > 1 else 0)]) or 0))

    def _flt_out_op(name, sig):
        def _fn(a):
            o = a[0] if a else None
            inner = o.fields.get("out") if isinstance(o, JObject) else None
            if isinstance(inner, JObject):
                _invoke_vm_or_stub(stubs, inner, OS, name, sig, list(a[1:]))
            return None
        return _fn

    def _dos_write_bytes(o, raw: bytes):
        if isinstance(o, JObject):
            o.fields["_written"] = int(o.fields.get("_written", 0)) + len(raw)
            inner = o.fields.get("out")
            if isinstance(inner, JObject):
                _write_stream_bytes(stubs, inner, raw)

    for _foc in (FOS_FLT, BOS, DOS):
        _add(_foc, "<init>", f"({OS})V", lambda a: a[0].fields.__setitem__("out", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        _add(_foc, "<init>", f"({OS}I)V", lambda a: a[0].fields.__setitem__("out", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        for _nm, _sg in (("write", "(I)V"), ("write", "([B)V"), ("write", "([BII)V"), ("flush", "()V"), ("close", "()V")):
            _add(_foc, _nm, _sg, _flt_out_op(_nm, _sg))

    _add(DOS, "writeBoolean", "(Z)V", lambda a: _dos_write_bytes(a[0], b"\x01" if len(a) > 1 and a[1] else b"\x00"))
    _add(DOS, "writeByte", "(I)V", lambda a: _dos_write_bytes(a[0], struct.pack(">b", _s8(a[1] if len(a) > 1 else 0))))
    _add(DOS, "writeShort", "(I)V", lambda a: _dos_write_bytes(a[0], struct.pack(">h", _s32(a[1] if len(a) > 1 else 0) & 0xFFFF if False else max(-32768, min(32767, _s32(a[1] if len(a) > 1 else 0))))))
    _add(DOS, "writeInt", "(I)V", lambda a: _dos_write_bytes(a[0], struct.pack(">i", _s32(a[1] if len(a) > 1 else 0))))
    _add(DOS, "writeLong", "(J)V", lambda a: _dos_write_bytes(a[0], struct.pack(">q", _s64(a[1] if len(a) > 1 else 0))))
    _add(DOS, "writeFloat", "(F)V", lambda a: _dos_write_bytes(a[0], struct.pack(">f", _as_f32(a[1] if len(a) > 1 else 0.0))))
    _add(DOS, "writeDouble", "(D)V", lambda a: _dos_write_bytes(a[0], struct.pack(">d", _as_f64(a[1] if len(a) > 1 else 0.0))))
    _add(DOS, "writeBytes", "(Ljava/lang/String;)V", lambda a: _dos_write_bytes(a[0], _str_or_empty(a[1] if len(a) > 1 else "").encode("latin-1", "replace")))
    _add(DOS, "writeUTF", "(Ljava/lang/String;)V", lambda a: (lambda b: _dos_write_bytes(a[0], struct.pack(">H", min(65535, len(b))) + b[:65535]))(_str_or_empty(a[1] if len(a) > 1 else "").encode("utf-8", "replace")))
    _add(DOS, "size", "()I", lambda a: int(a[0].fields.get("_written", 0)) if a and isinstance(a[0], JObject) else 0)

    # PrintWriter & PrintStream
    PW = "Ljava/io/PrintWriter;"
    PS = "Ljava/io/PrintStream;"

    def _pw_write_str(pw_obj, text: str) -> None:
        if not isinstance(pw_obj, JObject):
            return
        w = pw_obj.fields.get("_writer")
        if isinstance(w, JObject):
            if "_wbuf" in w.fields:
                w.fields["_wbuf"].append(text)
            else:
                _invoke_vm_or_stub(stubs, w, "Ljava/io/Writer;", "write", "(Ljava/lang/String;)V", [JString(text)])
            return
        out_s = pw_obj.fields.get("_out")
        if isinstance(out_s, JObject):
            _write_stream_bytes(stubs, out_s, text.encode("utf-8", "replace"))
            return
        pw_obj.fields.setdefault("_wbuf", []).append(text)

    def _pw_init(a):
        if a and isinstance(a[0], JObject):
            target = a[1] if len(a) > 1 else None
            if isinstance(target, JObject):
                if target.cls in (FL,) or target.cls.endswith("Writer;"):
                    if target.cls == FL:
                        fos = JObject(FOS, {"path": _fpath(target), "buf": bytearray()})
                        a[0].fields["_out"] = fos
                    else:
                        a[0].fields["_writer"] = target
                else:
                    a[0].fields["_out"] = target
            elif target is not None:
                a[0].fields["_out"] = JObject(FOS, {"path": _norm_path(_str_or_empty(target)), "buf": bytearray()})
            a[0].fields.setdefault("_wbuf", [])
        return a[0] if a else None

    for _psig in ("(Ljava/io/Writer;)V", "(Ljava/io/Writer;Z)V", f"({OS})V", f"({OS}Z)V", f"({FL})V", "(Ljava/lang/String;)V"):
        _add(PW, "<init>", _psig, _pw_init)

    def _fmt_arg(sig_t: str, val) -> str:
        if sig_t == "Z":
            return "true" if val else "false"
        if sig_t == "C":
            return chr(int(val or 0) & 0xFFFF)
        if sig_t in ("I", "J"):
            return str(int(val or 0))
        if sig_t in ("F", "D"):
            return str(float(val or 0.0))
        if sig_t == "Ljava/lang/String;":
            return "null" if _is_null(val) else (_txt(val) or "")
        return stubs._obj_to_str(val)

    for _pt in ("I", "J", "F", "D", "Z", "C", "Ljava/lang/String;", "Ljava/lang/Object;"):
        _add(PW, "print", f"({_pt})V", lambda a, t=_pt: _pw_write_str(a[0], _fmt_arg(t, a[1] if len(a) > 1 else None)) if a else None)
        _add(PW, "println", f"({_pt})V", lambda a, t=_pt: _pw_write_str(a[0], _fmt_arg(t, a[1] if len(a) > 1 else None) + "\n") if a else None)
    _add(PW, "println", "()V", lambda a: _pw_write_str(a[0], "\n") if a else None)
    _add(PW, "write", "(Ljava/lang/String;)V", lambda a: _pw_write_str(a[0], _str_or_empty(a[1] if len(a) > 1 else "")) if a else None)
    _add(PW, "write", "(I)V", lambda a: _pw_write_str(a[0], chr(int(a[1] if len(a) > 1 else 0) & 0xFFFF)) if a else None)
    _add(PW, "append", f"(Ljava/lang/CharSequence;){PW}", lambda a: (_pw_write_str(a[0], _str_or_empty(a[1] if len(a) > 1 else "null")), a[0])[1] if a else None)
    _add(PW, "append", f"(C){PW}", lambda a: (_pw_write_str(a[0], chr(int(a[1] if len(a) > 1 else 0) & 0xFFFF)), a[0])[1] if a else None)
    _add(PW, "flush", "()V", lambda a: None)
    _add(PW, "close", "()V", lambda a: None)
    _add(PW, "checkError", "()Z", lambda a: 0)

    def _ps_emit(ps_obj, text: str) -> None:
        stubs.__dict__.setdefault("_stdout_lines", []).append(text)
        if isinstance(ps_obj, JObject):
            out_s = ps_obj.fields.get("_out")
            if isinstance(out_s, JObject):
                _write_stream_bytes(stubs, out_s, text.encode("utf-8", "replace"))
            else:
                ps_obj.fields.setdefault("_buf", bytearray()).extend(text.encode("utf-8", "replace"))

    _add(PS, "<init>", f"({OS})V", lambda a: a[0].fields.__setitem__("_out", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(PS, "<init>", f"({OS}Z)V", lambda a: a[0].fields.__setitem__("_out", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    for _pt in ("I", "J", "F", "D", "Z", "C", "Ljava/lang/String;", "Ljava/lang/Object;"):
        _add(PS, "print", f"({_pt})V", lambda a, t=_pt: _ps_emit(a[0], _fmt_arg(t, a[1] if len(a) > 1 else None)) if a else None)
        _add(PS, "println", f"({_pt})V", lambda a, t=_pt: _ps_emit(a[0], _fmt_arg(t, a[1] if len(a) > 1 else None) + "\n") if a else None)
    _add(PS, "println", "()V", lambda a: _ps_emit(a[0], "\n") if a else None)
    _add(PS, "flush", "()V", lambda a: None)
    _add(PS, "close", "()V", lambda a: None)

    # ObjectInputStream / ObjectOutputStream (lightweight stateful queue/stream)
    OIS = "Ljava/io/ObjectInputStream;"
    OOS = "Ljava/io/ObjectOutputStream;"

    def _oos_items(o):
        out_s = o.fields.get("out") if isinstance(o, JObject) else None
        if isinstance(out_s, JObject):
            return out_s.fields.setdefault("$oos_items", [])
        return o.fields.setdefault("$oos_items", []) if isinstance(o, JObject) else []

    def _ois_init(a):
        if a and isinstance(a[0], JObject):
            inp = a[1] if len(a) > 1 else None
            a[0].fields["in"] = inp
            items = list(inp.fields.get("$oos_items", [])) if isinstance(inp, JObject) else []
            a[0].fields["$ois_items"] = items
            a[0].fields["$ois_pos"] = 0
        return a[0] if a else None

    def _ois_next(a, default=None):
        if not a or not isinstance(a[0], JObject):
            return default
        items = a[0].fields.get("$ois_items", [])
        pos = int(a[0].fields.get("$ois_pos", 0))
        if pos < len(items):
            a[0].fields["$ois_pos"] = pos + 1
            return items[pos]
        return default

    _add(OOS, "<init>", f"({OS})V", lambda a: a[0].fields.__setitem__("out", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(OOS, "writeInt", "(I)V", lambda a: _oos_items(a[0]).append(int(a[1] if len(a) > 1 else 0)) if a else None)
    _add(OOS, "writeLong", "(J)V", lambda a: _oos_items(a[0]).append(int(a[1] if len(a) > 1 else 0)) if a else None)
    _add(OOS, "writeObject", "(Ljava/lang/Object;)V", lambda a: _oos_items(a[0]).append(a[1] if len(a) > 1 else None) if a else None)
    _add(OOS, "writeUTF", "(Ljava/lang/String;)V", lambda a: _oos_items(a[0]).append(a[1] if len(a) > 1 else JString("")) if a else None)
    _add(OOS, "flush", "()V", lambda a: None)
    _add(OOS, "close", "()V", lambda a: None)

    _add(OIS, "<init>", f"({IS})V", _ois_init)
    _add(OIS, "readInt", "()I", lambda a: int(_ois_next(a, 0) or 0))
    _add(OIS, "readLong", "()J", lambda a: int(_ois_next(a, 0) or 0))
    _add(OIS, "readObject", "()Ljava/lang/Object;", lambda a: _ois_next(a, None))
    _add(OIS, "readUTF", "()Ljava/lang/String;", lambda a: (lambda v: v if isinstance(v, JString) else JString(str(v or "")))(_ois_next(a, JString(""))))
    _add(OIS, "close", "()V", lambda a: None)

    # java.nio.file.Paths / Path / Files
    PATHS = "Ljava/nio/file/Paths;"
    PATH = "Ljava/nio/file/Path;"
    FILES = "Ljava/nio/file/Files;"

    def _paths_get(a):
        first = _str_or_empty(a[0] if a else "")
        more = []
        if len(a) > 1 and isinstance(a[1], JArray):
            more = [_str_or_empty(x) for x in a[1].data if _txt(x)]
        joined = "/".join([first] + more) if more else first
        return JObject(PATH, {"path": _norm_path(joined)})

    def _files_readlink(a):
        p = _fpath(a[0]) if a and isinstance(a[0], JObject) else ""
        fs = getattr(stubs, "_shared_fs", None)
        if fs is not None and p:
            ino = fs.lookup(p, follow=False)
            if ino is not None and getattr(ino, "target", None):
                return JObject(PATH, {"path": _norm_path(ino.target)})
        return JObject(PATH, {"path": p})

    _add(PATHS, "get", f"(Ljava/lang/String;[Ljava/lang/String;){PATH}", _paths_get)
    _add(PATH, "toFile", f"(){FL}", lambda a: JObject(FL, {"path": _fpath(a[0])}) if a else JObject(FL, {"path": ""}))
    _add(PATH, "toString", "()Ljava/lang/String;", lambda a: JString(_fpath(a[0])) if a else JString(""))
    _add(PATH, "getFileName", f"(){PATH}", lambda a: JObject(PATH, {"path": posixpath.basename(_fpath(a[0]))}) if a else JObject(PATH, {"path": ""}))
    _add(FILES, "readSymbolicLink", f"({PATH}){PATH}", _files_readlink)
    _add(FILES, "exists", f"({PATH}[Ljava/nio/file/LinkOption;)Z", lambda a: 1 if (a and _vfs_exists(stubs, _fpath(a[0]))) else 0)
    _add(FILES, "readAllBytes", f"({PATH})[B", lambda a: JArray("[B", list(_vfs_read_bytes(stubs, _fpath(a[0])) or b"")) if a else JArray("[B", []))

    # java.net.URI / URL / URLEncoder / URLDecoder / IDN
    JURI = "Ljava/net/URI;"
    JURL = "Ljava/net/URL;"

    def _juri_init(a):
        if a and isinstance(a[0], JObject):
            if len(a) == 2:
                a[0].fields["$uri"] = _str_or_empty(a[1])
            elif len(a) == 4:
                sc, ssp, fr = _str_or_empty(a[1]), _str_or_empty(a[2]), _txt(a[3])
                u = (f"{sc}:{ssp}" if sc else ssp) + (f"#{fr}" if fr else "")
                a[0].fields["$uri"] = u
            elif len(a) == 5:
                sc, host, path, fr = _str_or_empty(a[1]), _str_or_empty(a[2]), _str_or_empty(a[3]), _txt(a[4])
                a[0].fields["$uri"] = urlunparse((sc, host, path, "", "", fr or ""))
            elif len(a) >= 8:
                sc, ui, host, port, path, q, fr = (_txt(a[i]) for i in range(1, 8))
                netloc = host or ""
                if port is not None and int(port) >= 0:
                    netloc = f"{netloc}:{int(port)}"
                if ui:
                    netloc = f"{ui}@{netloc}"
                a[0].fields["$uri"] = urlunparse((sc or "", netloc, path or "", "", q or "", fr or ""))
        return a[0] if a else None

    def _juri_parse(a):
        return urlparse(_uri_text(a[0] if a else None))

    def _juri_port(a):
        try:
            p = _juri_parse(a).port
            return int(p) if p is not None else -1
        except Exception:
            return -1

    def _juri_ssp(a):
        p = _juri_parse(a)
        u = _uri_text(a[0] if a else None)
        if p.scheme and u.startswith(p.scheme + ":"):
            rest = u[len(p.scheme) + 1:]
            return rest.split("#", 1)[0]
        return u.split("#", 1)[0]

    def _juri_resolve(a):
        base = _uri_text(a[0] if a else None)
        ref = _uri_text(a[1] if len(a) > 1 else None)
        from urllib.parse import urljoin
        return JObject(JURI, {"$uri": urljoin(base, ref)})

    _add(JURI, "<init>", "(Ljava/lang/String;)V", _juri_init)
    _add(JURI, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", _juri_init)
    _add(JURI, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", _juri_init)
    _add(JURI, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", _juri_init)
    _add(JURI, "create", f"(Ljava/lang/String;){JURI}", lambda a: JObject(JURI, {"$uri": _str_or_empty(a[0] if a else "")}))
    _add(JURI, "getScheme", "()Ljava/lang/String;", lambda a: JString(_juri_parse(a).scheme) if _juri_parse(a).scheme else None)
    _add(JURI, "getAuthority", "()Ljava/lang/String;", lambda a: JString(unquote(_juri_parse(a).netloc)) if _juri_parse(a).netloc else None)
    _add(JURI, "getRawAuthority", "()Ljava/lang/String;", lambda a: JString(_juri_parse(a).netloc) if _juri_parse(a).netloc else None)
    _add(JURI, "getHost", "()Ljava/lang/String;", lambda a: (lambda h: JString(h) if h else None)(_juri_parse(a).hostname))
    _add(JURI, "getPort", "()I", _juri_port)
    _add(JURI, "getPath", "()Ljava/lang/String;", lambda a: JString(unquote(_juri_parse(a).path)))
    _add(JURI, "getRawPath", "()Ljava/lang/String;", lambda a: JString(_juri_parse(a).path))
    _add(JURI, "getQuery", "()Ljava/lang/String;", lambda a: (lambda q: JString(unquote(q)) if q else None)(_juri_parse(a).query))
    _add(JURI, "getRawQuery", "()Ljava/lang/String;", lambda a: (lambda q: JString(q) if q else None)(_juri_parse(a).query))
    _add(JURI, "getFragment", "()Ljava/lang/String;", lambda a: (lambda f: JString(unquote(f)) if f else None)(_juri_parse(a).fragment))
    _add(JURI, "getRawFragment", "()Ljava/lang/String;", lambda a: (lambda f: JString(f) if f else None)(_juri_parse(a).fragment))
    _add(JURI, "getSchemeSpecificPart", "()Ljava/lang/String;", lambda a: JString(unquote(_juri_ssp(a))))
    _add(JURI, "getRawSchemeSpecificPart", "()Ljava/lang/String;", lambda a: JString(_juri_ssp(a)))
    _add(JURI, "getUserInfo", "()Ljava/lang/String;", lambda a: (lambda p: JString(f"{p.username}:{p.password}" if p.password else p.username) if p.username else None)(_juri_parse(a)))
    _add(JURI, "isAbsolute", "()Z", lambda a: 1 if _juri_parse(a).scheme else 0)
    _add(JURI, "isOpaque", "()Z", lambda a: 1 if (_juri_parse(a).scheme and not _juri_ssp(a).startswith("/")) else 0)
    _add(JURI, "normalize", f"(){JURI}", lambda a: JObject(JURI, {"$uri": _uri_text(a[0] if a else None)}))
    _add(JURI, "resolve", f"(Ljava/lang/String;){JURI}", _juri_resolve)
    _add(JURI, "resolve", f"({JURI}){JURI}", _juri_resolve)
    _add(JURI, "toURL", f"(){JURL}", lambda a: JObject(JURL, {"$url": _uri_text(a[0] if a else None), "path": _juri_parse(a).path}))
    _add(JURI, "toString", "()Ljava/lang/String;", lambda a: JString(_uri_text(a[0] if a else None)))
    _add(JURI, "toASCIIString", "()Ljava/lang/String;", lambda a: JString(_uri_text(a[0] if a else None)))
    _add(JURI, "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == JURI and _uri_text(a[0]) == _uri_text(a[1])) else 0)
    _add(JURI, "hashCode", "()I", lambda a: stubs._java_strhash(_uri_text(a[0] if a else None)))

    def _jurl_str(u) -> str:
        if isinstance(u, JObject):
            return str(u.fields.get("$url") or u.fields.get("text") or u.fields.get("path") or "")
        return _str_or_empty(u)

    _add(JURL, "getProtocol", "()Ljava/lang/String;", lambda a: JString(urlparse(_jurl_str(a[0] if a else None)).scheme))
    _add(JURL, "getHost", "()Ljava/lang/String;", lambda a: JString(urlparse(_jurl_str(a[0] if a else None)).hostname or ""))
    _add(JURL, "getPort", "()I", lambda a: int(urlparse(_jurl_str(a[0] if a else None)).port or -1))
    _add(JURL, "getDefaultPort", "()I", lambda a: 443 if urlparse(_jurl_str(a[0] if a else None)).scheme == "https" else (80 if urlparse(_jurl_str(a[0] if a else None)).scheme == "http" else -1))
    _add(JURL, "getQuery", "()Ljava/lang/String;", lambda a: (lambda q: JString(q) if q else None)(urlparse(_jurl_str(a[0] if a else None)).query))
    _add(JURL, "getRef", "()Ljava/lang/String;", lambda a: (lambda f: JString(f) if f else None)(urlparse(_jurl_str(a[0] if a else None)).fragment))
    _add(JURL, "toURI", f"(){JURI}", lambda a: JObject(JURI, {"$uri": _jurl_str(a[0] if a else None)}))
    _add(JURL, "toExternalForm", "()Ljava/lang/String;", lambda a: JString(_jurl_str(a[0] if a else None)))

    _add("Ljava/net/URLEncoder;", "encode", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
         lambda a: JString(quote(_str_or_empty(a[0] if a else ""), safe="").replace("%20", "+")))
    _add("Ljava/net/URLDecoder;", "decode", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;",
         lambda a: JString(unquote(_str_or_empty(a[0] if a else "").replace("+", " "))))

    def _idn_ascii(a):
        s = _str_or_empty(a[0] if a else "")
        try:
            return JString(s.encode("idna").decode("ascii"))
        except Exception:
            return JString(s)

    def _idn_unicode(a):
        s = _str_or_empty(a[0] if a else "")
        try:
            return JString(s.encode("ascii").decode("idna"))
        except Exception:
            return JString(s)

    _add("Ljava/net/IDN;", "toASCII", "(Ljava/lang/String;)Ljava/lang/String;", _idn_ascii)
    _add("Ljava/net/IDN;", "toUnicode", "(Ljava/lang/String;)Ljava/lang/String;", _idn_unicode)


# ---------------------------------------------------------------------------
# 3. java.lang.* & java.lang.reflect.*
# ---------------------------------------------------------------------------

def _install_lang_and_reflect(stubs, _add, R, RB) -> None:
    # Object
    OBJ = "Ljava/lang/Object;"

    def _obj_wait(a):
        o = a[0] if a else None
        ms = int(a[1]) if len(a) > 1 and isinstance(a[1], (int, float)) else 0
        if ms > 0 and hasattr(stubs, "advance_clock_ms"):
            try:
                stubs.advance_clock_ms(min(ms, 1000))
            except Exception:
                pass
        if isinstance(o, JObject):
            cnt = int(o.fields.get("_wait_count", 0)) + 1
            o.fields["_wait_count"] = cnt
            if cnt > 2:
                _raise_exc("Ljava/lang/InterruptedException;", "wait loop interrupted")
        return None

    def _obj_notify(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["_wait_count"] = 0
        return None

    def _obj_tostr(a):
        if not a:
            return JString("")
        v = a[0]
        if isinstance(v, JString):
            return v
        if isinstance(v, JArray):
            return JString(f"{v.eltype}@{id(v):x}")
        if isinstance(v, JObject):
            if "__ename" in v.fields:
                return JString(str(v.fields["__ename"]))
            if "$text" in v.fields:
                return JString(str(v.fields["$text"]))
            return JString(v.cls)
        return JString(str(v))

    def _obj_eq(a):
        if len(a) < 2:
            return 0
        x, y = a[0], a[1]
        if _is_null(x) or _is_null(y):
            return 0
        if x is y:
            return 1
        if isinstance(x, JString) and isinstance(y, JString):
            return 1 if x.text == y.text else 0
        return 0

    _add(OBJ, "wait", "()V", _obj_wait)
    _add(OBJ, "wait", "(J)V", _obj_wait)
    _add(OBJ, "wait", "(JI)V", _obj_wait)
    _add(OBJ, "notify", "()V", _obj_notify)
    _add(OBJ, "notifyAll", "()V", _obj_notify)
    _add(OBJ, "finalize", "()V", lambda a: None)
    _add(OBJ, "toString", "()Ljava/lang/String;", _obj_tostr)
    _add(OBJ, "hashCode", "()I", lambda a: (id(a[0]) & 0x7FFFFFFF) if a and not _is_null(a[0]) else 0)
    _add(OBJ, "equals", "(Ljava/lang/Object;)Z", _obj_eq)

    # Throwable & Exception types
    THR = "Ljava/lang/Throwable;"

    def _thr_init4(a):
        if a and isinstance(a[0], JObject):
            a[0].fields["message"] = _txt(a[1]) if len(a) > 1 else None
            a[0].fields["cause"] = a[2] if len(a) > 2 else None
        return a[0] if a else None

    def _thr_get_msg(a):
        if a and isinstance(a[0], JObject):
            f = a[0].fields
            for k in ("message", "detailMessage", "msg", "$message"):
                v = f.get(k)
                if v is not None:
                    return v if isinstance(v, JString) else JString(str(v))
        return JString("")

    def _thr_tostr(a):
        if not a or not isinstance(a[0], JObject):
            return JString("")
        dotted = a[0].cls[1:-1].replace("/", ".") if a[0].cls.startswith("L") and a[0].cls.endswith(";") else a[0].cls
        msg = _thr_get_msg(a).text
        return JString(f"{dotted}: {msg}" if msg else dotted)

    def _thr_add_supp(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JObject):
            a[0].fields.setdefault("_suppressed", []).append(a[1])
        return None

    def _thr_print_st(a):
        if a and isinstance(a[0], JObject):
            msg = _thr_tostr(a).text
            if len(a) > 1 and isinstance(a[1], JObject):
                stubs.resolve(a[1].cls, "println", "(Ljava/lang/String;)V", [a[1], JString(msg)], False)
            else:
                stubs.__dict__.setdefault("_stdout_lines", []).append(msg)
        return None

    _add(THR, "<init>", "(Ljava/lang/String;Ljava/lang/Throwable;ZZ)V", _thr_init4)
    _add(THR, "initCause", f"({THR}){THR}", lambda a: (a[0].fields.__setitem__("cause", a[1] if len(a) > 1 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(THR, "fillInStackTrace", f"(){THR}", lambda a: (a[0].fields.__setitem__("_stack", stubs._make_stack_trace()), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(THR, "setStackTrace", "([Ljava/lang/StackTraceElement;)V", lambda a: a[0].fields.__setitem__("_stack", a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(THR, "getStackTrace", "()[Ljava/lang/StackTraceElement;", lambda a: a[0].fields.get("_stack") if a and isinstance(a[0], JObject) and a[0].fields.get("_stack") is not None else stubs._make_stack_trace())
    _add(THR, "addSuppressed", f"({THR})V", _thr_add_supp)
    _add(THR, "getSuppressed", f"()[{THR}", lambda a: JArray(f"[{THR}", list(a[0].fields.get("_suppressed", []))) if a and isinstance(a[0], JObject) else JArray(f"[{THR}", []))
    _add(THR, "printStackTrace", "()V", _thr_print_st)
    _add(THR, "printStackTrace", "(Ljava/io/PrintWriter;)V", _thr_print_st)
    _add(THR, "printStackTrace", "(Ljava/io/PrintStream;)V", _thr_print_st)
    _add(THR, "toString", "()Ljava/lang/String;", _thr_tostr)
    R(THR, "getMessage", "()Ljava/lang/String;", _thr_get_msg)
    R(THR, "getLocalizedMessage", "()Ljava/lang/String;", _thr_get_msg)
    RB("getMessage", "()Ljava/lang/String;", _thr_get_msg)
    RB("getLocalizedMessage", "()Ljava/lang/String;", _thr_get_msg)

    _add("Ljava/lang/AssertionError;", "<init>", "()V", lambda a: None)
    _add("Ljava/lang/AssertionError;", "<init>", "(Ljava/lang/Object;)V", lambda a: a[0].fields.__setitem__("message", stubs._obj_to_str(a[1] if len(a) > 1 else None)) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/lang/AssertionError;", "<init>", "(Ljava/lang/String;Ljava/lang/Throwable;)V", stubs._throwable_init_msg_cause)
    for _ap in ("Z", "C", "I", "J", "F", "D"):
        _add("Ljava/lang/AssertionError;", "<init>", f"({_ap})V", lambda a, p=_ap: a[0].fields.__setitem__("message", str(a[1] if len(a) > 1 else "")) if a and isinstance(a[0], JObject) else None)

    _add("Ljava/lang/reflect/InvocationTargetException;", "<init>", f"({THR})V", lambda a: a[0].fields.update({"target": a[1] if len(a) > 1 else None, "cause": a[1] if len(a) > 1 else None}) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/lang/reflect/InvocationTargetException;", "<init>", f"({THR}Ljava/lang/String;)V", lambda a: a[0].fields.update({"target": a[1] if len(a) > 1 else None, "cause": a[1] if len(a) > 1 else None, "message": _txt(a[2]) if len(a) > 2 else ""}) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/lang/reflect/InvocationTargetException;", "getTargetException", f"(){THR}", lambda a: (a[0].fields.get("target") or a[0].fields.get("cause")) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/concurrent/ExecutionException;", "<init>", f"({THR})V", lambda a: a[0].fields.__setitem__("cause", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/concurrent/ExecutionException;", "<init>", f"(Ljava/lang/String;{THR})V", stubs._throwable_init_msg_cause)
    _add("Ljava/text/ParseException;", "<init>", "(Ljava/lang/String;I)V", lambda a: a[0].fields.update({"message": _txt(a[1]) if len(a) > 1 else "", "errorOffset": int(a[2]) if len(a) > 2 else 0}) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/text/ParseException;", "getErrorOffset", "()I", lambda a: int(a[0].fields.get("errorOffset", 0)) if a and isinstance(a[0], JObject) else 0)

    STE = "Ljava/lang/StackTraceElement;"
    _add(STE, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;I)V",
         lambda a: a[0].fields.update({
             "cls": _str_or_empty(a[1] if len(a) > 1 else ""),
             "method": _str_or_empty(a[2] if len(a) > 2 else ""),
             "file": _txt(a[3]) if len(a) > 3 else None,
             "line": int(a[4]) if len(a) > 4 else -1,
         }) if a and isinstance(a[0], JObject) else None)
    _add(STE, "toString", "()Ljava/lang/String;", lambda a: JString(f"{a[0].fields.get('cls', '')}.{a[0].fields.get('method', '')}({a[0].fields.get('file') or 'Unknown Source'}:{a[0].fields.get('line', -1)})") if a and isinstance(a[0], JObject) else JString(""))

    # Character
    CH = "Ljava/lang/Character;"
    _BIDI_MAP = {
        "L": 0, "R": 1, "AL": 2, "EN": 3, "ES": 4, "ET": 5, "AN": 6,
        "CS": 7, "B": 8, "S": 9, "WS": 10, "ON": 11, "LRE": 14, "LRO": 15,
        "RLE": 16, "RLO": 17, "PDF": 18, "NSM": 12, "BN": 13,
    }

    def _cp_from_cs(cs, idx: int) -> int:
        s = _str_or_empty(cs)
        if 0 <= idx < len(s):
            c1 = ord(s[idx])
            if 0xD800 <= c1 <= 0xDBFF and idx + 1 < len(s):
                c2 = ord(s[idx + 1])
                if 0xDC00 <= c2 <= 0xDFFF:
                    return 0x10000 + ((c1 - 0xD800) << 10) + (c2 - 0xDC00)
            return c1
        return 0

    def _cp_before_cs(cs, idx: int) -> int:
        s = _str_or_empty(cs)
        if 1 <= idx <= len(s):
            c2 = ord(s[idx - 1])
            if 0xDC00 <= c2 <= 0xDFFF and idx >= 2:
                c1 = ord(s[idx - 2])
                if 0xD800 <= c1 <= 0xDBFF:
                    return 0x10000 + ((c1 - 0xD800) << 10) + (c2 - 0xDC00)
            return c2
        return 0

    def _ch_to_chars_into(a) -> int:
        cp = int(a[0]) if a else 0
        dst = a[1] if len(a) > 1 else None
        idx = int(a[2]) if len(a) > 2 else 0
        if not isinstance(dst, JArray) or idx < 0:
            return 0
        if cp < 0x10000:
            if idx < len(dst.data):
                dst.data[idx] = cp & 0xFFFF
            return 1
        u = cp - 0x10000
        hi = 0xD800 + (u >> 10)
        lo = 0xDC00 + (u & 0x3FF)
        if idx < len(dst.data):
            dst.data[idx] = hi
        if idx + 1 < len(dst.data):
            dst.data[idx + 1] = lo
        return 2

    def _safe_chr(cp: int) -> str:
        c = int(cp) & 0x1FFFFF
        return chr(c) if 0 <= c <= 0x10FFFF and not (0xD800 <= c <= 0xDFFF) else "\x00"

    _add(CH, "charCount", "(I)I", lambda a: 2 if (a and int(a[0]) >= 0x10000) else 1)
    _add(CH, "codePointAt", "(Ljava/lang/CharSequence;I)I", lambda a: _cp_from_cs(a[0], int(a[1])) if len(a) > 1 else 0)
    _add(CH, "codePointBefore", "(Ljava/lang/CharSequence;I)I", lambda a: _cp_before_cs(a[0], int(a[1])) if len(a) > 1 else 0)
    _add(CH, "getDirectionality", "(C)B", lambda a: _BIDI_MAP.get(unicodedata.bidirectional(_safe_chr(a[0] if a else 0)), -1))
    _add(CH, "getDirectionality", "(I)B", lambda a: _BIDI_MAP.get(unicodedata.bidirectional(_safe_chr(a[0] if a else 0)), -1))
    _add(CH, "isHighSurrogate", "(C)Z", lambda a: 1 if (a and 0xD800 <= (int(a[0]) & 0xFFFF) <= 0xDBFF) else 0)
    _add(CH, "isLowSurrogate", "(C)Z", lambda a: 1 if (a and 0xDC00 <= (int(a[0]) & 0xFFFF) <= 0xDFFF) else 0)
    _add(CH, "isSurrogate", "(C)Z", lambda a: 1 if (a and 0xD800 <= (int(a[0]) & 0xFFFF) <= 0xDFFF) else 0)
    _add(CH, "isSurrogatePair", "(CC)Z", lambda a: 1 if (len(a) > 1 and 0xD800 <= (int(a[0]) & 0xFFFF) <= 0xDBFF and 0xDC00 <= (int(a[1]) & 0xFFFF) <= 0xDFFF) else 0)
    _add(CH, "toCodePoint", "(CC)I", lambda a: (0x10000 + (((int(a[0]) & 0xFFFF) - 0xD800) << 10) + ((int(a[1]) & 0xFFFF) - 0xDC00)) if len(a) > 1 else 0)
    _add(CH, "isUpperCase", "(C)Z", lambda a: 1 if (a and _safe_chr(int(a[0]) & 0xFFFF).isupper()) else 0)
    _add(CH, "isUpperCase", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).isupper()) else 0)
    _add(CH, "isLowerCase", "(C)Z", lambda a: 1 if (a and _safe_chr(int(a[0]) & 0xFFFF).islower()) else 0)
    _add(CH, "isLowerCase", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).islower()) else 0)
    _add(CH, "isDigit", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).isdigit()) else 0)
    _add(CH, "isLetter", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).isalpha()) else 0)
    _add(CH, "isLetterOrDigit", "(C)Z", lambda a: 1 if (a and _safe_chr(int(a[0]) & 0xFFFF).isalnum()) else 0)
    _add(CH, "isLetterOrDigit", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).isalnum()) else 0)
    _add(CH, "isWhitespace", "(I)Z", lambda a: 1 if (a and _safe_chr(int(a[0])).isspace()) else 0)
    _add(CH, "isSpaceChar", "(C)Z", lambda a: 1 if (a and unicodedata.category(_safe_chr(int(a[0]) & 0xFFFF)).startswith("Z")) else 0)
    _add(CH, "isSpaceChar", "(I)Z", lambda a: 1 if (a and unicodedata.category(_safe_chr(int(a[0]))).startswith("Z")) else 0)
    _add(CH, "digit", "(II)I", stubs._char_digit)
    _add(CH, "toChars", "(I[CI)I", _ch_to_chars_into)
    _add(CH, "toLowerCase", "(I)I", lambda a: ord(_safe_chr(int(a[0])).lower()[:1] or "\x00") if a else 0)
    _add(CH, "toUpperCase", "(I)I", lambda a: ord(_safe_chr(int(a[0])).upper()[:1] or "\x00") if a else 0)
    _add(CH, "toString", "()Ljava/lang/String;", lambda a: JString(chr(int(a[0].fields.get("value", 0)) & 0xFFFF)) if a and isinstance(a[0], JObject) else JString(""))

    # Number & Wrappers & String & StringBuilder & CharSequence & Appendable
    def _num_val(a, kind: str):
        v = a[0] if a else 0
        if isinstance(v, JObject):
            if "_bi" in v.fields:
                v = v.fields["_bi"]
            elif "_dec" in v.fields:
                v = v.fields["_dec"]
            else:
                v = v.fields.get("value", 0)
        try:
            if kind == "I":
                return _s32(int(v or 0))
            if kind == "J":
                return _s64(int(v or 0))
            if kind == "S":
                n = int(v or 0) & 0xFFFF
                return n - 0x10000 if (n & 0x8000) else n
            if kind == "B":
                return _s8(int(v or 0))
            return float(v or 0.0)
        except Exception:
            return 0.0 if kind in ("F", "D") else 0

    for _nc in ("Ljava/lang/Number;", "Ljava/lang/Integer;", "Ljava/lang/Long;", "Ljava/lang/Short;", "Ljava/lang/Byte;", "Ljava/lang/Float;", "Ljava/lang/Double;"):
        _add(_nc, "intValue", "()I", lambda a: _num_val(a, "I"))
        _add(_nc, "longValue", "()J", lambda a: _num_val(a, "J"))
        _add(_nc, "floatValue", "()F", lambda a: _num_val(a, "F"))
        _add(_nc, "doubleValue", "()D", lambda a: _num_val(a, "D"))
        _add(_nc, "byteValue", "()B", lambda a: _num_val(a, "B"))
        _add(_nc, "shortValue", "()S", lambda a: _num_val(a, "S"))

    def _sys_props_dict() -> dict:
        d = getattr(stubs, "_sys_props", None)
        if not isinstance(d, dict):
            d = {
                "java.io.tmpdir": "/data/local/tmp",
                "file.separator": "/",
                "path.separator": ":",
                "line.separator": "\n",
                "user.dir": "/",
                "user.home": "/data/local/tmp",
                "os.name": "Linux",
                "http.agent": "Dalvik/2.1.0 (Linux; U; Android 13)",
            }
            stubs._sys_props = d
        return d

    def _sys_get_prop_val(k):
        if k is None:
            return None
        po = getattr(stubs, "_sys_props_obj", None)
        if isinstance(po, JObject):
            m = po.fields.get("map", {})
            if k in m:
                return _str_or_empty(m[k])
        d = _sys_props_dict()
        if k in d:
            return d[k]
        return None

    def _sys_prop_get(a):
        k = _txt(a[0]) if a else None
        val = _sys_get_prop_val(k)
        if val is not None:
            return JString(str(val))
        return a[1] if len(a) > 1 else None

    def _sys_prop_set(a):
        k = _txt(a[0]) if a else None
        v = _txt(a[1]) if len(a) > 1 else None
        if k is None:
            return None
        d = _sys_props_dict()
        prev = _sys_get_prop_val(k)
        if v is None:
            d.pop(k, None)
            po = getattr(stubs, "_sys_props_obj", None)
            if isinstance(po, JObject):
                po.fields.get("map", {}).pop(k, None)
        else:
            d[k] = str(v)
            po = getattr(stubs, "_sys_props_obj", None)
            if isinstance(po, JObject):
                po.fields.setdefault("map", {})[k] = JString(str(v))
        return JString(str(prev)) if prev is not None else None

    def _sys_get_properties(a):
        d = _sys_props_dict()
        po = getattr(stubs, "_sys_props_obj", None)
        if not isinstance(po, JObject):
            po = JObject("Ljava/util/Properties;", {"map": {k: JString(str(v)) for k, v in d.items()}})
            stubs._sys_props_obj = po
        else:
            m = po.fields.setdefault("map", {})
            for k, v in d.items():
                m.setdefault(k, JString(str(v)))
        return po

    R("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;)Ljava/lang/String;", _sys_prop_get)
    R("Ljava/lang/System;", "getProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", _sys_prop_get)
    _add("Ljava/lang/System;", "setProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", _sys_prop_set)
    _add("Ljava/lang/System;", "getProperties", "()Ljava/util/Properties;", _sys_get_properties)

    def _parse_int_prop(k: str, default=None):
        raw = _sys_get_prop_val(k)
        if raw is not None:
            try:
                return int(str(raw).strip(), 0)
            except Exception:
                pass
        return default

    _add("Ljava/lang/Boolean;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Boolean;",
         lambda a: JObject("Ljava/lang/Boolean;", {"value": 1 if (a and (_txt(a[0]) or "").strip().lower() == "true") else 0}))
    _add("Ljava/lang/Boolean;", "getBoolean", "(Ljava/lang/String;)Z",
         lambda a: 1 if (a and str(_sys_get_prop_val(_txt(a[0])) or "").strip().lower() == "true") else 0)
    _add("Ljava/lang/Double;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Double;",
         lambda a: JObject("Ljava/lang/Double;", {"value": stubs._parse_float(a)}))
    _add("Ljava/lang/Double;", "compare", "(DD)I", lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_as_f64(a[0]), _as_f64(a[1])) if len(a) > 1 else 0)
    _add("Ljava/lang/Float;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Float;",
         lambda a: JObject("Ljava/lang/Float;", {"value": stubs._parse_float(a)}))
    _add("Ljava/lang/Float;", "compare", "(FF)I", lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_as_f32(a[0]), _as_f32(a[1])) if len(a) > 1 else 0)

    def _to_base_str(val: int, base: int, bits: int) -> str:
        if base == 16:
            return format(val & ((1 << bits) - 1), "x")
        if base == 2:
            return format(val & ((1 << bits) - 1), "b")
        if base == 8:
            return format(val & ((1 << bits) - 1), "o")
        radix = max(2, min(36, int(base)))
        signed = _s32(val) if bits == 32 else _s64(val)
        if signed == 0:
            return "0"
        neg = signed < 0
        n = -signed if neg else signed
        digs = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = []
        while n:
            out.append(digs[n % radix])
            n //= radix
        return ("-" if neg else "") + "".join(reversed(out))

    def _get_integer_fn(a, fallback_is_obj: bool = False):
        k = _txt(a[0]) if a else None
        pv = _parse_int_prop(k) if k else None
        if pv is not None:
            return JObject("Ljava/lang/Integer;", {"value": _s32(pv)})
        if len(a) > 1:
            if fallback_is_obj:
                return a[1] if isinstance(a[1], JObject) else None
            return JObject("Ljava/lang/Integer;", {"value": _s32(int(a[1]))})
        return None

    def _get_long_fn(a, fallback_is_obj: bool = False):
        k = _txt(a[0]) if a else None
        pv = _parse_int_prop(k) if k else None
        if pv is not None:
            return JObject("Ljava/lang/Long;", {"value": _s64(pv)})
        if len(a) > 1:
            if fallback_is_obj:
                return a[1] if isinstance(a[1], JObject) else None
            return JObject("Ljava/lang/Long;", {"value": _s64(int(a[1]))})
        return None

    _add("Ljava/lang/Integer;", "toBinaryString", "(I)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, 2, 32)))
    _add("Ljava/lang/Integer;", "toHexString", "(I)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, 16, 32)))
    _add("Ljava/lang/Integer;", "toOctalString", "(I)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, 8, 32)))
    _add("Ljava/lang/Integer;", "toString", "(II)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, int(a[1]) if len(a) > 1 else 10, 32)))
    _add("Ljava/lang/Integer;", "getInteger", "(Ljava/lang/String;)Ljava/lang/Integer;", lambda a: _get_integer_fn(a, False))
    _add("Ljava/lang/Integer;", "getInteger", "(Ljava/lang/String;I)Ljava/lang/Integer;", lambda a: _get_integer_fn(a, False))
    _add("Ljava/lang/Integer;", "getInteger", "(Ljava/lang/String;Ljava/lang/Integer;)Ljava/lang/Integer;", lambda a: _get_integer_fn(a, True))
    _add("Ljava/lang/Long;", "parseLong", "(Ljava/lang/String;)J",
         lambda a: stubs._parse_java_int(a[0] if a else None, 10, 64))
    _add("Ljava/lang/Long;", "valueOf", "(Ljava/lang/String;)Ljava/lang/Long;",
         lambda a: JObject("Ljava/lang/Long;", {"value": stubs._parse_java_int(a[0] if a else None, 10, 64)}))
    _add("Ljava/lang/Long;", "toHexString", "(J)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, 16, 64)))
    _add("Ljava/lang/Long;", "toBinaryString", "(J)Ljava/lang/String;", lambda a: JString(_to_base_str(int(a[0]) if a else 0, 2, 64)))
    R("Ljava/lang/Long;", "getLong", "(Ljava/lang/String;)Ljava/lang/Long;", lambda a: _get_long_fn(a, False))
    R("Ljava/lang/Long;", "getLong", "(Ljava/lang/String;J)Ljava/lang/Long;", lambda a: _get_long_fn(a, False))
    R("Ljava/lang/Long;", "getLong", "(Ljava/lang/String;Ljava/lang/Long;)Ljava/lang/Long;", lambda a: _get_long_fn(a, True))

    def _str_getchars(a):
        if len(a) >= 5 and isinstance(a[3], JArray):
            s = _str_or_empty(a[0])
            sb = max(0, int(a[1]))
            se = min(len(s), int(a[2]))
            db = max(0, int(a[4]))
            for i in range(max(0, se - sb)):
                if db + i < len(a[3].data):
                    a[3].data[db + i] = ord(s[sb + i])
        return None

    _add("Ljava/lang/String;", "getChars", "(II[CI)V", _str_getchars)
    _add("Ljava/lang/CharSequence;", "length", "()I", lambda a: len(_str_or_empty(a[0] if a else "")))
    _add("Ljava/lang/CharSequence;", "charAt", "(I)C", lambda a: (lambda t: ord(t[int(a[1])]) if 0 <= int(a[1]) < len(t) else 0)(_str_or_empty(a[0] if a else "")))
    _add("Ljava/lang/CharSequence;", "toString", "()Ljava/lang/String;", lambda a: JString(_str_or_empty(a[0] if a else "")))
    _add("Ljava/lang/CharSequence;", "chars", "()Ljava/util/stream/IntStream;", lambda a: JObject("Ljava/util/stream/IntStream;", {"__items": [ord(c) for c in _str_or_empty(a[0] if a else "")]}))
    _add("Ljava/lang/CharSequence;", "codePoints", "()Ljava/util/stream/IntStream;", lambda a: JObject("Ljava/util/stream/IntStream;", {"__items": [ord(c) for c in _str_or_empty(a[0] if a else "")]}))

    for _sbc in ("Ljava/lang/StringBuilder;", "Ljava/lang/StringBuffer;"):
        _add(_sbc, "append", f"([CII){_sbc}", lambda a: stubs._sb_push(a[0], stubs._chars_to_text(a[1], int(a[2]), int(a[3]))) if len(a) > 3 else (a[0] if a else None))
        _add(_sbc, "delete", f"(II){_sbc}", lambda a: stubs._sb_set(a[0], (lambda b, s, e: b[:max(0, s)] + b[max(0, e):])(a[0].fields.get("buf", "") or "", int(a[1]), int(a[2]))) if len(a) > 2 and isinstance(a[0], JObject) else (a[0] if a else None))
        _add(_sbc, "replace", f"(IILjava/lang/String;){_sbc}", lambda a: stubs._sb_set(a[0], (lambda b, s, e, r: b[:max(0, s)] + r + b[max(0, e):])(a[0].fields.get("buf", "") or "", int(a[1]), int(a[2]), _str_or_empty(a[3]))) if len(a) > 3 and isinstance(a[0], JObject) else (a[0] if a else None))
        _add(_sbc, "setCharAt", "(IC)V", lambda a: (lambda b, i, ch: stubs._sb_set(a[0], b[:i] + ch + b[i + 1:]) if 0 <= i < len(b) else None)(a[0].fields.get("buf", "") or "", int(a[1]), chr(int(a[2]) & 0xFFFF)) if len(a) > 2 and isinstance(a[0], JObject) else None)
        _add(_sbc, "substring", "(I)Ljava/lang/String;", lambda a: JString((a[0].fields.get("buf", "") or "")[max(0, int(a[1])):]) if len(a) > 1 and isinstance(a[0], JObject) else JString(""))
        _add(_sbc, "substring", "(II)Ljava/lang/String;", lambda a: JString((a[0].fields.get("buf", "") or "")[max(0, int(a[1])):max(0, int(a[2]))]) if len(a) > 2 and isinstance(a[0], JObject) else JString(""))
        _add(_sbc, "indexOf", "(Ljava/lang/String;)I", lambda a: (a[0].fields.get("buf", "") or "").find(_str_or_empty(a[1])) if len(a) > 1 and isinstance(a[0], JObject) else -1)
        _add(_sbc, "indexOf", "(Ljava/lang/String;I)I", lambda a: (a[0].fields.get("buf", "") or "").find(_str_or_empty(a[1]), int(a[2])) if len(a) > 2 and isinstance(a[0], JObject) else -1)
        _add(_sbc, "ensureCapacity", "(I)V", lambda a: None)

    _add("Ljava/lang/Appendable;", "append", "(C)Ljava/lang/Appendable;", lambda a: (_invoke_vm_or_stub(stubs, a[0], "Ljava/lang/Object;", "append", "(C)Ljava/lang/StringBuilder;", [a[1] if len(a) > 1 else 0]), a[0])[1] if a else None)
    _add("Ljava/lang/Appendable;", "append", "(Ljava/lang/CharSequence;)Ljava/lang/Appendable;", lambda a: (_invoke_vm_or_stub(stubs, a[0], "Ljava/lang/Object;", "append", "(Ljava/lang/CharSequence;)Ljava/lang/StringBuilder;", [a[1] if len(a) > 1 else None]), a[0])[1] if a else None)

    # Thread & Thread$State & Thread$UncaughtExceptionHandler
    TH = "Ljava/lang/Thread;"
    THS = "Ljava/lang/Thread$State;"
    THU = "Ljava/lang/Thread$UncaughtExceptionHandler;"

    def _th_state(a):
        o = a[0] if a and isinstance(a[0], JObject) else None
        st_name = "RUNNABLE"
        if o is not None:
            if o.fields.get("_terminated"):
                st_name = "TERMINATED"
            elif o is not getattr(stubs, "_looper_thread", None) and not o.fields.get("_started"):
                st_name = "NEW"
        return JObject(THS, {"__ename": st_name, "__ordinal": {"NEW": 0, "RUNNABLE": 1, "BLOCKED": 2, "WAITING": 3, "TIMED_WAITING": 4, "TERMINATED": 5}.get(st_name, 1)})

    def _th_interrupted(_a=None):
        cur = getattr(stubs, "_looper_thread", None)
        if isinstance(cur, JObject):
            was = 1 if cur.fields.get("_interrupted") else 0
            cur.fields["_interrupted"] = 0
            return was
        return 0

    def _th_run(a):
        if a and isinstance(a[0], JObject):
            tgt = a[0].fields.get("_target")
            if isinstance(tgt, JObject):
                _invoke_vm_or_stub(stubs, tgt, "Ljava/lang/Runnable;", "run", "()V", [])
        return None

    _add(TH, "interrupt", "()V", lambda a: a[0].fields.__setitem__("_interrupted", 1) if a and isinstance(a[0], JObject) else None)
    R(TH, "isInterrupted", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_interrupted")) else 0)
    _add(TH, "interrupted", "()Z", _th_interrupted)
    _add(TH, "yield", "()V", lambda a: None)
    _add(TH, "getState", f"(){THS}", _th_state)
    R(TH, "setName", "(Ljava/lang/String;)V", lambda a: a[0].fields.__setitem__("name", _str_or_empty(a[1] if len(a) > 1 else "")) if a and isinstance(a[0], JObject) else None)
    _add(TH, "isAlive", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and (a[0] is getattr(stubs, "_looper_thread", None) or (a[0].fields.get("_started") and not a[0].fields.get("_terminated")))) else 0)
    _add(TH, "isDaemon", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_daemon")) else 0)
    _add(TH, "getPriority", "()I", lambda a: int(a[0].fields.get("_priority", 5)) if a and isinstance(a[0], JObject) else 5)
    _add(TH, "activeCount", "()I", lambda a: 1)
    _add(TH, "holdsLock", "(Ljava/lang/Object;)Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("_lock_depth", 0)) > 0) else 0)
    _add(TH, "join", "(JI)V", lambda a: None)
    _add(TH, "run", "()V", _th_run)
    _add(TH, "getContextClassLoader", "()Ljava/lang/ClassLoader;", lambda a: (a[0].fields.get("_cl") if a and isinstance(a[0], JObject) and a[0].fields.get("_cl") is not None else JObject("Ldalvik/system/PathClassLoader;", {"pathList": stubs._make_dex_path_list()})))
    _add(TH, "setContextClassLoader", "(Ljava/lang/ClassLoader;)V", lambda a: a[0].fields.__setitem__("_cl", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(TH, "getUncaughtExceptionHandler", f"(){THU}", lambda a: a[0].fields.get("_ueh") or JObject(THU, {}) if a and isinstance(a[0], JObject) else JObject(THU, {}))
    _add(TH, "setUncaughtExceptionHandler", f"({THU})V", lambda a: a[0].fields.__setitem__("_ueh", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(TH, "getDefaultUncaughtExceptionHandler", f"(){THU}", lambda a: stubs.__dict__.get("_default_ueh") or JObject(THU, {}))
    _add(TH, "setDefaultUncaughtExceptionHandler", f"({THU})V", lambda a: stubs.__dict__.__setitem__("_default_ueh", a[0] if a else None))
    _add(THU, "uncaughtException", f"({TH}{THR})V", lambda a: None)

    # Class & Reflection
    CLS = "Ljava/lang/Class;"
    FLD = "Ljava/lang/reflect/Field;"
    MTH = "Ljava/lang/reflect/Method;"
    CTR = "Ljava/lang/reflect/Constructor;"

    def _cls_is_instance(a):
        if len(a) < 2 or not isinstance(a[0], JObject) or _is_null(a[1]):
            return 0
        desc = _desc_of_class(stubs, a[0])
        if not desc:
            return 0
        vm = getattr(stubs, "vm", None)
        if vm is not None and hasattr(vm, "_instance_of"):
            return 1 if vm._instance_of(a[1], desc) else 0
        if isinstance(a[1], JString):
            return 1 if desc in ("Ljava/lang/String;", "Ljava/lang/CharSequence;", "Ljava/lang/Object;") else 0
        if isinstance(a[1], JObject):
            return 1 if (desc == "Ljava/lang/Object;" or a[1].cls == desc) else 0
        return 0

    def _cls_cast(a):
        if len(a) < 2 or _is_null(a[1]):
            return None
        if not _cls_is_instance(a):
            _raise_exc("Ljava/lang/ClassCastException;", f"Cannot cast to {_desc_of_class(stubs, a[0])}")
        return a[1]

    def _cls_as_subclass(a):
        if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            _raise_exc("Ljava/lang/ClassCastException;", "asSubclass")
        d0 = _desc_of_class(stubs, a[0])
        d1 = _desc_of_class(stubs, a[1])
        vm = getattr(stubs, "vm", None)
        ok = (d0 == d1 or d1 == "Ljava/lang/Object;" or (vm is not None and d0 and d1 and vm._is_subclass(d0, d1)))
        if not ok:
            _raise_exc("Ljava/lang/ClassCastException;", f"{d0} is not a subclass of {d1}")
        return a[0]

    def _cls_get_fields(a):
        desc = _desc_of_class(stubs, a[0]) if a and isinstance(a[0], JObject) else None
        out = []
        vm = getattr(stubs, "vm", None)
        if desc and vm is not None:
            prov = getattr(vm, "class_provider", None)
            if prov is not None and hasattr(prov, "get_fields"):
                try:
                    for fn, ftype in (prov.get_fields(desc) or {}).items():
                        out.append(JObject(FLD, {"owner": a[0], "name": fn, "type_desc": ftype}))
                except Exception:
                    pass
            if not out and desc in vm.statics:
                for fn in vm.statics.get(desc, {}):
                    out.append(JObject(FLD, {"owner": a[0], "name": fn}))
        return JArray(f"[{FLD}", out)

    def _cls_get_ctors(a):
        desc = _desc_of_class(stubs, a[0]) if a and isinstance(a[0], JObject) else None
        out = []
        vm = getattr(stubs, "vm", None)
        if desc and vm is not None:
            stubs._lazy_load(desc)
            for (mc, mn, ms), _md in vm.registry.items():
                if mc == desc and mn == "<init>":
                    ptypes, _ = stubs._split_sig(ms)
                    out.append(JObject(CTR, {"owner": a[0], "nparams": len(ptypes), "ptypes": ptypes}))
        return JArray(f"[{CTR}", out)

    def _cls_is_enum(a):
        desc = _desc_of_class(stubs, a[0]) if a and isinstance(a[0], JObject) else None
        vm = getattr(stubs, "vm", None)
        if not desc or vm is None:
            return 0
        try:
            if vm._is_subclass(desc, "Ljava/lang/Enum;"):
                return 1
        except Exception:
            pass
        st = vm.statics.get(desc, {})
        return 1 if any(isinstance(v, JObject) and "__ename" in v.fields for v in st.values()) else 0

    def _cls_enum_consts(a):
        if not _cls_is_enum(a):
            return None
        desc = _desc_of_class(stubs, a[0])
        vm = getattr(stubs, "vm", None)
        if vm is None or not desc:
            return JArray("[Ljava/lang/Object;", [])
        stubs._lazy_load(desc)
        try:
            vm._ensure_class_init(desc)
        except Exception:
            pass
        st = vm.statics.get(desc, {})
        vals = st.get("$VALUES")
        if isinstance(vals, JArray):
            return JArray("[Ljava/lang/Object;", list(vals.data))
        consts = [v for v in st.values() if isinstance(v, JObject) and "__ename" in v.fields]
        consts.sort(key=lambda o: int(o.fields.get("__ordinal", 0)))
        return JArray("[Ljava/lang/Object;", consts)

    def _cls_tostr(a):
        if not a or not isinstance(a[0], JObject):
            return JString("")
        nm = str(a[0].fields.get("name", ""))
        if a[0].fields.get("primitive") or nm in ("boolean", "byte", "char", "short", "int", "long", "float", "double", "void"):
            return JString(nm)
        return JString("class " + stubs._to_dotted_name(nm))

    R(CLS, "isInstance", "(Ljava/lang/Object;)Z", _cls_is_instance)
    _add(CLS, "cast", "(Ljava/lang/Object;)Ljava/lang/Object;", _cls_cast)
    _add(CLS, "asSubclass", f"({CLS}){CLS}", _cls_as_subclass)
    _add(CLS, "getDeclaredFields", f"()[{FLD}", _cls_get_fields)
    _add(CLS, "getFields", f"()[{FLD}", _cls_get_fields)
    _add(CLS, "getDeclaredConstructors", f"()[{CTR}", _cls_get_ctors)
    _add(CLS, "getConstructors", f"()[{CTR}", _cls_get_ctors)
    _add(CLS, "isEnum", "()Z", _cls_is_enum)
    _add(CLS, "getEnumConstants", "()[Ljava/lang/Object;", _cls_enum_consts)
    _add(CLS, "getEnclosingMethod", f"(){MTH}", lambda a: None)
    _add(CLS, "getEnclosingConstructor", f"(){CTR}", lambda a: None)
    _add(CLS, "getAnnotations", "()[Ljava/lang/annotation/Annotation;", lambda a: JArray("[Ljava/lang/annotation/Annotation;", []))
    _add(CLS, "getDeclaredAnnotations", "()[Ljava/lang/annotation/Annotation;", lambda a: JArray("[Ljava/lang/annotation/Annotation;", []))
    _add(CLS, "getTypeParameters", "()[Ljava/lang/reflect/TypeVariable;", lambda a: JArray("[Ljava/lang/reflect/TypeVariable;", []))
    _add(CLS, "desiredAssertionStatus", "()Z", lambda a: 0)
    _add(CLS, "isSynthetic", "()Z", lambda a: 0)
    _add(CLS, "isAnnotation", "()Z", lambda a: 0)
    _add(CLS, "getTypeName", "()Ljava/lang/String;", stubs._cls_getname)
    _add(CLS, "getGenericSuperclass", "()Ljava/lang/reflect/Type;", stubs._cls_getsuperclass)
    _add(CLS, "getGenericInterfaces", "()[Ljava/lang/reflect/Type;", stubs._cls_getinterfaces)
    _add(CLS, "toString", "()Ljava/lang/String;", _cls_tostr)

    _add("Ljava/lang/ClassLoader;", "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;",
         lambda a: _intern_class(stubs, _str_or_empty(a[1] if len(a) > 1 else "java.lang.Object")))
    _add("Ljava/lang/ClassLoader;", "loadClass", "(Ljava/lang/String;Z)Ljava/lang/Class;",
         lambda a: _intern_class(stubs, _str_or_empty(a[1] if len(a) > 1 else "java.lang.Object")))
    _add("Ljava/lang/ClassLoader;", "getResources", "(Ljava/lang/String;)Ljava/util/Enumeration;",
         lambda a: (lambda u: JObject("Ljava/util/Enumeration;", {"__items": [u] if u is not None else [], "__idx": 0}))(stubs._cls_getresource(a)))

    def _ptypes_arr(obj):
        pts = (obj.fields.get("ptypes") if isinstance(obj, JObject) else None) or []
        return JArray(f"[{CLS}", [_intern_class(stubs, p) for p in pts])

    def _field_type(a):
        o = a[0] if a and isinstance(a[0], JObject) else None
        td = o.fields.get("type_desc") if o else None
        if td:
            return _intern_class(stubs, td)
        return _intern_class(stubs, "Ljava/lang/Object;")

    _add(FLD, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("name", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(FLD, "getType", f"(){CLS}", _field_type)
    _add(FLD, "getGenericType", "()Ljava/lang/reflect/Type;", _field_type)
    _add(FLD, "getDeclaringClass", f"(){CLS}", lambda a: a[0].fields.get("owner") or _intern_class(stubs, "Ljava/lang/Object;") if a and isinstance(a[0], JObject) else _intern_class(stubs, "Ljava/lang/Object;"))
    _add(FLD, "getAnnotation", "(Ljava/lang/Class;)Ljava/lang/annotation/Annotation;", lambda a: None)

    _add(MTH, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("name", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(MTH, "getDeclaringClass", f"(){CLS}", lambda a: a[0].fields.get("owner") or _intern_class(stubs, "Ljava/lang/Object;") if a and isinstance(a[0], JObject) else _intern_class(stubs, "Ljava/lang/Object;"))
    _add(MTH, "getParameterTypes", f"()[{CLS}", lambda a: _ptypes_arr(a[0] if a else None))
    _add(MTH, "getReturnType", f"(){CLS}", lambda a: _intern_class(stubs, a[0].fields.get("rtype", "Ljava/lang/Object;")) if a and isinstance(a[0], JObject) else _intern_class(stubs, "Ljava/lang/Object;"))

    _add(CTR, "getName", "()Ljava/lang/String;", lambda a: stubs._cls_getname([a[0].fields.get("owner")]) if a and isinstance(a[0], JObject) and a[0].fields.get("owner") else JString(""))
    _add(CTR, "getParameterTypes", f"()[{CLS}", lambda a: _ptypes_arr(a[0] if a else None))
    _add(CTR, "getDeclaringClass", f"(){CLS}", lambda a: a[0].fields.get("owner") or _intern_class(stubs, "Ljava/lang/Object;") if a and isinstance(a[0], JObject) else _intern_class(stubs, "Ljava/lang/Object;"))

    # Reflect Type interfaces & Proxy
    _add("Ljava/lang/reflect/ParameterizedType;", "getActualTypeArguments", "()[Ljava/lang/reflect/Type;", lambda a: JArray("[Ljava/lang/reflect/Type;", []))
    _add("Ljava/lang/reflect/ParameterizedType;", "getOwnerType", "()Ljava/lang/reflect/Type;", lambda a: None)
    _add("Ljava/lang/reflect/ParameterizedType;", "getRawType", "()Ljava/lang/reflect/Type;", lambda a: _intern_class(stubs, "Ljava/lang/Object;"))
    _add("Ljava/lang/reflect/GenericArrayType;", "getGenericComponentType", "()Ljava/lang/reflect/Type;", lambda a: _intern_class(stubs, "Ljava/lang/Object;"))
    _add("Ljava/lang/reflect/TypeVariable;", "getGenericDeclaration", "()Ljava/lang/reflect/GenericDeclaration;", lambda a: _intern_class(stubs, "Ljava/lang/Object;"))
    _add("Ljava/lang/reflect/TypeVariable;", "getName", "()Ljava/lang/String;", lambda a: JString("T"))
    _add("Ljava/lang/reflect/WildcardType;", "getLowerBounds", "()[Ljava/lang/reflect/Type;", lambda a: JArray("[Ljava/lang/reflect/Type;", []))
    _add("Ljava/lang/reflect/WildcardType;", "getUpperBounds", "()[Ljava/lang/reflect/Type;", lambda a: JArray("[Ljava/lang/reflect/Type;", [_intern_class(stubs, "Ljava/lang/Object;")]))

    PRX = "Ljava/lang/reflect/Proxy;"

    def _new_proxy(a):
        ifaces = list(a[1].data) if len(a) > 1 and isinstance(a[1], JArray) else []
        ih = a[2] if len(a) > 2 else None
        return JObject(PRX, {"$proxy_handler": ih, "$proxy_ifaces": ifaces})

    _add(PRX, "newProxyInstance", "(Ljava/lang/ClassLoader;[Ljava/lang/Class;Ljava/lang/reflect/InvocationHandler;)Ljava/lang/Object;", _new_proxy)
    _add(PRX, "getInvocationHandler", "(Ljava/lang/Object;)Ljava/lang/reflect/InvocationHandler;",
         lambda a: a[0].fields.get("$proxy_handler") if a and isinstance(a[0], JObject) else None)

    vm = getattr(stubs, "vm", None)
    if vm is not None and not getattr(vm, "_g15_proxy_hook_attached", False):
        vm._g15_proxy_hook_attached = True
        _proxy_guard = set()

        def _proxy_hook(rcls, cls, name, sig, logical, static, vm_obj):
            if static or not logical or not isinstance(logical[0], JObject):
                return False, None
            recv = logical[0]
            ih = recv.fields.get("$proxy_handler")
            if not isinstance(ih, JObject) or name in ("<init>", "getClass"):
                return False, None
            key = (id(recv), name, sig)
            if key in _proxy_guard:
                return False, None
            if name == "equals":
                return True, 1 if (len(logical) > 1 and recv is logical[1]) else 0
            if name == "hashCode":
                return True, id(recv) & 0x7FFFFFFF
            if name == "toString":
                return True, JString(f"Proxy@{id(recv):x}")
            _proxy_guard.add(key)
            try:
                ptypes, rettype = stubs._split_sig(sig)
                owner = _intern_class(stubs, cls)
                mobj = JObject(MTH, {"owner": owner, "name": name, "nparams": len(ptypes), "ptypes": ptypes, "rtype": rettype})
                boxed_args = JArray("[Ljava/lang/Object;", [
                    stubs._box(arg, ptypes[i]) if i < len(ptypes) and ptypes[i] in ("Z", "B", "S", "C", "I", "J", "F", "D") else arg
                    for i, arg in enumerate(logical[1:])
                ])
                md = vm_obj._resolve_method(ih.cls, ih.cls, "invoke", "(Ljava/lang/Object;Ljava/lang/reflect/Method;[Ljava/lang/Object;)Ljava/lang/Object;")
                if md is not None:
                    res = vm_obj._run_mdef(md, [ih, recv, mobj, boxed_args])
                    if rettype in ("Z", "B", "S", "C", "I", "J", "F", "D"):
                        res = stubs._unbox_for_param(res, rettype)
                    return True, res
            except (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop):
                raise
            except Exception:
                pass
            finally:
                _proxy_guard.discard(key)
            return True, (0 if sig.endswith(("I", "J", "Z", "B", "S", "C", "F", "D")) else None)

        hooks = getattr(vm, "_method_hooks", None)
        if isinstance(hooks, list):
            hooks.append(_proxy_hook)


# ---------------------------------------------------------------------------
# 4. java.util.concurrent.*, Collections, Arrays, ListIterator, BitSet, Objects
# ---------------------------------------------------------------------------

def _natural_cmp(stubs, a, b) -> int:
    if a is b:
        return 0
    if _is_null(a):
        return -1
    if _is_null(b):
        return 1
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return -1 if a < b else (1 if a > b else 0)
    ta = _txt(a)
    tb = _txt(b)
    if ta is not None and tb is not None:
        return -1 if ta < tb else (1 if ta > tb else 0)
    if isinstance(a, JObject):
        ok, res = _invoke_vm_or_stub(stubs, a, "Ljava/lang/Comparable;", "compareTo", "(Ljava/lang/Object;)I", [b])
        if ok and isinstance(res, int):
            return _s32(res)
    sa = stubs._obj_to_str(a)
    sb = stubs._obj_to_str(b)
    return -1 if sa < sb else (1 if sa > sb else 0)


def _cmp_items(stubs, cmp_obj, a, b) -> int:
    if isinstance(cmp_obj, JObject):
        ok, res = _invoke_vm_or_stub(stubs, cmp_obj, "Ljava/util/Comparator;", "compare", "(Ljava/lang/Object;Ljava/lang/Object;)I", [a, b])
        if ok and isinstance(res, int):
            return _s32(res)
    return _natural_cmp(stubs, a, b)


def _install_concurrent_and_collections(stubs, _add, R, RB) -> None:
    from functools import cmp_to_key

    # Callable / Future / FutureTask / CompletableFuture / ExecutorService
    FUT = "Ljava/util/concurrent/Future;"
    SFUT = "Ljava/util/concurrent/ScheduledFuture;"
    FT = "Ljava/util/concurrent/FutureTask;"
    CF = "Ljava/util/concurrent/CompletableFuture;"
    ES = "Ljava/util/concurrent/ExecutorService;"
    AES = "Ljava/util/concurrent/AbstractExecutorService;"
    TPE = "Ljava/util/concurrent/ThreadPoolExecutor;"
    SES = "Ljava/util/concurrent/ScheduledExecutorService;"
    STPE = "Ljava/util/concurrent/ScheduledThreadPoolExecutor;"
    FJP = "Ljava/util/concurrent/ForkJoinPool;"
    EXS = "Ljava/util/concurrent/Executors;"

    def _run_callable(c):
        if not isinstance(c, JObject):
            return None, None
        vm = getattr(stubs, "vm", None)
        try:
            if vm is not None:
                md = vm._resolve_method(c.cls, c.cls, "call", "()Ljava/lang/Object;")
                if md is not None:
                    return vm._run_mdef(md, [c]), None
            ok, res = stubs.resolve(c.cls, "call", "()Ljava/lang/Object;", [c], False)
            return (res if ok else None), None
        except (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop):
            raise
        except Exception as exc:
            return None, exc

    def _run_runnable(r):
        if not isinstance(r, JObject):
            return
        vm = getattr(stubs, "vm", None)
        try:
            if vm is not None:
                md = vm._resolve_method(r.cls, r.cls, "run", "()V")
                if md is not None:
                    vm._run_mdef(md, [r])
                    return
            stubs.resolve(r.cls, "run", "()V", [r], False)
        except (VmBudgetExceeded, DalvikUnknownOpcode, DebugStop):
            raise
        except Exception:
            pass

    def _mk_future(res=None, exc=None, done=True, runnable=None, callable_obj=None, cls=FT, delay_ms=0):
        return JObject(cls, {
            "_result": res,
            "_exc": exc,
            "_done": done,
            "_cancelled": False,
            "_runnable": runnable,
            "_callable": callable_obj,
            "_delay_ms": delay_ms,
        })

    def _fut_get(a):
        f = a[0] if a and isinstance(a[0], JObject) else None
        if f is None:
            return None
        if f.fields.get("_cancelled"):
            _raise_exc("Ljava/util/concurrent/CancellationException;", "Future cancelled")
        if not f.fields.get("_done"):
            if isinstance(f.fields.get("_callable"), JObject):
                res, exc = _run_callable(f.fields["_callable"])
                f.fields["_result"] = res
                f.fields["_exc"] = exc
                f.fields["_done"] = True
            elif isinstance(f.fields.get("_runnable"), JObject):
                _run_runnable(f.fields["_runnable"])
                f.fields["_done"] = True
        if f.fields.get("_exc") is not None:
            exc = f.fields["_exc"]
            if isinstance(exc, DalvikException):
                raise exc
            _raise_exc("Ljava/util/concurrent/ExecutionException;", str(exc))
        return f.fields.get("_result")

    def _fut_cancel(a):
        f = a[0] if a and isinstance(a[0], JObject) else None
        if f is None or f.fields.get("_done") or f.fields.get("_cancelled"):
            return 0
        f.fields["_cancelled"] = True
        f.fields["_done"] = True
        return 1

    def _es_submit_call(a):
        c = a[1] if len(a) > 1 else None
        res, exc = _run_callable(c)
        return _mk_future(res=res, exc=exc, done=True, callable_obj=c)

    def _es_submit_run_res(a):
        r = a[1] if len(a) > 1 else None
        res = a[2] if len(a) > 2 else None
        stubs._queue_runnable(r, 0)
        return _mk_future(res=res, done=True, runnable=r)

    def _tu_to_ms(tu_obj, val: int) -> int:
        ns = int(tu_obj.fields.get("_ns", 1_000_000)) if isinstance(tu_obj, JObject) else 1_000_000
        return max(0, (int(val) * ns) // 1_000_000)

    def _ses_schedule(a, periodic: bool = False):
        task = a[1] if len(a) > 1 else None
        delay = int(a[2] or 0) if len(a) > 2 else 0
        tu = a[4] if (periodic and len(a) > 4) else (a[3] if len(a) > 3 else None)
        delay_ms = _tu_to_ms(tu, delay)
        should_q = (delay_ms == 0) if periodic else (delay_ms < 1000)
        if isinstance(task, JObject) and should_q:
            stubs._queue_runnable(task, delay_ms)
        return _mk_future(done=False, runnable=task, cls=SFUT, delay_ms=delay_ms)

    def _ses_schedule_call(a):
        c = a[1] if len(a) > 1 else None
        delay = int(a[2] or 0) if len(a) > 2 else 0
        tu = a[3] if len(a) > 3 else None
        delay_ms = _tu_to_ms(tu, delay)
        res, exc = _run_callable(c)
        return _mk_future(res=res, exc=exc, done=True, callable_obj=c, cls=SFUT, delay_ms=delay_ms)

    _add("Ljava/util/concurrent/Callable;", "call", "()Ljava/lang/Object;", lambda a: _run_callable(a[0])[0] if a else None)
    _add(FT, "<init>", "(Ljava/util/concurrent/Callable;)V", lambda a: a[0].fields.update({"_callable": a[1] if len(a) > 1 else None, "_done": False, "_cancelled": False}) if a and isinstance(a[0], JObject) else None)
    _add(FT, "<init>", "(Ljava/lang/Runnable;Ljava/lang/Object;)V", lambda a: a[0].fields.update({"_runnable": a[1] if len(a) > 1 else None, "_result": a[2] if len(a) > 2 else None, "_done": False, "_cancelled": False}) if a and isinstance(a[0], JObject) else None)
    _add(FT, "run", "()V", lambda a: _fut_get(a) if a else None)

    for _fc in (FUT, SFUT, FT, CF):
        _add(_fc, "get", "()Ljava/lang/Object;", _fut_get)
        _add(_fc, "get", "(JLjava/util/concurrent/TimeUnit;)Ljava/lang/Object;", _fut_get)
        _add(_fc, "cancel", "(Z)Z", _fut_cancel)
        _add(_fc, "isCancelled", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_cancelled")) else 0)
        _add(_fc, "isDone", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_done")) else 0)

    _add(SFUT, "getDelay", "(Ljava/util/concurrent/TimeUnit;)J", lambda a: int(a[0].fields.get("_delay_ms", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(CF, "<init>", "()V", lambda a: a[0].fields.update({"_done": False, "_cancelled": False}) if a and isinstance(a[0], JObject) else None)
    _add(CF, "completedFuture", f"(Ljava/lang/Object;){CF}", lambda a: _mk_future(res=a[0] if a else None, done=True, cls=CF))
    _add(CF, "complete", "(Ljava/lang/Object;)Z", lambda a: (a[0].fields.update({"_result": a[1] if len(a) > 1 else None, "_done": True}), 1)[1] if a and isinstance(a[0], JObject) else 0)
    _add(CF, "completeExceptionally", "(Ljava/lang/Throwable;)Z", lambda a: (a[0].fields.update({"_exc": a[1] if len(a) > 1 else None, "_done": True}), 1)[1] if a and isinstance(a[0], JObject) else 0)
    _add(CF, "join", "()Ljava/lang/Object;", _fut_get)

    for _ec in (ES, AES, TPE, SES, STPE, FJP):
        _add(_ec, "submit", f"(Ljava/util/concurrent/Callable;){FUT}", _es_submit_call)
        _add(_ec, "submit", f"(Ljava/lang/Runnable;Ljava/lang/Object;){FUT}", _es_submit_run_res)
        _add(_ec, "shutdown", "()V", lambda a: a[0].fields.__setitem__("_shutdown", True) if a and isinstance(a[0], JObject) else None)
        _add(_ec, "shutdownNow", "()Ljava/util/List;", lambda a: (a[0].fields.__setitem__("_shutdown", True) if a and isinstance(a[0], JObject) else None, JObject("Ljava/util/ArrayList;", {"__items": []}))[1])
        _add(_ec, "isShutdown", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_shutdown")) else 0)
        _add(_ec, "isTerminated", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_shutdown")) else 0)
        _add(_ec, "awaitTermination", "(JLjava/util/concurrent/TimeUnit;)Z", lambda a: 1)

    for _sec in (SES, STPE, ES):
        _add(_sec, "schedule", f"(Ljava/lang/Runnable;JLjava/util/concurrent/TimeUnit;){SFUT}", lambda a: _ses_schedule(a, periodic=False))
        _add(_sec, "schedule", f"(Ljava/util/concurrent/Callable;JLjava/util/concurrent/TimeUnit;){SFUT}", _ses_schedule_call)
        _add(_sec, "scheduleAtFixedRate", f"(Ljava/lang/Runnable;JJLjava/util/concurrent/TimeUnit;){SFUT}", lambda a: _ses_schedule(a, periodic=True))
        _add(_sec, "scheduleWithFixedDelay", f"(Ljava/lang/Runnable;JJLjava/util/concurrent/TimeUnit;){SFUT}", lambda a: _ses_schedule(a, periodic=True))

    _add(FJP, "commonPool", f"(){FJP}", lambda a: stubs.__dict__.setdefault("_fjp_common", JObject(FJP, {})))
    _add(EXS, "newSingleThreadScheduledExecutor", f"(){SES}", lambda a: JObject(STPE, {}))
    _add(EXS, "newSingleThreadScheduledExecutor", f"(Ljava/util/concurrent/ThreadFactory;){SES}", lambda a: JObject(STPE, {}))

    # Locks, Condition, CountDownLatch, Semaphore, BlockingQueues, AtomicArrays
    LK = "Ljava/util/concurrent/locks/Lock;"
    RLK = "Ljava/util/concurrent/locks/ReentrantLock;"
    CND = "Ljava/util/concurrent/locks/Condition;"

    def _cnd_await(a, timed: bool = False):
        if a and isinstance(a[0], JObject):
            cnt = int(a[0].fields.get("_awaits", 0)) + 1
            a[0].fields["_awaits"] = cnt
            if cnt > 2:
                if timed:
                    return 0
                _raise_exc("Ljava/lang/InterruptedException;", "Condition.await bounded")
        return 1 if timed else None

    _add(RLK, "<init>", "(Z)V", lambda a: None)
    _add(RLK, "newCondition", f"(){CND}", lambda a: JObject(CND, {}))
    _add(RLK, "tryLock", "(JLjava/util/concurrent/TimeUnit;)Z", lambda a: 1)
    _add(RLK, "lockInterruptibly", "()V", lambda a: None)
    _add(RLK, "isLocked", "()Z", lambda a: 0)
    _add(RLK, "isHeldByCurrentThread", "()Z", lambda a: 1)
    _add(LK, "lock", "()V", lambda a: None)
    _add(LK, "lockInterruptibly", "()V", lambda a: None)
    _add(LK, "unlock", "()V", lambda a: None)
    _add(LK, "tryLock", "()Z", lambda a: 1)
    _add(LK, "tryLock", "(JLjava/util/concurrent/TimeUnit;)Z", lambda a: 1)
    _add(LK, "newCondition", f"(){CND}", lambda a: JObject(CND, {}))
    _add(CND, "await", "()V", lambda a: _cnd_await(a, timed=False))
    _add(CND, "await", "(JLjava/util/concurrent/TimeUnit;)Z", lambda a: _cnd_await(a, timed=True))
    _add(CND, "awaitNanos", "(J)J", lambda a: (0 if _cnd_await(a, timed=True) == 0 else max(0, int(a[1] if len(a) > 1 else 0))))
    _add(CND, "awaitUninterruptibly", "()V", lambda a: None)
    _add(CND, "signal", "()V", lambda a: a[0].fields.__setitem__("_awaits", 0) if a and isinstance(a[0], JObject) else None)
    _add(CND, "signalAll", "()V", lambda a: a[0].fields.__setitem__("_awaits", 0) if a and isinstance(a[0], JObject) else None)

    def _cdl_await(a):
        if a and isinstance(a[0], JObject):
            vm = getattr(stubs, "vm", None)
            pending = getattr(stubs, "pending_runnables", None)
            if isinstance(pending, list) and pending:
                for _ in range(64):
                    if int(a[0].fields.get("_count", 0)) <= 0 or not pending:
                        break
                    it = pending.pop(0)
                    r = it[0] if isinstance(it, tuple) else it
                    if callable(r):
                        r()
                    elif isinstance(r, JObject):
                        try:
                            _invoke_vm_or_stub(stubs, r, "Ljava/lang/Runnable;", "run", "()V", [])
                        except (DebugStop, DalvikUnknownOpcode, VmBudgetExceeded):
                            raise
                        except Exception:
                            pass
        return None

    CDL = "Ljava/util/concurrent/CountDownLatch;"
    _add(CDL, "<init>", "(I)V", lambda a: a[0].fields.__setitem__("_count", max(0, int(a[1] if len(a) > 1 else 0))) if a and isinstance(a[0], JObject) else None)
    _add(CDL, "countDown", "()V", lambda a: a[0].fields.__setitem__("_count", max(0, int(a[0].fields.get("_count", 0)) - 1)) if a and isinstance(a[0], JObject) else None)
    _add(CDL, "getCount", "()J", lambda a: int(a[0].fields.get("_count", 0)) if a and isinstance(a[0], JObject) else 0)
    R(CDL, "await", "()V", _cdl_await)
    R(CDL, "await", "(JLjava/util/concurrent/TimeUnit;)Z",
      lambda a: (_cdl_await(a), 1 if not (a and isinstance(a[0], JObject)) or int(a[0].fields.get("_count", 0)) <= 0 else 0)[1])

    SEM = "Ljava/util/concurrent/Semaphore;"
    _add(SEM, "<init>", "(I)V", lambda a: a[0].fields.__setitem__("_permits", int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "<init>", "(IZ)V", lambda a: a[0].fields.__setitem__("_permits", int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "acquire", "()V", lambda a: a[0].fields.__setitem__("_permits", int(a[0].fields.get("_permits", 1)) - 1) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "acquire", "(I)V", lambda a: a[0].fields.__setitem__("_permits", int(a[0].fields.get("_permits", 1)) - int(a[1] if len(a) > 1 else 1)) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "tryAcquire", "()Z", lambda a: (lambda p: (a[0].fields.__setitem__("_permits", p - 1), 1)[1] if p > 0 else 0)(int(a[0].fields.get("_permits", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(SEM, "tryAcquire", "(JLjava/util/concurrent/TimeUnit;)Z", lambda a: 1)
    _add(SEM, "release", "()V", lambda a: a[0].fields.__setitem__("_permits", int(a[0].fields.get("_permits", 0)) + 1) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "release", "(I)V", lambda a: a[0].fields.__setitem__("_permits", int(a[0].fields.get("_permits", 0)) + int(a[1] if len(a) > 1 else 1)) if a and isinstance(a[0], JObject) else None)
    _add(SEM, "availablePermits", "()I", lambda a: int(a[0].fields.get("_permits", 0)) if a and isinstance(a[0], JObject) else 0)

    for _bqc in (
        "Ljava/util/concurrent/LinkedBlockingQueue;",
        "Ljava/util/concurrent/ArrayBlockingQueue;",
        "Ljava/util/concurrent/PriorityBlockingQueue;",
        "Ljava/util/concurrent/SynchronousQueue;",
        "Ljava/util/concurrent/BlockingQueue;",
        "Ljava/util/concurrent/ConcurrentLinkedQueue;",
        "Ljava/util/Queue;",
    ):
        _add(_bqc, "<init>", "()V", lambda a: stubs._coll_init(a, "__items", []))
        _add(_bqc, "<init>", "(I)V", lambda a: stubs._coll_init(a, "__items", []))
        _add(_bqc, "<init>", "(Ljava/util/Collection;)V", stubs._jcoll_init)
        _add(_bqc, "put", "(Ljava/lang/Object;)V", lambda a: (stubs._coll_add(a), None)[1])
        _add(_bqc, "offer", "(Ljava/lang/Object;JLjava/util/concurrent/TimeUnit;)Z", lambda a: stubs._coll_add(a[:2]))
        _add(_bqc, "poll", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if (a and stubs._coll_items(a[0])) else None)
        _add(_bqc, "poll", "(JLjava/util/concurrent/TimeUnit;)Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if (a and stubs._coll_items(a[0])) else None)
        _add(_bqc, "take", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if (a and stubs._coll_items(a[0])) else None)
        _add(_bqc, "peek", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0])[0] if (a and stubs._coll_items(a[0])) else None)

    for _aac, _aat in (("Ljava/util/concurrent/atomic/AtomicIntegerArray;", "I"), ("Ljava/util/concurrent/atomic/AtomicLongArray;", "J")):
        _add(_aac, "<init>", "(I)V", lambda a: a[0].fields.__setitem__("arr", [0] * max(0, int(a[1] if len(a) > 1 else 0))) if a and isinstance(a[0], JObject) else None)
        _add(_aac, "<init>", f"([{_aat})V", lambda a: a[0].fields.__setitem__("arr", list(a[1].data) if len(a) > 1 and isinstance(a[1], JArray) else []) if a and isinstance(a[0], JObject) else None)
        _add(_aac, "length", "()I", lambda a: len(a[0].fields.get("arr", [])) if a and isinstance(a[0], JObject) else 0)
        _add(_aac, "get", f"(I){_aat}", lambda a: int(a[0].fields.get("arr", [])[int(a[1])]) if len(a) > 1 and isinstance(a[0], JObject) and 0 <= int(a[1]) < len(a[0].fields.get("arr", [])) else 0)
        _add(_aac, "set", f"(I{_aat})V", lambda a: a[0].fields.get("arr", []).__setitem__(int(a[1]), int(a[2])) if len(a) > 2 and isinstance(a[0], JObject) and 0 <= int(a[1]) < len(a[0].fields.get("arr", [])) else None)
        _add(_aac, "getAndSet", f"(I{_aat}){_aat}", lambda a: (lambda arr, i, nv: (arr[i], arr.__setitem__(i, nv))[0] if 0 <= i < len(arr) else 0)(a[0].fields.get("arr", []), int(a[1]), int(a[2])) if len(a) > 2 and isinstance(a[0], JObject) else 0)
        _add(_aac, "compareAndSet", f"(I{_aat}{_aat})Z", lambda a: (lambda arr, i, ev, nv: (arr.__setitem__(i, nv), 1)[1] if (0 <= i < len(arr) and arr[i] == ev) else 0)(a[0].fields.get("arr", []), int(a[1]), int(a[2]), int(a[3])) if len(a) > 3 and isinstance(a[0], JObject) else 0)

    # BitSet
    BS = "Ljava/util/BitSet;"

    def _bs_bits(o) -> set[int]:
        if isinstance(o, JObject):
            s = o.fields.get("_bits")
            if not isinstance(s, set):
                s = set()
                o.fields["_bits"] = s
            return s
        return set()

    def _bs_from_bytes(a):
        s: set[int] = set()
        raw = _bytes_from(a[0] if a else None)
        for bi, bv in enumerate(raw):
            for bit in range(8):
                if bv & (1 << bit):
                    s.add(bi * 8 + bit)
        return JObject(BS, {"_bits": s})

    def _bs_set_range(s: set[int], i0: int, i1: int, val: bool):
        for i in range(max(0, i0), max(0, i1)):
            if val:
                s.add(i)
            else:
                s.discard(i)

    _add(BS, "<init>", "()V", lambda a: a[0].fields.__setitem__("_bits", set()) if a and isinstance(a[0], JObject) else None)
    _add(BS, "<init>", "(I)V", lambda a: a[0].fields.__setitem__("_bits", set()) if a and isinstance(a[0], JObject) else None)
    _add(BS, "valueOf", f"([B){BS}", _bs_from_bytes)
    _add(BS, "get", "(I)Z", lambda a: 1 if (len(a) > 1 and int(a[1]) in _bs_bits(a[0])) else 0)
    _add(BS, "set", "(I)V", lambda a: _bs_bits(a[0]).add(max(0, int(a[1]))) if len(a) > 1 else None)
    _add(BS, "set", "(IZ)V", lambda a: (_bs_bits(a[0]).add(max(0, int(a[1]))) if a[2] else _bs_bits(a[0]).discard(max(0, int(a[1])))) if len(a) > 2 else None)
    _add(BS, "set", "(II)V", lambda a: _bs_set_range(_bs_bits(a[0]), int(a[1]), int(a[2]), True) if len(a) > 2 else None)
    _add(BS, "set", "(IIZ)V", lambda a: _bs_set_range(_bs_bits(a[0]), int(a[1]), int(a[2]), bool(a[3])) if len(a) > 3 else None)
    _add(BS, "clear", "()V", lambda a: _bs_bits(a[0]).clear() if a else None)
    _add(BS, "clear", "(I)V", lambda a: _bs_bits(a[0]).discard(max(0, int(a[1]))) if len(a) > 1 else None)
    _add(BS, "clear", "(II)V", lambda a: _bs_set_range(_bs_bits(a[0]), int(a[1]), int(a[2]), False) if len(a) > 2 else None)
    _add(BS, "cardinality", "()I", lambda a: len(_bs_bits(a[0])) if a else 0)
    _add(BS, "length", "()I", lambda a: (max(_bs_bits(a[0])) + 1) if (a and _bs_bits(a[0])) else 0)
    _add(BS, "isEmpty", "()Z", lambda a: 0 if (a and _bs_bits(a[0])) else 1)

    # Objects
    OBS = "Ljava/util/Objects;"

    def _obs_eq(a):
        if len(a) < 2:
            return 0
        x, y = a[0], a[1]
        if _is_null(x) and _is_null(y):
            return 1
        if _is_null(x) or _is_null(y):
            return 0
        return 1 if stubs._eq_el(x, y) else 0

    def _obs_hash1(x) -> int:
        if _is_null(x):
            return 0
        if isinstance(x, JString):
            return stubs._java_strhash(x.text)
        if isinstance(x, JObject):
            ok, res = _invoke_vm_or_stub(stubs, x, "Ljava/lang/Object;", "hashCode", "()I", [])
            if ok and isinstance(res, int):
                return _s32(res)
            return id(x) & 0x7FFFFFFF
        return hash(x) & 0x7FFFFFFF

    def _obs_hash_arr(a) -> int:
        arr = a[0] if a else None
        if not isinstance(arr, JArray):
            return 0
        h = 1
        for el in arr.data:
            h = _s32(31 * h + _obs_hash1(el))
        return h

    def _obs_req_nn(a):
        v = a[0] if a else None
        if _is_null(v):
            _raise_exc("Ljava/lang/NullPointerException;", _str_or_empty(a[1] if len(a) > 1 else ""))
        return v

    _add(OBS, "equals", "(Ljava/lang/Object;Ljava/lang/Object;)Z", _obs_eq)
    _add(OBS, "hashCode", "(Ljava/lang/Object;)I", lambda a: _obs_hash1(a[0] if a else None))
    _add(OBS, "hash", "([Ljava/lang/Object;)I", _obs_hash_arr)
    _add(OBS, "requireNonNull", "(Ljava/lang/Object;)Ljava/lang/Object;", _obs_req_nn)
    _add(OBS, "requireNonNull", "(Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;", _obs_req_nn)
    _add(OBS, "isNull", "(Ljava/lang/Object;)Z", lambda a: 1 if (not a or _is_null(a[0])) else 0)
    _add(OBS, "nonNull", "(Ljava/lang/Object;)Z", lambda a: 0 if (not a or _is_null(a[0])) else 1)
    _add(OBS, "toString", "(Ljava/lang/Object;)Ljava/lang/String;", lambda a: JString(stubs._obj_to_str(a[0] if a else None)))
    _add(OBS, "toString", "(Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/String;",
         lambda a: JString(_str_or_empty(a[1] if len(a) > 1 else "null")) if (not a or _is_null(a[0])) else JString(stubs._obj_to_str(a[0])))

    # ListIterator & List/Collection/Map gaps
    LIT = "Ljava/util/ListIterator;"

    def _lit_items(it):
        return stubs._coll_items(it.fields.get("__src")) if isinstance(it, JObject) else []

    def _lit_next(a):
        it = a[0] if a and isinstance(a[0], JObject) else None
        if it is None:
            return None
        items = _lit_items(it)
        idx = int(it.fields.get("__idx", 0))
        if idx >= len(items):
            return None
        it.fields["__last"] = idx
        it.fields["__idx"] = idx + 1
        return items[idx]

    def _lit_prev(a):
        it = a[0] if a and isinstance(a[0], JObject) else None
        if it is None:
            return None
        items = _lit_items(it)
        idx = int(it.fields.get("__idx", 0)) - 1
        if idx < 0 or idx >= len(items):
            return None
        it.fields["__idx"] = idx
        it.fields["__last"] = idx
        return items[idx]

    def _lit_set(a):
        it = a[0] if a and isinstance(a[0], JObject) else None
        if it is not None:
            items = _lit_items(it)
            last = int(it.fields.get("__last", -1))
            if 0 <= last < len(items):
                items[last] = a[1] if len(a) > 1 else None
        return None

    def _lit_add(a):
        it = a[0] if a and isinstance(a[0], JObject) else None
        if it is not None:
            items = _lit_items(it)
            idx = max(0, min(len(items), int(it.fields.get("__idx", 0))))
            items.insert(idx, a[1] if len(a) > 1 else None)
            it.fields["__idx"] = idx + 1
            it.fields["__last"] = -1
        return None

    def _lit_rem(a):
        it = a[0] if a and isinstance(a[0], JObject) else None
        if it is not None:
            items = _lit_items(it)
            last = int(it.fields.get("__last", -1))
            if 0 <= last < len(items):
                del items[last]
                if last < int(it.fields.get("__idx", 0)):
                    it.fields["__idx"] = max(0, int(it.fields.get("__idx", 0)) - 1)
                it.fields["__last"] = -1
        return None

    _add(LIT, "hasNext", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("__idx", 0)) < len(_lit_items(a[0]))) else 0)
    _add(LIT, "next", "()Ljava/lang/Object;", _lit_next)
    _add(LIT, "hasPrevious", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("__idx", 0)) > 0) else 0)
    _add(LIT, "previous", "()Ljava/lang/Object;", _lit_prev)
    _add(LIT, "nextIndex", "()I", lambda a: int(a[0].fields.get("__idx", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(LIT, "previousIndex", "()I", lambda a: int(a[0].fields.get("__idx", 0)) - 1 if a and isinstance(a[0], JObject) else -1)
    _add(LIT, "set", "(Ljava/lang/Object;)V", _lit_set)
    _add(LIT, "add", "(Ljava/lang/Object;)V", _lit_add)
    _add(LIT, "remove", "()V", _lit_rem)

    for _lc in ("Ljava/util/List;", "Ljava/util/ArrayList;", "Ljava/util/LinkedList;", "Ljava/util/Vector;", "Ljava/util/concurrent/CopyOnWriteArrayList;"):
        _add(_lc, "listIterator", f"(){LIT}", lambda a: JObject(LIT, {"__src": a[0] if a else None, "__idx": 0, "__last": -1}))
        _add(_lc, "listIterator", f"(I){LIT}", lambda a: JObject(LIT, {"__src": a[0] if a else None, "__idx": max(0, int(a[1] if len(a) > 1 else 0)), "__last": -1}))
        _add(_lc, "subList", "(II)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(stubs._coll_items(a[0])[max(0, int(a[1])):max(0, int(a[2]))])}) if len(a) > 2 else JObject("Ljava/util/ArrayList;", {"__items": []}))
        _add(_lc, "ensureCapacity", "(I)V", lambda a: None)

    def _coll_contains_all(a):
        if len(a) < 2:
            return 0
        items = stubs._coll_items(a[0])
        for x in stubs._extract_coll_items(a[1]):
            if not any(stubs._eq_el(y, x) for y in items):
                return 0
        return 1

    def _coll_remove_all(a):
        if len(a) < 2:
            return 0
        items = stubs._coll_items(a[0])
        rem = stubs._extract_coll_items(a[1])
        new_items = [x for x in items if not any(stubs._eq_el(x, r) for r in rem)]
        changed = 1 if len(new_items) != len(items) else 0
        items[:] = new_items
        return changed

    def _coll_retain_all(a):
        if len(a) < 2:
            return 0
        items = stubs._coll_items(a[0])
        keep = stubs._extract_coll_items(a[1])
        new_items = [x for x in items if any(stubs._eq_el(x, k) for k in keep)]
        changed = 1 if len(new_items) != len(items) else 0
        items[:] = new_items
        return changed

    for _cc in ("Ljava/util/Collection;", "Ljava/util/List;", "Ljava/util/Set;", "Ljava/util/ArrayList;", "Ljava/util/HashSet;", "Ljava/util/LinkedHashSet;", "Ljava/util/concurrent/CopyOnWriteArrayList;"):
        _add(_cc, "containsAll", "(Ljava/util/Collection;)Z", _coll_contains_all)
        # R auf ArrayList/CopyOnWriteArrayList, da g14 removeAll mit lambda a: 0 belegt hatte
        R(_cc, "removeAll", "(Ljava/util/Collection;)Z", _coll_remove_all)
        _add(_cc, "retainAll", "(Ljava/util/Collection;)Z", _coll_retain_all)

    # Arrays & Collections
    ARR = "Ljava/util/Arrays;"
    COLS = "Ljava/util/Collections;"

    def _arr_sort(a):
        if not a or not isinstance(a[0], JArray):
            return None
        d = a[0].data
        if len(a) == 1:
            if a[0].eltype in ("[I", "[J", "[B", "[S", "[C", "[F", "[D"):
                d.sort()
            else:
                d.sort(key=cmp_to_key(lambda x, y: _natural_cmp(stubs, x, y)))
        elif len(a) == 2:
            cmp_obj = a[1]
            d.sort(key=cmp_to_key(lambda x, y: _cmp_items(stubs, cmp_obj, x, y)))
        elif len(a) == 3:
            s, e = max(0, int(a[1])), min(len(d), int(a[2]))
            seg = d[s:e]
            seg.sort(key=cmp_to_key(lambda x, y: _natural_cmp(stubs, x, y)))
            d[s:e] = seg
        elif len(a) >= 4:
            s, e, cmp_obj = max(0, int(a[1])), min(len(d), int(a[2])), a[3]
            seg = d[s:e]
            seg.sort(key=cmp_to_key(lambda x, y: _cmp_items(stubs, cmp_obj, x, y)))
            d[s:e] = seg
        return None

    def _arr_bsearch(a):
        if len(a) < 2 or not isinstance(a[0], JArray):
            return -1
        d = a[0].data
        key = a[1]
        lo, hi = 0, len(d) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            c = _natural_cmp(stubs, d[mid], key)
            if c < 0:
                lo = mid + 1
            elif c > 0:
                hi = mid - 1
            else:
                return mid
        return -(lo + 1)

    def _arr_hash(a):
        if not a or not isinstance(a[0], JArray):
            return 0
        h = 1
        for el in a[0].data:
            eh = _obs_hash1(el) if isinstance(el, (JObject, JString)) else int(el if isinstance(el, int) else hash(el))
            h = _s32(31 * h + eh)
        return h

    def _arr_tostr(a, deep=False):
        if not a or _is_null(a[0]):
            return JString("null")
        if not isinstance(a[0], JArray):
            return JString("[]")
        parts = []
        for el in a[0].data:
            if deep and isinstance(el, JArray):
                parts.append(_arr_tostr([el], deep=True).text)
            elif isinstance(el, (JObject, JString)) or _is_null(el):
                parts.append(stubs._obj_to_str(el))
            else:
                parts.append(str(el))
        return JString("[" + ", ".join(parts) + "]")

    for _at in ("I", "J", "B", "S", "C", "F", "D", "Ljava/lang/Object;"):
        _add(ARR, "sort", f"([{_at})V", _arr_sort)
        _add(ARR, "sort", f"([{_at}II)V", _arr_sort)
        _add(ARR, "binarySearch", f"([{_at}{_at})I", _arr_bsearch)
        _add(ARR, "equals", f"([{_at}[{_at})Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JArray) and isinstance(a[1], JArray) and len(a[0].data) == len(a[1].data) and all(stubs._eq_el(x, y) for x, y in zip(a[0].data, a[1].data))) else 0)
        _add(ARR, "hashCode", f"([{_at})I", _arr_hash)
        _add(ARR, "toString", f"([{_at})Ljava/lang/String;", lambda a: _arr_tostr(a, False))
        _add(ARR, "copyOfRange", f"([{_at}II)[{_at}", lambda a, t=f"[{_at}": JArray(t, list(a[0].data[int(a[1]):int(a[2])])) if len(a) > 2 and isinstance(a[0], JArray) else JArray(t, []))

    _add(ARR, "sort", "([Ljava/lang/Object;Ljava/util/Comparator;)V", _arr_sort)
    _add(ARR, "sort", "([Ljava/lang/Object;IILjava/util/Comparator;)V", _arr_sort)
    _add(ARR, "deepToString", "([Ljava/lang/Object;)Ljava/lang/String;", lambda a: _arr_tostr(a, True))
    _add(ARR, "copyOfRange", "([Ljava/lang/Object;IILjava/lang/Class;)[Ljava/lang/Object;",
         lambda a: JArray(getattr(a[0], "eltype", "[Ljava/lang/Object;"), list(a[0].data[int(a[1]):int(a[2])])) if len(a) > 2 and isinstance(a[0], JArray) else JArray("[Ljava/lang/Object;", []))

    _add(COLS, "reverse", "(Ljava/util/List;)V", lambda a: stubs._coll_items(a[0]).reverse() if a else None)
    _add(COLS, "singletonMap", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/util/Map;",
         lambda a: JObject("Ljava/util/HashMap;", {"map": {stubs._mkey(a[0]): a[1]}, "__raw_keys": {stubs._mkey(a[0]): a[0]}}) if len(a) > 1 else JObject("Ljava/util/HashMap;", {"map": {}}))
    _add(COLS, "sort", "(Ljava/util/List;)V", lambda a: stubs._coll_items(a[0]).sort(key=cmp_to_key(lambda x, y: _natural_cmp(stubs, x, y))) if a else None)
    _add(COLS, "sort", "(Ljava/util/List;Ljava/util/Comparator;)V", lambda a: stubs._coll_items(a[0]).sort(key=cmp_to_key(lambda x, y: _cmp_items(stubs, a[1], x, y))) if len(a) > 1 else None)
    _add(COLS, "unmodifiableSet", "(Ljava/util/Set;)Ljava/util/Set;", lambda a: a[0] if a else None)
    _add(COLS, "synchronizedSet", "(Ljava/util/Set;)Ljava/util/Set;", lambda a: a[0] if a else None)
    _add(COLS, "binarySearch", "(Ljava/util/List;Ljava/lang/Object;)I",
         lambda a: (lambda d, k: next((i for i, x in enumerate(d) if _natural_cmp(stubs, x, k) == 0), -1))(stubs._coll_items(a[0]), a[1]) if len(a) > 1 else -1)
    _add(COLS, "list", "(Ljava/util/Enumeration;)Ljava/util/ArrayList;",
         lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(stubs._extract_coll_items(a[0]))}) if a else JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(COLS, "min", "(Ljava/util/Collection;Ljava/util/Comparator;)Ljava/lang/Object;",
         lambda a: min(stubs._extract_coll_items(a[0]), key=cmp_to_key(lambda x, y: _cmp_items(stubs, a[1], x, y))) if len(a) > 1 and stubs._extract_coll_items(a[0]) else None)
    _add(COLS, "swap", "(Ljava/util/List;II)V",
         lambda a: (lambda lst, i, j: lst.__setitem__(i, (lst[j], lst.__setitem__(j, lst[i]))[0]))(stubs._coll_items(a[0]), int(a[1]), int(a[2])) if len(a) > 2 else None)

    # Enumeration, ArrayDeque, Deque, PriorityQueue, IdentityHashMap, WeakHashMap, Map, TreeSet/Map, EnumSet, StringTokenizer, Currency, Timer
    _add("Ljava/util/Enumeration;", "hasMoreElements", "()Z", stubs._iter_hasnext)
    _add("Ljava/util/Enumeration;", "nextElement", "()Ljava/lang/Object;", stubs._iter_next)
    RB("hasMoreElements", "()Z", stubs._iter_hasnext)
    RB("nextElement", "()Ljava/lang/Object;", stubs._iter_next)

    for _dqc in ("Ljava/util/ArrayDeque;", "Ljava/util/Deque;", "Ljava/util/LinkedList;"):
        _add(_dqc, "peek", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0])[0] if a and stubs._coll_items(a[0]) else None)
        _add(_dqc, "poll", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if a and stubs._coll_items(a[0]) else None)
        _add(_dqc, "pop", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if a and stubs._coll_items(a[0]) else None)
        _add(_dqc, "push", "(Ljava/lang/Object;)V", lambda a: stubs._coll_items(a[0]).insert(0, a[1] if len(a) > 1 else None) if a else None)
        _add(_dqc, "remove", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if a and stubs._coll_items(a[0]) else None)
        _add(_dqc, "removeFirst", "()Ljava/lang/Object;", lambda a: stubs._coll_items(a[0]).pop(0) if a and stubs._coll_items(a[0]) else None)

    PQ = "Ljava/util/PriorityQueue;"
    _add(PQ, "<init>", "()V", lambda a: stubs._coll_init(a, "__items", []))
    _add(PQ, "<init>", "(ILjava/util/Comparator;)V", lambda a: (stubs._coll_init(a, "__items", []), a[0].fields.__setitem__("_cmp", a[2] if len(a) > 2 else None)) if a and isinstance(a[0], JObject) else None)
    _add(PQ, "poll", "()Ljava/lang/Object;", lambda a: (lambda lst, cmp_o: (lst.sort(key=cmp_to_key(lambda x, y: _cmp_items(stubs, cmp_o, x, y))), lst.pop(0))[1] if lst else None)(stubs._coll_items(a[0]), a[0].fields.get("_cmp") if isinstance(a[0], JObject) else None) if a else None)
    _add(PQ, "peek", "()Ljava/lang/Object;", lambda a: (lambda lst, cmp_o: (lst.sort(key=cmp_to_key(lambda x, y: _cmp_items(stubs, cmp_o, x, y))), lst[0])[1] if lst else None)(stubs._coll_items(a[0]), a[0].fields.get("_cmp") if isinstance(a[0], JObject) else None) if a else None)

    IHM = "Ljava/util/IdentityHashMap;"
    _add(IHM, "<init>", "()V", lambda a: stubs._coll_init(a, "map", {}))
    _add(IHM, "<init>", "(I)V", lambda a: stubs._coll_init(a, "map", {}))
    _add(IHM, "put", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
         lambda a: (lambda m, rk, k, v: (m.get(k), m.__setitem__(k, v), a[0].fields.setdefault("__raw_keys", {}).__setitem__(k, rk))[0])(a[0].fields.setdefault("map", {}), a[1], id(a[1]) if isinstance(a[1], (JObject, JArray)) else a[1], a[2] if len(a) > 2 else None) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(IHM, "get", "(Ljava/lang/Object;)Ljava/lang/Object;",
         lambda a: a[0].fields.get("map", {}).get(id(a[1]) if isinstance(a[1], (JObject, JArray)) else a[1]) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(IHM, "remove", "(Ljava/lang/Object;)Ljava/lang/Object;",
         lambda a: a[0].fields.get("map", {}).pop(id(a[1]) if isinstance(a[1], (JObject, JArray)) else a[1], None) if len(a) > 1 and isinstance(a[0], JObject) else None)

    WHM = "Ljava/util/WeakHashMap;"
    _add(WHM, "containsKey", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and stubs._mkey(a[1]) in a[0].fields.get("map", {})) else 0)
    _add(WHM, "entrySet", "()Ljava/util/Set;", stubs._map_entryset)
    _add(WHM, "keySet", "()Ljava/util/Set;", stubs._map_keyset)
    _add(WHM, "remove", "(Ljava/lang/Object;)Ljava/lang/Object;", lambda a: a[0].fields.get("map", {}).pop(stubs._mkey(a[1]), None) if len(a) > 1 and isinstance(a[0], JObject) else None)

    _add("Ljava/util/Map;", "containsValue", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[0], JObject) and any(stubs._eq_el(v, a[1]) for v in a[0].fields.get("map", {}).values())) else 0)
    _add("Ljava/util/Map$Entry;", "setValue", "(Ljava/lang/Object;)Ljava/lang/Object;", lambda a: (a[0].fields.get("value"), a[0].fields.__setitem__("value", a[1] if len(a) > 1 else None))[0] if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/concurrent/ConcurrentMap;", "putIfAbsent", "(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;", stubs._map_put_if_absent)
    _add("Ljava/util/TreeMap;", "<init>", "(Ljava/util/Comparator;)V", lambda a: (stubs._coll_init(a, "map", {}), a[0].fields.__setitem__("_cmp", a[1] if len(a) > 1 else None)) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/TreeSet;", "<init>", "(Ljava/util/Comparator;)V", lambda a: (stubs._coll_init(a, "__items", []), a[0].fields.__setitem__("_cmp", a[1] if len(a) > 1 else None)) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/AbstractQueue;", "<init>", "()V", lambda a: stubs._coll_init(a, "__items", []))
    _add("Ljava/util/Properties;", "entrySet", "()Ljava/util/Set;", stubs._map_entryset)
    _add("Ljava/util/Properties;", "keySet", "()Ljava/util/Set;", stubs._map_keyset)
    _add("Ljava/util/Properties;", "clone", "()Ljava/lang/Object;",
         lambda a: JObject("Ljava/util/Properties;", {"map": dict(a[0].fields.get("map", {}))}) if a and isinstance(a[0], JObject) else JObject("Ljava/util/Properties;", {"map": {}}))
    _add("Ljava/util/EnumSet;", "noneOf", "(Ljava/lang/Class;)Ljava/util/EnumSet;", lambda a: JObject("Ljava/util/EnumSet;", {"__items": [], "_elem_cls": a[0] if a else None}))

    # StringTokenizer
    STOK = "Ljava/util/StringTokenizer;"

    def _stok_init(a):
        if a and isinstance(a[0], JObject):
            s = _str_or_empty(a[1] if len(a) > 1 else "")
            delim = _str_or_empty(a[2]) if len(a) > 2 else " \t\n\r\f"
            ret_delims = bool(a[3]) if len(a) > 3 else False
            if ret_delims and delim:
                pat = f"([{re.escape(delim)}])"
                toks = [t for t in re.split(pat, s) if t]
            elif delim:
                toks = [t for t in re.split(f"[{re.escape(delim)}]+", s) if t]
            else:
                toks = [s] if s else []
            a[0].fields["__items"] = toks
            a[0].fields["__idx"] = 0
        return a[0] if a else None

    _add(STOK, "<init>", "(Ljava/lang/String;)V", _stok_init)
    _add(STOK, "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", _stok_init)
    _add(STOK, "<init>", "(Ljava/lang/String;Ljava/lang/String;Z)V", _stok_init)
    _add(STOK, "hasMoreTokens", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("__idx", 0)) < len(a[0].fields.get("__items", []))) else 0)
    _add(STOK, "hasMoreElements", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and int(a[0].fields.get("__idx", 0)) < len(a[0].fields.get("__items", []))) else 0)
    _add(STOK, "nextToken", "()Ljava/lang/String;", lambda a: (lambda it, i: (it.fields.__setitem__("__idx", i + 1), JString(it.fields["__items"][i]))[1] if i < len(it.fields.get("__items", [])) else JString(""))(a[0], int(a[0].fields.get("__idx", 0))) if a and isinstance(a[0], JObject) else JString(""))
    _add(STOK, "countTokens", "()I", lambda a: max(0, len(a[0].fields.get("__items", [])) - int(a[0].fields.get("__idx", 0))) if a and isinstance(a[0], JObject) else 0)

    _add("Ljava/util/Currency;", "getInstance", "(Ljava/lang/String;)Ljava/util/Currency;", lambda a: JObject("Ljava/util/Currency;", {"code": _str_or_empty(a[0] if a else "EUR") or "EUR"}))
    _add("Ljava/util/Currency;", "getCurrencyCode", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("code", "EUR")) if a and isinstance(a[0], JObject) else JString("EUR"))
    _add("Ljava/util/Timer;", "<init>", "()V", lambda a: None)
    _add("Ljava/util/Timer;", "<init>", "(Z)V", lambda a: None)
    _add("Ljava/util/Timer;", "schedule", "(Ljava/util/TimerTask;JJ)V", lambda a: (stubs._queue_runnable(a[1] if len(a) > 1 else None, 0), None)[1])
    _add("Ljava/util/Random;", "nextBytes", "([B)V", lambda a: [a[1].data.__setitem__(i, stubs._next_rand_int(256)) for i in range(len(a[1].data))] if len(a) > 1 and isinstance(a[1], JArray) else None)


# ---------------------------------------------------------------------------
# 5. java.util.zip.* & java.math.*
# ---------------------------------------------------------------------------

def _install_zip_and_math_big(stubs, _add, R) -> None:
    # Inflater / Deflater / InflaterInputStream / DeflaterOutputStream / CRC32 / Adler32
    INF = "Ljava/util/zip/Inflater;"
    DEF = "Ljava/util/zip/Deflater;"
    IIS = "Ljava/util/zip/InflaterInputStream;"
    DOS = "Ljava/util/zip/DeflaterOutputStream;"
    CRC = "Ljava/util/zip/CRC32;"
    ADL = "Ljava/util/zip/Adler32;"

    def _inf_init(a):
        if a and isinstance(a[0], JObject):
            nowrap = bool(a[1]) if len(a) > 1 else False
            wbits = -15 if nowrap else 15
            a[0].fields.update({
                "_nowrap": nowrap,
                "_wbits": wbits,
                "_dc": zlib.decompressobj(wbits),
                "_in": b"",
                "_finished": False,
                "_written": 0,
                "_read": 0,
                "_needs_dict": False,
            })
        return a[0] if a else None

    def _inf_set_input(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            raw = _bytes_from(a[1])
            off = int(a[2]) if len(a) > 3 else 0
            ln = int(a[3]) if len(a) > 3 else len(raw) - off
            a[0].fields["_in"] = raw[off:off + max(0, ln)]
        return None

    def _inf_inflate(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2 or not isinstance(a[1], JArray):
            return 0
        o = a[0]
        dst = a[1]
        off = int(a[2]) if len(a) > 3 else 0
        ln = int(a[3]) if len(a) > 3 else len(dst.data) - off
        ln = max(0, min(ln, len(dst.data) - off))
        inp = o.fields.get("_in", b"")
        dc = o.fields.get("_dc")
        if dc is None:
            return 0
        if not inp and not getattr(dc, "unconsumed_tail", b""):
            return 0
        try:
            chunk_in = inp if inp else dc.unconsumed_tail
            out = dc.decompress(chunk_in, ln)
            if dc.eof:
                o.fields["_finished"] = True
                o.fields["_in"] = bytes(dc.unused_data)
            else:
                o.fields["_in"] = bytes(dc.unconsumed_tail)
            consumed = len(chunk_in) - len(o.fields["_in"])
            o.fields["_read"] = int(o.fields.get("_read", 0)) + max(0, consumed)
            o.fields["_written"] = int(o.fields.get("_written", 0)) + len(out)
            for i, b in enumerate(out):
                dst.data[off + i] = b
            return len(out)
        except Exception:
            o.fields["_in"] = b""
            return 0

    _add(INF, "<init>", "()V", _inf_init)
    _add(INF, "<init>", "(Z)V", _inf_init)
    _add(INF, "setInput", "([B)V", _inf_set_input)
    _add(INF, "setInput", "([BII)V", _inf_set_input)
    _add(INF, "inflate", "([B)I", _inf_inflate)
    _add(INF, "inflate", "([BII)I", _inf_inflate)
    _add(INF, "finished", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_finished")) else 0)
    _add(INF, "needsInput", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and (a[0].fields.get("_in") or getattr(a[0].fields.get("_dc"), "unconsumed_tail", b""))) else 1)
    _add(INF, "needsDictionary", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_needs_dict")) else 0)
    _add(INF, "getRemaining", "()I", lambda a: len(a[0].fields.get("_in", b"")) if a and isinstance(a[0], JObject) else 0)
    _add(INF, "getBytesWritten", "()J", lambda a: int(a[0].fields.get("_written", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(INF, "getBytesRead", "()J", lambda a: int(a[0].fields.get("_read", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(INF, "reset", "()V", lambda a: _inf_init([a[0], a[0].fields.get("_nowrap", False)]) if a and isinstance(a[0], JObject) else None)
    _add(INF, "end", "()V", lambda a: a[0].fields.update({"_dc": None, "_in": b""}) if a and isinstance(a[0], JObject) else None)

    def _def_init(a):
        if a and isinstance(a[0], JObject):
            lvl = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else -1
            if lvl < -1 or lvl > 9:
                lvl = -1
            nowrap = bool(a[2]) if len(a) > 2 else False
            wbits = -15 if nowrap else 15
            a[0].fields.update({
                "_level": lvl,
                "_nowrap": nowrap,
                "_wbits": wbits,
                "_co": zlib.compressobj(lvl, zlib.DEFLATED, wbits),
                "_out": bytearray(),
                "_fin_req": False,
                "_finished": False,
                "_read": 0,
                "_written": 0,
                "_needs_in": True,
            })
        return a[0] if a else None

    def _def_set_input(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            raw = _bytes_from(a[1])
            off = int(a[2]) if len(a) > 3 else 0
            ln = int(a[3]) if len(a) > 3 else len(raw) - off
            seg = raw[off:off + max(0, ln)]
            co = a[0].fields.get("_co")
            if co is not None and seg:
                a[0].fields.setdefault("_out", bytearray()).extend(co.compress(seg))
                a[0].fields["_read"] = int(a[0].fields.get("_read", 0)) + len(seg)
            a[0].fields["_needs_in"] = False
        return None

    def _def_deflate(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2 or not isinstance(a[1], JArray):
            return 0
        o = a[0]
        dst = a[1]
        off = int(a[2]) if len(a) > 3 else 0
        ln = int(a[3]) if len(a) > 3 else len(dst.data) - off
        flush_mode = int(a[4]) if len(a) > 4 else 0
        ln = max(0, min(ln, len(dst.data) - off))
        co = o.fields.get("_co")
        buf = o.fields.setdefault("_out", bytearray())
        if co is not None:
            if o.fields.get("_fin_req") and not o.fields.get("_finished"):
                buf.extend(co.flush(zlib.Z_FINISH))
                o.fields["_finished"] = True
            elif flush_mode in (2, 3):  # SYNC_FLUSH / FULL_FLUSH
                buf.extend(co.flush(zlib.Z_SYNC_FLUSH if flush_mode == 2 else zlib.Z_FULL_FLUSH))
        take = min(ln, len(buf))
        for i in range(take):
            dst.data[off + i] = buf[i]
        del buf[:take]
        o.fields["_written"] = int(o.fields.get("_written", 0)) + take
        if not buf:
            o.fields["_needs_in"] = True
        return take

    _add(DEF, "<init>", "()V", _def_init)
    _add(DEF, "<init>", "(I)V", _def_init)
    _add(DEF, "<init>", "(IZ)V", _def_init)
    _add(DEF, "setInput", "([B)V", _def_set_input)
    _add(DEF, "setInput", "([BII)V", _def_set_input)
    _add(DEF, "finish", "()V", lambda a: a[0].fields.__setitem__("_fin_req", True) if a and isinstance(a[0], JObject) else None)
    _add(DEF, "finished", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_finished") and not a[0].fields.get("_out")) else 0)
    _add(DEF, "needsInput", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_needs_in", True)) else 0)
    _add(DEF, "deflate", "([B)I", _def_deflate)
    _add(DEF, "deflate", "([BII)I", _def_deflate)
    _add(DEF, "deflate", "([BIII)I", _def_deflate)
    _add(DEF, "getBytesRead", "()J", lambda a: int(a[0].fields.get("_read", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(DEF, "getBytesWritten", "()J", lambda a: int(a[0].fields.get("_written", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(DEF, "reset", "()V", lambda a: _def_init([a[0], a[0].fields.get("_level", -1), a[0].fields.get("_nowrap", False)]) if a and isinstance(a[0], JObject) else None)
    _add(DEF, "end", "()V", lambda a: a[0].fields.update({"_co": None, "_out": bytearray()}) if a and isinstance(a[0], JObject) else None)

    def _iis_init(a):
        if a and isinstance(a[0], JObject):
            inp = a[1] if len(a) > 1 else None
            inf = a[2] if len(a) > 2 and isinstance(a[2], JObject) else JObject(INF, {"_nowrap": False})
            raw = _read_stream_bytes(stubs, inp)
            wbits = -15 if inf.fields.get("_nowrap") else 15
            try:
                dec = zlib.decompress(raw, wbits)
            except Exception:
                try:
                    dec = zlib.decompress(raw, -15)
                except Exception:
                    dec = raw
            a[0].fields.update({"in": inp, "_inf": inf, "_dec": dec, "_pos": 0})
        return a[0] if a else None

    def _iis_read(a):
        if not a or not isinstance(a[0], JObject):
            return -1
        dec = a[0].fields.get("_dec", b"")
        pos = int(a[0].fields.get("_pos", 0))
        if len(a) == 1:
            if pos >= len(dec):
                return -1
            a[0].fields["_pos"] = pos + 1
            return dec[pos]
        dst = a[1]
        if not isinstance(dst, JArray) or pos >= len(dec):
            return -1
        off = int(a[2]) if len(a) > 3 else 0
        ln = int(a[3]) if len(a) > 3 else len(dst.data) - off
        take = min(max(0, ln), len(dst.data) - off, len(dec) - pos)
        if take <= 0:
            return -1
        for i in range(take):
            dst.data[off + i] = dec[pos + i]
        a[0].fields["_pos"] = pos + take
        return take

    _add(IIS, "<init>", "(Ljava/io/InputStream;)V", _iis_init)
    _add(IIS, "<init>", f"(Ljava/io/InputStream;{INF})V", _iis_init)
    _add(IIS, "<init>", f"(Ljava/io/InputStream;{INF}I)V", _iis_init)
    _add(IIS, "read", "()I", _iis_read)
    _add(IIS, "read", "([B)I", _iis_read)
    _add(IIS, "read", "([BII)I", _iis_read)
    _add(IIS, "available", "()I", lambda a: max(0, len(a[0].fields.get("_dec", b"")) - int(a[0].fields.get("_pos", 0))) if a and isinstance(a[0], JObject) else 0)
    _add(IIS, "close", "()V", lambda a: None)

    def _dos_init(a):
        if a and isinstance(a[0], JObject):
            out_s = a[1] if len(a) > 1 else None
            dfl = a[2] if len(a) > 2 and isinstance(a[2], JObject) else _def_init([JObject(DEF, {})])
            a[0].fields.update({"out": out_s, "_def": dfl, "_raw": bytearray(), "_done": False})
        return a[0] if a else None

    def _dos_write(a):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            buf = a[0].fields.setdefault("_raw", bytearray())
            if isinstance(a[1], int):
                buf.append(a[1] & 0xFF)
            else:
                b = _bytes_from(a[1])
                off = int(a[2]) if len(a) > 3 else 0
                ln = int(a[3]) if len(a) > 3 else len(b) - off
                buf.extend(b[off:off + max(0, ln)])
        return None

    def _dos_finish(a):
        if a and isinstance(a[0], JObject) and not a[0].fields.get("_done"):
            a[0].fields["_done"] = True
            dfl = a[0].fields.get("_def")
            lvl = int(dfl.fields.get("_level", -1)) if isinstance(dfl, JObject) else -1
            wbits = int(dfl.fields.get("_wbits", 15)) if isinstance(dfl, JObject) else 15
            co = zlib.compressobj(lvl, zlib.DEFLATED, wbits)
            comp = co.compress(bytes(a[0].fields.get("_raw", b""))) + co.flush()
            _write_stream_bytes(stubs, a[0].fields.get("out"), comp)
        return None

    _add(DOS, "<init>", "(Ljava/io/OutputStream;)V", _dos_init)
    _add(DOS, "<init>", f"(Ljava/io/OutputStream;{DEF})V", _dos_init)
    _add(DOS, "<init>", f"(Ljava/io/OutputStream;{DEF}I)V", _dos_init)
    _add(DOS, "<init>", f"(Ljava/io/OutputStream;{DEF}Z)V", _dos_init)
    _add(DOS, "write", "(I)V", _dos_write)
    _add(DOS, "write", "([B)V", _dos_write)
    _add(DOS, "write", "([BII)V", _dos_write)
    _add(DOS, "finish", "()V", _dos_finish)
    _add(DOS, "flush", "()V", lambda a: None)
    _add(DOS, "close", "()V", _dos_finish)

    def _chk_upd(a, fn):
        if a and isinstance(a[0], JObject) and len(a) > 1:
            cur = int(a[0].fields.get("_val", 1 if fn is zlib.adler32 else 0))
            if isinstance(a[1], int):
                seg = bytes([a[1] & 0xFF])
            else:
                raw = _bytes_from(a[1])
                off = int(a[2]) if len(a) > 3 else 0
                ln = int(a[3]) if len(a) > 3 else len(raw) - off
                seg = raw[off:off + max(0, ln)]
            a[0].fields["_val"] = fn(seg, cur) & 0xFFFFFFFF
        return None

    for _cc, _fn, _init_v in ((CRC, zlib.crc32, 0), (ADL, zlib.adler32, 1)):
        _add(_cc, "<init>", "()V", lambda a, iv=_init_v: a[0].fields.__setitem__("_val", iv) if a and isinstance(a[0], JObject) else None)
        _add(_cc, "update", "(I)V", lambda a, fn=_fn: _chk_upd(a, fn))
        _add(_cc, "update", "([B)V", lambda a, fn=_fn: _chk_upd(a, fn))
        _add(_cc, "update", "([BII)V", lambda a, fn=_fn: _chk_upd(a, fn))
        _add(_cc, "getValue", "()J", lambda a, iv=_init_v: int(a[0].fields.get("_val", iv)) & 0xFFFFFFFF if a and isinstance(a[0], JObject) else iv)
        _add(_cc, "reset", "()V", lambda a, iv=_init_v: a[0].fields.__setitem__("_val", iv) if a and isinstance(a[0], JObject) else None)

    _add("Ljava/util/zip/ZipEntry;", "getName", "()Ljava/lang/String;",
         lambda a: JString(a[0].fields.get("name", "")) if a and isinstance(a[0], JObject) else JString(""))

    # BigInteger & BigDecimal
    BI = "Ljava/math/BigInteger;"
    BD = "Ljava/math/BigDecimal;"
    RM = "Ljava/math/RoundingMode;"

    def _bi_val(o) -> int:
        if isinstance(o, JObject):
            return int(o.fields.get("_bi", 0))
        return int(o or 0)

    def _bi_obj(val: int) -> JObject:
        return JObject(BI, {"_bi": int(val)})

    def _bi_init_radix(a):
        if a and isinstance(a[0], JObject):
            s = (_txt(a[1]) or "0").strip() if len(a) > 1 else "0"
            radix = int(a[2]) if len(a) > 2 else 10
            try:
                a[0].fields["_bi"] = int(s, radix)
            except Exception:
                a[0].fields["_bi"] = 0
        return a[0] if a else None

    def _bi_init_sigmag(a):
        if a and isinstance(a[0], JObject) and len(a) > 2:
            sign = int(a[1] or 0)
            mag = _bytes_from(a[2])
            val = int.from_bytes(mag, "big", signed=False) if mag else 0
            a[0].fields["_bi"] = -val if sign < 0 else (0 if sign == 0 else val)
        return a[0] if a else None

    def _bi_mod_inv(x: int, m: int) -> int:
        if m <= 0:
            _raise_exc("Ljava/lang/ArithmeticException;", "BigInteger: modulus not positive")
        try:
            return pow(x, -1, m)
        except ValueError:
            _raise_exc("Ljava/lang/ArithmeticException;", "BigInteger not invertible.")
            return 0

    def _bi_tostr_radix(val: int, radix: int) -> str:
        r = max(2, min(36, int(radix)))
        if val == 0:
            return "0"
        neg = val < 0
        n = -val if neg else val
        digs = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = []
        while n:
            out.append(digs[n % r])
            n //= r
        return ("-" if neg else "") + "".join(reversed(out))

    _add(BI, "<init>", "(Ljava/lang/String;I)V", _bi_init_radix)
    _add(BI, "<init>", "(I[B)V", _bi_init_sigmag)
    _add(BI, "<init>", "(ILjava/util/Random;)V", lambda a: a[0].fields.__setitem__("_bi", stubs._next_rand_int(1 << max(1, min(64, int(a[1] if len(a) > 1 else 8))))) if a and isinstance(a[0], JObject) else None)
    _add(BI, "add", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) + _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "subtract", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) - _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "multiply", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) * _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "divide", f"({BI}){BI}", lambda a: _bi_obj(int(_bi_val(a[0]) / _bi_val(a[1])) if abs(_bi_val(a[0])) < (1 << 53) and abs(_bi_val(a[1])) < (1 << 53) else (abs(_bi_val(a[0])) // abs(_bi_val(a[1]))) * (-1 if (_bi_val(a[0]) < 0) ^ (_bi_val(a[1]) < 0) else 1)) if len(a) > 1 and _bi_val(a[1]) != 0 else _bi_obj(0))
    _add(BI, "remainder", f"({BI}){BI}", lambda a: (lambda x, y: _bi_obj(x - (abs(x) // abs(y)) * (-1 if (x < 0) ^ (y < 0) else 1) * y))(_bi_val(a[0]), _bi_val(a[1])) if len(a) > 1 and _bi_val(a[1]) != 0 else _bi_obj(0))
    _add(BI, "mod", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) % _bi_val(a[1])) if len(a) > 1 and _bi_val(a[1]) > 0 else _bi_obj(0))
    _add(BI, "modPow", f"({BI}{BI}){BI}", lambda a: _bi_obj(pow(_bi_val(a[0]), _bi_val(a[1]), _bi_val(a[2]))) if len(a) > 2 and _bi_val(a[2]) > 0 else _bi_obj(0))
    _add(BI, "modInverse", f"({BI}){BI}", lambda a: _bi_obj(_bi_mod_inv(_bi_val(a[0]), _bi_val(a[1]))) if len(a) > 1 else _bi_obj(0))
    _add(BI, "gcd", f"({BI}){BI}", lambda a: _bi_obj(math.gcd(_bi_val(a[0]), _bi_val(a[1]))) if len(a) > 1 else _bi_obj(0))
    _add(BI, "pow", f"(I){BI}", lambda a: _bi_obj(_bi_val(a[0]) ** max(0, int(a[1] if len(a) > 1 else 0))))
    _add(BI, "abs", f"(){BI}", lambda a: _bi_obj(abs(_bi_val(a[0]))))
    _add(BI, "negate", f"(){BI}", lambda a: _bi_obj(-_bi_val(a[0])))
    _add(BI, "min", f"({BI}){BI}", lambda a: _bi_obj(min(_bi_val(a[0]), _bi_val(a[1]))) if len(a) > 1 else _bi_obj(0))
    _add(BI, "max", f"({BI}){BI}", lambda a: _bi_obj(max(_bi_val(a[0]), _bi_val(a[1]))) if len(a) > 1 else _bi_obj(0))
    _add(BI, "and", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) & _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "or", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) | _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "xor", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) ^ _bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "not", f"(){BI}", lambda a: _bi_obj(~_bi_val(a[0])))
    _add(BI, "andNot", f"({BI}){BI}", lambda a: _bi_obj(_bi_val(a[0]) & ~_bi_val(a[1])) if len(a) > 1 else _bi_obj(0))
    _add(BI, "shiftLeft", f"(I){BI}", lambda a: (lambda x, n: _bi_obj(x << n if n >= 0 else x >> (-n)))(_bi_val(a[0]), int(a[1] if len(a) > 1 else 0)))
    _add(BI, "shiftRight", f"(I){BI}", lambda a: (lambda x, n: _bi_obj(x >> n if n >= 0 else x << (-n)))(_bi_val(a[0]), int(a[1] if len(a) > 1 else 0)))
    _add(BI, "testBit", "(I)Z", lambda a: 1 if (len(a) > 1 and ((_bi_val(a[0]) >> max(0, int(a[1]))) & 1)) else 0)
    _add(BI, "setBit", f"(I){BI}", lambda a: _bi_obj(_bi_val(a[0]) | (1 << max(0, int(a[1] if len(a) > 1 else 0)))))
    _add(BI, "clearBit", f"(I){BI}", lambda a: _bi_obj(_bi_val(a[0]) & ~(1 << max(0, int(a[1] if len(a) > 1 else 0)))))
    _add(BI, "bitLength", "()I", lambda a: (lambda x: x.bit_length() if x >= 0 else (-x - 1).bit_length())(_bi_val(a[0])))
    _add(BI, "signum", "()I", lambda a: (lambda x: -1 if x < 0 else (1 if x > 0 else 0))(_bi_val(a[0])))
    _add(BI, "compareTo", f"({BI})I", lambda a: (lambda x, y: -1 if x < y else (1 if x > y else 0))(_bi_val(a[0]), _bi_val(a[1])) if len(a) > 1 else 0)
    _add(BI, "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == BI and _bi_val(a[0]) == _bi_val(a[1])) else 0)
    _add(BI, "hashCode", "()I", lambda a: _s32(hash(_bi_val(a[0]))))
    _add(BI, "toString", "(I)Ljava/lang/String;", lambda a: JString(_bi_tostr_radix(_bi_val(a[0]), int(a[1] if len(a) > 1 else 10))))

    _RM_MAP = {
        "UP": ROUND_UP, 0: ROUND_UP,
        "DOWN": ROUND_DOWN, 1: ROUND_DOWN,
        "CEILING": ROUND_CEILING, 2: ROUND_CEILING,
        "FLOOR": ROUND_FLOOR, 3: ROUND_FLOOR,
        "HALF_UP": ROUND_HALF_UP, 4: ROUND_HALF_UP,
        "HALF_DOWN": ROUND_HALF_DOWN, 5: ROUND_HALF_DOWN,
        "HALF_EVEN": ROUND_HALF_EVEN, 6: ROUND_HALF_EVEN,
    }

    def _dec_val(o) -> Decimal:
        if isinstance(o, JObject) and "_dec" in o.fields:
            return o.fields["_dec"]
        try:
            return Decimal(str(_txt(o) or "0"))
        except Exception:
            return Decimal(0)

    def _rm_of(x):
        if isinstance(x, JObject):
            return _RM_MAP.get(x.fields.get("__ename"), ROUND_HALF_UP)
        if isinstance(x, int):
            return _RM_MAP.get(x, ROUND_HALF_UP)
        return ROUND_HALF_UP

    _add(BD, "<init>", f"({BI})V", lambda a: a[0].fields.__setitem__("_dec", Decimal(_bi_val(a[1]))) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(BD, "<init>", f"({BI}I)V", lambda a: a[0].fields.__setitem__("_dec", Decimal(_bi_val(a[1])) * (Decimal(10) ** (-int(a[2])))) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(BD, "setScale", f"(I{RM}){BD}", lambda a: JObject(BD, {"_dec": _dec_val(a[0]).quantize(Decimal(10) ** (-int(a[1])), rounding=_rm_of(a[2] if len(a) > 2 else None))}) if len(a) > 1 else JObject(BD, {"_dec": Decimal(0)}))
    _add(BD, "divide", f"({BD}{RM}){BD}", lambda a: JObject(BD, {"_dec": (_dec_val(a[0]) / (_dec_val(a[1]) or Decimal(1))).quantize(Decimal(10) ** min(0, _dec_val(a[0]).as_tuple().exponent if isinstance(_dec_val(a[0]).as_tuple().exponent, int) else 0), rounding=_rm_of(a[2] if len(a) > 2 else None))}) if len(a) > 1 else JObject(BD, {"_dec": Decimal(0)}))
    _add(BD, "divide", f"({BD}I{RM}){BD}", lambda a: JObject(BD, {"_dec": (_dec_val(a[0]) / (_dec_val(a[1]) or Decimal(1))).quantize(Decimal(10) ** (-int(a[2])), rounding=_rm_of(a[3] if len(a) > 3 else None))}) if len(a) > 2 else JObject(BD, {"_dec": Decimal(0)}))
    _add(BD, "divide", f"({BD}I){BD}", lambda a: JObject(BD, {"_dec": (_dec_val(a[0]) / (_dec_val(a[1]) or Decimal(1))).quantize(Decimal(10) ** min(0, _dec_val(a[0]).as_tuple().exponent if isinstance(_dec_val(a[0]).as_tuple().exponent, int) else 0), rounding=_rm_of(a[2] if len(a) > 2 else 4))}) if len(a) > 1 else JObject(BD, {"_dec": Decimal(0)}))
    _add(BD, "divide", f"({BD}Ljava/math/MathContext;){BD}", lambda a: JObject(BD, {"_dec": _dec_val(a[0]) / (_dec_val(a[1]) or Decimal(1))}) if len(a) > 1 else JObject(BD, {"_dec": Decimal(0)}))
    _add(BD, "scale", "()I", lambda a: max(0, -int(_dec_val(a[0]).as_tuple().exponent)) if a and isinstance(_dec_val(a[0]).as_tuple().exponent, int) else 0)
    _add(BD, "toBigInteger", f"(){BI}", lambda a: _bi_obj(int(_dec_val(a[0]))))
    _add(BD, "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == BD and _dec_val(a[0]) == _dec_val(a[1])) else 0)
    _add(RM, "valueOf", f"(I){RM}", lambda a: (lambda idx: JObject(RM, {"__ename": ("UP", "DOWN", "CEILING", "FLOOR", "HALF_UP", "HALF_DOWN", "HALF_EVEN", "UNNECESSARY")[max(0, min(7, idx))], "__ordinal": max(0, min(7, idx))}))(int(a[0]) if a else 4))


# ---------------------------------------------------------------------------
# 6. android.os.* (Parcel, Bundle, Intent, Handler, Looper, Message, Binder)
# ---------------------------------------------------------------------------

def _install_os_and_ipc(stubs, _add, R, RB) -> None:
    PCL = "Landroid/os/Parcel;"
    BND = "Landroid/os/Bundle;"
    BBND = "Landroid/os/BaseBundle;"
    PBND = "Landroid/os/PersistableBundle;"
    IT = "Landroid/content/Intent;"
    IB = "Landroid/os/IBinder;"
    HND = "Landroid/os/Handler;"
    MSG = "Landroid/os/Message;"
    LPR = "Landroid/os/Looper;"

    def _p_new():
        return JObject(PCL, {"_buf": bytearray(), "_pos": 0, "_refs": []})

    def _p_buf(p) -> bytearray:
        if isinstance(p, JObject):
            b = p.fields.get("_buf")
            if not isinstance(b, bytearray):
                b = bytearray(b or b"")
                p.fields["_buf"] = b
            return b
        return bytearray()

    def _p_pos(p) -> int:
        return max(0, int(p.fields.get("_pos", 0))) if isinstance(p, JObject) else 0

    def _p_wbytes(p, raw: bytes) -> None:
        if not isinstance(p, JObject):
            return
        b = _p_buf(p)
        pos = _p_pos(p)
        end = pos + len(raw)
        if end > len(b):
            b.extend(b"\x00" * (end - len(b)))
        b[pos:end] = raw
        p.fields["_pos"] = end

    def _p_rbytes(p, n: int) -> bytes:
        if not isinstance(p, JObject) or n <= 0:
            return b""
        b = _p_buf(p)
        pos = _p_pos(p)
        chunk = bytes(b[pos:pos + n])
        p.fields["_pos"] = min(len(b), pos + len(chunk))
        if len(chunk) < n:
            chunk = chunk + b"\x00" * (n - len(chunk))
        return chunk

    def _p_wi(p, v: int):
        _p_wbytes(p, struct.pack("<i", _s32(v)))

    def _p_ri(p) -> int:
        return struct.unpack("<i", _p_rbytes(p, 4))[0]

    def _p_wl(p, v: int):
        _p_wbytes(p, struct.pack("<q", _s64(v)))

    def _p_rl(p) -> int:
        return struct.unpack("<q", _p_rbytes(p, 8))[0]

    def _p_wf(p, v: float):
        _p_wbytes(p, struct.pack("<f", _as_f32(v)))

    def _p_rf(p) -> float:
        return struct.unpack("<f", _p_rbytes(p, 4))[0]

    def _p_wd(p, v: float):
        _p_wbytes(p, struct.pack("<d", _as_f64(v)))

    def _p_rd(p) -> float:
        return struct.unpack("<d", _p_rbytes(p, 8))[0]

    def _p_wb(p, v: int):
        _p_wbytes(p, struct.pack("<b", _s8(v)))

    def _p_rb(p) -> int:
        return struct.unpack("<b", _p_rbytes(p, 1))[0]

    def _p_ws(p, s):
        if _is_null(s):
            _p_wi(p, -1)
        else:
            raw = (_txt(s) or "").encode("utf-8", "replace")
            _p_wi(p, len(raw))
            _p_wbytes(p, raw)

    def _p_rs(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JString(_p_rbytes(p, ln).decode("utf-8", "replace"))

    def _p_add_ref(p, obj) -> int:
        refs = p.fields.setdefault("_refs", [])
        refs.append(obj)
        return len(refs) - 1

    def _p_get_ref(p, idx: int):
        refs = p.fields.get("_refs", []) if isinstance(p, JObject) else []
        return refs[idx] if 0 <= idx < len(refs) else None

    def _p_wval(p, v):
        if _is_null(v):
            _p_wb(p, 0)
        elif isinstance(v, (JString, str)):
            _p_wb(p, 1)
            _p_ws(p, v)
        elif isinstance(v, bool):
            _p_wb(p, 4)
            _p_wb(p, 1 if v else 0)
        elif isinstance(v, int):
            if -0x80000000 <= v <= 0x7FFFFFFF:
                _p_wb(p, 2)
                _p_wi(p, v)
            else:
                _p_wb(p, 3)
                _p_wl(p, v)
        elif isinstance(v, float):
            _p_wb(p, 6)
            _p_wd(p, v)
        elif isinstance(v, JArray):
            if v.eltype == "[B":
                _p_wb(p, 10)
                _p_wba(p, v)
            elif v.eltype == "[I":
                _p_wb(p, 11)
                _p_wia(p, v)
            else:
                _p_wb(p, 12)
                _p_wsa(p, v)
        elif isinstance(v, JObject):
            if v.cls == "Ljava/lang/Integer;":
                _p_wb(p, 2); _p_wi(p, int(v.fields.get("value", 0)))
            elif v.cls == "Ljava/lang/Long;":
                _p_wb(p, 3); _p_wl(p, int(v.fields.get("value", 0)))
            elif v.cls == "Ljava/lang/Boolean;":
                _p_wb(p, 4); _p_wb(p, 1 if v.fields.get("value") else 0)
            elif v.cls == "Ljava/lang/Float;":
                _p_wb(p, 5); _p_wf(p, float(v.fields.get("value", 0.0)))
            elif v.cls == "Ljava/lang/Double;":
                _p_wb(p, 6); _p_wd(p, float(v.fields.get("value", 0.0)))
            elif v.cls in (BND, BBND, PBND):
                _p_wb(p, 7); _p_wbnd(p, v)
            elif "List" in v.cls:
                _p_wb(p, 8); _p_wlist(p, v)
            elif "Map" in v.cls:
                _p_wb(p, 9); _p_wmap(p, v)
            else:
                _p_wb(p, 13); _p_wi(p, _p_add_ref(p, v))
        else:
            _p_wb(p, 0)

    def _p_rval(p):
        tag = _p_rb(p)
        if tag == 0:
            return None
        if tag == 1:
            return _p_rs(p)
        if tag == 2:
            return _p_ri(p)
        if tag == 3:
            return _p_rl(p)
        if tag == 4:
            return 1 if _p_rb(p) else 0
        if tag == 5:
            return _p_rf(p)
        if tag == 6:
            return _p_rd(p)
        if tag == 7:
            return _p_rbnd(p)
        if tag == 8:
            return _p_rlist(p)
        if tag == 9:
            return _p_rmap_new(p)
        if tag == 10:
            return _p_cba(p)
        if tag == 11:
            return _p_cia(p)
        if tag == 12:
            return _p_csa(p)
        if tag == 13:
            return _p_get_ref(p, _p_ri(p))
        return None

    def _p_wba(p, arr, off=0, cnt=None):
        if _is_null(arr):
            _p_wi(p, -1)
        else:
            raw = _bytes_from(arr)
            if cnt is not None:
                raw = raw[max(0, off):max(0, off) + max(0, cnt)]
            _p_wi(p, len(raw))
            _p_wbytes(p, raw)

    def _p_cba(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JArray("[B", list(_p_rbytes(p, ln)))

    def _p_rba(p, dst):
        ln = _p_ri(p)
        if ln >= 0 and isinstance(dst, JArray):
            raw = _p_rbytes(p, ln)
            for i in range(min(len(dst.data), len(raw))):
                dst.data[i] = raw[i]

    def _p_wia(p, arr):
        if _is_null(arr) or not isinstance(arr, JArray):
            _p_wi(p, -1)
        else:
            _p_wi(p, len(arr.data))
            for x in arr.data:
                _p_wi(p, int(x or 0))

    def _p_cia(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JArray("[I", [_p_ri(p) for _ in range(ln)])

    def _p_ria(p, dst):
        ln = _p_ri(p)
        if ln >= 0 and isinstance(dst, JArray):
            vals = [_p_ri(p) for _ in range(ln)]
            for i in range(min(len(dst.data), len(vals))):
                dst.data[i] = vals[i]

    def _p_wsa(p, arr):
        if _is_null(arr) or not isinstance(arr, JArray):
            _p_wi(p, -1)
        else:
            _p_wi(p, len(arr.data))
            for x in arr.data:
                _p_ws(p, x)

    def _p_csa(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JArray("[Ljava/lang/String;", [_p_rs(p) for _ in range(ln)])

    def _p_wlist(p, lst):
        if _is_null(lst):
            _p_wi(p, -1)
        else:
            items = stubs._extract_coll_items(lst)
            _p_wi(p, len(items))
            for it in items:
                _p_wval(p, it)

    def _p_rlist(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JObject("Ljava/util/ArrayList;", {"__items": [_p_rval(p) for _ in range(ln)]})

    def _p_wslist(p, lst):
        if _is_null(lst):
            _p_wi(p, -1)
        else:
            items = stubs._extract_coll_items(lst)
            _p_wi(p, len(items))
            for it in items:
                _p_ws(p, it)

    def _p_cslist(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JObject("Ljava/util/ArrayList;", {"__items": [_p_rs(p) for _ in range(ln)]})

    def _p_wmap(p, mp):
        if _is_null(mp) or not isinstance(mp, JObject):
            _p_wi(p, -1)
        else:
            m = mp.fields.get("map", {})
            _p_wi(p, len(m))
            for k, v in m.items():
                _p_wval(p, JString(k) if isinstance(k, str) else k)
                _p_wval(p, v)

    def _p_rmap_new(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        m = {}
        for _ in range(ln):
            k = _p_rval(p)
            v = _p_rval(p)
            m[stubs._mkey(k)] = v
        return JObject("Ljava/util/HashMap;", {"map": m})

    def _p_wbnd(p, b):
        if _is_null(b) or not isinstance(b, JObject):
            _p_wi(p, -1)
        else:
            m = _bundle_map(b)
            _p_wi(p, len(m))
            for k, v in m.items():
                _p_ws(p, str(k))
                _p_wval(p, v)

    def _p_rbnd(p):
        ln = _p_ri(p)
        if ln < 0:
            return None
        m = {}
        for _ in range(ln):
            k = _txt(_p_rs(p)) or ""
            m[k] = _p_rval(p)
        return JObject(BND, {"map": m, "$map": m})

    def _p_wparc(p, obj, flags=0):
        if _is_null(obj) or not isinstance(obj, JObject):
            _p_wb(p, 0)
        else:
            _p_wb(p, 1)
            _p_ws(p, obj.cls)
            _p_wi(p, _p_add_ref(p, obj))

    def _p_rparc(p):
        if _p_rb(p) == 0:
            return None
        _cls = _p_rs(p)
        return _p_get_ref(p, _p_ri(p))

    def _p_wtyped_obj(p, obj, flags=0):
        if _is_null(obj) or not isinstance(obj, JObject):
            _p_wb(p, 0)
            return
        vm = getattr(stubs, "vm", None)
        if vm is not None and vm._resolve_method(obj.cls, obj.cls, "writeToParcel", f"({PCL}I)V") is not None:
            _p_wb(p, 1)
            vm.invoke_virtual(obj, "writeToParcel", f"({PCL}I)V", (obj, p, int(flags)))
        else:
            _p_wb(p, 2)
            _p_wi(p, _p_add_ref(p, obj))

    def _p_rtyped_obj(p, creator):
        tag = _p_rb(p)
        if tag == 0:
            return None
        if tag == 2:
            return _p_get_ref(p, _p_ri(p))
        if isinstance(creator, JObject):
            ok, obj = _invoke_vm_or_stub(stubs, creator, "Landroid/os/Parcelable$Creator;", "createFromParcel", f"({PCL})Ljava/lang/Object;", [p])
            if ok:
                return obj
        return None

    def _p_wtyped_arr(p, arr, flags=0):
        if _is_null(arr) or not isinstance(arr, JArray):
            _p_wi(p, -1)
        else:
            _p_wi(p, len(arr.data))
            for el in arr.data:
                _p_wtyped_obj(p, el, flags)

    def _p_ctyped_arr(p, creator):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JArray("[Ljava/lang/Object;", [_p_rtyped_obj(p, creator) for _ in range(ln)])

    def _p_wtyped_list(p, lst, flags=0):
        if _is_null(lst):
            _p_wi(p, -1)
        else:
            items = stubs._extract_coll_items(lst)
            _p_wi(p, len(items))
            for el in items:
                _p_wtyped_obj(p, el, flags)

    def _p_ctyped_list(p, creator):
        ln = _p_ri(p)
        if ln < 0:
            return None
        return JObject("Ljava/util/ArrayList;", {"__items": [_p_rtyped_obj(p, creator) for _ in range(ln)]})

    _add(PCL, "obtain", f"(){PCL}", lambda a: _p_new())
    _add(PCL, "obtain", f"({PCL}){PCL}", lambda a: JObject(PCL, {"_buf": bytearray(_p_buf(a[0])), "_pos": 0, "_refs": list(a[0].fields.get("_refs", []))}) if a and isinstance(a[0], JObject) else _p_new())
    _add(PCL, "recycle", "()V", lambda a: a[0].fields.update({"_buf": bytearray(), "_pos": 0, "_refs": []}) if a and isinstance(a[0], JObject) else None)
    _add(PCL, "dataPosition", "()I", lambda a: _p_pos(a[0]) if a else 0)
    _add(PCL, "setDataPosition", "(I)V", lambda a: a[0].fields.__setitem__("_pos", max(0, int(a[1] if len(a) > 1 else 0))) if a and isinstance(a[0], JObject) else None)
    _add(PCL, "dataSize", "()I", lambda a: len(_p_buf(a[0])) if a else 0)
    _add(PCL, "dataAvail", "()I", lambda a: max(0, len(_p_buf(a[0])) - _p_pos(a[0])) if a else 0)
    _add(PCL, "dataCapacity", "()I", lambda a: max(len(_p_buf(a[0])), _p_pos(a[0])) if a else 0)
    _add(PCL, "marshall", "()[B", lambda a: JArray("[B", list(_p_buf(a[0]))) if a else JArray("[B", []))
    _add(PCL, "unmarshall", "([BII)V", lambda a: a[0].fields.update({"_buf": bytearray(_bytes_from(a[1])[int(a[2]):int(a[2]) + int(a[3])]), "_pos": 0}) if len(a) > 3 and isinstance(a[0], JObject) else None)
    _add(PCL, "writeInterfaceToken", "(Ljava/lang/String;)V", lambda a: _p_ws(a[0], a[1] if len(a) > 1 else "") if a else None)
    _add(PCL, "enforceInterface", "(Ljava/lang/String;)V", lambda a: _p_rs(a[0]) if a else None)
    _add(PCL, "writeNoException", "()V", lambda a: _p_wi(a[0], 0) if a else None)
    _add(PCL, "readException", "()V", lambda a: _p_ri(a[0]) if a else None)
    _add(PCL, "writeInt", "(I)V", lambda a: _p_wi(a[0], a[1] if len(a) > 1 else 0))
    _add(PCL, "readInt", "()I", lambda a: _p_ri(a[0]))
    _add(PCL, "writeLong", "(J)V", lambda a: _p_wl(a[0], a[1] if len(a) > 1 else 0))
    _add(PCL, "readLong", "()J", lambda a: _p_rl(a[0]))
    _add(PCL, "writeFloat", "(F)V", lambda a: _p_wf(a[0], a[1] if len(a) > 1 else 0.0))
    _add(PCL, "readFloat", "()F", lambda a: _p_rf(a[0]))
    _add(PCL, "writeDouble", "(D)V", lambda a: _p_wd(a[0], a[1] if len(a) > 1 else 0.0))
    _add(PCL, "readDouble", "()D", lambda a: _p_rd(a[0]))
    _add(PCL, "writeByte", "(B)V", lambda a: _p_wb(a[0], a[1] if len(a) > 1 else 0))
    _add(PCL, "readByte", "()B", lambda a: _p_rb(a[0]))
    _add(PCL, "writeBoolean", "(Z)V", lambda a: _p_wi(a[0], 1 if (len(a) > 1 and a[1]) else 0))
    _add(PCL, "readBoolean", "()Z", lambda a: 1 if _p_ri(a[0]) else 0)
    _add(PCL, "writeString", "(Ljava/lang/String;)V", lambda a: _p_ws(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readString", "()Ljava/lang/String;", lambda a: _p_rs(a[0]))
    _add(PCL, "writeByteArray", "([B)V", lambda a: _p_wba(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "writeByteArray", "([BII)V", lambda a: _p_wba(a[0], a[1] if len(a) > 1 else None, int(a[2] if len(a) > 2 else 0), int(a[3] if len(a) > 3 else 0)))
    _add(PCL, "readByteArray", "([B)V", lambda a: _p_rba(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "createByteArray", "()[B", lambda a: _p_cba(a[0]))
    _add(PCL, "writeIntArray", "([I)V", lambda a: _p_wia(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readIntArray", "([I)V", lambda a: _p_ria(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "createIntArray", "()[I", lambda a: _p_cia(a[0]))
    _add(PCL, "writeStringArray", "([Ljava/lang/String;)V", lambda a: _p_wsa(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "createStringArray", "()[Ljava/lang/String;", lambda a: _p_csa(a[0]))
    _add(PCL, "writeStringList", "(Ljava/util/List;)V", lambda a: _p_wslist(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "createStringArrayList", "()Ljava/util/ArrayList;", lambda a: _p_cslist(a[0]))
    _add(PCL, "writeBundle", f"({BND})V", lambda a: _p_wbnd(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readBundle", f"(){BND}", lambda a: _p_rbnd(a[0]))
    _add(PCL, "readBundle", f"(Ljava/lang/ClassLoader;){BND}", lambda a: _p_rbnd(a[0]))
    _add(PCL, "writeParcelable", "(Landroid/os/Parcelable;I)V", lambda a: _p_wparc(a[0], a[1] if len(a) > 1 else None, int(a[2] if len(a) > 2 else 0)))
    _add(PCL, "readParcelable", "(Ljava/lang/ClassLoader;)Landroid/os/Parcelable;", lambda a: _p_rparc(a[0]))
    _add(PCL, "readParcelable", "(Ljava/lang/ClassLoader;Ljava/lang/Class;)Ljava/lang/Object;", lambda a: _p_rparc(a[0]))
    _add(PCL, "writeParcelableArray", "([Landroid/os/Parcelable;I)V", lambda a: _p_wtyped_arr(a[0], a[1] if len(a) > 1 else None, int(a[2] if len(a) > 2 else 0)))
    _add(PCL, "readParcelableArray", "(Ljava/lang/ClassLoader;)[Landroid/os/Parcelable;", lambda a: _p_ctyped_arr(a[0], None))
    _add(PCL, "writeTypedObject", "(Landroid/os/Parcelable;I)V", lambda a: _p_wtyped_obj(a[0], a[1] if len(a) > 1 else None, int(a[2] if len(a) > 2 else 0)))
    _add(PCL, "readTypedObject", "(Landroid/os/Parcelable$Creator;)Ljava/lang/Object;", lambda a: _p_rtyped_obj(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "writeTypedArray", "([Landroid/os/Parcelable;I)V", lambda a: _p_wtyped_arr(a[0], a[1] if len(a) > 1 else None, int(a[2] if len(a) > 2 else 0)))
    _add(PCL, "createTypedArray", "(Landroid/os/Parcelable$Creator;)[Ljava/lang/Object;", lambda a: _p_ctyped_arr(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "writeTypedList", "(Ljava/util/List;)V", lambda a: _p_wtyped_list(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "createTypedArrayList", "(Landroid/os/Parcelable$Creator;)Ljava/util/ArrayList;", lambda a: _p_ctyped_list(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "writeValue", "(Ljava/lang/Object;)V", lambda a: _p_wval(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readValue", "(Ljava/lang/ClassLoader;)Ljava/lang/Object;", lambda a: _p_rval(a[0]))
    _add(PCL, "writeList", "(Ljava/util/List;)V", lambda a: _p_wlist(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readArrayList", "(Ljava/lang/ClassLoader;)Ljava/util/ArrayList;", lambda a: _p_rlist(a[0]))
    _add(PCL, "writeMap", "(Ljava/util/Map;)V", lambda a: _p_wmap(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readMap", "(Ljava/util/Map;Ljava/lang/ClassLoader;)V", lambda a: a[1].fields.setdefault("map", {}).update((_p_rmap_new(a[0]) or JObject("Ljava/util/HashMap;", {})).fields.get("map", {})) if len(a) > 1 and isinstance(a[1], JObject) else None)
    _add(PCL, "readHashMap", "(Ljava/lang/ClassLoader;)Ljava/util/HashMap;", lambda a: _p_rmap_new(a[0]))
    _add(PCL, "writeSerializable", "(Ljava/io/Serializable;)V", lambda a: _p_wval(a[0], a[1] if len(a) > 1 else None))
    _add(PCL, "readSerializable", "()Ljava/io/Serializable;", lambda a: _p_rval(a[0]))
    _add(PCL, "writeStrongBinder", f"({IB})V", lambda a: _p_wi(a[0], _p_add_ref(a[0], a[1] if len(a) > 1 else None)))
    _add(PCL, "readStrongBinder", f"(){IB}", lambda a: _p_get_ref(a[0], _p_ri(a[0])))
    _add(PCL, "writeStrongInterface", "(Landroid/os/IInterface;)V", lambda a: _p_wi(a[0], _p_add_ref(a[0], a[1] if len(a) > 1 else None)))

    _add("Landroid/os/Parcelable$Creator;", "createFromParcel", f"({PCL})Ljava/lang/Object;", lambda a: None)
    _add("Landroid/os/Parcelable$Creator;", "newArray", "(I)[Ljava/lang/Object;", lambda a: JArray("[Ljava/lang/Object;", [None] * max(0, int(a[1] if len(a) > 1 else 0))))

    # Bundle / BaseBundle / PersistableBundle parity
    def _b_copy_init(a):
        if a and isinstance(a[0], JObject):
            m = {}
            if len(a) > 1 and isinstance(a[1], JObject):
                m.update(_bundle_map(a[1]))
            a[0].fields["map"] = m
            a[0].fields["$map"] = m
        return a[0] if a else None

    def _b_put_all(a):
        if a and isinstance(a[0], JObject) and len(a) > 1 and isinstance(a[1], JObject):
            _bundle_map(a[0]).update(_bundle_map(a[1]))
        return None

    for _bc in (BND, BBND, PBND):
        _add(_bc, "<init>", "()V", _b_copy_init)
        _add(_bc, "<init>", "(I)V", _b_copy_init)
        _add(_bc, "<init>", f"({BND})V", _b_copy_init)
        _add(_bc, "<init>", f"({PBND})V", _b_copy_init)
        _add(_bc, "<init>", "(Ljava/lang/ClassLoader;)V", _b_copy_init)
        _add(_bc, "putAll", f"({BND})V", _b_put_all)
        _add(_bc, "putAll", f"({PBND})V", _b_put_all)
        _add(_bc, "remove", "(Ljava/lang/String;)V", lambda a: (_bundle_map(a[0]).pop(stubs._bundle_key(a), None) if a and isinstance(a[0], JObject) else None, None)[1])
        _add(_bc, "clear", "()V", lambda a: _bundle_map(a[0]).clear() if a and isinstance(a[0], JObject) else None)
        _add(_bc, "size", "()I", lambda a: len(_bundle_map(a[0])) if a and isinstance(a[0], JObject) else 0)
        _add(_bc, "isEmpty", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and _bundle_map(a[0])) else 1)
        _add(_bc, "keySet", "()Ljava/util/Set;", lambda a: JObject("Ljava/util/LinkedHashSet;", {"__items": [JString(str(k)) for k in _bundle_map(a[0]).keys()]}) if a and isinstance(a[0], JObject) else JObject("Ljava/util/LinkedHashSet;", {"__items": []}))
        _add(_bc, "containsKey", "(Ljava/lang/String;)Z", lambda a: 1 if (a and isinstance(a[0], JObject) and stubs._bundle_key(a) in _bundle_map(a[0])) else 0)
        _add(_bc, "get", "(Ljava/lang/String;)Ljava/lang/Object;", lambda a: _bundle_map(a[0]).get(stubs._bundle_key(a)) if a and isinstance(a[0], JObject) else None)
        _add(_bc, "setClassLoader", "(Ljava/lang/ClassLoader;)V", lambda a: a[0].fields.__setitem__("_cl", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        _add(_bc, "getClassLoader", "()Ljava/lang/ClassLoader;", lambda a: a[0].fields.get("_cl") if a and isinstance(a[0], JObject) else None)
        for _pm, _pt in (
            ("String", "Ljava/lang/String;"), ("Int", "I"), ("Long", "J"), ("Boolean", "Z"),
            ("Float", "F"), ("Double", "D"), ("Short", "S"), ("Byte", "B"), ("Char", "C"),
            ("Bundle", BND), ("CharSequence", "Ljava/lang/CharSequence;"),
            ("CharSequenceArray", "[Ljava/lang/CharSequence;"),
            ("IntegerArrayList", "Ljava/util/ArrayList;"),
            ("StringArrayList", "Ljava/util/ArrayList;"),
            ("ParcelableArrayList", "Ljava/util/ArrayList;"),
            ("SparseParcelableArray", "Landroid/util/SparseArray;"),
            ("Binder", IB), ("IBinder", IB),
            ("Parcelable", "Landroid/os/Parcelable;"),
            ("ParcelableArray", "[Landroid/os/Parcelable;"),
            ("Serializable", "Ljava/io/Serializable;"),
            ("IntArray", "[I"), ("LongArray", "[J"), ("ByteArray", "[B"),
            ("FloatArray", "[F"), ("DoubleArray", "[D"), ("BooleanArray", "[Z"),
            ("StringArray", "[Ljava/lang/String;"),
        ):
            _add(_bc, f"put{_pm}", f"(Ljava/lang/String;{_pt})V", lambda a: _bundle_map(a[0]).__setitem__(stubs._bundle_key(a), a[2] if len(a) > 2 else None) if a and isinstance(a[0], JObject) else None)
            _add(_bc, f"get{_pm}", f"(Ljava/lang/String;){_pt}", lambda a, pm=_pm: (lambda val: (0 if pm in ("Int", "Long", "Short", "Byte", "Char", "Boolean") else (0.0 if pm in ("Float", "Double") else None)) if val is None else val)(_bundle_map(a[0]).get(stubs._bundle_key(a)) if a and isinstance(a[0], JObject) else None))
        _add(_bc, "getCharSequence", "(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/CharSequence;",
             lambda a: _bundle_map(a[0]).get(stubs._bundle_key(a), a[2] if len(a) > 2 else None) if a and isinstance(a[0], JObject) else (a[2] if len(a) > 2 else None))
        _add(_bc, "writeToParcel", f"({PCL}I)V", lambda a: _p_wbnd(a[1], a[0]) if len(a) > 1 else None)
        _add(_bc, "describeContents", "()I", lambda a: 0)

    # Intent gaps (sync action/$action and _data_uri/$uri)
    def _it_sync_action(o, act: str):
        if isinstance(o, JObject):
            o.fields["action"] = act
            o.fields["$action"] = act
        return o

    def _it_sync_data(o, uri_val):
        if isinstance(o, JObject):
            u = _uri_text(uri_val) if not _is_null(uri_val) else None
            o.fields["_data_uri"] = u
            o.fields["$uri"] = u
        return o

    def _it_copy_init(a):
        if a and isinstance(a[0], JObject):
            if len(a) == 2 and isinstance(a[1], JObject):
                src = a[1]
                a[0].fields.update(dict(src.fields))
                a[0].fields["_extras"] = dict(stubs._intent_extras_map(src))
                a[0].fields["categories"] = list(src.fields.get("categories") or ())
            elif len(a) == 2:
                _it_sync_action(a[0], _str_or_empty(a[1]))
            elif len(a) >= 3 and isinstance(a[1], (JString, str)) and isinstance(a[2], JObject) and "Uri" in a[2].cls:
                _it_sync_action(a[0], _str_or_empty(a[1]))
                _it_sync_data(a[0], a[2])
            elif len(a) >= 3 and isinstance(a[2], JObject) and a[2].cls == "Ljava/lang/Class;":
                pkg = getattr(stubs.profile, "package", None) or "com.example.app"
                a[0].fields["component"] = JObject("Landroid/content/ComponentName;", {"pkg": pkg, "cls": stubs._to_dotted_name(a[2].fields.get("name", ""))})
        return a[0] if a else None

    _add(IT, "<init>", "()V", lambda a: a[0] if a else None)
    _add(IT, "<init>", f"({IT})V", _it_copy_init)
    _add(IT, "<init>", "(Ljava/lang/String;)V", _it_copy_init)
    _add(IT, "<init>", "(Ljava/lang/String;Landroid/net/Uri;)V", _it_copy_init)
    _add(IT, "<init>", "(Landroid/content/Context;Ljava/lang/Class;)V", _it_copy_init)
    R(IT, "setAction", f"(Ljava/lang/String;){IT}", lambda a: _it_sync_action(a[0], _str_or_empty(a[1] if len(a) > 1 else "")) if a else None)
    _add(IT, "getAction", "()Ljava/lang/String;", lambda a: (lambda act: JString(act) if act else None)(a[0].fields.get("action") or a[0].fields.get("$action")) if a and isinstance(a[0], JObject) else None)
    R(IT, "setData", f"(Landroid/net/Uri;){IT}", lambda a: _it_sync_data(a[0], a[1] if len(a) > 1 else None) if a else None)
    _add(IT, "setDataAndType", f"(Landroid/net/Uri;Ljava/lang/String;){IT}", lambda a: (_it_sync_data(a[0], a[1] if len(a) > 1 else None), a[0].fields.__setitem__("_type", _txt(a[2]) if len(a) > 2 else None), a[0])[2] if a and isinstance(a[0], JObject) else None)
    _add(IT, "setType", f"(Ljava/lang/String;){IT}", lambda a: (a[0].fields.__setitem__("_type", _txt(a[1]) if len(a) > 1 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(IT, "getType", "()Ljava/lang/String;", lambda a: (lambda t: JString(t) if t else None)(a[0].fields.get("_type")) if a and isinstance(a[0], JObject) else None)
    _add(IT, "getScheme", "()Ljava/lang/String;", lambda a: (lambda u: JString(urlparse(u).scheme) if u and urlparse(u).scheme else None)(a[0].fields.get("_data_uri") or a[0].fields.get("$uri")) if a and isinstance(a[0], JObject) else None)
    _add(IT, "addCategory", f"(Ljava/lang/String;){IT}", lambda a: (a[0].fields.setdefault("categories", []).append(_str_or_empty(a[1] if len(a) > 1 else "")), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(IT, "getCategories", "()Ljava/util/Set;", lambda a: JObject("Ljava/util/HashSet;", {"__items": [JString(c) for c in (a[0].fields.get("categories") or ())]}) if a and isinstance(a[0], JObject) else JObject("Ljava/util/HashSet;", {"__items": []}))
    _add(IT, "setComponent", f"(Landroid/content/ComponentName;){IT}", lambda a: (a[0].fields.__setitem__("component", a[1] if len(a) > 1 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(IT, "getComponent", "()Landroid/content/ComponentName;", lambda a: a[0].fields.get("component") if a and isinstance(a[0], JObject) else None)
    _add(IT, "setClipData", "(Landroid/content/ClipData;)V", lambda a: a[0].fields.__setitem__("_clip", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(IT, "getClipData", "()Landroid/content/ClipData;", lambda a: a[0].fields.get("_clip") if a and isinstance(a[0], JObject) else None)
    _add(IT, "setSelector", f"({IT})V", lambda a: a[0].fields.__setitem__("_selector", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(IT, "setExtrasClassLoader", "(Ljava/lang/ClassLoader;)V", lambda a: None)
    _add(IT, "resolveActivity", "(Landroid/content/pm/PackageManager;)Landroid/content/ComponentName;", lambda a: a[0].fields.get("component") or JObject("Landroid/content/ComponentName;", {"pkg": getattr(stubs.profile, "package", "com.example.app"), "cls": "MainActivity"}) if a and isinstance(a[0], JObject) else None)
    _add(IT, "makeMainActivity", f"(Landroid/content/ComponentName;){IT}", lambda a: JObject(IT, {"action": "android.intent.action.MAIN", "$action": "android.intent.action.MAIN", "categories": ["android.intent.category.LAUNCHER"], "component": a[0] if a else None}))
    _add(IT, "createChooser", f"({IT}Ljava/lang/CharSequence;){IT}", lambda a: JObject(IT, {"action": "android.intent.action.CHOOSER", "$action": "android.intent.action.CHOOSER", "_extras": {"android.intent.extra.INTENT": a[0] if a else None}}))
    _add(IT, "getIntArrayExtra", "(Ljava/lang/String;)[I", lambda a: stubs._intent_extras_map(a[0]).get(stubs._bundle_key(a)) if a and isinstance(a[0], JObject) else None)
    _add(IT, "getStringArrayExtra", "(Ljava/lang/String;)[Ljava/lang/String;", lambda a: stubs._intent_extras_map(a[0]).get(stubs._bundle_key(a)) if a and isinstance(a[0], JObject) else None)
    _add(IT, "getParcelableArrayListExtra", "(Ljava/lang/String;)Ljava/util/ArrayList;", lambda a: stubs._intent_extras_map(a[0]).get(stubs._bundle_key(a)) if a and isinstance(a[0], JObject) else None)
    _add(IT, "putParcelableArrayListExtra", f"(Ljava/lang/String;Ljava/util/ArrayList;){IT}", lambda a: (stubs._intent_extras_map(a[0]).__setitem__(stubs._bundle_key(a), a[2] if len(a) > 2 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(IT, "writeToParcel", f"({PCL}I)V", lambda a: _p_wbnd(a[1], JObject(BND, {"map": stubs._intent_extras_map(a[0])})) if len(a) > 1 and isinstance(a[0], JObject) else None)

    CN = "Landroid/content/ComponentName;"
    _add(CN, "<init>", "(Ljava/lang/String;Ljava/lang/String;)V", lambda a: a[0].fields.update({"pkg": _str_or_empty(a[1] if len(a) > 1 else ""), "cls": _str_or_empty(a[2] if len(a) > 2 else "")}) if a and isinstance(a[0], JObject) else None)
    _add(CN, "<init>", "(Landroid/content/Context;Ljava/lang/Class;)V", lambda a: a[0].fields.update({"pkg": getattr(stubs.profile, "package", "com.example.app"), "cls": stubs._to_dotted_name(a[2].fields.get("name", "")) if len(a) > 2 and isinstance(a[2], JObject) else ""}) if a and isinstance(a[0], JObject) else None)
    _add(CN, "getPackageName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("pkg", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(CN, "getClassName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("cls", "")) if a and isinstance(a[0], JObject) else JString(""))
    _add(CN, "flattenToShortString", "()Ljava/lang/String;", lambda a: JString(f"{a[0].fields.get('pkg', '')}/{a[0].fields.get('cls', '')}") if a and isinstance(a[0], JObject) else JString(""))
    _add(CN, "flattenToString", "()Ljava/lang/String;", lambda a: JString(f"{a[0].fields.get('pkg', '')}/{a[0].fields.get('cls', '')}") if a and isinstance(a[0], JObject) else JString(""))
    _add(CN, "unflattenFromString", f"(Ljava/lang/String;){CN}", lambda a: (lambda p: JObject(CN, {"pkg": p[0], "cls": p[1] if len(p) > 1 else ""}))(_str_or_empty(a[0] if a else "").split("/", 1)))

    IFLT = "Landroid/content/IntentFilter;"
    _add(IFLT, "countActions", "()I", lambda a: len(a[0].fields.get("_actions", [])) if a and isinstance(a[0], JObject) else 0)
    _add(IFLT, "getAction", "(I)Ljava/lang/String;", lambda a: (lambda acts, i: JString(acts[i]) if 0 <= i < len(acts) else None)(a[0].fields.get("_actions", []), int(a[1] if len(a) > 1 else -1)) if a and isinstance(a[0], JObject) else None)
    _add(IFLT, "match", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;Landroid/net/Uri;Ljava/util/Set;Ljava/lang/String;)I",
         lambda a: 1 if (a and isinstance(a[0], JObject) and len(a) > 1 and _str_or_empty(a[1]) in a[0].fields.get("_actions", [])) else -4)

    # Handler / Looper / Message
    MDR = "Landroid/os/Handler$MessageDispatchRunnable;"

    def _msg_dispatch_run(a):
        r = a[0] if a and isinstance(a[0], JObject) else None
        if r is None:
            return None
        h = r.fields.get("_handler")
        msg = r.fields.get("_msg")
        if not isinstance(msg, JObject) or msg.fields.get("_cancelled"):
            return None
        if isinstance(h, JObject):
            msgs = h.fields.get("_msgs")
            if isinstance(msgs, list) and msg in msgs:
                msgs.remove(msg)
        cb_r = msg.fields.get("callback")
        if isinstance(cb_r, JObject):
            _invoke_vm_or_stub(stubs, cb_r, "Ljava/lang/Runnable;", "run", "()V", [])
            return None
        if isinstance(h, JObject):
            hcb = h.fields.get("_cb")
            if isinstance(hcb, JObject):
                ok, handled = _invoke_vm_or_stub(stubs, hcb, "Landroid/os/Handler$Callback;", "handleMessage", f"({MSG})Z", [msg])
                if ok and handled:
                    return None
            _invoke_vm_or_stub(stubs, h, HND, "handleMessage", f"({MSG})V", [msg])
        return None

    _add(MDR, "run", "()V", _msg_dispatch_run)

    def _hnd_init(a):
        if a and isinstance(a[0], JObject):
            for arg in a[1:]:
                if isinstance(arg, JObject):
                    if arg.cls == LPR:
                        a[0].fields["_looper"] = arg
                    else:
                        a[0].fields["_cb"] = arg
        return a[0] if a else None

    def _hnd_send_msg(h, msg, delay_ms: int = 0) -> int:
        if not isinstance(h, JObject) or not isinstance(msg, JObject):
            return 1
        d = max(0, min(60_000, int(delay_ms or 0)))
        msg.fields["target"] = h
        msg.fields["_cancelled"] = False
        h.fields.setdefault("_msgs", []).append(msg)
        dr = JObject(MDR, {"_handler": h, "_msg": msg, "_orig_delay_ms": d})
        msg.fields["_dispatch_r"] = dr
        return stubs._queue_runnable(dr, d)

    def _hnd_has_msgs(a):
        if not a or not isinstance(a[0], JObject):
            return 0
        what = int(a[1]) if len(a) > 1 else 0
        obj = a[2] if len(a) > 2 else None
        for m in a[0].fields.get("_msgs", ()):
            if isinstance(m, JObject) and not m.fields.get("_cancelled") and int(m.fields.get("what", 0)) == what:
                if len(a) <= 2 or obj is None or m.fields.get("obj") is obj:
                    return 1
        return 0

    def _hnd_rm_msgs(a):
        if not a or not isinstance(a[0], JObject):
            return None
        what = int(a[1]) if len(a) > 1 else 0
        obj = a[2] if len(a) > 2 else None
        keep = []
        for m in a[0].fields.get("_msgs", ()):
            if isinstance(m, JObject) and int(m.fields.get("what", 0)) == what and (len(a) <= 2 or obj is None or m.fields.get("obj") is obj):
                m.fields["_cancelled"] = True
                dr = m.fields.get("_dispatch_r")
                if dr in stubs.pending_runnables:
                    stubs.pending_runnables.remove(dr)
            else:
                keep.append(m)
        a[0].fields["_msgs"] = keep
        return None

    def _hnd_rm_all(a):
        if not a or not isinstance(a[0], JObject):
            return None
        tok = a[1] if len(a) > 1 else None
        keep = []
        for m in a[0].fields.get("_msgs", ()):
            if isinstance(m, JObject) and (_is_null(tok) or m.fields.get("obj") is tok):
                m.fields["_cancelled"] = True
                dr = m.fields.get("_dispatch_r")
                if dr in stubs.pending_runnables:
                    stubs.pending_runnables.remove(dr)
            else:
                keep.append(m)
        a[0].fields["_msgs"] = keep
        return None

    for _hsig in ("()V", f"({LPR})V", "(Landroid/os/Handler$Callback;)V", f"({LPR}Landroid/os/Handler$Callback;)V"):
        R(HND, "<init>", _hsig, _hnd_init)
    _add(HND, "createAsync", f"({LPR}){HND}", lambda a: JObject(HND, {"_looper": a[0] if a else stubs._looper_main, "_async": True}))
    _add(HND, "createAsync", f"({LPR}Landroid/os/Handler$Callback;){HND}", lambda a: JObject(HND, {"_looper": a[0] if a else stubs._looper_main, "_cb": a[1] if len(a) > 1 else None, "_async": True}))
    _add(HND, "postAtTime", "(Ljava/lang/Runnable;Ljava/lang/Object;J)Z",
         lambda a: stubs._queue_runnable(a[1] if len(a) > 1 else None, max(0, min(60_000, int(a[3] if len(a) > 3 else 0) - int(getattr(stubs, "_clock_ms", 0))))))
    R(HND, "sendMessage", f"({MSG})Z", lambda a: _hnd_send_msg(a[0], a[1], 0) if len(a) > 1 else 1)
    _add(HND, "sendMessageDelayed", f"({MSG}J)Z", lambda a: _hnd_send_msg(a[0], a[1], int(a[2] if len(a) > 2 else 0)) if len(a) > 1 else 1)
    _add(HND, "sendMessageAtFrontOfQueue", f"({MSG})Z", lambda a: _hnd_send_msg(a[0], a[1], 0) if len(a) > 1 else 1)
    _add(HND, "sendMessageAtTime", f"({MSG}J)Z", lambda a: _hnd_send_msg(a[0], a[1], max(0, min(60_000, int(a[2] if len(a) > 2 else 0) - int(getattr(stubs, "_clock_ms", 0))))) if len(a) > 1 else 1)
    R(HND, "sendEmptyMessage", "(I)Z", lambda a: _hnd_send_msg(a[0], JObject(MSG, {"what": int(a[1] if len(a) > 1 else 0), "target": a[0]}), 0) if a else 1)
    _add(HND, "sendEmptyMessageDelayed", "(IJ)Z", lambda a: _hnd_send_msg(a[0], JObject(MSG, {"what": int(a[1] if len(a) > 1 else 0), "target": a[0]}), int(a[2] if len(a) > 2 else 0)) if a else 1)
    R(HND, "hasMessages", "(I)Z", _hnd_has_msgs)
    _add(HND, "hasMessages", "(ILjava/lang/Object;)Z", _hnd_has_msgs)
    _add(HND, "removeMessages", "(I)V", _hnd_rm_msgs)
    _add(HND, "removeMessages", "(ILjava/lang/Object;)V", _hnd_rm_msgs)
    R(HND, "removeCallbacksAndMessages", "(Ljava/lang/Object;)V", _hnd_rm_all)
    _add(HND, "handleMessage", f"({MSG})V", lambda a: None)

    _add(MSG, "obtain", f"(){MSG}", lambda a: JObject(MSG, {}))
    _add(MSG, "obtain", f"({MSG}){MSG}", lambda a: JObject(MSG, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(MSG, {}))
    _add(MSG, "obtain", f"({HND}){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None}))
    _add(MSG, "obtain", f"({HND}Ljava/lang/Runnable;){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None, "callback": a[1] if len(a) > 1 else None}))
    _add(MSG, "obtain", f"({HND}I){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None, "what": int(a[1] or 0) if len(a) > 1 else 0}))
    _add(MSG, "obtain", f"({HND}ILjava/lang/Object;){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None, "what": int(a[1] or 0) if len(a) > 1 else 0, "obj": a[2] if len(a) > 2 else None}))
    _add(MSG, "obtain", f"({HND}III){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None, "what": int(a[1] or 0) if len(a) > 1 else 0, "arg1": int(a[2] or 0) if len(a) > 2 else 0, "arg2": int(a[3] or 0) if len(a) > 3 else 0}))
    _add(MSG, "obtain", f"({HND}IIILjava/lang/Object;){MSG}", lambda a: JObject(MSG, {"target": a[0] if a else None, "what": int(a[1] or 0) if len(a) > 1 else 0, "arg1": int(a[2] or 0) if len(a) > 2 else 0, "arg2": int(a[3] or 0) if len(a) > 3 else 0, "obj": a[4] if len(a) > 4 else None}))
    R(MSG, "sendToTarget", "()V", lambda a: (_hnd_send_msg(a[0].fields.get("target"), a[0], 0) if a and isinstance(a[0], JObject) else None, None)[1])

    # Binder / IBinder / ParcelFileDescriptor / RemoteCallbackList / ResultReceiver / PowerManager / UserManager / LocaleList / Process
    BND_CLS = "Landroid/os/Binder;"
    _add(BND_CLS, "<init>", "()V", lambda a: None)
    _add(BND_CLS, "getCallingUid", "()I", lambda a: 10123)
    _add(BND_CLS, "getCallingPid", "()I", lambda a: 12345)
    _add(BND_CLS, "flushPendingCommands", "()V", lambda a: None)
    _add(BND_CLS, "onTransact", f"(I{PCL}{PCL}I)Z", lambda a: 1)
    _add(BND_CLS, "attachInterface", "(Landroid/os/IInterface;Ljava/lang/String;)V", lambda a: a[0].fields.update({"_iface": a[1], "_desc": _str_or_empty(a[2])}) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(BND_CLS, "queryLocalInterface", "(Ljava/lang/String;)Landroid/os/IInterface;", lambda a: a[0].fields.get("_iface") if len(a) > 1 and isinstance(a[0], JObject) and a[0].fields.get("_desc") == _txt(a[1]) else None)
    _add(BND_CLS, "transact", f"(I{PCL}{PCL}I)Z", lambda a: (_invoke_vm_or_stub(stubs, a[0], BND_CLS, "onTransact", f"(I{PCL}{PCL}I)Z", list(a[1:]))[1] if a and isinstance(a[0], JObject) else 1) or 0)

    _add(IB, "queryLocalInterface", "(Ljava/lang/String;)Landroid/os/IInterface;", lambda a: a[0].fields.get("_iface") if len(a) > 1 and isinstance(a[0], JObject) and a[0].fields.get("_desc") == _txt(a[1]) else None)
    _add(IB, "transact", f"(I{PCL}{PCL}I)Z", lambda a: (_invoke_vm_or_stub(stubs, a[0], BND_CLS, "onTransact", f"(I{PCL}{PCL}I)Z", list(a[1:]))[1] if a and isinstance(a[0], JObject) else 1) or 0)

    PFD = "Landroid/os/ParcelFileDescriptor;"
    _add(PFD, "open", f"(Ljava/io/File;I){PFD}", lambda a: (lambda p: JObject(PFD, {"_fd": JObject("Ljava/io/FileDescriptor;", {"descriptor": 20, "_path": p}), "_path": p}))(a[0].fields.get("path", "") if a and isinstance(a[0], JObject) else ""))
    _add(PFD, "getFileDescriptor", "()Ljava/io/FileDescriptor;", lambda a: a[0].fields.get("_fd") or JObject("Ljava/io/FileDescriptor;", {"descriptor": 20, "_path": a[0].fields.get("_path", "")}) if a and isinstance(a[0], JObject) else JObject("Ljava/io/FileDescriptor;", {"descriptor": -1}))
    _add(PFD, "getFd", "()I", lambda a: int((a[0].fields.get("_fd").fields.get("descriptor", 20) if isinstance(a[0].fields.get("_fd"), JObject) else a[0].fields.get("fd", 20))) if a and isinstance(a[0], JObject) else -1)
    _add(PFD, "close", "()V", lambda a: None)

    RCL = "Landroid/os/RemoteCallbackList;"
    _add(RCL, "<init>", "()V", lambda a: a[0].fields.update({"_cbs": [], "_snap": []}) if a and isinstance(a[0], JObject) else None)
    _add(RCL, "register", "(Landroid/os/IInterface;Ljava/lang/Object;)Z", lambda a: (a[0].fields.setdefault("_cbs", []).append((a[1], a[2] if len(a) > 2 else None)), 1)[1] if len(a) > 1 and isinstance(a[0], JObject) else 0)
    _add(RCL, "unregister", "(Landroid/os/IInterface;)Z", lambda a: (a[0].fields.__setitem__("_cbs", [it for it in a[0].fields.get("_cbs", []) if it[0] is not a[1]]), 1)[1] if len(a) > 1 and isinstance(a[0], JObject) else 0)
    _add(RCL, "beginBroadcast", "()I", lambda a: (a[0].fields.__setitem__("_snap", list(a[0].fields.get("_cbs", []))), len(a[0].fields.get("_snap", [])))[1] if a and isinstance(a[0], JObject) else 0)
    _add(RCL, "getBroadcastItem", "(I)Landroid/os/IInterface;", lambda a: a[0].fields.get("_snap", [])[int(a[1])][0] if len(a) > 1 and isinstance(a[0], JObject) and 0 <= int(a[1]) < len(a[0].fields.get("_snap", [])) else None)
    _add(RCL, "finishBroadcast", "()V", lambda a: a[0].fields.__setitem__("_snap", []) if a and isinstance(a[0], JObject) else None)

    _add("Landroid/os/ResultReceiver;", "send", f"(I{BND})V", lambda a: (_invoke_vm_or_stub(stubs, a[0], "Landroid/os/ResultReceiver;", "onReceiveResult", f"(I{BND})V", [int(a[1] if len(a) > 1 else 0), a[2] if len(a) > 2 else None]), None)[1] if a and isinstance(a[0], JObject) else None)

    PM = "Landroid/os/PowerManager;"
    WL = "Landroid/os/PowerManager$WakeLock;"
    _add(PM, "newWakeLock", f"(ILjava/lang/String;){WL}", lambda a: JObject(WL, {"_held": 0, "_ref": True}))
    _add(WL, "acquire", "(J)V", lambda a: a[0].fields.__setitem__("_held", 1) if a and isinstance(a[0], JObject) else None)
    _add(WL, "release", "()V", lambda a: a[0].fields.__setitem__("_held", 0) if a and isinstance(a[0], JObject) else None)
    _add(WL, "setReferenceCounted", "(Z)V", lambda a: a[0].fields.__setitem__("_ref", bool(a[1] if len(a) > 1 else True)) if a and isinstance(a[0], JObject) else None)

    _add("Landroid/os/UserManager;", "isUserUnlocked", "()Z", lambda a: 1)
    _add("Landroid/os/Process;", "sendSignal", "(II)V", lambda a: None)
    _add("Landroid/os/AsyncTask;", "onPostExecute", "(Ljava/lang/Object;)V", lambda a: None)

    LL = "Landroid/os/LocaleList;"
    _def_ll = JObject(LL, {"_locales": [JObject("Ljava/util/Locale;", {"lang": "de", "country": "DE"})], "_tags": "de-DE"})
    _add(LL, "getDefault", f"(){LL}", lambda a: stubs.__dict__.get("_default_locale_list", _def_ll))
    _add(LL, "getAdjustedDefault", f"(){LL}", lambda a: stubs.__dict__.get("_default_locale_list", _def_ll))
    _add(LL, "setDefault", f"({LL})V", lambda a: stubs.__dict__.__setitem__("_default_locale_list", a[0] if a and isinstance(a[0], JObject) else _def_ll))
    _add(LL, "get", "(I)Ljava/util/Locale;", lambda a: (lambda lst, i: lst[i] if 0 <= i < len(lst) else JObject("Ljava/util/Locale;", {"lang": "en", "country": "US"}))(a[0].fields.get("_locales") or _def_ll.fields["_locales"], int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else JObject("Ljava/util/Locale;", {"lang": "en", "country": "US"}))


# ---------------------------------------------------------------------------
# 7. Android Framework (Context, PackageManager, ContentResolver, Activity, Dialog, Fragment, Log, TextUtils, Os)
# ---------------------------------------------------------------------------

def _install_android_framework(stubs, _add, R, RB) -> None:
    CTX = "Landroid/content/Context;"
    CTW = "Landroid/content/ContextWrapper;"
    ACT = "Landroid/app/Activity;"
    APP = "Landroid/app/Application;"
    SVC = "Landroid/app/Service;"
    CC = "Landroidx/core/content/ContextCompat;"

    def _pkg() -> str:
        return getattr(getattr(stubs, "profile", None), "package", None) or "com.example.app"

    def _data_dir() -> str:
        return f"/data/data/{_pkg()}"

    def _mkfile(p: str) -> JObject:
        return JObject("Ljava/io/File;", {"path": _norm_path(p)})

    def _ctx_get_str_args(a):
        rid = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
        smap = getattr(stubs, "_strmap_id", None) or getattr(stubs, "_strmap", None) or {}
        fmt = smap.get(rid, "")
        arr = a[2] if len(a) > 2 else None
        if fmt and isinstance(arr, JArray):
            try:
                vals = [stubs._obj_to_str(x) if isinstance(x, (JObject, JString)) else x for x in arr.data]
                return JString(fmt % tuple(vals))
            except Exception:
                pass
        return JString(fmt)

    def _ctx_get_svc(a):
        cls_obj = a[1] if len(a) > 1 else None
        desc = _desc_of_class(stubs, cls_obj) if isinstance(cls_obj, JObject) else None
        if desc:
            return JObject(desc, {})
        return JObject("Landroid/os/SystemService;", {})

    _SVC_NAMES = {
        "android.app.NotificationManager": "notification",
        "android.content.ClipboardManager": "clipboard",
        "android.net.ConnectivityManager": "connectivity",
        "android.net.wifi.WifiManager": "wifi",
        "android.app.ActivityManager": "activity",
        "android.view.WindowManager": "window",
        "android.view.inputmethod.InputMethodManager": "input_method",
        "android.os.PowerManager": "power",
        "android.location.LocationManager": "location",
        "android.media.AudioManager": "audio",
        "android.app.AlarmManager": "alarm",
        "android.app.UiModeManager": "uimode",
        "android.app.AppOpsManager": "appops",
        "android.os.UserManager": "user",
        "android.hardware.display.DisplayManager": "display",
        "android.app.LocaleManager": "locale",
    }

    def _ctx_svc_name(a):
        cls_obj = a[1] if len(a) > 1 else None
        nm = stubs._to_dotted_name(cls_obj.fields.get("name", "")) if isinstance(cls_obj, JObject) else ""
        return JString(_SVC_NAMES.get(nm, nm.rsplit(".", 1)[-1].lower() if nm else ""))

    def _ctx_open_out(a):
        nm = _str_or_empty(a[1] if len(a) > 1 else "f") or "f"
        mode = int(a[2]) if len(a) > 2 and isinstance(a[2], int) else 0
        files_dir = _norm_path(_data_dir() + "/files")
        _vfs_mkdir(stubs, files_dir)
        path = _norm_path(files_dir + "/" + nm.lstrip("/"))
        buf = b""
        if mode & 0x8000:
            buf = _vfs_read_bytes(stubs, path) or b""
        _vfs_write_bytes(stubs, path, buf)
        return JObject("Ljava/io/FileOutputStream;", {"path": path, "buf": bytearray(buf), "_mem": bytearray(buf)})

    def _ctx_open_in(a):
        nm = _str_or_empty(a[1] if len(a) > 1 else "f") or "f"
        path = _norm_path(_data_dir() + "/files/" + nm.lstrip("/"))
        return JObject("Ljava/io/FileInputStream;", {"path": path, "pos": 0})

    for _cc in (CTX, CTW, ACT, APP, SVC):
        _add(_cc, "openFileOutput", "(Ljava/lang/String;I)Ljava/io/FileOutputStream;", _ctx_open_out)
        _add(_cc, "openFileInput", "(Ljava/lang/String;)Ljava/io/FileInputStream;", _ctx_open_in)
        _add(_cc, "getMainLooper", "()Landroid/os/Looper;", lambda a: stubs._looper_main)
        _add(_cc, "getMainExecutor", "()Ljava/util/concurrent/Executor;", lambda a: JObject("Ljava/util/concurrent/Executor;", {"_main": True}))
        _add(_cc, "checkSelfPermission", "(Ljava/lang/String;)I", lambda a: 0)
        _add(_cc, "checkPermission", "(Ljava/lang/String;II)I", lambda a: 0)
        _add(_cc, "checkCallingOrSelfPermission", "(Ljava/lang/String;)I", lambda a: 0)
        _add(_cc, "getDataDir", "()Ljava/io/File;", lambda a: _mkfile(_data_dir()))
        _add(_cc, "getCodeCacheDir", "()Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/code_cache"))
        _add(_cc, "getNoBackupFilesDir", "()Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/no_backup"))
        _add(_cc, "getExternalFilesDir", "(Ljava/lang/String;)Ljava/io/File;", lambda a: _mkfile(f"/storage/emulated/0/Android/data/{_pkg()}/files" + (f"/{_txt(a[1])}" if len(a) > 1 and _txt(a[1]) else "")))
        _add(_cc, "getExternalFilesDirs", "(Ljava/lang/String;)[Ljava/io/File;", lambda a: JArray("[Ljava/io/File;", [_mkfile(f"/storage/emulated/0/Android/data/{_pkg()}/files")]))
        _add(_cc, "getExternalCacheDir", "()Ljava/io/File;", lambda a: _mkfile(f"/storage/emulated/0/Android/data/{_pkg()}/cache"))
        _add(_cc, "getExternalCacheDirs", "()[Ljava/io/File;", lambda a: JArray("[Ljava/io/File;", [_mkfile(f"/storage/emulated/0/Android/data/{_pkg()}/cache")]))
        _add(_cc, "getObbDir", "()Ljava/io/File;", lambda a: _mkfile(f"/storage/emulated/0/Android/obb/{_pkg()}"))
        _add(_cc, "getObbDirs", "()[Ljava/io/File;", lambda a: JArray("[Ljava/io/File;", [_mkfile(f"/storage/emulated/0/Android/obb/{_pkg()}")]))
        _add(_cc, "fileList", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(n) for n in _vfs_listdir(stubs, _data_dir() + "/files")]))
        _add(_cc, "deleteFile", "(Ljava/lang/String;)Z", lambda a: 1 if (len(a) > 1 and _vfs_delete(stubs, _norm_path(_data_dir() + "/files/" + _str_or_empty(a[1])))) else 0)
        _add(_cc, "databaseList", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(n) for n in _vfs_listdir(stubs, _data_dir() + "/databases")]))
        _add(_cc, "deleteDatabase", "(Ljava/lang/String;)Z", lambda a: 1 if (len(a) > 1 and _vfs_delete(stubs, _norm_path(_data_dir() + "/databases/" + _str_or_empty(a[1])))) else 0)
        _add(_cc, "getColor", "(I)I", lambda a: 0xFF2F6DF0)
        _add(_cc, "getColorStateList", "(I)Landroid/content/res/ColorStateList;", lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": 0xFF2F6DF0}))
        _add(_cc, "getText", "(I)Ljava/lang/CharSequence;",
             lambda a: JString((getattr(stubs, "_strmap_id", None) or getattr(stubs, "_strmap", None) or {}).get(int(a[1] if len(a) > 1 and isinstance(a[1], int) else 0), "")))
        _add(_cc, "getString", "(I[Ljava/lang/Object;)Ljava/lang/String;", _ctx_get_str_args)
        _add(_cc, "isRestricted", "()Z", lambda a: 0)
        _add(_cc, "isDeviceProtectedStorage", "()Z", lambda a: 0)
        _add(_cc, "getOpPackageName", "()Ljava/lang/String;", lambda a: JString(_pkg()))
        _add(_cc, "setTheme", "(I)V", lambda a: None)
        _add(_cc, "registerComponentCallbacks", "(Landroid/content/ComponentCallbacks;)V", lambda a: None)
        _add(_cc, "unregisterComponentCallbacks", "(Landroid/content/ComponentCallbacks;)V", lambda a: None)
        _add(_cc, "createPackageContext", f"(Ljava/lang/String;I){CTX}", lambda a: JObject(CTX, {"__pkg__": _str_or_empty(a[1] if len(a) > 1 else _pkg())}))
        _add(_cc, "startActivities", "([Landroid/content/Intent;Landroid/os/Bundle;)V", lambda a: None)
        _add(_cc, "getSystemServiceName", "(Ljava/lang/Class;)Ljava/lang/String;", _ctx_svc_name)
        R(_cc, "getSystemService", "(Ljava/lang/Class;)Ljava/lang/Object;", _ctx_get_svc)
        _add(_cc, "registerReceiver", "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;Ljava/lang/String;Landroid/os/Handler;)Landroid/content/Intent;", lambda a: None)
        _add(_cc, "registerReceiver", "(Landroid/content/BroadcastReceiver;Landroid/content/IntentFilter;Ljava/lang/String;Landroid/os/Handler;I)Landroid/content/Intent;", lambda a: None)

    R("Ljava/lang/Object;", "getSystemService", "(Ljava/lang/Class;)Ljava/lang/Object;", _ctx_get_svc) if False else None
    RB("getSystemService", "(Ljava/lang/Class;)Ljava/lang/Object;", _ctx_get_svc)
    RB("getMainLooper", "()Landroid/os/Looper;", lambda a: stubs._looper_main)
    RB("getMainExecutor", "()Ljava/util/concurrent/Executor;", lambda a: JObject("Ljava/util/concurrent/Executor;", {"_main": True}))
    RB("checkSelfPermission", "(Ljava/lang/String;)I", lambda a: 0)
    RB("getCodeCacheDir", "()Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/code_cache"))
    RB("getNoBackupFilesDir", "()Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/no_backup"))

    _add(CC, "getMainExecutor", f"({CTX})Ljava/util/concurrent/Executor;", lambda a: JObject("Ljava/util/concurrent/Executor;", {"_main": True}))
    _add(CC, "getColor", f"({CTX}I)I", lambda a: 0xFF2F6DF0)
    _add(CC, "getColorStateList", f"({CTX}I)Landroid/content/res/ColorStateList;", lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": 0xFF2F6DF0}))
    _add(CC, "getDrawable", f"({CTX}I)Landroid/graphics/drawable/Drawable;", lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {"res_id": int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0}))
    _add(CC, "getDataDir", f"({CTX})Ljava/io/File;", lambda a: _mkfile(_data_dir()))
    _add(CC, "getCodeCacheDir", f"({CTX})Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/code_cache"))
    _add(CC, "getNoBackupFilesDir", f"({CTX})Ljava/io/File;", lambda a: _mkfile(_data_dir() + "/no_backup"))
    _add(CC, "getSystemService", f"({CTX}Ljava/lang/Class;)Ljava/lang/Object;", _ctx_get_svc)

    # PackageManager / PackageInfo / ResolveInfo
    PM = "Landroid/content/pm/PackageManager;"
    APM = "Landroid/app/ApplicationPackageManager;"
    PIF = "Landroid/content/pm/PackageManager$PackageInfoFlags;"
    RI = "Landroid/content/pm/ResolveInfo;"
    PI = "Landroid/content/pm/PackageInfo;"

    _add(PIF, "of", f"(J){PIF}", lambda a: JObject(PIF, {"_flags": int(a[0] if a else 0)}))
    _add(PI, "getLongVersionCode", "()J", lambda a: int(a[0].fields.get("versionCode", 1)) if a and isinstance(a[0], JObject) else 1)
    _add(RI, "loadLabel", f"({PM})Ljava/lang/CharSequence;", lambda a: JString(_pkg()))
    _add(RI, "loadIcon", f"({PM})Landroid/graphics/drawable/Drawable;", lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {}))

    for _pmc in (PM, APM):
        _add(_pmc, "getPackagesForUid", "(I)[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(_pkg())]))
        _add(_pmc, "hasSystemFeature", "(Ljava/lang/String;)Z", lambda a: 0 if any(x in _str_or_empty(a[1] if len(a) > 1 else "").lower() for x in ("watch", "television", "leanback", "automotive")) else 1)
        _add(_pmc, "hasSystemFeature", "(Ljava/lang/String;I)Z", lambda a: 0 if any(x in _str_or_empty(a[1] if len(a) > 1 else "").lower() for x in ("watch", "television", "leanback", "automotive")) else 1)
        _add(_pmc, "queryIntentActivities", "(Landroid/content/Intent;I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
        _add(_pmc, "queryIntentActivityOptions", "(Landroid/content/ComponentName;[Landroid/content/Intent;Landroid/content/Intent;I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
        _add(_pmc, "queryIntentServices", "(Landroid/content/Intent;I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
        _add(_pmc, "queryIntentContentProviders", "(Landroid/content/Intent;I)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
        _add(_pmc, "resolveActivity", f"(Landroid/content/Intent;I){RI}", lambda a: None)
        _add(_pmc, "resolveContentProvider", "(Ljava/lang/String;I)Landroid/content/pm/ProviderInfo;", lambda a: None)
        _add(_pmc, "getApplicationLabel", "(Landroid/content/pm/ApplicationInfo;)Ljava/lang/CharSequence;", lambda a: JString(getattr(stubs.profile, "app_name", None) or _pkg()))
        _add(_pmc, "getPackageInfo", f"(Ljava/lang/String;{PIF}){PI}", lambda a: stubs.resolve(PM, "getPackageInfo", f"(Ljava/lang/String;I){PI}", [a[0], a[1] if len(a) > 1 else JString(_pkg()), 0], False)[1])
        _add(_pmc, "getProviderInfo", "(Landroid/content/ComponentName;I)Landroid/content/pm/ProviderInfo;", lambda a: JObject("Landroid/content/pm/ProviderInfo;", {"packageName": JString(_pkg())}))
        _add(_pmc, "getResourcesForApplication", "(Ljava/lang/String;)Landroid/content/res/Resources;", lambda a: JObject("Landroid/content/res/Resources;", {}))

    # ContentResolver / Settings / Cursor / DocumentsContract
    CR = "Landroid/content/ContentResolver;"
    CPC = "Landroid/content/ContentProviderClient;"
    CUR = "Landroid/database/Cursor;"

    def _cr_open_in(a):
        u = _uri_text(a[1] if len(a) > 1 else None)
        p = urlparse(u).path if u.startswith("file://") else ""
        if p:
            raw = _vfs_read_bytes(stubs, _norm_path(unquote(p)))
            if raw is not None:
                return JObject("Ljava/io/ByteArrayInputStream;", {"buf": raw, "pos": 0})
        return JObject("Ljava/io/ByteArrayInputStream;", {"buf": b"", "pos": 0})

    _add(CR, "query", f"(Landroid/net/Uri;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;){CUR}", lambda a: JObject(CUR, {"_pos": -1, "_rows": []}))
    _add(CR, "openInputStream", "(Landroid/net/Uri;)Ljava/io/InputStream;", _cr_open_in)
    _add(CR, "openFileDescriptor", "(Landroid/net/Uri;Ljava/lang/String;Landroid/os/CancellationSignal;)Landroid/os/ParcelFileDescriptor;", lambda a: None)
    _add(CR, "openAssetFileDescriptor", "(Landroid/net/Uri;Ljava/lang/String;)Landroid/content/res/AssetFileDescriptor;", lambda a: None)
    _add(CR, "registerContentObserver", "(Landroid/net/Uri;ZLandroid/database/ContentObserver;)V", lambda a: None)
    _add(CR, "unregisterContentObserver", "(Landroid/database/ContentObserver;)V", lambda a: None)
    _add(CR, "acquireUnstableContentProviderClient", f"(Landroid/net/Uri;){CPC}", lambda a: JObject(CPC, {}))
    _add(CPC, "query", f"(Landroid/net/Uri;[Ljava/lang/String;Ljava/lang/String;[Ljava/lang/String;Ljava/lang/String;Landroid/os/CancellationSignal;){CUR}", lambda a: JObject(CUR, {"_pos": -1, "_rows": []}))
    def _cp_attach_info(a):
        if not (a and isinstance(a[0], JObject)):
            return None
        prv = a[0]
        if len(a) > 1:
            prv.fields["_ctx"] = a[1]
            prv.fields["mContext"] = a[1]
        if len(a) > 2:
            prv.fields["_provider_info"] = a[2]
        if prv.fields.get("_attach_info_done"):
            return None
        prv.fields["_attach_info_done"] = True
        _invoke_vm_or_stub(stubs, prv, prv.cls, "onCreate", "()Z", [])
        return None

    def _cp_get_ctx(a):
        if a and isinstance(a[0], JObject):
            c = a[0].fields.get("_ctx") or a[0].fields.get("mContext")
            if c is not None:
                return c
        return getattr(stubs, "application", None) or getattr(stubs, "_current_activity", None)

    _add("Landroid/content/ContentProvider;", "attachInfo", f"({CTX}Landroid/content/pm/ProviderInfo;)V", _cp_attach_info)
    _add("Landroid/content/ContentProvider;", "getContext", f"(){CTX}", _cp_get_ctx)
    _add("Landroid/content/ContentProvider;", "requireContext", f"(){CTX}", _cp_get_ctx)
    _add("Landroid/content/ContentUris;", "withAppendedId", "(Landroid/net/Uri;J)Landroid/net/Uri;", lambda a: JObject("Landroid/net/Uri;", {"$uri": _uri_text(a[0]).rstrip("/") + "/" + str(int(a[1] if len(a) > 1 else 0))}))
    _add("Landroid/database/ContentObserver;", "<init>", f"({ 'Landroid/os/Handler;' })V", lambda a: None)
    _add(CUR, "registerContentObserver", "(Landroid/database/ContentObserver;)V", lambda a: None)
    _add("Landroid/provider/DocumentsContract;", "getDocumentId", "(Landroid/net/Uri;)Ljava/lang/String;", lambda a: JString(posixpath.basename(urlparse(_uri_text(a[0] if a else "")).path)))

    def _settings_store(tbl: str) -> dict:
        st = stubs.__dict__.setdefault("_android_settings", {})
        sec = st.setdefault("secure", {"android_id": str(getattr(stubs.profile, "android_id", None) or "0123456789abcdef")})
        st.setdefault("global", {"adb_enabled": 0, "development_settings_enabled": 0})
        st.setdefault("system", {})
        return st.setdefault(tbl, {})

    for _st_cls, _tbl in (
        ("Landroid/provider/Settings$Secure;", "secure"),
        ("Landroid/provider/Settings$Global;", "global"),
        ("Landroid/provider/Settings$System;", "system"),
    ):
        _add(_st_cls, "getString", f"({CR}Ljava/lang/String;)Ljava/lang/String;",
             lambda a, t=_tbl: (lambda v: JString(str(v)) if v is not None else None)(_settings_store(t).get(_str_or_empty(a[1] if len(a) > 1 else ""))))
        _add(_st_cls, "putString", f"({CR}Ljava/lang/String;Ljava/lang/String;)Z",
             lambda a, t=_tbl: (_settings_store(t).__setitem__(_str_or_empty(a[1]), _txt(a[2])), 1)[1] if len(a) > 2 else 0)
        _add(_st_cls, "getInt", f"({CR}Ljava/lang/String;I)I",
             lambda a, t=_tbl: int(_settings_store(t).get(_str_or_empty(a[1]), a[2] if len(a) > 2 else 0)) if len(a) > 1 else 0)
        _add(_st_cls, "putInt", f"({CR}Ljava/lang/String;I)Z",
             lambda a, t=_tbl: (_settings_store(t).__setitem__(_str_or_empty(a[1]), int(a[2])), 1)[1] if len(a) > 2 else 0)
        _add(_st_cls, "getFloat", f"({CR}Ljava/lang/String;F)F",
             lambda a, t=_tbl: float(_settings_store(t).get(_str_or_empty(a[1]), _unpack_ieee(a[2], True) if len(a) > 2 else 1.0)) if len(a) > 1 else 1.0)
        _add(_st_cls, "getFloat", f"({CR}Ljava/lang/String;)F",
             lambda a, t=_tbl: float(_settings_store(t).get(_str_or_empty(a[1]), 1.0)) if len(a) > 1 else 1.0)
        _add(_st_cls, "putFloat", f"({CR}Ljava/lang/String;F)Z",
             lambda a, t=_tbl: (_settings_store(t).__setitem__(_str_or_empty(a[1]), _unpack_ieee(a[2], True)), 1)[1] if len(a) > 2 else 0)
        _add(_st_cls, "getLong", f"({CR}Ljava/lang/String;J)J",
             lambda a, t=_tbl: int(_settings_store(t).get(_str_or_empty(a[1]), a[2] if len(a) > 2 else 0)) if len(a) > 1 else 0)
        _add(_st_cls, "putLong", f"({CR}Ljava/lang/String;J)Z",
             lambda a, t=_tbl: (_settings_store(t).__setitem__(_str_or_empty(a[1]), int(a[2])), 1)[1] if len(a) > 2 else 0)
        _add(_st_cls, "getUriFor", "(Ljava/lang/String;)Landroid/net/Uri;",
             lambda a, t=_tbl: JObject("Landroid/net/Uri;", {"$uri": f"content://settings/{t}/{_str_or_empty(a[0] if a else '')}"}))

    # Activity / Dialog / Fragment / Service / BroadcastReceiver / Job / PendingIntent / ClipData
    _act_noop_void = (
        ("invalidateOptionsMenu", "()V"), ("onSaveInstanceState", "(Landroid/os/Bundle;)V"),
        ("onUserInteraction", "()V"), ("recreate", "()V"), ("requestPermissions", "([Ljava/lang/String;I)V"),
        ("onActivityResult", "(IILandroid/content/Intent;)V"), ("onPause", "()V"),
        ("postponeEnterTransition", "()V"), ("startPostponedEnterTransition", "()V"),
        ("setEnterSharedElementCallback", "(Landroid/app/SharedElementCallback;)V"),
        ("setExitSharedElementCallback", "(Landroid/app/SharedElementCallback;)V"),
        ("setRequestedOrientation", "(I)V"),
        ("startIntentSenderForResult", "(Landroid/content/IntentSender;ILandroid/content/Intent;IIILandroid/os/Bundle;)V"),
    )
    for _nm, _sg in _act_noop_void:
        _add(ACT, _nm, _sg, lambda a: None)
    _add(ACT, "dispatchKeyEvent", "(Landroid/view/KeyEvent;)Z", lambda a: 0)
    _add(ACT, "dispatchKeyShortcutEvent", "(Landroid/view/KeyEvent;)Z", lambda a: 0)
    _add(ACT, "findViewById", "(I)Landroid/view/View;", lambda a: None)
    _add(ACT, "getActionBar", "()Landroid/app/ActionBar;", lambda a: None)
    _add(ACT, "getFragmentManager", "()Landroid/app/FragmentManager;", lambda a: JObject("Landroid/app/FragmentManager;", {}))
    _add(ACT, "getParentActivityIntent", "()Landroid/content/Intent;", lambda a: None)
    _add(ACT, "navigateUpTo", "(Landroid/content/Intent;)Z", lambda a: 0)
    _add(ACT, "shouldUpRecreateTask", "(Landroid/content/Intent;)Z", lambda a: 0)
    _add(ACT, "shouldShowRequestPermissionRationale", "(Ljava/lang/String;)Z", lambda a: 0)
    def _act_get_title(a):
        if a and isinstance(a[0], JObject):
            t = a[0].fields.get("_title")
            if isinstance(t, JString):
                return t
            if t is not None and _txt(t):
                return JString(_txt(t))
        smap = stubs.__dict__.get("_strmap_name") or {}
        if isinstance(smap, dict) and smap.get("app_name"):
            return JString(str(smap["app_name"]))
        return JString(_pkg())

    def _act_set_title(a):
        if a and len(a) > 1 and isinstance(a[0], JObject):
            if isinstance(a[1], int):
                sm = getattr(stubs, "_strmap_id", None) or {}
                txt = sm.get(int(a[1]), "")
                a[0].fields["_title"] = JString(str(txt))
            else:
                a[0].fields["_title"] = JString(_str_or_empty(a[1]))
        return None

    _add(ACT, "getTitle", "()Ljava/lang/CharSequence;", _act_get_title)
    _add(ACT, "setTitle", "(Ljava/lang/CharSequence;)V", _act_set_title)
    _add(ACT, "setTitle", "(I)V", _act_set_title)
    _add(ACT, "requestDragAndDropPermissions", "(Landroid/view/DragEvent;)Landroid/view/DragAndDropPermissions;", lambda a: None)
    _add(ACT, "overridePendingTransition", "(II)V", lambda a: None)
    _add(ACT, "overridePendingTransition", "(III)V", lambda a: None)
    _add("Landroid/webkit/WebStorage;", "getInstance", "()Landroid/webkit/WebStorage;",
         lambda a: stubs.__dict__.setdefault("_web_storage", JObject("Landroid/webkit/WebStorage;", {})))
    _add("Landroid/webkit/WebStorage;", "deleteAllData", "()V", lambda a: None)

    AM = "Landroid/app/ActivityManager;"
    _add(AM, "isLowRamDevice", "()Z", lambda a: 0)
    _add(AM, "getMemoryClass", "()I", lambda a: 256)
    _add(AM, "getLargeMemoryClass", "()I", lambda a: 512)
    def _am_mem_info(a):
        mi = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        if mi is not None:
            mi.fields.update({
                "availMem": 2 * 1024 * 1024 * 1024,
                "totalMem": 4 * 1024 * 1024 * 1024,
                "threshold": 256 * 1024 * 1024,
                "lowMemory": 0,
            })
        return None
    _add(AM, "getMemoryInfo", "(Landroid/app/ActivityManager$MemoryInfo;)V", _am_mem_info)
    _add(AM, "getRunningAppProcesses", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {
        "__items": [JObject("Landroid/app/ActivityManager$RunningAppProcessInfo;", {
            "pid": 12345, "uid": 10123, "importance": 100, "processName": JString(_pkg()),
        })]
    }))
    RB("getMemoryClass", "()I", lambda a: 256)
    RB("getLargeMemoryClass", "()I", lambda a: 512)
    RB("getMemoryInfo", "(Landroid/app/ActivityManager$MemoryInfo;)V", _am_mem_info)
    AO = "Landroid/app/ActivityOptions;"
    _add(AO, "makeBasic", f"(){AO}", lambda a: JObject(AO, {}))
    _add(AO, "makeCustomAnimation", f"({CTX}II){AO}", lambda a: JObject(AO, {}))
    _add(AO, "toBundle", "()Landroid/os/Bundle;", lambda a: stubs.make_bundle())
    AOM = "Landroid/app/AppOpsManager;"
    _add(AOM, "noteProxyOpNoThrow", "(Ljava/lang/String;Ljava/lang/String;)I", lambda a: 0)
    _add(AOM, "checkOpNoThrow", "(Ljava/lang/String;ILjava/lang/String;)I", lambda a: 0)
    _add(AOM, "permissionToOp", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString(_str_or_empty(a[0] if a else "")))
    _add("Landroid/app/LocaleManager;", "getApplicationLocales", "()Landroid/os/LocaleList;", lambda a: JObject("Landroid/os/LocaleList;", {}))
    _add("Landroid/app/UiModeManager;", "getCurrentModeType", "()I", lambda a: 1)
    _add("Landroid/app/UiModeManager;", "getNightMode", "()I", lambda a: 1)
    _add("Landroid/accessibilityservice/AccessibilityServiceInfo;", "getSettingsActivityName", "()Ljava/lang/String;", lambda a: JString(""))

    def _dlg_delegate(name, sig, fallback):
        def _fn(a):
            bn_fn = getattr(stubs, "_by_name", {}).get((name, sig))
            if bn_fn is not None:
                return bn_fn(a)
            return fallback(a)
        return _fn

    for _dc in ("Landroid/app/Dialog;", "Landroidx/appcompat/app/AppCompatDialog;", "Landroid/app/AlertDialog;"):
        _add(_dc, "<init>", f"({CTX})V", lambda a: a[0].fields.update({"_ctx": a[1] if len(a) > 1 else None, "_showing": 0}) if a and isinstance(a[0], JObject) else None)
        _add(_dc, "<init>", f"({CTX}I)V", lambda a: a[0].fields.update({"_ctx": a[1] if len(a) > 1 else None, "_showing": 0}) if a and isinstance(a[0], JObject) else None)
        _add(_dc, "findViewById", "(I)Landroid/view/View;", _dlg_delegate("findViewById", "(I)Landroid/view/View;", lambda a: None))
        _add(_dc, "onCreate", "(Landroid/os/Bundle;)V", lambda a: None)
        _add(_dc, "onSaveInstanceState", "()Landroid/os/Bundle;", lambda a: stubs.make_bundle())
        _add(_dc, "onRestoreInstanceState", "(Landroid/os/Bundle;)V", lambda a: None)
        _add(_dc, "onStart", "()V", lambda a: None)
        _add(_dc, "onStop", "()V", lambda a: None)
        _add(_dc, "setContentView", "(I)V", _dlg_delegate("setContentView", "(I)V", lambda a: None))
        _add(_dc, "setContentView", "(Landroid/view/View;)V", _dlg_delegate("setContentView", "(Landroid/view/View;)V", lambda a: None))
        _add(_dc, "setContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V",
             _dlg_delegate("setContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", lambda a: None))
        _add(_dc, "addContentView", "(Landroid/view/View;Landroid/view/ViewGroup$LayoutParams;)V", lambda a: None)
        _add(_dc, "show", "()V", _dlg_delegate("show", "()V", lambda a: a[0].fields.__setitem__("_showing", 1) if a and isinstance(a[0], JObject) else None))
        _add(_dc, "hide", "()V", lambda a: a[0].fields.__setitem__("_showing", 0) if a and isinstance(a[0], JObject) else None)
        _add(_dc, "dismiss", "()V", _dlg_delegate("dismiss", "()V", lambda a: a[0].fields.__setitem__("_showing", 0) if a and isinstance(a[0], JObject) else None))
        _add(_dc, "cancel", "()V", _dlg_delegate("cancel", "()V", lambda a: a[0].fields.__setitem__("_showing", 0) if a and isinstance(a[0], JObject) else None))
        _add(_dc, "isShowing", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_showing")) else 0)
        _add(_dc, "requestWindowFeature", "(I)Z", lambda a: 1)
        _add(_dc, "setCancelable", "(Z)V", lambda a: None)
        _add(_dc, "setOnShowListener", "(Landroid/content/DialogInterface$OnShowListener;)V",
             lambda a: a[0].fields.__setitem__("_on_show", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
        _add(_dc, "setOnCancelListener", "(Landroid/content/DialogInterface$OnCancelListener;)V", lambda a: None)
        _add(_dc, "setOnDismissListener", "(Landroid/content/DialogInterface$OnDismissListener;)V", lambda a: None)
        _add(_dc, "setOwnerActivity", f"({ACT})V", lambda a: a[0].fields.__setitem__("_owner", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)

    FR = "Landroid/app/Fragment;"
    FM = "Landroid/app/FragmentManager;"
    FT = "Landroid/app/FragmentTransaction;"
    for _fnm, _fsg in (("onActivityCreated", "(Landroid/os/Bundle;)V"), ("onStart", "()V"), ("onResume", "()V"), ("onPause", "()V"), ("onStop", "()V"), ("onDestroy", "()V")):
        _add(FR, _fnm, _fsg, lambda a: None)
    _add(FR, "getActivity", f"(){ACT}", lambda a: getattr(stubs, "_current_activity", None))
    _add(FM, "beginTransaction", f"(){FT}", lambda a: JObject(FT, {"_ops": [], "_fm": a[0] if a else None}))
    _add(FM, "executePendingTransactions", "()Z", lambda a: 1)
    _add(FM, "findFragmentByTag", f"(Ljava/lang/String;){FR}", lambda a: a[0].fields.get("_frags", {}).get(_str_or_empty(a[1] if len(a) > 1 else "")) if a and isinstance(a[0], JObject) else None)
    _add(FT, "add", f"({FR}Ljava/lang/String;){FT}", lambda a: (a[0].fields.setdefault("_ops", []).append((a[1], _str_or_empty(a[2] if len(a) > 2 else ""))), a[0])[1] if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(FT, "commit", "()I", lambda a: 1)

    _add(SVC, "onCreate", "()V", lambda a: None)
    _add(SVC, "onDestroy", "()V", lambda a: None)
    _add("Landroid/content/BroadcastReceiver;", "onReceive", f"({CTX}Landroid/content/Intent;)V", lambda a: None)
    JIB = "Landroid/app/job/JobInfo$Builder;"
    _add(JIB, "<init>", "(ILandroid/content/ComponentName;)V", lambda a: a[0] if a else None)
    _add(JIB, "setOverrideDeadline", f"(J){JIB}", lambda a: a[0] if a else None)
    _add(JIB, "build", "()Landroid/app/job/JobInfo;", lambda a: JObject("Landroid/app/job/JobInfo;", {}))
    _add("Landroid/app/job/JobServiceEngine;", "<init>", f"({SVC})V", lambda a: a[0] if a else None)
    _add("Landroid/app/PendingIntent;", "getActivities", f"({CTX}I[Landroid/content/Intent;ILandroid/os/Bundle;)Landroid/app/PendingIntent;", lambda a: JObject("Landroid/app/PendingIntent;", {"_pi_kind": "activities"}))
    _add("Landroid/app/PendingIntent;", "getIntentSender", "()Landroid/content/IntentSender;", lambda a: JObject("Landroid/content/IntentSender;", {}))
    _add("Landroid/app/RemoteAction;", "<init>", "(Landroid/graphics/drawable/Icon;Ljava/lang/CharSequence;Ljava/lang/CharSequence;Landroid/app/PendingIntent;)V", lambda a: a[0] if a else None)

    CD = "Landroid/content/ClipData;"
    CDI = "Landroid/content/ClipData$Item;"
    CDESC = "Landroid/content/ClipDescription;"
    _add(CDI, "<init>", "(Landroid/net/Uri;)V", lambda a: a[0].fields.__setitem__("_uri", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(CDI, "getUri", "()Landroid/net/Uri;", lambda a: a[0].fields.get("_uri") if a and isinstance(a[0], JObject) else None)
    _add(CDI, "coerceToText", f"({CTX})Ljava/lang/CharSequence;", lambda a: JString(_str_or_empty(a[0].fields.get("_text") or _uri_text(a[0].fields.get("_uri")))) if a and isinstance(a[0], JObject) else JString(""))
    _add(CDI, "coerceToStyledText", f"({CTX})Ljava/lang/CharSequence;", lambda a: JString(_str_or_empty(a[0].fields.get("_text") or _uri_text(a[0].fields.get("_uri")))) if a and isinstance(a[0], JObject) else JString(""))
    _add(CD, "<init>", f"({CDESC}{CDI})V", lambda a: a[0].fields.update({"_desc": a[1] if len(a) > 1 else None, "_items": [a[2]] if len(a) > 2 else []}) if a and isinstance(a[0], JObject) else None)
    _add(CD, "getDescription", f"(){CDESC}", lambda a: a[0].fields.get("_desc") or JObject(CDESC, {}) if a and isinstance(a[0], JObject) else JObject(CDESC, {}))
    _add(CD, "getItemCount", "()I", lambda a: len(a[0].fields.get("_items", [1])) if a and isinstance(a[0], JObject) else 0)
    _add(CDESC, "hasMimeType", "(Ljava/lang/String;)Z", lambda a: 1)

    _add("Landroid/content/DialogInterface$OnClickListener;", "onClick", "(Landroid/content/DialogInterface;I)V", lambda a: None)
    _add("Landroid/content/DialogInterface$OnKeyListener;", "onKey", "(Landroid/content/DialogInterface;ILandroid/view/KeyEvent;)Z", lambda a: 0)

    # Log & TextUtils & Os & NetworkSecurityPolicy & Connectivity/Location/Display/Audio
    LOG = "Landroid/util/Log;"
    for _lvl in ("v", "d", "i", "w", "e", "wtf"):
        _add(LOG, _lvl, "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I", lambda a: 0)
        _add(LOG, _lvl, "(Ljava/lang/String;Ljava/lang/Throwable;)I", lambda a: 0)
    _add(LOG, "println", "(ILjava/lang/String;Ljava/lang/String;)I", lambda a: 0)
    _add(LOG, "getStackTraceString", "(Ljava/lang/Throwable;)Ljava/lang/String;", lambda a: JString(stubs._obj_to_str(a[0] if a else None)))

    TU = "Landroid/text/TextUtils;"
    _add(TU, "getLayoutDirectionFromLocale", "(Ljava/util/Locale;)I", lambda a: 0)
    _add(TU, "substring", "(Ljava/lang/CharSequence;II)Ljava/lang/String;", lambda a: JString(_str_or_empty(a[0])[max(0, int(a[1])):max(0, int(a[2]))]) if len(a) > 2 else JString(""))
    _add(TU, "writeToParcel", "(Ljava/lang/CharSequence;Landroid/os/Parcel;I)V", lambda a: stubs.resolve("Landroid/os/Parcel;", "writeString", "(Ljava/lang/String;)V", [a[1], JString(_str_or_empty(a[0]))], False)[1] if len(a) > 1 else None)
    _add(TU, "concat", "([Ljava/lang/CharSequence;)Ljava/lang/CharSequence;", lambda a: JString("".join(_str_or_empty(x) for x in a[0].data)) if a and isinstance(a[0], JArray) else JString(""))
    _add(TU, "join", "(Ljava/lang/CharSequence;Ljava/lang/Iterable;)Ljava/lang/String;", lambda a: JString(_str_or_empty(a[0]).join(_str_or_empty(x) for x in stubs._extract_coll_items(a[1]))) if len(a) > 1 else JString(""))
    _add(TU, "split", "(Ljava/lang/String;Ljava/lang/String;)[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(p) for p in re.split(_str_or_empty(a[1]), _str_or_empty(a[0]))]) if len(a) > 1 and _str_or_empty(a[0]) else JArray("[Ljava/lang/String;", []))

    _add("Landroid/system/Os;", "readlink", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString(_str_or_empty(a[0] if a else "")))
    _add("Landroid/system/Os;", "stat", "(Ljava/lang/String;)Landroid/system/StructStat;", lambda a: JObject("Landroid/system/StructStat;", {"st_mode": 0o100644 if _vfs_is_file(stubs, _norm_path(_str_or_empty(a[0] if a else ""))) else 0o040755, "st_size": len(_vfs_read_bytes(stubs, _norm_path(_str_or_empty(a[0] if a else ""))) or b"")}))
    _add("Landroid/system/OsConstants;", "S_ISREG", "(I)Z", lambda a: 1 if (a and (int(a[0]) & 0o170000) == 0o100000) else 0)
    _add("Landroid/security/NetworkSecurityPolicy;", "getInstance", "()Landroid/security/NetworkSecurityPolicy;", lambda a: JObject("Landroid/security/NetworkSecurityPolicy;", {}))
    _add("Landroid/security/NetworkSecurityPolicy;", "isCleartextTrafficPermitted", "(Ljava/lang/String;)Z", lambda a: 1)
    _add("Landroid/net/TrafficStats;", "setThreadStatsTag", "(I)V", lambda a: None)
    _add("Landroid/net/TrafficStats;", "clearThreadStatsTag", "()V", lambda a: None)
    _add("Landroid/net/ConnectivityManager;", "getRestrictBackgroundStatus", "()I", lambda a: 1)
    _add("Landroid/net/ConnectivityManager;", "isActiveNetworkMetered", "()Z", lambda a: 0)
    _add("Landroid/location/LocationManager;", "isLocationEnabled", "()Z", lambda a: 1)
    _add("Landroid/hardware/display/DisplayManager;", "getDisplay", "(I)Landroid/view/Display;", lambda a: JObject("Landroid/view/Display;", {"_id": int(a[1] if len(a) > 1 else 0)}))
    _add("Landroid/hardware/display/DisplayManager;", "getDisplays", "()[Landroid/view/Display;", lambda a: JArray("[Landroid/view/Display;", [JObject("Landroid/view/Display;", {"_id": 0})]))
    _add("Landroid/media/AudioManager;", "playSoundEffect", "(I)V", lambda a: None)
    AAB = "Landroid/media/AudioAttributes$Builder;"
    _add(AAB, "<init>", "()V", lambda a: a[0] if a else None)
    _add(AAB, "setContentType", f"(I){AAB}", lambda a: a[0] if a else None)
    _add(AAB, "setLegacyStreamType", f"(I){AAB}", lambda a: a[0] if a else None)
    _add(AAB, "setUsage", f"(I){AAB}", lambda a: a[0] if a else None)
    _add(AAB, "build", "()Landroid/media/AudioAttributes;", lambda a: JObject("Landroid/media/AudioAttributes;", {}))
    _add("Landroid/media/MediaMetadataRetriever;", "release", "()V", lambda a: None)


# ---------------------------------------------------------------------------
# 8. Resources, Resources$Theme, TypedArray, AttributeSet, TypedValue, Notifications
# ---------------------------------------------------------------------------

def _install_resources_and_notifications(stubs, _add, R, RB) -> None:
    RES = "Landroid/content/res/Resources;"
    THM = "Landroid/content/res/Resources$Theme;"
    TA = "Landroid/content/res/TypedArray;"
    AS = "Landroid/util/AttributeSet;"
    TV = "Landroid/util/TypedValue;"
    AM = "Landroid/content/res/AssetManager;"
    AFD = "Landroid/content/res/AssetFileDescriptor;"

    def _sys_res():
        return stubs.__dict__.setdefault("_sys_resources", JObject(RES, {"_system": True}))

    def _res_str(rid: int) -> str:
        return str((getattr(stubs, "_strmap", None) or {}).get(rid, ""))

    def _res_get_val(a):
        rid = int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0
        tv = a[2] if len(a) > 2 and isinstance(a[2], JObject) else None
        if tv is not None:
            s = _res_str(rid)
            tv.fields["resourceId"] = rid
            tv.fields["type"] = 3 if s else 16
            tv.fields["data"] = rid
            tv.fields["string"] = JString(s)
        return None

    _add(RES, "getSystem", f"(){RES}", lambda a: _sys_res())
    _add(RES, "newTheme", f"(){THM}", lambda a: JObject(THM, {"_res": a[0] if a else _sys_res(), "_styles": []}))
    _add(RES, "getColor", f"(I{THM})I", lambda a: 0xFF2F6DF0)
    _add(RES, "getColorStateList", "(I)Landroid/content/res/ColorStateList;", lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": 0xFF2F6DF0}))
    _add(RES, "getColorStateList", f"(I{THM})Landroid/content/res/ColorStateList;", lambda a: JObject("Landroid/content/res/ColorStateList;", {"color": 0xFF2F6DF0}))
    _add(RES, "getText", "(I)Ljava/lang/CharSequence;", lambda a: JString(_res_str(int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0)))
    _add(RES, "getString", "(I[Ljava/lang/Object;)Ljava/lang/String;", lambda a: (lambda s: JString(s % tuple(stubs._obj_to_str(x) for x in a[2].data)) if (s and len(a) > 2 and isinstance(a[2], JArray)) else JString(s))(_res_str(int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0)))
    _add(RES, "getQuantityString", "(II[Ljava/lang/Object;)Ljava/lang/String;", lambda a: JString(_res_str(int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0)))
    _add(RES, "getQuantityText", "(II)Ljava/lang/CharSequence;", lambda a: JString(_res_str(int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0)))
    _add(RES, "getIntArray", "(I)[I", lambda a: JArray("[I", []))
    _add(RES, "getStringArray", "(I)[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", []))
    _add(RES, "getValue", f"(I{TV}Z)V", _res_get_val)
    _add(RES, "obtainAttributes", f"({AS}[I){TA}", lambda a: JObject(TA, {"_attrs": {}}))
    _add(RES, "obtainTypedArray", f"(I){TA}", lambda a: JObject(TA, {"_attrs": {}}))
    _add(RES, "getXml", "(I)Landroid/content/res/XmlResourceParser;", lambda a: JObject("Landroid/content/res/XmlResourceParser;", {}))
    _add(RES, "getLayout", "(I)Landroid/content/res/XmlResourceParser;", lambda a: JObject("Landroid/content/res/XmlResourceParser;", {}))
    _add(RES, "getFont", "(I)Landroid/graphics/Typeface;", lambda a: JObject("Landroid/graphics/Typeface;", {}))
    _add(RES, "openRawResourceFd", f"(I){AFD}", lambda a: JObject(AFD, {"_bytes": b""}))
    _add(RES, "updateConfiguration", "(Landroid/content/res/Configuration;Landroid/util/DisplayMetrics;)V", lambda a: None)

    def _thm_resolve_attr(a):
        if len(a) > 2 and isinstance(a[2], JObject):
            a[2].fields.setdefault("type", 16)
            a[2].fields.setdefault("data", 0)
            a[2].fields.setdefault("resourceId", int(a[1]) if isinstance(a[1], int) else 0)
        return 1

    _add(THM, "applyStyle", "(IZ)V", lambda a: a[0].fields.setdefault("_styles", []).append((int(a[1]), bool(a[2]))) if len(a) > 2 and isinstance(a[0], JObject) else None)
    _add(THM, "setTo", f"({THM})V", lambda a: a[0].fields.update(dict(a[1].fields)) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JObject) else None)
    _add(THM, "rebase", "()V", lambda a: None)
    _add(THM, "getResources", f"(){RES}", lambda a: (a[0].fields.get("_res") or _sys_res()) if a and isinstance(a[0], JObject) else _sys_res())
    _add(THM, "getDrawable", "(I)Landroid/graphics/drawable/Drawable;", lambda a: JObject("Landroid/graphics/drawable/BitmapDrawable;", {"res_id": int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0}))
    R(THM, "resolveAttribute", f"(I{TV}Z)Z", _thm_resolve_attr)

    _add(TA, "getPositionDescription", "()Ljava/lang/String;", lambda a: JString("typedarray:1"))
    _add(TA, "getTextArray", "(I)[Ljava/lang/CharSequence;", lambda a: JArray("[Ljava/lang/CharSequence;", []))
    _add(TA, "getType", "(I)I", lambda a: 0)
    _add(TA, "getValue", f"(I{TV})Z", lambda a: 0)
    _add(TA, "peekValue", f"(I){TV}", lambda a: None)
    _add(TA, "getChangingConfigurations", "()I", lambda a: 0)
    _add(TA, "getIndex", "(I)I", lambda a: 0)

    _add(AS, "getAttributeCount", "()I", lambda a: 0)
    _add(AS, "getAttributeBooleanValue", "(IZ)Z", lambda a: 1 if (len(a) > 2 and a[2]) else 0)
    _add(AS, "getAttributeNameResource", "(I)I", lambda a: 0)
    _add(AS, "getAttributeValue", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", lambda a: None)
    _add(AS, "getAttributeIntValue", "(Ljava/lang/String;Ljava/lang/String;I)I", lambda a: int(a[3]) if len(a) > 3 else 0)
    _add(AS, "getPositionDescription", "()Ljava/lang/String;", lambda a: JString("xml:1"))

    def _tv_apply_dim(a):
        unit = int(a[0]) if a else 0
        val = _as_f32(a[1]) if len(a) > 1 else 0.0
        dm = a[2] if len(a) > 2 and isinstance(a[2], JObject) else None
        dens = float(dm.fields.get("density", 2.75)) if dm else 2.75
        sdens = float(dm.fields.get("scaledDensity", dens)) if dm else 2.75
        xdpi = float(dm.fields.get("xdpi", 440.0)) if dm else 440.0
        if unit == 0:
            return float(val)
        if unit == 1:
            return float(val * dens)
        if unit == 2:
            return float(val * sdens)
        if unit == 3:
            return float(val * xdpi * (1.0 / 72.0))
        if unit == 4:
            return float(val * xdpi)
        if unit == 5:
            return float(val * xdpi * (1.0 / 25.4))
        return 0.0

    def _tv_dim_px_size(a):
        f = _as_f32(stubs._complex_to_float([a[0]]) if a else 0.0)
        dm = a[1] if len(a) > 1 and isinstance(a[1], JObject) else None
        unit = int(a[0]) & 0xF if a else 0
        px = _tv_apply_dim([unit, f, dm])
        res = int(px + 0.5) if px >= 0 else -int(-px + 0.5)
        if res != 0:
            return res
        return 0 if f == 0.0 else (1 if f > 0 else -1)

    _add(TV, "applyDimension", "(IFLandroid/util/DisplayMetrics;)F", _tv_apply_dim)
    _add(TV, "complexToDimensionPixelSize", "(ILandroid/util/DisplayMetrics;)I", _tv_dim_px_size)
    _add(TV, "complexToDimensionPixelOffset", "(ILandroid/util/DisplayMetrics;)I", lambda a: int(_tv_apply_dim([int(a[0]) & 0xF, stubs._complex_to_float([a[0]]), a[1] if len(a) > 1 else None])) if a else 0)
    _add(TV, "getDimension", "(Landroid/util/DisplayMetrics;)F", lambda a: _tv_apply_dim([int(a[0].fields.get("data", 0)) & 0xF, stubs._complex_to_float([int(a[0].fields.get("data", 0))]), a[1] if len(a) > 1 else None]) if a and isinstance(a[0], JObject) else 0.0)
    _add(TV, "getFloat", "()F", lambda a: float(a[0].fields.get("float", 0.0)) if a and isinstance(a[0], JObject) else 0.0)
    _add(TV, "getFraction", "(FF)F", lambda a: float(a[1] if len(a) > 1 else 0.0))

    _add(AM, "openFd", f"(Ljava/lang/String;){AFD}", lambda a: JObject(AFD, {"_bytes": (stubs.vfs.read(_str_or_empty(a[1] if len(a) > 1 else "")) if stubs.vfs else None) or b""}))
    _add(AFD, "createInputStream", "()Ljava/io/FileInputStream;", lambda a: JObject("Ljava/io/FileInputStream;", {"path": "", "pos": 0, "_mem": bytes(a[0].fields.get("_bytes") or b"") if a and isinstance(a[0], JObject) else b""}))
    _add(AFD, "getLength", "()J", lambda a: len(a[0].fields.get("_bytes") or b"") if a and isinstance(a[0], JObject) else 0)
    _add(AFD, "getFileDescriptor", "()Ljava/io/FileDescriptor;", lambda a: JObject("Ljava/io/FileDescriptor;", {"descriptor": 25, "_bytes": bytes(a[0].fields.get("_bytes") or b"") if a and isinstance(a[0], JObject) else b""}))
    _add("Landroid/content/res/ColorStateList;", "<init>", "([[I[I)V", lambda a: a[0].fields.__setitem__("color", int(a[2].data[0]) if len(a) > 2 and isinstance(a[2], JArray) and a[2].data else 0xFF2F6DF0) if a and isinstance(a[0], JObject) else None)
    _add("Landroid/content/res/Configuration;", "getLayoutDirection", "()I", lambda a: 0)
    _add("Landroid/content/res/Configuration;", "setLocales", "(Landroid/os/LocaleList;)V", lambda a: a[0].fields.__setitem__("_locales", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)

    XML = "Landroid/util/Xml;"
    _add(XML, "newPullParser", "()Lorg/xmlpull/v1/XmlPullParser;", lambda a: JObject("Lorg/xmlpull/v1/XmlPullParser;", {}))
    _add(XML, "newSerializer", "()Lorg/xmlpull/v1/XmlSerializer;", lambda a: JObject("Lorg/xmlpull/v1/XmlSerializer;", {}))
    _add(XML, "asAttributeSet", f"(Lorg/xmlpull/v1/XmlPullParser;){AS}", lambda a: JObject(AS, {}))

    # Notifications & Icons
    NOTIF = "Landroid/app/Notification;"
    NB = "Landroid/app/Notification$Builder;"
    NCB = "Landroidx/core/app/NotificationCompat$Builder;"
    NS = "Landroid/app/Notification$Style;"
    BTS = "Landroid/app/Notification$BigTextStyle;"
    BPS = "Landroid/app/Notification$BigPictureStyle;"
    NAB = "Landroid/app/Notification$Action$Builder;"
    NA = "Landroid/app/Notification$Action;"
    NM = "Landroid/app/NotificationManager;"
    NMC = "Landroidx/core/app/NotificationManagerCompat;"

    for _nb in (NB, NCB):
        _add(_nb, "<init>", "(Landroid/content/Context;)V", lambda a: a[0].fields.update({"channelId": "", "actions": [], "extras": stubs.make_bundle()}) if a and isinstance(a[0], JObject) else None)
        _add(_nb, "<init>", "(Landroid/content/Context;Ljava/lang/String;)V", lambda a: a[0].fields.update({"channelId": _str_or_empty(a[2] if len(a) > 2 else ""), "actions": [], "extras": stubs.make_bundle()}) if a and isinstance(a[0], JObject) else None)
        _add(_nb, "setContentTitle", f"(Ljava/lang/CharSequence;){_nb}", lambda a: (a[0].fields.__setitem__("title", _str_or_empty(a[1] if len(a) > 1 else "")), a[0])[1] if a and isinstance(a[0], JObject) else None)
        _add(_nb, "setContentText", f"(Ljava/lang/CharSequence;){_nb}", lambda a: (a[0].fields.__setitem__("text", _str_or_empty(a[1] if len(a) > 1 else "")), a[0])[1] if a and isinstance(a[0], JObject) else None)
        _add(_nb, "setExtras", f"(Landroid/os/Bundle;){_nb}", lambda a: (a[0].fields.__setitem__("extras", a[1] if len(a) > 1 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
        _add(_nb, "addAction", f"({NA}){_nb}", lambda a: (a[0].fields.setdefault("actions", []).append(a[1] if len(a) > 1 else None), a[0])[1] if a and isinstance(a[0], JObject) else None)
        for _bm, _bsig in (
            ("addPerson", "(Ljava/lang/String;)"), ("setAutoCancel", "(Z)"), ("setBadgeIconType", "(I)"),
            ("setCategory", "(Ljava/lang/String;)"), ("setColor", "(I)"), ("setContent", "(Landroid/widget/RemoteViews;)"),
            ("setContentInfo", "(Ljava/lang/CharSequence;)"), ("setContentIntent", "(Landroid/app/PendingIntent;)"),
            ("setDefaults", "(I)"), ("setDeleteIntent", "(Landroid/app/PendingIntent;)"),
            ("setFullScreenIntent", "(Landroid/app/PendingIntent;Z)"), ("setGroup", "(Ljava/lang/String;)"),
            ("setGroupAlertBehavior", "(I)"), ("setGroupSummary", "(Z)"), ("setLights", "(III)"),
            ("setLocalOnly", "(Z)"), ("setNumber", "(I)"), ("setOngoing", "(Z)"), ("setOnlyAlertOnce", "(Z)"),
            ("setPriority", "(I)"), ("setProgress", "(IIZ)"), ("setPublicVersion", f"({NOTIF})"),
            ("setRemoteInputHistory", "([Ljava/lang/CharSequence;)"), ("setShortcutId", "(Ljava/lang/String;)"),
            ("setShowWhen", "(Z)"), ("setSmallIcon", "(I)"), ("setSmallIcon", "(II)"), ("setSortKey", "(Ljava/lang/String;)"),
            ("setSound", "(Landroid/net/Uri;)"), ("setSound", "(Landroid/net/Uri;Landroid/media/AudioAttributes;)"),
            ("setStyle", f"({NS})"), ("setSubText", "(Ljava/lang/CharSequence;)"),
            ("setTicker", "(Ljava/lang/CharSequence;)"), ("setTicker", "(Ljava/lang/CharSequence;Landroid/widget/RemoteViews;)"),
            ("setTimeoutAfter", "(J)"), ("setUsesChronometer", "(Z)"), ("setVibrate", "([J)"),
            ("setVisibility", "(I)"), ("setWhen", "(J)"), ("setChannelId", "(Ljava/lang/String;)"),
        ):
            _add(_nb, _bm, _bsig + _nb, lambda a: a[0] if a else None)
        _add(_nb, "build", f"(){NOTIF}", lambda a: JObject(NOTIF, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(NOTIF, {}))

    _add(NAB, "<init>", "(Landroid/graphics/drawable/Icon;Ljava/lang/CharSequence;Landroid/app/PendingIntent;)V", lambda a: a[0] if a else None)
    _add(NAB, "addExtras", f"(Landroid/os/Bundle;){NAB}", lambda a: a[0] if a else None)
    _add(NAB, "addRemoteInput", f"(Landroid/app/RemoteInput;){NAB}", lambda a: a[0] if a else None)
    _add(NAB, "setAllowGeneratedReplies", f"(Z){NAB}", lambda a: a[0] if a else None)
    _add(NAB, "setSemanticAction", f"(I){NAB}", lambda a: a[0] if a else None)
    _add(NAB, "build", f"(){NA}", lambda a: JObject(NA, dict(a[0].fields)) if a and isinstance(a[0], JObject) else JObject(NA, {}))

    for _st in (BTS, BPS):
        _add(_st, "<init>", "()V", lambda a: a[0] if a else None)
        _add(_st, "<init>", f"({NB})V", lambda a: a[0] if a else None)
        _add(_st, "setBigContentTitle", f"(Ljava/lang/CharSequence;){_st}", lambda a: a[0] if a else None)
    _add(BTS, "bigText", f"(Ljava/lang/CharSequence;){BTS}", lambda a: a[0] if a else None)
    _add(BPS, "bigLargeIcon", f"(Landroid/graphics/Bitmap;){BPS}", lambda a: a[0] if a else None)
    _add(BPS, "bigPicture", f"(Landroid/graphics/Bitmap;){BPS}", lambda a: a[0] if a else None)

    def _nm_notify(a):
        nobj = a[-1] if a else None
        nid = a[1] if len(a) >= 3 else 0
        stubs.__dict__.setdefault("_notifications", []).append({"id": nid, "notif": nobj})
        return None

    _add(NM, "areNotificationsEnabled", "()Z", lambda a: 1 if getattr(stubs, "_notif_enabled", False) else 0)
    _add(NM, "notify", f"(I{NOTIF})V", _nm_notify)
    _add(NM, "notify", f"(Ljava/lang/String;I{NOTIF})V", _nm_notify)
    _add(NM, "cancel", "(I)V", lambda a: None)
    _add(NM, "cancel", "(Ljava/lang/String;I)V", lambda a: None)
    _add(NM, "deleteNotificationChannel", "(Ljava/lang/String;)V", lambda a: (stubs.__dict__.get("_notification_channels", {}).pop(_str_or_empty(a[1] if len(a) > 1 else ""), None), None)[1])
    _add(NM, "getNotificationChannels", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(stubs.__dict__.get("_notification_channels", {}).values())}))
    _add(NMC, "notify", f"(I{NOTIF})V", _nm_notify)
    _add(NMC, "notify", f"(Ljava/lang/String;I{NOTIF})V", _nm_notify)
    _add(NMC, "cancel", "(Ljava/lang/String;I)V", lambda a: None)
    _add(NMC, "getNotificationChannels", "()Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": list(stubs.__dict__.get("_notification_channels", {}).values())}))


# ---------------------------------------------------------------------------
# 9. ByteBuffer / FileChannel / SSL / Socket / InetAddress / Uri / Time / ICU
# ---------------------------------------------------------------------------

def _install_nio_net_ssl_time(stubs, _add, R, RB) -> None:
    BB = "Ljava/nio/ByteBuffer;"
    BUF = "Ljava/nio/Buffer;"
    MBB = "Ljava/nio/MappedByteBuffer;"
    IB = "Ljava/nio/IntBuffer;"
    CB = "Ljava/nio/CharBuffer;"
    SB = "Ljava/nio/ShortBuffer;"
    BO = "Ljava/nio/ByteOrder;"
    FC = "Ljava/nio/channels/FileChannel;"
    FLK = "Ljava/nio/channels/FileLock;"

    # Upgrade Buffer / ByteBuffer with unified bb_* helpers
    for _bc in (BUF, BB, MBB):
        _add(_bc, "capacity", "()I", lambda a: len(bb_buf(a[0])) if a else 0)
        _add(_bc, "position", "()I", lambda a: bb_pos(a[0]) if a else 0)
        _add(_bc, "position", f"(I){BUF}", lambda a: bb_set_pos(a[0], a[1] if len(a) > 1 else 0))
        _add(_bc, "limit", "()I", lambda a: bb_limit(a[0]) if a else 0)
        _add(_bc, "limit", f"(I){BUF}", lambda a: bb_set_limit(a[0], a[1] if len(a) > 1 else 0))
        _add(_bc, "remaining", "()I", lambda a: max(0, bb_limit(a[0]) - bb_pos(a[0])) if a else 0)
        _add(_bc, "hasRemaining", "()Z", lambda a: 1 if (a and bb_pos(a[0]) < bb_limit(a[0])) else 0)
        _add(_bc, "flip", f"(){BUF}", lambda a: (bb_set_limit(a[0], bb_pos(a[0])), bb_set_pos(a[0], 0))[1] if a else None)
        _add(_bc, "clear", f"(){BUF}", lambda a: (bb_set_limit(a[0], len(bb_buf(a[0]))), bb_set_pos(a[0], 0))[1] if a else None)
        _add(_bc, "rewind", f"(){BUF}", lambda a: bb_set_pos(a[0], 0) if a else None)

    def _bb_get_idx(a, fmt: str):
        if not a or not isinstance(a[0], JObject) or len(a) < 2:
            return 0
        ba = bb_buf(a[0])
        idx = max(0, int(a[1]))
        sz = struct.calcsize(fmt)
        if idx + sz > len(ba):
            return 0
        end = ">" if bb_is_big(a[0]) else "<"
        return struct.unpack(end + fmt, bytes(ba[idx:idx + sz]))[0]

    def _bb_get_arr_off(a):
        if len(a) < 4 or not isinstance(a[0], JObject) or not isinstance(a[1], JArray):
            return a[0] if a else None
        ba = bb_buf(a[0])
        pos = bb_pos(a[0])
        off = max(0, int(a[2]))
        ln = max(0, int(a[3]))
        n = min(ln, len(a[1].data) - off, max(0, bb_limit(a[0]) - pos))
        for i in range(n):
            a[1].data[off + i] = ba[pos + i]
        bb_set_pos(a[0], pos + n)
        return a[0]

    def _bb_dup(a, readonly=False):
        if not a or not isinstance(a[0], JObject):
            return bb_make()
        ba = bb_buf(a[0])
        out = bb_make(ba, limit=bb_limit(a[0]), big=bb_is_big(a[0]))
        out.fields["buf"] = ba
        out.fields["_buf"] = ba
        bb_set_pos(out, bb_pos(a[0]))
        out.fields["_ro"] = readonly
        return out

    def _bb_as_int_buf(a):
        if not a or not isinstance(a[0], JObject):
            return bb_make(cls=IB)
        pos, lim = bb_pos(a[0]), bb_limit(a[0])
        raw = bytes(bb_buf(a[0])[pos:lim])
        out = bb_make(raw, limit=len(raw) // 4, big=bb_is_big(a[0]), cls=IB)
        out.fields["_raw_bytes"] = raw
        return out

    def _ib_get_arr(a):
        if not a or not isinstance(a[0], JObject) or len(a) < 2 or not isinstance(a[1], JArray):
            return a[0] if a else None
        raw = bytes(bb_buf(a[0]))
        ipos = bb_pos(a[0])
        bpos = ipos * 4
        fmt = (">" if bb_is_big(a[0]) else "<") + "i"
        off = int(a[2]) if len(a) > 3 else 0
        cnt = int(a[3]) if len(a) > 3 else len(a[1].data)
        for i in range(max(0, cnt)):
            if bpos + 4 > len(raw) or off + i >= len(a[1].data):
                break
            a[1].data[off + i] = struct.unpack_from(fmt, raw, bpos)[0]
            bpos += 4
        bb_set_pos(a[0], bpos // 4)
        return a[0]

    def _cb_array(a):
        if not a or not isinstance(a[0], JObject):
            return JArray("[C", [])
        raw = bytes(bb_buf(a[0]))
        fmt = (">" if bb_is_big(a[0]) else "<") + "H"
        chars = [struct.unpack_from(fmt, raw, i)[0] for i in range(0, len(raw) - 1, 2)]
        return JArray("[C", chars)

    def _bb_put_arr_off(a):
        if len(a) < 2 or not isinstance(a[0], JObject):
            return a[0] if a else None
        src = a[1]
        if isinstance(src, JArray):
            off = int(a[2]) if len(a) > 3 else 0
            cnt = int(a[3]) if len(a) > 3 else len(src.data)
            chunk = bytes(int(x) & 0xFF for x in src.data[off:off + max(0, cnt)])
        elif isinstance(src, JObject):
            spos, slim = bb_pos(src), bb_limit(src)
            chunk = bytes(bb_buf(src)[spos:slim])
            bb_set_pos(src, spos + len(chunk))
        else:
            chunk = b""
        ba = bb_buf(a[0])
        pos = bb_pos(a[0])
        end = pos + len(chunk)
        if end > len(ba):
            ba.extend(b"\x00" * (end - len(ba)))
        ba[pos:end] = chunk
        bb_set_pos(a[0], end)
        bb_set_limit(a[0], max(bb_limit(a[0]), end))
        return a[0]

    _add(BB, "put", f"([BII){BB}", _bb_put_arr_off)
    _add(BB, "put", f"({BB}){BB}", _bb_put_arr_off)
    _add(BB, "get", "(I)B", lambda a: _bb_get_idx(a, "b"))
    _add(BB, "getShort", "(I)S", lambda a: _bb_get_idx(a, "h"))
    _add(BB, "getInt", "(I)I", lambda a: _bb_get_idx(a, "i"))
    _add(BB, "getLong", "(I)J", lambda a: _bb_get_idx(a, "q"))
    _add(BB, "getFloat", "(I)F", lambda a: _bb_get_idx(a, "f"))
    _add(BB, "getDouble", "(I)D", lambda a: _bb_get_idx(a, "d"))
    _add(BB, "get", f"([BII){BB}", _bb_get_arr_off)
    _add(BB, "hasArray", "()Z", lambda a: 0 if (a and isinstance(a[0], JObject) and a[0].fields.get("_ro")) else 1)
    _add(BB, "arrayOffset", "()I", lambda a: int(a[0].fields.get("_offset", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(BB, "duplicate", f"(){BB}", lambda a: _bb_dup(a, False))
    _add(BB, "asReadOnlyBuffer", f"(){BB}", lambda a: _bb_dup(a, True))
    _add(BB, "asIntBuffer", f"(){IB}", _bb_as_int_buf)
    _add(BB, "order", f"(){BO}", lambda a: JObject(BO, {"order": "big" if (not a or bb_is_big(a[0])) else "little", "_le": False if (not a or bb_is_big(a[0])) else True}))
    _add(IB, "get", f"([I){IB}", _ib_get_arr)
    _add(IB, "get", f"([III){IB}", _ib_get_arr)
    _add(CB, "array", "()[C", _cb_array)
    _add(MBB, "load", f"(){MBB}", lambda a: a[0] if a else None)
    _add(MBB, "force", f"(){MBB}", lambda a: a[0] if a else None)

    # FileChannel & FileLock
    def _fc_data(fc_obj: JObject) -> bytearray:
        p = fc_obj.fields.get("path", "")
        st = fc_obj.fields.get("_stream")
        if isinstance(st, JObject) and isinstance(st.fields.get("buf"), bytearray) and fc_obj.fields.get("_w"):
            return st.fields["buf"]
        raw = _vfs_read_bytes(stubs, p)
        if raw is not None:
            return bytearray(raw)
        if isinstance(st, JObject) and st.fields.get("_mem") is not None:
            return bytearray(st.fields["_mem"])
        return bytearray()

    def _fc_save(fc_obj: JObject, ba: bytearray):
        p = fc_obj.fields.get("path", "")
        if p:
            _vfs_write_bytes(stubs, p, bytes(ba))
        st = fc_obj.fields.get("_stream")
        if isinstance(st, JObject) and isinstance(st.fields.get("buf"), bytearray):
            st.fields["buf"][:] = ba

    def _fc_read(a):
        if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            return -1
        fc_obj, dst = a[0], a[1]
        pos = int(fc_obj.fields.get("_pos", 0))
        data = _fc_data(fc_obj)
        if pos >= len(data):
            return -1
        dpos = bb_pos(dst)
        dlim = bb_limit(dst)
        avail = max(0, dlim - dpos)
        take = min(avail, len(data) - pos)
        if take <= 0:
            return 0
        dba = bb_buf(dst)
        dba[dpos:dpos + take] = data[pos:pos + take]
        bb_set_pos(dst, dpos + take)
        fc_obj.fields["_pos"] = pos + take
        return take

    def _fc_write(a):
        if len(a) < 2 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            return 0
        fc_obj, src = a[0], a[1]
        spos = bb_pos(src)
        slim = bb_limit(src)
        chunk = bytes(bb_buf(src)[spos:slim])
        bb_set_pos(src, slim)
        data = _fc_data(fc_obj)
        pos = int(fc_obj.fields.get("_pos", 0))
        end = pos + len(chunk)
        if end > len(data):
            data.extend(b"\x00" * (end - len(data)))
        data[pos:end] = chunk
        fc_obj.fields["_pos"] = end
        _fc_save(fc_obj, data)
        return len(chunk)

    def _fc_map(a):
        if not a or not isinstance(a[0], JObject):
            return bb_make(cls=MBB)
        data = _fc_data(a[0])
        pos = max(0, int(a[2] if len(a) > 2 else 0))
        sz = max(0, int(a[3] if len(a) > 3 else len(data)))
        seg = bytearray(data[pos:pos + sz])
        if len(seg) < sz:
            seg.extend(b"\x00" * (sz - len(seg)))
        return bb_make(seg, limit=sz, big=True, cls=MBB)

    def _fc_transfer_from(a):
        if len(a) < 4 or not isinstance(a[0], JObject) or not isinstance(a[1], JObject):
            return 0
        fc_obj, src_ch = a[0], a[1]
        pos = max(0, int(a[2]))
        cnt = max(0, int(a[3]))
        tmp_bb = bb_make(bytearray(min(cnt, 4 * 1024 * 1024)))
        _invoke_vm_or_stub(stubs, src_ch, "Ljava/nio/channels/ReadableByteChannel;", "read", f"({BB})I", [tmp_bb])
        got = bb_pos(tmp_bb)
        chunk = bytes(bb_buf(tmp_bb)[:got])
        data = _fc_data(fc_obj)
        end = pos + len(chunk)
        if end > len(data):
            data.extend(b"\x00" * (end - len(data)))
        data[pos:end] = chunk
        _fc_save(fc_obj, data)
        return len(chunk)

    def _fc_transfer_to(a):
        if len(a) < 4 or not isinstance(a[0], JObject) or not isinstance(a[3], JObject):
            return 0
        fc_obj, pos, cnt, dst_ch = a[0], max(0, int(a[1])), max(0, int(a[2])), a[3]
        data = _fc_data(fc_obj)
        seg = bytes(data[pos:pos + cnt])
        src_bb = bb_make(seg)
        _invoke_vm_or_stub(stubs, dst_ch, "Ljava/nio/channels/WritableByteChannel;", "write", f"({BB})I", [src_bb])
        return len(seg)

    _add(FC, "size", "()J", lambda a: len(_fc_data(a[0])) if a and isinstance(a[0], JObject) else 0)
    _add(FC, "position", "()J", lambda a: int(a[0].fields.get("_pos", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(FC, "position", f"(J){FC}", lambda a: (a[0].fields.__setitem__("_pos", max(0, int(a[1] if len(a) > 1 else 0))), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(FC, "read", f"({BB})I", _fc_read)
    _add(FC, "write", f"({BB})I", _fc_write)
    _add(FC, "map", f"(Ljava/nio/channels/FileChannel$MapMode;JJ){MBB}", _fc_map)
    _add(FC, "transferFrom", "(Ljava/nio/channels/ReadableByteChannel;JJ)J", _fc_transfer_from)
    _add(FC, "transferTo", "(JJLjava/nio/channels/WritableByteChannel;)J", _fc_transfer_to)
    _add(FC, "force", "(Z)V", lambda a: None)
    _add(FC, "lock", f"(){FLK}", lambda a: JObject(FLK, {"_valid": True}))
    _add(FC, "tryLock", f"(){FLK}", lambda a: JObject(FLK, {"_valid": True}))
    _add(FC, "close", "()V", lambda a: a[0].fields.__setitem__("_open", False) if a and isinstance(a[0], JObject) else None)
    _add(FLK, "release", "()V", lambda a: a[0].fields.__setitem__("_valid", False) if a and isinstance(a[0], JObject) else None)
    _add(FLK, "isValid", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_valid", True)) else 0)
    _add("Ljava/nio/charset/Charset;", "newEncoder", "()Ljava/nio/charset/CharsetEncoder;", lambda a: JObject("Ljava/nio/charset/CharsetEncoder;", {"_cs": a[0] if a else None}))

    # SSLContext / SSLSocket / SSLSession / Certificates / InetAddress / Socket / Proxy
    SSLC = "Ljavax/net/ssl/SSLContext;"
    SSLSF = "Ljavax/net/ssl/SSLSocketFactory;"
    SSLS = "Ljavax/net/ssl/SSLSocket;"
    SSLP = "Ljavax/net/ssl/SSLParameters;"
    SSLSESS = "Ljavax/net/ssl/SSLSession;"
    SF = "Ljavax/net/SocketFactory;"
    HV = "Ljavax/net/ssl/HostnameVerifier;"
    X509TM = "Ljavax/net/ssl/X509TrustManager;"
    X509C = "Ljava/security/cert/X509Certificate;"
    CERT = "Ljava/security/cert/Certificate;"
    TA_CERT = "Ljava/security/cert/TrustAnchor;"
    PRN = "Ljava/security/Principal;"
    X500 = "Ljavax/security/auth/x500/X500Principal;"
    KEY = "Ljava/security/Key;"
    PRV = "Ljava/security/Provider;"
    SEC = "Ljava/security/Security;"

    _CIPHERS = ["TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256"]
    _PROTOS = ["TLSv1.3", "TLSv1.2"]

    def _mk_cert():
        raw = getattr(stubs, "_apk_cert_bytes", None) or (b"\x30\x82\x01\x00" + b"\x5a" * 60)
        return JObject(X509C, {"_bytes": raw, "_subject": "CN=Android,O=Example", "_issuer": "CN=Android CA,O=Example"})

    def _mk_session(host="localhost", port=443):
        return JObject(SSLSESS, {"_cipher": _CIPHERS[0], "_proto": "TLSv1.3", "_host": host, "_port": port, "_certs": [_mk_cert()]})

    def _mk_sslsocket(host="localhost", port=443):
        return JObject(SSLS, {
            "$host": host, "$port": port,
            "_ciphers": list(_CIPHERS), "_protos": list(_PROTOS),
            "_params": JObject(SSLP, {"_alpn": ["h2", "http/1.1"]}),
            "_session": _mk_session(host, port),
            "_alpn": "http/1.1",
            "connected": 1, "closed": 0,
        })

    _add(SF, "getDefault", f"(){SF}", lambda a: JObject(SF, {}))
    _add(SF, "createSocket", "()Ljava/net/Socket;", lambda a: JObject("Ljava/net/Socket;", {"connected": 0, "closed": 0}))
    _add(SSLC, "getInstance", f"(Ljava/lang/String;){SSLC}", lambda a: JObject(SSLC, {"_proto": _str_or_empty(a[0] if a else "TLS")}))
    _add(SSLC, "init", "([Ljavax/net/ssl/KeyManager;[Ljavax/net/ssl/TrustManager;Ljava/security/SecureRandom;)V", lambda a: a[0].fields.update({"_km": a[1] if len(a) > 1 else None, "_tm": a[2] if len(a) > 2 else None}) if a and isinstance(a[0], JObject) else None)
    _add(SSLC, "getSocketFactory", f"(){SSLSF}", lambda a: JObject(SSLSF, {}))
    _add(SSLSF, "createSocket", "(Ljava/net/Socket;Ljava/lang/String;IZ)Ljava/net/Socket;", lambda a: _mk_sslsocket(_str_or_empty(a[2] if len(a) > 2 else "localhost"), int(a[3] if len(a) > 3 else 443)))
    _add(SSLS, "startHandshake", "()V", lambda a: None)
    _add(SSLS, "getSession", f"(){SSLSESS}", lambda a: (a[0].fields.get("_session") or _mk_session()) if a and isinstance(a[0], JObject) else _mk_session())
    _add(SSLS, "getSSLParameters", f"(){SSLP}", lambda a: (a[0].fields.get("_params") or JObject(SSLP, {})) if a and isinstance(a[0], JObject) else JObject(SSLP, {}))
    _add(SSLS, "setSSLParameters", f"({SSLP})V", lambda a: a[0].fields.__setitem__("_params", a[1] if len(a) > 1 else None) if a and isinstance(a[0], JObject) else None)
    _add(SSLS, "getSupportedCipherSuites", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(c) for c in _CIPHERS]))
    _add(SSLS, "getEnabledCipherSuites", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(c) for c in (a[0].fields.get("_ciphers", _CIPHERS) if a and isinstance(a[0], JObject) else _CIPHERS)]))
    _add(SSLS, "setEnabledCipherSuites", "([Ljava/lang/String;)V", lambda a: a[0].fields.__setitem__("_ciphers", [_str_or_empty(x) for x in a[1].data]) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JArray) else None)
    _add(SSLS, "getEnabledProtocols", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(p) for p in (a[0].fields.get("_protos", _PROTOS) if a and isinstance(a[0], JObject) else _PROTOS)]))
    _add(SSLS, "setEnabledProtocols", "([Ljava/lang/String;)V", lambda a: a[0].fields.__setitem__("_protos", [_str_or_empty(x) for x in a[1].data]) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JArray) else None)
    _add(SSLS, "getApplicationProtocol", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_alpn", "http/1.1")) if a and isinstance(a[0], JObject) else JString("http/1.1"))
    _add(SSLP, "setApplicationProtocols", "([Ljava/lang/String;)V", lambda a: a[0].fields.__setitem__("_alpn", [_str_or_empty(x) for x in a[1].data]) if len(a) > 1 and isinstance(a[0], JObject) and isinstance(a[1], JArray) else None)
    _add(SSLSESS, "getCipherSuite", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_cipher", _CIPHERS[0])) if a and isinstance(a[0], JObject) else JString(_CIPHERS[0]))
    _add(SSLSESS, "getProtocol", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("_proto", "TLSv1.3")) if a and isinstance(a[0], JObject) else JString("TLSv1.3"))
    _add(SSLSESS, "getPeerCertificates", f"()[{CERT}", lambda a: JArray(f"[{CERT}", list(a[0].fields.get("_certs", [_mk_cert()])) if a and isinstance(a[0], JObject) else [_mk_cert()]))
    _add(SSLSESS, "getLocalCertificates", f"()[{CERT}", lambda a: None)
    _add(HV, "verify", f"(Ljava/lang/String;{SSLSESS})Z", lambda a: 1)
    _add(X509TM, "getAcceptedIssuers", f"()[{X509C}", lambda a: JArray(f"[{X509C}", []))
    _add("Landroid/net/ssl/SSLSockets;", "isSupportedSocket", f"({SSLS})Z", lambda a: 1)
    _add("Landroid/net/ssl/SSLSockets;", "setUseSessionTickets", f"({SSLS}Z)V", lambda a: None)

    _add(CERT, "getEncoded", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_bytes") or b"\x30\x82\x01\x00")) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(X509C, "getSubjectDN", f"(){PRN}", lambda a: JObject(X500, {"name": a[0].fields.get("_subject", "CN=Android")}) if a and isinstance(a[0], JObject) else JObject(X500, {"name": "CN=Android"}))
    _add(X509C, "getIssuerDN", f"(){PRN}", lambda a: JObject(X500, {"name": a[0].fields.get("_issuer", "CN=Android CA")}) if a and isinstance(a[0], JObject) else JObject(X500, {"name": "CN=Android CA"}))
    _add(X509C, "getSubjectX500Principal", f"(){X500}", lambda a: JObject(X500, {"name": a[0].fields.get("_subject", "CN=Android")}) if a and isinstance(a[0], JObject) else JObject(X500, {"name": "CN=Android"}))
    _add(X509C, "getIssuerX500Principal", f"(){X500}", lambda a: JObject(X500, {"name": a[0].fields.get("_issuer", "CN=Android CA")}) if a and isinstance(a[0], JObject) else JObject(X500, {"name": "CN=Android CA"}))
    _add(X509C, "getSubjectAlternativeNames", "()Ljava/util/Collection;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": []}))
    _add(TA_CERT, "getTrustedCert", f"(){X509C}", lambda a: a[0].fields.get("_cert") or _mk_cert() if a and isinstance(a[0], JObject) else _mk_cert())
    _add(PRN, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("name", "CN=Android")) if a and isinstance(a[0], JObject) else JString("CN=Android"))
    _add(X500, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("name", "CN=Android")) if a and isinstance(a[0], JObject) else JString("CN=Android"))
    _add(KEY, "getEncoded", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_key") or a[0].fields.get("_bytes") or b"")) if a and isinstance(a[0], JObject) else JArray("[B", []))
    _add(PRV, "getName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("name", "AndroidOpenSSL")) if a and isinstance(a[0], JObject) else JString("AndroidOpenSSL"))
    _add(SEC, "getProviders", f"()[{PRV}", lambda a: JArray(f"[{PRV}", [JObject(PRV, {"name": "AndroidOpenSSL"}), JObject(PRV, {"name": "BC"})]))

    SR = "Ljava/security/SecureRandom;"

    def _sr_next_bytes(a):
        if len(a) > 1 and isinstance(a[1], JArray):
            import hashlib as _hl
            o = a[0] if isinstance(a[0], JObject) else None
            seed = int(o.fields.get("_ctr", 0)) if o else 0
            out = bytearray()
            while len(out) < len(a[1].data):
                seed += 1
                out.extend(_hl.sha256(f"sr:{seed}".encode()).digest())
            if o is not None:
                o.fields["_ctr"] = seed
            for i in range(len(a[1].data)):
                a[1].data[i] = out[i] & 0xFF
        return None

    _add(SR, "<init>", "()V", lambda a: a[0].fields.__setitem__("_ctr", 0) if a and isinstance(a[0], JObject) else None)
    _add(SR, "setSeed", "(J)V", lambda a: a[0].fields.__setitem__("_ctr", int(a[1] or 0)) if len(a) > 1 and isinstance(a[0], JObject) else None)
    _add(SR, "nextBytes", "([B)V", _sr_next_bytes)
    _add(SR, "nextInt", "()I", lambda a: (lambda r: int.from_bytes(bytes(r.data), "big", signed=True))((lambda arr: (_sr_next_bytes([a[0] if a else None, arr]), arr)[1])(JArray("[B", [0] * 4))))
    _add(SR, "nextInt", "(I)I", lambda a: (lambda bound, r: (int.from_bytes(bytes(r.data), "big") % max(1, bound)))(int(a[1] if len(a) > 1 else 1), (lambda arr: (_sr_next_bytes([a[0] if a else None, arr]), arr)[1])(JArray("[B", [0] * 4))))

    # InetAddress / InetSocketAddress / Socket / DatagramSocket / Proxy / ProxySelector
    IA = "Ljava/net/InetAddress;"
    ISA = "Ljava/net/InetSocketAddress;"
    SO = "Ljava/net/Socket;"
    DS = "Ljava/net/DatagramSocket;"
    PRX = "Ljava/net/Proxy;"
    PRXT = "Ljava/net/Proxy$Type;"
    PSEL = "Ljava/net/ProxySelector;"

    def _ia_bytes(host: str) -> list[int]:
        if host in ("localhost", "127.0.0.1"):
            return [127, 0, 0, 1]
        parts = host.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return [int(p) for p in parts]
        return [192, 168, 1, 10]

    _add(IA, "getAllByName", f"(Ljava/lang/String;)[{IA}", lambda a: JArray(f"[{IA}", [JObject(IA, {"$host": _str_or_empty(a[0] if a else "127.0.0.1") or "127.0.0.1"})]))
    _add(IA, "getByAddress", f"([B){IA}", lambda a: JObject(IA, {"$host": ".".join(str(b) for b in _bytes_from(a[0] if a else b"\x7f\x00\x00\x01")[:4]), "_addr": list(_bytes_from(a[0] if a else b"\x7f\x00\x00\x01"))}))
    _add(IA, "getAddress", "()[B", lambda a: JArray("[B", list(a[0].fields.get("_addr") or _ia_bytes(a[0].fields.get("$host", "127.0.0.1")))) if a and isinstance(a[0], JObject) else JArray("[B", [127, 0, 0, 1]))
    _add(ISA, "createUnresolved", f"(Ljava/lang/String;I){ISA}", lambda a: JObject(ISA, {"$host": _str_or_empty(a[0] if a else ""), "$port": int(a[1] if len(a) > 1 else 0), "_unresolved": True}))
    _add(ISA, "getAddress", f"(){IA}", lambda a: JObject(IA, {"$host": a[0].fields.get("$host", "127.0.0.1")}) if a and isinstance(a[0], JObject) else JObject(IA, {"$host": "127.0.0.1"}))
    _add(ISA, "getHostName", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("$host", "localhost")) if a and isinstance(a[0], JObject) else JString("localhost"))
    _add(ISA, "getPort", "()I", lambda a: int(a[0].fields.get("$port", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SO, f"<init>", f"({PRX})V", lambda a: a[0].fields.update({"_proxy": a[1] if len(a) > 1 else None, "closed": 0, "connected": 0}) if a and isinstance(a[0], JObject) else None)
    _add(SO, "getSoTimeout", "()I", lambda a: int(a[0].fields.get("_so_timeout", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(SO, "isInputShutdown", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_in_shut")) else 0)
    _add(SO, "isOutputShutdown", "()Z", lambda a: 1 if (a and isinstance(a[0], JObject) and a[0].fields.get("_out_shut")) else 0)
    _add(SO, "getRemoteSocketAddress", "()Ljava/net/SocketAddress;", lambda a: JObject(ISA, {"$host": a[0].fields.get("$host", "127.0.0.1"), "$port": int(a[0].fields.get("$port", 0))}) if a and isinstance(a[0], JObject) else None)
    _add(DS, "getLocalPort", "()I", lambda a: int(a[0].fields.get("$port", 49153)) if a and isinstance(a[0], JObject) else 49153)
    _add(PRX, "type", f"(){PRXT}", lambda a: a[0].fields.get("_type") or JObject(PRXT, {"__ename": "DIRECT", "__ordinal": 0}) if a and isinstance(a[0], JObject) else JObject(PRXT, {"__ename": "DIRECT", "__ordinal": 0}))
    _add(PRX, "address", "()Ljava/net/SocketAddress;", lambda a: a[0].fields.get("_addr") if a and isinstance(a[0], JObject) else None)
    _add(PSEL, "getDefault", f"(){PSEL}", lambda a: JObject(PSEL, {}))
    _add(PSEL, "select", "(Ljava/net/URI;)Ljava/util/List;", lambda a: JObject("Ljava/util/ArrayList;", {"__items": [JObject(PRX, {"_type": JObject(PRXT, {"__ename": "DIRECT", "__ordinal": 0})})]}))
    _add(PSEL, "connectFailed", "(Ljava/net/URI;Ljava/net/SocketAddress;Ljava/io/IOException;)V", lambda a: None)

    # android.net.Uri & Uri$Builder additions
    AURI = "Landroid/net/Uri;"
    AUB = "Landroid/net/Uri$Builder;"

    def _auri_port(a):
        try:
            p = urlparse(_uri_text(a[0] if a else None)).port
            return int(p) if p is not None else -1
        except Exception:
            return -1

    def _auri_ssp(a):
        u = _uri_text(a[0] if a else None)
        p = urlparse(u)
        if p.scheme and u.startswith(p.scheme + ":"):
            return u[len(p.scheme) + 1:].split("#", 1)[0]
        return u.split("#", 1)[0]

    _add(AURI, "encode", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString(quote(_str_or_empty(a[0] if a else ""), safe="")) if a and a[0] is not None else None)
    _add(AURI, "decode", "(Ljava/lang/String;)Ljava/lang/String;", lambda a: JString(unquote(_str_or_empty(a[0] if a else ""))) if a and a[0] is not None else None)
    _add(AURI, "fromParts", f"(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;){AURI}", lambda a: JObject(AURI, {"$uri": f"{_str_or_empty(a[0])}:{quote(_str_or_empty(a[1] if len(a) > 1 else ''), safe='')}" + (f"#{quote(_str_or_empty(a[2]), safe='')}" if len(a) > 2 and _txt(a[2]) else "")}))
    _add(AURI, "fromFile", f"(Ljava/io/File;){AURI}", lambda a: JObject(AURI, {"$uri": "file://" + _norm_path(a[0].fields.get("path", "") if a and isinstance(a[0], JObject) else "")}))
    _add(AURI, "withAppendedPath", f"({AURI}Ljava/lang/String;){AURI}", lambda a: (lambda p, seg: JObject(AURI, {"$uri": urlunparse((p.scheme, p.netloc, p.path.rstrip("/") + "/" + seg.lstrip("/"), p.params, p.query, p.fragment))}))(urlparse(_uri_text(a[0] if a else "")), _str_or_empty(a[1] if len(a) > 1 else "")))
    _add(AURI, "getEncodedPath", "()Ljava/lang/String;", lambda a: JString(urlparse(_uri_text(a[0] if a else "")).path))
    _add(AURI, "getPort", "()I", _auri_port)
    _add(AURI, "getSchemeSpecificPart", "()Ljava/lang/String;", lambda a: JString(unquote(_auri_ssp(a))))
    _add(AURI, "equals", "(Ljava/lang/Object;)Z", lambda a: 1 if (len(a) > 1 and isinstance(a[1], JObject) and a[1].cls == AURI and _uri_text(a[0]) == _uri_text(a[1])) else 0)
    _add(AURI, "hashCode", "()I", lambda a: stubs._java_strhash(_uri_text(a[0] if a else "")))
    _add(AUB, "appendEncodedPath", f"(Ljava/lang/String;){AUB}", lambda a: (a[0].fields.__setitem__("_path", a[0].fields.get("_path", "").rstrip("/") + "/" + _str_or_empty(a[1] if len(a) > 1 else "").lstrip("/")), a[0])[1] if a and isinstance(a[0], JObject) else None)
    _add(AUB, "query", f"(Ljava/lang/String;){AUB}", lambda a: (a[0].fields.__setitem__("_query", parse_qsl(_str_or_empty(a[1] if len(a) > 1 else ""), keep_blank_values=True)), a[0])[1] if a and isinstance(a[0], JObject) else None)

    # ICU DecimalFormatSymbols, DateFormat/NumberFormat/ParsePosition, sql.*, time.*, Calendar, Locale, UUID
    DFS = "Landroid/icu/text/DecimalFormatSymbols;"
    _add(DFS, "getInstance", f"(Ljava/util/Locale;){DFS}", lambda a: JObject(DFS, {}))
    _add(DFS, "getInstance", f"(){DFS}", lambda a: JObject(DFS, {}))
    _add(DFS, "getDigitStrings", "()[Ljava/lang/String;", lambda a: JArray("[Ljava/lang/String;", [JString(str(d)) for d in range(10)]))

    PP = "Ljava/text/ParsePosition;"
    _add(PP, "<init>", "(I)V", lambda a: a[0].fields.__setitem__("_idx", int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else None)
    _add(PP, "getIndex", "()I", lambda a: int(a[0].fields.get("_idx", 0)) if a and isinstance(a[0], JObject) else 0)
    _add(PP, "setIndex", "(I)V", lambda a: a[0].fields.__setitem__("_idx", int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else None)

    def _df_parse_pos(a):
        import datetime as _dt
        pat = a[0].fields.get("__pattern", "yyyy-MM-dd") if a and isinstance(a[0], JObject) else "yyyy-MM-dd"
        src = _str_or_empty(a[1] if len(a) > 1 else "")
        pp = a[2] if len(a) > 2 and isinstance(a[2], JObject) else None
        start = int(pp.fields.get("_idx", 0)) if pp else 0
        sub = src[start:]
        py_fmt = pat.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d").replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
        try:
            dt = _dt.datetime.strptime(sub, py_fmt)
            if pp:
                pp.fields["_idx"] = len(src)
            return JObject("Ljava/util/Date;", {"__ms": int(dt.timestamp() * 1000)})
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
                try:
                    dt = _dt.datetime.strptime(sub[:19], fmt)
                    if pp:
                        pp.fields["_idx"] = len(src)
                    return JObject("Ljava/util/Date;", {"__ms": int(dt.timestamp() * 1000)})
                except Exception:
                    pass
            return None

    _add("Ljava/text/DateFormat;", "parse", f"(Ljava/lang/String;{PP})Ljava/util/Date;", _df_parse_pos)
    _add("Ljava/text/SimpleDateFormat;", "parse", f"(Ljava/lang/String;{PP})Ljava/util/Date;", _df_parse_pos)
    _add("Ljava/text/SimpleDateFormat;", "toPattern", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("__pattern", "yyyy-MM-dd")) if a and isinstance(a[0], JObject) else JString("yyyy-MM-dd"))
    _add("Ljava/text/NumberFormat;", "format", "(J)Ljava/lang/String;", lambda a: JString(str(int(a[1] if len(a) > 1 else 0))))

    for _sqlc in ("Ljava/sql/Date;", "Ljava/sql/Time;", "Ljava/sql/Timestamp;"):
        _add(_sqlc, "<init>", "(J)V", lambda a: a[0].fields.__setitem__("__ms", int(a[1] if len(a) > 1 else 0)) if a and isinstance(a[0], JObject) else None)
        _add(_sqlc, "getTime", "()J", lambda a: int(a[0].fields.get("__ms", 0)) if a and isinstance(a[0], JObject) else 0)

    DUR = "Ljava/time/Duration;"
    _add(DUR, "ofSeconds", f"(JJ){DUR}", lambda a: JObject(DUR, {"_ms": int(a[0] if a else 0) * 1000 + int(a[1] if len(a) > 1 else 0) // 1_000_000}))
    _add(DUR, "toMillis", "()J", lambda a: int(a[0].fields.get("_ms", 0)) if a and isinstance(a[0], JObject) else 0)
    _add("Ljava/time/Instant;", "ofEpochSecond", "(JJ)Ljava/time/Instant;", lambda a: JObject("Ljava/time/Instant;", {"__epoch_sec": int(a[0] if a else 0), "__epoch_ms": int(a[0] if a else 0) * 1000 + int(a[1] if len(a) > 1 else 0) // 1_000_000}))
    _add("Ljava/util/Date;", "toInstant", "()Ljava/time/Instant;", lambda a: (lambda ms: JObject("Ljava/time/Instant;", {"__epoch_sec": ms // 1000, "__epoch_ms": ms}))(int(a[0].fields.get("__ms", 0)) if a and isinstance(a[0], JObject) else 0))
    _add("Ljava/util/Calendar;", "setLenient", "(Z)V", lambda a: a[0].fields.__setitem__("_lenient", bool(a[1] if len(a) > 1 else True)) if a and isinstance(a[0], JObject) else None)
    _add("Ljava/util/Calendar;", "getFirstDayOfWeek", "()I", lambda a: int(a[0].fields.get("_first_dow", 2)) if a and isinstance(a[0], JObject) else 2)

    LOC = "Ljava/util/Locale;"
    _add(LOC, "<init>", "(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", lambda a: a[0].fields.update({"lang": _str_or_empty(a[1] if len(a) > 1 else "en"), "country": _str_or_empty(a[2] if len(a) > 2 else ""), "variant": _str_or_empty(a[3] if len(a) > 3 else "")}) if a and isinstance(a[0], JObject) else None)
    _add(LOC, "forLanguageTag", f"(Ljava/lang/String;){LOC}", lambda a: (lambda pts: JObject(LOC, {"lang": pts[0], "country": pts[1] if len(pts) > 1 and len(pts[1]) == 2 else ""}))(_str_or_empty(a[0] if a else "en").replace("_", "-").split("-")))
    _add(LOC, "getDefault", f"(Ljava/util/Locale$Category;){LOC}", lambda a: JObject(LOC, {"lang": "de", "country": "DE"}))
    _add(LOC, "getScript", "()Ljava/lang/String;", lambda a: JString(a[0].fields.get("script", "")) if a and isinstance(a[0], JObject) else JString(""))

    _add("Ljava/util/UUID;", "<init>", "(JJ)V", lambda a: a[0].fields.__setitem__("text", str(_py_uuid.UUID(int=((int(a[1] if len(a) > 1 else 0) & ((1 << 64) - 1)) << 64) | (int(a[2] if len(a) > 2 else 0) & ((1 << 64) - 1))))) if a and isinstance(a[0], JObject) else None)


# ---------------------------------------------------------------------------
# Public Entrypoints: seed_g15_statics + install_g15_stubs
# ---------------------------------------------------------------------------

def seed_g15_statics(stubs, vm) -> None:
    """Seedet statische Konstanten (Math/StrictMath, Thread$State, BigInteger/Decimal, MapMode, Bundle.EMPTY, Uri.EMPTY)."""
    if vm is None or not hasattr(vm, "statics"):
        return
    st = vm.statics
    st.setdefault("Ljava/lang/Math;", {}).update({"PI": math.pi, "E": math.e})
    st.setdefault("Ljava/lang/StrictMath;", {}).update({"PI": math.pi, "E": math.e})

    ths = "Ljava/lang/Thread$State;"
    st.setdefault(ths, {}).update({
        nm: JObject(ths, {"__ename": nm, "__ordinal": idx})
        for idx, nm in enumerate(("NEW", "RUNNABLE", "BLOCKED", "WAITING", "TIMED_WAITING", "TERMINATED"))
    })
    st.setdefault("Ljava/lang/Thread;", {}).update({"MIN_PRIORITY": 1, "NORM_PRIORITY": 5, "MAX_PRIORITY": 10})

    bi = "Ljava/math/BigInteger;"
    st.setdefault(bi, {}).update({
        "ZERO": JObject(bi, {"_bi": 0}),
        "ONE": JObject(bi, {"_bi": 1}),
        "TWO": JObject(bi, {"_bi": 2}),
        "TEN": JObject(bi, {"_bi": 10}),
    })
    bd = "Ljava/math/BigDecimal;"
    st.setdefault(bd, {}).update({
        "ZERO": JObject(bd, {"_dec": Decimal(0)}),
        "ONE": JObject(bd, {"_dec": Decimal(1)}),
        "TEN": JObject(bd, {"_dec": Decimal(10)}),
        "ROUND_UP": 0, "ROUND_DOWN": 1, "ROUND_CEILING": 2, "ROUND_FLOOR": 3,
        "ROUND_HALF_UP": 4, "ROUND_HALF_DOWN": 5, "ROUND_HALF_EVEN": 6, "ROUND_UNNECESSARY": 7,
    })
    rm = "Ljava/math/RoundingMode;"
    st.setdefault(rm, {}).update({
        nm: JObject(rm, {"__ename": nm, "__ordinal": idx})
        for idx, nm in enumerate(("UP", "DOWN", "CEILING", "FLOOR", "HALF_UP", "HALF_DOWN", "HALF_EVEN", "UNNECESSARY"))
    })
    mc = "Ljava/math/MathContext;"
    st.setdefault(mc, {}).update({
        "UNLIMITED": JObject(mc, {"precision": 0}),
        "DECIMAL32": JObject(mc, {"precision": 7}),
        "DECIMAL64": JObject(mc, {"precision": 16}),
        "DECIMAL128": JObject(mc, {"precision": 34}),
    })
    mm = "Ljava/nio/channels/FileChannel$MapMode;"
    st.setdefault(mm, {}).update({
        nm: JObject(mm, {"_mode": nm}) for nm in ("READ_ONLY", "READ_WRITE", "PRIVATE")
    })
    pt = "Ljava/net/Proxy$Type;"
    st.setdefault(pt, {}).update({
        nm: JObject(pt, {"__ename": nm, "__ordinal": idx}) for idx, nm in enumerate(("DIRECT", "HTTP", "SOCKS"))
    })
    st.setdefault("Ljava/net/Proxy;", {}).update({
        "NO_PROXY": JObject("Ljava/net/Proxy;", {"_type": st[pt]["DIRECT"]}),
    })
    empty_bmap = {}
    st.setdefault("Landroid/os/Bundle;", {}).setdefault("EMPTY", JObject("Landroid/os/Bundle;", {"map": empty_bmap, "$map": empty_bmap}))
    st.setdefault("Landroid/net/Uri;", {}).setdefault("EMPTY", JObject("Landroid/net/Uri;", {"$uri": ""}))


def install_g15_stubs(stubs) -> None:
    """Registriert G15 SDK-Breiten-Stubs additiv (mit gezieltem R()-Ersatz fuer fehlerhafte Alt-Stubs)."""
    if stubs is None or getattr(stubs, "_g15_installed", False):
        return
    stubs._g15_installed = True

    R = stubs.register
    RB = stubs.register_by_name

    def _add(cls: str, name: str, sig: str, fn) -> None:
        if (cls, name, sig) not in stubs._stubs:
            R(cls, name, sig, fn)

    _install_math_strictmath(stubs, _add)
    _install_io_and_vfs(stubs, _add, R)
    _install_lang_and_reflect(stubs, _add, R, RB)
    _install_concurrent_and_collections(stubs, _add, R, RB)
    _install_zip_and_math_big(stubs, _add, R)
    _install_os_and_ipc(stubs, _add, R, RB)
    _install_android_framework(stubs, _add, R, RB)
    _install_resources_and_notifications(stubs, _add, R, RB)
    _install_nio_net_ssl_time(stubs, _add, R, RB)

    if getattr(stubs, "vm", None) is not None:
        seed_g15_statics(stubs, stubs.vm)
