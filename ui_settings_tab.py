import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.entries = {}
        self.create_widgets()

    def create_widgets(self):
        p_frame = ttk.LabelFrame(self, text="Pfade & App")
        p_frame.pack(fill="x", padx=10, pady=5)

        for i, (lbl, key) in enumerate(
                [("Base Dir", "BASE_DIR"), ("Split APK", "SPLIT_NAME"), ("Package", "APP_PACKAGE"),
                 ("Signer", "SIGNER_JAR"), ("APKEditor", "APKEDITOR_JAR")]):
            ttk.Label(p_frame, text=lbl + ":").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            ent = ttk.Entry(p_frame, width=60)
            ent.grid(row=i, column=1, padx=5, pady=2)
            self.entries[key] = ent

        pipe_frame = ttk.LabelFrame(self, text="Pipelines (JSON)")
        pipe_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.txt_pipes = tk.Text(pipe_frame, height=15, font=("Courier", 9))
        self.txt_pipes.pack(fill="both", expand=True, padx=5, pady=5)

        b_frame = ttk.Frame(self)
        b_frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(b_frame, text="Laden...", command=self.load_settings_file).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Speichern unter...", command=self.save_settings_as).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Defaults", command=self.restore_defaults).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Save (Aktuell)", command=self.save_settings).pack(side="right", padx=5)

        self.populate_settings()

    def populate_settings(self):
        for k, ent in self.entries.items():
            ent.delete(0, tk.END)
            ent.insert(0, self.app.cfg.config.get(k, ""))

        self.txt_pipes.delete("1.0", tk.END)
        self.txt_pipes.insert("1.0", json.dumps(self.app.cfg.config.get("PIPELINES", {}), indent=4))

    def _sync_config_from_ui(self):
        for k, ent in self.entries.items():
            self.app.cfg.config[k] = ent.get()

        try:
            self.app.cfg.config["PIPELINES"] = json.loads(self.txt_pipes.get("1.0", tk.END))
            return True
        except Exception as e:
            messagebox.showerror("JSON Error", str(e))
            return False

    def save_settings(self):
        if self._sync_config_from_ui():
            self.app.cfg.save()
            messagebox.showinfo("Saved", "Einstellungen gespeichert!")

    def save_settings_as(self):
        if self._sync_config_from_ui():
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
            if path:
                self.app.cfg.save(path)
                messagebox.showinfo("Gespeichert", f"Konfiguration gespeichert unter:\n{path}")

    def load_settings_file(self):
        path = filedialog.askopenfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if path:
            self.app.cfg.load(path)
            self.populate_settings()
            self.app.history.data = self.app.history.load()
            self.app.history_tab.refresh_tree()
            messagebox.showinfo("Geladen", f"Konfiguration geladen:\n{path}")

    def restore_defaults(self):
        self.app.cfg.restore_defaults()
        self.populate_settings()