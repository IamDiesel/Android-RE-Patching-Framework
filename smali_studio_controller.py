import os
import threading
import time
from tkinter import messagebox

from cg_manager import is_system_api
from smali_studio_parser import SmaliStudioParser
from smali_studio_fs_service import SmaliStudioFSService
from smali_studio_cg_controller import SmaliCGController
from smali_studio_global_search import SmaliGlobalSearchWindow
from ui_struct_dialog import CreateStructDialog


class SmaliStudioController:
    """Orchestriert die Logik zwischen UI, Dateisystem, Parser und Backend-Engines."""

    def __init__(self, view, app):
        self.view = view
        self.app = app

        self.smali_patches = []
        self.current_smali_file = ""
        self.current_method_name = ""
        self.editing_patch_idx = None
        self._cg_filter_timer = None

        self.fs_service = SmaliStudioFSService(self.view.get_smali_dir,
                                               lambda: self.app.cfg.config.get("MANIFEST_STRATEGY", "smali_only"))
        self.cg_controller = SmaliCGController(self.view.tree_callstack, self.app.cg, self.app.log)
        self.struct_manager = view.struct_manager
        self.search_engine = view.search_engine

    # --- Call Graph Live Filter (Debounce & Trigger) ---
    def on_cg_filter_change(self):
        """Wird bei jedem Tastendruck aufgerufen, wartet aber 300ms (Debouncing)."""
        if self._cg_filter_timer:
            self.app.after_cancel(self._cg_filter_timer)
        self._cg_filter_timer = self.app.after(300, self.apply_cg_filter)

    def apply_cg_filter(self):
        term = self.view.ent_cg_filter.get().strip()

        # Einmalig das schnelle Dictionary aus dem RAM aufbauen, falls noch nicht vorhanden
        if not hasattr(self, '_ram_dict') or len(self._ram_dict) != len(self.search_engine.ram_cache):
            self._ram_dict = {path.replace("\\", "/"): content for path, content in self.search_engine.ram_cache}

        self.cg_controller.apply_filter(term, self._ram_dict, self.view.lbl_cg_hits)

    # --- Standard Loading ---
    def load_method(self, rel_filepath, target_line=None, method_signature=None, add_as_root=True):
        block, method_def, lines = self.fs_service.extract_method_block(rel_filepath, target_line, method_signature)
        if not block:
            self.app.log("[!] Konnte den Block in der Datei nicht extrahieren.")
            return

        self.current_smali_file = rel_filepath.replace("\\", "/")
        if method_def != "<Klassen-Header & Felder>":
            self.current_method_name = SmaliStudioParser.clean_signature(method_def)
        else:
            self.current_method_name = method_def

        disp_name = self.current_method_name.split('(')[
            0] if "(" in self.current_method_name else self.current_method_name
        self.view.lbl_smali_file.config(text=f"{os.path.basename(self.current_smali_file)} -> {disp_name}")
        self.view.editor.load_code(block)

        if self.current_method_name != "<Klassen-Header & Felder>":
            node = self.app.cg.add_node(self.current_smali_file, self.current_method_name)
            if add_as_root:
                if not self._is_reachable_in_cg(node.id):
                    self.app.cg.make_root(node.id)

            self._update_outgoing_calls(block)
            self._update_data_flow(block)
        else:
            for i in self.view.tree_outgoing.get_children(): self.view.tree_outgoing.delete(i)
            for i in self.view.tree_datagraph.get_children(): self.view.tree_datagraph.delete(i)

        self._update_outline(lines)
        self.cg_controller.refresh_ui()
        self.app.after(50, lambda: self.cg_controller.find_and_highlight(
            f"{self.current_smali_file}|{self.current_method_name}", highlight_only=True))

        # WICHTIG: Wenn eine neue Methode geladen wird, Filter sofort wieder anwenden!
        if self.view.ent_cg_filter.get().strip():
            self.app.after(100, self.apply_cg_filter)

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
        items = SmaliStudioParser.parse_outline(lines, self.current_smali_file)
        for item in items:
            self.view.tree_outline.insert("", "end", values=(item["type"], item["display"]), tags=item["tags"])

    def find_incoming_xrefs(self):
        if not self.view._ensure_index_loaded() or not self.current_smali_file: return
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
        search_term = f"{d_class}->{self.current_method_name}"

        for i in self.view.tree_incoming.get_children(): self.view.tree_incoming.delete(i)
        self.view.tree_incoming.insert("", "end", values=("Suche läuft...", ""))

        def on_results(results, cancelled):
            self.app.after(0, lambda: self._process_xref_results(results))

        self.search_engine.search_xrefs_incoming(search_term, on_results)

    def _process_xref_results(self, results):
        for i in self.view.tree_incoming.get_children(): self.view.tree_incoming.delete(i)
        current_node_id = f"{self.current_smali_file}|{self.current_method_name}"

        if not results:
            self.view.tree_incoming.insert("", "end", values=("Keine Aufrufer gefunden.", ""))
            self.app.log("[*] Keine eingehenden Aufrufe (XREFs) für diese Methode im RAM gefunden.")
            return

        seen_callers = set()
        for r in results:
            norm_path = r[0].replace("\\", "/")
            clean_sig = SmaliStudioParser.clean_signature(r[1])
            caller_id = f"{norm_path}|{clean_sig}"

            if caller_id in seen_callers: continue
            seen_callers.add(caller_id)

            tags = ("system_api", clean_sig) if is_system_api("L" + norm_path) else (clean_sig,)
            self.view.tree_incoming.insert("", "end", values=(norm_path, clean_sig.split('(')[0]), tags=tags)
            caller, _ = self.app.cg.add_edge(norm_path, clean_sig, self.current_smali_file, self.current_method_name)

            if not self._is_reachable_in_cg(caller.id):
                self.app.cg.make_root(caller.id)

        self.app.cg.remove_root(current_node_id)
        self.cg_controller.refresh_ui()
        self.app.after(50, lambda: self.cg_controller.find_and_highlight(current_node_id, highlight_only=True))

    def load_custom_structure_into_editor(self, rel_filepath):
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

    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.view.editor.get_orig_text()
        edit = self.view.editor.get_edit_text()

        if self.current_method_name == "<Eigene Struktur>":
            self.struct_manager.save_existing_structure(f, edit)
            return

        if not f or not orig or not edit: return messagebox.showwarning("Fehlt", "Original oder Edit ist leer!")

        if self.editing_patch_idx is not None:
            self.smali_patches[self.editing_patch_idx] = {"type": "smali", "file": f, "orig": orig, "edit": edit}
            self.editing_patch_idx = None
        else:
            for p in self.smali_patches:
                if p["file"] == f and p["orig"] == orig:
                    if not messagebox.askyesno("Patch existiert",
                                               "Möchtest du den vorhandenen Patch überschreiben?"): return
                    self.smali_patches.remove(p)
                    break
            self.smali_patches.append({"type": "smali", "file": f, "orig": orig, "edit": edit})

        self.view.refresh_smali_tree()
        self.view.editor.clear_edit()

    def remove_smali_patch(self, idx):
        del self.smali_patches[idx]
        self.view.refresh_smali_tree()

    def open_global_search(self):
        if not self.view._ensure_index_loaded(): return
        if hasattr(self, "search_window") and self.search_window.winfo_exists():
            self.search_window.lift()
            self.search_window.focus_force()
            return
        self.search_window = SmaliGlobalSearchWindow(self.view, self.search_engine.ram_cache, self.load_method)

    def open_create_struct_dialog(self):
        if not self.view._ensure_index_loaded(): return
        CreateStructDialog(self.view, self)

    def unpack_apk_async(self):
        if self.app.check_lock(): return
        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk")]
        if not apks: return messagebox.showwarning("Fehler", "Keine APKs im Source-Ordner gefunden!")

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
                self.struct_manager.smali_dir = self.fs_service.get_smali_dir()
                dest_cache = os.path.join(self.app.cfg.paths.get("DEST_DIR", ""), self.view.get_unpacked_dir_name())
                self.search_engine.build_ram_index(
                    self.fs_service.get_smali_dir(), dest_cache, self.app.cfg.config.get("APP_PACKAGE", "app"),
                    lambda c: self.view.update_status(f"Bereit ({c} Dateien)")
                )
            else:
                self.app.after(0, lambda: self.view.lbl_progress_status.config(text="Fehler beim Vorbereiten!"))

        threading.Thread(target=task, daemon=True).start()

    # --- Call Graph Exploration ---
    def start_auto_explore(self):
        from tkinter import simpledialog
        selected = self.view.tree_callstack.selection()
        if not selected: return messagebox.showwarning("Fehlt", "Bitte wähle mindestens einen Startknoten aus!")

        start_nodes = []
        for item in selected:
            tags = self.view.tree_callstack.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]
            start_nodes.append(node_id)

        depth_str = simpledialog.askstring("Deep Explore",
                                           "Wie viele Ebenen in die Tiefe scannen?\n(Zahl eingeben oder 'max' für kompletten Ast):",
                                           initialvalue="3")
        if not depth_str: return

        if depth_str.strip().lower() == "max":
            max_depth = 9999
        else:
            try:
                max_depth = int(depth_str.strip())
            except ValueError:
                return messagebox.showerror("Fehler", "Ungültige Eingabe.")

        self.app.log(f"[*] Starte automatische Exploration für {len(start_nodes)} Knoten (Tiefe: {max_depth})...")
        self.explore_flag = True
        threading.Thread(target=self._exploration_thread, args=(start_nodes, max_depth), daemon=True).start()

    def stop_auto_explore(self):
        if getattr(self, 'explore_flag', False):
            self.explore_flag = False
            self.app.log("[*] Exploration wird gestoppt...")

    def _exploration_thread(self, start_nodes, max_depth):
        visited = set()
        queue = [(nid, 0) for nid in start_nodes]
        last_ui_update = time.time()
        processed_count = 0

        while queue and self.explore_flag:
            curr_id, current_depth = queue.pop(0)

            if current_depth >= max_depth: continue
            if curr_id in visited: continue
            visited.add(curr_id)

            filepath, sig = curr_id.split("|", 1)
            if is_system_api("L" + filepath): continue

            block, _, _ = self.fs_service.extract_method_block(filepath, method_signature=sig)
            if block:
                processed_count += 1
                calls = SmaliStudioParser.parse_outgoing_calls(block)
                for c in calls:
                    callee_path = self.fs_service.resolve_smali_path(c["class_part"][1:] + ".smali") or c["class_part"][
                                                                                                        1:]
                    self.app.cg.add_edge(filepath, sig, callee_path, c["method_part"])
                    queue.append((f"{callee_path}|{c['method_part']}", current_depth + 1))

            if time.time() - last_ui_update > 0.5:
                self.app.after(0, self.cg_controller.refresh_ui_stable)
                last_ui_update = time.time()

            if processed_count > 5000:
                self.app.log("[!] Sicherheitslimit von 5000 analysierten Methoden erreicht. Stoppe.")
                break

        self.app.after(0, self.cg_controller.refresh_ui_stable)

        # NACH der Exploration: Filter automatisch anwenden, falls etwas in der Maske steht!
        if self.view.ent_cg_filter.get().strip():
            self.app.after(100, self.apply_cg_filter)

        self.explore_flag = False
        self.app.log(f"[+] Exploration beendet! {processed_count} Methoden analysiert.")

    def clear_callgraph(self):
        """Löscht das Call Graph Lexikon und leert die UI."""
        self.app.cg.clear()
        self.cg_controller.refresh_ui()
        self.view.ent_cg_filter.delete(0, 'end')
        self.cg_controller.clear_filter()
        self.app.log("[*] Call Graph vollständig geleert.")