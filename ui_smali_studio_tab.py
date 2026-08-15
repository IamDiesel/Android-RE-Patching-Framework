import os
import tkinter as tk
from tkinter import ttk, messagebox
import re
import subprocess
import threading
import time

from cg_manager import is_system_api
from smali_editor import SmaliEditorWidget
from smali_search import SmaliSearchEngine


class SmaliStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.smali_patches = []
        self.current_smali_file = ""
        self.current_method_name = ""

        # Init Search Engine
        self.search_engine = SmaliSearchEngine(self.app.log, self.update_status)

        self.create_widgets()

    def get_smali_dir(self):
        """Liefert das Read-Only Source-Verzeichnis der entpackten App."""
        app_source = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        return os.path.join(app_source, "base_unpacked")

    def update_status(self, msg):
        self.app.after(0, lambda: self.lbl_progress_status.config(text=msg))

    def _ensure_index_loaded(self):
        """Prüft ob der Index im RAM ist, und lädt ihn nach einem Neustart automatisch nach."""
        if self.search_engine.is_indexed:
            return True

        if self.search_engine.is_indexing:
            messagebox.showinfo("Warte", "RAM Index wird gerade aufgebaut. Bitte kurz warten.")
            return False

        # Automatischer Lade-Versuch, falls der Ordner nach einem Neustart schon existiert
        source_smali = self.get_smali_dir()
        if os.path.exists(source_smali):
            pkg = self.app.cfg.config.get("APP_PACKAGE", "app")
            dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), "base_unpacked")

            self.search_engine.build_ram_index(
                source_smali,
                dest_cache,
                pkg,
                lambda c: self.update_status(f"Bereit ({c} Dateien)")
            )
            messagebox.showinfo("Lade Index...",
                                "Der gespeicherte Cache wird in den RAM geladen.\nBitte versuche es in 2-3 Sekunden nochmal!")
            return False

        # Wenn der Ordner gar nicht existiert, muss Apktool ran
        messagebox.showwarning("Fehler",
                               "Kein entpackter Code gefunden!\n\nBitte klicke zuerst oben links auf '📦 APK Entpacken & Indexieren'.")
        return False

    def create_widgets(self):
        # -- 1. TOOLBAR GANZ OBEN --
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", pady=5, padx=5)

        ttk.Button(top_bar, text="📦 APK Entpacken & Indexieren", command=self.unpack_apk_async).pack(side="left",
                                                                                                     padx=5)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(top_bar, variable=self.progress_var, maximum=100, length=150)

        self.lbl_progress_status = ttk.Label(top_bar, text="", foreground="gray")
        self.lbl_progress_status.pack(side="left", padx=5)

        ttk.Button(top_bar, text="🔍 Globale Suche", command=self.open_global_search).pack(side="left", padx=5)
        ttk.Button(top_bar, text="💾 Zur Patch-Liste", command=self.add_smali_patch).pack(side="right", padx=5)

        self.lbl_smali_file = ttk.Label(top_bar, text="Keine Datei geladen", font=("Segoe UI", 9, "bold"))
        self.lbl_smali_file.pack(side="right", padx=10)

        # -- 2. PATCH-LISTE (Unten verankert) --
        f_patches = ttk.LabelFrame(self, text="Aktive Smali Patches (Warten auf Build)")
        f_patches.pack(side="bottom", fill="both", expand=False, padx=5, pady=5)

        self.smali_tree = ttk.Treeview(f_patches, columns=("File", "Snippet"), show="headings", height=4)
        self.smali_tree.heading("File", text="Datei")
        self.smali_tree.heading("Snippet", text="Edit Preview")
        self.smali_tree.column("File", width=400)
        self.smali_tree.pack(side="left", fill="both", expand=True)
        self.smali_tree.bind("<Delete>", lambda e: self.remove_smali_patch())

        # -- 3. MAIN IDE LAYOUT (Links, Mitte, Rechts) --
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # LINKES PANEL (Notebook: Outline, CallGraph, DataGraph)
        self.left_nb = ttk.Notebook(main_paned)
        main_paned.add(self.left_nb, weight=1)

        # OUTLINE TAB
        f_outline = ttk.Frame(self.left_nb)
        self.tree_outline = ttk.Treeview(f_outline, columns=("Type", "Name"), show="headings")
        self.tree_outline.heading("Type", text="Typ")
        self.tree_outline.heading("Name", text="Signatur / Feldname")
        self.tree_outline.column("Type", width=40, stretch=False)
        self.tree_outline.pack(fill="both", expand=True)
        self.tree_outline.bind("<Double-1>", self.on_outline_double_click)
        self.tree_outline.tag_configure("system_api", foreground="gray")
        self.tree_outline.tag_configure("field", foreground="#007ACC")
        self.tree_outline.tag_configure("method", foreground="#A31515")
        self.left_nb.add(f_outline, text="Outline")

        # CALL GRAPH TAB
        f_callgraph = ttk.Frame(self.left_nb)
        self.tree_callstack = ttk.Treeview(f_callgraph, columns=("File",), show="tree headings")
        self.tree_callstack.heading("#0", text="Methode")
        self.tree_callstack.heading("File", text="Pfad")
        self.tree_callstack.pack(fill="both", expand=True)
        self.tree_callstack.bind("<Double-1>", self.on_callgraph_double_click)
        self.tree_callstack.bind("<<TreeviewOpen>>", self.on_cg_node_expand)
        self.tree_callstack.tag_configure("system_api", foreground="gray")
        self.left_nb.add(f_callgraph, text="Call Graph")

        # DATA GRAPH TAB
        f_datagraph = ttk.Frame(self.left_nb)
        self.tree_datagraph = ttk.Treeview(f_datagraph, columns=("Access", "Target"), show="headings")
        self.tree_datagraph.heading("Access", text="Zugriff")
        self.tree_datagraph.heading("Target", text="Variable / Feld")
        self.tree_datagraph.column("Access", width=60, stretch=False)
        self.tree_datagraph.pack(fill="both", expand=True)
        self.tree_datagraph.tag_configure("read", foreground="#6A9955")  # Grün für GET
        self.tree_datagraph.tag_configure("write", foreground="#D16969")  # Rot für PUT
        self.left_nb.add(f_datagraph, text="Data Graph")

        # CENTER PANEL (Editor Component)
        self.editor = SmaliEditorWidget(main_paned)
        main_paned.add(self.editor, weight=3)

        # RECHTES PANEL (Notebook: XREFs In/Out)
        self.right_nb = ttk.Notebook(main_paned)
        main_paned.add(self.right_nb, weight=1)

        f_incoming = ttk.Frame(self.right_nb)
        ttk.Button(f_incoming, text="🔍 Finde Aufrufer (XREF Scan)", command=self.find_incoming_xrefs).pack(fill="x")
        self.tree_incoming = ttk.Treeview(f_incoming, columns=("File", "Method"), show="headings")
        self.tree_incoming.heading("File", text="Aufrufer-Datei")
        self.tree_incoming.heading("Method", text="Methode")
        self.tree_incoming.pack(fill="both", expand=True)
        self.tree_incoming.bind("<Double-1>", self.on_incoming_double_click)
        self.tree_incoming.tag_configure("system_api", foreground="gray")
        self.right_nb.add(f_incoming, text="XREF (Incoming)")

        f_outgoing = ttk.Frame(self.right_nb)
        self.tree_outgoing = ttk.Treeview(f_outgoing, columns=("Target",), show="headings")
        self.tree_outgoing.heading("Target", text="Aufgerufene Methode")
        self.tree_outgoing.pack(fill="both", expand=True)
        self.tree_outgoing.bind("<Double-1>", self.on_outgoing_double_click)
        self.tree_outgoing.tag_configure("system_api", foreground="gray")
        self.right_nb.add(f_outgoing, text="Calls (Outgoing)")

    def get_dir_size_mb(self, path):
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
        except:
            pass
        return total / (1024 * 1024)

    def unpack_apk_async(self):
        if self.app.check_lock(): return

        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        if not app_source_dir or not os.path.exists(os.path.join(app_source_dir, "base.apk")):
            return messagebox.showwarning("Fehler", "base.apk fehlt im Source-Ordner!")

        smali_dir = self.get_smali_dir()

        def task():
            self.app.is_unpacking = True
            cmd = 'apktool d "base.apk" -o "base_unpacked" -f'
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

                process = subprocess.Popen(cmd, shell=True, cwd=app_source_dir,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, startupinfo=startupinfo,
                                           errors="replace")

                self.last_apktool_log = "Starte..."

                def log_reader():
                    for line in process.stdout:
                        clean_line = line.strip()
                        if clean_line:
                            self.app.log(f"[Apktool] {clean_line}")
                            self.last_apktool_log = clean_line
                            if "Loading resource table" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(20))
                            elif "Decoding AndroidManifest.xml" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(40))
                            elif "Baksmaling" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(60))
                            elif "Copying assets" in clean_line or "Copying raw" in clean_line or "Copying lib" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(80))
                            elif "Copying unknown" in clean_line or "Copying original" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(90))

                reader_thread = threading.Thread(target=log_reader, daemon=True)
                reader_thread.start()

                last_size = -1
                stuck_counter = 0

                while process.poll() is None:
                    time.sleep(1)
                    current_size = self.get_dir_size_mb(smali_dir)

                    status_text = f"Entpacke... {current_size:.1f} MB geschrieben"
                    if "Copying unknown" in self.last_apktool_log or "Copying original" in self.last_apktool_log:
                        status_text = f"Kopiere Assets... {current_size:.1f} MB"

                    self.app.after(0, lambda txt=status_text: self.lbl_progress_status.config(text=txt))

                    if current_size == last_size and current_size > 5:
                        stuck_counter += 1
                        if stuck_counter >= 5 and "Copying" in self.last_apktool_log:
                            self.app.log("[*] Ordner wächst nicht mehr. Beende blockierenden Apktool-Prozess...")
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

                    pkg = self.app.cfg.config.get("APP_PACKAGE", "app")
                    source_smali = self.get_smali_dir()
                    dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), "base_unpacked")

                    self.search_engine.build_ram_index(
                        source_smali, dest_cache, pkg,
                        lambda c: self.update_status(f"Bereit ({c} Dateien)")
                    )
                else:
                    self.app.log(f"[!] Fehler beim Entpacken (Exit {process.returncode}).")
                    self.app.after(0, lambda: self.lbl_progress_status.config(
                        text=f"Fehler! Exit-Code: {process.returncode}"))

            except Exception as e:
                self.app.log(f"[!] Ausnahme beim Entpacken: {e}")
            finally:
                self.app.is_unpacking = False
                self.app.after(3000, lambda: self.progress_bar.pack_forget())

        threading.Thread(target=task, daemon=True).start()

    def load_method(self, rel_filepath, target_line=None, method_signature=None, add_as_root=True):
        filepath = os.path.join(self.get_smali_dir(), rel_filepath)
        if not os.path.exists(filepath): return

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start_idx, end_idx = -1, -1
        method_def = ""

        if target_line is not None:
            idx = target_line - 1
            while idx >= 0:
                if lines[idx].strip().startswith(".method"):
                    start_idx = idx
                    break
                idx -= 1

            if start_idx != -1:
                method_def = lines[start_idx].strip().replace(".method ", "")
                idx = start_idx
                while idx < len(lines):
                    if lines[idx].strip().startswith(".end method"):
                        end_idx = idx
                        break
                    idx += 1
            else:
                # Class Header Fallback
                start_idx = 0
                end_idx = len(lines) - 1
                for i in range(len(lines)):
                    if lines[i].strip().startswith(".method"):
                        end_idx = i - 1
                        break
                method_def = "<Klassen-Header & Felder>"

        elif method_signature is not None:
            for i, l in enumerate(lines):
                if l.strip().startswith(".method") and method_signature in l:
                    start_idx = i
                    method_def = lines[start_idx].strip().replace(".method ", "")
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
            self.current_smali_file = rel_filepath.replace("\\", "/")
            self.current_method_name = method_def

            disp_name = method_def.split('(')[0] if "(" in method_def else method_def
            self.lbl_smali_file.config(text=f"{os.path.basename(self.current_smali_file)} -> {disp_name}")

            self.editor.load_code(block)

            if method_def != "<Klassen-Header & Felder>":
                self.app.cg.add_node(self.current_smali_file, self.current_method_name)
                if add_as_root:
                    node_id = f"{self.current_smali_file}|{self.current_method_name}"
                    self.app.cg.make_root(node_id)
                self.parse_outgoing_calls(block)
                self.parse_data_flow(block)  # Befüllt den neuen Data Graph
            else:
                for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
                for i in self.tree_datagraph.get_children(): self.tree_datagraph.delete(i)

            self.update_outline(lines)
            self.refresh_callgraph_ui()
        else:
            self.app.log("[!] Konnte den Block in der Datei nicht extrahieren.")

    def update_outline(self, lines):
        """Erkennt nun sowohl .method als auch .field Deklarationen."""
        for i in self.tree_outline.get_children(): self.tree_outline.delete(i)

        is_system = is_system_api("L" + self.current_smali_file.replace(".smali", "") + ";")

        for line in lines:
            line = line.strip()
            if line.startswith(".method"):
                sig = line.replace(".method ", "")
                disp = re.sub(
                    r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*',
                    '', sig)
                tags = ("system_api", sig) if is_system else ("method", sig)
                self.tree_outline.insert("", "end", values=("[M]", disp), tags=tags)
            elif line.startswith(".field"):
                sig = line.replace(".field ", "")
                disp = re.sub(r'^(public |private |protected |static |final |transient |volatile )*', '', sig)
                tags = ("system_api", sig) if is_system else ("field", sig)
                self.tree_outline.insert("", "end", values=("[F]", disp), tags=tags)

    def parse_outgoing_calls(self, method_block):
        for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
        matches = re.findall(r'invoke-\w+(?:/[a-z0-9]+)? \{[^}]*\}, (L[^;]+;->[^\s]+)', method_block)
        for call in list(dict.fromkeys(matches)):
            cls_part, meth_part = call.split(";->")
            tags = ("system_api",) if is_system_api(cls_part) else ()
            self.tree_outgoing.insert("", "end", values=(call,), tags=tags)

            # Kante im CallGraph-Manager speichern
            rel_base = cls_part[1:] + ".smali"
            found_path = self.resolve_smali_path(rel_base)
            callee_path = found_path if found_path else cls_part[1:]
            self.app.cg.add_edge(self.current_smali_file, self.current_method_name, callee_path, meth_part)

    def parse_data_flow(self, method_block):
        """Befüllt den Data Graph mit gelesenen (get) und geschriebenen (put) Feldern."""
        for i in self.tree_datagraph.get_children(): self.tree_datagraph.delete(i)

        # Regex erfasst iget, sget, iput, sput und zieht das Target-Feld raus
        matches = re.findall(r'\b([is](?:get|put)(?:-[a-z]+)?)\s+[^,]+(?:,\s*[^,]+)?,\s*(L[^;]+;->[^\s]+)',
                             method_block)

        for instruction, target in list(dict.fromkeys(matches)):
            if "get" in instruction:
                self.tree_datagraph.insert("", "end", values=("READ", target), tags=("read", target))
            elif "put" in instruction:
                self.tree_datagraph.insert("", "end", values=("WRITE", target), tags=("write", target))

    def resolve_smali_path(self, rel_base):
        smali_dir = self.get_smali_dir()
        try:
            for item in os.listdir(smali_dir):
                if item.startswith("smali") and os.path.isdir(os.path.join(smali_dir, item)):
                    test_path = os.path.join(item, rel_base)
                    if os.path.exists(os.path.join(smali_dir, test_path)):
                        return test_path
        except:
            pass
        return None

    def find_incoming_xrefs(self):
        if not self._ensure_index_loaded() or not self.current_smali_file:
            return

        parts = self.current_smali_file.split("/")
        d_class = f"L{'/'.join(parts[1:]).replace('.smali', '')};" if parts[0].startswith(
            "smali") else f"L{self.current_smali_file.replace('.smali', '')};"
        m_name = \
        re.sub(r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*',
               '', self.current_method_name).split('(')[0]
        search_term = f"{d_class}->{m_name}("

        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        self.tree_incoming.insert("", "end", values=("Suche läuft...", ""))

        def on_results(results, cancelled):
            self.app.after(0, lambda: self._update_incoming_ui(results))

        self.search_engine.search_xrefs_incoming(search_term, on_results)

    def _update_incoming_ui(self, results):
        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)

        current_node_id = f"{self.current_smali_file}|{self.current_method_name}"

        for r in results:
            tags = ("system_api", r[1]) if is_system_api("L" + r[0]) else (r[1],)
            self.tree_incoming.insert("", "end", values=(r[0], r[1].split('(')[0]), tags=tags)

            # Im CallGraph Manager eintragen
            self.app.cg.add_edge(r[0], r[1], self.current_smali_file, self.current_method_name)
            self.app.cg.make_root(f"{r[0]}|{r[1]}")

        self.app.cg.remove_root(current_node_id)
        self.refresh_callgraph_ui()

    def refresh_callgraph_ui(self):
        """Zeichnet den CallGraph Baum anhand des cg_manager neu."""
        for i in self.tree_callstack.get_children(): self.tree_callstack.delete(i)
        for root_id in self.app.cg.roots:
            self._insert_cg_node("", root_id)

    def _insert_cg_node(self, parent_item, node_id):
        node = self.app.cg.get_node(node_id)
        if not node: return None

        disp_text = node.signature.split('(')[0]
        tags = ["system_api"] if is_system_api("L" + node.filepath) else []
        tags.append(node_id)

        item = self.tree_callstack.insert(parent_item, "end", text=disp_text, values=(os.path.basename(node.filepath),),
                                          tags=tags)

        # Füge einen Dummy-Knoten hinzu, falls diese Methode weitere aufruft (erzeugt das "+" Icon zum Aufklappen)
        if node.callees:
            self.tree_callstack.insert(item, "end", text="*dummy*")
        return item

    def on_cg_node_expand(self, event):
        """Lädt die Child-Nodes (Callees) lazy nach, wenn der Nutzer auf '+' klickt."""
        item = self.tree_callstack.focus()
        children = self.tree_callstack.get_children(item)
        if len(children) == 1 and self.tree_callstack.item(children[0], "text") == "*dummy*":
            self.tree_callstack.delete(children[0])
            tags = self.tree_callstack.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]

            node = self.app.cg.get_node(node_id)
            if node:
                for callee_id in node.callees:
                    self._insert_cg_node(item, callee_id)

    def on_callgraph_double_click(self, event):
        """Lädt die Methode in den Editor, wenn man im CallGraph doppelt darauf klickt."""
        sel = self.tree_callstack.selection()
        if not sel: return
        tags = self.tree_callstack.item(sel[0], "tags")
        node_id = tags[1] if "system_api" in tags else tags[0]

        if "system_api" in tags:
            return self.app.log(f"[!] {node_id.split('|')[0]} ist eine System-API und kann nicht geöffnet werden.")

        if "|" in node_id:
            filepath, sig = node_id.split("|", 1)
            self.load_method(filepath, method_signature=sig, add_as_root=False)

    def on_outline_double_click(self, event):
        sel = self.tree_outline.selection()
        if sel:
            tags = self.tree_outline.item(sel[0], "tags")
            if "method" in tags or "system_api" in tags:
                sig = tags[1] if len(tags) > 1 else tags[0]
                self.load_method(self.current_smali_file, method_signature=sig, add_as_root=True)

    def on_incoming_double_click(self, event):
        sel = self.tree_incoming.selection()
        if sel:
            tags = self.tree_incoming.item(sel[0], "tags")
            sig = tags[1] if "system_api" in tags else tags[0]
            self.load_method(self.tree_incoming.item(sel[0], "values")[0], method_signature=sig, add_as_root=True)

    def on_outgoing_double_click(self, event):
        sel = self.tree_outgoing.selection()
        if not sel: return
        target = self.tree_outgoing.item(sel[0], "values")[0]
        if ";->" in target:
            cls, meth = target.split(";->")
            for r, _, fs in os.walk(self.get_smali_dir()):
                if cls[1:] + ".smali" in fs:
                    self.load_method(os.path.relpath(os.path.join(r, cls[1:] + ".smali"), self.get_smali_dir()),
                                     method_signature=meth)
                    break

    def clear_callgraph(self):
        self.app.cg.clear()
        self.refresh_callgraph_ui()

    def save_callgraph(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path: self.app.cg.save(path)

    def load_callgraph(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path and self.app.cg.load(path): self.refresh_callgraph_ui()

    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.editor.get_orig_text()
        edit = self.editor.get_edit_text()
        if not f or not orig or not edit:
            return messagebox.showwarning("Fehlt", "Original oder Edit ist leer!")

        for p in self.smali_patches:
            if p["file"] == f and p["orig"] == orig:
                if not messagebox.askyesno("Patch existiert",
                                           "Möchtest du den vorhandenen Patch überschreiben?"): return
                self.smali_patches.remove(p)
                break

        self.smali_patches.append({"type": "smali", "file": f, "orig": orig, "edit": edit})
        self.refresh_smali_tree()
        self.editor.clear_edit()
        self.app.log(f"[+] Patch für {f} gespeichert.")

    def remove_smali_patch(self):
        sel = self.smali_tree.selection()
        if sel:
            del self.smali_patches[self.smali_tree.index(sel[0])]
            self.refresh_smali_tree()

    def refresh_smali_tree(self):
        for i in self.smali_tree.get_children(): self.smali_tree.delete(i)
        for p in self.smali_patches:
            self.smali_tree.insert("", "end", values=(p["file"], p["edit"][:60].replace("\n", " ") + "..."))

    def open_global_search(self):
        if not self._ensure_index_loaded():
            return

        # NEU: Singleton-Check verhindert mehrfaches Öffnen und Crashes!
        if hasattr(self, "search_window") and self.search_window.winfo_exists():
            self.search_window.lift()  # Holt das Fenster nach vorne
            self.search_window.focus_force()  # Gibt ihm den Fokus
            return

        top = tk.Toplevel(self)
        top.title("🔍 Globale RAM-Suche (Echtzeit)")
        top.geometry("900x550")
        top.attributes("-top", True)

        f_top = ttk.Frame(top)
        f_top.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Suchbegriff:").grid(row=0, column=0, sticky="w", pady=2)
        ent_search = ttk.Entry(f_top, width=50)
        ent_search.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ergebnisse filtern:").grid(row=1, column=0, sticky="w", pady=2)
        ent_filter = ttk.Entry(f_top, width=50)
        ent_filter.grid(row=1, column=1, padx=5, pady=2)

        lbl_status = ttk.Label(top, text=f"Bereit. Durchsuche {len(self.search_engine.ram_cache)} Dateien im RAM.")
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
            for r in filtered:
                tree.insert("", "end", values=r)

            if f_term and all_results:
                lbl_status.config(text=f"Filter aktiv: {len(filtered)} von {len(all_results)} Treffern.")
            elif all_results:
                lbl_status.config(text=f"{len(all_results)} Treffer geladen.")

        def clear_search():
            nonlocal all_results
            all_results = []
            for i in tree.get_children(): tree.delete(i)
            ent_search.delete(0, tk.END)
            ent_filter.delete(0, tk.END)
            lbl_status.config(text="Suche geleert.")

        def do_search(event=None):
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
                for rel_path, content in self.search_engine.ram_cache:
                    if len(results) >= 10000 or self.cancel_search_flag: break
                    if term in content:
                        for line_no, line in enumerate(content.splitlines(), 1):
                            if term in line:
                                results.append((rel_path, line_no, line.strip()))
                                if len(results) >= 10000: break
                self.app.after(0, lambda: finish_search(results))

            def finish_search(results):
                if self.cancel_search_flag:
                    lbl_status.config(text="Suche abgebrochen.")
                    return
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
            lines = [f"{tree.item(i, 'values')[0]}:{tree.item(i, 'values')[1]} - {tree.item(i, 'values')[2]}" for i in
                     selected]
            top.clipboard_clear()
            top.clipboard_append("\n".join(lines))
            return "break"

        btn_frame = ttk.Frame(f_top)
        btn_frame.grid(row=0, column=2, rowspan=2, padx=10, sticky="ns")

        ttk.Button(btn_frame, text="Suchen", command=do_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="🗑 Leeren", command=clear_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="❌ Abbrechen", command=lambda: setattr(self, 'cancel_search_flag', True)).pack(
            side="top", fill="x", pady=2)

        ent_search.bind("<Return>", do_search)
        ent_filter.bind("<KeyRelease>", apply_filter)
        tree.bind("<Double-1>", lambda e: self.load_method(tree.item(tree.selection()[0], "values")[0],
                                                           target_line=int(tree.item(tree.selection()[0], "values")[1]),
                                                           add_as_root=True) if tree.selection() else None)
        tree.bind("<Control-a>", lambda e: [tree.selection_set(tree.get_children()), "break"][1])
        tree.bind("<Control-c>", copy_search_results)