import tkinter as tk

DEFAULT_TOOLTIP_DELAY_MS = 500


class Tooltip:
    """Einheitlicher Hover-Tooltip (Standard 500 ms) fuer beliebige tk/ttk-Widgets."""

    def __init__(self, widget, text: str, delay: int = DEFAULT_TOOLTIP_DELAY_MS):
        self.widget = widget
        self.text = text
        self.delay = max(0, int(delay))
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<FocusOut>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        if not self.text:
            return
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except Exception:
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._tip or not self.text:
            return
        try:
            if not self.widget.winfo_exists():
                return
            x = self.widget.winfo_rootx() + 10
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            sw = self.widget.winfo_screenwidth()
            sh = self.widget.winfo_screenheight()
        except Exception:
            return
        try:
            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            try:
                tw.wm_attributes("-topmost", True)
            except Exception:
                pass
            lbl = tk.Label(
                tw,
                text=self.text,
                justify="left",
                bg="#1e293b",
                fg="#f8fafc",
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 8),
                padx=8,
                pady=5,
                wraplength=360,
            )
            lbl.pack()
            tw.update_idletasks()
            tip_w = max(tw.winfo_reqwidth(), 60)
            tip_h = max(tw.winfo_reqheight(), 24)
            if x + tip_w > sw - 8:
                x = max(8, sw - tip_w - 8)
            if y + tip_h > sh - 8:
                y = max(8, self.widget.winfo_rooty() - tip_h - 6)
            tw.wm_geometry(f"+{x}+{y}")
            self._tip = tw
        except Exception:
            self._tip = None

    def _hide(self, _event=None):
        self._cancel()
        tw = self._tip
        self._tip = None
        if tw is not None:
            try:
                tw.destroy()
            except Exception:
                pass

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


def add_tooltip(widget, text: str, delay: int = DEFAULT_TOOLTIP_DELAY_MS) -> Tooltip:
    return Tooltip(widget, text, delay=delay)
