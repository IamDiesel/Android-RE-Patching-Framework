import os
import shutil
import datetime
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import subprocess
import re
import time
import concurrent.futures
import multiprocessing
import pickle

from cg_manager import is_system_api


# ==========================================
# SETTINGS TAB
# ==========================================
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
                 ("Signer", "SIGNER_JAR")]):
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


# ==========================================
# HISTORY TAB
# ==========================================
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

        self.tree.column("ID", width=130);
        self.tree.column("App", width=120);
        self.tree.column("Ver.", width=60)
        self.tree.column("Datum", width=130);
        self.tree.column("Name", width=150)
        self.tree.column("Patches", width=250);
        self.tree.column("Resultat", width=80)
        self.tree.column("Kommentar", width=250)

        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        ttk.Button(self, text="Refresh", command=self.refresh_tree).pack(pady=5)
        ttk.Label(self,
                  text="Doppelklick auf einen Eintrag, um ihn zu bearbeiten oder in den Workspace zu laden.").pack(
            pady=2)
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


# ==========================================
# SMALI STUDIO TAB
# ==========================================
# ==========================================
# SMALI STUDIO TAB
# ==========================================
class SmaliStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.smali_patches = []
        self.current_smali_file = ""
        self.current_method_name = ""

        # In-Memory RAM Cache
        self.ram_cache = []
        self.is_indexed = False
        self.is_indexing = False

        self.create_widgets()


    def get_smali_dir(self):
        """Hilfsfunktion: Liefert immer das Verzeichnis der entpackten base.apk"""
        app_source = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        return os.path.join(app_source, "base_unpacked")

    def create_widgets(self):
        # -- 1. TOOLBAR GANZ OBEN --
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", pady=5, padx=5)

        ttk.Button(top_bar, text="📦 1. APK Entpacken (Apktool)", command=self.unpack_apk_async).pack(side="left",
                                                                                                     padx=5)

        self.progress_var = tk.IntVar()
        self.progress_bar = ttk.Progressbar(top_bar, variable=self.progress_var, maximum=100, length=150)
        self.lbl_progress_status = ttk.Label(top_bar, text="", font=("Segoe UI", 8, "italic"), foreground="gray")

        ttk.Button(top_bar, text="🔍 Globale Suche", command=self.open_global_search).pack(side="left", padx=5)
        ttk.Button(top_bar, text="📄 In ext. Editor öffnen", command=self.open_in_external_editor).pack(side="left",
                                                                                                       padx=5)
        ttk.Button(top_bar, text="💾 Zur Patch-Liste", command=self.add_smali_patch).pack(side="right", padx=5)

        self.lbl_smali_file = ttk.Label(top_bar, text="Keine Datei geladen", font=("Segoe UI", 9, "bold"))
        self.lbl_smali_file.pack(side="right", padx=10)

        # =========================================================
        # 2. PATCH-LISTE (Fest unten verankert, wird ZUERST gepackt!)
        # =========================================================
        f_patches = ttk.LabelFrame(self, text="Aktive Smali Patches (Warten auf Build)")
        f_patches.pack(side="bottom", fill="both", expand=False, padx=5, pady=5)

        self.smali_tree = ttk.Treeview(f_patches, columns=("File", "Snippet"), show="headings", height=5)
        self.smali_tree.heading("File", text="Datei")
        self.smali_tree.heading("Snippet", text="Edit Preview")
        self.smali_tree.column("File", width=400)

        patch_scroll = ttk.Scrollbar(f_patches, orient="vertical", command=self.smali_tree.yview)
        self.smali_tree.configure(yscrollcommand=patch_scroll.set)

        patch_scroll.pack(side="right", fill="y")
        self.smali_tree.pack(side="left", fill="both", expand=True)

        self.smali_tree.bind("<Delete>", lambda e: self.remove_smali_patch())

        # =========================================================
        # 3. EDITOR BEREICH (Füllt den restlichen Platz in der Mitte)
        # =========================================================
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # SPALTE 1: OUTLINE & CALLGRAPH
        left_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(left_pane, weight=1)

        f_outline = ttk.LabelFrame(left_pane, text="Klassenübersicht")
        left_pane.add(f_outline, weight=1)
        self.tree_outline = ttk.Treeview(f_outline, columns=("Method",), show="headings")
        self.tree_outline.heading("Method", text="Signatur")
        self.tree_outline.pack(fill="both", expand=True)
        self.tree_outline.bind("<Double-1>", self.on_outline_double_click)
        self.tree_outline.tag_configure("system_api", foreground="gray")

        f_callgraph = ttk.LabelFrame(left_pane, text="Call Graph (Referenzen)")
        cg_toolbar = ttk.Frame(f_callgraph)
        cg_toolbar.pack(fill="x", padx=2, pady=2)
        ttk.Button(cg_toolbar, text="🗑 Graph leeren", command=self.clear_callgraph).pack(side="left")
        ttk.Button(cg_toolbar, text="💾 Sichern", command=self.save_callgraph).pack(side="right")
        ttk.Button(cg_toolbar, text="📂 Laden", command=self.load_callgraph).pack(side="right")

        left_pane.add(f_callgraph, weight=2)
        self.tree_callstack = ttk.Treeview(f_callgraph, columns=("File",), show="tree headings")
        self.tree_callstack.heading("#0", text="Methode")
        self.tree_callstack.heading("File", text="Pfad")
        self.tree_callstack.column("#0", width=180)
        self.tree_callstack.column("File", width=100)
        self.tree_callstack.pack(fill="both", expand=True)
        self.tree_callstack.tag_configure("system_api", foreground="gray")
        self.tree_callstack.bind("<Double-1>", self.on_callgraph_double_click)
        self.tree_callstack.bind("<<TreeviewOpen>>", self.on_cg_node_expand)

        # SPALTE 2: EDITOR
        center_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(center_pane, weight=3)

        frame_orig = ttk.LabelFrame(center_pane, text="Original Code (Read-Only)")
        center_pane.add(frame_orig, weight=1)
        self.txt_smali_orig = tk.Text(frame_orig, wrap="none", font=("Courier", 9), state="disabled", bg="#f0f0f0")
        self.txt_smali_orig.pack(fill="both", expand=True)

        frame_mid = ttk.Frame(center_pane)
        center_pane.add(frame_mid, weight=0)
        ttk.Button(frame_mid, text="⬇ Code zum Editieren kopieren ⬇", command=self.copy_smali_to_edit).pack(pady=2)

        frame_edit = ttk.LabelFrame(center_pane, text="Editierter Code (Dein Patch)")
        center_pane.add(frame_edit, weight=1)
        self.txt_smali_edit = tk.Text(frame_edit, wrap="none", font=("Courier", 9))
        self.txt_smali_edit.pack(fill="both", expand=True)

        # SPALTE 3: XREFS
        right_pane = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_pane, weight=1)

        f_outgoing = ttk.LabelFrame(right_pane, text="Ausgehende Aufrufe (Calls)")
        right_pane.add(f_outgoing, weight=1)
        self.tree_outgoing = ttk.Treeview(f_outgoing, columns=("Target",), show="headings")
        self.tree_outgoing.heading("Target", text="Aufgerufene Methode")
        self.tree_outgoing.pack(fill="both", expand=True)
        self.tree_outgoing.tag_configure("system_api", foreground="gray")
        self.tree_outgoing.bind("<Double-1>", self.on_outgoing_double_click)

        f_incoming = ttk.LabelFrame(right_pane, text="Eingehende Aufrufe (XREF)")
        right_pane.add(f_incoming, weight=1)
        ttk.Button(f_incoming, text="🔍 Finde Aufrufer (Projekt scannen)", command=self.find_incoming_xrefs).pack(
            fill="x", pady=2)

        self.tree_incoming = ttk.Treeview(f_incoming, columns=("File", "Method"), show="headings")
        self.tree_incoming.heading("File", text="Datei")
        self.tree_incoming.heading("Method", text="Methode")
        self.tree_incoming.column("File", width=80)
        self.tree_incoming.column("Method", width=120)
        self.tree_incoming.pack(fill="both", expand=True)
        self.tree_incoming.tag_configure("system_api", foreground="gray")
        self.tree_incoming.bind("<Double-1>", self.on_incoming_double_click)

    def get_dir_size_mb(self, path):
        """Hilfsfunktion: Berechnet die Größe eines Ordners in Megabyte."""
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
        except:
            pass
        return total / (1024 * 1024)

    def unpack_apk_async(self):
        if self.app.check_lock(): return

        app_source_dir = self.app.cfg.paths.get("APP_SOURCE_DIR", "")
        if not app_source_dir:
            return messagebox.showwarning("Fehler", "Bitte lade zuerst eine App über den App Manager!")

        base_apk_path = os.path.join(app_source_dir, "base.apk")
        if not os.path.exists(base_apk_path):
            return messagebox.showwarning("Fehler",
                                          "base.apk nicht gefunden! Bitte stelle sicher, dass die App gepullt wurde.")

        smali_dir = self.get_smali_dir()

        def task():
            self.app.is_unpacking = True

            # -r verhindert das Blockieren bei defekten Ressourcen,
            # -f überschreibt bestehende Ordner
            cmd = f'apktool d "base.apk" -o "base_unpacked" -f -r'
            self.app.log(f"[*] Starte Entpacken für Smali: {cmd}")

            self.app.after(0, lambda: self.progress_bar.pack(side="left", padx=5))
            self.app.after(0, lambda: self.lbl_progress_status.pack(side="left", padx=5))
            self.app.after(0, lambda: self.progress_var.set(5))
            self.app.after(0, lambda: self.lbl_progress_status.config(text="Initialisiere Apktool..."))

            try:
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                process = subprocess.Popen(cmd, shell=True, cwd=app_source_dir,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, startupinfo=startupinfo,
                                           errors="replace")

                self.last_apktool_log = "Starte..."

                # THREAD 1: Liest die Konsole von Apktool aus (blockiert bei Zombie-Bug)
                def log_reader():
                    for line in process.stdout:
                        clean_line = line.strip()
                        if clean_line:
                            self.app.log(f"[Apktool] {clean_line}")
                            self.last_apktool_log = clean_line

                            if "Loading resource table" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(20))
                            elif "Decoding AndroidManifest.xml" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(40))
                            elif "Baksmaling" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(60))
                            elif "Copying assets" in clean_line or "Copying raw" in clean_line or "Copying lib" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(80))
                            elif "Copying unknown" in clean_line or "Copying original" in clean_line:
                                self.app.after(0, lambda: self.progress_var.set(90))

                reader_thread = threading.Thread(target=log_reader, daemon=True)
                reader_thread.start()

                # THREAD 2 (Main-Task): Überwacht den Festplattenplatz in Echtzeit
                last_size = -1
                stuck_counter = 0

                while process.poll() is None:
                    time.sleep(1)
                    current_size = self.get_dir_size_mb(smali_dir)

                    # Update Live-Status in der GUI
                    status_text = f"Entpacke... {current_size:.1f} MB geschrieben"
                    if "Copying unknown" in self.last_apktool_log or "Copying original" in self.last_apktool_log:
                        status_text = f"Kopiere Assets... {current_size:.1f} MB"

                    self.app.after(0, lambda txt=status_text: self.lbl_progress_status.config(text=txt))

                    # Watchdog-Logik gegen Windows-Zombies
                    if current_size == last_size and current_size > 5:
                        stuck_counter += 1
                        # 5 Sekunden Stillstand + Apktool ist im Kopiermodus -> Zombie entdeckt!
                        if stuck_counter >= 5 and "Copying" in self.last_apktool_log:
                            self.app.log(
                                "[*] Ordner wächst nicht mehr. Beende blockierenden Apktool-Prozess (Zombie-Schutz)...")
                            process.terminate()
                            break
                    else:
                        stuck_counter = 0
                        last_size = current_size

                reader_thread.join(timeout=1.0)

                # Exitcode 1 oder None ist in diesem Fall ok, da wir den Zombie selbst getötet haben
                if process.returncode in [0, 1, None]:
                    self.app.after(0, lambda: self.progress_var.set(100))
                    self.app.after(0, lambda: self.lbl_progress_status.config(text="Erfolgreich entpackt!"))
                    self.app.log(f"[+] base.apk erfolgreich entpackt nach: {smali_dir}")
                    # NEU: Startet das RAM-Caching direkt nach dem Entpacken
                    self.build_ram_index()
                else:
                    self.app.log(f"[!] Fehler beim Entpacken (Exit {process.returncode}).")
                    self.app.after(0, lambda: self.lbl_progress_status.config(
                        text=f"Fehler! Exit-Code: {process.returncode}"))

            except Exception as e:
                self.app.log(f"[!] Ausnahme beim Entpacken: {e}")
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Systemfehler aufgetreten!"))
            finally:
                self.app.is_unpacking = False
                self.app.after(3000, lambda: self.progress_bar.pack_forget())
                self.app.after(3000, lambda: self.lbl_progress_status.pack_forget())

        threading.Thread(target=task, daemon=True).start()
    def open_in_external_editor(self):
        if not self.current_smali_file: return
        filepath = os.path.join(self.get_smali_dir(), self.current_smali_file)
        if os.path.exists(filepath):
            try:
                os.startfile(filepath)
            except:
                self.app.log("[!] Konnte externen Editor nicht starten.")

    def load_method(self, rel_filepath, target_line=None, method_signature=None, add_as_root=True):
        smali_dir = self.get_smali_dir()
        filepath = os.path.join(smali_dir, rel_filepath)

        if not os.path.exists(filepath):
            return self.app.log(f"[!] Datei nicht gefunden: {filepath}")

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            return self.app.log(f"[!] Lese-Fehler: {e}")

        start_idx, end_idx = -1, -1

        if target_line is not None:
            idx = target_line - 1
            while idx >= 0:
                if lines[idx].strip().startswith(".method"):
                    start_idx = idx
                    break
                idx -= 1
        elif method_signature is not None:
            for i, line in enumerate(lines):
                if line.strip().startswith(".method") and method_signature in line:
                    start_idx = i
                    break

        if start_idx != -1:
            idx = start_idx
            while idx < len(lines):
                if lines[idx].strip().startswith(".end method"):
                    end_idx = idx
                    break
                idx += 1

        if start_idx != -1 and end_idx != -1:
            block = "".join(lines[start_idx:end_idx + 1])
            method_def = lines[start_idx].strip().replace(".method ", "")

            self.current_smali_file = rel_filepath.replace("\\", "/")
            self.current_method_name = method_def
            self.lbl_smali_file.config(
                text=f"{os.path.basename(self.current_smali_file)} -> {method_def.split('(')[0]}")

            self.txt_smali_orig.config(state="normal")
            self.txt_smali_orig.delete("1.0", tk.END)
            self.txt_smali_orig.insert("1.0", block)
            self.txt_smali_orig.config(state="disabled")

            self.app.cg.add_node(self.current_smali_file, self.current_method_name)
            if add_as_root:
                node_id = f"{self.current_smali_file}|{self.current_method_name}"
                self.app.cg.make_root(node_id)

            self.parse_outgoing_calls(block)
            self.update_outline(lines)
            self.refresh_callgraph_ui()
        else:
            self.app.log("[!] Konnte Methode in der Datei nicht extrahieren.")

    def update_outline(self, lines):
        for i in self.tree_outline.get_children(): self.tree_outline.delete(i)
        for line in lines:
            line = line.strip()
            if line.startswith(".method"):
                sig = line.replace(".method ", "")
                display_sig = re.sub(
                    r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*',
                    '', sig)
                tags = ("system_api", sig) if is_system_api(
                    "L" + self.current_smali_file.replace(".smali", "") + ";") else (sig,)
                self.tree_outline.insert("", "end", values=(display_sig,), tags=tags)

    def parse_outgoing_calls(self, method_block):
        for i in self.tree_outgoing.get_children(): self.tree_outgoing.delete(i)
        matches = re.findall(r'invoke-\w+(?:/[a-z0-9]+)? \{[^}]*\}, (L[^;]+;->[^\s]+)', method_block)
        unique_calls = list(dict.fromkeys(matches))

        for call in unique_calls:
            cls_part, meth_part = call.split(";->")
            tags = ("system_api",) if is_system_api(cls_part) else ()
            self.tree_outgoing.insert("", "end", values=(call,), tags=tags)

            rel_base = cls_part[1:] + ".smali"
            found_path = self.resolve_smali_path(rel_base)
            callee_path = found_path if found_path else cls_part[1:]
            self.app.cg.add_edge(self.current_smali_file, self.current_method_name, callee_path, meth_part)

    def resolve_smali_path(self, rel_base):
        """Dynamische Erkennung, egal ob die App bis smali_classes2 oder smali_classes99 geht."""
        smali_dir = self.get_smali_dir()
        try:
            for item in os.listdir(smali_dir):
                if item.startswith("smali") and os.path.isdir(os.path.join(smali_dir, item)):
                    test_path = os.path.join(item, rel_base)
                    if os.path.exists(os.path.join(smali_dir, test_path)):
                        return test_path
        except:
            pass
        return None

    def find_incoming_xrefs(self):
        if self.app.check_lock() or not self.current_smali_file: return

        if not self.is_indexed:
            if not self.is_indexing: self.build_ram_index()
            return messagebox.showinfo("Index fehlt", "RAM-Index wird gerade geladen. Bitte gleich nochmal klicken.")

        parts = self.current_smali_file.split("/")
        dalvik_class = f"L{'/'.join(parts[1:]).replace('.smali', '')};" if parts[0].startswith(
            "smali") else f"L{self.current_smali_file.replace('.smali', '')};"
        method_name = \
        re.sub(r'^(public |private |protected |static |final |constructor |synthetic |bridge |declared-synchronized )*',
               '', self.current_method_name).split('(')[0]

        search_term = f"{dalvik_class}->{method_name}("
        self.app.log(f"[*] Suche XREFs für: {search_term}")

        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        self.tree_incoming.insert("", "end", values=("Suche läuft im RAM...", ""))

        self.cancel_xref_flag = False

        def cancel_xref():
            self.cancel_xref_flag = True

        btn_cancel_xref = ttk.Button(self.tree_incoming.master, text="❌ XREF Suche abbrechen", command=cancel_xref)
        btn_cancel_xref.pack(fill="x", pady=2)

        def search_task():
            results = []

            # WIR DURCHSUCHEN NUR NOCH DEN RAM-CACHE!
            for rel_path, content in self.ram_cache:
                if self.cancel_xref_flag or len(results) >= 500: break

                # Wenn es keine Smali Datei ist (z.B. XML), überspringen wir das Parsing
                if not rel_path.endswith(".smali"): continue

                if search_term in content:
                    lines = content.splitlines()
                    for line_no, line in enumerate(lines):
                        if search_term in line:
                            idx = line_no
                            # Rückwärts gehen, um die aufrufende Methode zu finden
                            while idx >= 0:
                                if lines[idx].strip().startswith(".method"):
                                    caller_method = lines[idx].strip().replace(".method ", "")
                                    results.append((rel_path, caller_method, line_no + 1))
                                    break
                                idx -= 1
                            if len(results) >= 500: break

            self.app.after(0, lambda: finish_xref(results))

        def finish_xref(results):
            btn_cancel_xref.destroy()
            if self.cancel_xref_flag:
                for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
                self.tree_incoming.insert("", "end", values=("Suche abgebrochen.", ""))
                return
            if len(results) >= 500:
                self.app.log("[!] Limit erreicht: Zeige nur die ersten 500 Aufrufer (GUI Schutz).")
            self._update_incoming_ui(results)

        threading.Thread(target=search_task, daemon=True).start()

    def build_ram_index(self):
        smali_dir = self.get_smali_dir()
        if not os.path.exists(smali_dir): return

        if self.is_indexing: return

        def task():
            self.is_indexing = True

            try:
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Prüfe RAM-Index..."))
                self.app.after(0, lambda: self.lbl_progress_status.pack(side="left", padx=5))

                self.ram_cache.clear()
                self.is_indexed = False

                # NEU: Der Dateiname wird dynamisch aus dem Package-Namen generiert
                pkg_name = self.app.cfg.config.get("APP_PACKAGE", "app")
                index_file = os.path.join(smali_dir, f".{pkg_name}_index.pkl")

                # DER SCHNELLE WEG (Sekundenbruchteile)
                if os.path.exists(index_file):
                    self.app.log(f"[*] Fand vorberechneten Index ({index_file}). Lade in RAM...")
                    with open(index_file, "rb") as f:
                        self.ram_cache = pickle.load(f)

                # DER GRÜNDLICHE WEG (Einmalig)
                else:
                    self.app.log("[*] Kein Index gefunden. Sammle Dateipfade (das geht schnell)...")

                    filepaths = []
                    for root, _, files in os.walk(smali_dir):
                        for file in files:
                            if file.endswith(".smali") or file.endswith(".xml"):
                                filepaths.append(os.path.join(root, file))

                    total_files = len(filepaths)
                    self.app.log(f"[*] {total_files} Dateien gefunden. Starte Multi-Threaded Lese-Vorgang...")

                    cache = []
                    files_read = 0

                    def read_file(path):
                        try:
                            with open(path, "r", encoding="utf-8") as f:
                                return (os.path.relpath(path, smali_dir), f.read())
                        except:
                            return None

                    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
                        futures = [executor.submit(read_file, p) for p in filepaths]

                        for future in concurrent.futures.as_completed(futures):
                            res = future.result()
                            if res: cache.append(res)

                            files_read += 1
                            if files_read % 1000 == 0 or files_read == total_files:
                                pct = int((files_read / total_files) * 100)
                                msg = f"Lese in RAM... {files_read}/{total_files} ({pct}%)"
                                self.app.after(0, lambda m=msg: self.lbl_progress_status.config(text=m))

                                if files_read % 15000 == 0:
                                    self.app.log(
                                        f"[*] Index-Fortschritt: {files_read} von {total_files} Dateien in den RAM geladen...")

                    self.ram_cache = cache

                    try:
                        self.app.log(f"[*] Lese-Vorgang beendet! Speichere Cache in {index_file} ...")
                        self.app.after(0, lambda: self.lbl_progress_status.config(text="Speichere .pkl Cache-Datei..."))
                        with open(index_file, "wb") as f:
                            pickle.dump(cache, f)
                        self.app.log("[+] Cache-Datei für künftige Schnellstarts erfolgreich geschrieben!")
                    except Exception as e:
                        self.app.log(f"[!] Konnte .pkl Index nicht speichern: {e}")

                self.is_indexed = True
                self.app.log(f"[+] RAM-Index bereit: {len(self.ram_cache)} Dateien geladen.")
                self.app.after(0, lambda: self.lbl_progress_status.config(
                    text=f"Index bereit ({len(self.ram_cache)} Dateien)."))

            except Exception as e:
                self.app.log(f"[!] Schwerer Fehler beim Indexieren: {e}")
                self.app.after(0, lambda: self.lbl_progress_status.config(text="Fehler beim Laden des Index!"))

            finally:
                self.is_indexing = False
                self.app.after(3000, lambda: self.lbl_progress_status.pack_forget())

        threading.Thread(target=task, daemon=True).start()

    def open_global_search(self):
        if self.app.check_lock(): return

        if not self.is_indexed:
            if not self.is_indexing:
                self.build_ram_index()
            return messagebox.showinfo("Index wird erstellt",
                                       "Die Codebasis wird gerade in den Arbeitsspeicher geladen. Bitte versuche es in wenigen Sekunden erneut.")

        top = tk.Toplevel(self)
        top.title("🔍 Globale RAM-Suche (Echtzeit)")
        top.geometry("900x550")
        top.attributes("-topmost", True)

        # -- Such- und Filter-Bereich --
        f_top = ttk.Frame(top)
        f_top.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_top, text="Suchbegriff:").grid(row=0, column=0, sticky="w", pady=2)
        ent_search = ttk.Entry(f_top, width=50)
        ent_search.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(f_top, text="Ergebnisse filtern:").grid(row=1, column=0, sticky="w", pady=2)
        ent_filter = ttk.Entry(f_top, width=50)
        ent_filter.grid(row=1, column=1, padx=5, pady=2)

        lbl_status = ttk.Label(top, text=f"Bereit. Durchsuche {len(self.ram_cache)} Dateien im RAM.")
        lbl_status.pack(pady=2)

        # -- Treeview mit Scrollbar --
        f_tree = ttk.Frame(top)
        f_tree.pack(fill="both", expand=True, padx=10, pady=5)

        tree = ttk.Treeview(f_tree, columns=("File", "Line", "Snippet"), show="headings")
        tree.heading("File", text="Datei")
        tree.heading("Line", text="Zeile")
        tree.heading("Snippet", text="Code-Ausschnitt")
        tree.column("Line", width=60)

        # NEU: Die Scrollbar für bis zu 10.000 Ergebnisse
        scrollbar = ttk.Scrollbar(f_tree, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        # Lokaler Speicher für die Live-Filterung
        all_results = []

        def clear_search():
            nonlocal all_results
            all_results = []
            for i in tree.get_children(): tree.delete(i)
            ent_search.delete(0, tk.END)
            ent_filter.delete(0, tk.END)
            lbl_status.config(text="Suche geleert.")

        def apply_filter(event=None):
            """Wird bei jedem Tastendruck im Filter-Feld ausgeführt."""
            for i in tree.get_children(): tree.delete(i)
            f_term = ent_filter.get().lower()

            filtered = []
            for r in all_results:
                # r[0] = Dateipfad, r[2] = Code-Ausschnitt
                if f_term in r[0].lower() or f_term in r[2].lower():
                    filtered.append(r)

            for r in filtered:
                tree.insert("", "end", values=r)

            if f_term and all_results:
                lbl_status.config(text=f"Filter aktiv: Zeige {len(filtered)} von {len(all_results)} Treffern.")
            elif all_results:
                lbl_status.config(text=f"{len(all_results)} Treffer geladen.")

        def do_search():
            for i in tree.get_children(): tree.delete(i)
            term = ent_search.get()
            if not term: return

            lbl_status.config(text="Suche läuft im RAM...")
            ent_filter.delete(0, tk.END)  # Filter beim neuen Suchen zurücksetzen
            top.update()

            start_time = time.time()

            def search_thread():
                results = []
                for rel_path, content in self.ram_cache:
                    if len(results) >= 10000: break  # Limit auf 10.000 erhöht

                    if term in content:
                        lines = content.splitlines()
                        for line_no, line in enumerate(lines, 1):
                            if term in line:
                                results.append((rel_path, line_no, line.strip()))
                                if len(results) >= 10000: break

                self.app.after(0, lambda: finish_search(results))

            def finish_search(results):
                nonlocal all_results
                all_results = results
                elapsed = time.time() - start_time

                # Lädt die Ergebnisse in den Baum und rendert sie
                apply_filter()

                msg = f"{len(results)} Treffer in {elapsed:.3f} Sekunden."
                if len(results) >= 10000: msg += " (UI-Limit von 10.000 erreicht!)"
                lbl_status.config(text=msg)

            threading.Thread(target=search_thread, daemon=True).start()

        # Buttons rechts von den Eingabefeldern
        btn_frame = ttk.Frame(f_top)
        btn_frame.grid(row=0, column=2, rowspan=2, padx=10, sticky="ns")

        ttk.Button(btn_frame, text="Suchen", command=do_search).pack(side="top", fill="x", pady=2)
        ttk.Button(btn_frame, text="🗑 Leeren", command=clear_search).pack(side="top", fill="x", pady=2)

        # Bindings
        ent_search.bind("<Return>", lambda e: do_search())
        ent_filter.bind("<KeyRelease>", apply_filter)  # Live-Filter triggert beim Tippen

        tree.bind("<Double-1>", lambda e: self.load_method(tree.item(tree.selection()[0], "values")[0],
                                                           target_line=int(tree.item(tree.selection()[0], "values")[1]),
                                                           add_as_root=True) if tree.selection() else None)

        # --- NEU: STRG+A (Alles auswählen) und STRG+C (Kopieren) ---
        def select_all_search_results(event):
            tree.selection_set(tree.get_children())
            return "break"  # Verhindert, dass Tkinter andere Standard-Aktionen ausführt

        def copy_search_results(event):
            selected = tree.selection()
            if not selected: return "break"

            lines = []
            for item in selected:
                vals = tree.item(item, "values")
                # Baut den String im Format: "Pfad/zur/Datei.smali:123 - invoke-virtual..."
                lines.append(f"{vals[0]}:{vals[1]} - {vals[2]}")

            top.clipboard_clear()
            top.clipboard_append("\n".join(lines))
            return "break"

        tree.bind("<Control-a>", select_all_search_results)
        tree.bind("<Control-A>", select_all_search_results)  # Für Feststell-Taste
        tree.bind("<Control-c>", copy_search_results)
        tree.bind("<Control-C>", copy_search_results)
    def _update_incoming_ui(self, results):
        for i in self.tree_incoming.get_children(): self.tree_incoming.delete(i)
        if not results:
            self.tree_incoming.insert("", "end", values=("Keine Aufrufer gefunden.", ""))
        else:
            current_node_id = f"{self.current_smali_file}|{self.current_method_name}"
            for r in results:
                caller_path, caller_sig, line = r
                disp_method = caller_sig.split('(')[0]
                tags = ("system_api", caller_sig) if is_system_api("L" + caller_path) else (caller_sig,)
                self.tree_incoming.insert("", "end", values=(caller_path.replace("\\", "/"), disp_method), tags=tags)

                self.app.cg.add_edge(caller_path, caller_sig, self.current_smali_file, self.current_method_name)
                self.app.cg.make_root(f"{caller_path}|{caller_sig}")

            self.app.cg.remove_root(current_node_id)
            self.refresh_callgraph_ui()

    def clear_callgraph(self):
        self.app.cg.clear()
        self.refresh_callgraph_ui()

    def save_callgraph(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path: self.app.cg.save(path)

    def load_callgraph(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path and self.app.cg.load(path): self.refresh_callgraph_ui()

    def refresh_callgraph_ui(self):
        for i in self.tree_callstack.get_children(): self.tree_callstack.delete(i)
        for root_id in self.app.cg.roots:
            self._insert_cg_node("", root_id)

    def _insert_cg_node(self, parent_item, node_id):
        node = self.app.cg.get_node(node_id)
        if not node: return None

        disp_text = node.signature.split('(')[0]
        tags = ["system_api"] if is_system_api("L" + node.filepath) else []
        tags.append(node_id)

        item = self.tree_callstack.insert(parent_item, "end", text=disp_text, values=(os.path.basename(node.filepath),),
                                          tags=tags)
        if node.callees: self.tree_callstack.insert(item, "end", text="*dummy*")
        return item

    def on_cg_node_expand(self, event):
        item = self.tree_callstack.focus()
        children = self.tree_callstack.get_children(item)

        if len(children) == 1 and self.tree_callstack.item(children[0], "text") == "*dummy*":
            self.tree_callstack.delete(children[0])
            tags = self.tree_callstack.item(item, "tags")
            node_id = tags[1] if "system_api" in tags else tags[0]

            node = self.app.cg.get_node(node_id)
            if node:
                for callee_id in node.callees:
                    self._insert_cg_node(item, callee_id)

    def on_outline_double_click(self, event):
        sel = self.tree_outline.selection()
        if not sel: return
        tags = self.tree_outline.item(sel[0], "tags")
        sig = tags[1] if "system_api" in tags else tags[0]
        self.load_method(self.current_smali_file, method_signature=sig, add_as_root=True)

    def on_callgraph_double_click(self, event):
        sel = self.tree_callstack.selection()
        if not sel: return
        tags = self.tree_callstack.item(sel[0], "tags")
        node_id = tags[1] if "system_api" in tags else tags[0]

        if "system_api" in tags:
            return self.app.log(f"[!] {node_id.split('|')[0]} ist eine System-API.")

        if "|" in node_id:
            filepath, sig = node_id.split("|", 1)
            self.app.active_cg_node = node_id
            self.load_method(filepath, method_signature=sig, add_as_root=False)

    def on_outgoing_double_click(self, event):
        sel = self.tree_outgoing.selection()
        if not sel: return
        tags = self.tree_outgoing.item(sel[0], "tags")
        target = self.tree_outgoing.item(sel[0], "values")[0]
        if "system_api" in tags: return self.app.log(f"[!] System Aufruf: {target}")

        if ";->" in target:
            cls_part, meth_part = target.split(";->")
            found_path = self.resolve_smali_path(cls_part[1:] + ".smali")
            if found_path: self.load_method(found_path, method_signature=meth_part, add_as_root=True)

    def on_incoming_double_click(self, event):
        sel = self.tree_incoming.selection()
        if not sel: return
        caller_file = self.tree_incoming.item(sel[0], "values")[0]
        tags = self.tree_incoming.item(sel[0], "tags")
        if "Keine Aufrufer" in caller_file or "Suche" in caller_file: return
        if "system_api" in tags: return self.app.log("[!] Aufruf durch Android-System.")

        caller_method = tags[1] if "system_api" in tags else tags[0]
        if caller_method: self.load_method(caller_file, method_signature=caller_method, add_as_root=True)

    def copy_smali_to_edit(self):
        self.txt_smali_edit.delete("1.0", tk.END)
        self.txt_smali_edit.insert("1.0", self.txt_smali_orig.get("1.0", tk.END).strip())

    def add_smali_patch(self):
        f = self.current_smali_file
        orig = self.txt_smali_orig.get("1.0", tk.END).strip()
        edit = self.txt_smali_edit.get("1.0", tk.END).strip()

        if not f or not orig or not edit: return messagebox.showwarning("Fehlt", "Original oder Edit leer!")

        for p in self.smali_patches:
            if p["file"] == f and p["orig"] == orig:
                if not messagebox.askyesno("Patch existiert", "Überschreiben?"): return
                self.smali_patches.remove(p)
                break

        self.smali_patches.append({"type": "smali", "file": f, "orig": orig, "edit": edit})
        self.refresh_smali_tree()
        self.txt_smali_edit.delete("1.0", tk.END)
        self.app.log(f"[+] Smali Patch für {f} gespeichert.")

    def remove_smali_patch(self):
        sel = self.smali_tree.selection()
        if not sel: return
        del self.smali_patches[self.smali_tree.index(sel[0])]
        self.refresh_smali_tree()

    # --- DIESE METHODE HAT GEFEHLT ---
    def refresh_smali_tree(self):
        for i in self.smali_tree.get_children():
            self.smali_tree.delete(i)

        for p in self.smali_patches:
            # Vorschau auf 80 Zeichen kürzen und Zeilenumbrüche entfernen
            snippet = p["edit"][:80].replace("\n", " ") + "..."
            self.smali_tree.insert("", "end", values=(p["file"], snippet))


def open_global_search(self):
    if self.app.check_lock(): return
    smali_dir = self.get_smali_dir()
    if not os.path.exists(smali_dir): return messagebox.showwarning("Fehler", "Bitte entpacke die App zuerst!")

    top = tk.Toplevel(self)
    top.title("🔍 Globale Suche (Floating)")
    top.geometry("850x450")
    top.attributes("-topmost", True)

    f_top = ttk.Frame(top)
    f_top.pack(fill="x", padx=10, pady=10)
    ttk.Label(f_top, text="Suchbegriff:").pack(side="left")
    ent_search = ttk.Entry(f_top, width=50)
    ent_search.pack(side="left", padx=5)

    lbl_status = ttk.Label(top, text="")
    lbl_status.pack(pady=2)

    tree = ttk.Treeview(top, columns=("File", "Line", "Snippet"), show="headings")
    tree.heading("File", text="Datei");
    tree.heading("Line", text="Zeile");
    tree.heading("Snippet", text="Code-Ausschnitt")
    tree.column("Line", width=60)
    tree.pack(fill="both", expand=True, padx=10, pady=5)

    self.cancel_search_flag = False

    def cancel_search():
        self.cancel_search_flag = True
        lbl_status.config(text="Breche ab...")

    def clear_search():
        for i in tree.get_children(): tree.delete(i)
        ent_search.delete(0, tk.END)
        lbl_status.config(text="Suche geleert.")

    def do_search():
        for i in tree.get_children(): tree.delete(i)
        term = ent_search.get()
        if not term: return

        lbl_status.config(text="Suche läuft... Bitte warten.")
        self.cancel_search_flag = False
        top.update()

        def search_thread():
            results = []
            for root, dirs, files in os.walk(smali_dir):
                if self.cancel_search_flag: break
                for file in files:
                    if self.cancel_search_flag: break
                    if file.endswith(".smali") or file.endswith(".xml"):
                        filepath = os.path.join(root, file)
                        rel_path = os.path.relpath(filepath, smali_dir)
                        try:
                            with open(filepath, "r", encoding="utf-8") as f:
                                content = f.read()  # Superschneller C-Level Substring-Match
                                if term in content:
                                    lines = content.split('\n')
                                    for line_no, line in enumerate(lines, 1):
                                        if term in line:
                                            results.append((rel_path, line_no, line.strip()))
                        except:
                            pass
            self.app.after(0, lambda: finish_search(results))

        def finish_search(results):
            if self.cancel_search_flag:
                lbl_status.config(text="Suche abgebrochen.")
                return
            for r in results: tree.insert("", "end", values=r)
            lbl_status.config(text=f"{len(results)} Treffer gefunden.")

    ttk.Button(f_top, text="Suchen", command=do_search).pack(side="left")
    ttk.Button(f_top, text="❌ Abbrechen", command=cancel_search).pack(side="left", padx=5)
    ttk.Button(f_top, text="🗑 Leeren", command=clear_search).pack(side="left", padx=5)

    ent_search.bind("<Return>", lambda e: do_search())
    tree.bind("<Double-1>", lambda e: self.load_method(tree.item(tree.selection()[0], "values")[0],
                                                       target_line=int(tree.item(tree.selection()[0], "values")[1]),
                                                       add_as_root=True) if tree.selection() else None)


# ==========================================
# WORKSPACE TAB
# ==========================================
class WorkspaceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.patch_rows = []
        self.create_widgets()

    def create_widgets(self):
        # --- 1. OBERER BEREICH (Meta-Daten) ---
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

        # --- 2. UNTERER BEREICH (Zuerst packen, damit er garantiert sichtbar bleibt!) ---

        # Konsole ganz unten (expand=True wurde hier entfernt, damit sie den Platz nicht frisst)
        self.console = tk.Text(self, height=6, bg="black", fg="lightgreen")
        self.console.pack(side="bottom", fill="x", padx=10, pady=5)

        # Resultat-Frame darüber
        r_frame = ttk.LabelFrame(self, text="4. Resultat")
        r_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        self.combo_res = ttk.Combobox(r_frame, values=["Success", "Crash", "No Internet", "Logic Error"],
                                      state="readonly")
        self.combo_res.current(0)
        self.combo_res.grid(row=0, column=1, padx=5, pady=2)

        self.txt_obs = tk.Text(r_frame, height=2, width=60)
        self.txt_obs.grid(row=1, column=1, padx=5, pady=2)

        ttk.Button(r_frame, text="Save Result", command=self.save_result).grid(row=2, column=1, sticky="e", padx=5,
                                                                               pady=5)

        # Pipeline-Frame (mit Build & Flash) darüber
        a_frame = ttk.LabelFrame(self, text="3. Pipelines Ausführen")
        a_frame.pack(side="bottom", fill="x", padx=10, pady=5)

        ttk.Label(a_frame, text="Pipeline:").grid(row=0, column=0, padx=5, pady=5)
        self.combo_pipe = ttk.Combobox(a_frame, values=["BUILD_FLUTTER", "BUILD_NATIVE"], state="readonly", width=15)
        self.combo_pipe.current(0)
        self.combo_pipe.grid(row=0, column=1, padx=5, pady=5)

        ttk.Button(a_frame, text="▶ Build", command=lambda: self.run_pipeline(self.combo_pipe.get())).grid(row=0,
                                                                                                           column=2,
                                                                                                           padx=5,
                                                                                                           pady=5)
        ttk.Button(a_frame, text="📱 Flash", command=lambda: self.run_pipeline("FLASH")).grid(row=0, column=3, padx=5,
                                                                                             pady=5)

        ttk.Separator(a_frame, orient="vertical").grid(row=0, column=4, sticky="ns", padx=5, pady=5)
        self.btn_trace_start = ttk.Button(a_frame, text="Start Trace", command=self.start_trace)
        self.btn_trace_start.grid(row=0, column=5, padx=5, pady=5)
        self.btn_trace_stop = ttk.Button(a_frame, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_trace_stop.grid(row=0, column=6, padx=5, pady=5)

        # --- 3. MITTLERER BEREICH (Füllt den restlichen Platz auf) ---
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
                     "patch": p["patch"].get(), "file": "libflutter.so"} for p in self.patch_rows if
                    p["ram"].get().strip()]
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
                last_row["ram"].delete(0, tk.END);
                last_row["ram"].insert(0, pt.get("ram", ""))
                last_row["base"].delete(0, tk.END);
                last_row["base"].insert(0, pt.get("base", "00100000"))
                last_row["orig"].delete(0, tk.END);
                last_row["orig"].insert(0, pt.get("orig", ""))
                last_row["patch"].delete(0, tk.END);
                last_row["patch"].insert(0, pt.get("patch", ""))

        self.tab_smali.refresh_smali_tree()
        self.ent_version.delete(0, tk.END)
        self.ent_version.insert(0, record.get("app_version", ""))
        self.app.notebook.select(self.app.tab_workspace)

    def run_pipeline(self, name):
        def task():
            if name.startswith("BUILD"):
                self.app.current_archive_path = os.path.join(self.app.cfg.paths["ARCHIVE_DIR"],
                                                             f"{self.app.current_id}_{self.ent_name.get().replace(' ', '_')}")
                os.makedirs(self.app.current_archive_path, exist_ok=True)

                # Leere dynamisches Zielverzeichnis
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        try:
                            os.remove(os.path.join(dest_dir, f))
                        except:
                            pass

            # Startet die Engine (dies blockiert nun nur den Hintergrund-Thread)
            success = self.app.engine.run_pipeline(name)

            if name.startswith("BUILD") and success:
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        if f.endswith("-aligned-debugSigned.apk"):
                            shutil.copy(os.path.join(dest_dir, f), self.app.current_archive_path)

        # Startet den Vorgang im Hintergrund
        threading.Thread(target=task, daemon=True).start()


    def start_trace(self):
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