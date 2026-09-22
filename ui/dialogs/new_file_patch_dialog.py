import tkinter as tk
from tkinter import ttk


class NewFilePatchDialog(tk.Toplevel):
    """Bestaetigt/anpasst den Zielpfad fuer einen new_file-Patch (aus Favoritenziel vorbelegt).
    Schreibt KEINE Datei auf die Platte — liefert nur result_path fuer die aktive Patch-Liste.
    Modal + topmost, damit der Dialog nicht hinter dem Favoriten-Fenster verschwindet."""

    def __init__(self, parent, default_path=""):
        super().__init__(parent)
        self.result_path = None

        self.title("➕ Neue Datei als Patch hinzufuegen")
        self.geometry("640x180")
        self.transient(parent)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        ttk.Label(self, text="Zielpfad (ab Workspace-Root, aus Favorit uebernommen):",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(12, 4))

        self.ent_path = ttk.Entry(self)
        self.ent_path.pack(fill="x", padx=10, pady=2)
        self.ent_path.insert(0, default_path or "")

        ttk.Label(self,
                  text=("Hinweis: Die Datei wird NICHT auf die Platte geschrieben. Sie wird beim Build "
                        "in die Destination erzeugt und erscheint als aktiver Patch."),
                  foreground="#888888", wraplength=600, justify="left").pack(anchor="w", padx=10, pady=(4, 8))

        f_btn = ttk.Frame(self)
        f_btn.pack(fill="x", padx=10, pady=8)
        ttk.Button(f_btn, text="Abbrechen", command=self._cancel).pack(side="right", padx=4)
        ttk.Button(f_btn, text="✅ Als Patch hinzufuegen", command=self._confirm).pack(side="right", padx=4)

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self._cancel())

        self.grab_set()
        self.lift()
        self.focus_force()
        self.ent_path.focus_set()
        self.wait_window(self)

    def _confirm(self):
        p = self.ent_path.get().strip()
        if not p:
            return
        self.result_path = p
        self.destroy()

    def _cancel(self):
        self.result_path = None
        self.destroy()
