import os
import sys
import subprocess
import site


def bootstrap_environment():
    # 1. Abhängigkeiten prüfen und installieren
    req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if os.path.exists(req_file):
        print("[*] Prüfe und installiere Abhängigkeiten (pip)...")
        # Führt pip install -r requirements.txt lautlos aus
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"])

    # 2. PATH-Variablen dynamisch für die Laufzeit patchen
    # Sammle bekannte Pfade, in denen ADB und mitmdump liegen könnten
    paths_to_add = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools"),  # Standard Android Studio Pfad
        os.path.join(site.USER_BASE, "Scripts"),  # Pfad für 'pip install --user'
        os.path.join(sys.prefix, "Scripts")  # Pfad für globale Python-Installationen
    ]

    # NEU: Zipalign aus den Build-Tools dynamisch hinzufügen
    build_tools_base = os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\build-tools")
    if os.path.exists(build_tools_base):
        # Alle installierten Versionen auflisten und die höchste nehmen
        versions = os.listdir(build_tools_base)
        if versions:
            latest_version = sorted(versions)[-1]
            zipalign_path = os.path.join(build_tools_base, latest_version)
            paths_to_add.append(zipalign_path)


    current_path = os.environ.get("PATH", "")
    new_paths = []

    # Prüfen, ob der Pfad existiert und noch nicht im System-PATH ist
    for p in paths_to_add:
        if os.path.exists(p) and p not in current_path:
            new_paths.append(p)

    # Wenn neue Pfade gefunden wurden, setzen wir sie an den Anfang der Umgebungsvariable
    if new_paths:
        os.environ["PATH"] = os.pathsep.join(new_paths) + os.pathsep + current_path
        for p in new_paths:
            print(f"[*] PATH dynamisch erweitert um: {p}")


if __name__ == "__main__":
    # Erst Umgebung vorbereiten, dann die GUI laden
    bootstrap_environment()

    # Der Import erfolgt erst hier, damit die GUI erst startet, 
    # wenn alle Abhängigkeiten (z.B. Drittanbieter-Bibliotheken) gesichert da sind.
    from gui import ReFrameworkApp

    app = ReFrameworkApp()
    app.mainloop()