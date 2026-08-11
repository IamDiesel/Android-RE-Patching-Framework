import os
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import json

# ==============================================================================
# STANDARD-KONFIGURATION (Fallback)
# ==============================================================================
DEFAULT_CONFIG = {
    "BASE_DIR": r"C:\Users\Lenovo\Downloads\APK-Tools\Script",
    "SPLIT_NAME": "split_config.arm64_v8a",
    "APP_PACKAGE": "com.datamars.kippynew",
    "SIGNER_JAR": "uber-apk-signer-1.3.0.jar",
    "PIPELINE": [
        {
            "name": "Backup original APK",
            "type": "cmd",
            "cmd": "copy {SPLIT_NAME}.apk {SPLIT_NAME}.zip",
            "cwd": "{SOURCE_DIR}"
        },
        {
            "name": "Extract APK (Windows tar)",
            "type": "cmd",
            "cmd": "tar -xf {SPLIT_NAME}.zip -C {SPLIT_NAME}",
            "cwd": "{SOURCE_DIR}"
        },
        {
            "name": "Apply Hex Patches",
            "type": "anchor_patch"
        },
        {
            "name": "Repack APK",
            "type": "cmd",
            "cmd": "jar c0f {SPLIT_NAME}.apk AndroidManifest.xml lib stamp-cert-sha256 META-INF",
            "cwd": "{EXTRACT_DIR}"
        },
        {
            "name": "Move repacked APK to Dest",
            "type": "cmd",
            "cmd": "move {SPLIT_NAME}.apk {DEST_DIR}\\{SPLIT_NAME}.apk",
            "cwd": "{EXTRACT_DIR}"
        },
        {
            "name": "Sign APKs",
            "type": "cmd",
            "cmd": "java -jar {SIGNER_JAR} -a . --allowResign",
            "cwd": "{DEST_DIR}"
        }
    ]
}

CONFIG_FILE = "config.json"


class KippyReFramework(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kippy RE-Framework V5 - Dynamic Pipeline")
        self.geometry("1000x900")

        self.logcat_process = None
        self.current_archive_path = None
        self.patch_rows = []

        self.load_config()
        self.history_data = self.load_history()

        self.create_widgets()
        self.generate_new_id()

    # --- DATEN-MANAGEMENT ---

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception:
                self.config = DEFAULT_CONFIG.copy()
        else:
            self.config = DEFAULT_CONFIG.copy()

        self.update_paths()

    def save_config(self):
        self.config["BASE_DIR"] = self.entry_base_dir.get()
        self.config["SPLIT_NAME"] = self.entry_split_name.get()
        self.config["APP_PACKAGE"] = self.entry_app_package.get()
        self.config["SIGNER_JAR"] = self.entry_signer.get()

        try:
            pipeline_data = json.loads(self.text_pipeline.get("1.0", tk.END))
            self.config["PIPELINE"] = pipeline_data
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON Fehler", f"Pipeline JSON ist ungültig:\n{e}")
            return

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4)

        self.update_paths()
        messagebox.showinfo("Gespeichert", "Einstellungen erfolgreich gespeichert!")

    def restore_defaults(self):
        self.config = DEFAULT_CONFIG.copy()
        self.populate_settings_tab()
        self.save_config()

    def update_paths(self):
        self.SOURCE_DIR = os.path.join(self.config["BASE_DIR"], "source")
        self.DEST_DIR = os.path.join(self.config["BASE_DIR"], "destination")
        self.ARCHIVE_DIR = os.path.join(self.config["BASE_DIR"], "archives")
        self.LOG_FILE = os.path.join(self.config["BASE_DIR"], "Kippy_RE_Log.md")
        self.JSON_HISTORY = os.path.join(self.config["BASE_DIR"], "RE_History.json")
        self.EXTRACT_DIR = os.path.join(self.SOURCE_DIR, self.config["SPLIT_NAME"])

        for d in [self.SOURCE_DIR, self.DEST_DIR, self.ARCHIVE_DIR]:
            os.makedirs(d, exist_ok=True)

    def load_history(self):
        if os.path.exists(self.JSON_HISTORY):
            with open(self.JSON_HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save_history(self):
        with open(self.JSON_HISTORY, "w", encoding="utf-8") as f:
            json.dump(self.history_data, f, indent=4)

    # --- GUI AUFBAU ---

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_workspace = ttk.Frame(self.notebook)
        self.tab_management = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_workspace, text="🔧 Workspace (Patch & Flash)")
        self.notebook.add(self.tab_management, text="📊 Test Management")
        self.notebook.add(self.tab_settings, text="⚙️ Einstellungen")

        self.setup_workspace_tab()
        self.setup_management_tab()
        self.setup_settings_tab()

    def setup_workspace_tab(self):
        meta_frame = ttk.LabelFrame(self.tab_workspace, text="1. Patch Meta-Daten")
        meta_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(meta_frame, text="Patch-ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_patch_id = ttk.Label(meta_frame, text="Wird generiert...", font=("Courier", 10, "bold"))
        self.lbl_patch_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(meta_frame, text="Manueller Name:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.entry_patch_name = ttk.Entry(meta_frame, width=40)
        self.entry_patch_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        self.patches_frame = ttk.LabelFrame(self.tab_workspace, text="2. Hex-Patches (Anker)")
        self.patches_frame.pack(fill="x", padx=10, pady=5)

        btn_add_patch = ttk.Button(self.patches_frame, text="+ Patch hinzufügen", command=self.add_patch_row)
        btn_add_patch.pack(anchor="w", padx=5, pady=5)

        self.patches_container = ttk.Frame(self.patches_frame)
        self.patches_container.pack(fill="x", padx=5, pady=5)
        self.add_patch_row()

        action_frame = ttk.LabelFrame(self.tab_workspace, text="3. Build & Flash")
        action_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(action_frame, text="1. Run Pipeline (Build)", command=self.run_pipeline).grid(row=0, column=0,
                                                                                                 padx=5, pady=5)
        ttk.Button(action_frame, text="2. Flash to Device", command=self.flash_device).grid(row=0, column=1, padx=5,
                                                                                            pady=5)
        self.btn_start_trace = ttk.Button(action_frame, text="Start Trace", command=self.start_trace)
        self.btn_start_trace.grid(row=0, column=2, padx=5, pady=5)
        self.btn_stop_trace = ttk.Button(action_frame, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_stop_trace.grid(row=0, column=3, padx=5, pady=5)

        result_frame = ttk.LabelFrame(self.tab_workspace, text="4. Test Resultate erfassen")
        result_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(result_frame, text="Ergebnis:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.combo_result = ttk.Combobox(result_frame, values=["Success", "Crash", "No Internet", "Logic Error"],
                                         state="readonly")
        self.combo_result.current(0)
        self.combo_result.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(result_frame, text="Notizen:").grid(row=1, column=0, sticky="nw", padx=5, pady=2)
        self.text_obs = tk.Text(result_frame, height=3, width=60)
        self.text_obs.grid(row=1, column=1, padx=5, pady=2)

        ttk.Button(result_frame, text="Save to JSON & Markdown", command=self.save_test_result).grid(row=2, column=1,
                                                                                                     sticky="e", padx=5,
                                                                                                     pady=5)

        self.text_console = tk.Text(self.tab_workspace, height=10, bg="black", fg="lightgreen")
        self.text_console.pack(fill="both", expand=True, padx=10, pady=5)

    def setup_management_tab(self):
        columns = ("ID", "Name", "Date", "Result")
        self.tree = ttk.Treeview(self.tab_management, columns=columns, show="headings", height=15)
        self.tree.heading("ID", text="Patch-ID")
        self.tree.heading("Name", text="Patch Name")
        self.tree.heading("Date", text="Datum")
        self.tree.heading("Result", text="Ergebnis")
        self.tree.column("ID", width=120)
        self.tree.column("Name", width=250)
        self.tree.column("Date", width=150)
        self.tree.column("Result", width=100)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Button(self.tab_management, text="Aktualisieren", command=self.refresh_treeview).pack(pady=5)
        self.refresh_treeview()

    def setup_settings_tab(self):
        # Basis Pfade
        paths_frame = ttk.LabelFrame(self.tab_settings, text="Pfade & App-Konfiguration")
        paths_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(paths_frame, text="Base Directory:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_base_dir = ttk.Entry(paths_frame, width=60)
        self.entry_base_dir.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(paths_frame, text="Split APK Name:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.entry_split_name = ttk.Entry(paths_frame, width=60)
        self.entry_split_name.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(paths_frame, text="App Package:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.entry_app_package = ttk.Entry(paths_frame, width=60)
        self.entry_app_package.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(paths_frame, text="Signer JAR:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.entry_signer = ttk.Entry(paths_frame, width=60)
        self.entry_signer.grid(row=3, column=1, padx=5, pady=5)

        # Pipeline Editor
        pipeline_frame = ttk.LabelFrame(self.tab_settings, text="Befehls-Pipeline (JSON Format)")
        pipeline_frame.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Label(pipeline_frame,
                  text="Verfügbare Variablen: {BASE_DIR}, {SOURCE_DIR}, {DEST_DIR}, {EXTRACT_DIR}, {SPLIT_NAME}, {APP_PACKAGE}, {SIGNER_JAR}").pack(
            anchor="w", padx=5)

        self.text_pipeline = tk.Text(pipeline_frame, height=15, width=80, font=("Courier", 10))
        self.text_pipeline.pack(fill="both", expand=True, padx=5, pady=5)

        btn_frame = ttk.Frame(self.tab_settings)
        btn_frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(btn_frame, text="Standardwerte laden", command=self.restore_defaults).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Einstellungen speichern", command=self.save_config).pack(side="right", padx=5)

        self.populate_settings_tab()

    def populate_settings_tab(self):
        self.entry_base_dir.delete(0, tk.END)
        self.entry_base_dir.insert(0, self.config.get("BASE_DIR", ""))
        self.entry_split_name.delete(0, tk.END)
        self.entry_split_name.insert(0, self.config.get("SPLIT_NAME", ""))
        self.entry_app_package.delete(0, tk.END)
        self.entry_app_package.insert(0, self.config.get("APP_PACKAGE", ""))
        self.entry_signer.delete(0, tk.END)
        self.entry_signer.insert(0, self.config.get("SIGNER_JAR", ""))

        self.text_pipeline.delete("1.0", tk.END)
        self.text_pipeline.insert("1.0", json.dumps(self.config.get("PIPELINE", []), indent=4))

    # --- WORKFLOW LOGIK ---

    def add_patch_row(self):
        row_frame = ttk.Frame(self.patches_container)
        row_frame.pack(fill="x", pady=2)

        ttk.Label(row_frame, text="RAM:").pack(side="left", padx=2)
        e_ram = ttk.Entry(row_frame, width=12)
        e_ram.pack(side="left", padx=2)

        ttk.Label(row_frame, text="Base:").pack(side="left", padx=2)
        e_base = ttk.Entry(row_frame, width=12)
        e_base.insert(0, "00100000")
        e_base.pack(side="left", padx=2)

        ttk.Label(row_frame, text="Orig:").pack(side="left", padx=2)
        e_orig = ttk.Entry(row_frame, width=20)
        e_orig.pack(side="left", padx=2)

        ttk.Label(row_frame, text="Patch:").pack(side="left", padx=2)
        e_patch = ttk.Entry(row_frame, width=20)
        e_patch.pack(side="left", padx=2)

        ttk.Button(row_frame, text="X", width=3, command=lambda: self.remove_patch_row(row_frame)).pack(side="left",
                                                                                                        padx=5)

        self.patch_rows.append({"frame": row_frame, "ram": e_ram, "base": e_base, "orig": e_orig, "patch": e_patch})

    def remove_patch_row(self, frame):
        frame.destroy()
        self.patch_rows = [p for p in self.patch_rows if p["frame"] != frame]

    def generate_new_id(self):
        self.current_id = f"PID-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.lbl_patch_id.config(text=self.current_id)

    def log(self, message):
        self.text_console.insert("end", message + "\n")
        self.text_console.see("end")
        self.update()

    def format_string(self, text):
        return text.format(
            BASE_DIR=self.config["BASE_DIR"],
            SOURCE_DIR=self.SOURCE_DIR,
            DEST_DIR=self.DEST_DIR,
            EXTRACT_DIR=self.EXTRACT_DIR,
            SPLIT_NAME=self.config["SPLIT_NAME"],
            APP_PACKAGE=self.config["APP_PACKAGE"],
            SIGNER_JAR=os.path.join(self.config["BASE_DIR"], self.config["SIGNER_JAR"])
        )

    def run_cmd(self, cmd, cwd):
        cmd_formatted = self.format_string(cmd)
        cwd_formatted = self.format_string(cwd)

        if not os.path.exists(cwd_formatted):
            os.makedirs(cwd_formatted, exist_ok=True)

        self.log(f"> [{cwd_formatted}]\n> {cmd_formatted}")
        try:
            result = subprocess.run(cmd_formatted, shell=True, cwd=cwd_formatted, capture_output=True, text=True)
            if result.stdout: self.log(result.stdout)
            if result.stderr: self.log(f"WARN/ERR: {result.stderr}")
            return result.returncode == 0
        except Exception as e:
            self.log(f"[!] Systemfehler bei CMD-Ausführung: {e}")
            return False

    def apply_hex_patches(self):
        lib_path = os.path.join(self.EXTRACT_DIR, "lib", "arm64-v8a", "libflutter.so")
        if not os.path.exists(lib_path):
            self.log(f"[!] Ziel-Bibliothek nicht gefunden: {lib_path}")
            return False

        try:
            with open(lib_path, "r+b") as f:
                for idx, p in enumerate(self.patch_rows):
                    ram_val = p["ram"].get().strip()
                    if not ram_val: continue

                    ram_val = int(ram_val, 16)
                    base_val = int(p["base"].get().strip(), 16)
                    file_offset = ram_val - base_val
                    patch_hex = p["patch"].get().replace(" ", "")

                    self.log(f"[*] Wende Patch {idx + 1} an: Offset 0x{file_offset:X}")
                    f.seek(file_offset)
                    f.write(bytes.fromhex(patch_hex))
            return True
        except Exception as e:
            self.log(f"[!] Fehler beim Patchen: {e}")
            return False

    def run_pipeline(self):
        self.log("=== STARTE DYNAMISCHE PIPELINE ===")
        self.current_archive_path = os.path.join(self.ARCHIVE_DIR,
                                                 f"{self.current_id}_{self.entry_patch_name.get().replace(' ', '_')}")
        os.makedirs(self.current_archive_path, exist_ok=True)

        # Zielordner vorab leeren
        for f in os.listdir(self.DEST_DIR):
            os.remove(os.path.join(self.DEST_DIR, f))

        pipeline = self.config.get("PIPELINE", [])

        for step in pipeline:
            step_name = step.get("name", "Unnamed Step")
            self.log(f"\n--- Schritt: {step_name} ---")

            if step.get("type") == "anchor_patch":
                success = self.apply_hex_patches()
            else:
                success = self.run_cmd(step.get("cmd", ""), step.get("cwd", self.config["BASE_DIR"]))

            if not success:
                self.log(f"\n[!] FEHLER: Pipeline bei Schritt '{step_name}' abgebrochen.")
                messagebox.showerror("Pipeline Fehler", f"Der Schritt '{step_name}' ist fehlgeschlagen. Abbruch.")
                return

        # Am Ende signierte APKs ins Archiv kopieren
        for f in os.listdir(self.DEST_DIR):
            if f.endswith("-aligned-debugSigned.apk"):
                shutil.copy(os.path.join(self.DEST_DIR, f), self.current_archive_path)

        self.log("\n=== PIPELINE ERFOLGREICH ABGESCHLOSSEN ===")

    def flash_device(self):
        apks = [f for f in os.listdir(self.DEST_DIR) if f.endswith("-aligned-debugSigned.apk")]
        if apks:
            cmd = f"adb install-multiple -i com.android.vending {' '.join(apks)}"
            self.run_cmd(cmd, self.DEST_DIR)

    def start_trace(self):
        self.run_cmd("adb logcat -c", self.config["BASE_DIR"])
        messagebox.showinfo("Trace", "Bitte starte die App und klicke OK.")
        pid_res = subprocess.run(f"adb shell pidof {self.config['APP_PACKAGE']}", shell=True, capture_output=True,
                                 text=True)
        pid = pid_res.stdout.strip()

        if not pid:
            self.log("[!] PID nicht gefunden.")
            return

        trace_file = os.path.join(self.current_archive_path if self.current_archive_path else self.ARCHIVE_DIR,
                                  "trace.txt")
        self.logcat_out = open(trace_file, "w")
        self.logcat_process = subprocess.Popen(f"adb logcat --pid={pid}", shell=True, stdout=self.logcat_out,
                                               stderr=subprocess.STDOUT)

        self.btn_start_trace.config(state="disabled")
        self.btn_stop_trace.config(state="normal")
        self.log("[*] Trace läuft...")

    def stop_trace(self):
        if self.logcat_process:
            self.logcat_process.terminate()
            self.logcat_out.close()
            self.logcat_process = None
        self.btn_start_trace.config(state="normal")
        self.btn_stop_trace.config(state="disabled")

    def save_test_result(self):
        patch_list = [
            {"ram": p["ram"].get(), "base": p["base"].get(), "orig": p["orig"].get(), "patch": p["patch"].get()} for p
            in self.patch_rows]
        record = {
            "id": self.current_id,
            "name": self.entry_patch_name.get(),
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "result": self.combo_result.get(),
            "observation": self.text_obs.get("1.0", tk.END).strip(),
            "patches": patch_list
        }

        self.history_data.append(record)
        self.save_history()
        self.refresh_treeview()

        md_block = f"### 🔧 RE-Patch-Report ({record['id']})\n* **Name:** {record['name']}\n* **Testergebnis:** {record['result']}\n"
        for i, pt in enumerate(
            patch_list): md_block += f"  * **Patch {i + 1}:** RAM: `0x{pt['ram']}` | Hex: `{pt['patch']}`\n"
        md_block += f"\n**Beobachtung:**\n{record['observation']}\n"

        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{md_block}\n---\n")

        self.log("\n=== GESPEICHERT ===")
        self.generate_new_id()
        self.entry_patch_name.delete(0, tk.END)

    def refresh_treeview(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        for record in reversed(self.history_data):
            self.tree.insert("", "end", values=(record["id"], record["name"], record["timestamp"], record["result"]))


if __name__ == "__main__":
    app = KippyReFramework()
    app.mainloop()