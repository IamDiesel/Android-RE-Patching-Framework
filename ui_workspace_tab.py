import os
import shutil
import datetime
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import threading
import re
import difflib

from ui_smali_studio_tab import SmaliStudioTab
from fuzzy_matcher import FuzzyMatchDialog


class WorkspaceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.patch_rows = []
        self.smali_studio = None
        self.create_widgets()

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
        self.main_paned.add(self.top_paned, weight=3)

        # WICHTIG: tk.Frame (nicht ttk) für abdockbare Bereiche!
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
        self.main_paned.add(f_console_side, weight=1)
        self.build_dock_header(f_console_side, "🖥️ Konsole", f_console_side, self.main_paned, 1)
        self.build_console(f_console_side)

        self.load_current_workspace_meta()

    def build_dock_header(self, parent, title, widget_to_dock, target_paned, weight):
        """Erzeugt einen Header mit Abdock-Button für jedes Panel."""
        f_header = ttk.Frame(parent, style="Secondary.TFrame")
        f_header.pack(side="top", fill="x")

        lbl = ttk.Label(f_header, text=title, font=("Segoe UI", 9, "bold"), background="#e0e0e0", padding=3)
        lbl.pack(side="left", fill="x", expand=True)

        btn = ttk.Button(f_header, text="⧉ Abdocken", width=12)
        btn.pack(side="right", padx=5)
        btn.config(command=lambda: self.toggle_dock(parent, title, widget_to_dock, target_paned, weight, btn))

    def toggle_dock(self, container_frame, title, inner_widget, target_paned, weight, btn):
        """Logik für das native Abdocken und Andocken (Löst das Grau-Fenster-Problem)."""
        if hasattr(container_frame, "_is_undocked") and container_frame._is_undocked:
            # Andocken
            try:
                container_frame.tk.call('wm', 'forget', container_frame._w)
            except:
                pass
            btn.config(text="⧉ Abdocken")
            target_paned.add(container_frame, weight=weight)
            container_frame._is_undocked = False
        else:
            # Abdocken
            target_paned.forget(container_frame)
            try:
                container_frame.tk.call('wm', 'manage', container_frame._w)
                container_frame.tk.call('wm', 'title', container_frame._w, title)
                geom = "1000x700" if "Editor" in title else "600x500"
                container_frame.tk.call('wm', 'geometry', container_frame._w, geom)

                def on_close():
                    self.toggle_dock(container_frame, title, inner_widget, target_paned, weight, btn)

                # Bindet das 'X' oben rechts am neuen Fenster an unsere on_close Logik
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

        self.smali_studio = SmaliStudioTab(patch_book, self.app)

        patch_book.add(tab_hex, text="Hex Patcher (Flutter / C++)")
        patch_book.add(self.smali_studio, text="Smali Studio (Java / Kotlin)")

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

    def build_console(self, parent):
        self.console = tk.Text(parent, height=12, bg="black", fg="lightgreen")
        self.console.pack(side="bottom", fill="both", expand=True, padx=5, pady=5)

        self.console_menu = tk.Menu(parent, tearoff=0)
        self.console_menu.add_command(label="Konsole leeren", command=lambda: self.console.delete("1.0", tk.END))

        def show_menu(e):
            self.console_menu.post(e.x_root, e.y_root)

        self.console.bind("<Button-3>", show_menu)



    def build_dock_header(self, parent, title, widget_to_dock, target_paned, weight):
        """Erzeugt einen Header mit Abdock-Button für jedes Panel."""
        header = ttk.Frame(parent)
        header.pack(side="top", fill="x", pady=2)
        ttk.Label(header, text=title, font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)
        btn = ttk.Button(header, text="⧉ Abdocken", width=12)
        btn.pack(side="right", padx=5)
        btn.config(command=lambda: self.toggle_dock(parent, title, widget_to_dock, target_paned, weight, btn))
        return header


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

        ttk.Button(f_favs, text="Patch Favoriten Manager öffnen", command=self.open_favorites).pack(fill="x", padx=10,
                                                                                                    pady=5)
        ttk.Button(f_favs, text="Aktuellen Stand als Favorit sichern", command=self.save_current_as_favorite).pack(
            fill="x", padx=10, pady=2)

        f_actions = ttk.LabelFrame(parent, text="Pipeline Steuerung")
        f_actions.pack(side="top", fill="x", padx=5, pady=5)

        f_strat = ttk.Frame(f_actions)
        f_strat.pack(fill="x", padx=10, pady=2)
        ttk.Label(f_strat, text="Manifest-Strategie:").pack(side="left", padx=2)
        self.combo_strat = ttk.Combobox(f_strat, values=["smali_only", "apkeditor", "aapt2"], state="readonly",
                                        width=12)
        self.combo_strat.pack(side="left", padx=5)
        self.combo_strat.set(self.app.cfg.config.get("MANIFEST_STRATEGY", "apkeditor"))
        self.combo_strat.bind("<<ComboboxSelected>>", self.on_strategy_changed)

        self.btn_build = ttk.Button(f_actions, text="▶ BUILD_NATIVE ausführen", command=self.run_build)
        self.btn_build.pack(fill="x", padx=10, pady=5)

        self.btn_flash = ttk.Button(f_actions, text="📱 FLASH (Install auf Gerät)", command=self.run_flash)
        self.btn_flash.pack(fill="x", padx=10, pady=5)

        ttk.Separator(f_actions, orient="horizontal").pack(fill="x", pady=5)

        f_trace = ttk.Frame(f_actions)
        f_trace.pack(fill="x", padx=10, pady=2)
        self.btn_trace_start = ttk.Button(f_trace, text="⏱ Trace Start", command=self.start_trace)
        self.btn_trace_start.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.btn_trace_stop = ttk.Button(f_trace, text="🛑 Trace Stop", command=self.stop_trace, state="disabled")
        self.btn_trace_stop.pack(side="left", fill="x", expand=True, padx=(2, 0))

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

        ttk.Button(f_docs, text="💾 Session permanent sichern", command=self.save_result).pack(fill="x", padx=5, pady=2)


    def on_strategy_changed(self, event):
        new_strat = self.combo_strat.get()
        self.app.cfg.config["MANIFEST_STRATEGY"] = new_strat
        self.app.cfg.save()
        self.app.log(f"[*] Manifest-Strategie global auf '{new_strat}' geändert.")

    def open_favorites(self):
        FavoritePatchesDialog(self, self)

    def save_current_as_favorite(self):
        name = simpledialog.askstring("Favorit sichern", "Gib einen Namen für diesen Favoriten ein:")
        if not name: return

        base_dir = self.app.cfg.config.get("BASE_DIR", "")
        fav_file = os.path.join(base_dir, "favorite_patches.json")
        favs = []
        if os.path.exists(fav_file):
            try:
                with open(fav_file, "r", encoding="utf-8") as f:
                    favs = json.load(f)
            except:
                pass

        active_patches = self.smali_studio.smali_patches if self.smali_studio else []
        if not active_patches:
            return messagebox.showwarning("Leer", "Es gibt aktuell keine geladenen Smali Patches zum Sichern.")

        new_fav = {
            "name": name,
            "comment": "Gesichert am " + datetime.datetime.now().strftime('%Y-%m-%d'),
            "date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "patches": active_patches
        }

        favs.append(new_fav)
        with open(fav_file, "w", encoding="utf-8") as f:
            json.dump(favs, f, indent=4)
        messagebox.showinfo("Gesichert", f"Favorit '{name}' mit {len(active_patches)} Patches erfolgreich hinterlegt!")

    def add_patch_row(self):
        row_frame = ttk.Frame(self.p_container)
        row_frame.pack(fill="x", pady=2, anchor="w")

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
        ent_orig = ttk.Entry(row_frame, width=20)
        ent_orig.pack(side="left", padx=2)

        lbl_patch = ttk.Label(row_frame, text="Patch Hex:")
        lbl_patch.pack(side="left", padx=2)
        ent_patch = ttk.Entry(row_frame, width=20)
        ent_patch.pack(side="left", padx=2)

        btn_del = ttk.Button(row_frame, text="X", width=3, command=lambda: self.remove_patch_row(row_frame))
        btn_del.pack(side="left", padx=5)

        self.patch_rows.append({
            "frame": row_frame, "ram": ent_ram, "base": ent_base, "orig": ent_orig, "patch": ent_patch
        })

    def remove_patch_row(self, row_frame):
        for row in list(self.patch_rows):
            if row["frame"] == row_frame:
                row_frame.destroy()
                self.patch_rows.remove(row)
                break

    def get_all_patches(self):
        hex_data = [{"type": "hex", "ram": p["ram"].get(), "base": p["base"].get(), "orig": p["orig"].get(),
                     "patch": p["patch"].get(), "file": "libflutter.so"} for p in self.patch_rows if
                    p["ram"].get().strip()]
        smali_data = self.smali_studio.smali_patches if self.smali_studio else []
        return hex_data + smali_data

    def load_patches_from_record(self, record, append=False):
        if not append:
            for p in list(self.patch_rows): self.remove_patch_row(p["frame"])
            if self.smali_studio: self.smali_studio.smali_patches.clear()

        for pt in record.get("patches", record.get("smali_patches", [])):
            if pt.get("type") == "smali" or "orig" in pt and "edit" in pt and "file" in pt:
                pt["type"] = "smali"
                if self.smali_studio:
                    is_dup = any(
                        ex["file"] == pt["file"] and ex["orig"] == pt["orig"] for ex in self.smali_studio.smali_patches)
                    if not is_dup: self.smali_studio.smali_patches.append(pt)
            else:
                self.add_patch_row()
                last_row = self.patch_rows[-1]
                last_row["ram"].insert(0, pt.get("ram", ""))
                last_row["base"].delete(0, tk.END);
                last_row["base"].insert(0, pt.get("base", "00100000"))
                last_row["orig"].insert(0, pt.get("orig", ""))
                last_row["patch"].insert(0, pt.get("patch", ""))

        if self.smali_studio: self.smali_studio.refresh_smali_tree()
        if "app_version" in record:
            self.ent_version.delete(0, tk.END)
            self.ent_version.insert(0, record.get("app_version", ""))
        self.app.notebook.select(self)

    def run_build(self):
        if self.app.check_lock(): return

        self.app.engine.active_smali_patches = self.smali_studio.smali_patches if self.smali_studio else []

        self.btn_build.config(state="disabled")
        self.app.log("\n=== PIPELINE START: BUILD_NATIVE ===")

        def task():
            success = self.app.engine.run_pipeline("BUILD_NATIVE")
            self.app.after(0, lambda: self.btn_build.config(state="normal"))
            if success:
                self.app.after(0, lambda: messagebox.showinfo("Build", "BUILD_NATIVE erfolgreich abgeschlossen!"))

        threading.Thread(target=task, daemon=True).start()

    def run_flash(self):
        if self.app.check_lock(): return
        self.btn_flash.config(state="disabled")
        self.app.log("\n=== PIPELINE START: FLASH ===")

        def task():
            success = self.app.engine.run_pipeline("FLASH")
            self.app.after(0, lambda: self.btn_flash.config(state="normal"))
            if success:
                dest_dir = self.app.cfg.paths.get("DEST_DIR", "")
                if os.path.exists(dest_dir) and hasattr(self.app, 'current_archive_path'):
                    for f in os.listdir(dest_dir):
                        if f.endswith("-aligned-debugSigned.apk"):
                            shutil.copy(os.path.join(dest_dir, f), self.app.current_archive_path)

        threading.Thread(target=task, daemon=True).start()

    def start_trace(self):
        messagebox.showinfo("Trace", "Bitte starte die App auf dem Gerät und klicke OK.")
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
        messagebox.showinfo("Historie", f"Session {self.app.current_id} erfolgreich gesichert!")


# --- BATCH FAVORITEN MANAGER DIALOG ---

class FavoritePatchesDialog(tk.Toplevel):
    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.title("⭐ Patch Favoriten Manager")
        self.geometry("1100x650")
        self.attributes("-topmost", True)
        self.transient(ws.winfo_toplevel())

        base_dir = self.ws.app.cfg.config.get("BASE_DIR", "")
        self.fav_file = os.path.join(base_dir, "favorite_patches.json")

        self.favs = []
        self.current_sub_patch_idx = 0
        self._debounce_timer = None
        self.load_favs()
        self.create_widgets()

    def load_favs(self):
        if os.path.exists(self.fav_file):
            try:
                with open(self.fav_file, "r", encoding="utf-8") as f:
                    self.favs = json.load(f)
            except:
                pass

    def save_favs(self):
        with open(self.fav_file, "w", encoding="utf-8") as f: json.dump(self.favs, f, indent=4)

    def create_widgets(self):
        f_btn = ttk.Frame(self)
        f_btn.pack(side="bottom", fill="x", padx=10, pady=10)

        ttk.Button(f_btn, text="💾 Speichern", command=self.save_current).pack(side="left", padx=5)
        ttk.Button(f_btn, text="🗑 Löschen", command=self.delete_current).pack(side="left", padx=5)

        ttk.Button(f_btn, text="▶ Alle anwenden (Batch)", command=self.start_batch_fav).pack(side="right", padx=5)
        ttk.Button(f_btn, text="▶ Nur aktuellen anwenden", command=self.start_single_fav).pack(side="right", padx=5)

        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        f_left = ttk.Frame(main_paned)
        main_paned.add(f_left, weight=1)

        self.tree_favs = ttk.Treeview(f_left, columns=("Bezeichnung",), show="headings")
        self.tree_favs.heading("Bezeichnung", text="Bezeichnung")
        self.tree_favs.pack(side="left", fill="both", expand=True)
        self.tree_favs.bind("<<TreeviewSelect>>", self.on_select)

        scrollbar = ttk.Scrollbar(f_left, orient="vertical", command=self.tree_favs.yview)
        self.tree_favs.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        f_right = ttk.LabelFrame(main_paned, text="Details & Edit (Mit Live Diff)")
        main_paned.add(f_right, weight=3)

        f_nav = ttk.Frame(f_right)
        f_nav.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_nav, text="Favorit Name:").pack(side="left", padx=(0, 5))
        self.ent_name = ttk.Entry(f_nav, width=30)
        self.ent_name.pack(side="left", padx=5)

        self.btn_next = ttk.Button(f_nav, text="▶", width=3, command=self.next_sub_patch)
        self.btn_next.pack(side="right", padx=5)
        self.lbl_sub_patch = ttk.Label(f_nav, text="Sub-Patch 1 / 1", font=("Segoe UI", 9, "bold"))
        self.lbl_sub_patch.pack(side="right", padx=10)
        self.btn_prev = ttk.Button(f_nav, text="◀", width=3, command=self.prev_sub_patch)
        self.btn_prev.pack(side="right", padx=5)

        ttk.Label(f_right, text="Ziel-Datei (relativ):").pack(anchor="w", padx=5, pady=(5, 2))
        self.ent_file = ttk.Entry(f_right)
        self.ent_file.pack(fill="x", padx=5)

        split_code = ttk.PanedWindow(f_right, orient=tk.VERTICAL)
        split_code.pack(fill="both", expand=True, padx=5, pady=5)

        f_orig = ttk.Frame(split_code)
        split_code.add(f_orig, weight=1)
        ttk.Label(f_orig, text="Original Code-Block:").pack(anchor="w")
        self.txt_orig = tk.Text(f_orig, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10))
        self.txt_orig.pack(fill="both", expand=True)

        f_edit = ttk.Frame(split_code)
        split_code.add(f_edit, weight=1)
        ttk.Label(f_edit, text="Edit Code-Block:").pack(anchor="w")
        self.txt_edit = tk.Text(f_edit, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10))
        self.txt_edit.pack(fill="both", expand=True)

        # Highlighting Bindings
        self.txt_orig.bind("<KeyRelease>", self._on_text_change)
        self.txt_edit.bind("<KeyRelease>", self._on_text_change)

        self.populate_list()

    # --- SYNTAX HIGHLIGHTING & DIFF ENGINE ---

    def _on_text_change(self, event=None):
        if self._debounce_timer:
            self.after_cancel(self._debounce_timer)
        self._debounce_timer = self.after(300, self._refresh_visuals)

    def _refresh_visuals(self):
        self._apply_diff(self.txt_orig, self.txt_edit)
        self._apply_smali_highlighting(self.txt_orig)
        self._apply_smali_highlighting(self.txt_edit)

    def _apply_diff(self, txt_left, txt_right):
        txt_left.tag_configure("diff_del", background="#4a1919")
        txt_right.tag_configure("diff_add", background="#1a3b1a")

        txt_left.tag_remove("diff_del", "1.0", tk.END)
        txt_right.tag_remove("diff_add", "1.0", tk.END)

        str_left = txt_left.get("1.0", tk.END).splitlines()
        str_right = txt_right.get("1.0", tk.END).splitlines()

        matcher = difflib.SequenceMatcher(None, str_left, str_right)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ('replace', 'delete'):
                for i in range(i1, i2):
                    txt_left.tag_add("diff_del", f"{i + 1}.0", f"{i + 1}.end")
            if tag in ('replace', 'insert'):
                for j in range(j1, j2):
                    txt_right.tag_add("diff_add", f"{j + 1}.0", f"{j + 1}.end")

    def _apply_smali_highlighting(self, txt_widget):
        txt_widget.tag_configure("s_key", foreground="#569CD6")
        txt_widget.tag_configure("s_inst", foreground="#C586C0")
        txt_widget.tag_configure("s_str", foreground="#CE9178")
        txt_widget.tag_configure("s_com", foreground="#6A9955")
        txt_widget.tag_configure("s_reg", foreground="#9CDCFE")

        for t in ["s_key", "s_inst", "s_str", "s_com", "s_reg"]:
            txt_widget.tag_remove(t, "1.0", tk.END)

        text = txt_widget.get("1.0", tk.END)
        for line_idx, line in enumerate(text.split('\n')):
            tk_line = line_idx + 1

            c_match = re.search(r'#.*', line)
            if c_match:
                txt_widget.tag_add("s_com", f"{tk_line}.{c_match.start()}", f"{tk_line}.{c_match.end()}")
                line = line[:c_match.start()]

            for m in re.finditer(r'".*?"', line):
                txt_widget.tag_add("s_str", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            for m in re.finditer(r'\b[vp]\d+\b', line):
                txt_widget.tag_add("s_reg", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            for m in re.finditer(r'(\.[a-zA-Z0-9_-]+)', line):
                txt_widget.tag_add("s_key", f"{tk_line}.{m.start()}", f"{tk_line}.{m.end()}")

            m = re.search(r'^\s*([a-zA-Z0-9_-]+)', line)
            if m and not m.group(1).startswith('.'):
                txt_widget.tag_add("s_inst", f"{tk_line}.{m.start(1)}", f"{tk_line}.{m.end(1)}")

    # --- UI LOGIK ---

    def populate_list(self):
        for i in self.tree_favs.get_children():
            self.tree_favs.delete(i)
        for idx, f in enumerate(self.favs):
            self.tree_favs.insert("", "end", iid=str(idx), values=(f.get("name", "Unnamed"),))

    def get_active_patches(self, fav):
        return fav.get("patches", [fav])

    def on_select(self, event):
        self.current_sub_patch_idx = 0
        self.display_sub_patch()

    def prev_sub_patch(self):
        if self.current_sub_patch_idx > 0:
            self.current_sub_patch_idx -= 1
            self.display_sub_patch()

    def next_sub_patch(self):
        sel = self.tree_favs.selection()
        if not sel: return
        fav = self.favs[int(sel[0])]
        patches = self.get_active_patches(fav)
        if self.current_sub_patch_idx < len(patches) - 1:
            self.current_sub_patch_idx += 1
            self.display_sub_patch()

    def display_sub_patch(self):
        sel = self.tree_favs.selection()
        if not sel: return
        fav = self.favs[int(sel[0])]
        patches = self.get_active_patches(fav)

        p = patches[self.current_sub_patch_idx]
        self.ent_name.delete(0, tk.END);
        self.ent_name.insert(0, fav.get("name", ""))
        self.ent_file.delete(0, tk.END);
        self.ent_file.insert(0, p.get("file", ""))
        self.txt_orig.delete("1.0", tk.END);
        self.txt_orig.insert("1.0", p.get("orig", ""))
        self.txt_edit.delete("1.0", tk.END);
        self.txt_edit.insert("1.0", p.get("edit", ""))

        self.lbl_sub_patch.config(text=f"Sub-Patch {self.current_sub_patch_idx + 1} / {len(patches)}")
        self._refresh_visuals()  # Löst Diff & Highlighting aus!

    def save_current(self):
        sel = self.tree_favs.selection()
        if not sel: return
        idx = int(sel[0])
        fav = self.favs[idx]

        fav["name"] = self.ent_name.get()
        patches = fav.get("patches", None)

        if patches is not None:
            patches[self.current_sub_patch_idx]["file"] = self.ent_file.get()
            patches[self.current_sub_patch_idx]["orig"] = self.txt_orig.get("1.0", tk.END).strip()
            patches[self.current_sub_patch_idx]["edit"] = self.txt_edit.get("1.0", tk.END).strip()
        else:
            new_patch = {
                "type": "smali",
                "file": self.ent_file.get(),
                "orig": self.txt_orig.get("1.0", tk.END).strip(),
                "edit": self.txt_edit.get("1.0", tk.END).strip()
            }
            fav["patches"] = [new_patch]
            fav.pop("file", None);
            fav.pop("orig", None);
            fav.pop("edit", None)

        self.save_favs()
        self.populate_list()
        self.tree_favs.selection_set(str(idx))

    def delete_current(self):
        sel = self.tree_favs.selection()
        if not sel: return
        if messagebox.askyesno("Löschen", "Favorit komplett löschen?", parent=self):
            del self.favs[int(sel[0])]
            self.save_favs()
            self.populate_list()
            self.ent_name.delete(0, tk.END)
            self.ent_file.delete(0, tk.END)
            self.txt_orig.delete("1.0", tk.END)
            self.txt_edit.delete("1.0", tk.END)

    def start_batch_fav(self):
        sel = self.tree_favs.selection()
        if not sel: return
        fav = self.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)

        studio = self.ws.smali_studio
        if not studio: return
        if not studio._ensure_index_loaded(): return

        success_count = 0
        failed_patches = []

        for index, current_patch in enumerate(patches_to_apply):
            file_path = current_patch.get("file", "")
            orig_code = current_patch.get("orig", "").replace("\r\n", "\n")

            found_content = None
            for path, content in studio.search_engine.ram_cache:
                if path == file_path:
                    found_content = content.replace("\r\n", "\n")
                    break

            if found_content and orig_code in found_content:
                is_dup = any(
                    p["file"] == file_path and p["orig"] == current_patch["orig"] for p in studio.smali_patches)
                if not is_dup:
                    studio.smali_patches.append(current_patch.copy())
                    studio.app.log(f"[+] Sub-Patch {index + 1} ({file_path}) erfolgreich.")
                    success_count += 1
            else:
                failed_patches.append((index, current_patch))

        if success_count > 0:
            studio.refresh_smali_tree()

        if not failed_patches:
            messagebox.showinfo("Batch Abgeschlossen", f"Alle {success_count} Patches erfolgreich angewendet!",
                                parent=self)
        else:
            msg = f"{success_count} Patches angewendet.\nEs gab {len(failed_patches)} Konflikte.\nÖffne Lösungsfenster für jeden Konflikt..."
            messagebox.showwarning("Batch Konflikte", msg, parent=self)

            for offset, (idx, fp) in enumerate(failed_patches):
                fuzzer = FuzzyMatchDialog(self, studio.app, studio, fp,
                                          title_suffix=f" (Patch {idx + 1}/{len(patches_to_apply)})")
                x = 50 + (offset * 30)
                y = 50 + (offset * 30)
                fuzzer.geometry(f"1300x750+{x}+{y}")

    def start_single_fav(self):
        sel = self.tree_favs.selection()
        if not sel: return
        fav = self.favs[int(sel[0])]
        patches_to_apply = self.get_active_patches(fav)

        studio = self.ws.smali_studio
        if not studio: return
        if not studio._ensure_index_loaded(): return

        current_patch = patches_to_apply[self.current_sub_patch_idx]
        file_path = current_patch.get("file", "")
        orig_code = current_patch.get("orig", "").replace("\r\n", "\n")

        found_content = None
        for path, content in studio.search_engine.ram_cache:
            if path == file_path:
                found_content = content.replace("\r\n", "\n")
                break

        if found_content and orig_code in found_content:
            is_dup = any(p["file"] == file_path and p["orig"] == current_patch["orig"] for p in studio.smali_patches)
            if not is_dup:
                studio.smali_patches.append(current_patch.copy())
                studio.app.log(f"[+] Sub-Patch {self.current_sub_patch_idx + 1} erfolgreich.")
                studio.refresh_smali_tree()
                messagebox.showinfo("Erfolg",
                                    "Dieser einzelne Sub-Patch wurde erfolgreich zur Patch-Liste hinzugefügt!",
                                    parent=self)
            else:
                messagebox.showinfo("Info", "Dieser einzelne Sub-Patch ist bereits in der Patch-Liste aktiv.",
                                    parent=self)
        else:
            FuzzyMatchDialog(self, studio.app, studio, current_patch,
                             title_suffix=f" (Patch {self.current_sub_patch_idx + 1}/{len(patches_to_apply)})")