import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import uuid


class FridaManagerDialog(tk.Toplevel):
    def __init__(self, parent, frida_manager, on_update_callback=None):
        super().__init__(parent)
        self.title("🦊 Frida Script Manager (v17+ Node.js)")
        self.geometry("1000x600")
        self.transient(parent.winfo_toplevel())
        self.attributes("-topmost", True)

        self.manager = frida_manager
        self.on_update_callback = on_update_callback
        self.current_idx = None

        self.create_widgets()
        self.populate_list()

    def create_widgets(self):
        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill="both", expand=True, padx=10, pady=10)

        # LINKE SEITE: Liste der Skripte
        f_left = ttk.Frame(main_paned)
        main_paned.add(f_left, weight=1)

        toolbar = ttk.Frame(f_left)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Button(toolbar, text="➕ Neu", command=self.add_script).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🗑 Löschen", command=self.delete_script).pack(side="left", padx=2)

        self.tree = ttk.Treeview(f_left, columns=("Status", "Name"), show="headings")
        self.tree.heading("Status", text="Aktiv")
        self.tree.heading("Name", text="Skript Name")
        self.tree.column("Status", width=50, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        ttk.Button(f_left, text="✅ Als aktives Skript setzen", command=self.set_active).pack(fill="x", pady=5)

        # RECHTE SEITE: Editor
        f_right = ttk.LabelFrame(main_paned, text="JavaScript / TypeScript Editor (frida-compile)")
        main_paned.add(f_right, weight=3)

        f_name = ttk.Frame(f_right)
        f_name.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_name, text="Name:").pack(side="left")
        self.ent_name = ttk.Entry(f_name)
        self.ent_name.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(f_name, text="💾 Speichern", command=self.save_current).pack(side="right")

        self.txt_code = tk.Text(f_right, bg="#1E1E1E", fg="#D4D4D4", font=("Consolas", 10), insertbackground="white")
        self.txt_code.pack(fill="both", expand=True, padx=5, pady=5)

    def populate_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, s in enumerate(self.manager.scripts):
            status = "✅" if s["id"] == self.manager.active_script_id else ""
            self.tree.insert("", "end", iid=str(idx), values=(status, s["name"]))

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel: return
        self.current_idx = int(sel[0])
        s = self.manager.scripts[self.current_idx]

        self.ent_name.delete(0, tk.END)
        self.ent_name.insert(0, s["name"])
        self.txt_code.delete("1.0", tk.END)
        self.txt_code.insert("1.0", s["code"])

    def add_script(self):
        new_id = str(uuid.uuid4())[:8]
        self.manager.scripts.append({
            "id": new_id,
            "name": "Neues Skript",
            "code": "import Java from \"frida-java-bridge\";\n\nconsole.log('Hello Frida!');"
        })
        self.manager.save()
        self.populate_list()
        self.tree.selection_set(str(len(self.manager.scripts) - 1))

    def save_current(self):
        if self.current_idx is None: return
        self.manager.scripts[self.current_idx]["name"] = self.ent_name.get()
        self.manager.scripts[self.current_idx]["code"] = self.txt_code.get("1.0", tk.END).strip()
        self.manager.save()
        self.populate_list()
        self.tree.selection_set(str(self.current_idx))
        if self.on_update_callback: self.on_update_callback()

    def delete_script(self):
        if self.current_idx is None: return
        if messagebox.askyesno("Löschen", "Skript löschen?", parent=self):
            script_id = self.manager.scripts[self.current_idx]["id"]
            del self.manager.scripts[self.current_idx]
            if self.manager.active_script_id == script_id:
                self.manager.active_script_id = None
            self.manager.save()
            self.txt_code.delete("1.0", tk.END)
            self.ent_name.delete(0, tk.END)
            self.current_idx = None
            self.populate_list()

    def set_active(self):
        if self.current_idx is None: return
        self.manager.active_script_id = self.manager.scripts[self.current_idx]["id"]
        self.manager.save()
        self.populate_list()
        self.tree.selection_set(str(self.current_idx))
        if self.on_update_callback: self.on_update_callback()