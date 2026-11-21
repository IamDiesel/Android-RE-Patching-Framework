import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from services.favorite_service import get_shared_favorites
from ui.widgets.c_editor_widget import CCodeEditorWidget
from ui.controllers.lib_forge_controller import LibForgeController

ABIS = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]


class LibForgeTab(ttk.Frame):
    """Native Lib Builder: C-Code schreiben/importieren, kompilieren, verwalten, aktiv schalten."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.controller = LibForgeController(self, app)
        self.mgr = self.controller.mgr
        self._iid_to_id = {}
        self._selected_id = None
        self._loading = False
        self.create_widgets()
        self.refresh_list()

    # ---------------- UI ----------------
    def create_widgets(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # ----- Links: Liste + Buttons -----
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(0, 4))
        ttk.Button(btns, text="＋ Neu", command=self.controller.new_lib).pack(side="left", padx=1)
        ttk.Button(btns, text="📁 .so importieren", command=self.controller.import_so).pack(side="left", padx=1)
        ttk.Button(btns, text="⏻ Aktiv/Inaktiv", command=self.controller.toggle_active).pack(side="left", padx=1)
        ttk.Button(btns, text="🗑 Löschen", command=self.controller.delete_selected).pack(side="left", padx=1)
        ttk.Button(btns, text="🔄 Aktualisieren", command=self.controller.reload_from_disk).pack(side="right", padx=1)

        cols = ("active", "id", "name", "version", "type", "build", "somt", "desc")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=12)
        self.tree.heading("active", text="Aktiv")
        self.tree.heading("name", text="Lib")
        self.tree.heading("type", text="Typ")
        self.tree.heading("build", text="Letzter Build")
        self.tree.heading("desc", text="Beschreibung")
        self.tree.heading("id", text="ID")
        self.tree.heading("version", text="Ver")
        self.tree.heading("somt", text=".so-Stand")
        self.tree.column("active", width=45, anchor="center")
        self.tree.column("name", width=140)
        self.tree.column("type", width=70, anchor="center")
        self.tree.column("build", width=130, anchor="center")
        self.tree.column("desc", width=180)
        self.tree.column("id", width=72, anchor="center")
        self.tree.column("version", width=50, anchor="center")
        self.tree.column("somt", width=120, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Delete>", lambda e: self.controller.delete_selected())

        # ----- Rechts: Editor/Detail -----
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        head = ttk.Frame(right)
        head.pack(fill="x", pady=2)
        ttk.Label(head, text="Name:").grid(row=0, column=0, sticky="w", padx=2)
        self.ent_name = ttk.Entry(head, width=24)
        self.ent_name.grid(row=0, column=1, sticky="w", padx=2)
        self.ent_name.bind("<KeyRelease>", lambda e: self._update_so_preview())
        self.lbl_so = ttk.Label(head, text="→ lib<name>.so", foreground="#888")
        self.lbl_so.grid(row=0, column=2, sticky="w", padx=8)
        ttk.Label(head, text="Version:").grid(row=0, column=3, sticky="e", padx=2)
        self.ent_ver = ttk.Entry(head, width=10)
        self.ent_ver.grid(row=0, column=4, sticky="w", padx=2)

        ttk.Label(head, text="Beschreibung:").grid(row=1, column=0, sticky="w", padx=2, pady=(2, 0))
        self.ent_desc = ttk.Entry(head, width=60)
        self.ent_desc.grid(row=1, column=1, columnspan=3, sticky="we", padx=2, pady=(2, 0))

        # Erweitert
        adv = ttk.LabelFrame(right, text="Erweitert")
        adv.pack(fill="x", pady=4)
        ttk.Label(adv, text="ABI:").grid(row=0, column=0, sticky="w", padx=2)
        self.cmb_abi = ttk.Combobox(adv, values=ABIS, width=14, state="readonly")
        self.cmb_abi.grid(row=0, column=1, sticky="w", padx=2)
        ttk.Label(adv, text="API:").grid(row=0, column=2, sticky="w", padx=2)
        self.ent_api = ttk.Entry(adv, width=6)
        self.ent_api.grid(row=0, column=3, sticky="w", padx=2)
        ttk.Label(adv, text="Link-Libs (-l):").grid(row=0, column=4, sticky="w", padx=2)
        self.ent_links = ttk.Entry(adv, width=16)
        self.ent_links.grid(row=0, column=5, sticky="w", padx=2)
        ttk.Label(adv, text="Extra-Flags:").grid(row=0, column=6, sticky="w", padx=2)
        self.ent_extra = ttk.Entry(adv, width=20)
        self.ent_extra.grid(row=0, column=7, sticky="w", padx=2)
        ttk.Label(adv, text="Ausgabe:").grid(row=1, column=0, sticky="w", padx=2, pady=(2, 0))
        self.cmb_kind = ttk.Combobox(adv, values=["shared", "executable"], width=14, state="readonly")
        self.cmb_kind.grid(row=1, column=1, sticky="w", padx=2, pady=(2, 0))
        self.cmb_kind.bind("<<ComboboxSelected>>", lambda e: self._update_so_preview())
        ttk.Label(adv, text="(shared = lib<name>.so · executable = <name>, PIE-Binary)",
                  foreground="#888").grid(row=1, column=2, columnspan=6, sticky="w", padx=2, pady=(2, 0))

        # Editor
        self.editor = CCodeEditorWidget(right)
        self.editor.pack(fill="both", expand=True, pady=2)

        # Aktionsleiste
        act = ttk.Frame(right)
        act.pack(fill="x", pady=2)
        ttk.Button(act, text="⚙️ Compile / Recompile", command=self.controller.compile_selected).pack(side="left", padx=2)
        ttk.Button(act, text="💾 Speichern", command=self.controller.save_editor).pack(side="left", padx=2)
        ttk.Button(act, text="🧬 In Native Studio öffnen", command=self.open_in_native_studio).pack(side="left", padx=4)
        ttk.Button(act, text="▶️ Live im Simulator testen", command=self.run_live_in_simulator).pack(side="left", padx=4)
        mb_fav = ttk.Menubutton(act, text="⭐ Favoriten ▾")
        fav_menu = tk.Menu(mb_fav, tearoff=0)
        fav_menu.add_command(label="⭐ Aktuelle Lib als neuen Favoriten speichern",
                             command=self.save_current_as_favorite)
        fav_menu.add_command(label="➕ Aktuelle Lib zu bestehendem Favoriten hinzufügen...",
                             command=self.append_current_to_favorite)
        fav_menu.add_command(label="📦 Aktive Workspace-Patches als Kombi-Favorit sichern",
                             command=self.save_workspace_bundle_as_favorite)
        fav_menu.add_separator()
        fav_menu.add_command(label="🗂 Favoriten-Manager öffnen (Filter: LibForge)",
                             command=self.open_favorites_manager)
        mb_fav.config(menu=fav_menu)
        mb_fav.pack(side="left", padx=4)
        self.lbl_status = ttk.Label(act, text="", font=("Segoe UI", 9, "bold"))
        self.lbl_status.pack(side="left", padx=10)

        # Hinweis-Banner (kontextabhängig: Shared = loadLibrary · Executable = Start & Live-Log)
        hint = ttk.LabelFrame(right, text="ℹ️ Hinweis / Laden")
        hint.pack(fill="x", pady=2)
        self.txt_hint = tk.Text(hint, height=4, bg="#2A2A2A", fg="#D4D4D4", wrap="none", font=("Consolas", 9))
        self.txt_hint.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        self.txt_hint.config(state="disabled")
        ttk.Button(hint, text="📋 Kopieren", command=self.controller.copy_load_snippet).pack(side="right", padx=4)

    # ---------------- View-API (vom Controller genutzt) ----------------
    def current_lib(self):
        return self.mgr.get(self._selected_id) if self._selected_id else None

    def refresh_list(self, select_id=None):
        # aktuellen Editor sichern, bevor die Liste neu aufgebaut wird
        if self._selected_id and not self._loading:
            lib = self.mgr.get(self._selected_id)
            if lib:
                self.sync_editor_into(lib)
                self.mgr.update(lib)

        target = select_id or self._selected_id
        self._loading = True
        self.tree.delete(*self.tree.get_children())
        self._iid_to_id.clear()
        first_iid = None
        for lib in self.mgr.get_all():
            mark = "✓" if lib.active else "–"
            _somt = "-"
            try:
                import os as _os, time as _tm
                _sp = self.mgr.abspath(lib.so_relpath) if lib.so_relpath else ""
                if _sp and _os.path.exists(_sp):
                    _somt = _tm.strftime("%Y-%m-%d %H:%M", _tm.localtime(_os.path.getmtime(_sp)))
                elif lib.so_relpath:
                    _somt = "(fehlt)"
            except Exception:
                _somt = "?"
            iid = self.tree.insert("", "end", values=(mark, (lib.id or "")[:8], lib.name or "(unbenannt)",
                                                       getattr(lib, "version", "") or "-", lib.origin,
                                                       lib.last_build_at or "-", _somt, lib.description or ""))
            self._iid_to_id[iid] = lib.id
            if first_iid is None:
                first_iid = iid
            if lib.id == target:
                self.tree.selection_set(iid)
                self.tree.see(iid)
        self._loading = False

        sel = self.tree.selection()
        if sel:
            self._selected_id = self._iid_to_id.get(sel[0])
            self._load_editor(self.mgr.get(self._selected_id))
        elif first_iid:
            self.tree.selection_set(first_iid)
        else:
            self._selected_id = None
            self._clear_editor()

    def _on_tree_select(self, event=None):
        if self._loading:
            return
        sel = self.tree.selection()
        if not sel:
            return
        new_id = self._iid_to_id.get(sel[0])
        if new_id == self._selected_id:
            return
        # vorherige Auswahl sichern
        if self._selected_id:
            old = self.mgr.get(self._selected_id)
            if old:
                self.sync_editor_into(old)
                self.mgr.update(old)
        self._selected_id = new_id
        self._load_editor(self.mgr.get(new_id))

    def _load_editor(self, lib):
        if not lib:
            self._clear_editor()
            return
        self.ent_name.delete(0, "end"); self.ent_name.insert(0, lib.name)
        self.ent_desc.delete(0, "end"); self.ent_desc.insert(0, lib.description)
        self.ent_ver.delete(0, "end"); self.ent_ver.insert(0, getattr(lib, "version", "") or "")
        self.cmb_abi.set(lib.abi or self.app.cfg.config.get("DEFAULT_ABI", "arm64-v8a"))
        self.ent_api.delete(0, "end"); self.ent_api.insert(0, str(lib.api_level or self.app.cfg.config.get("DEFAULT_API_LEVEL", 30)))
        self.ent_links.delete(0, "end"); self.ent_links.insert(0, lib.link_libs)
        self.ent_extra.delete(0, "end"); self.ent_extra.insert(0, lib.extra_flags)
        self.cmb_kind.set(getattr(lib, "output_kind", "shared") or "shared")
        if lib.origin == "imported":
            self.editor.set_text("// Importierte .so – kein Quellcode vorhanden.")
            self.editor.set_readonly(True)
        else:
            self.editor.set_readonly(False)
            self.editor.set_text(lib.source_code)
        self._update_so_preview()
        state = "aktiv" if lib.active else "inaktiv"
        col = "green" if lib.last_build_ok else "#888"
        self.set_status(f"{lib.origin} · {state} · Build: {'OK' if lib.last_build_ok else '—'}", col)
        self.show_load_hint(lib.name)

    def _clear_editor(self):
        for e in (self.ent_name, self.ent_desc, self.ent_ver, self.ent_api, self.ent_links, self.ent_extra):
            e.delete(0, "end")
        self.cmb_kind.set("shared")
        self.editor.set_readonly(False)
        self.editor.set_text("")
        self.set_status("", "#888")
        self._set_hint_text("")

    def sync_editor_into(self, lib):
        lib.name = self.ent_name.get().strip()
        lib.description = self.ent_desc.get().strip()
        lib.version = self.ent_ver.get().strip() or "1.0"
        lib.abi = self.cmb_abi.get() or "arm64-v8a"
        try:
            lib.api_level = int(self.ent_api.get().strip())
        except (ValueError, TypeError):
            lib.api_level = self.app.cfg.config.get("DEFAULT_API_LEVEL", 30)
        lib.link_libs = self.ent_links.get().strip()
        lib.extra_flags = self.ent_extra.get().strip()
        lib.output_kind = self.cmb_kind.get() or "shared"
        if lib.origin != "imported":
            lib.source_code = self.editor.get_text()

    def _update_so_preview(self):
        name = self.ent_name.get().strip()
        kind = self.cmb_kind.get() if hasattr(self, "cmb_kind") else "shared"
        if kind == "executable":
            self.lbl_so.config(text=f"→ {name}" if name else "→ <name>")
        else:
            self.lbl_so.config(text=f"→ lib{name}.so" if name else "→ lib<name>.so")

    def set_status(self, text, color="#888"):
        self.lbl_status.config(text=text, foreground=color)

    def _set_hint_text(self, text):
        self.txt_hint.config(state="normal")
        self.txt_hint.delete("1.0", "end")
        self.txt_hint.insert("1.0", text)
        self.txt_hint.config(state="disabled")

    def show_load_hint(self, name):
        lib = self.current_lib()
        kind = getattr(lib, "output_kind", "shared") if lib else "shared"
        if kind == "executable":
            self._set_hint_text(
                "Eigenständiges Executable (PIE) – wird NICHT in die App injiziert/geladen.\n"
                "Deploy & Ausführung im Reiter „Start & Live-Log“ (Executable-Bereich, Ziel-Dropdown).")
        elif name:
            self._set_hint_text(
                "// Shared Library – wird beim Build injiziert, aber NICHT automatisch geladen.\n"
                "// Per Smali-Patch laden (oder Smali Studio → 📚 loadLibrary):\n"
                + self.controller.load_snippet(name))
        else:
            self._set_hint_text("")

    def show_build_error(self, err):
        # Fehler landet bereits in der Konsole; hier zusaetzlich kompakt anzeigen
        self._set_hint_text("BUILD-FEHLER:\n" + (err or "").strip())

    def open_in_native_studio(self):
        """Oeffnet die aktuell ausgewaehlte gebaute/importierte .so im Native Studio (Paket 6B)."""
        lib = self.current_lib()
        if not lib or not getattr(lib, "so_relpath", ""):
            self.set_status("Bitte erst kompilieren oder .so importieren.", "red")
            return
        sp = self.mgr.abspath(lib.so_relpath)
        ns = getattr(self.app, "native_studio", None)
        ctrl = getattr(ns, "controller", None) if ns is not None else None
        if ctrl is not None and sp:
            try:
                ctrl._load_so(sp)
                if hasattr(self.app, "notebook") and ns is not None:
                    self.app.notebook.select(ns)
                self.app.log(f"[*] LibForge -> Native Studio: {sp}")
            except Exception as e:
                self.app.log(f"[!] Native Studio oeffnen FEHLER: {e!r}")

    def run_live_in_simulator(self):
        """Startet die App im Simulator mit aktiven LibForge-/Workspace-Patches ('live', ohne Build)."""
        dbg_ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        if dbg_ctrl is not None and hasattr(dbg_ctrl, "run_live_with_patches"):
            dbg_ctrl.run_live_with_patches()

    # ---------------- Favoriten-Integration ----------------
    def lib_to_patch_dict(self, lib) -> dict:
        """Serialisiert eine LibForge-NativeLib in das einheitliche Favoriten-Patch-Schema."""
        return {
            "type": "libforge",
            "name": getattr(lib, "name", "") or "",
            "version": getattr(lib, "version", "1.0") or "1.0",
            "description": getattr(lib, "description", "") or "",
            "origin": getattr(lib, "origin", "built") or "built",
            "source_code": getattr(lib, "source_code", "") or "",
            "abi": getattr(lib, "abi", "arm64-v8a") or "arm64-v8a",
            "api_level": int(getattr(lib, "api_level", 30) or 30),
            "link_libs": getattr(lib, "link_libs", "log dl") or "log dl",
            "extra_flags": getattr(lib, "extra_flags", "") or "",
            "output_kind": getattr(lib, "output_kind", "shared") or "shared",
            "so_relpath": getattr(lib, "so_relpath", "") or "",
        }

    def get_active_libforge_patches(self, include_selected_if_none: bool = False) -> list:
        """Liefert alle aktiven LibForge-Libs (optional Fallback auf aktuell ausgewaehlte Lib)."""
        cur = self.current_lib()
        if cur is not None and not self._loading:
            try:
                self.sync_editor_into(cur)
            except Exception:
                pass
        out = [self.lib_to_patch_dict(l) for l in self.mgr.get_all() if getattr(l, "active", False)]
        if not out and include_selected_if_none and cur is not None:
            out.append(self.lib_to_patch_dict(cur))
        return out

    def save_current_as_favorite(self):
        lib = self.current_lib()
        if lib is None:
            return messagebox.showinfo("LibForge Favoriten", "Bitte zuerst eine Bibliothek auswählen oder anlegen.")
        self.sync_editor_into(lib)
        name = simpledialog.askstring("Als Favorit speichern", "Name für den LibForge-Favoriten:",
                                      initialvalue=f"lib{lib.name or 'custom'}", parent=self)
        if not name or not name.strip():
            return
        patch = self.lib_to_patch_dict(lib)
        fav = {
            "name": name.strip(),
            "version": patch["version"],
            "description": f"[v{patch['version']}] LibForge lib{patch['name']}.so ({patch['abi']})",
            "patches": [patch],
        }
        get_shared_favorites(self.app).add_favorite(fav)
        self.app.log(f"[+] LibForge-Favorit '{name.strip()}' gespeichert.")

    def append_current_to_favorite(self):
        lib = self.current_lib()
        if lib is None:
            return messagebox.showinfo("LibForge Favoriten", "Bitte zuerst eine Bibliothek auswählen.")
        self.sync_editor_into(lib)
        fav_svc = get_shared_favorites(self.app)
        if not fav_svc.favs:
            return self.save_current_as_favorite()
        from ui.dialogs.favorite_patches_dialog import choose_existing_favorite_dialog
        idx = choose_existing_favorite_dialog(self.winfo_toplevel(), fav_svc,
                                              title="LibForge-Lib zu Favorit hinzufügen")
        if idx is None:
            return
        fav = fav_svc.favs[idx]
        patches = fav.setdefault("patches", [])
        patch = self.lib_to_patch_dict(lib)
        for i, ex in enumerate(patches):
            if ex.get("type") == "libforge" and ex.get("name") == patch["name"]:
                patches[i] = patch
                break
        else:
            patches.append(patch)
        fav_svc.save_favs()
        self.app.log(f"[+] LibForge-Lib '{lib.name}' zu Favorit '{fav.get('name', '')}' hinzugefügt.")

    def save_workspace_bundle_as_favorite(self):
        ws = getattr(self.app, "workspace_tab", None)
        ctrl = getattr(ws, "controller", None)
        if ctrl is not None:
            ctrl.save_current_as_favorite()

    def open_favorites_manager(self):
        ws = getattr(self.app, "workspace_tab", None)
        if ws is None:
            return
        from ui.dialogs.favorite_patches_dialog import FavoritePatchesDialog
        FavoritePatchesDialog(self.winfo_toplevel(), ws, initial_filter="LibForge")
