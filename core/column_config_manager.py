import os
import json
from typing import List, Dict, Any

class ColumnConfigManager:
    def __init__(self, base_path: str):
        self.config_file = os.path.join(os.path.dirname(base_path), "custom_columns.json")
        self.columns: List[Dict[str, Any]] = self.load()

    def load(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return []

    def save(self) -> None:
        with open(self.config_file, "w", encoding="utf-8") as f: 
            json.dump(self.columns, f, indent=4)

    def add_column(self, col_dict: Dict[str, Any]) -> None:
        self.columns.append(col_dict)
        self.save()

    def update_column(self, index: int, col_dict: Dict[str, Any]) -> None:
        if 0 <= index < len(self.columns):
            self.columns[index] = col_dict
            self.save()

    def delete_column(self, index: int) -> None:
        if 0 <= index < len(self.columns):
            del self.columns[index]
            self.save()