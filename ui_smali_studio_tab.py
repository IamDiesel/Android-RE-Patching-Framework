import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import re
import threading
import time

from cg_manager import is_system_api
from smali_editor import SmaliEditorWidget
from smali_search import SmaliSearchEngine
from smali_struct_manager import SmaliStructManager
from ui_utils import UIUtils


class SmaliStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.smali_patches = []
        self.current_smali_file = ""
        self.current_method_name = ""

        # Init Search Engine
        self.search_engine = SmaliSearchEngine(self.app.log, self.update_status)

        # Init Structure Manager für eigene Klassen
        self.struct_manager = SmaliStructManager(
            self.app,
            self.get_smali_dir(),
            self.search_engine,
            self.refresh_custom_structures_list
        )

        self.create_widgets()

        # UI-Verbesserungen und Shortcuts initialisieren
        UIUtils.apply_panedwindow_style()
        UIUtils.setup_global_shortcuts(self.winfo_toplevel())

    def get_unpacked_dir_name(self):
        """Ermittelt den dynamischen Ordnernamen basierend auf der Strategie."""
        strategy = self.app.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        return "base_unpacked_apkeditor" if strategy == "apkeditor" else "base_unpacked_apktool"

    def get_smali_dir(self):
        """Liefert das Read-Only Source-Verzeichnis der entpackten App."""
        app_source = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        return os.path.join(app_source, self.get_unpacked_dir_name())

    def update_status(self, msg):
        self.app.after(0, lambda: self.lbl_progress_status.config(text=msg))

    def _ensure_index_loaded(self):
        """Prüft ob der Index im RAM ist, und lädt ihn nach einem Neustart automatisch nach."""
        if self.search_engine.is_indexed:
            return True

        if self.search_engine.is_indexing:
            messagebox.showinfo("Warte", "RAM Index wird gerade aufgebaut. Bitte kurz warten.")
            return False

        source_smali = self.get_smali_dir()
        if os.path.exists(source_smali):
            pkg = self.app.cfg.config.get("APP_PACKAGE", "app")
            unpacked_name = self.get_unpacked_dir_name()
            dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), unpacked_name)

            self.search_engine.build_ram_index(
                source_smali,
                dest_cache,
                pkg,
                lambda c: self.update_status(f"Bereit ({c} Dateien)")
            )
            messagebox.showinfo("Lade Index...",
                                "Der gespeicherte Cache wird in den RAM geladen.\nBitte versuche es in 2-3 Sekunden nochmal!")
            return False

        messagebox.showwarning("Fehler",
                               "Kein entpackter Code gefunden!\n\nBitte klicke zuerst oben links auf '📦 APK Entpacken & Indexieren'.")
        return False

    def create_widgets(self):
        # -- 1. TOOLBAR GANZ OBEN --
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", pady=5, padx=5)

        ttk.Button(top_bar, text="📦 APK Entpacken & Indexieren", command=self.unpack_apk_async).pack(side="left",
                                                                                                     padx=5)

        # NEU: "+" Button für eigene Strukturen
        ttk.Button(top_bar, text="➕ Neue Struktur", command=self.open_create_struct_dialog).pack(side="left", padx=5)

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

        # NEU: Doppelklick auf angewendete Patches zum Editieren
        self.smali_tree.bind("<Double-1>", self.on_patch_double_click)

        # -- 3. MAIN IDE LAYOUT (Links, Mitte, Rechts) --
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # LINKES PANEL (Notebook: Outline, CallGraph, DataGraph, Eigene Strukturen)
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
        self.tree_datagraph.tag_configure("read", foreground="#6A9955")
        self.tree_datagraph.tag_configure("write", foreground="#D16969")
        self.left_nb.add(f_datagraph, text="Data Graph")

        # NEU: EGENE STRUKTUREN TAB
        f_custom_structs = ttk.Frame(self.left_nb)
        self.tree_custom_structs = ttk.Treeview(f_custom_structs, columns=("Path",), show="headings")
        self.tree_custom_structs.heading("Path", text="Erstellte Smali Dateien")
        self.tree_custom_structs.pack(fill="both", expand=True)
        self.tree_custom_structs.bind("<Double-1>", self.on_custom_struct_double_click)
        self.left_nb.add(f_custom_structs, text="Eigene Strukturen")

        # CENTER PANEL (Editor Component)
        self.editor = SmaliEditorWidget(main_paned)
        self.editor.btn_find_cg.config(command=lambda: self.find_current_in_callgraph(highlight_only=False))  # <--- NEU
        main_paned.add(self.editor, weight=3)

        # Baukasten-Kontextmenü für den Editor einrichten
        self.setup_snippet_context_menu()

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

    def unpack_apk_async(self):
        if self.app.check_lock(): return

        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk")]
        if not apks:
            return messagebox.showwarning("Fehler", "Keine APKs im Source-Ordner gefunden!")

        self.progress_bar.pack(side="left", padx=5)
        self.lbl_progress_status.pack(side="left", padx=5)
        self.progress_bar.config(mode="indeterminate")
        self.progress_bar.start()
        self.lbl_progress_status.config(text="Bereite Workspace vor (Pipeline läuft)...")

        def task():
            self.app.is_unpacking = True
            success = self.app.engine.run_pipeline("PREPARE_WORKSPACE")
            self.app.is_unpacking = False
            self.app.after(0, self.progress_bar.stop)
            self.app.after(0, self.progress_bar.pack_forget)

            if success:
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Erfolgreich entpackt! Indexiere..."))

                # Aktualisiere den Pfad im Struktur-Manager nach dem Entpacken
                self.struct_manager.smali_dir = self.get_smali_dir()

                pkg = self.app.cfg.config.get("APP_PACKAGE", "app")
                source_smali = self.get_smali_dir()
                dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), self.get_unpacked_dir_name())

                self.search_engine.build_ram_index(
                    source_smali, dest_cache, pkg,
                    lambda c: self.update_status(f"Bereit ({c} Dateien)")
                )
            else:
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Fehler beim Vorbereiten!"))

        threading.Thread(target=task, daemon=True).start()

    # --- NEU: DIALOG & LOGIK FÜR NEUE STRUKTUREN ---

    def open_create_struct_dialog(self):
        """Öffnet das Dialogfenster zum Erstellen neuer Klassen."""
        if not self.search_engine.is_indexed:
            return messagebox.showwarning("Index fehlt",
                                          "Bitte entpacke zuerst eine APK, um Strukturen anlegen zu können.")

        dialog = tk.Toplevel(self)
        dialog.title("➕ Neue Smali-Struktur anlegen")
        dialog.geometry("600x250")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        ttk.Label(dialog, text="Relativer Pfad (ab Workspace-Root):", font=("Segoe UI", 9, "bold")).pack(anchor="w",
                                                                                                         padx=10,
                                                                                                         pady=5)

        f_path = ttk.Frame(dialog)
        f_path.pack(fill="x", padx=10, pady=2)

        ent_path = ttk.Entry(f_path)
        ent_path.pack(side="left", fill="x", expand=True)
        # Vorschlagspfad generieren
        ent_path.insert(0, self.struct_manager.get_default_path(self.current_smali_file))

        def browse_path():
            # Öffnet den File-Manager im aktuellen Smali-Verzeichnis
            init_dir = self.get_smali_dir()
            chosen_file = filedialog.asksaveasfilename(
                initialdir=init_dir,
                title="Smali-Speicherort wählen",
                filetypes=[("Smali Files", "*.smali")],
                defaultextension=".smali"
            )
            if chosen_file:
                # Schneide den absoluten Pfadanteil des Workspace-Roots ab
                rel = os.path.relpath(chosen_file, init_dir).replace("\\", "/")
                ent_path.delete(0, tk.END)
                ent_path.insert(0, rel)

        ttk.Button(f_path, text="📁 Auswählen", command=browse_path).pack(side="right", padx=5)

        ttk.Label(dialog, text="Klassen-Typ Vorlage:", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=5)
        combo_type = ttk.Combobox(dialog, values=["Standard-Klasse", "BroadcastReceiver-Komponente"], state="readonly")
        combo_type.pack(fill="x", padx=10, pady=2)
        combo_type.current(0)

        def confirm():
            rel_p = ent_path.get().strip()
            if not rel_p: return

            # Dalvik Pfad aus Dateiname berechnen (z.B. smali/com/test/Class.smali -> Lcom/test/Class;)
            clean_p = rel_p.replace(".smali", "")
            parts = clean_p.split("/")
            # Wenn ein Root-Ordner wie smali_classes2 vorne steht, ignorieren wir ihn für den Klassennamen
            start_idx = 1 if parts[0].startswith("smali") else 0
            dalvik_classname = "L" + "/".join(parts[start_idx:]) + ";"

            if combo_type.get() == "BroadcastReceiver-Komponente":
                base_code = self.struct_manager.snippets.get("Android API (Intents/Context)", {}).get(
                    "BroadcastReceiver Klasse", "")
                base_code = base_code.replace("Lcom/example/MyBroadcastReceiver;", dalvik_classname)
            else:
                base_code = self.struct_manager.snippets.get("Struktur & Interfaces", {}).get("Neue Klasse (.class)",
                                                                                              "")
                base_code = base_code.replace("Lcom/example/MyClass;", dalvik_classname)

            # Physisch anlegen
            if self.struct_manager.create_new_structure(rel_p, base_code):
                dialog.destroy()
                # Direkt im Editor zum Bearbeiten laden
                self.load_custom_structure_into_editor(rel_p)

        ttk.Button(dialog, text="🚀 Struktur generieren", command=confirm).pack(pady=20)

    def refresh_custom_structures_list(self):
        """Aktualisiert die Treeview-Liste mit den eigenen Klassen."""
        for i in self.tree_custom_structs.get_children():
            self.tree_custom_structs.delete(i)
        for f in self.struct_manager.custom_files:
            self.tree_custom_structs.insert("", "end", values=(f,))

    def on_custom_struct_double_click(self, event):
        sel = self.tree_custom_structs.selection()
        if sel:
            rel_p = self.tree_custom_structs.item(sel[0], "values")[0]
            self.load_custom_structure_into_editor(rel_p)

    def load_custom_structure_into_editor(self, rel_filepath):
        """Lädt eine benutzerdefinierte Struktur komplett (ohne Methoden-Einschränkung) in den Editor."""
        filepath = os.path.join(self.get_smali_dir(), rel_filepath)
        if not os.path.exists(filepath): return

        with open(filepath, "r", encoding="utf-8") as f:
            block = f.read()

        self.current_smali_file = rel_filepath.replace("\\", "/")
        self.current_method_name = "<Eigene Struktur>"
        self.lbl_smali_file.config(text=os.path.basename(self.current_smali_file))

        self.editor.load_code(block)

        # Leere die XREF/Calls-Listen, da es sich um eine neue Datei handelt
        for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
        for i in self.tree_datagraph.get_children(): self.tree_datagraph.delete(i)
        for i in self.tree_outline.get_children(): self.tree_outline.delete(i)

        # --- BAUKASTEN CONTEXT MENÜ FÜR DEN EDITOR ---

    def setup_snippet_context_menu(self):
        """Erstellt das Kontextmenü für Code-Injections im Edit-Feld."""
        self.snippet_menu = tk.Menu(self, tearoff=0)

        for category, items in self.struct_manager.snippets.items():
            sub_menu = tk.Menu(self.snippet_menu, tearoff=0)
            self.snippet_menu.add_cascade(label=category, menu=sub_menu)

            for name, code in items.items():
                sub_menu.add_command(label=name, command=lambda c=code: self.insert_snippet_into_editor(c))

        # Rechtsklick im Textfeld anbinden
        self.editor.txt_edit.bind("<Button-3>", self.show_snippet_menu)

        # NEU: Den sichtbaren Button anbinden
        if hasattr(self.editor, "btn_snippet"):
            self.editor.btn_snippet.config(command=self.show_snippet_menu_btn)

    def show_snippet_menu(self, event):
        # Zeigt das Menü an der Mausposition (Rechtsklick)
        self.snippet_menu.post(event.x_root, event.y_root)

    def show_snippet_menu_btn(self):
        # Zeigt das Menü exakt unterhalb des Buttons an
        x = self.editor.btn_snippet.winfo_rootx()
        y = self.editor.btn_snippet.winfo_rooty() + self.editor.btn_snippet.winfo_height()
        self.snippet_menu.post(x, y)

    def insert_snippet_into_editor(self, code_snippet):
        try:
            self.editor.txt_edit.insert(tk.INSERT, f"\n{code_snippet}\n")
            if hasattr(self.editor, "apply_highlighting"):
                self.editor.apply_highlighting(self.editor.txt_edit)
        except Exception as e:
            self.app.log(f"[!] Fehler beim Einfügen des Snippets: {e}")

    def on_patch_double_click(self, event):
        """Lädt einen bereits existierenden Patch zurück in die IDE, um ihn zu bearbeiten."""
        sel = self.smali_tree.selection()
        if not sel: return

        idx = self.smali_tree.index(sel[0])
        patch = self.smali_patches[idx]

        # Speichere die ID/Index des aktuell editierten Patches im Tab-State
        self.editing_patch_idx = idx

        rel_file = patch.get("file", "")
        orig_code = patch.get("orig", "")
        edit_code = patch.get("edit", "")

        self.current_smali_file = rel_file
        self.current_method_name = "<Patch-Bearbeitung>"
        self.lbl_smali_file.config(text=f"Patch: {os.path.basename(rel_file)}")

        # FIX: Wir nutzen direkt die Textfelder und stellen sicher, dass BEIDE geleert werden.
        self.editor.txt_orig.config(state="normal")
        self.editor.txt_orig.delete("1.0", tk.END)
        self.editor.txt_orig.insert("1.0", orig_code)
        self.editor.txt_orig.config(state="disabled")

        self.editor.txt_edit.delete("1.0", tk.END)
        self.editor.txt_edit.insert("1.0", edit_code)

        if hasattr(self.editor, "rehighlight"):
            self.editor.rehighlight()



    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.editor.get_orig_text()
        edit = self.editor.get_edit_text()

        if self.current_method_name == "<Eigene Struktur>":
            self.struct_manager.save_existing_structure(f, edit)
            return

        if not f or not orig or not edit:
            return messagebox.showwarning("Fehlt", "Original oder Edit ist leer!")

        # Falls wir den Patch über den Doppelklick geladen haben, überschreiben wir ihn direkt am Index
        if hasattr(self, 'editing_patch_idx') and self.editing_patch_idx is not None:
            self.smali_patches[self.editing_patch_idx] = {"type": "smali", "file": f, "orig": orig, "edit": edit}
            self.editing_patch_idx = None  # State zurücksetzen
            self.app.log(f"[*] Existierender Patch für {f} aktualisiert.")
        else:
            # Normaler Fallback für neue Patches
            for p in self.smali_patches:
                if p["file"] == f and p["orig"] == orig:
                    if not messagebox.askyesno("Patch existiert",
                                               "Möchtest du den vorhandenen Patch überschreiben?"): return
                    self.smali_patches.remove(p)
                    break
            self.smali_patches.append({"type": "smali", "file": f, "orig": orig, "edit": edit})
            self.app.log(f"[+] Patch für {f} gespeichert.")

        self.refresh_smali_tree()
        self.editor.clear_edit()

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

            # Signatur hart normalisieren
            if not method_def.startswith("<"):
                clean_def = re.sub(
                    r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized |abstract |varargs |native |strictfp )*',
                    '', method_def)
                self.current_method_name = clean_def
            else:
                self.current_method_name = method_def

            disp_name = self.current_method_name.split('(')[
                0] if "(" in self.current_method_name else self.current_method_name
            self.lbl_smali_file.config(text=f"{os.path.basename(self.current_smali_file)} -> {disp_name}")

            self.editor.load_code(block)

            if not self.current_method_name.startswith("<"):
                # FIX: Hole den Knotenpunkt und prüfe, ob er schon Aufrufer hat!
                node = self.app.cg.add_node(self.current_smali_file, self.current_method_name)
                if add_as_root:
                    if not node.callers:
                        self.app.cg.make_root(node.id)

                self.parse_outgoing_calls(block)
                self.parse_data_flow(block)
            else:
                for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
                for i in self.tree_datagraph.get_children(): self.tree_datagraph.delete(i)

            self.update_outline(lines)
            self.refresh_callgraph_ui()
            self.app.after(50, lambda: self.find_current_in_callgraph(highlight_only=True))
        else:
            self.app.log("[!] Konnte den Block in der Datei nicht extrahieren.")

    def update_outline(self, lines):
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
            rel_base = cls_part[1:] + ".smali"
            found_path = self.resolve_smali_path(rel_base)
            callee_path = found_path if found_path else cls_part[1:]
            self.app.cg.add_edge(self.current_smali_file, self.current_method_name, callee_path, meth_part)

    def parse_data_flow(self, method_block):
        for i in self.tree_datagraph.get_children(): self.tree_datagraph.delete(i)

        # Tag für Strings konfigurieren (Farbton analog zum Editor-Syntax-Highlighting)
        self.tree_datagraph.tag_configure("string", foreground="#CE9178")

        # 1. State-Manipulation: Felder (SGET/SPUT/IGET/IPUT) tracken
        matches = re.findall(r'\b([is](?:get|put)(?:-[a-z]+)?)\s+[^,]+(?:,\s*[^,]+)?,\s*(L[^;]+;->[^\s]+)',
                             method_block)
        for instruction, target in list(dict.fromkeys(matches)):
            if "get" in instruction:
                self.tree_datagraph.insert("", "end", values=("READ", target), tags=("read", target))
            elif "put" in instruction:
                self.tree_datagraph.insert("", "end", values=("WRITE", target), tags=("write", target))

        # 2. Hardkodierte Strings tracken (const-string & const-string/jumbo)
        string_matches = re.findall(r'const-string(?:/jumbo)?\s+[vp]\d+,\s*"(.*?)"', method_block)
        for string_val in list(dict.fromkeys(string_matches)):
            # Überlange Strings (z.B. Base64 Blobs, Zertifikate) optisch kürzen
            display_str = string_val if len(string_val) < 80 else string_val[:77] + "..."
            self.tree_datagraph.insert("", "end", values=("STRING", f'"{display_str}"'), tags=("string", string_val))

    def resolve_smali_path(self, rel_base):
        smali_dir = self.get_smali_dir()
        pure_path_os = rel_base.replace("/", os.sep)
        target_tool = "apkeditor" if self.app.cfg.config.get("MANIFEST_STRATEGY",
                                                             "smali_only") == "apkeditor" else "apktool"
        possible_roots = []
        try:
            if target_tool == "apkeditor":
                base_smali = os.path.join(smali_dir, "smali")
                if os.path.exists(base_smali):
                    for d in os.listdir(base_smali):
                        if d.startswith("classes"): possible_roots.append(os.path.join("smali", d))
            else:
                if os.path.exists(smali_dir):
                    for d in os.listdir(smali_dir):
                        if d == "smali" or d.startswith("smali_classes"): possible_roots.append(d)
            for root in possible_roots:
                test_path = os.path.join(smali_dir, root, pure_path_os)
                if os.path.exists(test_path): return os.path.join(root, pure_path_os).replace("\\", "/")
        except:
            pass
        return None

    def find_incoming_xrefs(self):
        if not self._ensure_index_loaded() or not self.current_smali_file: return

        parts = self.current_smali_file.split("/")

        if parts[0] == "smali" and len(parts) > 1 and parts[1].startswith("classes"):
            pure_path = "/".join(parts[2:])
        elif parts[0].startswith("smali_classes"):
            pure_path = "/".join(parts[1:])
        elif parts[0] == "smali":
            pure_path = "/".join(parts[1:])
        else:
            pure_path = self.current_smali_file

        d_class = f"L{pure_path.replace('.smali', '')};"

        clean_def = re.sub(
            r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized |abstract |varargs |native |strictfp )*',
            '', self.current_method_name)

        # FIX: Die Parameter und der Rückgabetyp MÜSSEN im Suchbegriff bleiben!
        # Vorher wurde hier alles ab der Klammer '(' abgeschnitten. Das führte dazu, dass
        # das System bei Überladungen (wie <init>) alle Konstruktoren in einen Topf geworfen hat.
        search_term = f"{d_class}->{clean_def}"

        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        self.tree_incoming.insert("", "end", values=("Suche läuft...", ""))

        def on_results(results, cancelled):
            self.app.after(0, lambda: self._update_incoming_ui(results))

        self.search_engine.search_xrefs_incoming(search_term, on_results)

    def _update_incoming_ui(self, results):
        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        current_node_id = f"{self.current_smali_file}|{self.current_method_name}"

        # FIX: Wenn keine Aufrufer gefunden wurden, brechen wir ab, bevor die Methode aus dem Graphen gelöscht wird!
        if not results:
            self.tree_incoming.insert("", "end", values=("Keine Aufrufer gefunden.", ""))
            self.app.log("[*] Keine eingehenden Aufrufe (XREFs) für diese Methode im RAM gefunden.")
            return

        seen_callers = set()

        for r in results:
            norm_path = r[0].replace("\\", "/")

            clean_sig = re.sub(
                r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized |abstract |varargs |native |strictfp )*',
                '', r[1])

            caller_id = f"{norm_path}|{clean_sig}"
            if caller_id in seen_callers:
                continue
            seen_callers.add(caller_id)

            tags = ("system_api", clean_sig) if is_system_api("L" + norm_path) else (clean_sig,)
            self.tree_incoming.insert("", "end", values=(norm_path, clean_sig.split('(')[0]), tags=tags)

            caller, callee = self.app.cg.add_edge(norm_path, clean_sig, self.current_smali_file,
                                                  self.current_method_name)

            if not caller.callers:
                self.app.cg.make_root(caller.id)

        # Nur wenn es wirklich neue Eltern-Knoten gibt, darf die aktuelle Methode ihren Root-Status verlieren
        self.app.cg.remove_root(current_node_id)
        self.refresh_callgraph_ui()

        # UX-Plus: Nach der XREF-Suche den Baum aufklappen und die aktuelle Methode direkt wieder fokussieren
        self.app.after(50, lambda: self.find_current_in_callgraph(highlight_only=True))


    def refresh_callgraph_ui(self):
        # 1. State sichern (welche Pfade sind offen / selektiert?)
        open_paths = set()
        selected_paths = set()

        # Selektion sichern
        for sel_item in self.tree_callstack.selection():
            path = []
            curr = sel_item
            while curr:
                tags = self.tree_callstack.item(curr, "tags")
                if tags:
                    node_id = tags[1] if "system_api" in tags else tags[0]
                    path.insert(0, node_id)
                curr = self.tree_callstack.parent(curr)
            selected_paths.add(tuple(path))

        # Offene Knoten sichern
        def save_open_paths(item_id, current_path):
            tags = self.tree_callstack.item(item_id, "tags")
            if not tags: return
            node_id = tags[1] if "system_api" in tags else tags[0]
            new_path = current_path + (node_id,)

            if self.tree_callstack.item(item_id, "open"):
                open_paths.add(new_path)
                for child in self.tree_callstack.get_children(item_id):
                    save_open_paths(child, new_path)

        for child in self.tree_callstack.get_children():
            save_open_paths(child, ())

        # 2. Baum komplett leeren
        for i in self.tree_callstack.get_children():
            self.tree_callstack.delete(i)

        # 3. Wurzeln (Roots) neu einfügen
        for root_id in self.app.cg.roots:
            self._insert_cg_node("", root_id)

        # 4. State rekursiv wiederherstellen (inkl. Lazy-Loading Umgehung)
        def restore_state(item_id, current_path):
            tags = self.tree_callstack.item(item_id, "tags")
            if not tags: return
            node_id = tags[1] if "system_api" in tags else tags[0]
            new_path = current_path + (node_id,)

            # Selektion wiederherstellen
            if new_path in selected_paths:
                self.tree_callstack.selection_add(item_id)
                self.tree_callstack.see(item_id)

            # Aufklapp-Zustand wiederherstellen
            if new_path in open_paths:
                # Lazy-Loading manuell auflösen, da die echten Kinder noch nicht existieren
                children = self.tree_callstack.get_children(item_id)
                if len(children) == 1 and self.tree_callstack.item(children[0], "text") == "*dummy*":
                    self.tree_callstack.delete(children[0])
                    node = self.app.cg.get_node(node_id)
                    if node:
                        for callee_id in node.callees:
                            self._insert_cg_node(item_id, callee_id)

                # Item im UI aufklappen
                self.tree_callstack.item(item_id, open=True)

                # Kinder weiter prüfen
                for child in self.tree_callstack.get_children(item_id):
                    restore_state(child, new_path)

        for child in self.tree_callstack.get_children():
            restore_state(child, ())

    def _insert_cg_node(self, parent_item, node_id):
        node = self.app.cg.get_node(node_id)
        if not node: return None
        disp_text = node.signature.split('(')[0]
        tags = ["system_api"] if is_system_api("L" + node.filepath) else []
        tags.append(node_id)
        item = self.tree_callstack.insert(parent_item, "end", text=disp_text, values=(os.path.basename(node.filepath),),
                                          tags=tags)
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

    def on_callgraph_double_click(self, event):
        sel = self.tree_callstack.selection()
        if not sel: return "break"
        tags = self.tree_callstack.item(sel[0], "tags")
        node_id = tags[1] if "system_api" in tags else tags[0]
        if "system_api" in tags:
            self.app.log(f"[!] {node_id.split('|')[0]} ist eine System-API.")
            return "break"
        if "|" in node_id:
            filepath, sig = node_id.split("|", 1)
            self.load_method(filepath, method_signature=sig, add_as_root=False)

        # NEU: Verhindert das automatische Ein-/Ausklappen von Tkinter!
        return "break"

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
        if not self._ensure_index_loaded(): return
        if hasattr(self, "search_window") and self.search_window.winfo_exists():
            self.search_window.lift()
            self.search_window.focus_force()
            return

        top = tk.Toplevel(self.app)
        self.search_window = top
        top.title("🔍 Globale RAM-Suche (Echtzeit)")
        top.geometry("900x580")
        top.attributes("-topmost", True)

        f_top = ttk.Frame(top)
        f_top.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Suchbegriff:").grid(row=0, column=0, sticky="w", pady=2)
        ent_search = ttk.Entry(f_top, width=50)
        ent_search.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ergebnisse filtern (AND, per Leerzeichen):").grid(row=1, column=0, sticky="w", pady=2)
        ent_filter = ttk.Entry(f_top, width=50)
        ent_filter.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ausschließen (kommasepariert, Pfad o. Code):").grid(row=2, column=0, sticky="w", pady=2)
        ent_exclude = ttk.Entry(f_top, width=50)
        ent_exclude.grid(row=2, column=1, padx=5, pady=2)

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

            f_terms = [t.strip().lower() for t in ent_filter.get().split() if t.strip()]

            filtered = []
            for r in all_results:
                target_str = (r[0] + " " + r[2]).lower()

                match = True
                for ft in f_terms:
                    if ft not in target_str:
                        match = False
                        break

                if match:
                    filtered.append(r)

            for r in filtered: tree.insert("", "end", values=r)
            if f_terms and all_results:
                lbl_status.config(text=f"Filter aktiv: {len(filtered)} von {len(all_results)} Treffern.")
            elif all_results:
                lbl_status.config(text=f"{len(all_results)} Treffer geladen.")

        def clear_search():
            nonlocal all_results
            all_results = []
            for i in tree.get_children(): tree.delete(i)
            ent_search.delete(0, tk.END)
            ent_filter.delete(0, tk.END)
            ent_exclude.delete(0, tk.END)
            lbl_status.config(text="Suche geleert.")

        def do_search(event=None):
            for i in tree.get_children(): tree.delete(i)
            term = ent_search.get()
            if not term: return

            lbl_status.config(text="Suche läuft im RAM...")
            # FIX: Diese Zeile wurde entfernt, damit dein Filter erhalten bleibt!
            # ent_filter.delete(0, tk.END)

            self.cancel_search_flag = False
            top.update()
            start_time = time.time()

            ex_terms = [t.strip().lower() for t in ent_exclude.get().split(",") if t.strip()]

            def search_thread():
                results = []
                for rel_path, content in self.search_engine.ram_cache:
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
                self.app.after(0, lambda: finish_search(results))

            def finish_search(results):
                if self.cancel_search_flag:
                    lbl_status.config(text="Suche abgebrochen.")
                    return
                nonlocal all_results
                all_results = results
                elapsed = time.time() - start_time
                apply_filter()  # Filtert die neuen Ergebnisse sofort wieder durch!
                msg = f"{len(results)} Treffer in {elapsed:.3f} Sekunden."
                if len(results) >= 10000: msg += " (UI-Limit 10.000 erreicht!)"
                lbl_status.config(text=msg)

            threading.Thread(target=search_thread, daemon=True).start()

        btn_frame = ttk.Frame(f_top)
        btn_frame.grid(row=0, column=2, rowspan=3, padx=10, sticky="ns")

        ttk.Button(btn_frame, text="Suchen", command=do_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="🗑 Leeren", command=clear_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="❌ Abbrechen", command=lambda: setattr(self, 'cancel_search_flag', True)).pack(
            side="top", fill="x", pady=2)

        ent_search.bind("<Return>", do_search)
        ent_exclude.bind("<Return>", do_search)
        ent_filter.bind("<KeyRelease>", apply_filter)
        tree.bind("<Double-1>", lambda e: self.load_method(tree.item(tree.selection()[0], "values")[0],
                                                           target_line=int(tree.item(tree.selection()[0], "values")[1]),
                                                           add_as_root=True) if tree.selection() else None)

    def find_current_in_callgraph(self, highlight_only=False):
        if not self.current_smali_file: return
        target_id = f"{self.current_smali_file}|{self.current_method_name}"

        found_items = []

        # Rekursive Suche durch ALLE sichtbaren Tree-Nodes
        def search_tree(item):
            tags = self.tree_callstack.item(item, "tags")
            if tags:
                node_id = tags[1] if "system_api" in tags else tags[0]
                if node_id == target_id:
                    found_items.append(item)  # Treffer sammeln statt abbrechen!

            for child in self.tree_callstack.get_children(item):
                search_tree(child)

        for root_item in self.tree_callstack.get_children(""):
            search_tree(root_item)

        if found_items:
            # 1. Alle Elternknoten für JEDEN gefundenen Treffer aufklappen
            for item in found_items:
                curr = self.tree_callstack.parent(item)
                while curr:
                    self.tree_callstack.item(curr, open=True)
                    curr = self.tree_callstack.parent(curr)

            # 2. Alle Treffer gleichzeitig markieren
            self.tree_callstack.selection_set(found_items)

            # 3. Zum ersten Treffer scrollen, damit das Fenster nicht wild springt
            self.tree_callstack.see(found_items[0])
        else:
            if not highlight_only:
                self.app.log("[*] Aktuelle Methode ist (noch) nicht im Call Graph verknüpft/sichtbar.")