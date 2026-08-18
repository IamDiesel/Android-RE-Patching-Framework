import tkinter as tk
from tkinter import ttk


class UIUtils:
    @staticmethod
    def setup_global_shortcuts(root):
        """Registriert STRG+A und STRG+C global für Textfelder und Treeviews."""
        root.bind_all("<Control-a>", UIUtils._select_all)
        root.bind_all("<Control-c>", UIUtils._copy_selection)

    @staticmethod
    def _select_all(event):
        """Wählt alles in Text-Widgets, Entrys oder Treeviews aus."""
        widget = event.widget
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end")
            return "break"
        elif isinstance(widget, ttk.Entry) or isinstance(widget, tk.Entry):
            widget.select_range(0, tk.END)
            return "break"
        elif isinstance(widget, ttk.Treeview):
            widget.selection_set(widget.get_children())
            return "break"
        return None

    @staticmethod
    def _copy_selection(event):
        """Kopiert markierten Text oder selektierte Treeview-Reihen (Tab-getrennt)."""
        widget = event.widget

        # Für Treeviews bauen wir eine Tabulator-getrennte Tabelle
        if isinstance(widget, ttk.Treeview):
            selected = widget.selection()
            if not selected:
                return "break"

            lines = []
            for item in selected:
                # Hole den "text" (oft der Tree-Node Name) und die values
                text = widget.item(item, "text")
                values = widget.item(item, "values")

                parts = []
                if text: parts.append(str(text))
                if values: parts.extend([str(v) for v in values])

                lines.append("\t".join(parts))

            if lines:
                widget.clipboard_clear()
                widget.clipboard_append("\n".join(lines))
            return "break"

        # Für Standard-Textfelder lassen wir Tkinter den nativen Copy-Befehl machen
        return None

    @staticmethod
    def apply_panedwindow_style():
        """Sorgt für sichtbare Resizing-Linien zwischen den Fenstern."""
        style = ttk.Style()
        # Mache den Bereich um den Sash (die Trennlinie) dicker und farblich sichtbar
        style.configure("TPanedwindow", background="#e0e0e0")
        style.configure("Sash", background="#a0a0a0", thickness=4, sashrelief="raised")