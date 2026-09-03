import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
import os
import time

from core.application.event_bus import EventBus
from core.data_extractor import DataExtractor
from core.column_config_manager import ColumnConfigManager
from core.column_display_manager import ColumnDisplayManager
from ui.dialogs.column_dialogs import ColumnDisplayDialog, CustomColumnDialog

from services.api_db_service import ApiDbService
from services.proxy_service import ProxyService
from services.adb_network_service import AdbNetworkService


class APIInspectorTab(ttk.Frame):
    def __init__(self, parent, config_mgr):
        super().__init__(parent)
        self.cfg = config_mgr
        self.known_ids = set()
        self.last_filter_text = ""

        # Init Services
        self.db_service = ApiDbService(self.cfg.paths["API_DB"])
        self.proxy_service = ProxyService(self.cfg.paths["API_DB"], self.cfg.paths["API_RULES"])

        self.col_mgr = ColumnConfigManager(self.cfg.paths["API_RULES"])
        self.display_mgr = ColumnDisplayManager(self.cfg.paths["API_RULES"])

        if not os.path.exists(self.cfg.paths["API_RULES"]):
            with open(self.cfg.paths["API_RULES"], "w", encoding="utf-8") as f: json.dump([], f)

        EventBus.subscribe("PROXY_LOG", lambda msg: self.after(0, self.log, f"[PROXY] {msg}"))

        self.create_widgets()
        self.after(1500, self.poll_db)
        self.after(1000, self.check_proxy_status)

    def log(self, msg: str) -> None:
        EventBus.publish("LOG_INFO", msg)

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

    def start_proxy(self):
        # Nutzt den absoluten Pfad aus der Config und verweist auf das neue services-Verzeichnis
        base_dir = self.cfg.config.get("BASE_DIR", "")
        addon_path = os.path.join(base_dir, "services", "mitm_addon.py")

        try:
            self.proxy_service.start_proxy(addon_path)
            self.lbl_proxy_status.config(text="Proxy: 🟢 Läuft", foreground="green")
        except Exception as e:
            messagebox.showerror("Proxy Fehler", f"Konnte Proxy nicht starten:\n{e}")

    def stop_proxy(self):
        self.proxy_service.stop_proxy()
        self.lbl_proxy_status.config(text="Proxy: 🔴 Offline", foreground="black")

    def check_proxy_status(self):
        if not self.proxy_service.is_running() and self.lbl_proxy_status.cget("text") != "Proxy: 🔴 Offline":
            self.lbl_proxy_status.config(text="Proxy: 🔴 Offline (Fehler)", foreground="red")
        self.after(1000, self.check_proxy_status)

    def clear_log(self):
        self.db_service.clear_db()
        self.known_ids.clear()
        for i in self.tree.get_children(): self.tree.delete(i)
        self.txt_req.delete("1.0", tk.END)
        self.txt_res.delete("1.0", tk.END)
        self.ent_comment.delete(0, tk.END)

    def push_cert(self):
        if AdbNetworkService.push_cert():
            messagebox.showinfo("Zertifikat", "Installiere es in Android als CA-Zertifikat.")
        else:
            messagebox.showwarning("Hinweis", "Zertifikat fehlt.")

    def route_usb(self):
        AdbNetworkService.route_usb()
        self.lbl_tunnel_status.config(text="Tunnel: 🟡 USB")

    def route_wlan(self):
        ip = simpledialog.askstring("WLAN Proxy", "Lokale IP deines PCs:")
        if ip:
            AdbNetworkService.route_wlan(ip)
            self.lbl_tunnel_status.config(text=f"Tunnel: 🔵 WLAN")

    def reset_route(self):
        AdbNetworkService.reset_route()
        self.lbl_tunnel_status.config(text="Tunnel: 🔴 Inaktiv")

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
            filter_text = self.ent_filter.get().strip()
            filter_changed = filter_text != self.last_filter_text
            self.last_filter_text = filter_text

            if force_rebuild or filter_changed:
                self.known_ids.clear()
                for i in self.tree.get_children(): self.tree.delete(i)

            rows = self.db_service.get_requests(filter_text)

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

            if added_new and at_bottom:
                children = self.tree.get_children()
                if children: self.tree.see(children[-1])
        except Exception:
            pass

    def on_request_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        req_id = self.tree.item(sel[0], "values")[0]
        row = self.db_service.get_request_by_id(req_id)

        if row:
            self.txt_req.delete("1.0", tk.END)
            self.txt_req.insert("end", f"=== HEADERS ===\n{row[0]}\n\n=== BODY ===\n{row[1]}")
            self.txt_res.delete("1.0", tk.END)
            self.txt_res.insert("end", f"=== HEADERS ===\n{row[2]}\n\n=== BODY ===\n{row[3]}")
            self.ent_comment.delete(0, tk.END)
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

        updated_row = self.db_service.update_request(req_id, comment, req_body, res_body)

        if updated_row:
            row_dict = self.process_row_to_dict(updated_row)
            all_cols = self.get_all_available_columns()
            self.tree.item(item_id, values=[row_dict.get(c, "") for c in all_cols])

    def copy_selected_packets(self, event=None):
        sel = self.tree.selection()
        if not sel: return
        ids = [self.tree.item(i, "values")[0] for i in sel]
        rows = self.db_service.get_full_requests_by_ids(ids)

        lines = []
        for r in rows:
            t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r[1]))
            lines.append(
                f"[{t}] {r[2]} {r[3]} (Status: {r[4]})\nKommentar: {r[9]}\n--- REQ BODY ---\n{r[6]}\n--- RES BODY ---\n{r[8]}\n" + "=" * 40)

        self.clipboard_clear()
        self.clipboard_append("\n\n".join(lines))
        return "break"

    def export_selected(self):
        sel = self.tree.selection()
        if not sel: return
        path = filedialog.asksaveasfilename(defaultextension=".json")
        if not path: return
        ids = [self.tree.item(i, "values")[0] for i in sel]
        rows = self.db_service.get_full_requests_by_ids(ids)

        export_data = [{"id": r[0], "timestamp": r[1], "method": r[2], "url": r[3], "status": r[4],
                        "req_headers": r[5], "req_body": r[6], "res_headers": r[7], "res_body": r[8], "comment": r[9]}
                       for r in rows]

        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4)

    def import_packets(self):
        path = filedialog.askopenfilename()
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for i in data:
                self.db_service.insert_packet(i)
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
        self.ent_rule_url.delete(0, tk.END)
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
        for r in self.load_rules():
            self.rules_tree.insert("", "end", values=(r["url_match"], r["action"], r["payload"][:30] + "..."))