import os
import urllib.request
import lzma
import zipfile
import shutil
from core.application.event_bus import EventBus


class ToolManager:
    """Lädt externe Abhängigkeiten herunter und injiziert sie in den System-PATH."""

    # Feste Download-Links für die essentiellen Java/Android Tools
    TOOLS_JARS = {
        "uber-apk-signer.jar": "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar",
        "APKEditor.jar": "https://github.com/REAndroid/APKEditor/releases/download/V1.4.2/APKEditor-1.4.2.jar",
        "lspatch.jar": "https://github.com/LSPosed/LSPatch/releases/download/v0.6/jar-v0.6-398-release.jar",
        "TrustMeAlready.apk": "https://github.com/ViRb3/TrustMeAlready/releases/download/v1.11/TrustMeAlready-v1.11-release.apk"
    }

    # Der feste Link für Frida 17.17.0
    FRIDA_URL = "https://github.com/frida/frida/releases/download/17.17.0/frida-gadget-17.17.0-android-arm64.so.xz"

    # Spezifisch für Windows
    WIN_APKTOOL_JAR = "https://bitbucket.org/iBotPeaches/apktool/downloads/apktool_2.9.3.jar"
    WIN_APKTOOL_BAT = "https://raw.githubusercontent.com/iBotPeaches/Apktool/master/scripts/windows/apktool.bat"
    WIN_PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    WIN_BUILD_TOOLS = "https://dl.google.com/android/repository/build-tools_r34-windows.zip"

    @classmethod
    def _download_file(cls, url: str, target_path: str) -> None:
        """Lädt eine Datei mit einem sicheren User-Agent herunter, um 403-Fehler von GitHub zu vermeiden."""
        req = urllib.request.Request(url, headers={'User-Agent': 'Kippy-RE-Framework-Downloader'})
        with urllib.request.urlopen(req) as response, open(target_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

    @classmethod
    def setup_tools(cls, base_dir: str) -> None:
        tools_dir = os.path.join(base_dir, "tools")
        os.makedirs(tools_dir, exist_ok=True)
        is_win = os.name == 'nt'

        # 1. JARs und APKs laden
        for tool_name, url in cls.TOOLS_JARS.items():
            target_path = os.path.join(tools_dir, tool_name)
            if not os.path.exists(target_path):
                EventBus.publish("LOG_INFO", f"[*] Lade Tool herunter: {tool_name} ...")
                try:
                    cls._download_file(url, target_path)
                    EventBus.publish("LOG_INFO", f"[+] {tool_name} erfolgreich installiert!")
                except Exception as e:
                    EventBus.publish("LOG_INFO", f"[!] Download fehlgeschlagen für {tool_name}: {e}")

        # 2. Frida-Gadget laden und in den RAM dekomprimieren
        frida_target = os.path.join(tools_dir, "libfrida-gadget.so")
        if not os.path.exists(frida_target):
            EventBus.publish("LOG_INFO", "[*] Lade Frida 17.17.0 Gadget herunter...")
            xz_path = os.path.join(tools_dir, "frida.xz")
            try:
                cls._download_file(cls.FRIDA_URL, xz_path)
                with lzma.open(xz_path) as f_in, open(frida_target, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(xz_path)
                EventBus.publish("LOG_INFO", "[+] libfrida-gadget.so (17.17.0) erfolgreich installiert!")
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Frida-Download: {e}")

        # 3. Windows-Spezifische Binaries laden (Apktool, ADB, Zipalign)
        if is_win:
            cls._setup_windows_binaries(tools_dir)
        else:
            EventBus.publish("LOG_INFO",
                             "[*] Nicht-Windows-System. Bitte installiere adb, apktool und zipalign manuell (z.B. apt/brew).")

        # 4. PATH Injection (Sehr wichtig, damit Python Subprozesse die Tools direkt finden!)
        cls._inject_into_path(tools_dir, is_win)

        cls._create_manual_instructions(tools_dir)
        EventBus.publish("LOG_INFO", "[+] Auto-Setup abgeschlossen. Alle Tool-Abhängigkeiten sind bereit.")

    @classmethod
    def _setup_windows_binaries(cls, tools_dir: str):
        # Apktool (braucht BAT und JAR)
        apktool_bat = os.path.join(tools_dir, "apktool.bat")
        apktool_jar = os.path.join(tools_dir, "apktool.jar")
        if not os.path.exists(apktool_bat) or not os.path.exists(apktool_jar):
            EventBus.publish("LOG_INFO", "[*] Lade Windows Apktool herunter...")
            try:
                cls._download_file(cls.WIN_APKTOOL_BAT, apktool_bat)
                cls._download_file(cls.WIN_APKTOOL_JAR, apktool_jar)
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Apktool: {e}")

        # Platform-Tools (ADB)
        pt_dir = os.path.join(tools_dir, "platform-tools")
        if not os.path.exists(pt_dir) or not os.path.exists(os.path.join(pt_dir, "adb.exe")):
            EventBus.publish("LOG_INFO", "[*] Lade Android Platform-Tools (ADB) herunter...")
            zip_path = os.path.join(tools_dir, "pt.zip")
            try:
                cls._download_file(cls.WIN_PLATFORM_TOOLS, zip_path)
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tools_dir)
                os.remove(zip_path)
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Platform-Tools: {e}")

        # Build-Tools (Zipalign, AAPT2)
        bt_dir = os.path.join(tools_dir, "android-14")
        if not os.path.exists(bt_dir) or not os.path.exists(os.path.join(bt_dir, "zipalign.exe")):
            EventBus.publish("LOG_INFO", "[*] Lade Android Build-Tools (Zipalign/AAPT2) herunter...")
            zip_path = os.path.join(tools_dir, "bt.zip")
            try:
                cls._download_file(cls.WIN_BUILD_TOOLS, zip_path)
                with zipfile.ZipFile(zip_path, 'r') as z:
                    z.extractall(tools_dir)
                os.remove(zip_path)
            except Exception as e:
                EventBus.publish("LOG_INFO", f"[!] Fehler bei Build-Tools: {e}")

    @classmethod
    def _inject_into_path(cls, tools_dir: str, is_win: bool):
        """Fügt die heruntergeladenen Ordner dem PATH hinzu, sodass cmds wie 'adb' direkt funktionieren."""
        paths_to_add = [os.path.abspath(tools_dir)]
        if is_win:
            pt_dir = os.path.join(tools_dir, "platform-tools")
            bt_dir = os.path.join(tools_dir, "android-14")
            if os.path.exists(pt_dir): paths_to_add.append(os.path.abspath(pt_dir))
            if os.path.exists(bt_dir): paths_to_add.append(os.path.abspath(bt_dir))

        current_path = os.environ.get("PATH", "")
        new_path_elements = [p for p in paths_to_add if p not in current_path.split(os.pathsep)]

        if new_path_elements:
            os.environ["PATH"] = os.pathsep.join(new_path_elements) + os.pathsep + current_path

    @classmethod
    def _create_manual_instructions(cls, tools_dir: str) -> None:
        readme_path = os.path.join(tools_dir, "README_MANUAL_TOOLS.txt")
        if not os.path.exists(readme_path):
            content = (
                "Falls der automatische Download fehlschlägt, lade diese Dateien manuell herunter und lege sie hier ab:\n\n"
                "1. libfrida-gadget.so (Entpackt aus der .xz von Frida Releases, v17+)\n"
                "2. APKEditor.jar\n"
                "3. uber-apk-signer.jar\n"
                "4. lspatch.jar\n"
                "5. TrustMeAlready.apk\n\n"
                "Für Windows (Ohne globale Android Studio Installation):\n"
                "- apktool.jar und apktool.bat direkt in diesen Ordner\n"
                "- Den Google 'platform-tools' Ordner hier entpacken (sodass adb.exe unter tools/platform-tools/adb.exe liegt)\n"
                "- Den Google 'build-tools' Ordner hier als 'android-14' entpacken (sodass zipalign.exe unter tools/android-14/zipalign.exe liegt)\n"
            )
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(content)