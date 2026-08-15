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
        f_orig = ttk.LabelFrame(paned, text="Original Code (Read-Only)")
        paned.add(f_orig, weight=1)
        self.txt_orig = tk.Text(f_orig, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white")
        self.txt_orig.pack(fill="both", expand=True, padx=2, pady=2)
        self.txt_orig.bind("<Key>", lambda e: "break")  # Mach es Read-Only, aber kopierbar

        # Edit Button
        f_mid = ttk.Frame(paned)
        paned.add(f_mid, weight=0)
        ttk.Button(f_mid, text="⬇ Code zum Editieren kopieren ⬇", command=self.copy_to_edit).pack(pady=4)

        # Editierter Code
        f_edit = ttk.LabelFrame(paned, text="Editierter Code (Dein Patch)")
        paned.add(f_edit, weight=1)
        self.txt_edit = tk.Text(f_edit, wrap="none", font=("Consolas", 10), bg="#1E1E1E", fg="#D4D4D4",
                                insertbackground="white")
        self.txt_edit.pack(fill="both", expand=True, padx=2, pady=2)

        # Highlighting bei Eingabe im Edit-Feld aktualisieren
        self.txt_edit.bind("<KeyRelease>", lambda e: self.apply_highlighting(self.txt_edit))

    def setup_tags(self):
        """Definiert die Farbpalette (ähnlich VS Code Dark Theme)"""
        for txt in [self.txt_orig, self.txt_edit]:
            txt.tag_configure("keyword", foreground="#569CD6", font=("Consolas", 10, "bold"))  # Blau
            txt.tag_configure("instruction", foreground="#C586C0")  # Lila
            txt.tag_configure("register", foreground="#9CDCFE")  # Hellblau
            txt.tag_configure("string", foreground="#CE9178")  # Orange/Rot
            txt.tag_configure("comment", foreground="#6A9955", font=("Consolas", 10, "italic"))  # Grün
            txt.tag_configure("class", foreground="#4EC9B0")  # Türkis

    def copy_to_edit(self):
        content = self.txt_orig.get("1.0", tk.END).strip()
        self.txt_edit.delete("1.0", tk.END)
        self.txt_edit.insert("1.0", content)
        self.apply_highlighting(self.txt_edit)

    def load_code(self, code_block):
        """Lädt Code in das Original-Feld und wendet Farben an."""
        self.txt_orig.config(state="normal")
        self.txt_orig.delete("1.0", tk.END)
        self.txt_orig.insert("1.0", code_block)
        self.apply_highlighting(self.txt_orig)
        self.txt_orig.config(state="disabled")

    def get_orig_text(self):
        return self.txt_orig.get("1.0", tk.END).strip()

    def get_edit_text(self):
        return self.txt_edit.get("1.0", tk.END).strip()

    def clear_edit(self):
        self.txt_edit.delete("1.0", tk.END)

    def apply_highlighting(self, text_widget):
        """Nutzt Regex, um Smali-Syntax zu erkennen und einzufärben."""
        content = text_widget.get("1.0", "end")

        # Alle alten Tags entfernen
        for tag in ["keyword", "instruction", "register", "string", "comment", "class"]:
            text_widget.tag_remove(tag, "1.0", "end")

        # 1. Strings
        for match in re.finditer(r'(".*?")', content):
            text_widget.tag_add("string", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        # 2. Kommentare
        for match in re.finditer(r'(#.*)$', content, re.MULTILINE):
            text_widget.tag_add("comment", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        # 3. Keywords (z.B. .method, .end method, return, return-void)
        for match in re.finditer(r'^\s*(\.[a-zA-Z]+|return-void|return-wide|return-object|return)\b', content,
                                 re.MULTILINE):
            text_widget.tag_add("keyword", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        # 4. Instructions (invoke-*, if-*, move-*, sget, iput, etc.)
        for match in re.finditer(r'^\s*([a-z-]+(?:-[a-z]+)*)\b', content, re.MULTILINE):
            # Filtere keywords raus
            if match.group(1) not in ["return-void", "return-wide", "return-object", "return"]:
                text_widget.tag_add("instruction", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        # 5. Register (v0, p1, etc.)
        for match in re.finditer(r'\b([vp]\d+)\b', content):
            text_widget.tag_add("register", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")

        # 6. Klassen-Referenzen (Ljava/lang/String;)
        for match in re.finditer(r'(L[a-zA-Z0-9_/$]+;)', content):
            text_widget.tag_add("class", f"1.0 + {match.start(1)} chars", f"1.0 + {match.end(1)} chars")