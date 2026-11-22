# -*- coding: utf-8 -*-
"""ui.dock.dock_manager — tk-Spiegel des LayoutModel mit Split-Gruppen, Tab-Drag-&-Drop und
externen Zonen (z. B. die globale Main-Console-Tableiste).

Interne Notebook-Zonen (links/editor/rechts/panel) sind je ein horizontales `PanedWindow` von
`Notebook`-GRUPPEN (Split). Eine **externe Zone** ist ein von aussen gehaltenes `Notebook`
(z. B. `app.console_notebook`): der DockManager verwaltet dort NUR seine eigenen View-Tabs,
app-eigene Tabs (Main Console, Start & Live-Log) bleibt er unangetastet.

Drag-&-Drop (wie VS Code): einen Tab ziehen und
  - auf eine fremde Tableiste loslassen  -> View dockt dort an,
  - auf einen Fensterkoerper loslassen   -> interne Zone vertikal splitten (neue Gruppe).
App-eigene (stateful) Tabs koennen nicht zonen-uebergreifend verschoben werden (tk re-parentet nicht).
Favoritenansichten (Presets) ueber das LayoutModel + JSON.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, simpledialog

from ui.dock.layout_model import LayoutModel, ZONES


class DockManager:
    def __init__(self, parent, app=None, layout_path=None, with_panel=True, ext_notebooks=None):
        self.app = app
        self.with_panel = with_panel
        self.model = LayoutModel()
        self._factories = {}
        self._group_nb = {}     # (zone, group) -> Notebook (intern)
        self._nb_info = {}      # str(notebook) -> (zone, group)
        self._tab_vid = {}      # str(tab-child-widget) -> vid   (None/fehlend = app-eigener Tab)
        self._ext_nb = dict(ext_notebooks or {})   # zone -> externes Notebook
        self._ext_tab = {}      # vid -> frame (in externer Zone)
        self._popups = {}
        self._group_tops = {}   # (zone,group) -> Toplevel (abgedockt)
        self._nb_arrows = {}    # str(nb) -> (prev,next) Scroll-Pfeile
        self._realized_frame_vids = {}
        self._drag = None
        self._ghost = None
        self.layout_path = layout_path or os.path.join("config", "ui_layouts", "debugger.json")

        self.outer = ttk.Frame(parent)
        self.top = ttk.Frame(self.outer); self.top.pack(side="top", fill="x")
        self.top_ctrl = ttk.Frame(self.top); self.top_ctrl.pack(side="right")
        self.top_zone = ttk.Frame(self.top); self.top_zone.pack(side="left", fill="x", expand=True)
        self.status = ttk.Frame(self.outer); self.status.pack(side="bottom", fill="x")
        self.status_zone = ttk.Frame(self.status); self.status_zone.pack(side="left", fill="x", expand=True)

        self.vpan = ttk.PanedWindow(self.outer, orient="vertical"); self.vpan.pack(fill="both", expand=True)
        self.hpan = ttk.PanedWindow(self.vpan, orient="horizontal")
        self.vpan.add(self.hpan, weight=4)
        self._zone_pane = {}
        for zone, weight in (("left", 1), ("editor", 3), ("right", 2)):
            pane = ttk.PanedWindow(self.hpan, orient="horizontal")
            self._zone_pane[zone] = pane
            self.hpan.add(pane, weight=weight)
        if with_panel:
            self._zone_pane["panel"] = ttk.PanedWindow(self.vpan, orient="horizontal")
            self.vpan.add(self._zone_pane["panel"], weight=1)
        self._notebook_zones = ["left", "editor", "right"] + (["panel"] if with_panel else [])
        self._frame_zone = {"top": self.top_zone, "status": self.status_zone}

        # Externe Zonen: DnD einmalig binden, Zonen-Info setzen
        for zone, nb in self._ext_nb.items():
            self._nb_info[str(nb)] = (zone, 0)
            self._bind_dnd(nb)

        self._build_ctrl_menus()

    # ---- Registrierung ----------------------------------------------------
    def register_view(self, id, title, factory, default_zone="panel", visibility="D+"):
        if default_zone == "panel" and not self.with_panel:
            default_zone = "right"
        if default_zone == "console" and "console" not in self._ext_nb:
            default_zone = "right"
        self.model.register_view(id, title, default_zone, visibility)
        self._factories[id] = factory
        return self

    def register_command(self, id, label, fn, icon=None):
        self.model.register_command(id, label, icon)
        ttk.Button(self.top_zone, text=(("%s " % icon) if icon else "") + label, command=fn).pack(side="left", padx=1)

    # ---- Realisierung -----------------------------------------------------
    def build(self):
        try:
            self.model.load_json(self.layout_path)
        except Exception:
            pass
        self._realize()
        return self.outer

    def _clear_internal(self):
        for zone in self._notebook_zones:
            pane = self._zone_pane[zone]
            for p in list(pane.panes()):
                try:
                    w = pane.nametowidget(p); pane.forget(w); w.destroy()
                except Exception:
                    pass
        self._group_nb.clear()
        self._nb_arrows.clear()
        # _nb_info/_tab_vid: externe Eintraege behalten, interne neu aufbauen
        self._nb_info = {str(nb): (z, 0) for z, nb in self._ext_nb.items()}
        self._tab_vid = {}

    def _realize(self):
        self._clear_internal()
        self._zone_items = {}       # zone -> list[(collapsed_bool, weight)]
        self._zone_visible = {}     # zone -> hat mindestens eine sichtbare Gruppe
        for zone, fr in self._frame_zone.items():
            want_vids = tuple(self.model.views_in(zone))
            if self._realized_frame_vids.get(zone) != want_vids or not fr.winfo_children():
                for ch in list(fr.winfo_children()):
                    ch.destroy()
                for vid in want_vids:
                    self._make_widget(vid, fr, as_tab=False)
                self._realized_frame_vids[zone] = want_vids
        for zone in self._notebook_zones:
            pane = self._zone_pane[zone]
            items = []
            has_vis = False
            for g in self.model.groups_in(zone):
                vids = self.model.views_in(zone, group=g)
                if not vids:
                    continue
                if self.model.group_popped(zone, g):
                    self._ensure_popped_group(zone, g, vids)
                    continue
                if self.model.group_hidden(zone, g):
                    pane.add(self._make_restore_strip(pane, zone, g, vids), weight=0)
                    items.append((True, 1))
                    continue
                nb = ttk.Notebook(pane); pane.add(nb, weight=1)
                self._group_nb[(zone, g)] = nb
                self._nb_info[str(nb)] = (zone, g)
                for vid in vids:
                    w = self._make_widget(vid, nb, as_tab=True)
                    nb.add(w, text=self.model.views[vid].title)
                    self._tab_vid[str(w)] = vid
                self._bind_dnd(nb)
                self._add_chrome(nb, zone, g)
                items.append((False, 1))
                has_vis = True
            self._zone_items[zone] = items
            self._zone_visible[zone] = has_vis
        self._sync_external()
        self._rebuild_menus()
        try:
            self.outer.after_idle(self._reflow)
        except Exception:
            pass

    def _reflow(self, _tries=0):
        """Sash-Positionen nach Struktur-Aenderung (Ausblenden/Pop/DnD) neu setzen, damit
        eingeklappte Bereiche nur minimale Groesse behalten und die Nachbarn den Platz
        zurueckbekommen. Wird NUR hier aufgerufen, nicht bei jedem Resize, damit manuelles
        Ziehen der Sashes erhalten bleibt."""
        try:
            self.hpan.update_idletasks()
            W = self.hpan.winfo_width()
        except Exception:
            return
        if W <= 1:
            if _tries < 6:
                try:
                    self.outer.after(60, lambda: self._reflow(_tries + 1))
                except Exception:
                    pass
            return
        weights = {"left": 1, "editor": 3, "right": 2}
        zones = [z for z in ("left", "editor", "right") if z in self._zone_pane]
        hitems = [(not self._zone_visible.get(z, True), weights.get(z, 1)) for z in zones]
        self._reflow_h(self.hpan, hitems)
        for z in zones:
            items = self._zone_items.get(z, [])
            if len(items) >= 2:
                self._reflow_h(self._zone_pane[z], items)
        # vertikale Zone (panel/Console) unten: Platz freigeben, wenn eingeklappt
        if "panel" in self._zone_pane and getattr(self, "vpan", None) is not None:
            panel_coll = not self._zone_visible.get("panel", True)
            self._reflow_v(self.vpan, [(False, 4), (panel_coll, 1)])
            pitems = self._zone_items.get("panel", [])
            if len(pitems) >= 2:
                self._reflow_h(self._zone_pane["panel"], pitems)

    def _reflow_h(self, pane, items):
        """Ein horizontales PanedWindow neu aufteilen. items: [(collapsed, weight), ...] in
        Pane-Reihenfolge. Eingeklappte Kinder -> 26px, Rest nach Gewicht."""
        try:
            pane.update_idletasks()
            W = pane.winfo_width()
        except Exception:
            return
        n = len(items)
        if n < 2 or W <= 1:
            return
        COLL = 26
        active_w = sum(w for c, w in items if not c) or 1
        avail = max(0, W - COLL * sum(1 for c, _ in items if c))
        widths = [COLL if c else max(48, int(avail * w / active_w)) for c, w in items]
        x = 0
        for i in range(n - 1):
            x += widths[i]
            try:
                pane.sashpos(i, x)
            except Exception:
                pass

    def _reflow_v(self, pane, items):
        """Wie _reflow_h, aber vertikal (Hoehe). items: [(collapsed, weight), ...]."""
        try:
            pane.update_idletasks()
            H = pane.winfo_height()
        except Exception:
            return
        n = len(items)
        if n < 2 or H <= 1:
            return
        COLL = 28
        active_w = sum(w for c, w in items if not c) or 1
        avail = max(0, H - COLL * sum(1 for c, _ in items if c))
        heights = [COLL if c else max(60, int(avail * w / active_w)) for c, w in items]
        y = 0
        for i in range(n - 1):
            y += heights[i]
            try:
                pane.sashpos(i, y)
            except Exception:
                pass

    def _sync_external(self):
        """Eigene View-Tabs in externen Zonen (z. B. globale Console) abgleichen; app-eigene Tabs
        bleiben unberuehrt."""
        want = {}  # zone -> [vids]
        for zone in self._ext_nb:
            want[zone] = self.model.views_in(zone)
        # entfernen, was wegverschoben/ausgeblendet wurde
        for vid in list(self._ext_tab):
            p = self.model.placement.get(vid, {})
            if p.get("zone") not in self._ext_nb or not p.get("visible") or p.get("popped"):
                fr = self._ext_tab.pop(vid)
                nb = self._ext_nb.get(p.get("zone"))
                for cand in self._ext_nb.values():
                    try:
                        cand.forget(fr)
                    except Exception:
                        pass
                try:
                    fr.destroy()
                except Exception:
                    pass
        # hinzufuegen, was fehlt
        for zone, vids in want.items():
            nb = self._ext_nb[zone]
            for vid in vids:
                if vid not in self._ext_tab:
                    try:
                        fr = self._factories[vid](nb)
                        nb.add(fr, text=self.model.views[vid].title)
                        self._ext_tab[vid] = fr
                    except Exception:
                        continue
        # _tab_vid fuer unsere externen Tabs (re)registrieren
        for vid, fr in self._ext_tab.items():
            self._tab_vid[str(fr)] = vid

    def _make_widget(self, vid, container, as_tab):
        fac = self._factories.get(vid)
        try:
            w = fac(container)
        except Exception as e:                            # noqa
            w = ttk.Label(container, text="[View %s Fehler: %r]" % (vid, e))
        if not as_tab:
            w.pack(side="left", fill="x", padx=2)
        return w

    # ---- Menue-Operationen ------------------------------------------------
    def move_view(self, vid, zone, group=0):
        if zone == "panel" and not self.with_panel:
            zone = "right"
        if zone == "console" and "console" not in self._ext_nb:
            zone = "right"
        self.model.set_zone(vid, zone); self.model.set_group(vid, group)
        self.model.set_visible(vid, True); self.model.set_popped(vid, False)
        self._close_popup(vid); self._realize(); self._persist()

    def toggle_view(self, vid):
        self.model.toggle_visible(vid); self._realize(); self._persist()

    def pop_out(self, vid):
        self.model.set_popped(vid, True); self._realize()
        try:
            top = tk.Toplevel(self.outer); top.title(self.model.views[vid].title)
            self._factories[vid](top).pack(fill="both", expand=True)
            top.protocol("WM_DELETE_WINDOW", lambda v=vid: self.dock_back(v))
            self._popups[vid] = top
        except Exception:
            self.model.set_popped(vid, False); self._realize()

    def dock_back(self, vid):
        self.model.set_popped(vid, False); self._close_popup(vid); self._realize(); self._persist()

    def _close_popup(self, vid):
        top = self._popups.pop(vid, None)
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass

    # ---- Fenster-Chrome (Ausblenden / Abdocken / Scrollen) ----------------
    def _add_chrome(self, nb, zone, g):
        """Kleines Overlay rechts in der Tableiste: Scroll-Pfeile (bei Ueberlauf) + Ausblenden + Abdocken.
        Liegt per place() oben rechts und wird nicht von Tabs ueberdeckt (lift)."""
        ov = ttk.Frame(nb)
        prev = ttk.Label(ov, text="\u25c4", cursor="hand2")
        nxt = ttk.Label(ov, text="\u25ba", cursor="hand2")
        prev.bind("<Button-1>", lambda e, n=nb: self._tab_scroll(n, -1))
        nxt.bind("<Button-1>", lambda e, n=nb: self._tab_scroll(n, +1))
        hide = ttk.Label(ov, text="\u2012", cursor="hand2")      # Ausblenden
        pop = ttk.Label(ov, text="\u29c9", cursor="hand2")       # Abdocken (eigenes Fenster)
        hide.bind("<Button-1>", lambda e, z=zone, gg=g: self._hide_group(z, gg))
        pop.bind("<Button-1>", lambda e, z=zone, gg=g: self._pop_group(z, gg))
        for w in (prev, nxt, hide, pop):
            w.pack(side="left", padx=1)
        ov.place(relx=1.0, x=-2, y=1, anchor="ne")
        try:
            ov.lift()
        except Exception:
            pass
        self._nb_arrows[str(nb)] = (prev, nxt)
        nb.bind("<Configure>", lambda e, n=nb: self._update_arrows(n), add="+")
        self._update_arrows(nb)

    def _update_arrows(self, nb):
        pair = self._nb_arrows.get(str(nb))
        if not pair:
            return
        prev, nxt = pair
        try:
            overflow = nb.winfo_reqwidth() > max(nb.winfo_width(), 1) + 4
        except Exception:
            overflow = False
        for w in (prev, nxt):
            try:
                if overflow:
                    w.pack(side="left", padx=1)
                else:
                    w.pack_forget()
            except Exception:
                pass

    def _tab_scroll(self, nb, delta):
        try:
            tabs = nb.tabs()
            if not tabs:
                return
            cur = nb.index(nb.select())
            nb.select(tabs[(cur + delta) % len(tabs)])
        except Exception:
            pass

    def _hide_group(self, zone, g):
        self.model.set_group_hidden(zone, g, True); self._realize(); self._persist()

    def _show_group(self, zone, g):
        self.model.set_group_hidden(zone, g, False); self._realize(); self._persist()

    def _strip_bg(self):
        try:
            c = ttk.Style().lookup("TFrame", "background")
            if c:
                return c
        except Exception:
            pass
        return "#2b2b2b"

    def _strip_fg(self):
        try:
            c = ttk.Style().lookup("TLabel", "foreground")
            if c:
                return c
        except Exception:
            pass
        return "#dddddd"

    def _make_restore_strip(self, pane, zone, g, vids):
        """Einblend-Streifen fuer eine ausgeblendete Gruppe. Orientierung FOLGT der Teilung:
        horizontal geteilt (Gruppen nebeneinander) -> duenner VERTIKALER Streifen, damit die
        Nachbarn die Breite zurueckbekommen; vertikal geteilt (ganze Zeile) -> duenner
        HORIZONTALER Streifen. Beschriftung mittig, Kontur deutlich (klickbar = einblenden)."""
        names = [self.model.views[v].title for v in vids if v in self.model.views]
        full = ", ".join(names) or "Fenster"
        compact = (names[0] if names else "Fenster") + (f"  +{len(names)-1}" if len(names) > 1 else "")
        try:
            orient = str(pane.cget("orient"))
        except Exception:
            orient = "vertical"
        fg = self._strip_fg()
        # Die panel-Zone ist ein unteres, breites Band (in der vertikalen vpan): ein
        # eingeklappter Streifen dort ist breit-und-flach und braucht WAAGERECHTEN Text,
        # obwohl die panel-Pane ihre Gruppen (wie die Spalten) horizontal nebeneinander legt.
        if orient == "horizontal" and zone != "panel":
            # Spalten (links/editor/rechts) -> schmaler VERTIKALER Streifen (volle Hoehe).
            strip = ttk.Frame(pane, width=26, relief="solid", borderwidth=1)
            try:
                strip.pack_propagate(False)
            except Exception:
                pass
            cv = tk.Canvas(strip, width=24, highlightthickness=1, highlightbackground="#888",
                           cursor="hand2", bg=self._strip_bg())
            cv.pack(side="top", fill="both", expand=True)

            def _draw(e=None, c=cv, t=compact, color=fg):
                try:
                    c.delete("all")
                    h = c.winfo_height() or 200
                    # Titel vertikal + vertikal mittig, damit nichts ueberdeckt wird
                    c.create_text(13, h / 2, text=t, anchor="center", angle=270,
                                  fill=color, font=("Segoe UI", 8))
                    # Pfeil oben und unten als Hinweis "hier einblenden"
                    c.create_text(13, 9, text="\u25ba", anchor="n", fill=color,
                                  font=("Segoe UI", 10, "bold"))
                except Exception:
                    pass

            cv.bind("<Configure>", _draw)
            cv.bind("<Button-1>", lambda e, z=zone, gg=g: self._show_group(z, gg))
            _draw()
            return strip
        # Vertikal geteilt (ganze Zeile) -> horizontaler Streifen (schmal, volle Breite).
        strip = ttk.Frame(pane, relief="solid", borderwidth=1)
        btn = ttk.Label(strip, text="\u25ba  " + full, cursor="hand2", anchor="center", padding=2)
        btn.pack(fill="both", expand=True)
        btn.bind("<Button-1>", lambda e, z=zone, gg=g: self._show_group(z, gg))
        return strip

    def _pop_group(self, zone, g):
        self.model.set_group_popped(zone, g, True); self._realize(); self._persist()

    def _ensure_popped_group(self, zone, g, vids):
        key = (zone, g)
        top = self._group_tops.get(key)
        if top is not None:
            try:
                if top.winfo_exists():
                    return
            except Exception:
                pass
        try:
            top = tk.Toplevel(self.outer)
            top.title(", ".join(self.model.views[v].title for v in vids if v in self.model.views) or "Fenster")
            bar = ttk.Frame(top); bar.pack(side="top", fill="x")
            ttk.Button(bar, text="\u2913 Andocken", command=lambda z=zone, gg=g: self._dock_group(z, gg)).pack(side="right", padx=2, pady=1)
            nb = ttk.Notebook(top); nb.pack(side="top", fill="both", expand=True)
            self._nb_info[str(nb)] = (zone, g)
            for vid in vids:
                w = self._make_widget(vid, nb, as_tab=True)
                nb.add(w, text=self.model.views[vid].title)
                self._tab_vid[str(w)] = vid
            self._bind_dnd(nb)
            top.protocol("WM_DELETE_WINDOW", lambda z=zone, gg=g: self._dock_group(z, gg))
            top.geometry("520x360")
            self._group_tops[key] = top
        except Exception:
            self.model.set_group_popped(zone, g, False)

    def _dock_group(self, zone, g):
        key = (zone, g)
        top = self._group_tops.pop(key, None)
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass
        self.model.set_group_popped(zone, g, False); self._realize(); self._persist()

    # ---- Drag & Drop ------------------------------------------------------
    def _bind_dnd(self, nb):
        nb.bind("<ButtonPress-1>", self._dnd_press, add="+")
        nb.bind("<B1-Motion>", self._dnd_motion, add="+")
        nb.bind("<ButtonRelease-1>", self._dnd_release, add="+")

    def _dnd_press(self, e):
        nb = e.widget
        try:
            idx = nb.index("@%d,%d" % (e.x, e.y))
            tabs = nb.tabs()
        except Exception:
            self._drag = None; return
        if idx < 0 or idx >= len(tabs):
            self._drag = None; return
        vid = self._tab_vid.get(tabs[idx])   # None = app-eigener Tab (nicht verschiebbar)
        self._drag = {"vid": vid, "src": nb, "idx": idx, "x": e.x_root, "y": e.y_root, "active": False}

    def _dnd_motion(self, e):
        d = self._drag
        if not d or d.get("vid") is None:
            return
        if not d["active"] and (abs(e.x_root - d["x"]) + abs(e.y_root - d["y"])) > 18:
            d["active"] = True
            self._start_ghost(self.model.views[d["vid"]].title)
        if d["active"]:
            self._move_ghost(e.x_root, e.y_root)

    def _dnd_release(self, e):
        d = self._drag; self._drag = None
        self._kill_ghost()
        if not d or not d.get("active") or d.get("vid") is None:
            return
        vid = d["vid"]
        try:
            tgt = self.outer.winfo_containing(e.x_root, e.y_root)
        except Exception:
            tgt = None
        tnb = self._find_nb(tgt)
        if tnb is None:
            return
        zone, group = self._nb_info[str(tnb)]
        lx = e.x_root - tnb.winfo_rootx(); ly = e.y_root - tnb.winfo_rooty()
        on_tab = True
        try:
            tnb.index("@%d,%d" % (lx, ly))
        except Exception:
            on_tab = False
        # Gleiche Tableiste im selben Notebook -> kein Rebuild/Reset ausloesen!
        if tnb is d.get("src") and on_tab:
            return
        if zone in self._ext_nb:
            self.model.set_zone(vid, zone); self.model.set_group(vid, 0)
        elif on_tab:
            self.model.set_zone(vid, zone); self.model.set_group(vid, group)
        else:
            g = self.model.next_group(zone)
            self.model.set_zone(vid, zone); self.model.set_group(vid, g)
        self.model.set_visible(vid, True); self.model.set_popped(vid, False)
        self._realize(); self._persist()

    def _find_nb(self, widget):
        w = widget; guard = 0
        while w is not None and guard < 40:
            if str(w) in self._nb_info:
                return w
            try:
                w = w.nametowidget(w.winfo_parent()) if w.winfo_parent() else None
            except Exception:
                break
            guard += 1
        return None

    def _start_ghost(self, text):
        try:
            g = tk.Toplevel(self.outer); g.overrideredirect(True)
            try:
                g.attributes("-alpha", 0.85)
            except Exception:
                pass
            ttk.Label(g, text=text, relief="solid", padding=2).pack()
            self._ghost = g
        except Exception:
            self._ghost = None

    def _move_ghost(self, x, y):
        if self._ghost is not None:
            try:
                self._ghost.geometry("+%d+%d" % (x + 10, y + 10))
            except Exception:
                pass

    def _kill_ghost(self):
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except Exception:
                pass
        self._ghost = None

    # ---- Favoritenansichten -----------------------------------------------
    def save_layout(self, name=None):
        if name is None:
            try:
                name = simpledialog.askstring("Favoritenansicht", "Name des Layouts:")
            except Exception:
                name = None
        if not name:
            return
        self.model.save_preset(name); self._persist(); self._rebuild_menus()

    def apply_layout(self, name):
        if self.model.load_preset(name):
            self._realize(); self._persist()

    def reset_layout(self):
        self.model.reset_defaults(); self._realize(); self._persist()

    def _persist(self):
        try:
            self.model.save_json(self.layout_path)
        except Exception:
            pass

    # ---- Steuer-Menues ----------------------------------------------------
    def _build_ctrl_menus(self):
        self.mb_view = ttk.Menubutton(self.top_ctrl, text="Ansicht ▾")
        self.menu_view = tk.Menu(self.mb_view, tearoff=False); self.mb_view["menu"] = self.menu_view
        self.mb_view.pack(side="left", padx=2)
        self.mb_layout = ttk.Menubutton(self.top_ctrl, text="Layout ▾")
        self.menu_layout = tk.Menu(self.mb_layout, tearoff=False); self.mb_layout["menu"] = self.menu_layout
        self.mb_layout.pack(side="left", padx=2)

    def _rebuild_menus(self):
        m = self.menu_view; m.delete(0, "end")
        zone_choices = [z for z in ZONES if z != "panel" or self.with_panel]
        if "console" not in self._ext_nb:
            zone_choices = [z for z in zone_choices if z != "console"]
        for vid, spec in sorted(self.model.views.items(), key=lambda kv: kv[1].title):
            p = self.model.placement.get(vid, {})
            var = tk.BooleanVar(value=bool(p.get("visible")) and not p.get("popped"))
            m.add_checkbutton(label=spec.title, variable=var, command=lambda v=vid: self.toggle_view(v))
            sub = tk.Menu(m, tearoff=False)
            for z in zone_choices:
                sub.add_command(label=z, command=lambda v=vid, zz=z: self.move_view(v, zz))
            sub.add_separator()
            sub.add_command(label="eigenes Fenster", command=lambda v=vid: self.pop_out(v))
            m.add_cascade(label="    → %s" % spec.title, menu=sub)
        ml = self.menu_layout; ml.delete(0, "end")
        ml.add_command(label="Aktuelles als Favorit speichern…", command=lambda: self.save_layout())
        ml.add_command(label="Auf Standard zuruecksetzen", command=self.reset_layout)
        presets = self.model.list_presets()
        if presets:
            ml.add_separator()
            for name in presets:
                ml.add_command(label="Laden: %s" % name, command=lambda n=name: self.apply_layout(n))
