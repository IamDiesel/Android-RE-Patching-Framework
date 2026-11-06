# -*- coding: utf-8 -*-
"""native_rebase_service — Offsets/Adressen ueber Lib-Versionen neu ziehen.

Nach einem App-Update verschieben sich native Funktionsadressen. Dieser Service bildet
Adressen/Symbole einer alten Lib-Version auf die neue ab — ueber Namens-Match der
Dynsyms (gleicher Symbolname = gleiche Funktion), inkl. Offset innerhalb der Funktion.
Verallgemeinert aus dem dynsym-Offsets-Playbook. IP-neutral.

Arbeitet auf „Symbolkarten" (name -> addr); akzeptiert ElfImage (nutzt .dynsyms) ODER
direkt ein dict {name: addr} (fuer Tests/Skripte).
"""
from __future__ import annotations

from typing import Dict


class NativeRebaseService:
    @staticmethod
    def _symmap(img) -> Dict[str, int]:
        if isinstance(img, dict):
            return {k: int(v) for k, v in img.items() if int(v)}
        out = {}
        for name, sym in (getattr(img, "dynsyms", {}) or {}).items():
            v = int(getattr(sym, "value", 0) or 0)
            if v:
                out[name] = v
        return out

    def map_symbol(self, old, new, name: str) -> dict:
        """Bildet ein Symbol (per Name) alt->neu ab."""
        so, sn = self._symmap(old), self._symmap(new)
        if name not in so:
            return {"ok": False, "error": f"Symbol nicht in alter Lib: {name}"}
        res = {"ok": True, "name": name, "old_addr": so[name]}
        if name in sn:
            res["new_addr"] = sn[name]
            res["delta"] = sn[name] - so[name]
        else:
            res["new_addr"] = None
            res["note"] = "Symbol in neuer Lib nicht vorhanden (umbenannt/entfernt?)"
        return res

    def map_address(self, old, new, addr) -> dict:
        """Bildet eine Adresse alt->neu ab: umschliessende Funktion in alt finden (naechstes
        vorangehendes Symbol), Offset bewahren, in neuer Lib auf gleiches Symbol + Offset."""
        addr = int(addr)
        so, sn = self._symmap(old), self._symmap(new)
        best = None
        for nm, a in so.items():
            if a <= addr and (best is None or a > best[1]):
                best = (nm, a)
        if best is None:
            return {"ok": False, "error": "keine umschliessende Funktion in alter Lib gefunden"}
        nm, fa = best
        off = addr - fa
        out = {"ok": True, "name": nm, "old_func": fa, "offset": off, "old_addr": addr}
        if nm in sn:
            out["new_func"] = sn[nm]
            out["new_addr"] = sn[nm] + off
            out["delta"] = sn[nm] - fa
        else:
            out["new_addr"] = None
            out["note"] = "Funktion in neuer Lib nicht vorhanden"
        return out

    def diff(self, old, new, limit: int = 40) -> dict:
        """Versions-Diff: gemeinsame Symbole (mit |delta| sortiert), + neu/entfernt (Zahlen + Namen)."""
        so, sn = self._symmap(old), self._symmap(new)
        common = [{"name": nm, "old": so[nm], "new": sn[nm], "delta": sn[nm] - so[nm]}
                  for nm in so if nm in sn]
        common.sort(key=lambda c: abs(c["delta"]), reverse=True)
        added = [nm for nm in sn if nm not in so]
        removed = [nm for nm in so if nm not in sn]
        shifted = [c for c in common if c["delta"] != 0]
        return {
            "ok": True,
            "common": len(common), "shifted": len(shifted),
            "added": len(added), "removed": len(removed),
            "top_shifts": [{"name": c["name"], "old": f"0x{c['old']:x}",
                            "new": f"0x{c['new']:x}", "delta": c["delta"]} for c in common[:limit]],
            "added_names": added[:limit], "removed_names": removed[:limit],
        }
