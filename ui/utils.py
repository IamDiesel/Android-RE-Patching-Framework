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
        """Kopiert markierten Text oder selektierte Treeview-Reihen (Tab-getrennt) inkl. Hierarchie."""
        widget = event.widget

        # Für Treeviews bauen wir eine Tabulator-getrennte Tabelle mit Einrückungen
        if isinstance(widget, ttk.Treeview):
            selected = widget.selection()
            if not selected:
                return "break"

            lines = []
            for item in selected:
                # 1. Hierarchie-Tiefe (Ebene) des aktuellen Elements ermitteln
                depth = 0
                parent_item = widget.parent(item)
                while parent_item:
                    depth += 1
                    parent_item = widget.parent(parent_item)

                # 2. Visuelle Einrückung erzeugen (4 Leerzeichen pro Ebene)
                indent = "    " * depth

                # 3. Text und Werte auslesen
                text = widget.item(item, "text")
                values = widget.item(item, "values")

                parts = []
                if text:
                    # Einrückung auf die erste Hauptspalte (den Text) anwenden
                    parts.append(indent + str(text))

                if values:
                    # Falls der Tree keinen 'text' nutzt, rücken wir stattdessen den ersten Value ein
                    if not text and len(values) > 0:
                        parts.append(indent + str(values[0]))
                        parts.extend([str(v) for v in values[1:]])
                    else:
                        parts.extend([str(v) for v in values])

                # Die Spalten mit Tabulator trennen, damit es sich sauber in Excel/Notepad einfügt
                lines.append("\t".join(parts))

            if lines:
                widget.clipboard_clear()
                widget.clipboard_append("\n".join(lines))
            return "break"

        # Für Standard-Textfelder lassen wir Tkinter den nativen Copy-Befehl ausführen
        return None

    @staticmethod
    def apply_panedwindow_style():
        """Sorgt für sichtbare Resizing-Linien zwischen den Fenstern."""
        style = ttk.Style()
        # Mache den Bereich um den Sash (die Trennlinie) dicker und farblich sichtbar
        style.configure("TPanedwindow", background="#e0e0e0")
        style.configure("Sash", background="#a0a0a0", thickness=4, sashrelief="raised")