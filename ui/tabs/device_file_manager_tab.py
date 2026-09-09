import tkinter as tk
from tkinter import ttk
from ui.controllers.device_file_manager_controller import DeviceFileManagerController


class DeviceFileManagerTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.controller = DeviceFileManagerController(self, app)
        self.create_widgets()
        self.controller.load_packages()
        self.bind("<Control-v>", lambda e: self.controller.upload_file())

    def create_widgets(self):
        top_frame = ttk.Frame(self)
        top_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(top_frame, text="App-Package:").pack(side="left", padx=2)
        self.cb_pkg = ttk.Combobox(top_frame, state="readonly", width=30)
        self.cb_pkg.pack(side="left", padx=5)
        self.cb_pkg.bind("<<ComboboxSelected>>", lambda e: self.controller.load_directory(self.cb_pkg.get(),
                                                                                          f"/data/data/{self.cb_pkg.get()}/"))
        ttk.Button(top_frame, text="🔄 Apps laden", command=self.controller.load_packages).pack(side="left", padx=5)

        path_frame = ttk.Frame(self)
        path_frame.pack(fill="x", padx=5, pady=2)
        ttk.Button(path_frame, text="⬆ Aufwärts", command=self.go_up).pack(side="left", padx=2)
        self.ent_path = ttk.Entry(path_frame)
        self.ent_path.pack(side="left", fill="x", expand=True, padx=5)
        self.ent_path.bind("<Return>", lambda e: self.controller.load_directory(self.cb_pkg.get(), self.ent_path.get()))
        ttk.Button(path_frame, text="GOTO",
                   command=lambda: self.controller.load_directory(self.cb_pkg.get(), self.ent_path.get())).pack(
            side="left")

        self.tree = ttk.Treeview(self, columns=("Name", "Größe", "Datum", "Rechte"), show="headings",
                                 selectmode="extended")
        for col, width in [("Name", 350), ("Größe", 80), ("Datum", 120), ("Rechte", 100)]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width)

        scroll_y = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=5, pady=5)
        self.tree.bind("<Double-1>", self.on_double_click)

        bot_frame = ttk.Frame(self)
        bot_frame.pack(fill="x", padx=5, pady=5)
        self.lbl_status = ttk.Label(bot_frame, text="Bereit.")
        self.lbl_status.pack(side="left")

        ttk.Button(bot_frame, text="📥 Download (Pull)", command=self.on_download).pack(side="right", padx=2)
        ttk.Button(bot_frame, text="📤 Upload (Push) [STRG+V]", command=self.controller.upload_file).pack(side="right",
                                                                                                         padx=2)
        ttk.Button(bot_frame, text="🗑 Löschen", command=self.on_delete).pack(side="right", padx=2)

    def update_packages(self, pkgs):
        self.cb_pkg['values'] = pkgs
        current_app = self.app.cfg.config.get("APP_PACKAGE", "")
        if current_app in pkgs:
            self.cb_pkg.set(current_app)
            self.controller.load_directory(current_app, f"/data/data/{current_app}/")
        elif pkgs:
            self.cb_pkg.current(0)

    def update_status(self, msg):
        self.lbl_status.config(text=msg)

    def render_files(self, files, path):
        self.ent_path.delete(0, tk.END)
        self.ent_path.insert(0, path)
        for i in self.tree.get_children(): self.tree.delete(i)
        for f in files:
            icon = "📁 " if f["is_dir"] else "📄 "
            self.tree.insert("", "end", values=(icon + f["name"], f["size"], f["date"], f["perms"]))
        self.update_status(f"Geladen: {len(files)} Einträge im Ordner.")

    def go_up(self):
        path = self.controller.current_path
        if path.count("/") > 1:
            self.controller.load_directory(self.cb_pkg.get(),
                                           "/" + "/".join([p for p in path.split("/") if p][:-1]) + "/")

    def on_double_click(self, event):
        sel = self.tree.selection()
        if sel:
            item = self.tree.item(sel[0], "values")
            is_dir = "📁" in item[0]
            name = item[0][2:]
            if is_dir:
                self.controller.load_directory(self.cb_pkg.get(), self.controller.current_path + name + "/")
            else:
                self.controller.open_file(name)

    def get_selected_items(self):
        items = []
        for sel in self.tree.selection():
            item_vals = self.tree.item(sel, "values")
            items.append({"name": item_vals[0][2:], "is_dir": "📁" in item_vals[0]})
        return items

    def on_download(self):
        self.controller.download_selected(self.get_selected_items())

    def on_delete(self):
        if items := self.get_selected_items():
            self.controller.delete_file(items)

    # --- PROGRESS DIALOG UI ---

    def show_download_progress(self):
        self.prog_win = tk.Toplevel(self)
        self.prog_win.title("Download läuft...")
        self.prog_win.geometry("550x220")
        self.prog_win.transient(self.app)
        self.prog_win.grab_set()

        self.lbl_file = ttk.Label(self.prog_win, text="Initialisiere Rekursion...", wraplength=500, justify="center")
        self.lbl_file.pack(pady=(15, 5))

        self.lbl_count = ttk.Label(self.prog_win, text="Scanne...")
        self.lbl_count.pack(pady=5)

        self.progress = ttk.Progressbar(self.prog_win, orient="horizontal", mode="determinate", length=450)
        self.progress.pack(pady=10)

        self.lbl_size = ttk.Label(self.prog_win, text="- / -")
        self.lbl_size.pack(pady=5)

        ttk.Button(self.prog_win, text="🛑 Abbrechen", command=self.controller.cancel_download).pack(pady=5)

    def update_download_status(self, msg):
        if hasattr(self, "prog_win") and self.prog_win.winfo_exists():
            self.lbl_file.config(text=msg)

    def update_download_progress(self, current_file, file_idx, total_files, copied_bytes, total_bytes):
        if hasattr(self, "prog_win") and self.prog_win.winfo_exists():
            self.lbl_file.config(text=f"Datei: {current_file}")
            self.lbl_count.config(text=f"Lade Datei {file_idx} von {total_files}")

            if total_bytes > 0:
                self.progress["value"] = (copied_bytes / total_bytes) * 100

            cp_str = self.controller.format_size(copied_bytes)
            tot_str = self.controller.format_size(total_bytes)
            self.lbl_size.config(text=f"{cp_str} von {tot_str} abgeschlossen")

    def close_download_progress(self):
        if hasattr(self, "prog_win") and self.prog_win.winfo_exists():
            self.prog_win.destroy()