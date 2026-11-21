# -*- coding: utf-8 -*-
"""core.java.jni_boundary — Die Java<->nativ-Schnittstelle als erstklassige, **beobachtbare** und
**manipulierbare** Flaeche (wie sonst Register/Felder im Java-Debugger).

Sitzt als optionaler Hook in `JavaToNativeBridge.call_native` (invoke-native). Fuer JEDEN Uebergang
Java -> nativ:
  * **Beobachten:** Funktionszeiger, this-Klasse, Eingangs-Argumente und Rueckgabe werden
    aufgezeichnet (`JniBoundaryLog`), als EventBus-Ereignis `JNI_CALL` veroeffentlicht und als
    Zeitachsen-Marker (kind="jni") an einen angehaengten Debugger gemeldet.
  * **Manipulieren:** regelbasierte Aktionspunkte (Paritaet zu den Java-Aktionspunkten, derselbe
    `safe_eval`) koennen Eingangs-Argumente VOR dem nativen Aufruf und die Rueckgabe DANACH
    umschreiben - bedingt (`cond`), funktional (Ausdruck ueber a0..aN bzw. ret) oder konstant.

Namen im Ausdruck: `a0, a1, ...` = Argumente (ohne env/this), `ret` = Rueckgabe (nur fuer ret_expr).
Zielagnostisch, keine App-Spezifika. Default inaktiv (kein Hook gesetzt -> Null-Overhead)."""
from __future__ import annotations

from core.java.debug import safe_eval

_M64 = (1 << 64) - 1


def _arg_names(args, extra=None):
    names = {}
    for i, a in enumerate(args):
        names["a%d" % i] = a
    if extra:
        names.update(extra)
    return names


class JniBoundaryLog:
    """Aufzeichnung der Java<->nativ-Uebergaenge (Beobachtung)."""

    def __init__(self):
        self.entries = []   # list[dict]

    def record(self, fnptr, cls, args_in, args_eff, ret_in, ret_eff):
        e = {"fnptr": int(fnptr) & _M64, "cls": cls,
             "args_in": list(args_in), "args_eff": list(args_eff),
             "ret_in": ret_in, "ret_eff": ret_eff}
        self.entries.append(e)
        return e

    def to_list(self):
        return list(self.entries)

    def __len__(self):
        return len(self.entries)


class JniBoundaryHook:
    """Beobachtet + manipuliert die Grenze. An `bridge.boundary_hook` gehaengt.

    `vm` (optional) dient dem Zeitachsen-Marker (vm._dbg) - genau wie beim Netz-Recorder."""

    def __init__(self, vm=None, log=None):
        self.vm = vm
        self.log = log if log is not None else JniBoundaryLog()
        self.action_points = []      # regelbasierte Grenz-Manipulation
        self._stack = []             # (fnptr, cls, args_in) fuer korrekte Verschachtelung

    # ---- Aktionspunkte -----------------------------------------------------
    def add_action_point(self, fnptr=None, this_cls=None, cond=None,
                         arg_assigns=None, ret_expr=None, one_shot=False, hit=None,
                         method_name=None, method_sig=None, skip_native=False):
        """arg_assigns = [{"index": i, "value"|"expr": ..., "const": bool}], ret_expr = Ausdruck
        ueber a0..aN und `ret`. fnptr/this_cls/method_name/method_sig = None -> Platzhalter."""
        ap = {"fnptr": (None if fnptr in ("", None) else int(fnptr)),
              "this_cls": this_cls or None, "cond": cond or None,
              "method_name": method_name or None, "method_sig": method_sig or None,
              "skip_native": bool(skip_native),
              "arg_assigns": list(arg_assigns or []), "ret_expr": ret_expr or None,
              "one_shot": bool(one_shot), "hit": (int(hit) if hit else None),
              "_hits": 0, "_fired": False}
        self.action_points.append(ap)
        return ap

    def clear_action_points(self):
        self.action_points = []

    def _match(self, ap, fnptr, this_cls, method_name=None, method_sig=None):
        if ap["fnptr"] is not None:
            if fnptr is None:
                return False
            if ap["fnptr"] != (int(fnptr) & _M64) and ap["fnptr"] != int(fnptr):
                return False
        if ap["this_cls"] is not None and ap["this_cls"] != this_cls:
            return False
        if ap.get("method_name") is not None and ap["method_name"] != method_name:
            return False
        if ap.get("method_sig") is not None and ap["method_sig"] != method_sig:
            return False
        return True

    def _eligible(self, ap):
        """Hit-Count / One-Shot wie bei den Java-Aktionspunkten."""
        ap["_hits"] += 1
        if ap["one_shot"] and ap["_fired"]:
            return False
        if ap["hit"] is not None and ap["_hits"] != ap["hit"]:
            return False
        return True

    # ---- Grenzuebertritt ---------------------------------------------------
    def before(self, fnptr, this_cls, args, method_name=None, method_sig=None):
        """Vor dem nativen Aufruf: Eingangs-Argumente ggf. umschreiben. Rueckgabe: effektive Args."""
        args = [int(a) & _M64 if isinstance(a, int) else a for a in args]
        args_in = list(args)
        skip_native = False
        matched_aps = set()
        for idx_ap, ap in enumerate(self.action_points):
            if not (ap["arg_assigns"] or ap.get("skip_native")):
                continue
            if not self._match(ap, fnptr, this_cls, method_name, method_sig):
                continue
            names = _arg_names(args)
            if ap["cond"]:
                try:
                    if not safe_eval(ap["cond"], names):
                        continue
                except Exception:
                    continue
            if not self._eligible(ap):
                continue
            ap["_fired"] = True
            matched_aps.add(idx_ap)
            if ap.get("skip_native"):
                skip_native = True
            for asg in ap["arg_assigns"]:
                idx = int(asg.get("index", 0))
                if idx < 0 or idx >= len(args):
                    continue
                if asg.get("const"):
                    val = asg.get("value")
                else:
                    expr = asg.get("expr", asg.get("value"))
                    try:
                        val = safe_eval(str(expr), _arg_names(args))
                    except Exception:
                        continue
                args[idx] = int(val) & _M64 if isinstance(val, (int, bool)) else val
        self._stack.append((int(fnptr) & _M64, this_cls, args_in))
        if not hasattr(self, "_skip_stack"):
            self._skip_stack = []
        self._skip_stack.append((skip_native, matched_aps))
        return args

    def check_skip(self, fnptr, this_cls, args_eff, method_name=None, method_sig=None) -> bool:
        """Prueft, ob der native Aufruf laut Grenz-Aktionspunkten uebersprungen werden soll."""
        skip_stack = getattr(self, "_skip_stack", None)
        if skip_stack:
            return bool(skip_stack[-1][0])
        for ap in self.action_points:
            if not ap.get("skip_native"):
                continue
            if not self._match(ap, fnptr, this_cls, method_name, method_sig):
                continue
            if ap["cond"]:
                try:
                    if not safe_eval(ap["cond"], _arg_names(args_eff)):
                        continue
                except Exception:
                    continue
            if ap["one_shot"] and ap["_fired"]:
                continue
            return True
        return False

    def after(self, fnptr, this_cls, args_eff, ret, method_name=None, method_sig=None):
        """Nach dem nativen Aufruf: Rueckgabe ggf. umschreiben, dann Uebergang aufzeichnen."""
        if self._stack:
            fn, cls, args_in = self._stack.pop()
        else:
            fn, cls, args_in = (int(fnptr) & _M64, this_cls, list(args_eff))
        skip_stack = getattr(self, "_skip_stack", None)
        matched_aps = skip_stack.pop()[1] if skip_stack else None
        ret_in = ret
        ret_eff = ret
        for idx_ap, ap in enumerate(self.action_points):
            if not ap["ret_expr"] or not self._match(ap, fnptr, this_cls, method_name, method_sig):
                continue
            if matched_aps is not None and (ap["arg_assigns"] or ap.get("skip_native")):
                if idx_ap not in matched_aps:
                    continue
                names = _arg_names(args_eff, {"ret": ret_eff})
            else:
                names = _arg_names(args_eff, {"ret": ret_eff})
                if ap["cond"]:
                    try:
                        if not safe_eval(ap["cond"], names):
                            continue
                    except Exception:
                        continue
                if ap["one_shot"] or ap["hit"] is not None:
                    if not self._eligible(ap):
                        continue
                    ap["_fired"] = True
            try:
                v = safe_eval(ap["ret_expr"], names)
                ret_eff = int(v) & _M64 if isinstance(v, (int, bool)) else v
            except Exception:
                pass
        self.log.record(fn, cls, args_in, args_eff, ret_in, ret_eff)
        self._publish(fn, cls, args_in, args_eff, ret_in, ret_eff)
        self._mark(fn, cls, args_eff, ret_eff)
        return ret_eff

    # ---- Beobachtung: Event + Marker --------------------------------------
    def _publish(self, fnptr, cls, args_in, args_eff, ret_in, ret_eff):
        try:
            from core.application.event_bus import EventBus
            EventBus.publish("JNI_CALL", {"fnptr": fnptr, "cls": cls,
                                          "args_in": list(args_in), "args_eff": list(args_eff),
                                          "ret_in": ret_in, "ret_eff": ret_eff})
        except Exception:
            pass

    def _mark(self, fnptr, cls, args_eff, ret_eff):
        dbg = getattr(self.vm, "_dbg", None) if self.vm is not None else None
        if dbg is None or not hasattr(dbg, "markers"):
            return
        try:
            step = len(getattr(dbg, "timeline", []))
            lbl = "fn@0x%x a=%s -> %s" % (int(fnptr) & _M64, list(args_eff), ret_eff)
            dbg.markers.append({"step": step, "kind": "jni", "label": lbl})
        except Exception:
            pass


def install_jni_boundary(bridge, vm=None, log=None):
    """Haengt den Beobacht-/Manipulations-Hook an eine `JavaToNativeBridge`. Rueckgabe: der Hook."""
    hook = JniBoundaryHook(vm=vm, log=log)
    bridge.boundary_hook = hook
    return hook
