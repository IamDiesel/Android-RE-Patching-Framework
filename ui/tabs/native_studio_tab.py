"""NativeStudioTab — View des Analysezweigs „🧬 Native Studio".

Layout an das Smali Studio angelehnt (3 Spalten, ohne „Steuerung & Metadaten"):
  links   — Outline / Historie
  mitte   — Disassembly (read-only) oben · Eingaben/Run-Panel unten
  rechts  — Register/Flags oben · Memory + XREF/CallGraph (Notebook) unten
  unten   — Coverage / Unknown-Instr (spannt die Breite)
Logs laufen in die GEMEINSAME Main Console (app.log), kein eigenes Log-Feld.

Phase A1a: ▶ Run (run-to-completion via services.native_emu_service) + Register-,
Memory- und Coverage-Anzeige. Favoriten-Dropdown (native_run) folgt in A1b.
"""
import tkinter as tk
from tkinter import ttk

from ui.controllers.native_studio_controller import NativeStudioController


def _add_tooltip(widget, text):
    """Kleiner Hover-Tooltip (erklaert, was ein Button tut)."""
    state = {"win": None}
    def _enter(_e):
        if state["win"] is not None:
            return
        try:
            x = widget.winfo_rootx() + 14
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            w = tk.Toplevel(widget); w.wm_overrideredirect(True); w.wm_geometry(f"+{x}+{y}")
            tk.Label(w, text=text, background="#FFFFE0", relief="solid", borderwidth=1,
                     justify="left", font=("Segoe UI", 8), wraplength=380).pack()
            state["win"] = w
        except Exception:
            state["win"] = None
    def _leave(_e):
        w = state["win"]; state["win"] = None
        if w is not None:
            try: w.destroy()
            except Exception: pass
    widget.bind("<Enter>", _enter); widget.bind("<Leave>", _leave)


class NativeStudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.arg_rows = []          # [(label_text, ttk.Entry)] — x0..x7 + frei ergaenzte
        self._arg_host = None       # Frame, in das Arg-Zeilen kommen
        self.create_widgets()
        self.controller = NativeStudioController(self, app)
        self._wire_buttons()

    # ---- Aufbau -----------------------------------------------------------
    def create_widgets(self):
        # --- Top-Bar ---
        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=6, pady=5)
        self.btn_load = ttk.Button(top, text="📥 .so laden")
        self.btn_unpack = ttk.Button(top, text="📦 entpacken")
        self.btn_self = ttk.Button(top, text="▶ Engine-Selbsttest")
        self.btn_run = ttk.Button(top, text="▶ Run")
        self.btn_disasm = ttk.Button(top, text="🔎 Disasm ab Start")
        self.btn_clear = ttk.Button(top, text="🧹 Leeren")
        self.btn_back = ttk.Button(top, text="◀", width=3)
        self.btn_fwd = ttk.Button(top, text="▶", width=3)
        self.btn_search = ttk.Button(top, text="🔍 Suche")
        self.btn_decomp = ttk.Button(top, text="🧊 C")
        self.btn_decomp_cancel = ttk.Button(top, text="⏹ Abbrechen", state="disabled")
        for b in (self.btn_load, self.btn_unpack, self.btn_self, self.btn_run, self.btn_disasm,
                  self.btn_clear, self.btn_back, self.btn_fwd, self.btn_search, self.btn_decomp,
                  self.btn_decomp_cancel):
            b.pack(side="left", padx=4)
        self.lbl_loaded = ttk.Label(top, text="— keine Lib geladen —", foreground="gray")
        self.lbl_loaded.pack(side="right", padx=8)
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=140)

        # --- Coverage unten (spannt Breite) — zuerst packen, damit es unten sitzt ---
        f_cov = ttk.LabelFrame(self, text="Coverage / Unknown-Instr")
        f_cov.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
        self.cov_text = tk.Text(f_cov, height=4, wrap="word", font=("Consolas", 9))
        csb = ttk.Scrollbar(f_cov, orient="vertical", command=self.cov_text.yview)
        self.cov_text.configure(yscrollcommand=csb.set)
        self.cov_text.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")

        # --- 3 Spalten ---
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side="top", fill="both", expand=True, padx=6, pady=5)

        # SPALTE 1: Outline / Historie
        left_nb = ttk.Notebook(paned)
        f_out = ttk.Frame(left_nb)
        self.outline = ttk.Treeview(f_out, columns=("kind", "name", "addr"), show="headings", height=20)
        self.outline.heading("kind", text="Typ"); self.outline.column("kind", width=60, anchor="w")
        self.outline.heading("name", text="Symbol"); self.outline.column("name", width=230, anchor="w")
        self.outline.heading("addr", text="Adresse"); self.outline.column("addr", width=86, anchor="w")
        self.outline.tag_configure("imp", foreground="#9aa0a6")   # Importe/undefiniert (0x0) ausgegraut
        sb = ttk.Scrollbar(f_out, orient="vertical", command=self.outline.yview)
        self.outline.configure(yscrollcommand=sb.set)
        self.outline.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        left_nb.add(f_out, text="Outline")

        f_hist = ttk.Frame(left_nb)
        self.tree_history = ttk.Treeview(f_hist, show="tree", height=20)
        self.tree_history.tag_configure("current", foreground="#1a7f37")
        hsb = ttk.Scrollbar(f_hist, orient="vertical", command=self.tree_history.yview)
        self.tree_history.configure(yscrollcommand=hsb.set)
        self.tree_history.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")
        left_nb.add(f_hist, text="Historie")
        paned.add(left_nb, weight=2)

        # SPALTE 2 (Mitte): Disassembly oben + Run-Panel unten
        center = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(center, weight=4)

        f_dis = ttk.LabelFrame(center, text="Disassembly (Read-Only)")
        # Symbol-/Funktionskopf als Label UEBER der Tabelle (keine Instruktionszeile)
        self.disasm_sym = ttk.Label(f_dis, text="—", font=("Consolas", 9, "italic"), anchor="w")
        self.disasm_sym.pack(side="top", fill="x", padx=4, pady=(2, 0))
        try:
            _st = ttk.Style(); _st.configure("Disasm.Treeview", font=("Consolas", 9), rowheight=18)
        except Exception:
            pass
        _dwrap = ttk.Frame(f_dis); _dwrap.pack(side="top", fill="both", expand=True)
        self.disasm = ttk.Treeview(_dwrap, columns=("mark", "addr", "bytes", "mnem", "ops"),
                                   show="headings", height=16, style="Disasm.Treeview")
        self.disasm.heading("mark", text="▶"); self.disasm.column("mark", width=26, anchor="center", stretch=False)
        self.disasm.heading("addr", text="Adresse"); self.disasm.column("addr", width=96, anchor="w", stretch=False)
        self.disasm.heading("bytes", text="Rohbytes"); self.disasm.column("bytes", width=84, anchor="w", stretch=False)
        self.disasm.heading("mnem", text="Mnemonic"); self.disasm.column("mnem", width=84, anchor="w", stretch=False)
        self.disasm.heading("ops", text="Operanden"); self.disasm.column("ops", width=260, anchor="w")
        self.disasm.tag_configure("data", foreground="#9aa0a6")
        self.disasm.tag_configure("branch", foreground="#1a66d0")
        self.disasm.tag_configure("cur", background="#fff3bf")
        self._disasm_targets = {}
        dsb = ttk.Scrollbar(_dwrap, orient="vertical", command=self.disasm.yview)
        self.disasm.configure(yscrollcommand=dsb.set)
        self.disasm.pack(side="left", fill="both", expand=True)
        dsb.pack(side="right", fill="y")
        center.add(f_dis, weight=3)

        f_run = ttk.LabelFrame(center, text="Eingaben / Run-Panel")
        self._build_run_panel(f_run)
        center.add(f_run, weight=2)

        # SPALTE 3 (rechts): Register/Flags oben + Memory/XREF Notebook unten
        right = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(right, weight=3)

        f_reg = ttk.LabelFrame(right, text="Register / Flags")
        # flags-Label ZUERST unten packen (sonst quetscht das expandierende Treeview es weg)
        self.flags_lbl = ttk.Label(f_reg, text="Flags: —", font=("Consolas", 9))
        self.flags_lbl.pack(side="bottom", anchor="w", padx=4, pady=2)
        self.regs_tree = ttk.Treeview(f_reg, columns=("reg", "val"), show="headings", height=10)
        self.regs_tree.heading("reg", text="Reg"); self.regs_tree.column("reg", width=70, anchor="w")
        self.regs_tree.heading("val", text="Wert"); self.regs_tree.column("val", width=170, anchor="w")
        self.regs_tree.tag_configure("ret", foreground="#0c5e32", background="#e5f6ec")
        rsb = ttk.Scrollbar(f_reg, orient="vertical", command=self.regs_tree.yview)
        self.regs_tree.configure(yscrollcommand=rsb.set)
        self.regs_tree.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        right.add(f_reg, weight=1)

        mem_nb = ttk.Notebook(right)
        right.add(mem_nb, weight=2)

        # Reiter Memory
        f_mem = ttk.Frame(mem_nb)
        mbar = ttk.Frame(f_mem); mbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(mbar, text="Adresse:").pack(side="left")
        self.mem_addr = ttk.Entry(mbar, width=14); self.mem_addr.pack(side="left", padx=4)
        self.btn_memshow = ttk.Button(mbar, text="anzeigen"); self.btn_memshow.pack(side="left", padx=4)
        self.mem_view = tk.Text(f_mem, height=8, wrap="none", font=("Consolas", 9))
        msb = ttk.Scrollbar(f_mem, orient="vertical", command=self.mem_view.yview)
        self.mem_view.configure(yscrollcommand=msb.set)
        self.mem_view.pack(side="left", fill="both", expand=True)
        msb.pack(side="right", fill="y")
        mem_nb.add(f_mem, text="Memory")

        # Reiter XREF / CallGraph (bisheriges Panel, hierher verschoben)
        f_xr = ttk.Frame(mem_nb)
        xbar = ttk.Frame(f_xr); xbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(xbar, text="Adresse:").pack(side="left")
        self.xref_addr = ttk.Entry(xbar, width=14); self.xref_addr.pack(side="left", padx=4)
        self.btn_xref = ttk.Button(xbar, text="🔗 XREF"); self.btn_xref.pack(side="left", padx=4)
        self.xref_title = ttk.Label(xbar, text="—", foreground="gray"); self.xref_title.pack(side="left", padx=8)
        self.xref_tree = ttk.Treeview(f_xr, columns=("art", "addr", "func"), show="headings", height=8)
        self.xref_tree.heading("art", text="Art"); self.xref_tree.column("art", width=80, anchor="w")
        self.xref_tree.heading("addr", text="Adresse"); self.xref_tree.column("addr", width=110, anchor="w")
        self.xref_tree.heading("func", text="Funktion"); self.xref_tree.column("func", width=220, anchor="w")
        xsb = ttk.Scrollbar(f_xr, orient="vertical", command=self.xref_tree.yview)
        self.xref_tree.configure(yscrollcommand=xsb.set)
        self.xref_tree.pack(side="left", fill="both", expand=True)
        xsb.pack(side="right", fill="y")
        mem_nb.add(f_xr, text="XREF / CallGraph")

        self.append_status("Bereit. '📥 .so laden' → Funktion im Outline doppelklicken → Entry/Args setzen → '▶ Run'.")

    def _build_run_panel(self, parent):
        body = ttk.Frame(parent); body.pack(side="top", fill="both", expand=True, padx=4, pady=3)

        # Arbeitsansicht (Run-Favoriten) — A1b, vorerst deaktiviert
        favrow = ttk.Frame(body); favrow.pack(side="top", fill="x", pady=(0, 3))
        ttk.Label(favrow, text="Arbeitsansicht:").pack(side="left")
        self.fav_combo = ttk.Combobox(favrow, values=[], width=28, state="disabled")
        self.fav_combo.pack(side="left", padx=4)
        self.btn_fav_save = ttk.Button(favrow, text="💾 Speichern", state="disabled")
        self.btn_fav_save.pack(side="left", padx=2)
        self.btn_fav_upd = ttk.Button(favrow, text="✎ Aktualisieren", state="disabled")
        self.btn_fav_upd.pack(side="left", padx=2)
        ttk.Label(favrow, text="(Favoriten: A1b)", foreground="gray").pack(side="left", padx=6)

        # Entry
        erow = ttk.Frame(body); erow.pack(side="top", fill="x", pady=2)
        ttk.Label(erow, text="Entry:", width=8).pack(side="left")
        self.run_entry = ttk.Entry(erow); self.run_entry.pack(side="left", fill="x", expand=True, padx=4)

        # ▶ Run-Leiste ZUERST unten packen (vor dem expandierenden cols-Frame)
        runbar = ttk.Frame(body); runbar.pack(side="bottom", fill="x", pady=(2, 0))
        self.btn_run2 = ttk.Button(runbar, text="▶ Run")
        self.btn_run2.pack(side="right")

        # Args + Mem nebeneinander
        cols = ttk.Frame(body); cols.pack(side="top", fill="both", expand=True, pady=2)

        argbox = ttk.LabelFrame(cols, text="Argumente (x0–x7, + frei → Stack)")
        argbox.pack(side="left", fill="both", expand=True, padx=(0, 3))
        # ➕-Button ZUERST unten packen → bleibt bei beliebig vielen Argumenten erreichbar
        abar = ttk.Frame(argbox); abar.pack(side="bottom", fill="x", padx=4, pady=2)
        self.btn_arg_add = ttk.Button(abar, text="➕ Argument (→ Stack)", command=self._on_add_arg)
        self.btn_arg_add.pack(side="left")
        # scrollbarer Bereich fuer die Arg-Zeilen (Canvas + Scrollbar)
        self._arg_canvas = tk.Canvas(argbox, height=140, highlightthickness=0)
        asb = ttk.Scrollbar(argbox, orient="vertical", command=self._arg_canvas.yview)
        self._arg_canvas.configure(yscrollcommand=asb.set)
        asb.pack(side="right", fill="y")
        self._arg_canvas.pack(side="left", fill="both", expand=True, padx=4, pady=2)
        self._arg_host = ttk.Frame(self._arg_canvas)
        self._arg_winid = self._arg_canvas.create_window((0, 0), window=self._arg_host, anchor="nw")
        self._arg_host.bind("<Configure>",
                            lambda e: self._arg_canvas.configure(scrollregion=self._arg_canvas.bbox("all")))
        self._arg_canvas.bind("<Configure>",
                              lambda e: self._arg_canvas.itemconfigure(self._arg_winid, width=e.width))
        # Mausrad scrollt den Arg-Bereich, wenn der Zeiger darueber ist
        self._arg_canvas.bind("<Enter>", lambda e: self._arg_canvas.bind_all(
            "<MouseWheel>", lambda ev: self._arg_canvas.yview_scroll(int(-ev.delta / 120), "units")))
        self._arg_canvas.bind("<Leave>", lambda e: self._arg_canvas.unbind_all("<MouseWheel>"))
        for i in range(8):
            self._add_arg_row(f"x{i}")

        membox = ttk.LabelFrame(cols, text="Speicher-Vorbelegung  (je Zeile: 0xADDR: hexbytes)")
        membox.pack(side="left", fill="both", expand=True, padx=(3, 0))
        self.mem_text = tk.Text(membox, height=5, wrap="none", font=("Consolas", 9))
        self.mem_text.pack(side="top", fill="both", expand=True, padx=4, pady=2)
        sprow = ttk.Frame(membox); sprow.pack(side="bottom", fill="x", padx=4, pady=2)
        ttk.Label(sprow, text="SP:").pack(side="left")
        self.run_sp = ttk.Entry(sprow, width=16); self.run_sp.pack(side="left", padx=4)
        ttk.Label(sprow, text="maxins:").pack(side="left")
        self.run_maxins = ttk.Entry(sprow, width=10); self.run_maxins.insert(0, "2000000")
        self.run_maxins.pack(side="left", padx=4)

    def _add_arg_row(self, label):
        row = ttk.Frame(self._arg_host); row.pack(side="top", fill="x", pady=1)
        ttk.Label(row, text=label, width=4, font=("Consolas", 9)).pack(side="left")
        ent = ttk.Entry(row, font=("Consolas", 9)); ent.pack(side="left", fill="x", expand=True, padx=3)
        self.arg_rows.append((label, ent))

    def _on_add_arg(self):
        self._add_arg_row(f"x{len(self.arg_rows)}")

    def _wire_buttons(self):
        self.btn_load.config(command=self.controller.load_so_dialog)
        self.btn_unpack.config(command=self.controller.unpack_dialog)
        self.btn_self.config(command=self.controller.engine_selftest)
        self.btn_run.config(command=self.controller.run_emulation)
        self.btn_run2.config(command=self.controller.run_emulation)
        self.btn_disasm.config(command=self.controller.disasm_entry)
        self.btn_clear.config(command=self.clear)
        self.btn_xref.config(command=self._on_xref_button)
        self.btn_memshow.config(command=self.controller.show_memory)
        self.btn_back.config(command=self.controller.navigate_back)
        self.btn_fwd.config(command=self.controller.navigate_forward)
        self.btn_search.config(command=self.controller.open_search)
        self.btn_decomp.config(command=self.controller.decompile_button)
        self.btn_decomp_cancel.config(command=self.controller.cancel_decompile)
        self.disasm.bind("<Double-Button-1>", self._on_disasm_double)
        self.disasm_menu = tk.Menu(self.disasm, tearoff=0)
        self.disasm_menu.add_command(label="Zeile kopieren", command=self._copy_disasm_row)
        self.disasm_menu.add_command(label="Alles kopieren", command=lambda: self._disasm_copy_sel(all_rows=True))
        self.disasm.bind("<Button-3>", self._post_disasm_menu)
        self.disasm.bind("<Control-a>", self._disasm_select_all)
        self.disasm.bind("<Control-A>", self._disasm_select_all)
        self.disasm.bind("<Control-c>", self._disasm_copy_sel)
        self.disasm.bind("<Control-C>", self._disasm_copy_sel)
        self.outline.bind("<Double-Button-1>", self._on_outline_double)
        self.xref_tree.bind("<Double-Button-1>", self._on_xref_double)
        self.tree_history.bind("<Double-Button-1>", self._on_history_double)
        _add_tooltip(self.btn_load, "Laedt eine .so/ELF und baut danach automatisch den XREF/CallGraph-Index.")
        _add_tooltip(self.btn_unpack, "Entschluesselt eine gepackte Lib aus APK/.so ueber das Decrypter-Plugin.")
        _add_tooltip(self.btn_self, "Prueft, ob die Emulations-Engine arbeitet (rechnet Testinstruktionen).")
        _add_tooltip(self.btn_run, "Fuehrt die Funktion aus dem Run-Panel aus (Entry + Args + Speicher) und zeigt Register/Memory/Coverage.")
        _add_tooltip(self.btn_disasm, "Disassembly ab Entry-Punkt bzw. erstem Symbol/Segment.")
        _add_tooltip(self.btn_search, "Volltextsuche ueber den Index (Funktion/String/Adresse).")
        _add_tooltip(self.btn_decomp, "Dekompiliert die aktuelle Funktion zu C via Ghidra (gecacht).")
        _add_tooltip(self.btn_arg_add, "Weiteres Argument hinzufuegen; Argumente ab x8 werden auf den Stack gelegt (SP noetig).")

    # ---- vom Controller aufgerufen ----------------------------------------
    def append_status(self, msg):
        """Status/Fehler laufen in die GEMEINSAME Main Console (kein eigenes Log-Feld mehr)."""
        try:
            self.app.log(f"[NativeStudio] {msg}")
        except Exception:
            pass

    def set_coverage(self, text):
        try:
            self.cov_text.delete("1.0", "end"); self.cov_text.insert("end", text + "\n")
        except Exception:
            pass

    def start_progress(self):
        try:
            self.progress.pack(side="right", padx=8); self.progress.start(12)
        except Exception:
            pass

    def stop_progress(self):
        try:
            self.progress.stop(); self.progress.pack_forget()
        except Exception:
            pass

    def set_loaded(self, text):
        try:
            self.lbl_loaded.config(text=text)
        except Exception:
            pass

    def set_outline(self, rows):
        for iid in self.outline.get_children():
            self.outline.delete(iid)
        for row in rows:
            kind = row[0]
            name = row[1] if len(row) > 1 else ""
            addr = row[2] if len(row) > 2 else ""
            tags = ("imp",) if kind in ("import", "data") else ()
            self.outline.insert("", "end", values=(kind, name, addr), tags=tags)

    def select_outline_addr(self, addr):
        """Markiert die Outline-Zeile mit passender Adresse (nach Navigation/Suche)."""
        target = f"0x{addr:x}"
        for iid in self.outline.get_children():
            vals = self.outline.item(iid, "values")
            if len(vals) > 2 and vals[2] == target:
                try:
                    self.outline.selection_set(iid); self.outline.see(iid); self.outline.focus(iid)
                except Exception:
                    pass
                return

    def set_disasm_rows(self, sym_header, rows):
        """rows = Liste von (mark, addr_str, bytes_str, mnem, ops, tags, target_or_None)."""
        try:
            self.disasm_sym.config(text=sym_header or "—")
        except Exception:
            pass
        self._disasm_targets = {}
        for iid in self.disasm.get_children():
            self.disasm.delete(iid)
        cur_iid = None
        for r in rows:
            r = (list(r) + [None] * 7)[:7]
            mark, addr_s, bytes_s, mnem, ops, tags, target = r
            if isinstance(tags, str):
                tags = (tags,)
            elif tags:
                tags = tuple(tags)
            else:
                tags = ()
            iid = self.disasm.insert("", "end", values=(mark or "", addr_s, bytes_s, mnem, ops), tags=tags)
            if target is not None:
                self._disasm_targets[iid] = target
            if mark:
                cur_iid = iid
        if cur_iid:
            try:
                self.disasm.see(cur_iid); self.disasm.selection_set(cur_iid)
            except Exception:
                pass

    def set_disasm(self, text):
        """Rueckwaertskompatibel: Rohtext -> eine Operanden-Spalte (Fallback)."""
        self.set_disasm_rows("—", [("", "", "", "", ln, (), None) for ln in str(text).splitlines()])

    def set_xref(self, title, rows):
        self.xref_title.config(text=title)
        for iid in self.xref_tree.get_children():
            self.xref_tree.delete(iid)
        for art, addr, func in rows:
            self.xref_tree.insert("", "end", values=(art, addr, func))

    def set_registers(self, snap):
        """snap = {ret, x:[..31], sp, lr, pc, flags:{N,Z,C,V}}."""
        t = self.regs_tree
        for iid in t.get_children():
            t.delete(iid)
        try:
            t.insert("", "end", values=("x0◂ret", f"0x{snap['ret']:x}"), tags=("ret",))
            xs = snap.get("x", [])
            for i in range(1, min(31, len(xs))):
                t.insert("", "end", values=(f"x{i}", f"0x{xs[i]:x}"))
            t.insert("", "end", values=("sp", f"0x{snap['sp']:x}"))
            t.insert("", "end", values=("lr", f"0x{snap['lr']:x}"))
            t.insert("", "end", values=("pc", f"0x{snap['pc']:x}"))
            f = snap.get("flags", {})
            self.flags_lbl.config(text=f"Flags:  N={f.get('N','?')}  Z={f.get('Z','?')}  C={f.get('C','?')}  V={f.get('V','?')}")
        except Exception:
            pass

    def set_memory(self, addr, data):
        """Hexdump 16 Bytes/Zeile ab addr."""
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hx = " ".join(f"{b:02x}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"0x{addr + i:08x}  {hx:<47}  {asc}")
        self.mem_view.delete("1.0", "end")
        self.mem_view.insert("end", "\n".join(lines))

    def get_run_config(self):
        """Liest das Run-Panel → dict {entry, args[], mem_overrides[], sp, maxins}."""
        def _pi(s):
            s = (s or "").strip()
            if not s:
                return 0
            return int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        entry = self.run_entry.get().strip()
        args = [_pi(ent.get()) for _lbl, ent in self.arg_rows]
        # fuehrende 8 behalten; hintere leere (0) am Ende abschneiden
        while len(args) > 8 and args[-1] == 0:
            args.pop()
        mem = []
        for line in self.mem_text.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            a, h = line.split(":", 1)
            h = "".join(h.split())
            try:
                mem.append({"addr": int(a.strip(), 0), "bytes": bytes.fromhex(h)})
            except Exception:
                pass
        sp_raw = self.run_sp.get().strip()
        sp = _pi(sp_raw) if sp_raw else None
        mx_raw = self.run_maxins.get().strip()
        try:
            mx = int(mx_raw, 0) if mx_raw else 2000000
        except Exception:
            mx = 2000000
        return {"entry": entry, "args": args, "mem_overrides": mem, "sp": sp, "maxins": mx}

    @staticmethod
    def _hex_at(text, col):
        import re
        first = None
        for m in re.finditer(r"0x[0-9a-fA-F]+", text):
            if first is None:
                first = m.group(0)
            if m.start() <= col <= m.end():
                return m.group(0)
        return first

    def _on_disasm_double(self, event):
        if not getattr(self, "controller", None):
            return
        iid = self.disasm.focus()
        if not iid:
            return
        tgt = getattr(self, "_disasm_targets", {}).get(iid)
        if tgt is None:
            import re as _re
            vals = self.disasm.item(iid, "values")
            ops = vals[4] if len(vals) > 4 else ""
            m = _re.search(r"0x[0-9a-fA-F]+", ops)
            if not m:
                return
            try:
                tgt = int(m.group(0), 16)
            except Exception:
                return
        self.xref_addr.delete(0, "end"); self.xref_addr.insert(0, f"0x{tgt:x}")
        self.controller.navigate(tgt)

    def _copy_disasm_row(self):
        iid = self.disasm.focus()
        if not iid:
            return
        vals = self.disasm.item(iid, "values")
        txt = "  ".join(str(v) for v in vals[1:])  # ohne Marker-Spalte
        try:
            self.clipboard_clear(); self.clipboard_append(txt)
        except Exception:
            pass

    def _post_disasm_menu(self, event):
        try:
            iid = self.disasm.identify_row(event.y)
            if iid:
                self.disasm.focus(iid); self.disasm.selection_set(iid)
            self.disasm_menu.post(event.x_root, event.y_root)
        except Exception:
            pass

    def _on_outline_double(self, event):
        if not getattr(self, "controller", None):
            return
        sel = self.outline.focus()
        if not sel:
            return
        vals = self.outline.item(sel, "values")
        addr = vals[2] if len(vals) > 2 else ""
        if not addr or addr == "0x0":
            self.controller._log("Importiertes/undefiniertes Symbol (0x0) — in dieser Lib nicht definiert, "
                                 "kein Sprungziel. Nur Funktionen mit echter Adresse sind ausfuehr-/analysierbar.")
            return
        try:
            a = int(addr, 16)
        except Exception:
            return
        kind = vals[0] if len(vals) > 0 else ""
        if kind == "data":
            self.controller._log(f"'{vals[1] if len(vals) > 1 else addr}' ist ein Daten-Symbol "
                                 "(RTTI/vtable/Konstante), kein Code — zeige nur XREF (wer referenziert es).")
            self.controller.navigate(a, set_entry=False)
            return
        # Doppelklick auf definierte Funktion: Entry vorbelegen + navigieren (navigate spiegelt XREF/Outline)
        self.run_entry.delete(0, "end"); self.run_entry.insert(0, addr)
        self.controller.navigate(a, set_entry=True)

    def _on_xref_double(self, event):
        if not getattr(self, "controller", None):
            return
        sel = self.xref_tree.focus()
        if not sel:
            return
        vals = self.xref_tree.item(sel, "values")
        addr = vals[1] if len(vals) > 1 else ""
        if addr and str(addr).startswith("0x"):
            self.controller.navigate(int(addr, 16))

    def _on_xref_button(self):
        if not getattr(self, "controller", None):
            return
        tok = self.xref_addr.get().strip()
        try:
            a = int(tok, 16) if tok.lower().startswith("0x") else int(tok)
        except Exception:
            self.append_status(f"Ungueltige Adresse: {tok!r}")
            return
        self.controller.navigate(a)

    def set_decompile_running(self, running):
        """Aktiviert den ⏹-Abbrechen-Knopf waehrend eines Ghidra-Laufs und sperrt solange 🧊 C."""
        try:
            self.btn_decomp_cancel.config(state=("normal" if running else "disabled"))
            self.btn_decomp.config(state=("disabled" if running else "normal"))
        except Exception:
            pass

    @staticmethod
    def _short_name(name):
        """Lesbarer Kurzname aus gemangletem Itanium-Namen: letzte laengen-praefixierte
        <len><exakt n Zeichen>-Komponente (…E19get_untyped_deleterEv -> get_untyped_deleter)."""
        if not name or not name.startswith("_Z"):
            return name
        i, L, last = 0, len(name), None
        while i < L:
            c = name[i]
            if c in "CD" and i + 1 < L and name[i + 1] in "0123" and (i + 2 >= L or not name[i + 2].isdigit()):
                i += 2  # Ctor/Dtor-Marker -> Klassenname (last) bleibt stehen
                continue
            if c.isdigit():
                j = i
                while j < L and name[j].isdigit():
                    j += 1
                n = int(name[i:j])
                ident = name[j:j + n]
                if n > 0 and len(ident) == n and (ident[:1].isalpha() or ident[:1] == "_"):
                    last = ident
                i = j + n if n > 0 else j
            else:
                i += 1
        return last or name

    def _c_tab_label(self, title, code, addr=None):
        """Tab-Beschriftung: bevorzugt Ghidras demanglen Namen aus dem C-Header, sonst Kurzname;
        Adresse angehaengt -> eindeutig auch bei Ueberladungen."""
        import re as _re
        cname = None
        m = _re.search(r"//\s*(.+?)\s*\(Ghidra-Decompile", code or "")
        if m:
            cname = m.group(1).strip()
        if not cname:
            cname = self._short_name(title)
        cn = cname if len(cname) <= 24 else "…" + cname[-23:]
        return f"{cn} ·{addr:x}" if addr is not None else cn

    @staticmethod
    def _full_sig_from_c(code):
        """Vollqualifizierte Signaturzeile aus dem Ghidra-C (fuer Tab-Tooltip)."""
        if not code:
            return None
        lines = code.splitlines()
        i, n, in_block = 0, len(lines), False
        while i < n:
            s = lines[i].strip()
            if not s or s.startswith("//"):
                i += 1; continue
            if in_block:
                if "*/" in s:
                    in_block = False
                i += 1; continue
            if s.startswith("/*"):
                if "*/" not in s:
                    in_block = True
                i += 1; continue
            parts = []
            while i < n:
                t = lines[i].strip()
                parts.append(t)
                if "{" in t or ")" in t:
                    break
                i += 1
            sig = " ".join(parts)
            cut = sig.find("{")
            if cut >= 0:
                sig = sig[:cut]
            return sig.strip().rstrip("{").strip()[:400] or None
        return None

    def _c_tab_tip_setup(self):
        """Gelber Mouseover-Tooltip ueber dem Tab-Streifen (zeigt frame._c_full)."""
        nb = self._c_nb
        tip = tk.Toplevel(nb)
        tip.withdraw()
        try:
            tip.overrideredirect(True)
        except Exception:
            pass
        lbl = tk.Label(tip, justify="left", bg="#ffffe0", relief="solid", borderwidth=1,
                       font=("Consolas", 9), wraplength=760)
        lbl.pack(ipadx=4, ipady=2)
        self._c_tip, self._c_tip_lbl, self._c_tip_idx = tip, lbl, None
        nb.bind("<Motion>", self._c_tab_tip_motion)
        nb.bind("<Leave>", lambda e: self._c_tip.withdraw())

    def _c_tab_tip_motion(self, event):
        nb = self._c_nb
        tip = getattr(self, "_c_tip", None)
        if tip is None:
            return
        try:
            idx = nb.index("@%d,%d" % (event.x, event.y))
            tabs = nb.tabs()
            w = nb.nametowidget(tabs[idx])
            full = getattr(w, "_c_full", "")
        except Exception:
            tip.withdraw(); self._c_tip_idx = None
            return
        if not full:
            tip.withdraw(); self._c_tip_idx = None
            return
        if idx != self._c_tip_idx:
            self._c_tip_lbl.config(text=full)
            self._c_tip_idx = idx
        try:
            tip.geometry("+%d+%d" % (event.x_root + 12, event.y_root + 20))
            tip.deiconify()
        except Exception:
            pass

    def show_decompile(self, title, code, addr=None, base=None):
        """C-Dekompilat in EIGENEM Tab (je Funktion ein Tab). Calls im C sind per Doppelklick
        anspringbar; Button traegt die Funktion ins Run-Panel."""
        import re as _re
        win = getattr(self, "_c_win", None)
        if win is None or not win.winfo_exists():
            win = tk.Toplevel(self); self._c_win = win
            win.title("🧊 Ghidra Decompile (C)")
            win.geometry("980x680")
            self._c_nb = ttk.Notebook(win)
            self._c_nb.pack(fill="both", expand=True)
            self._c_tabs = {}
            self._c_tab_tip_setup()
        if base is None:
            m = _re.search(r"ImageBase 0x([0-9a-fA-F]+)", code or "")
            if m:
                try:
                    base = int(m.group(1), 16)
                except Exception:
                    base = None
        key = addr if addr is not None else title
        tabs = self._c_tabs
        entry = tabs.get(key)
        if entry is None or not entry["frame"].winfo_exists():
            frame = ttk.Frame(self._c_nb)
            bar = ttk.Frame(frame); bar.pack(side="top", fill="x")
            if addr is not None:
                ttk.Button(bar, text="▶ ins Run-Panel", width=16,
                           command=lambda a=addr: self.controller.load_addr_into_run(a)).pack(side="left", padx=3, pady=2)
                ttk.Button(bar, text="🔎 im Disasm", width=12,
                           command=lambda a=addr: self.controller.navigate(a, set_entry=False)).pack(side="left", padx=3, pady=2)
            ttk.Button(bar, text="✖ Tab schliessen", width=15,
                       command=lambda f=frame: self._close_c_tab(f)).pack(side="right", padx=3, pady=2)
            ttk.Label(bar, text="Doppelklick auf FUN_…/Symbol → springen & als Tab dekompilieren",
                      foreground="gray").pack(side="left", padx=8)
            txt = tk.Text(frame, wrap="none", font=("Consolas", 10))
            csb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=csb.set)
            txt.pack(side="left", fill="both", expand=True)
            csb.pack(side="right", fill="y")
            short = self._c_tab_label(title, code, addr)
            self._c_nb.add(frame, text=short)
            frame._c_key = key
            frame._c_full = self._full_sig_from_c(code) or self._short_name(title)
            txt.bind("<Double-Button-1>", self._on_c_double)
            entry = {"frame": frame, "text": txt}
            tabs[key] = entry
        txt = entry["text"]
        txt._c_base = base
        try:
            entry["frame"]._c_full = self._full_sig_from_c(code) or self._short_name(title)
            self._c_nb.tab(entry["frame"], text=self._c_tab_label(title, code, addr))   # Titel IMMER aktuell
        except Exception:
            pass
        txt.delete("1.0", "end")
        txt.insert("end", code or "(leer — Ghidra lieferte kein C)")
        self._highlight_c(txt)
        self._c_nb.select(entry["frame"])
        try:
            win.deiconify(); win.lift(); win.focus_force()
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

    def _on_c_double(self, event):
        """Doppelklick im C: Token unter dem Cursor (FUN_…/Symbol) -> Controller aufloesen lassen."""
        if not getattr(self, "controller", None):
            return
        import re as _re
        w = event.widget
        base = getattr(w, "_c_base", None)
        idx = w.index(f"@{event.x},{event.y}")
        row, col = idx.split("."); col = int(col)
        line = w.get(f"{row}.0", f"{row}.end")
        tok = None
        for m in _re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", line):
            if m.start() <= col <= m.end():
                tok = m.group(0); break
        if tok:
            self.controller.decompile_token(tok, base)

    def _close_c_tab(self, frame):
        try:
            key = getattr(frame, "_c_key", None)
            self._c_nb.forget(frame)
            if key in getattr(self, "_c_tabs", {}):
                del self._c_tabs[key]
        except Exception:
            pass

    _C_KW = {"if", "else", "return", "for", "while", "do", "switch", "case", "goto",
             "break", "continue", "sizeof", "default", "typedef", "struct", "union",
             "enum", "static", "const", "volatile", "extern", "register"}
    _C_TY = {"void", "char", "short", "int", "long", "unsigned", "signed", "float",
             "double", "bool", "uint", "ulong", "ushort", "uchar", "byte", "code",
             "undefined", "undefined1", "undefined2", "undefined4", "undefined8",
             "uint8_t", "uint16_t", "uint32_t", "uint64_t",
             "int8_t", "int16_t", "int32_t", "int64_t", "size_t", "wchar_t"}

    def _highlight_c(self, txt):
        """Leichtes C-Syntax-Highlighting via Text-Tags (Funktionen sind klein -> schnell)."""
        import re as _re
        txt.tag_configure("c_com", foreground="#2e7d32")
        txt.tag_configure("c_str", foreground="#b36b00")
        txt.tag_configure("c_num", foreground="#1565c0")
        txt.tag_configure("c_kw", foreground="#7b1fa2")
        txt.tag_configure("c_ty", foreground="#0277bd")
        txt.tag_configure("c_fun", foreground="#0b6b3a", underline=True)
        for t in ("c_com", "c_str", "c_num", "c_kw", "c_ty", "c_fun"):
            txt.tag_remove(t, "1.0", "end")
        content = txt.get("1.0", "end-1c")

        def ap(tag, a, b):
            txt.tag_add(tag, "1.0+%dc" % a, "1.0+%dc" % b)

        for m in _re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", content):
            w = m.group(0)
            if w in self._C_KW:
                ap("c_kw", m.start(), m.end())
            elif w in self._C_TY:
                ap("c_ty", m.start(), m.end())
            elif w.startswith("FUN_") or w.startswith("DAT_") or w.startswith("LAB_"):
                ap("c_fun", m.start(), m.end())
        for m in _re.finditer(r"\b0x[0-9a-fA-F]+\b|\b\d+\b", content):
            ap("c_num", m.start(), m.end())
        for m in _re.finditer(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", content):
            ap("c_str", m.start(), m.end())
        for m in _re.finditer(r"/\*.*?\*/|//[^\n]*", content, _re.DOTALL):
            ap("c_com", m.start(), m.end())
        # Kommentare/Strings haben Vorrang vor Keyword-Faerbung
        txt.tag_raise("c_str")
        txt.tag_raise("c_com")

    def _disasm_select_all(self, event=None):
        try:
            self.disasm.selection_set(self.disasm.get_children())
        except Exception:
            pass
        return "break"

    def _disasm_copy_sel(self, event=None, all_rows=False):
        try:
            rows = self.disasm.get_children() if all_rows else (self.disasm.selection() or self.disasm.get_children())
            lines = []
            for iid in rows:
                vals = list(self.disasm.item(iid, "values"))
                lines.append("  ".join(str(v) for v in vals[1:]))
            if lines:
                self.clipboard_clear(); self.clipboard_append("\n".join(lines))
        except Exception:
            pass
        return "break"

    def _on_history_double(self, event):
        if not getattr(self, "controller", None):
            return
        sel = self.tree_history.focus()
        if sel:
            self.controller.load_history_node(sel)

    def clear(self):
        self.set_outline([])
        self.set_disasm_rows("—", [])
        self.set_xref("—", [])
        for iid in self.regs_tree.get_children():
            self.regs_tree.delete(iid)
        self.flags_lbl.config(text="Flags: —")
        self.mem_view.delete("1.0", "end")
        self.cov_text.delete("1.0", "end")
        for i in self.tree_history.get_children():
            self.tree_history.delete(i)
        if getattr(self, "controller", None):
            self.controller._reset_history()
        try:
            self.lbl_loaded.config(text="— keine Lib geladen —")
        except Exception:
            pass
        self.append_status("geleert.")
