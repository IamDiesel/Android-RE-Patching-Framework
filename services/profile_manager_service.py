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
                "logcat --pid={PID} | grep -iE 'frida|ssl|crypto|keystore|network|http|intercept|key|GHOST'",
                "logcat --pid={PID} | grep -iE 'fatal|crash|exception|error'",
                "logcat *:E",
                "logcat"
            ]
        }

        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Wenn die Datei existiert, wird sie zur alleinigen Quelle.
                    # Das erlaubt es dem Nutzer, auch die hartcodierten Defaults dauerhaft zu löschen.
                    if "intents" in data and "logcats" in data:
                        return data
            except Exception:
                pass

        return default_profiles

    def save_profiles(self):
        try:
            # FIX: Zwingende Erstellung des /data Verzeichnisses verhindert den Silent-Crash
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=4)
        except Exception as e:
            from core.application.event_bus import EventBus
            EventBus.publish("LOG_INFO", f"[!] Fehler beim Speichern der Logger-Profile: {e}")

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