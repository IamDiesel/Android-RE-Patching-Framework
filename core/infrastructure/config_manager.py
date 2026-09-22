import os
import json
from core.infrastructure import json_store
from core.domain.frida_models import FridaConfig

CURRENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

DEFAULT_CONFIG = {
    "BASE_DIR": CURRENT_DIR,
    "SPLIT_NAME": "split_config.arm64_v8a",
    "APP_PACKAGE": "com.datamars.kippynew",
    "ADB_DEVICE_SERIAL": "",   # leer = Auto (USB-Default); sonst gewaehltes Geraet (ANDROID_SERIAL)
    "SIGNER_JAR": os.path.join("tools", "uber-apk-signer.jar"),
    "APKEDITOR_JAR": os.path.join("tools", "APKEditor.jar"),
    "LSPATCH_JAR": os.path.join("tools", "lspatch.jar"),
    "TRUSTMEALREADY_APK": os.path.join("tools", "TrustMeAlready.apk"),
    "MANIFEST_STRATEGY": "apkeditor",
    "NATIVE_LIB_STRATEGY": "zipalign",
    "NDK_DIR": "",
    "DEFAULT_ABI": "arm64-v8a",
    "DEFAULT_API_LEVEL": 30,
    "NDK_FALLBACK_VERSION": "r27c",
    "NATIVE_MAX_PAGE_SIZE": 16384,  # 16-KB-Page-Alignment fuer gebaute Libs (Android 15/16); 0 = aus
    "EXEC_RUN_DIR_DEFAULT": "/data/local/tmp",  # ExeDeploy: Standard-Run-Dir (exec-erlaubt)
    "EXEC_BACKUP_KEEP": 10,                      # ExeDeploy: max. lokale Sync-Backup-Staende
    "EXEC_COLOR_PER_EXE": True,                  # ExeDeploy: eigene Konsolenfarbe je Executable
    "INJECT_FRIDA": False,
    "INJECT_LSPATCH": False,
    "INJECT_NSC": True,
    "INJECT_DEBUGGABLE": False,
    "MCP_SETTINGS": {
        "server": {"enabled": False, "autostart": False, "host": "127.0.0.1", "port": 8765, "transport": "http"},
        "require_confirm_each_call": True,
        "log_export_dir": os.path.join("Claude outputs", "logs"),
        "audit": {"enabled": True, "file": os.path.join("data", "mcp_audit.jsonl"), "log_args": True},
        "tools": {},
        "caps": {"build": False, "flash": False, "app_start": False, "exec_run": False, "file_manager": False, "delete_ops": False},
        "console": {"max_bytes": 20971520, "clear_on_app_start": True},
    },
    "FRIDA_SETTINGS": FridaConfig().to_dict(),  # NEU: Integration der Frida-Modi
    "PIPELINES": {
        # ... (Pipeline-Konfigurationen bleiben exakt wie vorher)
        "PREPARE_WORKSPACE": [
            {"name": "Merge Split APKs", "type": "merge_splits"},
            {"name": "Decompile APK", "type": "decompile"}
        ],
        "BUILD_FLUTTER": [
            {"name": "Backup original APK", "type": "cmd", "cmd": "copy \"{SPLIT_NAME}.apk\" \"{SPLIT_NAME}.zip\"",
             "cwd": "{APP_SOURCE_DIR}"},
            {"name": "Extract APK (tar)", "type": "cmd", "cmd": "tar -xf \"..\\{SPLIT_NAME}.zip\" -C .",
             "cwd": "{EXTRACT_DIR}"},
            {"name": "Apply Hex Patches", "type": "anchor_patch"},
            {"name": "Repack APK (jar)", "type": "cmd",
             "cmd": "jar c0f \"{SPLIT_NAME}.apk\" AndroidManifest.xml lib stamp-cert-sha256 META-INF",
             "cwd": "{EXTRACT_DIR}"},
            {"name": "Move repacked APK", "type": "cmd",
             "cmd": "move /Y \"{EXTRACT_DIR}\\{SPLIT_NAME}.apk\" \"{DEST_DIR}\\{SPLIT_NAME}.apk\"",
             "cwd": "{BASE_DIR}"},
            {"name": "Zipalign (Page Alignment für .so)", "type": "cmd",
             "cmd": "zipalign -p -f 4 \"{SPLIT_NAME}.apk\" \"{SPLIT_NAME}_aligned.apk\"", "cwd": "{DEST_DIR}"},
            {"name": "Overwrite with Aligned APK", "type": "cmd",
             "cmd": "move /Y \"{SPLIT_NAME}_aligned.apk\" \"{SPLIT_NAME}.apk\"", "cwd": "{DEST_DIR}"},
            {"name": "Clean old signatures", "type": "cmd", "cmd": "del /Q /S \"*-debugSigned*.apk\" 2>nul",
             "cwd": "{DEST_DIR}"},
            {"name": "Sign all APKs", "type": "cmd",
             "cmd": "java -jar \"{SIGNER_JAR}\" -a . --skipZipAlign --allowResign", "cwd": "{DEST_DIR}"}
        ],
        "BUILD_NATIVE": [
            {"name": "Mirror Original Workspace", "type": "mirror_workspace"},
            {"name": "Apply Smali Patches", "type": "smart_patch"},
            {"name": "Inject Custom Libs", "type": "inject_custom_libs"},
            {"name": "Inject Added Libs", "type": "inject_added_libs"},
            {"name": "Inject Frida Gadget", "type": "inject_frida"},
            {"name": "Manifest & Build (Dynamic Strategy)", "type": "manifest_and_build"},
            {"name": "Apply LSPatch", "type": "apply_lspatch"},
            {"name": "Clean old signatures", "type": "cmd", "cmd": "del /Q /S \"*-debugSigned*.apk\" 2>nul",
             "cwd": "{DEST_DIR}"},
            {"name": "Sign all APKs", "type": "cmd",
             "cmd": "java -jar \"{SIGNER_JAR}\" -a . --skipZipAlign --allowResign", "cwd": "{DEST_DIR}"}
        ],
        "FLASH": [
            {"name": "Install to Device", "type": "cmd",
             "cmd": "adb install -r -t -d -i com.android.vending {SIGNED_APKS}", "cwd": "{DEST_DIR}"}
        ],
        "TRACE_START": [
            {"name": "Clear Logcat", "type": "cmd", "cmd": "adb logcat -c", "cwd": "{BASE_DIR}"},
            {"name": "Start Logcat", "type": "trace_start",
             "cmd": "adb logcat --pid={PID} | grep -iE 'fatal|crash|debug|linker|frida|console|CryptoAudit'",
             "cwd": "{BASE_DIR}"}
        ],
        "TRACE_STOP": [
            {"name": "Stop Logcat", "type": "trace_stop"}
        ]
    }
}


class ConfigManager:
    def __init__(self, config_file="config.json"):
        self.config_file = os.path.join(CURRENT_DIR, config_file)
        self.config = {}
        self.paths = {}
        self.frida_config = FridaConfig()  # NEU: Typisiertes Datenmodell initialisieren
        self.load()

    def load(self, file_path=None):
        if file_path:
            self.config_file = file_path

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)

                self.config["BASE_DIR"] = CURRENT_DIR

                # NEU: Lade Frida-Einstellungen in das typsichere Modell
                if "FRIDA_SETTINGS" in self.config:
                    self.frida_config = FridaConfig.from_dict(self.config["FRIDA_SETTINGS"])

                # Pipeline Safety Checks...
                if "PIPELINES" not in self.config:
                    self.config["PIPELINES"] = {}
                if "PREPARE_WORKSPACE" not in self.config["PIPELINES"]:
                    self.config["PIPELINES"]["PREPARE_WORKSPACE"] = DEFAULT_CONFIG["PIPELINES"]["PREPARE_WORKSPACE"]

                current_native_steps = [s.get("name") for s in self.config["PIPELINES"].get("BUILD_NATIVE", [])]
                if "Inject Frida Gadget" not in current_native_steps or "Apply LSPatch" not in current_native_steps or "Inject Custom Libs" not in current_native_steps or "Inject Added Libs" not in current_native_steps:
                    self.config["PIPELINES"]["BUILD_NATIVE"] = DEFAULT_CONFIG["PIPELINES"]["BUILD_NATIVE"]

                # LibForge/ExeDeploy: neue Keys robust nachziehen (bestehende config.json)
                for _k in ("NDK_DIR", "DEFAULT_ABI", "DEFAULT_API_LEVEL", "NDK_FALLBACK_VERSION",
                           "NATIVE_MAX_PAGE_SIZE", "INJECT_NSC",
                           "EXEC_RUN_DIR_DEFAULT", "EXEC_BACKUP_KEEP", "EXEC_COLOR_PER_EXE"):
                    if _k not in self.config:
                        self.config[_k] = DEFAULT_CONFIG[_k]
            except Exception:
                self.config = DEFAULT_CONFIG.copy()
                self.frida_config = FridaConfig()
        else:
            self.config = DEFAULT_CONFIG.copy()
            self.frida_config = FridaConfig()

        self._update_paths()

    def save(self, file_path=None):
        target = file_path if file_path else self.config_file

        # NEU: Synchronisiere das Objekt zurück in das Dict vor dem Speichern
        self.config["FRIDA_SETTINGS"] = self.frida_config.to_dict()

        json_store.atomic_write_text(target, json.dumps(self.config, indent=4))

        if file_path:
            self.config_file = target

        self._update_paths()

    def update_keys(self, partial: dict):
        """Merge-sicheres Setzen einzelner Top-Level-Config-Keys: frische Platte lesen,
        nur die genannten Keys anwenden (dict-Werte sub-key-tief mergen), atomar schreiben.
        Verhindert, dass MCP-Config-Writes gleichzeitige GUI-Aenderungen ueberschreiben."""
        disk = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    disk = json.load(f)
            except Exception:
                disk = {}
        if not isinstance(disk, dict):
            disk = {}
        for k, v in (partial or {}).items():
            if isinstance(v, dict) and isinstance(disk.get(k), dict):
                merged = dict(disk[k]); merged.update(v); disk[k] = merged
            else:
                disk[k] = v
        disk["BASE_DIR"] = CURRENT_DIR
        json_store.atomic_write_text(self.config_file, json.dumps(disk, indent=4))
        self.config = disk
        if "FRIDA_SETTINGS" in disk:
            self.frida_config = FridaConfig.from_dict(disk["FRIDA_SETTINGS"])
        self._update_paths()

    def restore_defaults(self):
        self.config = DEFAULT_CONFIG.copy()
        self.frida_config = FridaConfig()
        self.save()

    def _ensure_mcp_settings(self):
        """Stellt sicher, dass MCP_SETTINGS + alle Unterkeys vorhanden sind (tiefe Merge, ohne Nutzerwerte zu ueberschreiben)."""
        import copy
        defaults = copy.deepcopy(DEFAULT_CONFIG.get("MCP_SETTINGS", {}))
        cur = self.config.get("MCP_SETTINGS")
        if not isinstance(cur, dict):
            self.config["MCP_SETTINGS"] = defaults
            return
        def _merge(d, dd):
            for k, v in dd.items():
                if k not in d:
                    d[k] = v
                elif isinstance(v, dict) and isinstance(d.get(k), dict):
                    _merge(d[k], v)
        _merge(cur, defaults)

    def _update_paths(self):
        self._ensure_mcp_settings()
        b_dir = self.config.get("BASE_DIR", CURRENT_DIR)
        app_pkg = self.config.get("APP_PACKAGE", "")
        split = self.config.get("SPLIT_NAME", "")

        adb_full_path = os.path.join(b_dir, "tools", "platform-tools", "adb.exe")
        if not os.path.exists(adb_full_path):
            adb_full_path = "adb"

        data_dir = os.path.join(b_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        self.paths = {
            "SOURCE_DIR": os.path.join(b_dir, "source"),
            "APP_SOURCE_DIR": os.path.join(b_dir, "source", app_pkg),
            "DEST_DIR": os.path.join(b_dir, "destination", app_pkg),
            "ARCHIVE_DIR": os.path.join(b_dir, "archives"),
            "EXTRACT_DIR": os.path.join(b_dir, "source", app_pkg, split),
            "ADB": adb_full_path,
            "LOG_FILE": os.path.join(data_dir, "Kippy_RE_Log.md"),
            "JSON_HISTORY": os.path.join(data_dir, "RE_History.json"),
            "API_DB": os.path.join(data_dir, "api_traffic.db"),
            "API_RULES": os.path.join(data_dir, "intercept_rules.json")
        }

        for d in ["SOURCE_DIR", "DEST_DIR", "ARCHIVE_DIR", "APP_SOURCE_DIR"]:
            os.makedirs(self.paths[d], exist_ok=True)

    def get_format_vars(self):
        vars_dict = self.paths.copy()
        base_dir = self.config.get("BASE_DIR", CURRENT_DIR)
        vars_dict.update({
            "BASE_DIR": base_dir,
            "SPLIT_NAME": self.config.get("SPLIT_NAME", ""),
            "APP_PACKAGE": self.config.get("APP_PACKAGE", ""),
            "SIGNER_JAR": os.path.join(base_dir,
                                       self.config.get("SIGNER_JAR", os.path.join("tools", "uber-apk-signer.jar"))),
            "APKEDITOR_JAR": os.path.join(base_dir,
                                          self.config.get("APKEDITOR_JAR", os.path.join("tools", "APKEditor.jar")))
        })
        return vars_dict