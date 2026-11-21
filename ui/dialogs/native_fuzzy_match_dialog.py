# -*- coding: utf-8 -*-
"""ui.dialogs.native_fuzzy_match_dialog — Konflikt- & Pattern-Matcher fuer LibCarve-Favoriten."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from services.libcarve_pattern_service import LibCarvePatternService
from core.native.engine_arm64 import Arm64Decoder


class NativeFuzzyMatchDialog(tk.Toplevel):
    def __init__(self, parent, app, libcarve_tab, fav_patch: dict):
        super().__init__(parent)
        self.app = app
        self.libcarve_tab = libcarve_tab
        self.ctrl = libcarve_tab.controller
        self.fav_patch = dict(fav_patch)
        self.candidates = []

        self.title("⚠️ LibCarve Pattern Matcher (Versions-Abgleich)")
        self.geometry("1120x680")
        self.transient(parent)
        self.attributes("-topmost", True)

        self._create_widgets()
        self._run_match()

    def _create_widgets(self):
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=10, pady=8)
        ttk.Label(
            top,
            text=f"LibCarve Pattern Match fuer '{self.fav_patch.get('file', '?')}' ({self.fav_patch.get('func', '?')})",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(top, text=f"AOB-Signatur: {self.fav_patch.get('aob', '(keine)')}", font=("Consolas", 8), foreground="#555").pack(anchor="w")
        self.lbl_status = ttk.Label(top, text="Suche Kandidaten …", foreground="#1565c0")
        self.lbl_status.pack(anchor="w", pady=(4, 0))

        bot = ttk.Frame(self)
        bot.pack(side="bottom", fill="x", padx=10, pady=8)
        ttk.Button(bot, text="✅ Ausgewählte Adresse als Patch übernehmen", command=self._apply_selected).pack(side="left", padx=4)
        ttk.Button(bot, text="✏️ Im LibCarve Editor öffnen", command=self._open_in_editor).pack(side="left", padx=4)
        ttk.Button(bot, text="❌ Schließen", command=self.destroy).pack(side="right", padx=4)

        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        left = ttk.Frame(pw)
        pw.add(left, weight=2)
        self.tree = ttk.Treeview(left, columns=("Score", "VAddr", "Func", "Reason"), show="headings")
        self.tree.heading("Score", text="Score")
        self.tree.heading("VAddr", text="VAddr")
        self.tree.heading("Func", text="Funktion")
        self.tree.heading("Reason", text="Treffer-Grund")
        self.tree.column("Score", width=65, stretch=False)
        self.tree.column("VAddr", width=110, stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        ttk.Label(right, text="Disassembly-Vorschau am Kandidaten:").pack(anchor="w")
        self.txt_preview = tk.Text(right, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), wrap="none")
        self.txt_preview.pack(fill="both", expand=True)

    def _run_match(self):
        img, idx = self.ctrl.image, self.ctrl.index
        if img is None or idx is None:
            self.lbl_status.config(text="Keine .so geladen.", foreground="red")
            return
        res = LibCarvePatternService.match_patch(img, idx, self.fav_patch)
        self.candidates = res.get("candidates") or []
        for i in self.tree.get_children():
            self.tree.delete(i)
        for c in self.candidates:
            self.tree.insert("", "end", values=(f"{c['score']:.2f}", f"0x{c['vaddr']:x}", c["func"], c["reason"]))
        if self.candidates:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._show_preview(self.candidates[0]["vaddr"])
            self.lbl_status.config(text=f"Status: {res['status']} ({len(self.candidates)} Kandidat(en))")
        else:
            self.lbl_status.config(text="Keine automatischen Treffer gefunden — bitte manuell in LibCarve waehlen.", foreground="#b91c1c")

    def _selected_vaddr(self):
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        return int(vals[1], 16) if len(vals) >= 2 else None

    def _on_select(self, _e=None):
        v = self._selected_vaddr()
        if v is not None:
            self._show_preview(v)

    def _show_preview(self, vaddr: int):
        img = self.ctrl.image
        if img is None:
            return
        raw = img.read_vaddr(vaddr, 96)
        md = Arm64Decoder()._md
        lines = [f"// Kandidat @ 0x{vaddr:x}"]
        for a, _sz, mnem, op in md.disasm_lite(raw, vaddr):
            lines.append(f"0x{a:08x}:  {mnem:<8s} {op}")
        self.txt_preview.delete("1.0", tk.END)
        self.txt_preview.insert("1.0", "\n".join(lines))

    def _apply_selected(self):
        v = self._selected_vaddr()
        if v is None:
            return
        from core.application.session_state import SessionState
        patched = dict(self.fav_patch)
        patched["vaddr"] = f"0x{v:x}"
        if self.ctrl.index:
            patched["func"] = self.ctrl.index.func_at(v) or patched.get("func", "")
        SessionState.active_libcarve_patches.append(patched)
        self.libcarve_tab.refresh_patch_tree()
        if hasattr(self.app, "persist_active_session"):
            self.app.persist_active_session()
        messagebox.showinfo("Erfolg", f"LibCarve-Patch an 0x{v:x} uebernommen!", parent=self)
        self.destroy()

    def _open_in_editor(self):
        v = self._selected_vaddr()
        if v is not None:
            self.ctrl.navigate_to(v, add_to_history=True)
            self.libcarve_tab.editor.var_patch_mode.set(self.fav_patch.get("mode", "asm"))
            self.libcarve_tab.editor.set_edit_text(self.fav_patch.get("edit", ""))
            self.destroy()
