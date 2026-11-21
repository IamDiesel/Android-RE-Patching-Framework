import os
import tkinter as tk
from tkinter import messagebox, simpledialog
from services.patch_service import PatchService
from ui.dialogs.fuzzy_matcher_dialog import FuzzyMatchDialog

class FavoritePatchesController:
    def __init__(self, view, ws, fav_service):
        self.view = view
        self.ws = ws
        self.fav_service = fav_service

    def get_active_patches(self, fav):
        return fav.get("patches", [fav])

    def save_current(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        idx = int(sel[0])
        fav = self.fav_service.favs[idx]

        fav["name"] = self.view.ent_name.get()
        patches = fav.get("patches", None)
        current_idx = self.view.current_sub_patch_idx

        if patches is not None:
            ptype = patches[current_idx].get("type", "smali")
            if ptype == "lib_replace":
                patches[current_idx]["target"] = self.view.ent_file.get().strip()
                patches[current_idx]["source"] = self.view.txt_edit.get("1.0", tk.END).strip()
            elif ptype == "hex":
                patches[current_idx]["file"] = self.view.ent_file.get().strip()
                patches[current_idx]["patch"] = self.view.txt_edit.get("1.0", tk.END).strip()
            elif ptype == "libcarve":
                patches[current_idx]["file"] = self.view.ent_file.get().strip()
                patches[current_idx]["edit"] = self.view.txt_edit.get("1.0", tk.END).strip()
            elif ptype == "libforge":
                patches[current_idx]["name"] = self.view.ent_file.get().strip()
                patches[current_idx]["source_code"] = self.view.txt_edit.get("1.0", tk.END).rstrip("\n")
            else:
                patches[current_idx]["file"] = self.view.ent_file.get().strip()
                patches[current_idx]["orig"] = self.view.txt_orig.get("1.0", tk.END).strip()
                patches[current_idx]["edit"] = self.view.txt_edit.get("1.0", tk.END).strip()
        else:
            new_patch = {"type": "smali", "file": self.view.ent_file.get(), "orig": self.view.txt_orig.get("1.0", tk.END).strip(),
                         "edit": self.view.txt_edit.get("1.0", tk.END).strip()}
            fav["patches"] = [new_patch]
            for key in ["file", "orig", "edit"]:
                fav.pop(key, None)

        self.fav_service.save_favs()
        self.view.populate_list()
        self.view.tree_favs.selection_set(str(idx))

    def delete_current(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        n = len(sel)
        msg = "Favorit komplett löschen?" if n == 1 else f"Alle {n} markierten Favoriten komplett löschen?"
        if messagebox.askyesno("Löschen", msg, parent=self.view):
            for idx in sorted({int(s) for s in sel}, reverse=True):
                self.fav_service.delete_favorite(idx)
            self.view.populate_list()
            self.view.ent_name.delete(0, tk.END)
            self.view.ent_file.delete(0, tk.END)
            self.view.txt_orig.delete("1.0", tk.END)
            self.view.txt_edit.delete("1.0", tk.END)

    def _apply_libcarve_favorite_patch(self, current_patch: dict):
        """Wendet einen LibCarve-Favoriten via Wildcard-Pattern-Match auf die Ziel-.so an."""
        from core.application.session_state import SessionState
        from core.native.loader import ElfImage
        from core.native.nindex import NativeIndex
        from services.libcarve_pattern_service import LibCarvePatternService
        import os

        lc = getattr(self.ws, "lib_carve", None)
        ctrl = getattr(lc, "controller", None)
        if lc is None or ctrl is None:
            return {"ok": False, "reason": "LibCarve nicht initialisiert"}

        target_file = os.path.basename(current_patch.get("file", "") or "")
        # Pruefe ob passende Lib in LibCarve geladen ist, sonst automatisch aus discover_libraries waehlen
        if ctrl.image is None or (target_file and os.path.basename(ctrl.image_path) != target_file):
            cands = ctrl.discover_libraries()
            match_lib = next((c for c in cands if c["name"] == target_file), None)
            if match_lib is None and cands:
                match_lib = cands[0]
            if match_lib:
                try:
                    img = ElfImage.from_file(match_lib["path"])
                    idx = NativeIndex().build(img, fast=True)
                    ctrl.image = img
                    ctrl.image_path = match_lib["path"]
                    ctrl.image_abi = match_lib.get("abi", "arm64-v8a")
                    ctrl.index = idx
                except Exception as e:
                    return {"ok": False, "reason": repr(e)}

        if ctrl.image is None or ctrl.index is None:
            return {"ok": False, "reason": f"Ziel-Lib '{target_file}' nicht gefunden"}

        mres = LibCarvePatternService.match_patch(ctrl.image, ctrl.index, current_patch)
        if mres.get("ok") and mres.get("vaddr") is not None:
            cp = dict(current_patch)
            cp["type"] = "libcarve"
            cp["file"] = os.path.basename(ctrl.image_path)
            cp["abi"] = ctrl.image_abi
            cp["vaddr"] = f"0x{mres['vaddr']:x}"
            cp["func"] = ctrl.index.func_at(mres["vaddr"]) or cp.get("func", "")
            SessionState.active_libcarve_patches.append(cp)
            lc.refresh_patch_tree()
            return {"ok": True, "status": mres["status"], "vaddr": cp["vaddr"]}
        return {"ok": False, "status": mres.get("status", "fuzzy"), "patch": current_patch}

    def _apply_libforge_favorite_patch(self, current_patch: dict) -> dict:
        """Überträgt eine LibForge-Bibliothek aus den Favoriten in den NativeLibManager."""
        from services.native_lib_service import get_shared_manager

        lname = str(current_patch.get("name") or "").strip()
        if not lname:
            return {"ok": False, "reason": "LibForge-Name fehlt"}
        mgr = get_shared_manager(self.ws.app)
        lib = next((l for l in mgr.get_all() if l.name == lname), None)
        if lib is None:
            lib = mgr.create(name=lname, origin=str(current_patch.get("origin") or "built"))
        lib.version = str(current_patch.get("version") or "1.0")
        lib.description = str(current_patch.get("description") or "")
        if lib.origin != "imported":
            lib.source_code = str(current_patch.get("source_code") or lib.source_code or "")
        lib.abi = str(current_patch.get("abi") or "arm64-v8a")
        try:
            lib.api_level = int(current_patch.get("api_level") or 30)
        except (ValueError, TypeError):
            lib.api_level = 30
        lib.link_libs = str(current_patch.get("link_libs") or "log dl")
        lib.extra_flags = str(current_patch.get("extra_flags") or "")
        lib.output_kind = str(current_patch.get("output_kind") or "shared")
        if current_patch.get("so_relpath"):
            lib.so_relpath = str(current_patch.get("so_relpath"))
        mgr.update(lib)
        if lib.so_relpath and os.path.exists(mgr.abspath(lib.so_relpath)):
            mgr.set_active(lib.id, True)
        lf = getattr(self.ws, "lib_forge", None)
        if lf is not None:
            lf.refresh_list(select_id=lib.id)
        return {"ok": True, "name": lib.name, "active": bool(lib.active)}

    def append_patches_to_selected(self, new_patches: list):
        """Hängt einen oder mehrere Tool-Patches an den ausgewählten Favoriten an."""
        sel = self.view.tree_favs.selection()
        if not sel:
            messagebox.showinfo("Favoriten", "Bitte zuerst einen Ziel-Favoriten in der Liste auswählen.", parent=self.view)
            return False
        if not new_patches:
            return False
        idx = int(sel[0])
        fav = self.fav_service.favs[idx]
        if not isinstance(fav.get("patches"), list):
            base_patch = {k: v for k, v in fav.items() if k in ("file", "orig", "edit", "type", "scope")}
            base_patch.setdefault("type", "smali")
            fav["patches"] = [base_patch] if base_patch.get("edit") or base_patch.get("orig") else []
            for k in ("file", "orig", "edit"):
                fav.pop(k, None)
        patches = fav["patches"]
        for np in new_patches:
            patches.append(dict(np))
        self.fav_service.save_favs()
        self.view.populate_list()
        self.view.tree_favs.selection_set(str(idx))
        self.view.current_sub_patch_idx = max(0, len(patches) - 1)
        self.view.display_sub_patch()
        return True

    def append_from_tool(self, source: str):
        """Liest den aktuellen Patch aus Smali Studio, LibCarve, LibForge oder Workspace und hängt ihn an."""
        new_patches = []
        if source == "smali":
            st = getattr(self.ws, "smali_studio", None)
            ctrl = getattr(st, "controller", None)
            if ctrl is not None and hasattr(ctrl, "build_current_patch_dict"):
                p = ctrl.build_current_patch_dict()
                if p:
                    new_patches.append(p)
        elif source == "libcarve":
            lc = getattr(self.ws, "lib_carve", None)
            ctrl = getattr(lc, "controller", None)
            if ctrl is not None and hasattr(ctrl, "_build_current_patch_dict"):
                p = ctrl._build_current_patch_dict()
                if p:
                    new_patches.append(p)
        elif source == "libforge":
            lf = getattr(self.ws, "lib_forge", None)
            if lf is not None:
                lib = lf.current_lib()
                if lib is None:
                    messagebox.showinfo("LibForge", "Keine Bibliothek in LibForge ausgewählt.", parent=self.view)
                    return
                lf.sync_editor_into(lib)
                new_patches.append(lf.lib_to_patch_dict(lib))
        elif source == "workspace":
            new_patches = self.ws.get_all_patches(include_libforge=True)
        if not new_patches:
            messagebox.showinfo("Nichts zum Anhängen", f"Für '{source}' liegt aktuell kein Patch/Edit vor.", parent=self.view)
            return
        self.append_patches_to_selected(new_patches)

    def combine_selected_favorites(self):
        """Kombiniert >= 2 markierte Favoriten zu einem neuen übergreifenden Multi-Tool-Favoriten."""
        sel = self.view.tree_favs.selection()
        if len(sel) < 2:
            messagebox.showinfo(
                "Favoriten kombinieren",
                "Bitte mindestens 2 Favoriten in der Liste markieren (Strg+Klick / Shift+Klick).",
                parent=self.view,
            )
            return
        indices = sorted({int(s) for s in sel})
        names = [self.fav_service.favs[i].get("name", f"#{i}") for i in indices]
        default_name = "Kombi: " + " + ".join(names[:2]) + (" ..." if len(names) > 2 else "")
        new_name = simpledialog.askstring(
            "Multi-Tool-Favorit erstellen",
            f"{len(indices)} Favoriten zu einem Kombi-Favoriten zusammenführen.\nName:",
            initialvalue=default_name,
            parent=self.view,
        )
        if not new_name or not new_name.strip():
            return
        combined = []
        for i in indices:
            for p in self.get_active_patches(self.fav_service.favs[i]):
                combined.append(dict(p))
        fav = {
            "name": new_name.strip(),
            "version": "1.0",
            "description": f"[v1.0] Kombi aus {len(indices)} Favoriten ({len(combined)} Patches)",
            "patches": combined,
        }
        self.fav_service.add_favorite(fav)
        self.view.populate_list()
        new_idx = len(self.fav_service.favs) - 1
        if str(new_idx) in self.view.tree_favs.get_children():
            self.view.tree_favs.selection_set(str(new_idx))
            self.view.on_select(None)

    def remove_current_sub_patch(self):
        """Entfernt den aktuell angezeigten Sub-Patch aus einem Multi-Patch-Favoriten."""
        sel = self.view.tree_favs.selection()
        if not sel:
            return
        idx = int(sel[0])
        fav = self.fav_service.favs[idx]
        patches = fav.get("patches")
        if not isinstance(patches, list) or len(patches) <= 1:
            messagebox.showinfo("Sub-Patch entfernen", "Ein Favorit muss mindestens 1 Patch behalten.", parent=self.view)
            return
        sub_idx = min(self.view.current_sub_patch_idx, len(patches) - 1)
        if not messagebox.askyesno("Sub-Patch entfernen", f"Sub-Patch {sub_idx + 1} / {len(patches)} aus diesem Favoriten entfernen?", parent=self.view):
            return
        del patches[sub_idx]
        self.fav_service.save_favs()
        self.view.current_sub_patch_idx = max(0, min(sub_idx, len(patches) - 1))
        self.view.populate_list()
        if str(idx) in self.view.tree_favs.get_children():
            self.view.tree_favs.selection_set(str(idx))
        self.view.display_sub_patch()

    def start_batch_fav(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        fav = self.fav_service.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)
        has_smali = any(p.get("type", "smali") in ("smali", "new_file") for p in patches_to_apply)
        studio = self.ws.smali_studio
        if has_smali and (not studio or not studio._ensure_index_loaded(parent_widget=self.view)):
            return

        success_count = 0
        failed_patches = []
        failed_native = []

        for index, current_patch in enumerate(patches_to_apply):
            ptype = current_patch.get("type", "smali")

            if ptype == "lib_replace":
                self.ws.add_lib_row()
                last_row = self.ws.lib_rows[-1]
                last_row["target"].delete(0, tk.END)
                last_row["target"].insert(0, current_patch.get("target", ""))
                last_row["source"].delete(0, tk.END)
                last_row["source"].insert(0, current_patch.get("source", ""))
                success_count += 1
                continue

            elif ptype == "hex":
                self.ws.add_patch_row()
                last_row = self.ws.patch_rows[-1]
                last_row["file"].delete(0, tk.END)
                last_row["file"].insert(0, current_patch.get("file", "libflutter.so"))
                last_row["ram"].delete(0, tk.END)
                last_row["ram"].insert(0, current_patch.get("ram", ""))
                last_row["base"].delete(0, tk.END)
                last_row["base"].insert(0, current_patch.get("base", "00100000"))
                last_row["orig"].delete(0, tk.END)
                last_row["orig"].insert(0, current_patch.get("orig", ""))
                last_row["patch"].delete(0, tk.END)
                last_row["patch"].insert(0, current_patch.get("patch", ""))
                success_count += 1
                continue

            elif ptype == "libcarve":
                lres = self._apply_libcarve_favorite_patch(current_patch)
                if lres.get("ok"):
                    success_count += 1
                else:
                    failed_native.append((index, current_patch))
                continue

            elif ptype == "libforge":
                lfres = self._apply_libforge_favorite_patch(current_patch)
                if lfres.get("ok"):
                    success_count += 1
                else:
                    failed_patches.append((index, current_patch))
                continue

            elif ptype == "new_file":
                if studio.controller.add_new_file_patch(current_patch.get("file", ""), current_patch.get("edit", "")):
                    success_count += 1
                continue

            res = PatchService.evaluate_smali_patch(current_patch, studio.search_engine.ram_cache, studio.smali_patches)

            if res["success"]:
                if res["type"] in ["exact", "structural"]:
                    cp = current_patch.copy()
                    cp["file"] = res["file"]
                    cp["orig"] = res["orig"]
                    studio.smali_patches.append(cp)
                    studio.app.log(f"[+] Sub-Patch {index + 1} ({res['file']}) {res['type']} angewendet.")
                success_count += 1
            else:
                failed_patches.append((index, current_patch))

        if success_count > 0:
            if studio:
                studio.refresh_smali_tree()
            self.ws.app.persist_active_session()  # T1

        if not failed_patches and not failed_native:
            messagebox.showinfo("Batch Abgeschlossen", f"Alle {success_count} Patches erfolgreich angewendet!", parent=self.view)
        else:
            total_fail = len(failed_patches) + len(failed_native)
            messagebox.showwarning("Batch Konflikte", f"{success_count} Patches angewendet.\n{total_fail} Konflikte.", parent=self.view)
            for offset, (idx, fp) in enumerate(failed_patches):
                fuzzer = FuzzyMatchDialog(self.view, studio.app, studio, fp, title_suffix=f" (Patch {idx + 1}/{len(patches_to_apply)})")
                fuzzer.geometry(f"1300x750+{50 + offset * 30}+{50 + offset * 30}")
            for idx, fp in failed_native:
                self.ws.lib_carve.controller.open_native_fuzzy_matcher_for_patch(fp)

    def start_single_fav(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        fav = self.fav_service.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)
        current_patch = patches_to_apply[self.view.current_sub_patch_idx]
        ptype = current_patch.get("type", "smali")

        if ptype == "libcarve":
            lres = self._apply_libcarve_favorite_patch(current_patch)
            if lres.get("ok"):
                self.ws.app.persist_active_session()
                messagebox.showinfo("Erfolg", f"LibCarve-Patch gematcht & geladen ({lres.get('vaddr')})!", parent=self.view)
            else:
                self.ws.lib_carve.controller.open_native_fuzzy_matcher_for_patch(current_patch)
            return

        if ptype == "libforge":
            lfres = self._apply_libforge_favorite_patch(current_patch)
            if lfres.get("ok"):
                messagebox.showinfo("Erfolg", f"LibForge-Bibliothek '{lfres.get('name')}' geladen!", parent=self.view)
            else:
                messagebox.showerror("Fehler", lfres.get("reason", "LibForge-Patch fehlgeschlagen"), parent=self.view)
            return

        studio = self.ws.smali_studio
        if ptype in ("smali", "new_file") and (not studio or not studio._ensure_index_loaded(parent_widget=self.view)): return

        if ptype == "lib_replace":
            self.ws.add_lib_row()
            last_row = self.ws.lib_rows[-1]
            last_row["target"].delete(0, tk.END)
            last_row["target"].insert(0, current_patch.get("target", ""))
            last_row["source"].delete(0, tk.END)
            last_row["source"].insert(0, current_patch.get("source", ""))
            messagebox.showinfo("Erfolg", "Lib-Replacement erfolgreich geladen!", parent=self.view)
            return

        elif ptype == "hex":
            self.ws.add_patch_row()
            last_row = self.ws.patch_rows[-1]
            last_row["file"].delete(0, tk.END)
            last_row["file"].insert(0, current_patch.get("file", "libflutter.so"))
            last_row["ram"].delete(0, tk.END)
            last_row["ram"].insert(0, current_patch.get("ram", ""))
            last_row["base"].delete(0, tk.END)
            last_row["base"].insert(0, current_patch.get("base", "00100000"))
            last_row["orig"].delete(0, tk.END)
            last_row["orig"].insert(0, current_patch.get("orig", ""))
            last_row["patch"].delete(0, tk.END)
            last_row["patch"].insert(0, current_patch.get("patch", ""))
            messagebox.showinfo("Erfolg", "Hex-Patch erfolgreich geladen!", parent=self.view)
            return

        elif ptype == "new_file":
            if studio.controller.add_new_file_patch(current_patch.get("file", ""), current_patch.get("edit", "")):
                messagebox.showinfo("Erfolg", "Neue-Datei-Patch als aktiver Patch hinzugefuegt!", parent=self.view)
            return

        res = PatchService.evaluate_smali_patch(current_patch, studio.search_engine.ram_cache, studio.smali_patches)

        if res["success"]:
            if res["type"] in ["exact", "structural"]:
                cp = current_patch.copy()
                cp["file"] = res["file"]
                cp["orig"] = res["orig"]
                studio.smali_patches.append(cp)
                studio.app.log(f"[+] Sub-Patch {self.view.current_sub_patch_idx + 1} {res['type']} angewendet.")
                studio.refresh_smali_tree()
                messagebox.showinfo("Erfolg", "Patch erfolgreich hinzugefügt!", parent=self.view)
        else:
            FuzzyMatchDialog(self.view, studio.app, studio, current_patch, title_suffix=f" (Patch {self.view.current_sub_patch_idx + 1}/{len(patches_to_apply)})")