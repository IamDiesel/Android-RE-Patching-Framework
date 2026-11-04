"""RegisterNativesResolver — dynamische JNI-Bindung (Stufe 3).

Viele moderne Libs (React Native/fbjni, Rive, Go-Bridges) binden ihre nativen
Methoden **nicht** per `Java_<pkg>_<Class>_<method>`-Export, sondern zur Laufzeit
ueber `RegisterNatives(env, clazz, JNINativeMethod*, count)`. Dann gibt es kein
`Java_…`-Symbol, und Standard-Mangling/Regeln greifen nicht.

Dieser Resolver findet die `JNINativeMethod[]`-Tabellen **statisch** im ELF:

    struct JNINativeMethod { const char* name; const char* signature; void* fnPtr; };

Auf AArch64/PIE sind die drei Zeiger je Eintrag **relozierte** 8-Byte-Slots
(R_AARCH64_RELATIVE), deren Zieladressen `ElfImage.relocs` liefert. Ein Tripel
aufeinanderfolgender relozierter Slots (s, s+8, s+16) ist ein Eintrag, wenn:
  * Ziel(s)    -> C-String ohne '(' (Methodenname),
  * Ziel(s+8)  -> C-String, der mit '(' beginnt (JNI-Signatur),
  * Ziel(s+16) -> Adresse in einem ausfuehrbaren Segment (Funktion).

IP-neutral: kennt kein LLU. Liest nur die geladene ELF-Struktur.
"""
from __future__ import annotations

import re
from typing import Dict, List

from .model import Hit, JniTarget, StrategyInfo

_PF_X = 0x1

# Gueltiger JNI-Methodenname (Java-Identifier).
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
# Gueltiger JNI-Methoden-Deskriptor: (params)return — toetet Zufalls-Tripel,
# deren "Signatur" nur zufaellig mit '(' beginnt.
_FIELD = r"(?:\[*(?:[ZBCSIJFD]|L[^;()]+;))"
_SIG_RE = re.compile(r"^\((?:" + _FIELD + r")*\)(?:V|" + _FIELD + r")$")


def _v2o(img, v):
    """Virtuelle Adresse -> Datei-Offset (innerhalb filesz), wie loader._v2o."""
    for sg in getattr(img, "segments", []) or []:
        if sg.vaddr <= v < sg.vaddr + sg.filesz:
            return sg.offset + (v - sg.vaddr)
    return None


def _cstr(data: bytes, off, maxlen: int = 256):
    if off is None or off < 0 or off >= len(data):
        return None
    end = data.find(b"\x00", off, off + maxlen)
    if end < 0:
        return None
    try:
        return data[off:end].decode("utf-8")
    except Exception:
        return None


def _text_ranges(img):
    out = []
    for s in getattr(img, "segments", []) or []:
        if s.flags & _PF_X:
            out.append((s.vaddr, s.vaddr + max(getattr(s, "memsz", 0), s.filesz)))
    return out


def _looks_like_name(s):
    return bool(s) and len(s) <= 200 and bool(_NAME_RE.match(s))


def scan_register_natives(img) -> Dict[str, List[dict]]:
    """Findet JNINativeMethod[]-Tripel -> {methodenname: [{addr, sig}]}."""
    data = getattr(img, "data", b"") or b""
    relocs = getattr(img, "relocs", []) or []
    rmap = {}
    for tup in relocs:
        # (site_vaddr, target_vaddr, kind)
        if len(tup) >= 2:
            rmap[tup[0]] = tup[1]
    tranges = _text_ranges(img)

    def in_text(a):
        return any(lo <= a < hi for lo, hi in tranges)

    out: Dict[str, List[dict]] = {}
    for site, tgt in rmap.items():
        s1 = rmap.get(site + 8)
        s2 = rmap.get(site + 16)
        if s1 is None or s2 is None:
            continue
        name = _cstr(data, _v2o(img, tgt))
        if not _looks_like_name(name):
            continue
        sig = _cstr(data, _v2o(img, s1))
        if not sig or not _SIG_RE.match(sig):
            continue
        if not in_text(s2):
            continue
        out.setdefault(name, []).append({"addr": s2 & ~1, "sig": sig})
    return out


class RegisterNativesResolver:
    """Loest dynamisch (RegisterNatives) gebundene native Methoden auf."""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._cache: Dict[int, Dict[str, List[dict]]] = {}

    def info(self) -> StrategyInfo:
        return StrategyInfo(
            name="register_natives",
            label="RegisterNatives-Scan",
            enabled=self._enabled,
            source="framework",
            detail="Scannt JNINativeMethod[]-Tabellen (dynamische JNI-Bindung).",
        )

    def _map_for(self, img) -> Dict[str, List[dict]]:
        key = id(img)
        m = self._cache.get(key)
        if m is None:
            try:
                m = scan_register_natives(img)
            except Exception:
                m = {}
            self._cache[key] = m
        return m

    def resolve(self, target: JniTarget, symindex, prefer_libs=None) -> List[Hit]:
        images = symindex.images() if hasattr(symindex, "images") else {}
        if not images:
            return []
        order = []
        if prefer_libs:
            order += [l for l in prefer_libs if l in images]
        order += [l for l in images if l not in order]

        hits: List[Hit] = []
        for lib in order:
            img = images[lib]
            m = self._map_for(img)
            for e in m.get(target.method, []):
                conf = 0.85
                sig = e.get("sig", "")
                if target.arg_desc and sig.startswith("(" + target.arg_desc):
                    conf = 0.92  # Signatur passt -> ueberladungssicher
                hits.append(Hit(lib=lib, addr=e["addr"],
                                symbol=f"RegisterNatives:{target.method}",
                                mode="register_natives", by="register_natives",
                                confidence=conf, note=sig))
        return hits
