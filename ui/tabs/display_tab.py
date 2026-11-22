# -*- coding: utf-8 -*-
"""DisplayTab — Anzeige-/Simulator-Fenster (Vision-Saeule 4: Anzeige/UI).

Rendert den vom Emulator aufgezeichneten ViewTree (`core.java.view_tree`) in einem Hochformat-
Telefonrahmen (Statusleiste, Bildschirm, Android-Navigationsleiste Zurueck/Home/Kontext, seitliche
Lautstaerke/Power-Tasten) auf einem tkinter-Canvas und laesst ihn per Maus/Tastatur bedienen.
Rendering = reine `paint_commands`, Bedienung ueber hit_test/clickable_at. Zielagnostisch.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk

from core.java.view_tree import demo_screen, widgets_demo, paint_commands, info_screen, swipe_pager
from core.java.app_runtime import (
    detect_runtime,
    is_unrendered_surface_shell,
    inspect_engine_details,
    mount_inphone_runtime_if_needed,
)

# Telefon-Geometrie (logische Pixel, Hochformat ~ 19.5:9)
BEZEL = 16
STATUS_H = 24
NAV_H = 56
SCREEN_W = 380
SCREEN_H = 800


class DisplayTab(ttk.Frame):
    def __init__(self, parent, app, embedded=False):
        super().__init__(parent)
        self.app = app
        self.embedded = bool(embedded)
        self.tree = demo_screen()
        self._dl = None          # DisplayListSurface (immediate-mode) oder None
        self.vm = None                 # VM hinter dem Baum (fuer Listener-Dispatch)
        self._focus = None             # fokussierter EditText-Knoten
        self._focus_anchor = None      # stabiler Fokus-Anker ueber Rekompositionen hinweg
        self._dl_focus_xy = None       # fokussierter Punkt (x,y) im DisplayList-Screen (RN-Hermes)
        self._dl_text = ""             # akkumulierter Text des fokussierten TextInput
        self._hermes_client = None     # AP1.4: lebender Hermes-Client (beide Surface-Typen)
        self._activity = None          # aktuelle Activity (fuer Zurueck)
        self._nav = None               # NavController (Back-Stack)
        self._busy = False
        self.scroll_y = 0
        self._max_scroll = 0
        self._glass_mode = None
        self.zoom_mode = "auto"        # "auto" (Auto-Fit) oder float (0.5 .. 1.25)
        self._eff_s = 1.0
        self._resize_after_id = None
        self._nav_regions = []         # (name, x0, y0, x1, y1) Hardware/Nav-Flaechen (logisch)
        self._photo_cache = {}
        self._active_photos = []
        self._paint_cache = None
        self._event_subs = []
        self._build()
        try:
            from core.application.event_bus import EventBus
            self._event_subs = [
                ("GLASS_STARTED", self._on_glass_event),
                ("GLASS_STATE", self._on_glass_event),
                ("GLASS_SURFACE", self._on_surface_event),
                ("DEBUG_PAUSED", self._on_debug_paused),
                ("WEBVIEW_EVENT", self._on_webview_event),
                ("APP_IMPORTED", self._on_app_imported),
            ]
            for _ev, _cb in self._event_subs:
                EventBus.subscribe(_ev, _cb)
            self.bind("<Destroy>", self._on_destroy, add="+")
        except Exception:
            pass
        self.after(60, self.render)

    def _on_app_imported(self, data=None):
        try:
            self.after(0, lambda d=data: self.on_app_changed(d))
        except Exception:
            pass

    def _on_destroy(self, event=None):
        if event is not None and getattr(event, "widget", self) is not self:
            return
        subs = getattr(self, "_event_subs", None)
        if not subs:
            return
        self._event_subs = []
        try:
            from core.application.event_bus import EventBus
            for ev, cb in subs:
                EventBus.unsubscribe(ev, cb)
        except Exception:
            pass

    def on_app_changed(self, _data=None):
        """Setzt die Anzeige/Surface beim App-Wechsel auf neutralen Zustand zurueck."""
        if not self._alive():
            return
        self.tree = None
        self._dl = None
        self.vm = None
        self._hermes_client = None
        self._activity = None
        self._nav = None
        self._focus = None
        self._focus_anchor = None
        self._dl_focus_xy = None
        self._dl_text = ""
        self._glass_mode = None
        self._paint_cache = None
        self.scroll_y = 0
        if hasattr(self, "_entry_var") and self._entry_var is not None:
            try:
                self._entry_var.set("")
            except Exception:
                pass
        try:
            self.after_cancel(getattr(self, "_resize_after_id", None) or "")
        except Exception:
            pass
        if getattr(self, "_browser_panel", None) is not None:
            try:
                self._browser_panel.refresh_discovered_urls()
            except Exception:
                pass
        if hasattr(self, "_update_web_badge"):
            self._update_web_badge()
        self.render()

    def _on_webview_event(self, ev=None):
        def _apply():
            if not self._alive():
                return
            self._update_web_badge()
            if getattr(self, "_browser_panel", None) is not None and isinstance(ev, dict):
                try:
                    self._browser_panel.add_web_event(ev)
                except Exception:
                    pass
        try:
            self.after(0, _apply)
        except Exception:
            pass

    def _inphone_ctrl(self, tree=None):
        t = tree if tree is not None else self.tree
        if t is None:
            return None
        return (
            getattr(t, "_flutter_ctrl", None)
            or getattr(t, "_rn_ctrl", None)
            or getattr(t, "_hybrid_ctrl", None)
        )

    def _update_web_badge(self):
        btn = getattr(self, "_web_btn", None)
        if btn is None:
            return
        try:
            cnt = len(getattr(self.tree, "web_events", ()) or ())
            fctrl = self._inphone_ctrl()
            in_browser = bool(getattr(self, "_browser_mode", False) or (fctrl is not None and getattr(fctrl, "screen", "") == "browser"))
            if in_browser:
                btn.config(text="📱 App")
            else:
                btn.config(text=f"🌐 Browser ({cnt})" if cnt else "🌐 Browser")
        except Exception:
            pass

    def _active_stubs_and_root(self):
        ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        stubs = getattr(ctrl, "_stubs", None) if ctrl is not None else None
        root = ""
        if ctrl is not None and hasattr(ctrl, "_resolve_root"):
            try:
                root = ctrl._resolve_root() or ""
            except Exception:
                root = ""
        if not root:
            studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
            if studio is not None and hasattr(studio, "get_smali_dir"):
                try:
                    root = studio.get_smali_dir() or ""
                except Exception:
                    root = ""
        if not root:
            try:
                cfg = getattr(self.app, "cfg", None)
                src = cfg.paths.get("SOURCE_APK", "") if cfg is not None else ""
                if src:
                    root = os.path.join(src, cfg.config.get("UNPACK_DIR", "base_unpacked_apktool"))
            except Exception:
                root = ""
        return stubs, root

    def _on_inphone_browser_async(self):
        def _upd():
            if not self._alive():
                return
            self._update_web_badge()
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if ctrl is not None:
                if getattr(self, "_browser_mode", False):
                    if hasattr(ctrl, "_refresh"):
                        try:
                            ins = ctrl._jdb.inspect() if getattr(ctrl, "_jdb", None) else {"mode": "idle"}
                            ctrl._refresh(ins)
                        except Exception:
                            pass
                elif hasattr(ctrl, "_notify_glass_ui_updated"):
                    try:
                        ctrl._notify_glass_ui_updated()
                    except Exception:
                        pass
            self.render()
        try:
            self.app.after(0, _upd)
        except Exception:
            try:
                self.after(0, _upd)
            except Exception:
                pass

    def toggle_browser_view(self, url: str | None = None):
        """Schaltet direkt IN DER HANDY-SIMULATION (380x800) zwischen App und In-Phone-Browser um (keine Auslagerung!)."""
        from core.java.flutter_app_producer import InPhoneBrowserController

        fctrl = self._inphone_ctrl()
        if fctrl is not None and not getattr(self, "_browser_mode", False):
            fctrl.set_async_updater(self._on_inphone_browser_async)
            fctrl.toggle_browser(url=url)
            self._focus = None
            self._focus_anchor = None
            self.scroll_y = 0
            self._update_web_badge()
            self.render()
            return

        if getattr(self, "_browser_mode", False) and url is None:
            saved = getattr(self, "_saved_screen_state", None) or {}
            self._browser_mode = False
            self._dl = saved.get("dl")
            self.tree = saved.get("tree")
            self.vm = saved.get("vm")
            self._activity = saved.get("activity")
            self._focus = None
            self._focus_anchor = None
            self.scroll_y = 0
            self._update_web_badge()
            self.render()
            return

        if not getattr(self, "_browser_mode", False):
            self._saved_screen_state = {
                "tree": self.tree,
                "vm": self.vm,
                "dl": self._dl,
                "activity": self._activity,
            }

        stubs, app_root = self._active_stubs_and_root()
        bctrl = getattr(self, "_standalone_browser_ctrl", None)
        if bctrl is None or getattr(bctrl, "app_root", "") != app_root or getattr(bctrl, "stubs", None) is not stubs:
            bctrl = InPhoneBrowserController(
                app_root=app_root,
                stubs=stubs,
                on_exit=lambda: self.toggle_browser_view(),
                on_async_update=self._on_inphone_browser_async,
            )
            self._standalone_browser_ctrl = bctrl
        else:
            bctrl.on_async_update = self._on_inphone_browser_async

        self._dl = None
        self.vm = None
        self._activity = None
        self._focus = None
        self._focus_anchor = None
        self.scroll_y = 0
        self.tree = bctrl.tree
        self._browser_mode = True

        target = url
        if not target:
            bctrl._ensure_quick_urls()
            if bctrl.current_url:
                target = bctrl.current_url
            elif bctrl.quick_urls:
                target = bctrl.quick_urls[0]["url"]
        if target and (target != bctrl.current_url or bctrl.last_result is None):
            bctrl.load_url(target)
        else:
            bctrl.mount()

        self._update_web_badge()
        self.render()

    def open_browser_inspector(self, url: str | None = None):
        """Öffnet OPTIONAL die erweiterte Chromium-/DOM-Analyse im eigenen Fenster — Handy-Anzeige bleibt unverändert aktiv."""
        from ui.widgets.browser_inspector_panel import BrowserInspectorPanel

        cur_url = url
        if not cur_url and self.tree is not None:
            bctrl = getattr(self.tree, "_in_phone_browser", None) or getattr(self, "_standalone_browser_ctrl", None)
            if bctrl is not None and getattr(bctrl, "current_url", ""):
                cur_url = bctrl.current_url

        win = getattr(self, "_browser_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    if cur_url and self._browser_panel is not None:
                        self._browser_panel.load_current_url(cur_url)
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.winfo_toplevel())
        win.title("🔬 Browser-Analyse & Chromium Inspect — Android RE Patching Framework")
        win.geometry("1220x760")
        pnl = BrowserInspectorPanel(win, self.app)
        pnl.pack(fill="both", expand=True)
        self._browser_win = win
        self._browser_panel = pnl
        if cur_url:
            pnl.load_current_url(cur_url)

    def _open_engine_apis(self):
        dbg = getattr(self.app, "debugger_tab", None)
        fn = getattr(dbg, "open_flutter_api_extractor", None)
        if callable(fn):
            fn()

    def _log(self, msg):
        f = getattr(self.app, "log", None)
        (f if callable(f) else print)("[Anzeige] %s" % msg)

    def _alive(self):
        """Prueft ob Widget + Canvas noch leben (verhindert TclError nach Dock-Rebuild)."""
        try:
            c = getattr(self, "canvas", None)
            return bool(self.winfo_exists() and c is not None and c.winfo_exists())
        except Exception:
            return False

    def _calc_scale(self):
        """Berechnet den aktiven Zoomfaktor fuer das Handy-Display ('auto' passt ins Dock-Fenster)."""
        full_w = SCREEN_W + 2 * BEZEL + 20
        full_h = SCREEN_H + NAV_H + 2 * BEZEL + 16
        if self.zoom_mode == "auto":
            try:
                cw = self.canvas.winfo_width() if self._alive() else 0
                ch = self.canvas.winfo_height() if self._alive() else 0
            except Exception:
                cw = ch = 0
            if cw <= 16 or ch <= 16:
                s = 0.68 if self.embedded else 0.85
            else:
                s = min((cw - 8) / float(full_w), (ch - 8) / float(full_h))
                s = max(0.35, min(1.35, s))
        else:
            try:
                s = max(0.35, min(1.5, float(self.zoom_mode)))
            except Exception:
                s = 1.0
        self._eff_s = s
        return s

    def _on_zoom_select(self, _e=None):
        val = (self.zoom_var.get() or "Auto-Fit").strip()
        if val.lower().startswith("auto"):
            self.zoom_mode = "auto"
        else:
            try:
                self.zoom_mode = max(0.35, min(1.5, float(val.rstrip("%")) / 100.0))
            except Exception:
                self.zoom_mode = "auto"
        if self._dl is not None:
            self._render_dl()
        else:
            self.render()

    def _on_canvas_configure(self, _e=None):
        if self.zoom_mode != "auto" or not self._alive():
            return
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(
            45, lambda: (self._render_dl() if self._dl is not None else self.render())
        )

    def _on_zoom_wheel(self, e):
        steps = [0.4, 0.5, 0.65, 0.75, 0.9, 1.0, 1.15, 1.25]
        cur = getattr(self, "_eff_s", 1.0)
        up = (getattr(e, "num", None) == 4) or (getattr(e, "delta", 0) > 0)
        if up:
            nxt = next((v for v in steps if v > cur + 0.03), steps[-1])
        else:
            nxt = next((v for v in reversed(steps) if v < cur - 0.03), steps[0])
        self.zoom_mode = nxt
        self.zoom_var.set("%d%%" % int(round(nxt * 100)))
        if self._dl is not None:
            self._render_dl()
        else:
            self.render()

    # ---- Aufbau -----------------------------------------------------------
    def _build(self):
        try:
            from ui.widgets.tooltip import add_tooltip
        except Exception:
            add_tooltip = lambda *_a, **_kw: None

        bar = ttk.Frame(self); bar.pack(side="top", fill="x")
        demos_mb = ttk.Menubutton(bar, text="Demos \u25be")
        demos_menu = tk.Menu(demos_mb, tearoff=0)
        for lbl, cb in (
            ("Demo-Screen", self._load_demo),
            ("Widgets", self._load_widgets),
            ("Beispiel-App", self._load_example),
            ("Beispiel (XML)", self._load_xml_example),
            ("Login-Demo", self._load_login),
            ("Compose-Demo", self._load_compose),
            ("RN-Demo", self._load_rn),
            ("RN-JS-Demo", self._load_rn_js),
            ("RN-Hermes-Demo", self._load_rn_hermes),
            ("Flutter-Demo", self._load_skia),
            ("Navigation", self._load_nav_example),
        ):
            demos_menu.add_command(label=lbl, command=cb)
        demos_mb.configure(menu=demos_menu)
        demos_mb.pack(side="left", padx=2, pady=3)
        add_tooltip(demos_mb, "Lädt eingebaute UI-Demo-Szenen (Compose, RN/Hermes, Flutter/Skia, XML) zum Testen der Anzeige.")
        b_run = ttk.Button(bar, text="Aus App-Lauf", command=self._load_from_run)
        b_run.pack(side="left", padx=2)
        add_tooltip(b_run, "Startet einen schnellen App-Lifecycle direkt in der UI-Anzeige ohne vollständiges Debugger-Stepping.")
        b_gls = ttk.Button(bar, text="Gläsern starten", command=self._load_glass)
        b_gls.pack(side="left", padx=2)
        add_tooltip(b_gls, "Startet den gläsernen App-Lauf über den Debugger (Dalvik + VFS/ARM64 + Breakpoints) und koppelt diesen Screen.")
        self.zone_var = tk.StringVar(value="original")
        self._entry_var = tk.StringVar(value="")
        if not self.embedded:
            ttk.Label(bar, text="Zone:").pack(side="left", padx=(6, 1))
            ttk.Combobox(bar, textvariable=self.zone_var, width=9, state="readonly",
                         values=("original", "patched")).pack(side="left")
            ttk.Label(bar, text="Entry:").pack(side="left", padx=(6, 1))
            ttk.Entry(bar, textvariable=self._entry_var, width=22).pack(side="left")
        ttk.Label(bar, text="Zoom:").pack(side="left", padx=(6, 1))
        self.zoom_var = tk.StringVar(value="Auto-Fit")
        zoom_cb = ttk.Combobox(
            bar, textvariable=self.zoom_var, width=8, state="readonly",
            values=("Auto-Fit", "50%", "65%", "75%", "90%", "100%", "125%"),
        )
        zoom_cb.pack(side="left", padx=(0, 4))
        zoom_cb.bind("<<ComboboxSelected>>", self._on_zoom_select)
        add_tooltip(zoom_cb, "Skaliert die virtuelle Handy-Anzeige (Auto-Fit oder feste Prozentstufen).")
        self._pw_mask = False
        self._ui_inspect_mode = False
        self._drag_start = None
        self._drag_moved = False
        if self.embedded:
            bar2 = ttk.Frame(self); bar2.pack(side="top", fill="x")
            self._web_btn = ttk.Button(bar2, text="🌐 Browser", command=self.toggle_browser_view)
            self._web_btn.pack(side="left", padx=2, pady=(0, 2))
            self._inspect_btn = ttk.Button(bar2, text="🔬 Inspect", command=self.open_browser_inspector)
            self._inspect_btn.pack(side="left", padx=2, pady=(0, 2))
            self._pw_btn = ttk.Button(bar2, text="👁 PW", command=self.toggle_password_mask)
            self._pw_btn.pack(side="left", padx=2, pady=(0, 2))
            self._ui_inspect_btn = ttk.Button(bar2, text="🎯 UI-Lupe", command=self.toggle_ui_inspect)
            self._ui_inspect_btn.pack(side="left", padx=2, pady=(0, 2))
            self.status = ttk.Label(bar2, text="", anchor="w")
            self.status.pack(side="left", padx=4, pady=(0, 2))
        else:
            self._web_btn = ttk.Button(bar, text="🌐 Browser", command=self.toggle_browser_view)
            self._web_btn.pack(side="left", padx=2)
            self._inspect_btn = ttk.Button(bar, text="🔬 Inspect", command=self.open_browser_inspector)
            self._inspect_btn.pack(side="left", padx=2)
            b_eapi = ttk.Button(bar, text="🔍 Engine-APIs", command=self._open_engine_apis)
            b_eapi.pack(side="left", padx=2)
            add_tooltip(b_eapi, "Öffnet den statischen Engine-API-Scanner für Flutter/Hybrid/RN-Endpunkte.")
            self._pw_btn = ttk.Button(bar, text="👁 PW", command=self.toggle_password_mask)
            self._pw_btn.pack(side="left", padx=2)
            self._ui_inspect_btn = ttk.Button(bar, text="🎯 UI-Lupe", command=self.toggle_ui_inspect)
            self._ui_inspect_btn.pack(side="left", padx=2)
            self.status = ttk.Label(bar, text="", anchor="w")
            self.status.pack(side="left", padx=6)
        add_tooltip(self._web_btn, "Schaltet auf den eingebauten In-Phone-Browser / WebView um.")
        add_tooltip(self._inspect_btn, "Öffnet den DOM-/HTTP-WebView-Inspektor für geladene Seiten.")
        add_tooltip(self._pw_btn, "Maskiert bzw. zeigt Passwort-Eingabefelder in der Handy-Ansicht.")
        add_tooltip(
            self._ui_inspect_btn,
            "UI-Element-Lupe (An/Aus): Hebt App-Elemente unter dem Mauszeiger hervor und zeigt Klasse, ID, Text & Bounds.\n"
            "Solange '🟢 UI-Lupe: AN' aktiv ist, wählen Klicks das Element zur Inspektion aus, statt App-Buttons auszulösen.",
        )
        wrap = ttk.Frame(self); wrap.pack(side="top", fill="both", expand=True)
        self._wrap = wrap
        self._browser_mode = False
        self._browser_panel = None
        self._browser_win = None
        self.canvas = tk.Canvas(wrap, bg="#2b2f36", highlightthickness=0, takefocus=1)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover_motion)
        self.canvas.bind("<Key>", self._on_key)
        self.canvas.bind("<Tab>", self._on_key)
        self.canvas.bind("<Shift-Tab>", self._on_key)
        self.canvas.bind("<MouseWheel>", self._on_wheel)   # Windows/Mac
        self.canvas.bind("<Button-4>", self._on_wheel)     # Linux hoch
        self.canvas.bind("<Button-5>", self._on_wheel)     # Linux runter
        self.canvas.bind("<Control-MouseWheel>", self._on_zoom_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def set_tree(self, tree, vm=None, activity=None):
        self.tree = tree
        self.vm = vm
        self._activity = activity
        self._focus = None
        self._focus_anchor = None
        self._paint_cache = None
        self.scroll_y = 0
        self.render()

    def set_surface(self, surface):
        """Nimmt eine SurfaceSource an (Entkopplung): SceneGraph -> bestehender Baum-Pfad;
        DisplayList -> immediate-mode-Renderer. API des Render-Produzenten-Bausteins (S1b)."""
        from core.java.surface import SceneGraphSurface, DisplayListSurface
        self._hermes_client = getattr(surface, "_hermes_client", None)   # AP1.4: fuer BEIDE Typen
        if isinstance(surface, SceneGraphSurface):
            self._dl = None
            self.set_tree(surface.tree, getattr(surface, "vm", None), getattr(surface, "activity", None))
        elif isinstance(surface, DisplayListSurface):
            self._dl = surface
            self.tree = None
            self._focus = None
            self._focus_anchor = None
            self._paint_cache = None
            self.scroll_y = 0
            self._render_dl()

    def _load_pil_scaled(self, cmd, target_w, target_h):
        """Laedt ein Raster-Drawable ueber Pillow und liefert (resized_rgba, rw, rh, key_id) oder None."""
        tw = max(4, int(round(target_w)))
        th = max(4, int(round(target_h)))
        path = cmd.get("path") or ""
        data = cmd.get("data")
        if data is not None:
            import hashlib
            key_id = hashlib.sha1(bytes(data)).hexdigest()[:16]
        elif path and os.path.isfile(path):
            key_id = path
        else:
            return None
        from PIL import Image
        from core.java.drawable_loader import prepare_pil_rgba
        import io
        src = Image.open(io.BytesIO(bytes(data))) if data is not None else Image.open(path)
        src = prepare_pil_rgba(
            src,
            is_9patch=bool(cmd.get("is_9patch")),
            dark_bg=bool(cmd.get("dark_bg", getattr(self, "_cur_screen_dark", False))),
            pre_tinted=bool(cmd.get("pre_tinted", False)),
            alpha=float(cmd.get("alpha", 1.0)),
        )
        if cmd.get("scale") == "stretch":
            rw, rh = tw, th
            return src.resize((rw, rh), Image.LANCZOS), rw, rh, key_id
        if cmd.get("scale") == "crop":
            sc = max(tw / max(1, src.width), th / max(1, src.height))
            cw = max(tw, int(round(src.width * sc)))
            ch = max(th, int(round(src.height * sc)))
            cropped = src.resize((cw, ch), Image.LANCZOS)
            ox_c = max(0, (cw - tw) // 2)
            oy_c = max(0, (ch - th) // 2)
            cropped = cropped.crop((ox_c, oy_c, ox_c + tw, oy_c + th))
            return cropped, tw, th, key_id
        sc = min(tw / max(1, src.width), th / max(1, src.height))
        rw = max(1, int(round(src.width * sc)))
        rh = max(1, int(round(src.height * sc)))
        return src.resize((rw, rh), Image.LANCZOS), rw, rh, key_id

    def _resolve_tk_image(self, cmd, target_w, target_h, crop_v=None):
        """Laedt und skaliert ein Raster-Drawable (PIL optional, sonst Tkinter PhotoImage fuer PNG/GIF)."""
        tw = max(4, int(round(target_w)))
        th = max(4, int(round(target_h)))
        path = cmd.get("path") or ""
        data = cmd.get("data")
        if data is not None:
            import hashlib
            key_id = hashlib.sha1(bytes(data)).hexdigest()[:16]
        elif path and os.path.isfile(path):
            key_id = path
        else:
            return None
        crop_key = (int(crop_v[0]), int(crop_v[1])) if crop_v is not None else None
        cache_key = (
            key_id, tw // 2, th // 2,
            bool(cmd.get("is_9patch")), cmd.get("scale", "fit"),
            bool(cmd.get("dark_bg", getattr(self, "_cur_screen_dark", False))),
            bool(cmd.get("pre_tinted", False)),
            round(float(cmd.get("alpha", 1.0)), 2),
            crop_key,
        )
        cached = self._photo_cache.get(cache_key)
        if cached is not None:
            return cached
        ph = None
        try:
            from PIL import ImageTk
            loaded = self._load_pil_scaled(cmd, tw, th)
            if loaded is not None:
                resized, rw, rh, _ = loaded
                if crop_v is not None:
                    c0 = max(0, min(rh - 1, int(crop_v[0])))
                    c1 = max(c0 + 1, min(rh, int(crop_v[1])))
                    if c0 > 0 or c1 < rh:
                        resized = resized.crop((0, c0, rw, c1))
                ph = ImageTk.PhotoImage(resized)
        except ImportError:
            try:
                import base64
                if data is not None:
                    ph = tk.PhotoImage(data=base64.b64encode(bytes(data)).decode("ascii"))
                elif path.lower().endswith((".png", ".gif")):
                    ph = tk.PhotoImage(file=path)
                if ph is not None:
                    pw, ph_h = max(1, ph.width()), max(1, ph.height())
                    if pw > tw or ph_h > th:
                        sub = max(1, (pw + tw - 1) // tw, (ph_h + th - 1) // th)
                        ph = ph.subsample(sub, sub)
                    elif pw * 2 <= tw and ph_h * 2 <= th:
                        zm = max(1, min(tw // pw, th // ph_h, 4))
                        if zm > 1:
                            ph = ph.zoom(zm, zm)
            except Exception:
                ph = None
        except Exception:
            ph = None
        if ph is not None:
            if len(self._photo_cache) >= 256:
                self._photo_cache.clear()
            self._photo_cache[cache_key] = ph
        return ph

    def _draw_extra_cmd(self, c, cmd, ox, oy, sy, s, vy0, vy1):
        """Zeichnet Grafik-Opcodes ('image', 'polygon', 'polyline') einheitlich in beiden Rendern."""
        op = cmd.get("op")
        cy = (oy + cmd.get("y", 0)) if cmd.get("fixed_y") else (oy + cmd.get("y", 0) - sy)
        if op == "image":
            cw, ch = cmd.get("w", 0), cmd.get("h", 0)
            if cy + ch <= vy0 or cy >= vy1 or cw <= 1 or ch <= 1:
                return True
            x0 = ox + cmd.get("x", 0)
            draw_cy = (cy + ch * 0.5) * s
            ph = None
            try:
                loaded = self._load_pil_scaled(cmd, cw * s, ch * s)
                if loaded is not None:
                    _, _rw, rh, _ = loaded
                    iy0 = draw_cy - rh * 0.5
                    iy1 = iy0 + rh
                    ky0 = max(iy0, vy0 * s)
                    ky1 = min(iy1, vy1 * s)
                    if ky1 <= ky0:
                        return True
                    c0 = max(0, int(round(ky0 - iy0)))
                    c1 = min(rh, int(round(ky1 - iy0)))
                    ph = self._resolve_tk_image(cmd, cw * s, ch * s, crop_v=(c0, c1) if (c0 > 0 or c1 < rh) else None)
                    draw_cy = iy0 + (c0 + c1) * 0.5
            except Exception:
                ph = self._resolve_tk_image(cmd, cw * s, ch * s)
            if ph is not None:
                c.create_image((x0 + cw * 0.5) * s, draw_cy, image=ph, anchor="center")
                self._active_photos.append(ph)
            else:
                rx0, ry0, rx1, ry1 = x0, max(vy0, cy), x0 + cw, min(vy1, cy + ch)
                if ry1 > ry0:
                    c.create_rectangle(rx0 * s, ry0 * s, rx1 * s, ry1 * s,
                                       fill="#eef1f6", outline="#c3c9d6")
                    lbl = cmd.get("name") or os.path.basename(cmd.get("path") or "") or "IMG"
                    c.create_text((rx0 + cw * 0.5) * s, ((ry0 + ry1) * 0.5) * s,
                                  text=lbl, fill="#6b7280", anchor="c",
                                  font=("Segoe UI", max(7, int(round(9 * s)))))
            return True
        if op == "polygon":
            raw_pts = cmd.get("points") or []
            if len(raw_pts) < 6:
                return True
            ys = [oy + raw_pts[k + 1] - sy for k in range(0, len(raw_pts) - 1, 2)]
            if max(ys) <= vy0 or min(ys) >= vy1:
                return True
            scaled = []
            for k in range(0, len(raw_pts) - 1, 2):
                scaled.append((ox + raw_pts[k]) * s)
                scaled.append((oy + raw_pts[k + 1] - sy) * s)
            c.create_polygon(*scaled, fill=cmd.get("fill") or "",
                             outline=cmd.get("outline") or "",
                             width=max(1, int(round(cmd.get("width", 1) * s))))
            return True
        if op == "polyline":
            raw_pts = cmd.get("points") or []
            if len(raw_pts) < 4:
                return True
            ys = [oy + raw_pts[k + 1] - sy for k in range(0, len(raw_pts) - 1, 2)]
            if max(ys) <= vy0 or min(ys) >= vy1:
                return True
            scaled = []
            for k in range(0, len(raw_pts) - 1, 2):
                scaled.append((ox + raw_pts[k]) * s)
                scaled.append((oy + raw_pts[k + 1] - sy) * s)
            c.create_line(*scaled, fill=cmd.get("color") or "#000",
                          width=max(1, int(round(cmd.get("width", 1) * s))))
            return True
        return False

    def _render_dl(self):
        """Immediate-mode: zeichnet die Display-Liste (Canvas-/Skia-Recorder) + Telefonrahmen.
        Ein Flush je Frame. (tk-Sichtpruefung auf Windows; headless py_compile.)"""
        if not self._alive():
            return
        c = self.canvas
        dl = self._dl
        if dl is None:
            return
        try:
            s = self._calc_scale()
            c.delete("all")
            self._nav_regions = []
            self._active_photos = []
            ox, oy = BEZEL, BEZEL + STATUS_H
            out_w = SCREEN_W + 2 * BEZEL
            out_h = BEZEL + SCREEN_H + NAV_H + BEZEL
            c.configure(scrollregion=(0, 0, int((out_w + 24) * s), int((out_h + 16) * s)))
            c.create_rectangle(3 * s, 3 * s, out_w * s, out_h * s, fill="#0c0e12", outline="#000000", width=max(1, int(2 * s)))
            c.create_rectangle(BEZEL * s, BEZEL * s, (BEZEL + SCREEN_W) * s, (BEZEL + SCREEN_H) * s,
                               fill="#f3f4f7", outline="#23272e")
            view_h = SCREEN_H - STATUS_H
            vy0, vy1 = oy, oy + view_h
            cmds = dl.paint()
            content_h = max((cmd.get("y", 0) + cmd.get("h", 0) for cmd in cmds), default=view_h)
            self._max_scroll = max(0, content_h - view_h)
            sy = max(0, min(self.scroll_y, self._max_scroll)); self.scroll_y = sy
            f_main = ("Segoe UI", -max(8, int(round(12 * s))))
            for cmd in cmds:
                if self._draw_extra_cmd(c, cmd, ox, oy, sy, s, vy0, vy1):
                    continue
                cy = oy + cmd.get("y", 0) - sy
                op = cmd.get("op")
                if op in ("rect", "oval"):
                    if cy + cmd.get("h", 0) <= vy0 or cy >= vy1:
                        continue
                    x0 = ox + cmd.get("x", 0)
                    fn = c.create_oval if op == "oval" else c.create_rectangle
                    fn(x0 * s, cy * s, (x0 + cmd.get("w", 0)) * s, (cy + cmd.get("h", 0)) * s,
                       fill=cmd.get("fill") or "", outline=cmd.get("outline") or "")
                elif op == "line":
                    c.create_line((ox + cmd.get("x", 0)) * s, cy * s, (ox + cmd.get("x2", 0)) * s,
                                  (oy + cmd.get("y2", 0) - sy) * s, fill=cmd.get("color") or "#000")
                elif op == "text":
                    if cy < vy0 or cy > vy1:
                        continue
                    anchor = {"c": "center", "w": "w", "e": "e"}.get(cmd.get("anchor"), "w")
                    c.create_text((ox + cmd.get("x", 0)) * s, cy * s, text=cmd.get("text", ""),
                                  fill=cmd.get("color") or "#000", anchor=anchor, font=f_main)
            c.create_rectangle(BEZEL * s, BEZEL * s, (BEZEL + SCREEN_W) * s, (BEZEL + STATUS_H) * s,
                               fill="#e7e9ee", outline="")
            if getattr(self, "_ui_inspect_mode", False):
                self.status.config(text="🟢 UI-Lupe aktiv (Detail-Bounds nur im View-Baum verfügbar)")
            else:
                self.status.config(text="%dx%d · %d Zeichenbefehle · Zoom %d%%"
                                   % (SCREEN_W, SCREEN_H, len(cmds), int(round(s * 100))))
            self._sync_mcp_snapshot(dl=dl)
        except Exception as e:
            self._log("render(dl): %r" % e)

    def _load_widgets(self):
        self.set_tree(widgets_demo())
        self._log("Widget-Demo geladen (CheckBox/Switch/ProgressBar; Klick toggelt / erhoeht).")

    def _load_demo(self):
        self.set_tree(demo_screen())
        self._log("Demo-Screen geladen.")

    def _load_nav_example(self):
        """Zwei-Screen-App: Klick auf 'Weiter' navigiert zum zweiten Screen."""
        try:
            from core.java.demo_app import build_nav_demo
            tree, vm, main, nav = build_nav_demo()
            self._nav = nav
            self.set_tree(tree, vm, main)
            self._log("Navigations-Demo gestartet (Button 'Weiter' wechselt den Screen).")
        except Exception as e:
            self._log("Navigations-Demo FEHLER: %r" % e)

    def _load_xml_example(self):
        """Echtes XML-Layout inflaten und anzeigen (interaktiv)."""
        try:
            from core.java.demo_app import build_xml_demo_tree
            self.set_tree(build_xml_demo_tree(), None)
            self._log("XML-Layout inflatet und gerendert.")
        except Exception as e:
            self._log("XML-Beispiel FEHLER: %r" % e)

    def _load_example(self):
        """Echte Beispiel-App: fuehrt onCreate aus und zeigt den aufgezeichneten View-Baum."""
        try:
            from core.java.demo_app import build_demo_tree
            tree, vm, activity = build_demo_tree()
            self._nav = None
            self.set_tree(tree, vm, activity)
            self._log("Beispiel-App gestartet (echter onCreate-Lauf, %d Views)." % len(tree.nodes()))
        except Exception as e:
            self._log("Beispiel-App FEHLER: %r" % e)

    def _load_login(self):
        """Login-Demo mit nativer Auth-Lib: onCreate baut den Login-Screen, Anmelden-Klick ruft die
        native Pruefung (Fusion). Passwort eingeben, dann Anmelden."""
        try:
            from core.java.demo_login import build_login_app
            ctx = build_login_app()
            self._nav = None
            self._login_ctx = ctx
            self.set_tree(ctx["tree"], ctx["vm"], ctx["activity"])
            self._log("Login-Demo gestartet (onCreate + native Auth-Lib, %d Views). Passwort eingeben, dann Anmelden." % len(ctx["tree"].nodes()))
        except Exception as e:
            self._log("Login-Demo FEHLER: %r" % e)

    def _load_compose(self):
        """Render-Produzent Familie B (Canvas-Recorder): immediate-mode Display-Liste."""
        try:
            from core.java.canvas_recorder import demo
            self.set_surface(demo().surface())
            self._log("Compose-Demo (Canvas-Recorder, Familie B): %d Zeichenbefehle."
                      % len(self._dl.paint() if self._dl else []))
        except Exception as e:
            self._log("Compose-Demo FEHLER: %r" % e)

    def _load_rn(self):
        """Render-Produzent Familie A (RN-Fabric-Mount-Shim): View-Baum aus Mount-Sequenz."""
        try:
            from core.java.rn_producer import demo
            self._nav = None
            self.set_surface(demo().surface())
            self._log("RN-Demo (Fabric-Mount-Shim, Familie A): View-Baum aus nachgespielter Mount-Sequenz.")
        except Exception as e:
            self._log("RN-Demo FEHLER: %r" % e)

    def _load_rn_js(self):
        """S3 JS-Weg: echtes App-JS auf echter JS-Engine (Node) -> Fabric-Mount-JSON -> ViewTree.
        Faellt ohne Node auf den deterministischen demo()-Baum zurueck (gleiche Struktur)."""
        try:
            from core.java.rn_js_bridge import run_js_app, node_available, demo
            self._nav = None
            if node_available():
                surface, rec, _ = run_js_app()
                self.set_surface(surface)
                self._log("RN-JS-Demo (Node-Host): echtes JS -> nativeFabricUIManager-Mock -> ViewTree. Button-Tap wird als Press protokolliert.")
            else:
                surface, rec = demo()
                self.set_surface(surface)
                self._log("RN-JS-Demo (ohne Node: demo-Baum): JS-Weg-Struktur, Button klickbar.")
        except Exception as e:
            self._log("RN-JS-Demo FEHLER: %r" % e)

    def _load_rn_hermes(self):
        """S3-Hermes: ECHTES RN-Bundle auf der echten C++-Hermes-Runtime (dist/). Faellt ohne Host/dist
        sauber auf den Node-Mock (rn_js) zurueck. Der lebende Client haengt am Surface -> Tap/Taste
        loesen echte Re-Commits aus."""
        # NICHT-BLOCKIEREND: Host-Boot im Hintergrund-Thread, Surface-Setzen via app.after in die
        # tk-Schleife marshallt -> die GUI friert nie ein (auch wenn ein Host langsam ist/haengt).
        self._nav = None
        self._log("RN-Hermes-Demo: lade ...")

        def _work():
            try:
                import core.java.rn_js_bridge  # noqa: Node-Mock verfuegbar
                from core.java.rn_hermes_bridge import rn_hermes_producer, hermes_host_available
                surface = rn_hermes_producer({})
                real = hermes_host_available()

                def _apply():
                    self.set_surface(surface)
                    if real and surface is not None:
                        self._log("RN-Hermes-Demo: echtes RN-Bundle auf der C++-Hermes-Runtime. Tap/Taste -> echter Re-Commit.")
                    else:
                        self._log("RN-Hermes-Demo (Fallback Node-Mock: keine hermes_host.exe/dist). Struktur identisch.")
                self.app.after(0, _apply)
            except Exception as e:
                self.app.after(0, lambda: self._log("RN-Hermes-Demo FEHLER: %r" % e))

        import threading
        threading.Thread(target=_work, daemon=True).start()

    def _load_skia(self):
        """Render-Produzent Familie C (Skia/Flutter-DisplayList-Recorder): immediate-mode."""
        try:
            from core.java.skia_recorder import demo
            self._nav = None
            self.set_surface(demo().surface())
            self._log("Flutter-Demo (Skia-Recorder, Familie C): DisplayList -> Zeichenbefehle -> Grafik.")
        except Exception as e:
            self._log("Flutter-Demo FEHLER: %r" % e)

    # ---- App-Lauf (benutzerfreundlich: Root + Launcher automatisch) -------
    def _guess_entry(self, root):
        try:
            from core.java.manifest import parse_manifest
            am = parse_manifest(root)
            return am.entry() or ""
        except Exception:
            return ""

    def _load_glass(self):
        """Gläserner App-Lauf über den Debugger (ein VM: Recorder + BPs + Aktionspunkte).
        Startet den Lifecycle im Debugger; der aufgezeichnete View-Baum wird hier gerendert,
        Pausen (BP/Aktionspunkt) koppeln über EventBus zurück."""
        ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        if ctrl is None:
            self._log("Debugger-Reiter nicht verfügbar."); return
        ent = (self._entry_var.get() or "").strip()
        if ent:
            ctrl.app_entry = ent
        ctrl.zone = self.zone_var.get()        # AP1.2: gewaehlte Zone (original/patched) uebernehmen
        ctrl.zone_root = ""                      # Zonen-Aufloesung ueber cfg (nicht fix ueberschreiben)
        self._log("Gläsern starten (über Debugger, Zone %s) ..." % ctrl.zone)
        try:
            ctrl.start_app()
        except Exception as e:
            self._log("Gläsern-Start FEHLER: %r" % e); return
        if getattr(ctrl, "glass_tree", None) is not None:
            self.set_tree(ctrl.glass_tree, vm="glass")

    def _on_surface_event(self, data):
        if not self._alive():
            return
        d = data or {}
        surf = d.get("surface")
        client = d.get("client")
        if client is not None:
            self._hermes_client = client
        if surf is None:
            return
        def _apply():
            if not self._alive():
                return
            if client is not None:
                try:
                    surf._hermes_client = client
                except Exception:
                    pass
            self.set_surface(surf)
        try:
            self.app.after(0, _apply)
        except Exception:
            _apply()

    def _on_debug_paused(self, data):
        """Debug-Halt am Breakpoint -> lebenden Hermes-Host am Leben halten (Idle-Timeout aussetzen)."""
        if not self._alive():
            return
        cl = self._hermes_client
        if cl is not None and hasattr(cl, "set_paused"):
            try:
                cl.set_paused(bool(isinstance(data, dict) and data.get("paused")))
            except Exception:
                pass

    def _on_glass_event(self, data):
        if not self._alive():
            return
        tree = (data or {}).get("tree")
        mode = (data or {}).get("mode")

        def _apply():
            if not self._alive():
                return
            # AP1.4: Baum leer (oder reine Engine-Surface-Huelle wie FlutterView) -> Diagnose-Overlay.
            has_views = tree is not None and getattr(tree, "root", None) is not None
            rt_root = ""
            if has_views and is_unrendered_surface_shell(tree):
                try:
                    c = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
                    rt_root = c._resolve_root() if c is not None else ""
                except Exception:
                    rt_root = ""
            if has_views and not (rt_root and is_unrendered_surface_shell(tree) and detect_runtime(rt_root)[0]):
                self._browser_mode = False
                self.tree = tree
                self.vm = "glass"
                self._glass_mode = mode
                self._resolve_live_focus()
                fctrl = self._inphone_ctrl(tree)
                if fctrl is not None and hasattr(fctrl, "set_async_updater"):
                    fctrl.set_async_updater(self._on_inphone_browser_async)
                self._update_web_badge()
                self.render()
            else:
                self._glass_mode = mode
                self._render_status_overlay(mode)
        try:
            self.app.after(0, _apply)
        except Exception:
            _apply()

    def _render_status_overlay(self, mode=None):
        """AP1.4: Zeigt den echten App-Lauf-Zustand, wenn (noch) kein View-Baum vorliegt:
        Phase/Modus, aktuelle Methode, Anzahl geladener Klassen, HTTP-Requests, erkanntes UI-Framework."""
        if not self._alive():
            return
        c = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        lines = []
        rt_label = ""
        rt_why = ""
        root = ""
        try:
            if c is not None:
                cm = c.current_method()
                lines.append("Methode: %s" % (cm or "-"))
                lines.append("Geladene Klassen: %d" % len(c.loaded_classes()))
                nl = getattr(c, "net_log", None)
                nreq = len(getattr(nl, "entries", []) or []) if nl is not None else 0
                lines.append("HTTP-Requests: %d" % nreq)
                errs = [e for e in (getattr(c, "_clinit_errors", None) or [])]
                if errs:
                    lines.append("clinit-Fehler: %d" % len(errs))
                try:
                    root = c._resolve_root() or ""
                    if root:
                        rt_label, rt_why = detect_runtime(root)
                except Exception:
                    rt_label = ""
            lines.append("Modus: %s" % (mode or self._glass_mode or "-"))
        except Exception as e:
            lines.append("Status-Fehler: %r" % e)
        if not lines:
            lines = ["(kein Lauf aktiv)"]
        _m = (mode or self._glass_mode or "").lower()
        if rt_label:
            lines += ["", "UI-Framework: %s" % rt_label]
            if rt_why:
                lines.append(rt_why)
            if rt_label == "Flutter":
                lines.append("UI & Netzwerk (Dart/Skia/BoringSSL) laufen nativ in libapp.so/libflutter.so, nicht ueber android.view/OkHttp.")
            else:
                lines.append("Noch kein gerenderter View-Baum - live beobachtbar im Debugger-Reiter.")
            lines.extend(inspect_engine_details(root))
        elif _m == "done":
            lines += ["", "App-Lifecycle abgeschlossen,",
                      "aber kein android.view-Baum erzeugt.",
                      "Details siehe Main Console / Call-Stack."]
        else:
            lines += ["", "Noch kein View-Baum aufgebaut -",
                      "App startet / pausiert (Debugger-Reiter)."]
        try:
            badge = "RUNTIME-GRENZE" if rt_label else (mode or "LAUF").upper()[:10]
            title = ("%s-App (%s)" % (rt_label, mode or "lauf")) if rt_label else "Live-App-Status"
            self.set_tree(info_screen(title, lines, badge=badge), vm="glass" if self.vm == "glass" else None)
        except Exception:
            pass

    def _load_from_run(self):
        if self._busy:
            return
        # 1) Root = entpacktes App-Verzeichnis aus dem Smali-Studio-Index, sonst Debugger-Zone
        studio = getattr(getattr(self.app, "workspace_tab", None), "smali_studio", None)
        root = ""
        if studio is not None:
            try:
                root = studio.get_smali_dir()
            except Exception:
                root = ""
        if not root or not os.path.isdir(root):
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            try:
                root = ctrl._resolve_root() if ctrl is not None else ""
            except Exception:
                root = ""
        if not root or not os.path.isdir(root):
            self._log("Keine entpackte App gefunden - erst im App Manager laden/entpacken.")
            self.set_tree(info_screen("Keine App geladen", [
                "Es ist keine entpackte App ausgewaehlt.",
                "",
                "Lade/entpacke zuerst eine APK im App",
                "Manager (oder setze die Zonen-Wurzel im",
                "Debugger-Reiter) und starte erneut.",
            ], badge="EINRICHTEN"))
            return
        # 2) Entry aus Feld, sonst Manifest-Launcher, sonst Debugger-App-Entry
        entry = (self._entry_var.get() or "").strip()
        if not entry:
            entry = self._guess_entry(root)
            if entry:
                self._entry_var.set(entry)
        if not entry:
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            entry = (getattr(ctrl, "app_entry", "") or "").strip() if ctrl is not None else ""
        if not entry:
            self._log("Launcher nicht aus dem Manifest ermittelbar - bitte Entry eintragen.")
            self.set_tree(info_screen("App-Entry fehlt", [
                "Der Launcher liess sich nicht aus dem",
                "Manifest ermitteln.",
                "",
                "Trage oben einen Entry ein, z.B.",
                "Lcom/app/MainActivity;  und starte erneut.",
            ], badge="EINRICHTEN"))
            return
        zone = self.zone_var.get()
        self._busy = True
        self._log("App-Lauf %s (Zone %s) ..." % (entry, zone))

        def _w():
            try:
                tree, vm = self._run_lifecycle(root, zone, entry)
                label, why = detect_runtime(root)
                if tree is None or tree.root is None or is_unrendered_surface_shell(tree):
                    if tree is None:
                        from core.java.view_tree import ViewTree
                        tree = ViewTree()
                    stubs = getattr(getattr(vm, "runtime", None), "stubs", None)
                    try:
                        mount_inphone_runtime_if_needed(
                            tree, root, stubs=stubs, on_async_update=self._on_inphone_browser_async,
                        )
                    except Exception:
                        pass
                if tree is None or tree.root is None or (is_unrendered_surface_shell(tree) and label):
                    if label:
                        lines = [
                            why,
                            "",
                            "Diese App nutzt %s fuer ihre" % label,
                            "Oberflaeche zur Laufzeit - es",
                            "liegt kein reiner statischer",
                            "android.view-Baum vor.",
                        ]
                        lines.extend(inspect_engine_details(root))
                        lines += [
                            "",
                            "Nutze 'Gläsern starten' fuer die",
                            "vollstaendige Laufzeit-Bruecke.",
                        ]
                        self.app.after(0, lambda la=label: self.set_tree(
                            info_screen(la + " erkannt", lines, badge="RUNTIME-GRENZE")))
                        self._log("%s erkannt: %s" % (label, why))
                    else:
                        lines = [
                            "Der Lauf hat keinen android.view-Baum",
                            "erzeugt.",
                            "",
                            "Moeglich: UI erst zur Laufzeit gebaut,",
                            "Lauf zu frueh abgebrochen, oder Entry/",
                            "Zone unpassend.",
                            "",
                            "Pruefe Entry + Zone, oder nutze die",
                            "Beispiel-Apps.",
                        ]
                        self.app.after(0, lambda: self.set_tree(
                            info_screen("Kein View-Baum", lines, badge="HINWEIS")))
                        self._log("Lauf lieferte keinen statischen View-Baum.")
                else:
                    self.app.after(0, lambda: self.set_tree(tree, vm))
                    self._log("View-Baum uebernommen (%d Views)." % len(tree.nodes()))
            except Exception as e:
                self._log("App-Lauf FEHLER: %r" % e)
            finally:
                self._busy = False
        threading.Thread(target=_w, daemon=True).start()

    def _run_lifecycle(self, root, zone, entry):
        """Generischer Lauf: VM + Stubs + View-Recorder, dann LifecycleRunner.run_app."""
        from core.java.dalvik import DalvikVM
        from core.java.class_provider import StudioClassProvider
        from core.java.dex_loader import AndroidStubs
        from core.java.lifecycle import LifecycleRunner, _StubRuntime
        from core.java.view_tree import install_view_stubs

        vm = DalvikVM()
        vm.class_provider = StudioClassProvider(root, zone=zone)
        stubs = AndroidStubs(vm=vm)
        tree = install_view_stubs(stubs, app_root=root)
        try:
            stubs.seed_framework_statics(vm)
        except Exception:
            pass
        vm.runtime = _StubRuntime(stubs)
        runner = LifecycleRunner(vm, provider=vm.class_provider, with_stubs=False, stubs=stubs)
        try:
            runner.run_app(studio_root=root, launcher=entry)
        except Exception as e:
            self._log("Lifecycle (teilweise): %r" % e)
        return tree, vm

    # ---- MCP Live-Sync (nur bei Aenderung) --------------------------------
    def _sync_mcp_snapshot(self, *, tree=None, dl=None, status_clock="12:00", foc=None):
        """Exportiert den aktuellen GUI-Screen fuer `native.ui_view` nach `last_ui.json`."""
        try:
            key = (
                id(tree) if tree is not None else 0,
                getattr(tree, "version", -1) if tree is not None else -1,
                id(dl) if dl is not None else 0,
                getattr(dl, "_frame", -1) if dl is not None else -1,
                int(getattr(self, "scroll_y", 0) or 0),
                str(status_clock or "12:00"),
                bool(getattr(self, "_pw_mask", False)),
                getattr(tree, "_toast_text", None) if tree is not None else None,
                getattr(tree, "last_toast", None) if tree is not None else None,
                tuple(getattr(foc, "bounds", ()) or ()) if foc is not None else (),
            )
            if key == getattr(self, "_last_mcp_snap_key", None):
                return
            self._last_mcp_snap_key = key
            from core.java.ui_mcp_bridge import build_ui_snapshot_dict, persist_live_ui_snapshot
            cfg = getattr(getattr(self, "app", None), "cfg", None)
            pkg = ""
            out_dir = None
            if cfg is not None and hasattr(cfg, "config"):
                pkg = str(cfg.config.get("APP_PACKAGE") or "")
                base = str(cfg.config.get("BASE_DIR") or "").strip()
                if base:
                    out_dir = os.path.join(base, "secrets", "probe", "out", "glass")
            snap = build_ui_snapshot_dict(
                tree=tree,
                dl=dl,
                scroll_y=int(getattr(self, "scroll_y", 0) or 0),
                max_scroll=int(getattr(self, "_max_scroll", 0) or 0),
                status_clock=status_clock,
                focused_bounds=list(foc.bounds) if (foc is not None and getattr(foc, "bounds", None)) else None,
                package=pkg,
                source="gui_live",
            )
            persist_live_ui_snapshot(snap, out_dir=out_dir)
        except Exception:
            pass

    # ---- Rendering (Telefonrahmen) ---------------------------------------
    def render(self):
        if not self._alive():
            return
        c = self.canvas
        try:
            s = self._calc_scale()
            c.delete("all")
            self._nav_regions = []
            self._active_photos = []
            ox, oy = BEZEL, BEZEL + STATUS_H
            out_w = SCREEN_W + 2 * BEZEL
            out_h = BEZEL + SCREEN_H + NAV_H + BEZEL
            c.configure(scrollregion=(0, 0, int((out_w + 24) * s), int((out_h + 16) * s)))
            # Gehaeuse
            c.create_rectangle(3 * s, 3 * s, out_w * s, out_h * s,
                               fill="#0c0e12", outline="#000000", width=max(1, int(2 * s)))
            t = self.tree
            dark = (t.effective_dark() if hasattr(t, "effective_dark") else bool(getattr(t, "dark_mode", False))) if t is not None else False
            win_bg = getattr(t, "window_bg", None) if t is not None else None
            self._cur_screen_dark = bool(dark or (getattr(t, "window_bg_dark", False) if t is not None else False))
            # Bildschirm
            c.create_rectangle(BEZEL * s, BEZEL * s, (BEZEL + SCREEN_W) * s, (BEZEL + SCREEN_H) * s,
                               fill=win_bg or ("#121418" if dark else "#f3f4f7"), outline="#23272e")
            # ViewTree (vertikal scrollbar); Statusleiste wird DANACH darueber gezeichnet
            view_h = SCREEN_H - STATUS_H
            vy0, vy1 = oy, oy + view_h
            f_main = ("Segoe UI", -max(8, int(round(12 * s))))
            f_sm = ("Segoe UI", -max(7, int(round(10 * s))))
            foc = None
            if t is not None and t.root is not None:
                t.width = SCREEN_W
                t.height = view_h
                t.mask_passwords = bool(getattr(self, "_pw_mask", False))
                if hasattr(t, "ensure_layout"):
                    t.ensure_layout()
                else:
                    t.layout()
                content_h = t.root.bounds[3]
                self._max_scroll = max(0, content_h - view_h)
                sy = max(0, min(self.scroll_y, self._max_scroll))
                self.scroll_y = sy
                cache_key = (
                    id(t),
                    getattr(t, "version", 0),
                    t.width,
                    t.height,
                    bool(getattr(t, "mask_passwords", False)),
                )
                pcache = getattr(self, "_paint_cache", None)
                if pcache is not None and pcache[0] is t and pcache[1] == cache_key:
                    cmds = pcache[2]
                else:
                    cmds = paint_commands(t)
                    hl_cur = getattr(t, "_highlight_node", None)
                    if hl_cur is not None and getattr(hl_cur, "bounds", None):
                        hb = tuple(hl_cur.bounds)
                        cmds = [
                            cmd for cmd in cmds
                            if not (
                                cmd.get("op") == "rect"
                                and cmd.get("outline") == "#00e5ff"
                                and cmd.get("width") == 2
                                and cmd.get("fill") == ""
                                and (cmd.get("x"), cmd.get("y"), cmd.get("w"), cmd.get("h")) == hb
                            )
                        ]
                    self._paint_cache = (t, cache_key, cmds)
                for cmd in cmds:
                    if self._draw_extra_cmd(c, cmd, ox, oy, sy, s, vy0, vy1):
                        continue
                    cy = (oy + cmd["y"]) if cmd.get("fixed_y") else (oy + cmd["y"] - sy)
                    op = cmd["op"]
                    if op in ("rect", "oval"):
                        if cy + cmd["h"] <= vy0 or cy >= vy1:   # ausserhalb -> cullen
                            continue
                        raw_x0 = ox + cmd["x"]
                        raw_x1 = raw_x0 + cmd["w"]
                        rx0 = max(ox, raw_x0) if op == "rect" else raw_x0
                        rx1 = min(ox + SCREEN_W, raw_x1) if op == "rect" else raw_x1
                        ry0 = max(vy0, cy) if op == "rect" else cy
                        ry1 = min(vy1, cy + cmd["h"]) if op == "rect" else (cy + cmd["h"])
                        if rx1 <= rx0 or ry1 <= ry0:
                            continue
                        fn = c.create_oval if op == "oval" else c.create_rectangle
                        kw_shape = {"fill": cmd["fill"] or "", "outline": cmd["outline"]}
                        if cmd.get("width"):
                            kw_shape["width"] = max(1, int(round(cmd["width"] * s)))
                        fn(rx0 * s, ry0 * s, rx1 * s, ry1 * s, **kw_shape)
                    elif op == "line":
                        c.create_line((ox + cmd.get("x", 0)) * s, cy * s,
                                      (ox + cmd.get("x2", 0)) * s, (oy + cmd.get("y2", 0) - sy) * s,
                                      fill=cmd.get("color") or "#000",
                                      width=max(1, int(round(cmd.get("width", 1) * s))))
                    else:
                        if cy < vy0 - 40 or cy > vy1 + 40:
                            continue
                        anchor = {"c": "center", "w": "w", "e": "e"}.get(cmd.get("anchor"), "w")
                        fs_base = cmd.get("font_size", 10)
                        px_sz = -max(8, int(round(fs_base * 1.2 * s)))
                        f_use = ("Segoe UI", px_sz, "bold") if cmd.get("bold") else (
                            ("Segoe UI", px_sz) if "font_size" in cmd else f_main)
                        tkw = {"text": cmd["text"], "fill": cmd["color"], "anchor": anchor, "font": f_use}
                        if cmd.get("max_w"):
                            tkw["width"] = max(24, int(round(cmd["max_w"] * s)))
                            if cmd.get("justify"):
                                tkw["justify"] = cmd["justify"]
                        c.create_text((ox + cmd["x"]) * s, cy * s, **tkw)
                foc = self._resolve_live_focus()
                if foc is not None:
                    fx, fy, fw, fh = foc.bounds
                    c.create_rectangle((ox + fx) * s, (oy + fy - sy) * s,
                                       (ox + fx + fw) * s, (oy + fy + fh - sy) * s,
                                       outline="#f9a825", width=max(1, int(2 * s)))
            else:
                self._max_scroll = 0
            # Gehaeuse-Kappen oben/unten als Maske fuer mehrzeilige Scroll-Texte am Rand
            c.create_rectangle(3 * s, 3 * s, out_w * s, BEZEL * s, fill="#0c0e12", outline="")
            c.create_rectangle(3 * s, (BEZEL + SCREEN_H + NAV_H) * s, out_w * s, (out_h + 24) * s,
                               fill="#0c0e12", outline="")
            # Statusleiste (ueber dem Inhalt -> maskiert oberen Ueberlauf)
            c.create_rectangle(BEZEL * s, BEZEL * s, (BEZEL + SCREEN_W) * s, (BEZEL + STATUS_H) * s,
                               fill="#181b20" if dark else "#e7e9ee", outline="")
            st_col = "#cfd4dc" if dark else "#333"
            _st_clk = getattr(t, "_stubs_ref", None) if t is not None else None
            _clk_txt = (
                _st_clk.format_status_clock()
                if (_st_clk is not None and hasattr(_st_clk, "format_status_clock"))
                else "12:00"
            )
            c.create_text((BEZEL + 8) * s, (BEZEL + STATUS_H / 2) * s, text=_clk_txt, anchor="w",
                          font=f_sm, fill=st_col)
            _is_reconstructed = t is not None and getattr(t, "render_source", "executed") == "reconstructed"
            if _is_reconstructed:
                c.create_text((BEZEL + SCREEN_W / 2) * s, (BEZEL + STATUS_H / 2) * s,
                              text="[Rekonstruiert]", anchor="c", font=f_sm,
                              fill="#fbbf24" if dark else "#b45309")
            c.create_text((BEZEL + SCREEN_W - 8) * s, (BEZEL + STATUS_H / 2) * s, text="100%",
                          anchor="e", font=f_sm, fill=st_col)
            # Scroll-Indikator
            if self._max_scroll > 0 and t is not None and t.root is not None:
                knob = max(24, int(view_h * view_h / t.root.bounds[3]))
                ky = BEZEL + STATUS_H + int((view_h - knob) * self.scroll_y / self._max_scroll)
                c.create_rectangle((BEZEL + SCREEN_W - 5) * s, ky * s,
                                   (BEZEL + SCREEN_W - 2) * s, (ky + knob) * s,
                                   fill="#9aa5b5", outline="")
            # Android-Navigationsleiste
            ny = BEZEL + SCREEN_H
            c.create_rectangle(BEZEL * s, ny * s, (BEZEL + SCREEN_W) * s, (ny + NAV_H) * s,
                               fill="#15181d", outline="")
            third = SCREEN_W / 3.0
            cyb = ny + NAV_H / 2
            bx = BEZEL + third * 0.5
            hx = BEZEL + third * 1.5
            rx = BEZEL + third * 2.5
            # Zurueck (Dreieck), Home (Kreis), Kontext/Recent (Quadrat)
            c.create_polygon((bx + 9) * s, (cyb - 10) * s, (bx + 9) * s, (cyb + 10) * s, (bx - 9) * s, cyb * s,
                             fill="#cfd4dc", outline="")
            c.create_oval((hx - 11) * s, (cyb - 11) * s, (hx + 11) * s, (cyb + 11) * s,
                          outline="#cfd4dc", width=max(1, int(2 * s)))
            c.create_rectangle((rx - 10) * s, (cyb - 10) * s, (rx + 10) * s, (cyb + 10) * s,
                               outline="#cfd4dc", width=max(1, int(2 * s)))
            self._nav_regions += [
                ("back", BEZEL, ny, BEZEL + third, ny + NAV_H),
                ("home", BEZEL + third, ny, BEZEL + 2 * third, ny + NAV_H),
                ("recent", BEZEL + 2 * third, ny, BEZEL + SCREEN_W, ny + NAV_H),
            ]
            # Seitentasten (rechter Rahmen): Lauter/Leiser/Power
            vx0 = BEZEL + SCREEN_W + 3
            vx1 = out_w - 3
            for name, y0, y1, col, lbl in (
                    ("vol_up", BEZEL + 140, BEZEL + 196, "#2a2e35", "+"),
                    ("vol_down", BEZEL + 206, BEZEL + 262, "#2a2e35", "–"),
                    ("power", BEZEL + 300, BEZEL + 350, "#3a3e45", "")):
                c.create_rectangle(vx0 * s, y0 * s, vx1 * s, y1 * s, fill=col, outline="")
                if lbl:
                    c.create_text(((vx0 + vx1) / 2) * s, ((y0 + y1) / 2) * s, text=lbl,
                                  fill="#cfd4dc", font=f_sm)
                self._nav_regions.append((name, vx0, y0, vx1, y1))
            hl = getattr(t, "_highlight_node", None) if t is not None else None
            if hl is not None and getattr(hl, "bounds", None):
                hx, hy, hw, hh = hl.bounds
                if hw > 0 and hh > 0:
                    from core.java.view_tree import _is_fixed_y_node
                    hl_y = (oy + hy) if _is_fixed_y_node(hl) else (oy + hy - sy)
                    if hl_y + hh > vy0 and hl_y < vy1:
                        c.create_rectangle(
                            (ox + hx) * s, max(vy0, hl_y) * s,
                            (ox + hx + hw) * s, min(vy1, hl_y + hh) * s,
                            fill="", outline="#00e5ff", width=max(1, int(round(2 * s))),
                        )
            if getattr(self, "_ui_inspect_mode", False):
                if hl is not None and getattr(hl, "bounds", None):
                    hx, hy, hw, hh = hl.bounds
                    hud_lbl = "%s%s (%dx%d)" % (
                        getattr(hl, "cls", "View"),
                        (" #%s" % hl.vid) if getattr(hl, "vid", None) else "",
                        hw, hh,
                    )
                    pil_w = max(84, min(SCREEN_W - 12, len(hud_lbl) * 6 + 14))
                    pil_h = 18
                    px0 = max(BEZEL + 4, min(BEZEL + SCREEN_W - pil_w - 4, ox + hx))
                    py_above = oy + hy - sy - pil_h - 4
                    py_below = oy + hy + hh - sy + 4
                    py0 = py_above if py_above >= (BEZEL + STATUS_H + 2) else min(BEZEL + SCREEN_H - pil_h - 4, py_below)
                    py0 = max(BEZEL + STATUS_H + 2, py0)
                    c.create_rectangle(px0 * s, py0 * s, (px0 + pil_w) * s, (py0 + pil_h) * s,
                                       fill="#0f172a", outline="#00e5ff", width=max(1, int(s)))
                    c.create_text((px0 + 6) * s, (py0 + pil_h / 2) * s, text=hud_lbl,
                                  anchor="w", fill="#e0f2fe", font=f_sm)
                else:
                    msg = "🎯 UI-Lupe aktiv — Maus über ein Element bewegen"
                    pil_w = min(SCREEN_W - 16, len(msg) * 6 + 16)
                    px0 = BEZEL + 8
                    py0 = BEZEL + STATUS_H + 6
                    c.create_rectangle(px0 * s, py0 * s, (px0 + pil_w) * s, (py0 + 20) * s,
                                       fill="#0f172a", outline="#00e5ff", width=max(1, int(s)))
                    c.create_text((px0 + 8) * s, (py0 + 10) * s, text=msg,
                                  anchor="w", fill="#e0f2fe", font=f_sm)
            n = len(t.nodes()) if t else 0
            _gm = (" · ⏸ %s" % self._glass_mode) if getattr(self, "_glass_mode", None) in ("paused",) else ""
            _rs = " · [Rekonstruiert]" if _is_reconstructed else ""
            if getattr(self, "_ui_inspect_mode", False):
                if hl is not None:
                    self.status.config(text=self._format_inspect_node(hl))
                else:
                    self.status.config(text="🟢 UI-Lupe aktiv — Maus über App-Element bewegen (Klick = Fokus, kein App-Tap)")
            else:
                self.status.config(text="%dx%d · %d Views · %d%%%s%s%s"
                                   % (SCREEN_W, SCREEN_H, n, int(round(s * 100)),
                                      " (Demo)" if self.vm is None else "", _gm, _rs))
            if t is not None and t.root is not None:
                self._sync_mcp_snapshot(tree=t, status_clock=_clk_txt, foc=foc)
        except Exception as e:
            self._log("render: %r" % e)

    # ---- Bedienung (Maus/Tastatur) ---------------------------------------
    def _focusable_nodes(self):
        t = self.tree
        if t is None or getattr(t, "root", None) is None:
            return []
        raw = t.visible_nodes() if hasattr(t, "visible_nodes") else t.nodes()
        return [nd for nd in raw if getattr(nd, "visible", True) and getattr(nd, "focusable", False)]

    def _set_focus_node(self, node):
        self._focus = node
        if node is None:
            self._focus_anchor = None
            return
        f_nodes = self._focusable_nodes()
        idx = next((i for i, nd in enumerate(f_nodes) if nd is node), -1)
        bx, by, bw, bh = getattr(node, "bounds", (0, 0, 0, 0))
        self._focus_anchor = {
            "vid": getattr(node, "vid", None),
            "hint": (getattr(node, "props", None) or {}).get("hint"),
            "idx": idx,
            "count": len(f_nodes),
            "cx": bx + bw * 0.5,
            "cy": by + bh * 0.5,
        }

    def _resolve_live_focus(self):
        if self._focus is None and self._focus_anchor is None:
            return None
        f_nodes = self._focusable_nodes()
        if not f_nodes:
            self._focus = None
            self._focus_anchor = None
            return None
        if self._focus is not None and any(nd is self._focus for nd in f_nodes):
            self._set_focus_node(self._focus)
            return self._focus
        anc = self._focus_anchor or {}
        matched = None
        vid = anc.get("vid")
        if vid is not None:
            matched = next((nd for nd in f_nodes if getattr(nd, "vid", None) == vid), None)
        if matched is None and anc.get("hint"):
            cands = [nd for nd in f_nodes if (getattr(nd, "props", None) or {}).get("hint") == anc["hint"]]
            if len(cands) == 1:
                matched = cands[0]
            elif cands:
                idx = int(anc.get("idx", -1))
                matched = f_nodes[idx] if (0 <= idx < len(f_nodes) and f_nodes[idx] in cands) else cands[0]
        if matched is None and "cx" in anc and "cy" in anc:
            cx, cy = float(anc["cx"]), float(anc["cy"])
            for nd in f_nodes:
                bx, by, bw, bh = getattr(nd, "bounds", (0, 0, 0, 0))
                if bx <= cx < bx + bw and by <= cy < by + bh:
                    matched = nd
                    break
        if matched is None:
            idx = int(anc.get("idx", -1))
            if 0 <= idx < len(f_nodes) and len(f_nodes) == anc.get("count"):
                matched = f_nodes[idx]
        if matched is not None:
            self._set_focus_node(matched)
            return matched
        self._focus = None
        self._focus_anchor = None
        return None

    def _sync_sibling_displays(self):
        app = self.app
        if app is None:
            return
        other_candidates = (
            getattr(app, "display_tab", None),
            getattr(getattr(app, "debugger_tab", None), "screen_view", None),
        )
        for other in other_candidates:
            if other is not None and other is not self and getattr(other, "tree", None) is self.tree:
                try:
                    if hasattr(other, "_resolve_live_focus"):
                        other._resolve_live_focus()
                    other.render()
                except Exception:
                    pass

    def toggle_password_mask(self):
        self._pw_mask = not bool(getattr(self, "_pw_mask", False))
        if self.tree is not None:
            self.tree.mask_passwords = self._pw_mask
        if hasattr(self, "_pw_btn") and self._pw_btn is not None:
            try:
                self._pw_btn.config(text="🙈 PW" if self._pw_mask else "👁 PW")
            except Exception:
                pass
        self.render()
        self._sync_sibling_displays()

    @staticmethod
    def _format_inspect_node(nd):
        cls = getattr(nd, "cls", "View") or "View"
        vid = f" #{nd.vid}" if getattr(nd, "vid", None) else ""
        props = getattr(nd, "props", None) or {}
        raw_txt = (getattr(nd, "text", None) or props.get("hint") or "").strip()
        txt = f' "{raw_txt[:24]}"' if raw_txt else ""
        clk = " [klickbar]" if getattr(nd, "clickable", False) else ""
        bx, by, bw, bh = getattr(nd, "bounds", (0, 0, 0, 0))
        return f"🎯 {cls}{vid}{txt} · ({bx},{by} {bw}×{bh}){clk}"

    def toggle_ui_inspect(self):
        self._ui_inspect_mode = not bool(getattr(self, "_ui_inspect_mode", False))
        if not self._ui_inspect_mode and self.tree is not None:
            self.tree._highlight_node = None
        if hasattr(self, "_ui_inspect_btn") and self._ui_inspect_btn is not None:
            try:
                self._ui_inspect_btn.config(text="🟢 UI-Lupe: AN" if self._ui_inspect_mode else "🎯 UI-Lupe")
                self._ui_inspect_btn.state(["pressed"] if self._ui_inspect_mode else ["!pressed"])
            except Exception:
                pass
        if hasattr(self, "canvas") and self.canvas is not None:
            try:
                self.canvas.config(cursor="crosshair" if self._ui_inspect_mode else "")
            except Exception:
                pass
        self.render()
        self._sync_sibling_displays()

    def _canvas_to_tree_xy(self, e):
        s = getattr(self, "_eff_s", 1.0) or 1.0
        lx = int(round(self.canvas.canvasx(e.x) / s))
        ly = int(round(self.canvas.canvasy(e.y) / s))
        return lx, ly, lx - BEZEL, ly - (BEZEL + STATUS_H) + self.scroll_y

    def _get_active_stubs(self):
        if self.vm not in (None, "glass"):
            return getattr(self.vm, "_stubs_ref", None)
        ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
        return getattr(ctrl, "_stubs", None) if ctrl is not None else None

    def _update_seekbar_at(self, tx, ty):
        t = self.tree
        if t is None or getattr(t, "root", None) is None:
            return False
        nd = t.node_at(tx, ty)
        cur = nd
        while cur is not None and getattr(cur, "cls", "") != "SeekBar":
            cur = getattr(cur, "_parent", None)
        if cur is None:
            return False
        bx, _by, bw, _bh = getattr(cur, "bounds", (0, 0, 0, 0))
        if bw <= 0:
            return False
        max_v = int((cur.props or {}).get("max", 100) or 100)
        prog = int(round(max(0.0, min(1.0, (tx - bx) / float(bw))) * max_v))
        cur.props["progress"] = prog
        prog_fn = (cur.props or {}).get("$on_progress_fn")
        if callable(prog_fn):
            try:
                prog_fn(prog)
            except Exception:
                pass
        lst = (cur.props or {}).get("$seek_listener")
        stubs = self._get_active_stubs()
        if lst is not None and stubs is not None:
            vm = getattr(stubs, "vm", None)
            if vm is not None and hasattr(vm, "invoke_virtual"):
                try:
                    vm.invoke_virtual(lst, "onProgressChanged", "(Landroid/widget/SeekBar;IZ)V",
                                      (lst, (cur.props or {}).get("$jobj"), prog, 1))
                except Exception:
                    pass
        t.touch()
        self.render()
        return True

    def _on_press(self, e):
        lx, ly, tx, ty = self._canvas_to_tree_xy(e)
        self._drag_start = {
            "lx": lx, "ly": ly, "tx": tx, "ty": ty,
            "ex": e.x, "ey": e.y, "scroll_y": self.scroll_y,
        }
        self._drag_moved = False

    def _on_drag_motion(self, e):
        st = getattr(self, "_drag_start", None)
        if st is None:
            return
        dx = e.x - st["ex"]
        dy = e.y - st["ey"]
        if max(abs(dx), abs(dy)) < 10:
            return
        self._drag_moved = True
        _lx, _ly, tx, ty = self._canvas_to_tree_xy(e)
        if self._update_seekbar_at(tx, st["ty"]):
            return
        if abs(dy) > abs(dx) and self._max_scroll > 0:
            s = getattr(self, "_eff_s", 1.0) or 1.0
            dy_log = int(round(dy / s))
            self.scroll_y = max(0, min(self._max_scroll, st["scroll_y"] - dy_log))
            self.render()

    def _on_release(self, e):
        st = getattr(self, "_drag_start", None)
        self._drag_start = None
        if st is None:
            self._on_click(e)
            return
        dx = e.x - st["ex"]
        dy = e.y - st["ey"]
        if max(abs(dx), abs(dy)) < 12:
            if getattr(self, "_ui_inspect_mode", False) and self.tree is not None:
                _lx, _ly, tx, ty = self._canvas_to_tree_xy(e)
                nd = self.tree.node_at(tx, ty)
                self.tree._highlight_node = nd
                self.render()
                self._sync_sibling_displays()
                return
            self._on_click(e)
            return
        if abs(dx) >= 36 and abs(dx) > abs(dy) * 1.3 and self.tree is not None:
            direction = 1 if dx < 0 else -1
            if swipe_pager(self.tree, st["tx"], st["ty"], direction, stubs=self._get_active_stubs()):
                self.render()
                self._sync_sibling_displays()
                return
        if self._update_seekbar_at(self._canvas_to_tree_xy(e)[2], st["ty"]):
            self._sync_sibling_displays()

    def _on_hover_motion(self, e):
        if not getattr(self, "_ui_inspect_mode", False) or self.tree is None or not self._alive():
            return
        _lx, ly, tx, ty = self._canvas_to_tree_xy(e)
        vy = ly - (BEZEL + STATUS_H)
        if not (0 <= tx < SCREEN_W and vy >= 0):
            return
        nd = (self.tree.fixed_node_at(tx, vy) if hasattr(self.tree, "fixed_node_at") else None) or self.tree.node_at(tx, ty)
        if nd is not getattr(self.tree, "_highlight_node", None):
            self.tree._highlight_node = nd
            self.render()

    def _on_click(self, e):
        if not self._alive():
            return
        self.canvas.focus_set()
        s = getattr(self, "_eff_s", 1.0) or 1.0
        x = int(round(self.canvas.canvasx(e.x) / s))
        y = int(round(self.canvas.canvasy(e.y) / s))
        if self._dl is not None:
            ox, oy = BEZEL, BEZEL + STATUS_H
            evt = {"type": "click", "x": x - ox, "y": y - oy + self.scroll_y}
            self._dl_focus_xy = (evt["x"], evt["y"])   # Fokus fuer Tastatur merken
            self._dl_text = ""                          # neues Feld -> Text neu aufbauen
            dl = self._dl
            def _w(dl=dl, evt=evt):
                # WP3: Dispatch kann am Breakpoint/Host blockieren -> Worker-Thread,
                # Tk-Zeichnen ausschliesslich im Main-Thread via app.after (kein Freeze).
                try:
                    dl.dispatch(evt)
                except Exception as e:
                    self.app.after(0, lambda e=e: self._log("Dispatch-Fehler: %r" % e))
                    return
                self.app.after(0, self._render_dl)
            threading.Thread(target=_w, daemon=True).start()
            return
        for name, x0, y0, x1, y1 in self._nav_regions:
            if x0 <= x < x1 and y0 <= y < y1:
                self._on_nav(name)
                return
        t = self.tree
        if t is None:
            return
        ox, oy = BEZEL, BEZEL + STATUS_H
        view_h = SCREEN_H - STATUS_H
        vy = y - oy
        tx = x - ox
        if not (0 <= tx < t.width and 0 <= vy < view_h):   # nur im Screen-Bereich
            return
        ty = vy + self.scroll_y                            # Scroll-Offset
        fix_nd = t.fixed_node_at(tx, vy) if hasattr(t, "fixed_node_at") else None
        fix_clk = t.fixed_clickable_at(tx, vy) if hasattr(t, "fixed_clickable_at") else None
        is_fixed_hit = (fix_nd is not None) or (fix_clk is not None)
        node = fix_nd if is_fixed_hit else t.node_at(tx, ty)
        foc_node = None
        if node is not None:
            if getattr(node, "focusable", False):
                foc_node = node
            else:
                cur = getattr(node, "_parent", None)
                while cur is not None:
                    if getattr(cur, "focusable", False) and getattr(cur, "cls", "") == "EditText":
                        foc_node = cur
                        break
                    cur = getattr(cur, "_parent", None)
                if foc_node is None:
                    foc_node = next(
                        (sub for sub in getattr(node, "walk", lambda: ())()
                         if getattr(sub, "focusable", False) and getattr(sub, "cls", "") == "EditText"),
                        None,
                    )
        self._set_focus_node(foc_node)
        clk = fix_clk if is_fixed_hit else t.clickable_at(tx, ty)
        ty_eff = vy if is_fixed_hit else ty
        if clk is not None and clk.cls == "WebView" and clk.on_click is None and (clk.props or {}).get("web_url"):
            self.toggle_browser_view(clk.props["web_url"])
            return
        if self.vm == "glass":
            plain_edit_focus = (
                foc_node is not None
                and (clk is None or (
                    clk is foc_node
                    and getattr(clk, "on_click", None) is None
                    and not (getattr(clk, "props", None) or {}).get("onClick")
                ))
            )
            if plain_edit_focus:
                self._update_web_badge()
                self.render()
                return
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if ctrl is not None and hasattr(ctrl, "dispatch_ui_event"):
                self._log("Glass-Tap (%d, %d)" % (tx, ty_eff))
                ctrl.dispatch_ui_event("tap", x=tx, y=ty_eff, fixed=is_fixed_hit)
                self._update_web_badge()
                self.render()
                return
        if clk is not None:
            self._dispatch(clk)
        self._update_web_badge()
        self.render()

    def _on_wheel(self, e):
        if getattr(e, "num", None) == 4:
            delta = -1
        elif getattr(e, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if getattr(e, "delta", 0) > 0 else 1
        new = max(0, min(self.scroll_y + delta * 48, self._max_scroll))
        if new != self.scroll_y:
            self.scroll_y = new
            self.render()

    def _on_nav(self, name):
        labels = {"back": "Zurueck", "home": "Home", "recent": "Kontext/Recent",
                  "vol_up": "Lauter", "vol_down": "Leiser", "power": "Power"}
        self._log("Hardware-Button: %s" % labels.get(name, name))
        if name == "home":
            self._focus = None
            self._focus_anchor = None
            if getattr(self, "_browser_mode", False):
                self.toggle_browser_view()
                return
            fctrl = self._inphone_ctrl()
            if self.vm != "glass" and fctrl is not None and fctrl.handle_home():
                self._update_web_badge()
                self.render()
                return
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if self.vm == "glass" and ctrl is not None and hasattr(ctrl, "dispatch_ui_event"):
                ctrl.dispatch_ui_event("home")
                return
            self.render()
        elif name == "back":
            if getattr(self, "_browser_mode", False) and getattr(self, "_standalone_browser_ctrl", None) is not None:
                self._standalone_browser_ctrl.go_back()
                self._update_web_badge()
                self.render()
                return
            fctrl = self._inphone_ctrl()
            if self.vm != "glass" and fctrl is not None and fctrl.handle_back():
                self._update_web_badge()
                self.render()
                return
            nav = getattr(self, "_nav", None)
            ctrl = getattr(getattr(self.app, "debugger_tab", None), "ctrl", None)
            if self.vm == "glass" and ctrl is not None and hasattr(ctrl, "dispatch_ui_event"):
                ctrl.dispatch_ui_event("back")
                return
            if nav is not None:
                def _bn():
                    try:
                        nav.back()
                    except Exception:
                        pass
                    self.app.after(0, self.render)
                threading.Thread(target=_bn, daemon=True).start()
            elif self.vm not in (None, "glass") and self._activity is not None:
                def _bw():
                    try:
                        if hasattr(self.vm, "invoke_virtual"):
                            self.vm.invoke_virtual(self._activity, "onBackPressed", "()V", (self._activity,))
                        else:
                            self.vm.invoke(self._activity.cls, "onBackPressed", "()V", (self._activity,))
                    except Exception:
                        pass
                    self.app.after(0, self.render)
                threading.Thread(target=_bw, daemon=True).start()

    def _dispatch(self, node):
        """Klick-Injektion: Python-Callback (Demo) direkt, sonst Listener-JObject ueber die VM."""
        cb = node.on_click
        if callable(cb):
            try:
                cb(node)
            except Exception as e:
                self._log("onClick-Fehler: %r" % e)
            self.tree.touch()
            self.render()
            self._log("Klick: %s" % node.short())
            return
        if cb is not None and self.vm is not None:
            self._invoke_listener(cb, node)
            return
        self._log("Klick auf %s (kein Listener)" % node.short())

    def _invoke_listener(self, listener, node):
        """Ruft listener.onClick(View) in der VM (Worker-Thread), danach Re-Render."""
        if self._busy:
            return
        self._busy = True
        self._log("onClick -> %s ..." % getattr(listener, "cls", "?"))

        def _w():
            try:
                from core.java.objects import JObject
                view = getattr(node, "_jobj", None)
                if not isinstance(view, JObject):
                    view = JObject("Landroid/view/View;")
                    node._jobj = view
                view.fields["$view"] = node
                cls = getattr(listener, "cls", None)
                if hasattr(self.vm, "invoke_virtual"):
                    self.vm.invoke_virtual(listener, "onClick", "(Landroid/view/View;)V", (listener, view))
                elif cls:
                    self.vm.invoke(cls, "onClick", "(Landroid/view/View;)V", (listener, view))
                stubs = (
                    getattr(self.vm, "_stubs_ref", None)
                    or getattr(getattr(self.vm, "runtime", None), "stubs", None)
                    or (getattr(self.tree, "_stubs_ref", None) if self.tree is not None else None)
                )
                if stubs is not None and callable(getattr(stubs, "_flush_fragment_transactions", None)):
                    stubs._flush_fragment_transactions()
                if self.tree is not None:
                    self.tree.touch()
                if self.app is not None and hasattr(self.app, "after"):
                    self.app.after(0, self.render)
            except Exception as e:
                self._log("onClick-Dispatch FEHLER: %r" % e)
            finally:
                self._busy = False
        threading.Thread(target=_w, daemon=True).start()

    def _on_key(self, e):
        # RN-Hermes (DisplayList): Taste an das fokussierte TextInput ueber den lebenden Host.
        # key(x,y,text) sendet den VOLLEN Feldtext (topChange) -> hier akkumulieren wir inkrementell.
        if self._dl is not None or self._hermes_client is not None:
            client = self._hermes_client
            xy = self._dl_focus_xy
            if client is None or xy is None:
                return
            if e.keysym == "BackSpace":
                self._dl_text = self._dl_text[:-1]
            elif e.char and e.char.isprintable():
                self._dl_text += e.char
            else:
                return
            fx, fy = xy
            txt = self._dl_text
            def _w(fx=fx, fy=fy, txt=txt):
                # Breakpoint/Host kann blockieren -> Worker-Thread; tk-Zeichnen via app.after (kein Freeze).
                try:
                    client.key(fx, fy, txt)
                except Exception as ex:
                    self.app.after(0, lambda ex=ex: self._log("Tasten-Dispatch-Fehler: %r" % ex))
                    return
                self.app.after(0, self._render_dl)
            threading.Thread(target=_w, daemon=True).start()
            return
        n = self._resolve_live_focus()
        if n is None or not getattr(n, "focusable", False):
            return
        keysym = str(getattr(e, "keysym", "") or "")
        state = int(getattr(e, "state", 0) or 0)
        if keysym in ("Tab", "ISO_Left_Tab"):
            f_nodes = self._focusable_nodes()
            if f_nodes:
                cur_i = next((i for i, nd in enumerate(f_nodes) if nd is n), 0)
                step_d = -1 if (keysym == "ISO_Left_Tab" or (state & 0x1) != 0) else 1
                self._set_focus_node(f_nodes[(cur_i + step_d) % len(f_nodes)])
                self.render()
                self._sync_sibling_displays()
            return "break"
        if keysym in ("Return", "KP_Enter"):
            sub = n.props.get("$on_submit_fn") if hasattr(n, "props") else None
            if callable(sub):
                try:
                    sub(n.text)
                except Exception:
                    pass
                self._resolve_live_focus()
                self._update_web_badge()
                self.tree.touch()
                self.render()
                self._sync_sibling_displays()
            return "break"
        ctrl_held = (state & 0x4) != 0
        if ctrl_held and keysym.lower() == "v":
            try:
                pasted = str(self.clipboard_get() or "").replace("\r", "").replace("\n", "")
            except Exception:
                return
            if not pasted:
                return
            new_txt = (n.text or "") + pasted
        elif ctrl_held:
            return
        elif keysym in ("BackSpace", "Delete"):
            new_txt = (n.text or "")[:-1]
        elif getattr(e, "char", None) and e.char.isprintable():
            new_txt = (n.text or "") + e.char
        else:
            return
        n.text = new_txt
        for sub in getattr(n, "walk", lambda: ())():
            if sub is not n and getattr(sub, "cls", "") == "EditText":
                sub.text = new_txt
        par = getattr(n, "_parent", None)
        if par is not None and getattr(par, "cls", "") == "EditText":
            par.text = new_txt
        self._set_focus_node(n)
        _oc = n.props.get("$on_change_fn") if hasattr(n, "props") else None
        is_paused = (
            self.vm == "glass"
            and getattr(
                getattr(getattr(getattr(self.app, "debugger_tab", None), "ctrl", None), "_jdb", None),
                "mode",
                None,
            ) == "paused"
        )
        if callable(_oc) and not is_paused:
            try:
                _oc(new_txt)
            except Exception:
                pass
        live_n = self._resolve_live_focus()
        if live_n is not None and (not callable(_oc) or is_paused):
            live_n.text = new_txt
        self.tree.touch()
        self.render()
        self._sync_sibling_displays()
