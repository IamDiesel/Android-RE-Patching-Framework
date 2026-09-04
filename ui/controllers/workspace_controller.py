import os
import json
import shutil
import datetime
import threading
import subprocess
from tkinter import messagebox, simpledialog

from core.infrastructure.command_runner import CommandRunner
from services.frida_service import FridaManager
from ui.dialogs.frida_manager_dialog import FridaManagerDialog
from services.favorite_service import FavoriteService


class WorkspaceController:
    def __init__(self, view, app):
        self.view = view
        self.app = app

    def _disable_build_buttons(self):
        self.view.btn_build.config(state="disabled")
        self.view.btn_flash.config(state="disabled")
        self.view.btn_1click.config(state="disabled")

    def _enable_build_buttons(self):
        self.view.btn_build.config(state="normal")
        self.view.btn_flash.config(state="normal")
        self.view.btn_1click.config(state="normal")

    def _copy_signed_apks_to_archive(self):
        dest_dir = self.app.cfg.paths.get("DEST_DIR", "")
        if os.path.exists(dest_dir) and hasattr(self.app, 'current_archive_path'):
            for f in os.listdir(dest_dir):
                if f.endswith("-debugSigned.apk"):
                    shutil.copy(os.path.join(dest_dir, f), self.app.current_archive_path)

    def run_uninstall(self, silent=False):
        def task():
            if not silent and self.app.check_lock(): return
            pkg = self.app.cfg.config.get("APP_PACKAGE", "")
            adb = self.app.cfg.paths.get("ADB", "adb")
            base_dir = self.app.cfg.config.get("BASE_DIR", "")

            self.app.log(f"\n[*] Deinstalliere App: {pkg} ...")
            res = CommandRunner.run_blocking(f'"{adb}" uninstall {pkg}', cwd=base_dir)

            if res.returncode == 0 or "Success" in res.stdout:
                self.app.log(f"[+] {pkg} erfolgreich deinstalliert.")
                if not silent:
                    self.app.after(0, lambda: messagebox.showinfo("Uninstall",
                                                                  f"Die App '{pkg}' wurde erfolgreich vom Gerät entfernt."))
            else:
                self.app.log(f"[-] Deinstallation fehlgeschlagen oder App nicht installiert.")

        if silent:
            task()
        else:
            threading.Thread(target=task, daemon=True).start()

    def run_full_chain(self):
        if self.app.check_lock(): return
        self.view.sync_ui_to_state()
        self._disable_build_buttons()

        def task():
            self.app.is_unpacking = True

            if self.view.var_uninstall.get():
                self.run_uninstall(silent=True)

            self.app.log("\n=== PIPELINE START: BUILD_NATIVE ===")
            build_success = self.app.engine.run_pipeline("BUILD_NATIVE")

            if build_success:
                self.app.log("\n=== PIPELINE START: FLASH ===")
                flash_success = self.app.engine.run_pipeline("FLASH")
                if flash_success:
                    self._copy_signed_apks_to_archive()
                    self.app.after(0, lambda: messagebox.showinfo("Erfolg",
                                                                  "1-Click Pipeline erfolgreich abgeschlossen!\nDie gepatchte App ist jetzt auf dem Gerät installiert und startbereit."))
                else:
                    self.app.after(0, lambda: messagebox.showerror("Fehler",
                                                                   "Build war erfolgreich, aber das Installieren (Flash) via ADB ist fehlgeschlagen."))
            else:
                self.app.after(0, lambda: messagebox.showerror("Fehler",
                                                               "Build fehlgeschlagen. Die Pipeline wurde abgebrochen."))

            self.app.is_unpacking = False
            self.app.after(0, self._enable_build_buttons)

        threading.Thread(target=task, daemon=True).start()

    def run_build(self):
        if self.app.check_lock(): return
        self.view.sync_ui_to_state()
        self._disable_build_buttons()
        self.app.log("\n=== PIPELINE START: BUILD_NATIVE ===")

        def task():
            self.app.is_unpacking = True
            success = self.app.engine.run_pipeline("BUILD_NATIVE")
            self.app.is_unpacking = False
            self.app.after(0, self._enable_build_buttons)
            if success:
                self.app.after(0, lambda: messagebox.showinfo("Build", "BUILD_NATIVE erfolgreich abgeschlossen!"))

        threading.Thread(target=task, daemon=True).start()

    def run_flash(self):
        if self.app.check_lock(): return
        self._disable_build_buttons()
        self.app.log("\n=== PIPELINE START: FLASH ===")

        def task():
            self.app.is_unpacking = True
            success = self.app.engine.run_pipeline("FLASH")
            self.app.is_unpacking = False
            self.app.after(0, self._enable_build_buttons)
            if success:
                self._copy_signed_apks_to_archive()
                self.app.after(0, lambda: messagebox.showinfo("Flash", "FLASH erfolgreich abgeschlossen!"))

        threading.Thread(target=task, daemon=True).start()

    def push_clean_apk(self):
        def task():
            self.app.log("\n=== RASP SPOOFER: ORIGINAL APK PUSH ===")
            source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
            adb = self.app.cfg.paths.get("ADB", "adb")
            clean_apk = os.path.join(source_dir, "base.apk")

            if not os.path.exists(clean_apk):
                self.app.log("[!] FEHLER: Originale base.apk im Source-Ordner nicht gefunden!")
                return

            self.app.log(f"[*] Nutze saubere APK: {os.path.basename(clean_apk)}")
            cmds = [
                f'"{adb}" push "{clean_apk}" /data/local/tmp/clean_base.apk',
                f'"{adb}" shell "chmod 777 /data/local/tmp/clean_base.apk"'
            ]

            success = True
            for cmd in cmds:
                self.app.log(f"> {cmd}")
                try:
                    startupinfo = None
                    if os.name == 'nt':
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, startupinfo=startupinfo)
                    if res.stdout: self.app.log(res.stdout.strip())
                    if res.stderr: self.app.log(res.stderr.strip())

                    if res.returncode != 0:
                        self.app.log(f"[!] Befehl fehlgeschlagen mit Code {res.returncode}")
                        success = False
                        break
                except Exception as e:
                    self.app.log(f"[!] CMD Fehler: {e}")
                    success = False
                    break

            if success:
                self.app.log("[+] Originale base.apk erfolgreich als Clean APK platziert!")
                self.app.after(0, lambda: messagebox.showinfo("Erfolg",
                                                              "Die originale APK wurde erfolgreich auf dem Gerät platziert."))
            else:
                self.app.log("[!] Fehler beim Pushen der Clean APK.")
                self.app.after(0, lambda: messagebox.showerror("Fehler",
                                                               "Fehler beim Pushen der APK. Siehe Konsole für Details."))

        threading.Thread(target=task, daemon=True).start()

    def save_current_as_favorite(self):
        name = simpledialog.askstring("Favorit sichern", "Gib einen Namen für diesen Favoriten ein:")
        if not name: return

        active_patches = self.view.get_all_patches()
        if not active_patches:
            return messagebox.showwarning("Leer", "Es gibt aktuell keine aktiven Patches zum Sichern.")

        fav_service = FavoriteService(self.app.cfg.config.get("BASE_DIR", ""))
        new_fav = {
            "name": name,
            "comment": "Gesichert am " + datetime.datetime.now().strftime('%Y-%m-%d'),
            "date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "patches": active_patches
        }

        fav_service.add_favorite(new_fav)
        messagebox.showinfo("Gesichert", f"Favorit '{name}' mit {len(active_patches)} Patches erfolgreich hinterlegt!")

    def open_frida_manager(self):
        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        fm = FridaManager(base_dir)

        def on_script_changed():
            self.app.log("[*] Aktives Frida-Skript wurde geändert.")

        FridaManagerDialog(self.view, fm, on_script_changed)

    def attach_frida(self):
        def task():
            self.app.engine.attach_frida_usb()

        threading.Thread(target=task, daemon=True).start()

    def toggle_frida(self, state: bool):
        self.app.cfg.config["INJECT_FRIDA"] = state
        self.app.cfg.save()
        status = "aktiviert" if state else "deaktiviert"
        self.app.log(f"[*] Frida-Injection für nächsten Build {status}.")

    def toggle_lspatch(self, state: bool):
        self.app.cfg.config["INJECT_LSPATCH"] = state
        self.app.cfg.save()
        status = "aktiviert" if state else "deaktiviert"
        self.app.log(f"[*] LSPatch-Injection für nächsten Build {status}.")

    def toggle_nsc(self, state: bool):
        self.app.cfg.config["INJECT_NSC"] = state
        self.app.cfg.save()
        status = "aktiviert" if state else "deaktiviert"
        self.app.log(f"[*] MITM Cert Injection (NSC) für nächsten Build {status}.")

    def toggle_debuggable(self, state: bool):
        self.app.cfg.config["INJECT_DEBUGGABLE"] = state
        self.app.cfg.save()
        status = "aktiviert" if state else "deaktiviert"
        self.app.log(f"[*] Debuggable-Flag für nächsten Build {status}.")

    def change_manifest_strategy(self, strategy: str):
        self.app.cfg.config["MANIFEST_STRATEGY"] = strategy
        self.app.cfg.save()
        self.app.log(f"[*] Manifest-Strategie global auf '{strategy}' geändert.")

    def change_native_lib_strategy(self, strategy: str):
        self.app.cfg.config["NATIVE_LIB_STRATEGY"] = strategy
        self.app.cfg.save()
        self.app.log(f"[*] Native Libs Strategie global auf '{strategy}' geändert.")

    def save_session_result(self, name, version, result, observation, patches):
        record = {
            "id": self.app.current_id,
            "name": name,
            "app_package": self.app.cfg.config.get("APP_PACKAGE", "Unbekannt"),
            "app_version": version,
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "result": result,
            "observation": observation,
            "patches": patches
        }
        self.app.history.add_record(record)
        messagebox.showinfo("Historie", f"Session {self.app.current_id} erfolgreich gesichert!")