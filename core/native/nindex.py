"""nindex — nativer XREF/CallGraph-Index (M2/M7 + LibCarve v5, IP-neutral).

Das native Pendant zu `services/smali_search_service.SmaliSearchEngine` — und
nach demselben Performance-Muster gebaut:

  1. **Einmal in den RAM:** die ausführbaren Segmente werden **einmal** über
     capstone (`Arm64Decoder`) dekodiert; danach sind Queries reine
     In-Memory-Lookups — keine erneute Dekodierung pro Suche.
  2. **SHA-gegateter Pickle-Cache:** der Index wird LZMA-gepickelt, Schlüssel =
     SHA der Image-Bytes. Unverändertes Image -> `pickle.load` statt neu
     dekodieren (wie der mtime-gegatete Smali-Cache, nur inhaltsbasiert).
  3. **Substring-Prefilter & AOB-Masken** für Freitext-/String-/Byte-Suche.
  4. **Funktionssynthese (`sub_<hex>`):** entdeckt `bl`-Ziele, `DT_INIT` und
     ARM64-Prologe auch in gestrippten Bibliotheken.
  5. **Zonen-Policy:** Cache liegt im Analyse-Arbeitsbereich, nie in `source/`.
"""
from __future__ import annotations

import bisect
import lzma
import os
import pickle
import re
import struct
from typing import Dict, List, Optional, Tuple

from .engine_arm64 import Arm64Decoder
from .unpack import sha8  # noqa: F401  (Datei-SHA-Helfer, hier für Konsistenz)

_PF_X = 0x1
_BRANCH = {"b", "bl", "cbz", "cbnz", "tbz", "tbnz"}   # + b.<cc> (siehe _is_branch)
_CALL = {"bl"}
_IMM = re.compile(r"#(0x[0-9a-fA-F]+|\d+)")
_REG = re.compile(r"\b(x\d{1,2}|w\d{1,2}|sp|xzr|wzr)\b")
_MEM_RE = re.compile(r"\[(\w+)(?:,\s*#(0x[0-9a-fA-F]+|\d+))?\]")
_FMT = 5  # v5: + gestrippte sub_-Funktionssynthese, func_catalog, Imports


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


def _classify_symbol(name: str) -> str:
    if name.startswith("Java_") or name == "JNI_OnLoad":
        return "jni"
    if name.startswith("sub_"):
        return "sub"
    return "export"


class NativeIndex:
    def __init__(self):
        self.edges: List[Tuple[int, str, int]] = []   # (src_addr, mnem, target_addr)
        self.xref: dict = {}                           # target_addr -> [src_addr, ...]
        self.syms: List[Tuple[int, str]] = []          # sortiert [(addr, name)]
        self._sym_addrs: List[int] = []
        self.func_catalog: List[Tuple[int, str, str]] = []  # [(addr, name, kind)]
        self.imports: List[str] = []
        self.count: int = 0                            # dekodierte Instruktionen gesamt
        self.sha: Optional[str] = None
        self._strings_cache: Optional[List[Tuple[int, str]]] = None

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
        """Baut den XREF/CallGraph/Funktions-Index (fast=True: Kontrollfluss+Prologe; fast=False: +ADRP-Datenrefs)."""
        import hashlib
        self.sha = hashlib.sha256(image.data).hexdigest()[:12]
        _mode = "f" if fast else "x"
        cache = os.path.join(cache_dir, f".nidx_{self.sha}_{_mode}_v{_FMT}.pkl.xz") if cache_dir else None
        if cache and os.path.isfile(cache):
            try:
                with lzma.open(cache, "rb") as f:
                    d = pickle.load(f)
                self.edges = d["edges"]
                self.xref = d["xref"]
                self.syms = d["syms"]
                self.count = d["count"]
                self.func_catalog = d.get("func_catalog", [])
                self.imports = d.get("imports", [])
                self._sym_addrs = [a for a, _ in self.syms]
                if listener:
                    listener("native.index", {"stage": "cache", "edges": len(self.edges)})
                return self
            except Exception:
                pass  # defekter Cache -> neu bauen

        self.edges = []
        self.xref = {}
        self.count = 0
        self._strings_cache = None

        defined_syms: List[Tuple[int, str]] = []
        imports: List[str] = []
        for name, s in image.dynsyms.items():
            if s.shndx == 0 and s.value == 0:
                imports.append(name)
            elif s.value > 0 or s.shndx != 0:
                defined_syms.append((s.value, name))
        defined_syms.sort(key=lambda t: t[0])
        self.imports = sorted(set(imports))

        named_addrs = {a for a, _ in defined_syms}
        xseg = [(s.vaddr, s.vaddr + max(s.memsz, s.filesz)) for s in image.segments if s.flags & _PF_X]
        lseg = [(s.vaddr, s.vaddr + max(s.memsz, s.filesz)) for s in image.segments if max(s.memsz, s.filesz)]
        xlo = min((lo for lo, _ in xseg), default=0)
        xhi = max((hi for _, hi in xseg), default=0)
        llo = min((lo for lo, _ in lseg), default=0)
        lhi = max((hi for _, hi in lseg), default=0)

        md = Arm64Decoder()._md
        try:
            md.skipdata = True  # nicht dekodierbare Worte in C ueberspringen (kein Python-Resync)
        except Exception:
            pass

        edges = self.edges
        xref = self.xref
        bl_targets = set()

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
                        if mnem.split(".", 1)[0] == "bl":
                            bl_targets.add(tgt)
                    continue
                if fast:
                    continue
                bm = mnem.split(".", 1)[0]
                if bm == "adrp":
                    rs = _REG.findall(op)
                    im = _parse_target(op)
                    if rs and im is not None:
                        pages[rs[0]] = im
                elif bm == "add":
                    rs = _REG.findall(op)
                    im = _parse_target(op)
                    if len(rs) >= 2 and im is not None and rs[1] in pages:
                        tgt = pages[rs[1]] + im
                        if llo <= tgt < lhi:
                            edges.append((addr, "adr", tgt))
                            xref.setdefault(tgt, []).append(addr)
                    if rs:
                        pages.pop(rs[0], None)
                elif bm[:3] == "ldr" or bm[:3] == "str":
                    mm = _MEM_RE.search(op)
                    if mm and mm.group(1) in pages:
                        im = int(mm.group(2), 0) if mm.group(2) else 0
                        tgt = pages[mm.group(1)] + im
                        if llo <= tgt < lhi:
                            edges.append((addr, bm, tgt))
                            xref.setdefault(tgt, []).append(addr)
                    if bm[:3] == "ldr":
                        rs = _REG.findall(op)
                        if rs:
                            pages.pop(rs[0], None)
            if listener:
                listener("native.index", {"stage": "segment", "vaddr": seg.vaddr, "count": self.count})

        # DT_INIT / DT_INIT_ARRAY & ARM64 Prologe in PF_X (fuer gestrippte Binaries)
        init_addrs = set()
        if getattr(image, "init", 0) and xlo <= image.init < xhi:
            init_addrs.add(image.init)
        if getattr(image, "init_array", 0) and getattr(image, "init_arraysz", 0):
            ioff = image.vaddr_to_offset(image.init_array) if hasattr(image, "vaddr_to_offset") else None
            if ioff is not None:
                n_init = min(2048, int(image.init_arraysz) // 8)
                for k in range(n_init):
                    if ioff + k * 8 + 8 > len(image.data):
                        break
                    ptr = struct.unpack_from("<Q", image.data, ioff + k * 8)[0]
                    if xlo <= ptr < xhi:
                        init_addrs.add(ptr)

        prologue_addrs = set()
        for seg in image.segments:
            if not (seg.flags & _PF_X) or seg.filesz < 4:
                continue
            code = image.data[seg.offset:seg.offset + (seg.filesz & ~3)]
            base = seg.vaddr
            prev_pac_bti = False
            for idx_w, (w,) in enumerate(struct.iter_unpack("<I", code)):
                a = base + idx_w * 4
                is_pac_bti = w in (0xD503237F, 0xD503233F, 0xD503245F)  # pacibsp / paciasp / bti c
                is_stp_fp = (w & 0xFFC07FFF) == 0xA9807BFD               # stp x29, x30, [sp, #-imm]!
                if is_pac_bti:
                    prologue_addrs.add(a)
                    prev_pac_bti = True
                    continue
                if is_stp_fp and not prev_pac_bti:
                    prologue_addrs.add(a)
                prev_pac_bti = False

        for site, tgt, kind in getattr(image, "relocs", []):
            if llo <= tgt < lhi:
                edges.append((site, "reloc", tgt))
                xref.setdefault(tgt, []).append(site)

        # Symbole + synthetische sub_-Funktionen zusammenfuehren (keine Doppel-Eintraege <= 8B)
        all_syms: Dict[int, str] = {}
        cat: List[Tuple[int, str, str]] = []
        for a, nm in defined_syms:
            if a not in all_syms:
                all_syms[a] = nm
                cat.append((a, nm, _classify_symbol(nm)))

        def _near_existing(addr: int) -> bool:
            return any((addr - d) in all_syms for d in (0, 4, 8))

        for a in sorted(init_addrs):
            if not _near_existing(a):
                nm = f"init_{a:x}"
                all_syms[a] = nm
                cat.append((a, nm, "init"))

        for a in sorted(bl_targets | prologue_addrs):
            if not _near_existing(a):
                nm = f"sub_{a:x}"
                all_syms[a] = nm
                cat.append((a, nm, "sub"))

        self.syms = sorted(all_syms.items(), key=lambda t: t[0])
        self._sym_addrs = [a for a, _ in self.syms]
        self.func_catalog = sorted(cat, key=lambda t: t[0])

        if cache:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with lzma.open(cache, "wb", preset=1) as f:
                    pickle.dump({
                        "edges": self.edges, "xref": self.xref,
                        "syms": self.syms, "count": self.count,
                        "func_catalog": self.func_catalog, "imports": self.imports,
                    }, f, protocol=4)
            except Exception:
                pass
        return self

    # ---- Queries ---------------------------------------------------------
    def xrefs_to(self, addr: int, limit: int = 500) -> List[Tuple[int, str]]:
        """Eingehende Referenzen auf eine Adresse: [(src_addr, mnemonic), ...]."""
        srcs = self.xref.get(addr, [])
        out = []
        by_src = {s: m for s, m, t in self.edges if t == addr}
        for s in srcs[:limit]:
            out.append((s, by_src.get(s, "b")))
        return out

    def outgoing_from(self, start_vaddr: int, end_vaddr: int, limit: int = 500) -> List[Tuple[int, str, int, str]]:
        """Ausgehende Calls/Branches aus [start_vaddr, end_vaddr): [(src, mnem, tgt, callee_name)]."""
        lo, hi = int(start_vaddr), int(end_vaddr)
        out = []
        for src, mnem, tgt in self.edges:
            if lo <= src < hi and _is_branch(mnem):
                callee = self.func_at(tgt) or f"0x{tgt:x}"
                out.append((src, mnem, tgt, callee))
                if len(out) >= limit:
                    break
        out.sort(key=lambda r: r[0])
        return out

    def datagraph_for(self, image, start_vaddr: int, end_vaddr: int, limit: int = 400) -> List[dict]:
        """Analysiert Lese-/Schreib- und Adressreferenzen (adrp+add/ldr/str + relocs) im Bereich."""
        lo, hi = int(start_vaddr), int(end_vaddr)
        size = max(0, min(hi - lo, 16384))
        code = image.read_vaddr(lo, size) if hasattr(image, "read_vaddr") else b""
        md = Arm64Decoder()._md
        lseg = [(s.vaddr, s.vaddr + max(s.memsz, s.filesz)) for s in image.segments if max(s.memsz, s.filesz)]
        llo = min((a for a, _ in lseg), default=0)
        lhi = max((b for _, b in lseg), default=0)
        pages: Dict[str, int] = {}
        refs: List[dict] = []

        def _preview(vaddr: int) -> str:
            sym = self.func_at(vaddr) if (vaddr in dict(self.syms)) else None
            if sym:
                return sym
            raw = image.read_vaddr(vaddr, 48) if hasattr(image, "read_vaddr") else b""
            if raw:
                nul = raw.find(b"\x00")
                chunk = raw[:nul] if nul >= 0 else raw
                if len(chunk) >= 3 and all(32 <= b < 127 for b in chunk[:min(len(chunk), 8)]):
                    s = chunk.decode("ascii", "replace")
                    return f'"{s[:44]}"'
                return "0x" + raw[:8].hex()
            return f"0x{vaddr:x}"

        for addr, _sz, mnem, op in md.disasm_lite(code, lo):
            bm = mnem.split(".", 1)[0]
            if bm == "adrp":
                rs = _REG.findall(op)
                im = _parse_target(op)
                if rs and im is not None:
                    pages[rs[0]] = im
            elif bm == "add":
                rs = _REG.findall(op)
                im = _parse_target(op)
                if len(rs) >= 2 and im is not None and rs[1] in pages:
                    tgt = pages[rs[1]] + im
                    if llo <= tgt < lhi:
                        refs.append({"site": addr, "access": "read", "mnem": "adr", "target": tgt, "preview": _preview(tgt)})
                if rs:
                    pages.pop(rs[0], None)
            elif bm[:3] in ("ldr", "str"):
                mm = _MEM_RE.search(op)
                if mm and mm.group(1) in pages:
                    im = int(mm.group(2), 0) if mm.group(2) else 0
                    tgt = pages[mm.group(1)] + im
                    if llo <= tgt < lhi:
                        acc = "write" if bm[:3] == "str" else "read"
                        refs.append({"site": addr, "access": acc, "mnem": bm, "target": tgt, "preview": _preview(tgt)})
                if bm[:3] == "ldr":
                    rs = _REG.findall(op)
                    if rs:
                        pages.pop(rs[0], None)
            if len(refs) >= limit:
                break

        for site, tgt, _k in getattr(image, "relocs", ()):
            if lo <= site < hi and len(refs) < limit:
                refs.append({"site": site, "access": "read", "mnem": "reloc", "target": tgt, "preview": _preview(tgt)})
        return refs

    def extract_strings(self, image, min_len: int = 4, limit: int = 25000) -> List[Tuple[int, str]]:
        """Extrahiert und cached ASCII-Strings aus lesbaren ELF-Segmenten."""
        if self._strings_cache is not None:
            return self._strings_cache[:limit]
        out: List[Tuple[int, str]] = []
        pat = re.compile(rb"[\x20-\x7e]{%d,}" % max(2, int(min_len)))
        for seg in image.segments:
            if seg.filesz <= 0:
                continue
            blob = image.data[seg.offset:seg.offset + seg.filesz]
            for m in pat.finditer(blob):
                s = m.group(0).decode("ascii", "replace")
                out.append((seg.vaddr + m.start(), s))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        self._strings_cache = out
        return out

    def search_aob(self, image, pattern: str, exec_only: bool = True, limit: int = 200) -> List[int]:
        """Sucht Wildcard-Hex-Bytes (z.B. 'FD 7B ?? A9') schnell in ELF-Segmenten."""
        toks = str(pattern or "").strip().split()
        if not toks:
            return []
        rx_parts = []
        for t in toks:
            t = t.strip()
            if len(t) != 2:
                continue
            if t == "??" or t == "**":
                rx_parts.append(b".")
            else:
                try:
                    bval = int(t, 16)
                    rx_parts.append(re.escape(bytes((bval,))))
                except ValueError:
                    return []
        if not rx_parts:
            return []
        rx = re.compile(b"".join(rx_parts), re.DOTALL)
        hits: List[int] = []
        for seg in image.segments:
            if exec_only and not (seg.flags & _PF_X):
                continue
            if seg.filesz <= 0:
                continue
            blob = image.data[seg.offset:seg.offset + seg.filesz]
            for m in rx.finditer(blob):
                hits.append(seg.vaddr + m.start())
                if len(hits) >= limit:
                    return hits
        return hits

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
        """Substring-Prefilter über die Kanten-Mnemonics."""
        out = []
        for src, mnem, tgt in self.edges:
            if term in mnem or term in f"0x{tgt:x}":
                out.append((src, mnem, tgt))
                if len(out) >= limit:
                    break
        return out
