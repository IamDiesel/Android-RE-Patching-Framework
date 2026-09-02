import os
import re
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any

from core.pipeline.step_interface import PipelineStep
from core.infrastructure.command_runner import CommandRunner


def _get_dir_size_mb(path: str) -> float:
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
    except:
        pass
    return total / (1024 * 1024)


def run_build_cmd(cmd_template: str, engine_context: Any) -> bool:
    cmd = engine_context.format_cmd(cmd_template)
    cwd = engine_context.format_cmd("{DEST_DIR}")
    log_file = os.path.join(engine_context.cfg.paths["ARCHIVE_DIR"], "live_cmd_log.txt")
    engine_context.log(f"> [{cwd}]\n> {cmd}")
    return CommandRunner.run_live(cmd, cwd, engine_context.log, log_file)


def inject_nsc(engine_context: Any) -> bool:
    folder_name = engine_context.get_unpacked_dir_name()
    dest_dir = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name)
    manifest_path = os.path.join(dest_dir, "AndroidManifest.xml")

    if not os.path.exists(manifest_path):
        engine_context.log("[!] AndroidManifest.xml nicht gefunden. Überspringe Manifest-Patches.")
        return False

    engine_context.log("[*] Injiziere Network Security Config für Mitmproxy (User-Certs)...")

    nsc_content = '''<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>'''

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = f.read()
    except UnicodeDecodeError:
        engine_context.log("[!] FEHLER: AndroidManifest.xml ist binär kompiliert (AXML)!")
        engine_context.log("[!] Manifest-Patches übersprungen. Bitte entpacke die App neu (ohne '-r' Flag).")
        return True

    match = re.search(r'android:networkSecurityConfig="@xml/([^"]+)"', manifest_data)
    strategy_name = engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
    xml_dir = None

    if strategy_name == "apkeditor":
        res_root = os.path.join(dest_dir, "resources")
        for root, dirs, files in os.walk(res_root):
            if os.path.basename(root) == "xml" and os.path.basename(os.path.dirname(root)) == "res":
                xml_dir = root
                break
        if not xml_dir:
            xml_dir = os.path.join(res_root, "package_1", "res", "xml")
    else:
        xml_dir = os.path.join(dest_dir, "res", "xml")

    os.makedirs(xml_dir, exist_ok=True)
    manifest_changed = False

    if match:
        existing_nsc_name = match.group(1)
        existing_nsc_path = os.path.join(xml_dir, f"{existing_nsc_name}.xml")
        with open(existing_nsc_path, "w", encoding="utf-8") as f:
            f.write(nsc_content)
        engine_context.log(f"[+] Existierende NSC '{existing_nsc_name}.xml' mit User-Cert-Trust überschrieben!")
    else:
        nsc_path = os.path.join(xml_dir, "kippy_nsc.xml")
        with open(nsc_path, "w", encoding="utf-8") as f:
            f.write(nsc_content)
        manifest_data = manifest_data.replace("<application ",
                                              '<application android:networkSecurityConfig="@xml/kippy_nsc" ')
        engine_context.log("[+] Manifest erfolgreich gepatcht (kippy_nsc hinzugefügt)!")
        manifest_changed = True

    if strategy_name == "aapt2":
        app_source_dir = engine_context.cfg.paths.get("APP_SOURCE_DIR", "")
        has_merged_base = os.path.exists(os.path.join(app_source_dir, "merged_base.apk"))
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk") and f != "merged_base.apk"]

        if has_merged_base or len(apks) > 1:
            original_len = len(manifest_data)
            manifest_data = re.sub(
                r'<meta-data[^>]*?android:name="com\.android\.vending\.splits\.required"[^>]*?>\s*</meta-data>|<meta-data[^>]*?android:name="com\.android\.vending\.splits\.required"[^>]*?/>',
                '', manifest_data, flags=re.IGNORECASE | re.DOTALL)
            manifest_data = re.sub(r'\s*android:requiredSplitTypes="[^"]*"', '', manifest_data)
            manifest_data = re.sub(r'\s*android:splitTypes="[^"]*"', '', manifest_data)
            manifest_data = re.sub(r'\s*android:isSplitRequired="[^"]*"', '', manifest_data)

            if len(manifest_data) != original_len:
                engine_context.log("[+] Split-Zwang für AAPT2-Universal-Build aus Manifest entfernt!")
                manifest_changed = True

    native_strategy = engine_context.cfg.config.get("NATIVE_LIB_STRATEGY", "zipalign")
    if native_strategy == "extractNativeLibs":
        if 'android:extractNativeLibs="false"' in manifest_data:
            manifest_data = manifest_data.replace('android:extractNativeLibs="false"',
                                                  'android:extractNativeLibs="true"')
            engine_context.log("[+] Manifest-Hack angewendet: extractNativeLibs='true' (Crash-Prevention).")
            manifest_changed = True
        elif 'android:extractNativeLibs="true"' not in manifest_data:
            manifest_data = manifest_data.replace("<application ", '<application android:extractNativeLibs="true" ')
            engine_context.log("[+] Manifest-Hack angewendet: extractNativeLibs='true' (Crash-Prevention).")
            manifest_changed = True

    if manifest_changed:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest_data)

    return True


# --- STRATEGY CLASSES ---

class ManifestBuildStrategy(ABC):
    @abstractmethod
    def pre_process(self, engine_context: Any) -> bool: pass

    @abstractmethod
    def patch_manifest(self, engine_context: Any) -> bool: pass

    @abstractmethod
    def build(self, engine_context: Any) -> bool: pass


class SmaliOnlyStrategy(ManifestBuildStrategy):
    def pre_process(self, engine_context: Any) -> bool:
        engine_context.log("[*] SmaliOnly-Strategie: Kein Pre-Processing nötig.")
        return True

    def patch_manifest(self, engine_context: Any) -> bool:
        engine_context.log("[*] SmaliOnly-Strategie: Manifest bleibt unangetastet (-r).")
        return True

    def build(self, engine_context: Any) -> bool:
        engine_context.log("[*] Baue App mit Standard Apktool (Ohne Ressourcen-Check)...")
        folder_name = engine_context.get_unpacked_dir_name()
        return run_build_cmd(f"apktool b {folder_name} -o base.apk", engine_context)


class Aapt2Strategy(ManifestBuildStrategy):
    def pre_process(self, engine_context: Any) -> bool:
        engine_context.log("[*] AAPT2-Strategie: Setzt Universal-APK oder fehlerfreie Ressourcen voraus.")
        return True

    def patch_manifest(self, engine_context: Any) -> bool:
        return inject_nsc(engine_context)

    def build(self, engine_context: Any) -> bool:
        engine_context.log("[*] Baue App streng mit AAPT2...")
        folder_name = engine_context.get_unpacked_dir_name()
        return run_build_cmd(f"apktool b {folder_name} -o base.apk", engine_context)


class ApkEditorStrategy(ManifestBuildStrategy):
    def pre_process(self, engine_context: Any) -> bool:
        engine_context.log("[*] APKEditor-Strategie: Native AXML/ARSC Kompilierung.")
        return True

    def patch_manifest(self, engine_context: Any) -> bool:
        return inject_nsc(engine_context)

    def build(self, engine_context: Any) -> bool:
        engine_context.log("[*] Baue App mit APKEditor (ohne AAPT2)...")
        folder_name = engine_context.get_unpacked_dir_name()
        return run_build_cmd(f"java -jar \"{{APKEDITOR_JAR}}\" b -f -i {folder_name} -o base.apk", engine_context)


# --- PIPELINE STEPS ---

class MergeSplitsStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        app_source_dir = engine_context.cfg.paths.get("APP_SOURCE_DIR", "")
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk") and f != "merged_base.apk"]

        if not apks:
            engine_context.log("[!] Keine APKs im Source-Ordner gefunden!")
            return False

        if len(apks) == 1:
            engine_context.log("[*] Nur eine APK gefunden. Kein Merge notwendig.")
            return True

        engine_context.log(f"[*] {len(apks)} APKs erkannt. Verschmelze Split-APKs zu Universal-APK...")
        apkeditor_jar = engine_context.cfg.get_format_vars().get("APKEDITOR_JAR", "")

        merged_path = os.path.join(app_source_dir, "merged_base.apk")
        if os.path.exists(merged_path):
            try:
                os.remove(merged_path)
            except Exception as e:
                engine_context.log(f"[!] Konnte alte merged_base.apk nicht löschen: {e}")

        merge_cmd = f'java -jar "{apkeditor_jar}" m -i . -o merged_base.apk'
        result = CommandRunner.run_blocking(merge_cmd, app_source_dir)

        if result.returncode == 0:
            engine_context.log("[+] Split-APKs erfolgreich zu 'merged_base.apk' verschmolzen!")
            return True
        else:
            engine_context.log(f"[!] Fehler beim Verschmelzen (Exit {result.returncode}). Output: {result.stderr}")
            strategy = engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
            if strategy == "aapt2":
                engine_context.log("[!] WARNUNG: AAPT2-Strategie wird vermutlich fehlschlagen, da Ressourcen fehlen!")
            return True


class DecompileStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        app_source_dir = engine_context.cfg.paths.get("APP_SOURCE_DIR", "")
        target_apk = "base.apk"
        if os.path.exists(os.path.join(app_source_dir, "merged_base.apk")):
            target_apk = "merged_base.apk"
        elif not os.path.exists(os.path.join(app_source_dir, "base.apk")):
            engine_context.log("[!] Weder merged_base.apk noch base.apk gefunden!")
            return False

        strategy = engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        unpacked_name = engine_context.get_unpacked_dir_name()
        smali_dir = os.path.join(app_source_dir, unpacked_name)
        apkeditor_jar = engine_context.cfg.get_format_vars().get("APKEDITOR_JAR", "")

        if strategy == "apkeditor":
            cmd = f'java -jar "{apkeditor_jar}" d -f -i "{target_apk}" -o "{unpacked_name}"'
            tool_prefix = "[APKEditor]"
        else:
            cmd = f'apktool d "{target_apk}" -o "{unpacked_name}" -f'
            tool_prefix = "[Apktool]"

        engine_context.log(f"[*] Starte Entpacken für Smali: {cmd}")

        try:
            process = CommandRunner.run_background(cmd, app_source_dir)
            engine_context.last_log_line = "Starte..."

            def log_reader() -> None:
                if process.stdout:
                    for line in process.stdout:
                        clean_line = line.strip()
                        if clean_line:
                            engine_context.log(f"{tool_prefix} {clean_line}")
                            engine_context.last_log_line = clean_line

            reader_thread = threading.Thread(target=log_reader, daemon=True)
            reader_thread.start()

            last_size = -1.0
            stuck_counter = 0

            while process.poll() is None:
                time.sleep(1)
                current_size = _get_dir_size_mb(smali_dir)
                if current_size == last_size and current_size > 5:
                    stuck_counter += 1
                    if stuck_counter >= 5 and "Copying" in engine_context.last_log_line:
                        engine_context.log("[*] Ordner wächst nicht mehr. Beende blockierenden Prozess...")
                        process.terminate()
                        break
                else:
                    stuck_counter = 0
                    last_size = current_size

            reader_thread.join(timeout=1.0)

            if process.returncode in [0, 1, None]:
                engine_context.log(f"[+] '{target_apk}' erfolgreich entpackt nach: {smali_dir}")
                return True
            else:
                engine_context.log(f"[!] Fehler beim Entpacken (Exit {process.returncode}).")
                return False
        except Exception as e:
            engine_context.log(f"[!] Ausnahme beim Entpacken: {e}")
            return False


class ManifestBuildStep(PipelineStep):
    def execute(self, step_config: Dict[str, Any], engine_context: Any) -> bool:
        strategy_name = engine_context.cfg.config.get("MANIFEST_STRATEGY", "smali_only")

        if strategy_name == "aapt2":
            strategy: ManifestBuildStrategy = Aapt2Strategy()
        elif strategy_name == "apkeditor":
            strategy = ApkEditorStrategy()
        else:
            strategy = SmaliOnlyStrategy()

        engine_context.log(f"\n[*] Lade Manifest-Strategie: {strategy.__class__.__name__}")

        if not strategy.pre_process(engine_context): return False
        if not strategy.patch_manifest(engine_context): return False

        yml_path = os.path.join(engine_context.cfg.paths["DEST_DIR"], engine_context.get_unpacked_dir_name(),
                                "apktool.yml")
        if os.path.exists(yml_path):
            with open(yml_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            do_not_compress_idx = -1
            for i, line in enumerate(lines):
                if line.strip().startswith("doNotCompress:"):
                    do_not_compress_idx = i
                    break

            extensions_to_add = ["bundle", "jsbundle", "hbc"]

            if do_not_compress_idx != -1:
                if "null" in lines[do_not_compress_idx] or "[]" in lines[do_not_compress_idx]:
                    lines[do_not_compress_idx] = "doNotCompress:\n"
                existing_exts = "".join(lines[do_not_compress_idx:])
                for ext in extensions_to_add:
                    if f"- {ext}" not in existing_exts and f"- '{ext}'" not in existing_exts:
                        lines.insert(do_not_compress_idx + 1, f"- {ext}\n")
            else:
                lines.append("\ndoNotCompress:\n")
                for ext in extensions_to_add:
                    lines.append(f"- {ext}\n")

            with open(yml_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            engine_context.log("[+] Hermes-Fix: Kompression für React Native Bundles deaktiviert.")

        if not strategy.build(engine_context): return False

        if engine_context.cfg.config.get("INJECT_FRIDA", False) and strategy_name == "apkeditor":
            folder_name = engine_context.get_unpacked_dir_name()
            lib_dir = os.path.join(engine_context.cfg.paths["DEST_DIR"], folder_name, "lib")
            if os.path.exists(lib_dir):
                engine_context.log("[*] Injektiere Frida-Bibliotheken physisch unkomprimiert in die APK...")
                cmd_inject = f'jar u0f "base.apk" -C "{folder_name}" lib'
                if not run_build_cmd(cmd_inject, engine_context):
                    engine_context.log("[!] Fehler beim Injizieren der Bibliotheken via jar.")
                    return False

        native_strategy = engine_context.cfg.config.get("NATIVE_LIB_STRATEGY", "zipalign")
        if native_strategy == "zipalign":
            engine_context.log("[*] Optimiere Speicher-Alignment für Android 14 (Zipalign -p 4)...")
            cmd_zip = 'zipalign -p -f 4 "base.apk" "aligned_base.apk"'
            if not run_build_cmd(cmd_zip, engine_context): return False

            cmd_move = 'move /Y "aligned_base.apk" "base.apk"' if os.name == 'nt' else 'mv -f "aligned_base.apk" "base.apk"'
            if not run_build_cmd(cmd_move, engine_context): return False
            engine_context.log("[+] Zipalign erfolgreich abgeschlossen.")

        return True