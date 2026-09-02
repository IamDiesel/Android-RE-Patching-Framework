import tkinter as tk
from tkinter import ttk
import threading
import time


class SmaliGlobalSearchWindow(tk.Toplevel):
    """Eigenständiges Fenster für die globale Echtzeit-RAM-Suche."""

    def __init__(self, parent, ram_cache, load_method_callback):
        super().__init__(parent)
        self.ram_cache = ram_cache
        self.load_method_callback = load_method_callback

        self.title("🔍 Globale RAM-Suche (Echtzeit)")
        self.geometry("900x580")
        self.attributes("-topmost", True)

        self.all_results = []
        self.cancel_search_flag = False

        self.create_widgets()

    def create_widgets(self):
        f_top = ttk.Frame(self)
        f_top.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Suchbegriff:").grid(row=0, column=0, sticky="w", pady=2)
        self.ent_search = ttk.Entry(f_top, width=50)
        self.ent_search.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ergebnisse filtern (AND, per Leerzeichen):").grid(row=1, column=0, sticky="w", pady=2)
        self.ent_filter = ttk.Entry(f_top, width=50)
        self.ent_filter.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ausschließen (kommasepariert, Pfad o. Code):").grid(row=2, column=0, sticky="w", pady=2)
        self.ent_exclude = ttk.Entry(f_top, width=50)
        self.ent_exclude.grid(row=2, column=1, padx=5, pady=2)

        self.lbl_status = ttk.Label(self, text=f"Bereit. Durchsuche {len(self.ram_cache)} Dateien im RAM.")
        self.lbl_status.pack(pady=2)

        f_tree = ttk.Frame(self)
        f_tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree = ttk.Treeview(f_tree, columns=("File", "Line", "Snippet"), show="headings")
        self.tree.heading("File", text="Datei")
        self.tree.heading("Line", text="Zeile")
        self.tree.heading("Snippet", text="Code-Ausschnitt")
        self.tree.column("Line", width=60)

        scrollbar = ttk.Scrollbar(f_tree, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        btn_frame = ttk.Frame(f_top)
        btn_frame.grid(row=0, column=2, rowspan=3, padx=10, sticky="ns")

        ttk.Button(btn_frame, text="Suchen", command=self.do_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="🗑 Leeren", command=self.clear_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="❌ Abbrechen", command=self.cancel_search).pack(side="top", fill="x", pady=2)

        self.ent_search.bind("<Return>", self.do_search)
        self.ent_exclude.bind("<Return>", self.do_search)
        self.ent_filter.bind("<KeyRelease>", self.apply_filter)
        self.tree.bind("<Double-1>", self.on_double_click)

    def on_double_click(self, event):
        sel = self.tree.selection()
        if sel:
            item = self.tree.item(sel[0], "values")
            self.load_method_callback(item[0], target_line=int(item[1]))

    def cancel_search(self):
        self.cancel_search_flag = True

    def clear_search(self):
        self.all_results = []
        for i in self.tree.get_children(): self.tree.delete(i)
        self.ent_search.delete(0, tk.END)
        self.ent_filter.delete(0, tk.END)
        self.ent_exclude.delete(0, tk.END)
        self.lbl_status.config(text="Suche geleert.")

    def apply_filter(self, event=None):
        for i in self.tree.get_children(): self.tree.delete(i)

        f_terms = [t.strip().lower() for t in self.ent_filter.get().split() if t.strip()]
        filtered = []

        for r in self.all_results:
            target_str = (r[0] + " " + r[2]).lower()
            match = True
            for ft in f_terms:
                if ft not in target_str:
                    match = False
                    break
            if match:
                filtered.append(r)

        for r in filtered:
            self.tree.insert("", "end", values=r)

        if f_terms and self.all_results:
            self.lbl_status.config(text=f"Filter aktiv: {len(filtered)} von {len(self.all_results)} Treffern.")
        elif self.all_results:
            self.lbl_status.config(text=f"{len(self.all_results)} Treffer geladen.")

    def do_search(self, event=None):
        for i in self.tree.get_children(): self.tree.delete(i)
        term = self.ent_search.get()
        if not term: return

        self.lbl_status.config(text="Suche läuft im RAM...")
        self.cancel_search_flag = False
        self.update()

        start_time = time.time()
        ex_terms = [t.strip().lower() for t in self.ent_exclude.get().split(",") if t.strip()]

        def search_thread():
            results = []
            for rel_path, content in self.ram_cache:
                if len(results) >= 10000 or self.cancel_search_flag: break

                if ex_terms:
                    skip_file = False
                    path_lower = rel_path.lower()
                    for ex in ex_terms:
                        if ex in path_lower:
                            skip_file = True
                            break
                    if skip_file: continue

                if term in content:
                    for line_no, line in enumerate(content.splitlines(), 1):
                        if term in line:
                            if ex_terms:
                                skip_line = False
                                line_lower = line.lower()
                                for ex in ex_terms:
                                    if ex in line_lower:
                                        skip_line = True
                                        break
                                if skip_line: continue

                            results.append((rel_path, line_no, line.strip()))
                            if len(results) >= 10000: break
            self.after(0, lambda: self.finish_search(results, start_time))

        threading.Thread(target=search_thread, daemon=True).start()

    def finish_search(self, results, start_time):
        if self.cancel_search_flag:
            self.lbl_status.config(text="Suche abgebrochen.")
            return

        self.all_results = results
        elapsed = time.time() - start_time
        self.apply_filter()

        msg = f"{len(results)} Treffer in {elapsed:.3f} Sekunden."
        if len(results) >= 10000: msg += " (UI-Limit 10.000 erreicht!)"
        self.lbl_status.config(text=msg)