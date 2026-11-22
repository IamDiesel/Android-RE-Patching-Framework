# -*- coding: utf-8 -*-
"""ui.dialogs.flutter_api_extract_dialog — Optionaler Vorschau- & Auswahldialog
fuer statisch extrahierte Flutter/Dart-API-Vorlagen (Cognito / GraphQL / REST)."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk, messagebox

from services.flutter_api_extractor_service import FlutterApiExtractorService


class FlutterApiExtractDialog(tk.Toplevel):
    def __init__(self, parent, app, app_root: str, api_db_path: str):
        super().__init__(parent)
        self.app = app
        self.app_root = app_root
        self.api_db_path = api_db_path
        self._templates = []
        self._checked = {}  # tpl_id -> bool

        self.title("🔍 Statischer Flutter/Dart-API-Extractor (Optional)")
        self.geometry("1060x660")
        self.transient(parent)
        self.attributes("-topmost", True)

        self._create_widgets()
        self._run_scan()

    def _create_widgets(self):
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=10, pady=8)
        ttk.Label(
            top,
            text="Statische API-Vorlagen aus flutter_assets & libapp.so (nur ausgewaehlte Eintraege werden in den API Inspector uebernommen):",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Label(bar, text="Kategorie:").pack(side="left")
        self.var_cat = tk.StringVar(value="Alle")
        cb = ttk.Combobox(bar, textvariable=self.var_cat, values=("Alle", "Cognito", "GraphQL", "REST"), width=12, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._populate_tree())
        ttk.Button(bar, text="☑ Alle wählen", command=lambda: self._set_all(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="☐ Keine", command=lambda: self._set_all(False)).pack(side="left", padx=2)
        self.lbl_info = ttk.Label(bar, text="Scanne …", foreground="#1565c0")
        self.lbl_info.pack(side="right", padx=6)

        bot = ttk.Frame(self)
        bot.pack(side="bottom", fill="x", padx=10, pady=8)
        ttk.Button(bot, text="📥 Ausgewählte in API Inspector übernehmen", command=self._import_selected).pack(side="left", padx=4)
        ttk.Button(bot, text="❌ Schließen", command=self.destroy).pack(side="right", padx=4)

        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        left = ttk.Frame(pw)
        pw.add(left, weight=3)
        self.tree = ttk.Treeview(left, columns=("Pick", "Cat", "Method", "URL", "Hint"), show="headings")
        self.tree.heading("Pick", text="Wahl")
        self.tree.heading("Cat", text="Typ")
        self.tree.heading("Method", text="Meth.")
        self.tree.heading("URL", text="Endpunkt / URL")
        self.tree.heading("Hint", text="Quelle")
        self.tree.column("Pick", width=48, anchor="center", stretch=False)
        self.tree.column("Cat", width=72, stretch=False)
        self.tree.column("Method", width=55, stretch=False)
        self.tree.column("URL", width=290)
        self.tree.column("Hint", width=180)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_toggle)
        self.tree.bind("<space>", self._on_toggle)

        right = ttk.Frame(pw)
        pw.add(right, weight=2)
        ttk.Label(right, text="Vorschau (Vorlage):").pack(anchor="w")
        self.txt_preview = tk.Text(right, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 9), wrap="word")
        self.txt_preview.pack(fill="both", expand=True)

    def _run_scan(self):
        extra = []
        try:
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if ctrl and hasattr(ctrl, "_selfcache_so_path"):
                import os
                sc = ctrl._selfcache_so_path("libapp.so")
                if sc and os.path.isfile(sc):
                    extra.append(os.path.dirname(sc))
        except Exception:
            pass

        res = FlutterApiExtractorService.extract_from_app_root(self.app_root, extra_lib_dirs=extra)
        self._templates = res.get("templates") or []
        self._checked = {t["id"]: True for t in self._templates}
        self._populate_tree()
        if res.get("ok"):
            self.lbl_info.config(
                text=f"{len(self._templates)} Vorlage(n) in {len(res.get('scanned_files', []))} Datei(en) "
                     f"(Doppelklick/Leertaste = an-/abwählen)",
                foreground="#2e7d32" if self._templates else "#b45309",
            )
        else:
            self.lbl_info.config(text=res.get("error", "Fehler"), foreground="red")

    def _populate_tree(self):
        cat_f = self.var_cat.get()
        for i in self.tree.get_children():
            self.tree.delete(i)
        for t in self._templates:
            if cat_f != "Alle" and t.get("category") != cat_f:
                continue
            mark = "☑" if self._checked.get(t["id"], False) else "☐"
            url_col = (
                f"[{t.get('source_hint', 'GraphQL')}] {t['url']}"
                if t.get("category") == "GraphQL" and t.get("source_hint")
                else t["url"]
            )
            self.tree.insert(
                "", "end", iid=t["id"],
                values=(mark, t["category"], t["method"], url_col, t.get("source_hint", "")),
            )
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self._show_preview(kids[0])

    def _set_all(self, val: bool):
        cat_f = self.var_cat.get()
        for t in self._templates:
            if cat_f == "Alle" or t.get("category") == cat_f:
                self._checked[t["id"]] = val
        self._populate_tree()

    def _on_toggle(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        tid = sel[0]
        cur = not self._checked.get(tid, False)
        self._checked[tid] = cur
        vals = list(self.tree.item(tid, "values"))
        vals[0] = "☑" if cur else "☐"
        self.tree.item(tid, values=vals)

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if sel:
            self._show_preview(sel[0])

    def _show_preview(self, tid: str):
        tpl = next((t for t in self._templates if t["id"] == tid), None)
        if not tpl:
            return
        gql_sec = ""
        if tpl.get("category") == "GraphQL" and tpl.get("body"):
            try:
                body_obj = json.loads(tpl["body"])
                if isinstance(body_obj, dict) and body_obj.get("query"):
                    gql_sec = (
                        f"GraphQL Query ({body_obj.get('operationName') or 'Operation'}):\n"
                        f"{body_obj['query']}\n\n"
                        f"Variablen:\n{json.dumps(body_obj.get('variables') or {}, indent=2, ensure_ascii=False)}\n\n"
                    )
            except Exception:
                gql_sec = ""
        txt = (
            f"# {tpl.get('description', '')}\n"
            f"{tpl['method']} {tpl['url']}\n\n"
            f"Headers:\n{json.dumps(tpl.get('headers') or {}, indent=2, ensure_ascii=False)}\n\n"
            f"{gql_sec}"
            f"Body:\n{tpl.get('body') or '(leer)'}\n"
        )
        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", txt)

    def _import_selected(self):
        chosen = [t for t in self._templates if self._checked.get(t["id"], False)]
        if not chosen:
            messagebox.showinfo("Auswahl leer", "Bitte mindestens eine Vorlage anhaken.", parent=self)
            return
        n = FlutterApiExtractorService.import_templates_to_db(self.api_db_path, chosen)
        try:
            self.app.log(f"[Flutter-Extractor] {n} statische API-Vorlage(n) in API Inspector uebernommen.")
        except Exception:
            pass
        if messagebox.askyesno(
            "Import abgeschlossen",
            f"{n} statische API-Vorlage(n) wurden in den API Inspector uebernommen.\nJetzt direkt in den Reiter '🌐 API Inspector' wechseln?",
            parent=self,
        ):
            if hasattr(self.app, "notebook") and hasattr(self.app, "api_tab"):
                self.app.notebook.select(self.app.api_tab)
        self.destroy()
