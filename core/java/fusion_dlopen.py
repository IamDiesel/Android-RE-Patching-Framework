# -*- coding: utf-8 -*-
"""core.java.fusion_dlopen — native `dlopen`/`android_dlopen_ext`/`dlsym` fuer die Fusion.

Faithful: wenn NATIVER Code (nicht Java `System.load`) die von der App selbst entpackte Lib laedt,
muss derselbe VFS-Weg greifen. Diese Handler haengen an der FFI-Import-Bruecke (`NativeFfiService`):
  * `dlopen(path, flag)` / `android_dlopen_ext(path, flag, extinfo)` -> Bytes aus dem VFS (die die App
    dort hingeschrieben hat) -> `ElfImage.parse` -> `autoload_library` (load_image + JNI_OnLoad +
    RegisterNatives-Ernte) -> gibt ein Handle zurueck.
  * `dlsym(handle, name)` -> virtuelle Adresse des Symbols in der geladenen Lib.

Das OS entpackt nichts. Es laedt nur, was im VFS liegt. `image_parser`/`autoloader` sind injizierbar
(Tests). Zielagnostisch."""
from __future__ import annotations

_M64 = (1 << 64) - 1
_SYS_HANDLE = 0xDFFF0000


def _read_cstr(cpu, addr, maxlen=4096):
    if not addr:
        return None
    a = int(addr) & _M64
    out = bytearray()
    for i in range(maxlen):
        try:
            b = cpu.m.rb(a + i, 1)[0]
        except Exception:
            break
        if b == 0:
            break
        out.append(b)
    return out.decode("utf-8", "replace")


class _DlState:
    def __init__(self):
        self.handles = {}     # handle -> img
        self.by_path = {}     # pfad -> handle (Dedupe: schon geladene Libs nicht erneut laden)
        self.next_handle = 0xD000


def install_native_dlopen(eng, ffi, svc, jni, table, vfs_read,
                          image_parser=None, autoloader=None, fusion_loader=None):
    """Registriert dlopen/android_dlopen_ext/dlsym-Handler auf der FFI-Bruecke der Engine.

    `vfs_read(path) -> bytes|None` liest aus dem lokalen (virtuellen) Speicher der App.
    Rueckgabe: _DlState (Handle-Tabelle)."""
    from core.java.fusion_loader import _FD_RE, _SYSTEM_SONAMES
    if image_parser is None:
        from core.native.loader import ElfImage
        image_parser = ElfImage.parse
    is_default_auto = False
    if autoloader is None:
        from core.java.fusion_autoload import autoload_library
        autoloader = autoload_library
        is_default_auto = True
    st = _DlState()

    def _dlopen(cpu, _ffi, path_reg=0):
        import os as _os
        path = _read_cstr(cpu, cpu.x[path_reg])
        if not path:
            return 0
        if path in st.by_path:
            return st.by_path[path]           # schon geladen -> gleiches Handle, kein erneutes JNI_OnLoad
        base_name = _os.path.basename(path)
        if base_name in _SYSTEM_SONAMES:
            st.by_path[path] = _SYS_HANDLE
            return _SYS_HANDLE

        resolved_name = (
            fusion_loader.vfs_resolve_name(eng, path)
            if fusion_loader is not None else base_name
        )
        effective_path = (
            f"/data/data/{fusion_loader.pkg}/{resolved_name}"
            if fusion_loader is not None and _FD_RE.match(str(path or "")) and resolved_name
            else str(path)
        )
        if effective_path in st.by_path:
            st.by_path[path] = st.by_path[effective_path]
            return st.by_path[path]

        data = vfs_read(path)
        if not data and fusion_loader is not None and "/" not in path:
            for cand_vp in (
                f"/data/app/{fusion_loader.pkg}/lib/arm64/{base_name}",
                f"/data/data/{fusion_loader.pkg}/files/{base_name}",
            ):
                data = vfs_read(cand_vp)
                if data:
                    effective_path = cand_vp
                    break
            if not data:
                sc = fusion_loader.find_selfcache_so(base_name)
                if sc and _os.path.isfile(sc):
                    with open(sc, "rb") as fh:
                        data = fh.read()
                    effective_path = sc
        if not data:
            return 0   # nicht im VFS -> App hat (noch) nichts entpackt

        try:
            raw = bytes(data)
            img = image_parser(raw)
            if is_default_auto:
                if fusion_loader is not None:
                    if str(path).startswith(("/data/", "/proc/")) and resolved_name:
                        fusion_loader.save_selfcache_so(resolved_name, raw)
                    hint_dir = _os.path.dirname(effective_path) if "/" in effective_path else ""
                    fusion_loader._ensure_needed(img, hint_dir=hint_dir)
                    use_cache = bool(fusion_loader.seam_config and fusion_loader.seam_config.unpack_mode == "self_cache")
                    autoloader(
                        svc, eng, jni, img, table,
                        run_onload=fusion_loader._should_run_onload(resolved_name),
                        ffi=ffi, name=resolved_name or base_name, path=effective_path,
                        use_onload_cache=use_cache, cache_dir=fusion_loader._onload_cache_dir(),
                        **fusion_loader._budget_kwargs(),
                    )
                else:
                    autoloader(svc, eng, jni, img, table, ffi=ffi, path=effective_path,
                               name=resolved_name or base_name)
            else:
                autoloader(svc, eng, jni, img, table)
        except Exception:
            return 0
        h = st.next_handle; st.next_handle += 0x10
        st.handles[h] = img
        st.by_path[path] = h
        if effective_path:
            st.by_path[effective_path] = h
        return h

    def _dlopen_h(cpu, _ffi):
        return _dlopen(cpu, _ffi, path_reg=0)

    def _android_dlopen_ext(cpu, _ffi):
        return _dlopen(cpu, _ffi, path_reg=0)   # path ist x0 wie bei dlopen

    def _dlsym(cpu, _ffi):
        h = int(cpu.x[0]) & _M64
        name = _read_cstr(cpu, cpu.x[1])
        if not name:
            return 0
        if h in (0, _M64, _SYS_HANDLE):
            try:
                from core.native.linker import get_linker
                addr = get_linker(eng).resolve_symbol(name)
                if addr:
                    return int(addr) & _M64
            except Exception:
                pass
            try:
                from core.native.libc_stubs import install_libc_stubs
                install_libc_stubs(eng, ffi)
            except Exception:
                pass
            try:
                return int(ffi.install_named_stub(eng, name)) & _M64
            except Exception:
                return 0
        img = st.handles.get(h)
        if img is None:
            return 0
        try:
            sym = img.dynsyms.get(name)
        except Exception:
            sym = None
        if sym is None:
            return 0
        bias = int(getattr(img, "load_bias", 0) or 0)
        return (bias + int(sym.value)) & _M64

    for nm, fn in (("dlopen", _dlopen_h), ("android_dlopen_ext", _android_dlopen_ext),
                   ("__loader_android_dlopen_ext", _android_dlopen_ext), ("dlsym", _dlsym)):
        ffi.install_named_stub(eng, nm)
        ffi.set_handler(eng, nm, fn)
    return st
