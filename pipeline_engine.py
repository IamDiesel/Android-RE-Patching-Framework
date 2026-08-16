import os
import subprocess
import time
import shutil
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
        cmd = "apktool b base_unpacked -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class Aapt2Strategy(ManifestBuildStrategy):
    def pre_process(self, engine):
        engine.log("[*] AAPT2-Strategie: Achtung! Setzt Universal-APK oder fehlerfreie Ressourcen voraus.")
        return True

    def patch_manifest(self, engine):
        return engine._inject_nsc()

    def build(self, engine):
        engine.log("[*] Baue App streng mit AAPT2...")
        # FIX: --use-aapt2 entfernt, da es in v3 Standard ist
        cmd = "apktool b base_unpacked -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class ApkEditorStrategy(ManifestBuildStrategy):
    def pre_process(self, engine):
        engine.log("[*] APKEditor-Strategie: Native AXML/ARSC Kompilierung.")
        return True

    def patch_manifest(self, engine):
        return engine._inject_nsc()

    def build(self, engine):
        engine.log("[*] Baue App mit APKEditor (ohne AAPT2)...")
        # FIX: Das '-f' Flag hinzugefügt, damit die alte base.apk gnadenlos überschrieben wird!
        cmd = "java -jar \"{APKEDITOR_JAR}\" b -f -i base_unpacked -o base.apk"
        return engine._run_cmd_step({"cmd": cmd, "cwd": "{DEST_DIR}"})


class PipelineEngine:
    def __init__(self, config_mgr, logger_func, get_patches_func, get_archive_path_func):
        self.cfg = config_mgr
        self.log = logger_func
        self.get_patches = get_patches_func
        self.get_archive_path = get_archive_path_func
        self.logcat_process = None
        self.logcat_out = None

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
            elif step_type == "manifest_and_build":  # <--- Dynamischer Strategie-Aufruf
                success = self._run_manifest_and_build_strategy()
            elif step_type == "anchor_patch":
                success = self._apply_hex_patches()
            elif step_type == "smart_patch":
                success = self._apply_smart_patches()
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

    def _mirror_workspace(self):
        """Kopiert den entpackten Ordner in die Destination (überschreibt bestehende Dateien für einen sauberen Build)."""
        src = os.path.join(self.cfg.paths["APP_SOURCE_DIR"], "base_unpacked")
        dst = os.path.join(self.cfg.paths["DEST_DIR"], "base_unpacked")

        if not os.path.exists(src):
            self.log("[!] Original-Ordner fehlt in Source. Hast du die APK entpackt?")
            return False

        self.log("[*] Synchronisiere Original-Dateien in den Destination-Workspace...")
        try:
            # FIX: dirs_exist_ok=True erzwingt das Kopieren/Überschreiben, auch wenn der Ordner (durch die .pkl) schon existiert!
            shutil.copytree(src, dst, dirs_exist_ok=True)
            self.log("[+] Arbeitskopie erfolgreich synchronisiert.")
            return True
        except Exception as e:
            self.log(f"[!] Fehler beim Spiegeln: {e}")
            return False

    def _inject_nsc(self):
        """Injiziert automatisch eine Network Security Config, um User-Zertifikate (Mitmproxy) zu erlauben."""
        dest_dir = os.path.join(self.cfg.paths["DEST_DIR"], "base_unpacked")
        manifest_path = os.path.join(dest_dir, "AndroidManifest.xml")
        xml_dir = os.path.join(dest_dir, "res", "xml")
        nsc_path = os.path.join(xml_dir, "kippy_nsc.xml")

        if not os.path.exists(manifest_path):
            self.log("[!] AndroidManifest.xml nicht gefunden. Überspringe NSC-Injection.")
            return False

        self.log("[*] Injiziere Network Security Config für Mitmproxy (User-Certs)...")

        # 1. Freizügige XML-Datei anlegen
        os.makedirs(xml_dir, exist_ok=True)
        nsc_content = '''<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>'''

        # 2. Manifest parsen und anpassen
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_data = f.read()
        except UnicodeDecodeError:
            self.log("[!] FEHLER: AndroidManifest.xml ist binär kompiliert (AXML)!")
            self.log("[!] NSC-Injection übersprungen. Bitte entpacke die App neu (ohne '-r' Flag).")
            # Wir brechen den Build nicht ab, überspringen aber das Injizieren
            return True

        import re
        match = re.search(r'android:networkSecurityConfig="@xml/([^"]+)"', manifest_data)

        if match:
            existing_nsc_name = match.group(1)
            existing_nsc_path = os.path.join(xml_dir, f"{existing_nsc_name}.xml")
            with open(existing_nsc_path, "w", encoding="utf-8") as f:
                f.write(nsc_content)
            self.log(f"[+] Existierende NSC '{existing_nsc_name}.xml' mit User-Cert-Trust überschrieben!")
        else:
            with open(nsc_path, "w", encoding="utf-8") as f:
                f.write(nsc_content)
            manifest_data = manifest_data.replace("<application ",
                                                  '<application android:networkSecurityConfig="@xml/kippy_nsc" ')
            with open(manifest_path, "w", encoding="utf-8") as f:
                f.write(manifest_data)
            self.log("[+] Manifest erfolgreich gepatcht (kippy_nsc hinzugefügt)!")

        return True

    def _apply_smart_patches(self):
        """Wendet Patches sicher in der Destination an (inklusive Undo-Funktion)."""
        patches = [p for p in self.get_patches() if p.get("type") == "smali"]
        if not patches:
            self.log("[*] Keine Smali-Patches definiert, überspringe.")
            return True

        src_dir = os.path.join(self.cfg.paths.get("APP_SOURCE_DIR", ""), "base_unpacked")
        dst_dir = os.path.join(self.cfg.paths.get("DEST_DIR", ""), "base_unpacked")

        for idx, p in enumerate(patches):
            rel_file = p.get("file", "")
            src_file = os.path.join(src_dir, rel_file)
            dst_file = os.path.join(dst_dir, rel_file)

            # 1. Single-File Reset: Garantiert sauberen Status für entfernte/bearbeitete Patches
            if os.path.exists(src_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)
            else:
                self.log(f"[!] Originaldatei nicht in Source gefunden: {src_file}")
                return False

            # 2. Patchvorgang auf der Destination-Datei
            try:
                with open(dst_file, "r", encoding="utf-8") as f:
                    content = f.read()

                orig_block = p.get("orig", "").replace("\r\n", "\n")
                edit_block = p.get("edit", "").replace("\r\n", "\n")
                content = content.replace("\r\n", "\n")

                if orig_block not in content:
                    self.log(f"[!] Original-Block in '{rel_file}' nicht gefunden! (Hat sich der Code geändert?)")
                    return False

                content = content.replace(orig_block, edit_block)

                with open(dst_file, "w", encoding="utf-8") as f:
                    f.write(content)
                self.log(f"[*] Smali-Patch {idx + 1} erfolgreich in '{rel_file}' angewendet.")
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
        pid_res = subprocess.run(f"adb shell pidof {app_pkg}", shell=True, capture_output=True, text=True)
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
        if not strategy.build(self): return False

        return True