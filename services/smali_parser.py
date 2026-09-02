import re
from typing import List, Dict, Any, Tuple
from services.callgraph_service import is_system_api

class SmaliStudioParser:
    """Zustandslose Service-Klasse für Regex-Parsing und Code-Analyse im Smali Studio."""

    @staticmethod
    def clean_signature(raw_signature: str) -> str:
        if raw_signature == "<Klassen-Header & Felder>":
            return raw_signature

        return re.sub(
            r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized |abstract |varargs |native |strictfp )*',
            '', raw_signature)

    @staticmethod
    def parse_outline(lines: List[str], rel_filepath: str) -> List[Dict[str, Any]]:
        is_system: bool = is_system_api("L" + rel_filepath.replace(".smali", "") + ";")
        results: List[Dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if line.startswith(".method"):
                sig: str = line.replace(".method ", "")
                disp: str = SmaliStudioParser.clean_signature(sig)
                tags: Tuple[str, ...] = ("system_api", sig) if is_system else ("method", sig)
                results.append({"type": "[M]", "display": disp, "tags": tags, "signature": sig})
            elif line.startswith(".field"):
                sig: str = line.replace(".field ", "")
                disp: str = re.sub(r'^(public |private |protected |static |final |transient |volatile )*', '', sig)
                tags: Tuple[str, ...] = ("system_api", sig) if is_system else ("field", sig)
                results.append({"type": "[F]", "display": disp, "tags": tags, "signature": sig})
        return results

    @staticmethod
    def parse_outgoing_calls(method_block: str) -> List[Dict[str, Any]]:
        matches: List[str] = re.findall(r'invoke-\w+(?:/[a-z0-9]+)? \{[^}]*\}, (L[^;]+;->[^\s]+)', method_block)
        results: List[Dict[str, Any]] = []
        for call in list(dict.fromkeys(matches)):
            cls_part: str
            meth_part: str
            cls_part, meth_part = call.split(";->")
            tags: Tuple[str, ...] = ("system_api",) if is_system_api(cls_part) else ()
            results.append({
                "raw_call": call,
                "class_part": cls_part,
                "method_part": meth_part,
                "tags": tags
            })
        return results

    @staticmethod
    def parse_data_flow(method_block: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        matches: List[Tuple[str, str]] = re.findall(r'\b([is](?:get|put)(?:-[a-z]+)?)\s+[^,]+(?:,\s*[^,]+)?,\s*(L[^;]+;->[^\s]+)', method_block)
        for instruction, target in list(dict.fromkeys(matches)):
            access_type: str = "READ" if "get" in instruction else "WRITE"
            tags: Tuple[str, str] = ("read", target) if access_type == "READ" else ("write", target)
            results.append({"access": access_type, "target": target, "tags": tags, "raw": target})

        string_matches: List[str] = re.findall(r'const-string(?:/jumbo)?\s+[vp]\d+,\s*"(.*?)"', method_block)
        for string_val in list(dict.fromkeys(string_matches)):
            display_str: str = string_val if len(string_val) < 80 else string_val[:77] + "..."
            results.append(
                {"access": "STRING", "target": f'"{display_str}"', "tags": ("string", string_val), "raw": string_val})

        return results