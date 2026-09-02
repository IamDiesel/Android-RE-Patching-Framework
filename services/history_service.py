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

        app_pkg = record.get('app_package', 'Unbekannt')
        app_ver = record.get('app_version', 'N/A')

        md = f"### 🔧 RE-Patch-Report ({record['id']})\n"
        md += f"* **App:** {app_pkg} (v{app_ver})\n"
        md += f"* **Name:** {record.get('name', 'N/A')}\n"
        md += f"* **Testergebnis:** {record['result']}\n"

        for i, pt in enumerate(record.get('patches', [])):
            if pt.get("type") == "smali":
                md += f"\n  * **Smali Patch {i + 1}** in Datei: `{pt.get('file')}`\n"
                md += f"  ```smali\n{pt.get('edit', '')}\n  ```\n"
            elif pt.get("type") == "lib_replace":
                md += f"  * **Lib Replacement:** Ziel: `{pt.get('target')}` | Quelle: `{pt.get('source')}`\n"
            else:
                file_name = pt.get("file", "libflutter.so")
                md += f"  * **Hex Patch {i + 1}:** Datei: `{file_name}` | RAM: `0x{pt.get('ram', '?')}` | Hex: `{pt.get('patch', '?')}`\n"

        md += f"\n**Beobachtung:**\n{record.get('observation', '')}\n"

        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n{md}\n---\n")