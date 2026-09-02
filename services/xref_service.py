from services.callgraph_service import is_system_api
from services.smali_parser import SmaliStudioParser
from core.application.event_bus import EventBus


class XrefService:
    def __init__(self, search_engine, cg_manager):
        self.search_engine = search_engine
        self.cg = cg_manager

    def find_incoming_xrefs(self, current_smali_file, current_method_name, is_reachable_callback):
        if not current_smali_file: return
        parts = current_smali_file.split("/")
        if parts[0] == "smali" and len(parts) > 1 and parts[1].startswith("classes"):
            pure_path = "/".join(parts[2:])
        elif parts[0].startswith("smali_classes"):
            pure_path = "/".join(parts[1:])
        elif parts[0] == "smali":
            pure_path = "/".join(parts[1:])
        else:
            pure_path = current_smali_file

        d_class = f"L{pure_path.replace('.smali', '')};"
        search_term = f"{d_class}->{current_method_name}"

        EventBus.publish("XREF_SEARCH_STARTED")

        def on_results(results, cancelled):
            self._process_xref_results(results, current_smali_file, current_method_name, is_reachable_callback)

        self.search_engine.search_xrefs_incoming(search_term, on_results)

    def _process_xref_results(self, results, current_smali_file, current_method_name, is_reachable_callback):
        current_node_id = f"{current_smali_file}|{current_method_name}"

        if not results:
            EventBus.publish("LOG_INFO", "[*] Keine eingehenden Aufrufe (XREFs) für diese Methode im RAM gefunden.")
            EventBus.publish("XREF_SEARCH_FINISHED", {"results": [], "current_node_id": current_node_id})
            return

        seen_callers = set()
        processed_results = []
        for r in results:
            norm_path = r[0].replace("\\", "/")
            clean_sig = SmaliStudioParser.clean_signature(r[1])
            caller_id = f"{norm_path}|{clean_sig}"

            if caller_id in seen_callers: continue
            seen_callers.add(caller_id)

            tags = ("system_api", clean_sig) if is_system_api("L" + norm_path) else (clean_sig,)
            processed_results.append({"norm_path": norm_path, "clean_sig": clean_sig, "tags": tags})

            caller, _ = self.cg.add_edge(norm_path, clean_sig, current_smali_file, current_method_name)

            if not is_reachable_callback(caller.id):
                self.cg.make_root(caller.id)

        self.cg.remove_root(current_node_id)
        EventBus.publish("XREF_SEARCH_FINISHED", {"results": processed_results, "current_node_id": current_node_id})