import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json

from core.column_config_manager import ColumnConfigManager
from core.column_display_manager import ColumnDisplayManager

class ColumnDisplayDialog(tk.Toplevel):
    def __init__(self, parent, display_mgr: ColumnDisplayManager, all_cols: list, on_update):
        super().__init__(parent)
        self.title("👁️ Ansicht & Spaltenreihenfolge")
        self.geometry("550x400")
        self.display_mgr = display_mgr
        self.on_update = on_update

        self.active_cols, self.hidden_cols = self.display_mgr.get_lists(all_cols)
        self.create_widgets()

    def create_widgets(self):
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=10)
        ttk.Button(bottom, text="💾 Speichern", command=self.save_and_close).pack(side="right", padx=10)
        ttk.Button(bottom, text="❌ Abbrechen", command=self.destroy).pack(side="right")

        main_frame = ttk.Frame(self)
        main_frame.pack(side="top", fill="both", expand=True)

        frame_hidden = ttk.LabelFrame(main_frame, text="Ausgeblendete Spalten")
        frame_hidden.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        frame_mid = ttk.Frame(main_frame)
        frame_mid.pack(side="left", fill="y", padx=5, pady=50)

        frame_active = ttk.LabelFrame(main_frame, text="Aktive Spalten (Reihenfolge)")
        frame_active.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        frame_right = ttk.Frame(main_frame)
        frame_right.pack(side="left", fill="y", padx=5, pady=50)

        self.lst_hidden = tk.Listbox(frame_hidden, selectmode=tk.EXTENDED)
        self.lst_hidden.pack(fill="both", expand=True, padx=5, pady=5)
        for c in self.hidden_cols: self.lst_hidden.insert(tk.END, c)

        self.lst_active = tk.Listbox(frame_active, selectmode=tk.EXTENDED)
        self.lst_active.pack(fill="both", expand=True, padx=5, pady=5)
        for c in self.active_cols: self.lst_active.insert(tk.END, c)

        ttk.Button(frame_mid, text="Hinzufügen ➔", command=self.add_col).pack(pady=5)
        ttk.Button(frame_mid, text="⬅ Entfernen", command=self.remove_col).pack(pady=5)
        ttk.Button(frame_right, text="⬆ Hoch", command=self.move_up).pack(pady=5)
        ttk.Button(frame_right, text="⬇ Runter", command=self.move_down).pack(pady=5)

    def move_up(self):
        sel = self.lst_active.curselection()
        for i in sel:
            if i > 0:
                val = self.lst_active.get(i)
                self.lst_active.delete(i)
                self.lst_active.insert(i - 1, val)
                self.lst_active.selection_set(i - 1)

    def move_down(self):
        sel = self.lst_active.curselection()
        for i in reversed(sel):
            if i < self.lst_active.size() - 1:
                val = self.lst_active.get(i)
                self.lst_active.delete(i)
                self.lst_active.insert(i + 1, val)
                self.lst_active.selection_set(i + 1)

    def add_col(self):
        sel = self.lst_hidden.curselection()
        for i in reversed(sel):
            val = self.lst_hidden.get(i)
            self.lst_hidden.delete(i)
            self.lst_active.insert(tk.END, val)

    def remove_col(self):
        sel = self.lst_active.curselection()
        for i in reversed(sel):
            val = self.lst_active.get(i)
            self.lst_active.delete(i)
            self.lst_hidden.insert(tk.END, val)

    def save_and_close(self):
        active = list(self.lst_active.get(0, tk.END))
        hidden = list(self.lst_hidden.get(0, tk.END))
        self.display_mgr.save(active, hidden)
        self.on_update()
        self.destroy()

class CustomColumnDialog(tk.Toplevel):
    def __init__(self, parent, col_mgr: ColumnConfigManager, on_update_callback):
        super().__init__(parent)
        self.title("⚙️ Spalten-Logik Verwalten")
        self.geometry("700x550")
        self.col_mgr = col_mgr
        self.on_update = on_update_callback
        self.edit_index = None
        self.create_widgets()

    def create_widgets(self):
        self.add_frame = ttk.LabelFrame(self, text="Spalte hinzufügen / bearbeiten")
        self.add_frame.pack(side="bottom", fill="x", padx=10, pady=10)

        list_frame = ttk.LabelFrame(self, text="Aktive Custom-Spalten")
        list_frame.pack(side="top", fill="both", expand=True, padx=10, pady=5)

        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        ttk.Button(btn_frame, text="✏️ Bearbeiten", command=self.load_for_edit).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="🗑 Löschen", command=self.delete_col).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="📂 Laden...", command=self.import_settings).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="💾 Speichern unter...", command=self.export_settings).pack(side="right", padx=5)

        self.tree = ttk.Treeview(list_frame, columns=("Name", "Ext Type", "Quelle"), show="headings", height=5)
        self.tree.heading("Name", text="Spaltenname")
        self.tree.heading("Ext Type", text="Typ")
        self.tree.heading("Quelle", text="Datenquelle")
        self.tree.pack(side="top", fill="both", expand=True, padx=5, pady=5)

        ttk.Label(self.add_frame, text="Name:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.ent_name = ttk.Entry(self.add_frame, width=20)
        self.ent_name.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Filter Method:").grid(row=0, column=2, sticky="w", padx=5, pady=2)
        self.cb_meth = ttk.Combobox(self.add_frame, values=["ALL", "GET", "POST", "PUT", "DELETE"], state="readonly", width=10)
        self.cb_meth.current(0)
        self.cb_meth.grid(row=0, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Filter URL:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.ent_url = ttk.Entry(self.add_frame, width=20)
        self.ent_url.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Quelle:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        self.cb_src = ttk.Combobox(self.add_frame, values=["req_headers", "req_body", "res_headers", "res_body"], state="readonly", width=15)
        self.cb_src.current(3)
        self.cb_src.grid(row=1, column=3, sticky="w", padx=5, pady=2)

        ttk.Label(self.add_frame, text="Extraktor:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.cb_ext = ttk.Combobox(self.add_frame, values=["json", "regex", "offset"], state="readonly", width=15)
        self.cb_ext.current(0)
        self.cb_ext.grid(row=2, column=1, sticky="w", padx=5, pady=2)
        self.cb_ext.bind("<<ComboboxSelected>>", self.on_ext_change)

        self.param_frame = ttk.Frame(self.add_frame)
        self.param_frame.grid(row=3, column=0, columnspan=4, sticky="w", padx=5, pady=5)
        self.param_entries = {}
        self.on_ext_change()

        self.btn_submit = ttk.Button(self.add_frame, text="➕ Hinzufügen", command=self.submit_col)
        self.btn_submit.grid(row=4, column=2, pady=10, sticky="e")
        self.btn_cancel = ttk.Button(self.add_frame, text="❌ Abbrechen", command=self.cancel_edit)
        self.btn_cancel.grid(row=4, column=3, pady=10, padx=5, sticky="w")
        self.btn_cancel.grid_remove()

        self.refresh_list()

    def on_ext_change(self, event=None):
        for widget in self.param_frame.winfo_children(): widget.destroy()
        self.param_entries.clear()
        ext = self.cb_ext.get()
        if ext == "json":
            ttk.Label(self.param_frame, text="JSON Pfad:").pack(side="left")
            e = ttk.Entry(self.param_frame, width=30)
            e.pack(side="left", padx=5)
            self.param_entries["param1"] = e
        elif ext == "regex":
            ttk.Label(self.param_frame, text="Regex:").pack(side="left")
            e = ttk.Entry(self.param_frame, width=30)
            e.pack(side="left", padx=5)
            self.param_entries["param1"] = e
        elif ext == "offset":
            ttk.Label(self.param_frame, text="Offset:").pack(side="left")
            e1 = ttk.Entry(self.param_frame, width=8)
            e1.pack(side="left", padx=2)
            ttk.Label(self.param_frame, text="Länge:").pack(side="left")
            e2 = ttk.Entry(self.param_frame, width=8)
            e2.pack(side="left", padx=2)
            ttk.Label(self.param_frame, text="Typ:").pack(side="left")
            cb = ttk.Combobox(self.param_frame, values=["string", "hex", "int"], state="readonly", width=8)
            cb.current(0)
            cb.pack(side="left", padx=2)
            self.param_entries.update({"param1": e1, "param2": e2, "param3": cb})

    def load_for_edit(self):
        sel = self.tree.selection()
        if not sel: return
        self.edit_index = self.tree.index(sel[0])
        col_def = self.col_mgr.columns[self.edit_index]

        self.ent_name.delete(0, tk.END)
        self.ent_name.insert(0, col_def["name"])
        self.cb_meth.set(col_def.get("filter_method", "ALL"))
        self.ent_url.delete(0, tk.END)
        self.ent_url.insert(0, col_def.get("filter_url", ""))
        self.cb_src.set(col_def.get("source", "res_body"))
        self.cb_ext.set(col_def.get("ext_type", "json"))

        self.on_ext_change()
        if col_def.get("ext_type") in ["json", "regex"]:
            self.param_entries["param1"].delete(0, tk.END)
            self.param_entries["param1"].insert(0, col_def.get("param1", ""))
        elif col_def.get("ext_type") == "offset":
            self.param_entries["param1"].delete(0, tk.END)
            self.param_entries["param1"].insert(0, col_def.get("param1", ""))
            self.param_entries["param2"].delete(0, tk.END)
            self.param_entries["param2"].insert(0, col_def.get("param2", ""))
            self.param_entries["param3"].set(col_def.get("param3", "string"))

        self.add_frame.config(text=f"Spalte bearbeiten: {col_def['name']}")
        self.btn_submit.config(text="💾 Aktualisieren")
        self.btn_cancel.grid()

    def cancel_edit(self):
        self.edit_index = None
        self.add_frame.config(text="Neue Spalte hinzufügen")
        self.btn_submit.config(text="➕ Hinzufügen")
        self.btn_cancel.grid_remove()
        self.ent_name.delete(0, tk.END)
        self.ent_url.delete(0, tk.END)
        self.cb_meth.current(0)
        self.cb_src.current(3)
        self.cb_ext.current(0)
        self.on_ext_change()

    def submit_col(self):
        name = self.ent_name.get().strip()
        if not name: return
        col_def = {
            "name": name, "filter_method": self.cb_meth.get(), "filter_url": self.ent_url.get().strip(),
            "source": self.cb_src.get(), "ext_type": self.cb_ext.get(),
            "param1": self.param_entries.get("param1").get() if "param1" in self.param_entries else "",
            "param2": self.param_entries.get("param2").get() if "param2" in self.param_entries else "",
            "param3": self.param_entries.get("param3").get() if "param3" in self.param_entries else ""
        }
        if self.edit_index is not None:
            self.col_mgr.update_column(self.edit_index, col_def)
        else:
            self.col_mgr.add_column(col_def)
        self.cancel_edit()
        self.refresh_list()
        self.on_update()

    def delete_col(self):
        sel = self.tree.selection()
        if not sel: return
        idx = self.tree.index(sel[0])
        self.col_mgr.delete_column(idx)
        if self.edit_index == idx: self.cancel_edit()
        self.refresh_list()
        self.on_update()

    def refresh_list(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        for c in self.col_mgr.columns: self.tree.insert("", "end", values=(c["name"], c["ext_type"], c["source"]))

    def export_settings(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], title="Profil speichern")
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.col_mgr.columns, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Export", "Spalten-Profil erfolgreich exportiert!")
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte nicht speichern:\n{e}")

    def import_settings(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Profil laden")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.col_mgr.columns = data
                self.col_mgr.save()
                self.refresh_list()
                self.on_update()
                messagebox.showinfo("Import", "Spalten-Profil erfolgreich geladen!")
            else:
                messagebox.showerror("Fehler", "Ungültiges Dateiformat.")
        except Exception as e:
            messagebox.showerror("Fehler", f"Konnte nicht laden:\n{e}")