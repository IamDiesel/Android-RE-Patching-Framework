import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import subprocess
import sqlite3
import json
import os
import time
import sys
import threading


class APIInspectorTab(ttk.Frame):
    def __init__(self, parent, config_mgr, logger_func):
        super().__init__(parent)
        self.cfg = config_mgr
        self.log = logger_func
        self.proxy_process = None
        self.known_ids = set()

        self.init_db()
        self.create_widgets()

        # Periodische Aktualisierung von DB und Proxy-Prozess
        self.after(1500, self.poll_db)
        self.after(1000, self.check_proxy_status)

    def init_db(self):
        db_path = self.cfg.paths["API_DB"]
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS requests
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      timestamp REAL, method TEXT, url TEXT, status INTEGER,
                      req_headers TEXT, req_body TEXT, res_headers TEXT, res_body TEXT, comment TEXT)''')
        conn.commit()
        conn.close()

        if not os.path.exists(self.cfg.paths["API_RULES"]):
            with open(self.cfg.paths["API_RULES"], "w", encoding="utf-8") as f:
                json.dump([], f)

    def create_widgets(self):
        # --- Toolbar ---
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=5, pady=5)

        ttk.Button(toolbar, text="▶ Start Proxy", command=self.start_proxy).pack(side="left", padx=2)
        ttk.Button(toolbar, text="⏹ Stop Proxy", command=self.stop_proxy).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🗑 Clear Log", command=self.clear_log).pack(side="left", padx=2)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        ttk.Button(toolbar, text="💾 Export Selected", command=self.export_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📂 Import", command=self.import_packets).pack(side="left", padx=2)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        ttk.Button(toolbar, text="📱 Push Cert", command=self.push_cert).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🔌 Route USB", command=self.route_usb).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📡 Route WLAN", command=self.route_wlan).pack(side="left", padx=2)
        ttk.Button(toolbar, text="❌ Reset Route", command=self.reset_route).pack(side="left", padx=2)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)

        # Status Indikatoren
        self.lbl_proxy_status = ttk.Label(toolbar, text="Proxy: 🔴 Offline", font=("Segoe UI", 10, "bold"))
        self.lbl_proxy_status.pack(side="left", padx=5)

        self.lbl_tunnel_status = ttk.Label(toolbar, text="Tunnel: 🔴 Inaktiv", font=("Segoe UI", 10, "bold"))
        self.lbl_tunnel_status.pack(side="left", padx=5)

        # --- Filter Bar ---
        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=5, pady=2)
        ttk.Label(filter_bar, text="Filter (URL/Method):").pack(side="left")
        self.ent_filter = ttk.Entry(filter_bar, width=40)
        self.ent_filter.pack(side="left", padx=5)
        self.ent_filter.bind("<KeyRelease>", lambda e: self.refresh_list(force_rebuild=True))

        # --- Split View (PanedWindow) ---
        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned.pack(fill="both", expand=True, padx=5, pady=5)

        # Top: Request List (Treeview)
        list_frame = ttk.Frame(self.paned)
        self.paned.add(list_frame, weight=1)

        cols = ("ID", "Time", "Method", "URL", "Status", "Comment")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=8, selectmode="extended")
        for c in cols: self.tree.heading(c, text=c)
        self.tree.column("ID", width=50)
        self.tree.column("Time", width=90)
        self.tree.column("Method", width=70)
        self.tree.column("URL", width=450)
        self.tree.column("Status", width=60)
        self.tree.column("Comment", width=200)
        self.tree.pack(fill="both", expand=True, side="left")

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.bind("<<TreeviewSelect>>", self.on_request_select)
        self.tree.bind("<Control-c>", self.copy_selected_packets)

        # Bottom: Notebook (Details & Manipulation)
        bottom_frame = ttk.Frame(self.paned)
        self.paned.add(bottom_frame, weight=2)

        self.req_notebook = ttk.Notebook(bottom_frame)
        self.req_notebook.pack(fill="both", expand=True)

        # Details Tab
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

        # Manipulation Tab
        self.tab_manipulate = ttk.Frame(self.req_notebook)
        self.req_notebook.add(self.tab_manipulate, text="Intercept Regeln")
        self.setup_manipulation_tab()

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

    # --- Proxy Steuerung & Tunnel ---

    def start_proxy(self):
        if self.proxy_process and self.proxy_process.poll() is None:
            messagebox.showinfo("Info", "Proxy läuft bereits.")
            return

        script_dir = os.path.dirname(os.path.abspath(__file__))
        addon_path = os.path.join(script_dir, "mitm_addon.py")

        if not os.path.exists(addon_path):
            messagebox.showerror("Fehler", f"Addon fehlt:\n{addon_path}")
            return

        cmd = f"mitmdump --listen-host 0.0.0.0 -s \"{addon_path}\" --set api_db=\"{self.cfg.paths['API_DB']}\" --set rules_file=\"{self.cfg.paths['API_RULES']}\""
        self.log(f"[*] Starte Proxy-Command:\n> {cmd}")

        self.proxy_process = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )

        self.lbl_proxy_status.config(text="Proxy: 🟢 Läuft", foreground="green")
        threading.Thread(target=self._read_proxy_output, daemon=True).start()

    def _read_proxy_output(self):
        try:
            for line in iter(self.proxy_process.stdout.readline, ''):
                if line:
                    self.after(0, self.log, f"[PROXY] {line.strip()}")
        except Exception as e:
            self.after(0, self.log, f"[!] Proxy-Reader Fehler: {e}")

    def stop_proxy(self):
        if self.proxy_process:
            self.proxy_process.terminate()
            self.proxy_process = None
            self.lbl_proxy_status.config(text="Proxy: 🔴 Offline", foreground="black")
            self.log("[*] MITM Proxy gestoppt.")

    def check_proxy_status(self):
        if self.proxy_process:
            if self.proxy_process.poll() is not None:
                self.proxy_process = None
                self.lbl_proxy_status.config(text="Proxy: 🔴 Offline (Fehler)", foreground="red")
                self.log("[!] Proxy ist abgestürzt oder wurde beendet.")
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
        self.log("[*] API Log geleert.")

    def push_cert(self):
        cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.cer")
        if not os.path.exists(cert_path):
            messagebox.showwarning("Hinweis", "Zertifikat nicht gefunden. Wurde mitmproxy schon einmal gestartet?")
            return

        cmd = f"adb push \"{cert_path}\" /storage/emulated/0/Download/"
        subprocess.run(cmd, shell=True)

        self.log("[*] Zertifikat nach /Download/ gepusht.")
        messagebox.showinfo("Zertifikat",
                            "Zertifikat wurde nach /storage/emulated/0/Download/ kopiert.\n\nBitte in den Android-Einstellungen explizit als 'CA-Zertifikat' installieren.")

    def route_usb(self):
        subprocess.run("adb reverse tcp:8080 tcp:8080", shell=True)
        subprocess.run("adb shell settings put global http_proxy 127.0.0.1:8080", shell=True)
        self.lbl_tunnel_status.config(text="Tunnel: 🟡 USB (127.0.0.1)")
        self.log("[*] ADB Reverse eingerichtet und Android-Proxy auf 127.0.0.1:8080 gesetzt.")

    def route_wlan(self):
        ip = simpledialog.askstring("WLAN Proxy", "Lokale IP deines PCs:")
        if ip:
            subprocess.run(f"adb shell settings put global http_proxy {ip}:8080", shell=True)
            self.lbl_tunnel_status.config(text=f"Tunnel: 🔵 WLAN ({ip})")
            self.log(f"[*] WLAN Proxy auf dem Gerät gesetzt auf {ip}:8080")

    def reset_route(self):
        subprocess.run("adb reverse --remove-all", shell=True)
        subprocess.run("adb shell settings put global http_proxy :0", shell=True)
        self.lbl_tunnel_status.config(text="Tunnel: 🔴 Inaktiv")
        self.log("[*] Proxy-Routen auf dem Gerät entfernt.")

    # --- Datenhaltung & Polling (Intelligente Liste ohne Löschen) ---

    def poll_db(self):
        self.refresh_list(force_rebuild=False)
        self.after(1500, self.poll_db)

    def refresh_list(self, force_rebuild=False):
        if not os.path.exists(self.cfg.paths["API_DB"]): return
        try:
            conn = sqlite3.connect(self.cfg.paths["API_DB"])
            c = conn.cursor()

            filter_text = self.ent_filter.get().strip()

            if force_rebuild or filter_text:
                # Bei Filterwechsel oder Import/Clear die Tabelle neu befüllen
                self.known_ids.clear()
                for i in self.tree.get_children(): self.tree.delete(i)

                if filter_text:
                    c.execute(
                        "SELECT id, timestamp, method, url, status, comment FROM requests WHERE url LIKE ? OR method LIKE ? ORDER BY id ASC",
                        (f"%{filter_text}%", f"%{filter_text}%"))
                else:
                    c.execute("SELECT id, timestamp, method, url, status, comment FROM requests ORDER BY id ASC")
            else:
                # Inkrementell NUR NEUE Einträge holen (kein Nuking der Liste!)
                c.execute("SELECT id, timestamp, method, url, status, comment FROM requests ORDER BY id ASC")

            rows = c.fetchall()
            conn.close()

            # Prüfen ob der Nutzer am unteren Ende gescrollt ist
            scroll_pos = self.tree.yview()
            at_bottom = scroll_pos[1] >= 0.95 if scroll_pos else True
            has_selection = len(self.tree.selection()) > 0

            added_new = False
            for r in rows:
                rec_id = r[0]
                if rec_id not in self.known_ids:
                    self.known_ids.add(rec_id)
                    t_str = time.strftime('%H:%M:%S', time.localtime(r[1]))
                    self.tree.insert("", "end", values=(rec_id, t_str, r[2], r[3], r[4], r[5] or ""))
                    added_new = True
                else:
                    # Aktualisiere z. B. geänderte Kommentare im bestehenden Item
                    for child in self.tree.get_children():
                        if self.tree.item(child, "values")[0] == rec_id:
                            t_str = time.strftime('%H:%M:%S', time.localtime(r[1]))
                            self.tree.item(child, values=(rec_id, t_str, r[2], r[3], r[4], r[5] or ""))

            # Automatisch nach unten scrollen NUR WENN der Nutzer nicht liest/selektiert hat
            if added_new and at_bottom and not has_selection:
                children = self.tree.get_children()
                if children:
                    self.tree.see(children[-1])

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
            self.txt_req.delete("1.0", tk.END)
            self.txt_req.insert("end", f"=== HEADERS ===\n{row[0]}\n\n=== BODY ===\n{row[1]}")

            self.txt_res.delete("1.0", tk.END)
            self.txt_res.insert("end", f"=== HEADERS ===\n{row[2]}\n\n=== BODY ===\n{row[3]}")

            self.ent_comment.delete(0, tk.END)
            self.ent_comment.insert(0, row[4] or "")

    def save_packet_edits(self):
        """Speichert geänderte Details (Response/Request Body & Kommentar) zurück in die DB."""
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Hinweis", "Kein Paket zum Speichern ausgewählt.")
            return

        req_id = self.tree.item(sel[0], "values")[0]
        comment = self.ent_comment.get()

        req_raw = self.txt_req.get("1.0", tk.END)
        res_raw = self.txt_res.get("1.0", tk.END)

        # Versuche Header und Body zu trennen
        req_body = req_raw.split("=== BODY ===")[-1].strip() if "=== BODY ===" in req_raw else req_raw.strip()
        res_body = res_raw.split("=== BODY ===")[-1].strip() if "=== BODY ===" in res_raw else res_raw.strip()

        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()
        c.execute("UPDATE requests SET comment=?, req_body=?, res_body=? WHERE id=?",
                  (comment, req_body, res_body, req_id))
        conn.commit()
        conn.close()

        self.refresh_list(force_rebuild=True)
        self.log(f"[*] Paket #{req_id} und Kommentar erfolgreich in DB aktualisiert.")

    def copy_selected_packets(self, event=None):
        """Kopiert alle selektierten Pakete mit STRG+C in die Zwischenablage."""
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
            row = c.fetchone()
            if row:
                t_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row[0]))
                lines.append(
                    f"[{t_str}] {row[1]} {row[2]} (Status: {row[3]})\n"
                    f"Kommentar: {row[8] or '-'}\n"
                    f"--- REQUEST HEADERS ---\n{row[4]}\n"
                    f"--- REQUEST BODY ---\n{row[5]}\n"
                    f"--- RESPONSE HEADERS ---\n{row[6]}\n"
                    f"--- RESPONSE BODY ---\n{row[7]}\n"
                    + "=" * 60
                )
        conn.close()

        clipboard_text = "\n\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(clipboard_text)
        self.log(f"[*] {len(sel)} Paket(e) in die Zwischenablage kopiert (STRG+C).")

    # --- Export & Import ---

    def export_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Export", "Bitte mindestens ein Paket in der Liste markieren.")
            return

        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")],
                                            title="Markierte Pakete exportieren")
        if not path: return

        ids = [self.tree.item(item, "values")[0] for item in sel]
        conn = sqlite3.connect(self.cfg.paths["API_DB"])
        c = conn.cursor()

        query = f"SELECT id, timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment FROM requests WHERE id IN ({','.join(['?'] * len(ids))})"
        c.execute(query, ids)
        rows = c.fetchall()
        conn.close()

        export_data = []
        for r in rows:
            export_data.append({
                "id": r[0], "timestamp": r[1], "method": r[2], "url": r[3],
                "status": r[4], "req_headers": r[5], "req_body": r[6],
                "res_headers": r[7], "res_body": r[8], "comment": r[9]
            })

        with open(path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=4, ensure_ascii=False)

        self.log(f"[*] {len(export_data)} Paket(e) erfolgreich nach {path} exportiert.")

    def import_packets(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Pakete importieren")
        if not path: return

        try:
            with open(path, "r", encoding="utf-8") as f:
                imported_data = json.load(f)

            conn = sqlite3.connect(self.cfg.paths["API_DB"])
            c = conn.cursor()
            count = 0
            for item in imported_data:
                c.execute('''INSERT INTO requests (timestamp, method, url, status, req_headers, req_body, res_headers, res_body, comment)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                          (item.get("timestamp", time.time()), item.get("method", "GET"), item.get("url", ""),
                           item.get("status", 200), item.get("req_headers", "{}"), item.get("req_body", ""),
                           item.get("res_headers", "{}"), item.get("res_body", ""), item.get("comment", "")))
                count += 1
            conn.commit()
            conn.close()

            self.refresh_list(force_rebuild=True)
            self.log(f"[*] {count} Paket(e) aus {path} importiert.")
            messagebox.showinfo("Import", f"{count} Paket(e) erfolgreich importiert!")
        except Exception as e:
            messagebox.showerror("Import Fehler", f"Fehler beim Importieren:\n{e}")

    # --- Intercept Regeln ---

    def load_rules(self):
        try:
            with open(self.cfg.paths["API_RULES"], "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def save_rules(self, rules):
        with open(self.cfg.paths["API_RULES"], "w", encoding="utf-8") as f:
            json.dump(rules, f, indent=4, ensure_ascii=False)
        self.refresh_rules()

    def add_rule(self):
        rules = self.load_rules()
        rules.append({
            "url_match": self.ent_rule_url.get(),
            "action": self.cb_rule_action.get(),
            "payload": self.txt_rule_payload.get("1.0", tk.END).strip()
        })
        self.save_rules(rules)
        self.ent_rule_url.delete(0, tk.END)
        self.txt_rule_payload.delete("1.0", tk.END)

    def delete_rule(self):
        sel = self.rules_tree.selection()
        if not sel: return
        idx = self.rules_tree.index(sel[0])
        rules = self.load_rules()
        del rules[idx]
        self.save_rules(rules)

    def refresh_rules(self):
        for i in self.rules_tree.get_children(): self.rules_tree.delete(i)
        for r in self.load_rules():
            self.rules_tree.insert("", "end", values=(r["url_match"], r["action"], r["payload"][:30] + "..."))