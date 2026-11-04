"""find_library_names — erkennt geladene Lib-Namen aus Smali-Code.

Sucht ``System.loadLibrary("x")`` / ``loadLibrary``-Aufrufe und gibt die
zugehörigen const-string-Werte zurück. Dient dazu, beim Auflösen die
passende .so zu bevorzugen (prefer_libs).
"""
from __future__ import annotations

import re
from typing import List

# const-string vX, "name"  … später invoke-… loadLibrary
_CONST_STRING = re.compile(r'const-string(?:/jumbo)?\s+[vp]\d+,\s*"([^"]+)"')
_LOADLIB = re.compile(r'->(?:loadLibrary|load)\(Ljava/lang/String;\)')


def find_library_names(class_content: str) -> List[str]:
    """Heuristik: const-string-Werte in der Nähe eines loadLibrary-Aufrufs.

    Pragmatisch: sammelt alle const-string-Literale in Methoden, die einen
    loadLibrary-Aufruf enthalten. Für die Lib-Bevorzugung reicht das; die
    Auflösung testet ohnehin alle geladenen Libs als Fallback.
    """
    names: List[str] = []
    if "loadLibrary" not in class_content and "->load(" not in class_content:
        return names

    # Methodenweise betrachten
    for block in re.split(r'^\.method', class_content, flags=re.MULTILINE):
        if not _LOADLIB.search(block):
            continue
        for m in _CONST_STRING.finditer(block):
            val = m.group(1)
            if val and val not in names:
                names.append(val)
    return names
