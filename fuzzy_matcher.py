import tkinter as tk
from tkinter import ttk, messagebox
import re
import difflib


class FuzzyMatchDialog(tk.Toplevel):
    def __init__(self, parent, app, smali_studio, fav_patch, title_suffix=""):
        super().__init__(parent)
        self.app = app
        self.smali_studio = smali_studio
        self.fav_patch = fav_patch

        self.title(f"⚠️ Patch Konflikt & Fuzzy Matcher{title_suffix}")
        self.geometry("1300x750")
        self.transient(parent)
        self.attributes("-topmost", True)

        self.candidates = []
        self.search_results = []
        self._debounce_timer = None

        self.create_widgets()
        self.run_fuzzing()

    def create_widgets(self):
        f_top = ttk.Frame(self)
        f_top.pack(side="top", fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Der Original-Block des Favoriten wurde in der Zieldatei nicht exakt gefunden.",
                  font=("Segoe UI", 10, "bold"), foreground="#D16969").pack(anchor="w")

        target_path = self.fav_patch.get("file", "Unbekannt")
        ttk.Label(f_top, text=f"Erwarteter Originalpfad: {target_path}", font=("Segoe UI", 9, "italic"),
                  foreground="#569CD6").pack(anchor="w", pady=(2, 8))

        self.lbl_status = ttk.Label(f_top, text="Starte Fuzzing-Engine...")
        self.lbl_status.pack(anchor="w")

        f_bot = ttk.Frame(self)
        f_bot.pack(side="bottom", fill="x", padx=10, pady=10)

        ttk.Button(f_bot, text="✅ Direkt als Patch übernehmen", command=self.apply_patch_directly).pack(side="left",
                                                                                                        padx=5)
        ttk.Button(f_bot, text="✅ Übernehmen & Favorit reparieren", command=self.apply_and_update_fav).pack(side="left",
                                                                                                            padx=5)
        ttk.Separator(f_bot, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(f_bot, text="✏️ Nur in IDE laden", command=self.load_candidate_to_ide).pack(side="left", padx=5)
        ttk.Button(f_bot, text="⚠️ Als Neue Struktur erzwingen", command=self.force_load_to_ide).pack(side="left",
                                                                                                      padx=5)
        ttk.Button(f_bot, text="❌ Schließen", command=self.destroy).pack(side="right", padx=5)

        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=10, pady=5)

        self.left_nb = ttk.Notebook(main_paned)
        main_paned.add(self.left_nb, weight=2)

        f_fuzz = ttk.Frame(self.left_nb)
        self.left_nb.add(f_fuzz, text="Fuzzing Resultate")

        f_filter = ttk.Frame(f_fuzz)
        f_filter.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_filter, text="Ergebnisse Filtern:").pack(side="left", padx=2)
        self.ent_filter = ttk.Entry(f_filter)
        self.ent_filter.pack(side="left", fill="x", expand=True, padx=2)
        self.ent_filter.bind("<KeyRelease>", self.apply_filter)

        tree_frame_cands = ttk.Frame(f_fuzz)
        tree_frame_cands.pack(fill="both", expand=True)
        self.tree_cands = ttk.Treeview(tree_frame_cands, columns=("File", "Method"), show="headings")
        self.tree_cands.heading("File", text="Datei")
        self.tree_cands.heading("Method", text="Signatur")
        self.tree_cands.column("File", width=250)
        scroll_cands = ttk.Scrollbar(tree_frame_cands, orient="vertical", command=self.tree_cands.yview)
        self.tree_cands.configure(yscrollcommand=scroll_cands.set)
        scroll_cands.pack(side="right", fill="y")
        self.tree_cands.pack(side="left", fill="both", expand=True)
        self.tree_cands.bind("<Double-1>", self.on_candidate_select)

        f_man = ttk.Frame(self.left_nb)
        self.left_nb.add(f_man, text="Manuelle RAM Suche")

        f_search = ttk.Frame(f_man)
        f_search.pack(fill="x", padx=5, pady=5)
        self.ent_search = ttk.Entry(f_search)
        self.ent_search.pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(f_search, text="Suchen", command=self.run_manual_search).pack(side="left", padx=2)
        self.ent_search.bind("<Return>", lambda e: self.run_manual_search())

        tree_frame_search = ttk.Frame(f_man)
        tree_frame_search.pack(fill="both", expand=True)
        self.tree_search = ttk.Treeview(tree_frame_search, columns=("File", "Method"), show="headings")
        self.tree_search.heading("File", text="Datei")
        self.tree_search.heading("Method", text="Signatur")
        self.tree_search.column("File", width=250)
        scroll_search = ttk.Scrollbar(tree_frame_search, orient="vertical", command=self.tree_search.yview)
        self.tree_search.configure(yscrollcommand=scroll_search.set)
        scroll_search.pack(side="right", fill="y")
        self.tree_search.pack(side="left", fill="both", expand=True)
        self.tree_search.bind("<Double-1>", self.on_search_select)

        # --- RECHTES PANEL (Didaktisch überarbeitet + Cursor Fix) ---
        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=3)

        f_expected = ttk.LabelFrame(right_paned,
                                    text="🔍 1. ALTES ORIGINAL (Code aus deinem Favoriten, der gesucht wurde)")
        right_paned.add(f_expected, weight=1)
        # insertbackground="white" hinzugefügt!
        self.txt_expected = tk.Text(f_expected, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10),
                                    insertbackground="white")
        self.txt_expected.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_expected.insert("1.0", self.fav_patch.get("orig", ""))
        self.txt_expected.config(state="disabled")

        f_actual = ttk.LabelFrame(right_paned,
                                  text="🎯 2. NEUER KANDIDAT (Vergleich mit 1 -> 🔴 Gelöscht | 🟢 Neu im Kandidat)")
        right_paned.add(f_actual, weight=1)
        self.txt_actual = tk.Text(f_actual, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), insertbackground="white")
        self.txt_actual.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_actual.config(state="disabled")

        f_edit = ttk.LabelFrame(right_paned,
                                text="✏️ 3. DEIN PATCH (Hier anpassen, damit er zur Struktur von 2. passt!)")
        right_paned.add(f_edit, weight=1)
        # insertbackground="white" hinzugefügt -> Cursor wird sichtbar!
        self.txt_edit = tk.Text(f_edit, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), insertbackground="white")
        self.txt_edit.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_edit.insert("1.0", self.fav_patch.get("edit", ""))

        self.txt_edit.bind("<KeyRelease>", self._on_text_change)
        self._refresh_visuals()

    # --- SYNTAX HIGHLIGHTING & DIFF ENGINE ---

    def _on_text_change(self, event=None):
        if self._debounce_timer:
            self.after_cancel(self._debounce_timer)
        self._debounce_timer = self.after(300, self._refresh_visuals)

    def _refresh_visuals(self):
        self._apply_diff(self.txt_expected, self.txt_actual)
        self._apply_smali_highlighting(self.txt_expected)
        self._apply_smali_highlighting(self.txt_actual)
        self._apply_smali_highlighting(self.txt_edit)

    def _apply_diff(self, txt_left, txt_right):
        txt_left.tag_configure("diff_del", background="#4a1919")
        txt_right.tag_configure("diff_add", background="#1a3b1a")

        txt_left.tag_remove("diff_del", "1.0", tk.END)
        txt_right.tag_remove("diff_add", "1.0", tk.END)

        str_left = txt_left.get("1.0", tk.END).splitlines()
        str_right = txt_right.get("1.0", tk.END).splitlines()

        matcher = difflib.SequenceMatcher(None, str_left, str_right)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ('replace', 'delete'):
                for i in range(i1, i2):
                    txt_left.tag_add("diff_del", f"{i + 1}.0", f"{i + 1}.end")
            if tag in ('replace', 'insert'):
                for j in range(j1, j2):
                    txt_right.tag_add("diff_add", f"{j + 1}.0", f"{j + 1}.end")

    def _apply_smali_highlighting(self, txt_widget):
        txt_widget.tag_configure("s_key", foreground="#569CD6")
        txt_widget.tag_configure("s_inst", foreground="#C586C0")
        txt_widget.tag_configure("s_str", foreground="#CE9178")
        txt_widget.tag_configure("s_com", foreground="#6A9955")
        txt_widget.tag_configure("s_reg", foreground="#9CDCFE")

        for t in ["s_key", "s_inst", "s_str", "s_com", "s_reg"]:
            txt_widget.tag_remove(t, "1.0", tk.END)

        text = txt_widget.get("1.0", tk.END)
        for line_idx, line in enumerate(text.split('\n')):
            tk_line = line_idx + 1

            c_match = re.search(r'#.*', line)
            if c_match:
                txt_widget.tag_add("s_com", f"{tk_line}.{c_match.start()}", f"{tk_line}.{c_match.end()}")
                line = line[:c_match.start()]

            for m in re.finditer(r'".*?"', line):
                txt_widget.tag_add("s_str", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            for m in re.finditer(r'\b[vp]\d+\b', line):
                txt_widget.tag_add("s_reg", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            for m in re.finditer(r'(\.[a-zA-Z0-9_-]+)', line):
                txt_widget.tag_add("s_key", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            m = re.search(r'^\s*([a-zA-Z0-9_-]+)', line)
            if m and not m.group(1).startswith('.'):
                txt_widget.tag_add("s_inst", f"{tk_line}.{m.start(1)}", f"{tk_line}.{m.end(1)}")

    # --- FUZZING LOGIK ---

    def run_fuzzing(self):
        orig_code = self.fav_patch.get("orig", "")
        target_file = self.fav_patch.get("file", "")

        m = re.search(r'\.method.*? ([<a-zA-Z0-9_$\-]+)\(', orig_code)

        if m:
            method_name = m.group(1)
            self.lbl_status.config(text=f"Strategie 1: Suche im RAM nach Signatur: {method_name}() ...")
            self._fuzz_by_method_name(method_name)
        else:
            self.lbl_status.config(text="Strategie 2: Keine Signatur im Patch. Suche nach Inhalts-Schnipseln...")
            self._fuzz_by_content_snippet(orig_code, target_file)

        self.apply_filter()

        if self.candidates:
            self.lbl_status.config(text=f"{len(self.candidates)} mögliche Kandidaten gefunden.")
        else:
            self.lbl_status.config(
                text="Fuzzing erfolglos. Kein passender Kandidat gefunden. Nutze die manuelle Suche!")
            self.txt_actual.config(state="normal")
            self.txt_actual.insert("1.0", "Keine Kandidaten gefunden.")
            self.txt_actual.config(state="disabled")
            self._refresh_visuals()

    def _fuzz_by_method_name(self, method_name):
        safe_method_name = re.escape(method_name)
        pattern = re.compile(r'^\s*\.method\s+.*?\s+' + safe_method_name + r'\(')
        for path, content in self.smali_studio.search_engine.ram_cache:
            if method_name in content:
                self._extract_methods_from_content(path, content, pattern, self.candidates)

    def _fuzz_by_content_snippet(self, orig_code, target_file):
        target_content = None
        for path, content in self.smali_studio.search_engine.ram_cache:
            if path == target_file:
                target_content = content
                break

        if not target_content: return

        lines = [line.strip() for line in orig_code.split('\n') if
                 line.strip() and not line.strip().startswith('#') and not line.strip().startswith('.line')]
        if not lines: return

        longest_line = max(lines, key=len)
        current_sig = ""
        in_method = False
        current_block = []
        found_match_in_block = False

        for line in target_content.split('\n'):
            if line.startswith(".method "):
                in_method = True
                current_sig = line.strip().replace(".method ", "")
                current_block = [line]
                found_match_in_block = False
            elif in_method:
                current_block.append(line)
                if longest_line in line:
                    found_match_in_block = True
                if line.startswith(".end method"):
                    in_method = False
                    if found_match_in_block:
                        self.candidates.append({
                            "file": target_file,
                            "sig": current_sig,
                            "code": "\n".join(current_block)
                        })

    def _extract_methods_from_content(self, path, content, pattern, target_list):
        lines = content.split('\n')
        in_method = False
        current_block = []
        current_sig = ""

        for line in lines:
            if line.startswith(".method ") and pattern.search(line):
                in_method = True
                current_sig = line.strip().replace(".method ", "")
                current_block = [line]
            elif in_method:
                current_block.append(line)
                if line.startswith(".end method"):
                    in_method = False
                    target_list.append({
                        "file": path,
                        "sig": current_sig,
                        "code": "\n".join(current_block)
                    })

    def apply_filter(self, event=None):
        term = self.ent_filter.get().lower()
        for i in self.tree_cands.get_children():
            self.tree_cands.delete(i)

        for idx, cand in enumerate(self.candidates):
            if term in cand["file"].lower() or term in cand["sig"].lower() or term in cand["code"].lower():
                self.tree_cands.insert("", "end", iid=f"fuzz_{idx}", values=(cand["file"], cand["sig"]))

    def run_manual_search(self):
        term = self.ent_search.get().lower()
        if not term: return

        self.lbl_status.config(text=f"Durchsuche RAM nach '{term}'...")
        self.update()

        self.search_results = []
        for i in self.tree_search.get_children():
            self.tree_search.delete(i)

        for path, content in self.smali_studio.search_engine.ram_cache:
            if term in content.lower() or term in path.lower():
                lines = content.split("\n")
                in_method = False
                cur_block = []
                cur_sig = ""
                match_in_method = False
                for line in lines:
                    if line.startswith(".method "):
                        in_method = True
                        cur_block = [line]
                        cur_sig = line.strip().replace(".method ", "")
                        match_in_method = (term in line.lower())
                    elif in_method:
                        cur_block.append(line)
                        if term in line.lower():
                            match_in_method = True
                        if line.startswith(".end method"):
                            in_method = False
                            if match_in_method or term in path.lower():
                                self.search_results.append({
                                    "file": path,
                                    "sig": cur_sig,
                                    "code": "\n".join(cur_block)
                                })

        for idx, res in enumerate(self.search_results):
            self.tree_search.insert("", "end", iid=f"srch_{idx}", values=(res["file"], res["sig"]))

        self.lbl_status.config(text=f"Manuelle Suche: {len(self.search_results)} Methoden gefunden.")

    def get_selected_candidate(self):
        current_tab = self.left_nb.index(self.left_nb.select())
        if current_tab == 0:
            sel = self.tree_cands.selection()
            if not sel: return None
            idx = int(sel[0].replace("fuzz_", ""))
            return self.candidates[idx]
        else:
            sel = self.tree_search.selection()
            if not sel: return None
            idx = int(sel[0].replace("srch_", ""))
            return self.search_results[idx]

    def on_candidate_select(self, event):
        cand = self.get_selected_candidate()
        if cand:
            self._update_actual_code(cand["code"])

    def on_search_select(self, event):
        cand = self.get_selected_candidate()
        if cand:
            self._update_actual_code(cand["code"])

    def _update_actual_code(self, code):
        self.txt_actual.config(state="normal")
        self.txt_actual.delete("1.0", tk.END)
        self.txt_actual.insert("1.0", code)
        self.txt_actual.config(state="disabled")
        self._refresh_visuals()

    def apply_patch_directly(self):
        cand = self.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self)

        new_patch = {
            "type": "smali",
            "file": cand["file"],
            "orig": cand["code"],
            "edit": self.txt_edit.get("1.0", tk.END).strip()
        }

        for p in self.smali_studio.smali_patches:
            if p["file"] == new_patch["file"] and p["orig"] == new_patch["orig"]:
                return messagebox.showinfo("Duplikat", "Dieser Patch ist bereits in der Liste aktiv.", parent=self)

        self.smali_studio.smali_patches.append(new_patch)
        self.smali_studio.refresh_smali_tree()
        self.app.log(f"[+] Patch direkt aus Konflikt-Löser angewendet: {new_patch['file']}")
        self.destroy()

    def apply_and_update_fav(self):
        cand = self.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self)

        self.apply_patch_directly()

        self.fav_patch["file"] = cand["file"]
        self.fav_patch["orig"] = cand["code"]
        self.fav_patch["edit"] = self.txt_edit.get("1.0", tk.END).strip()

        if hasattr(self.master, "save_favs"):
            self.master.save_favs()
            if hasattr(self.master, "display_sub_patch"):
                self.master.display_sub_patch()

    def load_candidate_to_ide(self):
        cand = self.get_selected_candidate()
        if not cand:
            return messagebox.showwarning("Fehlt", "Bitte wähle links einen Kandidaten aus.", parent=self)

        self.smali_studio.load_method(cand["file"], method_signature=cand["sig"])

        self.smali_studio.editor.txt_edit.delete("1.0", tk.END)
        self.smali_studio.editor.txt_edit.insert("1.0", self.txt_edit.get("1.0", tk.END).strip())
        if hasattr(self.smali_studio.editor, "rehighlight"):
            self.smali_studio.editor.rehighlight()

        self.destroy()

    def force_load_to_ide(self):
        target_file = self.fav_patch.get("file", "")
        file_exists_in_ram = any(path == target_file for path, _ in self.smali_studio.search_engine.ram_cache)

        if file_exists_in_ram:
            self.smali_studio.load_method(target_file)
            messagebox.showinfo("Erzwungen",
                                f"Die Datei {target_file} wurde geladen.\nBitte suche die Zielmethode in der Outline manuell.",
                                parent=self)
        else:
            if messagebox.askyesno("Datei fehlt",
                                   f"Die Zieldatei '{target_file}' existiert nicht im RAM.\nMöchtest du sie als neue Eigene Struktur anlegen?",
                                   parent=self):
                self.smali_studio.open_create_struct_dialog()
                self.destroy()
                return
            else:
                self.smali_studio.current_smali_file = target_file
                self.smali_studio.current_method_name = "<Erzwungener Favorit>"
                self.smali_studio.lbl_smali_file.config(text=f"Erzwungen: {target_file}")
                self.smali_studio.editor.txt_orig.config(state="normal")
                self.smali_studio.editor.load_code("")

        self.smali_studio.editor.txt_orig.config(state="normal")
        self.smali_studio.editor.txt_orig.delete("1.0", tk.END)
        self.smali_studio.editor.txt_orig.insert("1.0", self.txt_expected.get("1.0", tk.END).strip())
        self.smali_studio.editor.txt_orig.config(state="disabled")

        self.smali_studio.editor.txt_edit.delete("1.0", tk.END)
        self.smali_studio.editor.txt_edit.insert("1.0", self.txt_edit.get("1.0", tk.END).strip())

        self.destroy()