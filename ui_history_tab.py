import os
import tkinter as tk
from tkinter import ttk

class HistoryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.create_widgets()

    def create_widgets(self):
        cols = ("ID", "App", "Ver.", "Datum", "Name", "Patches", "Resultat", "Kommentar")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=15)
        for c in cols:
            self.tree.heading(c, text=c)

        self.tree.column("ID", width=130)
        self.tree.column("App", width=120)
        self.tree.column("Ver.", width=60)
        self.tree.column("Datum", width=130)
        self.tree.column("Name", width=150)
        self.tree.column("Patches", width=250)
        self.tree.column("Resultat", width=80)
        self.tree.column("Kommentar", width=250)

        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        ttk.Button(self, text="Refresh", command=self.refresh_tree).pack(pady=5)
        ttk.Label(self, text="Doppelklick auf einen Eintrag, um ihn zu bearbeiten oder in den Workspace zu laden.").pack(pady=2)
        self.refresh_tree()

    def refresh_tree(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        for r in reversed(self.app.history.data):
            obs_preview = r.get("observation", "").replace("\n", " ")
            app_pkg = r.get("app_package", "N/A")
            app_ver = r.get("app_version", "-")

            patch_list = r.get("patches", [])
            formatted_patches = []
            for p in patch_list:
                if p.get("type") == "smali":
                    formatted_patches.append(f"Smali: {os.path.basename(p.get('file', ''))}")
                else:
                    formatted_patches.append(f"0x{p.get('ram', '?')}:{p.get('patch', '?')}")

            patch_str = " | ".join(formatted_patches) if formatted_patches else "Keine Patches"
            self.tree.insert("", "end", values=(
            r["id"], app_pkg, app_ver, r["timestamp"], r.get("name", ""), patch_str, r.get("result", ""), obs_preview))

    def on_tree_double_click(self, event):
        selection = self.tree.selection()
        if not selection: return
        rec_id = self.tree.item(selection[0], "values")[0]
        record = next((r for r in self.app.history.data if r["id"] == rec_id), None)
        if not record: return

        top = tk.Toplevel(self)
        top.title(f"Eintrag bearbeiten: {rec_id}")
        top.geometry("450x380")

        ttk.Label(top, text="Ergebnis:").pack(pady=5)
        combo = ttk.Combobox(top, values=["Success", "Crash", "No Internet", "Logic Error"], state="readonly")
        combo.set(record.get("result", "Success"))
        combo.pack(pady=5)

        ttk.Label(top, text="Notizen/Beobachtung:").pack(pady=5)
        txt = tk.Text(top, height=5, width=40)
        txt.insert("1.0", record.get("observation", ""))
        txt.pack(pady=5, fill="both", expand=True)

        def load_to_workspace():
            self.app.workspace_tab.load_patches_from_record(record)
            top.destroy()
            self.app.log(f"[*] Patches aus {rec_id} in Workspace geladen.")

        def save_edit():
            self.app.history.update_record(rec_id, combo.get(), txt.get("1.0", tk.END).strip())
            self.refresh_tree()
            top.destroy()
            self.app.log(f"[*] Datensatz {rec_id} aktualisiert.")

        btn_frame = ttk.Frame(top)
        btn_frame.pack(side="bottom", fill="x", pady=10)
        ttk.Button(btn_frame, text="Patches in Workspace laden", command=load_to_workspace).pack(side="left", padx=10)
        ttk.Button(btn_frame, text="Änderungen Speichern", command=save_edit).pack(side="right", padx=10)