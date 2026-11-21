# -*- coding: utf-8 -*-
"""ui.tabs.lib_carve_tab — Workspace-Subtab 🔪 LibCarve (C / ASM / ELF Decompiler & Patch Studio)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from core.application.session_state import SessionState
from ui.controllers.lib_carve_controller import LibCarveController
from ui.widgets.libcarve_editor_widget import LibCarveEditorWidget


class LibCarveTab(ttk.Frame):
    """Vollausgestattetes Patch- & Decompile-Studio fuer bestehende .so-Bibliotheken (Smali-Studio-Paritaet)."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._lib_map = {}  # display_label -> {path, abi, name}
        self.controller = LibCarveController(self, app)
        self._create_widgets()
        self.refresh_discovered_libs()
        self.controller.refresh_caves_list()
        self.refresh_patch_tree()

    @property
    def libcarve_patches(self):
        return SessionState.active_libcarve_patches

    def _create_widgets(self):
        # ---- Obere Werkzeugleiste ----
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=5, pady=4)

        ttk.Label(top, text="Lib:").pack(side="left", padx=(2, 2))
        self.var_lib = tk.StringVar(value="")
        self.cmb_lib = ttk.Combobox(top, textvariable=self.var_lib, width=28, state="readonly")
        self.cmb_lib.pack(side="left", padx=2)
        self.cmb_lib.bind("<<ComboboxSelected>>", self._on_lib_selected)

        ttk.Button(top, text="🔄", width=3, command=self.refresh_discovered_libs).pack(side="left", padx=1)
        ttk.Button(top, text="📁 .so öffnen", command=self.controller.open_external_so).pack(side="left", padx=2)
        ttk.Button(top, text="🔍 Globale Suche", command=self.controller.open_global_search).pack(side="left", padx=4)

        nav = ttk.Frame(top)
        nav.pack(side="left", padx=4)
        ttk.Button(nav, text="◀", width=3, command=self.controller.navigate_back).pack(side="left", padx=1)
        ttk.Button(nav, text="▶", width=3, command=self.controller.navigate_forward).pack(side="left", padx=1)

        ttk.Button(top, text="✏️ Name/Notiz", command=self.controller.rename_current_symbol).pack(side="left", padx=2)
        ttk.Button(top, text="🐞 Debugger", command=self.controller.open_in_debugger).pack(side="left", padx=2)
        ttk.Button(top, text="🔴 BP", command=self.controller.set_breakpoint_in_debugger).pack(side="left", padx=2)
        ttk.Button(top, text="▶️ Live testen", command=self.controller.run_live_in_simulator).pack(side="left", padx=3)

        self.lbl_status = ttk.Label(top, text="Keine .so geladen", foreground="gray")
        self.lbl_status.pack(side="left", padx=6)

        ttk.Button(top, text="\u2753 Hilfe", command=self._show_libcarve_help_dialog).pack(side="right", padx=3)
        ttk.Button(top, text="⚙️ Lib kompilieren", command=self.controller.compile_active_lib).pack(side="right", padx=3)
        ttk.Button(top, text="💾 Zur Patch-Liste", command=self.controller.add_libcarve_patch).pack(side="right", padx=3)
        mb_fav = ttk.Menubutton(top, text="⭐ Favoriten ▾")
        fav_menu = tk.Menu(mb_fav, tearoff=0)
        fav_menu.add_command(label="⭐ Aktuellen Patch als neuen Favoriten speichern",
                             command=self.controller.save_current_as_favorite)
        fav_menu.add_command(label="➕ Aktuellen Patch zu bestehendem Favoriten hinzufügen...",
                             command=self.controller.append_current_to_favorite)
        fav_menu.add_command(label="📦 Aktive Workspace-Patches als Kombi-Favorit sichern",
                             command=self.controller.save_workspace_bundle_as_favorite)
        fav_menu.add_separator()
        fav_menu.add_command(label="🗂 Favoriten-Manager öffnen (Filter: LibCarve)",
                             command=self.controller.open_favorites_manager)
        mb_fav.config(menu=fav_menu)
        mb_fav.pack(side="right", padx=3)

        self.lbl_file = ttk.Label(top, text="", font=("Segoe UI", 9, "bold"), anchor="e")
        self.lbl_file.pack(side="right", fill="x", expand=True, padx=6)

        # ---- Haupt-PanedWindow (Oben Studio, Unten Patch-Liste) ----
        self.outer_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.outer_paned.pack(side="top", fill="both", expand=True, padx=5, pady=4)

        main_paned = ttk.PanedWindow(self.outer_paned, orient=tk.HORIZONTAL)
        self.outer_paned.add(main_paned, weight=3)

        # ---- Linkes Notebook (5 Reiter analog Smali Studio) ----
        self.left_nb = ttk.Notebook(main_paned)
        main_paned.add(self.left_nb, weight=1)

        # 1. Outline
        f_out = ttk.Frame(self.left_nb)
        f_out_flt = ttk.Frame(f_out)
        f_out_flt.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(f_out_flt, text="Filter:").pack(side="left")
        self.ent_outline_filter = ttk.Entry(f_out_flt)
        self.ent_outline_filter.pack(side="left", fill="x", expand=True, padx=3)
        self.ent_outline_filter.bind("<KeyRelease>", lambda _e: self.controller.refresh_outline(self.ent_outline_filter.get()))

        out_box = ttk.Frame(f_out)
        out_box.pack(fill="both", expand=True)
        self.tree_outline = ttk.Treeview(out_box, columns=("Type", "Name", "Addr"), show="headings")
        self.tree_outline.heading("Type", text="Typ")
        self.tree_outline.heading("Name", text="Symbol / Funktion")
        self.tree_outline.heading("Addr", text="VAddr")
        self.tree_outline.column("Type", width=52, stretch=False)
        self.tree_outline.column("Addr", width=95, stretch=False)
        sy_o = ttk.Scrollbar(out_box, orient="vertical", command=self.tree_outline.yview)
        sx_o = ttk.Scrollbar(out_box, orient="horizontal", command=self.tree_outline.xview)
        self.tree_outline.configure(yscrollcommand=sy_o.set, xscrollcommand=sx_o.set)
        sy_o.pack(side="right", fill="y")
        sx_o.pack(side="bottom", fill="x")
        self.tree_outline.pack(side="left", fill="both", expand=True)
        self.tree_outline.tag_configure("jni", foreground="#9C27B0")
        self.tree_outline.tag_configure("export", foreground="#A31515")
        self.tree_outline.tag_configure("sub", foreground="#005A9E")
        self.tree_outline.tag_configure("system_api", foreground="gray")
        self.tree_outline.bind("<Double-1>", self._on_outline_dbl)
        self.left_nb.add(f_out, text="Outline")

        # 2. Call Graph
        f_cg = ttk.Frame(self.left_nb)
        cg_top = ttk.Frame(f_cg)
        cg_top.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Button(cg_top, text="🔍 Explore", width=9, command=self.controller.start_auto_explore).pack(side="left", padx=1)
        ttk.Button(cg_top, text="🛑 Stop", width=8, command=self.controller.stop_auto_explore).pack(side="left", padx=1)
        ttk.Button(cg_top, text="🕸️ Export", width=9, command=self.controller.export_callgraph).pack(side="left", padx=1)
        ttk.Button(cg_top, text="🗑 Clear", width=7, command=self.controller.clear_callgraph).pack(side="right", padx=1)

        cg_bot = ttk.Frame(f_cg)
        cg_bot.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Label(cg_bot, text="Filter:").pack(side="left", padx=2)
        self.ent_cg_filter = ttk.Entry(cg_bot)
        self.ent_cg_filter.pack(side="left", fill="x", expand=True, padx=2)
        self.ent_cg_filter.bind("<KeyRelease>", lambda _e: self.controller.apply_cg_filter())
        ttk.Button(cg_bot, text="⬇", width=3, command=self.controller.next_cg_hit).pack(side="right", padx=1)
        ttk.Button(cg_bot, text="⬆", width=3, command=self.controller.prev_cg_hit).pack(side="right", padx=1)
        self.lbl_cg_hits = ttk.Label(cg_bot, text="0/0")
        self.lbl_cg_hits.pack(side="right", padx=2)

        cg_box = ttk.Frame(f_cg)
        cg_box.pack(fill="both", expand=True)
        self.tree_callstack = ttk.Treeview(cg_box, columns=("Addr",), show="tree headings")
        self.tree_callstack.heading("#0", text="Funktion")
        self.tree_callstack.heading("Addr", text="VAddr")
        self.tree_callstack.column("Addr", width=95, stretch=False)
        sy_c = ttk.Scrollbar(cg_box, orient="vertical", command=self.tree_callstack.yview)
        self.tree_callstack.configure(yscrollcommand=sy_c.set)
        sy_c.pack(side="right", fill="y")
        self.tree_callstack.pack(side="left", fill="both", expand=True)
        self.tree_callstack.tag_configure("dimmed", foreground="#777777")
        self.tree_callstack.tag_configure("match_exact", foreground="#00FF00", background="#1a4d1a")
        self.tree_callstack.bind("<Double-1>", self._on_callgraph_dbl)
        self.left_nb.add(f_cg, text="Call Graph")

        # 3. Data Graph
        f_dg = ttk.Frame(self.left_nb)
        self.tree_datagraph = ttk.Treeview(f_dg, columns=("Access", "Target"), show="headings")
        self.tree_datagraph.heading("Access", text="Zugriff")
        self.tree_datagraph.heading("Target", text="Adresse / String / Symbol")
        self.tree_datagraph.column("Access", width=95, stretch=False)
        sy_dg = ttk.Scrollbar(f_dg, orient="vertical", command=self.tree_datagraph.yview)
        self.tree_datagraph.configure(yscrollcommand=sy_dg.set)
        sy_dg.pack(side="right", fill="y")
        self.tree_datagraph.pack(side="left", fill="both", expand=True)
        self.tree_datagraph.tag_configure("read", foreground="#2E7D32")
        self.tree_datagraph.tag_configure("write", foreground="#C62828")
        self.tree_datagraph.bind("<Double-1>", self._on_datagraph_dbl)
        self.left_nb.add(f_dg, text="Data Graph")

        # 4. Eigene Caves
        f_cv = ttk.Frame(self.left_nb)
        cv_bar = ttk.Frame(f_cv)
        cv_bar.pack(side="top", fill="x", padx=2, pady=2)
        ttk.Button(cv_bar, text="➕ Neue Cave-Vorlage", command=self.controller.create_new_cave_dialog).pack(side="left", padx=2)
        self.tree_caves = ttk.Treeview(f_cv, columns=("Name", "Lib", "VAddr"), show="headings")
        self.tree_caves.heading("Name", text="Name")
        self.tree_caves.heading("Lib", text="Lib")
        self.tree_caves.heading("VAddr", text="VAddr")
        self.tree_caves.pack(fill="both", expand=True)
        self.tree_caves.bind("<Double-1>", self._on_cave_dbl)
        self.left_nb.add(f_cv, text="Eigene Caves")

        # 5. Historie
        f_hist = ttk.Frame(self.left_nb)
        self.tree_history = ttk.Treeview(f_hist, columns=("Lib", "Addr", "Id"), show="tree", displaycolumns=())
        sy_h = ttk.Scrollbar(f_hist, orient="vertical", command=self.tree_history.yview)
        self.tree_history.configure(yscrollcommand=sy_h.set)
        sy_h.pack(side="right", fill="y")
        self.tree_history.pack(side="left", fill="both", expand=True)
        self.tree_history.tag_configure("method", foreground="#242424")
        self.tree_history.tag_configure("current", background="#FFF2CC", foreground="#000000")
        self.tree_history.bind("<Double-1>", self._on_history_dbl)
        self.left_nb.add(f_hist, text="Historie")

        # ---- Mitte: LibCarveEditorWidget ----
        self.editor = LibCarveEditorWidget(
            main_paned,
            on_budget_check=self.controller.check_editor_budget,
        )
        self.editor.btn_find_cg.config(command=self.controller.start_auto_explore)
        self.editor.btn_ghidra.config(command=self.controller.decompile_current_with_ghidra)
        main_paned.add(self.editor, weight=3)
        self._setup_snippet_menu()

        # ---- Rechtes Notebook (3 Reiter analog Smali Studio) ----
        self.right_nb = ttk.Notebook(main_paned)
        main_paned.add(self.right_nb, weight=1)

        f_file = ttk.Frame(self.right_nb)
        f_file_bar = ttk.Frame(f_file)
        f_file_bar.pack(side="top", fill="x")
        ttk.Button(f_file_bar, text="☕ Zugehörige Smali-Klasse öffnen", command=self.controller.jump_to_jni_smali).pack(fill="x", padx=2, pady=2)
        self.tree_file = ttk.Treeview(f_file, columns=("Type", "Name", "Val"), show="headings")
        self.tree_file.heading("Type", text="Typ")
        self.tree_file.heading("Name", text="Eigenschaft / Sektion")
        self.tree_file.heading("Val", text="Wert / VAddr")
        self.tree_file.column("Type", width=55, stretch=False)
        self.tree_file.pack(fill="both", expand=True)
        self.tree_file.bind("<Double-1>", self._on_file_dbl)
        self.right_nb.add(f_file, text="Bibliothek")

        f_inc = ttk.Frame(self.right_nb)
        ttk.Button(f_inc, text="🔍 Finde Aufrufer & XREFs", command=self.controller.find_incoming_xrefs).pack(fill="x")
        self.tree_incoming = ttk.Treeview(f_inc, columns=("Site", "Caller"), show="headings")
        self.tree_incoming.heading("Site", text="Stelle")
        self.tree_incoming.heading("Caller", text="Aufrufer / Typ")
        self.tree_incoming.column("Site", width=95, stretch=False)
        self.tree_incoming.pack(fill="both", expand=True)
        self.tree_incoming.bind("<Double-1>", self._on_xref_dbl)
        self.right_nb.add(f_inc, text="XREF (Incoming)")

        f_outg = ttk.Frame(self.right_nb)
        self.tree_outgoing = ttk.Treeview(f_outg, columns=("Site", "Target"), show="headings")
        self.tree_outgoing.heading("Site", text="Stelle")
        self.tree_outgoing.heading("Target", text="Aufgerufenes Ziel")
        self.tree_outgoing.column("Site", width=95, stretch=False)
        self.tree_outgoing.pack(fill="both", expand=True)
        self.tree_outgoing.bind("<Double-1>", self._on_xref_dbl)
        self.right_nb.add(f_outg, text="Calls (Outgoing)")

        # ---- Unten: Aktive LibCarve Patches ----
        f_p = ttk.LabelFrame(self.outer_paned, text="Aktive LibCarve Patches (Live im Simulator & Bereit für Build)")
        self.outer_paned.add(f_p, weight=1)
        self.patch_tree = ttk.Treeview(f_p, columns=("Lib", "Func", "Mode", "Preview"), show="headings", height=4)
        self.patch_tree.heading("Lib", text="Bibliothek [ABI]")
        self.patch_tree.heading("Func", text="Funktion @ VAddr")
        self.patch_tree.heading("Mode", text="Modus")
        self.patch_tree.heading("Preview", text="Patch-Vorschau")
        self.patch_tree.column("Lib", width=210, stretch=False)
        self.patch_tree.column("Func", width=250, stretch=False)
        self.patch_tree.column("Mode", width=90, stretch=False)
        sy_p = ttk.Scrollbar(f_p, orient="vertical", command=self.patch_tree.yview)
        self.patch_tree.configure(yscrollcommand=sy_p.set)
        sy_p.pack(side="right", fill="y")
        self.patch_tree.pack(side="left", fill="both", expand=True)
        self.patch_tree.bind("<Double-1>", self._on_patch_dbl)
        self.patch_tree.bind("<Delete>", self._on_patch_delete)

    def _setup_snippet_menu(self):
        self.snippet_menu = tk.Menu(self, tearoff=0)
        for cat, items in self.controller.snippets.items():
            sub = tk.Menu(self.snippet_menu, tearoff=0)
            self.snippet_menu.add_cascade(label=cat, menu=sub)
            for nm, code in items.items():
                mode = "c_cave" if "C" in cat else "asm"
                sub.add_command(
                    label=nm,
                    command=lambda c=code, m=mode: self._insert_snippet(c, m),
                )
        self.editor.btn_snippet.config(
            command=lambda: self.snippet_menu.post(
                self.editor.btn_snippet.winfo_rootx(),
                self.editor.btn_snippet.winfo_rooty() + self.editor.btn_snippet.winfo_height(),
            )
        )
        self.editor.txt_edit.bind("<Button-3>", lambda e: self.snippet_menu.post(e.x_root, e.y_root))

    def _insert_snippet(self, code: str, mode: str):
        self.editor.var_patch_mode.set(mode)
        self.editor.set_edit_text(code)

    # ---- Hilfsmethoden & Tree-Events ---------------------------------------
    def refresh_discovered_libs(self):
        items = self.controller.discover_libraries()
        self._lib_map = {it["label"]: it for it in items}
        vals = [it["label"] for it in items]
        self.cmb_lib.configure(values=vals)
        if vals and not self.var_lib.get():
            self.var_lib.set(vals[0])

    def _on_lib_selected(self, _e=None):
        info = self._lib_map.get(self.var_lib.get())
        if info:
            self.controller.load_so_path(info["path"], abi=info.get("abi", "arm64-v8a"))

    def set_status(self, text: str, color: str = "gray"):
        try:
            self.lbl_status.config(text=text, foreground=color)
        except Exception:
            pass

    def set_file_label(self, text: str):
        try:
            self.lbl_file.config(text=text)
        except Exception:
            pass

    def clear_views(self):
        for tr in (self.tree_outline, self.tree_callstack, self.tree_datagraph, self.tree_file, self.tree_incoming, self.tree_outgoing, self.tree_history):
            for i in tr.get_children():
                tr.delete(i)
        self.editor.load_function_views("", "", "", clear_edit=True)
        self.set_status("Keine .so geladen", "gray")
        self.set_file_label("")

    def refresh_patch_tree(self):
        for i in self.patch_tree.get_children():
            self.patch_tree.delete(i)
        for p in SessionState.active_libcarve_patches:
            lib = f"{p.get('file', '?')} [{p.get('abi', 'arm64-v8a')}]"
            fn = f"{p.get('func', '?')} @ {p.get('vaddr', '?')}"
            md = p.get("mode", "asm")
            prev = str(p.get("edit", "")).replace("\n", " ")[:80]
            self.patch_tree.insert("", "end", values=(lib, fn, md, prev))

    def _on_outline_dbl(self, _e=None):
        sel = self.tree_outline.selection()
        if not sel:
            return
        tags = self.tree_outline.item(sel[0], "tags")
        for t in tags:
            if str(t).startswith("0x"):
                self.controller.navigate_to(int(t, 16), add_to_history=True)
                break

    def _on_callgraph_dbl(self, _e=None):
        sel = self.tree_callstack.selection()
        if not sel:
            return
        meta = self.controller._cg_nodes.get(sel[0])
        if meta:
            self.controller.navigate_to(meta["addr"], add_to_history=True)

    def _on_datagraph_dbl(self, _e=None):
        sel = self.tree_datagraph.selection()
        if not sel:
            return
        for t in self.tree_datagraph.item(sel[0], "tags"):
            if str(t).startswith("0x"):
                a = int(t, 16)
                if self.controller.index:
                    self.controller.navigate_to(a, add_to_history=True)
                break

    def _on_cave_dbl(self, _e=None):
        sel = self.tree_caves.selection()
        if sel:
            self.controller.load_cave_into_editor(sel[0])

    def _on_history_dbl(self, _e=None):
        sel = self.tree_history.selection()
        if not sel:
            return
        vals = self.tree_history.item(sel[0], "values")
        if vals and len(vals) >= 3:
            self.controller.load_history_node(vals[2])

    def _on_file_dbl(self, _e=None):
        sel = self.tree_file.selection()
        if not sel:
            return
        for t in self.tree_file.item(sel[0], "tags"):
            if str(t).startswith("0x"):
                self.controller.navigate_to(int(t, 16), add_to_history=True)
                break

    def _on_xref_dbl(self, event=None):
        tr = event.widget if event else self.tree_incoming
        sel = tr.selection()
        if not sel:
            return
        for t in tr.item(sel[0], "tags"):
            if str(t).startswith("0x"):
                self.controller.navigate_to(int(t, 16), add_to_history=True)
                break

    def _on_patch_dbl(self, _e=None):
        sel = self.patch_tree.selection()
        if not sel:
            return
        idx = self.patch_tree.index(sel[0])
        p = SessionState.active_libcarve_patches[idx]
        self.controller.editing_patch_idx = idx
        self.editor.var_patch_mode.set(p.get("mode", "asm"))
        self.editor.load_function_views(p.get("orig", ""), p.get("orig", ""), "", clear_edit=False)
        self.editor.set_edit_text(p.get("edit", ""), mark_dirty=False)
        self.set_file_label(f"Patch-Bearbeitung: {p.get('file')} @ {p.get('vaddr')}")

    def _on_patch_delete(self, _e=None):
        sel = self.patch_tree.selection()
        if sel:
            idx = self.patch_tree.index(sel[0])
            self.controller.remove_libcarve_patch(idx)

    def _show_libcarve_help_dialog(self):
        """Erklaert Funktionen, Patch-Modi, AOB-Matching, Live-Test & KI-Sync fuer LibCarve."""
        win = tk.Toplevel(self.winfo_toplevel())
        win.title("LibCarve-Hilfe: Decompiler, ASM/C-Cave Recompiler & AOB")
        win.geometry("700x540")
        txt = tk.Text(
            win, wrap="word", font=("Segoe UI", 9), padx=12, pady=10,
            bg="#f8fafc", fg="#0f172a", relief="flat",
        )
        vs = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("h1", font=("Segoe UI", 11, "bold"), foreground="#1d4ed8", spacing1=8, spacing3=3)
        txt.tag_configure("b", font=("Segoe UI", 9, "bold"), foreground="#0f172a")

        sections = [
            ("1. Bibliotheken laden & Analyse (RAM-Index)", [
                ("Lib-Auswahl / 📁 .so öffnen", "Entdeckt .so-Dateien automatisch aus der entpackten APK sowie aus data/vfs_selfcache/ (selbst-entpackte Bibliotheken) oder öffnet beliebige ELF64/AArch64-Dateien."),
                ("Linkes Notebook (Outline / Graphs / Caves / Historie)", "Zeigt benannte Exports/JNI-Funktionen und synthetische stripped sub_-Funktionen, Call-Hierarchy, Daten-/String-Referenzen, wiederverwendbare Caves sowie Branch-Historie (◀ / ▶)."),
                ("Rechtes Notebook (Bibliothek / XREF / Calls)", "Prüft ELF-Segmente & 16-KB-Page-Alignment, eingehende Code-/Reloc-XREFs, ausgehende Aufrufe und bietet direkten Sprung zu Java-JNI-Gegenstücken im Smali Studio."),
                ("🔍 Globale Suche", "Durchsucht den RAM-Index nach Symbolen, ASCII-Strings, ASM/Kanten und Wildcard-Hex-AOB-Mustern (z. B. 'FD 7B ?? A9')."),
            ]),
            ("2. Editor, Patch-Modi & Recompiler", [
                ("Original-Ansichten & Ghidra-C", "Oben stehen 'Split ASM+C', 'ASM', 'Decompiled C' (Ghidra mit Live-Streaming & Cache) und 'Hex' bereit."),
                ("ARM64 ASM (In-Place + Auto-NOP / Cave)", "Kurze Patches innerhalb der Originalfunktion werden direkt eingepasst und automatisch mit NOPs aufgefüllt. Größere Blöcke erhalten automatisch ein 16-KB-ausgerichtetes Cave-Segment samt Branch-Trampolin."),
                ("C-Cave (NDK clang -fPIC -nostdlib)", "Kompiliert C-Funktionen direkt als positionsunabhängige Code-Cave, injiziert ein neues PF_R|PF_X PT_LOAD-Segment (16-KB-aligned) und verlinkt den Hook-Einsprung automatisch."),
                ("Hex", "Direkte Byte-/Opcode-Manipulation an der gewählten virtuellen Adresse."),
            ]),
            ("3. Favoriten & Versions-Pattern-Matching (Wildcard-AOB)", [
                ("⭐ Favorit & AOB-Signatur", "Speichert zu jedem Patch eine relokations-tolerante AOB-Signatur ('??' über PC-relative Offsets wie ADRP/BL/LDR) sowie Anker-Strings."),
                ("Übertragen auf neue App-Versionen", "Greift eine Adresse nicht mehr exakt, sucht LibCarve über Symbol-Heuristik, Wildcard-AOB und Anker-Strings; bei Mehrdeutigkeiten öffnet sich der NativeFuzzyMatchDialog."),
            ]),
            ("4. Zero-Build Live-Test, Debugger & Build", [
                ("▶️ Live testen", "Startet den gläsernen Debugger/Simulator sofort im Stand 'Live-Patches' (Zone 'live'): aktive LibCarve-Patches laufen per VFS-2.0-Overlay mit Priorität 1 vor dem self_cache — ganz ohne APK-Rebuild."),
                ("🐞 Debugger / 🔴 BP", "Öffnet die aktuelle Adresse direkt im Disassembly-Tab des Debuggers bzw. setzt einen nativen Breakpoint."),
                ("⚙️ Lib kompilieren & Pipeline", "Schreibt die physisch gepatchte .so nach data/libcarve/ oder wird über Step 'libcarve_patch' in BUILD_NATIVE / BUILD_FLUTTER automatisch in die signierte APK gebaut."),
            ]),
            ("5. 🤖 KI-Sync & MCP-Steuerung (Gruppe N: libcarve.*)", [
                ("⬆ An KI / ⬇ Von KI", "Synchronisiert LibCarve-Fokus und Patch-Entwürfe mit dem KI-Assistenten (data/ki_exchange/workspace_sync.json) — mit is_dirty-Überschreibschutz für ungesicherte Editor-Änderungen."),
                ("MCP-Parität", "Alle Read-, Preview-, Patch-, Favoriten-, Decompile- und Compile-Funktionen stehen als libcarve.*-Tools in der KI-Steuerung bereit."),
            ]),
        ]
        for heading, items in sections:
            txt.insert("end", heading + "\n", "h1")
            for k, desc in items:
                txt.insert("end", "• " + k + ": ", "b")
                txt.insert("end", desc + "\n\n")
        txt.configure(state="disabled")
