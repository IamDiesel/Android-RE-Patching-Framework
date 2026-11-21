# -*- coding: utf-8 -*-
"""ui.dock.layout_model — reiner Zustand des Dock-Layouts (KEIN tkinter).

Haelt registrierte Views/Commands und ihre Platzierung (Zone, sichtbar, Reihenfolge, herausgeloest)
sowie benannte Presets (Favoritenansichten). Serialisiert nach/aus JSON. Headless testbar; der
`DockManager` (tk) spiegelt dieses Modell in echte Widgets.
"""
from __future__ import annotations

import json
import os

ZONES = ("top", "left", "editor", "right", "panel", "console", "status")
VISIBILITIES = ("S", "D+", "D-", "P")   # Standard / dockbar sichtbar / dockbar aus / Popup


class ViewSpec:
    """Beschreibt eine registrierte View (ohne Widget)."""

    __slots__ = ("id", "title", "default_zone", "visibility")

    def __init__(self, id, title, default_zone, visibility="D+"):
        if default_zone not in ZONES:
            raise ValueError("unbekannte Zone: %r" % (default_zone,))
        if visibility not in VISIBILITIES:
            raise ValueError("unbekannte Sichtbarkeit: %r" % (visibility,))
        self.id = id
        self.title = title
        self.default_zone = default_zone
        self.visibility = visibility


class LayoutModel:
    def __init__(self):
        self.views = {}       # id -> ViewSpec
        self.commands = {}    # id -> {"label":..., "icon":...}
        self.placement = {}   # id -> {"zone", "visible", "order", "popped"}
        self.presets = {}     # name -> placement-Snapshot (dict)
        self.group_state = {} # 'zone/group' -> {'hidden':bool, 'popped':bool}
        self._order_seq = 0

    # ---- Registrierung ----------------------------------------------------
    def register_view(self, id, title, default_zone, visibility="D+"):
        spec = ViewSpec(id, title, default_zone, visibility)
        self.views[id] = spec
        if id not in self.placement:
            self.placement[id] = {
                "zone": default_zone,
                "group": 0,
                "visible": visibility in ("S", "D+"),
                "order": self._next_order(),
                "popped": False,
            }
        return spec

    def unregister_view(self, id):
        self.views.pop(id, None)
        self.placement.pop(id, None)

    def register_command(self, id, label, icon=None):
        self.commands[id] = {"label": label, "icon": icon}

    def unregister_command(self, id):
        self.commands.pop(id, None)

    def _next_order(self):
        self._order_seq += 1
        return self._order_seq

    # ---- Platzierung ------------------------------------------------------
    def set_zone(self, id, zone):
        if zone not in ZONES:
            raise ValueError("unbekannte Zone: %r" % (zone,))
        self._ensure(id)["zone"] = zone

    def set_group(self, id, group):
        self._ensure(id)["group"] = int(group)

    def groups_in(self, zone, visible_only=True):
        """Sortierte, belegte Gruppen-Indizes einer Zone (fuer Splits)."""
        gs = set()
        for vid, p in self.placement.items():
            if p.get("zone") != zone or p.get("popped"):
                continue
            if visible_only and not p.get("visible"):
                continue
            gs.add(int(p.get("group", 0)))
        return sorted(gs) if gs else [0]

    def next_group(self, zone):
        used = [int(p.get("group", 0)) for p in self.placement.values() if p.get("zone") == zone]
        return (max(used) + 1) if used else 0

    # ---- Fenster-Zustand pro Gruppe (ausgeblendet/abgedockt) --------------
    def _gk(self, zone, group):
        return "%s/%d" % (zone, int(group))

    def group_hidden(self, zone, group):
        return bool(self.group_state.get(self._gk(zone, group), {}).get("hidden"))

    def group_popped(self, zone, group):
        return bool(self.group_state.get(self._gk(zone, group), {}).get("popped"))

    def set_group_hidden(self, zone, group, val):
        self.group_state.setdefault(self._gk(zone, group), {})["hidden"] = bool(val)

    def set_group_popped(self, zone, group, val):
        self.group_state.setdefault(self._gk(zone, group), {})["popped"] = bool(val)

    def set_visible(self, id, visible):
        self._ensure(id)["visible"] = bool(visible)

    def toggle_visible(self, id):
        p = self._ensure(id)
        p["visible"] = not p["visible"]
        return p["visible"]

    def set_popped(self, id, popped):
        self._ensure(id)["popped"] = bool(popped)

    def set_order(self, id, order):
        self._ensure(id)["order"] = int(order)

    def _ensure(self, id):
        if id not in self.placement:
            self.placement[id] = {"zone": "panel", "group": 0, "visible": True,
                                  "order": self._next_order(), "popped": False}
        return self.placement[id]

    def views_in(self, zone, group=None, visible_only=True, include_popped=False):
        """IDs in einer Zone (optional nur einer Gruppe), nach Reihenfolge sortiert."""
        out = []
        for vid, p in self.placement.items():
            if p["zone"] != zone:
                continue
            if group is not None and int(p.get("group", 0)) != int(group):
                continue
            if visible_only and not p["visible"]:
                continue
            if not include_popped and p["popped"]:
                continue
            out.append(vid)
        out.sort(key=lambda v: self.placement[v]["order"])
        return out

    # ---- Presets (Favoritenansichten) -------------------------------------
    def save_preset(self, name):
        self.presets[name] = {vid: dict(p) for vid, p in self.placement.items()}

    def load_preset(self, name):
        snap = self.presets.get(name)
        if snap is None:
            return False
        for vid, p in snap.items():
            if vid in self.views or vid in self.placement:
                self.placement[vid] = dict(p)
        return True

    def delete_preset(self, name):
        return self.presets.pop(name, None) is not None

    def list_presets(self):
        return sorted(self.presets.keys())

    def reset_defaults(self):
        for vid, spec in self.views.items():
            self.placement[vid] = {
                "zone": spec.default_zone,
                "group": 0,
                "visible": spec.visibility in ("S", "D+"),
                "order": self._next_order(),
                "popped": False,
            }

    # ---- Serialisierung ---------------------------------------------------
    def to_dict(self):
        return {"placement": {vid: dict(p) for vid, p in self.placement.items()},
                "group_state": {k: dict(v) for k, v in self.group_state.items()},
                "presets": {n: {vid: dict(p) for vid, p in snap.items()}
                            for n, snap in self.presets.items()}}

    def from_dict(self, d):
        for vid, p in (d.get("placement") or {}).items():
            self.placement[vid] = dict(p)
        for k, v in (d.get("group_state") or {}).items():
            self.group_state[k] = dict(v)
        for n, snap in (d.get("presets") or {}).items():
            self.presets[n] = {vid: dict(p) for vid, p in snap.items()}

    def save_json(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)

    def load_json(self, path):
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8") as f:
            self.from_dict(json.load(f))
        return True
