# -*- coding: utf-8 -*-
"""ui.widgets.libcarve_editor_widget — Split-Editor (Original ASM/C/Hex + Edit ASM/C) fuer LibCarve.

Analog zu SmaliEditorWidget:
  - Oben: Read-Only Ansicht (Split ASM+C, ASM, Decompiled C oder Hex)
  - Mitte: Uebernahme-Buttons, Patch-Modus (asm | c_cave | hex), Snippets, Live-Byte-Budget
  - Unten: Editierbarer Patch-Editor mit debounced Viewport-Lazy-Highlighting (ARM64 ASM & C)
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

_C_KEYWORDS = (
    "return", "if", "else", "for", "while", "do", "switch", "case", "break",
    "continue", "struct", "typedef", "enum", "union", "const", "volatile",
    "static", "extern", "inline", "sizeof", "NULL", "true", "false",
)
_C_TYPES = (
    "void", "int", "unsigned", "signed", "char", "short", "long", "float", "double",
    "bool", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t",
    "int32_t", "int64_t", "uintptr_t", "intptr_t", "size_t", "ssize_t",
    "undefined", "undefined4", "undefined8",
)
_ARM_MNEMS = (
    r"\b(?:nop|ret|br|blr|bl|b(?:\.[a-z]{2})?|cbz|cbnz|tbz|tbnz|adrp|adr|"
    r"mov|movz|movn|movk|mvn|add|adds|sub|subs|cmp|cmn|tst|and|ands|orr|eor|"
    r"lsl|lsr|asr|ldr|ldrb|ldrh|ldp|ldur|str|strb|strh|stp|stur|svc|pacibsp|paciasp|bti)\b"
)


class LibCarveEditorWidget(ttk.Frame):
    def __init__(self, parent, on_mode_change=None, on_budget_check=None):
        super().__init__(parent)
        self._on_mode_change = on_mode_change
        self._on_budget_check = on_budget_check
        self._dirty = False
        self._orig_asm_text = ""
        self._orig_c_text = ""
        self._orig_hex_text = ""
        self._create_widgets()
        self._setup_tags()
        self._bind_lazy_highlighting()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def _create_widgets(self):
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill="both", expand=True)

        # ---- Oben: Read-Only Original (ASM / C / Split / Hex) ----
        f_orig = ttk.LabelFrame(paned)
        paned.add(f_orig, weight=1)

        f_hdr = ttk.Frame(f_orig)
        ttk.Label(f_hdr, text="Original Code (Read-Only)").pack(side="left")
        self.btn_find_cg = ttk.Button(f_hdr, text="🔍 Find in Call Graph")
        self.btn_find_cg.pack(side="left", padx=6)
        self.btn_ghidra = ttk.Button(f_hdr, text="⚡ Ghidra C dekompilieren")
        self.btn_ghidra.pack(side="left", padx=4)

        self.var_scope_mode = tk.StringVar(value="func")
        f_scope = ttk.Frame(f_hdr)
        f_scope.pack(side="right", padx=6)
        ttk.Radiobutton(f_scope, text="Nur Funktion", variable=self.var_scope_mode, value="func").pack(side="left", padx=2)
        ttk.Radiobutton(f_scope, text="Bereich/Sektion", variable=self.var_scope_mode, value="section").pack(side="left", padx=2)

        self.var_view_mode = tk.StringVar(value="split")
        f_view = ttk.Frame(f_hdr)
        f_view.pack(side="right", padx=8)
        for lbl, val in (("Split ASM+C", "split"), ("ASM", "asm"), ("Ghidra/Pseudo C", "c"), ("Hex", "hex")):
            ttk.Radiobutton(
                f_view, text=lbl, variable=self.var_view_mode, value=val,
                command=self._relayout_orig,
            ).pack(side="left", padx=2)

        f_orig.config(labelwidget=f_hdr)

        self._orig_paned = ttk.PanedWindow(f_orig, orient=tk.HORIZONTAL)
        self._orig_paned.pack(fill="both", expand=True, padx=2, pady=2)

        self._f_orig_asm = ttk.Frame(self._orig_paned)
        self.sy_orig = ttk.Scrollbar(self._f_orig_asm, orient="vertical")
        self.sx_orig = ttk.Scrollbar(self._f_orig_asm, orient="horizontal")
        self.txt_orig = tk.Text(
            self._f_orig_asm, wrap="none", font=("Consolas", 10),
            bg="#1E1E1E", fg="#D4D4D4", insertbackground="white",
            yscrollcommand=self.sy_orig.set, xscrollcommand=self.sx_orig.set,
            state="disabled",
        )
        self.sy_orig.config(command=self.txt_orig.yview)
        self.sx_orig.config(command=self.txt_orig.xview)
        self.sy_orig.pack(side="right", fill="y")
        self.sx_orig.pack(side="bottom", fill="x")
        self.txt_orig.pack(side="left", fill="both", expand=True)

        self._f_orig_c = ttk.Frame(self._orig_paned)
        self.sy_orig_c = ttk.Scrollbar(self._f_orig_c, orient="vertical")
        self.sx_orig_c = ttk.Scrollbar(self._f_orig_c, orient="horizontal")
        self.txt_orig_c = tk.Text(
            self._f_orig_c, wrap="none", font=("Consolas", 10),
            bg="#1E1E1E", fg="#D4D4D4", insertbackground="white",
            yscrollcommand=self.sy_orig_c.set, xscrollcommand=self.sx_orig_c.set,
            state="disabled",
        )
        self.sy_orig_c.config(command=self.txt_orig_c.yview)
        self.sx_orig_c.config(command=self.txt_orig_c.xview)
        self.sy_orig_c.pack(side="right", fill="y")
        self.sx_orig_c.pack(side="bottom", fill="x")
        self.txt_orig_c.pack(side="left", fill="both", expand=True)

        self._orig_paned.add(self._f_orig_asm, weight=1)
        self._orig_paned.add(self._f_orig_c, weight=1)

        # ---- Mitte: Steuerleiste ----
        f_mid = ttk.Frame(paned)
        paned.add(f_mid, weight=0)

        ttk.Button(f_mid, text="⬇ ASM zum Editieren kopieren", command=self.copy_asm_to_edit).pack(side="left", padx=2, pady=3)
        ttk.Button(f_mid, text="⬇ C-Vorlage übernehmen", command=self.copy_c_to_edit).pack(side="left", padx=2, pady=3)

        ttk.Label(f_mid, text="Patch-Modus:").pack(side="left", padx=(8, 2))
        self.var_patch_mode = tk.StringVar(value="asm")
        self.cmb_patch_mode = ttk.Combobox(
            f_mid, textvariable=self.var_patch_mode, width=14, state="readonly",
            values=("asm", "c_cave", "hex"),
        )
        self.cmb_patch_mode.pack(side="left", padx=2)
        self.cmb_patch_mode.bind("<<ComboboxSelected>>", self._handle_mode_selected)

        self.lbl_budget = ttk.Label(f_mid, text="Bytes: -", foreground="#555", font=("Consolas", 9))
        self.lbl_budget.pack(side="left", padx=8)

        self.btn_snippet = ttk.Button(f_mid, text="➕ Snippet einfügen")
        self.btn_snippet.pack(side="right", padx=2, pady=3)
        ttk.Button(f_mid, text="🧪 Assemblieren / Prüfen", command=self._trigger_budget_check).pack(side="right", padx=2, pady=3)

        # ---- Unten: Editierbarer Patch-Editor ----
        f_edit = ttk.LabelFrame(paned, text="Editierter Code (Dein Patch — ASM / C-Cave / Hex)")
        paned.add(f_edit, weight=1)
        c_edit = ttk.Frame(f_edit)
        c_edit.pack(fill="both", expand=True, padx=2, pady=2)
        self.sy_edit = ttk.Scrollbar(c_edit, orient="vertical")
        self.sx_edit = ttk.Scrollbar(c_edit, orient="horizontal")
        self.txt_edit = tk.Text(
            c_edit, wrap="none", font=("Consolas", 10),
            bg="#1E1E1E", fg="#D4D4D4", insertbackground="white", undo=True,
            yscrollcommand=self.sy_edit.set, xscrollcommand=self.sx_edit.set,
        )
        self.sy_edit.config(command=self.txt_edit.yview)
        self.sx_edit.config(command=self.txt_edit.xview)
        self.sy_edit.pack(side="right", fill="y")
        self.sx_edit.pack(side="bottom", fill="x")
        self.txt_edit.pack(side="left", fill="both", expand=True)

        def _on_key(_e=None):
            self._dirty = True
            self._debounced_highlight(self.txt_edit)
            self._debounced_budget_check()
        self.txt_edit.bind("<KeyRelease>", _on_key, add="+")

    def _relayout_orig(self):
        mode = self.var_view_mode.get()
        for pane in list(self._orig_paned.panes()):
            try:
                self._orig_paned.forget(pane)
            except Exception:
                pass
        if mode == "split":
            self._orig_paned.add(self._f_orig_asm, weight=1)
            self._orig_paned.add(self._f_orig_c, weight=1)
            self._set_ro_text(self.txt_orig, self._orig_asm_text)
            self._set_ro_text(self.txt_orig_c, self._orig_c_text)
        elif mode == "c":
            self._orig_paned.add(self._f_orig_c, weight=1)
            self._set_ro_text(self.txt_orig_c, self._orig_c_text)
        elif mode == "hex":
            self._orig_paned.add(self._f_orig_asm, weight=1)
            self._set_ro_text(self.txt_orig, self._orig_hex_text)
        else:  # asm
            self._orig_paned.add(self._f_orig_asm, weight=1)
            self._set_ro_text(self.txt_orig, self._orig_asm_text)
        self.rehighlight()

    @staticmethod
    def _set_ro_text(widget, content: str):
        try:
            widget.config(state="normal")
            widget.delete("1.0", tk.END)
            widget.insert("1.0", content or "")
            widget.config(state="disabled")
        except Exception:
            pass

    def load_function_views(self, asm_text: str, c_text: str = "", hex_text: str = "", *, clear_edit: bool = True):
        self._orig_asm_text = asm_text or ""
        self._orig_c_text = c_text or ""
        self._orig_hex_text = hex_text or ""
        self._relayout_orig()
        if clear_edit:
            self.clear_edit()
            self._dirty = False

    def set_c_text(self, c_text: str):
        self._orig_c_text = c_text or ""
        self._set_ro_text(self.txt_orig_c, self._orig_c_text)
        self._debounced_highlight(self.txt_orig_c)

    def copy_asm_to_edit(self):
        # Extrahiere reine Instruktionen (ohne Adress-Präfix), damit direkt assemblierbar
        clean_lines = []
        for ln in (self._orig_asm_text or "").splitlines():
            st = ln.strip()
            if not st or st.startswith(";"):
                clean_lines.append(ln)
                continue
            m = re.match(r"^0x[0-9a-fA-F]+:\s*(?:[0-9a-fA-F]{2}\s+){4}\|?\s*(.+)$", st)
            if m:
                clean_lines.append("    " + m.group(1).strip())
            else:
                clean_lines.append(ln)
        self.var_patch_mode.set("asm")
        self.set_edit_text("\n".join(clean_lines))

    def copy_c_to_edit(self):
        self.var_patch_mode.set("c_cave")
        txt = self._orig_c_text.strip()
        if not txt or txt.startswith("// Warte") or txt.startswith("// Kein"):
            txt = (
                "#include <stdint.h>\n\n"
                "int64_t carve_entry(int64_t x0, int64_t x1, int64_t x2, int64_t x3) {\n"
                "    return 1;\n"
                "}\n"
            )
        self.set_edit_text(txt)

    def set_edit_text(self, text: str, mark_dirty: bool = True):
        self.txt_edit.delete("1.0", tk.END)
        self.txt_edit.insert("1.0", text or "")
        self._dirty = mark_dirty
        self.rehighlight()
        self._trigger_budget_check()

    def get_edit_text(self) -> str:
        return self.txt_edit.get("1.0", tk.END).strip()

    def get_orig_text(self) -> str:
        if self.var_patch_mode.get() == "c_cave":
            return self._orig_c_text.strip()
        if self.var_patch_mode.get() == "hex":
            return self._orig_hex_text.strip()
        return self._orig_asm_text.strip()

    def clear_edit(self):
        self.txt_edit.delete("1.0", tk.END)
        self._dirty = False
        try:
            self.lbl_budget.config(text="Bytes: -", foreground="#555")
        except Exception:
            pass

    def set_budget_status(self, text: str, color: str = "#2e7d32"):
        try:
            self.lbl_budget.config(text=text, foreground=color)
        except Exception:
            pass

    def _handle_mode_selected(self, _e=None):
        self.rehighlight()
        self._trigger_budget_check()
        if callable(self._on_mode_change):
            try:
                self._on_mode_change(self.var_patch_mode.get())
            except Exception:
                pass

    def _debounced_budget_check(self):
        if getattr(self, "_bcheck_timer", None):
            try:
                self.after_cancel(self._bcheck_timer)
            except Exception:
                pass
        self._bcheck_timer = self.after(350, self._trigger_budget_check)

    def _trigger_budget_check(self):
        if callable(self._on_budget_check):
            try:
                self._on_budget_check()
            except Exception:
                pass

    def _setup_tags(self):
        for w in (self.txt_orig, self.txt_orig_c, self.txt_edit):
            w.tag_configure("addr", foreground="#858585")
            w.tag_configure("mnem", foreground="#C586C0", font=("Consolas", 10, "bold"))
            w.tag_configure("reg", foreground="#9CDCFE")
            w.tag_configure("num", foreground="#B5CEA8")
            w.tag_configure("kw", foreground="#569CD6", font=("Consolas", 10, "bold"))
            w.tag_configure("typ", foreground="#4EC9B0")
            w.tag_configure("str", foreground="#CE9178")
            w.tag_configure("com", foreground="#6A9955", font=("Consolas", 10, "italic"))

    def _bind_lazy_highlighting(self):
        events = ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Configure>")
        for txt in (self.txt_orig, self.txt_orig_c, self.txt_edit):
            for ev in events:
                txt.bind(ev, lambda _e, t=txt: self._debounced_highlight(t), add="+")
        self.sy_orig.config(command=lambda *a: (self.txt_orig.yview(*a), self._debounced_highlight(self.txt_orig)))
        self.sy_orig_c.config(command=lambda *a: (self.txt_orig_c.yview(*a), self._debounced_highlight(self.txt_orig_c)))
        self.sy_edit.config(command=lambda *a: (self.txt_edit.yview(*a), self._debounced_highlight(self.txt_edit)))

    def _debounced_highlight(self, w):
        if getattr(w, "_hl_timer", None):
            try:
                self.after_cancel(w._hl_timer)
            except Exception:
                pass
        w._hl_timer = self.after(100, lambda: self.apply_highlighting(w))

    def rehighlight(self):
        self._debounced_highlight(self.txt_orig)
        self._debounced_highlight(self.txt_orig_c)
        self._debounced_highlight(self.txt_edit)

    def apply_highlighting(self, txt_widget):
        try:
            if not txt_widget.winfo_exists():
                return
            top_idx = txt_widget.index("@0,0")
            bot_idx = txt_widget.index(f"@0,{max(1, txt_widget.winfo_height())}")
        except tk.TclError:
            return

        top_line = max(1, int(top_idx.split(".")[0]) - 25)
        bot_line = int(bot_idx.split(".")[0]) + 25
        start_idx = f"{top_line}.0"
        end_idx = f"{bot_line}.end"

        for tag in ("addr", "mnem", "reg", "num", "kw", "typ", "str", "com"):
            txt_widget.tag_remove(tag, start_idx, end_idx)

        is_c = (txt_widget is self.txt_orig_c) or (
            txt_widget is self.txt_edit and self.var_patch_mode.get() == "c_cave"
        )
        block = txt_widget.get(start_idx, end_idx)
        for rel_i, line in enumerate(block.splitlines()):
            ln = top_line + rel_i
            if is_c:
                for m in re.finditer(r"//.*$", line):
                    txt_widget.tag_add("com", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(r'"(?:[^"\\]|\\.)*"', line):
                    txt_widget.tag_add("str", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for kw in _C_KEYWORDS:
                    for m in re.finditer(rf"\b{kw}\b", line):
                        txt_widget.tag_add("kw", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for tp in _C_TYPES:
                    for m in re.finditer(rf"\b{tp}\b", line):
                        txt_widget.tag_add("typ", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(r"\b(?:0x[0-9a-fA-F]+|\d+)\b", line):
                    txt_widget.tag_add("num", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
            else:
                for m in re.finditer(r"(?://|;|#(?![-0-9a-fA-Fx])).*$", line):
                    txt_widget.tag_add("com", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(r"^0x[0-9a-fA-F]+:", line):
                    txt_widget.tag_add("addr", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(_ARM_MNEMS, line, re.I):
                    txt_widget.tag_add("mnem", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(r"\b(?:[wx](?:[0-9]|[12][0-9]|30|zr)|sp|lr|fp)\b", line, re.I):
                    txt_widget.tag_add("reg", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
                for m in re.finditer(r"#?-?\b(?:0x[0-9a-fA-F]+|\d+)\b", line):
                    txt_widget.tag_add("num", f"{ln}.{m.start()}", f"{ln}.{m.end()}")
