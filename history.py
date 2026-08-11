import os
import json

class HistoryManager:
    def __init__(self, config_mgr):
        self.cfg = config_mgr
        self.data = self.load()

    def load(self):
        path = self.cfg.paths.get("JSON_HISTORY")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save(self):
        path = self.cfg.paths.get("JSON_HISTORY")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)

    def add_record(self, record):
        self.data.append(record)
        self.save()
        self._append_markdown(record)
        
    def update_record(self, record_id, new_result, new_observation):
        for record in self.data:
            if record["id"] == record_id:
                record["result"] = new_result
                record["observation"] = new_observation
                break
        self.save()

    def _append_markdown(self, record):
        path = self.cfg.paths.get("LOG_FILE")
        if not path: return
        
        md = f"### 🔧 RE-Patch-Report ({record['id']})\n"
        md += f"* **Name:** {record['name']}\n"
        md += f"* **Testergebnis:** {record['result']}\n"
        for i, pt in enumerate(record.get('patches', [])):
            md += f"  * **Patch {i + 1}:** RAM: `0x{pt['ram']}` | Hex: `{pt['patch']}`\n"
        md += f"\n**Beobachtung:**\n{record['observation']}\n"
        
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n{md}\n---\n")
