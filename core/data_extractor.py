import json
import re
from typing import Dict, Any


class DataExtractor:
    @staticmethod
    def extract(rule: Dict[str, Any], method: str, url: str, req_h: str, req_b: str, res_h: str, res_b: str) -> str:
        if rule.get("filter_method") and rule["filter_method"] != "ALL":
            if rule["filter_method"] != method: return ""
        if rule.get("filter_url") and rule["filter_url"] not in url: return ""

        src: str = rule.get("source", "res_body")
        data_str: str = ""

        if src == "req_headers":
            data_str = req_h
        elif src == "req_body":
            data_str = req_b
        elif src == "res_headers":
            data_str = res_h
        elif src == "res_body":
            data_str = res_b

        if not data_str: return ""

        ext_type: str = rule.get("ext_type", "json")
        try:
            if ext_type == "json":
                obj: Any = json.loads(data_str)
                path: list[str] = rule.get("param1", "").split(".")
                for key in path:
                    if key: obj = obj[key]
                return str(obj)
            elif ext_type == "regex":
                pattern: str = rule.get("param1", "")
                match = re.search(pattern, data_str)
                if match:
                    return match.group(1) if len(match.groups()) > 0 else match.group(0)
            elif ext_type == "offset":
                offset: int = int(rule.get("param1", "0"))
                length: int = int(rule.get("param2", "1"))
                dtype: str = rule.get("param3", "string")
                chunk: bytes = data_str.encode('utf-8', errors='ignore')[offset:offset + length]
                if dtype == "hex":
                    return chunk.hex()
                elif dtype == "int":
                    return str(int.from_bytes(chunk, byteorder='big'))
                else:
                    return chunk.decode('utf-8', errors='ignore')
        except Exception:
            return "<err>"
        return ""