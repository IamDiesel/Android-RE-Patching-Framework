"""
gating — zentrale Erlaubnis-Pruefung fuer jeden MCP-Tool-Aufruf.

Zwei Ebenen:
  1. Pro-Tool-Schalter (MCP_SETTINGS.tools[id], sonst Tier-Default).
  2. Fuer gated Tools (tier G): alle caps freigeschaltet + confirm.
Verweigerte Aufrufe liefern einen Hinweis (GUI-Schritt) statt auszufuehren.
"""
from dataclasses import dataclass

from mcp_server import registry


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    hint: str = ""


def _mcp(cfg) -> dict:
    return cfg.config.get("MCP_SETTINGS", {}) or {}


def check(cfg, tool_id: str, confirm: bool = False) -> Decision:
    spec = registry.get(tool_id)
    if not spec:
        return Decision(False, f"unbekanntes Tool: {tool_id}")

    m = _mcp(cfg)
    if not registry.is_tool_enabled(m, tool_id):
        return Decision(False, "in den MCP-Einstellungen deaktiviert",
                        hint=f"[deaktiviert] '{tool_id}' ist in der GUI-Seite '🔌 MCP' ausgeschaltet.")

    if spec.tier != "G":
        return Decision(True)

    caps = m.get("caps", {}) or {}
    missing = [c for c in spec.caps if not caps.get(c, False)]
    if missing:
        return Decision(False, f"Kategorie(n) gesperrt: {', '.join(missing)}",
                        hint=(f"[verweigert] '{tool_id}' braucht Freigabe von "
                              f"{', '.join(missing)} in der GUI-Seite '🔌 MCP' — "
                              f"oder die Aktion direkt in der GUI ausfuehren."))

    if m.get("require_confirm_each_call", True) and not confirm:
        return Decision(False, "confirm fehlt",
                        hint=f"[Bestaetigung noetig] '{tool_id}' erneut mit confirm=true aufrufen.")

    return Decision(True)
