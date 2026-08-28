import os
from cg_manager import is_system_api


class SmaliCGController:
    """Verwaltet den Zustand, das Zeichnen und die Traversierung des Call Graph UI-Widgets."""

    def __init__(self, tree_widget, cg_manager, log_callback):
        self.tree = tree_widget
        self.cg = cg_manager
        self.log = log_callback

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
            self.tree.see(item_id)

        if is_open:
            self.handle_node_expand(item_id)
            self.tree.item(item_id, open=True)
            for child in self.tree.get_children(item_id):
                self._restore_state(child, new_path, open_paths, selected_paths, max_open_depth)

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