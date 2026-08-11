import os
import json

DEFAULT_CONFIG = {
    "BASE_DIR": r"C:\Users\Lenovo\Downloads\APK-Tools\Script",
    "SPLIT_NAME": "split_config.arm64_v8a",
    "APP_PACKAGE": "com.datamars.kippynew",
    "SIGNER_JAR": "uber-apk-signer-1.3.0.jar",
    "PIPELINES": {
        "BUILD": [
            {"name": "Backup original APK", "type": "cmd", "cmd": "copy {SPLIT_NAME}.apk {SPLIT_NAME}.zip", "cwd": "{SOURCE_DIR}"},
            {"name": "Extract APK", "type": "cmd", "cmd": "tar -xf {SPLIT_NAME}.zip -C {SPLIT_NAME}", "cwd": "{SOURCE_DIR}"},
            {"name": "Apply Hex Patches", "type": "anchor_patch"},
            {"name": "Repack APK", "type": "cmd", "cmd": "jar c0f {SPLIT_NAME}.apk AndroidManifest.xml lib stamp-cert-sha256 META-INF", "cwd": "{EXTRACT_DIR}"},
            {"name": "Move repacked APK", "type": "cmd", "cmd": "move {SPLIT_NAME}.apk {DEST_DIR}\\{SPLIT_NAME}.apk", "cwd": "{EXTRACT_DIR}"},
            {"name": "Sign APKs", "type": "cmd", "cmd": "java -jar {SIGNER_JAR} -a . --allowResign", "cwd": "{DEST_DIR}"}
        ],
        "FLASH": [
            {"name": "Install to Device", "type": "cmd", "cmd": "adb install-multiple -i com.android.vending {SIGNED_APKS}", "cwd": "{DEST_DIR}"}
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
        self.paths = {
            "SOURCE_DIR": os.path.join(b_dir, "source"),
            "DEST_DIR": os.path.join(b_dir, "destination"),
            "ARCHIVE_DIR": os.path.join(b_dir, "archives"),
            "LOG_FILE": os.path.join(b_dir, "Kippy_RE_Log.md"),
            "JSON_HISTORY": os.path.join(b_dir, "RE_History.json"),
            "EXTRACT_DIR": os.path.join(b_dir, "source", self.config.get("SPLIT_NAME", "")),
            "API_DB": os.path.join(b_dir, "api_traffic.db"),
            "API_RULES": os.path.join(b_dir, "intercept_rules.json")
        }
        for d in ["SOURCE_DIR", "DEST_DIR", "ARCHIVE_DIR"]:
            os.makedirs(self.paths[d], exist_ok=True)

    def get_format_vars(self):
        vars_dict = self.paths.copy()
        vars_dict.update({
            "BASE_DIR": self.config.get("BASE_DIR", ""),
            "SPLIT_NAME": self.config.get("SPLIT_NAME", ""),
            "APP_PACKAGE": self.config.get("APP_PACKAGE", ""),
            "SIGNER_JAR": os.path.join(self.config.get("BASE_DIR", ""), self.config.get("SIGNER_JAR", ""))
        })
        return vars_dict
