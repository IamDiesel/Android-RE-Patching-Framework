import os
import tkinter as tk
from tkinter import ttk, messagebox

from ui.widgets.smali_editor_widget import SmaliEditorWidget
from services.smali_search_service import SmaliSearchEngine
from services.smali_struct_service import SmaliStructManager
from ui.utils import UIUtils
from ui.controllers.smali_studio_controller import SmaliStudioController
from core.application.event_bus import EventBus


class SmaliStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.search_engine = SmaliSearchEngine()
        EventBus.subscribe("INDEX_PROGRESS", self.update_status)

        self.struct_manager = SmaliStructManager(self.app, self.get_smali_dir(), self.search_engine,
                                                 self.refresh_custom_structures_list)

        self.create_widgets()
        self.controller = SmaliStudioController(self, app)

        UIUtils.apply_panedwindow_style()
        UIUtils.setup_global_shortcuts(self.winfo_toplevel())

    @property
    def smali_patches(self):
        return self.controller.smali_patches

    def get_unpacked_dir_name(self):
        strategy = self.app.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        return "base_unpacked_apkeditor" if strategy == "apkeditor" else "base_unpacked_apktool"

    def get_smali_dir(self):
        return os.path.join(self.app.cfg.paths.get("APP_SOURCE_DIR", ""), self.get_unpacked_dir_name())

    def update_status(self, msg):
        self.app.after(0, lambda: self.lbl_progress_status.config(text=msg))

    def _ensure_index_loaded(self):
        if self.search_engine.is_indexed: return True
        if self.search_engine.is_indexing:
            messagebox.showinfo("Warte", "RAM Index wird gerade aufgebaut.")
            return False

        source_smali = self.get_smali_dir()
        if os.path.exists(source_smali):
            dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), self.get_unpacked_dir_name())
            self.search_engine.build_ram_index(source_smali, dest_cache, self.app.cfg.config.get("APP_PACKAGE", "app"),
                                               lambda c: self.update_status(f"Bereit ({c} Dateien)"))
            messagebox.showinfo("Lade Index...", "Cache wird geladen. Versuche es gleich noch einmal.")
            return False

        messagebox.showwarning("Fehler", "Kein entpackter Code gefunden! Bitte zuerst 'APK Entpacken' klicken.")
        return False

    def create_widgets(self):
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", pady=5, padx=5)

        ttk.Button(top_bar, text="📦 APK Entpacken", command=lambda: self.controller.unpack_apk_async()).pack(
            side="left", padx=5)
        ttk.Button(top_bar, text="➕ Neue Struktur", command=lambda: self.controller.open_create_struct_dialog()).pack(
            side="left", padx=5)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(top_bar, variable=self.progress_var, maximum=100, length=150)
        self.lbl_progress_status = ttk.Label(top_bar, text="", foreground="gray")
        self.lbl_progress_status.pack(side="left", padx=5)

        ttk.Button(top_bar, text="🔍 Globale Suche", command=lambda: self.controller.open_global_search()).pack(
            side="left", padx=5)
        ttk.Button(top_bar, text="💾 Zur Patch-Liste", command=lambda: self.controller.add_smali_patch()).pack(
            side="right", padx=5)

        # Label für den vollen Pfad auf der rechten Seite (links neben dem Speichern-Button)
        self.lbl_smali_file = ttk.Label(top_bar, text="Keine Datei geladen", font=("Segoe UI", 9, "bold"), anchor="e")
        self.lbl_smali_file.pack(side="right", fill="x", expand=True, padx=10)

        # --- PANED WINDOW FÜR VERTIKALES RESIZING ---
        self.outer_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.outer_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        main_paned = ttk.PanedWindow(self.outer_paned, orient=tk.HORIZONTAL)
        self.outer_paned.add(main_paned, weight=3)

        # --- PATC-LISTE UNTEN IM PANED WINDOW ---
        f_patches = ttk.LabelFrame(self.outer_paned, text="Aktive Smali Patches (Warten auf Build)")
        self.outer_paned.add(f_patches, weight=1)

        self.smali_tree = ttk.Treeview(f_patches, columns=("File", "Snippet"), show="headings", height=4)
        self.smali_tree.heading("File", text="Datei")
        self.smali_tree.heading("Snippet", text="Edit Preview")
        self.smali_tree.column("File", width=400)

        s_y_patch = ttk.Scrollbar(f_patches, orient="vertical", command=self.smali_tree.yview)
        s_x_patch = ttk.Scrollbar(f_patches, orient="horizontal", command=self.smali_tree.xview)
        self.smali_tree.configure(yscrollcommand=s_y_patch.set, xscrollcommand=s_x_patch.set)
        s_y_patch.pack(side="right", fill="y")
        s_x_patch.pack(side="bottom", fill="x")
        self.smali_tree.pack(side="left", fill="both", expand=True)

        self.smali_tree.bind("<Delete>", lambda e: self.controller.remove_smali_patch(
            self.smali_tree.index(self.smali_tree.selection()[0])) if self.smali_tree.selection() else None)
        self.smali_tree.bind("<Double-1>", self.on_patch_double_click)

        # --- LINKES NOTEBOOK ---
        self.left_nb = ttk.Notebook(main_paned)
        main_paned.add(self.left_nb, weight=1)

        f_outline = ttk.Frame(self.left_nb)
        self.tree_outline = ttk.Treeview(f_outline, columns=("Type", "Name"), show="headings")
        self.tree_outline.heading("Type", text="Typ")
        self.tree_outline.heading("Name", text="Signatur")
        self.tree_outline.column("Type", width=40, stretch=False)
        s_y_out = ttk.Scrollbar(f_outline, orient="vertical", command=self.tree_outline.yview)
        s_x_out = ttk.Scrollbar(f_outline, orient="horizontal", command=self.tree_outline.xview)
        self.tree_outline.configure(yscrollcommand=s_y_out.set, xscrollcommand=s_x_out.set)
        s_y_out.pack(side="right", fill="y")
        s_x_out.pack(side="bottom", fill="x")
        self.tree_outline.pack(side="left", fill="both", expand=True)

        self.tree_outline.bind("<Double-1>", self.on_outline_double_click)
        self.tree_outline.tag_configure("system_api", foreground="gray")
        self.tree_outline.tag_configure("field", foreground="#007ACC")
        self.tree_outline.tag_configure("method", foreground="#A31515")
        self.left_nb.add(f_outline, text="Outline")

        # Callgraph
        f_callgraph = ttk.Frame(self.left_nb)
        cg_toolbar_top = ttk.Frame(f_callgraph)
        cg_toolbar_top.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Button(cg_toolbar_top, text="🔍 Explore", width=9,
                   command=lambda: self.controller.start_auto_explore()).pack(side="left", padx=1)
        ttk.Button(cg_toolbar_top, text="🛑 Stop", width=8, command=lambda: self.controller.stop_auto_explore()).pack(
            side="left", padx=1)
        ttk.Button(cg_toolbar_top, text="🗑 Clear", width=7, command=lambda: self.controller.clear_callgraph()).pack(
            side="right", padx=1)
        cg_toolbar_bottom = ttk.Frame(f_callgraph)
        cg_toolbar_bottom.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(cg_toolbar_bottom, text="Filter:").pack(side="left", padx=(2, 2))
        self.ent_cg_filter = ttk.Entry(cg_toolbar_bottom)
        self.ent_cg_filter.pack(side="left", fill="x", expand=True, padx=2)
        self.ent_cg_filter.bind("<KeyRelease>", lambda e: self.controller.on_cg_filter_change())
        ttk.Button(cg_toolbar_bottom, text="⬇", width=3, command=lambda: self.controller.cg_controller.next_hit()).pack(
            side="right", padx=1)
        ttk.Button(cg_toolbar_bottom, text="⬆", width=3, command=lambda: self.controller.cg_controller.prev_hit()).pack(
            side="right", padx=1)
        self.lbl_cg_hits = ttk.Label(cg_toolbar_bottom, text="0/0")
        self.lbl_cg_hits.pack(side="right", padx=2)

        cg_tree_frame = ttk.Frame(f_callgraph)
        cg_tree_frame.pack(fill="both", expand=True)
        self.tree_callstack = ttk.Treeview(cg_tree_frame, columns=("File",), show="tree headings")
        self.tree_callstack.heading("#0", text="Methode")
        self.tree_callstack.heading("File", text="Pfad")
        s_y_cg = ttk.Scrollbar(cg_tree_frame, orient="vertical", command=self.tree_callstack.yview)
        s_x_cg = ttk.Scrollbar(cg_tree_frame, orient="horizontal", command=self.tree_callstack.xview)
        self.tree_callstack.configure(yscrollcommand=s_y_cg.set, xscrollcommand=s_x_cg.set)
        s_y_cg.pack(side="right", fill="y")
        s_x_cg.pack(side="bottom", fill="x")
        self.tree_callstack.pack(side="left", fill="both", expand=True)

        self.tree_callstack.bind("<Double-1>", self.on_callgraph_double_click)
        self.tree_callstack.bind("<<TreeviewOpen>>", lambda e: self.controller.cg_controller.handle_node_expand(
            self.tree_callstack.focus()))
        self.tree_callstack.tag_configure("system_api", foreground="gray")
        self.tree_callstack.tag_configure("dimmed", foreground="#555555")
        self.tree_callstack.tag_configure("match_parent", foreground="#D7BA7D")
        self.tree_callstack.tag_configure("match_exact", foreground="#00FF00", background="#1a4d1a")
        self.left_nb.add(f_callgraph, text="Call Graph")

        # Datagraph
        f_datagraph = ttk.Frame(self.left_nb)
        self.tree_datagraph = ttk.Treeview(f_datagraph, columns=("Access", "Target"), show="headings")
        self.tree_datagraph.heading("Access", text="Zugriff")
        self.tree_datagraph.heading("Target", text="Variable / Feld")
        self.tree_datagraph.column("Access", width=60, stretch=False)
        s_y_dg = ttk.Scrollbar(f_datagraph, orient="vertical", command=self.tree_datagraph.yview)
        s_x_dg = ttk.Scrollbar(f_datagraph, orient="horizontal", command=self.tree_datagraph.xview)
        self.tree_datagraph.configure(yscrollcommand=s_y_dg.set, xscrollcommand=s_x_dg.set)
        s_y_dg.pack(side="right", fill="y")
        s_x_dg.pack(side="bottom", fill="x")
        self.tree_datagraph.pack(side="left", fill="both", expand=True)
        self.tree_datagraph.tag_configure("read", foreground="#6A9955")
        self.tree_datagraph.tag_configure("write", foreground="#D16969")
        self.left_nb.add(f_datagraph, text="Data Graph")

        # Eigene Strukturen
        f_custom_structs = ttk.Frame(self.left_nb)
        self.tree_custom_structs = ttk.Treeview(f_custom_structs, columns=("Path",), show="headings")
        self.tree_custom_structs.heading("Path", text="Erstellte Smali Dateien")
        s_y_cs = ttk.Scrollbar(f_custom_structs, orient="vertical", command=self.tree_custom_structs.yview)
        s_x_cs = ttk.Scrollbar(f_custom_structs, orient="horizontal", command=self.tree_custom_structs.xview)
        self.tree_custom_structs.configure(yscrollcommand=s_y_cs.set, xscrollcommand=s_x_cs.set)
        s_y_cs.pack(side="right", fill="y")
        s_x_cs.pack(side="bottom", fill="x")
        self.tree_custom_structs.pack(side="left", fill="both", expand=True)
        self.tree_custom_structs.bind("<Double-1>", lambda e: self.controller.load_custom_structure_into_editor(
            self.tree_custom_structs.item(self.tree_custom_structs.selection()[0], "values")[
                0]) if self.tree_custom_structs.selection() else None)
        self.left_nb.add(f_custom_structs, text="Eigene Strukturen")

        # --- EDITOR (MITTE) ---
        self.editor = SmaliEditorWidget(main_paned)
        self.editor.btn_find_cg.config(command=lambda: self.controller.cg_controller.find_and_highlight(
            f"{self.controller.current_smali_file}|{self.controller.current_method_name}", highlight_only=False))
        main_paned.add(self.editor, weight=3)
        self.setup_snippet_context_menu()

        # --- RECHTES NOTEBOOK ---
        self.right_nb = ttk.Notebook(main_paned)
        main_paned.add(self.right_nb, weight=1)

        # NEU: Reiter "Datei"
        f_file = ttk.Frame(self.right_nb)
        self.tree_file = ttk.Treeview(f_file, columns=("Type", "Name"), show="headings")
        self.tree_file.heading("Type", text="Typ")
        self.tree_file.heading("Name", text="Signatur")
        self.tree_file.column("Type", width=40, stretch=False)
        s_y_file = ttk.Scrollbar(f_file, orient="vertical", command=self.tree_file.yview)
        s_x_file = ttk.Scrollbar(f_file, orient="horizontal", command=self.tree_file.xview)
        self.tree_file.configure(yscrollcommand=s_y_file.set, xscrollcommand=s_x_file.set)
        s_y_file.pack(side="right", fill="y")
        s_x_file.pack(side="bottom", fill="x")
        self.tree_file.pack(side="left", fill="both", expand=True)
        self.tree_file.bind("<Double-1>", self.on_file_double_click)
        self.tree_file.tag_configure("system_api", foreground="gray")
        self.tree_file.tag_configure("field", foreground="#007ACC")
        self.tree_file.tag_configure("method", foreground="#A31515")
        self.right_nb.add(f_file, text="Datei")

        f_incoming = ttk.Frame(self.right_nb)
        ttk.Button(f_incoming, text="🔍 Finde Aufrufer", command=lambda: self.controller.find_incoming_xrefs()).pack(
            fill="x")
        self.tree_incoming = ttk.Treeview(f_incoming, columns=("File", "Method"), show="headings")
        self.tree_incoming.heading("File", text="Aufrufer-Datei")
        self.tree_incoming.heading("Method", text="Methode")
        s_y_inc = ttk.Scrollbar(f_incoming, orient="vertical", command=self.tree_incoming.yview)
        s_x_inc = ttk.Scrollbar(f_incoming, orient="horizontal", command=self.tree_incoming.xview)
        self.tree_incoming.configure(yscrollcommand=s_y_inc.set, xscrollcommand=s_x_inc.set)
        s_y_inc.pack(side="right", fill="y")
        s_x_inc.pack(side="bottom", fill="x")
        self.tree_incoming.pack(side="left", fill="both", expand=True)
        self.tree_incoming.bind("<Double-1>", self.on_incoming_double_click)
        self.tree_incoming.tag_configure("system_api", foreground="gray")
        self.right_nb.add(f_incoming, text="XREF (Incoming)")

        f_outgoing = ttk.Frame(self.right_nb)
        self.tree_outgoing = ttk.Treeview(f_outgoing, columns=("Target",), show="headings")
        self.tree_outgoing.heading("Target", text="Aufgerufene Methode")
        s_y_outg = ttk.Scrollbar(f_outgoing, orient="vertical", command=self.tree_outgoing.yview)
        s_x_outg = ttk.Scrollbar(f_outgoing, orient="horizontal", command=self.tree_outgoing.xview)
        self.tree_outgoing.configure(yscrollcommand=s_y_outg.set, xscrollcommand=s_x_outg.set)
        s_y_outg.pack(side="right", fill="y")
        s_x_outg.pack(side="bottom", fill="x")
        self.tree_outgoing.pack(side="left", fill="both", expand=True)
        self.tree_outgoing.bind("<Double-1>", self.on_outgoing_double_click)
        self.tree_outgoing.tag_configure("system_api", foreground="gray")
        self.right_nb.add(f_outgoing, text="Calls (Outgoing)")

    def on_outline_double_click(self, event):
        sel = self.tree_outline.selection()
        if sel:
            tags = self.tree_outline.item(sel[0], "tags")
            if "method" in tags or "system_api" in tags:
                sig = tags[1] if len(tags) > 1 else tags[0]
                self.controller.load_method(self.controller.current_smali_file, method_signature=sig, add_as_root=True)

    def on_file_double_click(self, event):
        sel = self.tree_file.selection()
        if sel:
            tags = self.tree_file.item(sel[0], "tags")
            # Ignoriere System-APIs
            if "system_api" in tags:
                return "break"
            if "method" in tags:
                sig = tags[1] if len(tags) > 1 else tags[0]
                self.controller.load_method(self.controller.current_smali_file, method_signature=sig, add_as_root=True)

    def on_callgraph_double_click(self, event):
        sel = self.tree_callstack.selection()
        if not sel: return "break"
        tags = self.tree_callstack.item(sel[0], "tags")
        node_id = tags[1] if "system_api" in tags else tags[0]
        if "system_api" not in tags and "|" in node_id:
            filepath, sig = node_id.split("|", 1)
            self.controller.load_method(filepath, method_signature=sig, add_as_root=False)
        return "break"

    def on_incoming_double_click(self, event):
        sel = self.tree_incoming.selection()
        if sel:
            tags = self.tree_incoming.item(sel[0], "tags")
            if "system_api" in tags: return "break"
            sig = tags[1] if len(tags) > 1 else tags[0]
            self.controller.load_method(self.tree_incoming.item(sel[0], "values")[0], method_signature=sig,
                                        add_as_root=True)

    def on_outgoing_double_click(self, event):
        sel = self.tree_outgoing.selection()
        if not sel: return
        tags = self.tree_outgoing.item(sel[0], "tags")
        if "system_api" in tags: return "break"
        target = self.tree_outgoing.item(sel[0], "values")[0]
        if ";->" in target:
            cls, meth = target.split(";->")
            callee_path = self.controller.fs_service.resolve_smali_path(cls[1:] + ".smali")
            if callee_path:
                self.controller.load_method(callee_path, method_signature=meth)

    def on_patch_double_click(self, event):
        sel = self.smali_tree.selection()
        if not sel: return
        idx = self.smali_tree.index(sel[0])
        patch = self.controller.smali_patches[idx]

        self.controller.editing_patch_idx = idx
        self.controller.current_smali_file = patch.get("file", "")
        self.controller.current_method_name = "<Patch-Bearbeitung>"

        self.lbl_smali_file.config(text=f"Patch: {os.path.basename(self.controller.current_smali_file)}")
        self.editor.txt_orig.config(state="normal")
        self.editor.txt_orig.delete("1.0", tk.END)
        self.editor.txt_orig.insert("1.0", patch.get("orig", ""))
        self.editor.txt_orig.config(state="disabled")

        self.editor.txt_edit.delete("1.0", tk.END)
        self.editor.txt_edit.insert("1.0", patch.get("edit", ""))
        if hasattr(self.editor, "rehighlight"): self.editor.rehighlight()

    def refresh_smali_tree(self):
        for i in self.smali_tree.get_children(): self.smali_tree.delete(i)
        for p in self.controller.smali_patches:
            self.smali_tree.insert("", "end", values=(p["file"], p["edit"][:60].replace("\n", " ") + "..."))

    def refresh_custom_structures_list(self):
        for i in self.tree_custom_structs.get_children(): self.tree_custom_structs.delete(i)
        for f in self.struct_manager.custom_files: self.tree_custom_structs.insert("", "end", values=(f,))

    def setup_snippet_context_menu(self):
        self.snippet_menu = tk.Menu(self, tearoff=0)
        for category, items in self.struct_manager.snippets.items():
            sub_menu = tk.Menu(self.snippet_menu, tearoff=0)
            self.snippet_menu.add_cascade(label=category, menu=sub_menu)
            for name, code in items.items():
                sub_menu.add_command(label=name, command=lambda c=code: self.insert_snippet_into_editor(c))

        self.editor.txt_edit.bind("<Button-3>", lambda e: self.snippet_menu.post(e.x_root, e.y_root))
        if hasattr(self.editor, "btn_snippet"):
            self.editor.btn_snippet.config(command=lambda: self.snippet_menu.post(self.editor.btn_snippet.winfo_rootx(),
                                                                                  self.editor.btn_snippet.winfo_rooty() + self.editor.btn_snippet.winfo_height()))

    def insert_snippet_into_editor(self, code_snippet):
        try:
            self.editor.txt_edit.insert(tk.INSERT, f"\n{code_snippet}\n")
            if hasattr(self.editor, "apply_highlighting"): self.editor.apply_highlighting(self.editor.txt_edit)
        except Exception as e:
            self.app.log(f"[!] Fehler beim Einfügen: {e}")