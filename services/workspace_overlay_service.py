# -*- coding: utf-8 -*-
"""services.workspace_overlay_service — Zero-Build Live-Overlay fuer Debugger & MCP.

Sammlungs- und Aufloesungs-Service fuer alle aktiven Workspace-Aenderungen
(Smali Studio, LibForge, LibCarve, Hex Patcher, Lib Replacer), sodass sie direkt
im Simulator (`zone="live"`) ohne vorherigen APK-Build im RAM/VFS 2.0 laufen.
Veraendert NIEMALS Dateien in `source/` oder saubere Originals im `self_cache`.
"""
from __future__ import annotations

import glob
import hashlib
import os
from typing import Any, Dict, List, Optional


def _safe_pkg(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "").strip())
    return clean.strip("._") or "default.app"


def _find_base_so(base_dir: str, pkg: str, app_root: str, soname: str, abi: str = "arm64-v8a") -> Optional[bytes]:
    """Liest saubere Basis-Bytes einer .so (Prio: self_cache/<pkg>, dann app_root/lib/<abi>)."""
    fn = os.path.basename(str(soname or "").strip())
    if not fn:
        return None
    if base_dir and pkg:
        sc_cand = os.path.join(base_dir, "data", "vfs_selfcache", _safe_pkg(pkg), fn)
        if os.path.isfile(sc_cand):
            try:
                with open(sc_cand, "rb") as fh:
                    return fh.read()
            except Exception:
                pass
        sc_cand2 = os.path.join(base_dir, "data", "vfs_selfcache", pkg, fn)
        if sc_cand2 != sc_cand and os.path.isfile(sc_cand2):
            try:
                with open(sc_cand2, "rb") as fh:
                    return fh.read()
            except Exception:
                pass
    if app_root:
        for pat in (
            os.path.join(app_root, "lib", abi, fn),
            os.path.join(app_root, "lib", "arm64-v8a", fn),
            os.path.join(app_root, "**", fn),
        ):
            hits = glob.glob(pat, recursive=("**" in pat))
            for cand in hits:
                if os.path.isfile(cand):
                    try:
                        with open(cand, "rb") as fh:
                            return fh.read()
                    except Exception:
                        continue
    return None


class WorkspaceOverlayResolver:
    """Baut das einheitliche In-Memory-/VFS-Overlay fuer Smali + Native ohne APK-Build."""

    @classmethod
    def collect(
        cls,
        base_dir: str,
        pkg: str,
        app_root: str = "",
        *,
        app: Any = None,
        cfg: Optional[dict] = None,
        overlay_smali: bool = True,
        overlay_libcarve: bool = True,
        overlay_libforge: bool = True,
        extra_smali_patches: Optional[List[dict]] = None,
        extra_libcarve_patches: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        """Liefert {"smali_patches": [...], "vfs_so_overlays": {soname: bytes}, "summary": {...}}."""
        from core.application.session_state import SessionState

        base_dir = os.path.abspath(base_dir or ".")
        if cfg is None and app is not None:
            cfg = getattr(getattr(app, "cfg", None), "config", None) or {}
        cfg = cfg or {}

        smali_patches: List[dict] = []
        vfs_so_overlays: Dict[str, bytes] = {}
        summary = {"smali": 0, "libforge": 0, "lib_replace": 0, "hex": 0, "libcarve": 0, "warnings": []}

        # 1) Smali Studio Patches (Methoden + Full-File + Eigene Strukturen / new_file)
        if overlay_smali:
            ws = getattr(app, "workspace_tab", None) if app is not None else None
            studio = getattr(ws, "smali_studio", None) if ws is not None else None
            pats = list(getattr(studio, "smali_patches", None) or SessionState.active_smali_patches or ())
            if extra_smali_patches:
                pats.extend(extra_smali_patches)
            smali_patches = pats
            summary["smali"] = len(smali_patches)

        # 2) LibForge aktive kompilierte .so / PIE-Executables
        if overlay_libforge:
            try:
                from services.native_lib_service import NativeLibManager
                nm = NativeLibManager(base_dir)
                for lib in (nm.get_active() or ()):
                    sp = nm.abspath(lib.so_relpath) if getattr(lib, "so_relpath", "") else ""
                    if sp and os.path.isfile(sp):
                        with open(sp, "rb") as fh:
                            blob = fh.read()
                        if blob:
                            if getattr(lib, "output_kind", "shared") == "executable":
                                fn = os.path.basename(lib.name)
                            else:
                                fn = lib.name if (lib.name.startswith("lib") and lib.name.endswith(".so")) else f"lib{lib.name}.so"
                            vfs_so_overlays[os.path.basename(fn)] = blob
                            summary["libforge"] += 1
            except Exception as e:
                summary["warnings"].append(f"LibForge-Overlay: {e!r}")

        # 3) Native Lib Replacer + Hex Patcher + LibCarve
        if overlay_libcarve:
            ws = getattr(app, "workspace_tab", None) if app is not None else None
            # 3a) Whole-lib replacements (Native Lib Replacer)
            repls = (
                ws.get_lib_replacement_data()
                if (ws is not None and hasattr(ws, "get_lib_replacement_data"))
                else list(SessionState.active_lib_replacements or ())
            )
            for rp in repls:
                if not isinstance(rp, dict):
                    continue
                src = str(rp.get("source") or "").strip()
                tgt = str(rp.get("target") or "").strip()
                if src and os.path.isfile(src):
                    try:
                        with open(src, "rb") as fh:
                            blob = fh.read()
                        if blob:
                            soname = os.path.basename(tgt or src)
                            vfs_so_overlays[soname] = blob
                            summary["lib_replace"] += 1
                    except Exception as e:
                        summary["warnings"].append(f"Lib-Replace {tgt}: {e!r}")

            # Legacy lib_replace entries inside active_hex_patches
            hex_rows = (
                ws.get_hex_patch_data()
                if (ws is not None and hasattr(ws, "get_hex_patch_data"))
                else list(SessionState.active_hex_patches or ())
            )
            for hp in list(SessionState.active_hex_patches or ()):
                if isinstance(hp, dict) and hp.get("type") == "lib_replace":
                    src = str(hp.get("source") or "").strip()
                    tf = str(hp.get("file") or hp.get("target") or "").strip()
                    if src and os.path.isfile(src):
                        with open(src, "rb") as fh:
                            vfs_so_overlays[os.path.basename(tf or src)] = fh.read()
                            summary["lib_replace"] += 1

            # 3b) Raw Hex Patches -> in-memory ELF buffer
            hex_by_lib: Dict[str, List[dict]] = {}
            for hp in hex_rows:
                if not isinstance(hp, dict) or hp.get("type", "hex") != "hex":
                    continue
                fname = os.path.basename(str(hp.get("file") or "").strip())
                ram_s = str(hp.get("ram") or "").strip()
                patch_s = str(hp.get("patch") or "").replace(" ", "").strip()
                if not fname or not ram_s or not patch_s:
                    continue
                hex_by_lib.setdefault(fname, []).append(hp)

            for fname, hlist in hex_by_lib.items():
                raw = vfs_so_overlays.get(fname) or _find_base_so(base_dir, pkg, app_root, fname)
                if not raw:
                    summary["warnings"].append(f"Hex-Overlay: Basis-Lib {fname} nicht gefunden")
                    continue
                buf = bytearray(raw)
                applied_h = 0
                for hp in hlist:
                    try:
                        ram_v = int(str(hp.get("ram")).strip(), 16)
                        base_v = int(str(hp.get("base") or "0").strip() or "0", 16)
                        off = ram_v - base_v
                        pb = bytes.fromhex(str(hp.get("patch") or "").replace(" ", "").strip())
                        if 0 <= off and off + len(pb) <= len(buf):
                            buf[off:off + len(pb)] = pb
                            applied_h += 1
                    except Exception as e:
                        summary["warnings"].append(f"Hex-Overlay {fname}: {e!r}")
                if applied_h:
                    vfs_so_overlays[fname] = bytes(buf)
                    summary["hex"] += applied_h

            # 3c) LibCarve Patches (ASM / C-Cave / Hex)
            lc_widget = getattr(ws, "lib_carve", None) if ws is not None else None
            lc_patches = list(
                getattr(lc_widget, "libcarve_patches", None)
                or SessionState.active_libcarve_patches
                or ()
            )
            if extra_libcarve_patches:
                lc_patches.extend(extra_libcarve_patches)

            lc_by_lib: Dict[str, List[dict]] = {}
            for lp in lc_patches:
                if not isinstance(lp, dict):
                    continue
                fname = os.path.basename(str(lp.get("file") or "").strip())
                if not fname:
                    continue
                lc_by_lib.setdefault(fname, []).append(lp)

            if lc_by_lib:
                try:
                    from services.libcarve_compiler_service import LibCarveCompilerService
                except Exception as e:
                    LibCarveCompilerService = None
                    summary["warnings"].append(f"LibCarveCompilerService nicht verfuegbar: {e!r}")

                if LibCarveCompilerService is not None:
                    for fname, plist in lc_by_lib.items():
                        abi = str(plist[0].get("abi") or "arm64-v8a")
                        base_bytes = vfs_so_overlays.get(fname) or _find_base_so(base_dir, pkg, app_root, fname, abi=abi)
                        if not base_bytes:
                            # Fallback: vielleicht wurde bereits eine gepatchte Lib in data/libcarve abgelegt
                            cached_so = os.path.join(base_dir, "data", "libcarve", _safe_pkg(pkg), abi, fname)
                            if os.path.isfile(cached_so):
                                with open(cached_so, "rb") as fh:
                                    vfs_so_overlays[fname] = fh.read()
                                    summary["libcarve"] += len(plist)
                                continue
                            summary["warnings"].append(f"LibCarve-Overlay: Basis-Lib {fname} nicht gefunden")
                            continue

                        # Pruefe ob vorkompilierter Cache exakt zum aktuellen Basis-SHA + Patch-Satz passt
                        cache_so = os.path.join(base_dir, "data", "libcarve", _safe_pkg(pkg), abi, fname)
                        cache_meta = cache_so + ".meta.json"
                        used_cache = False
                        try:
                            base_sha = hashlib.sha256(base_bytes).hexdigest()[:12]
                            if os.path.isfile(cache_so) and os.path.isfile(cache_meta) and fname not in vfs_so_overlays:
                                import json
                                with open(cache_meta, "r", encoding="utf-8") as mf:
                                    meta = json.load(mf)
                                if (
                                    meta.get("base_sha") == base_sha
                                    and meta.get("patch_count") == len(plist)
                                    and meta.get("patch_digest") == LibCarveCompilerService.patches_digest(plist)
                                ):
                                    with open(cache_so, "rb") as cfh:
                                        vfs_so_overlays[fname] = cfh.read()
                                    summary["libcarve"] += len(plist)
                                    used_cache = True
                        except Exception:
                            used_cache = False

                        if used_cache:
                            continue

                        try:
                            out_bytes, report = LibCarveCompilerService.apply_patches_to_elf(base_bytes, plist, cfg=cfg)
                            vfs_so_overlays[fname] = out_bytes
                            ok_cnt = sum(1 for r in report if r.get("ok"))
                            summary["libcarve"] += ok_cnt
                            for r in report:
                                if not r.get("ok"):
                                    summary["warnings"].append(f"LibCarve {fname}: {r.get('error', '?')}")
                        except Exception as e:
                            summary["warnings"].append(f"LibCarve {fname}: {e!r}")

        return {
            "smali_patches": smali_patches,
            "vfs_so_overlays": vfs_so_overlays,
            "summary": summary,
        }
