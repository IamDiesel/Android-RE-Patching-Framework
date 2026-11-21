# -*- coding: utf-8 -*-
"""core.java.fusion_loader — Vereinheitlichter Java<->Native Fusion-Loader.

Buendelt das bisher in `GlassAppRunner` und `DebuggerController` duplizierte Laden nativer
Bibliotheken (`System.loadLibrary`, `System.load`, VFS-Read inkl. `/proc/self/fd/N` & Retained-Blobs,
Self-Cache-Lookup/-Speicherung und Magic-Mismatch-Diagnose) an EINER einzigen Stelle.
"""
from __future__ import annotations

import glob
import os
import re
import time
from typing import Callable, Optional

_FD_RE = re.compile(r"^/proc/(?:self|\d+)/(?:task/\d+/)?fd/(\d+)$")
_SO_TMP_RE = re.compile(r"^(.*\.so)(?:_\d+)?$")
_SYSTEM_SONAMES = frozenset({
    "libc.so", "libm.so", "libdl.so", "liblog.so", "libz.so",
    "libandroid.so", "libstdc++.so", "libc++.so", "libc++_shared.so",
    "libEGL.so", "libGLESv1_CM.so", "libGLESv2.so", "libGLESv3.so",
    "libvulkan.so", "libjnigraphics.so", "libOpenSLES.so", "libmediandk.so",
    "libaaudio.so", "libcamera2ndk.so", "libnativewindow.so", "libsync.so",
})


def _safe_pkg(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "").strip())
    return clean.strip("._") or "default.app"


class NativeFusionLoader:
    """Einheitlicher Loader fuer gebundene (loadLibrary) und entpackte (System.load/dlopen) Libs."""

    def __init__(
        self,
        *,
        svc,
        eng,
        jni,
        table,
        ffi=None,
        stubs=None,
        app_root: str = "",
        pkg: str = "com.example.app",
        base_dir: str = ".",
        seam_config=None,
        log_fn: Optional[Callable[[str], None]] = None,
        max_total_init_wall_s: float = 5.0,
    ):
        self.svc = svc
        self.eng = eng
        self.jni = jni
        self.table = table
        self.ffi = ffi
        self.stubs = stubs
        self.app_root = app_root or ""
        self.pkg = pkg or "com.example.app"
        self.base_dir = os.path.abspath(base_dir or ".")
        self.seam_config = seam_config
        self.log_fn = log_fn or (lambda _m: None)
        self.max_total_init_wall_s = float(max_total_init_wall_s)
        self._native_init_spent_s: float = 0.0
        if self.eng is not None and self.ffi is not None:
            try:
                from core.native.boringssl_seam import install_boringssl_seam
                install_boringssl_seam(self.eng, self.ffi, stubs=self.stubs, log_fn=self.log_fn)
            except Exception:
                pass
            try:
                from core.java.net_protocols_g7 import install_libcurl_seam
                install_libcurl_seam(self.eng, self.ffi, stubs=self.stubs, log_fn=self.log_fn)
            except Exception:
                pass

    @staticmethod
    def vfs_resolve_name(eng, path: str) -> str:
        """Loest `/proc/.../fd/<N>` ueber `fs._fd_paths` wieder zum echten Lib-Namen auf (ohne `_0`)."""
        p = str(path or "")
        m = _FD_RE.match(p)
        if m:
            os_ = getattr(eng, "_os", None)
            fs = getattr(os_, "fs", None) if os_ is not None else None
            if fs is not None:
                fd_paths = getattr(fs, "_fd_paths", None) or {}
                orig = fd_paths.get(int(m.group(1)))
                if orig:
                    p = str(orig)
        base = os.path.basename(p)
        m2 = _SO_TMP_RE.match(base)
        if m2:
            return m2.group(1)
        return base or f"lib_fd_{m.group(1)}.so" if m else base

    def _budget_kwargs(self) -> dict:
        if self.max_total_init_wall_s <= 0:
            return {"lazy_init": True}
        rem = max(0.0, self.max_total_init_wall_s - self._native_init_spent_s)
        if rem <= 0.05:
            return {"lazy_init": True}
        return {
            "lazy_init": False,
            "init_wall_s": min(1.0, rem * 0.45),
            "onload_wall_s": min(1.5, rem * 0.55),
        }

    @staticmethod
    def vfs_read(eng, path: str) -> Optional[bytes]:
        """Liest Bytes aus VFS 2.0, `/proc/self/.../fd/<N>`, `_retained_files` oder Legacy-`os_.vfs`."""
        os_ = getattr(eng, "_os", None)
        if os_ is None:
            return None
        fs = getattr(os_, "fs", None)
        if fs is not None:
            lookup = getattr(fs, "lookup", None)
            read = getattr(fs, "read", None)
            if callable(lookup) and callable(read):
                try:
                    ino = lookup(path)
                    if ino is not None:
                        data = read(ino)
                        if data:
                            return bytes(data)
                except Exception:
                    pass
            for meth in ("read_file", "get"):
                fn = getattr(fs, meth, None)
                if callable(fn):
                    try:
                        d = fn(path)
                        if d:
                            return bytes(d)
                    except Exception:
                        pass
            m = _FD_RE.match(str(path or ""))
            if m:
                try:
                    fdb = (getattr(fs, "_fd_blobs", None) or {}).get(int(m.group(1)))
                    if fdb:
                        return bytes(fdb)
                except Exception:
                    pass
            try:
                ret = (getattr(fs, "_retained_files", None) or {}).get(path)
                if ret:
                    return bytes(ret)
            except Exception:
                pass
        vfs = getattr(os_, "vfs", None)
        if isinstance(vfs, dict) and path in vfs and vfs[path] is not None:
            return bytes(vfs[path])
        return None

    def _selfcache_dir(self) -> str:
        sc_dir = getattr(self.seam_config, "self_cache_dir", "data/vfs_selfcache") if self.seam_config else "data/vfs_selfcache"
        if not os.path.isabs(sc_dir):
            sc_dir = os.path.join(self.base_dir, sc_dir)
        return os.path.join(sc_dir, _safe_pkg(self.pkg))

    def _onload_cache_dir(self) -> Optional[str]:
        if self.seam_config is None or self.seam_config.unpack_mode != "self_cache":
            return None
        return os.path.join(self._selfcache_dir(), "_onload_cache")

    def find_selfcache_so(self, lib_name: str) -> Optional[str]:
        if self.seam_config is None or self.seam_config.unpack_mode != "self_cache":
            return None
        d = self._selfcache_dir()
        for fn in (f"lib{lib_name}.so", lib_name, f"{lib_name}.so"):
            cand = os.path.join(d, fn)
            if os.path.isfile(cand):
                return cand
        return None

    def find_unpacked_twin(self, packed_path: str) -> Optional[str]:
        mode = getattr(self.seam_config, "unpack_mode", "self_cache") if self.seam_config else "self_cache"
        if mode == "self_cache":
            cand = os.path.join(self._selfcache_dir(), os.path.basename(packed_path))
            if os.path.isfile(cand):
                return cand
        if not (self.seam_config is not None and getattr(self.seam_config, "allow_external_dec_cache", False)):
            return None
        try:
            from core.native.unpack import sha8
            sha = sha8(packed_path)
        except Exception:
            return None
        pats = [
            os.path.join(self.base_dir, "data", "native", "*", "lib", sha, "*.dec.so"),
            os.path.join(self.base_dir, "data", "native", "*", "lib", sha, "*.so"),
        ]
        for pat in pats:
            for cand in sorted(glob.glob(pat)):
                if os.path.isfile(cand):
                    return cand
        return None

    def save_selfcache_so(self, filename: str, data: bytes) -> None:
        if not data or self.seam_config is None or self.seam_config.unpack_mode != "self_cache":
            return
        dst_dir = self._selfcache_dir()
        os.makedirs(dst_dir, exist_ok=True)
        try:
            with open(os.path.join(dst_dir, os.path.basename(filename)), "wb") as fh:
                fh.write(bytes(data))
        except Exception:
            pass

    def _should_run_onload(self, lib_name: str) -> bool:
        if self.seam_config is None:
            return True
        return bool(self.seam_config.should_run_onload(lib_name))

    def _ensure_needed(self, img, hint_dir: str = "", _seen: Optional[set] = None) -> None:
        needed = getattr(img, "needed", None) or ()
        if not needed:
            return
        seen = _seen if _seen is not None else set()
        try:
            from core.native.linker import get_linker
            lnk = get_linker(self.eng)
        except Exception:
            lnk = None
        from core.native.loader import ElfImage
        from core.native.unpack import looks_packed
        from core.java.fusion_autoload import autoload_library

        use_cache = bool(self.seam_config and self.seam_config.unpack_mode == "self_cache")
        c_dir = self._onload_cache_dir()
        for raw_dep in needed:
            dep = os.path.basename(str(raw_dep or "").strip())
            if not dep or dep in _SYSTEM_SONAMES or dep in seen:
                continue
            seen.add(dep)
            if lnk is not None and lnk.find_by_name(dep) is not None:
                continue
            dep_bytes = None
            dep_vpath = ""
            v_cands = []
            if hint_dir:
                v_cands.append(hint_dir.rstrip("/") + "/" + dep)
            v_cands.extend([
                f"/data/app/{self.pkg}/lib/arm64/{dep}",
                f"/data/data/{self.pkg}/files/{dep}",
            ])
            for vp in v_cands:
                b = self.vfs_read(self.eng, vp)
                if b and len(b) >= 64 and b[:4] == b"\x7fELF" and not looks_packed(b[:4096]):
                    dep_bytes = b
                    dep_vpath = vp
                    break
            if dep_bytes is None:
                sc = self.find_selfcache_so(dep)
                if sc and os.path.isfile(sc):
                    with open(sc, "rb") as fh:
                        b = fh.read()
                    if len(b) >= 64 and b[:4] == b"\x7fELF" and not looks_packed(b[:4096]):
                        dep_bytes = b
                        dep_vpath = sc
            if dep_bytes is None and self.app_root:
                ap = os.path.join(self.app_root, "lib", "arm64-v8a", dep)
                if os.path.isfile(ap):
                    with open(ap, "rb") as fh:
                        b = fh.read()
                    if len(b) >= 64 and b[:4] == b"\x7fELF" and not looks_packed(b[:4096]):
                        dep_bytes = b
                        dep_vpath = f"/data/app/{self.pkg}/lib/arm64/{dep}"
            if dep_bytes is None:
                continue
            try:
                dep_img = ElfImage.parse(dep_bytes)
                self._ensure_needed(dep_img, hint_dir=hint_dir, _seen=seen)
                _t0 = time.monotonic()
                info = autoload_library(
                    self.svc, self.eng, self.jni, dep_img, self.table,
                    run_onload=self._should_run_onload(dep), ffi=self.ffi,
                    name=dep, path=dep_vpath,
                    use_onload_cache=use_cache, cache_dir=c_dir,
                    **self._budget_kwargs(),
                )
                if not info.get("cached") and not info.get("lazy"):
                    self._native_init_spent_s += max(0.0, time.monotonic() - _t0)
            except Exception:
                continue

    def _record_magic_warning(self, path: str, data: bytes) -> None:
        head_hex = bytes(data[:16]).hex()
        missing = list(getattr(self.stubs, "missing", ()) or ())[-10:]
        warn = {
            "kind": "invalid_elf_magic",
            "path": str(path),
            "size": len(data),
            "head_hex": head_hex,
            "recent_missing": missing,
        }
        if self.stubs is not None:
            ws = getattr(self.stubs, "_unpack_warnings", None)
            if ws is None:
                ws = []
                self.stubs._unpack_warnings = ws
            ws.append(warn)
        miss_names = ", ".join(f"{m.get('cls')}->{m.get('name')}" for m in missing[-5:]) or "-"
        self.log_fn(
            f"> [Packer-Diagnose] System.load({path}): kein ELF-Header (magic={head_hex}, "
            f"len={len(data)}). Letzte offene Java-Stubs: {miss_names}"
        )

    def _find_vfs_overlay(self, name: str):
        """Prueft Prio 1: aktives VFS-2.0-Overlay (LibCarve/LibForge/Hex/Lib-Replace)."""
        ov_set = getattr(self.eng, "_vfs_overlay_libs", None) or ()
        if not ov_set:
            return None, None
        raw_bn = os.path.basename(str(name or "").strip())
        cands = []
        if raw_bn:
            cands.append(raw_bn)
            if not raw_bn.startswith("lib"):
                cands.append(f"lib{raw_bn}")
            if not raw_bn.endswith(".so"):
                cands.append(f"{raw_bn}.so")
            if not raw_bn.startswith("lib") and not raw_bn.endswith(".so"):
                cands.append(f"lib{raw_bn}.so")
        for fn in cands:
            if fn not in ov_set:
                continue
            for vp in (
                f"/data/app/{self.pkg}/lib/arm64/{fn}",
                f"/data/data/{self.pkg}/files/{fn}",
                f"/data/data/{self.pkg}/{fn}",
            ):
                b = self.vfs_read(self.eng, vp)
                if b and len(b) >= 64 and b[:4] == b"\x7fELF":
                    return bytes(b), vp
        return None, None

    def load_library(self, name: str) -> None:
        """Handler fuer `System.loadLibrary(name)`."""
        try:
            from core.native.loader import ElfImage
            from core.native.unpack import looks_packed
            from core.java.fusion_autoload import autoload_library

            run_onload = self._should_run_onload(name)
            use_cache = bool(self.seam_config and self.seam_config.unpack_mode == "self_cache")
            c_dir = self._onload_cache_dir()
            bk = self._budget_kwargs()

            # Prio 1: Live VFS 2.0 Overlay (LibCarve / LibForge / Hex / Lib-Replace)
            ov_bytes, ov_vpath = self._find_vfs_overlay(name)
            if ov_bytes is not None:
                img = ElfImage.parse(ov_bytes)
                self._ensure_needed(img, hint_dir=os.path.dirname(ov_vpath))
                _t0 = time.monotonic()
                info = autoload_library(
                    self.svc, self.eng, self.jni, img, self.table,
                    run_onload=run_onload, ffi=self.ffi,
                    name=os.path.basename(ov_vpath), path=ov_vpath,
                    use_onload_cache=use_cache, cache_dir=c_dir,
                    **bk,
                )
                if not info.get("cached") and not info.get("lazy"):
                    self._native_init_spent_s += max(0.0, time.monotonic() - _t0)
                self._log_loaded(f"loadLibrary({name})[live-overlay]", ov_vpath, info)
                return

            sc_hit = self.find_selfcache_so(name)
            if sc_hit:
                img = ElfImage.from_file(sc_hit)
                self._ensure_needed(img, hint_dir=os.path.dirname(sc_hit))
                _t0 = time.monotonic()
                info = autoload_library(
                    self.svc, self.eng, self.jni, img, self.table,
                    run_onload=run_onload, ffi=self.ffi,
                    name=os.path.basename(sc_hit), path=sc_hit,
                    use_onload_cache=use_cache, cache_dir=c_dir,
                    **bk,
                )
                if not info.get("cached") and not info.get("lazy"):
                    self._native_init_spent_s += max(0.0, time.monotonic() - _t0)
                self._log_loaded(f"loadLibrary({name})[cache]", sc_hit, info)
                return

            if not self.app_root:
                return
            cands = []
            for pat in (
                f"lib/arm64-v8a/lib{name}.so",
                f"lib/arm64-v8a/{name}.so",
                f"lib/arm64-v8a/{name}",
                f"**/lib{name}.so",
            ):
                cands += [c for c in glob.glob(os.path.join(self.app_root, pat), recursive=True) if os.path.isfile(c)]
            if not cands:
                self.log_fn(f"> loadLibrary({name}): .so in Zone nicht gefunden -> Tabelle leer")
                return
            so_path = cands[0]
            with open(so_path, "rb") as _fh:
                head = _fh.read(4096)
            if looks_packed(head):
                twin = self.find_unpacked_twin(so_path)
                if twin is None:
                    self.log_fn(f"> loadLibrary({name}): {os.path.basename(so_path)} gepackt (Self-Cache leer)")
                    return
                so_path = twin
            img = ElfImage.from_file(so_path)
            self._ensure_needed(img, hint_dir=f"/data/app/{self.pkg}/lib/arm64")
            _t0 = time.monotonic()
            info = autoload_library(
                self.svc, self.eng, self.jni, img, self.table,
                run_onload=run_onload, ffi=self.ffi,
                name=os.path.basename(so_path),
                path=f"/data/app/{self.pkg}/lib/arm64/{os.path.basename(so_path)}",
                use_onload_cache=use_cache, cache_dir=c_dir,
                **bk,
            )
            if not info.get("cached") and not info.get("lazy"):
                self._native_init_spent_s += max(0.0, time.monotonic() - _t0)
            self._log_loaded(f"loadLibrary({name})", so_path, info)
        except Exception as e:
            self.log_fn(f"> loadLibrary({name}) fehlgeschlagen: {e!r}")

    def load_path(self, path: str) -> None:
        """Handler fuer `System.load(absPfad)`."""
        try:
            from core.java.fusion_autoload import autoload_library_from_bytes

            data = self.vfs_read(self.eng, path)
            if not data:
                self.log_fn(f"> System.load({path}): nicht im VFS")
                return
            if len(data) < 64 or data[:4] != b"\x7fELF":
                self._record_magic_warning(path, data)
                return
            resolved_name = self.vfs_resolve_name(self.eng, path)
            ov_bytes, ov_vpath = self._find_vfs_overlay(resolved_name)
            is_overlay = False
            if ov_bytes is not None:
                data = ov_bytes
                is_overlay = True
            effective_path = (
                f"/data/data/{self.pkg}/{resolved_name}"
                if _FD_RE.match(str(path or "")) and resolved_name
                else str(path)
            )
            if not is_overlay and str(path).startswith(("/data/", "/proc/")) and resolved_name:
                self.save_selfcache_so(resolved_name, data)
            run_onload = self._should_run_onload(resolved_name)
            use_cache = bool(self.seam_config and self.seam_config.unpack_mode == "self_cache")
            c_dir = self._onload_cache_dir()
            bk = self._budget_kwargs()
            from core.native.loader import ElfImage
            img = ElfImage.parse(bytes(data))
            self._ensure_needed(img, hint_dir=os.path.dirname(effective_path) if "/" in effective_path else "")
            _t0 = time.monotonic()
            from core.java.fusion_autoload import autoload_library
            info = autoload_library(
                self.svc, self.eng, self.jni, img, self.table,
                run_onload=run_onload, ffi=self.ffi,
                name=resolved_name, path=(ov_vpath or effective_path),
                use_onload_cache=use_cache, cache_dir=c_dir,
                **bk,
            )
            if not info.get("cached") and not info.get("lazy"):
                self._native_init_spent_s += max(0.0, time.monotonic() - _t0)
            tag_suf = "[live-overlay]" if is_overlay else ""
            self._log_loaded(f"System.load({resolved_name}){tag_suf}", (ov_vpath or effective_path), info)
        except Exception as e:
            self.log_fn(f"> System.load({path}) fehlgeschlagen: {e!r}")

    def _log_loaded(self, tag: str, src: str, info: dict) -> None:
        bias = info.get("load_bias", 0)
        onl = info.get("onload_ret")
        onl_s = hex(onl) if onl is not None else "-"
        stalled = info.get("init_stalled", 0)
        extra = f", init_stalled={stalled}" if stalled else ""
        if info.get("lazy"):
            extra += ", lazy"
        cache_s = ", cached" if info.get("cached") else ""
        self.log_fn(
            f"> {tag}: {os.path.basename(src)} @0x{bias:x} "
            f"(relocs={info.get('relocs')}, wired={info.get('wired')}, "
            f"init={info.get('init_called')}{extra}, JNI_OnLoad={onl_s}{cache_s}) "
            f"-> {info.get('registered')} native Methoden"
        )
