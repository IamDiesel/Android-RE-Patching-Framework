import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import sqlite3
import json
import os
import time
import sys
import threading
import re
import subprocess


# ==========================================
# 1. DATA EXTRACTION ENGINE (Business Logic)
# ==========================================
class DataExtractor:
    @staticmethod
    def extract(rule, method, url, req_h, req_b, res_h, res_b):
        if rule.get("filter_method") and rule["filter_method"] != "ALL":
            if rule["filter_method"] != method: return ""
        if rule.get("filter_url") and rule["filter_url"] not in url: return ""

        src = rule.get("source", "res_body")
        data_str = ""
        if src == "req_headers":
            data_str = req_h
        elif src == "req_body":
            data_str = req_b
        elif src == "res_headers":
            data_str = res_h
        elif src == "res_body":
            data_str = res_b

        if not data_str: return ""

        ext_type = rule.get("ext_type", "json")
        try:
            if ext_type == "json":
                obj = json.loads(data_str)
                path = rule.get("param1", "").split(".")
                for key in path:
                    if key: obj = obj[key]
                return str(obj)
            elif ext_type == "regex":
                pattern = rule.get("param1", "")
                match = re.search(pattern, data_str)
                if match: return match.group(1) if len(match.groups()) > 0 else match.group(0)
            elif ext_type == "offset":
                offset, length, dtype = int(rule.get("param1", "0")), int(rule.get("param2", "1")), rule.get("param3",
                                                                                                             "string")
                chunk = data_str.encode('utf-8', errors='ignore')[offset:offset + length]
                if dtype == "hex":
                    return chunk.hex()
                elif dtype == "int":
                    return str(int.from_bytes(chunk, byteorder='big'))
                else:
                    return chunk.decode('utf-8', errors='ignore')
        except Exception:
            return "<err>"
        return ""


# ==========================================
# 2. COLUMN CONFIG MANAGER (Custom Columns)
# ==========================================
class ColumnConfigManager:
    def __init__(self, base_path):
        self.config_file = os.path.join(os.path.dirname(base_path), "custom_columns.json")
        self.columns = self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return []

    def save(self):
        with open(self.config_file, "w", encoding="utf-8") as f: json.dump(self.columns, f, indent=4)

    def add_column(self, col_dict):
        self.columns.append(col_dict)
        self.save()

    def update_column(self, index, col_dict):
        if 0 <= index < len(self.columns):
            self.columns[index] = col_dict
            self.save()

    def delete_column(self, index):
        if 0 <= index < len(self.columns):
            del self.columns[index]
            self.save()


# ==========================================
# 3. COLUMN DISPLAY MANAGER (Visibility & Order)
# ==========================================
class ColumnDisplayManager:
    def __init__(self, base_path):
        self.config_file = os.path.join(os.path.dirname(base_path), "column_display.json")
        self.data = self.load()

    def load(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {"active": ["ID", "Time", "Method", "URL", "Status", "Comment"], "hidden": []}

    def save(self, active, hidden):
        self.data = {"active": active, "hidden": hidden}
        with open(self.config_file, "w", encoding="utf-8") as f: json.dump(self.data, f, indent=4)

    def get_lists(self, all_available):
        active = [c for c in self.data.get("active", []) if c in all_available]
        hidden = [c for c in self.data.get("hidden", []) if c in all_available]
        known = set(active + hidden)
        new_cols = [c for c in all_available if c not in known]
        active.extend(new_cols)
        return active, hidden


# ==========================================
# 4. COLUMN DISPLAY DIALOG (UI Component)
# ==========================================
class ColumnDisplayDialog(tk.Toplevel):
    def __init__(self, parent, display_mgr, all_cols, on_update):
        super().__init__(parent)
        self.title("👁️ Ansicht & Spaltenreihenfolge")
        self.geometry("550x400")
        self.display_mgr = display_mgr
        self.on_update = on_update

        self.active_cols, self.hidden_cols = self.display_mgr.get_lists(all_cols)
        self.create_widgets()

    def create_widgets(self):
        # FIX: Die untere Button-Leiste ZUERST packen, damit sie ihren Platz fest reserviert
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=10)
        ttk.Button(bottom, text="💾 Speichern", command=self.save_and_close).pack(side="right", padx=10)
        ttk.Button(bottom, text="❌ Abbrechen", command=self.destroy).pack(side="right")

        # Haupt-Container für den restlichen Platz darüber
        main_frame = ttk.Frame(self)
        main_frame.pack(side="top", fill="both", expand=True)

        frame_hidden = ttk.LabelFrame(main_frame, text="Ausgeblendete Spalten")
        frame_hidden.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        frame_mid = ttk.Frame(main_frame)
        frame_mid.pack(side="left", fill="y", padx=5, pady=50)

        frame_active = ttk.LabelFrame(main_frame, text="Aktive Spalten (Reihenfolge)")
        frame_active.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        frame_right = ttk.Frame(main_frame)
        frame_right.pack(side="left", fill="y", padx=5, pady=50)

        self.lst_hidden = tk.Listbox(frame_hidden, selectmode=tk.EXTENDED)
        self.lst_hidden.pack(fill="both", expand=True, padx=5, pady=5)
        for c in self.hidden_cols: self.lst_hidden.insert(tk.END, c)

        self.lst_active = tk.Listbox(frame_active, selectmode=tk.EXTENDED)
        self.lst_active.pack(fill="both", expand=True, padx=5, pady=5)
        for c in self.active_cols: self.lst_active.insert(tk.END, c)

        ttk.Button(frame_mid, text="Hinzufügen ➔", command=self.add_col).pack(pady=5)
        ttk.Button(frame_mid, text="⬅ Entfernen", command=self.remove_col).pack(pady=5)
        ttk.Button(frame_right, text="⬆ Hoch", command=self.move_up).pack(pady=5)
        ttk.Button(frame_right, text="⬇ Runter", command=self.move_down).pack(pady=5)

    def move_up(self):
        sel = self.lst_active.curselection()
        for i in sel:
            if i > 0:
                val = self.lst_active.get(i)
                self.lst_active.delete(i)
                self.lst_active.insert(i - 1, val)
                self.lst_active.selection_set(i - 1)

    def move_down(self):
        sel = self.lst_active.curselection()
        for i in reversed(sel):
            if i < self.lst_active.size() - 1:
                val = self.lst_active.get(i)
                self.lst_active.delete(i)
                self.lst_active.insert(i + 1, val)
                self.lst_active.selection_set(i + 1)

    def add_col(self):
        sel = self.lst_hidden.curselection()
        for i in reversed(sel):
            val = self.lst_hidden.get(i)
            self.lst_hidden.delete(i)
            self.lst_active.insert(tk.END, val)

    def remove_col(self):
        sel = self.lst_active.curselection()
        for i in reversed(sel):
            val = self.lst_active.get(i)
            self.lst_active.delete(i)
            self.lst_hidden.insert(tk.END, val)

    def save_and_close(self):
        active = list(self.lst_active.get(0, tk.END))
        hidden = list(self.lst_hidden.get(0, tk.END))
        self.display_mgr.save(active, hidden)
        self.on_update()
        self.destroy()


# ==========================================
# 5. CUSTOM COLUMN MANAGER DIALOG
# ==========================================
class CustomColumnDialog(tk.Toplevel):
    def __init__(self, parent, col_mgr, on_update_callback):
        super().__init__(parent)
        self.title("⚙️ Spalten-Logik Verwalten")
        self.geometry("700x550")  # Leicht verbreitert für die neuen Buttons
        self.col_mgr = col_mgr
        self.on_update = on_update_callback
        self.edit_index = None
        self.create_widgets()

    def create_widgets(self):
        # 1. Editor unten ZUERST verankern
        self.add_frame = ttk.LabelFrame(self, text="Spalte hinzufügen / bearbeiten")
        self.add_frame.pack(side="bottom", fill="x", padx=10, pady=10)

        # 2. Container für Tabelle und Buttons
        list_frame = ttk.LabelFrame(self, text="Aktive Custom-Spalten")
        list_frame.pack(side="top", fill="both", expand=True, padx=10, pady=5)

        # 3. FIX: Button-Frame ZUERST unten ins list_frame packen!
        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        ttk.Button(btn_frame, text="✏️ Bearbeiten", command=self.load_for_edit).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🗑 Löschen", command=self.delete_col).pack(side="left", padx=5)

        # Import / Export übersichtlich auf der rechten Seite anordnen
        ttk.Button(btn_frame, text="📂 Laden...", command=self.import_settings).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="💾 Speichern unter...", command=self.export_settings).pack(side="right", padx=5)

        # 4. FIX: Die Tabelle als LETZTES ins list_frame packen, damit sie nur den REST ausfüllt
        self.tree = ttk.Treeview(list_frame, columns=("Name", "Ext Type", "Quelle"), show="headings", height=5)
        self.tree.heading("Name", text="Spaltenname")
        self.tree.heading("Ext Type", text="Typ")
        self.tree.heading("Quelle", text="Datenquelle")
        self.tree.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        # --- Editor Setup (wie vorher) ---
        ttk.Label(self.add_frame, text="Name:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(self.add_frame, width=20)
        self.ent_name.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Filter Method:").grid(row=0, column=2, sticky="w", padx=5, pady=2)
        self.cb_meth = ttk.Combobox(self.add_frame, values=["ALL", "GET", "POST", "PUT", "DELETE"], state="readonly",
                                    width=10)
        self.cb_meth.current(0)
        self.cb_meth.grid(row=0, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Filter URL:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_url = ttk.Entry(self.add_frame, width=20)
        self.ent_url.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Quelle:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        self.cb_src = ttk.Combobox(self.add_frame, values=["req_headers", "req_body", "res_headers", "res_body"],
                                   state="readonly", width=15)
        self.cb_src.current(3)
        self.cb_src.grid(row=1, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Extraktor:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.cb_ext = ttk.Combobox(self.add_frame, values=["json", "regex", "offset"], state="readonly", width=15)
        self.cb_ext.current(0)
        self.cb_ext.grid(row=2, column=1, sticky="w", padx=5, pady=2)
        self.cb_ext.bind("<<ComboboxSelected>>", self.on_ext_change)

        self.param_frame = ttk.Frame(self.add_frame)
        self.param_frame.grid(row=3, column=0, columnspan=4, sticky="w", padx=5, pady=5)
        self.param_entries = {}
        self.on_ext_change()

        self.btn_submit = ttk.Button(self.add_frame, text="➕ Hinzufügen", command=self.submit_col)
        self.btn_submit.grid(row=4, column=2, pady=10, sticky="e")
        self.btn_cancel = ttk.Button(self.add_frame, text="❌ Abbrechen", command=self.cancel_edit)
        self.btn_cancel.grid(row=4, column=3, pady=10, padx=5, sticky="w")
        self.btn_cancel.grid_remove()

        self.refresh_list()

    def on_ext_change(self, event=None):
        for widget in self.param_frame.winfo_children(): widget.destroy()
        self.param_entries.clear()
        ext = self.cb_ext.get()
        if ext == "json":
            ttk.Label(self.param_frame, text="JSON Pfad (z.B. user.id):").pack(side="left")
            e = ttk.Entry(self.param_frame, width=30)
            e.pack(side="left", padx=5)
            self.param_entries["param1"] = e
        elif ext == "regex":
            ttk.Label(self.param_frame, text="Regex (Capture Group 1):").pack(side="left")
            e = ttk.Entry(self.param_frame, width=30)
            e.pack(side="left", padx=5)
            self.param_entries["param1"] = e
        elif ext == "offset":
            ttk.Label(self.param_frame, text="Offset:").pack(side="left")
            e1 = ttk.Entry(self.param_frame, width=8)
            e1.pack(side="left", padx=2)
            ttk.Label(self.param_frame, text="Länge:").pack(side="left")
            e2 = ttk.Entry(self.param_frame, width=8)
            e2.pack(side="left", padx=2)
            ttk.Label(self.param_frame, text="Typ:").pack(side="left")
            cb = ttk.Combobox(self.param_frame, values=["string", "hex", "int"], state="readonly", width=8)
            cb.current(0)
            cb.pack(side="left", padx=2)
            self.param_entries.update({"param1": e1, "param2": e2, "param3": cb})

    def load_for_edit(self):
        sel = self.tree.selection()
        if not sel: return
        self.edit_index = self.tree.index(sel[0])
        col_def = self.col_mgr.columns[self.edit_index]

        self.ent_name.delete(0, tk.END);
        self.ent_name.insert(0, col_def["name"])
        self.cb_meth.set(col_def.get("filter_method", "ALL"))
        self.ent_url.delete(0, tk.END);
        self.ent_url.insert(0, col_def.get("filter_url", ""))
        self.cb_src.set(col_def.get("source", "res_body"))
        self.cb_ext.set(col_def.get("ext_type", "json"))

        self.on_ext_change()
        if col_def.get("ext_type") in ["json", "regex"]:
            self.param_entries["param1"].delete(0, tk.END);
            self.param_entries["param1"].insert(0, col_def.get("param1", ""))
        elif col_def.get("ext_type") == "offset":
            self.param_entries["param1"].delete(0, tk.END);
            self.param_entries["param1"].insert(0, col_def.get("param1", ""))
            self.param_entries["param2"].delete(0, tk.END);
            self.param_entries["param2"].insert(0, col_def.get("param2", ""))
            self.param_entries["param3"].set(col_def.get("param3", "string"))

        self.add_frame.config(text=f"Spalte bearbeiten: {col_def['name']}")
        self.btn_submit.config(text="💾 Aktualisieren")
        self.btn_cancel.grid()

    def cancel_edit(self):
        self.edit_index = None
        self.add_frame.config(text="Neue Spalte hinzufügen")
        self.btn_submit.config(text="➕ Hinzufügen")
        self.btn_cancel.grid_remove()
        self.ent_name.delete(0, tk.END);
        self.ent_url.delete(0, tk.END)
        self.cb_meth.current(0);
        self.cb_src.current(3);
        self.cb_ext.current(0)
        self.on_ext_change()

    def submit_col(self):
        name = self.ent_name.get().strip()
        if not name: return
        col_def = {
            "name": name, "filter_method": self.cb_meth.get(), "filter_url": self.ent_url.get().strip(),
            "source": self.cb_src.get(), "ext_type": self.cb_ext.get(),
            "param1": self.param_entries.get("param1").get() if "param1" in self.param_entries else "",
            "param2": self.param_entries.get("param2").get() if "param2" in self.param_entries else "",
            "param3": self.param_entries.get("param3").get() if "param3" in self.param_entries else ""
        }
        if self.edit_index is not None:
            self.col_mgr.update_column(self.edit_index, col_def)
        else:
            self.col_mgr.add_column(col_def)
        self.cancel_edit()
        self.refresh_list()
        self.on_update()

    def delete_col(self):
        sel = self.tree.selection()
        if not sel: return
        idx = self.tree.index(sel[0])
        self.col_mgr.delete_column(idx)
        if self.edit_index == idx: self.cancel_edit()
        self.refresh_list()
        self.on_update()

    def refresh_list(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        for c in self.col_mgr.columns: self.tree.insert("", "end", values=(c["name"], c["ext_type"], c["source"]))

    def export_settings(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")],
                                            title="Spalten-Profil speichern")
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.col_mgr.columns, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Export", "Spalten-Profil erfolgreich exportiert!")
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte nicht speichern:\n{e}")

    def import_settings(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Spalten-Profil laden")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                self.col_mgr.columns = data
                self.col_mgr.save()
                self.refresh_list()
                self.on_update()
                messagebox.showinfo("Import", "Spalten-Profil erfolgreich geladen!")
            else:
                messagebox.showerror("Fehler", "Ungültiges Dateiformat.")
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte nicht laden:\n{e}")

# ==========================================
# 6. MAIN ORCHESTRATOR (GUI Tab)
# ==========================================
class APIInspectorTab(ttk.Frame):
    def __init__(self, parent, config_mgr, logger_func):
        super().__init__(parent)
        self.cfg = config_mgr
        self.log = logger_func
        self.proxy_process = None
        self.known_ids = set()
        self.last_filter_text = ""  # AUTOSCROLL-FIX: Speichert den letzten Filterstatus

        self.col_mgr = ColumnConfigManager(self.cfg.paths["API_RULES"])
        self.display_mgr = ColumnDisplayManager(self.cfg.paths["API_RULES"])

        self.init_db()
        self.create_widgets()

        self.after(1500, self.poll_db)
        self.after(1000, self.check_proxy_status)

    def init_db(self):
        db_path = self.cfg.paths["API_DB"]
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS requests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, method TEXT, url TEXT, status INTEGER,
                      req_headers TEXT, req_body TEXT, res_headers TEXT, res_body TEXT, comment TEXT)''')
        conn.commit()
        conn.close()
        if not os.path.exists(self.cfg.paths["API_RULES"]):
            with open(self.cfg.paths["API_RULES"], "w", encoding="utf-8") as f: json.dump([], f)

    def create_widgets(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=5, pady=5)

        ttk.Button(toolbar, text="▶ Start Proxy", command=self.start_proxy).pack(side="left", padx=2)
        ttk.Button(toolbar, text="⏹ Stop Proxy", command=self.stop_proxy).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🗑 Clear Log", command=self.clear_log).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        ttk.Button(toolbar, text="💾 Export Selected", command=self.export_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📂 Import", command=self.import_packets).pack(side="left", padx=2)
        ttk.Button(toolbar, text="⚙️ Spalten-Logik", command=self.open_col_manager).pack(side="left", padx=2)
        ttk.Button(toolbar, text="👁️ Ansicht", command=self.open_display_manager).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        ttk.Button(toolbar, text="📱 Push Cert", command=self.push_cert).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🔌 Route USB", command=self.route_usb).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📡 Route WLAN", command=self.route_wlan).pack(side="left", padx=2)
        ttk.Button(toolbar, text="❌ Reset Route", command=self.reset_route).pack(side="left", padx=2)

        self.lbl_proxy_status = ttk.Label(toolbar, text="Proxy: 🔴 Offline", font=("Segoe UI", 10, "bold"))
        self.lbl_proxy_status.pack(side="right", padx=5)
        self.lbl_tunnel_status = ttk.Label(toolbar, text="Tunnel: 🔴 Inaktiv", font=("Segoe UI", 10, "bold"))
        self.lbl_tunnel_status.pack(side="right", padx=5)

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=5, pady=2)
        ttk.Label(filter_bar, text="Filter (URL/Method):").pack(side="left")
        self.ent_filter = ttk.Entry(filter_bar, width=40)
        self.ent_filter.pack(side="left", padx=5)
        self.ent_filter.bind("<KeyRelease>", lambda e: self.refresh_list(force_rebuild=True))

        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned.pack(fill="both", expand=True, padx=5, pady=5)

        list_frame = ttk.Frame(self.paned)
        self.paned.add(list_frame, weight=1)

        self.tree = ttk.Treeview(list_frame, show="headings", height=8, selectmode="extended")
        self.build_tree_columns()

        v_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        v_scroll.pack(side="right", fill="y")
        h_scroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        h_scroll.pack(side="bottom", fill="x")
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.tree.pack(fill="both", expand=True, side="left")

        self.tree.bind("<<TreeviewSelect>>", self.on_request_select)
        self.tree.bind("<Control-c>", self.copy_selected_packets)
        self.tree.bind("<Control-a>", self.select_all_packets)

        bottom_frame = ttk.Frame(self.paned)
        self.paned.add(bottom_frame, weight=2)
        self.req_notebook = ttk.Notebook(bottom_frame)
        self.req_notebook.pack(fill="both", expand=True)

        self.tab_details = ttk.Frame(self.req_notebook)
        self.req_notebook.add(self.tab_details, text="Request Details & Editor")
        det_paned = ttk.PanedWindow(self.tab_details, orient=tk.HORIZONTAL)
        det_paned.pack(fill="both", expand=True)

        req_frame = ttk.LabelFrame(det_paned, text="Request (Headers & Body)")
        det_paned.add(req_frame, weight=1)
        self.txt_req = tk.Text(req_frame, wrap="word", font=("Courier", 9))
        self.txt_req.pack(fill="both", expand=True)

        res_frame = ttk.LabelFrame(det_paned, text="Response (Headers & Body)")
        det_paned.add(res_frame, weight=1)
        self.txt_res = tk.Text(res_frame, wrap="word", font=("Courier", 9))
        self.txt_res.pack(fill="both", expand=True)

        comment_frame = ttk.Frame(self.tab_details)
        comment_frame.pack(fill="x", pady=5)
        ttk.Label(comment_frame, text="Kommentar:").pack(side="left")
        self.ent_comment = ttk.Entry(comment_frame)
        self.ent_comment.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(comment_frame, text="💾 Änderungen speichern", command=self.save_packet_edits).pack(side="right",
                                                                                                      padx=5)

        self.tab_manipulate = ttk.Frame(self.req_notebook)
        self.req_notebook.add(self.tab_manipulate, text="Intercept Regeln")
        self.setup_manipulation_tab()

    def get_all_available_columns(self):
        base_cols = ["ID", "Time", "Method", "URL", "Status", "Comment"]
        custom_cols = [c["name"] for c in self.col_mgr.columns]
        return base_cols + custom_cols

    def build_tree_columns(self):
        all_cols = self.get_all_available_columns()
        self.tree["columns"] = all_cols

        default_widths = {"ID": 50, "Time": 90, "Method": 70, "URL": 400, "Status": 60, "Comment": 150}
        for c in all_cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=default_widths.get(c, 120), minwidth=50)

        active, _ = self.display_mgr.get_lists(all_cols)
        self.tree["displaycolumns"] = active

    def open_col_manager(self):
        def on_close_update():
            self.build_tree_columns()
            self.refresh_list(force_rebuild=True)

        CustomColumnDialog(self, self.col_mgr, on_close_update)

    def open_display_manager(self):
        def on_close_update():
            active, _ = self.display_mgr.get_lists(self.get_all_available_columns())
            self.tree["displaycolumns"] = active

        ColumnDisplayDialog(self, self.display_mgr, self.get_all_available_columns(), on_close_update)

    def select_all_packets(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def setup_manipulation_tab(self):
        add_frame = ttk.LabelFrame(self.tab_manipulate, text="Neue Regel")
        add_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(add_frame, text="URL Match:").grid(row=0, column=0, padx=5, pady=2)
        self.ent_rule_url = ttk.Entry(add_frame, width=30)
        self.ent_rule_url.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(add_frame, text="Aktion:").grid(row=0, column=2, padx=5, pady=2)
        self.cb_rule_action = ttk.Combobox(add_frame, values=["replace_req_body", "replace_res_body"], state="readonly")
        self.cb_rule_action.current(1)
        self.cb_rule_action.grid(row=0, column=3, padx=5, pady=2)

        ttk.Label(add_frame, text="Payload:").grid(row=1, column=0, padx=5, pady=2, sticky="nw")
        self.txt_rule_payload = tk.Text(add_frame, height=4, width=50)
        self.txt_rule_payload.grid(row=1, column=1, columnspan=3, padx=5, pady=2)

        ttk.Button(add_frame, text="Regel Hinzufügen", command=self.add_rule).grid(row=2, column=3, padx=5, pady=5,
                                                                                   sticky="e")

        self.rules_tree = ttk.Treeview(self.tab_manipulate, columns=("URL", "Action", "Payload"), show="headings",
                                       height=5)
        self.rules_tree.heading("URL", text="URL Match")
        self.rules_tree.heading("Action", text="Aktion")
        self.rules_tree.heading("Payload", text="Payload (Preview)")
        self.rules_tree.pack(fill="both", expand=True, padx=5, pady=5)

        ttk.Button(self.tab_manipulate, text="Ausgewählte löschen", command=self.delete_rule).pack(pady=5)
        self.refresh_rules()

    # --- Proxy ---
    def start_proxy(self):
        if self.proxy_process and self.proxy_process.poll() is None: return
        addon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mitm_addon.py")
        if not os.path.exists(addon_path): return messagebox.showerror("Fehler", f"Addon fehlt:\n{addon_path}")

        # FIX: Nutze direkt den Befehl "mitmdump" (da der Scripts-Pfad im PATH liegt)
        cmd = f"mitmdump --listen-host 0.0.0.0 -s \"{addon_path}\" --set api_db=\"{self.cfg.paths['API_DB']}\" --set rules_file=\"{self.cfg.paths['API_RULES']}\""

        try:
            self.proxy_process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                  text=True, bufsize=1)
            self.lbl_proxy_status.config(text="Proxy: 🟢 Läuft", foreground="green")
            threading.Thread(target=self._read_proxy_output, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Proxy Fehler", f"Konnte Proxy nicht starten:\n{e}")
            self.log(f"[!] Proxy Start Error: {e}")

    def _read_proxy_output(self):
        try:
            for line in iter(self.proxy_process.stdout.readline, ''):
                if line: self.after(0, self.log, f"[PROXY] {line.strip()}")
        except:
            pass

    def stop_proxy(self):
        if self.proxy_process:
            self.proxy_process.terminate()
            self.proxy_process = None
            self.lbl_proxy_status.config(text="Proxy: 🔴 Offline", foreground="black")

    def check_proxy_status(self):
        if self.proxy_process and self.proxy_process.poll() is not None:
            self.proxy_process = None
            self.lbl_proxy_status.config(text="Proxy: 🔴 Offline (Fehler)", foreground="red")
        self.after(1000, self.check_proxy_status)

    def clear_log(self):
        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        conn.execute("DELETE FROM requests")
        conn.commit()
        conn.close()
        self.known_ids.clear()
        for i in self.tree.get_children(): self.tree.delete(i)
        self.txt_req.delete("1.0", tk.END)
        self.txt_res.delete("1.0", tk.END)
        self.ent_comment.delete(0, tk.END)

    def push_cert(self):
        cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.cer")
        if not os.path.exists(cert_path): return messagebox.showwarning("Hinweis", "Zertifikat fehlt.")
        subprocess.run(f"adb push \"{cert_path}\" /storage/emulated/0/Download/", shell=True)
        messagebox.showinfo("Zertifikat", "Installiere es in Android als CA-Zertifikat.")

    def route_usb(self):
        subprocess.run("adb reverse tcp:8080 tcp:8080", shell=True)
        subprocess.run("adb shell settings put global http_proxy 127.0.0.1:8080", shell=True)
        self.lbl_tunnel_status.config(text="Tunnel: 🟡 USB")

    def route_wlan(self):
        ip = simpledialog.askstring("WLAN Proxy", "Lokale IP deines PCs:")
        if ip:
            subprocess.run(f"adb shell settings put global http_proxy {ip}:8080", shell=True)
            self.lbl_tunnel_status.config(text=f"Tunnel: 🔵 WLAN")

    def reset_route(self):
        subprocess.run("adb reverse --remove-all", shell=True)
        subprocess.run("adb shell settings put global http_proxy :0", shell=True)
        self.lbl_tunnel_status.config(text="Tunnel: 🔴 Inaktiv")

    # --- Data Polling & Mapping ---
    def process_row_to_dict(self, r):
        t_str = time.strftime('%H:%M:%S', time.localtime(r[1]))
        row_dict = {"ID": r[0], "Time": t_str, "Method": r[2], "URL": r[3], "Status": r[4], "Comment": r[9] or ""}
        for col_rule in self.col_mgr.columns:
            val = DataExtractor.extract(col_rule, method=r[2], url=r[3], req_h=r[5], req_b=r[6], res_h=r[7], res_b=r[8])
            row_dict[col_rule["name"]] = val
        return row_dict

    def poll_db(self):
        self.refresh_list(force_rebuild=False)
        self.after(1500, self.poll_db)

    def refresh_list(self, force_rebuild=False):
        if not os.path.exists(self.cfg.paths["API_DB"]): return
        try:
            conn = sqlite3.connect(self.cfg.paths["API_DB"])
            c = conn.cursor()

            filter_text = self.ent_filter.get().strip()

            # AUTOSCROLL-FIX: Prüfe ob sich der Filter WIRKLICH geändert hat
            filter_changed = filter_text != self.last_filter_text
            self.last_filter_text = filter_text

            if force_rebuild or filter_changed:
                self.known_ids.clear()
                for i in self.tree.get_children(): self.tree.delete(i)

            if filter_text:
                c.execute(
                    "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE url LIKE ? OR method LIKE ? ORDER BY id ASC",
                    (f"%{filter_text}%", f"%{filter_text}%"))
            else:
                c.execute(
                    "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests ORDER BY id ASC")

            rows = c.fetchall()
            conn.close()

            # AUTOSCROLL-FIX: 0.99 ist robuster für das "Ganz-Unten-Tracking"
            scroll_pos = self.tree.yview()
            at_bottom = scroll_pos[1] >= 0.99 if scroll_pos else True
            all_cols = self.get_all_available_columns()

            added_new = False
            for r in rows:
                rec_id = r[0]
                if rec_id not in self.known_ids:
                    row_dict = self.process_row_to_dict(r)
                    values = [row_dict.get(c, "") for c in all_cols]
                    self.known_ids.add(rec_id)
                    self.tree.insert("", "end", iid=str(rec_id), values=values)
                    added_new = True

            # Scrollt nur noch, wenn der Nutzer ganz unten war UND WIRKLICH ein neues Paket reinkam
            if added_new and at_bottom:
                children = self.tree.get_children()
                if children: self.tree.see(children[-1])

        except Exception as e:
            pass

    def on_request_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        req_id = self.tree.item(sel[0], "values")[0]

        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()
        c.execute("SELECT req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id=?", (req_id,))
        row = c.fetchone()
        conn.close()

        if row:
            self.txt_req.delete("1.0", tk.END);
            self.txt_req.insert("end", f"=== HEADERS ===\n{row[0]}\n\n=== BODY ===\n{row[1]}")
            self.txt_res.delete("1.0", tk.END);
            self.txt_res.insert("end", f"=== HEADERS ===\n{row[2]}\n\n=== BODY ===\n{row[3]}")
            self.ent_comment.delete(0, tk.END);
            self.ent_comment.insert(0, row[4] or "")

    def save_packet_edits(self):
        sel = self.tree.selection()
        if not sel: return
        item_id = sel[0]
        req_id = self.tree.item(item_id, "values")[0]
        comment = self.ent_comment.get()

        req_raw, res_raw = self.txt_req.get("1.0", tk.END), self.txt_res.get("1.0", tk.END)
        req_body = req_raw.split("=== BODY ===")[-1].strip() if "=== BODY ===" in req_raw else req_raw.strip()
        res_body = res_raw.split("=== BODY ===")[-1].strip() if "=== BODY ===" in res_raw else res_raw.strip()

        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()
        c.execute("UPDATE requests SET comment=?, req_body=?, res_body=? WHERE id=?",
                  (comment, req_body, res_body, req_id))
        conn.commit()

        c.execute(
            "SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id=?",
            (req_id,))
        updated_row = c.fetchone()
        conn.close()

        if updated_row:
            row_dict = self.process_row_to_dict(updated_row)
            all_cols = self.get_all_available_columns()
            self.tree.item(item_id, values=[row_dict.get(c, "") for c in all_cols])

    def copy_selected_packets(self, event=None):
        sel = self.tree.selection()
        if not sel: return
        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()
        lines = []
        for item in sel:
            req_id = self.tree.item(item, "values")[0]
            c.execute(
                "SELECT timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id=?",
                (req_id,))
            r = c.fetchone()
            if r:
                t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r[0]))
                lines.append(
                    f"[{t}] {r[1]} {r[2]} (Status: {r[3]})\nKommentar: {r[8]}\n--- REQ BODY ---\n{r[5]}\n--- RES BODY ---\n{r[7]}\n" + "=" * 40)
        conn.close()
        self.clipboard_clear();
        self.clipboard_append("\n\n".join(lines))
        return "break"

    # --- Export, Import & Intercept Rules ---
    def export_selected(self):
        sel = self.tree.selection()
        if not sel: return
        path = filedialog.asksaveasfilename(defaultextension=".json")
        if not path: return
        ids = [self.tree.item(i, "values")[0] for i in sel]
        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()
        c.execute(f"SELECT * FROM requests WHERE id IN ({','.join(['?'] * len(ids))})", ids)
        export_data = [{"id": r[0], "timestamp": r[1], "method": r[2], "url": r[3], "status": r[4],
                        "req_headers": r[5], "req_body": r[6], "res_headers": r[7], "res_body": r[8], "comment": r[9]}
                       for r in c.fetchall()]
        conn.close()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4)

    def import_packets(self):
        path = filedialog.askopenfilename()
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            conn = sqlite3.connect(self.cfg.paths["API_DB"])
            c = conn.cursor()
            for i in data:
                c.execute('''INSERT INTO requests (timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (i.get("timestamp", time.time()), i.get("method", ""), i.get("url", ""), i.get("status", 200),
                           i.get("req_headers", ""), i.get("req_body", ""), i.get("res_headers", ""),
                           i.get("res_body", ""), i.get("comment", "")))
            conn.commit()
            conn.close()
            self.refresh_list(force_rebuild=True)
        except Exception as e:
            messagebox.showerror("Fehler", str(e))

    def load_rules(self):
        try:
            with open(self.cfg.paths["API_RULES"], "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def save_rules(self, rules):
        with open(self.cfg.paths["API_RULES"], "w", encoding="utf-8") as f: json.dump(rules, f, indent=4)
        self.refresh_rules()

    def add_rule(self):
        r = self.load_rules()
        r.append({"url_match": self.ent_rule_url.get(), "action": self.cb_rule_action.get(),
                  "payload": self.txt_rule_payload.get("1.0", tk.END).strip()})
        self.save_rules(r)
        self.ent_rule_url.delete(0, tk.END);
        self.txt_rule_payload.delete("1.0", tk.END)

    def delete_rule(self):
        sel = self.rules_tree.selection()
        if not sel: return
        idx = self.rules_tree.index(sel[0])
        r = self.load_rules()
        del r[idx]
        self.save_rules(r)

    def refresh_rules(self):
        for i in self.rules_tree.get_children(): self.rules_tree.delete(i)
        for r in self.load_rules(): self.rules_tree.insert("", "end", values=(
        r["url_match"], r["action"], r["payload"][:30] + "..."))