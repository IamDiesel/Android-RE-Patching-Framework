import tkinter as tk
from tkinter import messagebox
import re
import difflib
import threading
from core.fuzzing_engine import FuzzingEngine


class FuzzyMatchController:
    def __init__(self, view, app, smali_studio, fav_patch):
        self.view = view
        self.app = app
        self.smali_studio = smali_studio
        self.fav_patch = fav_patch.copy()
        if "file" in self.fav_patch:
            self.fav_patch["file"] = self.fav_patch["file"].replace("\\", "/")

        self.cancel_search = False

    def cancel(self):
        self.cancel_search = True

    def run_fuzzing(self, deep_search=False):
        self.cancel_search = False
        self.view.show_loading_state(True)
        self.view.update_status("Fuzzing läuft... Bitte warten.")

        def task():
            orig_code = self.fav_patch.get("orig", "")
            target_file = self.fav_patch.get("file", "")
            m = re.search(r'\.method.*? ([<a-zA-Z0-9_$\-]+)\(', orig_code)

            if m and not deep_search:
                method_name = m.group(1)
                self.app.after(0, lambda: self.view.update_status(f"Suche nach Signatur: {method_name}() ..."))
                candidates = FuzzingEngine.fuzz_by_method_name(method_name, self.smali_studio.search_engine.ram_cache,
                                                               cancel_hook=lambda: self.cancel_search)
            else:
                msg = "Deep Search im gesamten RAM (Pre-Filtered)..." if deep_search else f"Suche Heuristik in {target_file}..."
                self.app.after(0, lambda: self.view.update_status(msg))
                candidates = FuzzingEngine.fuzz_by_content_snippet(
                    orig_code, target_file, self.smali_studio.search_engine.ram_cache, deep_search=deep_search,
                    cancel_hook=lambda: self.cancel_search
                )

            if self.cancel_search:
                self.app.after(0, lambda: self.view.update_status("Suche vom Nutzer abgebrochen."))
                self.app.after(0, lambda: self.view.show_loading_state(False))
                return

            for cand in candidates:
                cand["file"] = cand["file"].replace("\\", "/")

            def update_ui():
                self.view.candidates = candidates
                self.view.apply_filter()

                if self.view.candidates:
                    self.view.update_status(f"{len(self.view.candidates)} Kandidaten gefunden.")
                else:
                    if not deep_search:
                        self.view.update_status(
                            "Schnelle Suche erfolglos. Klicke auf 'Deep Search' oder suche manuell!")
                        self.view.show_deep_search_button()
                    else:
                        self.view.update_status("Auch Deep Search erfolglos. Nutze die manuelle Suche!")
                        self.view.update_actual_code("Keine Kandidaten gefunden.")
                self.view.show_loading_state(False)

            self.app.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def run_manual_search(self, term):
        if not term: return
        self.view.update_status(f"Durchsuche RAM nach '{term}'...")
        self.view.update_idletasks()

        search_results = []
        terms = term.lower().split()

        for path, content in self.smali_studio.search_engine.ram_cache:
            norm_path = path.replace("\\", "/")
            path_lower = norm_path.lower()
            content_lower = content.lower()

            if not all((t in content_lower or t in path_lower) for t in terms):
                continue

            lines = content.split("\n")
            in_method = False
            cur_block = []
            cur_sig = ""

            for line in lines:
                if line.startswith(".method "):
                    in_method = True
                    cur_block = [line]
                    cur_sig = line.strip().replace(".method ", "")
                elif in_method:
                    cur_block.append(line)
                    if line.startswith(".end method"):
                        in_method = False
                        method_code_lower = "\n".join(cur_block).lower()

                        is_valid = True
                        for t in terms:
                            if t not in method_code_lower and t not in path_lower:
                                is_valid = False
                                break

                        if is_valid:
                            search_results.append({
                                "file": norm_path,
                                "sig": cur_sig,
                                "code": "\n".join(cur_block)
                            })

        self.view.search_results = search_results
        self.view.render_search_results()
        self.view.update_status(f"Manuelle Suche: {len(search_results)} Methoden gefunden.")

    def calculate_diff(self, str_left, str_right):
        """Asynchrone Diff-Berechnung, lagert Algorithmus aus der View aus."""

        def diff_worker():
            matcher = difflib.SequenceMatcher(None, str_left, str_right)
            opcodes = matcher.get_opcodes()
            self.app.after(0, lambda: self.view._apply_diff_tags(opcodes))

        threading.Thread(target=diff_worker, daemon=True).start()

    def apply_patch_directly(self):
        cand = self.view.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self.view)

        new_patch = {
            "type": "smali",
            "file": cand["file"],
            "orig": cand["code"],
            "edit": self.view.txt_edit.get("1.0", tk.END).strip()
        }

        for p in self.smali_studio.smali_patches:
            if p["file"].replace("\\", "/") == new_patch["file"] and p["orig"] == new_patch["orig"]:
                return messagebox.showinfo("Duplikat", "Dieser Patch ist bereits in der Liste aktiv.", parent=self.view)

        self.smali_studio.smali_patches.append(new_patch)
        self.smali_studio.refresh_smali_tree()
        self.app.log(f"[+] Patch direkt aus Konflikt-Löser angewendet: {new_patch['file']}")
        self.view.destroy()

    def apply_and_update_fav(self):
        cand = self.view.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self.view)

        self.apply_patch_directly()
        self.fav_patch["file"] = cand["file"]
        self.fav_patch["orig"] = cand["code"]
        self.fav_patch["edit"] = self.view.txt_edit.get("1.0", tk.END).strip()

        if hasattr(self.view.master, "controller") and hasattr(self.view.master.controller, "save_current"):
            self.view.master.controller.save_current()
            if hasattr(self.view.master, "display_sub_patch"):
                self.view.master.display_sub_patch()

    def load_candidate_to_ide(self):
        cand = self.view.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self.view)

        self.smali_studio.controller.load_method(cand["file"], method_signature=cand["sig"])
        self.smali_studio.editor.txt_edit.delete("1.0", tk.END)
        self.smali_studio.editor.txt_edit.insert("1.0", self.view.txt_edit.get("1.0", tk.END).strip())
        if hasattr(self.smali_studio.editor, "rehighlight"):
            self.smali_studio.editor.rehighlight()
        self.view.destroy()

    def force_load_to_ide(self):
        target_file = self.fav_patch.get("file", "Unbekannt").replace("\\", "/")
        file_exists_in_ram = any(
            path.replace("\\", "/") == target_file for path, _ in self.smali_studio.search_engine.ram_cache)

        if file_exists_in_ram:
            self.smali_studio.controller.load_method(target_file)
            messagebox.showinfo("Erzwungen",
                                f"Die Datei {target_file} wurde geladen.\nBitte suche die Zielmethode in der Outline manuell.",
                                parent=self.view)
        else:
            if messagebox.askyesno("Datei fehlt",
                                   f"Die Zieldatei '{target_file}' existiert nicht im RAM.\nMöchtest du sie als neue Eigene Struktur anlegen?",
                                   parent=self.view):
                self.smali_studio.controller.open_create_struct_dialog()
                self.view.destroy()
                return
            else:
                self.smali_studio.controller.current_smali_file = target_file
                self.smali_studio.controller.current_method_name = "<Erzwungener Favorit>"
                self.smali_studio.lbl_smali_file.config(text=f"Erzwungen: {target_file}")
                self.smali_studio.editor.txt_orig.config(state="normal")
                self.smali_studio.editor.load_code("")

        self.smali_studio.editor.txt_orig.config(state="normal")
        self.smali_studio.editor.txt_orig.delete("1.0", tk.END)
        self.smali_studio.editor.txt_orig.insert("1.0", self.view.txt_expected.get("1.0", tk.END).strip())
        self.smali_studio.editor.txt_orig.config(state="disabled")

        self.smali_studio.editor.txt_edit.delete("1.0", tk.END)
        self.smali_studio.editor.txt_edit.insert("1.0", self.view.txt_edit.get("1.0", tk.END).strip())
        self.view.destroy()