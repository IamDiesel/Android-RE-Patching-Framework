# -*- coding: utf-8 -*-
"""core.java.debug — gläserner Java-Debugger für die DalvikVM (X1 / MS-6, „Proben").

Thread-basiert: die DalvikVM läuft in einem Worker-Thread und pausiert über den `on_step`-Hook an
Breakpoints oder im Single-Step. Der Controller (Haupt-Thread) inspiziert/mutiert den Zustand und
treibt weiter. Voller **Per-Step-Replay**: jede ausgeführte Instruktion wird mit Registerstand
aufgezeichnet; `goto(n)` springt zu Schritt n (deterministische Re-Exec von vorn). Zielagnostisch.

Baut auf der bestehenden nativen Debug-Infra auf (die `DebugSession` in `core.java.debug_unified`
vereint diesen Java-Debugger mit `native_trace_service`/`arm64mini` über die J0-Grenze).
"""
from __future__ import annotations

import ast
import operator as _op
import threading
import time

from core.java.objects import JString, JObject, JArray


class DebugStop(Exception):
    """Signal: der Debugger wurde gestoppt (bricht den VM-Lauf im Worker-Thread ab)."""


def _fmt(v, _depth=0, _seen=None):
    if isinstance(v, JString):
        return {"t": "String", "v": v.text}
    if isinstance(v, JObject):
        if _seen is None:
            _seen = set()
        oid = id(v)
        if oid in _seen or _depth >= 3:        # Zyklenschutz + Tiefenlimit (kein unbegrenzter Graph)
            return {"t": "Object", "cls": v.cls, "truncated": True}
        _seen = _seen | {oid}
        return {"t": "Object", "cls": v.cls,
                "fields": {k: _fmt(x, _depth + 1, _seen) for k, x in v.fields.items()}}
    if isinstance(v, JArray):
        return {"t": "Array", "eltype": v.eltype, "len": len(v.data)}
    if v is None:
        return None
    return v


_BINOPS = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv,
           ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod, ast.LShift: _op.lshift,
           ast.RShift: _op.rshift, ast.BitAnd: _op.and_, ast.BitOr: _op.or_, ast.BitXor: _op.xor,
           ast.Pow: _op.pow}
_CMP = {ast.Eq: _op.eq, ast.NotEq: _op.ne, ast.Lt: _op.lt, ast.LtE: _op.le, ast.Gt: _op.gt,
        ast.GtE: _op.ge}
_UNARY = {ast.USub: _op.neg, ast.UAdd: _op.pos, ast.Invert: _op.invert, ast.Not: _op.not_}


def safe_eval(expr, names):
    """Sicherer Ausdruck-Evaluator fuer Breakpoint-Bedingungen / Watch-Ausdruecke.
    Erlaubt nur Namen (aus `names`), Literale, Vergleiche, Bool-/Arithmetik-/Bitoperatoren.
    Keine Calls/Attribute/Subscripts/Comprehensions -> kein Codepfad in beliebige Objekte."""
    node = ast.parse(expr, mode="eval").body

    def ev(n):
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                r = True
                for x in n.values:
                    r = r and ev(x)
                return r
            r = False
            for x in n.values:
                r = r or ev(x)
            return r
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            return _BINOPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY:
            return _UNARY[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            ok = True
            for opnode, comp in zip(n.ops, n.comparators):
                if type(opnode) not in _CMP:
                    raise ValueError("Vergleichsoperator nicht erlaubt")
                right = ev(comp)
                ok = ok and _CMP[type(opnode)](left, right)
                left = right
            return ok
        if isinstance(n, ast.Name):
            if n.id in names:
                return names[n.id]
            raise ValueError("unbekannter Name: %s" % n.id)
        if isinstance(n, ast.Constant):
            return n.value
        raise ValueError("Ausdruck nicht erlaubt: %s" % type(n).__name__)

    return ev(node)


_OBJ_CHILD_CAP = 64


def _preview(txt, n=60):
    t = str(txt).replace("\n", "\\n")
    return t if len(t) <= n else t[:n] + "\u2026"


def build_object_node(label, value, max_depth=4, _depth=0, _seen=None):
    """Bounded, zyklensicherer Objektbaum-Knoten fuer den Objekt-Inspektor.
    Knoten: {label, type, preview, children[], truncated?}. JString/JArray/JObject/Primitive/None."""
    if _seen is None:
        _seen = frozenset()
    if isinstance(value, JString):
        return {"label": label, "type": "String", "preview": _preview(value.text or ""), "children": []}
    if isinstance(value, JArray):
        n = len(value.data)
        node = {"label": label, "type": "%s[%d]" % (value.eltype, n), "preview": "len=%d" % n, "children": []}
        oid = id(value)
        if oid in _seen:
            node["preview"] = "<cycle>"; return node
        if _depth >= max_depth:
            node["truncated"] = True; return node
        seen2 = _seen | {oid}
        for i, el in enumerate(value.data[:_OBJ_CHILD_CAP]):
            node["children"].append(build_object_node("[%d]" % i, el, max_depth, _depth + 1, seen2))
        if n > _OBJ_CHILD_CAP:
            node["children"].append({"label": "\u2026", "type": "", "preview": "+%d weitere" % (n - _OBJ_CHILD_CAP), "children": []})
        return node
    if isinstance(value, JObject):
        node = {"label": label, "type": value.cls, "preview": "{%d Felder}" % len(value.fields), "children": []}
        oid = id(value)
        if oid in _seen:
            node["preview"] = "<cycle>"; return node
        if _depth >= max_depth:
            node["truncated"] = True; return node
        seen2 = _seen | {oid}
        for k, x in value.fields.items():
            node["children"].append(build_object_node(str(k), x, max_depth, _depth + 1, seen2))
        return node
    if value is None:
        return {"label": label, "type": "null", "preview": "null", "children": []}
    return {"label": label, "type": type(value).__name__, "preview": _preview(repr(value)), "children": []}


class JavaDebugger:
    """Interaktiver Debugger über einer `DalvikVM`. Immer EINE Methode pro Sitzung (mit Callees)."""

    def __init__(self, vm, max_steps=2_000_000, timeline_cap=500_000):
        self.vm = vm
        self.breakpoints = set()          # (cls, name, sig, pc|None)
        self._bp_meta = {}                 # key(tuple) -> {cond,hit,one_shot,hits}
        self.timeline = []                # je Schritt: dict(step,cls,name,sig,pc,op,depth,regs)
        self.step_count = 0               # UNABHAENGIGER Schrittzaehler (max_steps/pause_at_step/markers)
        self.record_timeline = False      # AP1.1: Time-Travel/Reverse AUS per Default (Performance)
        self.record_regs = False          # AP1.1: Register-Kopie je Schritt nur bei Bedarf
        self.mode = "idle"                # idle|run|step|paused|done|error|stopped
        self.max_steps = int(max_steps)
        self.timeline_cap = int(timeline_cap)
        self.current = None               # bei paused: dict(fr,insns,ins,op,idx)
        self.result = None
        self.error = None
        self.pause_at_step = None
        self.cur_depth = 0                 # Tiefe an der Haltestelle (fuer over/out)
        self._step_ref_depth = None        # Referenztiefe fuer step_over/step_out
        self._resume = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._entry = None                # (cls,name,sig,args)
        self._statics_snap = None
        self._since_yield = 0
        self.action_points = []           # regelbasierte Manipulation (bedingt/funktional/getriggert)
        self.markers = []                 # Zeitachsen-Marker {step,kind,label} (net/manip)
        self._run_fn = None               # Treiber des Laufs (None = Einzel-invoke aus _entry)
        self._pause_requested = False
        self.stop_on_exception = False
        self.exception_unhandled_only = True
        self.last_exception = None
        self.pause_reason = None
        self.flight_recorder_enabled = False
        self.flight_path = "data/glass_flight.jsonl"
        self.pause_on_budget = False
        self._budget_chunk = int(max_steps)
        self._budget_limit = int(max_steps)
        self._last_callstack = []
        self._last_frame_info = {}
        self._last_logic_info = {}
        self._last_fr = None
        self._last_raw_frames = []
        self._last_frames_len = 0

    def set_budget(self, chunk_steps, pause_on_budget=True):
        """Konfiguriert das Schritt-Budget je Lauf/Weiter-Klick."""
        c = max(1, int(chunk_steps))
        self.max_steps = c
        self._budget_chunk = c
        self._budget_limit = self.step_count + c
        self.pause_on_budget = bool(pause_on_budget)

    # ---- Breakpoints -------------------------------------------------------
    def add_breakpoint(self, cls, name, sig, pc=None, cond=None, hit=None, one_shot=False):
        """BP setzen. cond=Ausdruck (Register vN/pN), hit=Hit-Count (ab N-tem Treffer),
        one_shot=nach erstem Feuern entfernen."""
        key = (cls, name, sig, pc)
        self.breakpoints.add(key)
        meta = {}
        if cond:
            meta["cond"] = str(cond)
        if hit:
            try:
                meta["hit"] = int(hit)
            except Exception:
                pass
        if one_shot:
            meta["one_shot"] = True
        if meta:
            meta["hits"] = 0
            self._bp_meta[key] = meta
        else:
            self._bp_meta.pop(key, None)

    def clear_breakpoints(self):
        self.breakpoints.clear()
        self._bp_meta.clear()

    # ---- Aktionspunkte (bedingte/funktionale/getriggerte Manipulation) ----
    def add_action_point(self, cls, name, sig, pc=None, assigns=None, cond=None,
                         when_continue=False, one_shot=False, hit=None):
        """Regelbasierte Manipulation an (cls,name,sig,pc).
        assigns: Liste {target, value, const}. target in vN|pN|<vReg>.<feld>|<Cls;>.<feld>.
                 const=True -> value woertlich; sonst value = Ausdruck (safe_eval ueber vN/pN).
        cond: Ausdruck (safe_eval) oder None. when_continue=True -> ohne Halt weiterlaufen.
        one_shot/hit: wie bei Breakpoints."""
        ap = {"cls": cls, "name": name, "sig": sig, "pc": pc,
              "assigns": list(assigns or []), "cond": (str(cond) if cond else None),
              "when_continue": bool(when_continue), "one_shot": bool(one_shot),
              "hit": (int(hit) if hit else None), "hits": 0}
        self.action_points.append(ap)
        return ap

    def clear_action_points(self):
        self.action_points = []

    def _write_target(self, target, val, fr):
        """Schreibt val in ein Ziel: vN (Register), pN (Parameter), <vReg>.<feld> (Instanzfeld),
        <Lcls;>.<feld> (Static). Unbekannt/ungueltig -> ignoriert (sicher)."""
        t = str(target).strip()
        try:
            if t and t[0] == "v" and t[1:].isdigit():
                fr.v[int(t[1:])] = val
                return True
            if t and t[0] == "p" and t[1:].isdigit():
                md = getattr(fr, "md", None)
                nregs = getattr(md, "nregs", len(fr.v)); nargs = getattr(md, "nargs", 0)
                base = max(nregs - nargs, 0)
                fr.v[base + int(t[1:])] = val
                return True
            if "." in t:
                left, field = t.rsplit(".", 1)
                if left and left[0] == "v" and left[1:].isdigit():
                    obj = fr.v[int(left[1:])]
                    if isinstance(obj, JObject):
                        obj.fields[field] = val
                        return True
                else:
                    self.vm.statics.setdefault(left, {})[field] = val
                    return True
        except Exception:
            pass
        return False

    def _apply_action_points(self, cls, name, sig, idx, fr):
        """Prueft+feuert Aktionspunkte am aktuellen Schritt. Rueckgabe: True wenn pausiert werden soll."""
        pause = False
        remove = []
        names = None
        for ap in list(self.action_points):
            if not (ap["cls"] == cls and ap["name"] == name and ap["sig"] == sig
                    and (ap["pc"] is None or ap["pc"] == idx)):
                continue
            if names is None:
                names = self._frame_names(fr)
            cond = ap.get("cond")
            if cond:
                try:
                    if not bool(safe_eval(cond, names)):
                        continue
                except Exception:
                    continue
            ap["hits"] = ap.get("hits", 0) + 1
            ht = ap.get("hit")
            if ht and ap["hits"] < int(ht):
                continue
            for a in ap.get("assigns", []):
                try:
                    val = a["value"] if a.get("const") else safe_eval(str(a["value"]), names)
                except Exception:
                    continue
                if self._write_target(a.get("target"), val, fr):
                    names = self._frame_names(fr)   # Folgeaktionen sehen die Aenderung
                    self.markers.append({"step": self.step_count, "kind": "manip",
                                         "label": str(a.get("target")) + " := " + str(_fmt(val))})
                    try:
                        from core.application.event_bus import EventBus
                        EventBus.publish("MANIPULATION_APPLIED",
                                         {"cls": cls, "name": name, "pc": idx,
                                          "target": a.get("target"), "value": _fmt(val)})
                    except Exception:
                        pass
            if ap.get("one_shot"):
                remove.append(ap)
            if not ap.get("when_continue"):
                pause = True
        for ap in remove:
            try:
                self.action_points.remove(ap)
            except ValueError:
                pass
        return pause

    # ---- Hook aus der DalvikVM --------------------------------------------
    def on_step(self, vm, fr, insns, ins, op, idx):
        if self.mode == "stopped":
            raise DebugStop()
        self._since_yield += 1
        if self._since_yield >= 2048:
            self._since_yield = 0
            time.sleep(0)            # GIL kurz freigeben -> GUI bleibt waehrend langer Laeufe bedienbar
        limit = self._budget_limit if self.pause_on_budget else self.max_steps
        budget_hit = (self.step_count >= limit)
        if budget_hit and not self.pause_on_budget:
            raise DebugStop()
        md = fr.md
        cls = md.cls if md else None
        name = md.name if md else None
        sig = md.sig if md else None
        if md is not None:
            self._last_frame_info = {
                "cls": cls, "name": name, "sig": sig,
                "pc": idx, "op": op, "depth": vm._depth,
            }
            if (cls and not cls.startswith(("Landroid/", "Landroidx/", "Ljava/", "Lkotlin/", "Lkotlinx/"))
                    and "$$ExternalSyntheticLambda" not in cls):
                self._last_logic_info = dict(self._last_frame_info)
        self._last_fr = fr
        frames_ref = getattr(vm, "_frames", None) or ()
        nfl = len(frames_ref)
        if nfl and (nfl != self._last_frames_len or op.startswith(("invoke", "return", "throw"))):
            self._last_raw_frames = [
                (f, idx if pos == nfl - 1 else getattr(f, "cur", getattr(f, "pc", 0)))
                for pos, f in enumerate(frames_ref)
            ]
            self._last_frames_len = nfl
        sc = self.step_count                       # 0-basierter Index DIESER Instruktion
        # AP1.1 Fast-Path: reiner Lauf ohne Aufzeichnung/Haltepunkte/Pause-Anforderung -> nur Zaehler/Budget/Yield.
        if (self.mode == "run" and not self.breakpoints and not self.action_points
                and self.pause_at_step is None and not self.record_timeline
                and not self._pause_requested and not self.flight_recorder_enabled
                and not budget_hit):
            self.step_count = sc + 1
            return
        if self.record_timeline and len(self.timeline) < self.timeline_cap:
            entry = {"step": sc, "cls": cls, "name": name, "sig": sig,
                     "pc": idx, "op": op, "depth": vm._depth}
            if self.record_regs:
                entry["regs"] = [_fmt(x) for x in fr.v]
            self.timeline.append(entry)
        if self.flight_recorder_enabled and (sc % 100 == 0 or op.startswith("invoke")):
            self.flight_log("step", {"cls": cls, "name": name, "pc": idx, "op": op})
        self.cur_depth = vm._depth
        hit = self._bp_hit(cls, name, sig, idx, fr)
        ap_pause = self._apply_action_points(cls, name, sig, idx, fr) if self.action_points else False
        at_target = self.pause_at_step is not None and sc >= self.pause_at_step
        req_pause = bool(self._pause_requested)
        if req_pause:
            self._pause_requested = False
        want_step = False
        if self.mode == "step":
            want_step = True                       # Step Into: naechste Instruktion, egal welche Tiefe
        elif self.mode == "over":
            want_step = self._step_ref_depth is None or vm._depth <= self._step_ref_depth
        elif self.mode == "out":
            want_step = self._step_ref_depth is not None and vm._depth < self._step_ref_depth
        if want_step or hit or at_target or ap_pause or req_pause or budget_hit:
            if hit:
                self.pause_reason = "breakpoint"
            elif ap_pause:
                self.pause_reason = "action_point"
            elif budget_hit:
                self.pause_reason = "budget"
            elif req_pause:
                self.pause_reason = "pause"
            elif at_target and sc == 0:
                self.pause_reason = "entry"
            else:
                self.pause_reason = "step"
            self._pause(fr, insns, ins, op, idx)    # blockiert; state["step"] == sc (aktueller Index)
        self.step_count = sc + 1                     # nach (evtl.) Pause: naechster Index

    def _bp_hit(self, cls, name, sig, idx, fr):
        """Prueft alle passenden Breakpoints inkl. Bedingung / Hit-Count / One-Shot."""
        fired = False
        remove = []
        names = None
        for b in list(self.breakpoints):
            if not (b[0] == cls and b[1] == name and b[2] == sig and (b[3] is None or b[3] == idx)):
                continue
            meta = self._bp_meta.get(b)
            if not meta:
                fired = True
                continue
            cond = meta.get("cond")
            if cond:
                if names is None:
                    names = self._frame_names(fr)
                try:
                    if not bool(safe_eval(cond, names)):
                        continue
                except Exception:
                    continue
            meta["hits"] = meta.get("hits", 0) + 1
            ht = meta.get("hit")
            if ht and meta["hits"] < int(ht):
                continue
            fired = True
            if meta.get("one_shot"):
                remove.append(b)
        for b in remove:
            self.breakpoints.discard(b)
            self._bp_meta.pop(b, None)
        return fired

    def _scalar(self, v):
        return v.text if isinstance(v, JString) else v

    def _frame_names(self, fr):
        """Namensraum fuer Bedingungen/Watch: vN = Register N, pN = Parameter N."""
        names = {}
        try:
            for i, val in enumerate(fr.v):
                names["v%d" % i] = self._scalar(val)
            md = getattr(fr, "md", None)
            if md is not None:
                nregs = getattr(md, "nregs", len(fr.v))
                nargs = getattr(md, "nargs", 0)
                base = max(nregs - nargs, 0)
                for j in range(nargs):
                    if base + j < len(fr.v):
                        names["p%d" % j] = self._scalar(fr.v[base + j])
        except Exception:
            pass
        return names

    def eval_in_frame(self, expr):
        """Ausdruck im aktuellen Haltepunkt-Frame auswerten (Watch). Fehler -> Fehlerstring."""
        cur = self.current
        if not (self.mode == "paused" and cur and cur.get("fr")):
            return None
        try:
            return safe_eval(str(expr), self._frame_names(cur["fr"]))
        except Exception as e:
            return "<%s>" % e

    def _pause(self, fr, insns, ins, op, idx):
        self.current = {"fr": fr, "insns": insns, "ins": ins, "op": op, "idx": idx}
        self.mode = "paused"
        try:
            self._last_callstack = self._build_live_callstack()
        except Exception:
            pass
        if self.flight_recorder_enabled:
            md = getattr(fr, "md", None)
            self.flight_log("paused", {
                "reason": self.pause_reason,
                "cls": getattr(md, "cls", None),
                "name": getattr(md, "name", None),
                "pc": idx, "op": op,
            })
        self._resume.clear()
        self._paused.set()
        self._resume.wait()

    def pause(self):
        """Fordert einen sauberen Pause-Halt an der naechsten Dalvik-Instruktion an."""
        if self.mode in ("run", "step", "over", "out"):
            self._pause_requested = True
        return self.state()

    def on_exception(self, vm, fr, insns, idx, exc_obj, handled=False):
        """Exception-Trap fuer VM-Exceptions (handled & unhandled)."""
        exc_cls = exc_obj.cls if isinstance(exc_obj, JObject) else "Ljava/lang/Throwable;"
        md = getattr(fr, "md", None)
        info = {
            "cls": exc_cls,
            "msg": str(exc_obj),
            "handled": bool(handled),
            "method_cls": getattr(md, "cls", None),
            "method": getattr(md, "name", None),
            "pc": idx,
        }
        self.last_exception = info
        if self.flight_recorder_enabled:
            self.flight_log("exception", info)
        if self.stop_on_exception and (not self.exception_unhandled_only or not handled):
            ins = insns[idx] if insns and 0 <= idx < len(insns) else None
            self.pause_reason = "exception"
            self._pause(fr, insns, ins, "exception", idx)

    def flight_log(self, event_type, payload=None):
        """Korrelierter JSONL-Flight-Recorder nach data/glass_flight.jsonl."""
        if not self.flight_recorder_enabled:
            return
        import os as _os, json as _json
        try:
            d = _os.path.dirname(self.flight_path)
            if d:
                _os.makedirs(d, exist_ok=True)
            rec = {
                "ts": round(time.time(), 4),
                "step": int(self.step_count),
                "event": str(event_type),
                "data": payload or {},
            }
            with open(self.flight_path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _build_live_callstack(self):
        frames = list(getattr(self.vm, "_frames", []) or [])
        out = []
        for pos, fr in enumerate(reversed(frames)):
            md = getattr(fr, "md", None)
            pc = (self.current.get("idx") if (pos == 0 and self.current)
                  else getattr(fr, "cur", getattr(fr, "pc", 0)))
            out.append({
                "index": pos,
                "cls": getattr(md, "cls", None),
                "name": getattr(md, "name", None),
                "sig": getattr(md, "sig", None),
                "pc": pc,
                "regs": [_fmt(x) for x in getattr(fr, "v", [])],
            })
        return out

    def callstack(self):
        """Aktueller Aufruf-Stack von innen (#0) nach aussen (#N).
        Bei Beenden/Stopp bleibt der zuletzt bekannte Aufruf-Stack erhalten."""
        live = self._build_live_callstack()
        if live:
            return live
        if self._last_callstack:
            return [dict(item, historical=True) for item in self._last_callstack]
        if self._last_raw_frames:
            out = []
            for pos, (fr_obj, pc) in enumerate(reversed(self._last_raw_frames)):
                md = getattr(fr_obj, "md", fr_obj)
                if pos == 0 and self._last_frame_info:
                    pc = self._last_frame_info.get("pc", pc)
                raw_v = getattr(fr_obj, "v", None) or ()
                out.append({
                    "index": pos,
                    "cls": getattr(md, "cls", None),
                    "name": getattr(md, "name", None),
                    "sig": getattr(md, "sig", None),
                    "pc": pc,
                    "regs": [_fmt(x) for x in raw_v],
                    "historical": True,
                })
            return out
        if self._last_frame_info:
            lf = self._last_frame_info
            return [{
                "index": 0,
                "cls": lf.get("cls"),
                "name": lf.get("name"),
                "sig": lf.get("sig"),
                "pc": lf.get("pc"),
                "regs": [],
                "historical": True,
            }]
        return []

    def run_interaction(self, fn, stop_at_entry=False, min_budget=5_000_000):
        """Fuehrt eine UI-Interaktion (Tap/Back/Input) unter voller Debugger-Kontrolle aus,
        sodass Breakpoints/Exceptions im Listener/Callback sauber pausieren koennen."""
        if self.mode == "paused":
            return self.state()
        if self.vm is not None:
            self.vm.steps = 0
        self._entry = None
        self._run_fn = fn
        return self._launch(pause_at_step=0 if stop_at_entry else None, min_budget=min_budget)

    # ---- Lauf-Steuerung (Controller-Thread) --------------------------------
    def start(self, cls, name, sig, args=(), stop_at_entry=True):
        """Startet die Methode im Worker-Thread. Pausiert am Entry (stop_at_entry) oder am 1. Breakpoint."""
        self._entry = (cls, name, sig, tuple(args))
        self._run_fn = None
        self._statics_snap = {c: dict(d) for c, d in self.vm.statics.items()}
        return self._launch(pause_at_step=0 if stop_at_entry else None)

    def start_app(self, run_fn, stop_at_entry=False):
        """App-/Lifecycle-Einstieg: der Debugger treibt einen beliebigen `run_fn` (z.B.
        LifecycleRunner.run_app) statt einer Einzelmethode. Breakpoints UND Aktionspunkte feuern
        ueber den ganzen Lauf. `stop_at_entry` haelt an der 1. ausgefuehrten Instruktion."""
        self._entry = None
        self._run_fn = run_fn
        self._statics_snap = {c: dict(d) for c, d in self.vm.statics.items()}
        return self._launch(pause_at_step=0 if stop_at_entry else None)

    def _launch(self, pause_at_step=None, min_budget=None):
        self.timeline = []
        self.markers = []
        self.step_count = 0
        self.current = None
        self.result = None
        self.error = None
        self.pause_reason = None
        self._last_callstack = []
        self._last_frame_info = {}
        self._last_logic_info = {}
        self._last_fr = None
        self._last_raw_frames = []
        self._last_frames_len = 0
        if self.pause_on_budget:
            self._budget_limit = max(1, int(self._budget_chunk), int(min_budget or 0))
        self.mode = "run"
        self.pause_at_step = pause_at_step
        self._resume.clear()
        self._paused.clear()
        self.vm._dbg = self
        for _m in self._bp_meta.values():
            _m["hits"] = 0
        for _ap in self.action_points:
            _ap["hits"] = 0
        rf = self._run_fn
        if rf is None:
            cls, name, sig, args = self._entry
            rf = lambda: self.vm.invoke(cls, name, sig, args)

        def _run():
            try:
                self.result = rf()
                self.mode = "done"
            except DebugStop:
                self.mode = "stopped"
            except Exception as e:                       # noqa
                self.error = repr(e); self.mode = "error"
            finally:
                self.vm._dbg = None
                self._paused.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._paused.wait(15)
        return self.state()

    def _resume_and_wait(self):
        if self.pause_on_budget and (self.pause_reason == "budget" or self.step_count >= self._budget_limit):
            self._budget_limit = self.step_count + max(1, int(self._budget_chunk))
        self.pause_reason = None
        self.current = None
        self.pause_at_step = None
        self._paused.clear()
        self._resume.set()
        self._paused.wait(15)
        return self.state()

    def step(self):
        if self.mode != "paused":
            return self.state()
        self._step_ref_depth = None
        self.mode = "step"
        return self._resume_and_wait()

    def step_over(self):
        """In Callees NICHT hineinhalten: weiterlaufen, bis die Tiefe wieder <= Haltestelle ist."""
        if self.mode != "paused":
            return self.state()
        self._step_ref_depth = self.cur_depth
        self.mode = "over"
        return self._resume_and_wait()

    def step_out(self):
        """Aktuelle Methode zu Ende: weiterlaufen, bis die Tiefe < Haltestelle ist (Return in Caller)."""
        if self.mode != "paused":
            return self.state()
        self._step_ref_depth = self.cur_depth
        self.mode = "out"
        return self._resume_and_wait()

    def cont(self):
        if self.mode != "paused":
            return self.state()
        self._step_ref_depth = None
        self.mode = "run"
        return self._resume_and_wait()

    def stop(self):
        # Abbruch aus JEDEM Lauf-Zustand (run/step/over/out/paused):
        # on_step prueft `mode == "stopped"` und wirft beim naechsten Schritt DebugStop.
        if self.mode in ("run", "step", "over", "out", "paused"):
            self.mode = "stopped"
            self._paused.clear()     # erst loeschen, damit _paused.wait() wirklich auf den Worker wartet
            self._resume.set()       # falls pausiert: Worker freigeben -> on_step -> DebugStop
            self._paused.wait(5)
        return self.state()

    # ---- Time-Travel (voller Per-Step-Replay) ------------------------------
    def goto(self, n):
        """Deterministische Re-Exec von vorn bis Schritt n (rewind oder forward). Pausiert dort."""
        if self.mode == "paused":
            self.stop()
        # Statics auf den Startzustand zuruecksetzen (Determinismus)
        if self._statics_snap is not None:
            self.vm.statics = {c: dict(d) for c, d in self._statics_snap.items()}
        return self._launch(pause_at_step=max(0, int(n)))

    # ---- Inspektion / Mutation --------------------------------------------
    def state(self):
        cur = self.current or {}
        md = cur.get("fr").md if cur.get("fr") else None
        lf = self._last_frame_info or {}
        cls = md.cls if md else lf.get("cls")
        name = md.name if md else lf.get("name")
        sig = md.sig if md else lf.get("sig")
        pc = cur.get("idx") if cur else lf.get("pc")
        op = cur.get("op") if cur else lf.get("op")
        return {"mode": self.mode, "step": self.step_count,
                "cls": cls, "name": name,
                "sig": sig, "pc": pc, "op": op,
                "logic_cls": (self._last_logic_info or {}).get("cls"),
                "logic_name": (self._last_logic_info or {}).get("name"),
                "logic_sig": (self._last_logic_info or {}).get("sig"),
                "logic_pc": (self._last_logic_info or {}).get("pc"),
                "depth": self.vm._depth if cur else lf.get("depth", 0),
                "result": _fmt(self.result), "error": self.error,
                "reason": self.pause_reason, "exception": self.last_exception}

    def registers(self):
        cur = self.current
        fr = cur.get("fr") if cur else None
        if fr is None:
            fr = self._last_fr
        return [_fmt(x) for x in fr.v] if fr is not None else []

    def active_raw_regs(self, stack_idx=0):
        """Liefert die rohen Python/Dalvik-Registerwerte (auch nach mode=done/stopped)."""
        try:
            idx = int(stack_idx or 0)
        except Exception:
            idx = 0
        frames = list(getattr(self.vm, "_frames", []) or [])
        if frames and 0 <= idx < len(frames):
            return list(getattr(frames[len(frames) - 1 - idx], "v", []) or [])
        cur = self.current
        if cur and cur.get("fr") and idx == 0:
            return list(getattr(cur["fr"], "v", []) or [])
        if self._last_raw_frames and 0 <= idx < len(self._last_raw_frames):
            fr_obj = self._last_raw_frames[len(self._last_raw_frames) - 1 - idx][0]
            if getattr(fr_obj, "v", None) is not None:
                return list(fr_obj.v)
        if self._last_fr is not None:
            return list(getattr(self._last_fr, "v", []) or [])
        return []

    def inspect(self):
        st = self.state()
        st["registers"] = self.registers()
        st["breakpoints"] = sorted(self.breakpoints, key=lambda b: (b[0] or "", b[1] or "", b[3] or -1))
        st["timeline_len"] = len(self.timeline)
        return st

    def objects(self, max_depth=4):
        """Objekt-Baum je Register (live am Haltepunkt oder vom letzten aktiven Frame nach Ende)."""
        if self.mode == "idle":
            return []
        cur = self.current
        fr = cur.get("fr") if cur else None
        if fr is None:
            fr = self._last_fr
        if fr is None:
            return []
        roots = []
        for i, val in enumerate(fr.v):
            roots.append(build_object_node("v%d" % i, val, max_depth))
        return roots

    def timeline_slice(self, start=0, limit=50):
        return self.timeline[start:start + limit]

    def marker_list(self):
        return list(self.markers)

    def set_register(self, i, value):
        if self.mode == "paused" and self.current:
            self.current["fr"].v[int(i)] = value
            return True
        return False

    def set_field(self, obj_reg, field, value):
        if self.mode == "paused" and self.current:
            obj = self.current["fr"].v[int(obj_reg)]
            if isinstance(obj, JObject):
                obj.fields[field] = value
                return True
        return False

    def set_static(self, cls, field, value):
        self.vm.statics.setdefault(cls, {})[field] = value
        return True
