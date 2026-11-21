# -*- coding: utf-8 -*-
"""core.java.sandbox_patch — Smali-Patches im Emulator anwenden (MS-5, J4-Slice).

Bestehende Smali-Patch-Favoriten ({file, orig, edit}) als Find/Replace auf den Quelltext anwenden,
bevor er in die DalvikVM geladen wird. So wirkt ein Geraete-Patch identisch in der glaesernen Sandbox
(ohne Datei zu veraendern). Zielagnostisch, rein, headless testbar.
"""
from __future__ import annotations


def _same_file(a, b):
    if not a or not b:
        return True
    a = a.replace("\\", "/").lower()
    b = b.replace("\\", "/").lower()
    return a == b or a.endswith(b) or b.endswith(a)


def apply_smali_patches(source, patches, file=None):
    """Wendet Smali-Patches (Find/Replace {orig -> edit|new}) auf `source` an.
    Nur Patches ohne file-Filter oder mit zu `file` passendem Pfad. Hex-/Lib-Patches werden ignoriert.
    Rueckgabe: (patched_source, applied_count)."""
    out = source or ""
    n = 0
    for p in patches or []:
        if not isinstance(p, dict):
            continue
        if p.get("type") not in (None, "smali"):
            continue
        pf = p.get("file")
        if file is not None and pf and not _same_file(pf, file):
            continue
        orig = (p.get("orig") or "").replace("\r\n", "\n")
        edit = (p.get("edit") if p.get("edit") is not None else p.get("new", "")) or ""
        edit = edit.replace("\r\n", "\n")
        if orig and orig in out:
            out = out.replace(orig, edit)
            n += 1
    return out, n
