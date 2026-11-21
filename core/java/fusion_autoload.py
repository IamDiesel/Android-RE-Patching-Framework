# -*- coding: utf-8 -*-
"""core.java.fusion_autoload — laedt eine ECHTE native Library einer entpackten App in die
Fusions-Engine und registriert ihre nativen Methoden in der `NativeMethodTable`.

Nutzt den Multi-SO-Linker (`core.native.linker.LinkerSpace`), generische libc-FFI-Stubs,
Zeit-/Instruktionsbudgets + Futex-Stall-Isolation fuer `DT_INIT_ARRAY`/`JNI_OnLoad` sowie einen
optionalen SHA-256-OnLoad-Cache (fuer `self_cache`). Zielagnostisch und IP-neutral."""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, List, Optional

_M64 = (1 << 64) - 1
_RAM_ONLOAD_CACHE: Dict[str, dict] = {}


def ret_of_sig(sig):
    """JNI-Rueckgabekind fuer `bridge.call_native`: 'void' | 'int' | 'jref'."""
    try:
        r = sig[sig.rindex(")") + 1:]
    except Exception:
        return "int"
    if r == "V":
        return "void"
    if r and (r[0] == "L" or r[0] == "["):
        return "jref"
    return "int"   # Z B C S I J F D -> als 32/64-bit int (x0)


def _to_dalvik(cls):
    """Normalisiert einen Klassennamen auf die Dalvik-Form `Lpkg/Cls;`."""
    if not cls or cls == "?":
        return cls
    c = cls.replace(".", "/")
    if not c.startswith("L"):
        c = "L" + c
    if not c.endswith(";"):
        c = c + ";"
    return c


def _img_bias(img, load_bias: Optional[int] = None) -> int:
    if load_bias is not None:
        return int(load_bias) & _M64
    return int(getattr(img, "load_bias", 0) or 0) & _M64


def find_jni_onload(img, load_bias: Optional[int] = None):
    """Virtuelle Adresse von `JNI_OnLoad` im Image (inkl. load_bias) oder None."""
    sym = None
    try:
        sym = img.dynsyms.get("JNI_OnLoad")
    except Exception:
        sym = None
    if sym is None:
        return None
    return (_img_bias(img, load_bias) + int(sym.value)) & _M64


def run_jni_onload(svc, eng, addr, javavm=0, maxins=2000000, max_wall_s=2.5):
    """Fuehrt `JNI_OnLoad(JavaVM*, reserved)` an `addr` auf der Engine aus. Rueckgabe: x0 (Version)."""
    from core.native.libc_stubs import ensure_tls_page

    cpu = getattr(eng, "cpu", None)
    if cpu is not None:
        ensure_tls_page(eng)
        cpu.stopped = False
        cpu.stop_reason = None
    try:
        res = svc.run(eng, int(addr) & _M64, (int(javavm) & _M64, 0), None, None,
                      int(maxins), max_wall_s=max_wall_s)
    except TypeError:
        res = svc.run(eng, int(addr) & _M64, (int(javavm) & _M64, 0), None, None, int(maxins))
    if cpu is not None:
        cpu.stopped = False
        cpu.stop_reason = None
    if isinstance(res, dict) and not res.get("ok", True):
        return None
    return int(cpu.x[0]) & _M64 if cpu is not None else 0


def _run_ctor_bounded(svc, eng, addr: int, maxins: int, wall_s: float) -> str:
    """Fuehrt EINEN ELF-Konstruktor geschuetzt aus -> 'ok' | 'stalled' | 'timeout' | 'error'."""
    cpu = getattr(eng, "cpu", None)
    if cpu is not None:
        cpu.stopped = False
        cpu.stop_reason = None
    try:
        res = svc.run(eng, int(addr) & _M64, (0, 0, 0), None, None,
                      int(maxins), max_wall_s=max(0.05, float(wall_s)))
    except TypeError:
        res = svc.run(eng, int(addr) & _M64, (0, 0, 0), None, None, int(maxins))
    except Exception:
        if cpu is not None:
            cpu.stopped = False
            cpu.stop_reason = None
        return "error"

    reason = getattr(cpu, "stop_reason", None) if cpu is not None else None
    if cpu is not None:
        cpu.stopped = False
        cpu.stop_reason = None
    if reason and "futex" in str(reason):
        return "stalled"
    if isinstance(res, dict) and not res.get("ok", True):
        why = res.get("why", "error")
        if why in ("timeout", "maxins", "stall"):
            return "stalled"
        return "error"
    return "ok"


def run_init_funcs(svc, eng, img, maxins=250000, max_wall_s=1.5, load_bias=None):
    """Fuehrt DT_INIT + DT_INIT_ARRAY reloziert & budgetiert aus.

    Ein einzelner haengender Konstruktor (z. B. Go-/Watchdog-Scheduler-Spin oder Futex-Wait)
    wird isoliert gestoppt (`stalled`), damit nachfolgende Initializer (z. B. Registrierungen)
    trotzdem sauber ablaufen koennen.
    """
    from core.native.libc_stubs import ensure_tls_page

    ensure_tls_page(eng)
    bias = _img_bias(img, load_bias)
    out = {"init": 0, "init_array": 0, "stalled": 0, "errors": 0, "budget_exhausted": False}
    deadline = time.monotonic() + float(max_wall_s) if (max_wall_s and float(max_wall_s) > 0) else 0.0

    def _call(addr: int, budget_frac: float = 0.4) -> bool:
        rem = (deadline - time.monotonic()) if deadline else float(budget_frac)
        if deadline and rem <= 0.0:
            out["budget_exhausted"] = True
            return False
        status = _run_ctor_bounded(svc, eng, addr, int(maxins), min(float(budget_frac), rem) if deadline else float(budget_frac))
        if status == "ok":
            return True
        if status == "stalled":
            out["stalled"] += 1
            return False
        out["errors"] += 1
        return False

    init = int(getattr(img, "init", 0) or 0)
    if init:
        eff_init = (init + bias) & _M64
        if _call(eff_init, budget_frac=0.5):
            out["init"] += 1

    ia = int(getattr(img, "init_array", 0) or 0)
    sz = int(getattr(img, "init_arraysz", 0) or 0)
    if ia and sz >= 8:
        eff_ia = (ia + bias) & _M64
        for k in range(sz // 8):
            if deadline and time.monotonic() >= deadline:
                out["budget_exhausted"] = True
                break
            try:
                raw = eng.mem.read((eff_ia + k * 8) & _M64, 8)
                ptr = int.from_bytes(bytes(raw), "little")
            except Exception:
                out["errors"] += 1
                continue
            if ptr in (0, _M64):
                continue
            # Falls ein Eintrag noch nicht durch apply_relocs verschoben wurde (z. B. ohne RELATIVE)
            if bias and ptr < bias and ptr < 0x10000000:
                ptr = (ptr + bias) & _M64
            if _call(ptr, budget_frac=0.35):
                out["init_array"] += 1
    return out


def harvest_registered_natives(jni, table, default_static=True, start_index=0):
    """Liest die RegisterNatives-Map der Fassade (`jni.st.natives[start_index:]`) -> NativeMethodTable."""
    recs = getattr(getattr(jni, "st", None), "natives", None) or []
    n = 0
    for rec in recs[int(start_index):]:
        name = rec.get("name"); sig = rec.get("sig"); fp = rec.get("fnptr")
        cls = _to_dalvik(rec.get("class"))
        if not (name and sig and fp and cls):
            continue
        try:
            fnptr = int(fp, 16) if isinstance(fp, str) else int(fp)
        except Exception:
            continue
        table.register(cls, name, sig, fnptr, ret=ret_of_sig(sig), static=bool(default_static))
        n += 1
    return n


def autoload_library_from_bytes(svc, eng, jni, data, table, run_onload=True,
                                apply_relocs=True, ffi=None, **kwargs):
    """Wie `autoload_library`, aber aus KLARTEXT-Bytes, die die App selbst entpackt hat."""
    from core.native.loader import ElfImage
    img = ElfImage.parse(bytes(data))
    return autoload_library(svc, eng, jni, img, table, run_onload=run_onload,
                            apply_relocs=apply_relocs, ffi=ffi, **kwargs)


def _demangle_jni(sym):
    """Java_<mangled_class>_<method> -> (Lclass;, method)."""
    if not sym or not sym.startswith("Java_"):
        return None
    body = sym[5:]
    if "__" in body:
        body = body.split("__", 1)[0]
    out = []; i = 0; n = len(body)
    while i < n:
        c = body[i]
        if c == "_" and i + 1 < n:
            nx = body[i + 1]
            if nx == "1": out.append("_"); i += 2; continue
            if nx == "2": out.append(";"); i += 2; continue
            if nx == "3": out.append("["); i += 2; continue
            if nx == "0" and i + 5 < n:
                try: out.append(chr(int(body[i + 2:i + 6], 16)))
                except Exception: out.append("_")
                i += 6; continue
            out.append("/"); i += 1; continue
        out.append(c); i += 1
    dec = "".join(out)
    if "/" not in dec:
        return None
    cls_path, method = dec.rsplit("/", 1)
    return "L" + cls_path + ";", method


def harvest_static_jni(img, table, default_ret="int", load_bias=None):
    """Statisch exportierte Java_<Class>_<method>-Symbole (inkl. load_bias) in NativeMethodTable eintragen."""
    bias = _img_bias(img, load_bias)
    n = 0
    for name, sym in (getattr(img, "dynsyms", {}) or {}).items():
        if not name.startswith("Java_"):
            continue
        dm = _demangle_jni(name)
        if dm is None:
            continue
        try:
            raw_val = int(getattr(sym, "value", 0))
        except Exception:
            continue
        if not raw_val:
            continue
        fnptr = (bias + raw_val) & _M64
        ret = default_ret
        table.register_static_symbol(dm[0], dm[1], fnptr, ret=ret)
        n += 1
    return n


def _load_onload_cache(sha: str, cache_dir: Optional[str]) -> Optional[dict]:
    if not sha:
        return None
    if sha in _RAM_ONLOAD_CACHE:
        return _RAM_ONLOAD_CACHE[sha]
    if cache_dir:
        fp = os.path.join(cache_dir, f"{sha[:16]}.json")
        if os.path.isfile(fp):
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict) and d.get("sha256") == sha:
                    _RAM_ONLOAD_CACHE[sha] = d
                    return d
            except Exception:
                pass
    return None


def _hash_exec_segments(eng, img, bias: int) -> str:
    cpu = getattr(eng, "cpu", None)
    mem = getattr(cpu, "m", None) if cpu is not None else None
    if mem is None:
        return ""
    h = hashlib.sha256()
    any_seg = False
    for sg in (getattr(img, "segments", None) or ()):
        if (int(getattr(sg, "flags", 0) or 0) & 0x1) == 0:
            continue
        va = (bias + int(getattr(sg, "vaddr", 0) or 0)) & _M64
        sz = min(max(int(getattr(sg, "memsz", 0) or 0), int(getattr(sg, "filesz", 0) or 0)), 4 * 1024 * 1024)
        if sz <= 0:
            continue
        any_seg = True
        try:
            h.update(bytes(mem.rb(va, sz)))
        except Exception:
            pass
    return h.hexdigest() if any_seg else ""


def _save_onload_cache(sha: str, entry: dict, cache_dir: Optional[str]) -> None:
    if not sha:
        return
    _RAM_ONLOAD_CACHE[sha] = entry
    if cache_dir:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            fp = os.path.join(cache_dir, f"{sha[:16]}.json")
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass


def autoload_library(svc, eng, jni, img, table, run_onload=True, apply_relocs=True, ffi=None,
                     *, name="", path="", use_linker=True, lazy_init=False,
                     init_wall_s=1.5, onload_wall_s=2.5, use_onload_cache=False,
                     cache_dir=None):
    """Volle Multi-SO-Ladekette fuer eine echte Library:

    1. ELF in den `LinkerSpace` an eigenem `load_bias` laden + Relocations + libc-/Cross-Lib-Imports.
    2. Statische `Java_*`-Symbole sofort eintragen.
    3. Falls `lazy_init=True`: Initialisierung aufschieben; sonst (oder bei Cache-Miss)
       budgetiertes `DT_INIT`/`DT_INIT_ARRAY` + `JNI_OnLoad` ausfuehren und nur neue
       `RegisterNatives`-Bindungen in `table` uebernehmen.
    """
    out = {
        "load_bias": 0, "relocs": 0, "wired": 0, "init_called": 0,
        "init_stalled": 0, "init_errors": 0, "onload_addr": None,
        "onload_ret": None, "registered": 0, "static_jni": 0,
        "cached": False, "lazy": bool(lazy_init),
    }

    mod = None
    if use_linker:
        try:
            from core.native.linker import get_linker
            lnk = get_linker(eng)
            mod = lnk.load_module(
                svc, eng, img, name=name, path=path,
                apply_relocs=apply_relocs, ffi=ffi,
            )
            bias = mod.load_bias
            out["load_bias"] = bias
            out["relocs"] = int(mod.init_stats.get("relocs", 0))
            out["wired"] = int(mod.init_stats.get("wired", 0))
            if mod.onload_done:
                out["onload_ret"] = mod.onload_ret
                out["cached"] = True
                return out
        except Exception:
            mod = None

    if mod is None:
        bias = _img_bias(img)
        try:
            out["relocs"] = svc.load_image(eng, img, apply_relocs=apply_relocs, load_bias=bias)
        except TypeError:
            out["relocs"] = svc.load_image(eng, img, apply_relocs=apply_relocs)
        except Exception:
            out["relocs"] = 0
        if ffi is not None:
            try:
                from core.native.libc_stubs import install_libc_stubs
                install_libc_stubs(eng, ffi)
            except Exception:
                pass
            try:
                _w = ffi.wire_imports(eng, img, load_bias=bias)
                out["wired"] = int(_w.get("count", 0)) if isinstance(_w, dict) else 0
            except TypeError:
                _w = ffi.wire_imports(eng, img)
                out["wired"] = int(_w.get("count", 0)) if isinstance(_w, dict) else 0
            except Exception:
                out["wired"] = 0
    else:
        bias = mod.load_bias

    try:
        out["static_jni"] = harvest_static_jni(img, table, load_bias=bias)
    except Exception:
        out["static_jni"] = 0

    if lazy_init:
        return out

    raw_bytes = bytes(getattr(img, "data", b"") or b"")
    sha = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else ""
    if (use_onload_cache or cache_dir is not None) and sha:
        hit = _load_onload_cache(sha, cache_dir)
        if hit is not None:
            reg_n = 0
            for ent in (hit.get("natives") or []):
                try:
                    cls = _to_dalvik(ent["class"])
                    nm = ent["name"]; sg = ent["sig"]; rva = int(ent["rva"])
                    fnptr = (bias + rva) & _M64
                    table.register(cls, nm, sg, fnptr, ret=ret_of_sig(sg), static=True)
                    reg_n += 1
                except Exception:
                    continue
            out["registered"] = reg_n
            out["onload_ret"] = hit.get("onload_ret")
            out["cached"] = True
            if mod is not None:
                mod.init_done = True
                mod.onload_done = True
                mod.onload_ret = out["onload_ret"]
                mod.registered_count = reg_n
            return out

    exec_before = _hash_exec_segments(eng, img, bias)
    _t_init0 = time.monotonic()

    # DT_INIT / DT_INIT_ARRAY (budgetiert + stall-isoliert)
    try:
        _ic = run_init_funcs(svc, eng, img, max_wall_s=init_wall_s, load_bias=bias)
        out["init_called"] = _ic.get("init", 0) + _ic.get("init_array", 0)
        out["init_stalled"] = _ic.get("stalled", 0)
        out["init_errors"] = _ic.get("errors", 0)
    except Exception:
        out["init_called"] = 0

    recs = getattr(getattr(jni, "st", None), "natives", None)
    before_n = len(recs) if recs is not None else 0

    if run_onload:
        try:
            addr = find_jni_onload(img, load_bias=bias)
        except TypeError:
            addr = find_jni_onload(img)
            if addr is not None and bias:
                addr = (addr + bias) & _M64
        out["onload_addr"] = addr
        if addr is not None:
            try:
                _VM = getattr(jni, "VM_OBJ", None)
                if _VM is None:
                    try:
                        from services.native_jni_env_service import VM_OBJ as _VM
                    except Exception:
                        _VM = 0x66010000
                out["onload_ret"] = run_jni_onload(svc, eng, addr, javavm=_VM, max_wall_s=onload_wall_s)
            except Exception:
                out["onload_ret"] = None

    elapsed_init = time.monotonic() - _t_init0
    exec_after = _hash_exec_segments(eng, img, bias)

    out["registered"] = harvest_registered_natives(jni, table, start_index=before_n)
    if mod is not None:
        mod.init_done = True
        mod.onload_done = bool(run_onload)
        mod.onload_ret = out["onload_ret"]
        mod.registered_count = out["registered"]
        mod.static_jni_count = out["static_jni"]

    if (use_onload_cache or cache_dir is not None) and sha and run_onload and out["onload_ret"] is not None:
        segs = list(getattr(img, "segments", None) or ())
        if segs:
            mod_lo = bias + min(int(getattr(s, "vaddr", 0) or 0) for s in segs)
            mod_hi = bias + max(
                int(getattr(s, "vaddr", 0) or 0) + max(int(getattr(s, "memsz", 0) or 0), int(getattr(s, "filesz", 0) or 0), 0x1000)
                for s in segs
            )
        else:
            mod_lo, mod_hi = 0, 0
        new_recs = (getattr(getattr(jni, "st", None), "natives", None) or [])[before_n:]
        cached_natives: List[dict] = []
        all_in_module = True
        for rec in new_recs:
            fp = rec.get("fnptr")
            try:
                fn_abs = int(fp, 16) if isinstance(fp, str) else int(fp)
            except Exception:
                continue
            if mod_hi > mod_lo and not (mod_lo <= fn_abs < mod_hi):
                all_in_module = False
                break
            rva = (fn_abs - bias) & _M64
            cached_natives.append({
                "class": rec.get("class"),
                "name": rec.get("name"),
                "sig": rec.get("sig"),
                "rva": rva,
            })
        exec_unchanged = (exec_before == exec_after)
        worth_caching = (out["init_stalled"] > 0 or elapsed_init >= 0.20)
        if exec_unchanged and all_in_module and worth_caching:
            _save_onload_cache(sha, {
                "sha256": sha,
                "onload_ret": out["onload_ret"],
                "natives": cached_natives,
            }, cache_dir)

    return out


def mirror_tree_to_vfs(osv, eng, root, rel, dest_prefix, max_bytes=64 * 1024 * 1024,
                       total_max=256 * 1024 * 1024):
    """Spiegelt Dateien aus `<root>/<rel>` der entpackten App ins VFS unter `<dest_prefix>/…`."""
    base = os.path.join(root, rel)
    if not os.path.isdir(base):
        return 0
    files = {}
    total = 0
    for dp, _dirs, fns in os.walk(base):
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                sz = os.path.getsize(fp)
                if sz > max_bytes or total + sz > total_max:
                    continue
                with open(fp, "rb") as fh:
                    data = fh.read()
            except Exception:
                continue
            total += len(data)
            relp = os.path.relpath(fp, base).replace(os.sep, "/")
            files[dest_prefix.rstrip("/") + "/" + relp] = data
    if files:
        fs = getattr(getattr(eng, "_os", None), "fs", None)
        if fs is not None and hasattr(fs, "add_file"):
            for vp, blob in files.items():
                try:
                    fs.add_file(vp, blob)
                except Exception:
                    pass
        try:
            osv.vfs_mount(eng, files)
        except Exception:
            if fs is None:
                return 0
    return len(files)
