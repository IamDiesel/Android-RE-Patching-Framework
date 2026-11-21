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
        if patches and hasattr(controller, "smali_patches") and isinstance(controller.smali_patches, dict):
            controller.smali_patches.update(patches)


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
              api_db=None, extra_so_overlays: Optional[Dict[str, bytes]] = None) -> Dict[str, Any]:
        from core.java.dalvik import DalvikVM
        from core.java.class_provider import StudioClassProvider
        from core.java.dex_loader import AndroidStubs
        from core.java.lifecycle import LifecycleRunner, _StubRuntime
        from core.java.view_tree import install_view_stubs
        from core.java.net_recorder import install_net_stubs, NetLog
        from core.java.asset_bridge import install_asset_stubs, seed_assets_from_dir
        from core.java.debug import JavaDebugger

        vm = DalvikVM()
        vm.lenient = True
        provider = StudioClassProvider(self.app_root, zone=self.zone)
        if smali_patches and hasattr(provider, "patches"):
            provider.patches.update(smali_patches)
        resolved_overlays = None
        if self.zone == "live":
            try:
                from services.workspace_overlay_service import WorkspaceOverlayResolver
                _tmp_pkg = self.instance_state.package or "default.app"
                if self.app_root:
                    try:
                        from core.java.manifest import parse_manifest
                        _tmp_pkg = getattr(parse_manifest(self.app_root), "package", "") or _tmp_pkg
                    except Exception:
                        pass
                resolved_overlays = WorkspaceOverlayResolver.collect(self.base_dir, _tmp_pkg, self.app_root)
                if resolved_overlays.get("smali_patches") and hasattr(provider, "apply_smali_patches"):
                    provider.apply_smali_patches(resolved_overlays["smali_patches"])
            except Exception as _eov:
                self.log_fn(f"> Live-Overlay Warnung: {_eov!r}")
        vm.class_provider = provider
        stubs = AndroidStubs(vm=vm)
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
        net_log = NetLog()
        install_net_stubs(stubs, responder=net_responder, net_log=net_log,
                          api_db=api_db, rules_path=rules_path)
        install_asset_stubs(stubs)
        if self.app_root:
            seed_assets_from_dir(stubs, self.app_root)
        vm.runtime = _StubRuntime(stubs)

        man_pkg = None
        if self.app_root:
            try:
                from core.java.manifest import parse_manifest
                from core.java.device_profile import DeviceProfile
                man = parse_manifest(self.app_root)
                man_pkg = getattr(man, "package", None)
                if man_pkg and hasattr(stubs, "set_profile"):
                    stubs.set_profile(DeviceProfile(package=man_pkg), vm=vm)
            except Exception:
                man_pkg = None
        pkg = man_pkg or self.instance_state.package or getattr(getattr(stubs, "profile", None), "package", None) or "com.example.app"
        self.instance_state.package = pkg
        if self.seam_config.persist_appdata and (
            bool(native_fusion) or os.path.abspath(self.base_dir) != os.path.abspath(".")
        ):
            try:
                from core.java.sqlite_prefs_bridge import sync_java_appdata_from_dir
                sync_java_appdata_from_dir(stubs, self.instance_state.pkg_appdata_dir(self.base_dir))
            except Exception:
                pass

        hook = None
        fusion = None
        session = None
        if native_fusion and self.seam_config.unpack_mode != "noop_native":
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
            stubs._vfs_eng = eng
            ffi = NativeFfiService(publish=False)
            jni = NativeJniEnvService(publish=False, app_base=self.app_root)
            jni.install(eng, ffi, set_entry_reg=None)
            jni.java_runtime = VmBackedJavaRuntime(vm, jni)

            os_svc = NativeOsService(publish=False)
            os_svc.attach_env(eng)
            os_svc.install(eng)

            nld = f"/data/app/{pkg}/lib/arm64"
            if self.app_root:
                mirror_tree_to_vfs(os_svc, eng, self.app_root, "assets", "/data/app/assets")
                mirror_tree_to_vfs(os_svc, eng, self.app_root, "lib/arm64-v8a", nld)

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
            merged_so_overlays = {}
            if resolved_overlays and resolved_overlays.get("vfs_so_overlays"):
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
            if self.seam_config.persist_appdata and fs is not None:
                sync_appdata_into_vfs(fs, pkg, self.instance_state.pkg_appdata_dir(self.base_dir))

            tbl = NativeMethodTable()
            fusion_loader = NativeFusionLoader(
                svc=svc, eng=eng, jni=jni, table=tbl, ffi=ffi, stubs=stubs,
                app_root=self.app_root, pkg=pkg, base_dir=self.base_dir,
                seam_config=self.seam_config, log_fn=self.log_fn,
            )

            install_native_link(vm, eng, jni, table=tbl,
                                on_load_library=fusion_loader.load_library,
                                on_load_path=fusion_loader.load_path)
            dl = install_native_dlopen(eng, ffi, svc, jni, tbl,
                                       vfs_read=lambda p: fusion_loader.vfs_read(eng, p),
                                       fusion_loader=fusion_loader)
            hook = install_jni_boundary(vm.bridge, vm=vm)
            fusion = {"eng": eng, "jni": jni, "table": tbl, "bridge": vm.bridge,
                      "hook": hook, "svc": svc, "os": os_svc, "ffi": ffi, "dl": dl,
                      "loader": fusion_loader}
            session = DebugSession(eng, jni, vm=vm)

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
        return {
            "vm": vm, "stubs": stubs, "tree": tree, "net_log": net_log,
            "jdb": jdb, "session": session, "fusion": fusion, "provider": provider,
        }

    def run(self, *, entry: Optional[str] = None, max_steps: int = 200000,
            max_wall_s: float = 30.0, native_fusion: bool = True,
            smali_patches: Optional[Dict[str, str]] = None,
            extra_so_overlays: Optional[Dict[str, bytes]] = None) -> Dict[str, Any]:
        if self.vm is None:
            self.build(native_fusion=native_fusion, smali_patches=smali_patches,
                       extra_so_overlays=extra_so_overlays)
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
