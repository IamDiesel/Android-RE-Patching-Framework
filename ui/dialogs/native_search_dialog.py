"""NativeGlobalSearchWindow — globale Suche fuer das Native Studio.

Nach dem Vorbild von ui/dialogs/global_search_dialog.py (Smali Studio), aber fuer
native ELFs: sucht Funktionen/Symbole (aus NativeIndex), Strings (ASCII in den
geladenen Segmenten) oder springt direkt zu einer Hex-Adresse. Doppelklick auf ein
Ergebnis ruft den Navigations-Callback des Controllers (disasm + XREF + Historie).
"""
import tkinter as tk
from tkinter import ttk
import threading


def iter_strings(image, min_len=4, cap=300000):
    """ASCII-Strings aus allen geladenen Segmenten als (vaddr, text)."""
    out = []
    for seg in image.segments:
        size = min(seg.filesz, len(image.data) - seg.offset)
        if size <= 0:
            continue
        data = image.data[seg.offset:seg.offset + size]
        start = None
        for i, b in enumerate(data):
            if 32 <= b < 127:
                if start is None:
                    start = i
            else:
                if start is not None and i - start >= min_len:
                    out.append((seg.vaddr + start, data[start:i].decode("latin1")))
                    if len(out) >= cap:
                        return out
                start = None
        if start is not None and len(data) - start >= min_len:
            out.append((seg.vaddr + start, data[start:].decode("latin1")))
    return out


class NativeGlobalSearchWindow(tk.Toplevel):
    def __init__(self, parent, index, image, navigate_cb):
        super().__init__(parent)
        self.index = index
        self.image = image
        self.navigate_cb = navigate_cb
        self._strings = None  # lazy, gecacht
        self.title("🔍 Native-Suche — Funktion / String / Adresse")
        self.geometry("840x560")
        self.attributes("-topmost", True)
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Suche:").pack(side="left")
        self.ent = ttk.Entry(top, width=42)
        self.ent.pack(side="left", padx=6)
        self.mode = tk.StringVar(value="func")
        for txt, val in (("Funktion", "func"), ("String", "str"), ("Adresse", "addr"), ("ASM/Kante", "asm"), ("Wildcard-AOB", "aob")):
            ttk.Radiobutton(top, text=txt, value=val, variable=self.mode).pack(side="left", padx=3)
        ttk.Button(top, text="Suchen", command=self.do_search).pack(side="left", padx=6)
        self.lbl = ttk.Label(self, text=f"{len(self.index.syms)} Symbole indiziert. Doppelklick = dorthin springen.")
        self.lbl.pack(anchor="w", padx=10)

        ft = ttk.Frame(self)
        ft.pack(fill="both", expand=True, padx=10, pady=6)
        self.tree = ttk.Treeview(ft, columns=("typ", "addr", "text"), show="headings")
        self.tree.heading("typ", text="Typ"); self.tree.column("typ", width=70, anchor="w")
        self.tree.heading("addr", text="Adresse"); self.tree.column("addr", width=120, anchor="w")
        self.tree.heading("text", text="Treffer"); self.tree.column("text", width=600, anchor="w")
        sb = ttk.Scrollbar(ft, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.ent.bind("<Return>", lambda e: self.do_search())
        self.tree.bind("<Double-1>", self._on_double)
        self.ent.focus_set()

    def _clear(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def do_search(self):
        self._clear()
        term = self.ent.get().strip()
        if not term:
            return
        mode = self.mode.get()
        if mode == "addr":
            try:
                a = int(term, 16)
            except Exception:
                self.lbl.config(text=f"Keine gueltige Hex-Adresse: {term!r}")
                return
            self.tree.insert("", "end", values=("addr", f"0x{a:x}", "(direkt springen)"))
            self.lbl.config(text="1 Adresse — Doppelklick zum Springen.")
            return
        if mode == "func":
            t = term.lower()
            hits = [(a, n) for a, n in self.index.syms if t in n.lower()]
            for a, n in hits[:2000]:
                self.tree.insert("", "end", values=("func", f"0x{a:x}", n))
            self.lbl.config(text=f"{len(hits)} Funktions-/Symboltreffer (max. 2000 gezeigt).")
            return
        if mode == "asm":
            hits = self.index.search(term.lower(), limit=1500)
            for src, mnem, tgt in hits:
                fn = self.index.func_at(src) or "?"
                self.tree.insert("", "end", values=("asm", f"0x{src:x}", f"[{fn}]  {mnem} -> 0x{tgt:x}"))
            self.lbl.config(text=f"{len(hits)} Instruktions-/Kanten-Treffer (max. 1500 gezeigt).")
            return
        if mode == "aob":
            if hasattr(self.index, "search_aob"):
                hits = self.index.search_aob(self.image, term, exec_only=False, limit=500)
            else:
                hits = []
            for a in hits:
                fn = self.index.func_at(a) or "?"
                self.tree.insert("", "end", values=("aob", f"0x{a:x}", f"[{fn}]  Pattern: {term}"))
            self.lbl.config(text=f"{len(hits)} Byte-Pattern-Treffer (z. B. 'FD 7B ?? A9').")
            return
        # strings
        self.lbl.config(text="Scanne Strings in den Segmenten …")
        self.update_idletasks()

        def work():
            if self._strings is None:
                self._strings = iter_strings(self.image)
            t = term.lower()
            hits = [(a, s) for a, s in self._strings if t in s.lower()]

            def show():
                for a, s in hits[:2000]:
                    self.tree.insert("", "end", values=("str", f"0x{a:x}", s[:200]))
                self.lbl.config(text=f"{len(hits)} String-Treffer (max. 2000 gezeigt). "
                                     f"Doppelklick zeigt per XREF, wer den String referenziert.")
            self.after(0, show)
        threading.Thread(target=work, daemon=True).start()

    def _on_double(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if len(vals) >= 2 and str(vals[1]).startswith("0x"):
            self.navigate_cb(int(vals[1], 16))
