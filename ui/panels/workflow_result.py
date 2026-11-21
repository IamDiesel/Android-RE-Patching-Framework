# -*- coding: utf-8 -*-
"""workflow_result — generischer Renderer fuer die einheitliche Workflow-Ergebnisform.

Rein (tkinter-frei, testbar): ``build_result_sections(result)`` -> geordnete Liste von
Abschnitten ``{"title","lexer","text"}``. Die Panels rendern jeden Abschnitt in ein
HighlightedText-Pane mit dem angegebenen Lexer. So gibt es EINEN Render-Pfad fuer alle
Workflows (Devirt, Gate 2, Ausfuehren, …) statt acht Reiter-Sonderfaelle.
"""
from __future__ import annotations

from typing import List, Dict


def _devirt_sections(r: dict) -> List[Dict]:
    out = []
    head = "Entry %s (%s)  stop=%s  |  %s Dispatches · %s Handler · %s Branch-Points" % (
        r.get("entry"), r.get("fn"), r.get("stop"),
        r.get("n_dispatches", 0), r.get("n_unique", 0), r.get("n_branch_points", 0))
    if r.get("inputs"):
        head += "\nEingaben: " + ", ".join(repr(x) for x in r.get("inputs"))
    if r.get("note"):
        head += "\n" + str(r.get("note"))
    out.append({"title": "Zusammenfassung", "lexer": "text", "text": head})

    bps = r.get("branch_points") or []
    if bps:
        lines = []
        for bp in bps:
            succ = ", ".join("%s (x%s/r%s)" % (s["target"], s["count"], s["runs"])
                             for s in bp.get("successors", []))
            lines.append("%s  ->  %s" % (bp["handler"], succ))
            for row in bp.get("index_table", []):
                lines.append("       %s -> %s  (x%s, r%s)"
                             % (row["index"], row["target"], row["count"], row["runs"]))
        out.append({"title": "Branch-CFG", "lexer": "pseudocode", "text": "\n".join(lines)})

    mode = r.get("lift_mode")
    if mode == "runnable" and r.get("python"):
        out.append({"title": "Python [ausfuehrbar]", "lexer": "python", "text": r["python"]})
    elif r.get("pseudocode"):
        out.append({"title": "Pseudocode", "lexer": "pseudocode", "text": r["pseudocode"]})
    elif mode in ("skipped_large", "skipped_off"):
        out.append({"title": "Python-Lift", "lexer": "text",
                    "text": "uebersprungen (%s; Spur=%s Instr., lift_max=%s)"
                            % (mode, r.get("lift_trace_len"), r.get("lift_max"))})

    seq = r.get("sequence")
    if seq:
        out.append({"title": "Sequenz", "lexer": "pseudocode", "text": " -> ".join(seq)})
    return out


def _gate2_sections(r: dict) -> List[Dict]:
    out = []
    out.append({"title": "Zusammenfassung", "lexer": "text",
                "text": "Entry %s  stop=%s  |  %s Syscalls, %s Dispatches uebersprungen"
                        % (r.get("entry"), r.get("stop"),
                           len(r.get("syscalls") or []), r.get("n_skipped", 0))})
    rows = r.get("syscalls") or []
    if rows:
        lines = ["%-18s %-10s %s" % ("Syscall", "pc", "Argument")]
        for s in rows:
            lines.append("%-18s %-10s %s" % (s.get("name", s.get("nr")),
                                             s.get("pc", ""), s.get("arg", "")))
        out.append({"title": "Detection-Syscalls", "lexer": "text", "text": "\n".join(lines)})
    return out


def _run_sections(r):
    out = []
    head = "Entry %s  ret=%s  sp=%s  lr=%s" % (r.get("entry"), r.get("ret"),
                                               r.get("sp"), r.get("lr"))
    fl = r.get("flags")
    if isinstance(fl, dict) and fl:
        head += "\nFlags: " + ", ".join("%s=%s" % (k, v) for k, v in fl.items())
    xs = r.get("x0_7") or []
    if xs:
        head += "\n" + "  ".join("x%d=%s" % (i, v) for i, v in enumerate(xs))
    out.append({"title": "Register / Rueckgabe", "lexer": "text", "text": head})
    ob = r.get("out")
    if ob:
        lines = ["%s: %s" % (k, v) for k, v in ob.items()] if isinstance(ob, dict) else [str(ob)]
        out.append({"title": "Out-Puffer", "lexer": "text", "text": "\n".join(lines)})
    rd = r.get("read")
    if rd:
        lines = ["%s: %s" % (k, v) for k, v in rd.items()] if isinstance(rd, dict) else [str(rd)]
        out.append({"title": "Gelesen", "lexer": "text", "text": "\n".join(lines)})
    return out


def _jni_sections(r):
    out = []
    head = "stop=%s  pc=%s  steps=%s  |  relocs=%s  imports=%s  RegisterNatives=%s" % (
        r.get("stop"), r.get("pc"), r.get("steps"),
        r.get("relocs"), r.get("imports"), len(r.get("natives") or []))
    if r.get("jni"):
        head += "\n" + str(r.get("jni"))
    if r.get("error"):
        head += "\nFehler: " + str(r.get("error"))
    out.append({"title": "Zusammenfassung", "lexer": "text", "text": head})
    nats = r.get("natives") or []
    if nats:
        lines = []
        for n in nats:
            if isinstance(n, dict):
                lines.append("%s.%s%s  ->  %s" % (
                    n.get("cls", n.get("class", "?")), n.get("name", "?"),
                    n.get("sig", n.get("signature", "")),
                    n.get("addr", n.get("target", "?"))))
            else:
                lines.append(str(n))
        out.append({"title": "RegisterNatives", "lexer": "pseudocode", "text": "\n".join(lines)})
    calls = r.get("calls_head") or r.get("calls")
    if calls:
        out.append({"title": "JNI-Calls", "lexer": "text",
                    "text": "\n".join(str(c) for c in calls)})
    return out


def _open_sections(r):
    out = []
    ld = r.get("loaded") or {}
    ix = r.get("indexed") or {}
    head = "Lib: %s" % r.get("path")
    if isinstance(ld, dict) and ld:
        head += "\nEntry %s  ·  %s Segmente  ·  %s Symbole  ·  %s Relocs%s" % (
            ld.get("entry"), len(ld.get("segmente") or []), ld.get("symbole_gesamt"),
            ld.get("relocs"),
            "  ·  SIEHT GEPACKT AUS (erst native.unpack!)" if ld.get("sieht_gepackt_aus") else "")
    if isinstance(ix, dict) and ix:
        head += "\nIndex: %s Instruktionen · %s Kanten · %s Symbole (%s)" % (
            ix.get("instruktionen"), ix.get("kanten"), ix.get("symbole"), ix.get("modus"))
    out.append({"title": "Uebersicht", "lexer": "text", "text": head})
    segs = (ld.get("segmente") if isinstance(ld, dict) else None) or []
    if segs:
        lines = ["%-12s %-12s %-12s %s" % ("vaddr", "memsz", "filesz", "perms")]
        for s in segs:
            lines.append("%-12s %-12s %-12s %s" % (s.get("vaddr"), s.get("memsz"),
                                                   s.get("filesz"), s.get("perms")))
        out.append({"title": "Segmente", "lexer": "text", "text": "\n".join(lines)})
    syms = (ld.get("symbole_auszug") if isinstance(ld, dict) else None) or []
    if syms:
        lines = ["%s  %s  (%s)" % (s.get("addr"), s.get("name"), s.get("size")) for s in syms]
        out.append({"title": "Symbole (Auszug)", "lexer": "text", "text": "\n".join(lines)})
    return out


def build_result_sections(result: dict) -> List[Dict]:
    """Rein: mappt ein Workflow-Ergebnis auf geordnete Abschnitte {title,lexer,text}.

    Erkennt die Form am Inhalt (Devirt: branch_points/sequence; Gate 2: syscalls).
    Fallback: Schluessel/Werte als Text.
    """
    r = result or {}
    if not r.get("ok", True):
        return [{"title": "Fehler", "lexer": "text",
                 "text": str(r.get("hinweis") or r.get("error") or "unbekannter Fehler")}]
    wf = r.get("workflow")
    if wf == "devirt" or any(k in r for k in ("branch_points", "sequence", "n_dispatches")):
        return _devirt_sections(r)
    if wf == "gate2" or "syscalls" in r:
        return _gate2_sections(r)
    if wf == "jni" or "natives" in r:
        return _jni_sections(r)
    if wf == "open_lib" or ("loaded" in r and "indexed" in r):
        return _open_sections(r)
    if wf == "run_fn" or ("ret" in r and "x0_7" in r):
        return _run_sections(r)
    # Fallback: generisch
    lines = []
    for k, v in r.items():
        sv = str(v)
        if len(sv) > 400:
            sv = sv[:400] + " …"
        lines.append("%s: %s" % (k, sv))
    return [{"title": "Ergebnis", "lexer": "text", "text": "\n".join(lines)}]
