import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog


class SmaliStructManager:
    def __init__(self, app, smali_dir, search_engine, update_ui_callback):
        self.app = app
        self.smali_dir = smali_dir
        self.search_engine = search_engine
        self.update_ui_callback = update_ui_callback

        self.snippets = self._load_snippets()
        self.custom_files = []  # Liste relativer Pfade (z.B. smali/com/custom/MyClass.smali)

    def _load_snippets(self):
        """Lädt die Snippets aus der JSON-Config."""
        try:
            path = os.path.join(self.app.cfg.config.get("BASE_DIR", ""), "snippets.json")
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.app.log(f"[!] Fehler beim Laden der snippets.json: {e}")
            return {}

    def get_default_path(self, current_active_file):
        """Ermittelt einen intelligenten Vorschlagspfad für neue Klassen."""
        # Standard Root
        base_root = "smali/com/custom/"

        # Wenn wir gerade in einer Datei arbeiten, nehmen wir deren Verzeichnis als Vorlage
        if current_active_file:
            parts = current_active_file.split("/")
            if len(parts) > 1:
                # Nimm alles bis zum Dateinamen
                dir_path = "/".join(parts[:-1])
                return f"{dir_path}/NewClass.smali"

        return f"{base_root}NewClass.smali"

    def create_new_structure(self, relative_path, base_code):
        """Legt eine physisch neue .smali Datei im Workspace an und aktualisiert den Index."""
        if not relative_path.endswith(".smali"):
            relative_path += ".smali"

        full_path = os.path.join(self.smali_dir, relative_path)

        if os.path.exists(full_path):
            messagebox.showwarning("Fehler", f"Die Datei existiert bereits:\n{relative_path}")
            return False

        try:
            # Ordnerstruktur erstellen, falls sie nicht existiert
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # Datei schreiben
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(base_code)

            # Zur lokalen Liste hinzufügen
            if relative_path not in self.custom_files:
                self.custom_files.append(relative_path)

            # RAM-Cache aktualisieren, damit die globale Suche/XREF die neue Datei sofort findet!
            self.search_engine.ram_cache.append((relative_path, base_code))

            self.app.log(f"[+] Neue Struktur erstellt: {relative_path}")

            # Callback triggern, um die UI-Liste (Treeview) zu aktualisieren
            if self.update_ui_callback:
                self.update_ui_callback()

            return True

        except Exception as e:
            messagebox.showerror("IO Fehler", f"Konnte Datei nicht erstellen:\n{e}")
            return False

    def save_existing_structure(self, relative_path, new_code):
        """Überschreibt eine vom Nutzer erstellte Struktur direkt auf der Festplatte."""
        full_path = os.path.join(self.smali_dir, relative_path)
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_code)

            # RAM-Cache aktualisieren
            for i, (path, content) in enumerate(self.search_engine.ram_cache):
                if path == relative_path:
                    self.search_engine.ram_cache[i] = (path, new_code)
                    break

            self.app.log(f"[*] Struktur gespeichert: {relative_path}")
            return True
        except Exception as e:
            messagebox.showerror("IO Fehler", str(e))
            return False