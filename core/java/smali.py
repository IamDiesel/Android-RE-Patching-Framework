# -*- coding: utf-8 -*-
"""core.java.smali — begrenztes Smali-Frontend fuer den DalvikVM (J1).

Parst eine ``.method ... .end method`` (oder eine ganze ``.class``-Datei) in das Tupel-IR, das
`core.java.dalvik.DalvikVM` ausfuehrt: Register (``vN``/``pN``), Labels (``:cond_0`` -> Index),
``.registers``/``.locals``, und den implementierten Opcode-Satz. Bewusst bounded: kein try/catch,
keine Annotationen, kein packed/sparse-switch, Wide-Register (J/D) zaehlen vereinfacht als EIN Register.
Unbekannter Opcode -> `SmaliUnsupported` (Parse-Zeit-Coverage-Loop, analog FR-4). Zielagnostisch.
"""
from __future__ import annotations

import re

from core.java.dalvik import MethodDef
from core.java.objects import JString


class SmaliUnsupported(Exception):
    """Ein (noch) nicht unterstuetzter Smali-Opcode — speist den Coverage-Loop des Frontends."""
    def __init__(self, op, lineno, line):
        self.op = op
        self.lineno = lineno
        self.line = line
        super().__init__(f"Smali-Opcode nicht unterstuetzt: {op!r} (Zeile {lineno}: {line!r})")


_METHOD_RE = re.compile(r"^\s*\.method\b(?P<flags>[^\n]*?)\s+(?P<name>[^\s(]+)(?P<sig>\([^)]*\)\S+)\s*$")
_CLASS_RE = re.compile(r"^\s*\.class\b.*?\s(?P<cls>L[^\s;]+;)\s*$", re.MULTILINE)

_SKIP_PREFIX = (".line", ".prologue", ".local ", ".end local", ".restart local", ".param",
                ".annotation", ".end annotation", ".source", ".field", ".implements", ".super",
                ".catch", ".catchall", ".packed-switch", ".sparse-switch", ".array-data",
                ".end array-data", ".end packed-switch", ".end sparse-switch")

# Opcodes mit (reg, reg, reg)-Form (arithmetische 3-Adress + aget/aput)
_3REG = {"add-int", "sub-int", "mul-int", "div-int", "rem-int", "and-int", "or-int", "xor-int",
         "shl-int", "shr-int", "ushr-int", "add-long", "sub-long", "mul-long", "div-long",
         "aget", "aget-wide", "aget-object", "aget-boolean", "aget-byte", "aget-char", "aget-short",
         "aput", "aput-wide", "aput-object", "aput-boolean", "aput-byte", "aput-char", "aput-short"}
_2REG = {"move", "move/16", "move-object", "move-object/16", "move-wide", "neg-int", "not-int",
         "int-to-byte", "int-to-short", "int-to-char", "int-to-long", "long-to-int",
         "int-to-float", "float-to-int", "array-length"}
_2REG_FROM16 = {"move/from16", "move-object/from16", "move-wide/from16"}


def _reg(tok, param_base):
    tok = tok.strip()
    if not tok or tok[0] not in "vp":
        raise ValueError(f"kein Register: {tok!r}")
    k = int(tok[1:])
    return k if tok[0] == "v" else param_base + k


def _strip_comment(s):
    """Entfernt einen nachgestellten # Kommentar, aber nur ausserhalb von Strings."""
    in_q = False
    esc = False
    for i, ch in enumerate(s):
        if esc:
            esc = False; continue
        if ch == "\\":
            esc = True; continue
        if ch == '"':
            in_q = not in_q; continue
        if ch == "#" and not in_q:
            return s[:i].rstrip()
    return s


def _lit(tok):
    """Literal aus echtem Smali: Inline-Kommentar + Typ-Suffix (L/l/t/s/S) entfernen,
    Float/Double erkennen (Dezimalpunkt/Exponent/f/d), sonst hex/dez-Integer."""
    t = _strip_comment(tok).strip()
    if not t:
        return 0
    core = t
    while core and core[-1] in "LltsS":
        core = core[:-1]
    cl = core.lower()
    is_hex = cl.startswith("0x") or cl.startswith("-0x")
    if (not is_hex) and (("." in core) or (core.rstrip("fFdD") != core) or ("e" in cl)):
        try:
            return float(core.rstrip("fFdD"))
        except ValueError:
            pass
    try:
        return int(core, 0)
    except ValueError:
        try:
            return float(core.rstrip("fFdD"))
        except ValueError:
            return 0


def _split_top(rest):
    return [t.strip() for t in rest.split(",") if t.strip()]


def _parse_reglist(rest, param_base):
    # rest beginnt mit "{...}, ref"
    i = rest.index("}")
    inner = rest[1:i].strip()
    ref = rest[i + 1:].lstrip(", ").strip()
    regs = []
    if inner:
        if ".." in inner:
            a, b = [x.strip() for x in inner.split("..")]
            ra, rb = _reg(a, param_base), _reg(b, param_base)
            regs = list(range(ra, rb + 1))
        else:
            regs = [_reg(t, param_base) for t in inner.split(",") if t.strip()]
    return regs, ref


def _parse_methodref(ref):
    cls, right = ref.split("->", 1)
    name = right[:right.index("(")]
    sig = right[right.index("("):]
    return cls.strip(), name.strip(), sig.strip()


def _parse_fieldref(ref):
    cls, right = ref.split("->", 1)
    field = right.split(":", 1)[0]
    return cls.strip(), field.strip()


def _count_params(sig, static):
    inner = sig[sig.index("(") + 1:sig.index(")")]
    n = 0
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "[":
            i += 1
            continue
        if c == "L":
            i = inner.index(";", i) + 1
        else:
            i += 1
        n += 1
    return n + (0 if static else 1)

def _count_param_slots(sig, static):
    """Param-Register-Slots (Dalvik-ABI): J/D belegen 2 Slots, Objekt/Array/primitive je 1.
    Noetig fuer param_base: baksmali nummeriert pN ueber Slots (long p0 -> p0:p1, naechster -> p2)."""
    inner = sig[sig.index("(") + 1:sig.index(")")]
    slots = 0 if static else 1   # this = 1 Slot
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "[":
            i += 1
            while i < len(inner) and inner[i] == "[":
                i += 1
            if i < len(inner) and inner[i] == "L":
                i = inner.index(";", i) + 1
            else:
                i += 1
            slots += 1                     # Array -> Objekt-Ref, 1 Slot
        elif c == "L":
            i = inner.index(";", i) + 1
            slots += 1
        elif c in ("J", "D"):
            i += 1
            slots += 2                     # long/double -> 2 Slots (wide)
        else:
            i += 1
            slots += 1
    return slots


def _scan_body(body, base):
    # Erste Parser-Phase (autoritativ, aus parse_method herausgezogen): klassifiziert
    # den Methodenrumpf in Instruktionen (cleaned: (lineno,text)), Labels, Payloads.
    # base = 1-basierte Zeilennummer der ersten Rumpfzeile.
    cleaned = []   # (lineno, text)
    labels = {}
    payloads = {}  # payload-label -> {kind, ...}
    catches = []   # rohe .catch/.catchall-Zeilen (Handler-Tabelle nach Label-Aufloesung)
    count = 0
    block = None       # None | "annotation" | ("array"/"packed"/"sparse", label)
    last_label = None
    for lno, ln in enumerate(body, start=base):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if block is not None:
            kind = block[0] if isinstance(block, tuple) else block
            if kind == "annotation":
                if s.startswith(".end annotation"):
                    block = None
                continue
            if kind == "array":
                if s.startswith(".end array-data"):
                    block = None
                else:
                    payloads[block[1]]["values"].append(_arrval(s))
                continue
            if kind == "packed":
                if s.startswith(".end packed-switch"):
                    block = None
                elif s.startswith(":"):
                    payloads[block[1]]["targets"].append(s.split()[0])
                continue
            if kind == "sparse":
                if s.startswith(".end sparse-switch"):
                    block = None
                elif "->" in s:
                    kk, lab = s.split("->", 1)
                    payloads[block[1]]["keys"].append(_lit(kk.strip()))
                    payloads[block[1]]["targets"].append(lab.strip())
                continue
        if s.startswith(".annotation"):
            block = "annotation"; continue
        if s.startswith(".array-data"):
            payloads[last_label] = {"kind": "array", "values": []}
            block = ("array", last_label); continue
        if s.startswith(".packed-switch"):
            sp = s.split(None, 1)
            first = _lit(sp[1].strip()) if len(sp) > 1 else 0
            payloads[last_label] = {"kind": "packed", "first": first, "targets": []}
            block = ("packed", last_label); continue
        if s.startswith(".sparse-switch"):
            payloads[last_label] = {"kind": "sparse", "keys": [], "targets": []}
            block = ("sparse", last_label); continue
        if s.startswith(":"):
            lab = s.split()[0]
            labels[lab] = count
            last_label = lab
            continue
        if s.startswith(".registers") or s.startswith(".locals"):
            continue
        if s.startswith(".catch"):          # .catch UND .catchall -> Handler-Tabelle
            catches.append(s)
            continue
        if any(s.startswith(p) for p in _SKIP_PREFIX):
            continue
        if s.startswith("."):
            continue
        cleaned.append((lno, _strip_comment(s)))
        count += 1
    return cleaned, labels, payloads, catches


def source_map(text, method_name=None, method_sig=None):
    # pc(=Index) -> {lineno(1-basiert), op, text} der Zielmethode. Gleiche erste Phase
    # wie parse_method -> index-gleich zu MethodDef.insns. method_name (+ optional method_sig) waehlt die .method.
    lines = text.splitlines()
    mi = None
    if method_name is not None and method_sig:
        for idx, ln in enumerate(lines):
            m = _METHOD_RE.match(ln)
            if m and m.group('name') == method_name and m.group('sig') == method_sig:
                mi = idx
                break
    if mi is None:
        for idx, ln in enumerate(lines):
            m = _METHOD_RE.match(ln)
            if m and (method_name is None or m.group('name') == method_name):
                mi = idx
                break
    if mi is None and method_name is not None:
        for idx, ln in enumerate(lines):
            if _METHOD_RE.match(ln):
                mi = idx
                break
    if mi is None:
        return []
    body = []
    for ln in lines[mi + 1:]:
        if ln.strip().startswith('.end method'):
            break
        body.append(ln)
    cleaned, _labels, _payloads, _catches = _scan_body(body, mi + 2)
    out = []
    for pc, (lno, txt) in enumerate(cleaned):
        op = txt.split(None, 1)[0] if txt else ''
        out.append({'pc': pc, 'lineno': lno, 'op': op, 'text': txt})
    return out


def parse_method(text, cls="Lsmali/Adhoc;"):
    """Parst einen einzelnen ``.method``-Block in einen `MethodDef`."""
    lines = text.splitlines()
    mi = None
    for idx, ln in enumerate(lines):
        if _METHOD_RE.match(ln):
            mi = idx
            break
    if mi is None:
        raise ValueError("kein .method gefunden")
    m = _METHOD_RE.match(lines[mi])
    flags = m.group("flags")
    name = m.group("name")
    sig = m.group("sig")
    static = "static" in flags.split()
    nargs = _count_param_slots(sig, static)   # Register-Slots (wide=2) fuer korrektes param_base

    body = []
    for ln in lines[mi + 1:]:
        if ln.strip().startswith(".end method"):
            break
        body.append(ln)

    nregs = None
    for ln in body:
        s = ln.strip()
        if s.startswith(".registers"):
            nregs = int(s.split()[1]); break
        if s.startswith(".locals"):
            nregs = int(s.split()[1]) + nargs; break
    if nregs is None:
        nregs = nargs if nargs > 0 else 1
    param_base = max(nregs - nargs, 0)

    # 1. Durchlauf: Labels auf Instruktionsindex + Payloads (array-data/switch) sammeln;
    #   .annotation/Payload-Bloecke werden als GANZES uebersprungen (echter baksmali-Output).
    cleaned, labels, payloads, catches = _scan_body(body, mi + 2)

    # 2. Durchlauf: Instruktionen emittieren (Payloads fuer fill-array-data/switch verfuegbar)
    insns = []
    for lno, s in cleaned:
        insns.append(_emit(s, param_base, labels, lno, payloads))
    handlers = _build_handlers(catches, labels)
    return MethodDef(cls, name, sig, insns, nregs, nargs, static, handlers=handlers)


def _arrval(tok):
    """Ein .array-data-Wert: erstes Token, Typ-Suffix (t/s/S/L/l) entfernen, als int."""
    import re as _re
    t = tok.strip().split()[0] if tok.strip() else "0"
    m = _re.match(r"^(-?0x[0-9a-fA-F]+|-?[0-9]+)", t)
    return (_lit(m.group(1)) & ((1 << 64) - 1)) if m else 0


def _emit(s, param_base, labels, lno, payloads=None):
    parts = s.split(None, 1)
    op = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""

    if op == "nop":
        return ("nop",)
    if op == "return-void":
        return ("return-void",)
    if op in ("return", "return-wide", "return-object"):
        return (op, _reg(rest, param_base))
    if op in ("move-result", "move-result-object", "move-result-wide", "move-exception"):
        return (op, _reg(rest, param_base))

    # const-string zuerst (String kann Kommata enthalten)
    if op in ("const-string", "const-string/jumbo"):
        dst, txt = rest.split(",", 1)
        return ("const-string", _reg(dst, param_base), _unquote(txt.strip()))
    if op == "const-class":
        dst, t = _split_top(rest)
        return ("const-class", _reg(dst, param_base), t)
    if op in ("const", "const/4", "const/16", "const/high16", "const-wide", "const-wide/16",
              "const-wide/32", "const-wide/high16"):
        dst, lit = _split_top(rest)
        return ("const", _reg(dst, param_base), _lit(lit) & ((1 << 64) - 1))

    if "-to-" in op:
        dst, src = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(src, param_base))
    if op in ("cmp-long", "cmpl-float", "cmpg-float", "cmpl-double", "cmpg-double"):
        dst, ra, rb = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(ra, param_base), _reg(rb, param_base))
    if op in _2REG or op in _2REG_FROM16:
        dst, src = _split_top(rest)
        o = "move" if op.startswith("move") else op
        return (o, _reg(dst, param_base), _reg(src, param_base))

    if op.endswith("/2addr"):
        dst, src = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(src, param_base))
    if op.endswith("/lit8") or op.endswith("/lit16"):
        dst, src, lit = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(src, param_base), _lit(lit))
    if op in _3REG:
        dst, a, b = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(a, param_base), _reg(b, param_base))

    if op in ("if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le"):
        ra, rb, lab = _split_top(rest)
        return (op, _reg(ra, param_base), _reg(rb, param_base), labels[lab])
    if op in ("if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez"):
        ra, lab = _split_top(rest)
        return (op, _reg(ra, param_base), labels[lab])
    if op in ("goto", "goto/16", "goto/32"):
        return ("goto", labels[rest.strip()])

    if op == "new-instance":
        dst, t = _split_top(rest)
        return ("new-instance", _reg(dst, param_base), t)
    if op == "new-array":
        dst, sz, t = _split_top(rest)
        return ("new-array", _reg(dst, param_base), _reg(sz, param_base), t)
    if op == "check-cast":
        dst, t = _split_top(rest)
        return ("check-cast", _reg(dst, param_base), t)
    if op == "instance-of":
        dst, src, t = _split_top(rest)
        return ("instance-of", _reg(dst, param_base), _reg(src, param_base), t)

    if op.startswith("iget") or op.startswith("iput"):
        dst, obj, fref = _split_top(rest)
        cls, field = _parse_fieldref(fref)
        return (op, _reg(dst, param_base), _reg(obj, param_base), cls, field)
    if op.startswith("sget") or op.startswith("sput"):
        dst, fref = _split_top(rest)
        cls, field = _parse_fieldref(fref)
        return (op, _reg(dst, param_base), cls, field)

    if op.startswith("invoke-"):
        regs, ref = _parse_reglist(rest, param_base)
        cls, name, sig = _parse_methodref(ref)
        base = op.split("/")[0]   # /range vereinheitlichen
        return (base, tuple(regs), cls, name, sig)
    if op.startswith("filled-new-array"):
        regs, t = _parse_reglist(rest, param_base)
        return ("filled-new-array", tuple(regs), t)

    if op == "throw":
        return ("throw", _reg(rest, param_base))
    if op in ("monitor-enter", "monitor-exit"):
        return ("nop",)
    if op == "fill-array-data":
        dst, lab = _split_top(rest)
        pd = (payloads or {}).get(lab, {})
        return ("fill-array-data", _reg(dst, param_base), list(pd.get("values", [])))
    if op == "packed-switch":
        reg, lab = _split_top(rest)
        pd = (payloads or {}).get(lab, {})
        tgts = [labels[t] for t in pd.get("targets", []) if t in labels]
        return ("packed-switch", _reg(reg, param_base), int(pd.get("first", 0)), tgts)
    if op == "sparse-switch":
        reg, lab = _split_top(rest)
        pd = (payloads or {}).get(lab, {})
        tgts = [labels[t] for t in pd.get("targets", []) if t in labels]
        return ("sparse-switch", _reg(reg, param_base), list(pd.get("keys", [])), tgts)
    if op.startswith("neg-") or op.startswith("not-"):
        dst, src = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(src, param_base))
    if op in ("rsub-int", "rsub-int/lit16"):
        dst, src, lit = _split_top(rest)
        return ("rsub-int/lit16", _reg(dst, param_base), _reg(src, param_base), _lit(lit))
    if op.endswith("-float") or op.endswith("-double"):
        dst, ra, rb = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(ra, param_base), _reg(rb, param_base))
    if op.endswith("-long"):
        dst, ra, rb = _split_top(rest)
        return (op, _reg(dst, param_base), _reg(ra, param_base), _reg(rb, param_base))
    raise SmaliUnsupported(op, lno, s)


def _unquote(tok):
    """Smali-String entquoten MIT korrektem Escape-Decoding in EINEM Durchlauf.
    Wichtig: \\uXXXX (Unicode) wird dekodiert - baksmali schreibt alle nicht-druckbaren
    Zeichen als \\uXXXX; ohne dieses Decoding ist jeder String-Decrypt-Blob (z.B. $$d) falsch.
    Kein verkettetes .replace() (das wuerde Escapes doppelt verarbeiten)."""
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        tok = tok[1:-1]
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
              "\"": "\"", "'": "'", "\\": "\\", "0": "\x00"}
    out = []
    i, n = 0, len(tok)
    while i < n:
        c = tok[i]
        if c == "\\" and i + 1 < n:
            nx = tok[i + 1]
            if nx == "u" and i + 6 <= n:
                hx = tok[i + 2:i + 6]
                try:
                    out.append(chr(int(hx, 16))); i += 6; continue
                except ValueError:
                    pass
            if nx in simple:
                out.append(simple[nx]); i += 2; continue
            out.append(nx); i += 2; continue   # unbekanntes Escape: Zeichen beibehalten
        out.append(c); i += 1
    return "".join(out)


def _build_handlers(catches, labels):
    """Loest .catch/.catchall-Zeilen zu (start_pc, end_pc, exc_type|None, handler_pc) auf.
    Reihenfolge wie im Smali (spezifisch vor catchall). Labels sind try_start/try_end/handler."""
    out = []
    for line in catches:
        t = line.strip()
        if t.startswith(".catchall"):
            rest = t[len(".catchall"):].strip(); exc_type = None
        else:
            rest = t[len(".catch"):].strip()
            bi = rest.find("{")
            if bi < 0:
                continue
            exc_type = rest[:bi].strip() or None
            rest = rest[bi:]
        lb = rest.find("{"); rb = rest.find("}")
        if lb < 0 or rb < 0 or ".." not in rest[lb:rb]:
            continue
        sp = rest[lb + 1:rb].split("..")
        start_lab = sp[0].strip(); end_lab = sp[1].strip()
        tail = rest[rb + 1:].strip().split()
        handler_lab = tail[0] if tail else None
        if start_lab in labels and end_lab in labels and handler_lab in labels:
            out.append((labels[start_lab], labels[end_lab], exc_type, labels[handler_lab]))
    return out


import re as _re_fd
_FIELD_RE = _re_fd.compile(r"^\s*\.field\b(?P<flags>[^\n]*?)\s+(?P<name>[^\s:]+):(?P<type>\S+?)(?:\s*=\s*(?P<val>.+))?\s*$")

_SUPER_RE = _re_fd.compile(r"^\s*\.super\s+(?P<sup>L[^\s;]+;)\s*$", _re_fd.MULTILINE)
_IMPLEMENTS_RE = _re_fd.compile(r"^\s*\.implements\s+(?P<iface>L[^\s;]+;)\s*$", _re_fd.MULTILINE)

def parse_super(text):
    """Superklassen-Deskriptor aus `.super`. None wenn keiner (nur Ljava/lang/Object;)."""
    m = _SUPER_RE.search(text)
    return m.group("sup") if m else None


def parse_interfaces(text):
    """Liste aller Interface-Deskriptoren aus `.implements`."""
    return [m.group("iface") for m in _IMPLEMENTS_RE.finditer(text or "")]


def parse_field_defaults(text):
    """Statische Feld-Defaults aus `.field ... = <const>`-Deklarationen.
    Dalvik setzt diese VOR <clinit>. Rueckgabe {feldname: wert} (int/long/str; Objekte -> null/uebersprungen)."""
    out = {}
    for ln in text.splitlines():
        t = ln.strip()
        if not t.startswith(".field"):
            continue
        m = _FIELD_RE.match(t)
        if not m or "static" not in m.group("flags").split():
            continue
        name = m.group("name"); typ = m.group("type"); val = m.group("val")
        if val is None:
            continue                      # impliziter Default (0/null) -> statics-get liefert das
        val = val.strip()
        try:
            if typ == "Z" and val in ("true", "false"):
                out[name] = 1 if val == "true" else 0
            elif typ in ("I", "S", "B", "C", "Z"):
                out[name] = _u32_fd(_lit(val))
            elif typ == "J":
                out[name] = _lit(val) & ((1 << 64) - 1)
            elif typ in ("F", "D"):
                out[name] = float(val.rstrip("fFdD"))
            elif typ == "Ljava/lang/String;" and val.startswith("\""):
                out[name] = JString(_unquote(val[1:val.rindex(chr(34))]))
            # Objekt-Defaults (ausser String) sind null -> nichts setzen
        except Exception:
            pass
    return out


def _u32_fd(x):
    x &= 0xffffffff
    return x


def parse_class(text):
    """Parst alle ``.method``-Bloecke einer ``.class``-Datei. Rueckgabe: (clsname, [MethodDef])."""
    cm = _CLASS_RE.search(text)
    cls = cm.group("cls") if cm else "Lsmali/Adhoc;"
    mds = []
    for block in re.split(r"(?m)^\s*\.method\b", text)[1:]:
        md = parse_method(".method " + block, cls=cls)
        mds.append(md)
    return cls, mds


def load_into(vm, text):
    """Registriert alle Methoden einer ``.class``-Datei im ``vm``. Rueckgabe: Liste der MethodDefs."""
    cls, mds = parse_class(text)
    for md in mds:
        vm.register(md)
    return mds
