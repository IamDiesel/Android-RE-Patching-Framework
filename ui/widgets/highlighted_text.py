# -*- coding: utf-8 -*-
"""highlighted_text — wiederverwendbares, read-only Syntax-Highlight-Text-Widget.

Herausgeloest aus dem Python-Lift-/C-Highlighter (AP1 GUI-Refactoring). Kern ist die **reine**,
tkinter-freie Funktion ``tokenize(text, lexer)`` -> Liste von (start, end, tag) Spans; sie ist
headless unit-testbar. Das Widget ``HighlightedText`` wendet die Spans als Text-Tags an.

Lexer: ``python`` | ``disasm`` (AArch64) | ``pseudocode`` | ``c`` | ``text`` (kein Highlight).
Tags (VS-Code-Dark-Palette): keyword, type, number, string, comment, builtin, register, mnemonic, addr.
"""
from __future__ import annotations

import re
from typing import List, Tuple

Span = Tuple[int, int, str]

# ---- Palette (VS-Code-Dark, konsistent mit CCodeEditorWidget) -------------------
PALETTE = {
    "keyword":  "#569CD6",
    "type":     "#4EC9B0",
    "number":   "#B5CEA8",
    "string":   "#CE9178",
    "comment":  "#6A9955",
    "builtin":  "#DCDCAA",
    "register": "#9CDCFE",
    "mnemonic": "#C586C0",
    "addr":     "#4FC1FF",
}

# ---- Vokabulare -----------------------------------------------------------------
_PY_KW = {
    "def", "return", "if", "elif", "else", "for", "while", "break", "continue",
    "import", "from", "as", "class", "lambda", "try", "except", "finally", "with",
    "in", "not", "and", "or", "is", "pass", "yield", "raise", "assert", "del",
    "global", "nonlocal", "None", "True", "False", "async", "await",
}
_PY_BUILTIN = {
    "print", "range", "len", "int", "hex", "bin", "str", "bytes", "bytearray",
    "list", "dict", "set", "tuple", "abs", "min", "max", "sum", "enumerate",
    "zip", "map", "filter", "sorted", "isinstance", "getattr", "setattr", "ord",
    "chr", "bool", "float",
}
_C_KW = {
    "if", "else", "for", "while", "do", "switch", "case", "default", "break",
    "continue", "return", "goto", "sizeof", "typedef", "struct", "union", "enum",
    "static", "const", "volatile", "extern", "register", "inline",
}
_C_TY = {
    "void", "char", "short", "int", "long", "float", "double", "signed",
    "unsigned", "bool", "size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t", "undefined", "undefined1",
    "undefined2", "undefined4", "undefined8", "code", "byte", "uint", "ulong",
}
_PSEUDO_KW = {
    "goto", "if", "else", "call", "return", "ret", "loop", "while", "break",
    "TRAP", "then", "jump", "br", "blr",
}

# AArch64-Register
_REG_RE = r"\b(?:[wx](?:[12]?\d|3[01])|sp|lr|pc|xzr|wzr|fp)\b"
# Mnemonic am Zeilenanfang (optional nach Adresse/Label)
_MNEMONIC_RE = r"(?m)^\s*(?:0x[0-9a-fA-F]+:?\s+)?([a-z][a-z0-9.]{1,10})\b"
_ADDR_RE = r"\b0x[0-9a-fA-F]+\b"
_NUM_RE = r"#?-?\b\d+\b"
_WORD_RE = r"[A-Za-z_][A-Za-z0-9_]*"


def _word_spans(text: str, kw=None, builtin=None, ty=None) -> List[Span]:
    out: List[Span] = []
    for m in re.finditer(_WORD_RE, text):
        w = m.group(0)
        if kw and w in kw:
            out.append((m.start(), m.end(), "keyword"))
        elif ty and w in ty:
            out.append((m.start(), m.end(), "type"))
        elif builtin and w in builtin:
            out.append((m.start(), m.end(), "builtin"))
    return out


def _re_spans(text: str, pattern: str, tag: str, flags=0, group=0) -> List[Span]:
    out: List[Span] = []
    for m in re.finditer(pattern, text, flags):
        out.append((m.start(group), m.end(group), tag))
    return out


def tokenize(text: str, lexer: str = "text") -> List[Span]:
    """Rein: liefert (start, end, tag)-Spans fuer ``text`` je ``lexer``. Kein tkinter.

    Reihenfolge der Rueckgabe = Anwendungsreihenfolge; Strings/Kommentare kommen zuletzt,
    damit sie (via tag_raise im Widget) gewinnen.
    """
    text = text or ""
    lx = (lexer or "text").lower()
    spans: List[Span] = []
    if lx == "python":
        spans += _word_spans(text, kw=_PY_KW, builtin=_PY_BUILTIN)
        spans += _re_spans(text, _ADDR_RE, "addr")
        spans += _re_spans(text, r"\b\d+\b", "number")
        spans += _re_spans(text, r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", "string")
        spans += _re_spans(text, r"#[^\n]*", "comment")
    elif lx == "c":
        spans += _word_spans(text, kw=_C_KW, ty=_C_TY)
        spans += _re_spans(text, _ADDR_RE, "addr")
        spans += _re_spans(text, r"\b\d+\b", "number")
        spans += _re_spans(text, r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", "string")
        spans += _re_spans(text, r"/\*.*?\*/|//[^\n]*", "comment", re.DOTALL)
    elif lx == "disasm":
        spans += _re_spans(text, _MNEMONIC_RE, "mnemonic", group=1)
        spans += _re_spans(text, _REG_RE, "register")
        spans += _re_spans(text, _ADDR_RE, "addr")
        spans += _re_spans(text, _NUM_RE, "number")
        spans += _re_spans(text, r";[^\n]*|//[^\n]*", "comment")
    elif lx == "pseudocode":
        spans += _word_spans(text, kw=_PSEUDO_KW)
        spans += _re_spans(text, _ADDR_RE, "addr")
        spans += _re_spans(text, r"\b\d+\b", "number")
        spans += _re_spans(text, r"#[^\n]*", "comment")
    # "text" -> keine Spans
    return spans


LEXERS = ("text", "python", "disasm", "pseudocode", "c")

try:
    import tkinter as tk
    from tkinter import ttk

    class HighlightedText(ttk.Frame):
        """Read-only Text mit Syntax-Highlight (nutzt ``tokenize``). Dark-Theme, Scrollbars."""

        def __init__(self, parent, lexer: str = "text", height: int = 12,
                     font=("Consolas", 10)):
            super().__init__(parent)
            self.lexer = lexer
            self.sy = ttk.Scrollbar(self, orient="vertical")
            self.sx = ttk.Scrollbar(self, orient="horizontal")
            self.text = tk.Text(self, wrap="none", font=font, height=height,
                                bg="#1E1E1E", fg="#D4D4D4", insertbackground="white",
                                yscrollcommand=self.sy.set, xscrollcommand=self.sx.set)
            self.sy.config(command=self.text.yview)
            self.sx.config(command=self.text.xview)
            self.sy.pack(side="right", fill="y")
            self.sx.pack(side="bottom", fill="x")
            self.text.pack(side="left", fill="both", expand=True)
            for tag, col in PALETTE.items():
                self.text.tag_configure(tag, foreground=col)
            self.text.config(state="disabled")

        def set_lexer(self, lexer: str):
            self.lexer = lexer

        def set_text(self, content: str, lexer: str = None):
            if lexer is not None:
                self.lexer = lexer
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", content or "")
            for tag in PALETTE:
                self.text.tag_remove(tag, "1.0", "end")
            for a, b, tag in tokenize(content or "", self.lexer):
                try:
                    self.text.tag_add(tag, "1.0+%dc" % a, "1.0+%dc" % b)
                except Exception:
                    pass
            # Strings/Kommentare gewinnen
            for tag in ("string", "comment"):
                self.text.tag_raise(tag)
            self.text.config(state="disabled")

        def get_text(self):
            return self.text.get("1.0", "end-1c")

except Exception:  # pragma: no cover - ohne tkinter (headless) nur tokenize()
    HighlightedText = None
