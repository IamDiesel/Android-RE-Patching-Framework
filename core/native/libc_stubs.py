# -*- coding: utf-8 -*-
"""core.native.libc_stubs — Generische Standard-libc-/Bionic-/pthread-FFI-Handler fuer arm64mini.

Zielagnostisch und IP-neutral: stellt beim Ausfuehren beliebiger Android-ARM64-`.so`-Bibliotheken
(z. B. `DT_INIT_ARRAY`, `JNI_OnLoad`, JNI-Calls) eine funktionsfaehige Basis-C-/C++-Laufzeit bereit,
ohne dass existierende Custom- oder Plugin-Handler ueberschrieben werden.

Speicherlayout (disjunkt zu JNIEnv/SCRATCH/ThreadStack/MMAP/MODULE_ARENA):
  * TLS_BASE       = 0x60_0000_0000  (4 KiB TLS-Seite fuer TPIDR_EL0, Stack-Canary @ +0x28, errno @ +0x40)
  * LIBC_HEAP_BASE = 0x61_0000_0000 .. 0x6F_0000_0000  (Bump-Allokator fuer malloc/calloc/realloc)
"""
from __future__ import annotations

import struct
from typing import Dict

_M64 = (1 << 64) - 1
_M32 = 0xFFFFFFFF

TLS_BASE = 0x60_0000_0000
TLS_CANARY_OFF = 0x28
TLS_ERRNO_OFF = 0x40
TLS_DEFAULT_CANARY = 0x595443414E415259  # "YTRANACY" (non-zero stack protector canary)

LIBC_HEAP_BASE = 0x61_0000_0000
LIBC_HEAP_LIMIT = 0x6F_0000_0000
DEFAULT_STACK_TOP = 0x7F_FFF0_0000

_MAX_COPY = 32 * 1024 * 1024
_MAX_CSTR = 256 * 1024


def ensure_tls_page(eng) -> int:
    """Initialisiert TPIDR_EL0 (TLS-Basis mit Canary + errno) und Stack-Pointer defensiv."""
    cpu = getattr(eng, "cpu", None)
    if cpu is None:
        return TLS_BASE
    tls = int(getattr(cpu, "tpidr_el0", 0) or 0) & _M64
    if tls == 0:
        tls = TLS_BASE
        cpu.tpidr_el0 = tls
    # Stack-Canary (@+0x28 auf AArch64-Bionic/glibc) und errno (@+0x40) einmalig seeden
    try:
        cur_canary = int.from_bytes(cpu.m.rb(tls + TLS_CANARY_OFF, 8), "little")
        if cur_canary == 0:
            cpu.m.wb(tls + TLS_CANARY_OFF, TLS_DEFAULT_CANARY.to_bytes(8, "little"))
        cur_tp_self = int.from_bytes(cpu.m.rb(tls, 8), "little")
        if cur_tp_self == 0:
            cpu.m.wb(tls, tls.to_bytes(8, "little"))
    except Exception:
        pass
    if (int(getattr(cpu, "sp", 0) or 0) & _M64) == 0:
        cpu.sp = DEFAULT_STACK_TOP
    return tls


def _read_cstr(cpu, addr: int, max_len: int = _MAX_CSTR) -> bytes:
    """Liest nullterminierte C-String-Bytes chunk-weise aus `cpu.m`."""
    a = int(addr) & _M64
    if a == 0:
        return b""
    out = bytearray()
    while len(out) < max_len:
        chunk = bytes(cpu.m.rb(a + len(out), min(256, max_len - len(out))))
        z = chunk.find(0)
        if z >= 0:
            out.extend(chunk[:z])
            break
        out.extend(chunk)
    return bytes(out)


def _libc_alloc(eng, size: int, zero: bool = False, align: int = 16) -> int:
    cur = int(getattr(eng, "_libc_heap", 0) or 0)
    if cur < LIBC_HEAP_BASE or cur >= LIBC_HEAP_LIMIT:
        cur = LIBC_HEAP_BASE
    a_mask = max(16, int(align)) - 1
    ptr = (cur + a_mask) & ~a_mask
    sz = max(1, min(int(size), _MAX_COPY))
    nxt = (ptr + sz + 0xF) & ~0xF
    if nxt >= LIBC_HEAP_LIMIT:
        return 0
    eng._libc_heap = nxt
    allocs: Dict[int, int] = getattr(eng, "_libc_allocs", None)
    if allocs is None:
        allocs = {}
        eng._libc_allocs = allocs
    allocs[ptr] = sz
    if zero and sz > 0:
        try:
            eng.cpu.m.wb(ptr, b"\x00" * sz)
        except Exception:
            pass
    return ptr


def _u64_to_signed(v: int) -> int:
    v &= _M64
    return v - (1 << 64) if (v & (1 << 63)) else v


def install_libc_stubs(eng, ffi) -> int:
    """Registriert generische libc-/C++-/pthread-Handler auf `ffi` fuer `eng`.

    Ueberschreibt NIEMALS bereits registrierte Custom-/Plugin-Handler.
    Rueckgabe: Anzahl neu registrierter Handler.
    """
    ensure_tls_page(eng)
    st = ffi._st(eng)
    added = 0

    def _reg(name: str, fn) -> None:
        nonlocal added
        if name not in st.handlers:
            ffi.set_handler(eng, name, fn)
            added += 1

    # ---- Heap / C++ Operatoren -------------------------------------------
    def _malloc(cpu, _ffi):
        return _libc_alloc(eng, int(cpu.x[0]) & _M64, zero=False)

    def _calloc(cpu, _ffi):
        nmemb = int(cpu.x[0]) & _M64
        size = int(cpu.x[1]) & _M64
        total = min(nmemb * size, _MAX_COPY)
        return _libc_alloc(eng, total, zero=True)

    def _realloc(cpu, _ffi):
        old_p = int(cpu.x[0]) & _M64
        new_sz = int(cpu.x[1]) & _M64
        if new_sz == 0:
            return 0
        if old_p == 0:
            return _libc_alloc(eng, new_sz, zero=False)
        allocs = getattr(eng, "_libc_allocs", {}) or {}
        old_sz = allocs.get(old_p, 0)
        if old_sz and new_sz <= old_sz:
            return old_p
        new_p = _libc_alloc(eng, new_sz, zero=False)
        if new_p and old_sz:
            try:
                copy_n = min(old_sz, new_sz, _MAX_COPY)
                if copy_n > 0:
                    cpu.m.wb(new_p, cpu.m.rb(old_p, copy_n))
            except Exception:
                pass
        return new_p

    def _free(cpu, _ffi):
        return 0

    def _posix_memalign(cpu, _ffi):
        memptr = int(cpu.x[0]) & _M64
        align = max(8, int(cpu.x[1]) & _M64)
        size = int(cpu.x[2]) & _M64
        p = _libc_alloc(eng, size, zero=False, align=align)
        if not p:
            return 12  # ENOMEM
        if memptr:
            cpu.m.wb(memptr, p.to_bytes(8, "little"))
        return 0

    def _memalign(cpu, _ffi):
        return _libc_alloc(eng, int(cpu.x[1]) & _M64, zero=False, align=max(16, int(cpu.x[0]) & _M64))

    def _aligned_alloc(cpu, _ffi):
        return _libc_alloc(eng, int(cpu.x[1]) & _M64, zero=False, align=max(16, int(cpu.x[0]) & _M64))

    for nm, fn in (
        ("malloc", _malloc),
        ("calloc", _calloc),
        ("realloc", _realloc),
        ("free", _free),
        ("posix_memalign", _posix_memalign),
        ("memalign", _memalign),
        ("aligned_alloc", _aligned_alloc),
        ("_Znwm", _malloc),
        ("_Znam", _malloc),
        ("_ZdlPv", _free),
        ("_ZdaPv", _free),
        ("_ZdlPvm", _free),
        ("_ZdaPvm", _free),
    ):
        _reg(nm, fn)

    # ---- Speicher / Strings ----------------------------------------------
    def _memcpy(cpu, _ffi):
        dst = int(cpu.x[0]) & _M64
        src = int(cpu.x[1]) & _M64
        n = min(int(cpu.x[2]) & _M64, _MAX_COPY)
        if dst and src and n > 0:
            cpu.m.wb(dst, cpu.m.rb(src, n))
        return dst

    def _memset(cpu, _ffi):
        dst = int(cpu.x[0]) & _M64
        ch = int(cpu.x[1]) & 0xFF
        n = min(int(cpu.x[2]) & _M64, _MAX_COPY)
        if dst and n > 0:
            cpu.m.wb(dst, bytes((ch,)) * n)
        return dst

    def _memcmp(cpu, _ffi):
        p1 = int(cpu.x[0]) & _M64
        p2 = int(cpu.x[1]) & _M64
        n = min(int(cpu.x[2]) & _M64, _MAX_COPY)
        if n == 0 or p1 == p2:
            return 0
        b1 = cpu.m.rb(p1, n)
        b2 = cpu.m.rb(p2, n)
        if b1 == b2:
            return 0
        for x, y in zip(b1, b2):
            if x != y:
                return (1 if x > y else -1) & _M64
        return 0

    def _memchr(cpu, _ffi):
        p = int(cpu.x[0]) & _M64
        ch = int(cpu.x[1]) & 0xFF
        n = min(int(cpu.x[2]) & _M64, _MAX_COPY)
        if not p or n == 0:
            return 0
        idx = cpu.m.rb(p, n).find(ch)
        return (p + idx) & _M64 if idx >= 0 else 0

    def _strlen(cpu, _ffi):
        return len(_read_cstr(cpu, cpu.x[0]))

    def _strnlen(cpu, _ffi):
        maxlen = min(int(cpu.x[1]) & _M64, _MAX_CSTR)
        return len(_read_cstr(cpu, cpu.x[0], max_len=maxlen))

    def _strcmp(cpu, _ffi):
        s1 = _read_cstr(cpu, cpu.x[0])
        s2 = _read_cstr(cpu, cpu.x[1])
        if s1 == s2:
            return 0
        return (1 if s1 > s2 else -1) & _M64

    def _strncmp(cpu, _ffi):
        n = min(int(cpu.x[2]) & _M64, _MAX_CSTR)
        if n == 0:
            return 0
        s1 = _read_cstr(cpu, cpu.x[0], max_len=n)[:n]
        s2 = _read_cstr(cpu, cpu.x[1], max_len=n)[:n]
        if s1 == s2:
            return 0
        return (1 if s1 > s2 else -1) & _M64

    def _strcpy(cpu, _ffi):
        dst = int(cpu.x[0]) & _M64
        raw = _read_cstr(cpu, cpu.x[1]) + b"\x00"
        if dst:
            cpu.m.wb(dst, raw)
        return dst

    def _strncpy(cpu, _ffi):
        dst = int(cpu.x[0]) & _M64
        n = min(int(cpu.x[2]) & _M64, _MAX_CSTR)
        if dst and n > 0:
            raw = _read_cstr(cpu, cpu.x[1], max_len=n)[:n]
            buf = raw + b"\x00" * max(0, n - len(raw))
            cpu.m.wb(dst, buf)
        return dst

    def _strchr(cpu, _ffi):
        p = int(cpu.x[0]) & _M64
        ch = int(cpu.x[1]) & 0xFF
        raw = _read_cstr(cpu, p)
        if ch == 0:
            return (p + len(raw)) & _M64 if p else 0
        idx = raw.find(ch)
        return (p + idx) & _M64 if (p and idx >= 0) else 0

    def _strrchr(cpu, _ffi):
        p = int(cpu.x[0]) & _M64
        ch = int(cpu.x[1]) & 0xFF
        raw = _read_cstr(cpu, p)
        if ch == 0:
            return (p + len(raw)) & _M64 if p else 0
        idx = raw.rfind(ch)
        return (p + idx) & _M64 if (p and idx >= 0) else 0

    def _strstr(cpu, _ffi):
        p = int(cpu.x[0]) & _M64
        hay = _read_cstr(cpu, p)
        ndl = _read_cstr(cpu, cpu.x[1])
        if not ndl:
            return p
        idx = hay.find(ndl)
        return (p + idx) & _M64 if (p and idx >= 0) else 0

    def _strdup(cpu, _ffi):
        raw = _read_cstr(cpu, cpu.x[0]) + b"\x00"
        p = _libc_alloc(eng, len(raw))
        if p:
            cpu.m.wb(p, raw)
        return p

    def _strndup(cpu, _ffi):
        n = min(int(cpu.x[1]) & _M64, _MAX_CSTR)
        raw = _read_cstr(cpu, cpu.x[0], max_len=n)[:n] + b"\x00"
        p = _libc_alloc(eng, len(raw))
        if p:
            cpu.m.wb(p, raw)
        return p

    for nm, fn in (
        ("memcpy", _memcpy),
        ("__memcpy_chk", _memcpy),
        ("memmove", _memcpy),
        ("__memmove_chk", _memcpy),
        ("memset", _memset),
        ("__memset_chk", _memset),
        ("memcmp", _memcmp),
        ("memchr", _memchr),
        ("strlen", _strlen),
        ("__strlen_chk", _strlen),
        ("strnlen", _strnlen),
        ("strcmp", _strcmp),
        ("strncmp", _strncmp),
        ("strcpy", _strcpy),
        ("__strcpy_chk", _strcpy),
        ("strncpy", _strncpy),
        ("strchr", _strchr),
        ("strrchr", _strrchr),
        ("strstr", _strstr),
        ("strdup", _strdup),
        ("strndup", _strndup),
    ):
        _reg(nm, fn)

    # ---- C++ Guard / pthread / Laufzeit ----------------------------------
    def _cxa_guard_acquire(cpu, _ffi):
        gptr = int(cpu.x[0]) & _M64
        if not gptr:
            return 0
        flag = cpu.m.rb(gptr, 1)[0]
        if (flag & 1) == 0:
            cpu.m.wb(gptr, b"\x02")
            return 1
        return 0

    def _cxa_guard_release(cpu, _ffi):
        gptr = int(cpu.x[0]) & _M64
        if gptr:
            cpu.m.wb(gptr, b"\x01")
        return 0

    def _cxa_guard_abort(cpu, _ffi):
        gptr = int(cpu.x[0]) & _M64
        if gptr:
            cpu.m.wb(gptr, b"\x00")
        return 0

    def _pthread_once(cpu, _ffi):
        once_ptr = int(cpu.x[0]) & _M64
        init_fn = int(cpu.x[1]) & _M64
        if not once_ptr:
            return 0
        cur = int.from_bytes(cpu.m.rb(once_ptr, 4), "little")
        if cur != 0:
            return 0
        cpu.m.wb(once_ptr, struct.pack("<I", 2))
        if init_fn:
            saved_x = list(cpu.x)
            saved_sp = int(cpu.sp)
            saved_lr = int(cpu.lr)
            saved_pc = int(getattr(cpu, "pc", 0) or 0)
            saved_flags = (cpu.n, cpu.z, cpu.c, cpu.vf)
            saved_stopped = bool(getattr(cpu, "stopped", False))
            saved_reason = getattr(cpu, "stop_reason", None)
            try:
                cpu.run(init_fn, (), maxins=50_000, max_wall_s=0.25)
            except Exception:
                pass
            finally:
                cpu.x[:] = saved_x
                cpu.sp = saved_sp
                cpu.lr = saved_lr
                cpu.pc = saved_pc
                cpu.n, cpu.z, cpu.c, cpu.vf = saved_flags
                cpu.stopped = saved_stopped
                cpu.stop_reason = saved_reason
        return 0

    def _pthread_key_create(cpu, _ffi):
        kptr = int(cpu.x[0]) & _M64
        nxt = int(getattr(eng, "_tls_next_key", 1) or 1)
        eng._tls_next_key = nxt + 1
        keys = getattr(eng, "_tls_keys", None)
        if keys is None:
            keys = {}
            eng._tls_keys = keys
        keys.setdefault(nxt, 0)
        if kptr:
            cpu.m.wb(kptr, struct.pack("<I", nxt & _M32))
        return 0

    def _pthread_key_delete(cpu, _ffi):
        k = int(cpu.x[0]) & _M32
        keys = getattr(eng, "_tls_keys", None)
        if isinstance(keys, dict):
            keys.pop(k, None)
        return 0

    def _pthread_setspecific(cpu, _ffi):
        k = int(cpu.x[0]) & _M32
        v = int(cpu.x[1]) & _M64
        keys = getattr(eng, "_tls_keys", None)
        if keys is None:
            keys = {}
            eng._tls_keys = keys
        keys[k] = v
        return 0

    def _pthread_getspecific(cpu, _ffi):
        k = int(cpu.x[0]) & _M32
        keys = getattr(eng, "_tls_keys", None) or {}
        return int(keys.get(k, 0)) & _M64

    def _pthread_create(cpu, _ffi):
        tid_ptr = int(cpu.x[0]) & _M64
        nxt = int(getattr(eng, "_libc_next_tid", 2001) or 2001)
        eng._libc_next_tid = nxt + 1
        if tid_ptr:
            cpu.m.wb(tid_ptr, nxt.to_bytes(8, "little"))
        return 0

    def _errno_fn(cpu, _ffi):
        tls = ensure_tls_page(eng)
        return (tls + TLS_ERRNO_OFF) & _M64

    def _sysconf(cpu, _ffi):
        name = _u64_to_signed(cpu.x[0])
        if name in (30, 39):        # _SC_PAGESIZE (glibc=30, bionic=39)
            return 4096
        if name in (83, 84, 96, 97):  # _SC_NPROCESSORS_CONF / _ONLN
            return 4
        return 0

    def _cfg_val(key: str, default: int) -> int:
        os_ = getattr(eng, "_os", None)
        cfg = getattr(os_, "config", None) or {}
        try:
            return int(cfg.get(key, default)) & _M64
        except Exception:
            return int(default) & _M64

    def _make_stop(reason: str):
        def _stop_fn(cpu, _ffi):
            cpu.stopped = True
            cpu.stop_reason = reason
            return 0
        return _stop_fn

    def _noop_zero(cpu, _ffi):
        return 0

    def _sys_prop_get(cpu, _ffi):
        nm = _read_cstr(cpu, int(cpu.x[0])).decode("utf-8", errors="replace")
        dst = int(cpu.x[1]) & _M64
        os_st = getattr(eng, "_os", None)
        cfg = (getattr(os_st, "config", None) or {}) if os_st is not None else {}
        custom = cfg.get("sysprops") if isinstance(cfg.get("sysprops"), dict) else {}
        defaults = {
            "ro.build.version.sdk": "34",
            "ro.build.version.release": "14",
            "ro.product.model": "Pixel 8",
            "ro.product.manufacturer": "Google",
            "ro.product.cpu.abi": "arm64-v8a",
            "ro.hardware": "ranchu",
            "ro.debuggable": "0",
        }
        val = str(custom.get(nm, defaults.get(nm, "")))
        enc = val.encode("utf-8", errors="replace")[:91]
        if dst:
            cpu.m.wb(dst, enc + b"\x00")
        return len(enc)

    def _android_log_write(cpu, _ffi):
        prio = int(cpu.x[0]) & 0xFFFFFFFF
        tag = _read_cstr(cpu, int(cpu.x[1])).decode("utf-8", errors="replace")
        msg = _read_cstr(cpu, int(cpu.x[2])).decode("utf-8", errors="replace")
        logs = getattr(eng, "_android_logs", None)
        if logs is None:
            logs = []
            eng._android_logs = logs
        logs.append({"prio": prio, "tag": tag, "msg": msg})
        return len(msg)

    def _getauxval(cpu, _ffi):
        t = int(cpu.x[0]) & _M64
        if t == 6:    # AT_PAGESZ
            return 4096
        if t == 16:   # AT_HWCAP
            return 0xFF
        if t == 25:   # AT_RANDOM
            p = _libc_alloc(eng, 16, zero=False)
            if p:
                cpu.m.wb(p, b"\x42\x13\x37\xab" * 4)
            return p
        return 0

    def _arc4random(cpu, _ffi):
        seq = (int(getattr(eng, "_rng_seq", 0)) + 1) & 0xFFFFFFFF
        eng._rng_seq = seq
        return ((seq * 0x9E3779B1) ^ 0xA5A55A5A) & 0xFFFFFFFF

    def _arc4random_buf(cpu, _ffi):
        dst = int(cpu.x[0]) & _M64
        n = min(int(cpu.x[1]) & _M64, _MAX_COPY)
        if dst and n > 0:
            seq = (int(getattr(eng, "_rng_seq", 0)) + 1) & 0xFFFFFFFF
            eng._rng_seq = seq
            seed_b = bytes(((seq + i * 37) & 0xFF) for i in range(min(n, 256)))
            raw = (seed_b * ((n // max(1, len(seed_b))) + 1))[:n]
            cpu.m.wb(dst, raw)
        return 0

    def _getentropy(cpu, _ffi):
        _arc4random_buf(cpu, _ffi)
        return 0

    for nm, fn in (
        ("__cxa_guard_acquire", _cxa_guard_acquire),
        ("__cxa_guard_release", _cxa_guard_release),
        ("__cxa_guard_abort", _cxa_guard_abort),
        ("__cxa_atexit", _noop_zero),
        ("__cxa_finalize", _noop_zero),
        ("atexit", _noop_zero),
        ("pthread_once", _pthread_once),
        ("pthread_mutex_init", _noop_zero),
        ("pthread_mutex_lock", _noop_zero),
        ("pthread_mutex_trylock", _noop_zero),
        ("pthread_mutex_unlock", _noop_zero),
        ("pthread_mutex_destroy", _noop_zero),
        ("pthread_mutexattr_init", _noop_zero),
        ("pthread_mutexattr_settype", _noop_zero),
        ("pthread_mutexattr_destroy", _noop_zero),
        ("pthread_rwlock_init", _noop_zero),
        ("pthread_rwlock_rdlock", _noop_zero),
        ("pthread_rwlock_wrlock", _noop_zero),
        ("pthread_rwlock_unlock", _noop_zero),
        ("pthread_rwlock_destroy", _noop_zero),
        ("pthread_cond_init", _noop_zero),
        ("pthread_cond_wait", _noop_zero),
        ("pthread_cond_signal", _noop_zero),
        ("pthread_cond_broadcast", _noop_zero),
        ("pthread_cond_destroy", _noop_zero),
        ("pthread_attr_init", _noop_zero),
        ("pthread_attr_setdetachstate", _noop_zero),
        ("pthread_attr_destroy", _noop_zero),
        ("pthread_detach", _noop_zero),
        ("pthread_join", _noop_zero),
        ("pthread_key_create", _pthread_key_create),
        ("pthread_key_delete", _pthread_key_delete),
        ("pthread_setspecific", _pthread_setspecific),
        ("pthread_getspecific", _pthread_getspecific),
        ("pthread_self", lambda _c, _f: _cfg_val("tid", 1234)),
        ("pthread_create", _pthread_create),
        ("__errno", _errno_fn),
        ("__errno_location", _errno_fn),
        ("getpagesize", lambda _c, _f: 4096),
        ("sysconf", _sysconf),
        ("getpid", lambda _c, _f: _cfg_val("pid", 1234)),
        ("gettid", lambda _c, _f: _cfg_val("tid", 1234)),
        ("getuid", lambda _c, _f: _cfg_val("uid", 10234)),
        ("geteuid", lambda _c, _f: _cfg_val("uid", 10234)),
        ("__system_property_get", _sys_prop_get),
        ("getauxval", _getauxval),
        ("arc4random", _arc4random),
        ("arc4random_buf", _arc4random_buf),
        ("getentropy", _getentropy),
        ("abort", _make_stop("abort")),
        ("__stack_chk_fail", _make_stop("stack_chk_fail")),
        ("_exit", _make_stop("_exit")),
        ("exit", _make_stop("exit")),
        ("dl_iterate_phdr", _noop_zero),
        ("__android_log_print", _android_log_write),
        ("__android_log_write", _android_log_write),
        ("__android_log_vprint", _android_log_write),
    ):
        _reg(nm, fn)

    return added
