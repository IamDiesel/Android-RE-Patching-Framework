import os
import re
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from ui.controllers.frida_manager_controller import FridaManagerController


class FridaManagerDialog(tk.Toplevel):
    def __init__(self, parent, frida_manager, on_update_callback=None):
        super().__init__(parent)
        self.title("🦊 Frida Advanced Manager (V8)")
        self.geometry("1300x750")
        self.transient(parent.winfo_toplevel())
        self.attributes("-topmost", True)

        # Event fangen, um Config auto-zu-saven beim Schließen
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.app = parent.app if hasattr(parent, 'app') else parent.master.app
        self.controller = FridaManagerController(self.app, frida_manager)
        self.on_update_callback = on_update_callback

        self.current_script_id = None
        self.drawer_visible = False
        self._hl_timer_js = None  # Debouncer für Syntax-Highlighting

        self.create_widgets()
        self.populate_scripts()
        self.populate_collections()

    def create_widgets(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # TAB 1: Main Workspace (Editor + Drawer)
        self.tab_workspace = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_workspace, text="💻 Workspace & Sync")
        self._build_workspace_tab(self.tab_workspace)

        # TAB 2: Konfiguration & Server
        self.tab_server = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_server, text="⚙️ Modus & Setup")
        self._build_server_tab(self.tab_server)

    def _build_workspace_tab(self, parent):
        self.main_paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # --- LEFT: Script List ---
        f_left = ttk.Frame(self.main_paned)
        self.main_paned.add(f_left, weight=1)

        t_bar = ttk.Frame(f_left)
        t_bar.pack(fill="x", pady=2)
        ttk.Button(t_bar, text="➕ Neu", command=self.add_script).pack(side="left", padx=1)
        ttk.Button(t_bar, text="🗑 Löschen", command=self.delete_script).pack(side="left", padx=1)

        self.tree_scripts = ttk.Treeview(f_left, columns=("Status", "Name"), show="headings")
        self.tree_scripts.heading("Status", text="Aktiv")
        self.tree_scripts.heading("Name", text="Skript Name")
        self.tree_scripts.column("Status", width=50, anchor="center")
        self.tree_scripts.pack(fill="both", expand=True)

        self.tree_scripts.bind("<<TreeviewSelect>>", self.on_script_select)
        self.tree_scripts.bind("<Delete>", lambda e: self.delete_script())

        ttk.Button(f_left, text="✅ Als Build-Skript setzen (Modus 3)", command=self.set_active).pack(fill="x", pady=5)

        # --- MID: Editor & Execution ---
        f_mid = ttk.LabelFrame(self.main_paned, text="JS/TS Editor")
        self.main_paned.add(f_mid, weight=3)

        # Top Bar im Editor (Name + Drawer Toggle)
        f_name = ttk.Frame(f_mid)
        f_name.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_name, text="Name:").pack(side="left")
        self.ent_name = ttk.Entry(f_name)
        self.ent_name.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(f_name, text="💾 Speichern", command=self.save_script).pack(side="left", padx=5)

        self.btn_toggle = ttk.Button(f_name, text="Sammlungen & Sync ▶", command=self.toggle_drawer)
        self.btn_toggle.pack(side="right")

        # Container für den Editor inkl. beidseitigen Scrollbars
        f_txt_container = ttk.Frame(f_mid)
        f_txt_container.pack(fill="both", expand=True, padx=5, pady=5)

        self.scroll_y_code = ttk.Scrollbar(f_txt_container, orient="vertical")
        self.scroll_y_code.pack(side="right", fill="y")
        self.scroll_x_code = ttk.Scrollbar(f_txt_container, orient="horizontal")
        self.scroll_x_code.pack(side="bottom", fill="x")

        self.txt_code = tk.Text(
            f_txt_container, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10),
            insertbackground="white", wrap="none",  # Zeilenumbruch aus, damit X-Scroll wirkt
            yscrollcommand=self.scroll_y_code.set, xscrollcommand=self.scroll_x_code.set
        )
        self.txt_code.pack(side="left", fill="both", expand=True)

        self.scroll_y_code.config(command=self.txt_code.yview)
        self.scroll_x_code.config(command=self.txt_code.xview)

        # Bindings für Syntax Highlighting
        self.txt_code.bind("<KeyRelease>", self._on_code_change)

        f_exec = ttk.Frame(f_mid)
        f_exec.pack(fill="x", padx=5, pady=5)
        ttk.Button(f_exec, text="🔥 Fire USB (Listen Modus)",
                   command=lambda: self.controller.fire_usb_listen(self.txt_code.get("1.0", tk.END))).pack(side="left",
                                                                                                           padx=5)
        ttk.Button(f_exec, text="📡 Push Live (Connect Modus)",
                   command=lambda: self.controller.push_live_script(self.txt_code.get("1.0", tk.END))).pack(side="left",
                                                                                                            padx=5)

        # --- RIGHT (DRAWER): Collections & Sync ---
        self.f_right = ttk.PanedWindow(self.main_paned, orient=tk.VERTICAL)

        # TOP Drawer: Collections
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

        ttk.Button(f_cols, text="📤 Ausgewählte Sammlung aufs Gerät pushen", command=self.push_selected_collection).pack(
            fill="x", pady=2, padx=2)

        # BOTTOM Drawer: Device Sync
        f_sync = ttk.LabelFrame(self.f_right, text="🔄 Geräte Sync (Modus 4)")
        self.f_right.add(f_sync, weight=1)

        sy_bar = ttk.Frame(f_sync)
        sy_bar.pack(fill="x", padx=2, pady=2)
        ttk.Button(sy_bar, text="🔄 Refresh", command=self.refresh_device_scripts).pack(side="left", padx=1)
        ttk.Button(sy_bar, text="📤 Editor-Skript Pushen", command=self.push_to_device).pack(side="left", padx=1)
        ttk.Button(sy_bar, text="🗑 Löschen", command=self.delete_from_device).pack(side="right", padx=1)

        self.tree_device = ttk.Treeview(f_sync, columns=("Filename",), show="headings")
        self.tree_device.heading("Filename", text="Dateiname auf dem Gerät")
        self.tree_device.pack(fill="both", expand=True, padx=2, pady=2)
        self.tree_device.bind("<Delete>", lambda e: self.delete_from_device())

    def _build_server_tab(self, parent):
        cfg = self.app.cfg.frida_config

        f_mode = ttk.LabelFrame(parent, text="1. Betriebsmodus wählen")
        f_mode.pack(fill="x", padx=10, pady=10)

        self.var_mode = tk.StringVar(value=cfg.mode)
        ttk.Radiobutton(f_mode, text="Listen (Host injiziert via USB)", variable=self.var_mode, value="listen").pack(
            anchor="w", padx=10, pady=2)
        ttk.Radiobutton(f_mode, text="Connect (App verbindet sich zum lokalen Host-Server)", variable=self.var_mode,
                        value="connect").pack(anchor="w", padx=10, pady=2)
        ttk.Radiobutton(f_mode, text="Script (Autark, Skript wird beim Build integriert)", variable=self.var_mode,
                        value="script").pack(anchor="w", padx=10, pady=2)
        ttk.Radiobutton(f_mode, text="ScriptDirectory (Autark, App überwacht Geräte-Ordner)", variable=self.var_mode,
                        value="script_directory").pack(anchor="w", padx=10, pady=2)

        f_net = ttk.LabelFrame(parent, text="2. Netzwerk & Pfade (Modus 2 & 4)")
        f_net.pack(fill="x", padx=10, pady=10)

        ttk.Label(f_net, text="Host:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.ent_host = ttk.Entry(f_net, width=20)
        self.ent_host.insert(0, cfg.host)
        self.ent_host.grid(row=0, column=1, sticky="w", padx=5, pady=5)

        ttk.Label(f_net, text="Port:").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.ent_port = ttk.Entry(f_net, width=10)
        self.ent_port.insert(0, str(cfg.port))
        self.ent_port.grid(row=0, column=3, sticky="w", padx=5, pady=5)

        ttk.Label(f_net, text="Geräte-Pfad (ScriptDirectory):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.ent_path = ttk.Entry(f_net, width=50)
        self.ent_path.insert(0, cfg.script_directory_path)
        self.ent_path.grid(row=1, column=1, columnspan=3, sticky="w", padx=5, pady=5)

        ttk.Button(f_net, text="💾 Konfiguration Speichern", command=self.save_config).grid(row=2, column=0,
                                                                                           columnspan=4, pady=10)

        f_server = ttk.LabelFrame(parent, text="3. Lokaler Frida Server (Nur Connect Modus)")
        f_server.pack(fill="x", padx=10, pady=10)
        ttk.Button(f_server, text="▶ Server Starten", command=self.controller.start_server).pack(side="left", padx=10,
                                                                                                 pady=10)
        ttk.Button(f_server, text="⏹ Server Stoppen", command=self.controller.stop_server).pack(side="left", padx=10,
                                                                                                pady=10)

    # --- Syntax Highlighting (JS/TS) ---
    def _on_code_change(self, event=None):
        if self._hl_timer_js:
            self.after_cancel(self._hl_timer_js)
        self._hl_timer_js = self.after(300, self._apply_js_highlighting)

    def _apply_js_highlighting(self):
        if not self.txt_code.winfo_exists(): return

        # Farben definieren (VS Code Dark Theme Annäherung)
        self.txt_code.tag_configure("js_keyword", foreground="#569CD6", font=("Consolas", 10, "bold"))
        self.txt_code.tag_configure("js_string", foreground="#CE9178")
        self.txt_code.tag_configure("js_comment", foreground="#6A9955", font=("Consolas", 10, "italic"))
        self.txt_code.tag_configure("js_frida", foreground="#4EC9B0")
        self.txt_code.tag_configure("js_func", foreground="#DCDCAA")

        # Tags vor dem Neuzeichnen entfernen
        for tag in ["js_keyword", "js_string", "js_comment", "js_frida", "js_func"]:
            self.txt_code.tag_remove(tag, "1.0", tk.END)

        content = self.txt_code.get("1.0", "end-1c")

        # 1. Funktionsaufrufe
        for match in re.finditer(r'\b([a-zA-Z0-9_]+)\s*\(', content):
            self.txt_code.tag_add("js_func", f"1.0+{match.start(1)}c", f"1.0+{match.end(1)}c")

        # 2. Keywords (JS/TS)
        keywords = r'\b(import|from|const|let|var|function|if|else|try|catch|return|new|class|console|await|async|for|while|switch|case|break|continue|this)\b'
        for match in re.finditer(keywords, content):
            self.txt_code.tag_add("js_keyword", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

        # 3. Frida spezifische Klassen & APIs
        frida_apis = r'\b(Interceptor|Module|Memory|Thread|Java|NativeFunction|NativeCallback|Process|Script|rpc|send|recv|ptr|NULL)\b'
        for match in re.finditer(frida_apis, content):
            self.txt_code.tag_add("js_frida", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

        # 4. Strings (Berücksichtigt Escapes und Template Literals)
        for match in re.finditer(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'|`[^`]*`)', content):
            self.txt_code.tag_add("js_string", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

        # 5. Kommentare (// und /* */)
        for match in re.finditer(r'(//.*|/\*[\s\S]*?\*/)', content):
            self.txt_code.tag_add("js_comment", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

        # Priorität setzen: Kommentare und Strings überschreiben enthaltene Keywords
        self.txt_code.tag_raise("js_string")
        self.txt_code.tag_raise("js_comment")

    # --- Event Handlers & Core UI Logic ---
    def has_config_changes(self) -> bool:
        cfg = self.app.cfg.frida_config
        current_port = int(self.ent_port.get()) if str(self.ent_port.get()).strip().isdigit() else 27042
        return (
                self.var_mode.get() != cfg.mode
                or self.ent_host.get().strip() != cfg.host
                or current_port != cfg.port
                or self.ent_path.get().strip() != cfg.script_directory_path
        )

    def _execute_save(self):
        """Kapselt die Speicherlogik und fängt Modus-spezifische Konflikte (z.B. fehlendes Debug-Flag) ab."""
        mode = self.var_mode.get()

        # Check für ScriptDirectory Modus
        if mode == "script_directory":
            is_debuggable = self.app.cfg.config.get("INJECT_DEBUGGABLE", False)
            if not is_debuggable:
                ans = messagebox.askyesno(
                    "Debuggable Flag benötigt",
                    "Der Modus 'ScriptDirectory' (Geräte Sync) benötigt zwingend 'adb shell run-as', um Dateien in das isolierte App-Verzeichnis pushen zu können.\n\n"
                    "Dies funktioniert nur, wenn die App als 'debuggable' markiert ist.\n\n"
                    "Möchtest du das 'Debuggable Flag' für den nächsten Build automatisch in der Workspace-Konfiguration aktivieren?",
                    parent=self
                )
                if ans:
                    self.app.cfg.config["INJECT_DEBUGGABLE"] = True
                    # Falls der Workspace-Tab geladen ist, die Checkbox visuell nachziehen
                    if hasattr(self.app, "workspace_tab") and hasattr(self.app.workspace_tab, "var_debuggable"):
                        self.app.workspace_tab.var_debuggable.set(True)

        self.controller.save_config(
            self.var_mode.get(), self.ent_host.get(), self.ent_port.get(), self.ent_path.get()
        )

    def save_config(self):
        self._execute_save()
        messagebox.showinfo("Gespeichert", "Frida-Konfiguration erfolgreich gespeichert!", parent=self)

    def on_close(self):
        if self.has_config_changes():
            answer = messagebox.askyesnocancel(
                "Ungespeicherte Änderungen",
                "Es gibt ungespeicherte Änderungen an der Frida-Konfiguration.\n\n"
                "Möchtest du diese vor dem Schließen speichern?",
                parent=self
            )
            if answer is True:
                self._execute_save()
                self.destroy()
            elif answer is False:
                self.destroy()
            else:
                return
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

    # --- Skript & Editor ---
    def populate_scripts(self):
        for i in self.tree_scripts.get_children(): self.tree_scripts.delete(i)
        for s in self.controller.manager.scripts:
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
            self._apply_js_highlighting()  # Initiales Highlighting triggern

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

    # --- Collections ---
    def populate_collections(self):
        for i in self.tree_cols.get_children(): self.tree_cols.delete(i)
        for c in self.controller.manager.collections:
            names = [self.controller.manager.get_script_by_id(sid).name for sid in c.script_ids if
                     self.controller.manager.get_script_by_id(sid)]
            self.tree_cols.insert("", "end", iid=c.id, values=(c.name, ", ".join(names)))

    def create_collection(self):
        sel = self.tree_scripts.selection()
        if not sel:
            return messagebox.showinfo("Info", "Bitte markiere Skripte in der Liste links (STRG+Klick).", parent=self)
        name = simpledialog.askstring("Sammlung", "Name der Sammlung:", parent=self)
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
        col_id = sel[0]
        if messagebox.askyesno("Löschen", "Sammlung löschen (Skripte bleiben erhalten)?", parent=self):
            self.controller.delete_collection(col_id)
            self.populate_collections()

    def push_selected_collection(self):
        sel = self.tree_cols.selection()
        if not sel: return
        col_id = sel[0]
        self.controller.push_collection_to_device(col_id, self._update_device_list)

    # --- Device Sync ---
    def _update_device_list(self, files: list):
        for i in self.tree_device.get_children(): self.tree_device.delete(i)
        for f in files: self.tree_device.insert("", "end", iid=f, values=(f,))

    def refresh_device_scripts(self):
        self.controller.refresh_device_scripts(self._update_device_list)

    def push_to_device(self):
        code = self.txt_code.get("1.0", tk.END).strip()
        filename = self.ent_name.get().strip().replace(" ", "_").lower()
        if not code or not filename: return
        self.controller.push_to_device(code, filename, self._update_device_list)

    def delete_from_device(self):
        sel = self.tree_device.selection()
        if not sel: return
        filename = sel[0]
        if messagebox.askyesno("Löschen", f"'{filename}' vom Gerät löschen?", parent=self):
            self.controller.delete_from_device(filename, self._update_device_list)