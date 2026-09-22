import os
from tkinter import messagebox, simpledialog
import threading
import subprocess
import sys

from ui.controllers.smali_cg_controller import SmaliCGController
from ui.dialogs.struct_dialog import CreateStructDialog
from ui.dialogs.global_search_dialog import SmaliGlobalSearchWindow

from services.smali_fs_service import SmaliStudioFSService
from services.smali_parser import SmaliStudioParser
from services.exploration_service import CallGraphExplorationService
from services.xref_service import XrefService

from core.application.session_state import SessionState
from core.application.event_bus import EventBus
from services.graphviz_service import GraphvizExportService


class SmaliStudioController:
    """Schlanker UI-Controller, der als Vermittler zwischen Views und zustandslosen Services dient."""

    def __init__(self, view, app):
        self.view = view
        self.app = app

        self.current_smali_file = ""
        self.current_method_name = ""
        self.editing_patch_idx = None
        self._cg_filter_timer = None

        # NEU: Baum-basierte Historie (Git-Branch Style)
        self.history_nodes = {}  # Speichert alle Nodes: node_id -> dict(...)
        self.history_root_id = None  # Der allererste Aufruf
        self.current_history_id = None  # Wo befinden wir uns gerade im Baum?
        self.history_counter = 0  # ID-Generator

        # Services initialisieren
        self.fs_service = SmaliStudioFSService(
            self.view.get_smali_dir,
            lambda: self.app.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        )
        self.exploration_service = CallGraphExplorationService(self.app.cg, self.fs_service)
        self.xref_service = XrefService(self.view.search_engine, self.app.cg)

        self.cg_controller = SmaliCGController(self.view.tree_callstack, self.app.cg)
        self.struct_manager = view.struct_manager
        self.search_engine = view.search_engine

        # Binde Modus-Umschalter an Controller (Ganze Datei vs. Nur Methode)
        if hasattr(self.view.editor, "var_view_mode"):
            self.view.editor.var_view_mode.trace_add("write", self._on_view_mode_changed)

        # EventBus Subscriptions für asynchrone Service-Updates
        EventBus.subscribe("CG_REFRESH_STABLE", lambda _: self.app.after(0, self.cg_controller.refresh_ui_stable))
        EventBus.subscribe("CG_FILTER_APPLY", lambda _: self.app.after(100,
                                                                       self.apply_cg_filter) if self.view.ent_cg_filter.get().strip() else None)
        EventBus.subscribe("XREF_SEARCH_STARTED", lambda _: self._prepare_xref_ui())
        EventBus.subscribe("XREF_SEARCH_FINISHED",
                           lambda data: self.app.after(0, lambda: self._render_xref_results(data)))

    def _on_view_mode_changed(self, *args):
        # Wenn der Klick aus der Historie kam, blockieren wir diesen Trigger
        if getattr(self, "_ignore_mode_change", False):
            return

        if self.current_smali_file:
            sig = self.current_method_name if self.current_method_name != "<Klassen-Header & Felder>" else None
            self.load_method(self.current_smali_file, method_signature=sig, add_as_root=False, add_to_history=False)

    def load_method(self, rel_filepath, target_line=None, method_signature=None, add_as_root=True, add_to_history=True):
        self.editing_patch_idx = None

        block, method_def, lines = self.fs_service.extract_method_block(rel_filepath, target_line, method_signature)
        if not lines:
            EventBus.publish("LOG_INFO", "[!] Konnte die Datei nicht laden.")
            return

        mode = self.view.editor.var_view_mode.get() if hasattr(self.view.editor, "var_view_mode") else "method"
        if mode == "file" and hasattr(self.fs_service, "extract_full_file"):
            display_block, _ = self.fs_service.extract_full_file(rel_filepath)
        else:
            display_block = block if block else "".join(lines)

        self.current_smali_file = rel_filepath.replace("\\", "/")
        if method_def != "<Klassen-Header & Felder>" and method_def:
            self.current_method_name = SmaliStudioParser.clean_signature(method_def)
        else:
            self.current_method_name = method_def or "<Klassen-Header & Felder>"

        # --- NEU: Tree-basiertes History Tracking ---
        if add_to_history:
            new_id = f"hist_{self.history_counter}"
            self.history_counter += 1

            node = {
                "id": new_id,
                "file": self.current_smali_file,
                "method": self.current_method_name,
                "parent": self.current_history_id,
                "children": []
            }

            # Verhindern, dass mehrfaches Klicken denselben Child-Knoten spammt
            is_duplicate = False
            if self.current_history_id and self.current_history_id in self.history_nodes:
                parent_node = self.history_nodes[self.current_history_id]
                if parent_node["children"]:
                    last_child_id = parent_node["children"][-1]
                    last_child = self.history_nodes[last_child_id]
                    if last_child["file"] == node["file"] and last_child["method"] == node["method"]:
                        self.current_history_id = last_child_id
                        self.history_counter -= 1
                        is_duplicate = True

            if not is_duplicate:
                self.history_nodes[new_id] = node
                if self.current_history_id and self.current_history_id in self.history_nodes:
                    self.history_nodes[self.current_history_id]["children"].append(new_id)
                elif not self.history_root_id:
                    self.history_root_id = new_id
                self.current_history_id = new_id

        self._refresh_history_tree()

        # UI Updates
        disp_name = self.current_method_name.split('(')[
            0] if "(" in self.current_method_name else self.current_method_name
        pkg_name = self.app.cfg.config.get("APP_PACKAGE", "app")
        unpacked_folder = self.view.get_unpacked_dir_name()
        os_rel_path = self.current_smali_file.replace("/", "\\")
        self.view.lbl_smali_file.config(text=f"{pkg_name}\\{unpacked_folder}\\{os_rel_path}->{disp_name}")

        self.view.editor.load_code(display_block)

        if self.current_method_name != "<Klassen-Header & Felder>":
            node_obj = self.app.cg.add_node(self.current_smali_file, self.current_method_name)
            if add_as_root and not self._is_reachable_in_cg(node_obj.id):
                self.app.cg.make_root(node_obj.id)
            if block:
                self._update_outgoing_calls(block)
                self._update_data_flow(block)
        else:
            for i in self.view.tree_outgoing.get_children(): self.view.tree_outgoing.delete(i)
            for i in self.view.tree_datagraph.get_children(): self.view.tree_datagraph.delete(i)

        self._update_outline(lines)
        self.cg_controller.refresh_ui()
        self.app.after(50, lambda: self.cg_controller.find_and_highlight(
            f"{self.current_smali_file}|{self.current_method_name}", highlight_only=True))
        if self.view.ent_cg_filter.get().strip():
            self.app.after(100, self.apply_cg_filter)

    # --- Baum-Navigation ---
    def load_history_node(self, node_id):
        node = self.history_nodes.get(node_id)
        if not node: return
        self.current_history_id = node_id
        sig = node["method"] if node["method"] != "<Klassen-Header & Felder>" else None
        self.load_method(node["file"], method_signature=sig, add_as_root=False, add_to_history=False)

    def navigate_back(self):
        if not self.current_history_id: return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["parent"]:
            self.load_history_node(node["parent"])

    def navigate_forward(self):
        if not self.current_history_id: return
        node = self.history_nodes.get(self.current_history_id)
        if node and node["children"]:
            # Springt bei mehreren Branches immer in den zuletzt erstellten Zweig
            self.load_history_node(node["children"][-1])

    def _build_tree(self, parent_iid, node_id):
        node = self.history_nodes[node_id]
        filename = node["file"].split('/')[-1]
        m_sig = node["method"]

        disp = m_sig.split('(')[0] if m_sig and '(' in m_sig else str(m_sig)
        if disp == "<Klassen-Header & Felder>" or str(m_sig) == "None":
            disp = "[Ganze Datei]"

        text = f"{filename} -> {disp}"
        tags = ("current",) if node_id == self.current_history_id else ("method",)

        # Werte übergeben wir an die UI: (Pfad, Signatur, Node_ID)
        self.view.tree_history.insert(parent_iid, "end", iid=node_id, text=text, values=(node["file"], m_sig, node_id),
                                      tags=tags, open=True)

        for child_id in node["children"]:
            self._build_tree(node_id, child_id)

    def _refresh_history_tree(self):
        tree = self.view.tree_history
        for i in tree.get_children(): tree.delete(i)

        if self.history_root_id:
            self._build_tree("", self.history_root_id)
            if self.current_history_id:
                tree.selection_set(self.current_history_id)
                tree.see(self.current_history_id)

    @property
    def smali_patches(self):
        return SessionState.active_smali_patches

    # --- Call Graph Live Filter ---
    def on_cg_filter_change(self):
        if self._cg_filter_timer:
            self.app.after_cancel(self._cg_filter_timer)
        self._cg_filter_timer = self.app.after(300, self.apply_cg_filter)

    def unpack_apk_async(self):
        if self.app.check_lock(): return
        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk")]
        if not apks:
            return messagebox.showwarning("Fehler", "Keine APKs im Source-Ordner gefunden!")

        self.view.progress_bar.pack(side="left", padx=5)
        self.view.lbl_progress_status.pack(side="left", padx=5)
        self.view.progress_bar.config(mode="indeterminate")
        self.view.progress_bar.start()
        self.view.lbl_progress_status.config(text="Bereite Workspace vor (Pipeline läuft)...")

        def task():
            self.app.is_unpacking = True
            success = self.app.engine.run_pipeline("PREPARE_WORKSPACE")
            self.app.is_unpacking = False
            self.app.after(0, self.view.progress_bar.stop)
            self.app.after(0, self.view.progress_bar.pack_forget)

            if success:
                self.app.after(0,
                               lambda: self.view.lbl_progress_status.config(text="Erfolgreich entpackt! Indexiere..."))
                self.struct_manager.smali_dir = self.view.get_smali_dir()
                dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), self.view.get_unpacked_dir_name())
                self.search_engine.build_ram_index(
                    self.view.get_smali_dir(), dest_cache, self.app.cfg.config.get("APP_PACKAGE", "app"),
                    lambda c: self.view.update_status(f"Bereit ({c} Dateien)")
                )
            else:
                self.app.after(0, lambda: self.view.lbl_progress_status.config(text="Fehler beim Vorbereiten!"))

        threading.Thread(target=task, daemon=True).start()

    def apply_cg_filter(self):
        term = self.view.ent_cg_filter.get().strip()
        if not hasattr(self, '_ram_dict') or len(self._ram_dict) != len(self.search_engine.ram_cache):
            self._ram_dict = {path.replace("\\", "/"): content for path, content in self.search_engine.ram_cache}
        self.cg_controller.apply_filter(term, self._ram_dict, self.view.lbl_cg_hits)

    def _is_reachable_in_cg(self, target_id):
        visited = set()
        queue = list(self.app.cg.roots)
        while queue:
            curr = queue.pop(0)
            if curr == target_id: return True
            if curr not in visited:
                visited.add(curr)
                n = self.app.cg.get_node(curr)
                if n: queue.extend(n.callees)
        return False

    def _update_outgoing_calls(self, block):
        for i in self.view.tree_outgoing.get_children(): self.view.tree_outgoing.delete(i)
        calls = SmaliStudioParser.parse_outgoing_calls(block)
        for c in calls:
            self.view.tree_outgoing.insert("", "end", values=(c["raw_call"],), tags=c["tags"])
            callee_path = self.fs_service.resolve_smali_path(c["class_part"][1:] + ".smali") or c["class_part"][1:]
            self.app.cg.add_edge(self.current_smali_file, self.current_method_name, callee_path, c["method_part"])

    def _update_data_flow(self, block):
        for i in self.view.tree_datagraph.get_children(): self.view.tree_datagraph.delete(i)
        self.view.tree_datagraph.tag_configure("string", foreground="#CE9178")
        data = SmaliStudioParser.parse_data_flow(block)
        for d in data:
            self.view.tree_datagraph.insert("", "end", values=(d["access"], d["target"]), tags=d["tags"])

    def _update_outline(self, lines):
        for i in self.view.tree_outline.get_children(): self.view.tree_outline.delete(i)
        for i in self.view.tree_file.get_children(): self.view.tree_file.delete(i)

        items = SmaliStudioParser.parse_outline(lines, self.current_smali_file)
        for item in items:
            self.view.tree_outline.insert("", "end", values=(item["type"], item["display"]), tags=item["tags"])
            self.view.tree_file.insert("", "end", values=(item["type"], item["display"]), tags=item["tags"])

    # --- XREF Delegation ---
    def find_incoming_xrefs(self):
        if not self.view._ensure_index_loaded() or not self.current_smali_file: return
        self.xref_service.find_incoming_xrefs(self.current_smali_file, self.current_method_name,
                                              self._is_reachable_in_cg)

    def _prepare_xref_ui(self):
        for i in self.view.tree_incoming.get_children(): self.view.tree_incoming.delete(i)
        self.view.tree_incoming.insert("", "end", values=("Suche läuft...", ""))

    def _render_xref_results(self, data):
        for i in self.view.tree_incoming.get_children(): self.view.tree_incoming.delete(i)

        results = data.get("results", [])
        if not results:
            self.view.tree_incoming.insert("", "end", values=("Keine Aufrufer gefunden.", ""))
            return

        for r in results:
            self.view.tree_incoming.insert("", "end", values=(r["norm_path"], r["clean_sig"].split('(')[0]),
                                           tags=r["tags"])

        self.cg_controller.refresh_ui()
        self.app.after(50,
                       lambda: self.cg_controller.find_and_highlight(data.get("current_node_id"), highlight_only=True))

    def load_custom_structure_into_editor(self, rel_filepath):
        # Bearbeitungs-Modus beim Laden einer eigenen Struktur zwingend verlassen
        self.editing_patch_idx = None

        filepath = os.path.join(self.fs_service.get_smali_dir(), rel_filepath)
        if not os.path.exists(filepath): return
        with open(filepath, "r", encoding="utf-8") as f:
            block = f.read()

        self.current_smali_file = rel_filepath.replace("\\", "/")
        self.current_method_name = "<Eigene Struktur>"
        self.view.lbl_smali_file.config(text=os.path.basename(self.current_smali_file))
        self.view.editor.load_code(block)
        for tree in [self.view.tree_outgoing, self.view.tree_datagraph, self.view.tree_outline]:
            for i in tree.get_children(): tree.delete(i)

    def add_new_file_patch(self, default_path, content):
        """Zeigt einen vorbelegten (topmost) Pfad-Dialog und legt einen new_file-Patch
        in die AKTIVE Patch-Liste (keine Datei auf der Platte). Gleicher Pfad -> ueberschreiben."""
        from ui.dialogs.new_file_patch_dialog import NewFilePatchDialog
        dlg = NewFilePatchDialog(self.view.winfo_toplevel(), default_path=default_path or "")
        rel = dlg.result_path
        if not rel:
            return False
        rel = rel.replace("\\", "/")
        if not rel.endswith(".smali"):
            rel += ".smali"
        for p in self.smali_patches:
            if p.get("scope") == "new_file" and p.get("file", "").replace("\\", "/") == rel:
                p["edit"] = content
                p["file"] = rel
                self.view.refresh_smali_tree()
                self.app.log(f"[+] Neue-Datei-Patch aktualisiert: {rel}")
                return True
        self.smali_patches.append({"type": "new_file", "scope": "new_file", "file": rel, "orig": "", "edit": content})
        self.view.refresh_smali_tree()
        self.app.log(f"[+] Neue-Datei-Patch hinzugefuegt: {rel}")
        return True

    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.view.editor.get_orig_text()
        edit = self.view.editor.get_edit_text()

        if self.current_method_name == "<Eigene Struktur>":
            self.struct_manager.save_existing_structure(f, edit)
            return

        # Bearbeiten eines bestehenden new_file-Patches (kein orig noetig)
        if (self.editing_patch_idx is not None
                and 0 <= self.editing_patch_idx < len(self.smali_patches)
                and self.smali_patches[self.editing_patch_idx].get("scope") == "new_file"):
            if not edit.strip():
                return messagebox.showwarning("Fehlt", "Inhalt (Edit) ist leer!")
            p = self.smali_patches[self.editing_patch_idx]
            p["edit"] = edit
            if f:
                p["file"] = f
            self.editing_patch_idx = None
            self.view.refresh_smali_tree()
            self.view.editor.clear_edit()
            return

        if not f or not orig or not edit:
            return messagebox.showwarning("Fehlt", "Original oder Edit ist leer!")

        # Scope basierend auf der Ansicht setzen
        scope = "file" if hasattr(self.view.editor, "var_view_mode") and self.view.editor.var_view_mode.get() == "file" else "method"

        if self.editing_patch_idx is not None:
            self.smali_patches[self.editing_patch_idx] = {"type": "smali", "scope": scope, "file": f, "orig": orig, "edit": edit}
            self.editing_patch_idx = None
        else:
            for p in self.smali_patches:
                if p["file"] == f and p["orig"] == orig:
                    if not messagebox.askyesno("Patch existiert",
                                               "Möchtest du den vorhandenen Patch überschreiben?"): return
                    self.smali_patches.remove(p)
                    break
            self.smali_patches.append({"type": "smali", "scope": scope, "file": f, "orig": orig, "edit": edit})

        self.view.refresh_smali_tree()
        self.view.editor.clear_edit()

    def remove_smali_patch(self, idx):
        del self.smali_patches[idx]

        # Index synchron halten oder Modus beenden, wenn der aktuell bearbeitete Patch gelöscht wird
        if self.editing_patch_idx == idx:
            self.editing_patch_idx = None
            self.current_method_name = ""
            self.view.lbl_smali_file.config(text="Patch gelöscht")
            self.view.editor.clear_edit()
        elif self.editing_patch_idx is not None and self.editing_patch_idx > idx:
            self.editing_patch_idx -= 1

        self.view.refresh_smali_tree()

    def open_global_search(self):
        if not self.view._ensure_index_loaded(): return
        if hasattr(self, "search_window") and self.search_window.winfo_exists():
            self.search_window.lift()
            self.search_window.focus_force()
            return
        root_window = self.view.winfo_toplevel()
        self.search_window = SmaliGlobalSearchWindow(root_window, self.search_engine.ram_cache, self.load_method)

    def open_create_struct_dialog(self):
        if not self.view._ensure_index_loaded(): return
        root_window = self.view.winfo_toplevel()
        CreateStructDialog(root_window, self)

    # --- Call Graph Exploration Delegation ---
    def start_auto_explore(self):
        selected = self.view.tree_callstack.selection()
        if not selected: return messagebox.showwarning("Fehlt", "Bitte wähle mindestens einen Startknoten aus!")

        start_nodes = []
        for item in selected:
            tags = self.view.tree_callstack.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]
            start_nodes.append(node_id)

        depth_str = simpledialog.askstring("Deep Explore",
                                           "Wie viele Ebenen in die Tiefe scannen?\n(Zahl eingeben oder 'max'):",
                                           initialvalue="3")
        if not depth_str: return
        max_depth = 9999 if depth_str.strip().lower() == "max" else int(depth_str.strip())

        self.exploration_service.start_auto_explore(start_nodes, max_depth)

    def stop_auto_explore(self):
        self.exploration_service.stop_auto_explore()

    def export_callgraph(self):
        if not self.app.cg.nodes:
            messagebox.showinfo("Leer", "Der Call Graph ist leer. Es gibt nichts zu exportieren!")
            return

        export_dir = self.app.cfg.paths.get("ARCHIVE_DIR", os.path.expanduser("~"))

        self.app.log("[*] Generiere Graphviz-Export...")
        result_path = GraphvizExportService.export_to_svg(self.app.cg, export_dir)

        if result_path:
            if result_path.endswith(".svg"):
                self.app.log(f"[+] Call Graph als SVG exportiert: {result_path}")
                messagebox.showinfo("Exportiert", f"Graph erfolgreich exportiert!\nWird nun im Browser geöffnet.")

                try:
                    import webbrowser
                    file_url = f"file://{os.path.abspath(result_path)}"
                    webbrowser.open(file_url)
                except Exception as e:
                    self.app.log(f"[!] Konnte SVG nicht automatisch öffnen: {e}")
            else:
                self.app.log(f"[!] SVG Generierung fehlgeschlagen. Rohe .dot Datei gespeichert: {result_path}")
                messagebox.showwarning(
                    "Graphviz fehlt",
                    "Die .dot Datei wurde generiert, konnte aber nicht in ein Bild konvertiert werden.\n\n"
                    "Bitte installiere 'Graphviz' für Windows (https://graphviz.org/download/) und achte darauf, "
                    "während der Installation das Häkchen bei 'Add Graphviz to the system PATH' zu setzen."
                )

    def clear_callgraph(self):
        self.app.cg.clear()
        self.cg_controller.refresh_ui()
        self.view.ent_cg_filter.delete(0, 'end')
        self.cg_controller.clear_filter()
        EventBus.publish("LOG_INFO", "[*] Call Graph vollständig geleert.")