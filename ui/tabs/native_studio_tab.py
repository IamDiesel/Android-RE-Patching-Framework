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
        self.plugin_lbl = ttk.Label(top, text="Plugins: …", foreground="gray")
        self.plugin_lbl.pack(side="right", padx=8)
        self.plugin_bar = ttk.Frame(top)            # Stufe 2: dynamischer Plugin-Bereich
        self.plugin_bar.pack(side="right", padx=4)
        self.btn_self = ttk.Button(top, text="▶ Engine-Selbsttest")
        self.btn_run = ttk.Button(top, text="▶ Run")
        self.btn_disasm = ttk.Button(top, text="🔎 Disasm ab Start")
        self.btn_clear = ttk.Button(top, text="🧹 Leeren")
        self.btn_back = ttk.Button(top, text="◀", width=3)
        self.btn_fwd = ttk.Button(top, text="▶", width=3)
        self.btn_search = ttk.Button(top, text="🔍 Suche")
        self.btn_decomp = ttk.Button(top, text="🧊 C")
        self.btn_decomp_cancel = ttk.Button(top, text="⏹ Abbrechen", state="disabled")
        self.btn_favorites = ttk.Button(top, text="⭐ Favoriten")
        self.btn_c_col = ttk.Button(top, text="🧊 C-Spalte")
        for b in (self.btn_load, self.btn_unpack, self.btn_self, self.btn_run, self.btn_disasm,
                  self.btn_clear, self.btn_back, self.btn_fwd, self.btn_search, self.btn_decomp,
                  self.btn_decomp_cancel, self.btn_favorites, self.btn_c_col):
            b.pack(side="left", padx=4)
        _add_tooltip(self.btn_favorites,
                     "Arbeitsansichten (Run-Favoriten): laden / neu speichern / aktuelle ueberschreiben.")
        _add_tooltip(self.btn_c_col, "C-Dekompilat-Spalte (ganz rechts) ein-/ausblenden.")
        self.var_relocs = tk.BooleanVar(value=False)
        self.chk_relocs = ttk.Checkbutton(top, text="⚙ Relocs", variable=self.var_relocs)
        self.chk_relocs.pack(side="left", padx=6)
        _add_tooltip(self.chk_relocs,
                     "Beim ▶ Run die Relocations anwenden (fuellt Dispatch-/Call-Zeiger obfuskierter "
                     "Libs, z. B. libdb0c). Fuer die WB-Krypto-Libs aus lassen.")
        self.lbl_loaded = ttk.Label(top, text="— keine Lib geladen —", foreground="gray")
        self.lbl_loaded.pack(side="right", padx=8)
        self.progress = ttk.Progressbar(top, mode="indeterminate", length=140)

        # --- Coverage unten (spannt Breite) — zuerst packen, damit es unten sitzt ---
        # (Coverage/Unknown-Instr-Pane entfernt #GUI1 — Trace-Pane + Konsole ersetzen es)

        # --- vertikaler Rahmen: 3 Spalten oben + C-Dekompilat-Leiste unten ---
        self._vpane = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self._vpane.pack(side="top", fill="both", expand=True, padx=6, pady=5)
        # --- 3 Spalten ---
        paned = ttk.PanedWindow(self._vpane, orient=tk.HORIZONTAL)
        self._vpane.add(paned, weight=5)

        # SPALTE 1: Outline / Historie
        left_nb = ttk.Notebook(paned)
        f_out = ttk.Frame(left_nb)
        f_out_bar = ttk.Frame(f_out); f_out_bar.pack(side="top", fill="x", pady=(0, 2))
        self.btn_outline_fullname = ttk.Button(f_out_bar, text="⤢ Voller Name", width=14,
                                                command=self._toggle_outline_fullname)
        self.btn_outline_fullname.pack(side="left", padx=2)
        _add_tooltip(self.btn_outline_fullname, "Zeigt den vollen Symbolnamen statt des Kurznamens (Toggle).")
        self.btn_outline_reset = ttk.Button(f_out_bar, text="⟲ Filter/Sort", width=12,
                                             command=self._outline_reset_view)
        self.btn_outline_reset.pack(side="left", padx=2)
        _add_tooltip(self.btn_outline_reset, "Sortierung und alle Spaltenfilter zuruecksetzen.")
        ttk.Label(f_out_bar, text="(Spaltenkopf klicken: Sortieren + Filter)",
                  foreground="gray").pack(side="left", padx=6)
        self.outline = ttk.Treeview(f_out, columns=("kind", "name", "addr", "full"),
                                    displaycolumns=("addr", "kind", "name"), show="headings", height=20)
        self._outline_cols = ("kind", "name", "addr")
        self._outline_titles = {"kind": "Typ", "name": "Symbol", "addr": "Adresse"}
        self.outline.heading("kind", text="Typ", command=lambda: self._outline_header_menu(0))
        self.outline.column("kind", width=60, anchor="w")
        self.outline.heading("name", text="Symbol", command=lambda: self._outline_header_menu(1))
        self.outline.column("name", width=230, anchor="w")
        self.outline.heading("addr", text="Adresse", command=lambda: self._outline_header_menu(2))
        self.outline.column("addr", width=86, anchor="w")
        self.outline.column("full", width=0, stretch=False)   # versteckte Spalte: voller Symbolname (#165)
        self.outline.tag_configure("imp", foreground="#9aa0a6")   # Importe/undefiniert (0x0) ausgegraut
        self._outline_full_mode = False   # Toggle Kurz-/Vollname (#165)
        self._outline_rows = []           # Rohzeilen fuer Re-Render beim Umschalten
        self._outline_sort = None         # (Spaltenindex, absteigend) (#169)
        self._outline_filters = {}        # Spaltenindex -> Menge erlaubter Werte (#169)
        self.outline.bind("<Motion>", self._on_outline_motion)
        self.outline.bind("<Leave>", lambda e: self._outline_tip_hide())
        sb = ttk.Scrollbar(f_out, orient="vertical", command=self.outline.yview)
        self.outline.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.outline.pack(side="left", fill="both", expand=True)
        left_nb.add(f_out, text="Outline")

        f_hist = ttk.Frame(left_nb)
        self.tree_history = ttk.Treeview(f_hist, show="tree", height=20)
        self.tree_history.tag_configure("current", foreground="#1a7f37")
        hsb = ttk.Scrollbar(f_hist, orient="vertical", command=self.tree_history.yview)
        self.tree_history.configure(yscrollcommand=hsb.set)
        hsb.pack(side="right", fill="y")
        self.tree_history.pack(side="left", fill="both", expand=True)
        left_nb.add(f_hist, text="Historie")
        paned.add(left_nb, weight=2)

        # SPALTE 2 (Mitte): Disassembly oben + Run-Panel unten
        center = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(center, weight=4)

        f_dis = ttk.LabelFrame(center, text="Disassembly (Read-Only)")
        # Symbol-/Funktionskopf als Label UEBER der Tabelle (keine Instruktionszeile)
        self.disasm_sym = ttk.Label(f_dis, text="—", font=("Consolas", 9, "italic"), anchor="w")
        self.disasm_sym.pack(side="top", fill="x", padx=4, pady=(2, 0))
        _gbar = ttk.Frame(f_dis); _gbar.pack(side="top", fill="x", padx=4, pady=(0, 2))
        ttk.Label(_gbar, text="Gehe zu:").pack(side="left")
        self.disasm_goto = ttk.Entry(_gbar, width=18); self.disasm_goto.pack(side="left", padx=4)
        self.btn_goto = ttk.Button(_gbar, text="↵ springen", width=11, command=self._on_goto)
        self.btn_goto.pack(side="left")
        self.disasm_goto.bind("<Return>", lambda e: self._on_goto())
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
        self._bp_addrs = set()  # Breakpoint-Adressen (GUI-Gutter)
        dsb = ttk.Scrollbar(_dwrap, orient="vertical", command=self.disasm.yview)
        self.disasm.configure(yscrollcommand=dsb.set)
        dsb.pack(side="right", fill="y")
        self.disasm.pack(side="left", fill="both", expand=True)
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
        self.regs_tree = ttk.Treeview(f_reg, columns=("reg", "hex", "dec", "sdec", "ascii"),
                                      displaycolumns=("reg", "hex"), show="headings", height=10)
        self.regs_tree.heading("reg", text="Reg"); self.regs_tree.column("reg", width=70, anchor="w", stretch=False)
        self.regs_tree.heading("hex", text="Hex"); self.regs_tree.column("hex", width=170, anchor="w", stretch=False)
        self.regs_tree.heading("dec", text="Dez"); self.regs_tree.column("dec", width=170, anchor="w", stretch=False)
        self.regs_tree.heading("sdec", text="Signed"); self.regs_tree.column("sdec", width=180, anchor="w", stretch=False)
        self.regs_tree.heading("ascii", text="ASCII"); self.regs_tree.column("ascii", width=90, anchor="w", stretch=False)
        self._reg_fmt_all = ("hex", "dec", "sdec", "ascii")
        self._reg_fmt_on = {"hex": True, "dec": False, "sdec": False, "ascii": False}
        self._reg_menu = tk.Menu(self.regs_tree, tearoff=0)
        self.regs_tree.bind("<Button-3>", self._on_reg_menu)
        _add_tooltip(self.regs_tree, "Rechtsklick: weitere Wert-Formate (Dez/Signed/ASCII) ein-/ausblenden.")
        self.regs_tree.tag_configure("ret", foreground="#0c5e32", background="#e5f6ec")
        rsb = ttk.Scrollbar(f_reg, orient="vertical", command=self.regs_tree.yview)
        rhsb = ttk.Scrollbar(f_reg, orient="horizontal", command=self.regs_tree.xview)
        self.regs_tree.configure(yscrollcommand=rsb.set, xscrollcommand=rhsb.set)
        rsb.pack(side="right", fill="y")
        rhsb.pack(side="bottom", fill="x")
        self.regs_tree.pack(side="left", fill="both", expand=True)
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
        msb.pack(side="right", fill="y")
        self.mem_view.pack(side="left", fill="both", expand=True)
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
        xsb.pack(side="right", fill="y")
        self.xref_tree.pack(side="left", fill="both", expand=True)
        mem_nb.add(f_xr, text="XREF / CallGraph")

        # Reiter Trace (besuchte Bloecke, #GUI1)
        f_tr = ttk.Frame(mem_nb)
        tbar = ttk.Frame(f_tr); tbar.pack(side="top", fill="x", padx=4, pady=2)
        ttk.Label(tbar, text="Pfad (besuchte Bloecke):").pack(side="left")
        self.btn_trace_py = ttk.Button(tbar, text="🐍 Trace→Python", width=16)
        self.btn_trace_py.pack(side="right", padx=2)
        self.trace_tree = ttk.Treeview(f_tr, columns=("n", "pc"), show="headings", height=8)
        self.trace_tree.heading("n", text="#"); self.trace_tree.column("n", width=50, anchor="e")
        self.trace_tree.heading("pc", text="Block"); self.trace_tree.column("pc", width=150, anchor="w")
        tsb = ttk.Scrollbar(f_tr, orient="vertical", command=self.trace_tree.yview)
        self.trace_tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.trace_tree.pack(side="left", fill="both", expand=True)
        self.trace_tree.bind("<Double-1>", self._on_trace_jump)
        mem_nb.add(f_tr, text="Trace")

        # Reiter Python-Lift (#GUI1) — zeigt die geliftete Funktion
        f_py = ttk.Frame(mem_nb)
        self.py_lift = tk.Text(f_py, height=8, wrap="none", font=("Consolas", 9))
        pysb = ttk.Scrollbar(f_py, orient="vertical", command=self.py_lift.yview)
        self.py_lift.configure(yscrollcommand=pysb.set)
        pysb.pack(side="right", fill="y")
        self.py_lift.pack(side="left", fill="both", expand=True)
        mem_nb.add(f_py, text="Python-Lift")

        # Reiter: JNI-Calls (A3.2 Fake-JNIEnv-Fassade)
        f_jni = ttk.Frame(mem_nb)
        self.jni_tree = ttk.Treeview(f_jni, columns=("fn", "target", "cat", "ret"),
                                     show="headings", height=8)
        for _c, _t, _w in (("fn", "Funktion", 120), ("target", "Klasse.Methode", 240),
                           ("cat", "Kategorie", 90), ("ret", "Vorschlag/final", 110)):
            self.jni_tree.heading(_c, text=_t); self.jni_tree.column(_c, width=_w, anchor="w")
        jsb = ttk.Scrollbar(f_jni, orient="vertical", command=self.jni_tree.yview)
        self.jni_tree.configure(yscrollcommand=jsb.set)
        jsb.pack(side="right", fill="y")
        self.jni_tree.pack(side="left", fill="both", expand=True)
        self.jni_tree.bind("<Double-1>", self._on_jni_popup)
        mem_nb.add(f_jni, text="JNI-Calls")
        self._jni_rows = []

        # C-Dekompilat als eigene, einklappbare 4. Spalte ganz rechts (#167)
        self._c_host = ttk.Frame(paned)
        paned.add(self._c_host, weight=2)
        self._c_paned = paned
        self._c_col_visible = True
        self._build_c_drawer()
        self.append_status("Bereit. '📥 .so laden' → Funktion im Outline doppelklicken → Entry/Args setzen → '▶ Run'.")

    def _build_run_panel(self, parent):
        body = ttk.Frame(parent); body.pack(side="top", fill="both", expand=True, padx=4, pady=3)

        # Arbeitsansicht (Run-Favoriten): eigener Topbar-Button → Popup (#166).
        # fav_combo/btn_fav_save/btn_fav_upd werden in _open_favorites_popup erzeugt.

        # Entry
        erow = ttk.Frame(body); erow.pack(side="top", fill="x", pady=2)
        ttk.Label(erow, text="Entry:", width=8).pack(side="left")
        self.run_entry = ttk.Entry(erow); self.run_entry.pack(side="left", fill="x", expand=True, padx=4)

        # ▶ Run-Leiste ZUERST unten packen (vor dem expandierenden cols-Frame)
        runbar = ttk.Frame(body); runbar.pack(side="bottom", fill="x", pady=(2, 0))
        self.btn_run2 = ttk.Button(runbar, text="▶ Run")
        self.btn_run2.pack(side="right")
        # Debugger-Leiste (#GUI1) — Services sind headless getestet
        self.btn_dbg_start = ttk.Button(runbar, text="⟲ Dbg", width=7)
        self.btn_dbg_step = ttk.Button(runbar, text="⏭ Step", width=8)
        self.btn_dbg_instr = ttk.Button(runbar, text="⏱ Instr", width=8)
        self.btn_dbg_resume = ttk.Button(runbar, text="⏩ Resume", width=10)
        self.btn_dbg_sync = ttk.Button(runbar, text="⬆ An KI", width=9)
        self.btn_dbg_load = ttk.Button(runbar, text="⬇ Von KI", width=9)
        self.btn_dbg_status = ttk.Button(runbar, text="⇅ Abgleich", width=11)
        # Reihenfolge (links→rechts): Debug-Steuerung | Teilen (An KI / Von KI / Abgleich) | ▶ Run
        for _b in (self.btn_dbg_status, self.btn_dbg_load, self.btn_dbg_sync,
                   self.btn_dbg_resume, self.btn_dbg_instr, self.btn_dbg_step, self.btn_dbg_start):
            _b.pack(side="right", padx=2)
        _add_tooltip(self.btn_dbg_sync, "⬆ An KI: deinen Debug-Stand in die geteilte Sitzung schreiben (push, fuer die KI lesbar). Warnt, wenn die KI inzwischen weiter ist.")
        _add_tooltip(self.btn_dbg_load, "⬇ Von KI: den geteilten Stand (ggf. von der KI fortgeschrieben) in deine GUI laden (pull).")
        _add_tooltip(self.btn_dbg_status, "⇅ Abgleich: vergleicht deinen Stand mit der geteilten Sitzung — wer ist neuer?")
        self.btn_jni = ttk.Button(runbar, text="⚡ JNI", width=7)
        self.btn_jni.pack(side="left", padx=1)
        _add_tooltip(self.btn_jni, "⚡ JNI: JNI_OnLoad mit der Fake-JNIEnv-Fassade laufen lassen und die JNI-Call-Liste fuellen (Doppelklick auf eine Zeile: Smali + Klassifizierung + Vorschlag).")
        self.btn_snap_load = ttk.Button(runbar, text="📂 Snapshot", width=12)
        self.btn_snap_save = ttk.Button(runbar, text="💾 Snapshot", width=12)
        self.btn_snap_load.pack(side="left", padx=2)
        self.btn_snap_save.pack(side="left", padx=2)
        ttk.Label(runbar, text="Arg: Zahl | ptr:<hex> | buf:<n>", foreground="gray").pack(side="left", padx=8)

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
        ttk.Label(sprow, text="Stubs:").pack(side="left", padx=(8, 0))
        self.stub_entry = ttk.Entry(sprow, width=22); self.stub_entry.pack(side="left", padx=4)
        _add_tooltip(self.stub_entry, "Funktionen stubben (Rueckkehr mit x0=ret): 0xADDR=ret, Symbol=ret (kommagetrennt).")

    def _add_arg_row(self, label):
        row = ttk.Frame(self._arg_host); row.pack(side="top", fill="x", pady=1)
        ttk.Label(row, text=label, width=4, font=("Consolas", 9)).pack(side="left")
        ent = ttk.Entry(row, font=("Consolas", 9)); ent.pack(side="left", fill="x", expand=True, padx=3)
        self.arg_rows.append((label, ent))

    def _on_add_arg(self):
        self._add_arg_row(f"x{len(self.arg_rows)}")

    def set_jni_calls(self, rows):
        """Fuellt den JNI-Calls-Reiter aus einer Liste von Call-Log-Eintraegen."""
        t = self.jni_tree
        for _i in t.get_children():
            t.delete(_i)
        self._jni_rows = list(rows or [])
        for r in self._jni_rows:
            if r.get("method"):
                tgt = f"{r.get('class', '?')}->{r.get('method')}{r.get('sig', '')}"
            else:
                tgt = r.get("class") or ""
            ret = r.get("final", r.get("proposed", ""))
            t.insert("", "end", values=(r.get("fn", ""), tgt, r.get("category") or "", ret))

    def _on_jni_popup(self, _e=None):
        sel = self.jni_tree.selection()
        if not sel:
            return
        idx = self.jni_tree.index(sel[0])
        if 0 <= idx < len(self._jni_rows) and hasattr(self.controller, "jni_show_popup"):
            self.controller.jni_show_popup(self._jni_rows[idx])

    def set_plugin_contributions(self, contribs):
        """Stufe 2: baut den dynamischen Plugin-Bereich aus deklarativen Beitraegen.
        Beitrag = {kind:'button', label, action, tooltip?, plugin?}. Leer -> Bereich leer."""
        try:
            for _w in self.plugin_bar.winfo_children():
                _w.destroy()
            for c in (contribs or []):
                if c.get("kind", "button") != "button":
                    continue
                a = c.get("action"); pl = c.get("plugin")
                b = ttk.Button(self.plugin_bar, text=c.get("label", "Plugin"),
                               command=lambda a=a, pl=pl: self.controller.plugin_action(a, pl))
                b.pack(side="left", padx=1)
                if c.get("tooltip"):
                    _add_tooltip(b, c["tooltip"])
        except Exception:
            pass

    def set_plugin_state(self, names):
        """Stufe 1: Entpacken-Button nur aktiv, wenn ein Plugin vorhanden ist; Status-Label."""
        names = list(names or [])
        try:
            self.btn_unpack.config(state=("normal" if names else "disabled"))
            self.plugin_lbl.config(text=("Plugins: " + ", ".join(names)) if names
                                   else "Plugins: keine (native_plugins/ leer)")
        except Exception:
            pass

    def _wire_buttons(self):
        self.btn_load.config(command=self.controller.load_so_dialog)
        self.btn_unpack.config(command=self.controller.unpack_dialog)
        self.set_plugin_state(self.controller.plugin_names())
        self.set_plugin_contributions(self.controller.plugin_contributions())
        self.btn_self.config(command=self.controller.engine_selftest)
        self.btn_run.config(command=self.controller.run_emulation)
        self.btn_run2.config(command=self.controller.run_emulation)
        self.btn_dbg_start.config(command=self.controller.dbg_start)
        self.btn_dbg_step.config(command=self.controller.dbg_step_block)
        self.btn_dbg_instr.config(command=self.controller.dbg_step_insn)
        self.btn_dbg_resume.config(command=self.controller.dbg_resume)
        self.btn_trace_py.config(command=self.controller.dbg_lift_python)
        self.btn_dbg_sync.config(command=self.controller.dbg_sync)
        self.btn_dbg_load.config(command=self.controller.dbg_load_session)
        self.btn_dbg_status.config(command=self.controller.dbg_status)
        self.btn_jni.config(command=self.controller.jni_run)
        self.btn_snap_save.config(command=self.controller.snapshot_save)
        self.btn_snap_load.config(command=self.controller.snapshot_set_load)
        self.btn_favorites.config(command=self._open_favorites_popup)
        self.btn_c_col.config(command=self._c_col_toggle)
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
        self.disasm.bind("<Button-1>", self._on_disasm_click)
        self.disasm_menu = tk.Menu(self.disasm, tearoff=0)
        self.disasm_menu.add_command(label="Zeile kopieren", command=self._copy_disasm_row)
        self.disasm_menu.add_command(label="Alles kopieren", command=lambda: self._disasm_copy_sel(all_rows=True))
        self.disasm_menu.add_separator()
        self.disasm_menu.add_command(label="🔗 Reloc/Zeiger aufloesen", command=self._on_disasm_resolve_reloc)
        self.disasm_menu.add_command(label="⛔ Breakpoint ein/aus", command=self._on_disasm_toggle_bp)
        self.disasm.bind("<Button-3>", self._post_disasm_menu)
        self.disasm.bind("<Control-a>", self._disasm_select_all)
        self.disasm.bind("<Control-A>", self._disasm_select_all)
        self.disasm.bind("<Control-c>", self._disasm_copy_sel)
        self.disasm.bind("<Control-C>", self._disasm_copy_sel)
        self.outline.bind("<Double-Button-1>", self._on_outline_double)
        self._outline_menu = tk.Menu(self.outline, tearoff=0)
        self._outline_menu.add_command(label="✎ Umbenennen / Notiz", command=self._on_outline_rename)
        self.outline.bind("<Button-3>", self._on_outline_rmb)
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
        _add_tooltip(self.btn_clear, "Leert Outline, Disassembly, Register, Memory und Historie.")
        _add_tooltip(self.btn_back, "Navigation zurueck (vorheriger Sprung in der Historie).")
        _add_tooltip(self.btn_fwd, "Navigation vorwaerts.")
        _add_tooltip(self.btn_decomp_cancel, "Laufende Ghidra-Dekompilation abbrechen.")
        _add_tooltip(self.btn_goto, "Zu einer Adresse/Symbol im Disassembly springen (Fenster um die Adresse).")
        _add_tooltip(self.btn_memshow, "Speicher ab der eingegebenen Adresse als Hexdump anzeigen.")
        _add_tooltip(self.btn_xref, "Zeigt, wer die eingegebene Adresse referenziert (XREF/Caller).")
        _add_tooltip(self.btn_trace_py, "Trace -> Python: den gelaufenen Pfad als lueckenfreie Python-Funktion liften.")
        _add_tooltip(self.btn_run2, "Fuehrt die Funktion aus dem Run-Panel aus (wie ▶ Run oben).")
        _add_tooltip(self.btn_dbg_start, "⟲ Dbg: Debug-Sitzung am Entry starten (pc am Entry, Trace leer).")
        _add_tooltip(self.btn_dbg_step, "⏭ Step: einen Basisblock weiter.")
        _add_tooltip(self.btn_dbg_instr, "⏱ Instr: eine einzelne Instruktion weiter.")
        _add_tooltip(self.btn_dbg_resume, "⏩ Resume: bis zum naechsten Breakpoint bzw. bis zum Budget aus dem 'maxins'-Feld (Fallback 4000 Bloecke).")
        _add_tooltip(self.btn_snap_load, "Snapshot (Mem+Register) laden und ab Entry ausfuehren.")
        _add_tooltip(self.btn_snap_save, "Zustand nach dem letzten Lauf als Snapshot (.pkl.xz) speichern.")

    # ---- vom Controller aufgerufen ----------------------------------------
    def append_status(self, msg):
        """Status/Fehler laufen in die GEMEINSAME Main Console (kein eigenes Log-Feld mehr)."""
        try:
            self.app.log(f"[NativeStudio] {msg}")
        except Exception:
            pass

    def set_coverage(self, text):
        # #GUI1: Coverage-Pane entfernt; Zusammenfassung laeuft ueber die Konsole.
        return

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
        self._outline_rows = list(rows or [])
        self._render_outline()

    def _outline_disp_rows(self):
        """Anzeigezeilen (kind, disp_name, addr, full, tag) je nach Kurz-/Vollname-Modus."""
        full_mode = getattr(self, "_outline_full_mode", False)
        out = []
        for row in self._outline_rows:
            kind = row[0]
            name = row[1] if len(row) > 1 else ""
            addr = row[2] if len(row) > 2 else ""
            full = row[3] if len(row) > 3 else name
            dname = name if full_mode else self._short_name(name)
            out.append((kind, dname, addr, full, "imp" if kind in ("import", "data") else ""))
        return out

    def _render_outline(self):
        """Outline zeichnen: Kurz-/Vollname (#165) + Filter & Sortierung (#169)."""
        for iid in self.outline.get_children():
            self.outline.delete(iid)
        rows = self._outline_disp_rows()
        filters = getattr(self, "_outline_filters", {})
        if filters:
            def keep(d):
                for ci, allowed in filters.items():
                    if allowed is None:
                        continue
                    if (d[ci] if ci < 3 else "") not in allowed:
                        return False
                return True
            rows = [d for d in rows if keep(d)]
        srt = getattr(self, "_outline_sort", None)
        if srt:
            ci, desc = srt

            def _key(d):
                v = d[ci]
                if ci == 2:
                    try:
                        return (0, int(str(v), 16))
                    except Exception:
                        return (1, 0)
                return (0, str(v).lower())
            rows = sorted(rows, key=_key, reverse=desc)
        for d in rows:
            tags = ("imp",) if d[4] == "imp" else ()
            self.outline.insert("", "end", values=(d[0], d[1], d[2], d[3]), tags=tags)
        self._update_outline_headings()
        self._autosize_outline()

    def _autosize_outline(self):
        """Spaltenbreiten an den Inhalt anpassen (gerade so passend), gedeckelt."""
        import tkinter.font as _tkfont
        try:
            fnt = _tkfont.nametofont("TkDefaultFont")
        except Exception:
            return
        titles = getattr(self, "_outline_titles", {})
        widths = {c: fnt.measure(titles.get(c, c)) + 26 for c in ("kind", "name", "addr")}
        for iid in self.outline.get_children():
            vals = self.outline.item(iid, "values")
            for idx, col in ((0, "kind"), (1, "name"), (2, "addr")):
                if idx < len(vals):
                    w = fnt.measure(str(vals[idx])) + 26
                    if w > widths[col]:
                        widths[col] = w
        caps = {"kind": 120, "name": 460, "addr": 150}
        for col, w in widths.items():
            try:
                self.outline.column(col, width=min(w, caps[col]))
            except Exception:
                pass

    def _update_outline_headings(self):
        srt = getattr(self, "_outline_sort", None)
        titles = getattr(self, "_outline_titles", {"kind": "Typ", "name": "Symbol", "addr": "Adresse"})
        cols = getattr(self, "_outline_cols", ("kind", "name", "addr"))
        filters = getattr(self, "_outline_filters", {})
        for i, col in enumerate(cols):
            txt = titles.get(col, col)
            if srt and srt[0] == i:
                txt += "  " + ("▼" if srt[1] else "▲")
            if filters.get(i):
                txt += " 🔍"
            try:
                self.outline.heading(col, text=txt)
            except Exception:
                pass

    def _outline_apply_sort(self, ci, desc):
        self._outline_sort = (ci, desc)
        self._render_outline()

    def _outline_reset_view(self):
        self._outline_sort = None
        self._outline_filters = {}
        self._render_outline()

    def _outline_header_menu(self, ci):
        """Popup am Spaltenkopf: Sortierung + Excel-artiger Checkbox-Filter (#169)."""
        win = tk.Toplevel(self.outline)
        try:
            win.wm_overrideredirect(True)
        except Exception:
            pass
        x = self.outline.winfo_pointerx(); y = self.outline.winfo_pointery()
        win.wm_geometry(f"+{x}+{y + 8}")
        frm = ttk.Frame(win, relief="solid", borderwidth=1, padding=4)
        frm.pack(fill="both", expand=True)
        srow = ttk.Frame(frm); srow.pack(fill="x")
        ttk.Button(srow, text="▲ Aufsteigend", width=14,
                   command=lambda: (self._outline_apply_sort(ci, False), win.destroy())).pack(side="left", padx=1)
        ttk.Button(srow, text="▼ Absteigend", width=14,
                   command=lambda: (self._outline_apply_sort(ci, True), win.destroy())).pack(side="left", padx=1)
        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(frm, text="Filter (Haken = sichtbar):").pack(anchor="w")
        values, seen = [], set()
        for d in self._outline_disp_rows():
            v = d[ci] if ci < 3 else ""
            if v not in seen:
                seen.add(v); values.append(v)
        values.sort(key=lambda s: str(s).lower())
        cur = getattr(self, "_outline_filters", {}).get(ci)
        canv = tk.Canvas(frm, height=180, width=250, highlightthickness=0)
        inner = ttk.Frame(canv)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=canv.yview)
        canv.configure(yscrollcommand=vsb.set)
        canv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))
        vsb.pack(side="right", fill="y"); canv.pack(side="top", fill="both", expand=True)
        vars_ = {}
        for v in values:
            bv = tk.BooleanVar(value=(cur is None or v in cur))
            vars_[v] = bv
            ttk.Checkbutton(inner, text=(str(v) if v != "" else "(leer)"), variable=bv).pack(anchor="w")

        def _set_all(state):
            for bv in vars_.values():
                bv.set(state)

        brow = ttk.Frame(frm); brow.pack(fill="x", pady=(4, 0))
        ttk.Button(brow, text="Alle", width=6, command=lambda: _set_all(True)).pack(side="left", padx=1)
        ttk.Button(brow, text="Keine", width=6, command=lambda: _set_all(False)).pack(side="left", padx=1)

        def _apply():
            allowed = {v for v, bv in vars_.items() if bv.get()}
            flt = dict(getattr(self, "_outline_filters", {}))
            if len(allowed) == len(vars_):
                flt.pop(ci, None)
            else:
                flt[ci] = allowed
            self._outline_filters = flt
            self._render_outline(); win.destroy()

        def _clear():
            flt = dict(getattr(self, "_outline_filters", {}))
            flt.pop(ci, None)
            self._outline_filters = flt
            self._render_outline(); win.destroy()

        ttk.Button(brow, text="✓ Anwenden", width=11, command=_apply).pack(side="right", padx=1)
        ttk.Button(brow, text="Filter weg", width=10, command=_clear).pack(side="right", padx=1)
        try:
            win.grab_set()
        except Exception:
            pass
        win.bind("<Escape>", lambda e: win.destroy())

    def _toggle_outline_fullname(self):
        self._outline_full_mode = not getattr(self, "_outline_full_mode", False)
        try:
            self.btn_outline_fullname.config(
                text=("⤢ Kurzname" if self._outline_full_mode else "⤢ Voller Name"))
        except Exception:
            pass
        self._render_outline()

    def _outline_tip_hide(self):
        tip = getattr(self, "_outline_tip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
        self._outline_tip = None
        self._outline_tip_row = None

    def _on_outline_motion(self, event):
        """Tooltip mit vollem Symbolnamen ueber der Kurznamen-Zeile (#165)."""
        try:
            iid = self.outline.identify_row(event.y)
        except Exception:
            iid = None
        if not iid:
            self._outline_tip_hide(); return
        if getattr(self, "_outline_tip_row", None) == iid:
            return
        self._outline_tip_hide(); self._outline_tip_row = iid
        vals = self.outline.item(iid, "values")
        full = vals[3] if len(vals) > 3 else (vals[1] if len(vals) > 1 else "")
        short = vals[1] if len(vals) > 1 else ""
        if not full or full == short:
            return
        try:
            tip = tk.Toplevel(self.outline); tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 16}")
            tk.Label(tip, text=full, justify="left", background="#ffffe0",
                     relief="solid", borderwidth=1, font=("Consolas", 9)).pack()
            self._outline_tip = tip
        except Exception:
            self._outline_tip = None

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
        try:
            self._refresh_bp_marks()
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
        """snap = {ret, x:[..31], sp, lr, pc, flags:{N,Z,C,V}}. Mehrere Wert-Formate je Register."""
        t = self.regs_tree
        for iid in t.get_children():
            t.delete(iid)

        def _fmts(v):
            v &= (1 << 64) - 1
            sv = v - (1 << 64) if v >= (1 << 63) else v
            b = v.to_bytes(8, "little")
            asc = "".join(chr(c) if 32 <= c < 127 else "." for c in b)
            return (f"0x{v:x}", str(v), str(sv), asc)

        try:
            rows = [("x0◂ret", snap["ret"], "ret")]
            xs = snap.get("x", [])
            for i in range(1, min(31, len(xs))):
                rows.append((f"x{i}", xs[i], ""))
            rows.append(("sp", snap["sp"], ""))
            rows.append(("lr", snap["lr"], ""))
            rows.append(("pc", snap["pc"], ""))
            for name, val, tag in rows:
                hx, dc, sd, asc = _fmts(int(val))
                t.insert("", "end", values=(name, hx, dc, sd, asc), tags=((tag,) if tag else ()))
            f = snap.get("flags", {})
            self.flags_lbl.config(text=f"Flags:  N={f.get('N','?')}  Z={f.get('Z','?')}  C={f.get('C','?')}  V={f.get('V','?')}")
        except Exception:
            pass

    def _on_reg_menu(self, event):
        """Rechtsklick im Register-Pane: Wert-Format-Spalten ein-/ausblenden."""
        labels = {"hex": "Hex", "dec": "Dezimal", "sdec": "Signed (64-bit)", "ascii": "ASCII (8 Bytes)"}
        m = self._reg_menu
        m.delete(0, "end")
        for col in self._reg_fmt_all:
            on = self._reg_fmt_on.get(col, False)
            m.add_command(label=("\u2611 " if on else "\u2610 ") + labels[col],
                          command=lambda c=col: self._reg_toggle_col(c))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _reg_toggle_col(self, col):
        self._reg_fmt_on[col] = not self._reg_fmt_on.get(col, False)
        disp = ["reg"] + [c for c in self._reg_fmt_all if self._reg_fmt_on.get(c)]
        if len(disp) == 1:                      # immer mindestens Hex zeigen
            self._reg_fmt_on["hex"] = True
            disp.append("hex")
        try:
            self.regs_tree.configure(displaycolumns=tuple(disp))
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

    def set_trace(self, trace):
        """Fuellt das Trace-Pane mit der besuchten Blockfolge (#GUI1)."""
        t = self.trace_tree
        for iid in t.get_children():
            t.delete(iid)
        for e in (trace or []):
            t.insert("", "end", values=(e.get("n"), e.get("pc_hex")))

    def set_python_lift(self, src):
        """Zeigt die geliftete Python-Funktion (#GUI1)."""
        self.py_lift.delete("1.0", "end")
        self.py_lift.insert("end", src or "")

    def _on_trace_jump(self, event):
        """Doppelklick auf eine Trace-Zeile -> Sprung im Disassembly."""
        if not getattr(self, "controller", None):
            return
        sel = self.trace_tree.focus()
        if not sel:
            return
        vals = self.trace_tree.item(sel, "values")
        if len(vals) > 1:
            try:
                self.controller.navigate(int(str(vals[1]), 0), set_entry=False)
            except Exception:
                pass

    def _open_favorites_popup(self):
        """Popup mit allen Arbeitsansicht-Inhalten (Laden/Speichern/Ueberschreiben) (#166)."""
        win = getattr(self, "_fav_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.deiconify(); win.lift(); win.focus_force()
                    if getattr(self, "controller", None):
                        self.controller.fav_refresh()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self); win.title("Arbeitsansichten (Run-Favoriten)")
        try:
            win.transient(self.winfo_toplevel())
        except Exception:
            pass
        self._fav_win = win
        frm = ttk.Frame(win, padding=10); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Gespeicherte Arbeitsansicht laden:").pack(anchor="w")
        row1 = ttk.Frame(frm); row1.pack(fill="x", pady=(2, 8))
        self.fav_combo = ttk.Combobox(row1, values=[], width=34, state="readonly")
        self.fav_combo.pack(side="left", padx=(0, 4))
        ttk.Button(row1, text="📂 Laden",
                   command=lambda: self.controller.fav_load(self.fav_combo.get())).pack(side="left", padx=2)
        row2 = ttk.Frame(frm); row2.pack(fill="x")
        self.btn_fav_save = ttk.Button(row2, text="💾 Als neue Ansicht speichern",
                                       command=self.controller.fav_save)
        self.btn_fav_save.pack(side="left", padx=2)
        self.btn_fav_upd = ttk.Button(row2, text="✎ Aktuelle ueberschreiben",
                                      command=self.controller.fav_update)
        self.btn_fav_upd.pack(side="left", padx=2)
        ttk.Label(frm, text="Speichert/laedt Entry, Args, Mem-Overrides, SP, maxins und Stubs aus dem Run-Panel.",
                  foreground="gray", wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))
        win.protocol("WM_DELETE_WINDOW", lambda: win.withdraw())
        if getattr(self, "controller", None):
            try:
                self.controller.fav_refresh()
            except Exception:
                pass

    def set_fav_list(self, names):
        try:
            self.fav_combo["values"] = list(names or [])
        except Exception:
            pass

    def set_run_config(self, cfg):
        """Traegt eine gespeicherte Arbeitsansicht ins Run-Panel ein."""
        self.run_entry.delete(0, "end"); self.run_entry.insert(0, str(cfg.get("entry", "")))
        args = cfg.get("args", []) or []
        while len(self.arg_rows) < len(args):
            self._on_add_arg()
        for i, (_lbl, ent) in enumerate(self.arg_rows):
            ent.delete(0, "end")
            if i < len(args):
                a = args[i]
                if isinstance(a, dict):
                    if "ptr" in a:
                        ent.insert(0, "ptr:" + str(a["ptr"]))
                    elif "buf" in a:
                        ent.insert(0, "buf:" + str(a["buf"]))
                    elif "int" in a:
                        ent.insert(0, hex(int(a["int"])))
                else:
                    ent.insert(0, hex(a) if isinstance(a, int) else str(a))
        self.mem_text.delete("1.0", "end")
        for ov in cfg.get("mem_overrides", []) or []:
            a = ov.get("addr"); b = ov.get("bytes", "")
            if isinstance(b, (bytes, bytearray)):
                b = bytes(b).hex()
            addr_s = f"0x{a:x}" if isinstance(a, int) else str(a)
            self.mem_text.insert("end", f"{addr_s}: {b}\n")
        self.run_sp.delete(0, "end")
        if cfg.get("sp") is not None:
            self.run_sp.insert(0, hex(cfg["sp"]) if isinstance(cfg["sp"], int) else str(cfg["sp"]))
        self.run_maxins.delete(0, "end"); self.run_maxins.insert(0, str(cfg.get("maxins", 2000000)))
        try:
            self.stub_entry.delete(0, "end")
            self.stub_entry.insert(0, ", ".join(f"{s.get('at')}={s.get('ret')}" for s in cfg.get("stubs", []) or []))
        except Exception:
            pass

    def get_run_config(self):
        """Liest das Run-Panel → dict {entry, args[], mem_overrides[], sp, maxins}."""
        def _pi(s):
            s = (s or "").strip()
            if not s:
                return 0
            return int(s, 16) if s.lower().startswith("0x") else int(s, 0)
        entry = self.run_entry.get().strip()

        def _arg(s):
            s = (s or "").strip()
            if not s:
                return 0
            low = s.lower()
            if low.startswith("ptr:"):
                return {"ptr": s.split(":", 1)[1].strip().replace(" ", ""), "out": True}
            if low.startswith("buf:"):
                try:
                    return {"buf": int(s.split(":", 1)[1].strip(), 0)}
                except Exception:
                    return 0
            return _pi(s)
        args = [_arg(ent.get()) for _lbl, ent in self.arg_rows]
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
        stubs = []
        for tok in (self.stub_entry.get() or "").split(","):
            tok = tok.strip()
            if not tok or "=" not in tok:
                continue
            a, r = tok.split("=", 1)
            try:
                stubs.append({"at": a.strip(), "ret": int(r.strip(), 0)})
            except Exception:
                pass
        return {"entry": entry, "args": args, "mem_overrides": mem, "sp": sp, "maxins": mx, "stubs": stubs}

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

    def _on_disasm_click(self, event):
        """Klick in die schmale mark-Spalte (#1) toggelt einen Breakpoint."""
        try:
            if self.disasm.identify_region(event.x, event.y) != "cell":
                return
            if self.disasm.identify_column(event.x) != "#1":
                return
            iid = self.disasm.identify_row(event.y)
            if iid:
                self._toggle_bp_row(iid)
        except Exception:
            pass

    def _on_disasm_toggle_bp(self):
        iid = self.disasm.focus()
        if iid:
            self._toggle_bp_row(iid)

    def _toggle_bp_row(self, iid):
        vals = self.disasm.item(iid, "values")
        if len(vals) < 2:
            return
        try:
            addr = int(str(vals[1]), 0)
        except Exception:
            return
        if addr in self._bp_addrs:
            self._bp_addrs.discard(addr)
        else:
            self._bp_addrs.add(addr)
        self._refresh_bp_marks()
        if getattr(self, "controller", None):
            try:
                self.controller.set_breakpoints(self._bp_addrs)
            except Exception:
                pass

    def _refresh_bp_marks(self):
        """mark-Spalte setzen: ● fuer BP, ▶ fuer aktuellen pc bleibt erhalten (kombiniert)."""
        bps = getattr(self, "_bp_addrs", set())
        for iid in self.disasm.get_children():
            vals = self.disasm.item(iid, "values")
            if len(vals) < 2:
                continue
            try:
                addr = int(str(vals[1]), 0)
            except Exception:
                continue
            cur = "▶" in str(vals[0])
            self.disasm.set(iid, "mark", ("●" if addr in bps else "") + ("▶" if cur else ""))

    def _on_goto(self):
        """Adresse/Symbol aus dem Gehe-zu-Feld -> Controller-Navigation."""
        if getattr(self, "controller", None):
            try:
                self.controller.goto_text(self.disasm_goto.get())
            except Exception:
                pass

    def set_breakpoint_marks(self, addrs):
        """Vom Controller: BP-Adressen uebernehmen und anzeigen (z. B. nach 🔄 Laden)."""
        out = set()
        for a in (addrs or []):
            try:
                out.add(int(a) if isinstance(a, int) else int(str(a), 0))
            except Exception:
                pass
        self._bp_addrs = out
        self._refresh_bp_marks()

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

    def _on_disasm_resolve_reloc(self):
        """Loest den Reloc-Zeiger an einer Daten-/Zeigeradresse auf (delegiert an den Controller)."""
        if not getattr(self, "controller", None):
            return
        pre = ""
        try:
            vals = self.disasm.item(self.disasm.focus(), "values")
            if len(vals) > 1:
                pre = str(vals[1])        # Spalte 1 = Adresse
        except Exception:
            pass
        from tkinter import simpledialog
        a = simpledialog.askstring("Reloc/Zeiger aufloesen",
                                   "Daten-/Zeigeradresse (0x..) aufloesen:", initialvalue=pre)
        if a:
            self.controller.resolve_reloc(a)

    def _post_disasm_menu(self, event):
        try:
            iid = self.disasm.identify_row(event.y)
            if iid:
                self.disasm.focus(iid); self.disasm.selection_set(iid)
            self.disasm_menu.post(event.x_root, event.y_root)
        except Exception:
            pass

    def _on_outline_rmb(self, event):
        try:
            iid = self.outline.identify_row(event.y)
            if iid:
                self.outline.selection_set(iid); self.outline.focus(iid)
            self._outline_menu.post(event.x_root, event.y_root)
        except Exception:
            pass

    def _on_outline_rename(self):
        if not getattr(self, "controller", None):
            return
        sel = self.outline.focus()
        if not sel:
            return
        vals = self.outline.item(sel, "values")
        kind = vals[0] if vals else ""
        addr = vals[2] if len(vals) > 2 else ""
        if kind not in ("fn", "sub", "data") or not addr or addr == "0x0":
            self.controller._log("Nur definierte Funktionen/Daten sind annotierbar (keine Importe)."); return
        try:
            a = int(addr, 16)
        except Exception:
            return
        from tkinter import simpledialog
        cur = (vals[1] if len(vals) > 1 else "").replace("\u270e ", "")
        name = simpledialog.askstring("Umbenennen", f"Neuer Name fuer {addr}:", initialvalue=str(cur))
        if not name:
            return
        note = simpledialog.askstring("Notiz (optional)", f"Notiz fuer {addr}:") or ""
        self.controller.annotate(a, name, note)

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

    def _c_tip_show(self, text, x_root, y_root):
        """Gelber Mouseover-Tooltip (voller Signaturname) ueber einem Tab-Chip (#168)."""
        if not text:
            return
        tip = getattr(self, "_c_tip", None)
        if tip is None or not tip.winfo_exists():
            tip = tk.Toplevel(self._c_host)
            try:
                tip.overrideredirect(True)
            except Exception:
                pass
            lbl = tk.Label(tip, justify="left", bg="#ffffe0", relief="solid", borderwidth=1,
                           font=("Consolas", 9), wraplength=760)
            lbl.pack(ipadx=4, ipady=2)
            self._c_tip, self._c_tip_lbl = tip, lbl
        self._c_tip_lbl.config(text=text)
        try:
            self._c_tip.geometry("+%d+%d" % (x_root + 12, y_root + 20))
            self._c_tip.deiconify(); self._c_tip.lift()
        except Exception:
            pass

    def _c_tip_hide(self):
        tip = getattr(self, "_c_tip", None)
        if tip is not None:
            try:
                tip.withdraw()
            except Exception:
                pass

    def _build_c_drawer(self):
        """C-Spalte: einzeilige Kopfzeile (Titel | Tab-Leiste mit ◀▶ | Buttons) + Inhalt (#168)."""
        host = self._c_host
        self._c_tabs = {}
        self._c_order = []
        self._c_active = None
        self._c_dragging = None
        self._c_drawer_visible = True

        hdr = ttk.Frame(host); hdr.pack(side="top", fill="x")
        self._c_hdr = hdr
        ttk.Label(hdr, text="🧊 C", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 6))

        # rechte Button-Gruppe: Aktivtab-Aktionen + Spalten-Steuerung
        rgrp = ttk.Frame(hdr); rgrp.pack(side="right", padx=(4, 2))
        self._c_btn_dock = ttk.Button(rgrp, text="⧉ Abdocken", width=11,
            command=lambda: self.app.workspace_tab.toggle_dock(
                host, "🧊 Dekompilat (C)", host, self._c_paned, 2, self._c_btn_dock))
        self._c_btn_vis = ttk.Button(rgrp, text="▸ ausblenden", width=12,
                                     command=lambda: self._c_col_set(False))
        self._c_act_run = ttk.Button(rgrp, text="▶ Run-Panel", width=12, state="disabled",
                                     command=self._c_act_run_cb)
        self._c_act_dis = ttk.Button(rgrp, text="🔎 Disasm", width=10, state="disabled",
                                     command=self._c_act_dis_cb)
        self._c_act_close = ttk.Button(rgrp, text="✖", width=3, state="disabled",
                                       command=self._c_act_close_cb)
        for b in (self._c_act_run, self._c_act_dis, self._c_act_close, self._c_btn_vis, self._c_btn_dock):
            b.pack(side="left", padx=2)
        _add_tooltip(self._c_btn_dock, "C-Dekompilat-Spalte als eigenes Fenster ab-/andocken.")
        _add_tooltip(self._c_btn_vis, "C-Dekompilat-Spalte ausblenden (wieder ueber den Stub/Topbar-Button).")
        _add_tooltip(self._c_act_run, "Aktives C-Dekompilat ins Run-Panel uebernehmen (Entry setzen).")
        _add_tooltip(self._c_act_dis, "Zur Funktion des aktiven C-Dekompilats im Disassembly springen.")
        _add_tooltip(self._c_act_close, "Aktiven C-Dekompilat-Reiter schliessen.")

        # Mitte: ◀  scrollbarer Tab-Streifen  ▶
        mid = ttk.Frame(hdr)
        ttk.Button(mid, text="◀", width=2, command=lambda: self._c_scroll(-1)).pack(side="left")
        ttk.Button(mid, text="▶", width=2, command=lambda: self._c_scroll(1)).pack(side="right")
        self._c_tabcanvas = tk.Canvas(mid, height=26, highlightthickness=0)
        self._c_tabcanvas.pack(side="left", fill="x", expand=True)
        self._c_tabstrip = ttk.Frame(self._c_tabcanvas)
        self._c_tabwin = self._c_tabcanvas.create_window((0, 0), window=self._c_tabstrip, anchor="nw")
        self._c_tabstrip.bind("<Configure>",
            lambda e: self._c_tabcanvas.configure(scrollregion=self._c_tabcanvas.bbox("all")))
        mid.pack(side="left", fill="x", expand=True, padx=4)

        self._c_hint = ttk.Label(self._c_tabstrip, text="— noch nichts dekompiliert (🧊 C) —",
                                 foreground="gray")
        self._c_hint.pack(side="left", padx=4)

        self._c_body = ttk.Frame(host); self._c_body.pack(side="top", fill="both", expand=True)
        # Schmaler Stub mit sichtbarem Einblenden-Knopf: bleibt in der Paned, wenn die Spalte aus ist.
        self._c_stub = ttk.Frame(self._c_paned, width=20)
        try:
            ttk.Style().configure("Slim.TButton", padding=(2, 0), font=("Segoe UI", 7))
        except Exception:
            pass
        self._c_stub_btn = ttk.Button(self._c_stub, text="\U0001f9ca\n\u25b8\nC", width=2,
                                      style="Slim.TButton", command=lambda: self._c_col_set(True))
        self._c_stub_btn.pack(side="top", fill="y", expand=True, padx=1, pady=2)
        _add_tooltip(self._c_stub_btn, "C-Dekompilat-Spalte wieder einblenden.")

    def _c_scroll(self, direction):
        try:
            self._c_tabcanvas.xview_scroll(int(direction) * 2, "units")
        except Exception:
            pass

    def _c_act_run_cb(self):
        e = self._c_tabs.get(self._c_active) if self._c_active else None
        if e and e.get("addr") is not None and getattr(self, "controller", None):
            self.controller.load_addr_into_run(e["addr"])

    def _c_act_dis_cb(self):
        e = self._c_tabs.get(self._c_active) if self._c_active else None
        if e and e.get("addr") is not None and getattr(self, "controller", None):
            self.controller.navigate(e["addr"], set_entry=False)

    def _c_act_close_cb(self):
        if self._c_active:
            self._c_close(self._c_active)

    def _c_select(self, key):
        entry = self._c_tabs.get(key)
        if not entry:
            return
        for k, e in self._c_tabs.items():
            try:
                e["content"].pack_forget()
            except Exception:
                pass
            try:
                e["chip"].configure(relief=("solid" if k == key else "raised"))
            except Exception:
                pass
        try:
            entry["content"].pack(side="top", fill="both", expand=True)
        except Exception:
            pass
        self._c_active = key
        st = ("normal" if entry.get("addr") is not None else "disabled")
        try:
            self._c_act_run.config(state=st); self._c_act_dis.config(state=st)
            self._c_act_close.config(state="normal")
        except Exception:
            pass

    def _c_relayout_chips(self):
        for k in list(self._c_order):
            e = self._c_tabs.get(k)
            if e:
                try:
                    e["chip"].pack_forget()
                except Exception:
                    pass
        for k in self._c_order:
            e = self._c_tabs.get(k)
            if e:
                e["chip"].pack(side="left", padx=2, pady=1)
        try:
            self._c_tabcanvas.update_idletasks()
            self._c_tabcanvas.configure(scrollregion=self._c_tabcanvas.bbox("all"))
        except Exception:
            pass

    def _c_close(self, key):
        entry = self._c_tabs.pop(key, None)
        if key in self._c_order:
            self._c_order.remove(key)
        if entry:
            for w in ("chip", "content"):
                try:
                    entry[w].destroy()
                except Exception:
                    pass
        if self._c_active == key:
            self._c_active = None
            if self._c_order:
                self._c_select(self._c_order[-1])
        if not self._c_tabs:
            try:
                self._c_hint.pack(side="left", padx=4)
            except Exception:
                pass

    def _c_drag_start(self, key):
        self._c_dragging = key

    def _c_drag_motion(self, event):
        key = getattr(self, "_c_dragging", None)
        if not key:
            return
        try:
            wx = self._c_tabstrip.winfo_pointerx() - self._c_tabstrip.winfo_rootx()
        except Exception:
            return
        target = None
        for k in self._c_order:
            e = self._c_tabs.get(k)
            if not e:
                continue
            c = e["chip"]
            try:
                x0 = c.winfo_x(); x1 = x0 + c.winfo_width()
            except Exception:
                continue
            if x0 <= wx <= x1:
                target = k; break
        if target and target != key:
            try:
                i = self._c_order.index(key); j = self._c_order.index(target)
                self._c_order.insert(j, self._c_order.pop(i))
                self._c_relayout_chips()
            except Exception:
                pass

    def _c_drag_end(self):
        self._c_dragging = None

    def _c_col_set(self, show):
        """Blendet die C-Spalte ganz ein/aus. Eingeklappt bleibt ein schmaler Stub mit
        sichtbarem Einblenden-Knopf in der Paned stehen -> Platz wird frei (#167)."""
        host = getattr(self, "_c_host", None)
        paned = getattr(self, "_c_paned", None)
        stub = getattr(self, "_c_stub", None)
        if host is None or paned is None:
            return
        if getattr(host, "_is_undocked", False):
            return  # abgedockt: erst andocken, dann ein-/ausblenden
        vis = getattr(self, "_c_col_visible", True)
        try:
            if show and not vis:
                if stub is not None:
                    try:
                        paned.forget(stub)
                    except Exception:
                        pass
                paned.add(host, weight=2); self._c_col_visible = True
            elif (not show) and vis:
                paned.forget(host)
                if stub is not None:
                    paned.add(stub, weight=0)
                self._c_col_visible = False
        except Exception:
            pass

    def _c_col_toggle(self):
        self._c_col_set(not getattr(self, "_c_col_visible", True))

    def _c_ensure_visible(self):
        """Stellt sicher, dass die C-Spalte sichtbar ist (vor dem Anzeigen eines Dekompilats)."""
        self._c_col_set(True)
        if not getattr(self, "_c_drawer_visible", True):
            try:
                self._c_body.pack(side="top", fill="both", expand=True)
            except Exception:
                pass
            self._c_drawer_visible = True

    def show_decompile(self, title, code, addr=None, base=None):
        """C-Dekompilat je Funktion als eigener Tab (eigene Chip-Leiste, #168)."""
        import re as _re
        self._c_ensure_visible()
        try:
            self._c_hint.pack_forget()
        except Exception:
            pass
        if base is None:
            m = _re.search(r"ImageBase 0x([0-9a-fA-F]+)", code or "")
            if m:
                try:
                    base = int(m.group(1), 16)
                except Exception:
                    base = None
        key = addr if addr is not None else title
        entry = self._c_tabs.get(key)
        if entry is None or not entry["content"].winfo_exists():
            content = ttk.Frame(self._c_body)
            txt = tk.Text(content, wrap="none", font=("Consolas", 10))
            csb = ttk.Scrollbar(content, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=csb.set)
            csb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            txt.bind("<Double-Button-1>", self._on_c_double)
            chip = ttk.Frame(self._c_tabstrip, relief="raised", borderwidth=1)
            name_lbl = ttk.Label(chip, text="", cursor="hand2")
            name_lbl.pack(side="left", padx=(6, 2))
            close_lbl = ttk.Label(chip, text="✕", cursor="hand2", foreground="#a00")
            close_lbl.pack(side="left", padx=(0, 4))
            entry = {"content": content, "text": txt, "chip": chip, "name_lbl": name_lbl,
                     "addr": addr, "base": base, "full": "", "title": title}
            self._c_tabs[key] = entry
            self._c_order.append(key)
            name_lbl.bind("<Button-1>", lambda e, k=key: self._c_select(k))
            name_lbl.bind("<Enter>", lambda e, k=key: self._c_tip_show(self._c_tabs[k]["full"], e.x_root, e.y_root))
            name_lbl.bind("<Motion>", lambda e, k=key: self._c_tip_show(self._c_tabs[k]["full"], e.x_root, e.y_root))
            name_lbl.bind("<Leave>", lambda e: self._c_tip_hide())
            close_lbl.bind("<Button-1>", lambda e, k=key: self._c_close(k))
            name_lbl.bind("<ButtonPress-1>", lambda e, k=key: self._c_drag_start(k), add="+")
            name_lbl.bind("<B1-Motion>", self._c_drag_motion)
            name_lbl.bind("<ButtonRelease-1>", lambda e: self._c_drag_end())
            self._c_relayout_chips()
        entry["addr"] = addr if addr is not None else entry.get("addr")
        entry["base"] = base
        entry["text"]._c_base = base
        entry["full"] = self._full_sig_from_c(code) or self._short_name(title)
        entry["title"] = title
        try:
            entry["name_lbl"].config(text=self._c_tab_label(title, code, addr))
        except Exception:
            pass
        txt = entry["text"]
        txt.delete("1.0", "end")
        txt.insert("end", code or "(leer — Ghidra lieferte kein C)")
        self._highlight_c(txt)
        self._c_select(key)
        try:
            if getattr(self._c_host, "_is_undocked", False):
                self._c_host.lift()
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
        """Rueckwaertskompatibel: schliesst den Tab zu einem Content-Frame (#168)."""
        try:
            for k, e in list(self._c_tabs.items()):
                if e.get("content") is frame or e.get("frame") is frame:
                    self._c_close(k); return
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
        for i in self.tree_history.get_children():
            self.tree_history.delete(i)
        if getattr(self, "controller", None):
            self.controller._reset_history()
        try:
            self.lbl_loaded.config(text="— keine Lib geladen —")
        except Exception:
            pass
        self.append_status("geleert.")
