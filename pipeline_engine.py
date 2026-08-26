import os
import subprocess
import time
import shutil
import threading
from tkinter import messagebox
from abc import ABC, abstractmethod


# --- STRATEGY PATTERN FÜR MANIFEST & BUILD ---
class ManifestBuildStrategy(ABC):
    @abstractmethod
    def pre_process(self, engine): pass

    @abstractmethod
    def patch_manifest(self, engine): pass

    @abstractmethod
    def build(self, engine): pass


class SmaliOnlyStrategy(ManifestBuildStrategy):
    def pre_process(self, engine):
        engine.log("[*] SmaliOnly-Strategie: Kein Pre-Processing nötig.")
        return True

    def patch_manifest(self, engine):
        engine.log("[*] SmaliOnly-Strategie: Manifest bleibt unangetastet (-r).")
        return True

    def build(self, engine):
        engine.log("[*] Baue App mit Standard Apktool (Ohne Ressourcen-Check)...")
        folder_name = engine.get_unpacked_dir_name()
        cmd = f"apktool b {folder_name} -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class Aapt2Strategy(ManifestBuildStrategy):
    def pre_process(self, engine):
        engine.log("[*] AAPT2-Strategie: Setzt Universal-APK oder fehlerfreie Ressourcen voraus.")
        return True

    def patch_manifest(self, engine):
        return engine._inject_nsc()

    def build(self, engine):
        engine.log("[*] Baue App streng mit AAPT2...")
        folder_name = engine.get_unpacked_dir_name()
        cmd = f"apktool b {folder_name} -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class ApkEditorStrategy(ManifestBuildStrategy):
    def pre_process(self, engine):
        engine.log("[*] APKEditor-Strategie: Native AXML/ARSC Kompilierung.")
        return True

    def patch_manifest(self, engine):
        return engine._inject_nsc()

    def build(self, engine):
        engine.log("[*] Baue App mit APKEditor (ohne AAPT2)...")
        folder_name = engine.get_unpacked_dir_name()
        cmd = f"java -jar \"{{APKEDITOR_JAR}}\" b -f -i {folder_name} -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class PipelineEngine:
    def __init__(self, config_mgr, logger_func, get_patches_func, get_archive_path_func):
        self.cfg = config_mgr
        self.log = logger_func
        self.get_patches = get_patches_func
        self.get_archive_path = get_archive_path_func
        self.logcat_process = None
        self.logcat_out = None

    def get_unpacked_dir_name(self):
        """Ermittelt den dynamischen Ordnernamen basierend auf der Strategie."""
        strategy = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        return "base_unpacked_apkeditor" if strategy == "apkeditor" else "base_unpacked_apktool"

    def run_pipeline(self, pipeline_name):
        pipeline = self.cfg.config.get("PIPELINES", {}).get(pipeline_name, [])
        if not pipeline:
            self.log(f"[!] Pipeline '{pipeline_name}' ist leer oder nicht definiert.")
            return False

        self.log(f"=== STARTE PIPELINE: {pipeline_name} ===")

        for step in pipeline:
            step_name = step.get("name", "Unnamed Step")
            step_type = step.get("type", "cmd")
            self.log(f"\n--- Schritt: {step_name} ---")

            success = False
            if step_type == "cmd":
                success = self._run_cmd_step(step)
            elif step_type == "mirror_workspace":
                success = self._mirror_workspace()
            elif step_type == "manifest_and_build":
                success = self._run_manifest_and_build_strategy()
            elif step_type == "anchor_patch":
                success = self._apply_hex_patches()
            elif step_type == "smart_patch":
                success = self._apply_smart_patches()
            elif step_type == "merge_splits":
                success = self._merge_splits()
            elif step_type == "decompile":
                success = self._decompile()
            elif step_type == "trace_start":
                success = self._start_trace_step(step)
            elif step_type == "trace_stop":
                success = self._stop_trace_step()
            else:
                self.log(f"[!] Unbekannter Schritt-Typ: {step_type}")

            if not success:
                self.log(f"\n[!] FEHLER: Pipeline bei Schritt '{step_name}' abgebrochen.")
                messagebox.showerror("Pipeline Fehler", f"Der Schritt '{step_name}' ist fehlgeschlagen.")
                return False

        self.log(f"\n=== PIPELINE {pipeline_name} ERFOLGREICH ===")
        return True

    def _get_dir_size_mb(self, path):
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

    def _merge_splits(self):
        app_source_dir = self.cfg.paths.get("APP_SOURCE_DIR", "")
        apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk") and f != "merged_base.apk"]

        if not apks:
            self.log("[!] Keine APKs im Source-Ordner gefunden!")
            return False

        if len(apks) == 1:
            self.log("[*] Nur eine APK gefunden. Kein Merge notwendig.")
            return True

        self.log(f"[*] {len(apks)} APKs erkannt. Verschmelze Split-APKs zu Universal-APK...")
        apkeditor_jar = os.path.join(self.cfg.config.get("BASE_DIR", ""),
                                     self.cfg.config.get("APKEDITOR_JAR", "APKEditor.jar"))

        merged_path = os.path.join(app_source_dir, "merged_base.apk")
        if os.path.exists(merged_path):
            try:
                os.remove(merged_path)
            except Exception as e:
                self.log(f"[!] Konnte alte merged_base.apk nicht löschen: {e}")

        merge_cmd = f'java -jar "{apkeditor_jar}" m -i . -o merged_base.apk'

        try:
            result = subprocess.run(merge_cmd, shell=True, cwd=app_source_dir,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                self.log("[+] Split-APKs erfolgreich zu 'merged_base.apk' verschmolzen!")
                return True
            else:
                self.log(f"[!] Fehler beim Verschmelzen (Exit {result.returncode}). Output: {result.stderr}")
                strategy = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
                if strategy == "aapt2":
                    self.log("[!] WARNUNG: AAPT2-Strategie wird vermutlich fehlschlagen, da Ressourcen fehlen!")
                return True  # Erlaubt Fallback auf base.apk
        except Exception as e:
            self.log(f"[!] Ausnahme beim Verschmelzen: {e}")
            return True

    def _decompile(self):
        app_source_dir = self.cfg.paths.get("APP_SOURCE_DIR", "")

        target_apk = "base.apk"
        if os.path.exists(os.path.join(app_source_dir, "merged_base.apk")):
            target_apk = "merged_base.apk"
        elif not os.path.exists(os.path.join(app_source_dir, "base.apk")):
            self.log("[!] Weder merged_base.apk noch base.apk gefunden!")
            return False

        strategy = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
        unpacked_name = self.get_unpacked_dir_name()
        smali_dir = os.path.join(app_source_dir, unpacked_name)
        apkeditor_jar = os.path.join(self.cfg.config.get("BASE_DIR", ""),
                                     self.cfg.config.get("APKEDITOR_JAR", "APKEditor.jar"))

        if strategy == "apkeditor":
            cmd = f'java -jar "{apkeditor_jar}" d -f -i "{target_apk}" -o "{unpacked_name}"'
            tool_prefix = "[APKEditor]"
        else:
            cmd = f'apktool d "{target_apk}" -o "{unpacked_name}" -f'
            tool_prefix = "[Apktool]"

        self.log(f"[*] Starte Entpacken für Smali: {cmd}")

        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            process = subprocess.Popen(cmd, shell=True, cwd=app_source_dir,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, bufsize=1, startupinfo=startupinfo,
                                       errors="replace")

            self.last_log_line = "Starte..."

            def log_reader():
                for line in process.stdout:
                    clean_line = line.strip()
                    if clean_line:
                        self.log(f"{tool_prefix} {clean_line}")
                        self.last_log_line = clean_line

            reader_thread = threading.Thread(target=log_reader, daemon=True)
            reader_thread.start()

            last_size = -1
            stuck_counter = 0

            while process.poll() is None:
                time.sleep(1)
                current_size = self._get_dir_size_mb(smali_dir)

                # Check for frozen compilation (like "Copying original..." hang)
                if current_size == last_size and current_size > 5:
                    stuck_counter += 1
                    if stuck_counter >= 5 and "Copying" in self.last_log_line:
                        self.log("[*] Ordner wächst nicht mehr. Beende blockierenden Prozess...")
                        process.terminate()
                        break
                else:
                    stuck_counter = 0
                    last_size = current_size

            reader_thread.join(timeout=1.0)

            if process.returncode in [0, 1, None]:
                self.log(f"[+] '{target_apk}' erfolgreich entpackt nach: {smali_dir}")
                return True
            else:
                self.log(f"[!] Fehler beim Entpacken (Exit {process.returncode}).")
                return False

        except Exception as e:
            self.log(f"[!] Ausnahme beim Entpacken: {e}")
            return False

    # -------------------------------------------------------------

    def _mirror_workspace(self):
        """Kopiert den entpackten Ordner in die Destination."""
        folder_name = self.get_unpacked_dir_name()
        src = os.path.join(self.cfg.paths["APP_SOURCE_DIR"], folder_name)
        dst = os.path.join(self.cfg.paths["DEST_DIR"], folder_name)

        if not os.path.exists(src):
            self.log(
                f"[!] Original-Ordner '{folder_name}' fehlt in Source. Hast du die APK mit dieser Strategie entpackt?")
            self.log(
                "[!] Klicke oben links auf 'APK Entpacken & Indexieren', um den Ordner für diese Strategie zu erstellen.")
            return False

        self.log(f"[*] Synchronisiere '{folder_name}' in den Destination-Workspace...")
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            self.log("[+] Arbeitskopie erfolgreich synchronisiert.")
            return True
        except Exception as e:
            self.log(f"[!] Fehler beim Spiegeln: {e}")
            return False

    def _inject_nsc(self):
        """Injiziert automatisch eine Network Security Config und entfernt Split-Zwänge für AAPT2."""
        folder_name = self.get_unpacked_dir_name()
        dest_dir = os.path.join(self.cfg.paths["DEST_DIR"], folder_name)
        manifest_path = os.path.join(dest_dir, "AndroidManifest.xml")

        if not os.path.exists(manifest_path):
            self.log("[!] AndroidManifest.xml nicht gefunden. Überspringe NSC-Injection.")
            return False

        self.log("[*] Injiziere Network Security Config für Mitmproxy (User-Certs)...")

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
            self.log("[!] FEHLER: AndroidManifest.xml ist binär kompiliert (AXML)!")
            self.log("[!] NSC-Injection übersprungen. Bitte entpacke die App neu (ohne '-r' Flag).")
            return True

        import re
        match = re.search(r'android:networkSecurityConfig="@xml/([^"]+)"', manifest_data)

        strategy_name = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")
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

        # --- 1. Zertifikats-Injection ---
        if match:
            existing_nsc_name = match.group(1)
            existing_nsc_path = os.path.join(xml_dir, f"{existing_nsc_name}.xml")
            with open(existing_nsc_path, "w", encoding="utf-8") as f:
                f.write(nsc_content)
            self.log(f"[+] Existierende NSC '{existing_nsc_name}.xml' mit User-Cert-Trust überschrieben!")
        else:
            nsc_path = os.path.join(xml_dir, "kippy_nsc.xml")
            with open(nsc_path, "w", encoding="utf-8") as f:
                f.write(nsc_content)
            manifest_data = manifest_data.replace("<application ",
                                                  '<application android:networkSecurityConfig="@xml/kippy_nsc" ')
            self.log("[+] Manifest erfolgreich gepatcht (kippy_nsc hinzugefügt)!")
            manifest_changed = True

        # --- 2. Split-Zwang (NUR für AAPT2) ---
        if strategy_name == "aapt2":
            app_source_dir = self.cfg.paths.get("APP_SOURCE_DIR", "")
            has_merged_base = os.path.exists(os.path.join(app_source_dir, "merged_base.apk"))
            apks = [f for f in os.listdir(app_source_dir) if f.endswith(".apk") and f != "merged_base.apk"]

            if has_merged_base or len(apks) > 1:
                original_len = len(manifest_data)

                # a) Entfernt <meta-data android:name="com.android.vending.splits.required" ... />
                #    Nutze re.DOTALL und re.IGNORECASE, um Zeilenumbrüche etc. abzufangen.
                manifest_data = re.sub(
                    r'<meta-data[^>]*?android:name="com\.android\.vending\.splits\.required"[^>]*?>\s*</meta-data>|<meta-data[^>]*?android:name="com\.android\.vending\.splits\.required"[^>]*?/>',
                    '', manifest_data, flags=re.IGNORECASE | re.DOTALL)

                # b) Entfernt android:requiredSplitTypes="..." aus dem <manifest>-Tag
                manifest_data = re.sub(r'\s*android:requiredSplitTypes="[^"]*"', '', manifest_data)

                # c) Entfernt android:splitTypes="..." aus dem <manifest>-Tag
                manifest_data = re.sub(r'\s*android:splitTypes="[^"]*"', '', manifest_data)

                # d) Entfernt android:isSplitRequired="true" (Sicherheitshalber)
                manifest_data = re.sub(r'\s*android:isSplitRequired="[^"]*"', '', manifest_data)

                # e) Native Libs Fix
                if 'android:extractNativeLibs="false"' in manifest_data:
                    manifest_data = manifest_data.replace('android:extractNativeLibs="false"',
                                                          'android:extractNativeLibs="true"')
                elif 'android:extractNativeLibs="true"' not in manifest_data:
                    manifest_data = manifest_data.replace("<application ",
                                                          '<application android:extractNativeLibs="true" ')

                if len(manifest_data) != original_len:
                    self.log(
                        "[+] Split-Zwang (isSplitRequired/splitTypes/meta-data) für AAPT2-Universal-Build aus Manifest entfernt!")
                    manifest_changed = True

        # --- 3. Manifest speichern ---
        if manifest_changed:
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write(manifest_data)

        return True

    def _apply_smart_patches(self):
        """Wendet Patches an. Bei Abweichungen der Decompiler-Formatierung wird der Nutzer interaktiv gefragt."""
        patches = [p for p in self.get_patches() if p.get("type") == "smali"]
        if not patches:
            self.log("[*] Keine Smali-Patches definiert, überspringe.")
            return True

        folder_name = self.get_unpacked_dir_name()
        src_dir = os.path.join(self.cfg.paths.get("APP_SOURCE_DIR", ""), folder_name)
        dst_dir = os.path.join(self.cfg.paths.get("DEST_DIR", ""), folder_name)

        target_tool = "apkeditor" if self.cfg.config.get("MANIFEST_STRATEGY",
                                                         "smali_only") == "apkeditor" else "apktool"

        for idx, p in enumerate(patches):
            rel_file = p.get("file", "").replace("\\", "/")
            parts = rel_file.split("/")

            # --- DER INTELLIGENTE PFAD-KONVERTER ---
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
            # ----------------------------------------

            src_file = os.path.join(src_dir, actual_rel_file)
            dst_file = os.path.join(dst_dir, actual_rel_file)

            if not os.path.exists(src_file):
                self.log(f"[!] Originaldatei nicht in Source gefunden: {actual_rel_file}")
                return False

            if os.path.exists(src_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)

            try:
                import re
                with open(dst_file, "r", encoding="utf-8") as f:
                    content = f.read()

                orig_block = p.get("orig", "").replace("\r\n", "\n")
                edit_block = p.get("edit", "").replace("\r\n", "\n")
                content = content.replace("\r\n", "\n")

                if orig_block in content:
                    content = content.replace(orig_block, edit_block)
                    self.log(f"[*] Smali-Patch {idx + 1} erfolgreich in '{actual_rel_file}' angewendet.")
                else:
                    method_match = re.search(r'^(\.method\s+[^\n]+)', orig_block, re.MULTILINE)
                    if method_match:
                        method_sig = method_match.group(1).strip()
                        actual_method_pattern = re.compile(r'^' + re.escape(method_sig) + r'.*?^\.end method',
                                                           re.MULTILINE | re.DOTALL)
                        actual_match = actual_method_pattern.search(content)

                        if actual_match:
                            actual_block = actual_match.group(0)

                            msg = f"Patch {idx + 1} weicht von der Datei ab!\n\n"
                            msg += f"Datei: {actual_rel_file}\n"
                            msg += f"Methode: {method_sig}\n\n"
                            msg += "Die Decompiler-Formatierung (.line-Nummern, Kommentare) unterscheidet sich.\n\n"
                            msg += "Soll die Methode trotzdem überschrieben werden?\n\n"
                            msg += "[Ja] = Patch anwenden (Überschreiben)\n"
                            msg += "[Nein] = Diesen Patch überspringen\n"
                            msg += "[Abbrechen] = Pipeline sofort stoppen"

                            answer = messagebox.askyesnocancel("Patch Abweichung erkannt", msg)

                            if answer is True:
                                content = content.replace(actual_block, edit_block)
                                self.log(f"[*] Smali-Patch {idx + 1} (Fuzzy) durch Nutzer bestätigt und angewendet.")
                            elif answer is False:
                                self.log(f"[*] Smali-Patch {idx + 1} vom Nutzer absichtlich übersprungen.")
                                continue
                            else:
                                self.log(f"[!] Pipeline durch Nutzer bei Patch {idx + 1} abgebrochen.")
                                return False
                        else:
                            self.log(
                                f"[!] Methodensignatur '{method_sig}' für Patch {idx + 1} in '{actual_rel_file}' nicht gefunden!")
                            return False
                    else:
                        self.log(
                            f"[!] Original-Block in '{actual_rel_file}' nicht gefunden und keine .method Signatur erkannt!")
                        return False

                with open(dst_file, "w", encoding="utf-8") as f:
                    f.write(content)

            except Exception as e:
                self.log(f"[!] Fehler beim Anwenden von Smali-Patch: {e}")
                return False

        return True

    def _run_cmd_step(self, step):
        cmd_template = step.get("cmd", "")
        cwd_template = step.get("cwd", "{BASE_DIR}")

        extra_vars = {}
        if "{SIGNED_APKS}" in cmd_template:
            dest = self.cfg.paths["DEST_DIR"]
            apks = [f for f in os.listdir(dest) if f.endswith("-aligned-debugSigned.apk")]
            if not apks:
                self.log("[!] Keine signierten APKs gefunden.")
                return False
            extra_vars["SIGNED_APKS"] = " ".join(apks)

        cmd = self._format(cmd_template, extra_vars)
        cwd = self._format(cwd_template)

        if not os.path.exists(cwd):
            os.makedirs(cwd, exist_ok=True)

        self.log(f"> [{cwd}]\n> {cmd}")
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            log_file = os.path.join(self.cfg.paths["ARCHIVE_DIR"], "live_cmd_log.txt")

            with open(log_file, "w", encoding="utf-8") as out_f:
                process = subprocess.Popen(cmd, shell=True, cwd=cwd,
                                           stdout=out_f, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL,
                                           startupinfo=startupinfo)

                with open(log_file, "r", encoding="utf-8") as in_f:
                    while process.poll() is None:
                        line = in_f.readline()
                        if line:
                            self.log(line.strip())
                        else:
                            time.sleep(0.05)

                    for line in in_f.readlines():
                        if line.strip(): self.log(line.strip())

            return process.returncode == 0
        except Exception as e:
            self.log(f"[!] Systemfehler: {e}")
            return False

    def _apply_hex_patches(self):
        lib_path = os.path.join(self.cfg.paths["EXTRACT_DIR"], "lib", "arm64-v8a", "libflutter.so")
        patches = [p for p in self.get_patches() if p.get("type") == "hex"]

        if not patches:
            self.log("[*] Keine Hex-Patches definiert, überspringe.")
            return True

        if not os.path.exists(lib_path):
            self.log(f"[!] Bibliothek nicht gefunden: {lib_path}")
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
                    self.log(f"[*] Hex-Patch {idx + 1}: Offset 0x{offset:X}")
                    f.seek(offset)
                    f.write(bytes.fromhex(patch_hex))
            return True
        except Exception as e:
            self.log(f"[!] Patch-Fehler: {e}")
            return False

    def _start_trace_step(self, step):
        app_pkg = self.cfg.config.get("APP_PACKAGE", "")
        adb_bin = self.cfg.paths.get("ADB", "adb")
        pid_res = subprocess.run(f'"{adb_bin}" shell pidof {app_pkg}', shell=True, capture_output=True, text=True)
        pid = pid_res.stdout.strip()

        if not pid:
            self.log("[!] PID nicht gefunden. Läuft die App?")
            return False

        archive_path = self.get_archive_path()
        if not archive_path: archive_path = self.cfg.paths["ARCHIVE_DIR"]
        trace_file = os.path.join(archive_path, "trace.txt")

        self.logcat_out = open(trace_file, "w")
        cmd = self._format(step.get("cmd", ""), {"PID": pid})
        cwd = self._format(step.get("cwd", "{BASE_DIR}"))

        self.log(f"> [{cwd}]\n> {cmd}")
        self.logcat_process = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=self.logcat_out,
                                               stderr=subprocess.STDOUT)
        self.log(f"[*] Trace gestartet (PID: {pid}). Output in {trace_file}")
        return True

    def _stop_trace_step(self):
        if self.logcat_process:
            self.logcat_process.terminate()
            self.logcat_out.close()
            self.logcat_process = None
            self.log("[*] Trace gestoppt.")
        else:
            self.log("[*] Kein aktiver Trace zum Stoppen.")
        return True

    def _format(self, text, extra_vars=None):
        vars_dict = self.cfg.get_format_vars()
        if extra_vars: vars_dict.update(extra_vars)
        for k, v in vars_dict.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text

    def _run_manifest_and_build_strategy(self):
        strategy_name = self.cfg.config.get("MANIFEST_STRATEGY", "smali_only")

        if strategy_name == "aapt2":
            strategy = Aapt2Strategy()
        elif strategy_name == "apkeditor":
            strategy = ApkEditorStrategy()
        else:
            strategy = SmaliOnlyStrategy()

        self.log(f"\n[*] Lade Manifest-Strategie: {strategy.__class__.__name__}")

        if not strategy.pre_process(self): return False
        if not strategy.patch_manifest(self): return False

        # --- HERMES OOM FIX: Kompression für JS-Bundles deaktivieren ---
        yml_path = os.path.join(self.cfg.paths["DEST_DIR"], self.get_unpacked_dir_name(), "apktool.yml")
        if os.path.exists(yml_path):
            with open(yml_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Prüfen ob doNotCompress existiert und Erweiterungen hinzufügen
            if "doNotCompress:" in content:
                for ext in ["bundle", "hbc"]:
                    if f"- {ext}" not in content:
                        content = content.replace("doNotCompress:\n", f"doNotCompress:\n- {ext}\n")

                with open(yml_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self.log("[+] Hermes-Fix: Kompression für JS-Bundles in apktool.yml deaktiviert.")
        # ---------------------------------------------------------------

        if not strategy.build(self): return False

        return True