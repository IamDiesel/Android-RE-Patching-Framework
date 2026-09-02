import os
import json
from typing import List, Dict, Tuple

class ColumnDisplayManager:
    def __init__(self, base_path: str):
        self.config_file = os.path.join(os.path.dirname(base_path), "column_display.json")
        self.data: Dict[str, List[str]] = self.load()

    def load(self) -> Dict[str, List[str]]:
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {"active": ["ID", "Time", "Method", "URL", "Status", "Comment"], "hidden": []}

    def save(self, active: List[str], hidden: List[str]) -> None:
        self.data = {"active": active, "hidden": hidden}
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def get_lists(self, all_available: List[str]) -> Tuple[List[str], List[str]]:
        active = [c for c in self.data.get("active", []) if c in all_available]
        hidden = [c for c in self.data.get("hidden", []) if c in all_available]
        known = set(active + hidden)
        new_cols = [c for c in all_available if c not in known]
        active.extend(new_cols)
        return active, hidden