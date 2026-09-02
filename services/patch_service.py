import re
from typing import Dict, List, Any, Tuple

class PatchService:
    """Zustandsloser Service für die Evaluierung und Verarbeitung von Patches."""

    @staticmethod
    def normalize_path(p: str) -> str:
        """Normalisiert Dalvik-Pfade für den plattformübergreifenden Vergleich."""
        p = p.replace("\\", "/")
        parts = p.split("/")
        if not parts: return p
        if parts[0] == "smali" and len(parts) > 1 and parts[1].startswith("classes"):
            return "/".join(parts[2:])
        elif parts[0].startswith("smali_classes"):
            return "/".join(parts[1:])
        elif parts[0] == "smali":
            return "/".join(parts[1:])
        return p

    @staticmethod
    def clean_smali_for_match(text: str) -> str:
        """Bereinigt Smali-Code von Zeilennummern und Kommentaren für einen Strukturvergleich."""
        lines = text.split("\n")
        cleaned = []
        for l in lines:
            l = l.strip()
            if not l or l.startswith(".line ") or l.startswith("#"): continue
            cleaned.append(l)
        return "\n".join(cleaned)

    @classmethod
    def evaluate_smali_patch(cls, patch: Dict[str, Any], ram_cache: List[Tuple[str, str]], existing_patches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Prüft einen Smali-Patch gegen den RAM-Cache (Exakter Match oder Fuzzy-Match).
        Gibt ein Status-Dictionary zurück.
        """
        target_norm = cls.normalize_path(patch.get("file", ""))
        orig_code = patch.get("orig", "").replace("\r\n", "\n").strip()

        found_content, found_path = None, None
        for path, content in ram_cache:
            if cls.normalize_path(path) == target_norm:
                found_content = content.replace("\r\n", "\n")
                found_path = path
                break

        if not found_content:
            return {"success": False, "reason": "not_found"}

        # 1. Versuch: Exakter Match
        if orig_code in found_content:
            if not any(p["file"] == found_path and p["orig"] == orig_code for p in existing_patches):
                return {"success": True, "type": "exact", "file": found_path, "orig": orig_code}
            return {"success": True, "type": "already_applied"}

        # 2. Versuch: Struktureller Match (Ignoriert .line und Kommentare)
        c_orig = cls.clean_smali_for_match(orig_code)
        m = re.search(r'^(\.method\s+[^\n]+)', orig_code, re.MULTILINE)
        if m:
            sig = m.group(1).strip()
            actual_match = re.search(r'^' + re.escape(sig) + r'.*?^\.end method', found_content, re.MULTILINE | re.DOTALL)
            if actual_match:
                actual_code = actual_match.group(0)
                if c_orig == cls.clean_smali_for_match(actual_code):
                    if not any(p["file"] == found_path and p["orig"] == actual_code for p in existing_patches):
                        return {"success": True, "type": "structural", "file": found_path, "orig": actual_code}
                    return {"success": True, "type": "already_applied"}

        return {"success": False, "reason": "conflict"}