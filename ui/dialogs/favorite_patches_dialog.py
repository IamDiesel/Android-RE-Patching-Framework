import tkinter as tk
from tkinter import ttk
import difflib
import re
import threading

from services.favorite_service import FavoriteService, get_shared_favorites
from ui.controllers.favorite_patches_controller import FavoritePatchesController


TOOL_LABELS = {
    "smali": "Smali",
    "new_file": "Smali",
    "libcarve": "LibCarve",
    "libforge": "LibForge",
    "hex": "Hex",
    "lib_replace": "Lib-Replace",
}

FILTER_OPTIONS = [
    "Alle",
    "Smali",
    "LibCarve",
    "LibForge",
    "Hex",
    "Lib-Replace",
    "Kombi (Multi-Tool)",
]


def format_fav_tools(fav: dict):
    """Liefert (Badge-Text, Tool-Label-Set, Sub-Patch-Anzahl) fuer einen Favoriten."""
    patches = fav.get("patches") if isinstance(fav, dict) and isinstance(fav.get("patches"), list) else [fav]
    ordered = []
    seen = set()
    for p in patches:
        if not isinstance(p, dict):
            continue
        pt = str(p.get("type", "smali") or "smali")
        lbl = TOOL_LABELS.get(pt, pt)
        if lbl not in seen:
            seen.add(lbl)
            ordered.append(lbl)
    return (" + ".join(ordered) if ordered else "Smali"), seen, len(patches)


def choose_existing_favorite_dialog(parent, fav_service, title: str = "Zu bestehendem Favoriten hinzufügen"):
    """Modaler Auswahl-Dialog fuer einen bestehenden Favoriten. Gibt den Listen-Index oder None zurueck."""
    favs = getattr(fav_service, "favs", []) or []
    if not favs:
        return None
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.geometry("520x360")
    dlg.transient(parent)
    dlg.grab_set()
    result = {"idx": None}

    ttk.Label(dlg, text="Ziel-Favorit auswählen:", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(10, 4))
    f_lb = ttk.Frame(dlg)
    f_lb.pack(fill="both", expand=True, padx=10, pady=4)
    lb = tk.Listbox(f_lb, exportselection=False, font=("Segoe UI", 9))
    sb = ttk.Scrollbar(f_lb, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    idx_map = []
    for idx, f in reversed(list(enumerate(favs))):
        badge, _s, cnt = format_fav_tools(f)
        lb.insert(tk.END, f"[{idx}] {f.get('name', 'Unbenannt')}  —  {badge} ({cnt} Patch(es))")
        idx_map.append(idx)
    if idx_map:
        lb.selection_set(0)

    def _ok(_evt=None):
        sel = lb.curselection()
        if sel:
            result["idx"] = idx_map[int(sel[0])]
        dlg.destroy()

    def _cancel():
        dlg.destroy()

    lb.bind("<Double-1>", _ok)
    f_b = ttk.Frame(dlg)
    f_b.pack(fill="x", padx=10, pady=8)
    ttk.Button(f_b, text="Abbrechen", command=_cancel).pack(side="right", padx=4)
    ttk.Button(f_b, text="➕ Hinzufügen", command=_ok).pack(side="right", padx=4)
    dlg.wait_window()
    return result["idx"]


class FavoritePatchesDialog(tk.Toplevel):
    def __init__(self, parent, ws, initial_filter: str = "Alle"):
        super().__init__(parent)
        self.ws = ws
        self.title("⭐ Patch Favoriten Manager")
        self.geometry("1180x680")
        self.attributes("-topmost", True)
        self.transient(ws.winfo_toplevel())

        fav_service = get_shared_favorites(self.ws.app)
        self.controller = FavoritePatchesController(self, ws, fav_service)

        self.current_sub_patch_idx = 0
        self._debounce_timer = None
        self._initial_filter = initial_filter if initial_filter in FILTER_OPTIONS else "Alle"
        self.create_widgets()

    def create_widgets(self):
        f_btn = ttk.Frame(self)
        f_btn.pack(side="bottom", fill="x", padx=10, pady=10)

        ttk.Button(f_btn, text="💾 Speichern", command=self.controller.save_current).pack(side="left", padx=4)
        ttk.Button(f_btn, text="🗑 Löschen", command=self.controller.delete_current).pack(side="left", padx=4)
        ttk.Button(f_btn, text="🔄 Aktualisieren", command=self.reload_favs).pack(side="left", padx=4)

        mb_add = ttk.Menubutton(f_btn, text="➕ Hinzufügen ▾")
        m_add = tk.Menu(mb_add, tearoff=0)
        m_add.add_command(label="Aktuellen Smali-Editor anhängen",
                          command=lambda: self.controller.append_from_tool("smali"))
        m_add.add_command(label="Aktuellen LibCarve-Patch anhängen",
                          command=lambda: self.controller.append_from_tool("libcarve"))
        m_add.add_command(label="Ausgewählte LibForge-Lib anhängen",
                          command=lambda: self.controller.append_from_tool("libforge"))
        m_add.add_separator()
        m_add.add_command(label="Alle aktiven Workspace-Patches anhängen",
                          command=lambda: self.controller.append_from_tool("workspace"))
        mb_add.config(menu=m_add)
        mb_add.pack(side="left", padx=4)

        ttk.Button(f_btn, text="🔗 Auswahl kombinieren",
                   command=self.controller.combine_selected_favorites).pack(side="left", padx=4)

        ttk.Button(f_btn, text="▶ Alle anwenden (Batch)", command=self.controller.start_batch_fav).pack(side="right",
                                                                                                        padx=5)
        ttk.Button(f_btn, text="▶ Nur aktuellen anwenden", command=self.controller.start_single_fav).pack(side="right",
                                                                                                          padx=5)

        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        f_left = ttk.Frame(main_paned)
        main_paned.add(f_left, weight=2)

        f_flt = ttk.Frame(f_left)
        f_flt.pack(side="top", fill="x", pady=(0, 5))
        ttk.Label(f_flt, text="Tool:").pack(side="left", padx=(0, 3))
        self.cmb_filter = ttk.Combobox(f_flt, values=FILTER_OPTIONS, width=18, state="readonly")
        self.cmb_filter.set(self._initial_filter)
        self.cmb_filter.pack(side="left", padx=(0, 6))
        self.cmb_filter.bind("<<ComboboxSelected>>", lambda _e: self.populate_list())
        ttk.Label(f_flt, text="Suche:").pack(side="left", padx=(0, 3))
        self.ent_search = ttk.Entry(f_flt, width=16)
        self.ent_search.pack(side="left", fill="x", expand=True)
        self.ent_search.bind("<KeyRelease>", lambda _e: self.populate_list())

        f_tree = ttk.Frame(f_left)
        f_tree.pack(side="top", fill="both", expand=True)
        self.tree_favs = ttk.Treeview(
            f_tree, columns=("Bezeichnung", "Tools", "Anzahl"), show="headings", selectmode="extended"
        )
        self.tree_favs.heading("Bezeichnung", text="Bezeichnung")
        self.tree_favs.heading("Tools", text="Tools")
        self.tree_favs.heading("Anzahl", text="#")
        self.tree_favs.column("Bezeichnung", width=165, stretch=True)
        self.tree_favs.column("Tools", width=120, stretch=True)
        self.tree_favs.column("Anzahl", width=36, anchor="center", stretch=False)
        self.tree_favs.pack(side="left", fill="both", expand=True)
        self.tree_favs.bind("<<TreeviewSelect>>", self.on_select)

        scrollbar = ttk.Scrollbar(f_tree, orient="vertical", command=self.tree_favs.yview)
        self.tree_favs.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        f_right = ttk.LabelFrame(main_paned, text="Details & Edit (Mit Live Diff)")
        main_paned.add(f_right, weight=3)

        f_nav = ttk.Frame(f_right)
        f_nav.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_nav, text="Favorit Name:").pack(side="left", padx=(0, 5))
        self.ent_name = ttk.Entry(f_nav, width=28)
        self.ent_name.pack(side="left", padx=5)

        ttk.Button(f_nav, text="➖ Sub-Patch", command=self.controller.remove_current_sub_patch).pack(side="right", padx=4)
        self.btn_next = ttk.Button(f_nav, text="▶", width=3, command=self.next_sub_patch)
        self.btn_next.pack(side="right", padx=5)
        self.lbl_sub_patch = ttk.Label(f_nav, text="Sub-Patch 1 / 1", font=("Segoe UI", 9, "bold"))
        self.lbl_sub_patch.pack(side="right", padx=10)
        self.btn_prev = ttk.Button(f_nav, text="◀", width=3, command=self.prev_sub_patch)
        self.btn_prev.pack(side="right", padx=5)

        ttk.Label(f_right, text="Ziel-Datei / Attribut:").pack(anchor="w", padx=5, pady=(5, 2))
        self.ent_file = ttk.Entry(f_right)
        self.ent_file.pack(fill="x", padx=5)

        split_code = ttk.PanedWindow(f_right, orient=tk.VERTICAL)
        split_code.pack(fill="both", expand=True, padx=5, pady=5)

        f_orig = ttk.Frame(split_code)
        split_code.add(f_orig, weight=1)
        ttk.Label(f_orig, text="Original / Source Pfad:").pack(anchor="w")
        self.txt_orig = tk.Text(f_orig, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10))
        self.txt_orig.pack(fill="both", expand=True)

        f_edit = ttk.Frame(split_code)
        split_code.add(f_edit, weight=1)
        ttk.Label(f_edit, text="Editierter Code:").pack(anchor="w")
        self.txt_edit = tk.Text(f_edit, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10))
        self.txt_edit.pack(fill="both", expand=True)

        self.txt_orig.bind("<KeyRelease>", self._on_text_change)
        self.txt_edit.bind("<KeyRelease>", self._on_text_change)

        self.populate_list()

    def _on_text_change(self, event=None):
        if self._debounce_timer:
            self.after_cancel(self._debounce_timer)
        self._debounce_timer = self.after(300, self._refresh_visuals)

    def _refresh_visuals(self):
        self._apply_diff(self.txt_orig, self.txt_edit)
        self._apply_smali_highlighting(self.txt_orig)
        self._apply_smali_highlighting(self.txt_edit)

    def _apply_diff(self, txt_left, txt_right):
        # 1. Tags zurücksetzen (schnell, im Main-Thread)
        txt_left.tag_configure("diff_del", background="#4a1919")
        txt_right.tag_configure("diff_add", background="#1a3b1a")
        txt_left.tag_remove("diff_del", "1.0", tk.END)
        txt_right.tag_remove("diff_add", "1.0", tk.END)

        # 2. Textinhalte auslesen (muss im GUI-Thread passieren)
        str_left = txt_left.get("1.0", tk.END).splitlines()
        str_right = txt_right.get("1.0", tk.END).splitlines()

        # 3. Schwere Arbeit in den Hintergrund-Thread auslagern
        def diff_worker():
            matcher = difflib.SequenceMatcher(None, str_left, str_right)
            opcodes = matcher.get_opcodes()
            # 4. GUI-Update sauber an den Main-Thread zurückgeben
            self.after(0, lambda: self._apply_diff_tags(txt_left, txt_right, opcodes))

        threading.Thread(target=diff_worker, daemon=True).start()

    def _apply_diff_tags(self, txt_left, txt_right, opcodes):
        # Sicherheitscheck, falls der Dialog während der Berechnung geschlossen wurde
        if not txt_left.winfo_exists() or not txt_right.winfo_exists(): return

        for tag, i1, i2, j1, j2 in opcodes:
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

        for t in ["s_key", "s_inst", "s_str", "s_com", "s_reg"]: txt_widget.tag_remove(t, "1.0", tk.END)

        text = txt_widget.get("1.0", tk.END)
        for line_idx, line in enumerate(text.split('\n')):
            tk_line = line_idx + 1
            c_match = re.search(r'#.*', line)
            if c_match:
                txt_widget.tag_add("s_com", f"{tk_line}.{c_match.start()}", f"{tk_line}.{c_match.end()}")
                line = line[:c_match.start()]
            for m in re.finditer(r'".*?"', line): txt_widget.tag_add("s_str", f"{tk_line}.{m.start()}",
                                                                     f"{tk_line}.{m.end()}")
            for m in re.finditer(r'\b[vp]\d+\b', line): txt_widget.tag_add("s_reg", f"{tk_line}.{m.start()}",
                                                                           f"{tk_line}.{m.end()}")
            for m in re.finditer(r'(\.[a-zA-Z0-9_-]+)', line): txt_widget.tag_add("s_key", f"{tk_line}.{m.start()}",
                                                                                  f"{tk_line}.{m.end()}")
            m = re.search(r'^\s*([a-zA-Z0-9_-]+)', line)
            if m and not m.group(1).startswith('.'): txt_widget.tag_add("s_inst", f"{tk_line}.{m.start(1)}",
                                                                        f"{tk_line}.{m.end(1)}")

    def reload_favs(self):
        """Favoriten neu von der Platte laden (externe/MCP-Aenderungen uebernehmen)."""
        changed = self.controller.fav_service.reload_if_changed()
        self.populate_list()
        try:
            self.ws.app.log("[Favoriten] aktualisiert"
                            + (" — externe Aenderungen uebernommen." if changed else " (bereits aktuell)."))
        except Exception:
            pass

    def populate_list(self):
        for i in self.tree_favs.get_children():
            self.tree_favs.delete(i)

        flt = self.cmb_filter.get() if hasattr(self, "cmb_filter") else "Alle"
        query = self.ent_search.get().strip().lower() if hasattr(self, "ent_search") else ""

        # Rückwärts iterieren, originalen Index (idx) als iid beibehalten
        for idx, f in reversed(list(enumerate(self.controller.fav_service.favs))):
            badge, tool_set, cnt = format_fav_tools(f)
            if flt == "Kombi (Multi-Tool)":
                if len(tool_set) < 2:
                    continue
            elif flt != "Alle" and flt not in tool_set:
                continue
            fname = f.get("name", "Unnamed")
            if query and query not in fname.lower() and query not in badge.lower() and query not in str(f.get("description", "")).lower():
                continue
            self.tree_favs.insert("", "end", iid=str(idx), values=(fname, badge, str(cnt)))

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
        fav = self.controller.fav_service.favs[int(sel[0])]
        patches = self.controller.get_active_patches(fav)
        if self.current_sub_patch_idx < len(patches) - 1:
            self.current_sub_patch_idx += 1
            self.display_sub_patch()

    def display_sub_patch(self):
        sel = self.tree_favs.selection()
        if not sel: return
        fav = self.controller.fav_service.favs[int(sel[0])]
        patches = self.controller.get_active_patches(fav)

        p = patches[self.current_sub_patch_idx]
        ptype = p.get("type", "smali")

        self.ent_name.delete(0, tk.END)
        self.ent_name.insert(0, fav.get("name", ""))
        self.ent_file.delete(0, tk.END)
        self.txt_orig.delete("1.0", tk.END)
        self.txt_edit.delete("1.0", tk.END)

        if ptype == "lib_replace":
            self.ent_file.insert(0, p.get("target", ""))
            self.txt_orig.insert("1.0",
                                 f"LIB REPLACEMENT\n\nErsetzt in der APK: {p.get('target', '')}\nDurch lokale Datei: {p.get('source', '')}")
            self.txt_edit.insert("1.0", p.get("source", ""))
        elif ptype == "hex":
            self.ent_file.insert(0, p.get("file", "libflutter.so"))
            self.txt_orig.insert("1.0",
                                 f"HEX PATCH\n\nDatei: {p.get('file', 'libflutter.so')}\nRAM: {p.get('ram', '')}\nBase: {p.get('base', '')}\nOrig: {p.get('orig', '')}")
            self.txt_edit.insert("1.0", p.get("patch", ""))
        elif ptype == "libcarve":
            self.ent_file.insert(0, p.get("file", ""))
            orig_info = (
                f"// LIBCARVE PATCH ({p.get('mode', 'asm')})\n"
                f"// Funktion: {p.get('func', '?')} @ {p.get('vaddr', '?')} [{p.get('abi', 'arm64-v8a')}]\n"
                f"// AOB-Signatur: {p.get('aob', '(keine)')}\n\n"
                f"{p.get('orig', '')}"
            )
            self.txt_orig.insert("1.0", orig_info)
            self.txt_edit.insert("1.0", p.get("edit", ""))
        elif ptype == "libforge":
            self.ent_file.insert(0, p.get("name", ""))
            orig_info = (
                f"// LIBFORGE BIBLIOTHEK\n"
                f"// Name: lib{p.get('name', '')}.so (v{p.get('version', '1.0')})\n"
                f"// ABI: {p.get('abi', 'arm64-v8a')} | API: {p.get('api_level', 30)} | Typ: {p.get('output_kind', 'shared')}\n"
                f"// Link-Libs: {p.get('link_libs', '')} | Flags: {p.get('extra_flags', '')}\n"
                f"// Beschreibung: {p.get('description', '')}\n"
            )
            self.txt_orig.insert("1.0", orig_info)
            self.txt_edit.insert("1.0", p.get("source_code", ""))
        else:
            self.ent_file.insert(0, p.get("file", ""))
            self.txt_orig.insert("1.0", p.get("orig", ""))
            self.txt_edit.insert("1.0", p.get("edit", ""))

        self.lbl_sub_patch.config(text=f"Sub-Patch {self.current_sub_patch_idx + 1} / {len(patches)} ({ptype})")
        self._refresh_visuals()