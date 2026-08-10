import os
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import json

# ==============================================================================
# KONFIGURATION & PFADE
# ==============================================================================
BASE_DIR = r"C:\Users\Lenovo\Downloads\APK-Tools\Script"
SOURCE_DIR = os.path.join(BASE_DIR, "source")
DEST_DIR = os.path.join(BASE_DIR, "destination")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archives")
LOG_FILE = os.path.join(BASE_DIR, "Kippy_RE_Log.md")
JSON_HISTORY = os.path.join(BASE_DIR, "RE_History.json")
SIGNER_JAR = os.path.join(BASE_DIR, "uber-apk-signer-1.3.0.jar")
SPLIT_NAME = "split_config.arm64_v8a"
APP_PACKAGE = "com.datamars.kippynew"

for d in [SOURCE_DIR, DEST_DIR, ARCHIVE_DIR]:
    os.makedirs(d, exist_ok=True)


class KippyReFramework(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kippy RE-Framework V4 - Multi-Patch & Test Management")
        self.geometry("900x900")

        self.logcat_process = None
        self.current_archive_path = None
        self.patch_rows = []  # Speichert die dynamischen Patch-Eingabefelder

        # Lade Historie
        self.history_data = self.load_history()

        self.create_widgets()
        self.generate_new_id()

    def load_history(self):
        if os.path.exists(JSON_HISTORY):
            with open(JSON_HISTORY, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save_history(self):
        with open(JSON_HISTORY, "w", encoding="utf-8") as f:
            json.dump(self.history_data, f, indent=4)

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # Tabs
        self.tab_workspace = ttk.Frame(self.notebook)
        self.tab_management = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_workspace, text="🔧 Workspace (Patch & Flash)")
        self.notebook.add(self.tab_management, text="📊 Test Management")

        self.setup_workspace_tab()
        self.setup_management_tab()

    def setup_workspace_tab(self):
        # --- Meta Info ---
        meta_frame = ttk.LabelFrame(self.tab_workspace, text="1. Patch Meta-Daten")
        meta_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(meta_frame, text="Patch-ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_patch_id = ttk.Label(meta_frame, text="Wird generiert...", font=("Courier", 10, "bold"))
        self.lbl_patch_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(meta_frame, text="Manueller Name:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.entry_patch_name = ttk.Entry(meta_frame, width=40)
        self.entry_patch_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        # --- Dynamic Patches ---
        self.patches_frame = ttk.LabelFrame(self.tab_workspace, text="2. Hex-Patches")
        self.patches_frame.pack(fill="x", padx=10, pady=5)

        btn_add_patch = ttk.Button(self.patches_frame, text="+ Patch hinzufügen", command=self.add_patch_row)
        btn_add_patch.pack(anchor="w", padx=5, pady=5)

        self.patches_container = ttk.Frame(self.patches_frame)
        self.patches_container.pack(fill="x", padx=5, pady=5)

        self.add_patch_row()  # Initialer Patch

        # --- Actions ---
        action_frame = ttk.LabelFrame(self.tab_workspace, text="3. Build & Flash")
        action_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(action_frame, text="1. Build & Sign", command=self.build_and_sign).grid(row=0, column=0, padx=5,
                                                                                           pady=5)
        ttk.Button(action_frame, text="2. Flash to Device", command=self.flash_device).grid(row=0, column=1, padx=5,
                                                                                            pady=5)

        self.btn_start_trace = ttk.Button(action_frame, text="Start Trace", command=self.start_trace)
        self.btn_start_trace.grid(row=0, column=2, padx=5, pady=5)
        self.btn_stop_trace = ttk.Button(action_frame, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_stop_trace.grid(row=0, column=3, padx=5, pady=5)

        # --- Results ---
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

        # --- Console ---
        self.text_console = tk.Text(self.tab_workspace, height=10, bg="black", fg="lightgreen")
        self.text_console.pack(fill="both", expand=True, padx=10, pady=5)

    def setup_management_tab(self):
        # Treeview (Tabelle)
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

        btn_refresh = ttk.Button(self.tab_management, text="Aktualisieren", command=self.refresh_treeview)
        btn_refresh.pack(pady=5)

        self.refresh_treeview()

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

        btn_remove = ttk.Button(row_frame, text="X", width=3, command=lambda: self.remove_patch_row(row_frame))
        btn_remove.pack(side="left", padx=5)

        self.patch_rows.append({
            "frame": row_frame,
            "ram": e_ram,
            "base": e_base,
            "orig": e_orig,
            "patch": e_patch
        })

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

    def run_cmd(self, cmd, cwd=None):
        self.log(f"> {cmd}")
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        if result.stdout: self.log(result.stdout)
        if result.stderr: self.log(f"WARN: {result.stderr}")
        return result.returncode == 0

    def build_and_sign(self):
        self.log("=== STARTE BUILD-PROZESS ===")
        self.current_archive_path = os.path.join(ARCHIVE_DIR,
                                                 f"{self.current_id}_{self.entry_patch_name.get().replace(' ', '_')}")
        os.makedirs(self.current_archive_path, exist_ok=True)

        for f in os.listdir(DEST_DIR): os.remove(os.path.join(DEST_DIR, f))

        apk_path = os.path.join(SOURCE_DIR, f"{SPLIT_NAME}.apk")
        zip_path = os.path.join(SOURCE_DIR, f"{SPLIT_NAME}.zip")
        extract_path = os.path.join(SOURCE_DIR, SPLIT_NAME)

        if os.path.exists(extract_path): shutil.rmtree(extract_path)

        os.rename(apk_path, zip_path)
        shutil.unpack_archive(zip_path, extract_path, "zip")
        os.rename(zip_path, apk_path)

        lib_path = os.path.join(extract_path, "lib", "arm64-v8a", "libflutter.so")

        try:
            with open(lib_path, "r+b") as f:
                for idx, p in enumerate(self.patch_rows):
                    ram_val = int(p["ram"].get().strip(), 16)
                    base_val = int(p["base"].get().strip(), 16)
                    file_offset = ram_val - base_val
                    patch_hex = p["patch"].get().replace(" ", "")

                    self.log(f"[*] Wende Patch {idx + 1} an: Offset 0x{file_offset:X}")
                    f.seek(file_offset)
                    f.write(bytes.fromhex(patch_hex))
        except Exception as e:
            self.log(f"[!] Fehler beim Patchen: {e}")
            return

        self.run_cmd(f"jar c0f {SPLIT_NAME}.apk AndroidManifest.xml lib stamp-cert-sha256 META-INF", cwd=extract_path)
        shutil.move(os.path.join(extract_path, f"{SPLIT_NAME}.apk"), os.path.join(DEST_DIR, f"{SPLIT_NAME}.apk"))

        for f in os.listdir(SOURCE_DIR):
            if f.endswith(".apk") and f != f"{SPLIT_NAME}.apk":
                shutil.copy(os.path.join(SOURCE_DIR, f), os.path.join(DEST_DIR, f))

        shutil.rmtree(extract_path)
        self.run_cmd(f"java -jar {SIGNER_JAR} -a . --allowResign", cwd=DEST_DIR)

        for f in os.listdir(DEST_DIR):
            if f.endswith("-aligned-debugSigned.apk"):
                shutil.copy(os.path.join(DEST_DIR, f), self.current_archive_path)

        self.log("=== BUILD ERFOLGREICH ABGESCHLOSSEN ===")

    def flash_device(self):
        apks = [f for f in os.listdir(DEST_DIR) if f.endswith("-aligned-debugSigned.apk")]
        if apks:
            self.run_cmd(f"adb install-multiple -i com.android.vending {' '.join(apks)}", cwd=DEST_DIR)

    def start_trace(self):
        self.run_cmd("adb logcat -c")
        messagebox.showinfo("Trace", "Bitte starte die Kippy App und klicke OK.")
        pid = subprocess.run(f"adb shell pidof {APP_PACKAGE}", shell=True, capture_output=True,
                             text=True).stdout.strip()
        if not pid:
            self.log("[!] PID nicht gefunden.")
            return

        trace_file = os.path.join(self.current_archive_path if self.current_archive_path else ARCHIVE_DIR, "trace.txt")
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
        patch_list = []
        for p in self.patch_rows:
            patch_list.append({
                "ram": p["ram"].get(),
                "base": p["base"].get(),
                "orig": p["orig"].get(),
                "patch": p["patch"].get()
            })

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

        # Markdown Export formatieren
        md_block = f"### 🔧 RE-Patch-Report ({record['id']})\n"
        md_block += f"* **Name:** {record['name']}\n"
        md_block += f"* **Testergebnis:** {record['result']}\n"
        for i, pt in enumerate(patch_list):
            md_block += f"  * **Patch {i + 1}:** RAM: `0x{pt['ram']}` | Hex: `{pt['patch']}`\n"
        md_block += f"\n**Beobachtung:**\n{record['observation']}\n"

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{md_block}\n---\n")

        self.log("\n=== GESPEICHERT ===")
        self.generate_new_id()  # Bereite nächste ID vor
        self.entry_patch_name.delete(0, tk.END)

    def refresh_treeview(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in reversed(self.history_data):
            self.tree.insert("", "end", values=(record["id"], record["name"], record["timestamp"], record["result"]))


if __name__ == "__main__":
    app = KippyReFramework()
    app.mainloop()