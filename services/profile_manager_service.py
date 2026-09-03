import os
import json

class ProfileManagerService:
    def __init__(self, config_file: str):
        self.config_file = config_file
        self.profiles = self.load_profiles()

    def load_profiles(self):
        default_profiles = {
            "intents": [
                "monkey -p {APP_PACKAGE} -c android.intent.category.LAUNCHER 1",
                "am start -W -a android.intent.action.VIEW -d \"{APP_NAME}://\"",
                "am start -W -a android.intent.action.VIEW -d \"{APP_NAME}://debug\"",
                "am start -W -a android.intent.action.VIEW -d \"{APP_NAME}://dashboard\"",
                "am start -W -a android.intent.action.VIEW -d \"{APP_NAME}://admin\"",
                "am start -W -a android.intent.action.VIEW -d \"{APP_NAME}://login?bypass=true\""
            ],
            "logcats": [
                "logcat | grep -iE '{APP_NAME}|fatal|crash|debug|linker|frida|console'",
                "logcat --pid={PID}",
                "logcat --pid={PID} | grep -iE 'frida|ssl|crypto|keystore|network|http|intercept'",
                "logcat --pid={PID} | grep -iE 'fatal|crash|exception|error'",
                "logcat *:E",
                "logcat"
            ]
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    default_profiles["intents"].extend(
                        [x for x in data.get("intents", []) if x not in default_profiles["intents"]])
                    default_profiles["logcats"].extend(
                        [x for x in data.get("logcats", []) if x not in default_profiles["logcats"]])
            except Exception:
                pass
        return default_profiles

    def save_profiles(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.profiles, f, indent=4)

    def add_template(self, group: str, val: str):
        val = val.strip()
        if val and val not in self.profiles[group]:
            self.profiles[group].append(val)
            self.save_profiles()
            return True
        return False

    def remove_template(self, group: str, val: str):
        val = val.strip()
        if val in self.profiles[group]:
            self.profiles[group].remove(val)
            self.save_profiles()
            return True
        return False