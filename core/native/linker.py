# -*- coding: utf-8 -*-
"""core.native.linker — Multi-SO-Linker (`LinkerSpace`) fuer arm64mini.

Loest das fundamentale Adresskollisions-Problem bei mehreren ELF64/AArch64-`ET_DYN`-Libraries
(`vaddr = 0x0`): jede geladene `.so` erhaelt eine eigene 1-MiB-ausgerichtete Basisadresse
(`load_bias`), ihre `PT_LOAD`-Segmente, Relocations, Exporte, Imports, `DT_INIT`/`DT_INIT_ARRAY`
und `JNI_OnLoad` werden sauber reloziert, und `/proc/self/maps` im OS-Substrat spiegelt alle
geladenen Module realistisch wider.

Zielagnostisch und IP-neutral.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_M64 = (1 << 64) - 1

# Zwischen LIBC_HEAP (0x61_0000_0000..0x6F_0000_0000) und DEFAULT_STACK_TOP (0x7F_FFF0_0000).
MODULE_ARENA_BASE = 0x70_0000_0000
_ALIGN_1M = 0x10_0000
_GUARD_GAP = 0x10_0000


def _align_up(val: int, align: int) -> int:
    return (int(val) + align - 1) & ~(align - 1)


@dataclass
class LoadedModule:
    name: str
    path: str
    sha256: str
    load_bias: int
    base: int
    end: int
    image: Any
    init_done: bool = False
    onload_done: bool = False
    onload_ret: Optional[int] = None
    init_stats: Dict[str, Any] = field(default_factory=dict)
    registered_count: int = 0
    static_jni_count: int = 0
    symbols: Dict[str, int] = field(default_factory=dict)
    segments: List[Tuple[int, int, str]] = field(default_factory=list)


class LinkerSpace:
    """Verwaltet alle in einer Engine geladenen nativen Shared Libraries."""

    def __init__(self, arena_base: int = MODULE_ARENA_BASE):
        self.next_base: int = int(arena_base)
        self.modules: List[LoadedModule] = []
        self.by_sha: Dict[str, LoadedModule] = {}
        self.by_name: Dict[str, LoadedModule] = {}
        self.by_path: Dict[str, LoadedModule] = {}
        self.global_syms: Dict[str, int] = {}

    def find_by_sha(self, sha256: str) -> Optional[LoadedModule]:
        return self.by_sha.get(sha256) if sha256 else None

    def find_by_name(self, name: str) -> Optional[LoadedModule]:
        return self.by_name.get(name) if name else None

    def find_by_path(self, path: str) -> Optional[LoadedModule]:
        return self.by_path.get(path) if path else None

    def alloc_bias(self, image) -> int:
        segs = getattr(image, "segments", None) or []
        if segs:
            min_v = min(int(getattr(s, "vaddr", 0) or 0) for s in segs)
            max_v = max(
                int(getattr(s, "vaddr", 0) or 0)
                + max(int(getattr(s, "memsz", 0) or 0), int(getattr(s, "filesz", 0) or 0), 0x1000)
                for s in segs
            )
        else:
            min_v, max_v = 0, 0x10000
        adj_min = min_v & ~(_ALIGN_1M - 1)
        bias = (self.next_base - adj_min) & _M64
        span = max(_ALIGN_1M, _align_up(max_v - adj_min, _ALIGN_1M))
        self.next_base = self.next_base + span + _GUARD_GAP
        return bias

    def resolve_symbol(self, name: str) -> int:
        return int(self.global_syms.get(name, 0) or 0)

    def format_proc_maps(self) -> str:
        lines: List[str] = []
        for mod in self.modules:
            label = mod.path or (f"/data/app/lib/arm64/{mod.name}" if mod.name else "/data/local/tmp/lib.so")
            for start, end, perms in mod.segments:
                lines.append(f"{start:012x}-{end:012x} {perms} 00000000 00:00 0 {label}")
        lines.append("6100000000-6101000000 rw-p 00000000 00:00 0 [anon:libc_malloc]")
        lines.append("7ffff00000-7ffffff000 rw-p 00000000 00:00 0 [stack]")
        return "\n".join(lines) + "\n"

    def sync_proc_maps(self, eng) -> None:
        os_ = getattr(eng, "_os", None)
        env = getattr(os_, "env", None) if os_ is not None else None
        if env is not None and hasattr(env, "set_maps"):
            try:
                env.set_maps(self.format_proc_maps())
            except Exception:
                pass

    def load_module(
        self,
        svc,
        eng,
        image,
        *,
        name: str = "",
        path: str = "",
        apply_relocs: bool = True,
        ffi=None,
        load_bias: Optional[int] = None,
    ) -> LoadedModule:
        data = bytes(getattr(image, "data", b"") or b"")
        sha = hashlib.sha256(data).hexdigest() if data else ""
        if sha and sha in self.by_sha:
            mod = self.by_sha[sha]
            if path:
                self.by_path.setdefault(path, mod)
            if name:
                self.by_name.setdefault(name, mod)
            return mod
        if not sha and path and path in self.by_path:
            return self.by_path[path]

        from core.native.libc_stubs import ensure_tls_page, install_libc_stubs

        ensure_tls_page(eng)
        bias = self.alloc_bias(image) if load_bias is None else (int(load_bias) & _M64)
        try:
            setattr(image, "load_bias", bias)
        except Exception:
            pass

        relocs = svc.load_image(eng, image, apply_relocs=apply_relocs, load_bias=bias)

        syms: Dict[str, int] = {}
        for nm, sym in (getattr(image, "dynsyms", None) or {}).items():
            try:
                shndx = getattr(sym, "shndx", 1)
                val = int(getattr(sym, "value", 0) or 0)
            except Exception:
                continue
            if shndx != 0 and val != 0:
                eff = (bias + val) & _M64
                syms[nm] = eff
                self.global_syms.setdefault(nm, eff)

        wired = 0
        if ffi is not None:
            install_libc_stubs(eng, ffi)
            try:
                w = ffi.wire_imports(
                    eng, image, load_bias=bias, symbol_resolver=self.resolve_symbol
                )
                wired = int(w.get("count", 0)) if isinstance(w, dict) else 0
            except Exception:
                wired = 0

        segs_out: List[Tuple[int, int, str]] = []
        raw_segs = getattr(image, "segments", None) or []
        for s in raw_segs:
            start = (int(getattr(s, "vaddr", 0) or 0) + bias) & _M64
            sz = max(int(getattr(s, "memsz", 0) or 0), int(getattr(s, "filesz", 0) or 0), 0x1000)
            end = _align_up(start + sz, 0x1000)
            perms = s.perm_str() + "p" if hasattr(s, "perm_str") else "r-xp"
            segs_out.append((start, end, perms))
        if not segs_out:
            segs_out.append((bias, bias + 0x10000, "r-xp"))

        mod_base = min(st for st, _, _ in segs_out)
        mod_end = max(en for _, en, _ in segs_out)
        mod_name = name or (os.path.basename(path) if path else f"module_{len(self.modules)}")
        mod = LoadedModule(
            name=mod_name,
            path=path or "",
            sha256=sha,
            load_bias=bias,
            base=mod_base,
            end=mod_end,
            image=image,
            init_stats={"relocs": relocs, "wired": wired},
            symbols=syms,
            segments=segs_out,
        )
        self.modules.append(mod)
        if sha:
            self.by_sha[sha] = mod
        if mod_name:
            self.by_name[mod_name] = mod
        if path:
            self.by_path[path] = mod
        self.sync_proc_maps(eng)
        return mod


def get_linker(eng) -> LinkerSpace:
    lnk = getattr(eng, "_linker", None)
    if lnk is None:
        lnk = LinkerSpace()
        eng._linker = lnk
    return lnk
