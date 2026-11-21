# -*- coding: utf-8 -*-
"""core.java.objects — minimales Java-Objektmodell fuer den DalvikVM (J1).

Bewusst bounded: nur so viel Objektmodell, wie der Register-Interpreter braucht, um Objekte, Arrays
und Strings zu halten. Referenzen liegen direkt als Python-Objekte in den VM-Registern; `None` = null.
Primitive bleiben Python-``int`` (auf 32 Bit maskiert bei Arithmetik). Zielagnostisch, keine App-Spezifika.
"""
from __future__ import annotations


class JString:
    """Unveraenderlicher Java-String (nur Text)."""
    __slots__ = ("text",)

    def __init__(self, text=""):
        self.text = "" if text is None else str(text)

    def __repr__(self):
        return "JString(%r)" % self.text

    def __eq__(self, o):
        return isinstance(o, JString) and o.text == self.text

    def __hash__(self):
        return hash(("JString", self.text))


class JObject:
    """Instanz einer Klasse: Klassenname + Feld-Dictionary (name -> Wert)."""
    __slots__ = ("cls", "fields")

    def __init__(self, cls, fields=None):
        self.cls = cls
        self.fields = dict(fields or {})

    def __repr__(self):
        return "JObject(%s)" % self.cls


class JArray:
    """Array mit Elementtyp (z. B. 'I', '[B', 'Ljava/lang/Object;') und Python-Liste als Speicher."""
    __slots__ = ("eltype", "data")

    def __init__(self, eltype, data):
        self.eltype = eltype
        self.data = list(data)

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return "JArray(%s, len=%d)" % (self.eltype, len(self.data))


def default_for(eltype):
    """Default-Element eines Arrays: 0 fuer Primitive, None fuer Referenztypen."""
    if eltype and eltype[0] in ("[", "L"):
        return None
    return 0
