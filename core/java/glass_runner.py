# -*- coding: utf-8 -*-
"""core.java.glass_runner — Einheitlicher Glaeserner App-Runner, Instanzen (Fork/Merge) & Projekt-Snapshots (Paket 1).

Stellt bereit:
  - ``InstanceState``: isolierter Laufkontext (``main`` oder Fork) mit eigenem AppData-Verzeichnis.
  - ``ProjectSnapshotService``: Projektstaende speichern/laden (``data/projects/<pkg>/<name>/``)
    sowie Instanzen forken/mergen/verwerfen (``data/instances/<fork_id>/``).
  - ``sync_appdata_into_vfs`` / ``persist_appdata_from_vfs``: persistentes AppData im VFS 2.0.
  - ``GlassAppRunner``: gemeinsame Start- und Ausfuehrungspipeline fuer GUI & MCP.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import datetime as _dt
import glob
import json
import os
import shutil
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from core.java.seam_config import SeamConfig


def _safe_name(value: str, fallback: str = "default") -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "").strip())
    clean = clean.strip("._")
    return clean or fallback


def _read_json(path: str, default: Any = None) -> Any:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return copy.deepcopy(default)


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


@dataclass
class InstanceState:
    """Kontext einer (ggf. geforkten) App-Instanz."""

    instance_id: str = "main"
    label: str = "main"
    package: str = ""
    app_root: str = ""
    zone: str = "patched"
    apply_patches: bool = True
    seam_profile: str = "self_cache"
    net_mode: str = "mock"
    appdata_root: str = "data/vfs_appdata"
    selfcache_root: str = "data/vfs_selfcache"
    parent_id: str = ""
    created_at: str = field(default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds"))

    def is_fork(self) -> bool:
        return self.instance_id != "main"

    def pkg_appdata_dir(self, base_dir: str = ".") -> str:
        pkg = _safe_name(self.package or "default.app", "default.app")
        root = self.appdata_root
        if not os.path.isabs(root):
            root = os.path.join(base_dir, root)
        return os.path.join(root, pkg)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "label": self.label,
            "package": self.package,
            "app_root": self.app_root,
            "zone": self.zone,
            "apply_patches": bool(self.apply_patches),
            "seam_profile": self.seam_profile,
            "net_mode": self.net_mode,
            "appdata_root": self.appdata_root,
            "selfcache_root": self.selfcache_root,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "InstanceState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            instance_id=str(data.get("instance_id") or "main"),
            label=str(data.get("label") or data.get("instance_id") or "main"),
            package=str(data.get("package") or ""),
            app_root=str(data.get("app_root") or ""),
            zone=str(data.get("zone") or "patched"),
            apply_patches=bool(data.get("apply_patches", True)),
            seam_profile=str(data.get("seam_profile") or "self_cache"),
            net_mode=str(data.get("net_mode") or "mock"),
            appdata_root=str(data.get("appdata_root") or "data/vfs_appdata"),
            selfcache_root=str(data.get("selfcache_root") or "data/vfs_selfcache"),
            parent_id=str(data.get("parent_id") or ""),
            created_at=str(data.get("created_at") or _dt.datetime.now().isoformat(timespec="seconds")),
        )


def sync_appdata_into_vfs(fs, pkg: str, host_dir: str) -> int:
    """Synchronisiert persistierte Dateien aus ``host_dir`` nach ``/data/data/<pkg>/...`` ins VFS 2.0."""
    if fs is None or not pkg or not host_dir or not os.path.isdir(host_dir):
        return 0
    vroot = f"/data/data/{pkg}"
    count = 0
    for dp, _dirs, fns in os.walk(host_dir):
        for fn in fns:
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, host_dir).replace(os.sep, "/")
            try:
                with open(fp, "rb") as fh:
                    data = fh.read()
                fs.add_file(f"{vroot}/{rel}", data)
                count += 1
            except Exception:
                continue
    return count


def persist_appdata_from_vfs(fs, pkg: str, host_dir: str, max_file_bytes: int = 16 * 1024 * 1024) -> int:
    """Schreibt App-Daten unter ``/data/data/<pkg>/...`` aus VFS 2.0 auf Disk nach ``host_dir``."""
    if fs is None or not pkg or not host_dir:
        return 0
    prefix = f"/data/data/{pkg}/"
    nodes = getattr(fs, "nodes", None) or {}
    count = 0
    for vpath, ino in list(nodes.items()):
        if not vpath.startswith(prefix):
            continue
        # Only regular files (dirs also hold b"" in VFS 2.0!)
        kind = getattr(ino, "kind", None)
        if kind != "file":
            continue
        data = bytes(getattr(ino, "data", b"") or b"")
        if len(data) > max_file_bytes:
            continue
        rel = vpath[len(prefix):].lstrip("/")
        if not rel:
            continue
        # Avoid re-dumping large unpacked shared libraries into appdata when self_cache handles ELFs
        target = os.path.join(host_dir, rel.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            count += 1
        except Exception:
            continue
    return count


class ProjectSnapshotService:
    """Verwaltet Projektstaende (``data/projects/``) und parallele Instanzen/Forks (``data/instances/``)."""

    def __init__(self, base_dir: str = "."):
        self.base_dir = os.path.abspath(base_dir or ".")

    def _projects_root(self) -> str:
        return os.path.join(self.base_dir, "data", "projects")

    def _instances_root(self) -> str:
        return os.path.join(self.base_dir, "data", "instances")

    @staticmethod
    def _extract_package(src: Any, instance: Optional[InstanceState] = None) -> str:
        pkg = ""
        if instance and instance.package:
            pkg = instance.package
        if not pkg and src is not None:
            pkg = getattr(src, "package", "") or getattr(getattr(src, "instance_state", None), "package", "")
            if not pkg and hasattr(src, "_fusion") and isinstance(src._fusion, dict):
                pass
            if not pkg:
                root = getattr(src, "zone_root", "") or ""
                if not root and callable(getattr(src, "_resolve_root", None)):
                    try:
                        root = src._resolve_root() or ""
                    except Exception:
                        root = ""
                if root:
                    parent = os.path.basename(os.path.dirname(root))
                    base = os.path.basename(root)
                    if "." in parent:
                        pkg = parent
                    elif "." in base:
                        pkg = base
        return _safe_name(pkg or "default.app", "default.app")

    @staticmethod
    def _extract_debug_config(src: Any) -> Dict[str, Any]:
        if src is None:
            return {}
        if isinstance(src, dict):
            return copy.deepcopy(src)
        seam = getattr(src, "seam_config", None)
        bps = []
        app_bps = {}
        raw_app_bps = getattr(src, "app_bps", None)
        if isinstance(raw_app_bps, dict):
            for k, meta in raw_app_bps.items():
                if isinstance(k, tuple) and len(k) == 4:
                    app_bps[f"{k[0]}|{k[1]}|{k[2]}|{k[3]}"] = copy.deepcopy(meta)
        net_cond = getattr(src, "net_condition", None)
        net_cond_d = net_cond.to_dict() if hasattr(net_cond, "to_dict") else (copy.deepcopy(net_cond) if isinstance(net_cond, dict) else {})
        return {
            "zone": getattr(src, "zone", "patched"),
            "zone_root": getattr(src, "zone_root", ""),
            "apply_patches": bool(getattr(src, "apply_patches", True)),
            "app_entry": getattr(src, "app_entry", ""),
            "relpath": getattr(src, "relpath", ""),
            "bps": list(getattr(src, "bps", []) or []),
            "bp_meta": copy.deepcopy(getattr(src, "bp_meta", {}) or {}),
            "app_bps": app_bps,
            "aps": copy.deepcopy(getattr(src, "aps", []) or []),
            "jni_aps": copy.deepcopy(getattr(src, "jni_aps", []) or []),
            "watches": list(getattr(src, "watches", []) or []),
            "net_mode": getattr(src, "net_mode", "mock"),
            "net_condition": net_cond_d,
            "native_fusion": bool(getattr(src, "native_fusion", False)),
            "persist_session": bool(getattr(src, "persist_session", seam.persist_appdata if hasattr(seam, "persist_appdata") else False)),
            "seam_config": seam.to_dict() if hasattr(seam, "to_dict") else SeamConfig().to_dict(),
        }

    def _active_vfs_flush(self, src: Any, pkg: str, host_appdata_dir: str) -> None:
        if src is None:
            return
        try:
            stubs = getattr(src, "_stubs", None) or getattr(getattr(src, "_runner", None), "stubs", None)
            if stubs is not None:
                from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir
                persist_java_appdata_to_dir(stubs, host_appdata_dir)
        except Exception:
            pass
        fusion = getattr(src, "_fusion", None)
        if isinstance(fusion, dict) and fusion.get("eng") is not None:
            try:
                from core.java.fs_bridge import get_shared_fs
                fs = get_shared_fs(fusion["eng"])
                if fs is not None:
                    persist_appdata_from_vfs(fs, pkg, host_appdata_dir)
            except Exception:
                pass

    def _copy_dir_clean(self, src_dir: str, dst_dir: str) -> None:
        if os.path.isdir(dst_dir):
            shutil.rmtree(dst_dir, ignore_errors=True)
        if src_dir and os.path.isdir(src_dir):
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        else:
            os.makedirs(dst_dir, exist_ok=True)

    def _find_rules_path(self, src: Any = None) -> Optional[str]:
        try:
            paths = getattr(getattr(getattr(src, "app", None), "cfg", None), "paths", None)
            if isinstance(paths, dict) and paths.get("API_RULES"):
                return paths["API_RULES"]
        except Exception:
            pass
        for cand in (
            os.path.join(self.base_dir, "data", "intercept_rules.json"),
            os.path.join(self.base_dir, "config", "intercept_rules.json"),
        ):
            if os.path.isfile(cand):
                return cand
        return None

    def _collect_smali_patches(self, src: Any) -> Dict[str, str]:
        patches: Dict[str, str] = {}
        try:
            vm = getattr(getattr(src, "_jdb", None), "vm", None)
            prov = getattr(vm, "class_provider", None)
            raw = getattr(prov, "patches", None)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str):
                        patches[k] = v
        except Exception:
            pass
        return patches

    def _collect_libcarve_patches(self) -> List[Dict[str, Any]]:
        try:
            from core.application.session_state import SessionState
            return copy.deepcopy(list(SessionState.active_libcarve_patches or ()))
        except Exception:
            return []

    @staticmethod
    def _restore_libcarve_patches(lc_list: Any) -> None:
        if not isinstance(lc_list, list):
            return
        try:
            from core.application.session_state import SessionState
            SessionState.active_libcarve_patches.clear()
            for item in lc_list:
                if isinstance(item, dict):
                    SessionState.active_libcarve_patches.append(copy.deepcopy(item))
        except Exception:
            pass

    # ---- Projects -----------------------------------------------------------
    def save_project(self, name: str, source_obj: Any = None, *,
                     instance: Optional[InstanceState] = None,
                     extra: Optional[Dict[str, Any]] = None) -> str:
        inst = instance or getattr(source_obj, "instance_state", None) or InstanceState()
        pkg = self._extract_package(source_obj, inst)
        safe = _safe_name(name, "snapshot")
        proj_dir = os.path.join(self._projects_root(), pkg, safe)
        os.makedirs(proj_dir, exist_ok=True)

        appdata_src = inst.pkg_appdata_dir(self.base_dir)
        os.makedirs(appdata_src, exist_ok=True)
        self._active_vfs_flush(source_obj, pkg, appdata_src)
        self._copy_dir_clean(appdata_src, os.path.join(proj_dir, "appdata"))

        dbg_cfg = self._extract_debug_config(source_obj)
        _write_json(os.path.join(proj_dir, "debug_config.json"), dbg_cfg)

        rules_path = self._find_rules_path(source_obj)
        if rules_path and os.path.isfile(rules_path):
            shutil.copy2(rules_path, os.path.join(proj_dir, "intercept_rules.json"))

        patches = self._collect_smali_patches(source_obj)
        if patches:
            _write_json(os.path.join(proj_dir, "smali_patches.json"), patches)
        lc_patches = self._collect_libcarve_patches()
        if lc_patches:
            _write_json(os.path.join(proj_dir, "libcarve_patches.json"), lc_patches)

        meta = {
            "name": name,
            "safe_name": safe,
            "package": pkg,
            "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "instance": inst.to_dict(),
            "extra": copy.deepcopy(extra or {}),
        }
        _write_json(os.path.join(proj_dir, "project.json"), meta)
        return proj_dir

    def list_projects(self, pkg: Optional[str] = None) -> List[Dict[str, Any]]:
        root = self._projects_root()
        if not os.path.isdir(root):
            return []
        out: List[Dict[str, Any]] = []
        pkgs = [_safe_name(pkg)] if pkg else sorted(os.listdir(root))
        for p in pkgs:
            pdir = os.path.join(root, p)
            if not os.path.isdir(pdir):
                continue
            for name in sorted(os.listdir(pdir)):
                mdir = os.path.join(pdir, name)
                meta = _read_json(os.path.join(mdir, "project.json"))
                if isinstance(meta, dict):
                    meta["path"] = mdir
                    out.append(meta)
        return out

    def load_project(self, pkg: str, name: str, controller: Any = None) -> Dict[str, Any]:
        safe_pkg = _safe_name(pkg or "default.app", "default.app")
        safe_name = _safe_name(name, "snapshot")
        proj_dir = os.path.join(self._projects_root(), safe_pkg, safe_name)
        meta = _read_json(os.path.join(proj_dir, "project.json"), {}) or {}
        dbg_cfg = _read_json(os.path.join(proj_dir, "debug_config.json"), {}) or {}
        patches = _read_json(os.path.join(proj_dir, "smali_patches.json"), {}) or {}
        lc_patches = _read_json(os.path.join(proj_dir, "libcarve_patches.json"), []) or []
        rules_src = os.path.join(proj_dir, "intercept_rules.json")

        inst = InstanceState.from_dict(meta.get("instance"))
        inst.package = safe_pkg
        dst_appdata = inst.pkg_appdata_dir(self.base_dir)
        self._copy_dir_clean(os.path.join(proj_dir, "appdata"), dst_appdata)

        if controller is not None:
            self._apply_debug_config(controller, dbg_cfg, inst=inst, patches=patches)
            self._restore_libcarve_patches(lc_patches)
            rules_dst = self._find_rules_path(controller)
            if os.path.isfile(rules_src) and rules_dst:
                os.makedirs(os.path.dirname(rules_dst), exist_ok=True)
                shutil.copy2(rules_src, rules_dst)
        return {
            "meta": meta,
            "debug_config": dbg_cfg,
            "smali_patches": patches,
            "libcarve_patches": lc_patches,
            "instance": inst,
            "path": proj_dir,
        }

    # ---- Forks / Instances --------------------------------------------------
    def create_fork(self, base_ctrl_or_state: Any = None, label: str = "") -> InstanceState:
        if isinstance(base_ctrl_or_state, InstanceState):
            base_inst = base_ctrl_or_state
            src = None
        else:
            src = base_ctrl_or_state
            base_inst = getattr(src, "instance_state", None) or InstanceState()

        pkg = self._extract_package(src, base_inst)
        fork_id = f"fork-{_dt.datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        fork_dir = os.path.join(self._instances_root(), fork_id)
        os.makedirs(fork_dir, exist_ok=True)

        src_appdata = base_inst.pkg_appdata_dir(self.base_dir)
        os.makedirs(src_appdata, exist_ok=True)
        self._active_vfs_flush(src, pkg, src_appdata)
        fork_appdata_root = os.path.join(fork_dir, "appdata")
        self._copy_dir_clean(src_appdata, os.path.join(fork_appdata_root, pkg))

        dbg_cfg = self._extract_debug_config(src)
        _write_json(os.path.join(fork_dir, "debug_config.json"), dbg_cfg)
        rules_path = self._find_rules_path(src)
        if rules_path and os.path.isfile(rules_path):
            shutil.copy2(rules_path, os.path.join(fork_dir, "intercept_rules.json"))
        patches = self._collect_smali_patches(src)
        if patches:
            _write_json(os.path.join(fork_dir, "smali_patches.json"), patches)
        lc_patches = self._collect_libcarve_patches()
        if lc_patches:
            _write_json(os.path.join(fork_dir, "libcarve_patches.json"), lc_patches)

        fork_inst = InstanceState(
            instance_id=fork_id,
            label=(label or fork_id),
            package=pkg,
            app_root=base_inst.app_root or (getattr(src, "zone_root", "") if src is not None else ""),
            zone=getattr(src, "zone", base_inst.zone) if src is not None else base_inst.zone,
            apply_patches=bool(getattr(src, "apply_patches", base_inst.apply_patches)) if src is not None else base_inst.apply_patches,
            seam_profile=getattr(getattr(src, "seam_config", None), "unpack_mode", base_inst.seam_profile),
            net_mode=getattr(src, "net_mode", base_inst.net_mode) if src is not None else base_inst.net_mode,
            appdata_root=fork_appdata_root,
            selfcache_root=base_inst.selfcache_root,
            parent_id=base_inst.instance_id,
        )
        _write_json(os.path.join(fork_dir, "instance.json"), fork_inst.to_dict())
        if src is not None and hasattr(src, "instance_state"):
            src.instance_state = fork_inst
        return fork_inst

    def list_forks(self, pkg: Optional[str] = None) -> List[Dict[str, Any]]:
        root = self._instances_root()
        if not os.path.isdir(root):
            return []
        out: List[Dict[str, Any]] = []
        target_pkg = _safe_name(pkg) if pkg else None
        for fid in sorted(os.listdir(root)):
            fdir = os.path.join(root, fid)
            data = _read_json(os.path.join(fdir, "instance.json"))
            if not isinstance(data, dict):
                continue
            if target_pkg and _safe_name(data.get("package", "")) != target_pkg:
                continue
            data["path"] = fdir
            out.append(data)
        return out

    def load_fork(self, fork_id: str, controller: Any = None) -> InstanceState:
        fdir = os.path.join(self._instances_root(), _safe_name(fork_id))
        data = _read_json(os.path.join(fdir, "instance.json"))
        inst = InstanceState.from_dict(data)
        dbg_cfg = _read_json(os.path.join(fdir, "debug_config.json"), {}) or {}
        patches = _read_json(os.path.join(fdir, "smali_patches.json"), {}) or {}
        lc_patches = _read_json(os.path.join(fdir, "libcarve_patches.json"), []) or []
        if controller is not None:
            self._apply_debug_config(controller, dbg_cfg, inst=inst, patches=patches)
            self._restore_libcarve_patches(lc_patches)
        return inst

    def merge_fork(self, fork_id: str, target: Any = None) -> Dict[str, Any]:
        fdir = os.path.join(self._instances_root(), _safe_name(fork_id))
        inst_data = _read_json(os.path.join(fdir, "instance.json"))
        if not isinstance(inst_data, dict):
            return {"ok": False, "error": f"Fork nicht gefunden: {fork_id}"}
        fork_inst = InstanceState.from_dict(inst_data)
        pkg = _safe_name(fork_inst.package or "default.app", "default.app")

        # Flush live VFS of target if it currently points to this fork
        if target is not None and getattr(getattr(target, "instance_state", None), "instance_id", "") == fork_inst.instance_id:
            self._active_vfs_flush(target, pkg, fork_inst.pkg_appdata_dir(self.base_dir))

        main_inst = InstanceState(
            instance_id="main",
            label="main",
            package=pkg,
            app_root=fork_inst.app_root,
            zone=fork_inst.zone,
            apply_patches=fork_inst.apply_patches,
            seam_profile=fork_inst.seam_profile,
            net_mode=fork_inst.net_mode,
        )
        fork_appdata_pkg = os.path.join(fdir, "appdata", pkg)
        main_appdata_pkg = main_inst.pkg_appdata_dir(self.base_dir)
        self._copy_dir_clean(fork_appdata_pkg, main_appdata_pkg)

        dbg_cfg = _read_json(os.path.join(fdir, "debug_config.json"), {}) or {}
        patches = _read_json(os.path.join(fdir, "smali_patches.json"), {}) or {}
        lc_patches = _read_json(os.path.join(fdir, "libcarve_patches.json"), []) or []
        rules_src = os.path.join(fdir, "intercept_rules.json")

        if target is not None:
            if isinstance(target, InstanceState):
                target.instance_id = "main"
                target.label = "main"
                target.appdata_root = main_inst.appdata_root
                target.zone = main_inst.zone
                target.apply_patches = main_inst.apply_patches
                target.seam_profile = main_inst.seam_profile
                target.net_mode = main_inst.net_mode
            else:
                self._apply_debug_config(target, dbg_cfg, inst=main_inst, patches=patches)
                self._restore_libcarve_patches(lc_patches)
                rules_dst = self._find_rules_path(target)
                if os.path.isfile(rules_src) and rules_dst:
                    os.makedirs(os.path.dirname(rules_dst), exist_ok=True)
                    shutil.copy2(rules_src, rules_dst)
        return {"ok": True, "merged_fork": fork_id, "package": pkg, "instance": main_inst.to_dict()}

    def discard_fork(self, fork_id: str) -> bool:
        fdir = os.path.join(self._instances_root(), _safe_name(fork_id))
        if not os.path.isdir(fdir):
            return False
        shutil.rmtree(fdir, ignore_errors=True)
        return True

    @staticmethod
    def _apply_debug_config(controller: Any, dbg_cfg: Dict[str, Any], *,
                            inst: Optional[InstanceState] = None,
                            patches: Optional[Dict[str, str]] = None) -> None:
        if inst is not None and hasattr(controller, "instance_state"):
            controller.instance_state = inst
        if not isinstance(dbg_cfg, dict):
            return
        if "zone" in dbg_cfg and hasattr(controller, "zone"):
            controller.zone = dbg_cfg["zone"]
        if "zone_root" in dbg_cfg and dbg_cfg["zone_root"] and hasattr(controller, "zone_root"):
            controller.zone_root = dbg_cfg["zone_root"]
        if "apply_patches" in dbg_cfg and hasattr(controller, "apply_patches"):
            controller.apply_patches = bool(dbg_cfg["apply_patches"])
        if "app_entry" in dbg_cfg and hasattr(controller, "app_entry"):
            controller.app_entry = dbg_cfg["app_entry"]
        if "bps" in dbg_cfg and hasattr(controller, "bps"):
            controller.bps = list(dbg_cfg["bps"] or [])
        if "bp_meta" in dbg_cfg and hasattr(controller, "bp_meta"):
            controller.bp_meta = copy.deepcopy(dbg_cfg["bp_meta"] or {})
        if "app_bps" in dbg_cfg and hasattr(controller, "app_bps") and isinstance(dbg_cfg["app_bps"], dict):
            restored = {}
            for k, meta in dbg_cfg["app_bps"].items():
                parts = str(k).split("|")
                if len(parts) == 4:
                    try:
                        restored[(parts[0], parts[1], parts[2], int(parts[3]))] = copy.deepcopy(meta)
                    except Exception:
                        pass
            controller.app_bps = restored
        if "aps" in dbg_cfg and hasattr(controller, "aps"):
            controller.aps = copy.deepcopy(dbg_cfg["aps"] or [])
        if "jni_aps" in dbg_cfg and hasattr(controller, "jni_aps"):
            controller.jni_aps = copy.deepcopy(dbg_cfg["jni_aps"] or [])
        if "watches" in dbg_cfg and hasattr(controller, "watches"):
            controller.watches = list(dbg_cfg["watches"] or [])
        if "net_mode" in dbg_cfg and hasattr(controller, "net_mode"):
            controller.net_mode = dbg_cfg["net_mode"]
        if "seam_config" in dbg_cfg and hasattr(controller, "seam_config"):
            controller.seam_config = SeamConfig.from_dict(dbg_cfg["seam_config"])
        if "persist_session" in dbg_cfg and hasattr(controller, "persist_session"):
            controller.persist_session = bool(dbg_cfg["persist_session"])
            if getattr(controller, "seam_config", None) is not None:
                eff_ps = controller._should_persist_session() if hasattr(controller, "_should_persist_session") else bool(controller.persist_session)
                controller.seam_config.persist_appdata = eff_ps
        if patches and hasattr(controller, "smali_patches") and isinstance(controller.smali_patches, dict):
            controller.smali_patches.update(patches)


def apply_virtual_clock_spec(stubs: Any, spec: Optional[Dict[str, Any]], eng: Any = None) -> Dict[str, Any]:
    """Wendet eine Virtuelle-Uhr-Konfiguration (ISO/Epoch/TZ/Freeze/Shift/Reset) konsistent auf Stubs & OS-8 an."""
    from core.java.virtual_clock import (
        attach_virtual_clock_to_native_env,
        get_virtual_clock_state,
        reset_virtual_clock_to_host,
        set_virtual_clock,
        shift_virtual_clock,
    )

    if stubs is None:
        return {"ok": False, "hinweis": "stubs ist None"}
    if isinstance(spec, dict):
        if spec.get("reset"):
            tz_rst = spec.get("tz") if "tz" in spec else spec.get("tz_name")
            reset_virtual_clock_to_host(stubs, tz_name=tz_rst)
        iso_val = spec.get("iso")
        if iso_val is None:
            iso_val = spec.get("iso_str")
        if iso_val is None:
            iso_val = spec.get("datetime")
        tz_val = spec.get("tz") if "tz" in spec else spec.get("tz_name")
        epoch_val = spec.get("epoch_ms")
        frozen_val = spec.get("frozen")
        step_val = spec.get("step_ms")
        if any(x is not None for x in (epoch_val, iso_val, tz_val, frozen_val, step_val)):
            set_virtual_clock(
                stubs,
                epoch_ms=int(epoch_val) if epoch_val is not None else None,
                iso_str=str(iso_val) if iso_val is not None else None,
                tz_name=str(tz_val) if tz_val is not None else None,
                frozen=frozen_val,
                step_ms=step_val,
            )
        delta_ms = int(spec.get("delta_ms") or 0)
        s_days = float(spec.get("shift_days") or 0.0)
        s_hours = float(spec.get("shift_hours") or 0.0)
        s_mins = float(spec.get("shift_minutes") or 0.0)
        s_secs = float(spec.get("shift_seconds") or 0.0)
        if any(x != 0 for x in (delta_ms, s_days, s_hours, s_mins, s_secs)):
            shift_virtual_clock(
                stubs,
                delta_ms=delta_ms,
                days=s_days,
                hours=s_hours,
                minutes=s_mins,
                seconds=s_secs,
            )
    if eng is not None:
        try:
            attach_virtual_clock_to_native_env(stubs, eng)
        except Exception:
            pass
    return get_virtual_clock_state(stubs)


def extract_app_state_snapshot(stubs: Any, fusion: Optional[Dict[str, Any]] = None, db_query: str = "") -> Dict[str, Any]:
    """Liest SharedPreferences, SQLite-Tabellen, VFS-AppData-Dateien, WebEvents und Virtuelle Uhr aus Stubs/OS-8."""
    out: Dict[str, Any] = {
        "virtual_clock": None,
        "shared_prefs": {},
        "sqlite_dbs": {},
        "web_events": [],
        "vfs_files": [],
    }
    if stubs is None:
        return out
    try:
        from core.java.virtual_clock import get_virtual_clock_state
        out["virtual_clock"] = get_virtual_clock_state(stubs)
    except Exception:
        pass
    try:
        from core.java.sqlite_prefs_bridge import _to_py
    except Exception:
        _to_py = lambda x: x
    prefs_store = getattr(stubs, "_prefs_store", None) or {}
    if isinstance(prefs_store, dict):
        sp_out: Dict[str, Any] = {}
        for pname, store in list(prefs_store.items()):
            if not isinstance(store, dict):
                continue
            sp_out[str(pname)] = {str(k): _to_py(v) for k, v in list(store.items())[:100]}
        out["shared_prefs"] = sp_out

    conns = getattr(stubs, "_sqlite_conns", None) or {}
    q_raw = str(db_query or "").strip()
    q_target_db = ""
    q_sql = q_raw
    if ":" in q_raw and not q_raw.lower().startswith(("select", "pragma")):
        q_target_db, q_sql = [p.strip() for p in q_raw.split(":", 1)]
    if isinstance(conns, dict):
        dbs_out: Dict[str, Any] = {}
        for key, conn in list(conns.items()):
            db_info: Dict[str, Any] = {"tables": {}}
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [str(r[0]) for r in cur.fetchall() if r and not str(r[0]).startswith("sqlite_")]
                for tbl in tables[:40]:
                    try:
                        q_tbl = '"' + tbl.replace('"', '""') + '"'
                        rc = cur.execute(f"SELECT COUNT(*) FROM {q_tbl}").fetchone()
                        db_info["tables"][tbl] = int(rc[0]) if rc else 0
                    except Exception:
                        db_info["tables"][tbl] = None
                if q_sql and (not q_target_db or q_target_db == str(key)):
                    low_q = q_sql.lower().lstrip()
                    if low_q.startswith(("select", "pragma")):
                        cur.execute(q_sql)
                        cols = [d[0] for d in (cur.description or ())]
                        rows = cur.fetchmany(100)
                        db_info["query_rows"] = [
                            (dict(zip(cols, r)) if cols else list(r)) for r in rows
                        ]
            except Exception as e:
                db_info["error"] = repr(e)
            dbs_out[str(key)] = db_info
        out["sqlite_dbs"] = dbs_out

    vt = getattr(stubs, "view_tree", None)
    if vt is not None and hasattr(vt, "web_events"):
        try:
            out["web_events"] = list(getattr(vt, "web_events", ()) or ())[-40:]
        except Exception:
            pass

    eng = (fusion or {}).get("eng") if isinstance(fusion, dict) else getattr(stubs, "_vfs_eng", None)
    if eng is not None:
        try:
            from core.java.fs_bridge import get_shared_fs
            fs = get_shared_fs(eng)
            files_map = getattr(fs, "files", None) if fs is not None else None
            if isinstance(files_map, dict):
                vfs_list = []
                for fpath, data in sorted(files_map.items()):
                    if str(fpath).startswith("/data/"):
                        vfs_list.append({"path": str(fpath), "size": len(data or b"")})
                        if len(vfs_list) >= 80:
                            break
                out["vfs_files"] = vfs_list
        except Exception:
            pass
    return out


class GlassAppRunner:
    """Gemeinsamer glaeserner App-Runner fuer GUI & MCP (Headless/Interactive)."""

    def __init__(self, app_root: str, *, zone: str = "patched",
                 seam_config: Optional[SeamConfig] = None,
                 instance_state: Optional[InstanceState] = None,
                 base_dir: str = ".",
                 log_fn: Optional[Callable[[str], None]] = None):
        self.app_root = os.path.abspath(app_root) if app_root else ""
        self.zone = zone
        self.seam_config = seam_config or SeamConfig.from_profile("self_cache")
        self.instance_state = instance_state or InstanceState(app_root=self.app_root, zone=zone)
        self.base_dir = os.path.abspath(base_dir or ".")
        self.log_fn = log_fn or (lambda _msg: None)
        self.vm = None
        self.stubs = None
        self.tree = None
        self.net_log = None
        self.jdb = None
        self.session = None
        self.fusion = None
        self.provider = None

    def _vfs_read(self, eng, path: str) -> Optional[bytes]:
        from core.java.fusion_loader import NativeFusionLoader
        return NativeFusionLoader.vfs_read(eng, path)

    def _find_selfcache_so(self, pkg: str, lib_name: str) -> Optional[str]:
        if self.seam_config.unpack_mode != "self_cache":
            return None
        sc_dir = self.seam_config.self_cache_dir
        if not os.path.isabs(sc_dir):
            sc_dir = os.path.join(self.base_dir, sc_dir)
        cand = os.path.join(sc_dir, _safe_name(pkg, "default.app"), f"lib{lib_name}.so")
        return cand if os.path.isfile(cand) else None

    def _save_selfcache_so(self, pkg: str, filename: str, data: bytes) -> None:
        if self.seam_config.unpack_mode != "self_cache" or not data:
            return
        sc_dir = self.seam_config.self_cache_dir
        if not os.path.isabs(sc_dir):
            sc_dir = os.path.join(self.base_dir, sc_dir)
        dst_dir = os.path.join(sc_dir, _safe_name(pkg, "default.app"))
        os.makedirs(dst_dir, exist_ok=True)
        try:
            with open(os.path.join(dst_dir, os.path.basename(filename)), "wb") as fh:
                fh.write(bytes(data))
        except Exception:
            pass

    def build(self, *, native_fusion: bool = True, smali_patches: Optional[Dict[str, str]] = None,
              net_mode: str = "mock", net_responder=None, rules_path: Optional[str] = None,
              api_db=None, extra_so_overlays: Optional[Dict[str, bytes]] = None,
              virtual_clock: Optional[Dict[str, Any]] = None,
              net_log=None, net_condition=None,
              tolerant: bool = True, step_budget: Optional[int] = None,
              apply_overlays: Optional[bool] = None, overlay_opts: Optional[Dict[str, Any]] = None,
              app_ctx=None, cfg_dict: Optional[Dict[str, Any]] = None,
              force_sync_appdata: bool = False,
              fallback_java_on_fusion_err: bool = False,
              jni_aps: Optional[List[Dict[str, Any]]] = None,
              on_load_library=None, on_load_path=None,
              cancel_check: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        from core.java.dalvik import DalvikVM
        from core.java.class_provider import StudioClassProvider
        from core.java.dex_loader import AndroidStubs
        from core.java.lifecycle import LifecycleRunner, _StubRuntime
        from core.java.view_tree import install_view_stubs
        from core.java.net_recorder import install_net_stubs, NetLog
        from core.java.asset_bridge import install_asset_stubs, seed_assets_from_dir
        from core.java.debug import JavaDebugger

        if cancel_check and cancel_check():
            return {"cancelled": True}

        vm = DalvikVM()
        vm.lenient = bool(tolerant)
        if step_budget is not None:
            vm.step_budget = int(step_budget)
        provider = StudioClassProvider(self.app_root, zone=self.zone)
        if smali_patches and hasattr(provider, "patches"):
            provider.patches.update(smali_patches)

        _want_overlays = (self.zone == "live") if apply_overlays is None else bool(apply_overlays)
        resolved_overlays = None
        man = None
        man_pkg = None
        man_entry = None
        if self.app_root:
            try:
                from core.java.manifest import parse_manifest
                man = parse_manifest(self.app_root)
                man_pkg = getattr(man, "package", None)
                man_entry = (man.entry() or None) if hasattr(man, "entry") else None
            except Exception:
                man = None

        if _want_overlays:
            try:
                from services.workspace_overlay_service import WorkspaceOverlayResolver
                _tmp_pkg = man_pkg or self.instance_state.package or "default.app"
                _ov_kw = dict(overlay_opts or {})
                if app_ctx is not None:
                    _ov_kw.setdefault("app", app_ctx)
                if cfg_dict is not None:
                    _ov_kw.setdefault("cfg", cfg_dict)
                resolved_overlays = WorkspaceOverlayResolver.collect(
                    self.base_dir, _tmp_pkg, self.app_root, **_ov_kw,
                )
                if resolved_overlays.get("smali_patches"):
                    if hasattr(provider, "apply_smali_patches"):
                        np = provider.apply_smali_patches(resolved_overlays["smali_patches"])
                        if np:
                            self.log_fn(f"> {np} Smali-Patch(es) live im ClassProvider ueberlagert")
            except Exception as _eov:
                self.log_fn(f"> Workspace-Overlay Warnung: {_eov!r}")

        if cancel_check and cancel_check():
            return {"cancelled": True}

        vm.class_provider = provider
        stubs = AndroidStubs(vm=vm)
        stubs._seam_config = self.seam_config
        try:
            stubs.seed_framework_statics(vm)
        except Exception:
            pass
        try:
            from core.java.rn_producer import install_rn_mount_stubs
            install_rn_mount_stubs(stubs)
        except Exception:
            pass
        tree = install_view_stubs(stubs, app_root=self.app_root)
        net_log = net_log if net_log is not None else NetLog()
        install_net_stubs(stubs, responder=net_responder, net_log=net_log,
                          api_db=api_db, rules_path=rules_path,
                          net_condition=net_condition)
        install_asset_stubs(stubs)
        if self.app_root:
            _na = seed_assets_from_dir(stubs, self.app_root)
            if _na:
                self.log_fn(f"> Assets bereitgestellt (AssetManager): {_na} Dateien")

        if cancel_check and cancel_check():
            return {"cancelled": True}

        vm.runtime = _StubRuntime(stubs)

        if man_pkg and hasattr(stubs, "set_profile"):
            try:
                from core.java.device_profile import DeviceProfile
                stubs.set_profile(DeviceProfile(package=man_pkg), vm=vm)
            except Exception:
                try:
                    stubs.profile.package = man_pkg
                except Exception:
                    pass
        pkg = man_pkg or self.instance_state.package or getattr(getattr(stubs, "profile", None), "package", None) or "com.example.app"
        self.instance_state.package = pkg
        if virtual_clock is not None:
            try:
                apply_virtual_clock_spec(stubs, virtual_clock)
            except Exception:
                pass
        if self.seam_config.persist_appdata and (
            force_sync_appdata or bool(native_fusion) or os.path.abspath(self.base_dir) != os.path.abspath(".")
        ):
            try:
                from core.java.sqlite_prefs_bridge import sync_java_appdata_from_dir
                sync_java_appdata_from_dir(stubs, self.instance_state.pkg_appdata_dir(self.base_dir))
            except Exception:
                pass

        hook = None
        fusion = None
        session = None
        nld = f"/data/app/{pkg}/lib/arm64"
        if native_fusion and self.seam_config.unpack_mode != "noop_native" and not (cancel_check and cancel_check()):
            try:
                from services.native_emu_service import NativeEmuService
                from services.native_ffi_service import NativeFfiService
                from services.native_jni_env_service import NativeJniEnvService
                from services.native_os_service import NativeOsService
                from core.java.native_link import install_native_link, NativeMethodTable
                from core.java.jni_boundary import install_jni_boundary
                from core.java.runtime import VmBackedJavaRuntime
                from core.java.fusion_autoload import mirror_tree_to_vfs
                from core.java.fusion_dlopen import install_native_dlopen
                from core.java.fusion_loader import NativeFusionLoader
                from core.java.fs_bridge import install_fs_stubs, get_shared_fs
                from core.java.debug_unified import DebugSession

                svc = NativeEmuService(publish=False)
                eng = svc.new_engine()
                eng.cpu.yield_gil = True
                stubs._vfs_eng = eng
                ffi = NativeFfiService(publish=False)
                jni = NativeJniEnvService(publish=False, app_base=self.app_root)
                jni.install(eng, ffi, set_entry_reg=None)
                jni.java_runtime = VmBackedJavaRuntime(vm, jni)

                os_svc = NativeOsService(publish=False)
                os_svc.attach_env(eng)
                os_svc.install(eng)
                try:
                    from core.java.virtual_clock import attach_virtual_clock_to_native_env
                    attach_virtual_clock_to_native_env(stubs, eng)
                except Exception:
                    pass

                if self.app_root:
                    _nma = mirror_tree_to_vfs(os_svc, eng, self.app_root, "assets", "/data/app/assets")
                    _nml = mirror_tree_to_vfs(os_svc, eng, self.app_root, "lib/arm64-v8a", nld)
                    self.log_fn(f"> VFS gespiegelt: {_nma} Assets, {_nml} Libs -> {nld} (App liest selbst)")

                apk_bytes = None
                if self.app_root:
                    for cand in ("merged_base.apk", "base.apk"):
                        ap = os.path.join(os.path.dirname(self.app_root), cand)
                        if os.path.isfile(ap):
                            with open(ap, "rb") as afh:
                                apk_bytes = afh.read()
                            break
                install_fs_stubs(stubs, eng, pkg=pkg, native_lib_dir=nld, apk_bytes=apk_bytes)
                fs = get_shared_fs(eng)
                if net_condition is not None:
                    stubs._net_os_eng = eng
                    net_condition.apply_to_os(eng)
                merged_so_overlays = {}
                if _want_overlays and resolved_overlays and resolved_overlays.get("vfs_so_overlays"):
                    merged_so_overlays.update(resolved_overlays["vfs_so_overlays"])
                if extra_so_overlays:
                    merged_so_overlays.update(extra_so_overlays)
                if merged_so_overlays and fs is not None:
                    ov_names = set()
                    for soname, blob in merged_so_overlays.items():
                        if blob:
                            bn = os.path.basename(soname)
                            fs.add_file(f"{nld}/{bn}", bytes(blob))
                            ov_names.add(bn)
                    eng._vfs_overlay_libs = ov_names
                    ov_sum = (resolved_overlays or {}).get("summary") or {}
                    for warn in ov_sum.get("warnings") or ():
                        self.log_fn(f"> [Overlay-Warnung] {warn}")
                    if ov_names:
                        self.log_fn(
                            "> Native Live-Overlays in VFS 2.0 gemountet: %d Datei(en) "
                            "(LibForge=%d, LibCarve=%d, Hex=%d, Replace=%d) -> %s"
                            % (
                                len(ov_names),
                                ov_sum.get("libforge", 0),
                                ov_sum.get("libcarve", 0),
                                ov_sum.get("hex", 0),
                                ov_sum.get("lib_replace", 0),
                                ", ".join(sorted(ov_names)),
                            )
                        )
                if self.seam_config.persist_appdata and fs is not None:
                    _app_dir = self.instance_state.pkg_appdata_dir(self.base_dir)
                    _nsync = sync_appdata_into_vfs(fs, pkg, _app_dir)
                    if _nsync:
                        self.log_fn(f"> AppData synchronisiert: {_nsync} Dateien aus {_app_dir}")
                self.log_fn(
                    f"> VFS geteilt (Java-I/O<->nativ); base.apk gemountet: {'ja' if apk_bytes else 'nein'}; nativeLibraryDir={nld}"
                )

                tbl = NativeMethodTable()
                fusion_loader = NativeFusionLoader(
                    svc=svc, eng=eng, jni=jni, table=tbl, ffi=ffi, stubs=stubs,
                    app_root=self.app_root, pkg=pkg, base_dir=self.base_dir,
                    seam_config=self.seam_config, log_fn=self.log_fn,
                )

                if on_load_library is not None or on_load_path is not None:
                    vm._fusion_load_library = on_load_library or fusion_loader.load_library
                    vm._fusion_load_path = on_load_path or fusion_loader.load_path

                install_native_link(vm, eng, jni, table=tbl,
                                    on_load_library=on_load_library or fusion_loader.load_library,
                                    on_load_path=on_load_path or fusion_loader.load_path)
                dl = install_native_dlopen(eng, ffi, svc, jni, tbl,
                                           vfs_read=lambda p: fusion_loader.vfs_read(eng, p),
                                           fusion_loader=fusion_loader)
                hook = install_jni_boundary(vm.bridge, vm=vm)
                for _ap_spec in (jni_aps or ()):
                    try:
                        hook.add_action_point(**_ap_spec)
                    except Exception as _e2:
                        self.log_fn(f"> Grenz-AP-Fehler: {_e2!r}")
                fusion = {"eng": eng, "jni": jni, "table": tbl, "bridge": vm.bridge,
                          "hook": hook, "svc": svc, "os": os_svc, "ffi": ffi, "dl": dl,
                          "loader": fusion_loader, "stubs": stubs}
                session = DebugSession(eng, jni, vm=vm)
            except Exception as _ef:
                if not fallback_java_on_fusion_err:
                    raise
                self.log_fn(f"> Fusion-Setup fehlgeschlagen (Java-only Fallback): {_ef!r}")
                fusion = None
                hook = None
                session = None

        self.seam_config.apply_to_vm(vm, native_fusion=bool(fusion), jni_hook=hook)
        jdb = session.jdbg if session is not None else JavaDebugger(vm)
        self.vm = vm
        self.stubs = stubs
        self.tree = tree
        self.net_log = net_log
        self.jdb = jdb
        self.session = session
        self.fusion = fusion
        self.provider = provider
        self.runner = LifecycleRunner(vm, provider=provider, with_stubs=False, stubs=stubs)
        self.runner.seam_config = self.seam_config
        return {
            "vm": vm, "stubs": stubs, "tree": tree, "net_log": net_log,
            "jdb": jdb, "session": session, "fusion": fusion, "provider": provider,
            "man_pkg": pkg, "man_entry": man_entry, "nld": nld,
            "resolved_overlays": resolved_overlays,
        }

    def run(self, *, entry: Optional[str] = None, max_steps: int = 200000,
            max_wall_s: float = 30.0, native_fusion: bool = True,
            smali_patches: Optional[Dict[str, str]] = None,
            extra_so_overlays: Optional[Dict[str, bytes]] = None,
            virtual_clock: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.vm is None:
            self.build(native_fusion=native_fusion, smali_patches=smali_patches,
                       extra_so_overlays=extra_so_overlays, virtual_clock=virtual_clock)
        elif virtual_clock is not None and self.stubs is not None:
            try:
                apply_virtual_clock_spec(
                    self.stubs,
                    virtual_clock,
                    eng=(self.fusion or {}).get("eng") if isinstance(self.fusion, dict) else None,
                )
            except Exception:
                pass
        self.vm.max_steps = int(max_steps)
        self.vm.step_budget = int(max_steps)
        self.vm.wall_deadline = (time.monotonic() + float(max_wall_s)) if max_wall_s else None
        self.jdb.max_steps = int(max_steps)
        if hasattr(self.jdb, "max_wall_s"):
            self.jdb.max_wall_s = float(max_wall_s)
        if self.fusion and isinstance(self.fusion, dict) and self.fusion.get("loader") is not None and max_wall_s:
            try:
                ldr = self.fusion["loader"]
                ldr.max_total_init_wall_s = min(float(ldr.max_total_init_wall_s), max(1.0, float(max_wall_s) * 0.45))
            except Exception:
                pass
        t0 = time.monotonic()
        try:
            info = self.runner.run_app(studio_root=self.app_root, launcher=(entry or "").strip() or None)
        finally:
            self.vm.wall_deadline = None
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if self.tree is not None and getattr(self.tree, "root", None) is None:
            rec = getattr(self.stubs, "rn_mount_recorder", None)
            if rec is not None and getattr(rec, "nodes", None):
                try:
                    rn_surf = rec.surface(relayout=not getattr(rec, "_has_layout", False))
                    if getattr(rn_surf, "tree", None) is not None and rn_surf.tree.root is not None:
                        self.tree = rn_surf.tree
                except Exception:
                    pass
        if self.tree is not None and hasattr(self.tree, "ensure_layout"):
            try:
                self.tree.ensure_layout()
            except Exception:
                pass

        if self.seam_config.persist_appdata and (
            bool(self.fusion) or os.path.abspath(self.base_dir) != os.path.abspath(".")
        ):
            try:
                from core.java.sqlite_prefs_bridge import persist_java_appdata_to_dir
                persist_java_appdata_to_dir(self.stubs, self.instance_state.pkg_appdata_dir(self.base_dir))
            except Exception:
                pass
        if self.fusion and self.seam_config.persist_appdata:
            try:
                from core.java.fs_bridge import get_shared_fs
                fs = get_shared_fs(self.fusion["eng"])
                if fs is not None:
                    persist_appdata_from_vfs(
                        fs, self.instance_state.package,
                        self.instance_state.pkg_appdata_dir(self.base_dir),
                    )
            except Exception:
                pass

        return {
            "ok": True,
            "package": self.instance_state.package,
            "instance_id": self.instance_state.instance_id,
            "elapsed_ms": elapsed_ms,
            "steps": getattr(self.vm, "steps", 0),
            "activity": info.get("activity") if isinstance(info, dict) else None,
            "view_nodes": len(self.tree.nodes()) if self.tree is not None and callable(getattr(self.tree, "nodes", None)) else 0,
            "net_calls": len(self.net_log.entries) if self.net_log is not None and hasattr(self.net_log, "entries") else 0,
            "native_methods": len(self.fusion["table"]._exact) if self.fusion and hasattr(self.fusion["table"], "_exact") else 0,
        }
