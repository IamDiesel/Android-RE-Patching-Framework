"""nindex — nativer XREF/CallGraph-Index (M2/M7, IP-neutral).

Das native Pendant zu `services/smali_search_service.SmaliSearchEngine` — und
nach demselben Performance-Muster gebaut:

  1. **Einmal in den RAM:** die ausführbaren Segmente werden **einmal** über
     capstone (`Arm64Decoder`) dekodiert; danach sind Queries reine
     In-Memory-Lookups — keine erneute Dekodierung pro Suche.
  2. **SHA-gegateter Pickle-Cache:** der Index wird LZMA-gepickelt, Schlüssel =
     SHA der Image-Bytes. Unverändertes Image → `pickle.load` statt neu
     dekodieren (wie der mtime-gegatete Smali-Cache, nur inhaltsbasiert).
  3. **Substring-Prefilter** für die Freitext-Suche über `op_str` (C-Level
     `in` zuerst, dann Detail) — analog zum zweistufigen Smali-XREF.
  4. **Frühe Abbrüche** (Result-Cap) + optionaler Listener/Progress.
  5. **Zonen-Policy:** Cache liegt im Analyse-Arbeitsbereich (Aufrufer gibt
     `cache_dir` aus `data/native/<plugin>/…`), nie in `source/`.

Gespeichert werden Kontrollfluss-Kanten (Branch/Call-Ziele) + Symboltabelle —
genug für „wer ruft X?" (XREF) und Funktions-CallGraph, ohne pro Instruktion
ein Python-Objekt zu halten (speicherschonend bei 13-MB-Libs).
"""
from __future__ import annotations

import bisect
import lzma
import os
import pickle
import re
from typing import List, Optional, Tuple

from .engine_arm64 import Arm64Decoder
from .unpack import sha8  # noqa: F401  (Datei-SHA-Helfer, hier für Konsistenz)

_PF_X = 0x1
_BRANCH = {"b", "bl", "cbz", "cbnz", "tbz", "tbnz"}   # + b.<cc> (siehe _is_branch)
_CALL = {"bl"}
_IMM = re.compile(r"#(0x[0-9a-fA-F]+|\d+)")
_REG = re.compile(r"\b(x\d{1,2}|w\d{1,2}|sp|xzr|wzr)\b")
_FMT = 4  # Cache-Format-Version (v4: + .rela.dyn/RELR-Relokations-Refs)


def _is_branch(mnem: str) -> bool:
    base = mnem.split(".", 1)[0]
    return base in _BRANCH or mnem.startswith("b.")


def _parse_target(op_str: str) -> Optional[int]:
    """Letztes Immediat = Sprungziel (capstone löst PC-relativ absolut auf).
    Bei tbz/cbz etc. ist das Ziel das LETZTE #imm (Bit/Reg stehen davor)."""
    ims = _IMM.findall(op_str)
    if not ims:
        return None
    tok = ims[-1]
    return int(tok, 16) if tok.lower().startswith("0x") else int(tok)


class NativeIndex:
    def __init__(self):
        self.edges: List[Tuple[int, str, int]] = []   # (src_addr, mnem, target_addr)
        self.xref: dict = {}                           # target_addr -> [src_addr, ...]
        self.syms: List[Tuple[int, str]] = []          # sortiert [(addr, name)]
        self._sym_addrs: List[int] = []
        self.count: int = 0                            # dekodierte Instruktionen gesamt
        self.sha: Optional[str] = None

    # ---- Funktionsauflösung (nächstes Symbol <= addr; wie Rückwärts-Scan zu .method) ----
    def func_at(self, addr: int) -> Optional[str]:
        if not self._sym_addrs:
            return None
        i = bisect.bisect_right(self._sym_addrs, addr) - 1
        return self.syms[i][1] if i >= 0 else None

    def func_bounds(self, addr: int):
        """(start, end) der Funktion, die `addr` enthaelt — Symbol <= addr bis naechstes Symbol."""
        if not self._sym_addrs:
            return (addr, addr + 48 * 4)
        i = bisect.bisect_right(self._sym_addrs, addr) - 1
        if i < 0:
            return (addr, addr + 48 * 4)
        start = self._sym_addrs[i]
        end = self._sym_addrs[i + 1] if i + 1 < len(self._sym_addrs) else start + 2000 * 4
        return (start, min(end, start + 4000 * 4))

    # ---- Aufbau ----------------------------------------------------------
    def build(self, image, cache_dir: Optional[str] = None, listener=None,
              fast: bool = True) -> "NativeIndex":
        """Baut den XREF/CallGraph-Index.

        fast=True (Default): nur Kontrollfluss (Branch/Call) + Reloc-Kanten —
        ein capstone-Pass je Segment mit skipdata (nicht-dekodierbare Worte in C
        uebersprungen, kein Python-Resync), KEIN adrp/add/ldr-Regex pro
        Instruktion. Deutlich schneller; deckt „wer ruft X?" ab.
        fast=False: zusaetzlich Daten-/Adress-Referenzen (adrp+add/ldr -> Globals/
        Strings/vtables). Teurer. Cache ist nach Modus getrennt.
        """
        import hashlib
        self.sha = hashlib.sha256(image.data).hexdigest()[:12]
        _mode = "f" if fast else "x"
        cache = os.path.join(cache_dir, f".nidx_{self.sha}_{_mode}_v{_FMT}.pkl.xz") if cache_dir else None
        if cache and os.path.isfile(cache):
            try:
                with lzma.open(cache, "rb") as f:
                    d = pickle.load(f)
                self.edges, self.xref, self.syms, self.count = d["edges"], d["xref"], d["syms"], d["count"]
                self._sym_addrs = [a for a, _ in self.syms]
                if listener: listener("native.index", {"stage": "cache", "edges": len(self.edges)})
                return self
            except Exception:
                pass  # defekter Cache -> neu bauen

        self.syms = sorted((s.value, name) for name, s in image.dynsyms.items())
        self._sym_addrs = [a for a, _ in self.syms]

        md = Arm64Decoder()._md
        try:
            md.skipdata = True  # nicht dekodierbare Worte in C ueberspringen (kein Python-Resync)
        except Exception:
            pass

        xseg = [(s.vaddr, s.vaddr + max(s.memsz, s.filesz)) for s in image.segments if s.flags & _PF_X]
        lseg = [(s.vaddr, s.vaddr + max(s.memsz, s.filesz)) for s in image.segments if max(s.memsz, s.filesz)]
        xlo = min((lo for lo, _ in xseg), default=0); xhi = max((hi for _, hi in xseg), default=0)
        llo = min((lo for lo, _ in lseg), default=0); lhi = max((hi for _, hi in lseg), default=0)

        edges = self.edges
        xref = self.xref
        mem_re = re.compile(r"\[(\w+)(?:,\s*#(0x[0-9a-fA-F]+|\d+))?\]")

        for seg in image.segments:
            if not (seg.flags & _PF_X):
                continue
            code = image.data[seg.offset:seg.offset + seg.filesz]
            base = seg.vaddr
            pages = {}
            for addr, size, mnem, op in md.disasm_lite(code, base):
                self.count += 1
                c0 = mnem[0]
                if (c0 == "b" or c0 == "c" or c0 == "t") and _is_branch(mnem):
                    tgt = _parse_target(op)
                    if tgt is not None and xlo <= tgt < xhi:
                        edges.append((addr, mnem, tgt))
                        xref.setdefault(tgt, []).append(addr)
                    continue
                if fast:
                    continue
                bm = mnem.split(".", 1)[0]
                if bm == "adrp":
                    rs = _REG.findall(op); im = _parse_target(op)
                    if rs and im is not None:
                        pages[rs[0]] = im
                elif bm == "add":
                    rs = _REG.findall(op); im = _parse_target(op)
                    if len(rs) >= 2 and im is not None and rs[1] in pages:
                        tgt = pages[rs[1]] + im
                        if llo <= tgt < lhi:
                            edges.append((addr, "adr", tgt)); xref.setdefault(tgt, []).append(addr)
                    if rs:
                        pages.pop(rs[0], None)
                elif bm[:3] == "ldr" or bm[:3] == "str":
                    mm = mem_re.search(op)
                    if mm and mm.group(1) in pages:
                        im = int(mm.group(2), 0) if mm.group(2) else 0
                        tgt = pages[mm.group(1)] + im
                        if llo <= tgt < lhi:
                            edges.append((addr, bm, tgt)); xref.setdefault(tgt, []).append(addr)
                    if bm[:3] == "ldr":
                        rs = _REG.findall(op)
                        if rs:
                            pages.pop(rs[0], None)
            if listener:
                listener("native.index", {"stage": "segment", "vaddr": seg.vaddr, "count": self.count})

        for site, tgt, kind in getattr(image, "relocs", []):
            if llo <= tgt < lhi:
                edges.append((site, "reloc", tgt))
                xref.setdefault(tgt, []).append(site)

        if cache:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with lzma.open(cache, "wb", preset=1) as f:
                    pickle.dump({"edges": self.edges, "xref": self.xref,
                                 "syms": self.syms, "count": self.count}, f, protocol=4)
            except Exception:
                pass
        return self


    # ---- Queries ---------------------------------------------------------
    def xrefs_to(self, addr: int, limit: int = 500) -> List[Tuple[int, str]]:
        """Eingehende Referenzen auf eine Adresse: [(src_addr, mnemonic), …]."""
        srcs = self.xref.get(addr, [])
        out = []
        by_src = {s: m for s, m, t in self.edges if t == addr}
        for s in srcs[:limit]:
            out.append((s, by_src.get(s, "b")))
        return out

    def callgraph_edges(self, calls_only: bool = True) -> set:
        """Funktions-Kanten (caller_func -> callee_func) aus bl/b-Zielen."""
        out = set()
        for src, mnem, tgt in self.edges:
            base = mnem.split(".", 1)[0]
            if calls_only and base != "bl":
                continue
            cf, tf = self.func_at(src), self.func_at(tgt)
            if cf and tf:
                out.add((cf, tf))
        return out

    def callers_of(self, func_name: str) -> set:
        """Welche Funktionen rufen `func_name`? (Funktions-XREF)."""
        return {cf for cf, tf in self.callgraph_edges() if tf == func_name}

    def search(self, term: str, limit: int = 500) -> List[Tuple[int, str, int]]:
        """Substring-Prefilter über die Kanten-Mnemonics (Demo der Suchebene)."""
        out = []
        for src, mnem, tgt in self.edges:
            if term in mnem or term in f"0x{tgt:x}":
                out.append((src, mnem, tgt))
                if len(out) >= limit:
                    break
        return out
