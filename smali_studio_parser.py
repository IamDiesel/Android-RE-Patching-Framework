import re
from cg_manager import is_system_api


class SmaliStudioParser:
    """Zustandslose Service-Klasse für Regex-Parsing und Code-Analyse im Smali Studio."""

    @staticmethod
    def clean_signature(raw_signature):
        """Entfernt alle Smali-Modifikatoren aus einer Methodensignatur."""
        if raw_signature == "<Klassen-Header & Felder>":
            return raw_signature

        return re.sub(
            r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized |abstract |varargs |native |strictfp )*',
            '', raw_signature)

    @staticmethod
    def parse_outline(lines, rel_filepath):
        """Parst Felder und Methoden für den Outline-Tree."""
        is_system = is_system_api("L" + rel_filepath.replace(".smali", "") + ";")
        results = []
        for line in lines:
            line = line.strip()
            if line.startswith(".method"):
                sig = line.replace(".method ", "")
                disp = SmaliStudioParser.clean_signature(sig)
                tags = ("system_api", sig) if is_system else ("method", sig)
                results.append({"type": "[M]", "display": disp, "tags": tags, "signature": sig})
            elif line.startswith(".field"):
                sig = line.replace(".field ", "")
                disp = re.sub(r'^(public |private |protected |static |final |transient |volatile )*', '', sig)
                tags = ("system_api", sig) if is_system else ("field", sig)
                results.append({"type": "[F]", "display": disp, "tags": tags, "signature": sig})
        return results

    @staticmethod
    def parse_outgoing_calls(method_block):
        """Extrahiert alle 'invoke-X' Aufrufe aus einem Smali-Block."""
        matches = re.findall(r'invoke-\w+(?:/[a-z0-9]+)? \{[^}]*\}, (L[^;]+;->[^\s]+)', method_block)
        results = []
        for call in list(dict.fromkeys(matches)):
            cls_part, meth_part = call.split(";->")
            tags = ("system_api",) if is_system_api(cls_part) else ()
            results.append({
                "raw_call": call,
                "class_part": cls_part,
                "method_part": meth_part,
                "tags": tags
            })
        return results

    @staticmethod
    def parse_data_flow(method_block):
        """Extrahiert State-Manipulationen (get/put) und hartkodierte Strings."""
        results = []

        # 1. State-Manipulation: Felder (SGET/SPUT/IGET/IPUT)
        matches = re.findall(r'\b([is](?:get|put)(?:-[a-z]+)?)\s+[^,]+(?:,\s*[^,]+)?,\s*(L[^;]+;->[^\s]+)',
                             method_block)
        for instruction, target in list(dict.fromkeys(matches)):
            access_type = "READ" if "get" in instruction else "WRITE"
            tags = ("read", target) if access_type == "READ" else ("write", target)
            results.append({"access": access_type, "target": target, "tags": tags, "raw": target})

        # 2. Hardkodierte Strings (const-string & const-string/jumbo)
        string_matches = re.findall(r'const-string(?:/jumbo)?\s+[vp]\d+,\s*"(.*?)"', method_block)
        for string_val in list(dict.fromkeys(string_matches)):
            display_str = string_val if len(string_val) < 80 else string_val[:77] + "..."
            results.append(
                {"access": "STRING", "target": f'"{display_str}"', "tags": ("string", string_val), "raw": string_val})

        return results