import os
import shutil
import datetime
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import ConfigManager
from pipeline_engine import PipelineEngine
from history import HistoryManager
from api_inspector import APIInspectorTab

class KippyReFrameworkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kippy RE-Framework V8 - DAST & Proxy Suite")
        self.geometry("1200x950")
        
        self.cfg = ConfigManager()
        self.history = HistoryManager(self.cfg)
        self.engine = PipelineEngine(self.cfg, self.log, self.get_patch_data, self.get_current_archive_path)
        
        self.patch_rows = []
        self.current_id = ""
        self.current_archive_path = ""
        
        self.create_widgets()
        self.generate_new_id()
        
    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_workspace = ttk.Frame(self.notebook)
        self.tab_management = ttk.Frame(self.notebook)
        self.tab_api = APIInspectorTab(self.notebook, self.cfg, self.log)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_workspace, text="🔧 Workspace")
        self.notebook.add(self.tab_api, text="🌐 API Inspector")
        self.notebook.add(self.tab_management, text="📊 Test Management")
        self.notebook.add(self.tab_settings, text="⚙️ Einstellungen")

        self._setup_workspace()
        self._setup_management()
        self._setup_settings()

    def _setup_workspace(self):
        m_frame = ttk.LabelFrame(self.tab_workspace, text="1. Patch Meta-Daten")
        m_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(m_frame, text="Patch-ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_id = ttk.Label(m_frame, text="", font=("Courier", 10, "bold"))
        self.lbl_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(m_frame, text="Manueller Name:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(m_frame, width=40)
        self.ent_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        p_frame = ttk.LabelFrame(self.tab_workspace, text="2. Hex-Patches (Anker)")
        p_frame.pack(fill="x", padx=10, pady=5)
        ttk.Button(p_frame, text="+ Patch", command=self.add_patch_row).pack(anchor="w", padx=5, pady=5)
        self.p_container = ttk.Frame(p_frame)
        self.p_container.pack(fill="x", padx=5, pady=5)
        self.add_patch_row()

        a_frame = ttk.LabelFrame(self.tab_workspace, text="3. Pipelines Ausführen")
        a_frame.pack(fill="x", padx=10, pady=5)
        ttk.Button(a_frame, text="1. Build Pipeline", command=lambda: self.run_pipeline("BUILD")).grid(row=0, column=0, padx=5, pady=5)
        ttk.Button(a_frame, text="2. Flash Pipeline", command=lambda: self.run_pipeline("FLASH")).grid(row=0, column=1, padx=5, pady=5)
        self.btn_trace_start = ttk.Button(a_frame, text="Start Trace", command=self.start_trace)
        self.btn_trace_start.grid(row=0, column=2, padx=5, pady=5)
        self.btn_trace_stop = ttk.Button(a_frame, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_trace_stop.grid(row=0, column=3, padx=5, pady=5)

        r_frame = ttk.LabelFrame(self.tab_workspace, text="4. Resultat")
        r_frame.pack(fill="x", padx=10, pady=5)
        self.combo_res = ttk.Combobox(r_frame, values=["Success", "Crash", "No Internet", "Logic Error"], state="readonly")
        self.combo_res.current(0)
        self.combo_res.grid(row=0, column=1, padx=5, pady=2)
        self.txt_obs = tk.Text(r_frame, height=3, width=60)
        self.txt_obs.grid(row=1, column=1, padx=5, pady=2)
        ttk.Button(r_frame, text="Save Result", command=self.save_result).grid(row=2, column=1, sticky="e", padx=5, pady=5)

        self.console = tk.Text(self.tab_workspace, height=10, bg="black", fg="lightgreen")
        self.console.pack(fill="both", expand=True, padx=10, pady=5)

    def _setup_management(self):
        cols = ("ID", "Name", "Date", "Result", "Kommentar")
        self.tree = ttk.Treeview(self.tab_management, columns=cols, show="headings", height=15)
        for c in cols: 
            self.tree.heading(c, text=c)
        
        self.tree.column("ID", width=140)
        self.tree.column("Name", width=180)
        self.tree.column("Date", width=140)
        self.tree.column("Result", width=100)
        self.tree.column("Kommentar", width=400)

        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        ttk.Button(self.tab_management, text="Refresh", command=self.refresh_tree).pack(pady=5)
        ttk.Label(self.tab_management, text="Doppelklick auf einen Eintrag, um ihn zu bearbeiten.").pack(pady=2)
        self.refresh_tree()

    def _setup_settings(self):
        p_frame = ttk.LabelFrame(self.tab_settings, text="Pfade & App")
        p_frame.pack(fill="x", padx=10, pady=5)
        
        self.entries = {}
        for i, (lbl, key) in enumerate([("Base Dir", "BASE_DIR"), ("Split APK", "SPLIT_NAME"), ("Package", "APP_PACKAGE"), ("Signer", "SIGNER_JAR")]):
            ttk.Label(p_frame, text=lbl+":").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            ent = ttk.Entry(p_frame, width=60)
            ent.grid(row=i, column=1, padx=5, pady=2)
            self.entries[key] = ent

        pipe_frame = ttk.LabelFrame(self.tab_settings, text="Pipelines (JSON)")
        pipe_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.txt_pipes = tk.Text(pipe_frame, height=15, font=("Courier", 9))
        self.txt_pipes.pack(fill="both", expand=True, padx=5, pady=5)

        b_frame = ttk.Frame(self.tab_settings)
        b_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Button(b_frame, text="Laden...", command=self.load_settings_file).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Speichern unter...", command=self.save_settings_as).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Defaults", command=self.restore_defaults).pack(side="left", padx=5)
        ttk.Button(b_frame, text="Save (Aktuell)", command=self.save_settings).pack(side="right", padx=5)
        
        self.populate_settings()

    def on_tree_double_click(self, event):
        selection = self.tree.selection()
        if not selection: return
        item_id = selection[0]
        record_values = self.tree.item(item_id, "values")
        if not record_values: return
        
        rec_id = record_values[0]
        record = next((r for r in self.history.data if r["id"] == rec_id), None)
        if not record: return
        
        top = tk.Toplevel(self)
        top.title(f"Eintrag bearbeiten: {rec_id}")
        top.geometry("450x300")
        
        ttk.Label(top, text="Ergebnis:").pack(pady=5)
        combo = ttk.Combobox(top, values=["Success", "Crash", "No Internet", "Logic Error"], state="readonly")
        combo.set(record.get("result", "Success"))
        combo.pack(pady=5)
        
        ttk.Label(top, text="Notizen/Beobachtung:").pack(pady=5)
        txt = tk.Text(top, height=5, width=40)
        txt.insert("1.0", record.get("observation", ""))
        txt.pack(pady=5, fill="both", expand=True)
        
        def save_edit():
            new_res = combo.get()
            new_obs = txt.get("1.0", tk.END).strip()
            self.history.update_record(rec_id, new_res, new_obs)
            self.refresh_tree()
            top.destroy()
            self.log(f"[*] Datensatz {rec_id} aktualisiert.")
            
        ttk.Button(top, text="Änderungen Speichern", command=save_edit).pack(pady=10, side="bottom")

    def generate_new_id(self):
        self.current_id = f"PID-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.lbl_id.config(text=self.current_id)

    def log(self, msg):
        self.console.insert("end", msg + "\n")
        self.console.see("end")
        self.update()

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

    def get_patch_data(self):
        return [{"ram": p["ram"].get(), "base": p["base"].get(), "orig": p["orig"].get(), "patch": p["patch"].get()} for p in self.patch_rows]

    def get_current_archive_path(self):
        return self.current_archive_path

    def run_pipeline(self, name):
        if name == "BUILD":
            self.current_archive_path = os.path.join(self.cfg.paths["ARCHIVE_DIR"], f"{self.current_id}_{self.ent_name.get().replace(' ', '_')}")
            os.makedirs(self.current_archive_path, exist_ok=True)
            for f in os.listdir(self.cfg.paths["DEST_DIR"]): 
                os.remove(os.path.join(self.cfg.paths["DEST_DIR"], f))
            
        success = self.engine.run_pipeline(name)
        
        if name == "BUILD" and success:
            for f in os.listdir(self.cfg.paths["DEST_DIR"]):
                if f.endswith("-aligned-debugSigned.apk"):
                    shutil.copy(os.path.join(self.cfg.paths["DEST_DIR"], f), self.current_archive_path)

    def start_trace(self):
        messagebox.showinfo("Trace", "Bitte starte die App und klicke OK.")
        if self.engine.run_pipeline("TRACE_START"):
            self.btn_trace_start.config(state="disabled")
            self.btn_trace_stop.config(state="normal")

    def stop_trace(self):
        self.engine.run_pipeline("TRACE_STOP")
        self.btn_trace_start.config(state="normal")
        self.btn_trace_stop.config(state="disabled")

    def save_result(self):
        record = {
            "id": self.current_id,
            "name": self.ent_name.get(),
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "result": self.combo_res.get(),
            "observation": self.txt_obs.get("1.0", tk.END).strip(),
            "patches": self.get_patch_data()
        }
        self.history.add_record(record)
        self.refresh_tree()
        self.log("\n=== GESPEICHERT ===")
        self.generate_new_id()
        self.ent_name.delete(0, tk.END)

    def refresh_tree(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        for r in reversed(self.history.data):
            obs_preview = r.get("observation", "").replace("\n", " ")
            self.tree.insert("", "end", values=(r["id"], r["name"], r["timestamp"], r["result"], obs_preview))

    def populate_settings(self):
        for k, ent in self.entries.items():
            ent.delete(0, tk.END)
            ent.insert(0, self.cfg.config.get(k, ""))
        self.txt_pipes.delete("1.0", tk.END)
        self.txt_pipes.insert("1.0", json.dumps(self.cfg.config.get("PIPELINES", {}), indent=4))

    def _sync_config_from_ui(self):
        for k, ent in self.entries.items():
            self.cfg.config[k] = ent.get()
        try:
            self.cfg.config["PIPELINES"] = json.loads(self.txt_pipes.get("1.0", tk.END))
            return True
        except Exception as e:
            messagebox.showerror("JSON Error", str(e))
            return False

    def save_settings(self):
        if self._sync_config_from_ui():
            self.cfg.save()
            messagebox.showinfo("Saved", "Einstellungen gespeichert!")

    def save_settings_as(self):
        if self._sync_config_from_ui():
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], title="Einstellungen speichern unter")
            if path:
                self.cfg.save(path)
                messagebox.showinfo("Gespeichert", f"Konfiguration gespeichert unter:\n{path}")

    def load_settings_file(self):
        path = filedialog.askopenfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], title="Einstellungen laden")
        if path:
            self.cfg.load(path)
            self.populate_settings()
            self.history.data = self.history.load()
            self.refresh_tree()
            messagebox.showinfo("Geladen", f"Konfiguration geladen:\n{path}")

    def restore_defaults(self):
        self.cfg.restore_defaults()
        self.populate_settings()
