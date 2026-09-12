import os
import tkinter as tk
from tkinter import ttk, messagebox


class PackagePickerDialog(tk.Toplevel):
    """
    Ein modaler Dialog, der die Package-Struktur der App aus dem RAM-Cache als Baum darstellt.
    Erlaubt die Auswahl eines Ziel-Packages und die Eingabe eines neuen Dateinamens.
    """

    def __init__(self, parent, ram_cache, initial_filename="MyNewClass.smali"):
        super().__init__(parent)
        self.title("Ziel-Package auswählen")
        self.geometry("500x600")
        self.transient(parent)
        self.grab_set()

        self.ram_cache = ram_cache
        self.result_path = None
        self.initial_filename = initial_filename

        self._create_widgets()
        self._populate_tree()

        # Zentrieren
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

        self.wait_window(self)

    def _create_widgets(self):
        # Top Label
        ttk.Label(self, text="Wähle das Ziel-Package (Ordner) aus:", font=("Segoe UI", 10, "bold")).pack(pady=(10, 5),
                                                                                                         padx=10,
                                                                                                         anchor="w")

        # Treeview für die Ordnerstruktur
        f_tree = ttk.Frame(self)
        f_tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree = ttk.Treeview(f_tree, selectmode="browse", show="tree")
        s_y = ttk.Scrollbar(f_tree, orient="vertical", command=self.tree.yview)
        s_x = ttk.Scrollbar(f_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=s_y.set, xscrollcommand=s_x.set)

        s_y.pack(side="right", fill="y")
        s_x.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda e: self._on_ok())

        # Bottom Frame für Dateiname und Buttons
        f_bottom = ttk.Frame(self)
        f_bottom.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_bottom, text="Dateiname:").pack(side="left", padx=(0, 5))
        self.ent_filename = ttk.Entry(f_bottom)
        self.ent_filename.insert(0, self.initial_filename)
        self.ent_filename.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Fokus auf das Entry legen und Text markieren
        self.ent_filename.focus_set()
        self.ent_filename.select_range(0, tk.END)

        ttk.Button(f_bottom, text="Abbrechen", command=self.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(f_bottom, text="OK", command=self._on_ok).pack(side="right")

    def _populate_tree(self):
        """Extrahiert alle Pfade aus dem RAM-Cache, dedupliziert sie und baut den Baum auf."""
        packages = set()

        # Alle reinen Ordnerpfade aus den Dateipfaden extrahieren
        for path, _ in self.ram_cache:
            dir_name = os.path.dirname(path.replace("\\", "/"))
            if dir_name:
                packages.add(dir_name)

        inserted_nodes = set()

        # Alphabetisch sortiert iterieren, um sicherzustellen, dass Parent-Nodes zuerst da sind
        for pkg in sorted(packages):
            parts = pkg.split('/')
            current_path = ""

            for part in parts:
                parent = current_path
                current_path = f"{current_path}/{part}" if current_path else part

                if current_path not in inserted_nodes:
                    # Offen halten, wenn es eine oberste Ebene ist (z.B. smali_classes2)
                    is_open = (parent == "")
                    self.tree.insert(parent, "end", iid=current_path, text=part, open=is_open)
                    inserted_nodes.add(current_path)

    def _on_ok(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Fehlt", "Bitte wähle ein Ziel-Package aus dem Baum aus.", parent=self)
            return

        selected_pkg = sel[0]
        filename = self.ent_filename.get().strip()

        if not filename:
            messagebox.showwarning("Fehlt", "Bitte gib einen Dateinamen ein.", parent=self)
            return

        if not filename.endswith(".smali"):
            filename += ".smali"

        # Zusammensetzen des fertigen, relativen Pfades
        self.result_path = f"{selected_pkg}/{filename}"
        self.destroy()