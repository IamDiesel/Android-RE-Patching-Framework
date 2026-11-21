# -*- coding: utf-8 -*-
"""services.ki_workspace_sync_service — Geteilter Mensch<->KI Zustand fuer Workspace & API Inspector.

Speichert Fokus, ungegesicherte Entwuerfe (Clobber-Schutz), aktive Patch-Statistiken
und von der KI vorbereitete Vorschlaege unter `data/ki_exchange/`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, Optional

from core.application.session_state import SessionState
from core.infrastructure import json_store


class KiWorkspaceSyncService:
    """Kapselt push/pull/status fuer Workspace (Smali, LibForge, LibCarve) & API Inspector."""

    def __init__(self, base_dir: str = "."):
        self.base_dir = os.path.abspath(base_dir or ".")
        self.exchange_dir = os.path.join(self.base_dir, "data", "ki_exchange")
        os.makedirs(self.exchange_dir, exist_ok=True)
        self.ws_file = os.path.join(self.exchange_dir, "workspace_sync.json")
        self.api_file = os.path.join(self.exchange_dir, "api_sync.json")

    @staticmethod
    def _read_json(path: str) -> dict:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
        return {"seq": 0, "writer": "", "updated_at": "", "human_state": {}, "ki_staged": {}}

    @staticmethod
    def _write_json(path: str, payload: dict) -> None:
        json_store.atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    # ---- Workspace Snapshot Extraction & Sync ----
    @staticmethod
    def extract_workspace_focus(app: Any) -> Dict[str, Any]:
        ws = getattr(app, "workspace_tab", None)
        lc = getattr(ws, "lib_carve", None) if ws is not None else None
        ss = getattr(ws, "smali_studio", None) if ws is not None else None
        active_sub = ""
        try:
            pb = getattr(ws, "patch_book", None)
            if pb is not None:
                active_sub = pb.tab(pb.select(), "text")
        except Exception:
            pass

        lc_state: Dict[str, Any] = {}
        if lc is not None:
            ctrl = getattr(lc, "controller", None)
            ed = getattr(lc, "editor", None)
            lc_state = {
                "lib": os.path.basename(getattr(ctrl, "image_path", "") or ""),
                "lib_path": getattr(ctrl, "image_path", "") or "",
                "abi": getattr(ctrl, "image_abi", "arm64-v8a"),
                "vaddr": f"0x{ctrl.current_vaddr:x}" if getattr(ctrl, "current_vaddr", None) is not None else "",
                "func": getattr(ctrl, "current_func_name", "") or "",
                "mode": ed.var_patch_mode.get() if (ed and hasattr(ed, "var_patch_mode")) else "asm",
                "draft": ed.get_edit_text()[:8000] if ed else "",
                "is_dirty": bool(getattr(ed, "is_dirty", False)),
            }

        ss_state: Dict[str, Any] = {}
        if ss is not None:
            sctrl = getattr(ss, "controller", None)
            sed = getattr(ss, "editor", None)
            ss_state = {
                "file": getattr(sctrl, "current_smali_file", "") or "",
                "method": getattr(sctrl, "current_method_name", "") or "",
                "draft": sed.get_edit_text()[:8000] if (sed and hasattr(sed, "get_edit_text")) else "",
            }

        return {
            "active_subtab": active_sub,
            "libcarve": lc_state,
            "smali_studio": ss_state,
            "counts": {
                "smali": len(SessionState.active_smali_patches),
                "libcarve": len(SessionState.active_libcarve_patches),
                "hex": len(SessionState.active_hex_patches),
                "lib_replace": len(SessionState.active_lib_replacements),
            },
        }

    def workspace_push(self, app: Any) -> Dict[str, Any]:
        doc = self._read_json(self.ws_file)
        doc["seq"] = int(doc.get("seq", 0)) + 1
        doc["writer"] = "human"
        doc["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        doc["human_state"] = self.extract_workspace_focus(app)
        self._write_json(self.ws_file, doc)
        hs = doc["human_state"]
        lc = hs.get("libcarve") or {}
        focus_lbl = f"{lc.get('lib')}@{lc.get('vaddr')}" if lc.get("lib") else (hs.get("smali_studio") or {}).get("file") or "Workspace"
        return {"ok": True, "msg": f"Fokus gesendet ({focus_lbl}, seq={doc['seq']})"}

    def workspace_pull(self, app: Any, *, ask_overwrite=None) -> Dict[str, Any]:
        doc = self._read_json(self.ws_file)
        staged = doc.get("ki_staged") or {}
        applied_parts = []

        # Aktive Session ggf. nachladen
        svc = getattr(app, "active_session_svc", None)
        if svc is not None and svc.reload_if_changed():
            if hasattr(app, "_reflect_active_session"):
                app._reflect_active_session(initial=True)
            applied_parts.append(f"{len(svc.patches())} aktive Patch(es)")

        ws = getattr(app, "workspace_tab", None)
        lc = getattr(ws, "lib_carve", None) if ws is not None else None
        lc_draft = staged.get("libcarve_draft")
        if isinstance(lc_draft, dict) and lc is not None:
            draft_code = str(lc_draft.get("code") or "")
            mode = str(lc_draft.get("mode") or "asm")
            do_apply = True
            if getattr(lc.editor, "is_dirty", False) and draft_code:
                if callable(ask_overwrite):
                    do_apply = bool(ask_overwrite("LibCarve-Editor enthaelt ungesicherte Aenderungen.\nKI-Entwurf trotzdem uebernehmen?"))
                else:
                    do_apply = False
            if do_apply:
                tgt_lib = lc_draft.get("lib")
                tgt_vaddr = lc_draft.get("vaddr")
                va_int = int(tgt_vaddr, 0) if isinstance(tgt_vaddr, str) and tgt_vaddr else tgt_vaddr
                if tgt_lib and (lc.controller.image is None or os.path.basename(lc.controller.image_path) != os.path.basename(tgt_lib)):
                    cands = lc.controller.discover_libraries()
                    hit = next((c for c in cands if c["name"] == os.path.basename(tgt_lib)), None)
                    if hit:
                        def _on_ready():
                            if draft_code:
                                lc.editor.var_patch_mode.set(mode)
                                lc.editor.set_edit_text(draft_code, mark_dirty=False)
                        lc.controller.load_so_path(hit["path"], target_vaddr=va_int, on_ready=_on_ready)
                else:
                    if va_int is not None and lc.controller.image is not None:
                        lc.controller.navigate_to(va_int, add_to_history=True)
                    if draft_code:
                        lc.editor.var_patch_mode.set(mode)
                        lc.editor.set_edit_text(draft_code, mark_dirty=False)
                applied_parts.append("LibCarve-Entwurf")

        if not applied_parts:
            return {"ok": True, "msg": "Keine neuen KI-Ergebnisse im Workspace"}
        return {"ok": True, "msg": "Uebernommen: " + ", ".join(applied_parts)}

    def workspace_status(self, app: Any) -> Dict[str, Any]:
        doc = self._read_json(self.ws_file)
        staged = doc.get("ki_staged") or {}
        has_ki = bool(staged.get("libcarve_draft"))
        svc = getattr(app, "active_session_svc", None)
        ext_pending = bool(svc is not None and svc.reload_if_changed())
        if has_ki or ext_pending:
            return {"ok": True, "msg": f"KI-Daten bereit (seq={doc.get('seq', 0)}, Entwurf={has_ki}, Session-Update={ext_pending})"}
        return {"ok": True, "msg": f"Synchron (writer={doc.get('writer') or '-'}, seq={doc.get('seq', 0)})"}

    def stage_from_ki(self, staged: dict, base_seq: Optional[int] = None) -> Dict[str, Any]:
        doc = self._read_json(self.ws_file)
        cur_seq = int(doc.get("seq", 0))
        if base_seq is not None and int(base_seq) != cur_seq:
            return {"ok": False, "error": "seq_conflict", "current_seq": cur_seq}
        doc["seq"] = cur_seq + 1
        doc["writer"] = "ki"
        doc["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        doc["ki_staged"] = dict(staged or {})
        self._write_json(self.ws_file, doc)
        return {"ok": True, "seq": doc["seq"]}

    def read_workspace_sync(self) -> Dict[str, Any]:
        return self._read_json(self.ws_file)

    # ---- API Inspector Sync ----
    def api_push(self, app: Any) -> Dict[str, Any]:
        api = getattr(app, "api_tab", None)
        doc = self._read_json(self.api_file)
        doc["seq"] = int(doc.get("seq", 0)) + 1
        doc["writer"] = "human"
        doc["updated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        selected_ids = []
        flt = ""
        rules_count = 0
        if api is not None:
            try:
                flt = api.ent_filter.get().strip()
                selected_ids = [int(api.tree.item(i, "values")[0]) for i in api.tree.selection() if api.tree.item(i, "values")]
                if hasattr(api, "rule_mgr") and hasattr(api.rule_mgr, "rules"):
                    rules_count = len(api.rule_mgr.rules)
            except Exception:
                pass
        doc["human_state"] = {"filter": flt, "selected_ids": selected_ids[:30], "rules_count": rules_count}
        self._write_json(self.api_file, doc)
        return {"ok": True, "msg": f"API-Fokus gesendet ({len(selected_ids)} gewaehlt, seq={doc['seq']})"}

    def api_pull(self, app: Any) -> Dict[str, Any]:
        api = getattr(app, "api_tab", None)
        if api is not None and hasattr(api, "poll_db"):
            try:
                api.poll_db()
            except Exception:
                pass
        doc = self._read_json(self.api_file)
        return {"ok": True, "msg": f"API Inspector DB aktualisiert (seq={doc.get('seq', 0)})"}

    def api_status(self, app: Any) -> Dict[str, Any]:
        doc = self._read_json(self.api_file)
        return {"ok": True, "msg": f"API-Sync seq={doc.get('seq', 0)} (writer={doc.get('writer') or '-'})"}
