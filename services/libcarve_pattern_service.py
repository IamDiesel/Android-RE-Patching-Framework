# -*- coding: utf-8 -*-
"""services.libcarve_pattern_service — Wildcard-AOB-Signatur & Versions-Pattern-Matching.

Erzeugt relokations-tolerante Byte-Signaturen (AOB-Masken mit '??') ueber
ARM64-Instruktionen (ADRP/BL/B/LDR/ADD-Offsets automatisch maskiert) sowie
Anker-Strings und matcht Favoriten sauber auf neue/aktualisierte .so-Versionen.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re

from core.native.engine_arm64 import Arm64Decoder
from core.native.loader import ElfImage
from core.native.nindex import NativeIndex

_MASK_ALL_BRANCH = {"b", "bl", "cbz", "cbnz", "tbz", "tbnz", "adr", "adrp"}
_MASK_IMM12 = {"add", "sub", "ldr", "ldrb", "ldrh", "ldp", "str", "strb", "strh", "stp"}


class LibCarvePatternService:
    """Erstellt und matcht ARM64 Wildcard-Signaturen (analog Smali-Fuzzing)."""

    @classmethod
    def generate_signature(
        cls,
        image: ElfImage,
        index: Optional[NativeIndex],
        vaddr: int,
        byte_len: int = 32,
    ) -> Dict[str, Any]:
        """Generiert eine relokations-tolerante Wildcard-Signatur + Anker-Strings ab `vaddr`."""
        v = int(vaddr)
        req_len = max(8, min(64, int(byte_len)))
        raw = image.read_vaddr(v, req_len)
        if not raw:
            return {"aob": "", "orig_hex": "", "func_name": "", "anchor_strings": [], "rel_offset": 0}

        md = Arm64Decoder()._md
        tokens: List[str] = []
        pos = 0
        # 4-Byte-weise ueber Instruktionen gehen und relozierbare Bytes maskieren
        while pos + 4 <= len(raw):
            chunk = raw[pos:pos + 4]
            pc = v + pos
            insns = list(md.disasm_lite(chunk, pc))
            if not insns:
                tokens.extend(f"{b:02X}" for b in chunk)
                pos += 4
                continue
            _a, _sz, mnem, _op = insns[0]
            base = mnem.split(".", 1)[0].lower()
            if base in _MASK_ALL_BRANCH or mnem.lower().startswith("b."):
                # PC-relative Ziel-Offsets koennen zwischen Lib-Versionen wandern -> alle 4B maskieren,
                # ausser Byte 3 bei ldr/add, um Instruktionstyp zu bewahren
                tokens.extend(["??", "??", "??", "??"])
            elif base in _MASK_IMM12:
                # imm12/imm9 beruehrt primär Bytes 1 und 2 (Bits 8..23); Byte 0 (Rt/Rn) & Byte 3 (Opcode) bleiben fest!
                tokens.extend([f"{chunk[0]:02X}", "??", "??", f"{chunk[3]:02X}"])
            else:
                tokens.extend(f"{b:02X}" for b in chunk)
            pos += 4
        if pos < len(raw):
            tokens.extend(f"{b:02X}" for b in raw[pos:])

        # Fallback falls alles maskiert wurde: ersten & letzten 4B exakt lassen
        if tokens and all(t == "??" for t in tokens):
            for i in range(min(4, len(raw))):
                tokens[i] = f"{raw[i]:02X}"

        func_name = ""
        rel_offset = 0
        anchors: List[str] = []
        if index is not None:
            func_name = index.func_at(v) or ""
            fs, fe = index.func_bounds(v)
            rel_offset = max(0, v - fs)
            try:
                dg = index.datagraph_for(image, fs, fe, limit=80)
                seen_a = set()
                for ref in dg:
                    pv = str(ref.get("preview") or "")
                    if pv.startswith('"') and pv.endswith('"') and len(pv) >= 5:
                        s_val = pv[1:-1]
                        if s_val not in seen_a:
                            seen_a.add(s_val)
                            anchors.append(s_val)
                        if len(anchors) >= 6:
                            break
            except Exception:
                pass

        return {
            "aob": " ".join(tokens),
            "orig_hex": raw.hex(" "),
            "func_name": func_name,
            "anchor_strings": anchors,
            "rel_offset": rel_offset,
        }

    @classmethod
    def match_patch(
        cls,
        image: ElfImage,
        index: NativeIndex,
        patch: dict,
        *,
        limit: int = 30,
    ) -> Dict[str, Any]:
        """Sucht passende Zieladresse(n) fuer einen LibCarve-Patch in `image`.
        Liefert {'ok': bool, 'status': ..., 'vaddr': int|None, 'candidates': [...]}."""
        orig_hex = str(patch.get("orig_hex") or patch.get("orig") or "").replace(" ", "").lower()
        aob = str(patch.get("aob") or "").strip()
        func_name = str(patch.get("func") or patch.get("func_name") or "").strip()
        rel_off = int(patch.get("rel_offset") or 0)
        anchors = [str(a) for a in (patch.get("anchor_strings") or ()) if a]

        # 1) Exakte Pruefung ueber VAddr oder Symbolname
        v_raw = patch.get("vaddr")
        if v_raw is not None and v_raw != "":
            try:
                v0 = int(v_raw, 0) if isinstance(v_raw, str) else int(v_raw)
                if orig_hex:
                    cur = image.read_vaddr(v0, len(orig_hex) // 2).hex().lower()
                    if cur == orig_hex:
                        return {
                            "ok": True,
                            "status": "exact",
                            "vaddr": v0,
                            "confidence": 1.0,
                            "candidates": [{"vaddr": v0, "func": index.func_at(v0) or func_name, "score": 1.0, "reason": "Exakte Adresse & Bytes"}],
                        }
            except Exception:
                pass

        if func_name and not func_name.startswith("sub_") and func_name in image.dynsyms:
            sym = image.dynsyms[func_name]
            cand_v = sym.value + rel_off
            if cand_v > 0:
                if orig_hex and image.read_vaddr(cand_v, len(orig_hex) // 2).hex().lower() == orig_hex:
                    return {
                        "ok": True,
                        "status": "symbol_exact",
                        "vaddr": cand_v,
                        "confidence": 0.99,
                        "candidates": [{"vaddr": cand_v, "func": func_name, "score": 0.99, "reason": f"Symbol {func_name}+0x{rel_off:x}"}],
                    }
                if aob:
                    # Pruefe ob AOB genau dort greift
                    hits_sym = index.search_aob(image, aob, exec_only=True, limit=50)
                    if cand_v in hits_sym:
                        return {
                            "ok": True,
                            "status": "symbol_aob",
                            "vaddr": cand_v,
                            "confidence": 0.97,
                            "candidates": [{"vaddr": cand_v, "func": func_name, "score": 0.97, "reason": f"Symbol {func_name} + AOB"}],
                        }

        # 2) RAM Wildcard-AOB-Scan
        scored: List[dict] = []
        if aob:
            hits = index.search_aob(image, aob, exec_only=True, limit=limit)
            for h in hits:
                fn = index.func_at(h) or f"0x{h:x}"
                fs, fe = index.func_bounds(h)
                score = 0.70
                reasons = ["AOB-Match"]
                if func_name and not func_name.startswith("sub_") and fn == func_name:
                    score += 0.15
                    reasons.append("Symbolname")
                if anchors:
                    try:
                        dg = index.datagraph_for(image, fs, fe, limit=80)
                        found_strs = {str(r.get("preview") or "").strip('"') for r in dg}
                        common = [a for a in anchors if a in found_strs]
                        if common:
                            score += 0.25 * (len(common) / len(anchors))
                            reasons.append(f"Anker({len(common)}/{len(anchors)})")
                    except Exception:
                        pass
                scored.append({"vaddr": h, "func": fn, "score": round(min(1.0, score), 3), "reason": ", ".join(reasons)})

            scored.sort(key=lambda c: c["score"], reverse=True)
            if len(scored) == 1:
                return {"ok": True, "status": "pattern_unique", "vaddr": scored[0]["vaddr"], "confidence": scored[0]["score"], "candidates": scored}
            if len(scored) > 1 and (scored[0]["score"] - scored[1]["score"] >= 0.15):
                return {"ok": True, "status": "pattern_anchored", "vaddr": scored[0]["vaddr"], "confidence": scored[0]["score"], "candidates": scored}
            if scored:
                return {"ok": False, "status": "ambiguous", "vaddr": None, "confidence": scored[0]["score"], "candidates": scored}

        # 3) Anker-String Fuzzy-Fallback
        if anchors:
            str_addrs = {s: a for a, s in index.extract_strings(image) if s in anchors}
            cand_funcs: Dict[int, dict] = {}
            for s_txt, s_addr in str_addrs.items():
                for src, _mn in index.xrefs_to(s_addr, limit=40):
                    fs, _fe = index.func_bounds(src)
                    fn = index.func_at(fs) or f"sub_{fs:x}"
                    rec = cand_funcs.setdefault(fs, {"vaddr": fs + rel_off, "func": fn, "hits": set()})
                    rec["hits"].add(s_txt)
            fuzzy_cands = []
            for _fs, rec in cand_funcs.items():
                sc = round(0.3 + 0.5 * (len(rec["hits"]) / len(anchors)), 3)
                fuzzy_cands.append({
                    "vaddr": rec["vaddr"],
                    "func": rec["func"],
                    "score": sc,
                    "reason": f"Anker-Strings ({', '.join(sorted(rec['hits']))})",
                })
            fuzzy_cands.sort(key=lambda c: c["score"], reverse=True)
            if fuzzy_cands:
                return {"ok": False, "status": "fuzzy", "vaddr": None, "confidence": fuzzy_cands[0]["score"], "candidates": fuzzy_cands[:limit]}

        return {"ok": False, "status": "not_found", "vaddr": None, "confidence": 0.0, "candidates": []}
