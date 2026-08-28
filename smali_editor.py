import tkinter as tk
from tkinter import ttk
import re


class SmaliEditorWidget(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.create_widgets()
        self.setup_tags()

    def create_widgets(self):
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill="both", expand=True)

        # Original Code (Read-Only)
        f_orig = ttk.LabelFrame(paned)
        paned.add(f_orig, weight=1)

        # NEU: Custom Header mit Button für den Call Graph
        f_orig_header = ttk.Frame(f_orig)
        ttk.Label(f_orig_header, text="Original Code (Read-Only)").pack(side="left")
        self.btn_find_cg = ttk.Button(f_orig_header, text="🔍 Find in Call Graph")
        self.btn_find_cg.pack(side="left", padx=10)
        f_orig.config(labelwidget=f_orig_header)

        self.txt_orig = tk.Text(f_orig, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white")
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
        self.txt_edit = tk.Text(f_edit, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white")
        self.txt_edit.pack(fill="both", expand=True, padx=2, pady=2)

        self.txt_edit.bind("<KeyRelease>", lambda e: self.apply_highlighting(self.txt_edit))

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
        self.apply_highlighting(self.txt_edit)

    def load_code(self, code_block):
        self.txt_orig.config(state="normal")
        self.txt_orig.delete("1.0", tk.END)
        self.txt_orig.insert("1.0", code_block)
        self.apply_highlighting(self.txt_orig)
        self.txt_orig.config(state="disabled")

        # FIX: Das Edit-Feld muss zwingend geleert werden, wenn eine neue Methode geladen wird!
        self.clear_edit()

    def get_orig_text(self):
        return self.txt_orig.get("1.0", tk.END).strip()

    def get_edit_text(self):
        return self.txt_edit.get("1.0", tk.END).strip()

    def clear_edit(self):
        self.txt_edit.delete("1.0", tk.END)

    def rehighlight(self):
        """Aktualisiert das Syntax-Highlighting für beide Textfelder (Wichtig nach automatischem Laden)."""
        self.apply_highlighting(self.txt_orig)
        self.apply_highlighting(self.txt_edit)

    def apply_highlighting(self, text_widget):
        content = text_widget.get("1.0", "end")

        for tag in ["keyword", "instruction", "register", "string", "comment", "class"]:
            text_widget.tag_remove(tag, "1.0", "end")

        for match in re.finditer(r'(".*?")', content):
            text_widget.tag_add("string", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        for match in re.finditer(r'(#.*)$', content, re.MULTILINE):
            text_widget.tag_add("comment", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        for match in re.finditer(r'^\s*(\.[a-zA-Z]+|return-void|return-wide|return-object|return)\b', content,
                                 re.MULTILINE):
            text_widget.tag_add("keyword", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        for match in re.finditer(r'^\s*([a-z-]+(?:-[a-z]+)*)\b', content, re.MULTILINE):
            if match.group(1) not in ["return-void", "return-wide", "return-object", "return"]:
                text_widget.tag_add("instruction", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        for match in re.finditer(r'\b([vp]\d+)\b', content):
            text_widget.tag_add("register", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        for match in re.finditer(r'(L[a-zA-Z0-9_/$]+;)', content):
            text_widget.tag_add("class", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")