import os
import shutil
import datetime
import tkinter as tk
from tkinter import ttk

from ui_smali_studio_tab import SmaliStudioTab

class WorkspaceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.patch_rows = []
        self.create_widgets()

    def create_widgets(self):
        m_frame = ttk.LabelFrame(self, text="1. Patch Meta-Daten")
        m_frame.pack(side="top", fill="x", padx=10, pady=5)

        ttk.Label(m_frame, text="Patch-ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_id = ttk.Label(m_frame, text="", font=("Courier", 10, "bold"))
        self.lbl_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(m_frame, text="Manueller Name:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(m_frame, width=40)
        self.ent_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(m_frame, text="App-Version:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.ent_version = ttk.Entry(m_frame, width=20)
        self.ent_version.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        self.console = tk.Text(self, height=6, bg="black", fg="lightgreen")
        self.console.pack(side="bottom", fill="x", padx=10, pady=5)

        r_frame = ttk.LabelFrame(self, text="4. Resultat")
        r_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        self.combo_res = ttk.Combobox(r_frame, values=["Success", "Crash", "No Internet", "Logic Error"], state="readonly")
        self.combo_res.current(0)
        self.combo_res.grid(row=0, column=1, padx=5, pady=2)

        self.txt_obs = tk.Text(r_frame, height=2, width=60)
        self.txt_obs.grid(row=1, column=1, padx=5, pady=2)

        ttk.Button(r_frame, text="Save Result", command=self.save_result).grid(row=2, column=1, sticky="e", padx=5, pady=5)

        a_frame = ttk.LabelFrame(self, text="3. Pipelines Ausführen")
        a_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        ttk.Label(a_frame, text="Pipeline:").grid(row=0, column=0, padx=5, pady=5)
        self.combo_pipe = ttk.Combobox(a_frame, values=["BUILD_FLUTTER", "BUILD_NATIVE"], state="readonly", width=15)
        self.combo_pipe.current(0)
        self.combo_pipe.grid(row=0, column=1, padx=5, pady=5)

        ttk.Button(a_frame, text="▶ Build", command=lambda: self.run_pipeline(self.combo_pipe.get())).grid(row=0, column=2, padx=5, pady=5)
        ttk.Button(a_frame, text="📱 Flash", command=lambda: self.run_pipeline("FLASH")).grid(row=0, column=3, padx=5, pady=5)

        ttk.Separator(a_frame, orient="vertical").grid(row=0, column=4, sticky="ns", padx=5, pady=5)
        self.btn_trace_start = ttk.Button(a_frame, text="Start Trace", command=self.start_trace)
        self.btn_trace_start.grid(row=0, column=5, padx=5, pady=5)
        self.btn_trace_stop = ttk.Button(a_frame, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_trace_stop.grid(row=0, column=6, padx=5, pady=5)

        patch_book = ttk.Notebook(self)
        patch_book.pack(side="top", fill="both", expand=True, padx=10, pady=5)

        tab_hex = ttk.Frame(patch_book)
        self.tab_smali = SmaliStudioTab(patch_book, self.app)
        patch_book.add(tab_hex, text="Hex Patcher (Flutter / C++)")
        patch_book.add(self.tab_smali, text="Smali Studio (Java / Kotlin)")

        ttk.Button(tab_hex, text="+ Add Hex Patch", command=self.add_patch_row).pack(anchor="w", padx=5, pady=5)
        self.p_container = ttk.Frame(tab_hex)
        self.p_container.pack(fill="x", padx=5, pady=5)
        self.add_patch_row()

    def add_patch_row(self):
        f = ttk.Frame(self.p_container)
        f.pack(fill="x", pady=2)
        row = {"frame": f}
        for lbl, w in [("RAM:", 12), ("Base:", 12), ("Orig:", 20), ("Patch:", 20)]:
            ttk.Label(f, text=lbl).pack(side="left")
            e = ttk.Entry(f, width=w)
            e.pack(side="left", padx=2)
            row[lbl[:-1].lower()] = e
        row["base"].insert(0, "00100000")
        ttk.Button(f, text="X", width=3, command=lambda: self.remove_patch_row(f)).pack(side="left", padx=5)
        self.patch_rows.append(row)

    def remove_patch_row(self, f):
        f.destroy()
        self.patch_rows = [p for p in self.patch_rows if p["frame"] != f]

    def get_all_patches(self):
        hex_data = [{"type": "hex", "ram": p["ram"].get(), "base": p["base"].get(), "orig": p["orig"].get(),
                     "patch": p["patch"].get(), "file": "libflutter.so"} for p in self.patch_rows if p["ram"].get().strip()]
        return hex_data + self.tab_smali.smali_patches

    def load_patches_from_record(self, record):
        for p in list(self.patch_rows): self.remove_patch_row(p["frame"])
        self.tab_smali.smali_patches.clear()

        for pt in record.get("patches", []):
            if pt.get("type") == "smali":
                self.tab_smali.smali_patches.append(pt)
            else:
                self.add_patch_row()
                last_row = self.patch_rows[-1]
                last_row["ram"].delete(0, tk.END); last_row["ram"].insert(0, pt.get("ram", ""))
                last_row["base"].delete(0, tk.END); last_row["base"].insert(0, pt.get("base", "00100000"))
                last_row["orig"].delete(0, tk.END); last_row["orig"].insert(0, pt.get("orig", ""))
                last_row["patch"].delete(0, tk.END); last_row["patch"].insert(0, pt.get("patch", ""))

        self.tab_smali.refresh_smali_tree()
        self.ent_version.delete(0, tk.END)
        self.ent_version.insert(0, record.get("app_version", ""))
        self.app.notebook.select(self.app.workspace_tab) # FIX: War früher tab_workspace

    def run_pipeline(self, name):
        import threading
        def task():
            if name.startswith("BUILD"):
                self.app.current_archive_path = os.path.join(self.app.cfg.paths["ARCHIVE_DIR"], f"{self.app.current_id}_{self.ent_name.get().replace(' ', '_')}")
                os.makedirs(self.app.current_archive_path, exist_ok=True)
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        try: os.remove(os.path.join(dest_dir, f))
                        except: pass

            success = self.app.engine.run_pipeline(name)

            if name.startswith("BUILD") and success:
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        if f.endswith("-aligned-debugSigned.apk"):
                            shutil.copy(os.path.join(dest_dir, f), self.app.current_archive_path)

        threading.Thread(target=task, daemon=True).start()

    def start_trace(self):
        from tkinter import messagebox
        messagebox.showinfo("Trace", "Bitte starte die App und klicke OK.")
        if self.app.engine.run_pipeline("TRACE_START"):
            self.btn_trace_start.config(state="disabled")
            self.btn_trace_stop.config(state="normal")

    def stop_trace(self):
        self.app.engine.run_pipeline("TRACE_STOP")
        self.btn_trace_start.config(state="normal")
        self.btn_trace_stop.config(state="disabled")

    def save_result(self):
        record = {
            "id": self.app.current_id,
            "name": self.ent_name.get(),
            "app_package": self.app.cfg.config.get("APP_PACKAGE", "Unbekannt"),
            "app_version": self.ent_version.get(),
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "result": self.combo_res.get(),
            "observation": self.txt_obs.get("1.0", tk.END).strip(),
            "patches": self.get_all_patches()
        }
        self.app.history.add_record(record)
        self.app.history_tab.refresh_tree()
        self.app.log("\n=== GESPEICHERT ===")
        self.app.generate_new_id()
        self.ent_name.delete(0, tk.END)