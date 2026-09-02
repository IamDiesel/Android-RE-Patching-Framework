import time
import threading
from services.callgraph_service import is_system_api
from services.smali_parser import SmaliStudioParser
from core.application.event_bus import EventBus

class CallGraphExplorationService:
    def __init__(self, cg_manager, fs_service):
        self.cg = cg_manager
        self.fs_service = fs_service
        self.explore_flag = False

    def start_auto_explore(self, start_nodes, max_depth):
        EventBus.publish("LOG_INFO", f"[*] Starte automatische Exploration für {len(start_nodes)} Knoten (Tiefe: {max_depth})...")
        self.explore_flag = True
        threading.Thread(target=self._exploration_thread, args=(start_nodes, max_depth), daemon=True).start()

    def stop_auto_explore(self):
        if self.explore_flag:
            self.explore_flag = False
            EventBus.publish("LOG_INFO", "[*] Exploration wird gestoppt...")

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
                    callee_path = self.fs_service.resolve_smali_path(c["class_part"][1:] + ".smali") or c["class_part"][1:]
                    self.cg.add_edge(filepath, sig, callee_path, c["method_part"])
                    queue.append((f"{callee_path}|{c['method_part']}", current_depth + 1))

            if time.time() - last_ui_update > 0.5:
                EventBus.publish("CG_REFRESH_STABLE")
                last_ui_update = time.time()

            if processed_count > 5000:
                EventBus.publish("LOG_INFO", "[!] Sicherheitslimit von 5000 analysierten Methoden erreicht. Stoppe.")
                break

        EventBus.publish("CG_REFRESH_STABLE")
        EventBus.publish("CG_FILTER_APPLY")
        self.explore_flag = False
        EventBus.publish("LOG_INFO", f"[+] Exploration beendet! {processed_count} Methoden analysiert.")