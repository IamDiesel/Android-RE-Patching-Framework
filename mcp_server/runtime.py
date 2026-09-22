"""
runtime — einheitliche Ausfuehrungshuelle fuer jedes Tool.

Ablauf pro Aufruf: gating.check -> (bei Erlaubnis) fn() unter LogCollector ->
Ergebnis + Framework-Log -> audit.log. Verweigerte Aufrufe liefern den
GUI-Hinweis und werden ebenfalls auditiert.
"""
import json
import time

from mcp_server import registry, gating, audit
from mcp_server.eventbus_bridge import LogCollector


def run(ctx, tool_id, fn, args_summary="", confirm=False, capture_events=("LOG_INFO",)) -> str:
    spec = registry.get(tool_id)
    tier = spec.tier if spec else "?"
    t0 = time.time()

    dec = gating.check(ctx.cfg, tool_id, confirm)
    if not dec.allowed:
        audit.log(ctx.cfg, tool_id, tier, "denied", confirm, args_summary,
                  "blocked", int((time.time() - t0) * 1000))
        return dec.hint or f"[verweigert] {dec.reason}"

    try:
        with LogCollector(capture_events) as lc:
            out = fn()
        body = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2)
        log_txt = lc.text()
        if log_txt:
            body = f"{body}\n\n--- Framework-Log ---\n{log_txt}"
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "ok", int((time.time() - t0) * 1000))
        return body
    except Exception as e:
        audit.log(ctx.cfg, tool_id, tier, "allowed", confirm, args_summary,
                  "error", int((time.time() - t0) * 1000), error=e)
        return f"[Fehler in {tool_id}] {e}"
