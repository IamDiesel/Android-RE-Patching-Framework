import tkinter as tk
from tkinter import ttk, messagebox

from ui.tabs.smali_studio_tab import SmaliStudioTab
from ui.tabs.launcher_logger_tab import LauncherLoggerTab
from ui.dialogs.favorite_patches_dialog import FavoritePatchesDialog
from core.application.event_bus import EventBus
from core.domain.exceptions import PatchConflictException
from core.application.session_state import SessionState

# Der Controller übernimmt die gesamte Geschäftslogik
from ui.controllers.workspace_controller import WorkspaceController


class WorkspaceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.patch_rows = []
        self.lib_rows = []
        self.smali_studio = None

        # Controller instanziieren
        self.controller = WorkspaceController(self, app)

        self.create_widgets()
        EventBus.subscribe("PIPELINE_CONFLICT_DETECTED", self.handle_patch_conflict)

    def create_widgets(self):
        f_meta = ttk.LabelFrame(self, text="Workspace Meta-Daten")
        f_meta.pack(fill="x", padx=10, pady=5)

        ttk.Label(f_meta, text="Patch-ID (Laufzeit):").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_id = ttk.Label(f_meta, text=self.app.current_id, font=("Segoe UI", 10, "bold"), foreground="green")
        self.lbl_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Button(f_meta, text="🔄 Neue Patch-ID", command=self.renew_id).grid(row=0, column=2, padx=10, pady=2)

        ttk.Label(f_meta, text="App Name / Package:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(f_meta, width=40)
        self.ent_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(f_meta, text="Version:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        self.ent_version = ttk.Entry(f_meta, width=15)
        self.ent_version.grid(row=1, column=3, sticky="w", padx=5, pady=2)

        self.main_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, padx=10, pady=5)

        self.top_paned = ttk.PanedWindow(self.main_paned, orient=tk.HORIZONTAL)
        self.main_paned.add(self.top_paned, weight=2)

        f_left_side = tk.Frame(self.top_paned)
        self.top_paned.add(f_left_side, weight=1)
        self.build_dock_header(f_left_side, "⚙️ Steuerung & Metadaten", f_left_side, self.top_paned, 1)
        self.build_pipeline_controls(f_left_side)
        self.build_documentation_box(f_left_side)

        f_right_side = tk.Frame(self.top_paned)
        self.top_paned.add(f_right_side, weight=3)
        self.build_dock_header(f_right_side, "📝 Patch Editor", f_right_side, self.top_paned, 3)
        self.build_editor(f_right_side)

        f_console_side = tk.Frame(self.main_paned)
        self.main_paned.add(f_console_side, weight=2)
        self.build_dock_header(f_console_side, "🖥️ Konsole & Live-Logging", f_console_side, self.main_paned, 2)
        self.build_console(f_console_side)

        self.load_current_workspace_meta()

    def build_dock_header(self, parent, title, widget_to_dock, target_paned, weight):
        f_header = ttk.Frame(parent, style="Secondary.TFrame")
        f_header.pack(side="top", fill="x")

        lbl = ttk.Label(f_header, text=title, font=("Segoe UI", 9, "bold"), background="#e0e0e0", padding=3)
        lbl.pack(side="left", fill="x", expand=True)

        btn = ttk.Button(f_header, text="⧉ Abdocken", width=12)
        btn.pack(side="right", padx=5)
        btn.config(command=lambda: self.toggle_dock(parent, title, widget_to_dock, target_paned, weight, btn))

    def toggle_dock(self, container_frame, title, inner_widget, target_paned, weight, btn):
        if hasattr(container_frame, "_is_undocked") and container_frame._is_undocked:
            try:
                container_frame.tk.call('wm', 'forget', container_frame._w)
            except:
                pass
            btn.config(text="⧉ Abdocken")
            target_paned.add(container_frame, weight=weight)
            container_frame._is_undocked = False
        else:
            target_paned.forget(container_frame)
            try:
                container_frame.tk.call('wm', 'manage', container_frame._w)
                container_frame.tk.call('wm', 'title', container_frame._w, title)
                geom = "1000x700" if "Editor" in title else "600x500"
                container_frame.tk.call('wm', 'geometry', container_frame._w, geom)

                def on_close():
                    self.toggle_dock(container_frame, title, inner_widget, target_paned, weight, btn)

                cb_name = container_frame.register(on_close)
                container_frame.tk.call('wm', 'protocol', container_frame._w, 'WM_DELETE_WINDOW', cb_name)

                container_frame._is_undocked = True
                btn.config(text="⬎ Andocken")
            except Exception as e:
                self.app.log(f"[!] System unterstützt kein natives Abdocken: {e}")
                target_paned.add(container_frame, weight=weight)

    def build_editor(self, parent):
        patch_book = ttk.Notebook(parent)
        patch_book.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        tab_hex = ttk.Frame(patch_book)
        tab_libs = ttk.Frame(patch_book)
        self.smali_studio = SmaliStudioTab(patch_book, self.app)

        patch_book.add(tab_hex, text="Hex Patcher (Flutter / C++)")
        patch_book.add(tab_libs, text="Native Lib Replacer")
        patch_book.add(self.smali_studio, text="Smali Studio (Java / Kotlin)")

        # --- HEX PATCHER UI ---
        ttk.Button(tab_hex, text="+ Add Hex Patch", command=self.add_patch_row).pack(anchor="w", padx=5, pady=5)
        canvas = tk.Canvas(tab_hex, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab_hex, orient="vertical", command=canvas.yview)
        self.p_container = ttk.Frame(canvas)
        self.p_container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.p_container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.add_patch_row()

        # --- NATIVE LIB REPLACER UI ---
        ttk.Button(tab_libs, text="+ Add Lib Replacement", command=self.add_lib_row).pack(anchor="w", padx=5, pady=5)
        canvas_libs = tk.Canvas(tab_libs, borderwidth=0, highlightthickness=0)
        scrollbar_libs = ttk.Scrollbar(tab_libs, orient="vertical", command=canvas_libs.yview)
        self.l_container = ttk.Frame(canvas_libs)
        self.l_container.bind("<Configure>", lambda e: canvas_libs.configure(scrollregion=canvas_libs.bbox("all")))
        canvas_libs.create_window((0, 0), window=self.l_container, anchor="nw")
        canvas_libs.configure(yscrollcommand=scrollbar_libs.set)
        scrollbar_libs.pack(side="right", fill="y")
        canvas_libs.pack(side="left", fill="both", expand=True)

    def add_patch_row(self):
        row_frame = ttk.Frame(self.p_container)
        row_frame.pack(fill="x", pady=2, anchor="w")

        lbl_file = ttk.Label(row_frame, text="Lib:")
        lbl_file.pack(side="left", padx=2)
        ent_file = ttk.Entry(row_frame, width=16)
        ent_file.insert(0, "libflutter.so")
        ent_file.pack(side="left", padx=2)

        lbl_ram = ttk.Label(row_frame, text="RAM:")
        lbl_ram.pack(side="left", padx=2)
        ent_ram = ttk.Entry(row_frame, width=12)
        ent_ram.pack(side="left", padx=2)

        lbl_base = ttk.Label(row_frame, text="Base:")
        lbl_base.pack(side="left", padx=2)
        ent_base = ttk.Entry(row_frame, width=10)
        ent_base.insert(0, "00100000")
        ent_base.pack(side="left", padx=2)

        lbl_orig = ttk.Label(row_frame, text="Orig Hex:")
        lbl_orig.pack(side="left", padx=2)
        ent_orig = ttk.Entry(row_frame, width=18)
        ent_orig.pack(side="left", padx=2)

        lbl_patch = ttk.Label(row_frame, text="Patch Hex:")
        lbl_patch.pack(side="left", padx=2)
        ent_patch = ttk.Entry(row_frame, width=18)
        ent_patch.pack(side="left", padx=2)

        btn_del = ttk.Button(row_frame, text="X", width=3, command=lambda: self.remove_patch_row(row_frame))
        btn_del.pack(side="left", padx=5)

        self.patch_rows.append(
            {"frame": row_frame, "file": ent_file, "ram": ent_ram, "base": ent_base, "orig": ent_orig, "patch": ent_patch}
        )

    def remove_patch_row(self, row_frame):
        for row in list(self.patch_rows):
            if row["frame"] == row_frame:
                row_frame.destroy()
                self.patch_rows.remove(row)
                break

    def add_lib_row(self):
        row_frame = ttk.Frame(self.l_container)
        row_frame.pack(fill="x", pady=2, anchor="w")

        ttk.Label(row_frame, text="Ziel-Lib in APK:").pack(side="left", padx=2)
        ent_target = ttk.Entry(row_frame, width=25)
        ent_target.pack(side="left", padx=2)

        ttk.Label(row_frame, text="Ersatz-Datei (Lokaler Pfad):").pack(side="left", padx=(10, 2))
        ent_source = ttk.Entry(row_frame, width=45)
        ent_source.pack(side="left", padx=2)

        btn_browse = ttk.Button(row_frame, text="📁", width=3, command=lambda: self.browse_lib(ent_target, ent_source))
        btn_browse.pack(side="left", padx=2)

        btn_del = ttk.Button(row_frame, text="X", width=3, command=lambda: self.remove_lib_row(row_frame))
        btn_del.pack(side="left", padx=10)

        self.lib_rows.append({"frame": row_frame, "target": ent_target, "source": ent_source})

    def browse_lib(self, entry_target, entry_source):
        from tkinter import filedialog
        import os
        tools_dir = os.path.join(self.app.cfg.config.get("BASE_DIR", ""), "tools")
        init_dir = tools_dir if os.path.exists(tools_dir) else None

        path = filedialog.askopenfilename(initialdir=init_dir, title="Lokale Ersatz-Bibliothek (.so) auswählen",
                                          filetypes=[("Shared Objects", "*.so"), ("All Files", "*.*")])
        if path:
            entry_source.delete(0, tk.END)
            entry_source.insert(0, path)
            import os
            entry_target.delete(0, tk.END)
            entry_target.insert(0, os.path.basename(path))

    def remove_lib_row(self, row_frame):
        for row in list(self.lib_rows):
            if row["frame"] == row_frame:
                row_frame.destroy()
                self.lib_rows.remove(row)
                break

    def build_console(self, parent):
        self.console_notebook = ttk.Notebook(parent)
        self.console_notebook.pack(fill="both", expand=True, padx=5, pady=5)

        f_main_console = ttk.Frame(self.console_notebook)
        self.console_notebook.add(f_main_console, text="🖥️ Main Console")

        self.console = tk.Text(f_main_console, height=12, bg="black", fg="lightgreen")
        self.console.pack(side="bottom", fill="both", expand=True, padx=5, pady=5)

        self.console_menu = tk.Menu(f_main_console, tearoff=0)
        self.console_menu.add_command(label="Konsole leeren", command=lambda: self.console.delete("1.0", tk.END))

        self.console.bind("<Button-3>", lambda e: self.console_menu.post(e.x_root, e.y_root))

        self.launcher_logger_tab = LauncherLoggerTab(self.console_notebook, self)
        self.console_notebook.add(self.launcher_logger_tab, text="🚀 App Start & Live-Log")

    def renew_id(self):
        if messagebox.askyesno("Neue ID",
                               "Möchtest du eine neue Patch-ID generieren?\nAktuelle ungespeicherte UI-Einträge werden zurückgesetzt."):
            self.app.generate_new_id()
            self.ent_name.delete(0, tk.END)
            self.ent_version.delete(0, tk.END)
            self.txt_obs.delete("1.0", tk.END)
            self.combo_res.set("NOT_TESTED")
            self.load_current_workspace_meta()

    def load_current_workspace_meta(self):
        pkg = self.app.cfg.config.get("APP_PACKAGE", "")
        self.ent_name.delete(0, tk.END)
        self.ent_name.insert(0, pkg)
        self.ent_version.delete(0, tk.END)
        self.ent_version.insert(0, "1.0.0")

    def build_pipeline_controls(self, parent):
        f_favs = ttk.LabelFrame(parent, text="⭐ Patch Verwaltung & Favoriten")
        f_favs.pack(side="top", fill="x", padx=5, pady=5)

        ttk.Button(f_favs, text="Patch Favoriten Manager öffnen", command=self.open_favorites).pack(fill="x", padx=10, pady=5)
        ttk.Button(f_favs, text="Aktuellen Stand als Favorit sichern",
                   command=self.controller.save_current_as_favorite).pack(fill="x", padx=10, pady=2)

        f_actions = ttk.LabelFrame(parent, text="Pipeline Steuerung")
        f_actions.pack(side="top", fill="x", padx=5, pady=5)

        f_strat = ttk.Frame(f_actions)
        f_strat.pack(fill="x", padx=10, pady=2)
        ttk.Label(f_strat, text="Manifest-Strategie:").pack(side="left", padx=2)
        self.combo_strat = ttk.Combobox(f_strat, values=["smali_only", "apkeditor", "aapt2"], state="readonly", width=12)
        self.combo_strat.pack(side="left", padx=5)
        self.combo_strat.set(self.app.cfg.config.get("MANIFEST_STRATEGY", "apkeditor"))
        self.combo_strat.bind("<<ComboboxSelected>>", lambda e: self.controller.change_manifest_strategy(self.combo_strat.get()))

        f_native = ttk.Frame(f_actions)
        f_native.pack(fill="x", padx=10, pady=2)
        ttk.Label(f_native, text="Memory Alignment:").pack(side="left", padx=2)
        self.combo_native_lib = ttk.Combobox(f_native, values=["zipalign", "extractNativeLibs"], state="readonly", width=16)
        self.combo_native_lib.pack(side="left", padx=5)
        self.combo_native_lib.set(self.app.cfg.config.get("NATIVE_LIB_STRATEGY", "zipalign"))
        self.combo_native_lib.bind("<<ComboboxSelected>>", lambda e: self.controller.change_native_lib_strategy(self.combo_native_lib.get()))

        f_inject = ttk.Frame(f_actions)
        f_inject.pack(fill="x", padx=10, pady=2)

        self.var_frida = tk.BooleanVar(value=self.app.cfg.config.get("INJECT_FRIDA", False))
        chk_frida = ttk.Checkbutton(f_inject, text="Frida Gadget", variable=self.var_frida,
                                    command=lambda: self.controller.toggle_frida(self.var_frida.get()))
        chk_frida.pack(side="left", padx=2)

        btn_frida_mgr = ttk.Button(f_inject, text="🦊 Skripte", command=self.controller.open_frida_manager)
        btn_frida_mgr.pack(side="left", padx=(0, 10))

        self.var_lspatch = tk.BooleanVar(value=self.app.cfg.config.get("INJECT_LSPATCH", False))
        chk_lspatch = ttk.Checkbutton(f_inject, text="LSPatch (Xposed)", variable=self.var_lspatch,
                                      command=lambda: self.controller.toggle_lspatch(self.var_lspatch.get()))
        chk_lspatch.pack(side="left", padx=2)

        self.btn_build = ttk.Button(f_actions, text="▶ BUILD_NATIVE ausführen", command=self.controller.run_build)
        self.btn_build.pack(fill="x", padx=10, pady=5)

        self.btn_flash = ttk.Button(f_actions, text="📱 FLASH (Install auf Gerät)", command=self.controller.run_flash)
        self.btn_flash.pack(fill="x", padx=10, pady=5)

        ttk.Separator(f_actions, orient="horizontal").pack(fill="x", pady=5)

        f_trace = ttk.Frame(f_actions)
        f_trace.pack(fill="x", padx=10, pady=2)

        self.btn_launcher_logger = ttk.Button(f_trace, text="🚀 App Launcher & Logger", command=self.open_launcher_logger)
        self.btn_launcher_logger.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_frida_attach = ttk.Button(f_trace, text="🦊 Frida Zünden", command=self.controller.attach_frida)
        self.btn_frida_attach.pack(side="left", fill="x", expand=True, padx=(2, 0))

        f_rasp = ttk.Frame(f_actions)
        f_rasp.pack(fill="x", padx=10, pady=(2, 5))

        self.btn_push_clean = ttk.Button(f_rasp, text="📥 Push Clean APK (RASP Spoofer)",
                                         command=self.controller.push_clean_apk)
        self.btn_push_clean.pack(fill="x", expand=True)

    def build_documentation_box(self, parent):
        f_docs = ttk.LabelFrame(parent, text="Beobachtungen & Analyse-Ergebnis")
        f_docs.pack(side="bottom", fill="both", expand=True, padx=5, pady=5)

        ttk.Label(f_docs, text="Ergebnis-Status:").pack(anchor="w", padx=5, pady=2)
        self.combo_res = ttk.Combobox(f_docs, values=["NOT_TESTED", "WORKING", "WORKING_PARTIAL", "CRASH", "NO_CHANGE",
                                                      "NEEDS_FRIDA", "ERROR"], state="readonly")
        self.combo_res.pack(fill="x", padx=5, pady=2)
        self.combo_res.set("NOT_TESTED")

        ttk.Label(f_docs, text="Analyse-Notizen / Dokumentation:").pack(anchor="w", padx=5, pady=2)
        self.txt_obs = tk.Text(f_docs, height=6, font=("Segoe UI", 9))
        self.txt_obs.pack(fill="both", expand=True, padx=5, pady=5)

        ttk.Button(f_docs, text="💾 Session permanent sichern", command=self._on_save_result).pack(fill="x", padx=5, pady=2)

    def _on_save_result(self):
        self.controller.save_session_result(
            self.ent_name.get(),
            self.ent_version.get(),
            self.combo_res.get(),
            self.txt_obs.get("1.0", tk.END).strip(),
            self.get_all_patches()
        )

    def open_launcher_logger(self):
        self.console_notebook.select(self.launcher_logger_tab)

    def open_favorites(self):
        FavoritePatchesDialog(self, self)

    def sync_ui_to_state(self):
        SessionState.active_hex_patches = [
            {"type": "hex", "file": p["file"].get().strip(), "ram": p["ram"].get().strip(),
             "base": p["base"].get().strip(), "orig": p["orig"].get().strip(), "patch": p["patch"].get().strip()}
            for p in self.patch_rows if p["ram"].get().strip() and p["file"].get().strip()
        ]

        SessionState.active_lib_replacements = [
            {"type": "lib_replace", "target": p["target"].get().strip(), "source": p["source"].get().strip()}
            for p in self.lib_rows if p["target"].get().strip() and p["source"].get().strip()
        ]

    def get_all_patches(self):
        self.sync_ui_to_state()
        return SessionState.get_all_patches()

    def load_patches_from_record(self, record, append=False):
        if not append:
            for p in list(self.patch_rows): self.remove_patch_row(p["frame"])
            for p in list(self.lib_rows): self.remove_lib_row(p["frame"])
            if self.smali_studio: self.smali_studio.smali_patches.clear()

        for pt in record.get("patches", record.get("smali_patches", [])):
            if pt.get("type") == "smali" or "orig" in pt and "edit" in pt and "file" in pt:
                pt["type"] = "smali"
                if self.smali_studio:
                    is_dup = any(
                        ex["file"] == pt["file"] and ex["orig"] == pt["orig"] for ex in self.smali_studio.smali_patches)
                    if not is_dup: self.smali_studio.smali_patches.append(pt)

            elif pt.get("type") == "lib_replace":
                self.add_lib_row()
                last_row = self.lib_rows[-1]
                last_row["target"].insert(0, pt.get("target", ""))
                last_row["source"].insert(0, pt.get("source", ""))

            else:
                self.add_patch_row()
                last_row = self.patch_rows[-1]
                last_row["file"].delete(0, tk.END)
                last_row["file"].insert(0, pt.get("file", "libflutter.so"))
                last_row["ram"].insert(0, pt.get("ram", ""))
                last_row["base"].delete(0, tk.END)
                last_row["base"].insert(0, pt.get("base", "00100000"))
                last_row["orig"].insert(0, pt.get("orig", ""))
                last_row["patch"].insert(0, pt.get("patch", ""))

        if self.smali_studio: self.smali_studio.refresh_smali_tree()
        if "app_version" in record:
            self.ent_version.delete(0, tk.END)
            self.ent_version.insert(0, record.get("app_version", ""))
        self.app.notebook.select(self)

    def handle_patch_conflict(self, pce: PatchConflictException):
        def _show_dialog():
            msg = f"Patch {pce.patch_index + 1} weicht von der Datei ab!\n\nDatei: {pce.file_path}\nMethode: {pce.method_sig}\n\nDie Decompiler-Formatierung (.line-Nummern, Kommentare) unterscheidet sich.\n\nMöchtest du in den manuellen Bearbeitungsmodus wechseln?\n"
            if messagebox.askyesno("Patch Abweichung erkannt", msg):
                self.app.log(f"[*] Öffne Konflikt in Smali Studio für Patch {pce.patch_index + 1}...")
            else:
                self.app.log(f"[!] Konfliktlösung durch Nutzer abgebrochen.")

        self.app.after(0, _show_dialog)