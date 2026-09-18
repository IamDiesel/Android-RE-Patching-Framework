import tkinter as tk
from tkinter import ttk
import re

C_KEYWORDS = r"\b(if|else|for|while|do|switch|case|default|break|continue|return|goto|sizeof|typedef|struct|union|enum|static|const|volatile|extern|register|inline|restrict|__attribute__)\b"
C_TYPES = r"\b(void|char|short|int|long|float|double|signed|unsigned|_Bool|size_t|ssize_t|uint8_t|uint16_t|uint32_t|uint64_t|int8_t|int16_t|int32_t|int64_t|uintptr_t|intptr_t|jint|jlong|jobject|jclass|JNIEnv|JavaVM|pthread_t|FILE)\b"
C_PREPROC = r"^\s*#.*$"
C_STRING = r"\"(\\.|[^\"\\])*\"|'(\\.|[^'\\])*'"
C_COMMENT_LINE = r"//[^\n]*"
C_COMMENT_BLOCK = r"/\*.*?\*/"
C_NUMBER = r"\b(0[xX][0-9a-fA-F]+|\d+\.?\d*[fFuUlL]*)\b"


class CCodeEditorWidget(ttk.Frame):
    """Einfacher C-Editor mit debounced Regex-Syntax-Highlighting (dark)."""

    def __init__(self, parent):
        super().__init__(parent)
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        self.sy = ttk.Scrollbar(container, orient="vertical")
        self.sx = ttk.Scrollbar(container, orient="horizontal")
        self.text = tk.Text(container, wrap="none", font=("Consolas", 10),
                            bg="#1E1E1E", fg="#D4D4D4", insertbackground="white",
                            undo=True, yscrollcommand=self.sy.set, xscrollcommand=self.sx.set)
        self.sy.config(command=self.text.yview)
        self.sx.config(command=self.text.xview)
        self.sy.pack(side="right", fill="y")
        self.sx.pack(side="bottom", fill="x")
        self.text.pack(side="left", fill="both", expand=True)

        self._setup_tags()
        self._debounce = None
        self._readonly = False
        self.text.bind("<KeyRelease>", self._on_change)

    def _setup_tags(self):
        self.text.tag_configure("preproc", foreground="#C586C0")
        self.text.tag_configure("keyword", foreground="#569CD6")
        self.text.tag_configure("type", foreground="#4EC9B0")
        self.text.tag_configure("number", foreground="#B5CEA8")
        self.text.tag_configure("string", foreground="#CE9178")
        self.text.tag_configure("comment", foreground="#6A9955")

    def set_text(self, code):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", code or "")
        self._highlight()
        if self._readonly:
            self.text.config(state="disabled")

    def get_text(self):
        return self.text.get("1.0", "end-1c")

    def set_readonly(self, ro: bool):
        self._readonly = ro
        self.text.config(state="disabled" if ro else "normal")

    def _on_change(self, event=None):
        if self._readonly:
            return
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(150, self._highlight)

    def _apply(self, pattern, tag, flags=0):
        content = self.text.get("1.0", "end-1c")
        for m in re.finditer(pattern, content, flags):
            start = self.text.index(f"1.0+{m.start()}c")
            end = self.text.index(f"1.0+{m.end()}c")
            self.text.tag_add(tag, start, end)

    def _highlight(self):
        self._debounce = None
        for tag in ("preproc", "keyword", "type", "number", "string", "comment"):
            self.text.tag_remove(tag, "1.0", "end")
        # Reihenfolge: erst Code-Tokens, dann String/Kommentar (die gewinnen via tag_raise)
        self._apply(C_KEYWORDS, "keyword")
        self._apply(C_TYPES, "type")
        self._apply(C_NUMBER, "number")
        self._apply(C_PREPROC, "preproc", re.MULTILINE)
        self._apply(C_STRING, "string")
        self._apply(C_COMMENT_BLOCK, "comment", re.DOTALL)
        self._apply(C_COMMENT_LINE, "comment")
        self.text.tag_raise("string")
        self.text.tag_raise("comment")
