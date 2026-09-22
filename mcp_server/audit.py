"""
audit — Protokoll jeder MCP-Tool-Ausfuehrung (Pflicht).

Schreibt eine JSONL-Zeile pro Aufruf (auch verweigerte) nach
MCP_SETTINGS.audit.file (relativ zu BASE_DIR). Nie exception-werfend.
"""
import os
import json
import time


def _mcp(cfg) -> dict:
    return cfg.config.get("MCP_SETTINGS", {}) or {}


def _path(cfg) -> str:
    a = _mcp(cfg).get("audit", {}) or {}
    rel = a.get("file", os.path.join("data", "mcp_audit.jsonl"))
    base = cfg.config.get("BASE_DIR", ".")
    return rel if os.path.isabs(rel) else os.path.join(base, rel)


def log(cfg, tool_id, tier, decision, confirm, args_summary, result, duration_ms, error=None) -> None:
    a = _mcp(cfg).get("audit", {}) or {}
    if not a.get("enabled", True):
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": tool_id,
        "tier": tier,
        "decision": decision,            # allowed | denied
        "confirm": bool(confirm),
        "args": (args_summary if a.get("log_args", True) else "<hidden>"),
        "result": result,                # ok | error | blocked
        "duration_ms": duration_ms,
    }
    if error:
        rec["error"] = str(error)[:500]
    try:
        p = _path(cfg)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail(cfg, n: int = 50):
    p = _path(cfg)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []
