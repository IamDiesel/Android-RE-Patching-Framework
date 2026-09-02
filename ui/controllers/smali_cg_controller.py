# Datei: smali_studio_cg_controller.py

import os
import re
from services.callgraph_service import is_system_api
from core.application.event_bus import EventBus

class SmaliCGController:
    """Verwaltet den Zustand, das Zeichnen und die Traversierung des Call Graph UI-Widgets."""

    def __init__(self, tree_widget, cg_manager):
        self.tree = tree_widget
        self.cg = cg_manager

        # State für den Live-Filter & Sprünge
        self.current_hits = []
        self.current_hit_idx = -1
        self.lbl_hits = None

    def log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

    def insert_node(self, parent_item, node_id):
        node = self.cg.get_node(node_id)
        if not node: return None
        disp_text = node.signature.split('(')[0]
        tags = ["system_api"] if is_system_api("L" + node.filepath) else []
        tags.append(node_id)
        item = self.tree.insert(parent_item, "end", text=disp_text, values=(os.path.basename(node.filepath),),
                                tags=tags)
        if node.callees:
            self.tree.insert(item, "end", text="*dummy*")
        return item

    def handle_node_expand(self, item):
        children = self.tree.get_children(item)
        if len(children) == 1 and self.tree.item(children[0], "text") == "*dummy*":
            self.tree.delete(children[0])
            tags = self.tree.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]
            node = self.cg.get_node(node_id)
            if node:
                for callee_id in node.callees:
                    self.insert_node(item, callee_id)

    def refresh_ui(self):
        open_paths = set()
        selected_paths = set()

        for sel_item in self.tree.selection():
            selected_paths.add(tuple(self._build_path_for_item(sel_item)))

        for child in self.tree.get_children():
            self._save_open_paths(child, (), open_paths)

        max_open_depth = max((len(p) for p in open_paths), default=0)

        for i in self.tree.get_children():
            self.tree.delete(i)

        for root_id in self.cg.roots:
            self.insert_node("", root_id)

        for child in self.tree.get_children():
            self._restore_state(child, (), open_paths, selected_paths, max_open_depth)

    def refresh_ui_stable(self):
        """Aktualisiert den Baum, zwingt aber die Scroll-Position zum Stillstand (kein Ruckeln)."""
        y_pos = self.tree.yview()
        self._freeze_scroll = True
        self.refresh_ui()
        self._freeze_scroll = False
        if y_pos:
            self.tree.yview_moveto(y_pos[0])

    def _build_path_for_item(self, item):
        path = []
        curr = item
        while curr:
            tags = self.tree.item(curr, "tags")
            if tags:
                node_id = tags[1] if "system_api" in tags else tags[0]
                path.insert(0, node_id)
            curr = self.tree.parent(curr)
        return path

    def _save_open_paths(self, item_id, current_path, open_paths):
        tags = self.tree.item(item_id, "tags")
        if not tags: return
        node_id = tags[1] if "system_api" in tags else tags[0]
        new_path = current_path + (node_id,)

        if self.tree.item(item_id, "open"):
            open_paths.add(new_path)
            for child in self.tree.get_children(item_id):
                self._save_open_paths(child, new_path, open_paths)

    def _restore_state(self, item_id, current_path, open_paths, selected_paths, max_open_depth):
        if len(current_path) > max_open_depth + 2: return

        tags = self.tree.item(item_id, "tags")
        if not tags: return
        node_id = tags[1] if "system_api" in tags else tags[0]
        new_path = current_path + (node_id,)

        is_open, is_selected = False, False

        for p_set, flag_name in [(open_paths, 'is_open'), (selected_paths, 'is_selected')]:
            matched = False
            for i in range(len(new_path)):
                if new_path[i:] in p_set:
                    matched = True;
                    break
            if not matched:
                for p in p_set:
                    for i in range(len(p)):
                        if p[i:] == new_path:
                            matched = True;
                            break
            if flag_name == 'is_open':
                is_open = matched
            else:
                is_selected = matched

        if is_selected:
            self.tree.selection_add(item_id)
            if not getattr(self, '_freeze_scroll', False):
                self.tree.see(item_id)

        if is_open:
            self.handle_node_expand(item_id)
            self.tree.item(item_id, open=True)
            for child in self.tree.get_children(item_id):
                self._restore_state(child, new_path, open_paths, selected_paths, max_open_depth)

    # --- CALL GRAPH LIVE FILTER & NAVIGATION ---

    def _get_method_code_fast(self, filepath, sig, ram_dict):
        """Isoliert den Methodencode pfeilschnell aus dem vollen RAM-String."""
        full_code = ram_dict.get(filepath, "")
        if not full_code: return ""

        safe_sig = re.escape(sig)
        match = re.search(r'^\s*\.method\s+.*?' + safe_sig, full_code, re.MULTILINE)
        if not match: return ""

        start_idx = match.start()
        end_idx = full_code.find('.end method', start_idx)
        if end_idx == -1: return full_code[start_idx:]
        return full_code[start_idx:end_idx + 11]

    def clear_filter(self):
        """Entfernt alle Farb-Markierungen."""
        self.current_hits = []
        self.current_hit_idx = -1
        if self.lbl_hits: self.lbl_hits.config(text="0/0")

        def _clear(item):
            tags = self.tree.item(item, "tags")
            if tags:
                clean_tags = [t for t in tags if t not in ("match_exact", "match_parent", "dimmed")]
                self.tree.item(item, tags=clean_tags)
            for child in self.tree.get_children(item):
                _clear(child)

        for root in self.tree.get_children(""):
            _clear(root)

    def apply_filter(self, term, ram_dict, lbl_hits):
        """Durchsucht Pfad, Signatur und Quellcode und markiert Äste als Hits/Parents/Dimmed."""
        self.current_hits = []
        self.current_hit_idx = -1
        self.lbl_hits = lbl_hits

        search_terms = term.lower().split()
        if not search_terms:
            self.clear_filter()
            return

        def _traverse(item):
            tags = self.tree.item(item, "tags")
            if not tags or self.tree.item(item, "text") == "*dummy*":
                return False

            node_id = tags[1] if "system_api" in tags else tags[0]
            if "|" not in node_id: return False
            filepath, sig = node_id.split("|", 1)

            search_target = f"{filepath} {sig}".lower()
            method_code = self._get_method_code_fast(filepath, sig, ram_dict).lower()
            search_target += " " + method_code

            # AND-Logik: Alle getippten Wörter müssen im String vorkommen
            is_hit = all(t in search_target for t in search_terms)
            if is_hit:
                self.current_hits.append(item)

            # Kinder rekursiv prüfen (damit Eltern-Ordner aufleuchten)
            child_hit = False
            for child in self.tree.get_children(item):
                if _traverse(child):
                    child_hit = True

            # Tags zuweisen (Alte Farb-Tags bereinigen)
            base_tags = [t for t in tags if t not in ("match_exact", "match_parent", "dimmed")]
            if is_hit:
                base_tags.append("match_exact")
            elif child_hit:
                base_tags.append("match_parent")
            else:
                base_tags.append("dimmed")

            self.tree.item(item, tags=base_tags)
            return is_hit or child_hit

        # Freeze Scroll beim taggen (verhindert Zucken)
        self._freeze_scroll = True
        for root in self.tree.get_children(""):
            _traverse(root)
        self._freeze_scroll = False

        self.update_hit_label()
        if self.current_hits:
            self.next_hit()  # Springt automatisch zum ersten Treffer

    def update_hit_label(self):
        if not self.current_hits:
            self.lbl_hits.config(text="0/0")
        else:
            self.lbl_hits.config(text=f"{self.current_hit_idx + 1}/{len(self.current_hits)}")

    def next_hit(self):
        if not self.current_hits: return
        self.current_hit_idx = (self.current_hit_idx + 1) % len(self.current_hits)
        self._jump_to_current_hit()

    def prev_hit(self):
        if not self.current_hits: return
        self.current_hit_idx = (self.current_hit_idx - 1) % len(self.current_hits)
        self._jump_to_current_hit()

    def _jump_to_current_hit(self):
        item = self.current_hits[self.current_hit_idx]

        # Alle Elternordner aufklappen, damit der Treffer sichtbar wird
        parent = self.tree.parent(item)
        while parent:
            self.tree.item(parent, open=True)
            parent = self.tree.parent(parent)

        self.tree.selection_set(item)
        self.tree.see(item)
        self.update_hit_label()

    def find_and_highlight(self, target_id, highlight_only=False):
        paths_to_target = []
        queue = [[root_id] for root_id in self.cg.roots]

        while queue:
            path = queue.pop(0)
            if len(path) > 15: continue
            curr_id = path[-1]

            if curr_id == target_id:
                paths_to_target.append(path)
                continue

            node = self.cg.get_node(curr_id)
            if node:
                for callee_id in node.callees:
                    if callee_id not in path:
                        queue.append(path + [callee_id])

        if not paths_to_target:
            if not highlight_only:
                self.log("[*] Aktuelle Methode ist (noch) nicht im Call Graph verknüpft.")
            return

        found_ui_items = []
        for path in paths_to_target:
            curr_item = ""
            for step_idx, node_id in enumerate(path):
                self.handle_node_expand(curr_item)

                next_item = None
                for child in self.tree.get_children(curr_item):
                    tags = self.tree.item(child, "tags")
                    if tags:
                        child_id = tags[1] if "system_api" in tags else tags[0]
                        if child_id == node_id:
                            next_item = child;
                            break

                if next_item:
                    if step_idx < len(path) - 1:
                        self.tree.item(next_item, open=True)
                    curr_item = next_item
                else:
                    curr_item = None;
                    break

            if curr_item: found_ui_items.append(curr_item)

        if found_ui_items:
            self.tree.selection_set(found_ui_items)
            self.tree.see(found_ui_items[0])