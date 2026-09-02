import tkinter as tk
from tkinter import ttk
import re

from ui.controllers.fuzzy_match_controller import FuzzyMatchController


class FuzzyMatchDialog(tk.Toplevel):
    def __init__(self, parent, app, smali_studio, fav_patch, title_suffix=""):
        super().__init__(parent)
        self.app = app
        self.smali_studio = smali_studio

        self.title(f"⚠️ Patch Konflikt & Fuzzy Matcher{title_suffix}")
        self.geometry("1300x750")
        self.transient(parent)
        self.attributes("-topmost", True)

        self.candidates = []
        self.search_results = []
        self._debounce_timer = None

        self.controller = FuzzyMatchController(self, app, smali_studio, fav_patch)

        self.create_widgets()

        # UI Setup
        self.txt_expected.insert("1.0", fav_patch.get("orig", ""))
        self.txt_expected.config(state="disabled")
        self.txt_edit.insert("1.0", fav_patch.get("edit", ""))
        self._refresh_visuals()

        self.controller.run_fuzzing(deep_search=False)

    def create_widgets(self):
        f_top = ttk.Frame(self)
        f_top.pack(side="top", fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Der Original-Block des Favoriten wurde in der Zieldatei nicht exakt gefunden.",
                  font=("Segoe UI", 10, "bold"), foreground="#D16969").pack(anchor="w")

        # Pfad als kopierbares, aber nicht bearbeitbares Entry (Read-Only)
        path_frame = ttk.Frame(f_top)
        path_frame.pack(anchor="w", pady=(2, 8), fill="x")
        ttk.Label(path_frame, text="Erwarteter Originalpfad:", font=("Segoe UI", 9, "italic"),
                  foreground="#569CD6").pack(side="left")

        self.ent_target_path = ttk.Entry(path_frame, font=("Segoe UI", 9, "italic"))
        self.ent_target_path.insert(0, self.controller.fav_patch.get("file", "Unbekannt"))
        self.ent_target_path.config(state="readonly")
        self.ent_target_path.pack(side="left", fill="x", expand=True, padx=5)

        # Status & Control Buttons
        status_frame = ttk.Frame(f_top)
        status_frame.pack(fill="x", pady=2)

        self.lbl_status = ttk.Label(status_frame, text="Starte Fuzzing-Engine...")
        self.lbl_status.pack(side="left")

        self.btn_deep_search = ttk.Button(status_frame, text="🔍 Deep Search (Gesamter RAM)",
                                          command=lambda: self.controller.run_fuzzing(deep_search=True))

        self.btn_cancel = ttk.Button(status_frame, text="🛑 Abbrechen", command=self.controller.cancel)

        # Footer Buttons
        f_bot = ttk.Frame(self)
        f_bot.pack(side="bottom", fill="x", padx=10, pady=10)

        ttk.Button(f_bot, text="✅ Direkt als Patch übernehmen", command=self.controller.apply_patch_directly).pack(
            side="left", padx=5)
        ttk.Button(f_bot, text="✅ Übernehmen & Favorit reparieren", command=self.controller.apply_and_update_fav).pack(
            side="left", padx=5)
        ttk.Separator(f_bot, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(f_bot, text="✏️ Nur in IDE laden", command=self.controller.load_candidate_to_ide).pack(side="left",
                                                                                                          padx=5)
        ttk.Button(f_bot, text="⚠️ Als Neue Struktur erzwingen", command=self.controller.force_load_to_ide).pack(
            side="left", padx=5)
        ttk.Button(f_bot, text="❌ Schließen", command=self.destroy).pack(side="right", padx=5)

        # Layout Main
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
        ttk.Button(f_search, text="Suchen",
                   command=lambda: self.controller.run_manual_search(self.ent_search.get().strip())).pack(side="left",
                                                                                                          padx=2)
        self.ent_search.bind("<Return>", lambda e: self.controller.run_manual_search(self.ent_search.get().strip()))

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

        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=3)

        f_expected = ttk.LabelFrame(right_paned, text="🔍 1. ALTES ORIGINAL")
        right_paned.add(f_expected, weight=1)
        self.txt_expected = tk.Text(f_expected, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10),
                                    insertbackground="white")
        self.txt_expected.pack(fill="both", expand=True, padx=2, pady=2)

        f_actual = ttk.LabelFrame(right_paned, text="🎯 2. NEUER KANDIDAT (Vergleich)")
        right_paned.add(f_actual, weight=1)
        self.txt_actual = tk.Text(f_actual, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), insertbackground="white")
        self.txt_actual.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_actual.config(state="disabled")

        f_edit = ttk.LabelFrame(right_paned, text="✏️ 3. DEIN PATCH (Hier anpassen)")
        right_paned.add(f_edit, weight=1)
        self.txt_edit = tk.Text(f_edit, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), insertbackground="white")
        self.txt_edit.pack(fill="both", expand=True, padx=2, pady=2)

        self.txt_edit.bind("<KeyRelease>", self._on_text_change)

    def show_deep_search_button(self):
        self.btn_deep_search.pack(side="left", padx=10)

    def show_loading_state(self, is_loading):
        if is_loading:
            self.btn_cancel.pack(side="left", padx=10)
            self.btn_deep_search.pack_forget()
        else:
            self.btn_cancel.pack_forget()

    def update_status(self, text):
        self.lbl_status.config(text=text)

    def apply_filter(self, event=None):
        term = self.ent_filter.get().lower()
        for i in self.tree_cands.get_children(): self.tree_cands.delete(i)
        for idx, cand in enumerate(self.candidates):
            if term in cand["file"].lower() or term in cand["sig"].lower() or term in cand["code"].lower():
                self.tree_cands.insert("", "end", iid=f"fuzz_{idx}", values=(cand["file"], cand["sig"]))

    def render_search_results(self):
        for i in self.tree_search.get_children(): self.tree_search.delete(i)
        for idx, res in enumerate(self.search_results):
            self.tree_search.insert("", "end", iid=f"srch_{idx}", values=(res["file"], res["sig"]))

    def get_selected_candidate(self):
        current_tab = self.left_nb.index(self.left_nb.select())
        if current_tab == 0:
            sel = self.tree_cands.selection()
            if not sel: return None
            return self.candidates[int(sel[0].replace("fuzz_", ""))]
        else:
            sel = self.tree_search.selection()
            if not sel: return None
            return self.search_results[int(sel[0].replace("srch_", ""))]

    def on_candidate_select(self, event):
        cand = self.get_selected_candidate()
        if cand: self.update_actual_code(cand["code"])

    def on_search_select(self, event):
        cand = self.get_selected_candidate()
        if cand: self.update_actual_code(cand["code"])

    def update_actual_code(self, code):
        self.txt_actual.config(state="normal")
        self.txt_actual.delete("1.0", tk.END)
        self.txt_actual.insert("1.0", code)
        self.txt_actual.config(state="disabled")
        self._refresh_visuals()

    def _on_text_change(self, event=None):
        if self._debounce_timer:
            self.after_cancel(self._debounce_timer)
        self._debounce_timer = self.after(300, self._refresh_visuals)

    def _refresh_visuals(self):
        self.txt_expected.tag_configure("diff_del", background="#4a1919")
        self.txt_actual.tag_configure("diff_add", background="#1a3b1a")
        self.txt_expected.tag_remove("diff_del", "1.0", tk.END)
        self.txt_actual.tag_remove("diff_add", "1.0", tk.END)

        str_left = self.txt_expected.get("1.0", tk.END).splitlines()
        str_right = self.txt_actual.get("1.0", tk.END).splitlines()

        # Delegate diff algorithm to the controller
        self.controller.calculate_diff(str_left, str_right)

        self._apply_smali_highlighting(self.txt_expected)
        self._apply_smali_highlighting(self.txt_actual)
        self._apply_smali_highlighting(self.txt_edit)

    def _apply_diff_tags(self, opcodes):
        if not self.txt_expected.winfo_exists() or not self.txt_actual.winfo_exists(): return

        for tag, i1, i2, j1, j2 in opcodes:
            if tag in ('replace', 'delete'):
                for i in range(i1, i2):
                    self.txt_expected.tag_add("diff_del", f"{i + 1}.0", f"{i + 1}.end")
            if tag in ('replace', 'insert'):
                for j in range(j1, j2):
                    self.txt_actual.tag_add("diff_add", f"{j + 1}.0", f"{j + 1}.end")

    def _apply_smali_highlighting(self, txt_widget):
        txt_widget.tag_configure("s_key", foreground="#569CD6")
        txt_widget.tag_configure("s_inst", foreground="#C586C0")
        txt_widget.tag_configure("s_str", foreground="#CE9178")
        txt_widget.tag_configure("s_com", foreground="#6A9955")
        txt_widget.tag_configure("s_reg", foreground="#9CDCFE")

        for t in ["s_key", "s_inst", "s_str", "s_com", "s_reg"]: txt_widget.tag_remove(t, "1.0", tk.END)

        text = txt_widget.get("1.0", tk.END)
        for line_idx, line in enumerate(text.split('\n')):
            tk_line = line_idx + 1
            c_match = re.search(r'#.*', line)
            if c_match:
                txt_widget.tag_add("s_com", f"{tk_line}.{c_match.start()}", f"{tk_line}.{c_match.end()}")
                line = line[:c_match.start()]
            for m in re.finditer(r'".*?"', line): txt_widget.tag_add("s_str", f"{tk_line}.{m.start()}",
                                                                     f"{tk_line}.{m.end()}")
            for m in re.finditer(r'\b[vp]\d+\b', line): txt_widget.tag_add("s_reg", f"{tk_line}.{m.start()}",
                                                                           f"{tk_line}.{m.end()}")
            for m in re.finditer(r'(\.[a-zA-Z0-9_-]+)', line): txt_widget.tag_add("s_key", f"{tk_line}.{m.start()}",
                                                                                  f"{tk_line}.{m.end()}")
            m = re.search(r'^\s*([a-zA-Z0-9_-]+)', line)
            if m and not m.group(1).startswith('.'): txt_widget.tag_add("s_inst", f"{tk_line}.{m.start(1)}",
                                                                        f"{tk_line}.{m.end(1)}")