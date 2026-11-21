# -*- coding: utf-8 -*-
"""native_devirt_service — Devirtualizer fuer OLLVM-control-flow-flattened Funktionen (libdb0c).

Prinzip: der CFF-Dispatch-Tracer (native_trace_service.dispatch_trace_*) zeichnet pro Lauf die
geordnete Folge der Dispatch-Entscheidungen auf — jeder ``val`` ist die Adresse des Handler-
Blocks, der als Naechstes via ``blr`` ausgefuehrt wird. Diese Folge IST die entflachte
Ausfuehrung. Dieser Service extrahiert daraus:
  - die lineare Handler-Sequenz (entflachter Lauf),
  - die eindeutigen Handler-Bloecke (die echten Basic Blocks der Funktion) samt Disassembly,
  - einen einfachen Nachfolger-Graphen (welcher Block folgt auf welchen — Grundlage fuer v2-CFG).

SOA: stateless; die Engine/der Dispatch-Log kommen vom Aufrufer (MCP/GUI/Probe).
"""
from __future__ import annotations

from typing import List, Optional

_M64 = (1 << 64) - 1
_W32 = (1 << 32) - 1
_TERM = ("b", "bl", "blr", "br", "ret", "cbz", "cbnz", "tbz", "tbnz", "brk", "svc", "hlt")


class NativeDevirtService:
    def __init__(self, publish: bool = True):
        self.publish = publish

    def _disasm_block(self, eng, pc: int, maxn: int = 48) -> List[dict]:
        """Straight-line Disassembly ab pc bis zum ersten Terminator (inklusive)."""
        out: List[dict] = []
        p = int(pc) & _M64
        for _ in range(maxn):
            try:
                ins = eng.decode_at(p)
            except Exception as ex:
                out.append({"addr": f"0x{p:x}", "text": f"<decode {ex!r}>"})
                break
            out.append({"addr": f"0x{p:x}", "text": f"{ins.mnemonic} {ins.op_str}".strip()})
            m = (ins.mnemonic or "").lower()
            p += ins.size or 4
            if m in _TERM or m.startswith("b."):
                break
        return out

    def deflatten(self, eng, dispatch_log: list, max_unique: int = 64, maxn: int = 48) -> dict:
        """Entflacht aus dem Dispatch-Log. Liefert Sequenz, eindeutige Bloecke (+Disasm) und
        einen Nachfolger-Graphen. ``val==0`` (leere Slots / Trap) werden als 'TRAP' markiert."""
        seq: List[int] = []
        traps = 0
        for e in dispatch_log or []:
            v = int(e.get("val", 0)) & _M64
            if v == 0:
                traps += 1
                seq.append(0)
            else:
                seq.append(v)

        # eindeutige Handler in Erst-Sicht-Reihenfolge (ohne 0/Trap)
        uniq: List[int] = []
        seen = set()
        for v in seq:
            if v and v not in seen:
                seen.add(v)
                uniq.append(v)

        # Nachfolger-Graph (val -> Counter der nachfolgenden vals)
        succ = {}
        for a, b in zip(seq, seq[1:]):
            if not a:
                continue
            d = succ.setdefault(a, {})
            key = f"0x{b:x}" if b else "TRAP"
            d[key] = d.get(key, 0) + 1

        blocks = {}
        for h in uniq[:max_unique]:
            blocks[f"0x{h:x}"] = self._disasm_block(eng, h, maxn)

        return {
            "ok": True,
            "n_dispatches": len(seq),
            "n_unique": len(uniq),
            "n_traps": traps,
            "sequence": [f"0x{v:x}" if v else "TRAP" for v in seq],
            "unique_handlers": [f"0x{v:x}" for v in uniq],
            "blocks": blocks,
            "successors": {f"0x{k:x}": v for k, v in succ.items()},
            "note": (f"{len(uniq)} eindeutige Handler-Bloecke entflacht aus {len(seq)} Dispatches"
                     + (f"; {traps} Trap(s) (leerer Slot)" if traps else "")),
        }

    def deflatten_multi(self, eng, dispatch_logs: list, max_unique: int = 64,
                        maxn: int = 48) -> dict:
        """v2 Branch-CFG: merged N Dispatch-Logs (mehrere Laeufe mit variierten Eingaben) zu
        einem Nachfolger-Graphen mit Fan-out. Jede Kante A->B traegt Haeufigkeit (count) und
        Lauf-Abdeckung (runs = in wievielen distinct Laeufen sie auftrat). Ein Handler mit >=2
        verschiedenen (nicht-TRAP) Nachfolgern ist ein **Branch-Point**.

        Index-Entscheidungstabelle: die ``(idx1,col)`` des *naechsten* Dispatches ist die
        Entscheidung, die der aktuelle Handler via ``const - statekey`` getroffen hat -> so wird
        sichtbar, welcher Vtable-Index welchen Nachfolger waehlt.
        """
        logs = [l or [] for l in (dispatch_logs or [])]
        n_runs = len(logs)
        runs_seq = [[int(e.get("val", 0)) & _M64 for e in log] for log in logs]

        # Union der eindeutigen Handler (Erst-Sicht-Reihenfolge ueber alle Laeufe, ohne TRAP)
        uniq: List[int] = []
        seen = set()
        for s in runs_seq:
            for v in s:
                if v and v not in seen:
                    seen.add(v); uniq.append(v)

        # Nachfolger-Graph: succ[A][Bkey] = {count, runs, decisions{"[i][c]":count}}
        succ = {}
        edge_runs = {}
        for ri, log in enumerate(logs):
            for i in range(len(log) - 1):
                a = int(log[i].get("val", 0)) & _M64
                if not a:
                    continue
                ne = log[i + 1]
                b = int(ne.get("val", 0)) & _M64
                bkey = f"0x{b:x}" if b else "TRAP"
                cell = succ.setdefault(a, {}).setdefault(
                    bkey, {"count": 0, "runs": 0, "decisions": {}})
                cell["count"] += 1
                edge_runs.setdefault((a, bkey), set()).add(ri)
                i1 = ne.get("idx1"); c0 = ne.get("col")
                if i1 is not None and c0 is not None:
                    dk = f"[{i1}][{c0}]"
                    cell["decisions"][dk] = cell["decisions"].get(dk, 0) + 1
        for (a, bkey), rs in edge_runs.items():
            succ[a][bkey]["runs"] = len(rs)

        # Branch-Points: Handler mit >=2 distinct nicht-TRAP Nachfolgern
        branch_points = []
        for a, d in succ.items():
            nontrap = [k for k in d if k != "TRAP"]
            if len(nontrap) >= 2:
                table = []
                for bkey, cell in d.items():
                    for dk, cnt in cell["decisions"].items():
                        table.append({"index": dk, "target": bkey,
                                      "count": cnt, "runs": cell["runs"]})
                branch_points.append({
                    "handler": f"0x{a:x}",
                    "n_successors": len(nontrap),
                    "successors": [{"target": k, "count": v["count"], "runs": v["runs"]}
                                   for k, v in sorted(d.items())],
                    "index_table": sorted(table, key=lambda r: r["index"]),
                })
        branch_points.sort(key=lambda bp: (-bp["n_successors"], bp["handler"]))

        blocks = {}
        for h in uniq[:max_unique]:
            blocks[f"0x{h:x}"] = self._disasm_block(eng, h, maxn)

        rep = runs_seq[0] if runs_seq else []
        n_disp = sum(len(s) for s in runs_seq)
        traps_total = sum(1 for s in runs_seq for v in s if not v)
        return {
            "ok": True,
            "n_runs": n_runs,
            "n_dispatches": n_disp,
            "n_unique": len(uniq),
            "n_traps": traps_total,
            "n_branch_points": len(branch_points),
            "branch_points": branch_points,
            "unique_handlers": [f"0x{v:x}" for v in uniq],
            "successors": {f"0x{k:x}": {bk: {"count": c["count"], "runs": c["runs"]}
                                        for bk, c in d.items()}
                           for k, d in succ.items()},
            "blocks": blocks,
            "sequence": [f"0x{v:x}" if v else "TRAP" for v in rep],
            "note": (f"{len(uniq)} Handler, {len(branch_points)} Branch-Points "
                     f"aus {n_runs} Lauf/Laeufen ({n_disp} Dispatches"
                     + (f", {traps_total} Trap(s)" if traps_total else "") + ")"),
        }

    # ===================== Hybrid: statisch ⊕ getracter Zustand (N5/M7) =====================
    @staticmethod
    def _hx(a) -> str:
        """Adresse -> '0x..'-Kleinschreibung (akzeptiert int oder String)."""
        if isinstance(a, str):
            s = a.strip()
            if s.upper() == "TRAP":
                return "TRAP"
            return s.lower() if s.lower().startswith("0x") else ("0x%x" % int(s, 0))
        return "0x%x" % (int(a) & _M64)

    def _traced_targets(self, succ_entry) -> List[dict]:
        """Nicht-TRAP-Nachfolger aus einem successors-Eintrag extrahieren
        (deflatten: {bkey:int} | deflatten_multi: {bkey:{count,runs}})."""
        out = []
        for bk, v in (succ_entry or {}).items():
            if bk == "TRAP":
                continue
            cnt = v if isinstance(v, int) else int((v or {}).get("count", 0))
            out.append({"target": self._hx(bk), "count": cnt})
        return sorted(out, key=lambda r: -r["count"])

    def merge_hybrid(self, static_res: dict, traced_res: Optional[dict]) -> dict:
        """Verschmilzt den statischen Dispatch-Graphen (devirt_static) mit einem getracten
        Nachfolger-Graphen (deflatten/deflatten_multi) zu einer Hybrid-CFG **mit Provenienz**.
        Generisch (jede OLLVM `const-key`-Funktion). Kein Lauf hier — reine Merge-Logik:
          - statische Kanten bleiben erhalten (`static_successors`),
          - der Trace liefert die real genommenen Nachfolger (`traced_successors`) — damit werden
            register-abhaengige **Branch-Points** und statische **Traps/Luecken** aufgeloest,
          - `reached` zeigt die Trace-Abdeckung je Block, `resolved_traps` die vom Trace
            ueberwundenen statischen Luecken.
        """
        static_res = static_res or {}
        s_blocks_raw = static_res.get("blocks", {}) or {}
        s_blocks = {self._hx(k): v for k, v in s_blocks_raw.items()}
        t_succ_raw = (traced_res or {}).get("successors", {}) or {}
        t_succ = {self._hx(k): v for k, v in t_succ_raw.items()}
        t_uh = {self._hx(x) for x in ((traced_res or {}).get("unique_handlers", []) or [])}
        t_seq = {self._hx(x) for x in ((traced_res or {}).get("sequence", []) or []) if x != "TRAP"}
        t_reached = set(t_succ.keys()) | t_uh | t_seq

        def _bp_addr(b):
            return self._hx(b["handler"] if isinstance(b, dict) and "handler" in b else b)
        s_trap_set = {self._hx(t) for t in (static_res.get("traps", []) or [])}
        s_bp_set = {_bp_addr(b) for b in (static_res.get("branch_points", []) or [])}

        merged = {}
        resolved_traps = []
        for addr in sorted(set(s_blocks.keys()) | set(t_succ.keys())):
            sb = s_blocks.get(addr) or {}
            node_type = sb.get("node_type") or ("traced-only" if addr not in s_blocks else "unknown")
            s_succ = [self._hx(x) for x in (sb.get("successors", []) or [])]
            t_targets = self._traced_targets(t_succ.get(addr))
            t_addrs = [t["target"] for t in t_targets]
            reached = addr in t_reached
            is_gap = (addr in s_trap_set) or (node_type in ("branch", "jump", "unknown")) or (not s_succ)
            resolved = bool(is_gap and t_addrs)
            if addr in s_trap_set and t_addrs:
                resolved_traps.append({"block": addr, "traced_successors": t_addrs})
            merged[addr] = {
                "node_type": node_type,
                "reached": reached,
                "is_branch_point": addr in s_bp_set,
                "static_successors": s_succ,
                "traced_successors": t_targets,
                "resolved": resolved,
                "index": sb.get("index"),
            }

        n_static = len(s_blocks)
        n_reached = sum(1 for a in s_blocks if a in t_reached)
        return {
            "ok": True,
            "entry": static_res.get("entry"),
            "n_static_blocks": n_static,
            "n_traced_reached": n_reached,
            "coverage": round(n_reached / n_static, 3) if n_static else None,
            "n_static_branch_points": len(s_bp_set),
            "n_static_traps": len(s_trap_set),
            "resolved_traps": resolved_traps,
            "n_resolved_traps": len(resolved_traps),
            "blocks": merged,
            "note": (f"Hybrid: {n_reached}/{n_static} statische Bloecke vom Trace abgedeckt; "
                     f"{len(resolved_traps)} statische Trap(s)/Luecke(n) durch Trace aufgeloest"),
        }

    def devirt_hybrid(self, eng, entry, dispatch_logs=None, key=None, max_blocks: int = 400,
                      max_unique: int = 64) -> dict:
        """Hybrid-Devirt: statischer Graph (`devirt_static`) ⊕ getracter Branch-Zustand
        (`deflatten_multi` der uebergebenen `dispatch_logs`). `dispatch_logs` = Liste von
        Dispatch-Logs (vom `dispatch_trace`, ein oder mehrere Laeufe). Ohne Logs: nur statisch."""
        static_res = self.devirt_static(eng, entry, key=key, max_blocks=max_blocks)
        traced_res = None
        if dispatch_logs:
            traced_res = self.deflatten_multi(eng, dispatch_logs, max_unique=max_unique)
        out = self.merge_hybrid(static_res, traced_res)
        out["static"] = {k: static_res.get(k) for k in ("n_blocks", "n_branch_points", "n_traps")}
        out["traced"] = ({k: traced_res.get(k) for k in
                          ("n_runs", "n_dispatches", "n_branch_points", "n_traps")}
                         if traced_res else None)
        return out

    # ===================== Statischer Index-Vorpass (D-stat) =====================
    # Faithful AArch64-Block-Evaluator: rechnet den OLLVM-Dispatch eines Handler-Blocks
    # statisch aus (ohne Lauf der Zielfunktion). Modelliert die Opaque Predicates direkt
    # (orr/and/eor/add/sub/neg/lsl/...), loest das Zustandsorakel (getter -> *(state_slot))
    # auf und liest Tabelle/Nachfolger aus dem Emulator-Speicher.
    @staticmethod
    def _rtok(t):
        """Register-Token -> (idx|'zr'|'sp'|None, is32)."""
        t = (t or "").strip().lower()
        if t in ("xzr", "wzr"):
            return ("zr", t[0] == "w")
        if t in ("sp", "wsp"):
            return ("sp", t[0] == "w")
        if len(t) >= 2 and t[0] in "xw" and t[1:].isdigit():
            return (int(t[1:]), t[0] == "w")
        return (None, False)

    @staticmethod
    def _imm(tok):
        tok = (tok or "").strip()
        if tok.startswith("#"):
            tok = tok[1:]
        try:
            return int(tok, 0)
        except Exception:
            return None

    def _walk_dispatch(self, eng, pc, maxn=96):
        """Linear ab pc; bl/blr (Calls) laufen durch, Stopp bei br/ret/b (Blockende)."""
        out = []
        p = int(pc) & _M64
        for _ in range(maxn):
            try:
                ins = eng.decode_at(p)
            except Exception as ex:
                out.append({"addr": p, "mn": "<decode>", "ops": "", "size": 4, "err": repr(ex)})
                break
            mn = (ins.mnemonic or "").lower()
            out.append({"addr": p, "mn": mn, "ops": ins.op_str or "", "size": ins.size or 4})
            p += ins.size or 4
            if mn in ("br", "ret", "b"):
                break
        return out

    def _read_u(self, eng, ea, sz):
        raw = eng.cpu.m.rb(int(ea) & _M64, sz)
        return int.from_bytes(raw, "little") if raw else 0

    def _resolve_getter_key(self, eng, tgt, key):
        """Getter-Leaf (adrp xB,#page; ldr x0,[xB,#off]; ret) aufloesen -> (keyval, key_slot)."""
        if key is not None:
            return (key & _M64, None)
        if not tgt:
            return (None, None)
        A = {}
        p = int(tgt) & _M64
        for _ in range(8):
            try:
                ins = eng.decode_at(p)
            except Exception:
                break
            mn = (ins.mnemonic or "").lower()
            ops = [o.strip() for o in (ins.op_str or "").split(",")]
            if mn == "adrp" and len(ops) == 2:
                d = self._rtok(ops[0])[0]
                v = self._imm(ops[1])
                if isinstance(d, int) and v is not None:
                    A[d] = v
            elif mn == "add" and len(ops) == 3 and ops[2].startswith("#"):
                b = self._rtok(ops[1])[0]
                d = self._rtok(ops[0])[0]
                if b in A and self._imm(ops[2]) is not None:
                    A[d] = A[b] + self._imm(ops[2])
            elif mn == "ldr" and len(ops) >= 2 and self._rtok(ops[0])[0] == 0 and ops[1].startswith("["):
                inside = ",".join(ops[1:])
                inside = inside[inside.index("[") + 1:inside.rindex("]")]
                pp = [q.strip() for q in inside.split(",")]
                base = self._rtok(pp[0])[0]
                off = self._imm(pp[1]) if len(pp) >= 2 and pp[1].startswith("#") else 0
                if base in A:
                    ks = A[base] + (off or 0)
                    return (self._read_u(eng, ks, 8), ks)
            p += ins.size or 4
            if mn == "ret":
                break
        return (None, None)

    def resolve_index_static(self, eng, block_addr, key=None, maxn=96):
        """Loest den Dispatch-Nachfolger eines Handler-Blocks statisch auf.
        Rueckgabe: {ok, matched, node_type, block, table, stride, const, key, key_slot,
        getter_slot, index, successor | candidates, reason?, note}.
        node_type: 'index' (const-getter -> Tabelle), 'branch' (csel, 2 Kandidaten),
        'direct' (b #imm), 'return', 'unknown'. key optional (sonst Getter-Leaf-Aufloesung)."""
        rows = self._walk_dispatch(eng, block_addr, maxn)
        R = {}
        slot_of = {}      # regidx -> Adresse, aus der der Wert via ldr [base] geladen wurde
        getter_slot = None
        key_used = None
        key_slot = None
        table = None
        stride = None
        const_cands = []
        csel_cands = []
        note = []

        def rd(tok):
            idx, is32 = self._rtok(tok)
            if idx == "zr":
                return 0
            if not isinstance(idx, int):
                return None
            v = R.get(idx)
            if v is None:
                return None
            return (v & _W32) if is32 else (v & _M64)

        def wr(tok, val):
            idx, is32 = self._rtok(tok)
            if not isinstance(idx, int):
                return
            if val is None:
                R.pop(idx, None)
            else:
                R[idx] = (val & _W32) if is32 else (val & _M64)

        for r in rows:
            mn = r["mn"]
            ops = [o.strip() for o in r["ops"].split(",")] if r["ops"] else []
            try:
                if mn == "adrp" and len(ops) == 2:
                    wr(ops[0], self._imm(ops[1]))
                elif mn == "adr" and len(ops) == 2:
                    wr(ops[0], self._imm(ops[1]))
                elif mn in ("mov", "movz") and len(ops) == 2:
                    v = self._imm(ops[1])
                    wr(ops[0], v if v is not None else rd(ops[1]))
                    if v is not None and v > 0x10000:
                        const_cands.append(v)
                elif mn == "movk" and len(ops) >= 2:
                    base = rd(ops[0]) or 0
                    sh = 0
                    if len(ops) == 3 and "lsl" in ops[2]:
                        sh = self._imm(ops[2].split()[-1]) or 0
                    v = self._imm(ops[1])
                    if v is not None:
                        mask = 0xffff << sh
                        nv = (base & ~mask) | ((v & 0xffff) << sh)
                        wr(ops[0], nv)
                        if nv > 0x10000:
                            const_cands.append(nv)
                elif mn in ("add", "sub") and len(ops) == 3:
                    a = rd(ops[1])
                    b = self._imm(ops[2])
                    if b is None:
                        b = rd(ops[2])
                    wr(ops[0], (a + b if mn == "add" else a - b) if None not in (a, b) else None)
                elif mn == "neg" and len(ops) == 2:
                    a = rd(ops[1])
                    wr(ops[0], (-a) if a is not None else None)
                elif mn in ("orr", "and", "eor", "orn", "bic", "eon") and len(ops) == 3:
                    a = rd(ops[1])
                    b = self._imm(ops[2])
                    if b is None:
                        b = rd(ops[2])
                    if None not in (a, b):
                        wr(ops[0], {"orr": a | b, "and": a & b, "eor": a ^ b,
                                    "orn": a | (~b), "bic": a & (~b), "eon": a ^ (~b)}[mn])
                    else:
                        wr(ops[0], None)
                elif mn in ("lsl", "lsr", "asr") and len(ops) == 3:
                    a = rd(ops[1])
                    s = self._imm(ops[2])
                    if s is None:
                        s = rd(ops[2])
                    if None not in (a, s):
                        wr(ops[0], a << s if mn == "lsl" else (a & _M64) >> s if mn == "lsr" else a >> s)
                    else:
                        wr(ops[0], None)
                elif mn == "mul" and len(ops) == 3:
                    a = rd(ops[1]); b = rd(ops[2])
                    wr(ops[0], a * b if None not in (a, b) else None)
                elif mn == "madd" and len(ops) == 4:
                    n = rd(ops[1]); mm = rd(ops[2]); a = rd(ops[3])
                    if a is not None:
                        table = a
                    if mm is not None:
                        stride = mm
                    wr(ops[0], (n * mm + a) if None not in (n, mm, a) else None)
                elif mn in ("smaddl", "umaddl", "smsubl", "umsubl") and len(ops) == 4:
                    # 2D-Vtable-Dispatch (0x137740): Xd = Xa +/- sxtw(Wn)*sxtw(Wm)
                    n = rd(ops[1]); mm = rd(ops[2]); a = rd(ops[3])
                    if None not in (n, mm):
                        n &= _W32; mm &= _W32
                        if mn[0] == "s":
                            if n & 0x80000000:
                                n -= (1 << 32)
                            if mm & 0x80000000:
                                mm -= (1 << 32)
                    if a is not None:
                        table = a          # Vtable-Basis (z.B. 0x137740)
                    if mm is not None:
                        stride = mm         # Zeilen-Stride (z.B. 0x7f0)
                    if None not in (n, mm, a):
                        prod = n * mm
                        wr(ops[0], a + prod if "add" in mn else a - prod)
                    else:
                        wr(ops[0], None)
                elif mn == "msub" and len(ops) == 4:
                    n = rd(ops[1]); mm = rd(ops[2]); a = rd(ops[3])
                    wr(ops[0], (a - n * mm) if None not in (n, mm, a) else None)
                elif mn in ("sxtw", "uxtw") and len(ops) == 2:
                    a = rd(ops[1])
                    if a is not None:
                        a &= _W32
                        if mn == "sxtw" and (a & 0x80000000):
                            a -= (1 << 32)
                        wr(ops[0], a)
                    else:
                        wr(ops[0], None)
                elif mn in ("ubfx", "sbfx", "ubfiz", "sbfiz") and len(ops) == 4:
                    a = rd(ops[1]); lsb = self._imm(ops[2]); width = self._imm(ops[3])
                    if None not in (a, lsb, width) and width > 0:
                        if mn in ("ubfx", "sbfx"):
                            v = (a >> lsb) & ((1 << width) - 1)
                            if mn == "sbfx" and (v & (1 << (width - 1))):
                                v -= (1 << width)
                        else:
                            v = (a & ((1 << width) - 1)) << lsb
                        wr(ops[0], v)
                    else:
                        wr(ops[0], None)
                elif mn == "cset" and len(ops) == 2:
                    wr(ops[0], None)  # flag-abhaengig -> unbekannt (im Index-Pfad ungenutzt)
                elif mn in ("csel", "csinc", "csinv", "cneg", "csneg") and len(ops) >= 3:
                    csel_cands = [rd(ops[1]), rd(ops[2])]
                    wr(ops[0], None)
                elif mn in ("cmp", "cmn", "tst", "ccmp", "nop"):
                    pass  # Flags nicht modelliert (fuer Dispatch-Ziel irrelevant)
                elif mn in ("ldr", "ldrsw", "ldrb", "ldrh") and len(ops) >= 2 and ops[1].startswith("["):
                    inside = ",".join(ops[1:])
                    inside = inside[inside.index("[") + 1:inside.rindex("]")]
                    parts = [p.strip() for p in inside.split(",")]
                    base = rd(parts[0])
                    ea = base
                    if base is not None and len(parts) >= 2:
                        if parts[1].startswith("#"):
                            ea = base + (self._imm(parts[1]) or 0)
                        else:
                            ri = rd(parts[1]); sh = 0; ext = None
                            if len(parts) >= 3:
                                seg = parts[2].split()
                                ext = seg[0].lower()
                                if len(seg) > 1:
                                    sh = self._imm(seg[1]) or 0
                            if ri is not None:
                                if ext == "sxtw":
                                    ri &= _W32
                                    if ri & 0x80000000:
                                        ri -= (1 << 32)
                                ea = base + (ri << sh)
                            else:
                                ea = None
                    if len(parts) == 1 and base is not None:
                        gidx = self._rtok(ops[0])[0]
                        if isinstance(gidx, int):
                            slot_of[gidx] = base
                    if ea is None:
                        wr(ops[0], None)
                    else:
                        sz = 8 if (mn == "ldr" and not self._rtok(ops[0])[1]) else \
                             4 if mn in ("ldr", "ldrsw") else 2 if mn == "ldrh" else 1
                        val = self._read_u(eng, ea, sz)
                        if mn == "ldrsw" and (val & 0x80000000):
                            val -= (1 << 32)
                        wr(ops[0], val)
                elif mn in ("bl", "blr"):
                    if mn == "blr":
                        gi = self._rtok(ops[0])[0]
                        if isinstance(gi, int) and gi in slot_of:
                            getter_slot = slot_of[gi]
                        tgt = rd(ops[0])
                    else:
                        tgt = self._imm(ops[0])
                    kv, ks = self._resolve_getter_key(eng, tgt, key)
                    if kv is not None:
                        R[0] = kv & _M64
                        key_used, key_slot = kv & _M64, ks
                    else:
                        R.pop(0, None)
                        note.append("getter unresolved @%s" % (hex(tgt) if tgt else "?"))
                elif mn == "br":
                    return self._mk_static_result(eng, block_addr, table, stride, key_used, key_slot,
                                                  getter_slot, rd(ops[0]), csel_cands, const_cands, note)
                elif mn == "b":
                    return self._mk_static_result(eng, block_addr, table, stride, key_used, key_slot,
                                                  getter_slot, self._imm(ops[0]), csel_cands,
                                                  const_cands, note, direct=True)
                elif mn == "ret":
                    return {"ok": True, "matched": False, "node_type": "return",
                            "block": f"0x{block_addr:x}", "reason": "ret", "note": note}
            except Exception as ex:
                note.append("eval err @0x%x: %r" % (r["addr"], ex))
                continue
        return {"ok": True, "matched": False, "node_type": "unknown", "block": f"0x{block_addr:x}",
                "reason": f"kein Dispatch-Terminator in {len(rows)} Instr.", "note": note}

    def _mk_static_result(self, eng, block_addr, table, stride, key, key_slot, getter_slot,
                          target, csel_cands, const_cands, note, direct=False):
        # const = die groesste gebackene Konstante im Block (Dispatch-Konstante), zur Transparenz
        const = max(const_cands) if const_cands else None
        res = {"ok": True, "block": f"0x{block_addr:x}",
               "table": (f"0x{table:x}" if table is not None else None),
               "stride": stride,
               "const": (f"0x{const:x}" if const is not None else None),
               "key": (f"0x{key:x}" if key is not None else None),
               "key_slot": (f"0x{key_slot:x}" if key_slot is not None else None),
               "getter_slot": (f"0x{getter_slot:x}" if getter_slot is not None else None),
               "note": note}
        cc = [c & _M64 for c in csel_cands if c]
        if cc:
            # Branch-Knoten: zwei Zeiger(-slots); Nachfolger = *(cand) (deref), falls lesbar
            succs = []
            for c in cc:
                succs.append(f"0x{self._read_u(eng, c, 8):x}")
            res.update({"matched": True, "node_type": "branch",
                        "candidates": [f"0x{c:x}" for c in cc],
                        "candidate_successors": succs, "successor": None})
            return res
        if target is None:
            res.update({"matched": False,
                        "node_type": "index" if table is not None else "unknown",
                        "successor": None,
                        "reason": "Dispatch-Ziel nicht statisch bestimmbar (unbekannter Registerwert)"})
            return res
        if key is not None and table is not None and stride:
            # index zur Transparenz (Breite: low32, wie im w-Register-Pfad)
            idx = ((const - (key & _W32)) & _W32) if const is not None else None
            res["index"] = (idx if idx is not None else None)
        res.update({"matched": True,
                    "node_type": "direct" if direct else ("index" if table is not None else "jump"),
                    "successor": f"0x{int(target) & _M64:x}"})
        return res

    def devirt_static(self, eng, entry, key=None, max_blocks=400):
        """Statische Devirtualisierung: BFS ueber den Dispatch-Graphen ab ``entry`` mittels
        ``resolve_index_static`` (ohne Lauf der Zielfunktion). Folgt index/direct/jump-Kanten und
        beiden Branch-Kandidaten. Rueckgabe: {entry, n_blocks, n_branch_points, n_traps,
        branch_points[], traps[], blocks{addr:{node_type,successors,table,index}}, note}.
        Voraussetzung: Init einmal angewandt (State-Keys in .data), oder ``key=`` uebergeben."""
        from collections import deque
        blocks = {}
        traps = []
        bps = []
        q = deque([int(entry) & _M64])
        while q and len(blocks) < max_blocks:
            b = q.popleft()
            if b in blocks:
                continue
            r = self.resolve_index_static(eng, b, key=key)
            nt = r.get("node_type")
            succ = []
            if nt == "branch":
                succ = [int(x, 16) for x in r.get("candidate_successors", []) if x]
                bps.append(f"0x{b:x}")
            elif r.get("successor"):
                s = int(r["successor"], 16)
                if s == 0 or s < 0x1000:
                    traps.append(f"0x{b:x}")
                else:
                    succ = [s]
            elif not r.get("matched") and nt != "return":
                traps.append(f"0x{b:x}")
            blocks[b] = {"node_type": nt,
                         "successors": [f"0x{s:x}" for s in succ],
                         "table": r.get("table"), "index": r.get("index")}
            for s in succ:
                if s >= 0x1000 and s not in blocks:
                    q.append(s)
        return {"ok": True, "entry": f"0x{int(entry) & _M64:x}", "n_blocks": len(blocks),
                "n_branch_points": len(bps), "n_traps": len(traps),
                "branch_points": bps, "traps": traps,
                "blocks": {f"0x{k:x}": v for k, v in blocks.items()},
                "note": (f"{len(blocks)} Bloecke, {len(bps)} Branch-Points, {len(traps)} Trap(s) "
                         f"statisch ab {hex(int(entry) & _M64)}"
                         + (" (max_blocks erreicht)" if len(blocks) >= max_blocks else ""))}

    def resolve_deadslot_candidates(self, eng, ret_to_list, key_low32, table=0x137740,
                                    row_stride=0x7f0, col_size=8, back=36,
                                    text_lo=0x1000, text_hi=0x140000, max_rows=256):
        """Generisch (const-key-OLLVM): fuer eine Liste von Dead-Dispatch-Ruecksprungzielen
        (ret_to = Adresse NACH dem 'blr 0') den Index-Block dekodieren, die beiden
        'const - key'-Konstanten + Vtable-Basis extrahieren, den gebackenen Slot bestimmen und
        die gefuellten Diagonal-Successor-Kandidaten (vt[const_row-k'][const_col-k']) enumerieren.
        key_low32 = der aktuell wirksame (gebackene) Zustands-Key low32 (vom Aufrufer gemessen).
        Liefert pro Knoten beide Orientierungen (welche const = Zeile/Spalte) + Kandidatenliste.
        Dient als Eingabe fuer die zustandsgefaedelte genuine Rekonstruktion (N5/MS-2)."""
        M32 = 0xffffffff

        def rd8(a):
            raw = eng.cpu.m.rb(int(a) & _M64, 8)
            return int.from_bytes(raw, "little") if raw else 0

        def block_consts_table(end):
            start = int(end) - back * 4
            regval = {}; consts = []; radrp = {}; tables = []
            p = start
            for _ in range(back + 1):
                try:
                    ins = eng.decode_at(p)
                except Exception:
                    p += 4; continue
                mn = (ins.mnemonic or "").lower()
                ops = [o.strip() for o in (ins.op_str or "").split(",")]
                if mn == "mov" and len(ops) == 2 and ops[1].startswith("#"):
                    d = self._rtok(ops[0])[0]; v = self._imm(ops[1])
                    if isinstance(d, int) and v is not None:
                        regval[d] = v & M32
                elif mn == "movk" and len(ops) >= 2 and "lsl #16" in (ins.op_str or ""):
                    d = self._rtok(ops[0])[0]; v = self._imm(ops[1])
                    if isinstance(d, int) and v is not None:
                        base = regval.get(d, 0); regval[d] = ((v << 16) | (base & 0xffff)) & M32
                        if (regval[d] >> 24) == 0x90:
                            consts.append(regval[d])
                elif mn == "adrp" and len(ops) == 2:
                    d = self._rtok(ops[0])[0]; v = self._imm(ops[1])
                    if isinstance(d, int) and v is not None:
                        radrp[d] = v
                elif mn == "add" and len(ops) == 3 and ops[2].startswith("#"):
                    b = self._rtok(ops[1])[0]; d = self._rtok(ops[0])[0]
                    if b in radrp and self._imm(ops[2]) is not None:
                        val = radrp[b] + self._imm(ops[2]); radrp[d] = val
                        if val in (0x137740, 0x134f48):
                            tables.append(val)
                p += ins.size or 4
            tb = tables[-1] if tables else table
            seen = []
            for c in consts:
                if c not in seen:
                    seen.append(c)
            return tb, seen

        def diag_candidates(tb, crow, ccol):
            out = []
            for r in range(0, max_rows):
                kp = (crow - r) & M32
                col = (ccol - kp) & M32
                if not (0 <= col < 256):
                    continue
                v = rd8(tb + r * row_stride + col * col_size)
                if v and text_lo <= v < text_hi:
                    out.append({"row": r, "col": col, "key_low32": f"0x{kp:08x}",
                                "handler": f"0x{v:x}"})
            return out

        k = int(key_low32) & M32
        nodes = []
        for ret_to in ret_to_list:
            ret = int(ret_to, 16) if isinstance(ret_to, str) else int(ret_to)
            tb, cvals = block_consts_table(ret)
            node = {"ret_to": f"0x{ret:x}", "table": f"0x{tb:x}",
                    "consts": [f"0x{c:x}" for c in cvals]}
            if len(cvals) >= 2:
                orients = []
                for crow, ccol in ((cvals[0], cvals[1]), (cvals[1], cvals[0])):
                    row = (crow - k) & M32; col = (ccol - k) & M32
                    baked = rd8(tb + row * row_stride + col * col_size) if (row < 256 and col < 256) else 0
                    orients.append({"row_const": f"0x{crow:x}", "col_const": f"0x{ccol:x}",
                                    "baked_row": row, "baked_col": col,
                                    "baked_slot": f"0x{baked:x}",
                                    "candidates": diag_candidates(tb, crow, ccol)[:16]})
                node["orientations"] = orients
            nodes.append(node)
        return {"ok": True, "key_low32": f"0x{k:08x}", "table": f"0x{table:x}",
                "n_nodes": len(nodes), "nodes": nodes}
