import tkinter as tk
from tkinter import messagebox
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
        if messagebox.askyesno("Löschen", "Favorit komplett löschen?", parent=self.view):
            self.fav_service.delete_favorite(int(sel[0]))
            self.view.populate_list()
            self.view.ent_name.delete(0, tk.END)
            self.view.ent_file.delete(0, tk.END)
            self.view.txt_orig.delete("1.0", tk.END)
            self.view.txt_edit.delete("1.0", tk.END)

    def start_batch_fav(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        fav = self.fav_service.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)
        studio = self.ws.smali_studio
        if not studio or not studio._ensure_index_loaded(): return

        success_count = 0
        failed_patches = []

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

        if success_count > 0: studio.refresh_smali_tree()

        if not failed_patches:
            messagebox.showinfo("Batch Abgeschlossen", f"Alle {success_count} Patches erfolgreich angewendet!", parent=self.view)
        else:
            messagebox.showwarning("Batch Konflikte", f"{success_count} Patches angewendet.\n{len(failed_patches)} Konflikte.", parent=self.view)
            for offset, (idx, fp) in enumerate(failed_patches):
                fuzzer = FuzzyMatchDialog(self.view, studio.app, studio, fp, title_suffix=f" (Patch {idx + 1}/{len(patches_to_apply)})")
                fuzzer.geometry(f"1300x750+{50 + offset * 30}+{50 + offset * 30}")

    def start_single_fav(self):
        sel = self.view.tree_favs.selection()
        if not sel: return
        fav = self.fav_service.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)
        studio = self.ws.smali_studio
        if not studio or not studio._ensure_index_loaded(): return

        current_patch = patches_to_apply[self.view.current_sub_patch_idx]
        ptype = current_patch.get("type", "smali")

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