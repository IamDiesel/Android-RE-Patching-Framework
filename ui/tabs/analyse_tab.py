"""AnalyseTab — Top-Level-Container des Analysezweigs „🔬 Analyse" (B4).

Bündelt die Analyse-Werkzeuge in einem inneren Notebook (analog `patch_book` im
Workspace für den Patch-Zweig): „🌐 API Inspector" (DAST/MITM) + „🧬 Native Studio"
(geräte-/Frida-freie native Analyse). Weitere Analyse-Tabs docken hier an.

Kompatibilität: legt `self.api_tab` / `self.native_studio_tab` offen, damit
bestehende `app.api_tab`-Referenzen unverändert funktionieren.
"""
import tkinter as tk
from tkinter import ttk

from ui.tabs.api_inspector_tab import APIInspectorTab
from ui.tabs.native_studio_tab import NativeStudioTab


class AnalyseTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True)

        self.api_tab = APIInspectorTab(self._nb, app.cfg)
        self.native_studio_tab = NativeStudioTab(self._nb, app)

        self._nb.add(self.api_tab, text="🌐 API Inspector")
        self._nb.add(self.native_studio_tab, text="🧬 Native Studio")
