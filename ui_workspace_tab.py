import os
import shutil
import datetime
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from ui_smali_studio_tab import SmaliStudioTab


class FavoritePatchesDialog(tk.Toplevel):
    def __init__(self, parent, ws):
        super().__init__(parent)
        self.ws = ws
        self.title("⭐ Patch Favoriten Manager")
        self.geometry("850x550")
        self.attributes("-topmost", True)

        self.fav_file = os.path.join(self.ws.app.cfg.paths.get("BASE_DIR", ""), "favorite_patches.json")
        self.favs = []
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
        with open(self.fav_file, "w", encoding="utf-8") as f:
            json.dump(self.favs, f, indent=4)

    def create_widgets(self):
        # Linkes Panel: Liste der Favoriten
        f_left = ttk.Frame(self)
        f_left.pack(side="left", fill="y", padx=10, pady=10)

        self.listbox = tk.Listbox(f_left, width=35, font=("Segoe UI", 10))
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        ttk.Button(f_left, text="🗑️ Löschen", command=self.delete_selected).pack(fill="x", pady=5)

        # Rechtes Panel: Details und Aktionen
        f_right = ttk.Frame(self)
        f_right.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        self.lbl_name = ttk.Label(f_right, text="Wähle einen Favoriten", font=("Segoe UI", 12, "bold"))
        self.lbl_name.pack(anchor="w")

        ttk.Label(f_right, text="Kommentar / Beschreibung:").pack(anchor="w", pady=(10, 0))
        self.txt_comment = tk.Text(f_right, height=4, font=("Segoe UI", 9))
        self.txt_comment.pack(fill="x")

        ttk.Button(f_right, text="💾 Kommentar speichern", command=self.save_comment).pack(anchor="e", pady=5)

        ttk.Label(f_right, text="Enthaltene Patches:").pack(anchor="w", pady=(10, 0))
        self.tree_patches = ttk.Treeview(f_right, columns=("Type", "File", "Detail"), show="headings", height=8)
        self.tree_patches.heading("Type", text="Typ")
        self.tree_patches.heading("File", text="Datei")
        self.tree_patches.heading("Detail", text="Details")
        self.tree_patches.column("Type", width=60)
        self.tree_patches.column("File", width=200)
        self.tree_patches.pack(fill="both", expand=True)

        # Aktions-Buttons
        f_btns = ttk.Frame(f_right)
        f_btns.pack(fill="x", pady=10)
        ttk.Button(f_btns, text="⬇️ Anfügen (Zu aktuellem Workspace)",
                   command=lambda: self.load_selected(replace=False)).pack(side="left", padx=5)
        ttk.Button(f_btns, text="🔁 Überschreiben (Clear & Load)",
                   command=lambda: self.load_selected(replace=True)).pack(side="left", padx=5)

        self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        for f in self.favs:
            self.listbox.insert(tk.END, f.get("name", "Unbenannt"))

    def on_select(self, event):
        sel = self.listbox.curselection()
        if not sel: return
        fav = self.favs[sel[0]]

        self.lbl_name.config(text=fav.get("name", ""))
        self.txt_comment.delete("1.0", tk.END)
        self.txt_comment.insert("1.0", fav.get("comment", ""))

        for i in self.tree_patches.get_children(): self.tree_patches.delete(i)
        for p in fav.get("patches", []):
            if p.get("type") == "smali":
                self.tree_patches.insert("", "end",
                                         values=("Smali", p.get("file", ""), p.get("edit", "").replace("\n", " ")[:60]))
            else:
                self.tree_patches.insert("", "end", values=(
                "Hex", p.get("file", ""), f"0x{p.get('ram', '')} -> {p.get('patch', '')}"))

    def save_comment(self):
        sel = self.listbox.curselection()
        if not sel: return
        self.favs[sel[0]]["comment"] = self.txt_comment.get("1.0", tk.END).strip()
        self.save_favs()
        messagebox.showinfo("Gespeichert", "Kommentar erfolgreich aktualisiert!")

    def delete_selected(self):
        sel = self.listbox.curselection()
        if not sel: return
        if messagebox.askyesno("Löschen", "Diesen Favorit wirklich endgültig löschen?"):
            del self.favs[sel[0]]
            self.save_favs()
            self.refresh_list()
            for i in self.tree_patches.get_children(): self.tree_patches.delete(i)
            self.txt_comment.delete("1.0", tk.END)
            self.lbl_name.config(text="-")

    def load_selected(self, replace=False):
        sel = self.listbox.curselection()
        if not sel: return
        fav = self.favs[sel[0]]
        self.ws.load_patches_from_record({"patches": fav.get("patches", [])}, append=not replace)
        self.ws.app.log(f"[*] Favorit '{fav.get('name')}' geladen (Append: {not replace}).")
        self.destroy()


class WorkspaceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.patch_rows = []
        self.create_widgets()

    def create_widgets(self):
        # TOOLBAR (Favoriten)
        tb = ttk.Frame(self)
        tb.pack(side="top", fill="x", padx=5, pady=5)
        ttk.Button(tb, text="⭐ Aktuelle Patches als Favorit speichern", command=self.save_current_as_favorite).pack(
            side="right", padx=5)
        ttk.Button(tb, text="📂 Favoriten-Manager öffnen", command=self.open_favorites).pack(side="right", padx=5)

        # MAIN PANED WINDOW (Vertikal: Oben Controls+Editor, Unten Konsole)
        self.main_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # TOP PANED WINDOW (Horizontal: Links Controls, Rechts Editor)
        self.top_paned = ttk.PanedWindow(self.main_paned, orient=tk.HORIZONTAL)
        self.main_paned.add(self.top_paned, weight=2)

        # 1. CONTROLS FRAME (Left) - FIX: Zwingend tk.Frame für das native Abdocken!
        self.f_controls = tk.Frame(self.top_paned)
        self.top_paned.add(self.f_controls, weight=1)
        self.build_controls(self.f_controls)

        # 2. EDITOR FRAME (Right) - FIX: Zwingend tk.Frame für das native Abdocken!
        self.f_editor = tk.Frame(self.top_paned)
        self.top_paned.add(self.f_editor, weight=4)
        self.build_editor(self.f_editor)

        # 3. CONSOLE FRAME (Bottom) - FIX: Zwingend tk.Frame für das native Abdocken!
        self.f_console = tk.Frame(self.main_paned)
        self.main_paned.add(self.f_console, weight=1)
        self.build_console(self.f_console)

    def build_dock_header(self, parent, title, widget_to_dock, target_paned, weight):
        """Erzeugt einen Header mit Abdock-Button für jedes Panel."""
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=2)
        ttk.Label(header, text=title, font=("Segoe UI", 10, "bold")).pack(side="left", padx=5)
        btn = ttk.Button(header, text="⧉ Abdocken", width=12)
        btn.pack(side="right", padx=5)
        btn.config(command=lambda: self.toggle_dock(parent, title, widget_to_dock, target_paned, weight, btn))
        return header

    def toggle_dock(self, container_frame, title, inner_widget, target_paned, weight, btn):
        """Logik für das native Abdocken und Andocken (Löst das Grau-Fenster-Problem)."""
        if hasattr(container_frame, "_is_undocked") and container_frame._is_undocked:
            # Andocken (wm forget verwandelt das Fenster zurück in einen Frame)
            try:
                container_frame.tk.call('wm', 'forget', container_frame._w)
            except:
                pass
            btn.config(text="⧉ Abdocken")
            target_paned.add(container_frame, weight=weight)
            container_frame._is_undocked = False
        else:
            # Abdocken (wm manage macht den Frame zu einem eigenständigen Fenster)
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

    def build_controls(self, parent):
        self.build_dock_header(parent, "⚙️ Steuerung & Metadaten", parent, self.top_paned, 1)

        # Scrollbarer Bereich, damit es auf kleinen Bildschirmen / abgedockt nicht gequetscht wird
        scroll_canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=scroll_canvas.yview)
        scroll_frame = ttk.Frame(scroll_canvas)

        scroll_frame.bind("<Configure>", lambda e: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.create_window((0, 0), window=scroll_frame, anchor="nw", width=scroll_canvas.winfo_width())

        def on_canvas_configure(event):
            scroll_canvas.itemconfig(scroll_canvas.find_withtag("all")[0], width=event.width)

        scroll_canvas.bind("<Configure>", on_canvas_configure)

        scroll_canvas.configure(yscrollcommand=scrollbar.set)
        scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 1. Patch Meta-Daten
        m_frame = ttk.LabelFrame(scroll_frame, text="1. Patch Meta-Daten")
        m_frame.pack(side="top", fill="x", padx=5, pady=5)
        ttk.Label(m_frame, text="Patch-ID:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.lbl_id = ttk.Label(m_frame, text="", font=("Courier", 10, "bold"))
        self.lbl_id.grid(row=0, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(m_frame, text="Manueller Name:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(m_frame, width=25)
        self.ent_name.grid(row=1, column=1, sticky="w", padx=5, pady=2)
        ttk.Label(m_frame, text="App-Version:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.ent_version = ttk.Entry(m_frame, width=15)
        self.ent_version.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        # 4. Resultat
        r_frame = ttk.LabelFrame(scroll_frame, text="4. Resultat")
        r_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        self.combo_res = ttk.Combobox(r_frame, values=["Success", "Crash", "No Internet", "Logic Error"],
                                      state="readonly")
        self.combo_res.current(0)
        self.combo_res.pack(fill="x", padx=5, pady=2)
        self.txt_obs = tk.Text(r_frame, height=3)
        self.txt_obs.pack(fill="x", padx=5, pady=2)
        ttk.Button(r_frame, text="Save Result", command=self.save_result).pack(anchor="e", padx=5, pady=5)

        # 3. Pipelines Ausführen
        a_frame = ttk.LabelFrame(scroll_frame, text="3. Pipelines Ausführen")
        a_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        ttk.Label(a_frame, text="Pipeline:").pack(anchor="w", padx=5, pady=2)
        self.combo_pipe = ttk.Combobox(a_frame, values=["BUILD_FLUTTER", "BUILD_NATIVE"], state="readonly")
        self.combo_pipe.current(0)
        self.combo_pipe.pack(fill="x", padx=5, pady=2)

        btn_f = ttk.Frame(a_frame)
        btn_f.pack(fill="x", padx=5, pady=5)
        ttk.Button(btn_f, text="▶ Build", command=lambda: self.run_pipeline(self.combo_pipe.get())).pack(side="left",
                                                                                                         fill="x",
                                                                                                         expand=True,
                                                                                                         padx=2)
        ttk.Button(btn_f, text="📱 Flash", command=lambda: self.run_pipeline("FLASH")).pack(side="left", fill="x",
                                                                                           expand=True, padx=2)

        t_f = ttk.Frame(a_frame)
        t_f.pack(fill="x", padx=5, pady=5)
        self.btn_trace_start = ttk.Button(t_f, text="Start Trace", command=self.start_trace)
        self.btn_trace_start.pack(side="left", fill="x", expand=True, padx=2)
        self.btn_trace_stop = ttk.Button(t_f, text="Stop Trace", command=self.stop_trace, state="disabled")
        self.btn_trace_stop.pack(side="left", fill="x", expand=True, padx=2)

    def build_editor(self, parent):
        self.build_dock_header(parent, "📝 Patch Editor", parent, self.top_paned, 4)

        patch_book = ttk.Notebook(parent)
        patch_book.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        tab_hex = ttk.Frame(patch_book)
        self.tab_smali = SmaliStudioTab(patch_book, self.app)
        patch_book.add(tab_hex, text="Hex Patcher (Flutter / C++)")
        patch_book.add(self.tab_smali, text="Smali Studio (Java / Kotlin)")

        ttk.Button(tab_hex, text="+ Add Hex Patch", command=self.add_patch_row).pack(anchor="w", padx=5, pady=5)
        self.p_container = ttk.Frame(tab_hex)
        self.p_container.pack(fill="x", padx=5, pady=5)
        self.add_patch_row()

    def build_console(self, parent):
        self.build_dock_header(parent, "🖥️ Konsole", parent, self.main_paned, 1)
        self.console = tk.Text(parent, height=12, bg="black", fg="lightgreen")
        self.console.pack(side="bottom", fill="both", expand=True, padx=5, pady=5)

    def open_favorites(self):
        FavoritePatchesDialog(self, self)

    def save_current_as_favorite(self):
        patches = self.get_all_patches()
        if not patches:
            return messagebox.showwarning("Fehler", "Es gibt aktuell keine Patches zum Speichern!")

        name = simpledialog.askstring("Favorit speichern", "Wie soll dieses Patch-Paket heißen?")
        if not name: return

        comment = simpledialog.askstring("Favorit speichern", "Optionaler Kommentar / Beschreibung für das Paket:")

        fav_file = os.path.join(self.app.cfg.paths.get("BASE_DIR", ""), "favorite_patches.json")
        favs = []
        if os.path.exists(fav_file):
            try:
                with open(fav_file, "r", encoding="utf-8") as f:
                    favs = json.load(f)
            except:
                pass

        favs.append({
            "name": name,
            "comment": comment or "",
            "date": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "patches": patches
        })

        with open(fav_file, "w", encoding="utf-8") as f:
            json.dump(favs, f, indent=4)

        messagebox.showinfo("Erfolg", f"Die {len(patches)} Patches wurden unter '{name}' als Favorit gespeichert!")

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

    def load_patches_from_record(self, record, append=False):
        if not append:
            for p in list(self.patch_rows): self.remove_patch_row(p["frame"])
            self.tab_smali.smali_patches.clear()

        for pt in record.get("patches", []):
            if pt.get("type") == "smali":
                # Verhindere Duplikate beim Appenden
                is_dup = any(
                    ex["file"] == pt["file"] and ex["orig"] == pt["orig"] for ex in self.tab_smali.smali_patches)
                if not is_dup:
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
        if "app_version" in record:
            self.ent_version.delete(0, tk.END)
            self.ent_version.insert(0, record.get("app_version", ""))

        self.app.notebook.select(self.app.workspace_tab)

    def run_pipeline(self, name):
        import threading
        def task():
            if name.startswith("BUILD"):
                self.app.current_archive_path = os.path.join(self.app.cfg.paths["ARCHIVE_DIR"],
                                                             f"{self.app.current_id}_{self.ent_name.get().replace(' ', '_')}")
                os.makedirs(self.app.current_archive_path, exist_ok=True)
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        try:
                            os.remove(os.path.join(dest_dir, f))
                        except:
                            pass

            success = self.app.engine.run_pipeline(name)

            if name.startswith("BUILD") and success:
                dest_dir = self.app.cfg.paths["DEST_DIR"]
                if os.path.exists(dest_dir):
                    for f in os.listdir(dest_dir):
                        if f.endswith("-aligned-debugSigned.apk"):
                            shutil.copy(os.path.join(dest_dir, f), self.app.current_archive_path)

        threading.Thread(target=task, daemon=True).start()

    def start_trace(self):
        from tkinter import messagebox
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