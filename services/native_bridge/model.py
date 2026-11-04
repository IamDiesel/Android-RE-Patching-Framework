"""Datenmodelle der Native-Bridge (IP-neutral).

Enthält nur reine Datencontainer — keine GUI-, keine LLU-Abhängigkeit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class JniTarget:
    """Eine aus Smali stammende native Methode, die aufgelöst werden soll.

    dalvik_class : Dalvik-Form inkl. L…;  (z. B. "Lcom/foo/Bar;")
    method       : Methodenname (z. B. "decrypt")
    arg_desc     : optionaler JNI-Argument-Deskriptor (Klammerinhalt der
                   Signatur, z. B. "Ljava/lang/String;I") — nur für den
                   JNI-Langnamen bei überladenen Methoden nötig.
    """
    dalvik_class: str
    method: str
    arg_desc: Optional[str] = None

    @property
    def class_internal(self) -> str:
        """Klassenname ohne führendes 'L' und abschließendes ';'."""
        c = self.dalvik_class
        if c.startswith("L"):
            c = c[1:]
        if c.endswith(";"):
            c = c[:-1]
        return c


@dataclass(frozen=True)
class Hit:
    """Ein Auflösungs-Treffer: wohin der Sprung geht und wie er zustande kam."""
    lib: str                 # Name der geladenen .so
    addr: int                # virtuelle Adresse (Sprungziel)
    symbol: str              # aufgelöster Symbolname (oder "sub_<addr>")
    mode: str                # "static" | "rule" | "alias" | "plugin" | …
    by: str                  # Name der Strategie, die den Treffer lieferte
    confidence: float = 1.0  # 0..1 — höher = sicherer
    note: str = ""           # optionaler Hinweis (z. B. verwendete Regel)

    def key(self) -> tuple:
        return (self.lib, self.addr, self.symbol)


@dataclass
class StrategyInfo:
    """GUI-lesbare Metadaten einer Strategie (für die spätere Oberfläche)."""
    name: str                      # stabiler Schlüssel
    label: str                     # Anzeigename
    enabled: bool = True           # schaltbar in der GUI
    source: str = "framework"      # "framework" | "plugin:<name>" | "rules:<pfad>"
    detail: str = ""               # Kurzbeschreibung
    extra: dict = field(default_factory=dict)
