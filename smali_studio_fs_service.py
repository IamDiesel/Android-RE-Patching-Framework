import os


class SmaliStudioFSService:
    """Behandelt Dateisystem-Operationen und Pfad-Auflösungen für das Smali Studio."""

    def __init__(self, get_smali_dir_callback, get_manifest_strategy_callback):
        self.get_smali_dir = get_smali_dir_callback
        self.get_manifest_strategy = get_manifest_strategy_callback

    def resolve_smali_path(self, rel_base):
        """Löst den Dalvik-Pfad in einen echten Dateipfad auf (Apktool vs APKEditor Logik)."""
        smali_dir = self.get_smali_dir()
        pure_path_os = rel_base.replace("/", os.sep)
        target_tool = self.get_manifest_strategy()

        possible_roots = []
        try:
            if target_tool == "apkeditor":
                base_smali = os.path.join(smali_dir, "smali")
                if os.path.exists(base_smali):
                    for d in os.listdir(base_smali):
                        if d.startswith("classes"):
                            possible_roots.append(os.path.join("smali", d))
            else:
                if os.path.exists(smali_dir):
                    for d in os.listdir(smali_dir):
                        if d == "smali" or d.startswith("smali_classes"):
                            possible_roots.append(d)

            for root in possible_roots:
                test_path = os.path.join(smali_dir, root, pure_path_os)
                if os.path.exists(test_path):
                    return os.path.join(root, pure_path_os).replace("\\", "/")
        except:
            pass
        return None

    def extract_method_block(self, rel_filepath, target_line=None, method_signature=None):
        """Liest die Datei und extrahiert den Smali-Block einer bestimmten Methode."""
        filepath = os.path.join(self.get_smali_dir(), rel_filepath)
        if not os.path.exists(filepath):
            return None, None, None

        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        start_idx, end_idx = -1, -1
        method_def = ""

        if target_line is not None:
            idx = target_line - 1
            while idx >= 0:
                if lines[idx].strip().startswith(".method"):
                    start_idx = idx
                    break
                idx -= 1

            if start_idx != -1:
                method_def = lines[start_idx].strip().replace(".method ", "")
                idx = start_idx
                while idx < len(lines):
                    if lines[idx].strip().startswith(".end method"):
                        end_idx = idx
                        break
                    idx += 1
            else:
                start_idx = 0
                end_idx = len(lines) - 1
                for i in range(len(lines)):
                    if lines[i].strip().startswith(".method"):
                        end_idx = i - 1
                        break
                method_def = "<Klassen-Header & Felder>"

        elif method_signature is not None:
            for i, l in enumerate(lines):
                if l.strip().startswith(".method") and method_signature in l:
                    start_idx = i
                    method_def = lines[start_idx].strip().replace(".method ", "")
                    break

            if start_idx != -1:
                idx = start_idx
                while idx < len(lines):
                    if lines[idx].strip().startswith(".end method"):
                        end_idx = idx
                        break
                    idx += 1

        if start_idx != -1 and end_idx != -1:
            block = "".join(lines[start_idx:end_idx + 1])
            return block, method_def, lines

        return None, None, None