import os
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import subprocess
import re
import time
import concurrent.futures
import pickle

from cg_manager import is_system_api

class SmaliStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.smali_patches = []
        self.current_smali_file = ""
        self.current_method_name = ""

        # In-Memory RAM Cache
        self.ram_cache = []
        self.is_indexed = False
        self.is_indexing = False

        self.create_widgets()

    def get_smali_dir(self):
        """Hilfsfunktion: Liefert immer das Verzeichnis der entpackten base.apk"""
        app_source = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        return os.path.join(app_source, "base_unpacked")

    def create_widgets(self):
        # -- 1. TOOLBAR GANZ OBEN --
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", pady=5, padx=5)

        ttk.Button(top_bar, text="📦 1. APK Entpacken (Apktool)", command=self.unpack_apk_async).pack(side="left", padx=5)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(top_bar, variable=self.progress_var, maximum=100, length=150)
        self.lbl_progress_status = ttk.Label(top_bar, text="", font=("Segoe UI", 8, "italic"), foreground="gray")

        ttk.Button(top_bar, text="🔍 Globale Suche", command=self.open_global_search).pack(side="left", padx=5)
        ttk.Button(top_bar, text="📄 In ext. Editor öffnen", command=self.open_in_external_editor).pack(side="left", padx=5)
        ttk.Button(top_bar, text="💾 Zur Patch-Liste", command=self.add_smali_patch).pack(side="right", padx=5)

        self.lbl_smali_file = ttk.Label(top_bar, text="Keine Datei geladen", font=("Segoe UI", 9, "bold"))
        self.lbl_smali_file.pack(side="right", padx=10)

        # -- PATCH-LISTE --
        f_patches = ttk.LabelFrame(self, text="Aktive Smali Patches (Warten auf Build)")
        f_patches.pack(side="bottom", fill="both", expand=False, padx=5, pady=5)

        self.smali_tree = ttk.Treeview(f_patches, columns=("File", "Snippet"), show="headings", height=5)
        self.smali_tree.heading("File", text="Datei")
        self.smali_tree.heading("Snippet", text="Edit Preview")
        self.smali_tree.column("File", width=400)

        patch_scroll = ttk.Scrollbar(f_patches, orient="vertical", command=self.smali_tree.yview)
        self.smali_tree.configure(yscrollcommand=patch_scroll.set)

        patch_scroll.pack(side="right", fill="y")
        self.smali_tree.pack(side="left", fill="both", expand=True)

        self.smali_tree.bind("<Delete>", lambda e: self.remove_smali_patch())

        # -- EDITOR BEREICH --
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # SPALTE 1: OUTLINE & CALLGRAPH
        left_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(left_pane, weight=1)

        f_outline = ttk.LabelFrame(left_pane, text="Klassenübersicht")
        left_pane.add(f_outline, weight=1)
        self.tree_outline = ttk.Treeview(f_outline, columns=("Method",), show="headings")
        self.tree_outline.heading("Method", text="Signatur")
        self.tree_outline.pack(fill="both", expand=True)
        self.tree_outline.bind("<Double-1>", self.on_outline_double_click)
        self.tree_outline.tag_configure("system_api", foreground="gray")

        f_callgraph = ttk.LabelFrame(left_pane, text="Call Graph (Referenzen)")
        cg_toolbar = ttk.Frame(f_callgraph)
        cg_toolbar.pack(fill="x", padx=2, pady=2)
        ttk.Button(cg_toolbar, text="🗑 Graph leeren", command=self.clear_callgraph).pack(side="left")
        ttk.Button(cg_toolbar, text="💾 Sichern", command=self.save_callgraph).pack(side="right")
        ttk.Button(cg_toolbar, text="📂 Laden", command=self.load_callgraph).pack(side="right")

        left_pane.add(f_callgraph, weight=2)
        self.tree_callstack = ttk.Treeview(f_callgraph, columns=("File",), show="tree headings")
        self.tree_callstack.heading("#0", text="Methode")
        self.tree_callstack.heading("File", text="Pfad")
        self.tree_callstack.column("#0", width=180)
        self.tree_callstack.column("File", width=100)
        self.tree_callstack.pack(fill="both", expand=True)
        self.tree_callstack.tag_configure("system_api", foreground="gray")
        self.tree_callstack.bind("<Double-1>", self.on_callgraph_double_click)
        self.tree_callstack.bind("<<TreeviewOpen>>", self.on_cg_node_expand)

        # SPALTE 2: EDITOR
        center_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(center_pane, weight=3)

        frame_orig = ttk.LabelFrame(center_pane, text="Original Code (Read-Only)")
        center_pane.add(frame_orig, weight=1)
        self.txt_smali_orig = tk.Text(frame_orig, wrap="none", font=("Courier", 9), state="disabled", bg="#f0f0f0")
        self.txt_smali_orig.pack(fill="both", expand=True)

        frame_mid = ttk.Frame(center_pane)
        center_pane.add(frame_mid, weight=0)
        ttk.Button(frame_mid, text="⬇ Code zum Editieren kopieren ⬇", command=self.copy_smali_to_edit).pack(pady=2)

        frame_edit = ttk.LabelFrame(center_pane, text="Editierter Code (Dein Patch)")
        center_pane.add(frame_edit, weight=1)
        self.txt_smali_edit = tk.Text(frame_edit, wrap="none", font=("Courier", 9))
        self.txt_smali_edit.pack(fill="both", expand=True)

        # SPALTE 3: XREFS
        right_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_pane, weight=1)

        f_outgoing = ttk.LabelFrame(right_pane, text="Ausgehende Aufrufe (Calls)")
        right_pane.add(f_outgoing, weight=1)
        self.tree_outgoing = ttk.Treeview(f_outgoing, columns=("Target",), show="headings")
        self.tree_outgoing.heading("Target", text="Aufgerufene Methode")
        self.tree_outgoing.pack(fill="both", expand=True)
        self.tree_outgoing.tag_configure("system_api", foreground="gray")
        self.tree_outgoing.bind("<Double-1>", self.on_outgoing_double_click)

        f_incoming = ttk.LabelFrame(right_pane, text="Eingehende Aufrufe (XREF)")
        right_pane.add(f_incoming, weight=1)
        ttk.Button(f_incoming, text="🔍 Finde Aufrufer (Projekt scannen)", command=self.find_incoming_xrefs).pack(fill="x", pady=2)

        self.tree_incoming = ttk.Treeview(f_incoming, columns=("File", "Method"), show="headings")
        self.tree_incoming.heading("File", text="Datei")
        self.tree_incoming.heading("Method", text="Methode")
        self.tree_incoming.column("File", width=80)
        self.tree_incoming.column("Method", width=120)
        self.tree_incoming.pack(fill="both", expand=True)
        self.tree_incoming.tag_configure("system_api", foreground="gray")
        self.tree_incoming.bind("<Double-1>", self.on_incoming_double_click)

    def get_dir_size_mb(self, path):
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp): total += os.path.getsize(fp)
        except: pass
        return total / (1024 * 1024)

    def unpack_apk_async(self):
        if self.app.check_lock(): return

        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        if not app_source_dir: return messagebox.showwarning("Fehler", "Bitte lade zuerst eine App über den App Manager!")

        base_apk_path = os.path.join(app_source_dir, "base.apk")
        if not os.path.exists(base_apk_path): return messagebox.showwarning("Fehler", "base.apk nicht gefunden!")

        smali_dir = self.get_smali_dir()

        def task():
            self.app.is_unpacking = True
            cmd = f'apktool d "base.apk" -o "base_unpacked" -f -r'
            self.app.log(f"[*] Starte Entpacken für Smali: {cmd}")

            self.app.after(0, lambda: self.progress_bar.pack(side="left", padx=5))
            self.app.after(0, lambda: self.lbl_progress_status.pack(side="left", padx=5))
            self.app.after(0, lambda: self.progress_var.set(5))
            self.app.after(0, lambda: self.lbl_progress_status.config(text="Initialisiere Apktool..."))

            try:
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                process = subprocess.Popen(cmd, shell=True, cwd=app_source_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, startupinfo=startupinfo, errors="replace")

                self.last_apktool_log = "Starte..."

                def log_reader():
                    for line in process.stdout:
                        clean_line = line.strip()
                        if clean_line:
                            self.app.log(f"[Apktool] {clean_line}")
                            self.last_apktool_log = clean_line
                            if "Loading resource table" in clean_line: self.app.after(0, lambda: self.progress_var.set(20))
                            elif "Decoding AndroidManifest.xml" in clean_line: self.app.after(0, lambda: self.progress_var.set(40))
                            elif "Baksmaling" in clean_line: self.app.after(0, lambda: self.progress_var.set(60))
                            elif "Copying" in clean_line: self.app.after(0, lambda: self.progress_var.set(80))

                reader_thread = threading.Thread(target=log_reader, daemon=True)
                reader_thread.start()

                last_size, stuck_counter = -1, 0
                while process.poll() is None:
                    time.sleep(1)
                    current_size = self.get_dir_size_mb(smali_dir)
                    status_text = f"Entpacke... {current_size:.1f} MB"
                    self.app.after(0, lambda txt=status_text: self.lbl_progress_status.config(text=txt))

                    if current_size == last_size and current_size > 5:
                        stuck_counter += 1
                        if stuck_counter >= 5 and "Copying" in self.last_apktool_log:
                            process.terminate()
                            break
                    else:
                        stuck_counter = 0
                        last_size = current_size

                reader_thread.join(timeout=1.0)

                if process.returncode in [0, 1, None]:
                    self.app.after(0, lambda: self.progress_var.set(100))
                    self.app.after(0, lambda: self.lbl_progress_status.config(text="Erfolgreich entpackt!"))
                    self.app.log(f"[+] base.apk erfolgreich entpackt nach: {smali_dir}")
                    self.build_ram_index()
                else:
                    self.app.log(f"[!] Fehler beim Entpacken (Exit {process.returncode}).")
            except Exception as e:
                self.app.log(f"[!] Ausnahme beim Entpacken: {e}")
            finally:
                self.app.is_unpacking = False
                self.app.after(3000, lambda: self.progress_bar.pack_forget())
                self.app.after(3000, lambda: self.lbl_progress_status.pack_forget())

        threading.Thread(target=task, daemon=True).start()

    def open_in_external_editor(self):
        if not self.current_smali_file: return
        filepath = os.path.join(self.get_smali_dir(), self.current_smali_file)
        if os.path.exists(filepath):
            try:
                os.startfile(filepath)
            except:
                self.app.log("[!] Konnte externen Editor nicht starten.")

    def load_method(self, rel_filepath, target_line=None, method_signature=None, add_as_root=True):
        smali_dir = self.get_smali_dir()
        filepath = os.path.join(smali_dir, rel_filepath)

        if not os.path.exists(filepath): return self.app.log(f"[!] Datei nicht gefunden: {filepath}")

        try:
            with open(filepath, "r", encoding="utf-8") as f: lines = f.readlines()
        except Exception as e:
            return self.app.log(f"[!] Lese-Fehler: {e}")

        start_idx, end_idx = -1, -1

        if target_line is not None:
            idx = target_line - 1
            while idx >= 0:
                if lines[idx].strip().startswith(".method"):
                    start_idx = idx
                    break
                idx -= 1
        elif method_signature is not None:
            for i, line in enumerate(lines):
                if line.strip().startswith(".method") and method_signature in line:
                    start_idx = i
                    break

        if start_idx != -1:
            idx = start_idx
            while idx < len(lines):
                if lines[idx].strip().startswith(".end method"):
                    end_idx = idx
                    break
                idx += 1

        if start_idx != -1 and end_idx != -1:
            block = "".join(lines[start_idx:end_idx + 1])
            method_def = lines[start_idx].strip().replace(".method ", "")

            self.current_smali_file = rel_filepath.replace("\\", "/")
            self.current_method_name = method_def
            self.lbl_smali_file.config(text=f"{os.path.basename(self.current_smali_file)} -> {method_def.split('(')[0]}")

            self.txt_smali_orig.config(state="normal")
            self.txt_smali_orig.delete("1.0", tk.END)
            self.txt_smali_orig.insert("1.0", block)
            self.txt_smali_orig.config(state="disabled")

            self.app.cg.add_node(self.current_smali_file, self.current_method_name)
            if add_as_root:
                node_id = f"{self.current_smali_file}|{self.current_method_name}"
                self.app.cg.make_root(node_id)

            self.parse_outgoing_calls(block)
            self.update_outline(lines)
            self.refresh_callgraph_ui()
        else:
            self.app.log("[!] Konnte Methode in der Datei nicht extrahieren.")

    def update_outline(self, lines):
        for i in self.tree_outline.get_children(): self.tree_outline.delete(i)
        for line in lines:
            line = line.strip()
            if line.startswith(".method"):
                sig = line.replace(".method ", "")
                display_sig = re.sub(r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*', '', sig)
                tags = ("system_api", sig) if is_system_api("L" + self.current_smali_file.replace(".smali", "") + ";") else (sig,)
                self.tree_outline.insert("", "end", values=(display_sig,), tags=tags)

    def parse_outgoing_calls(self, method_block):
        for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
        matches = re.findall(r'invoke-\w+(?:/[a-z0-9]+)? \{[^}]*\}, (L[^;]+;->[^\s]+)', method_block)
        unique_calls = list(dict.fromkeys(matches))

        for call in unique_calls:
            cls_part, meth_part = call.split(";->")
            tags = ("system_api",) if is_system_api(cls_part) else ()
            self.tree_outgoing.insert("", "end", values=(call,), tags=tags)

            rel_base = cls_part[1:] + ".smali"
            found_path = self.resolve_smali_path(rel_base)
            callee_path = found_path if found_path else cls_part[1:]
            self.app.cg.add_edge(self.current_smali_file, self.current_method_name, callee_path, meth_part)

    def resolve_smali_path(self, rel_base):
        smali_dir = self.get_smali_dir()
        try:
            for item in os.listdir(smali_dir):
                if item.startswith("smali") and os.path.isdir(os.path.join(smali_dir, item)):
                    test_path = os.path.join(item, rel_base)
                    if os.path.exists(os.path.join(smali_dir, test_path)):
                        return test_path
        except: pass
        return None

    def find_incoming_xrefs(self):
        if self.app.check_lock() or not self.current_smali_file: return

        if not self.is_indexed:
            if not self.is_indexing: self.build_ram_index()
            return messagebox.showinfo("Index fehlt", "RAM-Index wird gerade geladen. Bitte gleich nochmal klicken.")

        parts = self.current_smali_file.split("/")
        dalvik_class = f"L{'/'.join(parts[1:]).replace('.smali', '')};" if parts[0].startswith("smali") else f"L{self.current_smali_file.replace('.smali', '')};"
        method_name = re.sub(r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*', '', self.current_method_name).split('(')[0]

        search_term = f"{dalvik_class}->{method_name}("
        self.app.log(f"[*] Suche XREFs für: {search_term}")

        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        self.tree_incoming.insert("", "end", values=("Suche läuft im RAM...", ""))

        self.cancel_xref_flag = False
        def cancel_xref(): self.cancel_xref_flag = True
        btn_cancel_xref = ttk.Button(self.tree_incoming.master, text="❌ XREF Suche abbrechen", command=cancel_xref)
        btn_cancel_xref.pack(fill="x", pady=2)

        def search_task():
            results = []
            for rel_path, content in self.ram_cache:
                if self.cancel_xref_flag or len(results) >= 500: break
                if not rel_path.endswith(".smali"): continue

                if search_term in content:
                    lines = content.splitlines()
                    for line_no, line in enumerate(lines):
                        if search_term in line:
                            idx = line_no
                            while idx >= 0:
                                if lines[idx].strip().startswith(".method"):
                                    caller_method = lines[idx].strip().replace(".method ", "")
                                    results.append((rel_path, caller_method, line_no + 1))
                                    break
                                idx -= 1
                            if len(results) >= 500: break

            self.app.after(0, lambda: finish_xref(results))

        def finish_xref(results):
            btn_cancel_xref.destroy()
            if self.cancel_xref_flag:
                for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
                self.tree_incoming.insert("", "end", values=("Suche abgebrochen.", ""))
                return
            if len(results) >= 500: self.app.log("[!] Limit erreicht: Zeige nur die ersten 500 Aufrufer.")
            self._update_incoming_ui(results)

        threading.Thread(target=search_task, daemon=True).start()

    def build_ram_index(self):
        smali_dir = self.get_smali_dir()
        if not os.path.exists(smali_dir): return
        if self.is_indexing: return

        def task():
            self.is_indexing = True
            try:
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Prüfe RAM-Index..."))
                self.app.after(0, lambda: self.lbl_progress_status.pack(side="left", padx=5))
                self.ram_cache.clear()
                self.is_indexed = False

                pkg_name = self.app.cfg.config.get("APP_PACKAGE", "app")
                index_file = os.path.join(smali_dir, f".{pkg_name}_index.pkl")

                if os.path.exists(index_file):
                    self.app.log(f"[*] Fand vorberechneten Index ({index_file}). Lade in RAM...")
                    with open(index_file, "rb") as f: self.ram_cache = pickle.load(f)
                else:
                    self.app.log("[*] Kein Index gefunden. Sammle Dateipfade (das geht schnell)...")
                    filepaths = []
                    for root, _, files in os.walk(smali_dir):
                        for file in files:
                            if file.endswith(".smali") or file.endswith(".xml"):
                                filepaths.append(os.path.join(root, file))

                    total_files = len(filepaths)
                    cache = []
                    files_read = 0

                    def read_file(path):
                        try:
                            with open(path, "r", encoding="utf-8") as f: return (os.path.relpath(path, smali_dir), f.read())
                        except: return None

                    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                        futures = [executor.submit(read_file, p) for p in filepaths]
                        for future in concurrent.futures.as_completed(futures):
                            res = future.result()
                            if res: cache.append(res)
                            files_read += 1
                            if files_read % 1000 == 0 or files_read == total_files:
                                pct = int((files_read / total_files) * 100)
                                self.app.after(0, lambda m=f"Lese in RAM... {files_read}/{total_files} ({pct}%)": self.lbl_progress_status.config(text=m))
                    self.ram_cache = cache
                    try:
                        self.app.log(f"[*] Lese-Vorgang beendet! Speichere Cache in {index_file} ...")
                        with open(index_file, "wb") as f: pickle.dump(cache, f)
                    except Exception as e:
                        self.app.log(f"[!] Konnte .pkl Index nicht speichern: {e}")

                self.is_indexed = True
                self.app.log(f"[+] RAM-Index bereit: {len(self.ram_cache)} Dateien geladen.")
                self.app.after(0, lambda: self.lbl_progress_status.config(text=f"Index bereit ({len(self.ram_cache)} Dateien)."))
            except Exception as e:
                self.app.log(f"[!] Schwerer Fehler beim Indexieren: {e}")
            finally:
                self.is_indexing = False
                self.app.after(3000, lambda: self.lbl_progress_status.pack_forget())

        threading.Thread(target=task, daemon=True).start()

    # --- GEFIXTER GLOBAL-SEARCH AUFRUF (War fehlerhaft deklariert) ---
    def open_global_search(self):
        if self.app.check_lock(): return
        smali_dir = self.get_smali_dir()
        if not os.path.exists(smali_dir): return messagebox.showwarning("Fehler", "Bitte entpacke die App zuerst!")

        if not self.is_indexed:
            if not self.is_indexing:
                self.build_ram_index()
            return messagebox.showinfo("Index wird erstellt", "Die Codebasis wird gerade geladen. Bitte kurz warten.")

        top = tk.Toplevel(self)
        top.title("🔍 Globale RAM-Suche (Echtzeit)")
        top.geometry("900x550")
        top.attributes("-topmost", True)

        f_top = ttk.Frame(top)
        f_top.pack(fill="x", padx=10, pady=10)
        ttk.Label(f_top, text="Suchbegriff:").grid(row=0, column=0, sticky="w", pady=2)
        ent_search = ttk.Entry(f_top, width=50)
        ent_search.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ergebnisse filtern:").grid(row=1, column=0, sticky="w", pady=2)
        ent_filter = ttk.Entry(f_top, width=50)
        ent_filter.grid(row=1, column=1, padx=5, pady=2)

        lbl_status = ttk.Label(top, text=f"Bereit. Durchsuche {len(self.ram_cache)} Dateien im RAM.")
        lbl_status.pack(pady=2)

        f_tree = ttk.Frame(top)
        f_tree.pack(fill="both", expand=True, padx=10, pady=5)

        tree = ttk.Treeview(f_tree, columns=("File", "Line", "Snippet"), show="headings")
        tree.heading("File", text="Datei")
        tree.heading("Line", text="Zeile")
        tree.heading("Snippet", text="Code-Ausschnitt")
        tree.column("Line", width=60)

        scrollbar = ttk.Scrollbar(f_tree, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        all_results = []
        self.cancel_search_flag = False

        def apply_filter(event=None):
            for i in tree.get_children(): tree.delete(i)
            f_term = ent_filter.get().lower()
            filtered = [r for r in all_results if f_term in r[0].lower() or f_term in r[2].lower()]
            for r in filtered: tree.insert("", "end", values=r)
            if f_term and all_results: lbl_status.config(text=f"Filter aktiv: {len(filtered)} von {len(all_results)} Treffern.")
            elif all_results: lbl_status.config(text=f"{len(all_results)} Treffer geladen.")

        def do_search():
            for i in tree.get_children(): tree.delete(i)
            term = ent_search.get()
            if not term: return

            lbl_status.config(text="Suche läuft im RAM...")
            ent_filter.delete(0, tk.END)
            self.cancel_search_flag = False
            top.update()
            start_time = time.time()

            def search_thread():
                results = []
                for rel_path, content in self.ram_cache:
                    if len(results) >= 10000 or self.cancel_search_flag: break
                    if term in content:
                        for line_no, line in enumerate(content.splitlines(), 1):
                            if term in line:
                                results.append((rel_path, line_no, line.strip()))
                                if len(results) >= 10000: break
                self.app.after(0, lambda: finish_search(results))

            def finish_search(results):
                if self.cancel_search_flag: return lbl_status.config(text="Suche abgebrochen.")
                nonlocal all_results
                all_results = results
                elapsed = time.time() - start_time
                apply_filter()
                msg = f"{len(results)} Treffer in {elapsed:.3f} Sekunden."
                if len(results) >= 10000: msg += " (UI-Limit 10.000 erreicht!)"
                lbl_status.config(text=msg)

            threading.Thread(target=search_thread, daemon=True).start()

        def copy_search_results(event):
            selected = tree.selection()
            if not selected: return "break"
            lines = [f"{tree.item(i, 'values')[0]}:{tree.item(i, 'values')[1]} - {tree.item(i, 'values')[2]}" for i in selected]
            top.clipboard_clear()
            top.clipboard_append("\n".join(lines))
            return "break"

        btn_frame = ttk.Frame(f_top)
        btn_frame.grid(row=0, column=2, rowspan=2, padx=10, sticky="ns")

        ttk.Button(btn_frame, text="Suchen", command=do_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="❌ Abbrechen", command=lambda: setattr(self, 'cancel_search_flag', True)).pack(side="top", fill="x", pady=2)

        ent_search.bind("<Return>", lambda e: do_search())
        ent_filter.bind("<KeyRelease>", apply_filter)
        tree.bind("<Double-1>", lambda e: self.load_method(tree.item(tree.selection()[0], "values")[0], target_line=int(tree.item(tree.selection()[0], "values")[1]), add_as_root=True) if tree.selection() else None)
        tree.bind("<Control-a>", lambda e: [tree.selection_set(tree.get_children()), "break"][1])
        tree.bind("<Control-c>", copy_search_results)

    def _update_incoming_ui(self, results):
        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        if not results:
            self.tree_incoming.insert("", "end", values=("Keine Aufrufer gefunden.", ""))
        else:
            current_node_id = f"{self.current_smali_file}|{self.current_method_name}"
            for r in results:
                caller_path, caller_sig, line = r
                disp_method = caller_sig.split('(')[0]
                tags = ("system_api", caller_sig) if is_system_api("L" + caller_path) else (caller_sig,)
                self.tree_incoming.insert("", "end", values=(caller_path.replace("\\", "/"), disp_method), tags=tags)
                self.app.cg.add_edge(caller_path, caller_sig, self.current_smali_file, self.current_method_name)
                self.app.cg.make_root(f"{caller_path}|{caller_sig}")

            self.app.cg.remove_root(current_node_id)
            self.refresh_callgraph_ui()

    def clear_callgraph(self):
        self.app.cg.clear()
        self.refresh_callgraph_ui()

    def save_callgraph(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path: self.app.cg.save(path)

    def load_callgraph(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path and self.app.cg.load(path): self.refresh_callgraph_ui()

    def refresh_callgraph_ui(self):
        for i in self.tree_callstack.get_children(): self.tree_callstack.delete(i)
        for root_id in self.app.cg.roots: self._insert_cg_node("", root_id)

    def _insert_cg_node(self, parent_item, node_id):
        node = self.app.cg.get_node(node_id)
        if not node: return None
        disp_text = node.signature.split('(')[0]
        tags = ["system_api"] if is_system_api("L" + node.filepath) else []
        tags.append(node_id)
        item = self.tree_callstack.insert(parent_item, "end", text=disp_text, values=(os.path.basename(node.filepath),), tags=tags)
        if node.callees: self.tree_callstack.insert(item, "end", text="*dummy*")
        return item

    def on_cg_node_expand(self, event):
        item = self.tree_callstack.focus()
        children = self.tree_callstack.get_children(item)
        if len(children) == 1 and self.tree_callstack.item(children[0], "text") == "*dummy*":
            self.tree_callstack.delete(children[0])
            tags = self.tree_callstack.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]
            node = self.app.cg.get_node(node_id)
            if node:
                for callee_id in node.callees: self._insert_cg_node(item, callee_id)

    def on_outline_double_click(self, event):
        sel = self.tree_outline.selection()
        if not sel: return
        tags = self.tree_outline.item(sel[0], "tags")
        sig = tags[1] if "system_api" in tags else tags[0]
        self.load_method(self.current_smali_file, method_signature=sig, add_as_root=True)

    def on_callgraph_double_click(self, event):
        sel = self.tree_callstack.selection()
        if not sel: return
        tags = self.tree_callstack.item(sel[0], "tags")
        node_id = tags[1] if "system_api" in tags else tags[0]
        if "system_api" in tags: return self.app.log(f"[!] {node_id.split('|')[0]} ist eine System-API.")
        if "|" in node_id:
            filepath, sig = node_id.split("|", 1)
            self.app.active_cg_node = node_id
            self.load_method(filepath, method_signature=sig, add_as_root=False)

    def on_outgoing_double_click(self, event):
        sel = self.tree_outgoing.selection()
        if not sel: return
        tags = self.tree_outgoing.item(sel[0], "tags")
        target = self.tree_outgoing.item(sel[0], "values")[0]
        if "system_api" in tags: return self.app.log(f"[!] System Aufruf: {target}")
        if ";->" in target:
            cls_part, meth_part = target.split(";->")
            found_path = self.resolve_smali_path(cls_part[1:] + ".smali")
            if found_path: self.load_method(found_path, method_signature=meth_part, add_as_root=True)

    def on_incoming_double_click(self, event):
        sel = self.tree_incoming.selection()
        if not sel: return
        caller_file = self.tree_incoming.item(sel[0], "values")[0]
        tags = self.tree_incoming.item(sel[0], "tags")
        if "Keine Aufrufer" in caller_file or "Suche" in caller_file: return
        if "system_api" in tags: return self.app.log("[!] Aufruf durch Android-System.")
        caller_method = tags[1] if "system_api" in tags else tags[0]
        if caller_method: self.load_method(caller_file, method_signature=caller_method, add_as_root=True)

    def copy_smali_to_edit(self):
        self.txt_smali_edit.delete("1.0", tk.END)
        self.txt_smali_edit.insert("1.0", self.txt_smali_orig.get("1.0", tk.END).strip())

    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.txt_smali_orig.get("1.0", tk.END).strip()
        edit = self.txt_smali_edit.get("1.0", tk.END).strip()
        if not f or not orig or not edit: return messagebox.showwarning("Fehlt", "Original oder Edit leer!")

        for p in self.smali_patches:
            if p["file"] == f and p["orig"] == orig:
                if not messagebox.askyesno("Patch existiert", "Überschreiben?"): return
                self.smali_patches.remove(p)
                break

        self.smali_patches.append({"type": "smali", "file": f, "orig": orig, "edit": edit})
        self.refresh_smali_tree()
        self.txt_smali_edit.delete("1.0", tk.END)
        self.app.log(f"[+] Smali Patch für {f} gespeichert.")

    def remove_smali_patch(self):
        sel = self.smali_tree.selection()
        if not sel: return
        del self.smali_patches[self.smali_tree.index(sel[0])]
        self.refresh_smali_tree()

    def refresh_smali_tree(self):
        for i in self.smali_tree.get_children(): self.smali_tree.delete(i)
        for p in self.smali_patches:
            snippet = p["edit"][:80].replace("\n", " ") + "..."
            self.smali_tree.insert("", "end", values=(p["file"], snippet))