import re
import difflib
from typing import List, Dict, Tuple, Callable, Optional


class FuzzingEngine:
    """Zustandslose Logik zur heuristischen Suche von abweichendem Smali-Code."""

    @staticmethod
    def _normalize_smali(code: str) -> str:
        """Entfernt Zeilennummern, Register und Kommentare für den reinen Opcode-Vergleich."""
        code = re.sub(r'^\s*\.line\s+\d+', '', code, flags=re.MULTILINE)
        code = re.sub(r'\b[vp]\d+\b', 'REG', code)
        code = re.sub(r'#.*', '', code)
        lines = [l.strip() for l in code.split('\n') if l.strip()]
        return '\n'.join(lines)

    @staticmethod
    def _extract_methods_from_content(path: str, content: str, pattern: re.Pattern,
                                      target_list: List[Dict[str, str]]) -> None:
        lines = content.split('\n')
        in_method = False
        current_block = []
        current_sig = ""

        for line in lines:
            if line.startswith(".method ") and pattern.search(line):
                in_method = True
                current_sig = line.strip().replace(".method ", "")
                current_block = [line]
            elif in_method:
                current_block.append(line)
                if line.startswith(".end method"):
                    in_method = False
                    target_list.append({
                        "file": path,
                        "sig": current_sig,
                        "code": "\n".join(current_block)
                    })

    @classmethod
    def fuzz_by_method_name(cls, method_name: str, ram_cache: List[Tuple[str, str]],
                            cancel_hook: Optional[Callable[[], bool]] = None) -> List[Dict[str, str]]:
        candidates = []
        safe_method_name = re.escape(method_name)
        pattern = re.compile(r'^\s*\.method\s+.*?\s+' + safe_method_name + r'\(')

        for path, content in ram_cache:
            if cancel_hook and cancel_hook(): break
            if method_name in content:
                cls._extract_methods_from_content(path, content, pattern, candidates)

        return candidates

    @classmethod
    def fuzz_by_content_snippet(cls, orig_code: str, target_file: str, ram_cache: List[Tuple[str, str]], threshold=0.85,
                                deep_search=False, cancel_hook: Optional[Callable[[], bool]] = None) -> List[
        Dict[str, str]]:
        candidates = []
        norm_orig = cls._normalize_smali(orig_code)
        if not norm_orig:
            return candidates

        target_content = None
        for path, content in ram_cache:
            if path == target_file:
                target_content = content
                break

        if not deep_search:
            files_to_search = [(target_file, target_content)] if target_content else []
        else:
            keywords = [w for w in re.findall(r'"([^"]+)"', orig_code) if len(w) > 3]
            files_to_search = []

            for path, content in ram_cache:
                if cancel_hook and cancel_hook(): break
                if target_content and path == target_file:
                    files_to_search.append((path, content))
                    continue
                if keywords and not any(kw in content for kw in keywords):
                    continue
                files_to_search.append((path, content))

        for path, content in files_to_search:
            if cancel_hook and cancel_hook(): break
            if not content: continue

            lines = content.split('\n')
            in_method = False
            current_sig = ""
            current_block = []

            for line in lines:
                if line.startswith(".method "):
                    in_method = True
                    current_sig = line.strip().replace(".method ", "")
                    current_block = [line]
                elif in_method:
                    current_block.append(line)
                    if line.startswith(".end method"):
                        in_method = False
                        method_code = "\n".join(current_block)
                        norm_method = cls._normalize_smali(method_code)

                        ratio = difflib.SequenceMatcher(None, norm_orig, norm_method).ratio()

                        if ratio >= threshold:
                            candidates.append({
                                "file": path,
                                "sig": current_sig,
                                "code": method_code,
                                "score": ratio
                            })

        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        return candidates