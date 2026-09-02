import os
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "BASE_DIR": CURRENT_DIR,
    "SPLIT_NAME": "split_config.arm64_v8a",
    "APP_PACKAGE": "com.datamars.kippynew",
    "SIGNER_JAR": "uber-apk-signer-1.3.0.jar",
    "APKEDITOR_JAR": "APKEditor-1.4.9.jar",
    "MANIFEST_STRATEGY": "apkeditor",
    "NATIVE_LIB_STRATEGY": "zipalign",
    "INJECT_FRIDA": False,
    "INJECT_LSPATCH": False,
    "PIPELINES": {
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
             "cmd": "zipalign -p -f 4 \"{SPLIT_NAME}.apk\" \"{SPLIT_NAME}_aligned.apk\"",
             "cwd": "{DEST_DIR}"},
            {"name": "Overwrite with Aligned APK", "type": "cmd",
             "cmd": "move /Y \"{SPLIT_NAME}_aligned.apk\" \"{SPLIT_NAME}.apk\"",
             "cwd": "{DEST_DIR}"},
            {"name": "Clean old signatures", "type": "cmd", "cmd": "del /Q /S \"*-debugSigned*.apk\" 2>nul",
             "cwd": "{DEST_DIR}"},
            {"name": "Sign all APKs", "type": "cmd",
             "cmd": "java -jar \"{SIGNER_JAR}\" -a . --skipZipAlign --allowResign",
             "cwd": "{DEST_DIR}"}
        ],
        "BUILD_NATIVE": [
            {"name": "Mirror Original Workspace", "type": "mirror_workspace"},
            {"name": "Apply Smali Patches", "type": "smart_patch"},
            {"name": "Inject Custom Libs", "type": "inject_custom_libs"},
            {"name": "Inject Frida Gadget", "type": "inject_frida"},
            {"name": "Manifest & Build (Dynamic Strategy)", "type": "manifest_and_build"},
            {"name": "Apply LSPatch", "type": "apply_lspatch"},
            {"name": "Clean old signatures", "type": "cmd", "cmd": "del /Q /S \"*-debugSigned*.apk\" 2>nul",
             "cwd": "{DEST_DIR}"},
            {"name": "Sign all APKs", "type": "cmd",
             "cmd": "java -jar \"{SIGNER_JAR}\" -a . --skipZipAlign --allowResign",
             "cwd": "{DEST_DIR}"}
        ],
        "FLASH": [
            {"name": "Install to Device", "type": "cmd",
             "cmd": "adb install -r -t -d -i com.android.vending {SIGNED_APKS}", "cwd": "{DEST_DIR}"}
        ],
        "TRACE_START": [
            {"name": "Clear Logcat", "type": "cmd", "cmd": "adb logcat -c", "cwd": "{BASE_DIR}"},
            {"name": "Start Logcat", "type": "trace_start", "cmd": "adb logcat --pid={PID}", "cwd": "{BASE_DIR}"}
        ],
        "TRACE_STOP": [
            {"name": "Stop Logcat", "type": "trace_stop"}
        ]
    }
}


class ConfigManager:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.config = {}
        self.paths = {}
        self.load()

    def load(self, file_path=None):
        if file_path:
            self.config_file = file_path

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self.config = json.load(f)

                if "PIPELINES" not in self.config:
                    self.config["PIPELINES"] = {}
                if "PREPARE_WORKSPACE" not in self.config["PIPELINES"]:
                    self.config["PIPELINES"]["PREPARE_WORKSPACE"] = DEFAULT_CONFIG["PIPELINES"]["PREPARE_WORKSPACE"]

                current_native_steps = [s.get("name") for s in self.config["PIPELINES"].get("BUILD_NATIVE", [])]
                if "Inject Frida Gadget" not in current_native_steps or "Apply LSPatch" not in current_native_steps or "Inject Custom Libs" not in current_native_steps:
                    self.config["PIPELINES"]["BUILD_NATIVE"] = DEFAULT_CONFIG["PIPELINES"]["BUILD_NATIVE"]
            except Exception:
                self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = DEFAULT_CONFIG.copy()
        self._update_paths()

    def save(self, file_path=None):
        target = file_path if file_path else self.config_file
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4)

        if file_path:
            self.config_file = target

        self._update_paths()

    def restore_defaults(self):
        self.config = DEFAULT_CONFIG.copy()
        self.save()

    def _update_paths(self):
        b_dir = self.config.get("BASE_DIR", "")
        app_pkg = self.config.get("APP_PACKAGE", "")
        split = self.config.get("SPLIT_NAME", "")

        adb_full_path = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")
        if not os.path.exists(adb_full_path):
            adb_full_path = "adb"

        self.paths = {
            "SOURCE_DIR": os.path.join(b_dir, "source"),
            "APP_SOURCE_DIR": os.path.join(b_dir, "source", app_pkg),
            "DEST_DIR": os.path.join(b_dir, "destination", app_pkg),
            "ARCHIVE_DIR": os.path.join(b_dir, "archives"),
            "LOG_FILE": os.path.join(b_dir, "Kippy_RE_Log.md"),
            "JSON_HISTORY": os.path.join(b_dir, "RE_History.json"),
            "EXTRACT_DIR": os.path.join(b_dir, "source", app_pkg, split),
            "API_DB": os.path.join(b_dir, "api_traffic.db"),
            "API_RULES": os.path.join(b_dir, "intercept_rules.json"),
            "ADB": adb_full_path
        }

        for d in ["SOURCE_DIR", "DEST_DIR", "ARCHIVE_DIR", "APP_SOURCE_DIR"]:
            os.makedirs(self.paths[d], exist_ok=True)

    def get_format_vars(self):
        vars_dict = self.paths.copy()
        vars_dict.update({
            "BASE_DIR": self.config.get("BASE_DIR", ""),
            "SPLIT_NAME": self.config.get("SPLIT_NAME", ""),
            "APP_PACKAGE": self.config.get("APP_PACKAGE", ""),
            "SIGNER_JAR": os.path.join(self.config.get("BASE_DIR", ""), self.config.get("SIGNER_JAR", "")),
            "APKEDITOR_JAR": os.path.join(self.config.get("BASE_DIR", ""), self.config.get("APKEDITOR_JAR", ""))
        })
        return vars_dict