# -*- coding: utf-8 -*-
"""workflow_panel — workflow-orientierte Oberflaeche fuer Native Studio (GUI-Refactoring AP2).

Zwei Zonen: links die **Workflow-Rail** (aus der Registry), rechts der **Arbeitsbereich**
(Eingabeformular je Workflow + Start + Ergebnis-Tabs via HighlightedText + next_hints-Buttons).
Die Rail/Formulare werden generisch aus ``controller.workflow_meta()`` gebaut; Ergebnisse
ueber ``build_result_sections`` einheitlich gerendert. Robust: jede Interaktion in try/except.
"""
from __future__ import annotations

try:
    import tkinter as tk
    from tkinter import ttk

    from ui.widgets.highlighted_text import HighlightedText
    from ui.panels.workflow_result import build_result_sections

    _LEXER_OK = HighlightedText is not None

    class WorkflowPanel(ttk.Frame):
        def __init__(self, parent, controller, app):
            super().__init__(parent)
            self.controller = controller
            self.app = app
            self._field_vars = {}
            self._active = None
            try:
                meta = controller.workflow_meta()
            except Exception:
                meta = {"order": [], "workflows": {}}
            self._order = meta.get("order", [])
            self._wf = meta.get("workflows", {})

            # ---- Zone links: Workflow-Rail ----
            rail = ttk.Frame(self)
            rail.pack(side="left", fill="y", padx=(6, 2), pady=6)
            ttk.Label(rail, text="WORKFLOWS", font=("Segoe UI", 8, "bold"),
                      foreground="#666").pack(anchor="w", pady=(0, 4))
            self._rail_btns = {}
            for name in self._order:
                w = self._wf.get(name, {})
                b = ttk.Button(rail, text="%s %s" % (w.get("icon", "•"), w.get("label", name)),
                               width=16, command=lambda n=name: self.select_workflow(n))
                b.pack(anchor="w", pady=1)
                self._rail_btns[name] = b
            ttk.Separator(rail, orient="horizontal").pack(fill="x", pady=6)
            ttk.Label(rail, text="Ergebnis rechts →", foreground="#999",
                      font=("Segoe UI", 7)).pack(anchor="w")

            # ---- Zone rechts aussen: Kontext (Funktionen der aktiven Lib) ----
            ctx = ttk.Frame(self)
            ctx.pack(side="right", fill="y", padx=(2, 6), pady=6)
            ttk.Label(ctx, text="FUNKTIONEN (aktive Lib)", font=("Segoe UI", 8, "bold"),
                      foreground="#666").pack(anchor="w")
            self._ctx_filter = tk.StringVar()
            _cfe = ttk.Entry(ctx, textvariable=self._ctx_filter, width=26)
            _cfe.pack(fill="x", pady=(0, 2))
            _cfe.bind("<KeyRelease>", lambda _e: self._ctx_render())
            self._ctx_list = tk.Listbox(ctx, width=28, height=22, font=("Consolas", 9),
                                        activestyle="none")
            self._ctx_list.pack(fill="both", expand=True)
            self._ctx_list.bind("<Double-Button-1>", self._ctx_pick)
            self._ctx_funcs = []
            ttk.Label(ctx, text="Doppelklick → Entry", foreground="#999",
                      font=("Segoe UI", 7)).pack(anchor="w")

            # ---- Zone rechts: Arbeitsbereich ----
            work = ttk.Frame(self)
            work.pack(side="left", fill="both", expand=True, padx=(2, 6), pady=6)
            self._desc = ttk.Label(work, text="Workflow links waehlen.",
                                   font=("Segoe UI", 9, "italic"), foreground="#555")
            self._desc.pack(anchor="w")
            # ⓘ Info je Workflow (Was/Wie/Anwendung/Ergebnis) — ein-/ausklappbar
            info_bar = ttk.Frame(work)
            info_bar.pack(fill="x", pady=(2, 0))
            self._info_btn = ttk.Button(info_bar, text="ⓘ Info", width=8,
                                        command=self._toggle_info)
            self._info_btn.pack(side="left")
            self._info_shown = False
            self._info_text = tk.Text(work, height=8, wrap="word",
                                      font=("Segoe UI", 9), background="#f6f6f6",
                                      relief="flat", padx=6, pady=4)
            self._info_text.configure(state="disabled")
            self._form = ttk.Frame(work)
            self._form.pack(fill="x", pady=4)
            # Rezept-Leiste (Workflow+Params als Ein-Klick-Vorlage)
            rec = ttk.Frame(work)
            rec.pack(fill="x", pady=(0, 2))
            ttk.Label(rec, text="Rezept:", foreground="#555").pack(side="left")
            self._recipe_var = tk.StringVar()
            self._recipe_cb = ttk.Combobox(rec, textvariable=self._recipe_var, width=22,
                                           state="readonly")
            self._recipe_cb.pack(side="left", padx=3)
            self._recipe_cb.bind("<<ComboboxSelected>>", self._on_recipe_select)
            ttk.Button(rec, text="⭐ Speichern", command=self._save_recipe).pack(side="left", padx=2)
            self._refresh_recipes()
            bar = ttk.Frame(work)
            bar.pack(fill="x")
            self._start = ttk.Button(bar, text="▶ Start", command=self._on_start,
                                     state="disabled")
            self._start.pack(side="left")
            self._status = ttk.Label(bar, text="", foreground="#1a7f37")
            self._status.pack(side="left", padx=8)
            # Ergebnis-Notebook
            self._results = ttk.Notebook(work)
            self._results.pack(fill="both", expand=True, pady=(6, 2))
            # next_hints
            self._hintbar = ttk.Frame(work)
            self._hintbar.pack(fill="x")

            if self._order:
                default = "devirt" if "devirt" in self._wf else self._order[0]
                self.select_workflow(default)

        # ---------------------------------------------------------------
        def _refresh_recipes(self):
            try:
                self._recipes = self.controller.list_recipes()
            except Exception:
                self._recipes = []
            names = [r.get("name", "?") for r in self._recipes]
            try:
                self._recipe_cb["values"] = names
            except Exception:
                pass

        def _save_recipe(self):
            if not self._active:
                return
            try:
                from tkinter import simpledialog
                name = simpledialog.askstring("Rezept speichern",
                                              "Name fuer dieses Rezept:", parent=self)
            except Exception:
                name = None
            if not name:
                return
            try:
                self.controller.save_recipe(name, self._active, self._collect_params())
            except Exception:
                pass
            self._refresh_recipes()

        def _on_recipe_select(self, _e=None):
            nm = self._recipe_var.get()
            rec = next((r for r in getattr(self, "_recipes", []) if r.get("name") == nm), None)
            if not rec:
                return
            self.select_workflow(rec.get("workflow"))
            for k, v in (rec.get("params") or {}).items():
                if k in self._field_vars:
                    try:
                        self._field_vars[k][0].set(str(v))
                    except Exception:
                        pass

        def select_workflow(self, name):
            self._active = name
            w = self._wf.get(name, {})
            self._desc.config(text="%s %s — %s" % (w.get("icon", ""), w.get("label", name),
                                                   w.get("desc", "")))
            info = w.get("info") or w.get("desc", "")
            try:
                self._info_text.configure(state="normal")
                self._info_text.delete("1.0", "end")
                self._info_text.insert("1.0", info)
                self._info_text.configure(state="disabled")
            except Exception:
                pass
            for n, b in self._rail_btns.items():
                try:
                    b.state(["pressed"] if n == name else ["!pressed"])
                except Exception:
                    pass
            for ch in self._form.winfo_children():
                ch.destroy()
            self._field_vars = {}
            for i, f in enumerate(w.get("fields", [])):
                ttk.Label(self._form, text=f.get("label", f["key"]) + ":").grid(
                    row=i, column=0, sticky="w", padx=(0, 6), pady=1)
                var = tk.StringVar(value=str(f.get("default", "")))
                if f.get("type") == "choice":
                    ttk.Combobox(self._form, textvariable=var, width=26, state="readonly",
                                 values=list(f.get("options", []))).grid(
                                     row=i, column=1, sticky="w", pady=1)
                else:
                    ttk.Entry(self._form, textvariable=var, width=28).grid(
                        row=i, column=1, sticky="w", pady=1)
                self._field_vars[f["key"]] = (var, f.get("type", "str"))
            self._start.config(state="normal")

        def _ctx_load(self):
            try:
                self.controller.workflow_load_active_lib(then=self._ctx_refresh)
            except Exception:
                pass

        def _ctx_refresh(self):
            try:
                self._ctx_funcs = self.controller.workflow_functions() or []
            except Exception:
                self._ctx_funcs = []
            self._ctx_render()

        def _ctx_render(self):
            try:
                flt = (self._ctx_filter.get() or "").lower()
                self._ctx_list.delete(0, "end")
                for nm, addr in self._ctx_funcs:
                    if flt and flt not in nm.lower():
                        continue
                    self._ctx_list.insert("end", "%s  %s" % (nm, addr))
            except Exception:
                pass

        def _ctx_pick(self, _e=None):
            try:
                sel = self._ctx_list.curselection()
                if not sel:
                    return
                nm = self._ctx_list.get(sel[0]).split("  ")[0].strip()
                if "entry" in self._field_vars:
                    self._field_vars["entry"][0].set(nm)
            except Exception:
                pass

        def _make_addrs_clickable(self, pane):
            try:
                import re as _re
                t = pane.text
                t.tag_configure("waddr", foreground="#2a6fdb", underline=True)
                content = t.get("1.0", "end-1c")
                for m in _re.finditer(r"0x[0-9a-fA-F]{4,}", content):
                    t.tag_add("waddr", "1.0+%dc" % m.start(), "1.0+%dc" % m.end())
                t.tag_bind("waddr", "<Button-1>", lambda e, p=pane: self._on_addr_click(p, e))
                t.tag_bind("waddr", "<Enter>", lambda e, w=t: w.config(cursor="hand2"))
                t.tag_bind("waddr", "<Leave>", lambda e, w=t: w.config(cursor=""))
            except Exception:
                pass

        def _on_addr_click(self, pane, event):
            try:
                t = pane.text
                idx = t.index("@%d,%d" % (event.x, event.y))
                ranges = t.tag_ranges("waddr")
                word = None
                for i in range(0, len(ranges), 2):
                    s, e = ranges[i], ranges[i + 1]
                    if t.compare(s, "<=", idx) and t.compare(idx, "<", e):
                        word = t.get(s, e)
                        break
                if word:
                    self._show_disasm(word)
            except Exception:
                pass

        def _show_disasm(self, addr):
            try:
                txt = self.controller.workflow_disasm(addr)
            except Exception as e:
                txt = "Disasm-Fehler: %r" % e
            if not txt:
                txt = "Keine Disasm — Lib geladen? (erst 'Oeffnen'). Addr=%s" % addr
            try:
                frame = getattr(self, "_disasm_frame", None)
                if frame is None or not frame.winfo_exists():
                    frame = ttk.Frame(self._results)
                    self._results.add(frame, text="🔎 Disasm")
                    self._disasm_frame = frame
                    if _LEXER_OK:
                        self._disasm_pane = HighlightedText(frame, lexer="disasm", height=14)
                    else:
                        self._disasm_pane = tk.Text(frame, wrap="none", height=14,
                                                    font=("Consolas", 9))
                    self._disasm_pane.pack(fill="both", expand=True)
                if _LEXER_OK:
                    self._disasm_pane.set_text(txt, "disasm")
                else:
                    self._disasm_pane.configure(state="normal")
                    self._disasm_pane.delete("1.0", "end")
                    self._disasm_pane.insert("1.0", txt)
                    self._disasm_pane.configure(state="disabled")
                self._results.select(frame)
            except Exception:
                pass

        def _toggle_info(self):
            self._info_shown = not getattr(self, "_info_shown", False)
            try:
                if self._info_shown:
                    self._info_text.pack(fill="x", pady=(2, 4), before=self._form)
                    self._info_btn.config(text="ⓘ Info ▲")
                else:
                    self._info_text.pack_forget()
                    self._info_btn.config(text="ⓘ Info")
            except Exception:
                pass

        def _collect_params(self):
            params = {}
            for k, (var, typ) in self._field_vars.items():
                v = var.get().strip()
                if typ == "int":
                    try:
                        v = int(v)
                    except Exception:
                        v = 0
                params[k] = v
            return params

        def _on_start(self):
            if not self._active:
                return
            params = self._collect_params()
            self._start.config(state="disabled")
            self._status.config(text="laeuft …", foreground="#b36b00")
            try:
                self.controller.run_workflow(self._active, params, self._show_result)
            except Exception as e:
                self._status.config(text="Fehler: %r" % e, foreground="#b5392e")
                self._start.config(state="normal")

        def _show_result(self, result):
            self._start.config(state="normal")
            # "Oeffnen" (open_lib) fuellt die Kontext-Funktionsliste automatisch
            if (result or {}).get("workflow") == "open_lib":
                try:
                    self._ctx_load()
                except Exception:
                    pass
            ok = (result or {}).get("ok", True)
            self._status.config(text="fertig" if ok else "Fehler",
                                foreground="#1a7f37" if ok else "#b5392e")
            for ch in self._results.winfo_children():
                ch.destroy()
            try:
                sections = build_result_sections(result or {})
            except Exception as e:
                sections = [{"title": "Fehler", "lexer": "text", "text": repr(e)}]
            for sec in sections:
                frame = ttk.Frame(self._results)
                if _LEXER_OK:
                    pane = HighlightedText(frame, lexer=sec.get("lexer", "text"), height=14)
                    pane.pack(fill="both", expand=True)
                    pane.set_text(sec.get("text", ""), sec.get("lexer", "text"))
                    self._make_addrs_clickable(pane)
                else:
                    t = tk.Text(frame, wrap="none", height=14, font=("Consolas", 9))
                    t.insert("1.0", sec.get("text", "")); t.config(state="disabled")
                    t.pack(fill="both", expand=True)
                self._results.add(frame, text=sec.get("title", "?"))
            # next_hints
            for ch in self._hintbar.winfo_children():
                ch.destroy()
            hints = (result or {}).get("next_hints") or []
            if hints:
                ttk.Label(self._hintbar, text="Naechster Schritt:",
                          foreground="#555").pack(side="left", padx=(0, 4))
                for h in hints:
                    if h in self._wf:
                        ttk.Button(self._hintbar, text="%s %s" % (
                            self._wf[h].get("icon", ""), self._wf[h].get("label", h)),
                            command=lambda n=h: self.select_workflow(n)).pack(side="left", padx=2)
                    else:
                        ttk.Label(self._hintbar, text="[%s]" % h,
                                  foreground="#999").pack(side="left", padx=2)

except Exception:  # pragma: no cover - ohne tkinter (headless)
    WorkflowPanel = None
