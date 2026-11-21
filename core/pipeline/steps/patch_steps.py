import os
import shutil
import re
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from core.application.session_state import SessionState
from core.domain.exceptions import PatchConflictException
from core.fuzzing_engine import FuzzingEngine


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
                            shutil.copyfile(source_path, target_lib_path)
                            os.utime(target_lib_path, None)  # Fix 1.9: frische mtime
                            engine_context.log(
                                f"[+] '{target_name}' in Architektur '{arch}' erfolgreich durch Custom-Lib überschrieben!")
                            replaced_count += 1
                            found_in_apk = True
                        except Exception as e:
                            engine_context.log(f"[!] Fehler beim Austauschen von '{target_name}': {e}")
                            return False

            if not found_in_apk:
                engine_context.log(
                    f"[-] WARNUNG: Ziel-Lib '{target_name}' wurde in keiner Architektur der APK gefunden!")

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


class ApplyLibCarvePatchesStep(PipelineStep):
    """Kompiliert & schreibt aktive LibCarve-Patches (ASM / C-Cave / Hex) in BUILD_NATIVE & BUILD_FLUTTER."""

    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        patches = list(getattr(SessionState, "active_libcarve_patches", None) or ())
        if not patches:
            engine_context.log("[*] Keine LibCarve-Patches definiert, überspringe.")
            return True

        target_mode = str(step_config.get("target") or "").lower()
        paths = getattr(engine_context.cfg, "paths", {}) or {}
        if target_mode == "extract" and paths.get("EXTRACT_DIR"):
            apk_lib_dir = os.path.join(paths["EXTRACT_DIR"], "lib")
        else:
            folder_name = engine_context.get_unpacked_dir_name()
            dest_base = paths.get("DEST_DIR", "")
            cand_dest = os.path.join(dest_base, folder_name, "lib") if dest_base else ""
            if cand_dest and os.path.isdir(cand_dest):
                apk_lib_dir = cand_dest
            elif paths.get("EXTRACT_DIR") and os.path.isdir(os.path.join(paths["EXTRACT_DIR"], "lib")):
                apk_lib_dir = os.path.join(paths["EXTRACT_DIR"], "lib")
            else:
                apk_lib_dir = cand_dest

        if not apk_lib_dir or not os.path.isdir(apk_lib_dir):
            engine_context.log("[*] Kein entpackter 'lib'-Ordner fuer LibCarve vorhanden. Überspringe.")
            return True

        from services.libcarve_compiler_service import LibCarveCompilerService
        by_target = {}
        for p in patches:
            fname = os.path.basename(str(p.get("file") or "").strip())
            abi = str(p.get("abi") or "arm64-v8a").strip()
            if fname:
                by_target.setdefault((abi, fname), []).append(p)

        cfg_dict = getattr(engine_context.cfg, "config", {}) or {}
        applied_files = 0
        for (abi, fname), plist in by_target.items():
            cand_path = os.path.join(apk_lib_dir, abi, fname)
            if not os.path.isfile(cand_path):
                # Fallback: nach Dateiname in allen ABIs suchen
                hits = [
                    os.path.join(apk_lib_dir, d, fname)
                    for d in sorted(os.listdir(apk_lib_dir))
                    if os.path.isfile(os.path.join(apk_lib_dir, d, fname))
                ]
                cand_path = hits[0] if hits else ""
            if not cand_path or not os.path.isfile(cand_path):
                engine_context.log(f"[!] LibCarve Ziel-Lib '{fname}' ({abi}) im Build-Verzeichnis nicht gefunden.")
                return False
            try:
                with open(cand_path, "rb") as fh:
                    orig_bytes = fh.read()
                out_bytes, reports = LibCarveCompilerService.apply_patches_to_elf(orig_bytes, plist, cfg=cfg_dict)
                fails = [r for r in reports if not r.get("ok")]
                if fails:
                    err_msg = "; ".join(str(r.get("error")) for r in fails)
                    engine_context.log(f"[!] LibCarve Build-Fehler in {fname}: {err_msg}")
                    return False
                with open(cand_path, "wb") as fh:
                    fh.write(out_bytes)
                os.utime(cand_path, None)
                applied_files += 1
                engine_context.log(f"[+] LibCarve: {len(reports)} Patch(es) erfolgreich in {cand_path} angewendet.")
            except Exception as e:
                engine_context.log(f"[!] LibCarve Fehler bei '{fname}': {e}")
                return False

        engine_context.log(f"[+] LibCarve Build-Step abgeschlossen ({applied_files} Bibliothek(en) aktualisiert).")
        return True


class SmartPatchStep(PipelineStep):
    @staticmethod
    def _is_super_stub(edit_block: str) -> bool:
        """True, wenn das Edit ein reiner Super-Delegations-Stub ist (nur
        invoke-super + return/move-result/nop/goto, plus Metadaten). Nur dann
        darf bei Body-Drift ohne Rueckfrage der komplette Methoden-Body ersetzt
        werden (Group-A Lifecycle-Neutralisierung). Chirurgische Edits enthalten
        andere Instruktionen -> bleiben ein Konflikt (Mensch entscheidet)."""
        e = edit_block.strip()
        if not e.startswith(".method"):
            return False
        if e.count(".end method") != 1 or not e.rstrip().endswith(".end method"):
            return False
        has_super = False
        in_ann = False
        for ln in e.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if s.startswith(".annotation"):
                in_ann = True
                continue
            if s.startswith(".end annotation"):
                in_ann = False
                continue
            if in_ann:
                continue
            if s.startswith(".method") or s.startswith(".end method"):
                continue
            if s.startswith((".locals", ".registers", ".param", ".prologue", ".line", "#", ":")):
                continue
            if s.startswith("invoke-super"):
                has_super = True
                continue
            if s.startswith(("return", "move-result", "nop", "goto", "move-object/from16", "move/from16")):
                continue
            return False
        return has_super

    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        patches = SessionState.active_smali_patches
        if not patches:
            engine_context.log("[*] Keine Smali-Patches definiert, überspringe.")
            return True

        folder_name = engine_context.get_unpacked_dir_name()
        src_dir = os.path.join(engine_context.cfg.paths.get("APP_SOURCE_DIR", ""), folder_name)
        dst_dir = os.path.join(engine_context.cfg.paths.get("DEST_DIR", ""), folder_name)
        target_tool = "apkeditor" if engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only") == "apkeditor" else "apktool"

        # 1. Patches nach Datei gruppieren
        file_groups = {}
        for idx, p in enumerate(patches):
            rel_file = p.get("file", "").replace("\\", "/")
            if rel_file not in file_groups:
                file_groups[rel_file] = []
            file_groups[rel_file].append((idx, p))

        # 2. Dateien iterieren und hierarchisch anwenden
        for rel_file, file_patches in file_groups.items():
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

            # NEUE DATEI (new_file): direkt in Destination schreiben, keine Source noetig
            _nf = next((pp for _i, pp in file_patches if pp.get("scope") == "new_file"), None)
            if _nf is not None:
                try:
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    with open(dst_file, "w", encoding="utf-8") as _f:
                        _f.write(_nf.get("edit", "").replace("\r", ""))
                    engine_context.log(f"[+] Neue Datei erstellt: '{actual_rel_file}'")
                except Exception as _e:
                    engine_context.log(f"[!] Fehler beim Erstellen der neuen Datei '{actual_rel_file}': {_e}")
                    return False
                continue

            if not os.path.exists(src_file):
                engine_context.log(f"[!] Originaldatei nicht in Source gefunden: {actual_rel_file}")
                return False

            if not os.path.exists(dst_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

            try:
                with open(dst_file, "r", encoding="utf-8") as f:
                    content = f.read()

                content = content.replace("\r", "")

                # 3. Sortieren: Full-File-Patches zuerst (0), dann Methoden-Patches (1)
                file_patches.sort(key=lambda item: 0 if item[1].get("scope") == "file" else 1)

                for idx, p in file_patches:
                    orig_block = p.get("orig", "").replace("\r", "")
                    edit_block = p.get("edit", "").replace("\r", "")
                    scope = p.get("scope", "method")

                    if scope == "file":
                        # Full-File Patch: Wir ersetzen den gesamten Dateiinhalt.
                        content = edit_block
                        engine_context.log(f"[*] Full-File-Patch {idx + 1} erfolgreich in '{actual_rel_file}' angewendet.")
                        continue

                    # Methoden-Patch
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
                                if SmartPatchStep._is_super_stub(edit_block):
                                    content = content.replace(actual_block, edit_block)
                                    engine_context.log(f"[*] Smali-Patch {idx + 1} per Super-Stub-Anchor in '{actual_rel_file}' angewendet (Body-Drift ignoriert).")
                                else:
                                    norm_orig = FuzzingEngine._normalize_smali(orig_block)
                                    norm_actual = FuzzingEngine._normalize_smali(actual_block)

                                    if norm_orig == norm_actual:
                                        content = content.replace(actual_block, edit_block)
                                        engine_context.log(f"[*] Smali-Patch {idx + 1} per Auto-Fix in '{actual_rel_file}' angewendet.")
                                    else:
                                        raise PatchConflictException(idx, actual_rel_file, method_sig, orig_code=orig_block, actual_block=actual_block, edit_block=edit_block)
                            else:
                                # SMART APPEND: Methode existiert nicht -> ans Dateiende hängen
                                engine_context.log(f"[*] Methode '{method_sig}' (Patch {idx + 1}) nicht gefunden. Führe Smart Append aus.")
                                content = content.rstrip() + "\n\n" + edit_block + "\n"
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
