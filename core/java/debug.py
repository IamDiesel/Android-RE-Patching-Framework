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
import collections
import itertools
import json
import operator as _op
import os
from pathlib import Path
import threading
import time

from core.java.deviation_journal import DebugStop
from core.java.objects import JString, JObject, JArray


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
    if not isinstance(expr, str):
        expr = str(expr)
    if len(expr) > 512:
        raise ValueError("Ausdruck zu lang (>512)")
    tree = ast.parse(expr, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("Ausdruck zu komplex (>64 AST-Knoten)")
    node = tree.body

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
            lv = ev(n.left)
            rv = ev(n.right)
            if isinstance(n.op, ast.Pow):
                if isinstance(rv, bool) or not isinstance(rv, (int, float)) or rv < 0 or rv > 64:
                    raise ValueError("Pow-Exponent ausserhalb 0..64")
                if isinstance(lv, int) and not isinstance(lv, bool) and abs(lv).bit_length() * int(rv) > 4096:
                    raise ValueError("Pow-Ergebnis zu gross")
            elif isinstance(n.op, (ast.LShift, ast.RShift)):
                if isinstance(rv, bool) or not isinstance(rv, int) or rv < 0 or rv > 256:
                    raise ValueError("Shift ausserhalb 0..256")
            elif isinstance(n.op, ast.Mult):
                seq_types = (str, bytes, bytearray, list, tuple)
                if isinstance(lv, seq_types) or isinstance(rv, seq_types):
                    seq, rep = (lv, rv) if isinstance(lv, seq_types) else (rv, lv)
                    if isinstance(rep, bool) or not isinstance(rep, int):
                        raise ValueError("Ungueltiger Sequenz-Multiplikator")
                    if len(seq) * max(0, rep) > 4096:
                        raise ValueError("Sequenz-Multiplikation zu gross")
            return _BINOPS[type(n.op)](lv, rv)
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


_SKIP_SNAP_FIELDS = frozenset({"$view", "_tree", "_dbg_reported"})


def _deep_snap_value(val, memo=None, _depth=0):
    """Tiefe Momentaufnahme fuer Dalvik-Werte (JArray/JObject/Container) unter Identitaetserhalt."""
    if memo is None:
        memo = {}
    if val is None or isinstance(val, (bool, int, float, str, bytes, JString)):
        return val
    oid = id(val)
    if oid in memo:
        return memo[oid]
    if _depth >= 16:
        return val
    if isinstance(val, JArray):
        clone = JArray(val.eltype, [])
        memo[oid] = clone
        clone.data = [_deep_snap_value(el, memo, _depth + 1) for el in val.data]
        return clone
    if isinstance(val, JObject):
        clone = JObject(val.cls, {})
        memo[oid] = clone
        for k, v in val.fields.items():
            if k in _SKIP_SNAP_FIELDS:
                clone.fields[k] = v
            else:
                clone.fields[k] = _deep_snap_value(v, memo, _depth + 1)
        return clone
    if isinstance(val, list):
        clone = []
        memo[oid] = clone
        clone.extend(_deep_snap_value(x, memo, _depth + 1) for x in val)
        return clone
    if isinstance(val, dict):
        clone = {}
        memo[oid] = clone
        for k, v in val.items():
            if k in _SKIP_SNAP_FIELDS:
                clone[k] = v
            else:
                clone[k] = _deep_snap_value(v, memo, _depth + 1)
        return clone
    if isinstance(val, tuple):
        return tuple(_deep_snap_value(x, memo, _depth + 1) for x in val)
    if isinstance(val, set):
        return {_deep_snap_value(x, memo, _depth + 1) for x in val}
    return val


class RingTimeline:
    """Bounded Ring-Puffer fuer Timeline-Eintraege auf Basis collections.deque."""

    def __init__(self, maxlen=500_000):
        cap = max(0, int(maxlen))
        self.maxlen = cap
        self._dq = collections.deque(maxlen=cap)

    def append(self, item):
        self._dq.append(item)

    def extend(self, items):
        self._dq.extend(items)

    def clear(self):
        self._dq.clear()

    def __len__(self):
        return len(self._dq)

    def __iter__(self):
        return iter(self._dq)

    def __bool__(self):
        return bool(self._dq)

    def __getitem__(self, item):
        if isinstance(item, slice):
            start, stop, step = item.indices(len(self._dq))
            if step == 1:
                return list(itertools.islice(self._dq, start, stop))
            return [self._dq[i] for i in range(start, stop, step)]
        return self._dq[item]


class JavaDebugger:
    """Interaktiver Debugger über einer `DalvikVM`. Immer EINE Methode pro Sitzung (mit Callees)."""

    def __init__(self, vm, max_steps=2_000_000, timeline_cap=500_000):
        self.vm = vm
        self._lock = threading.RLock()
        self._run_gen = 0
        self._active_gen = 0
        self._tls = threading.local()
        self.breakpoints = set()          # (cls, name, sig, pc|None)
        self._bp_meta = {}                 # key(tuple) -> {cond,hit,one_shot,hits}
        self._bp_by_m = {}                 # (cls, name, sig) -> set[bp_tuple]
        self._bp_wild = set()              # Wildcard-BPs (cls/name/sig is None)
        self.timeline_cap = int(timeline_cap)
        self.timeline = RingTimeline(self.timeline_cap)
        self.step_count = 0               # UNABHAENGIGER Schrittzaehler (max_steps/pause_at_step/markers)
        self.record_timeline = False      # AP1.1: Time-Travel/Reverse AUS per Default (Performance)
        self.record_regs = False          # AP1.1: Register-Kopie je Schritt nur bei Bedarf
        self.mode = "idle"                # idle|run|step|paused|done|error|stopped
        self.max_steps = int(max_steps)
        self.current = None               # bei paused: dict(fr,insns,ins,op,idx)
        self.result = None
        self.error = None
        self.pause_at_step = None
        self.cur_depth = 0                 # Tiefe an der Haltestelle (fuer over/out)
        self._step_ref_depth = None        # Referenztiefe fuer step_over/step_out
        self._step_ref_frame = None        # exakter Referenz-Frame fuer step_over/step_out
        self._step_guard_limit = None      # Schutzlimit gegen Endlosschleifen bei step_over/step_out
        self._resume = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._entry = None                # (cls,name,sig,args)
        self._entry_args_snap = None
        self._statics_snap = None
        self._clinit_snap = set()
        self._since_yield = 0
        self._last_yield_ts = 0.0
        self.action_points = []           # regelbasierte Manipulation (bedingt/funktional/getriggert)
        self.markers = []                 # Zeitachsen-Marker {step,kind,label} (net/manip)
        self._run_fn = None               # Treiber des Laufs (None = Einzel-invoke aus _entry)
        self._pause_requested = False
        self.stop_on_exception = False
        self.exception_unhandled_only = True
        self.last_exception = None
        self.last_bp_error = None
        self._bp_error_pause = False
        self.pause_reason = None
        self.flight_recorder_enabled = False
        self.flight_path = str(Path(__file__).resolve().parents[2] / "data" / "glass_flight.jsonl")
        self._flight_fh = None
        self._flight_fh_path = None
        self.pause_on_budget = False
        self._budget_chunk = int(max_steps)
        self._budget_limit = int(max_steps)
        self._last_callstack = []
        self._last_frame_info = {}
        self._last_logic_info = {}
        self._last_frame_raw = None
        self._last_frame_depth = 0
        self._last_logic_raw = None
        self._last_logic_depth = 0
        self._last_fr = None
        self._last_raw_frames = []
        self._last_frames_len = 0

    def _append_marker(self, marker):
        if len(self.markers) >= 10_000:
            del self.markers[:len(self.markers) - 9_999]
        self.markers.append(marker)

    @staticmethod
    def _validate_cond_syntax(cond):
        if not cond:
            return None
        cs = str(cond)
        if len(cs) > 512:
            raise ValueError("Bedingung zu lang (>512)")
        try:
            ast.parse(cs, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Ungueltige Bedingung: {e}") from e
        return cs

    def _index_bp_locked(self, key):
        if key[0] is None or key[1] is None or key[2] is None:
            self._bp_wild.add(key)
        else:
            self._bp_by_m.setdefault((key[0], key[1], key[2]), set()).add(key)

    def _unindex_bp_locked(self, key):
        if key[0] is None or key[1] is None or key[2] is None:
            self._bp_wild.discard(key)
        else:
            mkey = (key[0], key[1], key[2])
            bucket = self._bp_by_m.get(mkey)
            if bucket is not None:
                bucket.discard(key)
                if not bucket:
                    self._bp_by_m.pop(mkey, None)

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
        cond_s = self._validate_cond_syntax(cond)
        key = (cls, name, sig, pc)
        with self._lock:
            self.breakpoints.add(key)
            self._index_bp_locked(key)
            meta = {}
            if cond_s:
                meta["cond"] = cond_s
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

    def remove_breakpoint(self, cls, name, sig, pc=None):
        key = (cls, name, sig, pc)
        with self._lock:
            existed = key in self.breakpoints
            self.breakpoints.discard(key)
            self._bp_meta.pop(key, None)
            self._unindex_bp_locked(key)
            return existed

    def set_breakpoints(self, bps):
        with self._lock:
            self.clear_breakpoints()
            for item in (bps or ()):
                if isinstance(item, dict):
                    self.add_breakpoint(
                        item.get("cls"), item.get("name"), item.get("sig"),
                        pc=item.get("pc"), cond=item.get("cond"),
                        hit=item.get("hit"), one_shot=bool(item.get("one_shot", False)),
                    )
                elif isinstance(item, (tuple, list)):
                    if len(item) == 3:
                        self.add_breakpoint(item[0], item[1], item[2])
                    elif len(item) == 4:
                        self.add_breakpoint(item[0], item[1], item[2], item[3])
                    elif len(item) >= 5:
                        meta = item[4] if isinstance(item[4], dict) else {}
                        self.add_breakpoint(
                            item[0], item[1], item[2], item[3],
                            cond=meta.get("cond"), hit=meta.get("hit"),
                            one_shot=bool(meta.get("one_shot", False)),
                        )

    def clear_breakpoints(self):
        with self._lock:
            self.breakpoints.clear()
            self._bp_meta.clear()
            self._bp_by_m.clear()
            self._bp_wild.clear()

    # ---- Aktionspunkte (bedingte/funktionale/getriggerte Manipulation) ----
    def add_action_point(self, cls, name, sig, pc=None, assigns=None, cond=None,
                         when_continue=False, one_shot=False, hit=None):
        """Regelbasierte Manipulation an (cls,name,sig,pc).
        assigns: Liste {target, value, const}. target in vN|pN|<vReg>.<feld>|<Cls;>.<feld>.
                 const=True -> value woertlich; sonst value = Ausdruck (safe_eval ueber vN/pN).
        cond: Ausdruck (safe_eval) oder None. when_continue=True -> ohne Halt weiterlaufen.
        one_shot/hit: wie bei Breakpoints."""
        cond_s = self._validate_cond_syntax(cond)
        ap = {"cls": cls, "name": name, "sig": sig, "pc": pc,
              "assigns": list(assigns or []), "cond": cond_s,
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
                except Exception as e:
                    self.last_bp_error = {"bp": (cls, name, sig, idx), "cond": cond, "error": repr(e)}
                    self._append_marker({"step": self.step_count, "kind": "bp_error", "label": f"{cond}: {e}"})
                    self._bp_error_pause = True
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
                    self._append_marker({"step": self.step_count, "kind": "manip",
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
        if (self.mode == "stopped"
                or self._active_gen != self._run_gen
                or getattr(self._tls, "gen", self._run_gen) != self._run_gen):
            raise DebugStop()
        self._since_yield += 1
        if (self._since_yield & 511) == 0:
            now = time.monotonic()
            if now - self._last_yield_ts >= 0.012:
                self._last_yield_ts = now
                time.sleep(0.001)     # GIL + UI-Thread periodisch atmen lassen
        limit = self._budget_limit if self.pause_on_budget else self.max_steps
        budget_hit = (self.step_count >= limit)
        if budget_hit and not self.pause_on_budget:
            self.pause_reason = "max_steps"
            raise DebugStop()
        md = fr.md
        cls = md.cls if md else None
        name = md.name if md else None
        sig = md.sig if md else None
        frames_ref = getattr(vm, "_frames", None) or ()
        cur_d = len(frames_ref) if frames_ref else vm._depth
        if md is not None:
            self._last_frame_raw = (fr, idx, op)
            self._last_frame_depth = cur_d
            if (cls and not cls.startswith(("Landroid/", "Landroidx/", "Ljava/", "Lkotlin/", "Lkotlinx/"))
                    and "$$ExternalSyntheticLambda" not in cls):
                self._last_logic_raw = (fr, idx, op)
                self._last_logic_depth = cur_d
        self._last_fr = fr
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
        if self.record_timeline and self.timeline_cap > 0:
            entry = {"step": sc, "cls": cls, "name": name, "sig": sig,
                     "pc": idx, "op": op, "depth": cur_d}
            if self.record_regs:
                entry["regs"] = [_fmt(x) for x in fr.v]
            self.timeline.append(entry)
        if self.flight_recorder_enabled and (sc % 100 == 0 or op.startswith("invoke")):
            self.flight_log("step", {"cls": cls, "name": name, "pc": idx, "op": op})
        self.cur_depth = cur_d
        self._bp_error_pause = False
        hit = self._bp_hit(cls, name, sig, idx, fr)
        ap_pause = self._apply_action_points(cls, name, sig, idx, fr) if self.action_points else False
        bp_err = self._bp_error_pause
        self._bp_error_pause = False
        at_target = self.pause_at_step is not None and sc >= self.pause_at_step
        req_pause = bool(self._pause_requested)
        if req_pause:
            self._pause_requested = False
        want_step = False
        step_guard_hit = False
        if self.mode == "step":
            want_step = True                       # Step Into: naechste Instruktion, egal welche Tiefe
        elif self.mode == "over":
            want_step = (
                self._step_ref_depth is None
                or cur_d < self._step_ref_depth
                or (
                    cur_d == self._step_ref_depth
                    and (
                        self._step_ref_frame is None
                        or fr is self._step_ref_frame
                        or self._step_ref_frame not in frames_ref
                    )
                )
            )
            step_guard_hit = (self._step_guard_limit is not None and sc >= self._step_guard_limit)
        elif self.mode == "out":
            want_step = (
                (self._step_ref_frame is not None and self._step_ref_frame not in frames_ref)
                or (self._step_ref_frame is None and self._step_ref_depth is not None and cur_d < self._step_ref_depth)
            )
            step_guard_hit = (self._step_guard_limit is not None and sc >= self._step_guard_limit)
        if want_step or hit or at_target or ap_pause or bp_err or req_pause or budget_hit or step_guard_hit:
            if bp_err:
                self.pause_reason = "bp_error"
            elif hit:
                self.pause_reason = "breakpoint"
            elif ap_pause:
                self.pause_reason = "action_point"
            elif budget_hit:
                self.pause_reason = "budget"
            elif step_guard_hit:
                self.pause_reason = "step_limit"
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
        with self._lock:
            bucket = self._bp_by_m.get((cls, name, sig))
            if not bucket and not self._bp_wild:
                return False
            cands = list(bucket or ())
            if self._bp_wild:
                cands.extend(self._bp_wild)
        fired = False
        remove = []
        names = None
        for b in cands:
            if not ((b[0] is None or b[0] == cls) and (b[1] is None or b[1] == name)
                    and (b[2] is None or b[2] == sig) and (b[3] is None or b[3] == idx)):
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
                except Exception as e:
                    self.last_bp_error = {"bp": b, "cond": cond, "error": repr(e)}
                    self._append_marker({"step": self.step_count, "kind": "bp_error", "label": f"{cond}: {e}"})
                    self._bp_error_pause = True
                    continue
            meta["hits"] = meta.get("hits", 0) + 1
            ht = meta.get("hit")
            if ht and meta["hits"] < int(ht):
                continue
            fired = True
            if meta.get("one_shot"):
                remove.append(b)
        if remove:
            with self._lock:
                for b in remove:
                    self.breakpoints.discard(b)
                    self._bp_meta.pop(b, None)
                    self._unindex_bp_locked(b)
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
        with self._lock:
            if (self.mode == "stopped"
                    or self._active_gen != self._run_gen
                    or getattr(self._tls, "gen", self._run_gen) != self._run_gen):
                raise DebugStop()
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
                if self._flight_fh is not None:
                    try:
                        self._flight_fh.flush()
                    except Exception:
                        pass
            self._resume.clear()
            self._paused.set()
        self._resume.wait()
        with self._lock:
            if (self.mode == "stopped"
                    or self._active_gen != self._run_gen
                    or getattr(self._tls, "gen", self._run_gen) != self._run_gen):
                raise DebugStop()

    def pause(self):
        """Fordert einen sauberen Pause-Halt an der naechsten Dalvik-Instruktion an."""
        with self._lock:
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

    def close_flight_log(self):
        with self._lock:
            fh = self._flight_fh
            self._flight_fh = None
            self._flight_fh_path = None
            if fh is not None:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass

    def flight_log(self, event_type, payload=None):
        """Korrelierter JSONL-Flight-Recorder nach data/glass_flight.jsonl."""
        if not self.flight_recorder_enabled:
            return
        with self._lock:
            try:
                target = str(self.flight_path)
                if self._flight_fh is None or self._flight_fh_path != target or self._flight_fh.closed:
                    if self._flight_fh is not None:
                        try:
                            self._flight_fh.close()
                        except Exception:
                            pass
                    d = os.path.dirname(target)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    self._flight_fh = open(target, "a", encoding="utf-8", buffering=1)
                    self._flight_fh_path = target
                rec = {
                    "ts": round(time.time(), 4),
                    "step": int(self.step_count),
                    "event": str(event_type),
                    "data": payload or {},
                }
                self._flight_fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
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
        with self._lock:
            self._sync_frame_info_locked()
            raw_frames = list(self._last_raw_frames)
            lf = dict(self._last_frame_info) if self._last_frame_info else {}
        if raw_frames:
            out = []
            for pos, (fr_obj, pc) in enumerate(reversed(raw_frames)):
                md = getattr(fr_obj, "md", fr_obj)
                if pos == 0 and lf:
                    pc = lf.get("pc", pc)
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
        if lf:
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
        with self._lock:
            if self.mode == "paused":
                return self.state()
            need_stop = self.mode in ("run", "step", "over", "out")
        if need_stop:
            self.stop(wait_timeout=0.2)
        if self.vm is not None:
            self.vm.steps = 0
            if isinstance(getattr(self.vm, "step_budget", None), int) and self.vm.step_budget > 0:
                self.vm.step_budget = max(self.vm.step_budget, int(min_budget or 0))
        self._entry = None
        self._entry_args_snap = None
        self._run_fn = fn
        return self._launch(pause_at_step=0 if stop_at_entry else None, min_budget=min_budget)

    # ---- Lauf-Steuerung (Controller-Thread) --------------------------------
    def start(self, cls, name, sig, args=(), stop_at_entry=True, wait_timeout=15.0):
        """Startet die Methode im Worker-Thread. Pausiert am Entry (stop_at_entry) oder am 1. Breakpoint."""
        if self.mode in ("run", "step", "over", "out", "paused"):
            self.stop(wait_timeout=0.5)
        snap_memo = {}
        self._statics_snap = {c: _deep_snap_value(d, snap_memo) for c, d in self.vm.statics.items()}
        self._clinit_snap = set(getattr(self.vm, "_clinit_done", ()) or ())
        self._entry_args_snap = tuple(_deep_snap_value(x, snap_memo) for x in args)
        self._entry = (cls, name, sig, tuple(args))
        self._run_fn = None
        if hasattr(self.vm, "coverage") and isinstance(self.vm.coverage, dict):
            self.vm.coverage.clear()
        if hasattr(self.vm, "lenient_errors") and isinstance(self.vm.lenient_errors, list):
            self.vm.lenient_errors.clear()
        return self._launch(pause_at_step=0 if stop_at_entry else None, wait_timeout=wait_timeout)

    def start_app(self, run_fn, stop_at_entry=False, wait_timeout=15.0):
        """App-/Lifecycle-Einstieg: der Debugger treibt einen beliebigen `run_fn` (z.B.
        LifecycleRunner.run_app) statt einer Einzelmethode. Breakpoints UND Aktionspunkte feuern
        ueber den ganzen Lauf. `stop_at_entry` haelt an der 1. ausgefuehrten Instruktion."""
        if self.mode in ("run", "step", "over", "out", "paused"):
            self.stop(wait_timeout=0.5)
        snap_memo = {}
        self._entry = None
        self._entry_args_snap = None
        self._run_fn = run_fn
        self._statics_snap = {c: _deep_snap_value(d, snap_memo) for c, d in self.vm.statics.items()}
        self._clinit_snap = set(getattr(self.vm, "_clinit_done", ()) or ())
        return self._launch(pause_at_step=0 if stop_at_entry else None, wait_timeout=wait_timeout)

    def _launch(self, pause_at_step=None, min_budget=None, wait_timeout=15.0):
        th_old = self._thread
        if th_old is not None and th_old.is_alive() and th_old is not threading.current_thread():
            self.stop(wait_timeout=0.5)
        with self._lock:
            rf = self._run_fn
            if rf is None and self._entry is None:
                self.mode = "idle"
                self._paused.set()
                return self.state()
            if getattr(self.timeline, "maxlen", None) != self.timeline_cap:
                self.timeline = RingTimeline(self.timeline_cap)
            else:
                self.timeline.clear()
            self.markers = []
            self.step_count = 0
            self.current = None
            self.result = None
            self.error = None
            self.last_exception = None
            self.last_bp_error = None
            self._bp_error_pause = False
            self.pause_reason = None
            self._step_ref_depth = None
            self._step_ref_frame = None
            self._step_guard_limit = None
            self._last_callstack = []
            self._last_frame_info = {}
            self._last_logic_info = {}
            self._last_frame_raw = None
            self._last_frame_depth = 0
            self._last_logic_raw = None
            self._last_logic_depth = 0
            self._last_fr = None
            self._last_raw_frames = []
            self._last_frames_len = 0
            if self.pause_on_budget:
                self._budget_limit = max(1, int(self._budget_chunk), int(min_budget or 0))
            self._run_gen += 1
            my_gen = self._run_gen
            self._active_gen = my_gen
            self.mode = "run"
            self.pause_at_step = pause_at_step
            self._pause_requested = False
            self._resume.clear()
            self._paused.clear()
            self.vm._dbg = self
            for _m in self._bp_meta.values():
                _m["hits"] = 0
            for _ap in self.action_points:
                _ap["hits"] = 0
            if rf is None:
                cls, name, sig, args = self._entry
                rf = lambda: self.vm.invoke(cls, name, sig, args)

            def _run():
                self._tls.gen = my_gen
                try:
                    res = rf()
                    with self._lock:
                        if self._run_gen == my_gen:
                            self.result = res
                            self.mode = "done"
                except DebugStop:
                    with self._lock:
                        if self._run_gen == my_gen:
                            self.mode = "stopped"
                except Exception as e:                       # noqa
                    with self._lock:
                        if self._run_gen == my_gen:
                            self.error = repr(e)
                            self.mode = "error"
                finally:
                    with self._lock:
                        if self._run_gen == my_gen:
                            if self.vm is not None:
                                self.vm._dbg = None
                            self.close_flight_log()
                            self._paused.set()

            th = threading.Thread(target=_run, daemon=True)
            self._thread = th
        th.start()
        self._paused.wait(wait_timeout)
        return self.state()

    def _resume_and_wait_locked(self, wait_timeout=15.0):
        if self.pause_on_budget and (self.pause_reason == "budget" or self.step_count >= self._budget_limit):
            self._budget_limit = self.step_count + max(1, int(self._budget_chunk))
        self.pause_reason = None
        self.current = None
        self.pause_at_step = None
        self._paused.clear()
        self._resume.set()

    def _resume_and_wait(self, wait_timeout=15.0):
        with self._lock:
            if self.mode in ("stopped", "done", "error", "idle"):
                return self.state()
            self._resume_and_wait_locked(wait_timeout=wait_timeout)
        self._paused.wait(wait_timeout)
        return self.state()

    def step(self, wait_timeout=15.0):
        with self._lock:
            if self.mode != "paused":
                return self.state()
            self._step_ref_depth = None
            self._step_ref_frame = None
            self._step_guard_limit = None
            self.mode = "step"
            self._resume_and_wait_locked(wait_timeout=wait_timeout)
        self._paused.wait(wait_timeout)
        return self.state()

    def step_over(self, wait_timeout=15.0):
        """In Callees NICHT hineinhalten: weiterlaufen, bis der Ausgangs-Frame wieder aktiv ist bzw. verlassen wurde."""
        with self._lock:
            if self.mode != "paused":
                return self.state()
            frames_now = getattr(self.vm, "_frames", None) or ()
            self._step_ref_depth = len(frames_now) if frames_now else self.cur_depth
            self._step_ref_frame = (self.current or {}).get("fr")
            self._step_guard_limit = self.step_count + 200_000
            self.mode = "over"
            self._resume_and_wait_locked(wait_timeout=wait_timeout)
        self._paused.wait(wait_timeout)
        return self.state()

    def step_out(self, wait_timeout=15.0):
        """Aktuelle Methode zu Ende: weiterlaufen, bis der Ausgangs-Frame verlassen wurde."""
        with self._lock:
            if self.mode != "paused":
                return self.state()
            frames_now = getattr(self.vm, "_frames", None) or ()
            self._step_ref_depth = len(frames_now) if frames_now else self.cur_depth
            self._step_ref_frame = (self.current or {}).get("fr")
            self._step_guard_limit = self.step_count + 200_000
            self.mode = "out"
            self._resume_and_wait_locked(wait_timeout=wait_timeout)
        self._paused.wait(wait_timeout)
        return self.state()

    def cont(self, wait_timeout=15.0):
        with self._lock:
            if self.mode != "paused":
                return self.state()
            self._step_ref_depth = None
            self._step_ref_frame = None
            self._step_guard_limit = None
            self.mode = "run"
            self._resume_and_wait_locked(wait_timeout=wait_timeout)
        self._paused.wait(wait_timeout)
        return self.state()

    def stop(self, wait_timeout=5.0):
        # Abbruch aus JEDEM Lauf-Zustand (run/step/over/out/paused):
        # on_step/_pause pruefen `mode == "stopped"` und werfen DebugStop.
        with self._lock:
            th = self._thread
            if self.mode in ("run", "step", "over", "out", "paused"):
                self.mode = "stopped"
                self._pause_requested = False
                self._resume.set()
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=wait_timeout)
        with self._lock:
            self._paused.set()
        return self.state()

    # ---- Time-Travel (voller Per-Step-Replay) ------------------------------
    def goto(self, n, wait_timeout=15.0):
        """Deterministische Re-Exec von vorn bis Schritt n (rewind oder forward). Pausiert dort."""
        if self.mode in ("run", "step", "over", "out", "paused"):
            self.stop()
        # Statics + Entry-Args + VM-Zustand auf den Startzustand zuruecksetzen (Determinismus)
        restore_memo = {}
        if self._statics_snap is not None:
            self.vm.statics = {c: _deep_snap_value(d, restore_memo) for c, d in self._statics_snap.items()}
        if self._entry is not None and self._entry_args_snap is not None:
            c0, n0, s0, _ = self._entry
            self._entry = (c0, n0, s0, tuple(_deep_snap_value(x, restore_memo) for x in self._entry_args_snap))
        if hasattr(self.vm, "_clinit_done"):
            self.vm._clinit_done = set(self._clinit_snap)
        if hasattr(self.vm, "coverage") and isinstance(self.vm.coverage, dict):
            self.vm.coverage.clear()
        if hasattr(self.vm, "lenient_errors") and isinstance(self.vm.lenient_errors, list):
            self.vm.lenient_errors.clear()
        self.vm.steps = 0
        self.vm._depth = 0
        if hasattr(self.vm, "_frames") and isinstance(self.vm._frames, list):
            self.vm._frames.clear()
        tgt = max(0, int(n))
        return self._launch(pause_at_step=tgt, min_budget=max(tgt + 1, int(self._budget_chunk)), wait_timeout=wait_timeout)

    # ---- Inspektion / Mutation --------------------------------------------
    @staticmethod
    def _materialize_raw(raw, depth):
        if not raw:
            return {}
        fr_r, pc_r, op_r = raw[0], raw[1], raw[2]
        md_r = getattr(fr_r, "md", None)
        if md_r is None:
            return {}
        return {
            "cls": md_r.cls, "name": md_r.name, "sig": md_r.sig,
            "pc": pc_r, "op": op_r, "depth": depth,
        }

    def _sync_frame_info_locked(self):
        if self._last_frame_raw is not None:
            self._last_frame_info = self._materialize_raw(self._last_frame_raw, self._last_frame_depth)
        if self._last_logic_raw is not None:
            self._last_logic_info = self._materialize_raw(self._last_logic_raw, self._last_logic_depth)

    def state(self):
        with self._lock:
            self._sync_frame_info_locked()
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
                    "reason": self.pause_reason, "exception": self.last_exception,
                    "bp_error": self.last_bp_error}

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
