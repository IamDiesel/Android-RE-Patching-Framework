# -*- coding: utf-8 -*-
"""core.java.surface — Produzenten-Abstraktion fuer die Anzeige-Saeule (Skalierungsbaustein).

Entkoppelt WIE eine App ihre UI erzeugt (Produzent je Framework) von WIE wir sie zeichnen/bedienen
(Renderer/Input im Display-View). Konvergenzpunkt: die paint_commands-Liste. Open/Closed via
`ProducerRegistry` (wie das PipelineStep-Muster). Compute-Budget: dirty-getrieben - der View zeichnet
nur bei Aenderung neu (ViewTree.version bzw. Frame-Zaehler), nie pro CPU-Schritt.

Modi (aus dem Minimal-Client-Konzept):
  * SceneGraphSurface  — retained-mode: ein ViewTree (android.view / RN-gemountet / NativeScript).
  * DisplayListSurface — immediate-mode: fertige Zeichenbefehle + Hit-Regionen (Canvas-/Skia-Recorder).
Zielagnostisch."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.java.view_tree import paint_commands, DEFAULT_W, DEFAULT_H


class SurfaceSource(ABC):
    """Abstrakte Quelle einer renderbaren, bedienbaren Oberflaeche."""
    width = DEFAULT_W
    height = DEFAULT_H

    @abstractmethod
    def paint(self):
        """Zeichenbefehl-Liste im paint_commands-Format."""
        raise NotImplementedError

    @abstractmethod
    def hit(self, x, y):
        """Treffer (Knoten/Region-Handle) bei (x,y) oder None."""
        raise NotImplementedError

    def dispatch(self, event):
        """Tap/Key an die App zurueckspielen. event: {'type','x','y',...}. True wenn behandelt."""
        return False

    @property
    def dirty(self):
        """True, wenn seit dem letzten paint() eine Aenderung vorliegt (Compute-Budget)."""
        return True

    def clear_dirty(self):
        pass


class SceneGraphSurface(SurfaceSource):
    """Retained-mode: umhuellt einen `ViewTree`. Rueckwaertskompatibel zum heutigen Verhalten."""

    def __init__(self, tree, vm=None, activity=None):
        self.tree = tree
        self.vm = vm
        self.activity = activity
        self._painted_version = -1

    @property
    def width(self):
        return getattr(self.tree, "width", DEFAULT_W)

    @property
    def height(self):
        return getattr(self.tree, "height", DEFAULT_H)

    def paint(self):
        self._painted_version = getattr(self.tree, "version", 0)
        return paint_commands(self.tree)

    def hit(self, x, y):
        return self.tree.clickable_at(x, y) if self.tree is not None else None

    def dispatch(self, event):
        node = self.hit(event.get("x", 0), event.get("y", 0)) if event else None
        if node is None:
            return False
        stubs = getattr(self.vm, "_stubs_ref", None) if self.vm is not None else None
        if stubs is not None and hasattr(stubs, "_tick_ms"):
            stubs._tick_ms(1500)
        cb = getattr(node, "on_click", None)
        if callable(cb):
            cb(node)
            return True
        if cb is not None and self.vm is not None:
            try:
                from core.java.objects import JObject
                view = node._jobj if isinstance(getattr(node, "_jobj", None), JObject) else JObject("Landroid/view/View;", {"$view": node})
                if hasattr(self.vm, "invoke_virtual"):
                    self.vm.invoke_virtual(cb, "onClick", "(Landroid/view/View;)V", (cb, view))
                else:
                    self.vm.invoke(cb.cls, "onClick", "(Landroid/view/View;)V", (cb, view))
                return True
            except Exception:
                return False
        if self.vm is not None and self.activity is not None:
            try:
                self.vm.invoke(self.activity.cls, "onClick", "(Landroid/view/View;)V",
                               (self.activity, None))
                return True
            except Exception:
                return False
        return False

    @property
    def dirty(self):
        return getattr(self.tree, "version", 0) != self._painted_version

    def clear_dirty(self):
        self._painted_version = getattr(self.tree, "version", 0)


class DisplayListSurface(SurfaceSource):
    """Immediate-mode: fertige paint_commands-Liste + Hit-Regionen. Vom Canvas-/Skia-Recorder."""

    def __init__(self, cmds=None, hit_regions=None, width=DEFAULT_W, height=DEFAULT_H):
        self._cmds = list(cmds or [])
        self._regions = list(hit_regions or [])   # [{'x','y','w','h','on'(callable)|'handle'}]
        self.width = width
        self.height = height
        self._frame = 0
        self._painted_frame = -1

    def paint(self):
        self._painted_frame = self._frame
        return list(self._cmds)

    def hit(self, x, y):
        for r in reversed(self._regions):          # oberste Region zuerst
            if r["x"] <= x < r["x"] + r["w"] and r["y"] <= y < r["y"] + r["h"]:
                return r
        return None

    def set_frame(self, cmds, hit_regions=None):
        """Neuer Frame (ein Flush je Frame, nicht je Zeichenbefehl)."""
        self._cmds = list(cmds or [])
        if hit_regions is not None:
            self._regions = list(hit_regions)
        self._frame += 1

    def dispatch(self, event):
        r = self.hit(event.get("x", 0), event.get("y", 0)) if event else None
        if r is None:
            return False
        cb = r.get("on")
        if callable(cb):
            cb(event)
            return True
        return False

    @property
    def dirty(self):
        return self._frame != self._painted_frame

    def clear_dirty(self):
        self._painted_frame = self._frame


class ProducerRegistry:
    """Open/Closed: Framework-Runtime -> Produzenten-Factory (wie PipelineStep). INSTANZBASIERT, damit
    Tests eine eigene Registry nutzen koennen (kein globaler Zustand). Die Modul-globale Default-Instanz
    `producers` zieht ihre Standard-Produzenten beim ersten producer_for()/known() LAZY + IDEMPOTENT
    ueber core.java.producer_wiring.ensure_default_producers - statt frueherer Import-Seiteneffekte,
    deren Reihenfolge den aufgeloesten Produzenten bestimmte."""

    def __init__(self, ensure_fn=None):
        self._factories = {}
        self._default = None
        self._ensured = False
        self._ensure_fn = ensure_fn      # nur die globale Instanz traegt den Lazy-Default-Hook

    def _ensure(self):
        if self._ensured or self._ensure_fn is None:
            return
        self._ensured = True             # vor dem Aufruf -> idempotent, reentrancy-sicher
        try:
            self._ensure_fn(self)
        except Exception:
            pass

    def register(self, runtime_name, factory):
        self._factories[(runtime_name or "").lower()] = factory
        return self

    def set_default(self, factory):
        self._default = factory
        return self

    def producer_for(self, runtime_name):
        """Factory fuer den erkannten Runtime-Namen (oder Default)."""
        self._ensure()
        return self._factories.get((runtime_name or "").lower(), self._default)

    def known(self):
        self._ensure()
        return sorted(self._factories.keys())


def _default_ensure(reg):
    from core.java.producer_wiring import ensure_default_producers
    ensure_default_producers(reg)


# Modul-globale Default-Registry: EINE Instanz, kein Klassenzustand, keine Import-Seiteneffekte.
producers = ProducerRegistry(ensure_fn=_default_ensure)


# ---- Gläserne Interaktion: eine Logik fuer GUI UND MCP (Parität) ----
def snapshot(surface):
    """Gläserner Lesezugriff auf eine Surface: Paint-Liste, Masse, Texte, (Szenegraph-)Knoten."""
    if surface is None:
        return {"width": 0, "height": 0, "paint": [], "text": [], "nodes": []}
    paint = surface.paint()
    texts = [c.get("text") for c in paint if c.get("op") == "text" and c.get("text")]
    out = {"width": surface.width, "height": surface.height, "paint": paint, "text": texts}
    tree = getattr(surface, "tree", None)
    if tree is not None and getattr(tree, "root", None) is not None:
        out["nodes"] = [{"cls": n.cls, "text": n.text, "vid": n.vid,
                         "bounds": list(n.bounds), "clickable": bool(n.clickable),
                         "visible": bool(n.visible)} for n in tree.nodes()]
    else:
        out["nodes"] = []
    return out


def tap(surface, x, y):
    """Tap an (x,y) an die Surface zurueckspielen. Rueckgabe: {'handled': bool}."""
    if surface is None:
        return {"handled": False}
    return {"handled": bool(surface.dispatch({"type": "click", "x": int(x), "y": int(y)}))}


def key(surface, text):
    """Text in ein fokussiertes Eingabefeld der Surface tippen (sofern unterstuetzt)."""
    if surface is None:
        return {"handled": False}
    return {"handled": bool(surface.dispatch({"type": "key", "text": str(text)}))}
