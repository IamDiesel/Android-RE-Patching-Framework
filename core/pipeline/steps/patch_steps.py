import os
import shutil
import re
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from core.application.session_state import SessionState
from core.domain.exceptions import PatchConflictException

class InjectCustomLibsStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        lib_patches = SessionState.active_lib_replacements

        if not lib_patches:
            engine_context.log("[*] Keine Custom-Libs zum Austauschen konfiguriert. Überspringe.")
            return True

        folder_name = engine_context.get_unpacked_dir_name()
        apk_lib_dir = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name, "lib")

        if not os.path.exists(apk_lib_dir):
            engine_context.log("[*] Entpackte APK hat keinen 'lib' Ordner. Überspringe Lib-Austausch.")
            return True

        engine_context.log(f"[*] Führe {len(lib_patches)} Lib-Ersatz-Regeln aus...")
        replaced_count = 0

        for patch in lib_patches:
            target_name = patch.get("target", "").strip()
            source_path = patch.get("source", "").strip()

            if not target_name or not source_path: continue

            if not os.path.exists(source_path):
                engine_context.log(f"[!] FEHLER: Die lokale Ersatz-Lib existiert nicht: {source_path}")
                return False

            found_in_apk = False
            for arch in os.listdir(apk_lib_dir):
                arch_path = os.path.join(apk_lib_dir, arch)
                if os.path.isdir(arch_path):
                    target_lib_path = os.path.join(arch_path, target_name)
                    if os.path.exists(target_lib_path):
                        try:
                            shutil.copy2(source_path, target_lib_path)
                            engine_context.log(f"[+] '{target_name}' in Architektur '{arch}' erfolgreich durch Custom-Lib überschrieben!")
                            replaced_count += 1
                            found_in_apk = True
                        except Exception as e:
                            engine_context.log(f"[!] Fehler beim Austauschen von '{target_name}': {e}")
                            return False

            if not found_in_apk:
                engine_context.log(f"[-] WARNUNG: Ziel-Lib '{target_name}' wurde in keiner Architektur der APK gefunden!")

        engine_context.log(f"[+] Insgesamt {replaced_count} Lib(s) erfolgreich ausgetauscht.")
        return True

class AnchorPatchStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        lib_path = os.path.join(engine_context.cfg.paths["EXTRACT_DIR"], "lib", "arm64-v8a", "libflutter.so")
        patches = SessionState.active_hex_patches

        if not patches:
            engine_context.log("[*] Keine Hex-Patches definiert, überspringe.")
            return True

        if not os.path.exists(lib_path):
            engine_context.log(f"[!] Bibliothek nicht gefunden: {lib_path}")
            return False

        try:
            with open(lib_path, "r+b") as f:
                for idx, p in enumerate(patches):
                    ram_val = p.get("ram", "").strip()
                    if not ram_val: continue
                    ram_val = int(ram_val, 16)
                    base_val = int(p.get("base", "0").strip(), 16)
                    offset = ram_val - base_val
                    patch_hex = p.get("patch", "").replace(" ", "")
                    engine_context.log(f"[*] Hex-Patch {idx + 1}: Offset 0x{offset:X}")
                    f.seek(offset)
                    f.write(bytes.fromhex(patch_hex))
            return True
        except Exception as e:
            engine_context.log(f"[!] Patch-Fehler: {e}")
            return False

class SmartPatchStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        patches = SessionState.active_smali_patches
        if not patches:
            engine_context.log("[*] Keine Smali-Patches definiert, überspringe.")
            return True

        folder_name = engine_context.get_unpacked_dir_name()
        src_dir = os.path.join(engine_context.cfg.paths.get("APP_SOURCE_DIR", ""), folder_name)
        dst_dir = os.path.join(engine_context.cfg.paths.get("DEST_DIR", ""), folder_name)
        target_tool = "apkeditor" if engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only") == "apkeditor" else "apktool"

        for idx, p in enumerate(patches):
            rel_file = p.get("file", "").replace("\\", "/")
            parts = rel_file.split("/")
            dex_idx = None
            pure_path = rel_file

            if parts[0] == "smali" and len(parts) > 1 and parts[1].startswith("classes"):
                dex_idx = parts[1].replace("classes", "")
                pure_path = "/".join(parts[2:])
            elif parts[0].startswith("smali_classes"):
                dex_idx = parts[0].replace("smali_classes", "")
                pure_path = "/".join(parts[1:])
            elif parts[0] == "smali":
                dex_idx = ""
                pure_path = "/".join(parts[1:])

            if dex_idx is not None:
                if target_tool == "apkeditor":
                    actual_rel_file = f"smali/classes{dex_idx}/{pure_path}"
                else:
                    actual_rel_file = f"smali/{pure_path}" if dex_idx == "" else f"smali_classes{dex_idx}/{pure_path}"
            else:
                actual_rel_file = rel_file

            actual_rel_file = actual_rel_file.replace("/", os.sep)
            src_file = os.path.join(src_dir, actual_rel_file)
            dst_file = os.path.join(dst_dir, actual_rel_file)

            if not os.path.exists(src_file):
                engine_context.log(f"[!] Originaldatei nicht in Source gefunden: {actual_rel_file}")
                return False

            if not os.path.exists(dst_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

            try:
                with open(dst_file, "r", encoding="utf-8") as f:
                    content = f.read()

                orig_block = p.get("orig", "").replace("\r\n", "\n")
                edit_block = p.get("edit", "").replace("\r\n", "\n")
                content = content.replace("\r\n", "\n")

                if orig_block in content:
                    content = content.replace(orig_block, edit_block)
                    engine_context.log(f"[*] Smali-Patch {idx + 1} erfolgreich in '{actual_rel_file}' angewendet.")
                else:
                    method_match = re.search(r'^(\.method\s+[^\n]+)', orig_block, re.MULTILINE)
                    if method_match:
                        method_sig = method_match.group(1).strip()
                        actual_method_pattern = re.compile(r'^' + re.escape(method_sig) + r'.*?^\.end method', re.MULTILINE | re.DOTALL)
                        actual_match = actual_method_pattern.search(content)

                        if actual_match:
                            actual_block = actual_match.group(0)
                            raise PatchConflictException(idx, actual_rel_file, method_sig, orig_code=orig_block, actual_block=actual_block, edit_block=edit_block)
                        else:
                            engine_context.log(f"[!] Methodensignatur '{method_sig}' für Patch {idx + 1} in '{actual_rel_file}' nicht gefunden!")
                            return False
                    else:
                        engine_context.log(f"[!] Original-Block in '{actual_rel_file}' nicht gefunden und keine .method Signatur erkannt!")
                        return False

                with open(dst_file, "w", encoding="utf-8") as f:
                    f.write(content)

            except PatchConflictException as pce:
                raise pce
            except Exception as e:
                engine_context.log(f"[!] Fehler beim Anwenden von Smali-Patch: {e}")
                return False

        return True