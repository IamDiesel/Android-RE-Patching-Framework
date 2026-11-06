# -*- coding: utf-8 -*-
"""native_reloc_service — Relocations statisch aufloesen (read-only).

Der Loader (core/native/loader.py) parst Relocs bereits als
img.relocs = [(site_vaddr, target_vaddr, kind)] (RELATIVE -> target=addend; ABS64/
GLOB_DAT/JUMP_SLOT; RELR). Dieser Service loest einzelne Zeiger auf bzw. listet Relocs um
eine Adresse — ohne Emulation, ohne Engine-Eingriff. IP-neutral.

Nutzen: obfuskierte Libs (z. B. OLLVM-Control-Flow-Flattening in libdb0c) legen ihre
Dispatch-/Call-Zeiger als in der Datei NULL abgelegte Slots ab, die erst beim Laden ueber
RELATIVE-Relocs gefuellt werden. resolve() liefert das echte Ziel statisch.
"""
from __future__ import annotations


class NativeRelocService:
    @staticmethod
    def _as_int(a) -> int:
        return int(a, 0) if isinstance(a, str) else int(a)

    def _map(self, image) -> dict:
        """{site_vaddr -> target_vaddr}, einmal pro Image gecacht."""
        m = getattr(image, "_reloc_map", None)
        if m is None:
            m = {}
            for row in (getattr(image, "relocs", None) or []):
                try:
                    site, tgt = int(row[0]), int(row[1])
                except Exception:
                    continue
                m[site] = tgt
            try:
                image._reloc_map = m
            except Exception:
                pass
        return m

    def resolve(self, image, addr) -> dict:
        """Loest den Reloc-Slot an `addr` auf: {ok, addr, target}."""
        a = self._as_int(addr)
        t = self._map(image).get(a)
        return {"ok": t is not None, "addr": a, "target": t}

    def around(self, image, addr, radius: int = 0x40) -> dict:
        """Listet Relocs, deren Slot in [addr-radius, addr+radius] liegt."""
        a = self._as_int(addr)
        r = int(radius)
        hits = sorted((s, t) for s, t in self._map(image).items() if abs(s - a) <= r)
        return {"ok": True, "addr": a, "radius": r, "count": len(hits),
                "relocs": [{"site": s, "target": t} for s, t in hits]}

    def count(self, image) -> int:
        return len(self._map(image))
