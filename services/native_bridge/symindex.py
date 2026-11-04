"""SymbolIndex — einheitlicher Namens-/Adress-Zugriff über geladene Libs.

Nimmt die im Native Studio geladenen Images entgegen und bietet den
Strategien einen schlanken, einheitlichen Lookup. Akzeptiert bewusst
mehrere Image-Formen, damit der Bridge-Kern nicht an eine konkrete
Loader-Klasse gebunden ist (Testbarkeit, lose Kopplung):

  * ein ElfImage mit ``.dynsyms`` = {name -> Symbol(.value)}
  * ein Objekt mit ``.syms`` = [(addr, name), …]  (NativeIndex)
  * schlicht ein dict {name -> addr}
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_SUBPAT = re.compile(r"^(?:sub_)?(?:0x)?([0-9A-Fa-f]+)$")


def _symbols_of(image) -> Dict[str, int]:
    """Extrahiert {symbolname -> vaddr} aus einer der unterstützten Formen."""
    # 1) dict {name -> addr}
    if isinstance(image, dict):
        return {str(k): int(v) for k, v in image.items()}
    # 2) ElfImage mit .dynsyms {name -> Symbol(.value)}
    dyn = getattr(image, "dynsyms", None)
    if isinstance(dyn, dict) and dyn:
        out = {}
        for name, sym in dyn.items():
            val = getattr(sym, "value", sym)
            try:
                out[str(name)] = int(val)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    # 3) NativeIndex mit .syms [(addr, name), …]
    syms = getattr(image, "syms", None)
    if syms:
        return {str(n): int(a) for a, n in syms}
    return {}


class SymbolIndex:
    """Vereinheitlicht Symbol-Lookups über alle geladenen Libs."""

    def __init__(self, images: Dict[str, object]):
        # Original-Images behalten (fuer Strategien, die ELF-Struktur brauchen,
        # z. B. RegisterNatives-Scan ueber relocs/data).
        self._images: Dict[str, object] = dict(images or {})
        # lib_name -> {symbol -> addr}
        self._by_lib: Dict[str, Dict[str, int]] = {
            lib: _symbols_of(img) for lib, img in self._images.items()
        }

    def images(self) -> Dict[str, object]:
        """Die rohen (ElfImage-)Objekte je Lib — fuer struktur-basierte Strategien."""
        return self._images

    @property
    def libs(self) -> List[str]:
        return list(self._by_lib.keys())

    def lookup(self, symbol: str, prefer_libs: Optional[List[str]] = None
               ) -> List[Tuple[str, int]]:
        """Alle (lib, addr) mit diesem Symbolnamen; bevorzugte Libs zuerst."""
        order = []
        if prefer_libs:
            order += [l for l in prefer_libs if l in self._by_lib]
        order += [l for l in self._by_lib if l not in order]

        hits: List[Tuple[str, int]] = []
        for lib in order:
            addr = self._by_lib[lib].get(symbol)
            if addr is not None:
                hits.append((lib, int(addr)))
        return hits

    def resolve_target_ref(self, ref: str, prefer_libs: Optional[List[str]] = None
                           ) -> List[Tuple[str, int, str]]:
        """Löst eine Alias-Zielangabe auf: entweder ein Symbolname, eine rohe
        Adresse (``0x1a2b0``) oder ein ``sub_1a2b0``-Platzhalter.

        Rückgabe: Liste (lib, addr, resolved_symbol_or_subname).
        """
        ref = ref.strip()
        m = _SUBPAT.match(ref)
        is_addr = ref.lower().startswith("0x") or ref.lower().startswith("sub_")
        if is_addr and m:
            addr = int(m.group(1), 16)
            # Adresse gilt für alle (bevorzugten) Libs — die erste nehmen
            libs = (prefer_libs or []) + [l for l in self._by_lib if l not in (prefer_libs or [])]
            lib = libs[0] if libs else "?"
            return [(lib, addr, f"sub_{addr:x}")]
        # sonst: als Symbolname behandeln
        return [(lib, addr, ref) for lib, addr in self.lookup(ref, prefer_libs)]
