import os
import tkinter as tk
from tkinter import ttk, messagebox

# NEU: Wir importieren unseren eigenen Package Picker
from ui.dialogs.package_picker_dialog import PackagePickerDialog


class CreateStructDialog(tk.Toplevel):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        self.title("➕ Neue Smali-Struktur anlegen")
        self.geometry("600x250")
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.create_widgets()

    def create_widgets(self):
        ttk.Label(self, text="Relativer Pfad (ab Workspace-Root):", font=("Segoe UI", 9, "bold")).pack(anchor="w",
                                                                                                       padx=10, pady=5)

        f_path = ttk.Frame(self)
        f_path.pack(fill="x", padx=10, pady=2)

        self.ent_path = ttk.Entry(f_path)
        self.ent_path.pack(side="left", fill="x", expand=True)
        self.ent_path.insert(0, self.controller.struct_manager.get_default_path(self.controller.current_smali_file))

        ttk.Button(f_path, text="📁 Auswählen", command=self.browse_path).pack(side="right", padx=5)

        ttk.Label(self, text="Klassen-Typ Vorlage:", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=5)
        self.combo_type = ttk.Combobox(self, values=["Standard-Klasse", "BroadcastReceiver-Komponente"],
                                       state="readonly")
        self.combo_type.pack(fill="x", padx=10, pady=2)
        self.combo_type.current(0)

        ttk.Button(self, text="🚀 Struktur generieren", command=self.confirm).pack(pady=20)

    def browse_path(self):
        # Wir extrahieren den aktuellen Dateinamen aus dem Entry, falls der Nutzer
        # dort schon "MeinTest.smali" reingeschrieben hat, um ihn an den Dialog zu übergeben.
        current_path = self.ent_path.get().strip()
        filename = "MyNewClass.smali"
        if current_path:
            filename = current_path.split("/")[-1]

        # NEU: Aufruf unseres Workspace-internen Package-Pickers
        dialog = PackagePickerDialog(self, self.controller.search_engine.ram_cache, initial_filename=filename)

        # Wenn der Nutzer auf OK geklickt hat (result_path ist gesetzt)
        if dialog.result_path:
            self.ent_path.delete(0, tk.END)
            self.ent_path.insert(0, dialog.result_path)

    def confirm(self):
        rel_p = self.ent_path.get().strip()
        if not rel_p: return

        clean_p = rel_p.replace(".smali", "")
        parts = clean_p.split("/")
        start_idx = 1 if parts[0].startswith("smali") else 0
        dalvik_classname = "L" + "/".join(parts[start_idx:]) + ";"

        if self.combo_type.get() == "BroadcastReceiver-Komponente":
            base_code = self.controller.struct_manager.snippets.get("Android API (Intents/Context)", {}).get(
                "BroadcastReceiver Klasse", "")
        else:
            base_code = self.controller.struct_manager.snippets.get("Struktur & Interfaces", {}).get(
                "Neue Klasse (.class)", "")

        base_code = base_code.replace("Lcom/example/MyBroadcastReceiver;", dalvik_classname)
        base_code = base_code.replace("Lcom/example/MyClass;", dalvik_classname)

        if self.controller.struct_manager.create_new_structure(rel_p, base_code):
            self.destroy()
            self.controller.load_custom_structure_into_editor(rel_p)