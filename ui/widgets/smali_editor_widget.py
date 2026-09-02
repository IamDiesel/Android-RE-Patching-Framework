import tkinter as tk
from tkinter import ttk
import re


class SmaliEditorWidget(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.create_widgets()
        self.setup_tags()
        self._bind_lazy_highlighting()

    def create_widgets(self):
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill="both", expand=True)

        # Original Code (Read-Only)
        f_orig = ttk.LabelFrame(paned)
        paned.add(f_orig, weight=1)

        f_orig_header = ttk.Frame(f_orig)
        ttk.Label(f_orig_header, text="Original Code (Read-Only)").pack(side="left")
        self.btn_find_cg = ttk.Button(f_orig_header, text="🔍 Find in Call Graph")
        self.btn_find_cg.pack(side="left", padx=10)
        f_orig.config(labelwidget=f_orig_header)

        # Scrollbar anbinden (Notwendig für Viewport-Tracking)
        self.scroll_orig = ttk.Scrollbar(f_orig, orient="vertical")
        self.txt_orig = tk.Text(f_orig, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white", yscrollcommand=self.scroll_orig.set)
        self.scroll_orig.config(command=self.txt_orig.yview)
        self.scroll_orig.pack(side="right", fill="y")
        self.txt_orig.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_orig.bind("<Key>", lambda e: "break")

        # Edit Buttons (Mitte)
        f_mid = ttk.Frame(paned)
        paned.add(f_mid, weight=0)

        ttk.Button(f_mid, text="⬇ Code zum Editieren kopieren ⬇", command=self.copy_to_edit).pack(side="left", fill="x",
                                                                                                  expand=True, padx=2,
                                                                                                  pady=4)

        self.btn_snippet = ttk.Button(f_mid, text="➕ Snippet einfügen")
        self.btn_snippet.pack(side="right", fill="x", expand=True, padx=2, pady=4)

        # Editierter Code
        f_edit = ttk.LabelFrame(paned, text="Editierter Code (Dein Patch)")
        paned.add(f_edit, weight=1)

        self.scroll_edit = ttk.Scrollbar(f_edit, orient="vertical")
        self.txt_edit = tk.Text(f_edit, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white", yscrollcommand=self.scroll_edit.set)
        self.scroll_edit.config(command=self.txt_edit.yview)
        self.scroll_edit.pack(side="right", fill="y")
        self.txt_edit.pack(fill="both", expand=True, padx=2, pady=2)

    def _bind_lazy_highlighting(self):
        """Bindet Events für das asynchrone, Viewport-basierte Highlighting."""
        events = ["<KeyRelease>", "<MouseWheel>", "<Button-4>", "<Button-5>", "<Configure>"]

        for txt in [self.txt_orig, self.txt_edit]:
            for event in events:
                txt.bind(event, lambda e, t=txt: self._debounced_highlight(t), add="+")

            # Hook the scrollbar commands to trigger highlighting when dragged
            if txt == self.txt_orig:
                self.scroll_orig.config(command=lambda *args: self._on_scroll(self.txt_orig, self.scroll_orig, *args))
            else:
                self.scroll_edit.config(command=lambda *args: self._on_scroll(self.txt_edit, self.scroll_edit, *args))

    def _on_scroll(self, txt_widget, scrollbar, *args):
        txt_widget.yview(*args)
        self._debounced_highlight(txt_widget)

    def _debounced_highlight(self, text_widget):
        if getattr(text_widget, "_hl_timer", None):
            self.after_cancel(text_widget._hl_timer)
        text_widget._hl_timer = self.after(150, lambda: self.apply_highlighting(text_widget))

    def setup_tags(self):
        for txt in [self.txt_orig, self.txt_edit]:
            txt.tag_configure("keyword", foreground="#569CD6", font=("Consolas", 10, "bold"))
            txt.tag_configure("instruction", foreground="#C586C0")
            txt.tag_configure("register", foreground="#9CDCFE")
            txt.tag_configure("string", foreground="#CE9178")
            txt.tag_configure("comment", foreground="#6A9955", font=("Consolas", 10, "italic"))
            txt.tag_configure("class", foreground="#4EC9B0")

    def copy_to_edit(self):
        content = self.txt_orig.get("1.0", tk.END).strip()
        self.txt_edit.delete("1.0", tk.END)
        self.txt_edit.insert("1.0", content)
        self._debounced_highlight(self.txt_edit)

    def load_code(self, code_block):
        self.txt_orig.config(state="normal")
        self.txt_orig.delete("1.0", tk.END)
        self.txt_orig.insert("1.0", code_block)
        self._debounced_highlight(self.txt_orig)
        self.txt_orig.config(state="disabled")
        self.clear_edit()

    def get_orig_text(self):
        return self.txt_orig.get("1.0", tk.END).strip()

    def get_edit_text(self):
        return self.txt_edit.get("1.0", tk.END).strip()

    def clear_edit(self):
        self.txt_edit.delete("1.0", tk.END)

    def rehighlight(self):
        self._debounced_highlight(self.txt_orig)
        self._debounced_highlight(self.txt_edit)

    def apply_highlighting(self, text_widget):
        if not text_widget.winfo_exists(): return

        try:
            top_idx = text_widget.index("@0,0")
            bottom_idx = text_widget.index(f"@0,{text_widget.winfo_height()}")
        except tk.TclError:
            return

        # Puffer von 30 Zeilen oben und unten, damit flüssig gescrollt werden kann
        top_line = max(1, int(top_idx.split('.')[0]) - 30)
        bottom_line = int(bottom_idx.split('.')[0]) + 30

        start_idx = f"{top_line}.0"
        end_idx = f"{bottom_line}.end"

        content = text_widget.get(start_idx, end_idx)

        for tag in ["keyword", "instruction", "register", "string", "comment", "class"]:
            text_widget.tag_remove(tag, start_idx, end_idx)

        # Highlighting mit dynamischem Viewport-Offset
        for match in re.finditer(r'(".*?")', content):
            text_widget.tag_add("string", f"{start_idx} + {match.start(1)} chars",
                                f"{start_idx} + {match.end(1)} chars")

        for match in re.finditer(r'(#.*)$', content, re.MULTILINE):
            text_widget.tag_add("comment", f"{start_idx} + {match.start(1)} chars",
                                f"{start_idx} + {match.end(1)} chars")

        for match in re.finditer(r'^\s*(\.[a-zA-Z]+|return-void|return-wide|return-object|return)\b', content,
                                 re.MULTILINE):
            text_widget.tag_add("keyword", f"{start_idx} + {match.start(1)} chars",
                                f"{start_idx} + {match.end(1)} chars")

        for match in re.finditer(r'^\s*([a-z-]+(?:-[a-z]+)*)\b', content, re.MULTILINE):
            if match.group(1) not in ["return-void", "return-wide", "return-object", "return"]:
                text_widget.tag_add("instruction", f"{start_idx} + {match.start(1)} chars",
                                    f"{start_idx} + {match.end(1)} chars")

        for match in re.finditer(r'\b([vp]\d+)\b', content):
            text_widget.tag_add("register", f"{start_idx} + {match.start(1)} chars",
                                f"{start_idx} + {match.end(1)} chars")

        for match in re.finditer(r'(L[a-zA-Z0-9_/$]+;)', content):
            text_widget.tag_add("class", f"{start_idx} + {match.start(1)} chars", f"{start_idx} + {match.end(1)} chars")