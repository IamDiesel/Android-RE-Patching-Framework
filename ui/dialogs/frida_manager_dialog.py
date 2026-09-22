import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from ui.controllers.frida_manager_controller import FridaManagerController


class FridaManagerDialog(tk.Toplevel):
    def __init__(self, parent, frida_manager, on_update_callback=None):
        super().__init__(parent)
        self.title("🦊 Frida Dashboard (V8)")
        self.geometry("1400x850")
        self.transient(parent.winfo_toplevel())
        self.attributes("-topmost", True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.app = parent.app if hasattr(parent, 'app') else parent.master.app
        self.controller = FridaManagerController(self.app, frida_manager)
        self.on_update_callback = on_update_callback

        self.current_script_id = None
        self.drawer_visible = False
        self._hl_timer_js = None
        self._server_status_timer = None

        self.create_widgets()
        self.populate_scripts()
        self.populate_collections()

        # Initiale Anzeige des Action-Panels
        self._on_mode_change()
        # Starte Status-Schleife für den lokalen Server
        self._poll_server_status()

    def create_widgets(self):
        # =========================================================================
        # TOP PANEL: Konfiguration (Kompakt)
        # =========================================================================
        cfg = self.app.cfg.frida_config
        f_top = ttk.Frame(self)
        f_top.pack(fill="x", padx=10, pady=5)

        # Links: Modus
        f_mode = ttk.LabelFrame(f_top, text="1. Betriebsmodus")
        f_mode.pack(side="left", fill="y", padx=5)

        self.var_mode = tk.StringVar(value=cfg.mode)
        self.var_mode.trace_add("write", lambda *args: self._on_mode_change())

        combo_mode = ttk.Combobox(f_mode, textvariable=self.var_mode, state="readonly", width=20)
        combo_mode['values'] = ("listen", "connect", "script", "script_directory")
        combo_mode.pack(padx=10, pady=5)

        # Mitte: App Blockade (Wait / Resume)
        f_block = ttk.LabelFrame(f_top, text="2. App-Start Verhalten")
        f_block.pack(side="left", fill="y", padx=5)

        self.var_pause = tk.BooleanVar()
        self.var_pause.set(bool(cfg.pause_on_load))
        chk_pause = ttk.Checkbutton(f_block, text="⏸ App beim Start blockieren (wait)", variable=self.var_pause)
        chk_pause.pack(padx=10, pady=5)

        # Rechts: Netzwerk & Pfade
        f_net = ttk.LabelFrame(f_top, text="3. Netzwerk & ScriptDirectory Pfad")
        f_net.pack(side="left", fill="both", expand=True, padx=5)

        ttk.Label(f_net, text="Host/IP:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.ent_host = ttk.Entry(f_net, width=15)
        self.ent_host.insert(0, cfg.host)
        self.ent_host.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(f_net, text="Port:").grid(row=0, column=2, sticky="w", padx=5, pady=2)
        self.ent_port = ttk.Entry(f_net, width=8)
        self.ent_port.insert(0, str(cfg.port))
        self.ent_port.grid(row=0, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(f_net, text="Pfad:").grid(row=0, column=4, sticky="w", padx=(15, 5), pady=2)
        self.ent_path = ttk.Entry(f_net, width=35)
        self.ent_path.insert(0, cfg.script_directory_path)
        self.ent_path.grid(row=0, column=5, sticky="w", padx=5, pady=2)

        ttk.Button(f_top, text="💾 Setup Speichern", command=self.save_config).pack(side="right", padx=10, pady=10)

        # =========================================================================
        # MAIN WORKSPACE (PanedWindow)
        # =========================================================================
        self.main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill="both", expand=True, padx=10, pady=5)

        # --- LEFT: Script List ---
        f_left = ttk.Frame(self.main_paned)
        self.main_paned.add(f_left, weight=1)

        t_bar = ttk.Frame(f_left)
        t_bar.pack(fill="x", pady=2)
        ttk.Button(t_bar, text="➕ Neu", command=self.add_script).pack(side="left", padx=1)
        ttk.Button(t_bar, text="🗑 Löschen", command=self.delete_script).pack(side="left", padx=1)
        ttk.Button(t_bar, text="🔄 Aktualisieren", command=self.reload_store).pack(side="left", padx=1)

        self.tree_scripts = ttk.Treeview(f_left, columns=("Status", "Name"), show="headings")
        self.tree_scripts.heading("Status", text="Aktiv")
        self.tree_scripts.heading("Name", text="Skript Name")
        # Feature 1A: Breite reduziert und nicht dehnbar (stretch=False)
        self.tree_scripts.column("Status", width=40, stretch=False, anchor="center")

        # Feature 1C: Vertikaler Scrollbalken hinzugefügt
        scroll_y_scripts = ttk.Scrollbar(f_left, orient="vertical", command=self.tree_scripts.yview)
        self.tree_scripts.configure(yscrollcommand=scroll_y_scripts.set)
        scroll_y_scripts.pack(side="right", fill="y")
        self.tree_scripts.pack(side="left", fill="both", expand=True)

        self.tree_scripts.bind("<<TreeviewSelect>>", self.on_script_select)
        self.tree_scripts.bind("<Delete>", lambda e: self.delete_script())

        # --- MID: Editor & Dynamic Action Panel ---
        f_mid = ttk.LabelFrame(self.main_paned, text="JS/TS Code Editor")
        self.main_paned.add(f_mid, weight=3)

        f_name = ttk.Frame(f_mid)
        f_name.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_name, text="Skript-Name:").pack(side="left")
        self.ent_name = ttk.Entry(f_name)
        self.ent_name.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(f_name, text="💾 Code Speichern", command=self.save_script).pack(side="left", padx=5)
        self.btn_toggle = ttk.Button(f_name, text="Sammlungen & Sync ▶", command=self.toggle_drawer)
        self.btn_toggle.pack(side="right")

        f_txt_container = ttk.Frame(f_mid)
        f_txt_container.pack(fill="both", expand=True, padx=5, pady=5)
        self.scroll_y_code = ttk.Scrollbar(f_txt_container, orient="vertical")
        self.scroll_y_code.pack(side="right", fill="y")
        self.scroll_x_code = ttk.Scrollbar(f_txt_container, orient="horizontal")
        self.scroll_x_code.pack(side="bottom", fill="x")

        self.txt_code = tk.Text(
            f_txt_container, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10),
            insertbackground="white", wrap="none",
            yscrollcommand=self.scroll_y_code.set, xscrollcommand=self.scroll_x_code.set
        )
        self.txt_code.pack(side="left", fill="both", expand=True)
        self.scroll_y_code.config(command=self.txt_code.yview)
        self.scroll_x_code.config(command=self.txt_code.xview)
        self.txt_code.bind("<KeyRelease>", self._on_code_change)

        # Das dynamische Action Panel unten im Editor
        self.f_action_panel = ttk.Frame(f_mid)
        self.f_action_panel.pack(fill="x", padx=5, pady=10)

        # --- RIGHT (DRAWER): Collections & Sync ---
        self.f_right = ttk.PanedWindow(self.main_paned, orient=tk.VERTICAL)

        f_cols = ttk.LabelFrame(self.f_right, text="📂 Sammlungen")
        self.f_right.add(f_cols, weight=1)
        cb_bar = ttk.Frame(f_cols)
        cb_bar.pack(fill="x", padx=2, pady=2)
        ttk.Button(cb_bar, text="➕ Aus Auswahl", command=self.create_collection).pack(side="left", padx=1)
        ttk.Button(cb_bar, text="✏️ Umbenennen", command=self.rename_collection).pack(side="left", padx=1)
        ttk.Button(cb_bar, text="🗑 Löschen", command=self.delete_collection).pack(side="left", padx=1)

        self.tree_cols = ttk.Treeview(f_cols, columns=("Name", "Scripts"), show="headings")
        self.tree_cols.heading("Name", text="Sammlung")
        self.tree_cols.heading("Scripts", text="Skripte")
        self.tree_cols.pack(fill="both", expand=True, padx=2, pady=2)
        self.tree_cols.bind("<Delete>", lambda e: self.delete_collection())

        self.btn_push_col = ttk.Button(f_cols, text="📤 Ausgewählte Sammlung aufs Gerät pushen",
                                       command=self.push_selected_collection)
        self.btn_push_col.pack(fill="x", pady=2, padx=2)

        f_sync = ttk.LabelFrame(self.f_right, text="🔄 Geräte Sync (Modus 4)")
        self.f_right.add(f_sync, weight=1)
        sy_bar = ttk.Frame(f_sync)
        sy_bar.pack(fill="x", padx=2, pady=2)
        ttk.Button(sy_bar, text="🔄 Refresh", command=self.refresh_device_scripts).pack(side="left", padx=1)
        ttk.Button(sy_bar, text="🗑 Löschen", command=self.delete_from_device).pack(side="right", padx=1)

        self.tree_device = ttk.Treeview(f_sync, columns=("Filename",), show="headings")
        self.tree_device.heading("Filename", text="Dateiname auf dem Gerät")
        self.tree_device.pack(fill="both", expand=True, padx=2, pady=2)
        self.tree_device.bind("<Delete>", lambda e: self.delete_from_device())

    # =========================================================================
    # DYNAMIC ACTION PANEL LOGIC
    # =========================================================================
    def _on_mode_change(self):
        """Baut das Action-Panel unter dem Editor basierend auf dem gewählten Modus neu auf."""
        for widget in self.f_action_panel.winfo_children():
            widget.destroy()

        mode = self.var_mode.get()
        self.action_buttons = []  # Speichert Buttons für das asynchrone Deaktivieren

        if mode == "listen":
            lbl_info = ttk.Label(self.f_action_panel,
                                 text="ℹ️ USB-Injektion: App wartet. Klicke auf Zünden, um das Skript über USB zu injizieren.",
                                 font=("Segoe UI", 9, "italic"))
            lbl_info.pack(side="top", anchor="w", pady=(0, 5))

            btn = ttk.Button(self.f_action_panel, text="🔥 Skript via USB zünden", command=self._action_fire_usb)
            btn.pack(side="left")
            self.action_buttons.append(btn)

        elif mode == "connect":
            f_srv = ttk.Frame(self.f_action_panel)
            f_srv.pack(side="top", fill="x", pady=(0, 5))

            self.lbl_srv_status = ttk.Label(f_srv, text="Server Status: 🔴 Offline", font=("Segoe UI", 9, "bold"))
            self.lbl_srv_status.pack(side="left", padx=(0, 20))

            ttk.Button(f_srv, text="▶ Server Starten", command=self.controller.start_server).pack(side="left", padx=2)
            ttk.Button(f_srv, text="⏹ Server Stoppen", command=self.controller.stop_server).pack(side="left", padx=2)

            lbl_info = ttk.Label(self.f_action_panel,
                                 text="ℹ️ Connect: Framework agiert als lokaler Server. Skripte werden live an die App gepusht.",
                                 font=("Segoe UI", 9, "italic"))
            lbl_info.pack(side="top", anchor="w", pady=(5, 5))

            btn = ttk.Button(self.f_action_panel, text="📡 Live-Push an verbundene App", command=self._action_push_live)
            btn.pack(side="left")
            self.action_buttons.append(btn)

        elif mode == "script":
            lbl_info = ttk.Label(self.f_action_panel,
                                 text="ℹ️ Autarker Modus: Das aktive Skript wird beim nächsten APK-Build (BUILD_NATIVE) fest in die App integriert.",
                                 font=("Segoe UI", 9, "italic"))
            lbl_info.pack(side="top", anchor="w", pady=(0, 5))

            btn = ttk.Button(self.f_action_panel, text="✅ Aktuelles Skript als Build-Ziel setzen",
                             command=self.set_active)
            btn.pack(side="left")

        elif mode == "script_directory":
            lbl_info = ttk.Label(self.f_action_panel,
                                 text="ℹ️ Geräte-Sync: Frida lädt Skripte direkt aus dem Android-Ordner. Pushe das Skript und starte die App neu.",
                                 font=("Segoe UI", 9, "italic"))
            lbl_info.pack(side="top", anchor="w", pady=(0, 5))

            btn = ttk.Button(self.f_action_panel, text="📤 Dieses Skript auf das Gerät pushen",
                             command=self._action_push_device)
            btn.pack(side="left")
            self.action_buttons.append(btn)

            if not self.drawer_visible:
                ttk.Button(self.f_action_panel, text="Sync-Ordner öffnen ▶", command=self.toggle_drawer).pack(
                    side="right")

    def _poll_server_status(self):
        """Aktualisiert die Server-Anzeige im Connect-Modus alle 1000ms."""
        if self.var_mode.get() == "connect" and hasattr(self, "lbl_srv_status") and self.lbl_srv_status.winfo_exists():
            status = self.controller.get_server_status()
            if status["is_running"]:
                color = "green" if status["clients"] > 0 else "orange"
                text = f"Server Status: 🟢 Horcht ({status['clients']} Clients)"
                self.lbl_srv_status.config(text=text, foreground=color)
            else:
                self.lbl_srv_status.config(text="Server Status: 🔴 Offline", foreground="red")

        self._server_status_timer = self.after(1000, self._poll_server_status)

    # =========================================================================
    # ASYNC EXECUTION WRAPPERS (Für UI Feedback)
    # =========================================================================
    def _disable_buttons_for_compilation(self):
        for btn in self.action_buttons:
            if btn.winfo_exists():
                btn.config(state="disabled", text="⏳ Kompiliere JS/TS...")
        if hasattr(self, "btn_push_col") and self.btn_push_col.winfo_exists():
            self.btn_push_col.config(state="disabled")

    def _enable_buttons(self, *args):
        self._on_mode_change()  # Zeichnet Buttons sauber mit korrekten Namen neu
        if hasattr(self, "btn_push_col") and self.btn_push_col.winfo_exists():
            self.btn_push_col.config(state="normal")

    def _action_fire_usb(self):
        code = self.txt_code.get("1.0", tk.END).strip()
        self.controller.fire_usb_listen(code, on_start=self._disable_buttons_for_compilation,
                                        on_done=self._enable_buttons)

    def _action_push_live(self):
        code = self.txt_code.get("1.0", tk.END).strip()
        self.controller.push_live_script(code, on_start=self._disable_buttons_for_compilation,
                                         on_done=self._enable_buttons)

    def _action_push_device(self):
        code = self.txt_code.get("1.0", tk.END).strip()
        filename = self.ent_name.get().strip().replace(" ", "_").lower()
        if not code or not filename: return

        def on_done(files):
            self._update_device_list(files)
            self._enable_buttons()

        self.controller.push_to_device(code, filename, on_start=self._disable_buttons_for_compilation, on_done=on_done)

    def push_selected_collection(self):
        sel = self.tree_cols.selection()
        if not sel: return
        col_id = sel[0]

        def on_done(files):
            self._update_device_list(files)
            self._enable_buttons()

        self.controller.push_collection_to_device(col_id, on_start=self._disable_buttons_for_compilation,
                                                  on_done=on_done)

    # =========================================================================
    # CONFIG & SYSTEM EVENTS
    # =========================================================================
    def has_config_changes(self) -> bool:
        cfg = self.app.cfg.frida_config
        current_port = int(self.ent_port.get()) if str(self.ent_port.get()).strip().isdigit() else 27042
        return (
                self.var_mode.get() != cfg.mode
                or self.ent_host.get().strip() != cfg.host
                or current_port != cfg.port
                or self.ent_path.get().strip() != cfg.script_directory_path
                or self.var_pause.get() != cfg.pause_on_load
        )

    def _execute_save(self):
        mode = self.var_mode.get()
        if mode == "script_directory":
            is_debuggable = self.app.cfg.config.get("INJECT_DEBUGGABLE", False)
            if not is_debuggable:
                ans = messagebox.askyesno(
                    "Debuggable Flag benötigt",
                    "Der Modus 'ScriptDirectory' benötigt zwingend 'adb shell run-as', um Dateien pushen zu können.\n\n"
                    "Dies funktioniert nur, wenn die App als 'debuggable' markiert ist.\n\n"
                    "Möchtest du das 'Debuggable Flag' für den nächsten Build automatisch aktivieren?",
                    parent=self
                )
                if ans:
                    self.app.cfg.config["INJECT_DEBUGGABLE"] = True
                    if hasattr(self.app, "workspace_tab") and hasattr(self.app.workspace_tab, "var_debuggable"):
                        self.app.workspace_tab.var_debuggable.set(True)

        self.controller.save_config(
            self.var_mode.get(), self.ent_host.get(), self.ent_port.get(), self.ent_path.get(), self.var_pause.get()
        )

    def save_config(self):
        self._execute_save()
        messagebox.showinfo("Gespeichert",
                            "Frida-Setup erfolgreich gespeichert!\nDenk daran, einen neuen APK Build durchzuführen.",
                            parent=self)

    def on_close(self):
        if self._server_status_timer:
            self.after_cancel(self._server_status_timer)

        if self.has_config_changes():
            answer = messagebox.askyesnocancel("Ungespeicherte Änderungen", "Setup speichern vor dem Schließen?",
                                               parent=self)
            if answer is True:
                self._execute_save()
                self.destroy()
            elif answer is False:
                self.destroy()
        else:
            self.destroy()

    def toggle_drawer(self):
        if self.drawer_visible:
            self.main_paned.forget(self.f_right)
            self.btn_toggle.config(text="Sammlungen & Sync ▶")
            self.drawer_visible = False
        else:
            self.main_paned.add(self.f_right, weight=2)
            self.btn_toggle.config(text="Verbergen ◀")
            self.drawer_visible = True

    # =========================================================================
    # SKRIPT & SYNTAX HIGHLIGHTING
    # =========================================================================
    def reload_store(self):
        """Skripte/Collections neu von der Platte laden (externe/MCP-Aenderungen uebernehmen)."""
        try:
            changed = self.controller.manager.reload_if_changed()
        except Exception:
            changed = False
        self.populate_scripts()
        self.populate_collections()
        try:
            self.app.log("[Frida] Liste aktualisiert"
                         + (" — externe Aenderungen uebernommen." if changed else " (bereits aktuell)."))
        except Exception:
            pass

    def populate_scripts(self):
        for i in self.tree_scripts.get_children(): self.tree_scripts.delete(i)
        # Feature 1B: Umgekehrte Sortierung (neu zu alt) durch reversed()
        for s in reversed(self.controller.manager.scripts):
            status = "✅" if s.id == self.controller.manager.active_script_id else ""
            self.tree_scripts.insert("", "end", iid=s.id, values=(status, s.name))

    def on_script_select(self, event):
        sel = self.tree_scripts.selection()
        if not sel: return
        self.current_script_id = sel[0]
        script = self.controller.manager.get_script_by_id(self.current_script_id)
        if script:
            self.ent_name.delete(0, tk.END)
            self.ent_name.insert(0, script.name)
            self.txt_code.delete("1.0", tk.END)
            self.txt_code.insert("1.0", script.code)
            self._apply_js_highlighting()

    def add_script(self):
        script = self.controller.create_new_script()
        self.populate_scripts()
        self.tree_scripts.selection_set(script.id)

    def delete_script(self):
        sel = self.tree_scripts.selection()
        if not sel: return
        script_id = sel[0]
        if messagebox.askyesno("Löschen", "Skript wirklich löschen?", parent=self):
            self.controller.delete_script(script_id)
            if self.current_script_id == script_id:
                self.txt_code.delete("1.0", tk.END)
                self.ent_name.delete(0, tk.END)
                self.current_script_id = None
            self.populate_scripts()

    def save_script(self):
        if not self.current_script_id: return
        script = self.controller.manager.get_script_by_id(self.current_script_id)
        if script:
            script.name = self.ent_name.get()
            script.code = self.txt_code.get("1.0", tk.END).strip()
            self.controller.manager.save()
            self.populate_scripts()
            self.tree_scripts.selection_set(script.id)
            self.populate_collections()

    def set_active(self):
        sel = self.tree_scripts.selection()
        if not sel: return
        self.controller.set_active_script(sel[0])
        self.populate_scripts()
        self.tree_scripts.selection_set(sel[0])
        if self.on_update_callback: self.on_update_callback()

    def _on_code_change(self, event=None):
        if self._hl_timer_js:
            self.after_cancel(self._hl_timer_js)
        self._hl_timer_js = self.after(300, self._apply_js_highlighting)

    def _apply_js_highlighting(self):
        if not self.txt_code.winfo_exists(): return
        self.txt_code.tag_configure("js_keyword", foreground="#569CD6", font=("Consolas", 10, "bold"))
        self.txt_code.tag_configure("js_string", foreground="#CE9178")
        self.txt_code.tag_configure("js_comment", foreground="#6A9955", font=("Consolas", 10, "italic"))
        self.txt_code.tag_configure("js_frida", foreground="#4EC9B0")
        self.txt_code.tag_configure("js_func", foreground="#DCDCAA")

        for tag in ["js_keyword", "js_string", "js_comment", "js_frida", "js_func"]:
            self.txt_code.tag_remove(tag, "1.0", tk.END)

        content = self.txt_code.get("1.0", "end-1c")
        for match in re.finditer(r'\b([a-zA-Z0-9_]+)\s*\(', content):
            self.txt_code.tag_add("js_func", f"1.0+{match.start(1)}c", f"1.0+{match.end(1)}c")
        keywords = r'\b(import|from|const|let|var|function|if|else|try|catch|return|new|class|console|await|async|for|while|switch|case|break|continue|this)\b'
        for match in re.finditer(keywords, content):
            self.txt_code.tag_add("js_keyword", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        frida_apis = r'\b(Interceptor|Module|Memory|Thread|Java|NativeFunction|NativeCallback|Process|Script|rpc|send|recv|ptr|NULL)\b'
        for match in re.finditer(frida_apis, content):
            self.txt_code.tag_add("js_frida", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'|`[^`]*`)', content):
            self.txt_code.tag_add("js_string", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r'(//.*|/\*[\s\S]*?\*/)', content):
            self.txt_code.tag_add("js_comment", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

        self.txt_code.tag_raise("js_string")
        self.txt_code.tag_raise("js_comment")

    # =========================================================================
    # COLLECTIONS & DEVICE SYNC
    # =========================================================================
    def populate_collections(self):
        for i in self.tree_cols.get_children(): self.tree_cols.delete(i)
        for c in self.controller.manager.collections:
            names = [self.controller.manager.get_script_by_id(sid).name for sid in c.script_ids if
                     self.controller.manager.get_script_by_id(sid)]
            self.tree_cols.insert("", "end", iid=c.id, values=(c.name, ", ".join(names)))

    def create_collection(self):
        sel = self.tree_scripts.selection()
        if not sel: return messagebox.showinfo("Info", "Bitte markiere Skripte (STRG+Klick).", parent=self)
        name = simpledialog.askstring("Sammlung", "Name:", parent=self)
        if name:
            self.controller.create_collection(name, list(sel))
            self.populate_collections()

    def rename_collection(self):
        sel = self.tree_cols.selection()
        if not sel: return
        col_id = sel[0]
        old_name = self.tree_cols.item(col_id, "values")[0]
        new_name = simpledialog.askstring("Umbenennen", "Neuer Name:", initialvalue=old_name, parent=self)
        if new_name:
            self.controller.rename_collection(col_id, new_name)
            self.populate_collections()

    def delete_collection(self):
        sel = self.tree_cols.selection()
        if not sel: return
        if messagebox.askyesno("Löschen", "Sammlung löschen?", parent=self):
            self.controller.delete_collection(sel[0])
            self.populate_collections()

    def _update_device_list(self, files: list):
        for i in self.tree_device.get_children(): self.tree_device.delete(i)
        for f in files: self.tree_device.insert("", "end", iid=f, values=(f,))

    def refresh_device_scripts(self):
        self.controller.refresh_device_scripts(self._update_device_list)

    def delete_from_device(self):
        sel = self.tree_device.selection()
        if not sel: return
        filename = sel[0]
        if messagebox.askyesno("Löschen", f"'{filename}' vom Gerät löschen?", parent=self):
            self.controller.delete_from_device(filename, self._update_device_list)